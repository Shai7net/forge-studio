"""Engine checks that need no model files and no graphics card.

Run: webui\\video-studio\\env\\Scripts\\python.exe -m unittest discover -s extensions\\sd-forge-video-studio\\tests
"""

import os
import sys
import unittest

import numpy as np
import torch
import torch.nn.functional as F

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from videostudio.engine import ops, pipeline, vae  # noqa: E402
from videostudio.engine.wan import WanModel, _rename  # noqa: E402

ops.COMPILE["on"] = False


class Dequant(unittest.TestCase):
    # where the fp16 scales of each block type sit (byte offsets), so random blocks get sane scales
    SCALES = {"Q8_0": [0], "Q4_0": [0], "Q5_0": [0], "Q4_1": [0, 2], "Q5_1": [0, 2], "Q2_K": [80, 82],
              "Q3_K": [108], "Q4_K": [0, 2], "Q5_K": [0, 2], "Q6_K": [208]}

    def test_against_gguf_py(self):
        """Every supported quant type decodes like gguf-py's reference (numpy) implementation."""
        import gguf
        rng = np.random.default_rng(0)
        scale = np.frombuffer(np.float16(0.01).tobytes(), dtype=np.uint8)
        for name, offsets in self.SCALES.items():
            qtype = getattr(gguf.GGMLQuantizationType, name)
            block, size = ops.GGML_TYPES_BY_NAME[name][1], ops.GGML_TYPES_BY_NAME[name][2]
            packed = rng.integers(0, 256, (8, 512 // block * size), dtype=np.uint8)
            blocks = packed.reshape(-1, size)
            for o in offsets:
                blocks[:, o:o + 2] = scale
            ref = gguf.quants.dequantize(packed, qtype)
            w = ops.Weight(torch.from_numpy(np.ascontiguousarray(packed)).reshape(-1), qtype=name, shape=(8, 512))
            got = w.materialize(torch.device("cpu"), torch.float32).numpy()
            np.testing.assert_allclose(got, ref, rtol=2e-3, atol=2e-3 * np.abs(ref).max() + 1e-6, err_msg=name)


class Int8(unittest.TestCase):
    @unittest.skipUnless(torch.cuda.is_available(), "needs CUDA for _int_mm")
    def test_int8_linear_close_to_float(self):
        x = torch.randn(2048, 512, device="cuda", dtype=torch.bfloat16)
        w = torch.randn(256, 512, device="cuda", dtype=torch.float16) * 0.05
        wq, sw = ops._quant_weight(w)
        y = ops.int8_linear(x, wq, sw, None).float()
        ref = F.linear(x.float(), w.float())
        cos = F.cosine_similarity(y.flatten(), ref.flatten(), dim=0).item()
        self.assertGreater(cos, 0.999)


class Conv(unittest.TestCase):
    def test_conv3d_as_2d(self):
        torch.manual_seed(0)
        for k, stride, pad in (((3, 3, 3), (1, 1, 1), (0, 1, 1)), ((3, 1, 1), (2, 1, 1), (0, 0, 0)), ((1, 1, 1), (1, 1, 1), (0, 0, 0))):
            conv = torch.nn.Conv3d(6, 5, k, stride=stride, padding=pad)
            x = torch.randn(1, 6, 7, 9, 11)
            ref = F.conv3d(x, conv.weight, conv.bias, stride, pad)
            got = vae.conv3d_as_2d(x, conv.weight, conv.bias, stride, pad)
            torch.testing.assert_close(got, ref, rtol=1e-4, atol=1e-5)

    def test_auto_tile(self):
        self.assertIsNone(vae.auto_tile(480, 832, vae.DECODE_BYTES_PER_PIXEL, 8e9))
        tile = vae.auto_tile(720, 1280, vae.DECODE_BYTES_PER_PIXEL, 6e9)
        self.assertIsNotNone(tile)
        self.assertLessEqual((tile * 8) ** 2 * vae.DECODE_BYTES_PER_PIXEL, 6e9 * 0.75)


class Schedule(unittest.TestCase):
    def test_sigmas_lightx2v(self):
        """4 steps at shift 5 give the lightx2v schedule; the first two are above the 0.9 expert boundary."""
        s = pipeline.sigmas_for(4, 5.0).tolist()
        np.testing.assert_allclose(s, [1.0, 0.9375, 0.8333333, 0.625, 0.0], rtol=1e-5)
        self.assertEqual(sum(v >= 0.9 for v in s[:-1]), 2)

    def test_fit_size(self):
        self.assertEqual(pipeline.fit_size(1080, 1920, 480 * 832), (480, 848))
        w, h = pipeline.fit_size(1920, 1080, 720 * 1280)
        self.assertEqual((w % 16, h % 16), (0, 0))


class Lora(unittest.TestCase):
    def test_key_styles_reach_the_same_layer(self):
        from videostudio.engine import lora
        model = WanModel(in_dim=36, dim=256, ffn_dim=512, num_heads=2, num_layers=2)
        mods = lora._modules(model)
        for key in ("diffusion_model.blocks.1.ffn.0", "lora_unet_blocks_1_ffn_0", "blocks.1.ffn.0"):
            name, kohya = lora._norm_base(key)
            target = mods.get(name) or mods.get(name.replace(".", "_"))
            self.assertIs(target, model.blocks[1].ffn_0, key)
        self.assertTrue(lora.applies_to("wan22_high_noise_style.safetensors", "high noise"))
        self.assertFalse(lora.applies_to("wan22_high_noise_style.safetensors", "low noise"))
        self.assertTrue(lora.applies_to("style.safetensors", "low noise"))

    def test_rename(self):
        self.assertEqual(_rename("blocks.3.ffn.2.weight"), "blocks.3.ffn_2.weight")
        self.assertEqual(_rename("patch_embedding.bias"), "patch_bias")
        self.assertEqual(_rename("pose_patch_embedding.bias"), "pose_patch_bias")
        self.assertEqual(_rename("img_emb.proj.4.weight"), "img_emb.proj_4.weight")
        self.assertEqual(_rename("face_adapter.fuser_blocks.3.linear2.weight"), "blocks.15.face.linear2.weight")

    def test_diffs_reach_norms_and_biases(self):
        from videostudio.engine import lora
        w = {"blocks.0.norm3.weight": ops.Weight(torch.ones(4)), "head.head.bias": ops.Weight(torch.zeros(4))}
        parts = {"diff": torch.full((4,), 0.5)}
        self.assertEqual(lora._add_diffs(w, "blocks.0.norm3", parts, 2.0), 1)
        self.assertTrue(torch.equal(w["blocks.0.norm3.weight"].cpu, torch.full((4,), 2.0)))
        self.assertEqual(lora._add_diffs(w, "head.head", {"diff_b": torch.ones(4)}, 1.0), 1)
        self.assertEqual(float(w["head.head.bias"].cpu.sum()), 4.0)


class Animate(unittest.TestCase):
    """Wan Animate's pieces with small random weights: shapes, and the chunk arithmetic of the official code."""

    def test_chunks_and_mask(self):
        from videostudio.engine import animate
        self.assertEqual(animate.parts(77, 77, 5), [(0, 77)])
        self.assertEqual(animate.parts(80, 77, 5), [(0, 77), (72, 9)])     # a short tail, not a whole part
        self.assertEqual(animate.parts(149, 77, 5), [(0, 77), (72, 77)])
        self.assertEqual(animate.parts(40, 77, 5), [(0, 41)])             # 4k + 1
        known = torch.zeros(77, 3, 5)
        known[:5] = 1
        m = animate.i2v_mask(known, 20, 3, 5)
        self.assertEqual(tuple(m.shape), (1, 4, 20, 3, 5))
        self.assertTrue(bool(m[0, :, 0].eq(1).all()))       # frame 0, counted four times
        self.assertTrue(bool(m[0, :, 1].eq(1).all()))       # frames 1-4
        self.assertTrue(bool(m[0, 0, 2].eq(0).all()))       # frame 5 onwards unknown

    def test_face_motion_shapes(self):
        from videostudio.engine.animate_model import FaceEncoder, MotionEncoder
        g = torch.Generator().manual_seed(0)
        r = lambda *s: torch.randn(*s, generator=g) * 0.05  # noqa: E731
        w = {}
        p = "motion_encoder.enc.net_app.convs"
        w[f"{p}.0.0.weight"], w[f"{p}.0.1.bias"] = r(32, 3, 1, 1), r(1, 32, 1, 1)
        chans = [32, 64, 128, 256, 512, 512, 512, 512]
        for i in range(1, 8):
            a, b = chans[i - 1], chans[i]
            w[f"{p}.{i}.conv1.0.weight"], w[f"{p}.{i}.conv1.1.bias"] = r(a, a, 3, 3), r(1, a, 1, 1)
            w[f"{p}.{i}.conv2.1.weight"], w[f"{p}.{i}.conv2.2.bias"] = r(b, a, 3, 3), r(1, b, 1, 1)
            w[f"{p}.{i}.skip.1.weight"] = r(b, a, 1, 1)
        w[f"{p}.8.weight"] = r(512, 512, 4, 4)
        for i in range(4):
            w[f"motion_encoder.enc.fc.{i}.weight"], w[f"motion_encoder.enc.fc.{i}.bias"] = r(512, 512), r(512)
        w["motion_encoder.enc.fc.4.weight"], w["motion_encoder.enc.fc.4.bias"] = r(20, 512), r(20)
        w["motion_encoder.dec.direction.weight"] = r(512, 20)
        vec = MotionEncoder(w)(r(2, 3, 512, 512))
        self.assertEqual(tuple(vec.shape), (2, 512))
        dim = 64
        f = {"face_encoder.conv1_local.conv.weight": r(4096, 512, 3), "face_encoder.conv1_local.conv.bias": r(4096),
             "face_encoder.conv2.conv.weight": r(1024, 1024, 3), "face_encoder.conv2.conv.bias": r(1024),
             "face_encoder.conv3.conv.weight": r(1024, 1024, 3), "face_encoder.conv3.conv.bias": r(1024),
             "face_encoder.out_proj.weight": r(dim, 1024), "face_encoder.out_proj.bias": r(dim),
             "face_encoder.padding_tokens": r(1, 1, 1, dim)}
        tokens = FaceEncoder(f)(r(1, 77, 512))
        self.assertEqual(tuple(tokens.shape), (1, 20, 5, dim))  # 77 frames -> 20 latent frames, 4 + 1 tokens

    def test_skeleton_and_mask_helpers(self):
        from videostudio.engine import animate_draw
        kp = np.zeros((133, 3), np.float32)
        kp[:, 2] = 1.0
        kp[:, 0] = np.linspace(20, 80, 133)
        kp[:, 1] = np.linspace(10, 90, 133)
        canvas = np.zeros((100, 100, 3), np.uint8)
        animate_draw.draw_pose(canvas, kp, 0.5)
        self.assertGreater(int(canvas.sum()), 0)
        x1, x2, y1, y2 = animate_draw.face_box(kp, 1.3, 100, 100)
        self.assertTrue(0 <= x1 < x2 <= 100 and 0 <= y1 < y2 <= 100)
        m = np.zeros((40, 40), np.uint8)
        m[10:20, 5:8] = 1
        m[25:30, 30:35] = 1
        box = animate_draw.aug_mask(m, 1, 1)
        self.assertEqual(int(box[10:29, 5:34].min()), 1)        # 1 x 1: the whole box
        self.assertEqual(int(animate_draw.aug_mask(m, 10, 10)[22, 20]), 0)  # a finer grid keeps the gap


class Ingredients(unittest.TestCase):
    def test_reference_is_whole_and_centred_on_white(self):
        import tempfile
        from PIL import Image
        path = os.path.join(tempfile.mkdtemp(), "tall.png")
        Image.new("RGB", (100, 400), (255, 0, 0)).save(path)   # a tall red subject in a wide frame
        a = pipeline.load_reference(path, 320, 160)
        self.assertEqual(tuple(a.shape), (3, 160, 320))
        self.assertAlmostEqual(float(a[0, 80, 160]), 1.0, places=2)    # the subject, whole, in the middle
        self.assertAlmostEqual(float(a[1, 80, 160]), -1.0, places=2)
        self.assertAlmostEqual(float(a[1, 80, 5]), 1.0, places=2)      # white on both sides, nothing cut off


if __name__ == "__main__":
    unittest.main()

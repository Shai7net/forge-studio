"""Run using Forge's existing Python: python -m unittest discover -s .../tests -v."""

import base64
from dataclasses import replace
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from PIL import Image
from fibo2.contracts import Generation, crop_to_canvas, decode_image, encode_image, grey_out, preserve_unmasked
from fibo2 import models


class ContractTests(unittest.TestCase):
    def test_gradio_integral_floats_are_normalized_for_sampling(self):
        request = Generation(prompt="A mug", width=1024.0, height=1024.0, count=2.0, steps=4.0, seed=1234.0).validated()
        for field in (request.width, request.height, request.count, request.steps, request.seed):
            self.assertIsInstance(field, int)
        self.assertEqual(list(range(request.count)), [0, 1])

    def test_prompt_writer_preserves_json_and_validates_edit_output(self):
        from fibo2.prompting import wants_writer, extract_json
        self.assertFalse(wants_writer(Generation(prompt='{"short_description":"red car"}', prompt_writer="always")))
        self.assertTrue(wants_writer(Generation(prompt="מכונית אדומה", prompt_writer="auto")))
        self.assertEqual(json.loads(extract_json('```json\n{"short_description":"A car"}\n```'))["short_description"], "A car")
        with self.assertRaises(ValueError):
            extract_json('{"short_description":"A car"}', editing=True)

    def test_model_defaults_and_edit_only_turbo(self):
        self.assertEqual((Generation().effective_steps, Generation().effective_cfg), (50, 5))
        turbo = Generation(variant="edit-turbo", mode="edit", prompt="Make it blue")
        self.assertEqual((turbo.effective_steps, turbo.effective_cfg), (4, 1))
        turbo.validated([Image.new("RGB", (64, 64))])
        with self.assertRaisesRegex(ValueError, "for image editing"):
            replace(turbo, mode="generate").validated()
        with self.assertRaisesRegex(ValueError, "CFG 1"):
            replace(turbo, cfg=7).validated([Image.new("RGB", (64, 64))])

    def test_reference_order_and_mask_constraints(self):
        request = Generation(mode="edit", prompt="Put <image_2> beside <image_1>")
        refs = [Image.new("RGB", (64, 64), color) for color in ("red", "green", "blue", "white")]
        request.validated(refs)
        with self.assertRaisesRegex(ValueError, "four"):
            request.validated(refs + refs[:1])
        with self.assertRaisesRegex(ValueError, "exactly one"):
            request.validated(refs, Image.new("L", (64, 64)))
        with self.assertRaisesRegex(ValueError, "same dimensions"):
            request.validated(refs[:1], Image.new("L", (32, 32)))

    def test_invalid_dimensions_seed_and_nonfinite_cfg(self):
        request = Generation(prompt="A tree")
        for params in ({"width": 1025}, {"width": 2560, "height": 2560}, {"count": 1.5},
                       {"cfg": float("nan")}, {"seed": -3}, {"steps": 101}):
            with self.subTest(params=params), self.assertRaises(ValueError):
                replace(request, **params).validated()

    def test_vgl_json_is_not_rewritten_and_malformed_json_rejected(self):
        prompt = '{"short_description":"A sign: (OPEN:1.2)","text_render":[{"text":"שלום"}]}'
        self.assertEqual(Generation(prompt=prompt).validated().prompt, prompt)
        with self.assertRaisesRegex(ValueError, "Invalid VGL JSON"):
            Generation(prompt="{broken").validated()
        with self.assertRaisesRegex(ValueError, "edit_instruction"):
            Generation(prompt=prompt, mode="edit").validated([Image.new("RGB", (64, 64))])

    def test_binary_grey_mask_matches_bria_training(self):
        source = Image.new("RGB", (4, 1), (2, 3, 4))
        mask = Image.new("L", source.size)
        mask.putdata([0, 127, 128, 255])
        self.assertEqual(list(grey_out(source, mask).getdata()), [(2, 3, 4)] * 2 + [(128, 128, 128)] * 2)

    def test_black_mask_pixels_are_bit_exact(self):
        source = Image.new("RGB", (64, 64))
        source.putdata([(x % 256, x * 3 % 256, x * 7 % 256) for x in range(4096)])
        mask = Image.new("L", source.size, 0)
        for x in range(16, 48):
            for y in range(16, 48):
                mask.putpixel((x, y), 255)
        generated = Image.new("RGB", source.size, "red")
        result = preserve_unmasked(generated, source, mask)
        for y in range(64):
            for x in range(64):
                self.assertEqual(result.getpixel((x, y)), generated.getpixel((x, y)) if mask.getpixel((x, y)) else source.getpixel((x, y)))

    def test_base64_round_trip_and_url_rejection(self):
        source = Image.new("RGB", (64, 64), (17, 39, 250))
        decoded = decode_image(encode_image(source))
        self.assertEqual(decoded.tobytes(), source.tobytes())
        for value in ("https://example.com/image.png", "../local.png", "not base64!"):
            with self.assertRaises(ValueError):
                decode_image(value)

    def test_incomplete_aria2_download_is_not_loadable(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "model.safetensors"
            path.write_bytes(b"x" * 2000)
            self.assertTrue(models.complete_file(path))
            Path(str(path) + ".aria2").touch()
            self.assertFalse(models.complete_file(path))


class ApiTests(unittest.TestCase):
    def app(self, auth=None):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from fibo2.api import register
        app = FastAPI()
        register(app, auth)
        return TestClient(app)

    def test_api_auth_is_enforced_on_all_routes(self):
        client = self.app("tester:password")
        self.assertEqual(client.get("/fibo2/status").status_code, 401)
        self.assertEqual(client.post("/fibo2/interrupt").status_code, 401)
        self.assertEqual(client.post("/fibo2/generate", json={"prompt": "A tree"}).status_code, 401)
        self.assertEqual(client.get("/fibo2/status", auth=("tester", "wrong")).status_code, 401)
        self.assertEqual(client.get("/fibo2/status", auth=("tester", "password")).status_code, 200)

    def test_invalid_api_input_does_not_invoke_inference(self):
        client = self.app()
        from fibo2 import service
        with patch.object(service, "generate") as inference:
            response = client.post("/fibo2/generate", json={"mode": "edit", "prompt": "blue", "images": ["not base64"]})
            self.assertEqual(response.status_code, 400)
            inference.assert_not_called()
        response = client.post("/fibo2/generate", json={"prompt": "A tree", "images": ["x"] * 5})
        self.assertEqual(response.status_code, 422)

    def test_api_transmits_references_in_order_and_returns_real_png(self):
        from fibo2 import service
        client = self.app()
        refs = [Image.new("RGB", (64, 64), color) for color in ("red", "blue")]
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "result.png"
            refs[0].save(output)

            def inference(request, references, mask, loras, weight):
                request.validated(references, mask)
                self.assertEqual([x.getpixel((0, 0)) for x in references], [(255, 0, 0), (0, 0, 255)])
                self.assertEqual(request.effective_steps, 4)
                return {"images": [{"path": str(output), "metadata": {"seed": 1}}]}

            with patch.object(service, "generate", side_effect=inference):
                response = client.post("/fibo2/generate", json={"variant": "edit-turbo", "mode": "edit", "prompt": "Combine",
                                                               "images": [encode_image(im) for im in refs]})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(decode_image(response.json()["images"][0]).tobytes(), refs[0].tobytes())


class UiTests(unittest.TestCase):
    def test_tab_builds_in_forges_actual_gradio_version(self):
        # Only Forge's paste registry is isolated; every UI component/event is real Gradio 3.41.2.
        from fibo2 import ui
        registry = []
        fake = types.SimpleNamespace(ParamBinding=lambda **kw: kw, register_paste_params_button=registry.append)
        with patch.dict(sys.modules, {"modules.infotext_utils": fake}):
            modules = types.ModuleType("modules")
            modules.infotext_utils = fake
            with patch.dict(sys.modules, {"modules": modules}):
                tab = ui.build()
        config = tab.get_config_file()
        ids = {c.get("props", {}).get("elem_id") for c in config["components"]}
        self.assertIn("fibo2_generate", ids)
        self.assertIn("fibo2_gallery", ids)
        self.assertEqual([x["tabname"] for x in registry], ["img2img", "extras"])
        tab.close()


if __name__ == "__main__":
    unittest.main()

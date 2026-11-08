"""Continuing a clip with the tool that made it, without Forge: which tool a clip comes from, and what is asked of it.

Run from the extension folder:  ..\\..\\venv\\Scripts\\python.exe tests\\test_continuers.py
"""

import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
TMP = tempfile.mkdtemp(prefix="forge-studio-cont-")
os.environ["FORGE_STUDIO_ROOT"] = os.path.join(TMP, "data")

from PIL import Image, PngImagePlugin  # noqa: E402

from forgestudio import continuers  # noqa: E402

INFOTEXT = ('Psychedelic style shapes, neon glow, <lora:LCM_SD15:1>, <lora:glasssculpture_v8:1>, glasssculpture . trippy\n'
            'Negative prompt: monochrome, plain\n'
            'Steps: 8, Sampler: LCM, CFG scale: 1.5, Seed: 93389718, Size: 408x720, Model: cyberrealistic_final, '
            'VAE: vae-ft-mse-840000-ema-pruned.safetensors, AnimateDiff: "model: diffLCM.6rta.ckpt, video_length: 170, '
            'fps: 15, loop_number: 0, closed_loop: N, batch_size: 16, stride: 1, overlap: 4, interp: Off, interp_x: 10", '
            'ControlNet 0: "Module: dw_openpose_full, Model: control_v11p_sd15_openpose [73c2b67d], Weight: 0.65", '
            'ControlNet 2: "Module: CLIP-ViT-H (IPAdapter), Model: ip-adapter-plus_sd15 [c817b455], Weight: 1"')


def picture(path, text=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    meta = PngImagePlugin.PngInfo()
    if text:
        meta.add_text("parameters", text)
    Image.new("RGB", (16, 16), "purple").save(path, pnginfo=meta)


class Continuers(unittest.TestCase):
    def test_animatediff_clip_is_known_by_its_frames_folder(self):
        clip = os.path.join(TMP, "ad", "00014-1.mp4")
        picture(os.path.join(TMP, "ad", "00014-1", "00000.png"), INFOTEXT)
        found = continuers.recipe(clip)
        self.assertEqual(found["engine"], "animatediff")
        self.assertNotIn("<lora", found["prompt"])
        # a continuation of it, joined and with sound, is still known by the same folder
        self.assertEqual(continuers.recipe(clip[:-4] + "-joined-sound.mp4")["engine"], "animatediff")

    def test_animatediff_request_keeps_the_recipe_and_starts_from_the_frame(self):
        frame = os.path.join(TMP, "frame.png")
        picture(frame)
        p = continuers.animatediff_payload(INFOTEXT, frame, "a tunnel of light", 5, 7, (816, 1440), ["None", "R-ESRGAN 4x+"])
        self.assertTrue(p["prompt"].startswith("a tunnel of light"))
        for kept in ("<lora:LCM_SD15:1>", "<lora:glasssculpture_v8:1>", "glasssculpture"):
            self.assertIn(kept, p["prompt"])
        self.assertEqual((p["width"], p["height"], p["steps"], p["sampler_name"], p["cfg_scale"]), (408, 720, 8, "LCM", 1.5))
        self.assertEqual(p["override_settings"]["sd_model_checkpoint"], "cyberrealistic_final")
        ad = p["alwayson_scripts"]["AnimateDiff"]["args"][0]
        self.assertEqual((ad["model"], ad["fps"], ad["video_length"], ad["overlap"]), ("diffLCM.6rta.ckpt", 15, 75, 4))
        self.assertIn("MP4", ad["format"])
        self.assertEqual((ad["upscaler"], ad["upscale_by"]), ("R-ESRGAN 4x+", 2.0))
        units = p["alwayson_scripts"]["ControlNet"]["args"]
        self.assertEqual(len(units), 1)   # only the IP-Adapter: pose and the rest followed a driving video
        self.assertIn("IPAdapter", units[0]["module"])
        self.assertTrue(p["init_images"][0])

    def test_a_join_also_aims_at_the_next_clips_first_frame(self):
        first, last = os.path.join(TMP, "first.png"), os.path.join(TMP, "last.png")
        picture(first)
        picture(last)
        ad = continuers.animatediff_payload(INFOTEXT, first, "x", 3, 1, end_frame=last)["alwayson_scripts"]["AnimateDiff"]["args"][0]
        self.assertTrue(ad["last_frame"])
        self.assertEqual((ad["latent_power_last"], ad["latent_scale_last"]), (1, 32))
        plain = continuers.animatediff_payload(INFOTEXT, first, "x", 3, 1)["alwayson_scripts"]["AnimateDiff"]["args"][0]
        self.assertIsNone(plain["last_frame"])

    def test_deforum_clip_is_known_by_its_settings_and_restarts_from_the_frame(self):
        folder = os.path.join(TMP, "deforum")
        os.makedirs(folder, exist_ok=True)
        clip = os.path.join(folder, "20260514180121.mp4")
        with open(os.path.join(folder, "20260514180121_settings.txt"), "w", encoding="utf-8") as f:
            json.dump({"animation_mode": "Video Input", "prompts": {"0": "a bunny --neg nsfw", "30": "a fox"},
                       "fps": 15, "max_frames": 120, "use_init": False, "seed": 1}, f)
        found = continuers.recipe(clip)
        self.assertEqual((found["engine"], found["prompt"]), ("deforum", "a bunny"))
        s = continuers.deforum_settings(found["settings"], "D:/frame.png", "a portal", 4, 9)
        self.assertEqual(s["prompts"], {"0": "a portal --neg nsfw"})
        self.assertEqual((s["use_init"], s["init_image"], s["max_frames"], s["seed"]), (True, "D:/frame.png", 60, 9))
        self.assertEqual(s["animation_mode"], "3D")

    def test_anything_else_is_wan(self):
        self.assertEqual(continuers.recipe(os.path.join(TMP, "other.mp4"))["engine"], "wan")
        self.assertEqual(continuers.recipe("x.mp4", {"prompt": "p"})["engine"], "wan")


if __name__ == "__main__":
    unittest.main()

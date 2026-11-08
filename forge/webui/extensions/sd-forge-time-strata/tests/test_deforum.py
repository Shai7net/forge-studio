"""venv\\Scripts\\python.exe tests\\test_deforum.py  (no Forge needed): scenes, blends, ControlNet units, the
texture fill, the live cut and the DeforumationQT reader."""
import os
import pickle
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from strata import deforum, dqt  # noqa: E402

FOUND = {"units": {
    "pose": {"model": "control_v11p_sd15_openpose [cab727d4]", "module": "dw_openpose_full"},
    "depth": {"model": "sd-controlnet-depth", "module": "depth_midas"},
    "edges": {"model": "sd-controlnet-canny", "module": "canny"},
    "shape": {"model": "qrCodeMonster_v20", "module": "None"},
    "look": {"model": None, "module": "CLIP-ViT-H (IPAdapter)"},
}}


class Scenes(unittest.TestCase):
    def test_stages_of_an_older_project_become_scenes(self):
        options = {"stages": [{"at": 0.5, "text": "old"}, {"at": 0, "text": "young"}]}
        scenes = deforum.scenes_of(options, lambda at: at * 10)
        self.assertEqual([(s["from"], s["text"]) for s in scenes], [(0.0, "young"), (5.0, "old")])

    def test_no_scene_is_the_prompt(self):
        self.assertEqual(deforum.scenes_of({"prompt": "glass"}, lambda at: at)[0]["text"], "glass")

    def test_the_first_scene_starts_at_once(self):
        scenes = deforum.scenes_of({"scenes": [{"from": 2, "text": "a"}, {"from": 5, "text": "b"}]}, lambda at: at)
        self.assertEqual(scenes[0]["from"], 0.0)

    def test_blend_is_centred_and_smooth(self):
        scenes = [{"from": 0, "text": "a"}, {"from": 4, "text": "b"}]
        self.assertEqual(deforum.weights_at(scenes, 1, 2), [(0, 1.0)])
        self.assertEqual(deforum.weights_at(scenes, 6, 2), [(1, 1.0)])
        mid = dict(deforum.weights_at(scenes, 4, 2))
        self.assertAlmostEqual(mid[0], 0.5)
        self.assertAlmostEqual(mid[1], 0.5)
        early, late = dict(deforum.weights_at(scenes, 3.2, 2)), dict(deforum.weights_at(scenes, 4.8, 2))
        self.assertGreater(early[0], 0.8)
        self.assertGreater(late[1], 0.8)
        self.assertEqual(deforum.weights_at(scenes, 4, 0), [(1, 1.0)])

    def test_prompts_change_only_where_they_change_and_say_a_lora_once(self):
        options = {"prefix": "<lora:LCM_SD15:1>", "suffix": "full body", "negative": "text", "blend": 1.0}
        scenes = [{"from": 0, "text": "water"}, {"from": 2, "text": "fire <lora:LCM_SD15:1>"}]
        times = [n / 4 for n in range(16)]           # 4 s at 4 frames a second
        keyed = deforum._prompts(options, scenes, times)
        self.assertIn("0", keyed)
        self.assertTrue(all(p.count("<lora:LCM_SD15:1>") == 1 for p in keyed.values()))
        blended = [p for p in keyed.values() if " AND " in p]
        self.assertTrue(blended, "the blend is written with composable weights")
        self.assertTrue(all(":0." in p for p in blended))
        self.assertIn("fire", keyed[max(keyed, key=int)])
        self.assertNotIn(" AND ", keyed[max(keyed, key=int)])
        self.assertLess(len(keyed), 16, "frames with the same prompt are not written again")
        for prompt in keyed.values():
            positive, negative = prompt.split("--neg")
            self.assertIn(deforum.BACKDROP_WORDS, positive)
            self.assertIn("floor", negative)


class SteadyLook(unittest.TestCase):
    def test_each_scene_is_anchored_where_its_figure_is_largest(self):
        scenes = [{"from": 0.0}, {"from": 1.0}, {"from": 2.0}]
        measures = []
        for k in range(36):            # 12 frames a second; a crouch in the first scene, a big stance at 5, 17, 30
            area = 0.2 + (0.3 if k in (5, 17, 30) else 0.0)
            measures.append({"area": area, "top": 100, "bottom": 600, "empty": k == 25})
        self.assertEqual(deforum.anchor_frames(scenes, measures, 0, 35, 12), [5, 17, 30])

    def test_without_measures_the_middle_is_taken_and_stays_in_range(self):
        scenes = [{"from": 0.0}, {"from": 2.0}]
        self.assertEqual(deforum.anchor_frames(scenes, [], 6, 30, 12), [15, 27])


class Redraw(unittest.TestCase):
    def test_drawn_frames_map_back_to_their_motion_frames(self):
        # (a chain drawn before the motion frames were kept: by its first frame and step)
        frames, picks = deforum.redraw_map({"options": {"first": 10, "every": 2}}, [5, 0, 5, 99, -1, 3], 20)
        self.assertEqual(frames, [0, 3, 5])
        self.assertEqual(picks, [10, 16, 20])
        self.assertEqual(deforum.redraw_map({}, [7], 8), ([7], [7]))
        # frames with no figure were left out: the kept list says where each drawn frame came from
        self.assertEqual(deforum.redraw_map({"motion_frames": [0, 1, 2, 5, 6], "options": {"every": 1}}, [2, 3], 5), ([2, 3], [2, 5]))

    def test_a_new_seed_is_never_one_the_chain_was_drawn_with(self):
        drawn_with = {"options": {"seed": 1234}, "redraw_seeds": [1234 + 7919]}
        # a page opened again proposes the first redraw's seed once more: it moves on
        self.assertEqual(deforum.redraw_seed(drawn_with, 1234 + 7919), 1234 + 2 * 7919)
        self.assertEqual(deforum.redraw_seed(drawn_with, 1234), 1234 + 2 * 7919)
        self.assertEqual(deforum.redraw_seed(drawn_with, 99), 99)
        self.assertIsNone(deforum.redraw_seed(drawn_with, None))
        self.assertEqual(deforum.redraw_seed({}, 5), 5)


class Units(unittest.TestCase):
    def test_an_older_project_has_the_pose_alone_at_its_weight(self):
        units = deforum._units({"pose": 0.7})
        self.assertTrue(units["pose"]["on"])
        self.assertEqual(units["pose"]["weight"], 0.7)
        self.assertFalse(any(units[k]["on"] for k in ("depth", "edges", "shape", "look")))

    def test_units_fill_the_slots_in_order_and_the_rest_are_off(self):
        options = dict(deforum.DEFAULTS, units={"pose": {"on": True, "weight": 1.0}, "depth": {"on": True, "weight": 0.4, "end": 0.5},
                                                "shape": {"on": True}, "look": {"on": True}})
        motion = {"video": "m.mp4", "init": "i.mp4", "mask": "k.mp4", "shape": "s.mp4", "frames": 4, "width": 512, "height": 768,
                  "fps": 12, "times": [0, 0.1, 0.2, 0.3], "looking": False}
        data = deforum.settings({}, motion, options, "x", found=FOUND)
        self.assertEqual(data["cn_1_module"], "dw_openpose_full")
        self.assertEqual(data["cn_2_module"], "depth_midas")
        self.assertEqual(data["cn_2_guidance_end"], "0:(0.5)")
        self.assertEqual(data["cn_3_model"], "qrCodeMonster_v20")
        self.assertEqual(data["cn_3_vid_path"], "s.mp4")
        self.assertFalse(data["cn_4_enabled"], "no IP-Adapter model and no look picture: the look unit is left out")
        self.assertFalse(data["cn_5_enabled"])
        self.assertEqual(data["video_init_path"], "i.mp4")
        self.assertTrue(data["use_mask"])

    def test_models_are_picked_for_sd15(self):
        names = ["controlnet-canny-sdxl", "sd-controlnet-canny", "control_v1p_sd15_qrcode_monster", "qrCodeMonster_v20",
                 "ip-adapter_pulid_sdxl_fp16", "ipAdapterPlusSd15_ipAdapterPlusSd15", "ip-adapter-plus_sd15"]
        self.assertEqual(deforum._pick(names, "edges"), "sd-controlnet-canny")
        self.assertEqual(deforum._pick(names, "shape"), "qrCodeMonster_v20")
        self.assertEqual(deforum._pick(names, "look"), "ip-adapter-plus_sd15")
        self.assertIsNone(deforum._pick(names, "depth"))


class Fill(unittest.TestCase):
    def test_the_figure_takes_the_texture_with_its_shade_and_the_backdrop_stays(self):
        canvas = np.full((40, 30, 3), 128, np.uint8)
        alpha = np.zeros((40, 30), np.uint8)
        alpha[10:30, 10:20] = 255
        canvas[10:20, 10:20] = 200                   # a lit upper body
        canvas[20:30, 10:20] = 80                    # a darker lower one
        texture = np.zeros((40, 30, 3), np.uint8)
        texture[..., 2] = 200                        # blue water
        out = deforum._fill(canvas, alpha, texture, 1.0)
        self.assertTrue((out[:10] == 128).all(), "the backdrop is untouched")
        self.assertGreater(int(out[15, 15, 2]), int(out[25, 15, 2]), "light and shade are kept")
        self.assertLess(int(out[15, 15, 0]), 40, "the figure took the texture's colour")
        half = deforum._fill(canvas, alpha, texture, 0.5)
        self.assertTrue(int(out[15, 15, 0]) < int(half[15, 15, 0]) < 200)


class TextureTime(unittest.TestCase):
    def test_a_texture_video_follows_the_motions_time(self):
        import subprocess
        import tempfile
        from strata import media
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "t.mp4")
            command = [media.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "32x32",
                       "-r", "24", "-i", "-", "-c:v", "libx264", "-crf", "1", "-pix_fmt", "yuv444p", path]
            process = subprocess.Popen(command, stdin=subprocess.PIPE)
            for k in range(48):                        # two seconds: dark, then light
                process.stdin.write(np.full((32, 32, 3), 30 if k < 24 else 220, np.uint8).tobytes())
            process.stdin.close()
            self.assertEqual(process.wait(), 0)
            texture = deforum._Texture(path, 16, 16)
            self.assertLess(int(texture.at(0.2).mean()), 80)
            self.assertGreater(int(texture.at(1.5).mean()), 170, "drawn frames far apart still get the texture of their time")
            self.assertLess(int(texture.at(0.5).mean()), 80, "and going back works")
            self.assertGreater(int(texture.at(3.5).mean()), 170, "it loops")
            texture.close()

class HebrewProject(unittest.TestCase):
    def test_deforum_works_in_a_plain_ascii_folder(self):
        # (Deforum writes the frames it takes out of a video with OpenCV, which writes nothing under a Hebrew name)
        here = os.path.join("D:\\", "x", "chains", "c1")
        self.assertEqual(deforum._work_root(here), os.path.join(here, "deforum"))
        hebrew = os.path.join("D:\\", "השכבות שלי2", "chains", "c1")
        work = deforum._work_root(hebrew)
        self.assertTrue(os.path.abspath(work).isascii())
        self.assertNotEqual(work, deforum._work_root(os.path.join("D:\\", "השכבות שלי1", "chains", "c1")), "one folder for each chain")


class SkipEmpty(unittest.TestCase):
    def test_motion_frames_with_no_figure_are_not_drawn(self):
        import tempfile
        from PIL import Image
        from strata import cut, store

        class Job:
            def step(self, *a, **k):
                pass

        with tempfile.TemporaryDirectory() as root:
            before = os.environ.get("STRATA_ROOT")
            os.environ["STRATA_ROOT"] = root
            try:
                os.makedirs(os.path.join(root, "p"))
                with open(os.path.join(root, "p", "project.json"), "w") as file:
                    file.write("{}")
                make = os.path.join(store.chain_dir("p", "motion", create=True), "v0001")
                os.makedirs(make)
                measures = []
                for k in range(8):
                    alpha = np.zeros((40, 24), np.uint8)
                    if k not in (3, 4):                    # a whip pan: no figure in frames 3 and 4
                        alpha[8:38, 8:16] = 255
                    Image.fromarray(np.dstack([np.full((40, 24, 3), 200, np.uint8), alpha]), "RGBA").save(os.path.join(make, f"{k:06d}.webp"))
                    measures.append(cut.measure(alpha / 255.0))
                store.write_chain("p", "motion", {"make": "v0001", "frames": 8, "fps": 24, "width": 24, "height": 40, "measures": measures})
                scenes = [{"from": 0.0, "text": "glass"}]
                options = dict(deforum.DEFAULTS)
                out = os.path.join(root, "drawn")
                os.makedirs(out)
                motion = deforum._motion(Job(), "p", "motion", out, options, scenes, set())
                self.assertEqual(motion["picked"], [0, 1, 2, 5, 6, 7])
                self.assertEqual(motion["skipped"], [3, 4])
                self.assertEqual(motion["frames"], 6)
                self.assertEqual(motion["times"], [k / 24 for k in (0, 1, 2, 5, 6, 7)], "the scenes keep the motion's own time")
                self.assertEqual(len(os.listdir(os.path.join(out, "prior"))), 6)
                options["skip_empty"] = False
                self.assertEqual(deforum._motion(Job(), "p", "motion", out, options, scenes, set())["picked"], list(range(8)))
                # frames picked by hand (drawn again) are drawn as asked
                self.assertEqual(deforum._motion(Job(), "p", "motion", out, dict(deforum.DEFAULTS), scenes, set(), pick=[3, 5])["picked"], [3, 5])
            finally:
                if before is None:
                    os.environ.pop("STRATA_ROOT", None)
                else:
                    os.environ["STRATA_ROOT"] = before


class Live(unittest.TestCase):
    def test_a_drawn_frame_is_cut_where_the_motion_stands(self):
        W, H, w, h = 64, 128, 50, 120
        left, top = (W - w) // 2, H - h
        drawn = np.full((H, W, 3), 128, np.uint8)
        drawn[top + 30:top + 110, left + 20:left + 30] = (220, 40, 40)       # the drawn figure, red
        prior = np.zeros((H, W), np.float32)
        prior[top + 30:top + 110, left + 20:left + 30] = 1.0
        motion = {"record": {"width": w, "height": h}, "left": left, "top": top}
        placed = deforum.live_frame(drawn, prior, motion, "#808080")
        self.assertEqual(placed.shape, (h, w, 4))
        self.assertEqual(int(placed[5, 5, 3]), 0, "the backdrop is gone")
        self.assertGreater(int(placed[70, 25, 3]), 250)
        self.assertGreater(int(placed[70, 25, 0]), 200)


class DeforumationQT(unittest.TestCase):
    def test_answers_are_read_as_plain_values_only(self):
        self.assertEqual(dqt._plain(pickle.dumps(["1.5"])), ["1.5"])

        class Sneaky:
            def __reduce__(self):
                return (print, ("never",))
        with self.assertRaises(pickle.UnpicklingError):
            dqt._plain(pickle.dumps([Sneaky()]))

    def test_without_its_mediator_nothing_is_read(self):
        original = dqt.running
        dqt.running = lambda: False
        try:
            self.assertEqual(dqt.read(), {"running": False, "values": {}})
        finally:
            dqt.running = original


if __name__ == "__main__":
    unittest.main()

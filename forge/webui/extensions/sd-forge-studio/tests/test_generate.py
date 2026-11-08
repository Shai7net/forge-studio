"""Generation requests without the GPU: a stand-in for Video Studio checks what Forge Studio asks it for, and that a
request is recorded, run once, and marked done with its video in the library.

Run from the extension folder:  ..\\..\\venv\\Scripts\\python.exe tests\\test_generate.py
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
TMP = tempfile.mkdtemp(prefix="forge-studio-gen-")
os.environ["FORGE_STUDIO_ROOT"] = os.path.join(TMP, "data")
os.environ["FORGE_STUDIO_LIBRARY"] = os.path.join(TMP, "out")
os.environ["FORGE_STUDIO_EXPORTS"] = os.path.join(TMP, "exports")

from forgestudio import generate, library, projects  # noqa: E402

FFMPEG = library.ffmpeg()
CALLS = []


def make_video(path, seconds=1, size="320x576", color="red"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c={color}:s={size}:r=16:d={seconds}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", path], check=True)


def fake_vs():
    """Just enough of videostudio.ui: generate() records its arguments and 'makes' a red video."""
    vs = types.SimpleNamespace()
    vs.MAX_PICTURES = 6
    vs.QUALITY = {"480p - fast": 480 * 832, "720p - best": 720 * 1280}
    vs.DEFAULT_QUALITY = "480p - fast"
    vs.ASPECTS = {"9:16 portrait": (9, 16), "16:9 landscape": (16, 9)}
    vs.UPSCALE_OFF, vs.NO_LORA = "Off", "None"

    def fit_size(w, h, area, multiple=16):
        r = w / h
        hh = (area / r) ** 0.5
        return int(round(hh * r / multiple)) * multiple, int(round(hh / multiple)) * multiple
    vs.fit_size = fit_size
    vs.overall = lambda stage, p: 0.5
    vs.models = types.SimpleNamespace(PRESETS={"Wan Q3": {}}, DEFAULT_PRESET="Wan Q3", missing=lambda p: [])
    vs.runner = types.SimpleNamespace(current=lambda: None, cancel_current=lambda: True)

    def gen(mode, count, *args):
        CALLS.append((mode, count, args))
        yield "<b>Making the video</b>", None, None, None, None
        out = os.path.join(os.environ["FORGE_STUDIO_LIBRARY"], "video-studio", f"made-{len(CALLS)}.mp4")
        make_video(out, color="blue")
        yield "<b>Done</b>", None, out, "details", out[:-4] + ".last.png"
    vs.generate = gen
    return vs


def wait(name, gen_id, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = next(g for g in generate.records(name) if g["id"] == gen_id)
        if r["state"] not in ("waiting", "running"):
            return r
        time.sleep(0.1)
    raise AssertionError("the request never ended")


class Generate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        generate._vs = fake_vs
        generate._ui_defaults = lambda: (lambda label, default: default)
        projects.create("gen")
        cls.clip = os.path.join(TMP, "src", "clip.mp4")
        make_video(cls.clip, seconds=2)
        cls.web = projects.add_media("gen", cls.clip)

    def test_text_clip_in_the_sequence_shape(self):
        CALLS.clear()
        r = generate.submit("gen", "text", "a fox runs", seconds=2, width=1080, height=1920, sound=False,
                            placeholder={"clip": 5, "item": 9})
        done = wait("gen", r["id"])
        self.assertEqual(done["state"], "done", done.get("message"))
        mode, count, args = CALLS[0]
        self.assertEqual((mode, count), ("t2v", 1))
        rest = args[6 + 5:]
        self.assertEqual(len(rest), 28)
        self.assertEqual(rest[0], "9:16 portrait")                  # shape follows the sequence
        self.assertEqual(rest[5], "a fox runs")
        self.assertEqual((rest[17], rest[18]), (480, 848))            # 9:16 at 480p's area, multiples of 16
        self.assertFalse(rest[26])                                    # no sound asked
        self.assertTrue(os.path.isfile(done["video"]))
        self.assertEqual(library.get(done["media"])["path"], done["video"])
        generate.placed("gen", r["id"], "made-1.mp4")
        self.assertEqual(next(g for g in generate.records("gen") if g["id"] == r["id"])["state"], "placed")

    def test_frame_at_the_very_end_of_a_low_fps_clip(self):
        """A 15 fps clip (AnimateDiff's) on a 30 fps timeline: its last frame starts at 1.933 s of 2 s; extending
        from the end asked for 1.97 s and got no frame (2026-11-02). The last frame must come back, and the right
        one (the clip turns from red to green in its last frame)."""
        clip = os.path.join(TMP, "src", "fifteen.mp4")
        subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=64x64:r=15:d=1.9333",
                        "-f", "lavfi", "-i", "color=c=green:s=64x64:r=15:d=0.0667", "-filter_complex",
                        "[0:v][1:v]concat=n=2:v=1[v]", "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p", clip],
                       check=True)
        web = projects.add_media("gen", clip)
        from PIL import Image
        for seconds in (1.97, 2.0, 5.0):
            with Image.open(generate.frame("gen", web, seconds)) as im:
                r, g, b = im.convert("RGB").getpixel((32, 32))
            self.assertGreater(g, r, f"at {seconds} s the last (green) frame")
        with Image.open(generate.frame("gen", web, 0.5)) as im:
            r, g, b = im.convert("RGB").getpixel((32, 32))
        self.assertGreater(r, g)

    def test_transition_takes_both_frames(self):
        CALLS.clear()
        r = generate.submit("gen", "between", "the street turns into a beach", seconds=3,
                            start={"web": self.web, "seconds": 1.9}, end={"web": self.web, "seconds": 0.0})
        done = wait("gen", r["id"])
        self.assertEqual(done["state"], "done", done.get("message"))
        mode, count, args = CALLS[0]
        self.assertEqual((mode, count), ("i2v", 2))
        self.assertTrue(os.path.isfile(args[0]) and os.path.isfile(args[1]))
        from PIL import Image
        with Image.open(args[0]) as im:
            self.assertEqual(im.size, (320, 576))

    def test_take_needs_a_video_studio_clip_and_reuses_its_words(self):
        with self.assertRaises(ValueError):
            generate.submit("gen", "take", take_of=self.web)
        made = os.path.join(TMP, "src", "vs.mp4")
        make_video(made)
        with open(made[:-4] + ".json", "w", encoding="utf-8") as f:
            f.write('{"mode": "t2v", "prompt": "a red balloon", "seed": 7, "frames": 33, "fps": 32, "start_image": null}')
        web = projects.add_media("gen", made)
        CALLS.clear()
        r = generate.submit("gen", "take", take_of=web)
        self.assertEqual((r["kind"], r["prompt"], r["seconds"]), ("text", "a red balloon", 2.0))
        self.assertNotEqual(r["seed"], 7)
        self.assertEqual(wait("gen", r["id"])["state"], "done")

    def test_missing_words_and_pictures_are_refused(self):
        with self.assertRaises(ValueError):
            generate.submit("gen", "text", "  ")
        with self.assertRaises(ValueError):
            generate.submit("gen", "continue", "more")


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()

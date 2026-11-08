"""venv\\Scripts\\python.exe tests\\test_media.py  (no Forge needed): black bars around a video's picture."""
import os
import subprocess
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from strata import media  # noqa: E402


def write_video(path, frames, fps=12):
    h, w = frames[0].shape[:2]
    command = [media.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
               "-r", str(fps), "-i", "-", "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", path]
    process = subprocess.Popen(command, stdin=subprocess.PIPE)
    for frame in frames:
        process.stdin.write(frame.tobytes())
    process.stdin.close()
    assert process.wait() == 0


class Letterbox(unittest.TestCase):
    def test_bars_are_found_and_cut_away(self):
        rng = np.random.default_rng(1)
        frames = []
        for n in range(24):
            frame = np.zeros((256, 128, 3), np.uint8)
            frame[64:192] = rng.integers(40, 220, (128, 128, 3), dtype=np.uint8)       # the picture, between bars
            frames.append(frame)
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "boxed.mp4")
            write_video(path, frames)
            box = media.find_letterbox(path)
            self.assertIsNotNone(box)
            x, y, w, h = box
            self.assertEqual((x, w), (0, 128))
            self.assertLessEqual(abs(y - 64), 2)
            self.assertLessEqual(abs(h - 128), 4)
            first = next(media.read_video(path, crop=box))
            self.assertEqual(first.shape[:2], (h, w))
            self.assertGreater(int(first[:4, :, :3].max()), 30, "the picture starts at the top")

    def test_a_dark_scene_is_not_a_bar(self):
        frames = []
        for n in range(24):
            frame = np.zeros((128, 128, 3), np.uint8)
            frame[:, :] = 30
            frame[:, 20 + n:40 + n] = 200                       # a light moving across, over all the rows
            frame[:8 if n % 2 else 0] = 0
            frames.append(frame)
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "dark.mp4")
            write_video(path, frames)
            self.assertIsNone(media.find_letterbox(path))


class HebrewPaths(unittest.TestCase):
    def test_pictures_are_saved_and_read_in_a_project_named_in_hebrew(self):
        # (his "השכבות שלי2": cv2.imwrite saved nothing there, and every cut ended "the cut of a frame is missing")
        from strata import chain
        with tempfile.TemporaryDirectory() as root:
            folder = os.path.join(root, "השכבות שלי2", "alpha")
            os.makedirs(folder)
            alpha = np.zeros((40, 30), np.float32)
            alpha[10:30, 8:20] = 1.0
            path = os.path.join(folder, "000000.png")
            chain._save_alpha(path, alpha)
            self.assertTrue(os.path.isfile(path))
            self.assertTrue(np.array_equal(chain._load_alpha(path), alpha))
            colour = np.dstack([np.full((8, 6), v, np.uint8) for v in (10, 120, 250, 255)])
            self.assertTrue(media.imwrite(os.path.join(folder, "c.png"), colour))
            self.assertTrue(np.array_equal(media.imread(os.path.join(folder, "c.png"), -1), colour))
            self.assertIsNone(media.imread(os.path.join(folder, "none.png")))


if __name__ == "__main__":
    unittest.main()

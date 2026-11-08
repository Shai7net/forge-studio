"""venv\\Scripts\\python.exe tests\\test_upscale.py: Real-ESRGAN choices; with Deforum's realesrgan-ncnn-vulkan
installed, a real upscale of two small pictures (x2 by the video model, and a x4 model brought down to x2)."""
import os
import sys
import tempfile
import unittest

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from strata import jobs, upscale  # noqa: E402


class Choices(unittest.TestCase):
    def test_choices_are_read(self):
        self.assertEqual(upscale.parse("realesr-animevideov3:2"), ("realesr-animevideov3", 2))
        self.assertEqual(upscale.parse("realesrgan-x4plus"), ("realesrgan-x4plus", 4))
        self.assertIsNone(upscale.parse(None))
        self.assertIsNone(upscale.parse(""))
        with self.assertRaises(jobs.JobError):
            upscale.parse("something-else:2")
        for choice in upscale.CHOICES:
            self.assertIsNotNone(upscale.parse(choice))


@unittest.skipUnless(upscale.find(), "realesrgan-ncnn-vulkan is not installed here")
class Real(unittest.TestCase):
    def test_pictures_come_out_at_the_asked_size(self):
        with tempfile.TemporaryDirectory() as folder:
            source = os.path.join(folder, "in")
            os.makedirs(source)
            for n in range(2):
                picture = np.full((40, 32, 3), 128, np.uint8)
                cv2.circle(picture, (16, 20), 9, (40, 90, 220), -1)
                cv2.imwrite(os.path.join(source, f"{n:06d}.png"), picture)
            for choice in ("realesr-animevideov3:2", "realesrgan-x4plus-anime:2"):
                target = os.path.join(folder, choice.replace(":", "_"))
                self.assertEqual(upscale.upscale_folder(source, target, choice), 2)
                for name in sorted(os.listdir(target)):
                    picture = cv2.imread(os.path.join(target, name))
                    self.assertEqual(picture.shape[:2], (80, 64), choice)
                    self.assertGreater(int(picture[40, 32, 2]), 150, "the red disc is still there, in the middle")

    def test_a_frames_own_transparency_is_kept(self):
        with tempfile.TemporaryDirectory() as folder:
            def frames():
                picture = np.full((64, 56, 4), (90, 140, 200, 255), np.uint8)
                picture[:, :28, 3] = 0                       # the left half is see-through
                yield picture
                yield picture
            got = list(upscale.upscaled_frames(None, os.path.join(folder, "up"), 2, frames, "realesr-animevideov3:2")())
            self.assertEqual(got[0].shape, (128, 112, 4))
            self.assertLess(int(got[0][64, 20, 3]), 10, "the left stays see-through")
            self.assertGreater(int(got[0][64, 90, 3]), 245, "the right stays solid")

    def test_only_a_frame_drawn_again_is_enlarged_again(self):
        with tempfile.TemporaryDirectory() as folder:
            colours = [(200, 60, 60), (60, 200, 60), (60, 60, 200)]

            def frames():
                # (pictures of a size the model handles: a tiny flat one comes back black from its tiles)
                for colour in colours:
                    picture = np.full((64, 56, 4), (128, 128, 128, 255), np.uint8)
                    cv2.circle(picture, (28, 32), 14, (*colour, 255), -1)
                    yield picture
            cache = os.path.join(folder, "upscale")
            again = upscale.upscaled_frames(None, cache, 3, frames, "realesr-animevideov3:2")
            first = [frame.copy() for frame in again()]
            self.assertEqual(first[0].shape[:2], (128, 112))
            self.assertGreater(int(first[0][64, 56, 0]), 150, "the red disc came through")
            kept = os.path.getmtime(os.path.join(cache, "out", "000000.png"))
            # frame 1 is drawn again: its enlarged copy goes, and only it is made again
            colours[1] = (240, 240, 40)
            os.remove(os.path.join(cache, "out", "000001.png"))
            again = upscale.upscaled_frames(None, cache, 3, frames, "realesr-animevideov3:2")
            second = list(again())
            self.assertEqual(os.path.getmtime(os.path.join(cache, "out", "000000.png")), kept, "frame 0 was not made again")
            self.assertGreater(int(second[1][64, 56, 1]), 200, "frame 1 shows its new drawing")
            self.assertGreater(int(second[1][64, 56, 0]), 200)
            self.assertTrue(np.array_equal(second[2], first[2]))


if __name__ == "__main__":
    unittest.main()

"""References fitted to the size chosen in Forge (2026-11-16), without the GPU."""
from pathlib import Path
import sys
import unittest

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fibo2 import fit  # noqa: E402
from fibo2.contracts import Generation  # noqa: E402


class Fit(unittest.TestCase):
    def test_big_reference_takes_the_chosen_size(self):
        big = Image.new("RGB", (1920, 2880), "green")
        refs, mask, note = fit.fit([big], Image.new("L", (1920, 2880), 255), 1024, 576)
        self.assertEqual(refs[0].size, (1024, 576))
        self.assertEqual(mask.size, (1024, 576))
        self.assertIn("1920x2880 -> 1024x576", note)
        # what FIBO refused before: the request is valid now
        Generation("edit-turbo", "edit", "paint it", width=1024, height=576).validated(refs, mask)

    def test_square_reference_follows_a_tall_result(self):
        refs, _, _ = fit.fit([Image.new("RGB", (1024, 1024))], None, 576, 1024)
        self.assertEqual(refs[0].size, (576, 1024))

    def test_crop_stays_on_the_focus(self):
        # a 100x300 picture, white at the top third: a square crop around a focus at the top keeps the white
        im = Image.new("RGB", (100, 300), "black"); im.paste((255, 255, 255), (0, 0, 100, 100))
        cover, box = fit.crop_box(im.size, 100, 100, (0.5, 0.15))
        self.assertEqual(fit.crop_to(im, cover, box).getpixel((50, 50)), (255, 255, 255))
        cover, box = fit.crop_box(im.size, 100, 100, (0.5, 0.99))  # clamped inside the picture
        self.assertEqual(box, (0, 200, 100, 300))

    def test_several_references_keep_their_shape_and_only_shrink(self):
        small, big = Image.new("RGB", (640, 480)), Image.new("RGB", (3000, 2000))
        refs, _, _ = fit.fit([small, big], None, 1024, 1024)
        self.assertEqual(refs[0].size, (640, 480))
        w, h = refs[1].size
        self.assertLessEqual(w * h, fit.REFERENCE_AREA)
        self.assertAlmostEqual(w / h, 1.5, delta=0.02)

    def test_shape_like_keeps_the_area(self):
        w, h = fit.shape_like((1920, 2880), 1024 * 576)
        self.assertEqual((w % 16, h % 16), (0, 0))
        self.assertAlmostEqual(w / h, 2 / 3, delta=0.02)
        self.assertLess(abs(w * h - 1024 * 576) / (1024 * 576), 0.05)


if __name__ == "__main__":
    unittest.main()

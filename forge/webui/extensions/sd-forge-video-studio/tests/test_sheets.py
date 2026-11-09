"""Character sheets as ingredients: which views are kept (no model needed).

Run: webui\\venv\\Scripts\\python.exe -m unittest discover -s extensions\\sd-forge-video-studio\\tests -p test_sheets.py
"""

import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from videostudio import sheets  # noqa: E402


def box(x0, y0, x1, y1):
    return (x0, y0, x1, y1, (x1 - x0) * (y1 - y0))


class Views(unittest.TestCase):
    def test_front_with_arms_out_beats_the_side_view(self):
        """His sheet: front (arms out, wide), side (narrow), back; a block of heads; a close-up cut by the bottom."""
        front, side, back = box(18, 19, 210, 235), box(230, 18, 284, 234), box(310, 18, 498, 232)
        heads, close = box(25, 337, 294, 512), box(326, 270, 497, 512)
        f, c = sheets.pick_views([side, back, front, heads, close], (512, 512))
        self.assertEqual(f, front)
        self.assertEqual(c, close)

    def test_first_of_four_standing_views_is_the_front(self):
        views = [box(27, 28, 121, 272), box(152, 27, 236, 274), box(278, 28, 372, 274), box(397, 28, 491, 275)]
        f, c = sheets.pick_views(views[::-1], (512, 512))
        self.assertEqual(f, views[0])
        self.assertIsNone(c)

    def test_one_figure_is_not_a_sheet(self):
        self.assertEqual(sheets.pick_views([box(100, 50, 300, 500)], (512, 512)), (None, None))


class OneCharacter(unittest.TestCase):
    def picture(self, colours):
        small = np.full((200, 400, 3), 220, np.float32)
        mask = np.zeros((200, 400), bool)
        boxes = []
        for k, colour in enumerate(colours):
            x = 20 + k * 120
            small[20:180, x:x + 60] = colour
            mask[20:180, x:x + 60] = True
            boxes.append(box(x, 20, x + 60, 180))
        return small, mask, boxes

    def test_same_clothes_are_one_character(self):
        small, mask, boxes = self.picture([(250, 250, 250), (250, 250, 250), (245, 248, 250)])
        self.assertTrue(sheets.one_character(small, mask, boxes, boxes[0]))

    def test_different_people_are_not(self):
        small, mask, boxes = self.picture([(250, 250, 250), (30, 30, 120), (200, 40, 40)])
        self.assertFalse(sheets.one_character(small, mask, boxes, boxes[0]))


class Backdrop(unittest.TestCase):
    def test_lit_studio_backdrop_is_plain_and_texture_is_not(self):
        yy, xx = np.mgrid[0:256, 0:256].astype(np.float32)
        lit = np.repeat((160 + 50 * xx / 255 + 10 * yy / 255)[..., None], 3, axis=2)
        self.assertTrue(sheets.plain_backdrop(lit))
        noisy = lit + np.random.default_rng(0).normal(0, 25, lit.shape).astype(np.float32)
        self.assertFalse(sheets.plain_backdrop(noisy))


if __name__ == "__main__":
    unittest.main()

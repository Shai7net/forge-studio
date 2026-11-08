"""venv\\Scripts\\python.exe tests\\test_cut.py  (no Forge needed)"""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from strata import cut  # noqa: E402


def figure_alpha(h=200, w=120, x=60, top=30, bottom=190, half=18):
    """A soft-edged upright figure (a rounded bar) as alpha."""
    yy, xx = np.mgrid[0:h, 0:w]
    inside = np.clip((half - np.abs(xx - x)) / 2.0, 0, 1) * np.clip((yy - top) / 2.0, 0, 1) * np.clip((bottom - yy) / 2.0, 0, 1)
    return inside.astype(np.float32)


class Clean(unittest.TestCase):
    def test_faint_background_becomes_exactly_zero(self):
        a = figure_alpha()
        noisy = np.clip(a + 0.03, 0, 1)              # a 3% fog everywhere
        clean = cut.clean_alpha(noisy)
        self.assertEqual(float(clean[:20].max()), 0.0)
        # three hundred frames of it stacked: still nothing outside the figure
        outside = clean[a == 0]
        self.assertEqual(float(1 - np.prod(1 - outside.max()) ** 300), 0.0)
        self.assertGreater(float(clean[100, 60]), 0.99)

    def test_specks_are_removed_and_parts_kept(self):
        a = figure_alpha()
        a[5:8, 5:8] = 1.0                             # a speck
        a[100:150, 100:115] = 1.0                     # a second real part (another dancer's arm, 750 px)
        clean = cut.clean_alpha(a, keep_parts=0.03)
        self.assertEqual(float(clean[6, 6]), 0.0)
        self.assertEqual(float(clean[120, 108]), 1.0)

    def test_light_from_above_goes_and_a_second_figure_stays(self):
        a = figure_alpha(x=40)
        a[0:18, 30:60] = 1.0                          # light falling from the ceiling, above the head
        a[40:190, 90:112] = 1.0                       # a second figure beside the first
        clean = cut.clean_alpha(a)
        self.assertEqual(float(clean[0:18, 30:60].max()), 0.0)
        self.assertEqual(float(clean[120, 100]), 1.0)
        self.assertEqual(float(clean[100, 40]), 1.0)

    def test_small_holes_inside_are_filled(self):
        a = figure_alpha()
        a[100:103, 58:61] = 0.0
        self.assertEqual(float(cut.clean_alpha(a)[101, 59]), 1.0)

    def test_prior_keeps_nothing_far_from_the_known_figure(self):
        a = figure_alpha()
        a[40:60, 100:118] = 1.0                       # something the matte saw that the motion figure is not
        prior = figure_alpha()
        clean = cut.clean_alpha(a, keep_parts=0.0, prior=prior)
        self.assertEqual(float(clean[50, 110]), 0.0)
        self.assertGreater(float(clean[100, 60]), 0.99)


class Colours(unittest.TestCase):
    def test_unmix_is_exact_over_a_known_backdrop(self):
        a = figure_alpha()
        red = np.array([200, 40, 30], np.float32)
        backdrop = np.array([128, 128, 128], np.float32)
        seen = a[..., None] * red + (1 - a[..., None]) * backdrop
        figure = cut.unmix(seen.round().astype(np.uint8), a, backdrop) * 255
        edge = (a > 0.3) & (a < 0.9)
        self.assertTrue(edge.any())
        self.assertLess(float(np.abs(figure[edge] - red).max()), 6.0)

    def test_key_matte_finds_the_figure(self):
        a = figure_alpha()
        seen = (a[..., None] * np.array([220, 60, 50]) + (1 - a[..., None]) * np.array([128, 128, 128])).astype(np.uint8)
        colour, spread = cut.border_colour(seen)
        self.assertLess(spread, 1.0)
        matte = cut.key_matte(seen, colour)
        self.assertEqual(float(matte[5, 5]), 0.0)
        self.assertGreater(float(matte[100, 60]), 0.99)


class Align(unittest.TestCase):
    def measures(self, n=40):
        out = []
        for k in range(n):
            x = 50 + 10 * np.sin(k / 6) + (3 if k % 2 else -3)       # sway plus jitter
            out.append(cut.measure(figure_alpha(x=int(round(x)), top=30 + (k % 3), bottom=190)))
        return out

    def test_centre_holds_the_figure_in_the_middle_on_one_ground(self):
        ms = self.measures()
        plan, width = cut.plan_alignment(ms, 300, "centre", baseline=0.95, figure=0.8, smooth=3)
        scales = {round(p[0], 6) for p in plan}
        self.assertEqual(len(scales), 1, "one size for all")
        feet = [p[0] * m["feet_y"] + p[2] for p, m in zip(plan, ms)]
        self.assertLess(max(abs(f - 0.95 * 300) for f in feet), 1.0)
        centres = [p[0] * m["torso_x"] + p[1] - width / 2 for p, m in zip(plan, ms)]
        self.assertLess(max(abs(c) for c in centres[1:-1]), 1.5, "the body is held in the middle, shake and all")
        swayed, width = cut.plan_alignment(ms, 300, "sway", baseline=0.95, figure=0.8, smooth=3)
        kept = [p[0] * m["torso_x"] + p[1] - width / 2 for p, m in zip(swayed, ms)]
        self.assertGreater(max(abs(c) for c in kept), 3.0, "'sway' keeps the quick movement")

    def test_a_raised_arm_does_not_move_the_body(self):
        still = figure_alpha()
        raised = still.copy()
        raised[30:60, 78:110] = 1.0                   # an arm up and out to one side
        a, b = cut.measure(still), cut.measure(raised)
        self.assertGreater(abs(b["cx"] - a["cx"]), 2.0)
        self.assertLess(abs(b["torso_x"] - a["torso_x"]), 0.6)

    def test_nothing_is_cut_off(self):
        ms = self.measures()
        plan, width = cut.plan_alignment(ms, 300, "centre", baseline=0.95, figure=0.95, margin=0.02)
        tops = [p[0] * m["top"] + p[2] for p, m in zip(plan, ms)]
        self.assertGreaterEqual(min(tops), 0.02 * 300 - 1.0, "a figure too tall for the frame is made smaller")
        for p, m in zip(plan, ms):
            self.assertGreaterEqual(p[0] * m["left"] + p[1], -1)
            self.assertLessEqual(p[0] * m["right"] + p[1], width + 1)

    def test_suspects(self):
        ms = self.measures()
        ms[20] = cut.measure(figure_alpha(half=40))
        ms[25] = {"empty": True, "width": 120, "height": 200}
        self.assertEqual(cut.suspects(ms), [20, 25])
        self.assertEqual(cut.figureless(ms), [25])

    def test_a_gap_between_shots_does_not_make_its_neighbours_suspect(self):
        # (Four Elements: a whip pan left four frames with no figure or a sliver, and the three before them were
        # called too large)
        ms = self.measures()
        sliver = np.zeros((200, 120), np.float32)
        sliver[100:104, 50:70] = 1.0
        for k in (21, 23):
            ms[k] = cut.measure(sliver)
        for k in (22, 24):
            ms[k] = {"empty": True, "width": 120, "height": 200}
        self.assertEqual(cut.figureless(ms), [21, 22, 23, 24])
        self.assertEqual(cut.suspects(ms), [21, 22, 23, 24], "the figureless ones only (not believed when placing)")

    def test_a_cut_is_judged_by_the_drawing_on_a_flat_backdrop(self):
        frame = np.full((200, 120, 3), 128, np.uint8)
        frame[30:190, 30:90] = (60, 160, 220)                 # what was drawn
        right = np.zeros((200, 120), np.float32)
        right[30:190, 30:90] = 1.0
        kept, spill = cut.agreement(frame, right, (128, 128, 128))
        self.assertGreater(kept, 0.99)
        self.assertLess(spill, 0.01)
        folded = frame.copy()
        folded[80:150, 45:75] = 128                           # a grey fold of the robe, the backdrop's own colour
        self.assertLess(cut.agreement(folded, right, (128, 128, 128))[1], 0.01, "inside the figure is not backdrop")
        half = right.copy()
        half[90:, :] = 0.0                                    # the body below the chest lost
        wide = np.ones((200, 120), np.float32)                # the backdrop kept
        found = [cut.agreement(frame, a, (128, 128, 128)) for a in (right, half, wide, np.zeros((200, 120), np.float32))]
        self.assertEqual(cut.disagreeing(found, skip=[3]), [1, 2])

    def test_render_frame_places_and_never_brings_fog_back(self):
        a = figure_alpha()
        frame = np.dstack([np.full((200, 120, 3), 128, np.uint8), (a * 255).astype(np.uint8)])
        placed = cut.render_frame(frame, a, (0.6, 10.0, 5.0), 100, 140, background=[128, 128, 128])
        self.assertEqual(placed.shape, (140, 100, 4))
        alpha = placed[..., 3]
        self.assertEqual(int(alpha[:20].max()), 0)
        self.assertEqual(int(((alpha > 0) & (alpha < 2)).sum()), 0)
        ys, xs = np.nonzero(alpha > 128)
        self.assertAlmostEqual(xs.mean(), 0.6 * 60 + 10, delta=1.5)


class Steady(unittest.TestCase):
    def test_a_piece_that_is_there_for_one_frame_is_taken_away(self):
        import tempfile
        import cv2
        from strata import chain

        class Job:
            def step(self, *a, **k):
                pass

        with tempfile.TemporaryDirectory() as folder:
            measures = []
            for k in range(12):
                a = figure_alpha(x=40 + 2 * k)
                if k == 6:
                    a[10:150, 86:116] = 1.0           # a lit pillar beside the figure, for one frame
                cv2.imwrite(os.path.join(folder, f"{k:06d}.png"), (a * 255).astype(np.uint8))
                measures.append(cut.measure(a))
            self.assertIn(6, cut.suspects(measures))
            steadied = chain._steady_suspects(Job(), folder, measures)
            after = cv2.imread(os.path.join(folder, "000006.png"), cv2.IMREAD_GRAYSCALE)
            self.assertEqual(int(after[20:140, 100:116].max()), 0, "the pillar is gone")
            self.assertGreater(int(after[100, 52]), 250, "the figure stays")
            self.assertNotIn(6, cut.suspects(steadied))



class KnownFigure(unittest.TestCase):
    def test_a_thrown_rock_larger_than_the_figure_does_not_take_its_place(self):
        a = figure_alpha(x=90, top=60, bottom=190, half=8)        # a thin figure, low in the picture
        a[25:85, 5:60] = 1.0                                       # a big rock it throws, beside and above its head
        prior = figure_alpha(x=90, top=60, bottom=190, half=9)
        clean = cut.clean_alpha(a, prior=prior, prior_reach=0.5)
        self.assertGreater(float(clean[120, 90]), 0.99, "the figure stays")
        self.assertGreater(float(clean[50, 30]), 0.99, "and the rock it throws too, as it lies within reach")
        alone = cut.clean_alpha(a)
        self.assertEqual(float(alone[150, 90]), 0.0, "(without knowing where the figure is, the rock was taken for it)")
        far = cut.clean_alpha(a, prior=prior, prior_reach=0.03)
        self.assertGreater(float(far[120, 90]), 0.99, "the figure stays when the rock is out of reach")
        self.assertEqual(float(far[50, 30]), 0.0)

class Decide(unittest.TestCase):
    def test_a_dark_fade_in_is_not_taken_for_a_flat_backdrop(self):
        from strata import chain
        rng = np.random.default_rng(3)
        dark = np.zeros((64, 48, 4), np.uint8)
        dark[..., 3] = 255
        busy = rng.integers(0, 255, (64, 48, 4), dtype=np.uint8)
        busy[..., 3] = 255
        options = chain._options({})
        self.assertEqual(chain._decide([dark] * 8 + [busy] * 8, options)["matte"], "model")
        flat = np.zeros((64, 48, 4), np.uint8)
        flat[...] = (40, 200, 60, 255)
        flat[20:44, 18:30, :3] = 200                    # a figure in the middle of a green screen
        decided = chain._decide([dark] * 8 + [flat] * 8, options)
        self.assertEqual(decided["matte"], "key")
        self.assertEqual([round(c) for c in decided["key_colour"]], [40, 200, 60])

if __name__ == "__main__":
    unittest.main()

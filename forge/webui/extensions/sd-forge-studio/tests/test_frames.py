"""Carrying a frame edit through a clip, measured on a made-up clip where the right answer is known: a textured
square moves across a textured background; the square is recoloured on one frame; the recolour must follow the
square on every frame, both ways, and leave the background alone.

Run from the extension folder:  ..\\..\\venv\\Scripts\\python.exe tests\\test_frames.py
"""

import os
import sys
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from forgestudio import frames as fr  # noqa: E402

W, H, N, SIZE = 320, 240, 24, 60


def scene(k, rng_bg, tex):
    """Frame k: the background drifts a little, the square moves 4 px right and 2 px down a frame."""
    img = np.roll(rng_bg, k, axis=1).copy()
    x, y = 40 + 4 * k, 60 + 2 * k
    img[y:y + SIZE, x:x + SIZE] = tex
    return img, (x, y)


class Carry(unittest.TestCase):
    def test_recolour_follows_the_square_both_ways(self):
        rng = np.random.default_rng(0)
        bg = (rng.integers(40, 90, (H, W, 3))).astype(np.uint8)
        bg = __import__("cv2").GaussianBlur(bg, (0, 0), 2)
        tex = rng.integers(120, 200, (SIZE, SIZE, 3)).astype(np.uint8)
        tex = __import__("cv2").GaussianBlur(tex, (0, 0), 1.5)
        frames, places = zip(*(scene(k, bg, tex) for k in range(N)))
        frames = list(frames)
        k0 = 10
        edited = frames[k0].copy()
        x, y = places[k0]
        edited[y:y + SIZE, x:x + SIZE, 0] = np.clip(edited[y:y + SIZE, x:x + SIZE, 0].astype(int) + 60, 0, 255)  # bluer (BGR)
        change, weight = fr.change_of(frames[k0], edited)
        out, weights = fr.carried(frames, k0, change, weight)
        for k in (0, 5, 10, 15, 23):
            x, y = places[k]
            inner = (slice(y + 10, y + SIZE - 10), slice(x + 10, x + SIZE - 10))
            gained = out[k][inner][..., 0].astype(int) - frames[k][inner][..., 0].astype(int)
            self.assertGreater(gained.mean(), 45, f"frame {k}: the square lost its new colour ({gained.mean():.1f})")
            outside = np.ones((H, W), bool)
            outside[max(0, y - 12):y + SIZE + 12, max(0, x - 12):x + SIZE + 12] = False
            spill = np.abs(out[k].astype(int) - frames[k].astype(int))[outside].mean()
            self.assertLess(spill, 1.0, f"frame {k}: the background changed ({spill:.2f})")
        # steadiness: inside the edit the result changes from frame to frame about as much as the original does
        result, original = fr.flicker(out, frames, weights, every=1)
        self.assertLess(result, original * 1.25 + 1.0)

    def test_nothing_changed_means_nothing_to_carry(self):
        a = np.full((40, 40, 3), 100, np.uint8)
        _, weight = fr.change_of(a, a.copy())
        self.assertEqual(weight.max(), 0.0)


if __name__ == "__main__":
    unittest.main()

"""The beat finder on music made here, with known beats.

Run from the extension folder:  ..\\..\\venv\\Scripts\\python.exe tests\\test_beats.py
"""

import os
import subprocess
import sys
import tempfile
import unittest
import wave

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
TMP = tempfile.mkdtemp(prefix="forge-studio-beats-")
os.environ["FORGE_STUDIO_ROOT"] = os.path.join(TMP, "data")

from forgestudio import beats  # noqa: E402

SR = 44100


def song(path, bpm, start, seconds, accent_every=4, swing_noise=0.0, seed=1):
    """Kick on every beat (louder on every fourth), a hi-hat between, pad and noise under it."""
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    t = np.arange(n) / SR
    y = 0.05 * np.sin(2 * np.pi * 220 * t) + 0.03 * np.sin(2 * np.pi * 330 * t) + 0.02 * rng.standard_normal(n)
    period = 60.0 / bpm
    times = []
    k = 0
    while start + k * period < seconds - 0.3:
        at = start + k * period + (rng.normal(0, swing_noise) if swing_noise else 0)
        times.append(at)
        i = int(at * SR)
        d = np.arange(int(0.25 * SR))
        kick = np.sin(2 * np.pi * (50 + 120 * np.exp(-d / 800)) * d / SR) * np.exp(-d / 3000)
        amp = 1.0 if k % accent_every == 0 else 0.5
        y[i:i + d.size] += amp * kick[: max(0, min(d.size, n - i))]
        h = int((at + period / 2) * SR)
        hh = rng.standard_normal(int(0.03 * SR)) * np.exp(-np.arange(int(0.03 * SR)) / 300) * 0.15
        y[h:h + hh.size] += hh[: max(0, min(hh.size, n - h))]
        k += 1
    y = (y / np.abs(y).max() * 0.8 * 32767).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(y.tobytes())
    return np.array(times)


class Beats(unittest.TestCase):
    def check(self, bpm, start, seconds=20, swing=0.0):
        path = os.path.join(TMP, f"song-{bpm}.wav")
        truth = song(path, bpm, start, seconds, swing_noise=swing)
        found = beats.analyse(path)
        self.assertAlmostEqual(found["tempo"], bpm, delta=bpm * 0.02, msg=found["tempo"])
        got = np.array(found["beats"])
        # every true beat has a found beat within 35 ms, and nearly nothing extra
        near = [np.min(np.abs(got - b)) for b in truth]
        self.assertLess(np.percentile(near, 90), 0.035, (bpm, sorted(near)[-5:]))
        self.assertLessEqual(len(got), len(truth) + 2)
        # the "one" of the bar is the louder kick
        ones = got[found["bar"]::4]
        accented = truth[::4]
        hits = sum(np.min(np.abs(accented - o)) < 0.05 for o in ones)
        self.assertGreater(hits, 0.7 * len(ones), (found["bar"], hits, len(ones)))
        return found

    def test_128(self):
        self.check(128, 0.5)

    def test_90_late_start(self):
        self.check(90, 2.0)

    def test_174_fast(self):
        self.check(174, 0.3)

    def test_played_by_hand(self):
        self.check(110, 0.4, swing=0.008)

    def test_cached(self):
        path = os.path.join(TMP, "song-cache.wav")
        song(path, 120, 0.5, 8)
        from forgestudio import media
        a = beats.beats(path)
        self.assertTrue(os.path.isfile(media._cache(media._key(path) + f"-beats{beats.VERSION}.json")))
        self.assertEqual(a, beats.beats(path))


def tearDownModule():
    import shutil
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=1)

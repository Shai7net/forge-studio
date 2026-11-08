"""The recorder's sound: how loud it comes out, and sounds that step back under speech. Runs without Forge:

    venv\\Scripts\\python.exe extensions\\sd-forge-simulation-360\\tests\\test_sound.py
"""
import math
import os
import sys
import tempfile
import unittest
import wave

import numpy

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from sim360 import export  # noqa: E402

RATE = 48000


def tone(path, seconds, pitch, level):
    """A sine written as a wav file."""
    t = numpy.arange(int(seconds * RATE)) / RATE
    sound = numpy.sin(2 * math.pi * pitch * t) * level
    with wave.open(path, "wb") as file:
        file.setnchannels(1); file.setsampwidth(2); file.setframerate(RATE)
        file.writeframes((numpy.clip(sound, -1, 1) * 32767).astype("<i2").tobytes())
    return path


def level_of(sound, pitch, start, end):
    """How strong one pitch is inside a stretch of a recording, in dB."""
    piece = sound[int(start * RATE):int(end * RATE)]
    t = numpy.arange(len(piece)) / RATE
    strength = abs(numpy.sum(piece * numpy.exp(-2j * math.pi * pitch * t))) * 2 / max(1, len(piece))
    return 20 * math.log10(max(strength, 1e-9))


class Loudness(unittest.TestCase):
    def test_a_quiet_mix_is_turned_up_only_as_far_as_its_peaks_allow(self):
        quiet = [-30.0] * 990 + [-9.0] * 10                       # nearly all of it is far below the ceiling
        self.assertAlmostEqual(export.loud_gain(-30.0, quiet), 11.5, places=6)       # held by its one loud moment: 4 dB off it, no more
        self.assertAlmostEqual(export.loud_gain(-16.0, quiet), 2.0, places=6)        # room to spare: all the way to -14
        self.assertAlmostEqual(export.loud_gain(-10.0, quiet), -4.0, places=6)       # too loud: simply turned down
        spoken = [-30.0] * 900 + [-12.0] * 50 + [-10.0] * 50      # speech: a tenth of it is tall peaks
        self.assertAlmostEqual(export.loud_gain(-26.0, spoken), 8.5, places=6)       # up to where the tallest twentieth reaches the ceiling: -1.5 - (-10)
        peaky = [-20.0] * 900 + [-1.0] * 100                      # a tenth of it is already at the top
        self.assertEqual(export.loud_gain(-20.0, peaky), 0.0)     # nothing can be added without squashing it
        self.assertEqual(export.loud_gain(-20.0, []), 0.0)        # nothing is known: nothing is added

    def test_sounds_step_back_while_a_line_is_spoken_and_come_back_in_the_gaps(self):
        self.assertIsNone(export.duck_expression([], 9))
        self.assertIsNone(export.duck_expression([(1, 2)], 0))
        text = export.duck_expression([(1.0, 2.0), (2.2, 3.0), (6.0, 7.0)], 6.0)
        self.assertEqual(text.count("clip("), 4)                  # the first two lines are one stretch: no bump between them
        volume = lambda t: eval(text, {"clip": lambda x, low, high: min(high, max(low, x)), "max": max, "t": t})
        down = 10 ** (-6 / 20)
        self.assertAlmostEqual(volume(0.5), 1.0)                  # before anybody speaks
        self.assertAlmostEqual(volume(1.0), down, places=3)       # down by the time the line begins
        self.assertAlmostEqual(volume(2.1), down, places=3)       # and between two lines that follow closely
        self.assertAlmostEqual(volume(3.0), down, places=3)       # still down on the last word
        self.assertTrue(down < volume(3.15) < 1.0)                # coming back
        self.assertAlmostEqual(volume(4.5), 1.0)                  # back in the gap

    def test_a_recording_comes_out_at_the_asked_loudness_with_its_sounds_ducked(self):
        folder = tempfile.mkdtemp(prefix="sim360-sound-")
        spoken = [(1.0, 3.0), (5.0, 7.0)]
        music = {"path": tone(os.path.join(folder, "music.wav"), 8, 220, 0.02), "start": 0.0, "gain": 1.0, "speech": False, "seconds": 8.0}
        session = export.start(folder, "sound check", 64, 36, 10, "png", 16, 64, 36)
        try:
            for _ in range(80):                                   # eight seconds of grey frames
                session.add(bytes([90, 90, 90, 255]) * (64 * 36))
            lines = [{"path": tone(os.path.join(folder, f"line{i}.wav"), b - a, 1000, 0.05), "start": a, "gain": 1.0, "speech": True, "seconds": b - a} for i, (a, b) in enumerate(spoken)]
            result = session.finish(lines + [music], [], loudness=-14.0, duck=9.0)
        finally:
            export.end(session.id)
        self.assertEqual(result["sound"], 3)
        self.assertIsNone(result["note"])
        loud = result["loudness"]
        self.assertLess(loud["before"], -25)                      # it was a quiet mix
        self.assertAlmostEqual(loud["lufs"], -14.0, delta=0.7)    # and came out as loud as asked
        self.assertLessEqual(loud["peak"], -1.0 + 0.2)
        with wave.open(os.path.join(result["path"], "sound.wav"), "rb") as file:
            raw = file.readframes(file.getnframes())
            sound = numpy.frombuffer(raw, "<i2").reshape(-1, file.getnchannels()).mean(axis=1) / 32768
        under, between = level_of(sound, 220, 1.3, 2.7), level_of(sound, 220, 3.6, 4.8)
        self.assertAlmostEqual(between - under, 9.0, delta=1.0)   # the music is 9 dB lower under the voice
        self.assertAlmostEqual(level_of(sound, 220, 0.1, 0.8), between, delta=0.5)
        # without either, the mix is left as it was made
        session = export.start(folder, "sound plain", 64, 36, 10, "png", 16, 64, 36)
        try:
            for _ in range(80):
                session.add(bytes([90, 90, 90, 255]) * (64 * 36))
            plain = session.finish(lines + [music], [])
        finally:
            export.end(session.id)
        self.assertIsNone(plain["loudness"])
        with wave.open(os.path.join(plain["path"], "sound.wav"), "rb") as file:
            sound = numpy.frombuffer(file.readframes(file.getnframes()), "<i2").reshape(-1, file.getnchannels()).mean(axis=1) / 32768
        self.assertAlmostEqual(level_of(sound, 220, 1.3, 2.7), level_of(sound, 220, 3.6, 4.8), delta=0.3)


if __name__ == "__main__":
    unittest.main()

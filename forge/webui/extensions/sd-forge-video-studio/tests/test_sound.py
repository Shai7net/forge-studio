"""Sound and speech checks that need no models: sentence splitting, extending a video for long speech, the mix.

Run: webui\\video-studio\\env\\Scripts\\python.exe -m unittest discover -s tests   (from the extension folder)
"""

import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "videostudio", "helpers"))

from videostudio import sound  # noqa: E402
import voice_say  # noqa: E402


def make_video(path, seconds, fps=16, size="64x96"):
    subprocess.run([sound.ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size={size}:rate={fps}",
                    "-pix_fmt", "yuv420p", path], check=True)


def make_tone(path, seconds, freq=440):
    subprocess.run([sound.ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}",
                    "-ar", "44100", path], check=True)


class Sentences(unittest.TestCase):
    def test_hebrew_and_english(self):
        self.assertEqual(voice_say.sentences("שלום. מה שלומך? טוב!"), ["שלום.", "מה שלומך?", "טוב!"])
        self.assertEqual(voice_say.sentences("One line\n\nanother line"), ["One line", "another line"])

    def test_long_sentence_cut_at_commas(self):
        long = ", ".join(["מילה מילה מילה מילה"] * 30)
        parts = voice_say.sentences(long)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(p) <= 230 for p in parts))


class Media(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def test_segment_extends_past_the_end(self):
        src, out = os.path.join(self.dir, "v.mp4"), os.path.join(self.dir, "s.mp4")
        make_video(src, 2)
        sound._segment(src, 0.0, 3.5, 16, out)
        self.assertAlmostEqual(sound.duration(out), 3.5, delta=0.1)
        sound._segment(src, 0.5, 1.0, 16, out)
        self.assertAlmostEqual(sound.duration(out), 1.0, delta=0.1)

    def test_long_speech_never_replays_or_reverses_the_picture(self):
        import numpy as np
        src, out = os.path.join(self.dir, 'ordered.mkv'), os.path.join(self.dir, 'held.mp4')
        pictures = b''.join(bytes([i * 3]) * (64 * 64 * 3) for i in range(64))
        subprocess.run([sound.ffmpeg(), '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'rgb24',
                        '-s', '64x64', '-r', '32', '-i', '-', '-c:v', 'ffv1', src], input=pictures, check=True)
        for start in (0, 0.5):
            sound._segment(src, start, 3.5, 32, out)
            raw = subprocess.check_output([sound.ffmpeg(), '-v', 'error', '-i', out, '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'])
            frames = np.frombuffer(raw, dtype=np.uint8).reshape((-1, 64, 64, 3))
            values = frames[:, 32, 32, 0].astype(float)
            self.assertEqual(len(values), 112)
            self.assertTrue(np.all(np.diff(values) >= -3), values.tolist())
            self.assertAlmostEqual(values[0], round(start * 32) * 3, delta=3)
            self.assertTrue(np.all(abs(values[64 - round(start * 32):] - 189) <= 3))

    def test_mix_speech_and_effects(self):
        video, fx, sp, out = (os.path.join(self.dir, n) for n in ("v.mp4", "fx.wav", "sp.wav", "o.mp4"))
        make_video(video, 3)
        make_tone(fx, 3, 220)
        make_tone(sp, 1.5, 880)
        sound.mix(video, out, effects=fx, speech=sp, start=0.5)
        info = subprocess.run([sound.ffmpeg(), "-hide_banner", "-i", out], capture_output=True, text=True).stderr
        self.assertIn("Audio: aac", info)
        self.assertAlmostEqual(sound.duration(out), 3.0, delta=0.15)
        sound.mix(video, out, effects=fx)
        sound.mix(video, out, speech=sp, start=1.0)
        self.assertIn("Audio: aac", subprocess.run([sound.ffmpeg(), "-hide_banner", "-i", out], capture_output=True, text=True).stderr)



class Prompting(unittest.TestCase):
    """Uses the LoRA files in models/VideoStudio/loras when they are there."""

    def test_tags_become_loras_and_leave_the_prompt(self):
        from videostudio import prompting
        files = prompting.lora_files()
        clean, found, unknown = prompting.split_loras("<lora:ral-3dwvz:1>, ral-3dwvz, a cat walks, <lora:x:0.5>")
        self.assertEqual(clean, "ral-3dwvz, a cat walks")
        self.assertEqual(sorted(unknown), ["ral-3dwvz", "x"])
        yarn_high = next((n for n in files if "yarnwoven" in n.lower() and "high" in n.lower()), None)
        if yarn_high:
            stem = os.path.splitext(yarn_high)[0]
            clean, found, unknown = prompting.split_loras(f"<lora:{stem}:0.8> Yarnwoven Style, snails")
            self.assertEqual(clean, "Yarnwoven Style, snails")
            self.assertEqual([(os.path.basename(p), w) for p, w in found], [(yarn_high, 0.8)])
            pair = prompting.with_partners([{"path": found[0][0], "strength": 0.8}])
            self.assertEqual(len(pair), 2)
            self.assertTrue(any("low_noise" in p["path"] for p in pair))


if __name__ == "__main__":
    unittest.main()

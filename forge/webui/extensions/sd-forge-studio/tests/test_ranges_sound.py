"""Real media checks for exact generation ranges and reusable sound ownership; no GPU required."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from forgestudio import generate, library, media, paths, projects, sound, soundbank


class RangesAndSound(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="studio-ranges-")
        self.env = patch.dict(os.environ, {"FORGE_STUDIO_ROOT": self.tmp})
        self.env.start()
        projects.create("range")
        self.wav = os.path.join(self.tmp, "source.wav")
        self.ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=4", self.wav)
        self.video = os.path.join(self.tmp, "source.mp4")
        self.ffmpeg("-f", "lavfi", "-i", "testsrc2=s=128x96:r=30:d=10", "-c:v", "libx264", self.video)

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.tmp)

    def ffmpeg(self, *args):
        subprocess.run([library.ffmpeg(), "-v", "error", "-y", *args], check=True, capture_output=True)

    def test_sound_saved_once_reused_and_removed_without_breaking_project(self):
        web = projects.add_media("range", self.wav)
        saved = soundbank.save("range", web, "A tone", "music", "a soft tone")
        self.assertEqual(soundbank.save("range", web)["id"], saved["id"])
        self.assertEqual(len(soundbank.listing()), 1)
        used = soundbank.use("range", saved["id"])
        target = projects.media_path("range", used["web"])
        self.assertNotEqual(target, self.wav)
        soundbank.remove(saved["id"])
        self.assertEqual(soundbank.listing(), [])
        self.assertTrue(os.path.isfile(target))
        self.assertAlmostEqual(media.probe(target)["duration"], 4, places=2)
        with self.assertRaises(ValueError):
            soundbank.remove("../../source.wav")

    def test_audio_duration_is_exact_including_short_music(self):
        for seconds in (2, 0.5, 1.37, 1 / 30):
            out = os.path.join(self.tmp, f"fit-{seconds}.wav")
            shutil.copy2(self.wav, out)
            sound._fit_audio(out, seconds)
            self.assertAlmostEqual(media.probe(out)["duration"], seconds, places=3)

    def test_effects_on_empty_time_and_trimmed_video(self):
        calls = []
        def make(task, source, words, negative, seed, out, **kwargs):
            calls.append((media.probe(source), words, kwargs))
            shutil.copy2(self.wav, out)
        vs = types.SimpleNamespace(status=lambda: {"effects": True}, make_effects=make)
        with patch.object(sound, "_vs_sound", return_value=vs), patch.object(sound, "_on_card", side_effect=lambda f: f()), patch.object(sound, "_english", side_effect=lambda task, words, field: "Three knocks on wood"):
            result = sound._effects({}, "range", None, "tres golpes", 1, 2)
            self.assertTrue(calls[-1][2]["text_only"])
            self.assertAlmostEqual(calls[-1][0]["duration"], 2, places=2)
            self.assertAlmostEqual(media.probe(result["path"])["duration"], 2, places=3)
            web = projects.add_media("range", self.video)
            sound._effects({}, "range", web, "knocks", 1, 2, 3, 2)
            self.assertFalse(calls[-1][2]["text_only"])
            self.assertAlmostEqual(calls[-1][0]["duration"], 2, places=2)
            tiny = sound._effects({}, "range", None, "click", 1, 1 / 30)
            self.assertAlmostEqual(calls[-1][0]["duration"], 1, places=2)
            self.assertAlmostEqual(media.probe(tiny['path'])['duration'], 1 / 30, places=3)

    def test_any_language_uses_audio_specific_writer_target(self):
        writer = types.ModuleType("test_audio_writer")
        writer.EXT_DIR = "extensions/hebrew-translator"
        calls = []
        writer.write = lambda text, field: (calls.append((text, field)) or {"prompt": "Rain on a metal roof."})
        lazy = types.ModuleType("test_lazy_module")
        def fail_on_getattr(key):
            raise RuntimeError("lazy modules must not be inspected through getattr")
        lazy.__getattr__ = fail_on_getattr
        with patch.dict(sys.modules, {"test_lazy_module": lazy, "test_audio_writer": writer}):
            state = {}
            self.assertEqual(sound._english(state, "дождь по крыше", "fs_effects"), "Rain on a metal roof.")
            self.assertEqual(calls, [("дождь по крыше", "fs_effects")])
            self.assertEqual(state["source_prompt"], "дождь по крыше")
            self.assertEqual(state["prompt"], "Rain on a metal roof.")

    def test_loaded_forge_script_uses_the_package_bridge(self):
        bridge = types.ModuleType('prompt_writer')
        bridge.audio_engine_name = lambda: 'Gemma'
        bridge.audio_write = lambda text, field: {'prompt': 'Soft piano and warm strings.'}
        with patch.dict(sys.modules, {'prompt_writer': bridge}):
            state = {}
            self.assertEqual(sound._english(state, 'piano suave', 'fs_music'), 'Soft piano and warm strings.')

    def test_busy_writer_waits_for_its_turn(self):
        Busy = type('Busy', (Exception,), {})
        bridge = types.ModuleType('prompt_writer')
        bridge.audio_engine_name = lambda: 'Gemma'
        with patch.dict(sys.modules, {'prompt_writer': bridge}), patch.object(sound.time, 'sleep') as sleep:
            bridge.audio_write = unittest.mock.Mock(side_effect=[Busy(), {'prompt': 'Soft piano.'}])
            self.assertEqual(sound._english({}, 'piano suave', 'fs_music'), 'Soft piano.')
            self.assertEqual(bridge.audio_write.call_count, 2)
            sleep.assert_called_once_with(1)

    def test_long_video_is_chained_and_short_video_fits_requested_frames(self):
        calls = []
        def make(name, record):
            calls.append(dict(record))
            return self.video
        with patch.object(generate, "_make_segment", side_effect=make):
            for seconds, direction in ((2, "after"), (1.4, "before"), (10, "after")):
                record = {"id": str(seconds), "kind": "text", "engine": "wan", "seconds": seconds,
                          "fps": 30, "prompt": "Continue the shot", "direction": direction}
                out = generate._make("range", record)
                self.assertAlmostEqual(media.probe(out)["duration"], seconds, delta=0.05)
                if seconds == 10:
                    self.assertEqual(calls[-1]["kind"], "continue")
                    self.assertEqual(calls[-1]["seconds"], 2.5)
                    self.assertTrue(os.path.isfile(calls[-1]["start"]["path"]))
                with open(out[:-4] + ".json", encoding="utf-8") as f:
                    self.assertEqual(json.load(f)["prompt"], "Continue the shot")

    def test_invalid_ranges_are_rejected_before_queueing(self):
        for seconds in (-1, 0, float("nan"), float("inf"), 241):
            with self.assertRaises(ValueError):
                sound.music("range", "piano", seconds)
            with self.assertRaises(ValueError):
                generate.submit("range", "text", "a bird", seconds)

    def test_pre_roll_ends_on_the_original_anchor_frame(self):
        from PIL import Image
        video = os.path.join(self.tmp, 'two-colours.mp4')
        self.ffmpeg('-f', 'lavfi', '-i', 'color=red:s=64x64:r=30:d=1', '-f', 'lavfi', '-i',
                    'color=blue:s=64x64:r=30:d=1', '-filter_complex', '[0:v][1:v]concat=n=2:v=1:a=0[v]',
                    '-map', '[v]', '-c:v', 'libx264', video)
        with patch.object(generate, '_make_segment', return_value=video):
            out = generate._make('range', {'id': 'before', 'kind': 'text', 'engine': 'wan', 'seconds': 2,
                                          'fps': 30, 'prompt': 'Continue the shot', 'direction': 'before'})
        first, last = os.path.join(self.tmp, 'first.png'), os.path.join(self.tmp, 'last.png')
        media.grab(out, 0, first)
        media.grab(out, 1.9, last)
        with Image.open(first) as im:
            r, g, b = im.convert('RGB').getpixel((10, 10))
            self.assertGreater(b, r + 100)
        with Image.open(last) as im:
            r, g, b = im.convert('RGB').getpixel((10, 10))
            self.assertGreater(r, b + 100)


if __name__ == "__main__":
    unittest.main()

# Unit tests for camera_directing.py (no webui needed). From the /scripts directory run:
#   ..\..\..\venv\Scripts\python.exe -m unittest deforum_helpers.camera_directing_test -v

import json
import os
import tempfile
import unittest

import numpy as np

from . import audio_keyframes as ak
from . import camera_directing as cd

SR = ak.DECODE_SAMPLE_RATE


def analysis_from_bands(fps, full, bass=None, mids=None, highs=None):
    bands = {ak.BAND_FULL: full, ak.BAND_BASS: bass, ak.BAND_MIDS: mids, ak.BAND_HIGHS: highs}
    loudness = {band: np.asarray(full if curve is None else curve, dtype=np.float64) for band, curve in bands.items()}
    return ak.Analysis("", len(full) / fps, SR, fps, loudness)


def changing_song(fps=10):
    """30 seconds in three different sections, so songs split into parts."""
    rng = np.random.default_rng(5)
    full = np.concatenate([0.1 + 0.02 * rng.random(100), 0.8 + 0.1 * rng.random(100), 0.4 + 0.05 * rng.random(100)])
    bass = np.concatenate([0.05 + 0.02 * rng.random(100), 0.9 + 0.05 * rng.random(100), 0.1 + 0.02 * rng.random(100)])
    return analysis_from_bands(fps, full, bass, full[::-1].copy(), 0.3 + 0.1 * rng.random(300))


def camera_rows(values):
    return [ak.TargetSettings(t.key, enabled=values.get(f"{t.key}_enabled", False),
                              band=values.get(f"{t.key}_band", ak.BAND_FULL), function=values.get(f"{t.key}_function"),
                              quiet=values.get(f"{t.key}_quiet"), loud=values.get(f"{t.key}_loud"),
                              smooth=values.get(f"{t.key}_smooth", 0), switch=values.get(f"{t.key}_switch", ""))
            for t in ak.TARGETS if t.signed]


class DirectionToolTest(unittest.TestCase):
    def test_flip_and_switch_keep_the_speeds(self):
        values = np.array([0.0, 1.0, 2.0, 3.0])
        np.testing.assert_allclose(cd.flip_values(values, 0.0), [0, -1, -2, -3])
        np.testing.assert_allclose(cd.switch_values(values, 0.0, 2), [0, 1, -2, -3])
        np.testing.assert_allclose(cd.switch_values([1.02] * 4, 1.0, 1), [1.02, 0.98, 1.02, 0.98])  # zoom turns around 1
        np.testing.assert_allclose(cd.switch_values([-1.0] * 3, 0.0, 1), [-1, 1, -1])  # keeps the starting direction
        np.testing.assert_allclose(cd.switch_values([1.0, -1.0, 1.0], 0.0, 0), [1, 1, 1])  # 0 = one direction
        np.testing.assert_allclose(cd.switch_values(cd.flip_values([1.0, -1.0], 0.0), 0.0, 0), [-1, -1])
        np.testing.assert_allclose(cd.switch_values([0.0, 0.0], 0.0, 1), [0, 0])  # nothing moves, nothing to switch

    def test_direction_changes(self):
        self.assertEqual(cd.direction_changes([1, 1, -1, -1, 0, 1], 0.0), 2)  # the 0 keeps the direction before it
        self.assertEqual(cd.direction_changes([1.02, 0.98, 1.02], 1.0), 2)
        self.assertEqual(cd.direction_changes([0, 0], 0.0), 0)

    def test_last_key_frame(self):
        self.assertEqual(cd.last_key_frame("0: (0), 120: (1.5),\n 45:(2)"), 120)
        self.assertEqual(cd.last_key_frame(""), 0)


class ModeTest(unittest.TestCase):
    def test_builtin_modes_direct_any_song_in_2d_and_3d(self):
        analysis = changing_song()
        for name, mode in cd.BUILTIN_MODES.items():
            cd.check_mode(mode, name)
            for animation_mode in ("3D", "2D"):
                with self.subTest(mode=name, animation_mode=animation_mode):
                    values, _ = cd.mode_settings(mode, analysis, animation_mode)
                    results = ak.build_schedules(analysis, camera_rows(values), values["parts"], values["crossfade"])[0]
                    self.assertTrue(results)
                    for key, result in results.items():
                        self.assertIn(ak.TARGETS_BY_KEY[key].mode, (None, animation_mode))
                        self.assertTrue(np.isfinite(result.values).all())
                    self.assertTrue(all(len(row) == len(ak.PART_HEADERS) for row in values["parts"]))

    def test_action_switches_on_beats_and_alternates_the_parts(self):
        values, _ = cd.mode_settings(cd.BUILTIN_MODES["Action! · אקשן"], changing_song(), "3D")
        self.assertEqual(values["translation_x_switch"], "beats 0.5")
        self.assertFalse(values["rotation_3d_x_enabled"])
        self.assertTrue(values["rotation_3d_z_enabled"])
        x_cells = [row[ak.PART_HEADERS.index("X")] for row in values["parts"]]
        self.assertGreaterEqual(len(x_cells), 2)
        self.assertEqual(x_cells[:2], ["+", "-"])
        self.assertEqual(values["crossfade"], 1)  # 0.1 s at 10 fps

    def test_2d_twins(self):
        analysis = changing_song()
        values, notes = cd.mode_settings(cd.BUILTIN_MODES["Gentle · תנועות קלות"], analysis, "2D")
        self.assertTrue(values["zoom_enabled"])
        self.assertEqual((values["zoom_quiet"], values["zoom_loud"]), ("1.003", "1.01"))
        self.assertFalse(values["translation_z_enabled"] or values["rotation_3d_y_enabled"])
        self.assertTrue(any("Rotation 3D Y" in note for note in notes))
        self.assertEqual(values["parts"][0][ak.PART_HEADERS.index("Zoom")], "+")  # the pattern's Z column drives Zoom
        values, _ = cd.mode_settings(cd.BUILTIN_MODES["Dreamy · חלומי"], analysis, "2D")
        self.assertEqual(values["angle_function"], "0.2*sin(sec*0.2 + 1)")
        back, _ = cd.mode_rows({"rows": {"zoom": {"function": "1 + x^4", "quiet": "", "loud": ""}}}, "3D")
        self.assertEqual(back["translation_z"]["function"], "((1 + x^4) - 1)*100")


class LearnTest(unittest.TestCase):
    def test_learns_every_n_frames_with_the_band_and_function(self):
        rng = np.random.default_rng(11)
        frames = 400
        bands = [np.convolve(rng.random(frames), np.ones(5) / 5, mode="same") for _ in range(4)]
        analysis = analysis_from_bands(10, *bands)
        bass = analysis.curve(ak.BAND_BASS)
        speeds = 0.4 + 2.6 * bass ** 2
        signs = np.where((np.arange(frames) // 10) % 2 == 0, 1.0, -1.0)
        mode, lines = cd.learn_mode(analysis, {"translation_x": signs * speeds, "translation_z": np.full(frames, 0.8),
                                               "rotation_3d_x": np.zeros(frames)})
        row = mode["rows"]["translation_x"]
        self.assertEqual((row["switch"], row["band"], row["function"], row["smooth"]), ("10", ak.BAND_BASS, "x^2", 0))
        self.assertAlmostEqual(float(row["loud"]), float(np.percentile(speeds, 95)), delta=0.01)
        z = mode["rows"]["translation_z"]
        self.assertEqual((z["function"], z["quiet"], z["loud"], z["switch"]), ("1", "0.8", "0.8", ""))
        self.assertNotIn("rotation_3d_x", mode["rows"])
        self.assertEqual((mode["parts"]["min_seconds"], len(lines)), (0.0, 2))

    def test_learns_switching_on_beats_and_repeats_it(self):
        frames, fps = 400, 10
        x = np.full(frames, 0.1)
        for beat in (30, 62, 95, 130, 170, 205, 250, 290, 330, 370):
            x[beat:beat + 3] = 1.0
        analysis = analysis_from_bands(fps, x)
        original = 0.5 * ak.switch_signs(("beats", 1.0), frames, fps, analysis.curve(ak.BAND_FULL))
        mode, _ = cd.learn_mode(analysis, {"rotation_3d_y": original})
        self.assertTrue(mode["rows"]["rotation_3d_y"]["switch"].startswith("beats"), mode["rows"])
        values, _ = cd.mode_settings(mode, analysis, "3D")
        repeated = ak.build_schedules(analysis, camera_rows(values), values["parts"], values["crossfade"])[0]
        np.testing.assert_array_equal(np.sign(repeated["rotation_3d_y"].values), np.sign(original))

    def test_learns_direction_changes_with_the_song_parts(self):
        frames = 300
        analysis = analysis_from_bands(10, np.full(frames, 0.5))  # a song without beats
        values = np.concatenate([np.full(100, 0.3), np.full(120, -0.3), np.full(80, 0.3)])
        mode, _ = cd.learn_mode(analysis, {"rotation_3d_y": values})
        row = mode["rows"]["rotation_3d_y"]
        self.assertEqual((row["switch"], row["function"], row["quiet"], row["loud"]), ("", "1", "0.3", "0.3"))
        self.assertEqual(mode["parts"], {"min_seconds": 10.0, "pattern": [{"Rot Y": "+"}, {"Rot Y": "-"}]})
        self.assertEqual(mode["crossfade_seconds"], 0.0)
        with self.assertRaises(ak.AudioKeyframeError):
            cd.learn_mode(analysis, {"rotation_3d_y": np.zeros(frames)})


class SavedModeTest(unittest.TestCase):
    def test_save_load_replace_and_delete(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "camera_modes.json")
            settings = {"translation_x_enabled": True, "translation_x_band": "Bass", "translation_x_function": "x^2",
                        "translation_x_quiet": "0.4", "translation_x_loud": "3", "translation_x_smooth": 2,
                        "translation_x_switch": "beats 1", "rotation_3d_y_enabled": False,
                        "parts": [["0:00", "+", "", "-"], ["0:10", "-", "", "+"]], "auto_min": 6, "crossfade": 15}
            mode = cd.mode_from_settings(settings, fps=30)
            self.assertEqual(list(mode["rows"]), ["translation_x"])
            self.assertEqual(mode["parts"], {"min_seconds": 6.0, "pattern": [{"X": "-"}, {"X": "+"}]})
            self.assertEqual(mode["crossfade_seconds"], 0.5)
            self.assertFalse(cd.add_user_mode(path, "Space orbit", mode))
            self.assertTrue(cd.add_user_mode(path, "Space orbit", mode))  # saving again replaces it
            self.assertEqual(list(cd.all_modes(path)), list(cd.BUILTIN_MODES) + ["Space orbit"])
            values, _ = cd.mode_settings(cd.load_user_modes(path)["Space orbit"], changing_song(), "3D")
            self.assertEqual(values["translation_x_switch"], "beats 1")
            for bad in (lambda: cd.add_user_mode(path, "Action! · אקשן", mode),
                        lambda: cd.add_user_mode(path, " ", mode),
                        lambda: cd.delete_user_mode(path, "Gentle · תנועות קלות"),
                        lambda: cd.mode_from_settings({"translation_x_enabled": False, "parts": []}, fps=30)):
                with self.assertRaises(ak.AudioKeyframeError):
                    bad()
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            data["modes"]["Broken"] = {"rows": {"strength_schedule": {}}}
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f)
            self.assertNotIn("Broken", cd.load_user_modes(path))  # a broken saved mode is skipped, not fatal
            cd.delete_user_mode(path, "Space orbit")
            self.assertEqual(list(cd.all_modes(path)), list(cd.BUILTIN_MODES))


if __name__ == "__main__":
    unittest.main()

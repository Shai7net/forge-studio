# Unit tests for map_key.py (no webui needed). From the /scripts directory run:
#   ..\..\..\venv\Scripts\python.exe -m unittest deforum_helpers.map_key_test -v
# Schedules are parsed with Deforum's own FrameInterpolater, imported with the two webui modules it names stubbed.

import json
import os
import sys
import tempfile
import types
import unittest
import warnings

import numpy as np

from . import audio_keyframes as ak
from . import map_key as mk


def deforum_expand():
    try:
        import modules.scripts  # noqa: F401  (inside the webui the real ones are used)
    except ImportError:
        stub = types.ModuleType("modules")
        stub.scripts = types.ModuleType("modules.scripts")
        stub.shared = types.ModuleType("modules.shared")
        sys.modules.update({"modules": stub, "modules.scripts": stub.scripts, "modules.shared": stub.shared})
    from .animation_key_frames import FrameInterpolater

    def expand(text, frames):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)  # pandas' dtype notice inside Deforum's parser
            return FrameInterpolater(int(frames)).parse_inbetweens(text, "map_key_test").to_numpy(dtype=np.float64)
    return expand


EXPAND = deforum_expand()


def analysis_of(full, fps=30):
    loudness = {band: np.asarray(full, dtype=np.float64) for band in ak.BANDS}
    return ak.Analysis("D:/music/song.mp3", len(full) / fps, ak.DECODE_SAMPLE_RATE, fps, loudness)


class CompactScheduleTest(unittest.TestCase):
    def test_a_drawn_line_is_two_keys_and_deforum_reads_it_back(self):
        ramp = 0.5 + 0.013 * np.arange(100)
        self.assertEqual(mk.compact_schedule(ramp, 3), "0: (0.5), 99: (1.787)")
        self.assertEqual(mk.compact_schedule(np.full(40, 1.75), 3), "0: (1.75), 39: (1.75)")
        np.testing.assert_allclose(EXPAND(mk.compact_schedule(ramp, 3), 100), np.round(ramp, 3), atol=5e-4 + 1e-9)

    def test_any_curve_comes_back_within_one_step(self):
        rng = np.random.default_rng(3)
        for decimals in (2, 3, 4):
            values = np.cumsum(rng.normal(0, 0.05, 500)) + np.where(np.arange(500) % 97 == 0, 0.4, 0)
            text = mk.compact_schedule(values, decimals)
            back = EXPAND(text, 500)
            np.testing.assert_allclose(back, values, atol=10.0 ** -decimals + 1e-9)
            self.assertLess(text.count(":"), 500)
            self.assertNotIn("e-", text)
            self.assertNotIn("-0)", text)

    def test_a_mouse_drawn_line_with_an_odd_slope_is_still_its_ends(self):
        frames = np.arange(320)
        drawn = np.where((frames < 44) | (frames > 255), 0.0, 0.0033 + (frames - 44) * 0.0038517)
        keys = [frame for frame, _ in mk.compact_keys(drawn, 3)]
        self.assertLessEqual(len(keys), 6, keys)
        self.assertEqual((keys[0], keys[-1]), (0, 319))
        np.testing.assert_allclose(EXPAND(mk.compact_schedule(drawn, 3), 320), drawn, atol=1e-3 + 1e-9)

    def test_frames_at_the_ends_are_always_keys(self):
        keys = mk.compact_keys([0.0, 0.0, 1.0, 1.0, 1.0], 3)
        self.assertEqual([frame for frame, _ in keys], [0, 1, 2, 4])


class PromptsTest(unittest.TestCase):
    def test_numbers_and_formula_keys_are_placed_and_kept(self):
        text = json.dumps({"0": "forest", "30": "city --neg rain", "max_f-1": "the end", "nonsense": "?", "max_f/2": "mid"})
        entries, unplaced, error = mk.prompt_entries(text, 100)
        self.assertIsNone(error)
        self.assertEqual([(e["frame"], e["key"], e["pinned"]) for e in entries],
                         [(0, "0", False), (30, "30", False), (49, "max_f/2", True), (98, "max_f-1", True)])
        self.assertEqual(unplaced, [{"key": "nonsense", "text": "?"}])
        back = json.loads(mk.prompts_json(entries, unplaced))
        self.assertEqual(list(back), ["0", "30", "max_f/2", "max_f-1", "nonsense"])
        self.assertEqual(back["30"], "city --neg rain")

    def test_moved_prompts_get_frame_numbers_and_two_on_a_frame_are_refused(self):
        text = mk.prompts_json([{"frame": 40, "text": "b"}, {"frame": 0, "text": "a", "key": "0"},
                                {"frame": 70, "text": "end", "key": "max_f-1"}])
        self.assertEqual(list(json.loads(text).items()), [("0", "a"), ("40", "b"), ("max_f-1", "end")])
        with self.assertRaises(mk.MapKeyError):
            mk.prompts_json([{"frame": 5, "text": "a"}, {"frame": 5, "text": "b"}])

    def test_broken_prompts_are_never_written(self):
        entries, unplaced, error = mk.prompt_entries('{"0": "a",', 50)
        self.assertEqual((entries, unplaced), ([], []))
        self.assertIn("not valid JSON", error)
        data, notes = mk.load_state({}, '{"0": "a",', 50, 30, 1, "3D", EXPAND)
        self.assertFalse(data["prompts_ok"])
        self.assertTrue(any("not valid JSON" in note for note in notes))

    def test_a_prompt_past_the_end_is_kept_aside(self):
        entries, unplaced, _ = mk.prompt_entries(json.dumps({"0": "a", "200": "late"}), 100)
        self.assertEqual([e["frame"] for e in entries], [0])
        self.assertEqual(unplaced, [{"key": "200", "text": "late"}])


class LoadAndApplyTest(unittest.TestCase):
    def test_load_reads_every_schedule_with_deforums_parser(self):
        schedules = {"strength_schedule": "0: (0.65)", "translation_z": "0: (1.5*sin(t/5))", "zoom": "0:(1.0)",
                     "translation_x": "0: (0), 50: (4)", "angle": "not a schedule ("}
        data, notes = mk.load_state(schedules, '{"0": "forest"}', 120, 30, 2, "3D", EXPAND)
        self.assertEqual((data["frames"], data["cadence"], data["mode"]), (120, 2, "3D"))
        self.assertEqual(sorted(data["lanes"]), sorted(mk.LANE_KEYS))
        z = data["lanes"]["translation_z"]["knots"]
        frames, values = zip(*z)
        np.testing.assert_allclose(np.interp(np.arange(120), frames, values), 1.5 * np.sin(np.arange(120) / 5), atol=1e-4)
        self.assertEqual(data["lanes"]["translation_x"]["knots"], [[0, 0.0], [50, 4.0], [119, 4.0]])
        self.assertEqual(data["lanes"]["cfg_scale_schedule"]["knots"], [[0, 7.0], [119, 7.0]], "an empty field is neutral")
        self.assertEqual(data["lanes"]["angle"]["knots"], [[0, 0.0], [119, 0.0]])
        self.assertTrue(any("Angle could not be read" in note for note in notes))
        self.assertEqual(data["prompts"], [{"frame": 0, "text": "forest", "key": "0", "pinned": False}])

    def test_load_passes_the_fov_for_the_level_keeping_roll(self):
        data, notes = mk.load_state({}, "", 100, 30, 1, "3D", EXPAND, fov_text="0: (70), 50: (90)")
        frames_, values = zip(*data["warp"]["fov"])
        np.testing.assert_allclose(np.interp(np.arange(100), frames_, values)[[0, 25, 50, 99]], [70, 80, 90, 90])
        self.assertEqual(notes, [])
        data, notes = mk.load_state({}, "", 100, 30, 1, "3D", EXPAND, fov_text="0: (not a number")
        self.assertEqual(data["warp"], {"fov": [[0, 70.0], [99, 70.0]]})
        self.assertTrue(any("FOV schedule could not be read" in note for note in notes))

    def test_apply_writes_only_the_lanes_sent_and_load_reads_them_back(self):
        frames = 90
        z = np.where(np.arange(frames) < 45, 1.25, -1.25)
        strength = np.clip(0.6 + 0.2 * np.sin(np.arange(frames) / 9), 0, 1)
        payload = {"version": 1, "frames": frames, "lanes": {"translation_z": z.tolist(), "strength_schedule": strength.tolist()},
                   "prompts": [{"frame": 0, "text": "a", "key": "0"}, {"frame": 60, "text": "b", "key": None}],
                   "unplaced": [], "project": {"version": 1}}
        updates, prompts_text, project, notes = mk.apply_payload(json.dumps(payload), frames_now=frames)
        self.assertEqual(list(updates), ["translation_z", "strength_schedule"])
        self.assertEqual(updates["translation_z"], "0: (1.25), 44: (1.25), 45: (-1.25), 89: (-1.25)")
        self.assertEqual(json.loads(prompts_text), {"0": "a", "60": "b"})
        self.assertEqual((project, notes), ({"version": 1}, []))
        data, _ = mk.load_state(updates, prompts_text, frames, 30, 1, "3D", EXPAND)
        for key, sent in (("translation_z", z), ("strength_schedule", strength)):
            frames_, values = zip(*data["lanes"][key]["knots"])
            np.testing.assert_allclose(np.interp(np.arange(frames), frames_, values), sent, atol=1e-3 + 1e-9)
        self.assertEqual([p["frame"] for p in data["prompts"]], [0, 60])

    def test_apply_refuses_what_deforum_could_not_use(self):
        good = {"version": 1, "frames": 3, "lanes": {"translation_x": [0, 1, 2]}, "prompts": None}
        self.assertEqual(mk.apply_payload(good)[0], {"translation_x": "0: (0), 2: (2)"})
        self.assertIsNone(mk.apply_payload(good)[1], "prompts that were not edited are left alone")
        for bad, message in (({**good, "lanes": {"translation_x": [0, 1]}}, "2 values for 3 frames"),
                             ({**good, "lanes": {"strength_schedule": [0.5, 1.2, 0.5]}}, "between 0 and 1"),
                             ({**good, "lanes": {"translation_x": [0, float("nan"), 1]}}, "not a number"),
                             ({**good, "lanes": {"seed_schedule": [1, 2, 3]}}, "Unknown schedule"),
                             ({**good, "version": 7}, "not from this version"),
                             ({"version": 1, "error": "No song"}, "No song")):
            with self.assertRaises(mk.MapKeyError) as caught:
                mk.apply_payload(json.dumps(bad))
            self.assertIn(message, str(caught.exception))
        with self.assertRaises(mk.MapKeyError):
            mk.apply_payload("")
        _, _, _, notes = mk.apply_payload(good, frames_now=5)
        self.assertIn("Max frames is now 5", notes[0])


class SongAndProjectTest(unittest.TestCase):
    def test_song_lane_has_every_band_the_beats_and_the_parts(self):
        full = np.tile(np.r_[1.0, np.full(14, 0.1)], 20)  # a hit every half second
        song = mk.song_data(analysis_of(full), part_frames=[0, 150, 999])
        self.assertEqual((song["frames"], song["step"], song["fps"]), (300, 1, 30.0))
        self.assertEqual(sorted(song["bands"]), sorted(ak.BANDS))
        self.assertEqual((max(song["bands"]["Full"]), min(song["bands"]["Full"])), (999, 100))
        self.assertEqual(song["beats"], list(range(15, 300, 15)))
        self.assertEqual(song["parts"], [0, 150])
        long_song = mk.song_data(analysis_of(np.linspace(0, 1, 50_001)), max_points=20_000)
        self.assertEqual(long_song["step"], 3)
        self.assertEqual(len(long_song["bands"]["Bass"]), 16_667)
        data, notes = mk.load_state({}, "", 250, 30, 1, "2D", EXPAND, song=song)
        self.assertEqual(data["mode"], "2D")
        self.assertTrue(any("the song is 300 frames" in note for note in notes))

    def test_projects_save_load_and_list(self):
        project = {"version": 1, "frames": 10, "lanes": {"translation_z": {"mag": [[0, 1], [9, 1]], "sign": [[0, 1]]}},
                   "prompts": [{"frame": 0, "text": "שלום"}]}
        with tempfile.TemporaryDirectory() as folder:
            path = mk.project_file(folder, 'My "space" flight?')
            self.assertEqual(os.path.basename(path), "My _space_ flight_.json")
            mk.save_project(path, project)
            self.assertEqual(mk.load_project(path), project)
            self.assertEqual(mk.list_projects(folder), ["My _space_ flight_"])
            with open(os.path.join(folder, "other.json"), "w", encoding="utf-8") as f:
                f.write('{"not": "a project"}')
            with self.assertRaises(mk.MapKeyError):
                mk.load_project(os.path.join(folder, "other.json"))
        with self.assertRaises(mk.MapKeyError):
            mk.project_file("x", "  ..  ")
        with self.assertRaises(mk.MapKeyError):
            mk.save_project("unused.json", {"version": 1})


if __name__ == "__main__":
    unittest.main()

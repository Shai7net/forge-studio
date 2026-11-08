# Unit tests for live_control.py (no webui needed). From the /scripts directory run:
#   ..\..\..\venv\Scripts\python.exe -m unittest deforum_helpers.live_control_test -v
# The state file is moved into a temporary folder first, so a test never touches the real live_control.json.

import json
import os
import tempfile
import types
import unittest

from . import live_control as lc


class ResolveTest(unittest.TestCase):
    """What a frame should do, given the project's mode, the bar and DeforumationQT."""

    OFF = {"enabled": False, "mode": "Deforum", "video": 1.0}

    def resolve(self, base, state=None, mediator_mode="", mediator_video="", last="2D"):
        return lc.resolve(base, self.OFF if state is None else state, mediator_mode, mediator_video, last)

    def test_nothing_overrides_anything(self):
        self.assertEqual(self.resolve("3D"), ("3D", 0.0, False))
        self.assertEqual(self.resolve("2D"), ("2D", 0.0, False))
        self.assertEqual(self.resolve("Video Input"), ("2D", 1.0, False))

    def test_bar_off_ignores_its_own_mode_and_slider(self):
        state = {"enabled": False, "mode": "3D", "video": 0.0}
        self.assertEqual(self.resolve("Video Input", state), ("2D", 1.0, False))

    def test_deforum_mode_leaves_the_project_alone(self):
        state = {"enabled": True, "mode": "Deforum", "video": 0.0}
        self.assertEqual(self.resolve("Video Input", state), ("2D", 1.0, False))
        self.assertEqual(self.resolve("3D", state), ("3D", 0.0, False))

    def test_leaving_the_video_step_by_step(self):
        # picking 3D while the slider is still at 100% must not change the picture: the video is still the init
        state = {"enabled": True, "mode": "3D", "video": 1.0}
        self.assertEqual(self.resolve("Video Input", state), ("3D", 1.0, True))
        # and then the slider hands the picture over to the animation
        state["video"] = 0.4
        self.assertEqual(self.resolve("Video Input", state), ("3D", 0.4, True))
        state["video"] = 0.0
        self.assertEqual(self.resolve("Video Input", state), ("3D", 0.0, True))

    def test_the_video_can_be_brought_into_a_3d_render(self):
        state = {"enabled": True, "mode": "2D", "video": 0.6}
        self.assertEqual(self.resolve("3D", state), ("2D", 0.6, True))

    def test_video_input_mode_keeps_the_motion_it_had(self):
        state = {"enabled": True, "mode": "Video Input", "video": 1.0}
        self.assertEqual(self.resolve("3D", state, last="3D"), ("3D", 1.0, True))

    def test_deforumation_wins_over_the_bar(self):
        state = {"enabled": True, "mode": "2D", "video": 0.5}
        self.assertEqual(self.resolve("Video Input", state, mediator_mode="3D"), ("3D", 0.5, True))
        self.assertEqual(self.resolve("Video Input", state, mediator_mode="3D", mediator_video="0.25"), ("3D", 0.25, True))

    def test_deforumation_alone(self):
        self.assertEqual(self.resolve("3D", mediator_mode="2D"), ("2D", 0.0, True))
        self.assertEqual(self.resolve("3D", mediator_video="0.3"), ("3D", 0.3, True))
        self.assertEqual(self.resolve("Video Input", mediator_video="0.3"), ("2D", 0.3, True))

    def test_empty_and_unknown_values_mean_no_override(self):
        for value in ("", "   ", "Deforum", "nonsense", None):
            self.assertEqual(self.resolve("3D", mediator_mode=value), ("3D", 0.0, False))
        for value in ("", "none", "abc", None):
            self.assertEqual(self.resolve("3D", mediator_video=value), ("3D", 0.0, False))

    def test_the_video_share_is_clamped(self):
        self.assertEqual(self.resolve("3D", mediator_video="5")[1], 1.0)
        self.assertEqual(self.resolve("3D", mediator_video="-2")[1], 0.0)

    def test_an_unknown_project_mode_is_left_alone(self):
        self.assertEqual(self.resolve("Interpolation", last="3D"), ("3D", 0.0, False))


class StateFileTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.saved_file, lc.STATE_FILE = lc.STATE_FILE, os.path.join(self.folder.name, "live_control.json")
        self.saved_last, lc._last_good = lc._last_good, dict(lc.DEFAULT_STATE)

    def tearDown(self):
        lc.STATE_FILE, lc._last_good = self.saved_file, self.saved_last
        self.folder.cleanup()

    def test_defaults_without_a_file(self):
        self.assertEqual(lc.read_state(), lc.DEFAULT_STATE)
        self.assertFalse(lc.is_enabled())
        self.assertFalse(lc.keeps_max_frames())

    def test_the_bar_writes_and_reads_back(self):
        lc.set_enabled(True)
        lc.set_mode("3D")
        lc.set_video_percent(40)
        self.assertEqual(lc.read_state(), {"enabled": True, "mode": "3D", "video": 0.4, "fade": None, "plan": []})
        self.assertEqual(lc.current_video_percent(), 40)
        self.assertTrue(lc.keeps_max_frames())
        with open(lc.STATE_FILE, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["mode"], "3D")

    def test_a_mode_that_does_not_exist_falls_back(self):
        lc.set_mode("sideways")
        self.assertEqual(lc.current_mode(), "Deforum")

    def test_a_half_written_file_keeps_the_last_good_state(self):
        lc.set_enabled(True)
        lc.set_mode("2D")
        with open(lc.STATE_FILE, "w", encoding="utf-8") as f:
            f.write('{"enabled": tr')
        self.assertEqual(lc.read_state(), {"enabled": True, "mode": "2D", "video": 1.0, "fade": None, "plan": []})


class RenderStateTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.saved_file, lc.STATE_FILE = lc.STATE_FILE, os.path.join(self.folder.name, "live_control.json")
        self.saved_status, lc.STATUS_FILE = lc.STATUS_FILE, os.path.join(self.folder.name, "live_control_status.json")
        self.saved_last, lc._last_good = lc._last_good, dict(lc.DEFAULT_STATE)
        self.args = types.SimpleNamespace(outdir=self.folder.name, motion_preview_mode=False)
        self.anim_args = types.SimpleNamespace(video_init_path="", use_depth_warping=True, extract_nth_frame=1,
                                               overwrite_extracted_frames=False, extract_from_frame=0, extract_to_frame=-1)

    def tearDown(self):
        lc.STATE_FILE, lc.STATUS_FILE, lc._last_good = self.saved_file, self.saved_status, self.saved_last
        self.folder.cleanup()

    def test_it_starts_on_the_project_mode(self):
        for base, motion, mix in (("3D", "3D", 0.0), ("2D", "2D", 0.0), ("Video Input", "2D", 1.0)):
            live = lc.RenderState(base, mediator_on=False)
            self.assertEqual((live.motion_mode, live.video_mix), (motion, mix))
            self.assertEqual(live.video_only, mix >= 1.0)

    def test_without_video_frames_the_render_goes_on_without_the_video(self):
        lc.set_enabled(True)
        lc.set_mode("3D")
        lc.set_video_percent(100)
        live = lc.RenderState("Video Input", mediator_on=False)
        live.update(self.args, self.anim_args, 0)
        self.assertEqual((live.motion_mode, live.video_mix, live.video_only), ("3D", 0.0, False))
        self.assertTrue(live.active)

    def test_an_existing_video_frame_is_used(self):
        self.anim_args.video_init_path = os.path.join(self.folder.name, "talk.mp4")
        frames = os.path.join(self.folder.name, "inputframes")
        os.makedirs(frames)
        open(os.path.join(frames, "talk000000007.jpg"), "wb").close()
        live = lc.RenderState("Video Input", mediator_on=False)
        live.update(self.args, self.anim_args, 7)
        self.assertTrue(live.video_only)
        self.assertTrue(live.frame_path(self.args, self.anim_args, 7).endswith("talk000000007.jpg"))

    def test_it_tells_the_page_what_the_render_is_doing(self):
        lc.set_enabled(True)
        lc.set_mode("3D")
        lc.set_video_percent(0)
        live = lc.RenderState("Video Input", mediator_on=False)
        live.update(self.args, self.anim_args, 5)
        with open(lc.STATUS_FILE, encoding="utf-8") as f:
            status = json.load(f)
        self.assertEqual((status["running"], status["frame"], status["mode"], status["video"], status["source"]),
                         (True, 5, "3D", 0.0, "bar"))
        self.assertEqual(status["project"], "Video Input")
        lc.clear_status()
        with open(lc.STATUS_FILE, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["running"], False)

    def test_the_depth_model_is_only_wanted_for_real_3d_frames(self):
        live = lc.RenderState("2D", mediator_on=False)
        self.assertFalse(live.needs_depth_model(self.anim_args, self.args))
        live.motion_mode = "3D"
        self.assertTrue(live.needs_depth_model(self.anim_args, self.args))
        live.video_only = True  # the video frame is the picture, nothing is warped
        self.assertFalse(live.needs_depth_model(self.anim_args, self.args))
        live.video_only = False
        self.anim_args.use_depth_warping = False
        self.assertFalse(live.needs_depth_model(self.anim_args, self.args))
        self.anim_args.use_depth_warping = True
        self.args.motion_preview_mode = True
        self.assertFalse(live.needs_depth_model(self.anim_args, self.args))


class FadeTest(unittest.TestCase):
    """The video init walking to its target by itself, one frame at a time."""

    FRAMES = 10

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.saved_file, lc.STATE_FILE = lc.STATE_FILE, os.path.join(self.folder.name, "live_control.json")
        self.saved_status, lc.STATUS_FILE = lc.STATUS_FILE, os.path.join(self.folder.name, "live_control_status.json")
        self.saved_last, lc._last_good = lc._last_good, dict(lc.DEFAULT_STATE)
        self.args = types.SimpleNamespace(outdir=self.folder.name, motion_preview_mode=False)
        self.anim_args = types.SimpleNamespace(video_init_path=os.path.join(self.folder.name, "talk.mp4"),
                                               use_depth_warping=False, extract_nth_frame=1,
                                               overwrite_extracted_frames=False, extract_from_frame=0, extract_to_frame=-1)
        frames = os.path.join(self.folder.name, "inputframes")
        os.makedirs(frames)
        for i in range(40):
            open(os.path.join(frames, "talk%09d.jpg" % i), "wb").close()

    def tearDown(self):
        lc.STATE_FILE, lc.STATUS_FILE, lc._last_good = self.saved_file, self.saved_status, self.saved_last
        self.folder.cleanup()

    def run_frames(self, live, count, first=0):
        values = []
        for frame in range(first, first + count):
            live.update(self.args, self.anim_args, frame)
            values.append(round(live.video_mix, 3))
        return values

    def test_it_walks_from_where_it_is_to_the_target(self):
        lc.set_enabled(True)
        lc.set_mode("3D")
        lc.set_video_percent(100)
        live = lc.RenderState("Video Input", mediator_on=False)
        self.assertEqual(self.run_frames(live, 2), [1.0, 1.0])     # nothing moves until the fade is asked for
        lc.start_fade(self.FRAMES, 0)
        values = self.run_frames(live, self.FRAMES + 2, first=2)
        self.assertEqual(values[0], 1.0)                            # it starts from where the video was
        self.assertEqual(values[5], 0.5)                            # half way through, half the video
        self.assertEqual(values[self.FRAMES], 0.0)                  # and it lands exactly on the target
        self.assertEqual(values[self.FRAMES + 1], 0.0)              # and stays there
        self.assertEqual([round(v, 3) for v in values[:self.FRAMES]],
                         [round(1.0 - i / self.FRAMES, 3) for i in range(self.FRAMES)])
        self.assertIsNone(lc.read_state()["fade"])                  # the fade is done and gone
        self.assertEqual(lc.read_state()["video"], 0.0)             # and the bar shows where it ended

    def test_it_can_fade_the_video_back_in(self):
        lc.set_enabled(True)
        lc.set_mode("2D")
        lc.set_video_percent(0)
        live = lc.RenderState("3D", mediator_on=False)
        lc.start_fade(4, 100)
        self.assertEqual(self.run_frames(live, 5), [0.0, 0.25, 0.5, 0.75, 1.0])

    def test_a_running_fade_counts_as_an_override(self):
        lc.set_enabled(True)
        lc.set_mode("Deforum")      # nothing overridden, so a Video Input render is pure video
        live = lc.RenderState("Video Input", mediator_on=False)
        live.update(self.args, self.anim_args, 0)
        self.assertFalse(live.active)
        lc.start_fade(4, 0)
        live.update(self.args, self.anim_args, 1)
        self.assertTrue(live.active)
        self.assertEqual(live.video_mix, 1.0)
        live.update(self.args, self.anim_args, 2)
        self.assertEqual(round(live.video_mix, 3), 0.75)

    def test_deforumationqt_holding_the_video_cancels_the_fade(self):
        lc.set_enabled(True)
        lc.set_mode("3D")
        lc.set_video_percent(100)
        live = lc.RenderState("Video Input", mediator_on=False)
        lc.start_fade(10, 0)
        live.update(self.args, self.anim_args, 0)
        self.assertIsNotNone(lc.read_state()["fade"])
        motion, mix, active = lc.resolve("Video Input", lc.read_state(), "", "0.8")
        self.assertEqual(mix, 0.8)
        # what update() does when DeforumationQT hands over a value of its own
        lc.cancel_fade()
        self.assertIsNone(lc.read_state()["fade"])

    def test_silly_numbers_are_made_sensible(self):
        lc.start_fade(0, 500)
        fade = lc.read_state()["fade"]
        self.assertEqual((fade["frames"], fade["to"], fade["armed"]), (1, 1.0, True))
        lc.start_fade("30", "0")
        self.assertEqual(lc.read_state()["fade"]["frames"], 30)
        lc.cancel_fade()
        self.assertIsNone(lc.read_state()["fade"])

    def test_a_planned_fade_waits_for_its_frame(self):
        lc.set_enabled(True)
        lc.set_mode("3D")
        lc.set_video_percent(100)
        live = lc.RenderState("Video Input", mediator_on=False)
        lc.start_fade(4, 0, at_frame=10)
        before = self.run_frames(live, 10)                      # frames 0 to 9: the video, untouched
        self.assertEqual(before, [1.0] * 10)
        self.assertEqual(lc.read_state()["fade"]["at"], 10)     # still waiting
        during = self.run_frames(live, 6, first=10)             # and from frame 10 it walks
        self.assertEqual(during, [1.0, 0.75, 0.5, 0.25, 0.0, 0.0])
        self.assertIsNone(lc.read_state()["fade"])

    def test_a_planned_fade_says_so_while_it_waits(self):
        lc.set_enabled(True)
        lc.set_mode("3D")
        lc.set_video_percent(100)
        live = lc.RenderState("Video Input", mediator_on=False)
        lc.start_fade(4, 0, at_frame=20)
        live.update(self.args, self.anim_args, 3)
        with open(lc.STATUS_FILE, encoding="utf-8") as f:
            status = json.load(f)
        self.assertEqual((status["fade_at"], status["fade_left"]), (20, None))

    def test_a_frame_already_gone_by_starts_at_once(self):
        lc.set_enabled(True)
        lc.set_mode("3D")
        lc.set_video_percent(100)
        live = lc.RenderState("Video Input", mediator_on=False)
        lc.start_fade(4, 0, at_frame=2)
        self.assertEqual(self.run_frames(live, 3, first=7), [1.0, 0.75, 0.5])

    def test_a_hand_on_the_slider_stops_the_fade(self):
        lc.set_enabled(True)
        lc.set_mode("3D")
        lc.set_video_percent(100)
        live = lc.RenderState("Video Input", mediator_on=False)
        lc.start_fade(10, 0)
        self.run_frames(live, 3)
        lc.set_video_percent(80)
        self.assertIsNone(lc.read_state()["fade"])
        self.assertEqual(self.run_frames(live, 2, first=3), [0.8, 0.8])

    def test_the_line_under_the_bar_says_a_fade_is_running(self):
        lc.set_enabled(True)
        lc.set_mode("3D")
        lc.set_video_percent(100)
        live = lc.RenderState("Video Input", mediator_on=False)
        lc.start_fade(8, 0)
        live.update(self.args, self.anim_args, 0)
        live.update(self.args, self.anim_args, 1)
        with open(lc.STATUS_FILE, encoding="utf-8") as f:
            status = json.load(f)
        self.assertEqual((status["fade_to"], status["fade_left"]), (0.0, 7))


class PlanTest(unittest.TestCase):
    """A list of steps written down before the render, carried out as it goes."""

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.saved_file, lc.STATE_FILE = lc.STATE_FILE, os.path.join(self.folder.name, "live_control.json")
        self.saved_status, lc.STATUS_FILE = lc.STATUS_FILE, os.path.join(self.folder.name, "live_control_status.json")
        self.saved_last, lc._last_good = lc._last_good, dict(lc.DEFAULT_STATE)
        self.args = types.SimpleNamespace(outdir=self.folder.name, motion_preview_mode=False)
        self.anim_args = types.SimpleNamespace(video_init_path=os.path.join(self.folder.name, "talk.mp4"),
                                               use_depth_warping=False, extract_nth_frame=1,
                                               overwrite_extracted_frames=False, extract_from_frame=0, extract_to_frame=-1)
        frames = os.path.join(self.folder.name, "inputframes")
        os.makedirs(frames)
        for i in range(60):
            open(os.path.join(frames, "talk%09d.jpg" % i), "wb").close()
        lc.set_enabled(True)

    def tearDown(self):
        lc.STATE_FILE, lc.STATUS_FILE, lc._last_good = self.saved_file, self.saved_status, self.saved_last
        self.folder.cleanup()

    def walk(self, live, count, first=0):
        out = []
        for frame in range(first, first + count):
            live.update(self.args, self.anim_args, frame)
            out.append((live.motion_mode, round(live.video_mix, 3)))
        return out

    def test_what_the_table_keeps_and_what_it_drops(self):
        lc.set_plan([[10, "3D", 0, 40], ["", "", "", ""], [None, "", None, None], [5, "video", 100, None],
                     [20, "nonsense", None, None], [-3, "2D", None, None]])
        plan = lc.read_state()["plan"]
        self.assertEqual([(e["at"], e["mode"], e["video"], e["frames"]) for e in plan],
                         [(0, "2D", None, 0),                      # a frame before the start is simply the start
                          (5, "Video Input", 1.0, 0), (10, "3D", 0.0, 40)])   # sorted, and the empty rows are gone
        self.assertEqual(lc.plan_rows(), [[0, "2D", None, 0], [5, "Video Input", 100, 0], [10, "3D", 0, 40],
                                          [None, "", None, None]])

    def test_the_steps_happen_on_their_frames(self):
        lc.set_mode("Deforum")
        lc.set_video_percent(100)
        lc.set_plan([[4, "3D", 0, 4]])
        live = lc.RenderState("Video Input", mediator_on=False)
        self.assertEqual(self.walk(live, 4), [("2D", 1.0)] * 4)          # before frame 4: the video, untouched
        self.assertEqual(lc.read_state()["mode"], "Deforum")
        self.assertEqual(self.walk(live, 6, first=4),
                         [("3D", 1.0), ("3D", 0.75), ("3D", 0.5), ("3D", 0.25), ("3D", 0.0), ("3D", 0.0)])
        self.assertEqual(lc.read_state()["mode"], "3D")                  # the step moved the bar itself

    def test_a_step_without_a_fade_jumps(self):
        lc.set_mode("3D")
        lc.set_video_percent(100)
        lc.set_plan([[2, "", 0, 0]])
        live = lc.RenderState("Video Input", mediator_on=False)
        self.assertEqual(self.walk(live, 4), [("3D", 1.0), ("3D", 1.0), ("3D", 0.0), ("3D", 0.0)])

    def test_a_whole_clip_in_one_list(self):
        lc.set_mode("Deforum")
        lc.set_video_percent(100)
        lc.set_plan([[3, "3D", 0, 3], [9, "", 100, 2], [12, "Deforum", None, None]])
        live = lc.RenderState("Video Input", mediator_on=False)
        shares = [share for _mode, share in self.walk(live, 14)]
        self.assertEqual(shares, [1.0, 1.0, 1.0,          # the video
                                  1.0, round(2 / 3, 3), round(1 / 3, 3),   # fading out from frame 3
                                  0.0, 0.0, 0.0,          # the animation alone
                                  0.0, 0.5, 1.0,          # and back in from frame 9
                                  1.0, 1.0])              # the video again, with the mode handed back at 12
        self.assertEqual(lc.read_state()["mode"], "Deforum")

    def test_every_step_happens_once(self):
        lc.set_plan([[2, "2D", None, None]])
        live = lc.RenderState("Video Input", mediator_on=False)
        self.walk(live, 5)
        lc.set_mode("Deforum")                       # by hand, after the step already ran
        self.walk(live, 3, first=5)
        self.assertEqual(lc.read_state()["mode"], "Deforum")

    def test_the_plan_waits_for_the_master_switch(self):
        lc.set_enabled(False)
        lc.set_plan([[1, "3D", 0, 0]])
        live = lc.RenderState("Video Input", mediator_on=False)
        self.assertEqual(self.walk(live, 3), [("2D", 1.0)] * 3)
        self.assertEqual(lc.read_state()["mode"], "Deforum")

    def test_the_line_under_the_bar_says_what_is_next(self):
        lc.set_plan([[5, "3D", 0, 4], [30, "", 100, 10]])
        live = lc.RenderState("Video Input", mediator_on=False)
        live.update(self.args, self.anim_args, 0)
        with open(lc.STATUS_FILE, encoding="utf-8") as f:
            status = json.load(f)
        self.assertEqual((status["next_at"], status["next_what"]), (5, "3D + video 0% over 4"))


class MusicTest(unittest.TestCase):
    """The plan's first column written in music: beats, song parts and times."""

    SONG = {"beats": [30, 61, 95, 120], "parts": [0, 240, 600]}

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.saved_file, lc.STATE_FILE = lc.STATE_FILE, os.path.join(self.folder.name, "live_control.json")
        self.saved_last, lc._last_good = lc._last_good, dict(lc.DEFAULT_STATE)
        self.saved_points, lc.song_points = lc.song_points, lambda *args, **kwargs: dict(self.SONG)

    def tearDown(self):
        lc.STATE_FILE, lc._last_good, lc.song_points = self.saved_file, self.saved_last, self.saved_points
        self.folder.cleanup()

    def test_a_plain_frame_number(self):
        self.assertEqual(lc.parse_at("200", 15, self.SONG), (200, ""))
        self.assertEqual(lc.parse_at(200.0, 15, self.SONG), (200, ""))
        self.assertEqual(lc.parse_at("-3", 15, self.SONG)[0], 0)

    def test_a_time(self):
        self.assertEqual(lc.parse_at("1:30", 15, self.SONG)[0], 1350)
        self.assertEqual(lc.parse_at("12s", 15, self.SONG)[0], 180)
        self.assertEqual(lc.parse_at("0:02.5", 30, self.SONG)[0], 75)

    def test_beats_and_parts_counted_from_one(self):
        self.assertEqual(lc.parse_at("beat 1", 15, self.SONG)[0], 30)
        self.assertEqual(lc.parse_at("beat 4", 15, self.SONG)[0], 120)
        self.assertEqual(lc.parse_at("ביט 2", 15, self.SONG)[0], 61)
        self.assertEqual(lc.parse_at("part 2", 15, self.SONG)[0], 240)
        self.assertEqual(lc.parse_at("חלק 3", 15, self.SONG)[0], 600)
        self.assertEqual(lc.parse_at("beat#3", 15, self.SONG)[0], 95)

    def test_what_it_says_when_it_cannot(self):
        frame, note = lc.parse_at("beat 99", 15, self.SONG)
        self.assertIsNone(frame)
        self.assertIn("4 beats", note)
        frame, note = lc.parse_at("beat 1", 15, {"beats": [], "parts": []})
        self.assertIsNone(frame)
        self.assertIn("Audio Keyframes", note)
        frame, note = lc.parse_at("sometime", 15, self.SONG)
        self.assertIsNone(frame)
        self.assertIn("not a frame", note)

    def test_the_table_keeps_the_music_words(self):
        plan, notes = lc.set_plan([["beat 2", "3D", 0, 20], ["part 3", "", 100, 10], ["beat 99", "2D", None, None]],
                                  fps=15, path="song.mp3")
        self.assertEqual([(e["at"], e.get("at_text")) for e in plan], [(61, "beat 2"), (600, "part 3")])
        self.assertEqual(lc.plan_rows()[0][0], "beat 2")        # the table still shows what was typed
        self.assertTrue(any("4 beats" in note for note in notes))
        self.assertTrue(any("frame 61" in note for note in notes))

    def test_a_saved_plan_is_not_read_again_as_music(self):
        lc.set_plan([["beat 2", "3D", 0, 20]], fps=15, path="song.mp3")
        lc._last_good = dict(lc.DEFAULT_STATE)                  # force a read from the file
        again = lc.read_state()["plan"]
        self.assertEqual((again[0]["at"], again[0]["at_text"]), (61, "beat 2"))


if __name__ == "__main__":
    unittest.main()

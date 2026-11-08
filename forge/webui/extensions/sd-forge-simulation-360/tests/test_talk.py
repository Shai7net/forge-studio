"""Talking characters: the sound of a line, the performance written for it, lifting the character off its
flat colour, subtitles. Runs without Forge, LivePortrait or FaceFusion:

    venv\\Scripts\\python.exe extensions\\sd-forge-simulation-360\\tests\\test_talk.py
"""
import os
import sys
import tempfile
import types
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

ROOT = tempfile.mkdtemp(prefix="sim360-test-")
modules = types.ModuleType("modules")
modules.paths = types.SimpleNamespace(data_path=ROOT)
modules.shared = types.SimpleNamespace(opts=types.SimpleNamespace(data={}))
sys.modules["modules"] = modules
sys.modules["modules.paths"] = modules.paths
sys.modules["modules.shared"] = modules.shared

from sim360 import export, speech, talk  # noqa: E402
from sim360.runner import JobError  # noqa: E402


def tone(seconds, level=0.3, pitch=180.0, rate=speech.RATE):
    t = np.arange(int(seconds * rate)) / rate
    return (level * np.sin(2 * np.pi * pitch * t)).astype(np.float32)


class Sound(unittest.TestCase):
    def test_a_line_is_trimmed_and_brought_to_the_common_loudness(self):
        quiet_one = np.concatenate([np.zeros(speech.RATE), tone(2.0, 0.02), np.zeros(2 * speech.RATE)])
        loud_one = np.concatenate([np.zeros(speech.RATE // 2), tone(2.0, 0.8), np.zeros(speech.RATE)])
        levels = []
        for data in (quiet_one, loud_one):
            neat = speech.neaten(data.astype(np.float32), speech.RATE)
            self.assertAlmostEqual(len(neat) / speech.RATE, 2.0 + 2 * speech.QUIET + 0.11, delta=0.08)
            self.assertLessEqual(float(np.abs(neat).max()), speech.PEAK + 1e-3)
            self.assertEqual(float(np.abs(neat[:int(speech.QUIET * speech.RATE) - 10]).max()), 0.0)
            levels.append(float(np.sqrt((neat[int(0.5 * speech.RATE):int(1.5 * speech.RATE)] ** 2).mean())))
        self.assertAlmostEqual(levels[0], levels[1], delta=0.01)
        self.assertAlmostEqual(levels[0], speech.LOUDNESS, delta=0.02)

    def test_silence_is_refused(self):
        with self.assertRaises(JobError):
            speech.neaten(np.zeros(speech.RATE, np.float32), speech.RATE)

    def test_another_sample_rate_is_brought_to_ours(self):
        neat = speech.neaten(tone(1.0, 0.3, rate=16000), 16000)         # no silence around it: nothing to trim
        self.assertAlmostEqual(len(neat) / speech.RATE, 1.0 + 2 * speech.QUIET, delta=0.03)

    def test_the_voice_at_each_video_frame(self):
        data = np.concatenate([np.zeros(speech.RATE), tone(1.0), np.zeros(speech.RATE)])
        loud = speech.envelope(data, speech.RATE, 25)
        self.assertEqual(len(loud), 75)
        self.assertEqual(float(loud[:22].max()), 0.0)
        self.assertGreater(float(loud[30:45].min()), 0.9)
        self.assertEqual(float(loud[56:].max()), 0.0)
        bright = speech.brightness(np.concatenate([tone(1.0, pitch=300), tone(1.0, pitch=3000)]), speech.RATE, 25)
        self.assertLess(float(bright[5:20].mean()), 0.2)             # a dark sound
        self.assertGreater(float(bright[30:45].mean()), 0.8)         # a bright one


class Performance(unittest.TestCase):
    def setUp(self):
        self.loud = np.concatenate([np.zeros(10), np.tile([0.2, 1.0, 0.6, 0.1, 0.0], 20), np.zeros(15)]).astype(np.float32)

    def test_it_begins_and_ends_as_the_still_picture(self):
        made = talk.performance(5.0, 25, self.loud, None, liveliness=1.0, mood=1.0, lips=True, seed=3)
        self.assertEqual(made["n_frames"], 125)
        self.assertEqual(len(made["motion"]), 125)
        self.assertEqual(len(made["c_eyes_lst"]), 125)
        for frame in (made["motion"][0], made["motion"][-1]):
            np.testing.assert_allclose(frame["R"][0], np.eye(3), atol=1e-6)
            np.testing.assert_allclose(frame["exp"], 0, atol=1e-7)
            np.testing.assert_allclose(frame["t"], 0, atol=1e-7)
        for frame in made["motion"]:
            self.assertEqual(frame["R"].shape, (1, 3, 3))
            self.assertEqual(frame["exp"].shape, (1, 21, 3))
            self.assertEqual(frame["t"].shape, (1, 3))
            self.assertEqual(frame["scale"].shape, (1, 1))
            np.testing.assert_allclose(frame["R"][0] @ frame["R"][0].T, np.eye(3), atol=1e-5)

    def test_the_head_moves_but_stays_a_talking_head(self):
        made = talk.performance(5.0, 25, self.loud, None, liveliness=1.0, seed=3)
        turned = [np.degrees(np.arccos(np.clip((np.trace(f["R"][0]) - 1) / 2, -1, 1))) for f in made["motion"]]
        self.assertGreater(max(turned), 1.5)
        self.assertLess(max(turned), 14.0)
        calm = talk.performance(5.0, 25, self.loud, None, liveliness=0.0, seed=3)
        self.assertLess(max(np.degrees(np.arccos(np.clip((np.trace(f["R"][0]) - 1) / 2, -1, 1))) for f in calm["motion"]), max(turned) / 2)

    def test_the_eyes_blink(self):
        made = talk.performance(6.0, 25, self.loud, None, seed=5)
        closed = np.array([f["exp"][0, 11, 1] for f in made["motion"]])
        self.assertGreater(float(closed.max()), 0.02)
        self.assertLess(float((closed > 0.005).mean()), 0.25)       # and are open most of the time

    def test_the_mouth_is_left_to_the_lip_syncer_unless_asked(self):
        left = talk.performance(5.0, 25, self.loud, None, lips=False, seed=1)
        self.assertEqual(max(abs(float(f["exp"][0, 19, 1])) for f in left["motion"]), 0.0)
        moved = talk.performance(5.0, 25, self.loud, None, lips=True, seed=1)
        opened = np.array([f["exp"][0, 19, 1] for f in moved["motion"]])
        self.assertGreater(float(opened[11:100].max()), 0.02)
        self.assertEqual(float(opened[:8].max()), 0.0)              # nothing is said yet

    def test_the_same_seed_gives_the_same_performance(self):
        a, b = (talk.performance(3.0, 25, self.loud, None, seed=9) for _ in range(2))
        other = talk.performance(3.0, 25, self.loud, None, seed=10)
        self.assertTrue(all(np.array_equal(x["R"], y["R"]) and np.array_equal(x["exp"], y["exp"]) for x, y in zip(a["motion"], b["motion"])))
        self.assertFalse(all(np.array_equal(x["R"], y["R"]) for x, y in zip(a["motion"], other["motion"])))

    def test_a_face_that_only_lives(self):
        made = talk.performance(6.0, 25, None, None, liveliness=0.3, seed=2)
        self.assertEqual(made["n_frames"], 150)
        np.testing.assert_allclose(made["motion"][-1]["R"][0], np.eye(3), atol=1e-6)       # so the loop closes

    def test_the_turns_are_laid_out_as_liveportrait_lays_them_out(self):
        # LivePortrait: (rot_z @ rot_y @ rot_x) transposed, angles in degrees
        np.testing.assert_allclose(talk._rotation(0, 0, 0), np.eye(3), atol=1e-7)
        np.testing.assert_allclose(talk._rotation(0, 90, 0), [[0, 0, -1], [0, 1, 0], [1, 0, 0]], atol=1e-6)
        np.testing.assert_allclose(talk._rotation(90, 0, 0), [[1, 0, 0], [0, 0, 1], [0, -1, 0]], atol=1e-6)


class OffTheBackground(unittest.TestCase):
    def character(self):
        rgb = np.zeros((120, 80, 3), np.uint8)
        rgb[:] = (200, 150, 120)                                    # skin
        rgb[60:] = (30, 60, 200)                                    # a blue shirt
        alpha = np.zeros((120, 80), np.uint8)
        alpha[10:110, 20:60] = 255
        alpha[10:110, 18:20] = 128                                  # a soft edge
        return rgb, alpha

    def test_the_flat_colour_is_one_the_character_does_not_wear(self):
        rgb, alpha = self.character()
        self.assertEqual(talk.key_colour(rgb, alpha), (0, 255, 0))
        rgb[60:] = (20, 220, 30)                                    # a green shirt
        self.assertNotEqual(talk.key_colour(rgb, alpha), (0, 255, 0))

    def test_the_character_comes_back_off_the_flat_colour(self):
        rgb, alpha = self.character()
        for key in talk.KEYS:
            if key == (0, 0, 255):
                continue                                            # a blue shirt on blue is the case key_colour avoids
            seen = talk.on_key(rgb, alpha, key)
            colours, cut = talk.rimmed(seen, *talk.unkey(seen, key), key)
            self.assertEqual(int(cut[alpha == 0].max()), 0, key)
            self.assertEqual(int(cut[alpha == 255].min()), 255, key)
            self.assertLess(abs(float(cut[alpha == 128].mean()) - 128), 40, key)
            self.assertLess(float(np.abs(colours[alpha == 255] - rgb[alpha == 255]).max()), 4, key)
            # the soft edge carries no tint of the key colour: it is no nearer to it than the character's own
            # colours (skin, shirt, and where the two meet) are
            own = np.array([np.array([200, 150, 120]) * x + np.array([30, 60, 200]) * (1 - x) for x in np.linspace(0, 1, 21)], np.float32)
            edge = colours[alpha == 128]
            self.assertLessEqual(float(talk._lean(edge.astype(np.float32), key).max()), float(talk._lean(own, key).max()) + 1.0, key)

    def test_only_what_moved_is_taken_from_the_video(self):
        still = np.zeros((200, 120), np.float32)
        self.assertEqual(float(talk.moved_part(still, 200).max()), 0.0)
        still[40:80, 40:80] = 60                                    # the head moved
        still[150, 10] = 60                                         # one speckle of the encoder
        part = talk.moved_part(still, 200)
        self.assertGreater(float(part[45:75, 45:75].min()), 0.99)
        self.assertGreater(float(part[36, 60]), 0.5)                # a margin around it
        self.assertEqual(float(part[150:, :30].max()), 0.0)
        self.assertEqual(float(part[180:].max()), 0.0)


class CloseUp(unittest.TestCase):
    """A solid figure says its line in a close-up of its head: the part of its picture, enlarged."""

    def picture(self):
        from PIL import Image
        from sim360 import store
        store.project_dir("Close up", create=True)
        folder = store.world_dir("Close up", "wtest")
        pixels = np.zeros((300, 200, 4), np.uint8)
        pixels[60:280, 60:140] = (40, 90, 200, 255)                 # a body
        pixels[20:60, 80:120] = (220, 170, 140, 255)                # its head
        Image.fromarray(pixels).save(os.path.join(folder, "object-o1-0001.png"))
        return "Close up", "wtest", "object-o1-0001.png"

    def test_what_is_asked_for_is_a_part_of_the_picture(self):
        self.assertEqual(talk.close_up([0.3, 0.05, 0.7, 0.3]), [0.3, 0.05, 0.7, 0.3])
        self.assertIsNone(talk.close_up([0.3, 0.05, 0.31, 0.3]))    # a sliver
        self.assertIsNone(talk.close_up("the head"))
        self.assertIsNone(talk.close_up([0.3, 0.05, 0.7]))

    def test_the_whole_picture_is_never_enlarged(self):
        rgb, alpha = talk._still(*self.picture(), side=512)
        self.assertEqual(alpha.shape, (300, 200))

    def test_the_head_is_enlarged_to_the_size_faces_are_worked_at(self):
        rgb, alpha = talk._still(*self.picture(), side=512, crop=[0.3, 0.0, 0.7, 80 / 300])
        self.assertEqual(alpha.shape, (512, 512))                   # 80 x 80 pixels of the picture
        self.assertEqual(tuple(int(v) for v in rgb[200, 256]), (220, 170, 140))         # the head, in the middle
        self.assertEqual(int(alpha[200, 20]), 0)                    # beside it: nothing

    def test_past_the_edge_of_the_picture_there_is_nothing(self):
        rgb, alpha = talk._still(*self.picture(), side=256, crop=[0.2, -0.1, 0.8, 0.3])
        self.assertEqual(alpha.shape, (256, 256))
        self.assertEqual(int(alpha[:40].max()), 0)                  # above the picture's top edge
        self.assertEqual(int(alpha[200, 128]), 255)


class AliveBetweenLines(unittest.TestCase):
    """A speaker's whole stretch of a scene is one sound for its face to move to: its lines at their moments."""

    def test_the_lines_fall_at_their_moments_and_there_is_silence_between_them(self):
        from sim360 import store
        store.project_dir("Stretch", create=True)
        folder = store.world_dir("Stretch", "wtest")
        speech.write(os.path.join(folder, "speech-a-0001.wav"), tone(1.0, 0.3))
        speech.write(os.path.join(folder, "speech-b-0001.wav"), tone(0.5, 0.3))
        path = os.path.join(folder, "stretch.wav")
        talk._stretch("Stretch", [{"audio": "speech-a-0001.wav", "world": "wtest", "at": 1.0}, {"audio": "speech-b-0001.wav", "world": "wtest", "at": 4.0}], 6.0, path)
        data, rate = speech.read(path)
        self.assertAlmostEqual(len(data) / rate, 6.0, delta=0.01)
        loud = lambda a, b: float(np.sqrt(np.mean(np.asarray(data[int(a * rate):int(b * rate)], np.float64) ** 2)))
        self.assertGreater(loud(1.1, 1.9), 0.1)
        self.assertGreater(loud(4.1, 4.4), 0.1)
        self.assertLess(loud(0.0, 0.9), 0.001)
        self.assertLess(loud(2.2, 3.9), 0.001)
        self.assertLess(loud(4.7, 6.0), 0.001)

    def test_a_talking_video_joins_the_still_picture_softly(self):
        self.assertGreaterEqual(talk.SOFT_ENDS, 4)                  # (a face must not change skin in one frame)


class MuseTalk(unittest.TestCase):
    """MuseTalk is a lip syncer when its folder holds all it needs, and only then."""

    def folder(self, without=None):
        home = tempfile.mkdtemp(prefix="sim360-musetalk-")
        for part in talk.MUSETALK_NEEDS:
            if part == without:
                continue
            path = os.path.join(home, *part)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as file:
                file.write(b"x")
        return home

    def tearDown(self):
        modules.shared.opts.data.pop("sim360_musetalk_dir", None)

    def test_it_is_found_where_settings_say(self):
        home = self.folder()
        self.assertIsNone(talk.musetalk_home())                     # nothing in models\sim360-musetalk here
        modules.shared.opts.data["sim360_musetalk_dir"] = home
        self.assertEqual(talk.musetalk_home(), os.path.abspath(home))
        self.assertIn(talk.MUSETALK, talk.lip_models())

    def test_a_folder_that_lacks_a_weight_is_not_musetalk(self):
        for missing in (("app", "models", "musetalkV15", "unet.pth"), ("env", "Scripts", "python.exe"), ("app", "models", "dwpose", "dw-ll_ucoco_384.pth")):
            modules.shared.opts.data["sim360_musetalk_dir"] = self.folder(without=missing)
            self.assertIsNone(talk.musetalk_home(), missing)
            self.assertNotIn(talk.MUSETALK, talk.lip_models())

    def test_who_shaped_the_mouth_is_called_by_name(self):
        self.assertEqual(talk.lip_syncer_name(talk.MUSETALK), "MuseTalk")
        self.assertEqual(talk.lip_syncer_name("wav2lip_gan_96"), "FaceFusion")

    def test_without_its_folder_it_shapes_nothing(self):
        job = types.SimpleNamespace(status="running", share=0)
        self.assertFalse(talk.lip_sync(job, "in.mp4", "in.wav", "out.mp4", talk.MUSETALK, tempfile.gettempdir()))


class Subtitles(unittest.TestCase):
    def test_an_srt_file(self):
        text = export.subtitle_text([
            {"start": 3.25, "end": 5.0, "text": "second  line"},
            {"start": 0.5, "end": 2.0, "text": "שלום. מה שלומך?"},
            {"start": 6, "end": 6, "text": "no time at all"},
            {"start": 7, "end": 8, "text": "   "},
        ])
        blocks = text.strip().split("\n\n")
        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[0].split("\n")[:2], ["1", "00:00:00,500 --> 00:00:02,000"])
        self.assertEqual(blocks[0].split("\n")[2], "‫שלום. מה שלומך?‬")         # told to run from the right
        self.assertEqual(blocks[1].split("\n"), ["2", "00:00:03,250 --> 00:00:05,000", "second line"])
        self.assertEqual(export.subtitle_text([]), "")
        self.assertEqual(export._stamp(3725.5), "01:02:05,500")


if __name__ == "__main__":
    unittest.main(verbosity=1)

"""Exports from the edit, checked by pixels: placement, stacking, transparency, fades, speed, sound and loudness.

Run from the extension folder:  ..\\..\\venv\\Scripts\\python.exe tests\\test_render.py
"""

import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest


HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
TMP = tempfile.mkdtemp(prefix="forge-studio-render-")
os.environ["FORGE_STUDIO_ROOT"] = os.path.join(TMP, "data")
os.environ["FORGE_STUDIO_LIBRARY"] = os.path.join(TMP, "lib")
os.environ["FORGE_STUDIO_EXPORTS"] = os.path.join(TMP, "exports")

from forgestudio import library, media, projects, render  # noqa: E402

FF = library.ffmpeg()
W, H = 360, 640


def ff(*a):
    subprocess.run([FF, "-v", "error", "-y", *a], check=True)


def pixel(path, t, x, y):
    out = subprocess.run([FF, "-v", "error", "-ss", f"{t}", "-i", path, "-frames:v", "1", "-vf",
                          f"crop=1:1:{x}:{y},format=rgb24", "-f", "rawvideo", "-"], capture_output=True).stdout
    return tuple(out[:3])


def loudness(path):
    err = subprocess.run([FF, "-hide_banner", "-i", path, "-af", "ebur128", "-f", "null", "-"],
                         capture_output=True, text=True, errors="replace").stderr
    line = [l for l in err.splitlines() if l.strip().startswith("I:")][-1]
    return float(line.split()[1])


class Render(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        src = os.path.join(TMP, "src")
        os.makedirs(src)
        cls.red = os.path.join(src, "red.mp4")
        cls.blue = os.path.join(src, "blue.mp4")
        cls.box = os.path.join(src, "box.png")
        cls.tone = os.path.join(src, "tone.wav")
        ff("-f", "lavfi", "-i", "color=red:s=720x1280:r=30:d=4", "-f", "lavfi", "-i", "sine=f=440:d=4",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", cls.red)
        ff("-f", "lavfi", "-i", "color=blue:s=1280x720:r=30:d=4", "-c:v", "libx264", "-pix_fmt", "yuv420p", cls.blue)
        from PIL import Image
        Image.new("RGBA", (200, 200), (255, 255, 0, 255)).save(cls.box)
        ff("-f", "lavfi", "-i", "sine=f=220:d=3", cls.tone)
        projects.create("r")
        cls.w = {k: projects.add_media("r", p) for k, p in (("red", cls.red), ("blue", cls.blue), ("box", cls.box), ("tone", cls.tone))}

    def doc(self, clips, tracks=None):
        d = projects.empty_doc(W, H, 30)
        d["tracks"] = tracks or [{"id": "v1", "kind": "video", "main": True}, {"id": "v2", "kind": "video"},
                                 {"id": "a1", "kind": "audio"}]
        d["clips"] = clips
        return d

    def export(self, doc, loud=None):
        job = render.start("r", doc, "h264-soft", loud)
        while render.jobs[job["id"]]["state"] == "running":
            time.sleep(0.2)
        j = render.public(render.jobs[job["id"]])
        self.assertEqual(j["state"], "done", j.get("message"))
        return j["out"]

    def test_layers_placement_and_fades(self):
        clips = [
            {"id": "a", "track": "v1", "kind": "video", "media": self.w["red"], "start": 0, "duration": 2, "in": 0},
            {"id": "b", "track": "v1", "kind": "video", "media": self.w["blue"], "start": 2, "duration": 2, "in": 1,
             "fadeOut": 1.0},
            # a yellow square, a quarter of the frame's width, in the top-left quarter, half see-through
            {"id": "c", "track": "v2", "kind": "image", "media": self.w["box"], "start": 0.5, "duration": 3,
             "scale": 0.25, "x": -0.25, "y": -0.25, "opacity": 0.5},
        ]
        out = self.export(self.doc(clips))
        r = pixel(out, 0.25, W // 2, H // 2)
        self.assertTrue(r[0] > 200 and r[2] < 60, r)                       # red fills the frame
        b = pixel(out, 2.5, W // 2, H // 2)
        self.assertTrue(b[2] > 200 and b[0] < 60, b)                       # blue, letterboxed in the middle
        self.assertTrue(max(pixel(out, 2.5, W // 2, 20)) < 30)             # black above the landscape clip
        y = pixel(out, 1.0, W // 4, H // 4)                                # half yellow over red = orange
        self.assertTrue(abs(y[0] - 255) < 25 and 100 < y[1] < 160 and y[2] < 40, y)
        self.assertTrue(pixel(out, 1.0, W // 4, int(H * 0.45))[1] < 40)   # outside the square: still red
        self.assertLess(max(pixel(out, 3.95, W // 2, H // 2)), 40)         # faded to black at the end
        probe = media.probe(out)
        self.assertEqual((probe["width"], probe["height"]), (W, H))
        self.assertAlmostEqual(probe["duration"], 4.0, delta=0.1)

    def test_speed_sound_and_loudness(self):
        clips = [
            {"id": "a", "track": "v1", "kind": "video", "media": self.w["red"], "start": 0, "duration": 2, "in": 0,
             "speed": 2.0},
            {"id": "t", "track": "a1", "kind": "audio", "media": self.w["tone"], "start": 0.5, "duration": 1.5,
             "volume": 0.5, "fadeIn": 0.2},
        ]
        out = self.export(self.doc(clips), loud=-14)
        self.assertAlmostEqual(media.probe(out)["duration"], 2.0, delta=0.1)
        self.assertTrue(media.probe(out)["audio"])
        self.assertAlmostEqual(loudness(out), -14, delta=1.0)

    def test_transparent_webm_layer(self):
        """A cut-out (VP8 WebM with alpha) over another clip: only its opaque square covers what is under it."""
        webm = os.path.join(TMP, "src", "cut.webm")
        graph = ("color=black@0:s=400x400:r=30:d=2,format=rgba[bg];color=yellow:s=100x100:r=30:d=2,format=rgba[b];"
                 "[bg][b]overlay=150:150:shortest=1")
        ff("-filter_complex", graph, "-c:v", "libvpx", "-pix_fmt", "yuva420p", "-auto-alt-ref", "0", "-b:v", "2M", webm)
        self.assertTrue(media.probe(webm)["alpha"])
        web = projects.add_media("r", webm)
        clips = [
            {"id": "a", "track": "v1", "kind": "video", "media": self.w["red"], "start": 0, "duration": 2},
            {"id": "c", "track": "v2", "kind": "video", "media": web, "start": 0, "duration": 2},
        ]
        out = self.export(self.doc(clips))
        centre = pixel(out, 1.0, W // 2, H // 2)
        self.assertTrue(centre[0] > 200 and centre[1] > 200 and centre[2] < 60, centre)   # the yellow square
        edge = pixel(out, 1.0, W // 2, int(H * 0.38))
        self.assertTrue(edge[0] > 200 and edge[1] < 60, edge)                               # red shows through
        # moving (drawn frame by frame): the square's own colour all the way to its edge, red around it
        clips[1].update(keyEase="linear", keys={"x": [[0, 0], [2, 0.25]]})
        out = self.export(self.doc(clips))
        yellow = pixel(out, 1.0, W // 2 + 45, H // 2)
        self.assertTrue(yellow[0] > 200 and yellow[1] > 200 and yellow[2] < 60, yellow)
        inner = pixel(out, 1.0, W // 2 + 3, H // 2)                                          # just inside its left edge
        self.assertTrue(inner[0] > 200 and inner[1] > 150, inner)
        self.assertTrue(pixel(out, 1.0, W // 2 - 10, H // 2)[1] < 60)                       # left of it: red again

    def test_colour_matches_the_players_canvas_filter(self):
        """brightness(), contrast(), saturate() as CSS defines them, on a known colour."""
        from PIL import Image
        swatch = os.path.join(TMP, "src", "swatch.png")
        Image.new("RGB", (64, 64), (200, 100, 50)).save(swatch)
        web = projects.add_media("r", swatch)
        b, k, s = 1.1, 1.3, 0.6

        def css(rgb):
            r, g, bl = [min(255.0, v * b) for v in rgb]
            r, g, bl = [min(255.0, max(0.0, (v / 255 - 0.5) * k * 255 + 127.5)) for v in (r, g, bl)]
            m = [[0.213 + 0.787 * s, 0.715 - 0.715 * s, 0.072 - 0.072 * s],
                 [0.213 - 0.213 * s, 0.715 + 0.285 * s, 0.072 - 0.072 * s],
                 [0.213 - 0.213 * s, 0.715 - 0.715 * s, 0.072 + 0.928 * s]]
            return [min(255, max(0, round(sum(m[i][j] * (r, g, bl)[j] for j in range(3))))) for i in range(3)]
        clips = [{"id": "s", "track": "v1", "kind": "image", "media": web, "start": 0, "duration": 1, "scale": 3,
                  "brightness": b, "contrast": k, "saturation": s}]
        out = self.export(self.doc(clips))
        got = pixel(out, 0.5, W // 2, H // 2)
        want = css((200, 100, 50))
        self.assertTrue(all(abs(g - w) <= 8 for g, w in zip(got, want)), (got, want))
        # then warmth and tint, as channel gains after the rest
        clips[0].update(warmth=-0.6, tint=0.5)
        gr, gg, gb = render.gains(clips[0])
        self.assertAlmostEqual(gr, 0.85 * 1.05)
        out = self.export(self.doc(clips))
        got = pixel(out, 0.5, W // 2, H // 2)
        want = [min(255, round(v * g)) for v, g in zip(css((200, 100, 50)), (gr, gg, gb))]
        self.assertTrue(all(abs(g - w) <= 8 for g, w in zip(got, want)), (got, want))

    def test_dissolve_at_a_cut(self):
        clips = [
            {"id": "a", "track": "v1", "kind": "video", "media": self.w["red"], "start": 0, "duration": 2, "in": 0},
            {"id": "b", "track": "v1", "kind": "video", "media": self.w["red"], "start": 2, "duration": 2, "in": 0,
             "saturation": 0, "transitionIn": 1.0},
        ]
        out = self.export(self.doc(clips))
        self.assertTrue(pixel(out, 1.5, W // 2, H // 2)[1] < 40)            # red alone before the cut
        m = pixel(out, 2.5, W // 2, H // 2)                                 # half red under half grey
        self.assertTrue(m[0] > 140 and 25 < m[1] < 75, m)
        g = pixel(out, 3.5, W // 2, H // 2)                                 # grey alone after the dissolve
        self.assertTrue(abs(g[0] - g[1]) < 20 and g[1] > 40, g)
        self.assertAlmostEqual(media.probe(out)["duration"], 4.0, delta=0.1)

    def test_dips_slides_and_pushes(self):
        from PIL import Image
        frame = os.path.join(TMP, "src", "blue-frame.png")
        Image.new("RGB", (720, 1280), (0, 0, 255)).save(frame)
        blue = projects.add_media("r", frame)

        def cut(kind):
            return self.doc([
                {"id": "a", "track": "v1", "kind": "video", "media": self.w["red"], "start": 0, "duration": 2, "in": 0},
                {"id": "b", "track": "v1", "kind": "image", "media": blue, "start": 2, "duration": 2,
                 "transitionIn": 1.0, "transition": kind},
            ])

        def near(got, want, tol=30):
            return all(abs(g - w) <= tol for g, w in zip(got, want))

        out = self.export(cut("black"))
        for t, want in ((2.25, (128, 0, 0)), (2.52, (0, 0, 0)), (2.75, (0, 0, 128)), (3.5, (0, 0, 255))):
            got = pixel(out, t, W // 2, H // 2)
            self.assertTrue(near(got, want), (t, got, want))
        out = self.export(cut("white"))
        got = pixel(out, 2.75, W // 2, H // 2)
        self.assertTrue(near(got, (128, 128, 255)), got)
        # halfway (eased: halfway too), the new picture has come in half a frame from the right, over the red one
        out = self.export(cut("slide:right"))
        self.assertTrue(near(pixel(out, 2.5, W // 4, H // 2), (255, 0, 0)), pixel(out, 2.5, W // 4, H // 2))
        self.assertTrue(near(pixel(out, 2.5, 3 * W // 4, H // 2), (0, 0, 255)), pixel(out, 2.5, 3 * W // 4, H // 2))
        self.assertTrue(near(pixel(out, 3.5, W // 4, H // 2), (0, 0, 255)))
        # pushed from the left: the new one on the left half, the old one pushed to the right half
        out = self.export(cut("push:left"))
        self.assertTrue(near(pixel(out, 2.5, W // 4, H // 2), (0, 0, 255)), pixel(out, 2.5, W // 4, H // 2))
        self.assertTrue(near(pixel(out, 2.5, 3 * W // 4, H // 2), (255, 0, 0)), pixel(out, 2.5, 3 * W // 4, H // 2))
        # from the top, a quarter of the way: eased, so it has come in 0.156 of the frame
        out = self.export(cut("slide:top"))
        self.assertTrue(near(pixel(out, 2.25, W // 2, int(H * 0.12)), (0, 0, 255)), pixel(out, 2.25, W // 2, int(H * 0.12)))
        self.assertTrue(near(pixel(out, 2.25, W // 2, int(H * 0.2)), (255, 0, 0)), pixel(out, 2.25, W // 2, int(H * 0.2)))

    def test_keyframes_move_a_layer(self):
        # the yellow square, a quarter of the frame wide, slides from the left quarter to the right one, evenly
        clips = [
            {"id": "a", "track": "v1", "kind": "video", "media": self.w["red"], "start": 0, "duration": 2, "in": 0},
            {"id": "k", "track": "v2", "kind": "image", "media": self.w["box"], "start": 0, "duration": 2, "scale": 0.25,
             "keyEase": "linear", "keys": {"x": [[0, -0.25], [2, 0.25]], "opacity": [[0, 1], [1, 1], [2, 0.5]]}},
        ]
        out = self.export(self.doc(clips))
        yellow = lambda p: p[0] > 200 and p[1] > 200 and p[2] < 60
        red = lambda p: p[0] > 200 and p[1] < 60
        self.assertTrue(yellow(pixel(out, 0.0, W // 4, H // 2)))           # starts in the left quarter
        self.assertTrue(red(pixel(out, 0.0, W // 2, H // 2)))
        self.assertTrue(yellow(pixel(out, 1.0, W // 2, H // 2)))           # in the middle halfway
        self.assertTrue(red(pixel(out, 1.0, W // 4 - 20, H // 2)))         # and gone from where it was
        self.assertTrue(red(pixel(out, 1.0, W // 2, H // 2 + W // 8 + 6)))  # its own size: a quarter of the width
        end = pixel(out, 1.95, int(W * 0.74), H // 2)                      # ends on the right, half see-through
        self.assertTrue(end[0] > 200 and 100 < end[1] < 160, end)

    def test_keyframes_turn_and_size(self):
        # a landscape clip at half size turns a quarter of a turn: wide at the start, tall at the end
        clips = [{"id": "t", "track": "v1", "kind": "video", "media": self.w["blue"], "start": 0, "duration": 2, "in": 0,
                  "scale": 0.5, "keyEase": "linear", "keys": {"rotation": [[0, 0], [2, 90]]}}]
        out = self.export(self.doc(clips))
        blue = lambda p: p[2] > 200 and p[0] < 60
        self.assertTrue(blue(pixel(out, 0.0, W // 2 + 70, H // 2)))
        self.assertFalse(blue(pixel(out, 0.0, W // 2, H // 2 + 70)))
        self.assertTrue(blue(pixel(out, 1.95, W // 2, H // 2 + 70)))
        self.assertFalse(blue(pixel(out, 1.95, W // 2 + 70, H // 2)))
        # sizing: twice as big at the end, so a point beyond the first width is covered then
        clips[0].update(keys={"scale": [[0, 0.5], [2, 1.0]]}, keyEase="smooth")
        out = self.export(self.doc(clips))
        self.assertFalse(blue(pixel(out, 0.0, W // 2 + 120, H // 2)))
        self.assertTrue(blue(pixel(out, 1.95, W // 2 + 120, H // 2)))

    def test_keyframe_values_match_the_page(self):
        c = {"keys": {"x": [[1, 0.0], [3, 1.0]]}}
        self.assertEqual(render.value_at(c, "x", 0), 0.0)                 # held before the first key
        self.assertAlmostEqual(render.value_at(c, "x", 2), 0.5)           # smoothstep: halfway at the middle
        self.assertAlmostEqual(render.value_at(c, "x", 1.5), 0.15625)     # eased: slow at the start
        self.assertEqual(render.value_at(c, "x", 5), 1.0)                 # held after the last
        self.assertAlmostEqual(render.value_at(dict(c, keyEase="linear"), "x", 1.5), 0.25)
        self.assertEqual(render.value_at({"scale": 2}, "scale", 1), 2)    # no keys: the plain value

    def test_pending_clip_refused(self):
        clips = [{"id": "g", "track": "v1", "kind": "gen", "start": 0, "duration": 2}]
        with self.assertRaises(ValueError):
            render.build("r", self.doc(clips))

    def test_mixdown(self):
        clips = [{"id": "t", "track": "a1", "kind": "audio", "media": self.w["tone"], "start": 1, "duration": 2}]
        out = os.path.join(TMP, "mix.wav")
        render.mixdown("r", self.doc(clips), out)
        self.assertAlmostEqual(media.probe(out)["duration"], 3.0, delta=0.1)

    def test_music_lowered_under_speech(self):
        import numpy as np
        from forgestudio import duck
        src = os.path.join(TMP, "src")
        voice, music = os.path.join(src, "voice-say.wav"), os.path.join(src, "song.wav")
        # "speech": 1 kHz from 0.5 to 1.5 s and from 3.5 to 4.5 s of a 6 s file, silence between
        ff("-f", "lavfi", "-i", "sine=f=1000:d=6", "-af", "volume='between(t,0.5,1.5)+between(t,3.5,4.5)':eval=frame",
           "-ar", "48000", voice)
        ff("-f", "lavfi", "-i", "sine=f=220:d=8", "-ar", "48000", music)
        wv, wm = projects.add_media("r", voice), projects.add_media("r", music)
        # within a window of 20 ms (the test tone is switched in steps of 1024 samples)
        np.testing.assert_allclose(duck.talking(voice), [[0.5, 1.5], [3.5, 4.5]], atol=0.03)
        tracks = [{"id": "v1", "kind": "video", "main": True}, {"id": "a1", "kind": "audio"}, {"id": "a2", "kind": "audio"}]
        clips = [{"id": "m", "track": "a1", "kind": "audio", "media": wm, "start": 0, "duration": 8, "duck": 0.25},
                 {"id": "v", "track": "a2", "kind": "audio", "media": wv, "start": 1, "duration": 6}]
        doc = self.doc(clips, tracks)
        np.testing.assert_allclose(duck.stretches("r", doc), [[1.5, 2.5], [4.5, 5.5]], atol=0.03)
        out = os.path.join(TMP, "ducked.wav")
        render.mixdown("r", doc, out)
        y = np.frombuffer(subprocess.run([FF, "-v", "error", "-i", out, "-ac", "1", "-ar", "48000", "-f", "f32le", "-"],
                                         capture_output=True).stdout, np.float32)

        def music_level(t):
            # the 220 Hz part alone, over 0.1 s
            a = int(t * 48000)
            seg = y[a:a + 4800]
            k = np.arange(seg.size) / 48000
            return 2 * abs(np.mean(seg * np.exp(-2j * np.pi * 220 * k)))

        full = music_level(0.6)
        for t, want in ((2.0, 0.25), (3.5, 1.0), (5.0, 0.25), (6.5, 1.0)):
            self.assertAlmostEqual(music_level(t) / full, want, delta=0.03, msg=t)
            self.assertAlmostEqual(duck.gain(0.25, duck.stretches("r", doc), t + 0.05), want, delta=1e-6)
        # halfway down the attack, and part of the way up the release, as the page has it
        self.assertAlmostEqual(music_level(1.375 - 0.05) / full, duck.gain(0.25, [[1.5, 2.5]], 1.375), delta=0.05)
        self.assertAlmostEqual(music_level(2.8 - 0.05) / full, duck.gain(0.25, [[1.5, 2.5]], 2.8), delta=0.05)
        # unmarked: the narration is not speech any more, and nothing is lowered
        clips[1]["speech"] = False
        self.assertEqual(duck.stretches("r", doc), [])

    def test_typewriter_text_shows_its_letters_one_after_another(self):
        from PIL import Image
        import io as _io
        # four "letters": white squares from the right (as Hebrew is typed), the page's pictures for 0..3 of them
        spots = [(W - 60 - 70 * j, H // 2) for j in range(4)]

        def pic(k):
            im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            for j in range(k):
                x, y = spots[j]
                im.paste((255, 255, 255, 255), (x - 20, y - 20, x + 20, y + 20))
            b = _io.BytesIO()
            im.save(b, "PNG")
            return b.getvalue()

        render.store_text("r", "tw", pic(4))
        for k in range(4):
            render.store_typed("r", "tw", k, 4, pic(k), reset=(k == 0))
        clip = {"id": "tw", "track": "v2", "kind": "text", "start": 0, "duration": 2, "animIn": "type", "animInSec": 1.0,
                "text": {"text": "abcd"}}
        out = self.export(self.doc([clip]))
        for lt in (0.1, 0.35, 0.6, 0.85, 1.5):
            k = render.typed_count(clip, lt, 4)
            self.assertEqual(k, min(4, int(4 * lt)), lt)
            for j, (x, y) in enumerate(spots):
                lit = pixel(out, lt, x, y)[0] > 128
                self.assertEqual(lit, j < k, f"letter {j} at {lt} s")
        # typing out: from the end, gone by the clip's end
        clip2 = dict(clip, animIn=None, animOut="type", animOutSec=1.0)
        self.assertEqual([render.typed_count(clip2, t, 4) for t in (0.5, 1.1, 1.6, 1.99)], [4, 3, 1, 0])

    def test_sound_crossfades_at_a_cut_without_a_dip(self):
        import numpy as np
        src = os.path.join(TMP, "src")
        long_a, short_a, b = (os.path.join(src, n) for n in ("cross-a.wav", "cross-a-short.wav", "cross-b.wav"))
        ff("-f", "lavfi", "-i", "sine=f=300:d=6", "-ar", "48000", long_a)
        ff("-f", "lavfi", "-i", "sine=f=300:d=2", "-ar", "48000", short_a)
        ff("-f", "lavfi", "-i", "sine=f=500:d=4", "-ar", "48000", b)
        wa, wshort, wb = (projects.add_media("r", p) for p in (long_a, short_a, b))
        tracks = [{"id": "v1", "kind": "video", "main": True}, {"id": "a1", "kind": "audio"}]

        def mix(clips, tag):
            out = os.path.join(TMP, f"cross-{tag}.wav")
            render.mixdown("r", self.doc(clips, tracks), out)
            return np.frombuffer(subprocess.run([FF, "-v", "error", "-i", out, "-ac", "1", "-ar", "48000", "-f", "f32le", "-"],
                                                capture_output=True).stdout, np.float32)

        def level(y, f, t, span=0.01):
            # the f Hz part alone, over `span` seconds around t, relative to the sine's own amplitude (1/8)
            a, n = int((t - span / 2) * 48000), int(span * 48000)
            seg = y[a:a + n]
            k = np.arange(seg.size) / 48000
            return 2 * abs(np.mean(seg * np.exp(-2j * np.pi * f * k))) / 0.125

        A = {"id": "A", "track": "a1", "kind": "audio", "media": wa, "start": 0, "duration": 2}
        B = {"id": "B", "track": "a1", "kind": "audio", "media": wb, "start": 2, "duration": 2, "soundIn": 0.5}
        # A's file has more: A goes on past the cut under B, out as B comes in (equal power: the sum stays level)
        y = mix([A, B], "handle")
        for t, wa_, wb_ in ((1.0, 1, 0), (2.1, math.cos(math.pi / 2 * 0.2), math.sin(math.pi / 2 * 0.2)),
                            (2.25, math.sqrt(0.5), math.sqrt(0.5)), (2.4, math.cos(math.pi / 2 * 0.8), math.sin(math.pi / 2 * 0.8)), (3.0, 0, 1)):
            self.assertAlmostEqual(level(y, 300, t), wa_, delta=0.04, msg=f"A at {t}")
            self.assertAlmostEqual(level(y, 500, t), wb_, delta=0.04, msg=f"B at {t}")
            self.assertAlmostEqual(level(y, 300, t) ** 2 + level(y, 500, t) ** 2, 1.0, delta=0.06, msg=f"power at {t}")
        # A's file ends at the cut: it fades out over the last half second before it instead
        y = mix([dict(A, media=wshort), B], "nohandle")
        self.assertAlmostEqual(level(y, 300, 1.25), 1.0, delta=0.04)
        self.assertAlmostEqual(level(y, 300, 1.75), math.sqrt(0.5), delta=0.04)
        self.assertLess(level(y, 300, 2.25), 0.03)
        self.assertAlmostEqual(level(y, 500, 2.25), math.sqrt(0.5), delta=0.04)
        # a hard cut: both sides close to silent at the very cut (no click), full a few hundredths later
        y = mix([A, dict(B, soundIn=0)], "hard")
        at = int(2.0 * 48000)
        self.assertLess(float(np.max(np.abs(y[at - 24:at + 24]))) / 0.125, 0.35)
        self.assertAlmostEqual(level(y, 300, 1.98), 1.0, delta=0.05)
        self.assertAlmostEqual(level(y, 500, 2.02), 1.0, delta=0.05)
        # the two halves of a split: the sound goes straight on, no dip at all
        y = mix([dict(A, duration=1), dict(A, id="A2", start=1, duration=1, **{"in": 1})], "split")
        self.assertGreater(float(np.max(np.abs(y[48000 - 120:48000 + 120]))) / 0.125, 0.9)
        self.assertAlmostEqual(level(y, 300, 1.0), 1.0, delta=0.03)
        # a video clip with a picture transition: its sound crosses for as long, until set otherwise
        v = [{"id": "x", "track": "v1", "kind": "video", "media": self.w["red"], "start": 0, "duration": 2},
             {"id": "z", "track": "v1", "kind": "video", "media": self.w["red"], "start": 2, "duration": 1.5, "transitionIn": 0.8, **{"in": 2}}]
        cuts = render.soft_cuts("r", v, v)
        self.assertAlmostEqual(cuts["z"]["cross_in"], 0.8)
        self.assertAlmostEqual(cuts["x"]["tail"], 0.8)
        v[1]["soundIn"] = 0
        cuts = render.soft_cuts("r", v, v)
        self.assertEqual((cuts["z"]["cross_in"], cuts["x"]["tail"]), (0, 0))

    def test_crop_hides_edges_and_the_rest_stays_put(self):
        from PIL import Image
        quad = os.path.join(TMP, "src", "quarters.png")
        im = Image.new("RGB", (W, H), (255, 0, 0))     # red top left
        im.paste((0, 255, 0), (W // 2, 0, W, H // 2))  # green top right
        im.paste((0, 0, 255), (0, H // 2, W // 2, H))  # blue bottom left
        im.paste((255, 255, 0), (W // 2, H // 2, W, H))  # yellow bottom right
        im.save(quad)
        web = projects.add_media("r", quad)
        pts = {"tl": (W // 4, H // 4), "tr": (3 * W // 4, H // 4), "bl": (W // 4, 3 * H // 4), "br": (3 * W // 4, 3 * H // 4)}
        BLACK, RED, GREEN, BLUE, YELLOW = (0, 0, 0), (255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)

        def check(clip, want):
            out = self.export(self.doc([dict({"id": "q", "track": "v1", "kind": "image", "media": web, "start": 0, "duration": 1}, **clip)]))
            for name, colour in want.items():
                got = pixel(out, 0.5, *pts[name])
                self.assertTrue(all(abs(g - w) <= 40 for g, w in zip(got, colour)), (clip, name, got, colour))

        # the left half hidden: the right half where it was
        check({"cropL": 0.5}, {"tl": BLACK, "bl": BLACK, "tr": GREEN, "br": YELLOW})
        # the left half hidden, then turned half round about the clip's centre: the right half lands on the left
        check({"cropL": 0.5, "rotation": 180}, {"tl": YELLOW, "bl": GREEN, "tr": BLACK, "br": BLACK})
        # the top half hidden, drawn by moving.py (keyframes): the same
        check({"cropT": 0.5, "keys": {"opacity": [[0, 1], [1, 1]]}}, {"tl": BLACK, "tr": BLACK, "bl": BLUE, "br": YELLOW})
        check({"cropL": 0.5, "rotation": 180, "keys": {"opacity": [[0, 1], [1, 1]]}}, {"tl": YELLOW, "bl": GREEN, "tr": BLACK})
        self.assertAlmostEqual(render.crop_of({"kind": "video", "cropL": 0.8, "cropR": 0.8})[0], 0.475)

    def test_shape_masks(self):
        from PIL import Image
        quad = os.path.join(TMP, "src", "quarters-m.png")
        im = Image.new("RGB", (W, H), (255, 0, 0))
        im.paste((0, 255, 0), (W // 2, 0, W, H // 2))
        im.paste((0, 0, 255), (0, H // 2, W // 2, H))
        im.paste((255, 255, 0), (W // 2, H // 2, W, H))
        im.save(quad)
        web = projects.add_media("r", quad)

        def export(extra):
            return self.export(self.doc([dict({"id": "q", "track": "v1", "kind": "image", "media": web, "start": 0, "duration": 1}, **extra)]))

        def near(got, want, tol=40):
            return all(abs(g - w) <= tol for g, w in zip(got, want))

        for extra in ({"mask": "circle"}, {"mask": "circle", "keys": {"opacity": [[0, 1], [1, 1]]}}):
            out = export(extra)
            self.assertTrue(near(pixel(out, 0.5, 20, 30), (0, 0, 0)), ("corner outside the ellipse", extra))
            self.assertTrue(near(pixel(out, 0.5, 150, 200), (255, 0, 0)), ("inside", extra))
            self.assertTrue(near(pixel(out, 0.5, 340, 600), (0, 0, 0)), ("other corner", extra))
        out = export({"mask": "rounded", "maskRound": 0.15})
        self.assertTrue(near(pixel(out, 0.5, 4, 4), (0, 0, 0)), "the rounded corner")
        self.assertTrue(near(pixel(out, 0.5, 60, 300), (255, 0, 0)), "inside the rounded box")
        self.assertTrue(near(pixel(out, 0.5, 2, 320), (255, 0, 0)) or near(pixel(out, 0.5, 2, 300), (255, 0, 0)), "a straight edge is not cut")
        # soft: what the export shows is the colour times the mask render.py draws
        clip = {"kind": "image", "mask": "circle", "maskSoft": 0.2}
        m = render.mask_array(clip, W, H)
        out = export({"mask": "circle", "maskSoft": 0.2})
        for x, y in ((10, 160), (24, 160), (40, 160), (70, 160), (120, 100)):   # across the edge, in the red quarter
            want = round(255 * m[y, x] / 255)
            got = pixel(out, 0.5, x, y)[0]
            self.assertLess(abs(got - want), 25, (x, y, got, want))
        self.assertTrue(0 < m[160, 24] < 255 and m[320, 180] == 255)

    def test_a_huge_size_stays_bounded(self):
        """A clip keyed up to 60 times its size (typed by mistake once) must not draw a picture 60 times over."""
        import threading
        import psutil
        me, peak, done = psutil.Process(), [0], threading.Event()

        def watch():
            while not done.is_set():
                peak[0] = max(peak[0], me.memory_info().rss)
                time.sleep(0.05)
        threading.Thread(target=watch, daemon=True).start()
        try:
            self.export(self.doc([{"id": "b", "track": "v1", "kind": "image", "media": self.w["box"], "start": 0, "duration": 1,
                                   "mask": "circle", "maskSoft": 0.1, "keys": {"scale": [[0, 60], [1, 1]]}}]))
        finally:
            done.set()
        self.assertLess(peak[0], 3 * 2 ** 30, f"{peak[0] / 2 ** 30:.1f} GB")
        self.assertEqual(render.mask_array({"kind": "image", "mask": "circle"}, 6000, 9000).shape, (9000, 6000))

    def test_animations_in_and_out(self):
        import io
        from PIL import Image
        red = os.path.join(TMP, "src", "red-frame.png")
        Image.new("RGB", (W, H), (255, 0, 0)).save(red)
        web = projects.add_media("r", red)

        def export(extra, dur=2):
            return self.export(self.doc([dict({"id": "a", "track": "v1", "kind": "image", "media": web, "start": 0, "duration": dur}, **extra)]))

        def near(got, want, tol=30):
            return all(abs(g - w) <= tol for g, w in zip(got, want))

        out = export({"animIn": "fade", "animInSec": 1, "animOut": "fade", "animOutSec": 0.5})
        self.assertTrue(near(pixel(out, 0.5, 180, 320), (128, 0, 0)), pixel(out, 0.5, 180, 320))   # halfway in
        self.assertTrue(near(pixel(out, 1.3, 180, 320), (255, 0, 0)))
        self.assertTrue(near(pixel(out, 1.75, 180, 320), (128, 0, 0)), pixel(out, 1.75, 180, 320))  # halfway out
        # rise: halfway, 3% of the frame lower (19 px) and half see-through
        out = export({"animIn": "rise", "animInSec": 1})
        self.assertTrue(near(pixel(out, 0.5, 180, 8), (0, 0, 0)), pixel(out, 0.5, 180, 8))
        self.assertTrue(near(pixel(out, 0.5, 180, 100), (128, 0, 0)), pixel(out, 0.5, 180, 100))
        # pop: a quarter of the way, 92.7% of its size about the middle
        out = export({"animIn": "pop", "animInSec": 1})
        self.assertTrue(near(pixel(out, 0.25, 4, 320), (0, 0, 0)), pixel(out, 0.25, 4, 320))
        self.assertTrue(near(pixel(out, 0.25, 30, 320), (255, 0, 0)), pixel(out, 0.25, 30, 320))
        # wipe from the right: halfway, the right half shows
        out = export({"animIn": "wipe-r", "animInSec": 1})
        self.assertTrue(near(pixel(out, 0.5, 90, 320), (0, 0, 0)) and near(pixel(out, 0.5, 270, 320), (255, 0, 0)))
        # a text: the wipe runs across its letters (a white bar from x 100 to 260), not the frame
        T = {"id": "t", "track": "v1", "kind": "text", "start": 0, "duration": 2, "text": {"text": "x"}, "animIn": "wipe-r", "animInSec": 1}
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        img.paste((255, 255, 255, 255), (100, 500, 260, 560))
        buf = io.BytesIO()
        img.save(buf, "PNG")
        render.store_text("r", "t", buf.getvalue())
        np_anchor = render.anchor_of("r", T)
        self.assertTrue(all(abs(a - b) < 1e-6 for a, b in zip(np_anchor, (100 / W, 500 / H, 260 / W, 560 / H))), np_anchor)
        out = self.export(self.doc([T]))
        self.assertTrue(near(pixel(out, 0.5, 150, 530), (0, 0, 0)), pixel(out, 0.5, 150, 530))
        self.assertTrue(near(pixel(out, 0.5, 220, 530), (255, 255, 255)), pixel(out, 0.5, 220, 530))
        a = render.anim_at({"duration": 2, "animIn": "pop", "animInSec": 1}, 0.25)
        self.assertAlmostEqual(a["scale"], 0.6 + 0.4 * (1 - 2.70158 * 0.75 ** 3 + 1.70158 * 0.75 ** 2))

    def test_separated_sound_and_saved_frame(self):
        # the red clip's sound (440 Hz) as a clip of its own, the video quiet: as loud as the video was
        both = [{"id": "v", "track": "v1", "kind": "video", "media": self.w["red"], "start": 0, "duration": 2, "in": 0}]
        alone = [dict(both[0], muted=True), {"id": "s", "track": "a1", "kind": "audio", "media": self.w["red"], "start": 0, "duration": 2, "in": 0}]
        a, b = os.path.join(TMP, "both.wav"), os.path.join(TMP, "alone.wav")
        render.mixdown("r", self.doc(both), a)
        render.mixdown("r", self.doc(alone), b)
        self.assertAlmostEqual(loudness(a), loudness(b), delta=0.5)
        # a frame the page drew, saved next to the exports and in the library
        import base64
        import io
        from PIL import Image
        from forgestudio import api
        buf = io.BytesIO()
        Image.new("RGB", (W, H), (10, 200, 30)).save(buf, "PNG")
        r = api.api_still_save(name="r", image="data:image/png;base64," + base64.b64encode(buf.getvalue()).decode(), seconds=75.5)
        self.assertTrue(os.path.isfile(r["path"]) and "-frame-01m15_50s-" in os.path.basename(r["path"]), r)
        with Image.open(r["path"]) as im:
            self.assertEqual((im.size, im.getpixel((5, 5))), ((W, H), (10, 200, 30)))
        self.assertTrue(r["media"])

    def test_speed_ramp(self):
        import numpy as np
        rgb = os.path.join(TMP, "src", "rgb-ramp.mp4")
        ff("-f", "lavfi", "-i", "color=red:s=64x64:r=30:d=1", "-f", "lavfi", "-i", "color=green:s=64x64:r=30:d=1",
           "-f", "lavfi", "-i", "color=blue:s=64x64:r=30:d=1", "-f", "lavfi", "-i", "sine=f=330:d=3",
           "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]", "-map", "[v]", "-map", "3:a",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", rgb)
        web = projects.add_media("r", rgb)
        # the speed rises evenly from 0.5 to 2.5 over 2 s: the file second at lt is 0.5 lt + lt^2 / 2 (3 s in all)
        clip = {"id": "v", "track": "v1", "kind": "video", "media": web, "start": 0, "duration": 2, "in": 0, "scale": 5,
                "keyEase": "linear", "keys": {"speed": [[0, 0.5], [2, 2.5]]}}
        self.assertAlmostEqual(render.source_span(clip, 2), 3.0, places=4)
        self.assertAlmostEqual(render.source_span(clip, 0.8), 0.72, places=4)
        out = self.export(self.doc([clip]))
        for lt, want in ((0.8, (255, 0, 0)), (1.3, (0, 128, 0)), (1.8, (0, 0, 255))):
            got = pixel(out, lt, W // 2, H // 2)
            self.assertTrue(all(abs(g - w) <= 40 for g, w in zip(got, want)), (lt, got, want))
        # the sound follows the ramp like a tape: 330 Hz times the speed
        wav = os.path.join(TMP, "ramp.wav")
        render.mixdown("r", self.doc([clip]), wav)
        y = np.frombuffer(subprocess.run([FF, "-v", "error", "-i", wav, "-ac", "1", "-ar", "48000", "-f", "f32le", "-"],
                                         capture_output=True).stdout, np.float32)
        self.assertAlmostEqual(len(y) / 48000, 2.0, delta=0.05)

        def pitch(lt):
            seg = y[int((lt - 0.05) * 48000):int((lt + 0.05) * 48000)] * np.hanning(4800)
            spec = np.abs(np.fft.rfft(seg, 48000 * 2))
            return np.argmax(spec[100:]) / 2 + 50

        for lt in (0.25, 1.0, 1.5):
            self.assertAlmostEqual(pitch(lt), 330 * (0.5 + lt), delta=12, msg=lt)

    def test_chroma_key(self):
        import numpy as np
        from PIL import Image
        k = render.key_of({"kind": "video", "key": "#00ff00"})
        px = np.array([[[0, 255, 0, 255], [100, 200, 90, 255], [200, 40, 30, 255]]], np.uint8)
        self.assertEqual(render.chroma(k, px).tolist(), [[[0, 102, 0, 0], [100, 140, 90, 74], [200, 40, 30, 255]]])
        green = os.path.join(TMP, "src", "green-screen.png")
        im = Image.new("RGB", (W, H), (0, 255, 0))
        im.paste((220, 30, 30), (130, 270, 230, 370))   # a red square in the middle
        im.save(green)
        blue = os.path.join(TMP, "src", "blue-bg.png")
        Image.new("RGB", (W, H), (20, 40, 200)).save(blue)
        g, b = projects.add_media("r", green), projects.add_media("r", blue)
        out = self.export(self.doc([
            {"id": "bg", "track": "v1", "kind": "image", "media": b, "start": 0, "duration": 1},
            {"id": "fg", "track": "v2", "kind": "image", "media": g, "start": 0, "duration": 1, "key": "#00ff00"}]))
        near = lambda got, want: all(abs(x - y) <= 30 for x, y in zip(got, want))
        self.assertTrue(near(pixel(out, 0.5, 40, 60), (20, 40, 200)), pixel(out, 0.5, 40, 60))      # the green is gone
        self.assertTrue(near(pixel(out, 0.5, 180, 320), (220, 30, 30)), pixel(out, 0.5, 180, 320))  # the square stays

    def test_blurred_fill(self):
        from PIL import Image
        wide = os.path.join(TMP, "src", "wide-rb.png")
        im = Image.new("RGB", (360, 120), (255, 0, 0))
        im.paste((0, 0, 255), (180, 0, 360, 120))
        im.save(wide)
        web = projects.add_media("r", wide)
        clip = {"id": "w", "track": "v1", "kind": "image", "media": web, "start": 0, "duration": 1}
        out = self.export(self.doc([clip]))
        self.assertTrue(all(v < 20 for v in pixel(out, 0.5, 40, 50)), "black above it without a fill")
        clip["fill"] = "blur"
        self.assertEqual(render.fill_of(clip, H), (0.03 * H, math.ceil(3 * 0.03 * H), 0.85))
        out = self.export(self.doc([clip]))
        near = lambda got, want: all(abs(g - w) <= 20 for g, w in zip(got, want))
        # covering the frame, the middle of the picture fills it: red on the left, blue on the right, dimmed
        self.assertTrue(near(pixel(out, 0.5, 40, 50), (217, 0, 0)), pixel(out, 0.5, 40, 50))
        self.assertTrue(near(pixel(out, 0.5, 320, 600), (0, 0, 217)), pixel(out, 0.5, 320, 600))
        # soft across the middle (the blur), and the picture itself sharp on top
        mid = pixel(out, 0.5, 180, 50)
        self.assertTrue(80 < mid[0] < 180 and 80 < mid[2] < 180, mid)
        self.assertTrue(near(pixel(out, 0.5, 40, 320), (255, 0, 0)), pixel(out, 0.5, 40, 320))

    def test_no_black_frame_at_a_cut_between_frames(self):
        """Two shots meeting half way between two frames: before the fix the frame just before the cut was empty
        (overlay never showed a layer's own last frame), so the export had a black frame at every such cut."""
        import numpy as np
        cut = 1.0 + 0.37 / 30   # 30.37 frames in
        clips = [{"id": "a", "track": "v1", "kind": "video", "media": self.w["red"], "start": 0, "duration": cut, "in": 0},
                 {"id": "b", "track": "v1", "kind": "video", "media": self.w["red"], "start": cut, "duration": 2 - cut, "in": 0.5,
                  "saturation": 0},
                 {"id": "c", "track": "v1", "kind": "image", "media": self.w["box"], "start": 2, "duration": 1.0 + 1.0 / 3 / 30}]
        out = self.export(self.doc(clips))
        raw = subprocess.run([FF, "-v", "error", "-i", out, "-vf", f"crop=8:8:{W // 2}:{H // 2}", "-f", "rawvideo", "-pix_fmt", "gray",
                              "-"], capture_output=True).stdout
        means = np.frombuffer(raw, np.uint8).reshape(-1, 64).mean(axis=1)
        self.assertGreater(len(means), 85)
        dark = [i for i, m in enumerate(means) if m < 20]
        self.assertEqual(dark, [], f"black frames at {dark}")

    def test_volume_keyframes(self):
        import numpy as np
        song = os.path.join(TMP, "src", "steady.wav")
        ff("-f", "lavfi", "-i", "sine=f=220:d=4", "-ar", "48000", song)
        w = projects.add_media("r", song)
        # full, eased down to a quarter between 1 and 2 s of the clip, then held to its end
        clip = {"id": "m", "track": "a1", "kind": "audio", "media": w, "start": 0.5, "duration": 4,
                "keys": {"volume": [[1, 1.0], [2, 0.25], [3, 0.25]]}}
        out = os.path.join(TMP, "vol.wav")
        render.mixdown("r", self.doc([clip]), out)
        y = np.frombuffer(subprocess.run([FF, "-v", "error", "-i", out, "-ac", "1", "-ar", "48000", "-f", "f32le", "-"],
                                         capture_output=True).stdout, np.float32)

        def level(t):
            a = int(t * 48000)
            seg = y[a - 960:a + 960]
            k = np.arange(seg.size) / 48000
            return 2 * abs(np.mean(seg * np.exp(-2j * np.pi * 220 * k)))

        full = level(1.0)
        for lt, want in ((0.5, 1.0), (1.25, 1 - 0.75 * 0.15625), (1.5, 0.625), (2.5, 0.25), (3.6, 0.25)):
            self.assertAlmostEqual(level(0.5 + lt) / full, want, delta=0.03, msg=lt)
        clip["keyEase"] = "linear"
        self.assertIn("clip((t-1.0000)/1.0000,0,1)", render.volume_expression(clip))
        self.assertIsNone(render.volume_expression({"volume": 0.5}))

    def test_freeze_frame_and_backwards(self):
        from forgestudio import clipkit, frames
        rgb = os.path.join(TMP, "src", "rgb.mp4")
        # red, green, blue: a second each, with sound
        ff("-f", "lavfi", "-i", "color=red:s=64x64:r=30:d=1", "-f", "lavfi", "-i", "color=green:s=64x64:r=30:d=1",
           "-f", "lavfi", "-i", "color=blue:s=64x64:r=30:d=1", "-f", "lavfi", "-i", "sine=f=330:d=3",
           "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]", "-map", "[v]", "-map", "3:a",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", rgb)
        web = projects.add_media("r", rgb)
        for at, want in ((0.99, (255, 0, 0)), (1.0, (0, 128, 0)), (1.5, (0, 128, 0)), (2.9, (0, 0, 255))):
            still = clipkit.still("r", web, at)["path"]
            from PIL import Image
            with Image.open(still) as im:
                got = im.convert("RGB").getpixel((32, 32))
            self.assertTrue(all(abs(g - w) <= 30 for g, w in zip(got, want)), (at, got, want))
        was = clipkit.PIECE
        clipkit.PIECE = 0.7  # in several pieces, joined last first
        try:
            task = clipkit.reverse("r", web, 0.5, 2.0)
            while frames.tasks[task["id"]]["state"] == "running":
                time.sleep(0.1)
        finally:
            clipkit.PIECE = was
        done = frames.tasks[task["id"]]
        self.assertEqual(done["state"], "done", done.get("text"))
        out = projects.media_path("r", done["web"])
        info = media.probe(out)
        self.assertAlmostEqual(info["duration"], 2.0, delta=0.1)
        self.assertTrue(info["audio"])
        for t, want in ((0.1, (0, 0, 255)), (0.6, (0, 128, 0)), (1.4, (0, 128, 0)), (1.8, (255, 0, 0))):
            got = pixel(out, t, 32, 32)
            self.assertTrue(all(abs(g - w) <= 30 for g, w in zip(got, want)), (t, got, want))

    def test_old_project_is_converted(self):
        T = projects.TICKS
        fc = {"project": {"items": {
            "1": {"kind": {"Media": {"media": {"File": {"path": "/files/" + self.w["red"]}}}}},
            "2": {"kind": {"Sequence": {"settings": {"width": 992, "height": 1600, "frame_rate": {"num": 32, "den": 1}},
                                        "video_tracks": [{"items": [{"item": 1, "start": T, "duration": 2 * T,
                                                                     "source_in": T // 2, "speed": 1.0}]}],
                                        "audio_tracks": [{"items": []}]}}}}}}
        path = os.path.join(TMP, "old.fcproj")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(fc, f)
        doc = projects.from_filmcraft(path)
        self.assertEqual(doc["settings"]["width"], 992)
        c = doc["clips"][0]
        self.assertEqual((c["media"], c["start"], c["duration"], c["in"]), (self.w["red"], 1.0, 2.0, 0.5))


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


class ExportOptions(unittest.TestCase):
    """Where an export goes and which finishing steps it asks for (the steps themselves need the graphics card)."""

    def test_folder_name_and_never_over_a_file(self):
        folder = os.path.join(TMP, "chosen folder")
        a = render._target("p", "h264", {"folder": folder, "filename": "My film.mp4"})
        self.assertEqual(a, os.path.join(folder, "My film.mp4"))
        open(a, "w").close()
        self.assertEqual(render._target("p", "h264", {"folder": folder, "filename": "My film"}),
                         os.path.join(folder, "My film (2).mp4"))
        self.assertTrue(render._target("p", "prores", {"folder": folder}).endswith(".mov"))
        self.assertTrue(render._target("p", "prores", {"folder": folder, "hdr": {"tone": "vivid"}}).endswith(".mp4"))
        with self.assertRaises(ValueError):
            render._target("p", "h264", {"folder": "relative\folder"})

    def test_finishing_steps(self):
        self.assertEqual(render.finishing({}), {})
        self.assertEqual(render.finishing({"fps_x": 1, "upscale": {"scale": 1}}), {})
        steps = render.finishing({"fps_x": 2, "upscale": {"scale": 9}, "hdr": {"tone": "vivid", "junk": 1}})
        self.assertEqual((steps["fps_x"], steps["interpolator"], steps["upscale"]["scale"], steps["upscale"]["engine"]),
                         (2, "rife", 4.0, "rtx"))
        self.assertEqual(steps["hdr"], {"tone": "vivid"})


if __name__ == "__main__":
    unittest.main()

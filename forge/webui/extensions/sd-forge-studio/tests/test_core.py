"""Forge Studio without Forge: the library index, projects and a real export through FilmCraft's engine.

Run from the extension folder with Forge's Python:  ..\\..\\venv\\Scripts\\python.exe -m unittest tests.test_core

"""

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
TMP = tempfile.mkdtemp(prefix="forge-studio-test-")
os.environ["FORGE_STUDIO_ROOT"] = os.path.join(TMP, "data")
os.environ["FORGE_STUDIO_LIBRARY"] = os.path.join(TMP, "outputs")
os.environ["FORGE_STUDIO_EXPORTS"] = os.path.join(TMP, "exports")

from forgestudio import library, projects  # noqa: E402

FFMPEG = library.ffmpeg()


def make_video(path, seconds=2, size="320x568", color="red"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c={color}:s={size}:r=16:d={seconds}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", path], check=True)


def page_project(media_names, seconds=2):
    """A FilmCraft project file as the page saves it: media as /files/<name>, one sequence."""
    items, children = {}, []
    for i, name in enumerate(media_names, start=1):
        items[str(i)] = {"id": i, "name": name, "kind": {"Media": {"media": {"File": {"path": f"/files/{name}"}}}}}
        children.append({"Item": i})
    return {"format": "filmcraft.project", "schema_version": 15, "project": {"items": items, "root": {"children": children}}}


class Library(unittest.TestCase):
    def test_scan_finds_new_files_and_forgets_removed_ones(self):
        out = os.environ["FORGE_STUDIO_LIBRARY"]
        make_video(os.path.join(out, "video-studio", "2026-10-27", "a.mp4"))
        from PIL import Image
        os.makedirs(os.path.join(out, "txt2img-images"), exist_ok=True)
        Image.new("RGB", (64, 96), "blue").save(os.path.join(out, "txt2img-images", "b.png"),
                                               pnginfo=_pnginfo("a red fox\nSteps: 8, Seed: 1234"))
        library.scan()
        names = {i["name"]: i for i in library.query()}
        self.assertIn("a.mp4", names)
        self.assertEqual(names["a.mp4"]["source"], "video-studio")
        self.assertEqual(names["b.png"]["kind"], "image")
        info = library.info(names["b.png"]["id"])
        self.assertEqual(info["made"]["seed"], 1234)
        self.assertEqual(info["made"]["prompt"], "a red fox")
        self.assertTrue(os.path.isfile(library.thumbnail(names["a.mp4"]["id"])))
        self.assertTrue(os.path.isfile(library.thumbnail(names["b.png"]["id"])))
        time.sleep(0.05)
        os.remove(os.path.join(out, "txt2img-images", "b.png"))
        library.scan()
        self.assertNotIn("b.png", {i["name"] for i in library.query()})
        self.assertEqual([i["name"] for i in library.query(kind="video", text="video-studio")], ["a.mp4"])


def _pnginfo(text):
    from PIL import PngImagePlugin
    info = PngImagePlugin.PngInfo()
    info.add_text("parameters", text)
    return info


class Projects(unittest.TestCase):
    def test_names_media_saves_and_keeps_history(self):
        name = "סרט בדיקה"  # Hebrew names must work end to end
        projects.create(name)
        src = os.path.join(TMP, "src")
        a, b = os.path.join(src, "one", "clip.mp4"), os.path.join(src, "two", "clip.mp4")
        make_video(a)
        make_video(b, color="green")
        self.assertEqual(projects.add_media(name, a), "clip.mp4")
        self.assertEqual(projects.add_media(name, b), "clip (2).mp4")
        self.assertEqual(projects.add_media(name, a), "clip.mp4")  # the same file keeps its name
        doc = projects.load(name)["doc"]
        doc["clips"].append({"id": "c1", "track": "t1", "kind": "video", "media": "clip.mp4", "start": 0, "duration": 1})
        self.assertTrue(projects.save_doc(name, doc)["changed"])
        self.assertFalse(projects.save_doc(name, doc)["changed"])
        doc["clips"][0]["duration"] = 2
        projects.save_doc(name, doc)
        self.assertEqual(projects.load(name)["doc"]["clips"][0]["duration"], 2)
        self.assertEqual(len(os.listdir(os.path.join(projects._folder(name), "history"))), 1)
        with self.assertRaises(projects.ProjectError):
            projects.create("a/b")
        with self.assertRaises(projects.ProjectError):
            projects.save_doc(name, {"format": "something else"})


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()

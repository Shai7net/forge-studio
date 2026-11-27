"""The projects window's server side: covers, lengths, sizes, and the trash (move there and back, files kept).

Run from the extension folder with Forge's Python:  ..\\..\\venv\\Scripts\\python.exe tests\\test_projecthome.py
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
TMP = tempfile.mkdtemp(prefix="forge-studio-home-")
os.environ["FORGE_STUDIO_ROOT"] = os.path.join(TMP, "data")
os.environ["FORGE_STUDIO_LIBRARY"] = os.path.join(TMP, "outputs")
os.environ["FORGE_STUDIO_EXPORTS"] = os.path.join(TMP, "exports")

from forgestudio import paths, projecthome, projects  # noqa: E402


class ProjectHome(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TMP, ignore_errors=True)

    def make(self, name):
        from PIL import Image
        projects.create(name)
        outside = os.path.join(TMP, "outside-" + name + ".png")
        Image.new("RGB", (64, 64), (200, 30, 30)).save(outside)
        web = projects.store_copy(name, outside)      # a copy inside the project folder
        web2 = projects.add_media(name, outside)      # the same file where it is
        doc = projects.empty_doc(1080, 1920, 30)
        doc["tracks"] = [{"id": "v1", "kind": "video", "main": True}, {"id": "v2", "kind": "video"}]
        doc["clips"] = [{"id": "t", "track": "v2", "kind": "text", "start": 0, "duration": 2},
                        {"id": "a", "track": "v2", "kind": "image", "media": web2, "start": 0, "duration": 3},
                        {"id": "b", "track": "v1", "kind": "image", "media": web, "start": 1, "duration": 6.5}]
        projects.save_doc(name, doc)
        return web, outside

    def test_overview_and_trash_round_trip(self):
        web, outside = self.make("Home one")
        p = next(x for x in projecthome.overview() if x["name"] == "Home one")
        self.assertEqual(p["cover"], web)                     # the main track's picture, not the layer above
        self.assertAlmostEqual(p["duration"], 7.5)
        self.assertEqual((p["width"], p["height"]), (1080, 1920))
        self.assertGreaterEqual(p["size"], 2 * os.path.getsize(outside))   # its folder and the file it points to
        self.assertTrue(os.path.isfile(projecthome.cover_path("Home one")))

        projecthome.trash("Home one")
        self.assertNotIn("Home one", [x["name"] for x in projects.list_projects()])
        t = projecthome.trashed()
        self.assertEqual([x["was"] for x in t], ["Home one"])
        self.assertTrue(os.path.isfile(projecthome.cover_path(t[0]["name"], True)))   # the copy moved with it

        # a new project took the name meanwhile: the restored one gets a free name, and its own files still open
        projects.create("Home one")
        back = projecthome.restore(t[0]["name"])["name"]
        self.assertEqual(back, "Home one (2)")
        self.assertEqual(projecthome.trashed(), [])
        self.assertTrue(os.path.isfile(projects.media_path(back, web)))
        self.assertFalse(os.path.exists(os.path.join(paths.projects_dir(), back, "trashed.json")))
        with open(os.path.join(paths.projects_dir(), back, "studio.json"), encoding="utf-8") as f:
            self.assertIn(os.path.join(paths.projects_dir(), back), json.load(f)["media"][web]["path"])

    def test_restore_refuses_paths_outside_the_trash(self):
        with self.assertRaises(projects.ProjectError):
            projecthome.restore("..")
        with self.assertRaises(projects.ProjectError):
            projecthome.cover_path("..\\projects", True)


if __name__ == "__main__":
    unittest.main()

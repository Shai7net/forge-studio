"""Characters: the library and the sheet prompt (no model, no Forge).

Run: webui\\venv\\Scripts\\python.exe -m unittest discover -s extensions\\sd-forge-characters\\tests
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from forgecharacters import library, sheet, writer  # noqa: E402

CHARACTER = {"name": "Luna", "summary": "A cheerful skater.", "style": "Premium stylized 3D animation",
             "appearance": 'A young woman in a "mustard" beanie and a denim jacket', "avoid": "text"}


class Library(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.pic = os.path.join(self.root, "_in.png")
        Image.new("RGB", (32, 32), "red").save(self.pic)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_save_list_rename_move_and_trash(self):
        a = library.save(self.root, "Film A", "Luna", self.pic, {"character": CHARACTER}, template=self.pic)
        b = library.save(self.root, "Film A", "Luna", self.pic, {"character": CHARACTER})
        self.assertEqual(a, "Film A/Luna")
        self.assertEqual(b, "Film A/Luna (2)")  # same name: a second folder, nothing overwritten
        self.assertEqual(library.projects(self.root), ["Film A"])
        self.assertTrue(library.load(self.root, a)["template_path"])
        self.assertIsNone(library.load(self.root, b)["template_path"])
        moved = library.update(self.root, b, "Marcus", "Film B")
        self.assertEqual(moved, "Film B/Marcus")
        self.assertEqual(library.load(self.root, moved)["name"], "Marcus")
        self.assertEqual(len(library.characters(self.root)), 2)  # all projects
        self.assertEqual([c["id"] for c in library.characters(self.root, "Film B")], ["Film B/Marcus"])
        self.assertEqual([c["id"] for c in library.characters(self.root, None, "marc")], ["Film B/Marcus"])
        where = library.trash(self.root, moved)
        self.assertTrue(os.path.isfile(os.path.join(where, library.SHEET_FILE)))  # moved, not erased
        self.assertEqual(library.projects(self.root), ["Film A"])  # the emptied project folder went away

    def test_names_are_safe_folders_and_hebrew_stays(self):
        self.assertEqual(library.clean_name('לונה: "הסקייטרית"?'), "לונה הסקייטרית")
        self.assertEqual(library.clean_name("  ..  "), "character")
        self.assertEqual(library.clean_name("_trash"), "character")  # the library's own folders are not names


class KeepInSimulation360(unittest.TestCase):
    """The 🌍 hand-off writes a kept object Simulation 360's "My objects" can stand (with a stand-in for it)."""

    def test_kept_object_and_reuse(self):
        import types
        from forgecharacters import model3d
        root = tempfile.mkdtemp()
        kept_root = os.path.join(root, "_library")
        os.makedirs(kept_root)
        pic = os.path.join(root, "_in.png")
        Image.new("RGBA", (40, 80), (200, 100, 50, 255)).save(pic)
        fake = {"kept": types.SimpleNamespace(folder=lambda: kept_root),
                "objects": types.SimpleNamespace(_box=lambda alpha: [0, 0, 1, 1])}
        old_sim, old_root = model3d.sim360, model3d._root
        model3d.sim360, model3d._root = (lambda: fake), (lambda: root)
        try:
            cid = library.save(root, "Film", "Luna", pic, {"character": CHARACTER})
            with self.assertRaises(model3d.ModelError):
                model3d.keep_in_sim360(cid)  # no model yet
            folder = library.load(root, cid)["folder"]
            shutil.copyfile(pic, os.path.join(folder, model3d.PICTURE_FILE))
            open(os.path.join(folder, model3d.MODEL_FILE), "wb").write(b"glTF....")
            data = json.load(open(os.path.join(folder, library.CHARACTER_FILE), encoding="utf-8"))
            data["model3d"] = {"file": model3d.MODEL_FILE, "picture": model3d.PICTURE_FILE, "facts": {"triangles": 3}}
            library._write(folder, data)
            kept = model3d.keep_in_sim360(cid)
            described = json.load(open(os.path.join(kept_root, kept["id"], "object.json"), encoding="utf-8"))
            self.assertEqual(described["name"], "Luna")
            self.assertEqual((described["width"], described["height"]), (40, 80))
            self.assertTrue(os.path.isfile(os.path.join(kept_root, kept["id"], described["model"])))
            self.assertEqual(described["body"], {"kind": "biped"})
            self.assertEqual(model3d.keep_in_sim360(cid)["id"], kept["id"])  # sent again: the same kept object
        finally:
            model3d.sim360, model3d._root = old_sim, old_root
            shutil.rmtree(root, ignore_errors=True)


class SheetPrompt(unittest.TestCase):
    def test_compose_is_valid_json_with_the_character_in_it(self):
        text = sheet.compose(CHARACTER)
        value = json.loads(text)
        self.assertIn('"mustard" beanie and a denim jacket.', value["prompt"])
        self.assertIn("Premium stylized 3D animation", value["prompt"])
        self.assertEqual(value["negative_prompt"], "text")
        self.assertNotIn("second reference picture", value["prompt"])
        self.assertIn("second reference picture", json.loads(sheet.compose(CHARACTER, reference=True))["prompt"])

    def test_defaults_fill_a_bare_character(self):
        value = json.loads(sheet.compose({"appearance": "A tall old wizard"}))
        self.assertEqual(value["style"], sheet.DEFAULT_STYLE)
        self.assertEqual(value["negative_prompt"], sheet.DEFAULT_AVOID)

    def test_parse_character(self):
        self.assertEqual(sheet.parse_character(json.dumps(CHARACTER))["name"], "Luna")
        self.assertEqual(sheet.parse_character("```json\n" + json.dumps(CHARACTER) + "\n```")["avoid"], "text")
        self.assertIsNone(sheet.parse_character("בחור עם כובע אדום"))
        self.assertIsNone(sheet.parse_character('{"name": "no appearance"}'))

    def test_is_english(self):
        self.assertTrue(sheet.is_english("She skates, laughs. Café!"))
        self.assertFalse(sheet.is_english("היא גולשת בסקייטבורד"))
        self.assertFalse(sheet.is_english("Она катается"))

    def test_fit16_keeps_the_template_shape(self):
        w, h = sheet.fit16(1024, 1032)
        self.assertEqual((w % 16, h % 16), (0, 0))
        self.assertAlmostEqual(w / h, 1024 / 1032, delta=0.02)

    def test_video_request_names_picture_1(self):
        text = writer.video_request("היא קופצת", CHARACTER)
        self.assertTrue(text.startswith("היא קופצת"))
        self.assertIn("Picture 1 is Luna", text)


if __name__ == "__main__":
    unittest.main()

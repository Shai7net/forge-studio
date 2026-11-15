import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from uiskins import store  # noqa: E402

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
       b"\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82")


class Store(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        store.ROOT = self.tmp

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_classic_is_never_written_and_names_are_cleaned(self):
        self.assertEqual(store.load("Classic"), {"name": "Classic", "rules": {}})
        with self.assertRaises(store.SkinError):
            store.save("classic", {})
        self.assertEqual(store.clean_name(' My: "skin"/ '), "My skin")
        self.assertEqual(store.names(), ["Classic"])

    def test_rules_keep_only_style_fields(self):
        rules = store.clean_rules({"#txt2img_generate": {"bg": "#ff0000", "evil": "x", "bold": True, "text": ""},
                                   "card:ReActor": {}, "": {"bg": "#000"}})
        self.assertEqual(rules, {"#txt2img_generate": {"bg": "#ff0000", "bold": True}})

    def test_a_picture_from_classic_moves_into_the_skin_saved_as(self):
        file = store.store_image("", "my button.png", PNG)
        self.assertTrue(os.path.isfile(store.image_path("Classic", file)))
        rules = {"#txt2img_generate": {"image": file, "color": "#ffffff"}}
        store.save("Neon", rules)
        store.adopt_drafts("Neon", rules)
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "Neon", file)))
        self.assertEqual(store.image_path("Neon", file), os.path.join(self.tmp, "Neon", file))
        # Save as from Neon: the new skin gets the picture too
        store.save("Neon 2", rules, copy_from="Neon")
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "Neon 2", file)))
        self.assertEqual([s["name"] for s in store.listing()], ["Neon", "Neon 2"])

    def test_rename_delete_to_trash_export_and_import(self):
        file = store.store_image("Sun", "sun.png", PNG)
        store.save("Sun", {"tab:tabs:txt2img": {"color": "#00ff00"}, "#x": {"image": file}})
        self.assertEqual(store.rename("Sun", "Sunny"), "Sunny")
        shared = store.export("Sunny")
        self.assertIn(file, shared["pictures"])
        name = store.import_skin(shared)
        self.assertEqual(name, "Sunny (2)")
        self.assertEqual(store.load(name)["rules"]["tab:tabs:txt2img"], {"color": "#00ff00"})
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, name, file)))
        moved = store.delete("Sunny")
        self.assertTrue(os.path.isdir(moved) and "_trash" in moved)
        with self.assertRaises(store.SkinError):
            store.import_skin({"name": "x"})
        with self.assertRaises(store.SkinError):
            store.store_image("", "script.js", b"alert(1)")

    def test_layout_is_kept_apart(self):
        saved = store.save_layout({"txt2img": {"order": ["card:ReActor", "#controlnet"], "more": ["card:FreeU Integrated"]},
                                   "bad": "x"})
        self.assertEqual(store.layout(), saved)
        self.assertEqual(saved["txt2img"]["more"], ["card:FreeU Integrated"])
        self.assertNotIn("bad", saved)


if __name__ == "__main__":
    unittest.main()

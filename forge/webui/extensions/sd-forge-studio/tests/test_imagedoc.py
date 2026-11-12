"""Image shots (imagedoc.py): a new shot, a shot from a picture, saving layers and the flattened picture, the old
flattened pictures going when no clip shows them.

Run from the extension folder:  ..\\..\\venv\\Scripts\\python.exe tests\\test_imagedoc.py
"""

import io
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
TMP = tempfile.mkdtemp(prefix="forge-studio-imagedoc-")
os.environ["FORGE_STUDIO_ROOT"] = os.path.join(TMP, "data")
os.environ["FORGE_STUDIO_LIBRARY"] = os.path.join(TMP, "lib")

from PIL import Image  # noqa: E402

from forgestudio import imagedoc, projects  # noqa: E402


def png(im):
    b = io.BytesIO()
    im.save(b, "PNG")
    return b.getvalue()


class ImageDoc(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        projects.create("i")

    def test_new_blank_shot(self):
        doc = imagedoc.new("i", 320, 200, "#ff0000")
        self.assertEqual((doc["width"], doc["height"], len(doc["layers"])), (320, 200, 1))
        with Image.open(imagedoc.file_path("i", doc["id"], doc["layers"][0]["file"])) as im:
            self.assertEqual(im.size, (320, 200))
            self.assertEqual(im.convert("RGBA").getpixel((10, 10)), (255, 0, 0, 255))
        clear = imagedoc.new("i", 64, 64, "transparent")
        with Image.open(imagedoc.file_path("i", clear["id"], clear["layers"][0]["file"])) as im:
            self.assertEqual(im.convert("RGBA").getpixel((5, 5))[3], 0)
        with self.assertRaises(ValueError):
            imagedoc.new("i", 8, 8)

    def test_shot_from_a_picture_is_fitted_and_centred(self):
        src = os.path.join(TMP, "wide.png")
        Image.new("RGB", (400, 100), (0, 0, 255)).save(src)
        web = projects.add_media("i", src)
        doc = imagedoc.new("i", 200, 200, picture=web)
        with Image.open(imagedoc.file_path("i", doc["id"], doc["layers"][0]["file"])) as im:
            im = im.convert("RGBA")
            self.assertEqual(im.getpixel((100, 100)), (0, 0, 255, 255))   # the middle: the picture
            self.assertEqual(im.getpixel((100, 10))[3], 0)                 # above it: see-through
        self.assertEqual(doc["layers"][0]["name"], "wide")

    def test_save_layers_flat_and_forget_old_flats(self):
        doc = imagedoc.new("i", 100, 80)
        shot, base = doc["id"], doc["layers"][0]
        top = Image.new("RGBA", (100, 80), (0, 0, 0, 0))
        top.paste((0, 255, 0, 255), (10, 10, 30, 30))
        flat = Image.new("RGBA", (100, 80), (255, 255, 255, 255))
        meta = {"width": 100, "height": 80, "layers": [
            {"id": base["id"], "name": "Background", "visible": True, "opacity": 1, "blend": "normal", "x": 0, "y": 0},
            {"id": "ltop0001", "name": "Top", "visible": True, "opacity": 0.5, "blend": "multiply", "x": 5, "y": -3}]}
        first = imagedoc.save("i", shot, meta, {"ltop0001": png(top)}, png(flat))
        self.assertEqual([l["id"] for l in first["doc"]["layers"]], [base["id"], "ltop0001"])
        self.assertEqual(first["doc"]["layers"][1]["blend"], "multiply")
        self.assertEqual((first["doc"]["layers"][1]["x"], first["doc"]["layers"][1]["y"]), (5, -3))
        self.assertTrue(first["web"].startswith(f"shot-{shot}-1"))
        # a layer whose pixels change gets a new file; the old one goes
        old_file = first["doc"]["layers"][1]["file"]
        second = imagedoc.save("i", shot, meta, {"ltop0001": png(top)}, png(flat))
        new_file = second["doc"]["layers"][1]["file"]
        self.assertNotEqual(old_file, new_file)
        self.assertFalse(os.path.exists(os.path.join(imagedoc._dir("i", shot), old_file)))
        # the earlier flattened picture stays until no clip shows it
        self.assertEqual(imagedoc.forget_old_flats("i", shot, {first["web"], second["web"]}), [])
        gone = imagedoc.forget_old_flats("i", shot, {second["web"]})
        self.assertEqual(gone, [first["web"]])
        with self.assertRaises(projects.ProjectError):
            projects.media_path("i", first["web"])
        self.assertTrue(os.path.isfile(projects.media_path("i", second["web"])))
        # a layer without pixels, or of the wrong size, is refused
        with self.assertRaises(ValueError):
            imagedoc.save("i", shot, dict(meta, layers=meta["layers"] + [{"id": "lnew0001", "name": "x"}]), {}, png(flat))
        with self.assertRaises(ValueError):
            imagedoc.save("i", shot, meta, {"ltop0001": png(Image.new("RGBA", (10, 10)))}, png(flat))

    def test_names_are_checked(self):
        with self.assertRaises(ValueError):
            imagedoc.load("i", "../x")
        doc = imagedoc.new("i", 32, 32)
        with self.assertRaises(ValueError):
            imagedoc.file_path("i", doc["id"], "../doc.json")
        self.assertTrue(imagedoc.file_path("i", doc["id"], doc["layers"][0]["file"]).endswith(".png"))


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()

"""Projects on disk: saving, versions, copies. Runs without Forge:

    venv\\Scripts\\python.exe extensions\\sd-forge-simulation-360\\tests\\test_store.py
"""
import os
import sys
import tempfile
import time
import types
import unittest
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

# the two things store.py asks Forge for: where the webui is, and its settings
ROOT = tempfile.mkdtemp(prefix="sim360-test-")
modules = types.ModuleType("modules")
modules.paths = types.SimpleNamespace(data_path=ROOT)
modules.shared = types.SimpleNamespace(opts=types.SimpleNamespace(data={}))
sys.modules["modules"] = modules
sys.modules["modules.paths"] = modules.paths
sys.modules["modules.shared"] = modules.shared

from sim360 import store  # noqa: E402


def world(name, file="pano-0001.png"):
    return {"id": "w" + name.lower(), "name": name, "kind": "image", "file": file, "portals": [], "objects": []}


class Projects(unittest.TestCase):
    def setUp(self):
        self.name = f"Test {time.time_ns()}"
        store.create_project(self.name)
        self.folder = store.project_dir(self.name)

    def put(self, world_id, name, data=b"picture"):
        folder = store.world_dir(self.name, world_id)
        with open(os.path.join(folder, name), "wb") as file:
            file.write(data)

    def test_names_cannot_leave_the_folder(self):
        for bad in ["", "..", "_hidden", "a/b", "x\\y", "c:evil"]:
            if store._FORBIDDEN.sub(" ", bad).strip().strip(".").strip().startswith("_") or not store._FORBIDDEN.sub(" ", bad).strip().strip("."):
                with self.assertRaises(store.StoreError):
                    store.clean_name(bad)
            else:
                self.assertNotIn("/", store.clean_name(bad))
                self.assertNotIn("\\", store.clean_name(bad))
        self.put("wa", "pano-0001.png")
        self.assertTrue(store.resolve(self.name, "worlds/wa/pano-0001.png").endswith("pano-0001.png"))
        with self.assertRaises(store.StoreError):
            store.resolve(self.name, "../../config.json")
        with self.assertRaises(store.StoreError):
            store.clean_id("../x")

    def test_saving_keeps_the_state_before_it_as_a_version(self):
        doc = store.load_project(self.name)
        doc["worlds"] = [world("A")]
        store.save_project(self.name, doc)                       # replaces the empty project: that one is kept
        versions = store.list_versions(self.name)
        self.assertEqual(len(versions), 1)
        self.assertTrue(versions[0]["auto"])
        self.assertEqual(versions[0]["worlds"], 0)
        doc["worlds"].append(world("B"))
        store.save_project(self.name, doc)                       # seconds later: no second automatic version
        self.assertEqual(len(store.list_versions(self.name)), 1)
        self.assertEqual(len(store.load_project(self.name)["worlds"]), 2)

    def test_a_kept_version_comes_back_whole(self):
        doc = store.load_project(self.name)
        doc["worlds"] = [world("A")]
        store.save_project(self.name, doc)
        kept = store.save_version(self.name, "one world")
        doc["worlds"] = [world("A", "pano-0002.png"), world("B")]
        store.save_project(self.name, doc)
        restored = store.restore_version(self.name, kept)
        self.assertEqual([w["file"] for w in restored["worlds"]], ["pano-0001.png"])
        self.assertNotIn("version_label", restored)
        now = store.load_project(self.name)
        self.assertEqual(len(now["worlds"]), 1)
        labels = {v["file"].split("-")[0]: v for v in store.list_versions(self.name)}
        self.assertIn("before", labels)                          # what was replaced can be brought back too
        self.assertEqual(labels["before"]["worlds"], 2)
        self.assertEqual(labels["saved"]["label"], "one world")
        with self.assertRaises(store.StoreError):
            store.restore_version(self.name, "nothing.json")

    def test_a_copy_and_a_packed_project_hold_everything_but_recordings(self):
        doc = store.load_project(self.name)
        doc["worlds"] = [world("A")]
        store.save_project(self.name, doc)
        self.put("wa", "pano-0001.png", b"x" * 1000)
        os.makedirs(os.path.join(self.folder, "exports"), exist_ok=True)
        with open(os.path.join(self.folder, "exports", "tour.mp4"), "wb") as file:
            file.write(b"v" * 5000)
        self.assertGreater(store.usage(self.name)["bytes"], 6000)
        # recordings and packed projects of every project go to one folder, next to Forge's other outputs
        self.assertEqual(store.exports_dir(self.name), os.path.join(ROOT, "output", "simulation"))

        copy = store.duplicate_project(self.name, self.name + " copy")
        self.assertEqual(store.load_project(copy)["name"], copy)
        self.assertTrue(os.path.isfile(store.resolve(copy, "worlds/wa/pano-0001.png")))
        self.assertFalse(os.path.exists(os.path.join(store.project_dir(copy), "exports", "tour.mp4")))
        with self.assertRaises(store.StoreError):
            store.duplicate_project(self.name, copy)

        packed = store.pack_project(self.name)
        self.assertEqual(os.path.dirname(packed), os.path.join(ROOT, "output", "simulation"))
        with zipfile.ZipFile(packed) as archive:
            names = set(archive.namelist())
        self.assertIn("project.json", names)
        self.assertIn(os.path.normpath("worlds/wa/pano-0001.png"), {os.path.normpath(n) for n in names})
        self.assertFalse(any("tour.mp4" in n for n in names))

        opened = store.unpack_project(packed, self.name)         # the name is taken: it gets the next one
        self.assertNotEqual(opened, self.name)
        self.assertEqual(store.load_project(opened)["worlds"][0]["name"], "A")
        self.assertTrue(os.path.isfile(store.resolve(opened, "worlds/wa/pano-0001.png")))

    def test_a_packed_file_cannot_write_outside_its_project(self):
        evil = os.path.join(ROOT, "evil.zip")
        with zipfile.ZipFile(evil, "w") as archive:
            archive.writestr("project.json", '{"worlds": []}')
            archive.writestr("../../escaped.txt", "no")
            archive.writestr("worlds/../../escaped2.txt", "no")
        opened = store.unpack_project(evil, "Evil")
        base = os.path.realpath(store.root())
        for found_base, _dirs, files in os.walk(os.path.dirname(base)):
            for name in files:
                if name.startswith("escaped"):
                    self.assertTrue(os.path.realpath(found_base).startswith(os.path.realpath(store.project_dir(opened))), found_base)
        with self.assertRaises(store.StoreError):
            with open(os.path.join(ROOT, "not.zip"), "wb") as file:
                file.write(b"hello")
            store.unpack_project(os.path.join(ROOT, "not.zip"), "Nope")
        plain = os.path.join(ROOT, "plain.zip")
        with zipfile.ZipFile(plain, "w") as archive:
            archive.writestr("readme.txt", "not a project")
        with self.assertRaises(store.StoreError):
            store.unpack_project(plain, "Plain")

    def test_deleting_moves_aside(self):
        moved = store.delete_project(self.name)
        self.assertTrue(os.path.isfile(os.path.join(moved, "project.json")))
        with self.assertRaises(store.StoreError):
            store.project_dir(self.name)


class MyObjects(unittest.TestCase):
    """An object is kept from one world and stood in another project's world: its files go with it."""

    def test_an_object_is_kept_used_elsewhere_and_forgotten(self):
        from sim360 import library
        store.create_project("Made here")
        store.create_project("Used there")
        source = store.world_dir("Made here", "w1")
        for name, data in (("object-oaaa-0002.png", b"picture"), ("object-oaaa-0002-depth.png", b"depth"), ("object-oaaa-0003.glb", b"glTF model")):
            with open(os.path.join(source, name), "wb") as file:
                file.write(data)
        described = {"id": "oaaa", "name": "Blue house", "kind": "image", "file": "object-oaaa-0002.png", "depth": "object-oaaa-0002-depth.png", "model": "object-oaaa-0003.glb",
                     "size": 6.5, "box": [0.1, 0.1, 0.9, 1], "body": {"kind": ""}, "pos": [3, 0, -4], "turn": 40, "on_ground": True, "path": [{"t": 0, "pos": [0, 0, 0]}], "history": [{"file": "old.png"}]}
        kept = library.keep("Made here", "w1", described)
        self.assertEqual([(e["id"], e["name"], e["picture"], e["solid"]) for e in library.listed()], [(kept["id"], "Blue house", "object-oaaa-0002.png", True)])
        taken = library.use(kept["id"], "Used there", "w9", "obbb")
        self.assertEqual((taken["file"], taken["depth"], taken["model"]), ("object-obbb-0002.png", "object-obbb-0002-depth.png", "object-obbb-0003.glb"))
        self.assertEqual((taken["name"], taken["size"], taken["body"]), ("Blue house", 6.5, {"kind": ""}))
        for gone in ("id", "pos", "turn", "path", "on_ground", "was"):
            self.assertNotIn(gone, taken)                      # where it stood is not what it is
        self.assertEqual(taken["history"], [])
        with open(os.path.join(store.world_dir("Used there", "w9"), "object-obbb-0003.glb"), "rb") as file:
            self.assertEqual(file.read(), b"glTF model")
        self.assertEqual(os.path.basename(library.file(kept["id"], "object-oaaa-0002.png")), "object-oaaa-0002.png")
        # the kept object does not show as a project, and forgetting it leaves what was made of it
        self.assertEqual(sorted(p["name"] for p in store.list_projects() if p["name"] in ("Made here", "Used there")), ["Made here", "Used there"])
        self.assertNotIn("_library", [p["name"] for p in store.list_projects()])
        self.assertTrue(library.drop(kept["id"]))
        self.assertEqual(library.listed(), [])
        self.assertTrue(os.path.isfile(os.path.join(store.world_dir("Used there", "w9"), "object-obbb-0002.png")))
        with self.assertRaises(store.StoreError):
            library.use(kept["id"], "Used there", "w9", "occc")

    def test_an_object_whose_file_is_gone_is_not_kept_by_halves(self):
        from sim360 import library
        store.create_project("Half")
        store.world_dir("Half", "w1")
        with self.assertRaises(store.StoreError):
            library.keep("Half", "w1", {"id": "oxxx", "name": "ghost", "file": "object-oxxx-0001.png"})
        self.assertEqual([e for e in library.listed() if e["name"] == "ghost"], [])


if __name__ == "__main__":
    try:
        unittest.main(verbosity=1)
    finally:
        import shutil
        shutil.rmtree(ROOT, ignore_errors=True)

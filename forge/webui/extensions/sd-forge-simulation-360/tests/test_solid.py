"""Solid objects: finding Hunyuan3D, how it is asked, and what a model file holds. Runs without Forge and
without Hunyuan3D:

    venv\\Scripts\\python.exe extensions\\sd-forge-simulation-360\\tests\\test_solid.py
"""
import json
import os
import struct
import sys
import tempfile
import types
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

ROOT = tempfile.mkdtemp(prefix="sim360-test-")
OPTIONS = {}
modules = types.ModuleType("modules")
modules.paths = types.SimpleNamespace(data_path=ROOT)
modules.shared = types.SimpleNamespace(opts=types.SimpleNamespace(data=OPTIONS))
sys.modules["modules"] = modules
sys.modules["modules.paths"] = modules.paths
sys.modules["modules.shared"] = modules.shared

from sim360 import solid  # noqa: E402
from sim360.runner import JobError  # noqa: E402


def glb(gltf, binary=b""):
    """A .glb file: the description, then the numbers."""
    text = json.dumps(gltf).encode("utf8")
    text += b" " * (-len(text) % 4)
    binary += b"\0" * (-len(binary) % 4)
    body = struct.pack("<II", len(text), 0x4E4F534A) + text + struct.pack("<II", len(binary), 0x004E4942) + binary
    return struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body


def triangle(**extra):
    corners = np.array([[0, 0, 0], [1, 0, 0], [0, 2, 0]], np.float32).tobytes()
    index = np.array([0, 1, 2], np.uint16).tobytes()
    gltf = {
        "asset": {"version": "2.0"}, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1}]}],
        "accessors": [{"bufferView": 0, "componentType": 5126, "type": "VEC3", "count": 3, "min": [0, 0, 0], "max": [1, 2, 0]},
                      {"bufferView": 1, "componentType": 5123, "type": "SCALAR", "count": 3}],
        "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": 36}, {"buffer": 0, "byteOffset": 36, "byteLength": 6}],
        "buffers": [{"byteLength": 42}],
    }
    gltf.update(extra)
    return glb(gltf, corners + index)


class Files(unittest.TestCase):
    def path(self, data):
        handle, path = tempfile.mkstemp(suffix=".glb", dir=ROOT)
        os.write(handle, data)
        os.close(handle)
        return path

    def test_what_a_model_holds_is_read_from_its_file(self):
        facts = solid.measure(self.path(triangle()))
        self.assertEqual((facts["corners"], facts["triangles"], facts["pictures"], facts["bones"], facts["moves"]), (3, 1, 0, 0, []))
        self.assertEqual(facts["extent"], [1.0, 2.0, 0.0])
        moving = solid.measure(self.path(triangle(skins=[{"joints": [0, 0, 0]}], animations=[{"name": "walk"}, {}], images=[{}])))
        self.assertEqual((moving["bones"], moving["moves"], moving["pictures"]), (3, ["walk", "move 2"], 1))

    def test_a_file_that_is_not_a_model_or_cannot_be_shown_is_refused_in_words(self):
        with self.assertRaisesRegex(JobError, "not a .glb"):
            solid.measure(self.path(b"this is a text file, not a model at all"))
        with self.assertRaisesRegex(JobError, "packed"):
            solid.measure(self.path(triangle(extensionsRequired=["KHR_draco_mesh_compression"])))
        with self.assertRaisesRegex(JobError, "no triangles"):
            solid.measure(self.path(glb({"asset": {"version": "2.0"}, "meshes": []})))


class Pictures(unittest.TestCase):
    def test_a_picture_that_shows_the_thing_twice_is_known(self):
        # (Forge drew two convertibles, one above the other, and Hunyuan3D made one model of both)
        sheet = np.zeros((512, 768), np.uint8)
        sheet[120:200, 100:660] = 255
        self.assertFalse(solid.twice(sheet))                    # one car
        sheet[300:384, 110:650] = 255
        self.assertTrue(solid.twice(sheet))                     # two, alike
        person = np.zeros((768, 512), np.uint8)
        person[40:740, 180:330] = 255
        person[300:340, 400:430] = 255                          # and something small beside it: a bag, a ball
        self.assertFalse(solid.twice(person))
        umbrella = np.zeros((640, 640), np.uint8)
        umbrella[60:300, 120:520] = 255
        umbrella[420:520, 220:420] = 255                        # a chair under it, a different shape: not "twice"
        self.assertFalse(solid.twice(umbrella))


class Asking(unittest.TestCase):
    def test_values_go_in_the_order_this_hunyuan3d_takes_them(self):
        named = {"/generation_all": {"parameters": [
            {"parameter_name": "caption", "parameter_default": None}, {"parameter_name": "image", "parameter_default": None},
            {"parameter_name": "steps", "parameter_default": 5}, {"parameter_name": "something_new", "parameter_default": "its own"},
            {"parameter_name": "seed", "parameter_default": 1234}]}}
        self.assertEqual(solid._arguments(named, "generation_all", {"seed": 7, "image": "picture", "steps": 8}), [None, "picture", 8, "its own", 7])

    def test_the_model_is_found_in_an_answer_however_it_is_wrapped(self):
        bare = {"path": "a.glb", "url": "http://x/a.glb"}
        self.assertEqual(solid._files(bare), [bare])
        self.assertEqual(solid._files({"__type__": "update", "value": bare}), [bare])
        self.assertEqual(solid._files("<div>a page</div>"), [])
        self.assertEqual(solid._files({"__type__": "update", "value": None}), [])
        self.assertEqual(solid._files(1234), [])


class Memory(unittest.TestCase):
    def test_a_model_is_refused_when_the_computer_would_run_out_of_memory(self):
        free, promise = solid.memory_free()
        self.assertGreater(free, 0.0)
        self.assertGreater(promise, 0.0)
        solid.room(draws=True, free=(27.4, 31.5), own=False, elsewhere=False)          # this PC with little else open: it fits
        solid.room(draws=False, free=(6.0, 8.0), own=True, elsewhere=False)            # ours is running already
        solid.room(draws=True, free=(11.9, 8.2), own=True, elsewhere=False)            # the second model of a row: ours holds 20 GB and needs no more
        # 2026-10-12: Forge drew a picture while Pinokio's Hunyuan3D held 30 GB; no promise was left and the PC stood still
        with self.assertRaisesRegex(JobError, "open in Pinokio"):
            solid.room(draws=True, free=(9.5, 6.5), own=False, elsewhere=True)
        solid.room(draws=False, free=(9.5, 6.5), own=False, elsewhere=True)            # of a picture that exists, it still fitted
        with self.assertRaisesRegex(JobError, "Close other programs"):
            solid.room(draws=False, free=(12.0, 30.0), own=False, elsewhere=False)     # no room to start it from here
        with self.assertRaisesRegex(JobError, "8 GB, and about 2[0-9] GB"):
            solid.room(draws=False, free=(30.0, 8.0), own=False, elsewhere=False)      # the machine has room, Windows has no promise left


class Finding(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="sim360-pinokio-")
        self.app = os.path.join(self.home, "api", "Hunyuan3d-2-lowvram.git", "app")
        os.makedirs(os.path.join(self.app, "hy3dgen"))
        open(os.path.join(self.app, "gradio_app.py"), "w").close()
        self.kept = solid._pinokio_homes
        solid._pinokio_homes = lambda: [self.home]

    def tearDown(self):
        solid._pinokio_homes = self.kept
        OPTIONS.clear()

    def test_the_copy_pinokio_installed_is_found_and_settings_can_name_another(self):
        self.assertEqual(solid.folder(), os.path.normpath(self.app))
        elsewhere = tempfile.mkdtemp(prefix="sim360-h3d-")
        os.makedirs(os.path.join(elsewhere, "app", "hy3dgen"))
        open(os.path.join(elsewhere, "app", "gradio_app.py"), "w").close()
        OPTIONS["sim360_hunyuan3d_dir"] = elsewhere                   # the launcher's folder is as good as the program's own
        self.assertEqual(solid.folder(), os.path.normpath(os.path.join(elsewhere, "app")))
        solid._pinokio_homes = lambda: []
        OPTIONS["sim360_hunyuan3d_dir"] = os.path.join(elsewhere, "nothing here")
        self.assertIsNone(solid.folder())
        self.assertEqual(solid.state(), {"available": False})

    def test_it_is_started_as_pinokio_last_started_it_without_its_text_to_picture_part(self):
        self.assertEqual(solid._flags(self.app), ["--h2", "--turbo", "--profile", "4"])           # nothing is known: the usual
        log = os.path.join(self.home, "api", "Hunyuan3d-2-lowvram.git", "logs", "api", "start.js")
        os.makedirs(log)
        with open(os.path.join(log, "latest"), "w", encoding="utf8") as file:
            file.write("D:\\x\\app>conda_hook & activate && python gradio_app.py --mini --turbo --profile 5 --enable_t23d --host 127.0.0.1 --port 42003\nLoading...\n")
        self.assertEqual(solid._flags(self.app), ["--mini", "--turbo", "--profile", "5"])
        OPTIONS["sim360_hunyuan3d_flags"] = "--h2 --profile 3"
        self.assertEqual(solid._flags(self.app), ["--h2", "--profile", "3"])


class Home(unittest.TestCase):
    """Hunyuan3D is taken into Forge's models folder from the copy another program installed."""

    def setUp(self):
        # what Pinokio leaves: the program with its environment inside it, and the models in a cache beside it
        self.there = tempfile.mkdtemp(prefix="sim360-pinokio-", dir=ROOT)
        self.app = os.path.join(self.there, "api", "Hunyuan3d-2-lowvram.git", "app")
        for folder in ("hy3dgen", "assets", os.path.join("env", "Lib", "site-packages", "pretend3d")):
            os.makedirs(os.path.join(self.app, folder))
        for name, text in (("gradio_app.py", "print('the program')"), ("LICENSE", "not for commercial use"), (os.path.join("hy3dgen", "__init__.py"), ""),
                           (os.path.join("assets", "example_prompts.txt"), "a chair"), (os.path.join("env", "Lib", "site-packages", "pretend3d", "__init__.py"), "x = 1")):
            with open(os.path.join(self.app, name), "w", encoding="utf8") as file:
                file.write(text)
        hub = os.path.join(self.there, "api", "Hunyuan3d-2-lowvram.git", "cache", "HF_HOME", "hub")
        shape = os.path.join(hub, "models--tencent--Hunyuan3D-2", "snapshots", "abc123", "hunyuan3d-dit-v2-0-turbo")
        os.makedirs(shape)
        os.makedirs(os.path.join(hub, "models--tencent--Hunyuan3D-2", "refs"))
        with open(os.path.join(hub, "models--tencent--Hunyuan3D-2", "refs", "main"), "w") as file:
            file.write("abc123")
        for name in ("model.fp16.safetensors", "model.fp16.ckpt", "config.yaml"):
            with open(os.path.join(shape, name), "wb") as file:
                file.write(b"weights " * 1000)
        os.makedirs(os.path.join(hub, "models--Tencent-Hunyuan--HunyuanDiT-v1.1-Diffusers-Distilled", "snapshots", "x"))     # its text-to-picture model
        self.kept = solid._pinokio_homes
        solid._pinokio_homes = lambda: [self.there]
        modules.paths.models_path = tempfile.mkdtemp(prefix="sim360-models-", dir=ROOT)

    def tearDown(self):
        solid._pinokio_homes = self.kept
        del modules.paths.models_path
        OPTIONS.clear()

    def test_it_is_linked_in_whole_and_then_it_is_forge_own(self):
        from sim360 import solid_home
        self.assertEqual(solid.where(solid.folder()), "pinokio")
        home = solid.own_home()
        done = solid_home.adopt(self.app, home, ["--h2", "--turbo"], base_python=sys.executable, needs=("pretend3d",))
        self.assertEqual(done["models"], ["tencent/Hunyuan3D-2"])
        self.assertEqual(done["copied"], 0)
        self.assertTrue(solid_home.complete(home))
        taken = os.path.join(home, "models", "tencent", "Hunyuan3D-2", "hunyuan3d-dit-v2-0-turbo")
        self.assertEqual(sorted(os.listdir(taken)), ["config.yaml", "model.fp16.safetensors"])        # the second copy of the weights is left behind
        source = os.path.join(self.there, "api", "Hunyuan3d-2-lowvram.git", "cache", "HF_HOME", "hub", "models--tencent--Hunyuan3D-2",
                              "snapshots", "abc123", "hunyuan3d-dit-v2-0-turbo", "model.fp16.safetensors")
        self.assertTrue(os.path.samefile(source, os.path.join(taken, "model.fp16.safetensors")))      # the same file under a second name
        self.assertTrue(os.path.isfile(os.path.join(home, "env", "Lib", "site-packages", "pretend3d", "__init__.py")))
        with open(os.path.join(home, "app", "LICENSE")) as file:
            self.assertIn("not for commercial use", file.read())
        # from now on it is Forge's own that is found, and started with its own models and its own work folder
        app = solid.folder()
        self.assertEqual((app, solid.where(app)), (os.path.join(home, "app"), "forge"))
        self.assertEqual(solid._flags(app), ["--h2", "--turbo"])
        command, environment = solid._command(app, 4321)
        self.assertTrue(command[0].startswith(os.path.join(home, "env")))
        self.assertEqual(environment["HY3DGEN_MODELS"], os.path.join(home, "models"))
        self.assertEqual(environment["HF_HUB_OFFLINE"], "1")
        self.assertEqual(command[command.index("--cache-path") + 1], os.path.join(home, "work", "made"))
        # the other copy may go: what was taken stays, and is still whole
        import shutil
        shutil.rmtree(self.there)
        with open(os.path.join(taken, "model.fp16.safetensors"), "rb") as file:
            self.assertEqual(len(file.read()), 8000)
        self.assertIsNone(solid_home.check(home, needs=("pretend3d",)))
        self.assertEqual(solid.folder(), os.path.join(home, "app"))

    def test_taking_it_again_changes_nothing_and_a_copy_without_models_is_refused(self):
        from sim360 import solid_home
        home = solid.own_home()
        solid_home.adopt(self.app, home, ["--h2"], base_python=sys.executable, needs=("pretend3d",))
        again = solid_home.adopt(self.app, home, ["--h2"], base_python=sys.executable, needs=("pretend3d",))
        self.assertEqual((again["linked"], again["copied"]), (0, 0))
        self.assertGreater(again["there"], 3)
        import shutil
        shutil.rmtree(os.path.join(self.there, "api", "Hunyuan3d-2-lowvram.git", "cache"))
        with self.assertRaisesRegex(solid_home.HomeError, "no models yet"):
            solid_home.adopt(self.app, tempfile.mkdtemp(dir=ROOT), ["--h2"], base_python=sys.executable, needs=("pretend3d",))

    def test_what_it_wrote_while_it_worked_is_emptied(self):
        work = os.path.join(solid.own_home(), "work")
        for folder in (os.path.join("made", "one-model"), os.path.join("made", "env_maps"), os.path.join("gradio", "an-upload")):
            os.makedirs(os.path.join(work, folder))
        with open(os.path.join(work, "made", "one-model", "textured_mesh.glb"), "wb") as file:
            file.write(b"glTF")
        solid._tidy()
        self.assertEqual(os.listdir(os.path.join(work, "made")), ["env_maps"])
        self.assertEqual(os.listdir(os.path.join(work, "gradio")), [])


PRETEND = '''
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer

class Answers(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"named_endpoints": {"/shape_generation": {"parameters": []}}}).encode()
        self.send_response(200 if self.path == "/gradio_api/info" else 404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass

print("pretending to be Hunyuan3D", sys.argv[1:], flush=True)
if "--broken" in sys.argv:
    raise SystemExit("it cannot start today")
HTTPServer(("127.0.0.1", int(sys.argv[sys.argv.index("--port") + 1])), Answers).serve_forever()
'''


class Starting(unittest.TestCase):
    """The program is started from here, asked, and stopped: with a small program that only pretends."""

    def setUp(self):
        self.app = os.path.join(tempfile.mkdtemp(prefix="sim360-pretend-"), "app")
        os.makedirs(os.path.join(self.app, "hy3dgen"))
        with open(os.path.join(self.app, "gradio_app.py"), "w", encoding="utf8") as file:
            file.write(PRETEND)
        self.kept = solid._python
        solid._python = lambda app: sys.executable
        self.job = types.SimpleNamespace(status="running", stage="", share=None)

    def tearDown(self):
        solid.stop()
        solid._python = self.kept
        OPTIONS.clear()

    def test_it_is_started_answers_and_is_stopped(self):
        import psutil
        OPTIONS["sim360_hunyuan3d_flags"] = "--h2 --turbo"
        base = solid._start(self.app, self.job)
        self.assertEqual(self.job.stage, "starting Hunyuan3D")
        self.assertIsNotNone(solid._answers(base))
        pid = solid._own["process"].pid
        self.assertTrue(psutil.pid_exists(pid))
        with open(solid._log_path(), encoding="utf8") as file:
            self.assertIn("'--h2', '--turbo', '--host', '127.0.0.1'", file.read())
        self.assertTrue(solid.stop())
        self.assertIsNone(solid._own)
        self.assertFalse(solid.stop())                              # nothing left to stop
        self.assertFalse(psutil.pid_exists(pid) and psutil.Process(pid).is_running() and psutil.Process(pid).status() != psutil.STATUS_ZOMBIE)

    def test_it_looks_for_its_models_where_pinokio_put_them_not_where_forge_keeps_its_own(self):
        # (Forge sets these for itself; inherited, they sent Hunyuan3D to the wrong folder and it could not start)
        hub = os.path.join(os.path.dirname(self.app), "cache", "HF_HOME", "hub")
        os.makedirs(hub)
        os.environ.update(HF_HUB_CACHE="X:\\forge\\diffusers", HUGGINGFACE_HUB_CACHE="X:\\forge\\diffusers", TRANSFORMERS_CACHE="X:\\forge\\diffusers")
        try:
            command, environment = solid._command(self.app, 4321)
        finally:
            for name in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE", "TRANSFORMERS_CACHE"):
                del os.environ[name]
        self.assertEqual(environment["HF_HOME"], os.path.dirname(hub))
        self.assertEqual(environment["HF_HUB_OFFLINE"], "1")
        for name in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE", "TRANSFORMERS_CACHE"):
            self.assertNotIn(name, environment)
        self.assertEqual(command[-4:], ["--host", "127.0.0.1", "--port", "4321"])
        self.assertNotIn("--enable_t23d", command)

    def test_one_that_cannot_start_says_why_and_is_not_left_behind(self):
        OPTIONS["sim360_hunyuan3d_flags"] = "--broken"
        with self.assertRaisesRegex(JobError, "could not start.*it cannot start today"):
            solid._start(self.app, self.job)
        self.assertIsNone(solid._own)

    def test_a_start_that_is_cancelled_leaves_nothing_running(self):
        self.job.status = "cancelling"
        with self.assertRaisesRegex(JobError, "cancelled"):
            solid._start(self.app, self.job)
        self.assertIsNone(solid._own)


class RealSteps(unittest.TestCase):
    """AI4Animation's folder counts only when all it needs is there; the job refuses a walk without a path."""

    def test_the_folder_must_be_whole(self):
        import tempfile
        from sim360 import motion
        home = tempfile.mkdtemp(prefix="sim360-ai4a-")
        modules.shared.opts.data["sim360_ai4animation_dir"] = home
        try:
            self.assertIsNone(motion.home())
            for part in (("env", "Scripts", "python.exe"), ("ai4animation", "AI4Animation.py"), ("Demos", "Locomotion", "Biped", "Network.pt"),
                         ("Demos", "Locomotion", "Biped", "Program.py"), ("Demos", "_ASSETS_", "Geno", "Model.glb")):
                os.makedirs(os.path.join(home, *part[:-1]), exist_ok=True)
                open(os.path.join(home, *part), "wb").close()
            self.assertEqual(motion.home(), os.path.abspath(home))
            self.assertTrue(motion.describe()["available"])
            self.assertIn("Zombie", motion.describe()["styles"])
        finally:
            modules.shared.opts.data.pop("sim360_ai4animation_dir", None)


if __name__ == "__main__":
    unittest.main(verbosity=2)

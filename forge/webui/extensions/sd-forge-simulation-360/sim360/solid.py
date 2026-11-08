"""Solid objects: a picture becomes a real 3D model, which stands in a world like any other object.

Hunyuan3D-2 makes the model. It cannot live inside Forge's own program (it needs newer libraries than this
Forge has, and all of the graphics memory), so it is run beside it, as its own program with its own Python:
the copy in Forge's models folder (models\\sim360-hunyuan3d, see solid_home.py), or, until that is made, the
copy another program installed (Pinokio's). It is started from here when a model is asked for, without its
text-to-picture part (Forge draws the pictures), and stopped again when it has not been asked for a while.
If a Hunyuan3D of the same folder is already open (started from Pinokio), that one is used as it is. Either
way it is asked the way its own page asks it.

    object-<id>-0003.glb      the model: a mesh with its picture wrapped around it (glTF, binary)
"""

import json
import os
import re
import socket
import struct
import subprocess
import threading
import time

import numpy as np
from PIL import Image

from . import jobs, objects, runner, solid_home, store
from .runner import JobError

IDLE_MINUTES = 4            # a Hunyuan3D that was started from here is stopped after this long without work:
                            # it holds 15 GB of memory, and starting it again takes under a minute
# Memory, in gigabytes, as (what must be free in the machine, what Windows must still be able to promise).
# Measured on 2026-10-12 with --h2 --turbo --profile 4: started from here, Hunyuan3D held 14.6 and was
# promised 20.5 at its fullest; Forge drawing a picture from cold took 3 and 4 more (less when its checkpoint is
# loaded already). With 29 free and 33 promised, a model out of words left 11 and 7.4 at the lowest.
OWN_NEEDS = (15.0, 21.0)    # a Hunyuan3D that is started from here
OPEN_NEEDS = (3.0, 5.0)     # one that somebody else opened still grows while it works
MINE_NEEDS = (1.0, 2.0)     # one that was started from here and has made a model holds all it will hold
DRAW_NEEDS = (3.0, 3.5)     # Forge, to draw the picture a model is made of
SPARE = 1.0                 # and what is left to the rest of the computer
START_MINUTES = 10          # how long Hunyuan3D may take to start (under a minute here, once its files are in the disk's memory)
ALONE = ", the whole of it from top to bottom, a single subject in the middle of the picture, plain white background, soft even light"
NOT_ALONE = "cropped, cut off, out of frame, many, crowd, scenery, room, street, shadow on the wall, text, watermark"
SIZES = {"portrait": (512, 768), "square": (640, 640), "landscape": (768, 512)}
STEPS = (("Shape generation takes", "simplifying the mesh"), ("Face Reduction takes", "painting it"), ("Texture Generation takes", "saving"))

_lock = threading.Lock()
_own = None                 # the Hunyuan3D that was started from here: {"process", "base", "log"}
_used = 0.0                 # when it was last asked
_keeper = None
_busy = threading.Lock()    # one model at a time
_took = {"shape": 30.0, "all": 210.0}      # how long a model takes on this machine, learned as it goes


# --- where Hunyuan3D is ------------------------------------------------------------------------------------------

def _option(name):
    try:
        from modules import shared
        return str(shared.opts.data.get(name) or "").strip()
    except Exception:
        return ""


def _pinokio_homes():
    homes = []
    config = store.read_json(os.path.join(os.path.expanduser("~"), ".pinokio", "config.json"), {}) or {}
    for home in (config.get("home"), os.environ.get("PINOKIO_HOME")):
        if home and os.path.isdir(home) and home not in homes:
            homes.append(home)
    return homes


def own_home():
    """Where Forge keeps its own Hunyuan3D: sim360-hunyuan3d in the models folder."""
    from modules import paths
    return solid_home.home(getattr(paths, "models_path", None) or os.path.join(paths.data_path, "models"))


def folder():
    """The folder that holds Hunyuan3D's program (gradio_app.py and hy3dgen), or None when it is not installed.
    A folder named in Settings comes first, then Forge's own copy, then one that Pinokio installed."""
    asked = _option("sim360_hunyuan3d_dir")
    candidates = [asked, os.path.join(asked, "app")] if asked else []
    if solid_home.complete(own_home()):
        candidates.append(os.path.join(own_home(), "app"))
    for home in _pinokio_homes():
        api = os.path.join(home, "api")
        if os.path.isdir(api):
            candidates += [os.path.join(api, name, "app") for name in sorted(os.listdir(api)) if "hunyuan3d" in name.lower()]
    for candidate in candidates:
        if os.path.isfile(os.path.join(candidate, "gradio_app.py")) and os.path.isdir(os.path.join(candidate, "hy3dgen")):
            return os.path.normpath(candidate)
    return None


def _python(app):
    # (Pinokio keeps the environment inside the program's folder; Forge's own copy keeps it beside it)
    for root in (app, os.path.dirname(app)):
        for path in (os.path.join(root, "env", "Scripts", "python.exe"), os.path.join(root, "env", "bin", "python")):
            if os.path.isfile(path):
                return path
    return None


def where(app):
    """Whose copy this is: "forge" (Forge's own), "pinokio", or "named" (a folder from Settings)."""
    if app and os.path.normcase(os.path.normpath(os.path.dirname(app))) == os.path.normcase(os.path.normpath(own_home())):
        return "forge"
    return "pinokio" if app and any(os.path.normcase(app).startswith(os.path.normcase(os.path.join(h, "api"))) for h in _pinokio_homes()) else "named"


def _answers(base, wait=4):
    """Whether a Hunyuan3D answers at this address: its list of what it can be asked, or None."""
    import requests
    try:
        info = requests.get(base + "/gradio_api/info", timeout=wait).json()
    except Exception:
        return None
    named = info.get("named_endpoints") or {}
    return named if "/shape_generation" in named else None


def _open_elsewhere(app):
    """A Hunyuan3D of this folder that somebody else started (Pinokio): its address, or None."""
    import psutil
    here = os.path.normcase(app)
    for process in psutil.process_iter(["cmdline"]):
        try:
            command = process.info["cmdline"] or []
            if not any(part.endswith("gradio_app.py") for part in command):
                continue
            if _own and process.pid in _family(_own["process"].pid):
                continue
            if os.path.normcase(os.path.normpath(process.cwd())) != here:
                continue
            port = command[command.index("--port") + 1] if "--port" in command else "8080"
            host = command[command.index("--host") + 1] if "--host" in command else "127.0.0.1"
            base = f"http://{'127.0.0.1' if host in ('0.0.0.0', '::') else host}:{int(port)}"
            if _answers(base):
                return base
        except Exception:
            continue
    return None


def _family(pid):
    import psutil
    try:
        parent = psutil.Process(pid)
        return {pid} | {child.pid for child in parent.children(recursive=True)}
    except Exception:
        return {pid}


def _flags(app):
    """How Hunyuan3D is started: as Pinokio last started it (so the models it asks for are the ones that were
    downloaded), without its text-to-picture part."""
    asked = _option("sim360_hunyuan3d_flags")
    if not asked:
        try:            # Forge's own copy was told how when it was made
            with open(os.path.join(os.path.dirname(app), "flags.txt"), "r", encoding="utf8") as file:
                asked = file.read().strip()
        except OSError:
            asked = ""
    if not asked:
        try:
            with open(os.path.join(os.path.dirname(app), "logs", "api", "start.js", "latest"), "r", encoding="utf8", errors="replace") as file:
                found = re.search(r"python gradio_app\.py (.*?)--host", file.read(20000))
            asked = found.group(1) if found else ""
        except OSError:
            asked = ""
    flags = [part for part in (asked or "--h2 --turbo --profile 4").split() if part != "--enable_t23d"]
    return flags


def _free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _log_path():
    return os.path.join(store.root(), "_hunyuan3d.log")


def _command(app, port, more=()):
    """How Hunyuan3D's own program is run from here: the command, and the surroundings it needs."""
    python = _python(app)
    if not python:
        raise JobError("Hunyuan3D is installed without its own Python (app\\env): install it again from Pinokio")
    environment = {k: v for k, v in os.environ.items() if k.upper() not in ("PYTHONPATH", "PYTHONHOME")}
    root = os.path.dirname(app)
    cache = os.path.join(root, "cache")
    if os.path.isdir(os.path.join(root, "models", "tencent")):
        # Forge's own copy: its models are plain folders beside it, and it writes only into its own work folder
        for name in list(environment):
            if name.upper().startswith(("HF_", "HUGGINGFACE_", "TRANSFORMERS_", "DIFFUSERS_")):
                del environment[name]
        work = os.path.join(root, "work")
        environment.update(HY3DGEN_MODELS=os.path.join(root, "models"), HF_HOME=os.path.join(work, "hf"), HF_HUB_OFFLINE="1",
                           TORCH_HOME=os.path.join(work, "torch"), GRADIO_TEMP_DIR=os.path.join(work, "gradio"))
        more = list(more) + ["--cache-path", os.path.join(work, "made")]
    elif os.path.isdir(os.path.join(cache, "HF_HOME", "hub")):
        # The models Pinokio downloaded, and nothing new: a model that is missing is an error, not a download.
        # Forge points Hugging Face's libraries at its own folders (HF_HUB_CACHE and its like); passed on, they
        # would send Hunyuan3D looking for its models there.
        for name in list(environment):
            if name.upper().startswith(("HF_", "HUGGINGFACE_", "TRANSFORMERS_", "DIFFUSERS_")):
                del environment[name]
        environment.update(HF_HOME=os.path.join(cache, "HF_HOME"), HF_HUB_OFFLINE="1", TORCH_HOME=os.path.join(cache, "TORCH_HOME"),
                           GRADIO_TEMP_DIR=os.path.join(cache, "GRADIO_TEMP_DIR"))
    venv = os.path.dirname(os.path.dirname(python))
    environment.update(PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1", VIRTUAL_ENV=venv)
    paths = [os.path.dirname(python)]
    try:        # an environment built on conda's Python finds some of its libraries only beside that Python
        with open(os.path.join(venv, "pyvenv.cfg"), "r", encoding="utf8") as file:
            home = re.search(r"^home\s*=\s*(.+)$", file.read(), re.M)
        if home:
            base = home.group(1).strip()
            paths += [base, os.path.join(base, "Library", "mingw-w64", "bin"), os.path.join(base, "Library", "usr", "bin"),
                      os.path.join(base, "Library", "bin"), os.path.join(base, "Scripts")]
    except OSError:
        pass
    environment["PATH"] = os.pathsep.join(paths + [environment.get("PATH", "")])
    command = [python, "-u", os.path.join(os.path.dirname(os.path.abspath(__file__)), "solid_host.py"), str(os.getpid()),
               os.path.join(app, "gradio_app.py")] + _flags(app) + list(more) + ["--host", "127.0.0.1", "--port", str(port)]
    return command, environment


def _start(app, job):
    """Starts Hunyuan3D from here and waits until it answers. Its models stay in memory until it is stopped."""
    global _own
    port = _free_port()
    command, environment = _command(app, port)
    os.makedirs(store.root(), exist_ok=True)
    log = open(_log_path(), "w", encoding="utf8", errors="replace")
    log.write(" ".join(command) + "\n")
    log.flush()
    process = subprocess.Popen(command, cwd=app, env=environment, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    _own = {"process": process, "base": f"http://127.0.0.1:{port}", "log": log}
    job.stage = "starting Hunyuan3D"
    started = time.time()
    try:
        while time.time() - started < START_MINUTES * 60:
            if process.poll() is not None:
                raise JobError("Hunyuan3D could not start: " + (_log_tail() or f"it ended with code {process.returncode}"))
            if job.status == "cancelling":
                raise JobError("cancelled")
            if _answers(_own["base"], 3):
                _keep_watch()
                return _own["base"]
            job.share = min(0.95, (time.time() - started) / 60)
            time.sleep(2)
        raise JobError(f"Hunyuan3D did not start in {START_MINUTES} minutes")
    except BaseException:
        stop()              # whatever went wrong, it is not left running with nobody to stop it
        raise


def _log_tail(count=3):
    try:
        with open(_log_path(), "r", encoding="utf8", errors="replace") as file:
            lines = [line.strip() for line in file.read()[-4000:].splitlines() if line.strip()]
        return " | ".join(lines[-count:])[-500:]
    except OSError:
        return ""


def _keep_watch():
    """A Hunyuan3D that was started from here does not stay for ever: it holds memory Forge wants back."""
    global _keeper

    def watch():
        while True:
            time.sleep(20)
            with _lock:
                if _own is None:
                    return
                idle = time.time() - _used
            if idle > IDLE_MINUTES * 60 and not _busy.locked():
                stop()
                return

    if _keeper is None or not _keeper.is_alive():
        _keeper = threading.Thread(target=watch, name="sim360-hunyuan3d", daemon=True)
        _keeper.start()


def stop():
    """Stops the Hunyuan3D that was started from here (never one that Pinokio started). True if there was one."""
    global _own
    with _lock:
        own, _own = _own, None
    if own is None:
        return False
    import psutil
    for pid in _family(own["process"].pid):
        try:
            psutil.Process(pid).kill()
        except Exception:
            pass
    try:
        own["process"].wait(timeout=10)
    except Exception:
        pass
    try:
        own["log"].close()
    except Exception:
        pass
    return True


def state():
    """What the page is told: is Hunyuan3D installed, and is one running (ours, or Pinokio's)."""
    app = folder()
    if not app:
        return {"available": False}
    own = _own is not None and _own["process"].poll() is None
    return {"available": True, "folder": app, "home": where(app), "own": own, "open": own or bool(_open_elsewhere(app)),
            "seconds": {k: round(v) for k, v in _took.items()}, "memory_free": [round(v, 1) for v in memory_free()]}


def memory_free():
    """Gigabytes of memory that can still be taken: (free in the machine, what Windows will still promise to
    programs). The second runs out first when programs that use the graphics card are open: its driver asks
    for promises of many gigabytes, and when none are left, drawing fails and the computer stands still."""
    import psutil
    free = promise = psutil.virtual_memory().available / 2 ** 30
    if os.name == "nt":
        import ctypes

        class Status(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong), ("free", ctypes.c_ulonglong),
                        ("promised", ctypes.c_ulonglong), ("promise_free", ctypes.c_ulonglong), ("space", ctypes.c_ulonglong),
                        ("space_free", ctypes.c_ulonglong), ("more", ctypes.c_ulonglong)]

        status = Status()
        status.length = ctypes.sizeof(Status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            promise = status.promise_free / 2 ** 30
    return free, promise


def room(draws, free=None, own=None, elsewhere=None):
    """Refuses, in words, a model that cannot be made without the computer running out of memory.
    `draws`: Forge draws the picture first. `free`: (free, promise) when it is not to be measured."""
    if own is None:
        own = _own is not None and _own["process"].poll() is None
    if elsewhere is None:
        elsewhere = False if own else bool(_open_elsewhere(folder()))
    free = memory_free() if free is None else free
    parts = [MINE_NEEDS if own else OPEN_NEEDS if elsewhere else OWN_NEEDS] + ([DRAW_NEEDS] if draws else [])
    needed = [SPARE + sum(part[k] for part in parts) for k in (0, 1)]
    short = [k for k in (0, 1) if free[k] < needed[k]]
    if not short:
        return
    k = short[0]
    if elsewhere:
        raise JobError(f"Too little memory is free for this: {free[k]:.1f} GB, and about {needed[k]:.1f} GB are needed. Hunyuan3D is open in Pinokio "
                       f"and holds most of the memory: close it there, and it is started from here, without its text-to-picture part")
    raise JobError(f"Too little memory is free for a 3D model: {free[k]:.1f} GB, and about {needed[k]:.1f} GB are needed. Close other programs first")


def _address(job):
    global _own, _used
    app = folder()
    if not app:
        raise JobError("Hunyuan3D is not installed: install \"Hunyuan3D-2 (low VRAM)\" in Pinokio, or name its folder in Settings")
    _used = time.time()
    if _own is not None:
        if _own["process"].poll() is None and _answers(_own["base"]):
            return _own["base"], True
        stop()
    elsewhere = _open_elsewhere(app)
    if elsewhere:
        return elsewhere, False
    return _start(app, job), True


# --- asking it ---------------------------------------------------------------------------------------------------

def _arguments(named, endpoint, values):
    """The values in the order this Hunyuan3D takes them, with its own value for whatever is not said."""
    data = []
    for parameter in named["/" + endpoint]["parameters"]:
        name = parameter.get("parameter_name")
        data.append(values[name] if name in values else parameter.get("parameter_default"))
    return data


def _files(value):
    """The files in an answer, whatever they are wrapped in."""
    if isinstance(value, dict):
        if value.get("__type__") == "update":
            return _files(value.get("value"))
        if value.get("path") or value.get("url"):
            return [value]
    return []


def make(job, picture, target, options=None):
    """Hunyuan3D makes a model of what is in `picture` (a PNG with its own transparency) and it is written
    to `target` as .glb. Returns what is known about it. Forge's own models must be out of the graphics
    memory before this is called."""
    import requests
    global _used
    options = options or {}
    textured = options.get("texture", True) is not False
    with _busy:
        base, own = _address(job)
        named = _answers(base, 10)
        if named is None:
            raise JobError("Hunyuan3D does not answer")
        endpoint = "generation_all" if textured and "/generation_all" in named else "shape_generation"
        job.stage, job.share = "sending the picture", 0.0
        with open(picture, "rb") as file:
            sent = requests.post(base + "/gradio_api/upload", files={"files": (os.path.basename(picture), file, "image/png")}, timeout=120)
        if sent.status_code != 200:
            raise JobError("Hunyuan3D did not take the picture: " + sent.text[:200])
        seed = jobs._number(options.get("seed"), -1, -1, 9999999, whole=True)
        values = {
            "caption": None, "image": {"path": sent.json()[0], "meta": {"_type": "gradio.FileData"}},
            "steps": jobs._number(options.get("steps"), 5, 1, 100, whole=True),
            "guidance_scale": jobs._number(options.get("guidance"), 5.0, 0.0, 20.0),
            "seed": max(0, seed), "randomize_seed": seed < 0,
            "octree_resolution": jobs._number(options.get("detail"), 256, 64, 512, whole=True),
            "check_box_rembg": False, "num_chunks": 8000,
        }
        asked = requests.post(f"{base}/gradio_api/call/{endpoint}", json={"data": _arguments(named, endpoint, values)}, timeout=120)
        if asked.status_code != 200:
            raise JobError("Hunyuan3D refused: " + asked.text[:200])
        event = asked.json()["event_id"]
        started, expected = time.time(), _took["all" if endpoint == "generation_all" else "shape"]
        log = _log_path() if own else os.path.join(os.path.dirname(folder() or ""), "logs", "api", "start.js", "latest")
        try:
            offset = os.path.getsize(log)
        except OSError:
            offset = None
        done = threading.Event()

        def follow():
            # how far it is: by the clock, and by what it writes when it finishes each part
            job.stage = "making the shape"
            while not done.wait(1.0):
                job.share = min(0.97, (time.time() - started) / max(expected, 1.0))
                if offset is None:
                    continue
                try:
                    with open(log, "r", encoding="utf8", errors="replace") as file:
                        file.seek(offset)
                        written = file.read()
                except OSError:
                    continue
                for said, stage in STEPS:
                    if said in written:
                        job.stage = stage

        threading.Thread(target=follow, name="sim360-hunyuan3d-progress", daemon=True).start()
        answer, failure = None, None
        try:
            # it sends a heartbeat every few seconds while it works, so a silence of two minutes is an end
            with requests.get(f"{base}/gradio_api/call/{endpoint}/{event}", stream=True, timeout=(10, 120)) as stream:
                kind = None
                for line in stream.iter_lines(decode_unicode=True):
                    if job.status == "cancelling":
                        if own:
                            stop()          # the only way to make it stop working; it starts again when it is next asked
                        raise JobError("cancelled")
                    if not line:
                        continue
                    if line.startswith("event:"):
                        kind = line[6:].strip()
                    elif line.startswith("data:"):
                        if kind == "complete":
                            answer = json.loads(line[5:])
                            break
                        if kind == "error":
                            failure = line[5:].strip()
                            break
        except requests.RequestException as e:
            failure = f"{type(e).__name__}: {e}"
        finally:
            done.set()
            _used = time.time()
        if answer is None:
            said = _log_tail() if own else ""
            raise JobError("Hunyuan3D could not make the model" + (": " + (said or failure) if (said or failure) and (said or failure) != "null" else ""))
        files = [f for value in answer for f in _files(value)]
        if not files:
            raise JobError("Hunyuan3D returned no model")
        made = files[-1]                 # with a texture there are two: the bare shape, then the painted one
        job.stage = "saving"
        data = None
        if made.get("path") and os.path.isfile(made["path"]):
            with open(made["path"], "rb") as file:
                data = file.read()
        elif made.get("url"):
            data = requests.get(made["url"], timeout=300).content
        if not data or data[:4] != b"glTF":
            raise JobError("Hunyuan3D returned something that is not a model")
        with open(target, "wb") as file:
            file.write(data)
        if own:
            _tidy()
        seconds = time.time() - started
        key = "all" if endpoint == "generation_all" else "shape"
        _took[key] = 0.5 * _took[key] + 0.5 * seconds
        facts = measure(target)
        facts.update(seconds=round(seconds, 1), textured=endpoint == "generation_all",
                     seed=next((int(v) for v in reversed(answer) if isinstance(v, (int, float)) and not isinstance(v, bool)), None))
        return facts


def _tidy():
    """Forge's own copy keeps every model it makes, and every picture it is sent, in its work folder: emptied
    here, because the model is in the project by now."""
    import shutil
    work = os.path.join(own_home(), "work")
    for name in ("made", "gradio"):
        kept = os.path.join(work, name)
        if not os.path.isdir(kept):
            continue
        for entry in os.listdir(kept):
            full = os.path.join(kept, entry)
            try:
                if os.path.isdir(full):
                    if entry != "env_maps":             # (two small pictures its own page lights models with)
                        shutil.rmtree(full, ignore_errors=True)
                else:
                    os.remove(full)
            except OSError:
                pass


def measure(path):
    """What a .glb file holds: how many corners and triangles, how large it is, whether it has pictures,
    bones and movements of its own. Raises JobError for a file that is not a model this page can show."""
    try:
        with open(path, "rb") as file:
            magic, version, _length = struct.unpack("<4sII", file.read(12))
            size, kind = struct.unpack("<II", file.read(8))
            if magic != b"glTF" or version != 2 or kind != 0x4E4F534A:
                raise ValueError("not glTF 2")
            gltf = json.loads(file.read(size).decode("utf8"))
    except (OSError, ValueError, struct.error) as e:
        raise JobError(f"this is not a .glb model ({e})")
    needed = set(gltf.get("extensionsRequired") or [])
    packed = needed & {"KHR_draco_mesh_compression", "EXT_meshopt_compression"}
    if packed:
        raise JobError("this model is packed (" + ", ".join(sorted(packed)) + "): save it again without compression")
    accessors = gltf.get("accessors") or []
    corners = triangles = 0
    low, high = [np.inf] * 3, [-np.inf] * 3
    for mesh in gltf.get("meshes") or []:
        for part in mesh.get("primitives") or []:
            if part.get("mode", 4) != 4:
                continue
            position = accessors[part["attributes"]["POSITION"]]
            corners += position["count"]
            triangles += (accessors[part["indices"]]["count"] if "indices" in part else position["count"]) // 3
            if "min" in position and "max" in position:
                low = [min(a, b) for a, b in zip(low, position["min"])]
                high = [max(a, b) for a, b in zip(high, position["max"])]
    if not triangles:
        raise JobError("this model has no triangles")
    return {"corners": corners, "triangles": triangles, "pictures": len(gltf.get("images") or []),
            "bones": sum(len(skin.get("joints") or []) for skin in gltf.get("skins") or []),
            "moves": [str(a.get("name") or f"move {n + 1}") for n, a in enumerate(gltf.get("animations") or [])],
            "extent": [round(float(b - a), 4) for a, b in zip(low, high)] if np.isfinite(low[0]) else None}


# --- the jobs ----------------------------------------------------------------------------------------------------

def twice(alpha):
    """Whether a cut-out shows the same thing twice: two separate pieces of about the same size and shape."""
    import cv2
    count, _labels, stats, _ = cv2.connectedComponentsWithStats((alpha > 127).astype(np.uint8), connectivity=8)
    pieces = sorted((stats[i] for i in range(1, count)), key=lambda piece: -piece[cv2.CC_STAT_AREA])
    if len(pieces) < 2:
        return False
    a, b = pieces[0], pieces[1]
    alike = lambda field, low: b[field] >= low * a[field] and a[field] >= low * b[field]
    return bool(alike(cv2.CC_STAT_AREA, 0.6) and alike(cv2.CC_STAT_WIDTH, 0.7) and alike(cv2.CC_STAT_HEIGHT, 0.7))


def _draw_alone(job, folder_, object_id, s, pose=None):
    """Forge draws the thing by itself on a plain ground, the way a model is best made from, and it is cut out.
    `pose`: a drawing of a figure's bones (OpenPose) that the figure is drawn in: whole, from head to foot,
    its arms apart from its body, which is what lets its model walk."""
    settings = jobs._basic_settings(s)
    settings["steps"] = jobs._number(s.get("steps"), 8, 1, 150, whole=True)
    if not settings["prompt"].strip():
        raise JobError("Write what to make first")
    if s.get("alone", True):
        at = settings["prompt"].find("<lora:")
        words, loras = (settings["prompt"], "") if at < 0 else (settings["prompt"][:at], settings["prompt"][at:])
        settings["prompt"] = words.rstrip(" ,") + ALONE + (" " + loras if loras else "")
        settings["negative_prompt"] = ", ".join(part for part in (settings["negative_prompt"].strip(" ,"), NOT_ALONE) if part)
    settings["width"], settings["height"] = SIZES.get(s.get("shape"), SIZES["portrait"])
    alwayson = {}
    if pose:
        model = s.get("control_model") or jobs.openpose_model()
        if not model:
            raise JobError("no OpenPose model was found in models/ControlNet")
        alwayson["ControlNet"] = [{
            "enabled": True, "module": "None", "model": model, "image": pose, "weight": jobs._number(s.get("control_weight"), 1.0, 0.0, 2.0),
            "guidance_start": 0.0, "guidance_end": 1.0, "resize_mode": "Crop and Resize", "pixel_perfect": True, "control_mode": "Balanced",
            "processor_res": 512, "save_detected_map": False,
        }]
    # Asked for one long low thing (a car from its side), Forge often draws two, one above the other, and a
    # model of that is two cars welded together. Such a picture is drawn again, up to three times.
    tries = 1 if settings["seed"] >= 0 else 3
    for attempt in range(tries):
        processed = runner.draw(job, settings, alwayson=alwayson)
        frames = runner.pictures(processed)
        if not frames:
            raise JobError("Forge returned no picture")
        rgb = np.asarray(frames[0].convert("RGB"))
        job.stage = "cutting out"
        alpha = objects.cut_out(rgb, None)
        if (alpha > 127).mean() > 0.97:
            raise JobError("The picture has no plain background to cut the thing out of: try other words")
        if not twice(alpha):
            break
        print(f"[Simulation 360] the picture shows the thing twice (try {attempt + 1} of {tries})")
    text = runner.infotext(processed)
    described = objects._save(job, folder_, object_id, rgb, alpha, text)
    described.update(kind="image", seed=int(processed.seed), infotext=text)
    return described


@runner.handler("solid")
def make_solid(job):
    """A 3D model for an object: of the picture it already has ("file"), or of words (Forge draws the picture
    first). The answer describes the object the way the object jobs do, with "model" beside its picture."""
    from modules.call_queue import queue_lock
    params = job.params
    project, world, object_id = params["project"], params["world"], store.clean_id(params["object"], "object")
    where = store.world_dir(project, world)
    if not folder():
        raise JobError("Hunyuan3D is not installed: install \"Hunyuan3D-2 (low VRAM)\" in Pinokio, or name its folder in Settings")
    room(draws=not params.get("file"))
    if params.get("file"):
        picture = store.resolve(project, f"worlds/{world}/{os.path.basename(str(params['file']))}")
        described = {}
    else:
        described = _draw_alone(job, where, object_id, params.get("settings") or {}, params.get("pose"))
        picture = os.path.join(where, described["file"])
    try:
        with Image.open(picture) as opened:
            if opened.mode != "RGBA" or opened.getchannel("A").getextrema()[0] > 250:
                raise JobError("This picture has no cut-out: a model is made of a thing lifted off its background")
    except OSError:
        raise JobError("this picture cannot be read")
    name = f"object-{object_id}-{objects._next(where, object_id):04d}.glb"
    job.stage = "waiting for Forge"
    with queue_lock:            # Forge draws nothing meanwhile: the two would fight over the graphics memory
        if job.status == "cancelling":
            raise JobError("cancelled")
        _free_graphics_memory()
        room(draws=False)
        facts = make(job, picture, os.path.join(where, name), params.get("options") or {})
    described.update(model=name, model_facts=facts)
    return described


def _free_graphics_memory():
    try:
        from ldm_patched.modules import model_management
        model_management.unload_all_models()
        model_management.soft_empty_cache()
    except Exception as e:
        print(f"[Simulation 360] could not free graphics memory: {type(e).__name__}: {e}")


SEAMLESS = ", a seamless texture of this material, seen straight on, filling the picture, flat even light"
NOT_SEAMLESS = "perspective, horizon, objects, a scene, shadows, frame, border, text, watermark"


@runner.handler("skin")
def draw_skin(job):
    """A picture of a material (bricks, bark, rusty iron) for an object to wear over its model. It repeats
    without a seam: what Forge draws is laid four times, mirrored, so every edge meets itself."""
    params = job.params
    project, world, object_id = params["project"], params["world"], store.clean_id(params["object"], "object")
    where = store.world_dir(project, world)
    s = params.get("settings") or {}
    settings = jobs._basic_settings(s)
    settings["steps"] = jobs._number(s.get("steps"), 8, 1, 150, whole=True)
    if not settings["prompt"].strip():
        raise JobError("Write what it is made of first")
    at = settings["prompt"].find("<lora:")
    words, loras = (settings["prompt"], "") if at < 0 else (settings["prompt"][:at], settings["prompt"][at:])
    settings["prompt"] = words.rstrip(" ,") + SEAMLESS + (" " + loras if loras else "")
    settings["negative_prompt"] = ", ".join(part for part in (settings["negative_prompt"].strip(" ,"), NOT_SEAMLESS) if part)
    settings["width"] = settings["height"] = 512
    processed = runner.draw(job, settings)
    frames = runner.pictures(processed)
    if not frames:
        raise JobError("Forge returned no picture")
    tile = frames[0].convert("RGB")
    w, h = tile.size
    sheet = Image.new("RGB", (w * 2, h * 2))
    sheet.paste(tile, (0, 0))
    sheet.paste(tile.transpose(Image.FLIP_LEFT_RIGHT), (w, 0))
    sheet.paste(tile.transpose(Image.FLIP_TOP_BOTTOM), (0, h))
    sheet.paste(tile.transpose(Image.ROTATE_180), (w, h))
    name = f"object-{object_id}-{objects._next(where, object_id):04d}.skin.jpg"
    sheet.save(os.path.join(where, name), format="JPEG", quality=93)
    return {"skin": name, "seed": int(processed.seed), "infotext": runner.infotext(processed)}


@runner.handler("solid_home")
def make_home(job):
    """Hunyuan3D becomes a part of Forge: the copy another program installed is linked into Forge's models
    folder (program, Python, models), after which that program is not needed."""
    source = next((c for c in _others() if os.path.isfile(os.path.join(c, "gradio_app.py"))), None)
    if solid_home.complete(own_home()) and not job.params.get("again"):
        return {"folder": own_home(), "already": True}
    if not source:
        raise JobError("No Hunyuan3D was found to take in: install \"Hunyuan3D-2 (low VRAM)\" in Pinokio once, and start it there so it downloads its models")
    stop()

    def say(stage, share=None):
        job.stage, job.share = stage, share

    try:
        done = solid_home.adopt(source, own_home(), _flags(source), say=say)
    except solid_home.HomeError as e:
        raise JobError(str(e))
    done["from"] = os.path.dirname(source)
    return done


def _others():
    """Hunyuan3D programs that are not Forge's own: one named in Settings, and those Pinokio installed."""
    asked = _option("sim360_hunyuan3d_dir")
    found = [asked, os.path.join(asked, "app")] if asked else []
    for home in _pinokio_homes():
        api = os.path.join(home, "api")
        if os.path.isdir(api):
            found += [os.path.join(api, name, "app") for name in sorted(os.listdir(api)) if "hunyuan3d" in name.lower()]
    return [c for c in found if os.path.isdir(os.path.join(c, "hy3dgen")) and os.path.normcase(os.path.dirname(c)) != os.path.normcase(own_home())]


def take_in(project, world, object_id, path):
    """A .glb file from the computer becomes an object's model."""
    where = store.world_dir(project, world)
    facts = measure(path)
    name = f"object-{object_id}-{objects._next(where, object_id):04d}.glb"
    os.replace(path, os.path.join(where, name))
    return {"model": name, "model_facts": facts}


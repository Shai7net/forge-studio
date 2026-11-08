"""Hunyuan3D-2 as a part of Forge: its program, its own Python and its models in Forge's models folder.

    models\\sim360-hunyuan3d\\
        app\\        the program (Hunyuan3D-2GP: gradio_app.py and hy3dgen, the version that works in little
                    graphics memory), with its licence
        env\\        its own Python environment: it needs newer libraries than Forge has, so it cannot share Forge's
        models\\     tencent\\Hunyuan3D-2\\...: the shape model, its decoder, the two models that paint
        flags.txt   how it is started
        work\\       what it writes while it works; emptied after every model

`adopt` builds this from a copy that another program installed (Pinokio's), by linking: a linked file is the
same file on the disk under a second name, so nothing is copied, no space is taken, and the files stay when
the other program and its folder are removed. Linking needs both folders on one drive; across drives the
files are copied.
"""

import os
import shutil
import subprocess
import sys

NAME = "sim360-hunyuan3d"
NEEDS = ("torch", "diffusers", "transformers", "mmgp", "trimesh", "gradio", "custom_rasterizer", "xatlas")
SMALL = ("example_prompts.txt", "modelviewer-template.html", "modelviewer-textured-template.html")


class HomeError(Exception):
    """Something the user should read as it is."""


def home(models_path):
    return os.path.join(models_path, NAME)


def complete(root):
    """Whether Forge's own copy is whole: the program, a Python to run it, and models."""
    return (os.path.isfile(os.path.join(root, "app", "gradio_app.py")) and os.path.isdir(os.path.join(root, "app", "hy3dgen"))
            and python_of(root) is not None and os.path.isdir(os.path.join(root, "models", "tencent")))


def python_of(root):
    for path in (os.path.join(root, "env", "Scripts", "python.exe"), os.path.join(root, "env", "bin", "python")):
        if os.path.isfile(path):
            return path
    return None


def _put(source, target, counts):
    """One file under a second name (or, when that cannot be, a copy). A file that is there already stays."""
    if os.path.exists(target):
        if os.path.getsize(target) == os.path.getsize(source):
            counts["there"] += 1
            return
        os.remove(target)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    try:
        os.link(source, target)
        counts["linked"] += 1
    except OSError:
        shutil.copy2(source, target)
        counts["copied"] += 1
        counts["bytes"] += os.path.getsize(target)


def _tree(source, target, counts, skip=lambda relative: False, tell=None):
    done = 0
    for base, folders, files in os.walk(source):
        folders[:] = [f for f in folders if f != "__pycache__"]
        for name in files:
            full = os.path.join(base, name)
            relative = os.path.relpath(full, source)
            if skip(relative):
                continue
            _put(os.path.realpath(full), os.path.join(target, relative), counts)      # (a model's file is a pointer to where it is kept)
            done += 1
            if tell and done % 500 == 0:
                tell(done)


def model_folders(source_app):
    """The Hunyuan3D models another install has downloaded: [(name as the program asks for it, its folder)]."""
    hub = os.path.join(os.path.dirname(source_app), "cache", "HF_HOME", "hub")
    found = []
    if os.path.isdir(hub):
        for name in sorted(os.listdir(hub)):
            parts = name.split("--")
            if len(parts) != 3 or parts[0] != "models" or not parts[2].lower().startswith("hunyuan3d"):
                continue            # (its text-to-picture model is not taken: Forge draws the pictures)
            snapshots = os.path.join(hub, name, "snapshots")
            wanted = None
            try:
                with open(os.path.join(hub, name, "refs", "main"), "r", encoding="utf8") as file:
                    wanted = file.read().strip()
            except OSError:
                pass
            versions = sorted(os.listdir(snapshots)) if os.path.isdir(snapshots) else []
            if versions:
                found.append((f"{parts[1]}/{parts[2]}", os.path.join(snapshots, wanted if wanted in versions else versions[-1])))
    return found


def adopt(source_app, root, flags, base_python=None, say=lambda stage, share=None: None, needs=NEEDS):
    """Builds Forge's own copy at `root` from the install whose program is in `source_app`. Returns what was done.
    `needs`: the libraries that must load for it to count as working."""
    source_env = os.path.join(source_app, "env")
    packages = os.path.join(source_env, "Lib", "site-packages")
    if not os.path.isdir(packages):
        packages = next((os.path.join(base, "site-packages") for base, folders, _ in os.walk(os.path.join(source_env, "lib")) if "site-packages" in folders), None)
    models = model_folders(source_app)
    if not os.path.isfile(os.path.join(source_app, "gradio_app.py")) or not packages or not os.path.isdir(packages):
        raise HomeError("the Hunyuan3D that was found is not whole: its program or its Python libraries are missing")
    if not models:
        raise HomeError("the Hunyuan3D that was found has no models yet: start it once where it is installed, so it downloads them")
    counts = {"linked": 0, "copied": 0, "there": 0, "bytes": 0}
    app = os.path.join(root, "app")

    say("taking its program")
    _tree(os.path.join(source_app, "hy3dgen"), os.path.join(app, "hy3dgen"), counts)
    for name in ("gradio_app.py", "LICENSE", "NOTICE", "README.md"):
        if os.path.isfile(os.path.join(source_app, name)):
            shutil.copy2(os.path.join(source_app, name), os.path.join(app, name))
    for name in SMALL:
        os.makedirs(os.path.join(app, "assets"), exist_ok=True)
        if os.path.isfile(os.path.join(source_app, "assets", name)):
            shutil.copy2(os.path.join(source_app, "assets", name), os.path.join(app, "assets", name))
    for name in ("example_images", "example_mv_images"):         # it lists them when it starts; they may be empty
        os.makedirs(os.path.join(app, "assets", name), exist_ok=True)
    if os.path.isdir(os.path.join(source_app, "assets", "env_maps")):          # two small pictures its page lights models with; it will not start without them
        shutil.copytree(os.path.join(source_app, "assets", "env_maps"), os.path.join(app, "assets", "env_maps"), dirs_exist_ok=True)

    say("making its Python")
    if python_of(root) is None:
        # on the Python Forge itself is built on: that one stays as long as Forge does
        base_python = base_python or os.path.join(sys.base_prefix, "python.exe" if os.name == "nt" else os.path.join("bin", "python3"))
        made = subprocess.run([base_python, "-m", "venv", "--without-pip", os.path.join(root, "env")], capture_output=True,
                              stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if made.returncode != 0 or python_of(root) is None:
            raise HomeError("its Python could not be made: " + (made.stderr or made.stdout).decode("utf8", "replace")[-300:])
    relative = os.path.relpath(packages, source_env)
    total = sum(len(files) for _, _, files in os.walk(packages))
    _tree(packages, os.path.join(root, "env", relative), counts, tell=lambda done: say("taking its libraries", done / max(total, 1)))

    say("taking its models")
    for name, folder in models:
        # (the same model twice in another file format is left behind: the program is started to read .safetensors)
        _tree(folder, os.path.join(root, "models", *name.split("/")), counts, skip=lambda relative: relative.lower().endswith(".ckpt"))
    with open(os.path.join(root, "flags.txt"), "w", encoding="utf8") as file:
        file.write(" ".join(flags) + "\n")
    with open(os.path.join(root, "README.txt"), "w", encoding="utf8") as file:
        file.write("Hunyuan3D-2 for Simulation 360 (the Forge extension): the program (app), its own Python (env) and its models.\n"
                   f"Taken from {os.path.dirname(source_app)} by linking: the files here are the same files, under a second name.\n"
                   "That folder, and the program that installed it, can be removed; what is here stays.\n"
                   "Hunyuan3D-2 is Tencent's, under the licence in app\\LICENSE (not for commercial use).\n")
    os.makedirs(os.path.join(root, "work"), exist_ok=True)

    say("trying it")
    problem = check(root, needs)
    if problem:
        raise HomeError("it was taken in, but it cannot run here: " + problem)
    return {"folder": root, "linked": counts["linked"], "copied": counts["copied"], "there": counts["there"],
            "copied_gb": round(counts["bytes"] / 2 ** 30, 2), "models": [name for name, _ in models]}


def check(root, needs=NEEDS):
    """Whether Forge's own copy can run by itself: its libraries load, and none of them from anywhere else.
    Returns what is wrong, or None."""
    python = python_of(root)
    if python is None:
        return "its Python is missing"
    script = ("import importlib, json, os, sys\n"
              "seen = {}\n"
              f"for name in {tuple(needs)!r}:\n"
              "    module = importlib.import_module(name)\n"
              "    seen[name] = os.path.dirname(getattr(module, '__file__', '') or '')\n"
              "print('RESULT ' + json.dumps({'where': seen, 'prefix': sys.prefix}))\n")
    environment = {k: v for k, v in os.environ.items() if k.upper() not in ("PYTHONPATH", "PYTHONHOME")}
    done = subprocess.run([python, "-c", script], capture_output=True, stdin=subprocess.DEVNULL, env=environment, cwd=os.path.join(root, "app"),
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    said = done.stdout.decode("utf8", "replace")
    if done.returncode != 0 or "RESULT " not in said:
        return (done.stderr.decode("utf8", "replace").strip().splitlines() or ["its libraries do not load"])[-1][:300]
    import json
    result = json.loads(said.split("RESULT ", 1)[1].splitlines()[0])
    inside = os.path.normcase(os.path.realpath(os.path.join(root, "env")))
    elsewhere = [name for name, where in result["where"].items() if not os.path.normcase(os.path.realpath(where)).startswith(inside)]
    if elsewhere:
        return "these libraries are loaded from another place: " + ", ".join(elsewhere)
    return None

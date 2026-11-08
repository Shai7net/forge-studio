"""Motion made by a neural locomotion controller: AI4Animation (Sebastian and Paul Starke, Meta;
github.com/facebookresearch/ai4animationpy, CC BY-NC 4.0: for non-commercial use).

A 3D figure that walks along its path gets real steps instead of the formula walk: the network was trained on
motion capture (the Style100 dataset), so it starts, slows, turns into a corner, stops and stands the way
people do, in one of its styles (Neutral, Zombie, Chicken, Dinosaur, On heels, Big steps, ...). It runs in its
own Python (models\\sim360-ai4animation: the framework's code, the demo's network and characters, an env with
PyTorch for the processor) through ai4a_motion.py; the page lays the joints it gives onto the figure's own
bones (Solid.retarget).

    job "motion": project, world, object, path [[t, x, z], ...] (scene metres, scaled to the network's body),
                  kind "biped", style
    result:       object-<id>-NNNN.motion.json in the world's folder
"""
import json
import os
import subprocess
import time

from . import objects, runner, store
from .runner import JobError

STYLES = ("Neutral", "BigSteps", "Chicken", "Dinosaur", "DragLeftLeg", "HandsBetweenLegs", "LeanRight", "LegsApart", "OnHeels", "Star", "Zombie")
BODY_HEIGHTS = {"biped": 1.75, "four": 0.75}          # metres: how tall the bodies the networks learnt on are (Geno; the dog)


def home():
    """Forge's own copy of AI4AnimationPy (or the folder named in Settings) when it is whole, or None."""
    from modules import paths, shared
    wanted = str(shared.opts.data.get("sim360_ai4animation_dir") or "").strip() or \
        os.path.join(getattr(paths, "models_path", None) or os.path.join(paths.data_path, "models"), "sim360-ai4animation")
    needs = (("env", "Scripts", "python.exe"), ("ai4animation", "AI4Animation.py"), ("Demos", "Locomotion", "Biped", "Network.pt"),
             ("Demos", "Locomotion", "Biped", "Program.py"), ("Demos", "_ASSETS_", "Geno", "Model.glb"))
    return os.path.abspath(wanted) if all(os.path.isfile(os.path.join(wanted, *part)) for part in needs) else None


def describe():
    return {"available": bool(home()), "styles": list(STYLES), "body_heights": BODY_HEIGHTS, "kinds": list(BODY_HEIGHTS)}


@runner.handler("motion")
def make_motion(job):
    params = job.params
    project, world = params["project"], params["world"]
    object_id = store.clean_id(params["object"], "object")
    folder = store.world_dir(project, world)
    where = home()
    if not where:
        raise JobError("AI4Animation is not installed in this Forge (models\\sim360-ai4animation)")
    path = [[float(t), float(x), float(z)] for t, x, z in params.get("path") or []]
    if len(path) < 2:
        raise JobError("the object has no path to walk")
    if path[-1][0] - path[0][0] > 600:
        raise JobError("a path longer than ten minutes")
    kind = params.get("kind") if params.get("kind") in BODY_HEIGHTS else "biped"
    style = (params.get("style") if params.get("style") in STYLES else "Neutral") if kind == "biped" else ""
    acts = [[float(t), str(a)] for t, a in params.get("acts") or [] if str(a) in ("sit", "lie", "stand", "")]
    work = os.path.join(folder, f"_motion-{object_id}-{time.time_ns()}")
    os.makedirs(work)
    try:
        request, out = os.path.join(work, "request.json"), os.path.join(work, "motion.json")
        with open(request, "w", encoding="utf8") as file:
            json.dump({"kind": kind, "style": style, "fps": 30, "path": path, "acts": acts}, file)
        job.stage = "walking it"
        job.share = 0.1
        environment = {k: v for k, v in os.environ.items() if not k.upper().startswith(("PYTHON", "VIRTUAL_ENV", "HF_", "TORCH_HOME"))}
        helper = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ai4a_motion.py")
        done = subprocess.run([os.path.join(where, "env", "Scripts", "python.exe"), "-I", "-X", "utf8", helper, where, request, out],
                              capture_output=True, env=environment, stdin=subprocess.DEVNULL, timeout=1800,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        said = (done.stdout + done.stderr).decode("utf8", "replace")
        if done.returncode != 0 or not os.path.isfile(out):
            raise JobError("AI4Animation could not walk it: " + said.strip()[-400:])
        with open(out, encoding="utf8") as file:
            made = json.load(file)
        made["scale"] = float(params.get("scale") or 1)
        made["kind"] = kind
        made["made_with"] = ("AI4Animation biped locomotion (Style100)" if kind == "biped" else "AI4Animation quadruped locomotion (MANN, Starke 2018)") + ", CC BY-NC 4.0"
        name = f"object-{object_id}-{objects._next(folder, object_id):04d}.motion.json"
        with open(os.path.join(folder, name), "w", encoding="utf8") as file:
            json.dump(made, file, separators=(",", ":"))
        return {"file": name, "frames": len(made["frames"]), "style": style, "kind": kind, "seconds": round(len(made["frames"]) / made["fps"], 2)}
    finally:
        import shutil
        shutil.rmtree(work, ignore_errors=True)


# --- motion capture from a file -----------------------------------------------------------------------------------

MOTION_FILES = (".bvh", ".glb", ".gltf", ".npz", ".fbx")


def take_motion_in(project, world, object_id, incoming, filename):
    """A motion capture file for an object: kept beside the object's files, as it came."""
    folder = store.world_dir(project, world)
    kind = os.path.splitext(filename)[1].lower()
    if kind not in MOTION_FILES:
        raise JobError("a motion is a .bvh, .glb, .npz or .fbx file")
    name = f"object-{object_id}-{objects._next(folder, object_id):04d}{kind}"
    os.replace(incoming, os.path.join(folder, name))
    return {"file": name}


@runner.handler("mocap")
def read_mocap(job):
    """A motion capture file of an object -> a clip the page lays onto the figure's bones."""
    params = job.params
    project, world = params["project"], params["world"]
    object_id = store.clean_id(params["object"], "object")
    folder = store.world_dir(project, world)
    where = home()
    if not where:
        raise JobError("AI4Animation is not installed in this Forge (models\sim360-ai4animation)")
    source = os.path.join(folder, os.path.basename(str(params.get("file") or "")))
    if not os.path.isfile(source):
        raise JobError("the motion file is not there")
    out = os.path.join(folder, f"_mocap-{object_id}-{time.time_ns()}.json")
    job.stage = "reading the motion"
    job.share = 0.2
    environment = {k: v for k, v in os.environ.items() if not k.upper().startswith(("PYTHON", "VIRTUAL_ENV", "HF_", "TORCH_HOME"))}
    helper = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ai4a_mocap.py")
    try:
        done = subprocess.run([os.path.join(where, "env", "Scripts", "python.exe"), "-I", "-X", "utf8", helper, where, source, out],
                              capture_output=True, env=environment, stdin=subprocess.DEVNULL, timeout=900,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        said = (done.stdout + done.stderr).decode("utf8", "replace")
        failed = next((line[7:] for line in said.splitlines() if line.startswith("FAILED ")), None)
        if done.returncode != 0 or not os.path.isfile(out):
            raise JobError(failed or ("the motion could not be read: " + said.strip()[-300:]))
        name = f"object-{object_id}-{objects._next(folder, object_id):04d}.motion.json"
        os.replace(out, os.path.join(folder, name))
        with open(os.path.join(folder, name), encoding="utf8") as file:
            made = json.load(file)
        return {"file": name, "frames": len(made["frames"]), "seconds": made["seconds"], "height": made["height"], "source": made["source"],
                "skipped": made.get("skipped", 0)}
    finally:
        try:
            os.remove(out)
        except OSError:
            pass

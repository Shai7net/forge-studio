"""A character's 3D model, made by Simulation 360's Hunyuan3D-2, and kept in Simulation 360's "My objects".

The front view of the sheet (whole figure, arms out: what lets the model walk) is found by Video Studio's sheet
reader, cut out of its backdrop by Simulation 360's cut-out, and given to Hunyuan3D the way Simulation 360 asks it
(its own program, started when needed, with Forge's models off the card meanwhile). The model is kept with the
character and, as a kept object, in Simulation 360's library, from where it is stood in any world.
"""

import json
import os
import threading
import time

import numpy as np
from PIL import Image

from . import WEBUI_DIR, library

MODEL_FILE = "model.glb"
PICTURE_FILE = "model-picture.png"  # the cut-out front view the model was made of
DETAIL = {"Usual": 256, "Fine": 384}


class ModelError(RuntimeError):
    pass


def sim360():
    """Simulation 360's modules, or None when it is not installed."""
    try:
        from sim360 import library as kept, objects, solid, store
        return {"kept": kept, "objects": objects, "solid": solid, "store": store}
    except Exception:
        return None


def available():
    s = sim360()
    if s is None:
        return False, "Simulation 360 is not installed: it makes the 3D models."
    try:
        if not s["solid"].folder():
            return False, "Hunyuan3D-2 is not installed (Simulation 360 → A 3D model shows how)."
    except Exception as e:  # noqa: BLE001
        return False, f"Hunyuan3D-2 cannot be found: {e}"
    return True, ""


def front_view(sheet_path):
    """The front full-body view of a sheet (Video Studio's reader), or the whole picture when it is not a sheet."""
    try:
        from videostudio import sheets
        views = dict(sheets.split(sheet_path, sheets.matte_file(os.path.join(WEBUI_DIR, "models"))))
        if "front" in views:
            return views["front"]
    except Exception as e:  # noqa: BLE001
        print(f"[Characters] the sheet's front view could not be found: {type(e).__name__}: {e}")
    with Image.open(sheet_path) as im:
        return im.convert("RGB")


def let_go_of_checkpoint():
    """FLUX 3's engine (the sheets' checkpoint) out of memory with its pinned weights; the next picture loads it
    again. (Forge's own unload_model_weights does nothing on this Forge.)

    It runs on Forge's main thread, where the weights were loaded: freed from another thread they left Python but
    stayed committed to the process (measured 2026-11-09: 12.6 GB before and 12.2 after from a request thread,
    12.6 -> 2.5 GB on the main thread), and Hunyuan3D was refused for want of memory."""
    try:
        from modules_forge import main_thread
        return main_thread.run_and_wait_result(_let_go)
    except ImportError:
        return _let_go()


def _let_go():
    import gc
    import sys
    glue = sys.modules.get("flux3.glue")
    if glue is not None and getattr(glue, "ENGINE", None) is not None:
        try:
            from modules import sd_models
            if isinstance(sd_models.model_data.sd_model, glue.Flux3SdModel):
                sd_models.model_data.sd_model = None
                sd_models.model_data.loaded_sd_models = []
            glue._free_engine()
        except Exception as e:  # noqa: BLE001
            print(f"[Characters] could not free FLUX 3: {type(e).__name__}: {e}")
    gc.collect()


class _Job:
    """What Simulation 360's Hunyuan3D runner reports to (its own jobs have these fields)."""

    def __init__(self):
        self.stage, self.share, self.status = "starting", 0.0, "running"


def make(character_id, detail="Usual", report=None):
    """Makes the model of a saved character; returns its record ("model3d" in character.json). `report(stage,
    share)` is called while it works. Takes Forge's queue while Hunyuan3D runs."""
    ok, why = available()
    if not ok:
        raise ModelError(why)
    s = sim360()
    c = library.load(_root(), character_id)
    if c is None:
        raise ModelError("This character is gone.")
    report = report or (lambda *_: None)
    report("finding the front view", 0.0)
    front = np.asarray(front_view(c["sheet"]).convert("RGB"))
    report("cutting it out", 0.02)
    alpha = s["objects"].cut_out(front, None)  # takes Forge's queue itself, so before ours
    picture = Image.fromarray(np.dstack([front, alpha]), "RGBA")
    picture_path = os.path.join(c["folder"], PICTURE_FILE)
    picture.save(picture_path)
    target = os.path.join(c["folder"], MODEL_FILE)
    job = _Job()
    result, failure = {}, []

    def work():
        from modules.call_queue import queue_lock
        try:
            job.stage = "waiting for Forge"
            with queue_lock:
                s["solid"]._free_graphics_memory()
                try:
                    s["solid"].room(draws=False)
                except Exception:
                    # FLUX 3 Klein holds ~9 GB of pinned memory after drawing sheets, and Hunyuan3D needs ~16 GB
                    # free (measured 2026-11-09: 15.9 free, refused): Forge lets go of its checkpoint, which it
                    # loads again by itself at the next picture (~15 s)
                    job.stage = "letting go of Forge's checkpoint"
                    let_go_of_checkpoint()
                    s["solid"].room(draws=False)
                result.update(s["solid"].make(job, picture_path, target,
                                              {"texture": True, "detail": DETAIL.get(detail, 256)}))
        except Exception as e:  # noqa: BLE001 - reported to the tab
            failure.append(e)

    thread = threading.Thread(target=work, name="characters-3d", daemon=True)
    thread.start()
    while thread.is_alive():
        report(job.stage, job.share)
        thread.join(1.0)
    if failure:
        raise ModelError(str(failure[0]))
    record = {"file": MODEL_FILE, "picture": PICTURE_FILE, "facts": result, "detail": detail, "made": time.time()}
    data = _read(c["folder"])
    data["model3d"] = record
    data.pop("sim360_kept", None)  # a new model: kept again on the next send
    _write(c["folder"], data)
    return record


def keep_in_sim360(character_id):
    """The character's model as a kept object of Simulation 360 ("My objects"); returns {"id", "name"}. Made
    once per model; a kept object that was forgotten there is kept again."""
    s = sim360()
    if s is None:
        raise ModelError("Simulation 360 is not installed.")
    c = library.load(_root(), character_id)
    if c is None or not c.get("model3d"):
        raise ModelError("Make the character's 3D model first.")
    data = _read(c["folder"])
    base = s["kept"].folder()
    old = data.get("sim360_kept")
    if old and os.path.isfile(os.path.join(base, old, "object.json")):
        return {"id": old, "name": c.get("name", "")}
    picture_path = os.path.join(c["folder"], c["model3d"].get("picture") or PICTURE_FILE)
    model_path = os.path.join(c["folder"], c["model3d"].get("file") or MODEL_FILE)
    if not (os.path.isfile(picture_path) and os.path.isfile(model_path)):
        raise ModelError("The model's files are missing: make the 3D model again.")
    object_id = "ch" + format(time.time_ns() // 1000000, "x")
    kept_id = "k" + format(time.time_ns() // 1000000, "x")
    folder = os.path.join(base, kept_id)
    os.makedirs(folder)
    with Image.open(picture_path) as im:
        width, height = im.size
        alpha = np.asarray(im.getchannel("A"))
    file_name, model_name = f"object-{object_id}-0001.png", f"object-{object_id}-0002.glb"
    import shutil
    shutil.copyfile(picture_path, os.path.join(folder, file_name))
    shutil.copyfile(model_path, os.path.join(folder, model_name))
    ch = c.get("character") or {}
    described = {
        "name": c.get("name", ""), "kind": "image", "file": file_name, "poster": None, "depth": None,
        "width": width, "height": height, "box": s["objects"]._box(alpha), "frames": None, "fps": None,
        "seed": c.get("seed"), "relief": 0.2, "opacity": 1, "flip": False,
        # a picture's infotext makes the page match the model's colours to it and lay the face on it
        "infotext": f"Characters: {ch.get('summary') or c.get('name', '')}",
        "model": model_name, "model_facts": c["model3d"].get("facts") or {}, "flat": False,
        "body": {"kind": "biped"}, "act": "idle", "was": object_id,
    }
    with open(os.path.join(folder, "object.json"), "w", encoding="utf-8") as f:
        json.dump(described, f, ensure_ascii=False, indent=1)
    data["sim360_kept"] = kept_id
    _write(c["folder"], data)
    return {"id": kept_id, "name": c.get("name", "")}


def _root():
    from . import LIBRARY_DIR
    return LIBRARY_DIR


def _read(folder):
    with open(os.path.join(folder, library.CHARACTER_FILE), encoding="utf-8") as f:
        return json.load(f)


def _write(folder, data):
    library._write(folder, data)

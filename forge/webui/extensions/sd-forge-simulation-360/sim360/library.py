"""My objects: what was made in one world is kept for other worlds and other projects.

    simulation-360\\_library\\<id>\\
        object.json       what the object is (its name, its picture, its model, its body, its colours...), without
                          where it stood
        object-...        its files, as they were in its world

Keeping an object copies its files here; using a kept object copies them into the world it is put in, under
the new object's own name, so nothing is shared and nothing that is deleted takes something else with it.
"""

import os
import re
import shutil
import time

from . import store
from .store import StoreError

FILES = ("file", "poster", "depth", "model", "skin")            # the fields of an object that name files
PLACE = ("id", "pos", "path", "walk", "held", "history", "shy", "on_ground", "turn")        # where and how it stood: not kept


def folder():
    return os.path.join(store.root(), "_library")


def _entry(kept_id):
    path = os.path.join(folder(), store.clean_id(kept_id, "kept object"))
    if not os.path.isfile(os.path.join(path, "object.json")):
        raise StoreError("no such kept object", 404)
    return path


def listed():
    found = []
    base = folder()
    if os.path.isdir(base):
        for name in os.listdir(base):
            described = store.read_json(os.path.join(base, name, "object.json"))
            if not described:
                continue
            found.append({"id": name, "name": described.get("name") or "", "picture": described.get("poster") or described.get("file"),
                          "solid": bool(described.get("model") or described.get("shape")), "kind": described.get("kind"),
                          "kept_at": os.path.getmtime(os.path.join(base, name, "object.json"))})
    found.sort(key=lambda entry: -entry["kept_at"])
    return found


def keep(project, world, described):
    """An object of a world, as the page describes it, is kept: its files are copied, its place is left out."""
    source = store.world_dir(project, world, create=False)
    kept_id = "k" + format(time.time_ns() // 1000000, "x")
    target = os.path.join(folder(), kept_id)
    os.makedirs(target)
    kept = {key: value for key, value in dict(described).items() if key not in PLACE}
    kept["was"] = str(described.get("id") or "")
    try:
        for field in FILES:
            name = kept.get(field)
            if not name:
                continue
            name = os.path.basename(str(name))
            if not os.path.isfile(os.path.join(source, name)):
                raise StoreError(f"the object's file {name} is missing")
            shutil.copy2(os.path.join(source, name), os.path.join(target, name))
        store.write_json(os.path.join(target, "object.json"), kept)
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise
    return {"id": kept_id, "name": kept.get("name") or ""}


def use(kept_id, project, world, object_id):
    """A kept object is put into a world as a new object: its files are copied under the new object's name.
    Returns what the object is, for the page to stand it somewhere."""
    source = _entry(kept_id)
    target = store.world_dir(project, world)
    object_id = store.clean_id(object_id, "object")
    described = store.read_json(os.path.join(source, "object.json")) or {}
    was = re.escape(str(described.pop("was", "")))
    for field in FILES:
        name = described.get(field)
        if not name:
            continue
        # object-<the old id>-0002.png -> object-<the new id>-0002.png
        renamed = re.sub(rf"^object-{was}-", f"object-{object_id}-", name) if was else name
        if renamed == name:
            renamed = f"object-{object_id}-{name}"
        shutil.copy2(os.path.join(source, os.path.basename(name)), os.path.join(target, renamed))
        described[field] = renamed
    described["history"] = []
    return described


def drop(kept_id):
    shutil.rmtree(_entry(kept_id))
    return True


def file(kept_id, name):
    path = os.path.join(_entry(kept_id), os.path.basename(name))
    if not os.path.isfile(path):
        raise StoreError("no such file", 404)
    return path

"""Projects on disk.

    <root>/<project>/project.json                the whole project as the page keeps it (world, chains, camera)
    <root>/<project>/project.json.bak            the one before the last save
    <root>/<project>/uploads/                    files the user gave (a video, pictures, a panorama, music)
    <root>/<project>/chains/<chain>/             one folder per chain:
        source/                                      the chain's own source frames (pictures, or Deforum's)
        alpha/000000.png                             the cut of every source frame (8 bits), kept so that the
                                                     frames can be aligned again without cutting again
        v0003/000000.webp                            the frames as the page shows them: cut, cleaned, aligned;
                                                     every new make gets the next number (the browser keeps
                                                     numbered files for good), the older ones are removed
        chain.json                                   what was measured, which frames look wrong, the make
    <root>/_deleted/                             deleted projects (moved, never erased)
    <webui>/output/time-strata/                  recorded videos, unless Settings name another folder

The page is the only writer of project.json. Jobs write a chain's folder and say what they made.
"""

import json
import os
import re
import shutil
import threading
import time

from . import FORMAT, VERSION

_FORBIDDEN = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_ID = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
_writing = threading.Lock()


class StoreError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _webui():
    try:
        from modules import paths
        return paths.data_path
    except Exception:                                   # tests, outside Forge
        return os.environ.get("STRATA_WEBUI") or os.getcwd()


def _option(name):
    try:
        from modules import shared
        return str(shared.opts.data.get(name) or "").strip()
    except Exception:
        return ""


def root():
    chosen = _option("strata_projects_dir") or os.environ.get("STRATA_ROOT", "")
    folder = os.path.abspath(chosen) if chosen else os.path.join(_webui(), "time-strata")
    os.makedirs(folder, exist_ok=True)
    return folder


def exports_dir():
    chosen = _option("strata_exports_dir")
    folder = os.path.abspath(chosen) if chosen else os.path.join(_webui(), "output", "time-strata")
    os.makedirs(folder, exist_ok=True)
    return folder


def clean_name(name, what="name"):
    name = _FORBIDDEN.sub(" ", str(name or "")).strip().strip(".").strip()
    name = re.sub(r"\s+", " ", name)[:80]
    if not name or name.startswith("_"):
        raise StoreError(f"not a usable {what}: {name!r}")
    return name


def clean_id(value, what="id"):
    value = str(value or "")
    if not _ID.match(value):
        raise StoreError(f"not a usable {what}: {value!r}")
    return value


def project_dir(name, must_exist=True):
    folder = os.path.join(root(), clean_name(name, "project name"))
    if must_exist and not os.path.isfile(os.path.join(folder, "project.json")):
        raise StoreError(f"no project named {name!r}", 404)
    return folder


def chain_dir(project, chain, create=False):
    folder = os.path.join(project_dir(project), "chains", clean_id(chain, "chain id"))
    if create:
        os.makedirs(folder, exist_ok=True)
    elif not os.path.isdir(folder):
        raise StoreError(f"no chain {chain!r} in {project!r}", 404)
    return folder


def uploads_dir(project):
    folder = os.path.join(project_dir(project), "uploads")
    os.makedirs(folder, exist_ok=True)
    return folder


def resolve(project, path):
    """A file of a project, by its path inside the project folder; nothing outside it."""
    base = os.path.realpath(project_dir(project))
    full = os.path.realpath(os.path.join(base, str(path or "").replace("\\", "/").lstrip("/")))
    if not full.startswith(base + os.sep) or not os.path.isfile(full):
        raise StoreError("no such file", 404)
    return full


def relative(project, full):
    return os.path.relpath(full, project_dir(project)).replace("\\", "/")


# --- the project document ------------------------------------------------------------------------------------------

def new_doc(name):
    now = time.time()
    return {
        "format": FORMAT, "version": VERSION, "name": name, "created": now, "modified": now,
        "shot": {"duration": 10.0, "fps": 30, "width": 1080, "height": 1920},
        "world": {"kind": "sky", "panorama": None, "eye": 1.6, "radius": 90.0, "turn": 0.0,
                  "sky_top": "#5d5fa8", "sky_horizon": "#e4bfd6", "ground": "#34501f", "fog": 0.25},
        "chains": [],
        "camera": {"follow": None, "keys": []},
        "knife": {"on": False, "keys": []},
        "music": None,
    }


def _read_json(path):
    with open(path, "r", encoding="utf8") as file:
        return json.load(file)


def _write_json(path, data):
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf8") as file:
        json.dump(data, file, ensure_ascii=False, indent=1)
    os.replace(temporary, path)


def list_projects():
    found = []
    base = root()
    for entry in sorted(os.listdir(base), key=str.lower):
        path = os.path.join(base, entry, "project.json")
        if entry.startswith("_") or not os.path.isfile(path):
            continue
        try:
            doc = _read_json(path)
        except Exception:
            continue
        found.append({"name": entry, "modified": doc.get("modified") or os.path.getmtime(path),
                      "chains": len(doc.get("chains") or [])})
    found.sort(key=lambda p: -float(p["modified"] or 0))
    return found


def create(name):
    name = clean_name(name, "project name")
    folder = os.path.join(root(), name)
    if os.path.exists(folder):
        raise StoreError(f"a project named {name!r} already exists", 409)
    os.makedirs(os.path.join(folder, "chains"))
    doc = new_doc(name)
    _write_json(os.path.join(folder, "project.json"), doc)
    return doc


def load(name):
    doc = _read_json(os.path.join(project_dir(name), "project.json"))
    doc["name"] = clean_name(name)
    return doc


def save(name, doc):
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        raise StoreError("not a Time Strata project")
    folder = project_dir(name)
    path = os.path.join(folder, "project.json")
    with _writing:
        doc["modified"] = time.time()
        doc["name"] = clean_name(name)
        if os.path.isfile(path):
            shutil.copyfile(path, path + ".bak")
        _write_json(path, doc)
    return {"modified": doc["modified"]}


def delete(name):
    folder = project_dir(name)
    target = os.path.join(root(), "_deleted", f"{os.path.basename(folder)} {time.strftime('%Y-%m-%d %H-%M-%S')}")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    shutil.move(folder, target)
    return {"moved_to": target}


def read_chain(project, chain):
    path = os.path.join(chain_dir(project, chain), "chain.json")
    return _read_json(path) if os.path.isfile(path) else None


def write_chain(project, chain, data):
    _write_json(os.path.join(chain_dir(project, chain, create=True), "chain.json"), data)


def next_make(project, chain):
    """The folder for a new make of a chain's frames: v0001, v0002 …"""
    folder = chain_dir(project, chain, create=True)
    numbers = [int(n[1:]) for n in os.listdir(folder) if re.match(r"^v\d{4}$", n)]
    make = f"v{max(numbers or [0]) + 1:04d}"
    os.makedirs(os.path.join(folder, make))
    return make, os.path.join(folder, make)


def drop_old_makes(project, chain, keep):
    """Removes the makes of a chain other than `keep` (they are only ever shown, never edited)."""
    folder = chain_dir(project, chain)
    for name in os.listdir(folder):
        if re.match(r"^v\d{4}$", name) and name != keep:
            shutil.rmtree(os.path.join(folder, name), ignore_errors=True)


def delete_chain(project, chain):
    folder = chain_dir(project, chain)
    target = os.path.join(root(), "_deleted", f"{clean_name(project)} - chain {chain} {time.strftime('%Y-%m-%d %H-%M-%S')}")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    shutil.move(folder, target)
    return {"moved_to": target}

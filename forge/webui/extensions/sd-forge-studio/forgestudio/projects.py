"""Projects: one folder each, holding the edit (project.json) and studio.json.

project.json is Forge Studio's own document: the sequence's settings, its tracks and clips, every time in seconds.
The page edits it and saves it whole; the server renders exports from it (render.py). studio.json names each file
the project uses (its "web" name, which the page sees) and keeps where the file really is.

Projects made by the first version (FilmCraft's project.fcproj) are turned into project.json the first time they
are opened; the .fcproj stays beside it, untouched.
"""

import json
import os
import re
import shutil
import time
import uuid

from . import paths

BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
KEEP_HISTORY = 40
DOC_VERSION = 2
TICKS = 254016000000  # FilmCraft's time base, for reading its projects


class ProjectError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _folder(name, must_exist=True):
    name = (name or "").strip()
    if not name or BAD.search(name) or name in (".", "..") or name.endswith("."):
        raise ProjectError(f"not a usable project name: {name!r}")
    folder = os.path.join(paths.projects_dir(), name)
    if must_exist and not os.path.isdir(folder):
        raise ProjectError(f"no project named {name}", 404)
    return folder


def _write(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _studio(folder):
    path = os.path.join(folder, "studio.json")
    if not os.path.isfile(path):
        return {"version": 1, "media": {}, "created": time.time()}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _save_studio(folder, studio):
    studio["saved"] = time.time()
    _write(os.path.join(folder, "studio.json"), json.dumps(studio, ensure_ascii=False, indent=1))


def list_projects():
    root = paths.projects_dir()
    out = []
    for name in os.listdir(root):
        folder = os.path.join(root, name)
        if os.path.isdir(folder):
            saved = [os.path.getmtime(os.path.join(folder, f)) for f in ("project.json", "project.fcproj")
                     if os.path.isfile(os.path.join(folder, f))]
            out.append({"name": name, "saved": max(saved) if saved else os.path.getmtime(folder)})
    return sorted(out, key=lambda p: -p["saved"])


def empty_doc(width=1080, height=1920, fps=30):
    return {"version": DOC_VERSION, "settings": {"width": width, "height": height, "fps": fps, "background": "#000000"},
            "tracks": [{"id": "t1", "kind": "video", "main": True}, {"id": "t2", "kind": "audio"}],
            "clips": [], "next": 1}


def create(name):
    folder = _folder(name, must_exist=False)
    if os.path.exists(folder):
        raise ProjectError(f"a project named {name} already exists", 409)
    os.makedirs(os.path.join(folder, "media"))
    _save_studio(folder, {"version": 1, "media": {}, "created": time.time()})
    _write(os.path.join(folder, "project.json"), json.dumps(empty_doc(), ensure_ascii=False, indent=1))
    return load(name)


def load(name):
    folder = _folder(name)
    studio = _studio(folder)
    doc_path = os.path.join(folder, "project.json")
    migrated = False
    if not os.path.isfile(doc_path):
        old = os.path.join(folder, "project.fcproj")
        doc = from_filmcraft(old) if os.path.isfile(old) else empty_doc()
        _write(doc_path, json.dumps(doc, ensure_ascii=False, indent=1))
        migrated = os.path.isfile(old)
    with open(doc_path, encoding="utf-8") as f:
        doc = json.load(f)
    media = {web: {"path": m["path"], "missing": not os.path.isfile(m["path"])} for web, m in studio["media"].items()}
    return {"name": name, "folder": folder, "media": media, "doc": doc, "migrated": migrated, "saved": studio.get("saved")}


def save_doc(name, doc):
    """Writes the edit; the last KEEP_HISTORY versions stay in history\\."""
    folder = _folder(name)
    if not isinstance(doc, dict) or doc.get("version") != DOC_VERSION or not isinstance(doc.get("clips"), list):
        raise ProjectError("that is not a Forge Studio project")
    text = json.dumps(doc, ensure_ascii=False, indent=1)
    target = os.path.join(folder, "project.json")
    if os.path.isfile(target):
        with open(target, encoding="utf-8") as f:
            if f.read() == text:
                return {"changed": False}
        history = os.path.join(folder, "history")
        os.makedirs(history, exist_ok=True)
        shutil.copy2(target, os.path.join(history, time.strftime("project-%Y%m%d-%H%M%S.json")))
        old = sorted(f for f in os.listdir(history) if f.endswith(".json"))
        for f in old[:-KEEP_HISTORY]:
            os.remove(os.path.join(history, f))
    _write(target, text)
    return {"changed": True}


def _unique(studio, filename):
    stem, ext = os.path.splitext(os.path.basename(filename))
    web, n = stem + ext, 2
    while web in studio["media"]:
        web, n = f"{stem} ({n}){ext}", n + 1
    return web


def add_media(name, path):
    """Give a file on disk a name inside the project (the same file keeps the name it already has)."""
    folder = _folder(name)
    path = os.path.normpath(path)
    if not os.path.isfile(path):
        raise ProjectError(f"no such file: {path}", 404)
    studio = _studio(folder)
    for web, m in studio["media"].items():
        if os.path.normcase(m["path"]) == os.path.normcase(path):
            return web
    web = _unique(studio, path)
    studio["media"][web] = {"path": path, "added": time.time()}
    _save_studio(folder, studio)
    return web


def media_path(name, web):
    m = _studio(_folder(name))["media"].get(web)
    if not m:
        raise ProjectError(f"{web} is not part of project {name}", 404)
    return m["path"]


def store_upload(name, web, data):
    """A file brought in from the user's own disk: a copy is kept in the project's media folder."""
    folder = _folder(name)
    studio = _studio(folder)
    if web in studio["media"] and os.path.isfile(studio["media"][web]["path"]):
        return web
    safe = BAD.sub("_", os.path.basename(web)) or "media"
    target = os.path.join(folder, "media", safe)
    with open(target + ".tmp", "wb") as f:
        f.write(data)
    os.replace(target + ".tmp", target)
    web = _unique(studio, target) if web in studio["media"] else web
    studio["media"][web] = {"path": target, "added": time.time(), "uploaded": True}
    _save_studio(folder, studio)
    return web


# ---- the first version's projects ----

def _web_of_item(items, item_id):
    item = items.get(str(item_id)) or {}
    media = (item.get("kind") or {}).get("Media") or {}
    path = ((media.get("media") or {}).get("File") or {}).get("path") or ""
    return path[len("/files/"):] if path.startswith("/files/") else None


def from_filmcraft(path):
    """A FilmCraft 0.13 project (as the first Forge Studio saved it) as a Forge Studio document: its first sequence's
    clips with their place, trim and speed. Titles, captions and effects of that version are not carried over."""
    with open(path, encoding="utf-8") as f:
        fc = json.load(f)
    items = fc.get("project", {}).get("items", {})
    sequences = [v["kind"]["Sequence"] for v in items.values() if "Sequence" in (v.get("kind") or {})]
    if not sequences:
        return empty_doc()
    seq = sequences[0]
    st = seq.get("settings", {})
    rate = st.get("frame_rate") or {"num": 30, "den": 1}
    doc = empty_doc(int(st.get("width", 1080)), int(st.get("height", 1920)),
                    round(rate.get("num", 30) / max(1, rate.get("den", 1)), 3))
    doc["tracks"] = []
    n = 1
    for kind, tracks in (("video", seq.get("video_tracks", [])), ("audio", seq.get("audio_tracks", []))):
        for i, track in enumerate(tracks):
            tid = f"t{len(doc['tracks']) + 1}"
            doc["tracks"].append({"id": tid, "kind": kind, **({"main": True} if kind == "video" and i == 0 else {})})
            for c in track.get("items", []):
                web = _web_of_item(items, c.get("item"))
                if not web:
                    continue
                doc["clips"].append({"id": f"c{n}", "track": tid, "kind": kind if kind == "audio" else "video",
                                     "media": web, "start": c.get("start", 0) / TICKS, "duration": c.get("duration", 0) / TICKS,
                                     "in": c.get("source_in", 0) / TICKS, "speed": float(c.get("speed") or 1.0)})
                n += 1
    # empty tracks dropped, but always a main video track and an audio track
    used = {c["track"] for c in doc["clips"]}
    doc["tracks"] = [t for t in doc["tracks"] if t["id"] in used or t.get("main")]
    if not any(t["kind"] == "audio" for t in doc["tracks"]):
        doc["tracks"].append({"id": f"t{len(doc['tracks']) + 10}", "kind": "audio"})
    doc["next"] = n
    return doc


def new_id(prefix="c"):
    return prefix + uuid.uuid4().hex[:8]

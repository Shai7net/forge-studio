"""The projects window (like a classic editor's home): every project with a cover picture, its length and its size on
disk, and a trash that projects can be moved to and brought back from (nothing is ever deleted here: emptying the
trash is left to the user, in Explorer).

The cover is the first picture or video on the edit's main track (else on any picture track). The size is what the
project takes: its folder (media copied in, frames, history) and the files outside it that it uses, each once.
Sizes are kept per folder until the project is saved again.
"""

import json
import os
import shutil
import time

from . import paths, projects

_sizes = {}


def _folder_size(folder, stamp, studio):
    hit = _sizes.get(folder)
    if hit and hit[0] == stamp:
        return hit[1]
    total = 0
    for dp, _dn, files in os.walk(folder):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(dp, f))
            except OSError:
                pass
    inside = os.path.normcase(os.path.normpath(folder)) + os.sep
    outside = {os.path.normcase(os.path.normpath(m.get("path") or "")) for m in (studio.get("media") or {}).values()}
    for p in outside:
        if p and not p.startswith(inside):
            try:
                total += os.path.getsize(p)
            except OSError:
                pass
    _sizes[folder] = (stamp, total)
    return total


def _read_doc(folder):
    try:
        with open(os.path.join(folder, "project.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _cover(doc, studio):
    """The web name of the picture to show for the project (or None)."""
    tracks = doc.get("tracks") or []
    shown = [t for t in tracks if t.get("kind") != "audio" and not t.get("hidden")]
    main = next((t["id"] for t in shown if t.get("main")), shown[0]["id"] if shown else None)
    media = studio.get("media") or {}
    usable = [c for c in doc.get("clips") or [] if c.get("kind") in ("video", "image") and c.get("media") in media
              and os.path.isfile(media[c["media"]].get("path", ""))]
    usable.sort(key=lambda c: (c.get("track") != main, float(c.get("start") or 0)))
    return usable[0]["media"] if usable else None


def _describe(folder, name, saved, extra=None):
    doc = _read_doc(folder)
    studio = projects._studio(folder) if os.path.isfile(os.path.join(folder, "studio.json")) else {}
    clips = doc.get("clips") or []
    st = doc.get("settings") or {}
    out = {"name": name, "saved": saved,
           "duration": max([float(c.get("start") or 0) + float(c.get("duration") or 0) for c in clips] or [0.0]),
           "clips": len(clips), "width": st.get("width"), "height": st.get("height"),
           "cover": _cover(doc, studio), "size": _folder_size(folder, saved, studio)}
    out.update(extra or {})
    return out


def overview():
    root = paths.projects_dir()
    return [_describe(os.path.join(root, p["name"]), p["name"], p["saved"], {"empty": p["empty"]})
            for p in projects.list_projects()]


def trash_dir():
    return paths.sub("trash")


def trashed():
    root = trash_dir()
    out = []
    for entry in os.listdir(root):
        folder = os.path.join(root, entry)
        if not os.path.isdir(folder):
            continue
        meta = {}
        try:
            with open(os.path.join(folder, "trashed.json"), encoding="utf-8") as f:
                meta = json.load(f)
        except (OSError, ValueError):
            pass
        out.append(_describe(folder, entry, os.path.getmtime(folder),
                             {"trashed": meta.get("time", os.path.getmtime(folder)), "was": meta.get("name", entry)}))
    return sorted(out, key=lambda p: -p["trashed"])


def _free(root, name):
    out, n = name, 2
    while os.path.exists(os.path.join(root, out)):
        out = f"{name} ({n})"
        n += 1
    return out


def _repoint(folder, old):
    """studio.json keeps where each file is: files inside the project folder move with it."""
    path = os.path.join(folder, "studio.json")
    if not os.path.isfile(path):
        return
    studio = projects._studio(folder)
    old_n = os.path.normcase(os.path.normpath(old))
    changed = False
    for m in (studio.get("media") or {}).values():
        p = m.get("path") or ""
        if os.path.normcase(os.path.normpath(p)).startswith(old_n + os.sep):
            m["path"] = os.path.join(folder, os.path.normpath(p)[len(old_n) + 1:])
            changed = True
    if changed:
        projects._save_studio(folder, studio)


def trash(name):
    folder = projects._folder(name)
    root = trash_dir()
    dest = os.path.join(root, _free(root, name))
    shutil.move(folder, dest)
    _repoint(dest, folder)
    with open(os.path.join(dest, "trashed.json"), "w", encoding="utf-8") as f:
        json.dump({"name": name, "time": time.time()}, f, ensure_ascii=False)
    return {"trashed": os.path.basename(dest)}


def restore(entry):
    root = trash_dir()
    folder = os.path.join(root, entry)
    if not entry or os.path.dirname(os.path.normpath(folder)) != os.path.normpath(root) or not os.path.isdir(folder):
        raise projects.ProjectError(f"{entry} is not in the trash", 404)
    name = entry
    try:
        with open(os.path.join(folder, "trashed.json"), encoding="utf-8") as f:
            name = json.load(f).get("name") or entry
    except (OSError, ValueError):
        pass
    target_root = paths.projects_dir()
    dest = os.path.join(target_root, _free(target_root, name))
    shutil.move(folder, dest)
    _repoint(dest, folder)
    try:
        os.remove(os.path.join(dest, "trashed.json"))
    except OSError:
        pass
    return {"name": os.path.basename(dest)}


def cover_path(name, trashed_entry=False):
    """The cover file of a project (or of a project in the trash)."""
    folder = os.path.join(trash_dir(), name) if trashed_entry else projects._folder(name)
    if trashed_entry and os.path.dirname(os.path.normpath(folder)) != os.path.normpath(trash_dir()):
        raise projects.ProjectError("not in the trash", 404)
    studio = projects._studio(folder)
    web = _cover(_read_doc(folder), studio)
    if not web:
        raise projects.ProjectError("this project has no picture yet", 404)
    return studio["media"][web]["path"]

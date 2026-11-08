"""Projects on disk.

    <root>/<project>/project.json            the whole project as the page keeps it (worlds, portals, tours)
    <root>/<project>/worlds/<world>/         one folder per world:
        pano-0003.png | video-0002.mp4           the panorama; every change gets the next number, the older
                                                 ones stay, so a change can be taken back
        poster-0002.jpg                          the first frame of a video world
        depth-0003.png                           how far each direction is (16 bits in the red and green bytes)
        thumb-0003.jpg                           the small picture in the worlds strip
        portal-<id>-0002.png                     the mask of each portal (a new file when its shape changes)
        object-<id>-0002.png, …-depth.png, .mp4  the objects that stand in the world (see objects.py)
    <root>/<project>/versions/               older states of project.json: kept automatically every few
                                             minutes, by the user at any time, and before every restore
    <root>/_deleted/                         deleted projects and worlds (moved, never erased)
    <webui>/output/simulation/               recorded videos and packed projects, of all projects, unless Settings
                                             name another folder

The page is the only writer of project.json. Jobs only add files to a world's folder and tell the page
what they made. No file of a world is ever changed or erased, which is what makes a version cheap: an older
project.json still finds every file it names.
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


def root():
    from modules import paths, shared
    chosen = str(shared.opts.data.get("sim360_projects_dir") or "").strip()
    return os.path.abspath(chosen) if chosen else os.path.join(paths.data_path, "simulation-360")


def exports_dir(project=None):
    """Where recorded videos and packed projects go: one folder for all projects, next to Forge's other
    outputs, unless Settings name another."""
    from modules import paths, shared
    chosen = str(shared.opts.data.get("sim360_exports_dir") or "").strip()
    folder = os.path.abspath(chosen) if chosen else os.path.join(paths.data_path, "output", "simulation")
    os.makedirs(folder, exist_ok=True)
    return folder


def clean_name(name, what="name"):
    name = _FORBIDDEN.sub(" ", str(name or "")).strip().strip(".").strip()
    name = re.sub(r"\s+", " ", name)[:80].strip()
    if not name or name.startswith("_"):
        raise StoreError(f"bad {what}")
    return name


def clean_id(value, what="id"):
    value = str(value or "")
    if not _ID.match(value):
        raise StoreError(f"bad {what}")
    return value


def project_dir(project, create=False):
    path = os.path.join(root(), clean_name(project, "project name"))
    if create:
        os.makedirs(path, exist_ok=True)
    elif not os.path.isdir(path):
        raise StoreError("no such project", 404)
    return path


def world_dir(project, world, create=True):
    path = os.path.join(project_dir(project), "worlds", clean_id(world, "world"))
    if create:
        os.makedirs(path, exist_ok=True)
    return path


def read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf8") as file:
            return json.load(file)
    except FileNotFoundError:
        return default
    except Exception as e:
        print(f"[Simulation 360] cannot read {path}: {e}")
        return default


def write_json(path, data):
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf8") as file:
        json.dump(data, file, indent=1, ensure_ascii=False)
    os.replace(temporary, path)


def stamp():
    return time.strftime("%Y%m%d-%H%M%S")


# --- projects ----------------------------------------------------------------------------------------------------

def list_projects():
    base = root()
    found = []
    if os.path.isdir(base):
        for name in os.listdir(base):
            path = os.path.join(base, name, "project.json")
            if name.startswith("_") or not os.path.isfile(path):
                continue
            doc = read_json(path, {}) or {}
            worlds = doc.get("worlds") or []
            first = worlds[0] if worlds else {}
            found.append({
                "name": name,
                "saved_at": os.path.getmtime(path),
                "worlds": len(worlds),
                "thumb": f"worlds/{first.get('id')}/{first.get('thumb')}" if first.get("thumb") else None,
            })
    found.sort(key=lambda p: -p["saved_at"])
    return found


def empty_project(name):
    return {"format": FORMAT, "version": VERSION, "name": name, "created": time.strftime("%Y-%m-%d %H:%M:%S"),
            "start_world": None, "worlds": [], "tours": [], "settings": {}}


def create_project(name):
    name = clean_name(name, "project name")
    folder = os.path.join(root(), name)
    if os.path.exists(os.path.join(folder, "project.json")):
        raise StoreError("a project with this name already exists", 409)
    os.makedirs(os.path.join(folder, "worlds"), exist_ok=True)
    doc = empty_project(name)
    write_json(os.path.join(folder, "project.json"), doc)
    return doc


def load_project(name):
    doc = read_json(os.path.join(project_dir(name), "project.json"))
    if not isinstance(doc, dict):
        raise StoreError("the project file cannot be read", 500)
    return doc


def save_project(name, doc):
    """The page saves by itself after every change. Before the file is replaced, what it held is put aside
    as an automatic version when the last one is older than a few minutes, so a change that went wrong can
    be taken back even after it was saved over."""
    if not isinstance(doc, dict) or not isinstance(doc.get("worlds"), list):
        raise StoreError("not a project")
    folder = project_dir(name)
    doc = {**doc, "format": FORMAT, "version": VERSION, "name": clean_name(name), "saved": time.strftime("%Y-%m-%d %H:%M:%S")}
    doc.pop("version_label", None)
    with _writing:
        path = os.path.join(folder, "project.json")
        if os.path.isfile(path):
            autos = sorted(f for f in _version_files(folder) if f.startswith("auto-"))
            newest = os.path.getmtime(os.path.join(folder, "versions", autos[-1])) if autos else 0
            if time.time() - newest >= AUTO_EVERY:
                _keep_version(folder, "auto", "")
                for old in autos[:max(0, len(autos) + 1 - AUTO_KEPT)]:
                    try:
                        os.remove(os.path.join(folder, "versions", old))
                    except OSError:
                        pass
        write_json(path, doc)
    return {"saved": doc["saved"], "saved_at": time.time()}


# --- versions of a project ---------------------------------------------------------------------------------------
#
# A version is a copy of project.json. That is all it takes: the files of a world are never changed or
# erased (every change writes a new file), so an older project.json still finds everything it names.

AUTO_EVERY = 300            # seconds between automatic versions
AUTO_KEPT = 40


def _version_files(folder):
    versions = os.path.join(folder, "versions")
    return [f for f in os.listdir(versions) if f.endswith(".json")] if os.path.isdir(versions) else []


def _keep_version(folder, kind, label):
    source = os.path.join(folder, "project.json")
    if not os.path.isfile(source):
        return None
    versions = os.path.join(folder, "versions")
    os.makedirs(versions, exist_ok=True)
    name = f"{kind}-{stamp()}.json"
    n = 1
    while os.path.exists(os.path.join(versions, name)):
        n += 1
        name = f"{kind}-{stamp()}-{n}.json"
    doc = read_json(source, {}) or {}
    doc["version_label"] = str(label or "")[:120]
    write_json(os.path.join(versions, name), doc)
    return name


def list_versions(project):
    folder = project_dir(project)
    found = []
    for name in _version_files(folder):
        path = os.path.join(folder, "versions", name)
        doc = read_json(path, {}) or {}
        found.append({"file": name, "auto": name.startswith("auto-"), "label": doc.get("version_label") or "",
                      "saved_at": os.path.getmtime(path), "worlds": len(doc.get("worlds") or []),
                      "names": [w.get("name") for w in (doc.get("worlds") or [])][:12]})
    found.sort(key=lambda v: -v["saved_at"])
    return found


def save_version(project, label=""):
    with _writing:
        name = _keep_version(project_dir(project), "saved", label)
    if name is None:
        raise StoreError("the project has not been saved yet", 409)
    return name


def restore_version(project, file):
    """Makes a version the project again. What the project is now is kept as a version first."""
    folder = project_dir(project)
    file = os.path.basename(str(file))
    path = os.path.join(folder, "versions", file)
    doc = read_json(path)
    if not isinstance(doc, dict) or not isinstance(doc.get("worlds"), list):
        raise StoreError("no such version", 404)
    with _writing:
        _keep_version(folder, "before-restore", "")
        doc.pop("version_label", None)
        doc["name"] = clean_name(project)
        write_json(os.path.join(folder, "project.json"), doc)
    return doc


def delete_version(project, file):
    path = os.path.join(project_dir(project), "versions", os.path.basename(str(file)))
    if not os.path.isfile(path):
        raise StoreError("no such version", 404)
    os.remove(path)


def usage(project):
    """How much disk the project takes, in bytes."""
    total = 0
    for base, _dirs, files in os.walk(project_dir(project)):
        total += sum(os.path.getsize(os.path.join(base, f)) for f in files)
    return {"bytes": total}


def duplicate_project(project, new_name):
    source = project_dir(project)
    new_name = clean_name(new_name, "project name")
    target = os.path.join(root(), new_name)
    if os.path.exists(target):
        raise StoreError("a project with this name already exists", 409)
    shutil.copytree(source, target, ignore=shutil.ignore_patterns("exports", "_incoming-*", "_animatediff"))
    path = os.path.join(target, "project.json")
    doc = read_json(path, {}) or {}
    doc["name"] = new_name
    write_json(path, doc)
    return new_name


def pack_project(project):
    """The whole project as one .zip in the recordings folder: to keep, or to open on another computer."""
    import zipfile
    folder = project_dir(project)
    target = os.path.join(exports_dir(project), f"{clean_name(project)} {stamp()}.zip")
    with zipfile.ZipFile(target, "w", zipfile.ZIP_STORED) as archive:      # pictures and videos are compressed already
        for base, dirs, files in os.walk(folder):
            relative = os.path.relpath(base, folder)
            top = relative.split(os.sep)[0]
            if top == "exports":
                dirs[:] = []
                continue
            for name in files:
                if name.startswith("_incoming-") or name.endswith(".tmp"):
                    continue
                archive.write(os.path.join(base, name), os.path.normpath(os.path.join(relative, name)))
    return target


def unpack_project(path, wanted_name):
    """Opens a packed project as a new project. Nothing in the file can be written outside its folder."""
    import zipfile
    name = clean_name(wanted_name, "project name")
    stem, n = name, 1
    while os.path.exists(os.path.join(root(), name)):
        n += 1
        name = f"{stem} ({n})"
    target = os.path.join(root(), name)
    try:
        with zipfile.ZipFile(path) as archive:
            names = [i for i in archive.infolist() if not i.is_dir()]
            inside = "" if any(i.filename == "project.json" for i in names) else None
            if inside is None:      # packed with its folder
                tops = {i.filename.split("/")[0] for i in names}
                if len(tops) == 1 and any(i.filename == f"{next(iter(tops))}/project.json" for i in names):
                    inside = next(iter(tops)) + "/"
            if inside is None:
                raise StoreError("this file is not a Simulation 360 project")
            os.makedirs(target)
            base = os.path.realpath(target)
            for item in names:
                if not item.filename.startswith(inside):
                    continue
                relative = item.filename[len(inside):]
                goal = os.path.realpath(os.path.join(base, *[p for p in relative.split("/") if p not in ("", ".", "..")]))
                if not goal.startswith(base + os.sep):
                    continue
                os.makedirs(os.path.dirname(goal), exist_ok=True)
                with archive.open(item) as source, open(goal, "wb") as out:
                    shutil.copyfileobj(source, out)
    except zipfile.BadZipFile:
        raise StoreError("this file is not a .zip")
    project_file = os.path.join(target, "project.json")
    doc = read_json(project_file)
    if not isinstance(doc, dict) or not isinstance(doc.get("worlds"), list):
        shutil.rmtree(target, ignore_errors=True)
        raise StoreError("this file is not a Simulation 360 project")
    doc["name"] = name
    write_json(project_file, doc)
    return name


def _move_aside(path, label):
    folder = os.path.join(root(), "_deleted")
    os.makedirs(folder, exist_ok=True)
    target = os.path.join(folder, f"{label}.{stamp()}")
    os.replace(path, target)
    return target


def delete_project(name):
    return _move_aside(project_dir(name), clean_name(name))


def delete_world(project, world):
    path = world_dir(project, world, create=False)
    if not os.path.isdir(path):
        return None
    return _move_aside(path, f"{clean_name(project)}.{clean_id(world)}")


# --- a world's files ---------------------------------------------------------------------------------------------

def next_number(folder):
    """The number the next version of a world's panorama gets."""
    highest = 0
    for name in os.listdir(folder):
        match = re.match(r"^(?:pano|video)-(\d+)\.", name)
        if match:
            highest = max(highest, int(match.group(1)))
    return highest + 1


def resolve(project, relative):
    """A path inside a project, for serving a file. Nothing outside the project can be named."""
    base = os.path.realpath(project_dir(project))
    path = os.path.realpath(os.path.join(base, *[p for p in str(relative).replace("\\", "/").split("/") if p]))
    if path != base and not path.startswith(base + os.sep):
        raise StoreError("outside the project", 403)
    if not os.path.isfile(path):
        raise StoreError("no such file", 404)
    return path

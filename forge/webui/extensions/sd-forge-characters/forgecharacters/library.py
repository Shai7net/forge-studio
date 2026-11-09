"""The character library: one folder per character, grouped in project folders.

    output/characters/<project>/<name>/character.json   what the character is and how it was made
                                       sheet.png        the character sheet
                                       template.png     the pose template it was drawn on
                                       reference.png    the reference picture, when one was used
    output/characters/_templates/                        templates the user chose as default
    output/characters/_trash/                            deleted characters (moved, never erased)

Plain files on purpose: they can be browsed, copied and backed up like any other output.
"""

import json
import os
import re
import shutil
import time

TRASH = "_trash"
TEMPLATES = "_templates"
DEFAULT_PROJECT = "General"
CHARACTER_FILE = "character.json"
SHEET_FILE = "sheet.png"


def clean_name(name, fallback="character"):
    """A folder name from any name (Hebrew stays): no characters Windows refuses, no dots or spaces at the ends."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", str(name or "")).strip().strip(".").strip()
    name = re.sub(r"\s+", " ", name)[:60]
    return name if name and not name.startswith("_") else fallback


def projects(root):
    """The project folders, sorted (folders starting with "_" are the library's own)."""
    if not os.path.isdir(root):
        return []
    return sorted((d for d in os.listdir(root) if not d.startswith("_") and os.path.isdir(os.path.join(root, d))),
                  key=str.lower)


def _read(folder):
    try:
        with open(os.path.join(folder, CHARACTER_FILE), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def load(root, cid):
    """The character with id "<project>/<folder>", with its files' paths, or None."""
    folder = os.path.join(root, *cid.split("/"))
    data = _read(folder)
    if data is None:
        return None
    data = dict(data, id=cid, folder=folder, sheet=os.path.join(folder, SHEET_FILE))
    for key in ("template", "reference"):
        path = os.path.join(folder, f"{key}.png")
        data[f"{key}_path"] = path if os.path.isfile(path) else None
    return data


def characters(root, project=None, query=""):
    """Saved characters, newest first: all of them, or one project's; `query` matches the name, the project and
    the character's words."""
    query = (query or "").strip().lower()
    found = []
    for proj in projects(root):
        if project and proj != project:
            continue
        base = os.path.join(root, proj)
        for d in os.listdir(base):
            item = load(root, f"{proj}/{d}")
            if item is None or not os.path.isfile(item["sheet"]):
                continue
            if query:
                words = " ".join([item.get("name", ""), proj, item.get("idea", ""),
                                  json.dumps(item.get("character", {}), ensure_ascii=False)]).lower()
                if query not in words:
                    continue
            found.append(item)
    return sorted(found, key=lambda c: c.get("updated", 0), reverse=True)


def _free_folder(base, name):
    folder, n = os.path.join(base, name), 2
    while os.path.exists(folder):
        folder, n = os.path.join(base, f"{name} ({n})"), n + 1
    return folder


def save(root, project, name, sheet, data, template=None, reference=None):
    """A new character; returns its id. `sheet`, `template` and `reference` are picture files to copy in."""
    project = clean_name(project, DEFAULT_PROJECT)
    base = os.path.join(root, project)
    os.makedirs(base, exist_ok=True)
    folder = _free_folder(base, clean_name(name))
    os.makedirs(folder)
    shutil.copyfile(sheet, os.path.join(folder, SHEET_FILE))
    for key, path in (("template", template), ("reference", reference)):
        if path and os.path.isfile(path):
            _copy_png(path, os.path.join(folder, f"{key}.png"))
    now = time.time()
    record = dict(data or {}, name=str(name or "").strip() or os.path.basename(folder), project=project,
                  created=now, updated=now)
    _write(folder, record)
    return f"{project}/{os.path.basename(folder)}"


def update(root, cid, name=None, project=None):
    """Renames a character and/or moves it to another project; returns its (possibly new) id."""
    item = load(root, cid)
    if item is None:
        raise FileNotFoundError(cid)
    record = _read(item["folder"])
    name = str(name).strip() if name is not None and str(name).strip() else record.get("name", "")
    project = clean_name(project, DEFAULT_PROJECT) if project is not None and str(project).strip() else cid.split("/")[0]
    folder = item["folder"]
    if project != cid.split("/")[0] or clean_name(name) != os.path.basename(folder):
        base = os.path.join(root, project)
        os.makedirs(base, exist_ok=True)
        target = _free_folder(base, clean_name(name))
        shutil.move(folder, target)
        folder = target
        _drop_empty(os.path.join(root, cid.split("/")[0]))
    record.update(name=name, project=project, updated=time.time())
    _write(folder, record)
    return f"{project}/{os.path.basename(folder)}"


def trash(root, cid):
    """Moves a character to _trash (nothing is erased); returns where it went."""
    item = load(root, cid)
    if item is None:
        raise FileNotFoundError(cid)
    base = os.path.join(root, TRASH)
    os.makedirs(base, exist_ok=True)
    target = _free_folder(base, f"{time.strftime('%Y%m%d-%H%M%S')} {cid.replace('/', ' - ')}")
    shutil.move(item["folder"], target)
    _drop_empty(os.path.join(root, cid.split("/")[0]))
    return target


def _drop_empty(folder):
    try:
        if os.path.isdir(folder) and not os.listdir(folder):
            os.rmdir(folder)
    except OSError:
        pass


def _write(folder, record):
    tmp = os.path.join(folder, CHARACTER_FILE + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=1)
    os.replace(tmp, os.path.join(folder, CHARACTER_FILE))


def _copy_png(src, dest):
    if src.lower().endswith(".png"):
        shutil.copyfile(src, dest)
        return
    from PIL import Image
    with Image.open(src) as im:
        im.convert("RGB").save(dest)

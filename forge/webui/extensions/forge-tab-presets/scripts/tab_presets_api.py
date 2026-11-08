"""Storage for Tab Presets.

The page reads and writes the tab's settings by itself (javascript/tab-presets.js); this file only keeps
them on disk as JSON, one file per preset:

    <webui>/tab-presets/<tab>/<name>.json        the preset
    <webui>/tab-presets/<tab>/<name>.media/      images and uploaded files that belong to it
    <webui>/tab-presets/<tab>/_recent.json       what was saved or loaded last
    <webui>/tab-presets/<tab>/_last.json         the tab as it was when Generate was last pressed
    <webui>/tab-presets/<tab>/_last.media/       and its pictures and files
    <webui>/tab-presets/<tab>/_history/          the previous version of every overwritten preset
    <webui>/tab-presets/<tab>/_deleted/          deleted presets (moved, never erased)
"""

import base64
import hashlib
import json
import mimetypes
import os
import re
import shutil
import tempfile
import threading
import time

from fastapi import Body, FastAPI, HTTPException

from modules import paths, script_callbacks, shared

ROOT = os.path.join(paths.data_path, "tab-presets")
RECENT_FILE = "_recent.json"
LAST_FILE = "_last.json"
RECENT_KEPT = 20
# Format 1 presets took their "model" from the wrong checkpoint list on the page (Replacer's copy of it), so the
# page only loads the model of presets from format 2 on.
FORMAT = 2
HISTORY_KEPT = 5
MAX_UPLOAD_COPY = 4 * 1024 ** 3

# where Gradio keeps uploads; the real value is read from the app once it has started
upload_dir = os.environ.get("GRADIO_TEMP_DIR") or os.path.join(tempfile.gettempdir(), "gradio")

# two quick presses of Generate must not write the same files at once
_writing = threading.Lock()

_FORBIDDEN = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_DATA_URL = re.compile(r"^data:([\w.+-]+/[\w.+-]+)?(;[\w=.+-]+)*;base64,", re.I)


def clean_name(name, what="name"):
    name = _FORBIDDEN.sub(" ", str(name or "")).strip().strip(".").strip()
    name = re.sub(r"\s+", " ", name)[:80].strip()
    if not name or name.startswith("_"):
        raise HTTPException(status_code=400, detail=f"bad {what}")
    return name


def tab_dir(tab, create=False):
    path = os.path.join(ROOT, clean_name(tab, "tab"))
    if create:
        os.makedirs(path, exist_ok=True)
    return path


def preset_path(tab, name):
    return os.path.join(tab_dir(tab), clean_name(name) + ".json")


def media_dir(json_path):
    return json_path[:-len(".json")] + ".media"


def read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf8") as file:
            return json.load(file)
    except FileNotFoundError:
        return default
    except Exception as e:
        print(f"[Tab Presets] cannot read {path}: {e}")
        return default


def write_json(path, data):
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)
    os.replace(temporary, path)


def stamp():
    return time.strftime("%Y%m%d-%H%M%S")


# --- recent list -------------------------------------------------------------------------------------------------

def read_recent(tab):
    folder = tab_dir(tab)
    recent = read_json(os.path.join(folder, RECENT_FILE), [])
    if not isinstance(recent, list):
        return []
    return [r for r in recent if isinstance(r, dict) and os.path.isfile(os.path.join(folder, str(r.get("name")) + ".json"))]


def touch_recent(tab, name, action, forget=False):
    folder = tab_dir(tab, create=True)
    recent = [r for r in read_recent(tab) if r.get("name") != name]
    if not forget:
        recent.insert(0, {"name": name, "at": time.time(), "action": action})
    write_json(os.path.join(folder, RECENT_FILE), recent[:RECENT_KEPT])


# --- media -------------------------------------------------------------------------------------------------------

def _extension_for(mime, fallback=".bin"):
    known = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif"}
    return known.get((mime or "").lower()) or mimetypes.guess_extension(mime or "") or fallback


def store_data_url(folder, stem, data_url):
    match = _DATA_URL.match(data_url)
    if not match:
        return None
    mime = match.group(1) or "application/octet-stream"
    filename = stem + _extension_for(mime)
    with open(os.path.join(folder, filename), "wb") as file:
        file.write(base64.b64decode(data_url[match.end():]))
    return {"file": filename, "mime": mime}


def store_upload(folder, stem, item):
    """Keeps a file the page holds: one Gradio has on disk (an upload, or a file a preset put there earlier),
    or one the page carries inside the value itself (a dropped video)."""
    if not isinstance(item, dict):
        return None
    source = item.get("name")
    original = os.path.basename(str(item.get("orig_name") or source or "file"))
    filename = stem + os.path.splitext(original)[1][:12]
    target = os.path.join(folder, filename)
    data = item.get("data")
    if item.get("is_file") and source and os.path.isfile(source):
        if os.path.getsize(source) > MAX_UPLOAD_COPY:
            return None
        shutil.copyfile(source, target)
    elif isinstance(data, str) and _DATA_URL.match(data):
        with open(target, "wb") as file:
            file.write(base64.b64decode(data[_DATA_URL.match(data).end():]))
    else:
        return None
    return {"file": filename, "orig_name": original, "size": os.path.getsize(target)}


def keep_file(source, target):
    """The same file under a second name where the disk allows it, a copy where it does not."""
    try:
        os.link(source, target)
    except OSError:
        shutil.copyfile(source, target)


def store_media(json_path, media, previous=None):
    """Writes the media next to the preset and returns what the JSON should say about it.
    An entry of kind "kept" stands for a file the page did not send again; it is taken from `previous`, which is
    what the JSON said before."""
    final = media_dir(json_path)
    building = final + ".new"
    shutil.rmtree(building, ignore_errors=True)
    stored = {}
    for key, entry in (media or {}).items():
        if not isinstance(entry, dict):
            continue
        os.makedirs(building, exist_ok=True)
        # named after the field, so saving the same preset again writes the same files
        stem = hashlib.sha1(key.encode("utf8")).hexdigest()[:10]
        kind = entry.get("kind")
        try:
            if kind == "kept":
                old = (previous or {}).get(key) or {}
                names = [old.get("file")] if old.get("kind") == "image" else [f.get("file") for f in old.get("files") or []]
                if names and all(n and os.path.isfile(os.path.join(final, n)) for n in names):
                    for n in names:
                        keep_file(os.path.join(final, n), os.path.join(building, n))
                    stored[key] = old
            elif kind == "image" and isinstance(entry.get("data"), str):
                saved = store_data_url(building, stem, entry["data"])
                if saved:
                    stored[key] = {"kind": "image", **saved}
            elif kind == "files":
                files = []
                for n, item in enumerate(entry.get("files") or []):
                    saved = store_upload(building, f"{stem}-{n + 1}", item)
                    if saved:
                        files.append(saved)
                if files:
                    stored[key] = {"kind": "files", "many": bool(entry.get("many")), "files": files}
        except Exception as e:
            print(f"[Tab Presets] could not keep the file for '{key}': {e}")
    # the old folder stays in place until the new one is complete
    if os.path.isdir(final):
        shutil.rmtree(final, ignore_errors=True)
    if os.path.isdir(building):
        if stored:
            os.replace(building, final)
        else:
            shutil.rmtree(building, ignore_errors=True)
    return stored


def upload_copy(path, original):
    """A copy of a stored file in Gradio's upload folder, laid out the way Gradio lays out an upload.
    The page is given this copy, so nothing on screen depends on the presets folder: a preset can be saved over,
    renamed or deleted while its video is still in use."""
    digest = hashlib.sha1()
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(1 << 20), b""):
            digest.update(chunk)
    folder = os.path.join(upload_dir, digest.hexdigest())
    os.makedirs(folder, exist_ok=True)
    target = os.path.join(folder, os.path.basename(original))
    if not os.path.isfile(target) or os.path.getsize(target) != os.path.getsize(path):
        shutil.copyfile(path, target)
    return os.path.abspath(target)


def load_media(json_path, stored):
    """Turns the stored media back into values the page can put in its components."""
    folder = media_dir(json_path)
    media = {}
    for key, entry in (stored or {}).items():
        try:
            if entry.get("kind") == "image":
                path = os.path.join(folder, entry["file"])
                with open(path, "rb") as file:
                    encoded = base64.b64encode(file.read()).decode("ascii")
                media[key] = {"kind": "image", "data": f"data:{entry.get('mime') or 'image/png'};base64,{encoded}"}
            elif entry.get("kind") == "files":
                files = []
                for item in entry.get("files") or []:
                    path = os.path.join(folder, item["file"])
                    if os.path.isfile(path):
                        original = item.get("orig_name") or item["file"]
                        files.append({"name": upload_copy(path, original), "orig_name": original,
                                      "size": os.path.getsize(path), "data": None, "is_file": True})
                if files:
                    media[key] = {"kind": "files", "many": bool(entry.get("many")), "files": files}
        except Exception as e:
            print(f"[Tab Presets] could not read the file for '{key}': {e}")
    return media


# --- presets -----------------------------------------------------------------------------------------------------

def keep_history(tab, name, json_path):
    """Before a preset is overwritten, its previous version is put aside."""
    if not os.path.isfile(json_path):
        return
    folder = os.path.join(tab_dir(tab), "_history")
    os.makedirs(folder, exist_ok=True)
    shutil.copyfile(json_path, os.path.join(folder, f"{name}.{stamp()}.json"))
    kept = sorted(f for f in os.listdir(folder) if f.startswith(name + ".") and f.endswith(".json"))
    for old in kept[:-HISTORY_KEPT]:
        try:
            os.remove(os.path.join(folder, old))
        except OSError:
            pass


def describe(tab, filename):
    path = os.path.join(tab_dir(tab), filename)
    data = read_json(path, {}) or {}
    return {
        "name": filename[:-len(".json")],
        "saved_at": os.path.getmtime(path),
        "count": len(data.get("values") or {}),
        "media": len(data.get("media") or {}),
        "model": (data.get("model") or None) if (data.get("version") or 1) >= FORMAT else None,
    }


def api_list(tab: str):
    folder = tab_dir(tab)
    presets = []
    if os.path.isdir(folder):
        for filename in os.listdir(folder):
            if filename.endswith(".json") and not filename.startswith("_"):
                presets.append(describe(tab, filename))
    presets.sort(key=lambda p: p["name"].lower())
    last = os.path.join(folder, LAST_FILE)
    return {
        "presets": presets,
        "recent": read_recent(tab),
        "last": {"saved_at": os.path.getmtime(last)} if os.path.isfile(last) else None,
        "folder": folder,
    }


def save_last(tab, preset):
    """The tab as it was when Generate was pressed: one file per tab, replaced every time.
    The page sends a picture or a file only when it changed since the previous press."""
    path = os.path.join(tab_dir(tab, create=True), LAST_FILE)
    included = bool(preset.get("media_included"))
    sent = (preset.get("media") or {}) if included else {}
    with _writing:
        previous = (read_json(path, {}) or {}).get("media") or {}
        media = store_media(path, sent, previous)
        write_json(path, {
            "format": "forge-tab-preset",
            "version": FORMAT,
            "tab": tab,
            "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "model": preset.get("model"),
            "media_included": included,
            "media": media,
            "values": preset.get("values") or {},
            "layout": preset.get("layout") or {},
        })
    # what the page thought was here and is not: it sends those in full next time
    lost = [key for key, entry in sent.items() if isinstance(entry, dict) and entry.get("kind") == "kept" and key not in media]
    return {"name": "", "count": len(preset.get("values") or {}), "media": len(media), "lost": lost}


def api_save(tab: str = Body(...), name: str = Body(""), preset: dict = Body(...), overwrite: bool = Body(False),
             last: bool = Body(False)):
    if last:
        return save_last(tab, preset)
    name = clean_name(name)
    tab_dir(tab, create=True)
    path = preset_path(tab, name)
    if os.path.exists(path) and not overwrite:
        raise HTTPException(status_code=409, detail="exists")
    with _writing:
        keep_history(tab, name, path)
        # what the file is comes first, so it reads well when opened by hand
        data = {
            "format": "forge-tab-preset",
            "version": FORMAT,
            "tab": tab,
            "name": name,
            "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "model": preset.get("model"),
            "media_included": bool(preset.get("media_included")),
            "media": store_media(path, preset.get("media")),
            "values": preset.get("values") or {},
            "layout": preset.get("layout") or {},
        }
        write_json(path, data)
        touch_recent(tab, name, "save")
    return {"name": name, "count": len(data.get("values") or {}), "media": len(data["media"])}


def api_load(tab: str, name: str = "", mark: bool = True, last: bool = False):
    if last:
        path = os.path.join(tab_dir(tab), LAST_FILE)
        data = read_json(path)
        if not isinstance(data, dict):
            raise HTTPException(status_code=404, detail="nothing was generated yet")
        data["media"] = load_media(path, data.get("media"))
        return data
    path = preset_path(tab, name)
    data = read_json(path)
    if not isinstance(data, dict):
        raise HTTPException(status_code=404, detail="no such preset")
    data["media"] = load_media(path, data.get("media"))
    if mark:
        touch_recent(tab, clean_name(name), "load")
    return data


def api_delete(tab: str = Body(...), name: str = Body(...)):
    name = clean_name(name)
    path = preset_path(tab, name)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="no such preset")
    folder = os.path.join(tab_dir(tab), "_deleted")
    os.makedirs(folder, exist_ok=True)
    moved = f"{name}.{stamp()}"
    os.replace(path, os.path.join(folder, moved + ".json"))
    if os.path.isdir(media_dir(path)):
        os.replace(media_dir(path), os.path.join(folder, moved + ".media"))
    touch_recent(tab, name, "delete", forget=True)
    return {"moved_to": folder}


def api_open_folder(tab: str = Body(..., embed=True)):
    folder = tab_dir(tab, create=True)
    if getattr(shared.cmd_opts, "hide_ui_dir_config", False):
        raise HTTPException(status_code=403, detail="folders are hidden on this server")
    try:
        os.startfile(folder)
    except AttributeError:
        import subprocess
        import sys
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", folder])
    return {"folder": folder}


def on_app_started(_demo, app: FastAPI):
    global upload_dir
    upload_dir = getattr(app, "uploaded_file_dir", None) or upload_dir
    app.add_api_route("/tab-presets/list", api_list, methods=["GET"])
    app.add_api_route("/tab-presets/load", api_load, methods=["GET"])
    app.add_api_route("/tab-presets/save", api_save, methods=["POST"])
    app.add_api_route("/tab-presets/delete", api_delete, methods=["POST"])
    app.add_api_route("/tab-presets/open-folder", api_open_folder, methods=["POST"])


def on_ui_settings():
    section = ("tab_presets", "Tab Presets")
    shared.opts.add_option("tab_presets_hidden_tabs", shared.OptionInfo(
        "", "Tabs without the presets bar (tab ids separated by commas, for example: extras, svd)", section=section,
    ).needs_reload_ui())


script_callbacks.on_app_started(on_app_started)
script_callbacks.on_ui_settings(on_ui_settings)

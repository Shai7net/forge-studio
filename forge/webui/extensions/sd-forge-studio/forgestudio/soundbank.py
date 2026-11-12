"""Reusable sounds, separate from projects. Projects receive copies so removing a saved sound never breaks an edit."""

import hashlib
import io
import json
import os
import shutil
import threading
import time
import urllib.request
import urllib.parse
import uuid
import zipfile

from . import library, media, paths, projects

_lock = threading.RLock()
PACKS = {
    "interface": {"title": "Kenney Interface Sounds", "source": "https://kenney.nl/assets/interface-sounds",
                  "kind": "effects"},
    "jingles": {"title": "Kenney Music Jingles", "source": "https://kenney.nl/assets/music-jingles",
                "kind": "music"},
}


def _index():
    return os.path.join(paths.sub("sounds"), "index.json")


def _read():
    try:
        with open(_index(), encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return []


def _write(items):
    projects._write(_index(), json.dumps(items, ensure_ascii=False, indent=1))


def _path(item):
    return os.path.join(paths.sub("sounds", "files"), item["file"])


def listing():
    with _lock:
        return [{k: v for k, v in item.items() if k != "file"} for item in reversed(_read()) if os.path.isfile(_path(item))]


def get(sound_id):
    with _lock:
        item = next((x for x in _read() if x["id"] == sound_id), None)
        if not item or not os.path.isfile(_path(item)):
            raise ValueError("this saved sound is no longer available")
        return item


def _keep(data, filename, label, kind, prompt="", source="", license="Generated"):
    ext = os.path.splitext(filename)[1].lower()
    if library.kind_of(filename) != "audio":
        raise ValueError("only sound files can be saved here")
    digest = hashlib.sha256(data).hexdigest()
    with _lock:
        items = _read()
        old = next((x for x in items if x.get("sha256") == digest), None)
        if old and os.path.isfile(_path(old)):
            return {k: v for k, v in old.items() if k != "file"}
        item = {"id": uuid.uuid4().hex, "file": digest + ext, "name": (label or filename)[:160],
                "kind": kind if kind in ("effects", "music", "voice") else "effects", "prompt": prompt[:3000],
                "source": source, "license": license, "sha256": digest, "saved": time.time()}
        target = _path(item)
        with open(target + ".tmp", "wb") as f:
            f.write(data)
        os.replace(target + ".tmp", target)
        item["duration"] = media.probe(target).get("duration", 0)
        items.append(item)
        _write(items)
        return {k: v for k, v in item.items() if k != "file"}


def save(name, web, label="", kind="effects", prompt=""):
    source = projects.media_path(name, web)
    if library.kind_of(source) != "audio":
        raise ValueError("choose an audio file to save")
    with open(source, "rb") as f:
        return _keep(f.read(), os.path.basename(source), label or web, kind, prompt)


def remove(sound_id):
    with _lock:
        item = get(sound_id)
        items = [x for x in _read() if x["id"] != sound_id]
        _write(items)
        if not any(x["file"] == item["file"] for x in items):
            os.remove(_path(item))
    return {"ok": True}


def use(name, sound_id):
    with _lock:
        item = get(sound_id)
        folder = os.path.join(projects._folder(name), "media")
        os.makedirs(folder, exist_ok=True)
        target = os.path.join(folder, "saved-" + item["id"] + os.path.splitext(item["file"])[1])
        if not os.path.isfile(target):
            shutil.copy2(_path(item), target)
    return {"web": projects.add_media(name, target), "name": item["name"]}


def install():
    """Two fixed, creator-hosted CC0 packs. Only audio and license text are read; no archive paths are extracted."""
    count = 0
    for pack_id, pack in PACKS.items():
        # Kenney now signs downloads; old static ZIP URLs redirect to the asset page.
        slug = "assets/" + pack["source"].rsplit("/", 1)[-1]
        req = urllib.request.Request("https://kenney.nl/download-link/" + slug.encode().hex(),
                                     headers={"User-Agent": "ForgeStudio/1.0", "Referer": pack["source"]})
        with urllib.request.urlopen(req, timeout=30) as response:
            link = json.load(response).get("url", "")
        parsed = urllib.parse.urlparse(link)
        if parsed.scheme != "https" or parsed.hostname not in ("kenney.nl", "www.kenney.nl"):
            raise ValueError("the sound creator returned an invalid download link")
        req = urllib.request.Request(link, headers={"User-Agent": "ForgeStudio/1.0", "Referer": pack["source"]})
        with urllib.request.urlopen(req, timeout=90) as response:
            data = response.read(32 * 1024 * 1024 + 1)
        if len(data) > 32 * 1024 * 1024:
            raise ValueError("sound pack is unexpectedly large")
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for entry in archive.infolist():
                if entry.is_dir() or entry.file_size > 16 * 1024 * 1024:
                    continue
                filename = os.path.basename(entry.filename.replace("\\", "/"))
                if library.kind_of(filename) == "audio":
                    _keep(archive.read(entry), filename, pack["title"] + " · " + os.path.splitext(filename)[0],
                          pack["kind"], source=pack["source"], license="CC0-1.0")
                    count += 1
                elif "license" in filename.lower() and filename.lower().endswith(".txt"):
                    with open(os.path.join(paths.sub("sounds", "licenses"), pack_id + ".txt"), "wb") as f:
                        f.write(archive.read(entry))
    return {"count": count, "text": f"{count} CC0 sounds are ready", "progress": 1.0}

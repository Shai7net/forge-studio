"""The media library: every picture, video and sound in Forge's output folders, newest first.

The folders hold over a hundred thousand files, so they are indexed in a small SQLite file and re-read only
where a folder changed (adding or removing a file changes its folder's modified time on NTFS).
"""

import json
import os
import re
import sqlite3
import subprocess
import threading
import time

from . import paths

KINDS = {
    "image": (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"),
    "video": (".mp4", ".webm", ".mov", ".mkv", ".avi", ".gif", ".m4v"),
    "audio": (".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac"),
}
_KIND_OF = {ext: kind for kind, exts in KINDS.items() for ext in exts}
INDEX_VERSION = 2  # raise when the rules below change: every folder is then listed again
SKIP_DIRS = {"__pycache__", ".git", "node_modules", "thumbs", ".thumbnails", "live", "work"}

_local = threading.local()
_scan_lock = threading.Lock()
state = {"scanning": False, "scanned": 0, "last": 0.0, "files": 0}


def _db():
    if getattr(_local, "db", None) is None:
        db = sqlite3.connect(os.path.join(paths.data_root(), "library.sqlite"), timeout=30)
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript("""
            CREATE TABLE IF NOT EXISTS media(id INTEGER PRIMARY KEY, path TEXT UNIQUE, root TEXT, folder TEXT,
                name TEXT, kind TEXT, size INTEGER, mtime REAL);
            CREATE INDEX IF NOT EXISTS media_mtime ON media(mtime DESC);
            CREATE INDEX IF NOT EXISTS media_folder ON media(folder);
            CREATE TABLE IF NOT EXISTS dirs(path TEXT PRIMARY KEY, mtime REAL);
        """)
        if db.execute("PRAGMA user_version").fetchone()[0] != INDEX_VERSION:  # what counts as media changed
            with db:
                db.execute("DELETE FROM dirs")
                db.execute(f"PRAGMA user_version = {INDEX_VERSION}")
        _local.db = db
    return _local.db


HELPERS = re.compile(r"\.(first|last)\.png$", re.I)  # Video Studio's first/last frame beside each video


def kind_of(path):
    if HELPERS.search(path):
        return None
    return _KIND_OF.get(os.path.splitext(path)[1].lower())


def scan(roots=None):
    """Bring the index up to date. Only folders whose modified time changed are listed again."""
    if not _scan_lock.acquire(blocking=False):
        return state
    try:
        state.update(scanning=True, scanned=0)
        roots = roots or paths.library_roots()
        db = _db()
        known = dict(db.execute("SELECT path, mtime FROM dirs"))
        seen = set()
        for root in roots:
            stack = [root]
            while stack:
                folder = stack.pop()
                seen.add(folder)
                try:
                    mtime = os.stat(folder).st_mtime
                    entries = list(os.scandir(folder))
                except OSError:
                    continue
                state["scanned"] += 1
                for e in entries:
                    if e.is_dir(follow_symlinks=False) and e.name not in SKIP_DIRS and not e.name.startswith("."):
                        stack.append(e.path)
                if known.get(folder) == mtime:
                    continue
                rows = []
                for e in entries:
                    kind = kind_of(e.name)
                    if kind and e.is_file(follow_symlinks=False):
                        try:
                            st = e.stat()
                        except OSError:
                            continue
                        rows.append((e.path, root, folder, e.name, kind, st.st_size, st.st_mtime))
                with db:
                    db.execute("DELETE FROM media WHERE folder = ?", (folder,))
                    db.executemany("INSERT OR REPLACE INTO media(path, root, folder, name, kind, size, mtime) "
                                   "VALUES (?,?,?,?,?,?,?)", rows)
                    db.execute("INSERT OR REPLACE INTO dirs(path, mtime) VALUES (?,?)", (folder, mtime))
        with db:  # folders that are gone, or are no longer under a library folder
            for folder in set(known) - seen:
                db.execute("DELETE FROM media WHERE folder = ?", (folder,))
                db.execute("DELETE FROM dirs WHERE path = ?", (folder,))
        state.update(last=time.time(), files=db.execute("SELECT COUNT(*) FROM media").fetchone()[0])
        return state
    finally:
        state["scanning"] = False
        _scan_lock.release()


def scan_in_background():
    threading.Thread(target=scan, name="forge-studio-library", daemon=True).start()


def _row(r):
    return {"id": r[0], "path": r[1], "folder": r[2], "name": r[3], "kind": r[4], "size": r[5], "mtime": r[6],
            "source": source_of(r[1])}


def source_of(path):
    """Which part of Forge made a file, from where it was written (for the filter chips)."""
    p = path.replace("\\", "/").lower()
    for key, label in (("/video-studio/", "video-studio"), ("/time-strata/", "time-strata"),
                       ("/animatediff", "animatediff"), ("/deforum", "deforum"), ("/simulation", "simulation-360"),
                       ("/extras", "extras"), ("/img2img", "img2img"), ("/txt2img", "txt2img"),
                       ("/infinite-zoom", "infinite-zoom"), ("/svd/", "svd"), ("/forge-studio/", "forge-studio")):
        if key in p:
            return label
    return "other"


def query(kind=None, text=None, folder=None, before=None, limit=120):
    """Newest first. `before` is the mtime of the last item of the previous page."""
    sql, args = "SELECT id, path, folder, name, kind, size, mtime FROM media WHERE 1=1", []
    if kind:
        sql += " AND kind = ?"
        args.append(kind)
    if folder:
        sql += " AND folder = ?"
        args.append(os.path.normpath(folder))
    if text:
        for word in text.split():
            sql += " AND path LIKE ?"
            args.append(f"%{word}%")
    if before:
        sql += " AND mtime < ?"
        args.append(float(before))
    sql += " ORDER BY mtime DESC LIMIT ?"
    args.append(max(1, min(int(limit), 500)))
    return [_row(r) for r in _db().execute(sql, args)]


def folders():
    """Each folder with files, its count and its newest file, newest first."""
    rows = _db().execute("SELECT folder, COUNT(*), MAX(mtime) FROM media GROUP BY folder ORDER BY MAX(mtime) DESC")
    return [{"folder": f, "count": n, "mtime": m} for f, n, m in rows]


def get(media_id):
    r = _db().execute("SELECT id, path, folder, name, kind, size, mtime FROM media WHERE id = ?",
                      (int(media_id),)).fetchone()
    if r is None:
        raise KeyError(f"no media {media_id}")
    return _row(r)


def add_file(path):
    """Index one file now (one just made, or sent to the studio) and return it."""
    path = os.path.normpath(path)
    kind = kind_of(path)
    if not kind or not os.path.isfile(path):
        raise ValueError(f"not a picture, video or sound: {path}")
    st = os.stat(path)
    db = _db()
    root = next((r for r in paths.library_roots() if paths.inside(path, [r])), os.path.dirname(path))
    with db:
        db.execute("INSERT OR REPLACE INTO media(id, path, root, folder, name, kind, size, mtime) VALUES "
                   "((SELECT id FROM media WHERE path = ?),?,?,?,?,?,?,?)",
                   (path, path, root, os.path.dirname(path), os.path.basename(path), kind, st.st_size, st.st_mtime))
    return _row(db.execute("SELECT id, path, folder, name, kind, size, mtime FROM media WHERE path = ?",
                           (path,)).fetchone())


# ---- what a file was made with ----

def _png_parameters(path):
    try:
        from PIL import Image
        with Image.open(path) as im:
            return im.info.get("parameters") or im.info.get("prompt")
    except Exception:
        return None


def _ffprobe_comment(path):
    exe = ffmpeg("ffprobe")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "-v", "error", "-show_entries", "format_tags=comment,description",
                              "-of", "json", path], capture_output=True, timeout=10).stdout
        tags = json.loads(out or b"{}").get("format", {}).get("tags", {})
        return tags.get("comment") or tags.get("description")
    except Exception:
        return None


def info(media_id):
    """The prompt, seed and model a file was made with, where Forge left them."""
    item = get(media_id)
    path, made = item["path"], None
    sidecar = os.path.splitext(path)[0] + ".json"
    if item["kind"] == "video" and os.path.isfile(sidecar):  # Video Studio writes its job next to the video
        try:
            with open(sidecar, encoding="utf-8") as f:
                job = json.load(f)
            made = {k: job.get(k) for k in ("prompt", "negative", "seed", "mode", "width", "height", "frames", "fps")
                    if job.get(k) is not None}
        except Exception:
            made = None
    elif item["kind"] == "image":
        text = _png_parameters(path)
        made = {"parameters": text} if text else None
    elif item["kind"] == "video":
        text = _ffprobe_comment(path)
        made = {"parameters": text} if text else None
    if made and made.get("parameters"):
        seed = re.search(r"Seed: (\d+)", made["parameters"])
        if seed:
            made["seed"] = int(seed.group(1))
        made["prompt"] = made.get("prompt") or made["parameters"].split("\nNegative prompt:")[0].split("\nSteps:")[0]
    item["made"] = made
    return item


# ---- small pictures ----

_REAL = {}


def ffmpeg(name="ffmpeg"):
    """The ffmpeg (or ffprobe) program itself. Chocolatey puts a small launcher on PATH that starts the real one as
    its child: stopping the launcher left the real ffmpeg running, and ten of them at full load made exports crawl
    (2026-10-28). The real program beside chocolatey's package is used instead."""
    import glob
    import shutil
    if name in _REAL:
        return _REAL[name]
    found = shutil.which(name)
    if found and "chocolatey" in found.lower() and os.sep + "bin" + os.sep in found:
        root = found.lower().split(os.sep + "bin" + os.sep)[0]
        real = sorted(glob.glob(os.path.join(root, "lib", "ffmpeg*", "tools", "**", f"{name}.exe"), recursive=True))
        if real:
            found = real[0]
    if found:
        _REAL[name] = found
        return found
    if name == "ffmpeg":
        try:
            import imageio_ffmpeg
            return imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            return None
    return None


def thumbnail(media_id, size=320):
    """A JPEG at most `size` pixels wide, made once per file version."""
    item = get(media_id)
    return _thumbnail(item, size)


def thumbnail_path(path, size=320):
    import hashlib
    key = hashlib.sha256((os.path.abspath(path) + str(os.stat(path).st_mtime_ns)).encode()).hexdigest()[:24]
    return _thumbnail({'id': key, 'mtime': 0, 'path': path,
                       'kind': _KIND_OF.get(os.path.splitext(path)[1].lower())}, size)


def _thumbnail(item, size):
    out = os.path.join(paths.sub("thumbs"), f"{item['id']}-{int(item['mtime'])}-{size}.jpg")
    if os.path.isfile(out):
        return out
    from PIL import Image
    source = item["path"]
    if item["kind"] == "video":
        first = os.path.splitext(source)[0] + ".first.png"  # Video Studio saves the first frame
        if os.path.isfile(first):
            source = first
        else:
            exe = ffmpeg()
            if not exe:
                return None
            frame = out + ".png"
            subprocess.run([exe, "-v", "error", "-y", "-ss", "0.2", "-i", item["path"], "-frames:v", "1",
                            "-vf", f"scale={size}:-2", frame], capture_output=True, timeout=30)
            if not os.path.isfile(frame):
                subprocess.run([exe, "-v", "error", "-y", "-i", item["path"], "-frames:v", "1",
                                "-vf", f"scale={size}:-2", frame], capture_output=True, timeout=30)
            if not os.path.isfile(frame):
                return None
            source = frame
    elif item["kind"] != "image":
        return None
    with Image.open(source) as im:
        im.draft("RGB", (size, size))
        im = im.convert("RGB")
        im.thumbnail((size, size * 3))
        im.save(out, "JPEG", quality=82)
    if source.endswith(".jpg.png"):
        os.remove(source)
    return out

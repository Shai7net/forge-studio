"""Generation from the timeline: Video Studio (Wan 2.2) makes a clip and the page puts it where it was asked.

A request names a project, what to make and the placeholder clip the page laid on the timeline for it. Requests
run one at a time in a thread of their own (Video Studio has one worker and takes Forge's queue itself), so they
finish and are recorded even when the page is closed; the page swaps the placeholder for the result when it next
sees the request done (generations.json in the project folder).

Video Studio's own Generate is used, with the values the user last chose in its tab (webui\\ui-config.json), so a
clip made from here is made exactly as one made there. A continuation of an AnimateDiff or a Deforum clip is made by
that tool instead, from the clip's own settings (continuers.py); `engine` on a request says which ("auto": the tool
that made the clip).
"""

import html
import json
import os
import random
import re
import subprocess
import sys
import threading
import time
import uuid

from . import continuers, library, media, paths, projects

KINDS = ("text", "picture", "continue", "between", "take")
_queue = []
_lock = threading.Lock()
_wake = threading.Condition(_lock)
_thread = None
live = {}  # id -> what the running request is doing now


def _vs():
    """Video Studio's modules (loaded by Forge from its own extension)."""
    mod = sys.modules.get("videostudio.ui")
    if mod is None:
        folder = os.path.join(os.path.dirname(paths.EXTENSION), "sd-forge-video-studio")
        if folder not in sys.path:
            sys.path.append(folder)
        import importlib
        mod = importlib.import_module("videostudio.ui")
    return mod


def available():
    try:
        vs = _vs()
        installed = [p for p in vs.models.PRESETS if not vs.models.missing(p)]
        return {"ok": bool(installed), "presets": installed, "qualities": list(vs.QUALITY),
                "message": "" if installed else "Video Studio's Wan model is not downloaded yet"}
    except Exception as e:
        return {"ok": False, "presets": [], "qualities": [], "message": f"Video Studio is not available: {e}"}


def _ui_defaults():
    try:
        with open(os.path.join(paths.WEBUI, "ui-config.json"), encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}
    return lambda label, default: cfg.get(f"video_studio/{label}/value", default)


# ---- the record of each project's requests ----

def _file(name):
    return os.path.join(projects._folder(name), "generations.json")


def _read(name):
    try:
        with open(_file(name), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def _write(name, records):
    projects._write(_file(name), json.dumps(records, ensure_ascii=False, indent=1))


def _update(name, gen_id, **changes):
    with _lock:
        records = _read(name)
        for r in records:
            if r["id"] == gen_id:
                r.update(changes)
        _write(name, records)


def records(name):
    out = _read(name)
    for r in out:
        if r["id"] in live:
            r["live"] = live[r["id"]]
    return out


# ---- frames out of clips ----

def frame(name, web, seconds, last=False):
    """One frame of a project's video as a PNG (the picture a continuation or a transition starts from)."""
    path = projects.media_path(name, web)
    folder = os.path.join(projects._folder(name), "frames")
    os.makedirs(folder, exist_ok=True)
    out = os.path.join(folder, f"{uuid.uuid4().hex[:8]}.png")
    exe = library.ffmpeg()
    if library.kind_of(path) == "image":
        from PIL import Image
        with Image.open(path) as im:
            im.convert("RGB").save(out)
        return out
    if last:  # the very last decodable frame
        subprocess.run([exe, "-v", "error", "-y", "-sseof", "-0.5", "-i", path, "-update", "1", "-q:v", "1", out],
                       capture_output=True, timeout=120)
    if not os.path.isfile(out):
        media.grab(path, seconds if not last else 1e9, out)
    if not os.path.isfile(out):
        raise ValueError(f"could not take a frame of {web} at {seconds:.2f} s")
    return out


def _made_with(path):
    """Video Studio's record of a clip it made (prompt, seed, pictures), if it made it."""
    base = os.path.splitext(path)[0]
    for candidate in (base + ".json", re.sub(r"-sound$", "", base) + ".json"):
        if os.path.isfile(candidate):
            with open(candidate, encoding="utf-8") as f:
                return json.load(f)
    return None


# ---- requests ----

def submit(name, kind, prompt="", seconds=5.0, seed=-1, quality=None, sound=True, width=0, height=0,
           start=None, end=None, take_of=None, placeholder=None, engine="auto"):
    """`start` / `end`: {web, seconds, last} of the frames to begin and end on; `take_of`: the web name of a clip
    to make again. Returns the record."""
    if kind not in KINDS:
        raise ValueError(f"unknown kind {kind}")
    projects._folder(name)
    if kind == "take":
        made = _made_with(projects.media_path(name, take_of))
        if not made:
            raise ValueError("this clip was not made by Video Studio, so it cannot be made again")
        prompt = prompt or made.get("prompt", "")
        start = start or ({"path": made["start_image"]} if made.get("start_image") and os.path.isfile(made["start_image"]) else None)
        end = end or ({"path": made["end_image"]} if made.get("end_image") and os.path.isfile(made["end_image"]) else None)
        # its "frames" are Wan's frames (16 a second) even when "fps" is the smoothed rate (32): 17 frames, fps 32 = 1 s
        seconds = (made.get("frames", 81) - 1) / 16.0
        kind = "between" if end else "picture" if start else "text"
    if not (prompt or "").strip():
        raise ValueError("write what happens in the clip")
    if kind in ("picture", "continue", "between") and not start:
        raise ValueError("this needs a picture to start from")
    if kind == "between" and not end:
        raise ValueError("a transition needs the frame it ends on")
    record = {"id": uuid.uuid4().hex[:10], "kind": kind, "prompt": prompt.strip(), "seconds": float(seconds),
              "seed": int(seed) if seed is not None and int(seed) >= 0 else random.randint(0, 2 ** 31 - 1),
              "quality": quality, "sound": bool(sound), "width": int(width or 0), "height": int(height or 0),
              "start": start, "end": end, "take_of": take_of, "placeholder": placeholder, "project": name,
              "engine": engine if engine in continuers.ENGINES else "auto",
              "state": "waiting", "asked": time.time()}
    with _lock:
        recs = _read(name)
        recs.append(record)
        _write(name, recs)
        _queue.append((name, record["id"]))
        _wake.notify_all()
    _ensure_thread()
    return record


def placed(name, gen_id, web=None):
    """The page swapped the placeholder for the result."""
    _update(name, gen_id, state="placed", web=web, placed=time.time())
    return {"ok": True}


def cancel(name, gen_id):
    with _lock:
        waiting = (name, gen_id) in _queue
        if waiting:
            _queue.remove((name, gen_id))
    if waiting:
        _update(name, gen_id, state="cancelled")
    elif gen_id in live:
        if live[gen_id].get("engine") in ("animatediff", "deforum"):
            continuers.cancel_running()
        else:
            try:
                _vs().runner.cancel_current()
            except Exception:
                pass
    return {"ok": True}


def _ensure_thread():
    global _thread
    with _lock:
        if _thread is None or not _thread.is_alive():
            _thread = threading.Thread(target=_loop, name="forge-studio-generate", daemon=True)
            _thread.start()


def _loop():
    while True:
        with _lock:
            while not _queue:
                _wake.wait(30)
            name, gen_id = _queue.pop(0)
        record = next((r for r in _read(name) if r["id"] == gen_id), None)
        if not record or record["state"] != "waiting":
            continue
        _update(name, gen_id, state="running", started=time.time())
        live[gen_id] = {"stage": "start", "text": "waiting for Forge to be free", "progress": 0.0}
        try:
            video = _make(name, record)
            if not video:
                raise RuntimeError(live[gen_id].get("text") or "Video Studio made nothing")
            item = library.add_file(video)
            _update(name, gen_id, state="done", video=video, media=item["id"], ended=time.time())
        except Exception as e:
            stopped = "cancel" in str(e).lower() or live.get(gen_id, {}).get("stage") == "cancelled"
            _update(name, gen_id, state="cancelled" if stopped else "error", message=str(e)[:600], ended=time.time())
        finally:
            live.pop(gen_id, None)


def _picture(name, ref):
    if ref.get("path"):
        return ref["path"]
    if ref.get("frame"):  # a frame edited in the studio
        from . import frames
        return frames.path_of(name, ref["frame"])
    return frame(name, ref["web"], ref.get("seconds", 0.0), ref.get("last", False))


def engine_for(name, record):
    """The tool that makes a request: Wan for new shots and takes; a continuation goes to the tool that made the clip,
    a join between two clips (frame to frame) to AnimateDiff when either clip is AnimateDiff's (Deforum cannot aim
    at an end picture, so its clips are joined by Wan), unless the request names one."""
    if record["kind"] not in ("continue", "between"):
        return "wan"
    chosen = record.get("engine") or "auto"
    if record["kind"] == "between":
        if chosen in ("wan", "animatediff"):
            return chosen
        for ref in (record["start"], record.get("end") or {}):
            if ref.get("web") and _recipe_of(name, ref["web"])["engine"] == "animatediff":
                return "animatediff"
        return "wan"
    if chosen != "auto":
        return chosen
    return _recipe_of(name, record["start"]["web"])["engine"]


def _recipe_of(name, web):
    path = projects.media_path(name, web)
    return dict(continuers.recipe(path, _made_with(path)), path=path)


def _make(name, record):
    engine = engine_for(name, record)
    if engine in ("animatediff", "deforum"):
        refs = [record["start"]] + ([record["end"]] if record["kind"] == "between" else [])
        # the settings come from the clip made by that tool (for a join: the first of the two that is)
        source = next((r["path"] for r in (_recipe_of(name, ref["web"]) for ref in refs if ref.get("web"))
                       if r["engine"] == engine), projects.media_path(name, record["start"]["web"]))
        start = _picture(name, record["start"])
        end = _picture(name, record["end"]) if record["kind"] == "between" else None

        def progress(stage, value, text):
            live[record["id"]] = {"stage": stage, "text": text[:300], "progress": value, "engine": engine}
        progress("start", 0.0, f"{engine}: waiting for Forge to be free")
        if engine == "animatediff":
            video = continuers.run_animatediff(source, start, record["prompt"], record["seconds"], record["seed"],
                                               progress, end_frame=end)
        else:
            video = continuers.run_deforum(source, start, record["prompt"], record["seconds"], record["seed"], progress)
        video = continuers.soften(video, start, end)
        return _with_sound(video, record, progress) if record.get("sound") else video
    return _make_wan(name, record)


def _with_sound(video, record, progress):
    """Video Studio's AI sound (MMAudio, from the picture and the words) on a clip another tool made, as Wan's clips
    get it; the clip without sound when MMAudio is missing or fails."""
    try:
        vs = _vs()
        if not vs.sound.status().get("effects"):
            return video
        target = os.path.splitext(video)[0] + "-sound.mp4"
        job = {"video": video, "out": target, "speech": {"mode": "none"},
               "effects": {"on": True, "prompt": record["prompt"][:300], "negative": "music, speech, talking",
                           "seed": 0, "db": 0.0}}
        task = vs.sound.start(job, os.path.join(projects._folder(record["project"]), "sound-jobs"))
        for ev in task.run():
            progress("effects", ev.get("progress") or 0.0, "adding AI sound")
            if ev.get("stage") == "done":
                return target if os.path.isfile(target) else video
            if ev.get("stage") in ("error", "cancelled"):
                return video
    except Exception:  # noqa: BLE001 - the clip is made; sound is a bonus
        pass
    return video


def _make_wan(name, record):
    """Runs Video Studio's Generate with the user's own settings; returns the video it made (with sound if asked)."""
    vs = _vs()
    d = _ui_defaults()
    mode, count, pictures = "t2v", 1, [None] * vs.MAX_PICTURES
    if record["kind"] != "text":
        mode = "i2v"
        pictures[0] = _picture(name, record["start"])
        if record["kind"] == "between":
            pictures[1] = _picture(name, record["end"])
            count = 2
    quality = record["quality"] if record["quality"] in vs.QUALITY else d("Quality", vs.DEFAULT_QUALITY)
    preset = d("Model", vs.models.DEFAULT_PRESET)
    if vs.models.missing(preset):
        preset = next((p for p in vs.models.PRESETS if not vs.models.missing(p)), preset)
    width, height = record.get("width") or 0, record.get("height") or 0
    if width and height:  # the sequence's shape, at the chosen quality's size
        width, height = vs.fit_size(width, height, vs.QUALITY[quality])
    aspect = d("Shape", "9:16 portrait")
    if width and height:
        aspect = min(vs.ASPECTS, key=lambda k: abs(vs.ASPECTS[k][0] / vs.ASPECTS[k][1] - width / height))
    rest = [aspect, d("Picture prompt (empty: the video prompt)", ""), d("Picture steps", 25), d("Picture CFG", 6),
            d("Sampler for the picture", "Euler a"),
            record["prompt"], d("Negative prompt (used only when CFG is above 1)", ""), quality,
            min(7.5, max(1.0, record["seconds"])), record["seed"], preset,
            d("Steps", 4), d("Shift", 5.0), d("CFG", 1.0), d("Sampler", "euler"), d("Fast int8 math (about 1.5x faster)", True),
            d("Attention", "auto"), width, height, d("Smooth motion: 32 fps (FILM)", True),
            d("Enlarge the finished video", vs.UPSCALE_OFF), d("LoRA 1", vs.NO_LORA), d("Strength 1", 1.0),
            d("LoRA 2", vs.NO_LORA), d("Strength 2", 1.0), d("Styles", []), record["sound"],
            d("Sound: what is heard (plain English; empty: the prompt and the picture)", "")]
    args = pictures + [""] * (vs.MAX_PICTURES - 1) + rest
    video = None
    tags = re.compile(r"<[^>]+>")
    for status, _preview, out_video, _details, _last in vs.generate(mode, count, *args):
        if isinstance(out_video, str) and out_video:
            video = out_video
        text = html.unescape(" ".join(tags.sub(" ", status or "").split()))
        job = vs.runner.current()
        ev = (job.latest() if job else None) or {}
        stage = ev.get("stage") or ("error" if "Error" in text[:12] else "start")
        live[record["id"]] = {"stage": stage, "text": text[:300], "progress": vs.overall(ev.get("stage", ""), ev.get("progress"))
                              if ev else None}
        if text.startswith("Error"):
            raise RuntimeError(text[5:].strip(" ·:") or "Video Studio stopped")
        if text.startswith("Stopped"):
            raise RuntimeError("cancelled")
    return video

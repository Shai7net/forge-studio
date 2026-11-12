"""The routes under /forge-studio that the page talks to."""

import mimetypes
import os
import re
import subprocess
import tempfile
import time
from urllib.parse import unquote, urlsplit

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response

from . import NAME, VERSION, beats, clipkit, continuers, duck, framefiles, frames, generate, imageai, imagedoc, imagegen, layers, lipsync, lipspeech, library, media, paths, projectbin, projects, render, sound, soundbank

_RANGE = re.compile(r"bytes=(\d*)-(\d*)")
mimetypes.add_type("application/wasm", ".wasm")
mimetypes.add_type("text/javascript", ".js")

inbox = []  # files sent from other tabs, waiting for the page to take them


def _guard(function):
    try:
        return function()
    except projects.ProjectError as e:
        raise HTTPException(status_code=e.status, detail=str(e))
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e).strip("'\""))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


def _send(path, request: Request, mime=None, cache="no-cache"):
    """A file, with byte ranges so videos can seek."""
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="file not found")
    mime = mime or mimetypes.guess_type(path)[0] or "application/octet-stream"
    size = os.path.getsize(path)
    headers = {"Accept-Ranges": "bytes", "Cache-Control": cache}
    asked = _RANGE.match(request.headers.get("range") or "")
    if not asked or (not asked.group(1) and not asked.group(2)):
        return FileResponse(path, media_type=mime, headers=headers)
    start, end = asked.group(1), asked.group(2)
    if start:
        start, end = int(start), min(int(end) if end else size - 1, size - 1)
    else:
        start, end = max(0, size - int(end)), size - 1
    if start > end or start >= size:
        return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
    with open(path, "rb") as f:
        f.seek(start)
        data = f.read(end - start + 1)
    headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return Response(content=data, status_code=206, media_type=mime, headers=headers)


def _with_urls(item):
    item["url"] = f"/forge-studio/media/{item['id']}?v={int(item['mtime'])}"
    item["thumb"] = f"/forge-studio/thumb/{item['id']}?v={int(item['mtime'])}"
    return item


# ---- general ----

def api_options():
    return {"name": NAME, "version": VERSION, "presets": render.presets(), "loudness": sound.presets(), "library": library.state, "roots": paths.library_roots(),
            "projects": paths.projects_dir(), "exports": paths.exports_dir(), "finish": render.finish_tools()}


# ---- library ----

def api_library(kind: str = "", text: str = "", folder: str = "", before: float = 0, limit: int = 120):
    if not library.state["last"] and not library.state["scanning"]:
        library.scan_in_background()
    items = library.query(kind or None, text or None, folder or None, before or None, limit)
    return {"items": [_with_urls(i) for i in items], "state": library.state}


def api_library_folders():
    return {"folders": library.folders()}


def api_library_scan():
    library.scan_in_background()
    return library.state


def api_library_info(id: int):
    return _guard(lambda: _with_urls(library.info(id)))


def api_thumb(media_id: int, request: Request):
    path = _guard(lambda: library.thumbnail(media_id))
    if not path:
        raise HTTPException(status_code=404, detail="no picture for this file")
    return _send(path, request, "image/jpeg", cache="public, max-age=604800")


def api_media(media_id: int, request: Request):
    item = _guard(lambda: library.get(media_id))
    return _send(item["path"], request)


# ---- projects ----

def api_projects():
    return {"projects": projects.list_projects()}


def api_project(name: str):
    return _guard(lambda: projects.load(name))


def api_project_create(name: str = Body(..., embed=True)):
    return _guard(lambda: projects.create(name))


def api_project_save(name: str = Body(...), doc: dict = Body(...)):
    return _guard(lambda: projects.save_doc(name, doc))


def api_project_add(name: str = Body(...), id: int = Body(None), path: str = Body(None)):
    def add():
        real = library.get(id)["path"] if id is not None else path
        if id is None and not paths.inside(real, _sendable_roots()):
            raise projects.ProjectError("that file is not in a folder Forge Studio may read", 403)
        web = projects.add_media(name, real)
        return {"web": web, "url": _project_media_url(name, web)}
    return _guard(add)


def _project_media_url(name, web):
    from urllib.parse import quote
    return f"/forge-studio/project/media?name={quote(name)}&web={quote(web)}"


def api_project_media(name: str, web: str, request: Request):
    return _send(_guard(lambda: projects.media_path(name, web)), request)


def api_project_bin(name: str, kind: str = '', text: str = '', offset: int = 0, limit: int = 90):
    return _guard(lambda: projectbin.listed(name, kind, text, offset, limit))


def api_project_thumb(name: str, web: str, request: Request):
    path = _guard(lambda: projects.media_path(name, web))
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail='The project file is missing')
    return _send(_guard(lambda: library.thumbnail_path(path)), request, 'image/jpeg')


def api_frame_export(image: str = Body(...), filename: str = Body('frame.png')):
    return _guard(lambda: framefiles.export(image, filename))


async def api_project_upload(name: str, web: str, request: Request):
    data = await request.body()
    return _guard(lambda: {"web": projects.store_upload(name, web, data)})


# ---- what the timeline shows of a file ----

def api_probe(name: str, web: str):
    return _guard(lambda: media.probe(projects.media_path(name, web)))


def api_strip(name: str, web: str, request: Request):
    path = _guard(lambda: media.strip(projects.media_path(name, web)))
    return _send(path, request, "image/jpeg", cache="public, max-age=604800")


def api_wave(name: str, web: str, request: Request):
    path = _guard(lambda: media.wave(projects.media_path(name, web)))
    return _send(path, request, "image/png", cache="public, max-age=604800")


def api_beats(name: str, web: str):
    """The music's beats: {tempo, beats: [seconds in the file], bar: the first beat that is a "one"}."""
    return _guard(lambda: beats.beats(projects.media_path(name, web)))


def api_talk(name: str, web: str):
    """Where a sound is talking: [[start, end], ...] seconds in the file (for lowering music under it)."""
    return _guard(lambda: duck.talking_cached(projects.media_path(name, web)))


# ---- export ----

async def api_render_text(name: str, clip: str, request: Request, step: int = None, steps: int = None, reset: int = 0):
    """A text clip as the page drew it (PNG of the whole frame), for the export; with `step`, the typewriter's text
    with that many of its `steps` letters (the first of them with `reset`, which forgets the earlier ones)."""
    data = await request.body()
    if not re.match(r"^[A-Za-z0-9_-]{1,40}$", clip or ""):
        raise HTTPException(status_code=400, detail="not a clip id")
    if step is not None:
        return _guard(lambda: {"path": render.store_typed(name, clip, int(step), int(steps or 0), data, bool(reset))})
    return _guard(lambda: {"path": render.store_text(name, clip, data)})


def api_render_start(name: str = Body(...), doc: dict = Body(...), preset: str = Body("h264"),
                     loudness: float = Body(None), options: dict = Body(None)):
    """`options`: folder, filename, fps_x (2 / 4) with interpolator (rife / film), upscale {scale, engine, mode},
    hdr {style, tone, strength} - see render.start."""
    return _guard(lambda: render.start(name, doc, preset, loudness, options))


def api_render_open_folder(id: str = Body(..., embed=True)):
    """Explorer on a finished export, wherever it was saved."""
    job = _guard(lambda: render.jobs[id])
    out = job.get("out")
    if not out or not os.path.isfile(out):
        raise HTTPException(status_code=404, detail="the export is not there")
    subprocess.Popen(["explorer", "/select,", os.path.normpath(out)])
    return {"ok": True}


def api_pick_folder(start: str = Body("", embed=True)):
    """Windows' own folder picker, on this machine's screen; {"folder": path or ""}."""
    def pick():
        import tkinter
        from tkinter import filedialog
        root = tkinter.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        try:
            return filedialog.askdirectory(parent=root, initialdir=start or paths.exports_dir(),
                                           title="Forge Studio: where to save the export") or ""
        finally:
            root.destroy()
    chosen = pick()
    return {"folder": os.path.normpath(chosen) if chosen else ""}


def api_render(id: str):
    return _guard(lambda: render.public(render.jobs[id]))


def api_render_cancel(id: str = Body(..., embed=True)):
    return _guard(lambda: render.cancel(id))


def api_render_file(id: str, request: Request):
    job = _guard(lambda: render.jobs[id])
    return _send(job["out"] if job["state"] == "done" else None, request)


# ---- generating from the timeline ----

def api_gen_options():
    return generate.available()


def api_gen_submit(name: str = Body(...), kind: str = Body(...), prompt: str = Body(""), seconds: float = Body(5.0),
                   seed: int = Body(-1), quality: str = Body(None), sound: bool = Body(True), width: int = Body(0),
                   height: int = Body(0), start: dict = Body(None), end: dict = Body(None), take_of: str = Body(None),
                   placeholder: dict = Body(None), engine: str = Body("auto"), direction: str = Body("after"), fps: float = Body(30)):
    return _guard(lambda: generate.submit(name, kind, prompt, seconds, seed, quality, sound, width, height, start, end,
                                          take_of, placeholder, engine, direction, fps))


def api_gen_list(name: str):
    return _guard(lambda: {"generations": generate.records(name)})


def api_gen_placed(name: str = Body(...), id: str = Body(...), web: str = Body(None)):
    return _guard(lambda: generate.placed(name, id, web))


def api_gen_cancel(name: str = Body(...), id: str = Body(...)):
    return _guard(lambda: generate.cancel(name, id))


def api_gen_made_with(name: str, web: str):
    """Whether a clip of the project was made by Video Studio, and with what (for "Make it again"), and which tool
    continues it (the one that made it: Wan, AnimateDiff or Deforum) with its words."""
    def read():
        path = projects.media_path(name, web)
        made = generate._made_with(path)
        found = continuers.recipe(path, made)
        return {"made": made, "engine": found["engine"], "label": found["label"], "prompt": found.get("prompt", "")}
    return _guard(read)


# ---- one clip's file: a freeze frame, backwards ----

def api_clip_still(name: str = Body(...), web: str = Body(...), seconds: float = Body(0.0)):
    """The frame the player shows at `seconds` of the file, as a picture of the project: {web, path}."""
    return _guard(lambda: clipkit.still(name, web, seconds))


def api_clip_reverse(name: str = Body(...), web: str = Body(...), start: float = Body(0.0), seconds: float = Body(...)):
    """`seconds` of the file from `start`, played backwards, as a new file of the project (a task: follow it)."""
    return _guard(lambda: clipkit.reverse(name, web, start, seconds))


def api_clip_merge(name: str = Body(...), clips: list = Body(...), settings: dict = Body(...), audio: list = Body(None)):
    return _guard(lambda: clipkit.merge(name, clips, settings, audio))


def api_still_save(name: str = Body(...), image: str = Body(...), seconds: float = Body(0.0)):
    """The whole frame as the page drew it (every layer, the edit's full size; data URL), saved as a PNG next to the
    exports and put in the library: {path, media}."""
    def save():
        import base64
        import io as _io
        from PIL import Image
        raw = base64.b64decode(image.split(",", 1)[-1])
        out_dir = os.path.join(paths.exports_dir(), time.strftime("%Y-%m-%d"))
        os.makedirs(out_dir, exist_ok=True)
        mark = f"{int(seconds // 60):02d}m{seconds % 60:05.2f}s".replace(".", "_")
        out = os.path.join(out_dir, f"{projects.BAD.sub('_', name)}-frame-{mark}-{time.strftime('%H%M%S')}.png")
        with Image.open(_io.BytesIO(raw)) as im:
            im.convert("RGB").save(out)
        return {"path": out, "media": library.add_file(out)["id"]}
    return _guard(save)


# ---- editing a frame ----

def api_frame_grab(name: str = Body(...), web: str = Body(...), seconds: float = Body(0.0)):
    return _guard(lambda: frames.grab(name, web, seconds))


# ---- image shots (the image workspace) ----

def api_image_new(name: str = Body(...), width: int = Body(...), height: int = Body(...), fill: str = Body("#ffffff"),
                  picture: str = Body(None), title: str = Body(None)):
    return _guard(lambda: imagedoc.new(name, width, height, fill, picture, title))


def api_image_doc(name: str, shot: str):
    return _guard(lambda: imagedoc.load(name, shot))


def api_image_file(name: str, shot: str, file: str, request: Request):
    return _send(_guard(lambda: imagedoc.file_path(name, shot, file)), request, "image/png", cache="public, max-age=86400")


async def api_image_save(request: Request):
    """multipart: name, shot, doc (JSON), flat (the flattened PNG), layer:<id> (PNG) for each layer whose pixels
    changed"""
    import json as _json
    form = await request.form()
    files = {}
    for key, value in form.multi_items():
        if key.startswith("layer:") and hasattr(value, "read"):
            files[key[6:]] = await value.read()
    flat = form.get("flat")
    flat = await flat.read() if flat is not None and hasattr(flat, "read") else None
    if flat is None:
        raise HTTPException(status_code=400, detail="the flattened picture is missing")

    return _guard(lambda: imagedoc.save(form["name"], form["shot"], _json.loads(form["doc"]), files, flat))


def api_image_genoptions():
    return _guard(imagegen.options)


async def api_image_generate(request: Request):
    """multipart: name, shot, mode, params (JSON), flat and mask (PNG), units (JSON list of ControlNet units) with
    cn:<i> (PNG) the picture of unit i, ref:<i> (PNG) references for FLUX 3 / FIBO"""
    import json as _json
    form = await request.form()

    async def png(key):
        v = form.get(key)
        return await v.read() if v is not None and hasattr(v, "read") else None
    units = _json.loads(form.get("units") or "[]")
    for i, u in enumerate(units):
        u["image"] = await png(f"cn:{i}")
    refs = []
    for i in range(4):
        r = await png(f"ref:{i}")
        if r:
            refs.append(r)
    flat, mask = await png("flat"), await png("mask")
    return _guard(lambda: imagegen.generate(form["name"], form["shot"], form["mode"], _json.loads(form.get("params") or "{}"),
                                            flat, mask, units, refs))


async def _image_form(request):
    form = await request.form()

    async def png(key):
        v = form.get(key)
        return await v.read() if v is not None and hasattr(v, "read") else None
    return form, png


async def api_image_subject(request: Request):
    """multipart: name, shot, picture (PNG)"""
    form, png = await _image_form(request)
    picture = await png("picture")
    return _guard(lambda: imageai.subject(form["name"], form["shot"], picture))


async def api_image_object(request: Request):
    """multipart: name, shot, picture (PNG), points (JSON [[x, y, 1 in / 0 out], ...])"""
    import json as _json
    form, png = await _image_form(request)
    picture = await png("picture")
    return _guard(lambda: imageai.object_at(form["name"], form["shot"], picture, _json.loads(form.get("points") or "[]")))


async def api_image_erase(request: Request):
    """multipart: name, shot, picture (PNG), mask (PNG, white = take away)"""
    form, png = await _image_form(request)
    picture, mask = await png("picture"), await png("mask")
    return _guard(lambda: imageai.erase(form["name"], form["shot"], picture, mask))


def api_image_tidy(name: str = Body(...), shot: str = Body(...), used: list = Body(...)):
    """Earlier flattened pictures of the shot that no clip shows (asked when the workspace opens, so an undo in the
    editor during a session never points at a removed file)."""
    return _guard(lambda: {"forgotten": imagedoc.forget_old_flats(name, shot, set(used))})


def api_frame_file(name: str, id: str, request: Request):
    return _send(_guard(lambda: frames.path_of(name, id)), request, "image/png", cache="public, max-age=86400")


async def api_frame_draw(request: Request):
    """multipart: name, frame, prompt, negative, strength, seed and (optional) mask, a PNG painted white where to draw"""
    form = await request.form()
    mask = form.get("mask")
    data = await mask.read() if mask is not None and hasattr(mask, "read") else None
    return _guard(lambda: frames.draw(form["name"], form["frame"], data, form.get("prompt", ""), form.get("negative", ""),
                                      float(form.get("strength", 0.75)), int(form.get("seed", -1))))


def api_frame_store(name: str = Body(...), image: str = Body(...), source: str = Body(None)):
    """A frame the page drew itself (data URL), kept like any other."""
    def keep():
        import base64
        import io as _io
        from PIL import Image
        raw = base64.b64decode(image.split(",", 1)[-1])
        with Image.open(_io.BytesIO(raw)) as im:
            fid = frames.store(name, im.convert("RGB"), {"from": source, "page": True})
        return {"frame": fid}
    return _guard(keep)


def api_frame_carry(name: str = Body(...), web: str = Body(...), seconds: float = Body(...), original: str = Body(...),
                    edited: str = Body(...)):
    return _guard(lambda: frames.carry(name, web, seconds, original, edited))


def api_frame_still(name: str = Body(...), id: str = Body(...)):
    """The edited frame as a picture of the project (for putting it in as a still)."""
    def still():
        import shutil
        src = frames.path_of(name, id)
        target = os.path.join(projects._folder(name), "media", f"frame-{id}.png")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copyfile(src, target)
        web = projects.add_media(name, target)
        return {"web": web, "url": _project_media_url(name, web), "path": target}
    return _guard(still)


# ---- sound and words ----

def api_sound_options():
    return dict(sound.available(), loudness=sound.presets())


def api_sound_captions(name: str = Body(...), doc: dict = Body(...), language: str = Body("Hebrew")):
    return _guard(lambda: sound.captions(name, doc, language))


def api_sound_voice(name: str = Body(...), text: str = Body(...), language: str = Body("Hebrew"),
                    voice: str = Body(None), expressive: float = Body(0.5), seed: int = Body(0)):
    return _guard(lambda: sound.voice(name, text, language, voice, expressive, seed))


def api_sound_music(name: str = Body(...), caption: str = Body(...), seconds: float = Body(30.0), seed: int = Body(-1)):
    return _guard(lambda: sound.music(name, caption, seconds, seed))


def api_sound_effects(name: str = Body(...), web: str = Body(None), words: str = Body(""), seed: int = Body(0),
                      seconds: float = Body(None), start: float = Body(0), speed: float = Body(1)):
    return _guard(lambda: sound.effects(name, web, words, seed, seconds, start, speed))


def api_sound_library():
    return _guard(lambda: {"items": soundbank.listing()})


def api_sound_save(name: str = Body(...), web: str = Body(...), label: str = Body(""), kind: str = Body("effects"), prompt: str = Body("")):
    return _guard(lambda: soundbank.save(name, web, label, kind, prompt))


def api_sound_remove(id: str = Body(..., embed=True)):
    return _guard(lambda: soundbank.remove(id))


def api_sound_use(name: str = Body(...), id: str = Body(...)):
    return _guard(lambda: soundbank.use(name, id))


def api_sound_file(id: str, request: Request):
    return _send(_guard(lambda: soundbank._path(soundbank.get(id))), request)


def api_sound_install():
    return frames._task("sound-pack", lambda task: soundbank.install())


# ---- layers and titles ----

def api_layers_options():
    return layers.available()


def api_lips_options():
    options = lipsync.available()
    try:
        options.update(voice_samples=lipspeech.voices(), deliveries=list(lipspeech.DELIVERIES))
    except Exception:
        options.update(voice_samples=[], deliveries=list(lipspeech.DELIVERIES))
    return options


def api_lips_speech(name: str = Body(...), text: str = Body(...), language: str = Body('Hebrew'), voice: str = Body(...),
                    seconds: float = Body(...), delivery: str = Body('Natural'), expressive: float = Body(None), seed: int = Body(0)):
    return _guard(lambda: lipspeech.speech(name, text, language, voice, seconds, delivery, expressive, seed))


def api_lips_prepare(name: str = Body(...), web: str = Body(...), seconds: float = Body(...), speed: float = Body(1),
                     pitch: float = Body(0), tone: float = Body(0), gain: float = Body(0), fit: bool = Body(True)):
    return _guard(lambda: lipspeech.prepare(name, web, seconds, speed, pitch, tone, gain, fit))


def api_lips_sample(voice: str = Body(..., embed=True)):
    return _guard(lambda: lipspeech.sample(voice))


def api_lips_edit(name: str = Body(...), web: str = Body(...), seconds: float = Body(...), segments: list = Body(...)):
    return _guard(lambda: lipspeech.edit(name, web, seconds, segments))


def api_lips_voice_file(voice: str, request: Request):
    return _send(_guard(lambda: lipspeech.sample_path(voice)), request)


def api_lips(name: str = Body(...), web: str = Body(...), start: float = Body(0.0), seconds: float = Body(...),
             mode: str = Body(...), options: dict = Body(None)):
    """A clip's used part made to say something: mode 'sound' / 'text' / 'face' (a task: follow it for {web, speech})."""
    return _guard(lambda: lipsync.lipsync(name, web, start, seconds, mode, options))


def api_layers_reframe(name: str = Body(...), web: str = Body(...), start: float = Body(0.0), seconds: float = Body(...)):
    """Where the subject is through part of a video (a task: follow it for {points})."""
    return _guard(lambda: layers.reframe(name, web, start, seconds))


def api_layers_cutout(name: str = Body(...), web: str = Body(None), id: int = Body(None)):
    def run():
        target = web if web else projects.add_media(name, library.get(id)["path"])
        return layers.cutout(name, target)
    return _guard(run)


def api_layers_title(name: str = Body(...), image: str = Body(...), label: str = Body("title")):
    def keep():
        r = layers.title(name, image, label)
        return dict(r, url=_project_media_url(name, r["web"]))
    return _guard(keep)


def api_task(id: str):
    return _guard(lambda: frames.tasks[id])


# ---- sending from other tabs ----

def _sendable_roots():
    roots = list(paths.library_roots()) + [tempfile.gettempdir(), paths.projects_dir()]
    if os.environ.get("GRADIO_TEMP_DIR"):
        roots.append(os.environ["GRADIO_TEMP_DIR"])
    return roots


def _path_from(value):
    """A file path from what a page shows: '/file=D:/x.png?123', a full URL of that, or a plain path."""
    value = unquote(value or "")
    if "file=" in value:
        value = value.split("file=", 1)[1]
    elif value.startswith(("http://", "https://")):
        value = urlsplit(value).path
    value = value.split("?", 1)[0]
    return os.path.normpath(value)


def api_inbox_add(paths_: list = Body(..., embed=True, alias="paths")):
    added = []
    for value in paths_:
        path = _path_from(value)
        if not os.path.isfile(path) or not paths.inside(path, _sendable_roots()):
            continue
        try:
            item = _with_urls(library.add_file(path))
        except ValueError:
            continue
        inbox.append({"item": item, "at": time.time()})
        added.append(item)
    if not added:
        raise HTTPException(status_code=400, detail="nothing that Forge Studio can use was sent")
    return {"added": added}


def api_inbox_take():
    taken = list(inbox)
    inbox.clear()
    return {"items": [t["item"] for t in taken]}


def api_open_folder(path: str = Body(..., embed=True)):
    path = os.path.normpath(path)
    if not paths.inside(path, _sendable_roots() + [paths.exports_dir()]):
        raise HTTPException(status_code=403, detail="not a Forge Studio folder")
    target = path if os.path.isdir(path) else os.path.dirname(path)
    os.startfile(target)  # noqa: S606 - opens Explorer on the user's own machine
    return {"ok": True}


def register(app: FastAPI):
    lipsync.APP = app
    imagegen.APP = app
    route = app.add_api_route
    route("/forge-studio/options", api_options, methods=["GET"])
    route("/forge-studio/library", api_library, methods=["GET"])
    route("/forge-studio/library/folders", api_library_folders, methods=["GET"])
    route("/forge-studio/library/scan", api_library_scan, methods=["POST"])
    route("/forge-studio/library/info", api_library_info, methods=["GET"])
    route("/forge-studio/thumb/{media_id}", api_thumb, methods=["GET"])
    route("/forge-studio/media/{media_id}", api_media, methods=["GET"])
    route("/forge-studio/projects", api_projects, methods=["GET"])
    route("/forge-studio/project", api_project, methods=["GET"])
    route("/forge-studio/project/create", api_project_create, methods=["POST"])
    route("/forge-studio/project/bin", api_project_bin, methods=["GET"])
    route("/forge-studio/project/thumb", api_project_thumb, methods=["GET"])
    route("/forge-studio/still/export", api_frame_export, methods=["POST"])
    route("/forge-studio/project/save", api_project_save, methods=["POST"])
    route("/forge-studio/project/add", api_project_add, methods=["POST"])
    route("/forge-studio/project/media", api_project_media, methods=["GET"])
    route("/forge-studio/project/upload", api_project_upload, methods=["POST"])
    route("/forge-studio/probe", api_probe, methods=["GET"])
    route("/forge-studio/strip", api_strip, methods=["GET"])
    route("/forge-studio/wave", api_wave, methods=["GET"])
    route("/forge-studio/beats", api_beats, methods=["GET"])
    route("/forge-studio/talk", api_talk, methods=["GET"])
    route("/forge-studio/clip/still", api_clip_still, methods=["POST"])
    route("/forge-studio/clip/reverse", api_clip_reverse, methods=["POST"])
    route("/forge-studio/clip/merge", api_clip_merge, methods=["POST"])
    route("/forge-studio/still/save", api_still_save, methods=["POST"])
    route("/forge-studio/render/text", api_render_text, methods=["POST"])
    route("/forge-studio/render/start", api_render_start, methods=["POST"])
    route("/forge-studio/render", api_render, methods=["GET"])
    route("/forge-studio/render/cancel", api_render_cancel, methods=["POST"])
    route("/forge-studio/render/file", api_render_file, methods=["GET"])
    route("/forge-studio/generate/options", api_gen_options, methods=["GET"])
    route("/forge-studio/generate", api_gen_submit, methods=["POST"])
    route("/forge-studio/generations", api_gen_list, methods=["GET"])
    route("/forge-studio/generate/placed", api_gen_placed, methods=["POST"])
    route("/forge-studio/generate/cancel", api_gen_cancel, methods=["POST"])
    route("/forge-studio/generate/made-with", api_gen_made_with, methods=["GET"])
    route("/forge-studio/image/new", api_image_new, methods=["POST"])
    route("/forge-studio/image/doc", api_image_doc, methods=["GET"])
    route("/forge-studio/image/file", api_image_file, methods=["GET"])
    route("/forge-studio/image/save", api_image_save, methods=["POST"])
    route("/forge-studio/image/tidy", api_image_tidy, methods=["POST"])
    route("/forge-studio/image/genoptions", api_image_genoptions, methods=["GET"])
    route("/forge-studio/image/generate", api_image_generate, methods=["POST"])
    route("/forge-studio/image/subject", api_image_subject, methods=["POST"])
    route("/forge-studio/image/object", api_image_object, methods=["POST"])
    route("/forge-studio/image/erase", api_image_erase, methods=["POST"])
    route("/forge-studio/frame/grab", api_frame_grab, methods=["POST"])
    route("/forge-studio/frame/file", api_frame_file, methods=["GET"])
    route("/forge-studio/frame/draw", api_frame_draw, methods=["POST"])
    route("/forge-studio/frame/store", api_frame_store, methods=["POST"])
    route("/forge-studio/frame/carry", api_frame_carry, methods=["POST"])
    route("/forge-studio/frame/still", api_frame_still, methods=["POST"])
    route("/forge-studio/sound/options", api_sound_options, methods=["GET"])
    route("/forge-studio/sound/captions", api_sound_captions, methods=["POST"])
    route("/forge-studio/sound/voice", api_sound_voice, methods=["POST"])
    route("/forge-studio/sound/music", api_sound_music, methods=["POST"])
    route("/forge-studio/sound/effects", api_sound_effects, methods=["POST"])
    route("/forge-studio/sound/library", api_sound_library, methods=["GET"])
    route("/forge-studio/sound/save", api_sound_save, methods=["POST"])
    route("/forge-studio/sound/remove", api_sound_remove, methods=["POST"])
    route("/forge-studio/sound/use", api_sound_use, methods=["POST"])
    route("/forge-studio/sound/file", api_sound_file, methods=["GET"])
    route("/forge-studio/sound/install", api_sound_install, methods=["POST"])
    route("/forge-studio/layers/options", api_layers_options, methods=["GET"])
    route("/forge-studio/layers/cutout", api_layers_cutout, methods=["POST"])
    route("/forge-studio/layers/reframe", api_layers_reframe, methods=["POST"])
    route("/forge-studio/lips/options", api_lips_options, methods=["GET"])
    route("/forge-studio/lips/speech", api_lips_speech, methods=["POST"])
    route("/forge-studio/lips/prepare-speech", api_lips_prepare, methods=["POST"])
    route("/forge-studio/lips/edit-speech", api_lips_edit, methods=["POST"])
    route("/forge-studio/lips/voice-sample", api_lips_sample, methods=["POST"])
    route("/forge-studio/lips/voice-file", api_lips_voice_file, methods=["GET"])
    route("/forge-studio/lips", api_lips, methods=["POST"])
    route("/forge-studio/layers/title", api_layers_title, methods=["POST"])
    route("/forge-studio/task", api_task, methods=["GET"])
    route("/forge-studio/inbox", api_inbox_add, methods=["POST"])
    route("/forge-studio/inbox/take", api_inbox_take, methods=["POST"])
    route("/forge-studio/open-folder", api_open_folder, methods=["POST"])
    route("/forge-studio/render/open-folder", api_render_open_folder, methods=["POST"])
    route("/forge-studio/pick-folder", api_pick_folder, methods=["POST"])
    # The global library is indexed on demand when its view is opened.

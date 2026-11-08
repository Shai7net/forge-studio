"""The routes under /sim360 that the page talks to."""

import mimetypes
import os
import re
import time
from urllib.parse import unquote

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from starlette.concurrency import run_in_threadpool

from . import NAME, depth, export, jobs, library, look, motion, objects, runner, segment, solid, speech, store, talk  # noqa: F401  (objects, solid and talk register their jobs)

_RANGE = re.compile(r"bytes=(\d*)-(\d*)")
_NUMBERED = re.compile(r"-(\d{4})\.\w+$")       # a file that is written once and never changed


def _guard(function):
    """Runs a store call and turns its complaints into HTTP answers."""
    try:
        return function()
    except store.StoreError as e:
        raise HTTPException(status_code=e.status, detail=str(e))
    except runner.JobError as e:
        raise HTTPException(status_code=400, detail=str(e))


# --- what this Forge has -----------------------------------------------------------------------------------------

def _lora_names():
    from modules import shared
    folder = getattr(shared.cmd_opts, "lora_dir", None)
    names = []
    if folder and os.path.isdir(folder):
        for base, _dirs, files in os.walk(folder):
            names += [os.path.splitext(f)[0] for f in files if f.lower().endswith((".safetensors", ".ckpt", ".pt"))]
    return sorted(set(names), key=str.lower)


def _motion_modules():
    from modules import paths, shared
    folder = str(shared.opts.data.get("animatediff_model_path") or "").strip() or \
        os.path.join(paths.data_path, "extensions", "sd-forge-animatediff", "model")
    if not os.path.isdir(folder):
        return []
    return sorted(f for f in os.listdir(folder) if f.lower().endswith((".ckpt", ".safetensors", ".pth")))


def _tile_everything():
    from . import tile
    return bool(tile.everything)


def api_options():
    from modules import scripts, sd_models, sd_samplers, shared
    model = shared.sd_model
    titles = sorted((c.title for c in sd_models.checkpoints_list.values()), key=str.lower)
    always = [s.title().lower() for s in scripts.scripts_txt2img.alwayson_scripts]
    loras = _lora_names()
    return {
        "name": NAME,
        "checkpoints": titles,
        "checkpoint": shared.opts.sd_model_checkpoint,
        "sdxl": bool(getattr(model, "is_sdxl", False)),
        "samplers": [s.name for s in sd_samplers.all_samplers],
        "upscalers": [u.name for u in shared.sd_upscalers],
        "latent_upscalers": list(shared.latent_upscale_modes or {}),
        "loras": loras,
        "lora_360": next((n for n in loras if "360" in n), None),
        "lora_lcm": next((n for n in loras if n.lower() == ("lcm_sdxl15" if getattr(model, "is_sdxl", False) else "lcm_sd15")), None),
        "motion_modules": _motion_modules(),
        "animatediff": "animatediff" in always,
        "controlnet": "controlnet" in always,
        "openpose_model": jobs.openpose_model(),
        "auto_select": segment.available(),
        "depth": depth.available(),
        "formats": {k: {"extension": v[0], "note": v[1]} for k, v in export.FORMATS.items()},
        "folder": store.root(),
        "recordings": store.exports_dir(),
        "tile_everything": _tile_everything(),
        "talk": talk.describe(),
        "solid": solid.state(), "motion": motion.describe(),
    }


# --- projects ----------------------------------------------------------------------------------------------------

def api_projects():
    return {"projects": _guard(store.list_projects), "folder": store.root()}


def api_project_create(name: str = Body(..., embed=True)):
    return _guard(lambda: store.create_project(name))


def api_project(name: str):
    return _guard(lambda: store.load_project(name))


def api_project_save(name: str = Body(...), doc: dict = Body(...)):
    return _guard(lambda: store.save_project(name, doc))


def api_project_delete(name: str = Body(..., embed=True)):
    return {"moved_to": _guard(lambda: store.delete_project(name))}


def api_world_delete(project: str = Body(...), world: str = Body(...)):
    return {"moved_to": _guard(lambda: store.delete_world(project, world))}


def api_tile_everything(on: bool = Body(..., embed=True)):
    """Tile X for everything Forge draws, not only for this extension's worlds (see tile.py)."""
    from . import tile
    from modules import shared
    tile.everything = bool(on)
    if on:
        tile.keep_on(shared.sd_model)
    else:
        tile.restore(force=True)
    return {"on": tile.everything}


def api_versions(name: str):
    return {"versions": _guard(lambda: store.list_versions(name)), "usage": _guard(lambda: store.usage(name)),
            "folder": _guard(lambda: store.project_dir(name))}


def api_version_save(name: str = Body(...), label: str = Body("")):
    return {"file": _guard(lambda: store.save_version(name, label))}


def api_version_restore(name: str = Body(...), file: str = Body(...)):
    return _guard(lambda: store.restore_version(name, file))


def api_version_delete(name: str = Body(...), file: str = Body(...)):
    _guard(lambda: store.delete_version(name, file))
    return {"deleted": True}


def api_project_duplicate(name: str = Body(...), new_name: str = Body(...)):
    return {"name": _guard(lambda: store.duplicate_project(name, new_name))}


def api_project_pack(name: str = Body(..., embed=True)):
    path = _guard(lambda: store.pack_project(name))
    return {"path": path, "file": os.path.basename(path), "bytes": os.path.getsize(path)}


async def api_project_unpack(request: Request, filename: str = "project.zip"):
    folder = store.root()
    os.makedirs(folder, exist_ok=True)
    incoming = os.path.join(folder, f"_incoming-{time.time_ns()}.zip")
    try:
        if await _receive(request, incoming) == 0:
            raise HTTPException(status_code=400, detail="the file is empty")
        wanted = re.sub(r"\s+\d{8}-\d{6}$", "", os.path.splitext(os.path.basename(filename))[0]) or "Imported project"
        return {"name": await run_in_threadpool(lambda: _guard(lambda: store.unpack_project(incoming, wanted)))}
    finally:
        try:
            os.remove(incoming)
        except OSError:
            pass


def api_open_folder(project: str = Body(""), exports: bool = Body(False)):
    from modules import shared
    if getattr(shared.cmd_opts, "hide_ui_dir_config", False):
        raise HTTPException(status_code=403, detail="folders are hidden on this server")
    folder = _guard(lambda: (store.exports_dir(project) if exports else store.project_dir(project)) if project else store.root())
    os.makedirs(folder, exist_ok=True)
    try:
        os.startfile(folder)
    except AttributeError:
        import subprocess
        import sys
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", folder])
    return {"folder": folder}


# --- files -------------------------------------------------------------------------------------------------------

def _piece(path, start, length, chunk=1 << 18):
    with open(path, "rb") as file:
        file.seek(start)
        left = length
        while left > 0:
            data = file.read(min(chunk, left))
            if not data:
                break
            left -= len(data)
            yield data


def api_file(project: str, path: str, request: Request):
    """A file of a project. Videos are asked for in pieces (the browser jumps around in them)."""
    full = _guard(lambda: store.resolve(project, path))
    size = os.path.getsize(full)
    mime = mimetypes.guess_type(full)[0] or "application/octet-stream"
    cache = "public, max-age=31536000, immutable" if _NUMBERED.search(full) else "no-cache"
    headers = {"Accept-Ranges": "bytes", "Cache-Control": cache}
    asked = _RANGE.match(request.headers.get("range") or "")
    if not asked:
        return FileResponse(full, media_type=mime, headers=headers)
    first, last = asked.group(1), asked.group(2)
    if first == "":
        start, end = max(0, size - int(last or 0)), size - 1
    else:
        start, end = int(first), min(int(last), size - 1) if last else size - 1
    if start > end or start >= size:
        return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
    headers.update({"Content-Range": f"bytes {start}-{end}/{size}", "Content-Length": str(end - start + 1)})
    return StreamingResponse(_piece(full, start, end - start + 1), status_code=206, media_type=mime, headers=headers)


async def _receive(request, target):
    with open(target, "wb") as file:
        async for chunk in request.stream():
            file.write(chunk)
    return os.path.getsize(target)


async def api_upload(request: Request, project: str, world: str, filename: str = "upload.png", painted: bool = False,
                     object: str = "", enlarge: float = 1.0, upscaler: str = "", keep_background: bool = False,
                     bounce: bool = False, packed: bool = False):
    """A file for a world: an upload, or (painted) the page's own panorama after it drew into it; with
    `object`, a picture or a video that becomes an object standing in the world. The file is taken in by a
    job, because a video is converted, a panorama gets its depth map and an object is cut out."""
    folder = _guard(lambda: store.world_dir(project, world))
    extension = os.path.splitext(filename)[1].lower()[:8] or ".png"
    target = os.path.join(folder, f"_incoming-{time.time_ns()}{extension}")
    if await _receive(request, target) == 0:
        os.remove(target)
        raise HTTPException(status_code=400, detail="the file is empty")
    if object:
        job = runner.submit("import_object", {"project": project, "world": world, "path": target, "filename": filename,
                                              "object": _guard(lambda: store.clean_id(object, "object")),
                                              "keep_background": keep_background, "packed": packed})
    else:
        infotext = request.headers.get("x-sim360-infotext")
        job = runner.submit("import", {"project": project, "world": world, "path": target, "filename": filename,
                                       "painted": painted, "infotext": unquote(infotext) if infotext else None,
                                       "enlarge": enlarge, "upscaler": upscaler, "bounce": bounce})
    return job.describe(False)


async def api_upload_model(request: Request, project: str, world: str, object: str, filename: str = "model.glb"):
    """A 3D model from the computer (.glb) for an object: it is checked and kept beside the object's picture."""
    folder = _guard(lambda: store.world_dir(project, world))
    object_id = _guard(lambda: store.clean_id(object, "object"))
    if os.path.splitext(filename)[1].lower() != ".glb":
        raise HTTPException(status_code=400, detail="a 3D model is a .glb file (glTF, binary)")
    incoming = os.path.join(folder, f"_incoming-{time.time_ns()}.glb")
    try:
        if await _receive(request, incoming) == 0:
            raise HTTPException(status_code=400, detail="the file is empty")
        return await run_in_threadpool(lambda: _guard(lambda: solid.take_in(project, world, object_id, incoming)))
    finally:
        try:
            os.remove(incoming)
        except OSError:
            pass


async def api_upload_motion(request: Request, project: str, world: str, object: str, filename: str = "motion.bvh"):
    """A motion capture file from the computer (.bvh, .glb, .npz) for an object."""
    folder = _guard(lambda: store.world_dir(project, world))
    object_id = _guard(lambda: store.clean_id(object, "object"))
    kind = os.path.splitext(filename)[1].lower()
    if kind not in motion.MOTION_FILES:
        raise HTTPException(status_code=400, detail="a motion is a .bvh, .glb, .npz or .fbx file")
    incoming = os.path.join(folder, f"_incoming-{time.time_ns()}{kind}")
    try:
        if await _receive(request, incoming) == 0:
            raise HTTPException(status_code=400, detail="the file is empty")
        return await run_in_threadpool(lambda: _guard(lambda: motion.take_motion_in(project, world, object_id, incoming, filename)))
    finally:
        try:
            os.remove(incoming)
        except OSError:
            pass


# --- my objects --------------------------------------------------------------------------------------------------

def api_library():
    return {"objects": _guard(library.listed), "folder": library.folder()}


def api_library_keep(project: str = Body(...), world: str = Body(...), object: dict = Body(...)):
    return _guard(lambda: library.keep(project, world, object))


def api_library_use(id: str = Body(...), project: str = Body(...), world: str = Body(...), object: str = Body(...)):
    return _guard(lambda: library.use(id, project, world, object))


def api_library_drop(id: str = Body(..., embed=True)):
    return {"dropped": _guard(lambda: library.drop(id))}


def api_library_file(kept: str, name: str):
    full = _guard(lambda: library.file(kept, name))
    return FileResponse(full, media_type=mimetypes.guess_type(full)[0] or "application/octet-stream", headers={"Cache-Control": "no-cache"})


def api_solid_state():
    """Whether Hunyuan3D is installed, whether one is running (ours, or Pinokio's), how much memory is free."""
    return solid.state()


def api_solid_stop():
    """Closes the Hunyuan3D that was started from here, so its memory is free again."""
    return {"stopped": solid.stop()}


async def api_portal_mask(request: Request, project: str, world: str, portal: str, version: int = 0):
    """A portal's mask. Every change of its shape is a new file, so an older version of the project still
    finds the shape it had."""
    folder = _guard(lambda: store.world_dir(project, world))
    name = f"portal-{_guard(lambda: store.clean_id(portal, 'portal'))}-{max(0, int(version)):04d}.png"
    await _receive(request, os.path.join(folder, name))
    return {"file": name}


async def api_speech_audio(request: Request, project: str, world: str, line: str, filename: str = "line.wav", plain: bool = False):
    """A sound file the user brings for a line (a recording, speech made elsewhere). It is kept as the same
    tidy WAV a Windows voice would have made."""
    folder = _guard(lambda: store.world_dir(project, world))
    line = _guard(lambda: store.clean_id(line, "line"))
    extension = os.path.splitext(filename)[1].lower()[:8] or ".wav"
    incoming = os.path.join(folder, f"_incoming-{time.time_ns()}{extension}")
    try:
        if await _receive(request, incoming) == 0:
            raise HTTPException(status_code=400, detail="the file is empty")
        name = f"speech-{line}-{speech.next_number(folder, line):04d}.wav"
        take = speech.plain if plain else speech.tidy          # music and effects stay as they are; a spoken line is tidied
        seconds = await run_in_threadpool(lambda: _guard(lambda: take(incoming, os.path.join(folder, name))))
        return {"audio": name, "seconds": round(seconds, 3)}
    finally:
        try:
            os.remove(incoming)
        except OSError:
            pass


# --- jobs --------------------------------------------------------------------------------------------------------

def api_job_start(kind: str = Body(...), params: dict = Body(...)):
    if kind in ("import", "import_object"):
        raise HTTPException(status_code=400, detail="files arrive through /sim360/upload")
    if params.get("project"):
        _guard(lambda: store.project_dir(params["project"]))
    return _guard(lambda: runner.submit(kind, params)).describe(False)


def api_job(job_id: str, preview: bool = True):
    try:
        return runner.get(job_id).describe(preview)
    except KeyError:
        raise HTTPException(status_code=404, detail="no such job")


def api_job_cancel(job_id: str = Body(..., embed=True)):
    try:
        return runner.cancel(job_id).describe(False)
    except KeyError:
        raise HTTPException(status_code=404, detail="no such job")


# --- recording ---------------------------------------------------------------------------------------------------

def api_export_start(project: str = Body(...), name: str = Body("tour"), width: int = Body(...), height: int = Body(...),
                     fps: float = Body(30), kind: str = Body("mp4-h264"), quality: int = Body(16),
                     source_width: int = Body(0), source_height: int = Body(0), subtitles: list = Body(None),
                     burn_subtitles: bool = Body(False)):
    """`subtitles`: [{start, end, text}] in seconds. They are always written beside the video as an .srt
    file; with `burn_subtitles` they are also drawn into the picture."""
    if not (16 <= width <= 8192 and 16 <= height <= 8192 and 1 <= fps <= 240):
        raise HTTPException(status_code=400, detail="bad video size or frame rate")
    folder = _guard(lambda: store.exports_dir(project))
    try:
        session = export.start(folder, store.clean_name(name), width, height, fps, kind, quality,
                               source_width or width, source_height or height, subtitles, burn_subtitles)
    except (ValueError, store.StoreError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"id": session.id, "path": session.path, "width": session.width, "height": session.height}


async def api_export_frame(request: Request, id: str):
    try:
        session = export.get(id)
    except KeyError:
        raise HTTPException(status_code=404, detail="no such recording")
    pixels = await request.body()
    try:
        await run_in_threadpool(session.add, pixels)
    except (ValueError, RuntimeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"frames": session.frames}


async def api_export_finish(id: str = Body(...), project: str = Body(""), audio: list = Body(None), ambience: list = Body(None),
                            loudness: float = Body(None), duck: float = Body(0)):
    """`audio`: the lines that are heard, [{world, file, start, gain, speech, seconds}]; `ambience`: stretches
    of weather that should be heard, [{kind, start, end, gain}]. `loudness`: the LUFS the mix is brought to
    (-14 is what video sites play at), nothing to leave it alone; `duck`: dB by which sounds that are not
    speech step back while somebody speaks."""
    session = export.end(id)
    if session is None:
        raise HTTPException(status_code=404, detail="no such recording")
    tracks = []
    for track in audio or []:
        try:
            path = store.resolve(project, f"worlds/{store.clean_id(track.get('world'), 'world')}/{os.path.basename(str(track.get('file')))}")
        except store.StoreError:
            continue
        tracks.append({"path": path, "start": max(0.0, float(track.get("start") or 0)), "gain": float(track.get("gain") or 1),
                       "speech": bool(track.get("speech")), "seconds": max(0.0, float(track.get("seconds") or 0))})
    loud = None if loudness is None else max(-30.0, min(-8.0, float(loudness)))
    try:
        return await run_in_threadpool(lambda: session.finish(tracks, ambience or [], loud, max(0.0, min(24.0, float(duck or 0)))))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


async def api_export_cancel(id: str = Body(..., embed=True)):
    session = export.end(id)
    if session is not None:
        await run_in_threadpool(session.cancel)
    return {"cancelled": session is not None}


def api_export_file(project: str, name: str, request: Request):
    """A finished recording, for the player on the page."""
    folder = os.path.realpath(_guard(lambda: store.exports_dir(project)))
    full = os.path.realpath(os.path.join(folder, os.path.basename(name)))
    if not full.startswith(folder + os.sep) or not os.path.isfile(full):
        raise HTTPException(status_code=404, detail="no such recording")
    return FileResponse(full, media_type=mimetypes.guess_type(full)[0] or "application/octet-stream",
                        headers={"Accept-Ranges": "bytes", "Cache-Control": "no-cache"})


def register(app: FastAPI):
    runner.set_app(app)
    route = app.add_api_route
    route("/sim360/options", api_options, methods=["GET"])
    route("/sim360/projects", api_projects, methods=["GET"])
    route("/sim360/project", api_project, methods=["GET"])
    route("/sim360/project/create", api_project_create, methods=["POST"])
    route("/sim360/project/save", api_project_save, methods=["POST"])
    route("/sim360/project/delete", api_project_delete, methods=["POST"])
    route("/sim360/world/delete", api_world_delete, methods=["POST"])
    route("/sim360/tile-everything", api_tile_everything, methods=["POST"])
    route("/sim360/project/versions", api_versions, methods=["GET"])
    route("/sim360/project/version/save", api_version_save, methods=["POST"])
    route("/sim360/project/version/restore", api_version_restore, methods=["POST"])
    route("/sim360/project/version/delete", api_version_delete, methods=["POST"])
    route("/sim360/project/duplicate", api_project_duplicate, methods=["POST"])
    route("/sim360/project/pack", api_project_pack, methods=["POST"])
    route("/sim360/project/unpack", api_project_unpack, methods=["POST"])
    route("/sim360/open-folder", api_open_folder, methods=["POST"])
    route("/sim360/file/{project}/{path:path}", api_file, methods=["GET"])
    route("/sim360/upload", api_upload, methods=["POST"])
    route("/sim360/upload-model", api_upload_model, methods=["POST"])
    route("/sim360/upload-motion", api_upload_motion, methods=["POST"])
    route("/sim360/solid/state", api_solid_state, methods=["GET"])
    route("/sim360/library", api_library, methods=["GET"])
    route("/sim360/library/keep", api_library_keep, methods=["POST"])
    route("/sim360/library/use", api_library_use, methods=["POST"])
    route("/sim360/library/drop", api_library_drop, methods=["POST"])
    route("/sim360/library/file/{kept}/{name}", api_library_file, methods=["GET"])
    route("/sim360/solid/stop", api_solid_stop, methods=["POST"])
    route("/sim360/portal-mask", api_portal_mask, methods=["POST"])
    route("/sim360/speech-audio", api_speech_audio, methods=["POST"])
    route("/sim360/job/start", api_job_start, methods=["POST"])
    route("/sim360/job", api_job, methods=["GET"])
    route("/sim360/job/cancel", api_job_cancel, methods=["POST"])
    route("/sim360/export/start", api_export_start, methods=["POST"])
    route("/sim360/export/frame", api_export_frame, methods=["POST"])
    route("/sim360/export/finish", api_export_finish, methods=["POST"])
    route("/sim360/export/cancel", api_export_cancel, methods=["POST"])
    route("/sim360/export/file/{project}/{name}", api_export_file, methods=["GET"])

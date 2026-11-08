"""The routes under /time-strata that the page talks to."""

import json
import mimetypes
import os
import re
import shutil
import subprocess
import time
from urllib.parse import quote

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse

from . import NAME, VERSION, chain, cut, deforum, dqt, export, jobs, store, upscale  # noqa: F401  (chain and deforum register their jobs)

_RANGE = re.compile(r"bytes=(\d*)-(\d*)")


def _guard(function):
    try:
        return function()
    except store.StoreError as e:
        raise HTTPException(status_code=e.status, detail=str(e))
    except jobs.JobError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e).strip("'\""))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


def _deforum_present():
    return bool(deforum._deforum_dir())


def api_options():
    return {"name": NAME, "version": VERSION, "folder": store.root(), "exports": store.exports_dir(),
            "models": cut.model_names(), "kinds": list(export.KINDS), "deforum": _deforum_present(), "upscale": upscale.available(),
            "sim360": os.path.isdir(_sim360_root() or "")}


def api_deforum_options():
    """What generating with Deforum can use here, and the user's last Deforum sampler, steps and CFG."""
    return deforum.available()


def api_dqt():
    """DeforumationQT's pan, rotation, zoom and lens now (read only), to steer the trail and the camera."""
    return dqt.read()


def api_projects():
    return {"projects": store.list_projects()}


def api_project(name: str):
    return _guard(lambda: store.load(name))


def api_project_create(name: str = Body(..., embed=True)):
    return _guard(lambda: store.create(name))


def api_project_save(name: str = Body(...), doc: dict = Body(...)):
    return _guard(lambda: store.save(name, doc))


def api_project_delete(name: str = Body(..., embed=True)):
    return _guard(lambda: store.delete(name))


def api_chain(project: str, chain: str, measures: bool = False):
    def read():
        record = store.read_chain(project, chain)
        if record is None:
            raise store.StoreError("this chain has not been made yet", 404)
        if not measures:
            record = {k: v for k, v in record.items() if k not in ("measures", "plan")}
        return record
    return _guard(read)


def api_chain_delete(project: str = Body(...), chain: str = Body(...)):
    return _guard(lambda: store.delete_chain(project, chain))


# --- files -----------------------------------------------------------------------------------------------------

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


def _serve(full, request, immutable=False):
    size = os.path.getsize(full)
    mime = mimetypes.guess_type(full)[0] or ("image/webp" if full.lower().endswith(".webp") else "application/octet-stream")
    headers = {"Accept-Ranges": "bytes", "Cache-Control": "public, max-age=31536000, immutable" if immutable else "no-cache"}
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


def api_file(project: str, path: str, request: Request):
    full = _guard(lambda: store.resolve(project, path))
    # a chain's frames live in numbered makes (v0003/…) that are never changed, so the browser may keep them
    return _serve(full, request, immutable=bool(re.search(r"/v\d{4}/\d{6}\.webp$", full.replace("\\", "/"))))


async def api_upload(request: Request, project: str, filename: str = "upload", folder: str = ""):
    """A file from the computer into the project's uploads (or a folder inside them, for a set of pictures)."""
    base = _guard(lambda: store.uploads_dir(project))
    if folder:
        _guard(lambda: store.clean_id(folder, "folder"))
        base = os.path.join(base, folder)
        os.makedirs(base, exist_ok=True)
    name = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(filename))[-80:] or "upload"
    stem, extension = os.path.splitext(name)
    target = os.path.join(base, name)
    n = 2
    while os.path.exists(target):
        target = os.path.join(base, f"{stem}-{n}{extension}")
        n += 1
    with open(target, "wb") as file:
        async for piece in request.stream():
            file.write(piece)
    if os.path.getsize(target) == 0:
        os.remove(target)
        raise HTTPException(status_code=400, detail="the file is empty")
    answer = {"path": store.relative(project, target), "size": os.path.getsize(target)}
    if extension.lower() in (".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v", ".gif"):
        from . import media
        try:
            answer["video"] = media.probe(target)
        except Exception as e:
            answer["video_error"] = str(e)
    return answer


# --- jobs ------------------------------------------------------------------------------------------------------

def api_job_start(kind: str = Body(...), params: dict = Body(...)):
    return _guard(lambda: jobs.submit(kind, params).describe(False))


def api_job(id: str, preview: bool = True):
    return _guard(lambda: jobs.get(id).describe(preview))


def api_job_cancel(id: str = Body(..., embed=True)):
    return _guard(lambda: jobs.cancel(id).describe(False))


# --- recording -------------------------------------------------------------------------------------------------

def api_export_start(project: str = Body(""), name: str = Body("flight"), width: int = Body(...), height: int = Body(...),
                     fps: float = Body(30), kind: str = Body("mp4-h264"), quality: int = Body(17), supersample: int = Body(1),
                     music: str = Body(""), music_start: float = Body(0.0)):
    def begin():
        sound = store.resolve(project, music) if music and project else None
        session = export.start(name=name, width=width, height=height, fps=fps, kind=kind, quality=quality,
                               supersample=supersample, music=sound, music_start=music_start)
        return {"id": session.id, "drawn": session.drawn, "path": session.path}
    return _guard(begin)


async def api_export_frame(request: Request, id: str, jpeg: bool = False):
    session = _guard(lambda: export.get(id))
    data = await request.body()
    _guard(lambda: session.frame(data, jpeg))
    return {"frames": session.frames}


def api_export_finish(id: str = Body(..., embed=True)):
    try:
        result = export.finish(id)
    except KeyError:
        raise HTTPException(status_code=404, detail="no such recording")
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    result["url"] = f"./time-strata/export/file/{quote(result['name'])}"
    return result


def api_export_cancel(id: str = Body(..., embed=True)):
    export.cancel(id)
    return {"cancelled": True}


def api_export_file(name: str, request: Request):
    folder = os.path.realpath(store.exports_dir())
    full = os.path.realpath(os.path.join(folder, os.path.basename(name)))
    if not full.startswith(folder + os.sep) or not os.path.isfile(full):
        raise HTTPException(status_code=404, detail="no such file")
    return _serve(full, request)


def api_open_folder(project: str = Body(""), exports: bool = Body(False)):
    folder = store.exports_dir() if exports else (_guard(lambda: store.project_dir(project)) if project else store.root())
    if os.name == "nt":
        subprocess.Popen(["explorer", os.path.normpath(folder)])
    return {"folder": folder}


# --- worlds from Simulation 360 -------------------------------------------------------------------------------

def _sim360_root():
    try:
        from modules import paths, shared
        chosen = str(shared.opts.data.get("sim360_projects_dir") or "").strip()
        return os.path.abspath(chosen) if chosen else os.path.join(paths.data_path, "simulation-360")
    except Exception:
        # outside Forge: beside this extension's own webui folder (webui/extensions/<this>/strata/api.py)
        webui = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        return os.path.join(webui, "simulation-360")


def api_sim360_worlds():
    """Every world of every Simulation 360 project that has a still panorama to lend."""
    base = _sim360_root()
    found = []
    if not base or not os.path.isdir(base):
        return {"worlds": found}
    for project in sorted(os.listdir(base), key=str.lower):
        path = os.path.join(base, project, "project.json")
        if project.startswith("_") or not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf8") as file:
                doc = json.load(file)
        except Exception:
            continue
        for world in doc.get("worlds") or []:
            still = world.get("file") if world.get("kind") != "video" else world.get("poster")
            folder = os.path.join(base, project, "worlds", str(world.get("id")))
            if still and os.path.isfile(os.path.join(folder, still)):
                found.append({"project": project, "world": world.get("id"), "name": world.get("name") or world.get("id"),
                              "thumb": world.get("thumb"), "file": still, "video": world.get("kind") == "video",
                              "eye": world.get("eye")})
    return {"worlds": found}


def api_sim360_thumb(project: str, world: str, name: str, request: Request):
    base = os.path.realpath(_sim360_root() or "")
    full = os.path.realpath(os.path.join(base, project, "worlds", world, os.path.basename(name)))
    if not full.startswith(base + os.sep) or not os.path.isfile(full):
        raise HTTPException(status_code=404, detail="no such file")
    return _serve(full, request)


def api_sim360_take(project: str = Body(...), from_project: str = Body(...), world: str = Body(...), file: str = Body(...)):
    """A Simulation 360 world's panorama copied into this project (so the project does not depend on it)."""
    base = os.path.realpath(_sim360_root() or "")
    source = os.path.realpath(os.path.join(base, from_project, "worlds", world, os.path.basename(file)))
    if not source.startswith(base + os.sep) or not os.path.isfile(source):
        raise HTTPException(status_code=404, detail="no such world")
    folder = _guard(lambda: store.uploads_dir(project))
    target = os.path.join(folder, f"world-{re.sub(r'[^A-Za-z0-9_-]', '_', world)}-{int(time.time())}{os.path.splitext(source)[1]}")
    shutil.copyfile(source, target)
    return {"path": store.relative(project, target)}


def register(app: FastAPI):
    route = app.add_api_route
    route("/time-strata/options", api_options, methods=["GET"])
    route("/time-strata/deforum/options", api_deforum_options, methods=["GET"])
    route("/time-strata/dqt", api_dqt, methods=["GET"])
    route("/time-strata/projects", api_projects, methods=["GET"])
    route("/time-strata/project", api_project, methods=["GET"])
    route("/time-strata/project/create", api_project_create, methods=["POST"])
    route("/time-strata/project/save", api_project_save, methods=["POST"])
    route("/time-strata/project/delete", api_project_delete, methods=["POST"])
    route("/time-strata/chain", api_chain, methods=["GET"])
    route("/time-strata/chain/delete", api_chain_delete, methods=["POST"])
    route("/time-strata/file/{project}/{path:path}", api_file, methods=["GET"])
    route("/time-strata/upload", api_upload, methods=["POST"])
    route("/time-strata/job/start", api_job_start, methods=["POST"])
    route("/time-strata/job", api_job, methods=["GET"])
    route("/time-strata/job/cancel", api_job_cancel, methods=["POST"])
    route("/time-strata/export/start", api_export_start, methods=["POST"])
    route("/time-strata/export/frame", api_export_frame, methods=["POST"])
    route("/time-strata/export/finish", api_export_finish, methods=["POST"])
    route("/time-strata/export/cancel", api_export_cancel, methods=["POST"])
    route("/time-strata/export/file/{name}", api_export_file, methods=["GET"])
    route("/time-strata/open-folder", api_open_folder, methods=["POST"])
    route("/time-strata/sim360/worlds", api_sim360_worlds, methods=["GET"])
    route("/time-strata/sim360/thumb/{project}/{world}/{name}", api_sim360_thumb, methods=["GET"])
    route("/time-strata/sim360/take", api_sim360_take, methods=["POST"])

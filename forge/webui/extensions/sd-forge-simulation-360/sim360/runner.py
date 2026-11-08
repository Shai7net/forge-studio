"""Drawing with the loaded checkpoint, and the queue of jobs the page asks for.

A job is drawn the way Forge's own API draws (the same processing objects, the same scripts, so ControlNet
and AnimateDiff take their settings the way they do from the API), but from inside, so that the picture
never leaves the program and the processing object can carry our own wishes (Tile X).

One job runs at a time, on one worker thread. The page asks for a job, gets its id at once, and asks about
it until it is done; while it draws, the answers carry the progress and Forge's live preview.
"""

import base64
import io
import threading
import time
import traceback
import uuid
from contextlib import closing

from PIL import Image

from . import tile

JOBS_KEPT = 30

_jobs = {}
_order = []
_queue = []
_wake = threading.Condition()
_worker = None
_app = None                 # the FastAPI app, to find Forge's API object
_defaults = {}              # script defaults when Forge was started without its API
_handlers = {}              # kind -> function(job) -> result


class JobError(Exception):
    """Something the user should read as it is."""


class Job:
    def __init__(self, kind, params):
        self.id = uuid.uuid4().hex[:12]
        self.kind = kind
        self.params = params
        self.status = "waiting"         # waiting, running, done, failed, cancelled
        self.stage = ""
        self.result = None
        self.error = None
        self.created = time.time()
        self.started = None
        self.finished = None
        self.draws = False              # whether Forge's progress belongs to this job right now
        self.share = None               # how far a job that Forge does not draw has come, 0..1
        self.preview_id = -1

    def describe(self, with_preview=True):
        answer = {"id": self.id, "kind": self.kind, "status": self.status, "stage": self.stage,
                  "result": self.result, "error": self.error,
                  "seconds": round((self.finished or time.time()) - (self.started or self.created), 1),
                  "position": _queue.index(self) + 1 if self in _queue else 0}
        if self.status == "running" and self.draws:
            answer.update(_progress(self, with_preview))
        elif self.status == "running" and self.share is not None:
            answer["progress"] = min(max(float(self.share), 0.0), 1.0)
        return answer


def set_app(app):
    global _app
    _app = app


def handler(kind):
    def register(function):
        _handlers[kind] = function
        return function
    return register


# --- the queue ---------------------------------------------------------------------------------------------------

def submit(kind, params):
    global _worker
    if kind not in _handlers:
        raise JobError(f"unknown job: {kind}")
    job = Job(kind, params)
    with _wake:
        _jobs[job.id] = job
        _order.append(job.id)
        while len(_order) > JOBS_KEPT:
            old = _jobs.get(_order[0])
            if old is not None and old.status in ("waiting", "running"):
                break
            _jobs.pop(_order.pop(0), None)
        _queue.append(job)
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_work, name="sim360-jobs", daemon=True)
            _worker.start()
        _wake.notify()
    return job


def get(job_id):
    job = _jobs.get(job_id)
    if job is None:
        raise KeyError("no such job")
    return job


def cancel(job_id):
    from modules import shared
    job = get(job_id)
    with _wake:
        if job in _queue:
            _queue.remove(job)
            job.status = "cancelled"
            job.finished = time.time()
            return job
    if job.status == "running":
        job.status = "cancelling"
        if job.draws:
            shared.state.interrupt()
    return job


def _work():
    while True:
        with _wake:
            while not _queue:
                _wake.wait(timeout=60)
                if not _queue:
                    return
            job = _queue.pop(0)
        job.status = "running"
        job.started = time.time()
        try:
            job.result = _handlers[job.kind](job)
            job.status = "cancelled" if job.status == "cancelling" else "done"
        except JobError as e:
            job.error = str(e)
            job.status = "failed"
        except Exception as e:
            traceback.print_exc()
            job.error = f"{type(e).__name__}: {e}"
            job.status = "failed"
        finally:
            job.draws = False
            job.finished = time.time()


def _progress(job, with_preview):
    from modules import shared
    state = shared.state
    progress = 0.0
    if state.job_count > 0:
        progress += state.job_no / state.job_count
    if state.sampling_steps > 0 and state.job_count > 0:
        progress += 1 / state.job_count * state.sampling_step / state.sampling_steps
    answer = {"progress": min(max(progress, 0.0), 1.0), "step": state.sampling_step, "steps": state.sampling_steps}
    if with_preview:
        try:
            state.set_current_image()
            if state.current_image is not None and state.id_live_preview != job.preview_id:
                job.preview_id = state.id_live_preview
                buffer = io.BytesIO()
                state.current_image.convert("RGB").save(buffer, format="JPEG", quality=80)
                answer["preview"] = "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
        except Exception:
            pass
    return answer


# --- drawing -----------------------------------------------------------------------------------------------------

def _forge_api():
    """Forge's API object, when Forge runs with --api: it has already worked out every script's defaults."""
    for route in getattr(_app, "routes", []) or []:
        if getattr(route, "path", None) == "/sdapi/v1/txt2img":
            return getattr(route.endpoint, "__self__", None)
    return None


def _default_script_args(runner, is_img2img):
    api = _forge_api()
    if api is not None:
        api.default_script_args_ready.wait() if hasattr(api, "default_script_args_ready") else None
        return list(api.default_script_arg_img2img if is_img2img else api.default_script_arg_txt2img)
    if is_img2img not in _defaults:
        import gradio as gr
        last = 1
        for script in runner.scripts:
            last = max(last, script.args_to or 0)
        args = [None] * last
        args[0] = 0
        with gr.Blocks():
            for script in runner.scripts:
                controls = script.ui(script.is_img2img)
                if controls:
                    args[script.args_from:script.args_to] = [c.value for c in controls]
        _defaults[is_img2img] = args
    return list(_defaults[is_img2img])


def _script_args(runner, is_img2img, alwayson):
    args = _default_script_args(runner, is_img2img)
    for title, values in (alwayson or {}).items():
        script = next((s for s in runner.alwayson_scripts if s.title().lower() == title.lower()), None)
        if script is None:
            raise JobError(f"'{title}' is not installed or not enabled in Forge")
        for n, value in enumerate(values[:script.args_to - script.args_from]):
            args[script.args_from + n] = value
    return args


def sampler_name(wanted):
    from modules import sd_samplers
    names = [s.name for s in sd_samplers.all_samplers]
    if wanted in names:
        return wanted
    lowered = {n.lower(): n for n in names}
    if str(wanted).lower() in lowered:
        return lowered[str(wanted).lower()]
    raise JobError(f"no sampler named '{wanted}'")


def draw(job, settings, *, alwayson=None, tile_x=False, tile_start=0, init_image=None, mask=None, outdir=None):
    """Draws with the loaded checkpoint. `settings` are the processing object's own fields.
    With `init_image` it is img2img (and inpainting when there is a `mask`). Returns Forge's `Processed`."""
    from modules import scripts, shared
    from modules.call_queue import queue_lock
    from modules.processing import StableDiffusionProcessingImg2Img, StableDiffusionProcessingTxt2Img, process_images

    is_img2img = init_image is not None
    runner = scripts.scripts_img2img if is_img2img else scripts.scripts_txt2img
    script_args = _script_args(runner, is_img2img, alwayson)
    settings = dict(settings)
    settings["sampler_name"] = sampler_name(settings.get("sampler_name") or "Euler a")
    settings.setdefault("do_not_save_samples", True)
    settings.setdefault("do_not_save_grid", True)
    settings.setdefault("batch_size", 1)
    settings.setdefault("n_iter", 1)
    checkpoint = settings.pop("checkpoint", None)
    if checkpoint:
        # the chosen checkpoint stays loaded afterwards: going back and forth would cost a reload each time
        settings["override_settings"] = {**(settings.get("override_settings") or {}), "sd_model_checkpoint": checkpoint}
        settings["override_settings_restore_afterwards"] = False

    job.stage = "waiting for Forge"
    with queue_lock:
        if job.status == "cancelling":
            raise JobError("cancelled")
        cls = StableDiffusionProcessingImg2Img if is_img2img else StableDiffusionProcessingTxt2Img
        if is_img2img:
            settings["init_images"] = list(init_image) if isinstance(init_image, (list, tuple)) else [init_image]      # (several: a video's frames)
            if mask is not None:
                settings["mask"] = mask
        with closing(cls(sd_model=shared.sd_model, **settings)) as p:
            p.is_api = True
            p.scripts = runner
            p.script_args = tuple(script_args)
            p.outpath_grids = outdir or (shared.opts.outdir_img2img_grids if is_img2img else shared.opts.outdir_txt2img_grids)
            p.outpath_samples = outdir or (shared.opts.outdir_img2img_samples if is_img2img else shared.opts.outdir_txt2img_samples)
            p.sim360_tile_x = bool(tile_x)
            p.sim360_tile_start = int(tile_start or 0)
            job.stage = "drawing"
            job.draws = True
            try:
                shared.state.begin(job="sim360")
                processed = process_images(p)
            finally:
                job.draws = False
                tile.restore()
                shared.state.end()
                shared.total_tqdm.clear()
    if shared.state.interrupted or job.status == "cancelling":
        raise JobError("cancelled")
    return processed


def pictures(processed):
    """The pictures of a result, without the strings AnimateDiff puts in front of them (its video, as text)."""
    return [im for im in list(processed.images) + list(getattr(processed, "extra_images", []) or []) if isinstance(im, Image.Image)]


def infotext(processed):
    texts = getattr(processed, "infotexts", None) or []
    return texts[0] if texts else (getattr(processed, "info", "") or "")


def decode_image(data):
    """A picture the page sent: a data URL or bare base64."""
    if not isinstance(data, str) or not data:
        raise JobError("a picture is missing")
    if data.startswith("data:"):
        data = data.split(",", 1)[1]
    return Image.open(io.BytesIO(base64.b64decode(data)))


def encode_image(image, kind="PNG", quality=92):
    buffer = io.BytesIO()
    if kind == "JPEG":
        image.convert("RGB").save(buffer, format="JPEG", quality=quality)
        mime = "image/jpeg"
    else:
        image.save(buffer, format="PNG")
        mime = "image/png"
    return f"data:{mime};base64," + base64.b64encode(buffer.getvalue()).decode("ascii")

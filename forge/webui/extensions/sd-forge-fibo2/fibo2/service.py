"""Coordinate FIBO with Forge's existing GPU queue and memory manager."""

import gc
import json
import math
from pathlib import Path
import sys

from . import WEBUI_DIR
from .contracts import Generation
from .runner import runner


def lora_choices():
    folder = WEBUI_DIR / "models" / "Lora"
    return sorted(str(p.relative_to(folder)).replace("\\", "/") for p in folder.rglob("*.safetensors"))


def resolve_loras(names=(), weight=1.0):
    if not math.isfinite(weight) or not -4 <= weight <= 4:
        raise ValueError("LoRA strength must be between -4 and 4")
    root = (WEBUI_DIR / "models" / "Lora").resolve()
    results = []
    for name in names:
        path = (root / name).resolve()
        if not path.is_relative_to(root) or path.suffix.lower() != ".safetensors" or not path.is_file():
            raise ValueError("Choose an existing FIBO LoRA from models/Lora")
        results.append({"path": str(path), "weight": float(weight)})
    return results


def release_forge_gpu():
    """Only offload weights. Keep Forge's selected model, settings and loader intact."""
    from ldm_patched.modules import model_management
    from modules import devices
    model_management.unload_all_models()
    flux = sys.modules.get("flux3.glue")
    engine = getattr(flux, "ENGINE", None)
    if engine is not None:
        with engine.lock:
            engine.offload_all()
    gc.collect()
    devices.torch_gc()


def keep_like_forge(response, request, p=None):
    """FIBO's pictures into the folder of every other generation (Settings -> Paths for saving: <images folder>/<date>,
    numbered, as txt2img / img2img save), through Forge's own save_image. They were only in outputs/fibo2 under job
    names (2026-11-15). An edit (pictures in) goes where img2img saves, anything else where txt2img saves; a job that
    asked not to save (the API, a script) keeps them in memory only. Each item's "path" becomes the saved file."""
    from PIL import Image
    try:
        from modules import images, shared
    except ImportError:          # outside Forge (the tests): the worker's own files are what there is
        return response
    opts = shared.opts
    if p is not None and getattr(p, "do_not_save_samples", False):
        return response
    if not opts.samples_save:
        return response
    if p is not None and getattr(p, "outpath_samples", None):
        folder = p.outpath_samples
    else:
        own = opts.outdir_img2img_samples if request.mode == "edit" else opts.outdir_txt2img_samples
        folder = opts.outdir_samples or own
    for item in response.get("images", []):
        with Image.open(item["path"]) as opened:
            image = opened.copy()
            info = dict(opened.info)
        parameters = info.pop("parameters", "")
        extra = {k: v for k, v in info.items() if isinstance(v, str)}
        seed = item.get("metadata", {}).get("seed")
        saved, _ = images.save_image(image, folder, "", seed, request.prompt, opts.samples_format, info=parameters,
                                     p=p, existing_info=extra)
        item["worker_path"], item["path"] = item["path"], saved
    return response


def tidy_jobs(keep=30):
    """The worker's job folders (its request, log and pictures): the newest `keep` stay."""
    import shutil
    from . import HOME_DIR
    jobs = HOME_DIR / "jobs"
    if not jobs.is_dir():
        return
    folders = sorted((f for f in jobs.iterdir() if f.is_dir()), key=lambda f: f.stat().st_mtime, reverse=True)
    for old in folders[keep:]:
        shutil.rmtree(old, ignore_errors=True)


def generate(request: Generation, references=(), mask=None, loras=(), lora_weight=1.0, on_progress=None,
             manage_forge_job=True, p=None):
    from modules.call_queue import queue_lock
    from modules import shared
    from . import fit, models
    if request.mode == "edit" and references:
        # the API and FIBO's panel too: references take the requested size (txt2img/img2img already did this)
        references, mask, _ = fit.fit(references, mask, fit.size16(request.width), fit.size16(request.height))
    # Invalid input or missing models must not evict the user's working model.
    request = request.validated(references, mask)
    resolved_loras = resolve_loras(loras, lora_weight)
    models.components(request.variant, request.precision)
    if not models.status()["runtime_installed"]:
        raise RuntimeError("Install the FIBO runtime using Setup in the FIBO tab")
    def run():
        # The native FIBO path bypasses process_images_inner, which normally fills these.
        # /internal/progress ignores sampling_step altogether while job_count is unknown.
        shared.state.job_count = request.count
        shared.state.job_no = 0
        shared.state.sampling_step = 0
        shared.state.sampling_steps = request.effective_steps
        release_forge_gpu()

        def update(progress):
            shared.state.textinfo = "FIBO 2.0: " + progress.get("stage", "starting")
            stage = progress.get("stage", "starting")
            if "image" in progress:
                shared.state.job_no = max(0, min(request.count - 1, int(progress["image"]) - 1))
            if "step" in progress:
                shared.state.sampling_step = progress["step"]
            if "steps" in progress:
                shared.state.sampling_steps = progress["steps"]
            if stage == "done":
                shared.state.job_no = request.count - 1
                shared.state.sampling_step = shared.state.sampling_steps
            if shared.state.interrupted or shared.state.skipped or shared.state.stopping_generation:
                runner.cancel()
            if on_progress:
                on_progress(progress)

        response = runner.run(request, references, mask, resolved_loras, update)
        try:
            keep_like_forge(response, request, p)
        finally:
            tidy_jobs()
        return response

    # txt2img/img2img already execute under Forge's FIFO lock and state lifecycle.
    # The standalone HTTP API does not, so it uses the managed path.
    if not manage_forge_job:
        return run()
    with queue_lock:
        shared.state.begin(job="FIBO 2.0")
        try:
            return run()
        finally:
            shared.state.end()


def ui_generate(variant, mode, prompt, negative, width, height, steps, cfg, seed, count,
                precision, keep_source_size, reference1, reference2, reference3, reference4,
                mask, loras, lora_weight, prompt_writer="off"):
    from PIL import Image, ImageOps
    references = [ImageOps.exif_transpose(im).convert("RGB") for im in (reference1, reference2, reference3, reference4) if im is not None]
    if keep_source_size and mode == "edit" and len(references) == 1:
        from . import fit
        width, height = fit.shape_like(references[0].size, int(width) * int(height))
    request = Generation(variant, mode, prompt, negative, width, height, steps, cfg, seed, count, precision, prompt_writer)
    response = generate(request, references, mask, loras or [], lora_weight)
    # Return independent PIL images, preserving their PNG infotext for Send to img2img/Extras.
    outputs = []
    for item in response["images"]:
        with Image.open(item["path"]) as image:
            outputs.append(image.copy())
    text = json.dumps({k: v for k, v in response.items() if k != "images"}, ensure_ascii=False, indent=2)
    metadata = json.dumps([i["metadata"] for i in response["images"]], ensure_ascii=False, indent=2)
    return outputs, text, metadata

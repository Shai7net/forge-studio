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


def generate(request: Generation, references=(), mask=None, loras=(), lora_weight=1.0, on_progress=None,
             manage_forge_job=True):
    from modules.call_queue import queue_lock
    from modules import shared
    from . import models
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

        return runner.run(request, references, mask, resolved_loras, update)

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
        width, height = [max(64, n // 16 * 16) for n in references[0].size]
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

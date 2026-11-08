"""Forge-native FIBO checkpoint and txt2img/img2img integration.

FIBO weights are registered in Forge's normal checkpoint registry, but are
executed by the isolated FIBO worker. Native SD/SDXL/FLUX checkpoints continue
through Forge's original loader and processing functions unchanged.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys
import threading

from PIL import Image, ImageOps

from .contracts import Generation
from . import models, service

_lock = threading.RLock()
_installed = False
_orig = {}
_infos = {}

DEFAULTS = {
    "dev": {"steps": 50, "cfg": 5.0, "sampler": "Euler", "width": 1024, "height": 1024},
    "edit-turbo": {"steps": 4, "cfg": 1.0, "sampler": "Euler", "width": 1024, "height": 1024},
}


def _key(path):
    return os.path.normcase(os.path.abspath(str(path)))


def register_models():
    """Add downloaded FIBO weights to the ordinary checkpoint dropdown."""
    from modules import sd_models

    with _lock:
        for variant in ("dev", "edit-turbo"):
            for precision in ("int8", "bf16"):
                path = models.MODELS_DIR / models.model_name(variant, precision)
                if not models.complete_file(path):
                    continue
                info = sd_models.CheckpointInfo(str(path))
                expected = models.MODEL_SHA256.get((variant, precision))
                if expected:
                    info.sha256 = expected
                    info.shorthash = expected[:10]
                display = models.DISPLAY_NAMES[(variant, precision)]
                info.title = f"{display} [{info.shorthash}]" if info.shorthash else display
                info.short_title = info.title
                info.fibo2_variant = variant
                info.fibo2_precision = precision
                info.fibo2_display_name = display
                for alias in (display, info.title, path.name, path.stem, str(path)):
                    if alias not in info.ids:
                        info.ids.append(alias)
                info.register()
                _infos[_key(path)] = info
    return list(_infos.values())


def is_fibo_info(info):
    return info is not None and _key(getattr(info, "filename", "")) in _infos


def info_from_value(value):
    from modules import sd_models
    if not value:
        return None
    info = sd_models.checkpoint_aliases.get(value)
    if is_fibo_info(info):
        return info
    value = str(value).split(" [", 1)[0]
    return next((item for item in _infos.values()
                 if value in (item.title.split(" [", 1)[0], item.fibo2_display_name,
                              Path(item.filename).name, Path(item.filename).stem)), None)


def selected_info(p=None):
    from modules import shared
    # A PNG pasted into img2img may carry its old checkpoint as an automatic
    # override. An explicit FIBO choice in the global checkpoint selector must
    # win over that source-image metadata. API callers can still select FIBO
    # through override_settings while a native checkpoint is globally active.
    current = info_from_value(shared.opts.sd_model_checkpoint)
    if current is not None:
        return current
    override = getattr(p, "override_settings", {}).get("sd_model_checkpoint") if p is not None else None
    return info_from_value(override)


def checkpoint_payload():
    register_models()
    return {
        "models": [{"title": x.title, "name": x.fibo2_display_name,
                    "variant": x.fibo2_variant, "precision": x.fibo2_precision,
                    "sha256": x.sha256, "defaults": DEFAULTS[x.fibo2_variant]}
                   for x in _infos.values()],
        "vae": "flux2-vae.safetensors",
    }


def list_models(*args, **kwargs):
    result = _orig["list_models"](*args, **kwargs)
    register_models()
    return result


_last_native = None


def _placeholder():
    """The ordinary checkpoint kept loaded behind FIBO (Forge's UI, token counter and scripts expect one): the one
    used last, else the smallest. The first in the list was a 6.5 GB SDXL here, loaded just to stand there."""
    from modules import sd_models
    flux3 = sys.modules.get("flux3.glue")
    usable = [x for x in sd_models.checkpoints_list.values()
              if not is_fibo_info(x) and not (flux3 is not None and flux3.is_flux3_info(x))]
    if _last_native is not None and _last_native in usable:
        return _last_native

    def size(info):
        try:
            return os.path.getsize(info.filename)
        except OSError:
            return float("inf")
    return min(usable, key=size) if usable else None


def load_model(checkpoint_info=None, already_loaded_state_dict=None):
    """Keep the native model resident as a harmless placeholder for FIBO."""
    global _last_native
    from modules import sd_models, shared
    info = checkpoint_info or sd_models.select_checkpoint()
    if not is_fibo_info(info):
        model = _orig["load_model"](checkpoint_info, already_loaded_state_dict)
        if info is not None:
            shared.opts.data["sd_checkpoint_hash"] = info.sha256
            _last_native = info
        return model

    current = sd_models.model_data.sd_model
    if current is None:
        fallback = _placeholder()
        if fallback is None:
            raise RuntimeError("FIBO needs one native Forge checkpoint as a lightweight UI placeholder")
        selected = info.title
        current = _orig["load_model"](fallback, already_loaded_state_dict)
        shared.opts.data["sd_model_checkpoint"] = selected
    return current


def _switched_on(arg, name):
    # the UI hands the add-on its own settings object, the API a plain dict
    return bool(arg.get(name) if isinstance(arg, dict) else getattr(arg, name, False))


def _ignored(p):
    """Add-ons switched on for this run that FIBO does not run (it is a separate model in its own process)."""
    found = []
    runner = getattr(p, "scripts", None)
    script_args = getattr(p, "script_args", None) or []
    for script in getattr(runner, "alwayson_scripts", None) or []:
        title = script.title()
        args = script_args[script.args_from:script.args_to]
        if title == "AnimateDiff" and any(_switched_on(a, "enable") for a in args):
            found.append(title)
        elif title == "ControlNet" and any(_switched_on(a, "enabled") for a in args):
            found.append(title)
    if getattr(p, "enable_hr", False):
        found.append("Hires. fix")
    return found


def _clean_image(image, mode="RGB"):
    if image is None:
        return None
    return ImageOps.exif_transpose(image).convert(mode)


def _fibo_request(p, info):
    is_img2img = hasattr(p, "init_images")
    extra = [_clean_image(x) for x in getattr(p, "fibo2_references", []) if x is not None]
    references = extra
    if is_img2img:
        source = next((x for x in (getattr(p, "init_images", None) or []) if x is not None), None)
        if source is None:
            raise ValueError("FIBO img2img needs a source image")
        references = [_clean_image(source)] + extra

    mode = getattr(p, "fibo2_mode", "auto")
    if is_img2img:
        mode = "edit"
    elif mode == "auto":
        mode = "edit" if references or getattr(p, "fibo2_mask", None) is not None else "generate"

    mask = getattr(p, "fibo2_mask", None)
    if mask is None and is_img2img:
        mask = getattr(p, "image_mask", None)
        if mask is not None and getattr(p, "inpainting_mask_invert", 0):
            mask = ImageOps.invert(mask.convert("L"))
    mask = _clean_image(mask, "L")

    width, height = int(p.width), int(p.height)
    if getattr(p, "fibo2_keep_source_size", True) and mode == "edit" and len(references) == 1:
        width, height = (max(64, int(x) // 16 * 16) for x in references[0].size)

    count = int(p.batch_size) * int(p.n_iter)
    request = Generation(
        variant=info.fibo2_variant, mode=mode, prompt=p.prompt or "", negative_prompt=p.negative_prompt or "",
        width=width, height=height, steps=int(p.steps), cfg=float(p.cfg_scale), seed=int(p.seed), count=count,
        precision=info.fibo2_precision, prompt_writer=getattr(p, "fibo2_prompt_writer", "off"),
    )
    return request, references, mask


def process_images(p):
    info = selected_info(p)
    if info is None:
        return _orig["process_images"](p)

    if not getattr(p, "scripts_setup_complete", False):
        p.setup_scripts()
    ignored = _ignored(p)
    if "AnimateDiff" in ignored:
        # FIBO would return one still picture where a video is expected
        raise RuntimeError(f"{info.fibo2_display_name} makes pictures, not videos: AnimateDiff is on. Choose an SD 1.5 "
                           "checkpoint for AnimateDiff, or switch AnimateDiff off.")
    request, references, mask = _fibo_request(p, info)
    response = service.generate(
        request, references, mask, getattr(p, "fibo2_loras", []) or [],
        getattr(p, "fibo2_lora_weight", 1.0), manage_forge_job=False,
    )

    outputs, infotexts, seeds = [], [], []
    for item in response["images"]:
        with Image.open(item["path"]) as opened:
            image = opened.copy()
            image.info.update(opened.info)
        image.already_saved_as = item["path"]
        outputs.append(image)
        infotexts.append(image.info.get("parameters", ""))
        seeds.append(int(item["metadata"]["seed"]))

    from modules import processing
    effective = request.validated(references, mask)
    p.width, p.height = effective.width, effective.height
    p.steps, p.cfg_scale, p.sampler_name = effective.effective_steps, effective.effective_cfg, "Euler"
    p.sd_model_name, p.sd_model_hash = info.fibo2_display_name, info.shorthash
    p.sd_vae_name, p.sd_vae_hash = "flux2-vae", "868fe7b343"
    p.extra_generation_params.update({
        "FIBO variant": info.fibo2_variant,
        "FIBO precision": info.fibo2_precision,
        "FIBO references": len(references),
        "FIBO masked edit": mask is not None,
        "FIBO scheduler": "simple",
    })
    if ignored:
        p.extra_generation_params["FIBO ignored"] = ", ".join(ignored)
    prompts = [p.prompt] * len(outputs)
    negatives = [p.negative_prompt] * len(outputs)
    return processing.Processed(
        p, outputs, seed=seeds[0] if seeds else -1, info=infotexts[0] if infotexts else "",
        all_prompts=prompts, all_negative_prompts=negatives, all_seeds=seeds,
        all_subseeds=[-1] * len(outputs), infotexts=infotexts,
    )


def route_callers():
    """img2img and the API took their own reference to process_images when they were imported
    (`from modules.processing import process_images`), so replacing processing.process_images alone left them on
    Forge's original: with FIBO chosen, img2img and /sdapi quietly drew with the ordinary checkpoint kept behind it
    (the "FIBO" img2img tests of 2026-11-05 were SDXL pictures). Point those names here too; called again once the
    API exists."""
    for name in ("modules.img2img", "modules.api.api"):
        module = sys.modules.get(name)
        if module is not None and getattr(module, "process_images", None) is _orig.get("process_images"):
            module.process_images = process_images


def install():
    global _installed
    with _lock:
        if _installed:
            register_models()
            route_callers()
            return
        from modules import processing, sd_models
        _orig.update(list_models=sd_models.list_models, load_model=sd_models.load_model,
                     process_images=processing.process_images)
        sd_models.list_models = list_models
        sd_models.load_model = load_model
        processing.process_images = process_images
        route_callers()
        register_models()
        _installed = True
        print("[FIBO 2.0] ready in Stable Diffusion checkpoint, txt2img and img2img")

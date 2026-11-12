"""Generating inside the image workspace, with the checkpoint chosen at the top of Forge.

Five ways, as Forge's own tabs have them: words to a picture (txt2img), the picture again (img2img), a sketch (the
picture with colours painted on it, img2img), inpaint (only the selection drawn again) and inpaint sketch (the
selection drawn again keeping what was painted in it as a guide). The page sends the shot flattened and the
selection as a mask; every result is kept in the shot's folder and the page lays it on a new layer (for inpaint only
inside the selection).

SD 1.5 and SDXL go through Forge's own API functions, called in this process: ControlNet (Forge's built-in units,
every model and preprocessor), samplers, schedulers and inpaint options work exactly as in the tabs, and the call
waits its turn in Forge's queue. FLUX 3 and FIBO 2.0 take their native controls (ordered references, an exact mask,
the prompt writer, keeping the source size), which they read from the processing object as their own panel sets them,
so they are drawn through Forge's processing directly.
"""

import base64
import io
import os
import sys
import time
import uuid

from . import frames, imagedoc

APP = None   # Forge's FastAPI app (set by api.register): the /sdapi routes are found among its routes

MODES = ("txt2img", "img2img", "sketch", "inpaint", "inpaint-sketch")
FILLS = {"fill": 0, "original": 1, "latent noise": 2, "latent nothing": 3}


def _route(path):
    for route in getattr(APP, "routes", []) or []:
        if getattr(route, "path", None) == path:
            return route.endpoint
    return None


def family():
    """What draws now: 'flux3', 'fibo', 'sdxl', 'sd15' or 'other', and the checkpoint's name."""
    from modules import shared
    flux = sys.modules.get("flux3.glue")
    fibo = sys.modules.get("fibo2.glue")
    name = getattr(shared.opts, "sd_model_checkpoint", "") or ""
    try:
        if fibo is not None and fibo.selected_info() is not None:
            return "fibo", name
    except Exception:
        pass
    try:
        if flux is not None and flux.active():
            return "flux3", name
    except Exception:
        pass
    model = shared.sd_model
    if getattr(model, "is_sdxl", False):
        return "sdxl", name
    if getattr(model, "is_sd1", False) or getattr(model, "is_sd2", False):
        return "sd15", name
    return "other", name


def options():
    """What the page offers for the model now: samplers, schedulers, ControlNet models and preprocessors (SD models)."""
    from modules import sd_samplers
    fam, name = family()
    out = {"family": fam, "checkpoint": name, "samplers": [], "schedulers": [], "controlnet": {"models": [], "modules": []},
           "api": _route("/sdapi/v1/txt2img") is not None}
    try:
        out["samplers"] = sd_samplers.visible_sampler_names() if hasattr(sd_samplers, "visible_sampler_names") else [s.name for s in sd_samplers.samplers]
    except Exception:
        pass
    try:
        from modules import sd_schedulers
        out["schedulers"] = [s.label for s in sd_schedulers.schedulers]
    except Exception:
        pass
    state = sys.modules.get("lib_controlnet.global_state")
    if state is not None and fam in ("sd15", "sdxl", "other"):
        try:
            models = [m for m in state.get_all_controlnet_names() if m and m != "None"]
            xl = lambda m: "xl" in m.lower()
            # the ones made for this model first
            out["controlnet"]["models"] = sorted(models, key=lambda m: (xl(m) != (fam == "sdxl"), m.lower()))
            out["controlnet"]["modules"] = [m for m in state.get_all_preprocessor_names() if m]
        except Exception:
            pass
    big = fam in ("sdxl", "flux3", "fibo")
    out["defaults"] = {"steps": 16 if fam == "flux3" else 28 if fam == "fibo" else 25 if big else 25,
                       "cfg": 1.0 if fam == "flux3" else 4.0 if fam == "fibo" else 5.0 if big else 7.0,
                       "sampler": "Euler" if fam in ("flux3", "fibo") else "DPM++ 2M", "area": 1024 * 1024 if big else 640 * 640}
    return out


def _size(w, h, area, step):
    k = min(1.0, (area / float(w * h)) ** 0.5) if w * h > area else (area / float(w * h)) ** 0.5
    k = min(k, 2048 / max(w, h))
    return max(step * 4, int(w * k) // step * step), max(step * 4, int(h * k) // step * step)


def _b64(png_bytes):
    return base64.b64encode(png_bytes).decode("ascii")


def _pil(data, mode="RGBA"):
    from PIL import Image
    return Image.open(io.BytesIO(data)).convert(mode)


def generate(name, shot, mode, params, flat=None, mask=None, units=None, refs=None):
    """Start a generation for shot `shot`: `params` = prompt, negative, steps, cfg, sampler, scheduler, seed, count,
    denoise, mask_blur, fill, only_masked, padding, writer (FLUX/FIBO), keep_size; `flat` / `mask` PNG bytes;
    `units` = ControlNet units [{module, model, weight, guidance_start, guidance_end, control_mode, pixel_perfect,
    image (PNG bytes)}]; `refs` = extra reference pictures (PNG bytes) for FLUX 3 / FIBO."""
    if mode not in MODES:
        raise ValueError("unknown way of generating")
    if mode != "txt2img" and not flat:
        raise ValueError("the picture is missing")
    if mode in ("inpaint", "inpaint-sketch") and not mask:
        raise ValueError("select the area to draw again first")
    if not (params.get("prompt") or "").strip() and mode == "txt2img":
        raise ValueError("write what the picture shows")
    doc = imagedoc.load(name, shot)
    return frames._task("image", _generate, name, shot, doc["width"], doc["height"], mode, dict(params), flat, mask,
                        list(units or []), list(refs or []))


def _generate(task, name, shot, W, H, mode, p, flat, mask, units, refs):
    from PIL import Image
    fam, checkpoint = family()
    task["text"] = "waiting for Forge to be free"
    t0 = time.time()
    if fam in ("flux3", "fibo"):
        images, info = _native(task, fam, W, H, mode, p, flat, mask, refs)
    else:
        images, info = _api(task, fam, W, H, mode, p, flat, mask, units)
    folder = os.path.join(imagedoc._dir(name, shot), "gen")
    os.makedirs(folder, exist_ok=True)
    gid = uuid.uuid4().hex[:8]
    files = []
    for i, im in enumerate(images):
        im = im.convert("RGBA")
        if im.size != (W, H):
            im = im.resize((W, H), Image.LANCZOS)
        f = f"{gid}-{i}.png"
        im.save(os.path.join(folder, f), "PNG")
        files.append("gen/" + f)
    return {"files": files, "seeds": info.get("seeds", []), "family": fam, "checkpoint": checkpoint, "mode": mode,
            "progress": 1.0, "text": f"{len(files)} in {time.time() - t0:.0f} s · {checkpoint}"}


def _api(task, fam, W, H, mode, p, flat, mask, units):
    """SD 1.5 / SDXL (and other Forge models) through Forge's own API functions, in this process."""
    route = "/sdapi/v1/txt2img" if mode == "txt2img" else "/sdapi/v1/img2img"
    endpoint = _route(route)
    if endpoint is None:
        raise RuntimeError("Forge's API is off: start Forge with --api")
    model = endpoint.__annotations__.get("txt2imgreq" if mode == "txt2img" else "img2imgreq")
    w, h = _size(W, H, 1024 * 1024 if fam == "sdxl" else 640 * 640, 8)
    req = dict(prompt=p.get("prompt") or "", negative_prompt=p.get("negative") or "", steps=int(p.get("steps") or 25),
               cfg_scale=float(p.get("cfg") or 7), sampler_name=p.get("sampler") or "DPM++ 2M", seed=int(p.get("seed", -1)),
               batch_size=1, n_iter=1, width=w, height=h, send_images=True,
               save_images=False, do_not_save_samples=True, do_not_save_grid=True)
    if p.get("scheduler"):
        req["scheduler"] = p["scheduler"]
    if mode != "txt2img":
        req.update(init_images=[_b64(flat)], denoising_strength=float(p.get("denoise", 0.6)), resize_mode=0,
                   include_init_images=False)
        if mode in ("inpaint", "inpaint-sketch"):
            req.update(mask=_b64(mask), mask_blur=int(p.get("mask_blur", 4)),
                       inpainting_fill=1 if mode == "inpaint-sketch" else FILLS.get(p.get("fill") or "original", 1),
                       inpaint_full_res=bool(p.get("only_masked", True)), inpaint_full_res_padding=int(p.get("padding", 32)),
                       inpainting_mask_invert=0)
    cn = []
    for u in units:
        unit = {"enabled": True, "module": u.get("module") or "None", "model": u.get("model") or "None",
                "weight": float(u.get("weight", 1.0)), "guidance_start": float(u.get("guidance_start", 0)),
                "guidance_end": float(u.get("guidance_end", 1)), "control_mode": u.get("control_mode") or "Balanced",
                "pixel_perfect": bool(u.get("pixel_perfect", True)), "resize_mode": "Crop and Resize"}
        if u.get("image"):
            unit["image"] = _b64(u["image"])
        cn.append(unit)
    if cn:
        req["alwayson_scripts"] = {"ControlNet": {"args": cn}}
    import json
    images, seeds = [], []
    count = max(1, min(4, int(p.get("count") or 1)))
    for i in range(count):
        # one call a variation (several in one call can bring a grid first); the next seed each time
        if i and req["seed"] >= 0:
            req["seed"] += 1
        elif i and seeds:
            req["seed"] = int(seeds[-1]) + 1
        task["text"] = f"drawing {i + 1} of {count} ({w}x{h}, {req['steps']} steps)"
        task["progress"] = i / count
        result = endpoint(model(**req))
        # ControlNet sends its detected maps after the picture: keep the picture
        images.extend(_pil(base64.b64decode(b), "RGB") for b in (result.images or [])[:1])
        try:
            seeds.append(json.loads(result.info or "{}").get("seed"))
        except Exception:
            seeds.append(None)
    return images, {"seeds": seeds}


def _native(task, fam, W, H, mode, p, flat, mask, refs):
    """FLUX 3 / FIBO 2.0 through Forge's processing with their native controls set as their panel sets them."""
    from PIL import Image
    from modules import processing, shared
    from modules.call_queue import queue_lock
    step = 16
    w, h = _size(W, H, 1024 * 1024, step)
    references = [_pil(r, "RGB") for r in refs][:3 if mode != "txt2img" else 4]
    m = None
    if mode in ("inpaint", "inpaint-sketch") and mask:
        m = _pil(mask, "L").resize((W, H), Image.NEAREST)
    images = []
    seeds = []
    with queue_lock:
        for i in range(max(1, min(4, int(p.get("count") or 1)))):
            kw = dict(sd_model=shared.sd_model, prompt=p.get("prompt") or "", negative_prompt=p.get("negative") or "",
                      seed=int(p.get("seed", -1)) if int(p.get("seed", -1)) < 0 else int(p["seed"]) + i,
                      sampler_name=p.get("sampler") or "Euler", steps=int(p.get("steps") or (16 if fam == "flux3" else 28)),
                      cfg_scale=float(p.get("cfg") or (1.0 if fam == "flux3" else 4.0)), width=w, height=h,
                      do_not_save_samples=True, do_not_save_grid=True)
            # FLUX 3 without references takes Forge's classic fields (as Forge's own img2img / Inpaint tabs and the
            # frame editor give it): a description, the strength, Forge's mask. Its native "edit with reference"
            # redraws the whole picture from an instruction; with a mask and a description it gave a flat yellow block
            # or the area unchanged (2026-11-12). FIBO and FLUX with references take the native fields.
            classic = fam == "flux3" and not references
            inpaint = m is not None
            if mode == "txt2img":
                proc = processing.StableDiffusionProcessingTxt2Img(**kw)
            else:
                extra = {}
                if classic and inpaint:
                    extra = dict(mask=m, mask_blur=int(p.get("mask_blur", 4)), inpainting_fill=1 if mode == "inpaint-sketch" else
                                 FILLS.get(p.get("fill") or "original", 1), inpaint_full_res=bool(p.get("only_masked", True)),
                                 inpaint_full_res_padding=int(p.get("padding", 32)))
                proc = processing.StableDiffusionProcessingImg2Img(init_images=[_pil(flat, "RGB")],
                                                                   denoising_strength=float(p.get("denoise", 0.75)), **extra, **kw)
            # an empty script runner: FIBO / FLUX read their fields from `proc` themselves, and Forge (or their glue)
            # calls setup_scripts, which needs a runner (None failed: 'NoneType' has no attribute 'setup_scrips')
            from modules import scripts as forge_scripts
            proc.scripts = forge_scripts.ScriptRunner()
            proc.script_args = []
            if not classic:
                proc.fibo2_mode = "generate" if mode == "txt2img" and not references else ("auto" if mode == "txt2img" else "edit")
                proc.fibo2_prompt_writer = p.get("writer") or "off"
                proc.fibo2_keep_source_size = bool(p.get("keep_size", True))
                proc.fibo2_references = references
                proc.fibo2_mask = m
                proc.fibo2_loras = []
                proc.fibo2_lora_weight = 1.0
            task["text"] = f"drawing with {'FLUX 3' if fam == 'flux3' else 'FIBO 2.0'} ({i + 1})"
            try:
                res = processing.process_images(proc)
            finally:
                proc.close()
            images.extend(res.images[:1])
            seeds.append(res.seed)
    return images, {"seeds": seeds}

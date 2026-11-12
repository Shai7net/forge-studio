"""The Video Studio tab: Text to Video, Image to Video (one picture, a first and a last frame, or several pictures in a
row for a longer video) and Replace a character, one Generate for all of them."""

import html
import json
import os
import random
import re
import shutil
import subprocess
import time
import threading
import functools

import gradio as gr
from PIL import Image

from . import CACHE_DIR, JOBS_DIR, OUTPUT_DIR, WEBUI_DIR, faceswap, ltx, models, prompting, runner, sheets, sound

_FRAME_ACTIVE = False
_FRAME_STOP = threading.Event()


class FrameCancelled(RuntimeError):
    pass


def frame_task(function):
    @functools.wraps(function)
    def draw(*args, **kwargs):
        from modules import shared
        global _FRAME_ACTIVE
        _FRAME_STOP.clear()
        shared.state.begin(job='Video Studio first frame')
        _FRAME_ACTIVE = True
        try:
            result = function(*args, **kwargs)
            if _FRAME_STOP.is_set() or shared.state.interrupted:
                raise FrameCancelled()
            return result
        except Exception:
            if _FRAME_STOP.is_set():
                raise FrameCancelled() from None
            raise
        finally:
            _FRAME_ACTIVE = False
            shared.state.end()
    return draw

MODES = {"t2v": "Text to Video", "i2v": "Image to Video", "ingredients": "Ingredients to Video",
         "replace": "Replace a character", "ltx_audio": "Image + Audio to Video"}
MAX_INGREDIENTS = 4  # Phantom was trained on up to four
# Measured on his character sheet (2026-11-09): Phantom kept the clothes but drew a different, real person; FLUX 3
# Klein drawing the first frame from the sheet kept face, clothes and the 3D style, and Wan's Image to Video keeps
# what the first frame shows.
ING_FRAME = "First frame by FLUX 3 Klein from the pictures, then Wan 2.2 (keeps faces and style)"
ING_LTX_FRAME = 'First frame by FLUX 3 Klein from the pictures, then LTX 2.3 (keeps faces and style)'
ING_PHANTOM = "Phantom: the pictures go into the video (keeps clothes and things; faces and drawn styles drift)"
ING_AUTO = 'Automatic: use the best available reference workflow'
# FLUX 3 Klein (flow map) at 8 steps drew the two-character first frame as well as at 16, in 42 s instead of 83
# (1376x768 with two references, 2026-11-11)
FIRST_FRAME_STEPS = 8
ING_METHODS = (ING_AUTO, ING_FRAME, ING_PHANTOM)


def ingredient_methods(is_ltx):
    return [ING_AUTO, ING_LTX_FRAME, ING_PHANTOM] if is_ltx else list(ING_METHODS)


def ingredient_route(method, is_ltx, flux_available):
    if method == ING_PHANTOM:
        return 'phantom'
    if method in (ING_FRAME, ING_LTX_FRAME):
        return 'flux'
    if flux_available:
        return 'flux'
    return 'phantom'
# Image to Video takes up to this many pictures: one is a start, two a first and a last frame, more make one part per
# pair, all made in one run and joined.
MAX_PICTURES = 6
REPLACE_QUALITY = {"480p - fast (about 6 min per 5 s)": 480 * 832, "576p - sharper (about 10 min per 5 s)": 576 * 1024}
WHO = ["Only the chosen person (with the chosen person of the picture)",
       "Everyone in the picture, in the same order from the left",
       "Everyone in the picture, in reverse order"]
MASK_SHAPES = {"Box: the character may differ in shape (official)": "box",
               "Outline: keeps more of the background": "outline"}
QUALITY = {
    "480p - fast (about 5 min for 5 s)": 480 * 832,
    "576p - balanced (about 8 min for 5 s)": 576 * 1024,
    "720p - best (about 16 min for 5 s)": 720 * 1280,
}
DEFAULT_QUALITY = next(iter(QUALITY))
ASPECTS = {"9:16 portrait": (9, 16), "16:9 landscape": (16, 9), "1:1 square": (1, 1), "4:5 portrait": (4, 5), "3:4 portrait": (3, 4)}
UPSCALE_OFF = "Off"
NO_LORA = "None"
STAGES = {
    "idle": "Ready", "start": "Starting", "text": "Reading the prompt", "condition": "Preparing the frames", "loading": "Loading the model",
    "sampling": "Making the video", "decode": "Turning it into frames", "interpolate": "Smoothing motion (FILM)",
    "upscale": "Enlarging", "writing": "Saving the video", "done": "Done", "error": "Error", "cancelled": "Stopped",
    "picture": "Drawing the first frame",
    "voice": "Making the voice", "effects": "Making the sound effects", "lips": "Moving the lips",
    "mixing": "Mixing the sound", "prepare": "Reading the video (pose, face, the person's outline)",
}
# Share of the whole job each stage takes (for one progress bar over all of them), measured at 480p.
WEIGHTS = [("start", 0.0), ("prepare", 0.0), ("text", 0.02), ("condition", 0.05), ("loading", 0.05), ("sampling", 0.75),
           ("decode", 0.07), ("interpolate", 0.03), ("upscale", 0.02), ("writing", 0.01)]


def fit_size(width, height, area, multiple=16):
    ratio = width / height
    h = (area / ratio) ** 0.5
    w = h * ratio
    return max(multiple, int(round(w / multiple)) * multiple), max(multiple, int(round(h / multiple)) * multiple)


def frames_for(seconds):
    return 1 + 4 * max(1, round(seconds * 16 / 4))


def overall(stage, progress):
    total, done = sum(w for _, w in WEIGHTS), 0.0
    for name, w in WEIGHTS:
        if name == stage:
            return min(1.0, (done + w * (progress or 0)) / total)
        done += w
    return 1.0 if stage == "done" else 0.0


def status_html(stage, progress=None, note="", elapsed=None, eta=None, error=None, absolute=False):
    if stage in ("error", "cancelled"):
        pct = 0
    elif absolute:
        pct = int(round(100 * min(1.0, max(0.0, progress or 0.0))))
    else:
        pct = int(round(100 * overall(stage, progress)))
    label = STAGES.get(stage, stage)
    extra = []
    if note:
        extra.append(html.escape(note))
    if elapsed is not None:
        extra.append(f"{int(elapsed // 60)}:{int(elapsed % 60):02d}")
    if eta:
        extra.append(f"~{int(eta // 60)}:{int(eta % 60):02d} left")
    color = "#d33" if stage == "error" else "#e0a000" if stage == "cancelled" else "#4caf50" if stage == "done" else "#3b82f6"
    body = f"<b>{html.escape(label)}</b>"
    if extra:
        body += " &middot; " + " &middot; ".join(extra)
    if error:
        body += f"<pre class='vs-error'>{html.escape(error)}</pre>"
    bar = f"<div class='vs-bar'><div style='width:{pct}%;background:{color}'></div></div>" if stage not in ("error",) else ""
    return f"<div class='vs-status'>{bar}<div class='vs-status-text'>{body}</div></div>"


# The labels and notes below are the first look of Image to Video; afterwards videoStudioPictureLayout in
# javascript/video-studio.js shows them for the number of pictures (same texts, keep the two in step).
def picture_label(k, n):
    if n == 1:
        return "Start picture"
    if n == 2 and k < 2:
        return ("First frame", "Last frame")[k]
    return f"Picture {k + 1}"


def pictures_note(n):
    if n == 1:
        return "One picture: the video starts from it. ➕ Last frame makes it end on a picture of yours too."
    if n == 2:
        return ("The video goes from the first picture to the last. More pictures make a longer video: one part for "
                "each pair, joined into one.")
    return (f"{n} pictures: {n - 1} parts, one for each pair, joined into one video. The length below is per part; "
            "each part can have its own description.")


def _take_over_send_to_video(first_picture):
    """The 🎬 button under the txt2img and img2img results sent the picture to the old SVD tab. It now puts it into
    Image to Video here (switch_to_video_studio in javascript/video-studio.js opens the tab)."""
    try:
        from modules import infotext_utils as copypaste
    except ImportError:
        from modules import generation_parameters_copypaste as copypaste
    copypaste.add_paste_fields("video_studio", init_img=first_picture, fields=[])
    for binding in copypaste.registered_param_bindings:
        if binding.tabname == "svd":
            binding.tabname = "video_studio"


def models_markdown():
    lines = []
    for name in models.PRESETS:
        miss = models.missing(name)
        if not miss:
            lines.append(f"- **{name}**: ready")
        else:
            lines.append(f"- **{name}**: missing {len(miss)} file(s), {models.missing_bytes(name) / 1e9:.1f} GB to download")
    film = "ready" if models.film_model() else "missing (Deforum's film_net_fp16.pt)"
    lines.append(f"- FILM in-between frames: {film}")
    have = sound.status()
    for key, label in (("voices", "Voices (Chatterbox, 23 languages, copies a voice)"), ("piper", "Fast Hebrew voice (Piper)"),
                       ("effects", "Sound effects (MMAudio)"), ("lips", "Lip sync (MuseTalk)")):
        lines.append(f"- {label}: {'ready' if have[key] else 'not installed'}")
    py = runner.engine_python()
    lines.append('- LTX engine: ' + ('ready' if ltx.runtime_ready() else 'not set up (Set up LTX engine below)'))
    lines.append(f"- Engine: `{'Video Studio engine (PyTorch 2.9, SageAttention)' if 'video-studio' in py else 'Forge Python (slower: no SageAttention)'}`")
    st = models.DOWNLOADS.status()
    if st["active"]:
        lines.append(f"- Downloading {st['text']}: {st['done'] / 1e9:.1f} / {st['total'] / 1e9:.1f} GB")
    if st["error"]:
        lines.append(f"- Download problem: {st['error']}")
    return "\n".join(lines)


def replace_models_markdown():
    gone = models.replace_missing()
    if not gone:
        return "Models: all here (Wan 2.2 Animate 14B Q3, lightx2v, relight, SAM 2.1, DWPose, CLIP-H)."
    size = sum(models.FILES[k]["size"] for k in gone) / 1e9
    return f"Models: {len(gone)} files missing ({size:.1f} GB): " + ", ".join(
        os.path.basename(models.file_path(k)) for k in gone)


def _copy_input(path, job_dir, name):
    if not path:
        return None
    os.makedirs(job_dir, exist_ok=True)
    dest = os.path.join(job_dir, name + os.path.splitext(path)[1].lower())
    shutil.copyfile(path, dest)
    return dest


@frame_task
def draw_first_frame(prompt, negative, aspect, steps, cfg, sampler, seed, folder):
    """Text to Video's first frame, drawn by Forge with the checkpoint selected at the top of the page."""
    from modules import processing, shared
    aw, ah = ASPECTS.get(aspect, (9, 16))
    is_xl = bool(getattr(shared.sd_model, "is_sdxl", False))
    w, h = fit_size(aw, ah, 1024 * 1024 if is_xl else 640 * 640, 64)
    p = processing.StableDiffusionProcessingTxt2Img(
        sd_model=shared.sd_model, prompt=prompt, negative_prompt=negative or "", width=w, height=h,
        steps=int(steps), cfg_scale=float(cfg), sampler_name=sampler, seed=int(seed),
        do_not_save_samples=True, do_not_save_grid=True)
    try:
        res = processing.process_images(p)
    finally:
        p.close()
    image = res.images[0]
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "first_frame_drawn.png")
    image.save(path)
    return path


def installed_flux3_checkpoint(glue):
    """A complete installed Klein checkpoint, without changing Forge's selected model."""
    candidates = sorted(glue.sd_models.checkpoints_list.values(),
                        key=lambda info: ('klein' not in info.filename.lower(), info.filename.lower()))
    for info in candidates:
        if glue.is_flux3_info(info):
            try:
                glue.components(info)
                return info
            except (OSError, ValueError):
                continue
    return None


def flux3_glue():
    """Active or complete installed FLUX 3 support; no checkpoint-selection prerequisite."""
    try:
        from flux3 import glue
        from modules import shared
        return glue if glue.active(shared.sd_model) or installed_flux3_checkpoint(glue) else None
    except Exception:
        return None


@frame_task
def draw_from_ingredients(prompt, pictures, aspect, seed, folder):
    """Ingredients to Video's first frame: FLUX 3 Klein draws the scene with the ingredient pictures as its
    references (its own editing, as Shai made his character sheet from a pose sheet). Klein keeps a character's
    face, clothes and style from a whole reference sheet, which Phantom does not (it turned a stylized 3D character
    into a different real person, 2026-11-09); Wan then animates the frame."""
    from modules import processing, shared
    glue = flux3_glue()
    if glue is None:
        raise RuntimeError('Install a complete FLUX 3 Klein model for first-frame references, or choose Automatic.')
    aw, ah = ASPECTS.get(aspect, (9, 16))
    w, h = fit_size(aw, ah, 1024 * 1024, 16)
    refs = []
    for path in pictures:
        with Image.open(path) as im:
            refs.append(im.convert("RGB"))
    which = "the reference picture" if len(refs) == 1 else "the reference pictures"
    text = (f"A single frame from a film, not a reference sheet: {prompt.strip()} Everyone and everything in it looks "
            f"exactly as in {which}: the same face, hair, body, clothes and style.")
    if not glue.active(shared.sd_model):
        image = draw_with_installed_flux(glue, text, refs, w, h, seed)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, 'first_frame_from_ingredients.png')
        image.save(path)
        return path
    p = processing.StableDiffusionProcessingImg2Img(
        sd_model=shared.sd_model, init_images=[refs[0]], denoising_strength=1.0, prompt=text, negative_prompt="",
        width=w, height=h, steps=FIRST_FRAME_STEPS, cfg_scale=1.0, sampler_name="Euler", seed=int(seed),
        do_not_save_samples=True, do_not_save_grid=True)
    p.flux3_edit_mode = glue.EDIT_MODES[0]
    p.flux3_references = refs
    try:
        res = processing.process_images(p)
    finally:
        p.close()
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "first_frame_from_ingredients.png")
    res.images[0].save(path)
    return path


def draw_with_installed_flux(glue, prompt, references, width, height, seed):
    """Use the extension's own engine transiently; retain the user's image checkpoint and options."""
    import torch
    from modules import devices, shared
    info = installed_flux3_checkpoint(glue)
    if info is None:
        raise RuntimeError('The installed FLUX 3 Klein model is incomplete.')
    runner.free_forge_memory()
    engine = glue.feng.Flux3Engine(glue.components(info))
    try:
        engine.load(devices.device)
        with engine.lock, torch.inference_mode(), torch.autocast('cuda', enabled=False):
            clean = prompting.LORA_TAG.sub('', prompt)
            embedding = engine.encode(glue.clean_prompt(clean))
            reference_latents = glue.encode_references(engine, references)
            noise = torch.randn((1, 128, height // 16, width // 16),
                                generator=torch.Generator('cpu').manual_seed(int(seed)))
            def preview(step, total, latent):
                shared.state.sampling_steps, shared.state.sampling_step = total, step + 1
                if (step + 1) % 2 == 0:
                    shared.state.assign_current_image(glue.feng.latent_preview(latent[0]))
            latent = engine.sample(noise, embedding, FIRST_FRAME_STEPS, refs=reference_latents, callback=preview,
                                   should_stop=lambda: _FRAME_STOP.is_set() or shared.state.interrupted)
            if _FRAME_STOP.is_set() or shared.state.interrupted:
                raise FrameCancelled()
            return glue._to_pil(engine.decode(latent)[0])
    finally:
        engine.unload()


def build_job(kind, start, end, prompt, negative, quality, seconds, seed, preset, steps, shift, cfg, sampler, int8,
              attention, width, height, smooth, upscale, loras, aspect, parts=None):
    """`kind`: t2v, i2v (one picture), flf (first and last frame) or multi (`parts`, one per pair of pictures)."""
    pr = models.PRESETS[preset]
    if start:
        with Image.open(start) as im:
            iw, ih = im.size
    else:
        iw, ih = ASPECTS.get(aspect, (9, 16))
    is_ltx = ltx.is_ltx(preset)
    qualities = ltx.QUALITY if is_ltx else QUALITY
    area = qualities.get(quality, qualities[ltx.default_quality(preset) if is_ltx else DEFAULT_QUALITY])
    w, h = fit_size(iw, ih, area, 32 if is_ltx else 16)
    if width and height:
        w, h = int(width) // 16 * 16, int(height) // 16 * 16
    day = time.strftime("%Y-%m-%d")
    out_dir = os.path.join(OUTPUT_DIR, day)
    os.makedirs(out_dir, exist_ok=True)
    base = f"{time.strftime('%H%M%S')}-{kind}-{seed}"
    ups = models.upscaler_models()
    job = {
        "mode": "t2v" if kind == "t2v" and not start else {"flf": "flf2v", "multi": "multi"}.get(kind, "i2v"),
        "prompt": prompt.strip(), "negative": (negative or "").strip(),
        "start_image": start, "end_image": end if kind in ("flf", "multi") else None,
        "width": w, "height": h, "frames": frames_for(seconds), "fps": 16,
        "steps": int(steps), "shift": float(shift), "cfg": float(cfg), "sampler": sampler, "seed": int(seed),
        "int8": bool(int8), "attention": attention,
        "text_encoder": models.file_path(pr["text_encoder"]), "vae": models.file_path(pr["vae"]),
        "experts": [{"path": models.file_path(k), "until_sigma": s, "label": label} for k, s, label in pr.get("experts", [])],
        "cache_dir": CACHE_DIR, "output_dir": out_dir, "basename": base, "model_label": preset,
        "interpolate": bool(smooth), "film_model": models.film_model(),
        "upscale": {"model": ups.get(upscale.split(" ", 1)[-1]), "scale": float(upscale.split(" ", 1)[0][1:])}
        if upscale != UPSCALE_OFF else None,
        "loras": loras,
    }
    if parts:
        job["parts"] = parts
    if is_ltx:
        job = ltx.adapt_job(job, preset, quality, seconds)
    return job


def _guarded(fn):
    """Gradio 3 shows a bare "Error" for an exception in a generator and logs nothing; print it and show it."""
    import functools
    import traceback

    @functools.wraps(fn)
    def wrapper(*args):
        try:
            yield from fn(*args)
        except Exception as e:  # noqa: BLE001 - every failure goes to the status box
            traceback.print_exc()
            yield (status_html("error", error=f"{type(e).__name__}: {e}\n\n{traceback.format_exc()[-1200:]}"),
                   gr.update(), gr.update(), gr.update(), gr.update())
    return wrapper


def _guarded_sound(fn):
    import functools
    import traceback

    @functools.wraps(fn)
    def wrapper(*args):
        try:
            yield from fn(*args)
        except Exception as e:  # noqa: BLE001 - every failure goes to the status box
            traceback.print_exc()
            yield (status_html("error", error=f"{type(e).__name__}: {e}"), gr.update(), gr.update(), gr.update())
    return wrapper


def generate(mode, count, *args):
    """Generator: yields (status, preview, video, details, last frame path).
    args: the pictures of Image to Video, the description of each part, then the rest of the tab."""
    pictures = list(args[:MAX_PICTURES])
    part_texts = list(args[MAX_PICTURES:2 * MAX_PICTURES - 1])
    rest = args[2 * MAX_PICTURES - 1:]
    (t2v_aspect, t2v_picture_prompt, t2v_steps, t2v_cfg, t2v_sampler, prompt, negative, quality, seconds, seed, preset,
     steps, shift, cfg, sampler, int8, attention, width, height, smooth, upscale, lora1, lora1_w, lora2, lora2_w,
     styles, auto_sound, sound_words) = rest[:28]
    replace = rest[28:42]
    ingredients = rest[42:42 + MAX_INGREDIENTS]
    method = rest[42 + MAX_INGREDIENTS] if len(rest) > 42 + MAX_INGREDIENTS else ING_AUTO
    ltx_inputs = rest[43 + MAX_INGREDIENTS:]
    audio_picture, audio_file, native_audio = (list(ltx_inputs) + [None, None, True])[:3] if ltx_inputs else (None, None, True)
    mode = mode if mode in MODES else "i2v"
    nothing = gr.update()

    def out(status, preview=nothing, video=nothing, details=nothing, last=nothing):
        return status, preview, video, details, last

    is_ltx = ltx.is_ltx(preset)
    audio_mode = mode == 'ltx_audio'
    if audio_mode:
        if not is_ltx or not audio_picture or not audio_file:
            yield out(status_html('error', error='Choose an LTX model, add a picture and an audio file in Image + Audio to Video.'))
            return
        mode, count, pictures = 'i2v', 1, [audio_picture] + pictures[1:]
    route = ingredient_route(method, is_ltx, flux3_glue() is not None) if mode == 'ingredients' else None
    if is_ltx and not ltx.runtime_ready() and mode != 'replace' and route != 'phantom':
        yield out(status_html('error', error='Set up the LTX engine in Models before generating with LTX.'))
        return

    # Ingredients by a first frame: FLUX 3 Klein draws it from the pictures, then it goes on as Image to Video
    from_ingredients = route == 'flux'
    ingredient_pictures = [p for p in ingredients if p]
    if from_ingredients:
        if not ingredient_pictures:
            yield out(status_html("error", error="Add at least one ingredient: a picture of who or what is in the video."))
            return
        if flux3_glue() is None:
            yield out(status_html('error', error='A complete FLUX 3 Klein model is not installed. Choose Automatic or Phantom.'))
            return
    elif route == 'phantom':
        yield from generate_ingredients(prompt, negative, quality, seconds, seed, t2v_aspect, int8, attention, smooth,
                                        upscale, lora1, lora1_w, lora2, lora2_w, styles, auto_sound, sound_words,
                                        *ingredients)
        return
    if mode == "replace":
        yield from generate_replace(prompt, negative, seed, smooth, upscale, int8, attention, lora1, lora1_w, lora2,
                                    lora2_w, styles, auto_sound, sound_words, *replace)
        return

    n = max(1, min(MAX_PICTURES, int(count or 1))) if mode == "i2v" else 0
    used = pictures[:n]
    kind = "t2v" if mode == "t2v" else ("i2v" if n == 1 or from_ingredients else "flf" if n == 2 else "multi")
    texts = [(t or "").strip() for t in part_texts[:max(0, n - 1)]] if kind == "multi" else []
    if not (prompt or "").strip() and not (texts and all(texts)):
        yield out(status_html("error", error="Write a prompt: what happens in the video."))
        return
    if mode == "i2v":
        empty = [k + 1 for k, picture in enumerate(used) if not picture]
        if empty:
            yield out(status_html("error", error="Image to Video needs a start picture." if n == 1 else
                                  f"Picture {empty[0]} is empty: add it, or take it away with ✕."))
            return
    if models.missing(preset):
        yield out(status_html("error", error=f"The model files of '{preset}' are not all downloaded yet (see Models)."))
        return
    if runner.busy():
        yield out(status_html("error", error="A video is already being made (it keeps going even if this page was "
                                             "reloaded). Wait for it, press Stop, or press Show the last video."))
        return
    seed = int(seed) if seed is not None and int(seed) >= 0 else random.randint(0, 2 ** 31 - 1)

    # Styles exactly as txt2img applies them; <lora:...> tags (typed or from a style) become Wan LoRAs and are
    # taken out of what Wan reads; the full styled prompt still goes to Forge for Text to Video's first picture.
    styled, wan_prompt, negative, loras, not_wan = video_loras((prompt or "").strip() or texts[0], negative, styles,
                                                              lora1, lora1_w, lora2, lora2_w, preset)
    if not wan_prompt.strip():
        yield out(status_html("error", error="After taking out the <lora:...> tags the prompt is empty: describe the video too."))
        return
    # each part's own description gets the same styles; an empty one is the prompt
    part_prompts = []
    for text in texts:
        part = prompting.split_loras(prompting.apply_styles(text, "", styles or [])[0])[0].strip() if text else ""
        part_prompts.append(part or wan_prompt)

    from modules.call_queue import queue_lock
    yield out(status_html("start", 0, note="waiting for Forge to be free"))
    with queue_lock:
        stage_dir = os.path.join(JOBS_DIR, "inputs", time.strftime("%Y%m%d-%H%M%S"))
        start, end, parts = None, None, None
        if mode == "i2v":
            staged = [_copy_input(picture, stage_dir, f"picture{k + 1}") for k, picture in enumerate(used)]
            start, end = staged[0], (staged[-1] if n > 1 else None)
            if kind == "multi":
                parts = [{"start_image": staged[k], "end_image": staged[k + 1], "prompt": part_prompts[k]}
                         for k in range(n - 1)]
        elif from_ingredients:
            yield out(status_html("picture", 0.5, note="FLUX 3 Klein draws the first frame from the ingredients"))
            try:
                start = draw_from_ingredients(styled, ingredient_pictures, t2v_aspect, seed, stage_dir)
            except FrameCancelled:
                yield out(status_html('cancelled', note='first-frame generation stopped'))
                return
            except Exception as e:
                yield out(status_html("error", error=f"Drawing the first frame failed: {type(e).__name__}: {e}"))
                return
            yield out(status_html("picture", 1.0), preview=start)
        elif not is_ltx:
            yield out(status_html("picture", 0.5, note="Forge draws the first frame with the selected checkpoint"))
            try:
                start = draw_first_frame((t2v_picture_prompt or "").strip() or styled, negative, t2v_aspect,
                                         t2v_steps, t2v_cfg, t2v_sampler, seed, stage_dir)
            except FrameCancelled:
                yield out(status_html('cancelled', note='first-frame generation stopped'))
                return
            except Exception as e:
                yield out(status_html("error", error=f"Drawing the first frame failed: {type(e).__name__}: {e}"))
                return
            yield out(status_html("picture", 1.0), preview=start)

        spec = build_job(kind, start, end, wan_prompt, negative, quality, seconds, seed, preset, steps, shift, cfg,
                         sampler, int8, attention, width, height, smooth, upscale, loras, t2v_aspect, parts)
        if is_ltx:
            spec['native_audio'] = bool(native_audio) or audio_mode
            spec['source_audio'] = _copy_input(audio_file, stage_dir, 'source-audio') if audio_mode else None
        label = {"flf": "Image to Video, first and last frame",
                 "multi": f"Image to Video, {n} pictures ({n - 1} parts)"}.get(kind, MODES[mode])
        frames = spec["frames"] if not parts else len(parts) * (spec["frames"] - 1) + 1
        details = (f"{label} | {spec['width']}x{spec['height']} | {frames} frames at {spec['fps']} fps "
                   f"({(frames - 1) / spec['fps']:.1f} s) | seed {seed} | {preset}")
        if from_ingredients:
            details += (f"\nFirst frame: FLUX 3 Klein from {len(ingredient_pictures)} ingredient"
                        f"{'s' if len(ingredient_pictures) > 1 else ''}")
        if parts:
            details += "\nParts: " + " | ".join(f"{k + 1}. {part['prompt'][:70]}" for k, part in enumerate(parts))
        if loras:
            details += "\nLoRA: " + ", ".join(f"{os.path.splitext(os.path.basename(l['path']))[0]} {l['strength']:g}" for l in loras)
        if not_wan:
            details += ('\nImage-only LoRAs omitted from LTX: ' if is_ltx else
                        '\nNot Wan LoRAs (used for the first picture only): ') + ', '.join(not_wan)
        made = yield from _run_job(spec, details, out, nothing)
    # The sound job takes Forge's queue itself, so it starts after this one has let it go.
    if made and auto_sound and not (is_ltx and spec.get('native_audio')):
        words = (sound_words or "").strip() or wan_prompt
        yield from _sound_after(made, words, out, nothing, details)


def video_loras(prompt, negative, styles, lora1, lora1_w, lora2, lora2_w, preset):
    if not ltx.is_ltx(preset):
        return _wan_loras(prompt, negative, styles, lora1, lora1_w, lora2, lora2_w)
    styled, neg = prompting.apply_styles(prompt, negative or '', styles or [])
    available = {prompting._norm(os.path.basename(p)): p for p in ltx.loras()}
    requested = [(m.group(1), float(m.group(2) or 1)) for m in prompting.LORA_TAG.finditer(styled)]
    for name, strength in [(lora1, lora1_w), (lora2, lora2_w)]:
        if name and name != NO_LORA:
            if prompting._norm(name) not in available:
                raise ValueError('This LoRA is not in the LTX folder. Clear the slot or choose an LTX LoRA: ' + name)
            requested.append((name, float(strength)))
    adapters, image_only = [], []
    for name, strength in requested:
        path = available.get(prompting._norm(name))
        if path:
            if not any(item['path'] == path for item in adapters):
                adapters.append(dict(path=path, strength=strength, family='ltx'))
        else:
            image_only.append(name)
    clean = prompting.LORA_TAG.sub('', styled).strip(' ,')
    return styled, clean, prompting.LORA_TAG.sub('', neg).strip(' ,'), adapters, image_only


def _wan_loras(prompt, negative, styles, lora1, lora1_w, lora2, lora2_w):
    """Styles applied, `<lora:...>` tags taken out: (styled prompt, Wan prompt, negative, loras, not Wan LoRAs)."""
    styled, negative = prompting.apply_styles(prompt or "", negative or "", styles or [])
    wan_prompt, tag_loras, not_wan = prompting.split_loras(styled)
    negative = prompting.split_loras(negative)[0]
    loras = []
    lora_files = prompting.lora_files()
    for name, weight in ((lora1, lora1_w), (lora2, lora2_w)):
        if name and name != NO_LORA and name in lora_files and float(weight) != 0:
            loras.append({"path": lora_files[name], "strength": float(weight)})
    loras += [{"path": path, "strength": weight} for path, weight in tag_loras if weight != 0]
    return styled, wan_prompt, negative, prompting.with_partners(loras), not_wan


def video_info(path):
    """(width, height, seconds, has sound) of a video file."""
    import cv2
    cap = cv2.VideoCapture(path)
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    probe = subprocess.run([sound.ffmpeg(), "-hide_banner", "-i", path], capture_output=True, text=True,
                           encoding="utf-8", errors="replace").stderr
    return w, h, sound.duration(path), "Audio:" in probe


def find_people(video, start, character=None):
    """The frame at `start` (and the character picture) with every person numbered, and the choices for 'Person to
    replace' and 'Person in the picture'."""
    if not video:
        return (None, gr.update(choices=["1"], value="1"), gr.update(),
                status_html("error", error="Upload the video first."))
    w, h, _, _ = video_info(video)
    pw, ph = fit_size(w, h, 640 * 640)
    folder = os.path.join(JOBS_DIR, "people")
    os.makedirs(folder, exist_ok=True)
    out = os.path.join(folder, time.strftime("%Y%m%d-%H%M%S") + ".png")
    worker = os.path.join(os.path.dirname(runner.WORKER), "find_people.py")
    cmd = [runner.engine_python(), worker, video, str(float(start or 0)), str(pw), str(ph), out,
           models.file_path("dw_det"), models.file_path("dw_pose")] + ([character] if character else [])
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0 or not os.path.isfile(out):
        return None, gr.update(), gr.update(), status_html("error", error="Finding the people failed: "
                                                                         + (r.stderr or r.stdout)[-600:])
    found = json.loads(r.stdout.strip().splitlines()[-1])
    n, m = found["people"], found.get("picture_people", 0)
    if n > 1:
        note = f"{n} people in the video, numbered from the left: choose the one to replace"
    else:
        note = "one person in the video" if n == 1 else "nobody found on this frame: try another start time"
    if m > 1:
        note += f"; {m} people in the picture: choose one, or 'Everyone in the picture'"
    if found.get("character_sheet"):
        note += "; character sheet: one identity, using its front view"
    return (out, gr.update(choices=[str(i + 1) for i in range(max(1, n))], value="1"),
            gr.update(choices=[str(i + 1) for i in range(max(1, m))], value="1"), status_html("idle", note=note))


def generate_ingredients(prompt, negative, quality, seconds, seed, aspect, int8, attention, smooth, upscale, lora1,
                         lora1_w, lora2, lora2_w, styles, auto_sound, sound_words, *pictures):
    """Ingredients to Video (Phantom-Wan 14B): up to four pictures of who and what is in the video (people, an object,
    a place) and the words for what happens; the video keeps their looks. The shape is the Shape at the top. A
    character sheet (many views of one person on a plain background) is split into the views that carry the likeness
    (sheet_views)."""
    nothing = gr.update()

    def out(status, preview=nothing, video=nothing, details=nothing, last=nothing):
        return status, preview, video, details, last

    refs = [p for p in pictures if p]
    if not refs:
        yield out(status_html("error", error="Add at least one ingredient: a picture of who or what is in the video."))
        return
    if not (prompt or "").strip():
        yield out(status_html("error", error="Write what happens in the video, naming the ingredients "
                                             "(\"the woman from picture 1 holds the cup from picture 2...\")."))
        return
    gone = models.ingredients_missing()
    if gone:
        yield out(status_html("error", error="The ingredients model is not downloaded yet (the button in this tab)."))
        return
    if runner.busy():
        yield out(status_html("error", error="A video is already being made (it keeps going even if this page was "
                                             "reloaded). Wait for it, press Stop, or press Show the last video."))
        return
    seed = int(seed) if seed is not None and int(seed) >= 0 else random.randint(0, 2 ** 31 - 1)
    _, wan_prompt, negative, loras, not_wan = _wan_loras(prompt, negative, styles, lora1, lora1_w, lora2, lora2_w)
    # Wan 2.2 LoRAs (high/low pairs) are for another model; Wan 2.1 14B LoRAs fit Phantom
    loras = [l for l in loras if not re.search(r"(^|[^a-z])(high|low)([^a-z]|$)", os.path.basename(l["path"]).lower())]

    stage_dir = os.path.join(JOBS_DIR, "inputs", time.strftime("%Y%m%d-%H%M%S"))
    yield out(status_html("start", 0, note="looking at the ingredients"))
    staged, sheet_notes = sheet_views(refs, stage_dir)
    from modules.call_queue import queue_lock
    yield out(status_html("start", 0, note="waiting for Forge to be free"))
    with queue_lock:
        preset = next(iter(models.PRESETS))
        spec = build_job("t2v", None, None, wan_prompt, negative, quality, seconds, seed, preset, 8, 5.0, 1.0, "euler",
                         int8, attention, 0, 0, smooth, upscale, loras, aspect)
        spec.update(mode="ingredients", references=staged, model_label="Phantom-Wan 14B FusionX Q3",
                    basename=f"{time.strftime('%H%M%S')}-ingredients-{seed}",
                    experts=[{"path": models.file_path("phantom_q3"), "until_sigma": 0.0, "label": "phantom"}])
        details = (f"{MODES['ingredients']} | {len(staged)} ingredient{'s' if len(staged) > 1 else ''} | "
                   f"{spec['width']}x{spec['height']} | {spec['frames']} frames at 16 fps "
                   f"({(spec['frames'] - 1) / 16:.1f} s) | seed {seed} | Phantom-Wan 14B FusionX")
        if loras:
            details += "\nLoRA: " + ", ".join(f"{os.path.splitext(os.path.basename(l['path']))[0]} {l['strength']:g}" for l in loras)
        for note in sheet_notes:
            details += "\n" + note
        made = yield from _run_job(spec, details, out, nothing)
    if made and auto_sound:
        yield from _sound_after(made, (sound_words or "").strip() or wan_prompt, out, nothing, details)


def sheet_views(refs, stage_dir):
    """The ingredient pictures as staged for the engine. A character sheet shows one person many times and small;
    given whole, Phantom reads a crowd and the video person comes out a stranger. So a sheet becomes the views of
    it that carry the likeness (its front full body, and its close-up when it has one), within MAX_INGREDIENTS
    pictures in all."""
    staged, notes = [], []
    matte = sheets.matte_file(os.path.join(WEBUI_DIR, "models"))
    for k, path in enumerate(refs):
        room = MAX_INGREDIENTS - len(staged) - (len(refs) - k - 1)
        views = []
        try:
            views = sheets.split(path, matte)[:max(1, room)]
        except Exception as e:  # never stop a video over this: the picture is used whole
            print(f"[Video Studio] could not read ingredient {k + 1} as a character sheet: {e}")
        if not views:
            staged.append(_copy_input(path, stage_dir, f"ingredient{k + 1}"))
            continue
        os.makedirs(stage_dir, exist_ok=True)
        for name, picture in views:
            dest = os.path.join(stage_dir, f"ingredient{k + 1}-{name}.png")
            picture.save(dest)
            staged.append(dest)
        notes.append(f"Ingredient {k + 1} is a character sheet: used its {' and '.join(n for n, _ in views)} "
                     f"view{'s' if len(views) > 1 else ''}.")
    return staged, notes


def ingredients_models_markdown(preset=None):
    """What each way of Ingredients needs. The first-frame way needs only an installed FLUX 3 Klein (it is used
    for that one picture even when another checkpoint is selected) and the chosen video model; Phantom needs its own
    model, whichever video model is chosen."""
    flux = ("First frame: FLUX 3 Klein is installed." if flux3_glue() is not None else
            "First frame: FLUX 3 Klein is not installed (Phantom is used instead).")
    if not models.ingredients_missing():
        return flux + " Phantom-Wan 14B FusionX Q3 (7.2 GB): here."
    return flux + " Phantom-Wan 14B FusionX Q3 (7.2 GB): not downloaded yet."


def generate_replace(prompt, negative, seed, smooth, upscale, int8, attention, lora1, lora1_w, lora2, lora2_w, styles,
                     auto_sound, sound_words, video, character, person, start, seconds, rquality, fps, mask_shape,
                     keep_sound, steps, relight, char_person="1", who=None, faces=False):
    """Replace a character: the person in a video becomes the one in a picture (Wan 2.2 Animate). With 'Everyone in
    the picture', each person of the picture replaces one person of the video, a pass each, every pass working on
    the one before."""
    nothing = gr.update()

    def out(status, preview=nothing, video=nothing, details=nothing, last=nothing):
        return status, preview, video, details, last

    if not video:
        yield out(status_html("error", error="Replace a character needs the video."))
        return
    if not character:
        yield out(status_html("error", error="Replace a character needs a picture of the new character."))
        return
    gone = models.replace_missing()
    if gone:
        yield out(status_html("error", error="Some model files are missing (download them in this tab): "
                                             + ", ".join(os.path.basename(models.file_path(k)) for k in gone)))
        return
    if runner.busy():
        yield out(status_html("error", error="A video is already being made (it keeps going even if this page was "
                                             "reloaded). Wait for it, press Stop, or press Show the last video."))
        return
    seed = int(seed) if seed is not None and int(seed) >= 0 else random.randint(0, 2 ** 31 - 1)
    _, wan_prompt, negative, loras, not_wan = _wan_loras(prompt, negative, styles, lora1, lora1_w, lora2, lora2_w)
    w, h, total, has_sound = video_info(video)
    start = max(0.0, min(float(start or 0), max(0.0, total - 0.5)))
    seconds = max(0.5, min(float(seconds), total - start)) if total else float(seconds)
    width, height = fit_size(w, h, REPLACE_QUALITY.get(rquality, 480 * 832))
    base_loras = [{"path": models.file_path("lightx2v_i2v"), "strength": 1.0}]
    if relight:
        base_loras.append({"path": models.file_path("animate_relight"), "strength": 1.0})

    # (person in the picture, person in the video) per pass, 0-based
    passes = [(max(0, int(char_person or 1) - 1), max(0, int(person or 1) - 1))]
    if who in WHO[1:]:
        m = _count_picture_people(character)
        if m < 2:
            yield out(status_html("error", error="'Everyone in the picture' needs a picture with two or more people "
                                                 "(press Find the people to see who is found)."))
            return
        order = list(range(m)) if who == WHO[1] else list(range(m))[::-1]
        passes = [(c, v) for v, c in enumerate(order)]

    from modules.call_queue import queue_lock
    yield out(status_html("start", 0, note="waiting for Forge to be free"))
    with queue_lock:
        stage_dir = os.path.join(JOBS_DIR, "inputs", time.strftime("%Y%m%d-%H%M%S"))
        out_dir = os.path.join(OUTPUT_DIR, time.strftime("%Y-%m-%d"))
        os.makedirs(out_dir, exist_ok=True)
        ups = models.upscaler_models()
        spec = {
            "mode": "replace", "prompt": wan_prompt.strip(), "negative": (negative or "").strip(),
            "driving_video": _copy_input(video, stage_dir, "video"),
            "character_image": _copy_input(character, stage_dir, "character"),
            "person": passes[0][1], "character_person": passes[0][0], "start": start, "seconds": seconds,
            "width": width, "height": height, "fps": int(fps), "steps": int(steps), "shift": 5.0, "cfg": 1.0,
            "seed": seed, "int8": bool(int8), "attention": attention, "mask_shape": MASK_SHAPES.get(mask_shape, "box"),
            "keep_sound": bool(keep_sound),
            "model": models.file_path("animate_q3"), "clip_vision": models.file_path("clip_h"),
            "vae": models.file_path("vae21"), "text_encoder": models.file_path("umt5"),
            "pose_detector": models.file_path("dw_det"), "pose_model": models.file_path("dw_pose"),
            "sam_model": os.path.dirname(models.file_path("sam2")),
            "loras": base_loras + loras, "cache_dir": CACHE_DIR, "output_dir": out_dir,
            "basename": f"{time.strftime('%H%M%S')}-replace-{seed}", "model_label": "Wan 2.2 Animate 14B Q3",
            "interpolate": bool(smooth), "film_model": models.film_model(),
            "upscale": {"model": ups.get(upscale.split(" ", 1)[-1]), "scale": float(upscale.split(" ", 1)[0][1:])}
            if upscale != UPSCALE_OFF else None,
        }
        frames = int(seconds * int(fps))
        parts = max(1, -(-(frames - 5) // 72))
        details = (f"{MODES['replace']} | person {int(person or 1)} | {width}x{height} | {frames} frames at {int(fps)} fps "
                   f"({seconds:.1f} s from {start:.1f} s, {parts} part{'s' if parts > 1 else ''}) | seed {seed}")
        if loras:
            details += "\nLoRA: " + ", ".join(f"{os.path.splitext(os.path.basename(l['path']))[0]} {l['strength']:g}" for l in loras)
        if not_wan:
            details += "\nNot Wan LoRAs (not used): " + ", ".join(not_wan)
        made, first_base = None, spec["basename"]
        for i, (pic, vid) in enumerate(passes):
            last = i == len(passes) - 1
            step = dict(spec, person=vid, character_person=pic, basename=step_base(first_base, i, len(passes)))
            if i:
                # The next pass works on the last one's result, which already starts at `start` at the chosen rate.
                step.update(driving_video=made, start=0.0, seconds=frames / int(fps))
            if not last:
                step.update(interpolate=False, upscale=None)
            note = details + (f"\nPass {i + 1} of {len(passes)}: picture person {pic + 1} -> video person {vid + 1}"
                              if len(passes) > 1 else "")
            made = yield from _run_job(step, note, out, nothing)
            if not made:
                return
        if faces and faceswap.available():
            pictures = [os.path.join(out_dir, step_base(first_base, i, len(passes)) + ".character.png")
                        for i in range(len(passes))]
            pictures = [p.replace(".character.png", ".character-face.png")
                        if os.path.isfile(p.replace(".character.png", ".character-face.png")) else p for p in pictures]
            made = yield from _match_faces(made, [(pic, vid) for pic, (_, vid) in zip(pictures, passes)], out, details)
    # With the video's own sound kept, MMAudio is only asked when the video had none.
    if made and auto_sound and not (keep_sound and has_sound):
        words = (sound_words or "").strip() or wan_prompt or "ambient sound of the scene"
        yield from _sound_after(made, words, out, nothing, details)


def step_base(base, i, count):
    return base if i == count - 1 else f"{base}-pass{i + 1}"


def _match_faces(video, faces, out, details):
    """FaceFusion puts each character's real face on their dancer, one face at a time; the video without it stays
    as -raw. Returns the video to show."""
    raw = video[:-4] + "-raw.mp4"
    os.replace(video, raw)
    for ext in (".first.png", ".last.png", ".json"):
        if os.path.isfile(video[:-4] + ext):
            shutil.copyfile(video[:-4] + ext, raw[:-4] + ext)
    work = os.path.join(JOBS_DIR, "faces-" + time.strftime("%Y%m%d-%H%M%S"))
    current, t0 = raw, time.time()
    for n, (source, position) in enumerate(faces):
        if not os.path.isfile(source):
            continue
        target = os.path.join(work, f"faces{n + 1}.mp4") if n < len(faces) - 1 else video
        proc = faceswap.start(source, current, target, position, work)
        while proc.poll() is None:
            yield out(status_html("writing", None, note=f"making face {n + 1} of {len(faces)} like the picture (FaceFusion)",
                                  elapsed=time.time() - t0))
            time.sleep(2)
        if proc.returncode != 0 or not os.path.isfile(target):
            with open(faceswap.log(work), encoding="utf-8", errors="replace") as f:
                tail = f.read()[-400:]
            if not os.path.isfile(video):
                os.replace(raw, video)
            yield out(status_html("done", 1.0, note="the faces could not be matched: " + tail), video=video)
            return video
        current = target
    yield out(status_html("done", 1.0, note="faces matched to the picture", elapsed=time.time() - t0), video=video,
              details=details + f"\nSaved: {video}\nWithout the face pass: {raw}")
    return video


def _count_picture_people(path):
    """How many people the character picture shows (the CPU, a second or two)."""
    worker = os.path.join(os.path.dirname(runner.WORKER), "find_people.py")
    with Image.open(path) as im:
        w, h = im.size
    folder = os.path.join(JOBS_DIR, "people")
    os.makedirs(folder, exist_ok=True)
    # a picture is a one-frame video to ffmpeg
    cmd = [runner.engine_python(), worker, path, "0", str(w // 2 * 2), str(h // 2 * 2),
           os.path.join(folder, "picture.png"), models.file_path("dw_det"), models.file_path("dw_pose"), path]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        return int(json.loads(r.stdout.strip().splitlines()[-1])["picture_people"])
    except (ValueError, IndexError, KeyError):
        return 0


def _sound_after(video, words, out, nothing, details):
    """Generate's own sound: MMAudio from the picture and the prompt, as a -sound copy beside the video."""
    have = sound.status()
    if not have["effects"]:
        yield out(status_html("done", 1.0, note="no sound: MMAudio is not installed"))
        return
    base = os.path.splitext(video)[0]
    target = base + "-sound.mp4"
    job = {"video": video, "out": target,
           "effects": {"on": True, "prompt": words[:300], "negative": "music, speech, talking", "seed": 0, "db": 0.0},
           "speech": {"mode": "none"}}
    try:
        task = sound.start(job, JOBS_DIR)
    except RuntimeError as e:
        yield out(status_html("done", 1.0, note=f"no sound: {e}"))
        return
    t0 = time.time()
    for ev in task.run():
        stage = ev.get("stage")
        if stage == "done":
            for ext in (".first.png", ".last.png", ".json"):
                if os.path.isfile(base + ext):
                    shutil.copyfile(base + ext, base + "-sound" + ext)
            yield out(status_html("done", 1.0, note="with AI sound", elapsed=time.time() - t0), video=target,
                      details=details + f"\nSaved with sound: {target}\nWithout sound: {video}", last=base + "-sound.last.png")
            return
        if stage in ("error", "cancelled"):
            yield out(status_html("done", 1.0, note="the video is saved; the sound failed: " + str(ev.get("error", ""))[:200]))
            return
        yield out(status_html(stage, ev.get("progress"), note="adding AI sound", elapsed=time.time() - t0, absolute=True))


def _run_job(spec, details, out, nothing):
    """Runs a prepared job in the worker and yields UI updates until it ends. The caller holds Forge's queue."""
    runner.free_forge_memory()
    job = runner.start(spec)
    yield out(status_html("start", 0, note=details), details=details)
    t0 = time.time()
    last_preview = None
    try:
        for ev in job.run():
            stage = ev.get("stage", "")
            if stage == "done":
                video = ev.get("video")
                last = os.path.splitext(video)[0] + ".last.png"
                took = time.time() - t0
                text = details + f"\nSaved: {video}\nTook {int(took // 60)} min {int(took % 60)} s"
                yield out(status_html("done", 1.0, elapsed=took), video=video, details=text, last=last)
                # "done" arrives while the worker is still exiting (and giving its memory back); a job started in
                # that moment was refused as "already being made" (2026-10-25, the second pass of a chain)
                with job.cond:
                    while not job.ended:
                        job.cond.wait(1.0)
                runner.finish(job)
                return video
            if stage in ("error", "cancelled"):
                err = ev.get("error", "")
                if ev.get("oom"):
                    err += "\nThe card ran out of memory: choose a lower quality or a shorter video."
                trace = ev.get("trace", "")
                yield out(status_html(stage, error=(err + ("\n\n" + trace[-1500:] if trace and stage == "error" else "")) or None),
                          details=details + f"\nJob folder: {job.dir}")
                return
            p = ev.get("progress")
            frac = ev.get('overall_progress', overall(stage, p))
            elapsed = time.time() - t0
            eta = elapsed * (1 - frac) / frac if frac > 0.08 else None
            note = ev.get("note", "")
            if stage == "sampling" and ev.get("step"):
                note = f"step {ev['step']} of {ev['steps']}" + (f" | {note}" if note else "")
            preview = ev.get("preview")
            if preview and os.path.isfile(preview):
                snap = os.path.join(job.dir, f"preview_{ev.get('preview_key', ev.get('step', 0))}.png")
                if snap != last_preview:
                    try:
                        shutil.copyfile(preview, snap)
                        last_preview = snap
                    except OSError:
                        pass
            yield out(status_html(stage, frac if 'overall_progress' in ev else p, note=note,
                                  elapsed=elapsed, eta=eta, absolute='overall_progress' in ev),
                      preview=last_preview if preview else nothing)
    finally:
        # The page may be gone; the job keeps running in its own thread and saves its video either way. Keep Forge's
        # queue until then, so a txt2img started meanwhile does not land on a card the worker is using.
        if runner.current() is job:
            import threading
            threading.Thread(target=_hold_queue, args=(job,), daemon=True).start()


def _hold_queue(job):
    from modules.call_queue import queue_lock
    with queue_lock:
        with job.cond:
            while not job.ended:
                job.cond.wait(5.0)


def show_last():
    """The newest video in the output folder (or the one being made), for after a page reload."""
    job = runner.current()
    if job is not None:
        ev = job.latest() or {}
        return (status_html(ev.get("stage", "start"), ev.get("progress"), note="still being made; press again later"),
                gr.update(), gr.update(), gr.update())
    videos = []
    for root, _, files in os.walk(OUTPUT_DIR):
        videos += [os.path.join(root, f) for f in files if f.endswith(".mp4")]
    if not videos:
        return status_html("idle", 0, note="no video yet"), gr.update(), gr.update(), gr.update()
    video = max(videos, key=os.path.getmtime)
    last = os.path.splitext(video)[0] + ".last.png"
    return (status_html("done", 1.0, note=os.path.basename(video)), video, f"Saved: {video}",
            last if os.path.isfile(last) else "")


# Refine = a video Hires fix: the finished clip is encoded at a larger size and only the last 1 or 2 of the 4 steps
# run on it, so the motion and the composition stay and the detail is drawn again at the new resolution.
REFINE_STRENGTH = {"Light: same picture, finer detail (1 step)": 3, "Stronger: more new detail (2 steps)": 2}
REFINE_QUALITY = {"576p": 576 * 1024, "720p (clips up to 5 s)": 720 * 1280}


def refine(last_frame, rquality, rstrength, smooth, upscale, int8, attention, preset):
    nothing = gr.update()

    def out(status, preview=nothing, video=nothing, details=nothing, last=nothing):
        return status, preview, video, details, last

    if not last_frame or not last_frame.endswith(".last.png"):
        yield out(status_html("error", error="Make a video first: Refine works on the last video made here."))
        return
    base = last_frame[:-len(".last.png")]
    video, meta_path = base + ".mp4", base + ".json"
    if not (os.path.isfile(video) and os.path.isfile(meta_path)):
        yield out(status_html("error", error=f"The last video or its settings file is missing: {video}"))
        return
    if runner.busy():
        yield out(status_html("error", error="A video is already being made. Wait for it or press Stop."))
        return
    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)
    if meta.get("parts"):
        # the whole joined video at a larger size does not fit an 8 GB card in one go
        yield out(status_html("error", error="Refine works on a single clip. A video made from several pictures is "
                                             "not refined: make it at 576p directly."))
        return
    if meta.get('backend') == 'ltx':
        chosen = preset if ltx.is_ltx(preset) else meta.get('model_label', ltx.DEFAULT_PRESET)
        if not ltx.is_ltx(chosen):
            chosen = ltx.DEFAULT_PRESET
        if models.missing(chosen) or not ltx.runtime_ready():
            yield out(status_html('error', error='Download the selected LTX model and set up its engine before refining.'))
            return
        w, h = fit_size(int(meta['width']), int(meta['height']), min(REFINE_QUALITY[rquality], 576 * 1024), 32)
        fps = int(meta.get('base_fps', 24))
        frame_count = int(meta.get('base_frames', meta['frames']))
        spec = build_job('i2v', None, None, meta.get('prompt', ''), meta.get('negative', ''),
            ltx.DEFAULT_QUALITY, (frame_count - 1) / fps, int(meta.get('seed', 0)), chosen,
            8, 2, 1, 'euler_ancestral', False, attention, w, h, smooth, upscale,
            [item for item in meta.get('loras', []) if item.get('family') == 'ltx' and os.path.isfile(item.get('path', ''))],
            next(iter(ASPECTS)))
        spec.update(init_video=video, source_audio=video if meta.get('native_audio') else None,
                    native_audio=bool(meta.get('native_audio')), denoise_start_step=6 if REFINE_STRENGTH[rstrength] == 3 else 5)
        from modules.call_queue import queue_lock
        yield out(status_html('start', 0, note='waiting for Forge to be free'))
        with queue_lock:
            yield from _run_job(spec, f'LTX refine | {w}x{h} | same motion and original audio', out, nothing)
        return
    if ltx.is_ltx(preset):
        preset = meta.get('model_label', models.DEFAULT_PRESET)
    pr = models.PRESETS[preset]
    w, h = fit_size(int(meta["width"]), int(meta["height"]), REFINE_QUALITY[rquality])
    first = meta.get("start_image") if meta.get("start_image") and os.path.isfile(meta["start_image"]) else base + ".first.png"
    last = meta.get("end_image") if meta.get("end_image") and os.path.isfile(meta["end_image"]) else base + ".last.png"
    mode = meta.get("mode", "flf2v")
    out_dir = os.path.join(OUTPUT_DIR, time.strftime("%Y-%m-%d"))
    os.makedirs(out_dir, exist_ok=True)
    ups = models.upscaler_models()
    spec = {
        "mode": mode, "prompt": meta.get("prompt", ""), "negative": meta.get("negative", ""),
        "start_image": first, "end_image": last if mode == "flf2v" else None,
        "width": w, "height": h, "frames": int(meta["frames"]), "fps": 16,
        "steps": 4, "shift": float(meta.get("shift", pr["shift"])), "cfg": 1.0, "sampler": "euler",
        "seed": int(meta.get("seed", 0)), "int8": bool(int8), "attention": attention,
        "text_encoder": models.file_path(pr["text_encoder"]), "vae": models.file_path(pr["vae"]),
        "experts": [{"path": models.file_path(k), "until_sigma": s, "label": label} for k, s, label in pr["experts"]],
        "cache_dir": CACHE_DIR, "output_dir": out_dir,
        "basename": f"{time.strftime('%H%M%S')}-refine-{os.path.basename(base)}",
        "model_label": preset, "init_video": video, "init_every": max(1, round(int(meta.get("fps", 16)) / 16)),
        "denoise_start_step": REFINE_STRENGTH[rstrength],
        "loras": [l for l in (meta.get("loras") or []) if os.path.isfile(l.get("path", ""))],
        "interpolate": bool(smooth), "film_model": models.film_model(),
        "upscale": {"model": ups.get(upscale.split(" ", 1)[-1]), "scale": float(upscale.split(" ", 1)[0][1:])}
        if upscale != UPSCALE_OFF else None,
    }
    from modules.call_queue import queue_lock
    yield out(status_html("start", 0, note="waiting for Forge to be free"))
    with queue_lock:
        details = (f"Refine of {os.path.basename(video)} | {w}x{h} | {spec['frames']} frames | "
                   f"{'1 step' if spec['denoise_start_step'] == 3 else '2 steps'}")
        yield from _run_job(spec, details, out, nothing)


SPEECH_MODES = ["No speech", "Text", "My recording"]


def add_sound(last_frame, fx_on, fx_prompt, fx_negative, fx_db, mode, text, language, voice, rec_file, rec_mic,
              start, exaggeration, speech_db, lips, seed):
    """Sound and speech on the last video: AI effects, a voice from text or a recording, lips, a mix."""
    nothing = gr.update()

    def out(status, video=nothing, details=nothing, last=nothing):
        return status, video, details, last

    if not last_frame or not last_frame.endswith(".last.png"):
        yield out(status_html("error", error="Make a video first (or press Show the last video): sound is added to the last video."))
        return
    base = last_frame[:-len(".last.png")]
    video = base + ".mp4"
    if not os.path.isfile(video):
        yield out(status_html("error", error=f"The last video is missing: {video}"))
        return
    speaking = mode != SPEECH_MODES[0]
    recording = rec_file or rec_mic
    if not fx_on and not speaking:
        yield out(status_html("error", error="Choose sound effects, speech, or both."))
        return
    if mode == "Text" and not (text or "").strip():
        yield out(status_html("error", error="Write what the character says."))
        return
    if mode == "My recording" and not recording:
        yield out(status_html("error", error="Choose a sound file or record with the microphone."))
        return
    if speaking and voice == sound.CLONE and mode == "Text" and not recording:
        yield out(status_html("error", error="To copy a voice, add a recording of it (5-15 seconds of clear speech)."))
        return
    if runner.busy() or sound.busy():
        yield out(status_html("error", error="Something is already being made. Wait for it or press Stop."))
        return
    have = sound.status()
    missing = [n for n, need in (("the voices", mode == "Text" and voice != sound.PIPER), ("the fast Hebrew voice", mode == "Text" and voice == sound.PIPER),
                                  ("MMAudio", fx_on), ("MuseTalk", speaking and lips))
               if need and not have[{"the voices": "voices", "the fast Hebrew voice": "piper", "MMAudio": "effects", "MuseTalk": "lips"}[n]]]
    if missing:
        yield out(status_html("error", error="Not installed: " + ", ".join(missing)))
        return
    stem = os.path.basename(base)
    target = os.path.join(os.path.dirname(video), f"{stem}-sound.mp4")
    n = 2
    while os.path.exists(target):
        target = os.path.join(os.path.dirname(video), f"{stem}-sound{n}.mp4")
        n += 1
    job = {"video": video, "out": target,
           "effects": {"on": bool(fx_on), "prompt": fx_prompt, "negative": fx_negative, "seed": int(seed or 0), "db": float(fx_db)},
           "speech": {"mode": {"Text": "text", "My recording": "recording"}.get(mode, "none"), "text": text, "language": language,
                      "voice": voice, "recording": recording, "exaggeration": float(exaggeration), "seed": int(seed or 0),
                      "start": float(start), "db": float(speech_db), "lips": bool(lips)}}
    task = sound.start(job, JOBS_DIR)
    t0 = time.time()
    for ev in task.run():
        stage = ev.get("stage")
        elapsed = time.time() - t0
        if stage == "done":
            made = ev["video"]
            new_base = os.path.splitext(made)[0]
            for ext in (".first.png", ".last.png", ".json"):
                if os.path.isfile(base + ext):
                    shutil.copyfile(base + ext, new_base + ext)
            yield out(status_html("done", 1.0, elapsed=elapsed, note=os.path.basename(made)), video=made,
                      details=f"Saved: {made}", last=new_base + ".last.png")
            return
        if stage in ("error", "cancelled"):
            yield out(status_html(stage, error=ev.get("error")))
            return
        yield out(status_html(stage, ev.get("progress"), note=ev.get("note", ""), elapsed=elapsed, absolute=True))


def cancel():
    if _FRAME_ACTIVE:
        from modules import shared
        _FRAME_STOP.set()
        shared.state.interrupt()
    runner.cancel_current()
    sound.cancel_current()
    return status_html("cancelled", note="stopping after the current block")


def open_folder():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    day = os.path.join(OUTPUT_DIR, time.strftime("%Y-%m-%d"))
    target = day if os.path.isdir(day) else OUTPUT_DIR
    if os.name == "nt":
        os.startfile(target)  # noqa: S606 - opens Explorer on the user's own output folder
    else:
        subprocess.Popen(["xdg-open", target])


def lora_choices(preset=None):
    paths = ltx.loras() if ltx.is_ltx(preset) else models.loras()
    return [NO_LORA] + [os.path.basename(p) for p in paths]


def sampler_names():
    try:
        from modules import sd_samplers
        return [s.name for s in sd_samplers.visible_samplers()]
    except Exception:
        return ["Euler a", "DPM++ 2M Karras", "Euler"]


def build():
    presets = list(models.PRESETS)
    installed = models.installed_presets()
    default_preset = installed[0] if installed else models.DEFAULT_PRESET
    initial_ltx = ltx.is_ltx(default_preset)
    initial_settings = models.PRESETS[default_preset]
    # x1.5 turns 720x1280 into 1080x1920; the model enlarges x2 (or x4) and the frame is then scaled down.
    ups = [UPSCALE_OFF] + [f"x{s} {n}" for s in ("1.5", "2") for n in models.upscaler_models()]
    default_up = next((u for u in ups if u.startswith("x2 ") and "x2plus" in u), UPSCALE_OFF)
    samplers = sampler_names()

    with gr.Blocks(analytics_enabled=False) as tab:
        mode = gr.Textbox(value="t2v", visible=False, elem_id="vs_mode")
        preset = gr.Dropdown(presets, value=default_preset, label="Video model", elem_id="vs_model")
        gr.Markdown('Choose Wan or LTX. Model selection loads efficient defaults automatically. '
                    'Character replacement and Phantom use their dedicated Wan models. '
                    'Ingredients draws one first frame of all the pictures with an installed FLUX 3 Klein (the image checkpoint '
                    'stays as chosen), then the chosen video model animates it; without FLUX 3 it uses Phantom.')
        with gr.Row(elem_id="vs_main"):
            with gr.Column(scale=5, elem_id="vs_tabs_column"):
                # Shared by every sub-tab; style.css sets it on the right of the sub-tab buttons
                with gr.Row(elem_id="vs_shared_row"):
                    t2v_aspect = gr.Dropdown(list(ASPECTS), value="9:16 portrait", label="Shape",
                                             elem_id="vs_shape", min_width=124)
                    t2v_sampler = gr.Dropdown(samplers, value=samplers[0] if samplers else None,
                                              label="Sampler for the picture", elem_id="vs_picture_sampler",
                                              min_width=152, visible=not initial_ltx)
                # ids let javascript/video-studio.js open a sub-tab by name (the 🎬 button, Continue)
                with gr.Tabs(elem_id="vs_mode_tabs"):
                    with gr.Tab(MODES["t2v"], id="t2v", elem_id="vs_tab_t2v"):
                        gr.Markdown('Wan: Forge draws the first frame with the image checkpoint, then Wan animates it. '
                                    'LTX: generates video and optional synchronized sound directly from the video prompt.')
                        with gr.Column(visible=not initial_ltx) as picture_controls:
                            t2v_picture_prompt = gr.Textbox(label="Picture prompt (empty: the video prompt)", lines=2)
                            with gr.Row():
                                t2v_steps = gr.Slider(1, 60, value=25, step=1, label="Picture steps")
                                t2v_cfg = gr.Slider(1, 15, value=6, step=0.5, label="Picture CFG")
                    with gr.Tab(MODES["i2v"], id="i2v", elem_id="vs_tab_i2v"):
                        # One picture starts the video; ➕ adds a last frame, then more pictures for a longer video.
                        # All six exist from the start and are shown as they are added.
                        pictures, picture_rows = [], []
                        for row in range(MAX_PICTURES // 2):
                            with gr.Row(visible=row == 0) as picture_row:
                                for k in (2 * row, 2 * row + 1):
                                    pictures.append(gr.Image(label=picture_label(k, 1), type="filepath", height=360,
                                                             elem_id=f"vs_picture{k + 1}", visible=k == 0))
                            picture_rows.append(picture_row)
                        with gr.Row():
                            add_picture = gr.Button("➕ Last frame", size="sm", elem_id="vs_add_picture")
                            remove_picture = gr.Button("✕ Remove the last picture", size="sm", visible=False)
                            reverse_pictures = gr.Button("⇄ Reverse the order", size="sm", visible=False)
                        picture_count = gr.Number(value=1, precision=0, visible=False, label="Pictures in use",
                                                  elem_id="vs_picture_count")
                        pictures_md = gr.Markdown(pictures_note(1))
                        with gr.Column(visible=False) as parts_box:
                            part_prompts = [gr.Textbox(label=f"Part {k + 1}: from picture {k + 1} to picture {k + 2}",
                                                       lines=2, visible=False, elem_id=f"vs_part{k + 1}",
                                                       placeholder="What happens in this part (empty: the prompt below)")
                                            for k in range(MAX_PICTURES - 1)]
                        _take_over_send_to_video(pictures[0])
                    with gr.Tab(MODES["ingredients"], id="ingredients", elem_id="vs_tab_ingredients"):
                        gr.Markdown("Up to four pictures of who and what is in the video - people, a pet, an object, a "
                                    "place, or a whole character sheet - and the prompt says what happens; the video "
                                    "keeps their looks (like Flow's Ingredients). Name them in the prompt: \"the woman "
                                    "(picture 1) pours coffee into the red cup (picture 2) in the kitchen (picture 3)\". "
                                    "The shape is the Shape at the top.")
                        ing_method = gr.Radio(ingredient_methods(initial_ltx), value=ING_AUTO,
                                              interactive=True, label="How the ingredients are used",
                                              info='Automatic can use an installed FLUX 3 model without changing your checkpoint. Native LTX is slower and needs more RAM; describe the references and target scene separately.',
                                              elem_id="vs_ingredients_method")
                        ingredients = []
                        with gr.Row():
                            for k in range(MAX_INGREDIENTS):
                                ingredients.append(gr.Image(label=f"Ingredient {k + 1}", type="filepath", height=240,
                                                            elem_id=f"vs_ingredient{k + 1}"))
                        with gr.Row():
                            ing_models = gr.Markdown(ingredients_models_markdown(default_preset))
                            ing_download = gr.Button('Download missing Ingredients models', size="sm")
                    with gr.Tab(MODES["replace"], id="replace", elem_id="vs_tab_replace"):
                        gr.Markdown("The person in your video becomes the character in your picture: the motion, the "
                                    "face's expressions, the camera and the background stay (Wan 2.2 Animate). A picture "
                                    "of several people: choose one, or 'Everyone in the picture' (one pass per person). "
                                    "Describe the character in the prompt (clothes, hair) for the best likeness, and "
                                    "never a dance style or an occasion (waltz, wedding): the model dresses the "
                                    "character for it.")
                        with gr.Row():
                            rep_video = gr.Video(label="Video", elem_id="vs_rep_video", height=360)
                            rep_char = gr.Image(label="The new character (best: whole body, plain background)",
                                                type="filepath", elem_id="vs_rep_char", height=360)
                        with gr.Row():
                            rep_start = gr.Number(value=0, label="Start at (seconds)", precision=2)
                            rep_seconds = gr.Slider(1, 30, value=5, step=0.5, label="Length to replace (seconds)")
                            rep_people = gr.Button("Find the people", size="sm")
                        with gr.Row():
                            rep_marked = gr.Image(label="People found (numbered from the left): the video, then the picture",
                                                  interactive=False,
                                                  height=260)
                            with gr.Column():
                                rep_person = gr.Dropdown(["1"], value="1", label="Person to replace")
                                rep_char_person = gr.Dropdown(["1"], value="1", label="Person in the picture")
                                rep_quality = gr.Dropdown(list(REPLACE_QUALITY), value=next(iter(REPLACE_QUALITY)),
                                                          label="Quality of the replacement")
                                rep_fps = gr.Dropdown(["16", "24"], value="16", label="Frames per second")
                        rep_who = gr.Radio(WHO, value=WHO[0], label="Who becomes whom")
                        with gr.Row():
                            rep_mask = gr.Radio(list(MASK_SHAPES), value=next(iter(MASK_SHAPES)), label="Area redrawn")
                        with gr.Row():
                            rep_keep = gr.Checkbox(value=True, label="Keep the video's own sound")
                            rep_relight = gr.Checkbox(value=True, label="Light the character like the scene (relight LoRA)")
                            rep_steps = gr.Slider(4, 10, value=6, step=1, label="Steps of the replacement")
                        rep_faces = gr.Checkbox(value=False, interactive=faceswap.available(),
                                                label="Make the faces like the picture (FaceFusion face swap, about "
                                                      "1 min per person)", info='Optional for photographic faces; leave off for drawn or 3D characters.')
                        rep_models = gr.Markdown(replace_models_markdown())
                        rep_download = gr.Button("Download the missing files", size="sm")
                    with gr.Tab(MODES['ltx_audio'], id='ltx_audio', elem_id='vs_tab_ltx_audio'):
                        gr.Markdown('LTX: animate a picture to your audio. Speech and singing drive the video. '
                                    'Choose an LTX model above, add the picture and recording, then describe the scene. '
                                    'Length controls how much audio is used; the original audio is preserved in the result.')
                        with gr.Row():
                            ltx_picture = gr.Image(label='Talking picture', type='filepath', height=320, elem_id='vs_ltx_picture')
                            ltx_recording = gr.Audio(label='Driving audio', type='filepath', elem_id='vs_ltx_audio')
                prompt = gr.Textbox(label="Prompt: what happens in the video", lines=4, elem_id="vs_prompt",
                                    placeholder="Describe the motion and the camera, in English, e.g. 'The man stands up, "
                                                "catches a suit thrown to him and walks into the next room.'")
                styles = gr.Dropdown(prompting.style_names(), value=[], multiselect=True, label="Styles",
                                     elem_id="vs_styles")
                with gr.Row():
                    styles_refresh = gr.Button("Refresh styles", size="sm")
                    styles_apply = gr.Button("Write into the prompt", size="sm")
                with gr.Accordion("Save the prompt as a style", open=False):
                    style_name = gr.Textbox(label="Style name", lines=1, max_lines=1)
                    style_save = gr.Button("Save style", size="sm")
                    gr.Markdown("Saved in Forge's own styles list (the txt2img one too). A style can carry a LoRA: "
                                "`<lora:file name:1>, trigger words`. In Video Studio a tag that names a Wan LoRA loads it "
                                "(both halves of a high/low pair); the trigger words stay in the prompt.")
                with gr.Row():
                    quality = gr.Dropdown(list(ltx.QUALITY if initial_ltx else QUALITY),
                        value=ltx.default_quality(default_preset) if initial_ltx else DEFAULT_QUALITY, label="Quality", elem_id="vs_quality")
                    seconds = gr.Slider(1, 7.5, value=5, step=0.25, label="Length (seconds)", elem_id="vs_seconds")
                    seed = gr.Number(value=-1, label="Seed (-1 random)", precision=0)
                with gr.Row():
                    smooth = gr.Checkbox(value=True, label='Smooth motion: 48 fps (FILM)' if initial_ltx else 'Smooth motion: 32 fps (FILM)')
                    upscale = gr.Dropdown(ups, value=default_up, label="Enlarge the finished video")
                with gr.Row():
                    auto_sound = gr.Checkbox(value=True, label="Add AI sound automatically (MMAudio, about 1.5 min)")
                    sound_words = gr.Textbox(label="Sound: what is heard (plain English; empty: the prompt and the picture)",
                                             lines=1, scale=3, placeholder="explosions, fire roaring, debris falling")
                native_audio = gr.Checkbox(value=True, visible=initial_ltx, label='LTX synchronized sound',
                    info='Generate sound together with the video. This takes priority over automatic MMAudio.', elem_id='vs_ltx_native_audio')
                with gr.Accordion("LoRA", open=False):
                    with gr.Row():
                        lora1 = gr.Dropdown(lora_choices(default_preset), value=NO_LORA, label="LoRA 1")
                        lora1_w = gr.Slider(-2, 2, value=1.0, step=0.05, label="Strength 1")
                    with gr.Row():
                        lora2 = gr.Dropdown(lora_choices(default_preset), value=NO_LORA, label="LoRA 2")
                        lora2_w = gr.Slider(-2, 2, value=1.0, step=0.05, label="Strength 2")
                    gr.Markdown('LoRAs follow the selected video model. Wan: models/VideoStudio/loras or models/Lora/wan. '
                                'LTX: models/VideoStudio/ltx/loras or models/Lora/ltx. '
                                'Wan high/low pairs are loaded together. `<lora:name:1>` in a prompt or style also works.')
                    lora_refresh = gr.Button("Refresh list", size="sm")
                with gr.Accordion("Advanced", open=False):
                    negative = gr.Textbox(label="Negative prompt (used only when CFG is above 1)", lines=2)
                    with gr.Row():
                        steps = gr.Slider(2, 12, value=initial_settings['steps'], step=1, label="Steps", interactive=not initial_ltx)
                        shift = gr.Slider(1, 12, value=initial_settings['shift'], step=0.5, label="Shift", interactive=not initial_ltx)
                        cfg = gr.Slider(1, 6, value=initial_settings['cfg'], step=0.1, label="CFG", interactive=not initial_ltx)
                    with gr.Row():
                        sampler = gr.Dropdown(["euler", "lcm", "euler_ancestral"], value='euler_ancestral' if initial_ltx else 'euler',
                                               label="Sampler", interactive=not initial_ltx)
                        attention = gr.Dropdown(["auto", "sage", "sdpa"], value="auto", label="Attention", visible=not initial_ltx)
                        int8 = gr.Checkbox(value=True, label="Fast int8 math (about 1.5x faster)", visible=not initial_ltx)
                    with gr.Row():
                        width = gr.Number(value=0, label="Width (0: from quality)", precision=0)
                        height = gr.Number(value=0, label="Height (0: from quality)", precision=0)
                with gr.Accordion("Models", open=not installed):
                    models_md = gr.Markdown(models_markdown())
                    with gr.Row():
                        download = gr.Button("Download the missing files of this model", size="sm")
                        engine = gr.Button("Set up the fast engine", size="sm")
                        ltx_engine = gr.Button('Set up LTX engine', size='sm', elem_id='vs_ltx_setup')
                        refresh = gr.Button("Refresh", size="sm")
            with gr.Column(scale=4):
                with gr.Row():
                    go = gr.Button("Generate video", variant="primary", elem_id="vs_generate")
                    stop = gr.Button("Stop", elem_id="vs_stop")
                status = gr.HTML(status_html("idle", 0), elem_id="vs_status")
                video = gr.Video(label="Video", elem_id="vs_video", interactive=False)
                preview = gr.Image(label="Preview (middle frame while it is made)", interactive=False, height=320)
                details = gr.Textbox(label="Details", lines=3, interactive=False)
                last_frame = gr.Textbox(visible=False)
                with gr.Row():
                    cont = gr.Button("Continue from the last frame", size="sm")
                    folder = gr.Button("Open the output folder", size="sm")
                    show = gr.Button("Show the last video", size="sm")
                with gr.Accordion("Refine the last video: same motion, sharper", open=False):
                    with gr.Row():
                        rquality = gr.Dropdown(list(REFINE_QUALITY)[:1] if initial_ltx else list(REFINE_QUALITY),
                                              value=next(iter(REFINE_QUALITY)), label="Refine to")
                        rstrength = gr.Dropdown(list(REFINE_STRENGTH), value=next(iter(REFINE_STRENGTH)), label="Strength")
                    gr.Markdown("Make takes quickly at 480p; when you like one, refine it here. The clip is drawn again "
                                "at the larger size from its own frames, keeping the motion.")
                    rgo = gr.Button("Refine the last video", variant="primary", size="sm")
                with gr.Accordion("Sound and speech for the last video", open=False):
                    with gr.Row():
                        fx_on = gr.Checkbox(value=True, label="AI sound effects from the picture (MMAudio)")
                        fx_db = gr.Slider(-24, 6, value=-6, step=1, label="Effects volume (dB)")
                    fx_prompt = gr.Textbox(label="What is heard (plain English words; empty: from the picture alone)",
                                           placeholder="waves on a beach, footsteps on sand", lines=1)
                    fx_negative = gr.Textbox(value="music, speech, talking", label="Sounds not wanted", lines=1)
                    speech_mode = gr.Radio(SPEECH_MODES, value=SPEECH_MODES[0], label="Speech")
                    speech_text = gr.Textbox(label="What the character says", lines=3, elem_id="vs_speech_text")
                    voices = sound.voice_choices()
                    with gr.Row():
                        speech_lang = gr.Dropdown(list(sound.LANGUAGES), value="Hebrew", label="Language")
                        speech_voice = gr.Dropdown(voices, value=voices[0] if voices else None, label="Voice")
                    with gr.Row():
                        rec_file = gr.Audio(source="upload", type="filepath", label="Recording: a sound file")
                        rec_mic = gr.Audio(source="microphone", type="filepath", label="Recording: the microphone")
                    gr.Markdown("A recording is either what the character says (Speech: My recording) or, with the "
                                "voice 'Copy the voice of my recording', the voice the text is spoken in.")
                    with gr.Row():
                        speech_start = gr.Slider(0, 30, value=0.5, step=0.1, label="Speech starts at (seconds)")
                        speech_exag = gr.Slider(0.25, 1.0, value=0.5, step=0.05, label="Expressiveness")
                        speech_db = gr.Slider(-12, 6, value=0, step=1, label="Voice volume (dB)")
                    with gr.Row():
                        lips = gr.Checkbox(value=True, label="Move the lips to the words (MuseTalk)")
                        sound_seed = gr.Number(value=0, label="Sound seed", precision=0)
                    sgo = gr.Button("Add sound", variant="primary", size="sm")

        inputs = [mode, picture_count] + pictures + part_prompts + [
                  t2v_aspect, t2v_picture_prompt, t2v_steps, t2v_cfg, t2v_sampler,
                  prompt, negative, quality, seconds, seed, preset, steps, shift, cfg, sampler, int8, attention,
                  width, height, smooth, upscale, lora1, lora1_w, lora2, lora2_w, styles, auto_sound, sound_words,
                  rep_video, rep_char, rep_person, rep_start, rep_seconds, rep_quality, rep_fps, rep_mask, rep_keep,
                  rep_steps, rep_relight, rep_char_person, rep_who, rep_faces] + ingredients + [ing_method, ltx_picture, ltx_recording, native_audio]
        def start_ingredients_download(name):
            models.DOWNLOADS.start(models.ingredients_missing())
            error = models.DOWNLOADS.status().get('error')
            return ingredients_models_markdown(name) + (' ' + error if error else " Downloading…")

        ing_download.click(start_ingredients_download, inputs=[preset], outputs=[ing_models], queue=False)
        go.click(_guarded(generate), inputs=inputs, outputs=[status, preview, video, details, last_frame], _js="videoStudioSubmit")
        rgo.click(_guarded(refine), inputs=[last_frame, rquality, rstrength, smooth, upscale, int8, attention, preset],
                  outputs=[status, preview, video, details, last_frame])
        stop.click(cancel, outputs=[status], queue=False)

        def refresh_styles():
            try:
                from modules import shared
                shared.prompt_styles.reload()
            except Exception:
                pass
            return gr.update(choices=prompting.style_names())

        def write_styles(text, chosen):
            styled, _ = prompting.apply_styles(text or "", "", chosen or [])
            return styled, []

        def save_style_click(name, text, neg):
            try:
                saved = prompting.save_style(name, text, neg)
            except Exception as e:  # noqa: BLE001 - shown on the page
                return gr.update(), status_html("error", error=str(e))
            return gr.update(choices=prompting.style_names()), status_html("done", 1.0, note=f"style saved: {saved}")

        styles_refresh.click(refresh_styles, outputs=[styles], queue=False)
        styles_apply.click(write_styles, inputs=[prompt, styles], outputs=[prompt, styles], queue=False)
        style_save.click(save_style_click, inputs=[style_name, prompt, negative], outputs=[styles, status], queue=False)
        sgo.click(_guarded_sound(add_sound),
                  inputs=[last_frame, fx_on, fx_prompt, fx_negative, fx_db, speech_mode, speech_text, speech_lang,
                          speech_voice, rec_file, rec_mic, speech_start, speech_exag, speech_db, lips, sound_seed],
                  outputs=[status, video, details, last_frame])
        show.click(show_last, outputs=[status, video, details, last_frame], queue=False)
        # The number of pictures is the one thing the buttons change, and what is shown follows it (a preset that sets
        # it shows the same). All of it runs in the page: Gradio skips an event that is still waiting for the server's
        # answer to its last run, so with server events a quick second click on ➕ was lost.
        picture_count.change(fn=None, _js="videoStudioPictureLayout", inputs=[picture_count],
                             outputs=pictures + picture_rows + [add_picture, remove_picture, reverse_pictures,
                                                                pictures_md, parts_box] + part_prompts + [seconds])
        add_picture.click(fn=None, _js="videoStudioAddPicture", inputs=[picture_count], outputs=[picture_count])
        remove_picture.click(fn=None, _js="videoStudioRemovePicture", inputs=[picture_count],
                             outputs=[picture_count] + pictures + part_prompts)
        reverse_pictures.click(fn=None, _js="videoStudioReversePictures", inputs=[picture_count] + pictures + part_prompts,
                               outputs=pictures + part_prompts)
        folder.click(open_folder, queue=False)
        # a new clip from the last frame: one picture, the others cleared
        cont.click(lambda p: [p if p and os.path.isfile(p) else None] + [None] * (MAX_PICTURES - 1) + [1],
                   inputs=[last_frame], outputs=pictures + [picture_count], _js="videoStudioToImageTab", queue=False)
        lora_refresh.click(lambda name: (gr.update(choices=lora_choices(name)), gr.update(choices=lora_choices(name))), inputs=[preset],
                           outputs=[lora1, lora2], queue=False)

        def preset_defaults(name):
            pr = models.PRESETS[name]
            is_ltx = ltx.is_ltx(name)
            qualities = ltx.QUALITY if is_ltx else QUALITY
            return (gr.update(value=pr['steps'], interactive=not is_ltx),
                    gr.update(value=pr['shift'], interactive=not is_ltx),
                    gr.update(value=pr['cfg'], interactive=not is_ltx), models_markdown(),
                    gr.update(choices=list(qualities), value=ltx.default_quality(name) if is_ltx else DEFAULT_QUALITY),
                    gr.update(value='euler_ancestral' if is_ltx else 'euler', interactive=not is_ltx),
                    gr.update(visible=is_ltx), gr.update(visible=not is_ltx),
                    gr.update(choices=lora_choices(name), value=NO_LORA), gr.update(choices=lora_choices(name), value=NO_LORA),
                    gr.update(label='Smooth motion: 48 fps (FILM)' if is_ltx else 'Smooth motion: 32 fps (FILM)'),
                    gr.update(visible=not is_ltx), gr.update(visible=not is_ltx),
                    gr.update(choices=ingredient_methods(is_ltx), value=ING_AUTO, interactive=True),
                    gr.update(visible=True, value=ingredients_models_markdown(name)), gr.update(visible=True),
                    gr.update(choices=list(REFINE_QUALITY)[:1] if is_ltx else list(REFINE_QUALITY), value=next(iter(REFINE_QUALITY))),
                    gr.update(visible=not is_ltx), gr.update(value=0), gr.update(value=0))

        preset.change(preset_defaults, inputs=[preset], outputs=[steps, shift, cfg, models_md, quality, sampler,
                      native_audio, picture_controls, lora1, lora2, smooth, int8, attention,
                      ing_method, ing_models, ing_download, rquality, t2v_sampler, width, height], queue=False)
        refresh.click(models_markdown, outputs=[models_md], queue=False)
        refresh.click(ingredients_models_markdown, inputs=[preset], outputs=[ing_models], queue=False)

        def start_download(name):
            models.DOWNLOADS.start(models.missing(name))
            return models_markdown()

        download.click(start_download, inputs=[preset], outputs=[models_md], queue=False)
        rep_people.click(find_people, inputs=[rep_video, rep_start, rep_char],
                         outputs=[rep_marked, rep_person, rep_char_person, status])

        def start_replace_download():
            models.DOWNLOADS.start(models.replace_missing())
            return replace_models_markdown()

        rep_download.click(start_replace_download, outputs=[rep_models], queue=False)
        refresh.click(replace_models_markdown, outputs=[rep_models], queue=False)
        engine.click(lambda: models_markdown() + "\n\n" + runner.setup_engine(), outputs=[models_md])
        def setup_ltx_click():
            try:
                return runner.setup_ltx_engine() + '\n\n' + models_markdown()
            except Exception as error:
                return models_markdown() + '\n\nLTX setup failed: ' + html.escape(str(error))
        ltx_engine.click(setup_ltx_click, outputs=[models_md])
    return tab

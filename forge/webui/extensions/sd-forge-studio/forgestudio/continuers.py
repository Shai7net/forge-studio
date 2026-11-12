"""Continuing a clip with the tool that made it, so the continuation looks like the clip.

Video Studio (Wan) continues anything from its last frame, but an AnimateDiff or a Deforum clip continued by Wan
turns into another film: other model, other look. Here the clip's own recipe is found next to it and run again
from its last frame, with the new words:

- AnimateDiff writes each clip's frames into a folder of the same name, every PNG carrying the full generation
  settings (model, LoRAs, sampler, size, motion module, fps). The continuation is an img2img through Forge's API with
  AnimateDiff on: the last frame is the start picture (AnimateDiff's image-to-video fades it into the noise over the
  first frames) and also an IP-Adapter reference, so the look holds; the LoRAs of the clip stay with the new words.
  ControlNets that followed a driving video (pose, QR, canny) are left out: there is no driving video past the end.
- Deforum keeps `<timestring>_settings.txt` beside its video. The same settings run again through Deforum's API with
  the last frame as the init picture and the new words from frame 0.

Both run inside Forge's own queue (the API takes it), so they wait for anything Forge is doing.
"""

import base64
import glob
import json
import os
import re
import threading
import time
import urllib.request

ENGINES = ("auto", "wan", "animatediff", "deforum")
RE_PARAM = re.compile(r'\s*(\w[\w \-/]+):\s*("(?:\\.|[^\\"])+"|[^,]*)(?:,|$)')
RE_LORA = re.compile(r"<(?:lora|lyco):[^>]+>", re.I)
RE_TRIGGER = re.compile(r"<(?:lora|lyco):[^>]+>\s*,?\s*([A-Za-z][\w\-]{2,30}(?: [\w\-]{2,30})?)(?=\s*[,.\n]|$)", re.I)


# ---- what made a clip ----

def _without_loras(prompt):
    """The words of a prompt without its LoRA tags, and without the ", , ," the tags leave behind."""
    text = RE_LORA.sub("", prompt or "")
    return re.sub(r"\s*,(\s*,)+", ",", text).strip(" ,")


def parse_infotext(text):
    """A1111/Forge generation parameters -> {"Prompt", "Negative prompt", "Steps", ...} (values as strings)."""
    lines = (text or "").strip().split("\n")
    *lines, last = lines if len(lines) > 1 else (lines + [""])
    if len(RE_PARAM.findall(last)) < 3:  # no settings line: all of it is the prompt
        lines.append(last)
        last = ""
    prompt, negative, in_negative = [], [], False
    for line in lines:
        if line.startswith("Negative prompt:"):
            in_negative = True
            line = line[len("Negative prompt:"):].strip()
        (negative if in_negative else prompt).append(line)
    out = {"Prompt": "\n".join(prompt).strip(), "Negative prompt": "\n".join(negative).strip()}
    for key, value in RE_PARAM.findall(last):
        value = value.strip()
        if len(value) > 1 and value[0] == '"' and value[-1] == '"':
            value = value[1:-1].replace('\\"', '"')
        out[key.strip()] = value
    return out


def parse_inner(text):
    """'model: x, video_length: 170, fps: 15' (AnimateDiff's, ControlNet's quoted settings) -> dict."""
    out = {}
    for part in (text or "").split(","):
        key, sep, value = part.partition(":")
        if sep:
            out[key.strip()] = value.strip()
    return out


def _animatediff_infotext(path):
    # a continuation made here is "<clip>-joined.mp4" / "...-sound.mp4"; its frames folder is "<clip>"
    stem = re.sub(r"(-joined|-sound)+$", "", os.path.splitext(path)[0])
    for png in sorted(glob.glob(os.path.join(stem, "*.png")))[:1] + [stem + ".png"]:
        if os.path.isfile(png):
            from PIL import Image
            with Image.open(png) as im:
                text = im.info.get("parameters")
            if text and "AnimateDiff:" in text:
                return text
    gif = stem + ".gif"
    if os.path.isfile(gif):
        from PIL import Image
        with Image.open(gif) as im:
            text = im.info.get("comment")
        if isinstance(text, bytes):
            text = text.decode("utf-8", "replace")
        if text and "AnimateDiff:" in text:
            return text
    return None


def _deforum_settings(path):
    folder = os.path.dirname(path)
    stem = re.sub(r"(-joined|-sound)+$", "", os.path.splitext(os.path.basename(path))[0])
    for candidate in [os.path.join(folder, stem + "_settings.txt")] + sorted(glob.glob(os.path.join(folder, "*_settings.txt"))):
        if os.path.isfile(candidate):
            try:
                with open(candidate, encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, ValueError):
                continue
            if "animation_mode" in data and "prompts" in data:
                return candidate
    return None


def recipe(path, made_with=None):
    """{"engine": wan|animatediff|deforum, "label", "prompt", ...} for a clip file."""
    if made_with:
        if made_with.get("studio_recipe"):
            return made_with["studio_recipe"]
        return {"engine": "wan", "label": "Wan (Video Studio)", "prompt": made_with.get("prompt", "")}
    text = _animatediff_infotext(path)
    if text:
        info = parse_infotext(text)
        ad = parse_inner(info.get("AnimateDiff", ""))
        return {"engine": "animatediff", "label": f"AnimateDiff ({info.get('Model', '?')}, {ad.get('model', '?')})",
                "prompt": _without_loras(info["Prompt"]), "infotext": text}
    settings = _deforum_settings(path)
    if settings:
        with open(settings, encoding="utf-8") as f:
            data = json.load(f)
        prompts = data.get("prompts") or {}
        first = prompts.get("0") or next(iter(prompts.values()), "") if isinstance(prompts, dict) else ""
        return {"engine": "deforum", "label": f"Deforum ({data.get('animation_mode', '?')})",
                "prompt": str(first).split("--neg")[0].strip(), "settings": settings}
    return {"engine": "wan", "label": "Wan (Video Studio)", "prompt": ""}


# ---- Forge's own API ----

def _base():
    try:
        from modules import shared
        port = getattr(shared.cmd_opts, "port", None) or 7860
    except Exception:
        port = 7860
    return f"http://127.0.0.1:{port}"


def _call(route, payload=None, method=None, timeout=3600):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(_base() + route, data=data, method=method or ("POST" if data else "GET"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8") or "null")


def _b64(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


# ---- AnimateDiff ----

def _upscaler(names):
    for want in ("RTX", "R-ESRGAN 4x+", "ESRGAN"):
        hit = next((n for n in names if want.lower() in n.lower()), None)
        if hit:
            return hit
    return next((n for n in names if n not in ("None", "Lanczos", "Nearest")), "None")


def animatediff_payload(infotext, frame, prompt, seconds, seed, out_size=None, upscalers=(), end_frame=None):
    """The img2img request that continues an AnimateDiff clip from `frame` (a PNG path); with `end_frame` it also
    arrives at that picture (AnimateDiff's image-to-video fades the first picture out over the first frames and the
    last one in over the last frames): a join between two clips, frame to frame."""
    info = parse_infotext(infotext)
    ad = parse_inner(info.get("AnimateDiff", ""))
    fps = int(float(ad.get("fps", 8) or 8))
    width, _, height = info.get("Size", "512x512").partition("x")
    width, height = int(width), int(height)
    # the LoRAs stay, with the trigger word written right after a tag ("<lora:glasssculpture_v8:1>, glasssculpture")
    loras = ", ".join(RE_LORA.findall(info["Prompt"]) + RE_TRIGGER.findall(info["Prompt"]))
    words = (prompt or "").strip() or _without_loras(info["Prompt"])
    upscale_by = 1.0
    if out_size and out_size[0] > width * 1.2:
        upscale_by = round(out_size[0] / width, 2)
    units = []
    for i in range(8):
        cn = info.get(f"ControlNet {i}")
        if not cn:
            continue
        unit = parse_inner(cn)
        if "IPAdapter" in unit.get("Module", "") or "ip-adapter" in unit.get("Model", "").lower():
            units.append({"enabled": True, "module": unit["Module"], "model": unit["Model"], "weight": 0.8,
                          "image": _b64(frame), "resize_mode": "Crop and Resize", "control_mode": "Balanced",
                          "guidance_start": 0.0, "guidance_end": 1.0, "pixel_perfect": False})
    payload = {
        "prompt": words + (", " + loras if loras else ""),
        "negative_prompt": info.get("Negative prompt", ""),
        "init_images": [_b64(frame)], "denoising_strength": 0.75, "resize_mode": 1,
        "steps": int(info.get("Steps", 20)), "sampler_name": info.get("Sampler", "Euler a"),
        "cfg_scale": float(info.get("CFG scale", 7)), "seed": int(seed),
        "width": width, "height": height, "batch_size": 1, "n_iter": 1,
        "override_settings": {k: v for k, v in (("sd_model_checkpoint", info.get("Model")),
                                                ("sd_vae", info.get("VAE"))) if v},
        "override_settings_restore_afterwards": True,
        "alwayson_scripts": {"AnimateDiff": {"args": [{
            "enable": True, "model": ad.get("model", "mm_sd15_v3.safetensors"),
            "video_length": max(8, int(round(float(seconds) * fps))), "fps": fps,
            "batch_size": int(ad.get("batch_size", 16)), "stride": int(ad.get("stride", 1)),
            "overlap": int(ad.get("overlap", -1)), "closed_loop": "N", "format": ["MP4", "PNG"],
            "interp": ad.get("interp", "Off"), "interp_x": int(ad.get("interp_x", 10)),
            "latent_power": 1, "latent_scale": 32,
            "last_frame": _b64(end_frame) if end_frame else None, "latent_power_last": 1, "latent_scale_last": 32,
            "upscaler": _upscaler(upscalers) if upscale_by > 1 else "None", "upscale_by": upscale_by}]}},
    }
    if units:
        payload["alwayson_scripts"]["ControlNet"] = {"args": units}
    return payload


def _animatediff_folders():
    try:
        from modules import shared
        roots = [shared.opts.outdir_samples or shared.opts.outdir_img2img_samples, shared.opts.outdir_img2img_samples]
    except Exception:
        roots = []
    return [os.path.join(r, "AnimateDiff") for r in roots if r]


def run_animatediff(source, frame, prompt, seconds, seed, progress, end_frame=None):
    from . import media
    infotext = _animatediff_infotext(source)
    if not infotext:
        raise ValueError("AnimateDiff's settings for this clip were not found (its frames folder or GIF)")
    info = media.probe(source)
    try:
        upscalers = [u["name"] for u in _call("/sdapi/v1/upscalers", timeout=30)]
    except Exception:
        upscalers = []
    payload = animatediff_payload(infotext, frame, prompt, seconds, seed, (info.get("width"), info.get("height")), upscalers,
                                  end_frame)
    started = time.time()
    done = {}

    def post():
        try:
            done["result"] = _call("/sdapi/v1/img2img", payload, timeout=7200)
        except Exception as e:  # noqa: BLE001 - reported below
            done["error"] = e
    worker = threading.Thread(target=post, daemon=True)
    worker.start()
    while worker.is_alive():
        worker.join(2.0)
        try:
            p = _call("/sdapi/v1/progress?skip_current_image=true", timeout=10)
            state = p.get("state") or {}
            progress("sampling", p.get("progress") or 0.0,
                     f"AnimateDiff: step {state.get('sampling_step', 0)}/{state.get('sampling_steps', 0)}")
        except Exception:
            pass
    if "error" in done:
        raise RuntimeError(f"AnimateDiff failed: {done['error']}")
    made = []
    for folder in _animatediff_folders():
        made += [p for p in glob.glob(os.path.join(folder, "*", "*.mp4")) if os.path.getmtime(p) >= started - 1]
    if not made:
        raise RuntimeError("AnimateDiff made no video (is MP4 among its formats?)")
    return os.path.abspath(max(made, key=os.path.getmtime))


def interrupt():
    try:
        _call("/sdapi/v1/interrupt", {}, timeout=10)
    except Exception:
        pass


# ---- Deforum ----

def deforum_settings(settings_path, frame, prompt, seconds, seed):
    with open(settings_path, encoding="utf-8") as f:
        data = json.load(f)
    old = data.get("prompts") or {}
    first = str(old.get("0") or next(iter(old.values()), "")) if isinstance(old, dict) else ""
    negative = first.split("--neg", 1)[1].strip() if "--neg" in first else ""
    words = (prompt or "").strip() or first.split("--neg")[0].strip()
    fps = float(data.get("fps") or 15)
    data.update({
        "prompts": {"0": words + (f" --neg {negative}" if negative else "")},
        "use_init": True, "init_image": frame, "use_mask": False,
        "max_frames": max(2, int(round(float(seconds) * fps))),
        "seed": int(seed), "batch_name": "Continue_{timestring}", "resume_from_timestring": False,
        "motion_preview_mode": False,
    })
    if data.get("animation_mode") == "Video Input":  # the input video ends where the clip ends
        data["animation_mode"] = "3D"
    return data


RUNNING = {}  # Deforum job id of the continuation running now (for Cancel)


def run_deforum(source, frame, prompt, seconds, seed, progress):
    settings_path = _deforum_settings(source)
    if not settings_path:
        raise ValueError("Deforum's settings for this clip were not found (<timestring>_settings.txt beside it)")
    settings = deforum_settings(settings_path, frame, prompt, seconds, seed)
    answer = _call("/deforum_api/batches", {"deforum_settings": settings}, timeout=60)
    job_id = (answer.get("job_ids") or [None])[0]
    if not job_id:
        raise RuntimeError(f"Deforum did not take the job: {answer}")
    RUNNING["deforum"] = job_id
    try:
        while True:
            time.sleep(2.0)
            status = _call(f"/deforum_api/jobs/{job_id}", timeout=30)
            state, phase = status.get("status"), status.get("phase")
            progress("sampling", status.get("phase_progress") or 0.0, f"Deforum: {str(phase).lower().replace('_', ' ')}")
            if state == "SUCCEEDED":
                video = os.path.join(status.get("outdir") or "", (status.get("timestring") or "") + ".mp4")
                if not os.path.isfile(video):
                    found = sorted(glob.glob(os.path.join(status.get("outdir") or "", "*.mp4")), key=os.path.getmtime)
                    video = found[-1] if found else None
                if not video:
                    raise RuntimeError("Deforum finished but no video was found in " + str(status.get("outdir")))
                return video
            if state in ("FAILED", "CANCELLED"):
                raise RuntimeError("cancelled" if state == "CANCELLED" else f"Deforum failed: {status.get('message')}")
    finally:
        RUNNING.pop("deforum", None)


def cancel_running():
    job_id = RUNNING.get("deforum")
    if job_id:
        try:
            _call(f"/deforum_api/jobs/{job_id}", method="DELETE", timeout=10)
        except Exception:
            pass
    interrupt()


# ---- the join ----

SOFT_START = 0.5


def soften_start(video, frame, seconds=SOFT_START):
    return soften(video, frame, None, seconds)


def soften(video, first=None, last=None, seconds=SOFT_START):
    """The made clip's first `seconds` fade in from `first` (the clip before's last frame) and its last `seconds` fade
    out into `last` (the next clip's first frame). AnimateDiff and Deforum draw even their first and last frames
    again (Wan keeps them exact), so a cut showed a jump in colour and light (2026-11-02, the AnimateDiff
    continuation of his psychedelic clip). Writes `<video>-joined.mp4` (same length, frame rate and size) and returns
    it; the video as it was when ffmpeg fails."""
    import subprocess
    from . import library, media
    info = media.probe(video)
    fps, width, height, duration = info.get("fps") or 15, info.get("width"), info.get("height"), info.get("duration")
    if not (width and height and duration) or duration < seconds * 3 or not (first or last):
        return video
    out = os.path.splitext(video)[0] + "-joined.mp4"
    cmd = [library.ffmpeg(), "-v", "error", "-y", "-i", video]
    still = (f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1,"
             f"trim=duration={seconds + 0.1:.3f},setpts=PTS-STARTPTS,settb=AVTB,fps={fps},format=yuv420p")
    # both inputs on one timebase, then one constant frame rate (xfade needs both)
    graph, current, n = [f"[0:v]setsar=1,settb=AVTB,fps={fps},format=yuv420p[v0]"], "v0", 1
    if first:
        cmd += ["-loop", "1", "-t", f"{seconds + 0.2:.3f}", "-i", first]
        graph += [f"[{n}:v]{still}[s{n}]", f"[s{n}][{current}]xfade=transition=fade:duration={seconds:.3f}:offset=0[v{n}]"]
        current, n = f"v{n}", n + 1
    if last:
        cmd += ["-loop", "1", "-t", f"{seconds + 0.2:.3f}", "-i", last]
        graph += [f"[{n}:v]{still}[s{n}]",
                  f"[{current}]trim=duration={duration:.3f}[t{n}]",
                  f"[t{n}][s{n}]xfade=transition=fade:duration={seconds:.3f}:offset={duration - seconds:.3f},"
                  f"trim=duration={duration:.3f}[v{n}]"]
        current = f"v{n}"
    cmd += ["-filter_complex", ";".join(graph), "-map", f"[{current}]", "-map", "0:a?", "-c:v", "libx264", "-crf", "16",
            "-preset", "fast", "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart", out]
    r = subprocess.run(cmd, capture_output=True, timeout=600, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return out if r.returncode == 0 and os.path.isfile(out) else video

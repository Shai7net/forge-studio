"""The look pass: a piece of a recorded film, drawn again by AnimateDiff.

The recorder's picture is exact in its shapes, places, camera and timing, and plain in its surfaces; a video
model is the other way round. So the film's own frames give the new video its structure and one picture
gives it its look:

  * structure: ControlNet (edges of every frame), **only in the first part of the steps**. After that the
    model is free to make surfaces, light and detail of its own;
  * look: a picture through IP-Adapter, all the way through;
  * and, when asked (`keep` < 1), the frames themselves are what is drawn on (img2img): the nearer `keep`
    is to 0, the more of the film's own colours and people stay.

(The idea of early structure and a lasting look is CodeVideoModel's, github.com/code-video-model. Its video
model weighs 210 GB; this is the same idea with what this Forge has: AnimateDiff, ControlNet, IP-Adapter.)

    job "look":  video, start, seconds, fps, width, height, prompt, negative, look (a picture), keep,
                 structure {kind, weight, end}, interp (2: FILM doubles the frames), upscale (and upscaler),
                 out (where to write; otherwise beside the film), beside (false: no side-by-side video)
    result:      the piece drawn again, as long as the piece was, and (unless told not to) film | new
"""
import os
import shutil
import subprocess
import time

from PIL import Image

from . import export, runner, store
from .runner import JobError


def _model(*words):
    """A ControlNet model of this Forge whose name has all the words (the shortest such name)."""
    try:
        from lib_controlnet import global_state
        names = [n for n in global_state.get_all_controlnet_names() if all(w in n.lower() for w in words)]
    except Exception:
        return None
    return sorted(names, key=len)[0] if names else None


def _motion(wanted):
    """The motion module asked for, or the LCM one, or the newest plain one."""
    folder = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "sd-forge-animatediff", "model")
    try:
        names = [n for n in os.listdir(folder) if n.lower().endswith((".ckpt", ".safetensors"))]
    except OSError:
        names = []
    if wanted and wanted in names:
        return wanted
    for words in (("lcmmotion",), ("lcm", "sd15"), ("sd15", "v3"), ("sd15",)):
        found = [n for n in names if all(w in n.lower() for w in words)]
        if found:
            return sorted(found)[-1]
    return None


def _frames_of(video, start, seconds, fps, width, height, folder):
    """The piece of the film as numbered pictures of the asked size."""
    os.makedirs(folder, exist_ok=True)
    command = [export.ffmpeg(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-ss", f"{start:.3f}", "-t", f"{seconds:.3f}", "-i", video,
               "-vf", f"fps={fps},scale={width}:{height}:flags=lanczos", "-start_number", "0", os.path.join(folder, "%05d.png")]
    done = subprocess.run(command, capture_output=True, stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    names = sorted(n for n in os.listdir(folder) if n.endswith(".png"))
    if done.returncode != 0 or len(names) < 2:
        raise JobError("the piece of the film could not be read: " + done.stderr.decode("utf8", "replace")[-300:])
    return [os.path.join(folder, n) for n in names]


def _write(frames, path, fps, beside=None):
    """Pictures -> a video; with `beside` (as many pictures), each frame is the two side by side."""
    width, height = frames[0].size
    wide = width * (2 if beside else 1)
    command = [export.ffmpeg(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{wide}x{height}", "-r", f"{fps:g}", "-i", "-",
               "-an", "-vf", "format=yuv420p", "-c:v", "libx264", "-crf", "15", "-preset", "medium", "-movflags", "+faststart", path]
    writer = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        for index, frame in enumerate(frames):
            picture = frame.convert("RGB")
            if beside:
                both = Image.new("RGB", (wide, height))
                both.paste(beside[min(index, len(beside) - 1)].convert("RGB").resize((width, height), Image.LANCZOS), (0, 0))
                both.paste(picture, (width, 0))
                picture = both
            writer.stdin.write(picture.tobytes())
    finally:
        writer.stdin.close()
    if writer.wait() != 0:
        raise JobError("the video could not be written: " + writer.stderr.read().decode("utf8", "replace")[-300:])


@runner.handler("look")
def make_look(job):
    from modules import paths
    p = job.params
    number = lambda key, default, low, high, whole=False: (lambda v: int(round(v)) if whole else v)(
        min(max(float(p.get(key) if p.get(key) is not None else default), low), high))
    video = str(p.get("video") or "")
    if not os.path.isabs(video):
        video = os.path.join(store.exports_dir(), video)
    if not os.path.isfile(video):
        raise JobError("there is no such film: " + video)
    start, seconds = number("start", 0, 0, 1e6), number("seconds", 4, 0.3, 120)
    fps = number("fps", 12, 1, 30, whole=True)
    width, height = number("width", 768, 256, 1920, whole=True) // 8 * 8, number("height", 432, 256, 1920, whole=True) // 8 * 8
    keep = number("keep", 1.0, 0.05, 1.0)                   # 1: drawn from nothing; less: drawn on the film's own frames
    motion = _motion(p.get("motion"))
    if not motion:
        raise JobError("AnimateDiff has no motion module (extensions/sd-forge-animatediff/model)")
    name = store.clean_name(p.get("name") or (os.path.splitext(os.path.basename(video))[0] + " look"), "name")
    work = os.path.join(paths.data_path, "tmp", "sim360-look", f"job-{time.time_ns()}")
    try:
        job.stage = "reading the film"
        files = _frames_of(video, start, seconds, fps, width, height, os.path.join(work, "frames"))
        originals = [Image.open(f).convert("RGB") for f in files]
        count = len(originals)

        lcm = "lcm" in motion.lower() or bool(p.get("lcm"))
        prompt = str(p.get("prompt") or "").strip()
        if lcm and "<lora:LCM" not in prompt and os.path.isfile(os.path.join(paths.models_path, "Lora", "LCM_SD15.safetensors")):
            prompt += " <lora:LCM_SD15:1>"
        settings = dict(prompt=prompt, negative_prompt=str(p.get("negative") or ""), width=width, height=height,
                        steps=number("steps", 8 if lcm else 20, 1, 60, whole=True), cfg_scale=number("cfg", 2.0 if lcm else 7.0, 1, 20),
                        sampler_name=p.get("sampler") or ("LCM" if lcm else "Euler a"), seed=int(p.get("seed") if p.get("seed") is not None else -1),
                        batch_size=count)
        if p.get("checkpoint"):
            settings["checkpoint"] = p["checkpoint"]
        units = []
        structure = p.get("structure") if isinstance(p.get("structure"), dict) else {}
        kind = str(structure.get("kind") or "canny")
        model = _model(kind) if float(structure.get("weight", 1.0)) > 0 else None
        if model:
            units.append({"enabled": True, "module": structure.get("module") or kind, "model": model,
                          "weight": float(structure.get("weight", 1.0)), "guidance_start": 0.0, "guidance_end": float(structure.get("end", 0.4)),
                          "resize_mode": "Crop and Resize", "pixel_perfect": True, "control_mode": "Balanced", "processor_res": 512,
                          "threshold_a": float(structure.get("low", 60)), "threshold_b": float(structure.get("high", 140)), "save_detected_map": False})
        look = str(p.get("look") or "")
        adapter = _model("ip-adapter", "plus", "sd15") or _model("ipadapter", "plus", "sd15")
        if look and adapter and float(p.get("look_weight", 0.6)) > 0:
            if not os.path.isfile(look):
                raise JobError("the picture of the look is not there: " + look)
            units.append({"enabled": True, "module": "CLIP-ViT-H (IPAdapter)", "model": adapter, "image": runner.encode_image(Image.open(look).convert("RGB")),
                          "weight": float(p.get("look_weight", 0.6)), "guidance_start": 0.0, "guidance_end": 1.0,
                          "resize_mode": "Crop and Resize", "pixel_perfect": False, "control_mode": "Balanced", "save_detected_map": False})
        moving = {"enable": True, "model": motion, "video_length": count, "fps": fps, "loop_number": 0, "closed_loop": "N",
                  "batch_size": number("context", 16, 4, 32, whole=True), "stride": 1, "overlap": number("overlap", 4, 0, 16, whole=True),
                  "format": ["Frame"], "interp": "Off"}
        if int(p.get("interp") or 0) >= 2:                  # frames in between, by FILM: twice as smooth for the same drawing
            moving.update(interp="FILM", interp_x=int(p["interp"]))
        alwayson = {"AnimateDiff": [moving]}
        if units:
            alwayson["ControlNet"] = units
        started = time.time()
        if keep < 1:
            settings.update(denoising_strength=keep, resize_mode=0)
            # (every frame is drawn on its own picture: without this AnimateDiff takes the first picture as the
            # video's start and lets the frames after it sink into noise)
            moving.update(latent_power=1, latent_scale=1000000)
            processed = runner.draw(job, settings, alwayson=alwayson, init_image=originals, outdir=os.path.join(work, "out"))
        else:
            moving["video_path"] = os.path.join(work, "frames")        # (ControlNet reads its frames from here)
            processed = runner.draw(job, settings, alwayson=alwayson, outdir=os.path.join(work, "out"))
        frames = [f for f in runner.pictures(processed) if f.size == (width, height)]
        if len(frames) < 2:
            raise JobError("AnimateDiff returned no frames")
        drawn = round(time.time() - started, 1)
        rate = fps * len(frames) / count                    # (frames in between add frames, not time)
        scale = float(p.get("upscale") or 1)
        if scale > 1.01:
            job.stage = "enlarging"
            from modules import shared
            wanted = str(p.get("upscaler") or "RTX").lower()
            found = [u for u in shared.sd_upscalers if wanted in u.name.lower()] or [u for u in shared.sd_upscalers if u.name == "Lanczos"]
            if found:
                frames = [found[0].scaler.upscale(f.convert("RGB"), scale, found[0].data_path) for f in frames]
            size = (int(round(width * scale)) // 2 * 2, int(round(height * scale)) // 2 * 2)
            frames = [f if f.size == size else f.resize(size, Image.LANCZOS) for f in frames]
        job.stage = "video"
        folder = os.path.dirname(video)
        out = str(p.get("out") or "") or os.path.join(folder, name + ".mp4")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        _write(frames, out, rate)
        beside = None
        if p.get("beside", True):
            beside = os.path.splitext(out)[0] + " beside.mp4"
            _write([f.resize((width, height), Image.LANCZOS) for f in frames[::max(1, round(len(frames) / count))]][:count], beside, fps, beside=originals)
        return {"file": out, "beside": beside, "frames": len(frames), "fps": round(rate, 4), "width": frames[0].width, "height": frames[0].height, "seconds": round(time.time() - started, 1), "drawing": drawn,
                "seed": int(processed.seed), "motion": motion, "structure": model, "look": bool(look and adapter), "keep": keep}
    finally:
        shutil.rmtree(work, ignore_errors=True)

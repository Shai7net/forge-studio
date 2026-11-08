"""What the page can ask for: a new world, Hires. fix of a world, a character inside a view, an automatic
selection, a depth map, and taking in a file (an upload, or a panorama the page has painted into)."""

import os
import shutil
import subprocess

import numpy as np
from PIL import Image, PngImagePlugin

from . import depth, export, runner, segment, store
from .runner import JobError

IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
VIDEO_TYPES = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".gif"}
MAX_WIDTH = 8192            # wider panoramas are made smaller when they are taken in
THUMB_WIDTH = 384
FILL = {"fill": 0, "original": 1, "noise": 2, "nothing": 3}


def _number(value, default, low, high, whole=False):
    try:
        value = float(value)
    except (TypeError, ValueError):
        value = default
    value = min(max(value, low), high)
    return int(round(value)) if whole else value


def _side(value, default):
    return max(64, _number(value, default, 64, 8192, whole=True) // 8 * 8)


# --- a world's files ---------------------------------------------------------------------------------------------

def _save_png(image, path, text=None):
    info = PngImagePlugin.PngInfo()
    if text:
        info.add_text("parameters", text)
    image.save(path, format="PNG", pnginfo=info, compress_level=4)


def _finish_world(job, folder, number, picture, described):
    """The small picture and the depth map that every version of a world gets."""
    job.stage = "thumbnail"
    thumb = f"thumb-{number:04d}.jpg"
    small = picture.convert("RGB").resize((THUMB_WIDTH, max(2, round(THUMB_WIDTH * picture.height / picture.width))), Image.LANCZOS)
    small.save(os.path.join(folder, thumb), format="JPEG", quality=86)
    described["thumb"] = thumb
    described["depth"] = None
    job.stage = "depth"
    try:
        from modules.call_queue import queue_lock
        name = f"depth-{number:04d}.png"
        with queue_lock:
            if depth.measure(picture, os.path.join(folder, name)):
                described["depth"] = name
    except Exception as e:
        print(f"[Simulation 360] the depth map could not be made: {type(e).__name__}: {e}")
        described["depth_error"] = f"{type(e).__name__}: {e}"
    return described


def _write_video(frames, path, fps):
    """A world's video. Every frame is a key frame, so the page can jump to any frame at once."""
    width, height = frames[0].size
    width, height = width - width % 2, height - height % 2
    command = [export.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
               "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}", "-r", f"{fps:g}", "-i", "-", "-an",
               "-vf", "scale=flags=lanczos+accurate_rnd+full_chroma_int:out_color_matrix=bt709:out_range=tv,format=yuv420p,"
                      "setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv",
               "-c:v", "libx264", "-crf", "14", "-preset", "slow", "-g", "1", "-movflags", "+faststart", path]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        for frame in frames:
            process.stdin.write(np.asarray(frame.convert("RGB").crop((0, 0, width, height))).tobytes())
        process.stdin.close()
    except OSError:
        pass
    said = process.stderr.read().decode("utf8", "replace")
    if process.wait() != 0:
        raise JobError("the world's video could not be written: " + said[-400:])


def _video_facts(path):
    import cv2
    capture = cv2.VideoCapture(path)
    try:
        if not capture.isOpened():
            raise JobError("this video cannot be read")
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0) or 24.0
        frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        ok, first = capture.read()
        if not ok:
            raise JobError("this video has no frames")
        return width, height, fps, frames, Image.fromarray(first[:, :, ::-1].copy())
    finally:
        capture.release()


# --- a new world -------------------------------------------------------------------------------------------------

def _basic_settings(s):
    return {
        "prompt": str(s.get("prompt") or ""),
        "negative_prompt": str(s.get("negative") or ""),
        "steps": _number(s.get("steps"), 7, 1, 150, whole=True),
        "cfg_scale": _number(s.get("cfg"), 1.5, 0.0, 30.0),
        "sampler_name": s.get("sampler") or "LCM",
        "seed": _number(s.get("seed"), -1, -1, 2 ** 32 - 1, whole=True),
        "checkpoint": s.get("checkpoint") or None,
    }


@runner.handler("world")
def make_world(job):
    params = job.params
    project, world, s = params["project"], params["world"], params.get("settings") or {}
    folder = store.world_dir(project, world)
    settings = _basic_settings(s)
    settings["width"] = _side(s.get("width"), 1024)
    settings["height"] = _side(s.get("height"), 512)
    hires = s.get("hires") or {}
    if hires.get("enabled"):
        settings.update(enable_hr=True, hr_scale=_number(hires.get("scale"), 2.0, 1.0, 4.0),
                        hr_upscaler=hires.get("upscaler") or "Latent",
                        hr_second_pass_steps=_number(hires.get("steps"), 0, 0, 150, whole=True),
                        denoising_strength=_number(hires.get("denoise"), 0.5, 0.0, 1.0))
    alwayson = {}
    moving = s.get("animatediff") or {}
    temporary = os.path.join(folder, "_animatediff")
    if moving.get("enabled"):
        if not moving.get("model"):
            raise JobError("choose a motion module for AnimateDiff")
        length = _number(moving.get("frames"), 16, 2, 512, whole=True)
        alwayson["AnimateDiff"] = [{
            "enable": True, "model": moving["model"], "video_length": length,
            "fps": _number(moving.get("fps"), 8, 1, 60, whole=True), "loop_number": 0,
            "closed_loop": moving.get("closed_loop") or "R-P",
            "batch_size": _number(moving.get("context"), 16, 1, 32, whole=True),
            "stride": _number(moving.get("stride"), 1, 1, 32, whole=True),
            "overlap": _number(moving.get("overlap"), 4, -1, 16, whole=True),
            "format": ["MP4", "Frame"], "interp": moving.get("interp") or "Off",
            "interp_x": _number(moving.get("interp_x"), 10, 2, 20, whole=True),
        }]
    try:
        processed = runner.draw(job, settings, alwayson=alwayson, tile_x=s.get("tile_x", True),
                                tile_start=s.get("tile_start", 0), outdir=temporary if alwayson else None)
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
    frames = runner.pictures(processed)
    if not frames:
        raise JobError("Forge returned no picture")
    text = runner.infotext(processed)
    number = store.next_number(folder)
    described = {"number": number, "seed": int(processed.seed), "infotext": text,
                 "width": frames[0].width, "height": frames[0].height}
    if moving.get("enabled") and len(frames) > 1:
        job.stage = "video"
        asked = alwayson["AnimateDiff"][0]
        fps = asked["fps"] * len(frames) / asked["video_length"]     # frame interpolation adds frames, not time
        name = f"video-{number:04d}.mp4"
        _write_video(frames, os.path.join(folder, name), fps)
        poster = f"poster-{number:04d}.jpg"
        frames[0].convert("RGB").save(os.path.join(folder, poster), format="JPEG", quality=93)
        described.update(kind="video", file=name, poster=poster, frames=len(frames), fps=round(fps, 4),
                         width=frames[0].width - frames[0].width % 2, height=frames[0].height - frames[0].height % 2)
    else:
        name = f"pano-{number:04d}.png"
        _save_png(frames[0].convert("RGB"), os.path.join(folder, name), text)
        described.update(kind="image", file=name)
    return _finish_world(job, folder, number, frames[0], described)


# --- Hires. fix of a world the user already likes -----------------------------------------------------------------

def _upscaler(name):
    from modules import shared
    for upscaler in shared.sd_upscalers:
        if upscaler.name == name:
            return upscaler
    return None


def enlarge_ring(image, scale, upscaler_name):
    """Enlarges a panorama. The upscaler sees a little of the other side at each end, so the ends still meet."""
    borrowed = min(64, image.width // 4)
    pixels = np.asarray(image.convert("RGB"))
    wide = Image.fromarray(np.concatenate([pixels[:, -borrowed:], pixels, pixels[:, :borrowed]], axis=1))
    upscaler = _upscaler(upscaler_name)
    if upscaler is None or upscaler.name in ("None", "Lanczos", "Nearest") or upscaler.scaler is None:
        large = wide.resize((round(wide.width * scale), round(wide.height * scale)), Image.LANCZOS)
    else:
        large = upscaler.scaler.upscale(wide, scale, upscaler.data_path)
    cut = round(borrowed * large.width / wide.width)
    return large.crop((cut, 0, large.width - cut, large.height))


@runner.handler("hires")
def hires_world(job):
    params = job.params
    project, world, s = params["project"], params["world"], params.get("settings") or {}
    folder = store.world_dir(project, world)
    source = Image.open(store.resolve(project, f"worlds/{world}/{params['file']}")).convert("RGB")
    scale = _number(s.get("scale"), 2.0, 1.0, 4.0)
    width, height = _side(source.width * scale, 2048), _side(source.height * scale, 1024)
    job.stage = "enlarging"
    from modules.call_queue import queue_lock
    with queue_lock:
        large = enlarge_ring(source, scale, s.get("upscaler") or "Lanczos")
    if large.size != (width, height):
        large = large.resize((width, height), Image.LANCZOS)
    strength = _number(s.get("denoise"), 0.5, 0.0, 1.0)
    settings = _basic_settings(s)
    wanted = settings["steps"]
    if strength > 0:
        # as Hires. fix does it: all the steps asked for are run, on the end of a longer schedule
        settings["steps"] = min(150, max(wanted, int(round(wanted / max(strength, 0.05)))))
    settings.update(width=width, height=height, denoising_strength=strength, resize_mode=0)
    if strength <= 0:
        result, text, seed = large, params.get("infotext") or "", settings["seed"]
    else:
        processed = runner.draw(job, settings, init_image=large, tile_x=s.get("tile_x", True))
        frames = runner.pictures(processed)
        if not frames:
            raise JobError("Forge returned no picture")
        result, text, seed = frames[0].convert("RGB"), runner.infotext(processed), int(processed.seed)
    number = store.next_number(folder)
    name = f"pano-{number:04d}.png"
    _save_png(result, os.path.join(folder, name), text)
    described = {"number": number, "kind": "image", "file": name, "width": result.width, "height": result.height,
                 "seed": seed, "infotext": text}
    return _finish_world(job, folder, number, result, described)


# --- a character or an object, drawn into the view the user is looking at ----------------------------------------

def openpose_model():
    try:
        from lib_controlnet import global_state
        names = [n for n in global_state.get_all_controlnet_names() if "openpose" in n.lower()]
    except Exception:
        return None
    names.sort(key=lambda n: (0 if "v11" in n.lower() else 1, len(n)))
    return names[0] if names else None


@runner.handler("character")
def draw_character(job):
    params = job.params
    s = params.get("settings") or {}
    view = runner.decode_image(params.get("image")).convert("RGB")
    mask = runner.decode_image(params.get("mask")).convert("L").resize(view.size, Image.LANCZOS)
    settings = _basic_settings(s)
    settings["steps"] = _number(s.get("steps"), 8, 1, 150, whole=True)
    settings.update(
        width=view.width // 8 * 8, height=view.height // 8 * 8, resize_mode=0,
        denoising_strength=_number(s.get("denoise"), 0.9, 0.0, 1.0),
        inpainting_fill=FILL.get(s.get("fill"), 1), inpaint_full_res=False, inpainting_mask_invert=0,
        mask_blur=_number(s.get("mask_blur"), 8, 0, 64, whole=True),
    )
    alwayson = {}
    if params.get("pose"):
        model = s.get("control_model") or openpose_model()
        if not model:
            raise JobError("no OpenPose model was found in models/ControlNet")
        alwayson["ControlNet"] = [{
            "enabled": True, "module": "None", "model": model, "image": params["pose"],
            "weight": _number(s.get("control_weight"), 1.0, 0.0, 2.0),
            "guidance_start": 0.0, "guidance_end": _number(s.get("control_end"), 1.0, 0.0, 1.0),
            "resize_mode": "Crop and Resize", "pixel_perfect": True, "control_mode": "Balanced",
            "processor_res": 512, "save_detected_map": False,
        }]
    processed = runner.draw(job, settings, alwayson=alwayson, init_image=view, mask=mask)
    frames = runner.pictures(processed)
    if not frames:
        raise JobError("Forge returned no picture")
    result = frames[0].convert("RGB")
    if result.size != view.size:
        result = result.resize(view.size, Image.LANCZOS)
    return {"image": runner.encode_image(result), "seed": int(processed.seed), "infotext": runner.infotext(processed)}


# --- automatic selection -----------------------------------------------------------------------------------------

@runner.handler("select")
def auto_select(job):
    params = job.params
    view = np.asarray(runner.decode_image(params.get("image")).convert("RGB"))
    points = [(float(p[0]), float(p[1]), bool(p[2]) if len(p) > 2 else True) for p in params.get("points") or []]
    if not points:
        raise JobError("click on the thing to select")
    job.stage = "looking"
    from modules.call_queue import queue_lock
    with queue_lock:
        shapes = segment.select(view, points, picture_key=params.get("key"))
    if not shapes:
        return {"count": 0}
    # up to three shapes travel as the three colours of one picture
    packed = np.zeros(view.shape[:2] + (3,), np.uint8)
    for n, (shape, _score) in enumerate(shapes[:3]):
        packed[..., n] = shape.astype(np.uint8) * 255
    best = max(range(min(3, len(shapes))), key=lambda n: shapes[n][1])
    return {"count": min(3, len(shapes)), "masks": runner.encode_image(Image.fromarray(packed)),
            "scores": [round(s, 3) for _m, s in shapes[:3]], "best": best}


# --- the depth map, again ----------------------------------------------------------------------------------------

@runner.handler("depth")
def measure_depth(job):
    params = job.params
    project, world = params["project"], params["world"]
    folder = store.world_dir(project, world)
    source = store.resolve(project, f"worlds/{world}/{params['file']}")
    picture = Image.open(source).convert("RGB")
    number = _number(params.get("number"), 1, 1, 9999, whole=True)
    return _finish_world(job, folder, number, picture, {"number": number})


# --- taking a file in --------------------------------------------------------------------------------------------

@runner.handler("import")
def take_in(job):
    """An uploaded picture or video becomes a world, or the next version of one. The page's own panorama
    (after a character was painted into it) arrives the same way."""
    params = job.params
    project, world, path = params["project"], params["world"], params["path"]
    folder = store.world_dir(project, world)
    extension = os.path.splitext(params.get("filename") or path)[1].lower()
    number = store.next_number(folder)
    try:
        if extension in VIDEO_TYPES:
            job.stage = "video"
            width, height, fps, frames, first = _video_facts(path)
            name = f"video-{number:04d}.mp4"
            scale = "scale='min(4096,iw)':-2:flags=lanczos," if width > 4096 else "scale=trunc(iw/2)*2:trunc(ih/2)*2,"
            # a key frame every 6 frames: the recorder jumps from frame to frame, and each jump decodes from
            # the key frame before it
            command = [export.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-i", path, "-an", "-map", "0:v:0",
                       "-vf", scale + "format=yuv420p", "-c:v", "libx264", "-crf", "16", "-preset", "medium",
                       "-g", "6", "-movflags", "+faststart", os.path.join(folder, name)]
            done = subprocess.run(command, capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if done.returncode != 0:
                raise JobError("this video cannot be converted: " + done.stderr.decode("utf8", "replace")[-400:])
            width, height, fps, frames, first = _video_facts(os.path.join(folder, name))
            enlarge = _number(params.get("enlarge"), 1.0, 1.0, 4.0)
            if enlarge > 1.01 and width * enlarge <= 4096:
                # A small video (AnimateDiff, Deforum) stretched around a whole sphere is coarse: every frame
                # is enlarged with the chosen upscaler, the two ends kept meeting.
                import cv2
                from modules.call_queue import queue_lock
                job.stage = "enlarging"
                capture, large = cv2.VideoCapture(os.path.join(folder, name)), []
                try:
                    while True:
                        ok, frame = capture.read()
                        if not ok:
                            break
                        if job.status == "cancelling":
                            raise JobError("cancelled")
                        with queue_lock:
                            large.append(enlarge_ring(Image.fromarray(frame[:, :, ::-1].copy()), enlarge, params.get("upscaler") or "Lanczos"))
                finally:
                    capture.release()
                if large:
                    if params.get("bounce") and len(large) > 2:
                        large = large + large[-2:0:-1]
                    _write_video(large, os.path.join(folder, name), fps)
                    width, height, fps, frames, first = _video_facts(os.path.join(folder, name))
            elif params.get("bounce") and frames > 2:
                # A video that is not a loop (a Deforum render) jumps when it starts again. Played forwards and
                # then backwards it never does.
                import cv2
                capture, forward = cv2.VideoCapture(os.path.join(folder, name)), []
                try:
                    while True:
                        ok, frame = capture.read()
                        if not ok:
                            break
                        forward.append(Image.fromarray(frame[:, :, ::-1].copy()))
                finally:
                    capture.release()
                if len(forward) > 2:
                    _write_video(forward + forward[-2:0:-1], os.path.join(folder, name), fps)
                    width, height, fps, frames, first = _video_facts(os.path.join(folder, name))
            poster = f"poster-{number:04d}.jpg"
            first.save(os.path.join(folder, poster), format="JPEG", quality=93)
            described = {"number": number, "kind": "video", "file": name, "poster": poster, "width": width,
                         "height": height, "frames": frames, "fps": round(fps, 4)}
            picture = first
        elif extension in IMAGE_TYPES or params.get("painted"):
            try:
                picture = Image.open(path)
                text = picture.info.get("parameters") if isinstance(picture.info, dict) else None
                picture = picture.convert("RGB")
            except Exception:
                raise JobError("this picture cannot be read")
            if picture.width > MAX_WIDTH:
                picture = picture.resize((MAX_WIDTH, round(MAX_WIDTH * picture.height / picture.width)), Image.LANCZOS)
            name = f"pano-{number:04d}.png"
            _save_png(picture, os.path.join(folder, name), params.get("infotext") or text)
            described = {"number": number, "kind": "image", "file": name, "width": picture.width, "height": picture.height}
            if text and not params.get("painted"):
                described["infotext"] = text        # a picture that Forge made says how it was made
        else:
            raise JobError(f"files of type {extension or '?'} cannot be used as a world")
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    return _finish_world(job, folder, number, picture, described)

"""Objects: a character, a thing or a video that stands in a world as its own piece.

An object is not painted into the panorama. It is a cut-out picture (or video) on its own surface inside the
world, with its own small depth map, so it has thickness and the world behind it is still there when the
camera moves around it. It can be moved, turned, resized, drawn again or deleted at any time.

    object-<id>-0002.png          the picture, cut out (the transparency is the cut; the colours under the
                                  transparent part are kept, so the object can be drawn again in its place)
    object-<id>-0002-depth.png    its shape: white is nearest to the viewer
    object-<id>-0002.mp4          a video object: the colours on the left half, the cut on the right half
"""

import os
import re
import subprocess

import numpy as np
from PIL import Image, PngImagePlugin

from . import depth, export, jobs, runner, segment, store
from .runner import JobError

MAX_SIDE = 1536             # pictures of objects are made no larger than this
VIDEO_HEIGHT = 720          # video objects are made no taller than this
VIDEO_FRAMES = 600          # and no longer than this


def _next(folder, object_id):
    highest = 0
    for name in os.listdir(folder):
        match = re.match(rf"^object-{re.escape(object_id)}-(\d+)\.", name)
        if match:
            highest = max(highest, int(match.group(1)))
    return highest + 1


def _tidy(mask):
    """Small stray pieces go, small holes close."""
    import cv2
    mask = mask.astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if count > 2:
        largest = stats[1:, cv2.CC_STAT_AREA].max()
        keep = np.zeros(count, bool)
        keep[1:] = stats[1:, cv2.CC_STAT_AREA] >= 0.03 * largest
        mask = keep[labels].astype(np.uint8)
    size = max(3, round(max(mask.shape) * 0.006) * 2 + 1)
    return cv2.morphologyEx(mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))).astype(bool)


def _inner_points(shape, count=3):
    """A few points deep inside a shape, for telling Segment Anything which thing is meant."""
    import cv2
    deep = cv2.distanceTransform(shape.astype(np.uint8), cv2.DIST_L2, 5)
    points = []
    for _ in range(count):
        y, x = np.unravel_index(deep.argmax(), deep.shape)
        if deep[y, x] < 3:
            break
        points.append((float(x), float(y)))
        cv2.circle(deep, (int(x), int(y)), int(max(8, deep[y, x] * 1.6)), 0, -1)
    return points


def _rembg(rgb):
    from rembg import new_session, remove
    global _session
    if _session is None:
        _session = new_session("u2net", providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
    return np.asarray(remove(Image.fromarray(rgb), session=_session, only_mask=True), dtype=np.uint8)


_session = None


def cut_out(rgb, asked, points=None):
    """The transparency of an object: a uint8 array, 255 on the object.
    `asked` (bool array, or None) is where the object was asked for; the cut never goes far outside it."""
    import cv2
    from modules.call_queue import queue_lock
    height, width = rgb.shape[:2]
    found = None
    with queue_lock:
        if asked is not None and asked.any() and segment.available():
            ys, xs = np.nonzero(asked)
            pad = 0.02 * max(width, height)
            box = [max(0, xs.min() - pad), max(0, ys.min() - pad), min(width - 1, xs.max() + pad), min(height - 1, ys.max() + pad)]
            try:
                found = segment.cut(rgb, box, points or _inner_points(asked))
            except Exception as e:
                print(f"[Simulation 360] Segment Anything could not cut the object out: {type(e).__name__}: {e}")
        if found is None or found.sum() < 64:
            try:
                found = _rembg(rgb) > 127
            except Exception as e:
                print(f"[Simulation 360] the background could not be removed: {type(e).__name__}: {e}")
                found = None
    if asked is not None and asked.any():
        reach = max(3, round(max(width, height) * 0.03) * 2 + 1)
        near = cv2.dilate(asked.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (reach, reach))).astype(bool)
        found = near & found if found is not None and (found & near).sum() >= 0.2 * asked.sum() else asked
    elif found is None or found.sum() < 64:
        found = np.ones((height, width), bool)
    found = _tidy(found)
    # a hair inside the outline, and a soft edge: no rim of background comes along
    core = cv2.erode(found.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
    return np.clip(cv2.GaussianBlur(core.astype(np.float32) * 255, (0, 0), 0.9), 0, 255).astype(np.uint8)


def _box(alpha):
    ys, xs = np.nonzero(alpha > 96)
    height, width = alpha.shape
    if not len(xs):
        return [0, 0, 1, 1]
    return [round(float(xs.min()) / width, 4), round(float(ys.min()) / height, 4),
            round(float(xs.max() + 1) / width, 4), round(float(ys.max() + 1) / height, 4)]


def _save(job, folder, object_id, rgb, alpha, text=None):
    """Writes an object's picture and its shape; returns what the page keeps about them."""
    from modules.call_queue import queue_lock
    number = _next(folder, object_id)
    name = f"object-{object_id}-{number:04d}.png"
    picture = Image.fromarray(np.dstack([rgb, alpha]), "RGBA")
    info = PngImagePlugin.PngInfo()
    if text:
        info.add_text("parameters", text)
    picture.save(os.path.join(folder, name), format="PNG", pnginfo=info, compress_level=4)
    described = {"file": name, "width": picture.width, "height": picture.height, "box": _box(alpha), "depth": None}
    job.stage = "depth"
    try:
        with queue_lock:
            if depth.available():
                shape = depth.relief(Image.fromarray(rgb), alpha)
                described["depth"] = f"object-{object_id}-{number:04d}-depth.png"
                Image.fromarray(shape, "L").save(os.path.join(folder, described["depth"]), format="PNG")
    except Exception as e:
        print(f"[Simulation 360] the object's shape could not be measured: {type(e).__name__}: {e}")
    return described


@runner.handler("object")
def draw_object(job):
    """Draws an object and lifts it off its background.
    A new object is drawn into the view the page sends (image, mask, and a pose for a figure). An object that
    exists is drawn again on its own picture ("file"), inside its own outline."""
    import cv2
    params = job.params
    project, world, object_id = params["project"], params["world"], store.clean_id(params["object"], "object")
    folder = store.world_dir(project, world)
    s = params.get("settings") or {}
    points = None
    if params.get("file"):
        old = Image.open(store.resolve(project, f"worlds/{world}/{params['file']}")).convert("RGBA")
        # "Hires. fix" for an object: its picture is enlarged first (the upscaler does the colours, the cut is
        # simply scaled) and then drawn again at the new size, which is where the new detail comes from
        enlarge = min(jobs._number(s.get("enlarge"), 1.0, 1.0, 4.0), MAX_SIDE / max(old.size))
        if enlarge > 1.01:
            job.stage = "enlarging"
            size = (round(old.width * enlarge / 8) * 8, round(old.height * enlarge / 8) * 8)
            upscaler = jobs._upscaler(s.get("upscaler") or "")
            colours = old.convert("RGB")
            if upscaler is not None and upscaler.scaler is not None and upscaler.name not in ("None", "Lanczos", "Nearest"):
                from modules.call_queue import queue_lock
                with queue_lock:
                    colours = upscaler.scaler.upscale(colours, enlarge, upscaler.data_path)
            colours = colours.resize(size, Image.LANCZOS)
            old = Image.merge("RGBA", (*colours.split(), old.getchannel("A").resize(size, Image.LANCZOS)))
        pixels = np.asarray(old)
        view = Image.fromarray(np.ascontiguousarray(pixels[..., :3]))
        asked = pixels[..., 3] > 127
        grow = max(3, round(max(old.size) * 0.02) * 2 + 1)
        mask = Image.fromarray(cv2.dilate(asked.astype(np.uint8) * 255, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (grow, grow))))
    else:
        view = runner.decode_image(params.get("image")).convert("RGB")
        mask = runner.decode_image(params.get("mask")).convert("L").resize(view.size, Image.LANCZOS)
        asked = np.asarray(mask) > 127
        if params.get("points"):
            points = [(float(p[0]) * view.width, float(p[1]) * view.height) for p in params["points"]]
            points = [p for p in points if 0 <= p[0] < view.width and 0 <= p[1] < view.height] or None
    settings = jobs._basic_settings(s)
    settings["steps"] = jobs._number(s.get("steps"), 8, 1, 150, whole=True)
    settings.update(
        width=view.width // 8 * 8, height=view.height // 8 * 8, resize_mode=0,
        denoising_strength=jobs._number(s.get("denoise"), 1.0, 0.0, 1.0),
        inpainting_fill=jobs.FILL.get(s.get("fill"), 1), inpaint_full_res=False, inpainting_mask_invert=0,
        mask_blur=jobs._number(s.get("mask_blur"), 8, 0, 64, whole=True),
    )
    alwayson = {}
    if params.get("pose"):
        model = s.get("control_model") or jobs.openpose_model()
        if not model:
            raise JobError("no OpenPose model was found in models/ControlNet")
        alwayson["ControlNet"] = [{
            "enabled": True, "module": "None", "model": model, "image": params["pose"],
            "weight": jobs._number(s.get("control_weight"), 1.0, 0.0, 2.0),
            "guidance_start": 0.0, "guidance_end": jobs._number(s.get("control_end"), 1.0, 0.0, 1.0),
            "resize_mode": "Crop and Resize", "pixel_perfect": True, "control_mode": "Balanced",
            "processor_res": 512, "save_detected_map": False,
        }]
    # A moving object: AnimateDiff draws it as a short video inside the same outline; what is around the
    # outline stays the still picture, and every frame is cut out on its own.
    moving = s.get("animatediff") or {}
    temporary = os.path.join(folder, f"_animatediff-{object_id}")
    if moving.get("enabled") and not params.get("file"):
        if not moving.get("model"):
            raise JobError("choose a motion module for AnimateDiff")
        alwayson["AnimateDiff"] = [{
            "enable": True, "model": moving["model"], "video_length": jobs._number(moving.get("frames"), 16, 2, 96, whole=True),
            "fps": jobs._number(moving.get("fps"), 8, 1, 60, whole=True), "loop_number": 0,
            "closed_loop": moving.get("closed_loop") or "R-P", "batch_size": jobs._number(moving.get("context"), 16, 1, 32, whole=True),
            "stride": 1, "overlap": jobs._number(moving.get("overlap"), 4, -1, 16, whole=True),
            "format": ["MP4", "Frame"], "interp": moving.get("interp") or "Off",
            "interp_x": jobs._number(moving.get("interp_x"), 3, 2, 20, whole=True),
        }]
    try:
        processed = runner.draw(job, settings, alwayson=alwayson, init_image=view, mask=mask,
                                outdir=temporary if "AnimateDiff" in alwayson else None)
    finally:
        import shutil
        shutil.rmtree(temporary, ignore_errors=True)
    frames = runner.pictures(processed)
    if not frames:
        raise JobError("Forge returned no picture")
    text = runner.infotext(processed)
    job.stage = "cutting out"
    if "AnimateDiff" in alwayson and len(frames) > 1:
        asked_frames = alwayson["AnimateDiff"][0]
        fps = asked_frames["fps"] * len(frames) / asked_frames["video_length"]      # smoothing adds frames, not time
        cuts, first = [], None
        for frame in frames:
            if job.status == "cancelling":
                raise JobError("cancelled")
            rgb = np.asarray(frame.convert("RGB").resize(view.size, Image.LANCZOS) if frame.size != view.size else frame.convert("RGB"))
            alpha = cut_out(rgb, asked, points)
            if (alpha > 127).sum() >= 64:
                points = _inner_points(alpha > 127)                 # the next frame is asked about where this one was
            cuts.append((rgb, alpha))
            first = first or cuts[0]
        number = _next(folder, object_id)
        name = f"object-{object_id}-{number:04d}.mp4"
        _write_packed(cuts, os.path.join(folder, name), fps)
        described = _save(job, folder, object_id, first[0], first[1], text)
        described.update(kind="video", poster=described["file"], file=name, frames=len(cuts), fps=round(fps, 4),
                         seed=int(processed.seed), infotext=text)
        return described
    result = frames[0].convert("RGB")
    if result.size != view.size:
        result = result.resize(view.size, Image.LANCZOS)
    rgb = np.asarray(result)
    alpha = cut_out(rgb, asked, points)
    described = _save(job, folder, object_id, rgb, alpha, text)
    described.update(kind="image", seed=int(processed.seed), infotext=text)
    return described


def _write_packed(cuts, path, fps):
    """A video object's file: every frame's colours on the left half and its cut on the right half."""
    height, width = cuts[0][0].shape[:2]
    width, height = width - width % 2, height - height % 2
    command = [export.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
               "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width * 2}x{height}", "-r", f"{fps:g}", "-i", "-", "-an",
               "-vf", "format=yuv420p", "-c:v", "libx264", "-crf", "14", "-preset", "medium", "-g", "1",
               "-movflags", "+faststart", path]
    encoder = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        for rgb, alpha in cuts:
            packed = np.concatenate([rgb[:height, :width], np.repeat(alpha[:height, :width, None], 3, axis=2)], axis=1)
            encoder.stdin.write(np.ascontiguousarray(packed).tobytes())
        encoder.stdin.close()
    except OSError:
        pass
    said = encoder.stderr.read().decode("utf8", "replace")
    if encoder.wait() != 0:
        raise JobError("the object's video could not be written: " + said[-300:])


@runner.handler("import_object")
def take_object_in(job):
    """An uploaded picture or video becomes an object. A picture that has its own transparency keeps it;
    otherwise the background is removed."""
    params = job.params
    project, world, object_id, path = params["project"], params["world"], store.clean_id(params["object"], "object"), params["path"]
    folder = store.world_dir(project, world)
    extension = os.path.splitext(params.get("filename") or path)[1].lower()
    try:
        if extension in jobs.VIDEO_TYPES:
            return _take_video_in(job, folder, object_id, path, bool(params.get("keep_background")), bool(params.get("packed")))
        if extension not in jobs.IMAGE_TYPES:
            raise JobError(f"files of type {extension or '?'} cannot be used as an object")
        try:
            picture = Image.open(path).convert("RGBA")
        except Exception:
            raise JobError("this picture cannot be read")
        if max(picture.size) > MAX_SIDE:
            k = MAX_SIDE / max(picture.size)
            picture = picture.resize((round(picture.width * k), round(picture.height * k)), Image.LANCZOS)
        pixels = np.asarray(picture)
        rgb, alpha = np.ascontiguousarray(pixels[..., :3]), pixels[..., 3]
        if alpha.min() > 250 and not params.get("keep_background"):     # no transparency of its own
            job.stage = "cutting out"
            alpha = cut_out(rgb, None)
        described = _save(job, folder, object_id, rgb, np.ascontiguousarray(alpha))
        described["kind"] = "image"
        return described
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def _take_video_in(job, folder, object_id, path, whole=False, packed=False):
    """`whole`: the video stays a whole rectangle (a screen, a painting); otherwise its background is removed.
    `packed`: the video already is an object's video, the colours on its left half and the cut on its right."""
    import cv2
    from modules.call_queue import queue_lock
    capture = cv2.VideoCapture(path)
    if not capture.isOpened():
        raise JobError("this video cannot be read")
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 0) or 24.0
    width, height = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)), int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if packed:
        width //= 2
    k = min(1.0, (MAX_SIDE if packed else VIDEO_HEIGHT) / max(height, 1))
    width, height = max(2, round(width * k / 2) * 2), max(2, round(height * k / 2) * 2)
    number = _next(folder, object_id)
    name = f"object-{object_id}-{number:04d}.mp4"
    command = [export.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
               "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width * 2}x{height}", "-r", f"{fps:g}", "-i", "-", "-an",
               "-vf", "format=yuv420p", "-c:v", "libx264", "-crf", "15", "-preset", "medium", "-g", "4",
               "-movflags", "+faststart", os.path.join(folder, name)]
    encoder = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    first = None
    count = 0
    job.stage = "cutting out"
    try:
        while count < VIDEO_FRAMES:
            ok, frame = capture.read()
            if not ok:
                break
            half = frame.shape[1] // 2
            rgb = cv2.resize(frame[:, :half if packed else frame.shape[1], ::-1], (width, height), interpolation=cv2.INTER_AREA)
            if packed:
                cut = cv2.resize(frame[:, half:half * 2, 1], (width, height), interpolation=cv2.INTER_AREA)
            elif whole:
                cut = np.full((height, width), 255, np.uint8)
            else:
                with queue_lock:
                    cut = _rembg(np.ascontiguousarray(rgb))
            if first is None:
                first = (np.ascontiguousarray(rgb), cut.copy())
            both = np.concatenate([rgb, np.repeat(cut[..., None], 3, axis=2)], axis=1)
            encoder.stdin.write(np.ascontiguousarray(both).tobytes())
            count += 1
            if job.status == "cancelling":
                raise JobError("cancelled")
    finally:
        capture.release()
        try:
            encoder.stdin.close()
        except OSError:
            pass
    said = encoder.stderr.read().decode("utf8", "replace")
    if encoder.wait() != 0 or first is None:
        raise JobError("this video cannot be converted: " + said[-300:])
    described = _save(job, folder, object_id, first[0], first[1])            # the first frame: its poster and its shape
    described.update(kind="video", poster=described["file"], file=name, frames=count, fps=round(fps, 4), width=width, height=height)
    return described

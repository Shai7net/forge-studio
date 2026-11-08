"""Editing a frame of a video: take it out, draw over it with Forge, and bring the change back into the video.

Three ways back: the edited frame as a still, the clip made again from it (generate.py), or the change carried
through the clip: what the edit changed (edited minus original, only where it changed) follows the picture along
the optical flow, frame by frame both ways from the edited frame, so a new shirt colour moves with the shirt.
Where the flow cannot be trusted (it does not come back to where it started) the change fades out instead of
smearing. The rest of every frame is left exactly as it was.
"""

import io
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid

import numpy as np

from . import generate, library, paths, projects

ID = re.compile(r"^[0-9a-f]{8,12}$")
tasks = {}


def _folder(name):
    folder = os.path.join(projects._folder(name), "frames")
    os.makedirs(folder, exist_ok=True)
    return folder


def path_of(name, fid):
    if not ID.match(fid or ""):
        raise ValueError("not a frame id")
    path = os.path.join(_folder(name), fid + ".png")
    if not os.path.isfile(path):
        raise KeyError(f"no frame {fid}")
    return path


def grab(name, web, seconds):
    """A frame of a project's file at `seconds`, kept as a PNG of its own."""
    from PIL import Image
    made = generate.frame(name, web, seconds)
    fid = uuid.uuid4().hex[:10]
    out = os.path.join(_folder(name), fid + ".png")
    os.replace(made, out)
    with Image.open(out) as im:
        w, h = im.size
    with open(os.path.join(_folder(name), fid + ".json"), "w", encoding="utf-8") as f:
        json.dump({"web": web, "seconds": seconds, "made": time.time()}, f)
    return {"id": fid, "width": w, "height": h}


def store(name, image, meta):
    fid = uuid.uuid4().hex[:10]
    image.save(os.path.join(_folder(name), fid + ".png"))
    with open(os.path.join(_folder(name), fid + ".json"), "w", encoding="utf-8") as f:
        json.dump(dict(meta, made=time.time()), f, ensure_ascii=False)
    return fid


# ---- background tasks (the page asks, then follows) ----

def _task(kind, fn, *args):
    tid = uuid.uuid4().hex[:10]
    tasks[tid] = {"id": tid, "kind": kind, "state": "running", "text": "", "progress": 0.0, "started": time.time()}

    def run():
        try:
            tasks[tid].update(fn(tasks[tid], *args) or {}, state="done")
        except Exception as e:  # shown in the page
            import traceback
            traceback.print_exc()
            tasks[tid].update(state="error", text=f"{type(e).__name__}: {e}")
        tasks[tid]["ended"] = time.time()
    threading.Thread(target=run, name=f"forge-studio-{kind}", daemon=True).start()
    return tasks[tid]


# ---- drawing over a frame with Forge ----

def _ui(label, default):
    try:
        with open(os.path.join(paths.WEBUI, "ui-config.json"), encoding="utf-8") as f:
            return json.load(f).get(f"img2img/{label}/value", default)
    except Exception:
        return default


def _work_size(w, h, area):
    scale = min(1.0, (area / float(w * h)) ** 0.5)
    return max(64, int(w * scale) // 8 * 8), max(64, int(h * scale) // 8 * 8)


def draw(name, fid, mask_png, prompt, negative="", strength=0.75, seed=-1):
    """Forge's img2img with the selected checkpoint: inside the painted mask (inpaint), or over the whole frame."""
    return _task("draw", _draw, name, path_of(name, fid), mask_png, prompt, negative, strength, seed)


def _draw(task, name, source, mask_png, prompt, negative, strength, seed):
    from PIL import Image, ImageFilter
    from modules import processing, shared
    from modules.call_queue import queue_lock
    image = Image.open(source).convert("RGB")
    W, H = image.size
    mask = None
    if mask_png:
        mask = Image.open(io.BytesIO(mask_png)).convert("L").resize((W, H), Image.NEAREST)
        if not mask.getbbox():
            mask = None
    flux = sys.modules.get("flux3.glue")
    task["text"] = "waiting for Forge to be free"
    with queue_lock:
        is_flux = bool(flux and flux.active())
        is_big = is_flux or bool(getattr(shared.sd_model, "is_sdxl", False))
        if is_flux:
            sampler, steps, cfg = "Euler", 16, 1.0
        else:
            sampler, steps, cfg = _ui("Sampling method", "DPM++ 2M Karras"), int(_ui("Sampling steps", 20)), float(_ui("CFG Scale", 7.0))
        w, h = _work_size(W, H, (1024 * 1024) if is_big else (768 * 768))
        task["text"] = "drawing with " + (getattr(shared.sd_model, "sd_checkpoint_info", None) and
                                          shared.sd_model.sd_checkpoint_info.name_for_extra or "the selected checkpoint")
        p = processing.StableDiffusionProcessingImg2Img(
            sd_model=shared.sd_model, init_images=[image], mask=mask, mask_blur=8 if mask else 4,
            inpainting_fill=1, inpaint_full_res=bool(mask), inpaint_full_res_padding=64,
            denoising_strength=float(strength), prompt=prompt or "", negative_prompt=negative or "",
            seed=int(seed) if seed is not None else -1, sampler_name=sampler, steps=steps, cfg_scale=cfg,
            width=w, height=h, do_not_save_samples=True, do_not_save_grid=True)
        p.scripts = None
        try:
            res = processing.process_images(p)
        finally:
            p.close()
    out = res.images[0].convert("RGB")
    if out.size != (W, H):
        out = out.resize((W, H), Image.LANCZOS)
    if mask is not None:  # only the painted area changes, with a soft edge
        soft = mask.filter(ImageFilter.GaussianBlur(6))
        out = Image.composite(out, image, soft)
    fid = store(name, out, {"from": os.path.basename(source)[:-4], "prompt": prompt, "strength": strength,
                            "seed": res.seed, "masked": mask is not None, "sampler": sampler, "steps": steps, "cfg": cfg})
    return {"frame": fid, "seed": res.seed, "text": f"{sampler}, {steps} steps, CFG {cfg:g}"}


# ---- carrying an edit through the clip ----

def carry(name, web, seconds, original_fid, edited_fid):
    return _task("carry", _carry, name, web, float(seconds), path_of(name, original_fid), path_of(name, edited_fid))


def imread(path):
    """cv2.imread, for paths with Hebrew in them too (on Windows cv2.imread fails silently on those)."""
    import cv2
    image = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"could not read the picture {os.path.basename(path)}")
    return image


def _decode(path):
    import cv2
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 16.0
    frames = []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        frames.append(frame)
    cap.release()
    return frames, fps


def change_of(original, edited, threshold=8.0):
    """(change, weight): what the edit added, and where (0..1, soft edged). Small differences are left out: an
    inpaint redraws its whole box a little, and only the part that really changed should travel."""
    import cv2
    diff = edited.astype(np.float32) - original.astype(np.float32)
    strong = (np.abs(diff).max(axis=2) > threshold).astype(np.uint8)
    strong = cv2.morphologyEx(strong, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    strong = cv2.dilate(strong, np.ones((9, 9), np.uint8))
    weight = cv2.GaussianBlur(strong.astype(np.float32), (0, 0), 4)
    return diff, np.clip(weight, 0, 1)


class Follow:
    """Optical flow between neighbouring frames (DIS, on a small grey copy) and a trust map from a there-and-back
    check."""

    def __init__(self, shape, width=640):
        import cv2
        self.cv2 = cv2
        h, w = shape[:2]
        self.scale = min(1.0, width / float(w))
        self.small = (max(16, int(w * self.scale)), max(16, int(h * self.scale)))
        self.dis = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM)
        self.full = (w, h)
        gy, gx = np.mgrid[0:h, 0:w].astype(np.float32)
        self.grid = (gx, gy)

    def _grey(self, frame):
        return self.cv2.cvtColor(self.cv2.resize(frame, self.small, interpolation=self.cv2.INTER_AREA), self.cv2.COLOR_BGR2GRAY)

    def maps(self, to_frame, from_frame):
        """For every pixel of `to_frame`, where it was in `from_frame` (full size), and how much to trust it."""
        cv2 = self.cv2
        a, b = self._grey(to_frame), self._grey(from_frame)
        back = self.dis.calc(a, b, None)       # to -> from
        forth = self.dis.calc(b, a, None)      # from -> to
        sh, sw = a.shape
        gy, gx = np.mgrid[0:sh, 0:sw].astype(np.float32)
        # follow `back`, then `forth` from there: a good flow comes home
        fx = cv2.remap(forth[..., 0], gx + back[..., 0], gy + back[..., 1], cv2.INTER_LINEAR)
        fy = cv2.remap(forth[..., 1], gx + back[..., 0], gy + back[..., 1], cv2.INTER_LINEAR)
        error = np.hypot(back[..., 0] + fx, back[..., 1] + fy)
        trust = np.clip(1.5 - error, 0, 1)  # under half a small pixel: full trust; past 1.5: none
        W, H = self.full
        flow = cv2.resize(back, (W, H), interpolation=cv2.INTER_LINEAR) / self.scale
        trust = cv2.resize(trust, (W, H), interpolation=cv2.INTER_LINEAR)
        return self.grid[0] + flow[..., 0], self.grid[1] + flow[..., 1], trust


def carried(frames, k0, change, weight, progress=None):
    """The frames with the change carried from frame k0 to every other frame. Returns (frames, weights)."""
    import cv2
    follow = Follow(frames[0].shape)
    n = len(frames)
    out, weights = [None] * n, [None] * n
    out[k0] = np.clip(frames[k0].astype(np.float32) + change * weight[..., None], 0, 255).astype(np.uint8)
    weights[k0] = weight
    done = 1
    for step in (1, -1):
        d, w = change, weight
        k = k0 + step
        while 0 <= k < n:
            mx, my, trust = follow.maps(frames[k], frames[k - step])
            d = cv2.remap(d, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
            w = cv2.remap(w, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT) * trust
            out[k] = np.clip(frames[k].astype(np.float32) + d * w[..., None], 0, 255).astype(np.uint8)
            weights[k] = w
            done += 1
            if progress:
                progress(done / n)
            k += step
    return out, weights


def flicker(results, originals, weights, every=2):
    """How much the edited area changes from one frame to the next once the motion is taken out (the frame before
    is moved along the original's flow first), for the result and for the original: (result, original), 0..255.
    A plain frame difference would count a sharp new pattern that moves with the picture as flicker; measured on
    the elephant clip it read 13.2 against 8.8 while this reads 4.60 against 4.54."""
    import cv2
    follow = Follow(originals[0].shape)
    sums, count = [0.0, 0.0], 0
    for k in range(1, len(originals), every):
        m = (weights[k] > 0.5) & (weights[k - 1] > 0.5)
        if m.sum() < 50:
            continue
        mx, my, trust = follow.maps(originals[k], originals[k - 1])
        m &= trust > 0.9
        if m.sum() < 50:
            continue
        for i, seq in enumerate((results, originals)):
            before = cv2.remap(seq[k - 1], mx, my, cv2.INTER_LINEAR)
            sums[i] += float(np.abs(seq[k].astype(np.int16) - before.astype(np.int16))[m].mean())
        count += 1
    return (sums[0] / count, sums[1] / count) if count else (0.0, 0.0)


def _carry(task, name, web, seconds, original_path, edited_path):
    import cv2
    source = projects.media_path(name, web)
    task["text"] = "reading the clip"
    frames, fps = _decode(source)
    if not frames:
        raise ValueError("the clip could not be read")
    h, w = frames[0].shape[:2]
    k0 = min(len(frames) - 1, max(0, int(round(seconds * fps))))
    original = imread(original_path)
    edited = imread(edited_path)
    if original.shape[:2] != (h, w):
        original = cv2.resize(original, (w, h), interpolation=cv2.INTER_AREA)
    if edited.shape[:2] != (h, w):
        edited = cv2.resize(edited, (w, h), interpolation=cv2.INTER_AREA)
    # the edit is measured against the frame it was made on, then laid on the decoded frame k0 (the same picture)
    change, weight = change_of(original, edited)
    if weight.max() < 0.5:
        raise ValueError("the edited frame is the same as the original: nothing to carry")

    def progress(f):
        task.update(progress=0.9 * f, text=f"carrying the change: frame {int(f * len(frames))} of {len(frames)}")
    out, weights = carried(frames, k0, change, weight, progress)
    task["text"] = "writing the video"
    target_dir = os.path.join(projects._folder(name), "media")
    os.makedirs(target_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(source))[0]
    target = os.path.join(target_dir, f"{stem}-edit-{uuid.uuid4().hex[:6]}.mp4")
    exe = library.ffmpeg()
    cmd = [exe, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}", "-r", f"{fps:.6f}",
           "-i", "-", "-i", source, "-map", "0:v", "-map", "1:a?", "-c:v", "libx264", "-preset", "medium",
           "-crf", "16", "-pix_fmt", "yuv420p", "-c:a", "copy", "-shortest", target]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    for frame in out:
        proc.stdin.write(frame.tobytes())
    proc.stdin.close()
    if proc.wait() != 0 or not os.path.isfile(target):
        raise RuntimeError("ffmpeg could not write the edited clip")
    web_new = projects.add_media(name, target)
    task["text"] = "measuring how steady it is"
    steady, before = flicker(out, frames, weights)
    kept = [float((w > 0.5).sum()) for w in weights]
    held = sum(1 for k in kept if k > 0.5 * kept[k0]) / float(len(kept))
    return {"web": web_new, "frames": len(frames), "edited_frame": k0, "progress": 1.0,
            "flicker": round(steady, 2), "flicker_original": round(before, 2), "held": round(held, 2),
            "text": f"carried through {len(frames)} frames"}

"""Layers: a clip or picture cut out of its background (transparent, to lie over another one) and titles.

Cut-outs are made by BiRefNet-lite (the half-precision copy Time Strata keeps in webui\\models\\time-strata; about
0.36 s a frame on the 3070 Laptop and under 4 GB of the card) and written as VP8 WebM with an alpha channel:
FilmCraft keeps its transparency both in the page and in exports (checked against ProRes 4444, which also works
but is ~13 times bigger). Titles are drawn by the page itself (the browser lays Hebrew out right to left, which
Pillow here cannot) and stored here as transparent PNGs.
"""

import base64
import io
import os
import subprocess
import time
import uuid

import numpy as np

from . import frames, library, paths, projects

MODELS = os.path.join(paths.WEBUI, "models", "time-strata")
MEAN = np.array([0.485, 0.456, 0.406], np.float32)
STD = np.array([0.229, 0.224, 0.225], np.float32)


def model_file():
    for name in ("birefnet-general-lite.fp16.onnx", "birefnet-general-lite.onnx"):
        path = os.path.join(MODELS, name)
        if os.path.isfile(path):
            return path
    return None


def available():
    return {"cutout": bool(model_file()), "model": os.path.basename(model_file() or "")}


class Matte:
    """BiRefNet at 1024x1024 with ImageNet's normalisation and a sigmoid, kept inside the card's free memory."""

    def __init__(self):
        import onnxruntime as ort
        try:
            import torch
            free = torch.cuda.mem_get_info()[0]
        except Exception:
            free = 6 * 1024 ** 3
        cuda = {"cudnn_conv_algo_search": "HEURISTIC", "arena_extend_strategy": "kSameAsRequested",
                "gpu_mem_limit": str(int(max(2 * 1024 ** 3, free - 0.4 * 1024 ** 3)))}
        path = model_file()
        if not path:
            raise RuntimeError("BiRefNet-lite is not in webui\\models\\time-strata (Time Strata downloads it)")
        self.session = ort.InferenceSession(path, providers=[("CUDAExecutionProvider", cuda), "CPUExecutionProvider"])
        self.input = self.session.get_inputs()[0].name

    def __call__(self, rgb):
        import cv2
        h, w = rgb.shape[:2]
        im = cv2.resize(rgb, (1024, 1024), interpolation=cv2.INTER_AREA if max(h, w) > 1024 else cv2.INTER_CUBIC)
        im = (im.astype(np.float32) / 255.0 - MEAN) / STD
        out = self.session.run(None, {self.input: np.ascontiguousarray(im.transpose(2, 0, 1)[None])})[0][0, 0]
        alpha = 1.0 / (1.0 + np.exp(-out.astype(np.float32)))
        alpha = cv2.resize(alpha, (w, h), interpolation=cv2.INTER_LINEAR)
        # a few percent left over everywhere becomes a grey fog once layered (Time Strata, 2026-10-18)
        alpha[alpha < 0.03] = 0.0
        alpha[alpha > 0.97] = 1.0
        return alpha


def cutout(name, web):
    return frames._task("cutout", _cutout, name, web)


def _cutout(task, name, web):
    import cv2
    from .sound import _on_card
    source = projects.media_path(name, web)
    kind = library.kind_of(source)
    if kind not in ("video", "image"):
        raise ValueError("only a video or a picture can be cut out")
    folder = os.path.join(projects._folder(name), "media")
    os.makedirs(folder, exist_ok=True)
    stem = os.path.splitext(os.path.basename(source))[0]
    task["text"] = "waiting for the graphics card"

    def work():
        matte = Matte()
        if kind == "image":
            rgb = cv2.cvtColor(frames.imread(source), cv2.COLOR_BGR2RGB)
            alpha = matte(rgb)
            out = os.path.join(folder, f"{stem}-cutout-{uuid.uuid4().hex[:6]}.png")
            from PIL import Image
            Image.fromarray(np.dstack([rgb, (alpha * 255).astype(np.uint8)]), "RGBA").save(out)
            return out
        cap = cv2.VideoCapture(source)
        fps = cap.get(cv2.CAP_PROP_FPS) or 16.0
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        out = os.path.join(folder, f"{stem}-cutout-{uuid.uuid4().hex[:6]}.webm")
        cmd = [library.ffmpeg(), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{w}x{h}",
               "-r", f"{fps:.6f}", "-i", "-", "-c:v", "libvpx", "-pix_fmt", "yuva420p", "-auto-alt-ref", "0",
               "-crf", "8", "-b:v", "12M", "-an", out]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        n, t0 = 0, time.time()
        try:
            while True:
                ok, frame = cap.read()
                if not ok:
                    break
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                alpha = matte(rgb)
                proc.stdin.write(np.dstack([rgb, (alpha * 255).astype(np.uint8)]).tobytes())
                n += 1
                left = (time.time() - t0) / n * max(0, total - n)
                task.update(progress=n / max(1, total), text=f"cutting out frame {n} of {total} (about {int(left)} s left)")
        finally:
            cap.release()
            proc.stdin.close()
        if proc.wait() != 0 or not os.path.isfile(out):
            raise RuntimeError("ffmpeg could not write the cut-out")
        return out

    out = _on_card(work)
    new_web = projects.add_media(name, out)
    return {"web": new_web, "progress": 1.0, "text": "cut out: " + new_web}


def reframe(name, web, start, seconds, every=0.25):
    """Where the subject is in `seconds` of a video from `start` (file seconds): BiRefNet's matte every `every`
    seconds, its weighted centre and how much of the picture it covers. A task; its result is
    {points: [[file second, cx, cy, area], ...]} with cx, cy in parts of the picture (none where nothing was found)."""
    return frames._task("reframe", _reframe, name, web, float(start), float(seconds), float(every))


def _reframe(task, name, web, start, seconds, every):
    import cv2
    from .sound import _on_card
    source = projects.media_path(name, web)
    if library.kind_of(source) != "video":
        raise ValueError("only a video can be reframed")
    times = [start + k * every for k in range(max(1, min(240, int(seconds / every) + 1)))]
    task["text"] = "waiting for the graphics card"

    def work():
        matte = Matte()
        cap = cv2.VideoCapture(source)
        points = []
        try:
            for i, t in enumerate(times):
                cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
                ok, frame = cap.read()
                if not ok:
                    continue
                small = cv2.resize(frame, (frame.shape[1] // 2, frame.shape[0] // 2), interpolation=cv2.INTER_AREA)
                alpha = matte(cv2.cvtColor(small, cv2.COLOR_BGR2RGB))
                area = float(alpha.mean())
                if area > 0.004:
                    ys, xs = np.mgrid[0:alpha.shape[0], 0:alpha.shape[1]]
                    total = float(alpha.sum())
                    points.append([round(t, 3), round(float((alpha * xs).sum() / total / alpha.shape[1]), 4),
                                   round(float((alpha * ys).sum() / total / alpha.shape[0]), 4), round(area, 4)])
                task.update(progress=(i + 1) / len(times), text=f"finding the subject ({i + 1} of {len(times)})")
        finally:
            cap.release()
        return points

    points = _on_card(work)
    return {"points": points, "progress": 1.0, "text": f"the subject found in {len(points)} of {len(times)} moments"}


def title(name, image, label="title"):
    """A title the page drew (a PNG data URL with transparency), kept in the project's media."""
    from PIL import Image
    raw = base64.b64decode(image.split(",", 1)[-1])
    folder = os.path.join(projects._folder(name), "media")
    os.makedirs(folder, exist_ok=True)
    safe = "".join(c for c in (label or "title") if c.isalnum() or c in " -_")[:30].strip() or "title"
    out = os.path.join(folder, f"title-{safe}-{uuid.uuid4().hex[:6]}.png")
    with Image.open(io.BytesIO(raw)) as im:
        im.convert("RGBA").save(out)
    web = projects.add_media(name, out)
    return {"web": web, "path": out}

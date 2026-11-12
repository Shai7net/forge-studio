"""Character sheets as ingredients.

A character reference sheet (front / side / back, a grid of heads, a close-up, on a plain background) shows one
person many times and small. Given whole to Phantom it reads as a crowd of little figures and the video person
comes out a stranger. split() finds the figures and returns the views that carry the likeness: the front full body
and, when the sheet has one, a large close-up. A picture of several different people is not a sheet and is left
whole (the figures of a sheet share their colours: same clothes, same skin).

Measured on the 3070 Laptop: BiRefNet on the card well under a second, on the processor ~20 s a picture.
"""
import os

import numpy as np
from PIL import Image

ANALYSE = 512          # the sheet is studied at this size (long side)
SMOOTH = 1.5           # a studio backdrop's border changes less than this per pixel (photos: 3 and up)
# Histogram overlap (8 levels a channel) of two views of one character. Measured 2026-11-11: five real sheets
# 0.70-0.95; a picture of two different characters side by side 0.50 (at 4 levels it was 0.71, over the old 0.55,
# and a girl and a dog were taken for one character)
SAME_COLOURS = 0.6
MIN_VIEWS = 3          # a sheet shows its character at least three times (front, side, back...); two figures are two people
MATTE_FILES = ("birefnet-general-lite.fp16.onnx", "birefnet-general-lite.onnx")  # Time Strata's copies
MEAN = np.array([0.485, 0.456, 0.406], np.float32)
STD = np.array([0.229, 0.224, 0.225], np.float32)


def matte_file(models_dir):
    """Time Strata's BiRefNet-lite (a sheet is not split without it)."""
    for name in MATTE_FILES:
        path = os.path.join(models_dir, "time-strata", name)
        if os.path.isfile(path):
            return path
    return None


def _small(picture):
    scale = ANALYSE / max(picture.size)
    size = (max(1, round(picture.width * scale)), max(1, round(picture.height * scale)))
    return np.asarray(picture.resize(size, Image.BILINEAR)).astype(np.float32), scale


def plain_backdrop(small):
    """True when three of the four sides of the picture are smooth, like a studio backdrop (its light may change
    across it; a sheet's close-up is often cut by one side). Photos have texture at their edges."""
    from scipy import ndimage
    grey = small.mean(axis=2)
    change = np.hypot(ndimage.sobel(grey, 0), ndimage.sobel(grey, 1)) / 8
    sides = [change[2:8], change[-8:-2], change[:, 2:8], change[:, -8:-2]]
    return sum(np.percentile(side, 60) < SMOOTH for side in sides) >= 3


def _session(model_path, cpu=False):
    """BiRefNet on the card when it has room (well under a second), else on the processor (~20 s)."""
    import onnxruntime as ort
    options = ort.SessionOptions()
    options.log_severity_level = 3
    options.intra_op_num_threads = min(4, os.cpu_count() or 1)
    fp16 = "fp16" in os.path.basename(model_path)
    if not cpu and "CUDAExecutionProvider" in ort.get_available_providers():
        try:
            import torch
            free = torch.cuda.mem_get_info()[0]
        except Exception:
            free = 0
        if free > 3 * 1024 ** 3:
            cuda = {"cudnn_conv_algo_search": "HEURISTIC", "arena_extend_strategy": "kSameAsRequested",
                    "gpu_mem_limit": str(int(free - 0.5 * 1024 ** 3))}
            try:
                return ort.InferenceSession(model_path, options, providers=[("CUDAExecutionProvider", cuda)]), fp16
            except Exception:
                pass
    if fp16:  # the processor has no half-precision kernels
        full = model_path.replace(".fp16.onnx", ".onnx")
        if os.path.isfile(full):
            return ort.InferenceSession(full, options, providers=["CPUExecutionProvider"]), False
    return ort.InferenceSession(model_path, options, providers=["CPUExecutionProvider"]), fp16


def birefnet_mask(picture, model_path, cpu=False):
    """BiRefNet-lite's cut-out of everything in front of the background, at ANALYSE size."""
    session, fp16 = _session(model_path, cpu=cpu)
    im = np.asarray(picture.resize((1024, 1024), Image.BICUBIC)).astype(np.float32) / 255.0
    im = ((im - MEAN) / STD).transpose(2, 0, 1)[None]
    if session.get_inputs()[0].type == "tensor(float16)":
        im = im.astype(np.float16)
    out = session.run(None, {session.get_inputs()[0].name: np.ascontiguousarray(im)})[0][0, 0].astype(np.float32)
    del session
    alpha = Image.fromarray((255.0 / (1.0 + np.exp(-out))).astype(np.uint8))
    small, _ = _small(picture)
    return np.asarray(alpha.resize((small.shape[1], small.shape[0]), Image.BILINEAR)) > 127


def figures(mask):
    """Boxes (x0, y0, x1, y1, area) of the separate figures in an ANALYSE-size mask, in its pixels."""
    from scipy import ndimage
    h, w = mask.shape
    labels, _ = ndimage.label(ndimage.binary_fill_holes(mask))
    boxes = []
    for k, sl in enumerate(ndimage.find_objects(labels), start=1):
        if sl is None:
            continue
        ys, xs = sl
        area = int((labels[sl] == k).sum())
        if ys.stop - ys.start < 0.08 * h or area < 0.002 * h * w:
            continue  # specks, a title line
        boxes.append((xs.start, ys.start, xs.stop, ys.stop, area))
    return boxes


def pick_views(boxes, size=None):
    """The front full body and a large close-up (or None) from figures()' boxes. The full-body views are the
    tallest figures; of those the front is the first, in reading order, of the widest (a side view is narrow, and
    sheets put the front before the back). The close-up is the biggest other figure at least as tall as it is wide
    and over 0.6 of the full body's height (a row of small heads is wide, a single small head is short). A figure cut
    by the bottom of the picture (`size`, w x h) is a close-up, never the full body."""
    if len(boxes) < 2:
        return None, None
    height = lambda b: b[3] - b[1]
    width = lambda b: b[2] - b[0]
    whole = [b for b in boxes if not size or b[3] < 0.985 * size[1]]
    if not whole:
        return None, None
    top = max(height(b) for b in whole)
    tall = [b for b in whole if height(b) >= 0.85 * top and height(b) >= width(b)]
    if not tall:
        return None, None
    widest = max(width(b) for b in tall)
    row = 0.1 * top
    front = sorted((b for b in tall if width(b) >= 0.9 * widest), key=lambda b: (round(b[1] / row), b[0]))[0]
    others = [b for b in boxes if b is not front and b not in tall and 0.9 <= height(b) / max(1, width(b)) <= 2.2
              and height(b) >= 0.6 * height(front)]
    close = max(others, key=lambda b: b[4]) if others else None
    return front, close


def colours(small, mask, box):
    """A colour histogram (8 levels a channel) of a figure's own pixels."""
    x0, y0, x1, y1 = box[:4]
    px = small[y0:y1, x0:x1][mask[y0:y1, x0:x1]]
    if not len(px):
        return np.zeros(512)
    q = np.clip(px // 32, 0, 7).astype(int)
    hist = np.bincount(q[:, 0] * 64 + q[:, 1] * 8 + q[:, 2], minlength=512).astype(np.float64)
    return hist / hist.sum()


def one_character(small, mask, boxes, front):
    """True when the tallest figures (and so the views kept) look like one character: each overlaps the front's
    colours by SAME_COLOURS or more. Two or more full-body figures, and MIN_VIEWS figures in all, are needed to
    call a picture a sheet."""
    if len(boxes) < MIN_VIEWS:
        return False
    top = max(b[3] - b[1] for b in boxes)
    tall = [b for b in boxes if b[3] - b[1] >= 0.85 * top]
    if len(tall) < 2:
        return False
    ref = colours(small, mask, front)
    return all(np.minimum(ref, colours(small, mask, b)).sum() >= SAME_COLOURS for b in tall if b is not front)


def crop(picture, box, scale, pad=0.05):
    """The picture cut at an ANALYSE-size box, with a little room around it."""
    x0, y0, x1, y1 = (v / scale for v in box[:4])
    p = pad * max(x1 - x0, y1 - y0)
    return picture.crop((max(0, round(x0 - p)), max(0, round(y0 - p)),
                         min(picture.width, round(x1 + p)), min(picture.height, round(y1 + p))))


def split(path, matte_model, cpu=False):
    """[(name, PIL picture), ...] for a character sheet, or [] when the picture is not one. The figures are found
    by BiRefNet's cut-out (`matte_model`), which a lit backdrop and floor shadows do not fool; it runs only for
    pictures on a plain backdrop."""
    if not matte_model:
        return []
    with Image.open(path) as picture:
        picture = picture.convert("RGB")
    small, scale = _small(picture)
    if not plain_backdrop(small):
        return []
    mask = birefnet_mask(picture, matte_model, cpu=cpu)
    boxes = figures(mask)
    front, close = pick_views(boxes, small.shape[1::-1])
    if front is None or not one_character(small, mask, boxes, front):
        return []
    views = [("front", crop(picture, front, scale))]
    if close is not None:
        views.append(("close-up", crop(picture, close, scale)))
    return views

"""Reference pictures fitted to the size chosen in Forge (2026-11-16).

A single edit picture is cropped to the chosen Width x Height (the edit lines up with it pixel for pixel), around the
faces in it when there are any, so a 1920x2880 portrait asked for at 1024x576 keeps the face, not the chest.
Several references keep their own shape and are only made smaller (the model scales each to about 1 MP anyway; a
large one only cost memory and time). The same file is in sd-forge-flux3 (flux3/fit.py).
"""
import math

from PIL import Image

REFERENCE_AREA = 1024 * 1024 * 1.1  # the model's trained area per reference, with Bria's 10 % margin


def size16(n):
    return max(64, int(n) // 16 * 16)


def shape_like(size, area):
    """(width, height) with the picture's aspect ratio and about `area` pixels, multiples of 16."""
    w, h = size
    scale = math.sqrt(area / (w * h))
    return size16(round(w * scale)), size16(round(h * scale))


def faces_center(image):
    """The centre of the faces in the picture as fractions (x, y), or None. OpenCV is optional."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None
    small = image.convert("L")
    scale = min(1.0, 640 / max(small.size))
    if scale < 1:
        small = small.resize((max(1, round(small.width * scale)), max(1, round(small.height * scale))), Image.Resampling.BILINEAR)
    try:
        detector = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        found = detector.detectMultiScale(np.asarray(small), scaleFactor=1.1, minNeighbors=6,
                                          minSize=(max(24, small.width // 20),) * 2)
    except Exception:  # noqa: BLE001 - a missing cascade file must never stop a generation
        return None
    if len(found) == 0:
        return None
    # the faces that matter: the largest and those at least a third of its size
    biggest = max(w * h for x, y, w, h in found)
    found = [f for f in found if f[2] * f[3] >= biggest / 3]
    left, top = min(f[0] for f in found), min(f[1] for f in found)
    right, bottom = max(f[0] + f[2] for f in found), max(f[1] + f[3] for f in found)
    # a little above the middle of the face box: hair above, chin and shoulders below
    return (left + right) / 2 / small.width, (top + (bottom - top) * 0.45) / small.height


def crop_box(size, width, height, focus=None):
    """Scale-to-cover size and the crop box for width x height, centred on `focus` (x, y fractions).
    Without faces a tall picture is cut a little above its middle (heads are usually in the upper part)."""
    scale = max(height / size[1], width / size[0])
    cover = math.ceil(size[0] * scale), math.ceil(size[1] * scale)
    fx, fy = focus or (0.5, 0.42)
    left = min(max(0, round(fx * cover[0] - width / 2)), cover[0] - width)
    top = min(max(0, round(fy * cover[1] - height / 2)), cover[1] - height)
    return cover, (left, top, left + width, top + height)


def crop_to(image, cover, box, mask=False):
    image = image.convert("L" if mask else "RGB")
    if image.size != cover:
        image = image.resize(cover, Image.Resampling.NEAREST if mask else Image.Resampling.LANCZOS)
    return image.crop(box)


def fit(references, mask, width, height, single_edit=True):
    """References (and the exact mask of a single one) fitted to the output size. Returns (references, mask, note)."""
    references = [r.convert("RGB") for r in references]
    if not references:
        return references, mask, ""
    first = references[0]
    if mask is not None and mask.size != first.size:
        mask = mask.convert("L").resize(first.size, Image.Resampling.NEAREST)
    if single_edit and len(references) == 1:
        if first.size == (width, height):
            return references, mask, ""
        focus = faces_center(first)
        cover, box = crop_box(first.size, width, height, focus)
        note = f"{first.width}x{first.height} -> {width}x{height}" + (" around the face" if focus else "")
        return [crop_to(first, cover, box)], (crop_to(mask, cover, box, mask=True) if mask is not None else None), note
    fitted = []
    for r in references:
        if r.width * r.height > REFERENCE_AREA:
            r = r.resize(shape_like(r.size, 1024 * 1024), Image.Resampling.LANCZOS)
        fitted.append(r)
    return fitted, mask, ""

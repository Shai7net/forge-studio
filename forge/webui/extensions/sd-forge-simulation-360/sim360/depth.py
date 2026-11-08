"""How far each direction of a panorama is.

The page draws a world as a sphere whose surface is pushed in and out by this map, so the camera can leave
the centre and things nearby move past things far away. The map is "nearness": 1 is the nearest thing in the
picture, 0 the farthest; the page turns it into metres with the world's own near and far distances, so they
can be tuned without measuring again.

The depth model (MiDaS, the one ControlNet's depth preprocessor already has on disk) was trained on ordinary
photographs, so the panorama is prepared for it:
  * it is given a quarter of the picture from the other side at each end, so both ends of the ring agree;
  * towards the top and the bottom every row is one small circle around the pole, and the model's answer
    there is noise, so those rows are drawn towards their own average.
"""

import os

import numpy as np
from PIL import Image

WORK_WIDTH = 768        # the panorama as the model sees it (plus the borrowed quarters)
SAVE_WIDTH = 1024
POLE_BAND = 0.22        # share of the height, at each pole, that is drawn towards the row's average
RIM = 0.011             # share of the width by which near things are widened in the map

_model = None


def _load():
    global _model
    if _model is None:
        from annotator.midas.api import MiDaSInference
        _model = MiDaSInference(model_type="dpt_hybrid")
    return _model


def available():
    try:
        from annotator.midas.api import ISL_PATHS
        return os.path.isfile(ISL_PATHS["dpt_hybrid"])
    except Exception:
        return False


def _predict(rgb):
    """Nearness as the model gives it (any scale), for an RGB array whose sides are multiples of 32."""
    import torch
    from ldm_patched.modules import model_management
    device = model_management.get_torch_device()
    model = _load()
    try:
        model_management.free_memory(2 * 1024 ** 3, device)
        model.to(device)
        with torch.no_grad():
            x = torch.from_numpy(rgb.copy()).float().to(device) / 127.5 - 1.0
            return model(x.permute(2, 0, 1)[None])[0].float().cpu().numpy()
    finally:
        model.cpu()
        model_management.soft_empty_cache()


def nearness(image):
    """A float array (height, width), 0..1, 1 = nearest, for a panorama given as a PIL image."""
    import cv2
    width = WORK_WIDTH
    height = max(64, int(round(width * image.height / image.width / 32)) * 32)
    rgb = np.asarray(image.convert("RGB").resize((width, height), Image.LANCZOS))
    quarter = width // 4 // 32 * 32
    wide = np.concatenate([rgb[:, -quarter:], rgb, rgb[:, :quarter]], axis=1)
    near = _predict(wide)[:, quarter:quarter + width]

    low, high = np.percentile(near, 1), np.percentile(near, 99)
    near = np.clip((near - low) / max(high - low, 1e-6), 0.0, 1.0).astype(np.float32)

    # towards the poles a row is a small circle: its values are pulled to their average
    rows = np.arange(height, dtype=np.float32)
    from_pole = np.minimum(rows, height - 1 - rows) / max(1.0, height * POLE_BAND)
    keep = np.clip(from_pole, 0.0, 1.0)
    keep = (keep * keep * (3 - 2 * keep))[:, None]
    near = near * keep + near.mean(axis=1, keepdims=True) * (1 - keep)

    # A near thing takes a rim of what is behind it along: the steep wall between the two then lies on the
    # background, and the thing's own outline stays whole when the camera moves. Then the map is made soft:
    # sharp steps in it would tear the picture.
    rim = max(1, round(width * RIM))
    wrapped = np.concatenate([near[:, -32:], near, near[:, :32]], axis=1)
    wrapped = cv2.dilate(wrapped, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * rim + 1, 2 * rim + 1)))
    wrapped = cv2.GaussianBlur(wrapped, (0, 0), sigmaX=3.0, sigmaY=3.0)
    return np.ascontiguousarray(wrapped[:, 32:-32])


def save(near, path):
    """16 bits a pixel in an ordinary PNG: the high byte in red, the low byte in green."""
    import cv2
    height = max(2, int(round(SAVE_WIDTH * near.shape[0] / near.shape[1])))
    resized = cv2.resize(near, (SAVE_WIDTH, height), interpolation=cv2.INTER_CUBIC)
    value = np.clip(resized * 65535.0 + 0.5, 0, 65535).astype(np.uint16)
    packed = np.zeros((height, SAVE_WIDTH, 3), np.uint8)
    packed[..., 0] = value >> 8
    packed[..., 1] = value & 255
    Image.fromarray(packed).save(path, optimize=False, compress_level=4)


def load(path):
    packed = np.asarray(Image.open(path).convert("RGB")).astype(np.float32)
    return (packed[..., 0] * 256.0 + packed[..., 1]) / 65535.0


def relief(image, alpha):
    """The shape of one object, for its 3D effect: a uint8 array (height, width), 255 where the object comes
    nearest to the viewer and 0 where it is farthest, measured inside the object only (`alpha` > 127).
    Outside the object the map carries on smoothly from its edge."""
    import cv2
    height, width = alpha.shape
    scale = 448 / max(width, height)
    w, h = max(64, round(width * scale / 32) * 32), max(64, round(height * scale / 32) * 32)
    near = _predict(np.asarray(image.convert("RGB").resize((w, h), Image.LANCZOS)))
    inside = cv2.resize(alpha, (w, h), interpolation=cv2.INTER_AREA) > 127
    if inside.sum() < 32:
        return np.full((height, width), 128, np.uint8)
    # The model also sees the object leaning (its feet nearer than its head, or the other way round), which
    # is how the picture was taken, not what the object is. That lean is taken out; what stays is its body.
    ys, xs = np.nonzero(inside)
    plane = np.column_stack([xs, ys, np.ones(len(xs))]).astype(np.float64)
    lean, *_ = np.linalg.lstsq(plane, near[inside].astype(np.float64), rcond=None)
    grid_y, grid_x = np.mgrid[0:h, 0:w]
    near = (near - (lean[0] * grid_x + lean[1] * grid_y + lean[2])).astype(np.float32)
    low, high = np.percentile(near[inside], 3), np.percentile(near[inside], 97)
    near = np.clip((near - low) / max(high - low, 1e-6), 0.0, 1.0).astype(np.float32)
    # the object's own values, spread outwards past its edge, then made soft
    weight = cv2.GaussianBlur(inside.astype(np.float32), (0, 0), 6.0)
    spread = cv2.GaussianBlur(near * inside, (0, 0), 6.0) / np.maximum(weight, 1e-4)
    near = np.where(inside, near, spread)
    near = cv2.GaussianBlur(near, (0, 0), 2.0)
    return np.clip(cv2.resize(near, (width, height), interpolation=cv2.INTER_CUBIC) * 255 + 0.5, 0, 255).astype(np.uint8)


def measure(image, path):
    """Writes the depth map of a panorama. Returns False when no depth model is on this computer."""
    if not available():
        return False
    save(nearness(image), path)
    return True

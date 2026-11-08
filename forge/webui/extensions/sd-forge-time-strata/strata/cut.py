"""The cut: what makes a stack of hundreds of frames read as one solid figure through time.

A frame alone forgives a lot that a stack does not:
- **Fog.** A background left at 2% opacity is invisible on one frame; three hundred frames one behind the
  other turn it into a wall (1 - 0.98^300 = 99.8%). So whatever is not the figure is made exactly 0, and
  specks that are not part of the figure are removed (`clean_alpha`).
- **Fringe.** An edge pixel is part figure, part the background it was filmed against. In a stack the edges
  of every frame line the side of the chain, so a fringe becomes a coloured skin. The colour of the figure
  alone is worked out (`unmix`): exactly when the background is a known flat colour, from the background
  just outside the figure otherwise.
- **Jitter.** A figure that jumps sideways between frames makes the chain ragged; one that changes size makes
  it pump. Frames are aligned to one ground line and one centre, with one scale for the whole chain
  (`plan_alignment`), and the frame is made as large as the figure allows without cutting anything off.

Everything here is numpy and OpenCV, so it is tested without Forge (tests/test_cut.py).
"""

import cv2
import numpy as np

# --- what kind of background ---------------------------------------------------------------------------------------

def _lab(rgb):
    return cv2.cvtColor(np.ascontiguousarray(rgb[..., :3], dtype=np.float32) / 255.0, cv2.COLOR_RGB2LAB)


def border_colour(rgb, band=0.03):
    """The median colour of a picture's outer band (RGB 0..255) and how much the band varies (Lab units: a
    flat studio backdrop is under 4, a filmed place is 15 and more)."""
    h, w = rgb.shape[:2]
    b = max(2, int(round(min(h, w) * band)))
    pixels = np.concatenate([rgb[:b, :, :3].reshape(-1, 3), rgb[-b:, :, :3].reshape(-1, 3),
                             rgb[:, :b, :3].reshape(-1, 3), rgb[:, -b:, :3].reshape(-1, 3)]).astype(np.float32)
    colour = np.median(pixels, axis=0)
    lab = cv2.cvtColor(pixels.reshape(-1, 1, 3) / 255.0, cv2.COLOR_RGB2LAB).reshape(-1, 3)
    centre = cv2.cvtColor(colour.reshape(1, 1, 3) / 255.0, cv2.COLOR_RGB2LAB).reshape(3)
    spread = float(np.median(np.linalg.norm(lab - centre, axis=1)))
    return colour, spread


def has_alpha(rgba):
    """Whether a picture carries a real cut: some of it clearly transparent, some of it opaque."""
    if rgba.shape[2] < 4:
        return False
    a = rgba[..., 3]
    return bool((a < 16).mean() > 0.02 and (a > 240).mean() > 0.01)


# --- mattes --------------------------------------------------------------------------------------------------------

def key_matte(rgb, colour, near=9.0, far=26.0):
    """Alpha from the distance to a flat background colour (Lab units): `near` and closer is background,
    `far` and further is figure, in between is the soft edge."""
    lab = _lab(rgb)
    centre = cv2.cvtColor(np.asarray(colour, np.float32).reshape(1, 1, 3) / 255.0, cv2.COLOR_RGB2LAB).reshape(3)
    distance = np.linalg.norm(lab - centre, axis=2)
    return np.clip((distance - near) / max(1e-3, far - near), 0.0, 1.0).astype(np.float32)


_sessions = {}
_making = __import__("threading").Lock()       # U2NET_HOME is the process's: one session is made at a time
_PROVIDERS = ["CUDAExecutionProvider", "CPUExecutionProvider"]
RVM_FILES = {"rvm": "rvm_resnet50_fp32.onnx", "rvm-fast": "rvm_mobilenetv3_fp32.onnx"}
REMBG_MODELS = ["birefnet-general-lite", "birefnet-portrait", "birefnet-general", "isnet-general-use", "u2net_human_seg", "u2net"]
# Measured on an 8 GB laptop card (2026-10-18): BiRefNet's decoder asks for 800 MB at a time at 1024x1024. The
# lite one runs in about 6 GB, 0.4 s a frame; the full ones need more than 6 GB and, spilling into the computer's
# memory, took 62 s a frame. So the full ones are never chosen by themselves on a card of 8 GB or less.
HEAVY_MODELS = {"birefnet-portrait", "birefnet-general"}
_BIREFNET_CUDA = {"cudnn_conv_algo_search": "HEURISTIC", "arena_extend_strategy": "kSameAsRequested"}


def models_dir():
    """Where this extension keeps its own cut-out models: webui\\models\\time-strata (on Forge's own drive, not
    in the user's home folder)."""
    import os
    try:
        from modules import paths
        webui = paths.data_path
    except Exception:
        webui = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    folder = os.path.join(webui, "models", "time-strata")
    os.makedirs(folder, exist_ok=True)
    return folder


def _rembg_homes():
    import os
    return [models_dir(), os.path.expanduser(os.environ.get("U2NET_HOME") or os.path.join("~", ".u2net"))]


def _rembg_home(model):
    """The folder that has a rembg model's file (rembg names it <model>.onnx), or this extension's own."""
    import os
    for home in _rembg_homes():
        if os.path.isfile(os.path.join(home, f"{model}.onnx")):
            return home
    return models_dir()


def model_names():
    """The cut-out models, and which are already on disk (the others are downloaded the first time)."""
    import os
    found = [{"name": name, "kind": "video", "ready": os.path.isfile(os.path.join(models_dir(), file))} for name, file in RVM_FILES.items()]
    found += [{"name": name, "kind": "picture", "ready": any(os.path.isfile(os.path.join(h, f"{name}.onnx")) for h in _rembg_homes()),
               "heavy": name in HEAVY_MODELS and _card_memory() <= 8.5} for name in REMBG_MODELS]
    return found


def _card_memory():
    """The graphics card's memory in GB (0 when there is none)."""
    try:
        import torch
        return torch.cuda.get_device_properties(0).total_memory / 1024 ** 3 if torch.cuda.is_available() else 0.0
    except Exception:
        return 0.0


def _free_card_bytes(margin=0.6):
    """What the graphics card has free now, less a margin (GB) for others, in bytes (at least 2 GB)."""
    try:
        import torch
        free, _total = torch.cuda.mem_get_info()
        return int(max(2 * 1024 ** 3, free - margin * 1024 ** 3))
    except Exception:
        return 6 * 1024 ** 3


def best_ready_model():
    """The best picture model already here that this card runs well (BiRefNet before the older ones)."""
    for entry in model_names():
        if entry["kind"] == "picture" and entry["ready"] and not entry.get("heavy"):
            return entry["name"]
    return "u2net"


def free_graphics_memory():
    """Forge's checkpoint and the rest set aside (Forge loads them again for its next drawing), so a cut-out model
    has the card to itself."""
    from .compat import free_graphics_memory as free_host_memory
    return free_host_memory()


def rvm_ready(name="rvm"):
    import os
    return os.path.isfile(os.path.join(models_dir(), RVM_FILES.get(name, RVM_FILES["rvm"])))


def _cuda_libraries():
    try:
        import torch  # noqa: F401  (loads the CUDA libraries onnxruntime's CUDA provider needs)
    except Exception:
        pass


BIREFNET_FILES = {
    # rembg's own copies of BiRefNet: (address, md5)
    "birefnet-general-lite": ("https://github.com/danielgatis/rembg/releases/download/v0.0.0/BiRefNet-general-bb_swin_v1_tiny-epoch_232.onnx",
                              "4fab47adc4ff364be1713e97b7e66334"),
    "birefnet-portrait": ("https://github.com/danielgatis/rembg/releases/download/v0.0.0/BiRefNet-portrait-epoch_150.onnx",
                          "c3a64a6abf20250d090cd055f12a3b67"),
    "birefnet-general": ("https://github.com/danielgatis/rembg/releases/download/v0.0.0/BiRefNet-general-epoch_244.onnx", None),
}


def _download_birefnet(model, step=None):
    """A BiRefNet file into webui\\models\\time-strata, checked against rembg's md5."""
    import hashlib
    import os
    import urllib.request
    url, md5 = BIREFNET_FILES[model]
    target = os.path.join(models_dir(), f"{model}.onnx")
    part = target + ".part"
    with urllib.request.urlopen(url, timeout=60) as answer, open(part, "wb") as out:
        total = int(answer.headers.get("Content-Length") or 0)
        done = 0
        while True:
            piece = answer.read(1 << 20)
            if not piece:
                break
            out.write(piece)
            done += len(piece)
            if step and total:
                step(f"downloading {model}: {done // 1048576} of {total // 1048576} MB", 0.01 + 0.03 * done / total)
    if md5:
        digest = hashlib.md5()
        with open(part, "rb") as file:
            for piece in iter(lambda: file.read(1 << 22), b""):
                digest.update(piece)
        if digest.hexdigest() != md5:
            os.remove(part)
            raise ValueError(f"{model} came down damaged (its md5 does not match); try again")
    os.replace(part, target)
    return target


class NoRoom(RuntimeError):
    """The graphics card has too little free memory for a model (what it needs and what there is, in the text)."""


# What BiRefNet-lite needs on the card, measured 2026-10-20 on an 8 GB laptop card (onnxruntime 1.22, 1024x1024):
# in full precision more than 5.5 GB (6 GB worked), in half precision more than 3 GB (4 GB worked, 0.36 s a
# frame, its cut 99.95% the same as full precision's). The half-precision copy is used whenever it is there.
BIREFNET_NEEDS_GB = {"fp16": 3.9, "fp32": 6.0}


def _birefnet_file(model):
    """(path, precision) of a BiRefNet model: its half-precision copy when there is one (<model>.fp16.onnx)."""
    import os
    home = _rembg_home(model)
    half = os.path.join(home, f"{model}.fp16.onnx")
    if os.path.isfile(half):
        return half, "fp16"
    return os.path.join(home, f"{model}.onnx"), "fp32"


def _free_card_gb():
    try:
        import torch
        return torch.cuda.mem_get_info()[0] / 1024 ** 3 if torch.cuda.is_available() else None
    except Exception:
        return None


class _BiRefNet:
    """BiRefNet run directly (not through rembg: Forge may load another extension's older rembg, without it).
    The picture at 1024x1024, ImageNet's normalisation, a sigmoid; held inside the card's free memory. When the
    card has too little free memory for it, it says so (NoRoom) instead of failing part-way through a chain."""
    MEAN = np.array([0.485, 0.456, 0.406], np.float32)
    STD = np.array([0.229, 0.224, 0.225], np.float32)

    def __init__(self, path, precision="fp32"):
        import onnxruntime as ort
        _cuda_libraries()
        need, free = BIREFNET_NEEDS_GB[precision], _free_card_gb()
        if free is not None and free - 0.4 < need:
            raise NoRoom(f"BiRefNet-lite ({precision}) needs about {need:.1f} GB of the graphics card and {free:.1f} GB is free")
        cuda = dict(_BIREFNET_CUDA, gpu_mem_limit=str(_free_card_bytes(margin=0.4)))
        self.session = ort.InferenceSession(path, providers=[("CUDAExecutionProvider", cuda), "CPUExecutionProvider"])
        self.input = self.session.get_inputs()[0].name
        self.precision = precision

    def __call__(self, rgb):
        h, w = rgb.shape[:2]
        size = (1024, 1024)
        im = cv2.resize(np.ascontiguousarray(rgb[..., :3]), size, interpolation=cv2.INTER_AREA if max(h, w) > 1024 else cv2.INTER_CUBIC)
        im = (im.astype(np.float32) / 255.0 - self.MEAN) / self.STD
        try:
            out = self.session.run(None, {self.input: np.ascontiguousarray(im.transpose(2, 0, 1)[None])})[0][0, 0]
        except Exception as e:
            if "Available memory" in str(e) or "BFCArena" in str(e) or "out of memory" in str(e).lower():
                raise NoRoom(f"BiRefNet-lite ({self.precision}) ran out of graphics memory") from e
            raise
        pred = 1.0 / (1.0 + np.exp(-out.astype(np.float32)))
        return np.clip(cv2.resize(pred, (w, h), interpolation=cv2.INTER_LINEAR), 0.0, 1.0)


def _birefnet_session(model, step=None):
    import os
    path, precision = _birefnet_file(model)
    if not os.path.isfile(path):
        path, precision = _download_birefnet(model, step), "fp32"
    return _BiRefNet(path, precision)


def prepare_model(model, step=None):
    """A picture model made ready before the first frame (downloaded first, when it has to be, with its progress
    told to `step`). Raises NoRoom when the card has too little free memory for it."""
    if model.startswith("birefnet") and model not in _sessions:
        _sessions[model] = _birefnet_session(model, step)


def model_matte(rgb, model="u2net", step=None):
    """Alpha from a picture model. BiRefNet runs here directly (downloaded into webui\\models\\time-strata the
    first time); the older models go through rembg, whose own folder is looked in as well."""
    import os
    from PIL import Image
    if model.startswith("birefnet"):
        if model not in _sessions:
            _sessions[model] = _birefnet_session(model, step)
        return _sessions[model](rgb)
    if model not in _sessions:
        _cuda_libraries()
        from rembg import new_session
        with _making:
            before = os.environ.get("U2NET_HOME")
            os.environ["U2NET_HOME"] = _rembg_home(model)      # rembg reads it only while making the session
            try:
                try:
                    _sessions[model] = new_session(model, providers=_PROVIDERS)
                except TypeError:
                    _sessions[model] = new_session(model)
            finally:
                if before is None:
                    os.environ.pop("U2NET_HOME", None)
                else:
                    os.environ["U2NET_HOME"] = before
    from rembg import remove
    mask = remove(Image.fromarray(np.ascontiguousarray(rgb[..., :3])), session=_sessions[model], only_mask=True,
                  post_process_mask=False)
    return np.asarray(mask, np.float32) / 255.0


class VideoMatter:
    """Robust Video Matting: people in a video, cut frame after frame with a memory of the frames before, so the
    edges do not flicker (flicker is what makes a stack's sides ragged). Frames must come in order."""

    def __init__(self, name="rvm"):
        import os
        import onnxruntime as ort
        path = os.path.join(models_dir(), RVM_FILES.get(name, RVM_FILES["rvm"]))
        if not os.path.isfile(path):
            raise FileNotFoundError(f"the video model is not here yet: {path}")
        _cuda_libraries()
        if path not in _sessions:
            _sessions[path] = ort.InferenceSession(path, providers=_PROVIDERS)
        self.session = _sessions[path]
        self.reset()

    def reset(self):
        self.state = [np.zeros((1, 1, 1, 1), np.float32)] * 4

    def __call__(self, rgb):
        h, w = rgb.shape[:2]
        # the model looks at the whole frame at about 512 pixels and refines the edges at full size
        ratio = np.array([min(1.0, max(0.1, 512.0 / max(h, w)))], np.float32)
        src = np.ascontiguousarray(rgb[..., :3].transpose(2, 0, 1)[None], dtype=np.float32) / 255.0
        _fgr, pha, *self.state = self.session.run(None, {"src": src, "r1i": self.state[0], "r2i": self.state[1],
                                                          "r3i": self.state[2], "r4i": self.state[3], "downsample_ratio": ratio})
        return np.clip(pha[0, 0], 0.0, 1.0).astype(np.float32)


def release_models():
    """Every cut-out model let go, and its graphics memory with it."""
    _sessions.clear()
    import gc
    gc.collect()


# --- cleaning ------------------------------------------------------------------------------------------------------

def _disk(radius):
    radius = max(1, int(round(radius)))
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * radius + 1, 2 * radius + 1))


def clean_alpha(alpha, low=0.06, high=0.94, keep_parts=0.03, holes=0.0006, reach=0.012, prior=None, prior_reach=0.03,
                floating=0.3):
    """A matte made safe to stack.
    - below `low` is exactly 0 and above `high` exactly 1 (no fog, no see-through body);
    - parts smaller than `keep_parts` of the largest are removed, with their soft edges (`reach`: how far,
      as a share of the picture's height, a soft edge may lie from a solid part);
    - a separate part that lies mostly above the figure's head or below its feet (less than `floating` of its own
      height beside the figure) is removed: light falling from a ceiling, a sign, a lamp. A second figure stands
      beside the first and stays. (A disco clip's light streaks made its "figure" twice as tall as it was.)
    - holes inside the figure smaller than `holes` of the picture are filled;
    - with a `prior` (where the figure is known to be, e.g. the motion clip it was generated from), nothing
      further than `prior_reach` (a share of the height) from it is kept."""
    h, w = alpha.shape
    a = np.clip((alpha.astype(np.float32) - low) / max(1e-6, high - low), 0.0, 1.0)
    if prior is not None:
        allowed = cv2.dilate((prior >= 0.5).astype(np.uint8), _disk(prior_reach * h))
        allowed = cv2.GaussianBlur(allowed.astype(np.float32), (0, 0), max(1.0, prior_reach / 3 * h))
        a *= np.clip(allowed * 2.0, 0.0, 1.0)
    core = (a >= 0.5).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(core, connectivity=8)
    if count <= 1:
        return np.zeros_like(a)
    areas = stats[1:, cv2.CC_STAT_AREA]
    kept = 1 + np.nonzero(areas >= keep_parts * areas.max())[0]
    main = 1 + int(np.argmax(areas))
    known = np.zeros(0, int)
    if prior is not None:
        # where the figure is known to be decides which part is the figure: a big rock it throws is not, and the
        # parts lying mostly on it always stay (a thin figure beside a larger rock was taken for a stray part)
        on = np.bincount(labels[prior >= 0.5].ravel(), minlength=count)[1:]
        if on.max() > 0:
            main = 1 + int(np.argmax(on))
            known = 1 + np.nonzero(on >= 0.3 * areas)[0]
    if floating and len(kept) > 1:
        top, bottom = stats[main, cv2.CC_STAT_TOP], stats[main, cv2.CC_STAT_TOP] + stats[main, cv2.CC_STAT_HEIGHT]
        beside = []
        for n in kept:
            y, bh = stats[n, cv2.CC_STAT_TOP], stats[n, cv2.CC_STAT_HEIGHT]
            overlap = max(0, min(bottom, y + bh) - max(top, y))
            if n == main or overlap >= floating * bh:
                beside.append(n)
        kept = np.array(beside)
    kept = np.union1d(kept, known)
    solid = np.isin(labels, kept).astype(np.uint8)
    a *= cv2.dilate(solid, _disk(max(2.0, reach * h))).astype(np.float32)
    if holes > 0:
        empty = (a < 0.5).astype(np.uint8)
        count, labels, stats, _ = cv2.connectedComponentsWithStats(empty, connectivity=4)
        limit = holes * h * w
        for n in range(1, count):
            x, y, bw, bh, area = stats[n]
            touches = x == 0 or y == 0 or x + bw >= w or y + bh >= h
            if not touches and area <= limit:
                a[labels == n] = 1.0
    return a


def _soft(image, sigma):
    """A wide Gaussian blur, done on a smaller copy (what it estimates changes slowly across the picture)."""
    if sigma < 4:
        return cv2.GaussianBlur(image, (0, 0), sigma)
    factor = max(1, int(sigma // 2))
    h, w = image.shape[:2]
    small = cv2.resize(image, (max(1, w // factor), max(1, h // factor)), interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(small, (0, 0), sigma / factor)
    return cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)


def unmix(rgb, alpha, background=None):
    """The colour of the figure alone at every pixel (RGB 0..255 in, float 0..1 out). An edge pixel was seen
    as `a * figure + (1 - a) * background`; with the background's colour known (a flat backdrop) the figure's
    colour follows exactly, otherwise the background is taken from just outside the figure. Where the
    figure is too faint to tell, the colour of the figure just inside is used."""
    rgb = rgb[..., :3].astype(np.float32) / 255.0
    a = alpha.astype(np.float32)
    h = a.shape[0]
    a3 = a[..., None]
    inner_w = a ** 2
    inner = _soft(rgb * inner_w[..., None], 0.004 * h + 1) / np.maximum(_soft(inner_w, 0.004 * h + 1)[..., None], 1e-4)
    if background is None:
        outer_w = ((1.0 - a) ** 4)
        back = _soft(rgb * outer_w[..., None], 0.01 * h + 1) / np.maximum(_soft(outer_w, 0.01 * h + 1)[..., None], 1e-4)
    else:
        back = np.broadcast_to(np.asarray(background, np.float32).reshape(1, 1, 3) / 255.0, rgb.shape)
    figure = (rgb - (1.0 - a3) * back) / np.maximum(a3, 1e-3)
    trust = np.clip((a3 - 0.12) / 0.5, 0.0, 1.0)
    figure = trust * figure + (1.0 - trust) * inner
    figure = np.where(a3 >= 0.985, rgb, figure)
    return np.clip(figure, 0.0, 1.0)


# --- measuring and aligning ----------------------------------------------------------------------------------------

def measure(alpha, threshold=0.5):
    """Where the figure is in a frame, in pixels: its box, centre of mass, the point between its feet."""
    h, w = alpha.shape
    solid = (alpha >= threshold).astype(np.uint8)
    moments = cv2.moments(solid, binaryImage=True)
    if moments["m00"] < max(4.0, 1e-5 * h * w):
        return {"empty": True, "width": w, "height": h}
    rows = np.nonzero(solid.any(axis=1))[0]
    cols = np.nonzero(solid.any(axis=0))[0]
    top, bottom, left, right = int(rows[0]), int(rows[-1]), int(cols[0]), int(cols[-1])
    band = max(2, int(0.04 * (bottom - top + 1)))
    feet = np.nonzero(solid[bottom - band + 1:bottom + 1].any(axis=0))[0]
    # the torso: the middle of what is solid between chest and hips, which a raised arm or a kick hardly moves
    tall = bottom - top + 1
    torso_rows = solid[top + int(0.3 * tall):top + int(0.6 * tall) + 1]
    torso_cols = np.nonzero(torso_rows)[1]
    cx = float(moments["m10"] / moments["m00"])
    return {"empty": False, "width": w, "height": h, "area": float(moments["m00"] / (h * w)),
            "top": top, "bottom": bottom, "left": left, "right": right,
            "cx": cx, "cy": float(moments["m01"] / moments["m00"]),
            "torso_x": float(np.median(torso_cols)) if len(torso_cols) else cx,
            "feet_x": float(feet.mean()) if len(feet) else cx, "feet_y": float(bottom)}


def _smooth(values, sigma):
    values = np.asarray(values, np.float64)
    if sigma <= 0 or len(values) < 3:
        return values
    radius = int(np.ceil(3 * sigma))
    kernel = np.exp(-0.5 * (np.arange(-radius, radius + 1) / sigma) ** 2)
    padded = np.pad(values, radius, mode="edge")
    return np.convolve(padded, kernel / kernel.sum(), mode="valid")


def _rolling(values, radius, function):
    values = np.asarray(values, np.float64)
    out = np.empty_like(values)
    for i in range(len(values)):
        out[i] = function(values[max(0, i - radius):i + radius + 1])
    return out


def _fill(values, good):
    """Values at the bad places taken by straight lines from the good ones around them."""
    values = np.asarray(values, np.float64)
    index = np.arange(len(values))
    if good.sum() == 0:
        return values
    return np.interp(index, index[good], values[good])


def figureless(measures, small=0.12):
    """Frames with no figure, or only a sliver of one (`small` of the chain's usual area): a cut between shots,
    a flash, a whip pan. Nothing there to cut out."""
    empty = np.array([bool(m.get("empty")) for m in measures])
    if empty.all():
        return [int(i) for i in np.nonzero(empty)[0]]
    area = np.array([0.0 if m.get("empty") else m["area"] for m in measures])
    return [int(i) for i in np.nonzero(empty | (area < small * np.median(area[~empty])))[0]]


def suspects(measures, jump=0.45):
    """Frames whose measures are not to be believed: with no figure, or with an area far from that of the frames
    around them that have one (a run of empty frames between shots does not make its neighbours look large)."""
    n = len(measures)
    empty = np.zeros(n, bool)
    empty[figureless(measures)] = True
    area = np.array([0.0 if m.get("empty") else m["area"] for m in measures])
    if n < 5 or empty.all():
        return [int(i) for i in np.nonzero(empty)[0]]
    typical = _rolling(_fill(area, ~empty), 6, np.median)
    odd = empty | (np.abs(area / np.maximum(typical, 1e-9) - 1.0) > jump)
    return [int(i) for i in np.nonzero(odd)[0]]


def agreement(frame, alpha, colour, distance=30.0):
    """How a cut agrees with a flat backdrop it was drawn on: the share of what differs from the backdrop that the
    cut keeps, and the share of the cut that lies on the backdrop (every other pixel looked at). The backdrop is
    what reaches the frame's edge: a grey fold of a white robe, inside the figure, is not backdrop."""
    rgb = frame[::2, ::2, :3].astype(np.float32)
    drawn = np.linalg.norm(rgb - np.asarray(colour, np.float32)[:3], axis=2) > distance
    closed = cv2.dilate(drawn.astype(np.uint8), np.ones((5, 5), np.uint8))
    _count, labels = cv2.connectedComponents((closed == 0).astype(np.uint8), connectivity=4)
    edge = np.unique(np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]]))
    backdrop = np.isin(labels, edge[edge > 0])
    solid = alpha[::2, ::2] >= 0.5
    return float((solid & drawn).sum() / max(1, drawn.sum())), float((solid & backdrop).sum() / max(1, solid.sum()))


def disagreeing(agreements, skip=(), kept=0.5, spill=0.25):
    """Frames whose cut does not follow what was drawn: it lost much of the figure, or kept much backdrop."""
    skip = set(skip)
    return [k for k, (got, extra) in enumerate(agreements) if k not in skip and (got < kept or extra > spill)]


def plan_alignment(measures, out_h, mode="centre", baseline=0.95, figure=0.84, margin=0.02, smooth=3.0,
                   ground=12, scale="steady", out_w=None, bad=()):
    """Where every frame goes in the chain's frames: a list of (scale, dx, dy) that maps a source pixel to an
    output pixel (x' = s*x + dx), and the output width.
    `mode`: 'keep' (the frames as they are, only fitted); 'centre' (the torso held in the middle on one ground
    line: the chain's axis runs straight through the body, and a shaking camera is taken out with it);
    'sway' (the figure held in the middle on average, its quicker sway kept: what changes faster than
    `smooth` frames stays); 'lock' is 'centre';
    `scale`: 'steady' (one size for the whole chain, so a figure that grows is seen to grow) or 'follow'
    (every frame's figure brought to the same height); `ground`: frames on each side over which the lowest
    point counts as the ground (a jump stays a jump); `bad`: frames whose measures are not to be believed."""
    n = len(measures)
    if n == 0:
        return [], out_w or out_h
    src_w, src_h = measures[0]["width"], measures[0]["height"]
    good = np.array([not m.get("empty") and i not in set(bad) for i, m in enumerate(measures)])
    if mode == "keep" or not good.any():
        s = min(out_h / src_h, (out_w or out_h * src_w / src_h) / src_w)
        width = out_w or int(round(src_w * s / 16)) * 16 or 16
        dx, dy = (width - src_w * s) / 2, (out_h - src_h * s) / 2
        return [(s, dx, dy)] * n, width

    def series(key):
        return _fill([m.get(key, 0.0) if not m.get("empty") else 0.0 for m in measures], good)

    top, bottom = series("top"), series("feet_y")
    body = _fill([m.get("torso_x", m.get("cx", 0.0)) if not m.get("empty") else 0.0 for m in measures], good)
    left, right = series("left"), series("right")
    ground_line = _rolling(bottom, int(ground), np.max) if ground else bottom
    ground_line = _smooth(ground_line, max(1.0, ground / 2.0)) if mode != "lock" else ground_line
    tall = np.maximum(1.0, ground_line - top)
    if scale == "follow":
        wanted = figure * out_h / _smooth(tall, max(1.0, smooth * 3))
        roomy = (baseline - margin) * out_h / np.maximum(1.0, _smooth(tall, max(1.0, smooth * 3)))
        s = np.minimum(wanted, roomy)
    else:
        typical = float(np.median(tall[good]))
        highest = float(np.percentile(tall[good], 99))
        s = np.full(n, min(figure * out_h / typical, (baseline - margin) * out_h / highest))
    # held: the body's own place, frame by frame (any smoothing of it would leave the quick shake in the picture;
    # frames whose cut measured it wrong are already filled in from their neighbours); with its sway: only the
    # slow drift taken away
    centre = _smooth(body, smooth) if mode == "sway" else body
    dy = baseline * out_h - s * ground_line
    # the width that holds the figure's reach (arms, a step to the side) in 98 frames out of 100
    reach = np.maximum(np.abs(s * (left - centre)), np.abs(s * (right - centre)))
    if out_w is None:
        half = float(np.percentile(reach[good], 98)) * 1.06 + margin * out_h
        out_w = int(np.clip(round(2 * half / 16) * 16, 16 * round(0.36 * out_h / 16), 16 * round(1.6 * out_h / 16)))
    dx = out_w / 2.0 - s * centre
    return [(float(s[i]), float(dx[i]), float(dy[i])) for i in range(n)], int(out_w)


def render_frame(frame, alpha, transform, out_w, out_h, background=None, straight=False):
    """A source frame and its cut made into one of the chain's frames (RGBA uint8). The colours are worked
    out (`unmix`) at the size the frame will have, and only around the figure: that is where they matter,
    and it is several times faster than doing the whole source picture."""
    s, dx, dy = transform
    h, w = alpha.shape
    shrink = 1.0
    if s < 0.98:
        size = (max(1, int(round(w * s))), max(1, int(round(h * s))))
        frame = cv2.resize(np.ascontiguousarray(frame[..., :3]), size, interpolation=cv2.INTER_AREA)
        alpha = cv2.resize(alpha, size, interpolation=cv2.INTER_AREA)
        shrink = 0.5 * (size[0] / w + size[1] / h)
    scale = s / shrink                          # from the (smaller) picture's pixels to the chain frame's
    h, w = alpha.shape
    rows = np.nonzero(alpha.max(axis=1) > 0)[0]
    cols = np.nonzero(alpha.max(axis=0) > 0)[0]
    if not len(rows):
        return np.zeros((int(out_h), int(out_w), 4), np.uint8)
    margin = int(0.03 * h) + 4
    y0, y1 = max(0, rows[0] - margin), min(h, rows[-1] + margin + 1)
    x0, x1 = max(0, cols[0] - margin), min(w, cols[-1] + margin + 1)
    piece, piece_alpha = frame[y0:y1, x0:x1], alpha[y0:y1, x0:x1]
    colours = piece[..., :3].astype(np.float32) / 255.0 if straight else unmix(piece, piece_alpha, background)
    # the piece's pixel (0, 0) is the picture's (x0, y0), which lands at scale*x0 + dx
    return place(colours, piece_alpha, (scale, dx + scale * x0, dy + scale * y0), out_w, out_h)


def place(rgb01, alpha, transform, out_w, out_h):
    """A frame moved and scaled into the chain's frame. Colours are carried premultiplied, so the edges do
    not darken; returns RGBA uint8 (straight alpha)."""
    s, dx, dy = transform
    pre = np.dstack([rgb01 * alpha[..., None], alpha]).astype(np.float32)
    h, w = alpha.shape
    if abs(s - 1.0) > 1e-3:
        size = (max(1, int(round(w * s))), max(1, int(round(h * s))))
        pre = cv2.resize(pre, size, interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC)
        sx, sy = size[0] / w, size[1] / h
    else:
        sx = sy = 1.0
    matrix = np.float32([[s / sx, 0, dx], [0, s / sy, dy]])
    out = cv2.warpAffine(pre, matrix, (int(out_w), int(out_h)), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT,
                         borderValue=(0, 0, 0, 0))
    a = np.clip(out[..., 3], 0.0, 1.0)
    rgb = out[..., :3] / np.maximum(a[..., None], 1e-4)
    a = np.where(a < 1.5 / 255, 0.0, a)                         # resampling must not bring the fog back
    return np.dstack([np.clip(rgb, 0, 1) * 255, a * 255]).round().astype(np.uint8)

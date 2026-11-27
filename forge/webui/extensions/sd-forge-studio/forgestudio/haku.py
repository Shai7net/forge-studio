"""HakuImg's effects (KohakuBlueleaf/a1111-sd-webui-haku-img, Apache-2.0) on a layer of the image workspace.

The image workspace's effects panel (javascript/forge-studio-r-image-fx.js) sends the active layer as a PNG with an
effect and its settings; the picture comes back with the effect, made by HakuImg's own functions (the ones its tab in
Forge uses), so a setting gives the same picture in both places. HakuImg has to be installed as a Forge extension:
it is found among Forge's extensions by its `hakuimg` package, nothing of it is copied here.

The layer's transparency is kept: the colours go through the effect and the original alpha is put back, except for
the effects that move pixels (flip, lens) or make their own (pixelize), which get the whole RGBA picture.
"""

import io
import os
import sys
import threading

_lock = threading.Lock()
_modules = {}

# name -> the settings it takes, in HakuImg's order, with HakuImg's defaults (the page builds its sliders from these)
EFFECTS = {
    "color": [("bright", 0), ("contrast", 0), ("sat", 0), ("temp", 0), ("hue", 0), ("gamma", 1.0), ("exposure", 0),
              ("hdr", 0), ("noise", 0), ("sharpness", 0), ("vignette", 0)],
    "curve": [("all", None), ("r", None), ("g", None), ("b", None)],
    "blur": [("radius", 8)],
    "sketch": [("kernel", 0), ("sigma", 1.4), ("k_sigma", 1.6), ("eps", -0.03), ("phi", 10), ("gamma", 1.0),
               ("color", "gray"), ("scale", False)],
    "pixelize": [("colors", 16), ("dot", 8), ("smooth", 0), ("outline", 5), ("mode", "kmeans")],
    "pixeloe": [("pixel", 4), ("thickness", 2), ("colors", 256), ("mode", "contrast"), ("quant", "kmeans"),
                ("dither", "ordered"), ("color_quant", False), ("no_upscale", False)],
    "outline": [("pixel", 4), ("thickness", 3)],
    "glow": [("range", 16), ("strength", 1.0), ("mode", "BS")],
    "chromatic": [("strength", 1.0), ("blur", False)],
    "lens": [("k1", 0.0), ("k2", 0.0)],
    "tiltshift": [("focus", 0.0), ("dof", 60)],
    "flip": [("axis", "horizontal")],
}
MOVES_PIXELS = {"flip", "lens"}


def folder():
    """HakuImg's extension folder (the one holding its `hakuimg` package), or None when it is not installed."""
    try:
        from modules import extensions
        candidates = [e.path for e in extensions.extensions if e.enabled]
    except Exception:
        candidates = []
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    candidates.append(os.path.join(here, "a1111-sd-webui-haku-img"))
    for path in candidates:
        if os.path.isfile(os.path.join(path, "hakuimg", "blend.py")):
            return path
    return None


def available():
    return {"installed": folder() is not None, "effects": {k: [list(x) for x in v] for k, v in EFFECTS.items()}}


def _module(name):
    with _lock:
        if name not in _modules:
            path = folder()
            if path is None:
                raise ValueError("HakuImg is not installed (Extensions → Install from URL: "
                                   "https://github.com/KohakuBlueleaf/a1111-sd-webui-haku-img)")
            if path not in sys.path:
                sys.path.append(path)
            import importlib
            _modules[name] = importlib.import_module("hakuimg." + name)
        return _modules[name]


def _settings(effect, params):
    out = {}
    for key, default in EFFECTS[effect]:
        value = params.get(key, default)
        if isinstance(default, bool):
            value = bool(value)
        elif isinstance(default, (int, float)) and default is not None:
            value = type(default)(float(value)) if isinstance(default, float) else int(round(float(value)))
        elif isinstance(default, str):
            value = str(value)
        out[key] = value
    return out


def _curve(rgb, s):
    """HakuImg's tone curves (its own spline through the points), as a table of the 256 values: the spline on every
    pixel took ~0.9 s for 1024x768, the table ~10 ms."""
    import numpy as np
    curve = _module("curve")
    levels = np.arange(256, dtype=np.float64)

    def table(points):
        if not points:
            return None
        return np.clip(curve.make_curve([p[0] for p in points], [p[1] for p in points])(levels), 0, 255)

    out = np.array(rgb)
    whole = table(s.get("all"))
    for i, ch in enumerate("rgb"):
        t = table(s.get(ch))
        lut = levels if t is None else t
        if whole is not None:   # HakuImg runs the channel's curve first, then the one for all
            lut = whole[np.clip(lut, 0, 255).astype(np.uint8)]
        out[:, :, i] = np.clip(lut, 0, 255).astype(np.uint8)[out[:, :, i]]
    return out


def _device():
    """PixelOE on the graphics card when there is one (8 s on the processor for 1024x768); the picture is small."""
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "default"
    except Exception:
        return "default"


def apply(png, effect, params):
    """`png`: the layer (PNG bytes, RGBA); returns the picture with `effect` (PNG bytes, the same size)."""
    import numpy as np
    from PIL import Image
    if effect not in EFFECTS:
        raise ValueError(f"unknown effect: {effect}")
    s = _settings(effect, params or {})
    src = Image.open(io.BytesIO(png)).convert("RGBA")
    size = src.size
    alpha = src.getchannel("A")
    rgb = src.convert("RGB")
    whole = src if effect in MOVES_PIXELS else rgb
    if effect == "color":
        out = _module("color").run(rgb, s["bright"], s["contrast"], s["sat"], s["temp"], s["hue"], s["gamma"],
                                   s["exposure"], s["hdr"], s["noise"], s["sharpness"], s["vignette"])
    elif effect == "curve":
        out = _curve(rgb, s)
    elif effect == "blur":
        out = _module("blur").run(rgb, s["radius"])
    elif effect == "sketch":
        # OpenCV's Gaussian kernel must be odd (0 = from sigma); HakuImg's own slider lets an even one through and fails
        kernel = s["kernel"] + 1 if s["kernel"] > 0 and s["kernel"] % 2 == 0 else s["kernel"]
        out = _module("sketch").run(rgb, kernel, s["sigma"], s["k_sigma"], s["eps"], s["phi"], s["gamma"],
                                    s["color"], s["scale"])
    elif effect == "pixelize":
        out = _module("pixel").run(src, s["colors"], s["dot"], s["smooth"], s["outline"], s["mode"])
    elif effect == "pixeloe":
        out = _module("pixeloe").run(rgb, s["pixel"], s["thickness"], s["colors"], s["mode"], s["quant"], s["dither"],
                                     _device(), s["color_quant"], s["no_upscale"])
    elif effect == "outline":
        out = _module("outline_expansion").run(rgb, s["pixel"], s["thickness"], _device())
    elif effect == "glow":
        out = _module("neon").run(rgb, s["range"], s["strength"], s["mode"])
    elif effect == "chromatic":
        out = _module("chromatic").run(rgb, s["strength"], s["blur"])
    elif effect == "lens":
        out = _module("lens_distortion").run(whole, s["k1"], s["k2"])
    elif effect == "tiltshift":
        out = _module("tilt_shift").run(rgb, s["focus"], s["dof"])
    else:  # flip
        out = _module("flip").run(whole, s["axis"])
    if not isinstance(out, Image.Image):
        out = Image.fromarray(np.asarray(out).astype(np.uint8))
    if out.size != size:   # chromatic cuts to an odd size, pixel art may come back small
        out = out.resize(size, Image.NEAREST if effect in ("pixelize", "pixeloe", "outline") else Image.LANCZOS)
    if effect in MOVES_PIXELS or effect == "pixelize":
        out = out.convert("RGBA")
        if effect == "pixelize":   # its alpha is cut to on/off; where the layer was see-through it stays so
            out.putalpha(Image.fromarray(np.minimum(np.asarray(out.getchannel("A")), np.asarray(alpha))))
    else:
        out = out.convert("RGB").convert("RGBA")
        out.putalpha(alpha)
    buf = io.BytesIO()
    out.save(buf, "PNG", compress_level=1)
    return buf.getvalue()

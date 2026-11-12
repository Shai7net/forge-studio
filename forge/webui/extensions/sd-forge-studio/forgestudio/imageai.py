"""The image workspace's AI tools: what the page cannot do by itself.

- the subject (BiRefNet-lite, the same matte as the editor's cut-outs): a selection, or the background taken away;
- an object by a click (SAM, Segment Anything, from Forge's models/sam: the click points in, Alt-clicks out);
- what is selected taken away (LaMa from the Cleaner extension: the hole filled from what is around it).

Each runs under Forge's queue with Forge's models off the card, and answers with a PNG in the shot's folder (a mask
for the first two, the picture with the object gone for the third) that the page turns into a selection or a layer.
"""

import importlib.util
import io
import os
import uuid

import numpy as np

from . import frames, imagedoc, paths
from .sound import _on_card

_sam = {}


def _out(name, shot, image, prefix):
    folder = os.path.join(imagedoc._dir(name, shot), "gen")
    os.makedirs(folder, exist_ok=True)
    f = f"{prefix}-{uuid.uuid4().hex[:8]}.png"
    image.save(os.path.join(folder, f), "PNG")
    return "gen/" + f


def _rgba(data):
    from PIL import Image
    return Image.open(io.BytesIO(data)).convert("RGBA")


# ---- the subject ----

def subject(name, shot, picture):
    """The subject of `picture` (PNG bytes, RGBA: see-through parts count as background) as a mask."""
    return frames._task("subject", _subject, name, shot, picture)


def _subject(task, name, shot, picture):
    from PIL import Image
    from . import layers
    im = _rgba(picture)
    rgb = Image.new("RGB", im.size, (255, 255, 255))
    rgb.paste(im, mask=im.split()[3])
    task["text"] = "waiting for the graphics card"

    def run():
        task["text"] = "finding the subject"
        return layers.Matte()(np.asarray(rgb))
    alpha = _on_card(run)
    alpha = alpha * (np.asarray(im.split()[3], dtype=np.float32) / 255.0)
    mask = Image.fromarray((np.clip(alpha, 0, 1) * 255).astype(np.uint8), "L")
    return {"file": _out(name, shot, mask, "subject"), "progress": 1.0, "text": "the subject is selected"}


# ---- an object by a click ----

def _sam_model():
    """SAM ViT-B (or the first SAM model there is) from Forge's models/sam, kept between clicks."""
    if "predictor" in _sam:
        return _sam["predictor"]
    import torch
    from segment_anything import SamPredictor, sam_model_registry
    # where the Segment Anything extension keeps them: webui/models/sam, or its own models/sam
    folders = [os.path.join(paths.WEBUI, "models", "sam"),
               os.path.join(paths.WEBUI, "extensions", "sd-webui-segment-anything", "models", "sam")]
    choices = [("vit_b", "sam_vit_b_01ec64.pth"), ("vit_l", "sam_vit_l_0b3195.pth"), ("vit_h", "sam_vit_h_4b8939.pth")]
    found = [(kind, os.path.join(f, file)) for kind, file in choices for f in folders if os.path.isfile(os.path.join(f, file))]
    if not found:
        raise RuntimeError("no SAM model (sam_vit_b_01ec64.pth in webui\\models\\sam or in the Segment Anything "
                           "extension's models\\sam)")
    kind, path = found[0]
    model = sam_model_registry[kind](checkpoint=path).eval()   # kept on the processor between clicks
    _sam["predictor"] = SamPredictor(model)
    return _sam["predictor"]


def object_at(name, shot, picture, points):
    """The object under `points` ([[x, y, 1 in / 0 out], ...] in the picture's pixels) as a mask."""
    if not points:
        raise ValueError("click on the object")
    return frames._task("object", _object_at, name, shot, picture, [[float(x), float(y), int(k)] for x, y, k in points])


def _object_at(task, name, shot, picture, points):
    from PIL import Image
    im = _rgba(picture)
    rgb = Image.new("RGB", im.size, (255, 255, 255))
    rgb.paste(im, mask=im.split()[3])
    task["text"] = "waiting for the graphics card"

    def run():
        import torch
        task["text"] = "finding the object"
        predictor = _sam_model()
        if torch.cuda.is_available():
            predictor.model.to("cuda")
        try:
            with torch.inference_mode():
                predictor.set_image(np.asarray(rgb))
                masks, scores, _ = predictor.predict(point_coords=np.array([[x, y] for x, y, _k in points], dtype=np.float32),
                                                     point_labels=np.array([k for _x, _y, k in points], dtype=np.int32),
                                                     multimask_output=len(points) == 1)
        finally:
            predictor.reset_image()
            # the model goes back to the processor, so generating has the card
            predictor.model.to("cpu")
            torch.cuda.empty_cache()
        # one click gives three masks (a small part, a part, the whole): like a photo editor's object selection, the
        # largest of those scored nearly as well as the best (the best alone picked a dog's snout, 2026-11-12)
        good = [i for i in range(len(scores)) if scores[i] >= float(np.max(scores)) - 0.08]
        best = max(good, key=lambda i: int(masks[i].sum()))
        return masks[best]
    mask = _on_card(run)
    out = Image.fromarray((mask.astype(np.uint8) * 255), "L")
    return {"file": _out(name, shot, out, "object"), "progress": 1.0, "text": "the object is selected"}


# ---- taking away what is selected ----

def _lama():
    path = os.path.join(paths.WEBUI, "extensions", "sd-webui-cleaner", "scripts", "lama.py")
    if not os.path.isfile(path):
        raise RuntimeError("the Cleaner extension (LaMa) is not installed")
    spec = importlib.util.spec_from_file_location("forge_studio_cleaner_lama", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def erase(name, shot, picture, mask):
    """`picture` with what `mask` covers taken away and filled from around it (LaMa)."""
    return frames._task("erase", _erase, name, shot, picture, mask)


def _erase(task, name, shot, picture, mask):
    from PIL import Image, ImageFilter
    im = _rgba(picture)
    rgb = Image.new("RGB", im.size, (255, 255, 255))
    rgb.paste(im, mask=im.split()[3])
    m = Image.open(io.BytesIO(mask)).convert("L").resize(im.size)
    # a little wider than the selection: an object's own edge and shadow go with it
    m = m.point(lambda v: 255 if v >= 64 else 0).filter(ImageFilter.MaxFilter(9))
    task["text"] = "waiting for the graphics card"

    def run():
        task["text"] = "filling the hole (LaMa)"
        out = _lama().clean_object(rgb, m)
        result = out[0] if isinstance(out, (list, tuple)) else out
        if result is None:
            raise RuntimeError("LaMa failed (see Forge's console)")
        return result
    result = _on_card(run).convert("RGB")
    if result.size != im.size:
        result = result.resize(im.size, Image.LANCZOS)
    return {"file": _out(name, shot, result, "erased"), "progress": 1.0, "text": "taken away"}

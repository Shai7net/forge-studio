"""Automatic selection: the user clicks on a door, and the door is selected.

Segment Anything is asked about the picture the user is looking at (an ordinary view, not the stretched
panorama: the model was trained on ordinary pictures). It answers with up to three shapes for the same
clicks, a small part, the thing, and the thing with what it belongs to; the page lets the user step
through them.
"""

import os
import threading

import numpy as np

# the large model first: it follows edges best and still answers in about a second
SAM_MODELS = [("vit_l", "sam_vit_l_0b3195.pth"), ("vit_h", "sam_vit_h_4b8939.pth"), ("vit_b", "sam_vit_b_01ec64.pth")]

_lock = threading.Lock()
_predictor = None
_embedded = None      # the picture the predictor holds right now


def checkpoint():
    from modules.paths import data_path, models_path
    folders = [os.path.join(models_path, "sam"),
               os.path.join(data_path, "extensions", "sd-webui-segment-anything", "models", "sam")]
    for kind, name in SAM_MODELS:
        for folder in folders:
            if os.path.isfile(os.path.join(folder, name)):
                return kind, os.path.join(folder, name)
    return None


def available():
    if checkpoint() is None:
        return False
    try:
        import segment_anything  # noqa: F401
        return True
    except Exception:
        return False


def cut(image, box, points):
    """The outline of one thing: the thing inside `box` (x0, y0, x1, y1) that the `points` [(x, y)] lie on.
    Returns a bool mask. This is how a drawn character is lifted off its background."""
    wanted = [(p[0], p[1], True) for p in points]
    shapes = select(image, wanted, box=np.array(box, np.float32), several=False)
    if not shapes:
        return np.zeros(image.shape[:2], bool)
    return shapes[0][0].astype(bool)


def select(image, points, picture_key=None, box=None, several=True):
    """`image`: RGB array. `points`: [(x, y, wanted)], in pixels; wanted False marks what to leave out.
    Returns [(mask, score)], smallest shape first."""
    global _predictor, _embedded
    import torch
    from ldm_patched.modules import model_management
    found = checkpoint()
    if found is None:
        raise RuntimeError("no Segment Anything model was found in models/sam")
    device = model_management.get_torch_device()
    with _lock:
        if _predictor is None:
            from segment_anything import SamPredictor, sam_model_registry
            kind, path = found
            _predictor = SamPredictor(sam_model_registry[kind](checkpoint=path).eval())
            _embedded = None
        sam = _predictor.model
        try:
            if picture_key is None or picture_key != _embedded:
                # the picture is read once; more clicks on the same view only ask the small part of the model
                model_management.free_memory(5 * 1024 ** 3, device)
                sam.to(device)
                _predictor.set_image(image)
                _embedded = picture_key
            else:
                sam.to(device)
                _predictor.features = _predictor.features.to(device)
            coords = np.array([[p[0], p[1]] for p in points], np.float32)
            labels = np.array([1 if p[2] else 0 for p in points], np.int32)
            masks, scores, _ = _predictor.predict(point_coords=coords, point_labels=labels, box=box, multimask_output=several)
        finally:
            sam.cpu()
            if getattr(_predictor, "features", None) is not None:
                _predictor.features = _predictor.features.cpu()
            if torch.cuda.is_available():
                model_management.soft_empty_cache()
    found_shapes = [(masks[i], float(scores[i])) for i in range(len(masks)) if masks[i].any()]
    found_shapes.sort(key=lambda shape: int(shape[0].sum()))
    return found_shapes


def forget():
    """Lets go of the model (it is rebuilt on the next click)."""
    global _predictor, _embedded
    with _lock:
        _predictor = None
        _embedded = None

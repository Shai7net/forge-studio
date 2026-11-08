"""Generating a chain's frames with Deforum, set up for the shot.

The frames of a chain made from a video are the motion. They are laid on a flat backdrop and given to Deforum
as its Video Input and to ControlNet, so every frame it draws has the figure exactly where the motion chain has
it, on a backdrop whose colour is known. And:
- Deforum's own camera stands still (the 3D camera is Time Strata's). DeforumationQT is left out of these frames:
  it would move the camera and set the steps. Its switch is turned off in memory only for the job and put back
  when the job ends; its file is never written. (Its pan and rotation still steer the trail: see dqt.py.)
- The seed is fixed, so neighbouring frames, which stand a few centimetres apart in the chain, share their
  texture and the chain reads as one body.
- Scenes (as in Infinite Zoom): each starts at a second of the motion with its own prompt, and may fill the
  figure with its own texture and give it its own look picture; one scene blends into the next over `blend`
  seconds, its prompt by Deforum's composable weights ("a :0.7 AND b :0.3"), its texture and look by a crossfade.
- ControlNet units hold the drawing to the motion: the pose, the depth, the edges, the silhouette (QR Monster)
  and the look of a picture (IP-Adapter), each with its own weight and the part of the steps it acts in.
- While Deforum draws, every new frame is cut by the backdrop's colour and shown in the 3D view at once.
- The cut takes the backdrop away by its colour, keeps the motion figure's body solid and nothing far from it.
- Deforum writes into the chain's own folder, not into the usual outputs.
"""

import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid

import cv2
import numpy as np
from PIL import Image

from . import chain, compat, cut, jobs, media, store, upscale

DEFAULTS = {
    "prompt": "", "stages": [], "scenes": [], "blend": 1.0,
    "negative": "blurry, deformed, extra limbs, text, watermark, nsfw, nude",
    "prefix": "<lora:LCM_SD15:1>", "suffix": "", "sampler": "LCM", "steps": 8, "cfg": 1.5, "strength": 0.3, "pose": 0.9,
    "seed": 1234, "background": "#808080", "first": 0, "last": None, "every": 1, "checkpoint": None,
    "units": None,          # ControlNet units, {kind: {on, weight, start, end}} (see UNITS); None: the pose alone
    "fill": None,           # {"amount": 0..1, "texture": path}: the figure filled with a texture before it is drawn
    "look": None,           # a picture whose look IP-Adapter gives every frame (a scene may have its own)
    "upscale": None,        # the drawn frames made larger with Real-ESRGAN before their cut ("model:scale")
    "steady": None,         # {"on", "weight"}: one anchor frame drawn first per scene gives the scene its look (IP-Adapter)
    "redraw": None,         # frames of the drawn chain to draw again (the rest kept); see _redraw
    "redraw_seed": None,    # a new seed for those frames, moved past every seed the chain was drawn with (redraw_seed)
    "skip_empty": True,     # motion frames with no figure (a cut between shots, a flash) are not drawn
}

# What each ControlNet unit sees and which model and preprocessor it takes (found among the ones installed).
UNITS = {
    "pose": {"on": True, "weight": 0.9, "start": 0.0, "end": 1.0},
    "depth": {"on": False, "weight": 0.45, "start": 0.0, "end": 0.6},
    "edges": {"on": False, "weight": 0.35, "start": 0.0, "end": 0.5},
    "shape": {"on": False, "weight": 0.6, "start": 0.0, "end": 0.8},
    "look": {"on": False, "weight": 0.6, "start": 0.0, "end": 1.0},
}
_KINDS = {
    "pose": {"words": ("openpose",), "prefer": ("v11",), "modules": ("dw_openpose_full", "openpose_full", "openpose"), "sees": "motion"},
    "depth": {"words": ("depth",), "prefer": ("v11",), "modules": ("depth_midas", "depth_anything", "depth_zoe", "depth_leres"), "sees": "motion"},
    "edges": {"words": ("canny",), "prefer": ("v11",), "modules": ("canny",), "sees": "motion"},
    "shape": {"words": ("qrcode", "qrmonster", "monster"), "prefer": ("v20", "v2"), "modules": ("None", "none"), "sees": "shape"},
    "look": {"words": ("ip-adapter", "ipadapter", "ip_adapter"), "prefer": ("ip-adapter-plus_sd15", "plus"),
             "modules": ("CLIP-ViT-H (IPAdapter)",), "sees": "look"},
}
_NOT_SD15 = ("sdxl", "_xl", "-xl", "xl_", "pulid", "faceid", "flux", "sd3")
_VIDEO = (".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v", ".gif")

# The backdrop is laid flat by Time Strata itself, so every scene asks for it to stay flat: a drawn floor or a
# shadow under the feet is cut as part of the figure in some frames and not in others, and in the stack that
# turns into a grey band at the chain's feet (measured on the first real render: almost all the fog was in the
# bottom eighth of the frames).
BACKDROP_WORDS = "isolated on a plain flat grey background"
NOT_A_ROOM = "floor, shadow, reflection, horizon line, gradient background, room, scenery"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_TAG = re.compile(r"<[^<>]+>")


# --- Deforum, found and borrowed ----------------------------------------------------------------------------------

def _deforum_dir():
    return compat.deforum_dir()


def _ensure_paths():
    folder = _deforum_dir()
    if not folder:
        raise jobs.JobError("A compatible Deforum extension is not installed or enabled")
    for path in (os.path.join(folder, "scripts"), os.path.join(folder, "scripts", "deforum_helpers", "src")):
        if path not in sys.path:
            sys.path.append(path)
    return folder


def _switch():
    """Deforum's DeforumationQT switch module, as Deforum itself loaded it."""
    for name, module in list(sys.modules.items()):
        if name.endswith("deforumation_switch") and hasattr(module, "_load"):
            return module
    try:
        from deforum_helpers import deforumation_switch
        return deforumation_switch
    except Exception:
        return None


class _WithoutDeforumation:
    """DeforumationQT off for one render, in memory only; afterwards it is exactly as it was."""

    def __enter__(self):
        self.module = _switch()
        self.state = self.module._load() if self.module else None
        self.had = "enabled" in self.state if self.state is not None else False
        self.before = self.state.get("enabled") if self.state is not None else None
        if self.state is not None:
            self.state["enabled"] = False
        return self

    def __exit__(self, *_):
        if self.state is not None:
            if self.had:
                self.state["enabled"] = self.before
            else:
                self.state.pop("enabled", None)


def last_settings():
    """The sampler, steps and CFG of the user's newest Deforum render (its settings file beside its frames)."""
    try:
        from modules import shared
        folders = [shared.opts.outdir_samples, shared.opts.outdir_img2img_samples]
    except Exception:
        folders = []
    newest, when = None, 0
    for folder in [f for f in folders if f]:
        for path in glob.glob(os.path.join(folder, "Deforum_*", "*_settings.txt")):
            moment = os.path.getmtime(path)
            if moment > when:
                newest, when = path, moment
    if not newest:
        return {}
    try:
        with open(newest, "r", encoding="utf8") as file:
            data = json.load(file)
    except Exception:
        return {}
    cfg = str(data.get("cfg_scale_schedule") or "")
    found = {"sampler": data.get("sampler"), "steps": data.get("steps"), "checkpoint": data.get("sd_model_name"), "from": newest}
    try:
        found["cfg"] = float(cfg.split("(")[1].split(")")[0])
    except Exception:
        pass
    return {k: v for k, v in found.items() if v is not None}


def _pick(names, kind):
    """The installed ControlNet model for a unit (an SD 1.5 one), or None."""
    spec = _KINDS[kind]
    found = [n for n in names if any(w in n.lower().replace(" ", "") for w in spec["words"])
             and not any(x in n.lower() for x in _NOT_SD15)]
    found.sort(key=lambda n: (min([i for i, p in enumerate(spec["prefer"]) if p in n.lower()] or [len(spec["prefer"])]), len(n)))
    return found[0] if found else None


def available():
    try:
        from lib_controlnet import global_state
        names = list(global_state.get_all_controlnet_names())
        modules = global_state.get_all_preprocessor_names() if hasattr(global_state, "get_all_preprocessor_names") else []
    except Exception:
        names, modules = [], []
    units = {}
    for kind, spec in _KINDS.items():
        # (Forge's ControlNet calls its "no preprocessor" None; the older extension called it none)
        module = next((m for m in spec["modules"] if m in modules), spec["modules"][0])
        units[kind] = {"model": _pick(names, kind), "module": module}
    try:
        from modules import sd_models, shared
        checkpoints = sorted({info.name for info in sd_models.checkpoints_list.values()}, key=str.lower)
        loaded = shared.opts.data.get("sd_model_checkpoint")
    except Exception:
        checkpoints, loaded = [], None
    return {"deforum": bool(_deforum_dir()), "pose_model": units["pose"]["model"], "pose_preprocessor": units["pose"]["module"],
            "units": units, "checkpoints": checkpoints, "checkpoint": loaded, "last": last_settings()}


# --- scenes --------------------------------------------------------------------------------------------------------

def scenes_of(options, seconds_of):
    """The scenes as [{from (s), text, texture, look}], sorted. An older project's stages (`at`: a share of the
    chain, 0..1, or above 1 a frame number) are read as scenes; `seconds_of(at)` turns a stage's place into
    seconds. No scenes: the one prompt everywhere."""
    scenes = [dict(s) for s in (options.get("scenes") or []) if str(s.get("text") or "").strip()]
    if not scenes:
        scenes = [{"from": seconds_of(float(s.get("at") or 0)), "text": s["text"]}
                  for s in (options.get("stages") or []) if str(s.get("text") or "").strip()]
    if not scenes:
        scenes = [{"from": 0.0, "text": options.get("prompt") or "a person"}]
    for s in scenes:
        s["from"] = max(0.0, float(s.get("from") or 0))
    scenes.sort(key=lambda s: s["from"])
    scenes[0]["from"] = 0.0
    return scenes


def weights_at(scenes, t, blend):
    """[(scene index, weight)] at second t: one scene, or two while one blends into the next (the blend is centred
    on the next scene's start; smoothstep, so it starts and ends gently)."""
    i = 0
    while i + 1 < len(scenes) and scenes[i + 1]["from"] <= t:
        i += 1
    half = max(0.0, float(blend or 0)) / 2
    if half > 0:
        if i + 1 < len(scenes) and t > scenes[i + 1]["from"] - half:
            f = (t - (scenes[i + 1]["from"] - half)) / (2 * half)
            f = f * f * (3 - 2 * f)
            return [(i, 1 - f), (i + 1, f)]
        if i > 0 and t < scenes[i]["from"] + half:
            f = (t - (scenes[i]["from"] - half)) / (2 * half)
            f = f * f * (3 - 2 * f)
            return [(i - 1, 1 - f), (i, f)]
    return [(i, 1.0)]


def _prompts(options, scenes, times):
    """Deforum's prompt keyframes {frame: prompt} for the frames' seconds `times`: written where the prompt
    changes, every frame of a blend. LoRA and other <tags> are taken out and said once (twice would load a LoRA
    twice), and every prompt asks for the flat backdrop."""
    tags, seen = [], set()
    for text in [options.get("prefix"), options.get("suffix")] + [s["text"] for s in scenes]:
        for tag in _TAG.findall(str(text or "")):
            if tag not in seen:
                seen.add(tag)
                tags.append(tag)
    prefix = _TAG.sub("", str(options.get("prefix") or "")).strip(" ,")
    suffix = _TAG.sub("", str(options.get("suffix") or "")).strip(" ,")
    negative = ", ".join(p for p in (str(options.get("negative") or "").strip(), NOT_A_ROOM) if p)

    def words(scene):
        text = _TAG.sub("", str(scene["text"] or "")).strip(" ,")
        return ", ".join(p for p in (prefix, text, suffix, BACKDROP_WORDS) if p)

    head = " ".join(tags)
    keyed, last = {}, None
    for n, t in enumerate(times):
        mix = weights_at(scenes, t, options.get("blend"))
        if len(mix) == 1 or mix[1][1] < 0.005:
            body = words(scenes[mix[0][0]])
        elif mix[0][1] < 0.005:
            body = words(scenes[mix[1][0]])
        else:
            body = " AND ".join(f"{words(scenes[i])} :{w:.3f}" for i, w in mix)
        prompt = f"{head} {body} --neg {negative}".strip()
        if prompt != last:
            keyed[str(n)] = prompt
            last = prompt
    return keyed


# --- the motion clip ----------------------------------------------------------------------------------------------

def _hex(text):
    text = str(text or "#808080").lstrip("#")
    return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))


def _up64(n):
    return int((int(n) + 63) // 64 * 64)


def _cover(rgb, W, H):
    """A picture scaled to cover W x H and cut to it, from its middle."""
    h, w = rgb.shape[:2]
    s = max(W / w, H / h)
    size = (max(W, int(round(w * s))), max(H, int(round(h * s))))
    big = cv2.resize(np.ascontiguousarray(rgb[..., :3]), size, interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC)
    x, y = (size[0] - W) // 2, (size[1] - H) // 2
    return big[y:y + H, x:x + W]


class _Texture:
    """A picture or a video a scene fills its figure with (or gives as its look), covering W x H. A video is
    taken at the motion's own time (`at(seconds)`), so it stays in step with the motion also when only every
    n-th frame is drawn, and it loops."""

    def __init__(self, path, W, H):
        self.W, self.H, self.capture, self.last, self.index = W, H, None, None, None
        if os.path.splitext(path)[1].lower() in _VIDEO:
            self.capture = cv2.VideoCapture(path)
            if not self.capture.isOpened():
                raise jobs.JobError(f"this video cannot be read: {os.path.basename(path)}")
            self.fps = self.capture.get(cv2.CAP_PROP_FPS) or 24.0
            self.count = max(1, int(self.capture.get(cv2.CAP_PROP_FRAME_COUNT) or 1))
        else:
            self.last = _cover(np.asarray(Image.open(path).convert("RGB")), W, H)

    def at(self, seconds):
        if self.capture is None:
            return self.last
        k = int(round(max(0.0, seconds) * self.fps)) % self.count
        if k != self.index:
            if self.index is None or k != self.index + 1:
                self.capture.set(cv2.CAP_PROP_POS_FRAMES, k)          # (one after the other is read on, not sought)
            ok, bgr = self.capture.read()
            if ok:
                self.last, self.index = _cover(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB), self.W, self.H), k
        return self.last if self.last is not None else np.full((self.H, self.W, 3), 128, np.uint8)

    def close(self):
        if self.capture is not None:
            self.capture.release()


def _fill(canvas, alpha, texture, amount):
    """The figure filled with a texture, keeping its own light and shade (so Deforum still sees a body): the
    texture is darkened and lightened by the figure's brightness around its mean, then mixed in by `amount`."""
    photo = canvas.astype(np.float32)
    a = alpha.astype(np.float32)[..., None] / 255.0
    lum = photo @ np.float32([0.299, 0.587, 0.114])
    inside = alpha >= 128
    mean = float(lum[inside].mean()) if inside.any() else 128.0
    shade = np.clip(lum / max(mean, 8.0), 0.45, 1.6)[..., None]
    filled = np.clip(texture.astype(np.float32) * shade, 0, 255)
    mixed = photo * (1 - amount) + filled * amount
    return np.clip(photo * (1 - a) + mixed * a, 0, 255).astype(np.uint8)


def _motion(job, project, source, folder, options, scenes, uses, pick=None):
    """The motion chain's frames on the backdrop, padded to Deforum's sizes, as videos: the motion itself (what
    the pose, depth and edges are read from), the picture Deforum starts each frame from (the figure filled with
    its scene's texture, when there is one), where it may paint, the silhouette and the look pictures. Each
    frame's figure (its alpha, padded the same way) is kept as the prior of the cut. `pick`: these motion frames
    instead of first/last/every (the anchors of a steady look, frames drawn again). The motion frames used are
    returned as `picked`: frame n of the drawing is motion frame picked[n]."""
    record = store.read_chain(project, source)
    if not record or not record.get("make"):
        raise jobs.JobError("the motion chain has not been made yet")
    frames = list(range(int(options["first"] or 0), int(options["last"] if options["last"] is not None else record["frames"] - 1) + 1,
                        max(1, int(options["every"] or 1))))
    if pick is not None:
        frames = list(pick)
    frames = [k for k in frames if 0 <= k < record["frames"]]
    skipped = []
    if pick is None and options.get("skip_empty", True) and record.get("measures"):
        # (with no figure to follow - a cut between shots, a flash - Deforum draws plain backdrop)
        empty = set(cut.figureless(record["measures"]))
        skipped = [k for k in frames if k in empty]
        frames = [k for k in frames if k not in empty]
        if skipped and len(frames) < 2:
            raise jobs.JobError("the motion has no figure in the frames chosen")
    if len(frames) < 2:
        raise jobs.JobError("choose at least two frames of the motion chain")
    w, h = record["width"], record["height"]
    W, H = _up64(w), _up64(h)
    left, top = (W - w) // 2, H - h            # the padding goes above, so the ground line keeps its distance from the bottom
    backdrop = _hex(options["background"])
    prior_folder = os.path.join(folder, "prior")
    shutil.rmtree(prior_folder, ignore_errors=True)
    os.makedirs(prior_folder)
    record_fps = float(record.get("fps") or 24)
    fps = record_fps / max(1, int(options["every"] or 1)) if pick is None else record_fps
    times = [k / record_fps for k in frames]

    def writer(path):
        command = [media.ffmpeg(), "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
                   "-s", f"{W}x{H}", "-r", f"{fps:.6f}", "-i", "-", "-c:v", "libx264", "-crf", "12", "-pix_fmt", "yuv420p", path]
        return subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=_NO_WINDOW)

    fill = options.get("fill") or {}
    amount = float(fill.get("amount") or 0)
    textures, looks = {}, {}

    def texture_of(path, store_in):
        if path not in store_in:
            store_in[path] = _Texture(store.resolve(project, path), W, H)
        return store_in[path]

    scene_texture = [s.get("texture") or fill.get("texture") for s in scenes]
    scene_look = [s.get("look") or options.get("look") for s in scenes]
    # a scene's own texture or look plays from the scene's start; the one for every scene, at the motion's time
    texture_from = [s["from"] if s.get("texture") else 0.0 for s in scenes]
    look_from = [s["from"] if s.get("look") else 0.0 for s in scenes]
    filling = amount > 0 and any(scene_texture)
    looking = "look" in uses and any(scene_look)
    paths = {"video": os.path.join(folder, "motion.mp4"), "mask": os.path.join(folder, "mask.mp4")}
    if filling:
        paths["init"] = os.path.join(folder, "init.mp4")
    if "shape" in uses:
        paths["shape"] = os.path.join(folder, "shape.mp4")
    if looking:
        paths["look"] = os.path.join(folder, "look.mp4")
    processes = {key: writer(path) for key, path in paths.items()}
    # the motion, and where Deforum may paint: the motion figure grown a little (a skirt swings out, hair
    # flies); everything else keeps the flat backdrop exactly, so no floor or shadow can be drawn there
    grow = cut._disk(0.025 * H)
    source_folder = os.path.join(store.chain_dir(project, source), record["make"])
    try:
        for n, k in enumerate(frames):
            picture = Image.open(os.path.join(source_folder, f"{k:06d}.webp")).convert("RGBA")
            canvas = Image.new("RGB", (W, H), backdrop)
            canvas.paste(picture, (left, top), picture)
            alpha = Image.new("L", (W, H), 0)
            alpha.paste(picture.split()[3], (left, top))
            alpha.save(os.path.join(prior_folder, f"{n:06d}.png"))
            a = np.asarray(alpha)
            paint = cv2.dilate((a >= 128).astype(np.uint8) * 255, grow)
            motion_rgb = np.asarray(canvas)
            processes["video"].stdin.write(motion_rgb.tobytes())
            processes["mask"].stdin.write(np.repeat(paint[..., None], 3, axis=2).tobytes())
            mix = weights_at(scenes, times[n], options.get("blend"))
            shown = motion_rgb
            if filling:
                # the textures of the scenes this frame belongs to, crossfaded as their prompts are
                pieces = [(texture_of(scene_texture[i], textures).at(times[n] - texture_from[i]), wgt) for i, wgt in mix if scene_texture[i] and wgt > 0]
                if pieces:
                    total = sum(wgt for _, wgt in pieces)
                    texture = sum(t.astype(np.float32) * (wgt / total) for t, wgt in pieces)
                    own = sum(wgt for i, wgt in mix if scene_texture[i])        # a scene without a texture keeps the photo
                    shown = _fill(motion_rgb, a, texture, amount * own)
                processes["init"].stdin.write(shown.tobytes())
            if "shape" in processes:
                shape = cv2.GaussianBlur((a >= 128).astype(np.float32) * 255, (0, 0), 1.5).astype(np.uint8)
                processes["shape"].stdin.write(np.repeat(shape[..., None], 3, axis=2).tobytes())
            if "look" in processes:
                pieces = [(texture_of(scene_look[i], looks).at(times[n] - look_from[i]), wgt) for i, wgt in mix if scene_look[i] and wgt > 0]
                if pieces:
                    total = sum(wgt for _, wgt in pieces)
                    look = sum(t.astype(np.float32) * (wgt / total) for t, wgt in pieces).astype(np.uint8)
                else:
                    look = np.full((H, W, 3), 128, np.uint8)
                processes["look"].stdin.write(look.tobytes())
            if n % 10 == 0:
                job.step(f"laying out the motion: frame {n + 1} of {len(frames)}", 0.01 + 0.04 * n / len(frames),
                         jobs.preview_of(Image.fromarray(shown)))
    finally:
        for texture in list(textures.values()) + list(looks.values()):
            texture.close()
        errors = []
        for key, process in processes.items():
            process.stdin.close()
            error = process.stderr.read().decode("utf8", "replace")
            process.stderr.close()
            if process.wait() != 0:
                errors.append(f"{key}: {error[-300:]}")
        if errors:
            raise jobs.JobError("the motion clip could not be written: " + "; ".join(errors))
    return dict(paths, frames=len(frames), width=W, height=H, fps=fps, record=record, left=left, top=top, times=times,
                init=paths.get("init", paths["video"]), looking=looking, picked=frames, skipped=skipped)


# --- Deforum's settings ------------------------------------------------------------------------------------------

def _units(options):
    """The ControlNet units asked for: {kind: {on, weight, start, end}}, every kind present. Without units (an
    older project) the pose alone, at the old `pose` weight."""
    asked = options.get("units")
    out = {}
    for kind, default in UNITS.items():
        unit = dict(default)
        if isinstance(asked, dict) and isinstance(asked.get(kind), dict):
            unit.update({k: v for k, v in asked[kind].items() if k in default and v is not None})
        elif asked is None and kind == "pose":
            unit["weight"] = float(options.get("pose") if options.get("pose") is not None else default["weight"])
            unit["on"] = unit["weight"] > 0
        unit["on"] = bool(unit["on"]) and float(unit["weight"]) > 0
        unit["start"] = min(max(float(unit["start"]), 0.0), 1.0)
        unit["end"] = min(max(float(unit["end"]), unit["start"]), 1.0)
        out[kind] = unit
    return out


def settings(base, motion, options, outdir_name, scenes=None, units=None, found=None):
    found = found or available()
    scenes = scenes or scenes_of(options, lambda at: at)
    units = units or _units(options)
    data = dict(base)
    data.update({
        "animation_mode": "Video Input", "video_init_path": motion["init"], "extract_nth_frame": 1,
        "extract_from_frame": 0, "extract_to_frame": -1, "overwrite_extracted_frames": True,
        # only the figure's place is painted; the backdrop is put back as it was (white = paint)
        "use_mask_video": True, "video_mask_path": motion["mask"], "invert_mask": False, "overlay_mask": True,
        "mask_overlay_blur": 4, "mask_contrast_adjust": 1.0, "mask_brightness_adjust": 1.0, "fill": 1,
        "full_res_mask": False, "use_noise_mask": False, "use_alpha_as_mask": False,
        "hybrid_motion": "None", "hybrid_composite": "None",
        "max_frames": motion["frames"], "W": motion["width"], "H": motion["height"],
        "seed": int(options["seed"]), "seed_behavior": "fixed", "sampler": options["sampler"], "steps": int(options["steps"]),
        "enable_steps_scheduling": False, "cfg_scale_schedule": f"0: ({float(options['cfg'])})",
        "strength_schedule": f"0: ({float(options['strength'])})", "noise_schedule": "0: (0)",
        "color_coherence": "None", "diffusion_cadence": 1, "fps": max(1, int(round(motion["fps"]))),
        "prompts": _prompts(options, scenes, motion.get("times") or [n / max(1e-6, motion["fps"]) for n in range(motion["frames"])]),
        "animation_prompts": "", "positive_prompts": "", "negative_prompts": "",
        "batch_name": outdir_name, "skip_video_creation": True, "make_gif": False, "frame_interpolation_engine": "None",
        "r_upscale_video": False, "store_frames_in_ram": False, "motion_preview_mode": False, "save_depth_maps": False,
        "use_init": False, "use_mask": True, "show_info_on_ui": False, "resume_from_timestring": False,     # (Deforum masks only with use_mask)
        "enable_clipskip_scheduling": False, "enable_subseed_scheduling": False,
        "enable_sampler_scheduling": False, "enable_perspective_flip": False, "anti_blur_amount_schedule": "0: (0)",
        "amount_schedule": "0: (0)", "zoom": "0: (1.0)", "angle": "0: (0)", "translation_x": "0: (0)", "translation_y": "0: (0)",
        "translation_z": "0: (0)", "rotation_3d_x": "0: (0)", "rotation_3d_y": "0: (0)", "rotation_3d_z": "0: (0)",
    })
    # another checkpoint than the one Forge has loaded: through Deforum's own checkpoint schedule (Forge's is put
    # back when the job ends)
    data["enable_checkpoint_scheduling"] = bool(options.get("checkpoint"))
    if options.get("checkpoint"):
        data["checkpoint_schedule"] = '0: ("' + str(options["checkpoint"]).replace('"', "") + '")'
    # every unit Deforum knows, complete: Deforum reads the schedules of as many units as Forge is set up for
    # (6 on this Forge), and a unit its settings file does not name comes in with empty schedules and stops it
    try:
        from modules import shared
        count = int(shared.opts.data.get("control_net_unit_count", shared.opts.data.get("control_net_max_models_num", 5)) or 5)
    except Exception:
        count = 5
    off = {"enabled": False, "module": "none", "model": "None", "weight": "0:(1)", "guidance_start": "0:(0.0)",
           "guidance_end": "0:(1.0)", "vid_path": "", "mask_vid_path": "", "overwrite_frames": True, "low_vram": False,
           "pixel_perfect": False, "processor_res": 64, "threshold_a": 64, "threshold_b": 64,
           "resize_mode": "Inner Fit (Scale to Fit)", "control_mode": "Balanced", "loopback_mode": False}
    for n in range(1, max(5, count) + 1):
        for key, value in off.items():
            data[f"cn_{n}_{key}"] = value
    sees = {"motion": motion["video"], "shape": motion.get("shape"), "look": motion.get("look") if motion.get("looking") else None}
    slot = 0
    for kind in ("pose", "depth", "edges", "shape", "look"):
        unit, model = units[kind], found["units"][kind]["model"]
        path = sees[_KINDS[kind]["sees"]]
        if not unit["on"] or not model or not path or slot >= max(5, count):
            continue
        slot += 1
        data.update({
            f"cn_{slot}_enabled": True, f"cn_{slot}_module": found["units"][kind]["module"], f"cn_{slot}_model": model,
            f"cn_{slot}_weight": f"0:({float(unit['weight'])})", f"cn_{slot}_vid_path": path,
            f"cn_{slot}_guidance_start": f"0:({float(unit['start'])})", f"cn_{slot}_guidance_end": f"0:({float(unit['end'])})",
            f"cn_{slot}_pixel_perfect": kind in ("pose", "depth", "edges"),
            f"cn_{slot}_processor_res": 512, f"cn_{slot}_threshold_a": 100 if kind == "edges" else 64,
            f"cn_{slot}_threshold_b": 200 if kind == "edges" else 64,
        })
    return data


def _base_settings(folder):
    with open(os.path.join(folder, "scripts", "default_settings.txt"), "r", encoding="utf8") as file:
        return json.load(file)


class _File:
    """What Deforum's batch runner takes for a settings file: something with a name."""

    def __init__(self, name):
        self.name = name


class _Share:
    """A job seen through a part of its progress (a step that runs from `a` to `b` of the whole)."""

    def __init__(self, job, a, b):
        self.job, self.a, self.b = job, a, b

    def step(self, stage=None, progress=None, preview=None):
        self.job.step(stage, None if progress is None else self.a + (self.b - self.a) * progress, preview)

    def __getattr__(self, name):
        return getattr(self.job, name)


def _watch(job, done, total, part=(0.06, 0.9)):
    """Deforum's progress and its frame, while it draws (it reports through Forge's shared state)."""
    from modules import shared
    last_preview = 0
    while not done.is_set():
        state = shared.state
        share = 0.0
        if state.job_count and state.job_count > 0:
            share = state.job_no / state.job_count
            if state.sampling_steps:
                share += state.sampling_step / state.sampling_steps / state.job_count
        job.progress = part[0] + (part[1] - part[0]) * min(max(share, 0.0), 1.0)
        job.stage = f"Deforum is drawing frame {min(total, int(state.job_no) + 1)} of {total}"
        if time.time() - last_preview > 2:
            try:
                state.set_current_image()
                if state.current_image is not None:
                    job.preview = jobs.preview_of(state.current_image)
            except Exception:
                pass
            last_preview = time.time()
        done.wait(0.5)


def drawn_frames(outdir):
    """Deforum's drawn frames in a folder, in order (not its depth maps)."""
    return sorted(p for p in glob.glob(os.path.join(outdir, "*.png")) if "_depth_" not in os.path.basename(p))


def live_frame(drawn, prior, motion, background):
    """One frame Deforum drew, cut by the backdrop's colour where the motion frame stands (RGBA, the motion
    chain's frame size). A quick cut, for showing the chain grow; the chain's real cut comes after."""
    record = motion["record"]
    w, h, left, top = record["width"], record["height"], motion["left"], motion["top"]
    piece = np.ascontiguousarray(drawn[top:top + h, left:left + w, :3])
    if prior is not None:
        prior = prior[top:top + h, left:left + w]
    colour = [float(c) for c in _hex(background)]
    options = chain._options({"options": {"prior_reach": 0.012}})
    alpha = chain._alpha_of(piece, {"matte": "key", "key_colour": colour}, options, prior)
    return cut.render_frame(piece, alpha, (1.0, 0.0, 0.0), w, h, colour)


def _live(job, outdir, motion, folder, options, done):
    """Every frame Deforum has drawn, shown at once: cut and written where the page reads the chain's live
    frames, while Deforum goes on drawing."""
    live = os.path.join(folder, "live")
    shutil.rmtree(live, ignore_errors=True)
    os.makedirs(live)
    record = motion["record"]
    job.live = {"frames": 0, "total": motion["frames"], "width": record["width"], "height": record["height"],
                "fps": motion["fps"], "path": f"chains/{os.path.basename(folder)}/live"}
    prior_folder = os.path.join(folder, "prior")
    made = 0
    while True:
        finished = done.is_set()
        try:
            # a frame still being written is left for the next look
            ready = [p for p in drawn_frames(outdir) if finished or time.time() - os.path.getmtime(p) > 0.4]
        except OSError:
            ready = []
        while made < min(len(ready), motion["frames"]):
            try:
                drawn = np.asarray(Image.open(ready[made]).convert("RGB"))
                prior_path = os.path.join(prior_folder, f"{made:06d}.png")
                prior = np.asarray(Image.open(prior_path).convert("L"), np.float32) / 255.0 if os.path.isfile(prior_path) else None
                placed = live_frame(drawn, prior, motion, options["background"])
                Image.fromarray(placed, "RGBA").save(os.path.join(live, f"{made:06d}.webp"), "WEBP", quality=88, alpha_quality=100, method=1)
            except Exception as e:          # (shown later by the real cut; the live view only skips it)
                print(f"[Time Strata] live frame {made}: {e}")
                if not finished:
                    break
            made += 1
            job.live["frames"] = made
        if finished:
            return
        done.wait(0.5)


def _put_back_checkpoint(title):
    """Forge's own checkpoint again, after a job that drew with another one."""
    try:
        from modules import sd_models, shared
        if not title or shared.opts.data.get("sd_model_checkpoint") == title:
            return
        info = sd_models.get_closet_checkpoint_match(title)
        if info is not None:
            sd_models.reload_model_weights(info=info)
            shared.opts.data["sd_model_checkpoint"] = title
    except Exception as e:
        print(f"[Time Strata] Forge's checkpoint could not be put back ({e}); choose it again in Forge")


def _work_root(folder):
    """Where Deforum works for a chain's folder: inside it, or - when its path is not plain ASCII (a project named
    in Hebrew) - in a folder of its own under Forge's outputs, since Deforum writes the frames it takes out of
    a video with OpenCV, which writes nothing to such a path on Windows."""
    if os.path.abspath(folder).isascii():
        return os.path.join(folder, "deforum")
    key = hashlib.sha1(os.path.abspath(folder).encode("utf8")).hexdigest()[:16]
    places = [os.path.join(store._webui(), "outputs", "time-strata-deforum"), os.path.join(tempfile.gettempdir(), "time-strata-deforum"),
              os.path.join(os.path.splitdrive(os.path.abspath(folder))[0] + os.sep, "time-strata-deforum")]
    return os.path.join(next((p for p in places if os.path.abspath(p).isascii()), places[-1]), key)


def _run(job, folder, motion, data, name, options, live=False, share=(0.06, 0.9)):
    """One Deforum render of `data` into the chain's folder: its drawn frames' paths, in order. With `live`,
    each frame is also cut and shown while Deforum goes on."""
    out_root = _work_root(folder)
    shutil.rmtree(out_root, ignore_errors=True)
    os.makedirs(out_root)
    settings_path = os.path.join(folder, f"{name}.json" if not live else "deforum_settings.json")
    # (plain ASCII: Deforum reads the file in Windows' own encoding, which turned a Hebrew folder name into
    # gibberish and lost the motion video)
    with open(settings_path, "w", encoding="utf8") as file:
        json.dump(data, file, ensure_ascii=True, indent=1)
    import deforum_api
    from modules import shared
    from modules.call_queue import queue_lock
    batch_id = f"timestrata({uuid.uuid4().hex[:8]})"
    job_id = f"{batch_id}-0"
    tracker = deforum_api.JobStatusTracker()
    tracker.accept_job(batch_id=batch_id, job_id=job_id, deforum_settings=[data], options_overrides={})
    job.on_cancel = lambda: shared.state.interrupt()
    job.step("waiting for Forge (another drawing may be running)", 0.05)
    done = threading.Event()
    watcher = threading.Thread(target=_watch, args=(job, done, motion["frames"], share), daemon=True)
    shower = threading.Thread(target=_live, args=(job, os.path.join(out_root, name), motion, folder, options, done), daemon=True)
    with queue_lock:
        if job.cancel_asked:
            raise jobs.Cancelled()
        watcher.start()
        if live:
            shower.start()
        loaded = shared.opts.data.get("sd_model_checkpoint")
        try:
            with _WithoutDeforumation():
                deforum_api.run_deforum_batch(batch_id, [job_id], [_File(settings_path)], {"outdir_samples": out_root})
        finally:
            done.set()
            watcher.join(timeout=5)
            if live:
                shower.join(timeout=60)
            if options.get("checkpoint"):
                _put_back_checkpoint(loaded)
    if job.cancel_asked or shared.state.interrupted:
        raise jobs.Cancelled()
    status = tracker.get(job_id)
    outdir = getattr(status, "outdir", None) or os.path.join(out_root, name)
    stamp = getattr(status, "timestring", None)
    drawn = sorted(p for p in glob.glob(os.path.join(outdir, f"{stamp or '*'}_*.png")) if "_depth_" not in os.path.basename(p))
    if not drawn:
        message = getattr(status, "message", None)
        raise jobs.JobError(f"Deforum drew no frames{': ' + str(message) if message else ''} (see Forge's console)")
    return drawn


def anchor_frames(scenes, measures, first, last, fps):
    """The motion frame each scene's look is drawn from: where the figure is largest within the scene (a whole
    standing body is the best look to hold; a crouch or a turn away is not), else the scene's middle."""
    ends = [s["from"] for s in scenes[1:]] + [last / fps]
    pick = []
    for scene, end in zip(scenes, ends):
        a, b = max(first, int(round(scene["from"] * fps))), min(last, int(round(end * fps)))
        inside = [k for k in range(a, b + 1) if k < len(measures) and not measures[k].get("empty")]
        if inside:
            pick.append(max(inside, key=lambda k: measures[k].get("area", 0) * max(1, measures[k].get("bottom", 1) - measures[k].get("top", 0))))
        else:
            middle = int(round((max(scene["from"], first / fps) + end) / 2 * fps))
            pick.append(min(max(first, middle), last))
    return pick


def _anchor_scenes(job, project, source, target, folder, options, scenes, units, uses, found, deforum_folder):
    """A steady look: one frame of each scene is drawn first (the scene's middle), cut out, and becomes that
    scene's look picture, so IP-Adapter gives every frame of the scene the same face, clothes and colours.
    A scene that has a look picture of its own keeps it."""
    record = store.read_chain(project, source)
    fps = float(record.get("fps") or 24)
    first = int(options["first"] or 0)
    last = int(options["last"] if options["last"] is not None else record["frames"] - 1)
    pick = anchor_frames(scenes, record.get("measures") or [], first, last, fps)
    if len(pick) < 2:
        pick = pick + [min(last, pick[0] + 1)]
    anchors = os.path.join(folder, "anchors")
    os.makedirs(anchors, exist_ok=True)
    motion = _motion(_Share(job, 0.0, 0.01), project, source, anchors, options, scenes, uses - {"look"}, pick=pick)
    data = settings(_base_settings(deforum_folder), motion, options, f"TimeStrata_{target}_anchors", scenes,
                    dict(units, look=dict(units["look"], on=False)), found)
    job.step("drawing one frame of each scene first, for a steady look", 0.01)
    drawn = _run(job, anchors, motion, data, f"TimeStrata_{target}_anchors", options, share=(0.01, 0.05))
    for i, scene in enumerate(scenes):
        if scene.get("look") or i >= len(drawn):
            continue
        picture = np.asarray(Image.open(drawn[i]).convert("RGB"))
        prior = np.asarray(Image.open(os.path.join(anchors, "prior", f"{i:06d}.png")).convert("L"))
        ys, xs = np.nonzero(prior >= 128)
        if len(ys):
            pad = int(0.06 * picture.shape[0])
            picture = picture[max(0, ys.min() - pad):ys.max() + pad, max(0, xs.min() - pad):xs.max() + pad]
        path = os.path.join(anchors, f"look_{i}.png")
        Image.fromarray(picture).save(path)
        scene["look"] = store.relative(project, path)
    shutil.rmtree(_work_root(anchors), ignore_errors=True)


def redraw_map(drawn_with, frames, count):
    """Frames n of a drawn chain -> (the frames, sorted and inside the chain; the motion frames they were drawn
    from). `drawn_with`: what the chain was drawn with (its record's extra.deforum): the motion frames it kept, or
    for a chain drawn before those were kept, the first frame and the step."""
    picked = drawn_with.get("motion_frames") or []
    options = drawn_with.get("options") or {}
    first = int(options.get("first") or 0)
    every = max(1, int(options.get("every") or 1))
    wanted = sorted({int(n) for n in frames if 0 <= int(n) < count})
    return wanted, [int(picked[n]) if n < len(picked) else first + n * every for n in wanted]


def redraw_seed(drawn_with, asked):
    """The seed some frames are drawn again with: the one asked for, moved on past every seed the chain was drawn
    with before (the same seed draws the same frames again, and a page opened again proposes the same seed)."""
    if asked is None:
        return None
    used = {int(seed) for seed in drawn_with.get("redraw_seeds") or []}
    if (drawn_with.get("options") or {}).get("seed") is not None:
        used.add(int(drawn_with["options"]["seed"]))
    seed = int(asked)
    while seed in used:
        seed += 7919
    return seed


def _redraw(job, project, source, target, folder, options, scenes, units, uses, found, deforum_folder):
    """Some frames of a drawn chain drawn again - with the scenes and look set now and usually a new seed - in
    place of the old ones; every other frame is kept as it is. The steady look keeps the anchors the chain was
    drawn with, so the new frames match the old. Then the chain is cut again (only the new frames are enlarged
    again when it is sharpened)."""
    record = store.read_chain(project, target)
    drawn_with = ((record or {}).get("extra") or {}).get("deforum") or {}
    if not record or drawn_with.get("from") != source or not os.path.isdir(os.path.join(folder, "source")):
        raise jobs.JobError("only frames of a chain drawn from this motion can be drawn again")
    frames, picks = redraw_map(drawn_with, options["redraw"], int(record["frames"]))
    if not frames:
        raise jobs.JobError("choose which frames to draw again")
    options = dict(options)
    seed = redraw_seed(drawn_with, options.get("redraw_seed"))
    if seed is not None:
        options["seed"] = seed
    steady = options.get("steady") or {}
    if steady.get("on") and found["units"]["look"]["model"]:
        kept = [os.path.join(folder, "anchors", f"look_{i}.png") for i in range(len(scenes))]
        if all(scene.get("look") or os.path.isfile(path) for scene, path in zip(scenes, kept)):
            for scene, path in zip(scenes, kept):
                scene["look"] = scene.get("look") or store.relative(project, path)
        else:
            _anchor_scenes(job, project, source, target, folder, options, scenes, units, uses, found, deforum_folder)
        units["look"] = dict(units["look"], on=True, weight=float(steady.get("weight") or 0.55), start=0.0, end=1.0)
        uses = uses | {"look"}
    sub = os.path.join(folder, "redraw")
    shutil.rmtree(sub, ignore_errors=True)
    os.makedirs(sub)
    try:
        # (Deforum needs two frames at least: one frame alone is drawn twice)
        motion = _motion(_Share(job, 0.0, 0.05), project, source, sub, options, scenes, uses, pick=picks if len(picks) > 1 else picks * 2)
        data = settings(_base_settings(deforum_folder), motion, options, f"TimeStrata_{target}_redraw", scenes, units, found)
        job.step(f"drawing {len(frames)} frames again", 0.05)
        drawn = _run(job, sub, motion, data, f"TimeStrata_{target}_redraw", options, share=(0.05, 0.6))
        pictures = os.path.join(folder, "source")
        enlarged = os.path.join(folder, "upscale", "out")
        for n, path in zip(frames, drawn):
            shutil.copyfile(path, os.path.join(pictures, f"{n:06d}.png"))
            old = os.path.join(enlarged, f"{n:06d}.png")
            if os.path.isfile(old):
                os.remove(old)              # (enlarged again from the new drawing when the chain is cut)
    finally:
        shutil.rmtree(_work_root(sub), ignore_errors=True)     # (Deforum's folder, when it could not be inside)
        shutil.rmtree(sub, ignore_errors=True)
    extra = dict(record.get("extra") or {})
    extra["deforum"] = dict(drawn_with, redrawn=sorted(set(drawn_with.get("redrawn") or []) | set(frames)))
    if seed is not None:
        extra["deforum"]["redraw_seeds"] = sorted(set(drawn_with.get("redraw_seeds") or []) | {seed})
    result = chain.build(_Share(job, 0.6, 1.0), project, target, record["source"], chain._options({"options": record.get("options") or {}}),
                         priors=chain._known_figure(project, target), extra=extra)
    result["from"] = source
    result["redrawn"] = frames
    result["seed"] = options["seed"]
    return result


@jobs.handler("deforum")
def generate(job):
    """params: project, source (the motion chain), chain (the new chain), options (see DEFAULTS)"""
    params = job.params
    project = store.clean_name(params["project"])
    source = store.clean_id(params["source"], "chain id")
    target = store.clean_id(params["chain"], "chain id")
    if source == target:
        raise jobs.JobError("the new chain must be another chain than its motion")
    options = dict(DEFAULTS)
    options.update({k: v for k, v in (params.get("options") or {}).items() if k in DEFAULTS and v is not None})
    deforum_folder = _ensure_paths()
    folder = store.chain_dir(project, target, create=True)
    record = store.read_chain(project, source) or {}
    count = int(record.get("frames") or 2)
    record_fps = float(record.get("fps") or 24)
    # an older project's stage `at`: a share of the chain (0..1) or a frame number
    scenes = scenes_of(options, lambda at: (at * (count - 1) if at <= 1 else at) / record_fps)
    units = _units(options)
    found = available()
    for kind, unit in units.items():
        if unit["on"] and not found["units"][kind]["model"]:
            print(f"[Time Strata] no ControlNet model for '{kind}' was found: that unit is left out")
    uses = {kind for kind, unit in units.items() if unit["on"] and found["units"][kind]["model"]}

    if options.get("redraw"):
        return _redraw(job, project, source, target, folder, options, scenes, units, uses, found, deforum_folder)

    steady = options.get("steady") or {}
    if steady.get("on") and found["units"]["look"]["model"]:
        _anchor_scenes(job, project, source, target, folder, options, scenes, units, uses, found, deforum_folder)
        units["look"] = dict(units["look"], on=True, weight=float(steady.get("weight") or 0.55), start=0.0, end=1.0)
        uses = uses | {"look"}
    elif steady.get("on"):
        print("[Time Strata] a steady look needs an IP-Adapter ControlNet model; drawing without it")

    motion = _motion(job, project, source, folder, options, scenes, uses)
    name = f"TimeStrata_{target}"
    data = settings(_base_settings(deforum_folder), motion, options, name, scenes, units, found)

    try:
        drawn = _run(job, folder, motion, data, name, options, live=True)
    except BaseException:
        # a drawing cancelled or failed leaves no live frames behind (the chain keeps what it had)
        shutil.rmtree(os.path.join(folder, "live"), ignore_errors=True)
        shutil.rmtree(_work_root(folder), ignore_errors=True)
        raise
    if not drawn:
        raise jobs.JobError("Deforum drew no frames (see Forge's console)")

    # the drawn frames become the new chain's source, in order
    pictures = os.path.join(folder, "source")
    shutil.rmtree(pictures, ignore_errors=True)
    os.makedirs(pictures)
    for n, path in enumerate(drawn[:motion["frames"]]):
        shutil.copyfile(path, os.path.join(pictures, f"{n:06d}.png"))
    shutil.rmtree(_work_root(folder), ignore_errors=True)    # Deforum's own copies and its extracted frames

    prior_folder = os.path.join(folder, "prior")

    def priors(n, shape):
        path = os.path.join(prior_folder, f"{n:06d}.png")
        if not os.path.isfile(path):
            return None
        prior = np.asarray(Image.open(path).convert("L"), np.float32) / 255.0
        if prior.shape != tuple(shape):
            prior = np.asarray(Image.fromarray((prior * 255).astype(np.uint8)).resize((shape[1], shape[0]), Image.BILINEAR), np.float32) / 255.0
        return prior

    record = motion["record"]
    # sharper drawn frames: Real-ESRGAN before the cut, and a taller chain frame to keep what it adds
    sharper = upscale.parse(options.get("upscale"))
    height = int(min(2048, record["height"] * sharper[1])) if sharper else record["height"]
    cut_options = chain._options({"options": {
        "matte": "keymodel", "key_colour": str(options["background"]), "height": height, "upscale": options.get("upscale"),
        "align": "centre", "scale": "steady", "figure": (record.get("options") or {}).get("figure", 0.84),
        "baseline": (record.get("options") or {}).get("baseline", 0.95), "max_frames": len(drawn),
        "prior_reach": 0.012,       # the drawn figure follows the motion closely (its pose and its picture)
    }})
    result = chain.build(_Share(job, 0.9, 1.0), project, target,
                         {"kind": "pictures", "path": f"chains/{target}/source", "fps": motion["fps"]},
                         cut_options, priors=priors,
                         extra={"deforum": {"from": source, "options": options, "settings": "deforum_settings.json",
                                            "motion_frames": motion["picked"]}})
    shutil.rmtree(os.path.join(folder, "live"), ignore_errors=True)
    result["from"] = source
    result["skipped"] = motion["skipped"]
    return result

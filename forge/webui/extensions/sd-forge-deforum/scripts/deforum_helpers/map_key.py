# Map Key: the Python half of the 🗺️ Map Key editor (Keyframes -> Map Key). The drawing, the camera path and every
# edit happen in the browser (javascript/map-key-core.js); this module reads what is in Deforum's fields for the
# editor, and turns what the editor sends back into Deforum schedules and prompts.
#
# Pure module (numpy and the standard library only), unit-tested outside Forge:
#   ..\..\..\venv\Scripts\python.exe -m unittest deforum_helpers.map_key_test   (from /scripts)

import json
import math
import os
import re
import time
from collections import OrderedDict

import numpy as np

from . import audio_keyframes as ak

VERSION = 1
# the Deforum schedules the editor can draw (their decimals and neutral values come from audio_keyframes.TARGETS)
LANE_KEYS = ["strength_schedule", "cfg_scale_schedule", "zoom", "angle", "translation_x", "translation_y",
             "translation_z", "rotation_3d_x", "rotation_3d_y", "rotation_3d_z"]
MAX_FRAMES = ak.MAX_FRAMES
MAX_PROJECT_BYTES = 30_000_000
PROJECT_SUFFIX = ".json"


class MapKeyError(ValueError):
    pass


def neutral_of(key):
    target = ak.TARGETS_BY_KEY[key]
    if target.neutral is not None:
        return target.neutral
    return 0.65 if key == "strength_schedule" else 7.0


# --- schedules -------------------------------------------------------------------------------------------

def compact_keys(values, decimals):
    """The fewest (frame, text) keys whose linear in-betweens stay within one step (10^-decimals) of the drawn values,
    so a drawn straight line over 100 frames is 2 keys and not 100. The first and last frames are always keys, and the
    keys are the drawn values rounded. Same algorithm as compactKnots in map-key-core.js."""
    drawn = np.asarray(values, dtype=np.float64).tolist()
    texts = [ak.format_number(v, decimals) for v in drawn]
    if not texts:
        raise MapKeyError("There are no frames.")
    v = [float(text) for text in texts]
    tol = 10.0 ** -int(decimals) + 1e-12
    keys = [0]
    a, n = 0, len(v)
    while a < n - 1:
        lo, hi, best = -math.inf, math.inf, a + 1
        for e in range(a + 1, n):
            slope = (v[e] - v[a]) / (e - a)
            if lo - 1e-12 <= slope <= hi + 1e-12:
                best = e
            lo = max(lo, (drawn[e] - tol - v[a]) / (e - a))
            hi = min(hi, (drawn[e] + tol - v[a]) / (e - a))
            if lo > hi:
                break
        keys.append(best)
        a = best
    return [(i, texts[i]) for i in keys]


def compact_schedule(values, decimals):
    """'0: (0.8), 99: (1.5)': a Deforum schedule with a key only where the line bends."""
    return ", ".join(f"{frame}: ({text})" for frame, text in compact_keys(values, decimals))


def knots(values, decimals):
    """[[frame, value], ...] for the editor (a few keys instead of one number per frame)."""
    return [[frame, float(text)] for frame, text in compact_keys(values, decimals)]


def expanded_lane(key, text, frames, expand):
    """Per-frame values of one schedule, parsed by `expand` (Deforum's own parser in the webui). Returns (values, error)."""
    neutral = neutral_of(key)
    if not str(text or "").strip():
        return np.full(frames, neutral), None
    try:
        values = np.asarray(expand(str(text), frames), dtype=np.float64)
        if values.shape != (frames,):
            raise MapKeyError(f"got {values.size} values for {frames} frames")
        if not np.isfinite(values).all():
            raise MapKeyError("it gives a value that is not a number")
        return values, None
    except Exception as err:  # the field is kept as it is; the lane starts at the neutral value
        return np.full(frames, neutral), f"{ak.TARGETS_BY_KEY[key].label} could not be read ({err}); it starts at " \
                                         f"{ak.format_number(neutral, ak.TARGETS_BY_KEY[key].decimals)} and is only " \
                                         "written if you draw on it."


# --- prompts ---------------------------------------------------------------------------------------------

def key_frame(key, frames):
    """The frame of a prompt key: '30' -> 30, 'max_f-1' -> frames - 2 (max_f is the last frame, as in Deforum's
    parser). None when the key is not a number or a formula of max_f."""
    text = str(key).strip()
    if text.isdigit():
        return int(text)
    try:
        value = ak.compile_formula(text, variables=("max_f",))({"max_f": np.array([float(frames - 1)])})
        number = float(value[0])
    except Exception:
        return None
    return int(number) if math.isfinite(number) and number >= 0 else None


def prompt_entries(text, frames):
    """Deforum's Prompts JSON -> (entries [{frame, text, key, pinned}], unplaced [{key, text}], error).
    Formula keys (max_f-1) are shown at their frame and kept as written; keys that cannot be placed are kept aside
    and written back unchanged. On invalid JSON the prompts are not editable (error says why)."""
    if not str(text or "").strip():
        return [], [], None
    try:
        data = json.loads(text)
    except ValueError as err:
        return [], [], f"The Prompts are not valid JSON ({err}), so Map Key shows no prompts and never writes them."
    if not isinstance(data, dict):
        return [], [], 'The Prompts must be a JSON object like {"0": "a forest"}, so Map Key never writes them.'
    entries, unplaced, taken = [], [], set()
    for key, value in data.items():
        frame = key_frame(key, frames)
        if frame is None or frame >= frames or frame in taken:
            unplaced.append({"key": str(key), "text": str(value)})
            continue
        taken.add(frame)
        pinned = not str(key).strip().isdigit()
        entries.append({"frame": frame, "text": str(value), "key": str(key), "pinned": pinned})
    entries.sort(key=lambda entry: entry["frame"])
    return entries, unplaced, None


def prompts_json(entries, unplaced=None):
    """The editor's prompts -> Deforum's Prompts JSON, in frame order. A formula key that was not moved keeps its
    formula; prompts that could not be placed go at the end unchanged."""
    ordered = OrderedDict()
    frames_seen = set()
    for entry in sorted(entries or [], key=lambda e: int(e["frame"])):
        frame = int(entry["frame"])
        if frame < 0:
            raise MapKeyError(f"A prompt is at frame {frame}.")
        if frame in frames_seen:
            raise MapKeyError(f"Two prompts are at frame {frame}.")
        frames_seen.add(frame)
        key = str(entry.get("key") or "").strip() or str(frame)
        ordered[key] = str(entry.get("text", ""))
    for entry in unplaced or []:
        key = str(entry.get("key", "")).strip()
        if key and key not in ordered:
            ordered[key] = str(entry.get("text", ""))
    return json.dumps(ordered, ensure_ascii=False, indent=4)


# --- the song --------------------------------------------------------------------------------------------

def song_data(analysis, normalize=ak.NORMALIZE_PEAK, threshold=0.0, part_frames=None, path="", max_points=20000):
    """The music lane: every band's loudness 0..999 per frame (the loudest of every `step` frames for very long songs),
    the beats and the song parts."""
    curves = analysis.curves(normalize, threshold)
    frames = analysis.frame_count
    step = max(1, int(math.ceil(frames / float(max_points))))
    bands = {}
    for band, curve in curves.items():
        if step > 1:
            curve = np.concatenate([curve, np.zeros((-curve.size) % step)]).reshape(-1, step).max(axis=1)
        bands[band] = np.rint(np.clip(curve, 0.0, 1.0) * 999).astype(int).tolist()
    beats = ak.beat_frames(curves[ak.BAND_FULL], analysis.fps, 0.25)
    parts = sorted({int(f) for f in (part_frames or [0]) if 0 <= int(f) < frames} | {0})
    return {"name": analysis.name, "fps": analysis.fps, "frames": frames, "duration": round(float(analysis.duration), 4),
            "step": step, "bands": bands, "beats": [int(b) for b in beats], "parts": parts, "path": path or ""}


# --- load and apply --------------------------------------------------------------------------------------

def whole_number(value, what, low=1, high=MAX_FRAMES):
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise MapKeyError(f"{what} is not a number.") from None
    if not math.isfinite(number) or not low <= number <= high:
        raise MapKeyError(f"{what} must be between {low} and {high:,}.")
    return int(round(number))


def warp_data(fov_text, frames, expand):
    """Deforum's per-frame FOV, which sets how strongly the camera path's level-keeping roll acts (see rollScales in
    map-key-core.js). Returns (data, note or None)."""
    note = None
    try:
        fov = np.asarray(expand(str(fov_text or "0: (70)"), frames), dtype=np.float64)
        if fov.shape != (frames,) or not np.isfinite(fov).all() or fov.min() <= 0 or fov.max() >= 180:
            raise MapKeyError("the values must be between 0 and 180")
    except Exception as err:
        fov = np.full(frames, 70.0)
        note = f"The FOV schedule could not be read ({err}); the camera path assumes 70."
    return {"fov": knots(fov, 2)}, note


def load_state(schedules, prompts_text, frames, fps, cadence, mode, expand, song=None, project=None,
               fov_text="0: (70)"):
    """Everything the editor needs, from Deforum's fields: per-lane keys, prompts, the song (or None) and the frame
    count, FPS, cadence, animation mode and FOV. Returns (data, notes)."""
    frames = whole_number(frames, "Max frames")
    fps = float(fps) if fps and float(fps) > 0 else 30.0
    cadence = max(1, int(round(float(cadence or 1))))
    notes = []
    lanes = {}
    for key in LANE_KEYS:
        text = schedules.get(key, "")
        values, error = expanded_lane(key, text, frames, expand)
        if error:
            notes.append(error)
        lanes[key] = {"knots": knots(values, ak.TARGETS_BY_KEY[key].decimals + 2), "text": str(text or ""),
                      "error": error}
    entries, unplaced, prompt_error = prompt_entries(prompts_text, frames)
    if prompt_error:
        notes.append(prompt_error)
    if song is not None and abs(float(song["fps"]) - fps) > 1e-6:
        notes.append(f"The song was analysed at {song['fps']:g} fps but Deforum's FPS is {fps:g}.")
    if song is not None and song["frames"] != frames:
        notes.append(f"Max frames is {frames:,} but the song is {song['frames']:,} frames at {fps:g} fps. "
                     "Audio Keyframes → Apply to Deforum can set Max frames to the song length.")
    if str(mode) not in ("2D", "3D"):
        notes.append(f"The animation mode is {mode}: the camera lanes only move 2D and 3D animations (shown here as 3D).")
    mode = "2D" if str(mode) == "2D" else "3D"
    warp, warp_note = warp_data(fov_text, frames, expand)
    if warp_note:
        notes.append(warp_note)
    data = {"v": VERSION, "frames": frames, "fps": fps, "cadence": cadence, "mode": mode, "lanes": lanes,
            "prompts": entries, "unplaced": unplaced, "prompts_ok": prompt_error is None, "song": song,
            "project": project, "warp": warp}
    return data, notes


def apply_payload(payload, frames_now=None):
    """What the editor's Apply sends -> ({schedule key: text}, prompts JSON or None, project, notes).
    Only the lanes that were drawn on (or that the camera path drives) are in the payload."""
    if isinstance(payload, str):
        if not payload.strip():
            raise MapKeyError("There is nothing to apply yet: press 📥 Load from Deforum first.")
        try:
            payload = json.loads(payload)
        except ValueError:
            raise MapKeyError("The editor sent something that is not JSON.") from None
    if not isinstance(payload, dict) or payload.get("version") != VERSION:
        raise MapKeyError("The editor's data is not from this version of Map Key; press 📥 Load from Deforum.")
    if payload.get("error"):
        raise MapKeyError(str(payload["error"]))
    frames = whole_number(payload.get("frames"), "The editor's frame count")
    updates = OrderedDict()
    for key, values in (payload.get("lanes") or {}).items():
        if key not in LANE_KEYS:
            raise MapKeyError(f"Unknown schedule {key!r}.")
        target = ak.TARGETS_BY_KEY[key]
        try:
            array = np.asarray(values, dtype=np.float64)
        except (TypeError, ValueError):
            raise MapKeyError(f"{target.label}: the values are not numbers.") from None
        if array.shape != (frames,):
            raise MapKeyError(f"{target.label}: {array.size} values for {frames} frames.")
        if not np.isfinite(array).all():
            raise MapKeyError(f"{target.label}: a value is not a number (frame {int(np.argmin(np.isfinite(array)))}).")
        if key == "strength_schedule" and (array.min() < -1e-9 or array.max() > 1 + 1e-9):
            raise MapKeyError("Strength must stay between 0 and 1.")
        updates[key] = compact_schedule(array, target.decimals)
    prompts = payload.get("prompts")
    prompts_text = None if prompts is None else prompts_json(prompts, payload.get("unplaced"))
    notes = []
    if frames_now is not None:
        try:
            now = int(round(float(frames_now)))
        except (TypeError, ValueError):
            now = frames
        if now != frames:
            notes.append(f"Max frames is now {now:,}, and the editor was loaded with {frames:,}. Press 📥 Load from "
                         "Deforum to draw on the new length.")
    return updates, prompts_text, payload.get("project"), notes


# --- projects --------------------------------------------------------------------------------------------

_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def project_file(folder, name):
    stem = _UNSAFE.sub("_", str(name or "").strip()).strip(" .")[:80]
    if not stem:
        raise MapKeyError("Give the project a name.")
    return os.path.join(folder, stem + PROJECT_SUFFIX)


def check_project(project):
    if not isinstance(project, dict) or project.get("version") != VERSION or not isinstance(project.get("lanes"), dict):
        raise MapKeyError("This is not a Map Key project.")
    whole_number(project.get("frames"), "The project's frame count")
    return project


def save_project(path, project):
    check_project(project)
    text = json.dumps({"map_key": VERSION, "saved": time.strftime("%Y-%m-%d %H:%M:%S"), "project": project},
                      ensure_ascii=False, separators=(",", ":"))
    if len(text) > MAX_PROJECT_BYTES:
        raise MapKeyError("The project is too big to save.")
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    partial = path + ".partial"
    with open(partial, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(partial, path)


def load_project(path):
    if not os.path.isfile(path):
        raise MapKeyError(f"There is no project at {path}.")
    if os.path.getsize(path) > MAX_PROJECT_BYTES:
        raise MapKeyError(f"{path} is too big for a Map Key project.")
    with open(path, encoding="utf-8") as f:
        try:
            data = json.load(f)
        except ValueError:
            raise MapKeyError(f"{path} is not a Map Key project.") from None
    return check_project(data.get("project") if isinstance(data, dict) else None)


def list_projects(folder):
    if not os.path.isdir(folder):
        return []
    names = [name[:-len(PROJECT_SUFFIX)] for name in os.listdir(folder)
             if name.lower().endswith(PROJECT_SUFFIX) and os.path.isfile(os.path.join(folder, name))]
    return sorted(names, key=str.lower)

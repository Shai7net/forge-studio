# Camera directing for Deforum's Motion schedules.
#   * Direction tools: flip a schedule's direction, or switch it every N frames, keeping its speeds.
#   * Camera modes (built in, saved, or learned from existing Motion schedules): a mode fills the camera rows of the
#     Audio Keyframes tab (band, function, Quiet/Loud, Smooth, Switch) and its song parts, for any analysed song.
#
# Pure module (numpy and audio_keyframes only). Unit tests: camera_directing_test.py.

import json
import math
import os
import re
from collections import OrderedDict

import numpy as np

from . import audio_keyframes as ak

MOTION_KEYS = ["zoom", "angle", "translation_x", "translation_y", "translation_z",
               "rotation_3d_x", "rotation_3d_y", "rotation_3d_z"]
MODE_ROW_FIELDS = ["band", "function", "quiet", "loud", "smooth", "switch"]
LEARN_MIN_CORRELATION = 0.35
MAX_CROSSFADE_FRAMES = 60  # the Crossfade slider's maximum


# --- direction tools -------------------------------------------------------------------------------------

def last_key_frame(text):
    """The highest frame keyed in a schedule text ('0: (1), 120: (2)' -> 120)."""
    keys = [int(key) for key in re.findall(r"(?:^|,)\s*(\d+)\s*:", str(text or ""))]
    return max(keys) if keys else 0


def start_sign(values, neutral):
    """The direction a schedule starts moving in: +1 or -1 (+1 when it never moves)."""
    offsets = np.asarray(values, dtype=np.float64) - neutral
    moving = np.flatnonzero(np.abs(offsets) > 1e-12)
    return -1.0 if moving.size and offsets[moving[0]] < 0 else 1.0


def flip_values(values, neutral):
    """Every move the other way at the same speed (+ becomes - around the neutral value)."""
    return neutral - (np.asarray(values, dtype=np.float64) - neutral)


def direction_changes(values, neutral):
    """How many times a schedule turns around (frames that hardly move keep the direction before them)."""
    offsets = np.asarray(values, dtype=np.float64) - neutral
    peak = float(np.abs(offsets).max()) if offsets.size else 0.0
    if peak < 1e-12:
        return 0
    return int((np.diff(_carried_signs(offsets, 0.02 * peak)) != 0).sum())


def switch_values(values, neutral, every):
    """The same speeds, with the direction switching every `every` frames (0 = always the starting direction)."""
    values = np.asarray(values, dtype=np.float64)
    signs = np.full(values.size, start_sign(values, neutral))
    if int(every) > 0:
        signs = signs * np.where((np.arange(values.size) // int(every)) % 2 == 0, 1.0, -1.0)
    return neutral + signs * np.abs(values - neutral)


# --- camera modes ----------------------------------------------------------------------------------------

def _row(band, function, quiet, loud, smooth, switch=""):
    return {"band": band, "function": function, "quiet": quiet, "loud": loud, "smooth": smooth, "switch": switch}


def _mode(name, about, about_he, rows, part_seconds, pattern, crossfade_seconds):
    return name, {"about": about, "about_he": about_he, "rows": rows,
                  "parts": {"min_seconds": part_seconds, "pattern": pattern}, "crossfade_seconds": crossfade_seconds}


# In 3D terms; mode_rows turns Translation Z into Zoom and Rotation 3D Z into Angle for 2D.
# Rotation 3D Y + pans right and Translation X + moves right, so opposite signs orbit around the subject.
BUILTIN_MODES = OrderedDict([
    _mode("Gentle · תנועות קלות",
          "A slow push forward that swells with the music, and a soft pan that changes side with each part of the song.",
          "נסיעה איטית קדימה שמתגברת עם המוזיקה, ופנורמה רכה שמחליפה צד בכל חלק בשיר.",
          {"translation_z": _row("Full", "x^2", 0.3, 1.0, 8),
           "rotation_3d_y": _row("Mids", "x", 0.05, 0.25, 12),
           "translation_x": _row("Mids", "x", 0.0, 0.6, 12)},
          12, [{"Z": "+", "Rot Y": "+", "X": "-"}, {"Z": "+", "Rot Y": "-", "X": "+"}], 1.5),
    _mode("Action! · אקשן",
          "Hard pushes on the bass, sideways whips and a camera roll that change direction on the beats, short song parts.",
          "דחיפות חזקות קדימה על הבס, הצלפות לצדדים וגלגול מצלמה שמחליפים כיוון על הביטים, וחלקי שיר קצרים.",
          {"translation_z": _row("Bass", "x^4", 0.6, 5.0, 2),
           "translation_x": _row("Highs", "x^2", 0.0, 4.0, 1, "beats 0.5"),
           "rotation_3d_y": _row("Mids", "x^2", 0.0, 1.2, 2, "beats 1"),
           "rotation_3d_z": _row("Bass", "x^4", 0.0, 1.5, 1, "beats 0.5")},
          4, [{"Z": "+", "X": "+", "Rot Y": "+", "Rot Z": "+"}, {"Z": "+", "X": "-", "Rot Y": "-", "Rot Z": "-"}], 0.1),
    _mode("Artistic · אומנותי",
          "A slow orbit around the subject that reverses with each part of the song, and a gentle roll that breathes.",
          "סיבוב איטי סביב הנושא שמתהפך בכל חלק בשיר, וגלגול עדין שנושם.",
          {"rotation_3d_y": _row("Full", "x", 0.2, 0.6, 20),
           "translation_x": _row("Full", "x", 0.8, 2.4, 20),
           "translation_z": _row("Full", "x^2", 0.2, 0.7, 10),
           "rotation_3d_z": _row("Full", "0.25*sin(sec*0.5)", "", "", 0)},
          8, [{"Rot Y": "+", "X": "-", "Z": "+", "Rot Z": "+"}, {"Rot Y": "-", "X": "+", "Z": "+", "Rot Z": "+"}], 1.0),
    _mode("Dreamy · חלומי",
          "Floating: a very slow push forward while the camera gently rises, sinks and sways, and the music only swells it.",
          "ריחוף: נסיעה איטית מאוד קדימה, והמצלמה עולה, יורדת ומתנדנדת בעדינות, כשהמוזיקה רק מגבירה.",
          {"translation_z": _row("Full", "x^2", 0.1, 0.5, 30),
           "translation_y": _row("Full", "0.4*sin(sec*0.25)", "", "", 0),
           "rotation_3d_x": _row("Full", "0.15*sin(sec*0.3)", "", "", 0),
           "rotation_3d_z": _row("Full", "0.2*sin(sec*0.2 + 1)", "", "", 0)},
          15, [{"Z": "+", "Y": "+", "Rot X": "+", "Rot Z": "+"}], 2.0),
    _mode("Handheld · מצלמת כתף",
          "A camera held by hand: small nervous moves that switch direction every few frames, stronger when it gets loud.",
          "מצלמה ביד: תזוזות קטנות ועצבניות שמחליפות כיוון כל כמה פריימים, וחזקות יותר כשהמוזיקה חזקה.",
          {"translation_z": _row("Full", "x", 0.4, 1.0, 4),
           "translation_x": _row("Full", "x", 0.1, 0.8, 0, "5"),
           "translation_y": _row("Full", "x", 0.1, 0.6, 0, "7"),
           "rotation_3d_z": _row("Highs", "x", 0.05, 0.4, 0, "4")},
          0, [{"Z": "+", "X": "+", "Y": "+", "Rot Z": "+"}], 0),
])

# 2D and 3D twins: Translation Z moves the camera like Zoom (1 + tz/100 per frame), Rotation 3D Z rolls like Angle
TWINS = {"translation_z": "zoom", "zoom": "translation_z", "rotation_3d_z": "angle", "angle": "rotation_3d_z"}
COLUMN_TWINS = {"Z": "Zoom", "Zoom": "Z", "Rot Z": "Angle", "Angle": "Rot Z"}


def _is_empty(value):
    return value is None or str(value).strip() == ""


def _twin_row(row, key):
    """A 3D row as its 2D twin, or back."""
    row = dict(row)
    if key in ("translation_z", "zoom"):
        to_zoom = key == "translation_z"
        if _is_empty(row.get("quiet")) and _is_empty(row.get("loud")):
            function = row.get("function") or ak.DEFAULT_FUNCTION
            row["function"] = f"1 + ({function})/100" if to_zoom else f"(({function}) - 1)*100"
        else:
            convert = (lambda v: 1 + float(v) / 100.0) if to_zoom else (lambda v: (float(v) - 1) * 100.0)
            row["quiet"], row["loud"] = convert(row["quiet"]), convert(row["loud"])
    return row


def mode_rows(mode, animation_mode="3D"):
    """{target key: row} of a mode for the animation mode, with the notes about rows it had to change or leave out."""
    rows, notes = OrderedDict(), []
    for key, row in (mode.get("rows") or {}).items():
        target = ak.TARGETS_BY_KEY.get(key)
        if target is None or not target.signed:
            notes.append(f"{key} is not a camera motion, so it was left out.")
            continue
        if target.mode and animation_mode in ("2D", "3D") and target.mode != animation_mode:
            twin = TWINS.get(key)
            if twin is None:
                notes.append(f"{target.label} only works in {target.mode}, so it was left out in {animation_mode}.")
                continue
            if twin not in rows:
                rows[twin] = _twin_row(row, key)
                notes.append(f"{target.label} became {ak.TARGETS_BY_KEY[twin].label} for {animation_mode}.")
            continue
        rows[key] = dict(row)
    return rows, notes


def mode_parts(mode, analysis, animation_mode="3D"):
    """Song-parts rows for a song: split where the song changes (parts at least min_seconds long, or one part),
    with the mode's direction pattern repeated over the parts."""
    parts = mode.get("parts") or {}
    seconds = float(parts.get("min_seconds") or 0)
    starts = [0.0]
    if seconds > 0:
        starts = ak.split_auto(analysis, seconds)
        if len(starts) == 1 and analysis.duration > 2 * seconds:
            starts = ak.split_every(analysis.duration, 2 * seconds)
    pattern = [entry for entry in (parts.get("pattern") or []) if isinstance(entry, dict)] or [{}]

    def cell(entry, column):
        if column in entry:
            return str(entry[column])
        twin = COLUMN_TWINS.get(column)
        return str(entry[twin]) if twin in entry else "+"

    return [[ak.format_time(start)] + [cell(pattern[i % len(pattern)], column) for column in ak.PART_COLUMNS]
            for i, start in enumerate(starts)]


def _number_text(value, decimals):
    return "" if _is_empty(value) else ak.format_number(float(value), decimals)


def mode_settings(mode, analysis, animation_mode="3D"):
    """The Audio Keyframes settings a mode sets for a song: every camera row (unused ones unticked), the song parts
    and the crossfade. Returns ({setting name: value}, notes)."""
    rows, notes = mode_rows(mode, animation_mode)
    values = OrderedDict()
    for target in ak.TARGETS:
        if not target.signed:
            continue
        row = rows.get(target.key)
        values[f"{target.key}_enabled"] = row is not None
        if row is None:
            continue
        values[f"{target.key}_band"] = row.get("band") if row.get("band") in ak.BANDS else ak.BAND_FULL
        values[f"{target.key}_function"] = str(row.get("function") or ak.DEFAULT_FUNCTION)
        values[f"{target.key}_quiet"] = _number_text(row.get("quiet"), target.decimals)
        values[f"{target.key}_loud"] = _number_text(row.get("loud"), target.decimals)
        values[f"{target.key}_smooth"] = int(round(float(row.get("smooth") or 0)))
        values[f"{target.key}_switch"] = str(row.get("switch") or "")
    values["parts"] = mode_parts(mode, analysis, animation_mode)
    values["crossfade"] = min(int(round(float(mode.get("crossfade_seconds") or 0) * analysis.fps)), MAX_CROSSFADE_FRAMES)
    return values, notes


def check_mode(mode, name="The mode"):
    """Raises AudioKeyframeError when a mode could not drive the camera; returns it otherwise."""
    if not isinstance(mode, dict) or not isinstance(mode.get("rows"), dict) or not mode["rows"]:
        raise ak.AudioKeyframeError(f"{name} has no camera rows.")
    for key, row in mode["rows"].items():
        target = ak.TARGETS_BY_KEY.get(key)
        if target is None or not target.signed or not isinstance(row, dict):
            raise ak.AudioKeyframeError(f"{name}: {key} is not a camera motion.")
        ak.TargetSettings(key, band=row.get("band", ak.BAND_FULL), function=row.get("function"), quiet=row.get("quiet"),
                          loud=row.get("loud"), smooth=row.get("smooth", 0), switch=row.get("switch", ""))
    parts = mode.get("parts") or {}
    try:
        seconds = float(parts.get("min_seconds") or 0)
        crossfade = float(mode.get("crossfade_seconds") or 0)
    except (TypeError, ValueError):
        raise ak.AudioKeyframeError(f"{name}: the part length and the crossfade must be numbers.") from None
    if seconds < 0 or crossfade < 0:
        raise ak.AudioKeyframeError(f"{name}: the part length and the crossfade cannot be negative.")
    for entry in parts.get("pattern") or []:
        if not isinstance(entry, dict):
            raise ak.AudioKeyframeError(f"{name}: the song-part pattern must be a list of column: direction entries.")
        for column, cell in entry.items():
            if column not in ak.PART_COLUMNS:
                raise ak.AudioKeyframeError(f"{name}: {column} is not a song-parts column.")
            ak.parse_direction(cell)
    return mode


def _direction_text(direction):
    if direction == 1:
        return "+"
    if direction == -1:
        return "-"
    return ak.format_number(direction, 3)


def mode_from_settings(settings, fps, about="Saved from the Audio Keyframes tab."):
    """A mode from the Audio Keyframes tab as it is now: the ticked camera rows, the direction pattern of the song
    parts (split again for each song, at least 'Shortest auto part' long) and the crossfade."""
    rows = OrderedDict()
    for target in ak.TARGETS:
        if target.signed and settings.get(f"{target.key}_enabled"):
            rows[target.key] = {field: settings.get(f"{target.key}_{field}", "") for field in MODE_ROW_FIELDS}
    if not rows:
        raise ak.AudioKeyframeError("Tick at least one camera row (Zoom, Angle, Translation or Rotation) first.")
    columns = [ak.TARGETS_BY_KEY[key].column for key in rows]
    parts = ak.parse_parts(settings.get("parts"))
    pattern = [{column: _direction_text(directions[column]) for column in columns} for _, directions in parts] or \
              [{column: "+" for column in columns}]
    try:
        auto_min = float(settings.get("auto_min") or 8)
    except (TypeError, ValueError):
        auto_min = 8.0
    mode = {"about": about, "about_he": "נשמר מהטאב Audio Keyframes.", "rows": rows,
            "parts": {"min_seconds": auto_min if len(pattern) > 1 else 0, "pattern": pattern},
            "crossfade_seconds": round(float(settings.get("crossfade") or 0) / fps, 3)}
    return check_mode(mode)


# --- learning a mode from Motion schedules ---------------------------------------------------------------

def _fit_length(values, count):
    values = np.asarray(values, dtype=np.float64)
    if values.size >= count:
        return values[:count]
    return np.concatenate([values, np.full(count - values.size, values[-1] if values.size else 0.0)])


def _carried_signs(offsets, dead_zone):
    """+1 / -1 per frame; frames that hardly move keep the direction before them."""
    signs = np.where(np.abs(offsets) > dead_zone, np.sign(offsets), 0.0)
    moving = np.flatnonzero(signs)
    if moving.size == 0:
        return np.ones_like(offsets)
    index = np.where(signs != 0, np.arange(signs.size), 0)
    np.maximum.accumulate(index, out=index)
    carried = signs[index]
    carried[:moving[0]] = signs[moving[0]]
    return carried


def _best_fit(curves, speeds):
    """(band, exponent, smooth, correlation) whose shaped loudness follows the speeds best."""
    target = speeds - speeds.mean()
    norm = math.sqrt(float((target ** 2).sum()))
    best = (ak.BAND_FULL, 1, 0, 0.0)
    if norm < 1e-12:
        return best
    for band in ak.BANDS:
        for smooth in (0, 3, 8, 20):
            x = ak.smooth_release(curves[band], smooth)
            for exponent in (1, 2, 4):
                shaped = np.power(x, exponent)
                shaped = shaped - shaped.mean()
                denominator = math.sqrt(float((shaped ** 2).sum())) * norm
                if denominator < 1e-12:
                    continue
                correlation = float((shaped * target).sum() / denominator)
                if correlation > best[3] + 1e-9:
                    best = (band, exponent, smooth, correlation)
    return best


def _ramp_frames(offsets, changes, typical):
    """How many frames a direction change takes (0 for a hard switch)."""
    lengths = []
    for change in changes:
        window = np.abs(offsets[max(0, change - 60):change + 60])
        lengths.append(2 * int((window < 0.5 * typical).sum()))
    return float(np.median(lengths)) if lengths else 0.0


def learn_mode(analysis, schedules, normalize=ak.NORMALIZE_PEAK, threshold=0.0):
    """A camera mode from per-frame Motion schedules over the song ({target key: values}): for every axis that moves,
    which band and function its speed follows, and whether it switches direction every N frames, on the beats or with
    the parts of the song. Returns (mode, summary lines)."""
    fps, count = analysis.fps, analysis.frame_count
    curves = analysis.curves(normalize, threshold)
    beats = np.array(ak.beat_frames(curves[ak.BAND_FULL], fps, 0.25), dtype=np.int64)
    rows, first, alternate, part_seconds, fades, lines = OrderedDict(), {}, {}, [], [], []
    for key, raw in schedules.items():
        target = ak.TARGETS_BY_KEY.get(key)
        if target is None or not target.signed:
            continue
        offsets = _fit_length(raw, count) - target.neutral
        speeds = np.abs(offsets)
        if not np.isfinite(speeds).all() or speeds.max() < 1e-9:
            continue
        signs = _carried_signs(offsets, 0.02 * speeds.max())
        start = 1.0 if signs[0] > 0 else -1.0
        changes = np.flatnonzero(np.diff(signs) != 0) + 1
        switch, how = "", "keeps one direction"
        if changes.size:
            runs = np.diff(np.concatenate(([0], changes, [count])))
            inner = runs[1:-1]
            near_beats = float(np.mean([np.abs(beats - change).min() <= 2 for change in changes])) if beats.size else 0.0
            if inner.size >= 2 and inner.std() <= 0.05 * inner.mean() + 0.5:  # equal runs, give or take a frame
                every = max(1, int(round(float(np.median(inner)))))
                switch, how = str(every), f"switches direction every {every} frames"
            elif near_beats >= 0.5:
                gaps = np.diff(changes) / fps
                gap = max(0.25, math.floor(float(np.percentile(gaps, 10)) * 0.8 * 4) / 4) if gaps.size else ak.DEFAULT_BEAT_GAP
                switch, how = f"beats {gap:g}", f"switches direction on the beats, at least {gap:g} s apart"
            else:
                alternate[target.column] = start
                part_seconds.extend((runs / fps).tolist())
                fades.append(_ramp_frames(offsets, changes, float(np.median(speeds[speeds > 0.02 * speeds.max()]))))
                how = f"switches direction with the song parts ({changes.size} times)"
        band, exponent, smooth, correlation = _best_fit(curves, speeds)
        if correlation >= LEARN_MIN_CORRELATION:
            function = "x" if exponent == 1 else f"x^{exponent}"
            quiet = target.neutral + float(np.percentile(speeds, 5))
            loud = target.neutral + float(np.percentile(speeds, 95))
            speed = (f"{band} · {function} · {ak.format_number(quiet, target.decimals)} → "
                     f"{ak.format_number(loud, target.decimals)}")
        else:
            function, smooth = "1", 0
            quiet = loud = target.neutral + float(np.median(speeds))
            speed = f"a steady {ak.format_number(quiet, target.decimals)}"
        rows[key] = {"band": band, "function": function, "quiet": ak.format_number(quiet, target.decimals),
                     "loud": ak.format_number(loud, target.decimals), "smooth": smooth, "switch": switch}
        first[target.column] = "+" if start > 0 else "-"
        lines.append(f"{target.label}: {speed}, {how}")
    if not rows:
        raise ak.AudioKeyframeError("The Motion schedules do not move the camera, so there is nothing to learn.")
    pattern, min_seconds = [dict(first)], 0.0
    if alternate:
        second = dict(first)
        second.update({column: "-" if start > 0 else "+" for column, start in alternate.items()})
        pattern, min_seconds = [dict(first), second], round(float(np.median(part_seconds)), 1)
    mode = {"about": "Learned from Motion schedules. " + " · ".join(lines),
            "about_he": "נלמד מתנועות המצלמה שבטאב Motion.", "rows": rows,
            "parts": {"min_seconds": min_seconds, "pattern": pattern},
            "crossfade_seconds": round(float(np.median(fades)) / fps, 2) if fades else 0.0}
    return check_mode(mode, "The learned mode"), lines


# --- saved modes -----------------------------------------------------------------------------------------

def load_user_modes(path):
    if not path or not os.path.isfile(path):
        return OrderedDict()
    with open(path, encoding="utf-8") as f:
        data = json.load(f, object_pairs_hook=OrderedDict)
    modes = OrderedDict()
    for name, mode in ((data.get("modes") if isinstance(data, dict) else None) or {}).items():
        try:
            modes[str(name)] = check_mode(mode, str(name))
        except (ak.AudioKeyframeError, TypeError, ValueError) as err:
            print(f"Camera mode {name!r} was skipped: {err}")
    return modes


def save_user_modes(path, modes):
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    partial = path + ".partial"
    with open(partial, "w", encoding="utf-8") as f:
        json.dump({"version": 1, "modes": modes}, f, ensure_ascii=False, indent=2)
    os.replace(partial, path)


def all_modes(path):
    modes = OrderedDict(BUILTIN_MODES)
    modes.update((name, mode) for name, mode in load_user_modes(path).items() if name not in BUILTIN_MODES)
    return modes


def add_user_mode(path, name, mode):
    """Saves a mode under a name; returns True when it replaced a saved mode of that name."""
    name = str(name or "").strip()
    if not name:
        raise ak.AudioKeyframeError("Give the new mode a name.")
    if name in BUILTIN_MODES:
        raise ak.AudioKeyframeError(f"{name} is a built-in mode. Choose another name.")
    check_mode(mode, name)
    modes = load_user_modes(path)
    replaced = name in modes
    modes[name] = mode
    save_user_modes(path, modes)
    return replaced


def delete_user_mode(path, name):
    if name in BUILTIN_MODES:
        raise ak.AudioKeyframeError("Built-in modes cannot be deleted.")
    modes = load_user_modes(path)
    if name not in modes:
        raise ak.AudioKeyframeError(f"There is no saved mode called {name}.")
    del modes[name]
    save_user_modes(path, modes)

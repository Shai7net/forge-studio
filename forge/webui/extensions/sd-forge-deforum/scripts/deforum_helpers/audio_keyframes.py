# Audio Keyframes for Deforum: turn a song into per-frame Deforum schedules.
#
# Pure module (numpy, scipy and the standard library only - no gradio or webui imports), so it can be
# unit-tested outside Forge: run `python -m unittest deforum_helpers.audio_keyframes_test` from /scripts.
#
# The loudness arithmetic and the safe formula parser are adapted from the Infinite Zoom extension's port of
# https://www.chigozie.co.uk/audio-keyframe-generator/ (block mean of |L|+|R|, peak normalised, f(x, y)).

import ast
import json
import math
import os
import re
import subprocess
import threading
from collections import OrderedDict
from decimal import Decimal, ROUND_HALF_UP

import numpy as np

DECODE_SAMPLE_RATE = 22050
MAX_FRAMES = 250_000
GENERATOR_URL = "https://www.chigozie.co.uk/audio-keyframe-generator/"
DEFAULT_FUNCTION = "1 + x^4"

BAND_FULL, BAND_BASS, BAND_MIDS, BAND_HIGHS = "Full", "Bass", "Mids", "Highs"
BANDS = [BAND_FULL, BAND_BASS, BAND_MIDS, BAND_HIGHS]
BASS_CUTOFF_HZ = 150.0
HIGHS_CUTOFF_HZ = 2000.0

NORMALIZE_PEAK = "Peak (like the website)"
NORMALIZE_ROBUST = "Robust (98th percentile)"
NORMALIZE_MODES = [NORMALIZE_PEAK, NORMALIZE_ROBUST]


class AudioKeyframeError(ValueError):
    pass


class Target:
    """A Deforum schedule the music can drive.

    signed targets are camera motions: the song-parts table flips them around `neutral`
    (0 for moves and rotations, 1 for zoom).
    """

    def __init__(self, key, label, group, quiet, loud, decimals, neutral=None, column=None, mode=None,
                 lo=None, hi=None):
        self.key = key
        self.label = label
        self.group = group
        self.quiet = quiet
        self.loud = loud
        self.decimals = decimals
        self.neutral = neutral
        self.column = column
        self.mode = mode  # '2D', '3D' or None (both)
        self.lo = lo
        self.hi = hi

    @property
    def signed(self):
        return self.column is not None


TARGETS = [
    Target("strength_schedule", "Strength schedule", "image", 0.60, 0.80, 3),
    Target("cfg_scale_schedule", "CFG scale schedule", "image", 6.0, 8.0, 2),
    Target("noise_schedule", "Noise schedule", "image", 0.04, 0.08, 3),
    Target("zoom", "Zoom", "motion", 1.0, 1.04, 4, neutral=1.0, column="Zoom", mode="2D"),
    Target("angle", "Angle", "motion", 0.0, 1.0, 3, neutral=0.0, column="Angle", mode="2D"),
    Target("translation_x", "Translation X", "motion", 0.0, 4.0, 3, neutral=0.0, column="X"),
    Target("translation_y", "Translation Y", "motion", 0.0, 4.0, 3, neutral=0.0, column="Y"),
    Target("translation_z", "Translation Z", "motion", 0.4, 1.0, 3, neutral=0.0, column="Z", mode="3D"),
    Target("rotation_3d_x", "Rotation 3D X", "motion", 0.0, 1.0, 3, neutral=0.0, column="Rot X", mode="3D"),
    Target("rotation_3d_y", "Rotation 3D Y", "motion", 0.0, 1.0, 3, neutral=0.0, column="Rot Y", mode="3D"),
    Target("rotation_3d_z", "Rotation 3D Z", "motion", 0.0, 1.0, 3, neutral=0.0, column="Rot Z", mode="3D"),
    Target("fov_schedule", "FOV schedule", "more", 70.0, 60.0, 2, mode="3D"),
    Target("transform_center_x", "Transform Center X", "more", 0.5, 0.6, 3, mode="2D"),
    Target("transform_center_y", "Transform Center Y", "more", 0.5, 0.6, 3, mode="2D"),
    Target("perspective_flip_theta", "Perspective flip theta", "more", 0.0, 2.0, 3, neutral=0.0, column="Flip theta"),
    Target("perspective_flip_phi", "Perspective flip phi", "more", 0.0, 2.0, 3, neutral=0.0, column="Flip phi"),
    Target("perspective_flip_gamma", "Perspective flip gamma", "more", 0.0, 2.0, 3, neutral=0.0, column="Flip gamma"),
    Target("perspective_flip_fv", "Perspective flip tv", "more", 53.0, 60.0, 2),
]
TARGETS_BY_KEY = {t.key: t for t in TARGETS}
PART_COLUMNS = [t.column for t in TARGETS if t.signed]
PART_HEADERS = ["Start"] + PART_COLUMNS
PERSPECTIVE_FLIP_KEYS = {"perspective_flip_theta", "perspective_flip_phi", "perspective_flip_gamma", "perspective_flip_fv"}


# --- sources and decoding --------------------------------------------------------------------------------

def clean_path(text):
    """Strips spaces and the quotes Windows' "Copy as path" adds."""
    if text is None:
        return ""
    value = str(text).strip()
    while len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1].strip()
    return value


def is_url(text):
    return str(text).lower().startswith(("http://", "https://"))


def _run(args, timeout=None):
    kwargs = {"creationflags": 0x08000000} if os.name == "nt" else {}  # CREATE_NO_WINDOW
    return subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          timeout=timeout, **kwargs)


def _tail(stderr, lines=4):
    text = stderr.decode("utf-8", errors="replace").strip().splitlines()
    return " | ".join(text[-lines:]) if text else "no details"


class Audio:
    def __init__(self, source, pcm, sample_rate):
        self.source = source
        self.pcm = pcm
        self.sample_rate = sample_rate

    @property
    def name(self):
        return self.source.rstrip("/\\").replace("\\", "/").split("/")[-1].split("?")[0]

    @property
    def duration(self):
        return self.pcm.shape[0] / self.sample_rate


_decode_cache = OrderedDict()
_decode_lock = threading.Lock()


def _source_key(source, sample_rate):
    if is_url(source):
        return source, None, None, sample_rate
    stat = os.stat(source)
    return os.path.realpath(source), stat.st_mtime_ns, stat.st_size, sample_rate


def decode_audio(source, ffmpeg, sample_rate=DECODE_SAMPLE_RATE):
    """First audio track of a file or URL as (samples, 2) float32; mono is duplicated, surround down-mixed."""
    source = clean_path(source)
    if not source:
        raise AudioKeyframeError("Choose a music file (upload it, or paste its path or URL).")
    if not is_url(source) and not os.path.isfile(source):
        raise AudioKeyframeError(f"The music file was not found: {source}")
    key = _source_key(source, sample_rate)
    with _decode_lock:
        if key in _decode_cache:
            _decode_cache.move_to_end(key)
            return _decode_cache[key]
    result = _run([ffmpeg, "-hide_banner", "-v", "error", "-nostdin", "-i", source, "-map", "0:a:0", "-vn",
                   "-ac", "2", "-ar", str(sample_rate), "-f", "f32le", "-acodec", "pcm_f32le", "-"])
    usable = len(result.stdout) // 8 * 8
    if result.returncode != 0 or usable == 0:
        raise AudioKeyframeError(f"ffmpeg could not read the audio of {source}: {_tail(result.stderr)}")
    pcm = np.frombuffer(result.stdout[:usable], dtype=np.float32).reshape(-1, 2)
    audio = Audio(source, pcm, sample_rate)
    with _decode_lock:
        _decode_cache[key] = audio
        while len(_decode_cache) > 2:
            _decode_cache.popitem(last=False)
    return audio


# --- loudness per frame ----------------------------------------------------------------------------------

def website_loudness(pcm, sample_rate, fps):
    """Per-frame loudness computed like the website's filterData (before normalising).

    |channel| values are summed and divided by the channel count in float32, frames are blocks of
    floor(len / (duration * fps)) samples whose last sample is skipped, and each frame is the mean of its block.
    There are ceil(duration * fps) frames, which is also the Deforum Max frames for the song.
    """
    pcm = np.asarray(pcm, dtype=np.float32)
    if pcm.ndim == 1:
        pcm = pcm[:, None]
    length, channels = pcm.shape
    if length == 0:
        raise AudioKeyframeError("The music contains no audio samples.")
    if not fps or fps <= 0:
        raise AudioKeyframeError("FPS must be positive.")

    raw = np.abs(pcm[:, 0])
    for channel in range(1, channels):
        raw = np.abs(raw) + np.abs(pcm[:, channel])
    if channels > 1:
        raw = raw / np.float32(channels)

    samples = (length / sample_rate) * fps
    count = int(math.ceil(samples))
    if count > MAX_FRAMES:
        raise AudioKeyframeError(f"{count:,} frames is too many (the limit is {MAX_FRAMES:,}). Lower the FPS.")
    block = int(math.floor(length / samples))
    loudness = np.empty(count, dtype=np.float64)
    for i in range(count):
        start = i * block
        end = min((i + 1) * block - 1, length)
        if end <= start:
            loudness[i] = math.nan
        else:
            loudness[i] = np.cumsum(raw[start:end], dtype=np.float64)[-1] / (end - start)
    return loudness


def band_signal(mono, sample_rate, band):
    """Zero-phase Butterworth filter of a mono signal for the Bass, Mids or Highs band."""
    from scipy.signal import butter, sosfiltfilt

    mono = np.asarray(mono, dtype=np.float64)
    top = sample_rate / 2 * 0.99
    if band == BAND_BASS:
        sos = butter(4, min(BASS_CUTOFF_HZ, top), btype="lowpass", fs=sample_rate, output="sos")
    elif band == BAND_MIDS:
        sos = butter(4, [BASS_CUTOFF_HZ, min(HIGHS_CUTOFF_HZ, top)], btype="bandpass", fs=sample_rate, output="sos")
    elif band == BAND_HIGHS:
        sos = butter(4, min(HIGHS_CUTOFF_HZ, top), btype="highpass", fs=sample_rate, output="sos")
    else:
        raise AudioKeyframeError(f"Unknown band {band!r}. Use one of: {', '.join(BANDS)}.")
    if mono.shape[0] <= 3 * (2 * len(sos) + 1):
        return mono.astype(np.float32)  # too short to filter
    return sosfiltfilt(sos, mono).astype(np.float32)


class Analysis:
    """Raw (not normalised) loudness per frame for every band of one song at one FPS."""

    def __init__(self, source, duration, sample_rate, fps, loudness):
        self.source = source
        self.duration = duration
        self.sample_rate = sample_rate
        self.fps = float(fps)
        self.loudness = loudness

    @property
    def name(self):
        return Audio(self.source, np.zeros((0, 2), np.float32), self.sample_rate).name or "audio"

    @property
    def frame_count(self):
        return int(len(self.loudness[BAND_FULL]))

    def curve(self, band, normalize=NORMALIZE_PEAK, threshold=0.0):
        if band not in self.loudness:
            raise AudioKeyframeError(f"Unknown band {band!r}. Use one of: {', '.join(BANDS)}.")
        return apply_threshold(normalize_values(self.loudness[band], normalize), threshold)

    def curves(self, normalize=NORMALIZE_PEAK, threshold=0.0):
        return {band: self.curve(band, normalize, threshold) for band in BANDS}

    def describe(self):
        return (f"**{self.name}** · {format_time(self.duration)} · **{self.frame_count:,} frames** "
                f"at {self.fps:g} fps")


def analysis_from_pcm(pcm, sample_rate, fps, source=""):
    pcm = np.asarray(pcm, dtype=np.float32)
    if pcm.ndim == 1:
        pcm = np.stack([pcm, pcm], axis=1)
    loudness = {BAND_FULL: np.nan_to_num(website_loudness(pcm, sample_rate, fps), nan=0.0)}
    mono = pcm.mean(axis=1)
    for band in (BAND_BASS, BAND_MIDS, BAND_HIGHS):
        loudness[band] = np.nan_to_num(website_loudness(band_signal(mono, sample_rate, band), sample_rate, fps), nan=0.0)
    return Analysis(source, pcm.shape[0] / sample_rate, sample_rate, fps, loudness)


_analysis_cache = OrderedDict()
_analysis_lock = threading.Lock()


def analyze_audio(source, fps, ffmpeg, sample_rate=DECODE_SAMPLE_RATE):
    try:
        fps = float(fps)
    except (TypeError, ValueError):
        raise AudioKeyframeError("FPS must be a number.") from None
    if not 0 < fps <= 240:
        raise AudioKeyframeError("FPS must be between 1 and 240.")
    audio = decode_audio(source, ffmpeg, sample_rate)
    key = (_source_key(audio.source, sample_rate), fps)
    with _analysis_lock:
        if key in _analysis_cache:
            _analysis_cache.move_to_end(key)
            return _analysis_cache[key]
    analysis = analysis_from_pcm(audio.pcm, sample_rate, fps, audio.source)
    with _analysis_lock:
        _analysis_cache[key] = analysis
        while len(_analysis_cache) > 4:
            _analysis_cache.popitem(last=False)
    return analysis


# --- shaping ---------------------------------------------------------------------------------------------

def normalize_values(values, mode=NORMALIZE_PEAK):
    """0..1 curve: divided by the loudest frame (website) or by the 98th percentile, clipped at 1."""
    values = np.nan_to_num(np.asarray(values, dtype=np.float64), nan=0.0, posinf=0.0, neginf=0.0)
    if values.size == 0:
        return values
    reference = float(np.percentile(values, 98)) if mode == NORMALIZE_ROBUST else float(values.max())
    if reference <= 0:
        reference = float(values.max())
    if reference <= 0:
        return np.zeros_like(values)
    return np.clip(values / reference, 0.0, 1.0)


def apply_threshold(x, threshold):
    """Ignores everything below the threshold and stretches the rest back to 0..1."""
    x = np.asarray(x, dtype=np.float64)
    t = min(max(float(threshold or 0.0), 0.0), 0.99)
    if t == 0:
        return x.copy()
    return np.clip((x - t) / (1.0 - t), 0.0, 1.0)


def smooth_release(x, frames):
    """Rises instantly with the music and falls back over about `frames` frames (0 = raw)."""
    x = np.asarray(x, dtype=np.float64)
    frames = float(frames or 0)
    if frames <= 0 or x.size == 0:
        return x.copy()
    decay = math.exp(-1.0 / frames)
    out = np.empty_like(x)
    level = 0.0
    for i, value in enumerate(x.tolist()):
        level = max(value, level * decay)
        out[i] = level
    return out


def stretch(values, quiet, loud):
    """The song's lowest value becomes quiet and its highest becomes loud; the function decides the shape between."""
    values = np.asarray(values, dtype=np.float64)
    low, high = float(values.min()), float(values.max())
    if not high - low > 1e-12:
        return np.full(values.shape, float(quiet))
    return float(quiet) + (float(loud) - float(quiet)) * (values - low) / (high - low)


# --- formulas (parsed and whitelisted, never passed to eval) ---------------------------------------------

FORMULA_VARIABLES = ("x", "y", "t", "sec", "full", "bass", "mids", "highs")
_FUNCTIONS = {
    "sin": (np.sin, 1), "cos": (np.cos, 1), "tan": (np.tan, 1),
    "asin": (np.arcsin, 1), "acos": (np.arccos, 1), "atan": (np.arctan, 1),
    "sqrt": (np.sqrt, 1), "abs": (np.abs, 1), "exp": (np.exp, 1),
    "log10": (np.log10, 1), "log2": (np.log2, 1),
    "floor": (np.floor, 1), "ceil": (np.ceil, 1), "round": (np.round, 1),
    "min": (np.minimum, 2), "max": (np.maximum, 2), "pow": (np.power, 2), "clip": (np.clip, 3),
}
_CONSTANTS = {"pi": math.pi, "e": math.e}
_BINARY = {ast.Add: np.add, ast.Sub: np.subtract, ast.Mult: np.multiply,
           ast.Div: np.divide, ast.Mod: np.mod, ast.Pow: np.power}


def _check_node(node, variables):
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise AudioKeyframeError(f"Only numbers are allowed, not {node.value!r}.")
    elif isinstance(node, ast.Name):
        if node.id not in variables and node.id not in _CONSTANTS:
            raise AudioKeyframeError(f"Unknown name {node.id!r}. Use {', '.join(variables)}, pi or e.")
    elif isinstance(node, ast.UnaryOp):
        if not isinstance(node.op, (ast.UAdd, ast.USub)):
            raise AudioKeyframeError("Only + and - are allowed before a value.")
        _check_node(node.operand, variables)
    elif isinstance(node, ast.BinOp):
        if type(node.op) not in _BINARY:
            raise AudioKeyframeError("Allowed operators: + - * / % ^")
        _check_node(node.left, variables)
        _check_node(node.right, variables)
    elif isinstance(node, ast.Call):
        name = node.func.id if isinstance(node.func, ast.Name) else None
        if node.keywords or name is None or (name not in _FUNCTIONS and name != "log"):
            raise AudioKeyframeError(f"Function not allowed. Available: {', '.join(sorted(list(_FUNCTIONS) + ['log']))}.")
        arity = (1, 2) if name == "log" else (_FUNCTIONS[name][1],)
        if len(node.args) not in arity:
            raise AudioKeyframeError(f"{name}() takes {' or '.join(map(str, arity))} argument(s).")
        for arg in node.args:
            _check_node(arg, variables)
    else:
        raise AudioKeyframeError(f"{type(node).__name__} is not allowed in a formula.")


def _evaluate_node(node, env):
    if isinstance(node, ast.Constant):
        return float(node.value)
    if isinstance(node, ast.Name):
        if node.id in env:
            return env[node.id]
        if node.id in _CONSTANTS:
            return _CONSTANTS[node.id]
        raise AudioKeyframeError(f"{node.id!r} is not available here.")
    if isinstance(node, ast.UnaryOp):
        value = _evaluate_node(node.operand, env)
        return -value if isinstance(node.op, ast.USub) else value
    if isinstance(node, ast.BinOp):
        return _BINARY[type(node.op)](_evaluate_node(node.left, env), _evaluate_node(node.right, env))
    args = [_evaluate_node(arg, env) for arg in node.args]
    if node.func.id == "log":
        return np.log(args[0]) if len(args) == 1 else np.log(args[0]) / np.log(args[1])
    return _FUNCTIONS[node.func.id][0](*args)


def compile_formula(expression, variables=FORMULA_VARIABLES):
    """'1 + x^4' -> f(env) over numpy arrays. '^' is a power, as on the website."""
    source = (expression or "").strip()
    if not source:
        raise AudioKeyframeError("The formula is empty.")
    if len(source) > 500:
        raise AudioKeyframeError("The formula is too long.")
    try:
        tree = ast.parse(source.replace("^", "**"), mode="eval")
    except SyntaxError as err:
        raise AudioKeyframeError(f"Invalid formula {source!r}: {err.msg}.") from None
    _check_node(tree.body, variables)

    def evaluate(env):
        arrays = {name: np.asarray(value, dtype=np.float64) for name, value in env.items()}
        length = max((a.size for a in arrays.values()), default=1)
        with np.errstate(all="ignore"):
            result = _evaluate_node(tree.body, arrays)
        return np.broadcast_to(np.asarray(result, dtype=np.float64), (length,)).copy()

    return evaluate


_TARGET_ALIASES = {
    "strength": "strength_schedule", "cfg": "cfg_scale_schedule", "cfg_scale": "cfg_scale_schedule",
    "noise": "noise_schedule", "fov": "fov_schedule",
    "rotation_x": "rotation_3d_x", "rotation_y": "rotation_3d_y", "rotation_z": "rotation_3d_z",
    "rot_x": "rotation_3d_x", "rot_y": "rotation_3d_y", "rot_z": "rotation_3d_z",
}


def _name_key(name):
    return re.sub(r"[\s\-]+", "_", str(name).strip().lower())


def target_for_name(name):
    key = _name_key(name)
    key = _TARGET_ALIASES.get(key, key)
    if key in TARGETS_BY_KEY:
        return TARGETS_BY_KEY[key]
    for target in TARGETS:
        if _name_key(target.label) == key:
            return target
    raise AudioKeyframeError(f"Unknown parameter {name!r}. Use e.g. strength_schedule, zoom, translation_z, rotation_3d_y.")


def upgrade_settings(data):
    """Settings saved before each row had its own Function. Curve c becomes the function x^c, and a line of the old
    custom formulas ('translation_z = 0.5 + 3*bass^2') becomes that row's function with Quiet and Loud left empty,
    which keeps the formula's own values as before."""
    data = dict(data)
    formulas = {}
    for line in str(data.pop("formulas", "") or "").splitlines():
        line = line.split("#", 1)[0].strip()
        if "=" not in line:
            continue
        name, expression = line.split("=", 1)
        try:
            formulas[target_for_name(name).key] = expression.strip()
        except AudioKeyframeError:
            continue
    for target in TARGETS:
        key = target.key
        curve = data.pop(f"{key}_curve", None)
        if key in formulas:
            data.update({f"{key}_function": formulas[key], f"{key}_quiet": "", f"{key}_loud": ""})
        elif f"{key}_function" not in data and curve is not None:
            try:
                exponent = parse_optional_number(curve, "curve")
            except AudioKeyframeError:
                exponent = None
            valid = exponent is not None and exponent > 0 and exponent != 1
            data[f"{key}_function"] = f"x^{format_number(exponent, 3)}" if valid else "x"
    return data


# --- time, song parts and directions ---------------------------------------------------------------------

def parse_time(text):
    """'1:02.5', '0:10', '10.5' or '10.5s' -> seconds."""
    value = "" if text is None else str(text).strip().lower().replace(",", ".")
    if value.endswith("s"):
        value = value[:-1].strip()
    if not value:
        raise AudioKeyframeError("the start time is empty")
    fields = value.split(":")
    try:
        numbers = [float(field) for field in fields]
    except ValueError:
        raise AudioKeyframeError(f"{text!r} is not a time like 0:10 or 10.5") from None
    if len(numbers) > 3 or any(not math.isfinite(n) or n < 0 for n in numbers) or \
            any(not n.is_integer() for n in numbers[:-1]):
        raise AudioKeyframeError(f"{text!r} is not a time like 0:10 or 10.5")
    seconds = 0.0
    for number in numbers:
        seconds = seconds * 60 + number
    return seconds


def format_time(seconds):
    minutes, rest = divmod(max(float(seconds), 0.0), 60)
    rest = round(rest, 1)
    if rest >= 60:
        minutes, rest = minutes + 1, rest - 60
    text = f"{int(minutes)}:{rest:04.1f}"
    return text[:-2] if text.endswith(".0") else text


def format_time_precise(seconds):
    """m:ss.sss, exact enough to land on a frame even at 120 fps: 2.3333 -> '0:02.333', 5 -> '0:05'."""
    minutes, rest = divmod(max(float(seconds), 0.0), 60)
    rest = round(rest, 3)
    if rest >= 60:
        minutes, rest = minutes + 1, rest - 60
    return f"{int(minutes)}:{rest:06.3f}".rstrip("0").rstrip(".")


_DASHES = ("−", "–", "—")


def parse_direction(cell):
    """'+' or empty -> 1, '-' -> -1, '0' -> 0, or any number (0.5 = half the movement)."""
    if cell is None:
        return 1.0
    if isinstance(cell, (int, float)) and not isinstance(cell, bool):
        return float(cell) if math.isfinite(cell) else 1.0  # NaN = empty numeric cell
    text = str(cell).strip().lower()
    for dash in _DASHES:
        text = text.replace(dash, "-")
    if text in ("", "+", "+1", "nan"):
        return 1.0
    if text == "-":
        return -1.0
    if text in ("off", "none"):
        return 0.0
    try:
        value = float(text)
    except ValueError:
        raise AudioKeyframeError(f"{cell!r} is not +, -, 0 or a number") from None
    if not math.isfinite(value):
        raise AudioKeyframeError(f"{cell!r} is not +, -, 0 or a number")
    return value


def _blank(cell):
    return cell is None or (isinstance(cell, float) and math.isnan(cell)) or str(cell).strip() == ""


def parse_parts(rows):
    """Song-parts table rows [start, direction per column...] -> [(seconds, {column: direction})], sorted."""
    parts = {}
    for index, row in enumerate(rows or [], 1):
        cells = list(row) if row is not None else []
        if all(_blank(cell) for cell in cells):
            continue
        try:
            start = parse_time(cells[0] if cells else "")
        except AudioKeyframeError as err:
            raise AudioKeyframeError(f"Song part {index}: {err}.") from None
        directions = {}
        for column_index, column in enumerate(PART_COLUMNS, 1):
            cell = cells[column_index] if column_index < len(cells) else None
            try:
                directions[column] = parse_direction(cell)
            except AudioKeyframeError as err:
                raise AudioKeyframeError(f"Song part {index}, column {column}: {err}.") from None
        parts[round(start, 6)] = directions
    return [(start, parts[start]) for start in sorted(parts)]


def part_start_frames(parts, fps, frame_count):
    """{start frame: directions}. The first part always starts at frame 0; a later part on the same frame wins."""
    by_frame = OrderedDict()
    for index, (start, directions) in enumerate(parts or [(0.0, {c: 1.0 for c in PART_COLUMNS})]):
        frame = 0 if index == 0 else int(round(start * fps))
        if frame < frame_count:
            by_frame[frame] = directions
    return by_frame


def direction_curves(parts, fps, frame_count, crossfade_frames=0):
    """Per-frame direction for every column of the song-parts table, with an optional linear crossfade."""
    by_frame = part_start_frames(parts, fps, frame_count)
    frames, directions = list(by_frame.keys()), list(by_frame.values())
    fade = max(int(round(float(crossfade_frames or 0))), 0)
    curves = {}
    for column in PART_COLUMNS:
        curve = np.empty(frame_count, dtype=np.float64)
        for i, start in enumerate(frames):
            end = frames[i + 1] if i + 1 < len(frames) else frame_count
            curve[start:end] = directions[i][column]
        if fade > 1:
            for i in range(1, len(frames)):
                start = frames[i]
                end = frames[i + 1] if i + 1 < len(frames) else frame_count
                previous, target = directions[i - 1][column], directions[i][column]
                length = min(fade, end - start)
                if previous != target and length > 0:
                    steps = np.arange(1, length + 1, dtype=np.float64) / length
                    curve[start:start + length] = previous + (target - previous) * steps
        curves[column] = curve
    return curves


def split_equal(duration, count):
    count = int(count)
    if not 1 <= count <= 200:
        raise AudioKeyframeError("The number of parts must be between 1 and 200.")
    return [duration * i / count for i in range(count)]


def split_every(duration, seconds):
    seconds = float(seconds)
    if seconds <= 0:
        raise AudioKeyframeError("The part length must be above 0 seconds.")
    count = max(int(math.ceil(duration / seconds - 1e-9)), 1)
    if count > 500:
        raise AudioKeyframeError(f"That makes {count} parts; use a longer part length.")
    return [i * seconds for i in range(count)]


def split_auto(analysis, min_part_seconds=8.0, window_seconds=4.0, max_parts=24):
    """Part starts where the song changes: the band profile of the seconds before and after differ most."""
    features = np.stack([normalize_values(analysis.loudness[band]) for band in BANDS], axis=1)
    hop = max(1, int(round(analysis.fps / 4)))
    count = features.shape[0] // hop
    rate = analysis.fps / hop
    window = max(2, int(round(float(window_seconds) * rate)))
    if count <= 2 * window:
        return [0.0]
    coarse = features[:count * hop].reshape(count, hop, -1).mean(axis=1)
    cumulative = np.vstack([np.zeros((1, coarse.shape[1])), np.cumsum(coarse, axis=0)])
    index = np.arange(window, count - window + 1)
    past = (cumulative[index] - cumulative[index - window]) / window
    future = (cumulative[index + window] - cumulative[index]) / window
    novelty = np.zeros(count + 1)
    novelty[index] = np.abs(future - past).sum(axis=1)
    active = novelty[novelty > 0]
    if active.size == 0:
        return [0.0]
    level = active.mean() + 0.5 * active.std()
    gap = max(1, int(round(float(min_part_seconds) * rate)))
    peaks = [i for i in index if novelty[i] > level and novelty[i] >= novelty[i - 1] and novelty[i] >= novelty[i + 1]]
    picked = []
    for i in sorted(peaks, key=lambda i: -novelty[i]):
        if i < gap or count - i < gap // 2 or any(abs(i - p) < gap for p in picked):
            continue
        picked.append(i)
        if len(picked) >= max_parts - 1:
            break
    return [0.0] + sorted(p * hop / analysis.fps for p in picked)


def default_part_rows():
    return [["0:00"] + ["+"] * len(PART_COLUMNS)]


def part_rows(starts, template_rows=None):
    """Table rows for part starts; the directions repeat the existing rows' pattern in order (all + if none)."""
    patterns = []
    for row in template_rows or []:
        cells = list(row) if row is not None else []
        if all(_blank(cell) for cell in cells):
            continue
        pattern = ["" if _blank(cell) else str(cell).strip() for cell in cells[1:len(PART_HEADERS)]]
        patterns.append(pattern + ["+"] * (len(PART_COLUMNS) - len(pattern)))
    if not patterns:
        patterns = [["+"] * len(PART_COLUMNS)]
    return [[format_time(start)] + list(patterns[i % len(patterns)]) for i, start in enumerate(starts)]


def insert_part(rows, seconds, fps):
    """Song-parts rows with a new part starting on the frame at `seconds`. The new row copies the directions of the part
    it splits, so nothing moves differently until you edit it. Returns (rows, added); a part already starting on that
    frame is left as it is."""
    parse_parts(rows)  # the table has to be valid before a row is added
    kept = []
    for row in rows or []:
        cells = list(row) if row is not None else []
        if all(_blank(cell) for cell in cells):
            continue
        cells = ["" if _blank(cell) else str(cell).strip() for cell in cells[:len(PART_HEADERS)]]
        kept.append((parse_time(cells[0]), cells + ["+"] * (len(PART_HEADERS) - len(cells))))
    if not kept:
        kept = [(0.0, default_part_rows()[0])]
    kept.sort(key=lambda item: item[0])
    frame = max(int(round(float(seconds) * fps)), 0)
    starts = [0 if index == 0 else int(round(start * fps)) for index, (start, _) in enumerate(kept)]
    if frame in starts:
        return [cells for _, cells in kept], False
    active = [cells for (_, cells), start in zip(kept, starts) if start <= frame][-1]
    kept.append((frame / fps, [format_time_precise(frame / fps)] + active[1:]))
    kept.sort(key=lambda item: item[0])
    return [cells for _, cells in kept], True


# --- Deforum schedules -----------------------------------------------------------------------------------

def format_number(value, decimals):
    """Plain fixed-point text Deforum can parse: no exponent, no trailing zeros, no '-0'."""
    value = float(value)
    if not math.isfinite(value):
        raise AudioKeyframeError("A value is not a finite number (check the formula).")
    text = f"{value:.{int(decimals)}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def schedule_text(values, decimals, compress=True):
    """'0: (0.8), 12: (0.66), ...'. Runs of one value keep only their ends; Deforum interpolates between keys."""
    texts = [format_number(v, decimals) for v in np.asarray(values, dtype=np.float64).tolist()]
    if not texts:
        raise AudioKeyframeError("There are no frames.")
    last = len(texts) - 1
    if compress:
        keep = [i for i in range(len(texts))
                if i in (0, last) or texts[i] != texts[i - 1] or texts[i] != texts[i + 1]]
    else:
        keep = range(len(texts))
    return ", ".join(f"{i}: ({texts[i]})" for i in keep)


def parse_optional_number(value, what):
    """Number from a UI field; empty means 'not set' (None)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else None
    text = str(value).strip().replace(",", ".")
    for dash in _DASHES:
        text = text.replace(dash, "-")
    if text in ("", "-", "none"):
        return None
    try:
        number = float(text)
    except ValueError:
        raise AudioKeyframeError(f"{what}: {value!r} is not a number.") from None
    if not math.isfinite(number):
        raise AudioKeyframeError(f"{what}: {value!r} is not a number.")
    return number


SWITCH_OFF, SWITCH_EVERY, SWITCH_BEATS = "off", "every", "beats"
DEFAULT_BEAT_GAP = 0.5


def parse_switch(text, what="Switch"):
    """How a camera motion switches direction: '' / 0 / off -> never, '10' -> every 10 frames,
    'beats' or 'beats 1.5' -> on loudness jumps at least that many seconds apart (0.5 by default)."""
    value = "" if text is None else str(text).strip().lower().replace(",", ".")
    if value in ("", "0", "off", "none", "no"):
        return SWITCH_OFF, 0
    match = re.fullmatch(r"beats?(?:\s*[:=]?\s*([0-9]*\.?[0-9]+))?\s*(?:s|sec)?", value)
    if match:
        gap = float(match.group(1)) if match.group(1) else DEFAULT_BEAT_GAP
        if not gap > 0:
            raise AudioKeyframeError(f"{what}: the gap between beats must be above 0 seconds.")
        return SWITCH_BEATS, gap
    try:
        frames = float(value)
    except ValueError:
        raise AudioKeyframeError(f"{what}: write off, a number of frames, or beats (e.g. beats 1.5).") from None
    if not math.isfinite(frames) or not frames.is_integer() or frames < 1:
        raise AudioKeyframeError(f"{what}: the number of frames must be a whole number of 1 or more.")
    return SWITCH_EVERY, int(frames)


def beat_frames(x, fps, gap_seconds):
    """Frames of clear loudness jumps, at least gap_seconds apart (never frame 0)."""
    scores = jump_scores(x, fps)
    positive = scores[scores > 0]
    if positive.size == 0:
        return []
    level = max(0.02, 0.3 * float(np.percentile(positive, 99)))
    strong = np.where(scores >= level, scores, 0.0)
    gap = max(1, int(round(float(gap_seconds) * fps)))
    return [frame for frame in pick_moments(strong, strong.size, gap, include_zero=False) if frame > 0]


def switch_signs(switch, frame_count, fps, x):
    """+1 / -1 per frame for a parsed switch (see parse_switch), or None when the direction never switches."""
    kind, value = switch
    if kind == SWITCH_OFF:
        return None
    frames = np.arange(frame_count)
    if kind == SWITCH_EVERY:
        return np.where((frames // int(value)) % 2 == 0, 1.0, -1.0)
    flips = np.zeros(frame_count, dtype=np.int64)
    flips[beat_frames(x, fps, value)] = 1
    return np.where(np.cumsum(flips) % 2 == 0, 1.0, -1.0)


class TargetSettings:
    """One parameter row of the tab: how the music drives that Deforum schedule."""

    def __init__(self, key, enabled=True, band=BAND_FULL, function=DEFAULT_FUNCTION, quiet=None, loud=None, smooth=0.0,
                 lo=None, hi=None, switch=""):
        target = TARGETS_BY_KEY[key]
        self.key = key
        self.enabled = bool(enabled)
        self.band = band if band in BANDS else BAND_FULL
        self.function, self.quiet, self.loud, self.smooth, self.lo, self.hi = function, quiet, loud, smooth, lo, hi
        self.switch = switch
        self.formula = None
        if not self.enabled:
            return
        label = target.label
        self.function = str(function).strip() if function is not None and str(function).strip() else DEFAULT_FUNCTION
        try:
            self.formula = compile_formula(self.function)
        except AudioKeyframeError as err:
            raise AudioKeyframeError(f"{label} function: {err}") from None
        self.quiet = parse_optional_number(quiet, f"{label} quiet")
        self.loud = parse_optional_number(loud, f"{label} loud")
        if (self.quiet is None) != (self.loud is None):
            raise AudioKeyframeError(f"{label}: fill in both Quiet and Loud, or leave both empty to use the function's own values.")
        self.smooth = parse_optional_number(smooth, f"{label} smooth") or 0.0
        if self.smooth < 0:
            raise AudioKeyframeError(f"{label}: smoothing cannot be negative.")
        self.lo = parse_optional_number(lo, f"{label} min limit")
        self.hi = parse_optional_number(hi, f"{label} max limit")
        if self.lo is not None and self.hi is not None and self.lo > self.hi:
            raise AudioKeyframeError(f"{label}: the min limit is above the max limit.")
        self.switch = parse_switch(switch, f"{label} switch") if target.signed else (SWITCH_OFF, 0)


def target_values(settings, curves, fps, frame_count, directions=None):
    """smooth -> the row's function -> stretched between Quiet and Loud (when set) -> song-part direction and the
    row's direction switching -> Min/Max."""
    target = TARGETS_BY_KEY[settings.key]
    frames = np.arange(frame_count, dtype=np.float64)
    env = {"x": smooth_release(curves[settings.band], settings.smooth), "y": frames, "t": frames, "sec": frames / fps}
    env.update({band.lower(): curves[band] for band in BANDS})
    values = settings.formula(env)
    finite = np.isfinite(values)
    if not finite.all():
        raise AudioKeyframeError(f"{target.label}: the function gives an invalid number at frame {int(np.argmin(finite))}.")
    if settings.quiet is not None:
        values = stretch(values, settings.quiet, settings.loud)
    if target.signed:
        direction = 1.0 if directions is None else directions[target.column]
        signs = switch_signs(settings.switch, frame_count, fps, curves[settings.band])
        if signs is not None:
            direction = direction * signs
        values = target.neutral + direction * (values - target.neutral)
    if settings.lo is not None or settings.hi is not None:
        values = np.clip(values, -np.inf if settings.lo is None else settings.lo,
                         np.inf if settings.hi is None else settings.hi)
    return values


class ScheduleResult:
    def __init__(self, target, values, text):
        self.target = target
        self.values = values
        self.text = text

    @property
    def keyframe_count(self):
        return self.text.count(":")


def build_schedules(analysis, settings, part_table=None, crossfade_frames=0, normalize=NORMALIZE_PEAK, threshold=0.0):
    """Schedules for every enabled row. Returns (results by key, parts, direction curves, warnings)."""
    curves = analysis.curves(normalize, threshold)
    parts = parse_parts(part_table)
    directions = direction_curves(parts, analysis.fps, analysis.frame_count, crossfade_frames)
    results = OrderedDict()
    for key, row in OrderedDict((row.key, row) for row in settings if row.enabled).items():
        target = TARGETS_BY_KEY[key]
        values = target_values(row, curves, analysis.fps, analysis.frame_count, directions)
        results[key] = ScheduleResult(target, values, schedule_text(values, target.decimals))
    return results, parts, directions, []


def mode_warnings(keys, animation_mode):
    warnings = []
    for key in keys:
        target = TARGETS_BY_KEY[key]
        if animation_mode in ("Video Input", "Interpolation") and target.group != "image":
            warnings.append(f"{target.label} has no effect in {animation_mode} mode.")
        elif target.mode == "2D" and animation_mode == "3D":
            hint = " In 3D use Translation Z to move forward/back." if key == "zoom" else ""
            warnings.append(f"{target.label} only works in 2D mode, and the animation mode is 3D.{hint}")
        elif target.mode == "3D" and animation_mode == "2D":
            hint = " In 2D use Zoom." if key == "translation_z" else ""
            warnings.append(f"{target.label} only works in 3D mode, and the animation mode is 2D.{hint}")
    return warnings


def format_fixed2(value):
    """JavaScript toFixed(2), like the website: ties round away from zero on the exact binary value."""
    return str(Decimal(float(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def raw_keyframes_text(analysis, function="x", band=BAND_FULL, normalize=NORMALIZE_PEAK, threshold=0.0):
    """The website's output for f(x, y): '0: (1.00), 1: (1.53), ...' (x = loudness 0..1, y = frame)."""
    curves = analysis.curves(normalize, threshold)
    if band not in curves:
        raise AudioKeyframeError(f"Unknown band {band!r}. Use one of: {', '.join(BANDS)}.")
    frames = np.arange(analysis.frame_count, dtype=np.float64)
    env = {"x": curves[band], "y": frames, "t": frames, "sec": frames / analysis.fps}
    env.update({name.lower(): curve for name, curve in curves.items()})
    values = compile_formula(function or "x")(env)
    finite = np.isfinite(values)
    if not finite.all():
        raise AudioKeyframeError(f"The function gives an invalid number at frame {int(np.argmin(finite))}.")
    return ", ".join(f"{i}: ({format_fixed2(v)})" for i, v in enumerate(values.tolist()))


def timeline_data(analysis, normalize=NORMALIZE_PEAK, threshold=0.0, max_points=20000):
    """What the Listen timeline draws: the Full loudness (0..999) of every frame, or the loudest of every `step` frames
    for very long songs, with the FPS, the frame count and the duration."""
    curve = analysis.curve(BAND_FULL, normalize, threshold)
    step = max(1, int(math.ceil(curve.size / float(max_points))))
    if step > 1:
        curve = np.concatenate([curve, np.zeros((-curve.size) % step)]).reshape(-1, step).max(axis=1)
    return {"fps": analysis.fps, "frames": analysis.frame_count, "duration": round(float(analysis.duration), 4),
            "step": step, "loudness": np.rint(np.clip(curve, 0.0, 1.0) * 999).astype(int).tolist()}


# --- key-prompts -----------------------------------------------------------------------------------------

SOURCE_JUMPS = "Loudness jumps"
SOURCE_LOUDEST = "Loudest moments"
SOURCE_QUIETEST = "Quietest moments"
SOURCE_STRENGTH = "Biggest image change (lowest Strength)"
SOURCE_PARTS = "Song part starts"
PROMPT_SOURCES = [SOURCE_JUMPS, SOURCE_LOUDEST, SOURCE_QUIETEST, SOURCE_STRENGTH, SOURCE_PARTS]
PROMPT_MODE_ADD = "Add to existing prompts"
PROMPT_MODE_REPLACE = "Replace all prompts"
PROMPT_MODES = [PROMPT_MODE_ADD, PROMPT_MODE_REPLACE]
DEFAULT_PLACEHOLDER = "write prompt here ({time})"


def jump_scores(x, fps):
    """How much louder each frame is than the quarter second before it."""
    x = np.asarray(x, dtype=np.float64)
    k = max(1, int(round(fps / 4)))
    cumulative = np.concatenate(([0.0], np.cumsum(x)))
    index = np.arange(x.size)
    first = np.maximum(index - k, 0)
    counts = index - first
    past = np.where(counts > 0, (cumulative[index] - cumulative[first]) / np.maximum(counts, 1), x)
    jumps = x - past
    jumps[jumps < 1e-9] = 0.0  # a steady level leaves cumsum rounding noise, not a jump
    return jumps


def moment_scores(source, analysis, band=BAND_FULL, normalize=NORMALIZE_PEAK, threshold=0.0, strength_values=None):
    """Per-frame score for a key-prompt source (higher = better place for a new prompt)."""
    x = analysis.curve(band, normalize, threshold)
    if source == SOURCE_JUMPS:
        return jump_scores(x, analysis.fps)
    if source == SOURCE_LOUDEST:
        return x
    if source == SOURCE_QUIETEST:
        return 1.0 - x + 1e-6
    if source == SOURCE_STRENGTH:
        if strength_values is None:
            raise AudioKeyframeError("Tick the Strength schedule row to place prompts at its lowest values.")
        strength = np.asarray(strength_values, dtype=np.float64)
        return strength.max() - strength
    raise AudioKeyframeError(f"Unknown key-prompt source {source!r}.")


def pick_moments(scores, count, min_gap_frames, include_zero=True):
    """Best-scoring frames at least min_gap_frames apart, sorted. Frame 0 (when included) counts toward count."""
    scores = np.nan_to_num(np.asarray(scores, dtype=np.float64), nan=0.0)
    count = max(int(count), 1)
    gap = max(int(min_gap_frames), 1)
    blocked = np.zeros(scores.size, dtype=bool)
    picked = []

    def take(frame):
        picked.append(int(frame))
        blocked[max(0, frame - gap + 1):frame + gap] = True

    if include_zero and scores.size:
        take(0)
    for frame in np.argsort(-scores, kind="stable"):
        if len(picked) >= count or scores[frame] <= 0:
            break
        if not blocked[frame]:
            take(frame)
    return sorted(picked)


def _prompt_frame(key):
    text = str(key).strip()
    return int(text) if text.isdigit() else None


def build_prompts(frames, fps, existing_text="", placeholder=DEFAULT_PLACEHOLDER, mode=PROMPT_MODE_ADD, scores=None):
    """Deforum prompts JSON with a key at every frame. Returns (json text, cues [(frame, time, score)]).

    Add keeps every existing prompt and only fills new keys; Replace keeps just the frames asked for (reusing any
    text already written at those exact frames). {time}, {frame} and {prev} (the prompt active there) are filled in.
    """
    existing = {}
    if existing_text and str(existing_text).strip():
        try:
            existing = json.loads(existing_text)
        except ValueError as err:
            if mode == PROMPT_MODE_ADD:
                raise AudioKeyframeError(
                    f"The current Prompts are not valid JSON ({err}). Fix them, or choose '{PROMPT_MODE_REPLACE}'.") from None
            existing = {}
        if not isinstance(existing, dict):
            if mode == PROMPT_MODE_ADD:
                raise AudioKeyframeError('The current Prompts must be a JSON object like {"0": "a forest"}.')
            existing = {}
    numbered = sorted(((_prompt_frame(k), v) for k, v in existing.items() if _prompt_frame(k) is not None),
                      key=lambda item: item[0])

    def previous_prompt(frame):
        text = numbered[0][1] if numbered else ""
        for key_frame, value in numbered:
            if key_frame > frame:
                break
            text = value
        return str(text)

    template = placeholder if placeholder and str(placeholder).strip() else DEFAULT_PLACEHOLDER
    result = OrderedDict(existing) if mode == PROMPT_MODE_ADD else OrderedDict()
    cues = []
    for frame in sorted(set(int(f) for f in frames)):
        key = str(frame)
        if key in existing and key not in result:
            result[key] = existing[key]
        elif key not in result:
            result[key] = (template.replace("{time}", format_time(frame / fps)).replace("{frame}", key)
                           .replace("{prev}", previous_prompt(frame)))
        score = None if scores is None or frame >= len(scores) else float(scores[frame])
        cues.append((frame, format_time(frame / fps), score))
    ordered = OrderedDict((k, result[k]) for k in sorted((k for k in result if _prompt_frame(k) is not None),
                                                         key=_prompt_frame))
    ordered.update((k, v) for k, v in result.items() if _prompt_frame(k) is None)
    return json.dumps(ordered, ensure_ascii=False, indent=4), cues


def prompt_frames(text):
    """Frame numbers that have a prompt in Deforum's Prompts JSON (none when it is not valid JSON)."""
    try:
        data = json.loads(text) if text and str(text).strip() else {}
    except ValueError:
        return []
    return sorted({_prompt_frame(key) for key in data} - {None}) if isinstance(data, dict) else []


def mark_prompt(existing_text, frame, fps, placeholder=DEFAULT_PLACEHOLDER):
    """A prompt key at `frame` (and at frame 0 when the Prompts have none there). Returns (json text, added)."""
    frame = int(frame)
    existing = prompt_frames(existing_text)
    frames = [frame] if 0 in existing else [0, frame]
    text, _ = build_prompts(frames, fps, existing_text, placeholder, PROMPT_MODE_ADD)
    return text, frame not in existing

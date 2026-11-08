"""Music that steps back under speech ("ducking").

A sound clip with `duck` (0..1, the level it drops to; 0.25 is about -12 dB) is lowered wherever a speech clip is
actually talking: not for the whole narration clip, only its spoken stretches, so the music comes back up in the
pauses. A speech clip is one with `speech: true`, or one whose file is a narration made here (voice-*.wav) unless
`speech` is false.

1. where a file talks: its loudness in 20 ms windows; loud = within 30 dB of its loud parts (and above -50 dBFS);
   gaps shorter than 0.25 s closed, blips shorter than 0.1 s dropped; cached next to the media's other pictures;
2. on the timeline: each speech clip's stretches moved to where the clip plays them, cut to the clip, joined
   across the whole edit (gaps shorter than ATTACK + RELEASE joined too, so the music does not pump between words);
3. the gain: down over ATTACK seconds before a stretch, held, up over RELEASE seconds after it, the page's
   FS.duckGain does the same.
"""

import json
import os
import subprocess

import numpy as np

from . import library, media, projects

SR = 16000
WIN = 0.02
ATTACK = 0.15
RELEASE = 0.6
VERSION = 1


def _num(v, default):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def talking(path):
    """[[start, end], ...] seconds in the file where it is loud enough to be speech."""
    out = subprocess.run([library.ffmpeg(), "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                         capture_output=True, timeout=600, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    y = np.frombuffer(out.stdout, np.float32)
    n = int(SR * WIN)
    if y.size < n:
        return []
    frames = y[: y.size // n * n].reshape(-1, n)
    db = 10 * np.log10(np.mean(frames.astype(np.float64) ** 2, axis=1) + 1e-12)
    level = max(-50.0, float(np.percentile(db, 95)) - 30.0)
    loud = db > level
    ranges, start = [], None
    for i, on in enumerate(np.append(loud, False)):
        if on and start is None:
            start = i
        elif not on and start is not None:
            ranges.append([start * WIN, i * WIN])
            start = None
    joined = []
    for a, b in ranges:
        if joined and a - joined[-1][1] < 0.25:
            joined[-1][1] = b
        else:
            joined.append([a, b])
    return [[round(a, 3), round(b, 3)] for a, b in joined if b - a >= 0.1]


def talking_cached(path):
    out = media._cache(media._key(path) + f"-talk{VERSION}.json")
    if os.path.isfile(out):
        with open(out, encoding="utf-8") as f:
            return json.load(f)
    found = talking(path)
    with open(out + ".tmp", "w", encoding="utf-8") as f:
        json.dump(found, f)
    os.replace(out + ".tmp", out)
    return found


def is_speech(c):
    if c.get("speech") is not None:
        return bool(c.get("speech"))
    return os.path.basename(c.get("media") or "").startswith("voice-")


def stretches(name, doc):
    """Where speech is heard on the timeline, joined: [[start, end], ...] seconds."""
    muted = {t["id"] for t in doc.get("tracks", []) if t.get("muted")}
    found = []
    for c in doc.get("clips", []):
        if c.get("kind") not in ("audio", "video") or not c.get("media") or c.get("muted") or c.get("track") in muted:
            continue
        if not is_speech(c) or _num(c.get("duck"), 0) > 0:
            continue
        start, dur = _num(c.get("start"), 0), _num(c.get("duration"), 0)
        speed, cin = max(0.05, _num(c.get("speed"), 1)), _num(c.get("in"), 0)
        for a, b in talking_cached(projects.media_path(name, c["media"])):
            a, b = start + (a - cin) / speed, start + (b - cin) / speed
            a, b = max(a, start), min(b, start + dur)
            if b > a:
                found.append([a, b])
    found.sort()
    joined = []
    for a, b in found:
        if joined and a - joined[-1][1] < ATTACK + RELEASE:
            joined[-1][1] = max(joined[-1][1], b)
        else:
            joined.append([a, b])
    return joined


def gain(level, ranges, t):
    """The music's gain at timeline time t (the page's FS.duckGain)."""
    cover = 0.0
    for a, b in ranges:
        if a - ATTACK < t < b + RELEASE:
            cover = max(cover, min(1.0, (t - (a - ATTACK)) / ATTACK, ((b + RELEASE) - t) / RELEASE))
    return 1 - (1 - level) * cover


def expression(level, ranges, start, dur):
    """An ffmpeg `volume` expression of the gain in the clip's own time (t = 0 at its start); None when no speech
    falls inside the clip. The joined stretches never overlap their ramps, so the sum is the same as gain()."""
    terms = []
    for a, b in ranges:
        if b + RELEASE <= start or a - ATTACK >= start + dur:
            continue
        a, b = a - start, b - start
        terms.append(f"clip((t-{a - ATTACK:.4f})/{ATTACK},0,1)*clip(({b + RELEASE:.4f}-t)/{RELEASE},0,1)")
    if not terms:
        return None
    return f"1-{1 - level:.4f}*min(1,{'+'.join(terms)})"

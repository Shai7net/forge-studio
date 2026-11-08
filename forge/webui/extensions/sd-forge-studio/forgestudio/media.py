"""What the editor needs to know about a file, and the pictures the timeline draws on its clips.

- probe: kind, length, size, frame rate, whether it has sound, its rotation (phone videos);
- strip: a row of small frames evenly over the whole file (the page stretches it along the clip, so a trimmed
  clip shows its own part);
- wave: the sound's outline as a PNG, the same way.
Everything is cached by the file's path, size and time, so a file is read once.
"""

import hashlib
import json
import os
import subprocess

from . import library, paths

STRIP_HEIGHT = 72


PROBE_VERSION = 2  # raise when probe() learns something new, so cached answers are read again


def _key(path):
    st = os.stat(path)
    return hashlib.sha1(f"{os.path.normcase(path)}|{st.st_size}|{int(st.st_mtime)}|{PROBE_VERSION}".encode("utf-8")).hexdigest()[:20]


def _cache(name):
    return os.path.join(paths.sub("cache"), name)


def probe(path):
    out = _cache(_key(path) + ".json")
    if os.path.isfile(out):
        with open(out, encoding="utf-8") as f:
            return json.load(f)
    kind = library.kind_of(path) or "video"
    info = {"kind": kind, "duration": None, "width": None, "height": None, "fps": None, "audio": False, "rotation": 0}
    exe = library.ffmpeg("ffprobe")
    if exe:
        r = subprocess.run([exe, "-v", "error", "-show_streams", "-show_format", "-of", "json", path],
                           capture_output=True, timeout=30)
        data = json.loads(r.stdout or b"{}")
        for s in data.get("streams", []):
            if s.get("codec_type") == "video" and info["width"] is None:
                info["width"], info["height"] = s.get("width"), s.get("height")
                info["codec"] = s.get("codec_name")
                # VP8/VP9 keep transparency in a side stream that only libvpx decodes (ffmpeg's own decoders drop it)
                info["alpha"] = (s.get("tags") or {}).get("alpha_mode") == "1" or (s.get("tags") or {}).get("ALPHA_MODE") == "1" \
                    or "a" in (s.get("pix_fmt") or "").replace("gray", "")[3:]
                num, _, den = (s.get("avg_frame_rate") or s.get("r_frame_rate") or "0/1").partition("/")
                try:
                    info["fps"] = round(float(num) / float(den or 1), 3) if float(den or 1) else None
                except ValueError:
                    pass
                rot = (s.get("tags") or {}).get("rotate")
                for side in s.get("side_data_list") or []:
                    if "rotation" in side:
                        rot = side["rotation"]
                info["rotation"] = int(float(rot or 0)) % 360
                if info["rotation"] in (90, 270):
                    info["width"], info["height"] = info["height"], info["width"]
            if s.get("codec_type") == "audio":
                info["audio"] = True
        try:
            info["duration"] = float((data.get("format") or {}).get("duration") or 0) or None
        except ValueError:
            pass
        if kind == "image" or (kind == "video" and info["duration"] is None and library.kind_of(path) == "image"):
            info["duration"] = None
        if path.lower().endswith(".gif") and info["duration"] is None:
            info["duration"] = 2.0
    if kind == "image" and not info["width"]:
        from PIL import Image
        with Image.open(path) as im:
            info["width"], info["height"] = im.size
    with open(out, "w", encoding="utf-8") as f:
        json.dump(info, f)
    return info


def strip(path):
    """JPEG: up to 40 frames side by side, evenly over the file, STRIP_HEIGHT high."""
    out = _cache(_key(path) + "-strip.jpg")
    if os.path.isfile(out):
        return out
    info = probe(path)
    if info["kind"] == "image":
        from PIL import Image
        with Image.open(path) as im:
            im = im.convert("RGB")
            im.thumbnail((STRIP_HEIGHT * 4, STRIP_HEIGHT))
            im.save(out, "JPEG", quality=80)
        return out
    duration = info["duration"] or 1.0
    count = max(1, min(40, int(round(duration * 2))))
    rate = count / duration
    subprocess.run([library.ffmpeg(), "-v", "error", "-y", "-i", path, "-vf",
                    f"fps={rate:.6f},scale=-2:{STRIP_HEIGHT},tile={count}x1", "-frames:v", "1", "-q:v", "4", out],
                   capture_output=True, timeout=120)
    return out if os.path.isfile(out) else None


def wave(path):
    """PNG: the sound's outline over the whole file (white on transparent)."""
    out = _cache(_key(path) + "-wave.png")
    if os.path.isfile(out):
        return out
    info = probe(path)
    width = max(200, min(4000, int((info["duration"] or 4) * 60)))
    subprocess.run([library.ffmpeg(), "-v", "error", "-y", "-i", path, "-filter_complex",
                    f"aformat=channel_layouts=mono,showwavespic=s={width}x80:colors=white:scale=sqrt",
                    "-frames:v", "1", out], capture_output=True, timeout=120)
    return out if os.path.isfile(out) else None


def grab(path, seconds, out, decoder=(), pix_fmt=None):
    """The frame the player shows at `seconds` of a video (the last frame that starts at or before it) saved to
    `out`. ffmpeg's -ss gives the first frame that starts at or after a time, so it is asked for a quarter of a frame
    before that frame's start; a time past the last frame's start (the end of a clip, where a continuation starts)
    is the last frame. That was the failure "could not take a frame of … at 11.30 s" (2026-11-02): a 15 fps
    AnimateDiff clip's last frame starts at 11.267 s, and a 30 fps timeline's end is later. Steps back a frame at a
    time when a file's timestamps are odd, and ends with the very last decodable frame. Returns `out` or None."""
    info = probe(path)
    fps = info.get("fps") or 30.0
    duration = info.get("duration")
    last = max(0, int(round(duration * fps)) - 1) if duration else None
    index = max(0, int(float(seconds) * fps + 1e-6))
    if last is not None:
        index = min(index, last)
    exe = library.ffmpeg()
    tail = ["-frames:v", "1"] + (["-pix_fmt", pix_fmt] if pix_fmt else []) + [out]
    for back in (0, 1, 2, 4):
        if os.path.isfile(out):
            os.remove(out)
        at = max(0.0, (index - back - 0.25) / fps)
        subprocess.run([exe, "-v", "error", "-y"] + list(decoder) + ["-i", path, "-ss", f"{at:.6f}"] + tail,
                       capture_output=True, timeout=120, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if os.path.isfile(out) and os.path.getsize(out) > 0:
            return out
    subprocess.run([exe, "-v", "error", "-y", "-sseof", "-1"] + list(decoder) + ["-i", path, "-update", "1"] +
                   (["-pix_fmt", pix_fmt] if pix_fmt else []) + [out],
                   capture_output=True, timeout=120, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return out if os.path.isfile(out) and os.path.getsize(out) > 0 else None

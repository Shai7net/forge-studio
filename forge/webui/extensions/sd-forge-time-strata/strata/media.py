"""Reading a chain's source: a video file, a folder of pictures, or a zip of them. Frames come out as RGBA
numpy arrays (a source without transparency has alpha 255 everywhere)."""

import os
import re
import subprocess
import zipfile

import numpy as np
from PIL import Image

PICTURES = (".png", ".webp", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff")
VIDEOS = (".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v", ".gif")
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def ffmpeg():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def imread(path, flags=None):
    """cv2.imread for any path: OpenCV's own file reading on Windows fails silently on a path that is not plain
    ASCII (a project named in Hebrew), so the bytes are read here and decoded. None when there is no picture."""
    import cv2
    try:
        data = np.fromfile(path, np.uint8)
    except OSError:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR if flags is None else flags) if data.size else None


def imwrite(path, image):
    """cv2.imwrite for any path (see imread); the kind of picture follows the name's extension."""
    import cv2
    ok, data = cv2.imencode(os.path.splitext(path)[1] or ".png", image)
    if ok:
        data.tofile(path)
    return bool(ok)


def natural_key(name):
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", name)]


def probe(path):
    """What ffmpeg says of a video: {width, height, fps, duration, frames, codec, alpha}."""
    done = subprocess.run([ffmpeg(), "-hide_banner", "-nostdin", "-i", path], capture_output=True,
                          stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)
    said = done.stderr.decode("utf8", "replace")
    video = re.search(r"Stream #\S+.*?Video: (\w+)([^\n]*)", said)
    if not video:
        raise ValueError("ffmpeg finds no video in this file")
    codec, rest = video.group(1), video.group(2)
    size = re.search(r"(\d{2,5})x(\d{2,5})", rest)
    fps = re.search(r"([\d.]+) fps", rest) or re.search(r"([\d.]+) tbr", rest)
    duration = re.search(r"Duration: (\d+):(\d+):([\d.]+)", said)
    seconds = int(duration.group(1)) * 3600 + int(duration.group(2)) * 60 + float(duration.group(3)) if duration else 0.0
    rate = float(fps.group(1)) if fps else 25.0
    alpha = bool(re.search(r"alpha_mode\s*:\s*1", said)) or any(k in rest for k in ("yuva", "rgba", "argb", "bgra", "pal8")) \
        or codec in ("qtrle", "png") or (codec == "prores" and "4444" in rest)
    return {"width": int(size.group(1)) if size else 0, "height": int(size.group(2)) if size else 0,
            "fps": rate, "duration": seconds, "frames": int(round(seconds * rate)), "codec": codec, "alpha": alpha}


def read_video(path, start=0.0, end=None, fps=None, max_side=1280, crop=None):
    """The frames of a video, one RGBA array at a time. `fps`: take that many frames a second (None: all of
    them); `max_side`: frames larger than this are made smaller first (the cut does not need more); `crop`:
    (x, y, w, h) of the video's own pixels to keep (see find_letterbox)."""
    info = probe(path)
    width, height = info["width"], info["height"]
    filters = []
    if crop:
        x, y, width, height = (int(v) for v in crop)
        filters.append(f"crop={width}:{height}:{x}:{y}")
    scale = min(1.0, float(max_side) / max(width, height)) if max_side else 1.0
    out_w, out_h = max(2, int(round(width * scale / 2)) * 2), max(2, int(round(height * scale / 2)) * 2)
    if fps:
        filters.append(f"fps={float(fps):.6f}")
    if (out_w, out_h) != (width, height):
        filters.append(f"scale={out_w}:{out_h}:flags=area")
    command = [ffmpeg(), "-hide_banner", "-nostdin", "-loglevel", "error"]
    if info["codec"] in ("vp9", "vp8") and info["alpha"]:
        command += ["-c:v", "libvpx-vp9" if info["codec"] == "vp9" else "libvpx"]       # the decoder that keeps alpha
    if start:
        command += ["-ss", f"{float(start):.3f}"]
    command += ["-i", path]
    if end:
        command += ["-t", f"{max(0.04, float(end) - float(start or 0)):.3f}"]
    if filters:
        command += ["-vf", ",".join(filters)]
    command += ["-an", "-f", "rawvideo", "-pix_fmt", "rgba", "-"]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                               creationflags=_NO_WINDOW)
    size = out_w * out_h * 4
    try:
        while True:
            data = process.stdout.read(size)
            if len(data) < size:
                break
            yield np.frombuffer(data, np.uint8).reshape(out_h, out_w, 4)
    finally:
        process.stdout.close()
        process.kill()
        process.wait()
        process.stderr.close()


def find_letterbox(path, start=0.0, end=None, dark=24, least=0.03):
    """Black bars around a video's picture (a wide film inside an upright video, or the other way round):
    (x, y, w, h) of the picture in the video's own pixels, or None when it has none. A row or a column is a bar
    when it stays dark in every one of up to 24 frames spread over the part that is used, so a dark scene alone
    is not taken for one."""
    info = probe(path)
    W, H = info["width"], info["height"]
    length = (min(float(end), info["duration"]) if end else info["duration"]) - float(start or 0)
    peak = None
    for n, frame in enumerate(read_video(path, start, end, fps=min(4.0, 24.0 / max(0.5, length)), max_side=480)):
        bright = frame[..., :3].max(axis=2)
        peak = bright if peak is None else np.maximum(peak, bright)
        if n >= 23:
            break
    if peak is None:
        return None
    rows, cols = np.nonzero(peak.max(axis=1) > dark)[0], np.nonzero(peak.max(axis=0) > dark)[0]
    if not len(rows) or not len(cols):
        return None
    h, w = peak.shape
    if max(rows[0], h - 1 - rows[-1]) < least * h and max(cols[0], w - 1 - cols[-1]) < least * w:
        return None
    sx, sy = W / w, H / h
    x0, y0 = int(cols[0] * sx) // 2 * 2, int(rows[0] * sy) // 2 * 2
    x1, y1 = min(W, int(np.ceil((cols[-1] + 1) * sx))), min(H, int(np.ceil((rows[-1] + 1) * sy)))
    return (x0, y0, max(2, (x1 - x0) // 2 * 2), max(2, (y1 - y0) // 2 * 2))


def count_video(path, start=0.0, end=None, fps=None):
    info = probe(path)
    length = (min(float(end), info["duration"]) if end else info["duration"]) - float(start or 0)
    return max(1, int(round(max(0.0, length) * float(fps or info["fps"]))))


def list_pictures(folder):
    names = [n for n in os.listdir(folder) if n.lower().endswith(PICTURES)]
    return [os.path.join(folder, n) for n in sorted(names, key=natural_key)]


def unpack_zip(path, folder):
    """The pictures of a zip, in the order of their names, put into `folder`; nothing outside it is written."""
    os.makedirs(folder, exist_ok=True)
    written = []
    with zipfile.ZipFile(path) as packed:
        for member in packed.infolist():
            name = os.path.basename(member.filename)
            if member.is_dir() or not name.lower().endswith(PICTURES) or name.startswith("."):
                continue
            target = os.path.join(folder, f"{len(written):06d}_{re.sub(r'[^A-Za-z0-9._-]', '_', name)}")
            with packed.open(member) as source, open(target, "wb") as out:
                out.write(source.read())
            written.append((name, target))
    written.sort(key=lambda pair: natural_key(pair[0]))
    return [t for _n, t in written]


def read_picture(path, max_side=1280):
    image = Image.open(path)
    image.load()
    image = image.convert("RGBA")
    if max_side and max(image.size) > max_side:
        scale = float(max_side) / max(image.size)
        image = image.resize((max(2, round(image.size[0] * scale)), max(2, round(image.size[1] * scale))), Image.LANCZOS)
    return np.asarray(image)


def read_pictures(paths, max_side=1280):
    for path in paths:
        yield read_picture(path, max_side)

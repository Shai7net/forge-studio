"""The flight as a video file. The page draws every frame (at the video's size, or larger for supersampling)
and sends its pixels here as WebGL reads them: RGBA, the bottom row first; or, from a browser that sends
slowly, as a JPEG. ffmpeg turns them the right way up, scales them and encodes, with the music under them."""

import io
import os
import re
import subprocess
import threading
import time
import uuid

import numpy as np

from . import store

KINDS = {
    "mp4-h264": ["-c:v", "libx264", "-preset", "medium", "-pix_fmt", "yuv420p", "-movflags", "+faststart"],
    "mp4-nvenc": ["-c:v", "h264_nvenc", "-preset", "p5", "-pix_fmt", "yuv420p", "-movflags", "+faststart"],
    "webm-vp9": ["-c:v", "libvpx-vp9", "-row-mt", "1", "-pix_fmt", "yuv420p"],
}
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_sessions = {}
_lock = threading.Lock()


def ffmpeg():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def _target(name, extension):
    base = re.sub(r'[\\/:*?"<>|\x00-\x1f]', " ", str(name or "flight")).strip() or "flight"
    folder = store.exports_dir()
    path = os.path.join(folder, f"{base}.{extension}")
    n = 2
    while os.path.exists(path):
        path = os.path.join(folder, f"{base} ({n}).{extension}")
        n += 1
    return path


class Session:
    def __init__(self, name, width, height, fps, kind, quality, supersample, music=None, music_start=0.0, project=None):
        if kind not in KINDS:
            raise ValueError(f"unknown kind of video: {kind}")
        self.id = uuid.uuid4().hex[:12]
        self.width, self.height, self.fps = int(width) // 2 * 2, int(height) // 2 * 2, float(fps)
        self.ss = max(1, int(supersample or 1))
        self.drawn = (self.width * self.ss, self.height * self.ss)
        self.path = _target(name, "webm" if kind.startswith("webm") else "mp4")
        self.frames = 0
        self.started = time.time()
        command = [ffmpeg(), "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
                   "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{self.drawn[0]}x{self.drawn[1]}", "-r", f"{self.fps:.6f}", "-i", "-"]
        if music:
            command += ["-ss", f"{float(music_start or 0):.3f}", "-i", music]
        filters = ["vflip"]
        if self.ss > 1:
            filters.append(f"scale={self.width}:{self.height}:flags=lanczos")
        command += ["-vf", ",".join(filters), *KINDS[kind]]
        command += ["-cq" if kind == "mp4-nvenc" else "-crf", str(int(quality))]
        if kind == "webm-vp9":
            command += ["-b:v", "0"]
        if music:
            command += ["-map", "0:v", "-map", "1:a", "-c:a", "aac" if not kind.startswith("webm") else "libopus", "-b:a", "192k", "-shortest"]
        command.append(self.path)
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=_NO_WINDOW)

    def frame(self, data, jpeg=False):
        if jpeg:
            from PIL import Image
            image = Image.open(io.BytesIO(data)).convert("RGBA")
            data = np.asarray(image)[::-1].tobytes()      # the same bottom-first rows as raw pixels
        expected = self.drawn[0] * self.drawn[1] * 4
        if len(data) != expected:
            raise ValueError(f"a frame of {len(data)} bytes, {expected} expected")
        self.process.stdin.write(data)
        self.frames += 1

    def finish(self):
        self.process.stdin.close()
        error = self.process.stderr.read().decode("utf8", "replace")
        code = self.process.wait()
        self.process.stderr.close()
        if code != 0:
            raise RuntimeError(f"ffmpeg stopped ({code}): {error.strip()[-600:]}")
        return {"path": self.path, "name": os.path.basename(self.path), "frames": self.frames,
                "seconds": round(time.time() - self.started, 1), "size": os.path.getsize(self.path)}

    def cancel(self):
        try:
            self.process.stdin.close()
        except Exception:
            pass
        self.process.kill()
        self.process.wait()
        self.process.stderr.close()
        if os.path.exists(self.path):
            os.remove(self.path)


def start(**options):
    session = Session(**options)
    with _lock:
        _sessions[session.id] = session
    return session


def get(session_id):
    session = _sessions.get(session_id)
    if session is None:
        raise KeyError("no such recording")
    return session


def finish(session_id):
    with _lock:
        session = _sessions.pop(session_id)
    return session.finish()


def cancel(session_id):
    with _lock:
        session = _sessions.pop(session_id, None)
    if session:
        session.cancel()

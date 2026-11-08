"""Small tools on one clip's file: a still of one frame (for a freeze frame), and the used part of a video played
backwards.

- still: the frame the player shows at `seconds` of the file (the last frame that starts at or before it), as a
  PNG in the project's media folder; a transparent WebM keeps its transparency (decoded with libvpx).
- reverse: `seconds` of the file from `start`, backwards, sound too. ffmpeg's reverse filter keeps every frame of
  what it reverses in memory, so it is done in pieces of PIECE seconds, each reversed, then joined last piece first.
  A transparent WebM stays a transparent WebM; anything else becomes H.264 (CRF 16) with AAC sound.
"""

import os
import subprocess
import uuid

from . import frames, library, media, projects

PIECE = 5.0


def _run(cmd, what):
    r = subprocess.run(cmd, capture_output=True, timeout=1800, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode != 0:
        lines = (r.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        raise ValueError(f"{what}: {lines[-1]}" if lines else what)


def _media_folder(name):
    folder = os.path.join(projects._folder(name), "media")
    os.makedirs(folder, exist_ok=True)
    return folder


def _decoder(info):
    if info.get("alpha") and info.get("codec") in ("vp8", "vp9"):
        return ["-c:v", "libvpx" if info["codec"] == "vp8" else "libvpx-vp9"]
    return []


def still(name, web, seconds):
    path = projects.media_path(name, web)
    info = media.probe(path)
    stem = os.path.splitext(os.path.basename(web))[0]
    out = os.path.join(_media_folder(name), f"{stem}-freeze-{uuid.uuid4().hex[:6]}.png")
    if info.get("kind") == "image":
        from PIL import Image
        with Image.open(path) as im:
            im.save(out)
    else:
        media.grab(path, seconds, out, _decoder(info), "rgba" if info.get("alpha") else "rgb24")
    if not os.path.isfile(out):
        raise ValueError(f"could not take a frame of {web} at {seconds:.2f} s")
    return {"web": projects.add_media(name, out), "path": out}


def reverse(name, web, start, seconds):
    return frames._task("reverse", _reverse, name, web, float(start), float(seconds))


def _reverse(task, name, web, start, seconds):
    path = projects.media_path(name, web)
    info = media.probe(path)
    if info.get("kind") != "video":
        raise ValueError("only a video can be played backwards")
    end = min(start + seconds, info.get("duration") or start + seconds)
    if end - start < 0.02:
        raise ValueError("nothing to play backwards")
    alpha = bool(info.get("alpha") and info.get("codec") in ("vp8", "vp9"))
    ext = "webm" if alpha else "mp4"
    if alpha:
        enc = ["-c:v", "libvpx", "-pix_fmt", "yuva420p", "-auto-alt-ref", "0", "-b:v", "0", "-crf", "10",
               "-metadata:s:v:0", "alpha_mode=1", "-c:a", "libopus"]
    else:
        enc = ["-c:v", "libx264", "-preset", "fast", "-crf", "16", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k"]
    work = os.path.join(_media_folder(name), "reverse-" + uuid.uuid4().hex[:6])
    os.makedirs(work, exist_ok=True)
    stem = os.path.splitext(os.path.basename(web))[0]
    out = os.path.join(_media_folder(name), f"{stem}-reverse-{uuid.uuid4().hex[:6]}.{ext}")
    try:
        cuts, t = [], start
        while t < end - 1e-3:
            cuts.append((t, min(PIECE, end - t)))
            t += PIECE
        pieces = []
        for i, (a, n) in enumerate(cuts):
            task.update(text=f"piece {i + 1} of {len(cuts)}", progress=i / (len(cuts) + 1))
            piece = os.path.join(work, f"{i:04d}.{ext}")
            filters = ["-vf", "reverse"] + (["-af", "areverse"] if info.get("audio") else ["-an"])
            _run([library.ffmpeg(), "-v", "error", "-y"] + _decoder(info) + ["-ss", f"{a:.6f}", "-t", f"{n:.6f}", "-i", path]
                 + filters + enc + [piece], "could not play it backwards")
            pieces.append(piece)
        task.update(text="joining", progress=len(cuts) / (len(cuts) + 1))
        listing = os.path.join(work, "pieces.txt")
        with open(listing, "w", encoding="utf-8") as f:
            for p in reversed(pieces):
                f.write("file '" + p.replace("\\", "/").replace("'", "'\\''") + "'\n")
        _run([library.ffmpeg(), "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", listing, "-c", "copy", out],
             "could not join the pieces")
    finally:
        import shutil
        shutil.rmtree(work, ignore_errors=True)
    return {"web": projects.add_media(name, out), "seconds": round(end - start, 3), "text": f"{end - start:.1f} s backwards"}

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


def merge(name, clips, settings, audio=None):
    """Flatten two adjacent visual clips into one new take; never alter the project or source files."""
    import copy
    import math
    if not isinstance(clips, list) or len(clips) != 2:
        raise ValueError('Choose the shot and its adjacent extension')
    clips = sorted(copy.deepcopy(clips), key=lambda c: float(c.get('start', 0)))
    for c in clips:
        if c.get('kind') not in ('video', 'image') or not c.get('media'):
            raise ValueError('Only visual clips can be combined')
        projects.media_path(name, c['media'])
        if not all(math.isfinite(float(c.get(k, 0))) for k in ('start', 'duration')) or float(c.get('duration', 0)) <= 0:
            raise ValueError('Invalid clip duration')
    fps = float(settings.get('fps') or 30)
    if not math.isfinite(fps) or not 1 <= fps <= 120:
        raise ValueError('Invalid frame rate')
    if clips[0].get('track') != clips[1].get('track') or abs(clips[0]['start'] + clips[0]['duration'] - clips[1]['start']) > .51 / fps:
        raise ValueError('The shot and extension must still touch on the same track')
    origin = clips[0]['start']
    end = clips[1]['start'] + clips[1]['duration']
    audio = copy.deepcopy(audio or [])
    if len(audio) > 8:
        raise ValueError('Too many linked audio clips')
    for a in audio:
        if a.get('kind') != 'audio' or not a.get('media'):
            raise ValueError('Choose linked audio clips')
        projects.media_path(name, a['media'])
        if not all(math.isfinite(float(a.get(k, 0))) for k in ('start', 'duration')) or a.get('duration', 0) <= 0:
            raise ValueError('Invalid linked audio duration')
        if a['start'] < origin - .001 or a['start'] + a['duration'] > end + .001:
            raise ValueError('Linked audio must fit inside the combined shot')
        a['start'] -= origin
        a['track'] = 'a1'
    for c in clips:
        c['start'] -= origin
        c['track'] = 'v1'
    # The incoming boundary still belongs to the preceding timeline shot, outside this flattened pair.
    for key in ('transitionIn', 'transition', 'soundIn'):
        clips[0].pop(key, None)
    doc = {'version': 2, 'settings': copy.deepcopy(settings), 'tracks': [{'id': 'v1', 'kind': 'video', 'main': True}, {'id': 'a1', 'kind': 'audio'}], 'clips': clips + audio}
    return frames._task('combine-extension', _merge, name, doc)


def _merge(task, name, doc):
    import time
    from . import render
    folder = _media_folder(name)
    job = render.start(name, doc, 'h264-soft', None, {'folder': folder, 'filename': 'combined-' + uuid.uuid4().hex[:10]})
    while True:
        current = render.jobs[job['id']]
        task.update(progress=current.get('progress', 0), text='Combining the shot and extension')
        if current['state'] == 'done':
            path = current['out']
            return {'web': projects.add_media(name, path), 'seconds': current['seconds'], 'progress': 1, 'text': 'Combined clip ready'}
        if current['state'] in ('error', 'cancelled'):
            raise RuntimeError(current.get('message') or 'Combining clips was cancelled')
        time.sleep(.1)


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

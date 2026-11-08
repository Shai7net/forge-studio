"""Clips with keyframes, drawn for the export frame by frame.

ffmpeg's scale and rotate cannot change a layer's size from one frame to the next, so a clip whose place, size, turn
or opacity moves is drawn here instead: every frame of it is laid onto a transparent frame of the whole edit with
exactly the player's geometry (c-player.js P.box: fitted inside the frame, scaled, turned about its centre, moved),
and the result, a lossless UT Video with transparency (fast to write and to read), is layered by render.py like any other clip (its colour,
fades and dissolves are still done there).
"""

import math
import os
import subprocess

import numpy as np

from . import library, media, projects, render

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _source_frames(name, c, W, H, fps, n, size, crop=(0, 0, 0, 0)):
    """The clip's own frames, n of them at the edit's frame rate, the part that `crop` leaves, at `size` (w, h), RGBA
    uint8."""
    sw, sh = size
    L, T, R, B = crop
    kind = c.get("kind")
    if kind == "text":
        from PIL import Image
        img = Image.open(render.text_png(name, c["id"])).convert("RGBA").resize((sw, sh), Image.LANCZOS)
        frame = np.asarray(img)
        typed = render.typed_steps(name, c)
        if not typed:
            while True:
                yield frame
        # the typewriter: each frame the text with as many letters as the page shows then (a count the page did not
        # draw falls back to the nearest it did)
        n, pics = typed
        cache, i = {}, 0
        while True:
            k = render.typed_count(c, i / fps, n)
            i += 1
            if k >= n or not pics:
                yield frame
                continue
            if k not in pics:
                k = min(pics, key=lambda x: abs(x - k))
            if k not in cache:
                if len(cache) > 8:
                    cache.clear()
                cache[k] = np.asarray(Image.open(pics[k]).convert("RGBA").resize((sw, sh), Image.LANCZOS))
            yield cache[k]
    path = projects.media_path(name, c["media"])
    info = media.probe(path)
    if kind == "image" or info.get("kind") == "image":
        from PIL import Image
        with Image.open(path) as im:
            im = im.convert("RGBA")
            if L or T or R or B:
                iw, ih = im.size
                im = im.crop((iw * L, ih * T, iw * (1 - R), ih * (1 - B)))
            frame = np.asarray(im.resize((sw, sh), Image.LANCZOS))
        while True:
            yield frame
    speed = max(0.05, render._num(c.get("speed"), 1.0))
    cin = max(0.0, render._num(c.get("in"), 0.0))
    dur = render._num(c.get("duration"), 0)
    decoder = []
    if info.get("alpha") and info.get("codec") in ("vp8", "vp9"):
        decoder = ["-c:v", "libvpx" if info["codec"] == "vp8" else "libvpx-vp9"]
    cut = f"format=rgba,crop=iw*{1 - L - R:.6f}:ih*{1 - T - B:.6f}:iw*{L:.6f}:ih*{T:.6f}:exact=1," if (L or T or R or B) else ""
    if render.ramped(c):
        # a speed ramp: the file read in order at a rate fine enough for its fastest part, and for each frame of
        # the edit the frame nearest the file second the ramp has reached (render.source_span)
        table = render._ramp_table(c)
        span = render.source_span(c, dur, table)
        top = max(render.speed_at(c, i / fps) for i in range(n))
        rate = fps * max(1, math.ceil(top))
        cmd = [library.ffmpeg(), "-v", "error"] + decoder + ["-ss", f"{cin:.6f}", "-t", f"{span + 0.1:.6f}", "-i", path,
               "-vf", f"setpts=PTS-STARTPTS,fps={rate:.6f},{cut}scale={sw}:{sh}:flags=lanczos,format=rgba", "-f", "rawvideo", "-"]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
        step = sw * sh * 4
        last, have = np.zeros((sh, sw, 4), np.uint8), -1
        try:
            for k in range(n):
                want = int(round(render.source_span(c, k / fps, table) * rate))
                while have < want:
                    buf = proc.stdout.read(step)
                    if len(buf) < step:
                        break
                    last, have = np.frombuffer(buf, np.uint8).reshape(sh, sw, 4), have + 1
                yield last
        finally:
            proc.stdout.close()
            proc.kill()
            proc.wait()
        while True:
            yield last
    # the same frames the layer would get in render.build: from `in`, sped up, at the edit's frame rate
    cmd = [library.ffmpeg(), "-v", "error"] + decoder + ["-ss", f"{cin:.6f}", "-t", f"{dur * speed + 0.05:.6f}", "-i", path,
           "-vf", f"setpts=(PTS-STARTPTS)/{speed:.6f},fps={fps:.6f},{cut}scale={sw}:{sh}:flags=lanczos,format=rgba",
           "-frames:v", str(n), "-f", "rawvideo", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
    step = sw * sh * 4
    last = np.zeros((sh, sw, 4), np.uint8)
    try:
        while True:
            buf = proc.stdout.read(step)
            if len(buf) < step:
                break
            last = np.frombuffer(buf, np.uint8).reshape(sh, sw, 4)
            yield last
    finally:
        proc.stdout.close()
        proc.kill()
        proc.wait()
    while True:  # a file shorter than the clip holds its last frame
        yield last


def draw(name, doc, c, out, should_stop=lambda: False):
    """Draw clip c with its keyframes into `out` (.mkv, UT Video with transparency) of the edit's whole frame."""
    import cv2
    st = doc.get("settings", {})
    W, H = int(st.get("width", 1080)), int(st.get("height", 1920))
    fps = render._num(st.get("fps"), 30.0)
    dur = render._num(c.get("duration"), 0)
    n = max(1, int(round(dur * fps)))
    if c.get("kind") == "text":
        mw, mh = W, H
    else:
        info = media.probe(projects.media_path(name, c["media"]))
        mw, mh = info.get("width") or W, info.get("height") or H
    fit0 = min(W / mw, H / mh)
    at = lambda key, i: render.value_at(c, key, i / fps)
    scales = [max(0.01, at("scale", i)) for i in range(n)]
    # the source is made once at its largest size in the clip, so each frame is only ever made smaller from it
    big = max(scales)
    crop = render.crop_of(c)
    L, T, R, B = crop
    sw, sh = max(2, int(round(mw * fit0 * big * (1 - L - R)))), max(2, int(round(mh * fit0 * big * (1 - T - B))))
    # but never bigger than twice the frame (a huge size only shows a small part; the warp enlarges from there)
    cap = min(1.0, 2 * W / sw, 2 * H / sh)
    sw, sh = max(2, int(round(sw * cap))), max(2, int(round(sh * cap)))
    enc = [library.ffmpeg(), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{W}x{H}", "-r", f"{fps:.6f}",
           "-i", "-", "-c:v", "utvideo", "-pix_fmt", "gbrap", out]
    proc = subprocess.Popen(enc, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, creationflags=NO_WINDOW)
    frames = _source_frames(name, c, W, H, fps, n, (sw, sh), crop)
    shape = render.mask_array(c, sw, sh) if render.mask_of(c) else None
    key = render.key_of(c)
    anchor = render.anchor_of(name, c) if render.animated(c) else (0.0, 0.0, 1.0, 1.0)
    try:
        for i in range(n):
            if should_stop():
                raise RuntimeError("cancelled")
            src = next(frames)
            if key is not None:  # the chroma key first, then the shape (the page: key, mask, then colour)
                src = render.chroma(key, src)
            if shape is not None:  # the shape: multiplied into the frame's own transparency
                src = cv2.merge([src[..., 0], src[..., 1], src[..., 2], cv2.multiply(np.ascontiguousarray(src[..., 3]), shape, scale=1 / 255.0)])
            fw, fh = mw * fit0 * scales[i], mh * fit0 * scales[i]   # the whole picture; the crop shows a part of it
            w, h = fw * (1 - L - R), fh * (1 - T - B)
            ang = math.radians(at("rotation", i))
            ca, sa = math.cos(ang), math.sin(ang)
            cx, cy = render._crop_centre(c, W / 2 + at("x", i) * W, H / 2 + at("y", i) * H, fw, fh, at("rotation", i))
            anim = render.anim_at(c, i / fps, anchor)
            if anim["scale"] != 1 or anim["dx"] or anim["dy"]:
                # grown or shrunk about the anchor (the page: the centre moves towards it by 1 - scale), and moved
                s = anim["scale"]
                px, py = (anim["about"][0] - 0.5) * w, (anim["about"][1] - 0.5) * h
                cx += anim["dx"] * W + (1 - s) * (ca * px - sa * py)
                cy += anim["dy"] * H + (1 - s) * (sa * px + ca * py)
                w, h = w * s, h * s
            r0, r1 = anim["reveal"]
            if r0 > 0 or r1 < 1:
                # a wipe: only the columns of the box between r0 and r1 show
                a = np.array(src[..., 3])
                a[:, :max(0, int(round(r0 * sw)))] = 0
                a[:, max(0, int(round(r1 * sw))):] = 0
                src = cv2.merge([src[..., 0], src[..., 1], src[..., 2], a])
            k = w / sw   # = h / sh
            # the player: translate(cx, cy), rotate(ang), drawImage(-w/2, -h/2, w, h); pixel centres sit at +0.5
            ox, oy = k * 0.5 - w / 2, k * 0.5 - h / 2
            M = np.array([[ca * k, -sa * k, cx + ca * ox - sa * oy - 0.5],
                          [sa * k, ca * k, cy + sa * ox + ca * oy - 0.5]], np.float32)
            warp = lambda img, border: cv2.warpAffine(img, M, (W, H), flags=cv2.INTER_LINEAR, borderMode=border, borderValue=0)
            alpha = warp(np.ascontiguousarray(src[..., 3]), cv2.BORDER_CONSTANT)
            if src[..., 3].min() == 255:
                # opaque: the colour spreads past the edge, so the edge's half-covered pixels are not darkened
                rgb = warp(np.ascontiguousarray(src[..., :3]), cv2.BORDER_REPLICATE)
            else:
                # transparent parts: premultiplied while it is warped, so the edges do not take the hidden colours
                a3 = cv2.merge([src[..., 3]] * 3)
                pre = warp(cv2.multiply(np.ascontiguousarray(src[..., :3]), a3, scale=1 / 255.0), cv2.BORDER_CONSTANT)
                rgb = cv2.divide(pre, cv2.merge([alpha] * 3), scale=255.0)
            op = min(1.0, max(0.0, at("opacity", i))) * anim["alpha"]
            if op < 0.999:
                alpha = cv2.multiply(alpha, np.full_like(alpha, 255), scale=op / 255.0)
            proc.stdin.write(cv2.merge([rgb, alpha]).tobytes())
        proc.stdin.close()
        err = proc.stderr.read().decode(errors="replace")
        proc.stderr.close()
        if proc.wait() != 0 or not os.path.isfile(out):
            raise RuntimeError("drawing a moving clip failed: " + err[-400:])
    finally:
        frames.close()
        if proc.poll() is None:
            proc.kill()
    return out


def draw_all(name, doc, work, job=None):
    """Every visible clip with keyframes, drawn; returns clip id -> file for render.build."""
    tracks = doc.get("tracks", [])
    hidden = {t["id"] for t in tracks if t.get("hidden")}
    todo = [c for c in doc.get("clips", []) if c.get("kind") not in ("audio", "gen") and c.get("track") not in hidden
            and render._num(c.get("duration"), 0) > 0 and render.moves(c)]
    out = {}
    if not todo:
        return out
    os.makedirs(work, exist_ok=True)
    for i, c in enumerate(todo):
        if job is not None:
            job["text"] = f"drawing the moving clips ({i + 1} of {len(todo)})"
        out[c["id"]] = draw(name, doc, c, os.path.join(work, f"{c['id']}.mkv"),
                            lambda: job is not None and job.get("state") == "cancelled")
    return out

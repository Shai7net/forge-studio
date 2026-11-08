"""Exports: the edit (project.json) rendered by ffmpeg, the picture with the graphics card's encoder.

The page and this file place things the same way, so what the player shows is what comes out:
- a picture or video is fitted inside the frame (whole, centred), then scaled by `scale`, turned by `rotation`
  degrees and moved by `x`, `y` (fractions of the frame's width and height, from the centre);
- `opacity`, `fadeIn` / `fadeOut` (seconds; a fade is to transparent, so on the lowest track it is to the
  background colour), `speed` (the clip plays `duration * speed` seconds of its file from `in`);
- a text clip is a picture of the whole frame, drawn by the page (the browser lays Hebrew out right to left)
  and sent before the export;
- visual tracks are stacked in the order of `tracks` (the first is the lowest); sound comes from audio clips
  and from videos that have sound, each with its `volume` and fades, and lowered under speech when it has
  `duck` (duck.py);
- a clip with keyframes (`keys`: x, y, scale, rotation, opacity moving over time, see `value_at`) is drawn frame by
  frame here, with the player's own geometry, into a transparent video of the whole frame that is then layered
  like any other (moving.py).
"""

import json
import math
import os
import re
import subprocess
import threading
import time
import uuid

from . import duck, library, media, paths, projects

PRESETS = {
    "h264": {"label": "H.264 MP4 (NVENC)", "ext": "mp4",
             "video": ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "19", "-b:v", "0", "-pix_fmt", "yuv420p"]},
    "hevc": {"label": "H.265 MP4 (NVENC)", "ext": "mp4",
             "video": ["-c:v", "hevc_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "21", "-b:v", "0", "-pix_fmt", "yuv420p", "-tag:v", "hvc1"]},
    "h264-soft": {"label": "H.264 MP4 (processor, smaller file)", "ext": "mp4",
                  "video": ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p"]},
    "prores": {"label": "ProRes 422 HQ (for more editing)", "ext": "mov",
               "video": ["-c:v", "prores_ks", "-profile:v", "3", "-pix_fmt", "yuv422p10le"], "audio": ["-c:a", "pcm_s16le"]},
    "half": {"label": "Quick check: half size", "ext": "mp4", "scale": 0.5,
             "video": ["-c:v", "h264_nvenc", "-preset", "p3", "-cq", "26", "-pix_fmt", "yuv420p"]},
}
jobs = {}
_lock = threading.Lock()


def presets():
    return [{"id": k, "label": v["label"]} for k, v in PRESETS.items()]


def _num(v, default):
    try:
        v = float(v)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def _even(v):
    return max(2, int(round(v / 2.0)) * 2)


def _atempo(speed):
    """atempo takes 0.5..2 at a time."""
    parts, s = [], speed
    while s > 2.0:
        parts.append("atempo=2.0")
        s /= 2.0
    while s < 0.5:
        parts.append("atempo=0.5")
        s /= 0.5
    parts.append(f"atempo={s:.6f}")
    return ",".join(parts)


def text_png(name, clip_id):
    return os.path.join(projects._folder(name), "render", f"text-{clip_id}.png")


def store_text(name, clip_id, data):
    path = text_png(name, clip_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "wb") as f:
        f.write(data)
    os.replace(path + ".tmp", path)
    return path


def typed_folder(name, clip_id):
    return os.path.join(projects._folder(name), "render", f"typed-{clip_id}")


def store_typed(name, clip_id, step, steps, data, reset=False):
    """One step of a typewriter text (the text with `step` of its `steps` letters), as the page drew it."""
    import shutil
    folder = typed_folder(name, clip_id)
    if reset and os.path.isdir(folder):
        shutil.rmtree(folder, ignore_errors=True)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{max(0, step)}.png")
    with open(path + ".tmp", "wb") as f:
        f.write(data)
    os.replace(path + ".tmp", path)
    with open(os.path.join(folder, "steps.json"), "w", encoding="utf-8") as f:
        json.dump({"steps": max(0, steps)}, f)
    return path


def typed_steps(name, c):
    """(letters in all, {count: picture}) of a typewriter text clip, or None when it does not type."""
    if c.get("kind") != "text" or "type" not in (c.get("animIn"), c.get("animOut")):
        return None
    folder = typed_folder(name, c["id"])
    try:
        with open(os.path.join(folder, "steps.json"), encoding="utf-8") as f:
            n = int(json.load(f)["steps"])
    except (OSError, ValueError, KeyError):
        return None
    pics = {int(f[:-4]): os.path.join(folder, f) for f in os.listdir(folder) if f[:-4].isdigit() and f.endswith(".png")}
    return n, pics


def typed_count(c, lt, n):
    """How many of the n letters show `lt` seconds into the clip, as the page's FS.typedCount (the same arithmetic,
    so a frame shows the same letters in both)."""
    a, b = anim_spans(c)
    dur = _num(c.get("duration"), 0)
    k = n
    if c.get("animIn") == "type" and a > 0 and lt < a:
        k = min(k, math.floor(n * max(0.0, lt) / a))
    if c.get("animOut") == "type" and b > 0 and lt > dur - b:
        k = min(k, math.floor(n * max(0.0, dur - lt) / b))
    return max(0, k)


def build(name, doc, preset="h264", loudness=None, out=None, audio_only=False, video_only=False, moving=None):
    """The ffmpeg inputs and filter graph for the edit's picture ([vout]) and/or sound ([aout]).
    `moving`: clip id -> the clip already drawn with its keyframes (moving.draw); a keyed clip without one is
    placed where it stands at its start (only for checking the edit). Returns (args, graph, total seconds, fps)."""
    st = doc.get("settings", {})
    W, H = int(st.get("width", 1080)), int(st.get("height", 1920))
    fps = _num(st.get("fps"), 30.0)
    background = st.get("background") or "#000000"
    tracks = [t for t in doc.get("tracks", [])]
    visual_order = [t["id"] for t in tracks if t.get("kind") != "audio"]
    hidden = {t["id"] for t in tracks if t.get("hidden")}
    muted_tracks = {t["id"] for t in tracks if t.get("muted")}
    clips = [c for c in doc.get("clips", []) if _num(c.get("duration"), 0) > 0]
    pending = [c for c in clips if c.get("kind") == "gen"]
    if pending:
        raise ValueError("a clip is still being made; wait for it, or remove it, before exporting")
    total = max([_num(c.get("start"), 0) + _num(c.get("duration"), 0) for c in clips] or [0])
    if total <= 0:
        raise ValueError("the timeline is empty")

    args, graph, inputs = [], [], 0
    scale = PRESETS.get(preset, {}).get("scale", 1.0)
    OW, OH = _even(W * scale), _even(H * scale)
    if not audio_only:
        graph.append(f"color=c={background}:s={W}x{H}:r={fps:.6f}:d={total:.6f},format=rgba[base0]")
    current, step = "base0", 0
    audio_labels, sounds = [], []

    def visual_key(c):
        return (visual_order.index(c["track"]) if c["track"] in visual_order else 99, _num(c.get("start"), 0))

    for c in sorted((c for c in clips if c.get("track") not in hidden and c.get("kind") != "audio"), key=visual_key):
        kind = c.get("kind")
        start, dur = _num(c.get("start"), 0), _num(c.get("duration"), 0)
        speed = max(0.05, _num(c.get("speed"), 1.0))
        cin = max(0.0, _num(c.get("in"), 0.0))
        path, info = None, None
        if audio_only and kind != "video":
            continue
        if kind == "text":
            path = text_png(name, c["id"])
            if not os.path.isfile(path):
                raise ValueError(f"the text of clip {c['id']} was not drawn for the export")
            mw, mh = W, H
        else:
            path = projects.media_path(name, c["media"])
            info = media.probe(path)
            mw, mh = info.get("width") or W, info.get("height") or H
        has_audio = bool(info and info.get("audio") and kind == "video")
        idx = inputs
        if audio_only:
            if has_audio and not c.get("muted") and c.get("track") not in muted_tracks:
                sounds.append(c)
            continue
        drawn = (moving or {}).get(c["id"])
        if drawn:
            args += ["-i", drawn]
            src = f"[{idx}:v]setpts=PTS-STARTPTS"
        elif kind in ("text", "image") or (info and info["kind"] == "image"):
            args += ["-loop", "1", "-framerate", f"{fps:.6f}", "-t", f"{dur:.6f}", "-i", path]
            src = f"[{idx}:v]setpts=PTS-STARTPTS"
        else:
            decoder = []
            if info and info.get("alpha") and info.get("codec") in ("vp8", "vp9"):
                decoder = ["-c:v", "libvpx" if info["codec"] == "vp8" else "libvpx-vp9"]  # keeps the transparency
            args += decoder + ["-ss", f"{cin:.6f}", "-t", f"{dur * speed + 0.05:.6f}", "-i", path]
            src = f"[{idx}:v]setpts=(PTS-STARTPTS)/{speed:.6f}"
        inputs += 1
        if not audio_only:
            if drawn:  # placed, sized, turned and see-through already, frame by frame
                chain = [src, f"fps={fps:.6f}", "format=rgba"] + _colour(c)
            else:
                fit = min(W / mw, H / mh) * max(0.01, value_at(c, "scale", 0))
                L, T, R, B = crop_of(c)
                if L or T or R or B:
                    # the part that shows, at the size it has in the whole picture
                    w, h = _even(mw * fit * (1 - L - R)), _even(mh * fit * (1 - T - B))
                    chain = [src, f"fps={fps:.6f}", "format=rgba",
                             f"crop=iw*{1 - L - R:.6f}:ih*{1 - T - B:.6f}:iw*{L:.6f}:ih*{T:.6f}:exact=1",
                             f"scale={w}:{h}:flags=lanczos"]
                else:
                    w, h = _even(mw * fit), _even(mh * fit)
                    chain = [src, f"fps={fps:.6f}", f"scale={w}:{h}:flags=lanczos", "format=rgba"]
                chain += _colour(c)
                if mask_of(c):
                    # the shape: the mask picture's grey multiplied into the clip's own transparency
                    args += ["-loop", "1", "-framerate", f"{fps:.6f}", "-t", f"{dur * speed + 0.1:.6f}", "-i", mask_png(c, w, h)]
                    mi = inputs
                    inputs += 1
                    graph.append(",".join(chain) + f"[body{step}]")
                    graph.append(f"[{mi}:v]format=gray,scale={w}:{h}[shape{step}]")
                    graph.append(f"[body{step}]split[keep{step}][cut{step}]")
                    graph.append(f"[cut{step}]alphaextract[alpha{step}]")
                    graph.append(f"[alpha{step}][shape{step}]blend=all_mode=multiply:shortest=1[cutalpha{step}]")
                    graph.append(f"[keep{step}][cutalpha{step}]alphamerge[masked{step}]")
                    chain = [f"[masked{step}]null"]
                rot = value_at(c, "rotation", 0)
                if abs(rot) > 0.01:
                    a = math.radians(rot)
                    chain.append(f"rotate={a:.6f}:c=none:ow=rotw({a:.6f}):oh=roth({a:.6f})")
                op = min(1.0, max(0.0, value_at(c, "opacity", 0)))
                if op < 0.999:
                    chain.append(f"colorchannelmixer=aa={op:.4f}")
            trans, tkind, side = _transition(c)
            fi, fo = _num(c.get("fadeIn"), 0), _num(c.get("fadeOut"), 0)
            if tkind == "dissolve":
                fi = max(fi, trans)
            # a transition into the next clip on this track: this one stays on its last frame under it meanwhile
            after = [r for r in clips if r.get("track") == c.get("track") and _num(r.get("transitionIn"), 0) > 0
                     and abs(_num(r.get("start"), 0) - (start + dur)) < 1e-3]
            hold = max([_num(r.get("transitionIn"), 0) for r in after] or [0])
            if fi > 0:
                chain.append(f"fade=t=in:st=0:d={fi:.4f}:alpha=1")
            if tkind in ("black", "white"):
                # hidden until the middle of the dip, where the cut is
                chain.append(f"fade=t=in:st={max(0.0, trans / 2 - 1 / fps):.4f}:d={1 / fps:.4f}:alpha=1")
            if fo > 0:
                chain.append(f"fade=t=out:st={max(0.0, dur - fo):.4f}:d={fo:.4f}:alpha=1")
            # every layer runs from 0, transparent until its start: all the files then advance with the edit.
            # (A layer whose frames only begin at its start made ffmpeg decode it ahead and hold it, which crawled at
            # 1.5 frames a second now and then; giving the files their start with -itsoffset instead hung it.)
            # the last frame held a little past the end before the trim: fps= drops a clip's last frame when its length
            # is not a whole number of frames, and that frame then showed the black background between two clips
            chain += _held_to(dur, fps)
            if hold > 0:
                chain += [f"fps={fps:.6f}", f"tpad=stop_duration={hold:.6f}:stop_mode=clone"]
            if start > 0:
                # tpad needs to know the frame rate, which setpts leaves unknown (it then adds nothing)
                chain += [f"fps={fps:.6f}", f"tpad=start_duration={start:.6f}:start_mode=add:color=0x00000000"]
            label = f"v{step}"
            graph.append(",".join(chain) + f"[{label}]")
            fill = fill_of(c, H)
            if fill:
                # the blurred copy behind it (the page's fill): its own reading of the file, covering the frame and the
                # margin, blurred, cropped to the frame, dimmed, with the clip's fades and times; laid under the clip
                sigma, margin, dim = fill
                fw, fh = W + 2 * margin, H + 2 * margin
                if kind == "image" or (info and info["kind"] == "image"):
                    args += ["-loop", "1", "-framerate", f"{fps:.6f}", "-t", f"{dur:.6f}", "-i", path]
                    fsrc = f"[{inputs}:v]setpts=PTS-STARTPTS"
                else:
                    args += ["-ss", f"{cin:.6f}", "-t", f"{dur * speed + 0.05:.6f}", "-i", path]
                    fsrc = f"[{inputs}:v]setpts=(PTS-STARTPTS)/{speed:.6f}"
                inputs += 1
                fchain = [fsrc, f"fps={fps:.6f}", f"scale={fw}:{fh}:force_original_aspect_ratio=increase:flags=bicubic",
                          f"crop={fw}:{fh}", f"gblur=sigma={sigma:.3f}:steps=3", f"crop={W}:{H}",
                          f"colorchannelmixer=rr={dim}:gg={dim}:bb={dim}", "format=rgba"]
                op = min(1.0, max(0.0, value_at(c, "opacity", 0)))
                if op < 0.999:
                    fchain.append(f"colorchannelmixer=aa={op:.4f}")
                fchain += [x for x in chain if x.startswith("fade=")]
                fchain += _held_to(dur, fps)
                if hold > 0:
                    fchain += [f"fps={fps:.6f}", f"tpad=stop_duration={hold:.6f}:stop_mode=clone"]
                if start > 0:
                    fchain += [f"fps={fps:.6f}", f"tpad=start_duration={start:.6f}:start_mode=add:color=0x00000000"]
                graph.append(",".join(fchain) + f"[fill{step}]")
                graph.append(f"[{current}][fill{step}]overlay=x=0:y=0:eof_action=pass:"
                             f"enable='between(t,{start:.6f},{start + dur + hold:.6f})'[filled{step}]")
                current = f"filled{step}"
            if drawn:
                cx, cy = W / 2, H / 2
            else:
                cx, cy = W / 2 + value_at(c, "x", 0) * W, H / 2 + value_at(c, "y", 0) * H
                if kind in ("video", "image"):
                    cx, cy = _crop_centre(c, cx, cy, mw * fit, mh * fit, value_at(c, "rotation", 0))
            nxt = f"base{step + 1}"
            # sliding in, or pushed out by the next clip: the place moves with the time (overlay works it out a frame
            # at a time), eased as the page's FS.transitionLook
            mx, my = [], []
            if tkind in ("slide", "push") and side in SIDES:
                k = f"(1-{_smooth(start, trans)})"
                mx.append(f"{SIDES[side][0] * W}*{k}")
                my.append(f"{SIDES[side][1] * H}*{k}")
            for r in after:
                r_trans, r_kind, r_side = _transition(r)
                if r_kind == "push" and r_side in SIDES:
                    k = _smooth(_num(r.get("start"), 0), r_trans)
                    mx.append(f"{-SIDES[r_side][0] * W}*{k}")
                    my.append(f"{-SIDES[r_side][1] * H}*{k}")
            px = f"'{cx:.2f}-w/2+" + "+".join(mx) + "'" if mx else f"{cx:.2f}-w/2"
            py = f"'{cy:.2f}-h/2+" + "+".join(my) + "'" if my else f"{cy:.2f}-h/2"
            graph.append(f"[{current}][{label}]overlay=x={px}:y={py}:eof_action=pass:"
                         f"enable='between(t,{start:.6f},{start + dur + hold:.6f})'[{nxt}]")
            current, step = nxt, step + 1
            if tkind in ("black", "white"):
                # the dip: the colour rises over everything drawn so far, then falls away over the next clip
                half = trans / 2
                dip = [f"color=c={tkind}:s={W}x{H}:r={fps:.6f}:d={trans:.6f}", "format=rgba",
                       f"fade=t=in:st=0:d={half:.4f}:alpha=1", f"fade=t=out:st={half:.4f}:d={half:.4f}:alpha=1"]
                if start > 0:
                    dip.append(f"tpad=start_duration={start:.6f}:start_mode=add:color=0x00000000")
                graph.append(",".join(dip) + f"[dip{step}]")
                nxt = f"base{step + 1}"
                graph.append(f"[{current}][dip{step}]overlay=eof_action=pass:"
                             f"enable='between(t,{start:.6f},{start + trans:.6f})'[{nxt}]")
                current, step = nxt, step + 1
        if has_audio and not c.get("muted") and c.get("track") not in muted_tracks:
            sounds.append(c)

    sounds += [c for c in clips if c.get("kind") == "audio" and not c.get("muted") and c.get("track") not in muted_tracks]
    if video_only:
        sounds = []
    talk = duck.stretches(name, doc) if any(_num(c.get("duck"), 0) > 0 for c in sounds) else []
    soft = soft_cuts(name, clips, sounds)
    for c in sounds:
        start, dur = _num(c.get("start"), 0), _num(c.get("duration"), 0)
        speed = max(0.05, _num(c.get("speed"), 1.0))
        edges = soft.get(c["id"], {})
        if ramped(c):
            args += ["-i", ramp_audio(name, c)]
            speed = 1.0
        else:
            path = projects.media_path(name, c["media"])
            span = (dur + edges.get("tail", 0)) * speed + 0.05
            args += ["-ss", f"{max(0.0, _num(c.get('in'), 0)):.6f}", "-t", f"{span:.6f}", "-i", path]
        audio_labels.append(_audio_chain(graph, inputs, c, start, dur, speed, len(audio_labels), talk, edges))
        inputs += 1

    if video_only:
        pass
    elif audio_labels:
        mix = "".join(f"[{l}]" for l in audio_labels)
        # padded to the film's length by apad's own whole_dur: an endless apad cut by atrim hung ffmpeg 7.1 now and then
        # at full CPU (7 to 12 of 20 runs of one small mix, 2026-11-08); bounded, 0 of 20
        tail = (f"amix=inputs={len(audio_labels)}:normalize=0:dropout_transition=0,apad=whole_dur={total:.6f},"
                f"atrim=0:{total:.6f}")
        if loudness is not None:
            tail += f",loudnorm=I={float(loudness):.1f}:TP=-1.5:LRA=11"
        graph.append(f"{mix}{tail},aresample=48000[aout]")
    else:
        graph.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{total:.6f}[aout]")
    if not audio_only:
        final = f"[{current}]format=yuv420p" + (f",scale={OW}:{OH}:flags=lanczos" if (OW, OH) != (W, H) else "") + "[vout]"
        graph.append(final)
    return args, ";\n".join(graph), total, fps


SIDES = {"right": (1, 0), "left": (-1, 0), "top": (0, -1), "bottom": (0, 1)}


def _transition(c):
    """(seconds, kind, side) of the transition into clip c, as the page's FS.transitionLook: kind is dissolve, black,
    white, slide or push (with the side it comes in from); (0, None, None) without one."""
    trans = _num(c.get("transitionIn"), 0)
    if trans <= 0:
        return 0.0, None, None
    kind, _, side = (c.get("transition") or "dissolve").partition(":")
    return trans, kind, side or None


def _smooth(start, d):
    """The eased 0..1 of a transition at overlay time t, as an ffmpeg expression (smoothstep)."""
    u = f"clip((t-{start:.6f})/{d:.6f},0,1)"
    return f"({u}*{u}*(3-2*{u}))"


KEYED = {"x": 0.0, "y": 0.0, "scale": 1.0, "rotation": 0.0, "opacity": 1.0}


def keys_of(c, key):
    ks = (c.get("keys") or {}).get(key) or []
    return sorted(([_num(t, 0), _num(v, KEYED[key])] for t, v in ks), key=lambda k: k[0])


def keyed(c):
    return any(keys_of(c, k) for k in KEYED)


def moves(c):
    """Drawn frame by frame by moving.py: keyframes, an animation in or out, a speed ramp, or a chroma key."""
    return keyed(c) or animated(c) or (c.get("kind") == "video" and ramped(c)) or key_of(c) is not None


def key_of(c):
    """The chroma key of a picture or video, as the page's FS.keyOf: dict of rgb, dom, similar, soft, spill, or None."""
    import re
    if c.get("kind") not in ("video", "image") or not re.fullmatch(r"#[0-9a-fA-F]{6}", str(c.get("key") or "")):
        return None
    rgb = [int(c["key"][i:i + 2], 16) for i in (1, 3, 5)]
    pick = lambda key, default, lo, hi: max(lo, min(hi, default if c.get(key) is None else _num(c.get(key), default)))
    return {"rgb": rgb, "dom": rgb.index(max(rgb)), "similar": pick("keySimilar", 0.3, 0.0, 1.0),
            "soft": pick("keySoft", 0.1, 0.001, 0.5), "spill": pick("keySpill", 0.6, 0.0, 1.0)}


def chroma(k, rgba):
    """The key applied to an RGBA uint8 frame (a new array), as the page's FS.applyKey."""
    import numpy as np
    px = rgba.astype(np.float32)
    d = px[..., :3] - np.asarray(k["rgb"], np.float32)
    diff = np.sqrt((d * d).sum(axis=2) / (3 * 255.0 * 255.0))
    a = np.clip((diff - k["similar"]) / k["soft"], 0, 1)
    dom = k["dom"]
    others = np.maximum(px[..., (dom + 1) % 3], px[..., (dom + 2) % 3])
    excess = np.maximum(px[..., dom] - others, 0)
    px[..., dom] = np.round(px[..., dom] - k["spill"] * excess)
    px[..., 3] = np.round(px[..., 3] * a)
    return np.clip(px, 0, 255).astype(np.uint8)


# ---- speed ramps (keys.speed): the file seconds played are the integral of the speed, as the page's FS.sourceSpan ----

RAMP_STEPS = 240


def ramped(c):
    return bool((c.get("keys") or {}).get("speed"))


def speed_at(c, lt):
    ks = sorted(([_num(t, 0), _num(v, 1.0)] for t, v in (c.get("keys") or {}).get("speed") or []), key=lambda k: k[0])
    if not ks:
        return max(0.05, _num(c.get("speed"), 1.0))
    if lt <= ks[0][0]:
        return ks[0][1]
    for i in range(1, len(ks)):
        if lt < ks[i][0]:
            (a, va), (b, vb) = ks[i - 1], ks[i]
            u = (lt - a) / max(1e-6, b - a)
            if c.get("keyEase") != "linear":
                u = u * u * (3 - 2 * u)
            return va + (vb - va) * u
    return ks[-1][1]


def _ramp_table(c):
    dur = _num(c.get("duration"), 0)
    n = max(1, math.ceil(dur * RAMP_STEPS))
    h = dur / n
    sums = [0.0] * (n + 1)
    prev = max(0.01, speed_at(c, 0))
    for i in range(1, n + 1):
        v = max(0.01, speed_at(c, i * h))
        sums[i] = sums[i - 1] + (prev + v) / 2 * h
        prev = v
    return n, h, sums


def source_span(c, lt, table=None):
    """File seconds played from the clip's start to `lt` seconds into it."""
    dur = _num(c.get("duration"), 0)
    lt = max(0.0, min(dur, lt))
    if not ramped(c):
        return lt * max(0.05, _num(c.get("speed"), 1.0))
    n, h, sums = table or _ramp_table(c)
    i = min(n - 1, int(lt / h)) if h > 0 else 0
    f = lt / h - i if h > 0 else 0.0
    return sums[i] + (sums[i + 1] - sums[i]) * f


def ramp_audio(name, c):
    """The clip's sound played along its ramp (as a tape would: faster is higher), as a WAV the length of the clip,
    cached by the file, `in` and the ramp."""
    import hashlib
    import numpy as np
    path = projects.media_path(name, c["media"])
    dur = _num(c.get("duration"), 0)
    cin = max(0.0, _num(c.get("in"), 0.0))
    table = _ramp_table(c)
    span = source_span(c, dur, table)
    key = hashlib.sha1(json.dumps([media._key(path), cin, dur, (c.get("keys") or {}).get("speed"), c.get("keyEase"),
                                   c.get("speed")]).encode()).hexdigest()[:16]
    out = media._cache(f"ramp-{key}.wav")
    if os.path.isfile(out):
        return out
    sr = 48000
    raw = subprocess.run([library.ffmpeg(), "-v", "error", "-ss", f"{cin:.6f}", "-t", f"{span + 0.1:.6f}", "-i", path, "-vn",
                          "-ac", "2", "-ar", str(sr), "-f", "f32le", "-"], capture_output=True,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    src = np.frombuffer(raw, np.float32).reshape(-1, 2) if raw else np.zeros((1, 2), np.float32)
    n_out = max(1, int(round(dur * sr)))
    # the file second each output sample plays, from the table (read off linearly, as the page does)
    n, h, sums = table
    t = np.arange(n_out) / sr
    idx = np.minimum(n - 1, (t / h).astype(np.int64)) if h > 0 else np.zeros(n_out, np.int64)
    sums = np.asarray(sums)
    pos = (sums[idx] + (sums[idx + 1] - sums[idx]) * (t / h - idx)) * sr if h > 0 else np.zeros(n_out)
    pos = np.clip(pos, 0, len(src) - 1)
    lo = np.floor(pos).astype(np.int64)
    hi = np.minimum(lo + 1, len(src) - 1)
    f = (pos - lo)[:, None]
    y = src[lo] * (1 - f) + src[hi] * f
    import wave
    with wave.open(out + ".tmp", "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(y, -1, 1) * 32767).astype("<i2").tobytes())
    os.replace(out + ".tmp", out)
    return out


ANIMS = ("fade", "rise", "pop", "wipe-r", "wipe-l", "type")


def animated(c):
    return c.get("animIn") in ANIMS or c.get("animOut") in ANIMS


def anim_spans(c):
    a = max(0.05, _num(c.get("animInSec"), 0.5)) if c.get("animIn") in ANIMS else 0.0
    b = max(0.05, _num(c.get("animOutSec"), 0.5)) if c.get("animOut") in ANIMS else 0.0
    dur = _num(c.get("duration"), 0)
    if a + b > dur > 0:
        k = dur / (a + b)
        a, b = a * k, b * k
    return a, b


def _back_out(u):
    c1 = 1.70158
    return 1 + (c1 + 1) * (u - 1) ** 3 + c1 * (u - 1) ** 2


def anim_at(c, lt, anchor=(0.0, 0.0, 1.0, 1.0)):
    """The clip's in and out animations at `lt` seconds into it, as the page's FS.animAt: dict of alpha, dx, dy (frame
    parts), scale, about (x, y) and reveal (x0, x1) in parts of its box."""
    out = {"alpha": 1.0, "dx": 0.0, "dy": 0.0, "scale": 1.0, "about": (0.5, 0.5), "reveal": [0.0, 1.0]}
    if not animated(c):
        return out
    x0, y0, x1, y1 = anchor
    out["about"] = ((x0 + x1) / 2, (y0 + y1) / 2)
    a, b = anim_spans(c)
    dur = _num(c.get("duration"), 0)
    phases = []
    if a > 0 and lt < a:
        phases.append((c.get("animIn"), max(0.0, lt / a)))
    if b > 0 and lt > dur - b:
        phases.append((c.get("animOut"), max(0.0, (dur - lt) / b)))
    for kind, u in phases:
        e = u * u * (3 - 2 * u)
        if kind == "fade":
            out["alpha"] *= u
        elif kind == "rise":
            out["dy"] += (1 - e) * 0.06
            out["alpha"] *= e
        elif kind == "pop":
            out["scale"] *= 0.6 + 0.4 * _back_out(u)
            out["alpha"] *= min(1.0, u * 4)
        elif kind == "wipe-r":
            out["reveal"][0] = max(out["reveal"][0], x1 - (x1 - x0) * e)
        elif kind == "wipe-l":
            out["reveal"][1] = min(out["reveal"][1], x0 + (x1 - x0) * e)
    return out


def anchor_of(name, c):
    """The part of the clip's box with something in it: a text's letters (alpha > 8 in its picture), else all of it,
    as the page's P.anchor."""
    if c.get("kind") != "text":
        return 0.0, 0.0, 1.0, 1.0
    from PIL import Image
    with Image.open(text_png(name, c["id"])) as im:
        a = im.convert("RGBA").getchannel("A")
        box = a.point(lambda v: 255 if v > 8 else 0).getbbox()
        w, h = im.size
    if not box:
        return 0.0, 0.0, 1.0, 1.0
    return box[0] / w, box[1] / h, box[2] / w, box[3] / h


def value_at(c, key, lt):
    """A value at `lt` seconds into the clip, as the page's FS.at: held before the first and after the last key,
    eased in and out between keys (smoothstep), or evenly with keyEase 'linear'."""
    ks = keys_of(c, key)
    if not ks:
        return _num(c.get(key), KEYED[key])
    if lt <= ks[0][0]:
        return ks[0][1]
    for i in range(1, len(ks)):
        if lt < ks[i][0]:
            (a, va), (b, vb) = ks[i - 1], ks[i]
            u = (lt - a) / max(1e-6, b - a)
            if c.get("keyEase") != "linear":
                u = u * u * (3 - 2 * u)
            return va + (vb - va) * u
    return ks[-1][1]


def volume_expression(c):
    """Volume keyframes as an ffmpeg expression of the clip's own time t (as value_at: held before the first key and
    after the last, eased between): the first value plus each step's change times its eased 0..1. None without keys."""
    ks = [[t, max(0.0, v)] for t, v in ((t, _num(v, 1.0)) for t, v in ((c.get("keys") or {}).get("volume") or []))]
    ks.sort(key=lambda k: k[0])
    if not ks:
        return None
    terms = [f"{ks[0][1]:.4f}"]
    for (a, va), (b, vb) in zip(ks, ks[1:]):
        if abs(vb - va) < 1e-6:
            continue
        u = f"clip((t-{_num(a, 0):.4f})/{max(1e-6, _num(b, 0) - _num(a, 0)):.4f},0,1)"
        e = u if c.get("keyEase") == "linear" else f"{u}*{u}*(3-2*{u})"
        terms.append(f"({vb - va:+.4f})*{e}")
    return "max(0," + "+".join(terms) + ")"


def _colour(c):
    """Brightness, contrast and saturation as the player's canvas filter does them (CSS brightness(), contrast(),
    saturate(), in that order): each channel times b; each channel pulled from or pushed to the middle by k; each
    channel mixed with the picture's luminance by s (the CSS saturate matrix)."""
    b, k, s = (_num(c.get(key), 1.0) for key in ("brightness", "contrast", "saturation"))
    out = []
    if abs(b - 1) > 1e-3:
        out.append(f"colorchannelmixer=rr={b:.4f}:gg={b:.4f}:bb={b:.4f}")
    if abs(k - 1) > 1e-3:
        o = 0.5 * (1 - k)
        e = f"val*{k:.4f}+{o * 255:.3f}"  # lutrgb clips the result to 0..255 itself
        out.append(f"lutrgb=r={e}:g={e}:b={e}")
    if abs(s - 1) > 1e-3:
        m = [[0.213 + 0.787 * s, 0.715 - 0.715 * s, 0.072 - 0.072 * s],
             [0.213 - 0.213 * s, 0.715 + 0.285 * s, 0.072 - 0.072 * s],
             [0.213 - 0.213 * s, 0.715 - 0.715 * s, 0.072 + 0.928 * s]]
        names = (("rr", "rg", "rb"), ("gr", "gg", "gb"), ("br", "bg", "bb"))
        out.append("colorchannelmixer=" + ":".join(f"{n}={v:.4f}" for row, ns in zip(m, names) for n, v in zip(ns, row)))
    gr, gg, gb = gains(c)
    if max(abs(gr - 1), abs(gg - 1), abs(gb - 1)) > 1e-4:
        out.append(f"colorchannelmixer=rr={gr:.4f}:gg={gg:.4f}:bb={gb:.4f}")
    return out


def crop_of(c):
    """(left, top, right, bottom) parts of the picture hidden, as the page's FS.cropOf: each 0..0.95, and a side with its
    opposite at most 0.95."""
    if c.get("kind") not in ("video", "image"):
        return 0.0, 0.0, 0.0, 0.0
    v = [max(0.0, min(0.95, _num(c.get(k), 0.0))) for k in ("cropL", "cropT", "cropR", "cropB")]
    for a, b in ((0, 2), (1, 3)):
        if v[a] + v[b] > 0.95:
            k = 0.95 / (v[a] + v[b])
            v[a], v[b] = v[a] * k, v[b] * k
    return tuple(v)


def _crop_centre(c, cx, cy, w, h, rotation):
    """The centre of the part that shows, for a clip whose whole picture would be w x h at (cx, cy), turned."""
    L, T, R, B = crop_of(c)
    dx, dy = (L - R) / 2 * w, (T - B) / 2 * h
    a = math.radians(rotation)
    return cx + math.cos(a) * dx - math.sin(a) * dy, cy + math.sin(a) * dx + math.cos(a) * dy


def mask_of(c):
    """The shape the part that shows is cut to, as the page's FS.maskOf: (shape, round, soft) or None."""
    if c.get("kind") not in ("video", "image") or c.get("mask") not in ("circle", "rounded"):
        return None
    rnd = 0.15 if c.get("maskRound") is None else _num(c.get("maskRound"), 0.15)
    return c["mask"], max(0.0, min(0.5, rnd)), max(0.0, min(0.25, _num(c.get("maskSoft"), 0.0)))


def mask_array(c, w, h):
    """The mask at w x h, uint8 (255 where it shows): the shape drawn smaller by half the softness (at 4x, for smooth
    edges), then blurred by half the softness, as the page's maskCanvas."""
    from PIL import Image, ImageDraw, ImageFilter
    shape, rnd, soft = mask_of(c)
    side = min(w, h)
    f = soft * side
    i = f / 2
    k = max(1, min(4, int((16e6 / max(1, w * h)) ** 0.5)))   # at 4x for smooth edges, at most 16 million pixels
    im = Image.new("L", (w * k, h * k), 0)
    d = ImageDraw.Draw(im)
    box = (i * k, i * k, (w - i) * k - 1, (h - i) * k - 1)
    if shape == "circle":
        d.ellipse(box, fill=255)
    else:
        r = min(rnd * side, (side - 2 * i) / 2)
        d.rounded_rectangle(box, radius=r * k, fill=255)
    im = im.resize((w, h), Image.LANCZOS)
    if f > 0:
        im = im.filter(ImageFilter.GaussianBlur(f / 2))
    import numpy as np
    return np.asarray(im, dtype=np.uint8)


def mask_png(c, w, h):
    shape, rnd, soft = mask_of(c)
    out = media._cache(f"mask-{shape}-{rnd:.4f}-{soft:.4f}-{w}x{h}.png")
    if not os.path.isfile(out):
        from PIL import Image
        Image.fromarray(mask_array(c, w, h)).save(out + ".tmp.png")
        os.replace(out + ".tmp.png", out)
    return out


def _held_to(dur, fps):
    """A layer's frames from its start, running two frames past its `dur` seconds (its last frame held if its frames
    end sooner). The overlay's enable window still ends it exactly at `dur`; but a layer's own last frame is never
    shown, because overlay treats it as ended the moment it arrives, and that left one black frame before every cut
    (and fps= also drops a clip's last frame when its length is not a whole number of frames)."""
    return [f"fps={fps:.6f}", f"tpad=stop_mode=clone:stop_duration={3.0 / fps + 0.1:.6f}",
            f"trim=duration={dur + 2.0 / fps:.6f}", "setpts=PTS-STARTPTS"]


def fill_of(c, H):
    """(sigma, margin, dim) of the blurred fill behind a picture or video, as the page's FS.fillOf, or None."""
    if c.get("kind") not in ("video", "image") or c.get("fill") != "blur":
        return None
    part = 0.03 if c.get("fillBlur") is None else _num(c.get("fillBlur"), 0.03)
    sigma = max(1.0, min(0.1, max(0.005, part)) * H)
    return sigma, math.ceil(3 * sigma), 0.85


def gains(c):
    """Warmth (-1 blue … 1 amber) and tint (-1 green … 1 magenta) as red, green and blue gains, as the page's FS.gains."""
    w, m = _num(c.get("warmth"), 0.0), _num(c.get("tint"), 0.0)
    return (1 + 0.25 * w) * (1 + 0.1 * m), 1 - 0.2 * m, (1 - 0.25 * w) * (1 + 0.1 * m)


SOFT_CUT_MAX = 3.0
DECLICK = 0.006   # seconds: a hard cut in the middle of a wave clicks; this short a fade is not heard as one


def soft_cuts(name, clips, sounds):
    """How each sounding clip's sound begins and ends at the cuts of its track, as the page's FS.soundCross /
    FS.soundOut: {clip id: {cross_in, tail, cross_out, declick_in, declick_out}}.

    A clip crossfades with the sound of the clip just before it on its track over its `soundIn` seconds (as long as
    its picture transition when unset), when both sound; the one before goes on past its end from its own file (a
    "handle") when the file has that much more and its speed does not ramp, otherwise it fades out before the cut.
    Every other edge gets a fade of a few thousandths of a second against clicks, except between two halves of one
    split clip, where the sound simply goes on."""
    sounding = {c["id"]: c for c in sounds}
    by_track = {}
    for c in clips:
        by_track.setdefault(c.get("track"), []).append(c)
    start = lambda c: _num(c.get("start"), 0)
    end = lambda c: start(c) + _num(c.get("duration"), 0)

    def neighbour(c, before):
        for x in by_track.get(c.get("track"), []):
            if x is not c and abs((end(x) - start(c)) if before else (start(x) - end(c))) < 1e-3:
                return x
        return None

    def cross(c):
        b = neighbour(c, True)
        if not b or b["id"] not in sounding or c["id"] not in sounding:
            return 0.0
        x = c.get("soundIn")
        x = _num(x, 0) if x is not None else _num(c.get("transitionIn"), 0)
        return max(0.0, min(x, _num(c.get("duration"), 0), SOFT_CUT_MAX))

    def continues(a, b):
        """b plays on from where a stops in the same file (the two halves of a split)."""
        if not a or not b or a.get("media") != b.get("media") or ramped(a) or ramped(b):
            return False
        sa, sb = _num(a.get("speed"), 1.0), _num(b.get("speed"), 1.0)
        return abs(sa - sb) < 1e-6 and abs(_num(a.get("in"), 0) + _num(a.get("duration"), 0) * sa - _num(b.get("in"), 0)) < 2e-3

    room = {}

    def file_left(c):
        """seconds of the edit the clip's file still has after the clip's end"""
        if c["id"] not in room:
            try:
                total = media.probe(projects.media_path(name, c["media"])).get("duration") or 0
            except Exception:
                total = 0
            speed = max(0.05, _num(c.get("speed"), 1.0))
            room[c["id"]] = (total - _num(c.get("in"), 0)) / speed - _num(c.get("duration"), 0)
        return room[c["id"]]

    out = {}
    for c in sounds:
        b, a = neighbour(c, True), neighbour(c, False)
        e = {"cross_in": cross(c), "tail": 0.0, "cross_out": 0.0}
        x = cross(a) if a else 0.0
        if x > 0:
            if not ramped(c) and file_left(c) >= x - 1e-3:
                e["tail"] = x
            else:
                e["cross_out"] = min(x, _num(c.get("duration"), 0))
        e["declick_in"] = not e["cross_in"] and not continues(b, c)
        e["declick_out"] = not e["tail"] and not e["cross_out"] and not continues(c, a)
        out[c["id"]] = e
    return out


def _audio_chain(graph, idx, c, start, dur, speed, n, talk=(), edges=None):
    edges = edges or {}
    chain = [f"[{idx}:a]asetpts=PTS-STARTPTS", "aresample=48000", "aformat=channel_layouts=stereo"]
    if abs(speed - 1.0) > 1e-3:
        chain.append(_atempo(speed))
    moving = volume_expression(c)
    vol = max(0.0, _num(c.get("volume"), 1.0))
    if not moving and abs(vol - 1.0) > 1e-3:
        chain.append(f"volume={vol:.4f}")
    level = _num(c.get("duck"), 0)
    lowered = duck.expression(level, talk, start, dur) if 0 < level < 1 else None
    if moving or lowered:
        # in short pieces, so the gain moves smoothly (it is worked out once a piece)
        chain.append("asetnsamples=n=256:p=0")
    if moving:
        chain.append(f"volume='{moving}':eval=frame")
    if lowered:
        chain.append(f"volume='{lowered}':eval=frame")
    fi, fo = _num(c.get("fadeIn"), 0), _num(c.get("fadeOut"), 0)
    if fi > 0:
        chain.append(f"afade=t=in:st=0:d={fi:.4f}")
    if fo > 0:
        chain.append(f"afade=t=out:st={max(0.0, dur - fo):.4f}:d={fo:.4f}")
    # the cuts (soft_cuts): equal-power crossfades (a quarter sine in, its mirror out) or a click-free edge
    xin, tail, xout = edges.get("cross_in", 0), edges.get("tail", 0), edges.get("cross_out", 0)
    if xin > 0:
        chain.append(f"afade=t=in:st=0:d={xin:.4f}:curve=qsin")
    elif edges.get("declick_in") and fi <= 0 and dur > 4 * DECLICK:
        chain.append(f"afade=t=in:st=0:d={DECLICK}")
    if tail > 0:
        chain.append(f"afade=t=out:st={dur:.4f}:d={tail:.4f}:curve=qsin")
    elif xout > 0:
        chain.append(f"afade=t=out:st={max(0.0, dur - xout):.4f}:d={xout:.4f}:curve=qsin")
    elif edges.get("declick_out") and fo <= 0 and dur > 4 * DECLICK:
        chain.append(f"afade=t=out:st={dur - DECLICK:.4f}:d={DECLICK}")
    chain += [f"atrim=0:{dur + tail:.6f}", f"adelay={int(round(start * 1000))}:all=1"]
    label = f"a{n}"
    graph.append(",".join(chain) + f"[{label}]")
    return label


def _run_ffmpeg(job, args, graph, total, out, extra_out, work):
    script = os.path.join(work, "graph.txt")
    with open(script, "w", encoding="utf-8") as f:
        f.write(graph)
    progress = os.path.join(work, "progress.txt")
    log = os.path.join(work, "ffmpeg.log")
    cmd = [library.ffmpeg(), "-y", "-hide_banner", "-nostats", "-progress", progress] + args + \
          ["-filter_complex_script", script] + extra_out + [out]
    with open(os.path.join(work, "command.json"), "w", encoding="utf-8") as f:
        json.dump(cmd, f, ensure_ascii=False, indent=1)
    with open(log, "w", encoding="utf-8", errors="replace") as logf:
        proc = subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        job["process"] = proc
        while proc.poll() is None:
            time.sleep(0.5)
            try:
                with open(progress, encoding="utf-8", errors="replace") as f:
                    lines = f.read().splitlines()
                done = [l for l in lines if l.startswith("out_time_us=")]
                if done:
                    t = int(done[-1].split("=", 1)[1]) / 1e6
                    job.update(progress=min(0.99, max(0.0, t / total)) * job.get("span", 1.0), text=f"{t:.1f} of {total:.1f} s")
            except (OSError, ValueError):
                pass
    if proc.returncode != 0 or not os.path.isfile(out):
        with open(log, encoding="utf-8", errors="replace") as f:
            tail = [l for l in f.read().strip().splitlines() if l.strip()][-8:]
        raise RuntimeError("ffmpeg stopped: " + " | ".join(tail)[-700:])


def _target(name, preset, options):
    """Where the export goes: the chosen folder (or the dated one under the exports folder) and file name, never over
    an existing file. HDR is HEVC in MP4 whatever the format."""
    folder = (options.get("folder") or "").strip().strip('"')
    if folder:
        folder = os.path.normpath(os.path.expandvars(os.path.expanduser(folder)))
        if not os.path.isabs(folder):
            raise ValueError(f"not a full folder path: {folder}")
    else:
        folder = os.path.join(paths.exports_dir(), time.strftime("%Y-%m-%d"))
    os.makedirs(folder, exist_ok=True)
    ext = "mp4" if options.get("hdr") else PRESETS[preset]["ext"]
    stem = projects.BAD.sub("_", (options.get("filename") or "").strip())
    stem = re.sub(r"\.(mp4|mov|mkv|webm)$", "", stem, flags=re.I).strip(" .")
    stem = stem or f"{projects.BAD.sub('_', name)}-{time.strftime('%H%M%S')}"
    out, n = os.path.join(folder, f"{stem}.{ext}"), 2
    while os.path.exists(out):
        out, n = os.path.join(folder, f"{stem} ({n}).{ext}"), n + 1
    return out


def finishing(options):
    """The finishing steps asked for (frames, upscale, HDR): {} when none."""
    out = {}
    if int(options.get("fps_x") or 1) in (2, 4):
        out["fps_x"] = int(options["fps_x"])
        out["interpolator"] = options.get("interpolator") or "rife"
    up = options.get("upscale") or {}
    if float(up.get("scale") or 1) > 1:
        out["upscale"] = {"scale": min(4.0, float(up["scale"])), "engine": up.get("engine", "rtx"),
                          "mode": up.get("mode", "HIGHBITRATE_ULTRA")}
    if options.get("hdr"):
        out["hdr"] = {k: v for k, v in options["hdr"].items() if k in ("style", "tone", "strength", "eotf", "preset")}
    return out


def finish_tools():
    """What the finishing steps can use on this PC (for the export dialog)."""
    import importlib.util
    hdr_home = os.path.normpath(os.path.join(paths.WEBUI, "..", "..", "sdr2hdr"))
    engine = os.path.join(paths.WEBUI, "video-studio", "env", "Scripts", "python.exe")
    return {
        "rtx": importlib.util.find_spec("nvvfx") is not None,
        "rife": os.path.isfile(os.path.join(paths.WEBUI, "models", "Deforum", "RIFE46.pkl")),
        "film": os.path.isfile(os.path.join(paths.WEBUI, "models", "Deforum", "film_interpolation", "film_net_fp16.pt")),
        "hdr": os.path.isdir(os.path.join(hdr_home, "src", "sdr2hdr")) and os.path.isfile(engine),
        "hdr_home": hdr_home, "engine": engine,
    }


def _finish(job, src, out, steps, preset):
    """Runs finish.py (Forge's Python) on the exported file; progress into the job."""
    import sys
    tools = finish_tools()
    if steps.get("hdr") and not tools["hdr"]:
        raise RuntimeError("HDR needs sdr2hdr in " + tools["hdr_home"] + " and Video Studio's engine Python")
    try:  # the card for the finishing, not for Forge's checkpoint
        from backend import memory_management
        memory_management.unload_all_models()
        memory_management.soft_empty_cache()
    except Exception:
        pass
    work = os.path.join(os.path.dirname(src), "finish")
    spec = dict(steps, input=src, output=out, work=work, ffmpeg=library.ffmpeg(), encoder=PRESETS[preset]["video"],
                hdr_python=tools["engine"], sdr2hdr=tools["hdr_home"],
                rife_model=os.path.join(paths.WEBUI, "models", "Deforum", "RIFE46.pkl"),
                film_model=os.path.join(paths.WEBUI, "models", "Deforum", "film_interpolation", "film_net_fp16.pt"),
                deforum_scripts=os.path.join(paths.WEBUI, "extensions", "sd-forge-deforum", "scripts"))
    os.makedirs(work, exist_ok=True)
    spec_path = os.path.join(work, "job.json")
    with open(spec_path, "w", encoding="utf-8") as f:
        json.dump(spec, f, ensure_ascii=False, indent=1)
    names = {"picture": "more frames / bigger picture", "hdr": "HDR"}
    proc = subprocess.Popen([sys.executable, "-u", os.path.join(os.path.dirname(__file__), "finish.py"), spec_path],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                            errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    job["process"] = proc
    parts = (["picture"] if steps.get("fps_x") or steps.get("upscale") else []) + (["hdr"] if steps.get("hdr") else [])
    last, tail = {}, []
    for line in proc.stdout:
        tail = (tail + [line.strip()])[-15:]
        if not line.startswith("@@FS "):
            continue
        ev = json.loads(line[5:])
        last = ev
        if ev["stage"] in parts:
            i = parts.index(ev["stage"])
            job.update(progress=0.5 + 0.49 * (i + ev["progress"]) / len(parts),
                       text=f"{names[ev['stage']]}: {ev['text']}", stage=ev["stage"])
        elif ev["stage"] == "sound":
            job.update(text="putting the sound back")
    proc.wait()
    if job["state"] == "cancelled":
        return
    if proc.returncode != 0 or last.get("stage") != "done":
        raise RuntimeError(last.get("text") if last.get("stage") == "error" else "finishing stopped: " + " | ".join(tail)[-600:])


def start(name, doc, preset="h264", loudness=None, options=None):
    if preset not in PRESETS:
        raise ValueError(f"unknown export preset {preset}")
    options = options or {}
    steps = finishing(options)
    with _lock:
        if any(j["state"] == "running" for j in jobs.values()):
            raise ValueError("an export is already running")
        args, graph, total, fps = build(name, doc, preset, loudness)
        final = _target(name, preset, options)
        job_id = uuid.uuid4().hex[:10]
        work = paths.sub("exports-work", job_id)
        # with finishing steps the edit is exported into the work folder first, and the finished file goes out
        out = os.path.join(work, "edit." + PRESETS[preset]["ext"]) if steps else final
        job = {"id": job_id, "state": "running", "project": name, "preset": preset, "out": final, "started": time.time(),
               "progress": 0.0, "text": "", "seconds": total, "steps": steps, "span": 0.5 if steps else 1.0}
        jobs[job_id] = job
    p = PRESETS[preset]
    a_args, a_graph, _t, _f = build(name, doc, preset, loudness, audio_only=True)

    def run():
        # The picture and the sound are made by two ffmpegs at once and joined at the end without encoding again:
        # in one graph they waited on each other now and then (a 16 s edit stalled in one export of two).
        t0 = time.time()
        try:
            from . import moving as moving_mod
            drawn = moving_mod.draw_all(name, doc, paths.sub("exports-work", job_id, "moving"), job)
            if job["state"] == "cancelled":
                return
            v_args, v_graph, _t, _f = build(name, doc, preset, None, video_only=True, moving=drawn)
            picture = os.path.join(work, "picture." + p["ext"])
            sound = os.path.join(work, "sound.wav")
            audio_job = {}
            audio_error = []

            def make_sound():
                try:
                    _run_ffmpeg(audio_job, a_args, a_graph, total, sound,
                                ["-map", "[aout]", "-c:a", "pcm_s16le", "-t", f"{total:.6f}"], paths.sub("exports-work", job_id, "sound"))
                except Exception as e:  # reported with the picture's result
                    audio_error.append(e)
            sound_thread = threading.Thread(target=make_sound, daemon=True)
            sound_thread.start()
            _run_ffmpeg(job, v_args, v_graph, total, picture,
                        ["-map", "[vout]", "-r", f"{fps:.6f}"] + p["video"] + ["-t", f"{total:.6f}"], work)
            if job["state"] == "cancelled":
                if audio_job.get("process"):
                    audio_job["process"].kill()
                return
            job["text"] = "joining the picture and the sound"
            sound_thread.join()
            if audio_error:
                raise audio_error[0]
            mux = [library.ffmpeg(), "-y", "-v", "error", "-i", picture, "-i", sound, "-map", "0:v", "-map", "1:a",
                   "-c:v", "copy"] + p.get("audio", ["-c:a", "aac", "-b:a", "192k"]) + ["-t", f"{total:.6f}"] +                 (["-movflags", "+faststart"] if p["ext"] == "mp4" else []) + [out]
            r = subprocess.run(mux, capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if r.returncode != 0 or not os.path.isfile(out):
                raise RuntimeError("joining the picture and the sound failed: " + r.stderr.decode(errors="replace")[-400:])
            made = out
            if steps:
                job.update(progress=0.5, text="the edit is exported; finishing")
                _finish(job, out, final, steps, preset)
                if job["state"] == "cancelled":
                    return
                made = final
            job.update(state="done", progress=1.0, bytes=os.path.getsize(made), took=time.time() - t0,
                       fps=round(total * fps / max(0.01, time.time() - t0), 1))
            try:
                job["media"] = library.add_file(made)["id"]
            except Exception:
                pass
            import shutil
            shutil.rmtree(work, ignore_errors=True)
        except Exception as e:
            if job["state"] != "cancelled":
                job.update(state="error", message=str(e)[:800], work=work)
            try:
                os.remove(final)
            except OSError:
                pass
        finally:
            job["ended"] = time.time()
            job.pop("process", None)
    threading.Thread(target=run, name="forge-studio-render", daemon=True).start()
    return public(job)


def mixdown(name, doc, out, task=None):
    """The edit's sound alone, as a WAV (for Whisper)."""
    args, graph, total, _fps = build(name, doc, audio_only=True)
    work = paths.sub("exports-work", "mix-" + uuid.uuid4().hex[:6])
    job = task if task is not None else {}
    _run_ffmpeg(job, args, graph, total, out, ["-map", "[aout]", "-c:a", "pcm_s16le", "-t", f"{total:.6f}"], work)
    import shutil
    shutil.rmtree(work, ignore_errors=True)
    return out


def cancel(job_id):
    job = jobs.get(job_id)
    if not job:
        raise KeyError(job_id)
    if job["state"] == "running":
        job["state"] = "cancelled"
        if job.get("process"):
            job["process"].kill()
    return public(job)


def public(job):
    out = {k: v for k, v in job.items() if k not in ("process",)}
    out["elapsed"] = (job.get("ended") or time.time()) - job["started"]
    return out

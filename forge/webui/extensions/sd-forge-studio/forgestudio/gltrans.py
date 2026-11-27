"""GLSL transitions in the export (the page's ✨ Transitions: 'gl:<name>' in clip.transition, gl-transitions' shaders).

For a clip r with such a transition, coming after clip p on its track, a segment as long as the transition is made:
the edit's picture up to r's layer (the layers above left out) is rendered over the transition's time twice, by
render.build itself on two copies of the edit - "before" (r hidden: p held on its last frame, as the player holds it)
and "after" (p hidden) - and the shader mixes them frame by frame (glfx.transition_frame, progress = time into the
transition / its length, as the page). render.build then lays the segment on r's layer during the transition, so the
layers above stay on top. Each extra render goes from the film's start to the transition's end (ffmpeg works the
graph from 0), so a transition late in a long edit costs more.
"""

import copy
import os
import subprocess

import numpy as np

from . import glfx, library, render

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _hide(c):
    c["opacity"] = 0.0
    keys = c.get("keys")
    if isinstance(keys, dict):
        keys.pop("opacity", None)
    c.pop("fx", None)


def _stream(name, doc, moving, start, seconds, W, H, work, tag):
    """The edit's picture from `start` for `seconds`, as RGBA frames (a generator) at the edit's size."""
    args, graph, _total, _fps = render.build(name, doc, "h264", None, video_only=True, moving=moving, rgba_out=True)
    script = os.path.join(work, f"graph-{tag}.txt")
    with open(script, "w", encoding="utf-8") as f:
        f.write(graph + f";[vout]trim=start={start:.6f}:duration={seconds:.6f},setpts=PTS-STARTPTS[seg]")
    cmd = [library.ffmpeg(), "-v", "error", "-y"] + args + ["-filter_complex_script", script, "-map", "[seg]",
                                                          "-f", "rawvideo", "-pix_fmt", "rgba", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
    size = W * H * 4
    try:
        while True:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            yield np.frombuffer(buf, np.uint8).reshape(H, W, 4)
    finally:
        proc.stdout.close()
        if proc.poll() is None:
            proc.kill()


def render_all(name, doc, work, job=None, moving=None):
    """{clip id: (segment file, start, seconds)} for every GLSL transition of the edit."""
    st = doc.get("settings", {})
    W, H = int(st.get("width", 1080)), int(st.get("height", 1920))
    fps = render._num(st.get("fps"), 30.0)
    tracks = doc.get("tracks", [])
    visual = [t["id"] for t in tracks if t.get("kind") != "audio" and not t.get("hidden")]
    clips = doc.get("clips", [])
    moving = moving or {}
    todo = []
    for r in clips:
        shader = render.gl_transition(r)
        if not shader or r.get("track") not in visual:
            continue
        rs = render._num(r.get("start"), 0)
        p = next((c for c in clips if c is not r and c.get("track") == r.get("track")
                  and abs(render._num(c.get("start"), 0) + render._num(c.get("duration"), 0) - rs) < 1e-3), None)
        if p is None or shader not in glfx.transitions():
            continue
        todo.append((r, p, shader))
    out = {}
    if not todo:
        return out
    os.makedirs(work, exist_ok=True)
    for k, (r, p, shader) in enumerate(todo):
        if job is not None:
            job["text"] = f"drawing the transitions ({k + 1} of {len(todo)})"
            if job.get("state") == "cancelled":
                break
        start = render._num(r.get("start"), 0)
        seconds = min(render._num(r.get("transitionIn"), 0), render._num(r.get("duration"), 0))
        n = max(1, int(round(seconds * fps)))
        keep = set(visual[:visual.index(r["track"]) + 1])

        def copy_doc(*hide_ids):
            d = copy.deepcopy(doc)
            d["clips"] = [c for c in d["clips"] if c.get("track") in keep and c.get("kind") not in ("audio", "gen")]
            for c in d["clips"]:
                if c.get("id") in hide_ids:
                    _hide(c)
                if c.get("id") == r["id"] and r["id"] not in hide_ids:
                    # the "after" picture: r itself, plainly (a transitionIn without a kind would be a dissolve)
                    c.pop("transition", None)
                    c.pop("transitionIn", None)
            return d
        before = copy_doc(r["id"])
        after = copy_doc(p["id"])
        mv_before = {i: f for i, f in moving.items() if i != r["id"]}
        mv_after = {i: f for i, f in moving.items() if i != p["id"]}
        path = os.path.join(work, f"gl-{r['id']}.mkv")
        enc = [library.ffmpeg(), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{W}x{H}",
               "-r", f"{fps:.6f}", "-i", "-", "-c:v", "utvideo", "-pix_fmt", "gbrap", path]
        proc = subprocess.Popen(enc, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, creationflags=NO_WINDOW)
        a_frames = _stream(name, before, mv_before, start, n / fps, W, H, work, f"{r['id']}-a")
        b_frames = _stream(name, after, mv_after, start, n / fps, W, H, work, f"{r['id']}-b")
        # a see-through transition shows the layers below the clip: a third picture, both clips hidden
        c_frames = (_stream(name, copy_doc(r["id"], p["id"]), {i: f for i, f in moving.items() if i not in (r["id"], p["id"])},
                            start, n / fps, W, H, work, f"{r['id']}-c") if glfx.see_through(shader) else iter(()))
        params = r.get("transitionParams") if isinstance(r.get("transitionParams"), dict) else None
        last_a = last_b = last_c = None
        try:
            for i in range(n):
                last_a = next(a_frames, last_a)
                last_b = next(b_frames, last_b)
                last_c = next(c_frames, last_c)
                if last_a is None or last_b is None:
                    raise RuntimeError("the transition's pictures could not be made")
                frame = glfx.transition_frame(shader, last_a, last_b, (i / fps) / seconds, params, base=last_c)
                proc.stdin.write(frame.tobytes())
            proc.stdin.close()
            err = proc.stderr.read().decode(errors="replace")
            if proc.wait() != 0 or not os.path.isfile(path):
                raise RuntimeError("writing a transition failed: " + err[-300:])
        finally:
            a_frames.close()
            b_frames.close()
            if hasattr(c_frames, "close"):
                c_frames.close()
            if proc.poll() is None:
                proc.kill()
            proc.stderr.close()
        out[r["id"]] = (path, start, n / fps)
    return out

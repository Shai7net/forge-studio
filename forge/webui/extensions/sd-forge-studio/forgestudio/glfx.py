"""GLSL transitions and effects for the export, on the graphics card (moderngl, an OpenGL 3.3 context without a
window). The page runs the very same shader text in WebGL2 (forge-studio-t-glfx.js), with the same wrapper:

- a transition (gl-transitions' format, MIT, https://github.com/gl-transitions/gl-transitions) defines
  `vec4 transition(vec2 uv)` and reads `getFromColor(uv)`, `getToColor(uv)`, `progress` (0..1) and `ratio`
  (width / height); its own uniforms come with defaults (`defaultParams`);
- an effect (fx/effects.json, written for Forge Studio) defines `vec4 effect(vec2 uv)` and reads `getColor(uv)`,
  `time` (seconds into the clip), `amount` (0..1) and `resolution` (pixels).

uv (0, 0) is the picture's bottom left, as in WebGL; pictures go in as numpy RGBA (rows top to bottom) and come out
the same way.

A few transitions leave parts of the frame see-through (premultiplied: the Cube's space around the box, Doorway,
Swap, ...): their result is laid over `base`, the picture of the layers below the clip (black when there is none),
and comes out whole (alpha 1), in both runtimes.
"""

import json
import os
import threading

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
FX = os.path.join(HERE, "fx")

HEAD = """#version 330
uniform float progress;
uniform float ratio;
uniform float time;
uniform float amount;
uniform vec2 resolution;
uniform sampler2D from_tex;
uniform sampler2D to_tex;
uniform sampler2D base_tex;
in vec2 v_uv;
out vec4 f_color;
vec4 getFromColor(vec2 uv) { return texture(from_tex, uv); }
vec4 getToColor(vec2 uv) { return texture(to_tex, uv); }
vec4 getColor(vec2 uv) { return texture(from_tex, uv); }
#define texture2D texture
"""
VERTEX = """#version 330
in vec2 pos;
out vec2 v_uv;
void main() { v_uv = pos * 0.5 + 0.5; gl_Position = vec4(pos, 0.0, 1.0); }
"""

# the transition's result (premultiplied) laid over the picture below it, whole (the page does the same)
TRANSITION_MAIN = """
void main() {
  vec4 c = transition(v_uv);
  f_color = vec4(c.rgb + (1.0 - c.a) * texture(base_tex, v_uv).rgb, 1.0);
}
"""

_lock = threading.Lock()
# an OpenGL context belongs to the thread that made it (each export runs in a thread of its own: a second export
# failed with "cannot create program" on the first one's context), so each thread has its own
_local = threading.local()


def _st():
    if not hasattr(_local, "state"):
        _local.state = {}
    return _local.state


def release():
    """The current thread's context goes (at the end of an export)."""
    st = _st()
    ctx = st.get("ctx")
    if ctx is not None:
        try:
            ctx.release()
        except Exception:
            pass
    st.clear()


_lists = {}


def _list(kind, key):
    path = os.path.join(FX, kind + ".json")
    stamp = os.path.getmtime(path)
    if _lists.get(kind, (None,))[0] != stamp:
        with open(path, encoding="utf-8") as f:
            _lists[kind] = (stamp, {x[key]: x for x in json.load(f)})
    return _lists[kind][1]


def transitions():
    return _list("transitions", "name")


def effects():
    return _list("effects", "id")


_see_through = {}


def see_through(name):
    """Whether transition `name` leaves parts of the frame see-through (it then needs the picture below)."""
    if name not in _see_through:
        y, x = np.mgrid[0:48, 0:80]
        a = np.stack([x * 3, y * 5, np.full_like(x, 40), np.full_like(x, 255)], -1).astype(np.uint8)
        b = np.stack([200 - x * 2, np.full_like(x, 60), y * 5, np.full_like(x, 255)], -1).astype(np.uint8)
        white = np.full_like(a, 255)
        # over a black and then a white base: a see-through part shows the difference
        _see_through[name] = any(
            int(np.abs(transition_frame(name, a, b, p, base=np.zeros_like(a)).astype(int)
                       - transition_frame(name, a, b, p, base=white).astype(int)).max()) > 6
            for p in (0.2, 0.5, 0.8))
    return _see_through[name]


def _ctx():
    if "ctx" not in _st():
        import moderngl
        ctx = moderngl.create_standalone_context()
        quad = ctx.buffer(np.array([-1, -1, 1, -1, -1, 1, 1, 1], dtype="f4").tobytes())
        _st().update(ctx=ctx, quad=quad, programs={})
    return _st()["ctx"]


def _program(source, kind):
    ctx = _ctx()
    key = (kind, source)
    progs = _st()["programs"]
    if key not in progs:
        body = source + (TRANSITION_MAIN if kind == "transition" else "\nvoid main() { f_color = effect(v_uv); }\n")
        prog = ctx.program(vertex_shader=VERTEX, fragment_shader=HEAD + body)
        vao = ctx.vertex_array(prog, [(_st()["quad"], "2f", "pos")])
        if len(progs) > 40:
            progs.clear()
        progs[key] = (prog, vao)
    return progs[key]


def check_transition(glsl):
    """None when the transition compiles, else the compiler's message."""
    try:
        with _lock:
            _program(glsl, "transition")
        return None
    except Exception as e:
        return str(e)


def _set(prog, name, value):
    if name not in prog:
        return
    u = prog[name]
    if isinstance(value, bool):
        value = int(value)
    # the uniform's own type decides (an ivec2 takes ints, a vec2 floats), as the page's uniform() does
    if isinstance(value, (list, tuple)):
        tries = [tuple(float(v) for v in value), tuple(int(round(float(v))) for v in value)]
    else:
        tries = [float(value), int(round(float(value)))]
    for v in tries:
        try:
            u.value = v
            return
        except Exception:
            continue


def _texture(ctx, rgba, slot):
    h, w = rgba.shape[:2]
    tex = ctx.texture((w, h), 4, np.ascontiguousarray(rgba[::-1]).tobytes())   # rows bottom to top, as GL reads them
    tex.repeat_x = tex.repeat_y = False
    tex.filter = (_moderngl().LINEAR, _moderngl().LINEAR)
    tex.use(slot)
    return tex


def _moderngl():
    import moderngl
    return moderngl


def _draw(prog, vao, w, h):
    ctx = _ctx()
    fbo = _st().get("fbo")
    if fbo is None or fbo.size != (w, h):
        if fbo is not None:
            fbo.release()
        fbo = _st()["fbo"] = ctx.simple_framebuffer((w, h), components=4)
    fbo.use()
    fbo.clear(0, 0, 0, 0)
    vao.render(_moderngl().TRIANGLE_STRIP)
    out = np.frombuffer(fbo.read(components=4), dtype=np.uint8).reshape(h, w, 4)
    return out[::-1].copy()


def transition_frame(name, a, b, progress, params=None, base=None):
    """Transition `name` between RGBA pictures a (from) and b (to) of the same size at `progress` (0..1), over
    `base` (the layers below; black when None)."""
    t = transitions()[name]
    h, w = a.shape[:2]
    with _lock:
        ctx = _ctx()
        prog, vao = _program(t["glsl"], "transition")
        values = dict(t.get("defaultParams") or {})
        values.update(params or {})
        for k, v in values.items():
            _set(prog, k, v)
        _set(prog, "progress", float(min(1.0, max(0.0, progress))))
        _set(prog, "ratio", w / float(h))
        ta, tb = _texture(ctx, a, 0), _texture(ctx, b, 1)
        tc = _texture(ctx, base if base is not None else np.zeros((2, 2, 4), np.uint8), 2)
        _set(prog, "from_tex", 0)
        _set(prog, "to_tex", 1)
        _set(prog, "base_tex", 2)
        try:
            return _draw(prog, vao, w, h)
        finally:
            ta.release()
            tb.release()
            tc.release()


def effect_frame(effect_id, rgba, time, amount, resolution=None):
    """Effect `effect_id` on an RGBA picture, `time` seconds into the clip, at `amount` (0..1); `resolution` = the
    clip's size in the edit (what the effects' pixel distances are measured in), else the picture's own size."""
    e = effects()[effect_id]
    h, w = rgba.shape[:2]
    rw, rh = resolution or (w, h)
    with _lock:
        ctx = _ctx()
        prog, vao = _program(e["glsl"], "effect")
        _set(prog, "time", float(time))
        _set(prog, "amount", float(amount))
        _set(prog, "resolution", (float(rw), float(rh)))
        _set(prog, "ratio", w / float(h))
        tex = _texture(ctx, rgba, 0)
        _set(prog, "from_tex", 0)
        try:
            return _draw(prog, vao, w, h)
        finally:
            tex.release()

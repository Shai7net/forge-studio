/* Forge Studio - GLSL transitions and video effects (the ⇄ Transitions and ✦ Effects tabs).
 *
 * Transitions are gl-transitions (MIT, https://github.com/gl-transitions/gl-transitions: 224 that compile here),
 * effects are Forge Studio's own (forgestudio/fx/effects.json). The page runs them in WebGL2 with the very wrapper
 * the export runs them in (forgestudio/glfx.py, moderngl), so the player and the export draw the same:
 *   a transition defines vec4 transition(vec2 uv) and reads getFromColor / getToColor, progress (0..1), ratio;
 *   an effect defines vec4 effect(vec2 uv) and reads getColor, time (seconds into the clip), amount, resolution.
 * A transition is 'gl:<name>' in clip.transition (with transitionIn seconds): it mixes the picture up to the clip's
 * layer before the cut (the clip before held on its last frame) and after it; the layers above stay on top. Effects
 * are clip.fx = [{id, amount}], on pictures and videos, applied to the clip's own picture before it is placed.
 */
(function () {
    'use strict';
    const FS = window.ForgeStudio = window.ForgeStudio || {};
    const S = FS.state;
    const T = (...a) => FS.T(...a);
    const X = FS.glfx = {transitions: [], effects: [], byName: {}, byId: {}, loaded: false};

    // ---- the runtime ----

    const HEAD = `#version 300 es
precision highp float;
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
`;
    const VERT = `#version 300 es
in vec2 pos;
out vec2 v_uv;
void main() { v_uv = pos * 0.5 + 0.5; gl_Position = vec4(pos, 0.0, 1.0); }`;
    let gl = null, glCanvas = null, quad = null, failed = false;
    const programs = new Map(), broken = new Set();

    function context() {
        if (gl || failed) return gl;
        glCanvas = document.createElement('canvas');
        gl = glCanvas.getContext('webgl2', {premultipliedAlpha: false, preserveDrawingBuffer: true, antialias: false});
        if (!gl) { failed = true; return null; }
        quad = gl.createBuffer();
        gl.bindBuffer(gl.ARRAY_BUFFER, quad);
        gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
        glCanvas.addEventListener('webglcontextlost', (e) => { e.preventDefault(); programs.clear(); gl = null; });
        return gl;
    }
    function program(source, kind) {
        const key = kind + '\n' + source;
        if (programs.has(key)) return programs.get(key);
        if (broken.has(key)) return null;
        const compile = (type, text) => {
            const s = gl.createShader(type);
            gl.shaderSource(s, text);
            gl.compileShader(s);
            if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
            return s;
        };
        try {
            const p = gl.createProgram();
            gl.attachShader(p, compile(gl.VERTEX_SHADER, VERT));
            gl.attachShader(p, compile(gl.FRAGMENT_SHADER, HEAD + source + (kind === 'transition'
                // the result (premultiplied) over the picture below it, whole: as the export (glfx.py TRANSITION_MAIN)
                ? '\nvoid main() {\n  vec4 c = transition(v_uv);\n  f_color = vec4(c.rgb + (1.0 - c.a) * texture(base_tex, v_uv).rgb, 1.0);\n}\n'
                : '\nvoid main() { f_color = effect(v_uv); }\n')));
            gl.linkProgram(p);
            if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p));
            const types = {};
            for (let i = 0, n = gl.getProgramParameter(p, gl.ACTIVE_UNIFORMS); i < n; i++) {
                const u = gl.getActiveUniform(p, i);
                types[u.name] = u.type;
            }
            const entry = {p, types, loc: (name) => gl.getUniformLocation(p, name)};
            if (programs.size > 60) programs.clear();
            programs.set(key, entry);
            return entry;
        } catch (e) {
            console.warn('Forge Studio GLSL', kind, e.message);
            broken.add(key);
            return null;
        }
    }
    function uniform(entry, name, value) {
        const loc = entry.loc(name);
        if (loc == null) return;
        const t = entry.types[name], v = Array.isArray(value) ? value : [value];
        const ints = [gl.INT, gl.BOOL, gl.SAMPLER_2D, gl.INT_VEC2, gl.INT_VEC3, gl.INT_VEC4, gl.BOOL_VEC2, gl.BOOL_VEC3, gl.BOOL_VEC4];
        const n = v.length;
        if (ints.includes(t)) gl['uniform' + n + 'iv'](loc, v.map((x) => (x === true ? 1 : x === false ? 0 : Math.round(x))));
        else gl['uniform' + n + 'fv'](loc, v.map(Number));
    }
    function texture(unit, source) {
        const tex = gl.createTexture();
        gl.activeTexture(gl.TEXTURE0 + unit);
        gl.bindTexture(gl.TEXTURE_2D, tex);
        // uv (0, 0) is the picture's bottom left, as in the export (it turns its rows over the same way)
        gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true);
        gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, source);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
        return tex;
    }
    function run(entry, w, h, set, sources) {
        glCanvas.width = w;
        glCanvas.height = h;
        gl.viewport(0, 0, w, h);
        gl.useProgram(entry.p);
        const texs = sources.map((s, i) => texture(i, s));
        uniform(entry, 'from_tex', 0);
        if (sources.length > 1) uniform(entry, 'to_tex', 1);
        if (sources.length > 2) uniform(entry, 'base_tex', 2);
        set(entry);
        const pos = gl.getAttribLocation(entry.p, 'pos');
        gl.bindBuffer(gl.ARRAY_BUFFER, quad);
        gl.enableVertexAttribArray(pos);
        gl.vertexAttribPointer(pos, 2, gl.FLOAT, false, 0, 0);
        gl.clearColor(0, 0, 0, 0);
        gl.clear(gl.COLOR_BUFFER_BIT);
        gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
        texs.forEach((t) => gl.deleteTexture(t));
        return glCanvas;
    }

    X.load = async function () {
        if (X.loaded) return X;
        const [tr, fx] = await Promise.all([FS.api('fx/transitions'), FS.api('fx/effects')]);
        X.transitions = tr;
        X.effects = fx;
        X.byName = Object.fromEntries(tr.map((t) => [t.name, t]));
        X.byId = Object.fromEntries(fx.map((e) => [e.id, e]));
        X.loaded = true;
        if (FS.player && FS.player.requestDraw) FS.player.requestDraw();
        return X;
    };
    X.title = (name) => String(name).replace(/([a-z0-9])([A-Z])/g, '$1 $2');
    X.ok = () => X.loaded && !!context();

    /* transition `name` from canvas a to canvas b at `progress`: the WebGL canvas (w x h), or null */
    let black = null;
    X.transition = function (name, a, b, progress, w, h, params, base) {
        const t = X.byName[name];
        if (!t || !context()) return null;
        const e = program(t.glsl, 'transition');
        if (!e) return null;
        return run(e, w, h, (entry) => {
            for (const [k, v] of Object.entries(Object.assign({}, t.defaultParams || {}, params || {}))) uniform(entry, k, v);
            uniform(entry, 'progress', Math.max(0, Math.min(1, progress)));
            uniform(entry, 'ratio', w / h);
        }, [a, b, base || black || (black = Object.assign(document.createElement('canvas'), {width: 2, height: 2}))]);
    };
    /* effect `id` on a picture: the WebGL canvas (w x h), or null; `res` = the clip's size in the edit */
    X.effect = function (id, src, time, amount, w, h, res) {
        const f = X.byId[id];
        if (!f || !context()) return null;
        const e = program(f.glsl, 'effect');
        if (!e) return null;
        return run(e, w, h, (entry) => {
            uniform(entry, 'time', time);
            uniform(entry, 'amount', amount);
            uniform(entry, 'resolution', res || [w, h]);
            uniform(entry, 'ratio', w / h);
        }, [src]);
    };

    // ---- the player's hooks (c-player.js) ----

    const scratch = {};
    const canvasOf = (key, w, h) => {
        let c = scratch[key];
        if (!c) c = scratch[key] = document.createElement('canvas');
        if (c.width !== w || c.height !== h) { c.width = w; c.height = h; }
        return c;
    };
    const clipBefore = (c, clips) => clips.find((p) => p !== c && p.track === c.track && Math.abs(FS.end(p) - c.start) < 1e-3);
    const glName = (c) => (c && c.transitionIn > 0 && String(c.transition || '').startsWith('gl:') ? c.transition.slice(3) : null);
    const inWindow = (c, t) => t >= c.start - 1e-6 && t < c.start + c.transitionIn;

    /* clip c is the held clip before a GLSL transition running now (it is drawn into the transition instead) */
    X.heldBy = function (c, t, clips) {
        if (!X.ok() || t < FS.end(c) - 1e-6) return false;
        const r = clips.find((x) => x !== c && x.track === c.track && Math.abs(x.start - FS.end(c)) < 1e-3);
        return !!(r && glName(r) && inWindow(r, t) && X.byName[glName(r)]);
    };
    /* the clip before c when c's GLSL transition runs now */
    X.into = function (c, t, clips) {
        const name = glName(c);
        if (!name || !X.loaded || !X.byName[name] || !inWindow(c, t)) return null;
        return clipBefore(c, clips) || null;
    };
    /* draws the transition into c over everything drawn so far (the layers below): true when it was drawn */
    X.drawTransition = function (x, c, p, t, drawOne) {
        const cv = x.canvas, w = cv.width, h = cv.height;
        const base = canvasOf('base', w, h), a = canvasOf('a', w, h), b = canvasOf('b', w, h);
        base.getContext('2d').drawImage(cv, 0, 0);
        const paint = (target, clip) => {
            const g = target.getContext('2d');
            g.setTransform(1, 0, 0, 1, 0, 0);
            g.clearRect(0, 0, w, h);
            g.drawImage(base, 0, 0);
            g.setTransform(x.getTransform());
            drawOne(g, clip);
            g.setTransform(1, 0, 0, 1, 0, 0);
        };
        paint(a, p);
        paint(b, c);
        const out = X.transition(glName(c), a, b, (t - c.start) / c.transitionIn, w, h, c.transitionParams, base);
        if (!out) return false;
        x.save();
        x.setTransform(1, 0, 0, 1, 0, 0);
        x.drawImage(out, 0, 0);
        x.restore();
        return true;
    };
    /* the clip's picture with its effects (the cropped part, at most 1280 px across), or null when it has none */
    X.clipPicture = function (c, src, t, crop) {
        const list = (c.fx || []).filter((f) => X.byId[f.id]);
        if (!list.length || !X.ok()) return null;
        const nw = src.videoWidth || src.naturalWidth || src.width, nh = src.videoHeight || src.naturalHeight || src.height;
        if (!nw || !nh) return null;
        const [L, Tp, R, B] = crop || [0, 0, 0, 0];
        const cw = nw * (1 - L - R), ch = nh * (1 - Tp - B);
        const k = Math.min(1, 1280 / Math.max(cw, ch));
        const w = Math.max(2, Math.round(cw * k)), h = Math.max(2, Math.round(ch * k));
        const {width: W, height: H} = S.doc.settings;
        const fit = Math.min(W / nw, H / nh);
        const res = [nw * fit * (1 - L - R), nh * fit * (1 - Tp - B)];   // the clip's size in the edit, as the export
        let cur = canvasOf('fxin', w, h);
        cur.getContext('2d').clearRect(0, 0, w, h);
        cur.getContext('2d').drawImage(src, L * nw, Tp * nh, cw, ch, 0, 0, w, h);
        list.forEach((f, i) => {
            const out = X.effect(f.id, cur, t - c.start, f.amount == null ? 0.7 : f.amount, w, h, res);
            if (!out) return;
            const next = canvasOf('fx' + (i % 2), w, h), g = next.getContext('2d');
            g.clearRect(0, 0, w, h);
            g.drawImage(out, 0, 0);
            cur = next;
        });
        return cur;
    };

    // ---- the sample pictures of the galleries ----

    function sample(which, w, h) {
        const c = document.createElement('canvas');
        c.width = w;
        c.height = h;
        const g = c.getContext('2d');
        const grad = g.createLinearGradient(0, 0, 0, h);
        if (which === 'a') {
            grad.addColorStop(0, '#ff9a5a'); grad.addColorStop(0.55, '#e0507a'); grad.addColorStop(1, '#3a1d5c');
            g.fillStyle = grad; g.fillRect(0, 0, w, h);
            g.fillStyle = '#ffe9a8'; g.beginPath(); g.arc(w * 0.5, h * 0.52, w * 0.18, 0, Math.PI * 2); g.fill();
            g.fillStyle = '#2a1640'; g.fillRect(0, h * 0.72, w, h * 0.28);
        } else {
            grad.addColorStop(0, '#9fe3ff'); grad.addColorStop(1, '#1d6f86');
            g.fillStyle = grad; g.fillRect(0, 0, w, h);
            g.fillStyle = '#f4f7fb'; g.beginPath(); g.moveTo(w * 0.1, h * 0.8); g.lineTo(w * 0.48, h * 0.3); g.lineTo(w * 0.9, h * 0.8); g.fill();
            g.fillStyle = '#2e5e3a'; g.fillRect(0, h * 0.78, w, h * 0.22);
        }
        return c;
    }

    // ---- the galleries ----

    const CATEGORIES = [
        ['trending', 'Trending'], ['basic', 'Basic'], ['slide', 'Slide'], ['wipe', 'Wipe'], ['zoom', 'Zoom'], ['3d', '3D'],
        ['blur', 'Blur'], ['light', 'Light'], ['glitch', 'Glitch'], ['distort', 'Distortion'], ['shape', 'Shapes'], ['all', 'All']];
    const TRENDING = ['Cube', 'Swirl', 'CrossZoom', 'Dreamy', 'GlitchMemories', 'Ripple', 'Doorway', 'FilmBurn', 'LightningFlash',
        'Kaleidoscope', 'Morph', 'DreamyZoom', 'Pixelize', 'Mosaic', 'Hexagonalize', 'StaticFade', 'LensFlare', 'Shockwave',
        'BurnThrough', 'BookFlip', 'Bounce', 'WindowSlices', 'WhipSpin', 'LightRays', 'CircleOpen', 'Directional', 'ZoomInCircles', 'GlitchDisplace'];
    const TAGS = {basic: ['fade', 'dissolve', 'blend'], slide: ['slide', 'push', 'directional'], wipe: ['wipe', 'reveal', 'iris'],
        zoom: ['zoom'], '3d': ['3d', 'flip', 'fold'], blur: ['blur', 'motion-blur'], light: ['light', 'glow', 'flash', 'burn'],
        glitch: ['glitch', 'noise', 'scanlines', 'retro', 'chromatic'], distort: ['distort', 'wave', 'organic', 'warp'],
        shape: ['shape', 'circle', 'pattern', 'grid', 'tiles', 'stripes']};
    const inCategory = (t, cat) => cat === 'all' || (cat === 'trending' ? TRENDING.includes(t.name) : (t.tags || []).some((g) => TAGS[cat].includes(g)));

    /* a tile: its picture drawn when it comes into view, moving while the pointer is on it */
    function tile(h, title, draw, onPick) {
        // the canvas sits in a square frame of its own: the grid sizes the frame, never the canvas (a canvas's own
        // 96 x 96 made the rows shorter than the tiles, and they lay over each other)
        const cv = h('canvas', {width: 96, height: 96, class: 'fx-gl-thumb'});
        const b = h('button', {class: 'fx-gl-tile', title}, h('span', {class: 'fx-gl-frame'}, cv), h('span', {class: 'fx-gl-name'}, title));
        let anim = null, shown = false;
        const still = () => { try { draw(cv, 0.5, 0.7); } catch (e) { /* drawn later */ } };
        new IntersectionObserver((entries, obs) => { if (entries.some((e) => e.isIntersecting) && !shown) { shown = true; still(); obs.disconnect(); } }).observe(cv);
        b.addEventListener('pointerenter', () => {
            const t0 = performance.now();
            const step = () => { const s = ((performance.now() - t0) / 1600) % 1.25; draw(cv, Math.min(1, s), (performance.now() - t0) / 1000); anim = requestAnimationFrame(step); };
            anim = requestAnimationFrame(step);
        });
        b.addEventListener('pointerleave', () => { cancelAnimationFrame(anim); still(); });
        b.addEventListener('click', FS.guard(onPick));
        return b;
    }

    function gallery(kind) {
        const h = FS.h;
        const list = h('div', {class: 'fx-gl-cats'});
        const grid = h('div', {class: 'fx-gl-grid'});
        const note = h('div', {class: 'fx-dim fx-gl-note'});
        const wrap = h('div', {class: 'fx-panel-body fx-gl'},
            h('div', {class: 'fx-dim'}, kind === 'transitions'
                ? T('Pick a clip that comes after another (or put the playhead at a cut), then a transition. The pointer on a tile plays it.')
                : T('Pick one or more clips, then an effect. Its strength and its removal are in the inspector.')),
            h('div', {class: 'fx-gl-body'}, list, grid), note);
        const sa = sample('a', 96, 96), sb = sample('b', 96, 96);
        let cat = kind === 'transitions' ? 'trending' : null;
        const draw = () => {
            if (!X.loaded) return;
            const cats = kind === 'transitions' ? CATEGORIES : [...new Set(X.effects.map((e) => e.category))].map((c) => [c, c]);
            if (!cat) cat = cats[0][0];
            list.replaceChildren(...cats.map(([id, label]) => h('button', {class: 'fx-gl-cat' + (id === cat ? ' on' : ''),
                onclick: () => { cat = id; draw(); }}, T(label))));
            const items = kind === 'transitions' ? X.transitions.filter((t) => inCategory(t, cat)) : X.effects.filter((e) => e.category === cat);
            grid.replaceChildren(...items.map((it) => kind === 'transitions'
                ? tile(h, X.title(it.name), (cv, p) => { const out = X.transition(it.name, sa, sb, p, 96, 96); if (out) cv.getContext('2d').drawImage(out, 0, 0); },
                    () => applyTransition(it.name))
                : tile(h, T(it.name), (cv, _p, time) => { const out = X.effect(it.id, sa, time, 0.85, 96, 96, [96, 96]); if (out) cv.getContext('2d').drawImage(out, 0, 0); },
                    () => addEffect(it.id))));
            note.textContent = kind === 'transitions' ? T('{0} transitions from gl-transitions (MIT), by their authors', X.transitions.length) : '';
        };
        X.load().then(draw).catch((e) => { note.textContent = e.message; });
        if (!context()) note.textContent = T('This browser has no WebGL2: the transitions and effects cannot be shown here');
        return wrap;
    }

    /* the clip a transition goes into: the picked clip that follows another, else the cut nearest the playhead */
    function transitionTarget() {
        const clips = S.doc.clips;
        for (const id of S.selected) { const c = FS.clip(id); if (c && c.kind !== 'audio' && clipBefore(c, clips)) return c; }
        const main = FS.mainTrack && FS.mainTrack();
        const cuts = clips.filter((c) => c.kind !== 'audio' && (!main || c.track === main.id) && clipBefore(c, clips));
        cuts.sort((a, b) => Math.abs(a.start - S.time) - Math.abs(b.start - S.time));
        return cuts[0] || null;
    }
    function applyTransition(name) {
        if (!S.project) throw new Error(T('Open or create a project first'));
        const c = transitionTarget();
        if (!c) throw new Error(T('There is no cut: put two clips one after the other on a track'));
        FS.transition(c.id, c.transitionIn > 0 ? c.transitionIn : 0.8, 'gl:' + name);
        FS.select([c.id]);
        FS.seek(Math.max(0, c.start - 0.3));
        FS.message(T('Transition: {0}', X.title(name)));
    }
    function addEffect(id) {
        const picked = S.selected.map((x) => FS.clip(x)).filter((c) => c && (c.kind === 'video' || c.kind === 'image'));
        if (!picked.length) throw new Error(T('Pick a picture or video clip first'));
        FS.change('effect', () => {
            for (const c of picked) {
                const x = FS.clip(c.id);
                x.fx = (x.fx || []).filter((f) => f.id !== id);
                if (x.fx.length >= 4) x.fx.shift();
                x.fx.push({id, amount: 0.7});
            }
        });
        FS.emit('select', S.selected);
        FS.message(T('Effect: {0}', T(X.byId[id].name)));
    }

    // the clip's effects in the inspector: strength and removal
    (FS.inspectorHooks = FS.inspectorHooks || []).push((c, section) => {
        if (!(c.fx || []).length) return null;
        const h = FS.h;
        return section('Effects', ...c.fx.map((f, i) => {
            const e = X.byId[f.id];
            const r = h('input', {type: 'range', min: 0, max: 1, step: 0.01, value: f.amount == null ? 0.7 : f.amount, class: 'fx-range'});
            let began = false;
            r.addEventListener('input', () => {
                if (!began && FS.beginEdit) { FS.beginEdit(); began = true; }
                FS.clip(c.id).fx[i].amount = +r.value;
                if (FS.liveEdit) FS.liveEdit(); else FS.player.requestDraw && FS.player.requestDraw();
            });
            r.addEventListener('change', () => { began = false; if (FS.commitEdit) FS.commitEdit('effect'); });
            return h('div', {class: 'fx-prop fx-gl-fxrow'}, h('span', {class: 'fx-prop-label'}, '✦ ' + (e ? T(e.name) : f.id)), r,
                h('button', {class: 'fx-icon', title: T('Remove'), onclick: () => {
                    FS.change('effect off', () => { const x = FS.clip(c.id); x.fx.splice(i, 1); if (!x.fx.length) delete x.fx; });
                    FS.emit('select', S.selected);
                }}, '✕'));
        }));
    });

    FS.panels.push({id: 'transitions', icon: '⇄', label: 'Transitions', build: () => gallery('transitions')});
    FS.panels.push({id: 'effects', icon: '✦', label: 'Effects', build: () => gallery('effects')});
    X.load().catch(() => null);
})();

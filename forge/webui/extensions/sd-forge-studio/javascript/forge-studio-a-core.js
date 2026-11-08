/* Forge Studio - the core of the editor: the server's routes, the edit itself (one document), undo and redo,
 * opening and saving projects, what the files are, and the text renderer the player and the export share.
 *
 * The document (saved as project.json; render.py on the server reads the same fields):
 *   settings {width, height, fps, background}
 *   tracks   [{id, kind: 'video' | 'audio', main?, hidden?, muted?, locked?}]   visual tracks stack in this order
 *   clips    [{id, track, kind: 'video' | 'image' | 'audio' | 'text' | 'gen', media, start, duration, in, speed,
 *              x, y, scale, rotation, opacity, volume, fadeIn, fadeOut, muted, text?, gen?, takes?}]
 * Times are seconds. A clip plays `duration * speed` seconds of its file from `in`. x, y move the clip from the
 * frame's centre in fractions of the frame; scale 1 fits the whole picture inside the frame.
 */
(function () {
    'use strict';
    const FS = window.ForgeStudio = window.ForgeStudio || {};
    const q = encodeURIComponent;

    const listeners = {};
    FS.on = (event, fn) => (listeners[event] = listeners[event] || []).push(fn);
    FS.emit = (event, data) => (listeners[event] || []).slice().forEach((fn) => { try { fn(data); } catch (e) { console.error(e); } });

    FS.api = async function (path, body, raw) {
        const options = body === undefined ? {} : raw
            ? {method: 'POST', body}
            : {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)};
        const r = await fetch('/forge-studio/' + path, options);
        if (!r.ok) {
            let detail = r.statusText;
            try { detail = (await r.json()).detail || detail; } catch (e) { /* not JSON */ }
            throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
        }
        return r.json();
    };

    /* a task the server runs (frames.tasks), followed until it ends */
    FS.follow = async function (task, onText) {
        while (task.state === 'running') {
            await new Promise((r) => setTimeout(r, 1200));
            task = await FS.api('task?id=' + task.id);
            if (onText) onText(task);
        }
        if (task.state === 'error') throw new Error(task.text);
        return task;
    };

    /* A prompt in Hebrew, written in English for what will draw it by Forge's 🌐 prompt writer (the hebrew-translator
     * extension, Gemma 4): `mode` 't2v' / 'i2v' for Wan, 'image' for the checkpoint chosen at the top. Without the
     * writer, or if it fails, the words go as they are. */
    FS.english = async function (text, mode) {
        if (!/[֐-׿]/.test(text || '')) return text;
        FS.busy && FS.busy(FS.T('Writing the prompt in English…'));
        try {
            const r = await fetch('/translator/write', {method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({text, field: mode === 'image' ? 'img2img_prompt' : 'vs_prompt', video_mode: mode === 'image' ? '' : mode})});
            if (!r.ok) return text;
            const j = await r.json();
            if (!j.prompt) return text;
            FS.message && FS.message(FS.T('The prompt written for it: {0}', j.prompt.length > 160 ? j.prompt.slice(0, 160) + '…' : j.prompt));
            return j.prompt;
        } catch (e) {
            return text;
        } finally {
            FS.busy && FS.busy(null);
        }
    };

    /* ---- state ---- */

    const S = FS.state = {project: null, doc: null, media: {}, info: {}, selected: [], time: 0, playing: false,
        dirty: false, saving: false, savedText: null};

    FS.mediaUrl = (web) => `/forge-studio/project/media?name=${q(S.project)}&web=${q(web)}`;
    FS.stripUrl = (web) => `/forge-studio/strip?name=${q(S.project)}&web=${q(web)}`;
    FS.waveUrl = (web) => `/forge-studio/wave?name=${q(S.project)}&web=${q(web)}`;

    FS.CLIP_DEFAULTS = {in: 0, speed: 1, x: 0, y: 0, scale: 1, rotation: 0, opacity: 1, volume: 1, fadeIn: 0, fadeOut: 0, muted: false,
        brightness: 1, contrast: 1, saturation: 1, warmth: 0, tint: 0, cropL: 0, cropT: 0, cropR: 0, cropB: 0};
    /* crop: how much of each edge (left, top, right, bottom; parts of the picture) is hidden; what is left stays
     * where it was and as big as it was. A side and its opposite leave at least 5% between them. render.py crop_of */
    FS.CROP = ['cropL', 'cropT', 'cropR', 'cropB'];
    /* a shape the part that shows is cut to: clip.mask 'circle' (the ellipse that fits it) or 'rounded' (corners of
     * maskRound × its shorter side); maskSoft × the shorter side softens the edge: the shape drawn that much smaller
     * by half, then blurred by half (sigma). render.py mask_array draws the same. */
    /* in and out animations: clip.animIn / animOut = fade | rise | pop | wipe-r | wipe-l, over animInSec / animOutSec
     * (0.5 s; together at most the clip). The way out is the way in played backwards. Around the `anchor` (the part
     * of the clip that has something in it, [x0, y0, x1, y1] in parts of its box: a text's letters, a picture's all):
     *   fade    alpha rises evenly;
     *   rise    comes up from 6% of the frame lower, eased, while it fades in;
     *   pop     grows from 60% about the anchor's centre with a small overshoot (back-out), fading in quickly;
     *   wipe-r  shown from the anchor's right edge leftwards (Hebrew), wipe-l from the left, eased;
     *   type    (text) the letters appear one after another, evenly (FS.typedCount); out: they go from the end.
     * Returns {alpha, dx, dy (frame parts), scale, about [x, y] (box parts), reveal [x0, x1] (box parts)}.
     * render.py anim_at does the same. */
    FS.ANIMS = ['fade', 'rise', 'pop', 'wipe-r', 'wipe-l', 'type'];
    /* the typewriter: a text's letters (graphemes: a Hebrew letter with its niqqud is one) in the order they are
     * drawn, big lines first, then the small ones; spaces count, so the rhythm follows the words */
    FS.graphemes = (str) => {
        try { return [...new Intl.Segmenter(undefined, {granularity: 'grapheme'}).segment(str)].map((x) => x.segment); }
        catch (e) { return Array.from(str); }
    };
    FS.textRows = (t) => String((t && t.text) || '').split('\n').filter((l) => l.trim())
        .concat(String((t && t.sub) || '').split('\n').filter((l) => l.trim()));
    FS.typeSteps = (t) => FS.textRows(t).reduce((n, l) => n + FS.graphemes(l).length, 0);
    FS.typing = (c) => c.kind === 'text' && (c.animIn === 'type' || c.animOut === 'type');
    /* how many letters show `lt` seconds into the clip, of n (render.py typed_count does the same arithmetic) */
    FS.typedCount = function (c, lt, n) {
        const [a, b] = FS.animSpans(c);
        let k = n;
        if (c.animIn === 'type' && a > 0 && lt < a) k = Math.min(k, Math.floor(n * Math.max(0, lt) / a));
        if (c.animOut === 'type' && b > 0 && lt > c.duration - b) k = Math.min(k, Math.floor(n * Math.max(0, c.duration - lt) / b));
        return Math.max(0, k);
    };
    const backOut = (u) => { const c1 = 1.70158, c3 = c1 + 1; return 1 + c3 * Math.pow(u - 1, 3) + c1 * Math.pow(u - 1, 2); };
    FS.animSpans = (c) => {
        let a = FS.ANIMS.includes(c.animIn) ? Math.max(0.05, c.animInSec == null ? 0.5 : +c.animInSec) : 0;
        let b = FS.ANIMS.includes(c.animOut) ? Math.max(0.05, c.animOutSec == null ? 0.5 : +c.animOutSec) : 0;
        if (a + b > c.duration) { const k = c.duration / (a + b); a *= k; b *= k; }
        return [a, b];
    };
    FS.animated = (c) => FS.ANIMS.includes(c.animIn) || FS.ANIMS.includes(c.animOut);
    FS.animAt = function (c, t, anchor) {
        const out = {alpha: 1, dx: 0, dy: 0, scale: 1, about: [0.5, 0.5], reveal: [0, 1]};
        if (!FS.animated(c)) return out;
        const [x0, y0, x1, y1] = anchor || [0, 0, 1, 1];
        out.about = [(x0 + x1) / 2, (y0 + y1) / 2];
        const lt = t - c.start, [a, b] = FS.animSpans(c);
        const phases = [];
        if (a > 0 && lt < a) phases.push([c.animIn, Math.max(0, lt / a)]);
        if (b > 0 && lt > c.duration - b) phases.push([c.animOut, Math.max(0, (c.duration - lt) / b)]);
        for (const [kind, u] of phases) {
            const e = u * u * (3 - 2 * u);
            if (kind === 'fade') out.alpha *= u;
            else if (kind === 'rise') { out.dy += (1 - e) * 0.06; out.alpha *= e; }
            else if (kind === 'pop') { out.scale *= 0.6 + 0.4 * backOut(u); out.alpha *= Math.min(1, u * 4); }
            else if (kind === 'wipe-r') out.reveal = [Math.max(out.reveal[0], x1 - (x1 - x0) * e), out.reveal[1]];
            else if (kind === 'wipe-l') out.reveal = [out.reveal[0], Math.min(out.reveal[1], x0 + (x1 - x0) * e)];
        }
        return out;
    };
    /* chroma key: clip.key = the colour taken away ('#00ff00'), keySimilar (0.3), keySoft (0.1), keySpill (0.6).
     * A pixel's distance from the colour (0..1, RGB, as ffmpeg's colorkey measures it); see-through below `similar`,
     * rising evenly to whole over `soft` above it; then the key's own channel (green for green) is pulled towards the
     * stronger of the other two by `spill`, so the edges lose the green cast. render.py chroma does the same. */
    FS.keyOf = (c) => {
        if ((c.kind !== 'video' && c.kind !== 'image') || !/^#[0-9a-f]{6}$/i.test(c.key || '')) return null;
        const rgb = [1, 3, 5].map((i) => parseInt(c.key.slice(i, i + 2), 16));
        return {rgb, dom: rgb.indexOf(Math.max(...rgb)),
            similar: Math.max(0, Math.min(1, c.keySimilar == null ? 0.3 : +c.keySimilar)),
            soft: Math.max(0.001, Math.min(0.5, c.keySoft == null ? 0.1 : +c.keySoft)),
            spill: Math.max(0, Math.min(1, c.keySpill == null ? 0.6 : +c.keySpill))};
    };
    /* the key applied to RGBA pixels in place (ImageData.data) */
    FS.applyKey = function (k, px) {
        const [kr, kg, kb] = k.rgb, norm = 1 / (3 * 255 * 255), o1 = (k.dom + 1) % 3, o2 = (k.dom + 2) % 3;
        for (let i = 0; i < px.length; i += 4) {
            const dr = px[i] - kr, dg = px[i + 1] - kg, db = px[i + 2] - kb;
            const diff = Math.sqrt((dr * dr + dg * dg + db * db) * norm);
            const a = Math.max(0, Math.min(1, (diff - k.similar) / k.soft));
            const m = Math.max(px[i + o1], px[i + o2]), ex = px[i + k.dom] - m;
            if (ex > 0 && k.spill > 0) px[i + k.dom] = Math.round(px[i + k.dom] - k.spill * ex);
            px[i + 3] = Math.round(px[i + 3] * a);
        }
    };
    /* a blurred fill behind a picture that does not fill the frame: clip.fill 'blur', fillBlur (0.03 of the frame's
     * height, the Gaussian's sigma). The same source, scaled to cover the frame and a margin of 3 sigma all round (so
     * the blur's edges fall outside), blurred, at 85% brightness, behind the clip and fading with it. render.py
     * fill_of does the same. */
    FS.fillOf = (c) => {
        if ((c.kind !== 'video' && c.kind !== 'image') || c.fill !== 'blur') return null;
        const H = S.doc.settings.height;
        const sigma = Math.max(1, Math.min(0.1, Math.max(0.005, c.fillBlur == null ? 0.03 : +c.fillBlur)) * H);
        return {sigma, margin: Math.ceil(3 * sigma), dim: 0.85};
    };
    FS.MASK_SHAPES = ['circle', 'rounded'];
    FS.maskOf = (c) => {
        if ((c.kind !== 'video' && c.kind !== 'image') || !FS.MASK_SHAPES.includes(c.mask)) return null;
        return {shape: c.mask, round: Math.max(0, Math.min(0.5, c.maskRound == null ? 0.15 : +c.maskRound)),
            soft: Math.max(0, Math.min(0.25, +c.maskSoft || 0))};
    };
    FS.cropOf = (c) => {
        if (c.kind !== 'video' && c.kind !== 'image') return [0, 0, 0, 0];
        const v = FS.CROP.map((k) => Math.max(0, Math.min(0.95, +FS.val(c, k) || 0)));
        if (v[0] + v[2] > 0.95) { const k = 0.95 / (v[0] + v[2]); v[0] *= k; v[2] *= k; }
        if (v[1] + v[3] > 0.95) { const k = 0.95 / (v[1] + v[3]); v[1] *= k; v[3] *= k; }
        return v;
    };
    FS.val = (clip, key) => (clip[key] == null ? FS.CLIP_DEFAULTS[key] : clip[key]);
    /* colour: brightness, contrast, saturation (CSS's own steps), then warmth (-1 blue … 1 amber) and tint (-1 green …
     * 1 magenta) as gains on red, green and blue; render.py _colour does the same */
    FS.COLOUR = ['brightness', 'contrast', 'saturation', 'warmth', 'tint'];
    FS.gains = (c) => {
        const w = FS.val(c, 'warmth'), m = FS.val(c, 'tint');
        return [(1 + 0.25 * w) * (1 + 0.1 * m), 1 - 0.2 * m, (1 - 0.25 * w) * (1 + 0.1 * m)];
    };
    /* ready-made looks: [brightness, contrast, saturation, warmth, tint] */
    FS.LOOKS = {
        none: [1, 1, 1, 0, 0], warm: [1.03, 1.05, 1.1, 0.5, 0], cool: [1, 1.05, 0.95, -0.5, 0], bw: [1, 1.15, 0, 0, 0],
        vintage: [1.05, 0.85, 0.7, 0.45, 0.15], film: [0.97, 1.15, 0.85, 0.2, -0.1], vivid: [1.02, 1.1, 1.35, 0, 0],
        night: [0.8, 1.1, 0.7, -0.7, 0.1],
    };
    FS.lookOf = (c) => Object.keys(FS.LOOKS).find((k) => FS.COLOUR.every((key, i) => Math.abs(FS.val(c, key) - FS.LOOKS[k][i]) < 1e-3)) || '';
    FS.applyLook = (clipIds, look) => FS.change('look', () => {
        for (const id of clipIds) {
            const c = FS.clip(id);
            if (!c || (c.kind !== 'video' && c.kind !== 'image')) continue;
            FS.COLOUR.forEach((key, i) => { if (FS.LOOKS[look][i] === FS.CLIP_DEFAULTS[key]) delete c[key]; else c[key] = FS.LOOKS[look][i]; });
        }
    });
    FS.end = (clip) => clip.start + clip.duration;

    /* keyframes: clip.keys = {x|y|scale|rotation|opacity: [[seconds from the clip's start, value], ...]} (sorted),
     * clip.keyEase 'smooth' (ease in and out, the default) or 'linear'. render.py computes the same values. */
    FS.KEYED = ['x', 'y', 'scale', 'rotation', 'opacity'];
    FS.SOUND_KEYED = ['volume'];  // keyed too, but only the sound changes (render.py volume_expression)
    FS.keysOf = (clip, key) => (clip && clip.keys && clip.keys[key] && clip.keys[key].length ? clip.keys[key] : null);
    FS.keyed = (clip) => !!clip && FS.KEYED.some((k) => FS.keysOf(clip, k));
    FS.ease = (clip, u) => (clip.keyEase === 'linear' ? u : u * u * (3 - 2 * u));
    FS.at = function (clip, key, t) {
        const ks = FS.keysOf(clip, key);
        if (!ks) return FS.val(clip, key);
        const lt = (t == null ? S.time : t) - clip.start;
        if (lt <= ks[0][0]) return ks[0][1];
        for (let i = 1; i < ks.length; i++) {
            if (lt < ks[i][0]) {
                const [a, va] = ks[i - 1], [b, vb] = ks[i];
                return va + (vb - va) * FS.ease(clip, (lt - a) / Math.max(1e-6, b - a));
            }
        }
        return ks[ks.length - 1][1];
    };
    FS.keyTime = (clip, t) => Math.max(0, Math.min(clip.duration, Math.round(((t == null ? S.time : t) - clip.start) * FS.fps()) / FS.fps()));
    FS.keyIndex = (clip, key, t) => {
        const ks = FS.keysOf(clip, key);
        const lt = FS.keyTime(clip, t);
        return ks ? ks.findIndex((k) => Math.abs(k[0] - lt) < 0.5 / FS.fps()) : -1;
    };
    /* change a value as seen at time t: a keyed value gets (or moves) a key there, any other is set outright */
    FS.setAt = function (clip, key, v, t) {
        const ks = FS.keysOf(clip, key);
        if (!ks) { clip[key] = v; return; }
        const i = FS.keyIndex(clip, key, t);
        if (i >= 0) ks[i][1] = v;
        else { ks.push([FS.keyTime(clip, t), v]); ks.sort((a, b) => a[0] - b[0]); }
    };
    /* the ◆ of a value: a key at the playhead, or none */
    FS.toggleKey = (clipId, key) => FS.change('keyframe', () => {
        const c = FS.clip(clipId);
        const i = FS.keyIndex(c, key);
        if (i >= 0) {
            c.keys[key].splice(i, 1);
            if (!c.keys[key].length) { c[key] = FS.at(c, key); delete c.keys[key]; }
            return;
        }
        const v = FS.at(c, key);
        c.keys = c.keys || {};
        c.keys[key] = c.keys[key] || [];
        c.keys[key].push([FS.keyTime(c), v]);
        c.keys[key].sort((a, b) => a[0] - b[0]);
    });
    /* keys stay on the same moment of the picture when the clip's start in the file moves by `shift` seconds of the
     * edit and its time is stretched by `stretch`; keys that fall outside are dropped, with the value at the edge kept */
    FS.moveKeys = function (clip, shift, stretch) {
        if (!clip.keys) return;
        stretch = stretch || 1;
        for (const k of Object.keys(clip.keys)) {
            const ks = clip.keys[k];
            const edge = (lt) => {
                for (let i = 1; i < ks.length; i++) {
                    if (lt < ks[i][0]) { const [a, va] = ks[i - 1], [b, vb] = ks[i]; return va + (vb - va) * FS.ease(clip, (lt - a) / Math.max(1e-6, b - a)); }
                }
                return lt <= ks[0][0] ? ks[0][1] : ks[ks.length - 1][1];
            };
            const startV = edge(shift), endV = edge(shift + clip.duration * stretch);
            let out = ks.map(([t, v]) => [(t - shift) / stretch, v]).filter(([t]) => t > 1e-6 && t < clip.duration - 1e-6);
            if (ks.length > 1) out = [[0, startV], ...out, [clip.duration, endV]];
            else out = [[0, ks[0][1]]];
            clip.keys[k] = out.filter((x, i) => i === 0 || Math.abs(x[0] - out[i - 1][0]) > 1e-6);
        }
    };
    FS.fps = () => (S.doc && S.doc.settings.fps) || 30;
    FS.snapTime = (t) => Math.max(0, Math.round(t * FS.fps()) / FS.fps());
    FS.total = () => (S.doc ? Math.max(0, ...S.doc.clips.map(FS.end)) : 0);
    FS.clip = (id) => S.doc && S.doc.clips.find((c) => c.id === id);
    FS.track = (id) => S.doc && S.doc.tracks.find((t) => t.id === id);
    /* a transition into clip r: d seconds, at most half of either clip (0 removes it); kind as FS.TRANSITIONS */
    FS.transition = (rightId, seconds, kind) => FS.change(seconds > 0 ? 'transition' : 'no transition', () => {
        const r = FS.clip(rightId);
        const l = r && S.doc.clips.find((c) => c.track === r.track && Math.abs(FS.end(c) - r.start) < 1e-3);
        if (!r) return;
        if (seconds > 0 && l) {
            r.transitionIn = Math.max(1 / FS.fps(), Math.min(seconds, l.duration / 2, r.duration / 2));
            if (kind && kind !== 'dissolve') r.transition = kind; else if (kind) delete r.transition;
        } else { delete r.transitionIn; delete r.transition; }
    });
    FS.dissolve = (rightId, seconds) => FS.transition(rightId, seconds, 'dissolve');
    FS.clipBefore = (c) => S.doc.clips.find((x) => x !== c && x.track === c.track && Math.abs(FS.end(x) - c.start) < 1e-3) || null;
    FS.clipAfter = (c) => S.doc.clips.find((x) => x !== c && x.track === c.track && x.transitionIn > 0 && Math.abs(x.start - FS.end(c)) < 1e-3) || null;
    /* a soft sound at a cut (render.py does the same): the clip's sound crossfades with the sound of the clip before on
     * its track over FS.soundCross(c) seconds - c.soundIn, or as long as its picture transition when unset - with
     * equal-power curves (sin in, cos out), so the loudness does not dip in the middle. The clip before goes on under
     * the new sound from its own file past its end when the file has that much more (and its speed does not ramp);
     * otherwise it fades out over the last seconds before the cut. Only between two clips that both sound. */
    FS.sounds = (c) => !!c && (c.kind === 'audio' || (c.kind === 'video' && !(S.info[c.media] && S.info[c.media].audio === false)))
        && !FS.val(c, 'muted') && !S.doc.tracks.some((tr) => tr.id === c.track && tr.muted);
    FS.soundCross = (c) => {
        const before = FS.clipBefore(c);
        if (!before || !FS.sounds(c) || !FS.sounds(before)) return 0;
        const x = c.soundIn != null ? c.soundIn : (c.transitionIn || 0);
        return Math.max(0, Math.min(x, c.duration, 3));
    };
    FS.soundOut = (c) => {
        const next = S.doc.clips.find((x) => x !== c && x.track === c.track && Math.abs(x.start - FS.end(c)) < 1e-3);
        const x = next ? FS.soundCross(next) : 0;
        if (!x) return {cross: 0, tail: 0};
        const handle = !FS.ramped(c) && FS.room(c) >= c.duration + x - 1e-3;
        return {cross: Math.min(x, c.duration), tail: handle ? x : 0};
    };
    /* the soft cuts' share of the clip's volume at time t (its own fades and keyframes come on top) */
    FS.soundGain = (c, t) => {
        const u = (v) => Math.max(0, Math.min(1, v));
        let g = 1;
        const x = FS.soundCross(c);
        if (x > 0 && t < c.start + x) g *= Math.sin(Math.PI / 2 * u((t - c.start) / x));
        const out = FS.soundOut(c), end = FS.end(c);
        if (out.tail > 0) { if (t >= end) g *= Math.cos(Math.PI / 2 * u((t - end) / out.tail)); }
        else if (out.cross > 0 && t > end - out.cross) g *= Math.cos(Math.PI / 2 * u((t - end + out.cross) / out.cross));
        return g;
    };
    /* the transition into clip r over r.transitionIn seconds (r.transition; a dissolve when unset), as render.py:
     *   dissolve        r fades in over the clip before, which holds its last frame;
     *   black / white   a dip through the colour: up over the first half, the cut at the middle, down over the second;
     *   slide:<side>    r comes in from that side over the clip before (eased in and out);
     *   push:<side>     r comes in from that side and pushes the clip before out the other way. */
    FS.TRANSITIONS = ['dissolve', 'black', 'white', 'slide:right', 'slide:left', 'slide:top', 'slide:bottom',
        'push:right', 'push:left', 'push:top', 'push:bottom'];
    const SIDES = {right: [1, 0], left: [-1, 0], top: [0, -1], bottom: [0, 1]};
    const unit = (r, t) => Math.max(0, Math.min(1, (t - r.start) / r.transitionIn));
    const smooth = (u) => u * u * (3 - 2 * u);
    /* what the transitions into and out of clip c do to it at time t: alpha (a factor), dx / dy (frame widths / heights) */
    FS.transitionLook = function (c, t) {
        const out = {alpha: 1, dx: 0, dy: 0};
        if (c.transitionIn > 0 && t < c.start + c.transitionIn) {
            const [kind, side] = (c.transition || 'dissolve').split(':');
            const u = unit(c, t);
            if (kind === 'dissolve') out.alpha = u;
            else if (kind === 'black' || kind === 'white') out.alpha = u >= 0.5 ? 1 : 0;
            else if (SIDES[side]) { const k = 1 - smooth(u); out.dx = SIDES[side][0] * k; out.dy = SIDES[side][1] * k; }
        }
        const r = FS.clipAfter(c);
        if (r && t >= r.start && (r.transition || '').startsWith('push:')) {
            const [vx, vy] = SIDES[r.transition.split(':')[1]] || [0, 0];
            const k = smooth(unit(r, t));
            out.dx -= vx * k;
            out.dy -= vy * k;
        }
        return out;
    };
    /* the colour laid over everything drawn so far, after clip r, during a dip into it */
    FS.dipAt = function (r, t) {
        const kind = r.transitionIn > 0 && r.transition;
        if ((kind !== 'black' && kind !== 'white') || t < r.start || t >= r.start + r.transitionIn) return null;
        return {colour: kind === 'black' ? '#000000' : '#ffffff', alpha: 1 - Math.abs(2 * unit(r, t) - 1)};
    };
    /* beats of a sound (clip.beats = shown): fetched once a file, timeline times inside the clip */
    FS.beats = {};
    FS.loadBeats = async function (web) {
        if (!FS.beats[web]) FS.beats[web] = await FS.api(`beats?name=${q(S.project)}&web=${q(web)}`);
        return FS.beats[web];
    };
    FS.beatTimes = function (clip) {
        const b = clip && clip.beats && FS.beats[clip.media];
        if (!b) return [];
        const sp = FS.val(clip, 'speed'), cin = FS.val(clip, 'in'), out = [];
        b.beats.forEach((x, i) => {
            const t = clip.start + (x - cin) / sp;
            if (t >= clip.start - 1e-6 && t <= FS.end(clip) + 1e-6) out.push({t, i, one: ((i - b.bar) % 4 + 4) % 4 === 0});
        });
        return out;
    };
    /* the main track re-timed to the music: each shot ends on a beat (every: 0 the nearest beat to where it ends now,
     * 1 / 2 / 4 a shot every beat / two beats / bar, counted from the bar's "one"); a video never runs past its file */
    FS.cutToBeat = function (musicId, every) {
        const m = FS.clip(musicId), b = FS.beats[m.media];
        const all = FS.beatTimes(m);
        if (all.length < 2) throw new Error(FS.T('No beats were found in this sound'));
        const cuts = (every >= 2 ? all.filter((x) => ((x.i - b.bar) % every + every) % every === 0) : all).map((x) => x.t);
        return FS.change('cut to the beat', () => {
            const music = FS.clip(musicId);
            const clips = FS.clipsOn(FS.mainTrack().id).filter((c) => c.id !== musicId);
            let t = clips.length ? clips[0].start : 0, changed = 0;
            for (const c of clips) {
                c.start = t;
                const info = S.info[c.media];
                if (t < FS.end(music) - 1e-3) {
                    const room = c.kind === 'video' && info && info.duration ? (info.duration - FS.val(c, 'in')) / FS.val(c, 'speed') : Infinity;
                    const fits = (x) => x > t + 0.25 && x - t <= room + 1e-6;
                    const ends = cuts.filter(fits);
                    // a video too short for the next bar ends on the last beat it reaches, so the next shot still starts on one
                    const anyBeat = all.map((x) => x.t).filter(fits);
                    if (ends.length || anyBeat.length) {
                        const want = t + c.duration;
                        const end = !ends.length ? anyBeat[anyBeat.length - 1]
                            : every ? ends[0] : ends.reduce((a, x) => (Math.abs(x - want) < Math.abs(a - want) ? x : a));
                        c.duration = end - t;
                        if (c.transitionIn > c.duration / 2) c.transitionIn = c.duration / 2;
                        changed++;
                    }
                }
                t += c.duration;
            }
            return changed;
        });
    };
    /* music lowered under speech (clip.duck = the level it drops to), as duck.py: only where a speech clip really
     * talks, joined across short pauses, down over `attack` seconds and back up over `release` */
    FS.DUCK = {attack: 0.15, release: 0.6};
    FS.talk = {};
    const talkLoading = new Set();
    FS.loadTalk = async function (web) {
        if (!FS.talk[web]) FS.talk[web] = await FS.api(`talk?name=${q(S.project)}&web=${q(web)}`);
        return FS.talk[web];
    };
    FS.isSpeech = (c) => (c.speech != null ? !!c.speech : /(^|\/)voice-[^/]*$/.test(c.media || ''));
    FS.speechClips = function () {
        const muted = new Set(S.doc.tracks.filter((t) => t.muted).map((t) => t.id));
        return S.doc.clips.filter((c) => (c.kind === 'audio' || c.kind === 'video') && c.media && !c.muted && !muted.has(c.track)
            && !(c.duck > 0) && FS.isSpeech(c));
    };
    /* the timeline's speech stretches; a file not heard yet is fetched, and 'talk' is emitted when it arrives */
    FS.duckRanges = function () {
        const found = [];
        for (const c of FS.speechClips()) {
            const r = FS.talk[c.media];
            if (!r) {
                if (!talkLoading.has(c.media)) {
                    talkLoading.add(c.media);
                    FS.loadTalk(c.media).then(() => FS.emit('talk'), () => null).finally(() => talkLoading.delete(c.media));
                }
                continue;
            }
            const sp = FS.val(c, 'speed'), cin = FS.val(c, 'in');
            for (const [a, b] of r) {
                const x = Math.max(c.start, c.start + (a - cin) / sp), y = Math.min(FS.end(c), c.start + (b - cin) / sp);
                if (y > x) found.push([x, y]);
            }
        }
        found.sort((a, b) => a[0] - b[0]);
        const joined = [];
        for (const [a, b] of found) {
            const last = joined[joined.length - 1];
            if (last && a - last[1] < FS.DUCK.attack + FS.DUCK.release) last[1] = Math.max(last[1], b);
            else joined.push([a, b]);
        }
        return joined;
    };
    FS.duckGain = function (clip, t, ranges) {
        const level = clip.duck;
        if (!(level > 0 && level < 1)) return 1;
        const {attack, release} = FS.DUCK;
        let cover = 0;
        for (const [a, b] of ranges || FS.duckRanges()) {
            if (t > a - attack && t < b + release) cover = Math.max(cover, Math.min(1, (t - (a - attack)) / attack, (b + release - t) / release));
        }
        return 1 - (1 - level) * cover;
    };
    FS.clipsOn = (trackId) => S.doc.clips.filter((c) => c.track === trackId).sort((a, b) => a.start - b.start);
    FS.visualTracks = () => S.doc.tracks.filter((t) => t.kind !== 'audio');
    FS.audioTracks = () => S.doc.tracks.filter((t) => t.kind === 'audio');
    FS.mainTrack = () => S.doc.tracks.find((t) => t.main) || FS.visualTracks()[0];
    FS.newId = (prefix) => (prefix || 'c') + Math.random().toString(36).slice(2, 10);

    FS.timecode = function (t, withFrames) {
        t = Math.max(0, t || 0);
        const m = Math.floor(t / 60), s = Math.floor(t % 60), f = Math.floor((t % 1) * FS.fps() + 1e-6);
        const p = (n) => String(n).padStart(2, '0');
        return withFrames === false ? `${p(m)}:${p(s)}` : `${p(m)}:${p(s)}:${p(f)}`;
    };

    /* ---- what the files are ---- */

    FS.probe = async function (web) {
        if (S.info[web]) return S.info[web];
        const info = await FS.api(`probe?name=${q(S.project)}&web=${q(web)}`);
        S.info[web] = info;
        return info;
    };

    /* the length a clip of this file may have from `in` at `speed` (Infinity for pictures and text) */
    FS.room = function (clip) {
        if (clip.kind === 'image' || clip.kind === 'text' || clip.kind === 'gen') return Infinity;
        const info = S.info[clip.media];
        if (!info || !info.duration) return Infinity;
        return Math.max(0, (info.duration - FS.val(clip, 'in')) / FS.val(clip, 'speed'));
    };

    /* ---- undo and redo: every change is made inside FS.change(label, fn) ---- */

    const history = {undo: [], redo: [], limit: 120};
    let changing = 0;  // a change made of other changes (a split inside a freeze frame) is one step to undo
    FS.change = function (label, fn) {
        if (changing) return fn(S.doc);
        const before = JSON.stringify(S.doc);
        let result;
        changing++;
        try { result = fn(S.doc); } finally { changing--; }
        const after = JSON.stringify(S.doc);
        if (before !== after) {
            history.undo.push({label, doc: before});
            if (history.undo.length > history.limit) history.undo.shift();
            history.redo = [];
            changed();
        }
        return result;
    };
    FS.undo = function () {
        const step = history.undo.pop();
        if (!step) return;
        history.redo.push({label: step.label, doc: JSON.stringify(S.doc)});
        S.doc = JSON.parse(step.doc);
        changed(true);
    };
    FS.redo = function () {
        const step = history.redo.pop();
        if (!step) return;
        history.undo.push({label: step.label, doc: JSON.stringify(S.doc)});
        S.doc = JSON.parse(step.doc);
        changed(true);
    };
    /* a drag: the document changes live while the pointer moves, and becomes one undo step when it is let go */
    let pending = null;
    FS.beginEdit = () => { pending = JSON.stringify(S.doc); };
    FS.liveEdit = () => FS.emit('doc', {live: true});
    FS.commitEdit = function (label) {
        if (pending == null) return;
        const before = pending;
        pending = null;
        if (before !== JSON.stringify(S.doc)) {
            history.undo.push({label, doc: before});
            if (history.undo.length > history.limit) history.undo.shift();
            history.redo = [];
            changed();
        }
    };
    FS.canUndo = () => history.undo.length > 0;
    FS.canRedo = () => history.redo.length > 0;

    function changed(structural) {
        S.dirty = true;
        S.selected = S.selected.filter((id) => FS.clip(id));
        FS.emit('doc', {structural: !!structural});
        scheduleSave();
    }
    FS.touched = changed;  // for changes made during a drag, before the drag is committed

    /* ---- projects ---- */

    let saveTimer = null;
    function scheduleSave() {
        clearTimeout(saveTimer);
        saveTimer = setTimeout(() => FS.save().catch((e) => console.warn('Forge Studio save:', e)), 1500);
    }

    FS.save = async function () {
        if (!S.project || !S.doc) return false;
        const text = JSON.stringify(S.doc);
        if (text === S.savedText) return false;
        S.saving = true;
        try {
            await FS.api('project/save', {name: S.project, doc: S.doc});
            S.savedText = text;
            S.dirty = false;
            FS.emit('saved', Date.now());
            return true;
        } finally { S.saving = false; }
    };

    /* the latest open wins: the page reopens the last project when it starts, and a project chosen while that was
     * still loading was replaced by it when it arrived (2026-11-02) */
    let opening = 0;
    FS.openProject = async function (name) {
        const mine = ++opening;
        if (S.project && S.dirty) await FS.save().catch(() => null);
        const p = await FS.api('project?name=' + q(name));
        if (mine !== opening) return;
        S.project = name;
        S.doc = normalise(p.doc);
        S.media = p.media;
        S.info = {};
        S.selected = [];
        S.time = 0;
        S.savedText = JSON.stringify(S.doc);
        history.undo = [];
        history.redo = [];
        await Promise.all(Object.keys(S.media).filter((w) => !S.media[w].missing).map((w) => FS.probe(w).catch(() => null)));
        // clip kinds follow the files (a picture made into a video clip by the first version's projects)
        for (const c of S.doc.clips) {
            const info = S.info[c.media];
            if (info && c.kind !== 'text' && c.kind !== 'gen') c.kind = info.kind === 'image' ? 'image' : info.kind === 'audio' ? 'audio' : 'video';
        }
        try { localStorage.setItem('forgeStudio.project', name); } catch (e) { /* storage off */ }
        FS.emit('project', {name, migrated: p.migrated, missing: Object.keys(S.media).filter((w) => S.media[w].missing)});
        FS.emit('doc', {structural: true});
    };

    FS.newProject = async function (name, settings) {
        await FS.api('project/create', {name});
        await FS.openProject(name);
        if (settings) FS.change('settings', (d) => Object.assign(d.settings, settings));
    };

    FS.defaultProjectName = function () {
        const d = new Date(), p = (n) => String(n).padStart(2, '0');
        return `${FS.T('Project')} ${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}-${p(d.getMinutes())}`;
    };

    function normalise(doc) {
        doc.settings = Object.assign({width: 1080, height: 1920, fps: 30, background: '#000000'}, doc.settings || {});
        doc.tracks = doc.tracks && doc.tracks.length ? doc.tracks : [{id: 't1', kind: 'video', main: true}, {id: 't2', kind: 'audio'}];
        if (!doc.tracks.some((t) => t.main)) (doc.tracks.find((t) => t.kind !== 'audio') || {}).main = true;
        doc.clips = (doc.clips || []).filter((c) => c && c.track && c.duration > 0);
        return doc;
    }

    /* a file of the project's into the edit: `where` {track, time}; pictures get `pictureSeconds` */
    FS.addMedia = async function (web, where, opts) {
        opts = opts || {};
        const info = await FS.probe(web);
        const kind = info.kind === 'image' ? 'image' : info.kind === 'audio' ? 'audio' : 'video';
        const duration = kind === 'image' ? (opts.seconds || 5) : Math.max(1 / FS.fps(), info.duration || 5);
        let trackId = where && where.track;
        return FS.change('add', (d) => {
            if (!trackId || !FS.track(trackId) || (FS.track(trackId).kind === 'audio') !== (kind === 'audio')) {
                trackId = kind === 'audio' ? FS.ensureAudioTrack() : FS.mainTrack().id;
            }
            const time = where && where.time != null ? where.time : S.time;
            const clip = {id: FS.newId(), track: trackId, kind, media: web, start: FS.snapTime(time), duration, in: 0};
            if (opts.extra) Object.assign(clip, opts.extra);
            d.clips.push(clip);
            FS.place(clip, opts.mode || (FS.track(trackId).main ? 'insert' : 'free'));
            S.selected = [clip.id];
            return clip;
        });
    };

    /* ---- tracks ---- */

    FS.ensureAudioTrack = function () {
        const free = FS.audioTracks()[0];
        if (free) return free.id;
        return FS.addTrack('audio');
    };
    FS.addTrack = function (kind, above) {
        const id = FS.newId('t');
        const t = {id, kind};
        if (kind === 'audio') S.doc.tracks.push(t);
        else {
            // visual tracks: a new one goes above `above` (or on top)
            const visual = S.doc.tracks.filter((x) => x.kind !== 'audio');
            const at = above ? S.doc.tracks.indexOf(FS.track(above)) + 1 : S.doc.tracks.indexOf(visual[visual.length - 1]) + 1;
            S.doc.tracks.splice(at, 0, t);
        }
        return id;
    };
    FS.dropEmptyTracks = function () {
        S.doc.tracks = S.doc.tracks.filter((t) => t.main || S.doc.clips.some((c) => c.track === t.id) ||
            (t.kind === 'audio' && t === FS.audioTracks()[0]));
    };

    /* ---- placing clips: the main track has no gaps (like CapCut's main track); others overlap nothing ---- */

    FS.magnet = true;
    FS.place = function (clip, mode) {
        const track = FS.track(clip.track);
        const others = S.doc.clips.filter((c) => c.track === clip.track && c.id !== clip.id).sort((a, b) => a.start - b.start);
        if (track.main && FS.magnet) {
            // inserted at the nearest cut to its start; everything after moves along
            const order = others.slice();
            let index = order.findIndex((c) => clip.start < c.start + c.duration / 2);
            if (index < 0) index = order.length;
            order.splice(index, 0, clip);
            let t = 0;
            for (const c of order) { c.start = t; t += c.duration; }
            return;
        }
        if (mode === 'insert') {
            // pushes what starts at or after it
            for (const c of others) if (c.start >= clip.start - 1e-6) c.start += clip.duration;
            return;
        }
        // free: on the same track it may not cover another clip, so it goes to the first free track instead
        if (others.some((c) => c.start < FS.end(clip) - 1e-6 && FS.end(c) > clip.start + 1e-6)) {
            const kind = track.kind;
            const candidates = S.doc.tracks.filter((t) => t.kind === kind && t.id !== track.id && !t.main);
            const free = candidates.find((t) => !S.doc.clips.some((c) => c.track === t.id && c.start < FS.end(clip) - 1e-6 && FS.end(c) > clip.start + 1e-6));
            clip.track = free ? free.id : FS.addTrack(kind, kind === 'audio' ? null : track.id);
        }
    };

    FS.closeGaps = function () {
        const main = FS.mainTrack();
        if (!main || !FS.magnet) return;
        let t = 0;
        for (const c of FS.clipsOn(main.id)) { c.start = t; t += c.duration; }
    };

    FS.split = function (clipIds, time) {
        return FS.change('split', (d) => {
            const made = [];
            for (const id of clipIds) {
                const c = FS.clip(id);
                if (!c || time <= c.start + 1 / FS.fps() || time >= FS.end(c) - 1 / FS.fps()) continue;
                const first = time - c.start;
                const right = JSON.parse(JSON.stringify(c));
                right.id = FS.newId();
                right.start = time;
                right.duration = c.duration - first;
                right.in = FS.sourceTime(c, time);
                right.fadeIn = 0;
                delete right.transitionIn;
                delete right.transition;
                if (c.keys) {  // moveKeys reads the length each half ends up with
                    FS.moveKeys(right, first);
                    c.duration = first;
                    FS.moveKeys(c, 0);
                }
                c.duration = first;
                c.fadeOut = 0;
                d.clips.push(right);
                made.push(right.id);
            }
            return made;
        });
    };

    /* trimming from the keyboard. Q / W: the clip's start or end trimmed to `time` - a split there that keeps one side,
     * which keeps the clip's own outer edge (its fade, transition in or fade out) - for the picked clips, or the one under
     * the playhead on the main track; on the main track the gap closes (as Delete does). One undo.
     * Returns the kept clips' ids. */
    FS.trimTo = function (edge, time, clipIds) {
        const main = FS.mainTrack();
        let ids = clipIds && clipIds.length ? clipIds.slice()
            : S.doc.clips.filter((c) => main && c.track === main.id && c.start < time && FS.end(c) > time).map((c) => c.id);
        ids = ids.filter((id) => { const c = FS.clip(id); return c && time > c.start + 1 / FS.fps() && time < FS.end(c) - 1 / FS.fps(); });
        if (!ids.length) return [];
        return FS.change(edge === 'start' ? 'trim start' : 'trim end', (d) => {
            const was = {};
            for (const id of ids) { const c = FS.clip(id); was[id] = {fadeIn: c.fadeIn, fadeOut: c.fadeOut, transitionIn: c.transitionIn, transition: c.transition}; }
            const right = FS.split(ids, time);
            const kept = [];
            ids.forEach((id, i) => {
                const left = FS.clip(id), r = FS.clip(right[i]), w = was[id];
                if (edge === 'start') {
                    // the right part stays, with the clip's own way in
                    if (w.fadeIn) r.fadeIn = w.fadeIn;
                    if (w.transitionIn) { r.transitionIn = w.transitionIn; if (w.transition) r.transition = w.transition; }
                    d.clips = d.clips.filter((c) => c !== left);
                    kept.push(r.id);
                } else {
                    if (w.fadeOut) left.fadeOut = w.fadeOut;
                    d.clips = d.clips.filter((c) => c !== r);
                    kept.push(left.id);
                }
            });
            FS.closeGaps();
            S.selected = kept;
            return kept;
        }) || [];
    };
    /* , / . (or Alt+arrows): the picked clips a frame (Shift: a second) earlier or later, each only where it covers
     * nothing on its track; clips on the main track with the magnet on stay (they sit end to end there) */
    FS.nudge = function (clipIds, dt) {
        const main = FS.mainTrack();
        let moved = 0;
        FS.change('nudge', () => {
            for (const id of clipIds) {
                const c = FS.clip(id);
                if (!c || (main && c.track === main.id && FS.magnet)) continue;
                const to = Math.max(0, FS.snapTime(c.start + dt));
                const hit = S.doc.clips.some((x) => x !== c && x.track === c.track && x.start < to + c.duration - 1e-6 && FS.end(x) > to + 1e-6);
                if (hit || Math.abs(to - c.start) < 1e-9) continue;
                c.start = to;
                moved++;
            }
        });
        return moved;
    };
    /* ↑ / ↓: the cut (any clip's start or end) before or after `t` */
    FS.cutNear = function (t, dir) {
        const cuts = [...new Set(S.doc.clips.flatMap((c) => [c.start, FS.end(c)]).map((x) => +x.toFixed(6)))].sort((a, b) => a - b);
        return dir > 0 ? cuts.find((x) => x > t + 1e-4) : [...cuts].reverse().find((x) => x < t - 1e-4);
    };

    /* a freeze frame: the clip split at `time`, the still (`web`, the frame showing there) put between the halves for
     * `seconds` where the clip is and as it looks there, and what comes later on the track pushed along */
    /* speed ramps: keys.speed (the ◆ next to Speed) make the speed move like any keyed value; the clip keeps its length
     * and plays the file seconds the speed adds up to: in + the integral of the speed from the clip's start, summed in
     * steps of 1/240 s (trapezoids) and read off linearly between them. render.py source_span does the same sums. */
    FS.ramped = (c) => !!FS.keysOf(c, 'speed');
    /* a steady speed on a clip (inside FS.change): the same stretch of its file, so the clip gets shorter or longer,
     * its keyframes moved with it; a speed ramp on it gives way */
    FS.applySpeed = function (x, v) {
        v = Math.max(0.1, Math.min(8, +v || 1));
        if (x.keys && x.keys.speed) delete x.keys.speed;
        const was = FS.val(x, 'speed');
        const span = x.duration * was;
        x.speed = v;
        x.duration = Math.max(1 / FS.fps(), span / v);
        if (x.keys) for (const k of Object.keys(x.keys)) x.keys[k] = x.keys[k].map(([t, kv]) => [t * was / v, kv]);
        FS.closeGaps();
    };
    FS.setSpeed = function (ids, v) {
        FS.change('speed', () => { for (const id of ids) { const c = FS.clip(id); if (c) FS.applySpeed(c, v); } });
        FS.message(FS.T('Speed {0}×', +(+v).toFixed(2)));
    };
    const RAMP_STEPS = 240;
    const rampTables = new Map();
    function rampTable(c) {
        const key = JSON.stringify([c.keys.speed, c.duration, c.keyEase || '', FS.val(c, 'speed')]);
        let tab = rampTables.get(c.id);
        if (tab && tab.key === key) return tab;
        const n = Math.max(1, Math.ceil(c.duration * RAMP_STEPS)), h = c.duration / n;
        const sp = (lt) => Math.max(0.01, FS.at(c, 'speed', c.start + lt));
        const sum = new Float64Array(n + 1);
        let prev = sp(0);
        for (let i = 1; i <= n; i++) { const v = sp(i * h); sum[i] = sum[i - 1] + (prev + v) / 2 * h; prev = v; }
        tab = {key, n, h, sum};
        rampTables.set(c.id, tab);
        return tab;
    }
    /* file seconds played from the clip's start to `lt` seconds into it */
    FS.sourceSpan = function (c, lt) {
        lt = Math.max(0, Math.min(c.duration, lt));
        if (!FS.ramped(c)) return lt * FS.val(c, 'speed');
        const {n, h, sum} = rampTable(c);
        const i = Math.min(n - 1, Math.floor(lt / h)), f = lt / h - i;
        return sum[i] + (sum[i + 1] - sum[i]) * f;
    };
    FS.spanOf = (c) => FS.sourceSpan(c, c.duration);
    FS.sourceTime = (c, t) => FS.val(c, 'in') + FS.sourceSpan(c, t - c.start);
    /* ready-made ramps, as [part of the clip, times its speed]; the ramp is scaled down if the file is too short */
    FS.RAMPS = {
        hero: [[0, 2.5], [0.3, 2.5], [0.42, 0.35], [0.7, 0.35], [0.82, 2.5], [1, 2.5]],
        slow: [[0, 1], [0.45, 1], [1, 0.3]],
        fast: [[0, 1], [0.45, 1], [1, 3]],
        flash: [[0, 0.4], [0.38, 0.4], [0.5, 3], [0.62, 0.4], [1, 0.4]],
        montage: [[0, 1.6], [0.25, 0.5], [0.5, 2.4], [0.75, 0.6], [1, 1.8]],
    };
    FS.applyRamp = (clipId, ramp) => FS.change('speed ramp', () => {
        const c = FS.clip(clipId);
        if (!FS.RAMPS[ramp]) { if (c.keys) { delete c.keys.speed; if (!Object.keys(c.keys).length) delete c.keys; } return; }
        const base = FS.val(c, 'speed');
        c.keys = c.keys || {};
        c.keys.speed = FS.RAMPS[ramp].map(([u, k]) => [u * c.duration, Math.max(0.1, Math.min(8, k * base))]);
        const info = S.info[c.media];
        const room = info && info.duration ? info.duration - FS.val(c, 'in') : Infinity;
        const span = FS.spanOf(c);
        if (span > room) c.keys.speed = c.keys.speed.map(([t, v]) => [t, Math.max(0.1, v * room / span * 0.999)]);
    });
    FS.freezeAt = function (clipId, time, web, seconds) {
        return FS.change('freeze frame', () => {
            const c = FS.clip(clipId);
            const fps = FS.fps();
            let at = FS.snapTime(time);
            if (at > c.start + 1 / fps && at < FS.end(c) - 1 / fps) FS.split([c.id], at);
            else at = at <= c.start + 1 / fps ? c.start : FS.end(c);
            for (const x of S.doc.clips) if (x.track === c.track && x.start >= at - 1e-6) x.start += seconds;
            const still = {id: FS.newId(), track: c.track, kind: 'image', media: web, start: at, duration: seconds, in: 0, freeze: true};
            for (const k of FS.KEYED) if (FS.keysOf(c, k) || c[k] != null) still[k] = FS.at(c, k, Math.min(at, FS.end(c)));
            for (const k of FS.COLOUR.concat(FS.CROP, ['mask', 'maskRound', 'maskSoft', 'key', 'keySimilar', 'keySoft', 'keySpill'])) if (c[k] != null) still[k] = c[k];
            S.doc.clips.push(still);
            S.selected = [still.id];
            return still;
        });
    };
    /* backwards: the clip plays a new file, the used part of its own reversed (from the server); again: back to the
     * file and place it came from */
    FS.reverseClip = async function (clipId, onText) {
        const c = FS.clip(clipId);
        if (c.reversed) {
            return FS.change('forwards', () => {
                const x = FS.clip(clipId);
                Object.assign(x, {media: x.reversed.media, in: x.reversed.in});
                delete x.reversed;
            });
        }
        const span = FS.spanOf(c);
        const task = await FS.follow(await FS.api('clip/reverse', {name: S.project, web: c.media, start: FS.val(c, 'in'), seconds: span}), onText);
        S.media[task.web] = {path: task.web, missing: false};
        await FS.probe(task.web);
        return FS.change('backwards', () => {
            const x = FS.clip(clipId);
            if (!x) return;
            x.reversed = {media: x.media, in: FS.val(x, 'in')};
            x.media = task.web;
            x.in = Math.max(0, task.seconds - span);
            // a ramp runs backwards too, so each moment of the picture keeps its speed
            if (FS.keysOf(x, 'speed')) x.keys.speed = x.keys.speed.map(([t, v]) => [x.duration - t, v]).reverse();
        });
    };

    /* a marked part (doc.range = {in, out}): I and O at the playhead; the export can take just that part */
    FS.range = () => {
        const r = S.doc && S.doc.range;
        return r && r.out - r.in >= 1 / FS.fps() ? r : null;
    };
    FS.markRange = (edge, t) => FS.change('marked part', (d) => {
        t = FS.snapTime(t == null ? S.time : t);
        const r = Object.assign({in: 0, out: FS.total()}, d.range || {});
        r[edge] = t;
        if (r.out < r.in) { if (edge === 'in') r.out = FS.total(); else r.in = 0; }
        d.range = r;
    });
    FS.clearRange = () => FS.change('marked part', (d) => { delete d.range; });
    /* a copy of the edit cut to [a, b] and moved to start at 0: each clip trimmed the way a split or a trim does
     * (its place in the file, keyframes, speed ramp and fades kept), transitions from clips left out dropped */
    FS.docInRange = function (doc, a, b) {
        const d = JSON.parse(JSON.stringify(doc));
        const kept = [];
        for (const c of d.clips) {
            const end = c.start + c.duration;
            if (end <= a + 1e-6 || c.start >= b - 1e-6) continue;
            if (c.start < a) {
                const cut = a - c.start;
                if (c.kind === 'video' || c.kind === 'audio') c.in = FS.sourceTime(c, a);
                c.duration -= cut;
                if (c.keys) FS.moveKeys(c, cut);
                c.fadeIn = Math.max(0, FS.val(c, 'fadeIn') - cut);
                c.start = a;
                delete c.transitionIn;
                delete c.transition;
            }
            if (c.start + c.duration > b) {
                const over = c.start + c.duration - b;
                c.duration -= over;
                if (c.keys) FS.moveKeys(c, 0);
                c.fadeOut = Math.max(0, FS.val(c, 'fadeOut') - over);
            }
            c.start -= a;
            kept.push(c);
        }
        d.clips = kept;
        delete d.range;
        delete d.markers;
        return d;
    };

    /* markers: doc.markers = [{t, name?}] by time. M at the playhead adds one, or takes away the one already there */
    FS.markers = () => (S.doc && S.doc.markers) || [];
    FS.markerAt = (t) => FS.markers().findIndex((m) => Math.abs(m.t - t) < 0.5 / FS.fps());
    FS.toggleMarker = (t) => FS.change('marker', (d) => {
        t = FS.snapTime(t == null ? S.time : t);
        d.markers = d.markers || [];
        const i = FS.markerAt(t);
        if (i >= 0) d.markers.splice(i, 1); else { d.markers.push({t}); d.markers.sort((a, b) => a.t - b.t); }
        if (!d.markers.length) delete d.markers;
    });
    FS.renameMarker = (i, name) => FS.change('marker name', (d) => { if (name) d.markers[i].name = name; else delete d.markers[i].name; });
    FS.removeMarker = (i) => FS.change('marker', (d) => { d.markers.splice(i, 1); if (!d.markers.length) delete d.markers; });

    /* a clip's style, copied and pasted onto others (one undo): colour and look, crop and shape, motion (keyframes
     * stretched to the new clip's length, so a zoom across the whole clip stays across the whole clip), volume and
     * lowering under speech, and the transition into it. Each part goes only where it means something. */
    FS.STYLE = {
        colour: FS.COLOUR,
        frame: FS.CROP.concat(['mask', 'maskRound', 'maskSoft', 'key', 'keySimilar', 'keySoft', 'keySpill', 'fill', 'fillBlur']),
        motion: FS.KEYED.concat(['keyEase']),
        sound: ['volume', 'duck'],
        transition: ['transition', 'transitionIn'],
    };
    FS.styleClipboard = null;
    FS.copyStyle = function (clipId) {
        const c = FS.clip(clipId);
        const copy = {kind: c.kind, duration: c.duration, values: {}, keys: {}};
        for (const keys of Object.values(FS.STYLE)) for (const k of keys) if (c[k] != null) copy.values[k] = JSON.parse(JSON.stringify(c[k]));
        for (const k of FS.KEYED.concat(FS.SOUND_KEYED)) if (FS.keysOf(c, k)) copy.keys[k] = JSON.parse(JSON.stringify(c.keys[k]));
        FS.styleClipboard = copy;
        return copy;
    };
    FS.pasteStyle = function (clipIds) {
        const copy = FS.styleClipboard;
        if (!copy) return 0;
        const picture = (x) => x.kind === 'video' || x.kind === 'image';
        const sounds = (x) => x.kind === 'audio' || x.kind === 'video';
        const groups = {colour: picture, frame: picture, motion: (x) => x.kind !== 'audio', sound: sounds, transition: (x) => x.kind !== 'audio' && !!FS.clipBefore(x)};
        return FS.change('paste style', () => {
            let n = 0;
            for (const id of clipIds) {
                const x = FS.clip(id);
                if (!x) continue;
                for (const [group, keys] of Object.entries(FS.STYLE)) {
                    if (!groups[group](x)) continue;
                    for (const k of keys) { if (k in copy.values) x[k] = JSON.parse(JSON.stringify(copy.values[k])); else delete x[k]; }
                }
                const stretch = x.duration / Math.max(1e-6, copy.duration);
                for (const k of FS.KEYED.concat(FS.SOUND_KEYED)) {
                    const fits = FS.SOUND_KEYED.includes(k) ? sounds(x) : x.kind !== 'audio';
                    if (!fits) continue;
                    if (copy.keys[k]) { x.keys = x.keys || {}; x.keys[k] = copy.keys[k].map(([t, v]) => [t * stretch, v]); } else if (x.keys) delete x.keys[k];
                }
                if (x.keys && !Object.keys(x.keys).length) delete x.keys;
                if (x.transitionIn > 0) {
                    const l = FS.clipBefore(x);
                    x.transitionIn = Math.max(1 / FS.fps(), Math.min(x.transitionIn, l.duration / 2, x.duration / 2));
                }
                n++;
            }
            return n;
        });
    };

    /* the sound of a video as a clip of its own on a free sound track below it (same file, place, trim, speed, volume,
     * fades, volume keyframes, lowering and speech); the video goes quiet. One undo. */
    FS.detachAudio = function (clipId) {
        return FS.change('separate the sound', () => {
            const c = FS.clip(clipId);
            const a = c.start, b = FS.end(c);
            let track = FS.audioTracks().find((t) => !S.doc.clips.some((x) => x.track === t.id && x.start < b - 1e-6 && FS.end(x) > a + 1e-6));
            const trackId = track ? track.id : FS.addTrack('audio');
            const sound = {id: FS.newId(), track: trackId, kind: 'audio', media: c.media, start: c.start, duration: c.duration, from: c.id};
            for (const k of ['in', 'speed', 'volume', 'fadeIn', 'fadeOut', 'duck', 'speech']) if (c[k] != null) sound[k] = c[k];
            if (FS.keysOf(c, 'volume')) { sound.keys = {volume: JSON.parse(JSON.stringify(c.keys.volume))}; delete c.keys.volume; if (!Object.keys(c.keys).length) delete c.keys; }
            delete c.duck;
            c.muted = true;
            S.doc.clips.push(sound);
            S.selected = [sound.id];
            return sound;
        });
    };

    /* auto reframe: the clip made to fill the frame, and moved so the subject stays in the middle. `points` are
     * [file second, cx, cy, area] (layers.reframe: where BiRefNet finds it); they are put on the clip's own time,
     * smoothed over a second, read every half second, and turned into x / y keyframes that never show past the
     * picture's edge. One undo; returns how many keyframes. */
    FS.applyReframe = (clipId, points) => FS.change('reframe', () => {
        const c = FS.clip(clipId), info = S.info[c.media];
        const {width: W, height: H} = S.doc.settings;
        if (!info || !info.width || !points.length) return 0;
        const fit = Math.min(W / info.width, H / info.height), cover = Math.max(W / info.width, H / info.height) / fit;
        const dw = info.width * fit * cover, dh = info.height * fit * cover;
        const roomX = Math.max(0, (dw - W) / (2 * W)), roomY = Math.max(0, (dh - H) / (2 * H));
        const sp = FS.val(c, 'speed'), cin = FS.val(c, 'in');
        const pts = points.map(([ft, cx, cy]) => [(ft - cin) / sp, cx, cy]).filter(([t]) => t >= -0.25 && t <= c.duration + 0.25);
        if (!pts.length) return 0;
        // over a second, the same either side (narrower at the ends, so the window does not lean into the clip)
        const first = pts[0][0], last = pts[pts.length - 1][0];
        const smooth = pts.map(([t]) => {
            const half = Math.min(0.5, t - first, last - t) + 1e-6;
            const near = pts.filter(([u]) => Math.abs(u - t) <= half);
            return [t, near.reduce((s, p) => s + p[1], 0) / near.length, near.reduce((s, p) => s + p[2], 0) / near.length];
        });
        const at = (t, i) => {
            if (t <= smooth[0][0]) return smooth[0][i];
            for (let k = 1; k < smooth.length; k++) {
                if (t <= smooth[k][0]) { const [a, b] = [smooth[k - 1], smooth[k]]; return a[i] + (b[i] - a[i]) * (t - a[0]) / Math.max(1e-6, b[0] - a[0]); }
            }
            return smooth[smooth.length - 1][i];
        };
        const times = [];
        for (let t = 0; t < c.duration - 1e-6; t += 0.5) times.push(t);
        times.push(c.duration);
        const keyed = (room, i, size, frame) => times.map((t) => [t, Math.max(-room, Math.min(room, -(at(t, i) - 0.5) * size / frame))]);
        c.scale = cover;
        c.keys = c.keys || {};
        delete c.keys.scale;
        if (roomX > 0) c.keys.x = keyed(roomX, 1, dw, W); else { delete c.keys.x; c.x = 0; }
        if (roomY > 0) c.keys.y = keyed(roomY, 2, dh, H); else { delete c.keys.y; c.y = 0; }
        delete c.keyEase;
        return (c.keys.x || []).length + (c.keys.y || []).length;
    });

    FS.remove = function (clipIds) {
        FS.change('delete', (d) => {
            d.clips = d.clips.filter((c) => !clipIds.includes(c.id));
            FS.closeGaps();
            FS.dropEmptyTracks();
        });
        S.selected = [];
        FS.emit('select', S.selected);
    };

    FS.select = function (ids) {
        S.selected = ids.filter((id) => FS.clip(id));
        FS.emit('select', S.selected);
    };

    FS.seek = function (t) {
        S.time = Math.max(0, Math.min(t, Math.max(FS.total(), 0)));
        FS.emit('time', S.time);
    };

    /* ---- text: drawn the same way by the player, the title previews and the export ---- */

    const hebrew = (s) => /[֐-׿]/.test(s || '');
    FS.TEXT_DEFAULTS = {text: '', sub: '', template: 'center', font: 'Segoe UI', size: 6, color: '#ffffff',
        outline: 0, shadow: true, box: false, boxColor: '#000000', boxAlpha: 0.55, weight: 700,
        shape: 'rect', fill: true, thick: 6, aspect: 1};

    /* a shape (template 'shape'): t.shape rect | round | circle | arrow | line | star | heart, t.size % of H high,
     * t.aspect wide for each high, filled with t.color or drawn as an outline t.thick % of its height; in the middle of
     * the frame (the clip's place, size and turn move it, as for any text) */
    FS.SHAPES = ['rect', 'round', 'circle', 'arrow', 'line', 'star', 'heart'];
    FS.drawShape = function (c, W, H, t) {
        const h = Math.max(2, H * t.size / 100), w = h * Math.max(0.1, t.aspect || 1);
        const cx = W / 2, cy = H / 2, lw = Math.max(1, h * Math.max(0.5, t.thick == null ? 6 : t.thick) / 100);
        const shape = FS.SHAPES.includes(t.shape) ? t.shape : 'rect';
        c.save();
        if (t.shadow) { c.shadowColor = 'rgba(0,0,0,.55)'; c.shadowBlur = h * 0.08; c.shadowOffsetY = h * 0.03; }
        c.fillStyle = c.strokeStyle = t.color;
        c.lineWidth = lw;
        c.lineJoin = 'round';
        c.lineCap = 'round';
        c.beginPath();
        const x0 = cx - w / 2, y0 = cy - h / 2;
        if (shape === 'circle') c.ellipse(cx, cy, w / 2, h / 2, 0, 0, 2 * Math.PI);
        else if (shape === 'round') {
            const r = Math.min(w, h) * 0.22;
            c.moveTo(x0 + r, y0); c.arcTo(x0 + w, y0, x0 + w, y0 + h, r); c.arcTo(x0 + w, y0 + h, x0, y0 + h, r);
            c.arcTo(x0, y0 + h, x0, y0, r); c.arcTo(x0, y0, x0 + w, y0, r); c.closePath();
        } else if (shape === 'arrow') {
            // pointing to the right; turn the clip for any other way
            const head = Math.min(w * 0.45, h * 0.9), sh = h * 0.34;
            c.moveTo(x0, cy - sh / 2); c.lineTo(x0 + w - head, cy - sh / 2); c.lineTo(x0 + w - head, y0); c.lineTo(x0 + w, cy);
            c.lineTo(x0 + w - head, y0 + h); c.lineTo(x0 + w - head, cy + sh / 2); c.lineTo(x0, cy + sh / 2); c.closePath();
        } else if (shape === 'line') { c.moveTo(x0, cy); c.lineTo(x0 + w, cy); }
        else if (shape === 'star') {
            for (let i = 0; i < 10; i++) {
                const a = -Math.PI / 2 + i * Math.PI / 5, r = i % 2 ? 0.4 : 1;
                c.lineTo(cx + Math.cos(a) * w / 2 * r, cy + Math.sin(a) * h / 2 * r * 1.05 + h * 0.03);
            }
            c.closePath();
        } else if (shape === 'heart') {
            c.moveTo(cx, y0 + h * 0.28);
            c.bezierCurveTo(cx, y0, x0, y0, x0, y0 + h * 0.32);
            c.bezierCurveTo(x0, y0 + h * 0.62, cx - w * 0.1, y0 + h * 0.78, cx, y0 + h);
            c.bezierCurveTo(cx + w * 0.1, y0 + h * 0.78, x0 + w, y0 + h * 0.62, x0 + w, y0 + h * 0.32);
            c.bezierCurveTo(x0 + w, y0, cx, y0, cx, y0 + h * 0.28);
            c.closePath();
        } else c.rect(x0, y0, w, h);
        if (shape === 'line' || !t.fill) c.stroke(); else c.fill();
        c.restore();
    };

    /* draws a text clip's look over a W x H canvas context; `t.size` is the big line's height in % of H */
    FS.drawText = function (c, W, H, t, upto) {
        t = Object.assign({}, FS.TEXT_DEFAULTS, t || {});
        if (t.template === 'shape') { FS.drawShape(c, W, H, t); return; }
        const lines = String(t.text || '').split('\n').filter((l) => l.trim());
        const subs = String(t.sub || '').split('\n').filter((l) => l.trim());
        if (t.template === 'card') { c.fillStyle = t.boxColor; c.fillRect(0, 0, W, H); }
        const big = Math.max(4, Math.round(H * t.size / 100 * (t.template === 'signature' ? 0.45 : 1)));
        const small = Math.round(big * 0.55);
        const rtl = hebrew(lines.join('') + subs.join(''));
        c.save();
        c.direction = rtl ? 'rtl' : 'ltr';
        c.textBaseline = 'alphabetic';
        const rows = lines.map((x) => ({x, px: big, weight: t.weight})).concat(subs.map((x) => ({x, px: small, weight: 400})));
        if (!rows.length) { c.restore(); return; }
        const gap = 0.28;
        const font = (r) => {
            c.font = `${r.weight} ${r.px}px "${t.font}", "Segoe UI", sans-serif`;
            if ('letterSpacing' in c) c.letterSpacing = t.template === 'signature' ? `${Math.round(r.px * 0.25)}px` : '0px';
            return c.measureText(r.x).width;
        };
        const widths = rows.map(font);
        const blockW = Math.max(...widths);
        const blockH = rows.reduce((a, r) => a + r.px * (1 + gap), 0) - rows[rows.length - 1].px * gap;
        const margin = W * 0.06;
        let x, align, top;
        switch (t.template) {
            case 'top': align = 'center'; x = W / 2; top = H * 0.08; break;
            case 'lower': align = rtl ? 'right' : 'left'; x = rtl ? W - margin : margin; top = H * 0.78 - blockH; break;
            case 'caption': align = 'center'; x = W / 2; top = H * 0.86 - blockH; break;
            case 'signature': align = rtl ? 'left' : 'right'; x = rtl ? margin : W - margin; top = H * 0.94 - blockH; break;
            default: align = 'center'; x = W / 2; top = (H - blockH) / 2;
        }
        c.textAlign = align;
        if (t.box && t.template !== 'card') {
            const pad = big * 0.42;
            const left = align === 'center' ? x - blockW / 2 : align === 'left' ? x : x - blockW;
            const r = pad * 0.6, bx = left - pad, by = top - pad * 0.7, bw = blockW + 2 * pad, bh = blockH + 1.5 * pad;
            c.save();
            c.globalAlpha *= t.boxAlpha;
            c.fillStyle = t.boxColor;
            c.beginPath();
            c.moveTo(bx + r, by); c.arcTo(bx + bw, by, bx + bw, by + bh, r); c.arcTo(bx + bw, by + bh, bx, by + bh, r);
            c.arcTo(bx, by + bh, bx, by, r); c.arcTo(bx, by, bx + bw, by, r); c.closePath(); c.fill();
            c.restore();
        }
        let y = top, left = upto == null ? Infinity : Math.max(0, upto);
        rows.forEach((r, i) => {
            font(r);
            y += r.px;
            // the typewriter: only the first letters, each where it stands in the whole line (the line's edge fixed)
            let say = r.x, sx = x;
            if (left !== Infinity) {
                const g = FS.graphemes(r.x), show = Math.min(g.length, left);
                left -= show;
                if (!show) { y += r.px * gap; return; }
                if (show < g.length) {
                    say = g.slice(0, show).join('');
                    const edge = align === 'center' ? x - widths[i] / 2 : align === 'left' ? x : x - widths[i];
                    c.textAlign = rtl ? 'right' : 'left';
                    sx = rtl ? edge + widths[i] : edge;
                }
            }
            c.save();
            if (t.shadow) { c.shadowColor = 'rgba(0,0,0,.6)'; c.shadowBlur = r.px * 0.18; c.shadowOffsetY = r.px * 0.06; }
            if (t.outline > 0) {
                c.lineJoin = 'round';
                c.lineWidth = r.px * t.outline / 100 * 2;
                c.strokeStyle = '#000000';
                c.strokeText(say, sx, y);
                c.shadowColor = 'transparent';
            }
            c.fillStyle = t.color;
            c.fillText(say, sx, y);
            c.restore();
            c.textAlign = align;
            y += r.px * gap;
        });
        c.restore();
    };
})();

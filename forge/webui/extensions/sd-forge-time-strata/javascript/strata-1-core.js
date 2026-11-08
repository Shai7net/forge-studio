/* Time Strata - the maths: the spine a chain stands along, where every frame of it stands, the camera's keys,
 * the knife, and the planner that works out how many frames a shot needs. No page and no WebGL here, so it is
 * tested with node (tests/core.test.js).
 *
 * The world: metres, y up, the ground at y = 0. A heading of 0 goes towards -z, a positive heading turns right.
 * A chain's frame k stands at k * spacing along its spine. Every frame faces the spine's start (the way a
 * camera that flies along the spine comes from), so flying forward shows the frames in the order they were
 * filmed: "time as we know it".
 */
(function (root) {
    'use strict';

    /* ---- vectors -------------------------------------------------------------------------------------------- */
    const add = (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
    const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
    const mul = (a, k) => [a[0] * k, a[1] * k, a[2] * k];
    const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
    const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
    const len = a => Math.hypot(a[0], a[1], a[2]);
    const norm = a => { const l = len(a) || 1; return [a[0] / l, a[1] / l, a[2] / l]; };
    const lerp = (a, b, t) => a + (b - a) * t;
    const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
    const smooth = t => { t = clamp(t, 0, 1); return t * t * (3 - 2 * t); };
    const rad = d => d * Math.PI / 180;
    const UP = [0, 1, 0];

    /* v turned by `angle` (radians) about the unit axis k (Rodrigues) */
    function rotate(v, k, angle) {
        const c = Math.cos(angle), s = Math.sin(angle);
        return add(add(mul(v, c), mul(cross(k, v), s)), mul(k, dot(k, v) * (1 - c)));
    }

    const heading = deg => [Math.sin(rad(deg)), 0, -Math.cos(rad(deg))];

    /* ---- the spine ------------------------------------------------------------------------------------------ */

    const SPINE_DEFAULTS = {
        shape: 'arc', x: 0, z: 0, y: 0, heading: 0, length: null, spacing: 0.03, bend: 35, rise: 0, rise_from: 0.35,
        radius: 4, turns: 1, climb: 3, twist: 0, tilt: true, points: [],
        steer: [], steer_scale: 1, steer_smooth: 0,
    };

    /* A steered spine: how the trail turned and climbed at its frames, recorded while steering (keys, a joystick,
     * DeforumationQT) as [frame, turn (degrees per frame, + to the right), climb (degrees of slope, + up)], only
     * where it changed. Between entries it goes from one to the next; before the first and after the last it
     * holds. -> a function frame -> [turn, climb], with the steering strength and smoothing applied. */
    function steering(sp) {
        const list = (sp.steer || []).filter(e => Array.isArray(e) && e.length >= 3 && isFinite(e[0])).slice().sort((a, b) => a[0] - b[0]);
        const scale = sp.steer_scale == null ? 1 : +sp.steer_scale;
        const raw = (k, i) => {
            if (!list.length) return 0;
            if (k <= list[0][0]) return +list[0][i] || 0;
            if (k >= list[list.length - 1][0]) return +list[list.length - 1][i] || 0;
            // (a steered trail has a mark every frame while it is steered: found by halving)
            let lo = 0, hi = list.length - 1;
            while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (list[mid][0] <= k) lo = mid; else hi = mid; }
            const a = list[lo], b = list[hi], f = (k - a[0]) / Math.max(1e-9, b[0] - a[0]);
            return lerp(+a[i] || 0, +b[i] || 0, f);
        };
        const reach = Math.max(0, Math.round(+sp.steer_smooth || 0));
        return k => {
            if (!reach) return [raw(k, 1) * scale, raw(k, 2) * scale];
            let t = 0, c = 0, n = 0;
            for (let o = -reach; o <= reach; o++) { t += raw(k + o, 1); c += raw(k + o, 2); n++; }
            return [t / n * scale, c / n * scale];
        };
    }

    /* Steering marks with only the ones the line between their neighbours does not already say (within
     * `turnTol` degrees a frame and `climbTol` degrees): a mark a frame while steering becomes a few marks
     * where the steering changed. The first and the last mark always stay. */
    function compressSteer(list, turnTol, climbTol) {
        const marks = (list || []).filter(e => Array.isArray(e) && e.length >= 3).slice().sort((a, b) => a[0] - b[0]);
        if (marks.length <= 2) return marks;
        turnTol = turnTol == null ? 0.002 : turnTol; climbTol = climbTol == null ? 0.05 : climbTol;
        const out = [marks[0]];
        let anchor = 0;
        for (let i = 1; i < marks.length - 1; i++) {
            // can every mark from the anchor to the next one be read off the straight line between them?
            const a = marks[anchor], b = marks[i + 1];
            let fits = true;
            for (let j = anchor + 1; j <= i && fits; j++) {
                const f = (marks[j][0] - a[0]) / Math.max(1e-9, b[0] - a[0]);
                fits = Math.abs(lerp(a[1], b[1], f) - marks[j][1]) <= turnTol && Math.abs(lerp(a[2], b[2], f) - marks[j][2]) <= climbTol;
            }
            if (!fits) { out.push(marks[i]); anchor = i; }
        }
        out.push(marks[marks.length - 1]);
        return out;
    }

    /* "18-21, 44 50" -> [18, 19, 20, 21, 44, 50]: frame numbers as a person writes them (ranges with - or –,
     * separated by commas or spaces), sorted, once each, only those below `count` */
    function parseFrames(text, count) {
        const out = new Set();
        for (const part of String(text || '').split(/[\s,;]+/)) {
            const m = part.match(/^(\d+)(?:\s*[-–]\s*(\d+))?$/);
            if (!m) continue;
            let a = +m[1], b = m[2] != null ? +m[2] : a;
            if (b < a) [a, b] = [b, a];
            for (let k = a; k <= Math.min(b, a + 5000); k++) if (count == null || k < count) out.add(k);
        }
        return [...out].sort((x, y) => x - y);
    }

    /* The moment of a world video shown at second `t` of the shot. `world.video_play` 'shot': the video plays once
     * over the whole shot (as long as it is); 'own': at its own speed (`video_rate`), from `video_start`, again
     * and again. Never past its last frame. */
    function worldVideoTime(world, t, shotDuration, videoDuration) {
        const d = +videoDuration || 0;
        if (d <= 0) return 0;
        const last = Math.max(0, d - 0.001);
        if ((world.video_play || 'shot') === 'shot') return clamp(t / Math.max(1e-6, +shotDuration || 1) * d, 0, last);
        const at = t * (world.video_rate == null ? 1 : +world.video_rate) + (+world.video_start || 0);
        return Math.min(last, ((at % d) + d) % d);
    }

    /* the spine's centre line as points every `step` metres of horizontal travel, from a description */
    function centreLine(spine, length, step) {
        const sp = Object.assign({}, SPINE_DEFAULTS, spine);
        const points = [];
        const start = [sp.x, sp.y, sp.z];
        if (sp.shape === 'drawn' && sp.points && sp.points.length >= 2) {
            // the drawn points (x, z[, y]) through a Catmull-Rom curve
            const P = sp.points.map(p => [p[0], p[2] != null ? p[2] : sp.y, p[1]]);
            for (let i = 0; i < P.length - 1; i++) {
                const p0 = P[Math.max(0, i - 1)], p1 = P[i], p2 = P[i + 1], p3 = P[Math.min(P.length - 1, i + 2)];
                const n = Math.max(2, Math.ceil(len(sub(p2, p1)) / step));
                for (let j = 0; j < n; j++) {
                    const t = j / n, t2 = t * t, t3 = t2 * t;
                    points.push([0, 1, 2].map(c => 0.5 * ((2 * p1[c]) + (-p0[c] + p2[c]) * t + (2 * p0[c] - 5 * p1[c] + 4 * p2[c] - p3[c]) * t2 + (-p0[c] + 3 * p1[c] - 3 * p2[c] + p3[c]) * t3)));
                }
            }
            points.push(P[P.length - 1]);
            return points;
        }
        if (sp.shape === 'spiral') {
            // a helix around a vertical axis beside the start: `turns` rounds of `radius`, climbing `climb`
            const around = 2 * Math.PI * sp.radius * sp.turns;
            const n = Math.max(8, Math.ceil(Math.max(length, around) / step));
            const d0 = heading(sp.heading), right = cross(d0, UP);
            const centre = add(start, mul(right, sp.radius));
            for (let i = 0; i <= n; i++) {
                const f = i / n, a = f * Math.max(length, around) / sp.radius;
                const from = mul(right, -sp.radius);
                const flat = add(mul(from, Math.cos(a)), mul(mul(d0, sp.radius), Math.sin(a)));
                points.push([centre[0] + flat[0], sp.y + sp.climb * f * Math.max(1, length / around), centre[2] + flat[2]]);
            }
            return points;
        }
        if (sp.shape === 'steered') {
            // one step a frame, each turned and sloped as it was steered at that frame
            const per = Math.max(1e-3, +sp.spacing || SPINE_DEFAULTS.spacing), steer = steering(sp);
            const frames = Math.max(2, Math.ceil(length / per));
            let p = start.slice(), angle = sp.heading;
            points.push(p.slice());
            for (let k = 1; k <= frames; k++) {
                const [turn, climb] = steer(k);
                angle += turn;
                const d = heading(angle - turn / 2), slope = rad(clamp(climb, -80, 80));
                p = [p[0] + d[0] * per * Math.cos(slope), p[1] + per * Math.sin(slope), p[2] + d[2] * per * Math.cos(slope)];
                points.push(p.slice());
            }
            return points;
        }
        const n = Math.max(2, Math.ceil(length / step));
        let p = start.slice(), angle = sp.heading;
        points.push(p.slice());
        for (let i = 1; i <= n; i++) {
            const f = i / n;
            let turn = 0;
            if (sp.shape === 'arc') turn = sp.bend / n;
            else if (sp.shape === 's') turn = sp.bend * Math.PI / n * Math.sin(2 * Math.PI * f);       // swings `bend` one way, then back
            // 'line' and 'rise' do not turn
            angle += turn;
            const d = heading(angle - turn / 2);
            p = [p[0] + d[0] * step, 0, p[2] + d[2] * step];
            const riseNow = (sp.shape === 'line' && !sp.rise) ? 0 : sp.rise * Math.pow(smooth((f - sp.rise_from) / Math.max(1e-6, 1 - sp.rise_from)), 1.6);
            p[1] = sp.y + riseNow;
            points.push(p.slice());
        }
        return points;
    }

    /* The spine resampled every `ds` metres of its real (3D) length, with a frame at every point that does not
     * twist on its own (rotation-minimising, by double reflection): tangent T, up U, right R = T x U.
     * Built long enough for `length` metres; beyond its ends a point goes on straight along the end's tangent. */
    function spinePath(spine, length, ds) {
        const sp = Object.assign({}, SPINE_DEFAULTS, spine);
        ds = ds || 0.01;
        length = Math.max(ds * 4, length || 1);
        let line = centreLine(sp, length, Math.min(0.05, length / 400));
        // its real length, then points every ds of it
        const cum = [0];
        for (let i = 1; i < line.length; i++) cum.push(cum[i - 1] + len(sub(line[i], line[i - 1])));
        const total = cum[cum.length - 1];
        const count = Math.max(2, Math.floor(total / ds) + 1);
        const P = new Float64Array(count * 3);
        let j = 0;
        for (let i = 0; i < count; i++) {
            const s = i * ds;
            while (j < cum.length - 2 && cum[j + 1] < s) j++;
            const f = clamp((s - cum[j]) / Math.max(1e-9, cum[j + 1] - cum[j]), 0, 1);
            for (let c = 0; c < 3; c++) P[i * 3 + c] = lerp(line[j][c], line[j + 1][c], f);
        }
        const T = new Float64Array(count * 3), U = new Float64Array(count * 3), R = new Float64Array(count * 3);
        const at = (A, i) => [A[i * 3], A[i * 3 + 1], A[i * 3 + 2]];
        const put = (A, i, v) => { A[i * 3] = v[0]; A[i * 3 + 1] = v[1]; A[i * 3 + 2] = v[2]; };
        for (let i = 0; i < count; i++) {
            const a = at(P, Math.max(0, i - 1)), b = at(P, Math.min(count - 1, i + 1));
            put(T, i, norm(sub(b, a)));
        }
        // the first up: the world's up, made square to the tangent (for a vertical start: the heading's back)
        let t0 = at(T, 0), u0 = sub(UP, mul(t0, dot(UP, t0)));
        if (len(u0) < 1e-3) u0 = mul(heading(sp.heading), -1);
        put(U, 0, norm(u0));
        for (let i = 0; i < count - 1; i++) {
            const x0 = at(P, i), x1 = at(P, i + 1), ti = at(T, i), ui = at(U, i), tn = at(T, i + 1);
            const v1 = sub(x1, x0), c1 = dot(v1, v1);
            if (c1 < 1e-12) { put(U, i + 1, ui); continue; }
            const uL = sub(ui, mul(v1, 2 / c1 * dot(v1, ui))), tL = sub(ti, mul(v1, 2 / c1 * dot(v1, ti)));
            const v2 = sub(tn, tL), c2 = dot(v2, v2);
            put(U, i + 1, norm(c2 < 1e-12 ? uL : sub(uL, mul(v2, 2 / c2 * dot(v2, uL)))));
        }
        for (let i = 0; i < count; i++) {
            let t = at(T, i), u = at(U, i);
            if (!sp.tilt) {
                // upright frames: the card stands straight up, facing along the spine's direction on the ground
                const flat = [t[0], 0, t[2]];
                t = len(flat) > 1e-6 ? norm(flat) : heading(sp.heading);
                u = UP.slice();
            }
            if (sp.twist) u = rotate(u, t, rad(sp.twist) * (i * ds) / length);
            put(T, i, t); put(U, i, u); put(R, i, norm(cross(t, u)));
        }
        return { P, T, U, R, count, ds, length: (count - 1) * ds, spine: sp };
    }

    /* the point of a path at s metres (beyond the ends: straight on along the end's tangent) */
    function pointAt(path, s) {
        const last = path.count - 1;
        const x = s / path.ds;
        const at = (A, i) => [A[i * 3], A[i * 3 + 1], A[i * 3 + 2]];
        if (x <= 0 || x >= last) {
            const i = x <= 0 ? 0 : last, over = x <= 0 ? s : s - path.length;
            const t = at(path.T, i);
            return { p: add(at(path.P, i), mul(t, over)), t, u: at(path.U, i), r: at(path.R, i) };
        }
        const i = Math.floor(x), f = x - i;
        const mix = A => { const a = at(A, i), b = at(A, i + 1); return [lerp(a[0], b[0], f), lerp(a[1], b[1], f), lerp(a[2], b[2], f)]; };
        const t = norm(mix(path.T)), u0 = mix(path.U);
        const u = norm(sub(u0, mul(t, dot(u0, t))));
        return { p: mix(path.P), t, u, r: norm(cross(t, u)) };
    }

    /* ---- a chain -------------------------------------------------------------------------------------------- */

    const CHAIN_DEFAULTS = {
        size: 1.7,              // the figure's height in metres
        figure: 0.84,           // the share of a frame's height the figure takes (the cut measured it)
        baseline: 0.95,         // where the feet are, from the frame's top
        aspect: 0.66,           // frame width / height
        from: 0, to: null,      // which frames are shown
        offset: 0,              // metres along the spine before the first frame
    };

    /* the size of one frame (card) in metres, and how far below the card's bottom edge the ground is */
    function cardSize(chain) {
        const c = Object.assign({}, CHAIN_DEFAULTS, chain);
        const height = c.size / Math.max(0.05, c.figure);
        return { width: height * c.aspect, height, feet: height * (1 - c.baseline) };
    }

    /* every shown frame of a chain: where its card stands and which way (for the renderer)
     * -> [{k, s, bottom, right, up, normal}] where right/up are the card's half-width and full-height vectors.
     * A chain whose `faces` is 'end' is seen the right way round from beyond its last frame (the past behind
     * a present that grows), otherwise from before its first. */
    function chainCards(chain, path, frames) {
        const c = Object.assign({}, CHAIN_DEFAULTS, chain);
        const spacing = (chain.spine && chain.spine.spacing) || SPINE_DEFAULTS.spacing;
        const size = cardSize(c);
        const first = Math.max(0, c.from | 0), last = Math.min(frames - 1, c.to == null ? frames - 1 : c.to | 0);
        const flip = c.faces === 'end' ? -1 : 1;
        const out = [];
        for (let k = first; k <= last; k++) {
            const s = c.offset + (k - first) * spacing;
            const f = pointAt(path, s);
            // the feet stand on the spine: the card's bottom edge is `feet` below it
            const bottom = sub(f.p, mul(f.u, size.feet));
            out.push({ k, s, bottom, right: mul(f.r, flip * size.width / 2), up: mul(f.u, size.height), normal: f.t });
        }
        return out;
    }

    /* ---- time along a chain ----------------------------------------------------------------------------------
     * 'all': every frame stands there all the time (a sculpture of the whole clip).
     * 'future': the present eats the chain from its first frame: what has passed is gone, the present plays at
     *           `rate` frames a second and the frames still to come wait behind it (the reel's opening).
     * 'past': the chain grows: the present is its newest frame and what has been trails behind it. */

    const TIME_DEFAULTS = { mode: 'all', start: 0, rate: null, fade: 1, hold: true };

    /* the present at time t, as a (fractional) frame number, or null when every frame is there; with `hold` it
     * stops on the chain's `last` frame instead of running off its end (and the last frame stays) */
    function presentAt(time, t, fps, last) {
        const T = Object.assign({}, TIME_DEFAULTS, time);
        if (T.mode !== 'future' && T.mode !== 'past') return null;
        const now = (+T.start || 0) + (+T.rate || fps || 24) * t;
        return T.hold && last != null ? Math.min(now, last) : now;
    }

    /* how much of frame k is there when the present is `now` (0: gone or not yet, 1: wholly) */
    function presence(time, k, now) {
        if (now == null) return 1;
        const fade = Math.max(1e-3, +(time && time.fade) || 1);
        if (time.mode === 'future') return clamp((k - now) / fade + 1, 0, 1);
        return clamp((now - k) / fade + 1, 0, 1);
    }

    /* where the present stands along the spine, in metres */
    function presentS(chain, now) {
        const c = Object.assign({}, CHAIN_DEFAULTS, chain);
        const spacing = (chain.spine && chain.spine.spacing) || SPINE_DEFAULTS.spacing;
        return c.offset + (now - Math.max(0, c.from | 0)) * spacing;
    }

    function chainLength(chain, frames) {
        const spacing = (chain.spine && chain.spine.spacing) || SPINE_DEFAULTS.spacing;
        const c = Object.assign({}, CHAIN_DEFAULTS, chain);
        const last = Math.min(frames - 1, c.to == null ? frames - 1 : c.to | 0);
        return c.offset + Math.max(0, last - Math.max(0, c.from | 0)) * spacing;
    }

    /* ---- keys over time --------------------------------------------------------------------------------------- */

    /* Monotone cubic interpolation (Fritsch-Carlson) of one field of time-ordered keys: it never overshoots,
     * so a camera that moves forward between two keys never backs up. */
    function track(keys, field, t, fallback) {
        const ks = keys.filter(k => k[field] != null && isFinite(k[field])).sort((a, b) => a.t - b.t);
        if (!ks.length) return fallback;
        if (ks.length === 1 || t <= ks[0].t) return ks[0][field];
        if (t >= ks[ks.length - 1].t) return ks[ks.length - 1][field];
        const n = ks.length, x = ks.map(k => k.t), y = ks.map(k => k[field]);
        const d = [], m = new Array(n);
        for (let i = 0; i < n - 1; i++) d.push((y[i + 1] - y[i]) / Math.max(1e-9, x[i + 1] - x[i]));
        m[0] = d[0]; m[n - 1] = d[n - 2];
        for (let i = 1; i < n - 1; i++) m[i] = d[i - 1] * d[i] <= 0 ? 0 : (d[i - 1] + d[i]) / 2;
        for (let i = 0; i < n - 1; i++) {
            if (d[i] === 0) { m[i] = 0; m[i + 1] = 0; continue; }
            const a = m[i] / d[i], b = m[i + 1] / d[i];
            const h = a * a + b * b;
            if (h > 9) { const tau = 3 / Math.sqrt(h); m[i] = tau * a * d[i]; m[i + 1] = tau * b * d[i]; }
        }
        let i = 0;
        while (i < n - 2 && t > x[i + 1]) i++;
        const hh = x[i + 1] - x[i], f = (t - x[i]) / hh, f2 = f * f, f3 = f2 * f;
        // a key marked 'hold' keeps its values until the next key
        if (ks[i].hold) return y[i];
        return (2 * f3 - 3 * f2 + 1) * y[i] + (f3 - 2 * f2 + f) * hh * m[i] + (-2 * f3 + 3 * f2) * y[i + 1] + (f3 - f2) * hh * m[i + 1];
    }

    const CAMERA_FIELDS = { s: 0, side: 0, up: 1.1, look: 4, look_up: 1.0, look_side: 0, fov: 50, roll: 0 };

    /* the camera at time t: {eye, target, up, fov} from keys relative to a chain's spine; `shift` (metres) is
     * added to every key's place along it (a camera that rides with the chain's present) */
    function cameraAt(keys, path, t, shift) {
        const v = {};
        for (const [field, fallback] of Object.entries(CAMERA_FIELDS)) v[field] = track(keys, field, t, fallback);
        v.s += shift || 0;
        const here = pointAt(path, v.s);
        const eye = add(add(here.p, mul(here.r, v.side)), mul(here.u, v.up));
        const there = pointAt(path, v.s + v.look);
        let target = add(add(there.p, mul(there.r, v.look_side)), mul(there.u, v.look_up));
        if (len(sub(target, eye)) < 1e-4) target = add(eye, here.t);
        const forward = norm(sub(target, eye));
        let up = sub(here.u, mul(forward, dot(here.u, forward)));
        if (len(up) < 1e-4) up = sub(UP, mul(forward, dot(UP, forward)));
        up = rotate(norm(up), forward, rad(v.roll));
        return { eye, target, up, fov: v.fov, forward, values: v };
    }

    /* ---- the follow camera -----------------------------------------------------------------------------------
     * A camera that keeps a figure on a trail in view the way a camera operator would, set by what the picture
     * should show instead of by coordinates:
     *   yaw        the angle around the figure, degrees: 0 in front of its face, 90 at its left, 180 behind it
     *   elevation  degrees above the figure's own horizon
     *   fill       how much of the picture's height the figure takes (0.55: from knees-ish room to full body)
     *   aim        where on the figure the camera looks, as a share of its height from the feet
     *   lead       metres along the trail's way on that the camera looks past the figure (shows where it goes)
     *   smooth     metres of trail whose direction is averaged, so a sharp turn becomes a calm swing
     *   along      where on the chain the figure is when the chain has no present (metres)
     * The distance follows from the lens and the figure's size, so the proportions stay right whatever the lens. */
    const FOLLOW_FIELDS = { yaw: 20, elevation: 6, fill: 0.55, aim: 0.55, lead: 0.6, smooth: 1.5, along: 0, fov: 50, roll: 0 };

    function followAt(keys, path, t, place) {
        const v = {};
        for (const [field, fallback] of Object.entries(FOLLOW_FIELDS)) v[field] = track(keys, field, t, fallback);
        const s = place.s != null ? place.s : v.along;
        const size = place.size || 1.7, faces = place.faces === 'end' ? 1 : -1;
        const here = pointAt(path, s);
        // the trail's direction around the figure, averaged over `smooth` metres (mostly behind it, a little ahead)
        // (sampled every 5 cm with a triangular weight: a corner passing a coarse sample made the camera lurch)
        let avg = [0, 0, 0];
        const reach = Math.max(0, v.smooth), span = reach * 1.5;
        const steps = Math.max(2, Math.min(240, Math.ceil(span / 0.05)));
        for (let i = 0; i <= steps; i++) {
            const f = i / steps, weight = 1 - Math.abs(2 * f - 1) + 1e-3;
            avg = add(avg, mul(pointAt(path, s - reach + span * f).t, weight));
        }
        const way = len(avg) > 1e-6 ? norm(avg) : here.t;
        // the figure's own up (it leans with the spine), made square to that direction
        let up = sub(here.u, mul(way, dot(here.u, way)));
        up = len(up) > 1e-4 ? norm(up) : UP;
        const aimPoint = add(here.p, mul(up, size * (v.aim - 0.05)));            // (the feet are a little below the spine point)
        // from the figure towards the camera: its face's direction, turned by yaw about its up and raised by elevation
        const face = mul(way, faces);
        let toward = rotate(face, up, rad(v.yaw));
        const side = norm(cross(toward, up));
        toward = rotate(toward, side, rad(v.elevation));
        const distance = size / (Math.max(0.05, v.fill) * 2 * Math.tan(rad(v.fov) / 2));
        const eye = add(aimPoint, mul(toward, distance));
        let target = add(aimPoint, mul(way, -faces * v.lead));                    // past the figure, where its trail goes
        // looking ahead never takes the figure out of the picture: seen from an angle (and in an upright picture,
        // whose width is narrow) the look ahead is shortened so the figure stays within the inner 40% of the
        // narrower half of the picture
        const half = Math.atan(Math.tan(rad(v.fov) / 2) * Math.min(1, place.aspect || 1));
        const toFigure = norm(sub(aimPoint, eye)), toTarget = norm(sub(target, eye));
        const apart = Math.acos(clamp(dot(toFigure, toTarget), -1, 1)), most = 0.4 * half;
        if (apart > most) {
            // the point along the same line whose direction from the eye is `most` away from the figure's
            let lo = 0, hi = 1;
            for (let i = 0; i < 24; i++) {
                const mid = (lo + hi) / 2, p = add(aimPoint, mul(sub(target, aimPoint), mid));
                if (Math.acos(clamp(dot(toFigure, norm(sub(p, eye))), -1, 1)) > most) hi = mid; else lo = mid;
            }
            target = add(aimPoint, mul(sub(target, aimPoint), lo));
        }
        const forward = norm(sub(target, eye));
        let camUp = sub(up, mul(forward, dot(up, forward)));
        if (len(camUp) < 1e-4) camUp = sub(UP, mul(forward, dot(UP, forward)));
        camUp = rotate(norm(camUp), forward, rad(v.roll));
        return { eye, target, up: camUp, fov: v.fov, forward, values: v, distance };
    }

    /* follow keys a ready-made move gives, over `duration` seconds */
    function followPreset(name, duration) {
        const D = duration;
        switch (name) {
            case 'side': return [{ t: 0, yaw: 80, elevation: 4, fill: 0.5, aim: 0.55, lead: 0.4, smooth: 1.5, fov: 45 }];
            case 'behind': return [{ t: 0, yaw: 160, elevation: 12, fill: 0.45, aim: 0.6, lead: 1.5, smooth: 2, fov: 55 }];
            case 'close': return [{ t: 0, yaw: 15, elevation: 3, fill: 0.85, aim: 0.65, lead: 0.2, smooth: 1, fov: 40 }];
            case 'orbit': return [0, 0.25, 0.5, 0.75, 1].map((f, i) => ({ t: D * f, yaw: 20 + 360 * f, elevation: 6 + 6 * Math.sin(i), fill: 0.5, aim: 0.55, lead: 0.5, smooth: 1.5, fov: 48 }));
            case 'reveal': return [{ t: 0, yaw: 10, elevation: 4, fill: 0.8, aim: 0.6, lead: 0.3, smooth: 1.5, fov: 45 },
                                   { t: D, yaw: 55, elevation: 35, fill: 0.22, aim: 0.5, lead: 2.5, smooth: 3, fov: 55 }];
            case 'front':
            default: return [{ t: 0, yaw: 20, elevation: 6, fill: 0.55, aim: 0.55, lead: 0.6, smooth: 1.5, fov: 50 }];
        }
    }

    /* the place on a path nearest to a point: s (searched coarsely, then finely) */
    function nearestS(path, point, from, to) {
        from = from == null ? -10 : from; to = to == null ? path.length + 10 : to;
        let best = from, bestD = Infinity;
        for (let pass = 0, step = Math.max(0.05, (to - from) / 400); pass < 3; pass++, step /= 20) {
            const a = Math.max(from, best - step * 20), b = Math.min(to, best + step * 20);
            for (let s = pass ? a : from; s <= (pass ? b : to); s += step) {
                const q = pointAt(path, s).p, d = (q[0] - point[0]) ** 2 + (q[1] - point[1]) ** 2 + (q[2] - point[2]) ** 2;
                if (d < bestD) { bestD = d; best = s; }
            }
        }
        return best;
    }

    /* a camera key (relative to a spine) that gives the view from `eye` towards `target` */
    function keyFromView(path, eye, target, fov, shift) {
        const key = keyFromViewAbsolute(path, eye, target, fov);
        key.s -= shift || 0;
        return key;
    }

    function keyFromViewAbsolute(path, eye, target, fov) {
        const s = nearestS(path, eye);
        const here = pointAt(path, s);
        const rel = sub(eye, here.p);
        const forward = norm(sub(target, eye));
        // a point 4 m along the view; the spine's nearest place to it sees it square, so it is told exactly
        // by a sideways and an upward offset there
        const aim = add(eye, mul(forward, 4));
        const st = nearestS(path, aim);
        const there = pointAt(path, st);
        const relT = sub(aim, there.p);
        return { s, side: dot(rel, here.r), up: dot(rel, here.u), look: st - s, look_side: dot(relT, there.r), look_up: dot(relT, there.u), fov: fov || 50, roll: 0 };
    }

    /* the knife at time t: a plane that cuts away whatever of the chain is on its near side
     * -> {point, normal} (normal points to what is kept), or null */
    function knifeAt(knife, path, t) {
        if (!knife || !knife.on || !(knife.keys || []).length) return null;
        const s = track(knife.keys, 's', t, 0), yaw = track(knife.keys, 'yaw', t, 0), pitch = track(knife.keys, 'pitch', t, 0);
        const here = pointAt(path, s);
        let n = rotate(here.t, here.u, rad(yaw));
        n = rotate(n, norm(cross(n, here.u)), rad(pitch));
        return { point: here.p, normal: norm(n) };
    }

    /* ---- the camera's ready-made moves ---------------------------------------------------------------------- */

    /* keys for a move of `duration` seconds along a chain `length` metres long whose figure is `size` tall.
     * 'opening' rides with the present (its places are measured from it, `dir` -1 when the chain trails behind
     * a growing present); the others stand along the chain itself. */
    const RIDING = new Set(['opening']);

    function preset(name, length, duration, size, dir) {
        const D = duration, L = Math.max(0.5, length), H = size || 1.7;
        const chest = 0.62 * H, eye = 0.92 * H, w = dir === -1 ? -1 : 1;
        switch (name) {
            case 'through':         // fly through the whole chain at an even pace: the frames pass one by one
                return [{ t: 0, s: -1.2, side: 0, up: chest, look: 4, look_up: chest, look_side: 0, fov: 50 },
                        { t: D, s: L + 0.3, side: 0, up: chest, look: 4, look_up: chest, look_side: 0, fov: 50 }];
            case 'side':            // walk beside the chain: the stack seen as one body through time
                return [{ t: 0, s: -0.5, side: 2.6, up: 1.0, look: 1.2, look_up: chest, look_side: 0, fov: 45 },
                        { t: D, s: L * 0.75, side: 2.6, up: 1.0, look: 1.2, look_up: chest, look_side: 0, fov: 45 }];
            case 'front':           // stand before the first frame and come slowly closer
                return [{ t: 0, s: -6, side: 0.4, up: eye, look: 8, look_up: chest, look_side: 0, fov: 40 },
                        { t: D, s: -1.4, side: 0.1, up: chest + 0.1, look: 6, look_up: chest, look_side: 0, fov: 45 }];
            case 'orbit': {         // go round the middle of the chain
                const keys = [], m = L / 2, r = Math.max(3, L * 0.45);
                for (let i = 0; i <= 8; i++) {
                    const a = i / 8 * 2 * Math.PI;
                    keys.push({ t: D * i / 8, s: m - Math.cos(a) * r, side: Math.sin(a) * r, up: 1.4, look: Math.cos(a) * r, look_up: chest, look_side: -Math.sin(a) * r, fov: 45 });
                }
                return keys;
            }
            case 'opening':         // the reel's opening, riding with the present: beside the figure with the chain
            default:                // running off behind it, round to its front, into the frames to come, up to the sky
                return [{ t: 0, s: -0.7 * w, side: 1.35, up: 0.75, look: 4.5 * w, look_up: 1.0, look_side: -0.35, fov: 55 },
                        { t: D * 0.22, s: -1.7 * w, side: 0.9, up: 0.95, look: 3.4 * w, look_up: chest, look_side: -0.1, fov: 50 },
                        { t: D * 0.42, s: -2.9 * w, side: 0.0, up: chest, look: 2.9 * w, look_up: chest, look_side: 0, fov: 45 },
                        { t: D * 0.6, s: -1.1 * w, side: 0.0, up: chest, look: 4 * w, look_up: chest, look_side: 0, fov: 48 },
                        { t: D * 0.8, s: 1.6 * w, side: 0.15, up: chest + 0.1, look: 4 * w, look_up: chest, look_side: 0.25, fov: 50 },
                        { t: D, s: 3.4 * w, side: 0.3, up: 2.8, look: 5 * w, look_up: 1.5, look_side: 0, fov: 60 }];
        }
    }

    /* ---- the planner ---------------------------------------------------------------------------------------- */

    /* What a fly-through needs: `seconds` of flying at `speed` m/s while `rate` frames pass the lens each
     * second, seen by a camera of vertical `fov` degrees recording `height` pixels high, whose frames dissolve
     * at `near` metres. -> {frames, spacing, length, texture}: how many frames to make, how far apart they
     * stand, how long the chain is, and how many pixels high a frame must be to be sharp when it is nearest. */
    function plan(options) {
        const o = Object.assign({ seconds: 6, speed: 1.2, rate: 24, ahead: 6, size: 1.7, figure: 0.84, fov: 50, height: 1920, near: 0.35 }, options);
        const spacing = o.speed / Math.max(0.1, o.rate);
        const passed = Math.ceil(o.seconds * o.rate - 1e-9);
        const ahead = Math.ceil(o.ahead / spacing - 1e-9);    // the chain must go on beyond where the camera stops
        const frames = passed + ahead;
        const card = o.size / o.figure;
        const pixels = card / (2 * o.near * Math.tan(rad(o.fov) / 2)) * o.height;
        return { frames, spacing, length: frames * spacing, passed, ahead, texture: Math.round(pixels), card };
    }

    /* how many frames pass the lens each second while the camera moves along keys (a list over the shot) */
    function passRates(keys, spacing, duration, steps) {
        steps = steps || 60;
        const out = [];
        for (let i = 0; i < steps; i++) {
            const t0 = duration * i / steps, t1 = duration * (i + 1) / steps;
            const ds = track(keys, 's', t1, 0) - track(keys, 's', t0, 0);
            out.push(ds / Math.max(1e-6, t1 - t0) / Math.max(1e-6, spacing));
        }
        return out;
    }

    /* ---- the camera as matrices (column-major, for WebGL) --------------------------------------------------- */

    function lookAt(eye, target, up) {
        const f = norm(sub(target, eye)), s = norm(cross(f, up)), u = cross(s, f);
        return [s[0], u[0], -f[0], 0, s[1], u[1], -f[1], 0, s[2], u[2], -f[2], 0,
                -dot(s, eye), -dot(u, eye), dot(f, eye), 1];
    }

    function perspective(fovDeg, aspect, near, far) {
        const f = 1 / Math.tan(rad(fovDeg) / 2), nf = 1 / (near - far);
        return [f / aspect, 0, 0, 0, 0, f, 0, 0, 0, 0, (far + near) * nf, -1, 0, 0, 2 * far * near * nf, 0];
    }

    function multiply(a, b) {
        const out = new Array(16);
        for (let c = 0; c < 4; c++) for (let r = 0; r < 4; r++) {
            let v = 0;
            for (let k = 0; k < 4; k++) v += a[k * 4 + r] * b[c * 4 + k];
            out[c * 4 + r] = v;
        }
        return out;
    }

    const Core = {
        add, sub, mul, dot, cross, len, norm, lerp, clamp, smooth, rad, rotate, heading, UP,
        SPINE_DEFAULTS, CHAIN_DEFAULTS, CAMERA_FIELDS, TIME_DEFAULTS, RIDING,
        centreLine, spinePath, pointAt, cardSize, chainCards, chainLength, presentAt, presence, presentS,
        track, cameraAt, nearestS, keyFromView, knifeAt, preset, plan, passRates, lookAt, perspective, multiply,
        steering, compressSteer, parseFrames, worldVideoTime, FOLLOW_FIELDS, followAt, followPreset,
    };
    if (typeof module !== 'undefined' && module.exports) module.exports = Core;
    else root.StrataCore = Core;
})(typeof window !== 'undefined' ? window : globalThis);

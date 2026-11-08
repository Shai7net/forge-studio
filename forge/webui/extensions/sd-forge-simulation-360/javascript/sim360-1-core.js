/* Simulation 360 - the arithmetic, with no page and no WebGL in it, so it can be tested with node.
 *
 * Directions. A panorama's pixel (u, v), u from the left edge and v from the top, both 0..1, looks towards
 *     x =  cos(lat) sin(lon)      lon = (u - 0.5) * 360 degrees   (the middle of the picture is "ahead")
 *     y =  sin(lat)               lat = (0.5 - v) * 180 degrees   (the top of the picture is "up")
 *     z = -cos(lat) cos(lon)
 * so ahead is -z, right is +x, up is +y. A camera's yaw turns it to the right, its pitch tips it up;
 * every angle the rest of the program sees is in degrees.
 */
(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    else root.Sim360Core = api;
})(typeof self !== 'undefined' ? self : this, function () {
    'use strict';

    const RAD = Math.PI / 180;
    const clamp = (x, low, high) => Math.min(high, Math.max(low, x));
    const lerp = (a, b, t) => a + (b - a) * t;
    const smooth = t => { t = clamp(t, 0, 1); return t * t * (3 - 2 * t); };
    const wrapDeg = a => ((a + 180) % 360 + 360) % 360 - 180;
    /* the short way round from angle a to angle b */
    const turn = (a, b) => wrapDeg(b - a);

    function dirFromUv(u, v) {
        const lon = (u - 0.5) * 2 * Math.PI, lat = (0.5 - v) * Math.PI, c = Math.cos(lat);
        return [c * Math.sin(lon), Math.sin(lat), -c * Math.cos(lon)];
    }

    function uvFromDir(x, y, z) {
        const length = Math.hypot(x, y, z) || 1;
        let u = Math.atan2(x, -z) / (2 * Math.PI) + 0.5;
        if (u >= 1) u -= 1;
        return [u, Math.acos(clamp(y / length, -1, 1)) / Math.PI];
    }

    function dirFromAngles(yaw, pitch) {
        const c = Math.cos(pitch * RAD);
        return [c * Math.sin(yaw * RAD), Math.sin(pitch * RAD), -c * Math.cos(yaw * RAD)];
    }

    function anglesFromDir(x, y, z) {
        const length = Math.hypot(x, y, z) || 1;
        return { yaw: Math.atan2(x, -z) / RAD, pitch: Math.asin(clamp(y / length, -1, 1)) / RAD };
    }

    /* the camera's own right, up and ahead */
    function basis(yaw, pitch, roll) {
        const cy = Math.cos(yaw * RAD), sy = Math.sin(yaw * RAD), cp = Math.cos(pitch * RAD), sp = Math.sin(pitch * RAD);
        const f = [cp * sy, sp, -cp * cy];
        let r = [cy, 0, sy];
        let u = [-sp * sy, cp, sp * cy];
        if (roll) {
            const c = Math.cos(roll * RAD), s = Math.sin(roll * RAD);
            const r2 = [r[0] * c + u[0] * s, r[1] * c + u[1] * s, r[2] * c + u[2] * s];
            u = [u[0] * c - r[0] * s, u[1] * c - r[1] * s, u[2] * c - r[2] * s];
            r = r2;
        }
        return { r, u, f };
    }

    /* View and projection in one matrix (column-major, as WebGL takes it).
     * `crop` = {x0, y0, x1, y1}, fractions of the picture from its top left corner: the matrix then shows only
     * that part, filling the whole target. */
    function viewProjection(cam, aspect, crop, near, far) {
        const b = basis(cam.yaw, cam.pitch, cam.roll || 0), p = cam.pos || [0, 0, 0];
        near = near || 0.02; far = far || 4000;
        const t = 1 / Math.tan(clamp(cam.fov, 1, 170) * RAD / 2);
        let sx = t / aspect, sy = t, ox = 0, oy = 0;
        if (crop) {
            const w = crop.x1 - crop.x0, h = crop.y1 - crop.y0;
            const cx = crop.x0 + crop.x1 - 1, cy = 1 - (crop.y0 + crop.y1);
            sx /= w; sy /= h; ox = -cx / w; oy = -cy / h;
        }
        const A = -(far + near) / (far - near), B = -2 * far * near / (far - near);
        const tx = -(b.r[0] * p[0] + b.r[1] * p[1] + b.r[2] * p[2]);
        const ty = -(b.u[0] * p[0] + b.u[1] * p[1] + b.u[2] * p[2]);
        const tz = (b.f[0] * p[0] + b.f[1] * p[1] + b.f[2] * p[2]);       // camera z = -ahead
        // rows of the view matrix: right, up, -ahead
        const V = [
            [b.r[0], b.r[1], b.r[2], tx],
            [b.u[0], b.u[1], b.u[2], ty],
            [-b.f[0], -b.f[1], -b.f[2], tz],
        ];
        const m = new Float32Array(16);
        for (let c = 0; c < 4; c++) {
            const x = V[0][c], y = V[1][c], z = V[2][c], w = c === 3 ? 1 : 0;
            m[c * 4 + 0] = sx * x - ox * z;
            m[c * 4 + 1] = sy * y - oy * z;
            m[c * 4 + 2] = A * z + B * w;
            m[c * 4 + 3] = -z;
        }
        return m;
    }

    /* Where a point lands in the picture: [x, y] as fractions from the top left, or null behind the camera. */
    function project(m, p) {
        const w = m[3] * p[0] + m[7] * p[1] + m[11] * p[2] + m[15];
        if (w <= 1e-6) return null;
        const x = (m[0] * p[0] + m[4] * p[1] + m[8] * p[2] + m[12]) / w;
        const y = (m[1] * p[0] + m[5] * p[1] + m[9] * p[2] + m[13]) / w;
        return [x * 0.5 + 0.5, 0.5 - y * 0.5, w];
    }

    /* The direction a point of the picture looks at (fractions from the top left). */
    function rayThrough(cam, aspect, sx, sy) {
        const b = basis(cam.yaw, cam.pitch, cam.roll || 0), t = Math.tan(clamp(cam.fov, 1, 170) * RAD / 2);
        const x = (sx * 2 - 1) * t * aspect, y = (1 - sy * 2) * t;
        const d = [b.f[0] + b.r[0] * x + b.u[0] * y, b.f[1] + b.r[1] * x + b.u[1] * y, b.f[2] + b.r[2] * x + b.u[2] * y];
        const n = Math.hypot(d[0], d[1], d[2]);
        return [d[0] / n, d[1] / n, d[2] / n];
    }

    /* ---- depth ------------------------------------------------------------------------------------------- */

    /* How far each direction of a world is. `near` is a grid of "nearness" (1 = the nearest thing in the
     * picture, 0 = the farthest); the world's own near and far distances turn it into metres.
     * The world's form (`shape.form`):
     *   'round': as the picture's depth says (a ball pushed in and out);
     *   'earth': the ground is flat, `eye` metres below the eyes, and the sky a dome: looking down, the distance
     *            is the floor's, unless something stands well before it (nearer than 0.45 of the floor: a car, a
     *            person close by), which keeps its own (a picture's own ground is often drawn nearer than a true
     *            floor: it becomes the floor); far off the ground meets the sky;
     *   'box':   a room: flat walls `room` metres apart, a flat floor and a ceiling `ceiling` metres up, things
     *            nearer than them keeping their own distance as above. */
    class DepthField {
        constructor(nearness, width, height) {
            this.n = nearness || null; this.w = width || 0; this.h = height || 0;
            this.near = 1.6; this.far = 14; this.strength = 1;
            this.form = 'round'; this.eye = 1.65; this.room = 8; this.ceiling = 3.2;
        }
        tune(near, far, strength, shape) {
            this.near = Math.max(0.05, near); this.far = Math.max(this.near + 0.01, far);
            this.strength = clamp(strength == null ? 1 : strength, 0, 1.5);
            shape = shape || {};
            this.form = shape.form === 'earth' || shape.form === 'box' ? shape.form : 'round';
            this.eye = Math.max(0.1, +shape.eye || 1.65);
            this.room = Math.max(0.5, +shape.room || 8);
            this.ceiling = Math.max(this.eye + 0.1, +shape.ceiling || Math.max(3.2, this.eye + 1));
            return this;
        }
        /* where the flat surfaces of a form are, in the direction (x, y, z) of length one: metres, or Infinity
         * (an earth has only its ground; above the horizon its sky is the picture's own) */
        formAt(d) {
            if (this.form === 'round') return Infinity;
            let out = d[1] < -1e-6 ? this.eye / -d[1] : Infinity;
            if (this.form === 'earth') return out;
            const half = this.room / 2;
            if (Math.abs(d[0]) > 1e-6) out = Math.min(out, half / Math.abs(d[0]));
            if (Math.abs(d[2]) > 1e-6) out = Math.min(out, half / Math.abs(d[2]));
            if (d[1] > 1e-6) out = Math.min(out, (this.ceiling - this.eye) / d[1]);
            return out;
        }
        nearAt(u, v) {
            if (!this.n) return 0;
            const w = this.w, h = this.h;
            let x = (u - Math.floor(u)) * w - 0.5, y = clamp(v, 0, 1) * h - 0.5;
            const x0 = Math.floor(x), y0 = clamp(Math.floor(y), 0, h - 1), fx = x - x0, fy = clamp(y - y0, 0, 1);
            const xa = ((x0 % w) + w) % w, xb = (xa + 1) % w, yb = Math.min(h - 1, y0 + 1);
            const n = this.n;
            return lerp(lerp(n[y0 * w + xa], n[y0 * w + xb], fx), lerp(n[yb * w + xa], n[yb * w + xb], fx), fy);
        }
        metres(nearness) {
            return 1 / (clamp(nearness * this.strength, 0, 1) * (1 / this.near - 1 / this.far) + 1 / this.far);
        }
        distAt(u, v) {
            const own = this.metres(this.nearAt(u, v));
            if (this.form === 'round') return own;
            const flat = this.formAt(dirFromUv(u, v));
            if (flat === Infinity) return own;
            // Something that stands well before the floor or a wall (nearer than about 0.45 of it) keeps its own
            // distance; the picture's own ground and walls become flat. Between the two it changes softly, so no
            // seam is drawn where they meet; far off the ground goes no farther than the world's far distance.
            const w = smooth((own / flat - 0.35) / 0.2);
            return own + (Math.min(flat, this.form === 'earth' ? Math.max(own, this.far) : flat) - own) * w;
        }
        distTowards(x, y, z) { const uv = uvFromDir(x, y, z); return this.distAt(uv[0], uv[1]); }
    }

    /* Keeps the camera inside its world: it may go `limit` of the way to whatever is in that direction.
     * Returns the position (pulled back when needed), how far it is of the way (0..1) and the direction. */
    function constrain(pos, field, limit) {
        const d = Math.hypot(pos[0], pos[1], pos[2]);
        if (d < 1e-6) return { pos, share: 0, uv: null };
        const uv = uvFromDir(pos[0], pos[1], pos[2]), reach = field.distAt(uv[0], uv[1]);
        const share = d / reach;
        if (share <= limit) return { pos, share, uv };
        const k = limit * reach / d;
        return { pos: [pos[0] * k, pos[1] * k, pos[2] * k], share: limit, uv, stopped: true };
    }

    /* ---- the world behind a portal ------------------------------------------------------------------------ */

    /* A vector turned to the right by `degrees` (about the upright axis): a direction with yaw y becomes the
     * direction with yaw y + degrees. This is how a portal's world is turned against the world it is in. */
    function swing(v, degrees) {
        const a = degrees * RAD, c = Math.cos(a), s = Math.sin(a);
        return [c * v[0] - s * v[2], v[1], s * v[0] + c * v[2]];
    }

    /* The camera as it would stand in the world behind a portal. The two worlds are joined at one point: the
     * portal's doorstep here is the arrival point there, and that world is turned by `turnBy` degrees. So a
     * camera one metre before the doorstep is one metre before the arrival point, and on the doorstep it is
     * exactly where going through will put it. */
    function beyond(cam, door, arrive, turnBy) {
        const d = swing([cam.pos[0] - door[0], cam.pos[1] - door[1], cam.pos[2] - door[2]], turnBy);
        return { pos: [arrive[0] + d[0], arrive[1] + d[1], arrive[2] + d[2]], yaw: cam.yaw + turnBy, pitch: cam.pitch, roll: cam.roll || 0, fov: cam.fov };
    }

    /* ---- the ground --------------------------------------------------------------------------------------- */

    /* Where a thing that stands on the ground is drawn. Its place is a place on a map of the pictured scene:
     * x and z metres from where the picture was taken, on flat ground `eye` metres below the eyes. The world
     * is that picture pushed out by its depth, and a picture's depth is seldom as deep as the place it shows:
     * the far end of a street may be four metres away. So the thing is put on the world's surface in the
     * direction of its place, and made as much smaller as the surface is nearer than the place. From the
     * centre it then stands exactly on the spot of ground the picture has there, as large as the picture is
     * there, and when the camera moves its feet stay on the ground that is seen. `lift`: metres above the
     * ground. Returns {pos, small}: where its feet are in the world, and how much smaller it is. */
    function stood(field, x, z, eye, lift) {
        const far = Math.hypot(x, eye, z);
        const reach = field.distTowards(x, -eye, z) * 0.985;          // a little in front of the surface, not in it
        const k = clamp(reach / far, 0.01, 2.5);
        return { pos: [x * k, ((lift || 0) - eye) * k, z * k], small: k };
    }

    /* And back: the place on the map of a point of the world's surface (the feet of something standing there).
     * A point at eye level or above it is on no ground: it is taken to be very far. */
    function mapped(point, eye) {
        const flat = Math.hypot(point[0], point[2]);
        if (flat < 1e-6) return { x: 0, z: 0, small: Math.abs(point[1]) / eye || 1 };
        const r = eye / Math.max(0.02, -point[1] / flat);              // that far away, on flat ground
        return { x: point[0] / flat * r, z: point[2] / flat * r, small: Math.hypot(flat, point[1]) / Math.hypot(r, eye) };
    }

    /* How far a thing has gone along its own path (keys {t, pos}, eased between them) by the time t: its
     * steps follow the metres, so it does not step on the spot while it waits. */
    function pathWalked(path, t) {
        let sum = 0;
        for (let i = 0; i + 1 < path.length; i++) {
            const a = path[i], b = path[i + 1], length = Math.hypot(b.pos[0] - a.pos[0], b.pos[2] - a.pos[2]);
            if (t >= b.t) { sum += length; continue; }
            if (t > a.t && b.t > a.t) sum += length * pathShare(b, (t - a.t) / (b.t - a.t));
            break;
        }
        return sum;
    }

    /* How far along the stretch to path key b a thing is, at the share x of its time: eased in and out (it
     * starts and stops at every key), or at an even pace when b says `line` (a path made of many keys close
     * together, as a route's way is: easing at each of them would make it stutter). */
    function pathShare(b, x) {
        x = clamp(x, 0, 1);
        return b && b.line ? x : smooth(x);
    }

    /* ---- a flight made into a route ---------------------------------------------------------------------- */

    /* A flight that was flown (a take: samples {t, world, pos, yaw, pitch, fov}, one every frame) as route keys
     * that can be changed afterwards: a key where the flight bends, turns or changes its lens, a wait (`hold`)
     * where it stood still for `still` seconds or more, and a key on each side of a world it went into.
     * tol: how far the route may stray from the flight between keys: pos (metres), angle, fov (degrees). */
    function takeToKeys(samples, tol) {
        tol = Object.assign({ pos: 0.15, angle: 6, fov: 4, still: 0.6, step: 0.1 }, tol || {});
        const all = (samples || []).filter(s => s && s.world && s.pos);
        if (all.length < 2) return [];
        // a sample every `step` seconds, the yaw without its jumps at ±180
        const pts = [];
        let next = all[0].t;
        for (const s of all) if (s.t >= next - 1e-9) { pts.push(s); next = s.t + tol.step; }
        if (pts[pts.length - 1] !== all[all.length - 1]) pts.push(all[all.length - 1]);
        const yaw = [+pts[0].yaw || 0];
        for (let i = 1; i < pts.length; i++) yaw.push(yaw[i - 1] + turn(yaw[i - 1], +pts[i].yaw || 0));
        const keep = new Set([0, pts.length - 1]), holds = new Map();
        // a key on each side of a world it went into
        for (let i = 1; i < pts.length; i++) if (pts[i].world !== pts[i - 1].world) { keep.add(i - 1); keep.add(i); }
        // where it stood still: a key at the start of the stillness, waiting until it moves again
        for (let i = 0; i < pts.length;) {
            let j = i;
            const moved = (a, b) => Math.hypot(pts[b].pos[0] - pts[a].pos[0], pts[b].pos[1] - pts[a].pos[1], pts[b].pos[2] - pts[a].pos[2]) > 0.02 || Math.abs(yaw[b] - yaw[a]) > 1 || Math.abs((+pts[b].pitch || 0) - (+pts[a].pitch || 0)) > 1;
            while (j + 1 < pts.length && pts[j + 1].world === pts[i].world && !moved(i, j + 1)) j++;
            if (pts[j].t - pts[i].t >= tol.still) { keep.add(i); keep.add(j); holds.set(i, j); }
            i = j + 1;
        }
        // between the keys so far: a key wherever the flight strays from a straight run between two keys
        const err = (a, b, k) => {
            const x = (pts[k].t - pts[a].t) / Math.max(1e-6, pts[b].t - pts[a].t), at = j => lerp(pts[a].pos[j], pts[b].pos[j], x);
            return Math.max(Math.hypot(pts[k].pos[0] - at(0), pts[k].pos[1] - at(1), pts[k].pos[2] - at(2)) / tol.pos,
                Math.abs(yaw[k] - lerp(yaw[a], yaw[b], x)) / tol.angle,
                Math.abs((+pts[k].pitch || 0) - lerp(+pts[a].pitch || 0, +pts[b].pitch || 0, x)) / tol.angle,
                Math.abs((+pts[k].fov || 90) - lerp(+pts[a].fov || 90, +pts[b].fov || 90, x)) / tol.fov);
        };
        const split = (a, b) => {
            if (b - a < 2) return;
            let worst = 0, at = -1;
            for (let k = a + 1; k < b; k++) { const e = err(a, b, k); if (e > worst) { worst = e; at = k; } }
            if (worst > 1) { keep.add(at); split(a, at); split(at, b); }
        };
        const fixed = Array.from(keep).sort((a, b) => a - b);
        for (let i = 0; i + 1 < fixed.length; i++) if (pts[fixed[i]].world === pts[fixed[i + 1]].world && !holds.has(fixed[i])) split(fixed[i], fixed[i + 1]);
        // the keys: each with the seconds to get there and the seconds it waits there
        const order = Array.from(keep).sort((a, b) => a - b), keys = [];
        let from = null;
        for (const i of order) {
            if (from != null && holds.has(from) && i <= holds.get(from)) continue;            // (inside a wait)
            const p = pts[i], left = from == null ? null : holds.has(from) ? holds.get(from) : from;
            let dur = left == null ? 0 : p.t - pts[left].t;
            if (left != null && pts[left].world !== p.world) dur = Math.max(dur, 1);          // going through a portal
            keys.push({ world: p.world, pos: p.pos.map(v => +(+v).toFixed(3)), yaw: +yaw[i].toFixed(2), pitch: +(+p.pitch || 0).toFixed(2), fov: +(+p.fov || 90).toFixed(1),
                dur: +dur.toFixed(2), hold: holds.has(i) ? +(pts[holds.get(i)].t - p.t).toFixed(2) : 0 });
            from = i;
        }
        if (keys.length) keys[0].dur = 0;
        return keys;
    }

    /* ---- a second camera ----------------------------------------------------------------------------------- */

    /* The cut the film is in at a moment: cuts [{t, cam: 'a' | 'b', way}], each from its second on (null before
     * the first: the main camera). A cut to the second camera may name its own way of following (`way`). */
    function cutAt(cuts, time) {
        let found = null;
        for (const c of cuts || []) if (c && +c.t <= time && (!found || +c.t >= +found.t)) found = c;
        return found;
    }

    /* Which camera the film shows at a moment: the route ('a', the main camera) or the second one ('b'). */
    function shotAt(cuts, time) {
        const c = cutAt(cuts, time);
        return c && c.cam === 'b' ? 'b' : 'a';
    }

    /* How the film goes over from one shot to the next around a cut (cut.pass): 'blink' (the eyes shut just
     * before it and open after it, as a blink between worlds does), 'dip' (through black) or 'dissolve' (one
     * picture into the other), the last two over cut.long seconds (0.8 when it is not said) with the cut in their
     * middle. At moment t: null (a plain cut, or no cut near), or {lids, dark, other, amount}: how shut the eyes
     * are, how black the picture is, and the shot on the other side of the cut (its cut; null: the main camera
     * before any cut) laid over the one the film is in at `amount`. */
    function passAt(cuts, time) {
        const sorted = (cuts || []).filter(c => c && isFinite(+c.t)).slice().sort((a, b) => a.t - b.t);
        let best = null;
        sorted.forEach((c, i) => {
            if (!c.pass || c.pass === 'cut') return;
            const d = time - +c.t, half = c.pass === 'blink' ? null : Math.max(0.05, c.long == null || c.long === '' ? 0.8 : +c.long) / 2;
            const inside = c.pass === 'blink' ? d > -BLINK_CLOSE && d < BLINK_OPEN : Math.abs(d) < half;
            if (inside && (!best || Math.abs(d) < Math.abs(best.d))) best = { c, d, half, before: i ? sorted[i - 1] : null };
        });
        if (!best) return null;
        const { c, d, half, before } = best;
        if (c.pass === 'blink') return { lids: d < 0 ? smooth(1 + d / BLINK_CLOSE) : 1 - smooth(d / BLINK_OPEN), dark: 0, other: undefined, amount: 0 };
        if (c.pass === 'dip') return { lids: 0, dark: smooth(1 - Math.abs(d) / half), other: undefined, amount: 0 };
        // a dissolve: before the cut the film is in the shot before it, with this cut's shot coming in over it;
        // after it, in this cut's shot with the one before going out (half and half at the cut itself)
        const coming = smooth((d + half) / (2 * half));
        return d < 0 ? { lids: 0, dark: 0, other: c, amount: coming } : { lids: 0, dark: 0, other: before, amount: 1 - coming };
    }

    /* An edit made from who speaks when: shot and reverse shot. `spoken`: the lines in order, [{start, end,
     * speaker}] (speaker: an object's id, or null for a voice from nowhere: then the shot stays as it is);
     * `walker`: the figure whose eyes the route is, or null; or a list of them (the same person in each world the
     * route goes through: each is "the walker" in its own world). opts:
     *   mine: when the walker speaks, the second camera on 'front' (its face), 'over' (over the shoulder of the one
     *         it speaks to: the nearest other speaker) or 'side';
     *   others: when another speaks, 'eyes' (the main camera: seen through the walker's eyes) or 'front' (its
     *         face); without a walker it is always its face;
     *   lead: the cut comes this long before the line begins (0.15 s); after: the main camera again this long
     *   after the last line (0.6 s); pass, long: how each cut goes over.
     * Returns cuts [{t, cam, target, way, pass, long}], one where the shot changes. */
    function dialogueCuts(spoken, walker, opts) {
        opts = opts || {};
        const lead = opts.lead == null ? 0.15 : +opts.lead, after = opts.after == null ? 0.6 : +opts.after;
        const walkers = new Set((Array.isArray(walker) ? walker : [walker]).filter(Boolean));
        const lines = (spoken || []).filter(l => l && isFinite(+l.start)).slice().sort((a, b) => a.start - b.start);
        const listener = i => {
            for (let d = 1; d < lines.length; d++) for (const j of [i - d, i + d]) {
                const l = lines[j];
                if (l && l.speaker && l.speaker !== lines[i].speaker) return l.speaker;
            }
            return null;
        };
        const shotFor = (line, i) => {
            if (!line.speaker) return null;
            if (walkers.has(line.speaker)) {
                const other = opts.mine === 'over' ? listener(i) : null;
                if (other && !walkers.has(other)) return { cam: 'b', target: other, way: 'behind' };
                return { cam: 'b', target: line.speaker, way: opts.mine === 'side' ? 'side' : 'front' };
            }
            if (walkers.size && (opts.others || 'eyes') === 'eyes') return { cam: 'a' };
            return { cam: 'b', target: line.speaker, way: 'front' };
        };
        const same = (a, b) => a.cam === b.cam && (a.cam === 'a' || (a.target === b.target && a.way === b.way));
        const cuts = [], pass = opts.pass ? { pass: opts.pass, long: opts.long == null ? 0.5 : +opts.long } : {};
        let current = { cam: 'a' }, end = 0;
        lines.forEach((line, i) => {
            if (line.speaker) end = Math.max(end, +line.end || +line.start);          // (the talk ends with its last speaker)
            const shot = shotFor(line, i);
            if (!shot || same(shot, current)) return;
            const t = +Math.max(0, line.start - lead, cuts.length ? cuts[cuts.length - 1].t + 0.2 : 0).toFixed(2);
            cuts.push(Object.assign({ t }, shot, pass));
            current = shot;
        });
        if (current.cam !== 'a' && lines.length) cuts.push(Object.assign({ t: +(end + after).toFixed(2), cam: 'a' }, pass));
        return cuts;
    }

    /* Each way of following a figure, where its camera stands: distance, camera height, height looked at and
     * how far before the figure it looks (in heights of the figure), and the lens. */
    const FOLLOW_WAYS = {
        side: { distance: 2.4, height: 0.6, aim: 0.6, ahead: 0, fov: 40 },
        behind: { distance: 1.6, height: 0.9, aim: 0.75, ahead: 1.5, fov: 50 },
        front: { distance: 2, height: 0.85, aim: 0.85, ahead: 0, fov: 35 },
        round: { distance: 2.6, height: 0.7, aim: 0.6, ahead: 0, fov: 40 },
        tripod: { distance: 2.4, height: 0.6, aim: 0.6, ahead: 0, fov: 40, frame: 0 },
    };

    /* Where a camera that follows a figure stands and where it looks. `feet`: where the figure stands, `tall`:
     * how tall it is there (world units), `facing`: the yaw it faces, `time`: the scene's second (a camera that
     * goes round it turns with time). spec: {mode, side, distance, height, aim, ahead, fov, turn}:
     *   mode 'side': beside it, at its right (side 1) or its left (side -1), looking at it;
     *        'behind': behind it and a little off one shoulder, looking past it where it goes;
     *        'front': before it, a little to one side, looking at its face;
     *        'round': going round it, `turn` degrees a second;
     *        'tripod': standing where `stand.pos` says, turning to keep it in the picture; with `frame` (0..1) the
     *        lens closes in or opens up so that it fills that much of the picture's height (0: the lens as set).
     *        Not placed yet: beside it, as 'side';
     *   distance, height (of the camera), aim (the height looked at), ahead (how far before it the camera
     *   looks): in heights of the figure; fov in degrees. */
    function followCam(spec, feet, tall, facing, time) {
        spec = spec || {};
        const mode = FOLLOW_WAYS[spec.mode] ? spec.mode : 'side', side = +spec.side === -1 ? -1 : 1, way = FOLLOW_WAYS[mode];
        const given = key => (spec[key] == null || spec[key] === '' ? way[key] : +spec[key]);
        const distance = given('distance') * tall;
        const aside = mode === 'behind' ? 180 + 16 * side : mode === 'front' ? 14 * side : mode === 'round' ? 90 * side + (spec.turn == null ? 20 : +spec.turn) * time : 90 * side;
        const out = dirFromAngles(facing + aside, 0), ahead = dirFromAngles(facing, 0);
        const height = given('height') * tall;
        const pos = [feet[0] + out[0] * distance, feet[1] + height, feet[2] + out[2] * distance];
        const before = given('ahead') * tall, aim = given('aim') * tall;
        const look = [feet[0] + ahead[0] * before, feet[1] + aim, feet[2] + ahead[2] * before];
        let fov = given('fov');
        if (mode === 'tripod' && spec.stand && spec.stand.pos) {
            for (let j = 0; j < 3; j++) pos[j] = +spec.stand.pos[j] || 0;
            const frame = clamp(given('frame') || 0, 0, 0.95), far = Math.hypot(look[0] - pos[0], look[1] - pos[1], look[2] - pos[2]);
            if (frame > 0 && far > 1e-6) fov = 2 * Math.atan(tall / (2 * frame * far)) / RAD;
        }
        const a = anglesFromDir(look[0] - pos[0], look[1] - pos[1], look[2] - pos[2]);
        return { pos, look, yaw: a.yaw, pitch: clamp(a.pitch, -89, 89), roll: 0, fov: clamp(fov, 5, 150) };
    }

    /* ---- masks -------------------------------------------------------------------------------------------- */

    /* A mask (bytes, width x height, a panorama: its left edge meets its right edge) with its edge made
     * soft: `radius` pixels of blur at the horizon, and as much in degrees nearer the poles, where a pixel
     * is narrower. The edge itself stays where it was: half-way up the soft slope. */
    function feathered(mask, width, height, radius) {
        if (!(radius >= 0.5)) return mask;
        const a = new Float32Array(mask.length), b = new Float32Array(mask.length);
        for (let i = 0; i < mask.length; i++) a[i] = mask[i];
        const sums = new Float64Array(Math.max(width * 3, height) + 1);
        // three box blurs make a bell-shaped blur; each box is about half the radius
        const box = Math.max(1, Math.round(radius * 0.55));
        for (let pass = 0; pass < 3; pass++) {
            for (let y = 0; y < height; y++) {       // across: the picture wraps round
                const wide = Math.min(Math.floor(width / 2) - 1, Math.max(1, Math.round(box / Math.max(0.12, Math.cos((0.5 - (y + 0.5) / height) * Math.PI)))));
                const row = y * width;
                sums[0] = 0;
                for (let x = 0; x < width * 3; x++) sums[x + 1] = sums[x] + a[row + x % width];
                for (let x = 0; x < width; x++) b[row + x] = (sums[x + width + wide + 1] - sums[x + width - wide]) / (wide * 2 + 1);
            }
            for (let x = 0; x < width; x++) {        // up and down: the top and bottom rows repeat
                sums[0] = 0;
                for (let y = 0; y < height; y++) sums[y + 1] = sums[y] + b[y * width + x];
                for (let y = 0; y < height; y++) {
                    const top = y - box, bottom = y + box;
                    let sum = sums[Math.min(height, bottom + 1)] - sums[Math.max(0, top)];
                    if (top < 0) sum += b[x] * -top;
                    if (bottom > height - 1) sum += b[(height - 1) * width + x] * (bottom - height + 1);
                    a[y * width + x] = sum / (box * 2 + 1);
                }
            }
        }
        const out = new Uint8Array(mask.length);
        for (let i = 0; i < out.length; i++) out[i] = a[i] + 0.5;
        return out;
    }

    /* What is far: every direction of the world that is at least `metres` away and within `half` degrees to
     * either side of the heading `yaw`. A street's far part, a room's far wall: the natural shape of a
     * portal into the next place. */
    function farMask(field, width, height, metres, yaw, half) {
        const out = new Uint8Array(width * height);
        for (let y = 0; y < height; y++) {
            const v = (y + 0.5) / height;
            for (let x = 0; x < width; x++) {
                const u = (x + 0.5) / width;
                if (Math.abs(turn(yaw, (u - 0.5) * 360)) > half) continue;
                if (field.distAt(u, v) >= metres) out[y * width + x] = 255;
            }
        }
        return out;
    }

    /* A gate standing across the way: an upright opening `ahead` metres away in the heading `yaw`, `wide`
     * metres across, from the ground (`eye` metres below the eyes) up to `tall` metres, or up to the sky
     * when `tall` is 0. Everything seen through it, from the centre, is the mask: the street beyond that
     * line of the ground, between those two walls. */
    function gateMask(width, height, yaw, eye, ahead, wide, tall) {
        const out = new Uint8Array(width * height), half = wide / 2;
        for (let y = 0; y < height; y++) {
            const pitch = (0.5 - (y + 0.5) / height) * Math.PI, c = Math.cos(pitch), s = Math.sin(pitch);
            for (let x = 0; x < width; x++) {
                const off = turn(yaw, ((x + 0.5) / width - 0.5) * 360) * RAD, forward = c * Math.cos(off);
                if (forward < 1e-6) continue;
                const t = ahead / forward, up = t * s;                      // where this direction meets the gate's plane
                if (Math.abs(t * c * Math.sin(off)) <= half && up >= -eye && (!(tall > 0) || up <= tall - eye)) out[y * width + x] = 255;
            }
        }
        return out;
    }

    /* Keeps a point inside its world softly: up to `knee` of the way to the surface it is left alone, and
     * from there on it comes nearer and nearer to `limit` of the way without ever stopping short. This is
     * for the camera that draws a world from behind its portal: as the visitor walks on, that world keeps
     * moving, more and more slowly, instead of standing still against a wall. */
    function eased(pos, field, knee, limit) {
        const d = Math.hypot(pos[0], pos[1], pos[2]);
        if (d < 1e-6) return pos;
        const share = d / field.distTowards(pos[0], pos[1], pos[2]);
        if (share <= knee) return pos;
        const k = (knee + (limit - knee) * (1 - Math.exp(-(share - knee) / (limit - knee)))) / share;
        return [pos[0] * k, pos[1] * k, pos[2] * k];
    }

    /* ---- how the camera is carried ----------------------------------------------------------------------- */

    function hash(n) { const s = Math.sin(n * 127.1 + 311.7) * 43758.5453; return s - Math.floor(s); }
    /* smooth noise, -1..1, the same for the same x every time (so a recording can be drawn again exactly) */
    function noise(x, seed) {
        const i = Math.floor(x), f = x - i, t = f * f * (3 - 2 * f);
        return lerp(hash(i + seed * 57.3), hash(i + 1 + seed * 57.3), t) * 2 - 1;
    }
    function drift(x, seed) { return noise(x, seed) * 0.6 + noise(x * 2.03 + 11, seed + 3) * 0.3 + noise(x * 4.1 + 5, seed + 7) * 0.1; }

    const RIGS = {
        pro: { accel: 3.2, turn: 7 },           // a dolly on rails: only smooth starts and stops
        hover: { accel: 1.6, turn: 4 },         // floating: slow sway, as if weightless
        walk: { accel: 7, turn: 10 },           // a person walking: steps
        handheld: { accel: 5, turn: 12 },       // a camera in the hand
        run: { accel: 9, turn: 12 },            // somebody's eyes, running: strides, breath
        ride: { accel: 4, turn: 6 },            // on a bicycle: leaning into it, the road's rattle
    };

    /* What the way of carrying adds to the camera: metres along its own right / up / ahead, and degrees.
     * `time` in seconds, `travelled` in metres (steps follow the distance, not the clock). */
    function rigOffset(kind, time, travelled, strength, speed) {
        const k = strength == null ? 1 : strength;
        const out = { right: 0, up: 0, ahead: 0, yaw: 0, pitch: 0, roll: 0 };
        if (!k || kind === 'pro' || !RIGS[kind]) return out;
        if (kind === 'hover') {
            out.up = Math.sin(time * 0.9) * 0.045 * k + drift(time * 0.23, 1) * 0.03 * k;
            out.right = drift(time * 0.19, 2) * 0.035 * k;
            out.roll = drift(time * 0.21, 3) * 0.8 * k;
            out.pitch = drift(time * 0.17, 4) * 0.35 * k;
            out.yaw = drift(time * 0.15, 5) * 0.35 * k;
        } else if (kind === 'walk') {
            const step = travelled / 0.72 * Math.PI;                       // one step every 72 cm
            out.up = -Math.abs(Math.sin(step)) * 0.045 * k + 0.02 * k;
            out.right = Math.sin(step) * 0.022 * k;
            out.roll = Math.sin(step) * 0.45 * k;
            out.pitch = Math.sin(step * 2 + 0.6) * 0.22 * k + drift(time * 0.4, 6) * 0.2 * k;
            out.yaw = drift(time * 0.35, 7) * 0.3 * k;
        } else if (kind === 'handheld') {
            out.up = drift(time * 1.3, 8) * 0.012 * k;
            out.right = drift(time * 1.1, 9) * 0.012 * k;
            out.yaw = drift(time * 1.7, 10) * 0.55 * k + drift(time * 6.5, 11) * 0.07 * k;
            out.pitch = drift(time * 1.9, 12) * 0.5 * k + drift(time * 7.1, 13) * 0.07 * k;
            out.roll = drift(time * 1.2, 14) * 0.6 * k;
        } else if (kind === 'run') {
            // the strides are as large as the pace: standing still, only the breath is left
            const pace = speed == null ? 1 : clamp(speed / (RIGS.run.pace || 1.7), 0, 1.35), g = k * pace;
            const step = travelled / (RIGS.run.stride || 1.05) * Math.PI;  // a running stride
            out.up = (-Math.abs(Math.sin(step)) * 0.085 + 0.04) * g + Math.sin(time * 2.7) * 0.012 * k;
            out.right = Math.sin(step) * 0.035 * g;
            out.roll = Math.sin(step) * 1.1 * g + drift(time * 0.9, 21) * 0.5 * k;
            out.pitch = Math.sin(step * 2 + 0.6) * 0.7 * g + drift(time * 1.3, 22) * 0.55 * k + Math.sin(time * 2.7 + 1) * 0.22 * k;
            out.yaw = Math.sin(step + 0.4) * 0.5 * g + drift(time * 1.1, 23) * 0.6 * k;
        } else if (kind === 'ride') {
            out.up = drift(time * 9, 31) * 0.008 * k + Math.sin(time * 1.3) * 0.012 * k;
            out.right = drift(time * 0.45, 32) * 0.06 * k;
            out.roll = drift(time * 0.5, 33) * 2.4 * k;
            out.pitch = drift(time * 7, 34) * 0.12 * k + drift(time * 0.6, 35) * 0.4 * k;
            out.yaw = drift(time * 0.55, 36) * 1.1 * k;
        }
        return out;
    }

    /* The camera as it is drawn: the pose plus what its carrying adds. */
    function carried(pose, kind, time, travelled, strength, speed) {
        const o = rigOffset(kind, time, travelled, strength, speed);
        const b = basis(pose.yaw, 0, 0);
        return {
            pos: [pose.pos[0] + b.r[0] * o.right + b.f[0] * o.ahead, pose.pos[1] + o.up, pose.pos[2] + b.r[2] * o.right + b.f[2] * o.ahead],
            yaw: pose.yaw + o.yaw, pitch: clamp(pose.pitch + o.pitch, -89.5, 89.5), roll: (pose.roll || 0) + o.roll, fov: pose.fov,
        };
    }

    /* ---- the drawn route (tour) -------------------------------------------------------------------------- */

    const MIX_SECONDS = 0.55;       // going through a portal: the next world takes over the picture
    const UNFOLD_SECONDS = 0.9;     // and then gets its depth back
    const SPREAD_SECONDS = 0.8;     // a portal that shows the next world in its depth: it opens around the camera this long
    const RAMP_SECONDS = 0.9;       // a route starts and stops softly
    const BLINK_CLOSE = 0.11;       // a blink: the lids come down this fast
    const BLINK_OPEN = 0.2;         // and go up a little slower

    /* What a blow from behind does to the head, `d` seconds after it: thrown forward, and back again. */
    function jolted(d, strength) {
        if (d < 0 || d > 1.4) return null;
        const k = strength || 1;
        return {
            ahead: 0.5 * k * (1 - Math.exp(-d * 16)) * Math.exp(-d * 3.4),
            pitch: -11 * k * Math.sin(d * 10.5) * Math.exp(-d * 4.2),
            roll: 4.5 * k * Math.sin(d * 13 + 1) * Math.exp(-d * 4.8),
        };
    }

    function catmull(a, b, c, d, t) {
        const t2 = t * t, t3 = t2 * t;
        return 0.5 * ((2 * b) + (-a + c) * t + (2 * a - 5 * b + 4 * c - d) * t2 + (-a + 3 * b - 3 * c + d) * t3);
    }

    /* time 0..1 with a soft start and/or a soft stop, a steady pace between them */
    function paced(x, total, softIn, softOut) {
        const r = clamp(RAMP_SECONDS / Math.max(total, 1e-6), 0, 0.5);
        const a = softIn ? r : 0, b = softOut ? r : 0;
        if (a + b <= 0) return x;
        const v = 1 / (1 - a / 2 - b / 2);                 // the steady pace that makes the whole come to 1
        if (x < a) return v * x * x / (2 * a);
        if (x > 1 - b) { const y = 1 - x; return 1 - v * y * y / (2 * b); }
        return v * (x - a / 2);
    }

    /* A route is a list of keys, each a camera in some world, with the seconds it takes to get there from
     * the key before it and the seconds to wait there. `links(from, to)` says how to get from one world to
     * another: {enter: pose at the portal, arrive: {pos}, turn: degrees the next world is turned by} or null.
     * The route becomes stretches: each is a run of poses in one world, flown as one smooth curve. */
    function compileTour(tour, links) {
        const keys = (tour && tour.keys || []).filter(k => k && k.world);
        const stretches = [];
        if (!keys.length) return { stretches, duration: 0 };
        const poseOf = k => ({ pos: (k.pos || [0, 0, 0]).slice(), yaw: +k.yaw || 0, pitch: +k.pitch || 0, fov: +k.fov || 90, roll: +k.roll || 0, lids: clamp(+k.lids || 0, 0, 1) });
        // the way the camera is carried can change at a key (running, then on a bicycle): it holds from there on
        let rig = keys[0].rig ? { kind: keys[0].rig, strength: keys[0].rig_strength } : null, openEyes = false;
        const fresh = (world, poses, more) => Object.assign({ world, poses, times: [], softIn: true, softOut: true, unfold: false, mix: null, rig }, more || {});
        let current = fresh(keys[0].world, [poseOf(keys[0])]);
        const put = s => { if (openEyes) { s.blinkIn = true; openEyes = false; } stretches.push(s); };
        const close = () => { if (current.poses.length > 1 || current.wait) put(current); };
        const wait = seconds => { if (seconds > 0) { close(); const here = current.poses[current.poses.length - 1]; put(fresh(current.world, [here], { wait: seconds })); current = fresh(current.world, [here]); } };
        const marks = [], keyTimes = [0];
        let clock = 0;
        const mark = key => { if (key.jolt) marks.push({ t: clock, jolt: +key.jolt || 1 }); if (key.blink) marks.push({ t: clock, blink: true }); };
        mark(keys[0]);
        wait(+keys[0].hold || 0);
        clock += Math.max(0, +keys[0].hold || 0);
        for (let i = 1; i < keys.length; i++) {
            const key = keys[i], seconds = Math.max(0.05, +key.dur || 3), target = poseOf(key);
            const last = current.poses[current.poses.length - 1];
            const carriedAnew = key.rig && (!rig || rig.kind !== key.rig || rig.strength !== key.rig_strength);
            if (key.world !== current.world && key.cut === 'blink') {
                // through somebody's eyes there are no cuts, there are blinks: the eyes shut on one place
                // and open on another
                close();
                put(fresh(current.world, [last], { wait: seconds, blinkOut: true }));
                if (carriedAnew) rig = { kind: key.rig, strength: key.rig_strength };
                current = fresh(key.world, [target]);
                openEyes = true;
            } else if (key.world === current.world) {
                target.yaw = last.yaw + turn(last.yaw, target.yaw);        // the short way round
                current.poses.push(target); current.times.push(seconds);
                if (carriedAnew) {
                    current.softOut = false;
                    close();
                    rig = { kind: key.rig, strength: key.rig_strength };
                    current = fresh(key.world, [target], { softIn: false });
                }
            } else {
                const link = links ? links(current.world, key.world, key.via) : null;
                let approach = 0, enter = last, turnBy, arrive, deep = false, before = null;
                if (link && link.enter && link.deep) {
                    // The world behind this portal is drawn as it really looks from where the camera is, so
                    // the doorstep is only a point on the way: the camera keeps its pace, its heading and its
                    // view, the next world is already there around it, and nothing has to unfold.
                    deep = true; turnBy = link.turn || 0;
                    const door = link.enter.pos, there = (link.arrive && link.arrive.pos) || [0, 0, 0];
                    const way = [0, 1, 2].map(j => door[j] - last.pos[j]), on = swing([0, 1, 2].map(j => target.pos[j] - there[j]), -turnBy);
                    const near = Math.hypot(way[0], way[1], way[2]), far = Math.hypot(on[0], on[1], on[2]);
                    const share = near + far > 1e-3 ? clamp(near / (near + far), 0.15, 0.85) : 0.5;
                    approach = seconds * share;
                    const heading = turn(last.yaw, target.yaw - turnBy);
                    // everything about the camera changes evenly from the key before the portal to the key after it
                    const at = f => ({
                        pos: [0, 1, 2].map(j => last.pos[j] + (way[j] + on[j]) * f), yaw: last.yaw + heading * f, pitch: lerp(last.pitch, target.pitch, f),
                        fov: lerp(last.fov, target.fov, f), roll: lerp(last.roll || 0, target.roll || 0, f), lids: lerp(last.lids || 0, target.lids || 0, f),
                    });
                    enter = Object.assign(at(share), { pos: door.slice() });
                    arrive = Object.assign({}, enter, { pos: there.slice(), yaw: enter.yaw + turnBy });
                    current.poses.push(enter); current.times.push(approach);
                    // the curve does not end at the portal: it bends towards where the camera goes on to
                    current.after = at(2 * share);
                    before = at(2 * share - 1);
                    const back = swing([0, 1, 2].map(j => before.pos[j] - door[j]), turnBy);
                    before.pos = [0, 1, 2].map(j => there[j] + back[j]);
                    before.yaw += turnBy;
                } else if (link && link.enter) {
                    approach = seconds * 0.55; enter = link.enter; turnBy = link.turn || 0;
                    // at the portal the view may be narrower (zoomed into it), never wider than it was
                    const fov = Math.min(enter.fov || last.fov, last.fov);
                    arrive = { pos: (link.arrive && link.arrive.pos || [0, 0, 0]).slice(), yaw: enter.yaw + turnBy, pitch: enter.pitch, fov };
                    enter = { pos: enter.pos.slice(), yaw: last.yaw + turn(last.yaw, enter.yaw), pitch: enter.pitch, fov };
                    arrive.yaw = enter.yaw + turnBy;
                    current.poses.push(enter); current.times.push(approach);
                } else {
                    // no portal between them: the next world fades in around the camera where it stands
                    turnBy = turn(last.yaw, target.yaw);
                    arrive = { pos: target.pos.slice(), yaw: last.yaw + turnBy, pitch: last.pitch, fov: last.fov };
                    if (current.poses.length < 2) { current.poses.push({ ...last, pos: last.pos.slice() }); current.times.push(Math.min(1, seconds * 0.3)); approach = current.times[0]; }
                }
                current.softOut = false;
                current.mix = { world: key.world, turn: turnBy, seconds: deep ? Math.min(SPREAD_SECONDS, approach * 0.7) : MIX_SECONDS, deep, via: (link && link.via) || null };
                close();
                if (carriedAnew) rig = { kind: key.rig, strength: key.rig_strength };
                current = fresh(key.world, [arrive, target], { softIn: false, unfold: !deep, before });
                current.times = [Math.max(0.05, seconds - approach)];
                // the yaw of the keys after this one continues from the turned heading
                target.yaw = arrive.yaw + turn(arrive.yaw, target.yaw);
            }
            clock += seconds;
            keyTimes.push(clock);
            mark(key);
            if (+key.hold > 0) { wait(+key.hold); clock += +key.hold; }
        }
        close();
        // each stretch: when it starts, how long it is, and the metres flown before it and inside it
        let at = 0, flown = 0;
        for (const s of stretches) {
            s.start = at;
            s.total = s.wait || s.times.reduce((a, b) => a + b, 0);
            s.flownBefore = flown;
            s.lengths = [0];
            if (!s.wait) {
                let before = s.poses[0].pos, sum = 0;
                const pieces = (s.poses.length - 1) * 16;
                for (let j = 1; j <= pieces; j++) {
                    const p = alongStretch(s, j / pieces).pos;
                    sum += Math.hypot(p[0] - before[0], p[1] - before[1], p[2] - before[2]);
                    s.lengths.push(sum); before = p;
                }
                flown += sum;
            }
            at += s.total;
        }
        return { stretches, duration: at, marks, keyTimes };
    }

    /* the pose at share x (0..1 of the stretch's time, already paced) */
    function alongStretch(s, x) {
        const poses = s.poses, n = poses.length;
        if (n === 1) return { ...poses[0], pos: poses[0].pos.slice() };
        let seconds = clamp(x, 0, 1) * s.times.reduce((a, b) => a + b, 0), i = 0;
        while (i < s.times.length - 1 && seconds > s.times[i]) { seconds -= s.times[i]; i++; }
        const t = clamp(seconds / s.times[i], 0, 1);
        // (a stretch that begins or ends on a portal's doorstep knows where the camera came from or goes on to)
        const a = i > 0 ? poses[i - 1] : (s.before || poses[0]), b = poses[i], c = poses[i + 1], d = i + 2 < n ? poses[i + 2] : (s.after || poses[n - 1]);
        const curve = f => catmull(f(a), f(b), f(c), f(d), t);
        return {
            pos: [0, 1, 2].map(j => curve(p => p.pos[j])),
            yaw: curve(p => p.yaw), pitch: clamp(curve(p => p.pitch), -89, 89), fov: clamp(curve(p => p.fov), 5, 150),
            roll: curve(p => p.roll || 0), lids: clamp(lerp(b.lids || 0, c.lids || 0, smooth(t)), 0, 1),
        };
    }

    /* Where the route's camera is at a moment: {world, pos, yaw, pitch, fov, relief, mix, travelled}. */
    function tourAt(compiled, time) {
        const m = bare(compiled, time);
        if (!m) return null;
        // how fast the camera is going: the strides of a runner follow it
        const before = bare(compiled, Math.max(0, time - 0.15));
        m.speed = before && time > 0 ? Math.max(0, m.travelled - before.travelled) / Math.min(0.15, time) : 0;
        for (const mark of compiled.marks || []) {
            const d = time - mark.t;
            if (mark.blink && d > -BLINK_CLOSE && d < BLINK_OPEN) m.lids = Math.max(m.lids, d < 0 ? smooth(1 + d / BLINK_CLOSE) : 1 - smooth(d / BLINK_OPEN));
            const thrown = mark.jolt ? jolted(d, mark.jolt) : null;
            if (thrown) {
                const ahead = dirFromAngles(m.yaw, 0);
                m.pos = [m.pos[0] + ahead[0] * thrown.ahead, m.pos[1], m.pos[2] + ahead[2] * thrown.ahead];
                m.pitch = clamp(m.pitch + thrown.pitch, -89, 89); m.roll = (m.roll || 0) + thrown.roll;
            }
        }
        return m;
    }

    function bare(compiled, time) {
        const list = compiled.stretches;
        if (!list.length) return null;
        time = clamp(time, 0, compiled.duration);
        let s = list[list.length - 1];
        for (const candidate of list) { if (time < candidate.start + candidate.total) { s = candidate; break; } }
        const into = time - s.start, left = s.total - into;
        const rig = s.rig ? { rig: s.rig.kind, rigStrength: s.rig.strength } : {};
        let lids = 0;
        if (s.blinkOut && left < BLINK_CLOSE) lids = smooth(1 - left / BLINK_CLOSE);
        if (s.blinkIn && into < BLINK_OPEN) lids = Math.max(lids, 1 - smooth(into / BLINK_OPEN));
        if (s.wait) {
            const p = s.poses[0];
            return { world: s.world, ...p, pos: p.pos.slice(), roll: p.roll || 0, lids: Math.max(lids, p.lids || 0), relief: 1, mix: null, travelled: s.flownBefore, ...rig };
        }
        const x = paced(clamp(into / s.total, 0, 1), s.total, s.softIn, s.softOut);
        const pose = alongStretch(s, x);
        const piece = x * (s.lengths.length - 1), k = Math.min(s.lengths.length - 2, Math.floor(piece));
        const travelled = s.flownBefore + lerp(s.lengths[k], s.lengths[k + 1], piece - k);
        return {
            world: s.world, ...pose, lids: Math.max(lids, pose.lids || 0),
            relief: s.unfold ? smooth(into / UNFOLD_SECONDS) : 1,
            mix: s.mix && left < s.mix.seconds ? { world: s.mix.world, amount: smooth(1 - left / s.mix.seconds), turn: s.mix.turn, deep: !!s.mix.deep, via: s.mix.via } : null,
            travelled, ...rig,
        };
    }

    /* ---- a flight the user flew (recorded as it was) ----------------------------------------------------- */

    function takeAt(take, time) {
        const s = take.samples;
        if (!s || !s.length) return null;
        if (time <= s[0].t) return s[0];
        if (time >= s[s.length - 1].t) return s[s.length - 1];
        let low = 0, high = s.length - 1;
        while (high - low > 1) { const mid = (low + high) >> 1; if (s[mid].t <= time) low = mid; else high = mid; }
        const a = s[low], b = s[high];
        if (a.world !== b.world) return a;
        const t = (time - a.t) / Math.max(1e-6, b.t - a.t);
        const mix = a.mix && b.mix && a.mix.world === b.mix.world ? { world: a.mix.world, amount: lerp(a.mix.amount, b.mix.amount, t), turn: a.mix.turn, deep: !!a.mix.deep, via: a.mix.via } : (a.mix || null);
        return {
            world: a.world, pos: [0, 1, 2].map(j => lerp(a.pos[j], b.pos[j], t)),
            yaw: a.yaw + turn(a.yaw, b.yaw) * t, pitch: lerp(a.pitch, b.pitch, t), fov: lerp(a.fov, b.fov, t),
            roll: lerp(a.roll || 0, b.roll || 0, t), relief: lerp(a.relief, b.relief, t), mix,
            travelled: lerp(a.travelled || 0, b.travelled || 0, t), fade: lerp(a.fade || 0, b.fade || 0, t),
        };
    }

    /* ---- the figure for OpenPose ------------------------------------------------------------------------- */

    // nose, neck, right shoulder, elbow, wrist, left shoulder, elbow, wrist, right hip, knee, ankle,
    // left hip, knee, ankle, right eye, left eye, right ear, left ear. "Right" is the figure's own right.
    const LIMBS = [[1, 2], [1, 5], [2, 3], [3, 4], [5, 6], [6, 7], [1, 8], [8, 9], [9, 10], [1, 11], [11, 12], [12, 13], [1, 0], [0, 14], [14, 16], [0, 15], [15, 17]];
    const COLORS = [[255, 0, 0], [255, 85, 0], [255, 170, 0], [255, 255, 0], [170, 255, 0], [85, 255, 0], [0, 255, 0], [0, 255, 85], [0, 255, 170],
        [0, 255, 255], [0, 170, 255], [0, 85, 255], [0, 0, 255], [85, 0, 255], [170, 0, 255], [255, 0, 255], [255, 0, 170], [255, 0, 85]];

    // x: fractions of the figure's height, from its middle line; y: 0 at the top of the head, 1 at the soles
    const STANDING = [[0, 0.075], [0, 0.17], [-0.105, 0.18], [-0.135, 0.33], [-0.145, 0.47], [0.105, 0.18], [0.135, 0.33], [0.145, 0.47],
        [-0.062, 0.5], [-0.066, 0.73], [-0.07, 0.955], [0.062, 0.5], [0.066, 0.73], [0.07, 0.955], [-0.02, 0.058], [0.02, 0.058], [-0.045, 0.068], [0.045, 0.068]];
    const withJoints = changes => STANDING.map((p, i) => changes[i] ? changes[i].slice() : p.slice());
    const POSES = {
        standing: STANDING.map(p => p.slice()),
        walking: withJoints({ 3: [-0.15, 0.32], 4: [-0.1, 0.45], 6: [0.15, 0.32], 7: [0.2, 0.44], 9: [-0.045, 0.72], 10: [-0.11, 0.93], 12: [0.095, 0.715], 13: [0.07, 0.955] }),
        running: withJoints({ 0: [0.02, 0.085], 3: [-0.19, 0.27], 4: [-0.1, 0.2], 6: [0.17, 0.31], 7: [0.25, 0.4], 9: [-0.12, 0.68], 10: [-0.23, 0.83], 12: [0.13, 0.69], 13: [0.1, 0.94] }),
        waving: withJoints({ 6: [0.2, 0.15], 7: [0.25, 0.02] }),
        arms_up: withJoints({ 3: [-0.18, 0.08], 4: [-0.21, -0.05], 6: [0.18, 0.08], 7: [0.21, -0.05] }),
        hands_on_hips: withJoints({ 3: [-0.2, 0.33], 4: [-0.09, 0.47], 6: [0.2, 0.33], 7: [0.09, 0.47] }),
        dancing: withJoints({ 0: [0.02, 0.08], 3: [-0.22, 0.12], 4: [-0.27, -0.01], 6: [0.2, 0.3], 7: [0.3, 0.26], 9: [-0.1, 0.71], 10: [-0.07, 0.94], 12: [0.14, 0.7], 13: [0.24, 0.9] }),
        open_arms: withJoints({ 3: [-0.22, 0.2], 4: [-0.33, 0.19], 6: [0.22, 0.2], 7: [0.33, 0.19] }),
        sitting: withJoints({ 0: [0, 0.24], 1: [0, 0.33], 2: [-0.105, 0.34], 3: [-0.13, 0.48], 4: [-0.1, 0.6], 5: [0.105, 0.34], 6: [0.13, 0.48], 7: [0.1, 0.6],
            8: [-0.065, 0.63], 9: [-0.1, 0.72], 10: [-0.095, 0.955], 11: [0.065, 0.63], 12: [0.1, 0.72], 13: [0.095, 0.955], 14: [-0.02, 0.223], 15: [0.02, 0.223], 16: [-0.045, 0.233], 17: [0.045, 0.233] }),
    };

    /* A pose placed inside a frame: joints as fractions of the frame (x from its left, y from its top).
     * `size` is the figure's height as a share of the frame's, `aspect` the frame's width / height. */
    function placePose(name, aspect, size, mirror) {
        const pose = POSES[name] || POSES.standing;
        let top = Infinity, bottom = -Infinity;
        for (const p of pose) { top = Math.min(top, p[1]); bottom = Math.max(bottom, p[1]); }
        const tall = bottom - top + 0.09;                   // room for the top of the head and the soles
        const scale = size / tall, y0 = (1 - size) / 2 + (0.05 - top) * scale;
        return pose.map(p => [0.5 + (mirror ? -p[0] : p[0]) * scale / aspect, y0 + p[1] * scale]);
    }

    return {
        RAD, clamp, lerp, smooth, wrapDeg, turn,
        dirFromUv, uvFromDir, dirFromAngles, anglesFromDir, basis, viewProjection, project, rayThrough,
        DepthField, constrain, eased,
        RIGS, noise, drift, rigOffset, carried, jolted, BLINK_CLOSE, BLINK_OPEN,
        MIX_SECONDS, UNFOLD_SECONDS, SPREAD_SECONDS, compileTour, tourAt, takeAt, paced,
        swing, beyond, stood, mapped, pathWalked, pathShare, cutAt, shotAt, passAt, dialogueCuts, followCam, FOLLOW_WAYS, takeToKeys, feathered, farMask, gateMask,
        LIMBS, COLORS, POSES, placePose,
    };
});

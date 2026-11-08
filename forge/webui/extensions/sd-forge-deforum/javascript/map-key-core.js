// Map Key core: the pure logic of the 🗺️ Map Key editor (scripts/deforum_helpers/map_key_ui.py, javascript/map-key.js).
// Lanes are drawn as a magnitude per frame plus a sign, so a minus section turns the camera around without changing its
// speed. Each lane can follow the music, prompts can dip the Strength at their frame, every edit can be undone, and a
// camera path drawn on the map becomes Deforum's per-frame motion.
// There is no DOM here: the page uses window.MapKeyCore, and node tests it (tests/map_key_core.test.js).
(function (root) {
    'use strict';

    const VERSION = 1;
    const HEIGHT_SAMPLES = 201;
    const MAX_UNDO = 100;
    const BANDS = ['Full', 'Bass', 'Mids', 'Highs'];

    // decimals and neutral values match audio_keyframes.TARGETS, which the Python side uses to write the schedules
    const LANES = {
        path_speed: {kind: 'speed', neutral: 0, decimals: 3, max: 4,
                     en: 'Speed along the path', he: 'מהירות לאורך המסלול'},
        strength_schedule: {kind: 'strength', neutral: 0, decimals: 3, max: 1, en: 'Strength', he: 'Strength'},
        cfg_scale_schedule: {kind: 'cfg', neutral: 0, decimals: 2, max: 10, en: 'CFG scale', he: 'CFG'},
        translation_z: {kind: 'motion', neutral: 0, decimals: 3, max: 4, mode: '3D',
                        en: 'Forward / back · Translation Z', he: 'קדימה / אחורה · Translation Z'},
        translation_x: {kind: 'motion', neutral: 0, decimals: 3, max: 4,
                        en: 'Right / left · Translation X', he: 'ימינה / שמאלה · Translation X'},
        translation_y: {kind: 'motion', neutral: 0, decimals: 3, max: 4,
                        en: 'Up / down · Translation Y', he: 'למעלה / למטה · Translation Y'},
        rotation_3d_y: {kind: 'motion', neutral: 0, decimals: 3, max: 1, mode: '3D',
                        en: 'Pan · Rotation 3D Y', he: 'פנורמה · Rotation 3D Y'},
        rotation_3d_x: {kind: 'motion', neutral: 0, decimals: 3, max: 1, mode: '3D',
                        en: 'Tilt · Rotation 3D X', he: 'הטיה · Rotation 3D X'},
        rotation_3d_z: {kind: 'motion', neutral: 0, decimals: 3, max: 1, mode: '3D',
                        en: 'Roll · Rotation 3D Z', he: 'גלגול · Rotation 3D Z'},
        zoom: {kind: 'motion', neutral: 1, decimals: 4, max: 0.04, mode: '2D', en: 'Zoom in / out', he: 'זום פנימה / החוצה'},
        angle: {kind: 'motion', neutral: 0, decimals: 3, max: 1, mode: '2D', en: 'Turn · Angle', he: 'סיבוב · Angle'},
    };
    const ORDER = {
        '3D': ['translation_z', 'translation_x', 'translation_y', 'rotation_3d_y', 'rotation_3d_x', 'rotation_3d_z'],
        '2D': ['zoom', 'translation_x', 'translation_y', 'angle'],
    };
    const WRITABLE = ['strength_schedule', 'cfg_scale_schedule', 'zoom', 'angle', 'translation_x', 'translation_y',
                      'translation_z', 'rotation_3d_x', 'rotation_3d_y', 'rotation_3d_z'];

    // What + and - do on screen, measured with Deforum renders: [[plus en, plus he], [minus en, minus he]]
    function directionWords(key, mode) {
        switch (key) {
            case 'translation_z': return [['forward', 'קדימה'], ['back', 'אחורה']];
            case 'translation_x': return mode === '2D'
                ? [['picture right', 'התמונה ימינה'], ['picture left', 'התמונה שמאלה']]
                : [['camera right', 'המצלמה ימינה'], ['camera left', 'המצלמה שמאלה']];
            case 'translation_y': return [['camera up', 'המצלמה למעלה'], ['camera down', 'המצלמה למטה']];
            case 'rotation_3d_y': return [['pans right', 'פונה ימינה'], ['pans left', 'פונה שמאלה']];
            case 'rotation_3d_x': return [['tilts up', 'מסתכלת למעלה'], ['tilts down', 'מסתכלת למטה']];
            case 'rotation_3d_z':
            case 'angle': return [['counter-clockwise', 'נגד כיוון השעון'], ['clockwise', 'עם כיוון השעון']];
            case 'zoom': return [['in', 'פנימה'], ['out', 'החוצה']];
            case 'path_speed': return [['along the path', 'קדימה במסלול'], ['back along the path', 'אחורה במסלול']];
            default: return null;
        }
    }

    const clamp = (value, low, high) => Math.min(Math.max(value, low), high);
    const lerp = (a, b, t) => a + (b - a) * t;
    const degrees = (radians) => (radians * 180) / Math.PI;
    const signed = (key) => LANES[key] && (LANES[key].kind === 'motion' || LANES[key].kind === 'speed');

    function roundTo(value, decimals) {
        const scale = Math.pow(10, decimals);
        const rounded = Math.round(value * scale) / scale;
        return rounded === 0 ? 0 : rounded; // no -0
    }

    // 1, 2, 2.5, 5 times a power of ten: the top of a lane's range
    function niceCeil(value) {
        if (!(value > 0)) return 1;
        const base = Math.pow(10, Math.floor(Math.log10(value)));
        for (const step of [1, 2, 2.5, 5, 10]) {
            if (step * base >= value * (1 - 1e-9)) return Number((step * base).toPrecision(12));
        }
        return Number((10 * base).toPrecision(12));
    }

    // The next range up (+) or down (-) in 1, 2, 5 steps, for the lane's - / + buttons
    function niceNext(value, up) {
        const v = Math.max(Number(value) || 1, 1e-6);
        const exponent = Math.floor(Math.log10(v));
        const candidates = [];
        for (let e = exponent - 1; e <= exponent + 1; e++) {
            for (const step of [1, 2, 5]) candidates.push(Number((step * Math.pow(10, e)).toPrecision(12)));
        }
        if (up) return candidates.find((c) => c > v * 1.0001) || v * 2;
        return candidates.reverse().find((c) => c < v * 0.9999) || v / 2;
    }

    // --- per-frame values ----------------------------------------------------------------------------------

    // [[frame, value], ...] -> one value per frame, linear in between like Deforum; flat before the first and after the last
    function expandKnots(knots, frames) {
        const out = new Float64Array(frames);
        const sorted = (knots || []).map((k) => [Number(k[0]), Number(k[1])])
            .filter((k) => isFinite(k[0]) && isFinite(k[1])).sort((a, b) => a[0] - b[0]);
        if (!sorted.length) return out;
        let k = 0;
        for (let f = 0; f < frames; f++) {
            while (k + 1 < sorted.length && sorted[k + 1][0] <= f) k++;
            const [f0, v0] = sorted[k];
            if (f < f0 || k + 1 >= sorted.length) {
                out[f] = v0;
            } else {
                const [f1, v1] = sorted[k + 1];
                out[f] = f1 > f0 ? v0 + ((v1 - v0) * (f - f0)) / (f1 - f0) : v1;
            }
        }
        return out;
    }

    // The fewest keys whose linear in-betweens stay within one step (10^-decimals) of the drawn values: a drawn straight
    // line over 100 frames is 2 keys, not 100. The keys are the values rounded. Same algorithm as map_key.compact_keys.
    function compactKnots(values, decimals) {
        const n = values.length;
        if (!n) return [];
        const v = Array.from(values, (x) => roundTo(x, decimals));
        const tol = Math.pow(10, -decimals) + 1e-12;
        const knots = [[0, v[0]]];
        let a = 0;
        while (a < n - 1) {
            let lo = -Infinity;
            let hi = Infinity;
            let best = a + 1;
            for (let e = a + 1; e < n; e++) {
                const slope = (v[e] - v[a]) / (e - a);
                if (slope >= lo - 1e-12 && slope <= hi + 1e-12) best = e;
                lo = Math.max(lo, (values[e] - tol - v[a]) / (e - a));
                hi = Math.min(hi, (values[e] + tol - v[a]) / (e - a));
                if (lo > hi) break;
            }
            knots.push([best, v[best]]);
            a = best;
        }
        return knots;
    }

    // magnitude >= 0 and a sign per frame; frames at the neutral value keep the sign around them
    function splitSigned(values, neutral) {
        const n = values.length;
        const mag = new Float64Array(n);
        const sign = new Int8Array(n);
        let last = 0;
        for (let i = 0; i < n; i++) {
            const d = values[i] - neutral;
            mag[i] = Math.abs(d) < 1e-9 ? 0 : Math.abs(d);
            if (mag[i] > 0) last = d > 0 ? 1 : -1;
            sign[i] = last;
        }
        let first = 0;
        while (first < n && sign[first] === 0) first++;
        const fill = first < n ? sign[first] : 1;
        for (let i = 0; i < n && sign[i] === 0; i++) sign[i] = fill;
        return {mag, sign};
    }

    function signRuns(sign) {
        const runs = [];
        for (let i = 0; i < sign.length; i++) {
            if (i === 0 || sign[i] !== sign[i - 1]) runs.push([i, sign[i] < 0 ? -1 : 1]);
        }
        return runs;
    }

    function expandSignRuns(runs, frames) {
        const sign = new Int8Array(frames).fill(1);
        const sorted = (runs || []).map((r) => [Math.round(Number(r[0])), Number(r[1]) < 0 ? -1 : 1])
            .filter((r) => isFinite(r[0])).sort((a, b) => a[0] - b[0]);
        for (let k = 0; k < sorted.length; k++) {
            const end = k + 1 < sorted.length ? sorted[k + 1][0] : frames;
            for (let f = Math.max(0, sorted[k][0]); f < Math.min(end, frames); f++) sign[f] = sorted[k][1];
        }
        return sign;
    }

    // How much of the drawn speed plays at each frame: 1 - amount + amount * music, the music being the band's
    // loudness 0..1 ('even') or its 4th power, which mostly reacts to the peaks ('peaks'). null = the drawing as it is.
    function musicFactor(song, music, frames) {
        const amount = clamp(Number(music && music.amount) || 0, 0, 100) / 100;
        if (!song || !song.bands || !(amount > 0)) return null;
        const data = song.bands[music.band] || song.bands.Full;
        if (!data || !data.length) return null;
        const step = song.step || 1;
        const out = new Float64Array(frames);
        for (let f = 0; f < frames; f++) {
            const i = Math.floor(f / step);
            const x = i < data.length ? clamp(data[i] / 999, 0, 1) : 0;
            const shaped = music.curve === 'even' ? x : x * x * x * x;
            out[f] = 1 - amount + amount * shaped;
        }
        return out;
    }

    // Each prompt with a transition strength pulls Strength to it at its frame. The full dip holds for Cadence frames, so
    // a generated frame always gets it, and then comes back to the drawn value over `frames` frames.
    function strengthWithDips(base, prompts, cadence) {
        const out = Float64Array.from(base);
        const n = out.length;
        const hold = Math.max(1, Math.round(Number(cadence) || 1));
        for (const prompt of prompts || []) {
            const dip = prompt.dip;
            if (!dip || dip.strength === null || dip.strength === undefined || dip.strength === '' ||
                !isFinite(Number(dip.strength))) continue;
            const target = clamp(Number(dip.strength), 0, 1);
            const back = Math.max(0, Math.round(Number(dip.frames) || 0));
            for (let k = 0; k < hold + back; k++) {
                const f = prompt.frame + k;
                if (f < 0) continue;
                if (f >= n) break;
                const w = k < hold ? 1 : 1 - (k - hold + 1) / back; // back to the drawn value on the last of them
                out[f] = out[f] + (target - out[f]) * w;
            }
        }
        return out;
    }

    // --- state ---------------------------------------------------------------------------------------------

    function defaultPath() {
        return {enabled: false, points: [], closed: false, laps: 1, look: 'along', lookAhead: 8,
                heights: new Float64Array(HEIGHT_SAMPLES), heightMax: 0.5};
    }

    function peak(values) {
        let top = 0;
        for (let i = 0; i < values.length; i++) top = Math.max(top, Math.abs(values[i]));
        return top;
    }

    function fitRange(lane) {
        const spec = LANES[lane.key];
        if (spec.kind === 'strength') {
            lane.max = 1;
            return;
        }
        const top = peak(lane.mag);
        if (top > lane.max * (1 + 1e-9)) lane.max = niceCeil(top * 1.1);
    }

    function makeLane(key, values, frames) {
        const spec = LANES[key];
        const lane = {key, mag: new Float64Array(frames), sign: new Int8Array(frames).fill(1), original: null,
                      max: spec.max, write: false, edited: false, music: {amount: 0, band: 'Full', curve: 'peaks'}};
        if (values) {
            lane.original = Float64Array.from(values);
            if (signed(key)) {
                const split = splitSigned(values, spec.neutral);
                lane.mag = split.mag;
                lane.sign = split.sign;
            } else {
                lane.mag = Float64Array.from(values);
            }
            const top = peak(lane.mag);
            if (spec.kind !== 'strength' && top > 0) lane.max = niceCeil(top * 1.1);
        } else if (spec.kind === 'strength') {
            lane.mag.fill(0.65);
        } else if (spec.kind === 'cfg') {
            lane.mag.fill(7);
        }
        return lane;
    }

    function normalizeSong(song) {
        if (!song || !song.bands || !song.bands.Full) return null;
        return {name: song.name || '', fps: Number(song.fps) || 30, frames: song.frames | 0, step: song.step || 1,
                duration: Number(song.duration) || 0, bands: song.bands, beats: (song.beats || []).slice().sort((a, b) => a - b),
                parts: (song.parts || [0]).slice().sort((a, b) => a - b), path: song.path || ''};
    }

    function touch(state) {
        state.revision = (state.revision || 0) + 1;
        state.dirty = true;
    }

    // data: what map_key.load_state sends (frames, fps, mode, cadence, song, lanes {key: {knots}}, prompts, project)
    function createState(data) {
        data = data || {};
        const frames = Math.max(1, Math.round(Number(data.frames) || 1));
        const mode = data.mode === '2D' ? '2D' : '3D';
        const state = {
            version: VERSION, frames, fps: Number(data.fps) || 30, mode,
            cadence: Math.max(1, Math.round(Number(data.cadence) || 1)),
            song: normalizeSong(data.song), songPath: (data.song && data.song.path) || data.song_path || '',
            lanes: {}, prompts: [], unplaced: (data.unplaced || []).slice(), promptsEdited: false,
            promptsWritable: data.prompts_ok !== false, path: defaultPath(), shown: {cfg_scale_schedule: false},
            history: {undo: [], redo: []}, nextId: 1, revision: 0, dirty: false,
            // the live control plan (live_control.py): one step per line of its table, shown on the timeline
            plan: planEntries(data.plan),
            // Deforum's FOV per frame, for the level-keeping roll (read on every Load, never saved in a project)
            warp: {fov: data.warp && data.warp.fov ? expandKnots(data.warp.fov, frames) : null},
        };
        const lanes = data.lanes || {};
        for (const key of Object.keys(LANES)) {
            const source = lanes[key];
            state.lanes[key] = makeLane(key, source && source.knots ? expandKnots(source.knots, frames) : null, frames);
            if (source && source.text !== undefined) state.lanes[key].text = String(source.text);
        }
        // the camera path's speed starts as the forward speed already in Deforum
        const forward = mode === '2D'
            ? Array.from(laneValues(state, 'zoom'), (z) => (z - 1) * 100) : laneValues(state, 'translation_z');
        const speed = state.lanes.path_speed;
        const split = splitSigned(Float64Array.from(forward), 0);
        speed.mag = split.mag;
        speed.sign = split.sign;
        speed.max = niceCeil(Math.max(peak(split.mag) * 1.1, 1));
        state.prompts = (data.prompts || []).map((p) => ({
            id: state.nextId++, frame: clamp(Math.round(Number(p.frame) || 0), 0, frames - 1), text: String(p.text ?? ''),
            key: p.pinned ? String(p.key) : null, pinned: !!p.pinned, dip: null,
        }));
        sortPrompts(state);
        if (data.project) restoreProject(state, data.project);
        state.dirty = false;
        return state;
    }

    function laneOrder(state) {
        const keys = ['strength_schedule'];
        if (pathActive(state)) keys.push('path_speed');
        keys.push(...ORDER[state.mode]);
        if (state.shown.cfg_scale_schedule) keys.push('cfg_scale_schedule');
        return keys;
    }

    // The values Deforum gets for a lane: from the camera path when it drives the lane, else the drawing. Roll stays
    // yours, and the path adds the roll that keeps the horizon level where it climbs inside a bend.
    function laneValues(state, key) {
        const spec = LANES[key];
        const lane = state.lanes[key];
        const driven = key === 'path_speed' ? null : pathResult(state);
        if (driven && driven.values[key]) return driven.values[key];
        if (spec.kind === 'strength') return strengthWithDips(lane.mag, state.prompts, state.cadence);
        if (spec.kind === 'cfg') return Float64Array.from(lane.mag);
        const factor = musicFactor(state.song, lane.music, state.frames);
        const out = new Float64Array(state.frames);
        const roll = key === 'rotation_3d_z' && driven && hasRoll(driven) ? driven.roll : null;
        for (let f = 0; f < state.frames; f++) {
            out[f] = spec.neutral + lane.sign[f] * lane.mag[f] * (factor ? factor[f] : 1) + (roll ? roll[f] : 0);
        }
        return out;
    }

    function hasRoll(result) {
        for (let f = 0; f < result.roll.length; f++) {
            if (Math.abs(result.roll[f]) > 1e-9) return true;
        }
        return false;
    }

    // The path adds to the roll only in 3D, looking along a path that climbs or falls inside a bend
    function pathRolls(state) {
        const result = pathResult(state);
        return !!(result && hasRoll(result));
    }

    function isDriven(state, key) {
        return pathActive(state) && pathKeys(state.path, state.mode).includes(key);
    }

    // --- edits and undo ------------------------------------------------------------------------------------

    function clonePath(path) {
        return {enabled: path.enabled, points: path.points.map((p) => ({x: p.x, z: p.z})), closed: path.closed,
                laps: path.laps, look: path.look, lookAhead: path.lookAhead, heights: Float64Array.from(path.heights),
                heightMax: path.heightMax};
    }

    function clonePrompts(prompts) {
        return prompts.map((p) => Object.assign({}, p, {dip: p.dip ? Object.assign({}, p.dip) : null}));
    }

    function planEntries(list) {
        return (list || []).map((entry, index) => ({
            id: index + 1,
            at: Math.max(0, Math.round(Number(entry.at) || 0)),
            mode: typeof entry.mode === 'string' ? entry.mode : '',
            video: entry.video === null || entry.video === undefined ? null : clamp(Number(entry.video), 0, 1),
            frames: Math.max(0, Math.round(Number(entry.frames) || 0)),
            text: entry.at_text ? String(entry.at_text) : '',
        })).sort((a, b) => a.at - b.at);
    }

    function planStepById(state, id) {
        return state.plan.find((step) => step.id === id) || null;
    }

    function movePlanStep(state, id, frame) {
        const step = planStepById(state, id);
        const target = Math.max(0, Math.min(state.frames - 1, Math.round(frame)));
        if (!step || step.at === target) return false;
        step.at = target;
        step.text = '';            // it was moved by hand, so the music word it carried no longer holds
        state.plan.sort((a, b) => a.at - b.at);
        touch(state);
        return true;
    }

    function deletePlanStep(state, id) {
        const index = state.plan.findIndex((step) => step.id === id);
        if (index < 0) return false;
        state.plan.splice(index, 1);
        touch(state);
        return true;
    }

    function planPayload(state) {
        // what goes back to the bar's table: a step that sits exactly on a beat or a part says so again
        const beats = (state.song && state.song.beats) || [];
        const parts = (state.song && state.song.parts) || [];
        return state.plan.map((step) => {
            const beat = beats.indexOf(step.at);
            const part = parts.indexOf(step.at);
            const text = step.text || (beat >= 0 ? 'beat ' + (beat + 1) : part >= 0 ? 'part ' + (part + 1) : '');
            return {at: step.at, at_text: text, mode: step.mode, video: step.video, frames: step.frames};
        });
    }

    function snapshot(state, target) {
        if (target === 'plan') return state.plan.map((step) => Object.assign({}, step));
        if (target === 'prompts') {
            return {prompts: clonePrompts(state.prompts), edited: state.promptsEdited, nextId: state.nextId};
        }
        if (target === 'path') return clonePath(state.path);
        if (target === 'shown') return Object.assign({}, state.shown);
        const lane = state.lanes[target];
        return {mag: Float64Array.from(lane.mag), sign: Int8Array.from(lane.sign), max: lane.max, write: lane.write,
                edited: lane.edited, music: Object.assign({}, lane.music)};
    }

    function restoreSnapshot(state, target, snap) {
        if (target === 'plan') {
            state.plan = snap.map((step) => Object.assign({}, step));
            return;
        }
        if (target === 'prompts') {
            state.prompts = clonePrompts(snap.prompts);
            state.promptsEdited = snap.edited;
            state.nextId = Math.max(state.nextId, snap.nextId);
        } else if (target === 'path') {
            state.path = clonePath(snap);
        } else if (target === 'shown') {
            state.shown = Object.assign({}, snap);
        } else {
            const lane = state.lanes[target];
            lane.mag = Float64Array.from(snap.mag);
            lane.sign = Int8Array.from(snap.sign);
            lane.max = snap.max;
            lane.write = snap.write;
            lane.edited = snap.edited;
            lane.music = Object.assign({}, snap.music);
        }
    }

    function beginEdit(state, targets) {
        return targets.map((target) => [target, snapshot(state, target)]);
    }

    function endEdit(state, token, label) {
        const parts = token.map(([target, before]) => ({target, before, after: snapshot(state, target)}));
        state.history.undo.push({label: label || '', parts});
        if (state.history.undo.length > MAX_UNDO) state.history.undo.shift();
        state.history.redo.length = 0;
        touch(state);
    }

    function edit(state, targets, label, change) {
        const token = beginEdit(state, targets);
        const result = change();
        endEdit(state, token, label);
        return result;
    }

    function undo(state) {
        const entry = state.history.undo.pop();
        if (!entry) return null;
        for (const part of entry.parts) restoreSnapshot(state, part.target, part.before);
        state.history.redo.push(entry);
        touch(state);
        return entry;
    }

    function redo(state) {
        const entry = state.history.redo.pop();
        if (!entry) return null;
        for (const part of entry.parts) restoreSnapshot(state, part.target, part.after);
        state.history.undo.push(entry);
        touch(state);
        return entry;
    }

    function markEdited(lane) {
        lane.edited = true;
        lane.write = true;
    }

    // Sets every whole index between x0 and x1 on the straight line between the two values, so a fast mouse leaves
    // no holes. Returns [from, to] or null.
    function paintSegment(array, x0, v0, x1, v1, low, high) {
        if (x1 < x0) {
            [x0, x1] = [x1, x0];
            [v0, v1] = [v1, v0];
        }
        let from = Math.ceil(x0 - 1e-9);
        let to = Math.floor(x1 + 1e-9);
        if (from > to) from = to = Math.round((x0 + x1) / 2);
        from = Math.max(from, 0);
        to = Math.min(to, array.length - 1);
        for (let i = from; i <= to; i++) {
            const t = x1 > x0 ? clamp((i - x0) / (x1 - x0), 0, 1) : 0;
            array[i] = clamp(lerp(v0, v1, t), low, high);
        }
        return from <= to ? [from, to] : null;
    }

    // Draw on a lane at frames f0..f1 with (drawn) values v0..v1; the signs stay, so drawing over a minus part keeps it minus
    function paintLane(state, key, f0, v0, f1, v1) {
        const lane = state.lanes[key];
        const low = 0;
        const high = LANES[key].kind === 'strength' ? 1 : Infinity;
        const range = paintSegment(lane.mag, f0, v0, f1, v1, low, high);
        if (range) {
            markEdited(lane);
            touch(state);
        }
        return range;
    }

    function smoothRange(array, from, to, radius) {
        const copy = Float64Array.from(array);
        for (let i = from; i <= to; i++) {
            let sum = 0;
            let count = 0;
            for (let j = Math.max(0, i - radius); j <= Math.min(array.length - 1, i + radius); j++) {
                sum += copy[j];
                count++;
            }
            array[i] = sum / count;
        }
    }

    // An action on frames from..to of a lane: minus / plus / flip (the direction), half / double / smooth (the speed),
    // reset (motion stops; Strength and CFG go back to the values loaded from Deforum). Returns whether anything changed.
    function rangeOp(state, key, from, to, op) {
        const lane = state.lanes[key];
        const spec = LANES[key];
        if (!lane) return false;
        const a = clamp(Math.round(Math.min(from, to)), 0, state.frames - 1);
        const b = clamp(Math.round(Math.max(from, to)), 0, state.frames - 1);
        if (['minus', 'plus', 'flip'].includes(op) && !signed(key)) return false;
        for (let i = a; i <= b; i++) {
            if (op === 'minus') lane.sign[i] = -1;
            else if (op === 'plus') lane.sign[i] = 1;
            else if (op === 'flip') lane.sign[i] = lane.sign[i] < 0 ? 1 : -1;
            else if (op === 'half') lane.mag[i] *= 0.5;
            else if (op === 'double') lane.mag[i] = spec.kind === 'strength' ? Math.min(lane.mag[i] * 2, 1) : lane.mag[i] * 2;
            else if (op === 'reset') {
                if (signed(key)) {
                    lane.mag[i] = 0;
                    lane.sign[i] = 1;
                } else {
                    lane.mag[i] = lane.original ? lane.original[i] : lane.mag[i];
                }
            }
        }
        if (op === 'smooth') smoothRange(lane.mag, a, b, 3);
        else if (!['minus', 'plus', 'flip', 'half', 'double', 'reset'].includes(op)) return false;
        fitRange(lane);
        markEdited(lane);
        touch(state);
        return true;
    }

    // --- prompts -------------------------------------------------------------------------------------------

    function sortPrompts(state) {
        state.prompts.sort((a, b) => a.frame - b.frame);
    }

    function promptAt(state, frame) {
        return state.prompts.find((p) => p.frame === frame) || null;
    }

    function promptById(state, id) {
        return state.prompts.find((p) => p.id === id) || null;
    }

    function activePrompt(state, frame) {
        let active = null;
        for (const p of state.prompts) {
            if (p.frame <= frame) active = p;
        }
        return active || state.prompts[0] || null;
    }

    function addPrompt(state, frame, text) {
        const at = clamp(Math.round(frame), 0, state.frames - 1);
        if (promptAt(state, at)) return null;
        const prompt = {id: state.nextId++, frame: at, text: String(text ?? ''), key: null, pinned: false, dip: null};
        state.prompts.push(prompt);
        sortPrompts(state);
        state.promptsEdited = true;
        touch(state);
        return prompt;
    }

    // A prompt moves to a free frame only; one moved away from a formula key (max_f-1) gets a plain frame number
    function movePrompt(state, id, frame) {
        const prompt = promptById(state, id);
        if (!prompt) return false;
        const at = clamp(Math.round(frame), 0, state.frames - 1);
        if (prompt.frame === at) return true;
        if (promptAt(state, at)) return false;
        prompt.frame = at;
        prompt.pinned = false;
        prompt.key = null;
        sortPrompts(state);
        state.promptsEdited = true;
        touch(state);
        return true;
    }

    function deletePrompt(state, id) {
        const index = state.prompts.findIndex((p) => p.id === id);
        if (index < 0) return false;
        state.prompts.splice(index, 1);
        state.promptsEdited = true;
        touch(state);
        return true;
    }

    function setPromptText(state, id, text) {
        const prompt = promptById(state, id);
        if (!prompt || prompt.text === text) return false;
        prompt.text = String(text);
        state.promptsEdited = true;
        touch(state);
        return true;
    }

    // strength '' or null removes the dip. Dips change the Strength schedule, so its lane is written too.
    function setPromptDip(state, id, strength, frames) {
        const prompt = promptById(state, id);
        if (!prompt) return false;
        const value = strength === '' || strength === null || strength === undefined ? null : Number(strength);
        if (value === null || !isFinite(value)) {
            prompt.dip = null;
        } else {
            prompt.dip = {strength: clamp(value, 0, 1), frames: Math.max(0, Math.round(Number(frames) || 0))};
        }
        markEdited(state.lanes.strength_schedule);
        touch(state);
        return true;
    }

    function snapFrame(state, frame, snap, radius) {
        let best = Math.round(frame);
        if (state.song && snap && snap !== 'off') {
            const marks = snap === 'parts' ? state.song.parts : state.song.beats.concat(state.song.parts);
            let bestDistance = radius;
            for (const mark of marks) {
                const distance = Math.abs(mark - frame);
                if (distance <= bestDistance) {
                    bestDistance = distance;
                    best = mark;
                }
            }
        }
        return clamp(best, 0, state.frames - 1);
    }

    // --- the camera path -----------------------------------------------------------------------------------

    function segmentDistance(p, a, b) {
        const dx = b.x - a.x;
        const dz = b.z - a.z;
        const length2 = dx * dx + dz * dz;
        const t = length2 > 0 ? clamp(((p.x - a.x) * dx + (p.z - a.z) * dz) / length2, 0, 1) : 0;
        return Math.hypot(p.x - (a.x + t * dx), p.z - (a.z + t * dz));
    }

    // Ramer-Douglas-Peucker: the few points a hand-drawn stroke really needs
    function simplify(points, epsilon) {
        if (points.length < 3) return points.map((p) => ({x: p.x, z: p.z}));
        const keep = new Uint8Array(points.length);
        keep[0] = keep[points.length - 1] = 1;
        const stack = [[0, points.length - 1]];
        while (stack.length) {
            const [a, b] = stack.pop();
            let index = -1;
            let distance = epsilon;
            for (let i = a + 1; i < b; i++) {
                const d = segmentDistance(points[i], points[a], points[b]);
                if (d > distance) {
                    distance = d;
                    index = i;
                }
            }
            if (index >= 0) {
                keep[index] = 1;
                stack.push([a, index], [index, b]);
            }
        }
        return points.filter((_, i) => keep[i]).map((p) => ({x: p.x, z: p.z}));
    }

    // Centripetal Catmull-Rom (Barry-Goldman), which never loops or overshoots between close points
    function centripetal(p0, p1, p2, p3, u) {
        const knot = (a, b) => Math.max(Math.sqrt(Math.hypot(b.x - a.x, b.z - a.z)), 1e-6);
        const t0 = 0;
        const t1 = t0 + knot(p0, p1);
        const t2 = t1 + knot(p1, p2);
        const t3 = t2 + knot(p2, p3);
        const t = t1 + (t2 - t1) * u;
        const mix = (a, b, ta, tb) => {
            const w = (t - ta) / (tb - ta);
            return {x: a.x + (b.x - a.x) * w, z: a.z + (b.z - a.z) * w};
        };
        const a1 = mix(p0, p1, t0, t1);
        const a2 = mix(p1, p2, t1, t2);
        const a3 = mix(p2, p3, t2, t3);
        return mix(mix(a1, a2, t0, t2), mix(a2, a3, t1, t3), t1, t2);
    }

    // A smooth curve through the points: {points, index}, index[i] = where control point i is on the curve
    function catmullRom(points, closed, samples) {
        const n = points.length;
        const steps = Math.max(1, samples | 0);
        if (n === 0) return {points: [], index: []};
        if (n === 1) return {points: [{x: points[0].x, z: points[0].z}], index: [0]};
        const loop = closed && n >= 3;
        const get = (i) => {
            if (loop) return points[((i % n) + n) % n];
            if (i < 0) return {x: 2 * points[0].x - points[1].x, z: 2 * points[0].z - points[1].z};
            if (i >= n) return {x: 2 * points[n - 1].x - points[n - 2].x, z: 2 * points[n - 1].z - points[n - 2].z};
            return points[i];
        };
        const segments = loop ? n : n - 1;
        const out = [];
        const index = [];
        for (let s = 0; s < segments; s++) {
            index.push(out.length);
            const p0 = get(s - 1);
            const p1 = get(s);
            const p2 = get(s + 1);
            const p3 = get(s + 2);
            for (let j = 0; j < steps; j++) out.push(centripetal(p0, p1, p2, p3, j / steps));
        }
        const last = loop ? points[0] : points[n - 1];
        if (!loop) index.push(out.length);
        out.push({x: last.x, z: last.z});
        return {points: out, index};
    }

    function sampleProfile(heights, u) {
        const n = heights.length;
        if (!n) return 0;
        const x = clamp(u, 0, 1) * (n - 1);
        const i = Math.min(Math.floor(x), n - 2);
        return i < 0 ? heights[0] : lerp(heights[i], heights[i + 1], x - i);
    }

    // The path as a dense 3D polyline: map x (right), height y (up), map z (forward), and its length along the way
    function buildPath(path) {
        const segments = Math.max(1, path.points.length);
        const curve = catmullRom(path.points, path.closed, clamp(Math.ceil(2048 / segments), 16, 128));
        const pts = curve.points;
        const n = pts.length;
        const closed = !!path.closed && path.points.length >= 3;
        const cumH = new Float64Array(n);
        for (let i = 1; i < n; i++) cumH[i] = cumH[i - 1] + Math.hypot(pts[i].x - pts[i - 1].x, pts[i].z - pts[i - 1].z);
        const totalH = n ? cumH[n - 1] : 0;
        const x = new Float64Array(n);
        const y = new Float64Array(n);
        const z = new Float64Array(n);
        const cum = new Float64Array(n);
        for (let i = 0; i < n; i++) {
            x[i] = pts[i].x;
            z[i] = pts[i].z;
            y[i] = sampleProfile(path.heights, totalH > 0 ? cumH[i] / totalH : 0);
            if (i > 0) cum[i] = cum[i - 1] + Math.hypot(x[i] - x[i - 1], y[i] - y[i - 1], z[i] - z[i - 1]);
        }
        const pointU = curve.index.map((i) => (totalH > 0 ? cumH[i] / totalH : 0));
        return {x, y, z, cum, total: n ? cum[n - 1] : 0, closed, pointU};
    }

    function pointAt(built, s) {
        const n = built.cum.length;
        if (!n) return {x: 0, y: 0, z: 0};
        if (built.closed && built.total > 0) s = ((s % built.total) + built.total) % built.total;
        else s = clamp(s, 0, built.total);
        let lo = 0;
        let hi = n - 1;
        while (hi - lo > 1) {
            const mid = (lo + hi) >> 1;
            if (built.cum[mid] <= s) lo = mid;
            else hi = mid;
        }
        const span = built.cum[hi] - built.cum[lo];
        const t = span > 0 ? clamp((s - built.cum[lo]) / span, 0, 1) : 0;
        return {x: lerp(built.x[lo], built.x[hi], t), y: lerp(built.y[lo], built.y[hi], t), z: lerp(built.z[lo], built.z[hi], t)};
    }

    // Where the camera looks at distance s: along the chord from s - w to s + w (w is the look-ahead)
    function headingAt(built, s, w) {
        const a = pointAt(built, s - w);
        const b = pointAt(built, s + w);
        const dx = b.x - a.x;
        const dy = b.y - a.y;
        const dz = b.z - a.z;
        return {yaw: Math.atan2(dx, dz), pitch: Math.atan2(dy, Math.hypot(dx, dz))};
    }

    // Centred moving average whose window shrinks at the ends, so the first and last values (and the total turn) stay
    // exact, and a steady turn stays steady
    function smoothSymmetric(values, radius) {
        const r = Math.max(0, Math.round(Number(radius) || 0));
        const n = values.length;
        if (!r || n < 3) return;
        const prefix = new Float64Array(n + 1);
        for (let i = 0; i < n; i++) prefix[i + 1] = prefix[i] + values[i];
        for (let i = 0; i < n; i++) {
            const k = Math.min(r, i, n - 1 - i);
            values[i] = (prefix[i + k + 1] - prefix[i - k]) / (2 * k + 1);
        }
    }

    function unwrap(angles) {
        for (let i = 1; i < angles.length; i++) {
            let d = angles[i] - angles[i - 1];
            while (d > Math.PI) d -= 2 * Math.PI;
            while (d < -Math.PI) d += 2 * Math.PI;
            angles[i] = angles[i - 1] + d;
        }
    }

    // World motion (x right, y up, z forward on the map) seen from a camera turned by yaw (right +) and pitch (up +)
    function toCamera(dx, dy, dz, yaw, pitch) {
        const sy = Math.sin(yaw);
        const cy = Math.cos(yaw);
        const sp = Math.sin(pitch);
        const cp = Math.cos(pitch);
        return {
            x: dx * cy - dz * sy,
            y: -dx * sy * sp + dy * cp - dz * cy * sp,
            z: dx * sy * cp + dy * sp + dz * cy * cp,
        };
    }

    function pathKeys(path, mode) {
        if (mode === '2D') return ['zoom', 'translation_x', 'translation_y'];
        return path.look === 'forward' ? ['translation_x', 'translation_y', 'translation_z']
            : ['translation_x', 'translation_y', 'translation_z', 'rotation_3d_x', 'rotation_3d_y'];
    }

    function pathActive(state) {
        return !!(state.path && state.path.enabled && state.path.points.length >= 2);
    }

    // The camera travels the path at the signed speed of each frame (Deforum units, like Translation Z), and reaches its
    // end at the furthest point of the song (or after Laps rounds of a closed path). Each frame's motion is seen from the
    // camera, since Deforum moves every frame relative to the one before:
    //   3D: right -> Translation X +, up -> Translation Y +, forward -> Translation Z +, turning right -> Rotation 3D Y +,
    //       looking up -> Rotation 3D X + (degrees per frame; one round of a closed path is 360°)
    //   2D: forward -> Zoom 1 + z/100, right -> Translation X - (in 2D X+ slides the picture right), up -> Translation Y +
    function pathToMotion(path, speed, frames, mode, rollScale) {
        const built = buildPath(path);
        const keys = pathKeys(path, mode);
        const values = {};
        for (const key of keys) values[key] = new Float64Array(frames).fill(LANES[key].neutral);
        const result = {values, keys, built, scale: 0, x: new Float64Array(frames), y: new Float64Array(frames),
                        z: new Float64Array(frames), yaw: new Float64Array(frames), pitch: new Float64Array(frames),
                        roll: new Float64Array(frames)};
        const along = path.look !== 'forward' && mode !== '2D';
        if (!(built.total > 1e-9) || frames < 1) return result;
        const progress = new Float64Array(frames);
        let maxS = 0;
        let minS = 0;
        let travel = 0;
        for (let f = 1; f < frames; f++) {
            const v = Number(speed[f]) || 0;
            progress[f] = progress[f - 1] + v;
            maxS = Math.max(maxS, progress[f]);
            minS = Math.min(minS, progress[f]);
            travel += Math.abs(v);
        }
        const laps = built.closed ? Math.max(0.05, Number(path.laps) || 1) : 1;
        const reach = built.closed ? Math.max(maxS, -minS) : (maxS > 1e-12 ? maxS : -minS);
        const scale = reach > 1e-12 ? reach / (built.total * laps) : 0; // Deforum units per map unit
        const offset = !built.closed && !(maxS > 1e-12) && minS < 0 ? built.total : 0; // only backward: from the end
        const meanSpeed = frames > 1 ? travel / (frames - 1) : 0;
        // the direction of the path right where the camera is (half a frame of travel each way); the look-ahead
        // then smooths the turns over frames
        const window = Math.max(built.total * 1e-4, scale > 0 ? (0.5 * meanSpeed) / scale : 0);
        const start = headingAt(built, offset, window);
        for (let f = 0; f < frames; f++) {
            const q = scale > 0 ? offset + progress[f] / scale : offset;
            const p = pointAt(built, q);
            result.x[f] = p.x;
            result.y[f] = p.y;
            result.z[f] = p.z;
            if (along) {
                const heading = headingAt(built, q, window);
                result.yaw[f] = heading.yaw;
                result.pitch[f] = heading.pitch;
            } else {
                result.yaw[f] = start.yaw;
                result.pitch[f] = 0;
            }
        }
        unwrap(result.yaw);
        if (along) {
            smoothSymmetric(result.yaw, path.lookAhead);
            smoothSymmetric(result.pitch, path.lookAhead);
        }
        result.scale = scale;
        for (let f = 1; f < frames; f++) {
            const local = toCamera((result.x[f] - result.x[f - 1]) * scale, (result.y[f] - result.y[f - 1]) * scale,
                                   (result.z[f] - result.z[f - 1]) * scale, (result.yaw[f] + result.yaw[f - 1]) / 2,
                                   (result.pitch[f] + result.pitch[f - 1]) / 2);
            if (mode === '2D') {
                values.zoom[f] = 1 + local.z / 100;
                values.translation_x[f] = -local.x;
                values.translation_y[f] = local.y;
            } else {
                values.translation_x[f] = local.x;
                values.translation_y[f] = local.y;
                values.translation_z[f] = local.z;
                if (along) {
                    // Deforum turns the camera about its own axes. Turning about the world's up while tilted by p is
                    // a pan of cos(p) about the camera's up and a roll of sin(p) about its view axis; Rotation 3D Z +
                    // turns the picture counter-clockwise, which is the camera rolling the other way. So a climb inside
                    // a bend keeps the horizon level. rollScale matches Deforum's warp (see rollScales).
                    const turn = degrees(result.yaw[f] - result.yaw[f - 1]);
                    const tilt = (result.pitch[f] + result.pitch[f - 1]) / 2;
                    const scale = rollScale === undefined ? 1 : typeof rollScale === 'number' ? rollScale : rollScale[f];
                    values.rotation_3d_y[f] = turn * Math.cos(tilt);
                    values.rotation_3d_x[f] = degrees(result.pitch[f] - result.pitch[f - 1]);
                    result.roll[f] = -turn * Math.sin(tilt) * scale;
                }
            }
        }
        if (frames > 1) {
            for (const key of keys) values[key][0] = values[key][1]; // Deforum does not move frame 0
            result.roll[0] = result.roll[1];
        }
        return result;
    }

    // A camera turned by pan (about its own up axis, right +), tilt (about its own right axis, up +) and roll (about its
    // view axis; + rolls the camera clockwise, so the picture turns counter-clockwise like Deforum's Rotation 3D Z +):
    // its right, up and forward axes in the world
    function turnCamera(axes, pan, tilt, roll) {
        const [r, u, f] = axes;
        const cp = Math.cos(pan);
        const sp = Math.sin(pan);
        const f1 = [f[0] * cp + r[0] * sp, f[1] * cp + r[1] * sp, f[2] * cp + r[2] * sp];
        const r1 = [r[0] * cp - f[0] * sp, r[1] * cp - f[1] * sp, r[2] * cp - f[2] * sp];
        const ct = Math.cos(tilt);
        const st = Math.sin(tilt);
        const f2 = [f1[0] * ct + u[0] * st, f1[1] * ct + u[1] * st, f1[2] * ct + u[2] * st];
        const u2 = [u[0] * ct - f1[0] * st, u[1] * ct - f1[1] * st, u[2] * ct - f1[2] * st];
        const cr = Math.cos(roll || 0);
        const sr = Math.sin(roll || 0);
        const r3 = [r1[0] * cr - u2[0] * sr, r1[1] * cr - u2[1] * sr, r1[2] * cr - u2[2] * sr];
        const u3 = [u2[0] * cr + r1[0] * sr, u2[1] * cr + r1[1] * sr, u2[2] * cr + r1[2] * sr];
        return [r3, u3, f2];
    }

    // Where the lanes as they are take the camera, for the 3D view. Like Deforum, every frame moves and turns the camera
    // along its own axes. Positions in Deforum units, angles in radians.
    function integrateMotion(state) {
        const n = state.frames;
        const out = {x: new Float64Array(n), y: new Float64Array(n), z: new Float64Array(n), yaw: new Float64Array(n),
                     pitch: new Float64Array(n)};
        const twoD = state.mode === '2D';
        const get = (key) => laneValues(state, key);
        const forward = twoD ? Array.from(get('zoom'), (v) => (v - 1) * 100) : get('translation_z');
        const right = twoD ? Array.from(get('translation_x'), (v) => -v) : get('translation_x');
        const up = get('translation_y');
        const pan = twoD ? null : get('rotation_3d_y');
        const tilt = twoD ? null : get('rotation_3d_x');
        const roll = twoD ? null : get('rotation_3d_z');
        let axes = [[1, 0, 0], [0, 1, 0], [0, 0, 1]];
        const position = [0, 0, 0];
        for (let f = 1; f < n; f++) {
            const a = pan ? (pan[f] * Math.PI) / 180 : 0;
            const b = tilt ? (tilt[f] * Math.PI) / 180 : 0;
            const c = roll ? (roll[f] * Math.PI) / 180 : 0;
            const [r, u, fw] = turnCamera(axes, a / 2, b / 2, c / 2); // halfway through the frame's turn
            for (let i = 0; i < 3; i++) position[i] += right[f] * r[i] + up[f] * u[i] + forward[f] * fw[i];
            axes = turnCamera(axes, a, b, c);
            out.x[f] = position[0];
            out.y[f] = position[1];
            out.z[f] = position[2];
            const ahead = axes[2];
            out.yaw[f] = Math.atan2(ahead[0], ahead[2]);
            out.pitch[f] = Math.asin(clamp(ahead[1], -1, 1));
        }
        unwrap(out.yaw);
        return out;
    }

    // How strongly the level-keeping roll must act in Deforum's 3D warp. Deforum projects each pixel's point (x, y, 1)
    // with a focal length of 1/tan(FOV/2), which makes turning away from the picture's centre that much stronger than a
    // real camera's, so the roll needs the same factor. Measured on Deforum's own renders of a path climbing 19° inside
    // a 33° bend (the horizon at the end, and on average over the render):
    //   diffusion render with depth warping: no roll 7.50° / 3.90°, geometric roll 2.19° / 1.37°, this factor 0.00° / 0.16°
    //   motion preview (no depth):            no roll 7.86°,         geometric roll 2.33°,         this factor 0.07°
    function rollScales(state) {
        const warp = state.warp || {};
        const out = new Float64Array(state.frames);
        for (let f = 0; f < state.frames; f++) {
            const fov = warp.fov && warp.fov.length ? clamp(Number(warp.fov[Math.min(f, warp.fov.length - 1)]) || 70, 1, 179) : 70;
            out[f] = 1 / Math.tan(((fov / 2) * Math.PI) / 180);
        }
        return out;
    }

    function pathResult(state) {
        if (!pathActive(state)) return null;
        if (state._path && state._path.revision === state.revision) return state._path.result;
        const result = pathToMotion(state.path, laneValues(state, 'path_speed'), state.frames, state.mode, rollScales(state));
        state._path = {revision: state.revision, result};
        return result;
    }

    function nearestPoint(points, p, maxDistance) {
        let best = -1;
        let bestDistance = maxDistance;
        points.forEach((q, i) => {
            const d = Math.hypot(q.x - p.x, q.z - p.z);
            if (d <= bestDistance) {
                bestDistance = d;
                best = i;
            }
        });
        return best;
    }

    // Where a new point goes: into the path segment nearest to it (or at the end, when that is nearer)
    function insertPoint(points, closed, p) {
        if (points.length < 2) {
            points.push({x: p.x, z: p.z});
            return points.length - 1;
        }
        let bestIndex = points.length;
        let bestDistance = Math.hypot(points[points.length - 1].x - p.x, points[points.length - 1].z - p.z);
        const segments = closed ? points.length : points.length - 1;
        for (let i = 0; i < segments; i++) {
            const d = segmentDistance(p, points[i], points[(i + 1) % points.length]);
            if (d < bestDistance) {
                bestDistance = d;
                bestIndex = i + 1;
            }
        }
        if (Math.hypot(points[0].x - p.x, points[0].z - p.z) < bestDistance && !closed) bestIndex = 0;
        points.splice(bestIndex, 0, {x: p.x, z: p.z});
        return bestIndex;
    }

    // A smooth bump in the height profile around u (Shift + drag of a point in the 3D view)
    function raiseHeight(path, u, amount, width) {
        const n = path.heights.length;
        const sigma = Math.max(width || 0.08, 1e-3);
        for (let i = 0; i < n; i++) {
            const d = i / (n - 1) - u;
            path.heights[i] = clamp(path.heights[i] + amount * Math.exp(-(d * d) / (sigma * sigma)), -path.heightMax, path.heightMax);
        }
    }

    // Copies what the path does into the lanes, so they can be edited by hand, and turns the path off
    function bake(state) {
        const result = pathResult(state);
        if (!result) return false;
        const keys = result.keys.slice();
        const baked = {};
        for (const key of keys) baked[key] = result.values[key];
        if (hasRoll(result)) {
            keys.push('rotation_3d_z');
            baked.rotation_3d_z = laneValues(state, 'rotation_3d_z'); // your roll with the path's level-keeping roll
        }
        edit(state, keys.concat(['path']), 'bake', () => {
            for (const key of keys) {
                const lane = state.lanes[key];
                const split = splitSigned(baked[key], LANES[key].neutral);
                lane.mag = split.mag;
                lane.sign = split.sign;
                lane.music = Object.assign({}, lane.music, {amount: 0});
                fitRange(lane);
                markEdited(lane);
            }
            state.path.enabled = false;
        });
        return true;
    }

    // --- saving and applying -------------------------------------------------------------------------------

    function serialize(state) {
        const lanes = {};
        for (const key of Object.keys(state.lanes)) {
            const lane = state.lanes[key];
            lanes[key] = {mag: compactKnots(lane.mag, LANES[key].decimals + 2), sign: signRuns(lane.sign), max: lane.max,
                          write: lane.write, edited: lane.edited, music: Object.assign({}, lane.music)};
        }
        const path = state.path;
        return {
            version: VERSION, frames: state.frames, fps: state.fps, mode: state.mode, cadence: state.cadence,
            song_path: state.songPath || '', lanes,
            prompts: state.prompts.map((p) => ({frame: p.frame, text: p.text, key: p.key, pinned: p.pinned,
                                                dip: p.dip ? {strength: p.dip.strength, frames: p.dip.frames} : null})),
            prompts_edited: state.promptsEdited, unplaced: state.unplaced,
            path: {enabled: path.enabled, points: path.points.map((p) => ({x: roundTo(p.x, 5), z: roundTo(p.z, 5)})),
                   closed: path.closed, laps: path.laps, look: path.look, lookAhead: path.lookAhead,
                   heights: compactKnots(path.heights, 5), heightMax: path.heightMax},
            shown: Object.assign({}, state.shown),
        };
    }

    // A saved project replaces the lanes, prompts and path (cut or held at the end when Max frames changed since)
    function restoreProject(state, project) {
        if (!project || typeof project !== 'object') return;
        const frames = state.frames;
        for (const [key, saved] of Object.entries(project.lanes || {})) {
            const lane = state.lanes[key];
            if (!lane || !saved) continue;
            lane.mag = expandKnots(saved.mag, frames);
            for (let i = 0; i < frames; i++) lane.mag[i] = Math.max(0, lane.mag[i]);
            lane.sign = expandSignRuns(saved.sign, frames);
            lane.max = Number(saved.max) > 0 ? Number(saved.max) : lane.max;
            lane.write = !!saved.write;
            lane.edited = !!saved.edited;
            lane.music = Object.assign({amount: 0, band: 'Full', curve: 'peaks'}, saved.music || {});
            if (!BANDS.includes(lane.music.band)) lane.music.band = 'Full';
            fitRange(lane);
        }
        if (Array.isArray(project.prompts)) {
            state.prompts = [];
            const aside = [];
            for (const p of project.prompts) {
                const frame = Math.round(Number(p.frame));
                if (!isFinite(frame) || frame < 0) continue;
                if (frame >= frames || promptAt(state, frame)) {
                    aside.push({key: p.pinned && p.key ? String(p.key) : String(frame), text: String(p.text ?? '')});
                    continue;
                }
                state.prompts.push({id: state.nextId++, frame, text: String(p.text ?? ''), key: p.pinned ? String(p.key) : null,
                                    pinned: !!p.pinned, dip: p.dip && p.dip.strength !== null && p.dip.strength !== undefined
                                        ? {strength: clamp(Number(p.dip.strength), 0, 1), frames: Math.max(0, Math.round(Number(p.dip.frames) || 0))}
                                        : null});
            }
            sortPrompts(state);
            state.promptsEdited = !!project.prompts_edited || aside.length > 0;
            state.unplaced = (Array.isArray(project.unplaced) ? project.unplaced.slice() : []).concat(aside);
        }
        const saved = project.path;
        if (saved && typeof saved === 'object') {
            const path = defaultPath();
            path.enabled = !!saved.enabled;
            path.points = (saved.points || []).filter((p) => isFinite(p.x) && isFinite(p.z)).map((p) => ({x: Number(p.x), z: Number(p.z)}));
            path.closed = !!saved.closed;
            path.laps = Number(saved.laps) > 0 ? Number(saved.laps) : 1;
            path.look = saved.look === 'forward' ? 'forward' : 'along';
            path.lookAhead = Math.max(0, Number(saved.lookAhead) || 0);
            path.heightMax = Number(saved.heightMax) > 0 ? Number(saved.heightMax) : 0.5;
            if (saved.heights) path.heights = expandKnots(saved.heights, HEIGHT_SAMPLES);
            state.path = path;
        }
        if (project.shown) state.shown = Object.assign({cfg_scale_schedule: false}, project.shown);
        touch(state);
    }

    // What Apply sends to Python: the final values of the lanes to write and, when they were changed, the prompts
    function payload(state) {
        const lanes = {};
        const rolls = pathRolls(state);
        for (const key of WRITABLE) {
            const lane = state.lanes[key];
            if (!lane || !(lane.write || isDriven(state, key) || (key === 'rotation_3d_z' && rolls))) continue;
            lanes[key] = Array.from(laneValues(state, key), (v) => roundTo(v, LANES[key].decimals));
        }
        const prompts = state.promptsEdited && state.promptsWritable
            ? state.prompts.map((p) => ({frame: p.frame, text: p.text, key: p.pinned ? p.key : null})) : null;
        return {version: VERSION, frames: state.frames, lanes, prompts, unplaced: state.unplaced, project: serialize(state)};
    }

    const api = {
        VERSION, HEIGHT_SAMPLES, BANDS, LANES, ORDER, WRITABLE, directionWords, clamp, roundTo, niceCeil, niceNext,
        expandKnots, compactKnots, splitSigned, signRuns, expandSignRuns, musicFactor, strengthWithDips,
        createState, laneOrder, laneValues, isDriven, touch, snapshot, beginEdit, endEdit, edit, undo, redo, markEdited,
        paintSegment, paintLane, rangeOp, fitRange, signed,
        promptAt, promptById, activePrompt, addPrompt, movePrompt, deletePrompt, setPromptText, setPromptDip, snapFrame,
        planEntries, planStepById, movePlanStep, deletePlanStep, planPayload,
        simplify, catmullRom, buildPath, pointAt, headingAt, toCamera, pathKeys, pathActive, pathToMotion, pathResult,
        integrateMotion, pathRolls, rollScales,
        nearestPoint, insertPoint, raiseHeight, sampleProfile, bake, serialize, restoreProject, payload,
    };
    if (typeof module === 'object' && module.exports) module.exports = api;
    else root.MapKeyCore = api;
})(typeof window !== 'undefined' ? window : globalThis);

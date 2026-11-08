// Tests for javascript/map-key-core.js (the 🗺️ Map Key editor's logic). No browser needed:
//   node --test extensions/sd-forge-deforum/tests/map_key_core.test.js
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const Core = require(path.join(__dirname, '..', 'javascript', 'map-key-core.js'));

const near = (actual, expected, tolerance, what) =>
    assert.ok(Math.abs(actual - expected) <= tolerance, `${what || 'value'}: ${actual} is not within ${tolerance} of ${expected}`);
const flat = (frames, value) => [[0, value], [frames - 1, value]];

function makeState(frames, lanes, extra) {
    const knots = {};
    for (const [key, value] of Object.entries(lanes || {})) knots[key] = {knots: Array.isArray(value) ? value : flat(frames, value)};
    return Core.createState(Object.assign({frames, fps: 30, mode: '3D', cadence: 1, lanes: knots}, extra || {}));
}

function song(frames, loudness) {
    const full = Array.from({length: frames}, (_, f) => Math.round(loudness(f) * 999));
    return {name: 'test', fps: 30, frames, step: 1, duration: frames / 30,
            bands: {Full: full, Bass: full, Mids: full, Highs: full}, beats: [30, 60, 90], parts: [0, 50]};
}

test('knots expand like Deforum and a drawn straight line compacts to its two ends', () => {
    assert.deepEqual(Array.from(Core.expandKnots([[0, 0], [10, 1]], 12)),
                     [0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1, 1].map((v, i) => (i <= 10 ? i / 10 : 1)));
    const ramp = Array.from({length: 100}, (_, i) => 0.5 + i * 0.013);
    assert.deepEqual(Core.compactKnots(ramp, 3), [[0, 0.5], [99, Core.roundTo(0.5 + 99 * 0.013, 3)]]);
    assert.deepEqual(Core.compactKnots(new Float64Array(50).fill(1.75), 3), [[0, 1.75], [49, 1.75]]);
    const wave = Array.from({length: 300}, (_, i) => Math.sin(i / 7) * 2 + (i % 13 === 0 ? 0.5 : 0));
    const knots = Core.compactKnots(wave, 3);
    const back = Core.expandKnots(knots, 300);
    wave.forEach((v, i) => near(back[i], v, 0.001 + 1e-9, `frame ${i}`));
    assert.ok(knots.length < 300, 'a curve still loses some keys');
    // a line drawn with the mouse: its slope is not a round number, and it still needs only its ends
    const drawn = Array.from({length: 320}, (_, i) => (i < 44 || i > 255 ? 0 : 0.0033 + (i - 44) * 0.0038517));
    const lineKnots = Core.compactKnots(drawn, 3);
    assert.ok(lineKnots.length <= 6, `a drawn line is its corners only: ${JSON.stringify(lineKnots)}`);
    const lineBack = Core.expandKnots(lineKnots, 320);
    drawn.forEach((v, i) => near(lineBack[i], v, 0.001 + 1e-9, `line frame ${i}`));
});

test('split into speed and direction: frames at 0 keep the direction around them', () => {
    const {mag, sign} = Core.splitSigned([0, 0, 2, -1, 0, -3, 1], 0);
    assert.deepEqual(Array.from(mag), [0, 0, 2, 1, 0, 3, 1]);
    assert.deepEqual(Array.from(sign), [1, 1, 1, -1, -1, -1, 1]);
    const zoom = Core.splitSigned([1, 1.02, 0.98], 1);
    assert.deepEqual(Array.from(zoom.sign), [1, 1, -1]);
    near(zoom.mag[2], 0.02, 1e-12);
});

test('a fast stroke fills every frame between the mouse samples', () => {
    const array = new Float64Array(20);
    assert.deepEqual(Core.paintSegment(array, 2.4, 1, 11.6, 4, 0, 10), [3, 11]);
    for (let i = 3; i <= 11; i++) assert.ok(array[i] > 0, `frame ${i} was painted`);
    assert.equal(array[2], 0);
    assert.equal(array[12], 0);
    near(array[7], 1 + 3 * ((7 - 2.4) / 9.2), 1e-12);
    const state = makeState(40, {translation_z: 1});
    Core.rangeOp(state, 'translation_z', 10, 19, 'minus');
    Core.paintLane(state, 'translation_z', 5, 3, 25, 3);
    const values = Core.laneValues(state, 'translation_z');
    assert.equal(values[12], -3, 'drawing over a minus part keeps it going back');
    assert.equal(values[22], 3);
    assert.equal(Core.paintLane(state, 'strength_schedule', 0, 5, 3, 5)[1], 3);
    assert.equal(Core.laneValues(state, 'strength_schedule')[2], 1, 'Strength stays within 0..1');
});

test('minus turns the camera around only in the selected part, and the lane is written from then on', () => {
    const state = makeState(60, {translation_z: 1.5, strength_schedule: 0.65});
    assert.equal(state.lanes.translation_z.write, false);
    assert.ok(Core.rangeOp(state, 'translation_z', 20, 29, 'minus'));
    const values = Core.laneValues(state, 'translation_z');
    values.forEach((v, f) => assert.equal(v, f >= 20 && f <= 29 ? -1.5 : 1.5, `frame ${f}`));
    assert.equal(state.lanes.translation_z.write, true);
    Core.rangeOp(state, 'translation_z', 25, 34, 'flip');
    const flipped = Core.laneValues(state, 'translation_z');
    assert.deepEqual([flipped[24], flipped[25], flipped[29], flipped[30], flipped[34], flipped[35]], [-1.5, 1.5, 1.5, -1.5, -1.5, 1.5]);
    assert.equal(Core.rangeOp(state, 'strength_schedule', 0, 10, 'minus'), false, 'Strength has no direction');
    Core.rangeOp(state, 'translation_z', 0, 59, 'double');
    assert.ok(state.lanes.translation_z.max >= 3, 'the range grows to show the faster speed');
    Core.rangeOp(state, 'translation_z', 50, 59, 'reset');
    assert.equal(Core.laneValues(state, 'translation_z')[55], 0);
    const payload = Core.payload(state);
    assert.deepEqual(Object.keys(payload.lanes), ['translation_z'], 'untouched lanes are not written');
    assert.equal(payload.prompts, null, 'unchanged prompts are not written');
});

test('music follow: 0 % is the drawing, 100 % peaks is the drawing times loudness^4', () => {
    const frames = 40;
    const state = makeState(frames, {translation_z: 2}, {song: song(frames, (f) => f / (frames - 1))});
    assert.equal(Core.musicFactor(state.song, state.lanes.translation_z.music, frames), null);
    state.lanes.translation_z.music = {amount: 100, band: 'Bass', curve: 'peaks'};
    const values = Core.laneValues(state, 'translation_z');
    near(values[0], 0, 1e-12);
    near(values[frames - 1], 2, 1e-12);
    const x = Math.round((20 / (frames - 1)) * 999) / 999;
    near(values[20], 2 * x ** 4, 1e-9);
    state.lanes.translation_z.music = {amount: 60, band: 'Full', curve: 'even'};
    near(Core.laneValues(state, 'translation_z')[0], 0.8, 1e-9, '40 % of the speed in silence');
});

test('a prompt dip lands on a generated frame at every cadence and moves with its prompt', () => {
    const frames = 60;
    const state = makeState(frames, {strength_schedule: 0.7}, {cadence: 2, prompts: [{frame: 0, text: 'a'}, {frame: 21, text: 'b'}]});
    const b = state.prompts[1];
    Core.setPromptDip(state, b.id, 0.4, 4);
    let values = Core.laneValues(state, 'strength_schedule');
    near(values[20], 0.7, 1e-12);
    near(values[21], 0.4, 1e-12);
    near(values[22], 0.4, 1e-12, 'held for Cadence frames: frame 22 is generated');
    near(values[23], 0.4 + 0.3 * (1 / 4), 1e-12);
    near(values[25], 0.4 + 0.3 * (3 / 4), 1e-12);
    near(values[26], 0.7, 1e-12, 'back over 4 frames');
    assert.equal(state.lanes.strength_schedule.write, true, 'a dip writes the Strength schedule');
    assert.ok(Core.movePrompt(state, b.id, 40));
    values = Core.laneValues(state, 'strength_schedule');
    near(values[21], 0.7, 1e-12);
    near(values[40], 0.4, 1e-12);
    near(values[41], 0.4, 1e-12);
});

test('prompts: no two on a frame, a moved formula key becomes a frame number, deleting works', () => {
    const state = makeState(100, {}, {prompts: [{frame: 0, text: 'start'}, {frame: 99, text: 'end', key: 'max_f', pinned: true}]});
    const [start, end] = state.prompts;
    assert.equal(Core.addPrompt(state, 0, 'again'), null);
    const middle = Core.addPrompt(state, 50, 'middle');
    assert.deepEqual(state.prompts.map((p) => p.frame), [0, 50, 99]);
    assert.equal(Core.movePrompt(state, middle.id, 99), false);
    assert.equal(middle.frame, 50);
    assert.equal(Core.payload(state).prompts[2].key, 'max_f', 'a formula key is kept while it stays');
    assert.ok(Core.movePrompt(state, end.id, 90));
    assert.equal(end.pinned, false);
    assert.equal(Core.payload(state).prompts[2].key, null);
    assert.ok(Core.deletePrompt(state, start.id));
    assert.deepEqual(state.prompts.map((p) => p.frame), [50, 90]);
    assert.equal(Core.activePrompt(state, 10).text, 'middle', 'before the first key Deforum uses the first prompt');
    assert.equal(Core.snapFrame(Object.assign({}, state, {song: song(100, () => 0.5)}), 33, 'beats', 4), 30);
    assert.equal(Core.snapFrame(Object.assign({}, state, {song: song(100, () => 0.5)}), 36, 'beats', 4), 36);
});

test('undo and redo bring lanes, prompts and the path back', () => {
    const state = makeState(30, {translation_x: 1}, {prompts: [{frame: 0, text: 'a'}]});
    const token = Core.beginEdit(state, ['translation_x']);
    Core.paintLane(state, 'translation_x', 0, 3, 29, 3);
    Core.endEdit(state, token, 'draw');
    Core.edit(state, ['prompts'], 'add', () => Core.addPrompt(state, 10, 'b'));
    Core.edit(state, ['path'], 'path', () => {
        state.path.points = [{x: 0, z: 0}, {x: 0, z: 1}];
        state.path.enabled = true;
    });
    assert.equal(state.prompts.length, 2);
    Core.undo(state);
    assert.equal(state.path.points.length, 0);
    Core.undo(state);
    assert.equal(state.prompts.length, 1);
    Core.undo(state);
    assert.equal(Core.laneValues(state, 'translation_x')[5], 1);
    assert.equal(state.lanes.translation_x.write, false);
    Core.redo(state);
    assert.equal(Core.laneValues(state, 'translation_x')[5], 3);
    Core.redo(state);
    Core.redo(state);
    assert.equal(state.prompts.length, 2);
    assert.equal(state.path.points.length, 2);
    assert.equal(Core.redo(state), null);
});

function circle(count, radius) {
    return Array.from({length: count}, (_, i) => {
        const a = (2 * Math.PI * i) / count;
        return {x: radius * Math.sin(a), z: radius * Math.cos(a)}; // clockwise from above: the camera turns right
    });
}

function motion(pathSpec, speed, frames, mode) {
    return Core.pathToMotion(Object.assign({enabled: true, closed: false, laps: 1, look: 'along', lookAhead: 0,
                                            heights: new Float64Array(Core.HEIGHT_SAMPLES), heightMax: 0.5}, pathSpec),
                             new Float64Array(frames).fill(speed), frames, mode || '3D');
}

test('path: a straight line forward is Translation Z only, at the drawn speed', () => {
    const result = motion({points: [{x: 0, z: -0.8}, {x: 0, z: 0.8}]}, 2, 100);
    for (let f = 1; f < 100; f++) {
        near(result.values.translation_z[f], 2, 1e-9, `z ${f}`);
        near(result.values.translation_x[f], 0, 1e-9, `x ${f}`);
        near(result.values.translation_y[f], 0, 1e-9, `y ${f}`);
        near(result.values.rotation_3d_y[f], 0, 1e-9, `pan ${f}`);
    }
    const back = Core.pathToMotion({enabled: true, points: [{x: 0, z: -0.8}, {x: 0, z: 0.8}], closed: false, laps: 1,
                                    look: 'along', lookAhead: 0, heights: new Float64Array(Core.HEIGHT_SAMPLES)},
                                   Float64Array.from({length: 100}, (_, f) => (f < 50 ? 2 : -2)), 100, '3D');
    near(back.values.translation_z[30], 2, 1e-9);
    near(back.values.translation_z[70], -2, 1e-9, 'a minus speed drives back along the path');
});

test('path: a circle looking along it pans right at a steady 360° per round, with no sideways drift', () => {
    const frames = 361;
    const result = motion({points: circle(24, 0.7), closed: true}, 1.5, frames);
    let pan = 0;
    for (let f = 1; f < frames; f++) {
        pan += result.values.rotation_3d_y[f];
        near(result.values.rotation_3d_y[f], 1, 0.08, `pan at ${f}`);
        near(result.values.translation_z[f], 1.5, 0.01, `forward at ${f}`);
        near(result.values.translation_x[f], 0, 0.01, `sideways at ${f}`);
    }
    near(pan, 360, 0.5, 'one round');
    const twice = motion({points: circle(24, 0.7), closed: true, laps: 2}, 1.5, frames);
    near(twice.values.rotation_3d_y[100], 2, 0.16, 'two laps turn twice as fast');
});

test('path: a circle looking forward slides sideways instead of turning', () => {
    const frames = 361;
    const result = motion({points: circle(24, 0.7), closed: true, look: 'forward'}, 1.5, frames);
    assert.deepEqual(result.keys, ['translation_x', 'translation_y', 'translation_z']);
    near(result.values.translation_z[1], 1.5, 0.02, 'starts going forward');
    near(result.values.translation_x[90], 1.5, 0.05, 'a quarter round later it moves right');
    near(result.values.translation_z[180], -1.5, 0.05, 'half a round later it comes back');
    near(result.values.translation_x[270], -1.5, 0.05);
});

test('path: climbing moves the camera up, or tilts it up when it looks along the path', () => {
    const heights = Float64Array.from({length: Core.HEIGHT_SAMPLES}, (_, i) => {
        const u = i / (Core.HEIGHT_SAMPLES - 1);
        return u < 0.3 ? 0 : u > 0.7 ? 0.4 : ((u - 0.3) / 0.4) * 0.4;
    });
    const points = [{x: 0, z: -0.9}, {x: 0, z: 0.9}];
    const level = motion({points, heights, look: 'forward'}, 1, 200);
    assert.ok(level.values.translation_y[100] > 0.1, `up while climbing: ${level.values.translation_y[100]}`);
    near(level.values.translation_y[20], 0, 1e-9, 'flat before the climb');
    const along = motion({points, heights, look: 'along'}, 1, 200);
    const tilt = Array.from(along.values.rotation_3d_x);
    const up = tilt.reduce((best, v, f) => (v > tilt[best] ? f : best), 0);
    const down = tilt.reduce((best, v, f) => (v < tilt[best] ? f : best), 0);
    assert.ok(tilt[up] > 1 && up < 100, `tilts up where the climb starts (${tilt[up]} at ${up})`);
    assert.ok(tilt[down] < -1 && down > 100, `and back down where it ends (${tilt[down]} at ${down})`);
    near(along.values.translation_y[100], 0, 0.02, 'looking along the climb the camera just goes forward');
});

test('path in 2D: forward is Zoom above 1, right is Translation X minus, nothing turns', () => {
    const forward = motion({points: [{x: 0, z: -0.5}, {x: 0, z: 0.5}]}, 2, 50, '2D');
    assert.deepEqual(forward.keys, ['zoom', 'translation_x', 'translation_y']);
    near(forward.values.zoom[10], 1.02, 1e-9);
    const right = motion({points: [{x: -0.5, z: 0}, {x: 0.5, z: 0}], look: 'along'}, 2, 50, '2D');
    near(right.values.translation_x[10], 0, 1e-9, 'the camera faces the way the path starts');
    near(right.values.zoom[10], 1.02, 1e-9);
    const sideways = motion({points: [{x: 0, z: 0}, {x: 0, z: 0.4}, {x: 0.6, z: 0.4}]}, 2, 80, '2D');
    assert.ok(sideways.values.translation_x[70] < -1, `moving right slides the 2D picture left: ${sideways.values.translation_x[70]}`);
});

test('the path drives its lanes live, is always written, and bake hands it to the lanes', () => {
    const frames = 90;
    const state = makeState(frames, {translation_z: 1.2, translation_x: 0.5, rotation_3d_z: 0.3});
    Core.edit(state, ['path'], 'path', () => {
        state.path.points = [{x: 0, z: -0.8}, {x: 0, z: 0}, {x: 0.8, z: 0}];
        state.path.enabled = true;
    });
    assert.deepEqual(Core.laneOrder(state).slice(0, 3), ['strength_schedule', 'path_speed', 'translation_z']);
    near(state.lanes.path_speed.mag[5] * state.lanes.path_speed.sign[5], 1.2, 1e-12, 'the speed starts as Translation Z');
    assert.ok(Core.isDriven(state, 'translation_x'));
    assert.equal(Core.isDriven(state, 'rotation_3d_z'), false, 'roll stays free');
    const payload = Core.payload(state);
    assert.deepEqual(Object.keys(payload.lanes).sort(),
                     ['rotation_3d_x', 'rotation_3d_y', 'translation_x', 'translation_y', 'translation_z']);
    near(payload.lanes.rotation_3d_y.reduce((a, b) => a + b, 0), 90, 1, 'the right-hand bend pans 90° right');
    const pan = Core.laneValues(state, 'rotation_3d_y').reduce((a, b) => a + b, 0);
    assert.ok(Core.bake(state));
    assert.equal(Core.pathActive(state), false);
    near(Core.laneValues(state, 'rotation_3d_y').reduce((a, b) => a + b, 0), pan, 1e-6, 'baked lanes keep the motion');
    Core.undo(state);
    assert.ok(Core.pathActive(state), 'undo brings the path back');
});

test('the lanes taken back into a trail retrace the path that made them', () => {
    const frames = 240;
    const climb = Float64Array.from({length: Core.HEIGHT_SAMPLES}, (_, i) => 0.3 * Math.sin((Math.PI * i) / (Core.HEIGHT_SAMPLES - 1)));
    const bends = [{x: 0, z: -0.8}, {x: 0.5, z: -0.2}, {x: -0.3, z: 0.4}, {x: 0.2, z: 0.9}];
    // climbing inside the bends needs the roll: without it the trail is 23 % of a map unit off, with it under 1 %
    for (const [spec, tolerance] of [[{points: bends, lookAhead: 0}, 0.005], // bends
                                     [{points: [{x: 0.3, z: -0.8}, {x: -0.2, z: 0.9}], heights: climb, lookAhead: 0}, 0.005], // a hill
                                     [{points: bends, heights: climb, lookAhead: 0}, 0.012]]) { // climbing in the bends
        const made = motion(spec, 1.5, frames);
        const lanes = {};
        for (const key of made.keys) lanes[key] = Array.from(made.values[key], (v, f) => [f, v]);
        lanes.rotation_3d_z = Array.from(made.roll, (v, f) => [f, v]);
        const back = Core.integrateMotion(makeState(frames, lanes));
        // the trail starts at the origin, level and looking along +z, like Deforum's camera; the path starts at its
        // first point looking along it (turned and tilted), so the trail is the path seen from that first camera
        for (const f of [60, 120, 180, 239]) {
            const seen = Core.toCamera((made.x[f] - made.x[0]) * made.scale, (made.y[f] - made.y[0]) * made.scale,
                                       (made.z[f] - made.z[0]) * made.scale, made.yaw[0], made.pitch[0]);
            near(back.x[f], seen.x, tolerance * made.scale, `x at ${f}`);
            near(back.y[f], seen.y, tolerance * made.scale, `y at ${f}`);
            near(back.z[f], seen.z, tolerance * made.scale, `z at ${f}`);
        }
    }
    // climbing while turning right: the pan shrinks by cos(tilt) and the horizon is held with a roll (Rotation 3D Z -)
    const climbing = motion({points: circle(24, 0.7), closed: true, heights: Float64Array.from({length: Core.HEIGHT_SAMPLES},
        (_, i) => (0.5 * i) / (Core.HEIGHT_SAMPLES - 1))}, 1.5, 361);
    const f = 180;
    const tiltNow = (climbing.pitch[f] + climbing.pitch[f - 1]) / 2;
    assert.ok(tiltNow > 0.05, `the loop climbs (tilt ${tiltNow})`);
    near(climbing.values.rotation_3d_y[f], ((climbing.yaw[f] - climbing.yaw[f - 1]) * 180 / Math.PI) * Math.cos(tiltNow), 1e-9);
    assert.ok(climbing.roll[f] < 0, `turning right while looking up rolls with Rotation 3D Z - (${climbing.roll[f]})`);
    const climbState = makeState(361, {translation_z: 1.5, rotation_3d_z: 0.2});
    climbState.path = Object.assign(climbState.path, {enabled: true, points: circle(24, 0.7), closed: true, lookAhead: 0,
                                                      heights: Float64Array.from({length: Core.HEIGHT_SAMPLES}, (_, i) => (0.5 * i) / (Core.HEIGHT_SAMPLES - 1))});
    Core.touch(climbState);
    assert.equal(Core.isDriven(climbState, 'rotation_3d_z'), false, 'your roll lane stays yours');
    const rolled = Core.laneValues(climbState, 'rotation_3d_z');
    near(rolled[f], 0.2 + Core.pathResult(climbState).roll[f], 1e-9, 'the path adds its roll to yours');
    assert.ok(Object.keys(Core.payload(climbState).lanes).includes('rotation_3d_z'), 'and Apply writes it');
    const circleState = makeState(361, {translation_z: 1, rotation_3d_y: 1});
    const round = Core.integrateMotion(circleState);
    near(round.x[360], 0, 0.05, 'a full turn at a steady speed comes back to the start');
    near(round.z[360], 0, 0.05);
    assert.ok(Math.max(...round.x) > 100, 'turning right goes round to the right');
});

test('the level-keeping roll matches Deforum: 1/tan(FOV/2) times the geometric roll, frame by frame', () => {
    const frames = 120;
    const climb = Float64Array.from({length: Core.HEIGHT_SAMPLES}, (_, i) => (0.4 * i) / (Core.HEIGHT_SAMPLES - 1));
    const make = (warp) => {
        const state = makeState(frames, {translation_z: 1.5}, warp ? {warp} : {});
        Object.assign(state.path, {enabled: true, points: [{x: 0, z: -0.8}, {x: 0.5, z: 0}, {x: 0.2, z: 0.8}], heights: climb});
        Core.touch(state);
        return state;
    };
    const fov70 = make({fov: [[0, 70], [119, 70]]});
    const wide = make({fov: [[0, 70], [59, 70], [60, 90], [119, 90]]});
    const unknown = make(null);
    const s = 1 / Math.tan((35 * Math.PI) / 180);
    near(Core.rollScales(fov70)[10], s, 1e-12);
    near(Core.rollScales(unknown)[10], s, 1e-12, 'without Deforum settings its default FOV of 70 is assumed');
    const geometric = Core.pathToMotion(fov70.path, Core.laneValues(fov70, 'path_speed'), frames, '3D');
    const [fl, wi] = [fov70, wide].map((state) => Core.pathResult(state));
    let checked = 0;
    for (let f = 1; f < frames; f++) {
        if (Math.abs(geometric.roll[f]) < 1e-6) continue;
        near(fl.roll[f], geometric.roll[f] * s, 1e-9, `roll at ${f}`);
        near(wi.roll[f], geometric.roll[f] * (f < 60 ? s : 1), 1e-9, `FOV 90 from frame 60 at ${f}`);
        near(fl.values.rotation_3d_y[f], geometric.values.rotation_3d_y[f], 1e-12, 'the pan does not change');
        checked++;
    }
    assert.ok(checked > 20, `the path rolls on ${checked} frames`);
});

test('points: nearest, insert into the nearest segment, and a height bump', () => {
    const points = [{x: 0, z: 0}, {x: 1, z: 0}, {x: 1, z: 1}];
    assert.equal(Core.nearestPoint(points, {x: 0.95, z: 0.05}, 0.1), 1);
    assert.equal(Core.nearestPoint(points, {x: 0.5, z: 0.5}, 0.1), -1);
    assert.equal(Core.insertPoint(points, false, {x: 0.5, z: 0.02}), 1);
    assert.deepEqual(points.map((p) => p.x), [0, 0.5, 1, 1]);
    const spec = {heights: new Float64Array(Core.HEIGHT_SAMPLES), heightMax: 0.5};
    Core.raiseHeight(spec, 0.5, 0.3, 0.1);
    near(spec.heights[100], 0.3, 1e-9);
    assert.ok(spec.heights[0] < 0.001);
    const simple = Core.simplify(Array.from({length: 50}, (_, i) => ({x: i / 49, z: i < 25 ? 0 : (i - 24) / 25})), 0.01);
    assert.ok(simple.length >= 3 && simple.length <= 4, `a bent stroke keeps its corner: ${simple.length} points`);
});

test('a saved project restores lanes, signs, prompts, dips and the path', () => {
    const frames = 80;
    const state = makeState(frames, {translation_z: 1, strength_schedule: 0.6}, {prompts: [{frame: 0, text: 'a'}, {frame: 40, text: 'b'}]});
    Core.rangeOp(state, 'translation_z', 10, 20, 'minus');
    Core.paintLane(state, 'translation_x', 30, 2, 60, 1);
    Core.setPromptDip(state, state.prompts[1].id, 0.35, 3);
    state.path.points = [{x: 0.1, z: 0.2}, {x: 0.3, z: 0.4}];
    Core.raiseHeight(state.path, 0.5, 0.2, 0.1);
    state.lanes.translation_x.music = {amount: 40, band: 'Bass', curve: 'even'};
    const saved = JSON.parse(JSON.stringify(Core.serialize(state)));
    const copy = makeState(frames, {translation_z: 5}, {project: saved});
    for (const key of ['translation_z', 'translation_x', 'strength_schedule']) {
        const a = Core.laneValues(state, key);
        const b = Core.laneValues(copy, key);
        a.forEach((v, f) => near(b[f], v, 1e-4, `${key} at ${f}`));
    }
    assert.deepEqual(copy.lanes.translation_x.music, {amount: 40, band: 'Bass', curve: 'even'});
    assert.equal(copy.prompts[1].dip.strength, 0.35);
    assert.equal(copy.path.points.length, 2);
    near(copy.path.heights[100], state.path.heights[100], 1e-4);
    const shorter = makeState(30, {}, {project: saved});
    assert.deepEqual(shorter.prompts.map((p) => p.frame), [0]);
    assert.equal(shorter.unplaced.length, 1, 'a prompt past the new end is kept aside, not lost');
});


test('the plan: steps are read, moved, removed and come back as music', () => {
    const state = Core.createState({
        frames: 1000, fps: 15,
        song: {name: 'song', fps: 15, frames: 1000, duration: 66, step: 1, bands: {Full: [0, 0, 0]}, beats: [10, 61, 120], parts: [0, 450]},
        plan: [{at: 450, mode: '', video: 1, frames: 10, at_text: 'part 2'},
               {at: 61, mode: '3D', video: 0, frames: 20, at_text: 'beat 2'}],
    });
    assert.deepEqual(state.plan.map((s) => s.at), [61, 450], 'they come in sorted by frame');
    assert.equal(state.plan[0].mode, '3D');
    assert.equal(state.plan[0].text, 'beat 2', 'what was typed is kept');

    // dragged somewhere of its own
    const id = state.plan[0].id;
    assert.equal(Core.movePlanStep(state, id, 300), true);
    assert.equal(Core.planStepById(state, id).at, 300);
    assert.equal(Core.planStepById(state, id).text, '', 'a step moved by hand is no longer that beat');
    let payload = Core.planPayload(state);
    assert.deepEqual(payload.map((s) => [s.at, s.at_text]), [[300, ''], [450, 'part 2']]);

    // dropped exactly on a beat, so it says so again
    Core.movePlanStep(state, id, 120);
    payload = Core.planPayload(state);
    assert.deepEqual(payload.map((s) => [s.at, s.at_text]), [[120, 'beat 3'], [450, 'part 2']]);

    // and undo puts a removed step back
    const token = Core.beginEdit(state, ['plan']);
    Core.deletePlanStep(state, id);
    Core.endEdit(state, token, 'delete plan step');
    assert.equal(state.plan.length, 1);
    Core.undo(state);
    assert.equal(state.plan.length, 2);
    assert.equal(Core.planStepById(state, id).at, 120);
});

// node --test tests/core.test.js
const test = require('node:test');
const assert = require('node:assert');
const C = require('../javascript/strata-1-core.js');

const near = (a, b, eps, what) => assert.ok(Math.abs(a - b) <= eps, `${what || ''} ${a} is not near ${b}`);

test('a straight spine goes where its heading points, frames evenly spaced', () => {
    const path = C.spinePath({ shape: 'line', heading: 0, x: 1, z: 2 }, 10, 0.01);
    near(path.length, 10, 0.02, 'length');
    const end = C.pointAt(path, 10);
    near(end.p[0], 1, 1e-6); near(end.p[2], 2 - 10, 0.02);
    assert.deepStrictEqual(end.u.map(v => Math.round(v * 1e6) / 1e6), [0, 1, 0]);
    // right of a viewer looking along -z is +x
    near(end.r[0], 1, 1e-9, 'right');
    const turned = C.spinePath({ shape: 'line', heading: 90 }, 5, 0.01);
    near(C.pointAt(turned, 5).p[0], 5, 0.02, 'heading 90 goes to +x');
});

test('an arc turns by its bend, and its frame stays square', () => {
    const path = C.spinePath({ shape: 'arc', bend: 90, heading: 0 }, 20, 0.01);
    const end = C.pointAt(path, path.length);
    near(end.t[0], 1, 0.01, 'ends heading +x'); near(end.t[2], 0, 0.02);
    for (const s of [0, 3.3, 9.9, 19]) {
        const f = C.pointAt(path, s);
        near(C.dot(f.t, f.u), 0, 1e-6); near(C.dot(f.t, f.r), 0, 1e-6); near(C.len(f.u), 1, 1e-6);
        near(f.u[1], 1, 1e-6, 'a flat arc keeps up up');
    }
});

test('an S swings by its bend and comes back to its heading', () => {
    const path = C.spinePath({ shape: 's', bend: 40, heading: 0 }, 30, 0.01);
    const mid = C.pointAt(path, path.length / 2).t, end = C.pointAt(path, path.length - 0.05).t;
    near(Math.atan2(mid[0], -mid[2]) * 180 / Math.PI, 40, 2, 'swing');
    near(Math.atan2(end[0], -end[2]) * 180 / Math.PI, 0, 2, 'back');
});

test('a rising spine climbs, tilts its frames, and nothing flips', () => {
    const path = C.spinePath({ shape: 'arc', bend: 30, rise: 6, rise_from: 0.3, tilt: true }, 25, 0.01);
    near(C.pointAt(path, 0).p[1], 0, 1e-9);
    assert.ok(C.pointAt(path, path.length).p[1] > 5.9);
    let previous = C.pointAt(path, 0).u;
    for (let s = 0.2; s < path.length; s += 0.2) {
        const u = C.pointAt(path, s).u;
        assert.ok(C.dot(u, previous) > 0.99, 'the up turns smoothly');
        previous = u;
    }
    const upright = C.spinePath({ shape: 'arc', bend: 30, rise: 6, tilt: false }, 25, 0.01);
    near(C.pointAt(upright, 20).u[1], 1, 1e-9, 'upright cards stay vertical');
});

test('beyond its ends a spine goes straight on', () => {
    const path = C.spinePath({ shape: 'arc', bend: 90 }, 10, 0.01);
    const end = C.pointAt(path, path.length), past = C.pointAt(path, path.length + 2);
    near(C.len(C.sub(past.p, C.add(end.p, C.mul(end.t, 2)))), 0, 1e-9);
    const before = C.pointAt(path, -3);
    near(before.p[2], 3, 1e-4, 'before the start: behind it');
});

test('cards: one per frame at k * spacing, feet on the spine, facing along it', () => {
    const chain = { size: 1.7, figure: 0.85, baseline: 0.95, aspect: 0.5, spine: { shape: 'line', spacing: 0.05 } };
    const path = C.spinePath(chain.spine, C.chainLength(chain, 100) + 1, 0.01);
    const cards = C.chainCards(chain, path, 100);
    assert.strictEqual(cards.length, 100);
    near(cards[10].s, 0.5, 1e-9);
    near(C.len(cards[10].up), 2.0, 1e-9, 'card height = size / figure');
    near(C.len(cards[10].right) * 2, 1.0, 1e-9, 'width = height * aspect');
    // the feet (95% down the card) are on the spine
    const feet = C.add(cards[10].bottom, C.mul(cards[10].up, 0.05));
    near(feet[1], 0, 1e-9); near(feet[2], -0.5, 0.011);
    const shown = C.chainCards(Object.assign({}, chain, { from: 20, to: 29 }), path, 100);
    assert.strictEqual(shown.length, 10); assert.strictEqual(shown[0].k, 20); near(shown[0].s, 0, 1e-9);
});

test('keys: monotone, held, and the camera between them never backs up', () => {
    const keys = [{ t: 0, s: 0 }, { t: 1, s: 1 }, { t: 2, s: 1.05 }, { t: 3, s: 5 }];
    let last = -1;
    for (let t = 0; t <= 3; t += 0.01) {
        const s = C.track(keys, 's', t, 0);
        assert.ok(s >= last - 1e-9, `backed up at ${t}`);
        last = s;
    }
    near(C.track(keys, 's', 2, 0), 1.05, 1e-9);
    near(C.track([{ t: 0, s: 0, hold: true }, { t: 2, s: 4 }], 's', 1.5, 0), 0, 1e-9, 'hold');
    near(C.track([], 's', 1, 7), 7, 0, 'fallback');
});

test('the camera follows its keys along the spine and looks where it is told', () => {
    const path = C.spinePath({ shape: 'line' }, 10, 0.01);
    const cam = C.cameraAt([{ t: 0, s: 2, side: 1, up: 1.5, look: 3, look_up: 1, look_side: 0, fov: 40 }], path, 0);
    near(cam.eye[0], 1, 1e-6); near(cam.eye[1], 1.5, 1e-6); near(cam.eye[2], -2, 0.011);
    near(cam.target[2], -5, 0.011);
    near(C.dot(cam.up, cam.forward), 0, 1e-9);
    assert.strictEqual(cam.fov, 40);
});

test('the presets move forward and fit the chain', () => {
    for (const name of ['opening', 'through', 'side', 'front', 'orbit']) {
        const keys = C.preset(name, 12, 10, 1.7);
        assert.ok(keys.length >= 2, name);
        assert.strictEqual(keys[0].t, 0); near(keys[keys.length - 1].t, 10, 1e-9);
    }
    const through = C.preset('through', 12, 10, 1.7);
    assert.ok(through[through.length - 1].s > 12, 'through goes past the last frame');
});

test('the knife: a plane on the spine, turned by yaw and pitch', () => {
    const path = C.spinePath({ shape: 'line' }, 10, 0.01);
    const k = C.knifeAt({ on: true, keys: [{ t: 0, s: 4, yaw: 0, pitch: 0 }] }, path, 0);
    near(k.point[2], -4, 0.011); near(k.normal[2], -1, 1e-9);
    const slanted = C.knifeAt({ on: true, keys: [{ t: 0, s: 4, yaw: 45, pitch: 0 }] }, path, 0);
    near(Math.abs(slanted.normal[0]), Math.SQRT1_2, 1e-6);
    assert.strictEqual(C.knifeAt({ on: false, keys: [{ t: 0, s: 1 }] }, path, 0), null);
});

test('the planner: frames, spacing and sharpness for a fly-through', () => {
    const p = C.plan({ seconds: 6, speed: 1.2, rate: 24, ahead: 6, size: 1.7, figure: 0.85, fov: 50, height: 1920, near: 0.35 });
    near(p.spacing, 0.05, 1e-9);
    assert.strictEqual(p.passed, 144);
    assert.strictEqual(p.frames, 144 + 120);
    // a 2 m card at 0.35 m, fov 50, 1920 high: 2 / (2 * 0.35 * tan 25) * 1920
    near(p.texture, 2 / (2 * 0.35 * Math.tan(25 * Math.PI / 180)) * 1920, 1);
    const rates = C.passRates([{ t: 0, s: 0 }, { t: 10, s: 5 }], 0.05, 10, 10);
    for (const r of rates.slice(2, 8)) assert.ok(r > 5 && r < 20);
});

test('matrices: a point in front of the camera lands in the middle of the picture', () => {
    const view = C.lookAt([0, 1, 5], [0, 1, 0], [0, 1, 0]);
    const proj = C.perspective(50, 1, 0.05, 100);
    const m = C.multiply(proj, view);
    const p = [0, 1, 0, 1];
    const clip = [0, 1, 2, 3].map(r => m[r] * p[0] + m[4 + r] * p[1] + m[8 + r] * p[2] + m[12 + r] * p[3]);
    near(clip[0] / clip[3], 0, 1e-9); near(clip[1] / clip[3], 0, 1e-9);
    assert.ok(clip[2] / clip[3] > -1 && clip[2] / clip[3] < 1);
});

test('a view chosen freely becomes a key that gives the same view back', () => {
    const path = C.spinePath({ shape: 'arc', bend: 60, rise: 2 }, 15, 0.01);
    for (const [eye, target] of [[[1.5, 1.2, -3], [0, 1, -8]], [[-2, 2.5, -9], [1, 0.5, -12]], [[0.2, 1.0, 1.5], [0, 1, -3]]]) {
        const key = C.keyFromView(path, eye, target, 45);
        const cam = C.cameraAt([Object.assign({ t: 0 }, key)], path, 0);
        near(C.len(C.sub(cam.eye, eye)), 0, 0.02, 'eye');
        const want = C.norm(C.sub(target, eye));
        assert.ok(C.dot(cam.forward, want) > 0.995, `looks the same way (${C.dot(cam.forward, want)})`);
    }
});

test('time along a chain: the present eats it, or it grows behind the present', () => {
    assert.strictEqual(C.presentAt({ mode: 'all' }, 3, 24), null);
    near(C.presentAt({ mode: 'future', start: 10 }, 2, 24), 58, 1e-9, 'own fps');
    near(C.presentAt({ mode: 'future', start: 0, rate: 12 }, 2, 24), 24, 1e-9, 'given rate');
    const future = { mode: 'future', fade: 1 };
    assert.strictEqual(C.presence(future, 5, 3), 1, 'still to come');
    assert.strictEqual(C.presence(future, 5, 7), 0, 'gone');
    near(C.presence(future, 5, 5.25), 0.75, 1e-9, 'fading as the present passes');
    const past = { mode: 'past', fade: 1 };
    assert.strictEqual(C.presence(past, 5, 3), 0, 'not yet');
    assert.strictEqual(C.presence(past, 5, 9), 1, 'has been');
    near(C.presence(past, 5, 4.5), 0.5, 1e-9, 'appearing');
    near(C.presentS({ offset: 1, from: 10, spine: { spacing: 0.05 } }, 30), 2, 1e-9);
});

test('a growing chain faces its end, so it is seen the right way round from in front of the present', () => {
    const chain = { aspect: 0.5, spine: { shape: 'line', spacing: 0.05 } };
    const path = C.spinePath(chain.spine, 10, 0.01);
    const start = C.chainCards(chain, path, 10)[3], end = C.chainCards(Object.assign({ faces: 'end' }, chain), path, 10)[3];
    near(C.dot(start.right, end.right), -C.dot(start.right, start.right), 1e-9);
});

test('a riding camera is carried along by the shift', () => {
    const path = C.spinePath({ shape: 'line' }, 20, 0.01);
    const keys = [{ t: 0, s: -2, side: 0, up: 1, look: 3, look_up: 1 }];
    const still = C.cameraAt(keys, path, 0, 0), carried = C.cameraAt(keys, path, 0, 5);
    near(carried.eye[2] - still.eye[2], -5, 0.011);
    const key = C.keyFromView(path, carried.eye, carried.target, 50, 5);
    near(key.s, -2, 0.02, 'a key taken while riding is measured from the present');
    const opening = C.preset('opening', 10, 10, 1.7, 1), back = C.preset('opening', 10, 10, 1.7, -1);
    near(opening[2].s, -back[2].s, 1e-9, 'a growing chain gets the opening mirrored');
});

test('the present holds on the last frame, which then stays', () => {
    const time = { mode: 'future', start: 0, rate: 10, fade: 1 };
    near(C.presentAt(time, 100, 24, 49), 49, 1e-9, 'held');
    near(C.presentAt(Object.assign({}, time, { hold: false }), 100, 24, 49), 1000, 1e-9, 'not held');
    assert.strictEqual(C.presence(time, 49, C.presentAt(time, 100, 24, 49)), 1, 'the last frame is still there');
    assert.strictEqual(C.presence(time, 48, C.presentAt(time, 100, 24, 49)), 0);
});

test('a steered spine turns and climbs as it was steered, and can be softened afterwards', () => {
    const straight = C.spinePath({ shape: 'steered', spacing: 0.05, steer: [] }, 5, 0.01);
    near(C.pointAt(straight, 5).p[2], -5, 0.02, 'no steering: straight on');
    // 1 degree a frame to the right for 90 frames: a quarter turn
    const right = C.spinePath({ shape: 'steered', spacing: 0.05, steer: [[0, 1, 0], [89, 1, 0], [90, 0, 0]] }, 10, 0.01);
    const end = C.pointAt(right, right.length - 0.02).t;
    near(Math.atan2(end[0], -end[2]) * 180 / Math.PI, 90, 1.5, 'turned right a quarter');
    const up = C.spinePath({ shape: 'steered', spacing: 0.05, steer: [[0, 0, 30]] }, 4, 0.01);
    near(C.pointAt(up, up.length).p[1], up.length * 0.5, 0.05, 'a 30 degree climb rises half the length');
    const half = C.spinePath({ shape: 'steered', spacing: 0.05, steer: [[0, 1, 0], [89, 1, 0], [90, 0, 0]], steer_scale: 0.5 }, 10, 0.01);
    const halfEnd = C.pointAt(half, half.length - 0.02).t;
    near(Math.atan2(halfEnd[0], -halfEnd[2]) * 180 / Math.PI, 45, 1.5, 'steering strength scales it');
    // a sudden kink smoothed: the largest turn between two frames gets smaller
    const kink = [[0, 0, 0], [40, 0, 0], [41, 20, 0], [42, 0, 0]];
    const s = C.steering({ steer: kink, steer_smooth: 0 }), soft = C.steering({ steer: kink, steer_smooth: 4 });
    assert.ok(Math.max(...[39, 40, 41, 42, 43].map(k => soft(k)[0])) < Math.max(...[39, 40, 41, 42, 43].map(k => s(k)[0])) / 3);
});

test('the follow camera frames the figure at the asked size, from the asked side', () => {
    const path = C.spinePath({ shape: 'line' }, 20, 0.01);
    for (const fov of [35, 50, 70]) {
        const cam = C.followAt([{ t: 0, yaw: 0, elevation: 0, fill: 0.5, aim: 0.5, lead: 0, fov }], path, 0, { s: 8, size: 1.7, faces: 'start' });
        const figureOnScreen = 1.7 / (cam.distance * 2 * Math.tan(fov * Math.PI / 360));
        near(figureOnScreen, 0.5, 1e-9, `fill at fov ${fov}`);
    }
    // the figure faces the spine's start (-z here goes forward, so it faces +z): yaw 0 puts the camera at +z of it
    const front = C.followAt([{ t: 0, yaw: 0, elevation: 0, fill: 0.5, lead: 0 }], path, 0, { s: 8, size: 1.7, faces: 'start' });
    assert.ok(front.eye[2] > -8 + 1, 'in front of the face');
    near(front.eye[0], 0, 1e-6);
    const side = C.followAt([{ t: 0, yaw: 90, elevation: 0, fill: 0.5, lead: 0 }], path, 0, { s: 8, size: 1.7, faces: 'start' });
    near(Math.abs(side.eye[0]) > 1 ? 1 : 0, 1, 0, 'at its side');
    near(side.eye[2], -8, 0.05);
    const grown = C.followAt([{ t: 0, yaw: 0, elevation: 0, fill: 0.5, lead: 0 }], path, 0, { s: 8, size: 1.7, faces: 'end' });
    assert.ok(grown.eye[2] < -8 - 1, 'a growing chain faces its end: the camera goes there');
    const high = C.followAt([{ t: 0, yaw: 0, elevation: 30, fill: 0.5, lead: 0 }], path, 0, { s: 8, size: 1.7, faces: 'start' });
    assert.ok(high.eye[1] > front.eye[1] + 0.5, 'elevation raises it');
});

test('the follow camera swings smoothly through a sharp turn of the trail', () => {
    const path = C.spinePath({ shape: 'steered', spacing: 0.05, steer: [[0, 0, 0], [99, 0, 0], [100, 30, 0], [102, 30, 0], [103, 0, 0]] }, 15, 0.01);
    const keys = [{ t: 0, yaw: 0, elevation: 5, fill: 0.5, lead: 0.5, smooth: 2 }];
    let worst = 0, last = null;
    for (let s = 2; s < 9; s += 0.05) {
        const eye = C.followAt(keys, path, 0, { s, size: 1.7, faces: 'start' }).eye;
        if (last) worst = Math.max(worst, C.len(C.sub(eye, last)));
        last = eye;
    }
    assert.ok(worst < 0.25, `no jump of the camera (largest step ${worst.toFixed(3)} m for 5 cm of trail)`);
});

test('follow presets give keys for the whole shot', () => {
    for (const name of ['front', 'side', 'behind', 'close', 'orbit', 'reveal']) {
        const keys = C.followPreset(name, 12);
        assert.ok(keys.length >= 1 && keys.every(k => isFinite(k.yaw) && k.fill > 0), name);
    }
});

test('steering marks are thinned to where the steering changed, and the trail stays where it was', () => {
    // a mark a frame, as recorded while steering: straight, a steady right turn, then a climb
    const marks = [];
    for (let k = 0; k <= 300; k++) marks.push([k, k < 100 ? 0 : k < 200 ? 0.6 : 0, k < 200 ? 0 : 12]);
    const thin = C.compressSteer(marks);
    assert.ok(thin.length < 12, `few marks are left (${thin.length})`);
    assert.deepStrictEqual(thin[0], marks[0]);
    assert.deepStrictEqual(thin[thin.length - 1], marks[marks.length - 1]);
    const a = C.spinePath({ shape: 'steered', spacing: 0.03, steer: marks }, 9, 0.01);
    const b = C.spinePath({ shape: 'steered', spacing: 0.03, steer: thin }, 9, 0.01);
    for (const s of [1, 3, 4.5, 6, 8.9]) {
        const d = C.len(C.sub(C.pointAt(a, s).p, C.pointAt(b, s).p));
        assert.ok(d < 0.01, `the same trail at ${s} m (${d.toFixed(4)} m apart)`);
    }
});

test('a steered trail with a mark every frame is found quickly and exactly', () => {
    const marks = [];
    for (let k = 0; k <= 2000; k++) marks.push([k, Math.sin(k / 50) * 0.5, 0]);
    const steer = C.steering({ steer: marks });
    near(steer(1000)[0], Math.sin(20) * 0.5, 1e-9);
    near(steer(1000.5)[0], (Math.sin(20) + Math.sin(1001 / 50)) * 0.25, 1e-9);
    const started = Date.now();
    C.spinePath({ shape: 'steered', spacing: 0.03, steer: marks }, 60, 0.01);
    assert.ok(Date.now() - started < 400, 'a long steered trail is worked out in a moment');
});

test('looking ahead never takes the figure out of an upright picture', () => {
    const path = C.spinePath({ shape: 'line', spacing: 0.04 }, 20, 0.01);
    const aspect = 9 / 16, fov = 45;
    const half = Math.atan(Math.tan(fov / 2 * Math.PI / 180) * aspect);
    for (const yaw of [0, 30, 50, 80]) {
        const cam = C.followAt([{ t: 0, yaw, elevation: 10, fill: 0.5, aim: 0.55, lead: 6, smooth: 1, fov }], path, 0, { s: 8, size: 1.7, faces: 'end', aspect });
        const figure = C.add(C.pointAt(path, 8).p, [0, 1.7 * 0.5, 0]);
        const angle = Math.acos(C.dot(C.norm(C.sub(figure, cam.eye)), cam.forward));
        assert.ok(angle <= 0.41 * half + 1e-3, `yaw ${yaw}: the figure is ${(angle / half * 100).toFixed(0)}% of the half picture from its middle`);
    }
    const ahead = C.followAt([{ t: 0, yaw: 0, elevation: 10, fill: 0.5, aim: 0.55, lead: 1, smooth: 1, fov }], path, 0, { s: 8, size: 1.7, faces: 'end', aspect });
    assert.ok(C.len(C.sub(ahead.target, C.add(C.pointAt(path, 8).p, [0, 1.7 * 0.5, 0]))) > 0.9, 'seen from in front the look ahead is kept');
});

test('frame numbers are read as a person writes them', () => {
    assert.deepStrictEqual(C.parseFrames('18-21, 44 50', 100), [18, 19, 20, 21, 44, 50]);
    assert.deepStrictEqual(C.parseFrames('7, 3–5; 3', 100), [3, 4, 5, 7]);
    assert.deepStrictEqual(C.parseFrames('12-9', 100), [9, 10, 11, 12]);
    assert.deepStrictEqual(C.parseFrames('5, 300, x, 98-102', 100), [5, 98, 99]);
    assert.deepStrictEqual(C.parseFrames('', 10), []);
});

test('a world video plays over the shot, or at its own speed again and again', () => {
    // the leaders film: a 31 s world video over a 31 s shot turns colour when the leaders do
    assert.strictEqual(C.worldVideoTime({}, 7, 31, 31), 7);
    assert.strictEqual(C.worldVideoTime({ video_play: 'shot' }, 15.5, 31, 10), 5);
    assert.ok(C.worldVideoTime({}, 40, 31, 10) < 10, 'never past its last frame');
    assert.strictEqual(C.worldVideoTime({ video_play: 'own' }, 12, 31, 10), 2);
    assert.strictEqual(C.worldVideoTime({ video_play: 'own', video_rate: 0.5, video_start: 1 }, 6, 31, 10), 4);
    assert.strictEqual(C.worldVideoTime({ video_play: 'own' }, 3, 31, 0), 0);
});

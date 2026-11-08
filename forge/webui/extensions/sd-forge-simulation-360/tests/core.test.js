// node --test extensions/sd-forge-simulation-360/tests/core.test.js
const test = require('node:test');
const assert = require('node:assert');
const C = require('../javascript/sim360-1-core.js');

const near = (a, b, eps, what) => assert.ok(Math.abs(a - b) <= (eps || 1e-6), `${what || ''} ${a} is not ${b}`);

test('a pixel of the panorama and its direction agree both ways', () => {
    for (const [u, v] of [[0.5, 0.5], [0.75, 0.5], [0.1, 0.2], [0.93, 0.81], [0.25, 0.01]]) {
        const d = C.dirFromUv(u, v), back = C.uvFromDir(d[0], d[1], d[2]);
        near(back[0], u, 1e-9, 'u'); near(back[1], v, 1e-9, 'v');
    }
    assert.deepStrictEqual(C.dirFromUv(0.5, 0.5).map(x => +x.toFixed(9) + 0), [0, 0, -1]);       // the middle is ahead
    near(C.dirFromUv(0.75, 0.5)[0], 1, 1e-9, 'a quarter to the right is +x');
    near(C.dirFromUv(0.5, 0)[1], 1, 1e-9, 'the top is up');
});

test('the camera looks where its yaw and pitch say', () => {
    const b = C.basis(90, 0, 0);
    near(b.f[0], 1, 1e-9); near(b.r[2], 1, 1e-9);                  // turned right: ahead is +x, right is +z
    const up = C.basis(30, 40, 0);
    near(up.f[1], Math.sin(40 * C.RAD), 1e-9);
    // right, up, ahead are at right angles
    const dot = (a, c) => a[0] * c[0] + a[1] * c[1] + a[2] * c[2];
    near(dot(up.r, up.u), 0, 1e-9); near(dot(up.r, up.f), 0, 1e-9); near(dot(up.u, up.f), 0, 1e-9);
});

test('what is ahead lands in the middle of the picture, right is right, up is up', () => {
    const cam = { pos: [1, 2, 3], yaw: 37, pitch: -12, roll: 0, fov: 80 };
    const m = C.viewProjection(cam, 16 / 9), b = C.basis(cam.yaw, cam.pitch, 0);
    const at = k => [cam.pos[0] + k[0], cam.pos[1] + k[1], cam.pos[2] + k[2]];
    const mid = C.project(m, at(b.f.map(x => x * 5)));
    near(mid[0], 0.5, 1e-6); near(mid[1], 0.5, 1e-6);
    const right = C.project(m, at([0, 1, 2].map(i => b.f[i] * 5 + b.r[i])));
    assert.ok(right[0] > 0.5); near(right[1], 0.5, 1e-6);
    const above = C.project(m, at([0, 1, 2].map(i => b.f[i] * 5 + b.u[i])));
    assert.ok(above[1] < 0.5); near(above[0], 0.5, 1e-6);
    assert.strictEqual(C.project(m, at(b.f.map(x => -x))), null);          // behind the camera
    // the edge of the view: half the field of view up is the top edge
    const t = Math.tan(40 * C.RAD);
    const top = C.project(m, at([0, 1, 2].map(i => b.f[i] + b.u[i] * t)));
    near(top[1], 0, 1e-6);
});

test('a ray through a point of the picture comes back to that point', () => {
    const cam = { pos: [0, 0, 0], yaw: -50, pitch: 20, roll: 0, fov: 70 }, aspect = 1.5;
    const m = C.viewProjection(cam, aspect);
    for (const [x, y] of [[0.5, 0.5], [0.1, 0.9], [0.8, 0.3]]) {
        const d = C.rayThrough(cam, aspect, x, y), p = C.project(m, d.map(v => v * 7));
        near(p[0], x, 1e-6); near(p[1], y, 1e-6);
    }
});

test('a cropped view shows that part of the whole view', () => {
    const cam = { pos: [0.3, -0.2, 0.1], yaw: 15, pitch: 5, roll: 0, fov: 90 }, aspect = 16 / 9;
    const whole = C.viewProjection(cam, aspect), crop = { x0: 0.3, y0: 0.1, x1: 0.6, y1: 0.9 };
    const part = C.viewProjection(cam, aspect, crop);
    for (const p of [[0.4, 0.3, -4], [1.5, -0.5, -3], [2, 1, -5]]) {
        const a = C.project(whole, p), b = C.project(part, p);
        near(b[0], (a[0] - crop.x0) / (crop.x1 - crop.x0), 1e-6);
        near(b[1], (a[1] - crop.y0) / (crop.y1 - crop.y0), 1e-6);
    }
});

test('depth: nearness becomes metres, and the camera is held inside the world', () => {
    const w = 8, hgt = 4, n = new Float32Array(w * hgt).fill(0);
    n.fill(1, 3 * w, 4 * w);                                               // the ground is near
    const field = new C.DepthField(n, w, hgt).tune(2, 20, 1);
    near(field.metres(0), 20); near(field.metres(1), 2);
    near(field.distAt(0.5, 0.05), 20, 1e-3);
    near(field.distAt(0.3, 0.9), 2, 1e-3);
    const held = C.constrain([0, 0, -19], field, 0.7);
    assert.ok(held.stopped); near(Math.hypot(...held.pos), 14, 1e-6);
    assert.ok(!C.constrain([0, 0, -5], field, 0.7).stopped);
    assert.strictEqual(new C.DepthField(null, 0, 0).tune(1.6, 14, 1).distAt(0.2, 0.2), 14);  // no map: a plain sphere
    // kept inside softly: left alone near the centre, and from there nearer and nearer to the limit, never past it
    const ball = new C.DepthField(null, 0, 0).tune(1.6, 10, 1);
    assert.deepStrictEqual(C.eased([0, 0, -4], ball, 0.5, 0.8), [0, 0, -4]);
    const out = z => -C.eased([0, 0, -z], ball, 0.5, 0.8)[2];
    assert.ok(out(6) > 5 && out(6) < 6 && out(9) > out(6) && out(30) > out(9) && out(30) < 8 && out(300) <= 8);
    near(out(5.001) - out(5), 0.001, 1e-5, 'no jolt where the easing begins');
});

test('the professional camera adds nothing; the others stay small and repeat exactly', () => {
    assert.deepStrictEqual(C.rigOffset('pro', 3.3, 2, 1), { right: 0, up: 0, ahead: 0, yaw: 0, pitch: 0, roll: 0 });
    for (const kind of ['hover', 'walk', 'handheld']) {
        const a = C.rigOffset(kind, 12.34, 5.6, 1), b = C.rigOffset(kind, 12.34, 5.6, 1);
        assert.deepStrictEqual(a, b);
        assert.ok(Math.abs(a.up) < 0.12 && Math.abs(a.right) < 0.08 && Math.abs(a.yaw) < 2 && Math.abs(a.roll) < 2, kind);
        const zero = C.rigOffset(kind, 12.34, 5.6, 0);
        assert.deepStrictEqual(zero, { right: 0, up: 0, ahead: 0, yaw: 0, pitch: 0, roll: 0 });
    }
});

test('a route in one world: starts and ends on its keys, softly, the short way round', () => {
    const tour = { keys: [
        { world: 'a', pos: [0, 0, 0], yaw: 170, pitch: 0, fov: 90, dur: 0, hold: 0 },
        { world: 'a', pos: [2, 0, 0], yaw: -170, pitch: 10, fov: 70, dur: 4, hold: 0 },
        { world: 'a', pos: [2, 0, -3], yaw: -90, pitch: 0, fov: 70, dur: 2, hold: 1 },
    ] };
    const compiled = C.compileTour(tour, () => null);
    near(compiled.duration, 7);
    const first = C.tourAt(compiled, 0), last = C.tourAt(compiled, 7);
    assert.deepStrictEqual(first.pos, [0, 0, 0]); near(first.yaw, 170);
    near(last.pos[0], 2, 1e-9); near(last.pos[2], -3, 1e-9); near(C.wrapDeg(last.yaw), -90, 1e-9);
    // 170 to -170 goes through 180, not back through 0
    const mid = C.tourAt(compiled, 2);
    assert.ok(mid.yaw > 170 && mid.yaw < 190, 'yaw ' + mid.yaw);
    // a soft start: after a tenth of a second it has hardly moved
    assert.ok(C.tourAt(compiled, 0.1).pos[0] < 0.02);
    // it waits a second at the end
    near(C.tourAt(compiled, 6.2).pos[2], -3, 1e-9);
    // metres flown only grow
    let before = -1;
    for (let t = 0; t <= 7; t += 0.25) { const m = C.tourAt(compiled, t); assert.ok(m.travelled >= before - 1e-9); before = m.travelled; }
    near(before, C.tourAt(compiled, 5.9).travelled, 0.05);
});

test('a route through a portal: flies to the door, the next world takes over, then it unfolds', () => {
    const tour = { keys: [
        { world: 'a', pos: [0, 0, 0], yaw: 0, pitch: 0, fov: 90, dur: 0 },
        { world: 'b', pos: [1, 0, 1], yaw: 100, pitch: 0, fov: 90, dur: 6 },
    ] };
    const link = (from, to) => from === 'a' && to === 'b'
        ? { enter: { pos: [0, 0, -5], yaw: 0, pitch: 0 }, arrive: { pos: [0, 0, 0] }, turn: 40 } : null;
    const compiled = C.compileTour(tour, link);
    near(compiled.duration, 6);
    const door = 6 * 0.55;
    const before = C.tourAt(compiled, door - 0.01);
    assert.strictEqual(before.world, 'a'); near(before.pos[2], -5, 0.05);
    assert.ok(before.mix && before.mix.world === 'b' && before.mix.amount > 0.98 && before.mix.turn === 40);
    assert.strictEqual(C.tourAt(compiled, door - 1).mix, null);
    const after = C.tourAt(compiled, door + 0.01);
    assert.strictEqual(after.world, 'b'); assert.ok(after.relief < 0.01); near(after.yaw, 40, 0.5);
    near(C.tourAt(compiled, door + 2).relief, 1);
    const end = C.tourAt(compiled, 6);
    near(end.pos[0], 1, 1e-9); near(end.yaw, 100, 1e-9);
    // the camera does not stop at the door: it is still moving just before it
    const a = C.tourAt(compiled, door - 0.2).pos[2], b = C.tourAt(compiled, door - 0.1).pos[2];
    assert.ok(Math.abs(a - b) > 0.05);
});

test('at a portal the view narrows to the portal and widens again on the other side', () => {
    const tour = { keys: [
        { world: 'a', pos: [0, 0, 0], yaw: 0, pitch: 0, fov: 80, dur: 0 },
        { world: 'b', pos: [0, 0, -1], yaw: 0, pitch: 0, fov: 70, dur: 6 },
    ] };
    const narrow = C.compileTour(tour, () => ({ enter: { pos: [0, 0, -4], yaw: 0, pitch: 5, fov: 35 }, arrive: { pos: [0, 0, 0] }, turn: 0 }));
    const door = 6 * 0.55;
    near(C.tourAt(narrow, 0).fov, 80);
    near(C.tourAt(narrow, door - 0.001).fov, 35, 0.2);
    near(C.tourAt(narrow, door + 0.001).fov, 35, 0.2);                    // the same view on both sides of the door
    near(C.tourAt(narrow, 6).fov, 70);
    // a portal that asks for a wider view than the camera has does not widen it
    const wide = C.compileTour(tour, () => ({ enter: { pos: [0, 0, -4], yaw: 0, pitch: 5, fov: 120 }, arrive: { pos: [0, 0, 0] }, turn: 0 }));
    near(C.tourAt(wide, door - 0.001).fov, 80, 0.2);
});

test('a portal that shows the next world in its depth is only a point on the way', () => {
    // the door is 4 m ahead; behind it the next world is turned by 90 degrees and the camera goes on 4 m in it
    const tour = { keys: [
        { world: 'a', pos: [0, 0, 0], yaw: 10, pitch: -4, fov: 96, roll: 2, dur: 0 },
        { world: 'b', pos: [4, 0, 0], yaw: 80, pitch: 6, fov: 80, roll: -2, dur: 8, hold: 1 },
    ] };
    const link = () => ({ enter: { pos: [0, 0, -4], yaw: 0, pitch: 0, fov: 30 }, arrive: { pos: [0, 0, 0] }, turn: 90, deep: true, via: 'p1' });
    const compiled = C.compileTour(tour, link);
    const door = 4;                                                         // half the way is half the time
    const before = C.tourAt(compiled, door - 0.001), after = C.tourAt(compiled, door + 0.001);
    assert.strictEqual(before.world, 'a'); assert.strictEqual(after.world, 'b');
    // nothing unfolds, the view does not narrow, and the camera looks the same way on both sides (turned with the world)
    near(after.relief, 1); near(before.relief, 1);
    near(before.fov, 88, 0.05); near(after.fov, 88, 0.05);
    near(after.yaw - before.yaw, 90, 0.05); near(before.pitch, after.pitch, 0.02); near(before.roll, after.roll, 0.02);
    near(before.pos[2], -4, 0.01); near(after.pos[0], 0, 0.01);
    // the next world opens around the camera over the last moments before the door, and is everything on it
    assert.ok(before.mix && before.mix.deep && before.mix.via === 'p1' && before.mix.amount > 0.99);
    assert.strictEqual(C.tourAt(compiled, door - C.SPREAD_SECONDS - 0.05).mix, null);
    assert.strictEqual(after.mix, null);
    // the pace does not change at the door
    const speed = t => { const a = C.tourAt(compiled, t - 0.05), b = C.tourAt(compiled, t + 0.05); return (b.travelled - a.travelled) / 0.1; };
    near(speed(door - 0.3), speed(door + 0.3), 0.08, 'pace');
    near(C.tourAt(compiled, door - 0.3).speed, C.tourAt(compiled, door + 0.3).speed, 0.1, 'pace as the runner feels it');
    // a door that is nearer than what follows takes less of the time
    const nearDoor = C.compileTour(tour, () => ({ enter: { pos: [0, 0, -1] }, arrive: { pos: [1, 0, 0] }, turn: 90, deep: true }));
    assert.strictEqual(C.tourAt(nearDoor, 1.9).world, 'a'); assert.strictEqual(C.tourAt(nearDoor, 2.1).world, 'b');
});

test('the camera behind a portal: on the doorstep it is on the arrival point, and a step back is a step back', () => {
    const door = [0, 0.2, -5], arrive = [1, 0, 2];
    const on = C.beyond({ pos: door, yaw: 30, pitch: 5, roll: 1, fov: 70 }, door, arrive, 90);
    assert.deepStrictEqual(on.pos, arrive); near(on.yaw, 120); near(on.pitch, 5); near(on.fov, 70);
    // one metre before the door (towards +z here) is one metre before the arrival, in the turned world
    const back = C.beyond({ pos: [0, 0.2, -4], yaw: 0, pitch: 0, fov: 70 }, door, arrive, 90);
    const ahead = C.dirFromAngles(90, 0);                                   // "ahead" (yaw 0) behind the portal is yaw 90
    near(back.pos[0], arrive[0] - ahead[0], 1e-9); near(back.pos[2], arrive[2] - ahead[2], 1e-9); near(back.pos[1], 0, 1e-9);
    const d = C.swing(C.dirFromAngles(25, 10), 40), e = C.dirFromAngles(65, 10);
    near(d[0], e[0], 1e-9); near(d[1], e[1], 1e-9); near(d[2], e[2], 1e-9);
});

test('what stands on the ground is drawn on the world\'s surface, as small as the picture is there', () => {
    // a world shaped like a bowl: a flat floor 2 m below the eyes out to 6 m, and from there a slope up
    const w = 720, hgt = 360, n = new Float32Array(w * hgt);
    const field = new C.DepthField(n, w, hgt).tune(2, 40, 1);
    const floor = r => r < 6 ? -2 : -2 + (r - 6) * 0.5;                    // height of the ground r metres out
    for (let y = 0; y < hgt; y++) {
        const pitch = (0.5 - (y + 0.5) / hgt) * 180;
        let dist = 40;
        if (pitch < -0.5) { let lo = 0, hi = 40; for (let i = 0; i < 40; i++) { const r = (lo + hi) / 2; if (r * Math.tan(-pitch * C.RAD) > -floor(r)) hi = r; else lo = r; } dist = Math.min(40, lo / Math.cos(pitch * C.RAD)); }
        n.fill((1 / dist - 1 / 40) / (1 / 2 - 1 / 40), y * w, (y + 1) * w);
    }
    // where the world is as deep as the place it shows, a thing stands where it is and keeps its size
    const here = C.stood(field, 3, 0, 2);
    near(here.small, 0.985, 0.01); near(here.pos[0], 3 * 0.985, 0.03); near(here.pos[1], -2 * 0.985, 0.03); near(here.pos[2], 0, 1e-9);
    // twenty metres away on the map the world is only eight metres deep: it stands there, on the slope, and is smaller
    const there = C.stood(field, 0, -20, 2);
    near(-there.pos[2], 8.33 * 0.985, 0.12, 'how far'); near(there.pos[1], -0.833 * 0.985, 0.03, 'how high'); near(there.small, 8.33 / 20 * 0.985, 0.01, 'how small');
    // seen from the centre it is where the picture has that spot of ground: the same direction
    near(there.pos[1] / -there.pos[2], -2 / 20, 1e-9);
    // lifted off the ground, it is lifted as much as it is small
    near(C.stood(field, 0, -20, 2, 1).pos[1] - there.pos[1], there.small, 1e-9);
    // and back: the place on the map of a point of the surface
    const back = C.mapped(there.pos, 2);
    near(back.x, 0, 1e-9); near(back.z, -20, 1e-6); near(back.small, there.small, 1e-6);
    assert.ok(Math.hypot(C.mapped([0, 0.3, -5], 2).z) > 80);               // above the eyes: on no ground, very far
});

test('steps follow the metres walked along a path', () => {
    const path = [{ t: 0, pos: [0, 0, 0] }, { t: 2, pos: [0, 0, -4] }, { t: 5, pos: [0, 0, -4] }, { t: 6, pos: [3, 0, -4] }];
    near(C.pathWalked(path, 0), 0); near(C.pathWalked(path, 1), 2); near(C.pathWalked(path, 2), 4);
    near(C.pathWalked(path, 4), 4);                                         // waiting: no steps
    near(C.pathWalked(path, 6), 7); near(C.pathWalked(path, 60), 7);
});

test('a mask gets a soft edge that stays where the edge was, and what is far can be marked', () => {
    const w = 256, hgt = 128, mask = new Uint8Array(w * hgt);
    for (let y = 40; y < 90; y++) for (let x = 0; x < 60; x++) { mask[y * w + x] = 255; mask[y * w + (w - 1 - x)] = 255; }     // across the seam
    assert.strictEqual(C.feathered(mask, w, hgt, 0), mask);
    const soft = C.feathered(mask, w, hgt, 12);
    assert.ok(soft[64 * w + 2] > 250 && soft[64 * w + (w - 2)] > 250, 'the middle stays full, on both sides of the seam');
    assert.ok(soft[64 * w + 128] < 3 && soft[10 * w + 5] < 3, 'far from it stays empty');
    assert.ok(Math.abs(soft[64 * w + 59] - 140) < 30 && Math.abs(soft[64 * w + 60] - 115) < 30, 'half-way at the edge: ' + soft[64 * w + 59]);
    assert.ok(soft[64 * w + 52] > soft[64 * w + 59] && soft[64 * w + 59] > soft[64 * w + 66] && soft[64 * w + 66] > 0, 'a slope');
    const wide = C.feathered(mask, w, hgt, 30);
    assert.ok(wide[64 * w + 70] > soft[64 * w + 70], 'a larger radius reaches farther');
    // what is far: a world that is 4 m away ahead and 20 m away to the right
    const n = new Float32Array(8 * 4).fill(1); for (let y = 0; y < 4; y++) { n[y * 8 + 5] = 0; n[y * 8 + 6] = 0; }
    const field = new C.DepthField(n, 8, 4).tune(4, 20, 1);
    const far = C.farMask(field, 64, 32, 12, 90, 60);
    assert.strictEqual(far[16 * 64 + 48], 255);                             // to the right (yaw 90): far
    assert.strictEqual(far[16 * 64 + 32], 0);                               // ahead: near
    assert.strictEqual(C.farMask(field, 64, 32, 12, -90, 60)[16 * 64 + 48], 0);     // far, but not where we look
    // a gate across the way: 10 m ahead (to the right, yaw 90), 10 m wide, from the ground 2 m below the eyes to the sky
    const gw = 360, gh = 180, gate = C.gateMask(gw, gh, 90, 2, 10, 10, 0);
    const at = (yaw, pitch) => gate[Math.floor((90 - pitch) / 180 * gh) * gw + Math.floor((yaw + 180) / 360 * gw)];
    assert.strictEqual(at(90, 0), 255); assert.strictEqual(at(90, 60), 255);        // straight through it, and up to the sky
    assert.strictEqual(at(90, -9), 255); assert.strictEqual(at(90, -14), 0);        // the ground line is 11.3 degrees down
    assert.strictEqual(at(114, 0), 255); assert.strictEqual(at(119, 0), 0);         // its posts are 26.6 degrees to each side
    assert.strictEqual(at(-90, 0), 0);                                              // nothing behind
    const low = C.gateMask(gw, gh, 90, 2, 10, 10, 5);                               // a gate 5 m tall: its top is 16.7 degrees up
    assert.strictEqual(low[Math.floor((90 - 14) / 180 * gh) * gw + 270], 255); assert.strictEqual(low[Math.floor((90 - 20) / 180 * gh) * gw + 270], 0);
});

test('worlds without a portal between them: the next one fades in', () => {
    const tour = { keys: [{ world: 'a', pos: [0, 0, 0], yaw: 0, pitch: 0, fov: 90, dur: 0 }, { world: 'c', pos: [0, 0, 0], yaw: 90, pitch: 0, fov: 90, dur: 3 }] };
    const compiled = C.compileTour(tour, () => null);
    near(compiled.duration, 3);
    assert.strictEqual(C.tourAt(compiled, 0).world, 'a');
    assert.strictEqual(C.tourAt(compiled, 2.9).world, 'c');
    const fading = C.tourAt(compiled, 0.85);
    assert.ok(fading.mix && fading.mix.world === 'c' && fading.mix.turn === 90);
});

test('a recorded flight is read back between its samples', () => {
    const take = { duration: 2, samples: [
        { t: 0, world: 'a', pos: [0, 0, 0], yaw: 170, pitch: 0, fov: 90, relief: 1, mix: null, travelled: 0 },
        { t: 1, world: 'a', pos: [2, 0, 0], yaw: -170, pitch: 10, fov: 80, relief: 1, mix: null, travelled: 2 },
        { t: 2, world: 'b', pos: [0, 0, 0], yaw: 0, pitch: 0, fov: 80, relief: 0, mix: null, travelled: 2 },
    ] };
    const m = C.takeAt(take, 0.5);
    near(m.pos[0], 1); near(m.yaw, 180); near(m.fov, 85); near(m.travelled, 1);
    assert.strictEqual(C.takeAt(take, 1.5).world, 'a');                    // never a blend of two worlds
    assert.strictEqual(C.takeAt(take, 5).world, 'b');
});

test('figures: eighteen joints inside the frame, the right side on the left of the picture', () => {
    for (const name of Object.keys(C.POSES)) {
        const joints = C.placePose(name, 512 / 768, 0.86, false);
        assert.strictEqual(joints.length, 18);
        for (const j of joints) assert.ok(j[0] > 0 && j[0] < 1 && j[1] > 0.02 && j[1] < 0.98, `${name} ${j}`);
    }
    const standing = C.placePose('standing', 512 / 768, 0.86, false), mirrored = C.placePose('standing', 512 / 768, 0.86, true);
    assert.ok(standing[2][0] < 0.5 && standing[5][0] > 0.5);               // facing us: its right shoulder is on our left
    near(mirrored[2][0], 1 - standing[2][0], 1e-9);
    assert.strictEqual(C.LIMBS.length, 17); assert.strictEqual(C.COLORS.length, 18);
});

/* ---- somebody's eyes -------------------------------------------------------------------------------------- */

test('a route can be seen through somebody\'s eyes: lids, a tilt, a blink that is a cut', () => {
    const tour = { keys: [
        { world: 'a', pos: [0, 0, 0], yaw: 0, pitch: 0, fov: 90, dur: 0, hold: 0, lids: 1 },
        { world: 'a', pos: [0, 0, -4], yaw: 0, pitch: 0, fov: 90, dur: 2, hold: 0, lids: 0, roll: 10 },
        { world: 'b', pos: [1, 0, 0], yaw: 90, pitch: -5, fov: 70, dur: 0.5, hold: 1, cut: 'blink' },
        { world: 'b', pos: [3, 0, 0], yaw: 90, pitch: -5, fov: 70, dur: 2, hold: 0, blink: true },
    ] };
    const made = C.compileTour(tour, () => null);
    near(made.duration, 5.5, 1e-9, 'duration');
    assert.deepStrictEqual(made.keyTimes, [0, 2, 2.5, 5.5]);
    near(C.tourAt(made, 0).lids, 1, 1e-6, 'the eyes begin shut');
    assert.ok(C.tourAt(made, 1).lids > 0.2 && C.tourAt(made, 1).lids < 0.8, 'and open on the way');
    near(C.tourAt(made, 1.99).lids, 0, 0.01, 'open at the key');
    near(C.tourAt(made, 2).roll, 10, 0.2, 'the head is tilted as the key says');
    // the blink that is a cut: shut at the end of world a, the other world from then on, open again soon after
    assert.strictEqual(C.tourAt(made, 2.2).world, 'a');
    near(C.tourAt(made, 2.2).lids, 0, 1e-6, 'still open');
    assert.ok(C.tourAt(made, 2.49).lids > 0.95, 'shut at the cut');
    const after = C.tourAt(made, 2.5);
    assert.strictEqual(after.world, 'b');
    assert.ok(after.lids > 0.95, 'still shut on the other side');
    assert.deepStrictEqual(after.pos, [1, 0, 0]);                   // exactly where the key is, no gliding in
    near(after.yaw, 90, 1e-9); near(after.relief, 1, 1e-9, 'the world is whole when the eyes open');
    assert.strictEqual(after.mix, null);
    near(C.tourAt(made, 2.5 + C.BLINK_OPEN + 0.01).lids, 0, 1e-6, 'open again');
    // a blink at a key: shut exactly as the camera gets there, open before and after
    assert.ok(C.tourAt(made, 5.5).lids > 0.95);
    near(C.tourAt(made, 5.5 - C.BLINK_CLOSE - 0.02).lids, 0, 1e-6);
});

test('a blow from behind throws the head forward and lets it come back', () => {
    const tour = { keys: [
        { world: 'a', pos: [0, 0, 0], yaw: 0, pitch: 0, fov: 90, dur: 0, hold: 1 },
        { world: 'a', pos: [0, 0, 0], yaw: 0, pitch: 0, fov: 90, dur: 1, hold: 3, jolt: 1 },
    ] };
    const made = C.compileTour(tour, () => null);
    const before = C.tourAt(made, 1.9), hit = C.tourAt(made, 2.12), later = C.tourAt(made, 4.5);
    near(before.pos[2], 0, 1e-9); near(before.pitch, 0, 1e-9);
    assert.ok(hit.pos[2] < -0.2, 'thrown ahead (ahead is -z at yaw 0)');
    assert.ok(hit.pitch < -3, 'the head goes down');
    near(later.pos[2], 0, 1e-3); near(later.pitch, 0, 1e-3); near(later.roll, 0, 1e-3);
    assert.strictEqual(C.jolted(-0.1, 1), null);
});

test('the way the camera is carried can change at a key, and a runner\'s strides follow the pace', () => {
    const tour = { keys: [
        { world: 'a', pos: [0, 0, 0], yaw: 0, pitch: 0, fov: 90, dur: 0, hold: 0, rig: 'run', rig_strength: 1 },
        { world: 'a', pos: [0, 0, -6], yaw: 0, pitch: 0, fov: 90, dur: 3, hold: 2 },
        { world: 'a', pos: [0, 0, -12], yaw: 0, pitch: 0, fov: 90, dur: 3, hold: 0, rig: 'ride', rig_strength: 0.5 },
        { world: 'a', pos: [0, 0, -20], yaw: 0, pitch: 0, fov: 90, dur: 2, hold: 0 },
    ] };
    const made = C.compileTour(tour, () => null);
    assert.strictEqual(C.tourAt(made, 1).rig, 'run');
    assert.strictEqual(C.tourAt(made, 7).rig, 'run');               // until the camera gets to the key that changes it
    assert.strictEqual(C.tourAt(made, 9).rig, 'ride');
    near(C.tourAt(made, 9).rigStrength, 0.5, 1e-9);
    assert.ok(C.tourAt(made, 1.5).speed > 1.5, 'running');
    near(C.tourAt(made, 4).speed, 0, 1e-6, 'standing');
    // the strides of a runner are large at a run and gone at a standstill; the breath stays
    const running = C.rigOffset('run', 1, 0.52, 1, 2.2), standing = C.rigOffset('run', 1, 0.52, 1, 0);
    assert.ok(Math.abs(running.up) > 0.02);
    assert.ok(Math.abs(standing.up) < 0.02 && Math.abs(standing.up) > 0);
    assert.strictEqual(C.rigOffset('pro', 1, 1, 1, 2).up, 0);
    const seen = C.carried({ pos: [0, 0, 0], yaw: 0, pitch: 0, roll: 7, fov: 80 }, 'ride', 2, 5, 1, 4);
    assert.ok(Math.abs(seen.roll - 7) < 3 && seen.fov === 80);
});

/* ---- a second camera -------------------------------------------------------------------------------------- */

test('cuts say which camera the film shows, each from its second on', () => {
    const cuts = [{ t: 7.5, cam: 'a' }, { t: 3, cam: 'b' }, { t: 12, cam: 'b' }];
    assert.strictEqual(C.shotAt(cuts, 0), 'a', 'the route before the first cut');
    assert.strictEqual(C.shotAt(cuts, 2.99), 'a');
    assert.strictEqual(C.shotAt(cuts, 3), 'b', 'from the cut\'s own second');
    assert.strictEqual(C.shotAt(cuts, 7.4), 'b');
    assert.strictEqual(C.shotAt(cuts, 8), 'a');
    assert.strictEqual(C.shotAt(cuts, 99), 'b');
    assert.strictEqual(C.shotAt(null, 5), 'a');
    assert.strictEqual(C.shotAt([{ t: 1, cam: 'x' }], 5), 'a', 'what is not the second camera is the main one');
});

test('a camera that follows a figure stands beside it, behind it, before it or goes round it, and looks at it', () => {
    const feet = [2, -1.6, -3], tall = 1.7, facing = 0;              // facing -z
    const dist = (a, b) => Math.hypot(a[0] - b[0], a[2] - b[2]);
    // beside it, at its right (+x for a figure facing -z), looking back at it (yaw -90: toward -x)
    const right = C.followCam({ mode: 'side', side: 1, distance: 2, height: 0.6, aim: 0.6 }, feet, tall, facing, 0);
    near(right.pos[0], 2 + 3.4, 1e-9, 'right of it'); near(right.pos[2], -3, 1e-9); near(right.pos[1], -1.6 + 0.6 * 1.7, 1e-9);
    near(right.yaw, -90, 1e-9, 'looks at it'); near(right.pitch, 0, 1e-9, 'level: it looks at the height it stands at');
    const left = C.followCam({ mode: 'side', side: -1, distance: 2 }, feet, tall, facing, 0);
    assert.ok(left.pos[0] < 2, 'its left');
    // behind it: farther back than its feet and a little off one shoulder, looking past it the way it goes
    const behind = C.followCam({ mode: 'behind' }, feet, tall, facing, 0);
    assert.ok(behind.pos[2] > feet[2] + 1, 'behind it (it faces -z)');
    assert.ok(Math.abs(behind.pos[0] - feet[0]) > 0.1, 'off one shoulder');
    assert.ok(Math.abs(C.turn(behind.yaw, 0)) < 25, 'looking where it goes');
    // before it: in front, looking back at its face
    const front = C.followCam({ mode: 'front', distance: 2 }, feet, tall, 90, 0);   // it faces +x
    assert.ok(front.pos[0] > feet[0] + 3, 'before it'); assert.ok(Math.abs(C.turn(front.yaw, -90)) < 20, 'looking back at it');
    // going round: the same distance at every moment, a quarter of the way round in 90 / turn seconds
    const a = C.followCam({ mode: 'round', distance: 2, turn: 30 }, feet, tall, facing, 0), b = C.followCam({ mode: 'round', distance: 2, turn: 30 }, feet, tall, facing, 3);
    near(dist(a.pos, feet), 3.4, 1e-9); near(dist(b.pos, feet), 3.4, 1e-9);
    near(Math.acos(((a.pos[0] - feet[0]) * (b.pos[0] - feet[0]) + (a.pos[2] - feet[2]) * (b.pos[2] - feet[2])) / 3.4 / 3.4) * 180 / Math.PI, 90, 1e-6, 'turned 90°');
    // turning the figure turns the camera with it
    const turned = C.followCam({ mode: 'side', side: 1, distance: 2 }, feet, tall, 90, 0);
    near(turned.pos[2], -3 + 3.4, 1e-9, 'facing +x, its right is +z');
    near(C.followCam({ fov: 300 }, feet, tall, 0, 0).fov, 150, 1e-9, 'a lens is a lens');
});

test('a path key may be reached at an even pace instead of eased in and out', () => {
    near(C.pathShare({ line: true }, 0.25), 0.25, 1e-12);
    near(C.pathShare({}, 0.25), 0.15625, 1e-12, 'eased: smoothstep');
    near(C.pathShare({ line: true }, 1.5), 1, 1e-12);
    const path = [{ t: 0, pos: [0, 0, 0] }, { t: 1, pos: [1, 0, 0], line: true }, { t: 2, pos: [2, 0, 0], line: true }];
    near(C.pathWalked(path, 0.5), 0.5, 1e-12); near(C.pathWalked(path, 1.5), 1.5, 1e-12, 'a steady walk through a key');
});

test('a cut to the second camera may follow its own way, with that way\'s own distances', () => {
    const cuts = [{ t: 2, cam: 'b', way: 'behind' }, { t: 5, cam: 'a' }];
    assert.strictEqual(C.cutAt(cuts, 3).way, 'behind');
    assert.strictEqual(C.cutAt(cuts, 1), null, 'before the first cut: none (the main camera)');
    // a way given with nothing else: its own distance and lens
    const cam = C.followCam({ mode: 'front' }, [0, 0, 0], 2, 0, 0);
    near(Math.hypot(cam.pos[0], cam.pos[2]), C.FOLLOW_WAYS.front.distance * 2, 1e-9);
    near(cam.fov, C.FOLLOW_WAYS.front.fov, 1e-9);
    // an empty field (a cleared number) is the way's own too
    near(C.followCam({ mode: 'side', distance: '' }, [0, 0, 0], 1, 0, 0).pos[0], C.FOLLOW_WAYS.side.distance, 1e-9);
});

test('a cut may go over by a blink, through black or by a dissolve, the cut in its middle', () => {
    assert.strictEqual(C.passAt([{ t: 5, cam: 'b' }], 5), null, 'a plain cut is a cut');
    // a blink: the eyes shut just before the cut and open just after it
    const blink = [{ t: 5, cam: 'b', pass: 'blink' }];
    assert.ok(C.passAt(blink, 4.99).lids > 0.95, 'shut at the cut');
    assert.ok(C.passAt(blink, 5.01).lids > 0.95);
    assert.strictEqual(C.passAt(blink, 4.8), null, 'open before');
    assert.strictEqual(C.passAt(blink, 5.3), null, 'open after');
    // through black: black at the cut, clear at either end of its seconds
    const dip = [{ t: 5, cam: 'b', pass: 'dip', long: 1 }];
    near(C.passAt(dip, 5).dark, 1, 1e-9);
    near(C.passAt(dip, 4.75).dark, 0.5, 1e-9);
    assert.strictEqual(C.passAt(dip, 4.4), null);
    // a dissolve: half and half at the cut, from either side; the shot on the other side is the right one
    const cuts = [{ t: 2, cam: 'b' }, { t: 5, cam: 'a', pass: 'dissolve', long: 1 }];
    const before = C.passAt(cuts, 4.99), after = C.passAt(cuts, 5.01);
    assert.strictEqual(before.other, cuts[1], 'before the cut the next shot comes in');
    assert.strictEqual(after.other, cuts[0], 'after it the shot before goes out');
    near(before.amount, 0.5, 0.02); near(after.amount, 0.5, 0.02);
    near(C.passAt(cuts, 4.501).amount, 0, 1e-4, 'it begins at nothing');
    near(C.passAt(cuts, 5.4999).amount, 0, 1e-3, 'and ends at nothing');
    // a dissolve out of the start of the route: the other shot is the main camera (no cut before)
    assert.strictEqual(C.passAt([{ t: 3, cam: 'b', pass: 'dissolve' }], 3.1).other, null);
});

test('a camera on a tripod stays where it was put and turns after the figure, closing in as it goes away', () => {
    const stand = { world: 'w', pos: [0, 0, 0] };
    const close = C.followCam({ mode: 'tripod', stand, fov: 40 }, [0, -1.6, -4], 1.7, 0, 0), far = C.followCam({ mode: 'tripod', stand, fov: 40 }, [4, -1.6, -8], 1.7, 0, 3);
    assert.deepStrictEqual(close.pos, [0, 0, 0]); assert.deepStrictEqual(far.pos, [0, 0, 0], 'it does not move');
    near(close.yaw, 0, 1e-9, 'it looks at the figure'); near(far.yaw, C.anglesFromDir(4, 0, -8).yaw, 1e-9, 'and turns after it');
    near(close.fov, 40, 1e-9, 'the lens as set');
    // keeping it a third of the picture tall: a narrower lens far away, a wider one near
    const a = C.followCam({ mode: 'tripod', stand, frame: 0.33 }, [0, -1.6, -4], 1.7, 0, 0), b = C.followCam({ mode: 'tripod', stand, frame: 0.33 }, [0, -1.6, -12], 1.7, 0, 0);
    assert.ok(b.fov < a.fov / 2.4, 'closes in as it goes away');
    const tan = Math.tan(a.fov * Math.PI / 360) * 2 * Math.hypot(a.look[1], a.look[2]);
    near(1.7 / tan, 0.33, 1e-6, 'a third of the picture\'s height');
    // not placed yet: beside it
    const loose = C.followCam({ mode: 'tripod' }, [0, 0, 0], 1, 0, 0);
    near(loose.pos[0], C.FOLLOW_WAYS.tripod.distance, 1e-9);
});

test('an edit is made from who speaks when: through her eyes when he speaks, on her when she does', () => {
    const spoken = [{ start: 14.5, end: 17.5, speaker: 'man' }, { start: 20.1, end: 22.1, speaker: 'lady' }, { start: 24.6, end: 27.2, speaker: 'man' }, { start: 30, end: 31, speaker: null }];
    // she is the route's eyes: he is seen through them (the main camera), she on the second camera
    let cuts = C.dialogueCuts(spoken, 'lady', { mine: 'front' });
    assert.deepStrictEqual(cuts.map(c => [c.t, c.cam, c.target || null, c.way || null]), [[19.95, 'b', 'lady', 'front'], [24.45, 'a', null, null]]);
    // over the shoulder of the one she speaks to
    cuts = C.dialogueCuts(spoken, 'lady', { mine: 'over', pass: 'dissolve', long: 0.4 });
    assert.deepStrictEqual([cuts[0].target, cuts[0].way, cuts[0].pass, cuts[0].long], ['man', 'behind', 'dissolve', 0.4]);
    // no walker: each speaker's face, and back to the main camera after the last line
    cuts = C.dialogueCuts(spoken, null, {});
    assert.deepStrictEqual(cuts.map(c => [c.t, c.cam, c.target || null]), [[14.35, 'b', 'man'], [19.95, 'b', 'lady'], [24.45, 'b', 'man'], [27.8, 'a', null]]);
    // the same speaker twice: one shot; a voice from nowhere: the shot stays
    cuts = C.dialogueCuts([{ start: 1, end: 2, speaker: 'man' }, { start: 2.3, end: 3, speaker: 'man' }, { start: 3.5, end: 4, speaker: null }], null, {});
    assert.deepStrictEqual(cuts.map(c => c.t), [0.85, 3.6], 'back to the main camera after the last one who speaks');
    // lines that follow each other closely never put two cuts in the same moment
    cuts = C.dialogueCuts([{ start: 1, end: 1.1, speaker: 'a' }, { start: 1.05, end: 1.2, speaker: 'b' }], null, {});
    assert.ok(cuts[1].t > cuts[0].t);
    // the walker in each world (a copy of her in the next one): each copy is "her", and her shot is of that copy
    cuts = C.dialogueCuts([{ start: 1, end: 2, speaker: 'lady2' }, { start: 2.5, end: 4, speaker: 'man' }, { start: 4.5, end: 6, speaker: 'lady2' }], ['lady', 'lady2'], { mine: 'front' });
    assert.deepStrictEqual(cuts.map(c => [c.cam, c.target || null]), [['b', 'lady2'], ['a', null], ['b', 'lady2'], ['a', null]]);
});

/* ---- a world's form, a flight made into a route ------------------------------------------------------------- */

test('a world may be round, an earth (flat ground, a dome of sky) or a box (a room)', () => {
    const flat = new Float32Array(64 * 32);                    // nothing near: everything at `far`
    const f = new C.DepthField(flat, 64, 32);
    const down = (deg, yaw) => C.uvFromDir(...C.dirFromAngles(yaw || 0, -deg));
    f.tune(1.6, 14, 1, { form: 'round' });
    near(f.distAt(...down(30)), 14, 1e-6, 'round: the depth (here: far)');
    f.tune(1.6, 14, 1, { form: 'earth', eye: 1.6 });
    near(f.distAt(...down(30)), 1.6 / Math.sin(30 * Math.PI / 180), 1e-3, 'earth: the floor, 1.6 m down, looking 30° down');
    near(f.distAt(...down(2)), 14, 1e-6, 'far off the ground meets the sky');
    near(f.distAt(...down(-40)), 14, 1e-6, 'the sky is a dome');
    // somebody standing before the floor keeps their own distance
    const one = new Float32Array(64 * 32).fill(1), g = new C.DepthField(one, 64, 32).tune(0.8, 14, 1, { form: 'earth', eye: 1.6 });
    near(g.distAt(...down(30)), 0.8, 1e-6, 'a thing at 0.8 m before a floor 3.2 m away is kept');
    near(g.tune(2, 14, 1, { form: 'earth', eye: 1.6 }).distAt(...down(30)), 3.2, 1e-3, 'ground drawn at 2 m where the true floor is 3.2 m: the floor');
    // a room 6 m wide, 3 m high: walls, floor and ceiling flat
    f.tune(1.6, 14, 1, { form: 'box', eye: 1.6, room: 6, ceiling: 3 });
    near(f.distAt(...C.uvFromDir(1, 0, 0)), 3, 1e-6, 'the wall to the side: half the room');
    near(f.distAt(...C.uvFromDir(...C.dirFromAngles(45, 0))), 3 / Math.cos(45 * Math.PI / 180), 1e-3, 'into a corner: farther');
    near(f.distAt(...C.uvFromDir(0, 1, 0.001)), 1.4, 1e-2, 'the ceiling: 3 - 1.6 above');
    near(f.distAt(...C.uvFromDir(0, -1, 0.001)), 1.6, 1e-2, 'the floor');
    // a figure standing on an earth stands on flat ground: its place on the map is metric
    const at = C.stood(new C.DepthField(flat, 64, 32).tune(1.6, 40, 1, { form: 'earth', eye: 1.6 }), 3, -4, 1.6, 0);
    near(at.small, 0.985, 1e-6, 'drawn at its own size');
});

test('a flight that was flown becomes route keys: bends, waits and worlds', () => {
    const samples = [];
    // 3 s straight ahead (-z) at 1 m/s, still for 1.5 s, turning 90° to the right over 1 s, then 2 s to the right (+x), then through a portal
    for (let i = 0; i <= 390; i++) {
        const t = i / 60;
        let pos, yaw = 0;
        if (t <= 3) pos = [0, 0, -t];
        else if (t <= 4.5) pos = [0, 0, -3];
        else if (t <= 5.5) { pos = [0, 0, -3]; yaw = (t - 4.5) * 90; }
        else { pos = [t - 5.5, 0, -3]; yaw = 90; }
        samples.push({ t, world: t < 6.4 ? 'a' : 'b', pos: t < 6.4 ? pos : [0, 0, -(t - 6.4)], yaw: t < 6.4 ? yaw : 0, pitch: 0, fov: 80 });
    }
    const keys = C.takeToKeys(samples);
    assert.ok(keys.length >= 5 && keys.length <= 9, 'a handful of keys, not one a frame: ' + keys.length);
    near(keys[0].dur, 0, 1e-9);
    const wait = keys.find(k => k.hold > 0);
    assert.ok(wait && Math.abs(wait.hold - 1.5) < 0.15, 'the stop is a wait of 1.5 s: ' + (wait && wait.hold));
    assert.ok(Math.abs(wait.pos[2] + 3) < 0.05, 'where it stopped');
    assert.ok(keys.some(k => Math.abs(k.yaw - 90) < 1), 'it turned to the right');
    const worlds = keys.map(k => k.world);
    assert.strictEqual(worlds[0], 'a'); assert.strictEqual(worlds[worlds.length - 1], 'b');
    const into = keys.findIndex(k => k.world === 'b');
    assert.ok(keys[into].dur >= 1, 'going through the portal takes a second at least');
    // the times add up to the flight's own
    const total = keys.reduce((sum, k) => sum + k.dur + k.hold, 0);
    assert.ok(Math.abs(total - 6.5) < 1.2, 'as long as the flight: ' + total);
});

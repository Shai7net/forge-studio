/* The page's core without a browser: keyframes, splitting with keyframes, cutting to the beat, undo.
 * Run from the extension folder:  node tests/test_core_js.js
 */
'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const el = () => ({style: {}, classList: {add() {}, remove() {}, contains: () => false, toggle() {}}, append() {}, addEventListener() {},
    setAttribute() {}, appendChild() {}, getContext: () => null});
const window = {addEventListener() {}, setTimeout, clearTimeout, devicePixelRatio: 1};
const ctx = {window, document: {documentElement: {classList: {contains: () => false}}, createElement: el, addEventListener() {},
    querySelector: () => null, querySelectorAll: () => []}, console, setTimeout, clearTimeout, fetch: async () => { throw new Error('offline'); },
    requestAnimationFrame: () => 0, cancelAnimationFrame() {}, performance: {now: () => Date.now()}, navigator: {}, localStorage: {getItem: () => null, setItem() {}}};
ctx.globalThis = ctx;
window.document = ctx.document;
vm.createContext(ctx);
const dir = path.join(__dirname, '..', 'javascript');
for (const f of ['forge-studio-a-core.js', 'forge-studio-b-strings.js']) vm.runInContext(fs.readFileSync(path.join(dir, f), 'utf8'), ctx, {filename: f});
const FS = window.ForgeStudio, S = FS.state;
FS.save = async () => null;

function fresh(clips) {
    S.project = 'test';
    S.doc = {settings: {width: 1080, height: 1920, fps: 30, background: '#000'},
        tracks: [{id: 'v1', kind: 'video', main: true}, {id: 'a1', kind: 'audio'}], clips};
    S.history = []; S.future = [];
    S.time = 0;
}
const same = (a, b) => assert.strictEqual(JSON.stringify(a), JSON.stringify(b));
const near = (a, b, msg) => assert.ok(Math.abs(a - b) < 1e-6, `${msg}: ${a} != ${b}`);
let passed = 0;
function test(name, fn) { fn(); passed++; console.log('ok', name); }

test('a keyed value moves smoothly between keys and holds outside them', () => {
    fresh([{id: 'a', track: 'v1', kind: 'image', start: 2, duration: 4, keys: {x: [[1, 0], [3, 1]]}}]);
    const c = FS.clip('a');
    near(FS.at(c, 'x', 2.5), 0, 'before the first key');
    near(FS.at(c, 'x', 4), 0.5, 'halfway');
    near(FS.at(c, 'x', 3.5), 0.15625, 'eased');
    near(FS.at(c, 'x', 9), 1, 'after the last key');
    c.keyEase = 'linear';
    near(FS.at(c, 'x', 3.5), 0.25, 'even');
    near(FS.at(c, 'scale', 3), 1, 'no keys: the plain value');
});

test('setAt adds a key at the playhead only for keyed values', () => {
    fresh([{id: 'a', track: 'v1', kind: 'image', start: 0, duration: 4, keys: {x: [[0, 0]]}}]);
    const c = FS.clip('a');
    FS.setAt(c, 'x', 0.5, 2);
    same(c.keys.x, [[0, 0], [2, 0.5]]);
    FS.setAt(c, 'x', 0.7, 2.01);   // the same frame: that key moves
    same(c.keys.x, [[0, 0], [2, 0.7]]);
    FS.setAt(c, 'y', 0.3, 1);
    assert.strictEqual(c.y, 0.3);
    assert.ok(!c.keys.y);
});

test('splitting keeps the motion where it was on both halves', () => {
    fresh([{id: 'a', track: 'v1', kind: 'image', start: 0, duration: 4, keyEase: 'linear', keys: {x: [[0, 0], [4, 1]]}}]);
    const before = [0.5, 1, 3, 3.5].map((t) => FS.at(FS.clip('a'), 'x', t));
    const [rightId] = FS.split(['a'], 2);
    const left = FS.clip('a'), right = FS.clip(rightId);
    const after = [0.5, 1].map((t) => FS.at(left, 'x', t)).concat([3, 3.5].map((t) => FS.at(right, 'x', t)));
    after.forEach((v, i) => near(v, before[i], 'split at ' + [0.5, 1, 3, 3.5][i]));
});

test('cut to the beat: every shot ends on a bar, short videos on the last beat they reach, undo restores', () => {
    const beats = [];
    for (let t = 0.5; t < 20; t += 0.5) beats.push(+t.toFixed(3));   // 120 a minute
    FS.beats['song.wav'] = {tempo: 120, beats, bar: 1, duration: 20};
    S.info['long.mp4'] = {duration: 10};
    S.info['short.mp4'] = {duration: 1.2};
    fresh([
        {id: 'p1', track: 'v1', kind: 'image', media: 'p.png', start: 0, duration: 3},
        {id: 'v1', track: 'v1', kind: 'video', media: 'short.mp4', start: 3, duration: 1.2},
        {id: 'v2', track: 'v1', kind: 'video', media: 'long.mp4', start: 4.2, duration: 2},
        {id: 'm', track: 'a1', kind: 'audio', media: 'song.wav', start: 0, duration: 20, beats: true},
    ]);
    const was = JSON.stringify(S.doc);
    const n = FS.cutToBeat('m', 4);
    assert.strictEqual(n, 3);
    const ends = FS.clipsOn('v1').map((c) => +FS.end(c).toFixed(3));
    // bars ("ones") at beats 1, 5, 9…: 1.0, 3.0, 5.0, 7.0 s; the 1.2 s video reaches only 4.0 s (a beat, not a bar)
    same(ends, [1, 2, 3]);
    FS.undo();
    assert.strictEqual(JSON.stringify(S.doc), was);
    FS.cutToBeat('m', 0);   // the nearest beat to where each shot ends now
    same(FS.clipsOn('v1').map((c) => +FS.end(c).toFixed(3)), [3, 4, 6]);
});

test('music steps back only where the speech really talks, as duck.py does', () => {
    FS.talk['voice-a.wav'] = [[0.5, 1.5], [1.9, 2.4], [3.5, 4.5]];
    fresh([
        {id: 'm', track: 'a1', kind: 'audio', media: 'song.wav', start: 0, duration: 8, duck: 0.25},
        {id: 'v', track: 'a2', kind: 'audio', media: 'voice-a.wav', start: 1, duration: 4.5, in: 0.2},
    ]);
    S.doc.tracks.push({id: 'a2', kind: 'audio'});
    // file 0.5–1.5 and 1.9–2.4 play at 1.3–2.3 and 2.7–3.2 (joined: the pause is shorter than attack + release);
    // 3.5–4.5 plays at 4.3–5.3, cut to the clip's end at 5.5
    same(FS.duckRanges().map((r) => r.map((x) => +x.toFixed(3))), [[1.3, 3.2], [4.3, 5.3]]);
    const m = FS.clip('m');
    near(FS.duckGain(m, 0.5), 1, 'before');
    near(FS.duckGain(m, 1.3 - 0.075), 0.625, 'halfway down');
    near(FS.duckGain(m, 2), 0.25, 'under speech');
    near(FS.duckGain(m, 3.2 + 0.3), 0.625, 'halfway up');
    near(FS.duckGain(m, 4), 1, 'in the pause');
    FS.clip('v').speech = false;
    same(FS.duckRanges(), []);
    near(FS.duckGain(m, 2), 1, 'not speech any more');
    delete FS.clip('v').speech;
    FS.clip('v').muted = true;
    same(FS.duckRanges(), []);
});

test('transitions: dips cut at the middle, slides and pushes move eased, as render.py', () => {
    fresh([
        {id: 'a', track: 'v1', kind: 'video', media: 'a.mp4', start: 0, duration: 2},
        {id: 'b', track: 'v1', kind: 'image', media: 'b.png', start: 2, duration: 2},
    ]);
    FS.transition('b', 1, 'black');
    const a = FS.clip('a'), b = FS.clip('b');
    near(FS.transitionLook(b, 2.25).alpha, 0, 'hidden before the middle');
    near(FS.transitionLook(b, 2.5).alpha, 1, 'the cut at the middle');
    near(FS.dipAt(b, 2.25).alpha, 0.5, 'half black');
    near(FS.dipAt(b, 2.5).alpha, 1, 'all black');
    assert.strictEqual(FS.dipAt(b, 3.2), null);
    FS.transition('b', 1, 'push:left');
    assert.strictEqual(b.transition, 'push:left');
    near(FS.transitionLook(b, 2.25).dx, -(1 - 0.15625), 'coming in from the left, eased');
    near(FS.transitionLook(a, 2.25).dx, 0.15625, 'pushed out to the right');
    near(FS.transitionLook(a, 1.5).dx, 0, 'not before the cut');
    FS.transition('b', 1, 'slide:top');
    near(FS.transitionLook(b, 2.5).dy, -0.5, 'halfway down from the top');
    near(FS.transitionLook(a, 2.5).dy, 0, 'a slide leaves the clip before where it is');
    FS.transition('b', 1, 'dissolve');
    assert.ok(!('transition' in b));
    near(FS.transitionLook(b, 2.25).alpha, 0.25, 'a dissolve, evenly');
    FS.split(['b'], 3);
    FS.transition('b', 0);
    assert.ok(!('transitionIn' in b) && !('transition' in b));
});

test('a freeze frame: split, the still between the halves as the clip looks there, the rest pushed, one undo', () => {
    fresh([
        {id: 'a', track: 'v1', kind: 'video', media: 'a.mp4', start: 0, duration: 4, in: 1, x: 0.1, keys: {scale: [[0, 1], [4, 2]]}, keyEase: 'linear', brightness: 1.2},
        {id: 'b', track: 'v1', kind: 'video', media: 'b.mp4', start: 4, duration: 2},
        {id: 't', track: 'v2', kind: 'text', start: 5, duration: 1},
    ]);
    S.doc.tracks.splice(1, 0, {id: 'v2', kind: 'video'});
    const was = JSON.stringify(S.doc);
    near(FS.sourceTime(FS.clip('a'), 1), 2, 'the file second under the playhead');
    const still = FS.freezeAt('a', 1, 'a-freeze.png', 2);
    const on = FS.clipsOn('v1').map((c) => [c.media, c.start, +c.duration.toFixed(3)]);
    same(on, [['a.mp4', 0, 1], ['a-freeze.png', 1, 2], ['a.mp4', 3, 3], ['b.mp4', 6, 2]]);
    near(still.scale, 1.25, 'its size where it froze');
    near(still.x, 0.1, 'its place');
    near(still.brightness, 1.2, 'its colour');
    assert.ok(!still.keys, 'a still does not move');
    assert.strictEqual(FS.clip('t').start, 5, 'other tracks stay');
    near(FS.clipsOn('v1')[2].in, 2, 'the rest of the clip from the frame on');
    FS.undo();
    assert.strictEqual(JSON.stringify(S.doc), was);
    FS.freezeAt('b', 6, 'b-last.png', 1);   // at its very end: after it
    same(FS.clipsOn('v1').map((c) => c.media), ['a.mp4', 'b.mp4', 'b-last.png']);
});

test('volume keyframes: keyed like the picture, kept by a split, not a picture move', () => {
    fresh([{id: 'm', track: 'a1', kind: 'audio', media: 'song.wav', start: 0, duration: 6, volume: 0.8}]);
    S.time = 1;
    FS.toggleKey('m', 'volume');
    const m = FS.clip('m');
    same(m.keys.volume, [[1, 0.8]]);
    FS.setAt(m, 'volume', 0.2, 3);
    near(FS.at(m, 'volume', 2), 0.5, 'halfway down, eased');
    near(FS.at(m, 'volume', 5), 0.2, 'held after the last key');
    assert.ok(!FS.keyed(m), 'volume keys do not make it a moving picture');
    m.keyEase = 'linear';
    const before = [1.5, 2.5, 4].map((t) => FS.at(m, 'volume', t));
    const [right] = FS.split(['m'], 2);
    const after = [FS.at(m, 'volume', 1.5), FS.at(FS.clip(right), 'volume', 2.5), FS.at(FS.clip(right), 'volume', 4)];
    after.forEach((v, i) => near(v, before[i], 'split at ' + [1.5, 2.5, 4][i]));
});

test('looks: set the five colour values, are recognised again, and natural clears them', () => {
    fresh([{id: 'a', track: 'v1', kind: 'video', media: 'a.mp4', start: 0, duration: 2},
        {id: 's', track: 'a1', kind: 'audio', media: 's.wav', start: 0, duration: 2}]);
    FS.applyLook(['a', 's'], 'vintage');
    const a = FS.clip('a');
    same([a.brightness, a.contrast, a.saturation, a.warmth, a.tint], FS.LOOKS.vintage);
    assert.strictEqual(FS.lookOf(a), 'vintage');
    assert.ok(!('warmth' in FS.clip('s')), 'sound has no colour');
    near(FS.gains(a)[0], 1.1125 * 1.015, 'red up');
    a.contrast = 0.9;
    assert.strictEqual(FS.lookOf(a), '', 'changed by hand: its own colour');
    FS.applyLook(['a'], 'none');
    assert.ok(FS.COLOUR.every((k) => !(k in a)));
    assert.strictEqual(FS.lookOf(a), 'none');
});

test('crop: the part that shows keeps its place and size, opposite sides leave 5%', () => {
    fresh([{id: 'a', track: 'v1', kind: 'image', media: 'a.png', start: 0, duration: 2, cropL: 0.8, cropR: 0.8}]);
    const v = FS.cropOf(FS.clip('a'));
    near(v[0] + v[2], 0.95, 'squeezed to 95%');
    near(v[0], 0.475, 'evenly');
    same(FS.cropOf({kind: 'text', cropL: 0.5}), [0, 0, 0, 0]);
    same(FS.cropOf({kind: 'audio', cropL: 0.5}), [0, 0, 0, 0]);
});

test('shape masks: circle or rounded, only on pictures, values kept in range', () => {
    same(FS.maskOf({kind: 'video', mask: 'circle'}), {shape: 'circle', round: 0.15, soft: 0});
    same(FS.maskOf({kind: 'image', mask: 'rounded', maskRound: 0.9, maskSoft: 0.5}), {shape: 'rounded', round: 0.5, soft: 0.25});
    assert.strictEqual(FS.maskOf({kind: 'text', mask: 'circle'}), null);
    assert.strictEqual(FS.maskOf({kind: 'video', mask: 'star'}), null);
});

test('markers: M adds one at the playhead, again takes it away, names, one undo each', () => {
    fresh([{id: 'a', track: 'v1', kind: 'video', media: 'a.mp4', start: 0, duration: 4}]);
    FS.toggleMarker(2.01);
    FS.toggleMarker(1);
    same(FS.markers().map((m) => m.t), [1, 2]);
    FS.renameMarker(1, 'drop');
    assert.strictEqual(FS.markers()[1].name, 'drop');
    FS.toggleMarker(1.01);   // the same frame: taken away
    same(FS.markers().map((m) => m.t), [2]);
    FS.undo();
    same(FS.markers().map((m) => m.t), [1, 2]);
    FS.removeMarker(0); FS.removeMarker(0);
    assert.ok(!('markers' in S.doc));
});

test('style: copied whole, pasted where it means something, motion stretched to the new length, one undo', () => {
    fresh([
        {id: 'src', track: 'v1', kind: 'video', media: 'a.mp4', start: 0, duration: 2, warmth: 0.5, cropL: 0.2, mask: 'circle', scale: 1.5,
            keys: {x: [[0, 0], [2, 0.3]], volume: [[0, 1], [2, 0.2]]}, volume: 0.8},
        {id: 'v', track: 'v1', kind: 'video', media: 'b.mp4', start: 2, duration: 4, brightness: 1.3, x: 0.4},
        {id: 's', track: 'a1', kind: 'audio', media: 's.wav', start: 0, duration: 1},
        {id: 't', track: 'v1', kind: 'text', start: 6, duration: 1},
    ]);
    const was = JSON.stringify(S.doc);
    FS.copyStyle('src');
    assert.strictEqual(FS.pasteStyle(['v', 's', 't']), 3);
    const v = FS.clip('v'), s = FS.clip('s'), t = FS.clip('t');
    same([v.warmth, v.cropL, v.mask, v.scale, v.volume, v.brightness, v.x], [0.5, 0.2, 'circle', 1.5, 0.8, undefined, undefined]);
    same(v.keys.x, [[0, 0], [4, 0.3]]);
    same(v.keys.volume, [[0, 1], [4, 0.2]]);
    same([s.volume, s.warmth, s.cropL], [0.8, undefined, undefined]);
    same(s.keys, {volume: [[0, 1], [1, 0.2]]});
    same([t.warmth, t.mask, t.scale], [undefined, undefined, 1.5]);
    FS.undo();
    assert.strictEqual(JSON.stringify(S.doc), was);
});

test('animations: the same numbers as render.py anim_at, the way out is the way in backwards', () => {
    const c = {start: 1, duration: 2, animIn: 'pop', animInSec: 1, animOut: 'rise', animOutSec: 0.5};
    const pop = FS.animAt(c, 1.25);
    near(pop.scale, 0.6 + 0.4 * (1 - 2.70158 * Math.pow(0.75, 3) + 1.70158 * Math.pow(0.75, 2)), 'pop a quarter in');
    near(pop.alpha, 1, 'quickly seen');
    const rise = FS.animAt(c, 2.75);   // halfway out
    near(rise.dy, 0.03, 'half of 6% lower');
    near(rise.alpha, 0.5, 'half seen');
    const w = FS.animAt({start: 0, duration: 2, animIn: 'wipe-r', animInSec: 1}, 0.5, [0.2, 0.7, 0.8, 0.9]);
    same(w.reveal.map((x) => +x.toFixed(6)), [0.5, 1]);
    same(w.about.map((x) => +x.toFixed(6)), [0.5, 0.8]);
    same(FS.animSpans({duration: 0.6, animIn: 'fade', animInSec: 0.5, animOut: 'fade', animOutSec: 0.5}).map((x) => +x.toFixed(6)), [0.3, 0.3]);
    assert.ok(!FS.animated({animIn: 'spin'}));
});

test('separating the sound: a sound clip of the same file and trim below, the video quiet, one undo', () => {
    fresh([
        {id: 'v', track: 'v1', kind: 'video', media: 'talk.mp4', start: 2, duration: 3, in: 1.5, speed: 2, volume: 0.7, fadeOut: 0.4,
            keys: {x: [[0, 0], [3, 0.2]], volume: [[0, 1], [3, 0.5]]}, duck: 0.25},
        {id: 'm', track: 'a1', kind: 'audio', media: 'song.wav', start: 0, duration: 3},
    ]);
    const was = JSON.stringify(S.doc);
    const s = FS.detachAudio('v');
    const v = FS.clip('v');
    same([s.kind, s.media, s.start, s.duration, s.in, s.speed, s.volume, s.fadeOut, s.duck], ['audio', 'talk.mp4', 2, 3, 1.5, 2, 0.7, 0.4, 0.25]);
    same(s.keys, {volume: [[0, 1], [3, 0.5]]});
    assert.ok(s.track !== 'a1', 'not over the music');
    same([v.muted, v.duck, Object.keys(v.keys)], [true, undefined, ['x']]);
    FS.undo();
    assert.strictEqual(JSON.stringify(S.doc), was);
});

test('speed ramps: the file time is the integral of the speed, splits and trims keep each moment', () => {
    fresh([{id: 'v', track: 'v1', kind: 'video', media: 'a.mp4', start: 1, duration: 4, in: 2, keyEase: 'linear',
        keys: {speed: [[0, 1], [2, 3], [4, 3]]}}]);
    const v = FS.clip('v');
    // 1 to 3 over 2 s (2 + 4 = 4 s of file), then 3 for 2 s (6 s): 10 s of file in 4 s
    near(FS.spanOf(v), 10, 'all of it');
    near(FS.sourceTime(v, 2), 2 + 1.5, 'one second in: 1 + 2, halved');
    near(FS.sourceTime(v, 3), 2 + 4, 'two seconds in');
    near(FS.sourceTime(FS.clip('v'), 1), 2, 'at the start');
    const before = [1.5, 2.5, 3.5, 4.5].map((t) => FS.sourceTime(v, t));
    const [r] = FS.split(['v'], 3);
    const after = [1.5, 2.5].map((t) => FS.sourceTime(FS.clip('v'), t)).concat([3.5, 4.5].map((t) => FS.sourceTime(FS.clip(r), t)));
    after.forEach((x, i) => near(x, before[i], 'split at ' + [1.5, 2.5, 3.5, 4.5][i]));
    // a ready-made ramp, scaled down to fit a short file
    fresh([{id: 'w', track: 'v1', kind: 'video', media: 'short.mp4', start: 0, duration: 4, in: 0}]);
    S.info['short.mp4'] = {duration: 5};
    FS.applyRamp('w', 'hero');
    assert.ok(FS.ramped(FS.clip('w')));
    assert.ok(FS.spanOf(FS.clip('w')) <= 5, FS.spanOf(FS.clip('w')));
    FS.applyRamp('w', '');
    assert.ok(!FS.ramped(FS.clip('w')));
});

test('chroma key: the same numbers as render.py chroma', () => {
    const k = FS.keyOf({kind: 'video', key: '#00ff00'});
    same([k.rgb, k.dom, k.similar, k.soft, k.spill], [[0, 255, 0], 1, 0.3, 0.1, 0.6]);
    const px = new Uint8ClampedArray([0, 255, 0, 255, 100, 200, 90, 255, 200, 40, 30, 255]);
    FS.applyKey(k, px);
    // the key colour gone; a greenish pixel partly see-through with its green pulled down; a red one untouched
    same(Array.from(px), [0, 102, 0, 0, 100, 140, 90, 74, 200, 40, 30, 255]);
    assert.strictEqual(FS.keyOf({kind: 'text', key: '#00ff00'}), null);
    assert.strictEqual(FS.keyOf({kind: 'video', key: 'green'}), null);
});

test('blurred fill: sigma a part of the frame, margin 3 sigma, only on pictures', () => {
    fresh([]);
    same(FS.fillOf({kind: 'image', fill: 'blur'}), {sigma: 0.03 * 1920, margin: Math.ceil(3 * 0.03 * 1920), dim: 0.85});
    same(FS.fillOf({kind: 'video', fill: 'blur', fillBlur: 0.5}).sigma, 0.1 * 1920);
    assert.strictEqual(FS.fillOf({kind: 'text', fill: 'blur'}), null);
});

test('auto reframe: fills the frame, keeps the subject in the middle, never past the edge of the picture', () => {
    fresh([{id: 'v', track: 'v1', kind: 'video', media: 'wide.mp4', start: 0, duration: 2, in: 1}]);
    S.info['wide.mp4'] = {width: 1920, height: 1080, duration: 10};
    // the subject walks from a third of the way across to the right edge, in file seconds 1..3
    const pts = [];
    for (let t = 1; t <= 3.001; t += 0.25) pts.push([t, 1 / 3 + (t - 1) / 2 * (0.95 - 1 / 3), 0.5, 0.1]);
    const n = FS.applyReframe('v', pts);
    const c = FS.clip('v');
    // a 16:9 picture in a 9:16 frame: 3.16 times bigger fills it (1080 x 1920 frame, picture 1080 wide when fitted)
    near(c.scale, (1920 / 1080) / (1080 / 1920), 'covers the frame');
    assert.ok(n >= 5 && !c.keys.y, n);
    const dw = 1080 * c.scale, room = (dw - 1080) / 2 / 1080;
    near(c.keys.x[0][1], -(1 / 3 + 0.0 - 0.5) * dw / 1080, 'the subject in the middle at the start');
    near(c.keys.x[c.keys.x.length - 1][1], -room, 'at the right edge: as far as the picture goes, no further');
    assert.ok(c.keys.x.every(([, v]) => Math.abs(v) <= room + 1e-9));
    FS.undo();
    assert.ok(!FS.clip('v').keys);
});

test('a marked part: I / O, and a copy of the edit cut to it keeps every moment', () => {
    fresh([
        {id: 'a', track: 'v1', kind: 'video', media: 'a.mp4', start: 0, duration: 3, in: 1, keyEase: 'linear', keys: {x: [[0, 0], [3, 0.3]]}},
        {id: 'b', track: 'v1', kind: 'video', media: 'b.mp4', start: 3, duration: 4, in: 0.5, transitionIn: 0.5, keyEase: 'linear',
            keys: {speed: [[0, 1], [4, 2]]}, fadeOut: 1},
        {id: 'm', track: 'a1', kind: 'audio', media: 'm.wav', start: 0, duration: 7, in: 2},
        {id: 'late', track: 'v1', kind: 'image', media: 'p.png', start: 7, duration: 2},
    ]);
    FS.markRange('in', 2); FS.markRange('out', 5);
    same(FS.range(), {in: 2, out: 5});
    const before = (id, t) => [FS.sourceTime(FS.clip(id), t), FS.at(FS.clip(id), 'x', t), FS.at(FS.clip(id), 'speed', t)];
    const was = [[ 'a', 2.5], ['b', 3.2], ['b', 4.9], ['m', 4]].map(([id, t]) => before(id, t));
    const d = FS.docInRange(S.doc, 2, 5);
    same(d.clips.map((c) => [c.id, +c.start.toFixed(6), +c.duration.toFixed(6)]), [['a', 0, 1], ['b', 1, 2], ['m', 0, 3]]);
    const at = (id, t) => { const c = d.clips.find((x) => x.id === id); return [FS.sourceTime(c, t), FS.at(c, 'x', t), FS.at(c, 'speed', t)]; };
    [['a', 0.5], ['b', 1.2], ['b', 2.9], ['m', 2]].map(([id, t]) => at(id, t)).forEach((v, i) => v.forEach((x, j) => near(x, was[i][j], 'moment ' + i + '.' + j)));
    assert.ok(!('range' in d) && d.clips.every((c) => c.id !== 'late'));
    near(d.clips.find((c) => c.id === 'b').transitionIn, 0.5, 'its clip before is still there: kept');
    assert.strictEqual(d.clips.find((c) => c.id === 'b').fadeOut, 0, 'the fade fell outside the part');
    FS.clearRange();
    assert.strictEqual(FS.range(), null);
});

test('lip sync: the clip plays the talking take from its start at normal speed, and goes back exactly', () => {
    FS.clipActions = FS.clipActions || [];   // the AI module (not loaded here) makes this list in the page
    vm.runInContext(fs.readFileSync(path.join(dir, 'forge-studio-l-lips.js'), 'utf8'), ctx, {filename: 'l-lips'});
    fresh([{id: 'v', track: 'v1', kind: 'video', media: 'char.mp4', start: 0, duration: 2, in: 1.5, speed: 2, keyEase: 'linear',
        keys: {speed: [[0, 2], [2, 1]], x: [[0, 0], [2, 0.1]]}},
        {id: 'w', track: 'v1', kind: 'video', media: 'next.mp4', start: 2, duration: 1}]);
    const was = JSON.stringify(FS.clip('v'));
    FS.takeLips('v', 'char-lips-1.mp4', 4.5);
    const v = FS.clip('v');
    same([v.media, v.in, v.speed, v.duration, Object.keys(v.keys)], ['char-lips-1.mp4', 0, undefined, 4.5, ['x']]);
    assert.strictEqual(FS.clip('w').start, 4.5, 'the next shot moves along');
    FS.lipsBack('v');
    const back = FS.clip('v');
    same([back.media, back.in, back.speed, back.duration, back.lips], ['char.mp4', 1.5, 2, 2, undefined]);
    same(back.keys, {x: [[0, 0], [2, 0.1]], speed: [[0, 2], [2, 1]]});
    assert.strictEqual(FS.clip('w').start, 2);
    assert.strictEqual(JSON.stringify(back).length, was.length, 'the clip is as it was');
});

test('a soft sound at a cut: equal-power crossfade, the clip before goes on from its file when it can', () => {
    S.info = S.info || {};
    S.info['a.wav'] = {duration: 6}; S.info['short.wav'] = {duration: 2}; S.info['b.wav'] = {duration: 4};
    S.info['silent.mp4'] = {duration: 9, audio: false};
    fresh([{id: 'A', track: 'a1', kind: 'audio', media: 'a.wav', start: 0, duration: 2},
        {id: 'B', track: 'a1', kind: 'audio', media: 'b.wav', start: 2, duration: 2, soundIn: 0.5}]);
    const A = FS.clip('A'), B = FS.clip('B');
    near(FS.soundCross(B), 0.5, 'B crosses for its soundIn');
    same(FS.soundOut(A), {cross: 0.5, tail: 0.5});
    for (const [t, a, b] of [[1, 1, 0], [2.1, Math.cos(Math.PI / 10), Math.sin(Math.PI / 10)], [2.25, Math.SQRT1_2, Math.SQRT1_2], [3, 0, 1]]) {
        near(FS.soundGain(A, t), a, 'A at ' + t);
        near(FS.soundGain(B, t), b, 'B at ' + t);
    }
    A.media = 'short.wav';    // no more sound in its file: out before the cut instead
    same(FS.soundOut(A), {cross: 0.5, tail: 0});
    near(FS.soundGain(A, 1.75), Math.SQRT1_2, 'halfway out before the cut');
    A.media = 'a.wav';
    // a picture transition sets it until soundIn is set; 0 turns it off
    delete B.soundIn; B.transitionIn = 0.8;
    near(FS.soundCross(B), 0.8, 'as long as the picture transition');
    B.soundIn = 0;
    near(FS.soundCross(B), 0, 'turned off');
    same(FS.soundOut(A), {cross: 0, tail: 0});
    // only between two clips that both sound
    B.soundIn = 0.5; A.muted = true;
    near(FS.soundCross(B), 0, 'muted clip before');
    A.muted = false; A.kind = 'video'; A.media = 'silent.mp4';
    near(FS.soundCross(B), 0, 'a video without sound');
});

test('typewriter: letters with their niqqud are one step each, and the count matches the export', () => {
    same(FS.graphemes('שָׁלוֹם'), ['שָׁ', 'ל', 'וֹ', 'ם']);
    assert.strictEqual(FS.typeSteps({text: 'שָׁלוֹם\nעולם', sub: 'x'}), 9);
    const c = {kind: 'text', start: 3, duration: 2, animIn: 'type', animInSec: 1, text: {text: 'abcd'}};
    same([0.1, 0.35, 0.6, 0.85, 1.5].map((lt) => FS.typedCount(c, lt, 4)), [0, 1, 2, 3, 4]);
    const out = Object.assign({}, c, {animIn: undefined, animOut: 'type', animOutSec: 1});
    same([0.5, 1.1, 1.6, 1.99].map((lt) => FS.typedCount(out, lt, 4)), [4, 3, 1, 0]);
    assert.ok(FS.typing(c) && !FS.typing(Object.assign({}, c, {kind: 'image'})));
    // in and out longer than the clip share it, as every animation does
    const both = Object.assign({}, c, {animOut: 'type', animOutSec: 3});
    same(FS.animSpans(both), [0.5, 1.5]);
});

test('templates: the parts at their times, each on a layer above the one before, in one undo', () => {
    FS.panels = FS.panels || [];
    FS.clipActions = FS.clipActions || [];
    if (!FS.addTemplate) vm.runInContext(fs.readFileSync(path.join(dir, 'forge-studio-j-text.js'), 'utf8'), ctx, {filename: 'j-text'});
    // v2 is busy at the start only (free while the signature shows): the card takes v3, the signature must not drop to v2
    fresh([{id: 'busy', track: 'v2', kind: 'image', media: 'x.png', start: 10, duration: 0.5}]);
    S.doc.tracks = [{id: 'v1', kind: 'video', main: true}, {id: 'v2', kind: 'video'}, {id: 'v3', kind: 'video'}, {id: 'a1', kind: 'audio'}];
    const ending = FS.TEMPLATES.find((t) => t.id === 'ending');
    const made = FS.addTemplate(ending, 10);
    same(made.map((c) => [c.track, c.start, c.duration, c.animIn]), [['v3', 10, 4.5, 'fade'], [made[1].track, 11, 3.5, 'pop']]);
    const order = FS.visualTracks().map((t) => t.id);
    assert.ok(order.indexOf(made[1].track) > order.indexOf('v3'), 'the signature lies over the card');
    same(S.selected, made.map((c) => c.id));
    // the opening types at about 15 letters a second
    const [open] = FS.addTemplate(FS.TEMPLATES.find((t) => t.id === 'opening'), 20);
    near(open.animInSec, Math.round(Math.max(0.3, Math.min(4 * 0.7, FS.typeSteps(open.text) / 15)) * 20) / 20, 'typing time');
    assert.strictEqual(open.animOut, 'fade');
    // one undo takes a whole template away
    const n = S.doc.clips.length;
    FS.undo();
    assert.strictEqual(S.doc.clips.length, n - 1);
    FS.undo();
    assert.strictEqual(S.doc.clips.length, n - 3);
});

test('shapes: drawn by drawText for a shape text, filled or as an outline, within their size', () => {
    const rec = () => {
        const calls = [], pts = [];
        const ctx = new Proxy({}, {get: (o, k) => (k in o ? o[k] : (...a) => {
            calls.push(k);
            if (['moveTo', 'lineTo'].includes(k)) pts.push(a.slice(0, 2));
            if (k === 'bezierCurveTo') pts.push(a.slice(4, 6));
            if (k === 'ellipse') pts.push([a[0] - a[2], a[1] - a[3]], [a[0] + a[2], a[1] + a[3]]);
            if (k === 'rect') pts.push([a[0], a[1]], [a[0] + a[2], a[1] + a[3]]);
        }), set: (o, k, v) => { o[k] = v; return true; }});
        return {ctx, calls, pts};
    };
    const W = 1000, H = 1000;
    for (const shape of FS.SHAPES) {
        const r = rec();
        FS.drawText(r.ctx, W, H, {template: 'shape', shape, size: 20, aspect: 1.5, fill: true, color: '#ff0000'});
        assert.ok(r.calls.includes(shape === 'line' ? 'stroke' : 'fill'), shape + ' filled (a line is stroked)');
        assert.ok(!r.calls.includes('fillText'), shape + ' is not drawn as words');
        for (const [x, y] of r.pts) assert.ok(Math.abs(x - 500) <= 150 + 1e-6 && Math.abs(y - 500) <= 100 + 1e-6, `${shape} inside its box: ${x},${y}`);
        assert.strictEqual(r.ctx.fillStyle, '#ff0000');
    }
    const r = rec();
    FS.drawText(r.ctx, W, H, {template: 'shape', shape: 'star', size: 20, fill: false, thick: 10});
    assert.ok(r.calls.includes('stroke') && !r.calls.includes('fill'), 'an outline');
    near(r.ctx.lineWidth, 20, 'thickness is a share of the height');
});

test('keyboard trimming: Q / W trim to the playhead keeping the outer edge, the gap closes, one undo; nudge; cuts', () => {
    FS.magnet = true;
    fresh([{id: 'a', track: 'v1', kind: 'video', media: 'a.mp4', start: 0, duration: 4, in: 1, fadeIn: 0.5, fadeOut: 0.4},
        {id: 'b', track: 'v1', kind: 'video', media: 'b.mp4', start: 4, duration: 3, in: 0, transitionIn: 0.5, transition: 'black'},
        {id: 't', track: 'v2', kind: 'text', start: 1, duration: 2, text: {text: 'x'}}]);
    S.doc.tracks = [{id: 'v1', kind: 'video', main: true}, {id: 'v2', kind: 'video'}, {id: 'a1', kind: 'audio'}];
    // W on a at 3: a ends at 3 with its fade out, b follows at once
    const [a] = FS.trimTo('end', 3, ['a']);
    const A = FS.clip(a);
    same([A.start, A.duration, A.in, A.fadeIn, A.fadeOut], [0, 3, 1, 0.5, 0.4]);
    same([FS.clip('b').start], [3]);
    // Q on b at 4 (1 s into it): b starts there, in moved on by a second, its transition kept
    const [b] = FS.trimTo('start', 4, ['b']);
    const B = FS.clip(b);
    same([B.start, B.duration, B.in, B.transitionIn, B.transition], [3, 2, 1, 0.5, 'black']);
    // nothing picked: the clip under the playhead on the main track
    same(FS.trimTo('end', 2, []).map((id) => FS.clip(id).duration), [2]);
    same(FS.trimTo('end', 0.01, ['t']), [], 'outside the clip: nothing');
    FS.undo(); FS.undo(); FS.undo();
    same(S.doc.clips.map((c) => [c.id, c.start, c.duration]), [['a', 0, 4], ['b', 4, 3], ['t', 1, 2]]);
    // nudge: the text a frame later, not over another clip, never on the magnetic main track
    assert.strictEqual(FS.nudge(['t'], 1 / FS.fps()), 1);
    near(FS.clip('t').start, 1 + 1 / FS.fps(), 'a frame later');
    assert.strictEqual(FS.nudge(['a'], 1), 0, 'main track stays');
    S.doc.clips.push({id: 'u', track: 'v2', kind: 'text', start: 3.5, duration: 1, text: {text: 'y'}});
    assert.strictEqual(FS.nudge(['t'], 1), 0, 'would cover u');
    // the cuts around the playhead
    near(FS.cutNear(2, 1), 1 + 1 / FS.fps() + 2, 'next cut');
    near(FS.cutNear(2, -1), 1 + 1 / FS.fps(), 'cut before');
    assert.strictEqual(FS.cutNear(7, 1), undefined);
});

console.log(`${passed} passed`);

/* Playback scheduling, decoder ownership and Forge's global mutation observer regression checks. */
'use strict';
const assert = require('assert'), fs = require('fs'), path = require('path'), vm = require('vm');
let stamp = 0, sequence = 0, seeks = 0, plays = 0;
const rafs = new Map();
const context = new Proxy({canvas: {width: 640, height: 360}}, {get: (o, k) => k in o ? o[k] : () => {}});
function element(tag) {
    const handlers = {};
    return {tagName: tag.toUpperCase(), style: {}, readyState: 4, paused: true, seeking: false, duration: 100,
        _time: 0, get currentTime() { return this._time; },
        set currentTime(v) { seeks++; this._time = v; this.seeking = true; },
        addEventListener(k, f) { (handlers[k] ||= []).push(f); }, removeEventListener() {},
        emit(k) { for (const f of handlers[k] || []) f(); },
        finish() { this.seeking = false; this.emit('seeked'); },
        play() { plays++; this.paused = false; return Promise.resolve(); }, pause() { this.paused = true; },
        load() {}, removeAttribute() {}, getContext() { return context; },
        classList: {contains: () => false}};
}
const window = {addEventListener() {}, devicePixelRatio: 1};
const ctx = {window, console, document: {createElement: element, documentElement: {classList: {contains: () => false}}},
    Image: class { constructor() { Object.assign(this, element('img'), {complete: true, naturalWidth: 640, naturalHeight: 360}); } },
    performance: {now: () => stamp}, setTimeout, clearTimeout,
    requestAnimationFrame(f) { rafs.set(++sequence, f); return sequence; }, cancelAnimationFrame(id) { rafs.delete(id); },
    localStorage: {getItem: () => null, setItem() {}}, ResizeObserver: class {observe() {} disconnect() {}}};
vm.createContext(ctx);
for (const file of ['a-core', 'c-player']) vm.runInContext(fs.readFileSync(path.join(__dirname, '../javascript/forge-studio-' + file + '.js'), 'utf8'), ctx);
const F = window.ForgeStudio, S = F.state, P = F.player;
F.mediaUrl = web => web; F.T = text => text;
const paint = P.draw;
P.draw = () => { P.draws = (P.draws || 0) + 1; };
function frame(ms = 17) { stamp += ms; const callbacks = [...rafs.values()]; rafs.clear(); callbacks.forEach(f => f(stamp)); }
function fresh(clips) {
    S.playing = false; rafs.clear(); P.drawRaf = 0; P.syncRequested = false;
    S.doc = {settings: {width: 640, height: 360, fps: 24}, tracks: [{id: 'v', kind: 'video'}, {id: 'a', kind: 'audio'}], clips};
    S.info = {}; S.time = 0; S.selected = [];
    F.emit('project'); for (const s of Object.values(P.sources)) if (s.el.seeking) s.el.finish();
    frame(); seeks = 0; plays = 0;
}
const shot = (id, start = 0, duration = 5) => ({id, kind: 'video', track: 'v', media: id + '.mp4', start, duration, in: 0.1});
let passed = 0;
function test(name, fn) { fn(); passed++; console.log('ok', name); }
test('180 pointer events coalesce; final seek wins without restarting an in-flight decoder', () => {
    fresh([shot('one')]);
    for (let i = 0; i < 180; i++) F.seek(i / 50);
    assert.equal(rafs.size, 1); frame();
    const el = P.sources.one.el;
    assert.equal(seeks, 1); // the initial seek is still decoding
    F.seek(2); frame(); F.seek(3); frame();
    assert.equal(seeks, 1);
    el.finish(); assert.equal(seeks, 2); assert(Math.abs(el.currentTime - 3.1) < 1e-8);
    el.finish(); frame(); const before = seeks;
    P.sync(true); P.sync(true); el.emit('loadeddata'); frame();
    assert.equal(seeks, before, 'duplicate exact sync or loadeddata must not seek again');
});
test('inactive distant decoders and stale project snapshots are released', () => {
    fresh([shot('one'), shot('far', 60)]);
    assert(P.sources.one); S.time = 60; P.sync(true);
    assert(!P.sources.one); assert(P.sources.far);
    P.lastGood = {}; P.waitSince = 123; F.emit('project');
    assert.equal(P.lastGood, null); assert.equal(P.waitSince, 0); assert(!P.sources.one);
});
test('late loadeddata/seeked events cannot rewind a video that has started playing', () => {
    fresh([shot('one')]); const el = P.sources.one.el;
    F.seek(1); frame(); const before = seeks;
    el.seeking = false; el._time = 1.3; el.paused = false; S.playing = true;
    el.emit('loadeddata'); el.emit('seeked');
    assert.equal(seeks, before); assert.equal(el.currentTime, 1.3);
    S.playing = false;
});
test('playback composites at sequence fps rather than monitor fps', () => {
    fresh([shot('one', 0, 10)]); P.sources.one.el.finish(); frame();
    P.draws = 0; P.play();
    for (let i = 0; i < 120; i++) { for (const s of Object.values(P.sources)) if (s.el.seeking) s.el.finish(); frame(1000 / 120); }
    assert(P.draws >= 23 && P.draws <= 25, 'expected 24 composites, got ' + P.draws);
    assert(plays <= 2, 'pending play must not flood the media element'); P.pause();
});
test('timecode updates retain Text nodes and do not trigger childList mutations', () => {
    let replacements = 0; const node = {nodeType: 3, data: '0'};
    const el = {firstChild: node, childNodes: [node], set textContent(v) { replacements++; }};
    for (let i = 0; i < 500; i++) F.updateText(el, i);
    assert.equal(node.data, '499'); assert.equal(replacements, 0);
});
test('slow seeks retain the complete composite without a timeout flash', () => {
    fresh([shot('one')]); P.ctx = context; P.lastGood = {};
    P.sources.one.el.seeking = true; P.waitSince = stamp - 3000;
    let clears = 0, frames = 0;
    context.fillRect = () => clears++; context.drawImage = () => frames++;
    paint(); clearTimeout(P.retry);
    assert.equal(clears, 0); assert.equal(frames, 1); P.ctx = null;
});
test('a single oversized inactive image is not retained beyond the cache budget', () => {
    fresh([shot('one')]); let released = 0;
    P.images.huge = {naturalWidth: 20000, naturalHeight: 20000, removeAttribute() { released++; }};
    P.sync(); assert.equal(released, 1); assert(!P.images.huge);
});
test('overlays load before their first visible frame', () => {
    fresh([shot('one'), {id: 'caption', kind: 'image', track: 'v', media: 'caption.png', start: 1, duration: 2}]);
    assert(P.images['caption.png']); assert.equal(P.images['caption.png'].src, 'caption.png');
});
(async () => {
    fresh([shot('one')]); P.play();
    frame(120); assert.equal(S.time, 0, 'the clock waits for asynchronous media startup');
    for (let i = 0; i < 6; i++) await Promise.resolve();
    frame(50); assert.equal(S.time, 0, 'startup delay must not become permanent source drift');
    frame(50); assert(S.time >= 0.049 && S.time <= 0.051); P.pause();
    passed++; console.log('ok timeline clock begins after asynchronous playback startup');
    fresh([shot('one')]); P.play(); F.api = () => new Promise(() => {});
    F.openProject('next');
    assert.equal(S.playing, false); assert.equal(P.sources.one.el.paused, true);
    passed++; console.log('ok project loading pauses the old media before changing its document');
    console.log(passed + ' player checks passed');
})().catch(error => { console.error(error); process.exitCode = 1; });

/* A join (AI, frame to frame) goes between the two clips it joins, and what comes after the cut on the other tracks
 * moves with it, so overlays, titles and linked sound stay on their shots; stopping it puts everything back; a
 * shorter result pulls them back by the difference. Without a browser or GPU. */
'use strict';
const assert = require('assert'), fs = require('fs'), path = require('path'), vm = require('vm');
const ctx = {window: {}, document: {documentElement: {classList: {contains: () => false}}}, console,
    setTimeout, clearTimeout, fetch: async () => { throw Error('offline'); }};
vm.createContext(ctx);
const dir = path.join(__dirname, '..', 'javascript');
for (const name of ['forge-studio-a-core.js', 'forge-studio-b-strings.js']) vm.runInContext(fs.readFileSync(path.join(dir, name), 'utf8'), ctx);
const F = ctx.window.ForgeStudio, S = F.state;
F.guard = fn => fn; F.panels = []; F.message = () => {}; F.save = async () => {};
vm.runInContext(fs.readFileSync(path.join(dir, 'forge-studio-g-ai.js'), 'utf8'), ctx);
F.gen.poll = () => {};
F.english = async (t) => t;

// the shape of "Two Windows": shots on the main track, a darkened copy over s13, a title over s14, music under all
function fresh() {
    S.project = 'test'; S.selected = []; S.media = {}; S.info = {};
    S.doc = {settings: {fps: 24, width: 320, height: 192},
        tracks: [{id: 'v1', kind: 'video', main: true}, {id: 'v2', kind: 'video'}, {id: 'v4', kind: 'video'},
            {id: 'v5', kind: 'video'}, {id: 'a1', kind: 'audio'}],
        clips: [
            {id: 's12', kind: 'video', track: 'v1', media: 's12.mp4', start: 0, duration: 4, in: 0},
            {id: 's13', kind: 'video', track: 'v1', media: 's13.mp4', start: 4, duration: 4, in: 0},
            {id: 's14', kind: 'video', track: 'v1', media: 's14.mp4', start: 8, duration: 4, in: 0},
            {id: 'd13', kind: 'video', track: 'v2', media: 'dark13.webm', start: 4.5, duration: 4, in: 0},
            {id: 'n14', kind: 'text', track: 'v4', start: 8, duration: 4},
            {id: 'b1', kind: 'video', track: 'v5', media: 'b1.mp4', start: 1, duration: 2, in: 0},
            {id: 'b2', kind: 'video', track: 'v5', media: 'b2.mp4', start: 3, duration: 2, in: 0},
            {id: 'music', kind: 'audio', track: 'a1', media: 'music.wav', start: 0, duration: 12, in: 0}]};
    F.magnet = true;
}
const starts = () => Object.fromEntries(S.doc.clips.map(c => [c.id, +c.start.toFixed(3)]));

async function main() {
    let passed = 0, sent = null;
    const join = (left) => F.gen.request({kind: 'between', prompt: 'a join', seconds: 2,
        start: {web: left.media, seconds: 3.9}, end: {web: F.gen.nextOf(left).media, seconds: 0}},
        {time: F.end(left), track: left.track, between: true});
    F.api = async (url, body) => { if (url === 'generate') { sent = body; return {id: 'job', placeholder: body.placeholder, state: 'waiting'}; } return {}; };

    fresh();
    assert.strictEqual(F.gen.nextOf(F.clip('s12')).id, 's13');
    await join(F.clip('s12'));
    const g = sent.placeholder.clip;
    assert.deepStrictEqual(starts(), {s12: 0, s13: 6, s14: 10, d13: 6.5, n14: 10, b1: 1, b2: 3, music: 0, [g]: 4});
    assert.strictEqual(F.clip(g).track, 'v1');
    assert.strictEqual(sent.placeholder.ripple, 2);
    console.log('ok a join sits between the two shots and the overlay, title stay on their shots; music spanning the cut stays'); passed++;

    // the result is a little shorter: everything after it comes back by the difference
    F.probe = async () => ({duration: 1.5});
    F.api = async (url) => url.startsWith('generations?') ? {generations: [{id: 'job', state: 'done', video: 'join.mp4',
        placeholder: sent.placeholder}]} : url === 'generate/result' ? {web: 'join.mp4'} : {};
    await F.gen.refresh();
    assert.strictEqual(F.clip(g).kind, 'video');
    assert.deepStrictEqual(starts(), {s12: 0, s13: 5.5, s14: 9.5, d13: 6, n14: 9.5, b1: 1, b2: 3, music: 0, [g]: 4});
    console.log('ok a shorter result keeps every track in step'); passed++;

    // stopping it puts everything where it was
    fresh();
    const before = starts();
    F.api = async (url, body) => { if (url === 'generate') { sent = body; return {id: 'job2', placeholder: body.placeholder, state: 'waiting'}; } return {}; };
    await join(F.clip('s12'));
    F.api = async () => ({});
    await F.gen.cancelClip(sent.placeholder.clip, 'job2');
    assert.deepStrictEqual(starts(), before);
    console.log('ok stopping a join moves every track back'); passed++;

    // a join on a second track stays on that track, between its two clips
    fresh();
    F.api = async (url, body) => { if (url === 'generate') { sent = body; return {id: 'job3', placeholder: body.placeholder, state: 'waiting'}; } return {}; };
    assert.strictEqual(F.gen.nextOf(F.clip('b1')).id, 'b2');
    await join(F.clip('b1'));
    const g3 = sent.placeholder.clip;
    assert.strictEqual(F.clip(g3).track, 'v5');
    assert.deepStrictEqual(starts(), {s12: 0, s13: 4, s14: 8, d13: 4.5, n14: 8, b1: 1, b2: 5, music: 0, [g3]: 3});  // the main track and the others stay
    F.api = async () => ({});
    await F.gen.cancelClip(g3, 'job3');
    assert.strictEqual(F.clip('b2').start, 3);
    console.log('ok a join on a second track goes between its clips there (not to another track) and stops cleanly'); passed++;

    // a new shot inserted at a cut of the main track moves the other tracks too; an exact one moves nothing
    fresh();
    F.api = async (url, body) => { if (url === 'generate') { sent = body; return {id: 'job4', placeholder: body.placeholder, state: 'waiting'}; } return {}; };
    await F.gen.request({kind: 'continue', prompt: 'next', seconds: 1, start: {web: 's13.mp4', seconds: 3.9}}, {time: 8, track: 'v1'});
    assert.strictEqual(F.clip('n14').start, 9);
    assert.strictEqual(F.clip('d13').start, 4.5);
    fresh();
    await F.gen.request({kind: 'continue', prompt: 'next', seconds: 1, start: {web: 's13.mp4', seconds: 3.9}}, {time: 12, track: 'v1', exact: true});
    assert.strictEqual(sent.placeholder.ripple, 0);
    assert.strictEqual(F.clip('n14').start, 8);
    console.log('ok inserts on the main track keep the other tracks in step; exact placements move nothing'); passed++;
    console.log(`${passed} join tests passed`);
}
main().catch((e) => { console.error(e); process.exit(1); });

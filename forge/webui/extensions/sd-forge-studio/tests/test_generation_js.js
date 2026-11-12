/* Generation failure paths, including pre-roll rollback and project switches, without a browser or GPU. */
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
function fresh(clips) {
    S.project = 'test'; S.selected = [];
    S.doc = {settings: {fps: 30, width: 320, height: 192}, tracks: [{id: 'v1', kind: 'video', main: true}, {id: 'a1', kind: 'audio'}], clips};
    F.magnet = true;
}
const initial = () => [{id: 'v', kind: 'video', track: 'v1', media: 'v.mp4', start: 0, duration: 2},
    {id: 'a', kind: 'audio', track: 'a1', media: 'a.wav', start: 1, duration: 2}];
async function main() {
    let passed = 0;
    for (const state of ['error', 'cancelled']) {
        fresh(initial());
        S.doc.clips.forEach(c => c.start++);
        S.doc.clips.push({id: 'g', kind: 'gen', track: 'v1', start: 0, duration: 1});
        const placed = [];
        F.api = async (url, body) => url.startsWith('generations?') ? {generations: [{id: 'job', state, placeholder: {clip: 'g', shift: 1, shifted: ['v', 'a']}}]} : placed.push(body);
        await F.gen.refresh();
        assert.deepStrictEqual(S.doc.clips.map(c => [c.id, c.start]), [['v', 0], ['a', 1]]);
        assert.strictEqual(placed[0].id, 'job');
        console.log('ok', state, 'pre-roll restores picture and audio together'); passed++;
    }
    fresh(initial()); F.clip('v').pendingTake = 'take';
    F.api = async url => url.startsWith('generations?') ? {generations: [{id: 'take', state: 'error', placeholder: {clip: 'v', take: true}}]} : {};
    await F.gen.refresh();
    assert.strictEqual(F.clip('v').pendingTake, undefined);
    assert.strictEqual(F.clip('v').media, 'v.mp4');
    console.log('ok failed take clears pending state and keeps the original'); passed++;
    fresh(initial());
    const before = JSON.stringify(S.doc);
    F.api = async () => { S.project = 'other'; return {generations: [{id: 'old', state: 'error', placeholder: {clip: 'v'}}]}; };
    await F.gen.refresh();
    assert.strictEqual(JSON.stringify(S.doc), before);
    console.log('ok a stale project response cannot change the current edit'); passed++;
    fresh(initial());
    F.api = async () => { throw Error('submission failed'); };
    await assert.rejects(() => F.gen.request({kind: 'continue', seconds: 1, prompt: 'Continue the shot'},
        {time: 0, track: 'v1', exact: true, shift: 1}), /submission failed/);
    assert.deepStrictEqual(S.doc.clips.map(c => [c.id, c.start]), [['v', 0], ['a', 1]]);
    console.log('ok a rejected pre-roll request restores all shifted clips'); passed++;
    const pair=()=>[{id:'v',kind:'video',track:'v1',media:'v.mp4',start:2,duration:2},
        {id:'extension',kind:'video',track:'v1',media:'e.mp4',start:4,duration:1},
        {id:'audio',kind:'audio',track:'a1',media:'a.wav',start:3,duration:1}];
    const gen={placeholder:{extension:{source:'v'}}};
    fresh(pair());let calls=0;
    F.gen.confirmMerge=async()=>false;F.api=async()=>{calls++;};
    await F.gen.offerMerge(gen,'extension');
    assert.equal(calls,0);assert.equal(S.doc.clips.length,3);
    console.log('ok declining combine keeps both clips without rendering');passed++;
    F.gen.confirmMerge=async()=>true;F.follow=async x=>x;F.probe=async()=>({});
    S.media={};F.api=async()=>{throw Error('render failed');};
    const beforeFailure=JSON.stringify(S.doc);
    await assert.rejects(()=>F.gen.offerMerge(gen,'extension'),/render failed/);
    assert.equal(JSON.stringify(S.doc),beforeFailure);
    console.log('ok a failed combine leaves the complete edit intact');passed++;
    F.api=async()=>({web:'combined.mp4',seconds:3});
    await F.gen.offerMerge(gen,'extension');
    assert.deepStrictEqual(S.doc.clips.map(c=>[c.id,c.start,c.duration]),[['audio',3,1],['v',2,3]]);
    F.undo();assert.equal(JSON.stringify(S.doc),beforeFailure);
    F.redo();assert.equal(F.clip('v').media,'combined.mp4');
    console.log('ok combine replaces only the two visual pieces; undo and redo preserve audio');passed++;
    fresh(pair());F.api=async()=>{F.clip('v').start=1;return {web:'stale.mp4',seconds:3};};
    await assert.rejects(()=>F.gen.offerMerge(gen,'extension'),/clips changed/);
    assert.equal(S.doc.clips.length,3);assert.equal(F.clip('extension').media,'e.mp4');
    console.log('ok a stale combined response cannot replace edited clips');passed++;
    fresh(pair());
    F.clip('v').muted=true;F.clip('v').detachedAudio='linked';
    S.doc.clips.push({id:'linked',kind:'audio',track:'a1',media:'v.mp4',from:'v',linkedTo:'v',start:2,duration:2});
    const linkedBefore=JSON.stringify(S.doc);let sent=null;
    F.api=async(url,body)=>{sent=body;return {web:'linked-combined.mp4',seconds:3};};
    await F.gen.offerMerge(gen,'extension');
    assert.equal(sent.audio.length,1);assert.equal(sent.audio[0].id,'linked');
    assert.equal(F.clip('linked'),undefined);assert.ok(F.clip('audio'));
    assert.ok(!F.clip('v').muted);F.undo();assert.equal(JSON.stringify(S.doc),linkedBefore);
    console.log('ok combine bakes linked audio and keeps independent sound; Undo restores the linked pair');passed++;
    console.log(`${passed} passed`);
}
main().catch(error => { console.error(error); process.exitCode = 1; });

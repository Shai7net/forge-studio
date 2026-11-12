'use strict';
const assert=require('assert'),fs=require('fs'),path=require('path'),vm=require('vm');
const ctx={window:{ForgeStudio:{T:x=>x}},console};vm.createContext(ctx);
vm.runInContext(fs.readFileSync(path.join(__dirname,'../javascript/forge-studio-l-edit.js'),'utf8'),ctx);
const M=ctx.window.ForgeStudio.SpeechEdit, plain=x=>JSON.parse(JSON.stringify(x));let passed=0;
function test(name,fn){fn();passed++;console.log('ok '+name);}
function fresh(){const m=new M(8,30);m.setTake(4,1,4);return m;}
test('splitting preserves exact words and placement; local undo and redo restore both pieces',()=>{const m=fresh(),before=plain(m.parts);m.time=2;m.split();assert.deepStrictEqual(plain(m.segments()),[{start:1,in:0,duration:1},{start:2,in:1,duration:3}]);m.undo();assert.deepStrictEqual(plain(m.parts),before);m.redo();assert.equal(m.parts.length,2);});
test('dragging and trimming respect source, shot and neighboring boundaries',()=>{const m=fresh(),id=m.selected,b=m.snapshot();m.drag(id,null,99,b);assert.equal(m.get().start,4);m.drag(id,'l',-99,b);assert.equal(m.get().start,1);m.drag(id,'r',99,b);assert.equal(m.get().duration,4);m.drag(id,'l',1,b);assert.deepStrictEqual(plain(m.segments()),[{start:2,in:1,duration:3}]);});
test('deleting a marked range leaves a real silent gap and correct source trims',()=>{const m=fresh();m.range=[2,3];m.remove();assert.deepStrictEqual(plain(m.segments()),[{start:1,in:0,duration:1},{start:3,in:2,duration:2}]);m.undo();assert.equal(m.parts.length,1);});
test('silence removal closes pauses with breathing room and is undoable',()=>{const m=fresh();m.silence([[.2,1],[2,3]]);const a=m.segments();assert.equal(a.length,2);assert.ok(Math.abs(a[0].in-.16)<1e-8);assert.ok(Math.abs(a[1].start-(1+a[0].duration))<1e-8);assert.ok(a.at(-1).start+a.at(-1).duration<4);m.undo();assert.equal(m.parts[0].duration,4);});
test('moving one piece cannot overwrite a neighboring piece',()=>{const m=fresh();m.time=3;m.split();const id=m.selected,b=m.snapshot();m.drag(id,null,-2,b);assert.equal(m.get(id).start,3);});
test('faster tuning maps cuts to the same words; slower tuning rejects hidden truncation',()=>{const m=fresh();m.time=3;m.split();m.retime(.5,4);assert.deepStrictEqual(plain(m.segments()),[{start:1,in:0,duration:1},{start:3,in:1,duration:1}]);const before=plain(m.segments());assert.throws(()=>m.retime(4,8),/no longer fits/);assert.deepStrictEqual(plain(m.segments()),before);});
test('deleting the last piece can be undone without regenerating the recording',()=>{const m=fresh();m.remove();assert.equal(m.parts.length,0);m.undo();assert.equal(m.parts.length,1);});
test('pitch-only retuning retains the local undo history',()=>{const m=fresh();m.time=2;m.split();m.retime(1,4);m.undo();assert.equal(m.parts.length,1);assert.equal(m.parts[0].duration,4);});
console.log(`${passed} passed`);

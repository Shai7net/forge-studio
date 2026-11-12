'use strict';
const assert=require('assert'),fs=require('fs'),path=require('path'),vm=require('vm');
const F={on(){},T:x=>x},ctx={window:{ForgeStudio:F},document:{addEventListener(){}},localStorage:{getItem:()=>null},setInterval:()=>0,clearInterval(){},console};
vm.createContext(ctx);vm.runInContext(fs.readFileSync(path.join(__dirname,'../javascript/forge-studio-m-progress.js'),'utf8'),ctx);
let passed=0;function test(name,fn){fn();passed++;console.log('ok '+name);}
const task=(id,p,state='running')=>({id,kind:'video',state,progress:p});
test('ETA comes from measured progress across several samples',()=>{const m=new F.GenerationProgress();m.update(task('a',0),1000);m.update(task('a',.2),3000);const t=m.update(task('a',.4),5000);assert.ok(Math.abs(m.eta(t,5000)-6)<1e-8);assert.ok(Math.abs(m.eta(t,6000)-5)<1e-8);});
test('warmup, queued work and stalled progress show no invented countdown',()=>{const m=new F.GenerationProgress();let t=m.update(task('a',0),1000);assert.equal(m.eta(t,9000),null);m.update(task('a',.2),3000);t=m.update(task('a',.4),5000);assert.equal(m.eta(t,21000),null);t=m.update(task('b',null,'waiting'),22000);assert.equal(m.eta(t,23000),null);});
test('a phase reset discards the old estimate',()=>{const m=new F.GenerationProgress();m.update(task('a',0),1000);m.update(task('a',.3),4000);m.update(task('a',.6),8000);const t=m.update(task('a',.1),9000);assert.equal(m.eta(t,10000),null);});
test('completion is notified once and failures never croak',()=>{const m=new F.GenerationProgress();m.update(task('a',.5),1);let t=m.update(task('a',1,'done'),2);assert.equal(t.complete,true);t=m.update(task('a',1,'done'),3);assert.equal(t.complete,false);m.update(task('b',.5),4);t=m.update(task('b',.5,'error'),5);assert.equal(t.complete,false);});
test('old finished tasks and instant cached operations do not create spurious notifications',()=>{const m=new F.GenerationProgress();assert.equal(m.update(task('old',1,'done'),1000),null);assert.equal(m.current(1000),null);});
test('audio tuning and waveform edits do not trigger generation sounds',()=>{const m=new F.GenerationProgress();assert.equal(m.update({id:'a',kind:'tune-speech',state:'running',progress:0}),null);assert.equal(m.update({id:'b',kind:'edit-speech',state:'done',progress:1}),null);});
test('running tasks appear before queued tasks, and a finished card clears',()=>{const m=new F.GenerationProgress();m.update(task('queued',null,'waiting'),100);m.update(task('active',.1),200);assert.equal(m.current(300).id,'active');m.update(task('active',1,'done'),1000);assert.equal(m.current(2000).id,'queued');m.update(task('queued',1,'done'),2001);assert.equal(m.current(5000),null);});
console.log(`${passed} passed`);

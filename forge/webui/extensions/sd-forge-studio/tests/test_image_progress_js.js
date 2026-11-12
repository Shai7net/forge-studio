'use strict';
const assert=require('assert'),fs=require('fs'),path=require('path'),vm=require('vm');
let callbacks,reports=[],images=[],timers=[],now=0,passed=0;
const gallery={id:'txt2img_gallery',querySelectorAll:()=>images};
const container={parentNode:{querySelector:()=>({getBoundingClientRect:()=>({height:10})})}};
let nativeCalls=0;
const context={window:{opts:{show_progressbar:true},forgeGenerationFeedback:t=>reports.push(t),requestProgress:(...args)=>{nativeCalls++;callbacks=args;}},
    document:{body:{classList:{contains:c=>c==='fibo2-active'}}},onUiLoaded:f=>f(),getComputedStyle:()=>({display:'block'}),
    Date:{now:()=>now},setTimeout:f=>timers.push(f)};
vm.createContext(context);vm.runInContext(fs.readFileSync(path.join(__dirname,'../javascript/forge-studio-n-image-progress.js'),'utf8'),context);
const flush=()=>{while(timers.length)timers.shift()();};
function test(name,fn){reports=[];images=[];timers=[];now=0;fn();passed++;console.log('ok '+name);}
test('FIBO reports actual progress and only a new result proves completion',()=>{
    let ended=0;context.window.requestProgress('one',container,gallery,()=>ended++);
    assert.equal(reports[0].state,'waiting');callbacks[4]({active:true,progress:.25});assert.equal(reports.at(-1).progress,.25);
    callbacks[3]();assert.equal(ended,1);images=[{src:'new.png',closest:()=>null}];flush();assert.equal(reports.at(-1).state,'done');
});
test('an old image or live preview does not trigger a success croak',()=>{
    images=[{src:'old.png',closest:()=>null}];context.window.requestProgress('two',container,gallery,()=>{});
    images.push({src:'preview.png',closest:()=>({})});callbacks[3]();flush();assert.equal(reports.at(-1).state,'cancelled');
});
test('a working native bar takes over while missing progress gets a fallback',()=>{
    context.document.body.classList.contains=()=>false;
    context.window.requestProgress('three',container,gallery,()=>{});callbacks[4]({active:true,progress:.2});assert.equal(reports.length,0);
    now=3000;callbacks[4]({active:true,progress:0});assert.equal(reports.at(-1).hidden,false);
    callbacks[4]({active:true,progress:.5});assert.equal(reports.at(-1).hidden,true);
});
test('unrelated progress requests keep their original callback and arguments',()=>{
    const end=()=>{},cb=()=>{};context.window.requestProgress('other',container,{id:'extras'},end,cb,70);
    assert.equal(callbacks[3],end);assert.equal(callbacks[4],cb);assert.equal(callbacks[5],70);
});
console.log(`${passed} passed`);

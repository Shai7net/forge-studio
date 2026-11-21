/* Exercise the actual timeline pointer handler: Ctrl is an explicit AI gesture, never an ordinary trim. */
'use strict';
const assert=require('assert'),fs=require('fs'),path=require('path'),vm=require('vm');
const events=new Map(),ghosts=new Set();
const ctx={window:{addEventListener:(k,f)=>events.set(k,f),removeEventListener:k=>events.delete(k)},console,setTimeout,clearTimeout,
    document:{documentElement:{classList:{contains:()=>false}}}};
vm.createContext(ctx);vm.runInContext(fs.readFileSync(path.join(__dirname,'../javascript/forge-studio-a-core.js'),'utf8'),ctx);
const F=ctx.window.ForgeStudio,S=F.state,code=fs.readFileSync(path.join(__dirname,'../javascript/forge-studio-d-timeline.js'),'utf8');
const start=code.indexOf('    function clipPointer('),end=code.indexOf('\n    /*',start+10);
const handler=code.slice(start,end).trim();
let requests=[];F.guard=fn=>fn;F.save=async()=>{};F.gen={extendClip:(c,edge,seconds)=>requests.push({c,edge,seconds})};F.magnet=false;
const cl={remove(){},toggle(){}},TL={pps:20,lanes:[{track:{id:'v'},top:0,height:60,el:{classList:cl}}],content:{append(e){ghosts.add(e);}},dropLane:null};
const h=()=>{const el={style:{},textContent:'',remove(){ghosts.delete(el);}};return el;};
const pointer=new Function('FS','S','TL','h','T','val','laneAt','snapped','window','return ('+handler+');')(F,S,TL,h,x=>x,F.val,()=>TL.lanes[0],t=>t,ctx.window);
function fresh(){events.clear();ghosts.clear();requests=[];S.selected=[];S.info={'v.mp4':{duration:2}};S.playing=false;S.doc={settings:{fps:30},tracks:[{id:'v',kind:'video',main:true}],clips:[{id:'one',kind:'video',track:'v',media:'v.mp4',start:2,duration:2}]};}
function down(ctrlKey,edge='r'){pointer({button:0,ctrlKey,clientX:100,clientY:20,target:{classList:{contains:k=>k==='ft-'+edge}},stopPropagation(){}},F.clip('one'));}
function move(x,ctrlKey){events.get('pointermove')({clientX:x,clientY:20,ctrlKey});}
let passed=0;
fresh();down(false);move(200,false);events.get('pointerup')({ctrlKey:false});assert.equal(requests.length,0);assert.equal(F.clip('one').duration,2);passed++;
fresh();const original=JSON.stringify(S.doc);down(true);move(140,true);events.get('pointerup')({ctrlKey:true});assert.equal(requests.length,1);assert.equal(requests[0].seconds,2);assert.equal(JSON.stringify(S.doc),original);assert.equal(ghosts.size,0);passed++;
fresh();down(true);move(60,true);events.get('pointerup')({ctrlKey:true});assert.equal(requests.length,0);passed++;
fresh();down(true,'l');move(80,true);events.get('pointerup')({ctrlKey:true});assert.equal(requests[0].edge,'l');assert.equal(requests[0].seconds,1);assert.equal(F.clip('one').start,2);passed++;
fresh();down(true);move(140,true);events.get('pointerup')({ctrlKey:false});assert.equal(requests.length,0);assert.equal(ghosts.size,0);passed++;
fresh();down(true);move(140,true);events.get('keydown')({key:'Escape'});assert.equal(requests.length,0);assert.equal(ghosts.size,0);assert.equal(F.clip('one').duration,2);passed++;
fresh();down(true,'body');assert.equal(events.size,0);assert.deepEqual(S.selected,['one']);passed++;
console.log(passed+' Ctrl-drag and cancellation checks passed');

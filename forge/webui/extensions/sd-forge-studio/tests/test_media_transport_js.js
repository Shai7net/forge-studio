'use strict';
const assert=require('assert'),fs=require('fs'),path=require('path'),vm=require('vm');
const ctx={window:{},document:{documentElement:{classList:{contains:()=>false}}},console,setTimeout,clearTimeout};
vm.createContext(ctx);vm.runInContext(fs.readFileSync(path.join(__dirname,'../javascript/forge-studio-a-core.js'),'utf8'),ctx);
const Transport=ctx.window.ForgeStudio.MediaTransport;
function media(){
    const events=new Map();
    return {readyState:4,duration:10,paused:true,seeking:false,_time:0,seeks:[],requests:[],loads:0,
        addEventListener(n,f){if(!events.has(n))events.set(n,new Set());events.get(n).add(f);},
        removeEventListener(n,f){events.get(n)?.delete(f);},emit(n){for(const f of events.get(n)||[])f();},
        get listenerCount(){return [...events.values()].reduce((n,s)=>n+s.size,0);},
        get currentTime(){return this._time;},set currentTime(t){this._time=t;this.seeking=true;this.seeks.push(t);},
        finish(){this.seeking=false;this.emit('seeked');},
        play(){this.paused=false;return new Promise((resolve,reject)=>this.requests.push({resolve:()=>{this.paused=false;resolve();},reject:error=>{this.paused=true;reject(error);}}));},
        pause(){this.paused=true;},load(){this.loads++;},removeAttribute(){this.src='';}};
}
const settle=async()=>{for(let i=0;i<8;i++)await Promise.resolve();};
(async()=>{
    let passed=0;
    {const el=media(),t=new Transport(el);t.seek(1);for(let i=0;i<100;i++)t.seek(2+i/100);assert.equal(el.seeks.length,1);el.finish();assert.equal(el.seeks.length,2);assert.equal(el.currentTime,2.99);el.finish();el._time=3.5;el.emit('loadeddata');assert.equal(el.seeks.length,2);t.dispose();passed++;}
    {const el=media(),t=new Transport(el);const a=t.play(),b=t.play();assert.strictEqual(a,b);assert.equal(el.requests.length,1);t.pause();el.requests[0].resolve();await settle();assert(el.paused);assert.equal(await a,false);t.dispose();passed++;}
    {const el=media(),t=new Transport(el);t.play();t.dispose();t.dispose();el.requests[0].resolve();await settle();assert(el.paused);assert.equal(el.listenerCount,0);assert.equal(el.src,'');assert.equal(el.loads,1);passed++;}
    {const el=media(),t=new Transport(el);t.source('old');t.play();t.source('new');const p=t.play();el.requests[0].resolve();await settle();assert.strictEqual(t.pending,p);assert(!el.paused);el.requests[1].resolve();await settle();assert(t.running);t.source('new');assert.equal(el.loads,2);t.dispose();passed++;}
    {const el=media(),t=new Transport(el);el.readyState=0;t.seek(4);t.play();assert.equal(el.requests.length,0);el.readyState=4;el.emit('loadedmetadata');assert.equal(el.currentTime,4);assert.equal(el.requests.length,0);el.finish();assert.equal(el.requests.length,1);el.requests[0].resolve();await settle();assert(t.running);t.dispose();passed++;}
    {const el=media(),t=new Transport(el);t.seek(NaN);assert.equal(el.seeks.length,0);t.seek(100);assert.equal(el.currentTime,9.999);el.finish();t.seek(-1);assert.equal(el.currentTime,0);t.dispose();passed++;}
    {const el=media(),t=new Transport(el);t.play();el.requests[0].reject(Error('blocked'));await settle();assert(t.error);t.play();el.requests[1].resolve();await settle();assert.equal(t.error,null);assert(t.running);t.dispose();passed++;}
    console.log(passed+' shared media transport checks passed');
})().catch(error=>{console.error(error);process.exitCode=1;});

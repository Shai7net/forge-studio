/* A small frog keeps model generation visible. Percentages are real; ETA waits for measured progress. */
(function(){
    'use strict';
    const F=window.ForgeStudio,T=(...a)=>F.T(...a);
    const major=new Set(['image','video','lipsync','lip-speech','voice','music','effects','draw','carry','captions','combine-extension']);
    F.GenerationProgress=class {
        constructor(){this.tasks=new Map();}
        update(task,now=Date.now()){
            if(!task?.id||!major.has(task.kind))return null;
            let t=this.tasks.get(task.id);
            const active=task.state==='running'||task.state==='waiting';
            if(!t&&!active)return null;
            if(!t){t={id:task.id,kind:task.kind,samples:[],seen:now,notified:false};this.tasks.set(t.id,t);}
            const previous=t.state;t.state=task.state;t.updated=now;
            for(const key of ['scope','gallery','hidden'])if(key in task)t[key]=task[key];
            const p=Number.isFinite(task.progress)?Math.max(0,Math.min(1,task.progress)):null;
            if(p!=null&&active){
                if(t.progress!=null&&p<t.progress-.02)t.samples=[];
                if(!t.samples.length||p>t.samples.at(-1).p+1e-6){t.samples.push({p,time:now});t.lastProgress=now;t.samples=t.samples.filter(s=>now-s.time<120000).slice(-12);}
                t.progress=p;
            }
            if(task.state==='done')t.progress=1;
            t.complete=task.state==='done'&&previous!=='done'&&!t.notified;
            if(t.complete)t.notified=true;
            if(!active)t.ended=now;
            return t;
        }
        eta(t,now=Date.now()){
            if(t.state!=='running'||t.samples.length<3||now-t.lastProgress>15000)return null;
            const a=t.samples[0],b=t.samples.at(-1),dt=(b.time-a.time)/1000,dp=b.p-a.p;
            if(dt<4||dp<.025||t.progress>=1)return null;
            return Math.max(1,Math.min(86400,(1-b.p)*dt/dp-(now-b.time)/1000));
        }
        current(now=Date.now()){
            for(const [id,t] of this.tasks)if(t.ended&&now-t.ended>120000)this.tasks.delete(id);
            const all=[...this.tasks.values()].filter(t=>!t.hidden);
            const active=all.filter(t=>t.state==='running'||t.state==='waiting').sort((a,b)=>(a.state==='running'?0:1)-(b.state==='running'?0:1)||b.seen-a.seen);
            return active[0]||all.filter(t=>t.ended&&now-t.ended<2400).sort((a,b)=>b.ended-a.ended)[0]||null;
        }
    };
    const model=new F.GenerationProgress();
    let panel=null,ctx=null,timer=null,played=0,sound=true;
    try{sound=localStorage.getItem('forgeStudio.frogSound')!=='off';}catch{}
    function unlock(){if(!sound)return;try{const Audio=window.AudioContext||window.webkitAudioContext;if(!Audio)return;ctx=ctx||new Audio();if(ctx.state==='suspended')ctx.resume().catch(()=>{});}catch{}}
    function croak(){
        if(!sound||!ctx||ctx.state!=='running')return false;
        const now=ctx.currentTime;
        for(const delay of [0,.15]){
            const osc=ctx.createOscillator(),filter=ctx.createBiquadFilter(),gain=ctx.createGain(),lfo=ctx.createOscillator(),depth=ctx.createGain();
            osc.type='sawtooth';osc.frequency.setValueAtTime(150,now+delay);osc.frequency.exponentialRampToValueAtTime(68,now+delay+.13);
            filter.type='bandpass';filter.frequency.setValueAtTime(650,now+delay);filter.frequency.exponentialRampToValueAtTime(330,now+delay+.13);filter.Q.value=3;
            gain.gain.setValueAtTime(0,now+delay);gain.gain.linearRampToValueAtTime(.12,now+delay+.012);gain.gain.exponentialRampToValueAtTime(.0001,now+delay+.14);
            lfo.frequency.value=32;depth.gain.value=13;lfo.connect(depth);depth.connect(osc.frequency);
            osc.connect(filter);filter.connect(gain);gain.connect(ctx.destination);osc.start(now+delay);lfo.start(now+delay);osc.stop(now+delay+.15);lfo.stop(now+delay+.15);
            osc.onended=()=>{osc.disconnect();filter.disconnect();gain.disconnect();lfo.disconnect();depth.disconnect();};
        }
        played++;return true;
    }
    document.addEventListener('pointerdown',unlock,{capture:true,passive:true});document.addEventListener('keydown',unlock,{capture:true,passive:true});
    const labels={image:'Making image',video:'Making video',lipsync:'Lip sync','lip-speech':'Creating speech',voice:'Creating speech',music:'Making music',effects:'Making sound effects',draw:'Drawing',carry:'Editing video',captions:'Creating captions','combine-extension':'Combining clips'};
    function mount(){
        if(!document.body)return false;
        if(panel?.isConnected)return true;
        const h=F.h;
        const art=h('div',{class:'fx-frog-art','aria-hidden':'true'});
        art.innerHTML='<svg viewBox="0 0 96 80" xmlns="http://www.w3.org/2000/svg"><ellipse cx="48" cy="69" rx="31" ry="5" fill="#071c12" opacity=".4"/><path d="M23 55Q6 64 19 69M73 55Q90 64 77 69" fill="none" stroke="#3fdc82" stroke-width="9" stroke-linecap="round"/><ellipse cx="48" cy="46" rx="34" ry="25" fill="#42df86"/><circle cx="29" cy="24" r="15" fill="#42df86"/><circle cx="67" cy="24" r="15" fill="#42df86"/><circle cx="29" cy="24" r="10" fill="#effff3"/><circle cx="67" cy="24" r="10" fill="#effff3"/><circle cx="31" cy="25" r="5" fill="#102c21"/><circle cx="65" cy="25" r="5" fill="#102c21"/><circle cx="33" cy="23" r="1.8" fill="white"/><circle cx="67" cy="23" r="1.8" fill="white"/><path d="M33 49Q48 61 63 49" fill="none" stroke="#0b6440" stroke-width="3" stroke-linecap="round"/><ellipse cx="25" cy="46" rx="5" ry="3" fill="#a1ef98"/><ellipse cx="71" cy="46" rx="5" ry="3" fill="#a1ef98"/></svg>';
        const ring=h('div',{class:'fx-frog-ring'},art),headline=h('div',{class:'fx-frog-title',role:'status','aria-live':'polite'}),percent=h('div',{class:'fx-frog-percent','aria-live':'off'}),eta=h('div',{class:'fx-frog-eta','aria-live':'off'});
        const button=h('button',{class:'fx-frog-sound',onclick:()=>{sound=!sound;try{localStorage.setItem('forgeStudio.frogSound',sound?'on':'off');}catch{}if(sound)unlock();render();}},'🔊');
        panel=h('div',{class:'fx-frog-progress hebrew-ui-skip',hidden:true},ring,h('div',{class:'fx-frog-copy'},headline,percent,eta),button);
        panel.parts={headline,percent,eta,button,ring};document.body.append(panel);return true;
    }
    function time(seconds){const n=Math.ceil(seconds);return n<60?T('About {0}s remaining',n):T('About {0}m {1}s remaining',Math.floor(n/60),n%60);}
    function render(){
        if(!mount())return;
        const t=model.current(),parts=panel.parts;
        if(!t){panel.hidden=true;clearInterval(timer);timer=null;return;}
        const global=t.scope==='page',host=global?document.body:F.UI?.root;
        if(!host){panel.hidden=true;return;}
        if(panel.parentNode!==host)host.append(panel);
        panel.classList.toggle('fx-frog-global',global);
        panel.hidden=false;panel.classList.toggle('done',t.state==='done');panel.classList.toggle('failed',t.state==='error'||t.state==='cancelled');
        const title=t.state==='done'?T('Ready!'):t.state==='error'?T('Generation failed'):t.state==='cancelled'?T('Generation stopped'):T(labels[t.kind]||'Generating…');
        if(parts.headline.textContent!==title)parts.headline.textContent=title;
        parts.percent.textContent=t.progress==null?'…':Math.round(t.progress*100)+'%';
        const remaining=model.eta(t);
        parts.eta.textContent=t.state==='waiting'?T('Waiting for the graphics card'):t.state==='done'?T('Your result is ready'):t.state==='running'?(remaining==null?T('Estimating remaining time…'):time(remaining)):'';
        parts.ring.style.setProperty('--frog-progress',(t.progress==null?15:t.progress*100)+'%');
        parts.ring.classList.toggle('indeterminate',t.progress==null||t.state==='waiting');
        parts.button.textContent=sound?'🔊':'🔇';parts.button.title=sound?T('Completion sound on'):T('Completion sound off');parts.button.setAttribute('aria-label',parts.button.title);
    }
    function report(task){const t=model.update(task);if(!t)return;if(t.complete&&!t.hidden)croak();render();if(!timer)timer=setInterval(render,500);}
    F.on('task-progress',report);
    window.forgeGenerationFeedback=report;
    F.frogProgress={model,render,get played(){return played;},get soundEnabled(){return sound;},get audioState(){return ctx?.state;}};
})();

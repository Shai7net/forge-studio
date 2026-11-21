/* Local, non-destructive speech editing. All times are seconds within one shot. */
(function(){
    'use strict';
    const F=window.ForgeStudio, copy=x=>JSON.parse(JSON.stringify(x)), clamp=(v,a,b)=>Math.max(a,Math.min(b,v));
    const M=F.SpeechEdit=class {
        constructor(seconds,fps=30){this.seconds=seconds;this.fps=fps;this.parts=[];this.selected=null;this.range=null;this.time=0;this.past=[];this.future=[];this.sourceSeconds=0;this.serial=0;}
        id(){return 's'+(++this.serial);}
        snap(t){return Math.round(t*this.fps)/this.fps;}
        snapshot(){return copy({parts:this.parts,selected:this.selected,range:this.range});}
        restore(s){Object.assign(this,copy(s));}
        commit(before){if(JSON.stringify(before)!==JSON.stringify(this.snapshot())){this.past.push(before);if(this.past.length>80)this.past.shift();this.future=[];return true;}return false;}
        change(fn){const b=this.snapshot();fn();return this.commit(b);}
        undo(){if(!this.past.length)return false;this.future.push(this.snapshot());this.restore(this.past.pop());return true;}
        redo(){if(!this.future.length)return false;this.past.push(this.snapshot());this.restore(this.future.pop());return true;}
        setTake(duration,offset,length,parts){this.sourceSeconds=duration;this.parts=parts?copy(parts):[{id:this.id(),start:offset,in:0,duration:Math.min(length,duration,this.seconds-offset)}];this.selected=this.parts[0]?.id;this.range=null;this.past=[];this.future=[];}
        get(id=this.selected){return this.parts.find(p=>p.id===id);}
        split(){return this.change(()=>{const p=this.parts.find(p=>this.time>p.start+1/this.fps/2&&this.time<p.start+p.duration-1/this.fps/2);if(!p)return;const n=this.time-p.start,right={id:this.id(),start:this.time,in:p.in+n,duration:p.duration-n};p.duration=n;this.parts.push(right);this.selected=right.id;this.range=null;});}
        remove(){return this.change(()=>{if(this.range&&this.range[1]-this.range[0]>=1/this.fps/2){const [a,b]=this.range,out=[];for(const p of this.parts){const end=p.start+p.duration;if(end<=a||p.start>=b){out.push(p);continue;}if(p.start<a)out.push({...p,duration:a-p.start});if(end>b)out.push({...p,id:this.id(),start:b,in:p.in+b-p.start,duration:end-b});}this.parts=out;this.range=null;this.selected=null;}else{this.parts=this.parts.filter(p=>p.id!==this.selected);this.selected=null;}});}
        drag(id,edge,delta,before){this.restore(before);const p=this.get(id);if(!p)return;const others=this.parts.filter(x=>x.id!==id), left=Math.max(0,...others.filter(x=>x.start+x.duration<=p.start+1e-6).map(x=>x.start+x.duration)),right=Math.min(this.seconds,...others.filter(x=>x.start>=p.start+p.duration-1e-6).map(x=>x.start));const min=1/this.fps;
            if(edge==='l'){const t=clamp(this.snap(p.start+delta),Math.max(left,p.start-p.in),p.start+p.duration-min),d=t-p.start;p.start=t;p.in+=d;p.duration-=d;}
            else if(edge==='r'){p.duration=clamp(this.snap(p.start+p.duration+delta)-p.start,min,Math.min(right-p.start,this.sourceSeconds-p.in));}
            else p.start=clamp(this.snap(p.start+delta),left,right-p.duration);
            this.selected=id;this.range=null;
        }
        silence(ranges){return this.change(()=>{const out=[];for(const p of this.parts){if(this.selected&&p.id!==this.selected){out.push(p);continue;}let at=p.start;const regions=[];for(const [a,b] of ranges){const l=Math.max(p.in,a-.04),r=Math.min(p.in+p.duration,b+.06);if(r<=l)continue;if(regions.length&&l<=regions.at(-1)[1])regions.at(-1)[1]=Math.max(r,regions.at(-1)[1]);else regions.push([l,r]);}for(const [a,b] of regions){out.push({id:this.id(),start:at,in:a,duration:b-a});at+=b-a;}}this.parts=out;this.selected=null;this.range=null;});}
        retime(ratio,duration){const parts=this.parts.map(p=>({...p,in:p.in*ratio,duration:p.duration*ratio})).sort((a,b)=>a.start-b.start);
            if(parts.some((p,i)=>p.in+p.duration>duration+.001||p.start+p.duration>this.seconds+.001||(i&&parts[i-1].start+parts[i-1].duration>p.start+.001)))throw Error(F.T('Speech at this speed no longer fits the edited positions. Move the pieces or use a faster speed.'));
            const remap=s=>({...copy(s),parts:s.parts.map(p=>({...p,in:p.in*ratio,duration:p.duration*ratio}))});
            const valid=s=>s.parts.every(p=>p.in+p.duration<=duration+.001&&p.start+p.duration<=this.seconds+.001)&&s.parts.every(p=>s.parts.every(q=>p===q||p.start+p.duration<=q.start+.001||q.start+q.duration<=p.start+.001));
            this.sourceSeconds=duration;this.parts=parts;this.past=this.past.map(remap).filter(valid);this.future=this.future.map(remap).filter(valid);}
        segments(){return this.parts.map(({start,in:cin,duration})=>({start,in:cin,duration})).sort((a,b)=>a.start-b.start);}
    };

    F.createSpeechEditor=function({project,clip,source,onchange,onstatus,onplay}){
        const h=F.h,T=(...a)=>F.T(...a),q=encodeURIComponent,m=new M(clip.duration,F.fps());
        const url=(web,kind='project/media')=>`/forge-studio/${kind}?name=${q(project)}&web=${q(web)}`;
        let closed=false,locked=false,readOnly=false,take=null,compiled=null,playing=false,frame=0,result=null,showResult=false,zoom=1,drag=null;
        let paintFrame=0,seekRequested=false,layoutDirty=false,contentWidth=0,rulerKey='',lastTick=-Infinity,lastAlign=-Infinity,clock=null,starting=false;
        const pieces=new Map();
        const video=h('video',{class:'fx-lip-picture',muted:true,playsinline:'',preload:'auto'}),image=h('img',{class:'fx-lip-picture',hidden:source.kind!=='image',alt:T('Shot preview')});
        video.muted=true;
        const audio=h('audio',{preload:'auto',hidden:true}), stage=h('div',{class:'fx-lip-stage'},video,image);
        const picture=new F.MediaTransport(video),speech=new F.MediaTransport(audio);
        const original=h('button',{class:'fx-btn on',onclick:()=>setResultView(false)},T('Original')),
            rendered=h('button',{class:'fx-btn',disabled:true,onclick:()=>setResultView(true)},T('Lip preview'));
        const view=h('div',{class:'fx-lip-view'},h('div',{class:'fx-lip-view-switch'},original,rendered),stage,audio);
        const play=h('button',{id:'fx-lip-play',class:'fx-btn',title:T('Play / pause'),onclick:F.guard(()=>playing?pause():start())},'▶');
        const time=h('span',{class:'fx-lip-clock'},'0.00 / '+m.seconds.toFixed(2));
        const undo=h('button',{class:'fx-btn',id:'fx-speech-undo',onclick:()=>action(()=>m.undo())},'↶'),redo=h('button',{class:'fx-btn',id:'fx-speech-redo',onclick:()=>action(()=>m.redo())},'↷');undo.title=T('Undo speech edit');redo.title=T('Redo speech edit');
        const split=h('button',{id:'fx-speech-split',class:'fx-btn',onclick:()=>action(()=>m.split())},T('Split')),
            remove=h('button',{id:'fx-speech-delete',class:'fx-btn',onclick:()=>action(()=>m.remove())},T('Delete')),
            silence=h('button',{id:'fx-speech-silence',class:'fx-btn',onclick:F.guard(async()=>{if(!take)return;const key=take.web,before=JSON.stringify(m.segments());silence.disabled=true;try{const ranges=await F.api(`talk?name=${q(project)}&web=${q(key)}`);if(closed||take.web!==key||before!==JSON.stringify(m.segments()))return;action(()=>m.silence(ranges));if(before===JSON.stringify(m.segments()))onstatus(T('No removable silence in this selection'));}finally{draw();}})},T('Remove silence'));
        const z=h('input',{class:'fx-lip-zoom',type:'range',min:1,max:8,step:.25,value:1,'aria-label':T('Timeline zoom')});
        z.oninput=()=>{zoom=+z.value;draw();};
        const transport=h('div',{class:'fx-lip-transport'},play,time,undo,redo,split,remove,silence,h('span',{class:'fx-spacer'}),h('span',{class:'fx-dim'},T('Zoom')),z);
        const ruler=h('div',{class:'fx-speech-ruler'}),film=h('div',{class:'fx-speech-film','aria-label':T('Video track')}),lane=h('div',{class:'fx-speech-lane','aria-label':T('Speech track')}),head=h('div',{class:'fx-speech-head'}),band=h('div',{class:'fx-speech-band',hidden:true});
        film.style.backgroundImage=`url("${url(source.media,'strip')}")`;
        const content=h('div',{class:'fx-speech-content'},ruler,film,lane,band,head),scroll=h('div',{class:'fx-speech-scroll'},content);
        const help=h('div',{class:'fx-lip-edit-help'},T('Drag to move; edges trim. Shift-drag marks a range. S splits; Delete removes.'));
        const node=h('div',{class:'fx-speech-editor',tabindex:0},transport,scroll,help);
        const separateSpeech=()=>!!compiled&&!showResult;
        const hasPicture=()=>source.kind!=='image'||showResult;
        function syncPicture(t,run,exact=false){
            if(source.kind==='image'&&!showResult)return;
            const speed=showResult?1:F.val(source,'speed'),target=showResult?t:F.val(source,'in')+t*speed;
            const end=Number.isFinite(video.duration)?Math.max(0,video.duration-.001):Infinity,wanted=Math.min(target,end);
            video.playbackRate=clamp(speed,.1,8);
            const drift=wanted-video.currentTime,now=performance.now();
            if(!run||exact){picture.seek(wanted);lastAlign=now;}
            else if(separateSpeech()&&!video.seeking){
                if(Math.abs(drift)>.25&&now-lastAlign>500){picture.seek(wanted);lastAlign=now;}
                else if(Math.abs(drift)>.025)video.playbackRate=clamp(speed*clamp(1+drift*.5,.95,1.05),.1,8);
            }
            if(run&&target<end)picture.play();else picture.pause();
        }
        function setResultView(value){pause();showResult=!!(value&&result);original.classList.toggle('on',!showResult);rendered.classList.toggle('on',showResult);image.hidden=showResult||source.kind!=='image';video.hidden=!image.hidden;video.muted=!showResult;picture.source(hasPicture()?url(showResult?result:source.media):null);syncPicture(m.time,false);}
        if(source.kind==='image'){image.src=url(source.media);video.hidden=true;}else{picture.source(url(source.media));syncPicture(0,false);}
        function schedule(layout=false){
            layoutDirty=layoutDirty||layout;if(closed||paintFrame)return;
            paintFrame=requestAnimationFrame(()=>{paintFrame=0;if(closed)return;flushSeek();if(layoutDirty){layoutDirty=false;draw();}else drawHead();});
        }
        function flushSeek(){if(!seekRequested)return;seekRequested=false;if(separateSpeech())speech.seek(m.time);syncPicture(m.time,false);}
        function tick(now){
            if(!playing||closed)return;
            const interval=1000/m.fps;
            if(now-lastTick<interval-1){frame=requestAnimationFrame(tick);return;}
            lastTick=now;
            const drivers=[...(separateSpeech()?[speech]:[]),...(hasPicture()?[picture]:[])];
            const failed=drivers.find(d=>d.error);
            if(failed){pause();onstatus(T('The preview could not play. Try again.'));return;}
            if(starting){
                if(drivers.some(d=>!d.running)){if(now-clock.at>10000){pause();onstatus(T('The preview could not play. Try again.'));return;}frame=requestAnimationFrame(tick);return;}
                starting=false;clock={at:now,time:m.time};
            }
            if(separateSpeech()){
                m.time=clamp(audio.currentTime,0,m.seconds);
                // The speech clock owns the original-shot audition. Rendered lips use their own embedded sound.
                syncPicture(m.time,true);
            }else if(hasPicture())m.time=clamp(showResult?video.currentTime:(video.currentTime-F.val(source,'in'))/F.val(source,'speed'),0,m.seconds);
            else m.time=clamp(clock.time+(now-clock.at)/1000,0,m.seconds);
            drawHead();
            const master=separateSpeech()?audio:hasPicture()?video:null;
            if(m.time>=m.seconds-.015||master?.ended){if(m.time>=m.seconds-.05)m.time=m.seconds;pause();return;}
            frame=requestAnimationFrame(tick);
        }
        function start(){
            if(closed||playing)return;
            if(m.time>=m.seconds-1/m.fps)m.time=0;
            if(take&&!compiled&&!readOnly&&!showResult){onstatus(T('Wait for the edited speech preview'));return;}
            F.player?.pause();onplay&&onplay();cancelAnimationFrame(paintFrame);paintFrame=0;seekRequested=false;
            playing=true;starting=true;lastTick=-Infinity;clock={at:performance.now(),time:m.time};F.updateText(play,'❚❚');
            if(separateSpeech()){speech.seek(m.time);speech.play();}else speech.pause();
            syncPicture(m.time,true,true);frame=requestAnimationFrame(tick);
        }
        function pause(){playing=false;starting=false;cancelAnimationFrame(frame);speech.pause();picture.pause();F.updateText(play,'▶');drawHead();}
        function seek(t){if(closed)return;if(playing)pause();m.time=clamp(t,0,m.seconds);seekRequested=true;schedule();}
        function drawHead(){head.style.transform=`translate3d(${m.time/Math.max(.001,m.seconds)*contentWidth}px,0,0)`;F.updateText(time,m.time.toFixed(2)+' / '+m.seconds.toFixed(2));split.disabled=locked||readOnly||!take||!m.parts.some(p=>m.time>p.start&&m.time<p.start+p.duration);}
        function changed(){pause();compiled=null;speech.source(null);result=null;rendered.disabled=true;if(showResult)setResultView(false);draw();onchange();}
        function action(fn){if(locked||readOnly)return;pause();if(fn())changed();else draw();}
        function draw(){
            content.style.width=(zoom*100)+'%';const key=m.seconds+'|'+zoom;
            if(rulerKey!==key){rulerKey=key;ruler.replaceChildren();const step=m.seconds/zoom>25?5:m.seconds/zoom>10?2:m.seconds/zoom>4?1:.5;for(let t=0;t<=m.seconds+1e-6;t+=step)ruler.append(h('span',{style:{left:(t/m.seconds*100)+'%'}},t.toFixed(step<1?1:0)+'s'));}
            let empty=lane.querySelector('.fx-speech-empty');if(!take&&!empty)lane.append(h('span',{class:'fx-speech-empty'},T('Generate speech to start editing')));else if(take&&empty)empty.remove();
            const ids=new Set(m.parts.map(p=>p.id));for(const [id,el] of pieces)if(!ids.has(id)){el.remove();pieces.delete(id);}
            for(const p of m.parts){
                let el=pieces.get(p.id);if(!el){el=h('div',{'data-piece':p.id,title:T('Speech')},h('div',{class:'fx-speech-wave'}),h('span',{class:'fx-speech-name'},T('Speech')),h('span',{class:'fx-speech-edge left','data-edge':'l'}),h('span',{class:'fx-speech-edge right','data-edge':'r'}));pieces.set(p.id,el);lane.append(el);}
                el.className='fx-speech-piece'+(p.id===m.selected?' selected':'');el.style.left=p.start/m.seconds*100+'%';el.style.width=p.duration/m.seconds*100+'%';
                const wave=el.firstChild;wave.style.backgroundImage=`url("${url(take.web,'wave')}")`;wave.style.backgroundSize=(m.sourceSeconds/p.duration*100)+'% 100%';wave.style.backgroundPosition=(p.in/Math.max(.00001,m.sourceSeconds-p.duration)*100)+'% 0';
            }
            band.hidden=!m.range;if(m.range){band.style.left=m.range[0]/m.seconds*100+'%';band.style.width=(m.range[1]-m.range[0])/m.seconds*100+'%';}
            undo.disabled=locked||readOnly||!m.past.length;redo.disabled=locked||readOnly||!m.future.length;split.disabled=locked||readOnly||!take||!m.parts.some(p=>m.time>p.start&&m.time<p.start+p.duration);remove.disabled=locked||readOnly||(!m.selected&&!m.range);silence.disabled=locked||readOnly||!take||!m.parts.length;drawHead();
        }
        const at=e=>clamp((e.clientX-content.getBoundingClientRect().left)/content.getBoundingClientRect().width*m.seconds,0,m.seconds);
        content.addEventListener('pointerdown',e=>{if(e.button!==0||locked)return;e.preventDefault();node.focus();pause();const piece=e.target.closest('[data-piece]'),t=at(e),edge=e.target.dataset.edge;
            if(readOnly){drag={seek:true};seek(t);}
            else if(piece&&!e.shiftKey){m.selected=piece.dataset.piece;m.range=null;drag={id:m.selected,edge,x:e.clientX,before:m.snapshot(),moved:false};}
            else{m.selected=null;m.range=null;drag={range:e.target.closest('.fx-speech-lane')?t:null,seek:true};seek(t);}
            content.setPointerCapture(e.pointerId);draw();
        });
        content.addEventListener('pointermove',e=>{if(!drag)return;if(drag.id){if(Math.abs(e.clientX-drag.x)<2&&!drag.moved)return;drag.moved=true;const delta=(e.clientX-drag.x)/content.getBoundingClientRect().width*m.seconds;m.drag(drag.id,drag.edge,delta,drag.before);schedule(true);}else if(drag.range!=null){m.range=[Math.min(drag.range,at(e)),Math.max(drag.range,at(e))];schedule(true);}else seek(at(e));});
        const finish=(e,cancel)=>{if(!drag)return;if(drag.id){if(cancel)m.restore(drag.before);else if(m.commit(drag.before)){changed();}}drag=null;if(content.hasPointerCapture(e.pointerId))content.releasePointerCapture(e.pointerId);draw();};
        content.addEventListener('pointerup',e=>finish(e,false));content.addEventListener('pointercancel',e=>finish(e,true));
        function key(e){if(e.target.closest('input,textarea,select,[contenteditable=true]'))return;const ctrl=e.ctrlKey||e.metaKey;
            if(ctrl&&e.code==='KeyZ'){e.preventDefault();action(()=>e.shiftKey?m.redo():m.undo());}
            else if(ctrl&&e.code==='KeyY'){e.preventDefault();action(()=>m.redo());}
            else if(e.code==='Space'){e.preventDefault();F.guard(()=>playing?pause():start())();}
            else if(e.code==='KeyS'){e.preventDefault();action(()=>m.split());}
            else if(e.code==='Delete'||e.code==='Backspace'){e.preventDefault();action(()=>m.remove());}
            else if(e.code==='ArrowLeft'||e.code==='ArrowRight'){e.preventDefault();seek(m.time+(e.code==='ArrowLeft'?-1:1)*(e.shiftKey?1:1/m.fps));}
        }
        const resize=typeof ResizeObserver==='function'?new ResizeObserver(entries=>{contentWidth=entries[0].contentRect.width;schedule();}):null;
        resize?.observe(content);draw();
        return {view,node,model:m,key,pause,seek,
            take(value,offset,length,preserve){pause();const old=take;if(preserve&&old)m.retime(old.effective_speed/value.effective_speed,value.seconds);else m.setTake(value.seconds,offset,length,Array.isArray(preserve)?preserve:null);take=value;compiled=null;changed();},
            moveTo(offset){action(()=>m.change(()=>{const delta=offset-Math.min(...m.parts.map(p=>p.start));if(m.parts.some(p=>p.start+delta<0||p.start+delta+p.duration>m.seconds+1e-6))throw Error(T('The speech range must fit inside this shot'));m.parts.forEach(p=>p.start+=delta);}));},
            ready(web){if(closed)return;compiled=web;speech.source(url(web));},
            stale(){pause();compiled=null;speech.source(null);},
            result(web,seconds){pause();m.seconds=web&&seconds?seconds:clip.duration;result=web;rendered.disabled=!web;setResultView(!!web);draw();},
            lock(value,readonly=false){locked=value;readOnly=readonly;node.classList.toggle('locked',value);draw();},
            destroy(){if(closed)return;closed=true;pause();cancelAnimationFrame(paintFrame);resize?.disconnect();picture.dispose();speech.dispose();image.removeAttribute('src');pieces.clear();drag=null;},
            get takeWeb(){return take?.web;}
        };
    };
})();

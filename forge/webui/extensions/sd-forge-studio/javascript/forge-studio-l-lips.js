/* Lip sync: audition and tune speech first, animate only after approval, then apply the preview explicitly. */
(function () {
    'use strict';
    const FS = window.ForgeStudio, S = FS.state, T = (...a) => FS.T(...a), q = encodeURIComponent;
    FS.takeLips = (id, web, seconds) => FS.change('lip sync', () => {
        const c = FS.clip(id);
        const previousDuration=c.duration;
        if (!c.lips) {
            c.lips = {media:c.media, in:FS.val(c,'in'), speed:FS.val(c,'speed'), duration:c.duration, kind:c.kind, muted:c.muted == null ? null : c.muted};
            if (FS.keysOf(c,'speed')) c.lips.speedKeys = JSON.parse(JSON.stringify(c.keys.speed));
        }
        if (!('muted' in c.lips)) c.lips.muted = c.muted == null ? null : c.muted;
        Object.assign(c, {media:web, kind:'video', in:0, duration:Math.max(1/FS.fps(),seconds)});
        delete c.speed;
        if (c.keys) { delete c.keys.speed; if (!Object.keys(c.keys).length) delete c.keys; }
        if(Math.abs(c.duration-previousDuration)>1e-7)FS.closeGaps();
    });
    FS.lipsBack = id => FS.change('lip sync off', () => {
        const c = FS.clip(id), old = c.lips; if (!old) return;
        const previousDuration=c.duration;
        Object.assign(c, {media:old.media,in:old.in,duration:old.duration,kind:old.kind || 'video'});
        if ('muted' in old) { if (old.muted == null) delete c.muted; else c.muted = old.muted; }
        if (old.speed !== 1) c.speed=old.speed; else delete c.speed;
        if (old.speedKeys) { c.keys=c.keys || {}; c.keys.speed=old.speedKeys; }
        delete c.lips; if(Math.abs(c.duration-previousDuration)>1e-7)FS.closeGaps();
    });
    FS.lipsSource = c => {
        if (!c.lips) return c;
        const source=Object.assign({},c,c.lips); delete source.keys;
        if (c.lips.speedKeys) source.keys={speed:c.lips.speedKeys};
        return source;
    };
    FS.lipsWindow = (c,r) => {
        if (!r) return {offset:0,seconds:c.duration};
        const a=r ? Math.max(c.start,r.in) : c.start, b=r ? Math.min(FS.end(c),r.out) : FS.end(c);
        return b>a ? {offset:a-c.start,seconds:b-a} : {offset:0,seconds:c.duration};
    };
    FS.embedLipAudio = id => FS.change('use embedded lip audio',()=>{
        const c=FS.clip(id);if(!c?.lips||!S.info[c.media]?.audio)return false;
        const ids=new Set([...(c.lips.speechClips||[]),c.lips.speechClip,FS.detachedAudio(c)?.id].filter(Boolean));
        const removed=S.doc.clips.filter(a=>ids.has(a.id));
        if(removed.length)c.lips.detachedSpeechBackup=JSON.parse(JSON.stringify(removed));
        S.doc.clips=S.doc.clips.filter(a=>!ids.has(a.id));
        delete c.lips.speechClips;delete c.lips.speechClip;delete c.detachedAudio;
        c.lips.audioMode='embedded';c.muted=false;c.speech=true;
        return true;
    });
    FS.migrateLipAudio = () => {
        const plans=[];
        for(const c of S.doc.clips.slice()){
            if(!c.lips||c.lips.audioMode||!c.muted||!S.info[c.media]?.audio)continue;
            const ids=c.lips.speechClips||[c.lips.speechClip].filter(Boolean),o=c.lips.lastRequest?.options||{};
            if(!ids.length||c.lips.lastRequest?.mode!=='text'||FS.val(c,'volume')!==1||FS.keysOf(c,'volume'))continue;
            const parts=o.segments,tracks=S.doc.tracks;let shift=null,safe=true;
            ids.forEach((id,i)=>{
                const a=FS.clip(id),p=parts?.[i];
                if(!a||a.kind!=='audio'||a.muted||a.keys||FS.val(a,'speed')!==1||FS.val(a,'volume')!==1||a.fadeIn||a.fadeOut||a.duck||tracks.find(t=>t.id===a.track)?.muted){safe=false;return;}
                if(p){if(a.media!==o.prepared||Math.abs(FS.val(a,'in')-p.in)>.001||Math.abs(a.duration-p.duration)>.001){safe=false;return;}}
                else if(FS.val(a,'in')!==0||Math.abs(a.duration-(S.info[a.media]?.duration||0))>.05){safe=false;return;}
                const delta=a.start-c.start-(p?.start??o.offset??0);
                if(shift==null)shift=delta;else if(Math.abs(delta-shift)>.001)safe=false;
            });
            if(safe)plans.push(c.id);
        }
        plans.forEach(id=>FS.embedLipAudio(id));return plans.length;
    };
    function consumeSound(id,from,to){
        const a=FS.clip(id);if(!a)return;
        const original=JSON.parse(JSON.stringify(a)),end=FS.end(a),left=Math.max(a.start,from),right=Math.min(end,to);
        if(right<=left)return;
        const pieces=[];
        if(left>a.start+1e-7){const p={...JSON.parse(JSON.stringify(original)),duration:left-a.start,fadeOut:0};delete p.linkedTo;if(p.keys)FS.moveKeys(p,0);pieces.push(p);}
        if(right<end-1e-7){const p={...JSON.parse(JSON.stringify(original)),id:pieces.length?FS.newId():a.id,start:right,duration:end-right,in:FS.sourceTime(original,right),fadeIn:0};delete p.linkedTo;if(p.keys)FS.moveKeys(p,right-original.start);pieces.push(p);}
        S.doc.clips=S.doc.clips.filter(x=>x.id!==id);S.doc.clips.push(...pieces);
    }
    FS.applyLipResult = (id,result,request) => {
        const c=FS.clip(id),o=request.options||{};
        if(!c)throw Error(T('The shot changed; reopen lip sync'));
        if(result.speech&&!S.info[result.web]?.audio)throw Error(T('The lip preview has no embedded audio'));
        const sc=o.sound_clip&&FS.clip(o.sound_clip);
        if(sc&&o.sound_snapshot&&JSON.stringify(sc)!==JSON.stringify(o.sound_snapshot))throw Error(T('The source sound changed; generate a new lip preview'));
        return FS.change('apply lip sync',()=>{
            const owned=new Set([...(c.lips?.speechClips||[]),c.lips?.speechClip,...S.doc.clips.filter(a=>a.linkedTo===id).map(a=>a.id)].filter(Boolean));
            S.doc.clips=S.doc.clips.filter(a=>!owned.has(a.id));
            FS.takeLips(id,result.web,result.seconds);const x=FS.clip(id);
            x.lips.lastRequest=request;x.lips.audioMode='embedded';x.muted=false;
            if(result.speech||request.mode==='sound')x.speech=true;
            delete x.lips.speechClips;delete x.lips.speechClip;delete x.detachedAudio;
            if(request.mode==='sound'&&sc&&!owned.has(sc.id))consumeSound(sc.id,x.start+(o.offset||0),FS.end(x));
        });
    };
    FS.openLips = async function (clip) {
        const project=S.project, selected=FS.region() && JSON.parse(JSON.stringify(FS.region()));
        const opts=await FS.api('lips/options');
        if(S.project!==project || !FS.clip(clip.id))return;
        const c=FS.clip(clip.id), source=FS.lipsSource(c), old=c.lips?.lastRequest?.options || {}, initial=FS.lipsWindow(c,selected), h=FS.h;
        const clipKey=()=>JSON.stringify([FS.clip(c.id)?.media,FS.clip(c.id)?.start,FS.clip(c.id)?.duration,FS.clip(c.id)?.in,FS.clip(c.id)?.speed]);
        const originalKey=clipKey();
        let closed=false,busy=false,raw=null,rawKey='',prepared=null,edited=null,pending=null,request=null,tuneVersion=0,editVersion=0,tuning=null,editing=null,suspendEdit=false;
        const current=()=>{if(S.project!==project || originalKey!==clipKey())throw Error(T('The shot changed; reopen lip sync'));return FS.clip(c.id);};
        const field=(label,control,cls='')=>h('label',{class:'fx-lip-field '+cls},h('span',{},label),control);
        const num=(id,value,min,max,step)=>h('input',{id,class:'fx-num',type:'number',value,min,max,step});
        function slider(id,label,value,min,max,step){
            const n=num(id,value,min,max,step),r=h('input',{type:'range',min,max,step,value,'aria-label':label});
            r.oninput=()=>{n.value=r.value;n.dispatchEvent(new Event('input',{bubbles:true}));};
            r.onchange=()=>n.dispatchEvent(new Event('change',{bubbles:true}));
            n.oninput=()=>r.value=n.value;
            return {n,r,node:h('label',{class:'fx-lip-slider'},h('span',{},label),r,n)};
        }
        const start=num('fx-lips-start',!selected?(old.range_offset??old.offset??initial.offset):initial.offset,0,c.duration,.01);
        const seconds=num('fx-lips-seconds',!selected?(old.selected_seconds||initial.seconds):initial.seconds,1/FS.fps(),c.duration,.01);
        const windowOf=()=>{current();const offset=+start.value,length=+seconds.value;if(!Number.isFinite(offset)||!Number.isFinite(length)||offset<0||length<1/FS.fps()-1e-8||offset+length>c.duration+1e-7)throw Error(T('The speech range must fit inside this shot'));return {offset,seconds:length};};
        const text=h('textarea',{id:'fx-lips-text',class:'fx-input',dir:'auto',rows:2,placeholder:T('What the character says (Hebrew is given niqqud by itself)')},old.text||'');
        const language=h('select',{id:'fx-lips-lang',class:'fx-select'},...(opts.languages||['Hebrew']).map(v=>h('option',{value:v},T(v))));
        const voice=h('select',{id:'fx-lips-voice',class:'fx-native-voice',tabindex:-1,'aria-hidden':'true'},...(opts.voices||[]).map(v=>h('option',{value:v},v)));
        if(old.language)language.value=old.language;if((opts.voices||[]).includes(old.voice))voice.value=old.voice;
        const delivery=h('select',{id:'fx-lips-delivery',class:'fx-select'},...(opts.deliveries||['Natural','Relaxed','Expressive','Energetic']).map(v=>h('option',{value:v},T(v))));delivery.value=old.delivery||'Natural';
        const expression=slider('fx-lips-expression',T('Expression'),old.expressive??.5,0,2,.05), speed=slider('fx-lips-speed',T('Speed'),old.speed||1,.5,3,.05),pitch=slider('fx-lips-pitch',T('Pitch (semitones)'),old.pitch||0,-12,12,.5),tone=slider('fx-lips-tone',T('Tone: warm to bright'),old.tone||0,-1,1,.1),gain=slider('fx-lips-gain',T('Volume (dB)'),old.gain||0,-12,12,.5);
        const hint=h('div',{class:'fx-dim'},T('Piper has fixed delivery; pitch, tone and speed remain adjustable.'));
        const fit=h('input',{id:'fx-lips-fit',type:'checkbox',checked:old.fit!==false});
        const status=h('div',{class:'fx-dim fx-xstatus',role:'status'}),summary=h('span',{class:'fx-lip-summary'});
        const way=h('select',{id:'fx-lips-way',class:'fx-select'},h('option',{value:'text',disabled:!(opts.lips&&opts.voice)},T('To written words')),h('option',{value:'sound',disabled:!opts.lips},T('To a sound in the edit')),h('option',{value:'face',disabled:!opts.face},T('Like a face video (LivePortrait)')));
        if(!opts.voice)way.value=opts.lips?'sound':'face';
        const sounds=S.doc.clips.filter(x=>x.kind==='audio'&&x.media).sort((a,b)=>Math.abs(a.start-c.start)-Math.abs(b.start-c.start));
        const sound=h('select',{id:'fx-lips-sound',class:'fx-select'},...sounds.map(x=>h('option',{value:x.id},x.media)));
        const faces=Object.keys(S.media||{}).filter(w=>/\.(mp4|mov|webm|mkv|m4v)$/i.test(w)&&w!==c.media);
        const face=h('select',{id:'fx-lips-face',class:'fx-select'},h('option',{value:''},T('Choose a face video…')),...faces.map(w=>h('option',{value:w},w)));
        const faceRegion=h('select',{id:'fx-lips-region',class:'fx-select'},h('option',{value:'lips'},T('Only the lips (cleanest)')),h('option',{value:'all'},T('The whole face and head (livelier; an edge can show around the face)')));
        const file=h('input',{type:'file',accept:'video/*',hidden:true});
        file.onchange=FS.guard(async()=>{const f=file.files[0];if(!f)return;const a=await FS.api(`project/upload?name=${q(project)}&web=${q(f.name)}`,f,true);if(closed)return;S.media[a.web]={path:a.web,missing:false};face.append(h('option',{value:a.web},a.web));face.value=a.web;});
        const voiceKey=()=>JSON.stringify({text:text.value.trim(),language:language.value,voice:voice.value,delivery:delivery.value,expressive:+expression.n.value});
        const tuneKey=()=>JSON.stringify({raw,seconds:+seconds.value,speed:+speed.n.value,pitch:+pitch.n.value,tone:+tone.n.value,gain:+gain.n.value,fit:fit.checked});
        const editKey=()=>JSON.stringify({web:prepared?.web,segments:editor.model.segments(),seconds:c.duration});
        const apply=h('button',{id:'fx-lips-apply',class:'fx-btn fx-primary',disabled:true},T('Apply to timeline'));
        const go=h('button',{id:'fx-lips-generate',class:'fx-btn fx-primary',disabled:true},T('Generate lip sync'));
        const makeSpeech=h('button',{id:'fx-lips-make-speech',class:'fx-btn fx-primary'},T('Generate speech'));
        const tune=h('button',{id:'fx-lips-tune-speech',class:'fx-btn',disabled:true},T('Refresh audio'));
        const editor=FS.createSpeechEditor({project,clip:c,source,onplay:()=>stopSamples(),onstatus:t=>status.textContent=t,onchange:()=>{if(suspendEdit)return;edited=null;++editVersion;invalidate();enable();clearTimeout(editing);editing=setTimeout(()=>FS.guard(compileEdit)(),250);}});
        function invalidate(){pending=null;apply.disabled=true;editor.result(null);}
        function enable(){
            const piper=voice.value==='Fast Hebrew voice (Piper)';
            for(const el of controls.querySelectorAll('input,select,textarea,button'))el.disabled=busy;
            delivery.disabled=expression.n.disabled=expression.r.disabled=busy||piper;hint.hidden=!piper;
            tune.disabled=busy||!raw||rawKey!==voiceKey();makeSpeech.disabled=busy||way.value!=='text';
            go.disabled=busy||(way.value==='text'&&(!prepared||prepared.key!==tuneKey()||rawKey!==voiceKey()||!edited||edited.key!==editKey()||!editor.model.parts.length));
            apply.disabled=busy||!pending;editor.lock(busy,way.value!=='text');
        }
        async function compileEdit(){
            if(closed||way.value!=='text'||!prepared||rawKey!==voiceKey()||prepared.key!==tuneKey())return;
            const parts=editor.model.segments(),key=editKey(),version=++editVersion;edited=null;editor.stale();enable();
            if(!parts.length){status.textContent=T('No speech pieces. Undo or generate speech again.');return;}
            status.textContent=T('Updating edited speech…');
            try{
                const task=await FS.follow(await FS.api('lips/edit-speech',{name:project,web:prepared.web,seconds:c.duration,segments:parts}));
                if(closed||way.value!=='text'||version!==editVersion||key!==editKey()||S.project!==project)return;
                edited={...task,key};editor.ready(task.web);status.textContent=T('Listen and edit. Generate lips when the timing is right.');
            }finally{if(!closed)enable();}
        }
        async function tuneSpeech(reset=false){
            if(!raw||rawKey!==voiceKey())return;
            const w=windowOf(),key=tuneKey(),version=++tuneVersion;prepared=null;edited=null;++editVersion;invalidate();editor.stale();enable();
            status.textContent=T('Updating speech preview; the lips have not been generated');
            const task=await FS.follow(await FS.api('lips/prepare-speech',{name:project,web:raw,seconds:w.seconds,speed:+speed.n.value,pitch:+pitch.n.value,tone:+tone.n.value,gain:+gain.n.value,fit:fit.checked}));
            if(closed||version!==tuneVersion||S.project!==project||key!==tuneKey())return;
            prepared={...task,key};S.media[task.web]={path:task.web,missing:false};
            suspendEdit=true;try{editor.take(task,w.offset,w.seconds,!reset&&!!editor.takeWeb);}catch(e){prepared=null;status.textContent=e.message;throw e;}finally{suspendEdit=false;}
            summary.textContent=T('Effective speed: {0}x',task.effective_speed.toFixed(2));await compileEdit();
        }
        tune.onclick=FS.guard(async()=>{try{await tuneSpeech();}finally{enable();}});
        makeSpeech.onclick=FS.guard(async()=>{
            const w=windowOf(),key=voiceKey();if(!text.value.trim())throw Error(T('Write what the character says'));
            busy=true;invalidate();enable();editor.pause();stopSamples();status.textContent=T('Generating speech only; lip sync has not started');
            try{const task=await FS.follow(await FS.api('lips/speech',{name:project,text:text.value.trim(),language:language.value,voice:voice.value,seconds:w.seconds,delivery:delivery.value,expressive:+expression.n.value}),t=>{status.textContent=t.text;});if(closed||S.project!==project||key!==voiceKey())return;raw=task.raw;rawKey=key;S.media[raw]={path:raw,missing:false};await tuneSpeech(true);}finally{busy=false;if(!closed)enable();}
        });
        function voiceChanged(){++tuneVersion;++editVersion;prepared=null;edited=null;invalidate();editor.stale();voiceName.textContent=voice.value;for(const el of menu.querySelectorAll('[role=option]'))el.setAttribute('aria-selected',String(el.dataset.voice===voice.value));status.textContent=T('Voice settings changed. Generate speech again before the lips.');enable();}
        [text,language,voice,expression.n].forEach(el=>el.addEventListener('input',voiceChanged));
        voice.addEventListener('change',voiceChanged);
        delivery.onchange=()=>{expression.n.value=expression.r.value=({Natural:.5,Relaxed:.25,Expressive:.85,Energetic:1.2})[delivery.value];voiceChanged();};
        function adjustment(){++tuneVersion;++editVersion;prepared=null;edited=null;invalidate();editor.stale();enable();clearTimeout(tuning);tuning=setTimeout(()=>FS.guard(async()=>{try{await tuneSpeech();}finally{if(!closed)enable();}})(),450);}
        [speed.n,pitch.n,tone.n,gain.n].forEach(el=>el.addEventListener('input',adjustment));
        [fit,seconds].forEach(el=>el.addEventListener('change',adjustment));
        start.onchange=FS.guard(()=>{windowOf();if(editor.model.parts.length)editor.moveTo(+start.value);});
        const voiceName=h('span',{},voice.value),voiceButton=h('button',{id:'fx-lips-voice-button',class:'fx-btn fx-lip-voice-trigger','aria-expanded':'false','aria-haspopup':'listbox'},voiceName,h('span',{},'⌄'));
        const menu=h('div',{class:'fx-lip-voices',role:'listbox','aria-label':T('Voice'),hidden:true}),samples=[];
        function stopSamples(){samples.forEach(a=>a.pause());}
        function closeMenu(){menu.hidden=true;voiceButton.setAttribute('aria-expanded','false');stopSamples();}
        voiceButton.onclick=()=>{menu.hidden=!menu.hidden;voiceButton.setAttribute('aria-expanded',String(!menu.hidden));if(menu.hidden)stopSamples();};
        for(const item of opts.voice_samples||[]){
            const a=h('audio',{preload:'metadata',hidden:true,src:item.sample?`/forge-studio/lips/voice-file?voice=${q(item.name)}`:null});samples.push(a);
            const choose=h('button',{class:'fx-lip-voice-choice',role:'option','aria-selected':String(item.name===voice.value),'data-voice':item.name,onclick:()=>{voice.value=item.name;voiceChanged();closeMenu();voiceButton.focus();}},item.name);
            const audition=h('button',{class:'fx-btn fx-lip-audition','aria-label':T('Preview voice: {0}',item.name),title:T('Preview voice: {0}',item.name)},'▶');
            audition.onclick=FS.guard(async()=>{if(!a.paused){a.pause();return;}stopSamples();editor.pause();audition.disabled=true;try{if(!a.src){await FS.follow(await FS.api('lips/voice-sample',{voice:item.name}));if(closed||menu.hidden)return;a.src=`/forge-studio/lips/voice-file?voice=${q(item.name)}`;}a.currentTime=0;await a.play();}finally{audition.disabled=false;}});
            a.onplay=()=>{audition.textContent='❚❚';};a.onpause=a.onended=()=>{audition.textContent='▶';};
            menu.append(h('div',{class:'fx-lip-voice-option'},choose,audition,a));
        }
        const voicePicker=h('div',{class:'fx-lip-voice-picker'},voice,voiceButton,menu);
        const clickOutside=e=>{if(!voicePicker.contains(e.target))closeMenu();};document.addEventListener('pointerdown',clickOutside);
        const tuningPanel=h('details',{class:'fx-lip-tuning'},h('summary',{},T('Voice tuning'),summary),h('div',{class:'fx-lip-tuning-grid'},speed.node,pitch.node,tone.node,gain.node),
            h('div',{class:'fx-lip-fit'},h('label',{},fit,' ',T('Fit all words inside the selected time')),tune),hint);
        const textPart=h('div',{class:'fx-lip-text-part'},h('div',{class:'fx-lip-choices'},field(T('Language'),language),field(T('Voice'),voicePicker),field(T('Delivery style'),delivery),expression.node),
            h('div',{class:'fx-lip-prompt'},text,makeSpeech),tuningPanel);
        const soundPart=h('div',{class:'fx-lip-alt'},field(T('Sound'),sound));
        const facePart=h('div',{class:'fx-lip-alt'},field(T('Video'),face),field(T('Moves'),faceRegion),h('button',{class:'fx-btn',onclick:()=>file.click()},T('From the computer…')),file);
        const controls=h('div',{class:'fx-lip-controls'},h('div',{class:'fx-lip-source-row'},field(T('Way'),way),field(T('Start in shot (seconds)'),start),field(T('Seconds'),seconds)),textPart,soundPart,facePart);
        function show(){++tuneVersion;++editVersion;textPart.hidden=way.value!=='text';soundPart.hidden=way.value!=='sound';facePart.hidden=way.value!=='face';invalidate();editor.stale();if(way.value==='text'&&edited?.key===editKey())editor.ready(edited.web);else if(way.value==='text'&&prepared?.key===tuneKey())FS.guard(compileEdit)();enable();}
        way.onchange=show;
        go.onclick=FS.guard(async()=>{
            const x=current(),options={};let mode=way.value;if(mode==='text'){if(go.disabled||!edited)throw Error(T('Generate and listen to the speech preview first'));Object.assign(options,{speech:edited.web,offset:0,clip_seconds:x.duration,source_speed:FS.val(source,'speed')});mode='prepared';}
            else if(mode==='sound'){const sc=FS.clip(sound.value);if(!sc)throw Error(T('Choose the sound'));const from=Math.max(sc.start,x.start);Object.assign(options,{sound:sc.media,sound_clip:sc.id,sound_snapshot:JSON.parse(JSON.stringify(sc)),sound_in:FS.sourceTime(sc,from),sound_seconds:FS.sourceTime(sc,FS.end(sc))-FS.sourceTime(sc,from),offset:from-x.start});}
            else{if(!face.value)throw Error(T('Choose a face video'));Object.assign(options,{face:face.value,region:faceRegion.value});}
            busy=true;invalidate();enable();editor.pause();closeMenu();
            request={mode:way.value,options:{...options,...(way.value==='text'?JSON.parse(voiceKey()):{}),...(way.value==='text'?{raw,prepared:prepared.web,effective_speed:prepared.effective_speed,segments:editor.model.segments(),speed:+speed.n.value,pitch:+pitch.n.value,tone:+tone.n.value,gain:+gain.n.value,fit:fit.checked,range_offset:+start.value,selected_seconds:+seconds.value}: {})}};
            try{status.textContent=T('Making a preview. The timeline changes only after you apply it.');const task=await FS.follow(await FS.api('lips',{name:project,web:source.media,start:FS.val(source,'in'),seconds:FS.spanOf(source),mode,options}),t=>status.textContent=t.text);if(closed||S.project!==project)return;current();pending=task;editor.result(task.web,task.seconds);status.textContent=T('Preview ready. Watch it, then apply it to the timeline or cancel.');}finally{busy=false;if(!closed)enable();}
        });
        apply.onclick=FS.guard(async()=>{
            if(!pending)return;current();busy=true;enable();const result=pending;
            try{S.media[result.web]={path:result.web,missing:false};await FS.probe(result.web);if(result.speech){S.media[result.speech]={path:result.speech,missing:false};await FS.probe(result.speech);}if(request.mode==='text'){S.media[request.options.prepared]={path:request.options.prepared,missing:false};await FS.probe(request.options.prepared);}if(closed)return;current();
                FS.applyLipResult(c.id,result,request);close(true);FS.message(T('Lip sync applied to the timeline'));
            }finally{busy=false;if(!closed)enable();}
        });
        function close(force){if(busy&&!force&&!confirm(T('It goes on in the background. Close this window?')))return;closed=true;++tuneVersion;++editVersion;clearTimeout(tuning);clearTimeout(editing);editor.destroy();stopSamples();document.removeEventListener('pointerdown',clickOutside);box.remove();}
        const box=h('div',{class:'fx-modal-back',dir:FS.hebrew()?'rtl':'ltr'},h('div',{class:'fx-modal fx-lips-modal fx-lip-workspace',role:'dialog','aria-modal':'true','aria-label':T('Lip sync')},
            h('div',{class:'fx-modal-head'},h('span',{},T('Lip sync')),h('span',{class:'fx-lip-filename'},c.media),h('span',{class:'fx-spacer'}),h('button',{class:'fx-icon',onclick:()=>close(false),title:T('Close')},'✕')),
            h('div',{class:'fx-lip-workbody'},controls,editor.view,editor.node),
            h('div',{class:'fx-modal-foot'},status,h('div',{class:'fx-lip-actions'},h('button',{id:'fx-lips-cancel',class:'fx-btn',onclick:()=>close(false)},T('Cancel')),h('span',{class:'fx-spacer'}),go,apply))));
        box.addEventListener('keydown',e=>{e.stopPropagation();if(e.code==='Escape'){e.preventDefault();if(!menu.hidden)closeMenu();else close(false);}else editor.key(e);});
        FS.UI.root.append(box);show();editor.node.focus();
        if(old.raw&&old.prepared&&Array.isArray(old.segments)){
            FS.guard(async()=>{try{await FS.api(`probe?name=${q(project)}&web=${q(old.raw)}`);const info=await FS.api(`probe?name=${q(project)}&web=${q(old.prepared)}`);if(closed)return;raw=old.raw;rawKey=voiceKey();prepared={web:old.prepared,key:tuneKey(),seconds:info.duration,effective_speed:old.effective_speed||old.speed||1};suspendEdit=true;try{editor.take(prepared,+start.value,+seconds.value,old.segments.map((p,i)=>({...p,id:'restored'+i})));}finally{suspendEdit=false;}await compileEdit();if(!closed&&edited&&c.lips)editor.result(c.media,c.duration);}catch(e){status.textContent=T('Generate speech to begin a new take');}finally{if(!closed)enable();}})();
        }
    };
    FS.openRangeLips = async region => {
        const candidates=S.doc.clips.filter(c=>(c.kind==='video'||c.kind==='image')&&c.media&&(!region.tracks.length||region.tracks.includes(c.track))&&c.start<region.out&&FS.end(c)>region.in);
        if(candidates.length!==1)throw Error(T('Select a range inside one shot for lip sync'));
        await FS.openLips(candidates[0]);
    };
    const talks=c=>c&&(c.kind==='video'||c.kind==='image')&&c.media;
    FS.clipActions.push({id:'lips',label:'👄 '+T('Lip sync: make it say something'),when:talks,run:c=>FS.openLips(c)});
    FS.clipActions.push({id:'lipsback',label:'↩ '+T('Back to the original (before the lip sync)'),when:c=>c&&c.lips,run:c=>FS.lipsBack(c.id)});
    FS.clipActions.push({id:'lipsembed',label:T('Use original embedded lip audio'),when:c=>c?.lips&&c.muted&&S.info[c.media]?.audio,run:c=>FS.embedLipAudio(c.id)});
})();

/* A captured frame stays a picture: save it, or put it into an existing tool without starting generation. */
(function(){
    'use strict';
    const F=window.ForgeStudio,T=(...a)=>F.T(...a);
    const components=()=>window.gradio_config?.components||[];
    const component=id=>components().find(c=>c.props?.elem_id===id);
    function set(c,props){if(!c?.instance)throw Error(T('This destination is not available'));Object.assign(c.props,props);c.instance.$set(props);}
    function tab(id,value){const c=components().find(c=>c.type==='tabs'&&c.props?.elem_id===id);set(c,{selected:value});}
    function nextSlot(ids){const at=ids.findIndex(id=>{const c=component(id);return c&&!c.props.value;});if(at<0)throw Error(T('All reference slots are full. Clear a slot first.'));return at;}
    const destinations=[
        {id:'img2img',label:'Image to Image',fields:['img2img_image']},
        {id:'inpaint',label:'Inpaint',fields:['img2maskimg']},
        {id:'native',label:'FIBO / FLUX3 · native controls',fields:[1,2,3,4].map(n=>'txt2img_fibo2_reference_'+n)},
        {id:'i2v',label:'Video Studio · Image to Video',fields:[1,2,3,4,5,6].map(n=>'vs_picture'+n)},
        {id:'ingredients',label:'Video Studio · Ingredients to Video',fields:[1,2,3,4].map(n=>'vs_ingredient'+n)},
    ];
    let queue=Promise.resolve(),activeClose=null,lastDestination='img2img';
    function send(image,target){const result=queue.then(()=>sendOne(image,target));queue=result.catch(()=>{});return result;}
    async function sendOne(image,target){
        if(!image?.startsWith('data:image/png;base64,'))throw Error(T('Choose a captured PNG frame'));
        const dest=destinations.find(d=>d.id===target);
        if(!dest||dest.fields.some(id=>!component(id)))throw Error(T('This destination is not available'));
        const append=['native','i2v','ingredients'].includes(target),slot=append?nextSlot(dest.fields):0;
        if(target==='native'&&slot>0&&component('txt2img_fibo2_mask')?.props.value)throw Error(T('Clear the exact mask before adding another reference'));
        // Native controls belong to the selected model family. If it is not selected yet,
        // select FIBO Dev explicitly so the newly assigned reference will be used by Generate.
        if(target==='native'){
            const [fibo,flux]=await Promise.all([fetch('./fibo2/checkpoints').then(r=>r.json()),fetch('./flux3/checkpoints').then(r=>r.ok?r.json():{titles:[]}).catch(()=>({titles:[]}))]);
            const selected=(await fetch('./sdapi/v1/options').then(r=>r.json())).sd_model_checkpoint;
            const known=[...(fibo.models||[]).map(m=>m.title),...(flux.titles||[])];
            if(!known.some(t=>t.split(' [')[0]===String(selected).split(' [')[0])){
                const model=fibo.models?.find(m=>m.variant==='dev')||fibo.models?.[0];
                if(!model)throw Error(T('No FIBO or FLUX3 checkpoint is available'));
                const r=await fetch('./sdapi/v1/options',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({sd_model_checkpoint:model.title})});
                if(!r.ok)throw Error(T('Could not select the image model'));
                set(component('setting_sd_model_checkpoint'),{value:model.title,value_is_output:true});
            }
        }
        set(component(dest.fields[slot]),{value:image,value_is_output:true,visible:true});
        if(target==='native'&&component('txt2img_fibo2_mode'))set(component('txt2img_fibo2_mode'),{value:'auto',value_is_output:true});
        if(target==='i2v'){
            const count=component('vs_picture_count');
            const n=Math.max(+count.props.value||1,slot+1,...dest.fields.map((id,i)=>component(id).props.value?i+1:0));
            set(count,{value:n,value_is_output:true});
            // Apply the same layout updates as the existing ➕ control, including parent rows.
            const dep=(window.gradio_config.dependencies||[]).find(d=>d.inputs?.includes(count.id)&&d.js==='videoStudioPictureLayout');
            if(dep&&window.videoStudioPictureLayout){const updates=window.videoStudioPictureLayout(n);dep.outputs.forEach((id,i)=>{const props={...updates[i]};delete props.__type__;set(components().find(c=>c.id===id),props);});}
        }
        if(document.fullscreenElement)await document.exitFullscreen();
        if(F.UI?.root?.classList.contains('fx-full'))F.setFull(false);
        if(target==='i2v'||target==='ingredients'){
            tab('tabs','video_studio');tab('vs_mode_tabs',target);window.videoStudioSyncControls?.();
        }else{
            tab('tabs',target==='native'?'txt2img':'img2img');
            if(target!=='native')tab('mode_img2img',target==='inpaint'?'inpaint':'img2img');
            else set(component('txt2img_fibo2_controls'),{open:true});
        }
        await new Promise(r=>requestAnimationFrame(r));
        document.getElementById(dest.fields[slot])?.scrollIntoView({block:'center',behavior:'smooth'});
        window.hebrewUi?.refresh?.(document.getElementById(target==='i2v'?'vs_tab_i2v':dest.fields[slot]));
        return {target,slot:slot+1};
    }
    function filename(value){return String(value||'frame').replace(/[<>:"/\\|?*\x00-\x1f]/g,'_').replace(/\.png$/i,'')+'.png';}
    async function save(image,name){
        name=filename(name);
        if(window.showSaveFilePicker){
            let handle;
            try{handle=await window.showSaveFilePicker({suggestedName:name,excludeAcceptAllOption:true,types:[{description:T('PNG picture'),accept:{'image/png':['.png']}}]});}
            catch(e){if(e.name==='AbortError')return {cancelled:true};if(e.name!=='NotSupportedError'&&e.name!=='SecurityError')throw e;}
            if(handle){const data=await fetch(image).then(r=>r.blob()),stream=await handle.createWritable();try{await stream.write(data);await stream.close();}catch(e){await stream.abort?.();throw e;}return {saved:true};}
        }
        return F.api('still/export',{image,filename:name});
    }
    async function open({canvas,filename:name='frame.png',host}){
        if(!canvas?.width||!canvas?.height)throw Error(T('There is no frame to capture'));
        activeClose?.();
        const image=canvas.toDataURL('image/png'),h=F.h,previous=document.activeElement;
        const status=h('div',{class:'ff-share-status',role:'status'}),nameInput=h('input',{class:'ff-share-name',value:filename(name),'aria-label':T('File name')}),
            target=h('select',{class:'ff-share-select','aria-label':T('Destination')},...destinations.map(d=>h('option',{value:d.id,disabled:d.fields.some(id=>!component(id))},T(d.label))));
        if([...target.options].some(o=>o.value===lastDestination&&!o.disabled))target.value=lastDestination;
        const close=()=>{back.remove();document.removeEventListener('keydown',keys,true);activeClose=null;previous?.focus?.();};
        const keys=e=>{e.stopImmediatePropagation();if(e.key==='Escape'){e.preventDefault();close();}if(e.key==='Tab'){const nodes=[...box.querySelectorAll('button,input,select')].filter(n=>!n.disabled);if(e.shiftKey&&document.activeElement===nodes[0]){e.preventDefault();nodes.at(-1).focus();}else if(!e.shiftKey&&document.activeElement===nodes.at(-1)){e.preventDefault();nodes[0].focus();}}};
        const run=fn=>async()=>{buttons.forEach(b=>b.disabled=true);try{status.textContent=T('Preparing the frame…');const result=await fn();status.textContent=result?.cancelled?T('Save cancelled'):T('The frame is saved');}catch(e){status.textContent=T(e.message||String(e));}finally{buttons.forEach(b=>b.disabled=false);}};
        const saveButton=h('button',{type:'button',class:'ff-share-primary',onclick:run(()=>save(image,nameInput.value))},T('Save picture as…'));
        const sendButton=h('button',{type:'button',class:'ff-share-primary',onclick:run(async()=>{await send(image,target.value);lastDestination=target.value;close();})},T('Send frame'));
        const buttons=[saveButton,sendButton];
        const box=h('div',{class:'ff-share-box',role:'dialog','aria-modal':'true','aria-label':T('Save or send this frame')},
            h('header',{},h('strong',{},T('Save or send this frame')),h('button',{type:'button',class:'ff-share-close',onclick:close,'aria-label':T('Close')},'✕')),
            h('img',{class:'ff-share-preview',src:image,alt:T('Captured frame')}),h('div',{class:'ff-share-size'},canvas.width+' × '+canvas.height),
            h('div',{class:'ff-share-actions'},h('label',{},T('File name'),nameInput),saveButton,
                h('label',{},T('Destination'),target),sendButton),
            h('p',{class:'ff-share-note'},T('Video and native references fill the next free slot. Existing pictures are kept.')),status);
        const back=h('div',{class:'fx-modal-back fx-frame-share hebrew-ui-skip',dir:F.hebrew()?'rtl':'ltr',onpointerdown:e=>{if(e.target===back)close();}},box);
        activeClose=close;
        (document.fullscreenElement||host||document.body).append(back);document.addEventListener('keydown',keys,true);saveButton.focus();
        return back;
    }
    window.forgeFrameShare={open,send,save,nextSlot,destinations,filename};
})();

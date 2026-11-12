/* Fill missing native image progress without interfering with Forge's queue or its working bars. */
(function(){
    'use strict';
    function install(){
        const original=window.requestProgress;
        if(typeof original!=='function'||original.frogFeedback)return;
        function sources(gallery){return new Set([...gallery?.querySelectorAll('img')||[]].filter(im=>!im.closest('.livePreview')).map(im=>im.currentSrc||im.src).filter(Boolean));}
        function wrapped(id,container,gallery,atEnd,onProgress,inactivityTimeout){
            if(!gallery||!/^.*(?:txt2img|img2img)_gallery$/.test(gallery.id))return original.apply(this,arguments);
            const before=sources(gallery),began=Date.now(),key='image:'+id;
            const force=document.body.classList.contains('fibo2-active')&&!document.body.classList.contains('fibo2-flux3');
            let shown=false,ended=false,last=null;
            const emit=(state,p,hidden=false)=>window.forgeGenerationFeedback?.({id:key,kind:'image',scope:'page',gallery:gallery.id,state,progress:p,hidden});
            if(force||window.opts?.show_progressbar===false){shown=true;emit('waiting',null);}
            const progress=res=>{
                last=res;
                const bar=container.parentNode.querySelector('.progressDiv');
                const normal=bar&&getComputedStyle(bar).display!=='none'&&bar.getBoundingClientRect().height>0&&res.progress>0;
                if(force||!normal&&(shown||Date.now()-began>1800)){
                    shown=true;emit(res.active?'running':'waiting',res.active?(res.progress||0):null);
                }else if(shown)emit(res.active?'running':'waiting',res.progress||0,true);
                onProgress?.(res);
            };
            const end=()=>{
                if(ended)return;ended=true;
                atEnd();
                if(!shown)return;
                // atEnd is also used for cancellation and network failures. Only a new gallery
                // result proves success; an old picture or a live preview must never make a croak.
                let tries=0;
                const finish=()=>{
                    const changed=[...sources(gallery)].some(src=>!before.has(src));
                    if(changed){emit('done',1);return;}
                    if(++tries<20){setTimeout(finish,250);return;}
                    emit('cancelled',last?.progress||0);
                };
                setTimeout(finish,50);
            };
            return original.call(this,id,container,gallery,end,progress,inactivityTimeout);
        }
        wrapped.frogFeedback=true;window.requestProgress=wrapped;
    }
    if(typeof onUiLoaded==='function')onUiLoaded(install);
})();

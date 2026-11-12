'use strict';
const assert=require('assert'),fs=require('fs'),path=require('path'),vm=require('vm');
const comps=[],calls=[];let serial=0;
function add(elem,type='image',value=null){const c={id:++serial,type,props:{elem_id:elem,value},instance:{$set(p){calls.push({elem,props:p});}}};comps.push(c);return c;}
for(const id of ['tabs','mode_img2img','vs_mode_tabs'])add(id,'tabs');
for(const id of ['img2img_image','img2maskimg',...[1,2,3,4].map(n=>'txt2img_fibo2_reference_'+n),...[1,2,3,4,5,6].map(n=>'vs_picture'+n),...[1,2,3,4].map(n=>'vs_ingredient'+n)])add(id);
add('txt2img_fibo2_controls','accordion');add('setting_sd_model_checkpoint','dropdown','FIBO Dev');
const count=add('vs_picture_count','number',1),rows=[add('row1','row'),add('row2','row'),add('row3','row')];
const byId=id=>comps.find(c=>c.props.elem_id===id);
const ctx={console,gradio_config:{components:comps,dependencies:[{inputs:[count.id],outputs:[...Array.from({length:6},(_,i)=>byId('vs_picture'+(i+1)).id),...rows.map(r=>r.id)],js:'videoStudioPictureLayout'}]},
    ForgeStudio:{T:x=>x,UI:{root:{classList:{contains:()=>false}}}},
    document:{fullscreenElement:null,getElementById:()=>({scrollIntoView(){}})},requestAnimationFrame:f=>f(),
    fetch:async url=>({ok:true,json:async()=>url.includes('fibo2')?{models:[{title:'FIBO Dev',variant:'dev'}]}:url.includes('flux3')?{titles:[]}:{sd_model_checkpoint:'FIBO Dev'}}),
    videoStudioPictureLayout:n=>[...Array.from({length:6},(_,i)=>({visible:i<n,label:'Picture '+(i+1)})),...rows.map((_,i)=>({visible:i*2<n}))]};
ctx.window=ctx;vm.createContext(ctx);vm.runInContext(fs.readFileSync(path.join(__dirname,'../javascript/forge-studio-o-frame-share.js'),'utf8'),ctx);
const share=ctx.forgeFrameShare,img=n=>'data:image/png;base64,frame'+n;
let passed=0;async function test(name,fn){await fn();passed++;console.log('ok '+name);}
(async()=>{
    await test('concurrent frame sends append in order and reveal the required picture rows',async()=>{
        const results=await Promise.all([1,2,3].map(n=>share.send(img(n),'i2v')));
        assert.deepEqual(results.map(r=>r.slot),[1,2,3]);assert.equal(count.props.value,3);
        assert.equal(byId('vs_picture1').props.value,img(1));assert.equal(byId('vs_picture3').props.value,img(3));assert.equal(rows[1].props.visible,true);
    });
    await test('filled slots are never overwritten and a cleared slot can be reused',async()=>{
        for(let n=1;n<=4;n++)await share.send(img(n),'ingredients');
        await assert.rejects(()=>share.send(img(5),'ingredients'),/slots are full/);
        assert.equal(byId('vs_ingredient1').props.value,img(1));
        byId('vs_ingredient2').props.value=null;assert.equal((await share.send(img(8),'ingredients')).slot,2);
    });
    await test('native references append without leaking into Video Studio',async()=>{
        await share.send(img(1),'native');await share.send(img(2),'native');assert.equal(byId('txt2img_fibo2_reference_2').props.value,img(2));
        assert.equal(byId('vs_ingredient2').props.value,img(8));assert.equal(byId('tabs').props.selected,'txt2img');
    });
    await test('img2img and inpaint open their proper native sub-tabs',async()=>{
        await share.send(img(3),'inpaint');assert.equal(byId('mode_img2img').props.selected,'inpaint');
        await share.send(img(4),'img2img');assert.equal(byId('mode_img2img').props.selected,'img2img');assert.equal(byId('img2img_image').props.value,img(4));
    });
    await test('invalid input is rejected before any component is changed',async()=>{
        const before=calls.length;await assert.rejects(()=>share.send('https://remote/image.png','i2v'));assert.equal(calls.length,before);
    });
    console.log(`${passed} passed`);
})().catch(e=>{console.error(e);process.exitCode=1;});

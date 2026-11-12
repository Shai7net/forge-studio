const fs = require('fs'), vm = require('vm'), assert = require('assert');
const path = require('path');
const components = [];
function add(id, value, type='textbox', choices=[]) {
    const c = {type, props:{elem_id:id,value,choices,interactive:true}};
    c.instance = {$set: p => Object.assign(c.props,p)}; components.push(c); return c;
}
const model = add('setting_sd_model_checkpoint','SD');
for(const tab of ['txt2img','img2img']) {
    add(tab+'_styles',[], 'dropdown', ['LCM shortcut','Art']);
    add(tab+'_prompt','A subject'); add(tab+'_neg_prompt','');
    add(tab+'_sampling','Euler','dropdown',['Euler','LCM']); add(tab+'_steps',24,'slider'); add(tab+'_cfg_scale',7,'slider');
    add(tab+'_controlnet_ControlNet-0_controlnet_enable_checkbox',true,'checkbox');
    add(tab+'-ad-enable',true,'checkbox'); add(tab+'-dance-enable',true,'checkbox');
}
let loaded;
const window = {gradio_config:{components}, tabPresets:{settled:async()=>{}}};
const catalog = {loras:[{name:'LCM_SD15',alias:'LCM_SD15',family:'sd1'}],styles:{'LCM shortcut':{prompt:'<lora:LCM_SD15:1>'}, Art:{prompt:'watercolor'}}};
const ctx = {window, document:{getElementById:()=>null,querySelectorAll:()=>[]},
    localStorage:{getItem:()=>null,setItem:()=>{}},console,Map,Set,Date,JSON,Array,Number,String,
    setTimeout:()=>{},setInterval:()=>{},onUiLoaded:fn=>{loaded=fn;},onUiUpdate:()=>{},
    fetch:async url=>({ok:true,json:async()=>url.includes('/family')?{family:url.endsWith('Flux')?'flux':url.endsWith('XL')?'sdxl':'sd1'}:catalog})};
vm.createContext(ctx); vm.runInContext(fs.readFileSync(path.join(__dirname,'../javascript/workflow-assists.js'),'utf8'),ctx);
(async()=>{
    await loaded(); const api=window.forgeWorkflow;
    api.set('txt2img_styles',['LCM shortcut','Art']); await api.tick();
    assert.equal(api.get('txt2img_sampling'),'LCM'); assert.equal(api.get('txt2img_steps'),8); assert.equal(api.get('txt2img_cfg_scale'),1.5);
    model.props.value='Flux'; await api.tick();
    assert.deepEqual(Array.from(api.get('txt2img_styles')),['Art']);
    assert.equal(api.get('txt2img-ad-enable'),false); assert.equal(api.get('txt2img-dance-enable'),false);
    assert.equal(api.get('txt2img_controlnet_ControlNet-0_controlnet_enable_checkbox'),false);
    api.set('txt2img_prompt','A different subject');
    model.props.value='SD'; await api.tick();
    assert.equal(api.get('txt2img-ad-enable'),true); assert.equal(api.get('txt2img-dance-enable'),true);
    assert.equal(api.get('txt2img_controlnet_ControlNet-0_controlnet_enable_checkbox'),true);
    assert.deepEqual(Array.from(api.get('txt2img_styles')),['Art','LCM shortcut']);
    assert.equal(api.get('txt2img_prompt'),'A different subject');
    api.set('txt2img_styles',['Art']); await api.tick();
    assert.equal(api.get('txt2img_sampling'),'Euler'); assert.equal(api.get('txt2img_steps'),24);
    catalog.loras.push({name:'LCM_XL',alias:'LCM_XL',family:'sdxl'}); catalog.styles['XL shortcut']={prompt:'<lora:LCM_XL:1>'};
    model.props.value='XL'; await api.tick(); api.set('txt2img_styles',['XL shortcut']); await api.tick();
    assert.equal(api.get('txt2img_cfg_scale'),2); assert.equal(api.get('txt2img_steps'),8);
    console.log('workflow JS: LCM defaults, native pause, SD restore, prompt preservation, XL CFG passed');
})().catch(e=>{console.error(e);process.exitCode=1;});

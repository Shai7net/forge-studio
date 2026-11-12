// FIBO checkpoints use Forge's normal selector. Their settings (Dev: Euler, 50 steps, CFG 5; Edit Turbo: 4 steps,
// CFG 1; 1024x1024 in place of an SD 1.5 size) are set on both generation tabs, and your own come back with an
// ordinary checkpoint, by forge-tab-presets' model-defaults.js: one keeper for FLUX 3 and FIBO, so moving between
// them never hands one model's values to another. The native panel follows either FIBO or FLUX 3.
// The VAE list is left alone: "Automatic" is right (the worker always uses the FLUX.2 VAE), and setting it made
// Forge load the FLUX.2 VAE into the SD model kept behind FIBO.
(function() {
    let models = [], fluxTitles = [], family = null;
    const writers = {fibo: {}, flux: {txt2img: 'auto', img2img: 'off'}};

    async function titles() {
        const r = await fetch('./fibo2/checkpoints');
        const data = await r.json();
        models = data.models || [];
        try { const flux = await fetch('./flux3/checkpoints'); fluxTitles = (await flux.json()).titles || []; } catch (_) {}
        return models.map(function(m) { return m.title; });
    }

    function model(title) {
        const base = String(title || '').split(' [')[0];
        return models.find(function(m) { return m.title === title || m.title.split(' [')[0] === base; }) || null;
    }

    function component(id, props) {
        const c = window.gradio_config?.components?.find(c => c.props?.elem_id === id);
        if (!c) return null;
        if (props) { Object.assign(c.props, props); c.instance?.$set(props); }
        return c;
    }

    function panel(show, title) {
        const flux = fluxTitles.some(t => t.split(' [')[0] === String(title || '').split(' [')[0]);
        const next = flux ? 'flux' : show ? 'fibo' : null;
        document.body.classList.toggle('fibo2-active', Boolean(next));
        document.body.classList.toggle('fibo2-flux3', flux);
        for (const tab of ['txt2img', 'img2img']) {
            const id = tab + '_fibo2_writer';
            if (family && next !== family) writers[family][tab] = component(id)?.props.value;
            if (next && next !== family) component(id, {value: writers[next][tab] || 'off', value_is_output: true});
            component(tab + '_fibo2_controls', {label: flux ? 'FLUX 3 · native controls' : 'FIBO 2.0 · native controls'});
            component(id, {label: flux ? 'Local Qwen3-VL prompt writer · any language → English' : 'Local Qwen3-VL prompt writer · any language → VGL'});
        }
        family = next;
    }

    onUiLoaded(function() {
        panel(false);
        if (!window.modelDefaults) return;
        window.modelDefaults.register({
            name: 'FIBO 2.0',
            titles: titles,
            preset: function(title) {
                const m = model(title);
                return m ? m.defaults : {sampler: 'Euler', steps: 50, cfg: 5, width: 1024, height: 1024};
            },
            enabled: function() {
                return !(window.opts && window.opts.fibo2_auto_defaults === false);
            },
            shown: panel,
        });
    });
})();

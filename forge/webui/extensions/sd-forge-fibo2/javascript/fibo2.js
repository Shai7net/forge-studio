// FIBO checkpoints use Forge's normal selector. Their settings (Dev: Euler, 50 steps, CFG 5; Edit Turbo: 4 steps,
// CFG 1; 1024x1024 in place of an SD 1.5 size) are set on both generation tabs, and your own come back with an
// ordinary checkpoint, by forge-tab-presets' model-defaults.js: one keeper for FLUX 3 and FIBO, so moving between
// them never hands one model's values to another. The FIBO panel shows only while a FIBO model is chosen.
// The VAE list is left alone: "Automatic" is right (the worker always uses the FLUX.2 VAE), and setting it made
// Forge load the FLUX.2 VAE into the SD model kept behind FIBO.
(function() {
    let models = [];

    async function titles() {
        const r = await fetch('./fibo2/checkpoints');
        const data = await r.json();
        models = data.models || [];
        return models.map(function(m) { return m.title; });
    }

    function model(title) {
        const base = String(title || '').split(' [')[0];
        return models.find(function(m) { return m.title === title || m.title.split(' [')[0] === base; }) || null;
    }

    function panel(show) {
        document.body.classList.toggle('fibo2-active', Boolean(show));
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

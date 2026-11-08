// FLUX 3: when a FLUX 3 checkpoint is chosen, set the settings it is made for (Euler, 16 steps, CFG 1, and
// 1024x1024 when the size is still SD 1.5 sized) on txt2img and img2img; when another checkpoint is chosen again,
// put back what was there before. The keeping and giving back is done by forge-tab-presets' model-defaults.js, for
// every model that has settings of its own (FLUX 3 and FIBO 2.0 used to do it each on their own, and handed each
// other's values to SD 1.5).
(function() {
    let defaults = {steps: 16, cfg: 1.0, sampler: 'Euler'};

    async function titles() {
        const r = await fetch('./flux3/checkpoints');
        const j = await r.json();
        defaults = j.defaults || defaults;
        return j.titles || [];
    }

    onUiLoaded(function() {
        if (!window.modelDefaults) return;
        window.modelDefaults.register({
            name: 'FLUX 3',
            titles: titles,
            preset: function() {
                return {sampler: defaults.sampler, steps: defaults.steps, cfg: defaults.cfg, width: 1024, height: 1024};
            },
            enabled: function() {
                return !(window.opts && window.opts.flux3_auto_defaults === false);
            },
        });
    });
})();

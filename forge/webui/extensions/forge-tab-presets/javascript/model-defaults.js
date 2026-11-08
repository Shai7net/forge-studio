// Settings that belong to a checkpoint (FLUX 3, FIBO 2.0...). When one of these models is chosen, its sampler,
// steps, CFG and size are put on txt2img and img2img; when an ordinary checkpoint is chosen again, the values you had
// come back.
//
// One keeper for all of them (2026-11-05). FLUX 3 and FIBO each used to keep "your" values on their own, so going
// FIBO -> FLUX 3 -> SD 1.5 gave FLUX 3 the values FIBO had restored and SD 1.5 FIBO's 50 steps, CFG 5 and 1024x1024.
// Here your values are taken once, when you leave an ordinary checkpoint, kept in localStorage (a page reload in
// between does not lose them), and given back when you come to an ordinary checkpoint. A checkpoint loaded by a
// preset (Tab Presets) comes with the preset's own values: those are left as they are.
//
// An extension registers its models once the page is loaded:
//   window.modelDefaults.register({
//       name: 'FLUX 3',
//       titles: async () => [checkpoint titles],       // asked again when an unknown title is chosen
//       preset: (title) => ({sampler, steps, cfg, width, height}),   // the size only replaces an SD 1.5 size
//       enabled: () => true,                           // its "set automatically" option
//       shown: (active, title) => {},                  // optional: called on every change of checkpoint
//   });
(function() {
    const TABS = ['txt2img', 'img2img'];
    const FIELDS = ['sampling', 'steps', 'cfg_scale', 'width', 'height'];
    const KEY = 'forge-model-defaults';
    const PRESET_WINDOW = 5000;  // ms after Tab Presets sets a checkpoint during which a change is the preset's
    const families = [];
    let last = null;
    let started = false;
    let refreshing = null;
    let settledAt = 0;
    const SETTLE = 6000;  // ms after the page is ready during which the checkpoint list may still catch up

    function comp(id) {
        const config = window.gradio_config;
        if (!config || !config.components) return null;
        return config.components.find(function(c) { return c.props && c.props.elem_id === id; }) || null;
    }

    function get(id) {
        const c = comp(id);
        return c ? c.props.value : undefined;
    }

    // the way Gradio writes a value that came from the server (the page's record, then the component)
    function set(id, value) {
        const c = comp(id);
        if (!c || value === undefined || value === null) return;
        c.props.value = value;
        c.props.value_is_output = true;
        if (c.instance && typeof c.instance.$set === 'function') c.instance.$set({value: value, value_is_output: true});
    }

    function stored() {
        try {
            return JSON.parse(localStorage.getItem(KEY) || 'null');
        } catch (e) {
            return null;
        }
    }

    function store(value) {
        try {
            if (value) localStorage.setItem(KEY, JSON.stringify(value));
            else localStorage.removeItem(KEY);
        } catch (e) {
            // no storage: the values live as long as the page
        }
        store.memory = value;
    }

    function yours() {
        return stored() || store.memory || null;
    }

    function capture() {
        const values = {};
        for (const tab of TABS) {
            values[tab] = {};
            for (const f of FIELDS) values[tab][f] = get(tab + '_' + f);
        }
        return values;
    }

    function base(title) {
        return title ? String(title).split(' [')[0] : '';
    }

    function match(title) {
        if (!title) return null;
        for (const family of families) {
            const titles = family.known || [];
            if (titles.indexOf(title) >= 0 || titles.some(function(t) { return base(t) === base(title); })) return family;
        }
        return null;
    }

    async function refresh() {
        if (refreshing) return refreshing;
        refreshing = Promise.all(families.map(async function(family) {
            try {
                family.known = (await family.titles()) || [];
            } catch (e) {
                family.known = family.known || [];
            }
        })).finally(function() {
            refreshing = null;
        });
        return refreshing;
    }

    function apply(preset) {
        for (const tab of TABS) {
            set(tab + '_sampling', preset.sampler);
            set(tab + '_steps', preset.steps);
            set(tab + '_cfg_scale', preset.cfg);
            const w = get(tab + '_width'), h = get(tab + '_height');
            if (preset.width && w !== undefined && h !== undefined && Math.max(w, h) < 768) {
                set(tab + '_width', preset.width);
                set(tab + '_height', preset.height || preset.width);
            }
        }
    }

    function giveBack() {
        const values = yours();
        if (!values) return;
        for (const tab of TABS) {
            for (const f in values[tab] || {}) set(tab + '_' + f, values[tab][f]);
        }
        store(null);
    }

    function fromPreset() {
        const at = window.tabPresets && window.tabPresets.modelSetAt;
        return Boolean(at && Date.now() - at < PRESET_WINDOW);
    }

    function on(family) {
        return family && (!family.enabled || family.enabled() !== false);
    }

    // what was done on each change of checkpoint, newest last (for whoever looks into it: window.modelDefaults.log)
    const log = [];

    function note(action, before, title) {
        log.push({time: new Date().toLocaleTimeString(), from: before, to: title, action: action});
        if (log.length > 30) log.shift();
    }

    // One look at a time: at page load the checkpoint changes twice in a moment (see `settling`), and two looks
    // overlapping across the wait for the model lists left FLUX 3's values on a FIBO checkpoint.
    let looking = false;

    async function tick() {
        if (looking) return;
        looking = true;
        try {
            await look();
        } finally {
            looking = false;
        }
    }

    async function look() {
        const title = get('setting_sd_model_checkpoint');
        if (title === undefined || title === last) return;
        const before = last;
        if (!match(title)) {
            await refresh();
            if (get('setting_sd_model_checkpoint') !== title) return;  // changed again meanwhile: the next look
        }
        last = title;
        const now = match(title);
        const was = before === null ? null : match(before);
        for (const family of families) {
            if (family.shown) family.shown(family === now, title);
        }
        if (fromPreset()) {
            // the preset set these values for this checkpoint itself
            if (!now) store(null);
            note('preset', before, title);
            return;
        }
        // The page first shows the checkpoint Forge started with, and the current one a moment later: changes in
        // the first seconds are the page settling, not you choosing (counted as a choice, they wiped the values kept
        // before a reload).
        const settling = before === null || Date.now() < settledAt;
        if (now) {
            if (!on(now)) return note('off', before, title);
            // leaving an ordinary checkpoint: take your values (moving between these models keeps them); at page
            // load, values kept before the reload stay
            if (!was && (!settling || !yours())) store(capture());
            apply(now.preset(title));
            note('apply ' + now.name, before, title);
        } else if (was) {
            giveBack();
            note('give back', before, title);
        }
    }

    window.modelDefaults = {
        register: function(family) {
            families.push(family);
            family.known = [];
            last = null;  // look at the current checkpoint again with this family known
            if (started) refresh().then(tick);
        },
        refresh: refresh,
        kept: yours,
        log: log,
    };

    onUiLoaded(function() {
        // after every extension's onUiLoaded, so all families are registered
        setTimeout(function() {
            started = true;
            settledAt = Date.now() + SETTLE;
            refresh().then(tick);
            setInterval(tick, 500);
        }, 0);
    });
})();

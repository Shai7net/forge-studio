/* Simulation 360 - the page: the stage with the 3D view, the strip of worlds, and the panels.
 * Everything is drawn here, inside the one empty block the tab holds; nothing of Gradio's is used.
 */
(function () {
    'use strict';
    const Core = window.Sim360Core, Solid = window.Sim360Solid, S = window.Sim360, E = S.engine, T = S.T;

    const UI = S.ui = {
        root: null, tab: 'world', tool: 'look', subtract: false, brush: 34, map: true, mapArrows: false,
        drag: null, arrivalPick: null, started: false, lastFrame: 0, exportResult: null, message: null,
        record: { source: 'tour', size: '1920x1080', width: 1920, height: 1080, fps: 30, kind: 'mp4-h264', quality: 16, supersample: 1, name: 'tour', sound: true, weather_sound: true, subtitles: 'file', fov_scale: 1, loudness: true, duck: 0 },
    };

    /* ---- building blocks -------------------------------------------------------------------------------- */

    function h(tag, props, ...children) {
        const el = document.createElement(tag);
        for (const [key, value] of Object.entries(props || {})) {
            if (value == null || value === false) continue;
            if (key === 'class') el.className = value;
            else if (key === 't') { el.dataset.t = value; el.textContent = T(value); }
            else if (key === 'tt') { el.dataset.tt = value; el.title = T(value); }
            else if (key === 'tp') { el.dataset.tp = value; el.placeholder = T(value); }
            else if (key === 'on') for (const [event, fn] of Object.entries(value)) el.addEventListener(event, fn);
            else if (key === 'style') el.style.cssText = value;
            else if (key in el && key !== 'list') el[key] = value;
            else el.setAttribute(key, value);
        }
        for (const child of children.flat()) if (child != null && child !== false) el.append(child.nodeType ? child : document.createTextNode(child));
        return el;
    }

    const button = (text, onClick, extra) => h('button', Object.assign({ type: 'button', class: 's360-btn', t: text, on: { click: guarded(onClick) } }, extra || {}));
    const field = (label, control, hint) => h('label', { class: 's360-field', tt: hint }, h('span', { class: 's360-label', t: label }), control);
    const row = (...children) => h('div', { class: 's360-row' }, ...children);
    const section = (title, open, ...children) => h('details', { class: 's360-section', open: !!open }, h('summary', { t: title }), h('div', { class: 's360-section-body' }, ...children));

    /* a button's work may fail: the reason is shown, never swallowed */
    function guarded(fn) {
        return async event => {
            try { await fn(event); } catch (e) { if (String(e.message || e) !== 'cancelled') say(T(String(e.message || e)), 'error'); else say(T('cancelled'), 'warn'); console.warn('[Simulation 360]', e); }
        };
    }

    function number(object, key, options, after) {
        options = options || {};
        const input = h('input', { type: 'number', class: 's360-input', value: object[key], min: options.min, max: options.max, step: options.step || 1 });
        input.addEventListener('change', () => {
            let value = parseFloat(input.value);
            if (!isFinite(value)) value = options.fallback != null ? options.fallback : 0;
            if (options.min != null) value = Math.max(options.min, value);
            if (options.max != null) value = Math.min(options.max, value);
            object[key] = value; input.value = value; E.touch(); if (after) after(value);
        });
        return input;
    }

    function slider(object, key, options, after) {
        const shown = h('span', { class: 's360-value' }, String(object[key]));
        const input = h('input', { type: 'range', class: 's360-range', min: options.min, max: options.max, step: options.step || 1, value: object[key] });
        input.addEventListener('input', () => { object[key] = parseFloat(input.value); shown.textContent = input.value; E.touch(); if (after) after(object[key]); });
        return h('span', { class: 's360-slider' }, input, shown);
    }

    function text(object, key, options) {
        options = options || {};
        const input = options.area ? h('textarea', { class: 's360-input s360-area', rows: options.rows || 3, tp: options.placeholder, dir: 'auto' })
            : h('input', { type: 'text', class: 's360-input', tp: options.placeholder, dir: 'auto' });
        input.value = object[key] == null ? '' : object[key];
        input.addEventListener('input', () => { object[key] = input.value; E.touch(); if (options.after) options.after(input.value); });
        return input;
    }

    function check(object, key, label, hint, after) {
        const input = h('input', { type: 'checkbox', class: 's360-check', checked: !!object[key] });
        input.addEventListener('change', () => { object[key] = input.checked; E.touch(); if (after) after(input.checked); });
        return h('label', { class: 's360-checkline', tt: hint }, input, h('span', { t: label }));
    }

    /* options: [[value, label, translate?]] */
    function select(object, key, options, after) {
        const input = h('select', { class: 's360-input' });
        // a choice that is not translated (a size, a file name) keeps its left-to-right order in Hebrew too
        for (const option of options) input.append(h('option', option[2] === false ? { value: option[0] } : { value: option[0], t: option[1] }, option[2] === false ? '‎' + option[1] : null));
        input.value = object[key] == null ? '' : object[key];
        input.addEventListener('change', () => { object[key] = input.value; E.touch(); if (after) after(input.value); });
        return input;
    }

    function relabel(root) {
        for (const el of root.querySelectorAll('[data-t]')) el.textContent = T(el.dataset.t);
        for (const el of root.querySelectorAll('[data-tt]')) el.title = T(el.dataset.tt);
        for (const el of root.querySelectorAll('[data-tp]')) el.placeholder = T(el.dataset.tp);
    }

    let messageTimer = null;
    function say(textShown, kind) {
        UI.message = { text: textShown, kind: kind || 'info' };
        if (UI.status) { UI.status.textContent = textShown; UI.status.dataset.kind = kind || 'info'; }
        clearTimeout(messageTimer);
        messageTimer = setTimeout(() => { if (UI.status) UI.status.textContent = ''; }, kind === 'error' ? 15000 : 6000);
    }
    E.on('status', m => say(T(m.text), m.kind));

    /* ---- the frame of the page -------------------------------------------------------------------------- */

    /* the world as wide and tall as the page, the panel under it (remembered in this browser) */
    function bigView(on) {
        UI.root.classList.toggle('s360-wide', on);
        try { localStorage.setItem('sim360-wide', on ? '1' : '0'); } catch (e) { /* no storage */ }
    }

    function build(mount) {
        UI.gl = h('canvas', { class: 's360-gl' });
        UI.over = h('canvas', { class: 's360-over' });
        UI.hud = h('div', { class: 's360-hud' });
        UI.banner = h('div', { class: 's360-banner' });
        UI.jobsBox = h('div', { class: 's360-jobs' });
        UI.tools = h('div', { class: 's360-tools' });
        UI.stage = h('div', { class: 's360-stage', tabIndex: 0 }, UI.gl, UI.over, UI.hud, UI.tools, UI.banner, UI.jobsBox);
        UI.strip = h('div', { class: 's360-strip' });
        UI.projectSelect = h('select', { class: 's360-input s360-project', on: { change: guarded(() => E.open(UI.projectSelect.value)) } });
        UI.status = h('span', { class: 's360-status' });
        UI.saved = h('span', { class: 's360-saved' });
        UI.tabsBar = h('div', { class: 's360-tabs' });
        UI.panel = h('div', { class: 's360-panel' });
        const bar = h('div', { class: 's360-bar' },
            h('span', { class: 's360-title', t: 'Simulation 360' }),
            UI.projectSelect,
            button('New project', newProject),
            h('button', { type: 'button', class: 's360-btn s360-icon', tt: 'Open the project folder', on: { click: guarded(() => E.call('/open-folder', { body: { project: E.name || '' } })) } }, '📂'),
            UI.saved,
            UI.status,
            h('button', { type: 'button', class: 's360-btn s360-icon', tt: 'Help', on: { click: showHelp } }, '?'),
            h('button', { type: 'button', class: 's360-btn s360-icon', tt: 'A big view: the world as wide and tall as the page, the panel under it', on: { click: () => bigView(!UI.root.classList.contains('s360-wide')) } }, '↕'),
            h('button', { type: 'button', class: 's360-btn s360-icon', tt: 'Full screen', on: { click: fullScreen } }, '⛶'));
        UI.root = h('div', { class: 's360 hebrew-ui-skip' }, bar,
            h('div', { class: 's360-body' },
                h('div', { class: 's360-left' }, UI.stage, UI.strip),
                h('div', { class: 's360-side' }, UI.tabsBar, UI.panel)));
        mount.append(UI.root);
        try { if (localStorage.getItem('sim360-wide') === '1') UI.root.classList.add('s360-wide'); } catch (e) { /* no storage */ }
        UI.octx = UI.over.getContext('2d');
        wireStage();
        language();
        new MutationObserver(language).observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });
    }

    function language() {
        const hebrew = S.hebrew();
        const code = window.hebrewUi && window.hebrewUi.language ? window.hebrewUi.language() : (hebrew ? 'he' : 'en');
        if (UI.languageCode === code && UI.languageDone) return;
        UI.languageDone = true;
        UI.languageCode = code;
        UI.root.classList.toggle('s360-he', hebrew);
        relabel(UI.root);
        renderAll();
    }

    async function newProject() {
        const name = prompt(T('Name of the new project:'), T('My worlds'));
        if (name && name.trim()) await E.createProject(name.trim());
    }

    async function deleteProject() {
        if (E.name && confirm(T('Delete the project "{0}"? (It is moved to the _deleted folder, not erased.)', E.name))) await E.deleteProject();
    }

    function fullScreen() {
        if (document.fullscreenElement) document.exitFullscreen();
        else UI.stage.requestFullscreen().catch(() => {});
        UI.stage.focus();
    }

    function showHelp() {
        const lines = S.helpText();
        const box = h('div', { class: 's360-help' }, h('h3', { t: 'How it works' }), h('ol', {}, lines.map(l => h('li', { dir: 'auto' }, l))),
            h('p', { class: 's360-note', t: 'Drag: look · Wheel: zoom · W A S D: move · Q E: down, up · Shift: fast · Space: enter a portal · Backspace: back · F: full screen' }),
            button('Close', () => box.remove()));
        UI.stage.append(box);
    }

    /* ---- panels ----------------------------------------------------------------------------------------- */

    const TABS = [['world', 'World'], ['portals', 'Portals'], ['character', 'Objects'], ['dialogue', 'Dialogue'], ['route', 'Route'], ['record', 'Record'], ['project', 'Project']];

    function renderTabs() {
        UI.tabsBar.replaceChildren(...TABS.map(([id, label]) => h('button', {
            type: 'button', class: 's360-tab' + (UI.tab === id ? ' s360-on' : ''), t: label,
            on: { click: () => { UI.tab = id; if (id !== 'portals' && id !== 'character') setTool('look'); renderTabs(); renderPanel(); } },
        })));
    }

    function renderAll() {
        if (!UI.root) return;
        renderProjects(); renderTabs(); renderStrip(); renderPanel(); renderTools(); renderJobs(); renderBanner(); renderSaved();
    }

    function renderProjects() {
        const list = E.projects || [];
        UI.projectSelect.replaceChildren(...list.map(p => h('option', { value: p.name }, p.name)));
        if (E.name) UI.projectSelect.value = E.name;
        UI.projectSelect.style.display = list.length ? '' : 'none';
    }

    function renderPanel() {
        if (!UI.panel) return;
        const keep = UI.panel.scrollTop;
        let body;
        if (!E.doc) body = [h('p', { class: 's360-note', t: 'No project yet. Create one to begin.' }), button('Create a project', newProject, { class: 's360-btn s360-primary' })];
        else if (UI.tab === 'world') body = worldPanel();
        else if (UI.tab === 'portals') body = portalsPanel();
        else if (UI.tab === 'character') body = characterPanel();
        else if (UI.tab === 'dialogue') body = dialoguePanel();
        else if (UI.tab === 'route') body = routePanel();
        else if (UI.tab === 'project') body = projectPanel();
        else body = recordPanel();
        UI.panel.replaceChildren(...body.filter(Boolean));
        UI.panel.scrollTop = keep;
    }

    const SIZES = ['1024x512', '1536x768', '2048x1024'];

    function worldPanel() {
        const world = E.world();
        if (!world) return [h('p', { class: 's360-note', t: 'Press + below to add your first world.' })];
        const gen = world.gen = Object.assign(E.defaults(), world.gen || {});
        gen.hires = Object.assign(E.defaults().hires, gen.hires || {});
        gen.animatediff = Object.assign(E.defaults().animatediff, gen.animatediff || {});
        const o = E.options, sizeKey = `${gen.width}x${gen.height}`;
        const size = { value: SIZES.includes(sizeKey) ? sizeKey : 'custom' };
        const custom = row(field('Width', number(gen, 'width', { min: 256, max: 4096, step: 64 })), field('Height', number(gen, 'height', { min: 128, max: 2048, step: 64 })));
        custom.style.display = size.value === 'custom' ? '' : 'none';
        const sizeSelect = select(size, 'value', SIZES.map(s => [s, s.replace('x', ' × '), false]).concat([['custom', 'Custom']]), value => {
            if (value !== 'custom') { const [w, hh] = value.split('x').map(Number); gen.width = w; gen.height = hh; E.touch(); }
            custom.style.display = value === 'custom' ? '' : 'none';
        });
        const steps = number(gen, 'steps', { min: 1, max: 150 }), cfg = number(gen, 'cfg', { min: 0, max: 30, step: 0.5 });
        const sampler = select(gen, 'sampler', o.samplers.map(s => [s, s, false]));
        const lcm = check(gen, 'lcm', 'LCM (fast)', 'Adds the LCM LoRA and sets the LCM sampler, 7 steps, CFG 1.5', on => {
            if (on) { gen.sampler = 'LCM'; gen.steps = 7; gen.cfg = 1.5; } else { gen.sampler = o.samplers.includes('DPM++ 2M Karras') ? 'DPM++ 2M Karras' : o.samplers[0]; gen.steps = 20; gen.cfg = 7; }
            steps.value = gen.steps; cfg.value = gen.cfg; sampler.value = gen.sampler; E.touch();
        });
        const seed = number(gen, 'seed', { min: -1, max: 4294967295, fallback: -1 });
        const upscalers = o.latent_upscalers.concat(o.upscalers.filter(u => u !== 'None')).map(u => [u, u, false]);
        const isVideo = world.kind === 'video', busy = E.jobs.size > 0;
        const weather = world.weather = Object.assign({ kind: '', amount: 0.6, wind: 0.15, mist: 0 }, world.weather || {});
        return [
            field('Name', text(world, 'name', { after: () => renderStrip() })),
            field('Prompt', text(gen, 'prompt', { area: true, rows: 4, placeholder: 'Describe the world: a place, its light, its style…' })),
            row(o.lora_360 ? check(gen, 'lora360', '360 LoRA', 'Adds the panorama LoRA and its trigger word to the prompt') : null, o.lora_lcm ? lcm : null),
            section('Negative prompt', !!gen.negative, text(gen, 'negative', { area: true, rows: 2 })),
            row(field('Size', sizeSelect), field('Steps', steps), field('CFG', cfg)),
            custom,
            row(field('Sampler', sampler), field('Seed', h('span', { class: 's360-seed' }, seed,
                h('button', { type: 'button', class: 's360-btn s360-icon', tt: 'Random seed', on: { click: () => { gen.seed = -1; seed.value = -1; E.touch(); } } }, '🎲'),
                h('button', { type: 'button', class: 's360-btn s360-icon', tt: 'Use the seed of the present picture', disabled: world.seed == null, on: { click: () => { gen.seed = world.seed; seed.value = world.seed; E.touch(); } } }, '♻')))),
            field('Checkpoint', select(gen, 'checkpoint', [['', 'The one loaded in Forge']].concat(o.checkpoints.map(c => [c, c.replace(/\s*\[[0-9a-f]+\]$/, ''), false])))),
            section('Seamless ring (Tile X)', false,
                check(gen, 'tile_x', 'Tile X', 'The left edge continues the right edge, so the world has no seam'),
                field('From step', number(gen, 'tile_start', { min: -1, max: 150, fallback: -1 }), '-1 = automatic: the first steps lay the scene out freely, the rest close the ring'),
                check({ on: !!o.tile_everything }, 'on', 'Also for everything else Forge draws', 'txt2img, img2img and Deforum draw seamless rings too, until this is switched off or Forge restarts: this is how a 360 video is made in Deforum',
                    on => E.tileEverything(on).catch(e => say(String(e.message || e), 'error')))),
            section('Hires. fix', gen.hires.enabled,
                check(gen.hires, 'enabled', 'With the generation'),
                row(field('Scale', number(gen.hires, 'scale', { min: 1, max: 4, step: 0.25 })),
                    field('Denoising', number(gen.hires, 'denoise', { min: 0, max: 1, step: 0.05 }), '0 = only enlarge with the upscaler, nothing is drawn again'),
                    field('Hires steps', number(gen.hires, 'steps', { min: 0, max: 150 }), '0 = the same as Steps')),
                field('Upscaler', select(gen.hires, 'upscaler', upscalers))),
            o.animatediff ? section('AnimateDiff (a moving world)', gen.animatediff.enabled,
                check(gen.animatediff, 'enabled', 'Make it a video'),
                field('Motion module', select(gen.animatediff, 'model', o.motion_modules.map(m => [m, m, false]))),
                row(field('Frames', number(gen.animatediff, 'frames', { min: 2, max: 256 })), field('FPS', number(gen.animatediff, 'fps', { min: 1, max: 60 })),
                    field('Closed loop', select(gen.animatediff, 'closed_loop', ['N', 'R-P', 'R+P', 'A'].map(v => [v, v, false])))),
                row(field('Context', number(gen.animatediff, 'context', { min: 1, max: 32 })), field('Overlap', number(gen.animatediff, 'overlap', { min: -1, max: 16 })),
                    field('Smoothing', select(gen.animatediff, 'interp', [['Off', 'Off'], ['FILM', 'FILM', false]])), field('Times', number(gen.animatediff, 'interp_x', { min: 2, max: 20 })))) : null,
            h('div', { class: 's360-actions' },
                button('Generate world', async () => { if (!gen.prompt.trim()) throw new Error('Write a prompt first'); await E.generate(world.id); }, { class: 's360-btn s360-primary', disabled: busy }),
                h('label', { class: 's360-btn s360-filebtn' }, h('span', { t: 'Upload a picture or a video' }),
                    h('input', { type: 'file', accept: 'image/*,video/*', on: { change: guarded(async e => { const file = e.target.files[0]; e.target.value = ''; if (file) await E.upload(world.id, file); }) } })),
                check(gen, 'bounce', 'An uploaded video plays back and forth', 'For a video that is not a loop (a Deforum render): it is played forwards and then backwards, so it never jumps when it starts again'),
                button('Hires. fix this world', () => E.hires(world.id), { disabled: busy || !world.file || isVideo }),
                button('Previous version', () => E.undoWorld(world.id), { disabled: !(world.history || []).length, tt: 'Brings back the picture as it was before the last change' })),
            isVideo ? field('Video delay (s)', number(world, 'video_delay', { min: -600, max: 600, step: 0.1, fallback: 0 }),
                'In a recording, the moment at which this world\'s video is at its beginning: set it to when the camera arrives here') : null,
            h('div', { class: 's360-subtitle', t: 'Form of the world' }),
            h('div', { class: 's360-iconrow' }, ...WORLD_FORMS.map(([id, icon, label, tip]) => h('button', {
                type: 'button', class: 's360-btn s360-iconbtn' + ((world.form || 'round') === id ? ' s360-on' : ''), tt: tip,
                on: { click: () => { world.form = id === 'round' ? null : id; E.touch('worlds'); E.applyDepth(world); renderPanel(); } },
            }, h('span', { class: 's360-bigicon' }, icon), h('span', { t: label })))),
            world.form === 'box' ? row(field('Room width (m)', slider(world, 'room', { min: 2, max: 60, step: 0.5 }, () => E.applyDepth(world))),
                field('Ceiling (m)', slider(world, 'ceiling', { min: 2, max: 30, step: 0.1 }, () => E.applyDepth(world)))) : null,
            h('div', { class: 's360-subtitle', t: 'Moving in this world' }),
            h('div', { class: 's360-iconrow' }, ...[['', '⚙', 'As the project', 'The way the Route tab says for the whole project']].concat(WORLD_RIGS).map(([id, icon, label, tip]) => h('button', {
                type: 'button', class: 's360-btn s360-iconbtn' + ((world.rig || '') === id ? ' s360-on' : ''), tt: tip,
                on: { click: () => { world.rig = id || null; E.touch('worlds'); renderPanel(); } },
            }, h('span', { class: 's360-bigicon' }, icon), h('span', { t: label })))),
            world.rig ? row(field('Amount', slider(world, 'rig_strength', { min: 0, max: 2, step: 0.1 })), field('Speed (m/s)', slider(world, 'rig_speed', { min: 0.3, max: 6, step: 0.1 }))) : null,
            section('Depth', false,
                h('p', { class: 's360-note', t: 'How far things are: this is what lets you move inside the world' }),
                field('Nearest (m)', slider(world, 'near', { min: 0.3, max: 6, step: 0.1 }, () => E.applyDepth(world))),
                field('Farthest (m)', slider(world, 'far', { min: 4, max: 80, step: 1 }, () => E.applyDepth(world))),
                field('Strength', slider(world, 'strength', { min: 0, max: 1.5, step: 0.05 }, () => E.applyDepth(world))),
            field('Eye height (m)', number(world, 'eye', { min: 0.2, max: 20, step: 0.05, fallback: 1.65 }), 'How high above the ground the picture was taken. Whatever stands on the ground stands this far below the eyes'),
                button('Measure the depth again', () => E.remeasure(world.id), { disabled: busy || !world.file })),
            section('Weather', !!(weather.kind || weather.mist > 0),
                h('p', { class: 's360-note', t: 'Rain and snow fall inside the world: fly through them and the drops pass you by.' }),
                field('Falling', select(weather, 'kind', [['', 'Nothing'], ['rain', 'Rain'], ['snow', 'Snow']])),
                row(field('Amount', slider(weather, 'amount', { min: 0.05, max: 1, step: 0.05 })), field('Wind', slider(weather, 'wind', { min: -1, max: 1, step: 0.05 }))),
                field('Mist', slider(weather, 'mist', { min: 0, max: 1, step: 0.05 }))),
            section('Start point', false,
                row(button('Start here', () => { world.start = { pos: E.cam.pos.map(v => +v.toFixed(3)), yaw: +E.cam.yaw.toFixed(2), pitch: +E.cam.pitch.toFixed(2), fov: +E.cam.fov.toFixed(1) }; E.touch(); say(T('The start point was set')); },
                    { tt: 'Where you stand and look now becomes where this world begins' }),
                    button('Go to the start', () => E.go(world.id))),
                h('label', { class: 's360-checkline' }, h('input', { type: 'radio', class: 's360-check', checked: E.doc.start_world === world.id, on: { change: () => { E.doc.start_world = world.id; E.touch(); renderStrip(); } } }), h('span', { t: 'First world of the project' }))),
            world.infotext ? section('Generation data', false, h('pre', { class: 's360-info', dir: 'ltr' }, world.infotext)) : null,
            h('div', { class: 's360-actions' }, button('Delete world', async () => { if (confirm(T('Delete the world "{0}"?', world.name))) await E.removeWorld(world.id); }, { class: 's360-btn s360-danger' })),
        ];
    }

    // a world's form: a ball as the picture's depth says, flat ground under a dome of sky, or a room
    const WORLD_FORMS = [['round', '◯', 'Round', 'As the picture\'s depth says: a ball pushed in and out'],
        ['earth', '◓', 'Earth', 'Flat ground under a dome of sky: whatever stands on it stands on true, level ground'],
        ['box', '▢', 'Box', 'A room: flat walls, floor and ceiling']];
    // how one moves in a world
    const WORLD_RIGS = [['pro', '🎥', 'Smooth', 'A camera on rails: smooth starts and stops'], ['hover', '🎈', 'Hovering', 'Floating, weightless'],
        ['walk', '🚶', 'Walking', 'Somebody walking: steps'], ['handheld', '✋', 'Handheld', 'A camera in the hand'],
        ['run', '🏃', 'Running', 'Somebody running: strides, breath'], ['ride', '🚲', 'Cycling', 'On a bicycle: leaning, the road\'s rattle']];

    const TOOLS = [['look', 'Look around', '✋'], ['rect', 'Rectangle', '▭'], ['ellipse', 'Ellipse', '◯'], ['lasso', 'Lasso', '✎'], ['brush', 'Brush', '🖌'], ['erase', 'Eraser', '⌫'], ['wand', 'Auto select', '✨']];

    function setTool(tool) { UI.tool = tool; UI.drag = null; renderTools(); if (UI.over) UI.over.style.cursor = tool === 'look' ? 'grab' : 'crosshair'; }

    function renderTools() {
        if (!UI.tools) return;
        const show = E.doc && (UI.tab === 'portals' || (UI.tab === 'character' && E.character && E.character.mode === 'mask'));
        UI.tools.style.display = show ? '' : 'none';
        if (!show) return;
        UI.tools.replaceChildren(
            ...TOOLS.filter(t => t[0] !== 'wand' || E.options.auto_select).map(([id, label, icon]) => h('button', {
                type: 'button', class: 's360-tool' + (UI.tool === id ? ' s360-on' : ''), tt: label === 'Auto select' ? 'Click on a thing (a door, a window) and it is selected' : label,
                on: { click: () => setTool(id) },
            }, icon, h('span', { t: label }))),
            h('button', { type: 'button', class: 's360-tool s360-mode' + (UI.subtract ? ' s360-on' : ''), on: { click: () => { UI.subtract = !UI.subtract; renderTools(); } } }, UI.subtract ? '−' : '+', h('span', { t: UI.subtract ? 'Subtract' : 'Add' })),
            UI.tool === 'wand' && E.wandState && E.wandState.result ? h('span', { class: 's360-toolrow' },
                h('button', { type: 'button', class: 's360-tool', tt: 'Smaller', on: { click: () => E.wandSize(-1) } }, '◂'),
                h('button', { type: 'button', class: 's360-tool', tt: 'Larger', on: { click: () => E.wandSize(1) } }, '▸')) : null,
            h('button', { type: 'button', class: 's360-tool', tt: 'Clear', on: { click: () => E.clearSelection() } }, '✕', h('span', { t: 'Clear' })));
    }

    function worldOptions(except, withNew) {
        const list = E.doc.worlds.filter(w => w.id !== except).map(w => [w.id, w.name, false]);
        return [['', 'Choose a world…']].concat(list, withNew ? [['+', 'A new world']] : []);
    }

    function portalsPanel() {
        const world = E.world();
        if (!world) return [h('p', { class: 's360-note', t: 'Press + below to add your first world.' })];
        UI.newPortal = UI.newPortal || { target: '' };
        UI.far = UI.far || { metres: 7, half: 60 };
        UI.gate = UI.gate || { ahead: 9, wide: 12, tall: 0 };
        if (UI.newPortal.target && UI.newPortal.target !== '+' && !E.world(UI.newPortal.target)) UI.newPortal.target = '';
        const brush = { size: UI.brush };
        const create = async () => {
            let target = UI.newPortal.target || null;
            if (target === '+') { const made = E.addWorld(); target = made.id; UI.newPortal.target = target; }
            await E.createPortal(target);
            say(T('The portal was created'));
            setTool('look');
        };
        const list = (world.portals || []).map((portal, i) => {
            const tint = E.TINTS[i % E.TINTS.length].map(c => Math.round(c * 255)).join(',');
            const view = { name: portal.name || '', feather: Math.round(E.feather(portal) * 100), deep: portal.deep !== false };
            const nameInput = text(view, 'name', { placeholder: T('Portal {0}', i + 1), after: v => { portal.name = v; } });
            // the edge is drawn again a moment after the slider stops, not at every step of it
            const soften = value => { portal.feather = value / 100; clearTimeout(UI.softening); UI.softening = setTimeout(() => E.showMasks(world), 140); };
            return h('div', { class: 's360-card' },
                row(h('span', { class: 's360-dot', style: `background: rgb(${tint})` }), nameInput,
                    h('button', { type: 'button', class: 's360-btn s360-icon', tt: 'Delete', on: { click: guarded(() => E.removePortal(portal)) } }, '✕')),
                field('Leads to', select(portal, 'target', worldOptions(world.id, false), value => { portal.target = value || null; if (value) E.ensure(value); renderPanel(); })),
                row(check(portal, 'see_through', 'See through', 'The next world is seen through the portal, like through a window'),
                    field('Passing', select(portal, 'style', [['smooth', 'Walk through'], ['flash', 'Flash'], ['cut', 'Cut']]))),
                check(view, 'deep', 'The next world has its depth', 'The world behind the portal is drawn as it really looks from where you stand: things in it pass each other as you move, and walking in changes nothing in the picture. Without it the portal shows that world flat, and going in is a plunge', value => { portal.deep = value; }),
                field('Soft edge', slider(view, 'feather', { min: 0, max: 100, step: 1 }, soften), 'How softly the world behind the portal fades into this one at the portal\'s edge: 0 is a sharp edge'),
                h('div', { class: 's360-line' }, h('span', { class: 's360-label', t: 'Way in' }), h('span', { class: 's360-faint', t: portal.way ? 'a chosen direction' : portal.level ? 'at eye level' : 'through its middle' }),
                    button('Where I look now', () => { portal.way = { yaw: +E.cam.yaw.toFixed(2), pitch: Math.abs(E.cam.pitch) < 8 ? 0 : +E.cam.pitch.toFixed(2) }; E.touch(); renderPanel(); }, { tt: 'The direction in which this portal is entered: along a street, straight at a door' }),
                    portal.way ? button('Reset', () => { portal.way = null; E.touch(); renderPanel(); }) : null),
                h('div', { class: 's360-line' }, h('span', { class: 's360-label', t: 'Arrival' }), h('span', { class: 's360-faint', t: portal.arrival ? 'a chosen point' : 'the start of that world' }),
                    button('Choose…', () => chooseArrival(portal), { disabled: !portal.target }),
                    portal.arrival ? button('Reset', () => { portal.arrival = null; E.touch(); renderPanel(); }) : null),
                portal.twin_of ? h('p', { class: 's360-faint', t: 'The way back of a gate in another world: change that one' })
                    : row(check({ on: !!E.twinOf(portal) }, 'on', 'One gate for both worlds', 'The same gate stands in the world it leads to, behind where one arrives, and leads back here: a street goes on into the next world and back again',
                        guarded(async on => { if (on) { await E.twinPortal(portal); say(T('The gate leads both ways')); } else { const twin = E.twinOf(portal); if (twin) await E.removePortal(twin); } renderPanel(); })),
                        E.twinOf(portal) ? button('Again', guarded(async () => { await E.twinPortal(portal); say(T('The gate leads both ways')); }), { tt: 'After the shape or the arrival changed: the way back is made again' }) : null),
                h('div', { class: 's360-actions' },
                    button('Look at it', () => E.lookAtPortal(portal)),
                    button('Enter', () => { E.lookAtPortal(portal); E.enter(portal); UI.stage.focus(); }, { disabled: !portal.target }),
                    button('Edit shape', () => { E.selectPortal(portal); E.lookAtPortal(portal); }, { tt: 'Puts the portal\'s shape back into the selection, to change it' }),
                    button('Use selection', () => E.reshapePortal(portal), { disabled: !E.sel.any, tt: 'The selection becomes the portal\'s new shape' })));
        });
        return [
            h('p', { class: 's360-note', t: 'A portal is a place in this world that leads into another world. Mark the place, then choose where it leads.' }),
            h('div', { class: 's360-subtitle', t: 'Marking tools' }),
            h('div', { class: 's360-toolgrid' }, TOOLS.filter(t => t[0] !== 'wand' || E.options.auto_select).map(([id, label, icon]) => h('button', {
                type: 'button', class: 's360-btn' + (UI.tool === id ? ' s360-on' : ''), on: { click: () => { setTool(id); renderPanel(); UI.stage.focus(); } },
            }, icon + ' ', h('span', { t: label })))),
            row(h('label', { class: 's360-checkline' }, h('input', { type: 'checkbox', class: 's360-check', checked: UI.subtract, on: { change: e => { UI.subtract = e.target.checked; renderTools(); } } }), h('span', { t: 'Subtract' })),
                field('Brush size', slider(brush, 'size', { min: 6, max: 160, step: 2 }, v => { UI.brush = v; }))),
            row(button('Clear', () => E.clearSelection()), button('Invert', () => E.invertSelection())),
            section('The way on', false,
                h('p', { class: 's360-note', t: 'A portal can be the way on: the far part of a street, a gate across it, the far wall of a room. The next place is then the continuation of this one, and you walk on into it without noticing. Look where the way goes, mark, and create the portal.' }),
                h('div', { class: 's360-subtitle', t: 'A gate across the way' }),
                row(field('Ahead (m)', number(UI.gate, 'ahead', { min: 0.5, max: 200, step: 0.5 })), field('Width (m)', number(UI.gate, 'wide', { min: 0.2, max: 200, step: 0.5 })),
                    field('Height (m)', number(UI.gate, 'tall', { min: 0, max: 200, step: 0.5 }), '0: up to the sky')),
                button('Mark a gate', () => { E.selectGate(UI.gate.ahead, UI.gate.wide, UI.gate.tall, UI.subtract); renderPanel(); }, { tt: 'Everything seen beyond an opening that stands across the way, from the ground up: the street from there on' }),
                h('div', { class: 's360-subtitle', t: 'What is far' }),
                row(field('Farther than (m)', number(UI.far, 'metres', { min: 0.5, max: 80, step: 0.5 })), field('To each side (degrees)', number(UI.far, 'half', { min: 5, max: 180, step: 5 }))),
                button('Mark what is far', () => { E.selectFar(UI.far.metres, UI.far.half, UI.subtract); renderPanel(); }, { tt: 'Everything the world\'s depth says is at least this far, where you look: it follows the outlines of what is near' })),
            h('div', { class: 's360-card s360-new' },
                field('Leads to', select(UI.newPortal, 'target', worldOptions(world.id, true))),
                button('Create portal', create, { class: 's360-btn s360-primary', disabled: !E.sel.any })),
            h('div', { class: 's360-subtitle', t: 'Portals of this world' }),
            ...(list.length ? list : [h('p', { class: 's360-note', t: 'No portals yet.' })]),
        ];
    }

    async function chooseArrival(portal) {
        const from = { world: E.worldId, pose: { pos: E.cam.pos.slice(), yaw: E.cam.yaw, pitch: E.cam.pitch, fov: E.cam.fov } };
        UI.arrivalPick = { portal, from };
        setTool('look');
        const there = E.arrival(portal);
        await E.go(portal.target, { pos: there.pos, yaw: there.yaw, pitch: there.pitch, fov: E.cam.fov });
        renderBanner();
        UI.stage.focus();
    }

    function renderBanner() {
        if (!UI.banner) return;
        const pick = UI.arrivalPick, c = E.character;
        if (pick) {
            const done = async keep => {
                if (keep) { pick.portal.arrival = { pos: E.cam.pos.map(v => +v.toFixed(3)), yaw: +E.cam.yaw.toFixed(2), pitch: +E.cam.pitch.toFixed(2) }; E.touch(); }
                UI.arrivalPick = null;
                await E.go(pick.from.world, pick.from.pose);
                renderBanner(); renderPanel();
            };
            UI.banner.replaceChildren(h('span', { t: 'Stand where visitors should arrive, look where they should look, then confirm.' }),
                button('Arrive here', () => done(true), { class: 's360-btn s360-primary' }), button('Cancel', () => done(false)));
        } else if (c && c.pending) {
            UI.banner.replaceChildren(h('span', { t: 'Look around to judge it. Keep it, or try again.' }),
                button('Keep', keepCharacter, { class: 's360-btn s360-primary', disabled: c.busy }),
                button('Try again', () => E.generateCharacter(), { disabled: c.busy }), button('Discard', () => E.discardCharacter(), { disabled: c.busy }));
        } else UI.banner.replaceChildren();
        UI.banner.style.display = UI.banner.childNodes.length ? '' : 'none';
    }

    async function keepCharacter() { await E.keepCharacter(); say(T('The character is part of the world now')); }

    const POSE_NAMES = [['standing', 'Standing'], ['walking', 'Walking'], ['running', 'Running'], ['waving', 'Waving'], ['arms_up', 'Arms up'], ['hands_on_hips', 'Hands on hips'], ['dancing', 'Dancing'], ['open_arms', 'Open arms'], ['sitting', 'Sitting']];

    /* the objects of the world, and the one that is chosen */
    function objectsList(world) {
        const objects = world.objects || [], chosen = E.object();
        const cards = objects.map((object, i) => h('button', {
            type: 'button', class: 's360-object' + (chosen === object ? ' s360-on' : ''),
            on: { click: () => { E.choose(chosen === object ? null : object.id); } },
        }, h('img', { src: E.fileUrl(world, object.poster || object.file), alt: '', draggable: false }),
        h('span', { dir: 'auto' }, object.name || T('Object {0}', i + 1)), object.kind === 'video' ? h('span', { class: 's360-badge', t: 'video' }) : null,
        object.model ? h('span', { class: 's360-badge', dir: 'ltr' }, '3D') : null));
        const out = [
            h('p', { class: 's360-note', t: 'An object stands in the world as its own piece, with its own depth: move around it and the world behind it moves apart from it. Click an object in the view to choose it, and drag it to move it.' }),
            h('div', { class: 's360-actions' },
                button('Draw a new one', () => { E.openCharacter(); UI.stage.focus(); }, { class: 's360-btn s360-primary', disabled: !world.file }),
                h('label', { class: 's360-btn s360-filebtn' }, h('span', { t: 'Upload a picture or a video' }),
                    h('input', { type: 'file', accept: 'image/*,video/*', on: { change: guarded(async e => { const file = e.target.files[0]; e.target.value = ''; if (file) await E.uploadObject(file, UI.keepBackground); }) } })),
                h('label', { class: 's360-checkline', tt: 'For a screen, a painting, a poster: the upload stays a whole rectangle. Otherwise its background is removed.' },
                    h('input', { type: 'checkbox', class: 's360-check', checked: !!UI.keepBackground, on: { change: e => { UI.keepBackground = e.target.checked; } } }), h('span', { t: 'Keep its background' }))),
            solidMaker(world),
            shapesAndKept(world),
            objects.length ? h('div', { class: 's360-objects' }, cards) : h('p', { class: 's360-note', t: 'No objects yet.' }),
            check(E.doc.settings, 'objects_over', 'Objects are never hidden by the world', 'They are drawn over the panorama even where its surface is nearer than they are: people far down a street, where the depth of the panorama ends sooner than the street seems to'),
        ];
        if (!chosen) return out;
        const object = chosen, busy = E.jobs.size > 0;
        const settings = object.gen = object.gen || {}; settings.settings = settings.settings || {};
        const again = Object.assign({ prompt: '', negative: 'blurry, deformed, extra limbs, text, watermark', steps: 8, cfg: 2, sampler: (E.options.samplers || []).includes('LCM') ? 'LCM' : 'Euler a', seed: -1, denoise: 0.75, fill: 'original', mask_blur: 8, control_weight: 1, checkpoint: '', lcm: !!E.options.lora_lcm,
            enlarge: 1, upscaler: (E.options.upscalers || []).find(u => /R-ESRGAN 4x\+$/.test(u)) || 'Lanczos' }, settings.settings);
        settings.settings = again;
        const away = { d: +Math.hypot(object.pos[0], object.pos[2]).toFixed(2) || 0.5 }, lift = { y: object.pos[1] };
        const facing = { camera: object.turn == null, angle: object.turn == null ? Math.round(Core.anglesFromDir(...E.objectSheet(object, E.cam.pos).normal).yaw) : object.turn };
        if (object.relief == null) object.relief = 0.2;
        if (object.opacity == null) object.opacity = 1;
        const shape = { wide: +object.wide || 1, tall: +object.tall || 1, deep: +object.deep || 1, tilt: +object.tilt || 0, roll: +object.roll || 0 };
        out.push(h('div', { class: 's360-card' },
            field('Name', text(object, 'name', { placeholder: T('Object {0}', objects.indexOf(object) + 1), after: () => {} })),
            field('Height (m)', slider(object, 'size', { min: 0.1, max: 12, step: 0.05 })),
            row(field('Distance (m)', slider(away, 'd', { min: 0.3, max: Math.max(6, +(world.far || 14)), step: 0.05 }, value => {
                const now = Math.hypot(object.pos[0], object.pos[2]);
                if (now > 1e-3) { object.pos[0] *= value / now; object.pos[2] *= value / now; } else { const f = Core.dirFromAngles(E.cam.yaw, 0); object.pos[0] = f[0] * value; object.pos[2] = f[2] * value; }
            })), field('Lift (m)', slider(lift, 'y', { min: -4, max: 8, step: 0.05 }, value => { object.pos[1] = value; }))),
            field('Thickness (3D)', slider(object, 'relief', { min: 0, max: 0.8, step: 0.02 }), 'How far the object comes out of its sheet: its own depth map gives it a body'),
            row(check(facing, 'camera', 'Faces the camera', 'It turns with you, so it never looks like a flat card', on => { object.turn = on ? null : facing.angle; E.touch(); renderPanel(); }),
                facing.camera ? null : field('Heading', slider(facing, 'angle', { min: -180, max: 180, step: 1 }, value => { object.turn = value; }))),
            row(field('Opacity', slider(object, 'opacity', { min: 0.05, max: 1, step: 0.05 })), check(object, 'flip', 'Mirror')),
            section('Its shape', (+object.wide || 1) !== 1 || (+object.tall || 1) !== 1 || !!(+object.tilt) || !!(+object.roll),
                h('p', { class: 's360-note', t: 'The frame around the chosen object does the same by hand: a corner makes all of it larger or smaller, a side changes its width or its height, and the round handles turn it (above), tip it (right), turn it round (below) and bring it nearer or take it farther (left).' }),
                row(field('Width', slider(shape, 'wide', { min: 0.1, max: 5, step: 0.05 }, value => { object.wide = value; })), field('Height', slider(shape, 'tall', { min: 0.1, max: 5, step: 0.05 }, value => { object.tall = value; })),
                    object.model && !object.flat ? field('Depth', slider(shape, 'deep', { min: 0.1, max: 5, step: 0.05 }, value => { object.deep = value; })) : null),
                row(field('Tipped (°)', slider(shape, 'tilt', { min: -180, max: 180, step: 1 }, value => { object.tilt = value; })), field('Turned in its plane (°)', slider(shape, 'roll', { min: -180, max: 180, step: 1 }, value => { object.roll = value; }))),
                h('div', { class: 's360-actions' }, button('As it was made', () => { for (const key of ['wide', 'tall', 'deep', 'tilt', 'roll']) delete object[key]; E.touch('objects'); renderPanel(); }))),
            h('div', { class: 's360-actions' },
                object.on_ground ? null : button('Stand it on the ground', () => { const feet = E.objectFeet(object), at = Core.project(Core.viewProjection(E.cam, E.aspect()), feet); if (at) { E.moveObjectTo(object, at[0], at[1]); renderPanel(); } }),
                button('Look at it', () => { const now = E.objectNow(object), a = Core.anglesFromDir(now.pos[0] - E.cam.pos[0], now.pos[1] + now.size / 2 - E.cam.pos[1], now.pos[2] - E.cam.pos[2]); E.cam.yaw = a.yaw; E.cam.pitch = a.pitch; }),
                button('Duplicate', () => E.copyObject(object)),
                button('Keep it in my objects', async () => { await E.keepObject(object); say(T('It is kept: "Plain shapes and my objects", in every project')); renderPanel(); }, { tt: 'Its picture, its model and everything it is are copied to your own collection, to be stood in other worlds and other projects' }),
                button('Delete', () => E.removeObject(object), { class: 's360-btn s360-danger' })),
            movement(object),
            solidCard(object, busy),
            E.talk().live_portrait ? h('div', { class: 's360-actions' },
                button('Give it a line', () => { E.addLine(object); UI.tab = 'dialogue'; renderTabs(); renderPanel(); }, { tt: 'A character with a face can speak: the Dialogue tab' }),
                h('label', { class: 's360-btn s360-filebtn', tt: 'A recording, a song, speech made elsewhere: it becomes a line of this character, and its lips move to it' }, h('span', { t: 'Lips to a sound file' }),
                    h('input', { type: 'file', accept: 'audio/*,.wav,.mp3,.m4a,.ogg,.flac', disabled: busy, on: { change: guarded(async e => { const file = e.target.files[0]; e.target.value = ''; if (file) { await E.speakFile(object, file); say(T('It says the file now: see the Dialogue tab')); } }) } })),
                button('Bring it to life', () => E.enliven(object), { disabled: busy, tt: 'The face looks about and blinks, in a short loop that repeats (LivePortrait)' })) : null,
            object.kind === 'image' ? section('Draw on it again', true,
                h('p', { class: 's360-note', t: 'The object is drawn again on its own picture, inside its own outline: change what it is, keep where it stands.' }),
                field('Prompt', text(again, 'prompt', { area: true, rows: 3, placeholder: 'What to draw: a knight in silver armor, a red fox, a stone fountain…' })),
                row(field('Denoising', number(again, 'denoise', { min: 0.05, max: 1, step: 0.05 })), field('Steps', number(again, 'steps', { min: 1, max: 150 })), field('CFG', number(again, 'cfg', { min: 0, max: 30, step: 0.5 })), field('Seed', number(again, 'seed', { min: -1, max: 4294967295, fallback: -1 }))),
                row(field('Enlarge first', select(again, 'enlarge', [[1, '1 ×', false], [1.5, '1.5 ×', false], [2, '2 ×', false]], v => { again.enlarge = +v; }), 'Hires. fix for an object: its picture is enlarged and then drawn again at the new size. A denoising of about 0.35 keeps what it is and sharpens it.'),
                    field('Upscaler', select(again, 'upscaler', (E.options.upscalers || []).map(u => [u, u, false])))),
                h('div', { class: 's360-actions' },
                    button('Draw again', async () => { await E.redrawObject(object, again); object.gen.settings.enlarge = 1; renderPanel(); }, { class: 's360-btn s360-primary', disabled: busy }),
                    button('Previous picture', () => E.undoObject(object), { disabled: !(object.history || []).length }))) : null));
        return out;
    }

    /* ---- solid objects: real 3D models ------------------------------------------------------------------ */

    const MOVES = { walk: 'Walks', run: 'Runs', idle: 'Breathes and looks about', wave: 'Waves', talk: 'Talks with its hands', dance: 'Dances', fly: 'Beats its wings', glide: 'Glides', swim: 'Swims' };
    const GOING = { biped: ['walk', 'run'], four: ['walk', 'run'], wings: [], fish: [] };

    /* what Hunyuan3D is asked for, kept with the project */
    function solidWishes() {
        const o = E.options, s = E.doc.settings.solid = E.doc.settings.solid || {};
        return Object.assign(s, Object.assign({ prompt: '', negative: 'blurry, deformed, extra limbs', steps: 8, cfg: 2, sampler: (o.samplers || []).includes('LCM') ? 'LCM' : 'Euler a', seed: -1, checkpoint: '',
            lcm: !!o.lora_lcm, shape: 'portrait', alone: true, texture: true, detail: 256 }, s));
    }

    function solidHow(s) {
        return row(check(s, 'texture', 'With its colours', 'Without: only its shape, in grey, in a tenth of the time'),
            field('Detail of the shape', select(s, 'detail', [[256, 'Usual'], [384, 'Fine']], v => { s.detail = +v; })));
    }

    const SHAPE_NAMES = { cube: 'Cube', ball: 'Ball', half: 'Half a ball', cone: 'Cone', pyramid: 'Pyramid', cylinder: 'Cylinder', tube: 'Tube', capsule: 'Capsule', ring: 'Ring' };

    /* Plain 3D shapes to build with, and the objects that were kept from earlier work. */
    function shapesAndKept(world) {
        const busy = E.jobs.size > 0, kept = E.kept || [];
        UI.shapeColour = UI.shapeColour || '#8f95ff';
        return section('Plain shapes and my objects', kept.length > 0,
            h('p', { class: 's360-note', t: 'A plain shape is a 3D object like any other: stand it, stretch it by its frame, give it a colour or draw a material on it. Houses, crates, pillars and tables are built of them.' }),
            row(field('Colour', h('input', { type: 'color', class: 's360-input', value: UI.shapeColour, on: { input: e => { UI.shapeColour = e.target.value; } } })),
                h('div', { class: 's360-actions' }, Solid.SHAPES.map(kind => button(SHAPE_NAMES[kind], async () => { await E.addShape(kind, UI.shapeColour); renderPanel(); }, { disabled: busy || !world.file })))),
            h('p', { class: 's360-note', t: 'My objects: what you keep from any world (the button "Keep it in my objects" on an object) waits here, for this project and every other. Click one to stand it in this world.' }),
            kept.length ? h('div', { class: 's360-objects' }, kept.map(entry => h('div', { class: 's360-object s360-kept' },
                h('button', { type: 'button', class: 's360-keptpick', tt: 'Stand it in this world', disabled: busy || !world.file, on: { click: guarded(async () => { await E.useKept(entry); renderPanel(); }) } },
                    h('img', { src: `./sim360/library/file/${entry.id}/${encodeURIComponent(entry.picture || '')}`, alt: '', draggable: false }), h('span', { dir: 'auto' }, entry.name || '…'),
                    entry.solid ? h('span', { class: 's360-badge', dir: 'ltr' }, '3D') : null),
                h('button', { type: 'button', class: 's360-btn s360-icon', tt: 'Forget it', on: { click: guarded(async () => { if (confirm(T('Forget "{0}"? (Objects that were made of it stay.)', entry.name || '…'))) { await E.dropKept(entry); renderPanel(); } }) } }, '✕'))))
                : h('p', { class: 's360-note', t: 'Nothing is kept yet.' }));
    }

    /* A new 3D model: out of words, or a file from the computer. */
    function solidMaker(world) {
        const so = E.options.solid || {}, busy = E.jobs.size > 0;
        const upload = h('label', { class: 's360-btn s360-filebtn', tt: 'A model made elsewhere (Blender, Mixamo, a library of models). Bones and movements it brings are kept and can be played' }, h('span', { t: 'Upload a 3D model (.glb)' }),
            h('input', { type: 'file', accept: '.glb,model/gltf-binary', on: { change: guarded(async e => { const file = e.target.files[0]; e.target.value = ''; if (file) { await E.uploadSolid(file); renderPanel(); } }) } }));
        if (!so.available) {
            return section('A 3D model', false,
                h('p', { class: 's360-note', t: 'Real 3D models are made by Hunyuan3D-2, and it was not found on this computer (Pinokio installs it; Settings can name its folder). A model made elsewhere can still be uploaded.' }),
                h('div', { class: 's360-actions' }, upload));
        }
        const s = solidWishes();
        return section('A 3D model', false,
            h('p', { class: 's360-note', t: 'A real model, with a back and sides, that can walk: Forge draws the thing by itself and Hunyuan3D-2 builds it. About three minutes; Forge lets go of its own model meanwhile, because the two do not fit in the graphics memory together.' }),
            so.open && !so.own ? h('p', { class: 's360-note s360-warn', t: 'Hunyuan3D is open in Pinokio, and that one will be used. It holds a great deal of memory there: when the computer is short of memory, close it in Pinokio, and it is started from here, lighter.' }) : null,
            so.home === 'forge' ? h('p', { class: 's360-note', t: 'Hunyuan3D-2 is a part of this Forge: its program, its Python and its models are in the models folder (sim360-hunyuan3d). It is started when a model is asked for and closed a few minutes after the last one.' })
                : h('div', { class: 's360-actions' },
                    h('p', { class: 's360-note', t: 'Hunyuan3D-2 is used from where another program (Pinokio) installed it. It can be made a part of Forge: its program, its Python and its models are linked into Forge\'s models folder. Nothing is copied and no disk space is taken, and afterwards Pinokio is not needed.' }),
                    button('Make Hunyuan3D a part of Forge', async () => { await E.adoptSolid(); say(T('Hunyuan3D-2 is a part of this Forge now')); renderPanel(); }, { disabled: busy })),
            field('What to make', text(s, 'prompt', { area: true, rows: 2, placeholder: 'What to make: a knight in silver armor, a red fox, a wooden chair…' })),
            row(field('Its picture', select(s, 'shape', [['portrait', 'Tall'], ['square', 'Square'], ['landscape', 'Wide']])), field('Steps', number(s, 'steps', { min: 1, max: 150 })),
                field('CFG', number(s, 'cfg', { min: 0, max: 30, step: 0.5 })), field('Seed', number(s, 'seed', { min: -1, max: 4294967295, fallback: -1 }))),
            row(E.options.lora_lcm ? check(s, 'lcm', 'LCM (fast)') : null,
                check(s, 'alone', 'Alone, on a plain ground', 'Words are added that ask for the whole thing, once, in front of a plain background: what a model is best made of'),
                E.options.openpose_model ? check(s, 'figure', 'A person: standing, limbs apart', 'The figure is drawn on a standing skeleton (OpenPose): whole from head to foot, its arms and legs apart from its body. That is what lets its model walk and move its arms') : null),
            solidHow(s),
            h('div', { class: 's360-actions' },
                button('Make it', async () => { await E.newSolid(s, { texture: s.texture, detail: s.detail }); renderPanel(); }, { class: 's360-btn s360-primary', disabled: busy || !world.file }),
                upload,
                so.own ? button('Let Hunyuan3D go', async () => { await E.stopSolid(); renderPanel(); }, { disabled: busy, tt: 'It was started from here and stays in memory (15 GB) for a few minutes after its last model, to be quick for the next one. This closes it now' }) : null));
    }

    /* The chosen object as a 3D model: making one of its picture, and what a model can do. */
    /* AI4Animation's neural walk for a figure on two legs: made for its path, in a style */
    function realSteps(object, kind) {
        const motion = E.options.motion, busy = E.jobs.size > 0, wish = UI.walkStyle = UI.walkStyle || { style: (object.motion && object.motion.style) || 'Neutral' };
        const names = { Neutral: 'Ordinary', BigSteps: 'Big steps', Chicken: 'Chicken', Dinosaur: 'Dinosaur', DragLeftLeg: 'Drags its left leg', HandsBetweenLegs: 'Hands between the legs',
            LeanRight: 'Leans to the right', LegsApart: 'Legs apart', OnHeels: 'On its heels', Star: 'Star', Zombie: 'Zombie' };
        return h('div', { class: 's360-section' },
            row(kind === 'four' ? field('Real steps (AI4Animation)', h('span', { class: 's360-faint', t: 'A dog: it walks, paces, trots or canters by its speed; while it stands, "Sit", "Lie" and "Stand" among its acts' }), 'A neural network that learnt from a real dog walks it along its path. Made once for the path; make it again after the path or its acts change')
                : field('Real steps (AI4Animation)', select(wish, 'style', motion.styles.map(s => [s, names[s] || s])), 'A neural network that learnt from motion capture walks it along its path: it starts, slows down, turns into a corner and stops the way people do. Made once for the path; make it again after the path changes'),
                h('div', { class: 's360-actions' },
                    button(E.hasSteps(object) ? 'Walk it again' : 'Give it real steps', async () => { await E.walkObject(object, wish.style); renderPanel(); }, { class: 's360-btn s360-primary', disabled: busy || !object.path }),
                    E.hasSteps(object) ? check(object.motion, 'on', 'Use the real steps', 'Off: the formula walk, as before', () => { E.touch('objects'); renderPanel(); }) : null,
                    E.hasSteps(object) ? pinCheck(object.motion) : null)),
            E.walkStale(object) ? h('p', { class: 's360-note', t: 'The path changed since the steps were made: walk it again' }) : null,
            !object.path ? h('p', { class: 's360-faint', t: 'It needs a path of its own (Movement above): the network walks it along that path.' }) : null);
    }

    /* feet that stay where they step (on unless turned off) */
    function pinCheck(m) {
        if (m.pin == null) m.pin = true;
        return check(m, 'pin', 'Feet stay where they step', 'While a foot is on the ground it stays where it stepped: the leg bends to keep it there. Off: it may slide a little', () => E.touch('objects'));
    }

    /* a motion capture file (dance, fight, any action) on a figure on two legs */
    function motionFromFile(object) {
        const busy = E.jobs.size > 0, m = object.motion && object.motion.source === 'mocap' ? object.motion : null;
        const again = () => { if (m && m.travel) E.mocapTravel(object, true); };          // (its path follows when and how fast it goes)
        const input = h('input', { type: 'file', accept: '.bvh,.glb,.gltf,.npz,.fbx', style: 'display:none', on: { change: guarded(async e => {
            const file = e.target.files && e.target.files[0];
            if (file) { await E.mocapObject(object, file, { at: E.play ? E.play.t : 0 }); renderPanel(); }
        }) } });
        return h('div', { class: 's360-section' },
            row(field('A motion from a file', h('span', { class: 's360-faint', t: m ? m.name : 'BVH, GLB or NPZ: a dance, a fight, any motion capture' }), 'Motion capture of any skeleton (Mixamo, LaFan, CMU, AI4Animation\'s): its joints are matched to this figure\'s by their names and laid onto its bones'),
                h('div', { class: 's360-actions' }, h('label', { class: 's360-btn s360-filebtn' + (busy ? ' s360-disabled' : '') }, h('span', { t: m ? 'Another file' : 'Choose a file' }), input))),
            m ? row(field('From second', number(m, 'at', { min: 0, max: 3600, step: 0.1 }, again), 'When in the scene it begins'),
                field('Until second', number(m, 'until', { min: 0, max: 3600, step: 0.1 }, () => E.touch('objects')), 'When in the scene it stops, and the figure stands or does its act again (0: it does not stop)'),
                field('Speed', slider(m, 'speed', { min: 0.25, max: 2.5, step: 0.05 }, again)),
                m.travel ? null : check(m, 'loop', 'Round and round', 'When it ends it begins again; otherwise its last moment is held', () => { E.touch('objects'); renderPanel(); }),
                check(m, 'travel', 'Goes where it goes', 'Its way over the ground becomes its path (turned to its heading, sized to it). Off: it moves in place', on => { E.mocapTravel(object, on); renderPanel(); }),
                check(m, 'on', 'Use it', 'Off: what it did before', () => { E.touch('objects'); renderPanel(); }), pinCheck(m)) : null,
            m ? h('p', { class: 's360-faint', t: +m.skipped ? T('{0} seconds of motion (the T-pose at its start, {1} s, left out)', (+m.seconds || 0).toFixed(1), (+m.skipped).toFixed(1))
                : T('{0} seconds of motion', (+m.seconds || 0).toFixed(1)) }) : null);
    }

    function solidCard(object, busy) {
        const so = E.options.solid || {}, world = E.world();
        if (!object.model && !object.shape) {
            if (!so.available || object.kind !== 'image') return null;
            const s = solidWishes();
            return section('Make it 3D', false,
                h('p', { class: 's360-note', t: 'Hunyuan3D-2 builds a real model of this picture: what the picture shows becomes its front, and its back and sides are made up to fit. It then stands where the picture stood, as tall. About three minutes.' }),
                solidHow(s),
                h('div', { class: 's360-actions' }, button('Make it 3D', async () => { await E.makeSolid(object, { texture: s.texture, detail: s.detail }); renderPanel(); }, { class: 's360-btn s360-primary', disabled: busy })));
        }
        const model = E.modelOf(object), body = object.body = object.body || { kind: '' }, motion = object.motion = object.motion || {};
        const own = !!(model && model.bones), kind = own ? 'own' : body.kind || '';
        for (const [key, value] of [['lit', 0.8], ['bright', 1], ['shadow', object.on_ground ? 0.45 : 0], ['act_speed', 1], ['act', ''], ['go', '']]) if (object[key] == null) object[key] = value;
        for (const [key, value] of [['spin', 0], ['hover', 0], ['sway', 0], ['every', 3]]) if (motion[key] == null) motion[key] = value;
        const facts = object.model_facts || {}, facing = { way: object.face !== 'fixed' }, again = () => { E.touch('objects'); renderPanel(); };
        const faceOn = { on: object.face_on == null ? 'speaking' : object.face_on === 'always' ? 'head' : object.face_on || '' };
        const aim = { to: +object.aim || 0 };
        const standing = own ? (model.moves || []).map(m => [m.name, m.name, false]) : (Solid.KINDS[kind] || []).filter(m => !(GOING[kind] || []).includes(m)).map(m => [m, MOVES[m]]);
        const material = object.material = object.material || { prompt: object.skin_words || '', negative: '', steps: 8, cfg: 2, sampler: (E.options.samplers || []).includes('LCM') ? 'LCM' : 'Euler a', seed: -1, lcm: !!E.options.lora_lcm };
        if (object.skin_repeat == null) object.skin_repeat = 1;
        const tint = { colour: object.colour || '#ffffff' };
        const going = own ? standing : (GOING[kind] || []).map(m => [m, MOVES[m]]);
        return section('3D model', true,
            object.shape ? h('p', { class: 's360-note' }, T(SHAPE_NAMES[object.shape] || object.shape))
                : h('p', { class: 's360-note' }, T('{0} triangles', (facts.triangles || 0).toLocaleString('en-US')) + (facts.bones ? ' · ' + T('{0} bones of its own', facts.bones) : '') + (facts.seconds ? ' · ' + T('made in {0} s', Math.round(facts.seconds)) : '')),
            section('What it is made of', !!object.skin || !!object.shape,
                h('p', { class: 's360-note', t: 'Its colour, or a material that Forge draws and wraps round it: old red bricks, rough bark, rusty iron, roof tiles. The material repeats without a seam.' }),
                row(field('Colour', h('input', { type: 'color', class: 's360-input', value: tint.colour, on: { input: e => { object.colour = e.target.value; E.touch(); } } })),
                    field('The material repeats', slider(object, 'skin_repeat', { min: 0.25, max: 8, step: 0.25 }), 'How many times the drawn material goes round the object')),
                field('Material', text(material, 'prompt', { placeholder: 'Old red bricks, rough bark, rusty iron, roof tiles…' })),
                h('div', { class: 's360-actions' },
                    button('Draw the material', async () => { await E.skinObject(object, material); renderPanel(); }, { class: 's360-btn s360-primary', disabled: busy }),
                    object.skin ? button('Take the material off', async () => { object.skin = null; E.touch('objects'); await E.ensure(world.id); renderPanel(); }) : null)),
            check(object, 'flat', 'Show its flat picture', 'The picture the model was made of, on its sheet, as before. The model is kept', async () => { E.touch('objects'); await E.ensure(world.id); renderPanel(); }),
            row(field('Light of the world on it', slider(object, 'lit', { min: 0, max: 1, step: 0.05 }), 'The sky lights it from above, the ground from below and the brightest part of the panorama is its sun. 0: it keeps the colours it was painted with'),
                field('Its brightness', slider(object, 'bright', { min: 0.4, max: 2.5, step: 0.05 }), 'A model is painted in flat colours, without the light its picture had. When it is made, its colours are brought to those of its picture; this is on top of that'),
                field('Shadow on the ground', slider(object, 'shadow', { min: 0, max: 1, step: 0.05 }), 'A soft dark patch under it. It is known where the ground is only for what stands on the ground')),
            own || object.shape ? null : row(field('Its body', select(body, 'kind', [['', 'A thing: no bones'], ['biped', 'Two legs'], ['four', 'Four legs'], ['wings', 'Wings'], ['fish', 'A fish']], async () => {
                object.act = body.kind === 'biped' ? 'idle' : ''; object.go = ''; await E.rigObject(object); again();
            }), 'Bones are put into the model where its shape says its joints are, so it can walk, beat its wings or swim'),
            kind ? check(body, 'back', 'It faces the other way', 'When it walks backwards: its face is on the other side of the model', async () => { await E.rigObject(object); again(); }) : null),
            kind ? row(field(kind === 'wings' || kind === 'fish' ? 'It moves' : 'While it stands', select(object, 'act', [['', 'Stands still']].concat(standing))),
                going.length ? field('While it goes', select(object, 'go', [['', own ? 'As while it stands' : 'Walks']].concat(going.filter(m => own || m[0] !== 'walk')))) : null,
                field('Pace', slider(object, 'act_speed', { min: 0.2, max: 3, step: 0.05 }))) : null,
            kind ? h('p', { class: 's360-note', t: 'To make it go, open Movement above: "Walks up and down", or a path of its own. Its steps keep to the ground it covers.' }) : null,
            // (a dog can be walked by AI4Animation's quadruped network too, E.walkObject; on the bones guessed for
            // a four-legged model it looked like a crouch, so the panel offers it for two legs only)
            kind === 'biped' && E.options.motion && E.options.motion.available ? realSteps(object, kind) : null,
            kind === 'biped' && E.options.motion && E.options.motion.available ? motionFromFile(object) : null,
            row(field('Turns round (° a second)', number(motion, 'spin', { min: -720, max: 720, step: 5 })), field('Rises and falls (of its height)', number(motion, 'hover', { min: 0, max: 2, step: 0.01 })),
                field('Sways (°)', number(motion, 'sway', { min: 0, max: 45, step: 1 })), field('Every (s)', number(motion, 'every', { min: 0.2, max: 60, step: 0.1 }))),
            row(check(facing, 'way', 'Faces where it goes', 'Walking, it turns its face to its way. Without this it keeps its heading and may walk sideways', on => { object.face = on ? null : 'fixed'; E.touch('objects'); }),
                field('Its nose is', select(aim, 'to', [[0, 'Out of its picture'], [90, 'To the right of its picture'], [-90, 'To the left of its picture'], [180, 'Into its picture']], value => { object.aim = +value; E.touch('objects'); }),
                    'A car or an animal drawn from its side goes nose first only when it is known where its nose is')),
            field('Its picture on it', select(faceOn, 'on', [['front', 'On all of its front'], ['head', 'On its head'], ['speaking', 'On its head, while it speaks'], ['', 'Not at all: as the model was painted']], value => { object.face_on = value || false; E.touch('objects'); }),
                'A model is made of the object\'s picture, so that picture fits the model\'s front. Laid there, the front is as sharp as the picture and has its colours, and the face is a face. While the object says a line, the video of the line is laid there and its lips move: a figure on two legs is filmed from close, its head only, so that the lips have enough of the picture to be read. Turn who speaks towards the camera: a picture laid on a model shows little from the side'),
            h('div', { class: 's360-actions' },
                so.available && object.kind === 'image' ? button('Make the model again', async () => { const s = solidWishes(); await E.makeSolid(object, { texture: s.texture, detail: s.detail }); renderPanel(); }, { disabled: busy, tt: 'Another try, from the same picture: every try comes out a little different' }) : null,
                button('Take the model away', async () => { object.model = null; object.model_facts = null; object.shape = null; E.touch('objects'); await E.ensure(world.id); renderPanel(); }, { tt: 'The object is its flat picture again. The model\'s file stays in the project\'s folder' })));
    }

    /* An object need not stand still. */
    function movement(object) {
        const on = { walk: !!object.walk, held: !!object.held, ground: !!object.on_ground, steps: Math.round((object.gait == null ? (object.walk ? 1 : 0) : +object.gait) * 100) };
        if (object.shy == null) object.shy = 0;
        const again = () => { E.touch('objects'); renderPanel(); };
        return section('Movement', on.walk || on.held || object.shy > 0 || !!object.on_ground,
            check(on, 'ground', 'Stands on the ground', 'Its feet are kept on the ground that the picture shows, wherever it stands or walks: it never floats above the street or sinks into it, and far away it is as small as the street is there. Its distance is then the distance in the scene, and its lift is above the ground', value => { E.groundObject(object, value); renderPanel(); }),
            check(on, 'walk', 'Walks up and down', 'It walks a stretch of the world again and again, fading in at one end and out at the other: a passer-by', value => {
                object.walk = value ? { speed: 1.2, yaw: Math.round(E.cam.yaw), span: 14, phase: Math.round(Math.random() * 14) } : null;
                if (value && object.on_ground == null) E.groundObject(object, true);
                again();
            }),
            object.walk ? row(field('Speed (m/s)', number(object.walk, 'speed', { min: -6, max: 6, step: 0.1 })), field('Heading', number(object.walk, 'yaw', { min: -180, max: 180, step: 1 })),
                field('Stretch (m)', number(object.walk, 'span', { min: 2, max: 80, step: 1 })), field('Starts (m)', number(object.walk, 'phase', { min: 0, max: 80, step: 0.5 }))) : null,
            object.walk || (object.path && object.path.length > 1) ? field('Steps', slider(on, 'steps', { min: 0, max: 150, step: 5 }, value => { object.gait = value / 100; }),
                'It walks: its legs step, its body rises and falls and its arms swing, as far as it goes. 0: it glides') : null,
            field('Steps aside when the camera is nearer than (m)', number(object, 'shy', { min: 0, max: 10, step: 0.1 }), 'Nobody lets you bump into them: 0 switches it off'),
            check(on, 'held', 'Held in front of the camera', 'It goes where the eyes go: a bicycle\'s handlebars, a telephone in the hand', value => {
                object.held = value ? { x: 0, y: -0.42, dist: 0.7, size: 0.5 } : null; again();
            }),
            object.held ? row(field('Left / right', number(object.held, 'x', { min: -1, max: 1, step: 0.02 })), field('Down / up', number(object.held, 'y', { min: -1, max: 1, step: 0.02 })),
                field('Distance (m)', number(object.held, 'dist', { min: 0.2, max: 5, step: 0.05 })), field('Height (m)', number(object.held, 'size', { min: 0.05, max: 5, step: 0.05 }))) : null);
    }

    function characterPanel() {
        const world = E.world();
        if (!world) return [h('p', { class: 's360-note', t: 'Press + below to add your first world.' })];
        const c = E.character, intro = h('p', { class: 's360-note', t: 'Look at the place, put the frame there, and say what to draw. It is drawn in an ordinary view, so it stands straight in the world.' });
        if (!c) return objectsList(world);
        const s = c.settings, o = E.options;
        const steps = number(s, 'steps', { min: 1, max: 150 }), cfg = number(s, 'cfg', { min: 0, max: 30, step: 0.5 });
        const sampler = select(s, 'sampler', o.samplers.map(v => [v, v, false]));
        const seed = number(s, 'seed', { min: -1, max: 4294967295, fallback: -1 });
        const busy = c.busy || E.jobs.size > 0;
        return [
            intro,
            world.kind === 'image' ? field('It becomes', select(c, 'result', [['object', 'An object of its own (3D)'], ['paint', 'Part of the panorama (painted in)']], () => renderPanel()),
                'An object can be moved, resized and drawn again later; painted in, it is simply part of the picture') : null,
            field('Kind', select(c, 'mode', [['pose', 'Figure (OpenPose)'], ['mask', 'Free shape (the selection)']], () => { renderPanel(); renderTools(); })),
            row(field('Frame', select(c, 'aspect', [['2:3', 'Portrait 2:3'], ['1:1', 'Square 1:1'], ['3:2', 'Landscape 3:2'], ['9:16', 'Tall 9:16']], () => E.resetPose())),
                field('Frame size', slider(c.frame, 'h', { min: 0.2, max: 1, step: 0.02 }))),
            c.mode === 'pose' ? h('div', {},
                row(field('Pose', select(c, 'pose', POSE_NAMES, () => E.resetPose())), field('Figure size', slider(c, 'figure', { min: 0.3, max: 1, step: 0.02 }, () => E.resetPose()))),
                row(check(c, 'mirror', 'Mirror', null, () => E.resetPose()), button('Reset pose', () => E.resetPose())),
                h('p', { class: 's360-note', t: 'Drag the frame to place it; drag a joint to bend the figure.' }))
                : h('p', { class: 's360-note', t: 'Mark the place with the tools in the Portals tab, then put the frame over it.' }),
            field('Prompt', text(s, 'prompt', { area: true, rows: 3, placeholder: 'What to draw: a knight in silver armor, a red fox, a stone fountain…' })),
            section('Negative prompt', false, text(s, 'negative', { area: true, rows: 2 })),
            row(field('Steps', steps), field('CFG', cfg), field('Denoising', number(s, 'denoise', { min: 0.05, max: 1, step: 0.05 }))),
            row(field('Sampler', sampler), field('Seed', seed)),
            row(o.lora_lcm ? check(s, 'lcm', 'LCM (fast)') : null, c.mode === 'pose' ? field('Pose strength', number(s, 'control_weight', { min: 0, max: 2, step: 0.05 })) : null),
            row(field('Checkpoint', select(s, 'checkpoint', [['', 'Same as the world']].concat(o.checkpoints.map(v => [v, v.replace(/\s*\[[0-9a-f]+\]$/, ''), false])))),
                field('Masked content', select(s, 'fill', [['original', 'original'], ['noise', 'noise'], ['fill', 'fill']]))),
            o.animatediff && c.result === 'object' ? section('Make it move (AnimateDiff)', s.animatediff.enabled,
                check(s.animatediff, 'enabled', 'A moving object', 'It is drawn as a short video that repeats: a fish that swims, a flame that burns'),
                field('Motion module', select(s.animatediff, 'model', o.motion_modules.map(m => [m, m, false]))),
                row(field('Frames', number(s.animatediff, 'frames', { min: 2, max: 96 })), field('FPS', number(s.animatediff, 'fps', { min: 1, max: 60 })),
                    field('Smoothing', select(s.animatediff, 'interp', [['Off', 'Off'], ['FILM', 'FILM', false]])), field('Times', number(s.animatediff, 'interp_x', { min: 2, max: 20 })))) : null,
            h('div', { class: 's360-actions' },
                button(c.pending ? 'Try again' : 'Draw', () => E.generateCharacter(), { class: 's360-btn s360-primary', disabled: busy }),
                c.pending ? button('Keep', keepCharacter, { disabled: busy }) : null,
                c.pending ? button('Discard', () => E.discardCharacter(), { disabled: busy }) : null,
                button('Close', () => E.closeCharacter())),
        ];
    }

    /* ---- the dialogue ------------------------------------------------------------------------------------- */

    const minutes = seconds => `${Math.floor(seconds / 60)}:${(seconds % 60).toFixed(1).padStart(4, '0')}`;

    /* a sound of the scene, or a piece of acting: when it starts and how loud it is */
    function soundCard(line, index, times) {
        const trying = E.saying && E.saying.line === line, acting = line.kind === 'act', object = acting ? E.lineObject(line) : null;
        if (line.volume == null) line.volume = 0.8;
        if (line.start == null) line.start = 0;
        return h('div', { class: 's360-card s360-line' + (trying ? ' s360-on' : '') },
            h('div', { class: 's360-linehead' },
                h('span', { class: 's360-keyn' }, acting ? '☺' : '♪'),
                acting ? h('span', { class: 's360-keyname', dir: 'auto' }, T('Acting: {0}', object ? object.name || '' : '?')) : text(line, 'name', { placeholder: 'Sound' }),
                acting ? null : h('button', { type: 'button', class: 's360-btn s360-icon', tt: trying ? 'Stop' : 'Hear this line', disabled: !line.audio, on: { click: guarded(() => trying ? E.hush() : E.sayLine(line)) } }, trying ? '■' : '▶'),
                h('button', { type: 'button', class: 's360-btn s360-icon', tt: 'Delete', on: { click: () => { if (confirm(T('Delete this line?'))) E.removeLine(line); } } }, '✕')),
            row(field('Starts at second', number(line, 'start', { min: 0, max: 3600, step: 0.1 }, () => E.emit('lines'))),
                acting ? field('Holds its last look (s)', number(line, 'hold', { min: 0, max: 600, step: 0.5 }, () => E.emit('lines'))) : field('Volume', slider(line, 'volume', { min: 0, max: 1.5, step: 0.05 }))),
            h('p', { class: 's360-faint' }, T('{0} s, starts at {1}', (+line.seconds || 0).toFixed(1), minutes(times.get(line.id) || 0))));
    }

    function lineCard(line, index, times, speakers, busy) {
        if (line.kind === 'sound' || line.kind === 'act') return soundCard(line, index, times);
        const talk = E.talk(), object = E.lineObject(line), home = E.world(line.world);
        const who = { id: line.object || '' };
        const trying = E.saying && E.saying.line === line;
        const stale = E.lineStale(line);
        const picture = object ? (object.kind === 'video' ? object.poster : object.file) : null;
        const outdated = !!line.file && (stale || (picture && line.made_for && line.made_for !== picture));
        const own = { start: line.start !== null && line.start !== undefined && line.start !== '' };
        const state = !line.audio ? 'No sound yet' : !line.file ? (object ? 'Has sound, no moving face yet' : 'Voice only') : outdated ? 'The words or the character changed: make it talk again' : 'Ready';
        const voices = talk.voices.map(v => [v.id, `${v.name} · ${v.language}`, false]);
        if (line.voice && !talk.voices.some(v => v.id === line.voice)) voices.push([line.voice, line.voice, false]);
        const mouths = [['', 'The best there is']].concat(talk.lip_models.map(m => [m, m === 'musetalk_v15' ? 'MuseTalk 1.5' : m, false])).concat([['loudness', 'By the loudness of the voice']]);
        return h('div', { class: 's360-card s360-line' + (trying ? ' s360-on' : '') },
            h('div', { class: 's360-linehead' },
                h('span', { class: 's360-keyn' }, String(index + 1)),
                select(who, 'id', [['', 'Nobody (a voice only)']].concat(speakers.map(({ world, object: o }, i) => [o.id, `${world.name} › ${o.name || T('Object {0}', i + 1)}`, false])), value => {
                    line.object = value || null; line.file = null; E.touch('lines'); renderPanel();
                }),
                h('button', { type: 'button', class: 's360-btn s360-icon', tt: trying ? 'Stop' : 'Hear this line', disabled: !line.audio, on: { click: guarded(() => trying ? E.hush() : E.sayLine(line)) } }, trying ? '■' : '▶'),
                h('button', { type: 'button', class: 's360-btn s360-icon', tt: 'Earlier', disabled: index === 0, on: { click: () => E.moveLine(line, -1) } }, '↑'),
                h('button', { type: 'button', class: 's360-btn s360-icon', tt: 'Later', disabled: index === E.lines().length - 1, on: { click: () => E.moveLine(line, 1) } }, '↓'),
                h('button', { type: 'button', class: 's360-btn s360-icon', tt: 'Delete', on: { click: () => { if (!line.audio || confirm(T('Delete this line?'))) E.removeLine(line); } } }, '✕')),
            text(line, 'text', { area: true, rows: 2, placeholder: 'What the character says' }),
            row(field('Voice', select(line, 'voice', voices.length ? voices : [['', 'No voice is installed']])),
                field('Speed', slider(line, 'rate', { min: -6, max: 6, step: 1 })),
                field('Pitch', slider(line, 'pitch', { min: -10, max: 10, step: 1 }))),
            h('p', { class: 's360-faint' + (outdated || stale ? ' s360-warn' : '') },
                T(state) + (line.seconds ? '  ·  ' + T('{0} s, starts at {1}', (+line.seconds).toFixed(1), minutes(times.get(line.id) || 0)) : '')),
            h('div', { class: 's360-actions' },
                button('Speak', () => E.voiceLine(line), { disabled: busy || !talk.voices.length, tt: 'Make the sound of these words in the chosen voice' }),
                h('label', { class: 's360-btn s360-filebtn', tt: 'A recording of your own, or speech made in another program' }, h('span', { t: 'Bring a sound file' }),
                    h('input', { type: 'file', accept: 'audio/*,video/*', on: { change: guarded(async e => { const file = e.target.files[0]; e.target.value = ''; if (file) { await E.uploadLineAudio(line, file); renderPanel(); } }) } })),
                button('Make it talk', () => E.talkLine(line), { class: 's360-btn s360-primary', disabled: busy || !object || !talk.live_portrait, tt: 'The face of the character moves to the sound' })),
            section('More', false,
                row(check(own, 'start', 'Starts at a time of its own', 'Otherwise it starts when the line before it has ended', on => { line.start = on ? +(times.get(line.id) || 0).toFixed(1) : null; E.touch('lines'); renderPanel(); }),
                    own.start ? field('Second', number(line, 'start', { min: 0, max: 3600, step: 0.1 }, () => E.emit('lines'))) : field('Pause before it (s)', number(line, 'gap', { min: 0, max: 30, step: 0.05, fallback: 0.35 }, () => E.emit('lines')))),
                row(field('Liveliness', slider(line, 'liveliness', { min: 0, max: 1, step: 0.05 }), 'How much the head moves while speaking'),
                    field('Mood', slider(line, 'mood', { min: -1, max: 1, step: 0.1 }), 'From stern (-1) to smiling (1)')),
                row(field('Mouth', select(line, 'lips', mouths), 'Who shapes the mouth: MuseTalk (sharper lips and teeth, slower), a FaceFusion lip syncer, or LivePortrait alone'),
                    field('Volume', slider(line, 'volume', { min: 0, max: 1, step: 0.05 }))),
                field('Subtitle', text(line, 'subtitle', { placeholder: 'If empty, the words themselves' }), 'Shown in the video instead of the words: a translation, for instance'),
                home ? null : h('p', { class: 's360-faint s360-warn', t: 'The world this line was made in is gone: make its sound again.' })));
    }

    function dialoguePanel() {
        if (!E.world()) return [h('p', { class: 's360-note', t: 'Press + below to add your first world.' })];
        const talk = E.talk(), lines = E.lines(), times = E.lineTimes(), busy = E.jobs.size > 0, speakers = E.speakers();
        for (const line of lines) { if (line.volume == null) line.volume = 1; if (line.liveliness == null) line.liveliness = 0.5; if (line.mood == null) line.mood = 0; if (line.gap == null) line.gap = 0.35; if (line.lips == null) line.lips = ''; }
        const end = E.dialogueEnd(), route = E.compile().duration;
        const chosen = E.object();
        const out = [h('p', { class: 's360-note', t: 'A line is something a character says. Write the words and choose a voice; the face of the character then moves to the sound. The lines play along the route, one after the other.' })];
        if (!talk.live_portrait) out.push(h('p', { class: 's360-note s360-warn', t: 'The LivePortrait extension (sd-webui-live-portrait) is not installed in this Forge: lines can be heard, but faces do not move.' }));
        else if (!talk.lip_models.length) out.push(h('p', { class: 's360-note', t: 'No lip syncer was found (FaceFusion, MuseTalk), so mouths open and close with the loudness of the voice. Their folders can be set in Settings > Simulation 360.' }));
        if (!talk.voices.length) out.push(h('p', { class: 's360-note s360-warn', t: 'Windows has no voice installed (Settings > Time & language > Speech). A sound file can still be brought for each line.' }));
        if (!speakers.length) out.push(h('p', { class: 's360-note', t: 'There is nobody to speak yet: draw or upload a character in the Objects tab.' }));
        lines.forEach((line, i) => out.push(lineCard(line, i, times, speakers, busy)));
        out.push(h('div', { class: 's360-actions' },
            button('Add a line', () => { E.addLine(chosen || (speakers[0] && speakers[0].object) || null); renderPanel(); }, { class: 's360-btn' + (lines.length ? '' : ' s360-primary') }),
            h('label', { class: 's360-btn s360-filebtn', tt: 'Music, a telephone that rings, rain: a sound file that plays from a second you choose' }, h('span', { t: 'Add a sound' }),
                h('input', { type: 'file', accept: 'audio/*,video/*', on: { change: guarded(async e => { const file = e.target.files[0]; e.target.value = ''; if (file) { await E.addSound(file); renderPanel(); } }) } })),
            lines.length ? button('Make them all talk', () => E.talkAll(), { class: 's360-btn s360-primary', disabled: busy || !talk.live_portrait, tt: 'Every line that has no moving face yet, or whose words changed' }) : null,
            lines.length ? button('Keep them alive between their lines', () => E.keepAllAlive(), { disabled: busy || !talk.live_portrait, tt: 'One video of each speaker through its whole stretch of the scene: it says its lines where they fall and listens between them (breathes, blinks, mouth at rest), instead of freezing into its still picture every time it falls silent. Make it again after moving a line' }) : null,
            lines.length ? (E.play && E.play.kind === 'tour' ? button('Stop', () => E.stopPlay()) : button('Play the scene', () => E.playTour(), { disabled: route <= 0, tt: 'The route with its dialogue' })) : null));
        if (lines.length) {
            out.push(h('p', { class: 's360-faint' }, T('Dialogue: {0} seconds. Route: {1} seconds.', end.toFixed(1), route.toFixed(1))));
            if (route > 0 && end > route + 0.05) out.push(h('p', { class: 's360-faint s360-warn', t: 'The route ends before the dialogue does: give its keys more time in the Route tab.' }));
            if (route <= 0) out.push(h('p', { class: 's360-faint', t: 'Draw a route in the Route tab: the dialogue plays along it.' }));
        }
        return out;
    }

    const RIG_NAMES = [['pro', 'Professional (smooth)'], ['hover', 'Hovering'], ['walk', 'Walking person'], ['handheld', 'Handheld'], ['run', 'Somebody running (their eyes)'], ['ride', 'On a bicycle']];

    /* what a key can do besides being a place: these make the camera somebody's eyes */
    function keyMore(key, previous) {
        for (const name of ['roll', 'lids', 'jolt']) if (key[name] == null) key[name] = 0;
        if (key.rig == null) key.rig = '';
        if (key.cut == null) key.cut = '';
        if (key.rig_strength == null) key.rig_strength = 1;
        const changed = () => E.emit('tour');
        return h('div', { class: 's360-card' },
            row(field('Head tilt (°)', number(key, 'roll', { min: -180, max: 180, step: 1 }, changed)),
                field('Eyes shut', slider(key, 'lids', { min: 0, max: 1, step: 0.05 }, changed), 'Through somebody\'s eyes: 0 open, 1 shut. Between two keys the lids glide.'),
                field('A blow from behind', number(key, 'jolt', { min: 0, max: 3, step: 0.1 }, changed), 'The head is thrown forward as the camera gets here')),
            row(check(key, 'blink', 'Blink here', 'A quick blink as the camera gets here', changed),
                previous && previous.world !== key.world ? field('Gets to this world by', select(key, 'cut', [['', 'A portal, or a fade'], ['blink', 'A blink']], changed), 'A blink: the eyes shut on one place and open on another') : null),
            row(field('Carried from here on as', select(key, 'rig', [['', 'As before']].concat(RIG_NAMES), changed)),
                key.rig ? field('Amount', number(key, 'rig_strength', { min: 0, max: 3, step: 0.1 }, changed)) : null));
    }

    const SECOND_WAYS = Core.FOLLOW_WAYS;
    const WAY_NAMES = [['side', 'Beside it'], ['behind', 'Behind it, over its shoulder'], ['front', 'Before it, at its face'], ['round', 'Going round it'], ['tripod', 'In one place, turning after it (a tripod)']];
    /* where a camera on a tripod stands: where the view is now */
    const standHere = () => ({ world: E.worldId, pos: E.cam.pos.map(v => +v.toFixed(3)) });

    /* The film's shots along the route's time: the main camera's and the second camera's (whom it follows, and
     * how), with the way each cut goes over. A click puts the time for a new cut there (or goes there while the
     * route plays); while it plays a line shows where it is. */
    const WAY_SHORT = { side: 'beside', behind: 'behind', front: 'face', round: 'round', tripod: 'tripod' };
    function shotStrip(tour, duration) {
        if (!(duration > 0)) return null;
        const second = tour.second || {}, cuts = (tour.cuts || []).filter(c => c && isFinite(+c.t) && +c.t < duration).slice().sort((a, b) => a.t - b.t);
        const walker = tour.walker ? E.objectById(tour.walker) : null, at = x => (100 * Core.clamp(x, 0, duration) / duration) + '%';
        const label = cut => {
            if (!cut || cut.cam !== 'b') return walker ? T('Eyes of {0}', walker.name || '') : T('Main camera');
            const object = E.objectById(cut.target || second.target);
            return (object ? object.name || '' : '?') + ' · ' + T(WAY_SHORT[cut.way || second.mode] || 'beside');
        };
        const strip = h('div', { class: 's360-shots', tt: 'The film\'s shots. Click: the time for a new cut (or go there while the route plays)' });
        [null].concat(cuts).forEach((cut, i) => {
            const from = cut ? +cut.t : 0, to = i < cuts.length ? +cuts[i].t : duration;
            if (to - from <= 1e-6) return;
            strip.append(h('div', { class: 's360-shot ' + (cut && cut.cam === 'b' ? 's360-second' : 's360-main'), style: `left: ${at(from)}; width: ${at(to - from)}`,
                tt: `${from.toFixed(1)}–${to.toFixed(1)} s` }, h('span', {}, label(cut))));
        });
        for (const cut of cuts) {
            if (cut.pass === 'blink') strip.append(h('div', { class: 's360-pass s360-blink', style: `left: ${at(+cut.t)}` }));
            else if (cut.pass === 'dip' || cut.pass === 'dissolve') {
                const long = cut.long == null || cut.long === '' ? 0.8 : +cut.long;
                strip.append(h('div', { class: 's360-pass s360-' + cut.pass, style: `left: ${at(+cut.t - long / 2)}; width: ${at(long)}` }));
            }
        }
        const head = h('div', { class: 's360-head' });
        strip.append(head);
        UI.cutHead = { el: head, duration };
        strip.addEventListener('click', event => {
            const box = strip.getBoundingClientRect(), t = +(Core.clamp((event.clientX - box.left) / box.width, 0, 1) * duration).toFixed(1);
            if (E.play && E.play.kind === 'tour') E.seekPlay(t);
            else { UI.cutAt = { t }; renderPanel(); }
        });
        return h('div', {}, strip, h('div', { class: 's360-shots-times' }, h('span', {}, '0'), h('span', {}, T('{0} s', duration.toFixed(1)))));
    }

    /* ': the world looks stretched from there', for a tripod's place */
    const standLooks = stand => { const v = stand && stand.pos ? viewFrom(stand.pos, stand.world) : null; return v ? ' · ' + T('from there the world looks {0}', T(v.text)) : ''; };

    /* a line that says, while you fly about, how the world looks from where you are (kept up to date in tick) */
    function viewMeter() {
        const el = h('p', { class: 's360-viewmeter', dir: 'auto', tt: 'A world is a picture pushed out by its depth: near its surfaces (the ground too) what a camera sees is stretched. Look for a place where this says "as pictured" or "a little stretched"' });
        UI.viewMeter = el;
        return el;
    }

    /* Cuts made from the dialogue: shot and reverse shot, from who speaks when */
    function dialogueEdit(tour) {
        const o = tour.auto_cuts = tour.auto_cuts || { mine: 'front', others: 'eyes', pass: '' }, walker = tour.walker && E.objectById(tour.walker);
        const spoken = E.lines().filter(l => l.kind !== 'sound' && l.kind !== 'act' && (l.text || l.audio)).length;
        return [
            h('div', { class: 's360-subtitle', t: 'Cuts from the dialogue' }),
            h('p', { class: 's360-note', t: walker ? 'Shot and reverse shot: when another speaks, through its eyes; when it speaks, the second camera on it.' : 'Shot and reverse shot: whoever speaks is seen, the second camera on its face. (With a figure whose eyes the route is, the others are seen through its eyes.)' }),
            row(walker ? field('When it speaks', select(o, 'mine', [['front', 'Its face'], ['over', 'Over the shoulder of whom it speaks to'], ['side', 'Beside it']])) : null,
                walker ? field('When another speaks', select(o, 'others', [['eyes', 'Through its eyes (the main camera)'], ['front', 'The face of whoever speaks']])) : null,
                field('Each cut', select(o, 'pass', [['', 'Cut'], ['dissolve', 'Dissolve'], ['blink', 'A blink'], ['dip', 'Through black']]))),
            h('div', { class: 's360-actions' },
                button('Make the cuts from the dialogue', () => {
                    const made = E.dialogueCuts({ mine: o.mine, others: o.others, pass: o.pass || null, long: 0.5 });
                    if (!made.length) { say(T('The dialogue gives no cuts: it needs lines of figures that speak'), 'warn'); return; }
                    if ((tour.cuts || []).length && !confirm(T('Put {0} cuts made from the dialogue in place of the {1} there are?', made.length, tour.cuts.length))) return;
                    tour.cuts = made; E.touch('tour'); say(T('{0} cuts made from the dialogue', made.length)); renderPanel();
                }, { class: 's360-btn s360-primary', disabled: !spoken, tt: 'Puts the cuts in place of those there are. Make it again after the lines change' })),
        ];
    }

    /* the switch for "its head looks where the route looks" (on unless turned off) */
    const lookSwitch = tour => ({ on: tour.walker_looks !== false });

    /* the second camera: whose eyes the route is, whom the second camera follows and how, and when the film cuts */
    function secondPanel(tour) {
        const spec = tour.second = tour.second || Object.assign({ mode: 'side', side: '1', soft: 0.4, turn: 20 }, SECOND_WAYS.side);
        const cuts = tour.cuts = tour.cuts || [];
        // anything solid may be followed (a car, for a chase); only a figure on two legs can be somebody's eyes
        const solids = test => [['', '—']].concat(E.doc.worlds.flatMap(w => (w.objects || []).filter(o => o.model && !o.flat && test(o))
            .map(o => [o.id, (E.doc.worlds.length > 1 ? w.name + ': ' : '') + (o.name || o.id), false])));
        const figures = solids(() => true), people = solids(o => E.bodyOf(o) === 'biped' || o.id === tour.walker);
        const pick = UI.walkerPick = UI.walkerPick && UI.walkerPick.of === E.name ? UI.walkerPick : { of: E.name, id: tour.walker || '' };
        const at = UI.cutAt = UI.cutAt || { t: 0 }, playing = E.play && E.play.kind === 'tour';
        const now = () => +(playing ? E.play.t : +at.t || 0).toFixed(2);
        const addCut = cam => { cuts.push({ t: now(), cam }); cuts.sort((a, b) => a.t - b.t); E.touch('tour'); renderPanel(); };
        const walk = async () => {
            const object = E.objectById(pick.id);
            if (!object) return;
            await E.walkRoute(object);
            if (!spec.target) spec.target = object.id;
            if (E.options.motion && E.options.motion.available && E.bodyOf(object) === 'biped') {
                say(T('Giving it real steps...'));
                await E.walkObject(object, (object.motion && object.motion.style) || 'Neutral');
            }
            say(T('It walks along the route'));
            renderPanel();
        };
        // a cut: when, which camera and how the film goes over to it; a cut to the second camera has a second line:
        // whom it follows, how and from which side
        const list = cuts.map((cut, i) => h('div', { class: 's360-key s360-cut' },
            h('span', { class: 's360-keyn' }, String(i + 1)),
            h('label', { class: 's360-mini', tt: 'From this second' }, '⏱', number(cut, 't', { min: 0, max: 36000, step: 0.1 }, () => { cuts.sort((a, b) => a.t - b.t); E.touch('tour'); renderPanel(); })),
            h('span', { class: 's360-grow' }, select(cut, 'cam', [['a', 'Main camera (the route)'], ['b', 'Second camera']], () => { E.touch('tour'); renderPanel(); })),
            h('span', { class: 's360-pick', tt: 'How the film goes over to this shot' }, select(cut, 'pass', [['', 'Cut'], ['blink', 'A blink'], ['dip', 'Through black'], ['dissolve', 'Dissolve']], () => { E.touch('tour'); renderPanel(); })),
            cut.pass === 'dip' || cut.pass === 'dissolve' ? h('label', { class: 's360-mini', tt: 'Seconds it takes (the cut in its middle)' }, '↔', number(cut, 'long', { min: 0.1, max: 5, step: 0.1, fallback: 0.8 }, () => E.touch('tour'))) : null,
            h('button', { type: 'button', class: 's360-btn s360-icon', tt: 'Delete', on: { click: () => { cuts.splice(i, 1); E.touch('tour'); renderPanel(); } } }, '✕'),
            cut.cam === 'b' ? h('div', { class: 's360-cutmore' },
                h('span', { class: 's360-grow', tt: 'Whom this shot follows' }, select(cut, 'target', [['', 'Whom: as above']].concat(figures.slice(1)), () => { E.touch('tour'); renderPanel(); })),
                h('span', { class: 's360-grow' }, select(cut, 'way', [['', 'As set above']].concat(WAY_NAMES), () => { E.touch('tour'); renderPanel(); })),
                cut.way === 'tripod' ? h('button', { type: 'button', class: 's360-btn' + (cut.stand || spec.stand ? '' : ' s360-primary'), tt: 'This shot\'s tripod stands where the view is now (otherwise where the one above stands)',
                    on: { click: () => { cut.stand = standHere(); E.touch('tour'); renderPanel(); } } }, T(cut.stand ? 'Stands here ✓ (again)' : 'Stands here')) : null,
                cut.way && cut.way !== 'round' && cut.way !== 'tripod' ? h('span', { class: 's360-pick' }, select(cut, 'side', [['', 'Side as above'], ['1', 'Right'], ['-1', 'Left']], () => E.touch('tour'))) : null,
                h('label', { class: 's360-mini', tt: 'This shot\'s lens, in degrees: wide to see more of a small room, narrow to come close (0: as its way says)' }, '◎', number(cut, 'fov', { min: 0, max: 140, step: 1, fallback: 0 }, () => E.touch('tour')))) : null));
        return [
            h('div', { class: 's360-subtitle', t: 'Second camera' }),
            h('p', { class: 's360-note', t: 'Besides the route, a camera that follows one figure. The film cuts between the two: through somebody\'s eyes, then that somebody seen from the side.' }),
            row(field('Whose eyes the route is', select(pick, 'id', people, renderPanel), 'That figure walks where the route\'s camera goes and faces where it looks; while the film looks through its eyes it is not drawn'),
                h('div', { class: 's360-actions' },
                    pick.id ? button(tour.walker === pick.id ? 'Walk it along the route again' : 'Walk it along the route', walk, { disabled: tour.keys.length < 2, tt: 'Its path is made from the route (and its real steps, when AI4Animation is here). Again after the route changes' }) : null,
                    !pick.id && tour.walker ? button('The route is nobody\'s eyes', () => { delete tour.walker; E.touch('tour'); renderPanel(); }) : null)),
            tour.walker && E.objectById(tour.walker) ? h('p', { class: 's360-faint' }, T('The route is the eyes of {0}', E.objectById(tour.walker).name || '')) : null,
            tour.walker && E.objectById(tour.walker) ? check({ on: tour.walker_body !== false }, 'on', 'Its own body is seen through its eyes', 'Looking down through its eyes you see its hands, legs and feet walking (its head is not drawn: the camera is in it). Off: it is not drawn at all while the film looks through its eyes', on => { tour.walker_body = on; E.touch('tour'); }) : null,
            tour.walker && E.objectById(tour.walker) ? check(lookSwitch(tour), 'on', 'Its head looks where the route looks', 'In the second camera\'s shots its head (and a little of its chest) turns to what the film saw through its eyes', on => { tour.walker_looks = on; E.touch('tour'); }) : null,
            row(field('The second camera follows', select(spec, 'target', figures, renderPanel)),
                field('Where it stands', select(spec, 'mode', WAY_NAMES,
                    mode => { Object.assign(spec, SECOND_WAYS[mode] || {}); E.touch('tour'); renderPanel(); }))),
            ...(spec.mode === 'tripod' ? [
                row(h('div', { class: 's360-actions' }, button('The camera stands where I am now', () => { spec.stand = standHere(); E.touch('tour'); renderPanel(); }, { class: 's360-btn s360-primary', tt: 'Stand where it should be (fly there), look roughly at the figure, and press: in its shots it stays there and turns after the figure' })),
                    h('span', { class: 's360-faint', dir: 'auto' }, spec.stand ? T('It stands in {0}', (E.world(spec.stand.world) || {}).name || '?') + standLooks(spec.stand) : T('Not placed yet: until it is, it stands beside the figure'))),
                viewMeter(),
                row(field('Looks at (its heights)', slider(spec, 'aim', { min: 0, max: 1.3, step: 0.05 })), field('Lens (°)', slider(spec, 'fov', { min: 12, max: 90, step: 1 }))),
                row(field('Keeps it this big in the picture', slider(spec, 'frame', { min: 0, max: 0.9, step: 0.05 }), 'How much of the picture\'s height the figure fills: the lens closes in as it goes away and opens up as it comes near. 0: the lens as set'),
                    field('Follows softly (s)', slider(spec, 'soft', { min: 0, max: 1.5, step: 0.1 }), 'The camera follows where the figure is over this many seconds either side, so it does not shake with every step')),
            ] : [
                spec.mode === 'round' ? field('Turns (° a second)', slider(spec, 'turn', { min: -90, max: 90, step: 1 })) : field('On its', select(spec, 'side', [['1', 'Right'], ['-1', 'Left']])),
                row(field('Distance (its heights)', slider(spec, 'distance', { min: 0.5, max: 8, step: 0.1 })), field('Camera height (its heights)', slider(spec, 'height', { min: 0, max: 2, step: 0.05 }))),
                row(field('Looks at (its heights)', slider(spec, 'aim', { min: 0, max: 1.3, step: 0.05 })), field('Lens (°)', slider(spec, 'fov', { min: 12, max: 90, step: 1 }))),
                row(field('Looks ahead of it (its heights)', slider(spec, 'ahead', { min: 0, max: 4, step: 0.1 })), field('Follows softly (s)', slider(spec, 'soft', { min: 0, max: 1.5, step: 0.1 }), 'The camera follows where the figure is over this many seconds either side, so it does not shake with every step')),
            ]),
            h('div', { class: 's360-actions' },
                button('Look through it now', () => { if (!E.lookSecond(playing ? E.play.t : +at.t || 0)) say(T('Choose the figure it follows (a 3D figure)'), 'warn'); }, { disabled: !spec.target })),
            h('div', { class: 's360-subtitle', t: 'Cuts' }),
            shotStrip(tour, E.compile().duration),
            h('p', { class: 's360-note', t: playing ? 'The route is playing: "here" is the moment it is at.' : 'From the second given here, or from where the route is while it plays.' }),
            row(playing ? h('span', { class: 's360-faint' }, T('{0} s', E.play.t.toFixed(1))) : field('At second', number(at, 't', { min: 0, max: 36000, step: 0.1 })),
                h('div', { class: 's360-actions' },
                    button('Second camera from here', () => addCut('b'), { disabled: !spec.target }),
                    button('Main camera from here', () => addCut('a')))),
            ...(list.length ? list : [h('p', { class: 's360-faint', t: 'No cuts: the film is the route all the way.' })]),
            ...dialogueEdit(tour),
        ];
    }

    function routePanel() {
        if (!E.world()) return [h('p', { class: 's360-note', t: 'Press + below to add your first world.' })];
        const settings = E.doc.settings, tour = E.tour();
        if (settings.rig == null) settings.rig = 'pro';
        if (settings.rig_strength == null) settings.rig_strength = 1;
        if (settings.speed == null) settings.speed = 1.5;
        const compiled = E.compile();
        const keys = tour.keys.map((key, i) => {
            const world = E.world(key.world);
            return h('div', { class: 's360-key' + (key.world === E.worldId ? '' : ' s360-other') },
                h('span', { class: 's360-keyn' }, String(i + 1)),
                h('span', { class: 's360-keyname', dir: 'auto', tt: 'When the camera gets here' }, (world ? world.name : '?') + '  ' + minutes((compiled.keyTimes || [])[i] || 0)),
                h('button', { type: 'button', class: 's360-btn s360-icon', tt: 'More', on: { click: () => { UI.keyOpen = UI.keyOpen === i ? -1 : i; renderPanel(); } } }, '⋯'),
                i ? h('label', { class: 's360-mini', tt: 'Seconds to get here' }, '→', number(key, 'dur', { min: 0.2, max: 600, step: 0.5, fallback: 4 }, () => E.emit('tour'))) : h('span', { class: 's360-mini' }),
                h('label', { class: 's360-mini', tt: 'Seconds to wait here' }, '⏸', number(key, 'hold', { min: 0, max: 600, step: 0.5 }, () => E.emit('tour'))),
                h('button', { type: 'button', class: 's360-btn s360-icon', tt: 'Play the route from here', on: { click: guarded(() => E.playTour((compiled.keyTimes || [])[i] || 0)) } }, '▶'),
                h('button', { type: 'button', class: 's360-btn s360-icon', tt: 'Go there', on: { click: guarded(() => E.go(key.world, key)) } }, '◎'),
                h('button', { type: 'button', class: 's360-btn s360-icon', tt: 'Set to where I am', on: { click: () => { Object.assign(key, E.keyHere(), { dur: key.dur, hold: key.hold }); E.touch('tour'); } } }, '⟳'),
                h('button', { type: 'button', class: 's360-btn s360-icon', tt: 'Delete', on: { click: () => { tour.keys.splice(i, 1); E.touch('tour'); } } }, '✕'));
        });
        return [
            h('div', { class: 's360-subtitle', t: 'Camera' }),
            field('Carried as', select(settings, 'rig', RIG_NAMES)),
            row(field('Amount', slider(settings, 'rig_strength', { min: 0, max: 2, step: 0.1 })), field('Speed (m/s)', slider(settings, 'speed', { min: 0.3, max: 6, step: 0.1 }))),
            h('div', { class: 's360-subtitle', t: 'Drawn route' }),
            h('p', { class: 's360-note', t: 'The camera flies from key to key. Add keys from where you stand, or draw arrows on the map.' }),
            row(button('Key here', () => { E.addKey(); }, { class: 's360-btn s360-primary' }),
                h('label', { class: 's360-checkline', tt: 'Click on the map and drag: the arrow is where the camera will be and where it will look' },
                    h('input', { type: 'checkbox', class: 's360-check', checked: UI.mapArrows, on: { change: e => { UI.mapArrows = e.target.checked; if (UI.mapArrows) UI.map = true; } } }), h('span', { t: 'Arrows on the map' })),
                h('label', { class: 's360-checkline' }, h('input', { type: 'checkbox', class: 's360-check', checked: UI.map, on: { change: e => { UI.map = e.target.checked; } } }), h('span', { t: 'Map' }))),
            ...(keys.length ? keys.flatMap((row_, i) => UI.keyOpen === i ? [row_, keyMore(tour.keys[i], tour.keys[i - 1])] : [row_]) : [h('p', { class: 's360-note', t: 'No keys yet.' })]),
            compiled.duration > 0 ? h('p', { class: 's360-faint' }, T('Route: {0} seconds', compiled.duration.toFixed(1))) : null,
            h('div', { class: 's360-actions' },
                E.play && E.play.kind === 'tour' ? button('Stop', () => E.stopPlay()) : button('Play', () => E.playTour(), { disabled: tour.keys.length < 2 }),
                button('Clear route', () => { if (confirm(T('Delete all the keys of the route?'))) { tour.keys = []; E.touch('tour'); } }, { disabled: !tour.keys.length })),
            ...secondPanel(tour),
            h('div', { class: 's360-subtitle', t: 'My flight' }),
            h('p', { class: 's360-note', t: 'Fly yourself and record it; it is then drawn again at full quality.' }),
            flightSound(),
            h('div', { class: 's360-actions' },
                E.recording ? button('Stop recording', () => E.stopRecording(), { class: 's360-btn s360-danger' }) : button('Record my flight', () => { E.startRecording(); UI.stage.focus(); }),
                E.take ? (E.play && E.play.kind === 'take' ? button('Stop', () => E.stopPlay()) : button('Replay', () => E.playTake())) : null),
            E.take ? h('p', { class: 's360-faint' }, T('Recorded: {0} seconds', E.take.duration.toFixed(1))) : null,
            E.take && !E.recording ? row(h('div', { class: 's360-actions' }, button('Make it the route', guarded(async () => {
                const count = Core.takeToKeys(E.take.samples).length, had = E.tour().keys.length;
                if (had && !confirm(T('The flight becomes the route: {0} keys in place of the {1} there are?', count, had))) return;
                const keys = E.takeToRoute();
                if (UI.flight && UI.flight.file && UI.flight.into !== false) await E.addSound(UI.flight.file);
                say(T('The flight is the route now: {0} keys (arrows) to change as you like', keys.length));
                renderPanel();
            }), { class: 's360-btn s360-primary', tt: 'The flight becomes the route\'s keys (arrows on the map): a key where it bends, turns or stops, to move and change afterwards' })),
                UI.flight && UI.flight.file ? check(UI.flight, 'into', 'With its sound in the scene', 'The sound you flew to becomes a sound of the scene, from its start') : null) : null,
        ];
    }

    /* A sound to fly to: it plays from its start when the recording starts (and again from the same moment when the
     * flight is replayed), so the flight can follow the music. It stays in this page until the flight becomes the
     * route, when it can become a sound of the scene. */
    function flightSound() {
        const f = UI.flight = UI.flight || { into: true };
        return h('div', { class: 's360-line' }, h('span', { class: 's360-label', t: 'A sound to fly to' }),
            f.file ? h('span', { class: 's360-faint', dir: 'auto' }, f.file.name) : h('span', { class: 's360-faint', t: 'none' }),
            h('label', { class: 's360-btn s360-filebtn', tt: 'Music or a voice: it plays from its start while you record your flight' }, h('span', { t: f.file ? 'Another' : 'Choose…' }),
                h('input', { type: 'file', accept: 'audio/*,.wav,.mp3,.m4a,.ogg,.flac', on: { change: e => {
                    const file = e.target.files[0]; e.target.value = '';
                    if (!file) return;
                    if (f.audio) { f.audio.pause(); URL.revokeObjectURL(f.audio.src); }
                    f.file = file; f.audio = new Audio(URL.createObjectURL(file)); f.playing = false;
                    renderPanel();
                } } })),
            f.file ? h('button', { type: 'button', class: 's360-btn s360-icon', tt: 'No sound', on: { click: () => { if (f.audio) { f.audio.pause(); URL.revokeObjectURL(f.audio.src); } f.file = null; f.audio = null; renderPanel(); } } }, '✕') : null);
    }
    // the sound to fly to plays while a flight is recorded or replayed, from the moment it is at
    function flightSoundFollows() {
        const f = UI.flight;
        if (!f || !f.audio) return;
        const replay = E.play && E.play.kind === 'take', on = !!E.recording || replay;
        if (on && !f.playing) { f.audio.currentTime = replay ? E.play.t || 0 : 0; f.audio.play().catch(() => {}); f.playing = true; }
        else if (!on && f.playing) { f.audio.pause(); f.playing = false; }
    }
    E.on('take', flightSoundFollows);
    E.on('play', flightSoundFollows);

    const RESOLUTIONS = ['1280x720', '1920x1080', '2560x1440', '3840x2160', '1080x1920', '1080x1080'];
    const FORMATS = [['mp4-h264', 'MP4 (H.264): plays everywhere'], ['mp4-nvenc', 'MP4 (H.264, NVIDIA card): much faster to make'], ['mp4-h265', 'MP4 (H.265): smaller files'], ['webm-vp9', 'WebM (VP9): for the web'],
        ['mov-prores', 'MOV (ProRes): for editing, very large'], ['gif', 'GIF: short loops'], ['png', 'PNG pictures: one per frame']];

    function recordPanel() {
        if (!E.world()) return [h('p', { class: 's360-note', t: 'Press + below to add your first world.' })];
        const r = UI.record, x = E.exporting, result = UI.exportResult;
        const custom = row(field('Width', number(r, 'width', { min: 64, max: 8192, step: 2 })), field('Height', number(r, 'height', { min: 64, max: 8192, step: 2 })));
        custom.style.display = r.size === 'custom' ? '' : 'none';
        const duration = r.source === 'take' ? (E.take ? E.take.duration : 0) : E.compile().duration;
        const facts = h('p', { class: 's360-faint' }, duration > 0 ? T('{0} frames, {1} seconds', Math.ceil(duration * r.fps), duration.toFixed(1)) : T(r.source === 'take' ? 'Record a flight first' : 'Add at least two keys to the route'));
        const crf = r.kind === 'webm-vp9' ? { min: 10, max: 45 } : { min: 8, max: 32 };
        const start = async () => {
            UI.exportResult = null;
            const done = await E.exportVideo(r);
            UI.exportResult = done;
            say(T('The video is ready'));
            renderPanel();
        };
        return [
            field('What to record', select(r, 'source', [['tour', 'The drawn route'], ['take', 'My recorded flight']], renderPanel)),
            facts,
            row(field('Resolution', select(r, 'size', RESOLUTIONS.map(s => [s, s.replace('x', ' × '), false]).concat([['custom', 'Custom']]), value => {
                if (value !== 'custom') { const [w, hh] = value.split('x').map(Number); r.width = w; r.height = hh; }
                renderPanel();
            })), field('Frames per second', select(r, 'fps', [24, 25, 30, 50, 60].map(f => [f, String(f), false]), v => { r.fps = +v; renderPanel(); }))),
            custom,
            field('Format', select(r, 'kind', FORMATS, renderPanel)),
            ['mp4-h264', 'mp4-nvenc', 'mp4-h265', 'webm-vp9'].includes(r.kind) ? field('Quality', h('span', { class: 's360-slider' }, h('span', { class: 's360-faint', t: 'best' }),
                h('input', { type: 'range', class: 's360-range', min: crf.min, max: crf.max, step: 1, value: r.quality, on: { input: e => { r.quality = +e.target.value; e.target.nextSibling.nextSibling.textContent = 'CRF ' + r.quality; } } }),
                h('span', { class: 's360-faint', t: 'smallest' }), h('span', { class: 's360-value' }, 'CRF ' + r.quality))) : null,
            field('Supersampling', select(r, 'supersample', [[1, '1 ×', false], [1.5, '1.5 ×', false], [2, '2 ×', false]], v => { r.supersample = +v; }), 'Drawn larger and scaled down (smoother edges)'),
            field('View', select(r, 'fov_scale', [[1, 'As the camera has it'], [1.1, 'A little wider'], [1.25, 'Wider'], [0.9, 'A little closer']], v => { r.fov_scale = +v; }), 'An upright video shows only the middle of the picture: a wider view gives it back some of the sides'),
            field('File name', text(r, 'name')),
            E.lines().some(l => l.audio) ? row(check(r, 'sound', 'With the dialogue\'s sound'),
                field('Subtitles', select(r, 'subtitles', [['file', 'A file beside the video (.srt)'], ['burn', 'Drawn into the picture'], ['none', 'None']]))) : null,
            E.doc.worlds.some(w => w.weather && w.weather.kind) ? check(r, 'weather_sound', 'With the sound of the weather', 'Rain and snow are heard while the camera is in them') : null,
            E.lines().some(l => l.audio) && r.sound !== false ? row(
                check(r, 'loudness', 'As loud as the web plays videos', 'YouTube, TikTok and the rest play everything at one loudness (-14 LUFS). A quieter video stays quiet beside the others. With this the whole sound is turned up or down by one amount to that loudness, without squashing it'),
                E.lines().some(l => l.audio && l.kind === 'sound') ? field('Sounds step back under speech (dB)', number(r, 'duck', { min: 0, max: 24, step: 1 }), 'Music and other sounds are this much quieter while somebody speaks, and come back in the gaps. 0: they stay as they are') : null) : null,
            h('div', { class: 's360-actions' },
                x ? button('Cancel', () => { x.cancel = true; }, { class: 's360-btn s360-danger' }) : button('Render the video', start, { class: 's360-btn s360-primary', disabled: duration <= 0 }),
                button('Open the folder', () => E.call('/open-folder', { body: { project: E.name, exports: true } }))),
            h('p', { class: 's360-faint', dir: 'ltr' }, E.options.recordings || ''),
            x ? h('div', { class: 's360-progressline' }, h('div', { class: 's360-progressfill', style: `width: ${(100 * x.frame / x.total).toFixed(1)}%` }), h('span', {}, T('Rendering: frame {0} of {1}', x.frame, x.total))) : null,
            result ? h('div', { class: 's360-result' },
                /\.(mp4|webm)$/i.test(result.path) ? h('video', { class: 's360-video', src: result.url, controls: true, loop: true, muted: !result.sound, playsInline: true }) : null,
                result.note ? h('div', { class: 's360-faint s360-warn', dir: 'auto' }, T(result.note)) : null,
                h('div', { class: 's360-faint', dir: 'ltr' }, `${result.path}`),
                h('div', { class: 's360-faint', dir: 'ltr' }, `${result.width}×${result.height} · ${result.frames} f · ${(result.bytes / 1048576).toFixed(1)} MB · ${result.seconds} s`),
                result.loudness && result.loudness.lufs != null ? h('div', { class: 's360-faint', dir: 'auto' }, T('Sound: {0} LUFS, highest peak {1} dB (it was {2} LUFS)', result.loudness.lufs.toFixed(1), result.loudness.peak == null ? '?' : result.loudness.peak.toFixed(1), result.loudness.before.toFixed(1))) : null) : null,
        ];
    }

    /* ---- the project: saving, versions, copies ------------------------------------------------------------ */

    const clock = seconds => new Date(seconds * 1000).toLocaleString(S.hebrew() ? 'he-IL' : undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
    const megabytes = bytes => (bytes / 1048576).toFixed(bytes > 104857600 ? 0 : 1) + ' MB';

    async function loadVersions() {
        const name = E.name;
        try { const answer = await E.versions(); if (E.name === name) { UI.versions = Object.assign(answer, { of: name }); if (UI.tab === 'project') renderPanel(); } } catch (e) { /* the list stays as it was */ }
    }

    function projectPanel() {
        if (!E.doc) return [h('p', { class: 's360-note', t: 'No project yet. Create one to begin.' }), button('Create a project', newProject, { class: 's360-btn s360-primary' })];
        if (!UI.versions || UI.versions.of !== E.name) { UI.versions = { of: E.name, versions: [], usage: null, loading: true }; loadVersions(); }
        const v = UI.versions, label = UI.versionLabel = UI.versionLabel || { text: '' };
        const rows = (v.versions || []).map(version => h('div', { class: 's360-version' },
            h('span', { class: 's360-versionwhen' }, clock(version.saved_at)),
            h('span', { class: 's360-versionwhat', dir: 'auto' }, version.label || T(version.auto ? 'automatic' : version.file.startsWith('before-restore') ? 'before a restore' : 'saved by you'), ' · ', T('{0} worlds', version.worlds)),
            button('Restore', async () => { if (confirm(T('Go back to the project as it was on {0}? (The project as it is now is kept as a version first.)', clock(version.saved_at)))) { await E.restoreVersion(version.file); say(T('The version was restored')); loadVersions(); } }),
            h('button', { type: 'button', class: 's360-btn s360-icon', tt: 'Delete', on: { click: guarded(async () => { await E.deleteVersion(version.file); loadVersions(); }) } }, '✕')));
        return [
            h('div', { class: 's360-subtitle s360-first', t: 'Saving' }),
            h('p', { class: 's360-note', t: 'The project saves itself after every change. Every few minutes the state before a change is also kept as a version, and you can keep one yourself at any moment. Nothing a version needs is ever erased, so any version can be brought back whole.' }),
            row(text(label, 'text', { placeholder: 'A name for this version (optional)' }),
                button('Keep a version', async () => { await E.saveVersion(label.text); label.text = ''; say(T('The version was kept')); loadVersions(); }, { class: 's360-btn s360-primary' })),
            h('div', { class: 's360-subtitle', t: 'Versions' }),
            ...(rows.length ? rows : [h('p', { class: 's360-note', t: v.loading ? 'Reading…' : 'No versions yet.' })]),
            h('div', { class: 's360-subtitle', t: 'Copies' }),
            h('div', { class: 's360-actions' },
                button('Duplicate the project', async () => { const name = prompt(T('Name of the copy:'), E.name + ' 2'); if (name && name.trim()) await E.duplicateProject(name.trim()); }),
                button('Pack as a .zip', async () => { const packed = await E.packProject(); UI.packed = packed; say(T('The project was packed')); renderPanel(); }, { tt: 'The whole project in one file: to keep, or to open on another computer' }),
                h('label', { class: 's360-btn s360-filebtn', tt: 'Opens a packed project as a new project' }, h('span', { t: 'Open a .zip' }),
                    h('input', { type: 'file', accept: '.zip,application/zip', on: { change: guarded(async e => { const file = e.target.files[0]; e.target.value = ''; if (file) await E.unpackProject(file); }) } }))),
            UI.packed && UI.packed.path ? h('p', { class: 's360-faint', dir: 'ltr' }, h('a', { href: UI.packed.url, download: UI.packed.file }, UI.packed.path), ` · ${megabytes(UI.packed.bytes)}`) : null,
            h('div', { class: 's360-subtitle', t: 'On the disk' }),
            h('p', { class: 's360-faint', dir: 'ltr' }, v.folder || ''),
            v.usage ? h('p', { class: 's360-faint' }, T('The project takes {0}', megabytes(v.usage.bytes))) : null,
            h('div', { class: 's360-actions' },
                button('Open the project folder', () => E.call('/open-folder', { body: { project: E.name } })),
                button('Delete project', deleteProject, { class: 's360-btn s360-danger' })),
        ];
    }

    function renderSaved() {
        if (!UI.saved) return;
        const at = E.savedAt ? new Date(E.savedAt).toLocaleTimeString(S.hebrew() ? 'he-IL' : undefined, { hour: '2-digit', minute: '2-digit' }) : '';
        UI.saved.textContent = !E.doc ? '' : E.saveFailed ? '⚠ ' + T('Not saved') : E.unsaved ? '● ' + T('Saving…') : at ? '✓ ' + T('Saved {0}', at) : '✓ ' + T('Saved');
        UI.saved.dataset.kind = E.saveFailed ? 'error' : E.unsaved ? 'warn' : 'ok';
    }

    /* ---- the strip of worlds ----------------------------------------------------------------------------- */

    function renderStrip() {
        if (!UI.strip) return;
        if (!E.doc) { UI.strip.replaceChildren(); return; }
        const cards = E.doc.worlds.map(world => {
            const job = Array.from(E.jobs.values()).find(j => j.world === world.id);
            return h('button', { type: 'button', class: 's360-world' + (world.id === E.worldId ? ' s360-on' : ''), on: { click: guarded(async () => { if (UI.arrivalPick) return; await E.go(world.id); UI.stage.focus(); }) } },
                world.thumb ? h('img', { src: E.fileUrl(world, world.thumb), alt: '', draggable: false }) : h('span', { class: 's360-empty', t: 'empty' }),
                h('span', { class: 's360-worldname', dir: 'auto' }, world.name),
                world.kind === 'video' ? h('span', { class: 's360-badge', t: 'video' }) : null,
                E.doc.start_world === world.id ? h('span', { class: 's360-badge s360-first', t: 'first' }) : null,
                job ? h('span', { class: 's360-worldbusy', style: `width: ${Math.round((job.progress || 0) * 100)}%` }) : null);
        });
        cards.push(h('button', { type: 'button', class: 's360-world s360-add', tt: 'Add a world', on: { click: guarded(async () => { const world = E.addWorld(T('World {0}', E.doc.worlds.length + 1)); await E.go(world.id); UI.tab = 'world'; renderAll(); }) } }, '+'));
        UI.strip.replaceChildren(...cards);
    }

    const JOB_NAMES = { world: 'Generating the world', hires: 'Hires. fix', character: 'Drawing the character', object: 'Drawing the object', select: 'Selecting', import: 'Taking the file in', import_object: 'Taking the file in', depth: 'Measuring the depth', speech: 'Making the voice', talk: 'Making it talk', solid: '3D model', solid_home: 'Hunyuan3D into Forge', skin: 'Drawing the material' };

    function renderJobs() {
        if (!UI.jobsBox) return;
        const jobs = Array.from(E.jobs.values());
        UI.jobsBox.style.display = jobs.length ? '' : 'none';
        UI.jobsBox.replaceChildren(...jobs.map(job => h('div', { class: 's360-job' },
            h('div', { class: 's360-jobfill', style: `width: ${Math.round((job.progress || 0) * 100)}%` }),
            h('span', {}, `${T(JOB_NAMES[job.kind] || job.kind)} · ${T(job.status === 'waiting' ? 'waiting' : job.stage || 'waiting')}${job.progress ? ' · ' + Math.round(job.progress * 100) + '%' : ''}`),
            h('button', { type: 'button', class: 's360-btn s360-icon', tt: 'Cancel', on: { click: () => E.cancelJobs() } }, '✕'))));
    }

    /* ---- the stage: mouse, keys, and what is drawn over the view ------------------------------------------ */

    const point = event => { const r = UI.over.getBoundingClientRect(); return { x: event.clientX - r.left, y: event.clientY - r.top, w: r.width, h: r.height }; };
    // The map: in the bottom right corner (a third of the stage's height), large (most of its height), or all of
    // the stage (UI.mapSize 0, 1, 2: the button in its corner, or the N key); the wheel zooms it.
    const mapRect = () => {
        const w = UI.over.clientWidth, hh = UI.over.clientHeight, big = UI.mapSize || 0;
        const size = big === 2 ? Math.max(84, Math.min(w, hh) - 24) : Math.round(big === 1 ? Core.clamp(hh * 0.66, 160, 620) : Core.clamp(hh * 0.34, 84, 190));
        return big === 2 ? { x: (w - size) / 2, y: (hh - size) / 2, size } : { x: w - size - 12, y: hh - size - 12, size };
    };
    const mapButton = () => { const m = mapRect(); return { x: m.x + 4, y: m.y + 4, size: 20 }; };
    const onMapButton = p => { if (!UI.map) return false; const b = mapButton(); return p.x >= b.x && p.y >= b.y && p.x <= b.x + b.size && p.y <= b.y + b.size; };
    const inMap = p => { if (!UI.map) return false; const m = mapRect(); return p.x >= m.x && p.y >= m.y && p.x <= m.x + m.size && p.y <= m.y + m.size; };

    function mapScale() {
        const world = E.world(), far = world ? +world.far || 14 : 14;
        return (mapRect().size / 2 - 8) / Math.max(2, far * 0.8) * (UI.mapZoom || 1);
    }
    function mapToWorld(p) { const m = mapRect(), k = mapScale(); return [(p.x - m.x - m.size / 2) / k, (p.y - m.y - m.size / 2) / k]; }

    /* The frame around the chosen object, with its handles: squares on the frame change its size (a corner:
     * all of it; a side: its width or its height), and the round ones turn it (above: in the picture's plane;
     * right: tipping it; below: round its own upright) and bring it nearer or take it farther (left).
     * Returns {x0, y0, x1, y1, cx, cy, handles: [{kind, x, y, round}]} in the stage's pixels, or null. */
    function objectFrame(object, w, hh) {
        const vp = Core.viewProjection(E.cam, w / hh), sheet = E.objectSheet(object, E.cam.pos, E.objectNow(object)), box = object.box || [0, 0, 1, 1];
        const points = [[box[0], box[1]], [box[2], box[1]], [box[2], Math.min(1, box[3])], [box[0], Math.min(1, box[3])]].map(([u, v]) => {
            const x = (object.flip ? 1 - u : u) - 0.5, y = 1 - v;
            return Core.project(vp, [0, 1, 2].map(j => sheet.anchor[j] + sheet.right[j] * x * sheet.width + sheet.up[j] * y * sheet.height));
        });
        if (points.some(p => !p)) return null;
        const xs = points.map(p => p[0] * w), ys = points.map(p => p[1] * hh);
        const x0 = Math.min(...xs) - 8, x1 = Math.max(...xs) + 8, y0 = Math.min(...ys) - 8, y1 = Math.max(...ys) + 8, cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
        if (x1 - x0 > w * 3 || y1 - y0 > hh * 3) return null;
        const handles = [
            { kind: 'size', x: x0, y: y0 }, { kind: 'size', x: x1, y: y0 }, { kind: 'size', x: x1, y: y1 }, { kind: 'size', x: x0, y: y1 },
            { kind: 'wide', x: x0, y: cy }, { kind: 'wide', x: x1, y: cy }, { kind: 'tall', x: cx, y: y0 }, { kind: 'tall', x: cx, y: y1 },
            { kind: 'roll', x: cx, y: y0 - 30, round: '⟳' }, { kind: 'tilt', x: x1 + 30, y: cy, round: '⤒' }, { kind: 'turn', x: cx, y: y1 + 30, round: '⟲' }, { kind: 'near', x: x0 - 30, y: cy, round: '⇕' },
        ];
        return { x0, y0, x1, y1, cx, cy, handles };
    }

    function frameHit(p) {
        const object = UI.tab === 'character' && !E.character && !E.play ? E.object() : null;
        if (!object || object.held) return null;
        const frame = objectFrame(object, p.w, p.h);
        if (!frame) return null;
        const handle = frame.handles.find(h => Math.hypot(h.x - p.x, h.y - p.y) < (h.round ? 14 : 9));
        if (!handle) return null;
        const now = E.objectNow(object);
        return { kind: 'frame-handle', handle: handle.kind, object, start: p, cx: frame.cx, cy: frame.cy,
            was: { size: +object.size || 1.7, wide: +object.wide || 1, tall: +object.tall || 1, tilt: +object.tilt || 0, roll: +object.roll || 0, pos: object.pos.slice(),
                turn: object.turn == null ? Core.anglesFromDir(...E.objectSheet(Object.assign({}, object, { tilt: 0, roll: 0 }), E.cam.pos, now).normal).yaw : +object.turn } };
    }

    function frameDrag(d, p) {
        const o = d.object, was = d.was, far = (a, b) => Math.max(4, Math.hypot(a.x - d.cx, a.y - d.cy)) / Math.max(4, Math.hypot(b.x - d.cx, b.y - d.cy));
        if (d.handle === 'size') o.size = +Core.clamp(was.size * far(p, d.start), 0.03, 80).toFixed(3);
        else if (d.handle === 'wide') o.wide = +Core.clamp(was.wide * Math.max(4, Math.abs(p.x - d.cx)) / Math.max(4, Math.abs(d.start.x - d.cx)), 0.05, 20).toFixed(3);
        else if (d.handle === 'tall') o.tall = +Core.clamp(was.tall * Math.max(4, Math.abs(p.y - d.cy)) / Math.max(4, Math.abs(d.start.y - d.cy)), 0.05, 20).toFixed(3);
        else if (d.handle === 'roll') o.roll = Math.round(Core.wrapDeg(was.roll - (Math.atan2(p.y - d.cy, p.x - d.cx) - Math.atan2(d.start.y - d.cy, d.start.x - d.cx)) / Core.RAD));
        else if (d.handle === 'tilt') o.tilt = Math.round(Core.clamp(was.tilt + (p.y - d.start.y) * 0.5, -180, 180));
        else if (d.handle === 'turn') o.turn = Math.round(Core.wrapDeg(was.turn - (p.x - d.start.x) * 0.6));
        else if (d.handle === 'near') { const k = Math.exp((p.y - d.start.y) * 0.004); o.pos = [+(was.pos[0] * k).toFixed(3), was.pos[1], +(was.pos[2] * k).toFixed(3)]; }
        E.touch();
    }

    UI.frame = { of: objectFrame, hit: frameHit, drag: frameDrag };       // (for tests: the handles without a mouse)

    function characterHit(p) {
        const c = E.character;
        if (!c || c.pending || c.busy || UI.tab !== 'character') return null;
        const crop = E.characterCrop(), x0 = crop.x0 * p.w, y0 = crop.y0 * p.h, fw = (crop.x1 - crop.x0) * p.w, fh = (crop.y1 - crop.y0) * p.h;
        if (c.mode === 'pose') {
            for (let i = 0; i < c.joints.length; i++) {
                const j = c.joints[i];
                if (j && Math.hypot(x0 + j[0] * fw - p.x, y0 + j[1] * fh - p.y) < 9) return { kind: 'joint', index: i, x0, y0, fw, fh };
            }
        }
        if (Math.hypot(x0 + fw - p.x, y0 + fh - p.y) < 14) return { kind: 'size' };
        if (p.x >= x0 && p.x <= x0 + fw && p.y >= y0 && p.y <= y0 + fh) return { kind: 'frame' };
        return null;
    }

    function wireStage() {
        const over = UI.over;
        over.style.cursor = 'grab';
        over.addEventListener('contextmenu', e => e.preventDefault());
        over.addEventListener('pointerdown', event => {
            UI.stage.focus();
            const p = point(event);
            over.setPointerCapture(event.pointerId);
            const lookOnly = event.button !== 0;
            if (!lookOnly && onMapButton(p)) { UI.mapSize = ((UI.mapSize || 0) + 1) % 3; return; }
            if (!lookOnly && inMap(p)) {
                const at = mapToWorld(p);
                UI.drag = UI.mapArrows && UI.tab === 'route' ? { kind: 'arrow', from: at, to: at } : { kind: 'map' };
                if (UI.drag.kind === 'map' && !E.play) { const held = Core.constrain([at[0], E.cam.pos[1], at[1]], E.field(), 0.69); E.cam.pos = held.pos; E.auto = null; }
                return;
            }
            const hit = lookOnly ? null : (characterHit(p) || frameHit(p));
            if (hit) { UI.drag = Object.assign(hit, { last: p, kind: hit.kind }); return; }
            if (!lookOnly && UI.tab === 'character' && !E.character && !E.play) {
                // an object: a click chooses it, a drag moves it along the ground
                const object = E.pickObject(p.x / p.w, p.y / p.h);
                E.choose(object ? object.id : null);
                if (object) {
                    const feet = Core.project(Core.viewProjection(E.cam, E.aspect()), E.objectFeet(object));
                    UI.drag = { kind: 'object', object, start: p, moved: false, off: feet ? [p.x / p.w - feet[0], p.y / p.h - feet[1]] : [0, 0] };
                    return;
                }
            }
            const tool = lookOnly || UI.arrivalPick || !(UI.tab === 'portals' || (UI.tab === 'character' && E.character && E.character.mode === 'mask')) ? 'look' : UI.tool;
            if (tool === 'look') { UI.drag = { kind: 'look', last: p }; over.style.cursor = 'grabbing'; return; }
            const subtract = (UI.subtract !== event.altKey) || tool === 'erase';
            if (tool === 'wand') {
                UI.drag = null;
                guarded(async () => { await E.wand({ x: p.x / p.w, y: p.y / p.h, wanted: !event.shiftKey }, subtract); renderTools(); })();
                return;
            }
            UI.drag = { kind: tool, start: p, last: p, points: [p], subtract };
        });
        over.addEventListener('pointermove', event => {
            const d = UI.drag, p = point(event);
            UI.pointer = p;
            if (!d) return;
            if (d.kind === 'look') { E.look[0] += p.x - d.last.x; E.look[1] += p.y - d.last.y; d.last = p; E.auto = null; }
            else if (d.kind === 'map') { if (!E.play) { const at = mapToWorld(p), held = Core.constrain([at[0], E.cam.pos[1], at[1]], E.field(), 0.69); E.cam.pos = held.pos; } }
            else if (d.kind === 'arrow') d.to = mapToWorld(p);
            else if (d.kind === 'object') {
                if (!d.moved && Math.hypot(p.x - d.start.x, p.y - d.start.y) > 4) d.moved = true;
                if (d.moved) E.moveObjectTo(d.object, p.x / p.w - d.off[0], p.y / p.h - d.off[1]);
            }
            else if (d.kind === 'frame-handle') frameDrag(d, p);
            else if (d.kind === 'joint') { const c = E.character; c.joints[d.index] = [(p.x - d.x0) / d.fw, (p.y - d.y0) / d.fh]; }
            else if (d.kind === 'frame') { const f = E.character.frame; f.cx += (p.x - d.last.x) / p.w; f.cy += (p.y - d.last.y) / p.h; d.last = p; }
            else if (d.kind === 'size') { const f = E.character.frame; f.h = Core.clamp(2 * (p.y / p.h - f.cy), 0.15, 1.2); }
            else { d.last = p; d.points.push(p); }
        });
        const release = event => {
            const d = UI.drag;
            UI.drag = null;
            over.style.cursor = UI.tool === 'look' || !(UI.tab === 'portals' || UI.tab === 'character') ? 'grab' : 'crosshair';
            if (!d) return;
            if (d.kind === 'object') { if (d.moved && UI.tab === 'character') renderPanel(); return; }
            if (d.kind === 'frame-handle') { E.touch('objects'); return; }
            if (d.kind === 'arrow') {
                const dx = d.to[0] - d.from[0], dz = d.to[1] - d.from[1];
                const yaw = Math.hypot(dx, dz) > 0.05 ? Math.atan2(dx, -dz) / Core.RAD : E.cam.yaw;
                const at = Core.constrain([d.from[0], 0, d.from[1]], E.field(), 0.6).pos;      // inside the world
                E.addKey({ world: E.worldId, pos: at.map(v => +v.toFixed(3)), yaw: +yaw.toFixed(1), pitch: 0, fov: +E.cam.fov.toFixed(1), dur: 4, hold: 0 });
                return;
            }
            if (!['rect', 'ellipse', 'lasso', 'brush', 'erase'].includes(d.kind)) return;
            const p = d.last, w = Math.round(p.w), hh = Math.round(p.h);
            const shape = document.createElement('canvas');
            shape.width = w; shape.height = hh;
            const ctx = shape.getContext('2d');
            ctx.fillStyle = ctx.strokeStyle = '#fff';
            drawShape(ctx, d);
            E.selectShape(shape, d.subtract);
            if (UI.tab === 'portals') renderPanel();
        };
        over.addEventListener('pointerup', release);
        over.addEventListener('pointercancel', release);
        over.addEventListener('dblclick', event => { if (UI.tool === 'look' || UI.tab === 'world' || UI.tab === 'route') { const p = point(event); if (!inMap(p)) E.enter(); } });
        over.addEventListener('wheel', event => {
            event.preventDefault();
            if (inMap(point(event))) { UI.mapZoom = Core.clamp((UI.mapZoom || 1) * Math.pow(0.9985, event.deltaY), 0.4, 8); return; }      // (over the map: the map zooms)
            E.fovWanted = Core.clamp(E.fovWanted * Math.pow(1.0012, event.deltaY), 12, 125);
        }, { passive: false });

        const MOVE = new Set(['KeyW', 'KeyA', 'KeyS', 'KeyD', 'KeyQ', 'KeyE', 'ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'ShiftLeft', 'ShiftRight', 'Equal', 'Minus', 'NumpadAdd', 'NumpadSubtract']);
        UI.stage.addEventListener('keydown', event => {
            if (event.target !== UI.stage || event.ctrlKey || event.metaKey) return;
            const code = event.code || event.key;
            let used = true;
            if (MOVE.has(code)) E.keys.add(code);
            else if (code === 'Space') E.enter();
            else if (code === 'Backspace') E.back();
            else if (code === 'KeyF') fullScreen();
            else if (code === 'KeyM') UI.map = !UI.map;
            else if (code === 'KeyN') { UI.map = true; UI.mapSize = ((UI.mapSize || 0) + 1) % 3; }
            else if (code === 'KeyK') E.addKey();
            else if (code === 'KeyR') { const w = E.world(); if (w) E.go(w.id); }
            else if (code === 'Escape') { setTool('look'); E.auto = null; if (E.play) E.stopPlay(); }
            else used = false;
            if (used) { event.preventDefault(); event.stopPropagation(); }
        });
        UI.stage.addEventListener('keyup', event => { E.keys.delete(event.code || event.key); });
        UI.stage.addEventListener('blur', () => E.keys.clear());
    }

    function drawShape(ctx, d) {
        const a = d.start, b = d.last;
        ctx.lineCap = ctx.lineJoin = 'round';
        if (d.kind === 'rect') ctx.fillRect(Math.min(a.x, b.x), Math.min(a.y, b.y), Math.abs(b.x - a.x), Math.abs(b.y - a.y));
        else if (d.kind === 'ellipse') { ctx.beginPath(); ctx.ellipse((a.x + b.x) / 2, (a.y + b.y) / 2, Math.abs(b.x - a.x) / 2, Math.abs(b.y - a.y) / 2, 0, 0, Math.PI * 2); ctx.fill(); }
        else {
            ctx.beginPath();
            d.points.forEach((p, i) => i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y));
            if (d.kind === 'lasso') { ctx.closePath(); ctx.fill(); }
            else { ctx.lineWidth = UI.brush; if (d.points.length === 1) ctx.lineTo(d.points[0].x + 0.01, d.points[0].y); ctx.stroke(); }
        }
    }

    /* Where the second camera goes in each of its shots, in the world on the screen: [[x, z], ...] runs. Worked
     * out once for each state of the cuts and the camera (not frame by frame); a figure that has not come yet
     * is looked for again after a second. */
    function secondPath() {
        const tour = E.tour(), sign = JSON.stringify([tour.cuts, tour.second, tour.walker, tour.keys.length, E.worldId]), now = performance.now();
        const kept = UI.secondPath;
        if (kept && kept.sign === sign && (kept.runs.length || now - kept.at < 1000)) return kept.runs;
        const cuts = (tour.cuts || []).filter(c => c && isFinite(+c.t)).slice().sort((a, b) => a.t - b.t), duration = E.compile().duration, runs = [];
        cuts.forEach((cut, i) => {
            if (cut.cam !== 'b') return;
            const end = Math.min(duration, i + 1 < cuts.length ? +cuts[i + 1].t : duration), run = [];
            for (let t = +cut.t; t <= end + 1e-6; t += 0.25) { const m = E.secondAt(t, null, cut); if (m && m.world === E.worldId) run.push([m.pos[0], m.pos[2]]); }
            if (run.length) runs.push(run);
        });
        UI.secondPath = { sign, runs, at: now };
        return runs;
    }

    /* How the world looks from a place: a world is a picture pushed out by its depth, and the nearer a camera is to
     * its surfaces (the ground too), the more what it sees is stretched. */
    const VIEW_LEVELS = [[0.3, 'as pictured', '#6fdc8c'], [0.45, 'a little stretched', '#c7e36b'], [0.6, 'stretched', '#ffb347'], [Infinity, 'very stretched', '#ff6b6b']];
    function viewFrom(pos, worldId) {
        const field = E.field(worldId);
        if (!field || !field.n) return null;
        const share = Core.constrain(pos, field, 10).share, level = VIEW_LEVELS.find(l => share < l[0]);
        return { share, text: level[1], colour: level[2] };
    }

    function drawMap(ctx) {
        const m = mapRect(), k = mapScale(), cx = m.x + m.size / 2, cy = m.y + m.size / 2, field = E.field(), world = E.world();
        ctx.save();
        ctx.beginPath(); ctx.roundRect(m.x, m.y, m.size, m.size, 10); ctx.fillStyle = 'rgba(8, 10, 16, 0.66)'; ctx.fill(); ctx.clip();
        // the world at eye height, seen from above
        ctx.beginPath();
        for (let i = 0; i <= 120; i++) {
            const yaw = i * 3, d = Core.dirFromAngles(yaw, 0), r = field.distTowards(d[0], d[1], d[2]);
            const x = cx + d[0] * r * k, y = cy + d[2] * r * k;
            if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y);
        }
        ctx.closePath(); ctx.fillStyle = 'rgba(120, 140, 170, 0.16)'; ctx.fill(); ctx.strokeStyle = 'rgba(190, 205, 230, 0.55)'; ctx.lineWidth = 1; ctx.stroke();
        (world.portals || []).forEach((portal, i) => {
            const c = portal.center || { yaw: 0 }, span = portal.span || 8, tint = E.TINTS[i % E.TINTS.length].map(v => Math.round(v * 255)).join(',');
            ctx.beginPath();
            for (let a = -span; a <= span; a += 2) {
                const d = Core.dirFromAngles(c.yaw + a, 0), r = field.distTowards(d[0], d[1], d[2]);
                const x = cx + d[0] * r * k, y = cy + d[2] * r * k;
                if (a === -span) ctx.moveTo(x, y); else ctx.lineTo(x, y);
            }
            ctx.strokeStyle = `rgb(${tint})`; ctx.lineWidth = 4; ctx.stroke();
        });
        // the route's keys in this world, as arrows
        const arrow = (x, z, yaw, colour, label) => {
            const d = Core.dirFromAngles(yaw, 0), px = cx + x * k, py = cy + z * k, len = 15;
            ctx.strokeStyle = ctx.fillStyle = colour; ctx.lineWidth = 2;
            ctx.beginPath(); ctx.moveTo(px, py); ctx.lineTo(px + d[0] * len, py + d[2] * len); ctx.stroke();
            ctx.beginPath(); ctx.arc(px, py, 4, 0, Math.PI * 2); ctx.fill();
            const tipX = px + d[0] * len, tipY = py + d[2] * len, side = Core.dirFromAngles(yaw + 90, 0);
            ctx.beginPath(); ctx.moveTo(tipX + d[0] * 5, tipY + d[2] * 5); ctx.lineTo(tipX + side[0] * 4, tipY + side[2] * 4); ctx.lineTo(tipX - side[0] * 4, tipY - side[2] * 4); ctx.closePath(); ctx.fill();
            if (label) { ctx.font = '10px sans-serif'; ctx.fillText(label, px + 6, py - 6); }
        };
        if (UI.tab === 'route' && E.doc.tours.length) {
            const keys = E.tour().keys;
            ctx.beginPath(); let started = false;
            keys.forEach(key => { if (key.world !== E.worldId) { started = false; return; } const x = cx + key.pos[0] * k, y = cy + key.pos[2] * k; if (started) ctx.lineTo(x, y); else ctx.moveTo(x, y); started = true; });
            ctx.strokeStyle = 'rgba(255, 210, 90, 0.6)'; ctx.lineWidth = 1.5; ctx.setLineDash([4, 3]); ctx.stroke(); ctx.setLineDash([]);
            keys.forEach((key, i) => { if (key.world === E.worldId) arrow(key.pos[0], key.pos[2], key.yaw, '#ffd25a', String(i + 1)); });
        }
        if (UI.tab === 'route' && E.doc.tours.length) {
            // the second camera: the way it goes in its shots, and where its tripods stand
            const tour = E.tour(), second = tour.second || {};
            ctx.strokeStyle = 'rgba(255, 159, 67, 0.85)'; ctx.lineWidth = 2; ctx.setLineDash([2, 3]);
            for (const run of secondPath()) {
                ctx.beginPath();
                run.forEach((p, i) => { const x = cx + p[0] * k, y = cy + p[1] * k; if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y); });
                ctx.stroke();
            }
            ctx.setLineDash([]);
            const tripod = (stand, label) => {
                if (!stand || !stand.pos || stand.world !== E.worldId) return;
                const x = cx + stand.pos[0] * k, y = cy + stand.pos[2] * k;
                ctx.fillStyle = '#ff9f43'; ctx.strokeStyle = '#1b1e26'; ctx.lineWidth = 1;
                ctx.beginPath(); ctx.roundRect(x - 5, y - 4, 10, 8, 2); ctx.fill(); ctx.stroke();
                ctx.beginPath(); ctx.moveTo(x + 5, y); ctx.lineTo(x + 9, y - 3); ctx.lineTo(x + 9, y + 3); ctx.closePath(); ctx.fill();
                ctx.font = '10px sans-serif'; ctx.fillText(label, x + 8, y - 6);
            };
            if (second.mode === 'tripod' || (tour.cuts || []).some(c => c.way === 'tripod' && !c.stand)) tripod(second.stand, T('tripod'));
            (tour.cuts || []).forEach((c, i) => { if (c.cam === 'b' && c.way === 'tripod') tripod(c.stand, String(i + 1)); });
        }
        if (UI.drag && UI.drag.kind === 'arrow') {
            const d = UI.drag, yaw = Math.atan2(d.to[0] - d.from[0], -(d.to[1] - d.from[1])) / Core.RAD;
            arrow(d.from[0], d.from[1], yaw, '#fff');
        }
        // the button that makes the map larger, and how much it is zoomed
        const b = mapButton();
        ctx.fillStyle = 'rgba(255, 255, 255, 0.16)'; ctx.beginPath(); ctx.roundRect(b.x, b.y, b.size, b.size, 4); ctx.fill();
        ctx.fillStyle = '#e8eaf0'; ctx.font = '13px sans-serif'; ctx.fillText(UI.mapSize === 2 ? '⤡' : '⤢', b.x + 4, b.y + 15);
        if ((UI.mapZoom || 1) !== 1) { ctx.font = '10px sans-serif'; ctx.fillText('×' + (UI.mapZoom || 1).toFixed(1), b.x + b.size + 4, b.y + 14); }
        // the camera: where it is and what it sees
        const px = cx + E.cam.pos[0] * k, py = cy + E.cam.pos[2] * k, half = E.cam.fov * E.aspect() / 2 * 0.62;
        const left = Core.dirFromAngles(E.cam.yaw - half, 0), right = Core.dirFromAngles(E.cam.yaw + half, 0);
        ctx.beginPath(); ctx.moveTo(px, py); ctx.lineTo(px + left[0] * 34, py + left[2] * 34); ctx.lineTo(px + right[0] * 34, py + right[2] * 34); ctx.closePath();
        ctx.fillStyle = 'rgba(80, 200, 255, 0.28)'; ctx.fill();
        ctx.beginPath(); ctx.arc(px, py, 4.5, 0, Math.PI * 2); ctx.fillStyle = '#50c8ff'; ctx.fill(); ctx.strokeStyle = '#fff'; ctx.lineWidth = 1; ctx.stroke();
        ctx.restore();
    }

    function drawOver() {
        const over = UI.over, ratio = Math.min(window.devicePixelRatio || 1, 2);
        const w = over.clientWidth, hh = over.clientHeight;
        if (over.width !== Math.round(w * ratio) || over.height !== Math.round(hh * ratio)) { over.width = Math.round(w * ratio); over.height = Math.round(hh * ratio); }
        const ctx = UI.octx;
        ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
        ctx.clearRect(0, 0, w, hh);
        UI.stage.classList.toggle('s360-small', w < 760);          // a small stage: the tools show only their signs
        if (!E.doc || !E.world()) return;
        const x = E.exporting;
        if (x) {
            ctx.fillStyle = 'rgba(0, 0, 0, 0.55)'; ctx.fillRect(0, 0, w, hh);
            ctx.fillStyle = '#fff'; ctx.font = '600 18px sans-serif'; ctx.textAlign = 'center';
            ctx.fillText(T('Rendering: frame {0} of {1}', x.frame, x.total), w / 2, hh / 2 - 12);
            ctx.fillStyle = 'rgba(255,255,255,0.2)'; ctx.fillRect(w * 0.2, hh / 2 + 6, w * 0.6, 8);
            ctx.fillStyle = '#50c8ff'; ctx.fillRect(w * 0.2, hh / 2 + 6, w * 0.6 * x.frame / x.total, 8);
            ctx.textAlign = 'start';
            return;
        }
        const d = UI.drag, c = E.character;
        // the shape being drawn
        if (d && ['rect', 'ellipse', 'lasso', 'brush', 'erase'].includes(d.kind)) {
            ctx.save(); ctx.globalAlpha = 0.45; ctx.fillStyle = ctx.strokeStyle = d.subtract ? '#ff5a5a' : '#34c6ff';
            drawShape(ctx, d); ctx.restore();
        }
        if ((UI.tool === 'brush' || UI.tool === 'erase') && UI.tab === 'portals' && UI.pointer && !d) {
            ctx.beginPath(); ctx.arc(UI.pointer.x, UI.pointer.y, UI.brush / 2, 0, Math.PI * 2); ctx.strokeStyle = 'rgba(255,255,255,0.8)'; ctx.lineWidth = 1; ctx.stroke();
        }
        // the frame of a character, and its figure
        if (c && UI.tab === 'character' && !c.pending) {
            const crop = E.characterCrop(), x0 = crop.x0 * w, y0 = crop.y0 * hh, fw = (crop.x1 - crop.x0) * w, fh = (crop.y1 - crop.y0) * hh;
            ctx.save();
            ctx.fillStyle = 'rgba(0, 0, 0, 0.28)';
            ctx.beginPath(); ctx.rect(0, 0, w, hh); ctx.rect(x0, y0, fw, fh); ctx.fill('evenodd');
            ctx.strokeStyle = c.busy ? '#ffd25a' : '#fff'; ctx.lineWidth = 1.5; ctx.setLineDash([7, 5]); ctx.strokeRect(x0, y0, fw, fh); ctx.setLineDash([]);
            ctx.fillStyle = '#fff'; ctx.fillRect(x0 + fw - 7, y0 + fh - 7, 14, 14);
            if (c.mode === 'pose') { ctx.translate(x0, y0); ctx.globalAlpha = 0.92; E.drawPose(ctx, c.joints, fw, fh, Math.max(2.5, fh / 130)); }
            ctx.restore();
        }
        // the chosen object: its name over its head
        const chosen = UI.tab === 'character' && !c ? E.object() : null;
        if (chosen) {
            const now = E.objectNow(chosen), sheet = E.objectSheet(chosen, E.cam.pos, now), box = chosen.box || [0, 0, 1, 1];
            const head = Core.project(Core.viewProjection(E.cam, w / hh), [now.pos[0], now.pos[1] + sheet.height * (1 - box[1]), now.pos[2]]);
            if (head) {
                const label = chosen.name || T('Object {0}', (E.world().objects || []).indexOf(chosen) + 1);
                ctx.font = '600 13px sans-serif'; ctx.textAlign = 'center';
                const tw = ctx.measureText(label).width + 18, x = head[0] * w, y = Math.max(34, head[1] * hh - 12);
                ctx.fillStyle = 'rgba(255, 210, 90, 0.92)'; ctx.beginPath(); ctx.roundRect(x - tw / 2, y - 22, tw, 22, 11); ctx.fill();
                ctx.fillStyle = '#201800'; ctx.fillText(label, x, y - 6); ctx.textAlign = 'start';
            }
            const frame = !E.play && !chosen.held ? objectFrame(chosen, w, hh) : null;
            if (frame) {
                ctx.lineWidth = 1; ctx.strokeStyle = 'rgba(255, 255, 255, 0.75)';
                ctx.strokeRect(frame.x0 + 0.5, frame.y0 + 0.5, frame.x1 - frame.x0, frame.y1 - frame.y0);
                ctx.font = '15px sans-serif'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
                for (const handle of frame.handles) {
                    if (handle.round) {
                        ctx.beginPath(); ctx.arc(handle.x, handle.y, 12, 0, Math.PI * 2);
                        ctx.fillStyle = 'rgba(238, 240, 250, 0.94)'; ctx.fill(); ctx.strokeStyle = 'rgba(40, 44, 90, 0.9)'; ctx.stroke();
                        ctx.fillStyle = '#23264f'; ctx.fillText(handle.round, handle.x, handle.y + 1);
                    } else {
                        ctx.fillStyle = '#fff'; ctx.fillRect(handle.x - 4, handle.y - 4, 8, 8);
                        ctx.strokeStyle = 'rgba(40, 44, 90, 0.9)'; ctx.strokeRect(handle.x - 3.5, handle.y - 3.5, 7, 7);
                    }
                }
                ctx.textAlign = 'start'; ctx.textBaseline = 'alphabetic';
            }
        }
        // what the middle of the view points at
        if (!E.play && !c && !chosen) {
            ctx.beginPath(); ctx.arc(w / 2, hh / 2, 2, 0, Math.PI * 2); ctx.fillStyle = 'rgba(255,255,255,0.7)'; ctx.fill();
            const aimed = E.cross ? null : E.aimed();
            if (aimed) {
                const target = E.world(aimed.target), label = (target ? '→ ' + target.name : (aimed.name || '?')) + (target ? '   ' + T('Space: enter') : '');
                ctx.font = '600 14px sans-serif'; ctx.textAlign = 'center';
                const tw = ctx.measureText(label).width + 22;
                ctx.fillStyle = 'rgba(8, 10, 16, 0.7)'; ctx.beginPath(); ctx.roundRect(w / 2 - tw / 2, hh / 2 + 18, tw, 26, 13); ctx.fill();
                ctx.fillStyle = '#fff'; ctx.fillText(label, w / 2, hh / 2 + 36); ctx.textAlign = 'start';
            }
        }
        // the route's keys, standing in the world
        if (UI.tab === 'route' && E.doc.tours.length && !E.play) {
            const vp = Core.viewProjection(E.cam, w / hh);
            E.tour().keys.forEach((key, i) => {
                if (key.world !== E.worldId) return;
                const at = Core.project(vp, key.pos), dir = Core.dirFromAngles(key.yaw, key.pitch);
                const tip = Core.project(vp, [key.pos[0] + dir[0] * 0.6, key.pos[1] + dir[1] * 0.6, key.pos[2] + dir[2] * 0.6]);
                if (!at) return;
                ctx.strokeStyle = ctx.fillStyle = '#ffd25a'; ctx.lineWidth = 2.5;
                if (tip) { ctx.beginPath(); ctx.moveTo(at[0] * w, at[1] * hh); ctx.lineTo(tip[0] * w, tip[1] * hh); ctx.stroke(); }
                ctx.beginPath(); ctx.arc(at[0] * w, at[1] * hh, 9, 0, Math.PI * 2); ctx.fill();
                ctx.fillStyle = '#111'; ctx.font = '600 11px sans-serif'; ctx.textAlign = 'center'; ctx.fillText(String(i + 1), at[0] * w, at[1] * hh + 4); ctx.textAlign = 'start';
            });
        }
        if (E.recording) {
            ctx.fillStyle = '#ff4040'; ctx.beginPath(); ctx.arc(22, 22, 7, 0, Math.PI * 2); ctx.fill();
            ctx.fillStyle = '#fff'; ctx.font = '600 14px sans-serif'; ctx.fillText(`${T('REC')}  ${(E.clock - E.recording.started).toFixed(1)}`, 36, 27);
        }
        if (E.play) {
            ctx.fillStyle = 'rgba(255,255,255,0.25)'; ctx.fillRect(12, hh - 10, w - 24, 4);
            ctx.fillStyle = '#ffd25a'; ctx.fillRect(12, hh - 10, (w - 24) * Math.min(1, E.play.t / E.play.duration), 4);
        }
        if (E.pad) { ctx.font = '12px sans-serif'; ctx.fillStyle = 'rgba(255,255,255,0.75)'; ctx.fillText('🎮 ' + T('Gamepad'), 12, hh - 18); }
        if (UI.map) drawMap(ctx);
    }

    function renderHud() {
        const world = E.world();
        const text_ = world ? `${world.name}${world.width ? `  ·  ${world.width}×${world.height}` : ''}` : '';
        if (UI.hud.textContent !== text_) UI.hud.textContent = text_;
    }

    /* ---- the loop ---------------------------------------------------------------------------------------- */

    function frame(now) {
        requestAnimationFrame(frame);
        if (!UI.root || UI.root.offsetParent === null) { UI.lastFrame = 0; return; }
        const dt = UI.lastFrame ? Math.min(0.1, (now - UI.lastFrame) / 1000) : 0.016;
        UI.lastFrame = now;
        tick(dt);
    }

    function tick(dt) {
        if (!E.gl) return;
        try {
            E.steady = UI.tab === 'portals' || UI.tab === 'character' || !!UI.arrivalPick;
            E.step(dt);
            if (!E.exporting) E.gl.render(E.renderState(false));
            drawOver();
            renderHud();
            const meter = UI.viewMeter;
            if (meter && meter.isConnected && !E.play && (UI.meterAt = (UI.meterAt || 0) + 1) % 6 === 0) {
                const v = viewFrom(E.cam.pos, E.worldId);
                meter.textContent = v ? T('From where you are now the world looks {0} ({1}% of the way to its surfaces)', T(v.text), Math.round(v.share * 100)) : '';
                meter.style.color = v ? v.colour : '';
            }
            const head = UI.cutHead;
            if (head && head.el.isConnected) {
                const playing = E.play && E.play.kind === 'tour';
                head.el.style.display = playing ? '' : 'none';
                if (playing) head.el.style.left = (100 * Core.clamp(E.play.t / head.duration, 0, 1)) + '%';
            }
        } catch (e) {
            if (!UI.loopError) { UI.loopError = true; console.error('[Simulation 360]', e); say(String(e.message || e), 'error'); }
        }
    }
    UI.tick = tick;             // for tests: a hidden page gets no animation frames

    let panelTimer = null;
    const later = fn => { clearTimeout(panelTimer); panelTimer = setTimeout(fn, 30); };
    E.on('project', () => { renderProjects(); renderPanel(); renderStrip(); });
    E.on('worlds', () => { renderStrip(); });
    E.on('world', () => { renderStrip(); renderPanel(); renderTools(); });
    E.on('portals', () => { if (UI.tab === 'portals') renderPanel(); });
    E.on('selection', () => { if (UI.tab === 'portals') later(renderPanel); });
    E.on('character', () => { renderBanner(); renderTools(); if (UI.tab === 'character') renderPanel(); });
    E.on('objects', () => { if (UI.tab === 'character') renderPanel(); UI.secondPath = null; });
    E.on('saved', renderSaved);
    E.on('tour', () => { if (UI.tab === 'route' || UI.tab === 'record') renderPanel(); });
    E.on('play', () => { if (UI.tab === 'route' || UI.tab === 'dialogue') renderPanel(); });
    E.on('tour', () => { if (UI.tab === 'dialogue') later(renderPanel); });
    E.on('lines', () => { if (UI.tab === 'dialogue' || UI.tab === 'record') later(renderPanel); });
    E.on('take', () => { if (UI.tab === 'route' || UI.tab === 'record') renderPanel(); });
    E.on('export', () => { if (UI.tab === 'record') renderPanel(); });
    let hadJobs = 0;
    E.on('jobs', () => { renderJobs(); renderStrip(); if ((E.jobs.size > 0) !== (hadJobs > 0)) renderPanel(); hadJobs = E.jobs.size; });

    async function start() {
        const host = document.getElementById('sim360_host');
        if (!host || UI.started) return;
        UI.started = true;
        const mount = host.querySelector('.sim360-mount');
        const prose = mount && mount.closest('.prose');
        const place = h('div', { class: 's360-place' });
        // outside Gradio's own text block: a value update of the block would wipe what is inside it
        if (prose) prose.insertAdjacentElement('afterend', place); else host.append(place);
        build(place);
        try {
            await E.start(UI.gl);
            if (!E.projects.length) await E.createProject(T('My worlds'));      // the first visit starts ready to use
        } catch (e) {
            say(T(String(e.message || e)), 'error');
            console.error('[Simulation 360]', e);
        }
        renderAll();
        requestAnimationFrame(frame);
    }
    UI.start = start;

    if (typeof onUiLoaded === 'function') onUiLoaded(start);
    else document.addEventListener('DOMContentLoaded', start);
    window.addEventListener('beforeunload', () => { if (E.doc) E.save(); });
})();

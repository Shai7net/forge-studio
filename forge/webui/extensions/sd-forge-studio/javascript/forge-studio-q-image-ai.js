/* Forge Studio - generating in the image workspace (imagegen.py on the server), with the checkpoint chosen at the top
 * of Forge, the five ways of Forge's own tabs:
 *   words to a picture (txt2img) · the picture again (img2img) · sketch (paint colours on it first) ·
 *   inpaint (only the selection drawn again) · inpaint sketch (the selection again, keeping what is painted in it).
 * SD 1.5 / SDXL add ControlNet units (Forge's own: every preprocessor and model, the picture from the shot, a layer or
 * a file); FLUX 3 / FIBO 2.0 show their native controls instead (references, the prompt writer, keep the size).
 * Each result lands on a new layer (for inpaint only inside the selection); the other variations swap into it.
 */
(function () {
    'use strict';
    const FS = window.ForgeStudio = window.ForgeStudio || {};
    const S = FS.state;
    const T = (...a) => FS.T(...a);
    const q = encodeURIComponent;
    const IM = FS.imageShop;
    if (!IM) return;
    const G = IM.gen = {mode: 'txt2img', units: [], refs: [], opts: null};

    const MODES = [['txt2img', 'From words'], ['img2img', 'The picture again'], ['sketch', 'Sketch'],
        ['inpaint', 'Inpaint (the selection)'], ['inpaint-sketch', 'Inpaint sketch']];
    const NOTES = {
        txt2img: 'A new picture from the words, on a new layer.',
        img2img: 'The whole shot drawn again; how much it changes is the strength.',
        sketch: 'Paint rough colours and shapes on the shot first (brush), then draw it again from them.',
        inpaint: 'Only the selection is drawn again; the rest stays as it is.',
        'inpaint-sketch': 'Paint colours inside the selection first: they guide what is drawn there.'};

    async function loadOptions() {
        G.opts = await FS.api('image/genoptions');
        return G.opts;
    }

    function section(box, h) {
        const status = h('div', {class: 'fx-dim'});
        const modeRow = h('div', {class: 'im-modes'});
        const note = h('div', {class: 'fx-dim im-note'});
        const model = h('div', {class: 'im-model fx-dim'});
        const prompt = h('textarea', {class: 'fx-input fx-textarea', rows: 3, dir: 'auto', placeholder: T('What the picture shows (Hebrew is fine)')});
        const negative = h('input', {class: 'fx-input', dir: 'auto', placeholder: T('Not wanted (optional)')});
        const num = (value, min, max, step) => h('input', {type: 'number', class: 'fx-num', value, min, max, step});
        const steps = num(25, 1, 150, 1), cfg = num(7, 1, 30, 0.5), seed = num(-1, -1, 4294967295, 1), count = num(1, 1, 4, 1);
        const sampler = h('select', {class: 'fx-select'}), scheduler = h('select', {class: 'fx-select'});
        const denoise = h('input', {type: 'range', min: 0.05, max: 1, step: 0.05, value: 0.6, class: 'fx-range'});
        const denoiseText = h('span', {class: 'fx-dim'}, '0.60');
        denoise.addEventListener('input', () => { denoiseText.textContent = (+denoise.value).toFixed(2); });
        const fill = h('select', {class: 'fx-select'}, ...['original', 'fill', 'latent noise', 'latent nothing'].map((v) => h('option', {value: v}, T('fill:' + v))));
        const onlyMasked = h('input', {type: 'checkbox', checked: true});
        const blur = num(4, 0, 64, 1), padding = num(32, 0, 256, 4);
        const row = (label, ...el) => h('label', {class: 'im-opt im-row'}, h('span', {}, T(label)), ...el);
        const strengthRow = row('Strength', denoise, denoiseText);
        const inpaintBox = h('div', {class: 'im-sub'}, row('Masked content', fill), h('label', {class: 'im-opt'}, onlyMasked, T('Draw only the selected area at full size')),
            row('Soft edge of the mask', blur), row('Room around it', padding));
        // ControlNet (SD 1.5 / SDXL)
        const units = h('div', {class: 'im-units'});
        const cnBox = h('div', {class: 'im-sub'}, h('div', {class: 'im-subtitle'}, 'ControlNet', h('span', {class: 'fx-spacer'}),
            h('button', {class: 'fx-btn', onclick: () => { if (G.units.length < 3) { G.units.push({module: 'None', model: 'None', weight: 1, start: 0, end: 1, mode: 'Balanced', source: 'flat'}); drawUnits(); } }}, '＋ ' + T('Unit'))), units);
        // FLUX 3 / FIBO 2.0 native controls
        const writer = h('select', {class: 'fx-select'}, ...[['off', 'Off'], ['auto', 'Auto'], ['always', 'Always']].map(([v, l]) => h('option', {value: v}, T(l))));
        const keepSize = h('input', {type: 'checkbox', checked: true});
        const refs = h('div', {class: 'im-units'});
        const nativeBox = h('div', {class: 'im-sub'}, h('div', {class: 'im-subtitle'}, T('Native controls'), h('span', {class: 'fx-spacer'}),
            h('button', {class: 'fx-btn', onclick: () => { if (G.refs.length < 3) { G.refs.push({source: 'flat'}); drawRefs(); } }}, '＋ ' + T('Reference'))),
            row('Prompt writer (any language)', writer), h('label', {class: 'im-opt'}, keepSize, T('Keep the size of the source')), refs);
        const results = h('div', {class: 'im-results'});
        const go = h('button', {class: 'fx-primary', onclick: FS.guard(() => run())}, '✨ ' + T('Generate'));
        const refresh = h('button', {class: 'fx-icon', title: T('Read the model chosen at the top of Forge again'), onclick: FS.guard(() => apply(true))}, '⟳');

        function sources(sel, value) {
            const opts = [['flat', T('The picture (all visible layers)')], ...IM.layerList().map((l) => ['layer:' + l.id, T('Layer: {0}', l.name)]), ['file', T('A picture from the computer…')]];
            sel.replaceChildren(...opts.map(([v, l]) => h('option', {value: v, selected: v === value}, l)));
        }
        function pickFile(target) {
            const input = h('input', {type: 'file', accept: 'image/*'});
            input.addEventListener('change', () => { target.file = input.files[0] || null; target.fileName = target.file ? target.file.name : ''; drawUnits(); drawRefs(); });
            input.click();
        }
        function drawUnits() {
            const o = G.opts || {controlnet: {models: [], modules: []}};
            units.replaceChildren(...G.units.map((u, i) => {
                const mod = h('select', {class: 'fx-select'}, h('option', {value: 'None'}, T('No preprocessor')), ...o.controlnet.modules.map((m) => h('option', {value: m, selected: m === u.module}, m)));
                const mdl = h('select', {class: 'fx-select'}, h('option', {value: 'None'}, T('Choose a model…')), ...o.controlnet.models.map((m) => h('option', {value: m, selected: m === u.model}, m)));
                const src = h('select', {class: 'fx-select'});
                sources(src, u.source);
                const w = h('input', {type: 'number', class: 'fx-num', value: u.weight, min: 0, max: 2, step: 0.05});
                const st = h('input', {type: 'number', class: 'fx-num', value: u.start, min: 0, max: 1, step: 0.05});
                const en = h('input', {type: 'number', class: 'fx-num', value: u.end, min: 0, max: 1, step: 0.05});
                const md = h('select', {class: 'fx-select'}, ...['Balanced', 'My prompt is more important', 'ControlNet is more important'].map((m) => h('option', {value: m, selected: m === u.mode}, T(m))));
                mod.addEventListener('change', () => { u.module = mod.value; });
                mdl.addEventListener('change', () => { u.model = mdl.value; });
                src.addEventListener('change', () => { u.source = src.value; if (u.source === 'file') pickFile(u); });
                w.addEventListener('change', () => { u.weight = +w.value; });
                st.addEventListener('change', () => { u.start = +st.value; });
                en.addEventListener('change', () => { u.end = +en.value; });
                md.addEventListener('change', () => { u.mode = md.value; });
                return h('div', {class: 'im-unit'}, h('div', {class: 'im-subtitle'}, T('Unit {0}', i + 1), h('span', {class: 'fx-spacer'}),
                    h('button', {class: 'fx-icon', title: T('Remove'), onclick: () => { G.units.splice(i, 1); drawUnits(); }}, '✕')),
                    row('Preprocessor', mod), row('Model', mdl), row('Picture', src), u.fileName ? h('div', {class: 'fx-dim'}, u.fileName) : null,
                    row('Weight', w), row('From – to', st, en), row('Control mode', md));
            }));
        }
        function drawRefs() {
            refs.replaceChildren(...G.refs.map((r, i) => {
                const src = h('select', {class: 'fx-select'});
                sources(src, r.source);
                src.addEventListener('change', () => { r.source = src.value; if (r.source === 'file') pickFile(r); });
                return h('div', {class: 'im-unit'}, h('div', {class: 'im-subtitle'}, T('Reference {0}', i + 1), h('span', {class: 'fx-spacer'}),
                    h('button', {class: 'fx-icon', title: T('Remove'), onclick: () => { G.refs.splice(i, 1); drawRefs(); }}, '✕')), src, r.fileName ? h('div', {class: 'fx-dim'}, r.fileName) : null);
            }));
        }
        function setMode(m) {
            G.mode = m;
            modeRow.querySelectorAll('button').forEach((b) => b.classList.toggle('on', b.dataset.mode === m));
            note.textContent = T(NOTES[m]);
            strengthRow.hidden = m === 'txt2img';
            inpaintBox.hidden = !(m === 'inpaint' || m === 'inpaint-sketch') || (G.opts && G.opts.family === 'fibo');
            if (m !== 'txt2img' && !G.touchedStrength) { denoise.value = m === 'img2img' ? 0.55 : 0.75; denoiseText.textContent = (+denoise.value).toFixed(2); }
            if (m === 'sketch' || m === 'inpaint-sketch') IM.tool('brush');
            if (m === 'inpaint' || m === 'inpaint-sketch') { if (!IM.selection()) FS.message(T('Select the area to draw again (selection tools on the left)')); }
        }
        denoise.addEventListener('change', () => { G.touchedStrength = true; });
        modeRow.replaceChildren(...MODES.map(([m, l]) => h('button', {class: 'fx-btn', 'data-mode': m, onclick: () => setMode(m)}, T(l))));

        async function apply(reload) {
            const o = reload || !G.opts ? await loadOptions() : G.opts;
            const fam = {flux3: 'FLUX 3', fibo: 'FIBO 2.0', sdxl: 'SDXL', sd15: 'SD 1.5'}[o.family] || T('another model');
            model.replaceChildren(h('span', {}, T('Draws with: {0} ({1})', o.checkpoint || '?', fam)), refresh);
            sampler.replaceChildren(...o.samplers.map((x) => h('option', {value: x, selected: x === o.defaults.sampler}, x)));
            scheduler.replaceChildren(h('option', {value: ''}, T('Automatic')), ...o.schedulers.map((x) => h('option', {value: x}, x)));
            steps.value = o.defaults.steps;
            cfg.value = o.defaults.cfg;
            const native = ['flux3', 'fibo'].includes(o.family);
            cnBox.hidden = native || !o.controlnet.models.length && !o.controlnet.modules.length;
            nativeBox.hidden = !native;
            if (!o.api && !native) status.textContent = T('Forge\'s API is off: start Forge with --api to generate here');
            drawUnits();
            drawRefs();
            setMode(G.mode);
        }

        async function blobOf(source, target) {
            if (source === 'flat') return new Promise((r) => IM.flatten().toBlob(r, 'image/png'));
            if (source.startsWith('layer:')) return IM.layerBlob(source.slice(6));
            if (source === 'file' && target.file) return target.file;
            throw new Error(T('Choose a picture for every unit'));
        }

        async function run() {
            if (!G.opts) await apply(true);
            const mode = G.mode, text = prompt.value.trim();
            if (mode === 'txt2img' && !text) throw new Error(T('Write what the picture shows'));
            const sel = IM.selection();
            if ((mode === 'inpaint' || mode === 'inpaint-sketch') && !sel) throw new Error(T('Select the area to draw again first'));
            const form = new FormData();
            form.append('name', S.project);
            form.append('shot', IM.doc().shot);
            form.append('mode', mode);
            const english = text ? await FS.english(text, 'image') : '';
            const params = {prompt: english, negative: negative.value.trim(), steps: +steps.value, cfg: +cfg.value, sampler: sampler.value,
                scheduler: scheduler.value, seed: +seed.value, count: Math.max(1, Math.min(4, +count.value || 1)), denoise: +denoise.value,
                fill: fill.value, only_masked: onlyMasked.checked, mask_blur: +blur.value, padding: +padding.value,
                writer: writer.value, keep_size: keepSize.checked};
            form.append('params', JSON.stringify(params));
            if (mode !== 'txt2img') form.append('flat', await blobOf('flat'), 'flat.png');
            if (mode === 'inpaint' || mode === 'inpaint-sketch') form.append('mask', await IM.maskBlob(), 'mask.png');
            const native = ['flux3', 'fibo'].includes(G.opts.family);
            if (!native) {
                const list = G.units.filter((u) => u.model !== 'None' || u.module !== 'None');
                form.append('units', JSON.stringify(list.map((u) => ({module: u.module, model: u.model, weight: u.weight, guidance_start: u.start,
                    guidance_end: u.end, control_mode: u.mode, pixel_perfect: true}))));
                for (let i = 0; i < list.length; i++) form.append('cn:' + i, await blobOf(list[i].source, list[i]), 'cn.png');
            } else {
                for (let i = 0; i < G.refs.length; i++) form.append('ref:' + i, await blobOf(G.refs[i].source, G.refs[i]), 'ref.png');
            }
            go.disabled = true;
            status.textContent = T('Asking Forge…');
            try {
                const r = await fetch('/forge-studio/image/generate', {method: 'POST', body: form});
                if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
                const task = await FS.follow(await r.json(), (t) => { status.textContent = T(t.text || 'Working…'); });
                const imgs = await Promise.all(task.files.map((f) => new Promise((ok, bad) => {
                    const im = new Image();
                    im.onload = () => ok(im);
                    im.onerror = () => bad(new Error(T('Could not read the picture')));
                    im.src = `/forge-studio/image/file?name=${q(S.project)}&shot=${q(IM.doc().shot)}&file=${q(f)}`;
                })));
                const masked = mode === 'inpaint' || mode === 'inpaint-sketch';
                const name = '✨ ' + (text || T(MODES.find((x) => x[0] === mode)[1])).slice(0, 28);
                const id = IM.addResult(imgs[0], name, masked ? sel : null);
                showResults(imgs, id, task.seeds || []);
                status.textContent = T('Done: {0}', task.text);
            } finally { go.disabled = false; }
        }

        function showResults(imgs, layerId, seeds) {
            results.replaceChildren(...imgs.map((im, i) => {
                const b = h('button', {class: 'im-result' + (i === 0 ? ' on' : ''), title: seeds[i] != null ? T('Seed {0}', seeds[i]) : ''}, im);
                b.addEventListener('click', () => {
                    if (!IM.hasLayer(layerId)) return;
                    IM.swapResult(layerId, im);
                    results.querySelectorAll('.im-result').forEach((x) => x.classList.toggle('on', x === b));
                });
                return b;
            }));
        }

        box.append(h('div', {class: 'fx-group im-gen'},
            h('div', {class: 'fx-group-title'}, '✨ ' + T('Generate with Forge')),
            model, modeRow, note, prompt, negative,
            h('div', {class: 'im-grid'}, row('Steps', steps), row('CFG', cfg), row('Sampler', sampler), row('Schedule', scheduler),
                row('Seed', seed), row('Variations', count)),
            strengthRow, inpaintBox, cnBox, nativeBox, go, status, results));
        apply(false).catch((e) => { status.textContent = e.message; });
    }

    /* ---- the AI tools: the subject, an object by a click, the background away, what is selected away ---- */

    const fileUrl = (f) => `/forge-studio/image/file?name=${q(S.project)}&shot=${q(IM.doc().shot)}&file=${q(f)}`;
    const load = (src) => new Promise((ok, bad) => { const im = new Image(); im.onload = () => ok(im); im.onerror = () => bad(new Error(T('Could not read the picture'))); im.src = src; });
    const flatBlob = () => new Promise((r) => IM.flatten().toBlob(r, 'image/png'));
    async function ask(route, fields, label) {
        const form = new FormData();
        form.append('name', S.project);
        form.append('shot', IM.doc().shot);
        for (const [k, v] of Object.entries(fields)) { if (v instanceof Blob) form.append(k, v, k + '.png'); else form.append(k, String(v)); }
        IM.status(label);
        const r = await fetch('/forge-studio/image/' + route, {method: 'POST', body: form});
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
        const task = await FS.follow(await r.json(), (t) => IM.status(T(t.text || 'Working…')));
        IM.status(T(task.text || ''));
        return load(fileUrl(task.file));
    }
    /* a mask picture (white = in) as a canvas the shot's size */
    function maskCanvas(img) {
        const D = IM.doc(), c = document.createElement('canvas');
        c.width = D.width;
        c.height = D.height;
        const x = c.getContext('2d');
        x.drawImage(img, 0, 0, D.width, D.height);
        // grey to transparency: white selected, black not
        const d = x.getImageData(0, 0, D.width, D.height), p = d.data;
        for (let i = 0; i < p.length; i += 4) { p[i + 3] = p[i]; p[i] = p[i + 1] = p[i + 2] = 255; }
        x.putImageData(d, 0, 0);
        return c;
    }
    const pts = [];
    IM.objectClick = async function (p, label, keep) {
        const D = IM.doc();
        if (!keep) pts.length = 0;
        pts.push([Math.round(p[0]), Math.round(p[1]), label]);
        if (!pts.some((x) => x[2] === 1)) throw new Error(T('Click on the object first (Alt+click only takes parts away)'));
        const img = await ask('object', {picture: await flatBlob(), points: JSON.stringify(pts)}, T('Finding the object…'));
        IM.setSelection(maskCanvas(img), 'replace');
        IM.status(T('The object is selected: generate in it, take it away, or Ctrl+J for a layer of it'));
        return D;
    };
    async function subject() {
        const img = await ask('subject', {picture: await flatBlob()}, T('Finding the subject…'));
        IM.setSelection(maskCanvas(img), 'replace');
        IM.status(T('The subject is selected'));
    }
    async function background() {
        const D = IM.doc(), id = D.active;
        const img = await ask('subject', {picture: await IM.layerBlob(id)}, T('Finding the subject…'));
        IM.cutLayer(id, maskCanvas(img));
        IM.status(T('The background is taken away on a copy of the layer (the original is hidden)'));
    }
    async function erase() {
        const sel = IM.selection();
        if (!sel) throw new Error(T('Select what to take away first'));
        const img = await ask('erase', {picture: await flatBlob(), mask: await IM.maskBlob()}, T('Taking it away…'));
        // the filled picture on a layer, only a little wider than the selection
        const D = IM.doc(), wide = document.createElement('canvas');
        wide.width = D.width;
        wide.height = D.height;
        const w = wide.getContext('2d');
        w.filter = 'blur(6px)';
        for (const [dx, dy] of [[0, 0], [8, 0], [-8, 0], [0, 8], [0, -8]]) w.drawImage(sel, dx, dy);
        IM.addResult(img, '🧽 ' + T('Taken away'), wide);
        IM.deselect();
    }
    function tools(box, h) {
        const b = (icon, label, title, fn) => h('button', {class: 'fx-btn im-ai-btn', title: T(title), onclick: FS.guard(fn)}, icon + ' ' + T(label));
        box.append(h('div', {class: 'fx-group'}, h('div', {class: 'fx-group-title'}, '🪄 ' + T('AI tools')),
            h('div', {class: 'im-ai-tools'},
                b('👤', 'Select the subject', 'Selects the main subject of the picture (BiRefNet)', subject),
                b('🎯', 'Select an object', 'Then click the object on the picture (SAM); Shift+click adds, Alt+click takes away', () => IM.tool('object')),
                b('✂', 'Remove the background', 'The active layer without its background, on a copy (BiRefNet)', background),
                b('🧽', 'Take away what is selected', 'Fills the selection from what is around it (LaMa): best for small things; for a big one, select it and add the background in words', erase),
                b('➕', 'Add something in the selection', 'Draws what you write inside the selection (inpaint)', () => {
                    if (!IM.selection()) throw new Error(T('Select where to add it first'));
                    const gen = box.querySelector('.im-gen');
                    gen.querySelector('[data-mode="inpaint"]').click();
                    gen.scrollIntoView({block: 'start', behavior: 'smooth'});
                    gen.querySelector('textarea').focus();
                }))));
    }

    IM.panels.push(tools);
    IM.panels.push(section);
})();

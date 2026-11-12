/* QR presets are registered server-side; this file handles LoRA shortcuts and model-family state. */
(function() {
    'use strict';
    const TABS = ['txt2img', 'img2img'];
    const KEY = 'forge-workflow-compat-v1';
    const TAG = /<lora:([^:>]+)(?::([^>]+))?>/gi;
    const native = f => f === 'flux' || f === 'fibo';
    let data = {loras: [], styles: {}}, family = null, title = null, busy = false, ready = false;
    const families = new Map(), lcmActive = {}, lcmBefore = {};
    let saved = {};
    try { saved = JSON.parse(localStorage.getItem(KEY) || '{}'); } catch (_) { /* private state is optional */ }

    function comp(id) { return window.gradio_config?.components?.find(c => c.props?.elem_id === id); }
    function get(id) { return comp(id)?.props.value; }
    function set(id, value, extra) {
        const c = comp(id);
        if (!c || value === undefined) return;
        const props = Object.assign({value, value_is_output: true}, extra || {});
        Object.assign(c.props, props);
        if (c.instance?.$set) c.instance.$set(props);
    }
    function remember() { try { localStorage.setItem(KEY, JSON.stringify(saved)); } catch (_) { /* quota */ } }
    function tags(text) { return Array.from(String(text || '').matchAll(TAG)); }
    function loraFamily(name) {
        const key = name.toLowerCase();
        return data.loras.find(r => r.name.toLowerCase() === key || r.alias.toLowerCase() === key)?.family;
    }
    function compatible(name, f) { const found = loraFamily(name); return found === f || (!found && !native(f)); }
    function styleTags(name) {
        const s = data.styles[name];
        return tags((s?.prompt || '') + ' ' + (s?.negative || ''));
    }
    function clean(text, f) { return String(text || '').replace(TAG, (whole, name) => compatible(name, f) ? whole : ''); }
    function choices(c) { return (c?.props.choices || []).map(v => Array.isArray(v) ? v[1] : v); }
    function addon(c) {
        const id = c.props?.elem_id || '';
        return /^(txt2img|img2img)[_-]/.test(id) && /controlnet|animatediff|-(ad|dance)-/i.test(id) &&
            ['checkbox', 'slider', 'number', 'dropdown', 'radio', 'textbox'].includes(c.type) &&
            !/preview|gallery|status|output|batch_input_dir/i.test(id);
    }
    function enabled(c) { return c.type === 'checkbox' && /enable/i.test(c.props.elem_id); }
    function snapshot(f) {
        const state = {controls: {}, tabs: {}};
        for (const c of window.gradio_config?.components || []) if (addon(c)) state.controls[c.props.elem_id] = c.props.value;
        for (const tab of TABS) state.tabs[tab] = {styles: (get(tab + '_styles') || []).slice(),
            prompt: get(tab + '_prompt') || '', negative_prompt: get(tab + '_neg_prompt') || ''};
        saved[f] = state;
        remember();
    }
    function notice(message) {
        for (const tab of TABS) {
            const host = document.getElementById('tab_' + tab);
            if (!host) continue;
            let el = host.querySelector('.fw-model-note');
            if (!el) { el = document.createElement('div'); el.className = 'fw-model-note'; el.setAttribute('role', 'status'); host.prepend(el); }
            el.textContent = message;
            el.hidden = !message;
        }
    }
    function enforce(f) {
        for (const c of window.gradio_config?.components || []) if (addon(c) && enabled(c)) {
            if (native(f)) {
                if (c._fwInteractive === undefined) c._fwInteractive = c.props.interactive !== false;
                if (c.props.value || c.props.interactive !== false) set(c.props.elem_id, false, {interactive: false});
            } else if (c._fwInteractive !== undefined) {
                set(c.props.elem_id, c.props.value, {interactive: c._fwInteractive});
                delete c._fwInteractive;
            }
        }
        for (const tab of TABS) {
            const names = get(tab + '_styles') || [];
            const keep = names.filter(n => styleTags(n).every(m => compatible(m[1], f)));
            if (keep.length !== names.length) set(tab + '_styles', keep);
            for (const id of [tab + '_prompt', tab + '_neg_prompt']) {
                const before = get(id), after = clean(before, f);
                if (before !== undefined && after !== before) set(id, after);
            }
        }
        const denoise = comp('img2img_hires_denoise');
        if (denoise && denoise.props.interactive !== (f !== 'fibo')) set('img2img_hires_denoise', denoise.props.value, {interactive: f !== 'fibo'});
        notice(native(f) ? 'ControlNet and AnimateDiff are paused for this model. Compatible settings return when you switch back.' : '');
    }
    async function restore(f, expected) {
        const state = saved[f];
        if (!state) return;
        const fields = Object.entries(state.controls || {});
        for (const [id, value] of fields.filter(([id]) => /type_filter/.test(id))) set(id, value);
        if (window.tabPresets?.settled) await window.tabPresets.settled(0, 250, 2500);
        if (get('setting_sd_model_checkpoint') !== expected) return;
        for (const [id, value] of fields.filter(([id]) => !/type_filter/.test(id))) {
            const c = comp(id);
            if (!c) continue;
            if (['dropdown', 'radio'].includes(c.type) && !Array.isArray(value) && !choices(c).includes(value)) continue;
            set(id, value);
        }
        for (const tab of TABS) {
            const previous = state.tabs?.[tab];
            if (!previous) continue;
            const names = get(tab + '_styles') || [];
            const returning = (previous.styles || []).filter(n => styleTags(n).length && styleTags(n).every(m => compatible(m[1], f)));
            set(tab + '_styles', Array.from(new Set([...names, ...returning])));
            for (const [field, id] of [['prompt', tab + '_prompt'], ['negative_prompt', tab + '_neg_prompt']]) {
                const current = get(id) || '', original = previous[field] || '';
                const removed = tags(original).filter(m => compatible(m[1], f));
                if (!removed.length) continue;
                const bare = original.replace(TAG, '');
                const next = current === bare ? original : (current + ' ' + removed.filter(m => !current.includes(m[0])).map(m => m[0]).join(' ')).trim();
                if (next !== current) set(id, next);
            }
        }
    }
    function lcm(tab, f) {
        if (native(f)) { lcmActive[tab] = ''; return; }
        const text = (get(tab + '_prompt') || '') + ' ' + (get(tab + '_styles') || []).map(n => data.styles[n]?.prompt || '').join(' ');
        const found = tags(text).filter(m => compatible(m[1], f) && Number(m[2] || 1) !== 0 && /(^|[^a-z])lcm($|[^a-z])/i.test(m[1]));
        const key = found.map(m => m[1].toLowerCase()).sort().join('|');
        const fields = ['sampling', 'steps', 'cfg_scale'], values = ['LCM', 8, f === 'sdxl' ? 2 : 1.5];
        if (key && key !== lcmActive[tab]) {
            lcmBefore[tab] = lcmBefore[tab] || {};
            lcmBefore[tab][f] = lcmBefore[tab][f] || fields.map(k => get(tab + '_' + k));
            fields.forEach((k, i) => set(tab + '_' + k, values[i]));
        } else if (!key && lcmActive[tab]) {
            const previous = lcmBefore[tab]?.[f];
            if (previous) fields.forEach((k, i) => { if (get(tab + '_' + k) === values[i]) set(tab + '_' + k, previous[i]); });
            if (lcmBefore[tab]) delete lcmBefore[tab][f];
        }
        lcmActive[tab] = key;
    }
    async function tick() {
        if (busy || !ready) return;
        busy = true;
        try {
            const current = get('setting_sd_model_checkpoint');
            if (!current) return;
            if (current !== title) {
                let next = families.get(current);
                if (!next) {
                    const response = await fetch('/forge-workflow/family?title=' + encodeURIComponent(current));
                    if (!response.ok) return;
                    next = (await response.json()).family;
                    families.set(current, next);
                }
                if (get('setting_sd_model_checkpoint') !== current) return;
                if (family && next !== family) snapshot(family);
                // A reload with native models may expose persisted SD controls for
                // a moment. Keep those once, rather than throwing them away.
                if (!family && native(next) && !saved.sd1 && !saved.sdxl) {
                    const active = (window.gradio_config?.components || []).some(c => addon(c) && enabled(c) && c.props.value);
                    const sdStyles = TABS.some(t => (get(t + '_styles') || []).some(n => styleTags(n).some(m => !compatible(m[1], next))));
                    if (active || sdStyles) snapshot(TABS.some(t => (get(t + '_styles') || []).some(n => styleTags(n).some(m => loraFamily(m[1]) === 'sdxl'))) ? 'sdxl' : 'sd1');
                }
                const changed = family && next !== family;
                title = current; family = next;
                enforce(family);
                const presetAt = window.tabPresets?.modelSetAt || 0;
                if (changed && Date.now() - presetAt > 5000) await restore(family, current);
                enforce(family);
            } else enforce(family);
            for (const tab of TABS) lcm(tab, family);
        } catch (error) { console.warn('[Forge workflow]', error); }
        finally { busy = false; }
    }
    async function refresh() {
        const response = await fetch('/forge-workflow/catalog');
        if (!response.ok) throw new Error('Could not read the LoRA and style list.');
        data = await response.json();
        ready = true;
        return data;
    }
    function openStyle(row) {
        const veil = document.createElement('div'); veil.className = 'fw-veil';
        const form = document.createElement('form'); form.className = 'fw-dialog'; form.setAttribute('role', 'dialog'); form.setAttribute('aria-label', 'Add LoRA to Styles');
        const heading = document.createElement('h3'); heading.textContent = 'Add LoRA to Styles'; form.append(heading);
        function field(text, type, value) {
            const label = document.createElement('label'); label.textContent = text;
            const input = document.createElement('input'); input.type = type; input.value = value; label.append(input); form.append(label); return input;
        }
        const name = field('Style name', 'text', row.name); name.maxLength = 150;
        const weight = field('LoRA weight', 'number', row.weight); weight.min = -4; weight.max = 4; weight.step = 0.05;
        const replaceLabel = document.createElement('label'); replaceLabel.hidden = true;
        const replace = document.createElement('input'); replace.type = 'checkbox'; replaceLabel.append(replace, document.createTextNode('Replace the existing style')); form.append(replaceLabel);
        const status = document.createElement('p'); status.setAttribute('role', 'status'); form.append(status);
        const buttons = document.createElement('div'); buttons.className = 'fw-dialog-buttons';
        const cancel = document.createElement('button'); cancel.type = 'button'; cancel.textContent = 'Cancel'; cancel.onclick = () => veil.remove();
        const save = document.createElement('button'); save.type = 'submit'; save.textContent = 'Save style';
        buttons.append(cancel, save); form.append(buttons); veil.append(form); document.body.append(veil);
        veil.addEventListener('click', e => { if (e.target === veil) veil.remove(); });
        form.addEventListener('keydown', e => { e.stopPropagation(); if (e.key === 'Escape') veil.remove(); });
        form.onsubmit = async e => {
            e.preventDefault(); save.disabled = true;
            try {
                const response = await fetch('/forge-workflow/lora-style', {method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({lora: row.name, name: name.value, weight: Number(weight.value), overwrite: replace.checked})});
                const result = await response.json();
                if (!response.ok) { if (response.status === 409) replaceLabel.hidden = false; throw new Error(result.detail || 'Could not save the style.'); }
                await refresh();
                for (const id of ['txt2img_styles', 'img2img_styles', 'vs_styles']) if (comp(id)) set(id, get(id) || [], {choices: result.styles});
                status.textContent = 'Saved to Styles';
                setTimeout(() => veil.remove(), 700);
            } catch (error) { status.textContent = error.message; }
            finally { save.disabled = false; }
        };
        name.focus(); name.select();
    }
    function cards() {
        for (const tab of TABS) for (const card of document.querySelectorAll('#' + tab + '_lora_cards .card')) {
            if (card.querySelector('.fw-add-style')) continue;
            const row = data.loras.find(r => r.name === card.dataset.name);
            if (!row) continue;
            const button = document.createElement('button'); button.type = 'button'; button.className = 'fw-add-style'; button.textContent = 'Add to Styles';
            button.onclick = e => { e.preventDefault(); e.stopPropagation(); openStyle(row); };
            (card.querySelector('.button-row') || card).append(button);
        }
    }
    window.forgeWorkflow = {tick, refresh, get, set, tags, clean, compatible, openStyle, cards,
        state: () => ({family, saved, lcmActive, data})};
    onUiLoaded(async function() {
        try { await refresh(); await tick(); cards(); } catch (e) { console.warn('[Forge workflow]', e); }
        setInterval(tick, 400);
        onUiUpdate(cards);
    });
})();

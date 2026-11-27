/* Forge Workspace: a tidier txt2img / img2img.
 *
 * - The checkpoint sits above Generate; SD VAE, Clip skip and face restoration go into the "Model settings" card
 *   among the extension cards (scripts/workspace.py). They are Forge's own quick-settings components, moved, so
 *   they keep working and saving as before; on any other tab they go back to the quick-settings row, which now sits
 *   under the tab bar.
 * - With the quick-settings row out of the way the tab bar is the top line of the page, up to the update frog and
 *   the language switch.
 * - Above the styles: 🎨 Styles | 🧩 LoRA. LoRA opens a gallery of every LoRA (preview, name, SD version, folders,
 *   search); a click adds it as a chip beside the styles (− / + for its weight, ✕ to remove), a right-click edits
 *   its trigger words (saved in the LoRA's .json, as Forge's own card editor does). The chips live in a hidden field
 *   the script adds to the prompt as <lora:alias:weight> and the trigger words at generation.
 */
(function () {
    'use strict';
    const TABS = ['txt2img', 'img2img'];
    const STR = {
        styles: ['Styles', 'סגנונות'],
        lora: ['LoRA', 'LoRA'],
        loraTitle: ['LoRA', 'LoRA'],
        search: ['Search LoRA', 'חיפוש LoRA'],
        fits: ['Fits this model', 'מתאימים למודל'],
        all: ['All versions', 'כל הגרסאות'],
        allFolders: ['All', 'הכול'],
        hint: ['Click: add or remove · Right-click: trigger words', 'לחיצה: הוספה או הסרה · מקש ימני: מילות הפעלה'],
        none: ['No LoRA matches', 'אין LoRA מתאים'],
        loading: ['Loading…', 'טוען…'],
        weaker: ['Weaker', 'חלש יותר'],
        stronger: ['Stronger', 'חזק יותר'],
        remove: ['Remove', 'הסרה'],
        triggerTitle: ['Trigger words for {0}', 'מילות הפעלה ל-{0}'],
        triggerHelp: ['Added to the prompt with this LoRA. Saved with the LoRA (its .json), for every tab.',
            'נוספות לפרומפט יחד עם ה-LoRA. נשמרות עם ה-LoRA (בקובץ ה-json שלו), לכל הלשוניות.'],
        triggerPh: ['e.g. pixel art style, 1girl', 'למשל pixel art style, 1girl'],
        save: ['Save', 'שמירה'],
        cancel: ['Cancel', 'ביטול'],
        close: ['Close', 'סגירה'],
        triggerOn: ['Trigger words: {0}', 'מילות הפעלה: {0}'],
        noTrigger: ['No trigger words (right-click to add)', 'אין מילות הפעלה (מקש ימני כדי להוסיף)'],
        weight: ['Weight {0}', 'עוצמה {0}'],
        otherVersion: ['Made for {0}; the loaded model is {1}', 'נבנה בשביל {0}; המודל הטעון הוא {1}'],
    };
    const he = () => document.documentElement.classList.contains('hebrew-ui-active');
    const t = (k, ...a) => (STR[k] ? STR[k][he() ? 1 : 0] : k).replace(/\{(\d)\}/g, (_, i) => a[+i]);
    const app = () => gradioApp();
    const h = (tag, attrs, ...kids) => {
        const e = document.createElement(tag);
        for (const [k, v] of Object.entries(attrs || {})) {
            if (v == null || v === false) continue;
            if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
            else if (k === 'class') e.className = v;
            else e.setAttribute(k, v === true ? '' : v);
        }
        for (const c of kids.flat()) if (c != null) e.append(c);
        return e;
    };

    // ------------------------------------------------------------------ the quick settings, docked

    let qs = null, order = [], placed = false;
    const ckptIds = ['setting_sd_model_checkpoint', 'refresh_sd_model_checkpoint'];

    function currentTab() {
        const item = [...app().querySelectorAll('#tabs > .tabitem')].find((e) => e.style.display !== 'none');
        return item ? item.id.replace(/^tab_/, '') : '';
    }

    function setup() {
        qs = app().querySelector('#quicksettings');
        const tabs = app().querySelector('#tabs');
        const nav = tabs && tabs.querySelector(':scope > .tab-nav');
        if (!qs || !nav) return false;
        order = [...qs.children];
        // the row goes under the tab bar: the bar becomes the page's top line
        nav.after(qs);
        qs.classList.add('ws-qs');
        document.documentElement.classList.add('ws-on');
        placed = true;
        return true;
    }

    function dock() {
        if (!placed) return;
        const tab = currentTab();
        const kids = order.filter((e) => e.isConnected);
        if (TABS.includes(tab)) {
            const column = app().querySelector(`#${tab}_actions_column`);
            const home = app().querySelector(`#${tab}_ws_model_home`);
            if (column) {
                let top = column.querySelector(':scope > .ws-ckpt');
                if (!top) { top = h('div', {class: 'ws-ckpt'}); column.prepend(top); }
                // by reference: an img2img script ("Force a different Stable Diffusion model") has a component
                // with the very same id, so a search by id can find the other one
                for (const e of kids) if (ckptIds.includes(e.id)) top.append(e);
            }
            if (home) {
                let grid = home.querySelector('.ws-model-grid');
                if (!grid) { grid = h('div', {class: 'ws-model-grid'}); home.append(grid); }
                // a component with its 🔄 button (refresh_<name> after setting_<name>) stays on one line with it
                let pair = null;
                for (const e of kids) {
                    if (ckptIds.includes(e.id)) continue;
                    if (e.id.startsWith('refresh_') && pair && pair.dataset.for === e.id.slice(8)) { pair.append(e); continue; }
                    const next = kids[kids.indexOf(e) + 1];
                    if (e.id.startsWith('setting_') && next && next.id === 'refresh_' + e.id.slice(8)) {
                        pair = grid.querySelector(`.ws-pair[data-for="${e.id.slice(8)}"]`) || h('div', {class: 'ws-pair', 'data-for': e.id.slice(8)});
                        pair.append(e);
                        grid.append(pair);
                    } else { pair = null; grid.append(e); }
                }
            }
        } else {
            for (const e of kids) qs.append(e);   // back in their own order
        }
        qs.classList.toggle('ws-empty', !qs.children.length);
        reserveTop();
    }

    // the tab bar leaves room on its right for the frog and the language switch (they float there)
    function reserveTop() {
        const lang = document.getElementById('hebrew-ui-toggle');
        const frog = document.querySelector('.fs-upd-frog');
        const lefts = [lang, frog].filter(Boolean).map((e) => e.getBoundingClientRect().left).filter((x) => x > 0);
        const w = document.documentElement.clientWidth;
        const reserve = lefts.length ? Math.max(0, w - Math.min(...lefts) + 12) : 0;
        document.documentElement.style.setProperty('--ws-top-reserve', reserve + 'px');
    }

    // ------------------------------------------------------------------ LoRA chips

    let catalog = null, loading = null;
    function load(force) {
        if (catalog && !force) return Promise.resolve(catalog);
        if (loading && !force) return loading;
        loading = fetch('./forge-workspace/loras').then((r) => r.json()).then((d) => { catalog = d; loading = null; return d; })
            .catch((e) => { loading = null; throw e; });
        return loading;
    }

    const field = (tab) => app().querySelector(`#${tab}_ws_loras textarea`);
    function chips(tab) {
        try { const v = JSON.parse((field(tab) || {}).value || '[]'); return Array.isArray(v) ? v : []; } catch (e) { return []; }
    }
    function setChips(tab, list) {
        const ta = field(tab);
        if (!ta) return;
        ta.value = JSON.stringify(list);
        updateInput(ta);
        drawChips(tab);
        if (popup && popup.tab === tab) drawGallery();
    }
    const round = (w) => Math.round(w * 100) / 100;

    function toggleLora(tab, lora) {
        const list = chips(tab);
        const at = list.findIndex((c) => c.name === lora.name);
        if (at >= 0) list.splice(at, 1);
        else list.push({name: lora.name, alias: lora.alias, weight: lora.weight || (catalog && catalog.default_weight) || 1, trigger: lora.trigger || ''});
        setChips(tab, list);
    }

    function drawChips(tab) {
        const box = app().querySelector(`#${tab}_ws_chips`);
        if (!box) return;
        const list = chips(tab);
        box.replaceChildren(...list.map((c, i) => {
            const step = (d) => (e) => {
                e.stopPropagation();
                const l = chips(tab);
                l[i].weight = round(Math.max(-2, Math.min(3, (l[i].weight || 1) + d)));
                setChips(tab, l);
            };
            const title = c.name + '\n' + t('weight', round(c.weight).toFixed(2)) + '\n' + (c.trigger ? t('triggerOn', c.trigger) : t('noTrigger'));
            return h('span', {class: 'ws-chip', title, oncontextmenu: (e) => { e.preventDefault(); editTrigger(tab, {name: c.name, alias: c.alias, trigger: c.trigger}); }},
                h('span', {class: 'ws-chip-icon'}, '🧩'),
                h('span', {class: 'ws-chip-name', dir: 'auto'}, c.name),
                h('button', {type: 'button', class: 'ws-chip-btn', title: t('weaker'), onclick: step(-0.1)}, '−'),
                h('span', {class: 'ws-chip-w'}, round(c.weight).toFixed(1)),
                h('button', {type: 'button', class: 'ws-chip-btn', title: t('stronger'), onclick: step(0.1)}, '+'),
                h('button', {type: 'button', class: 'ws-chip-btn ws-chip-x', title: t('remove'), onclick: (e) => {
                    e.stopPropagation(); const l = chips(tab); l.splice(i, 1); setChips(tab, l);
                }}, '✕'));
        }));
        box.hidden = !list.length;
        const count = app().querySelector(`#${tab}_ws_switch .ws-count`);
        if (count) count.textContent = list.length ? String(list.length) : '';
    }

    // ------------------------------------------------------------------ the 🎨 Styles | 🧩 LoRA switch

    function buildSwitch(tab) {
        const row = app().querySelector(`#${tab}_styles_row`);
        if (!row || app().querySelector(`#${tab}_ws_switch`)) return;
        const styles = h('button', {type: 'button', class: 'ws-tab', 'data-k': 'styles', onclick: () => {
            const input = app().querySelector(`#${tab}_styles input`);
            if (input) { input.focus(); input.click(); }
        }}, h('span', {class: 'ws-tab-icon'}, '🎨'), h('span', {class: 'ws-tab-label'}));
        const lora = h('button', {type: 'button', class: 'ws-tab', 'data-k': 'lora', onclick: (e) => openGallery(tab, e.currentTarget)},
            h('span', {class: 'ws-tab-icon'}, '🧩'), h('span', {class: 'ws-tab-label'}), h('span', {class: 'ws-count'}));
        const bar = h('div', {id: `${tab}_ws_switch`, class: 'ws-switch hebrew-ui-skip'}, styles, lora);
        // in the row of the small buttons under Generate when there is one (no row of its own), else above the styles
        const tools = app().querySelector(`#${tab}_tools`);
        if (tools) { tools.append(bar); tools.classList.add('ws-tools'); } else row.before(bar);
        row.classList.add('ws-styles-row');
        const box = h('div', {id: `${tab}_ws_chips`, class: 'ws-chips hebrew-ui-skip', hidden: true});
        row.after(box);
        paintSwitch(tab);
        drawChips(tab);
    }
    function paintSwitch(tab) {
        const bar = app().querySelector(`#${tab}_ws_switch`);
        if (!bar) return;
        bar.dir = he() ? 'rtl' : 'ltr';
        bar.querySelector('[data-k="styles"] .ws-tab-label').textContent = t('styles');
        bar.querySelector('[data-k="lora"] .ws-tab-label').textContent = t('lora');
        const box = app().querySelector(`#${tab}_ws_chips`);
        if (box) box.dir = he() ? 'rtl' : 'ltr';
    }

    // ------------------------------------------------------------------ the LoRA gallery

    let popup = null;
    const remember = (k, v) => { try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch (e) { /* off */ } return null; };

    function closeGallery() {
        if (!popup) return;
        popup.el.remove();
        window.removeEventListener('pointerdown', outside, true);
        window.removeEventListener('keydown', esc, true);
        window.removeEventListener('resize', position);
        popup = null;
    }
    function outside(e) { if (popup && !popup.el.contains(e.target) && !popup.anchor.contains(e.target)) closeGallery(); }
    function esc(e) { if (e.key === 'Escape' && popup) { e.stopPropagation(); if (popup.editing) drawGallery(); else closeGallery(); } }
    function position() {
        if (!popup) return;
        const r = popup.anchor.getBoundingClientRect(), vw = document.documentElement.clientWidth, vh = window.innerHeight;
        const w = Math.min(760, vw - 24);
        const el = popup.el;
        el.style.width = w + 'px';
        // under the switch, its right edge with the column's (the column sits at the page's right)
        let left = Math.min(vw - w - 12, Math.max(12, r.right - w));
        el.style.left = left + 'px';
        const below = vh - r.bottom - 16;
        // below the switch when there is room, else above it; always whole inside the window
        if (below >= 380 || below >= r.top) {
            const top = Math.max(8, r.bottom + 6);
            el.style.top = top + 'px'; el.style.bottom = ''; el.style.maxHeight = Math.max(260, vh - top - 12) + 'px';
        } else {
            const bottom = Math.max(8, vh - r.top + 6);
            el.style.top = ''; el.style.bottom = bottom + 'px'; el.style.maxHeight = Math.max(260, vh - bottom - 12) + 'px';
        }
    }

    function openGallery(tab, anchor) {
        if (popup && popup.tab === tab) { closeGallery(); return; }
        closeGallery();
        const r = anchor.getBoundingClientRect();
        if (r.top < 60 || r.bottom > window.innerHeight - 60) anchor.scrollIntoView({block: 'center'});
        const el = h('div', {class: 'ws-gallery hebrew-ui-skip', dir: he() ? 'rtl' : 'ltr', role: 'dialog'});
        popup = {tab, anchor, el, query: '', folder: '', all: remember('forgeWorkspace.loraAll') === '1', editing: null};
        document.body.append(el);
        position();
        window.addEventListener('pointerdown', outside, true);
        window.addEventListener('keydown', esc, true);
        window.addEventListener('resize', position);
        el.append(h('div', {class: 'ws-g-empty'}, t('loading')));
        load().then(drawGallery).catch((e) => { if (popup) popup.el.replaceChildren(h('div', {class: 'ws-g-empty'}, String(e.message || e))); });
    }

    const fits = (l) => {
        const m = catalog.model;
        return l.version === m || l.version === 'Unknown' || m === 'Unknown';
    };

    function drawGallery() {
        if (!popup || !catalog) return;
        popup.editing = null;
        const {tab} = popup;
        const picked = new Set(chips(tab).map((c) => c.name));
        const pool = catalog.loras.filter((l) => popup.all || fits(l));
        const folders = [...new Set(pool.map((l) => l.folder).filter(Boolean))].sort();
        if (popup.folder && !folders.includes(popup.folder)) popup.folder = '';
        const list = pool.filter((l) => !popup.folder || l.folder === popup.folder || l.folder.startsWith(popup.folder + '/'));
        list.sort((a, b) => (picked.has(b.name) - picked.has(a.name)) || a.name.localeCompare(b.name));

        const search = h('input', {class: 'ws-g-search', type: 'search', placeholder: t('search'), value: popup.query});
        search.addEventListener('input', () => { popup.query = search.value; drawGrid(); });
        const scope = h('div', {class: 'ws-g-scope'},
            h('button', {type: 'button', class: 'ws-g-pill' + (popup.all ? '' : ' on'), onclick: () => { popup.all = false; remember('forgeWorkspace.loraAll', '0'); drawGallery(); }}, t('fits') + ' · ' + catalog.model),
            h('button', {type: 'button', class: 'ws-g-pill' + (popup.all ? ' on' : ''), onclick: () => { popup.all = true; remember('forgeWorkspace.loraAll', '1'); drawGallery(); }}, t('all')));
        const side = h('div', {class: 'ws-g-side'}, ...['', ...folders].map((f) => h('button', {type: 'button',
            class: 'ws-g-folder' + (f === popup.folder ? ' on' : ''), title: f || t('allFolders'),
            onclick: () => { popup.folder = f; drawGallery(); }}, f ? f.split('/').pop() : t('allFolders'))));
        const grid = h('div', {class: 'ws-g-grid'});
        function drawGrid() {
            const qq = popup.query.toLowerCase();
            const shown = list.filter((l) => !qq || (l.name + ' ' + l.alias + ' ' + l.trigger).toLowerCase().includes(qq));
            grid.replaceChildren(...(shown.length ? shown.map(card) : [h('div', {class: 'ws-g-empty'}, t('none'))]));
        }
        function card(l) {
            const on = picked.has(l.name);
            const thumb = h('div', {class: 'ws-g-thumb'});
            if (l.preview) {
                const img = h('img', {loading: 'lazy', alt: '', draggable: 'false', src: l.preview});
                img.onerror = () => img.replaceWith(h('span', {class: 'ws-g-initial'}, l.name.slice(0, 2).toUpperCase()));
                thumb.append(img);
            } else thumb.append(h('span', {class: 'ws-g-initial'}, l.name.slice(0, 2).toUpperCase()));
            if (l.version && l.version !== 'Unknown') thumb.append(h('span', {class: 'ws-g-ver' + (fits(l) ? '' : ' off'), title: fits(l) ? '' : t('otherVersion', l.version, catalog.model)}, l.version));
            if (on) thumb.append(h('span', {class: 'ws-g-check'}, '✓'));
            if (l.trigger) thumb.append(h('span', {class: 'ws-g-trig', title: t('triggerOn', l.trigger)}, 'T'));
            return h('button', {type: 'button', class: 'ws-g-card' + (on ? ' on' : ''), title: l.name + (l.trigger ? '\n' + t('triggerOn', l.trigger) : ''),
                onclick: () => toggleLora(tab, l),
                oncontextmenu: (e) => { e.preventDefault(); editTrigger(tab, l); }},
            thumb, h('span', {class: 'ws-g-name', dir: 'auto'}, l.name));
        }
        popup.el.replaceChildren(
            h('div', {class: 'ws-g-head'}, h('span', {class: 'ws-g-title'}, '🧩 ' + t('loraTitle'), h('span', {class: 'ws-g-n'}, String(list.length))),
                search, scope, h('button', {type: 'button', class: 'ws-g-close', title: t('close'), onclick: closeGallery}, '✕')),
            h('div', {class: 'ws-g-body' + (folders.length ? '' : ' no-side')}, folders.length ? side : null, grid),
            h('div', {class: 'ws-g-foot'}, t('hint')));
        drawGrid();
        position();
        if (!popup.focused) { search.focus(); popup.focused = true; }
    }

    // trigger words: in the gallery when it is open, else in a small window of its own
    function editTrigger(tab, lora) {
        const ta = h('textarea', {class: 'ws-t-input', rows: 3, placeholder: t('triggerPh'), dir: 'auto'});
        ta.value = lora.trigger || '';
        const save = async () => {
            const r = await fetch('./forge-workspace/lora-trigger', {method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({name: lora.name, trigger: ta.value})});
            if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
            const d = await r.json();
            if (catalog) { const it = catalog.loras.find((x) => x.name === lora.name); if (it) it.trigger = d.trigger; }
            for (const tb of TABS) {   // chips already picked take the new words too
                const l = chips(tb);
                let changed = false;
                for (const c of l) if (c.name === lora.name) { c.trigger = d.trigger; changed = true; }
                if (changed) setChips(tb, l);
            }
            done();
        };
        const box = h('div', {class: 'ws-t-box'},
            h('div', {class: 'ws-t-title', dir: 'auto'}, t('triggerTitle', lora.name)),
            h('div', {class: 'ws-t-help'}, t('triggerHelp')), ta,
            h('div', {class: 'ws-t-actions'},
                h('button', {type: 'button', class: 'ws-t-btn', onclick: () => done()}, t('cancel')),
                h('button', {type: 'button', class: 'ws-t-btn primary', onclick: () => save().catch((e) => alert(e.message))}, t('save'))));
        ta.addEventListener('keydown', (e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); save().catch((er) => alert(er.message)); } });
        let done;
        if (popup && popup.tab === tab) {
            popup.editing = lora.name;
            const layer = h('div', {class: 'ws-t-layer'}, box);
            popup.el.append(layer);
            done = () => drawGallery();
        } else {
            const back = h('div', {class: 'ws-t-back hebrew-ui-skip', dir: he() ? 'rtl' : 'ltr', onclick: (e) => { if (e.target === back) done(); }}, box);
            document.body.append(back);
            done = () => back.remove();
        }
        ta.focus();
    }

    // ------------------------------------------------------------------ start

    function start() {
        if (!setup()) return false;
        for (const tab of TABS) buildSwitch(tab);
        dock();
        onUiTabChange(() => dock());
        window.addEventListener('resize', reserveTop);
        // the frog and the language switch place themselves a little later
        let n = 0;
        const again = setInterval(() => { reserveTop(); if (++n > 12) clearInterval(again); }, 500);
        // the language switch: our own words follow it
        new MutationObserver(() => { for (const tab of TABS) { paintSwitch(tab); drawChips(tab); } if (popup) { popup.el.dir = he() ? 'rtl' : 'ltr'; drawGallery(); } })
            .observe(document.documentElement, {attributes: true, attributeFilter: ['class']});
        // a pasted infotext or a preset may set the hidden field: its chips follow
        for (const tab of TABS) {
            const ta = field(tab);
            if (ta) ta.addEventListener('input', () => drawChips(tab));
        }
        // the checkpoint changed: LoRAs that fit are another set
        let model = null, timer = 0;
        setInterval(() => {
            const ck = order.find((e) => e.id === 'setting_sd_model_checkpoint');
            const input = ck && ck.querySelector('input');
            const now = input ? input.value : null;
            if (model !== null && now !== model && catalog) {
                clearTimeout(timer);   // the new model takes a moment to load: its version is known after
                timer = setTimeout(() => load(true).then(() => popup && drawGallery()).catch(() => null), 4000);
            }
            model = now;
        }, 1500);
        return true;
    }

    onUiLoaded(() => {
        if (start()) return;
        let n = 0;
        const wait = setInterval(() => { if (start() || ++n > 40) clearInterval(wait); }, 500);
    });
})();

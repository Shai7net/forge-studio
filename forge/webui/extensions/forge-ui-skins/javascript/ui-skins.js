/* Forge UI Skins (2026-11-15) - re-dress Forge as you go.
 *
 * Hold the mouse on any button, tab or card title (Settings -> UI Skins sets how long, or turns it off) and a small
 * editor opens: background colour, text colour, a picture on it, roundness, text size, bold, a shadow under the text,
 * other words, or hide it. Every change shows at once. "Save" writes into the skin in use, "Save as" makes a new skin
 * (Classic, Forge as it is, is never changed: saving from it always makes a new skin). "Skins" switches, renames,
 * deletes, exports one shareable file (pictures inside) and imports one.
 *
 * Which element a rule is for (its key):
 *   "#elem_id"                     an element with an id of its own (not Gradio's component-123 numbers)
 *   "tab:<tabs id>:<English text>"  a tab's button
 *   "card:<English title>"          an extension card's title bar (txt2img / img2img accordions)
 *   "@<css path>"                   anything else: the path from the nearest element with an id of its own
 * Tabs and cards are found by their English words (the Hebrew interface shows other words; its dictionary is read
 * backwards), and are marked with data-skin-key so the skin's CSS reaches them.
 */
(function () {
    'use strict';
    const SK = window.forgeSkins = window.forgeSkins || {};
    const app = () => (typeof gradioApp === 'function' ? gradioApp() : document);
    const q = encodeURIComponent;
    const AUTO_ID = /^(component-\d+|input-accordion-\d+|tab-\d+)$/;

    /* ---- words ---- */
    const HE = {
        'Look of': 'עיצוב של', 'Skin': 'סקין', 'Classic': 'קלאסי', 'unsaved': 'לא נשמר',
        'Background': 'רקע', 'Text colour': 'צבע הכיתוב', 'Picture': 'תמונה', 'Choose a picture…': 'בחר תמונה…',
        'Fill': 'ממלאת', 'Whole picture': 'תמונה שלמה', 'Roundness': 'עיגול פינות', 'Text size': 'גודל כיתוב',
        'Bold': 'מודגש', 'Shadow under the text': 'צל מתחת לכיתוב', 'Other words (empty: its own)': 'כיתוב אחר (ריק: המקורי)',
        'Hide it': 'הסתר אותו', 'Reset this one': 'אפס את זה', 'Save': 'שמור', 'Save as…': 'שמור בשם…',
        'Skins…': 'סקינים…', 'Done': 'סיום', 'Name of the new skin': 'שם הסקין החדש', 'OK': 'אישור', 'Cancel': 'ביטול',
        'none': 'ללא', 'default': 'ברירת מחדל', 'Saved in skin {0}': 'נשמר בסקין {0}',
        'Use': 'השתמש', 'Rename': 'שנה שם', 'Export': 'ייצא', 'Delete': 'מחק', 'Import a skin file…': 'ייבא קובץ סקין…',
        'in use': 'בשימוש', 'Classic is Forge as it is. Saving from it makes a new skin.': 'קלאסי הוא Forge כמו שהוא. שמירה ממנו יוצרת סקין חדש.',
        'What this skin changes': 'מה הסקין הזה משנה', 'Nothing yet: hold the mouse on a button, tab or card title.': 'עדיין כלום: החזק את העכבר על כפתור, טאב או כותרת כרטיס.',
        'hidden': 'מוסתר', 'New name': 'שם חדש', 'Delete skin {0}? (it moves to ui-skins/_trash)': 'למחוק את הסקין {0}? (הוא עובר ל-ui-skins/_trash)',
        'Unsaved look changes': 'שינויי עיצוב שלא נשמרו', 'Discard': 'בטל', 'Imported as {0}': 'יובא בשם {0}',
        'Holding to edit can be turned off in Settings → UI Skins.': 'אפשר לכבות את העריכה בלחיצה ארוכה בהגדרות ← UI Skins.',
        'Back': 'חזרה', 'Border colour': 'צבע מסגרת',
    };
    const hebrew = () => document.documentElement.classList.contains('hebrew-ui-active');
    const T = (text, ...args) => {
        let out = hebrew() && HE[text] ? HE[text] : text;
        args.forEach((a, i) => { out = out.split(`{${i}}`).join(a); });
        return out;
    };
    SK.T = T;
    SK.HE = Object.assign(HE, SK.HE || {});   // ui-cards.js (loaded first) adds its words

    /* the English words behind what the page shows (the Hebrew interface's dictionary read backwards) */
    let reverse = null, reverseSize = -1;
    SK.english = function (text) {
        text = (text || '').replace(/\s+/g, ' ').trim();
        const dict = window.hebrewUiDictionary && window.hebrewUiDictionary.texts;
        if (!dict) return text;
        const size = Object.keys(dict).length;
        if (!reverse || reverseSize !== size) {
            reverse = {};
            for (const [en, he] of Object.entries(dict)) if (typeof he === 'string') reverse[he.replace(/\s+/g, ' ').trim()] = en;
            reverseSize = size;
        }
        return reverse[text] || text;
    };

    /* ---- state ---- */
    const S = SK.state = {active: 'Classic', rules: {}, saved: '{}', settings: {long_press: true, long_press_ms: 650}};
    const api = async (path, body, raw) => {
        const r = await fetch('/ui-skins/' + path, body === undefined ? {} : {method: 'POST', body: raw ? body : JSON.stringify(body),
            headers: raw ? {} : {'Content-Type': 'application/json'}});
        const data = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(data.detail || r.statusText);
        return data;
    };
    SK.api = api;
    const dirty = () => JSON.stringify(S.rules) !== S.saved;

    /* ---- which element ---- */
    const stableId = (el) => el && el.id && !AUTO_ID.test(el.id) ? el.id : '';
    const cssEsc = (s) => (window.CSS && CSS.escape ? CSS.escape(s) : s.replace(/[^\w-]/g, '\\$&'));
    const textOf = (el) => (el.dataset.skinOrigText || el.textContent || '').replace(/\s+/g, ' ').trim();

    /* the element a hold edits: a tab's button, a card's title bar, a button, a checkbox's label */
    function editable(target) {
        if (!target || !target.closest || target.closest('.ui-skin-editor, .ui-skin-pill, .uicards-tools, input, textarea, select, canvas, .ft-content, .ft-ruler, .cm-editor')) return null;
        return target.closest('.tab-nav > button, .label-wrap, button, label') || null;
    }
    SK.editable = editable;

    function keyOf(el) {
        if (el.matches('.tab-nav > button')) {
            const tabs = el.closest('.tabs');
            return `tab:${(tabs && stableId(tabs)) || ''}:${SK.english(textOf(el))}`;
        }
        if (el.matches('.label-wrap')) {
            const span = el.querySelector('span');
            return `card:${SK.english(span ? (span.dataset.skinOrigText || span.textContent) : textOf(el)).replace(/\s+/g, ' ').trim()}`;
        }
        if (stableId(el)) return '#' + el.id;
        // a path from the nearest element with an id of its own
        const parts = [];
        let node = el;
        while (node && node.parentElement && !stableId(node)) {
            const parent = node.parentElement;
            parts.unshift(`${node.tagName.toLowerCase()}:nth-child(${Array.prototype.indexOf.call(parent.children, node) + 1})`);
            node = parent;
        }
        return '@' + (node && stableId(node) ? '#' + cssEsc(node.id) + ' > ' : '') + parts.join(' > ');
    }
    SK.keyOf = keyOf;

    const selectorOf = (key) => (key.startsWith('#') ? '#' + cssEsc(key.slice(1)) : key.startsWith('@') ? key.slice(1)
        : `[data-skin-key="${key.replace(/["\\]/g, '\\$&')}"]`);

    /* tabs and cards are marked by their words (and a rule's other words are put on them) */
    function mark() {
        const keyed = Object.keys(S.rules).filter((k) => k.startsWith('tab:') || k.startsWith('card:'));
        const texts = Object.entries(S.rules).filter(([, r]) => r.text);
        const root = app();
        if (keyed.length) {
            const wanted = new Set(keyed);
            root.querySelectorAll('.tab-nav > button, .gradio-accordion > .label-wrap').forEach((el) => {
                const key = keyOf(el);
                if (wanted.has(key)) { if (el.dataset.skinKey !== key) el.dataset.skinKey = key; }
                else if (el.dataset.skinKey) delete el.dataset.skinKey;
            });
        }
        // other words: the element's own text is kept aside to come back to
        root.querySelectorAll('[data-skin-orig-text]').forEach((el) => {
            const rule = S.rules[el.dataset.skinTextKey];
            if (!rule || !rule.text) { setWords(el, el.dataset.skinOrigText); delete el.dataset.skinOrigText; delete el.dataset.skinTextKey; }
        });
        for (const [key, rule] of texts) {
            let els = [];
            try { els = root.querySelectorAll(selectorOf(key)); } catch (e) { continue; }
            els.forEach((el) => {
                if (!el.dataset.skinOrigText) { el.dataset.skinOrigText = textOf(el); el.dataset.skinTextKey = key; }
                setWords(el, rule.text);
            });
        }
    }
    function setWords(el, words) {
        const span = el.matches('.label-wrap') ? el.querySelector('span') : null;
        const target = span || el;
        // the last text node (keeps an icon or a checkbox before it)
        const nodes = Array.from(target.childNodes).filter((n) => n.nodeType === 3 && n.textContent.trim());
        if (nodes.length) { if (nodes[nodes.length - 1].textContent !== words) nodes[nodes.length - 1].textContent = words; }
        else if (!target.children.length && target.textContent !== words) target.textContent = words;
    }
    let markTimer = null;
    // only while the skin has tabs, cards or other words to put on the page (the page changes all the time while
    // something is made: nothing is looked at for a skin of colours only)
    const needsMark = () => Object.entries(S.rules).some(([k, r]) => r.text || k.startsWith('tab:') || k.startsWith('card:')) ||
        !!document.querySelector('[data-skin-orig-text], [data-skin-key]');
    const markSoon = () => { if (!needsMark()) return; clearTimeout(markTimer); markTimer = setTimeout(mark, 250); };

    /* ---- the skin as CSS ---- */
    SK.imageUrl = (file) => (/^(https?:|data:|\/)/.test(file) ? file : `/ui-skins/file/${q(S.active || 'Classic')}/${q(file)}`);
    function css() {
        const out = [];
        for (const [key, r] of Object.entries(S.rules)) {
            const sel = selectorOf(key);
            if (r.hidden) { out.push(`${sel} { display: none !important; }`); continue; }
            const p = [];
            const layers = [];
            if (r.image) layers.push(`url("${SK.imageUrl(r.image)}") center / ${r.fit === 'contain' ? 'contain' : 'cover'} no-repeat`);
            if (r.bg) layers.push(r.bg);
            if (layers.length) p.push(`background: ${layers.join(', ')} !important`);
            if (r.color) p.push(`color: ${r.color} !important`);
            if (r.border) p.push(`border-color: ${r.border} !important`);
            if (r.radius != null && r.radius !== '') p.push(`border-radius: ${+r.radius}px !important`);
            if (r.size) p.push(`font-size: ${+r.size}px !important`);
            if (r.bold) p.push('font-weight: 700 !important');
            if (r.shadow) p.push('text-shadow: 0 1px 3px rgba(0,0,0,.85), 0 0 8px rgba(0,0,0,.5) !important');
            if (p.length) out.push(`${sel} { ${p.join('; ')}; }`);
            if (r.color) out.push(`${sel} span, ${sel} .icon { color: inherit !important; }`);
            if (r.size) out.push(`${sel} span { font-size: inherit !important; }`);
        }
        return out.join('\n');
    }
    SK.apply = function () {
        let style = document.getElementById('ui-skin-style');
        if (!style) { style = document.createElement('style'); style.id = 'ui-skin-style'; document.head.append(style); }
        const text = css();
        if (style.textContent !== text) style.textContent = text;
        mark();
        pill();
    };

    SK.load = async function (name) {
        const skin = await api('skin?name=' + q(name || 'Classic'));
        S.active = skin.name;
        S.rules = skin.rules || {};
        S.saved = JSON.stringify(S.rules);
        SK.apply();
        if (SK.onLoad) SK.onLoad();
    };

    SK.save = async function (name, from) {
        const r = await api('save', {name, rules: S.rules, from: from || S.active});
        // pictures of a skin that had no name yet were moved into it: the same files under the new name
        S.active = r.name;
        S.saved = JSON.stringify(S.rules);
        if (window.opts) window.opts.ui_skins_active = r.name;
        SK.apply();
        return r.name;
    };

    /* ---- holding the mouse ---- */
    let hold = null, swallowUntil = 0;
    document.addEventListener('pointerdown', (e) => {
        if (!S.settings.long_press || e.button !== 0) return;
        const el = editable(e.target);
        if (!el) return;
        const ms = Math.max(300, +S.settings.long_press_ms || 650);
        el.classList.add('ui-skin-holding');
        el.style.setProperty('--ui-skin-hold', ms + 'ms');
        hold = {el, x: e.clientX, y: e.clientY, timer: setTimeout(() => {
            el.classList.remove('ui-skin-holding');
            swallowUntil = Date.now() + 1500;   // the click that ends this hold does nothing
            hold = null;
            SK.edit(el);
        }, ms)};
    }, true);
    const stopHold = () => { if (hold) { clearTimeout(hold.timer); hold.el.classList.remove('ui-skin-holding'); hold = null; } };
    document.addEventListener('pointermove', (e) => { if (hold && Math.hypot(e.clientX - hold.x, e.clientY - hold.y) > 8) stopHold(); }, true);
    document.addEventListener('pointerup', stopHold, true);
    document.addEventListener('pointercancel', stopHold, true);
    document.addEventListener('dragstart', stopHold, true);
    document.addEventListener('click', (e) => {
        if (Date.now() < swallowUntil && !e.target.closest('.ui-skin-editor')) { e.preventDefault(); e.stopPropagation(); swallowUntil = 0; }
    }, true);
    document.addEventListener('contextmenu', (e) => { if (Date.now() < swallowUntil) e.preventDefault(); }, true);

    /* ---- the floating window ---- */
    function h(tag, attrs, ...kids) {
        const e = document.createElement(tag);
        for (const [k, v] of Object.entries(attrs || {})) {
            if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
            else if (k === 'class') e.className = v;
            else if (v === true) e.setAttribute(k, '');
            else if (v != null && v !== false) e.setAttribute(k, v);
        }
        for (const kid of kids.flat()) if (kid != null) e.append(kid);
        return e;
    }
    SK.h = h;
    let win = null;
    function windowFor(anchor) {
        if (win) win.remove();
        win = h('div', {class: 'ui-skin-editor hebrew-ui-skip', dir: hebrew() ? 'rtl' : 'ltr'});
        document.body.append(win);
        // beside the element, kept on screen
        requestAnimationFrame(() => {
            const r = anchor ? anchor.getBoundingClientRect() : {left: innerWidth / 2 - 160, bottom: 120, top: 100};
            const w = win.offsetWidth, hh = win.offsetHeight;
            let left = Math.min(innerWidth - w - 10, Math.max(10, r.left));
            let top = r.bottom + 8;
            if (top + hh > innerHeight - 10) top = Math.max(10, r.top - hh - 8);
            win.style.left = left + 'px';
            win.style.top = top + 'px';
        });
        return win;
    }
    SK.close = () => { if (win) win.remove(); win = null; pill(); };
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && win) SK.close(); });

    function row(label, ...controls) { return h('div', {class: 'ui-skin-row'}, h('span', {class: 'ui-skin-label'}, label), h('div', {class: 'ui-skin-controls'}, ...controls)); }
    function colourField(rule, field) {
        const input = h('input', {type: 'color', value: rule[field] && /^#[0-9a-f]{6}$/i.test(rule[field]) ? rule[field] : '#22c55e'});
        input.addEventListener('input', () => { rule[field] = input.value; changed(); });
        const none = h('button', {type: 'button', class: 'ui-skin-mini', title: T('none'), onclick: () => { delete rule[field]; changed(); refresh(); }}, '✕');
        return [input, none];
    }
    let current = null, refresh = () => {}, lastNote = null;
    function changed() {
        if (!current) return;
        const {key, rule} = current;
        for (const k of Object.keys(rule)) if (rule[k] === '' || rule[k] == null || rule[k] === false) delete rule[k];
        if (Object.keys(rule).length) S.rules[key] = rule; else delete S.rules[key];
        SK.apply();
        if (win && win.querySelector('.ui-skin-state')) win.querySelector('.ui-skin-state').textContent = stateText();
    }
    const stateText = () => `${T('Skin')}: ${S.active === 'Classic' ? T('Classic') : S.active}${dirty() ? ' • ' + T('unsaved') : ''}`;

    SK.edit = function (el) {
        const key = keyOf(el);
        const rule = Object.assign({}, S.rules[key] || {});
        current = {key, rule, el};
        const label = (el.dataset.skinOrigText || textOf(el) || key).slice(0, 60);
        const w = windowFor(el);
        const draw = () => {
            w.innerHTML = '';
            const fileInput = h('input', {type: 'file', accept: 'image/*', hidden: true});
            fileInput.addEventListener('change', async () => {
                const f = fileInput.files[0];
                if (!f) return;
                const r = await api(`image?skin=${q(S.active === 'Classic' ? '' : S.active)}&filename=${q(f.name)}`, f, true);
                rule.image = r.file;
                changed(); draw();
            });
            const range = (field, min, max, fallback) => {
                const input = h('input', {type: 'range', min, max, value: rule[field] != null ? rule[field] : fallback});
                const shown = h('span', {class: 'ui-skin-value'}, rule[field] != null ? String(rule[field]) : T('default'));
                input.addEventListener('input', () => { rule[field] = +input.value; shown.textContent = input.value; changed(); });
                const reset = h('button', {type: 'button', class: 'ui-skin-mini', title: T('default'), onclick: () => { delete rule[field]; changed(); draw(); }}, '↺');
                return [input, shown, reset];
            };
            const check = (field, text) => {
                const box = h('input', {type: 'checkbox'});
                box.checked = !!rule[field];
                box.addEventListener('change', () => { rule[field] = box.checked; changed(); });
                return h('label', {class: 'ui-skin-check'}, box, ' ', text);
            };
            const words = h('input', {type: 'text', class: 'ui-skin-text', value: rule.text || '', placeholder: label});
            words.addEventListener('input', () => { rule.text = words.value; changed(); });
            const fit = h('select', {class: 'ui-skin-select'}, h('option', {value: 'cover'}, T('Fill')), h('option', {value: 'contain'}, T('Whole picture')));
            fit.value = rule.fit === 'contain' ? 'contain' : 'cover';
            fit.addEventListener('change', () => { rule.fit = fit.value === 'contain' ? 'contain' : ''; changed(); });
            const nameRow = h('div', {class: 'ui-skin-saveas', hidden: true});
            const nameInput = h('input', {type: 'text', class: 'ui-skin-text', placeholder: T('Name of the new skin')});
            const doSaveAs = async () => {
                const name = nameInput.value.trim();
                if (!name) return nameInput.focus();
                try { const saved = await SK.save(name, S.active); flash(T('Saved in skin {0}', saved)); nameRow.hidden = true; draw(); }
                catch (e) { flash(e.message, true); }
            };
            nameInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') doSaveAs(); });
            nameRow.append(nameInput, h('button', {type: 'button', class: 'ui-skin-btn primary', onclick: doSaveAs}, T('OK')),
                h('button', {type: 'button', class: 'ui-skin-btn', onclick: () => { nameRow.hidden = true; }}, T('Cancel')));
            const askName = () => { nameRow.hidden = false; nameInput.value = S.active === 'Classic' ? '' : S.active + ' 2'; nameInput.focus(); nameInput.select(); };
            const note = h('div', {class: 'ui-skin-note'});
            const flash = (text, bad) => {
                lastNote = {text, bad, until: Date.now() + 4000};
                note.textContent = text; note.classList.toggle('bad', !!bad);
                setTimeout(() => { const n = w.querySelector('.ui-skin-note'); if (n && n.textContent === text) n.textContent = ''; }, 4000);
            };
            if (lastNote && Date.now() < lastNote.until) { note.textContent = lastNote.text; note.classList.toggle('bad', !!lastNote.bad); }
            w.append(
                h('div', {class: 'ui-skin-head'}, h('b', {}, '🎨 ', T('Look of'), ' “', label, '”'),
                    h('button', {type: 'button', class: 'ui-skin-x', title: T('Done'), onclick: SK.close}, '✕')),
                h('div', {class: 'ui-skin-state'}, stateText()),
                row(T('Background'), ...colourField(rule, 'bg')),
                row(T('Text colour'), ...colourField(rule, 'color')),
                row(T('Border colour'), ...colourField(rule, 'border')),
                row(T('Picture'), h('button', {type: 'button', class: 'ui-skin-btn', onclick: () => fileInput.click()}, T('Choose a picture…')),
                    rule.image ? h('img', {class: 'ui-skin-thumb', src: SK.imageUrl(rule.image), alt: ''}) : null,
                    rule.image ? fit : null,
                    rule.image ? h('button', {type: 'button', class: 'ui-skin-mini', title: T('none'), onclick: () => { delete rule.image; delete rule.fit; changed(); draw(); }}, '✕') : null,
                    fileInput),
                row(T('Roundness'), ...range('radius', 0, 40, 8)),
                row(T('Text size'), ...range('size', 8, 40, 14)),
                h('div', {class: 'ui-skin-row ui-skin-checks'}, check('bold', T('Bold')), check('shadow', T('Shadow under the text')), check('hidden', T('Hide it'))),
                row(T('Other words (empty: its own)'), words),
                h('div', {class: 'ui-skin-buttons'},
                    h('button', {type: 'button', class: 'ui-skin-btn', onclick: () => { for (const k of Object.keys(rule)) delete rule[k]; changed(); draw(); }}, T('Reset this one')),
                    h('span', {class: 'ui-skin-grow'}),
                    h('button', {type: 'button', class: 'ui-skin-btn primary', onclick: async () => {
                        if (S.active === 'Classic') return askName();
                        try { flash(T('Saved in skin {0}', await SK.save(S.active))); draw(); } catch (e) { flash(e.message, true); }
                    }}, T('Save')),
                    h('button', {type: 'button', class: 'ui-skin-btn', onclick: askName}, T('Save as…')),
                    h('button', {type: 'button', class: 'ui-skin-btn', onclick: () => SK.manage(el)}, T('Skins…'))),
                nameRow, note);
        };
        refresh = draw;
        draw();
    };

    /* ---- the skins ---- */
    SK.manage = async function (anchor) {
        const w = windowFor(anchor);
        const state = await api('state');
        const note = h('div', {class: 'ui-skin-note'});
        const say = (text, bad) => { note.textContent = text; note.classList.toggle('bad', !!bad); };
        const use = async (name) => {
            await api('active', {name});
            if (window.opts) window.opts.ui_skins_active = name;
            await SK.load(name);
            SK.manage(anchor);
        };
        const importer = h('input', {type: 'file', accept: '.json,application/json', hidden: true});
        importer.addEventListener('change', async () => {
            const f = importer.files[0];
            if (!f) return;
            try { const r = await api('import', JSON.parse(await f.text())); say(T('Imported as {0}', r.name)); SK.manage(anchor); }
            catch (e) { say(e.message, true); }
        });
        const line = (name, count) => {
            const on = name === S.active;
            const actions = [];
            if (!on) actions.push(h('button', {type: 'button', class: 'ui-skin-btn primary', onclick: () => use(name)}, T('Use')));
            if (name !== 'Classic') {
                actions.push(
                    h('button', {type: 'button', class: 'ui-skin-btn', onclick: async () => {
                        const to = prompt(T('New name'), name);
                        if (!to || to === name) return;
                        try { const r = await api('rename', {name, to}); if (on) S.active = r.name; SK.manage(anchor); } catch (e) { say(e.message, true); }
                    }}, T('Rename')),
                    h('a', {class: 'ui-skin-btn', href: '/ui-skins/export?name=' + q(name), download: name + '.forgeskin.json'}, T('Export')),
                    h('button', {type: 'button', class: 'ui-skin-btn danger', onclick: async () => {
                        if (!confirm(T('Delete skin {0}? (it moves to ui-skins/_trash)', name))) return;
                        await api('delete', {name});
                        if (on) await SK.load('Classic');
                        SK.manage(anchor);
                    }}, T('Delete')));
            }
            return h('div', {class: 'ui-skin-skin' + (on ? ' on' : '')},
                h('span', {class: 'ui-skin-skin-name'}, on ? '● ' : '', name === 'Classic' ? T('Classic') : name,
                    count != null ? h('small', {}, ` · ${count}`) : null, on ? h('small', {}, ' · ' + T('in use')) : null), ...actions);
        };
        // what the skin in use changes (a hidden element can only be brought back from here)
        const rules = Object.entries(S.rules);
        const list = rules.length ? rules.map(([key, r]) => h('div', {class: 'ui-skin-rule'},
            h('span', {class: 'ui-skin-rule-key', title: key}, key.replace(/^(tab|card):[^:]*:?/, '').replace(/^[#@]/, '').slice(0, 50)),
            r.hidden ? h('small', {}, ' · ' + T('hidden')) : null,
            h('button', {type: 'button', class: 'ui-skin-mini', title: T('Reset this one'), onclick: () => { delete S.rules[key]; SK.apply(); SK.manage(anchor); }}, '↺')))
            : [h('div', {class: 'ui-skin-dim'}, T('Nothing yet: hold the mouse on a button, tab or card title.'))];
        w.innerHTML = '';
        w.append(
            h('div', {class: 'ui-skin-head'}, h('b', {}, '🎨 ', T('Skins…').replace('…', '')),
                h('button', {type: 'button', class: 'ui-skin-x', onclick: SK.close}, '✕')),
            h('div', {class: 'ui-skin-dim'}, T('Classic is Forge as it is. Saving from it makes a new skin.')),
            line('Classic'), ...state.skins.map((s) => line(s.name, s.rules)),
            h('div', {class: 'ui-skin-buttons'}, h('button', {type: 'button', class: 'ui-skin-btn', onclick: () => importer.click()}, T('Import a skin file…')), importer,
                current ? h('button', {type: 'button', class: 'ui-skin-btn', onclick: () => current.el && document.contains(current.el) ? SK.edit(current.el) : SK.close()}, T('Back')) : null),
            h('div', {class: 'ui-skin-sub'}, T('What this skin changes')), h('div', {class: 'ui-skin-rules'}, ...list),
            h('div', {class: 'ui-skin-dim'}, T('Holding to edit can be turned off in Settings → UI Skins.')),
            note);
    };

    /* unsaved changes: a small reminder in the corner */
    let pillEl = null;
    function pill() {
        const show = dirty() && !win;
        if (!show) { if (pillEl) { pillEl.remove(); pillEl = null; } return; }
        if (pillEl) return;
        pillEl = h('div', {class: 'ui-skin-pill hebrew-ui-skip', dir: hebrew() ? 'rtl' : 'ltr'}, '🎨 ', T('Unsaved look changes'), ' ',
            h('button', {type: 'button', class: 'ui-skin-btn primary', onclick: async () => {
                if (S.active === 'Classic') { const n = prompt(T('Name of the new skin')); if (n) await SK.save(n); }
                else await SK.save(S.active);
                pill();
            }}, T('Save')),
            h('button', {type: 'button', class: 'ui-skin-btn', onclick: () => { S.rules = JSON.parse(S.saved); SK.apply(); }}, T('Discard')));
        document.body.append(pillEl);
    }

    /* ---- start ---- */
    async function boot() {
        try {
            const state = await api('state');
            S.settings = state;
            await SK.load(state.active);
        } catch (e) { console.warn('UI Skins:', e); }
        new MutationObserver(markSoon).observe(app(), {childList: true, subtree: true});
        if (typeof onOptionsChanged === 'function') onOptionsChanged(() => {
            const o = window.opts || {};
            if (o.ui_skins_long_press !== undefined) S.settings.long_press = !!o.ui_skins_long_press;
            if (o.ui_skins_long_press_ms) S.settings.long_press_ms = +o.ui_skins_long_press_ms;
            if (o.ui_skins_active && o.ui_skins_active !== S.active && !dirty()) SK.load(o.ui_skins_active).catch(() => null);
        });
    }
    if (typeof onUiLoaded === 'function') onUiLoaded(boot); else window.addEventListener('load', boot);
})();

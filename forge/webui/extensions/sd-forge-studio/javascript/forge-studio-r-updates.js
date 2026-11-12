/* Forge Studio - the 🐸 "Check for updates" button next to Language at the top of Forge (every tab).
 * A click spins the frog and asks GitHub for the newest Release (updates.py); when there is one, the user decides:
 * update now (git pull in an installation cloned from GitHub), then restart Forge to apply it (webui.bat starts it
 * again and the page reloads by itself). A copy that cannot pull is told to use update.bat or download it.
 */
(function () {
    'use strict';
    const api = async (path, method) => {
        const r = await fetch('/forge-studio/updates/' + path, {method: method || 'GET'});
        const body = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(body.detail || r.statusText);
        return body;
    };
    const el = (tag, attrs, ...kids) => {
        const e = document.createElement(tag);
        for (const [k, v] of Object.entries(attrs || {})) {
            if (k === 'onclick') e.addEventListener('click', v); else if (v != null) e.setAttribute(k, v);
        }
        for (const k of kids) if (k != null) e.append(k);
        return e;
    };
    let box = null, frog = null, panel = null;

    function place() {
        const lang = document.getElementById('hebrew-ui-toggle');
        if (!box) return;
        if (lang) {
            const r = lang.getBoundingClientRect();
            box.style.right = Math.max(8, window.innerWidth - r.left + 10) + 'px';
            box.style.top = Math.max(2, r.top - 2) + 'px';
        } else {
            box.style.right = '140px';
            box.style.top = '2px';
        }
    }

    function show(...content) {
        if (panel) panel.remove();
        panel = el('div', {class: 'fs-upd-panel'}, ...content);
        box.append(panel);
    }
    const close = () => { if (panel) panel.remove(); panel = null; };
    const button = (label, fn, primary) => el('button', {class: 'fs-upd-btn' + (primary ? ' primary' : ''), onclick: fn}, label);

    async function busy(text, fn) {
        frog.classList.add('spinning');
        show(el('div', {}, text));
        try { return await fn(); } finally { frog.classList.remove('spinning'); }
    }

    async function waitAndReload() {
        show(el('div', {}, 'Restarting Forge… the page reloads by itself when it is back.'));
        await new Promise((r) => setTimeout(r, 6000));
        for (let i = 0; i < 200; i++) {
            try { const r = await fetch('/', {cache: 'no-store'}); if (r.ok) { location.reload(); return; } } catch (e) { /* not back yet */ }
            await new Promise((r) => setTimeout(r, 3000));
        }
    }
    async function restart() {
        try { await busy('Restarting…', () => api('restart', 'POST')); waitAndReload(); }
        catch (e) { show(el('div', {}, e.message), button('Close', close)); }
    }

    async function update(state) {
        try {
            const done = await busy(`Downloading Forge Studio ${state.latest}…`, () => api('apply', 'POST'));
            if (done.needs_install) {
                show(el('div', {}, `Updated (${done.changed} files). Some packages or extensions changed: close Forge and run update.bat to install them.`),
                    button('Close', close));
            } else {
                show(el('div', {}, `Updated to ${done.installed || state.latest} (${done.changed} files). Restart Forge now to apply it?`),
                    el('div', {class: 'fs-upd-row'}, button('Restart now', restart, true), button('Later', close)));
            }
        } catch (e) { show(el('div', {}, 'The update failed: ' + e.message), button('Close', close)); }
    }

    async function check() {
        if (panel) { close(); return; }
        let state;
        try { state = await busy('Checking for updates…', () => api('check')); }
        catch (e) { show(el('div', {}, 'Could not check: ' + e.message), button('Close', close)); return; }
        const link = state.url ? el('a', {href: state.url, target: '_blank', rel: 'noopener'}, 'What is new') : null;
        if (!state.available) {
            show(el('div', {}, `Forge Studio is up to date (${state.installed || state.latest}).`), el('div', {class: 'fs-upd-row'}, link, button('Close', close)));
            return;
        }
        const notes = el('div', {class: 'fs-upd-notes'}, (state.notes || '').split('\n').slice(0, 12).join('\n'));
        const head = el('div', {}, el('b', {}, `Forge Studio ${state.latest} is available`), state.installed ? ` (you have ${state.installed})` : '');
        if (state.can_update) {
            show(head, notes, el('div', {class: 'fs-upd-row'}, button('Update now', () => update(state), true), button('Later', close), link));
        } else {
            const why = state.reason === 'local-changes'
                ? 'Files of Forge Studio were changed on this computer, so it is not updated automatically: run update.bat.'
                : 'This copy was not installed from GitHub: run update.bat, or download the new version.';
            show(head, notes, el('div', {}, why), el('div', {class: 'fs-upd-row'}, link, button('Close', close)));
        }
    }

    function build() {
        if (box) return;
        frog = el('button', {class: 'fs-upd-frog', title: 'Check for updates', 'aria-label': 'Check for updates', onclick: check}, '🐸');
        box = el('div', {id: 'fs-update', class: 'hebrew-ui-block'}, frog, el('div', {class: 'fs-upd-label'}, 'Check for updates'));
        document.body.append(box);
        place();
        window.addEventListener('resize', place);
        // the language box can arrive or move after us
        let n = 0;
        const t = setInterval(() => { place(); if (++n > 20) clearInterval(t); }, 500);
        document.addEventListener('click', (e) => { if (panel && !box.contains(e.target)) close(); });
    }
    if (typeof onUiLoaded === 'function') onUiLoaded(build);
    else window.addEventListener('load', build);
})();

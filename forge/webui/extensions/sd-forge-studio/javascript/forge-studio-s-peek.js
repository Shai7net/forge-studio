/* Forge Studio - the timeline under the results of the other tabs (txt2img, img2img, Extras, Video Studio,
 * Deforum, Infinite Zoom, Time Strata). A green bar under each result: ▼ opens a drawer with Forge Studio's own
 * timeline of the open project (the same element, moved over while the drawer is open, so everything in it works),
 * and the result is dragged onto it (or added at the end with ➕). The timeline goes back to the editor when the
 * drawer closes, another drawer opens, or the Forge Studio tab is shown (2026-11-15).
 */
(function () {
    'use strict';
    const FS = window.ForgeStudio = window.ForgeStudio || {};
    const T = (...a) => FS.T(...a);
    const app = () => (typeof gradioApp === 'function' ? gradioApp() : document);
    const byId = (id) => app().getElementById(id);

    function galleryMedia(id) {
        const g = byId(id);
        if (!g) return null;
        const img = g.querySelector('.preview img, img.preview') || g.querySelector('.thumbnail-item.selected img') ||
            g.querySelector('.thumbnail-item img, .grid-wrap img');
        return img && img.src ? {url: img.src, kind: 'image'} : null;
    }
    function videoMedia(box) {
        const videos = box ? Array.from(box.querySelectorAll('video')).filter((v) => v.currentSrc || v.src) : [];
        const v = videos[videos.length - 1];
        return v ? {url: v.currentSrc || v.src, kind: 'video'} : null;
    }
    const blockOf = (el) => (el && (el.closest('.block') || el)) || null;

    // where each tab shows its result, and how to find the result (the newest one). txt2img, img2img and Extras: right
    // under the picture's buttons (after the whole panel it sat below the generation info, out of sight)
    const SPOTS = [
        {id: 'txt2img', anchor: () => byId('image_buttons_txt2img') || byId('txt2img_results'), dragBox: () => byId('txt2img_results'), media: () => galleryMedia('txt2img_gallery')},
        {id: 'img2img', anchor: () => byId('image_buttons_img2img') || byId('img2img_results'), dragBox: () => byId('img2img_results'), media: () => galleryMedia('img2img_gallery')},
        {id: 'extras', anchor: () => byId('image_buttons_extras') || byId('extras_results'), dragBox: () => byId('extras_results'),
            media: () => videoMedia(byId('extras_results')) || galleryMedia('extras_gallery')},
        {id: 'video', anchor: () => blockOf(byId('vs_video')), media: () => videoMedia(byId('vs_video'))},
        {id: 'deforum', anchor: () => byId('deforum_results'),
            media: () => videoMedia(byId('deforum_results')) || galleryMedia('deforum_gallery')},
        {id: 'zoom', anchor: () => byId('infinite-zoom_results'),
            media: () => { const r = byId('infinite-zoom_results'); return videoMedia(r && r.parentElement) || galleryMedia('infinite-zoom_gallery'); },
            dragBox: (anchor) => anchor.parentElement},
        {id: 'strata', anchor: () => byId('strata_host'), media: () => videoMedia(byId('strata_host'))},
    ];

    const bars = {};
    let open = null;           // the spot whose drawer holds the timeline
    let home = null;           // where the timeline lives in the editor {parent, next}

    function el(tag, attrs, ...kids) {
        const e = document.createElement(tag);
        for (const [k, v] of Object.entries(attrs || {})) {
            if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
            else if (k === 'class') e.className = v;
            else if (v != null && v !== false) e.setAttribute(k, v === true ? '' : v);
        }
        for (const k of kids) if (k != null) e.append(k);
        return e;
    }

    // only real changes touch the page (this runs every 1.5 s; the language extension watches the page)
    function setText(e, text) { if (e.textContent !== text) e.textContent = text; }
    function setTitle(e, text) { if (e.title !== text) e.title = text; }

    function lower() { return FS.timeline && FS.timeline.el ? FS.timeline.el.closest('.fx-lower') : null; }

    function makeBar(spot) {
        const b = {spot};
        const flip = () => (open === spot.id ? close() : show(spot.id));
        b.arrow = el('span', {class: 'fs-peek-arrow'}, '▼');
        b.word = el('span', {});
        b.toggle = el('button', {type: 'button', class: 'fs-peek-toggle', onclick: flip}, b.arrow, b.word);
        b.handle = el('button', {type: 'button', class: 'fs-peek-handle', hidden: true, onclick: close}, '▲');
        b.project = el('span', {class: 'fs-peek-project', dir: 'auto'});
        b.chip = el('div', {class: 'fs-peek-chip', draggable: 'true'});
        b.chip.addEventListener('dragstart', (e) => {
            const m = spot.media();
            if (!m) { e.preventDefault(); return; }
            const url = new URL(m.url, location.href).href;
            e.dataTransfer.setData('application/x-forge-studio-url', url);
            e.dataTransfer.setData('text/uri-list', url);
            e.dataTransfer.effectAllowed = 'copy';
            if (open !== spot.id) show(spot.id);   // dragging opens the drawer to drop on
        });
        b.add = el('button', {type: 'button', class: 'fs-peek-btn', onclick: () => addAtEnd(spot)});
        b.go = el('button', {type: 'button', class: 'fs-peek-btn', onclick: goToStudio});
        b.note = el('span', {class: 'fs-peek-note', dir: 'auto'});
        b.bar = el('div', {class: 'fs-peek-bar'}, b.toggle, b.project, b.chip, b.add, b.go, b.note);
        // a file from a folder brought over the bar opens the drawer; let go on the bar, it goes at the end
        const hasFiles = (e) => Array.from((e.dataTransfer && e.dataTransfer.types) || []).includes('Files');
        b.bar.addEventListener('dragover', (e) => {
            if (!hasFiles(e)) return;
            e.preventDefault();
            e.dataTransfer.dropEffect = 'copy';
            if (open !== spot.id) show(spot.id);
        });
        b.bar.addEventListener('drop', (e) => {
            if (!hasFiles(e)) return;
            e.preventDefault();
            const files = Array.from(e.dataTransfer.files || []).filter((f) => /^(image|video|audio)\//.test(f.type));
            if (files.length) FS.guard(() => FS.addOutside({files}, {time: FS.total()}))();
        });
        b.drawer = el('div', {class: 'fs-peek-drawer fx-app fs-peek-app', hidden: true, dir: 'ltr'});
        b.root = el('div', {class: 'fs-peek hebrew-ui-skip', 'data-spot': spot.id}, b.bar, b.drawer, b.handle);
        return b;
    }

    function label(b) {
        const isOpen = open === b.spot.id;
        setText(b.word, T('Timeline'));
        if (b.toggle.classList.contains('fs-peek-open') !== isOpen) b.toggle.classList.toggle('fs-peek-open', isOpen);
        setTitle(b.toggle, isOpen ? T('Close the timeline') : T('Open the Forge Studio Editor timeline here: drag the result onto it'));
        setTitle(b.handle, T('Close the timeline (▲)'));
        if (b.handle.hidden === isOpen) b.handle.hidden = !isOpen;
        setText(b.project, FS.state && FS.state.project ? '🎞️ ' + FS.state.project : '');
        const m = b.spot.media();
        if (b.chip.classList.contains('fs-peek-empty') === !!m) b.chip.classList.toggle('fs-peek-empty', !m);
        setTitle(b.chip, m ? T('Drag this onto the timeline') : T('Nothing to drag yet: make a result first'));
        const want = m ? m.url : '';
        if (b.chip.dataset.url !== want) {
            b.chip.dataset.url = want;
            b.chip.innerHTML = '';
            if (m && m.kind === 'image') b.chip.append(el('img', {src: m.url, alt: '', draggable: 'false'}));
            else if (m) b.chip.append(el('video', {src: m.url, muted: true, preload: 'metadata', draggable: 'false'}));
            b.chip.append(el('span', {}, m ? '✋ ' + T('Drag') : '—'));
        } else if (b.chip.lastChild) setText(b.chip.lastChild, m ? '✋ ' + T('Drag') : '—');
        setText(b.add, '➕ ' + T('Add at the end'));
        if (b.add.disabled !== !m) b.add.disabled = !m;
        setText(b.go, '↗ ' + T('Open Forge Studio Editor'));
    }

    async function show(id) {
        const b = bars[id];
        if (!b) return;
        if (!FS.begin) return;
        await FS.begin();
        const tl = lower();
        if (!tl) return;
        if (!home) home = {parent: tl.parentElement, next: tl.nextSibling};
        if (open && open !== id && bars[open]) { bars[open].drawer.hidden = true; }
        open = id;
        if (FS.acceptOutsideDrops) FS.acceptOutsideDrops(b.drawer);   // files from a folder, anywhere in the drawer
        b.drawer.hidden = false;
        b.drawer.classList.remove('fs-peek-opening');
        void b.drawer.offsetWidth;
        b.drawer.classList.add('fs-peek-opening');
        b.drawer.prepend(tl);
        Object.values(bars).forEach(label);
        requestAnimationFrame(() => { FS.timeline.render(); FS.timeline.fit(); });
    }

    function close() {
        const tl = lower();
        if (open && bars[open]) bars[open].drawer.hidden = true;
        open = null;
        if (tl && home && home.parent && tl.parentElement !== home.parent) {
            home.parent.insertBefore(tl, home.next && home.next.parentElement === home.parent ? home.next : null);
            requestAnimationFrame(() => { if (FS.timeline.el.offsetParent) FS.timeline.render(); });
        }
        Object.values(bars).forEach(label);
    }

    async function addAtEnd(spot) {
        const m = spot.media();
        if (!m) return;
        if (open !== spot.id) await show(spot.id);
        await FS.guard(() => FS.addOutside({urls: [new URL(m.url, location.href).href]}, {time: FS.total()}))();
    }

    function goToStudio() {
        close();
        const tabs = (window.gradio_config && window.gradio_config.components || []).find(
            (c) => c.type === 'tabs' && c.props && c.props.elem_id === 'tabs');
        if (tabs && tabs.instance) { tabs.props.selected = 'forge_studio'; tabs.instance.$set({selected: 'forge_studio'}); }
        else { const btn = Array.from(app().querySelectorAll('#tabs > .tab-nav button')).find((x) => /Forge Studio/.test(x.textContent)); if (btn) btn.click(); }
        window.scrollTo({top: 0});
    }

    function dragResults(spot, box) {
        if (!box || box.dataset.fsDrag) return;
        box.dataset.fsDrag = '1';
        box.addEventListener('pointerdown', (e) => {
            const v = e.target;
            if (v.tagName !== 'VIDEO' || v.closest('.fs-peek')) return;
            // above the controls the picture drags; on them they still play and seek
            v.draggable = e.clientY < v.getBoundingClientRect().bottom - 56;
            if (v.draggable) v.title = T('Drag the video onto the timeline');
        }, true);
        box.addEventListener('dragstart', (e) => {
            const m = e.target;
            if (!m || m.closest('.fs-peek') || (m.tagName !== 'VIDEO' && m.tagName !== 'IMG')) return;
            const src = m.currentSrc || m.src;
            if (!src || src.startsWith('data:')) return;
            const url = new URL(src, location.href).href;
            e.dataTransfer.setData('application/x-forge-studio-url', url);
            if (m.tagName === 'VIDEO') e.dataTransfer.setData('text/uri-list', url);
            e.dataTransfer.effectAllowed = 'copy';
            if (open !== spot.id) show(spot.id);   // the drawer opens under it, ready to drop on
        }, true);
    }

    function ensure() {
        for (const spot of SPOTS) {
            const anchor = spot.anchor();
            if (!anchor) continue;
            let b = bars[spot.id];
            if (!b) b = bars[spot.id] = makeBar(spot);
            dragResults(spot, spot.dragBox ? spot.dragBox(anchor) : anchor);
            if (b.root.previousElementSibling !== anchor) anchor.insertAdjacentElement('afterend', b.root);
            label(b);
        }
        // the editor shown again: its timeline goes home
        const host = byId('fstudio_host');
        if (open && host && host.offsetParent !== null) close();
    }

    function say(text, kind) {
        if (!open || !bars[open]) return;
        const note = bars[open].note;
        note.textContent = text || '';
        note.className = 'fs-peek-note' + (kind ? ' fs-peek-' + kind : '');
        clearTimeout(say.timer);
        if (text) say.timer = setTimeout(() => { note.textContent = ''; }, kind === 'error' ? 9000 : 4500);
    }

    function boot() {
        if (!FS.on) return setTimeout(boot, 500);
        FS.on('message', (m) => say(m && m.text, m && m.kind));
        FS.on('busy', (text) => { if (text) say(text); });
        FS.on('project', () => Object.values(bars).forEach(label));
        ensure();
        setInterval(ensure, 1500);
    }
    if (typeof onUiLoaded === 'function') onUiLoaded(boot); else window.addEventListener('load', boot);
})();

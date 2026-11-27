/* Forge Workspace: the recent generations under each result display, and one viewer for pictures and videos.
 *
 * - Under txt2img / img2img / Extras' results, a strip of the newest outputs of all of Forge (pictures and videos,
 *   from Forge Studio's library index: /forge-studio/library), newest first, ‹ › to scroll it.
 * - A click on the result picture (Forge's own lightbox is not opened) or on a strip item opens the viewer: the
 *   mouse wheel zooms at the pointer, a drag moves the zoomed picture, a double-click goes between fit and 2x;
 *   ‹ › and the arrow keys go to the earlier and later ones (this generation first, then the strip), ⛶ full screen,
 *   Esc closes. For a picture of this generation the result's own buttons (send to img2img, inpaint, Extras,
 *   Panorama, video ...) come into the viewer and act on it; for an older one: open its folder, send it to Forge
 *   Studio's editor.
 */
(function () {
    'use strict';
    const SPOTS = ['txt2img', 'img2img', 'extras'];   // result galleries whose picture opens the viewer
    // where a Recent strip goes: after the result's buttons, and in Video Studio after its row of small buttons
    const STRIPS = {txt2img: 'image_buttons_txt2img', img2img: 'image_buttons_img2img', extras: 'image_buttons_extras',
        video_studio: 'vs_actions'};
    const STRIP_TABS = Object.keys(STRIPS);
    const he = () => document.documentElement.classList.contains('hebrew-ui-active');
    const STR = {
        recent: ['Recent', 'אחרונים'], close: ['Close (Esc)', 'סגירה (Esc)'], full: ['Full screen', 'מסך מלא'],
        fit: ['Fit / actual size (double-click)', 'התאמה / גודל אמיתי (לחיצה כפולה)'], prev: ['Earlier', 'קודם'], next: ['Later', 'הבא'],
        folder: ['Open its folder', 'פתיחת התיקייה'], editor: ['Send to Forge Studio Editor', 'שליחה לעורך Forge Studio'],
        refresh: ['Look for new ones', 'חיפוש חדשים'], none: ['Nothing yet: what you make shows here', 'עוד אין כלום: מה שתיצור יופיע כאן'],
        hint: ['Wheel: zoom · drag: move · double-click: fit', 'גלגלת: זום · גרירה: הזזה · לחיצה כפולה: התאמה'],
        thisOne: ['this generation', 'הג׳ינרוט הזה'],
    };
    const t = (k) => STR[k][he() ? 1 : 0];
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
    const norm = (p) => String(p || '').replace(/\\/g, '/').toLowerCase();
    const fileOf = (src) => { try { const u = new URL(src, location.href); const f = u.pathname.match(/\/file=(.*)$/); return f ? decodeURIComponent(f[1]) : ''; } catch (e) { return ''; } };

    // ------------------------------------------------------------------ the recent outputs

    let recent = [], loadedAt = 0, loading = null;
    const skip = (it) => /^grid-/i.test(it.name) || /\.(first|last)\.png$/i.test(it.name) || !/^(image|video)$/.test(it.kind);
    async function loadRecent(force) {
        if (loading) return loading;
        if (!force && Date.now() - loadedAt < 4000) return recent;
        const take = async () => {
            const d = await (await fetch('./forge-studio/library?limit=90')).json();
            const items = (d.items || []).filter((it) => !skip(it));
            // Video Studio keeps a clip and its copy with sound (<name>-sound.mp4): the strip shows the one with sound
            const voiced = new Set(items.filter((it) => /-sound\.[^.]+$/i.test(it.name)).map((it) => norm(it.path).replace(/-sound(\.[^.]+)$/, '$1')));
            const next = items.filter((it) => !voiced.has(norm(it.path))).slice(0, 60);
            const changed = next.length !== recent.length || next.some((it, k) => it.path !== recent[k].path);
            recent = next;
            loadedAt = Date.now();
            if (changed) for (const tab of STRIP_TABS) drawStrip(tab);
        };
        loading = (async () => {
            try {
                // what the index has now, at once; then the folders that changed are listed again and it looks again
                if (!recent.length) await take();
                await fetch('./forge-studio/library/scan', {method: 'POST'}).catch(() => null);
                await new Promise((r) => setTimeout(r, 900));
                await take();
            } catch (e) { /* Forge Studio missing: no strip */ }
            loading = null;
            return recent;
        })();
        return loading;
    }

    function drawStrip(tab) {
        const box = app().querySelector(`#ws_recent_${tab} .ws-r-row`);
        if (!box) return;
        box.replaceChildren(...(recent.length ? recent.map((it, i) => h('button', {type: 'button', class: 'ws-r-item' + (it.kind === 'video' ? ' video' : ''),
            title: it.name, onclick: () => openViewer(tab, recentList(), i)},
        h('img', {src: it.thumb, alt: '', loading: 'lazy', draggable: 'false'}), it.kind === 'video' ? h('span', {class: 'ws-r-play'}, '▶') : null))
            : [h('span', {class: 'ws-r-none'}, t('none'))]));
        const label = app().querySelector(`#ws_recent_${tab} .ws-r-label`);
        if (label) label.textContent = t('recent');
    }
    const recentList = () => recent.map((it) => ({src: it.url, kind: it.kind, name: it.name, path: it.path, thumb: it.thumb}));

    function placeStrips() {
        for (const tab of STRIP_TABS) {
            const anchor = app().querySelector('#' + STRIPS[tab]);
            if (!anchor || app().querySelector(`#ws_recent_${tab}`)) continue;
            const row = h('div', {class: 'ws-r-row'});
            const scroll = (d) => () => row.scrollBy({left: d * row.clientWidth * 0.8, behavior: 'smooth'});
            const strip = h('div', {id: `ws_recent_${tab}`, class: 'ws-recent hebrew-ui-skip'},
                h('div', {class: 'ws-r-head'}, h('span', {class: 'ws-r-label'}, t('recent')),
                    h('button', {type: 'button', class: 'ws-r-btn', title: t('refresh'), onclick: () => loadRecent(true)}, '⟳')),
                h('button', {type: 'button', class: 'ws-r-arrow prev', onclick: scroll(-1)}, '‹'), row,
                h('button', {type: 'button', class: 'ws-r-arrow next', onclick: scroll(1)}, '›'));
            row.addEventListener('wheel', (e) => { if (Math.abs(e.deltaY) > Math.abs(e.deltaX)) { row.scrollLeft += e.deltaY; e.preventDefault(); } }, {passive: false});
            anchor.after(strip);
            drawStrip(tab);
        }
    }

    // ------------------------------------------------------------------ the viewer

    let V = null;   // {tab, list, i, el, stage, media, z, x, y, buttons: {node, home}}

    function galleryList(tab) {
        const g = app().querySelector(`#${tab}_gallery`);
        if (!g) return [];
        const thumbs = [...g.querySelectorAll('.thumbnail-item img, .thumbnails img')];
        const imgs = thumbs.length ? thumbs : [...g.querySelectorAll('img')].slice(0, 1);
        return imgs.map((img, k) => ({src: img.src, kind: /\.(mp4|webm|mov)(\?|$)/i.test(img.src) ? 'video' : 'image', name: fileOf(img.src).split('/').pop() || '', path: fileOf(img.src), gallery: k}));
    }

    function openFromGallery(tab, img) {
        const mine = galleryList(tab);
        const seen = new Set(mine.map((m) => norm(m.path)));
        const list = mine.concat(recentList().filter((r) => !seen.has(norm(r.path))));
        let i = mine.findIndex((m) => m.src === img.src);
        if (i < 0) {   // the large picture of a gallery: the selected thumbnail
            const sel = app().querySelector(`#${tab}_gallery .thumbnail-item.selected img`);
            i = sel ? Math.max(0, mine.findIndex((m) => m.src === sel.src)) : 0;
            if (!mine.length) { list.unshift({src: img.src, kind: 'image', name: fileOf(img.src).split('/').pop(), path: fileOf(img.src), gallery: 0}); i = 0; }
        }
        openViewer(tab, list, i);
    }

    function openViewer(tab, list, i) {
        closeViewer();
        const stage = h('div', {class: 'ws-v-stage'});
        const info = h('div', {class: 'ws-v-info'});
        const tools = h('div', {class: 'ws-v-tools'});
        const el = h('div', {class: 'ws-viewer hebrew-ui-skip', role: 'dialog', dir: he() ? 'rtl' : 'ltr'},
            h('div', {class: 'ws-v-top'}, info, h('span', {class: 'ws-v-hint'}, t('hint')),
                h('button', {type: 'button', class: 'ws-v-btn', title: t('fit'), onclick: () => toggleZoom()}, '⤢'),
                h('button', {type: 'button', class: 'ws-v-btn', title: t('full'), onclick: fullScreen}, '⛶'),
                h('button', {type: 'button', class: 'ws-v-btn', title: t('close'), onclick: closeViewer}, '✕')),
            stage,
            h('button', {type: 'button', class: 'ws-v-nav prev', title: t('prev'), onclick: () => go(1)}, '‹'),
            h('button', {type: 'button', class: 'ws-v-nav next', title: t('next'), onclick: () => go(-1)}, '›'),
            tools);
        document.body.append(el);
        V = {tab, list, i, el, stage, info, tools, z: 1, x: 0, y: 0, buttons: null};
        stage.addEventListener('wheel', onWheel, {passive: false});
        stage.addEventListener('pointerdown', onDown);
        stage.addEventListener('dblclick', (e) => toggleZoom(e));
        stage.addEventListener('click', (e) => { if (e.target === stage && !V.moved) closeViewer(); });
        window.addEventListener('keydown', onKey, true);
        show();
    }

    // the list runs newest first: "earlier" (‹, ←) is the next item in it
    function go(d) {
        if (!V) return;
        const n = V.i + d;
        if (n < 0 || n >= V.list.length) return;
        V.i = n;
        show();
    }

    function show() {
        const it = V.list[V.i];
        V.z = 1; V.x = 0; V.y = 0;
        let media;
        if (it.kind === 'video') {
            media = h('video', {src: it.src, controls: true, autoplay: true, loop: true, playsinline: true, class: 'ws-v-media'});
        } else {
            media = h('img', {src: it.src, alt: '', draggable: 'false', class: 'ws-v-media'});
        }
        V.stage.replaceChildren(media);
        V.media = media;
        apply();
        const n = V.list.length;
        V.info.replaceChildren(...[h('b', {}, `${V.i + 1} / ${n}`), h('span', {dir: 'auto'}, ' ' + (it.name || '')),
            it.gallery != null ? h('span', {class: 'ws-v-badge'}, t('thisOne')) : null].filter(Boolean));
        V.el.querySelector('.ws-v-nav.prev').disabled = V.i >= n - 1;
        V.el.querySelector('.ws-v-nav.next').disabled = V.i <= 0;
        tools(it);
    }

    // the result's own buttons for a picture of this generation (the gallery selects it, so they act on it)
    function tools(it) {
        giveBack();
        V.tools.replaceChildren();
        if (it.gallery != null) {
            const thumb = app().querySelectorAll(`#${V.tab}_gallery .thumbnail-item`)[it.gallery];
            if (thumb && !thumb.classList.contains('selected')) thumb.click();
            const node = app().querySelector(`#image_buttons_${V.tab}`);
            if (node) {
                V.buttons = {node, parent: node.parentElement, next: node.nextSibling};
                V.tools.append(node);
                // a button sends the picture somewhere (img2img, Extras, Panorama, Video Studio ...): the viewer steps
                // aside so that is seen (the click itself reaches the button first)
                if (!node.dataset.wsCloses) {
                    node.dataset.wsCloses = '1';
                    node.addEventListener('click', (e) => {
                        if (V && V.buttons && V.buttons.node === node && e.target.closest('button')) setTimeout(closeViewer, 250);
                    });
                }
            }
            return;
        }
        if (it.path) {
            V.tools.append(h('button', {type: 'button', class: 'ws-v-tool', onclick: () => fetch('./forge-studio/open-folder', {method: 'POST',
                headers: {'Content-Type': 'application/json'}, body: JSON.stringify({path: it.path})})}, '📁 ' + t('folder')));
            if (window.ForgeStudio && window.ForgeStudio.send) {
                V.tools.append(h('button', {type: 'button', class: 'ws-v-tool', onclick: () => { const p = it.path; closeViewer(); window.ForgeStudio.send([p]); }}, '🎞️ ' + t('editor')));
            }
        }
    }
    function giveBack() {
        if (!V || !V.buttons) return;
        const {node, parent, next} = V.buttons;
        if (next && next.parentElement === parent) parent.insertBefore(node, next); else parent.append(node);
        V.buttons = null;
    }

    function closeViewer() {
        if (!V) return;
        giveBack();
        if (document.fullscreenElement === V.el) document.exitFullscreen().catch(() => null);
        V.el.remove();
        window.removeEventListener('keydown', onKey, true);
        V = null;
    }
    function fullScreen() {
        if (!V) return;
        if (document.fullscreenElement) document.exitFullscreen().catch(() => null);
        else V.el.requestFullscreen().catch(() => null);
    }

    function apply() {
        if (!V || !V.media) return;
        V.media.style.transform = `translate(${V.x}px, ${V.y}px) scale(${V.z})`;
        V.stage.classList.toggle('zoomed', V.z > 1.01);
    }
    // zoom by `k` keeping the point under (cx, cy) where it is
    function zoomAt(k, cx, cy) {
        const r = V.stage.getBoundingClientRect();
        const px = cx - (r.left + r.width / 2), py = cy - (r.top + r.height / 2);
        const z = Math.max(1, Math.min(12, V.z * k));
        k = z / V.z;
        V.x = px - (px - V.x) * k;
        V.y = py - (py - V.y) * k;
        V.z = z;
        if (V.z <= 1.001) { V.x = 0; V.y = 0; }
        apply();
    }
    function onWheel(e) {
        if (!V || V.media.tagName === 'VIDEO' && e.target === V.media && e.offsetY > V.media.clientHeight - 48) return;
        e.preventDefault();
        zoomAt(Math.exp(-e.deltaY * 0.0015), e.clientX, e.clientY);
    }
    function toggleZoom(e) {
        if (!V) return;
        const r = V.stage.getBoundingClientRect();
        if (V.z > 1.01) { V.z = 1; V.x = 0; V.y = 0; apply(); } else zoomAt(2.5, e ? e.clientX : r.left + r.width / 2, e ? e.clientY : r.top + r.height / 2);
    }
    function onDown(e) {
        if (!V || e.button !== 0 || V.z <= 1.01) { if (V) V.moved = false; return; }
        if (V.media.tagName === 'VIDEO' && e.offsetY > V.media.clientHeight - 48) return;   // the video's controls
        e.preventDefault();
        const sx = e.clientX, sy = e.clientY, ox = V.x, oy = V.y;
        V.moved = false;
        V.stage.setPointerCapture(e.pointerId);
        V.stage.classList.add('dragging');
        const move = (ev) => { V.x = ox + ev.clientX - sx; V.y = oy + ev.clientY - sy; V.moved = V.moved || Math.abs(ev.clientX - sx) + Math.abs(ev.clientY - sy) > 4; apply(); };
        const up = () => { V.stage.removeEventListener('pointermove', move); V.stage.removeEventListener('pointerup', up); V.stage.classList.remove('dragging'); };
        V.stage.addEventListener('pointermove', move);
        V.stage.addEventListener('pointerup', up);
    }
    function onKey(e) {
        if (!V) return;
        const k = e.key;
        if (k === 'Escape') { if (document.fullscreenElement) return; e.preventDefault(); e.stopPropagation(); closeViewer(); }
        else if (k === 'ArrowLeft' || k === 'ArrowRight') {
            e.preventDefault(); e.stopPropagation();
            const earlier = (k === 'ArrowLeft') !== (V.el.dir === 'rtl');
            go(earlier ? 1 : -1);
        } else if (k === '+' || k === '=') { e.preventDefault(); toggleZoom(); }
        else if (k === '0') { V.z = 1; V.x = 0; V.y = 0; apply(); }
        else if (k === 'f' || k === 'F') { e.preventDefault(); fullScreen(); }
    }

    // the click on a result picture opens this viewer instead of Forge's lightbox (caught on its way down)
    document.addEventListener('click', (e) => {
        if (e.button !== 0) return;
        const img = e.target.closest && e.target.closest('img');
        if (!img) return;
        const tab = SPOTS.find((s) => img.closest(`#${s}_gallery`));
        if (!tab || img.closest('.thumbnail-item, .thumbnails, button')) return;
        e.preventDefault();
        e.stopImmediatePropagation();
        loadRecent(false).then(() => openFromGallery(tab, img));
    }, true);

    // A video result (AnimateDiff lists its GIF and its MP4 as the result; the gallery shows the GIF and a broken square
    // for the MP4): the result's window plays the video itself.
    const VIDEO_RE = /\.(mp4|webm|mov)(\?|$)/i;
    function videoResult(tab) {
        const g = app().querySelector(`#${tab}_gallery`);
        if (!g) return;
        const vids = [...g.querySelectorAll('img')].map((i) => i.src).filter((u) => VIDEO_RE.test(u || ''));
        let box = g.querySelector(':scope > .ws-video-result');
        if (!vids.length) {
            if (box) box.remove();
            g.classList.remove('ws-has-video');
            return;
        }
        const src = vids[vids.length - 1];
        if (!box) {
            box = h('div', {class: 'ws-video-result'});
            g.append(box);
            // exactly the gallery's size (a percent height did not hold inside it)
            const fit = () => { box.style.height = g.clientHeight + 'px'; };
            new ResizeObserver(fit).observe(g);
            fit();
        }
        g.classList.add('ws-has-video');
        if (box.dataset.src === src) return;
        box.dataset.src = src;
        const video = h('video', {src, controls: true, autoplay: true, loop: true, playsinline: true});
        video.muted = true;   // it may start by itself only without sound; the controls turn the sound on
        const item = {src, kind: 'video', name: fileOf(src).split('/').pop(), path: fileOf(src), gallery: null};
        box.replaceChildren(video, h('button', {type: 'button', class: 'ws-vr-open', title: t('full'),
            onclick: () => openViewer(tab, [item].concat(recentList().filter((r) => norm(r.path) !== norm(item.path))), 0)}, '⛶'));
    }

    // new outputs: the strip looks again when a result changes, and when its tab is opened
    function watch() {
        const vs = app().querySelector('#vs_video');
        if (vs && !vs.dataset.wsWatched) {
            vs.dataset.wsWatched = '1';
            let timer = 0;
            new MutationObserver(() => { clearTimeout(timer); timer = setTimeout(() => loadRecent(true), 2500); })
                .observe(vs, {subtree: true, childList: true, attributes: true, attributeFilter: ['src']});
        }
        for (const tab of SPOTS) {
            const g = app().querySelector(`#${tab}_gallery`);
            if (!g || g.dataset.wsWatched) continue;
            g.dataset.wsWatched = '1';
            let timer = 0;
            new MutationObserver(() => { videoResult(tab); clearTimeout(timer); timer = setTimeout(() => loadRecent(true), 1500); })
                .observe(g, {subtree: true, childList: true, attributes: true, attributeFilter: ['src']});
            videoResult(tab);
        }
    }

    onUiLoaded(() => {
        let n = 0;
        let asked = false;
        const start = () => {
            placeStrips();
            watch();
            // the first strip that is there fills at once (not after all three tabs are)
            if (!asked && STRIP_TABS.some((s) => app().querySelector(`#ws_recent_${s}`))) { asked = true; loadRecent(true); }
            if (STRIP_TABS.every((s) => app().querySelector(`#ws_recent_${s}`)) || ++n > 20) clearInterval(again);
        };
        const again = setInterval(start, 700);
        start();
        onUiTabChange(() => loadRecent(false));
        new MutationObserver(() => { for (const tab of STRIP_TABS) drawStrip(tab); })
            .observe(document.documentElement, {attributes: true, attributeFilter: ['class']});
    });
})();

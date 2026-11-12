/* Forge Studio - the editor's frame: the bar on top, the panels (Media, AI, Sound, Text) on one side, the player
 * in the middle, the inspector on the other side, the timeline below. It fills the tab from its top to the bottom
 * of the window; ⛶ lays it over all of Forge. Also: the library, the keyboard, exports, and the 🎞️ buttons that
 * send results from the other tabs.
 */
(function () {
    'use strict';
    const FS = window.ForgeStudio = window.ForgeStudio || {};
    const S = FS.state;
    const T = (...a) => FS.T(...a);
    const UI = FS.UI = {};
    const q = encodeURIComponent;

    function h(tag, attrs, ...children) {
        const el = document.createElement(tag);
        for (const [k, v] of Object.entries(attrs || {})) {
            if (v == null || v === false) continue;
            if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
            else if (k === 'class') el.className = v;
            else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
            else el.setAttribute(k, v === true ? '' : v);
        }
        for (const c of children.flat()) if (c != null && c !== false) el.append(c.nodeType ? c : document.createTextNode(String(c)));
        return el;
    }
    FS.h = h;

    FS.message = function (text, kind) {
        if (!UI.toast) return;
        UI.toast.textContent = text || '';
        UI.toast.className = 'fx-toast' + (text ? ' show' : '') + (kind ? ' fx-' + kind : '');
        clearTimeout(UI.toastTimer);
        if (text) UI.toastTimer = setTimeout(() => { UI.toast.className = 'fx-toast'; }, kind === 'error' ? 9000 : 4500);
    };
    FS.guard = (fn) => async (...args) => {
        try { return await fn(...args); } catch (e) { console.error(e); FS.message(e.message || String(e), 'error'); }
    };
    FS.busy = function (text) { if (UI.busy) { UI.busy.textContent = text || ''; UI.busy.hidden = !text; } };

    FS.panels = FS.panels || [];

    /* ---- top bar ---- */

    const ASPECTS = [['9:16', 1080, 1920], ['16:9', 1920, 1080], ['1:1', 1080, 1080], ['4:5', 1080, 1350], ['3:4', 1080, 1440]];

    function topBar() {
        UI.projectBtn = h('button', {class: 'fx-project', onclick: (e) => projectMenu(e)}, h('span', {class: 'fx-logo'}, '◆'), h('span', {class: 'fx-pname'}, T('No project open')), ' ▾');
        UI.saved = h('span', {class: 'fx-saved'});
        UI.aspect = h('select', {class: 'fx-select', title: T('Shape of the video'), onchange: FS.guard(() => {
            const a = ASPECTS.find((x) => x[0] === UI.aspect.value);
            if (a) FS.change('shape', (d) => { d.settings.width = a[1]; d.settings.height = a[2]; });
            FS.player.resize();
        })}, ...ASPECTS.map(([n]) => h('option', {value: n}, n)), h('option', {value: 'custom', hidden: true}, T('Custom')));
        UI.full = h('button', {class: 'fx-icon', title: T('Over all of Forge (Esc to return)'), onclick: () => setFull(!UI.root.classList.contains('fx-full'))}, '⛶');
        UI.exportBtn = h('button', {class: 'fx-primary', onclick: FS.guard(() => FS.openExport())}, '⬆ ' + T('Export'));
        const still = h('button', {class: 'fx-icon', title: T('Save the frame at the playhead as a picture (every layer, full size)'), onclick: FS.guard(async () => {
            if (!S.project) throw new Error(T('Open or create a project first'));
            still.disabled = true;
            try {
                FS.player.pause();
                const cv = await FS.player.fullFrame();
                await window.forgeFrameShare.open({canvas: cv, filename: S.project + '-frame-' + S.time.toFixed(2) + '.png', host: UI.root});
            } finally { still.disabled = false; }
        })}, '📷');
        UI.busy = h('span', {class: 'fx-busy', hidden: true});
        return h('div', {class: 'fx-top'}, UI.projectBtn, UI.saved, UI.busy, h('span', {class: 'fx-spacer'}),
            h('label', {class: 'fx-inline'}, T('Shape'), UI.aspect), UI.full, still, UI.exportBtn);
    }

    /* the preview bigger: the side panels fold away (remembered), or the picture alone on the whole screen */
    function setWide(on) {
        UI.root.classList.toggle('fx-wide', on);
        if (UI.wideBtn) UI.wideBtn.classList.toggle('on', on);
        try { localStorage.setItem('forgeStudio.wide', on ? '1' : ''); } catch (e) { /* storage off */ }
        setTimeout(() => { FS.player.resize(); FS.timeline.render(); }, 30);
    }
    function previewFull() {
        if (document.fullscreenElement) { document.exitFullscreen(); return; }
        if (UI.stage.requestFullscreen) UI.stage.requestFullscreen().catch(() => setFull(true));
    }
    document.addEventListener('fullscreenchange', () => {
        if (UI.stage) UI.stage.classList.toggle('fx-stage-full', document.fullscreenElement === UI.stage);
        setTimeout(() => FS.player.resize(), 60);
    });

    function setFull(on) {
        UI.root.classList.toggle('fx-full', on);
        document.body.classList.toggle('fx-noscroll', on);
        try { localStorage.setItem('forgeStudio.full', on ? '1' : ''); } catch (e) { /* storage off */ }
        fitHeight();
        setTimeout(() => { FS.player.resize(); FS.timeline.render(); }, 30);
    }
    FS.setFull = setFull;

    function popover(anchor, ...content) {
        closePopover();
        const p = UI.pop = h('div', {class: 'fx-pop', dir: FS.hebrew() ? 'rtl' : 'ltr'}, ...content);
        UI.root.append(p);
        const r = anchor.getBoundingClientRect(), root = UI.root.getBoundingClientRect(), pr = p.getBoundingClientRect();
        p.style.top = (r.bottom - root.top + 6) + 'px';
        p.style.left = Math.max(8, Math.min(root.width - pr.width - 8, r.left - root.left)) + 'px';
        setTimeout(() => window.addEventListener('pointerdown', outside), 0);
        return p;
    }
    function outside(e) { if (UI.pop && !UI.pop.contains(e.target)) closePopover(); }
    function closePopover() { if (UI.pop) UI.pop.remove(); UI.pop = null; window.removeEventListener('pointerdown', outside); }
    FS.closePopover = closePopover;

    async function projectMenu(e) {
        const list = (await FS.api('projects')).projects;
        const name = h('input', {class: 'fx-input', value: FS.defaultProjectName(), id: 'fx-new-name'});
        const shape = h('select', {class: 'fx-select', id: 'fx-new-shape'}, ...ASPECTS.map(([n]) => h('option', {value: n}, n)));
        const create = h('button', {class: 'fx-primary', onclick: FS.guard(async () => {
            const a = ASPECTS.find((x) => x[0] === shape.value);
            closePopover();
            await FS.newProject(name.value.trim(), {width: a[1], height: a[2]});
        })}, '+ ' + T('New project'));
        popover(e.currentTarget,
            h('div', {class: 'fx-pop-title'}, T('New project')),
            h('div', {class: 'fx-row'}, name, shape), create,
            h('div', {class: 'fx-pop-title'}, T('Projects')),
            h('div', {class: 'fx-list'}, ...list.map((p) => h('button', {class: 'fx-list-item' + (p.name === S.project ? ' on' : ''),
                onclick: FS.guard(async () => { closePopover(); await FS.openProject(p.name); })}, p.name,
            h('span', {class: 'fx-dim'}, new Date(p.saved * 1000).toLocaleString())))));
        name.select();
    }

    /* the export: text clips are drawn here (as the player draws them) and sent, then the server renders */
    FS.exportNow = async function (preset, loudness, onProgress, options) {
        if (!S.project) throw new Error(T('Open or create a project first'));
        await FS.save();
        const {width: W, height: H} = S.doc.settings;
        await document.fonts.ready;
        // just the marked part: a copy of the edit cut to it (FS.docInRange); the edit itself is not touched
        const part = options && options.onlyRange && FS.range();
        const doc = part ? FS.docInRange(S.doc, part.in, part.out) : S.doc;
        const png = async (c, upto) => {
            const cv = document.createElement('canvas');
            cv.width = W;
            cv.height = H;
            FS.drawText(cv.getContext('2d'), W, H, c.text, upto);
            return new Promise((r) => cv.toBlob(r, 'image/png'));
        };
        for (const c of doc.clips.filter((x) => x.kind === 'text')) {
            await FS.api(`render/text?name=${q(S.project)}&clip=${q(c.id)}`, await png(c), true);
            if (!FS.typing(c)) continue;
            // the typewriter: the text with each count of letters a frame of the clip shows (render.typed_count)
            const n = FS.typeSteps(c.text), fps = S.doc.settings.fps, ks = new Set();
            for (let i = 0, frames = Math.max(1, Math.round(c.duration * fps)); i < frames; i++) ks.add(FS.typedCount(c, i / fps, n));
            let first = true;
            for (const k of [...ks].filter((k) => k < n).sort((a, b) => a - b)) {
                await FS.api(`render/text?name=${q(S.project)}&clip=${q(c.id)}&step=${k}&steps=${n}${first ? '&reset=1' : ''}`, await png(c, k), true);
                first = false;
            }
        }
        if (options) { options = Object.assign({}, options); delete options.onlyRange; }
        let job = await FS.api('render/start', {name: S.project, doc, preset, loudness, options: options || null});
        while (job.state === 'running') {
            if (onProgress) onProgress(job);
            await new Promise((r) => setTimeout(r, 700));
            job = await FS.api('render?id=' + job.id);
        }
        if (onProgress) onProgress(job);
        if (job.state === 'error') throw new Error(T('Export failed: {0}', job.message));
        if (job.state === 'done') {
            FS.message(T('Exported in {0} s ({1} frames a second)', Math.round(job.took), job.fps));
            const p = UI.pop;
            if (p) p.append(h('div', {class: 'fx-row'},
                h('a', {href: '/forge-studio/render/file?id=' + job.id, target: '_blank', class: 'fx-link'}, '▶ ' + T('Watch')),
                h('a', {href: '#', class: 'fx-link', onclick: (e) => { e.preventDefault(); FS.api('open-folder', {path: job.out}); }}, '📁 ' + T('Open folder'))));
            FS.emit('exported', job);
        }
        return job;
    };

    /* ---- the player area ---- */

    function stage() {
        UI.canvas = h('canvas', {class: 'fx-canvas'});
        UI.canvasBox = h('div', {class: 'fx-canvas-box'}, UI.canvas);
        UI.playBtn = h('button', {class: 'fx-play', title: T('Play / pause (Space)'), onclick: () => FS.player.toggle()}, '▶');
        UI.time = h('span', {class: 'fx-time'});
        const step = (d) => () => FS.seek(FS.snapTime(S.time + d));
        const transport = h('div', {class: 'fx-transport', dir: 'ltr'},
            h('button', {class: 'fx-icon', title: T('To the start (Home)'), onclick: () => FS.seek(0)}, '⏮'),
            h('button', {class: 'fx-icon', title: T('A frame back (←)'), onclick: step(-1 / FS.fps())}, '◂'),
            UI.playBtn,
            h('button', {class: 'fx-icon', title: T('A frame on (→)'), onclick: step(1 / FS.fps())}, '▸'),
            h('button', {class: 'fx-icon', title: T('To the end (End)'), onclick: () => FS.seek(FS.total())}, '⏭'),
            UI.time,
            h('span', {class: 'fx-transport-end'},
                UI.wideBtn = h('button', {class: 'fx-icon', title: T('Bigger preview: the side panels away (P)'), onclick: () => setWide(!UI.root.classList.contains('fx-wide'))}, '⇔'),
                h('button', {class: 'fx-icon', title: T('The preview on the whole screen (F, Esc to return)'), onclick: () => previewFull()}, '⛶')));
        UI.stage = h('div', {class: 'fx-stage'}, UI.canvasBox, transport);
        UI.canvas.addEventListener('pointerdown', canvasPointer);
        UI.canvas.addEventListener('wheel', canvasWheel, {passive: false});
        return UI.stage;
    }

    /* the topmost clip under a point of the picture (edit pixels), selected first if it is there */
    function clipAt(px, py) {
        const order = FS.visualTracks().filter((t) => !t.hidden).map((t) => t.id);
        const here = S.doc.clips.filter((c) => order.includes(c.track) && S.time >= c.start && S.time < FS.end(c))
            .sort((a, b) => order.indexOf(b.track) - order.indexOf(a.track));
        const inside = (c) => {
            const b = FS.player.box(c);
            const dx = px - b.cx, dy = py - b.cy, ca = Math.cos(-b.angle), sa = Math.sin(-b.angle);
            const lx = dx * ca - dy * sa, ly = dx * sa + dy * ca;
            return Math.abs(lx) <= b.w / 2 && Math.abs(ly) <= b.h / 2;
        };
        const sel = here.find((c) => S.selected.includes(c.id) && inside(c));
        return sel || here.find(inside) || null;
    }

    function editPoint(e) {
        const r = UI.canvas.getBoundingClientRect();
        return [(e.clientX - r.left) / r.width * S.doc.settings.width, (e.clientY - r.top) / r.height * S.doc.settings.height];
    }

    function canvasPointer(e) {
        if (!S.doc || e.button !== 0) return;
        if (S.playing) FS.player.pause();
        const [px, py] = editPoint(e);
        if (FS.player.picking) {
            // 🎯 the colour under the click, drawn as it is before any key takes it away
            const done = FS.player.picking;
            FS.player.picking = null;
            UI.canvas.style.cursor = '';
            FS.player.noKey = true;
            FS.player.draw();
            const k = FS.player.scale;
            const d = FS.player.ctx.getImageData(Math.floor(px * k), Math.floor(py * k), 1, 1).data;
            FS.player.noKey = false;
            FS.player.draw();
            done('#' + [d[0], d[1], d[2]].map((v) => v.toString(16).padStart(2, '0')).join(''));
            return;
        }
        const c = clipAt(px, py);
        if (!c) { FS.select([]); return; }
        if (!S.selected.includes(c.id)) FS.select([c.id]);
        const b = FS.player.box(c);
        const corner = Math.hypot(Math.abs(px - b.cx) - b.w / 2, Math.abs(py - b.cy) - b.h / 2) < 28 / FS.player.scale * (window.devicePixelRatio || 1);
        const start = {x: FS.at(c, 'x'), y: FS.at(c, 'y'), scale: FS.at(c, 'scale'), d: Math.hypot(px - b.cx, py - b.cy) || 1};
        const {width: W, height: H} = S.doc.settings;
        let begun = false;
        UI.canvas.setPointerCapture(e.pointerId);
        const move = (ev) => {
            const [qx, qy] = editPoint(ev);
            if (!begun) { FS.beginEdit(); begun = true; }
            const clip = FS.clip(c.id);
            if (corner) FS.setAt(clip, 'scale', Math.max(0.02, start.scale * Math.hypot(qx - b.cx, qy - b.cy) / start.d));
            else {
                let nx = start.x + (qx - px) / W, ny = start.y + (qy - py) / H;
                // the centre lines pull a little
                if (Math.abs(nx) < 0.012) nx = 0;
                if (Math.abs(ny) < 0.012) ny = 0;
                FS.setAt(clip, 'x', nx);
                FS.setAt(clip, 'y', ny);
            }
            FS.liveEdit();
        };
        const up = () => {
            UI.canvas.removeEventListener('pointermove', move);
            UI.canvas.removeEventListener('pointerup', up);
            if (begun) FS.commitEdit(corner ? 'scale' : 'move in the picture');
        };
        UI.canvas.addEventListener('pointermove', move);
        UI.canvas.addEventListener('pointerup', up);
    }

    let wheelTimer = null;
    function canvasWheel(e) {
        const c = FS.clip(S.selected[0]);
        if (!c || c.kind === 'audio') return;
        e.preventDefault();
        if (!wheelTimer) FS.beginEdit();
        const clip = FS.clip(c.id);
        if (e.altKey) FS.setAt(clip, 'rotation', Math.round((FS.at(clip, 'rotation') + (e.deltaY < 0 ? 1 : -1)) * 10) / 10);
        else FS.setAt(clip, 'scale', Math.max(0.02, FS.at(clip, 'scale') * (e.deltaY < 0 ? 1.04 : 1 / 1.04)));
        FS.liveEdit();
        clearTimeout(wheelTimer);
        wheelTimer = setTimeout(() => { wheelTimer = null; FS.commitEdit(e.altKey ? 'rotate' : 'scale'); }, 400);
    }

    /* ---- the library ---- */

    const lib = {scope: 'project', kind: '', text: '', items: [], done: false, epoch: 0};

    function mediaPanel() {
        const kinds = h('div', {class: 'fx-chips'});
        for (const [kind, label] of [['', 'All'], ['video', 'Videos'], ['image', 'Pictures'], ['audio', 'Sound']]) {
            kinds.append(h('button', {class: 'fx-chip' + (kind === lib.kind ? ' on' : ''), 'data-kind': kind, onclick: () => {
                lib.kind = kind;
                kinds.querySelectorAll('.fx-chip').forEach((b) => b.classList.toggle('on', b.dataset.kind === kind));
                refreshLibrary();
            }}, T(label)));
        }
        const search = h('input', {class: 'fx-input', type: 'search', placeholder: T('Search by name or folder'), id: 'fx-lib-search'});
        let timer;
        search.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(() => { lib.text = search.value.trim(); refreshLibrary(); }, 300); });
        const upload = h('input', {type: 'file', multiple: true, hidden: true, accept: 'video/*,image/*,audio/*', onchange: FS.guard(() => uploadFiles(upload.files))});
        UI.grid = h('div', {class: 'fx-grid'});
        UI.more = h('button', {class: 'fx-btn', onclick: FS.guard(() => loadMore())}, T('More'));
        UI.libState = h('div', {class: 'fx-dim'});
        UI.libPreview = h('div', {class: 'fx-libpreview', hidden: true});
        UI.libScope = h('button', {class: 'fx-btn', id: 'fx-library-scope', onclick: () => {
            lib.scope = lib.scope === 'project' ? 'all' : 'project';
            UI.libScope.textContent = T(lib.scope === 'project' ? 'Show all application media' : 'Back to project media');
            UI.libPreview.hidden = true; UI.libPreview.innerHTML = ''; refreshLibrary();
        }}, T(lib.scope === 'project' ? 'Show all application media' : 'Back to project media'));
        const panel = h('div', {class: 'fx-panel-body'},
            h('div', {class: 'fx-row'}, search,
                h('button', {class: 'fx-btn', title: T('Bring files from this computer'), onclick: () => upload.click()}, '⤒'),
                h('button', {class: 'fx-btn', title: T('Refresh'), onclick: FS.guard(async () => { if(lib.scope === 'all') await FS.api('library/scan', {}); await refreshLibrary(); })}, '⟳')),
            UI.libScope, kinds, upload, UI.libPreview, UI.libState, h('div', {class: 'fx-scroll'}, UI.grid, h('div', {class: 'fx-center'}, UI.more)));
        setTimeout(refreshLibrary, 50);
        return panel;
    }

    async function loadMore(reset) {
        if (reset) { lib.epoch++; lib.items = []; lib.done = false; drawGrid(); }
        const epoch = lib.epoch, project = S.project, scope = lib.scope;
        if(scope === 'project' && !project){lib.done=true;drawGrid();UI.libState.textContent=T('Project media');return;}
        const last = lib.items[lib.items.length - 1];
        const params = new URLSearchParams({kind: lib.kind, text: lib.text, limit: 90});
        if(scope === 'project'){params.set('name',project);params.set('offset',lib.items.length);}
        else if (last && !reset) params.set('before', last.mtime);
        const r = await FS.api((scope === 'project' ? 'project/bin?' : 'library?') + params);
        if(epoch !== lib.epoch || project !== S.project || scope !== lib.scope)return;
        lib.items = lib.items.concat(r.items);
        lib.done = scope === 'project' ? lib.items.length >= r.total : r.items.length < 90;
        UI.libState.textContent = r.state && r.state.scanning ? T('Indexing your folders… {0} so far', r.state.scanned) : (r.state && r.state.files ? T('{0} files', r.state.files) : '');
        if(scope === 'project')UI.libState.textContent=T('Project media') + ' · ' + T('{0} files',r.total);
        drawGrid();
        if (r.state && r.state.scanning && !lib.items.length) setTimeout(refreshLibrary, 2500);
    }
    const refreshLibrary = FS.guard(() => loadMore(true));
    FS.refreshLibrary = refreshLibrary;

    function drawGrid() {
        UI.grid.innerHTML = '';
        if (!lib.items.length) UI.grid.append(h('div', {class: 'fx-dim'}, T(lib.scope === 'project' ? 'Import files or create a shot to start this project' : 'Nothing here yet')));
        for (const item of lib.items) UI.grid.append(tile(item));
        UI.more.hidden = lib.done;
    }

    function tile(item) {
        const el = h('div', {class: 'fx-tile fx-' + item.kind, title: item.name + '\n' + T('Drag to the timeline, or double-click'), draggable: 'true'},
            item.kind === 'audio' ? h('div', {class: 'fx-audio-icon'}, '♪') : h('img', {src: item.thumb, loading: 'lazy', alt: '', draggable: 'false'}),
            item.kind === 'video' ? h('span', {class: 'fx-badge'}, '▶') : null,
            h('span', {class: 'fx-tile-name'}, item.name),
            h('button', {class: 'fx-tile-add', title: T('Add at the playhead'), onclick: FS.guard(async (e) => { e.stopPropagation(); await FS.addFromLibrary(item); })}, '+'));
        el.addEventListener('click', () => libPreview(item));
        el.addEventListener('dblclick', FS.guard(() => FS.addFromLibrary(item)));
        el.addEventListener('dragstart', (e) => {
            e.dataTransfer.setData('application/x-forge-studio', JSON.stringify({id: item.id, web: item.web, project: item.project, kind: item.kind, name: item.name}));
            e.dataTransfer.effectAllowed = 'copy';
        });
        return el;
    }

    const libPreview = FS.guard(async function (item) {
        const p = UI.libPreview;
        p.hidden = false;
        p.innerHTML = '';
        const m = item.kind === 'video' ? h('video', {src: item.url, controls: true, muted: true, loop: true, autoplay: true, playsinline: true})
            : item.kind === 'audio' ? h('audio', {src: item.url, controls: true}) : h('img', {src: item.url, alt: item.name});
        const made = h('div', {class: 'fx-made'});
        p.append(h('div', {class: 'fx-row'}, h('b', {class: 'fx-ellipsis', dir: 'ltr'}, item.name),
            h('button', {class: 'fx-icon', title: T('Close the preview'), onclick: () => { p.hidden = true; p.innerHTML = ''; }}, '✕')),
        m, h('div', {class: 'fx-actions'},
            h('button', {class: 'fx-primary', onclick: FS.guard(() => FS.addFromLibrary(item))}, '+ ' + T('Add at the playhead')),
            item.kind !== 'audio' ? h('button', {class: 'fx-btn', onclick: FS.guard(() => FS.addFromLibrary(item, {layer: true}))}, T('As a layer above')) : null,
            item.kind !== 'audio' && FS.layers ? h('button', {class: 'fx-btn', onclick: FS.guard(() => FS.layers.cutoutLibrary(item))}, '✂ ' + T('Cut out, as a layer')) : null),
        made);
        if(item.web)return;
        const info = await FS.api('library/info?id=' + item.id);
        if (info.made && info.made.prompt) {
            made.append(h('div', {class: 'fx-dim'}, T('Made with') + (info.made.seed != null ? ` · ${T('Seed')} ${info.made.seed}` : '')),
                h('div', {class: 'fx-prompt', dir: 'auto'}, info.made.prompt));
        }
    });

    async function uploadFiles(files) {
        if (!S.project) await FS.newProject(FS.defaultProjectName());
        try {
            for (const f of files) {
                const project=S.project;
                FS.busy(T('Bringing in {0}…', f.name));
                const r = await FS.api(`project/upload?name=${q(project)}&web=${q(f.name)}`, f, true);
                if(project!==S.project)throw Error(T('The project changed; add this file again'));
                S.media[r.web] = {path: r.web, missing: false};
                await FS.addMedia(r.web, null, {mode: 'insert'});
            }
        } finally { FS.busy(null); }
        await refreshLibrary();
    }

    /* a library file into the edit: at the playhead on the main track, as a layer, or where it was dropped */
    FS.addFromLibrary = async function (item, opts) {
        opts = opts || {};
        if (!S.project) await FS.newProject(FS.defaultProjectName());
        if(item.web && item.project !== S.project)throw Error(T('This file belongs to another project'));
        const project=S.project;
        const added = item.web ? {web:item.web} : await FS.api('project/add', {name: S.project, id: item.id});
        if(project!==S.project)throw Error(T('The project changed; add this file again'));
        if(!item.web)S.media[added.web] = {path: item.path, missing: false};
        const info = S.info[added.web] || await FS.api(`probe?name=${q(project)}&web=${q(added.web)}`);
        if(project!==S.project)throw Error(T('The project changed; add this file again'));
        S.info[added.web]=info;
        if(!item.web)refreshLibrary();
        let where = {time: opts.time != null ? opts.time : S.time};
        const lane = opts.lane;
        if (lane && lane.newTrack) {
            where.track = FS.change('track', () => FS.addTrack(info.kind === 'audio' ? 'audio' : 'video', lane.newTrack === 'top' ? FS.visualTracks().slice(-1)[0].id : null));
        } else if (lane && lane.track) where.track = lane.track.id;
        else if (opts.layer) where.track = FS.change('track', () => freeLayer(S.time, S.time + (info.duration || 5)));
        if (!S.doc.clips.length) {
            // the first file sets the edit's shape (the nearest of the usual ones)
            const r = (info.width || 9) / (info.height || 16);
            const a = ASPECTS.slice().sort((x, y) => Math.abs(x[1] / x[2] - r) - Math.abs(y[1] / y[2] - r))[0];
            FS.change('shape', (d) => { d.settings.width = a[1]; d.settings.height = a[2]; if (info.fps && info.fps >= 23 && info.fps <= 61) d.settings.fps = Math.round(info.fps); });
            FS.player.resize();
        }
        const clip = await FS.addMedia(added.web, where, {mode: where.track && !FS.track(where.track).main ? 'free' : 'insert'});
        if (!opts.time && clip && FS.track(clip.track).main) FS.seek(FS.end(clip));
        return clip;
    };

    /* the first visual track above the main one that is free between a and b (made when there is none) */
    function freeLayer(a, b) {
        for (const t of FS.visualTracks()) {
            if (t.main) continue;
            if (!S.doc.clips.some((c) => c.track === t.id && c.start < b && FS.end(c) > a)) return t.id;
        }
        return FS.addTrack('video');
    }
    FS.freeLayer = freeLayer;

    /* ---- panels and inspector ---- */

    function sidePanel() {
        const panels = [{id: 'media', icon: '🎞', label: 'Media', build: mediaPanel}].concat(FS.panels);
        UI.tabs = h('div', {class: 'fx-tabs'});
        const bodies = {};
        const show = (id) => {
            for (const [key, el] of Object.entries(bodies)) el.hidden = key !== id;
            UI.tabs.querySelectorAll('.fx-tab').forEach((b) => b.classList.toggle('on', b.dataset.id === id));
            try { localStorage.setItem('forgeStudio.panel2', id); } catch (e) { /* storage off */ }
        };
        for (const p of panels) {
            UI.tabs.append(h('button', {class: 'fx-tab', 'data-id': p.id, onclick: () => show(p.id)}, h('span', {class: 'fx-tab-icon'}, p.icon || '•'), h('span', {}, T(p.label))));
            bodies[p.id] = p.build();
        }
        FS.showPanel = show;
        let first = 'media';
        try { first = localStorage.getItem('forgeStudio.panel2') || first; } catch (e) { /* storage off */ }
        show(bodies[first] ? first : 'media');
        return h('div', {class: 'fx-side'}, UI.tabs, ...Object.values(bodies));
    }

    /* ---- the keyboard ---- */

    function keys(e) {
        if (!UI.root || UI.root.offsetParent === null && !UI.root.classList.contains('fx-full')) return;
        // A dialog owns its shortcuts. The window capture listener must let those events reach it.
        if (UI.root.querySelector('.fx-modal-back, .fx-ask')) return;
        const tag = (e.target.tagName || '').toLowerCase();
        if (['input', 'textarea', 'select'].includes(tag) || e.target.isContentEditable) return;
        if (!S.doc) return;
        if (FS.imageShop && FS.imageShop.active && FS.imageShop.keys(e)) return;   // the image workspace's own keys
        const k = e.key, ctrl = e.ctrlKey || e.metaKey;
        const take = () => { e.preventDefault(); e.stopPropagation(); };
        if (k === ' ') { take(); FS.player.toggle(); }
        else if (ctrl && (k === 'z' || k === 'Z') && !e.shiftKey) { take(); FS.undo(); }
        else if (ctrl && (k === 'y' || ((k === 'z' || k === 'Z') && e.shiftKey))) { take(); FS.redo(); }
        else if (ctrl && (k === 'd' || k === 'D')) { take(); FS.timeline.duplicate(); }
        else if (ctrl && (k === 's' || k === 'S')) { take(); FS.save(); }
        // by the key's place, so they work on a Hebrew keyboard too: Ctrl+Alt+C / V copy and paste a clip's style, M a marker
        else if (ctrl && e.altKey && e.code === 'KeyC') { if (S.selected.length) { take(); FS.copyStyle(S.selected[0]); FS.message(T('Style copied')); } }
        else if (ctrl && e.altKey && e.code === 'KeyV') { if (S.selected.length && FS.styleClipboard) { take(); FS.message(T('Style pasted on {0} clips', FS.pasteStyle(S.selected))); } }
        else if (!ctrl && !e.altKey && e.code === 'KeyM') { take(); FS.toggleMarker(S.time); }
        else if (!ctrl && !e.altKey && (e.code === 'KeyI' || e.code === 'KeyO')) { take(); FS.markRange(e.code === 'KeyI' ? 'in' : 'out', S.time); }
        else if (!ctrl && !e.altKey && e.code === 'KeyF') { take(); previewFull(); }
        else if (!ctrl && !e.altKey && e.code === 'KeyP') { take(); setWide(!UI.root.classList.contains('fx-wide')); }
        else if ((!ctrl && (k === 's' || k === 'S')) || (ctrl && (k === 'b' || k === 'B'))) { take(); FS.timeline.split(); }
        else if (k === 'Delete' || k === 'Backspace') { if (S.selected.length) { take(); FS.remove(S.selected); } }
        // trimming from the keyboard, by the key's place too: Q / W trim to the playhead, , / . (or Alt+arrows) nudge
        else if (!ctrl && !e.altKey && (e.code === 'KeyQ' || e.code === 'KeyW')) {
            take();
            const kept = FS.trimTo(e.code === 'KeyQ' ? 'start' : 'end', FS.snapTime(S.time), S.selected);
            if (kept.length) {
                if (e.code === 'KeyQ') FS.seek(FS.clip(kept[0]).start);
                FS.emit('select', S.selected);
            } else FS.message(T('Put the playhead inside a clip to trim it there'));
        }
        else if (!ctrl && (e.code === 'Comma' || e.code === 'Period' || [',', '.', '<', '>'].includes(k) || (e.altKey && (k === 'ArrowLeft' || k === 'ArrowRight')))) {
            if (S.selected.length) {
                take();
                const dir = e.code === 'Period' || k === '.' || k === '>' || k === 'ArrowRight' ? 1 : -1;
                if (!FS.nudge(S.selected, dir * (e.shiftKey ? 1 : 1 / FS.fps()))) FS.message(T('Nothing moved: on the main track clips sit end to end, and a clip does not move over another'));
            }
        }
        else if (!ctrl && !e.altKey && (k === 'ArrowUp' || k === 'ArrowDown')) {
            const t = FS.cutNear(S.time, k === 'ArrowDown' ? 1 : -1);
            take();
            FS.seek(t != null ? t : k === 'ArrowDown' ? FS.total() : 0);
        }
        else if (k === 'ArrowLeft') { take(); FS.seek(FS.snapTime(S.time - (e.shiftKey ? 1 : 1 / FS.fps()))); }
        else if (k === 'ArrowRight') { take(); FS.seek(FS.snapTime(S.time + (e.shiftKey ? 1 : 1 / FS.fps()))); }
        else if (k === 'Home') { take(); FS.seek(0); }
        else if (k === 'End') { take(); FS.seek(FS.total()); }
        else if (k === 'Escape') {
            take();
            if (FS.timeline.cancelPointer) { FS.timeline.cancelPointer(); return; }
            FS.clearRegion(); FS.timeline.closeMenu();
            if (UI.root.classList.contains('fx-full')) setFull(false); else FS.select([]);
        }
    }

    /* ---- building and starting ---- */

    function fitHeight() {
        if (!UI.root) return;
        if (UI.root.classList.contains('fx-full')) { UI.root.style.height = ''; return; }
        const top = UI.root.getBoundingClientRect().top + window.scrollY;
        UI.root.style.height = Math.max(560, window.innerHeight - Math.max(0, top - window.scrollY) - 12) + 'px';
    }

    function build() {
        const host = document.getElementById('fstudio_host');
        if (!host) return false;
        const mount = host.querySelector('.fstudio-mount') || host;
        mount.innerHTML = '';
        UI.toast = h('div', {class: 'fx-toast'});
        UI.inspector = h('div', {class: 'fx-inspector'});
        UI.root = h('div', {class: 'fx-app hebrew-ui-skip', dir: FS.hebrew() ? 'rtl' : 'ltr'},
            topBar(),
            h('div', {class: 'fx-upper'}, sidePanel(), stage(), UI.inspector),
            UI.splitter = h('div', {class: 'fx-splitter', title: T('Drag to make the timeline taller or shorter')}),
            h('div', {class: 'fx-lower'}, FS.timeline.build()),
            UI.toast);
        mount.append(UI.root);
        FS.timeline.acceptDrops();
        FS.player.attach(UI.canvas);
        splitter();
        let lowerH = 300;
        try { lowerH = +localStorage.getItem('forgeStudio.timelineH') || 300; } catch (e) { /* storage off */ }
        UI.root.style.setProperty('--fx-lower', lowerH + 'px');
        window.addEventListener('resize', () => { fitHeight(); FS.player.resize(); });
        window.addEventListener('keydown', keys, true);
        return true;
    }

    function splitter() {
        UI.splitter.addEventListener('pointerdown', (e) => {
            const y0 = e.clientY;
            const h0 = parseFloat(getComputedStyle(UI.root).getPropertyValue('--fx-lower')) || 300;
            const move = (ev) => {
                const hgt = Math.max(140, Math.min(UI.root.clientHeight - 260, h0 - (ev.clientY - y0)));
                UI.root.style.setProperty('--fx-lower', hgt + 'px');
                FS.player.resize();
            };
            const up = () => {
                window.removeEventListener('pointermove', move);
                window.removeEventListener('pointerup', up);
                try { localStorage.setItem('forgeStudio.timelineH', parseFloat(getComputedStyle(UI.root).getPropertyValue('--fx-lower'))); } catch (err) { /* storage off */ }
                FS.timeline.render();
            };
            window.addEventListener('pointermove', move);
            window.addEventListener('pointerup', up);
        });
    }

    let readyResolve;
    FS.ready = new Promise((r) => { readyResolve = r; });

    async function start() {
        UI.options = await FS.api('options');
        fitHeight();
        let last = null, full = false;
        try { last = localStorage.getItem('forgeStudio.project'); full = !!localStorage.getItem('forgeStudio.full'); } catch (e) { /* storage off */ }
        if (full) setFull(true);
        try { if (localStorage.getItem('forgeStudio.wide')) setWide(true); } catch (e) { /* storage off */ }
        const names = (await FS.api('projects')).projects.map((p) => p.name);
        if (last && names.includes(last)) await FS.openProject(last);
        else if (names.length) await FS.openProject(names[0]);
        else await FS.newProject(FS.defaultProjectName());
        readyResolve();
    }

    FS.on('project', (p) => {
        lib.scope='project';lib.kind='';lib.text='';
        const search=document.getElementById('fx-lib-search');if(search)search.value='';
        UI.grid?.closest('.fx-panel-body')?.querySelectorAll('.fx-chip').forEach(b=>b.classList.toggle('on',b.dataset.kind===''));
        if(UI.libScope)UI.libScope.textContent=T('Show all application media');
        if(UI.libPreview){UI.libPreview.hidden=true;UI.libPreview.innerHTML='';}
        if(UI.grid)refreshLibrary();
        UI.projectBtn.querySelector('.fx-pname').textContent = p.name;
        const a = ASPECTS.find((x) => x[1] === S.doc.settings.width && x[2] === S.doc.settings.height);
        UI.aspect.value = a ? a[0] : 'custom';
        if (p.migrated) FS.message(T('This project was made with the first version: its clips were brought over (titles and captions of that version were not)'));
        if (p.missing && p.missing.length) FS.message(T('{0} files of this project are missing', p.missing.length), 'warn');
    });
    FS.on('doc', () => {
        if (!S.doc) return;
        const a = ASPECTS.find((x) => x[1] === S.doc.settings.width && x[2] === S.doc.settings.height);
        UI.aspect.value = a ? a[0] : 'custom';
        UI.saved.textContent = S.dirty ? '●' : '';
        const stamp=S.project+'|'+Object.keys(S.media).join('|');
        if(stamp!==lib.mediaStamp){lib.mediaStamp=stamp;clearTimeout(lib.refreshTimer);lib.refreshTimer=setTimeout(()=>{if(UI.grid)refreshLibrary();},250);}
    });
    FS.on('select', () => {
        const picked = S.selected.map((id) => FS.clip(id)).filter((c) => c && (c.kind === 'video' || c.kind === 'image'));
        if (picked.length === 2 && S.selected.length === 2) FS.message(T('Two shots picked: right-click one of them to join them'));
    });
    FS.on('saved', (at) => {
        const d = new Date(at), p = (n) => String(n).padStart(2, '0');
        UI.saved.textContent = T('Saved {0}', `${p(d.getHours())}:${p(d.getMinutes())}`);
    });
    FS.on('time', () => { UI.time.textContent = `${FS.timecode(S.time)} / ${FS.timecode(FS.total())}`; });
    FS.on('drawn', () => { UI.time.textContent = `${FS.timecode(S.time)} / ${FS.timecode(FS.total())}`; });
    FS.on('playing', (on) => { UI.playBtn.textContent = on ? '❚❚' : '▶'; });

    /* ---- 🎞️ in the other tabs ---- */

    function openTab(tabsId, id) {
        const tabs = (window.gradio_config && window.gradio_config.components || []).find(
            (c) => c.type === 'tabs' && c.props && c.props.elem_id === tabsId);
        if (!tabs || !tabs.instance) return false;
        tabs.props.selected = id;
        tabs.instance.$set({selected: id});
        return true;
    }

    FS.send = FS.guard(async function (urls) {
        urls = urls.filter(Boolean);
        if (!urls.length) return FS.message(T('Nothing to send: make or pick a result first'), 'warn');
        const r = await FS.api('inbox', {paths: urls});
        openTab('tabs', 'forge_studio');
        await FS.ready;
        for (const item of r.added) await FS.addFromLibrary(item);
        FS.message(T('{0} added to the timeline', r.added.length));
        refreshLibrary();
    });

    function galleryUrls(id) {
        const gallery = gradioApp().getElementById(id);
        if (!gallery) return [];
        const big = gallery.querySelector('.preview img, img.preview');
        if (big) return [big.src];
        const selected = gallery.querySelector('.thumbnail-item.selected img');
        if (selected) return [selected.src];
        return Array.from(gallery.querySelectorAll('.thumbnail-item img')).map((i) => i.src);
    }

    function addSendButtons() {
        for (const tab of ['txt2img', 'img2img', 'extras']) {
            const row = gradioApp().getElementById('image_buttons_' + tab);
            if (!row || row.querySelector('.fs-send')) continue;
            row.append(h('button', {class: 'lg secondary gradio-button tool fs-send hebrew-ui-skip', title: T('Send to Forge Studio'),
                onclick: (e) => { e.preventDefault(); FS.send(galleryUrls(tab + '_gallery')); }}, '🎞️'));
        }
        const video = gradioApp().getElementById('vs_video');
        if (video && !video.parentElement.querySelector('.fs-send-video')) {
            video.insertAdjacentElement('afterend', h('button', {class: 'lg secondary gradio-button fs-send fs-send-video hebrew-ui-skip', onclick: (e) => {
                e.preventDefault();
                const v = video.querySelector('video');
                FS.send([v && (v.currentSrc || v.src)]);
            }}, '🎞️ ' + T('Send to Forge Studio')));
        }
    }

    function boot() {
        if (!build()) return setTimeout(boot, 500);
        addSendButtons();
        setTimeout(addSendButtons, 3000);
        // starts the first time the tab is seen (hidden, it has no size to lay itself out in)
        const host = document.getElementById('fstudio_host');
        const watch = setInterval(() => {
            if (host.offsetParent === null) return;
            clearInterval(watch);
            FS.guard(start)();
        }, 300);
        // shown again after another tab: lay out for the space it has now
        new IntersectionObserver((entries) => {
            if (entries.some((en) => en.isIntersecting)) { fitHeight(); FS.player.resize(); FS.timeline.render(); }
        }).observe(UI.root);
    }
    if (typeof onUiLoaded === 'function') onUiLoaded(boot);
})();

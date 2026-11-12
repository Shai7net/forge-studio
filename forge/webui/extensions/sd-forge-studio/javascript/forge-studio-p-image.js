/* Forge Studio - the image workspace: a picture shot made or edited like in a photo editor, inside the editor.
 *
 * An image shot is an image clip with `imageShot` = the id of a layered document on the server (imagedoc.py); the
 * clip shows the flattened picture. Opening one covers the player with the canvas, puts a column of tools at its
 * side and the layers in place of the inspector; the timeline stays. Layers are canvases the size of the shot (moved
 * by an offset), drawn bottom to top with their opacity and blend mode, the same way when flattened for saving.
 * The selection is a mask the size of the shot (white = selected), shown with marching ants; painting, filling,
 * deleting and generating keep to it.
 */
(function () {
    'use strict';
    const FS = window.ForgeStudio = window.ForgeStudio || {};
    const S = FS.state;
    const T = (...a) => FS.T(...a);
    const q = encodeURIComponent;
    const IM = FS.imageShop = {active: false};
    let D = null;           // the open document
    const UI = {};

    const BLENDS = ['normal', 'multiply', 'screen', 'overlay', 'darken', 'lighten', 'color-dodge', 'color-burn', 'hard-light',
        'soft-light', 'difference', 'exclusion', 'hue', 'saturation', 'color', 'luminosity'];
    const op = (blend) => (blend && blend !== 'normal' ? blend : 'source-over');
    const TOOLS = [
        ['move', '✥', 'Move', 'V'], ['rect', '⬚', 'Rectangle selection', 'M'], ['ellipse', '◯', 'Ellipse selection', ''],
        ['lasso', '➰', 'Lasso', 'L'], ['wand', '🪄', 'Magic wand', 'W'], ['object', '🎯', 'Select an object (AI)', 'A'],
        ['brush', '🖌', 'Brush', 'B'], ['eraser', '⌫', 'Eraser', 'E'],
        ['bucket', '🪣', 'Fill', 'G'], ['picker', '💧', 'Eyedropper', 'I'], ['text', 'T', 'Text', 'T'], ['crop', '⛶', 'Crop', 'C'],
        ['hand', '✋', 'Hand', 'H'], ['zoom', '🔍', 'Zoom', 'Z']];

    const canvas = (w, h) => { const c = document.createElement('canvas'); c.width = w; c.height = h; return c; };
    const ctx = (c) => c.getContext('2d', {willReadFrequently: true});
    const fileUrl = (file) => `/forge-studio/image/file?name=${q(S.project)}&shot=${q(D.shot)}&file=${q(file)}`;
    const loadImage = (src) => new Promise((ok, bad) => { const i = new Image(); i.onload = () => ok(i); i.onerror = () => bad(new Error(T('Could not read the picture'))); i.src = src; });
    const newId = () => 'l' + Math.random().toString(36).slice(2, 10);
    const hex = (r, g, b) => '#' + [r, g, b].map((v) => v.toString(16).padStart(2, '0')).join('');
    const rgb = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));

    /* ---- the document ---- */

    async function openDoc(shot, clipId) {
        const doc = await FS.api(`image/doc?name=${q(S.project)}&shot=${q(shot)}`);
        D = {shot, clipId, width: doc.width, height: doc.height, title: doc.title || '', layers: [], active: null, sel: null,
            tool: (D && D.tool) || 'brush', fg: (D && D.fg) || '#ffffff', bg: (D && D.bg) || '#000000',
            brush: (D && D.brush) || {size: 40, hardness: 0.8, opacity: 1}, tolerance: 32, contiguous: true, feather: 0,
            font: {size: Math.round(doc.height / 12), family: 'Segoe UI', weight: 700},
            history: [], future: [], dirty: false, view: {scale: 1, ox: 0, oy: 0}, store: {}};
        for (const l of doc.layers) {
            const c = canvas(D.width, D.height);
            ctx(c).drawImage(await loadImage(fileUrl(l.file)), 0, 0);
            D.layers.push({id: l.id, name: l.name, visible: l.visible !== false, opacity: l.opacity == null ? 1 : l.opacity,
                blend: l.blend || 'normal', x: l.x || 0, y: l.y || 0, canvas: c, dirty: false, text: l.text || null});
            D.store[l.id] = c;
        }
        D.active = D.layers[D.layers.length - 1].id;
    }

    const layer = (id) => D.layers.find((l) => l.id === (id || D.active));
    const activeLayer = () => layer(D.active);

    /* the layers drawn together (as the export of the shot will be) onto `c` at `scale`, from `x0, y0` of the shot */
    function composite(c, scale, x0, y0, skip) {
        for (const l of D.layers) {
            if (!l.visible || l === skip) continue;
            c.save();
            c.globalAlpha = l.opacity;
            c.globalCompositeOperation = op(l.blend);
            c.drawImage(l.canvas, (l.x - x0) * scale, (l.y - y0) * scale, D.width * scale, D.height * scale);
            c.restore();
        }
    }
    IM.flatten = function () {
        const c = canvas(D.width, D.height);
        composite(ctx(c), 1, 0, 0);
        return c;
    };

    /* ---- history: each step keeps the layers' list and the pixels of the layers it touched ---- */

    function remember(label, touched) {
        const step = {label, meta: D.layers.map(({canvas: _c, ...m}) => Object.assign({}, m)), active: D.active,
            width: D.width, height: D.height, pixels: {}, sel: D.sel ? copyOf(D.sel) : null};
        for (const l of (touched === 'all' ? D.layers : (touched || []).map((id) => layer(id)).filter(Boolean))) step.pixels[l.id] = copyOf(l.canvas);
        step.all = touched === 'all';
        D.history.push(step);
        if (D.history.length > 30) D.history.shift();
        D.future = [];
        D.dirty = true;
    }
    function copyOf(c) { const n = canvas(c.width, c.height); ctx(n).drawImage(c, 0, 0); return n; }
    function snapshot(touchedIds) {
        return {meta: D.layers.map(({canvas: _c, ...m}) => Object.assign({}, m)), active: D.active, width: D.width, height: D.height,
            pixels: Object.fromEntries(touchedIds.map((id) => [id, copyOf(layer(id).canvas)])), sel: D.sel ? copyOf(D.sel) : null};
    }
    /* every layer's canvas stays in D.store by id, so a layer taken away (or undone) can always come back */
    function restore(step) {
        D.width = step.width;
        D.height = step.height;
        D.layers = step.meta.map((m) => {
            const c = step.pixels[m.id] || D.store[m.id];
            D.store[m.id] = c;
            return Object.assign({}, m, {canvas: c, dirty: true});
        });
        D.active = step.active;
        D.sel = step.sel;
        selectionChanged();
    }
    IM.undo = function () {
        const step = D.history.pop();
        if (!step) return;
        const ids = Object.keys(step.pixels);
        D.future.push(Object.assign(snapshot(step.all ? D.layers.map((l) => l.id) : ids.filter((id) => layer(id))), {label: step.label, all: step.all}));
        restore(step);
        D.dirty = true;
        refresh();
    };
    IM.redo = function () {
        const step = D.future.pop();
        if (!step) return;
        const ids = Object.keys(step.pixels);
        D.history.push(Object.assign(snapshot(step.all ? D.layers.map((l) => l.id) : ids.filter((id) => layer(id))), {label: step.label, all: step.all}));
        restore(step);
        D.dirty = true;
        refresh();
    };

    /* ---- the selection ---- */

    function selectionChanged() {
        D.ants = null;
        if (D.sel && selectionEmpty()) D.sel = null;
        if (D.sel) D.ants = antsPath(D.sel);
    }
    function selectionEmpty() {
        const d = ctx(D.sel).getImageData(0, 0, D.width, D.height).data;
        for (let i = 3; i < d.length; i += 4) if (d[i] > 0) return false;
        return true;
    }
    /* the outline of the mask (alpha >= 128) as runs of horizontal and vertical pixel edges, for marching ants */
    function antsPath(mask) {
        const W = mask.width, H = mask.height, a = ctx(mask).getImageData(0, 0, W, H).data;
        const on = (x, y) => x >= 0 && y >= 0 && x < W && y < H && a[(y * W + x) * 4 + 3] >= 128;
        const p = new Path2D();
        for (let y = 0; y <= H; y++) {
            let run = -1;
            for (let x = 0; x <= W; x++) {
                const edge = x < W && on(x, y) !== on(x, y - 1);
                if (edge && run < 0) run = x;
                if (!edge && run >= 0) { p.moveTo(run, y); p.lineTo(x, y); run = -1; }
            }
        }
        for (let x = 0; x <= W; x++) {
            let run = -1;
            for (let y = 0; y <= H; y++) {
                const edge = y < H && on(x, y) !== on(x - 1, y);
                if (edge && run < 0) run = y;
                if (!edge && run >= 0) { p.moveTo(x, run); p.lineTo(x, y); run = -1; }
            }
        }
        return p;
    }
    /* a new selection shape `draw(c)` (white on a clear mask) combined with the current one: replace, add (Shift) or
     * take away (Alt); feathered by D.feather */
    function select(draw, mode) {
        remember(T('Selection'), []);
        const m = canvas(D.width, D.height), c = ctx(m);
        c.fillStyle = '#fff';
        c.strokeStyle = '#fff';
        draw(c);
        let shape = m;
        if (D.feather > 0) {
            shape = canvas(D.width, D.height);
            const f = ctx(shape);
            f.filter = `blur(${D.feather}px)`;
            f.drawImage(m, 0, 0);
        }
        if (!D.sel || mode === 'replace') D.sel = shape;
        else {
            const c2 = ctx(D.sel);
            c2.globalCompositeOperation = mode === 'add' ? 'source-over' : 'destination-out';
            c2.drawImage(shape, 0, 0);
            c2.globalCompositeOperation = 'source-over';
        }
        selectionChanged();
        refresh();
    }
    IM.selectAll = () => select((c) => c.fillRect(0, 0, D.width, D.height), 'replace');
    IM.deselect = () => { if (D.sel) { remember(T('Deselect'), []); D.sel = null; selectionChanged(); refresh(); } };
    IM.invert = function () {
        remember(T('Invert the selection'), []);
        const m = canvas(D.width, D.height), c = ctx(m);
        c.fillStyle = '#fff';
        c.fillRect(0, 0, D.width, D.height);
        if (D.sel) { c.globalCompositeOperation = 'destination-out'; c.drawImage(D.sel, 0, 0); }
        D.sel = m;
        selectionChanged();
        refresh();
    };
    /* the selection as a black and white PNG (white = selected), or the whole shot when nothing is selected */
    IM.maskBlob = function () {
        const m = canvas(D.width, D.height), c = ctx(m);
        c.fillStyle = '#000';
        c.fillRect(0, 0, D.width, D.height);
        if (D.sel) {
            const w = canvas(D.width, D.height), wc = ctx(w);
            wc.fillStyle = '#fff';
            wc.fillRect(0, 0, D.width, D.height);
            wc.globalCompositeOperation = 'destination-in';
            wc.drawImage(D.sel, 0, 0);
            c.drawImage(w, 0, 0);
        } else { c.fillStyle = '#fff'; c.fillRect(0, 0, D.width, D.height); }
        return new Promise((r) => m.toBlob(r, 'image/png'));
    };
    IM.setSelection = function (maskCanvas, mode) {   // used by the AI tools (a subject, an object)
        select((c) => c.drawImage(maskCanvas, 0, 0, D.width, D.height), mode || 'replace');
    };

    /* the region of the active layer's pixels (in its own coordinates) like the one at x, y: magic wand and fill */
    function region(l, x, y, tolerance, contiguous) {
        const W = D.width, H = D.height;
        const lx = Math.floor(x - l.x), ly = Math.floor(y - l.y);
        const out = canvas(W, H);
        if (lx < 0 || ly < 0 || lx >= W || ly >= H) return out;
        const src = ctx(l.canvas).getImageData(0, 0, W, H).data;
        const i0 = (ly * W + lx) * 4, ref = [src[i0], src[i0 + 1], src[i0 + 2], src[i0 + 3]];
        const like = (i) => Math.max(Math.abs(src[i] - ref[0]), Math.abs(src[i + 1] - ref[1]), Math.abs(src[i + 2] - ref[2]), Math.abs(src[i + 3] - ref[3])) <= tolerance;
        const img = ctx(out).createImageData(W, H), o = img.data;
        if (contiguous) {
            const seen = new Uint8Array(W * H), stack = [ly * W + lx];
            seen[ly * W + lx] = 1;
            while (stack.length) {
                const p = stack.pop();
                if (!like(p * 4)) continue;
                o[p * 4] = o[p * 4 + 1] = o[p * 4 + 2] = o[p * 4 + 3] = 255;
                const px = p % W, py = (p - px) / W;
                if (px > 0 && !seen[p - 1]) { seen[p - 1] = 1; stack.push(p - 1); }
                if (px < W - 1 && !seen[p + 1]) { seen[p + 1] = 1; stack.push(p + 1); }
                if (py > 0 && !seen[p - W]) { seen[p - W] = 1; stack.push(p - W); }
                if (py < H - 1 && !seen[p + W]) { seen[p + W] = 1; stack.push(p + W); }
            }
        } else {
            for (let p = 0; p < W * H; p++) if (like(p * 4)) o[p * 4] = o[p * 4 + 1] = o[p * 4 + 2] = o[p * 4 + 3] = 255;
        }
        ctx(out).putImageData(img, 0, 0);
        // into the shot's coordinates
        const placed = canvas(W, H);
        ctx(placed).drawImage(out, l.x, l.y);
        return placed;
    }

    /* ---- changing pixels (always within the selection when there is one) ---- */

    /* `paint(c)` draws in the shot's coordinates onto a clear canvas; the result goes onto the active layer through
     * the selection, `erase` takes it away instead */
    function applyToLayer(l, painted, opacity, erase) {
        let piece = painted;
        if (D.sel) {
            piece = copyOf(painted);
            const pc = ctx(piece);
            pc.globalCompositeOperation = 'destination-in';
            pc.drawImage(D.sel, 0, 0);
        }
        const c = ctx(l.canvas);
        c.save();
        c.globalAlpha = opacity;
        c.globalCompositeOperation = erase ? 'destination-out' : 'source-over';
        c.drawImage(piece, -l.x, -l.y);
        c.restore();
        l.dirty = true;
    }
    IM.clearSelected = function () {
        const l = activeLayer();
        if (!l) return;
        remember(T('Delete'), [l.id]);
        const all = canvas(D.width, D.height), c = ctx(all);
        c.fillStyle = '#000';
        c.fillRect(0, 0, D.width, D.height);
        applyToLayer(l, all, 1, true);
        refresh();
    };
    IM.fillSelected = function (colour) {
        const l = activeLayer();
        if (!l) return;
        remember(T('Fill'), [l.id]);
        const all = canvas(D.width, D.height), c = ctx(all);
        c.fillStyle = colour || D.fg;
        c.fillRect(0, 0, D.width, D.height);
        applyToLayer(l, all, 1, false);
        refresh();
    };

    /* ---- layers ---- */

    function addLayer(name, c, extra, below) {
        const l = Object.assign({id: newId(), name, visible: true, opacity: 1, blend: 'normal', x: 0, y: 0, canvas: c || canvas(D.width, D.height), dirty: true, text: null}, extra || {});
        const at = D.layers.findIndex((x) => x.id === D.active);
        D.layers.splice(below ? Math.max(0, at) : at + 1, 0, l);
        D.active = l.id;
        D.store[l.id] = l.canvas;
        return l;
    }
    IM.newLayer = function () { remember(T('New layer'), []); addLayer(T('Layer {0}', D.layers.length + 1)); refresh(); };
    IM.duplicate = function () {
        const l = activeLayer();
        remember(T('Duplicate layer'), []);
        addLayer(l.name + ' ' + T('copy'), copyOf(l.canvas), {opacity: l.opacity, blend: l.blend, x: l.x, y: l.y, text: l.text ? Object.assign({}, l.text) : null});
        refresh();
    };
    IM.removeLayer = function () {
        if (D.layers.length < 2) throw new Error(T('A shot keeps at least one layer'));
        remember(T('Delete layer'), []);
        const at = D.layers.findIndex((x) => x.id === D.active);
        D.layers.splice(at, 1);
        D.active = D.layers[Math.max(0, at - 1)].id;
        refresh();
    };
    IM.moveLayer = function (dir) {
        const at = D.layers.findIndex((x) => x.id === D.active), to = at + dir;
        if (to < 0 || to >= D.layers.length) return;
        remember(T('Layer order'), []);
        const [l] = D.layers.splice(at, 1);
        D.layers.splice(to, 0, l);
        refresh();
    };
    IM.mergeDown = function () {
        const at = D.layers.findIndex((x) => x.id === D.active);
        if (at < 1) throw new Error(T('There is no layer below to merge into'));
        const top = D.layers[at], under = D.layers[at - 1];
        remember(T('Merge down'), [under.id]);
        const c = ctx(under.canvas);
        c.save();
        c.globalAlpha = top.visible ? top.opacity : 0;
        c.globalCompositeOperation = op(top.blend);
        c.drawImage(top.canvas, top.x - under.x, top.y - under.y);
        c.restore();
        under.dirty = true;
        under.text = null;
        D.layers.splice(at, 1);
        D.active = under.id;
        refresh();
    };
    /* a layer from the selected pixels of the active layer (Ctrl+J), or a copy of it all */
    IM.layerViaCopy = function () {
        const l = activeLayer();
        remember(T('Layer from the selection'), []);
        const c = canvas(D.width, D.height), cc = ctx(c);
        cc.drawImage(l.canvas, l.x, l.y);
        if (D.sel) { cc.globalCompositeOperation = 'destination-in'; cc.drawImage(D.sel, 0, 0); }
        addLayer(l.name + ' ' + T('part'), c);
        refresh();
    };
    /* a picture (an Image, canvas or blob) as a new layer, fitted inside the shot and centred */
    IM.addPicture = async function (source, name) {
        const img = source instanceof Blob ? await loadImage(URL.createObjectURL(source)) : source;
        const w0 = img.naturalWidth || img.width, h0 = img.naturalHeight || img.height;
        const k = Math.min(1, D.width / w0, D.height / h0);
        const w = Math.round(w0 * k), h = Math.round(h0 * k);
        const c = canvas(D.width, D.height);
        ctx(c).drawImage(img, Math.round((D.width - w) / 2), Math.round((D.height - h) / 2), w, h);
        remember(T('Add a picture'), []);
        addLayer(name || T('Picture'), c);
        refresh();
    };
    /* ---- for the AI tools (q-image-ai.js) ---- */

    /* the selection as it is now (a copy), or null */
    IM.selection = () => (D && D.sel ? copyOf(D.sel) : null);
    /* layer `id`'s pixels where they show in the shot (its offset applied), as a PNG */
    IM.layerBlob = function (id) {
        const l = layer(id), c = canvas(D.width, D.height);
        ctx(c).drawImage(l.canvas, l.x, l.y);
        return new Promise((r) => c.toBlob(r, 'image/png'));
    };
    IM.layerList = () => D.layers.slice().reverse().map((l) => ({id: l.id, name: l.name}));
    /* a picture (fitted to the shot) on a new layer above the active one; with `mask` (a selection) only inside it */
    function picturePixels(img, mask) {
        const c = canvas(D.width, D.height), cc = ctx(c);
        cc.drawImage(img, 0, 0, D.width, D.height);
        if (mask) { cc.globalCompositeOperation = 'destination-in'; cc.drawImage(mask, 0, 0); }
        return c;
    }
    IM.addResult = function (img, name, mask) {
        remember(T('Generated'), []);
        const l = addLayer(name, picturePixels(img, mask));
        l.generated = {mask: mask || null};
        refresh();
        return l.id;
    };
    /* another variation into the same layer (keeping the mask it was made with) */
    IM.swapResult = function (id, img) {
        const l = layer(id);
        if (!l) return false;
        remember(T('Variation'), [l.id]);
        l.canvas = picturePixels(img, l.generated && l.generated.mask);
        D.store[l.id] = l.canvas;
        l.x = l.y = 0;
        l.dirty = true;
        refresh();
        return true;
    };
    IM.hasLayer = (id) => !!(D && layer(id));
    IM.cutLayer = function (id, mask) {
        const l = layer(id);
        remember(T('Remove the background'), []);
        const c = canvas(D.width, D.height), cc = ctx(c);
        cc.drawImage(l.canvas, l.x, l.y);
        cc.globalCompositeOperation = 'destination-in';
        cc.drawImage(mask, 0, 0);
        l.visible = false;
        addLayer(l.name + ' ' + T('cut out'), c);
        refresh();
    };
    IM.status = (t) => { if (UI.status) UI.status.textContent = t || ''; };

    /* a text layer: its words stay editable (double-click the layer) */
    function renderText(l) {
        const t = l.text, c = ctx(l.canvas);
        c.clearRect(0, 0, D.width, D.height);
        c.save();
        c.font = `${t.weight || 700} ${t.size}px "${t.family || 'Segoe UI'}", "Segoe UI", sans-serif`;
        c.fillStyle = t.color;
        c.textBaseline = 'top';
        const rtl = /[֐-׿؀-ۿ]/.test(t.words);
        c.direction = rtl ? 'rtl' : 'ltr';
        c.textAlign = rtl ? 'right' : 'left';
        String(t.words).split('\n').forEach((line, i) => c.fillText(line, t.x, t.y + i * t.size * 1.2));
        c.restore();
        l.dirty = true;
    }
    function textAt(x, y, existing) {
        const box = FS.h('textarea', {class: 'im-textin', dir: 'auto', rows: 2}, existing ? existing.text.words : '');
        const [vx, vy] = toView(existing ? existing.text.x : x, existing ? existing.text.y : y);
        Object.assign(box.style, {left: vx + 'px', top: vy + 'px', fontSize: Math.max(12, (existing ? existing.text.size : D.font.size) * D.view.scale) + 'px',
            color: existing ? existing.text.color : D.fg});
        UI.area.append(box);
        setTimeout(() => box.focus(), 0);
        const done = (keep) => {
            box.remove();
            const words = box.value.replace(/\s+$/, '');
            if (!keep || !words) return;
            if (existing) {
                remember(T('Text'), [existing.id]);
                existing.text.words = words;
                existing.name = words.split('\n')[0].slice(0, 30);
                renderText(existing);
            } else {
                remember(T('Text'), []);
                const rtl = /[֐-׿؀-ۿ]/.test(words);
                const l = addLayer(words.split('\n')[0].slice(0, 30), null, {text: {words, x: rtl ? x + 1 : x, y, size: D.font.size, color: D.fg, family: D.font.family, weight: D.font.weight}});
                if (rtl) { l.text.x = x; }
                renderText(l);
            }
            refresh();
        };
        box.addEventListener('keydown', (e) => {
            e.stopPropagation();
            if (e.key === 'Escape') done(false);
            if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) done(true);
        });
        box.addEventListener('blur', () => done(true));
    }
    IM.editText = function (id) { const l = layer(id); if (l && l.text) textAt(0, 0, l); };

    /* the shot cut to `r` = [x, y, w, h] (every layer, offsets baked in) */
    function crop(r) {
        const [x, y, w, h] = r.map(Math.round);
        if (w < 8 || h < 8) return;
        remember(T('Crop'), 'all');
        for (const l of D.layers) {
            const c = canvas(w, h);
            ctx(c).drawImage(l.canvas, l.x - x, l.y - y);
            l.canvas = c;
            D.store[l.id] = c;
            l.x = l.y = 0;
            l.dirty = true;
            if (l.text) { l.text.x -= x; l.text.y -= y; }
        }
        D.width = w;
        D.height = h;
        D.sel = null;
        selectionChanged();
        fit();
        refresh();
    }

    /* ---- the view ---- */

    function toDoc(e) {
        const r = UI.view.getBoundingClientRect();
        return [(e.clientX - r.left - D.view.ox) / D.view.scale, (e.clientY - r.top - D.view.oy) / D.view.scale];
    }
    function toView(x, y) { return [x * D.view.scale + D.view.ox, y * D.view.scale + D.view.oy]; }
    function fit() {
        const r = UI.area.getBoundingClientRect();
        const s = Math.min((r.width - 40) / D.width, (r.height - 40) / D.height);
        D.view.scale = Math.max(0.02, s);
        D.view.ox = (r.width - D.width * D.view.scale) / 2;
        D.view.oy = (r.height - D.height * D.view.scale) / 2;
        draw();
    }
    IM.fit = fit;
    function zoomAt(k, cx, cy) {
        const s = Math.max(0.02, Math.min(32, D.view.scale * k));
        D.view.ox = cx - (cx - D.view.ox) * (s / D.view.scale);
        D.view.oy = cy - (cy - D.view.oy) * (s / D.view.scale);
        D.view.scale = s;
        draw();
    }

    let checker = null;
    function draw() {
        if (!UI.view || !D) return;
        const r = UI.area.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
        const W = Math.max(1, Math.round(r.width * dpr)), H = Math.max(1, Math.round(r.height * dpr));
        if (UI.view.width !== W || UI.view.height !== H) { UI.view.width = W; UI.view.height = H; }
        const c = UI.view.getContext('2d');
        c.setTransform(dpr, 0, 0, dpr, 0, 0);
        c.clearRect(0, 0, r.width, r.height);
        const {scale: s, ox, oy} = D.view;
        // a checkerboard where the shot is see-through
        if (!checker) { checker = canvas(16, 16); const k = ctx(checker); k.fillStyle = '#2a2e36'; k.fillRect(0, 0, 16, 16); k.fillStyle = '#3a3f49'; k.fillRect(0, 0, 8, 8); k.fillRect(8, 8, 8, 8); }
        c.save();
        c.fillStyle = c.createPattern(checker, 'repeat');
        c.fillRect(ox, oy, D.width * s, D.height * s);
        c.restore();
        c.save();
        c.beginPath();
        c.rect(ox, oy, D.width * s, D.height * s);
        c.clip();
        c.imageSmoothingEnabled = s < 2;
        c.translate(ox, oy);
        composite(c, s, 0, 0);
        c.restore();
        // the selection's marching ants
        if (D.ants) {
            c.save();
            c.translate(ox, oy);
            c.scale(s, s);
            c.lineWidth = 1 / s;
            c.strokeStyle = '#fff';
            c.stroke(D.ants);
            c.setLineDash([4 / s, 4 / s]);
            c.lineDashOffset = -(performance.now() / 60 % 8) / s;
            c.strokeStyle = '#000';
            c.stroke(D.ants);
            c.restore();
        }
        if (UI.drag && UI.drag.preview) UI.drag.preview(c);
        if (UI.cursor && ['brush', 'eraser'].includes(D.tool)) {
            c.save();
            c.beginPath();
            c.arc(UI.cursor[0], UI.cursor[1], Math.max(1, D.brush.size * s / 2), 0, Math.PI * 2);
            c.strokeStyle = 'rgba(255,255,255,.9)';
            c.lineWidth = 1;
            c.stroke();
            c.restore();
        }
        UI.zoomText && (UI.zoomText.textContent = Math.round(s * 100) + '%');
    }
    function animate() {
        if (!IM.active) return;
        if (D && D.ants) draw();
        UI.raf = setTimeout(() => requestAnimationFrame(animate), 110);
    }

    /* ---- pointer tools ---- */

    function pointer() {
        const v = UI.view;
        let space = false;
        IM.spaceDown = (on) => { space = on; v.style.cursor = on ? 'grab' : cursorFor(); };
        v.addEventListener('pointerdown', (e) => {
            if (!D) return;
            v.setPointerCapture(e.pointerId);
            const p = toDoc(e), tool = space || e.button === 1 ? 'hand' : D.tool;
            const mode = e.shiftKey ? 'add' : e.altKey ? 'sub' : 'replace';
            const r = UI.view.getBoundingClientRect(), vx = e.clientX - r.left, vy = e.clientY - r.top;
            if (tool === 'hand') { UI.drag = {kind: 'hand', from: [vx, vy], o: [D.view.ox, D.view.oy]}; return; }
            if (tool === 'zoom') { zoomAt(e.altKey ? 0.5 : 2, vx, vy); return; }
            if (tool === 'picker') {
                const flat = IM.flatten(), d = ctx(flat).getImageData(Math.max(0, Math.min(D.width - 1, Math.floor(p[0]))), Math.max(0, Math.min(D.height - 1, Math.floor(p[1]))), 1, 1).data;
                D.fg = hex(d[0], d[1], d[2]);
                options();
                return;
            }
            if (tool === 'object') { if (IM.objectClick) FS.guard(() => IM.objectClick(p, e.altKey ? 0 : 1, e.shiftKey || e.altKey))(); return; }
            if (tool === 'wand') {
                const l = activeLayer();
                const m = region(l, p[0], p[1], D.tolerance, D.contiguous);
                select((c) => c.drawImage(m, 0, 0), mode);
                return;
            }
            if (tool === 'bucket') {
                const l = activeLayer();
                if (D.sel) { IM.fillSelected(D.fg); return; }
                remember(T('Fill'), [l.id]);
                const m = region(l, p[0], p[1], D.tolerance, D.contiguous), c = ctx(m);
                c.globalCompositeOperation = 'source-in';
                c.fillStyle = D.fg;
                c.fillRect(0, 0, D.width, D.height);
                applyToLayer(l, m, 1, false);
                refresh();
                return;
            }
            if (tool === 'text') {
                const hit = D.layers.slice().reverse().find((l) => l.text && l.visible && ctx(l.canvas).getImageData(Math.floor(p[0] - l.x), Math.floor(p[1] - l.y), 1, 1).data[3] > 0);
                textAt(p[0], p[1], hit || null);
                return;
            }
            if (tool === 'move') {
                const l = activeLayer();
                UI.drag = {kind: 'move', from: p, start: [l.x, l.y], done: false};
                remember(T('Move'), []);
                return;
            }
            if (tool === 'brush' || tool === 'eraser') {
                const l = activeLayer();
                remember(tool === 'brush' ? T('Brush') : T('Eraser'), [l.id]);
                UI.drag = {kind: 'paint', erase: tool === 'eraser', base: copyOf(l.canvas), stroke: canvas(D.width, D.height), last: null, layer: l};
                dab(p);
                return;
            }
            if (tool === 'rect' || tool === 'ellipse' || tool === 'crop') {
                UI.drag = {kind: tool, from: p, to: p, mode, preview: (c) => {
                    const [a, b] = [toView(...UI.drag.from), toView(...UI.drag.to)];
                    c.save();
                    c.strokeStyle = tool === 'crop' ? '#f5a524' : '#fff';
                    c.setLineDash([5, 4]);
                    c.beginPath();
                    if (tool === 'ellipse') c.ellipse((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, Math.abs(b[0] - a[0]) / 2, Math.abs(b[1] - a[1]) / 2, 0, 0, Math.PI * 2);
                    else c.rect(Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.abs(b[0] - a[0]), Math.abs(b[1] - a[1]));
                    c.stroke();
                    if (tool === 'crop') { c.fillStyle = 'rgba(0,0,0,.45)'; c.setLineDash([]); c.fillRect(0, 0, r.width, Math.min(a[1], b[1])); c.fillRect(0, Math.max(a[1], b[1]), r.width, r.height); c.fillRect(0, Math.min(a[1], b[1]), Math.min(a[0], b[0]), Math.abs(b[1] - a[1])); c.fillRect(Math.max(a[0], b[0]), Math.min(a[1], b[1]), r.width, Math.abs(b[1] - a[1])); }
                    c.restore();
                }};
                return;
            }
            if (tool === 'lasso') {
                UI.drag = {kind: 'lasso', points: [p], mode, preview: (c) => {
                    c.save();
                    c.strokeStyle = '#fff';
                    c.setLineDash([4, 3]);
                    c.beginPath();
                    UI.drag.points.forEach((pt, i) => { const [x, y] = toView(...pt); if (i) c.lineTo(x, y); else c.moveTo(x, y); });
                    c.stroke();
                    c.restore();
                }};
            }
        });
        v.addEventListener('pointermove', (e) => {
            if (!D) return;
            const r = UI.view.getBoundingClientRect();
            UI.cursor = [e.clientX - r.left, e.clientY - r.top];
            const g = UI.drag;
            if (!g) { if (['brush', 'eraser'].includes(D.tool)) draw(); return; }
            const p = toDoc(e);
            if (g.kind === 'hand') { D.view.ox = g.o[0] + UI.cursor[0] - g.from[0]; D.view.oy = g.o[1] + UI.cursor[1] - g.from[1]; draw(); return; }
            if (g.kind === 'move') {
                const l = activeLayer();
                l.x = Math.round(g.start[0] + p[0] - g.from[0]);
                l.y = Math.round(g.start[1] + p[1] - g.from[1]);
                g.done = true;
                draw();
                return;
            }
            if (g.kind === 'paint') { dab(p); return; }
            if (g.kind === 'lasso') { g.points.push(p); draw(); return; }
            g.to = e.shiftKey && g.kind !== 'crop' ? square(g.from, p) : p;
            draw();
        });
        const end = () => {
            const g = UI.drag;
            UI.drag = null;
            if (!g) return;
            if (g.kind === 'move' && !g.done) D.history.pop();
            if (g.kind === 'paint') { g.layer.dirty = true; refresh(); return; }
            if (g.kind === 'rect' || g.kind === 'ellipse') {
                const [x0, y0] = g.from, [x1, y1] = g.to;
                if (Math.abs(x1 - x0) < 2 && Math.abs(y1 - y0) < 2) { if (g.mode === 'replace') IM.deselect(); draw(); return; }
                select((c) => {
                    c.beginPath();
                    if (g.kind === 'ellipse') c.ellipse((x0 + x1) / 2, (y0 + y1) / 2, Math.abs(x1 - x0) / 2, Math.abs(y1 - y0) / 2, 0, 0, Math.PI * 2);
                    else c.rect(Math.min(x0, x1), Math.min(y0, y1), Math.abs(x1 - x0), Math.abs(y1 - y0));
                    c.fill();
                }, g.mode);
                return;
            }
            if (g.kind === 'lasso') {
                if (g.points.length < 3) { draw(); return; }
                select((c) => { c.beginPath(); g.points.forEach(([x, y], i) => (i ? c.lineTo(x, y) : c.moveTo(x, y))); c.closePath(); c.fill(); }, g.mode);
                return;
            }
            if (g.kind === 'crop') {
                const [x0, y0] = g.from, [x1, y1] = g.to;
                UI.pendingCrop = [Math.max(0, Math.min(x0, x1)), Math.max(0, Math.min(y0, y1)), 0, 0];
                UI.pendingCrop[2] = Math.min(D.width, Math.max(x0, x1)) - UI.pendingCrop[0];
                UI.pendingCrop[3] = Math.min(D.height, Math.max(y0, y1)) - UI.pendingCrop[1];
                UI.drag = {kind: 'crop-wait', preview: g.preview};
                g.to = [UI.pendingCrop[0] + UI.pendingCrop[2], UI.pendingCrop[1] + UI.pendingCrop[3]];
                g.from = [UI.pendingCrop[0], UI.pendingCrop[1]];
                UI.drag = Object.assign(g, {kind: 'crop-wait'});
                FS.message(T('Enter cuts the shot to the frame, Esc leaves it'));
                draw();
                return;
            }
            refresh();
        };
        v.addEventListener('pointerup', end);
        v.addEventListener('pointercancel', end);
        v.addEventListener('pointerleave', () => { UI.cursor = null; draw(); });
        v.addEventListener('wheel', (e) => {
            e.preventDefault();
            const r = UI.view.getBoundingClientRect();
            zoomAt(e.deltaY < 0 ? 1.15 : 1 / 1.15, e.clientX - r.left, e.clientY - r.top);
        }, {passive: false});
        v.addEventListener('dblclick', () => { if (D.tool === 'hand' || D.tool === 'zoom') fit(); });
        // pictures dropped on the canvas become layers
        UI.area.addEventListener('dragover', (e) => { if (e.dataTransfer.types.includes('Files')) e.preventDefault(); });
        UI.area.addEventListener('drop', FS.guard(async (e) => {
            const files = [...(e.dataTransfer.files || [])].filter((f) => f.type.startsWith('image/'));
            if (!files.length) return;
            e.preventDefault();
            for (const f of files) await IM.addPicture(f, f.name.replace(/\.[^.]+$/, ''));
        }));
    }
    const square = (a, b) => { const d = Math.max(Math.abs(b[0] - a[0]), Math.abs(b[1] - a[1])); return [a[0] + Math.sign(b[0] - a[0] || 1) * d, a[1] + Math.sign(b[1] - a[1] || 1) * d]; };

    /* a brush dab at p, and the line of dabs from the last one: a soft round tip (hardness), painted at full strength
     * into the stroke and laid on the layer at the brush's opacity (so a stroke never darkens where it crosses itself) */
    function dab(p) {
        const g = UI.drag, c = ctx(g.stroke), rad = D.brush.size / 2;
        const tip = (x, y) => {
            const grd = c.createRadialGradient(x, y, rad * D.brush.hardness * 0.999, x, y, rad);
            const col = g.erase ? '#000000' : D.fg, [r, gg, b] = rgb(col);
            grd.addColorStop(0, `rgba(${r},${gg},${b},1)`);
            grd.addColorStop(1, `rgba(${r},${gg},${b},0)`);
            c.fillStyle = grd;
            c.beginPath();
            c.arc(x, y, rad, 0, Math.PI * 2);
            c.fill();
        };
        if (g.last) {
            const [x0, y0] = g.last, d = Math.hypot(p[0] - x0, p[1] - y0), step = Math.max(1, rad * 0.2);
            for (let t = step; t <= d; t += step) tip(x0 + (p[0] - x0) * t / d, y0 + (p[1] - y0) * t / d);
        } else tip(p[0], p[1]);
        g.last = p;
        const l = g.layer, lc = ctx(l.canvas);
        lc.clearRect(0, 0, D.width, D.height);
        lc.drawImage(g.base, 0, 0);
        applyToLayer(l, g.stroke, D.brush.opacity, g.erase);
        draw();
    }

    function cursorFor() {
        return {move: 'move', hand: 'grab', zoom: 'zoom-in', text: 'text', picker: 'copy', brush: 'none', eraser: 'none'}[D.tool] || 'crosshair';
    }
    IM.tool = function (name) {
        D.tool = name;
        if (name !== 'crop' && UI.drag && UI.drag.kind === 'crop-wait') UI.drag = null;
        UI.tools.querySelectorAll('.im-tool').forEach((b) => b.classList.toggle('on', b.dataset.tool === name));
        UI.view.style.cursor = cursorFor();
        options();
        draw();
    };

    /* ---- keys (when the workspace is open; returns true when it took the key) ---- */

    IM.keys = function (e) {
        if (!IM.active || !D) return false;
        const k = e.key, ctrl = e.ctrlKey || e.metaKey, code = e.code;
        const take = () => { e.preventDefault(); e.stopPropagation(); return true; };
        if (k === ' ' ) { if (!IM.spacing) { IM.spacing = true; IM.spaceDown(true); window.addEventListener('keyup', function up(ev) { if (ev.key === ' ') { IM.spacing = false; IM.spaceDown(false); window.removeEventListener('keyup', up, true); } }, true); } return take(); }
        if (ctrl && code === 'KeyZ' && !e.shiftKey) { IM.undo(); return take(); }
        if (ctrl && (code === 'KeyY' || (code === 'KeyZ' && e.shiftKey))) { IM.redo(); return take(); }
        if (ctrl && code === 'KeyA') { IM.selectAll(); return take(); }
        if (ctrl && code === 'KeyD') { IM.deselect(); return take(); }
        if (ctrl && e.shiftKey && code === 'KeyI') { IM.invert(); return take(); }
        if (ctrl && code === 'KeyJ') { IM.layerViaCopy(); return take(); }
        if (ctrl && code === 'KeyC') { copy(); return take(); }
        if (ctrl && code === 'KeyS') { FS.guard(IM.save)(); return take(); }
        if (ctrl && code === 'Digit0') { fit(); return take(); }
        if (ctrl && (k === '+' || k === '=')) { zoomAt(1.25, UI.view.clientWidth / 2, UI.view.clientHeight / 2); return take(); }
        if (ctrl && k === '-') { zoomAt(0.8, UI.view.clientWidth / 2, UI.view.clientHeight / 2); return take(); }
        if (ctrl && e.altKey && code === 'KeyV') return false;   // the editor's paste-style is not for pictures
        if (ctrl) return false;   // Ctrl+V comes as a paste event
        if (k === 'Delete' || k === 'Backspace') { IM.clearSelected(); return take(); }
        if (k === 'Escape') { if (UI.drag && UI.drag.kind === 'crop-wait') { UI.drag = null; draw(); } else if (D.sel) IM.deselect(); return take(); }
        if (k === 'Enter' && UI.drag && UI.drag.kind === 'crop-wait') { const r = UI.pendingCrop; UI.drag = null; crop(r); return take(); }
        if (k === '[' || k === ']') { D.brush.size = Math.max(1, Math.min(1000, Math.round(D.brush.size * (k === ']' ? 1.2 : 1 / 1.2)))); options(); draw(); return take(); }
        if (code === 'KeyX') { [D.fg, D.bg] = [D.bg, D.fg]; options(); return take(); }
        if (code === 'KeyD' && !e.shiftKey) { D.fg = '#ffffff'; D.bg = '#000000'; options(); return take(); }
        if (k.startsWith('Arrow') && D.tool === 'move') {
            const l = activeLayer(), n = e.shiftKey ? 10 : 1;
            remember(T('Move'), []);
            l.x += k === 'ArrowLeft' ? -n : k === 'ArrowRight' ? n : 0;
            l.y += k === 'ArrowUp' ? -n : k === 'ArrowDown' ? n : 0;
            refresh();
            return take();
        }
        if (code === 'KeyM') { IM.tool(D.tool === 'rect' ? 'ellipse' : 'rect'); return take(); }
        const t = TOOLS.find(([, , , key]) => key && code === 'Key' + key);
        if (t) { IM.tool(t[0]); return take(); }
        return false;
    };

    let clip = null;   // pixels copied with Ctrl+C
    function copy() {
        const l = activeLayer(), c = canvas(D.width, D.height), cc = ctx(c);
        cc.drawImage(l.canvas, l.x, l.y);
        if (D.sel) { cc.globalCompositeOperation = 'destination-in'; cc.drawImage(D.sel, 0, 0); }
        clip = c;
        c.toBlob((b) => { try { navigator.clipboard.write([new ClipboardItem({'image/png': b})]).catch(() => null); } catch (err) { /* no clipboard */ } }, 'image/png');
        FS.message(T('Copied'));
    }
    function paste(e) {
        if (!IM.active || !D) return;
        const tag = (e.target.tagName || '').toLowerCase();
        if (['input', 'textarea'].includes(tag)) return;
        const item = [...(e.clipboardData && e.clipboardData.items || [])].find((i) => i.type.startsWith('image/'));
        e.preventDefault();
        if (item) { FS.guard(() => IM.addPicture(item.getAsFile(), T('Pasted')))(); return; }
        if (clip) { remember(T('Paste'), []); addLayer(T('Pasted'), copyOf(clip)); refresh(); }
    }

    /* ---- the panels ---- */

    function build() {
        const h = FS.h;
        UI.view = h('canvas', {class: 'im-view'});
        UI.area = h('div', {class: 'im-area'}, UI.view);
        UI.tools = h('div', {class: 'im-tools'}, ...TOOLS.map(([id, icon, label, key]) => h('button', {class: 'im-tool', 'data-tool': id,
            title: T(label) + (key ? ` (${key})` : ''), onclick: () => IM.tool(id)}, icon)),
            UI.swatch = h('div', {class: 'im-swatches', title: T('Colours: X swaps them, D makes them white and black')}));
        UI.opts = h('div', {class: 'im-options'});
        UI.zoomText = h('span', {class: 'fx-dim im-zoom'});
        UI.overlay = h('div', {class: 'im-root', dir: 'ltr'},
            UI.opts,
            h('div', {class: 'im-body'}, UI.tools, UI.area));
        UI.layersList = h('div', {class: 'im-layers'});
        UI.layerProps = h('div', {class: 'im-layer-props'});
        UI.picker = h('input', {type: 'file', accept: 'image/*', multiple: true, hidden: true});
        UI.picker.addEventListener('change', FS.guard(async () => { for (const f of UI.picker.files) await IM.addPicture(f, f.name.replace(/\.[^.]+$/, '')); UI.picker.value = ''; }));
        const lbtn = (icon, label, fn) => h('button', {class: 'fx-icon', title: T(label), onclick: FS.guard(fn)}, icon);
        UI.status = h('div', {class: 'fx-dim im-status'});
        UI.right = h('div', {class: 'im-right', dir: FS.hebrew() ? 'rtl' : 'ltr'},
            h('div', {class: 'im-head'}, h('b', {}, '🖼 ' + T('Image shot')), h('span', {class: 'fx-spacer'}),
                h('button', {class: 'fx-icon', title: T('Close the image workspace (it is saved)'), onclick: FS.guard(() => IM.close())}, '✕')),
            h('div', {class: 'fx-row im-actions'},
                h('button', {class: 'fx-primary', onclick: FS.guard(() => IM.save())}, '💾 ' + T('Save into the shot')),
                UI.videoBtn = h('button', {class: 'fx-btn', onclick: FS.guard(() => IM.toVideo())}, '🎬 ' + T('Make a video from it')),
                h('button', {class: 'fx-btn', title: T('Save the picture as a file, or send it to img2img, Inpaint, FIBO / FLUX references or Video Studio (Wan, LTX)'),
                    onclick: FS.guard(() => {
                        if (!window.forgeFrameShare) throw new Error(T('This destination is not available'));
                        window.forgeFrameShare.open({canvas: IM.flatten(), filename: (D.title || 'shot-' + D.shot), host: FS.UI.root});
                    })}, '📤 ' + T('Save / send…'))),
            UI.status,
            UI.aiBox = h('div', {class: 'im-ai'}),
            h('div', {class: 'fx-group'}, h('div', {class: 'fx-group-title im-layers-title'}, T('Layers'), h('span', {class: 'fx-spacer'}),
                lbtn('＋', 'New layer', IM.newLayer), lbtn('⧉', 'Duplicate the layer', IM.duplicate), lbtn('🖼', 'Add a picture as a layer', () => UI.picker.click()),
                lbtn('⤓', 'Merge down', IM.mergeDown), lbtn('▲', 'Up', () => IM.moveLayer(1)), lbtn('▼', 'Down', () => IM.moveLayer(-1)), lbtn('🗑', 'Delete the layer', IM.removeLayer)),
                UI.layerProps, UI.layersList, UI.picker));
        pointer();
        for (const hook of IM.panels) hook(UI.aiBox, h);
    }
    IM.panels = [];   // the AI tools add their sections here (q-image-ai.js)
    IM.ui = UI;

    function options() {
        const h = FS.h, t = D.tool;
        const num = (label, value, min, max, step, set) => {
            const i = h('input', {type: 'range', min, max, step, value, class: 'fx-range'});
            const out = h('span', {class: 'fx-dim'}, String(value));
            i.addEventListener('input', () => { set(+i.value); out.textContent = i.value; draw(); });
            return h('label', {class: 'im-opt'}, T(label), i, out);
        };
        const check = (label, on, set) => { const i = h('input', {type: 'checkbox', checked: on}); i.addEventListener('change', () => set(i.checked)); return h('label', {class: 'im-opt'}, i, T(label)); };
        const parts = [h('b', {class: 'im-opt-title'}, T((TOOLS.find((x) => x[0] === t) || [])[2] || ''))];
        if (t === 'brush' || t === 'eraser') parts.push(num('Size', D.brush.size, 1, 600, 1, (v) => { D.brush.size = v; }),
            num('Hardness', Math.round(D.brush.hardness * 100), 0, 100, 1, (v) => { D.brush.hardness = v / 100; }),
            num('Opacity', Math.round(D.brush.opacity * 100), 1, 100, 1, (v) => { D.brush.opacity = v / 100; }));
        if (t === 'object') parts.push(h('span', {class: 'fx-dim'}, T('Click the object; Shift+click adds to it, Alt+click takes a part away')));
        if (['rect', 'ellipse', 'lasso', 'wand'].includes(t)) parts.push(num('Feather', D.feather, 0, 100, 1, (v) => { D.feather = v; }),
            h('span', {class: 'fx-dim'}, T('Shift adds, Alt takes away')));
        if (t === 'wand' || t === 'bucket') parts.push(num('Tolerance', D.tolerance, 0, 255, 1, (v) => { D.tolerance = v; }),
            check('Contiguous', D.contiguous, (v) => { D.contiguous = v; }));
        if (t === 'text') {
            const fam = h('select', {class: 'fx-select'}, ...(FS.FONTS || ['Segoe UI']).map((f) => h('option', {value: f, selected: f === D.font.family}, f)));
            fam.addEventListener('change', () => { D.font.family = fam.value; });
            parts.push(num('Size', D.font.size, 8, 600, 1, (v) => { D.font.size = v; }), fam, h('span', {class: 'fx-dim'}, T('Click to write; Ctrl+Enter ends')));
        }
        if (t === 'crop') parts.push(h('span', {class: 'fx-dim'}, T('Drag the frame, then Enter')));
        if (t === 'move') parts.push(h('span', {class: 'fx-dim'}, T('Drag the layer; arrows move it a pixel (Shift: 10)')));
        parts.push(h('span', {class: 'fx-spacer'}),
            h('button', {class: 'fx-btn', title: T('Select all (Ctrl+A)'), onclick: () => IM.selectAll()}, T('All')),
            h('button', {class: 'fx-btn', title: T('Deselect (Ctrl+D)'), onclick: () => IM.deselect()}, T('None')),
            h('button', {class: 'fx-btn', title: T('Invert (Ctrl+Shift+I)'), onclick: () => IM.invert()}, T('Invert')),
            h('button', {class: 'fx-icon', title: T('Undo (Ctrl+Z)'), onclick: () => IM.undo()}, '↶'),
            h('button', {class: 'fx-icon', title: T('Redo (Ctrl+Y)'), onclick: () => IM.redo()}, '↷'),
            h('button', {class: 'fx-btn', title: T('Fit (Ctrl+0)'), onclick: fit}, T('Fit')), UI.zoomText);
        UI.opts.replaceChildren(...parts);
        // the two colours
        const fg = h('input', {type: 'color', value: D.fg, class: 'im-fg', title: T('Colour')});
        const bg = h('input', {type: 'color', value: D.bg, class: 'im-bg', title: T('Second colour')});
        fg.addEventListener('input', () => { D.fg = fg.value; });
        bg.addEventListener('input', () => { D.bg = bg.value; });
        UI.swatch.replaceChildren(bg, fg);
    }

    function layersPanel() {
        const h = FS.h;
        const rows = D.layers.slice().reverse().map((l) => {
            const thumb = canvas(44, Math.max(10, Math.round(44 * D.height / D.width)));
            const tc = ctx(thumb);
            tc.drawImage(l.canvas, l.x * 44 / D.width, l.y * 44 / D.width, 44, thumb.height);
            const eye = h('button', {class: 'im-eye' + (l.visible ? ' on' : ''), title: T('Show / hide'), onclick: (e) => {
                e.stopPropagation(); remember(T('Show / hide'), []); l.visible = !l.visible; refresh(); }}, l.visible ? '👁' : '·');
            const name = h('span', {class: 'im-lname'}, (l.text ? 'T ' : '') + l.name);
            const row = h('div', {class: 'im-layer' + (l.id === D.active ? ' on' : ''), onclick: () => { D.active = l.id; refresh(); }}, eye, thumb, name);
            row.addEventListener('dblclick', () => {
                if (l.text) return IM.editText(l.id);
                const n = prompt(T('Layer name'), l.name);
                if (n) { remember(T('Rename'), []); l.name = n.slice(0, 60); refresh(); }
            });
            return row;
        });
        UI.layersList.replaceChildren(...rows);
        const l = activeLayer();
        const blend = h('select', {class: 'fx-select', title: T('Blend mode')}, ...BLENDS.map((b) => h('option', {value: b, selected: b === l.blend}, FS.hebrew() ? T('blend:' + b) : b[0].toUpperCase() + b.slice(1).replace('-', ' '))));
        blend.addEventListener('change', () => { remember(T('Blend mode'), []); l.blend = blend.value; refresh(); });
        const opacity = h('input', {type: 'range', min: 0, max: 100, value: Math.round(l.opacity * 100), class: 'fx-range'});
        let began = false;
        opacity.addEventListener('input', () => { if (!began) { remember(T('Opacity'), []); began = true; } l.opacity = +opacity.value / 100; draw(); });
        opacity.addEventListener('change', () => { began = false; refresh(); });
        UI.layerProps.replaceChildren(h('label', {class: 'im-opt'}, T('Blend'), blend), h('label', {class: 'im-opt'}, T('Opacity'), opacity));
    }

    function refresh() {
        if (!D || !UI.right) return;
        layersPanel();
        draw();
        UI.status.textContent = D.dirty ? T('Not saved yet') : T('Saved');
    }
    IM.refresh = refresh;
    IM.doc = () => D;

    /* ---- opening, saving, closing ---- */

    function enter() {
        IM.active = true;
        FS.UI.root.classList.add('fx-image-mode');
        FS.UI.stage.append(UI.overlay);
        FS.UI.inspector.after(UI.right);
        options();
        IM.tool(D.tool);
        requestAnimationFrame(() => { fit(); refresh(); });
        animate();
        document.addEventListener('paste', paste);
        if (!UI.resize) { UI.resize = () => IM.active && draw(); window.addEventListener('resize', UI.resize); }
    }

    /* the image shot of clip `c` (an image clip: its picture becomes the first layer if it has no shot yet) */
    IM.open = async function (c) {
        if (IM.active) await IM.close();
        if (S.playing) FS.player.pause();
        FS.busy(T('Opening the picture…'));
        try {
            let shot = c.imageShot;
            if (!shot) {
                const info = await FS.probe(c.media);
                const doc = await FS.api('image/new', {name: S.project, width: info.width || S.doc.settings.width, height: info.height || S.doc.settings.height, picture: c.media});
                shot = doc.id;
                FS.change('image shot', () => { const x = FS.clip(c.id); x.imageShot = shot; });
            }
            build();
            await openDoc(shot, c.id);
            tidy(shot);
        } finally { FS.busy(null); }
        enter();
    };
    /* the shot's earlier flattened pictures that no clip shows any more go (at opening: during a session an undo in
     * the editor may still want one) */
    function tidy(shot) {
        const used = new Set();
        for (const c of S.doc.clips) { if (c.media) used.add(c.media); for (const t of c.takes || []) used.add(t); }
        FS.api('image/tidy', {name: S.project, shot, used: [...used]}).then((r) => { for (const w of r.forgotten || []) delete S.media[w]; }).catch(() => null);
    }
    /* a new empty shot at the playhead (the edit's size), on the main track */
    IM.create = async function (fill) {
        if (!S.project) throw new Error(T('Open or create a project first'));
        if (IM.active) await IM.close();
        const {width, height} = S.doc.settings;
        const doc = await FS.api('image/new', {name: S.project, width, height, fill: fill || '#ffffff'});
        build();
        await openDoc(doc.id, null);
        D.dirty = true;
        const saved = await save();
        const c = await FS.addMedia(saved.web, {time: S.time}, {seconds: 3, extra: {imageShot: doc.id}});
        D.clipId = c.id;
        enter();
        FS.select([c.id]);
        return c;
    };

    async function save() {
        const form = new FormData();
        form.append('name', S.project);
        form.append('shot', D.shot);
        const meta = {width: D.width, height: D.height, title: D.title, layers: D.layers.map((l) => ({id: l.id, name: l.name, visible: l.visible,
            opacity: l.opacity, blend: l.blend, x: l.x, y: l.y, text: l.text || undefined}))};
        form.append('doc', JSON.stringify(meta));
        for (const l of D.layers) if (l.dirty) form.append('layer:' + l.id, await new Promise((r) => l.canvas.toBlob(r, 'image/png')), l.id + '.png');
        form.append('flat', await new Promise((r) => IM.flatten().toBlob(r, 'image/png')), 'flat.png');
        const r = await fetch('/forge-studio/image/save', {method: 'POST', body: form});
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
        const out = await r.json();
        for (const l of D.layers) l.dirty = false;
        D.dirty = false;
        S.media[out.web] = {path: out.web, missing: false};
        return out;
    }
    IM.save = async function () {
        if (!D) return null;
        FS.busy(T('Saving the picture…'));
        let out;
        try { out = await save(); } finally { FS.busy(null); }
        await FS.probe(out.web);
        if (D.clipId && FS.clip(D.clipId)) {
            FS.change('image shot', () => {
                const x = FS.clip(D.clipId);
                x.media = out.web;
                x.kind = 'image';
                x.imageShot = D.shot;
            });
        }
        refresh();
        FS.message(T('The shot is saved'));
        return out;
    };
    IM.close = async function () {
        if (!IM.active) return;
        if (D && D.dirty) await IM.save();
        IM.active = false;
        clearTimeout(UI.raf);
        document.removeEventListener('paste', paste);
        if (UI.overlay) UI.overlay.remove();
        if (UI.right) UI.right.remove();
        FS.UI.root.classList.remove('fx-image-mode');
        FS.player.resize && FS.player.resize();
    };
    /* the shot as the first frame of a new video clip (Wan), placed after the shot */
    IM.toVideo = async function () {
        const saved = await IM.save();
        const c = D.clipId && FS.clip(D.clipId);
        if (!c) throw new Error(T('Save the shot into the edit first'));
        if (!FS.gen || !FS.gen.request) throw new Error(T('Video making is not available'));
        const words = await FS.ask(T('Make a video from the shot'), T('What happens in the video? (empty: natural, gentle motion)'), '',
            T('Wan starts from this picture; the new clip goes right after the shot.'));
        if (words == null) return;
        await IM.close();
        await FS.gen.request({kind: 'picture', prompt: String(words || '').trim() || 'natural, gentle motion, the same scene',
            start: {web: saved.web, seconds: 0}, seconds: 5}, {time: FS.end(c)});
        FS.message(T('The video is being made after the shot'));
    };

    /* ---- the entries ---- */

    FS.clipActions.push({id: 'imageshop', label: '🖼 ' + T('Open in the image workspace'),
        when: (c) => c && c.kind === 'image' && c.media, run: (c) => FS.guard(() => IM.open(c))()});
    FS.on('project', () => { if (IM.active) { IM.active = false; if (UI.overlay) UI.overlay.remove(); if (UI.right) UI.right.remove(); FS.UI.root && FS.UI.root.classList.remove('fx-image-mode'); } });
    // picking another clip on the timeline leaves the workspace (saving it)
    FS.on('select', (ids) => { if (IM.active && D && D.clipId && ids.length && !ids.includes(D.clipId)) FS.guard(() => IM.close())(); });
})();

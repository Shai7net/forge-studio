/* Forge Studio - HakuImg's effects in the image workspace (forgestudio/haku.py on the server runs HakuImg's own
 * functions: KohakuBlueleaf/a1111-sd-webui-haku-img, installed as a Forge extension).
 *
 * An effect works on the active layer (inside the selection when there is one): every change of a setting shows it
 * on the canvas a moment later, Apply keeps it (one undo step), Cancel puts the layer back. The tone curve is drawn
 * here and HakuImg's spline is used for the picture.
 */
(function () {
    'use strict';
    const FS = window.ForgeStudio = window.ForgeStudio || {};
    const T = (...a) => FS.T(...a);
    const IM = FS.imageShop;
    if (!IM) return;

    // [key, label, min, max, step, default] for a slider; [key, label, ['a', 'b'...], default] for a choice;
    // [key, label, true|false] for a checkbox. The ranges and defaults are HakuImg's.
    const EFFECTS = [
        ['color', '🎨', 'Color', [
            ['bright', 'Brightness', -100, 100, 1, 0], ['contrast', 'Contrast', -100, 100, 1, 0],
            ['sat', 'Saturation', -100, 100, 1, 0], ['temp', 'Temperature', -100, 100, 1, 0],
            ['hue', 'Hue', -90, 90, 1, 0], ['gamma', 'Gamma', 0.2, 2.2, 0.05, 1],
            ['exposure', 'Exposure offset', 0, 1, 0.01, 0], ['hdr', 'HDR', 0, 1, 0.01, 0],
            ['sharpness', 'Sharpness', 0, 1, 0.01, 0], ['noise', 'Noise', 0, 1, 0.01, 0],
            ['vignette', 'Vignette', 0, 1, 0.01, 0]]],
        ['curve', '📈', 'Tone curve', []],
        ['blur', '💧', 'Blur', [['radius', 'Blur', 0, 128, 1, 8]]],
        ['sketch', '✏', 'Sketch', [
            ['kernel', 'Kernel size', 0, 25, 1, 0], ['sigma', 'Sigma', 1, 5, 0.05, 1.4], ['k_sigma', 'K sigma', 1, 5, 0.05, 1.6],
            ['eps', 'Epsilon', -0.2, 0.2, 0.005, -0.03], ['phi', 'Phi', 1, 50, 1, 10], ['gamma', 'Gamma', 0.75, 1, 0.005, 1],
            ['color', 'Colour', ['gray', 'rgb'], 'gray'], ['scale', 'Use scale', false]]],
        ['pixelize', '👾', 'Pixelize', [
            ['colors', 'Colours', 2, 256, 1, 16], ['dot', 'Dot size', 1, 32, 1, 8], ['smooth', 'Smoothing', 0, 10, 1, 0],
            ['outline', 'Outline inflating', 0, 10, 1, 5], ['mode', 'Colour reduction', ['kmeans', 'dithering', 'kmeans with dithering'], 'kmeans']]],
        ['pixeloe', '🕹', 'PixelOE', [
            ['pixel', 'Pixel size', 1, 32, 1, 4], ['thickness', 'Thickness', 0, 6, 1, 2], ['colors', 'Colours', 2, 256, 1, 256],
            ['mode', 'Downscale', ['contrast', 'k_centroid', 'lanczos', 'nearest', 'bilinear'], 'contrast'],
            ['quant', 'Colour quantization', ['kmeans', 'weighted-kmeans', 'repeat-kmeans'], 'kmeans'],
            ['dither', 'Dithering', ['ordered', 'error_diffusion', 'none'], 'ordered'],
            ['color_quant', 'Reduce the colours', false], ['no_upscale', 'Keep it small (no upscale)', false]]],
        ['outline', '🔲', 'Outline expansion', [['pixel', 'Pixel size', 1, 32, 1, 4], ['thickness', 'Thickness', 1, 6, 1, 3]]],
        ['glow', '✨', 'Glow', [['range', 'Range', 2, 128, 1, 16], ['strength', 'Strength', 0, 1, 0.05, 1],
            ['mode', 'Glow mode', ['BS', 'BMBL'], 'BS']]],
        ['chromatic', '🌈', 'Chromatic aberration', [['strength', 'Strength', 0, 3, 0.05, 1], ['blur', 'Blur', false]]],
        ['lens', '🔭', 'Lens distortion', [['k1', 'K1', -1, 1, 0.01, 0], ['k2', 'K2', -1, 1, 0.01, 0]]],
        ['tiltshift', '🏙', 'Tilt shift', [['focus', 'Focus line', -3, 3, 0.1, 0], ['dof', 'Depth of field', 10, 100, 1, 60]]],
        ['flip', '⇋', 'Flip', [['axis', 'Axis', ['horizontal', 'vertical'], 'horizontal']]],
    ];
    const CHOICE_LABELS = {gray: 'Grey', rgb: 'Colour', BS: 'Blur + screen', BMBL: 'Blur, multiply, lighten',
        horizontal: 'Left ↔ right', vertical: 'Up ↕ down', none: 'None'};

    const F = {effect: 'color', values: {}, curves: {all: [], r: [], g: [], b: []}, channel: 'all', state: null, blob: null,
        result: null, token: 0, timer: 0, installed: null};
    const defaults = (id) => Object.fromEntries(EFFECTS.find((e) => e[0] === id)[3].map((c) => [c[0], c[c.length - 1]]));
    for (const e of EFFECTS) F.values[e[0]] = defaults(e[0]);

    function panel(box, h) {
        F.state = F.result = F.blob = null;   // a new workspace (another shot): nothing of the last one is previewed
        const status = h('div', {class: 'fx-dim im-fx-status'});
        const chips = h('div', {class: 'im-fx-chips'});
        const controls = h('div', {class: 'im-fx-controls'});
        const apply = h('button', {class: 'fx-primary', onclick: FS.guard(() => commit())}, '✓ ' + T('Apply'));
        const cancel = h('button', {class: 'fx-btn', onclick: () => stop(T('Cancelled: the layer is as it was'))}, T('Cancel'));
        const reset = h('button', {class: 'fx-btn', title: T('HakuImg\'s default settings'), onclick: () => {
            F.values[F.effect] = defaults(F.effect);
            if (F.effect === 'curve') F.curves = {all: [], r: [], g: [], b: []};
            draw();
            change();
        }}, T('Reset'));
        const live = h('input', {type: 'checkbox', checked: true});
        const actions = h('div', {class: 'fx-row im-fx-actions'}, apply, cancel, reset, h('span', {class: 'fx-spacer'}),
            h('label', {class: 'im-opt', title: T('Shows each change on the canvas a moment later')}, live, T('Live preview')));
        const note = h('div', {class: 'fx-dim im-fx-note'});
        const group = h('div', {class: 'fx-group im-fx'}, h('div', {class: 'fx-group-title'}, '🎨 ' + T('Effects (HakuImg)')),
            note, chips, controls, actions, status);
        box.append(group);

        function say(t) { status.textContent = t || ''; }

        function slider(effect, c) {
            const [key, label, min, max, step] = c;
            const v = F.values[effect][key];
            const range = h('input', {type: 'range', min, max, step, value: v, class: 'fx-range'});
            const number = h('input', {type: 'number', min, max, step, value: v, class: 'fx-num'});
            const set = (x) => { F.values[effect][key] = +x; change(); };
            range.addEventListener('input', () => { number.value = range.value; set(range.value); });
            number.addEventListener('change', () => { range.value = number.value; set(number.value); });
            return h('label', {class: 'im-fx-slider'}, h('span', {}, T(label)), range, number);
        }
        function choice(effect, c) {
            const [key, label, options] = c;
            const sel = h('select', {class: 'fx-select'}, ...options.map((o) => h('option', {value: o, selected: o === F.values[effect][key]},
                CHOICE_LABELS[o] ? T(CHOICE_LABELS[o]) : o)));
            sel.addEventListener('change', () => { F.values[effect][key] = sel.value; change(); });
            return h('label', {class: 'im-fx-slider im-fx-choice'}, h('span', {}, T(label)), sel);
        }
        function checkbox(effect, c) {
            const [key, label] = c;
            const i = h('input', {type: 'checkbox', checked: !!F.values[effect][key]});
            i.addEventListener('change', () => { F.values[effect][key] = i.checked; change(); });
            return h('label', {class: 'im-opt'}, i, T(label));
        }

        function draw() {
            chips.replaceChildren(...EFFECTS.map(([id, icon, label]) => h('button', {class: 'fx-btn im-fx-chip' + (id === F.effect ? ' on' : ''),
                title: T(label), onclick: () => choose(id)}, icon + ' ' + T(label))));
            const e = EFFECTS.find((x) => x[0] === F.effect);
            if (F.effect === 'curve') { controls.replaceChildren(curveEditor(h)); return; }
            controls.replaceChildren(...e[3].map((c) => (Array.isArray(c[2]) ? choice(F.effect, c) : typeof c[2] === 'boolean' ? checkbox(F.effect, c) : slider(F.effect, c))));
        }

        function choose(id) {
            if (id === F.effect) return;
            if (F.state) stop();
            F.effect = id;
            draw();
            say(id === 'flip' ? T('Choose the axis; Apply flips the layer') : T('Change a setting to see it on the active layer'));
        }

        /* ---- the tone curve: a point is added by a click, dragged, and taken away by a double click ---- */
        function curveEditor(hh) {
            const size = 200, c = hh('canvas', {class: 'im-curve', width: size, height: size});
            const tabs = hh('div', {class: 'im-modes'}, ...[['all', 'All'], ['r', 'Red'], ['g', 'Green'], ['b', 'Blue']].map(([k, l]) =>
                hh('button', {class: 'fx-btn' + (k === F.channel ? ' on' : ''), onclick: () => { F.channel = k; draw(); }}, T(l))));
            const g = c.getContext('2d');
            const colour = {all: '#e8ebf1', r: '#f87171', g: '#4ade80', b: '#60a5fa'};
            const toCanvas = ([x, y]) => [x / 255 * size, size - y / 255 * size];
            const toValue = (px, py) => [Math.max(1, Math.min(254, Math.round(px / size * 255))), Math.max(0, Math.min(255, Math.round((size - py) / size * 255)))];
            function paint() {
                g.clearRect(0, 0, size, size);
                g.fillStyle = '#0d0f13';
                g.fillRect(0, 0, size, size);
                g.strokeStyle = '#262b35';
                g.lineWidth = 1;
                for (let i = 1; i < 4; i++) { g.beginPath(); g.moveTo(i * size / 4, 0); g.lineTo(i * size / 4, size); g.moveTo(0, i * size / 4); g.lineTo(size, i * size / 4); g.stroke(); }
                g.strokeStyle = '#323946';
                g.beginPath(); g.moveTo(0, size); g.lineTo(size, 0); g.stroke();
                for (const ch of ['r', 'g', 'b', 'all']) {
                    const pts = F.curves[ch];
                    if (!pts.length && ch !== F.channel) continue;
                    g.strokeStyle = colour[ch];
                    g.globalAlpha = ch === F.channel ? 1 : 0.35;
                    g.lineWidth = ch === F.channel ? 2 : 1;
                    const f = spline(pts);
                    g.beginPath();
                    for (let x = 0; x <= 255; x += 3) { const [px, py] = toCanvas([x, f(x)]); x ? g.lineTo(px, py) : g.moveTo(px, py); }
                    g.stroke();
                    g.globalAlpha = 1;
                    if (ch === F.channel) for (const p of pts) { const [px, py] = toCanvas(p); g.fillStyle = colour[ch]; g.beginPath(); g.arc(px, py, 4.5, 0, Math.PI * 2); g.fill(); }
                }
            }
            let drag = null;
            const at = (e) => { const r = c.getBoundingClientRect(); return [(e.clientX - r.left) * size / r.width, (e.clientY - r.top) * size / r.height]; };
            const near = (px, py) => F.curves[F.channel].findIndex((p) => { const [x, y] = toCanvas(p); return Math.hypot(x - px, y - py) < 9; });
            c.addEventListener('pointerdown', (e) => {
                const [px, py] = at(e);
                let i = near(px, py);
                if (i < 0) {
                    if (F.curves[F.channel].length >= 8) return say(T('At most 8 points a curve'));
                    F.curves[F.channel].push(toValue(px, py));
                    i = F.curves[F.channel].length - 1;
                    change();
                }
                drag = i;
                c.setPointerCapture(e.pointerId);
                paint();
            });
            c.addEventListener('pointermove', (e) => {
                if (drag == null) return;
                F.curves[F.channel][drag] = toValue(...at(e));
                paint();
                change();
            });
            c.addEventListener('pointerup', () => { drag = null; });
            c.addEventListener('dblclick', (e) => {
                const i = near(...at(e));
                if (i >= 0) { F.curves[F.channel].splice(i, 1); paint(); change(); }
            });
            paint();
            return hh('div', {class: 'im-curve-box'}, tabs, c, hh('div', {class: 'fx-dim'}, T('Click to add a point, drag it, double-click to take it away')));
        }

        /* ---- preview, apply, cancel ---- */
        function params() {
            if (F.effect === 'curve') return {all: F.curves.all, r: F.curves.r, g: F.curves.g, b: F.curves.b};
            return F.values[F.effect];
        }
        function change() {
            if (!live.checked || F.effect === 'flip') return;
            clearTimeout(F.timer);
            F.timer = setTimeout(() => preview().catch((e) => say(e.message)), 180);
        }
        async function begin() {
            if (F.state && IM.hasLayer(F.state.id) && IM.doc().active === F.state.id) return F.state;
            if (F.state) IM.effect.cancel(F.state);
            F.state = IM.effect.begin();
            if (!F.state) throw new Error(T('Choose a layer first'));
            F.blob = await new Promise((r) => F.state.flat.toBlob(r, 'image/png'));
            return F.state;
        }
        async function run() {
            if (F.installed === false) throw new Error(T('HakuImg is not installed'));
            const st = await begin();
            const token = ++F.token;
            const form = new FormData();
            form.append('picture', F.blob, 'layer.png');
            form.append('effect', F.effect);
            form.append('params', JSON.stringify(params()));
            const t0 = performance.now();
            say(T('HakuImg is working…'));
            const r = await fetch('/forge-studio/image/haku', {method: 'POST', body: form});
            if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
            const url = URL.createObjectURL(await r.blob());
            const img = await new Promise((ok, bad) => { const im = new Image(); im.onload = () => ok(im); im.onerror = () => bad(new Error(T('Could not read the picture'))); im.src = url; });
            URL.revokeObjectURL(url);
            if (token !== F.token || F.state !== st) return null;   // a newer change, or cancelled meanwhile
            F.result = img;
            say(T('{0} · {1} s · Apply keeps it', T(EFFECTS.find((e) => e[0] === F.effect)[2]), ((performance.now() - t0) / 1000).toFixed(2)));
            return img;
        }
        async function preview() {
            const img = await run();
            if (img && F.state) IM.effect.preview(F.state, img);
        }
        async function commit() {
            const img = F.result && F.state ? F.result : await run();
            if (!img || !F.state) return;
            IM.effect.commit(F.state, img, T(EFFECTS.find((e) => e[0] === F.effect)[2]));
            F.state = null;
            F.result = null;
            F.blob = null;
            F.token++;
            say(T('Applied (Ctrl+Z undoes it)'));
        }
        function stop(message) {
            clearTimeout(F.timer);
            F.token++;
            if (F.state && IM.hasLayer(F.state.id)) IM.effect.cancel(F.state);
            F.state = null;
            F.result = null;
            F.blob = null;
            if (message) say(message);
        }
        IM.stopEffect = stop;

        // the preview is not the layer: drawing, saving or closing first put the layer back
        const area = IM.ui && IM.ui.area;
        if (area) area.addEventListener('pointerdown', () => { if (F.state) stop(T('The preview was put away (Apply keeps an effect)')); }, true);

        FS.api('image/haku-info').then((info) => {
            F.installed = !!info.installed;
            note.textContent = info.installed ? T('On the active layer, inside the selection when there is one.') :
                T('HakuImg is not installed: Extensions → Install from URL → https://github.com/KohakuBlueleaf/a1111-sd-webui-haku-img, then restart Forge.');
            group.classList.toggle('im-fx-off', !info.installed);
        }).catch(() => { note.textContent = ''; });
        draw();
    }

    /* the curve as the page draws it: a natural cubic spline through (0,0), the points and (255,255) */
    function spline(points) {
        const pts = [[0, 0], ...points.slice().sort((a, b) => a[0] - b[0]), [255, 255]].filter((p, i, a) => !i || p[0] !== a[i - 1][0]);
        if (pts.length === 2) return (x) => x;
        const n = pts.length, xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]), hs = [], al = [0], l = [1], mu = [0], z = [0];
        for (let i = 0; i < n - 1; i++) hs.push(xs[i + 1] - xs[i]);
        for (let i = 1; i < n - 1; i++) al.push(3 / hs[i] * (ys[i + 1] - ys[i]) - 3 / hs[i - 1] * (ys[i] - ys[i - 1]));
        for (let i = 1; i < n - 1; i++) { l[i] = 2 * (xs[i + 1] - xs[i - 1]) - hs[i - 1] * mu[i - 1]; mu[i] = hs[i] / l[i]; z[i] = (al[i] - hs[i - 1] * z[i - 1]) / l[i]; }
        const c = new Array(n).fill(0), b = [], d = [];
        for (let j = n - 2; j >= 0; j--) { c[j] = (z[j] || 0) - (mu[j] || 0) * c[j + 1]; b[j] = (ys[j + 1] - ys[j]) / hs[j] - hs[j] * (c[j + 1] + 2 * c[j]) / 3; d[j] = (c[j + 1] - c[j]) / (3 * hs[j]); }
        return (x) => {
            let i = n - 2;
            while (i > 0 && x < xs[i]) i--;
            const dx = x - xs[i];
            return Math.max(0, Math.min(255, ys[i] + b[i] * dx + c[i] * dx * dx + d[i] * dx * dx * dx));
        };
    }

    // saving and closing the workspace put a previewed layer back first
    for (const name of ['save', 'close', 'toVideo']) {
        const original = IM[name];
        if (typeof original !== 'function') continue;
        IM[name] = function (...args) { if (IM.stopEffect) IM.stopEffect(); return original.apply(this, args); };
    }

    IM.panels.push(panel);
})();

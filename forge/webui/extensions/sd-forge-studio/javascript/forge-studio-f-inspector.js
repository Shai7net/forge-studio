/* Forge Studio - the inspector: what the selected clip is and everything that can be changed in it (a slider and a
 * number for each value; a whole drag of a slider is one undo step), its AI actions, and with nothing selected the
 * edit's own settings.
 */
(function () {
    'use strict';
    const FS = window.ForgeStudio = window.ForgeStudio || {};
    const S = FS.state;
    const T = (...a) => FS.T(...a);
    const val = (c, k) => FS.val(c, k);
    const I = FS.inspector = {};

    /* a value of the selected clip: slider + number, live while dragged, one undo step for the whole drag */
    function field(label, clipId, key, opt) {
        const h = FS.h;
        // a value that can move (opt.keyed) shows its value at the playhead, and a change there becomes a key once it has any
        const get = () => {
            const c = FS.clip(clipId);
            const v = opt.keyed ? FS.at(c, key) : opt.get ? opt.get(c) : val(c, key);
            return opt.show ? opt.show(v) : v;
        };
        const set = (shown) => {
            const c = FS.clip(clipId);
            // a typed number stays inside the field's range (a size of 60 once made an export draw a picture 60 times over)
            if (!Number.isFinite(shown)) return;
            shown = Math.max(opt.min, Math.min(opt.max, shown));
            const v = opt.store ? opt.store(shown) : shown;
            // a keyed value without keys yet goes through its own `set` (the speed changes the clip's length there)
            if (opt.keyed && (FS.keysOf(c, key) || !opt.set)) FS.setAt(c, key, v); else if (opt.set) opt.set(c, v); else c[key] = v;
        };
        const range = h('input', {type: 'range', min: opt.min, max: opt.max, step: opt.step, value: get(), class: 'fx-range'});
        const num = h('input', {type: 'number', min: opt.min, max: opt.max, step: opt.step, value: round(get(), opt.step), class: 'fx-num'});
        let editing = false;
        const begin = () => { if (!editing) { editing = true; FS.beginEdit(); } };
        const end = () => { if (editing) { editing = false; FS.commitEdit(label); } };
        range.addEventListener('pointerdown', begin);
        range.addEventListener('input', () => { begin(); set(+range.value); num.value = round(+range.value, opt.step); FS.liveEdit(); });
        range.addEventListener('change', end);
        num.addEventListener('focus', begin);
        num.addEventListener('input', () => { if (num.value === '') return; begin(); set(+num.value); range.value = num.value; FS.liveEdit(); });
        num.addEventListener('change', () => { num.value = round(Math.max(opt.min, Math.min(opt.max, +num.value)), opt.step); end(); });
        num.addEventListener('blur', end);
        const reset = h('button', {class: 'fx-icon fx-reset', title: T('Back to the start value'), onclick: () => {
            FS.change(label, () => {
                const c = FS.clip(clipId);
                if (opt.keyed && c.keys) delete c.keys[key];
                if (opt.set) opt.set(c, opt.reset); else delete c[key];
            });
        }}, '↺');
        const diamond = opt.keyed ? h('button', {class: 'fx-icon fx-key', title: T('A keyframe at the playhead (the value moves between keyframes)'),
            onclick: () => FS.toggleKey(clipId, key)}, '◆') : null;
        const refresh = () => {
            const c = FS.clip(clipId);
            if (!c || editing) return;
            range.value = get();
            num.value = round(get(), opt.step);
            if (diamond) diamond.className = 'fx-icon fx-key' + (FS.keyIndex(c, key) >= 0 ? ' fx-key-on' : FS.keysOf(c, key) ? ' fx-key-has' : '');
        };
        I.live.push(refresh);
        refresh();
        return h('div', {class: 'fx-prop' + (diamond ? ' fx-prop-keyed' : '')}, h('span', {class: 'fx-prop-label'}, T(label)), range, num, diamond, reset);
    }

    /* the clip's movement: jump between its keyframes, how it moves, and ready-made moves for a still or a shot */
    function motion(c) {
        const h = FS.h;
        const times = () => {
            const x = FS.clip(c.id);
            const all = new Set();
            for (const k of FS.KEYED) for (const [t] of FS.keysOf(x, k) || []) all.add(Math.round(t * 1000) / 1000);
            return [...all].sort((a, b) => a - b).map((t) => x.start + t);
        };
        const jump = (dir) => () => {
            const ts = times(), eps = 0.5 / FS.fps();
            const t = dir > 0 ? ts.find((v) => v > S.time + eps) : [...ts].reverse().find((v) => v < S.time - eps);
            if (t != null) FS.seek(t);
        };
        const ease = h('select', {class: 'fx-select'}, h('option', {value: 'smooth', selected: c.keyEase !== 'linear'}, T('Smooth (eases in and out)')),
            h('option', {value: 'linear', selected: c.keyEase === 'linear'}, T('Even')));
        ease.addEventListener('change', () => FS.change('motion', () => { FS.clip(c.id).keyEase = ease.value; }));
        const preset = h('select', {class: 'fx-select'}, h('option', {value: ''}, T('A ready-made move…')),
            ...MOVES.map(([id, label]) => h('option', {value: id}, T(label))));
        preset.addEventListener('change', () => { if (preset.value) FS.move(c.id, preset.value); preset.value = ''; });
        const count = times().length;
        return section('Motion',
            h('div', {class: 'fx-row'},
                h('button', {class: 'fx-icon', title: T('The keyframe before'), onclick: jump(-1)}, '◀◆'),
                h('button', {class: 'fx-icon', title: T('The keyframe after'), onclick: jump(1)}, '◆▶'),
                h('span', {class: 'fx-dim'}, count ? T('{0} keyframes', count) : T('No keyframes: press ◆ next to a value')),
                count ? h('button', {class: 'fx-icon', title: T('Remove all the movement'), onclick: () => FS.change('no motion', () => {
                    const x = FS.clip(c.id);
                    for (const k of FS.KEYED) if (FS.keysOf(x, k)) x[k] = FS.at(x, k);
                    delete x.keys;
                })}, '✕') : null),
            preset, ease);
    }

    const MOVES = [['in', 'Slow zoom in'], ['out', 'Slow zoom out'], ['right', 'Pan to the right'], ['left', 'Pan to the left'],
        ['up', 'Pan up'], ['down', 'Pan down'], ['rise', 'Rise in from below'], ['spin', 'Turn in']];

    /* a ready-made move over the whole clip; it replaces the movement of the values it uses */
    FS.move = (clipId, id) => FS.change('motion', () => {
        const c = FS.clip(clipId);
        const {width: W, height: H} = S.doc.settings;
        const info = S.info[c.media] || {};
        const mw = c.kind === 'text' || !info.width ? W : info.width, mh = c.kind === 'text' || !info.height ? H : info.height;
        const cover = Math.max(W / mw, H / mh) / Math.min(W / mw, H / mh);   // the size at which it fills the frame
        const d = c.duration, s0 = FS.at(c, 'scale', c.start), x0 = FS.at(c, 'x', c.start), y0 = FS.at(c, 'y', c.start);
        const big = Math.max(s0, cover) * 1.12;
        const slackX = Math.max(0.02, (mw * Math.min(W / mw, H / mh) * big - W) / 2 / W);
        const slackY = Math.max(0.02, (mh * Math.min(W / mw, H / mh) * big - H) / 2 / H);
        const keys = {
            in: {scale: [[0, s0], [d, s0 * 1.2]]},
            out: {scale: [[0, s0 * 1.2], [d, s0]]},
            right: {scale: [[0, big]], x: [[0, slackX], [d, -slackX]]},
            left: {scale: [[0, big]], x: [[0, -slackX], [d, slackX]]},
            up: {scale: [[0, big]], y: [[0, -slackY], [d, slackY]]},
            down: {scale: [[0, big]], y: [[0, slackY], [d, -slackY]]},
            rise: {y: [[0, y0 + 0.25], [Math.min(d, 0.8), y0]], opacity: [[0, 0], [Math.min(d, 0.8), FS.at(c, 'opacity', c.start)]]},
            spin: {rotation: [[0, -12], [Math.min(d, 1), 0]], scale: [[0, s0 * 0.6], [Math.min(d, 1), s0]]},
        }[id];
        if (!keys) return;
        c.keys = Object.assign(c.keys || {}, keys);
        for (const k of Object.keys(keys)) if (keys[k].length === 1) { c[k] = keys[k][0][1]; delete c.keys[k]; }
        if (id === 'right' || id === 'left') { c.y = y0; if (c.keys.y) delete c.keys.y; }
        if (id === 'up' || id === 'down') { c.x = x0; if (c.keys.x) delete c.keys.x; }
    });
    const round = (v, step) => { const d = String(step).includes('.') ? String(step).split('.')[1].length : 0; return (+v).toFixed(d); };

    function section(title, ...children) {
        return FS.h('div', {class: 'fx-section'}, FS.h('div', {class: 'fx-section-title'}, T(title)), ...children);
    }

    function textEditor(c) {
        const h = FS.h;
        const t = Object.assign({}, FS.TEXT_DEFAULTS, c.text || {});
        const commit = (patch, label) => FS.change(label || 'text', () => { const x = FS.clip(c.id); x.text = Object.assign({}, FS.TEXT_DEFAULTS, x.text || {}, patch); });
        const live = (key) => (e) => {
            const x = FS.clip(c.id);
            x.text = Object.assign({}, FS.TEXT_DEFAULTS, x.text || {}, {[key]: e.target.type === 'checkbox' ? e.target.checked : e.target.type === 'range' || e.target.type === 'number' ? +e.target.value : e.target.value});
            FS.liveEdit();
        };
        const area = h('textarea', {class: 'fx-input fx-textarea', dir: 'auto', rows: 3}, t.text);
        area.addEventListener('focus', () => FS.beginEdit());
        area.addEventListener('input', live('text'));
        area.addEventListener('blur', () => FS.commitEdit('text'));
        const sub = h('input', {class: 'fx-input', dir: 'auto', value: t.sub, placeholder: T('Second line, smaller (optional)')});
        sub.addEventListener('focus', () => FS.beginEdit());
        sub.addEventListener('input', live('sub'));
        sub.addEventListener('blur', () => FS.commitEdit('text'));
        const sel = (key, options) => {
            const s = h('select', {class: 'fx-select'}, ...options.map(([v, l]) => h('option', {value: v, selected: v === t[key]}, l)));
            s.addEventListener('change', () => commit({[key]: s.value}));
            return s;
        };
        const color = (key) => {
            const i = h('input', {type: 'color', value: t[key], class: 'fx-swatch'});
            i.addEventListener('focus', () => FS.beginEdit());
            i.addEventListener('input', live(key));
            i.addEventListener('change', () => FS.commitEdit('colour'));
            return i;
        };
        const check = (key, label) => {
            const i = h('input', {type: 'checkbox', checked: !!t[key]});
            i.addEventListener('change', () => commit({[key]: i.checked}));
            return h('label', {class: 'fx-check'}, i, ' ', T(label));
        };
        const slide = (key, label, min, max, step) => {
            const r = h('input', {type: 'range', min, max, step, value: t[key], class: 'fx-range'});
            r.addEventListener('pointerdown', () => FS.beginEdit());
            r.addEventListener('input', live(key));
            r.addEventListener('change', () => FS.commitEdit(label));
            return h('div', {class: 'fx-prop'}, h('span', {class: 'fx-prop-label'}, T(label)), r);
        };
        if (t.template === 'shape') {
            return section('Shape',
                sel('shape', FS.SHAPES.map((v) => [v, T(SHAPE_NAMES[v])])),
                slide('size', 'Size', 1, 60, 0.5),
                slide('aspect', 'Width', 0.2, 6, 0.05),
                h('div', {class: 'fx-row fx-wrap'}, h('label', {class: 'fx-check'}, color('color'), ' ', T('Colour')), check('fill', 'Filled'), check('shadow', 'Shadow')),
                t.fill && t.shape !== 'line' ? null : slide('thick', 'Thickness', 0.5, 30, 0.5));
        }
        return section('Text', area, sub,
            h('div', {class: 'fx-row'}, sel('template', FS.TEXT_TEMPLATES.map(([v, l]) => [v, T(l)])), sel('font', FS.FONTS.map((f) => [f, f]))),
            slide('size', 'Size', 1.5, 14, 0.25),
            h('div', {class: 'fx-row fx-wrap'}, h('label', {class: 'fx-check'}, color('color'), ' ', T('Colour')), check('shadow', 'Shadow'), check('box', 'Box behind'), color('boxColor')),
            slide('outline', 'Outline', 0, 8, 0.5),
            slide('boxAlpha', 'Box', 0.1, 1, 0.05));
    }

    function clipInspector(c) {
        const h = FS.h;
        const out = [];
        const kindName = {video: 'Video', image: 'Picture', audio: 'Sound', text: 'Text', gen: 'Being made'}[c.kind] || c.kind;
        const info = S.info[c.media] || {};
        out.push(h('div', {class: 'fx-ins-head'}, h('b', {}, T(kindName)),
            h('span', {class: 'fx-dim fx-ellipsis', dir: 'ltr'}, c.media || ''),
            h('span', {class: 'fx-dim', dir: 'ltr'}, `${FS.timecode(c.start)} → ${FS.timecode(FS.end(c))}`)), styleTools(c));
        if (c.kind === 'text') out.push(textEditor(c));
        if (c.kind !== 'audio') {
            out.push(section('Picture',
                field('Size', c.id, 'scale', {keyed: true, min: 0.05, max: 4, step: 0.01, reset: 1, set: (x, v) => { x.scale = v; }}),
                field('Left / right', c.id, 'x', {keyed: true, min: -1, max: 1, step: 0.005, reset: 0, set: (x, v) => { x.x = v; }}),
                field('Up / down', c.id, 'y', {keyed: true, min: -1, max: 1, step: 0.005, reset: 0, set: (x, v) => { x.y = v; }}),
                field('Turn', c.id, 'rotation', {keyed: true, min: -180, max: 180, step: 0.5, reset: 0, set: (x, v) => { x.rotation = v; }}),
                field('Opacity', c.id, 'opacity', {keyed: true, min: 0, max: 100, step: 1, reset: 1, show: (v) => Math.round(v * 100), store: (v) => v / 100, set: (x, v) => { x.opacity = v; }})));
            out.push(motion(c));
            out.push(animation(c));
        }
        if (c.kind === 'video' || c.kind === 'image') {
            const pct = (key, label, max) => field(label, c.id, key, {min: 0, max, step: 1, reset: 1, show: (v) => Math.round(v * 100),
                store: (v) => v / 100, set: (x, v) => { x[key] = v; }});
            const shade = (key, label) => field(label, c.id, key, {min: -100, max: 100, step: 1, reset: 0, show: (v) => Math.round(v * 100),
                store: (v) => v / 100, set: (x, v) => { x[key] = v; }});
            out.push(section('Colour', looks(c), pct('brightness', 'Brightness', 200), pct('contrast', 'Contrast', 200), pct('saturation', 'Saturation', 300),
                shade('warmth', 'Warmth'), shade('tint', 'Tint (green / magenta)')));
            // a side and its opposite leave at least 5% of the picture between them
            const edge = (key, other, label) => field(label, c.id, key, {min: 0, max: 95, step: 1, reset: 0, show: (v) => Math.round(v * 100),
                store: (v) => v / 100, set: (x, v) => { const room = 0.95 - val(x, other); if (v > 0) x[key] = Math.min(v, Math.max(0, room)); else delete x[key]; }});
            out.push(chromaKey(c));
            out.push(fillTools(c));
            const shape = h('select', {class: 'fx-select'}, ...[['', 'No shape'], ['circle', 'Circle'], ['rounded', 'Rounded corners']]
                .map(([v, l]) => h('option', {value: v, selected: (c.mask || '') === v}, '◯ ' + T(l))));
            shape.addEventListener('change', () => FS.change('shape', () => { const x = FS.clip(c.id); if (shape.value) x.mask = shape.value; else delete x.mask; }));
            const part = (key, label, max, reset) => field(label, c.id, key, {min: 0, max, step: 1, reset, show: (v) => Math.round(v * 100),
                get: (x) => (x[key] == null ? reset : x[key]), store: (v) => v / 100, set: (x, v) => { x[key] = v; }});
            out.push(section('Crop and shape', edge('cropL', 'cropR', 'Left'), edge('cropR', 'cropL', 'Right'), edge('cropT', 'cropB', 'Top'),
                edge('cropB', 'cropT', 'Bottom'), shape,
                c.mask === 'rounded' ? part('maskRound', 'Corners', 50, 0.15) : null,
                c.mask ? part('maskSoft', 'Soft edge', 25, 0) : null));
        }
        const before = c.kind !== 'audio' ? FS.clipBefore(c) : null;
        out.push(section('Time',
            before ? transitionKind(c) : null,
            before ? field('Transition from the clip before (s)', c.id, 'transitionIn', {min: 0, max: Math.min(3, before.duration / 2, c.duration / 2), step: 0.05, reset: 0, get: (x) => x.transitionIn || 0,
                set: (x, v) => { if (v > 0) x.transitionIn = v; else { delete x.transitionIn; delete x.transition; } }}) : null,
            soundCrossField(c),
            field('Fade in (s)', c.id, 'fadeIn', {min: 0, max: Math.min(5, c.duration), step: 0.05, reset: 0, set: (x, v) => { x.fadeIn = v; }}),
            field('Fade out (s)', c.id, 'fadeOut', {min: 0, max: Math.min(5, c.duration), step: 0.05, reset: 0, set: (x, v) => { x.fadeOut = v; }}),
            c.kind === 'video' || c.kind === 'audio' ? rampPick(c) : null,
            c.kind === 'video' || c.kind === 'audio' ? field('Speed', c.id, 'speed', {keyed: true, min: 0.1, max: 8, step: 0.05, reset: 1,
                set: (x, v) => FS.applySpeed(x, v)}) : null,
            c.kind !== 'video' && c.kind !== 'audio' ? field('Length (s)', c.id, 'duration', {min: 0.2, max: 60, step: 0.1, reset: 5,
                get: (x) => x.duration, set: (x, v) => { x.duration = v; FS.closeGaps(); }}) : null,
            c.kind === 'video' ? timeTools(c) : null));
        if (c.kind === 'audio' || (c.kind === 'video' && info.audio)) {
            const mute = h('input', {type: 'checkbox', checked: !!c.muted});
            mute.addEventListener('change', () => FS.change('mute', () => { FS.clip(c.id).muted = mute.checked; }));
            out.push(section('Sound',
                field('Volume', c.id, 'volume', {keyed: true, min: 0, max: 200, step: 1, reset: 1, show: (v) => Math.round(v * 100), store: (v) => v / 100, set: (x, v) => { x.volume = v; }}),
                h('label', {class: 'fx-check'}, mute, ' ', T('Mute this clip')),
                c.kind === 'video' && !c.muted ? h('button', {class: 'fx-ai-btn', title: T('The sound becomes a clip of its own below, and the video goes quiet'),
                    onclick: () => { FS.detachAudio(c.id); FS.message(T('The sound is a clip of its own now')); }}, '🔊 ' + T('Separate the sound')) : null,
                duckTools(c), beatTools(c)));
        }
        const actions = FS.clipActions.filter((a) => !a.when || a.when(c));
        if (actions.length) {
            out.push(section('AI', h('div', {class: 'fx-ai-actions'},
                ...actions.map((a) => h('button', {class: 'fx-ai-btn', onclick: FS.guard(() => a.run(c))}, a.label)))));
        }
        return out;
    }

    /* 🎨 a ready-made look (FS.LOOKS) for this clip, or the same look on every picture of the edit */
    const LOOK_NAMES = {'': 'My own colour', none: 'Natural', warm: 'Warm', cool: 'Cool', bw: 'Black and white', vintage: 'Vintage',
        film: 'Film', vivid: 'Vivid', night: 'Night'};
    function looks(c) {
        const h = FS.h;
        const now = FS.lookOf(c);
        const pick = h('select', {class: 'fx-select'}, ...Object.keys(LOOK_NAMES).filter((k) => k || !now)
            .map((k) => h('option', {value: k, selected: k === now, disabled: !k}, '🎨 ' + T(LOOK_NAMES[k]))));
        pick.addEventListener('change', () => FS.applyLook([c.id], pick.value));
        const all = h('button', {class: 'fx-ai-btn', title: T('Every video and picture in the edit gets the colour of this clip'),
            onclick: () => {
                const ids = S.doc.clips.filter((x) => x.kind === 'video' || x.kind === 'image').map((x) => x.id);
                FS.change('colour for all', () => {
                    for (const id of ids) { const x = FS.clip(id); for (const key of FS.COLOUR) { if (c[key] == null) delete x[key]; else x[key] = c[key]; } }
                });
                FS.message(T('{0} clips have this colour now', ids.length));
            }}, T('The same colour on every shot'));
        return h('div', {class: 'fx-duck'}, pick, all);
    }

    /* 🌫 a blurred copy behind a picture that does not fill the frame (FS.fillOf), here or on every such shot */
    function fillTools(c) {
        const h = FS.h;
        const pick = h('select', {class: 'fx-select'}, ...[['', 'Nothing behind it'], ['blur', 'A blurred copy behind it']]
            .map(([v, l]) => h('option', {value: v, selected: (c.fill || '') === v}, '🌫 ' + T(l))));
        pick.addEventListener('change', () => FS.change('fill', () => { const x = FS.clip(c.id); if (pick.value) x.fill = pick.value; else { delete x.fill; delete x.fillBlur; } }));
        const all = h('button', {class: 'fx-ai-btn', title: T('Every video and picture whose shape is not the frame\'s gets a blurred copy behind it'), onclick: FS.guard(async () => {
            // the shape of every file first (right after a project opens, some are not known yet)
            await Promise.all(S.doc.clips.filter((x) => (x.kind === 'video' || x.kind === 'image') && x.media && !(S.info[x.media] || {}).width)
                .map((x) => FS.probe(x.media).catch(() => null)));
            const {width: W, height: H} = S.doc.settings;
            const n = FS.change('fill all', () => {
                let k = 0;
                for (const x of S.doc.clips) {
                    const info = S.info[x.media];
                    if ((x.kind !== 'video' && x.kind !== 'image') || !info || !info.width) continue;
                    if (Math.abs(info.width / info.height - W / H) < 0.02) continue;   // fills the frame already
                    x.fill = 'blur';
                    k++;
                }
                return k;
            });
            FS.message(T('{0} shots have a blurred copy behind them', n));
        })}, T('On every shot that does not fill the frame'));
        return section('Background', pick,
            c.fill ? field('Blur', c.id, 'fillBlur', {min: 0.5, max: 10, step: 0.5, reset: 3, show: (v) => +(v * 100).toFixed(1),
                get: (x) => (x.fillBlur == null ? 0.03 : x.fillBlur), store: (v) => v / 100, set: (x, v) => { x.fillBlur = v; }}) : null,
            all);
    }

    /* 🟩 chroma key: a colour taken away (green by default), picked in the picture if wanted */
    function chromaKey(c) {
        const h = FS.h;
        const on = h('input', {type: 'checkbox', checked: !!c.key});
        const colour = h('input', {type: 'color', class: 'fx-swatch', value: c.key || '#00ff00'});
        const setKey = (v) => FS.change('chroma key', () => { const x = FS.clip(c.id); if (v) x.key = v; else delete x.key; });
        on.addEventListener('change', () => setKey(on.checked ? colour.value : null));
        colour.addEventListener('change', () => setKey(colour.value));
        const pick = h('button', {class: 'fx-ai-btn', title: T('Click the colour to take away in the picture')}, '🎯 ' + T('Pick it in the picture'));
        pick.addEventListener('click', () => {
            FS.player.picking = (hex) => setKey(hex);
            const cv = FS.player.ctx && FS.player.ctx.canvas;
            if (cv) cv.style.cursor = 'crosshair';
            FS.message(T('Click the colour to take away in the picture'));
        });
        const part = (key, label, max, reset) => field(label, c.id, key, {min: 0, max, step: 1, reset, show: (v) => Math.round(v * 100),
            get: (x) => (x[key] == null ? reset : x[key]), store: (v) => v / 100, set: (x, v) => { x[key] = v; }});
        return section('Chroma key', h('div', {class: 'fx-row'}, h('label', {class: 'fx-check'}, on, ' ', T('Take a colour away')), colour), pick,
            c.key ? part('keySimilar', 'Similar colours', 100, 0.3) : null,
            c.key ? part('keySoft', 'Soft edge', 50, 0.1) : null,
            c.key ? part('keySpill', 'Remove the colour cast', 100, 0.6) : null);
    }

    /* 🏎 a ready-made speed ramp (FS.RAMPS); the ◆ next to Speed makes one by hand */
    const RAMP_NAMES = {'': 'Steady speed', hero: 'Hero (fast, slow, fast)', slow: 'Slowing down', fast: 'Speeding up',
        flash: 'Flash (slow, fast, slow)', montage: 'Montage'};
    function rampPick(c) {
        const h = FS.h;
        const now = FS.ramped(c) ? (Object.keys(FS.RAMPS).find((k) => JSON.stringify(c.keys.speed.map(([t]) => +(t / c.duration).toFixed(3)))
            === JSON.stringify(FS.RAMPS[k].map(([u]) => u))) || 'own') : '';
        const pick = h('select', {class: 'fx-select', title: T('The clip keeps its length; the speed changes inside it')},
            ...Object.keys(RAMP_NAMES).concat(now === 'own' ? ['own'] : []).map((k) => h('option', {value: k, selected: k === now, disabled: k === 'own'},
                '🏎 ' + T(k === 'own' ? 'My own ramp' : RAMP_NAMES[k]))));
        pick.addEventListener('change', () => FS.applyRamp(c.id, pick.value));
        return pick;
    }

    /* ❄ a freeze frame at the playhead, ⏪ the clip backwards (and forwards again) */
    I.freezeSeconds = 2;
    function timeTools(c) {
        const h = FS.h;
        const seconds = h('input', {type: 'number', class: 'fx-num', min: 0.2, max: 30, step: 0.1, value: I.freezeSeconds, title: T('Seconds')});
        seconds.addEventListener('change', () => { I.freezeSeconds = Math.max(0.2, +seconds.value || 2); });
        const freeze = h('button', {class: 'fx-ai-btn', title: T('The frame at the playhead stays on the screen; the rest of the clip comes after it'),
            onclick: FS.guard(async () => {
                const clip = FS.clip(c.id);
                const t = Math.min(Math.max(S.time, clip.start), FS.end(clip) - 1 / FS.fps());
                const r = await FS.api('clip/still', {name: S.project, web: clip.media, seconds: FS.sourceTime(clip, t)});
                S.media[r.web] = {path: r.path, missing: false};
                await FS.probe(r.web);
                FS.freezeAt(clip.id, t, r.web, I.freezeSeconds);
                FS.message(T('A freeze frame of {0} s', I.freezeSeconds));
            })}, '❄ ' + T('Freeze frame at the playhead'));
        const back = h('button', {class: 'fx-ai-btn', onclick: FS.guard(async () => {
            back.disabled = true;
            try { await FS.reverseClip(c.id, (t) => FS.busy && FS.busy(T('Playing it backwards… {0}', t.text || ''))); }
            finally { back.disabled = false; FS.busy && FS.busy(null); }
        })}, c.reversed ? '⏩ ' + T('Play it forwards again') : '⏪ ' + T('Play it backwards'));
        return h('div', {class: 'fx-duck'}, h('div', {class: 'fx-row'}, freeze, seconds), back);
    }

    /* ⇄: how the clip comes in from the clip before it (FS.TRANSITIONS); choosing one without a length gives it a second */
    const TRANSITION_NAMES = {'': 'No transition', dissolve: 'Dissolve', black: 'Dip to black', white: 'Dip to white',
        'slide:right': 'Slide in from the right', 'slide:left': 'Slide in from the left', 'slide:top': 'Slide in from the top',
        'slide:bottom': 'Slide in from the bottom', 'push:right': 'Push from the right', 'push:left': 'Push from the left',
        'push:top': 'Push from the top', 'push:bottom': 'Push from the bottom'};
    FS.transitionName = (c) => FS.T(TRANSITION_NAMES[c.transitionIn > 0 ? c.transition || 'dissolve' : '']);
    function transitionKind(c) {
        const h = FS.h;
        const now = c.transitionIn > 0 ? c.transition || 'dissolve' : '';
        const pick = h('select', {class: 'fx-select'}, ...['', ...FS.TRANSITIONS].map((k) => h('option', {value: k, selected: k === now}, '⇄ ' + T(TRANSITION_NAMES[k]))));
        pick.addEventListener('change', () => FS.transition(c.id, pick.value ? c.transitionIn || (pick.value.includes(':') ? 0.6 : 1) : 0, pick.value));
        return pick;
    }

    /* 🔉 the sound's crossfade with the clip before (FS.soundCross): follows the picture transition until set */
    function soundCrossField(c) {
        const prev = FS.clipBefore(c);
        if (!prev || !FS.sounds(c) || !FS.sounds(prev)) return null;
        const f = field('Soft sound from the clip before (s)', c.id, 'soundIn', {min: 0, max: Math.min(3, c.duration), step: 0.05, reset: null,
            get: (x) => (x.soundIn != null ? x.soundIn : x.transitionIn || 0),
            set: (x, v) => { if (v == null) delete x.soundIn; else x.soundIn = Math.max(0, v); }});
        const out = FS.soundOut(prev);
        const note = FS.soundCross(c) > 0 && !(out.tail > 0) ? T('The clip before has no more sound in its file: it fades out before the cut')
            : c.soundIn == null && c.transitionIn > 0 ? T('As long as the picture transition, until you set it') : '';
        return FS.h('div', {title: T('The two sounds cross: the one before goes on from its file and fades out while this one fades in, without a dip')},
            f, note ? FS.h('div', {class: 'fx-dim'}, note) : null);
    }

    /* 🗣: this clip is speech (music steps back under it), or this clip steps back under speech */
    function duckTools(c) {
        const h = FS.h;
        const speech = h('input', {type: 'checkbox', checked: FS.isSpeech(c), disabled: c.duck > 0});
        speech.addEventListener('change', () => FS.change('speech', () => { FS.clip(c.id).speech = speech.checked; }));
        const levels = [[0, 'Not lowered under speech'], [0.5, 'Lowered a little under speech (−6 dB)'],
            [0.25, 'Lowered under speech (−12 dB)'], [0.125, 'Lowered a lot under speech (−18 dB)']];
        const duck = h('select', {class: 'fx-select', title: T('Only while someone is really talking; it comes back up in the pauses')},
            ...levels.map(([v, l]) => h('option', {value: v, selected: Math.abs(v - (c.duck || 0)) < 1e-6}, T(l))));
        duck.addEventListener('change', () => FS.change('duck', () => {
            const x = FS.clip(c.id);
            if (+duck.value > 0) { x.duck = +duck.value; delete x.speech; } else delete x.duck;
        }));
        const out = [h('label', {class: 'fx-check', title: T('Music marked to be lowered steps back while this clip talks')}, speech, ' 🗣 ', T('This is speech')), duck];
        if (c.duck > 0) {
            const n = FS.speechClips().length;
            out.push(h('div', {class: 'fx-dim'}, n === 1 ? T('One speech clip') : n ? T('{0} speech clips', n) : T('No speech clips yet: narration made here counts by itself, or tick "This is speech" on a clip')));
        }
        return h('div', {class: 'fx-duck'}, ...out);
    }

    /* 🥁: the music's beats on the clip, edges snap to them, and the main track cut to them */
    function beatTools(c) {
        const h = FS.h;
        if (!c.beats) {
            return h('button', {class: 'fx-ai-btn', onclick: FS.guard(async () => {
                FS.busy && FS.busy(T('Listening for the beats…'));
                try { await FS.loadBeats(c.media); } finally { FS.busy && FS.busy(null); }
                FS.change('beats', () => { FS.clip(c.id).beats = true; });
            })}, '🥁 ' + T('Find the beats'));
        }
        const b = FS.beats[c.media];
        if (!b) { FS.loadBeats(c.media).then(() => I.render()).catch(() => null); return h('div', {class: 'fx-dim'}, T('Listening for the beats…')); }
        const every = h('select', {class: 'fx-select'}, ...[[0, 'On the nearest beat'], [1, 'A shot every beat'], [2, 'A shot every two beats'],
            [4, 'A shot every bar (4 beats)']].map(([v, l]) => h('option', {value: v, selected: v === (I.beatEvery || 0)}, T(l))));
        every.addEventListener('change', () => { I.beatEvery = +every.value; });
        return h('div', {class: 'fx-beats'},
            h('div', {class: 'fx-row'}, h('span', {}, '🥁 ' + T('{0} beats a minute · {1} beats', Math.round(b.tempo), FS.beatTimes(c).length)),
                h('button', {class: 'fx-icon', title: T('Hide the beats'), onclick: () => FS.change('beats', () => { delete FS.clip(c.id).beats; })}, '✕')),
            h('div', {class: 'fx-row'}, every,
                h('button', {class: 'fx-ai-btn', title: T('Every shot on the main track ends on a beat of this music'),
                    onclick: FS.guard(() => {
                        const n = FS.cutToBeat(c.id, +every.value);
                        FS.message(T('{0} shots now end on the beat', n));
                    })}, '✂ ' + T('Cut to the beat'))));
    }

    function projectInspector() {
        const h = FS.h;
        const st = S.doc.settings;
        const fps = h('select', {class: 'fx-select'}, ...[24, 25, 30, 32, 50, 60].map((f) => h('option', {value: f, selected: +f === +st.fps}, f)));
        fps.addEventListener('change', () => FS.change('frame rate', (d) => { d.settings.fps = +fps.value; }));
        const bg = h('input', {type: 'color', value: st.background || '#000000', class: 'fx-swatch'});
        bg.addEventListener('change', () => FS.change('background', (d) => { d.settings.background = bg.value; }));
        const w = h('input', {type: 'number', class: 'fx-num', value: st.width, min: 64, max: 7680, step: 2});
        const hh = h('input', {type: 'number', class: 'fx-num', value: st.height, min: 64, max: 7680, step: 2});
        const size = () => FS.change('size', (d) => { d.settings.width = Math.max(64, Math.round(+w.value / 2) * 2); d.settings.height = Math.max(64, Math.round(+hh.value / 2) * 2); FS.player.resize(); });
        w.addEventListener('change', size);
        hh.addEventListener('change', size);
        const clips = S.doc.clips.length;
        return [
            h('div', {class: 'fx-ins-head'}, h('b', {}, T('The video')), h('span', {class: 'fx-dim'}, T('{0} clips · {1}', clips, FS.timecode(FS.total(), false)))),
            section('Frame', h('div', {class: 'fx-row'}, w, h('span', {}, '×'), hh), h('label', {class: 'fx-field'}, T('Frames a second'), fps),
                h('label', {class: 'fx-check'}, bg, ' ', T('Background colour'))),
            section('Keys', h('div', {class: 'fx-keys', dir: 'ltr'},
                ...[['Space', T('Play / pause')], ['S', T('Split')], ['Del', T('Delete')], ['Ctrl+Z / Y', T('Undo / redo')],
                    ['Ctrl+D', T('Duplicate')], ['← →', T('A frame (Shift: a second)')], ['Ctrl+wheel', T('Zoom the timeline')],
                    [T('Wheel on the picture'), T('Size of the selected clip')], ['Alt+wheel', T('Turn it')],
                    ['M', T('A marker at the playhead (again: take it away)')], ['I / O', T('Start / end of the marked part (for the export)')], ['Ctrl+Alt+C / V', T('Copy / paste a clip\'s style')],
                    ['Q / W', T('Trim the clip\'s start / end to the playhead')], ['↑ / ↓', T('The cut before / after')],
                    [', / .', T('Nudge the picked clips a frame (Shift: a second)')]]
                    .map(([k, d]) => h('div', {}, h('kbd', {}, k), ' ', d)))),
            markerList(),
        ];
    }

    /* ✨ how the clip comes in and goes out (FS.ANIMS; the way out is the way in backwards) */
    const ANIM_NAMES = {'': 'None', fade: 'Fade', rise: 'Rise', pop: 'Pop', 'wipe-r': 'Wipe from the right', 'wipe-l': 'Wipe from the left', type: 'Typewriter'};
    function animation(c) {
        const h = FS.h;
        const row = (key, secKey, label) => {
            const kinds = Object.keys(ANIM_NAMES).filter((k) => k !== 'type' || c.kind === 'text');
            const pick = h('select', {class: 'fx-select'}, ...kinds.map((k) => h('option', {value: k, selected: (c[key] || '') === k}, T(ANIM_NAMES[k]))));
            pick.addEventListener('change', () => FS.change('animation', () => {
                const x = FS.clip(c.id);
                if (pick.value) x[key] = pick.value; else { delete x[key]; delete x[secKey]; }
                // typing: about 15 letters a second to begin with, at most most of the clip
                if (pick.value === 'type') x[secKey] = Math.round(Math.max(0.3, Math.min(x.duration * 0.7, FS.typeSteps(x.text) / 15)) * 20) / 20;
            }));
            return [h('label', {class: 'fx-field'}, T(label), pick),
                c[key] ? field('Seconds', c.id, secKey, {min: 0.1, max: Math.max(0.1, Math.min(c[key] === 'type' ? 30 : 5, c.duration)), step: 0.05, reset: 0.5,
                    get: (x) => (x[secKey] == null ? 0.5 : x[secKey]), set: (x, v) => { x[secKey] = v; }}) : null];
        };
        return section('Animation', ...row('animIn', 'animInSec', 'Coming in'), ...row('animOut', 'animOutSec', 'Going out'));
    }

    /* 🚩 the markers: jump to one, give it a name, take it away */
    function markerList() {
        const h = FS.h;
        const part = FS.range();
        const partRow = part ? h('div', {class: 'fx-row fx-marker-row'},
            h('button', {class: 'fx-ai-btn', title: T('Go there'), onclick: () => FS.seek(part.in)}, '🟦 ' + FS.timecode(part.in) + ' – ' + FS.timecode(part.out)),
            h('span', {class: 'fx-dim'}, T('The marked part (the export can take just this)')),
            h('button', {class: 'fx-icon', title: T('Clear the marked part'), onclick: () => FS.clearRange()}, '✕')) : null;
        const ms = FS.markers();
        if (partRow) return section('Markers', partRow, ...(ms.length ? markerRows(ms) : []));
        if (!ms.length) return section('Markers', h('div', {class: 'fx-dim'}, T('M puts a marker at the playhead; clip edges snap to markers.')));
        return section('Markers', ...markerRows(ms));
    }
    function markerRows(ms) {
        const h = FS.h;
        return ms.map((m, i) => {
            const name = h('input', {class: 'fx-input', value: m.name || '', dir: 'auto', placeholder: T('Name')});
            name.addEventListener('change', () => FS.renameMarker(i, name.value.trim()));
            return h('div', {class: 'fx-row fx-marker-row'},
                h('button', {class: 'fx-ai-btn', title: T('Go there'), onclick: () => FS.seek(m.t)}, '🚩 ' + FS.timecode(m.t)),
                name,
                h('button', {class: 'fx-icon', title: T('Take the marker away'), onclick: () => FS.removeMarker(i)}, '✕'));
        });
    }

    /* 🖌 a clip's style onto others */
    function styleTools(c) {
        const h = FS.h;
        const paste = h('button', {class: 'fx-ai-btn', disabled: !FS.styleClipboard, title: T('Colour, crop and shape, motion, volume and transition onto the selected clips'),
            onclick: () => FS.message(T('Style pasted on {0} clips', FS.pasteStyle(S.selected.length ? S.selected : [c.id])))}, '📋 ' + T('Paste style'));
        return h('div', {class: 'fx-row'},
            h('button', {class: 'fx-ai-btn', title: T('Copy this clip\'s colour, crop and shape, motion, volume and transition'),
                onclick: () => { FS.copyStyle(c.id); paste.disabled = false; FS.message(T('Style copied')); }}, '🖌 ' + T('Copy style')), paste);
    }

    I.live = [];
    I.render = function () {
        const box = FS.UI && FS.UI.inspector;
        if (!box || !S.doc) return;
        if (box.contains(document.activeElement) && document.activeElement.matches('input,textarea,select')) return;  // typing
        const c = FS.clip(S.selected[0]);
        box.innerHTML = '';
        I.live = [];
        box.append(...(c ? clipInspector(c) : projectInspector()));
    };

    FS.TEXT_TEMPLATES = [['center', 'Title in the middle'], ['lower', 'Lower third'], ['top', 'Title at the top'],
        ['caption', 'Caption'], ['card', 'Full-screen card'], ['signature', 'Signature in the corner']];
    FS.FONTS = ['Segoe UI', 'David', 'FrankRuehl', 'Arial', 'Tahoma', 'Georgia', 'Impact', 'Segoe UI Emoji'];
    const SHAPE_NAMES = {rect: 'Rectangle', round: 'Rounded rectangle', circle: 'Circle', arrow: 'Arrow', line: 'Line', star: 'Star', heart: 'Heart'};
    FS.SHAPE_NAMES = SHAPE_NAMES;

    FS.on('select', () => I.render());
    FS.on('inspect', () => I.render());
    FS.on('project', () => I.render());
    FS.on('doc', (e) => { if (!e || !e.live) I.render(); else I.live.forEach((f) => f()); });
    FS.on('time', () => I.live.forEach((f) => f()));
})();

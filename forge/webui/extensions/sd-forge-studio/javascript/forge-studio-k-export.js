/* Forge Studio - the export window. File (name, folder, format), Picture (upscale: RTX Video Super Resolution by
 * default, x2), Frames (x2 / x4 by RIFE or FILM), HDR10 (sdr2hdr) and Sound, with a summary of what comes out and
 * how long it takes, progress and Stop, then Watch / Open folder. The choices are remembered (per browser).
 * The finishing steps run on the server after the edit is exported (finish.py); render.finish_tools() says which
 * are there, and the ones that are not are shown greyed with why.
 */
(function () {
    'use strict';
    const FS = window.ForgeStudio = window.ForgeStudio || {};
    const S = FS.state;
    const T = (...a) => FS.T(...a);
    const KEY = 'forgeStudio.export';

    function remembered() {
        try { return JSON.parse(localStorage.getItem(KEY) || '{}'); } catch (e) { return {}; }
    }
    function remember(v) {
        try { localStorage.setItem(KEY, JSON.stringify(v)); } catch (e) { /* storage off */ }
    }

    // seconds a frame, measured on the RTX 3070 Laptop at 1080 x 1920 (2026-11-02); scaled by the pixels
    const COST = {export: 1 / 55, rife: 0.05, film: 0.25, rtx: 0.03, hdr1080: 1 / 7.8};

    FS.openExport = function () {
        if (!S.project) throw new Error(T('Open or create a project first'));
        const h = FS.h;
        const opts = FS.UI.options || {};
        const tools = opts.finish || {};
        const was = remembered();
        const {width: W, height: H, fps} = S.doc.settings;
        const select = (id, items, value, attrs) => h('select', Object.assign({class: 'fx-select', id}, attrs || {}),
            ...items.map(([v, t, off]) => h('option', {value: v, selected: String(v) === String(value) ? '' : null, disabled: off ? '' : null}, t)));
        const row = (label, ...el) => h('label', {class: 'fx-xrow'}, h('span', {class: 'fx-xlabel'}, label), h('span', {class: 'fx-xfield'}, ...el));
        const section = (title, note, ...rows) => h('div', {class: 'fx-xsec'}, h('div', {class: 'fx-xsec-title'}, title, note ? h('span', {class: 'fx-dim'}, note) : null), ...rows);

        const name = h('input', {class: 'fx-input', id: 'fx-x-name', value: '', placeholder: S.project + ' – ' + T('time of the export'), dir: 'auto'});
        const folder = h('input', {class: 'fx-input', id: 'fx-x-folder', value: was.folder || '', placeholder: opts.exports || '', dir: 'ltr'});
        const browse = h('button', {class: 'fx-btn', type: 'button', onclick: FS.guard(async () => {
            browse.disabled = true;
            try { const r = await FS.api('pick-folder', {start: folder.value}); if (r.folder) { folder.value = r.folder; update(); } } finally { browse.disabled = false; }
        })}, '📁 ' + T('Choose…'));
        const preset = select('fx-x-preset', (opts.presets || []).map((p) => [p.id, T(p.label)]), was.preset || 'h264');

        const upScale = select('fx-x-up', [['1', T('Off: the edit\'s own size')], ['1.5', '×1.5'], ['2', '×2'], ['3', '×3'], ['4', '×4']], was.up || '1');
        const upEngine = select('fx-x-engine', [
            ['rtx:HIGHBITRATE_ULTRA', 'RTX Video Super Resolution (NVIDIA)', !tools.rtx],
            ['rtx:ULTRA', 'RTX Video Super Resolution – ' + T('compressed video'), !tools.rtx],
            ['lanczos:', 'Lanczos (' + T('no AI') + ')']], was.engine || (tools.rtx ? 'rtx:HIGHBITRATE_ULTRA' : 'lanczos:'));
        const frames = select('fx-x-frames', [['1', T('Same ({0} a second)', fps)], ['2', T('×2 smoother ({0} a second)', fps * 2), !tools.rife && !tools.film],
            ['4', T('×4 ({0} a second, for slow motion)', fps * 4), !tools.rife && !tools.film]], was.frames || '1');
        const interp = select('fx-x-interp', [['rife', 'RIFE 4.6 (' + T('fast') + ')', !tools.rife], ['film', 'FILM (' + T('finer on big motion, slower') + ')', !tools.film]],
            was.interp || (tools.rife ? 'rife' : 'film'));
        const hdrOn = h('input', {type: 'checkbox', id: 'fx-x-hdr', checked: was.hdr && tools.hdr ? '' : null, disabled: tools.hdr ? null : ''});
        const hdrStyle = select('fx-x-style', [['natural', T('Natural')], ['cinematic', T('Cinematic')], ['night', T('Night')]], was.style || 'natural');
        const hdrTone = select('fx-x-tone', [['vivid', T('Vivid (white at full brightness)')], ['reference', T('Reference (BT.2408, white at 203 nits)')]], was.tone || 'vivid');
        const strength = h('input', {type: 'range', class: 'fx-range', id: 'fx-x-strength', min: 0, max: 1, step: 0.05, value: was.strength != null ? was.strength : 0.25});
        const strengthShown = h('span', {class: 'fx-dim'});
        const loud = select('fx-x-loud', (opts.loudness || []).map((l) => [l.id, T(l.label)]), was.loud != null ? was.loud : '-14');
        // the whole film, or only the part marked with I and O
        const marked = FS.range();
        const part = select('fx-x-part', [['all', T('The whole film')],
            ['range', marked ? T('The marked part ({0} – {1})', FS.timecode(marked.in), FS.timecode(marked.out)) : T('The marked part (mark it with I and O)'), !marked]],
            marked ? 'range' : 'all');
        const length = () => (part.value === 'range' && marked ? marked.out - marked.in : FS.total());

        const summary = h('div', {class: 'fx-xsummary'});
        const bar = h('div', {class: 'fx-bar'}, h('div'));
        const status = h('div', {class: 'fx-dim fx-xstatus'});
        const after = h('div', {class: 'fx-row'});
        let job = null;

        function choices() {
            const [engine, mode] = upEngine.value.split(':');
            return {folder: folder.value.trim(), preset: preset.value, up: upScale.value, engine: upEngine.value, frames: frames.value,
                interp: interp.value, hdr: hdrOn.checked, style: hdrStyle.value, tone: hdrTone.value, strength: +strength.value,
                loud: loud.value, options: {
                    onlyRange: part.value === 'range',
                    folder: folder.value.trim(), filename: name.value.trim(),
                    fps_x: +frames.value > 1 ? +frames.value : 1, interpolator: interp.value,
                    upscale: +upScale.value > 1 ? {scale: +upScale.value, engine, mode} : null,
                    hdr: hdrOn.checked ? {style: hdrStyle.value, tone: hdrTone.value, strength: +strength.value} : null}};
        }
        function update() {
            const c = choices();
            const scale = +c.up, ow = Math.floor(W * scale / 8) * 8 || W, oh = Math.floor(H * scale / 8) * 8 || H;
            const n = Math.round(length() * fps), nOut = n * (+c.frames);
            const px = (W * H) / (1080 * 1920), pxOut = (ow * oh) / (1080 * 1920);
            let secs = n * COST.export * px;
            if (+c.frames > 1) secs += n * (c.interp === 'film' ? COST.film : COST.rife) * px * (+c.frames - 1);
            if (scale > 1) secs += nOut * (c.engine.startsWith('rtx') ? COST.rtx : 0.01) * pxOut;
            if (c.hdr) secs += nOut * COST.hdr1080 * pxOut;
            upEngine.disabled = scale <= 1;
            interp.disabled = +c.frames <= 1;
            for (const el of [hdrStyle, hdrTone, strength]) el.disabled = !c.hdr;
            preset.disabled = c.hdr;
            strengthShown.textContent = (+c.strength).toFixed(2);
            const codec = c.hdr ? 'HEVC 10-bit HDR10 (MP4)' : (preset.selectedOptions[0] || {}).textContent || '';
            summary.textContent = T('Out: {0} × {1}, {2} frames a second, {3}, {4}. About {5}.', ow, oh, fps * (+c.frames), codec,
                FS.timecode(length(), false), secs < 90 ? T('{0} seconds', Math.max(5, Math.round(secs / 5) * 5)) : T('{0} minutes', Math.round(secs / 60)));
            remember(Object.assign({}, c, {options: undefined}));
        }

        const go = h('button', {class: 'fx-primary fx-xgo', onclick: FS.guard(async () => {
            const c = choices();
            go.disabled = true;
            stop.hidden = false;
            after.innerHTML = '';
            try {
                job = null;
                const done = await FS.exportNow(c.preset, c.loud ? parseFloat(c.loud) : null, (j) => {
                    job = j;
                    bar.firstChild.style.width = Math.round((j.progress || 0) * 100) + '%';
                    status.textContent = j.state === 'running' ? `${Math.round((j.progress || 0) * 100)}% · ${T(j.text || 'Exporting…')}` : '';
                }, c.options);
                if (done && done.state === 'done') {
                    status.textContent = T('Saved: {0}', done.out);
                    after.append(
                        h('a', {href: '/forge-studio/render/file?id=' + done.id, target: '_blank', class: 'fx-btn'}, '▶ ' + T('Watch')),
                        h('button', {class: 'fx-btn', onclick: FS.guard(() => FS.api('render/open-folder', {id: done.id}))}, '📁 ' + T('Show in the folder')));
                } else if (done && done.state === 'cancelled') status.textContent = T('Stopped');
            } finally { go.disabled = false; stop.hidden = true; }
        })}, '⬆ ' + T('Export'));
        const stop = h('button', {class: 'fx-btn', hidden: '', onclick: FS.guard(async () => { if (job) await FS.api('render/cancel', {id: job.id}); })}, '■ ' + T('Stop'));
        const close = () => { if (!go.disabled || confirm(T('The export goes on in the background. Close this window?'))) box.remove(); };

        const box = h('div', {class: 'fx-modal-back', dir: FS.hebrew() ? 'rtl' : 'ltr', onclick: (e) => { if (e.target === box) close(); }},
            h('div', {class: 'fx-modal', role: 'dialog'},
                h('div', {class: 'fx-modal-head'}, h('span', {}, '⬆ ' + T('Export')), h('span', {class: 'fx-dim'}, S.project),
                    h('span', {class: 'fx-spacer'}), h('button', {class: 'fx-icon', title: T('Close'), onclick: close}, '✕')),
                h('div', {class: 'fx-modal-body'},
                    section(T('File'), null,
                        row(T('Name'), name),
                        row(T('Save to'), folder, browse, h('button', {class: 'fx-icon', type: 'button', title: T('Forge Studio\'s own exports folder'), onclick: () => { folder.value = ''; update(); }}, '↺')),
                        row(T('Format'), preset),
                        row(T('Part'), part)),
                    section(T('Picture'), T('the edit is {0} × {1}', W, H),
                        row(T('Upscale'), upScale, upEngine)),
                    section(T('Frames'), T('in-between frames drawn by AI'),
                        row(T('Frames a second'), frames, interp)),
                    section('HDR', tools.hdr ? 'sdr2hdr' : T('needs sdr2hdr in {0}', tools.hdr_home || ''),
                        h('label', {class: 'fx-check'}, hdrOn, ' ', T('HDR10 (HEVC 10-bit, BT.2020 / PQ) for HDR screens and phones')),
                        row(T('Look'), hdrStyle, hdrTone),
                        row(T('AI strength'), strength, strengthShown)),
                    section(T('Sound'), null, row(T('Loudness'), loud))),
                h('div', {class: 'fx-modal-foot'}, summary, h('div', {class: 'fx-row'}, go, stop), bar, status, after)));
        for (const el of [name, folder, preset, upScale, upEngine, frames, interp, hdrOn, hdrStyle, hdrTone, strength, loud, part]) {
            el.addEventListener('input', update);
            el.addEventListener('change', update);
        }
        box.addEventListener('keydown', (e) => { if (e.key === 'Escape') { e.stopPropagation(); close(); } });
        FS.UI.root.append(box);
        update();
        name.focus();
    };
})();

/* Forge Studio - text and layers. Text clips are drawn by the page in every look (titles, lower third, caption,
 * card, the SHAI TIRI signature) on the first free layer at the playhead and edited in the inspector. Captions:
 * Whisper listens to the edit's own sound and each sentence becomes a caption clip on a track of its own. A clip
 * can be cut out of its background (BiRefNet): the cut-out lies on the first free layer above it, same place.
 */
(function () {
    'use strict';
    const FS = window.ForgeStudio = window.ForgeStudio || {};
    const S = FS.state;
    const T = (...a) => FS.T(...a);
    const q = encodeURIComponent;
    const UI = {};

    const LOOKS = [
        {id: 'center', label: 'Title in the middle', text: {template: 'center', text: 'כותרת', size: 7}},
        {id: 'top', label: 'Title at the top', text: {template: 'top', text: 'כותרת', size: 5.5, outline: 2}},
        {id: 'lower', label: 'Lower third', text: {template: 'lower', text: 'שם', sub: 'תיאור קצר', size: 4.5, box: true}},
        {id: 'caption', label: 'Caption', text: {template: 'caption', text: 'שורת כתובית', size: 4, box: true, boxAlpha: 0.5}},
        {id: 'card', label: 'Full-screen card', text: {template: 'card', text: 'פרק ראשון', size: 7, boxColor: '#101318'}},
        {id: 'signature', label: 'Signature in the corner', text: {template: 'signature', text: 'SHAI TIRI', font: 'Georgia', size: 6}},
    ];

    /* 🎬 templates: ready titles with their animation and length, one or more text clips put at the playhead in one
     * undo; each part `at` seconds after the first, on a layer above the part before it (an ending's signature lies
     * over its card). A typewriter part types about 15 letters a second, as the inspector's choice does. */
    const TEMPLATES = [
        {id: 'opening', label: 'Opening', parts: [{at: 0, seconds: 4, animIn: 'type', animOut: 'fade', animOutSec: 0.6,
            text: {template: 'center', text: 'שם הסרט', sub: 'סרט מאת', size: 7.5}}]},
        {id: 'lower', label: 'Lower third', parts: [{at: 0, seconds: 5, animIn: 'wipe-r', animInSec: 0.5, animOut: 'wipe-r', animOutSec: 0.4,
            text: {template: 'lower', text: 'שם', sub: 'תיאור קצר', size: 4.5, box: true}}]},
        {id: 'chapter', label: 'Chapter', parts: [{at: 0, seconds: 3, animIn: 'fade', animInSec: 0.5, animOut: 'fade', animOutSec: 0.5,
            text: {template: 'card', text: 'פרק ראשון', sub: 'שם הפרק', size: 7, boxColor: '#101318'}}]},
        {id: 'quote', label: 'Quote', parts: [{at: 0, seconds: 5, animIn: 'type', animOut: 'fade', animOutSec: 0.6,
            text: {template: 'center', text: '״משפט אחד שנשאר״', sub: '— שם', size: 5.5, box: true, boxAlpha: 0.45}}]},
        {id: 'ending', label: 'Ending', parts: [
            {at: 0, seconds: 4.5, animIn: 'fade', animInSec: 0.6, text: {template: 'card', text: 'תודה שצפיתם', size: 6.5, boxColor: '#101318'}},
            {at: 1, seconds: 3.5, animIn: 'pop', animInSec: 0.6, text: {template: 'signature', text: 'SHAI TIRI', font: 'Georgia', size: 6}}]},
    ];
    FS.TEMPLATES = TEMPLATES;
    const STICKERS = ['😂', '😍', '😮', '😢', '😡', '👍', '👏', '🙏', '🔥', '❤️', '✨', '⭐', '🎉', '💯', '👀', '💡',
        '✅', '❌', '⚠️', '➡️', '⬅️', '📍', '🎬', '🎵'];

    FS.addTemplate = function (tpl, at) {
        const time = FS.snapTime(at != null ? at : S.time);
        return FS.change('template', (d) => {
            const made = [];
            let floor = -1;   // each part above the one before it
            for (const p of tpl.parts) {
                const a = time + p.at, b = a + p.seconds;
                const visual = FS.visualTracks();
                let track = visual.find((t, i) => !t.main && i > floor && !d.clips.some((c) => c.track === t.id && c.start < b && FS.end(c) > a));
                track = track ? track.id : FS.addTrack('video');
                floor = FS.visualTracks().findIndex((t) => t.id === track);
                const clip = {id: FS.newId(), track, kind: 'text', start: FS.snapTime(a), duration: p.seconds,
                    fadeIn: 0, fadeOut: 0, text: Object.assign({}, FS.TEXT_DEFAULTS, p.text)};
                for (const k of ['animIn', 'animInSec', 'animOut', 'animOutSec']) if (p[k] != null) clip[k] = p[k];
                if (clip.animIn === 'type' && clip.animInSec == null) clip.animInSec = Math.round(Math.max(0.3, Math.min(clip.duration * 0.7, FS.typeSteps(clip.text) / 15)) * 20) / 20;
                d.clips.push(clip);
                made.push(clip);
            }
            S.selected = made.map((c) => c.id);
            return made;
        });
    };

    FS.addText = function (look, at, seconds, trackId) {
        const time = at != null ? at : S.time;
        const len = seconds || (look.text.template === 'signature' ? 3 : 4);
        return FS.change('text', (d) => {
            const track = trackId || FS.freeLayer(time, time + len);
            const clip = {id: FS.newId(), track, kind: 'text', start: FS.snapTime(time), duration: len,
                fadeIn: 0.3, fadeOut: 0.3, text: Object.assign({}, FS.TEXT_DEFAULTS, look.text)};
            d.clips.push(clip);
            FS.place(clip, 'free');
            S.selected = [clip.id];
            return clip;
        });
    };

    async function captions(button, status) {
        if (!S.project) throw new Error(T('Open or create a project first'));
        await FS.save();
        status(T('Whisper is listening…'));
        const task = await FS.follow(await FS.api('sound/captions', {name: S.project, doc: S.doc, language: UI.capLang.value}), (t) => status(t.text));
        FS.change('captions', (d) => {
            const track = FS.addTrack('video');
            FS.track(track).name = 'captions';
            for (const cue of task.cues) {
                d.clips.push({id: FS.newId(), track, kind: 'text', start: FS.snapTime(cue.start), duration: Math.max(0.4, cue.end - cue.start),
                    text: Object.assign({}, FS.TEXT_DEFAULTS, {template: 'caption', text: cue.text, size: 3.6, box: true, boxAlpha: 0.5, weight: 600})});
            }
        });
        status(T('{0} captions on a track of their own: click one to edit it', task.cues.length));
    }

    /* ---- cut-outs ---- */

    FS.layers = {
        cutoutClip: async function (clip) {
            FS.busy(T('Cutting out…'));
            try {
                const task = await FS.follow(await FS.api('layers/cutout', {name: S.project, web: clip.media}), (t) => FS.busy(t.text));
                S.media[task.web] = {path: task.web, missing: false};
                await FS.probe(task.web);
                FS.change('cut out', (d) => {
                    const c = FS.clip(clip.id);
                    const track = FS.freeLayer(c.start, FS.end(c));
                    const copy = Object.assign(JSON.parse(JSON.stringify(c)), {id: FS.newId(), track, media: task.web, kind: c.kind, takes: undefined});
                    d.clips.push(copy);
                    S.selected = [copy.id];
                });
                FS.message(T('The cut-out is on a layer above the clip. Put a new background under it.'));
            } finally { FS.busy(null); }
        },
        cutoutLibrary: async function (item) {
            if (!S.project) await FS.newProject(FS.defaultProjectName());
            FS.busy(T('Cutting out…'));
            try {
                const task = await FS.follow(await FS.api('layers/cutout', {name: S.project, id: item.id}), (t) => FS.busy(t.text));
                S.media[task.web] = {path: task.web, missing: false};
                const info = await FS.probe(task.web);
                await FS.addMedia(task.web, {time: S.time, track: FS.change('track', () => FS.freeLayer(S.time, S.time + (info.duration || 5)))}, {mode: 'free'});
            } finally { FS.busy(null); }
        },
    };

    FS.clipActions.push({id: 'cutout', label: '✂ ' + T('Cut out the background (a layer above)'), when: (c) => (c.kind === 'video' || c.kind === 'image') && c.media,
        run: (c) => FS.layers.cutoutClip(c)});

    function build() {
        const h = FS.h;
        const grid = h('div', {class: 'fx-looks'});
        for (const look of LOOKS) {
            const cv = h('canvas', {width: 270, height: 480});
            const tile = h('button', {class: 'fx-look', title: T('Add at the playhead'), onclick: FS.guard(() => {
                if (!S.project) throw new Error(T('Open or create a project first'));
                FS.addText(look);
                FS.emit('select', S.selected);
            })}, cv, h('span', {}, T(look.label)));
            grid.append(tile);
            document.fonts.ready.then(() => {
                const x = cv.getContext('2d');
                x.fillStyle = '#20252e';
                x.fillRect(0, 0, 270, 480);
                FS.drawText(x, 270, 480, Object.assign({}, FS.TEXT_DEFAULTS, look.text));
            });
        }
        const ready = h('div', {class: 'fx-looks'});
        for (const tpl of TEMPLATES) {
            const cv = h('canvas', {width: 270, height: 480});
            ready.append(h('button', {class: 'fx-look', title: T('Add at the playhead'), onclick: FS.guard(() => {
                if (!S.project) throw new Error(T('Open or create a project first'));
                FS.addTemplate(tpl);
                FS.emit('select', S.selected);
            })}, cv, h('span', {}, T(tpl.label))));
            document.fonts.ready.then(() => {
                const x = cv.getContext('2d');
                x.fillStyle = '#20252e';
                x.fillRect(0, 0, 270, 480);
                for (const p of tpl.parts) FS.drawText(x, 270, 480, Object.assign({}, FS.TEXT_DEFAULTS, p.text));
            });
        }
        // shapes and stickers: text clips that draw a shape or one big emoji (they move, grow and animate like any text)
        const shapes = h('div', {class: 'fx-looks fx-shapes'});
        for (const [shape, aspect] of [['rect', 1.6], ['round', 1.6], ['circle', 1], ['arrow', 1.8], ['line', 3], ['star', 1], ['heart', 1.1]]) {
            const look = {text: {template: 'shape', shape, aspect, size: shape === 'line' ? 10 : 16, color: '#ffd400', fill: shape !== 'line', thick: 6, text: ''}};
            const cv = h('canvas', {width: 120, height: 120});
            shapes.append(h('button', {class: 'fx-look', title: T('Add at the playhead'), onclick: FS.guard(() => {
                if (!S.project) throw new Error(T('Open or create a project first'));
                FS.addText(look);
                FS.emit('select', S.selected);
            })}, cv, h('span', {}, T(FS.SHAPE_NAMES[shape]))));
            const x = cv.getContext('2d');
            x.fillStyle = '#20252e';
            x.fillRect(0, 0, 120, 120);
            FS.drawShape(x, 120, 120, Object.assign({}, FS.TEXT_DEFAULTS, look.text, {size: shape === 'line' ? 30 : 50, shadow: false}));
        }
        const stickers = h('div', {class: 'fx-stickers'});
        for (const e of STICKERS) {
            stickers.append(h('button', {class: 'fx-sticker', title: T('Add at the playhead'), onclick: FS.guard(() => {
                if (!S.project) throw new Error(T('Open or create a project first'));
                FS.addText({text: {template: 'center', text: e, font: 'Segoe UI Emoji', size: 14, shadow: false, weight: 400}});
                FS.emit('select', S.selected);
            })}, e));
        }
        UI.capLang = h('select', {class: 'fx-select', id: 'fx-cap-lang'}, ...['Hebrew', 'English', 'Arabic', 'Russian', 'Auto'].map((l) => h('option', {value: l}, T(l))));
        const capStatusEl = h('div', {class: 'fx-dim'});
        const capGo = h('button', {class: 'fx-primary'}, '🗒 ' + T('Captions from the sound'));
        capGo.addEventListener('click', FS.guard(async () => {
            capGo.disabled = true;
            try { await captions(capGo, (t) => { capStatusEl.textContent = t || ''; }); } finally { capGo.disabled = false; }
        }));
        return h('div', {class: 'fx-panel-body'},
            h('div', {class: 'fx-group'}, h('div', {class: 'fx-group-title'}, '🎬 ' + T('Templates')),
                h('div', {class: 'fx-dim'}, T('A ready title with its animation and length, at the playhead; change the words in the inspector.')), ready),
            h('div', {class: 'fx-group'}, h('div', {class: 'fx-group-title'}, '𝐓 ' + T('Text')),
                h('div', {class: 'fx-dim'}, T('Click a look to put it at the playhead; edit its words and style in the inspector.')), grid),
            h('div', {class: 'fx-group'}, h('div', {class: 'fx-group-title'}, '⭐ ' + T('Stickers and shapes')),
                h('div', {class: 'fx-dim'}, T('In the middle of the frame at the playhead: drag it in the picture, size, colour and animation in the inspector.')),
                shapes, stickers),
            h('div', {class: 'fx-group'}, h('div', {class: 'fx-group-title'}, '🗒 ' + T('Captions')),
                h('div', {class: 'fx-dim'}, T('Whisper (large-v2) listens to the edit and writes each sentence as a caption clip.')),
                h('div', {class: 'fx-row'}, h('span', {class: 'fx-dim'}, T('Language')), UI.capLang), capGo, capStatusEl));
    }

    FS.panels.push({id: 'text', icon: '𝐓', label: 'Text', build});
})();

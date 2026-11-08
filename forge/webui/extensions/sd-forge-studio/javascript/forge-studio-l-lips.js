/* Forge Studio - lip sync: a video clip made to say something (lipsync.py on the server), three ways:
 *   🔊 a sound already in the edit (a narration, a recording)   - MuseTalk draws the mouth for it;
 *   ✍ written words, said by a voice of the list                - the voice, then MuseTalk; the voice goes under the clip;
 *   🙂 a face video (someone filmed saying it)                   - LivePortrait moves the face like it, with its sound.
 * The clip then plays the talking take from its start at normal speed; "Back to the original" returns it.
 */
(function () {
    'use strict';
    const FS = window.ForgeStudio = window.ForgeStudio || {};
    const S = FS.state;
    const T = (...a) => FS.T(...a);
    const q = encodeURIComponent;

    /* the clip plays the talking take (from 0, at normal speed, as long as the take); the original is kept to go back */
    FS.takeLips = (clipId, web, seconds) => FS.change('lip sync', () => {
        const c = FS.clip(clipId);
        if (!c.lips) {
            c.lips = {media: c.media, in: FS.val(c, 'in'), speed: FS.val(c, 'speed'), duration: c.duration, kind: c.kind};
            if (FS.keysOf(c, 'speed')) c.lips.speedKeys = JSON.parse(JSON.stringify(c.keys.speed));
        }
        c.media = web;
        c.kind = 'video';   // a picture that talks is a video now
        c.in = 0;
        delete c.speed;
        if (c.keys) { delete c.keys.speed; if (!Object.keys(c.keys).length) delete c.keys; }
        c.duration = Math.max(1 / FS.fps(), seconds);
        FS.closeGaps();
    });
    FS.lipsBack = (clipId) => FS.change('lip sync off', () => {
        const c = FS.clip(clipId);
        if (!c.lips) return;
        Object.assign(c, {media: c.lips.media, in: c.lips.in, duration: c.lips.duration, kind: c.lips.kind || 'video'});
        if (c.lips.speed !== 1) c.speed = c.lips.speed; else delete c.speed;
        if (c.lips.speedKeys) { c.keys = c.keys || {}; c.keys.speed = c.lips.speedKeys; }
        delete c.lips;
        FS.closeGaps();
    });

    /* the sound clips that could be the words: those playing with the clip first */
    function soundsFor(c) {
        const overlap = (x) => Math.max(0, Math.min(FS.end(x), FS.end(c)) - Math.max(x.start, c.start));
        return S.doc.clips.filter((x) => x.kind === 'audio' && x.media).sort((a, b) => overlap(b) - overlap(a) || a.start - b.start);
    }

    FS.openLips = async function (clip) {
        const h = FS.h;
        const opts = await FS.api('lips/options').catch(() => ({}));
        const c = FS.clip(clip.id);
        const row = (label, ...el) => h('label', {class: 'fx-xrow'}, h('span', {class: 'fx-xlabel'}, label), h('span', {class: 'fx-xfield'}, ...el));
        const sec = (title, note, ...rows) => h('div', {class: 'fx-xsec'}, h('div', {class: 'fx-xsec-title'}, title, note ? h('span', {class: 'fx-dim'}, note) : null), ...rows);

        const way = h('select', {class: 'fx-select', id: 'fx-lips-way'},
            h('option', {value: 'sound', disabled: opts.lips ? null : ''}, '🔊 ' + T('To a sound in the edit')),
            h('option', {value: 'text', disabled: opts.lips && opts.voice ? null : ''}, '✍ ' + T('To written words')),
            h('option', {value: 'face', disabled: opts.face ? null : ''}, '🙂 ' + T('Like a face video (LivePortrait)')));
        // a sound of the edit
        const sounds = soundsFor(c);
        const sound = h('select', {class: 'fx-select', id: 'fx-lips-sound'}, ...sounds.map((x) =>
            h('option', {value: x.id}, `${x.media} · ${FS.timecode(x.start)}–${FS.timecode(FS.end(x))}`)));
        if (!sounds.length) sound.append(h('option', {value: '', disabled: ''}, T('No sound clip in the edit yet')));
        // words
        const text = h('textarea', {class: 'fx-input fx-textarea', id: 'fx-lips-text', rows: 3, dir: 'auto', placeholder: T('What the character says (Hebrew is given niqqud by itself)')});
        const lang = h('select', {class: 'fx-select', id: 'fx-lips-lang'}, ...(opts.languages || ['Hebrew']).map((l) => h('option', {value: l}, T(l))));
        const voice = h('select', {class: 'fx-select', id: 'fx-lips-voice'}, ...(opts.voices || []).map((v) => h('option', {value: v}, v)));
        // a face video: one from the computer, or one of the project's videos
        const faces = Object.keys(S.media || {}).filter((w) => /\.(mp4|mov|webm|mkv|m4v)$/i.test(w) && w !== c.media);
        const face = h('select', {class: 'fx-select', id: 'fx-lips-face'}, h('option', {value: ''}, T('Choose a face video…')),
            ...faces.map((w) => h('option', {value: w}, w)));
        const file = h('input', {type: 'file', accept: 'video/*', hidden: ''});
        const upload = h('button', {class: 'fx-btn', type: 'button', onclick: () => file.click()}, '⤒ ' + T('From the computer…'));
        file.addEventListener('change', FS.guard(async () => {
            const f = file.files[0];
            if (!f) return;
            status.textContent = T('Bringing the face video…');
            const r = await FS.api(`project/upload?name=${q(S.project)}&web=${q(f.name)}`, f, true);
            S.media[r.web] = {path: r.web, missing: false};
            face.append(h('option', {value: r.web}, r.web));
            face.value = r.web;
            status.textContent = '';
        }));
        const region = h('select', {class: 'fx-select', id: 'fx-lips-region'},
            h('option', {value: 'lips'}, T('Only the lips (cleanest)')),
            h('option', {value: 'all'}, T('The whole face and head (livelier; an edge can show around the face)')));

        const parts = {
            sound: sec(T('The words'), T('MuseTalk draws the mouth for this sound'), row(T('Sound'), sound)),
            text: sec(T('The words'), T('a voice says them, then MuseTalk draws the mouth'), text, row(T('Language'), lang), row(T('Voice'), voice)),
            face: sec(T('The face video'), T('someone filmed saying it; their face, head and sound go onto the character'),
                row(T('Video'), face, upload, file), row(T('Moves'), region),
                h('div', {class: 'fx-dim'}, T('Film the face close up and from the front, like a selfie: from far away the mouth hardly moves.'))),
        };
        const show = () => { for (const [k, el] of Object.entries(parts)) el.hidden = k !== way.value; };
        way.addEventListener('change', show);
        if (!sounds.length && !way.options[1].disabled) way.value = 'text';
        show();

        const status = h('div', {class: 'fx-dim fx-xstatus'});
        const bar = h('div', {class: 'fx-bar'}, h('div'));
        const go = h('button', {class: 'fx-primary fx-xgo', onclick: FS.guard(async () => {
            const x = FS.clip(c.id);
            const options = {};
            if (way.value === 'sound') {
                const sc = FS.clip(sound.value);
                if (!sc) throw new Error(T('Choose the sound'));
                // the part of the sound that plays with the clip, and where in the clip it begins
                const from = Math.max(sc.start, x.start);
                options.sound = sc.media;
                options.sound_in = FS.sourceTime(sc, from);
                options.sound_seconds = FS.sourceTime(sc, FS.end(sc)) - options.sound_in;
                options.offset = from - x.start;
            } else if (way.value === 'text') {
                if (!text.value.trim()) throw new Error(T('Write what the character says'));
                Object.assign(options, {text: text.value.trim(), language: lang.value, voice: voice.value});
            } else {
                if (!face.value) throw new Error(T('Choose a face video'));
                Object.assign(options, {face: face.value, region: region.value});
            }
            go.disabled = true;
            try {
                const task = await FS.follow(await FS.api('lips', {name: S.project, web: x.media, start: FS.val(x, 'in'), seconds: FS.spanOf(x),
                    mode: way.value, options}), (t) => {
                    bar.firstChild.style.width = Math.round((t.progress || 0) * 100) + '%';
                    status.textContent = T(t.text || 'Working…');
                });
                S.media[task.web] = {path: task.web, missing: false};
                await FS.probe(task.web);
                FS.takeLips(c.id, task.web, task.seconds);
                if (task.speech) {
                    S.media[task.speech] = {path: task.speech, missing: false};
                    const info = await FS.probe(task.speech);
                    const start = FS.clip(c.id).start;
                    const free = FS.audioTracks().find((tr) => !S.doc.clips.some((y) => y.track === tr.id && y.start < start + (info.duration || 1) && FS.end(y) > start));
                    await FS.addMedia(task.speech, {time: start, track: free ? free.id : FS.change('track', () => FS.addTrack('audio'))}, {mode: 'free'});
                }
                status.textContent = T('Done: the clip talks now ({0})', task.text || '');
                bar.firstChild.style.width = '100%';
                FS.message(T('The clip talks now'));
            } finally { go.disabled = false; }
        })}, '👄 ' + T('Make it talk'));
        const close = () => { if (!go.disabled || confirm(T('It goes on in the background. Close this window?'))) box.remove(); };
        const box = h('div', {class: 'fx-modal-back', dir: FS.hebrew() ? 'rtl' : 'ltr', onclick: (e) => { if (e.target === box) close(); }},
            h('div', {class: 'fx-modal', role: 'dialog'},
                h('div', {class: 'fx-modal-head'}, h('span', {}, '👄 ' + T('Lip sync')), h('span', {class: 'fx-dim'}, c.media),
                    h('span', {class: 'fx-spacer'}), h('button', {class: 'fx-icon', title: T('Close'), onclick: close}, '✕')),
                h('div', {class: 'fx-modal-body'},
                    sec(T('How'), T('the character needs a face looking towards the camera'), row(T('Way'), way)),
                    parts.sound, parts.text, parts.face,
                    opts.message ? h('div', {class: 'fx-dim'}, opts.message) : null),
                h('div', {class: 'fx-modal-foot'}, bar, status, h('div', {class: 'fx-row'}, go))));
        FS.UI.root.append(box);
    };

    const talks = (c) => c && (c.kind === 'video' || c.kind === 'image') && c.media;
    FS.clipActions.push({id: 'lips', label: '👄 ' + T('Lip sync: make it say something'), when: talks, run: (c) => FS.openLips(c)});
    FS.clipActions.push({id: 'lipsback', label: '↩ ' + T('Back to the original (before the lip sync)'), when: (c) => c && c.lips, run: (c) => FS.lipsBack(c.id)});
})();

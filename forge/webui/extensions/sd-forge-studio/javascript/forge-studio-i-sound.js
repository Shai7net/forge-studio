/* Exact sound ranges, multilingual audio prompts and a reusable, project-independent library. */
(function () {
    'use strict';
    const FS = window.ForgeStudio = window.ForgeStudio || {}, S = FS.state;
    const T = (...a) => FS.T(...a), UI = {}, recent = [];
    function destination(region) {
        const r = region || FS.region();
        return {project: S.project, time: r ? r.in : S.time, seconds: r ? r.out - r.in : null,
            region: r ? JSON.parse(JSON.stringify(r)) : null};
    }
    async function land(web, at, seconds, label) {
        if (S.project !== at.project) { FS.message(T('The sound is ready in its original project')); return; }
        S.media[web] = {path: web, missing: false};
        const info = await FS.probe(web);
        if (S.project !== at.project) return;
        let track = at.region && at.region.tracks.find((id) => FS.track(id) && FS.track(id).kind === 'audio');
        if (!track) {
            const free = FS.audioTracks().find((t) => !S.doc.clips.some((c) => c.track === t.id && c.start < at.time + (seconds || info.duration) && FS.end(c) > at.time));
            track = free ? free.id : (FS.audioTracks()[0] || {}).id;
        }
        return FS.addMedia(web, {time: at.time, track}, {exact: true, seconds: seconds || info.duration, extra: label ? {label} : {}});
    }
    function runner(button, status, work) {
        return FS.guard(async () => {
            if (!S.project) throw new Error(T('Open or create a project first'));
            button.disabled = true;
            try { await work(); } catch (e) { status(e.message); throw e; } finally { button.disabled = false; }
        });
    }
    async function saveSound(item) {
        await FS.api('sound/save', {name: item.project, web: item.web, label: item.label, kind: item.kind, prompt: item.prompt || ''});
        await FS.sound.refresh(); FS.message(T('Sound saved for reuse'));
    }
    async function generate(kind, words, at, status, source) {
        if (!at.project) throw new Error(T('Open or create a project first'));
        const seconds = at.seconds || (kind === 'music' ? parseFloat(UI.musicSeconds && UI.musicSeconds.value) || Math.max(5, FS.total() - at.time) : 5);
        status = status || ((text) => FS.busy && FS.busy(text));
        status(T('Writing the audio prompt in English…'));
        try {
            const body = {name: at.project, seconds};
            if (kind === 'music') body.caption = words;
            else Object.assign(body, {words}, source || {});
            const task = await FS.follow(await FS.api('sound/' + kind, body), (t) => status(t.prompt ? T('English prompt: {0}', t.prompt) + '\n' + t.text : t.text));
            const item = {project: at.project, web: task.web, label: (words || task.text).slice(0, 80), kind, prompt: task.prompt || words};
            recent.unshift(item); recent.splice(8);
            await land(task.web, at, seconds, item.label);
            if (!UI.keep || UI.keep.checked) await saveSound(item);
            renderRecent();
            status(T('Ready: {0}', task.text) + (task.prompt ? '\n' + T('English prompt: {0}', task.prompt) : ''));
            return task;
        } finally { if (FS.busy) FS.busy(null); }
    }
    FS.sound = {
        effectsFor: async (clip, words, status) => generate('effects', words || '',
            {project: S.project, time: clip.start, seconds: clip.duration, region: null}, status,
            {web: clip.media, start: FS.val(clip, 'in'), speed: FS.spanOf(clip) / clip.duration}),
        inRegion: async function (kind, region) {
            const at = destination(region);
            const words = await FS.ask(T(kind === 'music' ? 'Music for the selected range' : 'Sound effects for the selected range'), T('Describe what is heard in any language'));
            if (words) await generate(kind, words, at);
        },
        refresh: async function () { const r = await FS.api('sound/library'); UI.items = r.items || []; renderLibrary(); },
    };
    function renderRecent() {
        if (!UI.recent) return;
        UI.recent.replaceChildren(...recent.map((item) => FS.h('button', {class: 'fx-btn', onclick: FS.guard(() => saveSound(item))}, '💾 ' + T('Save sound for reuse') + ' · ' + item.label)));
    }
    function renderLibrary() {
        if (!UI.library) return;
        const text = (UI.search.value || '').toLowerCase(), kind = UI.filter.value;
        const items = (UI.items || []).filter((i) => (!kind || i.kind === kind) && (i.name + ' ' + i.prompt).toLowerCase().includes(text));
        UI.library.replaceChildren(); UI.count.textContent = T('{0} saved sounds', items.length);
        for (const item of items.slice(0, UI.limit || 40)) {
            const h = FS.h, preview = h('audio', {controls: '', preload: 'none', src: '/forge-studio/sound/file?id=' + encodeURIComponent(item.id)});
            preview.addEventListener('play', () => UI.library.querySelectorAll('audio').forEach((a) => { if (a !== preview) a.pause(); }));
            UI.library.append(h('div', {class: 'fx-saved-sound'}, h('div', {}, item.name),
                h('div', {class: 'fx-dim'}, `${(item.duration || 0).toFixed(2)}s · ${item.license}`), preview,
                h('div', {class: 'fx-row'},
                    h('button', {class: 'fx-btn', onclick: FS.guard(async () => {
                        if (!S.project) throw new Error(T('Open or create a project first'));
                        const at = destination(), added = await FS.api('sound/use', {name: at.project, id: item.id});
                        await land(added.web, at, at.seconds ? Math.min(at.seconds, item.duration) : null, item.name);
                    })}, '+ ' + T('Add to the timeline')),
                    h('button', {class: 'fx-btn', title: T('Remove from the sound library'), onclick: FS.guard(async () => {
                        preview.pause(); await FS.api('sound/remove', {id: item.id}); await FS.sound.refresh();
                    })}, '🗑 ' + T('Remove')))));
        }
        if (items.length > (UI.limit || 40)) UI.library.append(FS.h('button', {class: 'fx-btn', onclick: () => { UI.limit = (UI.limit || 40) + 40; renderLibrary(); }}, T('More')));
    }
    function build() {
        const h = FS.h;
        const line = () => { const el = h('div', {class: 'fx-dim', style: 'white-space:pre-wrap'}); return [el, (t) => { el.textContent = t || ''; }]; };
        UI.range = h('div', {class: 'fx-dim'});
        const updateRange = () => { const r = FS.region(); UI.range.textContent = r ? T('Selected: {0} seconds at {1}', (r.out - r.in).toFixed(2), FS.timecode(r.in)) : T('Drag on an empty lane to select time, then generate here or right-click the range.'); };
        FS.on('region', updateRange); updateRange();
        UI.voiceText = h('textarea', {class: 'fx-input fx-textarea', rows: 3, dir: 'auto', id: 'fx-voice-text', placeholder: T('What is said (Hebrew is given niqqud by itself)')});
        UI.voiceLang = h('select', {class: 'fx-select', id: 'fx-voice-lang'}); UI.voiceName = h('select', {class: 'fx-select', id: 'fx-voice-name'});
        const [voiceStatusEl, voiceStatus] = line(), voiceGo = h('button', {class: 'fx-primary'}, '🗣 ' + T('Make the narration'));
        voiceGo.addEventListener('click', runner(voiceGo, voiceStatus, async () => {
            const at = destination(); voiceStatus(T('Making the voice…'));
            const task = await FS.follow(await FS.api('sound/voice', {name: at.project, text: UI.voiceText.value, language: UI.voiceLang.value, voice: UI.voiceName.value}), (t) => voiceStatus(t.text));
            await land(task.web, at, null); voiceStatus(T('Ready: {0}', task.text));
        }));
        UI.musicWords = h('input', {class: 'fx-input', id: 'fx-music-words', dir: 'auto', placeholder: T('Describe the music in any language')});
        UI.musicSeconds = h('input', {class: 'fx-num', type: 'number', min: 0.04, max: 240, step: 0.1, id: 'fx-music-seconds', placeholder: T('to the end')});
        const [musicStatusEl, musicStatus] = line(), musicGo = h('button', {class: 'fx-primary'}, '🎵 ' + T('Make the music'));
        musicGo.addEventListener('click', runner(musicGo, musicStatus, () => generate('music', UI.musicWords.value, destination(), musicStatus)));
        UI.fxWords = h('input', {class: 'fx-input', id: 'fx-fx-words', dir: 'auto', placeholder: T('Describe the sound effect in any language')});
        const [fxStatusEl, fxStatus] = line(), fxGo = h('button', {class: 'fx-primary'}, '🔊 ' + T('Make sound effects'));
        fxGo.addEventListener('click', runner(fxGo, fxStatus, async () => {
            const r = FS.region(); if (r) return generate('effects', UI.fxWords.value, destination(r), fxStatus);
            const a = FS.gen.around();
            if (a.under && a.under.kind === 'video') return FS.sound.effectsFor(a.under, UI.fxWords.value, fxStatus);
            return generate('effects', UI.fxWords.value, destination(), fxStatus);
        }));
        UI.keep = h('input', {type: 'checkbox', checked: true}); UI.recent = h('div', {class: 'fx-sound-list'});
        UI.search = h('input', {class: 'fx-input', placeholder: T('Search saved sounds'), dir: 'auto'});
        UI.filter = h('select', {class: 'fx-select'}, ...[['', 'All sounds'], ['effects', 'Sound effects'], ['music', 'Music'], ['voice', 'Narration']].map(([v, t]) => h('option', {value: v}, T(t))));
        UI.search.addEventListener('input', renderLibrary); UI.filter.addEventListener('change', renderLibrary);
        UI.library = h('div', {class: 'fx-sound-list'}); UI.count = h('div', {class: 'fx-dim'});
        const [packStatusEl, packStatus] = line(), pack = h('button', {class: 'fx-btn'}, '⬇ ' + T('Download free sounds (CC0)'));
        pack.addEventListener('click', FS.guard(async () => {
            pack.disabled = true; packStatus(T('Downloading the free sound packs…'));
            try { const done = await FS.follow(await FS.api('sound/install', {}), (t) => packStatus(t.text)); await FS.sound.refresh(); packStatus(done.text); }
            finally { pack.disabled = false; }
        }));
        UI.note = h('div', {class: 'fx-dim'});
        FS.api('sound/options').then((o) => {
            for (const l of o.speech_languages || ['Hebrew', 'English']) UI.voiceLang.append(h('option', {value: l}, T(l)));
            for (const v of o.voices || []) UI.voiceName.append(h('option', {value: v}, v));
            const missing = []; if (!o.voice) missing.push(T('voices')); if (!o.music) missing.push('ACE-Step'); if (!o.effects) missing.push('MMAudio');
            if (missing.length) UI.note.textContent = T('Not installed here: {0}', missing.join(', '));
        }).catch(() => null);
        FS.sound.refresh().catch((e) => packStatus(e.message)); renderRecent();
        return h('div', {class: 'fx-panel-body'}, UI.note, UI.range,
            h('div', {class: 'fx-group'}, h('div', {class: 'fx-group-title'}, '🗣 ' + T('Narration')), UI.voiceText, h('div', {class: 'fx-row'}, UI.voiceLang, UI.voiceName), voiceGo, voiceStatusEl),
            h('div', {class: 'fx-group'}, h('div', {class: 'fx-group-title'}, '🎵 ' + T('Music')), UI.musicWords, h('div', {class: 'fx-row'}, h('span', {class: 'fx-dim'}, T('Seconds')), UI.musicSeconds), musicGo, musicStatusEl),
            h('div', {class: 'fx-group'}, h('div', {class: 'fx-group-title'}, '🔊 ' + T('Sound effects')), UI.fxWords, fxGo, fxStatusEl),
            h('label', {class: 'fx-check'}, UI.keep, ' ', T('Save generated sounds for reuse')), UI.recent,
            h('div', {class: 'fx-group'}, h('div', {class: 'fx-group-title'}, '📚 ' + T('Sound library')), UI.search, UI.filter, pack, packStatusEl, UI.count, UI.library),
            h('div', {class: 'fx-dim'}, T('Audio prompts are written in English for the sound model. Each generation waits its turn for the graphics card.')));
    }
    FS.clipActions.push({id: 'sound', label: '🔊 ' + T('Sound effects for this clip (MMAudio)'), when: (c) => c.kind === 'video' && c.media,
        run: async (c) => { const words = await FS.ask(T('Sound effects for this clip'), T('Describe what is heard in any language'), ''); if (words !== null) await FS.sound.effectsFor(c, words); }});
    FS.clipActions.push({id: 'save-sound', label: '💾 ' + T('Save sound for reuse'), when: (c) => c.kind === 'audio' && c.media,
        run: async (c) => { const project = S.project, label = await FS.ask(T('Save sound for reuse'), T('Name this sound'), c.label || c.media); if (label) await saveSound({project, web: c.media, label, kind: 'effects'}); }});
    FS.panels.push({id: 'sound', icon: '♪', label: 'Sound', build});
})();

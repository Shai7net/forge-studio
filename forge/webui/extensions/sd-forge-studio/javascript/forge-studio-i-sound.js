/* Forge Studio - sound: narration (Hebrew is given niqqud by itself), music from ACE-Step, sound effects from a
 * clip's picture (MMAudio). Each lands as a sound clip at the playhead (effects under their clip), on the first
 * sound track that is free there. Every request waits its turn for the graphics card.
 */
(function () {
    'use strict';
    const FS = window.ForgeStudio = window.ForgeStudio || {};
    const S = FS.state;
    const T = (...a) => FS.T(...a);
    const UI = {};

    function freeAudio(a, b) {
        for (const t of FS.audioTracks()) if (!S.doc.clips.some((c) => c.track === t.id && c.start < b && FS.end(c) > a)) return t.id;
        return FS.change('track', () => FS.addTrack('audio'));
    }

    async function land(web, time) {
        S.media[web] = {path: web, missing: false};
        const info = await FS.probe(web);
        await FS.addMedia(web, {time, track: freeAudio(time, time + (info.duration || 5))}, {mode: 'free'});
    }

    function runner(button, status, work) {
        return FS.guard(async () => {
            if (!S.project) throw new Error(T('Open or create a project first'));
            button.disabled = true;
            try { await work(); } catch (e) { status(''); throw e; } finally { button.disabled = false; }
        });
    }

    FS.sound = {
        effectsFor: async function (clip, words, status) {
            status = status || ((t) => FS.busy(t));
            status(T('MMAudio is listening to the picture…'));
            const task = await FS.follow(await FS.api('sound/effects', {name: S.project, web: clip.media, words: words || ''}), (t) => status(t.text));
            await land(task.web, clip.start - FS.val(clip, 'in') / FS.val(clip, 'speed'));
            status('');
            FS.message(T('Sound effects are under the clip'));
        },
    };

    function build() {
        const h = FS.h;
        const line = () => { const el = h('div', {class: 'fx-dim'}); return [el, (t) => { el.textContent = t || ''; }]; };

        UI.voiceText = h('textarea', {class: 'fx-input fx-textarea', rows: 3, dir: 'auto', id: 'fx-voice-text', placeholder: T('What is said (Hebrew is given niqqud by itself)')});
        UI.voiceLang = h('select', {class: 'fx-select', id: 'fx-voice-lang'});
        UI.voiceName = h('select', {class: 'fx-select', id: 'fx-voice-name'});
        const [voiceStatusEl, voiceStatus] = line();
        const voiceGo = h('button', {class: 'fx-primary'}, '🗣 ' + T('Make the narration'));
        voiceGo.addEventListener('click', runner(voiceGo, voiceStatus, async () => {
            const at = S.time;
            voiceStatus(T('Making the voice…'));
            const task = await FS.follow(await FS.api('sound/voice', {name: S.project, text: UI.voiceText.value, language: UI.voiceLang.value, voice: UI.voiceName.value}),
                (t) => voiceStatus(t.text));
            await land(task.web, at);
            voiceStatus(T('At the playhead: {0}', task.text));
        }));

        UI.musicWords = h('input', {class: 'fx-input', id: 'fx-music-words', dir: 'auto', placeholder: T('Music in English words, e.g. calm cinematic piano and soft strings')});
        UI.musicSeconds = h('input', {class: 'fx-num', type: 'number', min: 5, max: 240, step: 1, id: 'fx-music-seconds', placeholder: T('to the end')});
        const [musicStatusEl, musicStatus] = line();
        const musicGo = h('button', {class: 'fx-primary'}, '🎵 ' + T('Make the music'));
        musicGo.addEventListener('click', runner(musicGo, musicStatus, async () => {
            const at = S.time;
            const seconds = parseFloat(UI.musicSeconds.value) || Math.ceil(Math.max(5, FS.total() - at));
            musicStatus(T('ACE-Step is composing…'));
            const task = await FS.follow(await FS.api('sound/music', {name: S.project, caption: UI.musicWords.value, seconds}), (t) => musicStatus(t.text));
            await land(task.web, at);
            musicStatus(T('At the playhead: {0}', task.text));
        }));

        UI.fxWords = h('input', {class: 'fx-input', id: 'fx-fx-words', dir: 'auto', placeholder: T('What is heard (optional, English): footsteps on gravel, wind')});
        const [fxStatusEl, fxStatus] = line();
        const fxGo = h('button', {class: 'fx-primary'}, '🔊 ' + T('Sound for the clip under the playhead'));
        fxGo.addEventListener('click', runner(fxGo, fxStatus, async () => {
            const a = FS.gen.around();
            if (!a.under || a.under.kind !== 'video') throw new Error(T('There is no clip under the playhead'));
            await FS.sound.effectsFor(a.under, UI.fxWords.value, fxStatus);
        }));

        UI.note = h('div', {class: 'fx-dim'});
        FS.api('sound/options').then((o) => {
            for (const l of o.speech_languages || ['Hebrew', 'English']) UI.voiceLang.append(h('option', {value: l}, T(l)));
            for (const v of o.voices || []) UI.voiceName.append(h('option', {value: v}, v));
            const missing = [];
            if (!o.voice) missing.push(T('voices'));
            if (!o.music) missing.push('ACE-Step');
            if (!o.effects) missing.push('MMAudio');
            if (missing.length) UI.note.textContent = T('Not installed here: {0}', missing.join(', '));
        }).catch(() => null);

        return h('div', {class: 'fx-panel-body'}, UI.note,
            h('div', {class: 'fx-group'}, h('div', {class: 'fx-group-title'}, '🗣 ' + T('Narration')), UI.voiceText,
                h('div', {class: 'fx-row'}, UI.voiceLang, UI.voiceName), voiceGo, voiceStatusEl),
            h('div', {class: 'fx-group'}, h('div', {class: 'fx-group-title'}, '🎵 ' + T('Music')), UI.musicWords,
                h('div', {class: 'fx-row'}, h('span', {class: 'fx-dim'}, T('Seconds')), UI.musicSeconds), musicGo, musicStatusEl),
            h('div', {class: 'fx-group'}, h('div', {class: 'fx-group-title'}, '🔊 ' + T('Sound effects')), UI.fxWords, fxGo, fxStatusEl),
            h('div', {class: 'fx-dim'}, T('Each runs on the graphics card in turn, after anything Forge is drawing.')));
    }

    FS.clipActions.push({id: 'sound', label: '🔊 ' + T('Sound effects for this clip (MMAudio)'), when: (c) => c.kind === 'video' && c.media,
        run: async (c) => { const words = await FS.ask(T('Sound effects for this clip'), T('What is heard (optional, English): footsteps on gravel, wind'), ''); if (words !== null) await FS.sound.effectsFor(c, words); }});
    FS.panels.push({id: 'sound', icon: '♪', label: 'Sound', build});
})();

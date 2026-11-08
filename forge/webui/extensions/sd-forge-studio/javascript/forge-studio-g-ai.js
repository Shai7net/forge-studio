/* Forge Studio - AI in the edit. Video Studio (Wan 2.2) makes clips on the server; here a placeholder clip of the
 * right length goes onto the timeline at once (striped, with its progress) and turns into the clip when it is
 * ready, also after a reload (the server keeps the requests in generations.json). Every clip made again keeps its
 * earlier takes (the 🎞 badge on it switches between them). The ✨ at each cut, the clip menu and the inspector
 * offer what fits where they are: a new shot, a continuation, a transition, another take, a cut-out.
 */
(function () {
    'use strict';
    const FS = window.ForgeStudio = window.ForgeStudio || {};
    const S = FS.state;
    const T = (...a) => FS.T(...a);
    const val = (c, k) => FS.val(c, k);
    const q = encodeURIComponent;
    const G = FS.gen = {records: [], seconds: 5, quality: '', sound: true};

    /* ---- asking for words, in a small box ---- */

    /* choice (optional): {label, options: [[value, text]], value, number?: {label, value, min, max, step}}; then the
     * answer is {text, choice, number} */
    FS.ask = function (title, placeholder, value, note, choice) {
        return new Promise((resolve) => {
            const h = FS.h;
            const area = h('textarea', {class: 'fx-input fx-textarea', dir: 'auto', rows: 4, placeholder: placeholder || ''}, value || '');
            const select = choice ? h('select', {class: 'fx-select'}, ...choice.options.map(([v, t]) =>
                h('option', {value: v, selected: v === choice.value ? '' : null}, t))) : null;
            const num = choice && choice.number ? h('input', {class: 'fx-num', type: 'number', min: choice.number.min, max: choice.number.max,
                step: choice.number.step || 0.5, value: choice.number.value}) : null;
            const done = (v) => { box.remove(); resolve(choice && v !== null ? {text: v, choice: select.value, number: num ? +num.value : null} : v); };
            const box = h('div', {class: 'fx-ask', dir: FS.hebrew() ? 'rtl' : 'ltr'},
                h('div', {class: 'fx-group'},
                    h('div', {class: 'fx-group-title'}, title), note ? h('div', {class: 'fx-dim'}, note) : null,
                    select ? h('label', {class: 'fx-row'}, h('span', {}, choice.label), select) : null,
                    num ? h('label', {class: 'fx-row'}, h('span', {}, choice.number.label), num, h('span', {class: 'fx-dim'}, T('seconds'))) : null, area,
                    h('div', {class: 'fx-row'},
                        h('button', {class: 'fx-primary', onclick: () => done(area.value.trim())}, '✨ ' + T('Go')),
                        h('button', {class: 'fx-btn', onclick: () => done(null)}, T('Cancel')))));
            area.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) done(area.value.trim());
                if (e.key === 'Escape') done(null);
            });
            FS.UI.root.append(box);
            area.focus();
        });
    };

    /* ---- where things are around the playhead ---- */

    G.around = function () {
        const t = S.time, half = 0.5 / FS.fps();
        const main = FS.mainTrack();
        const onMain = FS.clipsOn(main.id);
        const order = FS.visualTracks().filter((tr) => !tr.hidden).map((tr) => tr.id);
        const under = S.doc.clips.filter((c) => order.includes(c.track) && (c.kind === 'video' || c.kind === 'image') && t >= c.start && t < FS.end(c))
            .sort((a, b) => order.indexOf(b.track) - order.indexOf(a.track))[0] || null;
        const before = onMain.filter((c) => FS.end(c) <= t + half).pop() || null;
        const after = onMain.find((c) => c.start >= t - half) || null;
        const {width, height} = S.doc.settings;
        return {t, main, under, before, after, width, height};
    };
    G.sourceAt = (c, t) => FS.sourceTime(c, t);
    const lastFrame = (c) => Math.max(0, val(c, 'in') + FS.spanOf(c) - 1 / FS.fps());

    /* ---- placeholders ---- */

    function placeholder(time, seconds, trackId, text) {
        return FS.change('generate', (d) => {
            const clip = {id: FS.newId('g'), track: trackId || FS.mainTrack().id, kind: 'gen', start: FS.snapTime(time),
                duration: seconds, gen: {text: text || '', progress: 0}};
            d.clips.push(clip);
            FS.place(clip, FS.track(clip.track).main ? 'insert' : 'free');
            return clip;
        });
    }

    /* Video Studio makes 1 + 4k frames at 16 a second; the clip is that long */
    const madeSeconds = (s) => (4 * Math.max(1, Math.round(s * 16 / 4))) / 16;

    G.request = async function (body, opts) {
        if (!S.project) throw new Error(T('Open or create a project first'));
        opts = opts || {};
        if (body.prompt) body.prompt = await FS.english(body.prompt, body.kind === 'text' ? 't2v' : 'i2v');
        const seconds = madeSeconds(body.seconds || G.seconds);
        let clip = opts.take ? FS.clip(opts.take) : placeholder(opts.time != null ? opts.time : S.time, seconds, opts.track, body.prompt);
        const payload = Object.assign({name: S.project, seconds, quality: G.quality || null, sound: G.sound,
            width: S.doc.settings.width, height: S.doc.settings.height}, body, {placeholder: {clip: clip.id, take: !!opts.take}});
        let record;
        try {
            record = await FS.api('generate', payload);
        } catch (e) {
            if (!opts.take) FS.change('generate', (d) => { d.clips = d.clips.filter((c) => c.id !== clip.id); FS.closeGaps(); });
            throw e;
        }
        if (opts.take) FS.change('take', () => { FS.clip(clip.id).pendingTake = record.id; });
        else FS.change('generate', () => { FS.clip(clip.id).gen = {id: record.id, text: T('Waiting'), progress: 0}; });
        G.records.push(record);
        G.poll(800);
        FS.message(T('Asked: it goes where the striped clip is when it is ready'));
        return record;
    };

    /* ---- following the requests ---- */

    let timer = null;
    G.poll = function (delay) {
        clearTimeout(timer);
        timer = setTimeout(G.refresh, delay == null ? 2500 : delay);
    };

    G.refresh = FS.guard(async function () {
        if (!S.project) return;
        const r = await FS.api('generations?name=' + q(S.project));
        G.records = r.generations;
        let busy = false;
        for (const g of G.records) {
            const clipId = g.placeholder && g.placeholder.clip;
            const clip = clipId && FS.clip(clipId);
            if (g.state === 'waiting' || g.state === 'running') {
                busy = true;
                if (clip && clip.kind === 'gen') {
                    const live = g.live || {};
                    clip.gen = {id: g.id, text: g.state === 'waiting' ? T('Waiting for the card') : (live.text || ''), progress: live.progress};
                    FS.liveEdit();
                }
            } else if (g.state === 'done' && g.video) {
                await place(g, clip);
            } else if ((g.state === 'error' || g.state === 'cancelled') && clip && clip.kind === 'gen') {
                FS.change('generate', (d) => { d.clips = d.clips.filter((c) => c.id !== clip.id); FS.closeGaps(); });
                if (g.state === 'error') FS.message(T('Making the clip failed: {0}', g.message || ''), 'error');
                await FS.api('generate/placed', {name: S.project, id: g.id});
            }
        }
        drawList();
        if (busy || S.doc.clips.some((c) => c.kind === 'gen')) G.poll();
    });

    async function place(g, clip) {
        const added = await FS.api('project/add', {name: S.project, path: g.video});
        S.media[added.web] = {path: g.video, missing: false};
        const info = await FS.probe(added.web);
        FS.change('generated', () => {
            const c = clip && FS.clip(clip.id);
            if (c && c.kind === 'gen') {
                c.kind = 'video';
                c.media = added.web;
                c.in = 0;
                c.duration = info.duration || c.duration;
                c.takes = [added.web];
                delete c.gen;
                FS.closeGaps();
            } else if (c && g.placeholder.take) {
                c.takes = c.takes && c.takes.length ? c.takes : [c.media];
                if (!c.takes.includes(added.web)) c.takes.push(added.web);
                c.media = added.web;
                c.in = 0;
                c.duration = Math.min(c.duration, (info.duration || c.duration) / val(c, 'speed'));
                delete c.pendingTake;
                FS.closeGaps();
            }
        });
        await FS.api('generate/placed', {name: S.project, id: g.id, web: added.web});
        FS.message(clip ? T('The new clip is on the timeline') : T('The new clip is in the project (its placeholder was removed)'));
        if (FS.refreshLibrary) FS.refreshLibrary();
    }

    /* ---- the kinds of request ---- */

    G.newShot = async function (time, track) {
        const prompt = await FS.ask(T('A new shot from words'), T('What happens in the shot (Hebrew or English)'), '',
            T('Forge draws its first frame with the checkpoint chosen at the top, then Wan animates it.'));
        if (!prompt) return;
        await G.request({kind: 'text', prompt}, {time, track});
    };

    G.fromFrame = async function (clip, time) {
        const prompt = await FS.ask(T('A new shot from this frame'), T('What happens next (Hebrew or English)'));
        if (!prompt) return;
        await G.request({kind: 'picture', prompt, start: {web: clip.media, seconds: G.sourceAt(clip, time)}},
            {time: FS.end(clip), track: clip.track});
    };

    /* a continuation is made by the tool that made the clip (Wan, AnimateDiff, Deforum: the server reads the clip's
     * own settings beside it), so it looks like the clip; the box can choose another */
    G.continueClip = async function (clip) {
        const made = await FS.api(`generate/made-with?name=${q(S.project)}&web=${q(clip.media)}`).catch(() => ({}));
        const engine = made.engine || 'wan';
        const answer = await FS.ask(T('Continue this shot'), T('What happens next (Hebrew or English)'), made.prompt || '',
            made.label ? T('Made with {0}: the continuation is made the same way, from the last frame.', made.label) : null,
            {label: T('Continue with'), value: engine, options: [
                ['animatediff', 'AnimateDiff' + (engine === 'animatediff' ? ' ✓' : '')],
                ['deforum', 'Deforum' + (engine === 'deforum' ? ' ✓' : '')],
                ['wan', 'Wan (Video Studio)' + (engine === 'wan' ? ' ✓' : '')]]});
        if (!answer || !answer.text) return;
        await G.request({kind: 'continue', prompt: answer.text, engine: answer.choice,
            start: {web: clip.media, seconds: lastFrame(clip)}}, {time: FS.end(clip), track: clip.track});
    };

    /* a join, frame to frame: from the last frame of the first clip to the first frame of the second, by Wan (first +
     * last frame) or AnimateDiff (its image-to-video with a first and a last picture, in the AnimateDiff clip's own
     * settings) - AnimateDiff when either clip is AnimateDiff's */
    G.transition = async function (left, right) {
        const madeWith = (c) => FS.api(`generate/made-with?name=${q(S.project)}&web=${q(c.media)}`).catch(() => ({}));
        const [a, b] = await Promise.all([madeWith(left), madeWith(right)]);
        const engine = a.engine === 'animatediff' || b.engine === 'animatediff' ? 'animatediff' : 'wan';
        const answer = await FS.ask(T('An AI transition between the two shots'),
            T('Describe one continuous shot from the first to the second (no cuts)'), a.prompt || '',
            T('From the last frame of the first clip to the first frame of the second.'),
            {label: T('Make it with'), value: engine, options: [
                ['animatediff', 'AnimateDiff' + (engine === 'animatediff' ? ' ✓' : '')],
                ['wan', 'Wan (Video Studio)' + (engine === 'wan' ? ' ✓' : '')]],
             number: {label: T('Length of the join'), value: G.joinSeconds || 2, min: 1, max: 7.5, step: 0.5}});
        if (!answer || !answer.text) return;
        G.joinSeconds = Math.max(1, Math.min(7.5, answer.number || 2));
        await G.request({kind: 'between', prompt: answer.text, engine: answer.choice, seconds: G.joinSeconds,
            start: {web: left.media, seconds: lastFrame(left)},
            end: {web: right.media, seconds: val(right, 'in')}}, {time: FS.end(left), track: left.track});
    };
    /* the clip right after `c` on its track (touching it or after a gap), if it is a picture or a video */
    G.nextOf = (c) => FS.clipsOn(c.track).filter((x) => x.id !== c.id && x.start >= FS.end(c) - 0.5 / FS.fps())
        .sort((x, y) => x.start - y.start).find(isPicture) || null;

    G.anotherTake = async function (clip) {
        await G.request({kind: 'take', take_of: clip.media, prompt: ''}, {take: clip.id});
    };

    /* ---- where they are offered ---- */

    const isVideo = (c) => c && c.kind === 'video' && c.media;
    const isPicture = (c) => c && (c.kind === 'video' || c.kind === 'image') && c.media;
    const madeByWan = (c) => isVideo(c) && /-(t2v|i2v|flf|multi|picture|text|continue|between)-\d+/.test(c.media + (c.takes || []).join(' '));

    FS.cutActions = [
        {label: '⇄ ' + T('Dissolve between them (1 s)'), when: (l, r) => r && !(r.transitionIn > 0), run: (l, r) => FS.transition(r.id, 1, 'dissolve')},
        {label: '⬛ ' + T('Dip to black (1 s)'), when: (l, r) => r && !(r.transitionIn > 0), run: (l, r) => FS.transition(r.id, 1, 'black')},
        {label: '⬅ ' + T('Slide in from the right (0.6 s)'), when: (l, r) => r && !(r.transitionIn > 0), run: (l, r) => FS.transition(r.id, 0.6, 'slide:right')},
        {label: '⇇ ' + T('Push from the right (0.6 s)'), when: (l, r) => r && !(r.transitionIn > 0), run: (l, r) => FS.transition(r.id, 0.6, 'push:right')},
        {label: '⇄ ' + T('Remove the transition'), when: (l, r) => r && r.transitionIn > 0, run: (l, r) => FS.transition(r.id, 0)},
        {label: '✨ ' + T('AI transition between these shots'), when: (l, r) => isPicture(l) && isPicture(r), run: (l, r) => G.transition(l, r)},
        {label: '✨ ' + T('Continue the shot on the left'), when: (l) => isVideo(l), run: (l) => G.continueClip(l)},
        {label: '✨ ' + T('A new shot here, from words'), run: (l) => G.newShot(FS.end(l), l.track)},
    ];
    /* 🎯 auto reframe: BiRefNet finds the subject through the clip, and the clip fills the frame following it */
    G.reframe = async function (c) {
        const task = await FS.follow(await FS.api('layers/reframe', {name: S.project, web: c.media, start: val(c, 'in'), seconds: FS.spanOf(c)}),
            (t) => FS.busy && FS.busy(T('Finding the subject… {0}', t.text || '')));
        FS.busy && FS.busy(null);
        await FS.probe(c.media);
        const n = FS.applyReframe(c.id, task.points || []);
        FS.message(n ? T('The clip fills the frame and follows the subject ({0} keyframes)', n) : T('No subject was found in this clip'));
    };
    const reshaped = (c) => {
        const info = S.info[c.media];
        return isVideo(c) && info && info.width && Math.abs(info.width / info.height - S.doc.settings.width / S.doc.settings.height) > 0.02;
    };

    FS.clipActions = [
        {id: 'take', label: '🎞 ' + T('Make it again (another take)'), when: madeByWan, run: (c) => G.anotherTake(c)},
        {id: 'continue', label: '➡ ' + T('Continue this shot'), when: isVideo, run: (c) => G.continueClip(c)},
        {id: 'join', label: '🔗 ' + T('Join to the next clip (AI, frame to frame)'), when: (c) => isPicture(c) && G.nextOf(c),
            run: (c) => G.transition(c, G.nextOf(c))},
        {id: 'fromframe', label: '🎬 ' + T('A new shot from the frame at the playhead'), when: (c) => isPicture(c) && S.time >= c.start && S.time < FS.end(c),
            run: (c) => G.fromFrame(c, S.time)},
        {id: 'reframe', label: '🎯 ' + T('Fill the frame and follow the subject (auto reframe)'), when: reshaped, run: (c) => G.reframe(c)},
    ];

    /* ---- the AI panel ---- */

    const UI = {};
    function build() {
        const h = FS.h;
        UI.prompt = h('textarea', {class: 'fx-input fx-textarea', rows: 4, dir: 'auto', id: 'fx-ai-prompt',
            placeholder: T('What happens in the shot (Hebrew or English)')});
        UI.seconds = h('input', {class: 'fx-num', type: 'number', min: 1, max: 7.5, step: 0.25, value: G.seconds, id: 'fx-ai-seconds'});
        UI.seconds.addEventListener('change', () => { G.seconds = +UI.seconds.value || 5; });
        UI.quality = h('select', {class: 'fx-select', id: 'fx-ai-quality'});
        UI.quality.addEventListener('change', () => { G.quality = UI.quality.value; });
        UI.sound = h('input', {type: 'checkbox', checked: G.sound, id: 'fx-ai-sound'});
        UI.sound.addEventListener('change', () => { G.sound = UI.sound.checked; });
        const go = (fn) => FS.guard(async () => {
            const prompt = UI.prompt.value.trim();
            if (!prompt) throw new Error(T('Write what happens in the shot'));
            await fn(prompt, G.around());
        });
        UI.list = h('div', {class: 'fx-genlist'});
        FS.api('generate/options').then((o) => {
            UI.quality.append(h('option', {value: ''}, T('As in Video Studio')));
            for (const x of o.qualities || []) UI.quality.append(h('option', {value: x}, x));
            if (!o.ok) UI.list.prepend(h('div', {class: 'fx-dim'}, o.message));
        }).catch(() => null);
        return h('div', {class: 'fx-panel-body'},
            h('div', {class: 'fx-group'},
                h('div', {class: 'fx-group-title'}, '✨ ' + T('Make a shot with Wan 2.2')),
                UI.prompt,
                h('div', {class: 'fx-row'}, h('span', {class: 'fx-dim'}, T('Seconds')), UI.seconds, UI.quality),
                h('label', {class: 'fx-check'}, UI.sound, ' ', T('With AI sound (MMAudio)')),
                h('button', {class: 'fx-primary', onclick: go((p) => G.request({kind: 'text', prompt: p}))}, '✨ ' + T('New shot at the playhead')),
                h('button', {class: 'fx-btn', onclick: go(async (p, a) => {
                    if (!a.under) throw new Error(T('There is no clip under the playhead'));
                    await G.request({kind: 'picture', prompt: p, start: {web: a.under.media, seconds: G.sourceAt(a.under, a.t)}}, {time: FS.end(a.under), track: a.under.track});
                })}, '🎬 ' + T('From the frame under the playhead')),
                h('button', {class: 'fx-btn', onclick: go(async (p, a) => {
                    if (!isVideo(a.before)) throw new Error(T('There is no clip before the playhead'));
                    await G.request({kind: 'continue', prompt: p, start: {web: a.before.media, seconds: lastFrame(a.before)}}, {time: FS.end(a.before), track: a.before.track});
                })}, '➡ ' + T('Continue the shot before the playhead')),
                h('button', {class: 'fx-btn', onclick: go(async (p, a) => {
                    if (!a.before || !a.after || Math.abs(FS.end(a.before) - a.after.start) > 1 / FS.fps()) throw new Error(T('Put the playhead on a cut between two clips'));
                    await G.request({kind: 'between', prompt: p, start: {web: a.before.media, seconds: lastFrame(a.before)},
                        end: {web: a.after.media, seconds: val(a.after, 'in')}}, {time: FS.end(a.before), track: a.before.track});
                })}, '⇄ ' + T('Transition at the cut under the playhead')),
                h('div', {class: 'fx-dim'}, T('Tip: the ✨ at every cut on the timeline does these too. Made with the settings you last chose in Video Studio.'))),
            h('div', {class: 'fx-group-title'}, T('Requests')),
            UI.list);
    }

    const STATE = {waiting: 'Waiting', running: 'Making', done: 'Ready', placed: 'On the timeline', error: 'Failed', cancelled: 'Stopped'};
    function drawList() {
        if (!UI.list) return;
        const h = FS.h;
        UI.list.querySelectorAll('.fx-gen').forEach((e) => e.remove());
        for (const g of G.records.slice().reverse().slice(0, 15)) {
            const live = g.live || {};
            const pct = live.progress != null ? Math.round(live.progress * 100) : null;
            UI.list.append(h('div', {class: 'fx-gen' + (g.state === 'running' ? ' running' : '')},
                h('div', {class: 'fx-row'}, h('b', {}, T(STATE[g.state] || g.state)),
                    h('span', {class: 'fx-dim'}, `${g.seconds}s · seed ${g.seed}`), h('span', {class: 'fx-spacer'}),
                    (g.state === 'waiting' || g.state === 'running') ? h('button', {class: 'fx-btn', onclick: FS.guard(async () => {
                        await FS.api('generate/cancel', {name: S.project, id: g.id});
                        G.poll(500);
                    })}, T('Stop')) : null),
                g.state === 'running' ? h('div', {class: 'fx-bar'}, h('div', {style: `width:${pct || 2}%`})) : null,
                g.state === 'running' && live.text ? h('div', {class: 'fx-dim'}, live.text) : null,
                g.state === 'error' ? h('div', {class: 'fx-gen-error'}, g.message || '') : null,
                h('div', {class: 'fx-prompt', dir: 'auto'}, g.prompt)));
        }
    }

    FS.panels.push({id: 'ai', icon: '✨', label: 'AI', build});
    FS.on('project', () => { G.records = []; drawList(); G.poll(400); });
})();

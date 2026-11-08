/* Time Strata - what the program does: the project, the chains and their frames on the graphics card, the scene
 * at any moment of the shot, playing, and recording. The page (strata-5-ui.js) only shows it and asks for it.
 */
(function () {
    'use strict';
    const Core = window.StrataCore, S = window.Strata = window.Strata || {};

    const E = S.engine = {
        options: null, project: null, doc: null, renderer: null, time: 0, playing: false, dirty: false,
        free: null,                 // a camera the user moves with the mouse, outside the keys: {yaw, pitch, distance, target}
        loading: null,              // {done, total} while frames go to the graphics card
        loaded: {},                 // chain id -> {make, base (first layer), frames}
        budget: 640,                // megabytes of the graphics card the frames may take
        listeners: new Set(),
    };

    E.on = fn => { E.listeners.add(fn); return () => E.listeners.delete(fn); };
    E.emit = what => { for (const fn of E.listeners) { try { fn(what); } catch (e) { console.warn('[Time Strata]', e); } } };

    /* ---- the server ---- */

    E.api = async function (path, body, raw) {
        const init = body === undefined ? {} : raw ? { method: 'POST', body } :
            { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
        const response = await fetch('./time-strata/' + path, init);
        let answer = null;
        try { answer = await response.json(); } catch (e) { /* no body */ }
        if (!response.ok) {
            const detail = answer && answer.detail;
            throw new Error(typeof detail === 'string' ? detail : (detail ? JSON.stringify(detail) : `${response.status} ${response.statusText}`));
        }
        return answer;
    };

    E.fileUrl = path => './time-strata/file/' + encodeURIComponent(E.project) + '/' + path.split('/').map(encodeURIComponent).join('/');

    /* a worker's timer: a hidden page holds back setTimeout, a worker's does not */
    let sleeper = null;
    E.sleep = ms => new Promise(resolve => {
        try {
            if (!sleeper) sleeper = new Worker(URL.createObjectURL(new Blob(['onmessage=e=>setTimeout(()=>postMessage(e.data[0]),e.data[1])'])));
            const id = Math.random();
            const done = e => { if (e.data === id) { sleeper.removeEventListener('message', done); resolve(); } };
            sleeper.addEventListener('message', done);
            sleeper.postMessage([id, ms]);
        } catch (e) { setTimeout(resolve, ms); }
    });

    /* waits for a job, telling `progress` how it goes; returns its result */
    E.waitJob = async function (job, progress) {
        while (true) {
            const now = await E.api('job?id=' + job.id);
            if (progress) progress(now);
            if (now.status === 'done') return now.result;
            if (now.status === 'failed') throw new Error(now.error || 'failed');
            if (now.status === 'cancelled') throw new Error('cancelled');
            await E.sleep(700);
        }
    };

    /* ---- the project ---- */

    E.start = async function () {
        E.options = await E.api('options');
        const list = await E.api('projects');
        let wanted = null;
        try { wanted = localStorage.getItem('time-strata-project'); } catch (e) { /* private window */ }
        const names = list.projects.map(p => p.name);
        if (wanted && names.includes(wanted)) await E.open(wanted);
        else if (names.length) await E.open(names[0]);
        return list.projects;
    };

    E.open = async function (name) {
        if (E.steering.on) E.stopSteering();
        await E.saveNow();
        const doc = await E.api('project?name=' + encodeURIComponent(name));
        E.project = name; E.doc = upgrade(doc); E.time = 0; E.free = null; E.followAdjust = null; E.loaded = {};
        E.setDqt(E.doc.steer.dqt);
        try { localStorage.setItem('time-strata-project', name); } catch (e) { /* private window */ }
        E.emit('project');
        await E.loadWorld();
        await E.loadFrames();
    };

    E.create = async function (name) {
        const doc = await E.api('project/create', { name });
        await E.open(doc.name);
    };

    function upgrade(doc) {
        doc.shot = Object.assign({ duration: 10, fps: 30, width: 1080, height: 1920 }, doc.shot);
        doc.world = Object.assign({ kind: 'sky', eye: 1.6, radius: 90, turn: 0, fog: 0.25 }, doc.world);
        doc.camera = Object.assign({ follow: null, keys: [] }, doc.camera);
        // a camera that follows the figure is the way to start; a project made before it keeps its own keys
        if (!doc.camera.mode) doc.camera.mode = doc.camera.keys.length ? 'keys' : 'follow';
        if (!Array.isArray(doc.camera.follow_keys) || !doc.camera.follow_keys.length) doc.camera.follow_keys = Core.followPreset('front', doc.shot.duration);
        doc.steer = Object.assign({ turn: 45, climb: 25, flip_turn: false, flip_climb: false, screen: true, dqt: false, dqt_camera: true, dqt_range: 3 }, doc.steer);
        doc.knife = Object.assign({ on: false, keys: [] }, doc.knife);
        // a frame dissolves before it reaches the lens: inside a chain the frames ahead are then read as slices,
        // instead of the nearest one filling the picture
        doc.look = Object.assign({ near: 0.45, far: 1.2 }, doc.look);
        doc.record = Object.assign({ size: '1080x1920', fps: 30, kind: 'mp4-h264', quality: 17, supersample: 1, name: '', music_start: 0 }, doc.record);
        doc.plan = Object.assign({ seconds: 6, speed: 1.2, rate: 24, ahead: 6 }, doc.plan);
        for (const c of doc.chains || []) fillChain(c);
        return doc;
    }

    function fillChain(c) {
        c.spine = Object.assign({}, Core.SPINE_DEFAULTS, c.spine);
        c.look = Object.assign({ fade: 0.35, tint: '#8fb8ff', tint_amount: 0.0, glow: 0, glow_color: '#5ff2ff', rib: 0.25, reverse: false, shadow: 0.35 }, c.look);
        c.cut = Object.assign({ start: 0, end: null, fps: null, max_frames: 600, matte: 'auto', model: null, key_colour: null, crop: 'auto', upscale: null,
                                align: 'centre', scale: 'steady', ground: 12, height: 768, figure: 0.84, baseline: 0.95 }, c.cut);
        c.time = Object.assign({}, Core.TIME_DEFAULTS, c.time);
        delete c.grow;
        if (c.size == null) c.size = 1.7;
        if (c.from == null) c.from = 0;
        if (c.offset == null) c.offset = 0;
        if (c.visible == null) c.visible = true;
        return c;
    }

    E.touch = function () {
        E.dirty = true;
        clearTimeout(E._saveTimer);
        E._saveTimer = setTimeout(() => E.saveNow().catch(e => console.warn('[Time Strata] save', e)), 1200);
        E.emit('changed');
    };

    E.saveNow = async function () {
        clearTimeout(E._saveTimer);
        if (!E.dirty || !E.project || !E.doc) return;
        E.dirty = false;
        await E.api('project/save', { name: E.project, doc: E.doc });
        E.emit('saved');
    };

    /* ---- chains ---- */

    E.chain = id => (E.doc.chains || []).find(c => c.id === id) || null;

    E.addChain = function (name) {
        const id = 'c' + Math.random().toString(36).slice(2, 8);
        const n = E.doc.chains.length;
        const c = fillChain({ id, name: name || ('Chain ' + (n + 1)), frames: 0, make: null, width: 512, height: 768, fps: 24,
                              figure: 0.84, baseline: 0.95, source: null });
        // a second chain stands beside the first, so both are seen
        c.spine.x = n * 3.5; c.spine.z = -n * 2;
        E.doc.chains.push(c);
        if (!E.doc.camera.follow) E.doc.camera.follow = id;
        E.touch();
        return c;
    };

    E.removeChain = async function (id) {
        const c = E.chain(id);
        if (!c) return;
        if (c.make) await E.api('chain/delete', { project: E.project, chain: id }).catch(() => null);
        E.doc.chains = E.doc.chains.filter(x => x.id !== id);
        if (E.doc.camera.follow === id) E.doc.camera.follow = E.doc.chains.length ? E.doc.chains[0].id : null;
        E.touch();
        await E.loadFrames();
    };

    /* a file from the computer into the project's uploads -> its path there */
    E.upload = async function (file, folder) {
        const query = `upload?project=${encodeURIComponent(E.project)}&filename=${encodeURIComponent(file.name)}` + (folder ? `&folder=${encodeURIComponent(folder)}` : '');
        return E.api(query, file, true);
    };

    /* Makes a chain from files the user chose: one video, or pictures (or a zip of them).
     * `progress(job)` hears how it goes. */
    E.makeChain = async function (id, files, progress) {
        const c = E.chain(id);
        files = Array.from(files || []);
        if (!files.length) throw new Error('Choose a video or pictures first');
        let source;
        const isVideo = f => /\.(mp4|mov|webm|mkv|avi|m4v|gif)$/i.test(f.name);
        if (files.length === 1 && isVideo(files[0])) {
            const up = await E.upload(files[0]);
            source = { kind: 'video', path: up.path };
            if (up.video && !c.cut.fps) c.source_fps = up.video.fps;
        } else if (files.length === 1 && /\.zip$/i.test(files[0].name)) {
            const up = await E.upload(files[0]);
            source = { kind: 'zip', path: up.path, fps: c.cut.fps || 24 };
        } else {
            const folder = 'pics-' + Date.now().toString(36);
            const sorted = files.filter(f => /\.(png|webp|jpe?g|bmp|tiff?)$/i.test(f.name)).sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
            if (!sorted.length) throw new Error('These are neither a video nor pictures');
            for (let i = 0; i < sorted.length; i++) {
                if (progress) progress({ status: 'running', stage: `uploading ${i + 1} of ${sorted.length}`, progress: i / sorted.length * 0.05 });
                await E.upload(sorted[i], folder);
            }
            source = { kind: 'pictures', path: 'uploads/' + folder, fps: c.cut.fps || 24 };
        }
        c.source = source;
        E.touch();
        return E.cutChain(id, false, progress);
    };

    /* cuts a chain's source out again (or only places its frames again: `realign`) */
    E.cutChain = async function (id, realign, progress) {
        const c = E.chain(id);
        if (!c.source) throw new Error('This chain has no source yet');
        const options = Object.assign({}, c.cut);
        const job = await E.api('job/start', { kind: 'chain', params: { project: E.project, chain: id, source: c.source, options, realign: !!realign } });
        const result = await E.waitJob(job, progress);
        Object.assign(c, { frames: result.frames, make: result.make, width: result.width, height: result.height, fps: result.fps,
                           figure: result.figure, baseline: result.baseline, suspects: result.suspects, figureless: result.figureless || [], method: result.method });
        if (c.to != null && c.to >= c.frames) c.to = null;
        E.touch();
        await E.loadFrames();
        return result;
    };

    /* a chain cut again from its own source with Real-ESRGAN first (`choice` "model:scale"), at a frame `scale` times
     * taller (at most 2048 px); a drawn chain keeps its backdrop's key and the motion it was drawn over */
    E.sharpen = async function (id, choice, progress) {
        const c = E.chain(id);
        if (!c || !c.make) throw new Error('Make the chain first');
        const scale = +String(choice).split(':')[1] || 2;
        const job = await E.api('job/start', { kind: 'sharpen', params: { project: E.project, chain: id, upscale: choice,
                                                                         height: Math.min(2048, Math.round((c.height || 768) * scale)) } });
        const result = await E.waitJob(job, progress);
        Object.assign(c, { frames: result.frames, make: result.make, width: result.width, height: result.height, fps: result.fps,
                           figure: result.figure, baseline: result.baseline, suspects: result.suspects, figureless: result.figureless || [], method: result.method });
        c.cut.upscale = choice; c.cut.height = result.height;
        E.touch();
        await E.loadFrames();
        return result;
    };

    /* Draws a new chain with Deforum over the motion of chain `sourceId`: the new chain stands where the motion
     * chain stood (which is then hidden) and the camera follows it. `targetId`: a chain drawn before, drawn again. */
    E.generate = async function (sourceId, options, targetId, progress) {
        const from = E.chain(sourceId);
        if (!from || !from.make) throw new Error('Make the motion chain first');
        let c = targetId ? E.chain(targetId) : null;
        if (!c) {
            c = E.addChain(from.name + ' · ' + 'drawn');
            c.spine = JSON.parse(JSON.stringify(from.spine));
            c.size = from.size; c.time = JSON.parse(JSON.stringify(from.time)); c.look = JSON.parse(JSON.stringify(from.look));
        }
        c.generated = { from: sourceId, options: JSON.parse(JSON.stringify(options)) };
        E.touch();
        const project = E.project, doc = E.doc;        // (another project may be opened while Deforum draws)
        const job = await E.api('job/start', { kind: 'deforum', params: { project, source: sourceId, chain: c.id, options } });
        // while Deforum draws, the new chain shows each frame as it comes, where the motion chain stands (which
        // is hidden), the camera follows its newest frame, and it can be steered
        const before = { frames: c.frames, width: c.width, height: c.height, fps: c.fps, figure: c.figure, baseline: c.baseline,
                         visible: c.visible, fromVisible: from.visible, follow: E.doc.camera.follow };
        let started = false;
        const watch = now => {
            if (progress) progress(now);
            if (!now.live || now.status !== 'running') return;
            if (!started) {
                started = true;
                Object.assign(c, { frames: now.live.total, width: now.live.width, height: now.live.height, fps: now.live.fps,
                                   figure: from.figure, baseline: from.baseline, visible: true });
                from.visible = false;
                E.doc.camera.follow = c.id;
                E.live = { chain: c.id, path: now.live.path, frames: now.live.frames, total: now.live.total, loaded: 0 };
                if (E.steering.on || E.dqt.on) { if (E.steering.on) E.stopSteering(); E.startSteering(); }
                E.loadFrames().then(() => E.liveMore(E.live && E.live.frames));
                E.emit('live');
            } else E.liveMore(now.live.frames);
        };
        let result;
        try {
            result = await E.waitJob(job, watch);
        } catch (e) {
            if (started) {
                Object.assign(c, { frames: before.frames, width: before.width, height: before.height, fps: before.fps,
                                   figure: before.figure, baseline: before.baseline, visible: before.visible });
                from.visible = before.fromVisible;
                doc.camera.follow = before.follow;
            }
            throw e;
        } finally {
            if (E.live && E.live.chain === c.id) {
                if (E.steering.on && E.steering.chain === c.id) E.stopSteering();
                E.live = null;
                E.emit('live');
                if (started && E.project === project) { E.touch(); E.loadFrames(); }
            }
        }
        Object.assign(c, { frames: result.frames, make: result.make, width: result.width, height: result.height, fps: result.fps,
                           figure: result.figure, baseline: result.baseline, suspects: result.suspects, figureless: result.figureless || [], method: result.method,
                           source: { kind: 'pictures', path: `chains/${c.id}/source`, fps: result.fps } });
        c.visible = true;
        from.visible = false;
        doc.camera.follow = c.id;
        if (E.project !== project) {
            // the drawing belongs to the project it was started in, which is no longer open: it is saved there
            await E.api('project/save', { name: project, doc });
            return c;
        }
        E.touch();
        await E.loadFrames();
        return c;
    };

    /* frames `frames` of drawn chain `targetId` drawn again (with `options` as set now, usually a new seed); the
     * rest of the chain is kept and the chain is cut again */
    E.redraw = async function (sourceId, options, targetId, frames, progress) {
        const c = E.chain(targetId);
        if (!c || !c.generated || !c.make) throw new Error('Only a drawn chain can have some of its frames drawn again');
        if (!frames.length) throw new Error('Choose which frames to draw again');
        const job = await E.api('job/start', { kind: 'deforum', params: { project: E.project, source: sourceId, chain: targetId,
                                                                          options: Object.assign({}, options, { redraw: frames }) } });
        const result = await E.waitJob(job, progress);
        Object.assign(c, { frames: result.frames, make: result.make, width: result.width, height: result.height, fps: result.fps,
                           figure: result.figure, baseline: result.baseline, suspects: result.suspects, figureless: result.figureless || [], method: result.method });
        c.generated.redrawn = Array.from(new Set([...(c.generated.redrawn || []), ...(result.redrawn || frames)])).sort((a, b) => a - b);
        E.touch();
        await E.loadFrames();
        return result;
    };

    /* the frames of the chain Deforum is drawing that are not on the graphics card yet, up to `count` */
    E.liveMore = async function (count) {
        const L = E.live;
        if (!L || !E.renderer) return;
        L.frames = Math.max(L.frames, count || 0);
        if (L.pulling) return;
        L.pulling = true;
        try {
            let added = 0;
            while (E.live === L && L.loaded < Math.min(L.frames, L.total)) {
                const entry = E.loaded[L.chain];
                if (!entry || E.loading) break;            // (the full load is still going: it takes them)
                const k = L.loaded;
                const blob = await (await fetch(E.fileUrl(`${L.path}/${String(k).padStart(6, '0')}.webp`))).blob();
                if (E.live !== L || E.loaded[L.chain] !== entry) return;
                await E.renderer.upload(entry.base + k, blob);
                L.loaded++;
                added++;
            }
            if (added) { E.renderer.finishUploads(); E.emit('frames'); }
        } catch (e) {
            console.warn('[Time Strata] live frame', e);
        } finally { L.pulling = false; }
    };

    E.isLive = c => !!(c && E.live && E.live.chain === c.id);

    /* ---- frames on the graphics card ---- */

    /* the chain whose frames a chain shows: itself, or the one it borrows them from (`uses`: a second life of
     * the same frames, laid out another way, without a second copy of them anywhere) */
    E.ownerOf = c => {
        const owner = c.uses ? E.chain(c.uses) : null;
        return owner && owner.make && owner.frames > 0 ? owner : c;
    };

    E.shownChains = () => (E.doc ? E.doc.chains.filter(c => c.visible && (E.isLive(c) || (E.ownerOf(c).make && E.ownerOf(c).frames > 0))) : []);

    E.loadFrames = async function () {
        const R = E.renderer;
        if (!R || !E.doc) return;
        const chains = E.shownChains();
        // every set of frames once, even when two chains show it (or the one that owns it is hidden)
        const owners = [];
        for (const c of chains) if (!owners.includes(E.ownerOf(c))) owners.push(E.ownerOf(c));
        const layers = owners.reduce((n, c) => n + c.frames, 0);
        E.loaded = {};
        if (!layers) { R.texture && R.gl.deleteTexture(R.texture); R.texture = null; E.prepareSharp([]); E.emit('frames'); return; }
        const aspect = Math.max(...owners.map(c => c.width / c.height));
        const tallest = Math.max(...owners.map(c => c.height));
        const [w, h] = R.textureSize(Math.min(layers, R.maxLayers), aspect, tallest, E.budget);
        R.allocate(layers, w, h);
        E.prepareSharp(owners);
        let base = 0;
        const todo = [], byOwner = {};
        for (const o of owners) {
            const live = E.isLive(o);
            byOwner[o.id] = { make: live ? 'live' : o.make, base, frames: Math.min(o.frames, R.maxLayers - base) };
            // (a chain Deforum is drawing: the frames drawn so far; the rest come as they are drawn)
            const have = live ? Math.min(E.live.frames, byOwner[o.id].frames) : byOwner[o.id].frames;
            const folder = live ? E.live.path : `chains/${o.id}/${o.make}`;
            for (let k = 0; k < have; k++) todo.push([base + k, `${folder}/${String(k).padStart(6, '0')}.webp`]);
            if (live) E.live.loaded = have;
            base += o.frames;
            if (base >= R.maxLayers) break;
        }
        for (const c of chains) if (byOwner[E.ownerOf(c).id]) E.loaded[c.id] = byOwner[E.ownerOf(c).id];
        const ticket = E._loadTicket = {};
        E.loading = { done: 0, total: todo.length, size: [w, h] };
        E.emit('loading');
        let next = 0;
        const worker = async () => {
            while (next < todo.length && E._loadTicket === ticket) {
                const [layer, path] = todo[next++];
                try {
                    const blob = await (await fetch(E.fileUrl(path))).blob();
                    if (E._loadTicket !== ticket) return;
                    await R.upload(layer, blob);
                } catch (e) { console.warn('[Time Strata] frame', path, e); }
                E.loading.done++;
                if (E.loading.done % 48 === 0) { R.finishUploads(); E.emit('loading'); }
            }
        };
        await Promise.all(Array.from({ length: 6 }, worker));
        if (E._loadTicket !== ticket) return;
        R.finishUploads();
        E.loading = null;
        E.emit('frames');
    };

    /* ---- the world ---- */

    E.isVideo = path => /\.(mp4|webm|mov|m4v|mkv)$/i.test(String(path || ''));

    E.loadWorld = async function () {
        if (!E.renderer || !E.doc) return;
        const W = E.doc.world;
        const panorama = W.kind === 'panorama' && W.panorama;
        await E.renderer.setPanorama(panorama ? E.fileUrl(W.panorama) : null, panorama && E.isVideo(W.panorama));
        E.emit('world');
    };

    /* the second of the world video seen at second `t` of the shot (see Core.worldVideoTime) */
    E.worldVideoTime = t => Core.worldVideoTime(E.doc.world, t, E.doc.shot.duration, E.renderer ? E.renderer.panoramaDuration() : 0);

    /* ---- the scene at a moment ---- */

    const pathCache = new Map();
    E.pathOf = function (c) {
        const length = Core.chainLength(c, Math.max(1, E.ownerOf(c).frames || 1)) + 2;
        // (a steered spine's marks grow at its end while it is steered: the key holds their count and the last
        // two, and E.steerEdits for any other change, instead of all of them)
        const steer = c.spine.steer || [];
        const spine = Object.assign({}, c.spine, { steer: steer.length + ':' + JSON.stringify(steer.slice(-2)) + ':' + E.steerEdits });
        const key = c.id + JSON.stringify(spine) + '|' + length.toFixed(3);
        let path = pathCache.get(key);
        if (!path) {
            path = Core.spinePath(c.spine, length, 0.01);
            pathCache.set(key, path);
            if (pathCache.size > 24) pathCache.delete(pathCache.keys().next().value);
        }
        return path;
    };

    E.cardGeometry = function (c) {
        const o = E.ownerOf(c);         // the frames' own measures, also for a chain that borrows them
        return { size: c.size, figure: o.figure || 0.84, baseline: o.baseline || 0.95, aspect: (o.width || 512) / (o.height || 768),
                 from: c.from || 0, to: c.to, offset: c.offset || 0, spine: c.spine, faces: c.time && c.time.mode === 'past' ? 'end' : 'start' };
    };

    E.followed = function () {
        const doc = E.doc;
        return (doc && (E.chain(doc.camera.follow) || doc.chains[0])) || null;
    };

    /* the present of a chain at time t (a fractional frame), or null when all its frames stand there */
    E.presentOf = (c, t) => Core.presentAt(c.time, t, E.ownerOf(c).fps, c.to != null ? c.to : Math.max(0, (E.ownerOf(c).frames || 1) - 1));

    /* how far along its chain a riding camera's keys are measured from at time t */
    E.shiftAt = function (t) {
        const c = E.followed();
        if (!c || !E.doc.camera.ride) return 0;
        const now = E.presentOf(c, t);
        return now == null ? 0 : Core.presentS(E.cardGeometry(c), now);
    };

    E.defaultKeys = function () {
        const c = E.followed();
        return Core.preset('opening', c ? Core.chainLength(E.cardGeometry(c), c.frames || 120) : 4, E.doc.shot.duration, c ? c.size : 1.7,
                           c && c.time.mode === 'past' ? -1 : 1);
    };

    /* where the figure the camera follows is at time t: its present, or (a chain without one) the `along` key */
    E.figurePlace = function (c, t) {
        if (!c) return { s: 0, size: 1.7, faces: 'start', aspect: E.doc.shot.width / E.doc.shot.height };
        // (while it is steered the camera stays with the frame being steered, also on a chain where all frames stand)
        const steered = E.steering.on && E.steering.chain === c.id;
        const g = E.cardGeometry(c), now = E.isLive(c) || steered ? E.headOf(c, t) : E.presentOf(c, t);
        return { s: now == null ? null : Core.presentS(g, now), size: c.size || 1.7, faces: g.faces, aspect: E.doc.shot.width / E.doc.shot.height };
    };

    /* the follow keys as they are shown now: with what the mouse (or DeforumationQT) adds on top of them */
    E.adjustedFollowKeys = function () {
        const keys = E.doc.camera.follow_keys && E.doc.camera.follow_keys.length ? E.doc.camera.follow_keys : Core.followPreset('front', E.doc.shot.duration);
        const a = E.followAdjust, q = E.dqtCamera;
        if (!a && !q) return keys;
        return keys.map(k => Object.assign({}, k, {
            yaw: (k.yaw != null ? k.yaw : Core.FOLLOW_FIELDS.yaw) + (a ? a.yaw : 0) + (q ? q.yaw : 0),
            elevation: Core.clamp((k.elevation != null ? k.elevation : Core.FOLLOW_FIELDS.elevation) + (a ? a.elevation : 0) + (q ? q.elevation : 0), -80, 85),
            fill: Core.clamp((k.fill != null ? k.fill : Core.FOLLOW_FIELDS.fill) * (a ? a.fill : 1) * (q && q.fill ? q.fill : 1), 0.05, 1.6),
            fov: q && q.fov ? q.fov : k.fov,
        }));
    };

    /* the camera at time t: following the figure, or the keys along the chain (without keys, the reel's opening) */
    E.cameraAt = function (t) {
        const c = E.followed();
        const path = c ? E.pathOf(c) : Core.spinePath({ shape: 'line' }, 10, 0.01);
        let cam;
        if (E.doc.camera.mode === 'follow') {
            cam = Core.followAt(E.adjustedFollowKeys(), path, t, E.figurePlace(c, t));
        } else {
            let keys = E.doc.camera.keys, shift = E.shiftAt(t);
            if (!keys || !keys.length) {
                keys = E.defaultKeys();
                const now = c ? E.presentOf(c, t) : null;
                shift = now == null ? 0 : Core.presentS(E.cardGeometry(c), now);
            }
            cam = Core.cameraAt(keys, path, t, shift);
        }
        if (E.free) {
            // looking around: the camera goes round a point, as the mouse says
            const f = E.free;
            const d = [Math.cos(Core.rad(f.pitch)) * Math.sin(Core.rad(f.yaw)), Math.sin(Core.rad(f.pitch)), Math.cos(Core.rad(f.pitch)) * Math.cos(Core.rad(f.yaw))];
            const eye = Core.add(f.target, Core.mul(d, f.distance));
            return { eye, target: f.target, up: Core.UP, fov: cam.fov, forward: Core.norm(Core.sub(f.target, eye)), values: cam.values };
        }
        return cam;
    };

    E.sceneAt = function (t) {
        const doc = E.doc;
        const camera = E.cameraAt(t);
        const cards = [], shadows = [];
        for (const c of E.shownChains()) {
            const loaded = E.loaded[c.id];
            if (!loaded) continue;
            const path = E.pathOf(c), geometry = E.cardGeometry(c);
            const list = Core.chainCards(geometry, path, loaded.frames);
            // a chain Deforum is drawing: the frames drawn so far stand, the newest is its present
            const live = E.isLive(c);
            const now = live ? Math.max(0, E.live.loaded - 1) : E.presentOf(c, t);
            const o = E.ownerOf(c);
            const L = c.look, n = Math.max(1, (c.to == null ? o.frames - 1 : c.to) - (c.from || 0));
            const tint = window.StrataGL.hex(L.tint), glowColor = window.StrataGL.hex(L.glow_color);
            const shown = [];
            for (const card of list) {
                const there = live ? (card.k < E.live.loaded ? 1 : 0) : Core.presence(c.time, card.k, now);
                if (there <= 0) continue;
                // age: how far a frame is from the near end (the present, when there is one)
                let age = now == null ? (card.k - (c.from || 0)) / n : Math.min(1, Math.abs(card.k - now) / n);
                if (L.reverse) age = 1 - age;
                cards.push({ bottom: card.bottom, right: card.right, up: card.up, layer: loaded.base + card.k,
                             alpha: there * (1 - L.fade * age), tint, tintAmount: L.tint_amount * age, glow: L.glow * (0.25 + 0.75 * age),
                             glowColor, rib: L.rib, key: o.id + ':' + card.k, path: `chains/${o.id}/${o.make}/${String(card.k).padStart(6, '0')}.webp` });
                shown.push(card);
            }
            if (L.shadow > 0 && shown.length > 1) {
                const strip = [], half = Core.cardSize(geometry).width * 0.32;
                const last = shown[shown.length - 1].s, first = shown[0].s;
                for (let s = first; s <= last + 1e-6; s += Math.max(0.05, (last - first) / 200)) {
                    const p = Core.pointAt(path, s).p;
                    strip.push([p[0], p[2], half, L.shadow * Math.max(0, 1 - p[1] / 0.8)]);
                }
                if (strip.length > 1) shadows.push(strip);
            }
        }
        const follow = E.followed();
        const knife = follow ? Core.knifeAt(doc.knife, E.pathOf(follow), t) : null;
        const waiting = E.assignSharp(cards, camera);
        return { world: doc.world, camera, cards, shadows, knife, near: [doc.look.near, doc.look.far], waiting };
    };

    /* ---- full-size copies of the frames nearest the camera ---- */

    const SHARP_SLOTS = 32;
    E.sharp = { slots: 0, keyOf: [], ready: [], used: [], loading: new Map(), tick: 0 };

    /* after the small copies are made: full-size copies are worth having only when the small ones are smaller */
    E.prepareSharp = function (chains) {
        const R = E.renderer, S = E.sharp;
        const tallest = Math.max(0, ...chains.map(c => c.height || 0));
        const widest = Math.max(0.1, ...chains.map(c => (c.width || 1) / (c.height || 1)));
        const total = chains.reduce((n, c) => n + c.frames, 0);
        const slots = R.texH && tallest > R.texH * 1.15 ? Math.min(SHARP_SLOTS, total) : 0;
        R.allocateSharp(slots, Math.round(tallest * widest / 4) * 4, tallest);
        Object.assign(S, { slots, keyOf: new Array(slots).fill(null), ready: new Array(slots).fill(false),
                           used: new Array(slots).fill(0), loading: new Map(), ticket: {} });
    };

    /* Gives the cards that would be drawn larger than their small copy a full-size one: those already loaded are
     * marked to use it, the others are asked for (into a slot no wanted card uses). Returns the loads still
     * running for this picture (a recording waits for them). */
    E.assignSharp = function (cards, camera) {
        const R = E.renderer, S = E.sharp;
        if (!S.slots || !R) return [];
        S.tick++;
        const eye = camera.eye, fwd = camera.forward || Core.norm(Core.sub(camera.target, camera.eye));
        const pxPerUnit = R.canvas.height / (2 * Math.tan(Core.rad(camera.fov) / 2));
        const wanted = [];
        for (const c of cards) {
            const centre = [c.bottom[0] + c.up[0] * 0.5, c.bottom[1] + c.up[1] * 0.5, c.bottom[2] + c.up[2] * 0.5];
            const ahead = (centre[0] - eye[0]) * fwd[0] + (centre[1] - eye[1]) * fwd[1] + (centre[2] - eye[2]) * fwd[2];
            if (ahead < 0.05) continue;
            const tall = Math.hypot(c.up[0], c.up[1], c.up[2]) / ahead * pxPerUnit;      // its height on screen, in pixels
            if (tall > R.texH * 1.1) wanted.push([tall, c]);
        }
        wanted.sort((a, b) => b[0] - a[0]);
        const keep = new Set(wanted.slice(0, S.slots).map(([, c]) => c.key));
        const waiting = [];
        for (const [, c] of wanted.slice(0, S.slots)) {
            let slot = S.keyOf.indexOf(c.key);
            if (slot >= 0) {
                S.used[slot] = S.tick;
                if (S.ready[slot]) c.sharp = slot;
                else if (S.loading.has(c.key)) waiting.push(S.loading.get(c.key));
                continue;
            }
            // the slot used longest ago by a card not wanted now
            slot = -1;
            for (let i = 0; i < S.slots; i++) if (!keep.has(S.keyOf[i]) && (slot < 0 || S.used[i] < S.used[slot])) slot = i;
            if (slot < 0) break;
            S.keyOf[slot] = c.key; S.ready[slot] = false; S.used[slot] = S.tick;
            const ticket = S.ticket, key = c.key;
            const load = (async () => {
                try {
                    const blob = await (await fetch(E.fileUrl(c.path))).blob();
                    if (S.ticket !== ticket || S.keyOf[slot] !== key) return;
                    await R.uploadSharp(slot, blob);
                    if (S.ticket === ticket && S.keyOf[slot] === key) S.ready[slot] = true;
                } catch (e) { console.warn('[Time Strata] sharp frame', c.path, e); S.keyOf[slot] = null; }
                finally { S.loading.delete(key); E.emit('sharp'); }
            })();
            S.loading.set(key, load);
            waiting.push(load);
        }
        return waiting;
    };

    E.draw = function () {
        if (!E.renderer || !E.doc) return;
        // (a world video's frame for this moment: when it arrives, the page draws again)
        E.renderer.panoramaFrame(E.worldVideoTime(E.time), false, () => E.emit('worldframe'));
        E.renderer.draw(E.sceneAt(E.time));
    };

    /* ---- camera keys ---- */

    E.applyPreset = function (name) {
        const c = E.followed();
        const length = c ? Core.chainLength(E.cardGeometry(c), c.frames || 120) : 4;
        const riding = Core.RIDING.has(name);
        // the reel's opening needs a present to ride with: a chain where every frame stands gets one that eats it
        if (riding && c && c.time.mode === 'all') { c.time.mode = 'future'; c.time.start = c.from || 0; }
        E.doc.camera.ride = riding;
        const dir = c && c.time.mode === 'past' ? -1 : 1;
        const keys = Core.preset(name, length, E.doc.shot.duration, c ? c.size : 1.7, dir);
        if (riding && c && c.time.mode === 'future') {
            // the flight into the frames still to come stays inside them: no deeper than what is left of the chain
            for (const key of keys) {
                if (key.s * dir <= 0) continue;
                const left = length - Core.presentS(E.cardGeometry(c), E.presentOf(c, key.t));
                const room = Math.max(0, left - 0.6);
                key.s = Math.min(key.s, room * (key.t >= E.doc.shot.duration ? 0.85 : 0.6));
            }
        }
        E.doc.camera.keys = keys.map(k => roundKey(k));
        E.doc.camera.mode = 'keys';
        E.free = null;
        E.touch();
    };

    /* ---- the follow camera's keys ---- */

    E.applyFollowPreset = function (name) {
        E.doc.camera.follow_keys = Core.followPreset(name, E.doc.shot.duration).map(roundKey);
        E.doc.camera.mode = 'follow';
        E.followAdjust = null; E.free = null;
        E.touch();
    };

    /* the follow values at time t, as the camera uses them (with the mouse's adjustment) */
    E.followValues = function (t) {
        const keys = E.adjustedFollowKeys(), out = {};
        for (const [field, fallback] of Object.entries(Core.FOLLOW_FIELDS)) out[field] = Core.track(keys, field, t, fallback);
        return out;
    };

    /* sets one follow value at the current moment: on the key there, or on a new key made from what is shown */
    E.setFollowValue = function (field, value) {
        const keys = E.doc.camera.follow_keys;
        let key = keys.find(k => Math.abs(k.t - E.time) < 0.05);
        if (!key) {
            key = roundKey(Object.assign({ t: E.time }, E.followValues(E.time)));
            keys.push(key);
            keys.sort((a, b) => a.t - b.t);
        }
        key[field] = value;
        E.touch();
        return key;
    };

    /* ---- steering the trail ----
     * The trail of a chain whose shape is 'steered' goes where it was steered: one step a frame, each turned and
     * sloped by the marks of c.spine.steer ([step, degrees of turn a frame, degrees of climb]). Input (-1..1 for
     * turn and climb) comes from the arrows, the joystick and DeforumationQT; it is smoothed, and while the
     * chain's head moves (the shot plays, or Deforum draws new frames) every frame the head reaches gets a mark
     * with the turn and climb of that moment. The trail beyond the head goes straight on until it is reached.
     * Left and right are the screen's: from in front of the figure its own right is the screen's left. */
    E.steering = { on: false, input: { turn: 0, climb: 0 }, dqt: null, turn: 0, climb: 0, last: null, chain: null, sign: 1, tail: false };
    E.steerEdits = 0;           // bumped when marks change other than at the head (the trail is then worked out again)

    /* a frame's step along a steered spine (it is walked one step a frame from its start) */
    E.stepOf = (c, k) => Math.round(k - (c.from || 0) + (c.offset || 0) / Math.max(1e-6, c.spine.spacing || Core.SPINE_DEFAULTS.spacing));

    /* a chain made ready to be steered: its shape becomes 'steered', keeping the way it already went */
    E.makeSteered = function (c) {
        if (c.spine.shape === 'steered') return;
        const frames = Math.max(1, (E.ownerOf(c).frames || 1) - 1);
        const turn = c.spine.shape === 'arc' ? c.spine.bend / frames : 0;
        c.spine.steer = [[0, Math.round(turn * 1000) / 1000, 0]];
        c.spine.shape = 'steered';
        E.steerEdits++;
        E.touch();
    };

    /* the head of a chain at time t: the newest frame it shows (Deforum's newest while it draws; else its
     * present; a chain without one: as it plays) */
    E.headOf = function (c, t) {
        if (E.isLive(c)) return Math.max(0, E.live.loaded - 1);
        const now = E.presentOf(c, t);
        if (now != null) return Math.floor(now);
        return Math.floor((c.time.rate || E.ownerOf(c).fps || 24) * t);
    };

    /* the trail as it was before the last take (or before it was straightened), for "Undo" */
    function keepForUndo(c) {
        E.steering.undo = { chain: c.id, shape: c.spine.shape, steer: JSON.parse(JSON.stringify(c.spine.steer || [])) };
    }

    E.undoSteering = function () {
        const u = E.steering.undo, c = u && E.chain(u.chain);
        if (!c) return;
        if (E.steering.on) E.stopSteering();
        c.spine.shape = u.shape; c.spine.steer = u.steer;
        E.steering.undo = null;
        E.steerEdits++;
        E.touch();
    };

    E.startSteering = function () {
        const c = (E.live && E.chain(E.live.chain)) || E.followed();
        if (!c) throw new Error('Make a chain first');
        if (!E.ownerOf(c).frames && !(E.live && E.live.chain === c.id)) throw new Error('This chain has no frames yet');
        keepForUndo(c);
        E.makeSteered(c);
        Object.assign(E.steering, { on: true, chain: c.id, last: null, turn: 0, climb: 0, tail: false });
        E.emit('steer');
    };

    /* ends steering: the marks are thinned to where the steering changed; after the last steered frame the
     * trail goes straight on, level */
    E.stopSteering = function () {
        const S = E.steering;
        const c = S.on ? E.chain(S.chain) : null;
        let kept = null;
        if (c && Array.isArray(c.spine.steer)) {
            if (S.tail) c.spine.steer.pop();
            if (S.last != null) {
                c.spine.steer.push([S.last + 1, 0, 0]);
                kept = [S.first, S.last];
            }
            c.spine.steer = Core.compressSteer(c.spine.steer);
            E.steerEdits++;
            E.touch();
        }
        Object.assign(S, { on: false, last: null, first: null, input: { turn: 0, climb: 0 }, turn: 0, climb: 0, tail: false });
        E.emit('steer');
        return kept;
    };

    /* the smoothed turn (degrees a second) and climb (degrees) the input asks for now */
    E.steerTick = function (dt) {
        const S = E.steering, cfg = E.doc.steer;
        const c = E.chain(S.chain);
        if (c && cfg.screen !== false) {
            // which way the screen's right is on the trail, from where the camera stands now (with a margin,
            // so a camera beside the figure does not flip it back and forth)
            const cam = E.cameraAt(E.time);
            const s = Core.presentS(E.cardGeometry(c), E.headOf(c, E.time));
            const right = Core.pointAt(E.pathOf(c), s).r;
            const d = Core.dot(Core.norm(Core.cross(cam.forward, cam.up)), right);
            if (d > 0.25) S.sign = 1; else if (d < -0.25) S.sign = -1;
        }
        const turn = Core.clamp(S.input.turn + (S.dqt ? S.dqt.turn : 0), -1, 1);
        const climb = Core.clamp(S.input.climb + (S.dqt ? S.dqt.climb : 0), -1, 1);
        const k = 1 - Math.exp(-Math.max(0, dt) / 0.25);
        const wantTurn = turn * cfg.turn * (cfg.flip_turn ? -1 : 1) * (cfg.screen !== false ? S.sign : 1);
        const wantClimb = climb * cfg.climb * (cfg.flip_climb ? -1 : 1);
        S.turn += (wantTurn - S.turn) * k;
        S.climb += (wantClimb - S.climb) * k;
    };

    /* Marks the steering of the frame the head is on (head: a frame number). Called every tick: on the same
     * frame it changes that frame's mark, so the trail ahead answers at once even while Deforum takes seconds
     * a frame. Returns 'back' when the head went back (the shot started over), which ends a take. */
    E.recordSteering = function (head) {
        const S = E.steering;
        if (!S.on || head == null) return null;
        const c = E.chain(S.chain);
        if (!c) return null;
        const list = Array.isArray(c.spine.steer) ? c.spine.steer : (c.spine.steer = []);
        const step = E.stepOf(c, head);
        const rate = c.time.rate || E.ownerOf(c).fps || 24;
        const mark = [step, Math.round(S.turn / rate * 1e4) / 1e4, Math.round(S.climb * 100) / 100];
        if (S.last != null && step < S.last) return 'back';
        if (S.tail) { list.pop(); S.tail = false; }
        if (S.last == null) {
            // steering starts here: what was steered from this frame on is steered anew; the trail before it
            // keeps the way it had
            while (list.length && list[list.length - 1][0] >= step) list.pop();
            const prev = list[list.length - 1];
            if (prev && step - 1 > prev[0]) list.push([step - 1, prev[1], prev[2]]);
            list.push(mark);
            S.first = S.last = step;
            E.steerEdits++;
        } else if (step === S.last && list.length && list[list.length - 1][0] === step) {
            list[list.length - 1] = mark;
        } else {
            list.push(mark);
            S.last = step;
        }
        // beyond the head the trail goes straight on, at the climb of now
        list.push([S.last + 1, 0, mark[2]]);
        S.tail = true;
        E.touch();
        return null;
    };

    E.clearSteering = function (c) {
        if (E.steering.on && E.steering.chain === c.id) E.stopSteering();
        keepForUndo(c);
        c.spine.steer = [[0, 0, 0]];
        E.steerEdits++;
        E.touch();
    };

    /* ---- DeforumationQT: its pan steers the trail, its rotation and zoom move the follow camera ----
     * Read only (the page asks the server, the server asks DeforumationQT's mediator). Pan left/right turns the
     * trail, pan up/down makes it climb (each at `dqt_range` and more: full); rotate left/right turns the camera
     * around the figure and rotate up/down raises it, as long as they are held (degrees a second); zooming in
     * DeforumationQT brings the camera closer and its FOV becomes the lens, once they are moved. */
    E.dqt = { on: false, running: false, values: {}, base: null, yaw: 0, elevation: 0, timer: null, busy: false };

    E.setDqt = function (on) {
        const Q = E.dqt;
        Q.on = !!on;
        clearInterval(Q.timer);
        Q.timer = null;
        if (!Q.on) { E.steering.dqt = null; E.dqtCamera = null; Q.base = null; Q.yaw = Q.elevation = 0; E.emit('dqt'); return; }
        Q.last = performance.now();
        Q.timer = setInterval(() => E.pollDqt().catch(() => null), 250);
        E.pollDqt().catch(() => null);
    };

    E.pollDqt = async function () {
        const Q = E.dqt;
        if (!Q.on || Q.busy || !E.doc) return;
        Q.busy = true;
        try {
            const answer = await E.api('dqt');
            const wasRunning = Q.running;
            Q.running = !!answer.running;
            Object.assign(Q.values, answer.values || {});
            if (Q.running !== wasRunning) E.emit('dqt');
            const now = performance.now(), dt = Math.min(1, (now - Q.last) / 1000);
            Q.last = now;
            if (!Q.running) { E.steering.dqt = null; return; }
            const cfg = E.doc.steer, v = Q.values, range = Math.max(0.05, +cfg.dqt_range || 3);
            if (!Q.base) Q.base = { translation_z: v.translation_z || 0, fov: v.fov || 70 };
            E.steering.dqt = { turn: Core.clamp((v.translation_x || 0) / range, -1, 1), climb: Core.clamp((v.translation_y || 0) / range, -1, 1) };
            if (cfg.dqt_camera !== false) {
                Q.yaw += (v.rotation_y || 0) * 20 * dt;
                Q.elevation = Core.clamp(Q.elevation + (v.rotation_x || 0) * 15 * dt, -70, 70);
                const zoom = (v.translation_z || 0) - Q.base.translation_z;
                const fovMoved = Math.abs((v.fov || 70) - Q.base.fov) > 0.5;
                E.dqtCamera = { yaw: Q.yaw, elevation: Q.elevation, fill: Core.clamp(Math.exp(zoom * 0.15), 0.3, 3),
                                fov: fovMoved ? Core.clamp(v.fov, 10, 120) : null };
            } else E.dqtCamera = null;
            E.emit('dqt-values');
        } finally { Q.busy = false; }
    };

    /* how many frames a riding shot uses and how many it needs (the present's frames plus the chain still seen
     * behind it at the end) */
    E.ridingNeeds = function (c) {
        const D = E.doc.shot.duration, rate = c.time.rate || c.fps || 24;
        const used = Math.ceil(D * rate);
        const behind = Math.ceil((E.doc.plan.ahead || 6) / c.spine.spacing);
        return { used, behind, frames: used + behind + (c.time.start || 0), have: c.frames };
    };

    function roundKey(k) {
        const out = {};
        for (const [key, value] of Object.entries(k)) out[key] = typeof value === 'number' ? Math.round(value * 1000) / 1000 : value;
        return out;
    }

    /* a key at the current moment, from what the camera shows now (also when looking around freely) */
    E.keyHere = function () {
        const c = E.followed();
        if (!c) return null;
        if (E.doc.camera.mode === 'follow' && !E.free) {
            // the follow values seen now (the mouse's adjustment included) become a key here
            const key = roundKey(Object.assign({ t: E.time }, E.followValues(E.time)));
            if (E.presentOf(c, E.time) != null) delete key.along;        // (a chain with a present is followed there)
            const keys = E.doc.camera.follow_keys.filter(k => Math.abs(k.t - E.time) > 1e-3);
            keys.push(key);
            keys.sort((a, b) => a.t - b.t);
            E.doc.camera.follow_keys = keys;
            E.followAdjust = null;
            E.touch();
            return key;
        }
        const cam = E.cameraAt(E.time);
        E.doc.camera.mode = 'keys';
        if (!E.doc.camera.keys.length) {
            E.doc.camera.keys = E.defaultKeys().map(roundKey);
            E.doc.camera.ride = c.time.mode !== 'all';
        }
        const key = roundKey(Object.assign({ t: E.time }, Core.keyFromView(E.pathOf(c), cam.eye, cam.target, cam.fov, E.shiftAt(E.time))));
        const keys = E.doc.camera.keys.filter(k => Math.abs(k.t - E.time) > 1e-3);
        keys.push(key);
        keys.sort((a, b) => a.t - b.t);
        E.doc.camera.keys = keys;
        E.free = null;
        E.touch();
        return key;
    };

    /* the planner's answer for this project's chain and shot */
    E.plan = function () {
        const c = E.followed();
        const shot = E.doc.shot;
        // the lens is vertical, so the frame's height in the video is what counts
        return Core.plan(Object.assign({}, E.doc.plan, { size: c ? c.size : 1.7, figure: c ? (c.figure || 0.84) : 0.84,
                                                         fov: 50, height: shot.height, near: E.doc.look.far }));
    };

    /* the planner's answer made true: the spacing set, and the camera flown through at its pace */
    E.usePlan = function () {
        const c = E.followed();
        if (!c) return;
        const p = E.plan();
        c.spine.spacing = Math.round(p.spacing * 10000) / 10000;
        const L = Core.chainLength(E.cardGeometry(c), c.frames || p.frames);
        const pace = E.doc.plan.speed;
        const keys = Core.preset('through', L, E.doc.shot.duration, c.size).map(roundKey);
        // flown at the planned speed, starting where the flight would end at the shot's end
        keys[0].s = -1.2; keys[1].s = Math.min(L + 0.3, -1.2 + pace * E.doc.shot.duration);
        E.doc.camera.keys = keys;
        E.doc.camera.mode = 'keys';
        E.doc.camera.ride = false;
        E.touch();
    };

    /* how many frames pass the lens each second at time t */
    E.passRate = function (t) {
        const c = E.followed();
        if (!c) return 0;
        // the camera's own place along the chain, wherever it is measured from
        const along = at => E.cameraAt(at).eye;
        const dt = 0.05, a = Math.max(0, t - dt), b = Math.min(E.doc.shot.duration, t + dt);
        if (E.free) return 0;
        const path = E.pathOf(c);
        const ds = Core.nearestS(path, along(b)) - Core.nearestS(path, along(a));
        return ds / Math.max(1e-6, b - a) / c.spine.spacing;
    };

    /* ---- recording ---- */

    E.recording = null;

    E.record = async function (progress) {
        const doc = E.doc, R = E.renderer, rec = doc.record;
        const [width, height] = String(rec.size).split('x').map(Number);
        const fps = +rec.fps || 30, total = Math.max(1, Math.round(doc.shot.duration * fps));
        if (E.loading) throw new Error('The frames are still loading');
        const session = await E.api('export/start', { project: E.project, name: rec.name || E.project, width, height, fps,
                                                      kind: rec.kind, quality: +rec.quality, supersample: +rec.supersample || 1,
                                                      music: doc.music || '', music_start: +rec.music_start || 0 });
        const canvas = R.canvas, keepW = canvas.width, keepH = canvas.height, keepFree = E.free, keepTime = E.time;
        E.free = null;
        E.recording = { cancel: false };
        let jpeg = false;
        const started = performance.now();
        try {
            R.resize(session.drawn[0], session.drawn[1]);
            for (let i = 0; i < total; i++) {
                if (E.recording.cancel) throw new Error('cancelled');
                E.time = i / fps;
                let scene = E.sceneAt(E.time);
                if (scene.waiting.length) {
                    // the frames this picture shows up close are drawn from their full-size copies, never blurred
                    await Promise.all(scene.waiting);
                    scene = E.sceneAt(E.time);
                }
                await R.panoramaFrame(E.worldVideoTime(E.time), true);      // the world video at this very moment
                R.draw(scene);
                const t0 = performance.now();
                let body;
                if (jpeg) {
                    const url = canvas.toDataURL('image/jpeg', 0.96);
                    const bin = atob(url.slice(url.indexOf(',') + 1));
                    body = new Uint8Array(bin.length);
                    for (let j = 0; j < bin.length; j++) body[j] = bin.charCodeAt(j);
                } else body = R.pixels();
                await E.api(`export/frame?id=${session.id}&jpeg=${jpeg}`, body, true);
                // a browser that sends raw frames slowly sends JPEGs instead (from the third frame on)
                if (!jpeg && i >= 1 && i <= 3 && performance.now() - t0 > 450) jpeg = true;
                if (progress) progress({ frame: i + 1, total, seconds: (performance.now() - started) / 1000, jpeg });
            }
            const result = await E.api('export/finish', { id: session.id });
            return result;
        } catch (e) {
            await E.api('export/cancel', { id: session.id }).catch(() => null);
            throw e;
        } finally {
            R.resize(keepW, keepH);
            E.free = keepFree; E.time = keepTime;
            E.recording = null;
            E.draw();
        }
    };
})();

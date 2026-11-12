/* Simulation 360 - what the program does, apart from its buttons: the project, the worlds and their pictures,
 * the camera and how it is steered, portals and going through them, the selection, characters, the route,
 * and the recorder. The buttons and panels are in sim360-5-ui.js; they call what is here.
 */
(function () {
    'use strict';
    const Core = window.Sim360Core, Solid = window.Sim360Solid;
    const S = window.Sim360 = window.Sim360 || {};

    const MASK_W = 2048, MASK_H = 1024;     // masks and the selection, whatever the panorama's own size
    const ENTER_SHARE = 0.6;                // this far of the way to a portal's surface, and you are through
    const SECOND_SHARE = 0.65;              // a second camera (it follows a figure, slowly) may go this far of the way to the picture's surfaces
    const WALL_SHARE = 0.62;                // and this far of the way to anything else is as near as it gets (nearer,
                                            // the picture is stretched too far to look like anything)
    const ENTER_METRES = 5;                 // a portal far away (in the sky, on the horizon) is entered after this many
                                            // metres towards it: flying most of the way there would leave the picture behind
    const HISTORY_KEPT = 12;                // older versions of a world that can be brought back
    const FEATHER_PIXELS = 90;              // a portal's softest edge: this many pixels of its mask (16 degrees)
    const FEATHER = 0.3;                    // and the edge a portal has when nothing else was asked
    const DEEP_SHARE = 0.8;                 // the camera that draws a world from behind its portal is kept inside that
    const DEEP_KNEE = 0.45;                 // world too: beyond the knee it slows down, and never gets nearer the wall than this
    const TINTS = [[0.15, 0.8, 1], [1, 0.55, 0.15], [0.55, 1, 0.3], [1, 0.3, 0.75], [1, 0.9, 0.2], [0.6, 0.5, 1], [0.2, 1, 0.8], [1, 0.4, 0.3]];
    const CHARACTER_SIZES = { '2:3': [512, 768], '1:1': [640, 640], '3:2': [768, 512], '9:16': [432, 768] };

    const E = S.engine = {
        MASK_W, MASK_H, TINTS, CHARACTER_SIZES,
        options: null, name: null, doc: null, gl: null,
        worldId: null,
        cam: { pos: [0, 0, 0], yaw: 0, pitch: 0, roll: 0, fov: 90 },
        fovWanted: 90, vel: [0, 0, 0], travelled: 0, clock: 0,
        relief: 1, mix: null, fade: null, guide: 1,
        keys: new Set(), look: [0, 0], pad: null,
        cross: null, auto: null, trail: [],
        play: null, take: null, recording: null, exporting: null,
        sel: { data: new Uint8Array(MASK_W * MASK_H), any: false },
        masks: new Map(),               // world id -> [Uint8Array | null] in the order of its portals
        fields: new Map(),              // world id -> Core.DepthField
        loaded: new Map(),              // world id -> what of it is on the graphics card
        videos: new Map(),              // world id -> its <video>
        wandState: null, character: null,
        objectsLoaded: new Map(),       // object id -> what of it is loaded (and a small copy of its transparency)
        solids: new Map(),              // model file and kind of body -> {model, users}: copies of an object share one model
        clips: new Map(),               // line id -> its sound and its video, loaded
        said: new Map(),                // object id -> the video of the line it is saying right now
        saying: null,                   // a single line that is being tried: {line, t}
        chosen: null,                   // the id of the object that is selected
        jobs: new Map(),
        handlers: {},
    };

    /* ---- small things ----------------------------------------------------------------------------------- */

    E.on = (event, fn) => { (E.handlers[event] = E.handlers[event] || []).push(fn); };
    E.emit = (event, data) => { for (const fn of E.handlers[event] || []) { try { fn(data); } catch (e) { console.error('[Simulation 360]', e); } } };
    E.say = (text, kind) => E.emit('status', { text, kind: kind || 'info' });

    // A hidden page's own timers slow to one a second and worse; a worker's do not.
    let sleeper = null, sleepers = new Map(), sleepId = 0;
    E.sleep = ms => new Promise(resolve => {
        try {
            if (!sleeper) {
                sleeper = new Worker(URL.createObjectURL(new Blob(['onmessage = e => setTimeout(() => postMessage(e.data[0]), e.data[1]);'], { type: 'text/javascript' })));
                sleeper.onmessage = e => { const done = sleepers.get(e.data); sleepers.delete(e.data); if (done) done(); };
            }
            sleepers.set(++sleepId, resolve);
            sleeper.postMessage([sleepId, ms]);
        } catch (e) { setTimeout(resolve, ms); }
    });

    async function call(path, options) {
        options = options || {};
        const init = { method: options.body !== undefined || options.raw !== undefined ? 'POST' : 'GET', headers: {} };
        if (options.body !== undefined) { init.body = JSON.stringify(options.body); init.headers['Content-Type'] = 'application/json'; }
        if (options.raw !== undefined) { init.body = options.raw; init.headers['Content-Type'] = 'application/octet-stream'; }
        if (options.headers) Object.assign(init.headers, options.headers);
        let url = './sim360' + path;
        if (options.query) url += '?' + new URLSearchParams(options.query);
        const response = await fetch(url, init);
        if (!response.ok) {
            let detail = response.statusText;
            try { const answer = await response.json(); detail = answer.detail || answer.error || detail; } catch (e) { /* not JSON */ }
            throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
        }
        return response.json();
    }
    E.call = call;

    const uid = prefix => prefix + Date.now().toString(36) + Math.floor(Math.random() * 1296).toString(36);
    E.fileUrl = (world, file) => `./sim360/file/${encodeURIComponent(E.name)}/worlds/${world.id}/${file}`;
    E.world = id => E.doc && E.doc.worlds.find(w => w.id === (id || E.worldId)) || null;
    // the view's shape; a page that is not shown has no size, and then it is the stage's own 16:9
    E.aspect = () => { const c = E.gl.canvas, w = c.clientWidth, h = c.clientHeight; return w > 8 && h > 8 ? w / h : 16 / 9; };

    async function bitmapFrom(url) {
        const blob = await (await fetch(url)).blob();
        return createImageBitmap(blob, { premultiplyAlpha: 'none', colorSpaceConversion: 'none' });
    }
    function pixelsOf(bitmap, width, height) {
        const canvas = document.createElement('canvas');
        canvas.width = width || bitmap.width; canvas.height = height || bitmap.height;
        const ctx = canvas.getContext('2d', { willReadFrequently: true });
        ctx.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
        return ctx.getImageData(0, 0, canvas.width, canvas.height);
    }
    function canvasFromPixels(pixels, width, height) {
        const canvas = document.createElement('canvas');
        canvas.width = width; canvas.height = height;
        canvas.getContext('2d').putImageData(new ImageData(new Uint8ClampedArray(pixels.buffer, pixels.byteOffset, width * height * 4), width, height), 0, 0);
        return canvas;
    }
    const toBlob = (canvas, type, quality) => new Promise(resolve => canvas.toBlob(resolve, type || 'image/png', quality));
    E.canvasFromPixels = canvasFromPixels;

    /* ---- the project ------------------------------------------------------------------------------------ */

    // The project saves itself half a second after every change; the page shows when it last did.
    let saveTimer = null, saving = Promise.resolve();
    E.touch = what => {
        if (!E.doc) return;
        clearTimeout(saveTimer);
        saveTimer = setTimeout(E.save, 500);
        if (!E.unsaved) { E.unsaved = true; E.emit('saved'); }
        if (what) E.emit(what);
    };
    E.save = () => {
        clearTimeout(saveTimer);
        if (!E.doc || !E.name) return saving;
        const name = E.name, doc = JSON.parse(JSON.stringify(E.doc));
        saving = saving.then(() => call('/project/save', { body: { name, doc } }))
            .then(() => { E.savedAt = Date.now(); E.unsaved = false; E.saveFailed = false; E.emit('saved'); })
            .catch(e => { E.saveFailed = true; E.emit('saved'); E.say(String(e.message || e), 'error'); });
        return saving;
    };

    E.start = async canvas => {
        E.gl = new window.Sim360GL(canvas);
        E.options = await call('/options');
        E.kept = [];
        E.library().catch(() => {});
        const list = await call('/projects');
        E.projects = list.projects;
        let last = null;
        try { last = localStorage.getItem('sim360-project'); } catch (e) { /* no storage */ }
        const pick = E.projects.find(p => p.name === last) || E.projects[0];
        if (pick) await E.open(pick.name);
        E.emit('project');
    };

    E.refreshProjects = async () => { E.projects = (await call('/projects')).projects; E.emit('project'); };

    E.open = async name => {
        await E.save();
        const doc = await call('/project', { query: { name } });
        for (const id of Array.from(E.gl.worlds.keys())) E.gl.dropWorld(id);
        for (const video of E.videos.values()) { video.pause(); video.removeAttribute('src'); video.load(); }
        for (const [id, have] of E.objectsLoaded) { if (have.video) { have.video.pause(); have.video.removeAttribute('src'); have.video.load(); } E.gl.dropObject(id); }
        E.objectsLoaded.clear(); E.chosen = null;
        for (const key of Array.from(E.solids.keys())) E.gl.dropModel(key);
        E.solids.clear();
        for (const id of Array.from(E.clips.keys())) dropClip(id);
        E.said.clear(); E.saying = null;
        E.videos.clear(); E.loaded.clear(); E.fields.clear(); E.masks.clear(); softMasks.clear();
        E.doc = doc; E.name = doc.name || name;
        E.doc.worlds = E.doc.worlds || []; E.doc.tours = E.doc.tours || []; E.doc.settings = E.doc.settings || {};
        E.doc.lines = E.doc.lines || [];
        E.cross = null; E.auto = null; E.play = null; E.trail = []; E.mix = null; E.relief = 1; E.character = null;
        E.clearSelection();
        try { localStorage.setItem('sim360-project', E.name); } catch (e) { /* no storage */ }
        const first = E.world(E.doc.start_world) || E.doc.worlds[0];
        E.worldId = null;
        if (first) await E.go(first.id);
        E.emit('project'); E.emit('worlds'); E.emit('world');
        E.ensureClips().then(() => E.emit('lines'));
    };

    E.createProject = async name => {
        const doc = await call('/project/create', { body: { name } });
        await E.refreshProjects();
        await E.open(doc.name);
        return doc.name;
    };

    E.deleteProject = async () => {
        if (!E.name) return;
        await call('/project/delete', { body: { name: E.name } });
        E.doc = null; E.name = null; E.worldId = null;
        await E.refreshProjects();
        if (E.projects[0]) await E.open(E.projects[0].name);
        else { E.emit('worlds'); E.emit('world'); }
    };

    /* ---- worlds ----------------------------------------------------------------------------------------- */

    E.defaults = () => {
        const o = E.options || {};
        return {
            prompt: '', negative: '', lora360: !!o.lora_360, lcm: !!o.lora_lcm,
            steps: 7, cfg: 1.5, sampler: (o.samplers || []).includes('LCM') ? 'LCM' : 'Euler a', seed: -1, checkpoint: '',
            width: 1024, height: 512, tile_x: true, tile_start: -1,
            hires: { enabled: false, scale: 2, denoise: 0.5, upscaler: (o.upscalers || []).find(u => /R-ESRGAN 4x\+$/.test(u)) || 'Lanczos', steps: 0 },
            animatediff: {
                enabled: false, model: (o.motion_modules || []).find(m => /lcm/i.test(m)) || (o.motion_modules || [])[0] || '',
                frames: 16, fps: 8, closed_loop: 'R-P', context: 16, overlap: 4, stride: 1, interp: 'Off', interp_x: 10,
            },
        };
    };

    E.addWorld = name => {
        const world = {
            id: uid('w'), name: name || `World ${E.doc.worlds.length + 1}`, kind: null, file: null, thumb: null, depth: null,
            width: 0, height: 0, near: 1.6, far: 14, strength: 1, gen: E.defaults(), portals: [], history: [],
            start: { pos: [0, 0, 0], yaw: 0, pitch: 0, fov: 90 },
        };
        const current = E.world();
        if (current && current.gen) {           // a new world starts from the settings of the one before it
            world.gen = JSON.parse(JSON.stringify(current.gen)); world.gen.prompt = ''; world.gen.seed = -1;
            world.gen.hires.enabled = false;
        }
        E.doc.worlds.push(world);
        if (!E.doc.start_world) E.doc.start_world = world.id;
        E.touch('worlds');
        return world;
    };

    E.removeWorld = async id => {
        const world = E.world(id);
        if (!world) return;
        E.doc.worlds = E.doc.worlds.filter(w => w.id !== id);
        for (const other of E.doc.worlds) for (const portal of other.portals || []) if (portal.target === id) portal.target = null;
        for (const tour of E.doc.tours) tour.keys = (tour.keys || []).filter(k => k.world !== id);
        if (E.doc.start_world === id) E.doc.start_world = E.doc.worlds[0] ? E.doc.worlds[0].id : null;
        E.gl.dropWorld(id); E.loaded.delete(id); E.fields.delete(id); E.masks.delete(id);
        for (const object of world.objects || []) {
            const have = E.objectsLoaded.get(object.id);
            if (have && have.video) have.video.pause();
            if (have) releaseSolid(have.key);
            E.gl.dropObject(object.id); E.objectsLoaded.delete(object.id);
        }
        const video = E.videos.get(id); if (video) { video.pause(); E.videos.delete(id); }
        if (E.worldId === id) { E.worldId = null; if (E.doc.worlds[0]) await E.go(E.doc.worlds[0].id); }
        await E.save();
        call('/world/delete', { body: { project: E.name, world: id } }).catch(() => {});
        E.emit('worlds'); E.emit('world'); E.emit('tour');
    };

    function fieldFor(world) {
        let field = E.fields.get(world.id);
        if (!field) { field = new Core.DepthField(null, 0, 0); E.fields.set(world.id, field); }
        return field.tune(+world.near || 1.6, +world.far || 14, world.strength == null ? 1 : +world.strength,
            { form: world.form, eye: Core.clamp(+world.eye || 1.65, 0.2, 20), room: world.room, ceiling: world.ceiling });
    }
    const noWorld = new Core.DepthField(null, 0, 0);
    E.field = id => { const world = E.world(id); return world ? fieldFor(world) : noWorld; };

    /* Puts a world on the graphics card (its picture or video, its depth, its portals' masks), unless it is
     * there already. Every caller may wait for it. */
    E.ensure = async id => {
        const world = E.world(id);
        if (!world) return null;
        const state = E.loaded.get(world.id) || {};
        if (state.busy) { await state.busy; return E.ensure(id); }
        const want = {
            file: world.file, depth: world.depth ? world.depth + '?v=' + (world.depth_version || 0) : null,
            portals: (world.portals || []).map(p => p.id + ':' + (p.mask_version || 0)).join(','),
            objects: (world.objects || []).map(o => o.id + ':' + o.file + ':' + (o.depth || '') + ':' + (solidKey(world, o) || '') + ':' + (o.skin || '')).join(','),
        };
        if (state.file === want.file && state.depth === want.depth && state.portals === want.portals && state.objects === want.objects) { E.applyDepth(world); return world; }
        state.busy = (async () => {
            if (state.file !== want.file) {
                const old = E.videos.get(world.id);
                if (old) { old.pause(); old.removeAttribute('src'); old.load(); E.videos.delete(world.id); }
                if (world.file && world.kind === 'video') {
                    const video = document.createElement('video');
                    video.muted = true; video.loop = true; video.playsInline = true; video.preload = 'auto';
                    video.src = E.fileUrl(world, world.file);
                    await new Promise((resolve, reject) => { video.addEventListener('loadeddata', resolve, { once: true }); video.addEventListener('error', () => reject(new Error('the video cannot be played')), { once: true }); });
                    E.videos.set(world.id, video);
                    E.gl.putPicture(world.id, video, true);
                    E.gl.lights.set(world.id, lightFrom(pixelsOf(video, 64, 32)));
                    if (!E.exporting) video.play().catch(() => {});
                } else if (world.file) {
                    const bitmap = await bitmapFrom(E.fileUrl(world, world.file));
                    E.gl.putPicture(world.id, bitmap, false);
                    E.gl.lights.set(world.id, lightFrom(pixelsOf(bitmap, 64, 32)));
                    bitmap.close();
                } else E.gl.dropWorld(world.id);
            }
            if (state.depth !== want.depth) {
                const field = fieldFor(world);
                if (world.depth) {
                    const bitmap = await bitmapFrom(E.fileUrl(world, want.depth));
                    const image = pixelsOf(bitmap), near = new Float32Array(image.width * image.height), d = image.data;
                    for (let i = 0, n = near.length; i < n; i++) near[i] = (d[i * 4] * 256 + d[i * 4 + 1]) / 65535;
                    field.n = near; field.w = image.width; field.h = image.height;
                    bitmap.close();
                } else { field.n = null; }
                state.depthTuned = null;
            }
            if (state.portals !== want.portals) await loadMasks(world);
            if (state.objects !== want.objects) await loadObjects(world);
            Object.assign(state, want);
        })();
        E.loaded.set(world.id, state);
        try { await state.busy; } catch (e) { E.say(`${world.name}: ${e.message || e}`, 'error'); } finally { state.busy = null; }
        E.applyDepth(world);
        return world;
    };

    /* the sphere of a world follows its near / far / strength */
    E.applyDepth = world => {
        const state = E.loaded.get(world.id) || {}, field = fieldFor(world);
        const tuned = `${field.near}|${field.far}|${field.strength}|${field.form}|${field.eye}|${field.room}|${field.ceiling}|${world.depth}|${world.depth_version || 0}`;
        if (state.depthTuned === tuned) return;
        E.gl.setDepth(world.id, field);
        state.depthTuned = tuned;
        E.loaded.set(world.id, state);
    };

    async function loadMasks(world) {
        const masks = [];
        for (const portal of (world.portals || []).slice(0, window.Sim360GL.PORTALS)) {
            let mask = null;
            try {
                const bitmap = await bitmapFrom(E.fileUrl(world, portal.mask) + '?v=' + (portal.mask_version || 0));
                const image = pixelsOf(bitmap, MASK_W, MASK_H);
                mask = new Uint8Array(MASK_W * MASK_H);
                for (let i = 0, n = mask.length; i < n; i++) mask[i] = image.data[i * 4];
                bitmap.close();
                if (portal.level == null) portal.level = levelWith(mask, portal.center);
            } catch (e) { console.warn('[Simulation 360] a portal mask is missing', portal.id); }
            masks.push(mask);
        }
        E.masks.set(world.id, masks);
        E.showMasks(world);
    }

    /* can it be walked into: is the horizon, at the portal's heading, inside its shape */
    function levelWith(mask, center) {
        const u = ((((center && +center.yaw) || 0) / 360 + 0.5) % 1 + 1) % 1;
        return mask[(MASK_H >> 1) * MASK_W + Math.min(MASK_W - 1, Math.floor(u * MASK_W))] > 127;
    }

    /* How soft a portal's edge is, 0..1: where its shape ends, the world behind it fades into this one over
     * a band this wide. */
    E.feather = portal => portal.feather == null ? FEATHER : Core.clamp(+portal.feather, 0, 1);

    /* The masks go to the graphics card with their soft edges; the masks themselves stay as they were drawn
     * (they say where a portal is: what a click lands on, where the camera walks in). */
    const softMasks = new Map();
    E.showMasks = world => {
        const masks = E.masks.get(world.id) || [];
        const shown = (world.portals || []).slice(0, window.Sim360GL.PORTALS).map((portal, i) => {
            const mask = masks[i];
            if (!mask) return null;
            const feather = E.feather(portal);
            let have = softMasks.get(portal.id);
            if (!have || have.from !== mask || have.feather !== feather) {
                have = { from: mask, feather, mask: Core.feathered(mask, MASK_W, MASK_H, feather * FEATHER_PIXELS) };
                softMasks.set(portal.id, have);
            }
            return have.mask;
        });
        E.gl.setPortalMasks(world.id, shown.concat(new Array(8).fill(null)).slice(0, 8), MASK_W, MASK_H);
    };

    /* The pictures of a world's objects go onto the graphics card; a small copy of each one's transparency
     * stays here, to know what a click landed on. */
    async function loadObjects(world) {
        const wanted = new Set();
        for (const object of world.objects || []) {
            wanted.add(object.id);
            if (object.motion && object.motion.file) await loadMotion(world, object);
            const wanted3d = solidKey(world, object);
            const signature = object.file + ':' + (object.depth || '') + ':' + (wanted3d || '') + ':' + (object.skin || '');
            const have = E.objectsLoaded.get(object.id);
            if (have && have.signature === signature) continue;
            try {
                if (have && have.video) { have.video.pause(); have.video.removeAttribute('src'); have.video.load(); }
                const still = await bitmapFrom(E.fileUrl(world, object.kind === 'video' ? object.poster : object.file));
                const small = pixelsOf(still, 64, Math.max(8, Math.round(64 * still.height / still.width)));
                const alpha = new Uint8Array(small.width * small.height);
                for (let i = 0; i < alpha.length; i++) alpha[i] = small.data[i * 4 + 3];
                const shape = object.depth ? await bitmapFrom(E.fileUrl(world, object.depth)) : null;
                let video = null;
                if (object.kind === 'video') {
                    video = document.createElement('video');
                    video.muted = true; video.loop = true; video.playsInline = true; video.preload = 'auto';
                    video.src = E.fileUrl(world, object.file);
                    await new Promise((resolve, reject) => { video.addEventListener('loadeddata', resolve, { once: true }); video.addEventListener('error', () => reject(new Error('the video cannot be played')), { once: true }); });
                    if (!E.exporting) video.play().catch(() => {});
                }
                E.gl.putObject(object.id, video || still, shape, !!video);
                still.close(); if (shape) shape.close();
                let model = null;
                if (have) releaseSolid(have.key);
                if (wanted3d) {
                    try { model = await acquireSolid(wanted3d, world, object); }
                    catch (e) { console.warn('[Simulation 360] a model could not be loaded', object.id, e); E.say(`${object.name || object.id}: ${e.message || e}`, 'error'); }
                }
                if (object.skin) { const worn = await bitmapFrom(E.fileUrl(world, object.skin)); E.gl.putSkin(object.id, worn); worn.close(); }
                E.objectsLoaded.set(object.id, { signature, world: world.id, alpha, aw: small.width, ah: small.height, video, model, key: model ? wanted3d : null });
            } catch (e) { console.warn('[Simulation 360] an object could not be loaded', object.id, e); }
        }
        for (const [id, have] of Array.from(E.objectsLoaded)) {
            if (have.world !== world.id || wanted.has(id)) continue;
            if (have.video) { have.video.pause(); have.video.removeAttribute('src'); have.video.load(); }
            releaseSolid(have.key);
            E.gl.dropObject(id); E.objectsLoaded.delete(id);
        }
    }

    /* The worlds a world's portals lead to are loaded too: they are seen through its portals. And the worlds
     * behind those, and behind those again: a portal shows three worlds deep. */
    E.ensureNeighbours = async (id, further) => {
        const world = E.world(id);
        if (!world) return;
        const targets = Array.from(new Set((world.portals || []).filter(p => p.target && E.world(p.target)).map(p => p.target)));
        await Promise.all(targets.map(target => E.ensure(target)));
        further = further == null ? 2 : further;
        if (further > 0) for (const target of targets) await E.ensureNeighbours(target, further - 1);
    };

    E.go = async (id, pose) => {
        const world = await E.ensure(id);
        if (!world) return;
        E.worldId = world.id;
        const start = pose || world.start || {};
        E.cam = { pos: (start.pos || [0, 0, 0]).slice(), yaw: +start.yaw || 0, pitch: +start.pitch || 0, roll: 0, fov: +start.fov || 90 };
        E.fovWanted = E.cam.fov; E.vel = [0, 0, 0];
        E.cross = null; E.auto = null; E.mix = null; E.relief = 1; E.fade = null;
        E.emit('world');
        E.ensureNeighbours(world.id);
    };

    /* What a job made becomes the world's current version; the version before it can be brought back. */
    E.adopt = (world, result, extra) => {
        if (world.file) {
            const keep = {};
            for (const k of ['kind', 'file', 'poster', 'thumb', 'depth', 'depth_version', 'width', 'height', 'frames', 'fps', 'seed', 'infotext']) keep[k] = world[k];
            world.history = (world.history || []).concat([keep]).slice(-HISTORY_KEPT);
        }
        for (const k of ['kind', 'file', 'poster', 'thumb', 'depth', 'width', 'height', 'frames', 'fps']) world[k] = result[k] === undefined ? null : result[k];
        if (result.seed !== undefined) world.seed = result.seed;
        if (result.infotext !== undefined) world.infotext = result.infotext;
        world.depth_version = 0;
        Object.assign(world, extra || {});
        E.touch('worlds');
    };

    E.undoWorld = async id => {
        const world = E.world(id);
        if (!world || !(world.history || []).length) return;
        Object.assign(world, world.history.pop());
        E.touch('worlds');
        await E.ensure(world.id);
        E.emit('world');
    };

    /* ---- jobs ------------------------------------------------------------------------------------------- */

    /* Starts a job and waits for it. `onProgress` gets every answer on the way. */
    E.job = async (kind, params, onProgress, started) => {
        let job = started || await call('/job/start', { body: { kind, params } });
        const info = { id: job.id, kind, world: params && params.world, progress: 0, stage: '' };
        E.jobs.set(job.id, info);
        E.emit('jobs');
        try {
            for (;;) {
                job = await call('/job', { query: { job_id: job.id, preview: onProgress ? 'true' : 'false' } });
                Object.assign(info, { progress: job.progress || 0, stage: job.stage, status: job.status, position: job.position });
                if (onProgress) onProgress(job);
                E.emit('jobs');
                if (job.status === 'done') return job.result;
                if (job.status === 'failed') throw new Error(job.error || 'the job failed');
                if (job.status === 'cancelled') throw new Error('cancelled');
                await E.sleep(job.status === 'running' ? 450 : 900);
            }
        } finally { E.jobs.delete(info.id); E.emit('jobs'); }
    };

    /* Tile X for everything Forge draws (txt2img, Deforum), until it is switched off or Forge restarts. */
    E.tileEverything = async on => { E.options.tile_everything = (await call('/tile-everything', { body: { on: !!on } })).on; return E.options.tile_everything; };

    E.cancelJobs = () => Promise.all(Array.from(E.jobs.keys()).map(id => call('/job/cancel', { body: { job_id: id } }).catch(() => {})));

    /* the prompt as it is sent: the user's words plus the panorama LoRA and the LCM LoRA, when ticked */
    E.promptFor = gen => {
        const o = E.options || {};
        let prompt = (gen.prompt || '').trim();
        if (gen.lora360 && o.lora_360 && !new RegExp(`<lora:${o.lora_360}[:>]`, 'i').test(prompt)) prompt += `, qxj <lora:${o.lora_360}:1>`;
        if (gen.lcm && o.lora_lcm && !new RegExp(`<lora:${o.lora_lcm}[:>]`, 'i').test(prompt)) prompt += ` <lora:${o.lora_lcm}:1>`;
        return prompt.replace(/^,\s*/, '');
    };

    E.generate = async id => {
        const world = E.world(id);
        if (!world) return;
        const gen = world.gen, steps = +gen.steps || 7;
        const settings = {
            prompt: E.promptFor(gen), negative: gen.negative, steps, cfg: gen.cfg, sampler: gen.sampler, seed: gen.seed,
            checkpoint: gen.checkpoint || null, width: gen.width, height: gen.height, tile_x: gen.tile_x !== false,
            // the first steps lay the scene out freely, the rest close the ring
            tile_start: +gen.tile_start >= 0 ? +gen.tile_start : Math.max(1, Math.round(steps * 0.15)),
            hires: gen.hires, animatediff: gen.animatediff,
        };
        let shown = 0;
        const result = await E.job('world', { project: E.name, world: world.id, settings }, job => {
            if (job.preview && E.worldId === world.id && ++shown) {
                fetch(job.preview).then(r => r.blob()).then(createImageBitmap).then(bitmap => {
                    if (E.jobs.size) { E.gl.putPicture(world.id, bitmap, false); const s = E.loaded.get(world.id); if (s) s.file = '(preview)'; }
                    bitmap.close();
                }).catch(() => {});
            }
        });
        E.adopt(world, result, { used: JSON.parse(JSON.stringify(gen)) });
        await E.ensure(world.id);
        E.emit('world');
        return result;
    };

    E.hires = async id => {
        const world = E.world(id);
        if (!world || world.kind !== 'image') throw new Error('Hires. fix works on picture worlds');
        const gen = world.gen;
        const settings = {
            prompt: E.promptFor(gen), negative: gen.negative, steps: gen.steps, cfg: gen.cfg, sampler: gen.sampler,
            seed: world.seed != null && world.seed >= 0 ? world.seed : gen.seed, checkpoint: gen.checkpoint || null,
            scale: gen.hires.scale, denoise: gen.hires.denoise, upscaler: gen.hires.upscaler, tile_x: gen.tile_x !== false,
        };
        const result = await E.job('hires', { project: E.name, world: world.id, file: world.file, infotext: world.infotext, settings });
        E.adopt(world, result);
        await E.ensure(world.id);
        E.emit('world');
        return result;
    };

    E.upload = async (id, file, painted, infotext) => {
        const world = E.world(id), query = { project: E.name, world: world.id, filename: file.name || 'panorama.png', painted: painted ? 'true' : 'false' };
        // with Hires. fix ticked, an uploaded video is enlarged frame by frame with the chosen upscaler
        const hires = !painted && world.gen && world.gen.hires;
        if (hires && hires.enabled && !/^Latent/.test(hires.upscaler || '')) { query.enlarge = hires.scale; query.upscaler = hires.upscaler; }
        if (!painted && world.gen && world.gen.bounce) query.bounce = 'true';
        const started = await call('/upload', {
            raw: file, query,
            headers: infotext ? { 'x-sim360-infotext': encodeURIComponent(infotext).slice(0, 6000) } : undefined,
        });
        const result = await E.job('import', { world: world.id }, null, started);
        E.adopt(world, result, painted ? { seed: world.seed, infotext: world.infotext } : { seed: null, infotext: result.infotext || null });
        await E.ensure(world.id);
        E.emit('world');
        return result;
    };

    E.remeasure = async id => {
        const world = E.world(id);
        const source = world.kind === 'video' ? world.poster : world.file;
        const number = +(/-(\d+)\./.exec(world.file) || [0, 1])[1];
        const result = await E.job('depth', { project: E.name, world: world.id, file: source, number });
        world.depth = result.depth; world.thumb = result.thumb || world.thumb;
        world.depth_version = (world.depth_version || 0) + 1;       // the same file name, measured again
        E.touch('worlds');
        await E.ensure(world.id);
    };

    /* ---- drawing state ---------------------------------------------------------------------------------- */

    E.arrival = portal => {
        const target = E.world(portal.target);
        const base = portal.arrival || (target && target.start) || {};
        return { pos: (base.pos || [0, 0, 0]).slice(), yaw: +base.yaw || 0, pitch: +base.pitch || 0 };
    };
    /* The way into a portal: the direction, from the world's centre, in which it is entered. A portal that
     * can be walked into (the horizon is inside its shape) is entered at eye level and not through the
     * middle of its shape: the far part of a street reaches up to the sky, and nobody walks into the sky.
     * A portal may also be given a way in of its own. */
    E.wayIn = portal => {
        if (portal.way) return { yaw: +portal.way.yaw || 0, pitch: +portal.way.pitch || 0 };
        const c = portal.center || { yaw: 0, pitch: 0 };
        return portal.level ? { yaw: +c.yaw || 0, pitch: 0 } : { yaw: +c.yaw || 0, pitch: +c.pitch || 0 };
    };
    /* by how much the world behind a portal is turned: the way in looks along the arrival's heading */
    E.turnOf = portal => E.arrival(portal).yaw - E.wayIn(portal).yaw;

    /* Does the portal show the world behind it in its depth (drawn from where the camera would stand in it),
     * so that walking through is seamless? Otherwise it shows that world flat, and going through is a plunge. */
    E.deepPortal = portal => !!(portal && portal.target && E.world(portal.target) && portal.see_through !== false && portal.deep !== false);

    /* the camera as it would stand in the world behind a portal (see Core.beyond) */
    E.beyond = (portal, cam) => {
        const door = E.doorstep(portal).pos, there = Core.beyond(cam, door, E.arrival(portal).pos, E.turnOf(portal));
        // It is kept inside that world: softly while the portal is far (a world seen from near its own wall
        // is a smear), and on the doorstep exactly as a visitor is kept, so that stepping through does not
        // move it.
        const reach = Math.max(0.5, Math.hypot(door[0], door[1], door[2])), field = E.field(portal.target);
        const close = Core.smooth(1 - Math.hypot(cam.pos[0] - door[0], cam.pos[1] - door[1], cam.pos[2] - door[2]) / reach);
        const soft = Core.eased(there.pos, field, DEEP_KNEE, DEEP_SHARE), hard = Core.constrain(there.pos, field, WALL_SHARE + 0.03).pos;
        there.pos = [0, 1, 2].map(j => Core.lerp(soft[j], hard[j], close));
        return there;
    };

    /* What is drawn behind the portals of a world: for the portals the camera looks at (two at most), the
     * world they lead to as this camera would see it from inside, and behind each of them the next world,
     * and the one after it. A world farther on than that is shown flat inside its portal.
     *
     * Going through a portal moves every world one place nearer, and nothing may change in the picture
     * when it does. So the third world on is not simply in its depth: it comes into it as the camera nears
     * the portal in front of it. At the start of a street it is flat (as it was a moment ago, when it was
     * the fourth), and on the doorstep it is wholly in its depth (as it will be a moment later, as the
     * second).
     * `o`: {t, time, over, via}; `via` is the portal being gone through, which is always among them. */
    E.through = (id, cam, o, level) => {
        const world = E.world(id);
        if (!world || level > 3) return [];
        const ahead = Core.dirFromAngles(cam.yaw, cam.pitch), field = E.field(id), found = [];
        (world.portals || []).slice(0, window.Sim360GL.PORTALS).forEach((portal, index) => {
            if (!E.deepPortal(portal)) return;
            const ready = E.gl.world(portal.target);
            if (!ready || !ready.tex) return;
            const way = E.wayIn(portal), d = Core.dirFromAngles(way.yaw, way.pitch), far = field.distTowards(d[0], d[1], d[2]);
            const to = [d[0] * far - cam.pos[0], d[1] * far - cam.pos[1], d[2] * far - cam.pos[2]], length = Math.hypot(to[0], to[1], to[2]) || 1;
            const off = Math.acos(Core.clamp((to[0] * ahead[0] + to[1] * ahead[1] + to[2] * ahead[2]) / length, -1, 1)) / Core.RAD;
            found.push({ portal, index, dir: to.map(v => v / length), off: portal.id === o.via ? -1e3 : off - Math.max(+portal.span || 8, +portal.rise || 8) });
        });
        found.sort((a, b) => a.off - b.off);
        return found.slice(0, level > 1 ? 1 : 2).filter(f => f.off < 95).map(f => {
            // how near the camera is to going through: of the camera itself, for the portals of the world it is in
            let near = o.near;
            if (level === 1) { const door = E.doorstep(f.portal).pos, reach = Math.max(0.5, Math.hypot(door[0], door[1], door[2])); near = Core.clamp(1 - Math.hypot(cam.pos[0] - door[0], cam.pos[1] - door[1], cam.pos[2] - door[2]) / reach, 0, 1); }
            if (level === 3 && !(near > 0.004)) return null;
            const target = f.portal.target, inner = E.beyond(f.portal, cam);
            const state = {
                world: target, cam: inner, relief: 1, mix: null, fade: null, time: o.time, lids: 0, over: o.over, guide: 0, selection: false,
                portals: E.portalStates(target, true), objects: E.objectStates(target, inner.pos, 1, null, inner, o.t), weather: E.weatherState(target, 1),
            };
            state.through = E.through(target, inner, { t: o.t, time: o.time, over: o.over, near }, level + 1);
            return { index: f.index, portal: f.portal, dir: f.dir, state, weight: level === 3 ? Core.smooth(near) : 1 };
        }).filter(Boolean);
    };

    /* a drawing state gets the worlds behind its portals; a crossing that goes into one of them shows that
     * very picture taking over */
    E.deepen = (state, t) => {
        const via = state.mix && state.mix.deep ? state.mix.via : null;
        state.through = E.through(state.world, state.cam, { t, time: state.time, over: state.over, via }, 1);
        if (state.mix && state.mix.deep) {
            const found = state.through.find(x => x.portal.id === via);
            state.mix = found ? Object.assign({}, state.mix, { index: found.index, dir: found.dir }) : Object.assign({}, state.mix, { deep: false });
        }
        return state;
    };

    E.portalStates = (id, clean) => {
        const world = E.world(id);
        if (!world) return [];
        return (world.portals || []).slice(0, window.Sim360GL.PORTALS).map((portal, i) => ({
            world: portal.target, see: portal.target && portal.see_through !== false && !(clean === 'closed') ? 1 : 0,
            turn: E.turnOf(portal), tint: TINTS[i % TINTS.length],
        }));
    };

    /* What the graphics card is asked to draw. `clean`: no guides and no selection, as a recording wants it;
     * 'closed' also shuts the portals, so a door looks like the door in the picture. */
    E.renderState = (clean, pose) => {
        const rig = E.rig();
        const base = pose || E.cam;
        // while something is being marked or placed the picture stands still: what is drawn on the screen
        // has to land exactly where it was drawn
        // while a route plays, each of its moments says how the camera is carried and how far the eyes are shut
        const now = E.play && E.moment ? E.moment : null;
        const cam = pose || E.steady ? { pos: base.pos, yaw: base.yaw, pitch: base.pitch, roll: base.roll || 0, fov: base.fov }
            : Core.carried(E.cam, (now && now.rig) || rig.kind, now ? E.play.t : E.clock, E.travelled, now && now.rigStrength != null ? now.rigStrength : rig.strength, now ? now.speed : undefined);
        const state = {
            world: E.worldId, cam, relief: E.relief, mix: E.mix, fade: E.fade, time: E.clock, lids: now && !pose ? now.lids || 0 : 0, over: !!E.doc.settings.objects_over,
            guide: clean ? 0 : E.guide, selection: !clean && E.sel.any, portals: E.portalStates(E.worldId, clean), aspect: E.aspect(),
            objects: E.objectStates(E.worldId, cam.pos, E.relief * (1 - (E.mix ? E.mix.amount : 0)), clean ? null : E.chosen, cam),
            weather: E.weatherState(E.worldId, E.relief * (1 - (E.mix ? E.mix.amount : 0))),
        };
        // the other shot of a dissolve, drawn as a picture of its own and laid over this one
        if (now && now.blend && !pose) state.blend = { state: E.momentState(now.blend.moment, E.play.t, state.aspect), amount: now.blend.amount };
        // while a place is being marked the portals stay flat too: the picture must not move under the pen
        return clean === 'closed' || E.relief < 1 ? state : E.deepen(state);
    };

    /* A world's weather as it is drawn: rain or snow and mist, fading with the world when a portal is passed. */
    E.weatherState = (id, shown) => {
        const world = E.world(id), w = world && world.weather;
        if (!w || (!w.kind && !(w.mist > 0)) || shown <= 0.003) return null;
        return { kind: w.kind || null, amount: (w.amount == null ? 0.6 : +w.amount) * shown, wind: +w.wind || 0, mist: (+w.mist || 0) * shown };
    };

    /* ---- objects: where each one stands ------------------------------------------------------------------ */

    /* An object's sheet: it stands upright on its anchor (the middle of its bottom edge) and faces the
     * camera, turning with it, unless it was given a heading of its own. */
    E.objectSheet = (object, from, placed) => {
        const a = placed ? placed.pos : object.pos;
        let n;
        if (object.turn == null) {
            n = [from[0] - a[0], 0, from[2] - a[2]];
            const length = Math.hypot(n[0], n[2]);
            n = length > 1e-4 ? [n[0] / length, 0, n[2] / length] : [0, 0, 1];
        } else { const d = Core.dirFromAngles(object.turn, 0); n = [d[0], 0, d[2]]; }
        const height = placed ? placed.size : +object.size || 1.7;
        const sheet = { anchor: a, right: [n[2], 0, -n[0]], up: [0, 1, 0], normal: n, width: height * object.width / Math.max(1, object.height) * (+object.wide || 1), height: height * (+object.tall || 1) };
        return +object.tilt || +object.roll ? leaned(sheet, +object.tilt || 0, +object.roll || 0) : sheet;
    };

    /* a direction turned about an axis (both of length one), by degrees */
    function turnedAbout(v, axis, degrees) {
        const a = degrees * Core.RAD, c = Math.cos(a), s = Math.sin(a), d = v[0] * axis[0] + v[1] * axis[1] + v[2] * axis[2];
        const x = [axis[1] * v[2] - axis[2] * v[1], axis[2] * v[0] - axis[0] * v[2], axis[0] * v[1] - axis[1] * v[0]];
        return [0, 1, 2].map(j => v[j] * c + x[j] * s + axis[j] * d * (1 - c));
    }

    /* A sheet that leans: tipped back or forward (tilt) and turned in its own plane (roll), about its middle. */
    function leaned(sheet, tilt, roll) {
        const middle = [0, 1, 2].map(j => sheet.anchor[j] + sheet.up[j] * sheet.height / 2);
        let right = sheet.right, up = sheet.up, normal = sheet.normal;
        if (roll) { right = turnedAbout(right, normal, roll); up = turnedAbout(up, normal, roll); }
        if (tilt) { up = turnedAbout(up, right, tilt); normal = turnedAbout(normal, right, tilt); }
        return Object.assign({}, sheet, { right, up, normal, anchor: middle.map((v, j) => v - up[j] * sheet.height / 2) });
    }

    /* Where an object is at a moment. An object may walk up and down a street (walk: {speed, yaw, span,
     * phase}), follow a path of its own through the scene's time (path: [{t, pos, size, opacity}]), and step
     * aside when the camera comes near (shy: metres). All of it is worked out from the time and the camera
     * alone, so a recording drawn twice shows the same thing. */
    /* how high above the ground the picture of a world was taken: where the ground of its scene is */
    E.eye = id => { const world = E.world(id); return Core.clamp(+(world && world.eye) || 1.65, 0.2, 20); };

    /* where a thing standing on the ground at x, z of the scene's map is drawn (Core.stood); null in a world
     * with no depth, which has no ground */
    E.stand = (id, x, z, lift) => {
        const field = E.field(id);
        return field.n ? Core.stood(field, x, z, E.eye(id), lift) : null;
    };

    E.objectPlace = (object, t, cam, worldId) => {
        let pos = object.pos, size = +object.size || 1.7, opacity = object.opacity == null ? 1 : +object.opacity;
        const path = object.path;
        if (path && path.length) {
            let i = 0;
            while (i < path.length - 1 && t >= path[i + 1].t) i++;
            const a = path[i], b = path[Math.min(path.length - 1, i + 1)];
            const x = b.t > a.t ? Core.pathShare(b, (t - a.t) / (b.t - a.t)) : 0;
            const between = key => a[key] == null && b[key] == null ? null : Core.lerp(a[key] == null ? b[key] : a[key], b[key] == null ? a[key] : b[key], x);
            pos = [0, 1, 2].map(j => Core.lerp(a.pos[j], b.pos[j], x));
            if (between('size') != null) size = between('size');
            if (between('opacity') != null) opacity = between('opacity');
        }
        const carried = mocapCarried(object, t, worldId);
        if (carried) pos = [carried[0], pos[1], carried[1]];
        const walk = object.walk;
        if (walk && +walk.speed) {
            const span = Math.max(1, +walk.span || 12), d = Core.dirFromAngles(+walk.yaw || 0, 0);
            const along = (((t * walk.speed + (+walk.phase || 0)) % span) + span) % span - span / 2;
            pos = [pos[0] + d[0] * along, pos[1], pos[2] + d[2] * along];
            opacity *= Core.smooth(Core.clamp((span / 2 - Math.abs(along)) / Math.min(1.5, span / 4), 0, 1));       // out of nowhere, and back into it
        }
        // One that stands on the ground: its place is a place on the map of the scene, and its height
        // (pos[1]) is above the ground. `stood` is where that puts its feet in the world.
        const lift = pos[1];
        let stood = object.on_ground && worldId ? E.stand(worldId, pos[0], pos[2], lift) : null;
        if (+object.shy > 0 && cam) {
            const at = stood ? stood.pos : pos;
            const dx = at[0] - cam.pos[0], dz = at[2] - cam.pos[2], near = Math.hypot(dx, dz), reach = +object.shy;
            if (near < reach) {
                const b = Core.basis(cam.yaw, 0, 0);
                let side = dx * b.r[0] + dz * b.r[2];
                side = Math.abs(side) < 0.02 ? ((object.id.charCodeAt(object.id.length - 1) & 1) ? 1 : -1) : Math.sign(side);
                const away = Core.smooth(1 - near / reach) * (object.dodge == null ? 1.1 : +object.dodge) * side;
                if (stood) {
                    // it steps aside where it is, and is still on the ground there
                    const eye = E.eye(worldId), k = stood.small;
                    const moved = Core.mapped([at[0] + b.r[0] * away, -eye * k, at[2] + b.r[2] * away], eye);
                    stood = E.stand(worldId, moved.x, moved.z, lift) || stood;
                } else pos = [pos[0] + b.r[0] * away, pos[1], pos[2] + b.r[2] * away];
            }
        }
        let small = 1;
        if (stood) {
            small = stood.small; size *= small;
            // its feet are where its picture's feet are, which may be a little above the picture's bottom edge
            pos = [stood.pos[0], stood.pos[1] - (object.box ? (1 - Math.min(1, object.box[3])) * size : 0), stood.pos[2]];
        }
        return { pos, size, opacity, small };
    };

    /* An object is told to stand on the ground, or released from it, without moving in the picture: its place
     * in the world becomes its place on the scene's map, or the other way round. */
    E.groundObject = (object, on, id) => {
        id = id || E.worldId;
        const under = size => (object.box ? (1 - Math.min(1, object.box[3])) * size : 0);
        if (on && !object.on_ground) {
            const size = +object.size || 1.7, feet = [object.pos[0], object.pos[1] + under(size), object.pos[2]];
            if (E.field(id).n && feet[1] < -0.05) {
                const at = Core.mapped(feet, E.eye(id));
                object.pos = [+at.x.toFixed(3), 0, +at.z.toFixed(3)];
                object.size = +(size / Core.clamp(at.small, 0.05, 2.5)).toFixed(3);
            } else object.pos = [object.pos[0], 0, object.pos[2]];
        } else if (!on && object.on_ground) {
            const now = E.objectPlace(Object.assign({}, object, { walk: null, path: null, shy: 0 }), 0, null, id);
            object.pos = now.pos.map(v => +v.toFixed(3)); object.size = +now.size.toFixed(3);
        }
        object.on_ground = !!on;
        E.touch('objects');
    };

    /* an object where it is right now, in the world being looked at */
    E.objectNow = (object, id) => {
        const scene = E.play ? E.play.t : 0;
        return E.objectPlace(object, object.walk ? (E.play ? scene : E.clock) : scene, E.cam, id || E.worldId);
    };

    /* The steps of a thing that walks: {phase, strength, across, body}, or null while it stands. The steps
     * follow the metres it has gone, so they neither slide nor run on the spot. */
    E.gait = (object, sheet, walkTime, sceneTime, small) => {
        const walk = object.walk && +object.walk.speed ? object.walk : null, path = !walk && object.path && object.path.length > 1 ? object.path : null;
        const strength = object.gait == null ? (walk ? 1 : 0) : +object.gait;
        if (!(strength > 0) || (!walk && !path)) return null;
        const box = object.box || [0, 0, 1, 1], tall = Math.max(0.2, Math.min(1, box[3]) - box[1]);
        let walked, heading, moving = 1;
        if (walk) {
            walked = walkTime * walk.speed + (+walk.phase || 0);
            const d = Core.dirFromAngles(+walk.yaw || 0, 0), way = Math.sign(walk.speed);
            heading = [d[0] * way, d[2] * way];
        } else {
            walked = Core.pathWalked(path, sceneTime);
            const a = E.objectPlace({ pos: object.pos, path }, sceneTime - 0.1, null).pos, b = E.objectPlace({ pos: object.pos, path }, sceneTime + 0.1, null).pos;
            const dx = b[0] - a[0], dz = b[2] - a[2], length = Math.hypot(dx, dz);
            moving = Core.clamp(length / 0.2 / 0.25, 0, 1);         // slower than a quarter of a metre a second: the steps die down
            heading = length > 1e-4 ? [dx / length, dz / length] : [0, 0];
        }
        if (moving <= 0.01) return null;
        const middle = (box[0] + box[2]) / 2;
        return {
            // two steps to a stride, a step about three eighths of the figure's height (its own height, on the map)
            phase: (walked / (0.74 * sheet.height / (small || 1) * tall) * 2 * Math.PI) % (2 * Math.PI), strength: strength * moving,
            across: heading[0] * sheet.right[0] + heading[1] * sheet.right[2],
            body: [object.flip ? 1 - middle : middle, 1 - Math.min(1, box[3]), 1 - box[1]],
        };
    };

    E.objectStates = (id, from, shown, chosen, cam, t, shot) => {
        const world = E.world(id);
        if (!world || !(world.objects || []).length) return [];
        // the scene's clock: the route's time while it plays or is recorded; otherwise paths rest at their
        // start and walkers keep walking
        const staged = t != null || !!E.play, sceneTime = t != null ? t : (E.play ? E.play.t : 0), walkTime = staged ? sceneTime : E.clock;
        const far = p => Math.hypot(p[0] - from[0], p[1] - from[1], p[2] - from[2]);
        // Through the eyes of the figure whose eyes the route is, its own body is seen (its head is not: the
        // camera is in it), unless `walker_body` is off; a figure that is only a picture is not seen at all.
        // (the route may be the eyes of one figure in each world it goes through: `route_eyes` on each of them)
        const list = [], tour = E.doc.tours.length ? E.tour() : null, inEyes = staged && tour && (shot || E.shotAt(sceneTime)) === 'a';
        for (const object of world.objects) {
            if (!E.objectsLoaded.has(object.id)) continue;
            const ownEyes = !!(inEyes && (object.id === tour.walker || object.route_eyes));
            if (ownEyes && !(tour.walker_body !== false && E.objectsLoaded.get(object.id).model && !object.flat)) continue;
            let sheet, opacity;
            if (object.held && cam) {
                // held in front of the camera (a bicycle's handlebars, a telephone): it goes where the eyes go
                const h = object.held, b = Core.basis(cam.yaw, cam.pitch, cam.roll || 0), dist = +h.dist || 0.8, height = +h.size || 0.5;
                const centre = [0, 1, 2].map(j => cam.pos[j] + (b.f[j] + b.r[j] * (+h.x || 0) + b.u[j] * (+h.y || 0)) * dist);
                sheet = { anchor: centre.map((v, j) => v - b.u[j] * height / 2), right: b.r, up: b.u, normal: b.f.map(v => -v), width: height * object.width / Math.max(1, object.height), height };
                opacity = E.objectPlace(Object.assign({}, object, { walk: null, shy: 0 }), sceneTime, null).opacity;
                sheet.far = 0;
            } else {
                const placed = E.objectPlace(object.walk ? Object.assign({}, object, { walk: Object.assign({}, object.walk, { _: 0 }) }) : object, object.walk ? walkTime : sceneTime, cam || { pos: from, yaw: 0 }, id);
                sheet = E.objectSheet(object, from, placed);
                opacity = placed.opacity;
                sheet.far = far(placed.pos);
                sheet.gait = E.gait(object, sheet, walkTime, sceneTime, placed.small);
                sheet.small = placed.small;
            }
            // what is held in the hands does not fade with the world when a portal is passed: it comes along
            const seen = opacity * (object.held ? 1 : shown);
            if (seen <= 0.003) continue;
            const have = E.objectsLoaded.get(object.id);
            sheet.headless = ownEyes;
            if (have.model && !object.flat) { sheet.solid = E.solidState(object, have.model, sheet, id, walkTime, sceneTime, from); sheet.solid.key = have.key; }
            list.push(Object.assign(sheet, {
                id: object.id, thick: (object.relief == null ? 0.2 : +object.relief) * sheet.height,
                opacity: seen, mirror: !!object.flip, chosen: object.id === chosen,
                speech: E.said.has(object.id) ? E.said.get(object.id).video : null,
            }));
        }
        return list.sort((a, b) => b.far - a.far);
    };

    /* where a ray from `origin` meets the world's surface */
    E.surfaceHit = (origin, d, id) => {
        const field = E.field(id);
        const outside = t => { const p = [origin[0] + d[0] * t, origin[1] + d[1] * t, origin[2] + d[2] * t]; return Math.hypot(p[0], p[1], p[2]) >= field.distTowards(p[0], p[1], p[2]); };
        let low = 0, high = 0.1;
        for (let i = 0; i < 60 && !outside(high); i++) { low = high; high *= 1.25; }
        for (let i = 0; i < 18; i++) { const mid = (low + high) / 2; if (outside(mid)) high = mid; else low = mid; }
        return [origin[0] + d[0] * low, origin[1] + d[1] * low, origin[2] + d[2] * low];
    };

    /* Where an object stands and how tall it is, from the view it was drawn in. `view`: {world, cam, aspect,
     * crop}; `box`: where the object is inside its picture (fractions). Its feet are put on the ground the
     * view shows there, and the sheet is as large as the picture's frame was at that distance. */
    E.placeObject = (view, box) => {
        const crop = view.crop || { x0: 0, y0: 0, x1: 1, y1: 1 }, cam = view.cam, o = cam.pos;
        const sx = u => crop.x0 + u * (crop.x1 - crop.x0), sy = v => crop.y0 + v * (crop.y1 - crop.y0);
        const down = Core.rayThrough(cam, view.aspect, sx((box[0] + box[2]) / 2), sy(Math.min(1, box[3])));
        let ground;
        if (down[1] > -0.14) {
            // Its lowest point is not on the ground the view shows: it floats (a fish in the air, a lamp, a
            // bird). It is put a couple of metres in front of the camera, not on the far wall behind it.
            const mid = Core.rayThrough(cam, view.aspect, sx((box[0] + box[2]) / 2), sy((box[1] + box[3]) / 2));
            const wall = E.surfaceHit(o, mid, view.world);
            const near = Math.min(2.4, 0.55 * Math.hypot(wall[0] - o[0], wall[1] - o[1], wall[2] - o[2]));
            const low = [0, 1, 2].map(i => o[i] + down[i] * near / Math.max(0.2, mid[0] * down[0] + mid[1] * down[1] + mid[2] * down[2]));
            ground = low;
        } else {
            const feet = E.surfaceHit(o, down, view.world);
            ground = [0, 1, 2].map(i => o[i] + (feet[i] - o[i]) * 0.95);        // a little in front of the surface, not in it
        }
        let n = [o[0] - ground[0], 0, o[2] - ground[2]];
        const length = Math.hypot(n[0], n[2]);
        if (length < 1e-3) { const f = Core.dirFromAngles(cam.yaw, 0); n = [-f[0], 0, -f[2]]; } else n = [n[0] / length, 0, n[2] / length];
        const onSheet = d => {
            const along = d[0] * n[0] + d[2] * n[2];
            if (Math.abs(along) < 1e-4) return null;
            const t = ((ground[0] - o[0]) * n[0] + (ground[2] - o[2]) * n[2]) / along;
            return t > 0 ? [o[0] + d[0] * t, o[1] + d[1] * t, o[2] + d[2] * t] : null;
        };
        const bottom = onSheet(Core.rayThrough(cam, view.aspect, sx(0.5), sy(1))), top = onSheet(Core.rayThrough(cam, view.aspect, sx(0.5), sy(0)));
        if (!bottom || !top) return { pos: ground.map(v => +v.toFixed(3)), size: 1.7 };
        const size = Core.clamp(Math.hypot(top[0] - bottom[0], top[1] - bottom[1], top[2] - bottom[2]), 0.05, 60);
        return { pos: bottom.map(v => +v.toFixed(3)), size: +size.toFixed(3) };
    };

    /* the object under a point of the view (fractions from the top left), the nearest one first */
    E.pickObject = (px, py) => {
        const world = E.world();
        if (!world || !(world.objects || []).length) return null;
        const o = E.cam.pos, d = Core.rayThrough(E.cam, E.aspect(), px, py);
        let best = null;
        for (const object of world.objects) {
            const have = E.objectsLoaded.get(object.id);
            if (!have) continue;
            const s = E.objectSheet(object, o, E.objectNow(object)), n = s.normal;
            if (have.model && !object.flat) {
                const t = solidHit(E.solidState(object, have.model, s, E.worldId, E.clock, E.play ? E.play.t : 0, o), have.model, o, d);
                if (t != null && (!best || t < best.t)) best = { object, t };
                continue;
            }
            const along = d[0] * n[0] + d[2] * n[2];
            if (Math.abs(along) < 1e-5) continue;
            const t = ((s.anchor[0] - o[0]) * n[0] + (s.anchor[2] - o[2]) * n[2]) / along;
            if (t <= 0 || (best && t >= best.t)) continue;
            const p = [o[0] + d[0] * t - s.anchor[0], o[1] + d[1] * t - s.anchor[1], o[2] + d[2] * t - s.anchor[2]];
            let u = (p[0] * s.right[0] + p[2] * s.right[2]) / s.width + 0.5;
            const v = 1 - p[1] / s.height;
            if (u < 0 || u > 1 || v < 0 || v > 1) continue;
            if (object.flip) u = 1 - u;
            const a = have.alpha[Math.min(have.ah - 1, Math.floor(v * have.ah)) * have.aw + Math.min(have.aw - 1, Math.floor(u * have.aw))];
            if (a > 50) best = { object, t };
        }
        return best ? best.object : null;
    };

    /* the point where an object touches the ground: under the middle of what is seen of it */
    E.objectFeet = object => {
        const box = object.box || [0, 0, 1, 1];
        // (where it stands, not where it has walked to: this is the point it is moved by)
        const now = object.on_ground ? E.objectPlace(Object.assign({}, object, { walk: null, path: null, shy: 0 }), 0, null, E.worldId) : { pos: object.pos, size: +object.size || 1.7 };
        return [now.pos[0], now.pos[1] + (1 - Math.min(1, box[3])) * now.size, now.pos[2]];
    };

    /* Puts an object where a point of the view shows the ground. */
    E.moveObjectTo = (object, px, py) => {
        const hit = E.surfaceHit(E.cam.pos, Core.rayThrough(E.cam, E.aspect(), px, py), E.worldId);
        if (object.on_ground && E.field().n) {
            // one that stands on the ground goes to that spot of the scene's map
            const at = Core.mapped(hit, E.eye(E.worldId));
            object.pos = [+at.x.toFixed(3), +(+object.pos[1] || 0).toFixed(3), +at.z.toFixed(3)];
        } else {
            const o = E.cam.pos, at = [0, 1, 2].map(i => o[i] + (hit[i] - o[i]) * 0.95);
            const lift = E.objectFeet(object)[1] - object.pos[1];
            object.pos = [+at[0].toFixed(3), +(at[1] - lift).toFixed(3), +at[2].toFixed(3)];
        }
        E.touch();
    };

    E.object = id => { const world = E.world(); return world && (world.objects || []).find(o => o.id === (id || E.chosen)) || null; };
    E.choose = id => { if (E.chosen !== (id || null)) { E.chosen = id || null; E.emit('objects'); } };

    E.removeObject = async object => {
        const world = E.world();
        world.objects = (world.objects || []).filter(o => o !== object);
        if (E.chosen === object.id) E.chosen = null;
        for (const line of E.lines()) if (line.object === object.id) { line.object = null; line.file = null; }
        E.touch('objects');
        await E.ensure(world.id);
    };

    E.copyObject = async object => {
        const world = E.world(), copy = JSON.parse(JSON.stringify(object));
        copy.id = uid('o'); copy.name = (object.name || '') + ' 2';
        const side = E.objectSheet(object, E.cam.pos).right;
        copy.pos = [object.pos[0] + side[0] * object.size * 0.6, object.pos[1], object.pos[2] + side[2] * object.size * 0.6].map(v => +v.toFixed(3));
        world.objects.push(copy);
        E.chosen = copy.id;
        E.touch('objects');
        await E.ensure(world.id);
        return copy;
    };

    /* what a job made becomes the object's picture; the one before can be brought back */
    function adoptObject(object, result) {
        if (object.file) {
            const keep = {};
            for (const k of ['kind', 'file', 'poster', 'depth', 'width', 'height', 'box', 'frames', 'fps', 'seed', 'infotext', 'model', 'model_facts']) keep[k] = object[k];
            object.history = (object.history || []).concat([keep]).slice(-8);
        }
        // (a picture that is drawn again is no longer the picture its model was made of: the model goes with the old picture)
        for (const k of ['kind', 'file', 'poster', 'depth', 'width', 'height', 'box', 'frames', 'fps', 'seed', 'infotext', 'model', 'model_facts']) object[k] = result[k] === undefined ? null : result[k];
    }

    /* ---- solid objects: real 3D models ------------------------------------------------------------------- */

    /* The light of a world, from a small copy of its panorama: the sky's colour and the ground's, how bright
     * the scene is, and where its brightest part is (the sun, a lamp, a window). */
    function lightFrom(image) {
        const d = image.data, w = image.width, h = image.height;
        const sky = [0, 0, 0], ground = [0, 0, 0], sun = [0, 0, 0], sunColour = [0, 0, 0];
        let skyN = 0, groundN = 0, all = 0, allN = 0, peak = 0, sunN = 0;
        for (let y = 0; y < h; y++) {
            const share = Math.cos((0.5 - (y + 0.5) / h) * Math.PI);           // the rows near the poles are small
            for (let x = 0; x < w; x++) {
                const i = (y * w + x) * 4, r = d[i] / 255, g = d[i + 1] / 255, b = d[i + 2] / 255, bright = 0.3 * r + 0.59 * g + 0.11 * b;
                all += bright * share; allN += share; peak = Math.max(peak, bright);
                const to = y < h / 2 ? sky : ground;
                to[0] += r * share; to[1] += g * share; to[2] += b * share;
                if (y < h / 2) skyN += share; else groundN += share;
                if (y < h * 0.6) {
                    const pull = Math.pow(bright, 6) * share, dir = Core.dirFromUv((x + 0.5) / w, (y + 0.5) / h);
                    for (let k = 0; k < 3; k++) sun[k] += dir[k] * pull;
                    sunColour[0] += r * pull; sunColour[1] += g * pull; sunColour[2] += b * pull; sunN += pull;
                }
            }
        }
        // a dark scene dims what stands in it, but only so far: the model's own colours are already without light
        const mean = all / Math.max(allN, 1e-6), exposure = Core.clamp(0.58 + mean, 0.62, 1.2);
        // a colour's cast, without its brightness, and only most of the way: a red sky does not make a red man
        const cast = (c, n) => { const v = c.map(x => x / Math.max(n, 1e-6)), l = Math.max(0.3 * v[0] + 0.59 * v[1] + 0.11 * v[2], 1e-3); return v.map(x => 1 + (x / l - 1) * 0.6); };
        const length = Math.hypot(sun[0], sun[1], sun[2]), contrast = Core.clamp((peak - mean) / 0.4, 0, 1);
        const dir = length > 1e-9 ? [sun[0] / length, Math.max(0.25, sun[1] / length), sun[2] / length] : [0.35, 0.8, 0.48];
        const dl = Math.hypot(dir[0], dir[1], dir[2]);
        return {
            sky: cast(sky, skyN).map(v => v * exposure * 1.12), ground: cast(ground, groundN).map(v => v * exposure * 0.74),
            sun: dir.map(v => v / dl), sunColour: (sunN > 0 ? cast(sunColour, sunN) : [1, 1, 1]).map(v => v * exposure * 0.4 * contrast),
        };
    }
    E.lightFrom = lightFrom;

    /* a model's bytes onto the graphics card, under an object's id; returns the model as it was read */
    async function loadModel(id, buffer) {
        const model = Solid.read(buffer);
        const pictures = await Promise.all(model.pictures.map(p => (p ? createImageBitmap(new Blob([p.bytes], { type: p.type }), { premultiplyAlpha: 'none', colorSpaceConversion: 'none' }).catch(() => null) : null)));
        E.gl.putModel(id, model, pictures);
        for (const picture of pictures) if (picture) picture.close();
        model.pictures = null;                  // they are on the graphics card now
        return model;
    }

    /* What a solid object's model is kept under: its file and the kind of body it is given. Objects that
     * are copies of each other (a crowd of one passer-by) share one model on the graphics card. */
    function solidKey(world, object) {
        if (!object.flat && object.shape) return `shape/${object.shape}`;          // a plain shape is the same model wherever it stands
        if (object.flat || !object.model) return null;
        const body = object.body && object.body.kind ? object.body.kind + (object.body.back ? '-back' : '') : '';
        return `${world.id}/${object.model}/${body}`;
    }

    async function acquireSolid(key, world, object) {
        let kept = E.solids.get(key);
        if (!kept) {
            kept = { users: 0, loading: (async () => {
                if (object.shape) { const made = Solid.shape(object.shape); E.gl.putModel(key, made, []); return made; }
                const model = await loadModel(key, await (await fetch(E.fileUrl(world, object.model))).arrayBuffer());
                const kind = object.body && object.body.kind;
                if (!model.bones && kind && Solid.BONES[kind]) {
                    const shape = Solid.guess(model, kind);
                    if (object.body.back) shape.front = -shape.front;
                    Solid.bind(model, shape);
                    E.gl.setBones(key, model.bone, model.weight);
                }
                return model;
            })() };
            E.solids.set(key, kept);
        }
        kept.users++;
        try { return await kept.loading; } catch (e) { kept.users--; if (kept.users <= 0) E.solids.delete(key); throw e; }
    }

    function releaseSolid(key) {
        const kept = key && E.solids.get(key);
        if (!kept || --kept.users > 0) return;
        E.solids.delete(key);
        E.gl.dropModel(key);
    }

    /* '#rrggbb' as the four numbers the graphics card takes */
    const colourOf = text => { const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(String(text)); return m ? [parseInt(m[1], 16) / 255, parseInt(m[2], 16) / 255, parseInt(m[3], 16) / 255, 1] : [1, 1, 1, 1]; };

    /* A plain shape (a cube, a ball, a cone...) becomes a solid object, standing in front of the camera. Its
     * picture (what it is listed with) is drawn here, like an uploaded model's. */
    E.addShape = async (kind, colour) => {
        const world = E.world(), id = uid('o'), model = Solid.shape(kind);
        colour = colour || '#8f95ff';
        let pixels;
        E.gl.putModel(id, model, []);
        try { pixels = E.gl.portrait(id, model.low, model.high, 512, 512, 28, 0.9, colourOf(colour)); } finally { E.gl.dropModel(id); }
        const started = await call('/upload', { raw: await toBlob(canvasFromPixels(pixels, 512, 512), 'image/png'), query: { project: E.name, world: world.id, filename: kind + '.png', object: id } });
        const result = await E.job('import_object', { world: world.id }, null, started);
        const object = await standNew(world, id, kind, result, { shape: kind, colour, body: { kind: '' }, face_on: false });
        return object;
    };

    /* "Draw a material on it": Forge draws a picture that repeats without a seam, and the object wears it. */
    E.skinObject = async (object, settings) => {
        const world = E.objectWorld(object.id), s = Object.assign({}, settings);
        let prompt = (s.prompt || '').trim();
        if (!prompt) throw new Error('Write what it is made of first');
        if (s.lcm && E.options.lora_lcm && !/<lora:LCM/i.test(prompt)) prompt += ` <lora:${E.options.lora_lcm}:1>`;
        const result = await E.job('skin', { project: E.name, world: world.id, object: object.id, settings: { ...s, prompt, checkpoint: s.checkpoint || (world.gen && world.gen.checkpoint) || null } });
        object.skin = result.skin; object.skin_words = (s.prompt || '').trim();
        if (object.skin_repeat == null) object.skin_repeat = 1;
        E.touch('objects');
        await E.ensure(world.id);
        return object;
    };

    /* ---- my objects: what was made once is kept for other worlds and other projects -------------------------- */

    E.library = async () => { E.kept = (await call('/library')).objects; return E.kept; };
    E.keepObject = async object => {
        const world = E.objectWorld(object.id);
        await E.save();
        const kept = await call('/library/keep', { body: { project: E.name, world: world.id, object } });
        await E.library();
        E.emit('objects');
        return kept;
    };
    E.useKept = async entry => {
        const world = E.world(), id = uid('o');
        const taken = await call('/library/use', { body: { id: entry.id, project: E.name, world: world.id, object: id } });
        const object = await standNew(world, id, taken.name || entry.name, taken, taken);
        return object;
    };
    E.dropKept = async entry => { await call('/library/drop', { body: { id: entry.id } }); await E.library(); E.emit('objects'); };

    /* a solid object's model as it is loaded, or null (a flat object, or one whose world is not loaded) */
    E.modelOf = object => { const have = object && E.objectsLoaded.get(object.id); return have && have.model && !object.flat ? have.model : null; };

    /* What kind of body a model is taken to have: 'own' when it brought its bones with it, the kind it was
     * given (two legs, wings, four legs, fish), or '' for a thing that only stands. */
    E.bodyOf = object => {
        const model = E.modelOf(object);
        if (model && model.bones) return 'own';
        return (object.body && object.body.kind) || '';
    };

    /* A model that brought no bones is given them, for the kind of body its object is said to have (when the
     * kind is changed, the object's model is loaded again as that kind). Returns its bones, or null. */
    E.rigObject = async object => {
        const world = E.objectWorld(object.id);
        if (world) await E.ensure(world.id);
        const model = E.modelOf(object);
        return model && model.rig || null;
    };

    /* what a solid object can do: [[name, shown name]], standing and going */
    E.movesOf = object => {
        const model = E.modelOf(object), body = E.bodyOf(object);
        if (!model) return [];
        if (body === 'own') return model.moves.map(m => m.name);
        return Solid.KINDS[body] || [];
    };

    /* the heading of an object that stands, at a moment of the scene: its `turn`, and what its `turns` have made of it by then */
    function turnAt(object, time) {
        let yaw = +object.turn || 0;
        for (const k of object.turns) {
            if (!(time >= +k.t)) break;
            yaw += Core.turn(yaw, +k.turn) * Core.smooth((time - k.t) / Math.max(0.05, k.over == null ? 0.9 : +k.over));
        }
        return yaw;
    }

    /* Where a point of a resting model is in its object's picture: u = m[0] + m[1] * x, v = m[2] + m[3] * y
     * (the model was made of the figure in the picture, and fills the figure's box). */
    function pictureOn(object, model) {
        const box = object.box || [0, 0, 1, 1], bottom = Math.min(1, box[3]), own = Math.max(1e-6, model.high[1] - model.low[1]);
        const wide = Math.max(1e-6, model.high[0] - model.low[0]), bx = (box[2] - box[0]) / wide, by = (bottom - box[1]) / own;
        return model.rig && model.rig.front < 0 ? [box[2] + model.low[0] * bx, -bx, bottom + model.low[1] * by, -by] : [box[0] - model.low[0] * bx, bx, bottom + model.low[1] * by, -by];
    }

    /* Where the head of a solid figure is in its picture: a square of the picture's pixels around the head, with
     * room for the neck and the tops of the shoulders ([x0, y0, x1, y1] as shares of the picture; it may reach
     * past the picture's edge). A line's video is made of this close-up and laid on the model's head: the lips
     * of a whole figure are a few pixels wide, and no lip syncer can say anything with them. */
    E.headCrop = object => {
        const model = E.modelOf(object), rig = model && model.rig;
        if (!rig || rig.kind !== 'biped' || rig.front < 0 || !rig.joints.neck || !rig.joints.head || !(object.width > 0 && object.height > 0)) return null;
        const map = pictureOn(object, model), W = object.width, H = object.height;
        const top = (map[2] + map[3] * rig.joints.head[1]) * H, neck = (map[2] + map[3] * rig.joints.neck[1]) * H, tall = neck - top;
        if (!(tall > 4)) return null;
        const middle = (map[0] + map[1] * rig.joints.head[0]) * W, side = tall * 2.1, y0 = top - tall * 0.5;
        return [(middle - side / 2) / W, y0 / H, (middle + side / 2) / W, (y0 + side) / H].map(v => +v.toFixed(4));
    };

    /* ---- real steps: a walk made by AI4Animation's neural controller for this object's path ---------------- */

    E.motions = new Map();          // object id -> {file, clip (what the job wrote), laid: WeakMap rig -> retargeted, clock}

    async function loadMotion(world, object) {
        const have = E.motions.get(object.id);
        if (have && have.file === object.motion.file) return have;
        try {
            const clip = await (await fetch(E.fileUrl(world, object.motion.file))).json();
            const motion = { file: object.motion.file, clip, laid: new WeakMap(), clock: null };
            E.motions.set(object.id, motion);
            return motion;
        } catch (e) { console.warn('[Simulation 360] a walk could not be loaded', object.id, e); return null; }
    }

    /* how tall a figure stands in the scene, in metres (its picture's figure, at its size) */
    const figureHeight = object => { const box = object.box || [0, 0, 1, 1]; return Math.max(0.2, (+object.size || 1.7) * (Math.min(1, box[3]) - box[1])); };
    const pathSign = object => JSON.stringify((object.path || []).map(k => [k.t, k.pos[0], k.pos[2]])) + ':' + (+object.size || 0);

    /* The clip's own time for a moment of the scene. The network walks at its own pace; the figure goes as fast
     * as its path says. While both move, the clip runs at the ratio of the two speeds, so each step covers the
     * ground the figure covers (no feet sliding); while it stands the clip runs at its own time. */
    function motionClock(object, motion) {
        const clip = motion.clip, legs = clipScale(object, clip, true), deep = deepWorld(object);
        const sign = pathSign(object) + ':' + (legs ? legs.toFixed(4) : '');
        if (motion.clock && motion.clock.sign === sign && motion.clock.field === (deep && deep.field)) return motion.clock;
        // (the clip's metres for one of the figure's: by the legs when they can be measured)
        const fps = clip.fps || 30, dt = 1 / fps, n = clip.frames.length, k = legs ? 1 / legs : clip.scale || 1, root = clip.root || [];
        const netSpeed = i => { const a = root[Math.max(0, Math.min(n - 2, i))], b = root[Math.max(1, Math.min(n - 1, i + 1))]; return a && b ? Math.hypot(b[0] - a[0], b[1] - a[1]) / dt : 0; };
        const steps = n + Math.ceil(fps * 2), clock = new Float32Array(steps);
        let tau = 0, rate = 1;
        for (let i = 0; i < steps; i++) {
            clock[i] = tau;
            const t = clip.start + i * dt, ours = ownMetres(object, t, dt, deep && deep.id) / dt * k, theirs = netSpeed(Math.round(tau * fps));
            const want = ours > 0.25 && theirs > 0.25 ? Core.clamp(ours / theirs, 0.7, 1.45) : 1;
            rate += (want - rate) * 0.25;
            tau += rate * dt;
        }
        motion.clock = { sign, field: deep && deep.field, fps, start: clip.start, table: clock, at(t) { const x = (t - this.start) * this.fps; if (x < 0 || x > this.table.length - 1) return null; const i = Math.floor(x), u = x - i; return this.table[i] * (1 - u) + this.table[Math.min(this.table.length - 1, i + 1)] * u; } };
        return motion.clock;
    }

    /* ---- feet that stay where they step --------------------------------------------------------------------- */

    /* ---- feet that stay where they step ------------------------------------------------------------------- */

    /* The touch of the ground that holds a foot at moment t, and how much. Wholly while the foot is down; in
     * the air, toward the touch it is coming down for or away from the one it lifted from, over `ease`
     * seconds at most (half the time in the air when that is shorter): whatever the leg was held back by is
     * given back while the foot is in the air, where nobody sees it slide. {span: [from, to], w} or null. */
    function footHold(clock, contacts, fps, bit, t, ease) {
        const down = time => { const tau = clock.at(time); if (tau == null) return false; const i = Math.round(tau * fps); return i >= 0 && i < contacts.length && !!(contacts[i] & bit); };
        const step = 1 / 60;
        const spanAt = time => {
            let a = time, b = time;
            for (let i = 0; i < 240 && down(a - step); i++) a -= step;
            for (let i = 0; i < 240 && down(b + step); i++) b += step;
            return [a, b];
        };
        if (down(t)) return { span: spanAt(t), w: 1 };
        let lifted = null, touches = null;
        for (let i = 1; i * step <= ease + 1e-9 && (lifted == null || touches == null); i++) {
            if (lifted == null && down(t - i * step)) lifted = t - i * step;
            if (touches == null && down(t + i * step)) touches = t + i * step;
        }
        if (lifted == null && touches == null) return null;
        const reach = lifted != null && touches != null ? Math.min(ease, (touches - lifted) / 2) : ease;
        const gone = lifted != null ? t - lifted : Infinity, coming = touches != null ? touches - t : Infinity, d = Math.min(gone, coming);
        if (d >= reach) return null;
        return { span: spanAt(coming <= gone ? touches : lifted), w: Core.smooth(1 - d / reach) };
    }

    /* A foot that is on the ground stays where it stepped. Between a clip's steps and the figure's ground (a
     * picture's depth, legs of another length) a planted foot still drifts a little; while a foot is down, the
     * leg is bent so that the ankle stays where it was in the middle of that touch (footHold: in the air it
     * comes to that place and leaves it). Worked out from the time alone (the middle of the touch is found
     * again frame by frame), so a recording drawn twice is the same. `weight`: how much the real steps hold
     * now. A foot that slides more than a tenth of the figure's height or so during a touch is not drifting
     * but sliding on purpose (a leg that drags): that touch is let go (from 9% to 15% of its height). */
    function pinFeet(object, model, steps, bones, matrix, scene, weight) {
        const rig = model.rig, motion = E.motions.get(object.id);
        if (!motion || !motion.clip || !(weight > 1e-3)) return;
        const contacts = motion.contacts || (motion.contacts = Solid.footContacts(motion.clip)), fps = motion.clip.fps || 30;
        const own = model.high[1] - model.low[1];
        let inverse = null;
        for (const [side, bit] of [['l', 1], ['r', 2]]) {
            const hip = rig.names.indexOf('hip_' + side), knee = rig.names.indexOf('knee_' + side);
            if (hip < 0 || knee < 0) continue;
            const hold = footHold(steps.clock, contacts, fps, bit, scene.sceneTime, 0.25);
            if (!hold) continue;
            const w = weight * hold.w;
            if (w < 1e-3) continue;
            // where the ankle is, unheld, at the middle of this touch (where it is held) and at its two ends
            const free = time => {
                const placed = E.objectPlace(object, time, null, scene.worldId), sheet = E.objectSheet(object, scene.from, placed);
                sheet.small = placed.small;
                const then = E.solidState(object, model, sheet, scene.worldId, scene.walkTime + time - scene.sceneTime, time, scene.from, true);
                return then.bones ? { at: Solid.moved(then.model, Solid.moved(then.bones.subarray(knee * 16, knee * 16 + 16), rig.ends[knee])), tall: own * then.scale } : null;
            };
            const planted = free((hold.span[0] + hold.span[1]) / 2), first = free(hold.span[0]), last = free(hold.span[1]);
            if (!planted || !first || !last) continue;
            // how far the foot slides in the clip itself during this touch, against the figure's height
            const slide = Math.max(Math.hypot(...[0, 1, 2].map(j => first.at[j] - planted.at[j])), Math.hypot(...[0, 1, 2].map(j => last.at[j] - planted.at[j]))) / Math.max(1e-6, planted.tall);
            const k = w * (1 - Core.smooth(Core.clamp((slide - 0.09) / 0.06, 0, 1)));
            if (k < 1e-3) continue;
            inverse = inverse || Solid.affineInverse(matrix);
            const want = Solid.moved(inverse, planted.at), F = Solid.moved(bones.subarray(knee * 16, knee * 16 + 16), rig.ends[knee]);
            Solid.reach(rig, bones, hip, knee, [0, 1, 2].map(j => F[j] + (want[j] - F[j]) * k));
        }
    }

    /* the walk of an object, laid onto its model's bones (null while it has none, or for a body it does not fit) */
    function walkOf(object, model) {
        const motion = object.motion && object.motion.on !== false ? E.motions.get(object.id) : null, rig = model && model.rig;
        if (!motion || motion.file !== object.motion.file || !rig || (rig.kind !== 'biped' && rig.kind !== 'four') || (motion.clip.kind || 'biped') !== rig.kind) return null;
        // (where the nose of a model on four legs is: its picture shows it from the side, `aim` says which way)
        const a = (+object.aim || 0) * Core.RAD, nose = [Math.sin(a), 0, Math.cos(a)], key = rig.kind === 'four' ? rig : rig;
        if (!motion.laid.has(key) || motion.nose !== +object.aim) {
            motion.nose = +object.aim;
            try { motion.laid.set(key, Solid.retarget(rig, motion.clip, nose)); } catch (e) { console.warn('[Simulation 360] the walk does not fit', object.id, e); motion.laid.set(key, null); }
        }
        const laid = motion.laid.get(rig);
        return laid ? { laid, clock: object.motion.source === 'mocap' ? mocapClock(object, motion) : motionClock(object, motion) } : null;
    }

    /* The clip time of a motion capture at a moment of the scene: from `at` (seconds of the scene), at `speed`,
     * round and round when it `loop`s, otherwise its last frame held. Before `at`, and from `until` on (when it is
     * later than `at`): none (the formulas: it stands, or does its act; a conductor stops to talk). */
    function mocapClock(object, motion) {
        const m = object.motion, clip = motion.clip, length = (clip.frames.length - 1) / (clip.fps || 30);
        return { at(t) {
            const x = (t - (+m.at || 0)) * (m.speed == null ? 1 : +m.speed);
            if (x < 0 || (+m.until > (+m.at || 0) && t >= +m.until)) return null;
            return m.loop ? x % Math.max(1e-3, length) : Math.min(x, length);
        } };
    }

    /* A motion capture file (.bvh, .glb, .npz) becomes what a figure on two legs does: AI4AnimationPy reads it, any
     * skeleton's joints are matched to ours by their names, and it is laid onto the figure's bones. It plays from
     * the second `at` of the scene, in place (turning as it turns), or going where it goes (`travel`). */
    E.mocapObject = async (object, file, options) => {
        options = options || {};
        const world = E.objectWorld(object.id);
        const kept = await call('/upload-motion', { raw: await file.arrayBuffer(), query: { project: E.name, world: world.id, object: object.id, filename: file.name || 'motion.bvh' } });
        const result = await E.job('mocap', { project: E.name, world: world.id, object: object.id, file: kept.file });
        if (object.motion && object.motion.travel) E.mocapTravel(object, false);       // (its own path back first)
        object.motion = { ...ownTurning(object.motion), file: result.file, source: 'mocap', name: (file.name || '').replace(/\.[^.]+$/, '').slice(0, 60), from_file: kept.file, seconds: result.seconds, skipped: result.skipped || 0,
            on: true, at: options.at == null ? 0 : +options.at, speed: 1, loop: options.loop !== false, travel: false };
        E.touch('objects');
        await loadMotion(world, object);
        return object.motion;
    };

    /* How many of the figure's metres a metre of a clip is. By the legs: a step is as long as the legs that take
     * it, and a figure's legs are seldom the same share of its height as the captured body's (the Lady's are
     * 0.40 of hers, the capture's 0.48: going by heights her steps were a sixth shorter than the ground she
     * covered). By the heights when there are no legs to measure. */
    function clipScale(object, clip, strict) {
        const have = E.objectsLoaded.get(object.id), model = have && have.model, J = model && model.rig && model.rig.joints;
        const byHeight = strict ? null : figureHeight(object) / Math.max(0.3, clip.height || 1.7), names = clip.names, first = clip.frames && clip.frames[0];
        if (!J || !J.hip_l || !J.knee_l || !J.foot_l || !J.hip_r || !J.knee_r || !J.foot_r || !names || !first) return byHeight;
        const d = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]), at = name => first[names.indexOf(name)];
        if (['LeftUpLeg', 'LeftLeg', 'LeftFoot', 'RightUpLeg', 'RightLeg', 'RightFoot'].some(n => names.indexOf(n) < 0)) return byHeight;
        const ours = (d(J.hip_l, J.knee_l) + d(J.knee_l, J.foot_l) + d(J.hip_r, J.knee_r) + d(J.knee_r, J.foot_r)) / 2 / Math.max(1e-6, model.high[1] - model.low[1]) * figureHeight(object);
        const theirs = (d(at('LeftUpLeg'), at('LeftLeg')) + d(at('LeftLeg'), at('LeftFoot')) + d(at('RightUpLeg'), at('RightLeg')) + d(at('RightLeg'), at('RightFoot'))) / 2;
        const k = ours / Math.max(0.05, theirs), height = figureHeight(object) / Math.max(0.3, clip.height || 1.7);
        return k > height / 2 && k < height * 2 ? k : byHeight;
    }

    /* How far a figure that stands on the ground goes along its path between t and t + dt, in its own metres. In
     * a world with depth a metre of the map is seldom a metre there (Core.stood: toward or away from where the
     * picture was taken the depth is squeezed or stretched), so it is measured in the world against how tall the
     * figure is drawn at that spot; between a third and three times the map's metres, so a cliff in the depth
     * does not throw it. `deep`: the world's id, or null for the map's metres. */
    function ownMetres(object, t, dt, deep) {
        const a = E.objectPlace({ pos: object.pos, path: object.path }, t, null).pos, b = E.objectPlace({ pos: object.pos, path: object.path }, t + dt, null).pos;
        const plain = Math.hypot(b[0] - a[0], b[2] - a[2]);
        if (!deep || plain < 1e-9) return plain;
        const stub = { pos: object.pos, path: object.path, on_ground: true }, A = E.objectPlace(stub, t, null, deep), B = E.objectPlace(stub, t + dt, null, deep);
        return Core.clamp(Math.hypot(B.pos[0] - A.pos[0], B.pos[2] - A.pos[2]) / Math.max(1e-6, A.small), plain / 3, plain * 3);
    }
    const deepWorld = object => { const world = object.on_ground ? E.objectWorld(object.id) : null, field = world ? E.field(world.id) : null; return field && field.n ? { id: world.id, field } : null; };

    /* Where a motion capture that goes where it goes has carried its object at moment t ([x, z] on the map), read
     * from the clip's hips frame by frame. (Its path, drawn from the same hips, is for the map and for when the clip
     * is not loaded: a path eases into and out of every key, which on a key every few frames is a stutter.) */
    function mocapCarried(object, t, worldId) {
        const m = object.motion;
        if (!m || m.source !== 'mocap' || !m.travel || m.on === false || !m.base) return null;
        const motion = E.motions.get(object.id), clip = motion && motion.clip, root = clip && clip.root;
        if (!root || root.length < 2) return null;
        const x = (t - (+m.at || 0)) * (m.speed == null ? 1 : +m.speed);
        if (x < 0) return null;
        const fps = clip.fps || 30, track = mocapTrack(object, motion, worldId || (E.objectWorld(object.id) || {}).id);
        if (track) {
            const i = Math.min(track.points.length - 1, x * fps / track.every), lo = Math.floor(i), hi = Math.min(track.points.length - 1, lo + 1), u = i - lo;
            const a = track.points[lo], b = track.points[hi];
            return [a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u];
        }
        const i = Math.min(root.length - 1, x * fps), lo = Math.floor(i), hi = Math.min(root.length - 1, lo + 1), u = i - lo;
        const rx = root[lo][0] + (root[hi][0] - root[lo][0]) * u, rz = root[lo][1] + (root[hi][1] - root[lo][1]) * u;
        const k = clipScale(object, clip), f = Core.dirFromAngles(object.turn == null ? +m.heading || 0 : +object.turn, 0), left = [f[2], 0, -f[0]];
        return [m.base[0] + (rx * left[0] + rz * f[0]) * k, m.base[2] + (rx * left[2] + rz * f[2]) * k];
    }

    /* Its way in a world with depth, in the figure's own metres. A picture's depth is not the place's depth
     * (Core.stood): toward or away from where the picture was taken, a metre of the map can be a longer or a
     * shorter step in the world than the figure's size there says, and its feet would slide. So each step of
     * the clip's hips is taken in the world, as long as the figure is tall at that spot, and the step on the map
     * that makes it is found from how the ground stretches there (between a third and three times the plain
     * step, so a cliff in the depth neither holds it nor throws it). [x, z] on the map every `every` frames. */
    function mocapTrack(object, motion, worldId) {
        const m = object.motion, clip = motion.clip, root = clip.root;
        const field = object.on_ground && worldId ? E.field(worldId) : null;
        if (!field || !field.n) return null;
        const turn = object.turn == null ? +m.heading || 0 : +object.turn, k = clipScale(object, clip);
        const sign = [worldId, m.base.join(), turn, k.toFixed(4), E.eye(worldId), root.length].join('|');
        if (motion.track && motion.track.sign === sign && motion.track.field === field) return motion.track;
        const f = Core.dirFromAngles(turn, 0), left = [f[2], 0, -f[0]], every = 3, h = 0.2, points = [];
        const at = (x, z) => { const s = E.stand(worldId, x, z, 0); return s ? s : null; };
        let x = m.base[0], z = m.base[2];
        for (let i = 0; ; i += every) {
            points.push([+x.toFixed(4), +z.toFixed(4)]);
            const j = Math.min(root.length - 1, i + every);
            if (j <= i) break;
            const dx = root[j][0] - root[i][0], dz = root[j][1] - root[i][1];
            const mx = (dx * left[0] + dz * f[0]) * k, mz = (dx * left[2] + dz * f[2]) * k, plain = Math.hypot(mx, mz);
            let sx = mx, sz = mz;
            const here = at(x, z), xa = at(x + h, z), xb = at(x - h, z), za = at(x, z + h), zb = at(x, z - h);
            if (plain > 1e-6 && here && xa && xb && za && zb) {
                // how the world moves for a step on the map: [[a, b], [c, d]]; the world's step wanted: the
                // figure's metres there (it is drawn `small` times its size)
                const a = (xa.pos[0] - xb.pos[0]) / (2 * h), b = (za.pos[0] - zb.pos[0]) / (2 * h);
                const c = (xa.pos[2] - xb.pos[2]) / (2 * h), d = (za.pos[2] - zb.pos[2]) / (2 * h), det = a * d - b * c;
                if (Math.abs(det) > 1e-8) {
                    const wx = mx * here.small, wz = mz * here.small, ux = (d * wx - b * wz) / det, uz = (a * wz - c * wx) / det;
                    const n = Math.hypot(ux, uz), s = n > 1e-9 ? Core.clamp(n, plain / 3, plain * 3) / n : 1;
                    sx = ux * s; sz = uz * s;
                }
            }
            x += sx; z += sz;
        }
        motion.track = { sign, field, points, every };
        return motion.track;
    }

    /* A motion capture that goes somewhere: its hips' way over the ground becomes the object's path, turned to the
     * object's heading and sized to its height. Its own turning is in the clip, so the object keeps its heading. */
    E.mocapTravel = (object, on) => {
        const motion = E.motions.get(object.id), m = object.motion;
        if (!m || m.source !== 'mocap') return;
        if (!on) {
            if (m.before) { object.path = m.before.path; object.face = m.before.face; }
            m.travel = false; delete m.before; delete m.base; delete m.heading; E.touch('objects'); return;
        }
        if (!motion) return;
        const clip = motion.clip, fps = clip.fps || 30, k = clipScale(object, clip), speed = m.speed == null ? 1 : +m.speed;
        const heading = object.turn == null ? 0 : +object.turn, f = Core.dirFromAngles(heading, 0), left = [f[2], 0, -f[0]];
        const from = m.before ? m.before.path : object.path, base = from && from.length ? from[0].pos : object.pos, path = [];
        // (what the old path said before the motion begins stays, and how seen it was at each moment comes along)
        for (const key of from || []) if (key.t < (+m.at || 0)) path.push(key);
        const seen = t => (from && from.length ? E.objectPlace({ pos: object.pos, path: from }, t, null).opacity : 1);
        if (!m.before) m.before = { path: object.path || null, face: object.face || null };
        m.travel = true; m.loop = false; m.base = base.slice(); m.heading = heading; object.turn = heading;
        // (where it really goes: in a world with depth, its own metres there; mocapTrack)
        const used = m.on; m.on = true;
        for (let i = 0; i < clip.root.length; i += Math.max(1, Math.round(fps / 6))) {
            const r = clip.root[i], t = +((+m.at || 0) + i / fps / speed).toFixed(3);
            const c = mocapCarried(object, t) || [base[0] + (r[0] * left[0] + r[1] * f[0]) * k, base[2] + (r[0] * left[2] + r[1] * f[2]) * k];
            path.push({ t, pos: [+c[0].toFixed(3), base[1] || 0, +c[1].toFixed(3)], opacity: +seen(t).toFixed(3) });
        }
        m.on = used;
        object.path = path; object.face = 'fixed';
        E.touch('objects');
    };

    /* `motion` also holds how a solid object turns, rises and sways (spin, hover, sway, every: its card's
     * settings). Steps or a capture put in it keep those. */
    const ownTurning = m => { const kept = {}; for (const key of ['spin', 'hover', 'sway', 'every']) if (m && m[key] != null) kept[key] = m[key]; return kept; };
    const hasSteps = object => !!(object.motion && object.motion.file && object.motion.source !== 'mocap');

    /* AI4Animation walks the object along its path (job "motion"): real steps, starts, stops and turns, in a style */
    E.walkObject = async (object, style) => {
        if (object.motion && object.motion.travel) E.mocapTravel(object, false);       // (a capture's way was its path: its own path back)
        if (!object.path || object.path.length < 2) throw new Error('Give it a path first: the network walks it along its path');
        const kind = E.bodyOf(object) === 'four' ? 'four' : 'biped', heights = (E.options.motion && E.options.motion.body_heights) || { biped: 1.75, four: 0.75 };
        const world = E.objectWorld(object.id), scale = heights[kind] / figureHeight(object);
        const first = object.path[0].t, last = object.path[object.path.length - 1].t, path = [], deep = deepWorld(object);
        // the path's shape, with each step as long as it is in the world, in the figure's own metres: so the
        // network takes the steps the figure really takes (toward the camera a picture's depth is squeezed: a
        // walk of seven metres on the map may be two in the world, and long steps there would slide)
        let at = E.objectPlace({ pos: object.pos, path: object.path }, first, null).pos, x = at[0], z = at[2];
        for (let t = first; t <= last + 1e-6; t += 0.1) {
            if (t > first) {
                const p = E.objectPlace({ pos: object.pos, path: object.path }, t, null).pos, plain = Math.hypot(p[0] - at[0], p[2] - at[2]);
                const own = plain > 1e-9 ? ownMetres(object, t - 0.1, 0.1, deep && deep.id) / plain : 1;
                x += (p[0] - at[0]) * own; z += (p[2] - at[2]) * own; at = p;
            }
            path.push([+t.toFixed(3), +(x * scale).toFixed(4), +(z * scale).toFixed(4)]);
        }
        // what a dog does while it stands, from its acts: sit, lie down, stand up
        const told = object.acts && object.acts.length ? object.acts : [{ t: first, act: object.act || '' }];
        const acts = kind === 'four' ? told.filter(a => ['sit', 'lie', 'stand', 'idle', ''].includes(a.act)).map(a => [+(+a.t).toFixed(3), a.act === 'idle' ? '' : a.act]) : [];
        const result = await E.job('motion', { project: E.name, world: world.id, object: object.id, path, style: kind === 'four' ? '' : style || 'Neutral', kind, scale, acts });
        object.motion = { ...ownTurning(object.motion), file: result.file, style: result.style, made_for: pathSign(object), on: true };
        E.touch('objects');
        await loadMotion(world, object);
        return object.motion;
    };
    E.walkStale = object => hasSteps(object) && object.motion.made_for !== pathSign(object);
    E.hasSteps = hasSteps;
    E.walkKinds = ['biped', 'four'];

    /* How a solid object is drawn at a moment: where its model stands (it takes the place of the figure in the
     * object's picture: as tall, on the same feet), which way it faces, how its bones are held, its shadow. */
    E.solidState = (object, model, sheet, worldId, walkTime, sceneTime, from, bare) => {
        const box = object.box || [0, 0, 1, 1], bottom = Math.min(1, box[3]);
        const wider = +object.wide || 1, taller = +object.tall || 1, deeper = +object.deep || 1;
        const tall = Math.max(1e-3, sheet.height / taller * (bottom - box[1])), own = Math.max(1e-6, model.high[1] - model.low[1]), k = tall / own;
        const across = ((box[0] + box[2]) / 2 - 0.5) * sheet.width * (object.flip ? -1 : 1), lift = (1 - bottom) * sheet.height;
        const feet = [0, 1, 2].map(j => sheet.anchor[j] + sheet.right[j] * across + sheet.up[j] * lift);
        // which way it goes, and how far it has gone
        const walk = object.walk && +object.walk.speed ? object.walk : null, path = !walk && object.path && object.path.length > 1 ? object.path : null;
        let heading = null, walked = 0, moving = 0;
        // which way it goes along its path at a moment of the scene, and how much of a walk that is (0..1)
        const wayAt = time => {
            const a = E.objectPlace({ pos: object.pos, path }, time - 0.1, null).pos, b = E.objectPlace({ pos: object.pos, path }, time + 0.1, null).pos;
            const dx = b[0] - a[0], dz = b[2] - a[2], length = Math.hypot(dx, dz);
            return { heading: length > 1e-4 ? [dx / length, dz / length] : null, moving: Core.clamp(length / 0.2 / 0.25, 0, 1) };
        };
        if (walk) {
            const d = Core.dirFromAngles(+walk.yaw || 0, 0), way = Math.sign(walk.speed);
            heading = [d[0] * way, d[2] * way]; walked = Math.abs(walkTime * walk.speed + (+walk.phase || 0)); moving = 1;
        } else if (path) {
            const way = wayAt(sceneTime);
            heading = way.heading; moving = way.moving;
            walked = Core.pathWalked(path, sceneTime);
        }
        // Where it faces while it stands: its heading, which may change at moments of the scene (`turns`,
        // [{t, turn, over}]: from second t it turns to that heading, in `over` seconds).
        const standing = time => {
            if (object.turn == null || !(object.turns || []).length) return [sheet.normal[0], sheet.normal[2]];
            const d = Core.dirFromAngles(turnAt(object, time), 0);
            return [d[0], d[2]];
        };
        const frontAt = time => {
            const way = path && object.face !== 'fixed' ? wayAt(time) : null;
            return way && way.heading && way.moving > 0.01 ? way.heading : standing(time);
        };
        let front = heading && moving > 0.01 && object.face !== 'fixed' ? heading : standing(sceneTime);
        if (path && object.face !== 'fixed') {
            // it turns into its way and out of it (and round a corner of its path) in most of a second
            let x = 0, z = 0;
            for (let i = -3; i <= 3; i++) { const f = frontAt(sceneTime + i * 0.12); x += f[0]; z += f[1]; }
            const length = Math.hypot(x, z);
            if (length > 0.5) front = [x / length, z / length];
        }
        // A model's face is what its picture showed. A car drawn from its side has its nose to the picture's
        // right or left: `aim` says where its nose is, so that it drives nose first and not door first.
        if (+object.aim) { const a = +object.aim * Core.RAD, c = Math.cos(a), s = Math.sin(a); front = [front[0] * c - front[1] * s, front[0] * s + front[1] * c]; }
        // seen through its own eyes it stands a little behind them, as a body stands behind its eyes: looking down
        // through them, its legs and feet step ahead (the camera is above its middle otherwise)
        if (sheet.headless) { feet[0] -= front[0] * 0.1 * tall; feet[2] -= front[1] * 0.1 * tall; }
        const rig = model.rig, body = model.bones ? 'own' : rig ? rig.kind : '';
        const turned = (rig ? rig.front < 0 : !!(object.body && object.body.back)) ? -1 : 1;      // a model made facing away is turned round
        let fx = front[0] * turned, fz = front[1] * turned;
        // things that only stand may still turn on the spot, rise and fall, and sway
        const t = walkTime, m = object.motion || {};
        if (+m.spin) { const a = (+m.spin) * t * Core.RAD, c = Math.cos(a), s = Math.sin(a); [fx, fz] = [fx * c - fz * s, fx * s + fz * c]; }
        const hover = (+m.hover || 0) * tall * Math.sin(t * 2 * Math.PI / (+m.every || 3)), sway = (+m.sway || 0) * Math.sin(t * 2 * Math.PI / ((+m.every || 3) * 1.37)) * Core.RAD;
        let right = [fz, 0, -fx], ahead = [fx, 0, fz];
        const cx = (model.low[0] + model.high[0]) / 2, cz = (model.low[2] + model.high[2]) / 2, mirror = object.flip ? -1 : 1;
        // columns: the model's x (its left), y (up, leaning a little when it sways) and z (its face)
        let up = [right[0] * Math.sin(sway), Math.cos(sway), right[2] * Math.sin(sway)];
        if (+object.roll) { right = turnedAbout(right, ahead, +object.roll * turned); up = turnedAbout(up, ahead, +object.roll * turned); }
        if (+object.tilt) { up = turnedAbout(up, right, +object.tilt * turned); ahead = turnedAbout(ahead, right, +object.tilt * turned); }
        const X = right.map(v => v * k * mirror * wider), Y = up.map(v => v * k * taller), Z = ahead.map(v => v * k * deeper);
        const at = [0, 1, 2].map(j => feet[j] + (j === 1 ? hover : 0) - X[j] * cx - Y[j] * model.low[1] - Z[j] * cz);
        const matrix = new Float32Array([X[0], X[1], X[2], 0, Y[0], Y[1], Y[2], 0, Z[0], Z[1], Z[2], 0, at[0], at[1], at[2], 1]);
        // its bones (what it does while it stands may change with the scene's time: `acts`, [{t, act}])
        let bones = null, stepsHold = 0;
        const act = (object.acts || []).reduce((now, a) => (sceneTime >= +a.t ? a.act : now), object.act);
        const steps = !model.bones && rig ? walkOf(object, model) : null;
        const speed = object.act_speed == null ? 1 : +object.act_speed;
        if (body === 'own') {
            const names = model.moves.map(x => x.name), going = moving > 0.01 && names.indexOf(object.go || '') >= 0 ? names.indexOf(object.go) : -1;
            const standing = names.indexOf(act || '');
            const move = going >= 0 ? going : standing >= 0 ? standing : (act === '' ? -1 : names.length ? 0 : -1);
            bones = Solid.poseOf(model, move, t * speed);
        } else if (rig) {
            const strength = object.gait == null ? 1 : +object.gait, small = sheet.small || 1;
            const kinds = Solid.KINDS[rig.kind], airborne = rig.kind === 'wings' || rig.kind === 'fish';
            if ((moving > 0.01 && strength > 0) || (airborne && act !== '')) {
                const move = kinds.includes(airborne ? act : object.go) ? (airborne ? act : object.go) : kinds[0];
                // steps keep to the ground that is covered; wings and tails keep time
                const phase = airborne ? t * speed * 2 * Math.PI * (rig.kind === 'wings' ? 2.4 : 1.3) : walked / Solid.stride(rig, tall / small, move) * 2 * Math.PI;
                bones = Solid.pose(rig, Solid.hold(rig, move, phase, t, rig.front), airborne ? 1 : Math.min(1.5, strength) * moving);
            } else if (rig.kind === 'biped' && E.said.has(object.id)) bones = Solid.pose(rig, Solid.hold(rig, 'talk', 0, t * speed, rig.front), 1);       // it is saying a line: its hands say it too
            else if (act && kinds.includes(act)) bones = Solid.pose(rig, Solid.hold(rig, act, t * speed * 2 * Math.PI, t * speed, rig.front), 1);
            else bones = Solid.pose(rig, {}, 0);
            if (steps) {
                // Its real steps, while it goes and while it only stands; what it does besides (a line it says, a
                // wave) is the formulas'. Between the two it changes over half a second, not in one frame.
                const own = time => { if (steps.clock.at(time) == null) return 0; if (object.motion.source === 'mocap') return 1; const p = E.objectPlace({ pos: object.pos, path }, time - 0.1, null).pos, q = E.objectPlace({ pos: object.pos, path }, time + 0.1, null).pos;
                    const going = path && Math.hypot(q[0] - p[0], q[2] - p[2]) > 0.02, doing = (object.acts || []).reduce((now, a) => (time >= +a.t ? a.act : now), object.act);
                    return going || rig.kind === 'four' || ((!doing || doing === 'idle') && !E.said.has(object.id)) ? 1 : 0; };
                let w = 0;
                for (let i = -2; i <= 2; i++) w += own(sceneTime + i * 0.12) / 5;
                const tau = steps.clock.at(sceneTime);
                if (w > 0.001 && tau != null) {
                    const held = steps.laid.at(tau);
                    // An arm that is one skin with the body (a jacket, a dress) would pull a web of cloth after it:
                    // it goes only part of the way, as in the formulas.
                    for (const side of ['l', 'r']) {
                        const free = rig.free ? rig.free[side] : 1, k = 0.3 + 0.7 * free * free * (3 - 2 * free);
                        if (k < 0.999) for (const bone of ['arm_' + side, 'elbow_' + side]) if (held[bone]) held[bone] = Solid.slerp([0, 0, 0, 1], held[bone], k);
                    }
                    const real = Solid.pose(rig, held, 1);
                    if (w >= 0.999 || !bones) bones = real;
                    else for (let i = 0; i < bones.length; i++) bones[i] = bones[i] * (1 - w) + real[i] * w;
                    stepsHold = Math.min(1, w);
                }
                // feet that are on the ground stay where they stepped (`pin`, on unless turned off)
                if (!bare && rig.kind === 'biped' && stepsHold > 0 && object.motion.pin !== false) pinFeet(object, model, steps, bones, matrix, { worldId, walkTime, sceneTime, from }, stepsHold);
            }
        }
        // the figure whose eyes the route is looks where the route's camera looks (its head and a little of its chest)
        if (!bare && rig && !model.bones && rig.kind === 'biped' && bones && E.doc.tours.length && (E.tour().walker === object.id || object.route_eyes) && E.tour().walker_looks !== false) {
            const look = routeLook(sceneTime);
            if (look && look.world === worldId) {
                const d = Core.dirFromAngles(look.yaw, look.pitch), inv = Solid.affineInverse(matrix);
                Solid.lookToward(rig, bones, [inv[0] * d[0] + inv[4] * d[1] + inv[8] * d[2], inv[1] * d[0] + inv[5] * d[1] + inv[9] * d[2], inv[2] * d[0] + inv[6] * d[1] + inv[10] * d[2]], 1);
            }
        }
        // seen through its own eyes: its body, not its head
        if (sheet.headless && rig && bones) Solid.hideBranch(rig, bones, rig.names.indexOf('neck'));
        // its shadow lies on the ground under it
        let shadow = null;
        const dark = object.shadow == null ? (object.on_ground ? 0.45 : 0) : +object.shadow;       // what does not stand on the ground has no ground known under it
        if (dark > 0 && !object.held) {
            const rx = Math.max(model.high[0] - model.low[0], (model.high[2] - model.low[2]) * 0.8) * k * 0.62, rz = Math.max((model.high[2] - model.low[2]), (model.high[0] - model.low[0]) * 0.5) * k * 0.62;
            const height = Math.max(0, hover + (object.on_ground ? (+object.pos[1] || 0) * (sheet.small || 1) : 0));
            const fadeUp = 1 / (1 + height / Math.max(tall, 1e-3) * 2.5);
            shadow = { at: [feet[0], feet[1] - height + 0.012 * k * own, feet[2]], across: [fz * rx * wider, 0, -fx * rx * wider], along: [fx * rz * deeper, 0, fz * rz * deeper], dark: dark * fadeUp };
        }
        // Its picture on its model. The model was made of the object's picture, so the picture fits the model's
        // front: laid there, the front has the picture's own sharpness and colours (a model's face is seldom
        // more than a smear), and while the object says a line, the video of the line lies there and its
        // lips move. `face_on`: 'front' (all of its front), 'head' (from the neck up), 'speaking' (the head,
        // and only while it speaks), or false.
        let face = null;
        const wanted = object.face_on == null ? 'speaking' : object.face_on === 'always' ? 'head' : object.face_on;
        if (wanted && (wanted !== 'speaking' || E.said.has(object.id))) {
            const away = rig && rig.front < 0, map = pictureOn(object, model), said = E.said.get(object.id);
            const neck = rig && rig.kind === 'biped' ? rig.joints.neck[1] - 0.045 * own : model.low[1] + 0.8 * own;
            face = { map, from: wanted === 'front' ? model.low[1] - own : neck, soft: 0.035 * own, front: away ? -1 : 1, speaking: wanted === 'speaking' };
            // the line it is saying was filmed as a close-up of its head: where the model is in that close-up
            if (said && said.crop) { const c = said.crop, cw = c[2] - c[0], ch = c[3] - c[1]; face.talk = [(map[0] - c[0]) / cw, map[1] / cw, (map[2] - c[1]) / ch, map[3] / ch]; }
        }
        const bright = object.bright == null ? 1 : +object.bright;
        // Seen through its own eyes (its head pulled in, its body behind its eyes) nothing of it within an eighth
        // of its height of the eyes is drawn either: hair on its shoulders, a collar. Looking down, its chest and
        // hands are seen as from its eyes. (Cut at its waist, a look down went into its clothes.)
        return { model: matrix, bones, lit: object.lit == null ? 0.8 : +object.lit, tone: (object.tone || [1, 1, 1]).map(v => v * bright), shadow, scale: k, face, near: sheet.headless ? 0.12 * tall : 0,
            colour: object.colour ? colourOf(object.colour) : null, skin: object.skin ? Math.max(0.1, +object.skin_repeat || 1) : 0 };
    };

    /* where a ray meets a solid object's box: how far along the ray, or null */
    function solidHit(solid, model, origin, d) {
        const m = solid.model, k2 = m[0] * m[0] + m[1] * m[1] + m[2] * m[2], ky = m[4] * m[4] + m[5] * m[5] + m[6] * m[6], kz = m[8] * m[8] + m[9] * m[9] + m[10] * m[10];
        const rel = [origin[0] - m[12], origin[1] - m[13], origin[2] - m[14]];
        const into = v => [(v[0] * m[0] + v[1] * m[1] + v[2] * m[2]) / k2, (v[0] * m[4] + v[1] * m[5] + v[2] * m[6]) / ky, (v[0] * m[8] + v[1] * m[9] + v[2] * m[10]) / kz];
        const o = into(rel), r = into(d);
        let near = 0, far = Infinity;
        for (let j = 0; j < 3; j++) {
            if (Math.abs(r[j]) < 1e-9) { if (o[j] < model.low[j] || o[j] > model.high[j]) return null; continue; }
            let a = (model.low[j] - o[j]) / r[j], b = (model.high[j] - o[j]) / r[j];
            if (a > b) [a, b] = [b, a];
            near = Math.max(near, a); far = Math.min(far, b);
            if (near > far) return null;
        }
        return near;
    }

    /* "Make it 3D": Hunyuan3D makes a model of the object's picture, and the model takes the picture's place. */
    E.makeSolid = async (object, options) => {
        const world = E.objectWorld(object.id) || E.world();
        if (object.turn == null) object.turn = Math.round(Core.anglesFromDir(...E.objectSheet(object, E.cam.pos, E.objectNow(object, world.id)).normal).yaw);
        let result;
        try { result = await E.job('solid', { project: E.name, world: world.id, object: object.id, file: object.poster || object.file, options: options || {} }); } finally { E.askSolid(); }
        object.model = result.model; object.model_facts = result.model_facts; object.flat = false;
        E.touch('objects');
        await E.ensure(world.id);
        await suggestBody(object);
        await E.matchTone(object);
        if (object.face_on == null) { object.face_on = object.picture_fits >= 0.8 ? 'front' : false; E.touch('objects'); }       // the picture goes on the model's front when it shows that front
        return object;
    };

    /* A model's colours are painted without the light its picture had: they come out darker, and of another
     * cast. Its tone (how much of red, green and blue) is set so that, seen from its front, it has the
     * colours of the picture it was made of. */
    E.matchTone = async object => {
        const model = E.modelOf(object), world = E.objectWorld(object.id);
        if (!model || !world) return null;
        const mean = (d, n) => { const sum = [0, 0, 0]; let count = 0; for (let i = 0; i < n; i++) if (d[i * 4 + 3] > 200) { sum[0] += d[i * 4]; sum[1] += d[i * 4 + 1]; sum[2] += d[i * 4 + 2]; count++; } return count ? sum.map(v => v / count) : null; };
        const seen = E.gl.portrait(E.objectsLoaded.get(object.id).key, model.low, model.high, 128, 192, 0, 0);
        const bitmap = await bitmapFrom(E.fileUrl(world, object.poster || object.file)), picture = pixelsOf(bitmap, 128, 192);
        bitmap.close();
        const of = mean(picture.data, 128 * 192), has = mean(seen, 128 * 192);
        if (!of || !has) return null;
        object.tone = of.map((v, k) => +Core.clamp(v / Math.max(has[k], 4), 0.6, 2.2).toFixed(3));
        // Does the picture show the model's front? Hunyuan3D turns what it makes to stand square (a car drawn
        // from three quarters comes out side on), and then the picture does not fit the front it would be
        // laid on. The two outlines are compared, each in its own frame.
        object.picture_fits = +outlinesAlike(picture.data, seen, 128, 192).toFixed(2);
        E.touch('objects');
        return object.tone;
    };

    /* how alike two cut-outs are (RGBA, the same size): what they share of what either covers, 0..1, after
     * each is fitted to its own frame */
    function outlinesAlike(a, b, width, height) {
        const N = 48, fitted = data => {
            let x0 = width, y0 = height, x1 = -1, y1 = -1;
            for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) if (data[(y * width + x) * 4 + 3] > 127) { if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y; }
            const grid = new Uint8Array(N * N);
            if (x1 < x0) return grid;
            for (let j = 0; j < N; j++) for (let i = 0; i < N; i++) {
                const x = Math.min(width - 1, Math.round(x0 + (i + 0.5) / N * (x1 - x0))), y = Math.min(height - 1, Math.round(y0 + (j + 0.5) / N * (y1 - y0)));
                grid[j * N + i] = data[(y * width + x) * 4 + 3] > 127 ? 1 : 0;
            }
            return grid;
        };
        const ga = fitted(a), gb = fitted(b);
        let both = 0, either = 0;
        for (let i = 0; i < N * N; i++) { both += ga[i] & gb[i]; either += ga[i] | gb[i]; }
        return either ? both / either : 0;
    }
    E.outlinesAlike = outlinesAlike;

    /* a model that stands tall on two legs is taken for a figure; anything else is left a thing until it is said what it is */
    async function suggestBody(object) {
        const model = E.modelOf(object);
        if (!model || model.bones || object.body) return;
        const tall = model.high[1] - model.low[1], wide = Math.max(model.high[0] - model.low[0], model.high[2] - model.low[2]);
        let kind = '';
        if (tall > wide * 1.35) { const shape = Solid.guess(model, 'biped'); if (shape.legs) kind = 'biped'; }
        object.body = { kind };
        if (kind) { if (object.act == null) object.act = 'idle'; await E.rigObject(object); }
        E.touch('objects');
    }

    /* A new solid object out of words: Forge draws the thing by itself, Hunyuan3D makes its model, and it is
     * stood in front of the camera. */
    E.newSolid = async (settings, options) => {
        const world = E.world(), id = uid('o'), s = Object.assign({}, settings);
        let prompt = (s.prompt || '').trim();
        if (!prompt) throw new Error('Write what to make first');
        if (s.lcm && E.options.lora_lcm && !/<lora:LCM/i.test(prompt)) prompt += ` <lora:${E.options.lora_lcm}:1>`;
        // A figure is drawn on a skeleton, so it is whole (Forge likes to cut people at the knees) and its arms
        // and legs stand apart: a model with free limbs is one that can walk.
        let pose = null;
        if (s.figure && E.options.openpose_model) {
            const size = { portrait: [512, 768], square: [640, 640], landscape: [768, 512] }[s.shape] || [512, 768];
            const joints = Core.placePose('standing', size[0] / size[1], 0.9, false);
            for (const [joint, by] of [[3, -0.045], [4, -0.1], [6, 0.045], [7, 0.1], [9, -0.012], [10, -0.03], [12, 0.012], [13, 0.03]]) if (joints[joint]) joints[joint][0] += by;
            const drawing = document.createElement('canvas');
            drawing.width = size[0]; drawing.height = size[1];
            const ctx = drawing.getContext('2d');
            ctx.fillStyle = '#000'; ctx.fillRect(0, 0, size[0], size[1]);
            E.drawPose(ctx, joints, size[0], size[1]);
            pose = drawing.toDataURL('image/png');
        }
        let result;
        try {
            result = await E.job('solid', {
                project: E.name, world: world.id, object: id, options: options || {}, pose,
                settings: { ...s, prompt, checkpoint: s.checkpoint || (world.gen && world.gen.checkpoint) || null },
            });
        } finally { E.askSolid(); }
        return standNew(world, id, (s.prompt || '').trim().slice(0, 40), result);
    };

    /* a new object stands in front of the camera, facing it */
    async function standNew(world, id, name, result, more) {
        const aspect = result.width / result.height, h = 0.62, w = h * aspect / E.aspect();
        const view = { world: world.id, cam: { ...E.cam, pos: E.cam.pos.slice(), roll: 0 }, aspect: E.aspect(), crop: { x0: 0.5 - w / 2, x1: 0.5 + w / 2, y0: 0.56 - h / 2, y1: 0.56 + h / 2 } };
        const object = Object.assign({ id, name, turn: null, relief: 0.2, opacity: 1, flip: false, history: [] }, E.placeObject(view, result.box || [0, 0, 1, 1]));
        adoptObject(object, result);
        // (what else it is: a shape and its colour, or everything a kept object was, except where it stood)
        for (const [key, value] of Object.entries(more || {})) if (!['id', 'name', 'pos', 'turn', 'path', 'walk', 'held', 'history', 'on_ground', 'shy'].includes(key) && value != null) object[key] = value;
        object.turn = Math.round(Core.anglesFromDir(...E.objectSheet(object, E.cam.pos).normal).yaw);
        world.objects = (world.objects || []).concat([object]);
        E.chosen = id;
        E.touch('objects');
        await E.ensure(world.id);
        await suggestBody(object);
        if (result.infotext) { await E.matchTone(object); if (object.face_on == null) object.face_on = object.picture_fits >= 0.8 ? 'front' : false; E.touch('objects'); }       // one that was made of a drawn picture
        return object;
    }

    /* A .glb file from the computer becomes a solid object. Its picture (what it is listed with, and what
     * stands in for it when it is shown flat) is drawn here, from its front. */
    E.uploadSolid = async file => {
        const world = E.world(), id = uid('o'), bytes = await file.arrayBuffer();
        const model = await loadModel(id, bytes.slice(0));
        let pixels;
        try { pixels = E.gl.portrait(id, model.low, model.high, 512, 768); } finally { E.gl.dropModel(id); }
        const portrait = await toBlob(canvasFromPixels(pixels, 512, 768), 'image/png');
        const kept = await call('/upload-model', { raw: bytes, query: { project: E.name, world: world.id, object: id, filename: file.name || 'model.glb' } });
        const started = await call('/upload', { raw: portrait, query: { project: E.name, world: world.id, filename: 'model.png', object: id } });
        const result = await E.job('import_object', { world: world.id }, null, started);
        return standNew(world, id, (file.name || '').replace(/\.[^.]+$/, '').slice(0, 40), Object.assign(result, kept));
    };

    /* what is known about Hunyuan3D is asked again: whether one is running now, and whose */
    E.askSolid = async () => { try { E.options.solid = await call('/solid/state'); E.emit('objects'); } catch (e) { /* it stays as it was */ } return E.options.solid; };
    E.stopSolid = async () => { await call('/solid/stop', { body: {} }); await E.askSolid(); };
    /* Hunyuan3D becomes a part of Forge: the copy another program installed is linked into Forge's models folder */
    E.adoptSolid = async () => { try { return await E.job('solid_home', {}); } finally { await E.askSolid(); } };

    E.undoObject = async object => {
        if (!(object.history || []).length) return;
        Object.assign(object, object.history.pop());
        E.touch('objects');
        await E.ensure(E.worldId);
    };

    /* A picture or a video from the computer becomes an object, standing in front of the camera. */
    E.uploadObject = async (file, keepBackground) => {
        const world = E.world(), id = uid('o');
        const started = await call('/upload', { raw: file, query: { project: E.name, world: world.id, filename: file.name || 'object.png', object: id, keep_background: keepBackground ? 'true' : 'false' } });
        const result = await E.job('import_object', { world: world.id }, null, started);
        const aspect = result.width / result.height, h = 0.62, w = h * aspect / E.aspect();
        const view = { world: world.id, cam: { ...E.cam, pos: E.cam.pos.slice(), roll: 0 }, aspect: E.aspect(), crop: { x0: 0.5 - w / 2, x1: 0.5 + w / 2, y0: 0.56 - h / 2, y1: 0.56 + h / 2 } };
        const object = Object.assign({ id, name: (file.name || '').replace(/\.[^.]+$/, '').slice(0, 40), turn: null, relief: 0.2, opacity: 1, flip: false, history: [] }, E.placeObject(view, result.box || [0, 0, 1, 1]));
        adoptObject(object, result);
        world.objects = (world.objects || []).concat([object]);
        E.chosen = id;
        E.touch('objects');
        await E.ensure(world.id);
        return object;
    };

    /* "Generate on it": the object is drawn again on its own picture, inside its own outline. */
    E.redrawObject = async (object, settings) => {
        const world = E.world();
        if (object.kind !== 'image') throw new Error('Only picture objects can be drawn again');
        const s = Object.assign({}, (object.gen && object.gen.settings) || {}, settings || {});
        let prompt = (s.prompt || '').trim();
        if (!prompt) throw new Error('Write a prompt first');
        if (s.lcm && E.options.lora_lcm && !/<lora:LCM/i.test(prompt)) prompt += ` <lora:${E.options.lora_lcm}:1>`;
        let pose = null;
        if (object.gen && object.gen.mode === 'pose' && object.gen.joints && s.keep_pose !== false) {
            const drawing = document.createElement('canvas');
            drawing.width = object.width; drawing.height = object.height;
            const ctx = drawing.getContext('2d');
            ctx.fillStyle = '#000'; ctx.fillRect(0, 0, drawing.width, drawing.height);
            E.drawPose(ctx, object.gen.joints, drawing.width, drawing.height);
            pose = drawing.toDataURL('image/png');
        }
        const result = await E.job('object', {
            project: E.name, world: world.id, object: object.id, file: object.file, pose,
            settings: { ...s, prompt, checkpoint: s.checkpoint || (world.gen && world.gen.checkpoint) || null },
        });
        adoptObject(object, result);
        object.gen = Object.assign({}, object.gen, { settings: { ...s, seed: -1 } });
        E.touch('objects');
        await E.ensure(world.id);
        return object;
    };

    /* How the camera is carried in a world (the present one unless one is named): the world's own way when it
     * has one (walking, hovering, on a bicycle...), otherwise the project's. */
    E.rig = worldId => {
        const s = E.doc && E.doc.settings || {}, world = E.doc ? E.world(worldId === undefined ? E.worldId : worldId) : null;
        // a runner's stride and pace, in this project's metres: worlds drawn from a picture are often smaller
        // than they look, and a run across one would be over in three steps
        Core.RIGS.run.stride = +s.stride || 1.05; Core.RIGS.run.pace = +s.pace || 1.7;
        const own = world && world.rig && Core.RIGS[world.rig];
        return { kind: own ? world.rig : s.rig || 'pro', strength: own && world.rig_strength != null ? +world.rig_strength : s.rig_strength == null ? 1 : +s.rig_strength,
            speed: own && world.rig_speed ? +world.rig_speed : +s.speed || 1.5 };
    };

    /* ---- the camera ------------------------------------------------------------------------------------- */

    const DEAD = 0.16;
    const stick = v => Math.abs(v) < DEAD ? 0 : (v - Math.sign(v) * DEAD) / (1 - DEAD);

    function readPad() {
        const pads = navigator.getGamepads ? navigator.getGamepads() : [];
        for (const pad of pads) {
            if (!pad || !pad.connected || pad.axes.length < 2) continue;
            const b = i => pad.buttons[i] ? pad.buttons[i].value || (pad.buttons[i].pressed ? 1 : 0) : 0;
            return {
                id: pad.id, x: stick(pad.axes[0]), y: stick(pad.axes[1]), lookX: stick(pad.axes[2] || 0), lookY: stick(pad.axes[3] || 0),
                up: b(7) - b(6), zoom: b(5) - b(4), enter: b(0) > 0.5, back: b(1) > 0.5, fast: b(10) > 0.5 || b(2) > 0.5,
            };
        }
        return null;
    }

    E.portalAt = (u, v) => {
        const world = E.world(), masks = E.masks.get(E.worldId);
        if (!world || !masks) return null;
        const x = Math.min(MASK_W - 1, Math.floor((u - Math.floor(u)) * MASK_W)), y = Math.min(MASK_H - 1, Math.max(0, Math.floor(v * MASK_H)));
        for (let i = 0; i < masks.length; i++) if (masks[i] && masks[i][y * MASK_W + x] > 127) return world.portals[i];
        return null;
    };

    /* the portal in the middle of the view, if there is one */
    E.aimed = () => {
        const d = Core.dirFromAngles(E.cam.yaw, E.cam.pitch), field = E.field();
        // from where the camera stands: walk along the look direction to the surface
        let t = 0.2, p = E.cam.pos, hit = null;
        for (let i = 0; i < 48 && !hit; i++) {
            const q = [p[0] + d[0] * t, p[1] + d[1] * t, p[2] + d[2] * t], r = Math.hypot(q[0], q[1], q[2]);
            if (r >= field.distTowards(q[0], q[1], q[2])) hit = q;
            t *= 1.22;
        }
        const uv = hit ? Core.uvFromDir(hit[0], hit[1], hit[2]) : Core.uvFromDir(d[0], d[1], d[2]);
        return E.portalAt(uv[0], uv[1]);
    };

    /* Where the camera is when it goes through a portal: on the way to its middle, looking at it, and
     * zoomed in until the portal fills most of the picture, so going through feels like plunging into it. */
    E.doorstep = (portal, widest) => {
        const c = E.wayIn(portal), d = Core.dirFromAngles(c.yaw, c.pitch);
        const far = E.field(E.portalWorld(portal)).distTowards(d[0], d[1], d[2]);
        const reach = Math.min(far * (ENTER_SHARE + 0.012), ENTER_METRES + 0.1);
        const seen = half => Math.atan(Math.tan(Math.min(80, half) * Core.RAD) * far / Math.max(0.05, far - reach)) / Core.RAD;
        const across = seen(+portal.span || 8), tall = seen(+portal.rise || +portal.span || 8);
        const fov = Core.clamp(Math.max(2 * tall / 0.8, 2 * across / 0.8 / 1.6), 26, widest || 90);
        return { pos: [d[0] * reach, d[1] * reach, d[2] * reach], yaw: c.yaw, pitch: c.pitch, fov };
    };
    E.portalWorld = portal => { const w = E.doc.worlds.find(w => (w.portals || []).includes(portal)); return w ? w.id : E.worldId; };

    /* fly to a portal and through it */
    E.enter = portal => {
        portal = portal || E.aimed();
        if (!portal || E.cross || E.play) return false;
        if (!portal.target || !E.world(portal.target)) { E.say('This portal leads nowhere yet: choose its world', 'warn'); return false; }
        const to = E.doorstep(portal, E.cam.fov), from = { pos: E.cam.pos.slice(), yaw: E.cam.yaw, pitch: E.cam.pitch, fov: E.cam.fov };
        if (E.deepPortal(portal)) to.fov = from.fov;           // the world behind it is really there: nothing to plunge into
        const far = Math.hypot(to.pos[0] - from.pos[0], to.pos[1] - from.pos[1], to.pos[2] - from.pos[2]);
        // (`through`: it is walked through without stopping on its doorstep)
        E.auto = { from, to, t: 0, seconds: Core.clamp(far / (E.rig().speed * 1.6), 1.4, 5), portal, through: E.deepPortal(portal) };
        E.glide = null; E.level = null;
        return true;
    };

    function beginCross(portal) {
        const target = E.world(portal.target);
        if (!target || E.cross) return;
        const ready = E.gl.world(target.id);
        if (!ready || !ready.tex) { E.ensure(target.id); return; }
        E.cross = {
            portal, from: E.worldId, to: target.id, turn: E.turnOf(portal), arrive: E.arrival(portal), phase: 'mix', t: 0, style: portal.style || 'smooth',
            deep: (portal.style || 'smooth') === 'smooth' && E.deepPortal(portal),
            widen: E.auto && E.auto.portal === portal ? E.auto.from.fov : null,       // the view it had before it zoomed into the portal
        };
        if (E.cross.deep && E.auto && E.auto.portal === portal) {
            // Going through a door, nobody stops in the doorway: the camera goes on at the pace it came with,
            // through the opening and a few steps into the next world, where it slows down.
            const a = E.auto, way = [0, 1, 2].map(i => a.to.pos[i] - a.from.pos[i]), length = Math.hypot(way[0], way[1], way[2]);
            if (length > 1e-3) E.glide = { dir: way.map(v => v / length), speed: length / a.seconds };
            E.auto = null;
        }
        E.emit('cross');
    }

    function stepCross(dt) {
        const c = E.cross;
        c.t += dt;
        if (c.deep) {
            // The next world is already there behind the portal, in its depth. It opens around the camera,
            // and then the camera simply is in it, at the very place it was seeing it from.
            E.mix = { world: c.to, amount: Core.smooth(c.t / Core.SPREAD_SECONDS), turn: c.turn, deep: true, via: c.portal.id };
            if (c.t >= Core.SPREAD_SECONDS) {
                E.trail.push({ world: c.from, pose: { pos: E.cam.pos.slice(), yaw: E.cam.yaw, pitch: E.cam.pitch, fov: E.cam.fov } });
                if (E.trail.length > 50) E.trail.shift();
                const there = E.beyond(c.portal, E.cam);
                E.worldId = c.to;
                E.cam.pos = Core.constrain(there.pos, E.field(c.to), WALL_SHARE).pos;
                E.cam.yaw = Core.wrapDeg(there.yaw);
                // it goes on the way it was going, as that way lies in this world, and no longer up or down;
                // and a view that was tipped up at a window or down at a hatch comes level again
                if (E.glide) { const d = Core.swing(E.glide.dir, c.turn); E.glide.dir = [d[0], d[1] * 0.25, d[2]]; }
                E.level = { to: +c.arrive.pitch || 0 };
                E.mix = null; E.auto = null; E.relief = 1; E.cross = null;
                E.emit('world');
                E.ensureNeighbours(c.to);
            }
            return;
        }
        const flash = c.style === 'flash', mixSeconds = c.style === 'cut' ? 0 : flash ? 0.22 : Core.MIX_SECONDS;
        if (c.phase === 'mix') {
            const x = mixSeconds ? Core.smooth(c.t / mixSeconds) : 1;
            if (flash) E.fade = [1, 1, 1, x]; else E.mix = { world: c.to, amount: x, turn: c.turn };
            if (c.t >= mixSeconds) {
                E.trail.push({ world: c.from, pose: { pos: E.cam.pos.slice(), yaw: E.cam.yaw, pitch: E.cam.pitch, fov: E.cam.fov } });
                if (E.trail.length > 50) E.trail.shift();
                E.worldId = c.to;
                E.cam.pos = c.arrive.pos.slice();
                E.cam.yaw = Core.wrapDeg(E.cam.yaw + c.turn);
                E.mix = null; E.auto = null;
                if (c.widen) E.fovWanted = c.widen;
                E.relief = flash || c.style === 'cut' ? 1 : 0;
                c.phase = 'unfold'; c.t = 0;
                E.emit('world');
                E.ensureNeighbours(c.to);
            }
        } else {
            const seconds = flash ? 0.5 : c.style === 'cut' ? 0 : Core.UNFOLD_SECONDS, x = seconds ? Core.smooth(c.t / seconds) : 1;
            if (flash) E.fade = [1, 1, 1, 1 - x]; else E.relief = x;
            if (c.t >= seconds) { E.cross = null; E.relief = 1; E.fade = null; }
        }
    }

    /* back to where the last portal was entered */
    E.back = async () => {
        const last = E.trail.pop();
        if (!last || E.cross) return false;
        await E.go(last.world, last.pose);
        return true;
    };

    E.step = dt => {
        E.clock += dt;
        if (E.exporting) return;
        // a browser may stop a video it thinks nobody watches (the page was hidden when it loaded): moving
        // worlds are kept moving
        E.videoChecked = (E.videoChecked || 0) + dt;
        if (E.videoChecked > 0.5) {
            E.videoChecked = 0;
            for (const video of E.videos.values()) if (video.paused && video.readyState >= 2) video.play().catch(() => {});
            for (const have of E.objectsLoaded.values()) if (have.video && have.video.paused && have.video.readyState >= 2) have.video.play().catch(() => {});
        }
        if (E.play) { stepPlay(dt); return; }
        if (E.saying) {
            E.saying.t += dt;
            if (E.saying.t >= (+E.saying.line.seconds || 0)) E.hush(); else { hear(null); tell(null); }
        }
        const cam = E.cam, rig = E.rig(), kind = Core.RIGS[rig.kind] || Core.RIGS.pro, keys = E.keys;
        const pad = E.pad = readPad();
        const held = (...codes) => codes.some(c => keys.has(c)) ? 1 : 0;

        // looking around: the mouse, the arrow keys, the right stick
        const perPixel = cam.fov / Math.max(200, E.gl.canvas.clientHeight);
        cam.yaw -= E.look[0] * perPixel; cam.pitch += E.look[1] * perPixel;
        const looked = E.look[0] !== 0 || E.look[1] !== 0;
        E.look[0] = E.look[1] = 0;
        const turnRate = 75 * cam.fov / 90;
        cam.yaw += (held('ArrowRight') - held('ArrowLeft') + (pad ? pad.lookX * 1.3 : 0)) * turnRate * dt;
        cam.pitch += (held('ArrowUp') - held('ArrowDown') - (pad ? pad.lookY : 0)) * turnRate * 0.7 * dt;
        if (E.level) {
            // after a portal the view comes level by itself, unless the user is looking somewhere
            if (looked || held('ArrowUp', 'ArrowDown') || (pad && Math.abs(pad.lookY) > 0.2)) E.level = null;
            else { cam.pitch += (E.level.to - cam.pitch) * (1 - Math.exp(-dt * 2.2)); if (Math.abs(E.level.to - cam.pitch) < 0.25) E.level = null; }
        }
        cam.pitch = Core.clamp(cam.pitch, -89, 89);
        cam.yaw = Core.wrapDeg(cam.yaw);

        // zoom
        const zoom = held('Equal', 'NumpadAdd') - held('Minus', 'NumpadSubtract') + (pad ? pad.zoom : 0);
        if (zoom) E.fovWanted = Core.clamp(E.fovWanted * Math.pow(0.45, zoom * dt), 12, 125);
        cam.fov += (E.fovWanted - cam.fov) * (1 - Math.exp(-dt * 9));

        // moving
        let want = [held('KeyD') - held('KeyA') + (pad ? pad.x : 0), held('KeyE') - held('KeyQ') + (pad ? pad.up : 0),
            held('KeyW') - held('KeyS') - (pad ? pad.y : 0)];
        if (rig.kind === 'walk') want[1] = 0;
        const fast = (held('ShiftLeft', 'ShiftRight') || (pad && pad.fast)) ? 2.6 : 1;
        const ease = 1 - Math.exp(-dt * kind.accel);
        for (let i = 0; i < 3; i++) E.vel[i] += (Core.clamp(want[i], -1, 1) * fast - E.vel[i]) * ease;
        const before = cam.pos;
        let next;
        if (E.auto) {
            const a = E.auto;
            a.t += dt;
            const share = Math.min(1, a.t / a.seconds), x = a.through ? share * share * (2 - share) : Core.smooth(share);       // (`through`: it arrives still moving)
            next = [0, 1, 2].map(i => Core.lerp(a.from.pos[i], a.to.pos[i], x));
            cam.yaw = Core.wrapDeg(a.from.yaw + Core.turn(a.from.yaw, a.to.yaw) * x);
            cam.pitch = Core.lerp(a.from.pitch, a.to.pitch, x);
            if (a.to.fov) cam.fov = E.fovWanted = Core.lerp(a.from.fov, a.to.fov, x);
            if (want.some(v => Math.abs(v) > 0.5)) { E.fovWanted = a.from.fov; E.auto = null; }      // the user takes over
            else if (a.t >= a.seconds + 0.6) E.auto = null;
        } else {
            const b = Core.basis(cam.yaw, rig.kind === 'walk' ? 0 : cam.pitch, 0), speed = rig.speed * dt;
            next = [
                before[0] + (b.r[0] * E.vel[0] + b.f[0] * E.vel[2]) * speed,
                before[1] + (E.vel[1] + b.f[1] * E.vel[2]) * speed,
                before[2] + (b.r[2] * E.vel[0] + b.f[2] * E.vel[2]) * speed,
            ];
            if (rig.kind === 'walk') next[1] += (0 - next[1]) * (1 - Math.exp(-dt * 3));        // back to eye height
            if (E.glide) {
                // carried on by the pace it went through a portal with, slowing down
                const g = E.glide;
                next = next.map((v, i) => v + g.dir[i] * g.speed * dt);
                g.speed *= Math.exp(-dt * (E.cross ? 0.4 : 1.9));
                if (g.speed < 0.05 || want.some(v => Math.abs(v) > 0.5)) E.glide = null;
            }
        }
        const field = E.field();
        // (while a world behind a portal is taking over the picture, the camera may go on towards the opening:
        // what it would stretch there is already the next world)
        const held_ = Core.constrain(next, field, E.cross && E.cross.deep ? 0.95 : WALL_SHARE);
        cam.pos = held_.pos;
        E.travelled += Math.hypot(cam.pos[0] - before[0], cam.pos[1] - before[1], cam.pos[2] - before[2]);

        if (E.cross) stepCross(dt);
        else if (held_.uv && (held_.share >= ENTER_SHARE || Math.hypot(cam.pos[0], cam.pos[1], cam.pos[2]) >= ENTER_METRES)) {
            const portal = E.portalAt(held_.uv[0], held_.uv[1]);
            if (portal && portal.target) beginCross(portal);
        }
        if (pad) {
            if (pad.enter && !E.padEnter) E.enter();
            if (pad.back && !E.padBack) E.back();
            E.padEnter = pad.enter; E.padBack = pad.back;
        }
        if (E.recording) {
            const r = E.recording;
            r.samples.push({
                t: E.clock - r.started, world: E.worldId, pos: cam.pos.slice(), yaw: cam.yaw, pitch: cam.pitch, fov: cam.fov,
                relief: E.relief, mix: E.mix ? { ...E.mix } : null, travelled: E.travelled, fade: E.fade ? E.fade[3] : 0,
            });
        }
    };

    /* ---- the selection ---------------------------------------------------------------------------------- */

    function showSelection() {
        const d = E.sel.data;
        let any = false;
        for (let i = 0, n = d.length; i < n; i += 97) if (d[i] > 127) { any = true; break; }
        if (!any) for (let i = 0, n = d.length; i < n; i++) if (d[i] > 127) { any = true; break; }
        E.sel.any = any;
        if (E.gl) E.gl.setSelection(any ? d : null, MASK_W, MASK_H);
        E.emit('selection');
    }

    E.clearSelection = () => { E.sel.data.fill(0); E.sel.way = null; E.wandState = null; showSelection(); };

    /* What is far is selected: everything at least `metres` away, within `half` degrees to either side of
     * where the camera looks. The far part of a street, the far wall of a room: the natural shape of a portal
     * into the next place. It also remembers the heading, as the way into the portal made of it. */
    E.selectFar = (metres, half, subtract) => {
        const field = E.field();
        if (!field.n) throw new Error('This world has no depth yet');
        // (a soft pass and a hard cut: single stray points of the depth do not become holes or islands)
        const rough = Core.farMask(field, MASK_W, MASK_H, +metres || 6, E.cam.yaw, Core.clamp(+half || 60, 5, 180));
        const far = Core.feathered(rough, MASK_W, MASK_H, 7), d = E.sel.data;
        let any = false;
        for (let i = 0, n = d.length; i < n; i++) {
            const a = far[i] > 127 ? 255 : 0;
            any = any || a > 0;
            d[i] = subtract ? Math.min(d[i], 255 - a) : Math.max(d[i], a);
        }
        if (!any) throw new Error('Nothing here is that far');
        if (!subtract) E.sel.way = { yaw: +E.cam.yaw.toFixed(2), pitch: 0 };
        showSelection();
    };

    /* A gate across the way is selected: an upright opening `ahead` metres in front, where the camera looks,
     * `wide` metres across, from the ground up to `tall` metres (0: up to the sky). What is seen through it
     * from the world's centre becomes the shape; the metres are metres of the scene the picture shows. */
    E.selectGate = (ahead, wide, tall, subtract) => {
        const gate = Core.gateMask(MASK_W, MASK_H, E.cam.yaw, E.eye(E.worldId), Math.max(0.5, +ahead || 8), Math.max(0.2, +wide || 10), +tall || 0), d = E.sel.data;
        for (let i = 0, n = d.length; i < n; i++) d[i] = subtract ? Math.min(d[i], 255 - gate[i]) : Math.max(d[i], gate[i]);
        if (!subtract) E.sel.way = { yaw: +E.cam.yaw.toFixed(2), pitch: 0 };
        showSelection();
    };
    E.invertSelection = () => { const d = E.sel.data; for (let i = 0, n = d.length; i < n; i++) d[i] = 255 - d[i]; showSelection(); };

    /* A shape drawn on the screen (a canvas, opaque where the shape is) is laid onto the sphere and added to
     * the selection, or taken out of it. `view`: the camera it was drawn in, the present one by default. */
    E.selectShape = (shape, subtract, view, onto) => {
        const state = view || { world: E.worldId, cam: { ...E.cam, pos: E.cam.pos.slice(), roll: 0 }, aspect: E.aspect() };
        const baked = E.gl.bake(state, shape, MASK_W, MASK_H, null);
        const d = E.sel.data, base = onto || d;
        for (let i = 0, n = d.length; i < n; i++) {
            const a = baked[i * 4];
            d[i] = subtract ? Math.min(base[i], 255 - a) : Math.max(base[i], a);
        }
        showSelection();
    };

    /* the view as a picture file, without guides: what the automatic selection and the character look at */
    E.captureFrame = async () => {
        if(!E.doc||!E.worldId||!E.gl)throw new Error('Open a world before capturing a frame');
        const aspect=E.aspect(),w=1920,h=Math.max(64,Math.round(w/aspect)),scale=Math.min(1,4096/Math.max(w,h),Math.sqrt(8000000/(w*h)));
        const width=Math.round(w*scale),height=Math.round(h*scale);
        // Keep the actual viewpoint, camera movement, portals, weather and transitions.
        // The clean flag removes editor guides and selection outlines only.
        const state=E.renderState(true);
        return {canvas:canvasFromPixels(E.gl.capture(state,width,height),width,height),state};
    };
    E.snapshot = async (width, height, options) => {
        options = options || {};
        const state = E.renderState(options.closed === false ? true : 'closed', options.cam || { ...E.cam, pos: E.cam.pos.slice(), roll: 0 });
        state.relief = 1; state.mix = null; state.fade = null;
        if (options.crop) state.crop = options.crop;
        if (options.output) state.output = options.output;
        const pixels = E.gl.capture(state, width, height);
        return { canvas: canvasFromPixels(pixels, width, height), state };
    };

    /* Automatic selection: clicks on the thing (and, with `wanted` false, on what to leave out). */
    E.wand = async (point, subtractMode) => {
        if (!E.options.auto_select) throw new Error('No Segment Anything model was found in models/sam');
        const aspect = E.aspect(), width = 1024, height = Math.round(width / aspect / 8) * 8;
        let w = E.wandState;
        const sameView = w && w.world === E.worldId && w.camKey === JSON.stringify([E.cam.pos, E.cam.yaw, E.cam.pitch, E.cam.fov, aspect]);
        if (!sameView) {
            const shot = await E.snapshot(width, height);
            w = E.wandState = {
                world: E.worldId, camKey: JSON.stringify([E.cam.pos, E.cam.yaw, E.cam.pitch, E.cam.fov, aspect]), key: uid('v'),
                image: shot.canvas.toDataURL('image/jpeg', 0.93), view: { world: E.worldId, cam: shot.state.cam, aspect },
                points: [], base: E.sel.data.slice(), subtract: !!subtractMode, pick: null,
            };
        }
        w.points.push([point.x * width, point.y * height, point.wanted === false ? 0 : 1]);
        const result = await E.job('select', { image: w.image, points: w.points, key: w.key });
        if (!result.count) { E.say('Nothing was found there', 'warn'); return; }
        w.result = result; w.width = width; w.height = height;
        w.shapes = await bitmapFrom(result.masks);
        if (w.pick == null || w.pick >= result.count) w.pick = result.best;
        E.applyWand();
    };

    /* the three shapes Segment Anything offers for the same clicks: -1 smaller, +1 larger */
    E.wandSize = step => {
        const w = E.wandState;
        if (!w || !w.result) return;
        w.pick = Core.clamp(w.pick + step, 0, w.result.count - 1);
        E.applyWand();
    };

    E.applyWand = () => {
        const w = E.wandState, image = pixelsOf(w.shapes, w.width, w.height), d = image.data;
        for (let i = 0, n = w.width * w.height; i < n; i++) { const a = d[i * 4 + w.pick]; d[i * 4] = d[i * 4 + 1] = d[i * 4 + 2] = 255; d[i * 4 + 3] = a; }
        const canvas = document.createElement('canvas');
        canvas.width = w.width; canvas.height = w.height;
        canvas.getContext('2d').putImageData(image, 0, 0);
        E.selectShape(canvas, w.subtract, w.view, w.base);
    };

    /* ---- portals ---------------------------------------------------------------------------------------- */

    function softened(mask) {
        // a soft edge two pixels wide: the portal's rim glows instead of stepping
        const out = new Uint8Array(mask.length), tmp = new Uint16Array(mask.length), w = MASK_W, h = MASK_H;
        for (let y = 0; y < h; y++) for (let x = 0, row = y * w; x < w; x++) {
            tmp[row + x] = mask[row + (x + w - 2) % w] + mask[row + (x + w - 1) % w] + mask[row + x] + mask[row + (x + 1) % w] + mask[row + (x + 2) % w];
        }
        for (let y = 0; y < h; y++) {
            const a = Math.max(0, y - 2) * w, b = Math.max(0, y - 1) * w, c = y * w, d = Math.min(h - 1, y + 1) * w, e = Math.min(h - 1, y + 2) * w;
            for (let x = 0; x < w; x++) out[c + x] = (tmp[a + x] + tmp[b + x] + tmp[c + x] + tmp[d + x] + tmp[e + x]) / 25;
        }
        return out;
    }

    /* the direction of a mask's middle, and how wide it is seen from the centre */
    function describeMask(mask) {
        let sx = 0, sy = 0, sz = 0, weight = 0;
        for (let y = 0; y < MASK_H; y += 4) {
            const v = (y + 0.5) / MASK_H, c = Math.cos((0.5 - v) * Math.PI);
            for (let x = 0; x < MASK_W; x += 4) {
                if (mask[y * MASK_W + x] < 128) continue;
                const d = Core.dirFromUv((x + 0.5) / MASK_W, v);
                sx += d[0] * c; sy += d[1] * c; sz += d[2] * c; weight += c;
            }
        }
        if (!weight) return null;
        const center = Core.anglesFromDir(sx, sy, sz);
        let span = 0, rise = 0;
        for (let y = 0; y < MASK_H; y += 8) for (let x = 0; x < MASK_W; x += 8) {
            if (mask[y * MASK_W + x] < 128) continue;
            span = Math.max(span, Math.abs(Core.turn(center.yaw, ((x + 0.5) / MASK_W - 0.5) * 360)) * Math.cos((0.5 - (y + 0.5) / MASK_H) * Math.PI));
            rise = Math.max(rise, Math.abs((0.5 - (y + 0.5) / MASK_H) * 180 - center.pitch));
        }
        // how far it reaches from its middle, in degrees: to the sides and up or down
        return { center: { yaw: +center.yaw.toFixed(2), pitch: +center.pitch.toFixed(2) }, span: +Math.max(2, span).toFixed(1), rise: +Math.max(2, rise).toFixed(1) };
    }

    async function storeMask(world, portal, mask) {
        const pixels = new Uint8Array(MASK_W * MASK_H * 4);
        for (let i = 0, n = mask.length; i < n; i++) { pixels[i * 4] = pixels[i * 4 + 1] = pixels[i * 4 + 2] = mask[i]; pixels[i * 4 + 3] = 255; }
        const blob = await toBlob(canvasFromPixels(pixels, MASK_W, MASK_H), 'image/png');
        const version = (portal.mask_version || 0) + 1;         // a new file every time: older versions of the project keep theirs
        const answer = await call('/portal-mask', { raw: blob, query: { project: E.name, world: world.id, portal: portal.id, version } });
        portal.mask = answer.file;
        portal.mask_version = version;
    }

    /* the selection becomes a portal of the present world */
    E.createPortal = async target => {
        const world = E.world();
        if (!world) return null;
        if (!E.sel.any) throw new Error('Select the place of the portal first');
        if ((world.portals || []).length >= window.Sim360GL.PORTALS) throw new Error(`A world can have up to ${window.Sim360GL.PORTALS} portals`);
        const mask = softened(E.sel.data), facts = describeMask(E.sel.data);
        if (!facts) throw new Error('The selection is empty');
        const portal = {
            id: uid('p'), name: '', target: target || null, see_through: true, deep: true, feather: FEATHER, style: 'smooth', arrival: null,
            center: facts.center, span: facts.span, rise: facts.rise, level: levelWith(mask, facts.center), way: E.sel.way || null,
        };
        await storeMask(world, portal, mask);
        world.portals = (world.portals || []).concat([portal]);
        const masks = (E.masks.get(world.id) || []).slice(); masks[world.portals.length - 1] = mask;
        E.masks.set(world.id, masks);
        E.showMasks(world);
        const state = E.loaded.get(world.id); if (state) state.portals = world.portals.map(p => p.id + ':' + (p.mask_version || 0)).join(',');
        E.clearSelection();
        E.touch('portals');
        if (portal.target) E.ensure(portal.target);
        return portal;
    };

    /* a portal's shape is replaced by the selection */
    E.reshapePortal = async portal => {
        const world = E.world(E.portalWorld(portal));
        if (!E.sel.any) throw new Error('Select the new place of the portal first');
        const facts = describeMask(E.sel.data);
        portal.center = facts.center; portal.span = facts.span; portal.rise = facts.rise;
        const mask = softened(E.sel.data);
        portal.level = levelWith(mask, facts.center); portal.way = E.sel.way || null;
        await storeMask(world, portal, mask);
        E.clearSelection();
        E.touch('portals');
        await E.ensure(world.id);
    };

    E.removePortal = async portal => {
        const world = E.world(E.portalWorld(portal));
        world.portals = world.portals.filter(p => p !== portal);
        // one gate for two worlds goes as one
        const twin = E.twinOf(portal);
        if (twin) { const there = E.world(E.portalWorld(twin)); there.portals = there.portals.filter(p => p !== twin); delete twin.twin; delete twin.twin_of; }
        E.touch('portals');
        await E.ensure(world.id);
        if (twin) await E.ensure(E.portalWorld(twin)).catch(() => {});
    };

    /* the other side of a gate that leads both ways (null: it leads one way) */
    E.twinOf = portal => {
        const id = portal && (portal.twin || portal.twin_of);
        if (!id) return null;
        for (const w of E.doc.worlds) { const found = (w.portals || []).find(p => p.id === id && p !== portal); if (found) return found; }
        return null;
    };

    /* One gate for two worlds. In the world a portal leads to, a portal of the same shape stands behind the place
     * one arrives at (turned round and mirrored, as a doorway is from its other side) and leads back to just
     * before the first one, facing away from it. So a street can go on into the next world and back again, and
     * a wall-wide portal looks like the street's continuation both ways. Made again whenever it is asked for (after
     * the portal's shape or its arrival changes). */
    E.twinPortal = async portal => {
        const home = E.world(E.portalWorld(portal)), there = E.world(portal.target);
        if (!home || !there) throw new Error('This portal leads nowhere yet: choose its world');
        if (there === home) throw new Error('A portal into its own world cannot lead back');
        await E.ensure(home.id); await E.ensure(there.id);
        const mask = (E.masks.get(home.id) || [])[home.portals.indexOf(portal)];
        if (!mask) throw new Error('The portal has no shape yet');
        const arrive = E.arrival(portal), wayIn = E.wayIn(portal), centre = portal.center ? +portal.center.yaw || 0 : wayIn.yaw, behind = arrive.yaw + 180;
        // its shape behind the arrival: each direction there (an angle from the middle of the way back) looks at
        // the same angle on the other side of the first portal's middle
        const back = new Uint8Array(MASK_W * MASK_H), column = new Int32Array(MASK_W);
        for (let x = 0; x < MASK_W; x++) {
            let u = (centre - Core.turn(behind, ((x + 0.5) / MASK_W - 0.5) * 360)) / 360 + 0.5;
            u -= Math.floor(u);
            column[x] = Math.min(MASK_W - 1, Math.floor(u * MASK_W));
        }
        for (let y = 0; y < MASK_H; y++) for (let x = 0; x < MASK_W; x++) back[y * MASK_W + x] = mask[y * MASK_W + column[x]];
        const facts = describeMask(back);
        if (!facts) throw new Error('The portal has no shape yet');
        let twin = E.twinOf(portal);
        if (!twin) {
            if ((there.portals || []).length >= window.Sim360GL.PORTALS) throw new Error(`A world can have up to ${window.Sim360GL.PORTALS} portals`);
            twin = { id: uid('p'), name: portal.name ? portal.name + ' ↩' : '', twin_of: portal.id };
            there.portals = (there.portals || []).concat([twin]);
            portal.twin = twin.id;
        }
        // where one comes back: just before the first portal, facing away from it, into its world
        const step = E.doorstep(portal).pos.map(v => +(v * 0.85).toFixed(3));
        Object.assign(twin, {
            target: home.id, see_through: portal.see_through !== false, deep: portal.deep !== false, feather: portal.feather, style: portal.style || 'smooth',
            center: facts.center, span: facts.span, rise: facts.rise, level: levelWith(back, facts.center), way: { yaw: +Core.turn(0, behind).toFixed(2), pitch: 0 },
            arrival: { pos: step, yaw: +Core.turn(0, wayIn.yaw + 180).toFixed(2), pitch: 0 },
        });
        await storeMask(there, twin, back);
        E.touch('portals');
        await E.ensure(there.id);
        return twin;
    };

    /* a portal's mask back into the selection, to change it */
    E.selectPortal = portal => {
        const world = E.world(E.portalWorld(portal)), masks = E.masks.get(world.id) || [], mask = masks[world.portals.indexOf(portal)];
        if (!mask) return;
        for (let i = 0, n = mask.length; i < n; i++) E.sel.data[i] = mask[i] > 127 ? 255 : 0;
        showSelection();
    };

    E.lookAtPortal = portal => { const c = portal.center || {}; E.cam.yaw = +c.yaw || 0; E.cam.pitch = (+c.pitch || 0); E.auto = null; };

    /* ---- a character or an object in the view ------------------------------------------------------------ */

    E.openCharacter = () => {
        const world = E.world();
        if (!world || !world.file) throw new Error('Make the world first');
        const saved = (E.doc.settings.character || {});
        E.chosen = null;
        E.character = {
            // an object of its own (3D), or painted into the panorama; a video world can only take objects
            result: world.kind === 'image' && saved.result === 'paint' ? 'paint' : 'object',
            mode: saved.mode || 'pose', aspect: saved.aspect || '2:3', frame: { cx: 0.5, cy: 0.52, h: 0.82 },
            pose: saved.pose || 'standing', figure: 0.86, mirror: false, joints: null, busy: false, pending: null,
            settings: Object.assign({
                prompt: '', negative: 'blurry, deformed, extra limbs, text, watermark', steps: 8, cfg: 2, sampler: (E.options.samplers || []).includes('LCM') ? 'LCM' : 'Euler a',
                seed: -1, denoise: 1, fill: 'original', mask_blur: 8, control_weight: 1, checkpoint: '', lcm: !!E.options.lora_lcm,
            }, saved.settings || {}),
        };
        // a moving object: AnimateDiff draws it as a short video
        E.character.settings.animatediff = Object.assign({
            enabled: false, model: (E.options.motion_modules || []).find(m => /lcm/i.test(m)) || (E.options.motion_modules || [])[0] || '',
            frames: 16, fps: 8, interp: 'Off', interp_x: 3,
        }, E.character.settings.animatediff || {});
        E.resetPose();
        E.emit('character');
        return E.character;
    };

    E.resetPose = () => {
        const c = E.character, size = CHARACTER_SIZES[c.aspect] || CHARACTER_SIZES['2:3'];
        c.joints = Core.placePose(c.pose, size[0] / size[1], c.figure, c.mirror);
        E.emit('character');
    };

    E.closeCharacter = () => {
        if (E.character && E.character.pending) E.discardCharacter();
        E.character = null;
        E.emit('character');
    };

    /* the frame as a part of the view: fractions from the top left */
    E.characterCrop = () => {
        const c = E.character, size = CHARACTER_SIZES[c.aspect] || CHARACTER_SIZES['2:3'];
        const h = c.frame.h, w = h * (size[0] / size[1]) / E.aspect();
        return { x0: c.frame.cx - w / 2, x1: c.frame.cx + w / 2, y0: c.frame.cy - h / 2, y1: c.frame.cy + h / 2 };
    };

    /* the figure as OpenPose draws it: what ControlNet's openpose model expects */
    E.drawPose = (ctx, joints, width, height, thickness) => {
        const stick = thickness || Math.max(2, Math.round(4 * Math.min(width, height) / 512));
        ctx.lineCap = 'round';
        Core.LIMBS.forEach((limb, i) => {
            const a = joints[limb[0]], b = joints[limb[1]];
            if (!a || !b) return;
            const x0 = a[0] * width, y0 = a[1] * height, x1 = b[0] * width, y1 = b[1] * height;
            const c = Core.COLORS[i];
            ctx.save();
            ctx.translate((x0 + x1) / 2, (y0 + y1) / 2);
            ctx.rotate(Math.atan2(y1 - y0, x1 - x0));
            ctx.beginPath();
            ctx.ellipse(0, 0, Math.hypot(x1 - x0, y1 - y0) / 2, stick, 0, 0, Math.PI * 2);
            ctx.fillStyle = `rgb(${Math.round(c[0] * 0.6)}, ${Math.round(c[1] * 0.6)}, ${Math.round(c[2] * 0.6)})`;
            ctx.fill();
            ctx.restore();
        });
        joints.forEach((p, i) => {
            if (!p) return;
            const c = Core.COLORS[i];
            ctx.beginPath(); ctx.arc(p[0] * width, p[1] * height, stick, 0, Math.PI * 2);
            ctx.fillStyle = `rgb(${c[0]}, ${c[1]}, ${c[2]})`; ctx.fill();
        });
    };

    /* where a figure with these joints will be drawn: its body, a little wider than the bones */
    function bodyShape(joints, width, height) {
        const canvas = document.createElement('canvas');
        canvas.width = width; canvas.height = height;
        const ctx = canvas.getContext('2d');
        let top = 1, bottom = 0;
        for (const p of joints) if (p) { top = Math.min(top, p[1]); bottom = Math.max(bottom, p[1]); }
        const tall = Math.max(0.05, bottom - top) * height;
        ctx.strokeStyle = ctx.fillStyle = '#fff'; ctx.lineCap = ctx.lineJoin = 'round';
        const P = i => [joints[i][0] * width, joints[i][1] * height];
        const line = (a, b, thick) => { if (!joints[a] || !joints[b]) return; ctx.lineWidth = thick * tall; ctx.beginPath(); ctx.moveTo(...P(a)); ctx.lineTo(...P(b)); ctx.stroke(); };
        if (joints[2] && joints[5] && joints[8] && joints[11]) {
            ctx.lineWidth = 0.14 * tall;
            ctx.beginPath(); ctx.moveTo(...P(2)); ctx.lineTo(...P(5)); ctx.lineTo(...P(11)); ctx.lineTo(...P(8)); ctx.closePath(); ctx.fill(); ctx.stroke();
        }
        [[2, 3], [3, 4], [5, 6], [6, 7]].forEach(l => line(l[0], l[1], 0.13));
        [[8, 9], [9, 10], [11, 12], [12, 13]].forEach(l => line(l[0], l[1], 0.16));
        line(1, 0, 0.14);
        if (joints[0]) { ctx.beginPath(); ctx.arc(P(0)[0], P(0)[1] - 0.02 * tall, 0.105 * tall, 0, Math.PI * 2); ctx.fill(); }
        for (const i of [4, 7]) if (joints[i]) { ctx.beginPath(); ctx.arc(...P(i), 0.075 * tall, 0, Math.PI * 2); ctx.fill(); }
        for (const i of [10, 13]) if (joints[i]) { ctx.beginPath(); ctx.ellipse(P(i)[0], P(i)[1] + 0.025 * tall, 0.1 * tall, 0.075 * tall, 0, 0, Math.PI * 2); ctx.fill(); }
        return canvas;
    }

    function opaqueMask(shape) {                   // white where the shape is, black elsewhere
        const canvas = document.createElement('canvas');
        canvas.width = shape.width; canvas.height = shape.height;
        const ctx = canvas.getContext('2d');
        ctx.fillStyle = '#000'; ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.drawImage(shape, 0, 0);
        return canvas;
    }

    E.generateCharacter = async () => {
        const c = E.character, world = E.world();
        if (!c || c.busy) return;
        if (c.pending) E.discardCharacter(true);
        const size = CHARACTER_SIZES[c.aspect] || CHARACTER_SIZES['2:3'], width = size[0], height = size[1];
        const cam = { ...E.cam, pos: E.cam.pos.slice(), roll: 0 }, crop = E.characterCrop();
        c.busy = true; E.emit('character');
        try {
            const shot = await E.snapshot(width, height, { cam, crop });
            let shape, pose = null;
            if (c.mode === 'pose') {
                shape = bodyShape(c.joints, width, height);
                const drawing = document.createElement('canvas');
                drawing.width = width; drawing.height = height;
                const ctx = drawing.getContext('2d');
                ctx.fillStyle = '#000'; ctx.fillRect(0, 0, width, height);
                E.drawPose(ctx, c.joints, width, height);
                pose = drawing.toDataURL('image/png');
            } else {
                if (!E.sel.any) throw new Error('Paint or select where to draw first (the selection tools are in the Portals tab)');
                const seen = await E.snapshot(width, height, { cam, crop, output: 'selection' });
                const image = seen.canvas.getContext('2d').getImageData(0, 0, width, height), d = image.data;
                let any = false;
                for (let i = 0, n = width * height; i < n; i++) { const a = d[i * 4]; d[i * 4] = d[i * 4 + 1] = d[i * 4 + 2] = 255; d[i * 4 + 3] = a; any = any || a > 127; }
                if (!any) throw new Error('The selection is outside the frame: move the frame over it');
                shape = document.createElement('canvas'); shape.width = width; shape.height = height;
                shape.getContext('2d').putImageData(image, 0, 0);
            }
            const s = c.settings;
            let prompt = (s.prompt || '').trim();
            if (!prompt) throw new Error('Write a prompt first');
            if (s.lcm && E.options.lora_lcm && !/<lora:LCM/i.test(prompt)) prompt += ` <lora:${E.options.lora_lcm}:1>`;
            E.doc.settings.character = { mode: c.mode, aspect: c.aspect, pose: c.pose, result: c.result, settings: { ...s, seed: -1 } };
            if (c.result !== 'paint') {
                // An object of its own: drawn here, lifted off its background, and stood in the world where
                // the view shows it. The panorama is not touched.
                const id = uid('o');
                const mid = (a, b) => a && b ? [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2] : null;
                const points = c.mode === 'pose' ? [c.joints[1], c.joints[0], mid(c.joints[8], c.joints[11]), c.joints[9], c.joints[12]].filter(Boolean) : null;
                const made = await E.job('object', {
                    project: E.name, world: world.id, object: id, image: shot.canvas.toDataURL('image/png'),
                    mask: opaqueMask(shape).toDataURL('image/png'), pose, points,
                    settings: { ...s, prompt, checkpoint: s.checkpoint || (world.gen && world.gen.checkpoint) || null },
                });
                const object = Object.assign({
                    id, name: '', turn: null, relief: 0.2, opacity: 1, flip: false, history: [],
                    gen: { mode: c.mode, aspect: c.aspect, pose: c.pose, joints: c.mode === 'pose' ? c.joints.map(j => j && j.slice()) : null, settings: { ...s, seed: -1 } },
                }, E.placeObject({ world: world.id, cam, aspect: E.aspect(), crop }, made.box || [0, 0, 1, 1]));
                adoptObject(object, made);
                world.objects = (world.objects || []).concat([object]);
                E.chosen = id;
                if (c.mode === 'mask') E.clearSelection();
                E.character = null;
                E.touch('objects');
                await E.ensure(world.id);
                return object;
            }
            const result = await E.job('character', {
                image: shot.canvas.toDataURL('image/png'), mask: opaqueMask(shape).toDataURL('image/png'), pose,
                settings: { ...s, prompt, checkpoint: s.checkpoint || (world.gen && world.gen.checkpoint) || null },
            });
            // the drawing goes back onto the sphere: only where the mask is, with a soft edge
            const drawn = await bitmapFrom(result.image);
            const patch = document.createElement('canvas');
            patch.width = width; patch.height = height;
            const ctx = patch.getContext('2d');
            ctx.filter = `blur(${Math.max(2, Math.round(width / 100))}px)`;
            ctx.drawImage(shape, 0, 0);
            ctx.filter = 'none';
            ctx.globalCompositeOperation = 'source-in';
            ctx.drawImage(drawn, 0, 0, width, height);
            drawn.close();
            const view = { world: world.id, cam, aspect: E.aspect(), crop };
            const pixels = E.gl.bake(view, patch, world.width, world.height, world.id);
            E.gl.putPixels(world.id, pixels, world.width, world.height);
            c.pending = { pixels, width: world.width, height: world.height, seed: result.seed, infotext: result.infotext, world: world.id };
            E.touch();
        } finally { c.busy = false; E.emit('character'); }
    };

    /* the character stays: the painted panorama becomes the world's next version */
    E.keepCharacter = async () => {
        const c = E.character;
        if (!c || !c.pending) return;
        const p = c.pending, world = E.world(p.world);
        c.busy = true; E.emit('character');
        try {
            const blob = await toBlob(canvasFromPixels(p.pixels, p.width, p.height), 'image/png');
            blob.name = 'painted.png';
            c.pending = null;
            await E.upload(world.id, blob, true, world.infotext);
        } finally { c.busy = false; E.emit('character'); }
    };

    E.discardCharacter = async quiet => {
        const c = E.character;
        if (!c || !c.pending) return;
        const id = c.pending.world;
        c.pending = null;
        const state = E.loaded.get(id); if (state) state.file = '(again)';
        await E.ensure(id);
        if (!quiet) E.emit('character');
    };

    /* ---- the route --------------------------------------------------------------------------------------- */

    E.tour = () => {
        if (!E.doc.tours.length) E.doc.tours.push({ id: uid('t'), name: 'Tour', keys: [] });
        return E.doc.tours[0];
    };

    E.keyHere = () => ({ world: E.worldId, pos: E.cam.pos.map(v => +v.toFixed(3)), yaw: +E.cam.yaw.toFixed(2), pitch: +E.cam.pitch.toFixed(2), fov: +E.cam.fov.toFixed(1), dur: 4, hold: 0 });

    E.addKey = (key, index) => {
        const tour = E.tour();
        key = key || E.keyHere();
        if (!tour.keys.length) key.dur = 0;
        if (index == null) tour.keys.push(key); else tour.keys.splice(index, 0, key);
        E.touch('tour');
        return key;
    };

    /* how the route gets from one world into another: through the first portal that leads there */
    E.link = (from, to) => {
        const world = E.world(from);
        const portal = world && (world.portals || []).find(p => p.target === to);
        if (!portal) return null;
        return { enter: E.doorstep(portal), arrive: E.arrival(portal), turn: E.turnOf(portal), portal, deep: E.deepPortal(portal), via: portal.id };
    };

    E.compile = () => Core.compileTour(E.tour(), E.link);

    /* a moment of the route, kept inside its world: a curve between two keys may cut a corner */
    const inside = m => { if (m) m.pos = Core.constrain(m.pos, E.field(m.world), WALL_SHARE + 0.03).pos; return m; };

    /* ---- a second camera ---------------------------------------------------------------------------------- */

    /* Besides the route (the main camera: often somebody's eyes) a scene may have a second camera that follows
     * one figure: beside it, behind it, before it or going round it (`tour.second`). Cuts (`tour.cuts`, [{t,
     * cam: 'a' | 'b'}]) say which of the two the film shows from each second on. The figure whose eyes the
     * route is (`tour.walker`) is not drawn while the film looks through those eyes, and is seen walking when
     * the second camera looks at it. All of it from the time alone: a recording drawn twice is the same. */
    E.objectById = id => { const world = E.objectWorld(id); return world ? world.objects.find(o => o.id === id) || null : null; };
    E.shotAt = t => (E.doc && E.doc.tours.length ? Core.shotAt(E.tour().cuts, t) : 'a');

    /* where a figure faces at a moment: the way it goes while it goes (over a second, so a step does not turn
     * the camera), otherwise the way it was turned to */
    function facingAt(object, world, t) {
        let x = 0, z = 0;
        for (let i = -3; i <= 3; i++) {
            const a = E.objectPlace({ pos: object.pos, path: object.path }, t + i * 0.15 - 0.1, null).pos, b = E.objectPlace({ pos: object.pos, path: object.path }, t + i * 0.15 + 0.1, null).pos;
            const dx = b[0] - a[0], dz = b[2] - a[2];
            if (Math.hypot(dx, dz) > 0.03 && object.face !== 'fixed') { x += dx; z += dz; }
        }
        if (Math.hypot(x, z) > 1e-6) return Core.anglesFromDir(x, 0, z).yaw;
        return object.turn == null ? 0 : (object.turns || []).length ? turnAt(object, t) : +object.turn;
    }

    /* The second camera at a moment of the scene: following its figure (where it stands and how tall it is
     * there, softened over `soft` seconds either side, so the camera does not shake with every step), in that
     * figure's world, kept inside the world. null when it has no figure to follow. */
    E.secondAt = (t, base, given) => {
        const tour = E.doc && E.doc.tours.length ? E.tour() : null, cut = given !== undefined ? given : tour ? Core.cutAt(tour.cuts, t) : null;
        // (a cut to the second camera may follow its own way: then that way's own distances, on the side it says)
        const second = tour ? tour.second || {} : null;
        // (the panel's own way keeps the panel's distances, lens and size; another way takes that way's own)
        const own = cut && cut.cam === 'b' && Core.FOLLOW_WAYS[cut.way] && cut.way !== (tour.second && tour.second.mode);
        const spec = second ? Object.assign({}, second, own ? Core.FOLLOW_WAYS[cut.way] : null, own ? { mode: cut.way, side: cut.side || second.side } : null, { stand: (cut && cut.cam === 'b' && cut.stand) || second.stand }) : null;
        // (and its own lens: degrees, a wide one to see two people in a small room)
        if (spec && cut && cut.cam === 'b' && +cut.fov > 0) { spec.fov = Core.clamp(+cut.fov, 5, 140); spec.frame = 0; }
        // (and may follow a figure of its own: a shot of one, then of another)
        const target = cut && cut.cam === 'b' && cut.target ? cut.target : spec && spec.target, object = target ? E.objectById(target) : null;
        const world = object ? E.objectWorld(object.id) : null;
        if (!object || !world || !E.field(world.id).n) return null;
        const soft = spec.soft == null ? 0.4 : Core.clamp(+spec.soft, 0, 2), n = soft > 0 ? 9 : 1, feet = [0, 0, 0];
        let tall = 0;
        for (let i = 0; i < n; i++) {
            const at = n > 1 ? t - soft + 2 * soft * i / (n - 1) : t, p = E.objectPlace(object, at, null, world.id), box = object.box || [0, 0, 1, 1];
            for (let j = 0; j < 3; j++) feet[j] += p.pos[j] / n;
            tall += p.size * Math.max(0.1, Math.min(1, box[3]) - box[1]) / n;
        }
        // a camera on a tripod stands where it was put, in its own world: a figure in another world it cannot see
        const tripod = spec.mode === 'tripod' && spec.stand && spec.stand.pos;
        if (tripod && spec.stand.world && spec.stand.world !== world.id) return null;
        const cam = Core.followCam(spec, feet, tall, facingAt(object, world, t), t), field = E.field(world.id), limit = SECOND_SHARE;
        // The world is a picture pushed out by its depth, and the ground is part of it: low down and away from
        // where the picture was taken a camera is soon too near the ground to look right. Then it goes up toward
        // the picture's own eye height (it still sees the figure from the side it was asked for, a little from
        // above), and only then nearer the figure along its line to it. Pulled straight back toward where the
        // picture was taken it would swing round to the figure's face.
        const inside = q => !Core.constrain(q, field, limit).stopped;
        if (!tripod && !inside(cam.pos)) {
            const placed = (x, up) => { const q = cam.look.map((v, j) => v + (cam.pos[j] - v) * x); q[1] += (Math.max(q[1], 0.05) - q[1]) * up; return q; };
            let best = null;
            for (const up of [0, 0.35, 0.7, 1]) {
                let near = 0.15, far = 1;
                if (!inside(placed(near, up))) continue;
                if (inside(placed(far, up))) near = far;
                else for (let i = 0; i < 14; i++) { const x = (near + far) / 2; if (inside(placed(x, up))) near = x; else far = x; }
                if (!best || near > best.x + 0.05) best = { x: near, up };
                if (near >= 0.6) break;
            }
            cam.pos = best ? placed(best.x, best.up) : Core.constrain(cam.pos, field, limit).pos;
            const a = Core.anglesFromDir(cam.look[0] - cam.pos[0], cam.look[1] - cam.pos[1], cam.look[2] - cam.pos[2]);
            cam.yaw = a.yaw; cam.pitch = Core.clamp(a.pitch, -89, 89);
        }
        return Object.assign(cam, { world: world.id, lids: 0, relief: 1, mix: null, travelled: base ? base.travelled || 0 : 0, rig: 'pro', speed: 0, shot: 'b' });
    };

    /* The cuts of a dialogue (Core.dialogueCuts): its spoken lines, when they start and how long they are (a line
     * without its voice yet: as long as its words take, two and a half a second), who says them. */
    E.dialogueCuts = opts => {
        const times = E.lineTimes(), spoken = [];
        for (const line of E.lines()) {
            if (line.kind === 'sound' || line.kind === 'act') continue;
            const words = String(line.text || '').trim().split(/\s+/).filter(Boolean).length, seconds = +line.seconds || words / 2.5;
            if (!(seconds > 0)) continue;
            const start = times.get(line.id) || 0;
            spoken.push({ start, end: start + seconds, speaker: line.object && E.objectById(line.object) ? line.object : null });
        }
        // (the walker, and its copies that are the route's eyes in the other worlds)
        const eyes = [E.tour().walker].concat(E.doc.worlds.flatMap(w => (w.objects || []).filter(o => o.route_eyes).map(o => o.id))).filter(Boolean);
        return Core.dialogueCuts(spoken, eyes, opts);
    };

    /* the second camera's view at a moment, shown now (to set it up) */
    E.lookSecond = t => { const m = E.secondAt(t || 0, null); if (!m) return false; showMoment(m); return true; };

    /* where the route's camera is and looks at a moment (the route compiled once for each state of its keys) */
    let routeLookKept = null;
    function routeLook(t) {
        const sign = JSON.stringify(E.tour().keys);
        if (!routeLookKept || routeLookKept.sign !== sign) routeLookKept = { sign, compiled: E.compile() };
        return routeLookKept.compiled.duration > 0 ? Core.tourAt(routeLookKept.compiled, t) : null;
    }

    /* the moment a cut shows: the route's (the main camera, or no cut yet) or the second camera's */
    const momentOf = (cut, t, base) => (cut && cut.cam === 'b' ? E.secondAt(t, base, cut) || base : base);

    /* The moment the film shows: the route's, or the second camera's while a cut says so; around a cut that
     * goes over softly (Core.passAt) the eyes blink, the picture goes through black, or the other shot is laid
     * over it (`blend`: drawn as a second picture). */
    E.shotMoment = (t, base) => {
        const cuts = E.doc && E.doc.tours.length ? E.tour().cuts : null;
        const m = momentOf(Core.cutAt(cuts, t), t, base), pass = m ? Core.passAt(cuts, t) : null;
        if (!pass) return m;
        const shown = Object.assign({}, m);
        if (pass.lids) shown.lids = Math.max(m.lids || 0, pass.lids);
        if (pass.dark) { shown.fade = Math.max(m.fade || 0, pass.dark); shown.fadeColor = [0, 0, 0]; }
        if (pass.other !== undefined && pass.amount > 0.002) { const other = momentOf(pass.other, t, base); if (other) shown.blend = { moment: other, amount: pass.amount }; }
        return shown;
    };

    /* A moment drawn as a picture of its own (the other shot of a dissolve): its camera carried as the moment
     * says, its world's objects as that shot shows them (the figure whose eyes the route is: not in its own eyes). */
    E.momentState = (m, t, aspect) => {
        const rig = E.rig(m.world), cam = Core.carried(m, m.rig || rig.kind, t, m.travelled || 0, m.rigStrength != null ? m.rigStrength : rig.strength, m.speed);
        const relief = m.relief == null ? 1 : m.relief, mix = m.mix && m.mix.amount > 0 ? m.mix : null, shown = relief * (1 - (mix ? mix.amount : 0));
        const state = {
            world: m.world, cam, relief, lids: m.lids || 0, over: !!E.doc.settings.objects_over, mix, fade: null, guide: 0, selection: false,
            time: t, portals: E.portalStates(m.world, true), aspect,
            objects: E.objectStates(m.world, cam.pos, shown, null, cam, t, m.shot || 'a'), weather: E.weatherState(m.world, shown),
        };
        return relief >= 1 ? E.deepen(state, t) : state;
    };

    /* the worlds a recording of the route needs: the route's own, and the worlds of the figures the second
     * camera follows */
    const routeWorlds = () => {
        const tour = E.tour(), worlds = new Set(tour.keys.filter(k => k && k.world).map(k => k.world));
        for (const cut of tour.cuts || []) {
            if (cut.cam !== 'b') continue;
            const followed = E.objectWorld(cut.target || (tour.second && tour.second.target));
            if (followed) worlds.add(followed.id);
        }
        return worlds;
    };

    /* A figure whose eyes the route is: it walks where the camera goes (on the ground under it), faces where the
     * camera looks while it stands, and is out of sight while the route's camera is in another world. Its path
     * is made of a key every quarter of a second, each straight from the one before. */
    E.walkRoute = async object => {
        const world = E.objectWorld(object.id);
        if (!world) throw new Error('The figure is in no world');
        await E.ensure(world.id);
        await preload(routeWorlds());
        const compiled = E.compile();
        if (compiled.duration <= 0) throw new Error('Add at least two keys to the route');
        const path = [], turns = [];
        let last = null;
        for (let t = 0; t <= compiled.duration + 1e-6; t += 0.25) {
            const m = Core.tourAt(compiled, t), when = +t.toFixed(3);
            if (!m || m.world !== world.id) { if (last) path.push({ t: when, pos: last.slice(), opacity: 0, line: true }); continue; }
            // the place on the map whose ground is drawn under the camera (Core.stood: the world is the map drawn
            // smaller by as much as the picture's depth is nearer than the place)
            let x = m.pos[0], z = m.pos[2];
            for (let i = 0; i < 8; i++) { const stood = E.stand(world.id, x, z, 0); if (!stood || !(stood.small > 0)) break; x = m.pos[0] / stood.small; z = m.pos[2] / stood.small; }
            last = [+x.toFixed(3), 0, +z.toFixed(3)];
            // (out of sight until the route comes into its world: not fading in from the start)
            if (!path.length && when > 0) path.push({ t: 0, pos: last.slice(), opacity: 0 });
            if (path.length === 1 && when > 0.05) path.push({ t: +(when - 0.05).toFixed(3), pos: last.slice(), opacity: 0 });
            path.push(path.length ? { t: when, pos: last.slice(), opacity: 1, line: true } : { t: when, pos: last.slice(), opacity: 1 });
            turns.push({ t: when, turn: +(((m.yaw % 360) + 540) % 360 - 180).toFixed(1), over: 0.25 });
        }
        if (path.length < 2) throw new Error('The route never comes into this figure\'s world');
        object.path = path; object.turns = turns; object.turn = turns[0].turn; object.on_ground = true;
        if (object.face === 'fixed') delete object.face;
        // the first such figure is the one the panel names; one in each other world the route goes through may be too
        if (!E.tour().walker || !E.objectById(E.tour().walker) || E.objectWorld(E.tour().walker) === world) E.tour().walker = object.id;
        else object.route_eyes = true;
        E.touch('objects'); E.touch('tour');
        return object;
    };

    async function preload(worlds) {
        for (const id of worlds) { await E.ensure(id); await E.ensureNeighbours(id); }
    }

    /* ---- versions of the project ------------------------------------------------------------------------- */

    E.versions = () => call('/project/versions', { query: { name: E.name } });

    E.saveVersion = async label => {
        await E.save();
        return call('/project/version/save', { body: { name: E.name, label: label || '' } });
    };

    E.restoreVersion = async file => {
        await E.save();
        await call('/project/version/restore', { body: { name: E.name, file } });
        const name = E.name;
        E.doc = null;                               // what is on the page is not saved over what was restored
        await E.open(name);
    };

    E.deleteVersion = file => call('/project/version/delete', { body: { name: E.name, file } });

    E.duplicateProject = async newName => {
        await E.save();
        const answer = await call('/project/duplicate', { body: { name: E.name, new_name: newName } });
        await E.refreshProjects();
        await E.open(answer.name);
        return answer.name;
    };

    E.packProject = async () => {
        await E.save();
        const answer = await call('/project/pack', { body: { name: E.name } });
        answer.url = `./sim360/export/file/${encodeURIComponent(E.name)}/${encodeURIComponent(answer.file)}`;
        return answer;
    };

    E.unpackProject = async file => {
        const answer = await call('/project/unpack', { raw: file, query: { filename: file.name || 'project.zip' } });
        await E.refreshProjects();
        await E.open(answer.name);
        return answer.name;
    };

    E.playTour = async from => {
        // (the worlds first: a portal knows its way in, and so where its doorstep is, once its world is loaded)
        await preload(routeWorlds());
        const compiled = E.compile();
        if (compiled.duration <= 0) { E.say('Add at least two keys to the route', 'warn'); return; }
        E.saying = null;
        await E.ensureClips();
        E.cross = null; E.auto = null;
        E.play = { kind: 'tour', compiled, t: from || 0, duration: compiled.duration, at: t => E.shotMoment(t, inside(Core.tourAt(compiled, t))) };
        E.emit('play');
    };

    E.playTake = async () => {
        const take = E.take;
        if (!take || !take.samples.length) return;
        E.saying = null;
        await preload(new Set(take.samples.map(s => s.world)));
        await E.ensureClips();
        E.play = { kind: 'take', t: 0, duration: take.duration, at: t => Core.takeAt(take, t) };
        E.emit('play');
    };

    E.stopPlay = () => { if (!E.play) return; E.play = null; E.moment = null; E.cam.roll = 0; E.mix = null; E.relief = 1; E.fade = null; hear(null); tell(null); E.emit('play'); };

    function showMoment(m) {
        if (m.world !== E.worldId) { E.worldId = m.world; E.emit('world'); }
        E.cam = { pos: m.pos.slice(), yaw: m.yaw, pitch: m.pitch, roll: m.roll || 0, fov: m.fov };
        E.moment = m;
        E.fovWanted = m.fov;
        E.relief = m.relief == null ? 1 : m.relief;
        E.mix = m.mix && m.mix.amount > 0 ? m.mix : null;
        E.fade = m.fade ? [...(m.fadeColor || [1, 1, 1]), m.fade] : null;
        E.travelled = m.travelled || 0;
    }

    function stepPlay(dt) {
        const p = E.play;
        p.t += dt;
        const m = p.at(Math.min(p.t, p.duration));
        if (m) showMoment(m);
        hear(p.t); tell(p.t);
        if (p.t >= p.duration) { E.stopPlay(); E.cam.pos = E.cam.pos.slice(); }
    }

    E.seekPlay = t => { const p = E.play; if (!p) return; p.t = Core.clamp(t, 0, p.duration); const m = p.at(p.t); if (m) showMoment(m); hear(p.t); tell(p.t); };

    /* recording the flight as it is flown */
    E.startRecording = () => { E.stopPlay(); E.recording = { started: E.clock, samples: [] }; E.emit('take'); };
    /* The recorded flight as the route's keys (Core.takeToKeys), to change afterwards like any route; the keys
     * that were there are replaced. */
    E.takeToRoute = () => {
        if (!E.take || E.take.samples.length < 2) throw new Error('Record a flight first');
        const keys = Core.takeToKeys(E.take.samples);
        if (keys.length < 2) throw new Error('The flight is too short to become a route');
        E.tour().keys = keys;
        E.touch('tour');
        return keys;
    };

    E.stopRecording = () => {
        const r = E.recording;
        if (!r) return;
        E.recording = null;
        if (r.samples.length > 1) E.take = { samples: r.samples, duration: r.samples[r.samples.length - 1].t };
        E.emit('take');
    };

    /* ---- the dialogue -------------------------------------------------------------------------------------
     * A line is something an object says: words, a voice, the sound that came of them, and a video of the
     * object saying it (talk.py). Lines play on the route's clock: each starts when the one before it has
     * ended, or at a time of its own. While a line plays, its video takes the place of the object's picture. */

    E.lines = () => (E.doc ? (E.doc.lines = E.doc.lines || []) : []);
    E.talk = () => (E.options && E.options.talk) || { live_portrait: false, lip_models: [], voices: [], fps: 25 };

    /* when each line starts, in seconds from the start of the route: Map(line id -> seconds) */
    E.lineTimes = () => {
        const times = new Map();
        let end = 0, first = true;
        for (const line of E.lines()) {
            if (line.kind === 'sound' || line.kind === 'act') { times.set(line.id, Math.max(0, +line.start || 0)); continue; }
            const own = line.start !== null && line.start !== undefined && line.start !== '' && isFinite(+line.start);
            const start = own ? Math.max(0, +line.start) : (first ? 0.6 : end + (line.gap == null ? 0.35 : Math.max(0, +line.gap)));
            times.set(line.id, +start.toFixed(3));
            end = start + (+line.seconds || 0);
            first = false;
        }
        return times;
    };
    E.dialogueEnd = () => { const times = E.lineTimes(); let end = 0; for (const line of E.lines()) end = Math.max(end, times.get(line.id) + (+line.seconds || 0)); return end; };

    // A line's files stay in the world it was first made in (line.world); the one who says it may stand anywhere.
    E.objectWorld = id => E.doc.worlds.find(w => (w.objects || []).some(o => o.id === id)) || null;
    E.lineObject = line => { const world = line.object ? E.objectWorld(line.object) : null; return world ? world.objects.find(o => o.id === line.object) : null; };
    E.speakers = () => E.doc.worlds.flatMap(world => (world.objects || []).map(object => ({ world, object })));
    function lineHome(line) {
        if (!E.world(line.world)) { line.world = (E.objectWorld(line.object) || E.world() || {}).id || null; line.audio = null; line.file = null; }
        if (!line.world) throw new Error('Add a world first');
        return line.world;
    }

    E.addLine = object => {
        const world = (object && E.objectWorld(object.id)) || E.world();
        const voices = E.talk().voices, last = E.lines().filter(l => object && l.object === object.id).pop();
        const hebrew = voices.find(v => /^he/i.test(v.language));
        const line = {
            id: uid('l'), world: world ? world.id : null, object: object ? object.id : null, text: '', subtitle: '',
            voice: last ? last.voice : ((S.hebrew && S.hebrew() && hebrew) || voices[0] || {}).id || '',
            rate: last ? last.rate : 0, pitch: last ? last.pitch : 0, start: null, gap: 0.35, volume: 1,
            liveliness: last ? last.liveliness : 0.5, mood: last ? last.mood : 0, lips: last ? last.lips : '',
            audio: null, own_audio: false, seconds: 0, file: null,
        };
        E.lines().push(line);
        E.touch('lines');
        return line;
    };

    E.removeLine = line => {
        E.doc.lines = E.lines().filter(l => l !== line);
        dropClip(line.id);
        E.touch('lines');
    };

    E.moveLine = (line, by) => {
        const lines = E.lines(), at = lines.indexOf(line), to = at + by;
        if (at < 0 || to < 0 || to >= lines.length) return;
        lines.splice(at, 1); lines.splice(to, 0, line);
        E.touch('lines');
    };

    function dropClip(id) {
        const clip = E.clips.get(id);
        if (!clip) return;
        for (const media of [clip.video, clip.audio]) if (media) { media.pause(); media.removeAttribute('src'); media.load(); }
        E.clips.delete(id);
    }

    /* a line's sound and video, ready to play */
    async function loadClip(line) {
        const world = E.world(line.world);
        if (!world || (!line.audio && !line.file)) { dropClip(line.id); return null; }
        const signature = (line.audio || '') + '|' + (line.file || '');
        let clip = E.clips.get(line.id);
        if (clip && clip.signature === signature) { clip.line = line; return clip; }
        dropClip(line.id);
        clip = { signature, line, video: null, audio: null };
        if (line.audio) { clip.audio = new Audio(); clip.audio.preload = 'auto'; clip.audio.src = E.fileUrl(world, line.audio); }
        if (line.file) {
            const video = document.createElement('video');
            video.muted = true; video.loop = false; video.playsInline = true; video.preload = 'auto';
            video.src = E.fileUrl(world, line.file);
            await Promise.race([new Promise(resolve => { video.addEventListener('loadeddata', resolve, { once: true }); video.addEventListener('error', resolve, { once: true }); }), E.sleep(8000)]);
            if (video.readyState >= 2) clip.video = video;
        }
        E.clips.set(line.id, clip);
        return clip;
    }

    E.ensureClips = async () => {
        const wanted = new Set(E.lines().map(l => l.id));
        for (const id of Array.from(E.clips.keys())) if (!wanted.has(id)) dropClip(id);
        for (const line of E.lines()) { try { await loadClip(line); } catch (e) { console.warn('[Simulation 360] a line could not be loaded', line.id, e); } }
    };

    /* Who is saying what at time t of the dialogue (null: nobody). Fills E.said: object id -> its line's video. */
    function tell(t) {
        E.said.clear();
        if (!E.doc) return;
        if (E.saying) {
            const clip = E.clips.get(E.saying.line.id);
            if (clip && clip.video && E.saying.line.object) E.said.set(E.saying.line.object, { video: clip.video, at: E.saying.t, crop: E.saying.line.crop || null });
            return;
        }
        if (t == null) return;
        const times = E.lineTimes();
        for (const line of E.lines()) {
            const clip = E.clips.get(line.id);
            if (!clip || !clip.video || !line.object) continue;
            const at = t - times.get(line.id), seconds = +line.seconds || 0;
            // a piece of acting may hold its last look for a while after it ends
            if (at >= 0 && at < seconds + (+line.hold || 0)) E.said.set(line.object, { video: clip.video, at: Math.min(at, seconds - 0.05), crop: line.crop || null });
        }
    }

    /* The sound and the videos of the lines keep to the clock while the route plays (or one line is tried). */
    function hear(t) {
        const times = t == null ? null : E.lineTimes();
        for (const [id, clip] of E.clips) {
            let at = null;
            if (E.saying) at = E.saying.line.id === id ? E.saying.t : null;
            else if (times && times.has(id)) { const a = t - times.get(id); if (a >= 0 && a < (+clip.line.seconds || 0)) at = a; }
            for (const media of [clip.video, clip.audio]) {
                if (!media) continue;
                if (at == null) { if (!media.paused) media.pause(); continue; }
                if (media === clip.audio) media.volume = Core.clamp(clip.line.volume == null ? 1 : +clip.line.volume, 0, 1);
                if (Math.abs(media.currentTime - at) > 0.14) media.currentTime = at;
                if (media.paused) media.play().catch(() => {});
            }
        }
    }

    /* Try one line now: the object says it where it stands. */
    E.sayLine = async line => {
        E.stopPlay();
        const clip = await loadClip(line);
        if (!clip || !clip.audio) throw new Error('This line has no sound yet');
        E.saying = { line, t: 0 };
        E.emit('lines');
    };
    E.hush = () => { if (!E.saying) return; E.saying = null; hear(null); tell(null); E.emit('lines'); };

    /* The words of a line become sound, in the chosen voice. */
    E.voiceLine = async line => {
        if (!(line.text || '').trim()) throw new Error('Write what the character says first');
        if (!line.voice) throw new Error('Choose a voice first');
        const result = await E.job('speech', { project: E.name, world: lineHome(line), line: line.id, text: line.text, voice: line.voice, rate: +line.rate || 0, pitch: +line.pitch || 0 });
        Object.assign(line, { audio: result.audio, seconds: result.seconds, own_audio: false, file: null, spoken: [line.text, line.voice, +line.rate || 0, +line.pitch || 0].join('|') });
        E.touch('lines');
        await loadClip(line);
        return line;
    };

    /* A sound file of the user's own becomes the line's sound. */
    E.uploadLineAudio = async (line, file) => {
        const result = await call('/speech-audio', { raw: file, query: { project: E.name, world: lineHome(line), line: line.id, filename: file.name || 'line.wav' } });
        Object.assign(line, { audio: result.audio, seconds: result.seconds, own_audio: true, file: null, spoken: null });
        E.touch('lines');
        await loadClip(line);
        return line;
    };

    /* A sound of the scene: music, a telephone that rings, rain. It plays from its own second, as it is. */
    E.addSound = async (file, name) => {
        const world = E.world();
        if (!world) throw new Error('Add a world first');
        const line = { id: uid('l'), kind: 'sound', name: name || (file.name || 'sound').replace(/\.[^.]+$/, '').slice(0, 40), world: world.id, object: null, text: '', start: 0, volume: 0.8, audio: null, own_audio: true, seconds: 0, file: null };
        const result = await call('/speech-audio', { raw: file, query: { project: E.name, world: world.id, line: line.id, filename: file.name || 'sound.wav', plain: 'true' } });
        Object.assign(line, { audio: result.audio, seconds: result.seconds });
        E.lines().push(line);
        E.touch('lines');
        await loadClip(line);
        return line;
    };

    /* Acting without words: the object raises its head, stares, smiles, laughs. `beats` are moments of the
     * act: [{t, head, eyes, smile, laugh, turn}] (see talk.py). */
    E.actLine = async (object, beats, seconds, options) => {
        const stage = E.objectWorld(object.id);
        let line = (options && options.line) || null;
        if (!line) { line = { id: uid('l'), kind: 'act', world: stage.id, object: object.id, text: '', start: 0, hold: 0, audio: null, seconds: 0, file: null }; E.lines().push(line); }
        Object.assign(line, { act: beats, start: options && options.start != null ? options.start : line.start, hold: options && options.hold != null ? options.hold : line.hold });
        const result = await E.job('talk', {
            project: E.name, world: stage.id, object: object.id, picture: object.kind === 'video' ? object.poster : object.file,
            line: line.id, line_world: lineHome(line), act: beats, seconds, settings: { liveliness: options && options.liveliness != null ? options.liveliness : 0.3, seed: (options && options.seed) || 0, side: (options && options.side) || 0 },
        });
        Object.assign(line, { file: result.file, seconds: result.seconds, frames: result.frames, fps: result.fps });
        E.touch('lines');
        await loadClip(line);
        return line;
    };

    /* whether the words or the voice changed since the sound was made */
    E.lineStale = line => !line.own_audio && !!line.audio && line.spoken !== [line.text, line.voice, +line.rate || 0, +line.pitch || 0].join('|');

    /* An object says what is in a sound file: a new line of its own, the file as its sound, its lips to it. */
    E.speakFile = async (object, file) => {
        const line = E.addLine(object);
        line.text = (file.name || '').replace(/\.[^.]+$/, '').slice(0, 80);
        await E.uploadLineAudio(line, file);
        await E.talkLine(line);
        return line;
    };

    /* The object says the line: its face moves to the sound. */
    E.talkLine = async line => {
        const object = E.lineObject(line);
        if (!object) throw new Error('Choose who says this line');
        if (!E.world(line.world) || !line.audio || E.lineStale(line)) await E.voiceLine(line);
        const solid = !!(object.model && !object.flat);
        if (solid) await E.ensure(E.objectWorld(object.id).id);
        const crop = solid ? E.headCrop(object) : null;         // a solid figure is filmed from close: its head only
        const result = await E.job('talk', {
            project: E.name, world: E.objectWorld(object.id).id, object: object.id, picture: object.kind === 'video' ? object.poster : object.file,
            line: line.id, line_world: line.world, audio: line.audio, crop, settings: { lips: line.lips || '', liveliness: (line.liveliness == null ? 0.5 : +line.liveliness) * (object.model && !object.flat ? 0.12 : 1), mood: +line.mood || 0, seed: +line.seed || 0, side: +line.side || 0 },
        });
        Object.assign(line, { file: result.file, seconds: result.seconds, frames: result.frames, fps: result.fps, lips_used: result.lips, seed: result.seed, crop: result.crop || null, made_for: object.kind === 'video' ? object.poster : object.file });
        E.touch('lines');
        await loadClip(line);
        if (result.note) E.say(result.note, 'warn');
        return line;
    };

    /* The object stays alive between its lines. One video of it is made through the whole stretch, from a
     * little before its first line to a little after its last: it says each line where the line falls and,
     * between them, it listens (breathes, blinks, its head moves a little), its mouth at rest but drawn by
     * the same lip syncer as when it speaks. A face that changes its skin every time it falls silent, and
     * then stands frozen, is a puppet's. The video is a line of its own (kind 'act', `presence`) that lies
     * over the object's speaking lines; their sound stays theirs. */
    E.keepAlive = async (object, options) => {
        options = options || {};
        const times = E.lineTimes();
        const spoken = E.lines().filter(l => l.object === object.id && l.kind !== 'sound' && l.kind !== 'act' && l.audio && !E.lineStale(l));
        if (!spoken.length) throw new Error('This one has no line with a voice yet');
        const first = Math.min(...spoken.map(l => times.get(l.id))), last = Math.max(...spoken.map(l => times.get(l.id) + (+l.seconds || 0)));
        const start = Math.max(0, options.from != null ? +options.from : first - 1.2), end = Math.max(start + 1, options.to != null ? +options.to : last + 1.6);
        let line = E.lines().find(l => l.kind === 'act' && l.presence && l.object === object.id);
        if (line) E.doc.lines.splice(E.doc.lines.indexOf(line), 1);
        else line = { id: uid('l'), kind: 'act', presence: true, object: object.id, text: '', hold: 0, audio: null, seconds: 0, file: null };
        const stage = E.objectWorld(object.id), solid = !!(object.model && !object.flat), lead = spoken[0];
        line.world = stage.id;
        E.lines().push(line);                           // (the last of the lines: it lies over the ones it holds)
        if (solid) await E.ensure(stage.id);
        const result = await E.job('talk', {
            project: E.name, world: stage.id, object: object.id, picture: object.kind === 'video' ? object.poster : object.file,
            line: line.id, line_world: line.world, seconds: +(end - start).toFixed(3), crop: solid ? E.headCrop(object) : null,
            lines: spoken.map(l => ({ audio: l.audio, world: l.world, at: +(times.get(l.id) - start).toFixed(3) })),
            settings: { lips: lead.lips || '', liveliness: (lead.liveliness == null ? 0.5 : +lead.liveliness) * (solid ? 0.12 : 1), mood: +lead.mood || 0, seed: +line.seed || 0, side: +lead.side || 0 },
        });
        Object.assign(line, { start: +start.toFixed(3), file: result.file, seconds: result.seconds, frames: result.frames, fps: result.fps, crop: result.crop || null, lips_used: result.lips, seed: result.seed,
            holds: spoken.map(l => l.id + '@' + times.get(l.id).toFixed(2) + ':' + l.audio).join('|') });
        E.touch('lines');
        await loadClip(line);
        if (result.note) E.say(result.note, 'warn');
        return line;
    };

    /* whether what an object's stretch was made of has changed since (a line moved, or was said again) */
    E.aliveStale = line => {
        const times = E.lineTimes();
        return E.lines().filter(l => l.object === line.object && l.kind !== 'sound' && l.kind !== 'act' && l.audio).map(l => l.id + '@' + times.get(l.id).toFixed(2) + ':' + l.audio).join('|') !== line.holds;
    };

    /* everyone who speaks stays alive between their lines, one after the other */
    E.keepAllAlive = async () => {
        const speakers = [];
        for (const line of E.lines()) if (line.object && line.kind !== 'sound' && line.kind !== 'act' && line.audio && !speakers.includes(line.object)) speakers.push(line.object);
        for (const id of speakers) {
            const object = E.objectById ? E.objectById(id) : null, held = E.lines().find(l => l.kind === 'act' && l.presence && l.object === id);
            const found = object || (E.doc.worlds.flatMap(w => w.objects || []).find(o => o.id === id));
            if (!found || (held && held.file && !E.aliveStale(held))) continue;
            await E.keepAlive(found);
        }
    };

    /* every line that has no video yet, or whose words changed, one after the other */
    E.talkAll = async () => {
        for (const line of E.lines().slice()) {
            const object = E.lineObject(line);
            if (!object || !(line.text || '').trim() && !line.audio) continue;
            const picture = object.kind === 'video' ? object.poster : object.file;
            if (line.file && !E.lineStale(line) && line.made_for === picture) continue;
            await E.talkLine(line);
        }
    };

    /* The object comes alive: its picture becomes a short loop in which it looks about and blinks. */
    E.enliven = async (object, seconds) => {
        const world = E.world();
        const result = await E.job('talk', {
            project: E.name, world: world.id, object: object.id, picture: object.kind === 'video' ? object.poster : object.file,
            idle: seconds || 6, settings: { liveliness: 0.5, mood: 0 },
        });
        const kept = { depth: object.depth, box: object.box, seed: object.seed, infotext: object.infotext };
        adoptObject(object, Object.assign({}, kept, result));
        E.touch('objects');
        await E.ensure(world.id);
        return object;
    };

    /* what the recorder is told to put under the picture: the lines' sounds, and the words as subtitles */
    E.dialogueTracks = duration => {
        const times = E.lineTimes(), audio = [], subtitles = [];
        for (const line of E.lines()) {
            const start = times.get(line.id), seconds = +line.seconds || 0;
            if (!line.audio || start >= duration) continue;
            // (a sound of the scene is not speech: it may be told to step back while somebody speaks)
            audio.push({ world: line.world, file: line.audio, start, gain: line.volume == null ? 1 : +line.volume, speech: line.kind !== 'sound', seconds });
            const words = line.kind === 'sound' ? '' : (line.subtitle || line.text || '').trim();
            if (words) subtitles.push({ start: start + 0.05, end: Math.min(duration, start + seconds - 0.05), text: words });
        }
        return { audio, subtitles };
    };

    /* ---- the recorder ------------------------------------------------------------------------------------ */

    function seek(video, time, once) {
        return new Promise(resolve => {
            const span = video.duration || 0;
            // a looping video wraps round; a line's video is played once and holds its last frame
            const target = span <= 0 ? 0 : once ? Core.clamp(time, 0, Math.max(0, span - 0.02)) : ((time % span) + span) % span;
            if (Math.abs(video.currentTime - target) < 1e-4) { resolve(); return; }
            let finished = false;
            const done = () => { if (!finished) { finished = true; video.removeEventListener('seeked', done); resolve(); } };
            video.addEventListener('seeked', done);
            video.currentTime = target;
            E.sleep(2500).then(done);          // a video that will not answer does not stop the recording
        });
    }

    /* Draws a flight frame by frame at the asked size and sends each frame to be encoded.
     * options: {source: 'tour' | 'take', width, height, fps, kind, quality, supersample, name, from, to}
     * (`from` and `to`, in seconds, record a part of the flight: a part is recorded without its sound) */
    E.exportVideo = async (options, onProgress) => {
        if (E.exporting) throw new Error('A recording is already being made');
        let at, duration;
        if (options.source === 'take') {
            if (!E.take) throw new Error('Record a flight first');
            at = t => Core.takeAt(E.take, t); duration = E.take.duration;
            await preload(new Set(E.take.samples.map(s => s.world)));
        } else {
            // (the worlds first: a portal knows its way in, and so where its doorstep is, once its world is loaded)
            await preload(routeWorlds());
            const compiled = E.compile();
            if (compiled.duration <= 0) throw new Error('Add at least two keys to the route');
            at = t => E.shotMoment(t, inside(Core.tourAt(compiled, t))); duration = compiled.duration;
        }
        const fps = +options.fps || 30, scale = Core.clamp(+options.supersample || 1, 1, 3);
        const width = Math.round(options.width), height = Math.round(options.height);
        let drawW = Math.round(width * scale), drawH = Math.round(height * scale);
        const limit = Math.min(E.gl.maxSize, 8192);
        if (Math.max(drawW, drawH) > limit) { const k = limit / Math.max(drawW, drawH); drawW = Math.floor(width * scale * k); drawH = Math.floor(height * scale * k); }
        const begin = Core.clamp(+options.from || 0, 0, duration), end = Core.clamp(options.to == null ? duration : +options.to, begin, duration);
        const part = begin > 0 || end < duration;
        const total = Math.max(1, Math.ceil((end - begin) * fps)), rig = E.rig();
        await E.ensureClips();
        const spoken = options.sound === false || part ? { audio: [], subtitles: [] } : E.dialogueTracks(duration);
        const session = await call('/export/start', { body: {
            project: E.name, name: options.name || 'tour', width, height, fps, kind: options.kind || 'mp4-h264', quality: +options.quality || 16, source_width: drawW, source_height: drawH,
            subtitles: options.subtitles === 'none' ? [] : spoken.subtitles, burn_subtitles: options.subtitles === 'burn',
        } });
        const weatherHeard = [];           // how much of each kind of weather is around the camera, frame by frame
        const wider = Core.clamp(+options.fov_scale || 1, 0.5, 2);      // an upright video may want to see a little more
        const saved = { world: E.worldId, cam: { ...E.cam, pos: E.cam.pos.slice() }, relief: E.relief, mix: E.mix, fade: E.fade, travelled: E.travelled };
        E.stopPlay(); E.hush();
        E.exporting = { id: session.id, cancel: false, frame: 0, total, started: performance.now() };
        for (const video of E.videos.values()) video.pause();
        for (const have of E.objectsLoaded.values()) if (have.video) have.video.pause();
        for (const clip of E.clips.values()) for (const media of [clip.video, clip.audio]) if (media) media.pause();
        E.emit('export');
        const buffer = new Uint8Array(drawW * drawH * 4);
        // Frames go to the encoder exactly as drawn. Some browsers send large uploads slowly (measured: 14 MB
        // a second in an embedded pane, against 280 from a plain client); then each frame is packed as a 97%
        // JPEG on the way, which after the encoder cannot be told apart and is several times faster.
        let packed = options.transport === 'packed', slow = 0, packer = null;
        const pack = () => {
            if (!packer) {
                const canvas = document.createElement('canvas');
                canvas.width = drawW; canvas.height = drawH;
                packer = { canvas, ctx: canvas.getContext('2d'), image: new ImageData(new Uint8ClampedArray(buffer.buffer), drawW, drawH) };
            }
            packer.ctx.putImageData(packer.image, 0, 0);
            // packed at once, as text: the way that answers later (convertToBlob) takes a full second for every
            // frame on a page that is not shown
            return packer.canvas.toDataURL('image/jpeg', 0.97);
        };
        try {
            for (let i = 0; i < total; i++) {
                if (E.exporting.cancel) throw new Error('cancelled');
                const t = Math.min(duration, begin + i / fps), m = at(t);
                const state = {
                    world: m.world, cam: Core.carried(m, m.rig || E.rig(m.world).kind, t, m.travelled || 0, m.rigStrength != null ? m.rigStrength : E.rig(m.world).strength, m.speed), relief: m.relief == null ? 1 : m.relief, lids: m.lids || 0, over: !!E.doc.settings.objects_over,
                    mix: m.mix && m.mix.amount > 0 ? m.mix : null, fade: m.fade ? [...(m.fadeColor || [1, 1, 1]), m.fade] : null, guide: 0, selection: false,
                    time: t, portals: E.portalStates(m.world, true), aspect: width / height,
                };
                tell(t);
                for (const speaking of E.said.values()) await seek(speaking.video, speaking.at, true);
                if (wider !== 1) state.cam.fov = Core.clamp(state.cam.fov * wider, 5, 150);
                state.objects = E.objectStates(m.world, state.cam.pos, state.relief * (1 - (state.mix ? state.mix.amount : 0)), null, state.cam, t, m.shot || 'a');
                state.weather = E.weatherState(m.world, state.relief * (1 - (state.mix ? state.mix.amount : 0)));
                weatherHeard.push(state.weather && state.weather.kind ? [state.weather.kind, state.weather.amount] : null);
                if (state.relief >= 1) E.deepen(state, t);
                // the other shot of a dissolve
                if (m.blend) {
                    const other = E.momentState(m.blend.moment, t, width / height);
                    if (wider !== 1) other.cam.fov = Core.clamp(other.cam.fov * wider, 5, 150);
                    state.blend = { state: other, amount: m.blend.amount };
                }
                // everything that is drawn: this world and the worlds behind its portals (and the other shot's)
                const drawn = [], gather = one => { drawn.push(one); for (const behind of one.through || []) gather(behind.state); };
                gather(state);
                if (state.blend) gather(state.blend.state);
                const moving = new Set();
                if (state.mix) moving.add(state.mix.world);
                for (const one of drawn) { moving.add(one.world); for (const portal of one.portals) if (portal.see && portal.world) moving.add(portal.world); }
                // a moving world's own time 0 can be set to fall on a moment of the recording ("video delay")
                for (const id of moving) { const video = E.videos.get(id), w = E.world(id); if (video) await seek(video, t - (w && +w.video_delay || 0)); }
                for (const one of drawn) for (const item of one.objects) { const have = E.objectsLoaded.get(item.id); if (have && have.video) await seek(have.video, t); }
                E.gl.capture(state, drawW, drawH, buffer);
                const sent = performance.now();
                const response = await fetch(`./sim360/export/frame?id=${session.id}`, { method: 'POST', body: packed ? pack() : buffer, headers: { 'Content-Type': packed ? 'text/plain' : 'application/octet-stream' } });
                if (!response.ok) { let detail = response.statusText; try { detail = (await response.json()).detail; } catch (e) { /* */ } throw new Error(detail); }
                if (!packed && options.transport !== 'exact' && i >= 1 && i < 10) {
                    if (buffer.length / 1048576 / Math.max(0.001, (performance.now() - sent) / 1000) < 25) slow++;
                    if (slow >= 3) { packed = true; E.exporting.packed = true; }
                }
                E.exporting.frame = i + 1;
                if (onProgress) onProgress(i + 1, total);
                if (i % 4 === 3) { E.emit('export'); await E.sleep(0); }
            }
            // stretches of rain or snow become stretches of their sound
            const ambience = [];
            if (options.sound !== false && options.weather_sound !== false && !part) {
                let open = null;
                weatherHeard.concat([null]).forEach((heard, i) => {
                    if (heard && open && open.kind === heard[0]) { open.sum += heard[1]; open.n++; return; }
                    if (open) { ambience.push({ kind: open.kind, start: open.from / fps, end: i / fps, gain: Math.min(1.5, 0.35 + open.sum / open.n) }); open = null; }
                    if (heard) open = { kind: heard[0], from: i, sum: heard[1], n: 1 };
                });
            }
            const result = await call('/export/finish', { body: {
                id: session.id, project: E.name, audio: spoken.audio, ambience,
                // as loud as video sites play things, unless told otherwise; and how far sounds step back under speech
                loudness: options.loudness === false ? null : (typeof options.loudness === 'number' ? options.loudness : -14), duck: +options.duck || 0,
            } });
            result.url = `./sim360/export/file/${encodeURIComponent(E.name)}/${encodeURIComponent(result.path.split(/[\\/]/).pop())}?t=${Date.now()}`;
            result.duration = duration; result.fps = fps; result.packed = packed;
            return result;
        } catch (e) {
            await call('/export/cancel', { body: { id: session.id } }).catch(() => {});
            throw e;
        } finally {
            E.exporting = null;
            E.worldId = saved.world; E.cam = saved.cam; E.relief = saved.relief; E.mix = saved.mix; E.fade = saved.fade; E.travelled = saved.travelled;
            tell(null);
            for (const video of E.videos.values()) video.play().catch(() => {});
            for (const have of E.objectsLoaded.values()) if (have.video) have.video.play().catch(() => {});
            E.emit('export'); E.emit('world');
        }
    };
})();

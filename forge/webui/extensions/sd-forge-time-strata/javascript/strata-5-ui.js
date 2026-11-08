/* Time Strata - the page: the bar, the 3D view with its timeline, and the panels. Everything is drawn here,
 * inside the one empty block the tab holds; nothing of Gradio's is used.
 */
(function () {
    'use strict';
    const Core = window.StrataCore, S = window.Strata, E = S.engine, T = S.T;

    const UI = S.ui = { root: null, tab: 'chains', current: null, files: null, busy: null, message: null, preview: 0.75,
                        started: false, needsDraw: true, worlds: null };

    /* ---- building blocks ---- */

    function h(tag, props, ...children) {
        const el = document.createElement(tag);
        for (const [key, value] of Object.entries(props || {})) {
            if (value == null || value === false) continue;
            if (key === 'class') el.className = value;
            else if (key === 't') el.textContent = T(value);
            else if (key === 'tt') el.title = T(value);
            else if (key === 'on') for (const [event, fn] of Object.entries(value)) el.addEventListener(event, fn);
            else if (key === 'style') el.style.cssText = value;
            else if (key in el && key !== 'list') el[key] = value;
            else el.setAttribute(key, value);
        }
        for (const child of children.flat()) if (child != null && child !== false) el.append(child.nodeType ? child : document.createTextNode(child));
        return el;
    }

    function guarded(fn) {
        return async event => {
            try { await fn(event); } catch (e) {
                const text = String(e && e.message || e);
                if (text === 'cancelled') say(T('Cancelled'), 'warn'); else say(T(text), 'error');
                console.warn('[Time Strata]', e);
            }
        };
    }

    /* children into a parent, leaving out the empty ones (append would write "null" for them) */
    const put = (parent, ...children) => parent.append(...children.flat().filter(c => c != null && c !== false));

    const button = (text, onClick, extra) => h('button', Object.assign({ type: 'button', class: 'ts-btn', t: text, on: { click: guarded(onClick) } }, extra || {}));
    const field = (label, control, hint) => h('label', { class: 'ts-field', tt: hint }, h('span', { class: 'ts-label', t: label }), control);
    const row = (...children) => h('div', { class: 'ts-row' }, ...children);
    const section = (title, open, ...children) => h('details', { class: 'ts-section', open: !!open }, h('summary', { t: title }), h('div', { class: 'ts-section-body' }, ...children));
    const note = (text, ...values) => h('div', { class: 'ts-note' }, T(text, ...values));

    function changed(after, value) { E.touch(); UI.needsDraw = true; if (after) after(value); }

    function number(object, key, options, after) {
        options = options || {};
        const input = h('input', { type: 'number', class: 'ts-input', value: object[key] == null ? '' : object[key], min: options.min, max: options.max, step: options.step || 'any', placeholder: options.placeholder || '' });
        input.addEventListener('change', () => {
            if (input.value === '' && options.empty !== undefined) { object[key] = options.empty; changed(after, options.empty); return; }
            let value = parseFloat(input.value);
            if (!isFinite(value)) value = options.fallback != null ? options.fallback : 0;
            if (options.min != null) value = Math.max(options.min, value);
            if (options.max != null) value = Math.min(options.max, value);
            if (options.scale) value /= options.scale;
            object[key] = value; input.value = options.scale ? +(value * options.scale).toFixed(4) : value;
            changed(after, value);
        });
        if (options.scale && object[key] != null) input.value = +(object[key] * options.scale).toFixed(4);
        return input;
    }

    function slider(object, key, options, after) {
        const shown = h('span', { class: 'ts-value' }, String(object[key]));
        const input = h('input', { type: 'range', class: 'ts-range', min: options.min, max: options.max, step: options.step || 0.01, value: object[key] });
        input.addEventListener('input', () => {
            object[key] = parseFloat(input.value); shown.textContent = String(object[key]);
            E.touch(); UI.needsDraw = true; if (after) after(object[key]);
        });
        return h('span', { class: 'ts-slider' }, input, shown);
    }

    function select(object, key, choices, after) {
        const el = h('select', { class: 'ts-input' });
        for (const choice of choices) {
            const [value, label] = Array.isArray(choice) ? choice : [choice, choice];
            el.append(h('option', { value: String(value), selected: String(object[key]) === String(value) }, T(String(label))));
        }
        el.addEventListener('change', () => {
            const original = choices.map(c => Array.isArray(c) ? c[0] : c).find(v => String(v) === el.value);
            object[key] = original;
            changed(after, original);
        });
        return el;
    }

    function check(object, key, after) {
        const el = h('input', { type: 'checkbox', class: 'ts-check', checked: !!object[key] });
        el.addEventListener('change', () => { object[key] = el.checked; changed(after, el.checked); });
        return el;
    }

    function colour(object, key, after) {
        const el = h('input', { type: 'color', class: 'ts-colour', value: object[key] || '#888888' });
        el.addEventListener('input', () => { object[key] = el.value; E.touch(); UI.needsDraw = true; if (after) after(el.value); });
        return el;
    }

    function text(object, key, after, placeholder) {
        const el = h('input', { type: 'text', class: 'ts-input', value: object[key] || '', placeholder: placeholder ? T(placeholder) : '' });
        el.addEventListener('change', () => { object[key] = el.value; changed(after, el.value); });
        return el;
    }

    function say(message, kind) {
        UI.message = { text: message, kind: kind || 'info', at: Date.now() };
        const bar = UI.root && UI.root.querySelector('.ts-message');
        if (bar) { bar.textContent = message; bar.className = 'ts-message ts-' + (kind || 'info'); }
    }

    const fmt = (v, d) => (v == null || !isFinite(v)) ? '-' : (+v).toFixed(d == null ? 2 : d);

    /* ---- the page ---- */

    function build() {
        const host = document.getElementById('strata_host');
        if (!host) return false;
        const mount = host.querySelector('.strata-mount') || host;
        mount.innerHTML = '';
        const root = UI.root = h('div', { class: 'ts-app hebrew-ui-skip', dir: S.hebrew() ? 'rtl' : 'ltr' });
        mount.append(root);
        root.append(topBar(), h('div', { class: 'ts-main' }, h('div', { class: 'ts-panel' }), stage()), h('div', { class: 'ts-message' }));
        renderPanel();
        return true;
    }

    function topBar() {
        const projects = h('select', { class: 'ts-input ts-projects' });
        const fill = async () => {
            const list = (await E.api('projects')).projects;
            projects.innerHTML = '';
            if (!list.length) projects.append(h('option', { value: '' }, T('(no project yet)')));
            for (const p of list) projects.append(h('option', { value: p.name, selected: p.name === E.project }, p.name));
        };
        fill().catch(() => null);
        UI.fillProjects = fill;
        projects.addEventListener('change', guarded(async () => { if (projects.value) { await E.open(projects.value); renderPanel(); } }));
        return h('div', { class: 'ts-bar' },
            h('span', { class: 'ts-title', t: 'Time Strata' }),
            field('Project', projects),
            button('New project', async () => {
                const name = prompt(T('Name of the new project:'), T('My strata'));
                if (!name) return;
                await E.create(name); await fill(); UI.current = null; renderPanel();
            }),
            button('Open the project folder', async () => { await E.api('open-folder', { project: E.project || '' }); }),
            h('span', { class: 'ts-saved' }));
    }

    function stage() {
        const canvas = h('canvas', { class: 'ts-canvas' });
        const time = h('input', { type: 'range', class: 'ts-time', min: 0, max: 1000, step: 1, value: 0 });
        const clock = h('span', { class: 'ts-clock' }, '0.00');
        const play = h('button', { type: 'button', class: 'ts-btn ts-play' }, '▶');
        const info = h('div', { class: 'ts-info' });
        play.addEventListener('click', () => { E.playing = !E.playing; play.textContent = E.playing ? '❚❚' : '▶'; UI.lastTick = performance.now(); });
        time.addEventListener('input', () => { if (!E.doc) return; E.time = time.value / 1000 * E.doc.shot.duration; UI.needsDraw = true; });
        const quality = h('select', { class: 'ts-input ts-quality', tt: 'How sharp the view is drawn while working (recording always draws at full size)' },
            ...[[0.5, 'View: fast'], [0.75, 'View: good'], [1, 'View: sharp']].map(([v, l]) => h('option', { value: v, selected: v === UI.preview }, T(l))));
        quality.addEventListener('change', () => { UI.preview = +quality.value; fit(); });
        const joystick = makeJoystick();
        const wrap = h('div', { class: 'ts-stage-view' }, canvas, h('div', { class: 'ts-overlay' }),
                       h('div', { class: 'ts-live-badge' }), h('div', { class: 'ts-steer-hint' }), joystick);
        const steer = h('button', { type: 'button', class: 'ts-btn ts-steer', tt: 'Steer the trail: while the chain plays (or Deforum draws it), the arrows or W A S D, the joystick or DeforumationQT turn it and make it climb; the camera follows' }, '🎮 ' + T('Steer'));
        steer.addEventListener('click', guarded(() => {
            if (E.steering.on) { endSteering(); return; }
            E.startSteering();
            // steering moves the trail as its head moves: the shot plays from where it is (Deforum's frames move it by themselves)
            if (!E.playing && !E.live) { E.playing = true; UI.playButton.textContent = '❚❚'; UI.lastTick = performance.now(); }
        }));
        const back = button('Back to the keys', () => { E.free = null; E.followAdjust = null; UI.needsDraw = true; UI.slidersStale = true; }, { tt: 'Leave looking around and see what the camera keys show' });
        const el = h('div', { class: 'ts-stage' }, wrap, h('div', { class: 'ts-transport' }, play, steer, time, clock, quality, back), info);
        UI.canvas = canvas; UI.timeSlider = time; UI.clock = clock; UI.info = info; UI.playButton = play; UI.view = wrap; UI.steerButton = steer; UI.joystick = joystick;
        // the stage is built once (its canvas holds the frames): its words are put again when the language changes
        UI.stageWords = [[back, 'Back to the keys', 'Leave looking around and see what the camera keys show'],
                         [quality, null, 'How sharp the view is drawn while working (recording always draws at full size)'],
                         [steer, null, 'Steer the trail: while the chain plays (or Deforum draws it), the arrows or W A S D, the joystick or DeforumationQT turn it and make it climb; the camera follows'],
                         [joystick, null, 'Drag to steer the trail'],
                         ...[...quality.options].map((option, i) => [option, ['View: fast', 'View: good', 'View: sharp'][i], null])];
        if (!E.renderer || E.renderer.canvas !== canvas) {
            try { E.renderer = window.StrataGL.create(canvas); } catch (e) { setTimeout(() => say(String(e.message || e), 'error'), 0); }
        }
        attachMouse(canvas);
        return el;
    }

    /* the on-screen joystick: drag its knob; left and right turn the trail, up and down make it climb */
    function makeJoystick() {
        const knob = h('div', { class: 'ts-knob' });
        const base = h('div', { class: 'ts-joystick', tt: 'Drag to steer the trail' }, knob);
        let held = false;
        const move = e => {
            if (!held) return;
            const box = base.getBoundingClientRect(), r = box.width / 2;
            let dx = e.clientX - (box.left + r), dy = e.clientY - (box.top + r);
            const d = Math.hypot(dx, dy);
            if (d > r) { dx *= r / d; dy *= r / d; }
            knob.style.transform = `translate(${dx}px, ${dy}px)`;
            E.steering.input.turn = dx / r; E.steering.input.climb = -dy / r;
        };
        const release = () => {
            if (!held) return;
            held = false; knob.style.transform = ''; E.steering.input.turn = 0; E.steering.input.climb = 0;
        };
        base.addEventListener('pointerdown', e => { held = true; base.setPointerCapture(e.pointerId); move(e); e.preventDefault(); });
        base.addEventListener('pointermove', move);
        base.addEventListener('pointerup', release);
        base.addEventListener('pointercancel', release);
        return base;
    }

    /* ends a take of steering and says which frames it steered */
    function endSteering(why) {
        const kept = E.stopSteering();
        held.clear();
        if (kept) say((why ? why + ' ' : '') + T('The trail was steered from frame {0} to {1}.', kept[0], kept[1] + 1), 'ok');
        else if (why) say(why, 'info');
    }

    /* the stage shows whether it steers and whether Deforum is drawing into it */
    function stageState() {
        if (!UI.view) return;
        const steering = E.steering.on;
        UI.steerButton.classList.toggle('ts-on', steering);
        UI.steerButton.textContent = '🎮 ' + T(steering ? 'Stop steering' : 'Steer');
        UI.view.classList.toggle('ts-steering', steering);
        UI.view.classList.toggle('ts-live', !!E.live);
        const badge = UI.view.querySelector('.ts-live-badge');
        if (badge) badge.textContent = E.live ? T('● Deforum is drawing: frame {0} of {1}', E.live.loaded, E.live.total) : '';
        const hint = UI.view.querySelector('.ts-steer-hint');
        if (hint) hint.textContent = T('← → or A D: turn · ↑ ↓ or W S: climb · or the joystick') + (E.dqt.on ? ' · ' + T('DeforumationQT: pan') : '');
    }

    /* the arrows and W A S D steer while steering is on (by the key's place, so a Hebrew keyboard works too) */
    const STEER_KEYS = { ArrowLeft: ['turn', -1], KeyA: ['turn', -1], ArrowRight: ['turn', 1], KeyD: ['turn', 1],
                         ArrowUp: ['climb', 1], KeyW: ['climb', 1], ArrowDown: ['climb', -1], KeyS: ['climb', -1] };
    const held = new Set();
    function keyboardSteering(e) {
        if (!E.steering.on || !STEER_KEYS[e.code]) return;
        const target = e.target;
        if (target && (/^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName) || target.isContentEditable)) return;
        if (!UI.root || !UI.root.isConnected || !UI.canvas.offsetParent) return;          // (another tab of Forge is shown)
        if (e.type === 'keydown') held.add(e.code); else held.delete(e.code);
        let turn = 0, climb = 0;
        for (const code of held) { const [axis, sign] = STEER_KEYS[code]; if (axis === 'turn') turn += sign; else climb += sign; }
        E.steering.input.turn = Core.clamp(turn, -1, 1); E.steering.input.climb = Core.clamp(climb, -1, 1);
        e.preventDefault();
    }

    /* the canvas keeps the shot's shape inside the space it has */
    function fit() {
        if (!UI.canvas || !E.doc || !UI.view) return;
        const box = UI.view.getBoundingClientRect();
        const W = Math.max(50, box.width), H = Math.max(50, Math.min(window.innerHeight * 0.74, 900));
        const aspect = E.doc.shot.width / E.doc.shot.height;
        let w = W, hh = W / aspect;
        if (hh > H) { hh = H; w = H * aspect; }
        UI.canvas.style.width = Math.round(w) + 'px'; UI.canvas.style.height = Math.round(hh) + 'px';
        const dpr = window.devicePixelRatio || 1;
        if (E.renderer && !E.recording) E.renderer.resize(Math.max(2, Math.round(w * dpr * UI.preview)), Math.max(2, Math.round(hh * dpr * UI.preview)));
        UI.needsDraw = true;
    }

    /* Drag on the picture: with the follow camera, turn around the figure (up and down: higher and lower) and the
     * wheel brings it closer; Shift or the right button looks around freely, as with the keys camera. A double
     * click goes back to what the keys show. */
    function attachMouse(canvas) {
        let drag = null;
        canvas.addEventListener('mousedown', e => {
            if (!E.doc) return;
            if (E.doc.camera.mode === 'follow' && !E.free && !(e.button === 2 || e.shiftKey)) {
                E.followAdjust = E.followAdjust || { yaw: 0, elevation: 0, fill: 1 };
                drag = { follow: true, x: e.clientX, y: e.clientY, yaw: E.followAdjust.yaw, elevation: E.followAdjust.elevation };
                e.preventDefault();
                return;
            }
            if (!E.free) {
                const cam = E.cameraAt(E.time);
                const d = Core.sub(cam.eye, cam.target), r = Core.len(d) || 1;
                E.free = { target: cam.target, distance: r, yaw: Math.atan2(d[0], d[2]) * 180 / Math.PI, pitch: Math.asin(Core.clamp(d[1] / r, -1, 1)) * 180 / Math.PI };
            }
            drag = { x: e.clientX, y: e.clientY, yaw: E.free.yaw, pitch: E.free.pitch, pan: e.button === 2 || e.shiftKey, target: E.free.target.slice() };
            e.preventDefault();
        });
        window.addEventListener('mousemove', e => {
            if (drag && drag.follow && E.followAdjust) {
                E.followAdjust.yaw = drag.yaw - (e.clientX - drag.x) * 0.4;
                E.followAdjust.elevation = Core.clamp(drag.elevation + (e.clientY - drag.y) * 0.3, -80, 80);
                UI.needsDraw = true; UI.slidersStale = true;
                return;
            }
            if (!drag || !E.free) return;
            const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
            if (drag.pan) {
                const cam = E.cameraAt(E.time), right = Core.norm(Core.cross(cam.forward, Core.UP));
                const k = E.free.distance / 400;
                E.free.target = Core.add(Core.add(drag.target, Core.mul(right, -dx * k)), Core.mul(Core.UP, dy * k));
            } else {
                E.free.yaw = drag.yaw - dx * 0.35;
                E.free.pitch = Core.clamp(drag.pitch + dy * 0.3, -85, 85);
            }
            UI.needsDraw = true;
        });
        window.addEventListener('mouseup', () => { drag = null; });
        canvas.addEventListener('contextmenu', e => e.preventDefault());
        canvas.addEventListener('wheel', e => {
            if (E.doc && E.doc.camera.mode === 'follow' && !E.free) {
                E.followAdjust = E.followAdjust || { yaw: 0, elevation: 0, fill: 1 };
                E.followAdjust.fill = Core.clamp(E.followAdjust.fill * Math.exp(-e.deltaY * 0.0012), 0.1, 6);
                UI.needsDraw = true; UI.slidersStale = true; e.preventDefault();
                return;
            }
            if (!E.free) return;
            E.free.distance = Core.clamp(E.free.distance * Math.exp(e.deltaY * 0.0012), 0.2, 200);
            UI.needsDraw = true; e.preventDefault();
        }, { passive: false });
        canvas.addEventListener('dblclick', () => { E.free = null; E.followAdjust = null; UI.needsDraw = true; UI.slidersStale = true; });
    }

    /* ---- the panel ---- */

    const TABS = [['chains', 'Chains'], ['generate', 'Generate'], ['world', 'World'], ['camera', 'Camera'], ['knife', 'Knife'], ['record', 'Record']];

    function renderPanel() {
        if (!UI.root) return;
        const panel = UI.root.querySelector('.ts-panel');
        const scroll = panel.querySelector('.ts-panel-body');
        const kept = scroll ? scroll.scrollTop : 0;
        panel.innerHTML = '';
        if (!E.doc) {
            panel.append(h('div', { class: 'ts-empty' }, h('p', { t: 'No project yet. Create one to begin.' }),
                button('Create a project', async () => {
                    const name = prompt(T('Name of the new project:'), T('My strata'));
                    if (name) { await E.create(name); await UI.fillProjects(); renderPanel(); }
                })));
            return;
        }
        const tabs = h('div', { class: 'ts-tabs' }, ...TABS.map(([id, label]) =>
            h('button', { type: 'button', class: 'ts-tab' + (UI.tab === id ? ' ts-on' : ''), t: label, on: { click: () => { UI.tab = id; renderPanel(); } } })));
        const body = h('div', { class: 'ts-panel-body' });
        ({ chains: chainsPanel, generate: generatePanel, world: worldPanel, camera: cameraPanel, knife: knifePanel, record: recordPanel })[UI.tab](body);
        panel.append(tabs, body);
        body.scrollTop = kept;
        fit();
    }
    UI.render = renderPanel;

    /* -- chains -- */

    function chainsPanel(body) {
        const doc = E.doc;
        if (!UI.current || !E.chain(UI.current)) UI.current = doc.chains.length ? doc.chains[0].id : null;
        put(body, h('div', { class: 'ts-chips' },
            ...doc.chains.map(c => h('button', { type: 'button', class: 'ts-chip' + (c.id === UI.current ? ' ts-on' : ''),
                                                  on: { click: () => { UI.current = c.id; renderPanel(); } } }, c.name + (c.frames ? ` (${c.frames})` : ''))),
            button('+ Add a chain', () => { const c = E.addChain(); UI.current = c.id; renderPanel(); })));
        const c = E.chain(UI.current);
        if (!c) { put(body, note('A chain is a figure\'s frames standing one behind the other. Add one, then give it a video or pictures.')); return; }

        put(body, row(field('Name', text(c, 'name', () => renderPanel()))));

        // the source and the cut
        const files = h('input', { type: 'file', class: 'ts-file', multiple: true, accept: 'video/*,image/*,.zip' });
        files.addEventListener('change', () => { UI.files = files.files; });
        const progress = progressBox('cut:' + c.id);
        progress.dataset.place = 'cut:' + c.id;
        const cut = c.cut;
        const sharpness = upscaleChoices();
        const allModels = E.options && E.options.models || [];
        const models = allModels.filter(m => m.kind !== 'video').map(m => [m.name, m.name + (m.heavy ? T(' (too big for this card: very slow)') : m.ready ? '' : T(' (downloads first)'))]);
        const videoModels = allModels.filter(m => m.kind === 'video' && m.ready).map(m => [m.name, m.name === 'rvm' ? T('RVM (best)') : T('RVM (fast)')]);
        put(body, section('Source and cut', !c.make,
            note('A video of one figure, or its frames as pictures (PNG with transparency is used as it is). The figure is cut out, cleaned so that hundreds of frames stack without fog, and held in the middle on one ground line.'),
            row(field('Video or pictures', files)),
            row(field('From second', number(cut, 'start', { min: 0 })), field('To second', number(cut, 'end', { min: 0, empty: null, placeholder: T('end') })),
                field('Frames a second', number(cut, 'fps', { min: 1, max: 120, empty: null, placeholder: T('all') }), 'How many frames of each second are taken (empty: every frame). Fewer frames: a shorter chain, faster passing.'),
                field('At most', number(cut, 'max_frames', { min: 2, max: 2000, step: 1 })),
                field('Black bars', select(cut, 'crop', [['auto', 'Cut away'], ['none', 'Keep']]), 'Black bars around the picture (a wide film in an upright video) are found and cut away, so they are not taken for the backdrop')),
            row(field('Background', select(cut, 'matte', [['auto', 'Decide by itself'], ...(videoModels.length ? [['rvm', 'People in a video, fast (RVM)']] : []),
                                                           ['model', 'Remove with a model'], ['key', 'A flat colour'], ['alpha', 'Already transparent']], () => { cut.model = null; renderPanel(); }),
                      'Decide by itself: its own transparency, a flat backdrop, otherwise the best picture model (BiRefNet-lite). RVM is ten times faster but leaves holes where a figure has the background\'s colour.'),
                cut.matte === 'model' ? field('Model', select(cut, 'model', [[null, T('the best one here')], ...models])) : null,
                cut.matte === 'rvm' ? field('Model', select(cut, 'model', [[null, T('RVM (best)')], ...videoModels.filter(m => m[0] !== 'rvm')])) : null,
                cut.matte === 'key' ? field('Colour', h('span', {}, colour(cut, 'key_colour'), button('From the edges', () => { cut.key_colour = null; renderPanel(); }, { tt: 'Take the colour of the picture\'s edges' }))) : null),
            row(field('Hold the figure', select(cut, 'align', [['centre', 'Body held in the middle'], ['sway', 'In the middle, with its sway'], ['keep', 'Where it was filmed']]),
                      'Held: the chain runs straight through the body and a shaking camera is taken out. With its sway: only slow drifting is taken out.'),
                field('Size', select(cut, 'scale', [['steady', 'One size for all'], ['follow', 'Every frame the same height']]), 'One size keeps growing and shrinking as filmed (a baby growing up stays visible)'),
                field('Ground', select(cut, 'ground', [[12, 'Keep jumps'], [0, 'Feet always on the ground']]), 'Keep jumps: the feet may leave the ground line, as when the figure jumps. Feet always on the ground: for a video whose camera moves up and down'),
                field('Frame height', select(cut, 'height', [512, 640, 768, 1024, 1280, 1536, 2048].map(v => [v, v + ' px'])))),
            sharpness ? row(field('Sharper (Real-ESRGAN)', select(cut, 'upscale', sharpness),
                                  'Small or soft footage made larger before the cut (choose a taller frame height too). The video model is fast; the photo model brings the most detail, slowly')) : null,
            c.make && sharpness ? row(field('Make these frames sharper', select(UI, 'sharpenChoice', sharpness.filter(([v]) => v)),
                                            'Cuts the chain again from its own source with Real-ESRGAN first, at a taller frame (a drawn chain keeps its key and its motion)'),
                                      button('Make them sharper', async () => {
                                          const choice = UI.sharpenChoice || 'realesr-animevideov3:2';
                                          await run(progress, p => E.sharpen(c.id, choice, p));
                                          say(T('Sharper: {0} frames of {1}×{2}', c.frames, c.width, c.height), 'ok'); renderPanel();
                                      })) : null,
            row(button(c.make ? 'Cut out again' : 'Cut out and make the chain', async () => {
                    if (!UI.files || !UI.files.length) {
                        if (!c.source) throw new Error('Choose a video or pictures first');
                        await run(progress, p => E.cutChain(c.id, false, p));
                    } else await run(progress, p => E.makeChain(c.id, UI.files, p));
                    UI.files = null; say(T('The chain is ready: {0} frames', c.frames), 'ok'); renderPanel();
                }, { class: 'ts-btn ts-primary' }),
                c.make ? button('Place again', async () => { await run(progress, p => E.cutChain(c.id, true, p)); say(T('Placed again'), 'ok'); renderPanel(); },
                                { tt: 'Change how the frames are held and their size, without cutting them out again' }) : null),
            progress));
        if (c.make) {
            const suspects = (c.suspects || []).length;
            const figureless = (c.figureless || []).length;
            const list = frames => frames.slice(0, 12).join(', ') + (frames.length > 12 ? '…' : '');
            const method = c.method || {};
            const how = T({ model: 'a model', rvm: 'a model', key: 'a flat colour', keymodel: 'a flat colour and a model', alpha: 'its own transparency' }[method.matte] || '-') + (method.model ? ` (${method.model})` : '');
            put(body, note('{0} frames, {1}x{2}, {3} a second. Background: {4}.', c.frames, c.width, c.height, fmt(c.fps, 1), how),
                        method.fallback ? h('div', { class: 'ts-warn' }, T('Not enough graphics memory for the best model: {0}. Close what else uses the graphics card and cut it out again for the best cut.', method.fallback)) : null,
                        // (frames with no figure are not cut wrongly, and drawing them again cannot help: nothing is there)
                        figureless ? note(c.generated ? '{0} frames have no figure, as the motion has none there (a cut between shots, a flash): {1}'
                                                      : '{0} frames have no figure: {1}. A cut between shots or a flash has none; otherwise the cut lost it.',
                                          figureless, list(c.figureless)) : null,
                        suspects ? h('div', { class: 'ts-warn' }, T('{0} frames look wrongly cut: {1}', suspects, list(c.suspects)),
                                     c.generated ? ' ' : null,
                                     c.generated ? button('Draw them again', () => {
                                         // to the Generate tab, with these frames ready to be drawn again
                                         const G = E.doc.generate = E.doc.generate || {};
                                         G.source = c.generated.from; G.target = c.id;
                                         UI.redrawText = c.suspects.join(', ');
                                         UI.tab = 'generate'; renderPanel();
                                     }, { tt: 'Opens Generate with these frames in "Draw some frames again"' }) : null) : null);
        }

        // where it stands
        const sp = c.spine;
        const length = Core.chainLength(E.cardGeometry(c), Math.max(1, c.frames));
        const shapes = [['line', 'Straight'], ['arc', 'A bend'], ['s', 'An S'], ['rise', 'Rising'], ['spiral', 'A spiral'], ['steered', 'Steered by hand']];
        put(body, section('Where it stands', true,
            row(field('Shape', select(sp, 'shape', shapes, value => {
                    if (value === 'steered' && !(sp.steer || []).length) sp.steer = [[0, 0, 0]];
                    if (value !== 'steered' && E.steering.on && E.steering.chain === c.id) E.stopSteering();
                    E.steerEdits++; renderPanel();
                }), 'Steered by hand: the trail goes where it was steered (🎮 Steer, under the picture)'),
                field('Spacing (cm)', number(sp, 'spacing', { min: 0.2, max: 200, scale: 100 }, () => renderPanel()), 'How far apart the frames stand. Closer: a solid body; farther: separate frames.'),
                field('Figure height (m)', number(c, 'size', { min: 0.1, max: 50 }))),
            note('The chain is {0} m long.', fmt(length, 2)),
            sp.shape !== 'steered' ? row(sp.shape === 'arc' || sp.shape === 's' ? field('Bend (°)', number(sp, 'bend', { min: -720, max: 720 })) : null,
                sp.shape !== 'spiral' ? field('Rise (m)', number(sp, 'rise', { min: -50, max: 200 })) : null,
                sp.shape !== 'spiral' && sp.rise ? field('Rising from', slider(sp, 'rise_from', { min: 0, max: 0.95, step: 0.05 })) : null) : null,
            sp.shape === 'spiral' ? row(field('Radius (m)', number(sp, 'radius', { min: 0.3, max: 100 })), field('Rounds', number(sp, 'turns', { min: 0.1, max: 50 })),
                                        field('Climb (m)', number(sp, 'climb', { min: -100, max: 100 }))) : null,
            row(field('Heading (°)', number(sp, 'heading', {})), field('X (m)', number(sp, 'x', {})), field('Z (m)', number(sp, 'z', {})),
                field('Height (m)', number(sp, 'y', {}))),
            row(field('Twist (°)', number(sp, 'twist', {}), 'The frames turn about the spine along the chain'),
                field('Lean with the spine', check(sp, 'tilt'), 'Frames lean as the spine rises (off: they stay upright)'),
                field('Shown', check(c, 'visible', () => E.loadFrames()))),
            row(field('First frame', number(c, 'from', { min: 0, step: 1 })), field('Last frame', number(c, 'to', { min: 0, step: 1, empty: null, placeholder: T('last') })),
                field('Start after (m)', number(c, 'offset', {}))),
            row(button('Remove this chain', async () => {
                if (!confirm(T('Remove the chain "{0}"? (Its frames are moved to the _deleted folder.)', c.name))) return;
                if (E.steering.on && E.steering.chain === c.id) E.stopSteering();
                await E.removeChain(c.id); UI.current = null; renderPanel();
            }, { class: 'ts-btn ts-danger' }))));

        put(body, steeringSection(c));

        // its look
        const L = c.look;
        put(body, section('Look', false,
            row(field('Fade to the far end', slider(L, 'fade', { min: 0, max: 1 })), field('Turn it over', check(L, 'reverse'), 'The fade, tint and glow grow towards the first frame instead')),
            row(field('Tint', colour(L, 'tint')), field('Tint amount', slider(L, 'tint_amount', { min: 0, max: 1 }))),
            row(field('Glow', colour(L, 'glow_color')), field('Glow amount', slider(L, 'glow', { min: 0, max: 3 }))),
            row(field('Darker edge-on', slider(L, 'rib', { min: 0, max: 1 }), 'Frames seen from the side are darker, so the stack reads as a ribbed body'),
                field('Shadow', slider(L, 'shadow', { min: 0, max: 1 }))),
            row(field('Dissolve near the lens (m)', number(E.doc.look, 'far', { min: 0.02, max: 5 }), 'A frame starts to dissolve this close to the camera'),
                field('Gone at (m)', number(E.doc.look, 'near', { min: 0, max: 5 })))));

        // time along the chain
        const TM = c.time;
        const rate = TM.rate || c.fps || 24;
        const used = Math.round(rate * E.doc.shot.duration);
        put(body, section('Time along the chain', TM.mode !== 'all',
            row(field('Time', select(TM, 'mode', [['all', 'Every frame stands there'], ['future', 'The present eats the chain'], ['past', 'The chain grows behind the present']],
                                     () => { E.touch(); renderPanel(); }))),
            TM.mode === 'future' ? note('The present plays at the front of the chain and what has passed is gone; the frames still to come wait behind it, running off into the distance (the reel\'s opening).') : null,
            TM.mode === 'past' ? note('The present is the newest frame and everything that has been trails behind it; the chain grows as the shot goes on.') : null,
            TM.mode !== 'all' ? row(field('Starts at frame', number(TM, 'start', { min: 0, step: 1 })),
                                    field('Frames a second', number(TM, 'rate', { min: 0.1, max: 240, empty: null, placeholder: String(c.fps || 24) }, () => renderPanel()),
                                          'How fast the present goes through the frames (empty: as they were filmed)'),
                                    field('Fade over (frames)', number(TM, 'fade', { min: 0.05, max: 50 })),
                                    field('Hold on the last frame', check(TM, 'hold'), 'When the present reaches the end of the chain it stays on the last frame')) : null,
            TM.mode !== 'all' && c.frames ? (used > c.frames - (TM.start || 0)
                ? h('div', { class: 'ts-warn' }, T('The shot uses {0} frames but the chain has {1}: the present runs out at {2} s.', used, c.frames - (TM.start || 0), fmt((c.frames - (TM.start || 0)) / rate, 1)))
                : note('The shot uses {0} of the chain\'s {1} frames.', used, c.frames)) : null));
    }

    /* -- steering: how the trail is steered by hand, and DeforumationQT -- */

    const dqtStatus = () => T(!E.dqt.on ? 'DeforumationQT is not linked' : E.dqt.running ? 'DeforumationQT is connected' : 'DeforumationQT is not running (start it, it is looked for every moment)');

    function steeringSection(c) {
        if (!E.ownerOf(c).frames && !E.isLive(c)) return section('Steering', false, note('Once the chain has its frames it can be steered through space.'));
        const sp = c.spine, cfg = E.doc.steer, steering = E.steering.on && E.steering.chain === c.id;
        const map = h('canvas', { class: 'ts-map' });
        UI.mapCanvas = map; UI.mapStale = true;
        const steered = sp.shape === 'steered';
        return section('Steering', steered || steering || E.dqt.on,
            note('Steer the figure through space as it moves: press 🎮 Steer and the shot plays; the arrows or W A S D (or the joystick on the picture) turn its trail left and right and make it climb, and the camera follows. While Deforum draws, the trail is steered frame by frame as the frames come. Left and right are the screen\'s.'),
            map,
            row(button(steering ? 'Stop steering' : '🎮 Steer this chain', () => {
                    if (steering) { endSteering(); return; }
                    E.doc.camera.follow = c.id;
                    E.startSteering();
                    E.touch();
                    if (!E.playing && !E.live) { E.playing = true; UI.playButton.textContent = '❚❚'; UI.lastTick = performance.now(); }
                }, { class: 'ts-btn ts-primary' }),
                steered ? button('Straighten the trail', () => { E.clearSteering(c); renderPanel(); }, { tt: 'Forget the steering: the trail goes straight' }) : null,
                E.steering.undo && E.steering.undo.chain === c.id && !steering ? button('Undo the last steering', () => { E.undoSteering(); renderPanel(); }, { tt: 'The trail as it was before the last take (or before it was straightened)' }) : null),
            steered ? note('{0} steering marks.', (sp.steer || []).length) : null,
            steered ? row(field('Steering strength', slider(sp, 'steer_scale', { min: 0, max: 3, step: 0.05 }), 'More: every turn and climb that was steered is stronger; 0: straight'),
                          field('Smooth (frames)', number(sp, 'steer_smooth', { min: 0, max: 60, step: 1 }), 'Each frame takes the average steering of this many frames around it')) : null,
            row(field('Turn (° a second)', number(cfg, 'turn', { min: 1, max: 360 }), 'How fast the trail turns with the stick fully over'),
                field('Climb (°)', number(cfg, 'climb', { min: 0, max: 80 }), 'How steeply it climbs with the stick fully up')),
            row(field('Left and right as on the screen', check(cfg, 'screen'), 'Off: left is the figure\'s own left'),
                field('Flip left and right', check(cfg, 'flip_turn')), field('Flip up and down', check(cfg, 'flip_climb'))),
            h('div', { class: 'ts-note' }, h('span', { class: 'ts-dqt-dot' + (E.dqt.running ? ' ts-on' : '') }), h('span', { class: 'ts-dqt-status' }, dqtStatus())),
            row(field('Steer from DeforumationQT', check(cfg, 'dqt', on => { E.setDqt(on); stageState(); renderPanel(); }),
                      'Its pan left/right turns the trail and up/down makes it climb; its rotation turns the camera around the figure and its zoom brings it closer. Only read: DeforumationQT and its renders are left as they are'),
                cfg.dqt ? field('Its rotation and zoom move the camera', check(cfg, 'dqt_camera', on => { if (!on) E.dqtCamera = null; })) : null,
                cfg.dqt ? field('Full turn at pan', number(cfg, 'dqt_range', { min: 0.1, max: 50 }), 'The pan value in DeforumationQT that turns the trail as much as the stick fully over') : null));
    }

    /* the place a job's progress is shown in a panel: the running job's own, when it belongs there (a panel
     * drawn again while a job runs keeps showing it) */
    function progressBox(place) {
        if (UI.jobView && UI.jobView.place === place) return UI.jobView.box;
        return h('div', { class: 'ts-progress' });
    }

    /* the Real-ESRGAN choices (Deforum's own upscaler), or null when the page does not know of them */
    function upscaleChoices() {
        const info = E.options && E.options.upscale;
        if (!info) return null;
        const labels = { 'realesr-animevideov3': 'video model', 'realesrgan-x4plus': 'photo model', 'realesrgan-x4plus-anime': 'anime model' };
        const notes = { 'realesr-animevideov3': ' (fast)', 'realesrgan-x4plus': ' (most detail, slow)', 'realesrgan-x4plus-anime': '' };
        const out = [[null, T('No')]];
        for (const name of Object.keys(labels))
            for (const scale of [2, 4]) out.push([`${name}:${scale}`, `${T(labels[name])} ×${scale}${notes[name] ? T(notes[name]) : ''}`]);
        return out;
    }

    /* a job's progress shown under its buttons, with the frame it is on */
    async function run(box, work) {
        if (UI.busyWork) throw new Error('Something is already being made; wait for it');
        const label = h('div', { class: 'ts-progress-text' }), bar = h('div', { class: 'ts-bar-fill' }), picture = h('img', { class: 'ts-progress-img' });
        // (the job's own Cancel lives in its progress box, so it is there however the panel is drawn again)
        const cancel = button('Cancel', async () => { if (UI.busy) await E.api('job/cancel', { id: UI.busy }); }, { tt: 'Stop this job' });
        box.innerHTML = ''; box.append(h('div', { class: 'ts-bar-track' }, bar), h('div', { class: 'ts-row' }, label, cancel), picture);
        UI.jobView = { place: box.dataset.place, box };
        UI.busyWork = true;
        try {
            return await work(job => {
                if (job.id) UI.busy = job.id;
                label.textContent = T(job.stage || job.status || '') + (job.position ? ' · ' + T('waiting, {0} before it', job.position) : '');
                if (job.progress != null) bar.style.width = (job.progress * 100).toFixed(1) + '%';
                if (job.preview) picture.src = job.preview;
            });
        } finally { UI.busyWork = false; UI.busy = null; UI.jobView = null; }
    }

    /* an editable list of keys: one row each, a field per column */
    function keysTable(keys, columns, after) {
        const table = h('table', { class: 'ts-keys' });
        table.append(h('tr', {}, ...columns.map(([, label]) => h('th', { t: label })), h('th')));
        keys.sort((a, b) => a.t - b.t);
        for (const key of keys) {
            table.append(h('tr', {}, ...columns.map(([name]) => h('td', {}, number(key, name, { step: 'any' }))),
                h('td', {}, h('button', { type: 'button', class: 'ts-x', tt: 'Remove this key', on: { click: () => {
                    keys.splice(keys.indexOf(key), 1); E.touch(); UI.needsDraw = true; if (after) after();
                } } }, '×'))));
        }
        return table;
    }

    /* -- generate -- */

    // the ControlNet units, as the server has them (strata/deforum.py UNITS)
    const UNITS = [
        ['pose', 'Pose (OpenPose)', 'Holds the body\'s pose, frame by frame', { on: true, weight: 0.9, start: 0, end: 1 }],
        ['depth', 'Depth', 'Keeps the body\'s volume and what is in front of what', { on: false, weight: 0.45, start: 0, end: 0.6 }],
        ['edges', 'Edges (Canny)', 'Keeps the outlines: clothes, hair, fingers', { on: false, weight: 0.35, start: 0, end: 0.5 }],
        ['shape', 'Silhouette (QR Monster)', 'The figure\'s exact shape, white on black: the strongest hold for a figure made of something else (water, fire, smoke)', { on: false, weight: 0.6, start: 0, end: 0.8 }],
        ['look', 'Look of a picture (IP-Adapter)', 'Gives every frame the look of a picture (a scene may have its own)', { on: false, weight: 0.6, start: 0, end: 1 }],
    ];

    // ready-made scenes: original descriptions, spread over the motion's length
    const SCENE_SETS = {
        life: { label: 'A life: a baby to old age', blend: 1.2, texts: ['a baby', 'a small child', 'a young adult', 'a middle-aged adult', 'a very old person'], who: true },
        // (a figure "made of" water or mist is drawn bare: a robed warrior surrounded by the element is not)
        elements: { label: 'Four elements', blend: 1.0, texts: [
            'a water warrior in long flowing robes of deep blue, surrounded by swirling water and splashing droplets, full body',
            'an earth warrior in a full suit of heavy rock armor covering the whole body, moss and glowing amber crystals, full body',
            'a fire warrior in long robes of dark red, surrounded by living flames and glowing embers, rising sparks, full body',
            'an air warrior in long layered robes of white and pale grey, surrounded by swirling wind and ribbons of mist, full body'] },
    };

    /* a picture or video for a field, from the computer (kept in the project's uploads) */
    function filePick(label, object, key, accept) {
        const input = h('input', { type: 'file', accept, style: 'display:none' });
        input.addEventListener('change', guarded(async () => {
            if (!input.files.length) return;
            say(T('Uploading…'));
            const up = await E.upload(input.files[0]);
            object[key] = up.path; E.touch();
            say(T('In place: {0}', up.path.split('/').pop()), 'ok');
            renderPanel();
        }));
        return h('span', { class: 'ts-unit-name' }, T(label) + ':', h('b', {}, object[key] ? object[key].split('/').pop() : T('none')),
            h('button', { type: 'button', class: 'ts-btn ts-pick', on: { click: () => input.click() } }, T('Choose…')),
            object[key] ? h('button', { type: 'button', class: 'ts-x', tt: 'Take it away', on: { click: () => { object[key] = null; E.touch(); renderPanel(); } } }, '×') : null,
            input);
    }

    function generatePanel(body) {
        const doc = E.doc;
        if (!UI.deforum) {
            UI.deforum = {};
            E.api('deforum/options').then(info => { UI.deforum = info; if (UI.tab === 'generate') renderPanel(); }).catch(e => { UI.deforum = { error: String(e.message || e) }; });
        }
        const info = UI.deforum || {}, last = info.last || {}, found = info.units || {};
        const G = doc.generate = doc.generate || {};
        G.options = Object.assign({ prompt: '', stages: [], scenes: [], blend: 1, negative: 'blurry, deformed, extra limbs, text, watermark, nsfw, nude',
                                    prefix: '<lora:LCM_SD15:1>', suffix: '', sampler: last.sampler || 'LCM', steps: last.steps || 8, cfg: last.cfg || 1.5,
                                    strength: 0.3, pose: 0.9, seed: 1234, background: '#808080', first: 0, last: null, every: 1, look: null,
                                    skip_empty: true }, G.options);
        const O = G.options;
        O.fill = Object.assign({ amount: 0, texture: null }, O.fill);
        O.steady = Object.assign({ on: false, weight: 0.55 }, O.steady);
        // an older project's pose weight becomes the pose unit
        const units = O.units = O.units || { pose: { on: (O.pose == null ? 0.9 : O.pose) > 0, weight: O.pose == null ? 0.9 : O.pose } };
        for (const [kind, , , defaults] of UNITS) units[kind] = Object.assign({}, defaults, units[kind]);
        const motions = doc.chains.filter(c => c.make && !c.generated);
        if (!G.source || !E.chain(G.source)) G.source = motions.length ? motions[0].id : null;
        const from = E.chain(G.source);
        const seconds = from ? Math.max(0.1, (from.frames - 1) / (from.fps || 24)) : 10;
        // an older project's stages (a share of the chain, or a frame number) become scenes in seconds
        if (!(O.scenes || []).length && (O.stages || []).length && from) {
            O.scenes = O.stages.map(s => ({ from: Math.round((s.at <= 1 ? s.at * seconds : s.at / (from.fps || 24)) * 100) / 100, text: s.text }));
            O.stages = [];
        }
        O.scenes = O.scenes || [];
        const drawn = doc.chains.filter(c => c.generated && c.generated.from === G.source);
        if (G.target && !E.chain(G.target)) G.target = null;

        put(body, note('Deforum draws a new figure over the motion of a chain: every frame is drawn where the motion has its figure, on a flat backdrop, with a still camera and the same seed, held by ControlNet. Each frame appears in the 3D view as soon as it is drawn (🎮 Steer moves it through space while it grows); at the end the new chain is cut cleanly and stands where the motion stood.'));
        if (info.error) put(body, h('div', { class: 'ts-warn' }, info.error));
        if (info.deforum === false) put(body, h('div', { class: 'ts-warn' }, T('Deforum is not installed in this Forge.')));

        // the motion: a chain made before, or a video given here
        const video = h('input', { type: 'file', class: 'ts-file', accept: 'video/*,image/*,.zip', multiple: true });
        video.addEventListener('change', () => { UI.motionFiles = video.files.length ? video.files : null; });
        put(body, section('The motion', true,
            motions.length ? row(field('Motion', select(G, 'source', motions.map(c => [c.id, `${c.name} (${c.frames})`]), () => renderPanel())),
                                 field('Draw into', select(G, 'target', [[null, T('a new chain')], ...drawn.map(c => [c.id, c.name])]))) : null,
            row(field(motions.length ? 'Or a new motion from a video' : 'A moving figure (a video)', video,
                      'A video of a moving figure: it is cut out into a motion chain first (as in the Chains tab), then drawn')),
            !motions.length ? note('Give a video of a moving figure (one person, the whole body): it becomes the motion.') : null));

        put(body, field('Who is drawn', (() => {
            const el = h('textarea', { class: 'ts-input ts-text', rows: 3, placeholder: T('e.g. a dancer made of glowing glass, full body') }, O.prompt || '');
            el.addEventListener('change', () => { O.prompt = el.value; E.touch(); });
            return el;
        })(), 'Used when there are no scenes'));

        // scenes along the time, as in Infinite Zoom
        const list = h('div', { class: 'ts-stages' });
        O.scenes.sort((a, b) => a.from - b.from);
        O.scenes.forEach((scene, i) => {
            const textEl = h('input', { type: 'text', class: 'ts-input ts-stage-text', value: scene.text || '', placeholder: T('who is drawn in this scene') });
            textEl.addEventListener('change', () => { scene.text = textEl.value; E.touch(); });
            list.append(h('div', { class: 'ts-scene' },
                number(scene, 'from', { min: 0, max: 3600 }, () => renderPanel()), textEl,
                h('button', { type: 'button', class: 'ts-x', tt: 'Remove this scene', on: { click: () => { O.scenes.splice(i, 1); E.touch(); renderPanel(); } } }, '×'),
                h('div', { class: 'ts-scene-files' }, filePick('Texture', scene, 'texture', 'video/*,image/*'), filePick('Look', scene, 'look', 'image/*,video/*'))));
        });
        const spread = (texts, blend, who) => {
            const base = (O.prompt || 'a person, full body').trim();
            O.scenes = texts.map((t, i) => ({ from: Math.round(seconds * i / texts.length * 100) / 100, text: who ? `${base}, ${t}` : t }));
            O.blend = blend; E.touch(); renderPanel();
        };
        put(body, section('Scenes along the time', O.scenes.length > 0,
            note('Like Infinite Zoom: each scene starts at a second of the motion with its own prompt and blends into the next. A scene may fill the figure with its own texture (a video or picture: water, fire, marble) and give it the look of its own picture. Without scenes, "Who is drawn" is used for every frame.'),
            list,
            h('div', { class: 'ts-chips' },
                button('+ A scene', () => {
                    const after = O.scenes.length ? O.scenes[O.scenes.length - 1].from : -1;
                    O.scenes.push({ from: Math.round(Math.min(seconds, after + Math.max(1, seconds / 4)) * 100) / 100, text: O.prompt || '' });
                    if (O.scenes.length === 1) O.scenes[0].from = 0;
                    E.touch(); renderPanel();
                }),
                ...Object.values(SCENE_SETS).map(set => button(set.label, () => spread(set.texts, set.blend, set.who)))),
            row(field('Blend (s)', number(O, 'blend', { min: 0, max: 30 }), 'How long one scene takes to become the next'),
                field('Before every prompt', text(O, 'prefix'), 'A LoRA or words put before every scene (LCM needs its LoRA)'),
                field('After every prompt', text(O, 'suffix'))),
            row(field('Not wanted', text(O, 'negative')))));

        // the figure's fill
        put(body, section('Fill the figure', O.fill.amount > 0,
            note('Before Deforum draws, the figure can be filled with a texture (a video of water, fire, clouds, or a picture) keeping its own light and shade: Deforum then draws a figure made of it. A scene\'s own texture replaces this one.'),
            row(filePick('Texture', O.fill, 'texture', 'video/*,image/*'),
                field('How much', slider(O.fill, 'amount', { min: 0, max: 1, step: 0.05 }), '0: the motion\'s own picture; 1: all texture'))));

        // ControlNet
        const grid = h('div', { class: 'ts-units' });
        for (const [kind, label, hint] of UNITS) {
            const unit = units[kind], model = found[kind] && found[kind].model;
            grid.append(h('div', { class: 'ts-unit' }, h('label', { class: 'ts-unit-name', tt: hint }, check(unit, 'on', () => renderPanel()), T(label)),
                h('div', { class: 'ts-unit-values' },
                    unit.on ? field('Weight', slider(unit, 'weight', { min: 0, max: 2, step: 0.05 })) : null,
                    unit.on ? field('From', number(unit, 'start', { min: 0, max: 1, step: 0.05 }), 'The share of the steps it starts in (0: from the first)') : null,
                    unit.on ? field('To', number(unit, 'end', { min: 0, max: 1, step: 0.05 }), 'The share of the steps it stops after (1: to the last)') : null,
                    info.units ? (model ? h('span', { class: 'ts-note' }, model) : h('span', { class: 'ts-missing' }, T('no model for it here: left out'))) : null)));
        }
        put(body, section('ControlNet', true,
            note('What holds each drawn frame to the motion. The pose is enough for a person; for a figure made of something else add the silhouette, and depth or edges for more of the body.'),
            grid,
            units.look.on ? row(filePick('Look picture', O, 'look', 'image/*,video/*')) : null,
            row(field('Keep the look steady', check(O.steady, 'on', () => renderPanel()),
                      'One frame of each scene is drawn first; IP-Adapter then gives every frame of that scene its face, clothes and colours, so they do not change from frame to frame'),
                O.steady.on ? field('How strongly', slider(O.steady, 'weight', { min: 0.2, max: 1.2, step: 0.05 })) : null,
                O.steady.on && found.look && !found.look.model ? h('span', { class: 'ts-missing' }, T('needs an IP-Adapter model')) : null)));

        put(body, section('Drawing', true,
            info.checkpoints && info.checkpoints.length ? row(field('Checkpoint', select(O, 'checkpoint', [[null, T('the one Forge has loaded') + (info.checkpoint ? ` (${String(info.checkpoint).split(' [')[0]})` : '')],
                                                                                         ...info.checkpoints.map(n => [n, n])]),
                                                                   'The model Deforum draws with; Forge\'s own is put back when it is done')) : null,
            row(field('Sampler', text(O, 'sampler')), field('Steps', number(O, 'steps', { min: 1, max: 150, step: 1 })),
                field('CFG', number(O, 'cfg', { min: 0, max: 30 })), field('Seed', number(O, 'seed', { step: 1 }))),
            row(field('The motion frame kept', slider(O, 'strength', { min: 0.1, max: 0.9, step: 0.02 }), 'How much of the motion frame stays (Deforum\'s strength): less keeps the person, more lets the prompt draw a new one'),
                upscaleChoices() ? field('Sharper drawn frames (Real-ESRGAN)', select(O, 'upscale', upscaleChoices()),
                                         'Each drawn frame is made larger before it is cut, and the chain\'s frames are that much taller: sharp close-ups. Deforum\'s own upscaler') : null),
            row(field('Backdrop', colour(O, 'background'), 'The flat colour the figure is drawn on, and cut away by'),
                field('First frame', number(O, 'first', { min: 0, step: 1 })), field('Last frame', number(O, 'last', { min: 0, step: 1, empty: null, placeholder: T('last') })),
                field('Every', number(O, 'every', { min: 1, step: 1 }), 'Draw every n-th frame of the motion (fewer frames, faster)'),
                field('Skip frames with no figure', check(O, 'skip_empty', () => renderPanel()),
                      'A frame of the motion with no figure (a cut between shots, a flash) would be drawn as plain backdrop: it is left out, and the drawn chain goes on from the next figure')),
            last.from ? note('Sampler, steps and CFG start from your last Deforum render.') : null));

        const progress = progressBox('generate');
        progress.dataset.place = 'generate';
        if (from) {
            const first = O.first || 0, end = O.last == null ? from.frames - 1 : O.last, step = Math.max(1, O.every);
            // (a motion placed before frames with no figure were told apart has no list of them: none are counted)
            const skipped = O.skip_empty ? (from.figureless || []).filter(k => k >= first && k <= end && (k - first) % step === 0).length : 0;
            const count = Math.max(0, Math.floor((end - first) / step) + 1 - skipped);
            if (skipped) put(body, note('{0} frames of the motion have no figure and are not drawn.', skipped));
            const W = Math.ceil(from.width / 64) * 64, H = Math.ceil(from.height / 64) * 64;
            const extra = UNITS.filter(([kind]) => kind !== 'pose' && units[kind].on).length;
            put(body, note('Deforum draws {0} frames of {1}×{2}: about {3} minutes here.', count, W, H,
                           fmt(count * (W * H / (768 * 768)) * O.steps * 0.32 * (1 + 0.22 * extra) / 60, 0)));
        }
        // some frames of the chain drawn into, drawn again (the rest kept)
        const target = G.target && E.chain(G.target);
        if (target && target.generated && target.make && target.generated.from === G.source) {
            const redrawProgress = progressBox('redraw');
            redrawProgress.dataset.place = 'redraw';
            const framesEl = h('input', { type: 'text', class: 'ts-input', value: UI.redrawText || '', placeholder: T('e.g. 18-21, 44') });
            framesEl.addEventListener('change', () => { UI.redrawText = framesEl.value; });
            if (UI.redrawNewSeed == null) UI.redrawNewSeed = true;
            const newSeed = h('input', { type: 'checkbox', class: 'ts-check', checked: UI.redrawNewSeed });
            newSeed.addEventListener('change', () => { UI.redrawNewSeed = newSeed.checked; });
            put(body, section('Draw some frames again', !!UI.redrawText,
                note('Only these frames of "{0}" are drawn again, with the scenes and settings above (a steady look keeps the anchors it was drawn with); every other frame stays as it is, and the chain is cut again.', target.name),
                row(field('Frames', framesEl, 'Frame numbers of the drawn chain, as in "18-21, 44"'),
                    field('With a new seed', newSeed, 'The same seed and settings would draw the same frames again'),
                    target.suspects && target.suspects.length ? button('The frames that look wrongly cut', () => {
                        UI.redrawText = target.suspects.join(', '); framesEl.value = UI.redrawText;
                    }) : null),
                target.generated.redrawn && target.generated.redrawn.length ? note('Drawn again so far: {0}', target.generated.redrawn.join(', ')) : null,
                row(button('Draw these frames again', async () => {
                    UI.redrawText = framesEl.value;
                    const frames = Core.parseFrames(UI.redrawText, target.frames);
                    if (!frames.length) throw new Error('Choose which frames to draw again');
                    // (the server moves it past every seed the chain was drawn with)
                    const options = Object.assign({}, O, { redraw_seed: UI.redrawNewSeed ? (+O.seed || 0) + 7919 : null });
                    await run(redrawProgress, p => E.redraw(G.source, options, target.id, frames, p));
                    say(T('Drawn again: {0} frames of "{1}"', frames.length, target.name), 'ok'); renderPanel();
                }, { class: 'ts-btn ts-primary' })),
                redrawProgress));
        }

        put(body, row(button('Generate with Deforum', async () => {
                if (!(O.prompt || '').trim() && !O.scenes.some(s => (s.text || '').trim())) throw new Error('Say who is drawn first');
                const files = UI.motionFiles;
                if (!files && !from) throw new Error('Choose a video of a moving figure first');
                const c = await run(progress, async p => {
                    let source = G.source;
                    if (files) {
                        // the video becomes a motion chain first, standing where a new chain stands
                        const name = String(files[0].name || 'motion').replace(/\.[^.]+$/, '');
                        const motion = E.addChain(name);
                        await E.makeChain(motion.id, files, p);
                        source = G.source = motion.id;
                        G.target = null;
                        UI.motionFiles = null;
                    }
                    return E.generate(source, O, G.target, p);
                });
                G.target = c.id;
                say(T('The drawn chain is ready: {0} frames', c.frames), 'ok'); renderPanel();
            }, { class: 'ts-btn ts-primary' })),
            progress);
    }

    /* -- world -- */

    function worldPanel(body) {
        const W = E.doc.world;
        const pano = h('input', { type: 'file', class: 'ts-file', accept: 'image/*,video/*' });
        pano.addEventListener('change', guarded(async () => {
            if (!pano.files.length) return;
            say(T('Uploading…'));
            const up = await E.upload(pano.files[0]);
            W.kind = 'panorama'; W.panorama = up.path; E.touch(); await E.loadWorld(); say(T('The world is in place'), 'ok'); renderPanel();
        }));
        put(body,
            row(field('World', select(W, 'kind', [['sky', 'A sky and a ground'], ['panorama', 'A 360 panorama']], () => { E.loadWorld(); renderPanel(); }))),
            W.kind === 'panorama' ? row(field('Panorama from the computer', pano, 'A 360 picture, or a 360 video: the world then changes while the shot plays')) : null,
            W.kind === 'panorama' && W.panorama ? note('Now: {0}', W.panorama.split('/').pop()) : null,
            W.kind === 'panorama' && E.isVideo(W.panorama) ? row(
                field('The video plays', select(W, 'video_play', [['shot', 'Once over the whole shot'], ['own', 'At its own speed, again and again']], () => { E.touch(); UI.needsDraw = true; renderPanel(); }),
                      'Over the whole shot: its first frame at the shot\'s start and its last at the end, so it changes together with the figures'),
                (W.video_play || 'shot') === 'own' ? field('Speed', number(W, 'video_rate', { min: 0.05, max: 8, step: 0.05 })) : null,
                (W.video_play || 'shot') === 'own' ? field('From second', number(W, 'video_start', { min: 0, step: 0.1 })) : null) : null,
            row(field('The panorama was seen from (m)', number(W, 'eye', { min: 0.05, max: 50 }), 'How high above the ground the panorama was taken: the ground is put that far below its middle'),
                field('Size of the world (m)', number(W, 'radius', { min: 5, max: 2000 })),
                field('Turn (°)', number(W, 'turn', {}))),
            row(field('Haze', slider(W, 'fog', { min: 0, max: 2 }))),
            W.kind === 'sky' ? row(field('Sky above', colour(W, 'sky_top')), field('At the horizon', colour(W, 'sky_horizon')), field('Ground', colour(W, 'ground'))) : null);
        if (E.options && E.options.sim360) {
            const grid = h('div', { class: 'ts-worlds' });
            put(body, section('A world from Simulation 360', !!UI.worlds,
                row(button(UI.worlds ? 'Look again' : 'Show its worlds', async () => { UI.worlds = (await E.api('sim360/worlds')).worlds; renderPanel(); })),
                grid));
            for (const w of UI.worlds || []) {
                const thumb = w.thumb ? `./time-strata/sim360/thumb/${encodeURIComponent(w.project)}/${encodeURIComponent(w.world)}/${encodeURIComponent(w.thumb)}` : '';
                grid.append(h('button', { type: 'button', class: 'ts-world', title: `${w.project} / ${w.name}`, on: { click: guarded(async () => {
                    const got = await E.api('sim360/take', { project: E.project, from_project: w.project, world: w.world, file: w.file });
                    W.kind = 'panorama'; W.panorama = got.path;
                    if (w.eye != null && w.eye > 0.3) W.eye = w.eye;
                    E.touch(); await E.loadWorld(); say(T('The world is in place'), 'ok'); renderPanel();
                }) } }, thumb ? h('img', { src: thumb, loading: 'lazy' }) : null, h('span', {}, w.name), h('small', {}, w.project)));
            }
        }
    }

    /* -- camera -- */

    /* a slider for one follow value at the current moment (it edits the key there, or makes one) */
    function followSlider(fieldName, label, min, max, step, hint) {
        const now = E.followValues(E.time)[fieldName];
        const shown = h('span', { class: 'ts-value' }, fmt(now, step < 0.1 ? 2 : step < 1 ? 1 : 0));
        const input = h('input', { type: 'range', class: 'ts-range', min, max, step, value: now });
        input.addEventListener('input', () => {
            const value = parseFloat(input.value);
            E.followAdjust = null;
            E.setFollowValue(fieldName, value);
            shown.textContent = fmt(value, step < 0.1 ? 2 : step < 1 ? 1 : 0);
            UI.needsDraw = true;
        });
        UI.followSliders.push({ fieldName, input, shown, step });
        return field(label, h('span', { class: 'ts-slider' }, input, shown), hint);
    }

    function cameraPanel(body) {
        const doc = E.doc, cam = doc.camera;
        UI.followSliders = [];
        put(body,
            row(field('The shot (seconds)', number(doc.shot, 'duration', { min: 0.5, max: 600 }, () => renderPanel())),
                field('Follow the chain', select(cam, 'follow', doc.chains.map(c => [c.id, c.name]))),
                field('Camera', select(cam, 'mode', [['follow', 'Follow the figure'], ['keys', 'Keys along the chain']], () => { E.free = null; E.followAdjust = null; renderPanel(); }),
                      'Follow the figure: say from where and how large, and the camera keeps it there along its trail. Keys along the chain: places along the chain, for flying into it')));
        if (cam.mode === 'follow') {
            const c = E.followed(), hasPresent = c && E.presentOf(c, E.time) != null;
            put(body,
                note('Ready-made moves (they replace the follow keys):'),
                h('div', { class: 'ts-chips' }, ...[['front', 'In front'], ['side', 'From the side'], ['behind', 'From behind'], ['close', 'Close up'], ['orbit', 'Around it'], ['reveal', 'Reveal the trail']]
                    .map(([id, label]) => button(label, () => { E.applyFollowPreset(id); renderPanel(); }))),
                note('Drag on the picture to turn around the figure, the wheel brings it closer, Shift or the right button looks around freely; "A key here" keeps it. Or set it here, at this moment:'),
                row(followSlider('yaw', 'Around it (°)', -180, 360, 1, '0: in front of its face, 90: at its side, 180: behind it'),
                    followSlider('elevation', 'Height (°)', -30, 85, 1, 'How far above the figure\'s own horizon the camera looks from')),
                row(followSlider('fill', 'Size in the picture', 0.08, 1.4, 0.01, 'How much of the picture\'s height the figure takes; the distance follows from the lens'),
                    followSlider('aim', 'Aim at', 0.1, 1, 0.01, 'Where on the figure the camera looks, from its feet (0) to its head (1)')),
                row(followSlider('lead', 'Look ahead (m)', -3, 6, 0.1, 'How far past the figure, along its trail, the camera looks'),
                    followSlider('smooth', 'Smooth turns (m)', 0, 8, 0.1, 'How much of the trail\'s direction is averaged: more, and a sharp turn becomes a calm swing')),
                row(followSlider('fov', 'Lens °', 15, 100, 1), followSlider('roll', 'Roll °', -45, 45, 1),
                    !hasPresent ? followSlider('along', 'Along the chain (m)', -2, Math.max(2, Core.chainLength(E.cardGeometry(c || { spine: {} }), (c && c.frames) || 2) + 2), 0.05,
                                               'Where on the chain the figure followed is (a chain without a present)') : null),
                row(button('A key here', () => { E.keyHere(); renderPanel(); }, { class: 'ts-btn ts-primary' }), h('span', { class: 'ts-rate' })),
                keysTable(cam.follow_keys, [['t', 'Second'], ['yaw', 'Around °'], ['elevation', 'Height °'], ['fill', 'Size'], ['aim', 'Aim'], ['lead', 'Ahead'], ['smooth', 'Smooth'], ['fov', 'Lens °']],
                          () => renderPanel()),
                E.dqt.on && doc.steer.dqt_camera !== false ? row(note('DeforumationQT\'s rotation and zoom are added on top of these (Chains › Steering).'),
                    button('Forget its turn', () => { Object.assign(E.dqt, { yaw: 0, elevation: 0, base: null }); E.dqtCamera = null; UI.needsDraw = true; UI.slidersStale = true; })) : null);
        } else put(body,
            row(field('Ride with the present', check(cam, 'ride'), 'The keys\' places along the chain are measured from the chain\'s present, so the camera travels with it')),
            note('Ready-made moves (they replace the keys):'),
            h('div', { class: 'ts-chips' }, ...[['opening', 'The reel\'s opening'], ['through', 'Fly through'], ['side', 'Beside it'], ['front', 'From the front'], ['orbit', 'Around it']]
                .map(([id, label]) => button(label, () => { E.applyPreset(id); renderPanel(); }))),
            note('Drag on the picture to look around (right button or Shift: move), the wheel comes closer, a double click goes back to the keys. "A key here" keeps what is seen now at this moment.'),
            row(button('A key here', () => { E.keyHere(); renderPanel(); }, { class: 'ts-btn ts-primary' }),
                h('span', { class: 'ts-rate' })),
            keysTable(cam.keys, [['t', 'Second'], ['s', 'Along (m)'], ['side', 'Side'], ['up', 'Up'], ['look', 'Look ahead'], ['look_up', 'Look up'], ['look_side', 'Look side'], ['fov', 'Lens °'], ['roll', 'Roll °']],
                      () => renderPanel()),
            !cam.keys.length ? note('No keys yet: the reel\'s opening is shown.') : null);

        // the planner
        const P = doc.plan, plan = E.plan();
        put(body, section('Plan a flight through', true,
            note('How many frames a flight needs, how far apart they stand, and how sharp they must be.'),
            row(field('Seconds of flying', number(P, 'seconds', { min: 0.5, max: 600 }, () => renderPanel())),
                field('Speed (m/s)', number(P, 'speed', { min: 0.05, max: 50 }, () => renderPanel())),
                field('Frames passing a second', number(P, 'rate', { min: 1, max: 240 }, () => renderPanel())),
                field('Chain ahead at the end (m)', number(P, 'ahead', { min: 0, max: 100 }, () => renderPanel()))),
            h('div', { class: 'ts-plan' },
                T('Make {0} frames, {1} cm apart: a chain of {2} m. To be sharp up close a frame needs about {3} px of height.',
                  plan.frames, fmt(plan.spacing * 100, 1), fmt(plan.length, 1), plan.texture)),
            row(button('Use this plan', () => { E.usePlan(); renderPanel(); }, { tt: 'Sets the chain\'s spacing and flies the camera through it at this speed' }))));
    }

    /* -- knife -- */

    function knifePanel(body) {
        const K = E.doc.knife;
        const c = E.followed();
        const L = c ? Core.chainLength(E.cardGeometry(c), Math.max(1, c.frames)) : 4;
        put(body,
            note('The knife cuts the chain with a plane and takes away everything in front of it: what is left shows its cut face, every strip of it a different moment (past and present in one slice, as in strata-cut).'),
            row(field('Cut', check(K, 'on'))),
            h('div', { class: 'ts-chips' },
                button('Sweep along the chain', () => { K.on = true; K.keys = [{ t: 0, s: 0, yaw: 0, pitch: 0 }, { t: E.doc.shot.duration, s: L, yaw: 0, pitch: 0 }]; E.touch(); renderPanel(); }),
                button('A slanted slice', () => { K.on = true; K.keys = [{ t: 0, s: L * 0.1, yaw: 55, pitch: 0 }, { t: E.doc.shot.duration, s: L * 0.9, yaw: 55, pitch: 0 }]; E.touch(); renderPanel(); }),
                button('A slice from above', () => { K.on = true; K.keys = [{ t: 0, s: L * 0.2, yaw: 0, pitch: 50 }, { t: E.doc.shot.duration, s: L * 0.8, yaw: 0, pitch: 50 }]; E.touch(); renderPanel(); })),
            keysTable(K.keys, [['t', 'Second'], ['s', 'Along (m)'], ['yaw', 'Turn °'], ['pitch', 'Tilt °']], () => renderPanel()));
    }

    /* -- record -- */

    function recordPanel(body) {
        const R = E.doc.record;
        const music = h('input', { type: 'file', class: 'ts-file', accept: 'audio/*,video/*' });
        music.addEventListener('change', guarded(async () => {
            if (!music.files.length) return;
            const up = await E.upload(music.files[0]); E.doc.music = up.path; E.touch(); say(T('The music is in place'), 'ok'); renderPanel();
        }));
        const progress = h('div', { class: 'ts-progress' });
        put(body,
            row(field('Size', select(R, 'size', [['1080x1920', '1080×1920 (upright, reels)'], ['1920x1080', '1920×1080 (wide)'], ['1080x1080', '1080×1080 (square)'], ['720x1280', '720×1280 (upright, quick)'], ['1280x720', '1280×720 (wide, quick)']],
                    () => { const [w, hh] = R.size.split('x').map(Number); E.doc.shot.width = w; E.doc.shot.height = hh; E.touch(); fit(); })),
                field('Frames a second', select(R, 'fps', [24, 25, 30, 60]))),
            row(field('Kind', select(R, 'kind', [['mp4-h264', 'MP4 (plays everywhere)'], ['mp4-nvenc', 'MP4 by the NVIDIA card (faster)'], ['webm-vp9', 'WebM']])),
                field('Quality', select(R, 'quality', [[14, 'Very high'], [17, 'High'], [21, 'Good'], [25, 'Small file']])),
                field('Drawn larger', select(R, 'supersample', [[1, 'No'], [2, 'Twice (smoother edges, slower)']]))),
            row(field('Name', text(R, 'name', null, E.project))),
            row(field('Music', music), E.doc.music ? h('span', { class: 'ts-note' }, E.doc.music.split('/').pop(), ' ',
                h('button', { type: 'button', class: 'ts-x', tt: 'No music', on: { click: () => { E.doc.music = null; E.touch(); renderPanel(); } } }, '×')) : null,
                E.doc.music ? field('Music from second', number(R, 'music_start', { min: 0 })) : null),
            row(button('Record', async () => {
                    const label = h('div', { class: 'ts-progress-text' }), bar = h('div', { class: 'ts-bar-fill' });
                    progress.innerHTML = ''; progress.append(h('div', { class: 'ts-bar-track' }, bar), label);
                    E.playing = false; UI.playButton.textContent = '▶';
                    const result = await E.record(p => {
                        bar.style.width = (p.frame / p.total * 100).toFixed(1) + '%';
                        label.textContent = T('Frame {0} of {1}', p.frame, p.total) + ` · ${fmt(p.seconds, 0)} s` + (p.jpeg ? ' · JPEG' : '');
                    });
                    progress.innerHTML = '';
                    progress.append(h('div', { class: 'ts-ok' }, T('Saved: {0} ({1} frames, {2} s)', result.name, result.frames, result.seconds)),
                                    h('video', { class: 'ts-result', src: result.url, controls: true, loop: true }));
                    say(T('Saved: {0} ({1} frames, {2} s)', result.name, result.frames, result.seconds), 'ok');
                }, { class: 'ts-btn ts-primary' }),
                button('Stop', () => { if (E.recording) E.recording.cancel = true; }),
                button('Open the videos folder', async () => { await E.api('open-folder', { exports: true }); })),
            progress);
    }

    /* ---- drawing ---- */

    /* the follow sliders show the values of the moment (they change along the keys while the shot plays) */
    function refreshFollowSliders(now) {
        UI.slidersStale = false; UI.slidersAt = now;
        if (!UI.followSliders || !UI.followSliders.length) return;
        const values = E.followValues(E.time);
        for (const s of UI.followSliders) {
            if (!s.input.isConnected || document.activeElement === s.input) continue;
            s.input.value = values[s.fieldName];
            s.shown.textContent = fmt(values[s.fieldName], s.step < 0.1 ? 2 : step < 1 ? 1 : 0);
        }
    }

    /* the trail from above: the followed chain's spine, the frames drawn, the head and the camera */
    function drawMap(now) {
        UI.mapAt = now; UI.mapStale = false;
        const canvas = UI.mapCanvas;
        if (!canvas || !canvas.isConnected || !E.doc) return;
        const box = canvas.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
        if (!box.width) return;
        canvas.width = Math.round(box.width * dpr); canvas.height = Math.round(box.height * dpr);
        const g = canvas.getContext('2d');
        g.setTransform(dpr, 0, 0, dpr, 0, 0);
        g.clearRect(0, 0, box.width, box.height);
        const c = (E.steering.on && E.chain(E.steering.chain)) || E.followed();
        if (!c) return;
        const path = E.pathOf(c), geometry = E.cardGeometry(c);
        const frames = Math.max(2, E.ownerOf(c).frames || 2);
        const length = Core.chainLength(geometry, frames);
        const head = E.headOf(c, E.time), headS = Core.presentS(geometry, head);
        const cam = E.cameraAt(E.time);
        const points = [];
        for (let i = 0; i <= 160; i++) points.push(Core.pointAt(path, length * i / 160).p);
        // framed on the trail (at least 2.5 m across); a camera further out is shown at the edge, where it is
        const xs = points.map(p => p[0]), zs = points.map(p => p[2]);
        const minX = Math.min(...xs), maxX = Math.max(...xs), minZ = Math.min(...zs), maxZ = Math.max(...zs);
        const scale = Math.min((box.width - 28) / Math.max(2.5, maxX - minX), (box.height - 28) / Math.max(2.5, maxZ - minZ));
        const cx = (minX + maxX) / 2, cz = (minZ + maxZ) / 2;
        const at = p => [box.width / 2 + (p[0] - cx) * scale, box.height / 2 + (p[2] - cz) * scale];
        const inside = ([x, y]) => {
            const hx = box.width / 2 - 8, hy = box.height / 2 - 8, dx = x - box.width / 2, dy = y - box.height / 2;
            const k = Math.min(1, hx / Math.max(1e-6, Math.abs(dx)), hy / Math.max(1e-6, Math.abs(dy)));
            return [box.width / 2 + dx * k, box.height / 2 + dy * k];
        };
        const css = name => getComputedStyle(UI.root).getPropertyValue(name).trim() || '#888';
        g.lineWidth = 2; g.lineCap = 'round';
        // the trail: what stands now bright, the rest faint
        for (let i = 1; i < points.length; i++) {
            const s = length * i / 160;
            const [x0, y0] = at(points[i - 1]), [x1, y1] = at(points[i]);
            g.strokeStyle = s <= headS + 1e-6 ? css('--ts-accent') : css('--ts-dim');
            g.globalAlpha = s <= headS + 1e-6 ? 1 : 0.45;
            g.beginPath(); g.moveTo(x0, y0); g.lineTo(x1, y1); g.stroke();
        }
        g.globalAlpha = 1;
        const [hx, hy] = at(Core.pointAt(path, headS).p);
        g.fillStyle = E.steering.on ? css('--ts-bad') : css('--ts-fg');
        g.beginPath(); g.arc(hx, hy, 5, 0, Math.PI * 2); g.fill();
        // the camera, and where it looks
        const [ex, ey] = inside(at(cam.eye)), [tx, ty] = at(cam.target);
        const look = Math.hypot(tx - ex, ty - ey) || 1, reach = Math.min(40, look);
        g.strokeStyle = css('--ts-ok'); g.fillStyle = css('--ts-ok'); g.lineWidth = 1.5;
        g.beginPath(); g.moveTo(ex, ey); g.lineTo(ex + (tx - ex) / look * reach, ey + (ty - ey) / look * reach); g.stroke();
        g.fillRect(ex - 4, ey - 4, 8, 8);
        g.fillStyle = css('--ts-dim'); g.font = '11px sans-serif';
        g.direction = 'ltr'; g.textAlign = 'left';
        g.fillText(T('from above'), 6, 13);
    }

    function tick(now) {
        requestAnimationFrame(tick);
        if (!E.doc || !E.renderer || E.recording || !UI.root || !UI.root.isConnected || !UI.canvas.offsetParent) return;
        const D = E.doc.shot.duration;
        const dt = Math.min(0.1, Math.max(0, (now - (UI.lastTick || now)) / 1000));
        UI.lastTick = now;
        if (E.playing) {
            let t = E.time + dt;
            if (t >= D) {
                t %= D;
                // a take of steering ends where the shot ends (from its start it would steer the same frames again)
                if (E.steering.on && !E.live) endSteering(T('The shot started over.'));
            }
            E.time = t;
            UI.needsDraw = true;
        }
        if (E.steering.on) {
            E.steerTick(dt);
            const c = E.chain(E.steering.chain);
            if (c && E.recordSteering(E.headOf(c, E.time)) === 'back') endSteering(T('The shot went back.'));
            UI.needsDraw = true;
        }
        if (E.loading || E.live || E.dqtCamera) UI.needsDraw = true;
        if (UI.tab === 'camera' && (UI.slidersStale || (E.playing && now - (UI.slidersAt || 0) > 150))) refreshFollowSliders(now);
        if (UI.mapCanvas && (UI.mapStale || ((E.steering.on || E.live || E.playing) && now - (UI.mapAt || 0) > 120))) drawMap(now);
        if (E.live && now - (UI.badgeAt || 0) > 500) { UI.badgeAt = now; stageState(); }
        if (!UI.needsDraw) return;
        UI.needsDraw = false;
        E.draw();
        UI.timeSlider.value = Math.round(E.time / D * 1000);
        UI.clock.textContent = fmt(E.time, 2) + ' / ' + fmt(D, 1);
        const rate = Math.abs(E.passRate(E.time));
        const parts = [];
        if (E.loading) parts.push(T('Loading frames {0} of {1}', E.loading.done, E.loading.total));
        parts.push(T('{0} frames drawn', E.renderer.drawn));
        if (E.renderer.texH) parts.push(T('frames on the card: {0}×{1}', E.renderer.texW, E.renderer.texH));
        if (E.sharp.slots) parts.push(T('sharp up close: {0} at {1}×{2}', E.sharp.ready.filter(Boolean).length, E.renderer.sharpW, E.renderer.sharpH));
        parts.push(T('{0} frames pass a second', fmt(rate, 1)));
        if (E.free) parts.push(T('looking around'));
        UI.info.textContent = parts.join(' · ');
        const rateBox = UI.root.querySelector('.ts-rate');
        if (rateBox) rateBox.textContent = T('{0} frames pass a second', fmt(rate, 1));
    }

    function start() {
        if (UI.started) return;
        if (!build()) return;
        UI.started = true;
        E.on(what => {
            if (what === 'project') { renderPanel(); UI.fillProjects && UI.fillProjects(); }
            if (what === 'saved') { const s = UI.root.querySelector('.ts-saved'); if (s) { s.textContent = T('Saved'); setTimeout(() => { s.textContent = ''; }, 1500); } }
            if (what === 'steer' || what === 'live') { stageState(); if (UI.tab === 'chains' && !UI.busyWork) renderPanel(); UI.mapStale = true; }
            if (what === 'dqt') {
                stageState();
                const dot = UI.root.querySelector('.ts-dqt-dot'), status = UI.root.querySelector('.ts-dqt-status');
                if (dot) dot.classList.toggle('ts-on', E.dqt.running);
                if (status) status.textContent = dqtStatus();
            }
            if (what === 'frames') UI.mapStale = true;
            if (what === 'worldframe') UI.needsDraw = true;
            UI.needsDraw = true;
        });
        window.addEventListener('keydown', keyboardSteering);
        window.addEventListener('keyup', keyboardSteering);
        window.addEventListener('blur', () => { held.clear(); E.steering.input.turn = 0; E.steering.input.climb = 0; });
        E.start().then(() => { renderPanel(); }).catch(e => say(String(e.message || e), 'error'));
        new ResizeObserver(() => fit()).observe(UI.view);
        new MutationObserver(() => {
            const dir = S.hebrew() ? 'rtl' : 'ltr';
            if (UI.root && UI.root.dir !== dir) {
                UI.root.dir = dir; renderPanel();
                const bar = UI.root.querySelector('.ts-bar'); if (bar) bar.replaceWith(topBar());
                for (const [el, words, title] of UI.stageWords || []) { if (words) el.textContent = T(words); if (title) el.title = T(title); }
                stageState(); UI.mapStale = true;
            }
        }).observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });
        window.addEventListener('beforeunload', () => { if (E.dirty) E.saveNow(); });
        requestAnimationFrame(tick);
    }
    S.start = start;

    if (typeof onUiLoaded === 'function') onUiLoaded(start);
})();

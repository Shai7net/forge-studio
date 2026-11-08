/* Forge Studio - editing a frame of a clip. The frame at the playhead opens over the player: paint where to change
 * it and Forge draws there (the checkpoint chosen at the top; FLUX 3 gets its own settings), or draws the whole
 * frame again. Every result is a version. Back into the video: carried through the clip along the optical flow
 * (the clip gets the edited file as a new take, same trim), the clip made again from it by Wan, or a still.
 */
(function () {
    'use strict';
    const FS = window.ForgeStudio = window.ForgeStudio || {};
    const S = FS.state;
    const T = (...a) => FS.T(...a);
    const q = encodeURIComponent;
    const E = FS.frameEditor = {};
    let X = null;  // the open editing session
    const UI = {};

    const frameUrl = (id) => `/forge-studio/frame/file?name=${q(S.project)}&id=${q(id)}`;
    const loadImage = (src) => new Promise((resolve, reject) => {
        const img = new Image();
        img.onload = () => resolve(img);
        img.onerror = () => reject(new Error('could not load ' + src));
        img.src = src;
    });

    E.open = async function (clip) {
        clip = clip || (FS.gen.around().under);
        if (!clip || !clip.media) throw new Error(T('There is no clip under the playhead'));
        if (S.time < clip.start || S.time >= FS.end(clip)) FS.seek(clip.start);
        if (S.playing) FS.player.pause();
        const seconds = FS.gen.sourceAt(clip, S.time);
        FS.busy(T('Taking the frame out…'));
        let grabbed;
        try { grabbed = await FS.api('frame/grab', {name: S.project, web: clip.media, seconds}); } finally { FS.busy(null); }
        const img = await loadImage(frameUrl(grabbed.id));
        X = {clip: clip.id, web: clip.media, seconds, original: grabbed.id, versions: [{id: grabbed.id, img, label: T('Original')}],
            current: 0, W: grabbed.width, H: grabbed.height, tool: 'brush', size: Math.round(Math.max(grabbed.width, grabbed.height) / 30)};
        X.mask = document.createElement('canvas');
        X.mask.width = X.W;
        X.mask.height = X.H;
        build();
        draw();
    };

    function build() {
        const h = FS.h;
        close();
        UI.view = h('canvas', {class: 'fs-fe-canvas'});
        UI.view.width = X.W;
        UI.view.height = X.H;
        UI.versions = h('div', {class: 'fs-fe-versions'});
        UI.status = h('div', {class: 'fx-dim'});
        UI.prompt = h('textarea', {class: 'fx-input fx-textarea', rows: 3, dir: 'auto', placeholder: T('What should be there (for example: a red silk shirt)')});
        UI.negative = h('input', {class: 'fx-input', placeholder: T('Not wanted (used when CFG is above 1)')});
        UI.strength = h('input', {type: 'range', min: 0.1, max: 1, step: 0.05, value: 0.75, class: 'fx-range'});
        UI.strengthText = h('span', {class: 'fx-dim'}, '0.75');
        UI.strength.addEventListener('input', () => { UI.strengthText.textContent = UI.strength.value; });
        UI.size = h('input', {type: 'range', min: 4, max: Math.round(Math.max(X.W, X.H) / 6), value: X.size, class: 'fx-range'});
        UI.size.addEventListener('input', () => { X.size = +UI.size.value; });
        const tool = (name, label) => h('button', {class: 'fx-btn fs-fe-tool' + (X.tool === name ? ' on' : ''), 'data-tool': name,
            onclick: () => { X.tool = name; UI.root.querySelectorAll('.fs-fe-tool').forEach((b) => b.classList.toggle('on', b.dataset.tool === name)); }}, label);
        const compare = h('button', {class: 'fx-btn', title: T('Hold to see the original')}, T('Compare'));
        compare.addEventListener('pointerdown', () => { X.comparing = true; draw(); });
        for (const ev of ['pointerup', 'pointerleave']) compare.addEventListener(ev, () => { X.comparing = false; draw(); });
        UI.root = h('div', {class: 'fs-fe', dir: FS.hebrew() ? 'rtl' : 'ltr'},
            h('div', {class: 'fs-fe-left'},
                h('div', {class: 'fs-fe-head'}, h('b', {}, T('Frame of {0} at {1} s', X.web, X.seconds.toFixed(2))), compare,
                    h('button', {class: 'fx-icon', title: T('Close'), onclick: close}, '✕')),
                h('div', {class: 'fs-fe-stagebox'}, UI.view),
                UI.versions),
            h('div', {class: 'fs-fe-right'},
                h('div', {class: 'fx-group'}, h('div', {class: 'fx-group-title'}, T('1. Paint where to change')),
                    h('div', {class: 'fx-actions'}, tool('brush', '🖌 ' + T('Brush')), tool('erase', '⌫ ' + T('Eraser')),
                        h('button', {class: 'fx-btn', onclick: () => { X.mask.getContext('2d').clearRect(0, 0, X.W, X.H); draw(); }}, T('Clear'))),
                    h('label', {class: 'fx-field'}, T('Brush size'), UI.size)),
                h('div', {class: 'fx-group'}, h('div', {class: 'fx-group-title'}, T('2. Ask Forge to draw')),
                    UI.prompt, UI.negative,
                    h('label', {class: 'fx-field'}, h('span', {}, T('How much to change'), ' ', UI.strengthText), UI.strength),
                    h('button', {class: 'fx-primary', onclick: FS.guard(() => drawWithForge(true))}, '✨ ' + T('Draw in the painted area')),
                    h('button', {class: 'fx-btn', onclick: FS.guard(() => drawWithForge(false))}, T('Draw the whole frame again')),
                    h('div', {class: 'fx-dim'}, T('Uses the checkpoint selected at the top of Forge; FLUX 3 gets its own settings.'))),
                h('div', {class: 'fx-group'}, h('div', {class: 'fx-group-title'}, T('3. Back into the video')),
                    h('button', {class: 'fx-primary', onclick: FS.guard(carry)}, T('Carry the change through the clip')),
                    h('div', {class: 'fx-dim'}, T('Best for a colour, a logo, a stain or a small object: the change moves with the picture, the rest stays as it was.')),
                    h('button', {class: 'fx-btn', onclick: FS.guard(again)}, T('Make the clip again from this frame (Wan)')),
                    h('button', {class: 'fx-btn', onclick: FS.guard(still)}, T('Put it in as a still at the playhead'))),
                UI.status));
        FS.UI.stage.append(UI.root);
        brush();
        drawVersions();
    }

    function close() { if (UI.root) UI.root.remove(); UI.root = null; }
    E.close = close;

    function draw() {
        if (!UI.view || !X) return;
        const c = UI.view.getContext('2d');
        c.globalCompositeOperation = 'source-over';
        c.drawImage(X.comparing ? X.versions[0].img : X.versions[X.current].img, 0, 0, X.W, X.H);
        if (!X.comparing) {
            if (!X.tint) { X.tint = document.createElement('canvas'); X.tint.width = X.W; X.tint.height = X.H; }
            const t = X.tint.getContext('2d');
            t.globalCompositeOperation = 'source-over';
            t.clearRect(0, 0, X.W, X.H);
            t.drawImage(X.mask, 0, 0);
            t.globalCompositeOperation = 'source-in';
            t.fillStyle = '#ff3b30';
            t.fillRect(0, 0, X.W, X.H);
            c.globalAlpha = 0.45;
            c.drawImage(X.tint, 0, 0);
            c.globalAlpha = 1;
        }
    }

    function brush() {
        let down = false, last = null;
        const at = (e) => { const r = UI.view.getBoundingClientRect(); return [(e.clientX - r.left) * X.W / r.width, (e.clientY - r.top) * X.H / r.height]; };
        const paint = (p) => {
            const m = X.mask.getContext('2d');
            m.globalCompositeOperation = X.tool === 'erase' ? 'destination-out' : 'source-over';
            m.strokeStyle = m.fillStyle = '#fff';
            m.lineWidth = X.size;
            m.lineCap = m.lineJoin = 'round';
            m.beginPath();
            if (last) { m.moveTo(last[0], last[1]); m.lineTo(p[0], p[1]); m.stroke(); }
            m.beginPath();
            m.arc(p[0], p[1], X.size / 2, 0, Math.PI * 2);
            m.fill();
            last = p;
            draw();
        };
        UI.view.addEventListener('pointerdown', (e) => { down = true; last = null; UI.view.setPointerCapture(e.pointerId); paint(at(e)); });
        UI.view.addEventListener('pointermove', (e) => { if (down) paint(at(e)); });
        UI.view.addEventListener('pointerup', () => { down = false; last = null; });
    }

    function drawVersions() {
        const h = FS.h;
        UI.versions.innerHTML = '';
        X.versions.forEach((v, i) => UI.versions.append(h('button', {class: 'fs-fe-version' + (i === X.current ? ' on' : ''), title: v.label,
            onclick: () => { X.current = i; drawVersions(); draw(); }}, h('img', {src: frameUrl(v.id), alt: ''}), h('span', {}, v.label))));
    }

    const status = (t) => { if (UI.status) UI.status.textContent = t || ''; };

    function maskIsEmpty() {
        const d = X.mask.getContext('2d').getImageData(0, 0, X.W, X.H).data;
        for (let i = 3; i < d.length; i += 4 * 7) if (d[i]) return false;
        return true;
    }

    async function drawWithForge(masked) {
        if (masked && maskIsEmpty()) throw new Error(T('Paint the area to change first'));
        if (!UI.prompt.value.trim()) throw new Error(T('Write what should be there'));
        const form = new FormData();
        form.append('name', S.project);
        form.append('frame', X.versions[X.current].id);
        form.append('prompt', await FS.english(UI.prompt.value.trim(), 'image'));
        form.append('negative', UI.negative.value.trim());
        form.append('strength', UI.strength.value);
        form.append('seed', '-1');
        if (masked) form.append('mask', await new Promise((r) => X.mask.toBlob(r, 'image/png')), 'mask.png');
        status(T('Forge is drawing…'));
        const r = await fetch('/forge-studio/frame/draw', {method: 'POST', body: form});
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
        const task = await FS.follow(await r.json(), (t) => status(t.text));
        const img = await loadImage(frameUrl(task.frame));
        X.versions.push({id: task.frame, img, label: T('Version {0}', X.versions.length)});
        X.current = X.versions.length - 1;
        X.mask.getContext('2d').clearRect(0, 0, X.W, X.H);
        drawVersions();
        draw();
        status(T('Done ({0}). Paint again to change more, or send it back into the video.', task.text));
    }

    function chosen() {
        if (X.current === 0) throw new Error(T('Choose an edited version first (the original is unchanged)'));
        return X.versions[X.current].id;
    }

    async function carry() {
        const edited = chosen();
        status(T('Carrying the change through the clip…'));
        const task = await FS.follow(await FS.api('frame/carry', {name: S.project, web: X.web, seconds: X.seconds, original: X.original, edited}), (t) => status(t.text));
        S.media[task.web] = {path: task.web, missing: false};
        await FS.probe(task.web);
        FS.change('edited frame', () => {
            const c = FS.clip(X.clip);
            if (!c) return;
            c.takes = c.takes && c.takes.length ? c.takes : [c.media];
            if (!c.takes.includes(task.web)) c.takes.push(task.web);
            c.media = task.web;
        });
        let text = T('The clip now has the change ({0} frames). Change between frames inside it: {1} (the original: {2}).', task.frames, task.flicker, task.flicker_original);
        if (task.held != null && task.held < 0.8) text += ' ' + T('Where the picture moves too fast it fades out (kept on {0}% of the frames): for that, make the clip again from the frame.', Math.round(task.held * 100));
        FS.message(text);
        close();
    }

    async function again() {
        const edited = chosen();
        const c = FS.clip(X.clip);
        await FS.gen.request({kind: 'picture', prompt: UI.prompt.value.trim() || 'the same scene, natural motion', start: {frame: edited},
            seconds: Math.min(7.5, Math.max(1, c.duration))}, {take: c.id});
        close();
    }

    async function still() {
        const edited = chosen();
        const r = await FS.api('frame/still', {name: S.project, id: edited});
        S.media[r.web] = {path: r.path, missing: false};
        await FS.addMedia(r.web, {time: S.time, track: FS.freeLayer(S.time, S.time + 3)}, {mode: 'free', seconds: 3});
        FS.message(T('The edited frame is on the timeline at the playhead'));
        close();
    }

    FS.clipActions.push({id: 'frame', label: '🖼 ' + T('Edit the frame at the playhead'),
        when: (c) => (c.kind === 'video' || c.kind === 'image') && c.media && S.time >= c.start && S.time < FS.end(c),
        run: (c) => E.open(c)});
    FS.on('project', close);
})();

/* Forge Studio - the player: every layer of the edit drawn on one canvas, at the playhead or while playing.
 *
 * Each clip that shows has its own <video> / <audio> / picture, made when it is about to be needed and let go when
 * it is far away. While playing, the clock is the page's (performance.now); each video is kept within a quarter of
 * a second of where it should be and nudged back when it drifts. Paused, every video is moved to its exact frame
 * and the canvas is drawn again when the frame arrives. Placement is the same as render.py's.
 */
(function () {
    'use strict';
    const FS = window.ForgeStudio = window.ForgeStudio || {};
    const S = FS.state;
    const val = (c, k) => FS.val(c, k);

    const P = FS.player = {canvas: null, ctx: null, scale: 1, sources: {}, images: {}, texts: {}, raf: 0, clock: null};

    P.attach = function (canvas) {
        P.canvas = canvas;
        P.ctx = canvas.getContext('2d');
        new ResizeObserver(() => P.resize()).observe(canvas.parentElement);
        P.resize();
    };

    /* the canvas keeps the edit's shape, as sharp as the space it has (never above the edit's own size) */
    P.resize = function () {
        if (!P.canvas || !S.doc) return;
        const box = P.canvas.parentElement.getBoundingClientRect();
        const {width: W, height: H} = S.doc.settings;
        const fit = Math.min(box.width / W, box.height / H);
        P.canvas.style.width = Math.max(10, Math.floor(W * fit)) + 'px';
        P.canvas.style.height = Math.max(10, Math.floor(H * fit)) + 'px';
        P.scale = Math.min(1, fit * (window.devicePixelRatio || 1));
        P.canvas.width = Math.max(2, Math.round(W * P.scale));
        P.canvas.height = Math.max(2, Math.round(H * P.scale));
        P.draw();
    };

    /* ---- sources ---- */

    function sourceOf(clip) {
        if (clip.kind === 'image') {
            let img = P.images[clip.media];
            if (!img) {
                img = P.images[clip.media] = new Image();
                img.onload = () => P.draw();
                img.src = FS.mediaUrl(clip.media);
            }
            return img;
        }
        if (clip.kind !== 'video' && clip.kind !== 'audio') return null;
        let s = P.sources[clip.id];
        if (s && s.media !== clip.media) { release(clip.id); s = null; }
        if (!s) {
            const el = document.createElement(clip.kind === 'audio' ? 'audio' : 'video');
            el.preload = 'auto';
            el.playsInline = true;
            el.crossOrigin = 'anonymous';
            el.src = FS.mediaUrl(clip.media);
            el.addEventListener('seeked', () => { if (!S.playing) P.draw(); });
            el.addEventListener('loadeddata', () => { if (!S.playing) P.sync(true); });
            s = P.sources[clip.id] = {el, media: clip.media};
        }
        return s.el;
    }

    function release(id) {
        const s = P.sources[id];
        if (!s) return;
        s.el.pause();
        s.el.removeAttribute('src');
        s.el.load();
        delete P.sources[id];
    }

    /* ---- what shows at time t ---- */

    const active = (c, t) => t >= c.start - 1e-6 && t < FS.end(c) - 1e-6;
    const sourceTime = (c, t) => FS.sourceTime(c, t);

    /* a transition into clip r (r.transitionIn seconds): the clip before it stays on its last frame under it */
    const heldUnder = (c, t) => S.doc.clips.some((r) => r.track === c.track && r.transitionIn > 0 && Math.abs(r.start - FS.end(c)) < 1e-3
        && t >= r.start - 1e-6 && t < r.start + r.transitionIn);
    P.heldUnder = heldUnder;

    /* the picture's fades and transition (sound: its fades only, as render.py mixes it) */
    function fade(c, t, sound) {
        const fi = val(c, 'fadeIn'), fo = val(c, 'fadeOut');
        let a = c.transitionIn > 0 && !sound ? FS.transitionLook(c, t).alpha : 1;
        if (fi > 0) a = Math.min(a, (t - c.start) / fi);
        if (fo > 0) a = Math.min(a, (FS.end(c) - t) / fo);
        return Math.max(0, Math.min(1, a));
    }

    /* the clip's colour as a canvas filter (the export uses the same steps, render.py): CSS's brightness, contrast and
     * saturate, then warmth and tint as an SVG colour matrix of channel gains (worked out in sRGB, as ffmpeg does) */
    let gainDefs = null;
    function gainFilter(r, g, b) {
        const id = 'fs-gain-' + [r, g, b].map((v) => Math.round(v * 1000)).join('-');
        if (!gainDefs) {
            const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
            svg.setAttribute('width', '0');
            svg.setAttribute('height', '0');
            svg.style.position = 'absolute';
            gainDefs = document.createElementNS('http://www.w3.org/2000/svg', 'defs');
            svg.append(gainDefs);
            document.body.append(svg);
        }
        if (!document.getElementById(id)) {
            const f = document.createElementNS('http://www.w3.org/2000/svg', 'filter');
            f.id = id;
            f.setAttribute('color-interpolation-filters', 'sRGB');
            const m = document.createElementNS('http://www.w3.org/2000/svg', 'feColorMatrix');
            m.setAttribute('type', 'matrix');
            m.setAttribute('values', `${r} 0 0 0 0  0 ${g} 0 0 0  0 0 ${b} 0 0  0 0 0 1 0`);
            f.append(m);
            gainDefs.append(f);
        }
        return `url(#${id})`;
    }
    function colour(c) {
        const b = val(c, 'brightness'), k = val(c, 'contrast'), s = val(c, 'saturation');
        const gains = FS.gains(c);
        const tinted = gains.some((v) => Math.abs(v - 1) > 1e-4);
        if (b === 1 && k === 1 && s === 1 && !tinted) return '';
        return `brightness(${b}) contrast(${k}) saturate(${s})` + (tinted ? ' ' + gainFilter(...gains) : '');
    }

    /* where a visual clip is drawn at time t (keyframes moving it): centre, size and angle in the edit's pixels of the
     * part that shows (a crop hides edges, the rest stays put), as render.py places it; `crop` for drawing it */
    P.box = function (c, t) {
        const {width: W, height: H} = S.doc.settings;
        let mw = W, mh = H;
        if (c.kind !== 'text' && c.kind !== 'gen') {
            const info = S.info[c.media];
            if (info && info.width) { mw = info.width; mh = info.height; }
        }
        const fit = Math.min(W / mw, H / mh) * Math.max(0.01, FS.at(c, 'scale', t));
        const w = mw * fit, h = mh * fit, angle = FS.at(c, 'rotation', t) * Math.PI / 180;
        const crop = FS.cropOf(c), [L, T, R, B] = crop;
        const dx = (L - R) / 2 * w, dy = (T - B) / 2 * h, ca = Math.cos(angle), sa = Math.sin(angle);
        return {cx: W / 2 + FS.at(c, 'x', t) * W + ca * dx - sa * dy, cy: H / 2 + FS.at(c, 'y', t) * H + sa * dx + ca * dy,
            w: w * (1 - L - R), h: h * (1 - T - B), angle, crop};
    };

    /* masks: a scratch canvas the size the clip shows at, and the mask (white where it shows) at that size */
    const scratch = document.createElement('canvas');
    function masked(b) {
        const k = P.scale;  // the player canvas's pixels per pixel of the edit
        const w = Math.max(2, Math.round(b.w * k)), h = Math.max(2, Math.round(b.h * k));
        if (scratch.width !== w || scratch.height !== h) { scratch.width = w; scratch.height = h; }
        const ctx = scratch.getContext('2d');
        ctx.clearRect(0, 0, w, h);
        return {canvas: scratch, ctx, w, h};
    }
    const maskCache = new Map();
    function maskCanvas(m, w, h) {
        const key = `${m.shape}|${m.round}|${m.soft}|${w}x${h}`;
        let cv = maskCache.get(key);
        if (cv) return cv;
        if (maskCache.size > 24) maskCache.clear();
        cv = document.createElement('canvas');
        cv.width = w;
        cv.height = h;
        const g = cv.getContext('2d');
        const side = Math.min(w, h), f = m.soft * side, i = f / 2;
        if (f > 0) g.filter = `blur(${f / 2}px)`;
        g.fillStyle = '#fff';
        g.beginPath();
        if (m.shape === 'circle') g.ellipse(w / 2, h / 2, Math.max(0.5, w / 2 - i), Math.max(0.5, h / 2 - i), 0, 0, Math.PI * 2);
        else g.roundRect(i, i, Math.max(1, w - 2 * i), Math.max(1, h - 2 * i), Math.min(m.round * side, (Math.min(w, h) - 2 * i) / 2));
        g.fill();
        maskCache.set(key, cv);
        return cv;
    }

    function textCanvas(c, upto) {
        const {width: W, height: H} = S.doc.settings;
        // the whole text and a typewriter's part each keep a canvas of their own
        const part = upto != null && upto < FS.typeSteps(c.text);
        const key = JSON.stringify(c.text || {}) + W + 'x' + H + (part ? '#' + upto : '');
        const slot = part ? c.id + '~typed' : c.id;
        let e = P.texts[slot];
        if (!e || e.key !== key) {
            const cv = e ? e.canvas : document.createElement('canvas');
            cv.width = W;
            cv.height = H;
            const x = cv.getContext('2d');
            x.clearRect(0, 0, W, H);
            FS.drawText(x, W, H, c.text, part ? upto : undefined);
            e = P.texts[slot] = {canvas: cv, key};
        }
        return e.canvas;
    }
    P.textCanvas = textCanvas;

    /* the part of a clip that has something in it, for its animations: a text's letters (where its picture is not
     * see-through), anything else whole; [x0, y0, x1, y1] in parts of its box (render.py anchor_of) */
    P.anchor = function (c) {
        if (c.kind !== 'text') return [0, 0, 1, 1];
        const cv = textCanvas(c);
        const e = P.texts[c.id];
        if (e.anchorKey === e.key) return e.anchor;
        const {width: w, height: h} = cv;
        const data = cv.getContext('2d').getImageData(0, 0, w, h).data;
        let x0 = w, y0 = h, x1 = -1, y1 = -1;
        for (let y = 0; y < h; y++) {
            for (let x = 0; x < w; x++) {
                if (data[(y * w + x) * 4 + 3] > 8) { if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y; }
            }
        }
        e.anchor = x1 < 0 ? [0, 0, 1, 1] : [x0 / w, y0 / h, (x1 + 1) / w, (y1 + 1) / h];
        e.anchorKey = e.key;
        return e.anchor;
    };

    function drawPlaceholder(x, b, c, t) {
        x.fillStyle = '#2a1c05';
        x.fillRect(-b.w / 2, -b.h / 2, b.w, b.h);
        x.save();
        x.beginPath();
        x.rect(-b.w / 2, -b.h / 2, b.w, b.h);
        x.clip();
        x.strokeStyle = 'rgba(224,144,42,.35)';
        x.lineWidth = b.w / 18;
        const shift = (performance.now() / 40) % (b.w / 6);
        for (let k = -b.h; k < b.w + b.h; k += b.w / 6) {
            x.beginPath();
            x.moveTo(-b.w / 2 + k + shift, -b.h / 2);
            x.lineTo(-b.w / 2 + k + shift - b.h, b.h / 2);
            x.stroke();
        }
        x.restore();
        const g = c.gen || {};
        x.fillStyle = '#ffd08a';
        x.textAlign = 'center';
        x.font = `700 ${Math.round(b.h / 22)}px "Segoe UI", sans-serif`;
        x.fillText('✨ ' + FS.T('Being made'), 0, -b.h / 40);
        x.font = `400 ${Math.round(b.h / 34)}px "Segoe UI", sans-serif`;
        x.fillText(g.text || '', 0, b.h / 20);
        if (g.progress != null) {
            x.fillStyle = 'rgba(255,255,255,.15)';
            x.fillRect(-b.w * 0.3, b.h / 10, b.w * 0.6, b.h / 80);
            x.fillStyle = '#e0902a';
            x.fillRect(-b.w * 0.3, b.h / 10, b.w * 0.6 * g.progress, b.h / 80);
        }
    }

    P.draw = function () {
        if (!P.ctx || !S.doc) return;
        const t = S.time;
        const x = P.ctx;
        const {width: W, height: H, background} = S.doc.settings;
        x.setTransform(P.scale, 0, 0, P.scale, 0, 0);
        x.globalAlpha = 1;
        x.fillStyle = background || '#000';
        x.fillRect(0, 0, W, H);
        const order = FS.visualTracks().filter((tr) => !tr.hidden).map((tr) => tr.id);
        const clips = S.doc.clips.filter((c) => order.includes(c.track) && (active(c, t) || heldUnder(c, t)))
            .sort((a, b) => order.indexOf(a.track) - order.indexOf(b.track) || a.start - b.start);
        // a shot whose frame the browser is still finding (scrubbing, or the next shot at a cut) would be a black hole:
        // the last whole picture stays up instead, for up to 1.5 s, and the picture is drawn again as soon as it is there
        const loading = P.holdWhileLoading !== false && !P.clean && clips.some((c) => {
            if (c.kind !== 'video' && c.kind !== 'image') return false;
            const src = sourceOf(c);
            // a seeking video counts too: Chrome hands back an empty picture for it until 'seeked', though it says it is ready
            return !(src && (src.tagName === 'IMG' ? src.complete && src.naturalWidth : src.readyState >= 2 && !src.seeking));
        });
        P.loadingDraws = (P.loadingDraws || 0) + (loading ? 1 : 0);
        if (loading) {
            const now = performance.now();
            if (!P.waitSince) P.waitSince = now;
            if (P.lastGood && now - P.waitSince < 1500) {
                x.setTransform(1, 0, 0, 1, 0, 0);
                x.drawImage(P.lastGood, 0, 0);
                clearTimeout(P.retry);
                P.retry = setTimeout(() => P.draw(), 50);
                P.heldDraws = (P.heldDraws || 0) + 1;
                FS.emit('drawn', t);
                return;
            }
        } else P.waitSince = 0;
        let animating = false;
        for (const c of clips) {
            const b = P.box(c, t);
            const fill = FS.fillOf(c);
            if (fill) {
                // the blurred copy behind it: covering the frame and the margin, fading with the clip
                const src = sourceOf(c);
                const nw = src && (src.videoWidth || src.naturalWidth), nh = src && (src.videoHeight || src.naturalHeight);
                if (nw && nh) {
                    const fw = W + 2 * fill.margin, fh = H + 2 * fill.margin, k = Math.max(fw / nw, fh / nh);
                    x.save();
                    x.globalAlpha = Math.max(0, Math.min(1, FS.at(c, 'opacity', t))) * (active(c, t) ? fade(c, t) : 1);
                    x.filter = `blur(${fill.sigma * P.scale}px) brightness(${fill.dim})`;
                    x.drawImage(src, W / 2 - nw * k / 2, H / 2 - nh * k / 2, nw * k, nh * k);
                    x.restore();
                }
            }
            const look = FS.transitionLook(c, t);
            b.cx += look.dx * W;
            b.cy += look.dy * H;
            const anim = active(c, t) ? FS.animAt(c, t, P.anchor(c)) : null;
            if (anim) {
                // grown or shrunk about the anchor: the centre moves towards it by (1 - scale)
                const px = (anim.about[0] - 0.5) * b.w, py = (anim.about[1] - 0.5) * b.h, ca = Math.cos(b.angle), sa = Math.sin(b.angle);
                b.cx += anim.dx * W + (1 - anim.scale) * (ca * px - sa * py);
                b.cy += anim.dy * H + (1 - anim.scale) * (sa * px + ca * py);
                b.w *= anim.scale;
                b.h *= anim.scale;
            }
            x.save();
            x.globalAlpha = Math.max(0, Math.min(1, FS.at(c, 'opacity', t))) * (active(c, t) ? fade(c, t) : 1) * (anim ? anim.alpha : 1);
            x.translate(b.cx, b.cy);
            x.rotate(b.angle);
            if (anim && (anim.reveal[0] > 0 || anim.reveal[1] < 1)) {
                x.beginPath();
                x.rect(-b.w / 2 + anim.reveal[0] * b.w, -b.h / 2, Math.max(0, anim.reveal[1] - anim.reveal[0]) * b.w, b.h);
                x.clip();
            }
            if (c.kind === 'gen') { drawPlaceholder(x, b, c, t); animating = true; }
            else if (c.kind === 'text') x.drawImage(textCanvas(c, FS.typing(c) && active(c, t) ? FS.typedCount(c, t - c.start, FS.typeSteps(c.text)) : undefined), -b.w / 2, -b.h / 2, b.w, b.h);
            else {
                const src = sourceOf(c);
                const ready = src && (src.tagName === 'IMG' ? src.complete && src.naturalWidth : src.readyState >= 2);
                if (ready) {
                    const mask = FS.maskOf(c), key = P.noKey ? null : FS.keyOf(c);
                    // with a mask or a key the clip is drawn on a canvas of its own first, at the player's pixels, keyed
                    // and cut there, and coloured as it goes onto the picture (after the key, as the export does)
                    const into = mask || key ? masked(b) : null;
                    const g = into ? into.ctx : x;
                    const [dx, dy, dw, dh] = into ? [0, 0, into.w, into.h] : [-b.w / 2, -b.h / 2, b.w, b.h];
                    const look = colour(c);
                    if (look && !into) g.filter = look;
                    const [L, T, R, B] = b.crop;
                    if (L || T || R || B) {
                        const nw = src.videoWidth || src.naturalWidth, nh = src.videoHeight || src.naturalHeight;
                        g.drawImage(src, L * nw, T * nh, (1 - L - R) * nw, (1 - T - B) * nh, dx, dy, dw, dh);
                    } else g.drawImage(src, dx, dy, dw, dh);
                    g.filter = 'none';
                    if (into) {
                        if (key) {
                            const img = g.getImageData(0, 0, into.w, into.h);
                            FS.applyKey(key, img.data);
                            g.putImageData(img, 0, 0);
                        }
                        if (mask) {
                            g.globalCompositeOperation = 'destination-in';
                            g.drawImage(maskCanvas(mask, into.w, into.h), 0, 0);
                            g.globalCompositeOperation = 'source-over';
                        }
                        if (look) x.filter = look;
                        x.drawImage(into.canvas, -b.w / 2, -b.h / 2, b.w, b.h);
                        x.filter = 'none';
                    }
                }
            }
            x.restore();
            const dip = FS.dipAt(c, t);
            if (dip) {
                x.save();
                x.globalAlpha = dip.alpha;
                x.fillStyle = dip.colour;
                x.fillRect(0, 0, W, H);
                x.restore();
            }
        }
        // a whole picture: kept, to stay up while the next one is loading (before the selection frame goes on it)
        if (!loading && !P.clean) {
            const cv = x.canvas;
            if (!P.lastGood) P.lastGood = document.createElement('canvas');
            if (P.lastGood.width !== cv.width || P.lastGood.height !== cv.height) { P.lastGood.width = cv.width; P.lastGood.height = cv.height; }
            P.lastGood.getContext('2d').drawImage(cv, 0, 0);
        }
        // the selected clip's frame, for moving and sizing it in the picture
        if (!S.playing && !P.clean) {
            for (const id of S.selected) {
                const c = FS.clip(id);
                if (!c || !order.includes(c.track) || !active(c, t)) continue;
                const b = P.box(c, t);
                x.save();
                x.translate(b.cx, b.cy);
                x.rotate(b.angle);
                x.strokeStyle = '#e0902a';
                x.lineWidth = 2 / P.scale;
                x.setLineDash([8 / P.scale, 6 / P.scale]);
                x.strokeRect(-b.w / 2, -b.h / 2, b.w, b.h);
                x.setLineDash([]);
                x.fillStyle = '#e0902a';
                const k = 7 / P.scale;
                for (const [hx, hy] of [[-1, -1], [1, -1], [1, 1], [-1, 1]]) x.fillRect(hx * b.w / 2 - k, hy * b.h / 2 - k, 2 * k, 2 * k);
                x.restore();
            }
        }
        if (animating && !S.playing) {
            cancelAnimationFrame(P.raf);
            P.raf = requestAnimationFrame(() => P.draw());
        }
        FS.emit('drawn', t);
    };

    /* the frame at the playhead at the edit's full size, every layer as the player shows it (no selection frame):
     * the media are seeked there first */
    P.fullFrame = async function () {
        P.sync(true);
        const videos = Object.values(P.sources).map((s) => s.el).filter((el) => el.tagName === 'VIDEO');
        await Promise.all(videos.map((el) => (el.seeking || el.readyState < 2
            ? new Promise((r) => { const done = () => r(); el.addEventListener('seeked', done, {once: true}); el.addEventListener('loadeddata', done, {once: true}); setTimeout(done, 3000); })
            : null)));
        const {width: W, height: H} = S.doc.settings;
        const cv = document.createElement('canvas');
        cv.width = W;
        cv.height = H;
        const keep = {ctx: P.ctx, scale: P.scale};
        try {
            P.ctx = cv.getContext('2d');
            P.scale = 1;
            P.clean = true;
            P.draw();
        } finally {
            P.ctx = keep.ctx;
            P.scale = keep.scale;
            P.clean = false;
            P.draw();
        }
        return cv;
    };

    /* ---- keeping the media where the playhead is ---- */

    P.sync = function (exact) {
        if (!S.doc) return;
        const t = S.time;
        const keep = new Set();
        const mutedTracks = new Set(S.doc.tracks.filter((tr) => tr.muted).map((tr) => tr.id));
        const hiddenTracks = new Set(S.doc.tracks.filter((tr) => tr.hidden).map((tr) => tr.id));
        const talk = S.doc.clips.some((c) => c.duck > 0) ? FS.duckRanges() : [];
        for (const c of S.doc.clips) {
            if (c.kind !== 'video' && c.kind !== 'audio') continue;
            const soon = c.start - t < 2.0 && FS.end(c) - t > -1.0;
            if (!soon) continue;
            keep.add(c.id);
            const el = sourceOf(c);
            const on = active(c, t) && !hiddenTracks.has(c.track);
            const hold = !on && heldUnder(c, t);
            const target = hold ? Math.max(0, val(c, 'in') + FS.spanOf(c) - 1 / FS.fps()) : sourceTime(c, on ? t : c.start);
            el.muted = !!(val(c, 'muted') || mutedTracks.has(c.track));
            el.volume = Math.max(0, Math.min(1, Math.max(0, FS.at(c, 'volume', t)) *(on ? fade(c, t, true) * FS.duckGain(c, t, talk) * FS.soundGain(c, t) : 1)));
            if (S.playing && on) {
                el.playbackRate = Math.max(0.0625, Math.min(16, FS.ramped(c) ? FS.at(c, 'speed', t) : val(c, 'speed')));
                if (Math.abs(el.currentTime - target) > (el.paused ? 0.05 : 0.25)) el.currentTime = target;
                if (el.paused) el.play().catch(() => null);
            } else {
                if (!el.paused) el.pause();
                if (exact || Math.abs(el.currentTime - target) > 0.02) {
                    if (el.readyState >= 1) el.currentTime = target;
                }
            }
        }
        // a soft cut: the clip before goes on under the new sound from its own file, in a sound element of its own
        // (its picture stays held on the last frame meanwhile, as the export draws it)
        for (const c of S.doc.clips) {
            if (c.kind !== 'video' && c.kind !== 'audio') continue;
            const end = FS.end(c);
            if (t < end - 2 || t > end + 4) continue;
            const out = FS.soundOut(c);
            if (!(out.tail > 0)) continue;
            const id = c.id + '~tail';
            keep.add(id);
            const el = sourceOf({kind: 'audio', id, media: c.media});
            const inTail = t >= end - 1e-6 && t < end + out.tail;
            const target = val(c, 'in') + Math.max(0, Math.min(t, end + out.tail) - c.start) * val(c, 'speed');
            el.muted = false;
            el.volume = Math.max(0, Math.min(1, Math.max(0, FS.at(c, 'volume', end)) * FS.duckGain(c, Math.min(t, end - 1e-3), talk) * FS.soundGain(c, t)
                * (val(c, 'fadeOut') > 0 ? 0 : 1)));
            if (S.playing && inTail) {
                el.playbackRate = Math.max(0.0625, Math.min(16, val(c, 'speed')));
                if (Math.abs(el.currentTime - target) > (el.paused ? 0.05 : 0.25)) el.currentTime = target;
                if (el.paused) el.play().catch(() => null);
            } else {
                if (!el.paused) el.pause();
                if (Math.abs(el.currentTime - target) > 0.02 && el.readyState >= 1) el.currentTime = target;
            }
        }
        for (const id of Object.keys(P.sources)) {
            const c = FS.clip(id.replace(/~tail$/, ''));
            if (!c || !keep.has(id)) {
                if (!c || Math.abs(c.start - t) > 30) release(id);
                else if (!P.sources[id].el.paused) P.sources[id].el.pause();
            }
        }
    };

    /* ---- playing ---- */

    function tick() {
        if (!S.playing) return;
        const now = performance.now();
        S.time = P.clock.t0 + (now - P.clock.at) / 1000;
        const end = FS.total();
        if (S.time >= end) {
            S.time = end;
            P.pause();
            FS.emit('time', S.time);
            return;
        }
        P.sync(false);
        P.draw();
        FS.emit('time', S.time);
        P.raf = requestAnimationFrame(tick);
    }

    P.play = function () {
        if (S.playing || !S.doc) return;
        if (S.time >= FS.total() - 1 / FS.fps()) S.time = 0;
        S.playing = true;
        P.clock = {t0: S.time, at: performance.now()};
        P.sync(false);
        FS.emit('playing', true);
        cancelAnimationFrame(P.raf);
        P.raf = requestAnimationFrame(tick);
    };

    P.pause = function () {
        if (!S.playing) return;
        S.playing = false;
        cancelAnimationFrame(P.raf);
        S.time = FS.snapTime(S.time);
        P.sync(true);
        P.draw();
        FS.emit('playing', false);
    };

    P.toggle = () => (S.playing ? P.pause() : P.play());

    FS.on('time', () => { if (!S.playing) { P.sync(true); P.draw(); } });
    FS.on('doc', () => { if (!S.playing) { P.sync(false); P.draw(); } });
    FS.on('select', () => P.draw());
    FS.on('project', () => {
        for (const id of Object.keys(P.sources)) release(id);
        P.images = {};
        P.texts = {};
        P.resize();
        P.sync(true);
    });
})();

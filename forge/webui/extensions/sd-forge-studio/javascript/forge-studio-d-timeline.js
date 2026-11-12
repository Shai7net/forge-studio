/* Forge Studio - the timeline (always left to right, also in Hebrew). Visual tracks are shown top to bottom from
 * the highest layer to the main track; audio tracks under them. A clip shows its own frames (a strip of the whole
 * file, laid so the trimmed part shows) or its sound's outline. Drag a clip to move it (also to another track, or
 * to the empty row above or below for a new track), drag its edges to trim, click the ruler to move the playhead.
 * Edges snap to the playhead and to other clips. Each cut on the main track has a ✨ for what AI can do there.
 */
(function () {
    'use strict';
    const FS = window.ForgeStudio = window.ForgeStudio || {};
    const S = FS.state;
    const T = (...a) => FS.T(...a);
    const val = (c, k) => FS.val(c, k);
    const TL = FS.timeline = {pps: 60, el: null, snap: true};
    const HEAD = 116, RULER = 26, SNAP_PX = 8;
    const heightOf = (track) => (track.kind === 'audio' ? 46 : track.main ? 70 : 54);

    FS.clipActions = FS.clipActions || [];   // [{id, label, when(clip), run(clip)}] for the clip menu
    FS.cutActions = FS.cutActions || [];     // [{label, run(left, right)}] for the ✨ at a cut

    function h(tag, attrs, ...children) { return FS.h(tag, attrs, ...children); }

    /* ---- building ---- */

    TL.build = function () {
        const root = TL.el = h('div', {class: 'ft', dir: 'ltr'});
        TL.toolbar = toolbar();
        TL.rulerCanvas = h('canvas', {class: 'ft-ruler-canvas'});
        TL.rulerView = h('div', {class: 'ft-ruler-view'}, TL.rulerCanvas);
        TL.heads = h('div', {class: 'ft-heads'});
        TL.content = h('div', {class: 'ft-content'});
        TL.playhead = h('div', {class: 'ft-playhead'}, h('div', {class: 'ft-playhead-cap'}));
        TL.scroll = h('div', {class: 'ft-scroll'}, TL.content, TL.playhead);
        TL.body = h('div', {class: 'ft-body'}, TL.heads, TL.scroll);
        root.append(TL.toolbar, h('div', {class: 'ft-ruler-row'}, h('div', {class: 'ft-corner', style: `width:${HEAD}px`}, TL.timecodeEl = h('span', {class: 'ft-tc'})), TL.rulerView), TL.body);
        // the clips scroll both ways in one place (so the bar for sideways stays in sight); the track names follow up and down
        TL.scroll.addEventListener('scroll', () => { drawRuler(); TL.heads.style.transform = `translateY(${-TL.scroll.scrollTop}px)`; });
        TL.heads.addEventListener('wheel', (e) => { if (!e.ctrlKey) { e.preventDefault(); TL.scroll.scrollTop += e.deltaY; } }, {passive: false});
        TL.scroll.addEventListener('wheel', (e) => {
            if (e.ctrlKey || e.metaKey) {
                e.preventDefault();
                zoomAt(e.deltaY < 0 ? 1.18 : 1 / 1.18, e.clientX);
            } else if (!e.shiftKey && Math.abs(e.deltaY) > Math.abs(e.deltaX) && TL.scroll.scrollHeight <= TL.scroll.clientHeight + 2) {
                e.preventDefault();
                TL.scroll.scrollLeft += e.deltaY;
            }
        }, {passive: false});
        TL.rulerView.addEventListener('pointerdown', scrubStart);
        TL.content.addEventListener('pointerdown', (e) => {
            if (!e.target.closest('.ft-clip,.ft-cut,.ft-menu')) marqueeStart(e);
        });
        TL.content.addEventListener('contextmenu', (e) => {
            if (e.target.closest('.ft-clip')) return;
            e.preventDefault();
            if (FS.region()) TL.regionMenu(e.clientX, e.clientY);
        });
        new ResizeObserver(() => { drawRuler(); placePlayhead(); }).observe(TL.scroll);
        return root;
    };

    function toolbar() {
        const btn = (icon, title, fn, cls) => h('button', {class: 'ft-btn ' + (cls || ''), title, onclick: FS.guard(fn)}, icon);
        TL.magnetBtn = btn('🧲', T('Magnetic main track: no gaps'), () => { FS.magnet = !FS.magnet; TL.magnetBtn.classList.toggle('on', FS.magnet); if (FS.magnet) FS.change('magnet', () => FS.closeGaps()); }, 'on');
        TL.snapBtn = btn('⌖', T('Snap to edges and the playhead'), () => { TL.snap = !TL.snap; TL.snapBtn.classList.toggle('on', TL.snap); }, 'on');
        TL.zoom = h('input', {type: 'range', min: 0, max: 100, value: 40, class: 'ft-zoom', title: T('Zoom')});
        TL.zoom.addEventListener('input', () => setZoom(zoomFromSlider(+TL.zoom.value)));
        TL.regionButton = btn('▧', T('Generate in the selected range'), (e) => {
            const r = TL.regionButton.getBoundingClientRect(); TL.regionMenu(r.left, r.bottom);
        });
        return h('div', {class: 'ft-toolbar'},
            btn('↶', T('Undo (Ctrl+Z)'), () => FS.undo()),
            btn('↷', T('Redo (Ctrl+Y)'), () => FS.redo()),
            h('span', {class: 'ft-sep'}),
            btn('✂', T('Split at the playhead (S)'), () => TL.split()),
            btn('🗑', T('Delete (Del)'), () => S.selected.length && FS.remove(S.selected)),
            btn('⧉', T('Duplicate (Ctrl+D)'), () => TL.duplicate()),
            h('span', {class: 'ft-sep'}),
            btn('🖼＋', T('New image shot at the playhead: an empty picture to draw, generate or put pictures on'), () => FS.imageShop.create()),
            h('span', {class: 'ft-sep'}),
            TL.magnetBtn, TL.snapBtn, TL.regionButton,
            h('span', {class: 'ft-spacer'}),
            btn('－', T('Zoom out'), () => zoomAt(1 / 1.4)),
            TL.zoom,
            btn('＋', T('Zoom in'), () => zoomAt(1.4)),
            btn('⤢', T('Fit the whole edit'), () => TL.fit()));
    }

    const zoomFromSlider = (v) => 4 * Math.pow(500 / 4, v / 100);
    const sliderFromZoom = (pps) => Math.round(100 * Math.log(pps / 4) / Math.log(500 / 4));

    function setZoom(pps, anchorX) {
        const old = TL.pps;
        const rect = TL.scroll.getBoundingClientRect();
        const ax = anchorX != null ? anchorX - rect.left : rect.width / 2;
        const tAt = (TL.scroll.scrollLeft + ax) / old;
        TL.pps = Math.max(4, Math.min(500, pps));
        TL.zoom.value = sliderFromZoom(TL.pps);
        TL.render();
        TL.scroll.scrollLeft = Math.max(0, tAt * TL.pps - ax);
        drawRuler();
    }
    function zoomAt(factor, x) { setZoom(TL.pps * factor, x); }
    TL.fit = function () {
        const w = TL.scroll.clientWidth - 40;
        setZoom(w / Math.max(5, FS.total() * 1.05));
        TL.scroll.scrollLeft = 0;
    };

    /* ---- drawing ---- */

    function orderedTracks() {
        return FS.visualTracks().slice().reverse().concat(FS.audioTracks());
    }

    function trackLabel(track) {
        if (track.kind === 'audio') return 'A' + (FS.audioTracks().indexOf(track) + 1);
        const visual = FS.visualTracks();
        return track.main ? T('Main') : 'V' + (visual.indexOf(track) + 1);
    }

    TL.render = function () {
        if (!TL.el || !S.doc) return;
        const tracks = orderedTracks();
        const width = Math.max(TL.scroll.clientWidth, (FS.total() + 30) * TL.pps);
        TL.content.style.width = width + 'px';
        TL.heads.innerHTML = '';
        TL.content.innerHTML = '';
        TL.lanes = [];
        let y = 0;
        // an empty row on top: drop here for a new layer
        const topDrop = h('div', {class: 'ft-lane ft-new', style: `top:${y}px;height:22px`}, h('span', {}, T('Drop here for a new layer')));
        TL.content.append(topDrop);
        TL.heads.append(h('div', {class: 'ft-head ft-head-new', style: 'height:22px'}));
        TL.lanes.push({track: null, kind: 'video', top: y, height: 22, el: topDrop, newTrack: 'top'});
        y += 22;
        for (const track of tracks) {
            const hgt = heightOf(track);
            const lane = h('div', {class: `ft-lane ft-${track.kind}${track.main ? ' ft-main' : ''}`, style: `top:${y}px;height:${hgt}px`});
            TL.content.append(lane);
            TL.lanes.push({track, kind: track.kind, top: y, height: hgt, el: lane});
            TL.heads.append(head(track, hgt));
            for (const c of FS.clipsOn(track.id)) lane.append(clipEl(c, hgt));
            if (track.main) for (const cut of cuts(track)) lane.append(cut);
            y += hgt;
        }
        const bottomDrop = h('div', {class: 'ft-lane ft-new', style: `top:${y}px;height:22px`}, h('span', {}, T('Drop here for a new sound track')));
        TL.content.append(bottomDrop);
        TL.heads.append(h('div', {class: 'ft-head ft-head-new', style: 'height:22px'}));
        TL.lanes.push({track: null, kind: 'audio', top: y, height: 22, el: bottomDrop, newTrack: 'bottom'});
        y += 22;
        for (const m of FS.markers()) TL.content.append(h('div', {class: 'ft-marker', title: m.name || FS.timecode(m.t), style: `left:${m.t * TL.pps}px;height:${y}px`}));
        TL.content.style.height = y + 'px';
        TL.heads.style.height = y + 'px';
        TL.playhead.style.height = y + 'px';
        drawRegion();
        placePlayhead();
        drawRuler();
    };

    function head(track, hgt) {
        const icon = track.kind === 'audio' ? '♪' : track.main ? '▣' : '▤';
        const toggle = (key, on, off, title) => h('button', {class: 'ft-hbtn' + (track[key] ? ' off' : ''), title,
            onclick: () => FS.change(key, () => { FS.track(track.id)[key] = !track[key]; })}, track[key] ? off : on);
        return h('div', {class: 'ft-head', style: `height:${hgt}px;width:${HEAD}px`},
            h('span', {class: 'ft-hicon'}, icon), h('span', {class: 'ft-hname'}, trackLabel(track)),
            track.kind === 'audio' ? toggle('muted', '🔊', '🔇', T('Mute')) :
                h('span', {}, toggle('hidden', '👁', '◌', T('Hide')), toggle('muted', '🔊', '🔇', T('Mute'))));
    }

    function clipEl(c, hgt) {
        const sel = FS.linkedIds(S.selected).includes(c.id);
        const el = h('div', {class: `ft-clip ft-${c.kind}${sel ? ' sel' : ''}`, 'data-id': c.id,
            style: `left:${c.start * TL.pps}px;width:${Math.max(2, c.duration * TL.pps)}px;height:${hgt - 6}px`});
        const info = S.info[c.media];
        const speed = val(c, 'speed');
        if ((c.kind === 'video' || c.kind === 'image') && c.media) {
            const strip = h('div', {class: 'ft-strip'});
            if (c.kind === 'video' && info && info.duration) {
                strip.style.backgroundImage = `url("${FS.stripUrl(c.media)}")`;
                strip.style.backgroundSize = `${info.duration / speed * TL.pps}px 100%`;
                strip.style.backgroundPosition = `${-val(c, 'in') / speed * TL.pps}px 0`;
            } else {
                strip.style.backgroundImage = `url("${FS.stripUrl(c.media)}")`;
                strip.style.backgroundSize = 'auto 100%';
                strip.style.backgroundRepeat = 'repeat-x';
            }
            el.append(strip);
        }
        if ((c.kind === 'audio' || (c.kind === 'video' && info && info.audio)) && c.media) {
            const wave = h('div', {class: 'ft-wave' + (c.kind === 'video' ? ' ft-wave-under' : '')});
            if (info && info.duration) {
                wave.style.backgroundImage = `url("${FS.waveUrl(c.media)}")`;
                wave.style.backgroundSize = `${info.duration / speed * TL.pps}px 100%`;
                wave.style.backgroundPosition = `${-val(c, 'in') / speed * TL.pps}px 0`;
            }
            el.append(wave);
            if (c.beats) {
                if (!FS.beats[c.media]) FS.loadBeats(c.media).then(() => TL.render()).catch(() => null);
                for (const b of FS.beatTimes(c)) {
                    el.append(h('div', {class: 'ft-beat' + (b.one ? ' ft-beat-one' : ''), style: `left:${(b.t - c.start) * TL.pps}px`}));
                }
            }
            if (FS.keysOf(c, 'volume')) {
                // the volume as it moves between its keyframes: 100% halfway up, 200% at the top
                const ns = 'http://www.w3.org/2000/svg', w = c.duration * TL.pps, n = Math.max(2, Math.min(400, Math.round(w / 4)));
                const pts = [];
                for (let i = 0; i <= n; i++) {
                    const lt = c.duration * i / n;
                    pts.push(`${(lt * TL.pps).toFixed(1)},${(100 - Math.min(2, Math.max(0, FS.at(c, 'volume', c.start + lt))) * 50).toFixed(1)}`);
                }
                const svg = document.createElementNS(ns, 'svg');
                svg.setAttribute('class', 'ft-vol');
                svg.setAttribute('viewBox', `0 0 ${w.toFixed(1)} 100`);
                svg.setAttribute('preserveAspectRatio', 'none');
                svg.style.width = `${w}px`;
                const line = document.createElementNS(ns, 'polyline');
                line.setAttribute('points', pts.join(' '));
                line.setAttribute('vector-effect', 'non-scaling-stroke');
                svg.append(line);
                el.append(svg);
            }
            if (c.duck > 0) {
                // where the sound steps back under speech: its volume line, the part taken away darkened
                const ns = 'http://www.w3.org/2000/svg', depth = (1 - c.duck) * 100;
                for (const [a, b] of FS.duckRanges()) {
                    const x0 = a - FS.DUCK.attack, x1 = b + FS.DUCK.release;
                    const x = Math.max(c.start, x0), y = Math.min(FS.end(c), x1);
                    if (y <= x) continue;
                    const w = (y - x) * TL.pps;
                    const px = (t) => ((t - x) * TL.pps).toFixed(1);
                    const pts = [[x0, 0], [a, depth], [b, depth], [x1, 0]].map(([t, d]) => `${px(t)},${d.toFixed(1)}`).join(' ');
                    const svg = document.createElementNS(ns, 'svg');
                    svg.setAttribute('class', 'ft-duck');
                    svg.setAttribute('viewBox', `0 0 ${w.toFixed(1)} 100`);
                    svg.setAttribute('preserveAspectRatio', 'none');
                    svg.style.left = `${(x - c.start) * TL.pps}px`;
                    svg.style.width = `${w}px`;
                    const shade = document.createElementNS(ns, 'polygon');
                    shade.setAttribute('points', `${px(x0)},0 ${pts} ${px(x1)},0`);
                    const line = document.createElementNS(ns, 'polyline');
                    line.setAttribute('points', pts);
                    line.setAttribute('vector-effect', 'non-scaling-stroke');
                    const tip = document.createElementNS(ns, 'title');
                    tip.textContent = T('Lowered under speech');
                    svg.append(tip, shade, line);
                    el.append(svg);
                }
            }
        }
        let label = c.media || '';
        if (c.kind === 'text') label = '𝐓 ' + ((c.text && (c.text.text || c.text.sub)) || T('Text'));
        if (c.kind === 'gen') label = '✨ ' + T('Being made') + ((c.gen && c.gen.progress != null) ? ` ${Math.round(c.gen.progress * 100)}%` : '');
        const badges = [];
        if(c.linkedTo||S.doc.clips.some(a=>a.linkedTo===c.id))badges.push('🔗');
        if (Math.abs(speed - 1) > 1e-3) badges.push(h('span', {class: 'ft-badge'}, `${+speed.toFixed(2)}×`));
        if (c.reversed) badges.push(h('span', {class: 'ft-badge', title: T('Played backwards')}, '⏪'));
        if (c.lips) badges.push(h('span', {class: 'ft-badge', title: T('Lip sync')}, '👄'));
        if (c.freeze) badges.push(h('span', {class: 'ft-badge', title: T('Freeze frame')}, '❄'));
        if (c.takes && c.takes.length > 1) badges.push(h('span', {class: 'ft-badge ft-takes', title: T('Takes: click to switch')}, `🎞 ${c.takes.indexOf(c.media) + 1}/${c.takes.length}`));
        const keyTimes = new Set();
        for (const k of FS.KEYED.concat(FS.SOUND_KEYED)) for (const [kt] of FS.keysOf(c, k) || []) keyTimes.add(Math.round(kt * 1000) / 1000);
        for (const kt of keyTimes) {
            const dot = h('div', {class: 'ft-key', title: T('Keyframe'), style: `left:${kt * TL.pps}px`});
            dot.addEventListener('pointerdown', (e) => { e.stopPropagation(); FS.select([c.id]); FS.seek(c.start + kt); });
            el.append(dot);
        }
        if (c.transitionIn > 0) {
            const kind = (c.transition || 'dissolve').split(':')[0];
            const mark = {black: '⬛', white: '⬜', slide: '⇤', push: '⇇'}[kind] || '';
            el.append(h('div', {class: 'ft-dissolve ft-trans-' + kind, title: FS.transitionName ? FS.transitionName(c) : T('Dissolve from the clip before'),
                style: `width:${c.transitionIn * TL.pps}px`}, mark));
        }
        else if (val(c, 'fadeIn') > 0) el.append(h('div', {class: 'ft-fade ft-fade-in', style: `width:${val(c, 'fadeIn') * TL.pps}px`}));
        if (val(c, 'fadeOut') > 0) el.append(h('div', {class: 'ft-fade ft-fade-out', style: `width:${val(c, 'fadeOut') * TL.pps}px`}));
        el.append(h('div', {class: 'ft-label', dir: 'auto'}, ...badges, h('span', {class: 'ft-name'}, label)),
            h('div', {class: 'ft-handle ft-l'}), h('div', {class: 'ft-handle ft-r'}));
        el.addEventListener('pointerdown', (e) => clipPointer(e, c));
        el.addEventListener('contextmenu', (e) => { e.preventDefault(); if (!S.selected.includes(c.id)) FS.select([c.id]); TL.menu(e.clientX, e.clientY, c); });
        el.addEventListener('dblclick', () => FS.emit('inspect', c.id));
        const takes = el.querySelector('.ft-takes');
        if (takes) takes.addEventListener('pointerdown', (e) => { e.stopPropagation(); TL.nextTake(c); });
        return el;
    }

    /* the ✨ between two clips of the main track */
    function cuts(track) {
        const clips = FS.clipsOn(track.id);
        const out = [];
        for (let i = 0; i < clips.length; i++) {
            const left = clips[i], right = clips[i + 1] || null;
            const at = FS.end(left);
            const b = h('button', {class: 'ft-cut' + (right ? '' : ' ft-cut-end'), style: `left:${at * TL.pps}px`,
                title: right ? T('AI at this cut') : T('AI: what comes next')}, '✨');
            b.addEventListener('pointerdown', (e) => e.stopPropagation());
            b.addEventListener('click', (e) => {
                e.stopPropagation();
                const items = FS.cutActions.filter((a) => !a.when || a.when(left, right));
                TL.menu(e.clientX, e.clientY, null, items.map((a) => ({label: a.label, run: () => a.run(left, right)})));
            });
            out.push(b);
        }
        return out;
    }

    function drawRuler() {
        if (!TL.rulerCanvas) return;
        const w = TL.rulerView.clientWidth, dpr = window.devicePixelRatio || 1;
        const cv = TL.rulerCanvas;
        cv.width = Math.max(1, Math.round(w * dpr));
        cv.height = Math.round(RULER * dpr);
        cv.style.width = w + 'px';
        cv.style.height = RULER + 'px';
        const x = cv.getContext('2d');
        x.setTransform(dpr, 0, 0, dpr, 0, 0);
        x.clearRect(0, 0, w, RULER);
        const left = TL.scroll.scrollLeft;
        const steps = [1 / 30, 0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300];
        const major = steps.find((s) => s * TL.pps >= 70) || 300;
        const minor = major / (major >= 1 ? (major % 5 === 0 ? 5 : 4) : 5);
        const style = getComputedStyle(TL.el);
        x.strokeStyle = style.getPropertyValue('--ft-tick') || '#556';
        x.fillStyle = style.getPropertyValue('--ft-text') || '#aab';
        x.font = '10px "Segoe UI", sans-serif';
        const t0 = Math.floor(left / TL.pps / minor) * minor;
        for (let t = t0; t * TL.pps - left < w; t += minor) {
            const px = Math.round(t * TL.pps - left) + 0.5;
            const isMajor = Math.abs(t / major - Math.round(t / major)) < 1e-6;
            x.beginPath();
            x.moveTo(px, RULER);
            x.lineTo(px, isMajor ? RULER - 12 : RULER - 5);
            x.stroke();
            if (isMajor) x.fillText(major < 1 ? FS.timecode(t) : FS.timecode(t, false), px + 3, 11);
        }
        // the marked part (I / O): a band along the ruler with its two edges
        const part = FS.range();
        if (part) {
            const x0 = part.in * TL.pps - left, x1 = part.out * TL.pps - left;
            x.fillStyle = 'rgba(80, 170, 255, .22)';
            x.fillRect(x0, 0, x1 - x0, RULER);
            x.fillStyle = '#50aaff';
            x.fillRect(Math.round(x0), 0, 2, RULER);
            x.fillRect(Math.round(x1) - 2, 0, 2, RULER);
        }
        // markers: a small flag with its name
        for (const m of FS.markers()) {
            const px = Math.round(m.t * TL.pps - left) + 0.5;
            if (px < -60 || px > w + 2) continue;
            x.fillStyle = '#ff9f43';
            x.beginPath();
            x.moveTo(px - 5, RULER - 9);
            x.lineTo(px + 5, RULER - 9);
            x.lineTo(px, RULER);
            x.closePath();
            x.fill();
            if (m.name) { x.fillStyle = '#ffcf99'; x.fillText(m.name, px + 6, RULER - 2); }
        }
    }

    function placePlayhead() {
        if (!TL.playhead) return;
        TL.playhead.style.left = (S.time * TL.pps) + 'px';
        if (TL.timecodeEl) TL.timecodeEl.textContent = FS.timecode(S.time);
        if (S.playing) {
            const x = S.time * TL.pps - TL.scroll.scrollLeft;
            if (x > TL.scroll.clientWidth * 0.85 || x < 0) TL.scroll.scrollLeft = S.time * TL.pps - TL.scroll.clientWidth * 0.15;
        }
    }

    /* ---- pointer work ---- */

    function timeAt(clientX) {
        const rect = TL.scroll.getBoundingClientRect();
        return Math.max(0, (clientX - rect.left + TL.scroll.scrollLeft) / TL.pps);
    }

    function laneAt(clientY) {
        const rect = TL.content.getBoundingClientRect();
        const y = clientY - rect.top;
        return TL.lanes.find((l) => y >= l.top && y < l.top + l.height) || (y < 0 ? TL.lanes[0] : TL.lanes[TL.lanes.length - 1]);
    }

    function scrubStart(e) {
        if (e.button !== 0) return;
        if (S.playing) FS.player.pause();
        // the playhead is pulled onto a marker it passes close to
        const toMarker = (t) => { for (const m of FS.markers()) if (Math.abs(m.t - t) * TL.pps < SNAP_PX) return m.t; return t; };
        const move = (ev) => FS.seek(FS.snapTime(toMarker(timeAt(ev.clientX))));
        move(e);
        const up = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); };
        window.addEventListener('pointermove', move);
        window.addEventListener('pointerup', up);
    }

    function drawRegion() {
        if (!TL.content) return;
        TL.content.querySelectorAll('.ft-region').forEach((el) => el.remove());
        const r = FS.region();
        if (TL.regionButton) {
            TL.regionButton.disabled = !r;
            TL.regionButton.textContent = r ? '▧ ' + (r.out - r.in).toFixed(2) + 's · ✨' : '▧';
        }
        if (!r || TL.marquee) return;
        const lanes = TL.lanes.filter((l) => l.track ? r.tracks.includes(l.track.id) : r.newTrack === l.newTrack && r.kind === l.kind);
        for (const l of lanes) TL.content.append(h('div', {class: 'ft-region',
            style: `left:${r.in * TL.pps}px;width:${(r.out - r.in) * TL.pps}px;top:${l.top}px;height:${l.height}px`}));
    }

    TL.regionMenu = function (x, y) {
        const r = FS.region();
        if (!r) return;
        const region = JSON.parse(JSON.stringify(r));
        const audio = region.kind === 'audio';
        TL.menu(x, y, null, [
            !audio && FS.gen ? {label: '🎬 ' + T('Generate video in the selected range'), run: () => FS.gen.inRegion(region)} : null,
            !audio && FS.openRangeLips ? {label: '👄 ' + T('Lip sync in the selected range'), run: () => FS.openRangeLips(region)} : null,
            audio && FS.sound ? {label: '🔊 ' + T('Generate sound effects in the selected range'), run: () => FS.sound.inRegion('effects', region)} : null,
            audio && FS.sound ? {label: '🎵 ' + T('Generate music in the selected range'), run: () => FS.sound.inRegion('music', region)} : null,
            S.selected.length ? {label: '🗑 ' + T('Delete selected clips'), run: () => FS.remove(S.selected)} : null,
            {label: '✕ ' + T('Clear the selected range'), run: () => FS.clearRegion()},
        ]);
    };

    function marqueeStart(e) {
        if (e.button !== 0 || !S.doc) return;
        e.preventDefault();
        TL.closeMenu();
        if (S.playing) FS.player.pause();
        const project = S.project, startLane = laneAt(e.clientY);
        const origin = {x: timeAt(e.clientX) * TL.pps, y: e.clientY - TL.content.getBoundingClientRect().top};
        const base = e.ctrlKey || e.metaKey || e.shiftKey ? S.selected.slice() : [];
        let moved = false, last = e;
        const box = h('div', {class: 'ft-marquee'});
        TL.marquee = box;
        const update = (ev) => {
            last = ev;
            if (S.project !== project) { up(null, true); return; }
            const x = timeAt(ev.clientX) * TL.pps, y = ev.clientY - TL.content.getBoundingClientRect().top;
            if (!moved && Math.abs(x - origin.x) + Math.abs(y - origin.y) < 4) return;
            moved = true;
            const left = Math.min(x, origin.x), right = Math.max(x, origin.x), top = Math.min(y, origin.y), bottom = Math.max(y, origin.y);
            const lanes = TL.lanes.filter((l) => l.top < bottom && l.top + l.height > top);
            const ids = lanes.filter((l) => l.track).map((l) => l.track.id);
            const selected = S.doc.clips.filter((c) => {
                const lane = lanes.find((l) => l.track && l.track.id === c.track);
                return lane && lane.top + 3 < bottom && lane.top + lane.height - 3 > top &&
                    c.start * TL.pps < right && FS.end(c) * TL.pps > left;
            }).map((c) => c.id);
            FS.select([...new Set(base.concat(selected))]);
            FS.setRegion(left / TL.pps, right / TL.pps, ids, startLane.kind, startLane.newTrack);
            box.style.cssText = `left:${left}px;width:${right - left}px;top:${Math.max(0, top)}px;height:${bottom - Math.max(0, top)}px`;
            TL.content.append(box);
        };
        const move = (ev) => update(ev);
        const scroll = setInterval(() => {
            if (!moved) return;
            const r = TL.scroll.getBoundingClientRect(), border = 30;
            const dx = last.clientX > r.right - border ? 16 : last.clientX < r.left + border ? -16 : 0;
            const dy = last.clientY > r.bottom - border ? 10 : last.clientY < r.top + border ? -10 : 0;
            if (dx || dy) { TL.scroll.scrollLeft += dx; TL.scroll.scrollTop += dy; update(last); }
        }, 50);
        const key = (ev) => { if (ev.key === 'Escape') up(null, true); };
        const up = (ev, cancelled) => {
            clearInterval(scroll);
            window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up);
            window.removeEventListener('pointercancel', cancel); window.removeEventListener('keydown', key);
            if (TL.cancelPointer === cancel) TL.cancelPointer = null;
            box.remove(); TL.marquee = null;
            if (cancelled) { FS.clearRegion(); FS.select(base); }
            else if (!moved) { FS.clearRegion(); FS.select(base); FS.seek(FS.snapTime(origin.x / TL.pps)); }
            drawRegion();
        };
        const cancel = () => up(null, true);
        TL.cancelPointer = cancel;
        window.addEventListener('pointermove', move); window.addEventListener('pointerup', up);
        window.addEventListener('pointercancel', cancel); window.addEventListener('keydown', key);
    }

    /* the nearest snapping point within SNAP_PX of time t (or t itself) */
    /* skipTrack: the clips of that track are not points (on the magnetic track they move while one is trimmed) */
    function snapped(t, ignoreId, skipTrack) {
        if (!TL.snap) return t;
        let best = t, dist = SNAP_PX / TL.pps;
        const points = [0, S.time].concat(FS.markers().map((m) => m.t));
        for (const c of S.doc.clips) {
            if (c.id !== ignoreId && c.track !== skipTrack) points.push(c.start, FS.end(c));
            for (const b of FS.beatTimes(c)) points.push(b.t);   // edges snap to the music's beats
        }
        for (const p of points) if (Math.abs(p - t) < dist) { dist = Math.abs(p - t); best = p; }
        return best;
    }

    function clipPointer(e, c) {
        if (e.button !== 0) return;
        e.stopPropagation();
        FS.clearRegion();
        if (S.playing) FS.player.pause();
        const handle = e.target.classList.contains('ft-l') ? 'l' : e.target.classList.contains('ft-r') ? 'r' : null;
        if(handle&&FS.audioParent(c))c=FS.audioParent(c);
        // Ctrl-click picks clips one by one (again: leaves it out), Shift-click adds, as with files
        if (e.ctrlKey || e.metaKey) { FS.select(S.selected.includes(c.id) ? S.selected.filter((id) => id !== c.id) : S.selected.concat(c.id)); return; }
        if (!S.selected.includes(c.id) || e.shiftKey) FS.select(e.shiftKey ? S.selected.concat(c.id) : [c.id]);
        const x0 = e.clientX, y0 = e.clientY;
        const orig = JSON.parse(JSON.stringify(c));
        const group = FS.linkedIds(S.selected).map((id) => FS.clip(id)).filter(Boolean).map((x) => JSON.parse(JSON.stringify(x)));
        const linkedGroup=group.length>1&&FS.linkedRoots(S.selected).length===1;
        let moved = false, extension = 0, extensionClip = null;
        const track = FS.track(c.track);
        const move = (ev) => {
            const dx = (ev.clientX - x0) / TL.pps;
            if (!moved && Math.abs(ev.clientX - x0) + Math.abs(ev.clientY - y0) < 4) return;
            if (!moved) { moved = true; FS.beginEdit(); }
            const clip = FS.clip(c.id);
            extension = 0;
            if (handle === 'l') {
                const minIn = clip.kind === 'video' || clip.kind === 'audio' ? -val(orig, 'in') / val(orig, 'speed') : -Infinity;
                let d = Math.max(minIn, Math.min(orig.duration - 1 / FS.fps(), dx));
                extension = orig.kind === 'video' && Number.isFinite(minIn) ? Math.max(0, minIn - dx) : 0;
                if (!(track.main && FS.magnet)) d = snapped(orig.start + d, c.id) - orig.start;
                d = Math.max(minIn, Math.min(orig.duration - 1 / FS.fps(), d));
                // the file second the new start shows (a ramp's own sums inside the clip; its first speed before it)
                clip.in = d >= 0 ? FS.sourceTime(orig, orig.start + d) : val(orig, 'in') + d * FS.at(orig, 'speed', orig.start);
                clip.duration = orig.duration - d;
                if (orig.keys) { clip.keys = JSON.parse(JSON.stringify(orig.keys)); FS.moveKeys(clip, d); }
                if (!(track.main && FS.magnet)) clip.start = orig.start + d;
                if (track.main && FS.magnet) FS.closeGaps();
                extensionClip = JSON.parse(JSON.stringify(clip));
            } else if (handle === 'r') {
                let end = orig.start + orig.duration + dx;
                end = track.main && FS.magnet ? snapped(end, c.id, c.track) : snapped(end, c.id);
                clip.duration = Math.max(1 / FS.fps(), Math.min(FS.room(orig), end - orig.start));
                extension = orig.kind === 'video' && Number.isFinite(FS.room(orig)) ? Math.max(0, end - orig.start - FS.room(orig)) : 0;
                if (track.main && FS.magnet) FS.closeGaps();
                extensionClip = JSON.parse(JSON.stringify(clip));
            } else {
                let start = Math.max(0, orig.start + dx);
                const s1 = snapped(start, c.id), s2 = snapped(start + orig.duration, c.id) - orig.duration;
                start = Math.abs(s1 - start) <= Math.abs(s2 - start) ? s1 : s2;
                clip.start = Math.max(0, start);
                delete clip.fixedStart;
                if (group.length > 1) {
                    const d = Math.max(-Math.min(...group.map((x) => x.start)), clip.start - orig.start);
                    for (const g of group) { const item = FS.clip(g.id); item.start = g.start + d; delete item.fixedStart; }
                    if(linkedGroup){const lane=laneAt(ev.clientY);TL.dropLane=lane;if(lane.track&&lane.kind===(clip.kind==='audio'?'audio':'video'))clip.track=lane.track.id;}
                    FS.liveEdit(); return;
                }
                const lane = laneAt(ev.clientY);
                TL.dropLane = lane;
                TL.lanes.forEach((l) => l.el.classList.toggle('ft-target', l === lane && l.kind === (clip.kind === 'audio' ? 'audio' : 'video')));
                if (lane.track && lane.kind === (clip.kind === 'audio' ? 'audio' : 'video')) clip.track = lane.track.id;
            }
            FS.liveEdit();
            if (extension >= 1 / FS.fps() && extensionClip) {
                const lane = TL.lanes.find((l) => l.track && l.track.id === clip.track);
                if (lane) TL.content.append(h('div', {class: 'ft-extension',
                    style: `left:${(handle === 'l' ? Math.max(0, clip.start - extension) : FS.end(clip)) * TL.pps}px;width:${extension * TL.pps}px;top:${lane.top + 3}px;height:${lane.height - 6}px`},
                    '✨ ' + T('Continue the shot') + ' · ' + FS.snapTime(extension).toFixed(2) + 's'));
            }
        };
        const cancel = () => { cleanup(); if (moved) FS.cancelEdit(); };
        const key = (ev) => { if (ev.key === 'Escape') cancel(); };
        const cleanup = () => {
            window.removeEventListener('pointermove', move);
            window.removeEventListener('pointerup', up);
            window.removeEventListener('pointercancel', cancel);
            window.removeEventListener('keydown', key);
            if (TL.cancelPointer === cancel) TL.cancelPointer = null;
        };
        const up = () => {
            cleanup();
            if (!moved) { FS.emit('inspect', c.id); return; }
            const clip = FS.clip(c.id);
            if (!handle && (group.length === 1||linkedGroup)) {
                const lane = TL.dropLane;
                if (lane && lane.newTrack && lane.kind === (clip.kind === 'audio' ? 'audio' : 'video')) {
                    clip.track = FS.addTrack(lane.kind, lane.newTrack === 'top' ? FS.visualTracks().slice(-1)[0].id : null);
                }
                clip.start = FS.snapTime(clip.start);
                if (FS.track(orig.track).main && FS.magnet && clip.track !== orig.track) FS.closeGaps();
                const anchor=FS.audioParent(clip)||clip;
                FS.place(anchor, FS.track(anchor.track).main ? 'insert' : 'free');
                FS.dropEmptyTracks();
            }
            if (!handle && group.length > 1&&!linkedGroup) {
                const ids = new Set(group.map((g) => g.id));
                const overlaps = group.some((g) => S.doc.clips.some((other) => !ids.has(other.id) && other.track === g.track &&
                    other.start < FS.end(FS.clip(g.id)) - 1e-6 && FS.end(other) > FS.clip(g.id).start + 1e-6));
                if (overlaps) { FS.cancelEdit(); return; }
                if (FS.magnet) FS.closeGaps();
            }
            TL.lanes.forEach((l) => l.el.classList.remove('ft-target'));
            TL.dropLane = null;
            FS.commitEdit(handle ? 'trim' : 'move');
            if (handle && extension >= 1 / FS.fps() && extensionClip && FS.gen) {
                FS.guard(() => FS.gen.extendClip(extensionClip, handle, extension))();
            }
        };
        window.addEventListener('pointermove', move);
        window.addEventListener('pointerup', up);
        window.addEventListener('pointercancel', cancel);
        window.addEventListener('keydown', key);
        TL.cancelPointer = cancel;
    }

    /* ---- commands ---- */

    TL.split = function () {
        let ids = S.selected.slice();
        if (!ids.length) {
            const main = FS.mainTrack();
            ids = S.doc.clips.filter((c) => c.track === main.id && c.start < S.time && FS.end(c) > S.time).map((c) => c.id);
        }
        const made = FS.split(ids, FS.snapTime(S.time));
        if (made && made.length) FS.select(made);
    };

    TL.duplicate = function () {
        if (!S.selected.length) return;
        FS.change('duplicate', (d) => {
            const made = [];
            const ids=FS.linkedIds(S.selected),copies=new Map();
            for (const id of ids) {
                const c = FS.clip(id);
                const copy = JSON.parse(JSON.stringify(c));
                copy.id = FS.newId();
                copy.start = FS.end(c);
                d.clips.push(copy);
                FS.place(copy, FS.track(copy.track).main ? 'insert' : 'free');
                made.push(copy.id);
                copies.set(id,copy.id);
            }
            for(const id of made){const x=FS.clip(id);if(x.linkedTo&&copies.has(x.linkedTo)){x.linkedTo=copies.get(x.linkedTo);x.from=x.linkedTo;}if(x.detachedAudio&&copies.has(x.detachedAudio))x.detachedAudio=copies.get(x.detachedAudio);}
            S.selected = made;
        });
        FS.emit('select', S.selected);
    };

    TL.nextTake = function (c) {
        if (!c.takes || c.takes.length < 2) return;
        const i = (c.takes.indexOf(c.media) + 1) % c.takes.length;
        FS.change('take', () => { FS.clip(c.id).media = c.takes[i]; });
        FS.message(T('Take {0} of {1}', i + 1, c.takes.length));
    };

    /* a small menu at (x, y): the clip's actions, or the given items */
    const SPEEDS = [0.25, 0.5, 0.75, 1, 1.25, 1.5, 2, 3, 4];
    function speedRow(clip) {
        const ids = S.selected.includes(clip.id) ? S.selected.filter((id) => { const c = FS.clip(id); return c && (c.kind === 'video' || c.kind === 'audio'); }) : [clip.id];
        const now = FS.ramped(clip) ? null : +FS.val(clip, 'speed').toFixed(2);
        return {title: '🏎 ' + T('Speed') + (FS.ramped(clip) ? ' (' + T('ramp') + ')' : ''), slider: speedSlider(clip, ids), chips: SPEEDS.map((v) => ({
            label: v + '×', on: now === v, run: () => FS.setSpeed(ids, v)})).concat([{label: '…', title: T('Another speed'),
            run: async () => {
                const v = await FS.ask(T('Speed'), '1.6', String(now || 1), T('0.1 (ten times slower) to 8 (eight times faster); the clip gets shorter or longer'));
                if (v && isFinite(+v)) FS.setSpeed(ids, +v);
            }}])};
    }

    /* drag to the left: slower and longer, to the right: faster and shorter (0.1x - 8x on a log scale, 1x in the
     * middle); the clips change as it moves, one undo when it is let go */
    function speedSlider(clip, ids) {
        const toSpeed = (v) => Math.round(Math.pow(2, v) * 100) / 100;
        const label = h('span', {class: 'ft-slider-value'});
        const show = () => {
            const c = FS.clip(clip.id);
            label.textContent = `${(+FS.val(c, 'speed')).toFixed(2)}× · ${c.duration.toFixed(1)} ${T('s')}`;
        };
        const range = h('input', {type: 'range', class: 'ft-slider', min: -3.32, max: 3, step: 0.01,
            value: Math.log2(Math.max(0.1, FS.ramped(clip) ? 1 : +FS.val(clip, 'speed'))), title: T('Left: slower and longer · right: faster and shorter')});
        let editing = false;
        range.addEventListener('input', () => {
            if (!editing) { FS.beginEdit(); editing = true; }
            const v = toSpeed(+range.value);
            for (const id of ids) { const c = FS.clip(id); if (c) FS.applySpeed(c, Math.abs(v - 1) < 0.04 ? 1 : v); }
            FS.liveEdit();
            show();
        });
        range.addEventListener('change', () => { if (editing) FS.commitEdit('speed'); editing = false; });
        range.addEventListener('dblclick', () => { range.value = 0; range.dispatchEvent(new Event('input')); range.dispatchEvent(new Event('change')); });
        show();
        return h('div', {class: 'ft-slider-row', dir: 'ltr'}, h('span', {class: 'ft-dim'}, '🐢'), range, h('span', {class: 'ft-dim'}, '🐇'), label);
    }

    /* sound of a clip: mute, or its sound as a clip of its own */
    function soundItems(clip) {
        const info = S.info[clip.media] || {};
        const hasSound = clip.kind === 'audio' || (clip.kind === 'video' && info.audio);
        if (!hasSound) return [];
        const target=FS.soundTarget(clip),ids=S.selected.includes(clip.id)?S.selected:[clip.id];
        const items = [{label: target.muted ? '🔊 ' + T('Sound back on') : '🔇 ' + T('Mute'), run: () => {
            const on = !target.muted;
            FS.change('mute', () => { for (const id of ids) { const c = FS.soundTarget(FS.clip(id)); if (c && (c.kind === 'audio' || c.kind === 'video')) c.muted = on; } });
        }}];
        if(clip.kind==='video'&&!FS.detachedAudio(clip)&&!clip.muted){
            items.push({label:'🔗 '+T('Detach audio (keep linked)'),run:()=>FS.detachAudio(clip.id,true)});
            items.push({label:'🎵 '+T('Detach audio (independent)'),run:()=>FS.detachAudio(clip.id,false)});
        }
        items.push(...FS.audioLinkItems(clip));
        return items;
    }

    /* two shots picked (Ctrl-click): an AI join between them, from the first one's last frame to the second's first */
    function joinItem() {
        const two = S.selected.map((id) => FS.clip(id)).filter((c) => c && (c.kind === 'video' || c.kind === 'image') && c.media);
        if (two.length !== 2 || !FS.gen || !FS.gen.transition) return null;
        two.sort((a, b) => a.start - b.start);
        return {label: '🔗 ' + T('Join these two shots (AI)'), run: () => FS.gen.transition(two[0], two[1])};
    }

    TL.menu = function (x, y, clip, items) {
        TL.closeMenu();
        items = items || [
            joinItem(),
            clip.kind === 'video' || clip.kind === 'audio' ? speedRow(clip) : null,
            ...soundItems(clip),
            {label: '✂ ' + T('Split at the playhead'), run: () => FS.split([clip.id], FS.snapTime(S.time))},
            {label: '⧉ ' + T('Duplicate'), run: () => TL.duplicate()},
            {label: '🗑 ' + T('Delete'), run: () => FS.remove(S.selected.includes(clip.id) ? S.selected : [clip.id])},
        ].concat(FS.clipActions.filter((a) => !a.when || a.when(clip)).map((a) => ({label: a.label, run: () => a.run(clip)})));
        items = items.filter(Boolean);
        if (!items.length) return;
        const m = TL.menuEl = h('div', {class: 'ft-menu', dir: FS.hebrew() ? 'rtl' : 'ltr'},
            ...items.map((it) => it.chips
                ? h('div', {class: 'ft-menu-row'}, h('div', {class: 'ft-menu-title'}, it.title), it.slider || null,
                    h('div', {class: 'ft-chips', dir: 'ltr'}, ...it.chips.map((c) => h('button', {class: 'ft-chip' + (c.on ? ' on' : ''), title: c.title || '',
                        onclick: FS.guard(async () => { TL.closeMenu(); await c.run(); })}, c.label))))
                : h('button', {class: 'ft-menu-item', onclick: FS.guard(async () => { TL.closeMenu(); await it.run(); })}, it.label)));
        document.body.append(m);
        const r = m.getBoundingClientRect();
        m.style.left = Math.min(x, window.innerWidth - r.width - 8) + 'px';
        m.style.top = Math.min(y, window.innerHeight - r.height - 8) + 'px';
        setTimeout(() => window.addEventListener('pointerdown', TL.closeOnOutside), 0);
    };
    TL.closeOnOutside = (e) => { if (TL.menuEl && !TL.menuEl.contains(e.target)) TL.closeMenu(); };
    TL.closeMenu = function () {
        if (TL.menuEl) TL.menuEl.remove();
        TL.menuEl = null;
        window.removeEventListener('pointerdown', TL.closeOnOutside);
    };

    /* dropping a library file on the timeline */
    TL.acceptDrops = function () {
        TL.content.addEventListener('dragover', (e) => { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; });
        TL.content.addEventListener('drop', FS.guard(async (e) => {
            e.preventDefault();
            const raw = e.dataTransfer.getData('application/x-forge-studio');
            if (!raw) return;
            const item = JSON.parse(raw);
            const lane = laneAt(e.clientY);
            const time = FS.snapTime(snapped(timeAt(e.clientX)));
            await FS.addFromLibrary(item, {lane, time});
        }));
    };

    FS.on('doc', (e) => { if (e && e.live) TL.render(); else TL.render(); });
    FS.on('select', () => TL.render());
    FS.on('region', () => drawRegion());
    FS.on('talk', () => TL.render());
    FS.on('time', () => placePlayhead());
    FS.on('project', () => { FS.clearRegion(); setTimeout(() => TL.fit(), 50); });
})();

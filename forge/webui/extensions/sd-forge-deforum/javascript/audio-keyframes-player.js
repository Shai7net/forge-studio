// Audio Keyframes, "Listen and mark frames" (scripts/deforum_helpers/audio_keyframes_ui.py, timeline_html).
// Shows which Deforum frame of the song is playing and which prompt is active there, draws the whole song and a
// frame-by-frame close-up around the playhead, and puts a prompt (M) or a song part (P) at the frame you hear.
// The song plays in the #audio_kf_player audio component, the timeline HTML carries the song's data (data-json),
// and marks reach Python through the hidden #audio_kf_mark_request box and #audio_kf_mark_btn button.
(function () {
    'use strict';

    const CLOSE_UP_SECONDS = 6;
    const HEIGHT = 158;
    const WHOLE = {labels: 11, top: 14, height: 50};
    const CLOSE = {labels: 81, top: 86, height: 60};

    let armed = false; // the keys act after a click on the player or the timeline, until a click anywhere else
    let song = {version: null, data: null};
    let pending = []; // marks drawn at once, until Python sends the timeline back
    let hover = null; // the mouse over the canvas
    let animation = 0;
    let watched = null; // the canvas whose size is followed
    let wholeImage = {key: '', image: null};
    const resize = typeof ResizeObserver === 'function' ? new ResizeObserver(() => draw()) : null;

    const app = () => (typeof gradioApp === 'function' ? gradioApp() : document);
    const panel = () => app().querySelector('#audio_kf_timeline .akf-timeline');
    const player = () => app().querySelector('#audio_kf_player audio');
    const clamp = (value, low, high) => Math.min(Math.max(value, low), high);

    function songData(element) {
        if (!element) return null;
        if (song.version !== element.dataset.version) {
            let data = null;
            try {
                data = JSON.parse(element.dataset.json);
            } catch (e) {
                data = null;
            }
            song = {version: element.dataset.version, data};
            pending = [];
        }
        return song.data;
    }

    function frameAt(data, seconds) {
        return clamp(Math.floor(seconds * data.fps + 1e-6), 0, data.frames - 1);
    }

    function clock(seconds) {
        const minutes = Math.floor(seconds / 60);
        return `${minutes}:${(seconds - minutes * 60).toFixed(3).padStart(6, '0')}`;
    }

    // The prompts as they are in Deforum's Prompts box right now, typed by hand or added by a mark: [[frame, text]]
    function prompts(data) {
        const box = data.prompts_id ? app().querySelector(`#${data.prompts_id} textarea`) : null;
        if (box) {
            try {
                const parsed = JSON.parse(box.value);
                if (parsed && typeof parsed === 'object') {
                    return Object.keys(parsed).filter((key) => /^\s*\d+\s*$/.test(key))
                        .map((key) => [parseInt(key, 10), String(parsed[key])]).sort((a, b) => a[0] - b[0]);
                }
            } catch (e) {
                // half-typed JSON: use the frames that came with the timeline
            }
        }
        return (data.prompts || []).map((frame) => [frame, '']);
    }

    // Changes the text node in place: replacing it would wake A1111's page-wide onUiUpdate callbacks on every frame
    function setText(root, selector, text) {
        const node = root.querySelector(selector);
        if (!node) return;
        if (node.childNodes.length === 1 && node.firstChild.nodeType === Node.TEXT_NODE) {
            if (node.firstChild.nodeValue !== text) node.firstChild.nodeValue = text;
        } else if (node.textContent !== text) {
            node.textContent = text;
        }
    }

    function palette(element) {
        const style = getComputedStyle(element);
        const read = (name, fallback) => style.getPropertyValue(name).trim() || fallback;
        return {
            text: style.color || '#888',
            strip: read('--akf-strip', 'rgba(127,127,127,0.08)'),
            loudness: read('--akf-loudness', 'rgba(127,127,127,0.55)'),
            grid: read('--akf-grid', 'rgba(127,127,127,0.6)'),
            playhead: read('--akf-playhead', '#ef4444'),
            prompt: read('--akf-prompt', '#a855f7'),
            part: read('--akf-part', '#22c55e'),
        };
    }

    function draw() {
        const element = panel();
        const data = songData(element);
        if (!element || !data) return;
        element.classList.toggle('akf-armed', armed);
        const canvas = element.querySelector('canvas');
        if (canvas && canvas !== watched && resize) {
            if (watched) resize.unobserve(watched);
            resize.observe(canvas);
            watched = canvas;
        }
        const audio = player();
        const now = audio && isFinite(audio.currentTime) ? clamp(audio.currentTime, 0, data.duration) : 0;
        const frame = frameAt(data, now);
        const list = prompts(data);
        const active = list.filter(([key]) => key <= frame).pop();
        setText(element, '.akf-clock', clock(now));
        setText(element, '.akf-frame', String(frame));
        setText(element, '.akf-part-number', String(Math.max((data.parts || [0]).filter((start) => start <= frame).length, 1)));
        setText(element, '.akf-prompt-text', active ? `${active[0]} · ${active[1]}`.slice(0, 200) : '—');

        const width = canvas ? canvas.clientWidth : 0;
        if (!width) return; // the tab is not showing
        const ratio = window.devicePixelRatio || 1;
        if (canvas.width !== Math.round(width * ratio) || canvas.height !== Math.round(HEIGHT * ratio)) {
            canvas.width = Math.round(width * ratio);
            canvas.height = Math.round(HEIGHT * ratio);
        }
        const ctx = canvas.getContext('2d');
        ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
        ctx.clearRect(0, 0, width, HEIGHT);
        const colors = palette(element);
        const whole = Object.assign({start: 0, end: Math.max(data.duration, 1 / data.fps)}, WHOLE);
        const close = Object.assign({start: now - CLOSE_UP_SECONDS / 2, end: now + CLOSE_UP_SECONDS / 2}, CLOSE);
        drawWhole(ctx, data, whole, width, colors, ratio);
        drawCloseUp(ctx, data, close, width, colors, frame);
        drawMarks(ctx, data, whole, width, colors, list, now, false);
        drawMarks(ctx, data, close, width, colors, list, now, true);
        if (hover) drawHover(ctx, data, hover, width, colors, [whole, close]);
    }

    function drawWhole(ctx, data, view, width, colors, ratio) {
        const key = `${song.version}|${width}|${ratio}|${colors.loudness}|${colors.strip}`;
        if (wholeImage.key !== key) {
            const image = document.createElement('canvas');
            image.width = Math.round(width * ratio);
            image.height = Math.round(view.height * ratio);
            const c = image.getContext('2d');
            c.setTransform(ratio, 0, 0, ratio, 0, 0);
            c.fillStyle = colors.strip;
            c.fillRect(0, 0, width, view.height);
            c.fillStyle = colors.loudness;
            const points = data.loudness.length;
            const perPixel = points / width;
            for (let px = 0; px < width; px++) {
                const from = Math.floor(px * perPixel);
                const to = Math.max(from + 1, Math.floor((px + 1) * perPixel));
                let peak = 0;
                for (let i = from; i < to && i < points; i++) peak = Math.max(peak, data.loudness[i]);
                const h = (peak / 999) * view.height;
                c.fillRect(px, view.height - h, 1, h);
            }
            wholeImage = {key, image};
        }
        ctx.drawImage(wholeImage.image, 0, view.top, width, view.height);
        // a tick every few seconds, labelled with its time and its frame
        const span = view.end - view.start;
        const every = [1, 2, 5, 10, 15, 30, 60, 120, 300].find((n) => (n / span) * width >= 90) || 600;
        ctx.font = '11px sans-serif';
        for (let s = 0; s <= span + 1e-9; s += every) {
            const px = Math.round((s / span) * width);
            ctx.fillStyle = colors.grid;
            ctx.fillRect(px, view.top, 1, 6);
            ctx.fillStyle = colors.text;
            ctx.fillText(`${clock(s).replace(/\.000$/, '')} · ${Math.round(s * data.fps)}`, px + 3, view.labels);
        }
    }

    function drawCloseUp(ctx, data, view, width, colors, frame) {
        const span = view.end - view.start;
        const x = (seconds) => ((seconds - view.start) / span) * width;
        const pxPerFrame = width / (span * data.fps);
        ctx.fillStyle = colors.strip;
        ctx.fillRect(0, view.top, width, view.height);
        ctx.fillStyle = colors.playhead; // the frame showing now
        ctx.globalAlpha = 0.14;
        ctx.fillRect(x(frame / data.fps), view.top, Math.max(pxPerFrame, 2), view.height);
        ctx.globalAlpha = 1;
        const pointSeconds = data.step / data.fps; // one bar per frame (per few frames for very long songs)
        const first = Math.max(0, Math.floor(view.start / pointSeconds));
        const last = Math.min(data.loudness.length - 1, Math.ceil(view.end / pointSeconds));
        const bar = Math.max(1, (pointSeconds / span) * width - (pxPerFrame >= 4 ? 1 : 0));
        ctx.fillStyle = colors.loudness;
        for (let i = first; i <= last; i++) {
            const h = (data.loudness[i] / 999) * view.height;
            ctx.fillRect(x(i * pointSeconds), view.top + view.height - h, bar, h);
        }
        const every = [1, 2, 5, 10, 15, 30, 60, 120].find((n) => n * pxPerFrame >= 30) || 240;
        const firstFrame = Math.max(0, Math.ceil(view.start * data.fps));
        const lastFrame = Math.min(data.frames - 1, Math.floor(view.end * data.fps));
        ctx.font = '11px sans-serif';
        for (let f = firstFrame; f <= lastFrame; f++) {
            const labelled = f % every === 0;
            if (!labelled && pxPerFrame < 5) continue;
            const px = Math.round(x(f / data.fps));
            ctx.fillStyle = colors.grid;
            ctx.fillRect(px, view.top, 1, labelled ? 8 : 4);
            if (labelled) {
                ctx.fillStyle = colors.text;
                ctx.fillText(String(f), px + 2, view.labels);
            }
        }
    }

    function drawMarks(ctx, data, view, width, colors, list, now, closeUp) {
        const span = view.end - view.start;
        const x = (seconds) => ((seconds - view.start) / span) * width;
        const line = (px, color, dashed) => {
            if (px < -1 || px > width + 1) return;
            ctx.save();
            ctx.strokeStyle = color;
            ctx.lineWidth = 1.5;
            if (dashed) ctx.setLineDash([4, 3]);
            ctx.beginPath();
            ctx.moveTo(Math.round(px) + 0.5, view.top);
            ctx.lineTo(Math.round(px) + 0.5, view.top + view.height);
            ctx.stroke();
            ctx.restore();
        };
        for (const start of data.parts || []) {
            if (start > 0) line(x(start / data.fps), colors.part, true);
        }
        for (const [key] of list) line(x(key / data.fps), colors.prompt, false);
        for (const mark of pending) line(x(mark.frame / data.fps), mark.kind === 'part' ? colors.part : colors.prompt, true);
        if (closeUp) {
            ctx.font = 'bold 10px sans-serif';
            ctx.fillStyle = colors.prompt;
            for (const [key] of list) {
                const px = x(key / data.fps);
                if (px >= 0 && px <= width) ctx.fillText(`✚ ${key}`, px + 3, view.top + view.height - 3);
            }
        }
        ctx.fillStyle = colors.playhead;
        ctx.fillRect(Math.round(x(now)) - 1, view.top - 2, 2, view.height + 4);
    }

    function viewAt(point, views) {
        return views.find((view) => point.y >= view.labels - 12 && point.y <= view.top + view.height) || null;
    }

    function drawHover(ctx, data, point, width, colors, views) {
        const view = viewAt(point, views);
        if (!view) return;
        const seconds = clamp(view.start + (point.x / width) * (view.end - view.start), 0, data.duration);
        const text = `${clock(seconds)} · 🎞 ${frameAt(data, seconds)}`;
        ctx.font = '11px sans-serif';
        const boxWidth = ctx.measureText(text).width + 8;
        const left = clamp(point.x + 8, 0, width - boxWidth);
        ctx.fillStyle = colors.text;
        ctx.globalAlpha = 0.5;
        ctx.fillRect(Math.round(point.x), view.top, 1, view.height);
        ctx.globalAlpha = 1;
        ctx.fillStyle = 'rgba(0,0,0,0.78)';
        ctx.fillRect(left, view.top + 2, boxWidth, 16);
        ctx.fillStyle = '#fff';
        ctx.fillText(text, left + 4, view.top + 14);
    }

    function goToFrame(data, frame) {
        const audio = player();
        if (!audio) return;
        audio.currentTime = (clamp(frame, 0, data.frames - 1) + 0.5) / data.fps; // the middle of the frame
        draw();
    }

    function seek(event, canvas) {
        const data = songData(panel());
        const audio = player();
        if (!data || !audio) return;
        const rect = canvas.getBoundingClientRect();
        const point = {x: event.clientX - rect.left, y: event.clientY - rect.top};
        const now = audio.currentTime || 0;
        const views = [
            Object.assign({start: 0, end: Math.max(data.duration, 1 / data.fps)}, WHOLE),
            Object.assign({start: now - CLOSE_UP_SECONDS / 2, end: now + CLOSE_UP_SECONDS / 2}, CLOSE),
        ];
        const view = viewAt(point, views) || (point.y < CLOSE.labels - 12 ? views[0] : views[1]);
        const seconds = view.start + (point.x / rect.width) * (view.end - view.start);
        goToFrame(data, frameAt(data, clamp(seconds, 0, data.duration)));
    }

    function mark(kind) {
        const data = songData(panel());
        const audio = player();
        const box = app().querySelector('#audio_kf_mark_request textarea, #audio_kf_mark_request input');
        const button = app().querySelector('#audio_kf_mark_btn');
        if (!data || !audio || !box || !button) return;
        const reactionBox = app().querySelector('#audio_kf_reaction input');
        const reaction = audio.paused ? 0 : Math.max(parseFloat(reactionBox ? reactionBox.value : '0') || 0, 0);
        const frame = frameAt(data, Math.max((audio.currentTime || 0) - reaction, 0));
        box.value = JSON.stringify({kind, frame, fps: data.fps, at: Date.now()});
        if (typeof updateInput === 'function') updateInput(box);
        else box.dispatchEvent(new Event('input', {bubbles: true}));
        pending.push({kind, frame});
        draw();
        setTimeout(() => button.click(), 40);
    }

    function act(action) {
        const data = songData(panel());
        const audio = player();
        if (!data || !audio) return;
        const frame = frameAt(data, audio.currentTime || 0);
        const second = Math.round(data.fps);
        if (action === 'play') {
            if (audio.paused) audio.play().catch(() => {});
            else audio.pause();
        } else if (action === 'frame-back' || action === 'frame-forward') {
            audio.pause();
            goToFrame(data, frame + (action === 'frame-back' ? -1 : 1));
        } else if (action === 'second-back' || action === 'second-forward') {
            goToFrame(data, frame + (action === 'second-back' ? -second : second));
        } else if (action === 'mark-prompt') {
            mark('prompt');
        } else if (action === 'mark-part') {
            mark('part');
        }
    }

    document.addEventListener('pointerdown', (event) => {
        const target = event.target;
        armed = !!(target && target.closest && target.closest('#audio_kf_player, #audio_kf_timeline'));
        const element = panel();
        if (element) element.classList.toggle('akf-armed', armed);
    }, true);

    document.addEventListener('keydown', (event) => {
        if (!armed || event.ctrlKey || event.altKey || event.metaKey) return;
        const element = panel();
        if (!element || !element.offsetParent) return; // the Audio Keyframes tab is not showing
        const target = event.target;
        if (target && (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName))) return;
        const action = {
            Space: 'play',
            KeyM: 'mark-prompt',
            KeyP: 'mark-part',
            ArrowLeft: event.shiftKey ? 'second-back' : 'frame-back',
            ArrowRight: event.shiftKey ? 'second-forward' : 'frame-forward',
        }[event.code]; // event.code, so M and P work with a Hebrew keyboard too
        if (!action) return;
        event.preventDefault();
        event.stopPropagation();
        if (!event.repeat || action.startsWith('frame') || action.startsWith('second')) act(action);
    }, true);

    ['timeupdate', 'seeked', 'loadedmetadata', 'pause', 'ended'].forEach((type) => document.addEventListener(type, (event) => {
        if (event.target === player()) draw();
    }, true));

    document.addEventListener('play', (event) => {
        if (event.target !== player()) return;
        armed = true;
        cancelAnimationFrame(animation);
        const tick = () => {
            draw();
            const audio = player();
            if (audio && !audio.paused) animation = requestAnimationFrame(tick);
        };
        animation = requestAnimationFrame(tick);
    }, true);

    document.addEventListener('pointermove', (event) => {
        const canvas = event.target && event.target.closest ? event.target.closest('#audio_kf_timeline canvas') : null;
        if (!canvas) {
            if (hover) {
                hover = null;
                draw();
            }
            return;
        }
        const rect = canvas.getBoundingClientRect();
        hover = {x: event.clientX - rect.left, y: event.clientY - rect.top};
        draw();
    });

    document.addEventListener('click', (event) => {
        const target = event.target;
        if (!target || !target.closest) return;
        const button = target.closest('#audio_kf_timeline button[data-akf]');
        if (button) {
            act(button.dataset.akf);
            button.blur(); // so Space goes to the player, not to the button
            return;
        }
        const canvas = target.closest('#audio_kf_timeline canvas');
        if (canvas) seek(event, canvas);
    });

    // Python replaces the timeline after Analyze, a split or a mark: draw the new one
    if (typeof onUiUpdate === 'function') {
        let seen = null;
        onUiUpdate(() => {
            const element = panel();
            if (element !== seen) {
                seen = element;
                draw();
            }
        });
    }
})();

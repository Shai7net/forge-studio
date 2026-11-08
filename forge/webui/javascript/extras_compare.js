// The before / after viewer of the Extras video tab: one picture with a line to drag across it, the original on
// the left of the line and the result on the right, the way upscaling programs show a preview.
//
// Both pictures are two parts of one video's frame (compare_layout() in modules/postprocessing_video.py) drawn
// onto a canvas, so the two sides can never be a frame apart. After a click on the picture the mouse wheel zooms
// in on the pointer, and dragging then moves the picture; near the line, or with the whole picture in view,
// dragging moves the line.

(function() {
    const HOST = 'extras_video_compare';
    const NEAR_LINE = 14;       // pixels around the line where a drag takes the line and not the picture
    const TEXTS = {             // [English, Hebrew]
        before: ['Before', 'לפני'],
        after: ['After', 'אחרי'],
        play: ['Play / pause (Space)', 'ניגון / עצירה (רווח)'],
        back: ['One frame back (←)', 'פריים אחד אחורה (←)'],
        forward: ['One frame forward (→)', 'פריים אחד קדימה (→)'],
        zoom: ['Real size / whole picture (double click)', 'גודל אמיתי / כל התמונה (לחיצה כפולה)'],
        sound: ['Sound', 'קול'],
        full: ['Full screen', 'מסך מלא'],
    };
    let current = null;         // the viewer on the page: {key, destroy, ...}

    function clamp(value, low, high) {
        return Math.min(Math.max(value, low), high);
    }

    function clock(seconds) {
        seconds = Math.max(0, seconds || 0);
        return Math.floor(seconds / 60) + ':' + (seconds % 60).toFixed(1).padStart(4, '0');
    }

    function build(anchor, data) {
        const [afterX, afterY, width, height] = data.after;
        const [beforeX, beforeY, beforeW, beforeH] = data.before;

        const root = document.createElement('div');
        root.className = 'extras-compare hebrew-ui-skip' + (data.still ? ' ec-still' : '');     // one frame: nothing to play
        root.tabIndex = 0;
        root.innerHTML =
            '<div class="ec-stage"><canvas></canvas>' +
            '<span class="ec-tag ec-before"></span><span class="ec-tag ec-after"></span>' +
            '<div class="ec-line"><div class="ec-handle"></div></div></div>' +
            '<div class="ec-bar">' +
            '<button type="button" class="ec-play"></button>' +
            '<button type="button" class="ec-back">‹</button>' +
            '<button type="button" class="ec-forward">›</button>' +
            '<input type="range" class="ec-seek" min="0" max="1000" value="0">' +
            '<span class="ec-time"></span>' +
            '<button type="button" class="ec-zoom"></button>' +
            '<button type="button" class="ec-sound"></button>' +
            '<button type="button" class="ec-full">⛶</button></div>';
        const part = name => root.querySelector('.ec-' + name);
        const stage = part('stage'), canvas = root.querySelector('canvas'), line = part('line'), seek = part('seek');
        const context = canvas.getContext('2d');
        for (const name of ['before', 'after']) {
            part(name).innerHTML = '<span class="extras-en">' + TEXTS[name][0] + '</span><span class="extras-he">' + TEXTS[name][1] + '</span>';
        }

        const video = document.createElement('video');
        video.loop = true;
        video.preload = 'auto';
        video.playsInline = true;
        video.src = data.video;

        let split = 0.5;                            // where the line is, as a part of the picture's width
        let zoom = 1;                               // 1 = the whole picture in view
        let centerX = width / 2, centerY = height / 2;      // the middle of the view, in the result's pixels
        let drag = null, last = null, waiting = false;

        // the zoom at which one pixel of the result is one pixel of the screen
        const realSize = () => Math.max(1, width / canvas.width);

        function draw() {
            const viewW = width / zoom, viewH = height / zoom;
            centerX = clamp(centerX, viewW / 2, width - viewW / 2);
            centerY = clamp(centerY, viewH / 2, height - viewH / 2);
            const viewX = centerX - viewW / 2, viewY = centerY - viewH / 2;
            const lineAt = Math.round(split * canvas.width);
            const scaleX = beforeW / width, scaleY = beforeH / height;      // from the result's pixels to the original's
            context.imageSmoothingEnabled = true;
            context.imageSmoothingQuality = 'high';
            if (video.readyState >= 2) {
                context.save();
                context.beginPath();
                context.rect(0, 0, lineAt, canvas.height);
                context.clip();
                context.drawImage(video, beforeX + viewX * scaleX, beforeY + viewY * scaleY, viewW * scaleX, viewH * scaleY, 0, 0, canvas.width, canvas.height);
                context.restore();
                context.save();
                context.beginPath();
                context.rect(lineAt, 0, canvas.width - lineAt, canvas.height);
                context.clip();
                context.drawImage(video, afterX + viewX, afterY + viewY, viewW, viewH, 0, 0, canvas.width, canvas.height);
                context.restore();
            }
            line.style.left = (split * 100) + '%';
            part('zoom').textContent = zoom > 1 ? '⤢' : '1:1';
            part('zoom').disabled = realSize() <= 1 && zoom <= 1;
        }

        function showTime() {
            // an attribute, shown through CSS: changing text on every frame would wake every script that
            // watches the page for changes, and this page has many
            const text = clock(video.currentTime) + ' / ' + clock(video.duration);
            if (part('time').dataset.time !== text) part('time').dataset.time = text;
            if (drag !== 'seek' && video.duration) seek.value = Math.round(video.currentTime / video.duration * 1000);
            part('play').textContent = video.paused ? '▶' : '❚❚';
            part('sound').textContent = video.muted ? '🔇' : '🔊';
        }

        function frame() {
            draw();
            showTime();
            if (!video.paused) nextFrame();
        }

        function nextFrame() {
            if (waiting) return;
            waiting = true;
            const run = () => {
                waiting = false;
                frame();
            };
            if (video.requestVideoFrameCallback) video.requestVideoFrameCallback(run);
            else requestAnimationFrame(run);
        }

        function layout() {
            const full = document.fullscreenElement === root;
            const roomW = full ? window.innerWidth : root.clientWidth;
            const roomH = full ? window.innerHeight - part('bar').offsetHeight : window.innerHeight * 0.75;
            if (!roomW || roomH <= 0) return;      // the tab is not on screen yet
            const scale = Math.min(roomW / width, roomH / height);
            const shownW = Math.max(1, Math.round(width * scale)), shownH = Math.max(1, Math.round(height * scale));
            stage.style.width = shownW + 'px';
            stage.style.height = shownH + 'px';
            const ratio = window.devicePixelRatio || 1;
            canvas.width = Math.round(shownW * ratio);
            canvas.height = Math.round(shownH * ratio);
            draw();
        }

        function zoomAt(newZoom, x, y) {
            // x, y: the place that stays put, as parts of the picture's width and height
            newZoom = clamp(newZoom, 1, Math.max(8, realSize() * 4));
            const underX = centerX + (x - 0.5) * width / zoom, underY = centerY + (y - 0.5) * height / zoom;
            zoom = newZoom;
            centerX = underX - (x - 0.5) * width / zoom;
            centerY = underY - (y - 0.5) * height / zoom;
            draw();
        }

        function toggleZoom(x, y) {
            zoomAt(zoom > 1 ? 1 : realSize(), x, y);
        }

        function step(frames) {
            video.pause();
            const index = Math.floor(video.currentTime * data.fps + 0.001) + frames;
            const lastIndex = Math.max(0, Math.round((video.duration || 0) * data.fps) - 1);
            video.currentTime = (clamp(index, 0, lastIndex) + 0.5) / data.fps;      // the middle of that frame
        }

        function togglePlay() {
            if (video.paused) video.play();
            else video.pause();
        }

        function place(event) {
            const box = stage.getBoundingClientRect();
            return [(event.clientX - box.left) / box.width, (event.clientY - box.top) / box.height, box];
        }

        stage.addEventListener('pointerdown', function(event) {
            if (event.button !== 0) return;
            root.focus({preventScroll: true});
            const [x, , box] = place(event);
            drag = zoom === 1 || Math.abs(x - split) * box.width < NEAR_LINE ? 'line' : 'pan';
            last = [event.clientX, event.clientY];
            if (drag === 'line') {
                split = clamp(x, 0, 1);
                draw();
            }
            stage.setPointerCapture(event.pointerId);
            event.preventDefault();
        });
        stage.addEventListener('pointermove', function(event) {
            const [x, , box] = place(event);
            if (!drag) {
                stage.style.cursor = zoom === 1 || Math.abs(x - split) * box.width < NEAR_LINE ? 'ew-resize' : 'grab';
                return;
            }
            if (drag === 'line') {
                split = clamp(x, 0, 1);
            } else {
                centerX -= (event.clientX - last[0]) / box.width * width / zoom;
                centerY -= (event.clientY - last[1]) / box.height * height / zoom;
                last = [event.clientX, event.clientY];
            }
            draw();
        });
        for (const name of ['pointerup', 'pointercancel']) {
            stage.addEventListener(name, function() {
                drag = null;
            });
        }
        stage.addEventListener('dblclick', function(event) {
            const [x, y] = place(event);
            toggleZoom(x, y);
        });
        stage.addEventListener('wheel', function(event) {
            // only after a click on the picture: until then the wheel goes on scrolling the page
            if (document.activeElement !== root && !event.ctrlKey) return;
            event.preventDefault();
            const [x, y] = place(event);
            zoomAt(zoom * Math.pow(1.0015, -event.deltaY), x, y);
        }, {passive: false});

        root.addEventListener('keydown', function(event) {
            if (event.target !== root) return;      // a focused button or the time bar handles its own keys
            if (event.key === ' ') togglePlay();
            else if (event.key === 'ArrowLeft') step(-1);
            else if (event.key === 'ArrowRight') step(1);
            else return;
            event.preventDefault();
        });
        part('play').addEventListener('click', togglePlay);
        part('back').addEventListener('click', () => step(-1));
        part('forward').addEventListener('click', () => step(1));
        part('zoom').addEventListener('click', () => toggleZoom(split, 0.5));
        part('sound').addEventListener('click', function() {
            video.muted = !video.muted;
            showTime();
        });
        part('full').addEventListener('click', function() {
            if (document.fullscreenElement === root) document.exitFullscreen();
            else root.requestFullscreen();
        });
        seek.addEventListener('pointerdown', () => {
            drag = 'seek';
        });
        seek.addEventListener('input', function() {
            if (video.duration) video.currentTime = seek.value / 1000 * video.duration;
        });
        seek.addEventListener('change', () => {
            drag = null;
        });

        video.addEventListener('play', frame);
        for (const name of ['loadeddata', 'seeked', 'pause', 'volumechange']) video.addEventListener(name, frame);

        function titles() {
            const language = document.documentElement.classList.contains('hebrew-ui-active') ? 1 : 0;
            for (const name of ['play', 'back', 'forward', 'zoom', 'sound', 'full']) part(name).title = TEXTS[name][language];
        }
        const languageWatch = new MutationObserver(titles);
        languageWatch.observe(document.documentElement, {attributes: true, attributeFilter: ['class']});
        const sizeWatch = new ResizeObserver(layout);
        sizeWatch.observe(root);
        window.addEventListener('resize', layout);
        document.addEventListener('fullscreenchange', layout);

        // right after the html block's own content, which Gradio replaces whenever the value changes
        anchor.insertAdjacentElement('afterend', root);
        titles();
        layout();
        showTime();

        return {
            root, video, draw, layout, step, zoomAt,
            state: () => ({split, zoom, centerX, centerY, canvas: [canvas.width, canvas.height], realSize: realSize()}),
            setSplit: value => {
                split = clamp(value, 0, 1);
                draw();
            },
            destroy: function() {
                languageWatch.disconnect();
                sizeWatch.disconnect();
                window.removeEventListener('resize', layout);
                document.removeEventListener('fullscreenchange', layout);
                video.pause();
                video.removeAttribute('src');
                video.load();
                root.remove();
            },
        };
    }

    function check() {
        const host = gradioApp().getElementById(HOST);
        if (!host) return;
        const holder = host.querySelector('.extras-compare-data');
        const key = holder ? holder.dataset.compare : '';
        if (current && current.key === key) return;
        if (current) current.destroy();
        current = null;
        if (!key) return;
        current = build(holder.closest('.prose') || holder, JSON.parse(key));
        current.key = key;
        window.extrasCompare = current;
    }

    onUiUpdate(check);
})();

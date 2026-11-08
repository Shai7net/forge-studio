// 🗺️ Map Key editor (scripts/deforum_helpers/map_key_ui.py). A shared timeline shows the song, the prompts and a lane
// per schedule; lanes are drawn with the mouse (up = fast), parts are selected and turned around (minus), prompts are
// dragged, and a camera path drawn on a map (with a height lane and a live 3D view) moves the camera.
// The logic is in map-key-core.js (window.MapKeyCore). Python puts the data in #map_key_editor .mk-data; the Load,
// Apply and Save buttons get what they need from here through their _js functions (mapKeyLoad, mapKeyApply, ...).
(function () {
    'use strict';

    const HEAD = 200; // width of the lane headers, px
    const HEIGHT = {music: 66, plan: 34, prompts: 50, lane: 74, overview: 20, height: 74};
    const PAD = 6; // space above and below a lane's curve
    const SIGN_OPS = ['minus', 'plus', 'flip'];

    let state = null; // MapKeyCore.createState(...)
    let version = null; // data-version of the data the state came from
    let root = null; // the editor (.mk-app)
    let rowsSignature = '';
    let armed = false; // keys act after a click in the editor or the player, until a click anywhere else
    let pending = false;
    let cache = {revision: -1, values: {}, built: null};
    const ui = {
        tool: 'draw', pathTool: 'draw', selection: null, view: {start: 0, end: 1}, playFrame: 0, virtual: null,
        hover: null, drag: null, snap: 'beats', selectedPrompt: null, selectedPlan: null,
        orbit: {yaw: -0.45, pitch: 0.6, distance: 3.3},
        info: null, animation: 0, anchor: null,
    };

    const Core = () => window.MapKeyCore;
    const gradio = () => (typeof gradioApp === 'function' ? gradioApp() : document);
    const host = () => gradio().querySelector('#map_key_editor');
    const player = () => gradio().querySelector('#map_key_player audio');
    const hebrew = () => document.documentElement.classList.contains('hebrew-ui-active');
    const L = (english, hebrewText) => (hebrew() ? hebrewText : english);
    const clamp = (value, low, high) => Math.min(Math.max(value, low), high);

    // Numbers and English inside a Hebrew sentence keep their own order (a left-to-right isolate)
    const ltr = (value) => `⁦${value}⁩`;

    function clock(seconds) {
        const minutes = Math.floor(Math.max(seconds, 0) / 60);
        return `${minutes}:${(Math.max(seconds, 0) - minutes * 60).toFixed(3).padStart(6, '0')}`;
    }

    function format(value, decimals) {
        const text = Number(value).toFixed(decimals).replace(/\.?0+$/, '');
        return text === '-0' || text === '' ? '0' : text;
    }

    // element builder: h('button', {class: 'x', 'data-mk': 'play'}, 'text', child, ...)
    function h(tag, attributes, ...children) {
        const element = document.createElement(tag);
        for (const [name, value] of Object.entries(attributes || {})) {
            if (value === false || value === null || value === undefined) continue;
            if (name === 'class') element.className = value;
            else if (name === 'text') element.textContent = value;
            else element.setAttribute(name, value === true ? '' : String(value));
        }
        for (const child of children.flat()) {
            if (child === null || child === undefined || child === false) continue;
            element.appendChild(typeof child === 'string' ? document.createTextNode(child) : child);
        }
        return element;
    }

    // Changes a text node in place, so no childList mutation wakes A1111's page-wide onUiUpdate callbacks
    function setText(element, text) {
        if (!element) return;
        if (element.childNodes.length === 1 && element.firstChild.nodeType === Node.TEXT_NODE) {
            if (element.firstChild.nodeValue !== text) element.firstChild.nodeValue = text;
        } else if (element.textContent !== text) {
            element.textContent = text;
        }
    }

    // --- values, cached per edit -------------------------------------------------------------------------

    function values(key) {
        if (cache.revision !== state.revision) cache = {revision: state.revision, values: {}, built: null};
        if (!cache.values[key]) cache.values[key] = Core().laneValues(state, key);
        return cache.values[key];
    }

    function built() {
        if (cache.revision !== state.revision) cache = {revision: state.revision, values: {}, built: null};
        if (!cache.built) cache.built = Core().buildPath(state.path);
        return cache.built;
    }

    // Where the lanes as they are take the camera, fitted into the map (the path views show it while no path drives it)
    function trail() {
        if (cache.revision !== state.revision) cache = {revision: state.revision, values: {}, built: null};
        if (!cache.trail) {
            const motion = Core().integrateMotion(state);
            let minX = 0, maxX = 0, minZ = 0, maxZ = 0, minY = 0, maxY = 0;
            for (let f = 0; f < state.frames; f++) {
                minX = Math.min(minX, motion.x[f]); maxX = Math.max(maxX, motion.x[f]);
                minZ = Math.min(minZ, motion.z[f]); maxZ = Math.max(maxZ, motion.z[f]);
                minY = Math.min(minY, motion.y[f]); maxY = Math.max(maxY, motion.y[f]);
            }
            const extent = Math.max(maxX - minX, maxZ - minZ, maxY - minY);
            const scale = extent > 1e-9 ? 1.8 / extent : 0;
            const cx = (minX + maxX) / 2;
            const cz = (minZ + maxZ) / 2;
            const at = (f) => ({x: (motion.x[f] - cx) * scale, y: (motion.y[f] - minY) * scale, z: (motion.z[f] - cz) * scale,
                                yaw: motion.yaw[f], pitch: motion.pitch[f]});
            cache.trail = {moves: scale > 0, at};
        }
        return cache.trail;
    }

    function laneRange(key) {
        const spec = Core().LANES[key];
        return spec.kind === 'strength' ? 1 : state.lanes[key].max;
    }

    function magnitude(key, value) {
        const spec = Core().LANES[key];
        return Core().signed(key) ? Math.abs(value - spec.neutral) : value;
    }

    // --- time on the canvases ----------------------------------------------------------------------------

    const xOf = (frame, width) => ((frame - ui.view.start) / (ui.view.end - ui.view.start)) * width;
    const frameOf = (x, width) => ui.view.start + (x / width) * (ui.view.end - ui.view.start);
    const frameAt = (x, width) => clamp(Math.floor(frameOf(x, width)), 0, state.frames - 1);
    const pxPerFrame = (width) => width / (ui.view.end - ui.view.start);

    function currentFrame() {
        const audio = player();
        if (audio && audio.src && isFinite(audio.currentTime) && state.song) {
            return clamp(Math.floor(audio.currentTime * state.fps + 1e-6), 0, state.frames - 1);
        }
        return clamp(Math.round(ui.playFrame), 0, state.frames - 1);
    }

    function isPlaying() {
        const audio = player();
        return (audio && state && state.song && !audio.paused) || !!ui.virtual;
    }

    function seek(frame) {
        const target = clamp(Math.round(frame), 0, state.frames - 1);
        ui.playFrame = target;
        const audio = player();
        if (audio && audio.src && state.song) audio.currentTime = (target + 0.5) / state.fps;
        if (ui.virtual) ui.virtual = {start: performance.now(), frame: target};
        keepInView(target);
        render();
    }

    function keepInView(frame) {
        const span = ui.view.end - ui.view.start;
        if (frame < ui.view.start || frame >= ui.view.end) setView(frame - span * 0.1, frame - span * 0.1 + span);
    }

    function setView(start, end) {
        const frames = state.frames;
        let span = clamp(end - start, Math.min(8, frames), frames);
        let from = clamp(start, 0, frames - span);
        ui.view = {start: from, end: from + span};
    }

    function zoomView(factor, anchorFrame) {
        const span = ui.view.end - ui.view.start;
        const anchor = anchorFrame === undefined ? (ui.view.start + ui.view.end) / 2 : anchorFrame;
        const next = clamp(span * factor, Math.min(8, state.frames), state.frames);
        const t = (anchor - ui.view.start) / span;
        setView(anchor - t * next, anchor - t * next + next);
        render();
    }

    function togglePlay() {
        const audio = player();
        if (audio && audio.src && state.song) {
            if (audio.paused) audio.play().catch(() => {});
            else audio.pause();
            return;
        }
        if (ui.virtual) {
            ui.virtual = null;
        } else {
            ui.virtual = {start: performance.now(), frame: currentFrame() >= state.frames - 1 ? 0 : currentFrame()};
            loop();
        }
        render();
    }

    function loop() {
        cancelAnimationFrame(ui.animation);
        const tick = () => {
            if (!state) return;
            if (ui.virtual) {
                const frame = ui.virtual.frame + ((performance.now() - ui.virtual.start) / 1000) * state.fps;
                ui.playFrame = frame;
                if (frame >= state.frames - 1) {
                    ui.playFrame = state.frames - 1;
                    ui.virtual = null;
                }
            }
            if (isPlaying()) keepInView(currentFrame());
            renderNow();
            if (isPlaying()) ui.animation = requestAnimationFrame(tick);
        };
        ui.animation = requestAnimationFrame(tick);
    }

    // --- building the editor -----------------------------------------------------------------------------

    function button(attributes, english, hebrewText, title) {
        return h('button', Object.assign({type: 'button', title: title || null}, attributes), L(english, hebrewText));
    }

    function toolbar() {
        const t = (en, he) => L(en, he);
        return h('div', {class: 'mk-toolbar'},
            h('span', {class: 'mk-group'},
                button({'data-mk': 'play', class: 'mk-play'}, '▶ ❚❚', '▶ ❚❚', t('Play / pause (Space)', 'ניגון / עצירה (רווח)'))),
            h('span', {class: 'mk-group'},
                button({'data-mk-tool': 'draw'}, '✏ Draw', '✏ ציור', t('Draw on a lane: up = fast (D)', 'ציור על שורה: למעלה = מהר (D)')),
                button({'data-mk-tool': 'line'}, '📏 Line', '📏 קו', t('A straight line (L)', 'קו ישר (L)')),
                button({'data-mk-tool': 'select'}, '⬚ Select', '⬚ סימון', t('Select a part, then − / + / ⇄ ... (S)', 'מסמנים קטע ואז − / + / ⇄ ... (S)'))),
            h('span', {class: 'mk-group mk-ops'},
                button({'data-mk-op': 'minus', class: 'mk-minus'}, '− Minus', '− מינוס', t('The selected part goes the other way: back, left, down (−)', 'הקטע המסומן נוסע הפוך: אחורה, שמאלה, למטה (−)')),
                button({'data-mk-op': 'plus'}, '+ Plus', '+ פלוס', t('The selected part goes the + way (=)', 'הקטע המסומן נוסע לכיוון + (=)')),
                button({'data-mk-op': 'flip'}, '⇄', '⇄', t('Flip the direction of the selected part (F)', 'הפוך את הכיוון בקטע המסומן (F)')),
                button({'data-mk-op': 'smooth'}, '≈', '≈', t('Smooth the selected part', 'החלק את הקטע המסומן')),
                button({'data-mk-op': 'half'}, '×½', '×½', t('Half the speed', 'חצי מהירות')),
                button({'data-mk-op': 'double'}, '×2', '×2', t('Double the speed', 'פי 2 מהירות')),
                button({'data-mk-op': 'reset'}, '⟲', '⟲', t('Reset: motion stops; Strength and CFG go back to the loaded values', 'איפוס: התנועה נעצרת; Strength ו-CFG חוזרים לערכים שנטענו'))),
            h('span', {class: 'mk-group'},
                button({'data-mk': 'undo'}, '↶', '↶', t('Undo (Ctrl+Z)', 'ביטול (Ctrl+Z)')),
                button({'data-mk': 'redo'}, '↷', '↷', t('Redo (Ctrl+Y)', 'חזרה (Ctrl+Y)'))),
            h('span', {class: 'mk-group'},
                h('select', {'data-mk': 'snap', title: t('Where dragged prompts snap to', 'לאן פרומפטים שנגררים נצמדים')},
                  h('option', {value: 'beats', selected: ui.snap === 'beats'}, t('snap: beats', 'הצמדה: ביטים')),
                  h('option', {value: 'parts', selected: ui.snap === 'parts'}, t('snap: song parts', 'הצמדה: חלקי שיר')),
                  h('option', {value: 'off', selected: ui.snap === 'off'}, t('no snap', 'בלי הצמדה'))),
                button({'data-mk': 'zoom-out'}, '🔍−', '🔍−', t('Zoom out (wheel)', 'התרחק (גלגלת)')),
                button({'data-mk': 'zoom-in'}, '🔍+', '🔍+', t('Zoom in (wheel)', 'התקרב (גלגלת)')),
                button({'data-mk': 'fit'}, '⤢', '⤢', t('The whole song', 'כל השיר')),
                button({'data-mk': 'cfg'}, state.shown.cfg_scale_schedule ? '− CFG' : '+ CFG', state.shown.cfg_scale_schedule ? '− CFG' : '+ CFG',
                       t('Show or hide the CFG lane', 'הצג או הסתר את שורת ה-CFG')),
                button({'data-mk': 'fullscreen'}, '⛶', '⛶', t('Full screen (Esc to leave)', 'מסך מלא (Esc ליציאה)'))));
    }

    function laneHead(key) {
        const spec = Core().LANES[key];
        const lane = state.lanes[key];
        const words = Core().directionWords(key, state.mode);
        const children = [h('div', {class: 'mk-name', dir: 'auto'}, L(spec.en, spec.he),
                            h('span', {class: 'mk-driven-tag'}, L(' · from the path', ' · מהמסלול')))];
        if (words) {
            children.push(h('div', {class: 'mk-dir', dir: 'auto'}, `+ ${L(...words[0])} · − ${L(...words[1])}`));
        }
        const controls = h('div', {class: 'mk-controls'});
        if (key !== 'path_speed') {
            controls.appendChild(h('label', {class: 'mk-write', title: L('Write this lane to Deforum on Apply (ticked by itself when you edit it)',
                                                                           'לכתוב את השורה לדיפורום ב-Apply (מסתמן לבד כשעורכים)')},
                                   h('input', {type: 'checkbox', 'data-mk-write': key, checked: lane.write}), L('write', 'לכתוב')));
        }
        if (spec.kind !== 'strength') {
            controls.appendChild(h('span', {class: 'mk-range'},
                button({'data-mk-range': '-', 'data-key': key}, '−', '−', L('Smaller range: finer low speeds', 'טווח קטן יותר: דיוק במהירויות נמוכות')),
                h('span', {class: 'mk-max', 'data-key': key}, format(lane.max, 4)),
                button({'data-mk-range': '+', 'data-key': key}, '+', '+', L('Bigger range: draw faster speeds', 'טווח גדול יותר: לצייר מהירויות גבוהות'))));
        }
        children.push(controls);
        if (Core().signed(key)) {
            const music = lane.music;
            const amounts = [0, 25, 50, 75, 100];
            children.push(h('div', {class: 'mk-music', title: L('How much the motion follows the music: 0% = the drawing, 100% = the drawing is the ceiling and the music moves within it',
                                                                'כמה התנועה עוקבת אחרי המוזיקה: 0% = הציור, 100% = הציור הוא התקרה והמוזיקה מזיזה בתוכו')},
                '♫ ',
                h('select', {'data-mk-music': 'amount', 'data-key': key},
                  amounts.map((a) => h('option', {value: a, selected: Number(music.amount) === a}, `${a}%`))),
                h('select', {'data-mk-music': 'band', 'data-key': key},
                  Core().BANDS.map((band) => h('option', {value: band, selected: music.band === band}, band))),
                h('select', {'data-mk-music': 'curve', 'data-key': key},
                  h('option', {value: 'peaks', selected: music.curve !== 'even'}, L('peaks', 'שיאים')),
                  h('option', {value: 'even', selected: music.curve === 'even'}, L('even', 'שווה')))));
        }
        return h('div', {class: 'mk-head'}, children);
    }

    function row(kind, key, head, height) {
        return h('div', {class: `mk-row mk-row-${kind}`, 'data-row': kind, 'data-key': key || null, style: `height:${height}px`},
                 head, h('canvas', {class: 'mk-canvas'}));
    }

    function rows() {
        const list = h('div', {class: 'mk-rows'});
        list.appendChild(row('music', null, h('div', {class: 'mk-head'},
            h('div', {class: 'mk-clock'}, ''),
            h('div', {class: 'mk-name', dir: 'auto'}, state.song ? `🎵 ${state.song.name}` : L('🎵 No song', '🎵 אין שיר')),
            h('div', {class: 'mk-dir', dir: 'auto'}, state.song ? L('drag here to select a time in every lane', 'גרירה כאן מסמנת זמן בכל השורות')
                : L('choose a song in Audio Keyframes, then 📥 Load', 'בוחרים שיר ב-Audio Keyframes ואז 📥 טען'))), HEIGHT.music));
        list.appendChild(row('plan', null, h('div', {class: 'mk-head'},
            h('div', {class: 'mk-name', dir: 'auto'}, L('\u{1F3AC} Plan', '\u{1F3AC} תוכנית')),
            h('div', {class: 'mk-dir', dir: 'auto'}, state.plan.length
                ? L('drag to move \u00b7 Del removes', 'גרירה מזיזה \u00b7 Del מוחק')
                : L('write the steps in the bar above the preview', 'את השורות כותבים בסרגל מעל התצוגה'))), HEIGHT.plan));
        list.appendChild(row('prompts', null, h('div', {class: 'mk-head'},
            h('div', {class: 'mk-name', dir: 'auto'}, L('Prompts', 'פרומפטים')),
            h('div', {class: 'mk-dir', dir: 'auto'}, state.promptsWritable ? L('drag to move · double-click to add', 'גרירה מזיזה · לחיצה כפולה מוסיפה')
                : L('read only: fix the Prompts JSON', 'לקריאה בלבד: צריך לתקן את ה-JSON של הפרומפטים'))), HEIGHT.prompts));
        list.appendChild(promptEditor());
        for (const key of Core().laneOrder(state)) list.appendChild(row('lane', key, laneHead(key), HEIGHT.lane));
        list.appendChild(row('overview', null, h('div', {class: 'mk-head mk-head-small', dir: 'auto'}, L('whole song', 'כל השיר')), HEIGHT.overview));
        return list;
    }

    function promptEditor() {
        return h('div', {class: 'mk-prompt-editor', hidden: true},
            h('div', {class: 'mk-pe-line'},
                h('b', {}, L('Prompt at frame', 'פרומפט בפריים')), ' ',
                h('input', {type: 'number', class: 'mk-pe-frame', min: 0, step: 1}), ' ',
                h('span', {class: 'mk-pe-time'}, ''), ' ',
                h('span', {class: 'mk-pe-pinned'}, ''),
                h('label', {class: 'mk-pe-dip-label', title: L('Strength right at the change, for a clean switch to the new prompt (empty = no dip)',
                                                              'Strength ברגע המעבר, למעבר נקי לפרומפט החדש (ריק = בלי ירידה)')},
                  L('transition strength', 'עוצמת מעבר'), ' ',
                  h('input', {type: 'number', class: 'mk-pe-dip', min: 0, max: 1, step: 0.01, placeholder: L('none', 'בלי')})),
                h('label', {class: 'mk-pe-back-label'}, L('back over', 'חוזרת תוך'), ' ',
                  h('input', {type: 'number', class: 'mk-pe-back', min: 0, step: 1}), ' ', L('frames', 'פריימים')),
                button({'data-mk': 'prompt-delete'}, '🗑 Delete', '🗑 מחק', L('Delete this prompt (Delete)', 'מחק את הפרומפט (Delete)')),
                button({'data-mk': 'prompt-close'}, '✕', '✕', L('Close', 'סגור'))),
            h('textarea', {class: 'mk-pe-text', rows: 2, dir: 'auto', spellcheck: 'false'}));
    }

    function fovText() {
        const fov = state.warp && state.warp.fov;
        if (!fov || !fov.length) return '70°';
        let low = Infinity;
        let high = -Infinity;
        for (let f = 0; f < fov.length; f++) {
            low = Math.min(low, fov[f]);
            high = Math.max(high, fov[f]);
        }
        return Math.abs(high - low) < 0.05 ? `${format(low, 1)}°` : `${format(low, 1)}–${format(high, 1)}°`;
    }

    function pathSection() {
        const path = state.path;
        const twoD = state.mode === '2D';
        return h('div', {class: 'mk-path'},
            h('div', {class: 'mk-path-bar'},
                h('label', {class: 'mk-strong'}, h('input', {type: 'checkbox', 'data-mk-path': 'enabled', checked: path.enabled}),
                  L('🎥 The camera follows a path', '🎥 המצלמה נוסעת במסלול')),
                h('select', {'data-mk-path': 'look', disabled: twoD, title: twoD ? L('In 2D the camera cannot turn, so it always looks forward', 'ב-2D המצלמה לא מסתובבת, אז היא תמיד מסתכלת קדימה') : null},
                  h('option', {value: 'along', selected: path.look !== 'forward' && !twoD}, L('looks along the path (turns)', 'מסתכלת לאורך המסלול (מסתובבת)')),
                  h('option', {value: 'forward', selected: path.look === 'forward' || twoD}, L('always looks forward (slides)', 'תמיד קדימה (מחליקה)'))),
                h('label', {}, h('input', {type: 'checkbox', 'data-mk-path': 'closed', checked: path.closed}), L('closed loop', 'מסלול סגור')),
                h('label', {title: L('How many rounds of a closed path in the song', 'כמה סיבובים במסלול סגור לאורך השיר')},
                  L('laps', 'סיבובים'), ' ', h('input', {type: 'number', 'data-mk-path': 'laps', min: 0.25, step: 0.25, value: path.laps})),
                h('label', {title: L('Smooths the turns over this many frames', 'מחליק את הפניות על פני כמה פריימים')},
                  L('smooth turns', 'החלקת פניות'), ' ', h('input', {type: 'number', 'data-mk-path': 'lookAhead', min: 0, step: 1, value: path.lookAhead})),
                button({'data-mk-path-tool': 'draw'}, '✏ Draw a path', '✏ צייר מסלול', L('Draw the whole path on the map', 'ציור כל המסלול על המפה')),
                button({'data-mk-path-tool': 'edit'}, '✋ Points', '✋ נקודות', L('Drag points · double-click adds · right-click deletes', 'גרירת נקודות · לחיצה כפולה מוסיפה · לחיצה ימנית מוחקת')),
                button({'data-mk': 'path-clear'}, '🧹', '🧹', L('Clear the path', 'נקה את המסלול')),
                button({'data-mk': 'bake'}, '🍞 Bake into the lanes', '🍞 אפה לשורות', L('Copy what the path does into the lanes to edit it by hand, and turn the path off',
                                                                                      'העתק את מה שהמסלול עושה לשורות, כדי לערוך ביד, וכבה את המסלול')),
                h('span', {class: 'mk-warp-note', dir: 'auto', title: L(
                    'In a climbing bend the path adds a roll that keeps the horizon level. Deforum turns the picture more strongly away from its centre than a real camera, by 1/tan(FOV/2), so the roll gets the same factor from the FOV schedule. Press 📥 Load after changing the FOV.',
                    'בפנייה שעולה המסלול מוסיף גלגול ששומר על האופק ישר. דיפורום מסובב את התמונה רחוק מהמרכז חזק יותר ממצלמה אמיתית, פי 1/tan(FOV/2), ולכן הגלגול מקבל את אותו מקדם לפי תזמון ה-FOV. אחרי שינוי ה-FOV לוחצים 📥 טען.')},
                  L(`horizon kept level for FOV ${fovText()}`, `האופק נשמר ישר לפי FOV ${fovText()}`))),
            h('div', {class: 'mk-path-views'},
                h('div', {class: 'mk-view mk-view-map'},
                  h('div', {class: 'mk-view-title', dir: 'auto'}, L('Map, seen from above · up = forward', 'מפה, מבט מלמעלה · למעלה = קדימה')),
                  h('canvas', {class: 'mk-map'})),
                h('div', {class: 'mk-view mk-view-3d'},
                  h('div', {class: 'mk-view-title', dir: 'auto'}, L('3D view · drag to turn it · Shift + drag a point to raise it', 'תצוגת 3D · גרירה מסובבת · Shift עם גרירה על נקודה מרים אותה')),
                  h('canvas', {class: 'mk-3d'}))),
            row('height', null, h('div', {class: 'mk-head'},
                h('div', {class: 'mk-name', dir: 'auto'}, L('Height along the path', 'גובה לאורך המסלול')),
                h('div', {class: 'mk-dir', dir: 'auto'}, L('start → end of the path · draw up to climb', 'מתחילת המסלול לסופו · מציירים למעלה כדי לעלות'))), HEIGHT.height));
    }

    function build() {
        const element = h('div', {class: 'mk-app hebrew-ui-skip', dir: 'ltr'});
        element.appendChild(toolbar());
        element.appendChild(h('div', {class: 'mk-info', dir: 'auto'}, ''));
        element.appendChild(rows());
        element.appendChild(pathSection());
        element.appendChild(h('div', {class: 'mk-keys', dir: 'auto'}, L(
            'Keys after a click in the editor: Space play · ← → frame (Shift: second) · D draw · L line · S select · − / = minus / plus · F flip · M prompt · Delete · Ctrl+Z / Ctrl+Y · wheel zoom (Shift: scroll) · Esc',
            'מקשים אחרי לחיצה בעורך: רווח ניגון · חצים פריים (Shift: שנייה) · D ציור · L קו · S סימון · − / = מינוס / פלוס · F הפוך · M פרומפט · Delete · Ctrl+Z / Ctrl+Y · גלגלת זום (Shift: גלילה) · Esc')));
        rowsSignature = signature();
        return element;
    }

    function signature() {
        return `${state.mode}|${Core().laneOrder(state).join(',')}|${state.shown.cfg_scale_schedule}|${hebrew()}|${state.song ? state.song.name : ''}`;
    }

    // Everything is built from the state again (after a Load, a language switch or a change of lanes)
    function rebuild() {
        const element = host();
        if (!element) return;
        const old = element.querySelector(':scope > .mk-app');
        const fullscreen = !!(old && old.classList.contains('mk-fullscreen'));
        const scroll = old ? old.scrollTop : 0;
        // the part that was used last stays where it was on the screen, even when lanes above it come or go
        const anchor = old && ui.anchor ? old.querySelector(ui.anchor) : null;
        const anchorTop = anchor ? anchor.getBoundingClientRect().top : null;
        element.classList.toggle('mk-loaded', !!state);
        if (!state) {
            if (old) old.remove();
            root = null;
            return;
        }
        root = build();
        root.classList.toggle('mk-fullscreen', fullscreen);
        root.classList.toggle('mk-armed', armed);
        if (old) old.replaceWith(root); // in place, so the page does not jump
        else element.appendChild(root);
        root.scrollTop = scroll;
        const again = anchorTop !== null ? root.querySelector(ui.anchor) : null;
        if (again) {
            const delta = again.getBoundingClientRect().top - anchorTop;
            if (fullscreen) root.scrollTop += delta;
            else window.scrollBy(0, delta);
        }
        if (resizer) resizer.observe(root);
        renderNow();
    }

    // --- drawing ---------------------------------------------------------------------------------------------

    let colors = null;

    function readColors() {
        const style = getComputedStyle(root);
        const read = (name, fallback) => style.getPropertyValue(name).trim() || fallback;
        colors = {
            text: style.color || '#888', bg: read('--mk-bg', 'rgba(127,127,127,0.07)'), grid: read('--mk-grid', 'rgba(127,127,127,0.25)'),
            plus: read('--mk-plus', '#3b82f6'), minus: read('--mk-minus', '#ef4444'), minusBg: read('--mk-minus-bg', 'rgba(239,68,68,0.12)'),
            music: read('--mk-music', 'rgba(127,127,127,0.5)'), bass: read('--mk-bass', '#f97316'), beat: read('--mk-beat', 'rgba(250,204,21,0.8)'),
            part: read('--mk-part', '#22c55e'), prompt: read('--mk-prompt', '#a855f7'), playhead: read('--mk-playhead', '#ef4444'),
            select: read('--mk-select', 'rgba(59,130,246,0.18)'), path: read('--mk-path', '#06b6d4'), driven: read('--mk-driven', 'rgba(6,182,212,0.16)'),
            strength: read('--mk-strength', '#10b981'), plan: read('--mk-plan', '#38bdf8'),
        };
    }

    // Canvas text in the direction of its first letter, so Hebrew (the UI or a prompt) reads right to left in place
    function canvasText(ctx, value, x, y) {
        const rtl = /^[^A-Za-z֐-׿]*[֐-׿]/.test(value);
        const align = ctx.textAlign;
        ctx.direction = rtl ? 'rtl' : 'ltr';
        if (align === 'start') ctx.textAlign = 'left';
        ctx.fillText(value, x, y);
        ctx.direction = 'ltr';
        ctx.textAlign = align;
    }

    function prepare(canvas) {
        if (!canvas) return null;
        const width = canvas.clientWidth;
        const height = canvas.clientHeight;
        if (!width || !height) return null;
        const ratio = window.devicePixelRatio || 1;
        const w = Math.round(width * ratio);
        const hgt = Math.round(height * ratio);
        if (canvas.width !== w || canvas.height !== hgt) {
            canvas.width = w;
            canvas.height = hgt;
        }
        const ctx = canvas.getContext('2d');
        ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
        ctx.clearRect(0, 0, width, height);
        return {ctx, width, height};
    }

    // visit(x0, x1, from, to) for every pixel column (or every frame, when frames are wider than a pixel)
    function columns(width, visit) {
        const perPixel = (ui.view.end - ui.view.start) / width;
        if (perPixel <= 1) {
            const first = Math.max(0, Math.floor(ui.view.start));
            const last = Math.min(state.frames, Math.ceil(ui.view.end));
            for (let f = first; f < last; f++) visit(xOf(f, width), xOf(f + 1, width), f, f + 1);
            return;
        }
        for (let px = 0; px < width; px++) {
            const from = Math.max(0, Math.floor(ui.view.start + px * perPixel));
            if (from >= state.frames) break;
            const to = Math.min(state.frames, Math.max(from + 1, Math.floor(ui.view.start + (px + 1) * perPixel)));
            visit(px, px + 1, from, to);
        }
    }

    function overlays(ctx, width, height, key) {
        const selection = ui.selection;
        if (selection && (!selection.keys || !key || selection.keys.includes(key))) {
            const x0 = xOf(Math.min(selection.from, selection.to), width);
            const x1 = xOf(Math.max(selection.from, selection.to) + 1, width);
            ctx.fillStyle = colors.select;
            ctx.fillRect(x0, 0, Math.max(x1 - x0, 1), height);
            ctx.strokeStyle = colors.plus;
            ctx.lineWidth = 1;
            ctx.strokeRect(Math.round(x0) + 0.5, 0.5, Math.max(Math.round(x1 - x0) - 1, 1), height - 1);
        }
        const frame = currentFrame();
        ctx.fillStyle = colors.playhead;
        ctx.fillRect(Math.round(xOf(frame + 0.5, width)) - 1, 0, 2, height);
    }

    function drawRuler(ctx, width) {
        const fps = state.fps;
        const spanSeconds = (ui.view.end - ui.view.start) / fps;
        const every = [0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300].find((s) => (s / spanSeconds) * width >= 80) || 600;
        ctx.font = '10px sans-serif';
        const first = Math.ceil(ui.view.start / fps / every) * every;
        for (let s = first; s * fps <= ui.view.end; s += every) {
            const x = Math.round(xOf(s * fps, width));
            ctx.fillStyle = colors.grid;
            ctx.fillRect(x, 0, 1, 6);
            ctx.fillStyle = colors.text;
            ctx.globalAlpha = 0.75;
            ctx.fillText(`${clock(s).replace(/(\.\d*?)0+$/, '$1').replace(/\.$/, '')} · ${Math.round(s * fps)}`, x + 3, 9);
            ctx.globalAlpha = 1;
        }
    }

    function drawMusic(canvas) {
        const prep = prepare(canvas);
        if (!prep) return;
        const {ctx, width, height} = prep;
        ctx.fillStyle = colors.bg;
        ctx.fillRect(0, 0, width, height);
        const top = 12;
        const bottom = height - 4;
        const song = state.song;
        if (song) {
            const full = song.bands.Full;
            const bass = song.bands.Bass;
            const step = song.step || 1;
            const peakOf = (data, from, to) => {
                let peak = 0;
                for (let f = from; f < to; f++) {
                    const i = Math.floor(f / step);
                    if (i < data.length) peak = Math.max(peak, data[i]);
                }
                return peak / 999;
            };
            ctx.fillStyle = colors.music;
            columns(width, (x0, x1, from, to) => {
                const hgt = peakOf(full, from, to) * (bottom - top);
                ctx.fillRect(x0, bottom - hgt, Math.max(x1 - x0 - (x1 - x0 > 3 ? 1 : 0), 1), hgt);
            });
            ctx.strokeStyle = colors.bass;
            ctx.lineWidth = 1.2;
            ctx.globalAlpha = 0.8;
            ctx.beginPath();
            let started = false;
            columns(width, (x0, x1, from, to) => {
                const y = bottom - peakOf(bass, from, to) * (bottom - top);
                if (!started) ctx.moveTo((x0 + x1) / 2, y);
                else ctx.lineTo((x0 + x1) / 2, y);
                started = true;
            });
            ctx.stroke();
            ctx.globalAlpha = 1;
            const perFrame = pxPerFrame(width);
            if (perFrame * 4 >= 1 || song.beats.length < width / 3) {
                ctx.fillStyle = colors.beat;
                for (const beat of song.beats) {
                    if (beat < ui.view.start || beat >= ui.view.end) continue;
                    const x = Math.round(xOf(beat + 0.5, width));
                    ctx.fillRect(x - 1, bottom - 8, 3, 8);
                    if (perFrame >= 3) {
                        ctx.globalAlpha = 0.3;
                        ctx.fillRect(x, top, 1, bottom - top);
                        ctx.globalAlpha = 1;
                    }
                }
            }
            ctx.strokeStyle = colors.part;
            ctx.lineWidth = 1.2;
            ctx.setLineDash([4, 3]);
            song.parts.forEach((start, index) => {
                if (start <= 0 || start < ui.view.start || start >= ui.view.end) return;
                const x = Math.round(xOf(start, width)) + 0.5;
                ctx.beginPath();
                ctx.moveTo(x, top);
                ctx.lineTo(x, height);
                ctx.stroke();
                ctx.fillStyle = colors.part;
                canvasText(ctx, `${L('part', 'חלק')} ${index + 1}`, x + 3, top + 10);
            });
            ctx.setLineDash([]);
        }
        drawRuler(ctx, width);
        overlays(ctx, width, height, null);
    }

    function drawPrompts(canvas) {
        const prep = prepare(canvas);
        if (!prep) return;
        const {ctx, width, height} = prep;
        ctx.fillStyle = colors.bg;
        ctx.fillRect(0, 0, width, height);
        overlays(ctx, width, height, 'prompts');
        const active = Core().activePrompt(state, currentFrame());
        let lastRight = -Infinity;
        ctx.font = '11px sans-serif';
        for (const prompt of state.prompts) {
            const x = Math.round(xOf(prompt.frame + 0.5, width));
            if (x < -200 || x > width + 2) continue;
            const selected = prompt.id === ui.selectedPrompt;
            ctx.fillStyle = colors.prompt;
            ctx.fillRect(x - (selected ? 1.5 : 1), 0, selected ? 3 : 2, height);
            let label = `${prompt.pinned ? '📌' : ''}${prompt.frame} ${prompt.text.replace(/\s+/g, ' ').trim() || '…'}`;
            if (prompt.dip) label = `↓${format(prompt.dip.strength, 2)} ${label}`;
            const maxWidth = 170;
            while (label.length > 4 && ctx.measureText(label).width > maxWidth) label = label.slice(0, -2);
            const labelWidth = ctx.measureText(label).width + 8;
            if (x + 2 > lastRight + 2 || selected) {
                ctx.fillStyle = selected ? colors.prompt : 'rgba(168,85,247,0.2)';
                ctx.fillRect(x + 2, 4, labelWidth, 17);
                ctx.fillStyle = selected ? '#fff' : colors.text;
                ctx.font = prompt === active ? 'bold 11px sans-serif' : '11px sans-serif';
                canvasText(ctx, label, x + 6, 16);
                lastRight = x + 2 + labelWidth;
            }
            ctx.beginPath(); // the handle to drag
            ctx.fillStyle = colors.prompt;
            ctx.moveTo(x - 6, height);
            ctx.lineTo(x + 6, height);
            ctx.lineTo(x, height - 10);
            ctx.fill();
        }
        if (!state.promptsWritable) {
            ctx.fillStyle = colors.minus;
            canvasText(ctx, L('The Prompts JSON is not valid: fix it in the Prompts tab and press 📥 Load.',
                           'ה-JSON של הפרומפטים לא תקין: מתקנים בטאב Prompts ולוחצים 📥 טען.'), 8, height - 16);
        }
        if (ui.drag && ui.drag.type === 'prompt-new') {
            ctx.fillStyle = colors.prompt;
            ctx.globalAlpha = 0.5;
            ctx.fillRect(Math.round(xOf(ui.drag.frame + 0.5, width)) - 1, 0, 2, height);
            ctx.globalAlpha = 1;
        }
    }

    function planLabel(step) {
        const parts = [];
        if (step.mode) parts.push(step.mode === 'Video Input' ? L('video', 'וידאו') : step.mode === 'Deforum' ? L('as set', 'כמו בפרויקט') : step.mode);
        if (step.video !== null && step.video !== undefined) {
            parts.push(L('video', 'וידאו') + ' ' + Math.round(step.video * 100) + '%' + (step.frames ? ' \u00d7' + step.frames : ''));
        }
        return parts.join(' + ');
    }

    function drawPlan(canvas) {
        const prep = prepare(canvas);
        if (!prep) return;
        const {ctx, width, height} = prep;
        ctx.fillStyle = colors.bg;
        ctx.fillRect(0, 0, width, height);
        overlays(ctx, width, height, 'plan');
        ctx.font = '11px sans-serif';
        let lastRight = -Infinity;
        for (const step of state.plan) {
            const x = Math.round(xOf(step.at + 0.5, width));
            if (x < -220 || x > width + 2) continue;
            const selected = step.id === ui.selectedPlan;
            // the fade is a bar as long as it takes, so the walk is visible against the music
            if (step.frames && step.video !== null) {
                const end = Math.round(xOf(step.at + step.frames + 0.5, width));
                ctx.fillStyle = 'rgba(56,189,248,0.25)';
                ctx.fillRect(x, height - 7, Math.max(end - x, 2), 5);
            }
            ctx.fillStyle = colors.plan || '#38bdf8';
            ctx.fillRect(x - (selected ? 1.5 : 1), 0, selected ? 3 : 2, height);
            let label = (step.text ? step.text + ' ' : '') + planLabel(step);
            const maxWidth = 190;
            while (label.length > 4 && ctx.measureText(label).width > maxWidth) label = label.slice(0, -2);
            const labelWidth = ctx.measureText(label).width + 8;
            if (x + 2 > lastRight + 2 || selected) {
                ctx.fillStyle = selected ? (colors.plan || '#38bdf8') : 'rgba(56,189,248,0.2)';
                ctx.fillRect(x + 2, 2, labelWidth, 15);
                ctx.fillStyle = selected ? '#042f3f' : colors.text;
                canvasText(ctx, label, x + 6, 13);
                lastRight = x + 2 + labelWidth;
            }
            ctx.beginPath();  // the handle to drag
            ctx.fillStyle = colors.plan || '#38bdf8';
            ctx.moveTo(x - 5, height);
            ctx.lineTo(x + 5, height);
            ctx.lineTo(x, height - 8);
            ctx.fill();
        }
    }

    function drawLane(canvas, key) {
        const prep = prepare(canvas);
        if (!prep) return;
        const {ctx, width, height} = prep;
        const spec = Core().LANES[key];
        const lane = state.lanes[key];
        const driven = Core().isDriven(state, key);
        const finals = values(key);
        const range = laneRange(key);
        const y = (v) => height - PAD - (clamp(v, 0, range * 1.02) / range) * (height - 2 * PAD);
        ctx.fillStyle = driven ? colors.driven : colors.bg;
        ctx.fillRect(0, 0, width, height);
        const isSigned = Core().signed(key);
        // the parts that go the other way
        if (isSigned) {
            ctx.fillStyle = colors.minusBg;
            columns(width, (x0, x1, from, to) => {
                let minus = 0;
                for (let f = from; f < to; f++) {
                    const sign = driven ? (finals[f] - spec.neutral < 0 ? -1 : 1) : lane.sign[f];
                    if (sign < 0) minus++;
                }
                if (minus * 2 >= to - from) ctx.fillRect(x0, 0, Math.max(x1 - x0, 1), height);
            });
        }
        ctx.strokeStyle = colors.grid;
        ctx.lineWidth = 1;
        for (const level of [0.5, 1]) {
            const yy = Math.round(y(range * level)) + 0.5;
            ctx.beginPath();
            ctx.moveTo(0, yy);
            ctx.lineTo(width, yy);
            ctx.stroke();
        }
        // what Deforum gets: filled, blue forward / red the other way
        columns(width, (x0, x1, from, to) => {
            let top = 0;
            let minus = 0;
            for (let f = from; f < to; f++) {
                const value = finals[f];
                top = Math.max(top, magnitude(key, value));
                if (isSigned && value - spec.neutral < 0) minus++;
            }
            ctx.fillStyle = key === 'strength_schedule' ? colors.strength : minus * 2 > to - from ? colors.minus : colors.plus;
            ctx.globalAlpha = driven ? 0.45 : 0.55;
            const yy = y(top);
            ctx.fillRect(x0, yy, Math.max(x1 - x0 - (x1 - x0 > 3 ? 1 : 0), 1), height - PAD - yy);
            ctx.globalAlpha = 1;
        });
        // the drawn line (differs from the fill when the music moves it, or at the prompts' Strength dips)
        if (!driven) {
            ctx.strokeStyle = colors.text;
            ctx.globalAlpha = 0.85;
            ctx.lineWidth = 1.3;
            ctx.beginPath();
            let started = false;
            columns(width, (x0, x1, from, to) => {
                let top = 0;
                for (let f = from; f < to; f++) top = Math.max(top, lane.mag[f]);
                const yy = y(top);
                if (!started) ctx.moveTo(x0, yy);
                else ctx.lineTo(x0, yy);
                ctx.lineTo(x1, yy);
                started = true;
            });
            ctx.stroke();
            ctx.globalAlpha = 1;
        }
        if (key === 'strength_schedule') {
            ctx.fillStyle = colors.prompt;
            for (const prompt of state.prompts) {
                if (!prompt.dip) continue;
                const x = xOf(prompt.frame + 0.5, width);
                const yy = y(prompt.dip.strength);
                ctx.beginPath();
                ctx.moveTo(x - 5, yy - 6);
                ctx.lineTo(x + 5, yy - 6);
                ctx.lineTo(x, yy);
                ctx.fill();
            }
        }
        if (driven) {
            ctx.fillStyle = colors.path;
            ctx.font = 'bold 11px sans-serif';
            canvasText(ctx, L('🎥 from the camera path (bake to edit)', '🎥 מהמסלול (אפה כדי לערוך)'), 8, 14);
        }
        if (ui.drag && ui.drag.type === 'line' && ui.drag.key === key) {
            ctx.strokeStyle = colors.playhead;
            ctx.lineWidth = 2;
            ctx.setLineDash([5, 3]);
            ctx.beginPath();
            ctx.moveTo(xOf(ui.drag.f0 + 0.5, width), y(ui.drag.v0));
            ctx.lineTo(xOf(ui.drag.f1 + 0.5, width), y(ui.drag.v1));
            ctx.stroke();
            ctx.setLineDash([]);
        }
        overlays(ctx, width, height, key);
        if (ui.hover && ui.hover.key === key && ui.hover.row === 'lane') {
            const frame = frameAt(ui.hover.x, width);
            const text = `🎞 ${frame} · ${format(finals[frame], spec.decimals)}`;
            ctx.font = '11px sans-serif';
            const boxWidth = ctx.measureText(text).width + 8;
            const left = clamp(ui.hover.x + 10, 0, width - boxWidth);
            ctx.fillStyle = 'rgba(0,0,0,0.75)';
            ctx.fillRect(left, 2, boxWidth, 16);
            ctx.fillStyle = '#fff';
            ctx.fillText(text, left + 4, 14);
        }
    }

    function drawOverview(canvas) {
        const prep = prepare(canvas);
        if (!prep) return;
        const {ctx, width, height} = prep;
        ctx.fillStyle = colors.bg;
        ctx.fillRect(0, 0, width, height);
        const frames = state.frames;
        if (state.song) {
            const full = state.song.bands.Full;
            const step = state.song.step || 1;
            ctx.fillStyle = colors.music;
            for (let px = 0; px < width; px++) {
                const from = Math.floor((px / width) * frames);
                const to = Math.max(from + 1, Math.floor(((px + 1) / width) * frames));
                let peak = 0;
                for (let f = from; f < to; f++) peak = Math.max(peak, full[Math.floor(f / step)] || 0);
                const hgt = (peak / 999) * (height - 4);
                ctx.fillRect(px, height - 2 - hgt, 1, hgt);
            }
        }
        const x0 = (ui.view.start / frames) * width;
        const x1 = (ui.view.end / frames) * width;
        ctx.strokeStyle = colors.plus;
        ctx.lineWidth = 2;
        ctx.strokeRect(x0 + 1, 1, Math.max(x1 - x0 - 2, 3), height - 2);
        ctx.fillStyle = colors.playhead;
        ctx.fillRect(Math.round((currentFrame() / frames) * width), 0, 2, height);
    }

    // --- the camera path views ---------------------------------------------------------------------------

    function mapTransform(width, height) {
        const size = Math.min(width, height);
        const scale = (size / 2 - 8) / 1.1;
        const cx = width / 2;
        const cy = height / 2;
        return {scale, toScreen: (p) => ({x: cx + p.x * scale, y: cy - p.z * scale}),
                toMap: (x, y) => ({x: (x - cx) / scale, z: (cy - y) / scale})};
    }

    function cameraAtFrame() {
        const f = currentFrame();
        const result = Core().pathActive(state) ? Core().pathResult(state) : null;
        if (!result) {
            const lanesTrail = trail();
            return lanesTrail.moves ? lanesTrail.at(f) : null;
        }
        return {x: result.x[f], y: result.y[f], z: result.z[f], yaw: result.yaw[f], pitch: result.pitch[f]};
    }

    function drawMap(canvas) {
        const prep = prepare(canvas);
        if (!prep) return;
        const {ctx, width, height} = prep;
        const m = mapTransform(width, height);
        ctx.fillStyle = colors.bg;
        ctx.fillRect(0, 0, width, height);
        ctx.strokeStyle = colors.grid;
        ctx.lineWidth = 1;
        for (let i = -4; i <= 4; i++) {
            const a = m.toScreen({x: i / 4, z: -1});
            const b = m.toScreen({x: i / 4, z: 1});
            const c = m.toScreen({x: -1, z: i / 4});
            const d = m.toScreen({x: 1, z: i / 4});
            ctx.globalAlpha = i === 0 ? 1 : 0.6;
            ctx.beginPath();
            ctx.moveTo(a.x, a.y);
            ctx.lineTo(b.x, b.y);
            ctx.moveTo(c.x, c.y);
            ctx.lineTo(d.x, d.y);
            ctx.stroke();
        }
        ctx.globalAlpha = 1;
        ctx.fillStyle = colors.text;
        ctx.font = '10px sans-serif';
        const up = m.toScreen({x: 0, z: 1.05});
        canvasText(ctx, L('↑ forward', '↑ קדימה'), up.x + 4, up.y + 4);
        const path = built();
        const active = Core().pathActive(state);
        const lanesTrail = active ? null : trail();
        if (lanesTrail && lanesTrail.moves) {
            ctx.strokeStyle = colors.bass;
            ctx.lineWidth = 2;
            ctx.setLineDash([5, 4]);
            ctx.beginPath();
            const step = Math.max(1, Math.floor(state.frames / 600));
            for (let f = 0; f < state.frames; f += step) {
                const q = m.toScreen(lanesTrail.at(f));
                if (f === 0) ctx.moveTo(q.x, q.y);
                else ctx.lineTo(q.x, q.y);
            }
            ctx.stroke();
            ctx.setLineDash([]);
            ctx.fillStyle = colors.bass;
            ctx.font = '10px sans-serif';
            canvasText(ctx, L('- - where the lanes take the camera now', '- - לאן השורות לוקחות את המצלמה עכשיו'), 8, height - 8);
        }
        if (path.x.length > 1) {
            ctx.strokeStyle = colors.path;
            ctx.globalAlpha = active ? 1 : 0.45;
            ctx.lineWidth = 2.5;
            ctx.beginPath();
            for (let i = 0; i < path.x.length; i++) {
                const p = m.toScreen({x: path.x[i], z: path.z[i]});
                if (i === 0) ctx.moveTo(p.x, p.y);
                else ctx.lineTo(p.x, p.y);
            }
            ctx.stroke();
            // arrows along the way
            const arrows = 8;
            for (let k = 1; k < arrows; k++) {
                const s = (path.total * k) / arrows;
                const p = Core().pointAt(path, s);
                const heading = Core().headingAt(path, s, path.total / 200);
                const q = m.toScreen(p);
                ctx.save();
                ctx.translate(q.x, q.y);
                ctx.rotate(heading.yaw);
                ctx.fillStyle = colors.path;
                ctx.beginPath();
                ctx.moveTo(0, -6);
                ctx.lineTo(4, 3);
                ctx.lineTo(-4, 3);
                ctx.fill();
                ctx.restore();
            }
            ctx.globalAlpha = 1;
        }
        state.path.points.forEach((point, index) => {
            const p = m.toScreen(point);
            ctx.fillStyle = index === 0 ? colors.part : index === state.path.points.length - 1 && !state.path.closed ? colors.minus : colors.path;
            ctx.beginPath();
            ctx.arc(p.x, p.y, ui.drag && ui.drag.type === 'point' && ui.drag.index === index ? 7 : 5, 0, 2 * Math.PI);
            ctx.fill();
            ctx.strokeStyle = '#fff';
            ctx.lineWidth = 1;
            ctx.stroke();
        });
        if (ui.drag && ui.drag.type === 'stroke') {
            ctx.strokeStyle = colors.playhead;
            ctx.lineWidth = 2;
            ctx.beginPath();
            ui.drag.points.forEach((point, i) => {
                const p = m.toScreen(point);
                if (i === 0) ctx.moveTo(p.x, p.y);
                else ctx.lineTo(p.x, p.y);
            });
            ctx.stroke();
        }
        const camera = cameraAtFrame();
        if (camera) {
            const p = m.toScreen(camera);
            ctx.save();
            ctx.translate(p.x, p.y);
            ctx.rotate(camera.yaw);
            ctx.fillStyle = colors.playhead;
            ctx.beginPath();
            ctx.moveTo(0, -11);
            ctx.lineTo(7, 7);
            ctx.lineTo(-7, 7);
            ctx.closePath();
            ctx.fill();
            ctx.restore();
        }
        if (!state.path.points.length) {
            ctx.fillStyle = colors.text;
            ctx.font = '12px sans-serif';
            ctx.textAlign = 'center';
            canvasText(ctx, L('✏ Draw the path here', '✏ מציירים כאן את המסלול'), width / 2, height / 2 - 20);
            ctx.textAlign = 'start';
        }
    }

    const vec = {
        sub: (a, b) => ({x: a.x - b.x, y: a.y - b.y, z: a.z - b.z}),
        dot: (a, b) => a.x * b.x + a.y * b.y + a.z * b.z,
        cross: (a, b) => ({x: a.y * b.z - a.z * b.y, y: a.z * b.x - a.x * b.z, z: a.x * b.y - a.y * b.x}),
        norm: (a) => {
            const length = Math.hypot(a.x, a.y, a.z) || 1;
            return {x: a.x / length, y: a.y / length, z: a.z / length};
        },
    };

    function camera3D(width, height) {
        const o = ui.orbit;
        const cp = Math.cos(o.pitch);
        const eye = {x: o.distance * cp * Math.sin(o.yaw), y: o.distance * Math.sin(o.pitch), z: -o.distance * cp * Math.cos(o.yaw)};
        const f = vec.norm({x: -eye.x, y: -eye.y, z: -eye.z});
        const r = vec.norm(vec.cross({x: 0, y: 1, z: 0}, f));
        const u = vec.cross(f, r);
        const focal = height * 1.15;
        const view = (p) => {
            const v = vec.sub(p, eye);
            return {x: vec.dot(v, r), y: vec.dot(v, u), z: vec.dot(v, f)};
        };
        const screen = (v) => ({x: width / 2 + (focal * v.x) / v.z, y: height / 2 - (focal * v.y) / v.z});
        return {eye, f, r, u, focal, view, screen, project: (p) => {
            const v = view(p);
            return v.z > 0.05 ? screen(v) : null;
        }};
    }

    function line3(ctx, cam, a, b) {
        let va = cam.view(a);
        let vb = cam.view(b);
        const near = 0.05;
        if (va.z < near && vb.z < near) return;
        if (va.z < near || vb.z < near) {
            const t = (near - va.z) / (vb.z - va.z);
            const cut = {x: va.x + (vb.x - va.x) * t, y: va.y + (vb.y - va.y) * t, z: near};
            if (va.z < near) va = cut;
            else vb = cut;
        }
        const p = cam.screen(va);
        const q = cam.screen(vb);
        ctx.moveTo(p.x, p.y);
        ctx.lineTo(q.x, q.y);
    }

    function draw3D(canvas) {
        const prep = prepare(canvas);
        if (!prep) return;
        const {ctx, width, height} = prep;
        const cam = camera3D(width, height);
        ctx.fillStyle = colors.bg;
        ctx.fillRect(0, 0, width, height);
        ctx.strokeStyle = colors.grid;
        ctx.lineWidth = 1;
        ctx.beginPath();
        for (let i = -4; i <= 4; i++) {
            line3(ctx, cam, {x: i / 4, y: 0, z: -1}, {x: i / 4, y: 0, z: 1});
            line3(ctx, cam, {x: -1, y: 0, z: i / 4}, {x: 1, y: 0, z: i / 4});
        }
        ctx.stroke();
        ctx.strokeStyle = colors.part; // which way is forward
        ctx.lineWidth = 2;
        ctx.beginPath();
        line3(ctx, cam, {x: 0, y: 0, z: 1}, {x: 0, y: 0, z: 1.25});
        ctx.stroke();
        const path = built();
        const active = Core().pathActive(state);
        if (path.x.length > 1) {
            ctx.strokeStyle = colors.text;
            ctx.globalAlpha = 0.25;
            ctx.setLineDash([4, 4]);
            ctx.lineWidth = 1.5;
            ctx.beginPath();
            for (let i = 1; i < path.x.length; i++) {
                line3(ctx, cam, {x: path.x[i - 1], y: 0, z: path.z[i - 1]}, {x: path.x[i], y: 0, z: path.z[i]});
            }
            ctx.stroke();
            ctx.setLineDash([]);
            ctx.globalAlpha = active ? 1 : 0.5;
            ctx.strokeStyle = colors.path;
            ctx.lineWidth = 3;
            ctx.beginPath();
            for (let i = 1; i < path.x.length; i++) {
                line3(ctx, cam, {x: path.x[i - 1], y: path.y[i - 1], z: path.z[i - 1]}, {x: path.x[i], y: path.y[i], z: path.z[i]});
            }
            ctx.stroke();
            ctx.globalAlpha = 1;
        }
        const lanesTrail = active ? null : trail();
        if (lanesTrail && lanesTrail.moves) {
            ctx.strokeStyle = colors.bass;
            ctx.lineWidth = 2;
            ctx.setLineDash([5, 4]);
            ctx.beginPath();
            const step = Math.max(1, Math.floor(state.frames / 600));
            let previous = lanesTrail.at(0);
            for (let f = step; f < state.frames; f += step) {
                const next = lanesTrail.at(f);
                line3(ctx, cam, previous, next);
                previous = next;
            }
            ctx.stroke();
            ctx.setLineDash([]);
        }
        state.path.points.forEach((point, index) => {
            const heightAt = Core().sampleProfile(state.path.heights, path.pointU[index] || 0);
            const top = {x: point.x, y: heightAt, z: point.z};
            ctx.strokeStyle = colors.grid;
            ctx.lineWidth = 1;
            ctx.beginPath();
            line3(ctx, cam, {x: point.x, y: 0, z: point.z}, top);
            ctx.stroke();
            const p = cam.project(top);
            if (!p) return;
            ctx.fillStyle = index === 0 ? colors.part : colors.path;
            ctx.beginPath();
            ctx.arc(p.x, p.y, 4.5, 0, 2 * Math.PI);
            ctx.fill();
        });
        const camera = cameraAtFrame() || {x: 0, y: 0, z: 0, yaw: 0, pitch: 0};
        if (camera) {
            const cy = Math.cos(camera.yaw);
            const sy = Math.sin(camera.yaw);
            const cpch = Math.cos(camera.pitch);
            const spch = Math.sin(camera.pitch);
            const F = {x: sy * cpch, y: spch, z: cy * cpch};
            const R = {x: cy, y: 0, z: -sy};
            const U = {x: -sy * spch, y: cpch, z: -cy * spch};
            const at = (a, b, c) => ({x: camera.x + F.x * a + R.x * b + U.x * c, y: camera.y + F.y * a + R.y * b + U.y * c,
                                     z: camera.z + F.z * a + R.z * b + U.z * c});
            const apex = at(0, 0, 0);
            const corners = [at(0.22, -0.13, 0.08), at(0.22, 0.13, 0.08), at(0.22, 0.13, -0.08), at(0.22, -0.13, -0.08)];
            ctx.strokeStyle = colors.playhead;
            ctx.lineWidth = 1.8;
            ctx.beginPath();
            for (let i = 0; i < 4; i++) {
                line3(ctx, cam, apex, corners[i]);
                line3(ctx, cam, corners[i], corners[(i + 1) % 4]);
            }
            ctx.stroke();
        }
        if (ui.hover && ui.hover.row === '3d' && ui.hover.point >= 0) {
            ctx.fillStyle = colors.text;
            ctx.font = '11px sans-serif';
            canvasText(ctx, L('drag: move · Shift + drag: up / down', 'גרירה: הזזה · Shift עם גרירה: למעלה / למטה'), 8, height - 8);
        }
    }

    function drawHeight(canvas) {
        const prep = prepare(canvas);
        if (!prep) return;
        const {ctx, width, height} = prep;
        const path = state.path;
        const hmax = path.heightMax;
        const y = (v) => height / 2 - (v / hmax) * (height / 2 - PAD);
        ctx.fillStyle = colors.bg;
        ctx.fillRect(0, 0, width, height);
        ctx.strokeStyle = colors.grid;
        ctx.beginPath();
        ctx.moveTo(0, Math.round(height / 2) + 0.5);
        ctx.lineTo(width, Math.round(height / 2) + 0.5);
        ctx.stroke();
        const n = path.heights.length;
        ctx.fillStyle = colors.path;
        ctx.globalAlpha = 0.35;
        ctx.beginPath();
        ctx.moveTo(0, height / 2);
        for (let i = 0; i < n; i++) ctx.lineTo((i / (n - 1)) * width, y(path.heights[i]));
        ctx.lineTo(width, height / 2);
        ctx.fill();
        ctx.globalAlpha = 1;
        ctx.strokeStyle = colors.path;
        ctx.lineWidth = 2;
        ctx.beginPath();
        for (let i = 0; i < n; i++) {
            if (i === 0) ctx.moveTo(0, y(path.heights[0]));
            else ctx.lineTo((i / (n - 1)) * width, y(path.heights[i]));
        }
        ctx.stroke();
        const pathBuilt = built();
        ctx.fillStyle = colors.path;
        (pathBuilt.pointU || []).forEach((u) => ctx.fillRect(Math.round(u * width) - 1, height - 8, 2, 8));
        ctx.fillStyle = colors.text;
        ctx.font = '10px sans-serif';
        ctx.fillText(`+${format(hmax, 2)}`, 4, 11);
        ctx.fillText(`−${format(hmax, 2)}`, 4, height - 3);
        if (ui.drag && ui.drag.type === 'height-line') {
            ctx.strokeStyle = colors.playhead;
            ctx.setLineDash([5, 3]);
            ctx.beginPath();
            ctx.moveTo((ui.drag.i0 / (n - 1)) * width, y(ui.drag.v0));
            ctx.lineTo((ui.drag.i1 / (n - 1)) * width, y(ui.drag.v1));
            ctx.stroke();
            ctx.setLineDash([]);
        }
    }

    // --- the parts that are not canvases -----------------------------------------------------------------

    function updateControls() {
        root.querySelectorAll('[data-mk-tool]').forEach((b) => b.classList.toggle('mk-on', b.dataset.mkTool === ui.tool));
        root.querySelectorAll('[data-mk-path-tool]').forEach((b) => b.classList.toggle('mk-on', b.dataset.mkPathTool === ui.pathTool));
        const selection = ui.selection;
        root.querySelectorAll('[data-mk-op]').forEach((b) => {
            b.disabled = !selection || (SIGN_OPS.includes(b.dataset.mkOp) && selection.keys && !selection.keys.some((k) => Core().signed(k)));
        });
        const undo = root.querySelector('[data-mk="undo"]');
        const redo = root.querySelector('[data-mk="redo"]');
        if (undo) undo.disabled = !state.history.undo.length;
        if (redo) redo.disabled = !state.history.redo.length;
        const rolls = Core().pathRolls(state);
        root.querySelectorAll('input[data-mk-write]').forEach((box) => {
            const key = box.dataset.mkWrite;
            const forced = Core().isDriven(state, key) || (key === 'rotation_3d_z' && rolls); // the path needs it written
            box.checked = state.lanes[key].write || forced;
            box.disabled = forced;
        });
        root.querySelectorAll('.mk-max').forEach((span) => setText(span, format(state.lanes[span.dataset.key].max, 4)));
        root.querySelectorAll('.mk-row-lane').forEach((rowElement) => rowElement.classList.toggle('mk-driven', Core().isDriven(state, rowElement.dataset.key)));
        const enabled = root.querySelector('[data-mk-path="enabled"]');
        if (enabled) enabled.checked = state.path.enabled;
        const closed = root.querySelector('[data-mk-path="closed"]');
        if (closed) closed.checked = state.path.closed;
        const bake = root.querySelector('[data-mk="bake"]');
        if (bake) bake.disabled = !Core().pathActive(state);
        updatePromptEditor();
        const info = root.querySelector('.mk-info');
        setText(info, infoText());
        const frame = currentFrame();
        setText(root.querySelector('.mk-clock'), `${clock((frame + 0.5) / state.fps)} · 🎞 ${frame} / ${state.frames - 1}`);
    }

    function infoText() {
        if (ui.info) return ui.info;
        const selection = ui.selection;
        if (selection) {
            const from = Math.min(selection.from, selection.to);
            const to = Math.max(selection.from, selection.to);
            const where = selection.keys ? selection.keys.map((k) => L(Core().LANES[k].en, Core().LANES[k].he)).join(', ') : L('every lane', 'כל השורות');
            return L(`Selected frames ${from}–${to} (${clock(from / state.fps)} – ${clock((to + 1) / state.fps)}) in ${where}. − Minus sends the camera the other way there.`,
                     `מסומנים פריימים ${ltr(`${from}–${to}`)} (${ltr(`${clock(from / state.fps)} – ${clock((to + 1) / state.fps)}`)}) ב${where}. − מינוס שולח את המצלמה לכיוון ההפוך שם.`);
        }
        const unsaved = state.dirty ? L(' · changes not applied yet', ' · יש שינויים שעוד לא הוחלו') : '';
        return L(`${state.frames} frames · ${state.fps} fps · ${state.mode} · Cadence ${state.cadence}${unsaved}`,
                 `${state.frames} פריימים · ${ltr(`${state.fps} FPS · ${state.mode} · Cadence ${state.cadence}`)}${unsaved}`);
    }

    function say(english, hebrewText) {
        ui.info = L(english, hebrewText);
        clearTimeout(say.timer);
        say.timer = setTimeout(() => {
            ui.info = null;
            if (root) setText(root.querySelector('.mk-info'), infoText());
        }, 5000);
        if (root) setText(root.querySelector('.mk-info'), ui.info);
    }

    function updatePromptEditor() {
        const editor = root.querySelector('.mk-prompt-editor');
        if (!editor) return;
        const prompt = ui.selectedPrompt !== null ? Core().promptById(state, ui.selectedPrompt) : null;
        editor.hidden = !prompt;
        if (!prompt) return;
        const active = document.activeElement;
        const set = (selector, value) => {
            const input = editor.querySelector(selector);
            if (input && input !== active && input.value !== String(value)) input.value = String(value);
        };
        set('.mk-pe-frame', prompt.frame);
        set('.mk-pe-text', prompt.text);
        set('.mk-pe-dip', prompt.dip ? prompt.dip.strength : '');
        set('.mk-pe-back', prompt.dip ? prompt.dip.frames : 6);
        setText(editor.querySelector('.mk-pe-time'), clock(prompt.frame / state.fps));
        setText(editor.querySelector('.mk-pe-pinned'), prompt.pinned ? `📌 ${prompt.key}` : '');
        editor.querySelectorAll('input, textarea, button').forEach((input) => {
            if (input.dataset.mk !== 'prompt-close') input.disabled = !state.promptsWritable;
        });
    }

    // --- render ------------------------------------------------------------------------------------------

    function render() {
        if (pending) return;
        pending = true;
        const run = () => {
            if (!pending) return;
            pending = false;
            renderNow();
        };
        requestAnimationFrame(run);
        setTimeout(run, 60); // requestAnimationFrame waits while the page is in the background
    }

    function renderNow() {
        pending = false;
        if (!state) return;
        if (!root || !root.isConnected) {
            rebuild();
            return;
        }
        if (signature() !== rowsSignature) {
            rebuild();
            return;
        }
        readColors();
        root.querySelectorAll('.mk-row').forEach((rowElement) => {
            const canvas = rowElement.querySelector('canvas');
            const kind = rowElement.dataset.row;
            if (kind === 'music') drawMusic(canvas);
            else if (kind === 'plan') drawPlan(canvas);
            else if (kind === 'prompts') drawPrompts(canvas);
            else if (kind === 'lane') drawLane(canvas, rowElement.dataset.key);
            else if (kind === 'overview') drawOverview(canvas);
            else if (kind === 'height') drawHeight(canvas);
        });
        drawMap(root.querySelector('canvas.mk-map'));
        draw3D(root.querySelector('canvas.mk-3d'));
        updateControls();
    }

    const resizer = typeof ResizeObserver === 'function' ? new ResizeObserver(() => render()) : null;

    // --- editing -----------------------------------------------------------------------------------------

    function local(event, canvas) {
        const rect = canvas.getBoundingClientRect();
        return {x: event.clientX - rect.left, y: event.clientY - rect.top, width: rect.width, height: rect.height};
    }

    function laneValueAt(key, y, height) {
        const range = laneRange(key);
        return clamp(((height - PAD - y) / (height - 2 * PAD)) * range, 0, range);
    }

    function applyOp(op) {
        const selection = ui.selection;
        if (!selection) {
            say('Select a part first: ⬚ Select, then drag on a lane (or on the music for every lane).',
                'קודם מסמנים קטע: ⬚ סימון ואז גרירה על שורה (או על המוזיקה לכל השורות).');
            return;
        }
        const keys = (selection.keys || Core().laneOrder(state)).filter((key) => !Core().isDriven(state, key) &&
            (!SIGN_OPS.includes(op) || Core().signed(key)));
        if (!keys.length) return;
        const token = Core().beginEdit(state, keys);
        let changed = false;
        for (const key of keys) changed = Core().rangeOp(state, key, selection.from, selection.to, op) || changed;
        if (changed) Core().endEdit(state, token, op);
        render();
    }

    function selectPrompt(id) {
        ui.selectedPrompt = id;
        render();
    }

    function addPromptAt(frame, focus) {
        if (!state.promptsWritable) return;
        const at = clamp(Math.round(frame), 0, state.frames - 1);
        const there = Core().promptAt(state, at);
        if (there) {
            selectPrompt(there.id);
            say(`Frame ${at} already has a prompt.`, `בפריים ${at} כבר יש פרומפט.`);
            return;
        }
        let prompt = null;
        Core().edit(state, ['prompts'], 'add prompt', () => {
            prompt = Core().addPrompt(state, at, '');
        });
        selectPrompt(prompt.id);
        if (focus) {
            renderNow();
            const text = root.querySelector('.mk-pe-text');
            if (text) text.focus();
        }
    }

    function pointerDown(event) {
        const canvas = event.target;
        if (!state || !(canvas instanceof HTMLCanvasElement) || !root.contains(canvas) || event.button > 0) return;
        const rowElement = canvas.closest('.mk-row');
        const p = local(event, canvas);
        let started = false;
        if (canvas.classList.contains('mk-map') || canvas.classList.contains('mk-3d')) {
            ui.anchor = canvas.classList.contains('mk-map') ? 'canvas.mk-map' : 'canvas.mk-3d';
        } else if (rowElement) {
            ui.anchor = `.mk-row[data-row="${rowElement.dataset.row}"]` + (rowElement.dataset.key ? `[data-key="${rowElement.dataset.key}"]` : '');
        }
        if (canvas.classList.contains('mk-map')) started = mapDown(event, canvas, p);
        else if (canvas.classList.contains('mk-3d')) started = orbitDown(event, canvas, p);
        else if (rowElement) {
            const kind = rowElement.dataset.row;
            const key = rowElement.dataset.key;
            if (kind === 'lane') started = laneDown(key, p);
            else if (kind === 'music') {
                const frame = frameAt(p.x, p.width);
                ui.drag = {type: 'select-all', anchor: frame, moved: false};
                started = true;
            } else if (kind === 'plan') started = planDown(p);
            else if (kind === 'prompts') started = promptsDown(p);
            else if (kind === 'overview') {
                ui.drag = {type: 'overview'};
                overviewMove(p);
                started = true;
            } else if (kind === 'height') started = heightDown(p);
        }
        if (started) {
            canvas.setPointerCapture(event.pointerId);
            ui.drag.canvas = canvas;
            event.preventDefault();
            render();
        }
    }

    function laneDown(key, p) {
        if (Core().isDriven(state, key)) {
            say('This lane comes from the camera path. 🍞 Bake the path into the lanes to draw on it.',
                'השורה הזו מגיעה מהמסלול. 🍞 אופים את המסלול לשורות כדי לצייר עליה.');
            return false;
        }
        const frame = frameOf(p.x, p.width) - 0.5;
        const value = laneValueAt(key, p.y, p.height);
        if (ui.tool === 'select') {
            const at = frameAt(p.x, p.width);
            ui.selection = {from: at, to: at, keys: [key]};
            ui.drag = {type: 'select', key, anchor: at, moved: false};
        } else if (ui.tool === 'line') {
            ui.drag = {type: 'line', key, f0: frame, v0: value, f1: frame, v1: value};
        } else {
            ui.drag = {type: 'draw', key, token: Core().beginEdit(state, [key]), f: frame, v: value};
            Core().paintLane(state, key, frame, value, frame, value);
        }
        return true;
    }

    function planDown(p) {
        let hit = null;
        let best = 7;
        for (const step of state.plan) {
            const distance = Math.abs(xOf(step.at + 0.5, p.width) - p.x);
            if (distance <= best) {
                best = distance;
                hit = step;
            }
        }
        if (hit) {
            ui.selectedPlan = hit.id;
            ui.drag = {type: 'plan', id: hit.id, token: Core().beginEdit(state, ['plan']), start: hit.at};
            return true;
        }
        ui.selectedPlan = null;
        seek(frameAt(p.x, p.width));
        return false;
    }

    function promptsDown(p) {
        if (!state.promptsWritable) return false;
        let hit = null;
        let best = 7;
        for (const prompt of state.prompts) {
            const distance = Math.abs(xOf(prompt.frame + 0.5, p.width) - p.x);
            if (distance <= best) {
                best = distance;
                hit = prompt;
            }
        }
        if (hit) {
            ui.selectedPrompt = hit.id;
            ui.drag = {type: 'prompt', id: hit.id, token: Core().beginEdit(state, ['prompts']), start: hit.frame, moved: false};
            return true;
        }
        ui.selectedPrompt = null;
        seek(frameAt(p.x, p.width));
        return false;
    }

    function heightDown(p) {
        const n = state.path.heights.length;
        const i = (p.x / p.width) * (n - 1);
        const hmax = state.path.heightMax;
        const value = clamp(((p.height / 2 - p.y) / (p.height / 2 - PAD)) * hmax, -hmax, hmax);
        if (ui.tool === 'line') {
            ui.drag = {type: 'height-line', i0: i, v0: value, i1: i, v1: value};
        } else {
            ui.drag = {type: 'height', token: Core().beginEdit(state, ['path']), i, v: value};
            Core().paintSegment(state.path.heights, i, value, i, value, -hmax, hmax);
            Core().touch(state);
        }
        return true;
    }

    function mapDown(event, canvas, p) {
        const m = mapTransform(p.width, p.height);
        const point = m.toMap(p.x, p.y);
        const near = Core().nearestPoint(state.path.points, point, 12 / m.scale);
        if (near >= 0) {
            ui.drag = {type: 'point', index: near, token: Core().beginEdit(state, ['path'])};
            return true;
        }
        if (ui.pathTool === 'draw') {
            ui.drag = {type: 'stroke', points: [point]};
            return true;
        }
        return false;
    }

    function orbitDown(event, canvas, p) {
        const index = pointIn3D(p);
        if (index >= 0) {
            ui.drag = {type: 'point3d', index, token: Core().beginEdit(state, ['path']), x: p.x, y: p.y, shift: event.shiftKey};
        } else {
            ui.drag = {type: 'orbit', x: p.x, y: p.y};
        }
        return true;
    }

    function pointIn3D(p) {
        const cam = camera3D(p.width, p.height);
        const path = built();
        let best = -1;
        let bestDistance = 11;
        state.path.points.forEach((point, index) => {
            const q = cam.project({x: point.x, y: Core().sampleProfile(state.path.heights, path.pointU[index] || 0), z: point.z});
            if (!q) return;
            const d = Math.hypot(q.x - p.x, q.y - p.y);
            if (d <= bestDistance) {
                bestDistance = d;
                best = index;
            }
        });
        return best;
    }

    function overviewMove(p) {
        const span = ui.view.end - ui.view.start;
        const center = (p.x / p.width) * state.frames;
        setView(center - span / 2, center + span / 2);
    }

    function pointerMove(event) {
        const drag = ui.drag;
        if (!drag || !state) {
            hoverMove(event);
            return;
        }
        const canvas = drag.canvas;
        const p = local(event, canvas);
        if (drag.type === 'draw') {
            const frame = frameOf(p.x, p.width) - 0.5;
            const value = laneValueAt(drag.key, p.y, p.height);
            Core().paintLane(state, drag.key, drag.f, drag.v, frame, value);
            drag.f = frame;
            drag.v = value;
        } else if (drag.type === 'line') {
            drag.f1 = frameOf(p.x, p.width) - 0.5;
            drag.v1 = laneValueAt(drag.key, p.y, p.height);
        } else if (drag.type === 'select' || drag.type === 'select-all') {
            const at = frameAt(p.x, p.width);
            if (at !== drag.anchor) drag.moved = true;
            if (drag.moved) ui.selection = {from: drag.anchor, to: at, keys: drag.type === 'select' ? [drag.key] : null};
        } else if (drag.type === 'plan') {
            const target = Core().snapFrame(state, frameOf(p.x, p.width) - 0.5, ui.snap, 8 / pxPerFrame(p.width));
            Core().movePlanStep(state, drag.id, target);
        } else if (drag.type === 'prompt') {
            const target = Core().snapFrame(state, frameOf(p.x, p.width) - 0.5, ui.snap, 8 / pxPerFrame(p.width));
            if (Core().movePrompt(state, drag.id, target)) drag.moved = drag.moved || target !== drag.start;
        } else if (drag.type === 'overview') {
            overviewMove(p);
        } else if (drag.type === 'height') {
            const n = state.path.heights.length;
            const hmax = state.path.heightMax;
            const i = (p.x / p.width) * (n - 1);
            const value = clamp(((p.height / 2 - p.y) / (p.height / 2 - PAD)) * hmax, -hmax, hmax);
            Core().paintSegment(state.path.heights, drag.i, drag.v, i, value, -hmax, hmax);
            Core().touch(state);
            drag.i = i;
            drag.v = value;
        } else if (drag.type === 'height-line') {
            const n = state.path.heights.length;
            const hmax = state.path.heightMax;
            drag.i1 = (p.x / p.width) * (n - 1);
            drag.v1 = clamp(((p.height / 2 - p.y) / (p.height / 2 - PAD)) * hmax, -hmax, hmax);
        } else if (drag.type === 'stroke') {
            const m = mapTransform(p.width, p.height);
            const point = m.toMap(p.x, p.y);
            const last = drag.points[drag.points.length - 1];
            if (Math.hypot(point.x - last.x, point.z - last.z) * m.scale >= 2) drag.points.push(point);
        } else if (drag.type === 'point') {
            const m = mapTransform(p.width, p.height);
            const point = m.toMap(p.x, p.y);
            state.path.points[drag.index] = {x: clamp(point.x, -1.2, 1.2), z: clamp(point.z, -1.2, 1.2)};
            Core().touch(state);
        } else if (drag.type === 'orbit') {
            ui.orbit.yaw += (p.x - drag.x) * 0.01;
            ui.orbit.pitch = clamp(ui.orbit.pitch + (p.y - drag.y) * 0.01, 0.05, 1.5);
            drag.x = p.x;
            drag.y = p.y;
        } else if (drag.type === 'point3d') {
            const cam = camera3D(p.width, p.height);
            const point = state.path.points[drag.index];
            const u = built().pointU[drag.index] || 0;
            const depth = cam.view({x: point.x, y: Core().sampleProfile(state.path.heights, u), z: point.z}).z;
            const k = Math.max(depth, 0.1) / cam.focal;
            const dx = p.x - drag.x;
            const dy = p.y - drag.y;
            if (event.shiftKey || drag.shift) {
                Core().raiseHeight(state.path, u, -dy * k, 0.07);
            } else {
                const right = vec.norm({x: cam.r.x, y: 0, z: cam.r.z});
                const ahead = vec.norm({x: cam.f.x, y: 0, z: cam.f.z});
                state.path.points[drag.index] = {x: clamp(point.x + (dx * right.x - dy * ahead.x) * k, -1.2, 1.2),
                                                 z: clamp(point.z + (dx * right.z - dy * ahead.z) * k, -1.2, 1.2)};
            }
            Core().touch(state);
            drag.x = p.x;
            drag.y = p.y;
        }
        render();
    }

    function pointerUp(event) {
        const drag = ui.drag;
        if (!drag || !state) return;
        ui.drag = null;
        const canvas = drag.canvas;
        const p = local(event, canvas);
        if (drag.type === 'draw') {
            Core().endEdit(state, drag.token, 'draw');
        } else if (drag.type === 'line') {
            const token = Core().beginEdit(state, [drag.key]);
            if (Core().paintLane(state, drag.key, drag.f0, drag.v0, drag.f1, drag.v1)) Core().endEdit(state, token, 'line');
        } else if (drag.type === 'select' || drag.type === 'select-all') {
            if (!drag.moved) {
                ui.selection = null;
                seek(drag.anchor);
            }
        } else if (drag.type === 'plan') {
            const step = Core().planStepById(state, drag.id);
            if (step && step.at !== drag.start) {
                Core().endEdit(state, drag.token, 'move plan step');
                savePlan();
            }
        } else if (drag.type === 'prompt') {
            const prompt = Core().promptById(state, drag.id);
            if (prompt && prompt.frame !== drag.start) Core().endEdit(state, drag.token, 'move prompt');
        } else if (drag.type === 'height') {
            Core().endEdit(state, drag.token, 'height');
        } else if (drag.type === 'height-line') {
            const hmax = state.path.heightMax;
            Core().edit(state, ['path'], 'height line', () => Core().paintSegment(state.path.heights, drag.i0, drag.v0, drag.i1, drag.v1, -hmax, hmax));
        } else if (drag.type === 'stroke') {
            finishStroke(drag.points, mapTransform(p.width, p.height));
        } else if (drag.type === 'point' || drag.type === 'point3d') {
            Core().endEdit(state, drag.token, 'move point');
        }
        render();
    }

    function finishStroke(points, m) {
        if (points.length < 2) {
            say('Draw a longer line: press and drag across the map.', 'מציירים קו ארוך יותר: לוחצים וגוררים על המפה.');
            return;
        }
        let simple = Core().simplify(points, 3 / m.scale);
        const first = simple[0];
        const last = simple[simple.length - 1];
        const closed = simple.length >= 4 && Math.hypot(first.x - last.x, first.z - last.z) * m.scale < 14;
        if (closed) simple = simple.slice(0, -1);
        Core().edit(state, ['path'], 'draw path', () => {
            state.path.points = simple;
            state.path.closed = closed;
            state.path.enabled = true;
        });
        ui.pathTool = 'edit';
        say(closed ? 'A closed path: the camera goes round it (Laps = how many times).' : 'The camera follows this path now. Drag the points to change it.',
            closed ? 'מסלול סגור: המצלמה מקיפה אותו (סיבובים = כמה פעמים).' : 'המצלמה נוסעת עכשיו במסלול הזה. גוררים נקודות כדי לשנות אותו.');
    }

    function hoverMove(event) {
        if (!state || !root) return;
        const canvas = event.target instanceof HTMLCanvasElement && root.contains(event.target) ? event.target : null;
        let next = null;
        if (canvas) {
            const rowElement = canvas.closest('.mk-row');
            const p = local(event, canvas);
            if (rowElement && rowElement.dataset.row === 'lane') next = {row: 'lane', key: rowElement.dataset.key, x: p.x};
            else if (canvas.classList.contains('mk-3d')) next = {row: '3d', point: pointIn3D(p)};
        }
        const changed = JSON.stringify(next) !== JSON.stringify(ui.hover);
        ui.hover = next;
        if (changed) render();
    }

    function doubleClick(event) {
        const canvas = event.target;
        if (!state || !(canvas instanceof HTMLCanvasElement) || !root.contains(canvas)) return;
        const p = local(event, canvas);
        const rowElement = canvas.closest('.mk-row');
        if (rowElement && rowElement.dataset.row === 'prompts') {
            addPromptAt(Core().snapFrame(state, frameOf(p.x, p.width) - 0.5, ui.snap, 8 / pxPerFrame(p.width)), true);
        } else if (canvas.classList.contains('mk-map')) {
            const m = mapTransform(p.width, p.height);
            Core().edit(state, ['path'], 'add point', () => {
                Core().insertPoint(state.path.points, state.path.closed, m.toMap(p.x, p.y));
                if (state.path.points.length >= 2) state.path.enabled = true;
            });
            render();
        }
    }

    function contextMenu(event) {
        const canvas = event.target;
        if (!state || !(canvas instanceof HTMLCanvasElement) || !canvas.classList.contains('mk-map')) return;
        const p = local(event, canvas);
        const m = mapTransform(p.width, p.height);
        const index = Core().nearestPoint(state.path.points, m.toMap(p.x, p.y), 12 / m.scale);
        if (index < 0) return;
        event.preventDefault();
        Core().edit(state, ['path'], 'delete point', () => state.path.points.splice(index, 1));
        render();
    }

    function wheel(event) {
        if (!state || !armed) return; // the page scrolls normally until the editor is clicked
        const canvas = event.target instanceof HTMLCanvasElement && root.contains(event.target) ? event.target : null;
        if (!canvas) return;
        event.preventDefault();
        if (canvas.classList.contains('mk-3d')) {
            ui.orbit.distance = clamp(ui.orbit.distance * Math.exp(event.deltaY * 0.001), 1.2, 9);
            render();
            return;
        }
        const rowElement = canvas.closest('.mk-row');
        if (!rowElement || rowElement.dataset.row === 'height') return;
        const p = local(event, canvas);
        if (event.shiftKey || Math.abs(event.deltaX) > Math.abs(event.deltaY)) {
            const span = ui.view.end - ui.view.start;
            const delta = (event.shiftKey ? event.deltaY : event.deltaX) / p.width * span;
            setView(ui.view.start + delta, ui.view.end + delta);
            render();
        } else {
            zoomView(Math.exp(event.deltaY * 0.0015), frameOf(p.x, p.width));
        }
    }

    function click(event) {
        const target = event.target;
        if (!state || !root || !target.closest) return;
        const element = target.closest('button');
        if (!element || !root.contains(element)) return;
        const data = element.dataset;
        if (data.mkTool) {
            ui.tool = data.mkTool;
            if (ui.tool !== 'select') ui.selection = null;
        } else if (data.mkPathTool) {
            ui.pathTool = data.mkPathTool;
        } else if (data.mkOp) {
            applyOp(data.mkOp);
        } else if (data.mkRange) {
            const key = data.key;
            const lane = state.lanes[key];
            Core().edit(state, [key], 'range', () => {
                lane.max = Core().niceNext(lane.max, data.mkRange === '+');
            });
        } else if (data.mk) {
            action(data.mk);
        }
        element.blur(); // so Space goes to the editor, not to the button
        render();
    }

    function action(name) {
        if (name === 'play') togglePlay();
        else if (name === 'undo') Core().undo(state);
        else if (name === 'redo') Core().redo(state);
        else if (name === 'zoom-in') zoomView(0.5);
        else if (name === 'zoom-out') zoomView(2);
        else if (name === 'fit') setView(0, state.frames);
        else if (name === 'cfg') Core().edit(state, ['shown'], 'lanes', () => {
            state.shown.cfg_scale_schedule = !state.shown.cfg_scale_schedule;
        });
        else if (name === 'fullscreen') root.classList.toggle('mk-fullscreen');
        else if (name === 'path-clear') {
            Core().edit(state, ['path'], 'clear path', () => {
                state.path.points = [];
                state.path.heights.fill(0);
                state.path.enabled = false;
            });
            ui.pathTool = 'draw';
        } else if (name === 'bake') {
            if (Core().bake(state)) say('The path is in the lanes now: draw on them by hand. Undo brings the path back.',
                                        'המסלול עבר לשורות: אפשר לצייר עליהן ביד. ביטול מחזיר את המסלול.');
        } else if (name === 'prompt-delete') deleteSelectedPrompt();
        else if (name === 'prompt-close') ui.selectedPrompt = null;
        render();
    }

    function deleteSelectedPlanStep() {
        if (ui.selectedPlan === null) return false;
        const id = ui.selectedPlan;
        Core().edit(state, ['plan'], 'delete plan step', () => Core().deletePlanStep(state, id));
        ui.selectedPlan = null;
        savePlan();
        render();
        return true;
    }

    // The plan belongs to the bar above the preview (live_control.py), not to Deforum's schedules, so a move or a
    // delete is written straight back through its own hidden box and button - there is nothing to Apply.
    let planTimer = null;

    function savePlan() {
        if (planTimer) clearTimeout(planTimer);
        planTimer = setTimeout(() => {
            planTimer = null;
            const box = gradio().querySelector('#map_key_plan_request textarea, #map_key_plan_request input');
            const button = gradio().querySelector('#map_key_plan_save');
            if (!box || !button || !state) return;
            box.value = JSON.stringify(Core().planPayload(state));
            box.dispatchEvent(new Event('input', {bubbles: true}));
            setTimeout(() => button.click(), 30);
        }, 120);
    }

    function deleteSelectedPrompt() {
        if (ui.selectedPrompt === null || !state.promptsWritable) return false;
        const id = ui.selectedPrompt;
        Core().edit(state, ['prompts'], 'delete prompt', () => Core().deletePrompt(state, id));
        ui.selectedPrompt = null;
        render();
        return true;
    }

    let textToken = null;

    function change(event) {
        const target = event.target;
        if (!state || !root || !root.contains(target)) return;
        const data = target.dataset;
        if (data.mk === 'snap') {
            ui.snap = target.value;
        } else if (data.mkWrite) {
            const lane = state.lanes[data.mkWrite];
            Core().edit(state, [data.mkWrite], 'write', () => {
                lane.write = target.checked;
            });
        } else if (data.mkMusic) {
            const key = data.key;
            const lane = state.lanes[key];
            Core().edit(state, [key], 'music', () => {
                lane.music = Object.assign({}, lane.music, {[data.mkMusic]: data.mkMusic === 'amount' ? Number(target.value) : target.value});
                Core().markEdited(lane);
            });
        } else if (data.mkPath) {
            const field = data.mkPath;
            Core().edit(state, ['path'], 'path settings', () => {
                if (field === 'enabled' || field === 'closed') state.path[field] = target.checked;
                else if (field === 'look') state.path.look = target.value;
                else if (field === 'laps') state.path.laps = Math.max(0.25, Number(target.value) || 1);
                else if (field === 'lookAhead') state.path.lookAhead = Math.max(0, Math.round(Number(target.value) || 0));
            });
            if (field === 'enabled' && target.checked && state.path.points.length < 2) {
                say('Now draw the path on the map (✏).', 'עכשיו מציירים את המסלול על המפה (✏).');
            }
        } else if (target.classList.contains('mk-pe-frame')) {
            const prompt = Core().promptById(state, ui.selectedPrompt);
            if (prompt) {
                const frame = Math.round(Number(target.value));
                let moved = false;
                Core().edit(state, ['prompts'], 'move prompt', () => {
                    moved = Core().movePrompt(state, prompt.id, frame);
                });
                if (!moved) say(`Frame ${frame} already has a prompt.`, `בפריים ${frame} כבר יש פרומפט.`);
            }
        } else if (target.classList.contains('mk-pe-dip') || target.classList.contains('mk-pe-back')) {
            const prompt = Core().promptById(state, ui.selectedPrompt);
            const editor = root.querySelector('.mk-prompt-editor');
            if (prompt && editor) {
                const strength = editor.querySelector('.mk-pe-dip').value;
                const back = editor.querySelector('.mk-pe-back').value;
                Core().edit(state, ['prompts', 'strength_schedule'], 'transition strength',
                            () => Core().setPromptDip(state, prompt.id, strength === '' ? null : strength, back === '' ? 6 : back));
            }
        } else if (target.classList.contains('mk-pe-text')) {
            finishText();
        }
        render();
    }

    function input(event) {
        const target = event.target;
        if (!state || !root || !target.classList || !target.classList.contains('mk-pe-text')) return;
        const prompt = Core().promptById(state, ui.selectedPrompt);
        if (!prompt) return;
        if (!textToken) textToken = Core().beginEdit(state, ['prompts']);
        Core().setPromptText(state, prompt.id, target.value);
        render();
    }

    function finishText() {
        if (!textToken) return;
        Core().endEdit(state, textToken, 'prompt text');
        textToken = null;
    }

    // event.code, so the letters work with a Hebrew keyboard too; the key itself when a device sends no code
    function keyOf(event) {
        if (event.code) return event.code;
        const key = event.key || '';
        const named = {' ': 'Space', '-': 'Minus', '=': 'Equal', '+': 'Equal'};
        if (named[key]) return named[key];
        return /^[a-z]$/i.test(key) ? `Key${key.toUpperCase()}` : key;
    }

    function keyDown(event) {
        if (!state || !root || !armed || event.altKey || event.metaKey) return;
        const code = keyOf(event);
        const fullscreen = root.classList.contains('mk-fullscreen');
        if (!root.offsetParent && !fullscreen) return; // the Map Key tab is not showing
        const target = event.target;
        const typing = target && (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName));
        if (code === 'Escape') {
            if (fullscreen) root.classList.remove('mk-fullscreen');
            else if (ui.selection) ui.selection = null;
            else ui.selectedPrompt = null;
            event.preventDefault();
            render();
            return;
        }
        if (typing) return;
        const ctrl = event.ctrlKey;
        let handled = true;
        if (ctrl && code === 'KeyZ') {
            if (event.shiftKey) Core().redo(state);
            else Core().undo(state);
        } else if (ctrl && code === 'KeyY') {
            Core().redo(state);
        } else if (ctrl) {
            handled = false;
        } else if (code === 'Space') {
            if (!event.repeat) togglePlay();
        } else if (code === 'ArrowLeft' || code === 'ArrowRight') {
            const step = event.shiftKey ? Math.round(state.fps) : 1;
            const audio = player();
            if (audio && !audio.paused) audio.pause();
            ui.virtual = null;
            seek(currentFrame() + (code === 'ArrowLeft' ? -step : step));
        } else if (code === 'KeyD') {
            ui.tool = 'draw';
        } else if (code === 'KeyL') {
            ui.tool = 'line';
        } else if (code === 'KeyS') {
            ui.tool = 'select';
        } else if (code === 'Minus' || code === 'NumpadSubtract') {
            applyOp('minus');
        } else if (code === 'Equal' || code === 'NumpadAdd') {
            applyOp('plus');
        } else if (code === 'KeyF') {
            applyOp('flip');
        } else if (code === 'KeyM') {
            if (!event.repeat) addPromptAt(currentFrame(), false);
        } else if (code === 'Delete' || code === 'Backspace') {
            if (!deleteSelectedPlanStep() && !deleteSelectedPrompt() && ui.selection) applyOp('reset');
        } else {
            handled = false;
        }
        if (handled) {
            event.preventDefault();
            event.stopPropagation();
            render();
        }
    }

    // --- loading, the page and Python --------------------------------------------------------------------

    function sync() {
        const element = host();
        if (!element || !window.MapKeyCore) return;
        const data = element.querySelector('.mk-data');
        if (data && data.dataset.version && data.dataset.version !== version) {
            version = data.dataset.version;
            let parsed = null;
            try {
                parsed = JSON.parse(data.dataset.json);
            } catch (e) {
                parsed = null;
            }
            state = parsed ? Core().createState(parsed) : null;
            cache = {revision: -1, values: {}, built: null};
            if (state) {
                ui.view = {start: 0, end: state.frames};
                ui.selection = null;
                ui.selectedPrompt = null;
                ui.playFrame = 0;
                ui.virtual = null;
                ui.pathTool = state.path.points.length ? 'edit' : 'draw';
            }
            rebuild();
            return;
        }
        if (state && (!root || !root.isConnected)) rebuild();
    }

    document.addEventListener('pointerdown', (event) => {
        const target = event.target;
        armed = !!(target && target.closest && target.closest('#map_key_editor .mk-app, #map_key_player'));
        if (root) root.classList.toggle('mk-armed', armed);
        if (armed) pointerDown(event);
    }, true);
    document.addEventListener('pointermove', pointerMove);
    document.addEventListener('pointerup', pointerUp);
    document.addEventListener('pointercancel', pointerUp);
    document.addEventListener('dblclick', doubleClick);
    document.addEventListener('contextmenu', contextMenu);
    document.addEventListener('wheel', wheel, {passive: false});
    document.addEventListener('click', click);
    document.addEventListener('change', change);
    document.addEventListener('input', input);
    document.addEventListener('focusout', (event) => {
        if (event.target && event.target.classList && event.target.classList.contains('mk-pe-text')) finishText();
    });
    document.addEventListener('keydown', keyDown, true);
    ['play', 'pause', 'seeked', 'timeupdate', 'loadedmetadata', 'ended'].forEach((type) => document.addEventListener(type, (event) => {
        if (!state || event.target !== player()) return;
        if (type === 'play') {
            armed = true;
            loop();
        } else {
            render();
        }
    }, true));
    new MutationObserver(() => {
        if (state && root && signature() !== rowsSignature) rebuild();
    }).observe(document.documentElement, {attributes: true, attributeFilter: ['class']});
    if (typeof onUiUpdate === 'function') onUiUpdate(sync);

    // The Load / Apply / Save buttons call these first (their _js); `count` is the number of the event's inputs,
    // so the page does not send the outputs' old values along
    function discardOk() {
        if (!state || !state.dirty || window.MapKey.askBeforeDiscard === false) return true;
        return window.confirm(L('Your changes are not applied yet. Load again and lose them?', 'השינויים שלך עוד לא הוחלו. לטעון מחדש ולאבד אותם?'));
    }

    window.mapKeyLoad = function (args, count) {
        const out = Array.from(args).slice(0, count || args.length);
        out[0] = discardOk() ? 'load' : 'skip';
        return out;
    };
    window.mapKeyAutoLoad = function (args, count) {
        const out = Array.from(args).slice(0, count || args.length);
        out[0] = state ? 'skip' : 'load';
        return out;
    };
    window.mapKeyApply = function (args, count) {
        const out = Array.from(args).slice(0, count || args.length);
        if (!state) {
            out[0] = JSON.stringify({version: 1, error: 'There is nothing to apply yet: press 📥 Load from Deforum first.'});
        } else {
            if (textToken) finishText();
            out[0] = JSON.stringify(Core().payload(state));
            state.dirty = false;
            render();
        }
        return out;
    };
    window.mapKeySave = function (args, count) {
        const out = Array.from(args).slice(0, count || args.length);
        out[0] = state ? JSON.stringify(Core().serialize(state)) : '';
        return out;
    };

    // for tests and the console
    window.MapKey = {
        askBeforeDiscard: true,
        get state() {
            return state;
        },
        ui,
        renderNow,
        sync,
    };
})();

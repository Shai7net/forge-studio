// Tab Presets: Save / Load / Recent / Reset for everything that is set in a tab.
//
// The page itself knows every setting: Gradio keeps the live value of each component in
// window.gradio_config.components[n].props.value. A preset is those values, read straight from there, and loading
// one writes them back the same way Gradio applies a server answer, so every "on change" reaction of the tab
// (ControlNet filling its model list, Hires. fix opening, ...) runs as if the user had made the change.
// No Gradio event carries the values, and the events a load does set off are kept cheap, which matters on a page this
// large: see "knowing when the tab has finished reacting" below.

(function() {
    'use strict';

    const VALUE_TYPES = new Set(['textbox', 'number', 'slider', 'checkbox', 'checkboxgroup', 'radio', 'dropdown', 'colorpicker',
        'dataframe']);
    const MEDIA_TYPES = new Set(['image', 'video', 'audio', 'file']);
    const NO_BAR = new Set(['settings', 'extensions', 'pnginfo', 'modelmerger', 'train', 'image_browser', 'system_info',
        'sddp-wildcard-manager']);
    const MIN_FIELDS = 6;

    // values the page uses to talk to itself, and controls that would start an action of their own when set
    const SKIP_ID = /^(generation_info_|html_info_|html_log_|setting_)|_edit_user_metadata|_styles_edit_select$|_edit_style_(neg_)?prompt$/;
    const SKIP_CLASS = ['cnet-preset-dropdown', 'cnet-preset-name'];
    // ids Gradio or the webui number by creation order; they move when an extension is added, labels do not
    const AUTO_ID = /^(component-\d+|input-accordion-\d+(-checkbox)?)$/;
    // txt2img and img2img keep their settings in the first sub-tab ("Generation"); the sub-tabs next to it browse
    // models, and which of them is open is not a setting
    const EXTRA_TABS = /_extra_tabs$/;

    const MODEL_FIELDS = {
        checkpoint: 'setting_sd_model_checkpoint',
        vae: 'setting_sd_vae',
        clip_skip: 'setting_CLIP_stop_at_last_layers',
    };

    // Presets written before format 2 carry the model of the wrong list (see findModel), so theirs is not loaded.
    const FORMAT = 2;

    const QUIET = 250;        // ms without traffic before the tab counts as settled
    const EVENT_BUDGET = 10;  // server events one step may start
    const MAX_ROUNDS = 6;
    const MAX_TRIES = 3;
    const MAX_INLINE_UPLOAD = 300 * 1024 * 1024;

    const TEXT = {
        en: {
            title: 'Presets',
            choose: '— choose a preset —',
            empty: '— nothing saved yet —',
            load: 'Load', loadTip: 'Put the chosen preset back on this tab',
            save: 'Save', saveTip: 'Save what is set on this tab now. With a preset chosen: update it',
            saveAs: 'Save as…', saveAsTip: 'Save what is set on this tab now under a new name',
            recent: 'Recent', recentTip: 'The presets saved or loaded last. Click one to load it',
            reset: 'Reset', resetTip: 'Put the whole tab back to how it opens after a restart',
            more: 'More',
            remove: 'Delete "{name}"', removeSure: 'Click again to delete "{name}"',
            folder: 'Open the presets folder',
            withModel: 'Load the model too (checkpoint, VAE, clip skip)',
            withMedia: 'Keep pictures and files too (ControlNet image, video…)',
            name: 'Name for this preset', ok: 'Save', cancel: 'Cancel', undo: 'Undo',
            noRecent: 'Nothing yet', last: 'Last generation', lastTip: 'What this tab held when Generate was last pressed',
            working: 'Loading "{name}"…', resetting: 'Resetting…', progress: '{done} of {total}', saving: 'Saving…', undoing: 'Undoing…',
            loaded: 'Loaded "{name}": {n} settings changed', loadedSame: '"{name}" is already what is on screen',
            wasReset: 'Tab reset: {n} settings changed', resetSame: 'The tab is already at its starting values',
            undone: 'Back to how it was', saved: 'Saved "{name}" ({n} settings{media})', mediaCount: ', files: {n}',
            removed: 'Deleted "{name}" (moved to the _deleted folder)',
            exists: '"{name}" already exists. Press Save again to replace it',
            needChoice: 'Choose a preset first', needName: 'Type a name first',
            failed: 'Could not set: {list}', missing: 'Not on this computer: {list}',
            modelLoading: 'Loading the model: {name}', modelMissing: 'The model "{name}" is not on this computer',
            unknown: '{n} saved settings no longer exist on this tab',
            error: 'It did not work: {message}',
            now: 'just now', minutes: '{n} min ago', hours: '{n} h ago', yesterday: 'yesterday',
            settings: '{n} settings',
        },
        he: {
            title: 'הגדרות שמורות',
            choose: '— בחירת שמירה —',
            empty: '— עוד לא נשמר כלום —',
            load: 'טען', loadTip: 'להחזיר ללשונית את השמירה שנבחרה',
            save: 'שמור', saveTip: 'לשמור את מה שמוגדר עכשיו בלשונית. כשנבחרה שמירה: לעדכן אותה',
            saveAs: 'שמור בשם…', saveAsTip: 'לשמור את מה שמוגדר עכשיו בלשונית בשם חדש',
            recent: 'אחרונים', recentTip: 'השמירות האחרונות שנשמרו או נטענו. לחיצה טוענת',
            reset: 'איפוס', resetTip: 'להחזיר את כל הלשונית למצב שבו היא נפתחת אחרי הפעלה',
            more: 'עוד',
            remove: 'למחוק את "{name}"', removeSure: 'לחיצה נוספת תמחק את "{name}"',
            folder: 'לפתוח את תיקיית השמירות',
            withModel: 'לטעון גם את המודל (Checkpoint, VAE, Clip skip)',
            withMedia: 'לשמור גם תמונות וקבצים (תמונת ControlNet, וידאו…)',
            name: 'שם לשמירה', ok: 'שמור', cancel: 'ביטול', undo: 'בטל',
            noRecent: 'אין עדיין', last: 'היצירה האחרונה', lastTip: 'מה שהיה מוגדר בלשונית בלחיצה האחרונה על צור',
            working: 'טוען את "{name}"…', resetting: 'מאפס…', progress: '{done} מתוך {total}', saving: 'שומר…', undoing: 'מבטל…',
            loaded: 'נטען "{name}": {n} הגדרות השתנו', loadedSame: '"{name}" כבר זהה למה שעל המסך',
            wasReset: 'הלשונית אופסה: {n} הגדרות השתנו', resetSame: 'הלשונית כבר במצב ההתחלתי',
            undone: 'חזר למצב הקודם', saved: 'נשמר "{name}" ({n} הגדרות{media})', mediaCount: ', קבצים: {n}',
            removed: 'נמחק "{name}" (הועבר לתיקייה \u200e_deleted)',
            exists: '"{name}" כבר קיים. לחיצה נוספת על שמור תחליף אותו',
            needChoice: 'קודם לבחור שמירה', needName: 'קודם לכתוב שם',
            failed: 'לא הצליח לקבוע: {list}', missing: 'לא נמצא במחשב הזה: {list}',
            modelLoading: 'טוען את המודל: {name}', modelMissing: 'המודל "{name}" לא נמצא במחשב הזה',
            unknown: '{n} הגדרות שנשמרו כבר לא קיימות בלשונית',
            error: 'לא הצליח: {message}',
            now: 'עכשיו', minutes: 'לפני {n} דק׳', hours: 'לפני {n} שע׳', yesterday: 'אתמול',
            settings: '{n} הגדרות',
        },
    };

    function hebrew() {
        return document.documentElement.classList.contains('hebrew-ui-active');
    }

    function t(key, values) {
        let text = (hebrew() ? TEXT.he : TEXT.en)[key] || TEXT.en[key] || key;
        for (const name in values || {}) {
            // a preset name can be Hebrew inside an English sentence or the other way round; the isolate marks keep
            // it from reordering the words and numbers around it
            const value = name === 'name' || name === 'list' ? '\u2068' + values[name] + '\u2069' : String(values[name]);
            text = text.split('{' + name + '}').join(value);
        }
        return text;
    }

    // A page that is not in front gets its timers once a second, later once a minute, which would stretch a load
    // from seconds to minutes when the window is behind another one. A worker's timers are not held back, so the
    // waiting is done there.
    const workerSleep = (function() {
        try {
            const source = 'onmessage = function(e) { setTimeout(function() { postMessage(e.data[0]); }, e.data[1]); };';
            const worker = new Worker(URL.createObjectURL(new Blob([source], {type: 'text/javascript'})));
            const waiting = new Map();
            let last = 0;
            worker.onmessage = function(event) {
                const resolve = waiting.get(event.data);
                waiting.delete(event.data);
                if (resolve) resolve();
            };
            return function(ms) {
                return new Promise(function(resolve) {
                    waiting.set(++last, resolve);
                    worker.postMessage([last, ms]);
                });
            };
        } catch (e) {
            return null;
        }
    })();

    function sleep(ms) {
        return workerSleep ? workerSleep(ms) : new Promise(function(resolve) {
            setTimeout(resolve, ms);
        });
    }

    // --- knowing when the tab has finished reacting -------------------------------------------------------------
    //
    // Setting a value makes Gradio call the server for every "on change" listener, and the answers may set further
    // values. Loading has to wait for those answers before it checks the result, so the calls are counted here:
    // un-queued ones go through fetch('/run/predict'), queued ones each open a WebSocket to '/queue/join'.

    const net = {active: new Set(), last: 0, started: 0, dropped: 0, loading: 0, skipped: 0};

    // While a preset loads, two of the messages a queued event receives are left out: "estimation" (the place in
    // the queue) and "process_starts". Gradio's client only shows them, and on this page showing anything means
    // redrawing all ~9,000 components, 150-200 ms each time. Without them an event costs one redraw instead of three.
    function onlyShown(data) {
        if (typeof data !== 'string' || data.length > 600) return false;
        try {
            const kind = JSON.parse(data).msg;
            return kind === 'estimation' || kind === 'process_starts';
        } catch (e) {
            return false;
        }
    }

    // The answers themselves cost a redraw each too, unless they reach Gradio in one go: Svelte redraws once per
    // task, so answers that arrive within a few milliseconds of each other are handed over together.
    const ANSWER = /^\{"msg":\s*"process_completed"/;
    const held = [];
    let handing = false;

    function handOver() {
        handing = false;
        for (const [handler, socket, event] of held.splice(0)) {
            try {
                handler.call(socket, event);
            } catch (error) {
                console.error('[Tab Presets]', error);
            }
        }
        net.last = performance.now();
    }

    function began(kind) {
        const call = {at: performance.now(), kind: kind};
        net.active.add(call);
        net.started++;
        return call;
    }

    function ended(call) {
        if (net.active.delete(call)) net.last = performance.now();
    }

    (function watchGradioCalls() {
        const nativeFetch = window.fetch;
        if (typeof nativeFetch === 'function') {
            window.fetch = function(input) {
                const promise = nativeFetch.apply(this, arguments);
                try {
                    const url = typeof input === 'string' ? input : String((input && input.url) || input);
                    if (url.indexOf('/run/') !== -1) {
                        const call = began('fetch');
                        promise.then(function() {
                            ended(call);
                        }, function() {
                            ended(call);
                        });
                    }
                } catch (e) {
                    // counting must never break a request
                }
                return promise;
            };
        }

        const NativeWebSocket = window.WebSocket;
        if (typeof NativeWebSocket === 'function') {
            // a subclass, so WebSocket.OPEN and the other constants Gradio's client reads stay in place
            window.WebSocket = class extends NativeWebSocket {
                constructor(...args) {
                    super(...args);
                    try {
                        if (String(args[0]).indexOf('/queue/join') === -1) return;
                        this.tabPresetsQuiet = net.loading > 0;
                        const call = began('queue');
                        let completed = false;
                        this.addEventListener('message', function(event) {
                            if (typeof event.data === 'string' && event.data.indexOf('process_completed') !== -1) completed = true;
                        });
                        this.addEventListener('close', function() {
                            if (!completed) net.dropped++;
                            ended(call);
                        });
                    } catch (e) {
                        // as above
                    }
                }

                get onmessage() {
                    return super.onmessage;
                }

                set onmessage(handler) {
                    const socket = this;
                    super.onmessage = typeof handler !== 'function' ? handler : function(event) {
                        if (socket.tabPresetsQuiet) {
                            if (onlyShown(event.data)) {
                                net.skipped++;
                                return undefined;
                            }
                            if (typeof event.data === 'string' && ANSWER.test(event.data.slice(0, 40))) {
                                held.push([handler, this, event]);
                                if (!handing) {
                                    handing = true;
                                    sleep(20).then(handOver);
                                }
                                return undefined;
                            }
                        }
                        return handler.call(this, event);
                    };
                }
            };
        }
    })();

    // Resolves once every call started after `since` has been answered and nothing followed for `quiet` ms.
    // Calls that were already running (a generation in progress) are not waited for.
    async function settled(since, quiet, limit) {
        const deadline = performance.now() + (limit || 20000);
        for (;;) {
            await sleep(50);
            const now = performance.now();
            let busy = held.length > 0;   // an answer that is still waiting to be handed over
            for (const call of net.active) {
                if (call.at >= since) {
                    busy = true;
                    break;
                }
            }
            if (!busy && now - Math.max(net.last, since) >= quiet) return true;
            if (now > deadline) return false;
        }
    }

    // --- what a tab holds ---------------------------------------------------------------------------------------

    const registry = {};   // tab id -> {tab, fields, byId, tabsets, accordions, controls}
    const defaults = {};   // tab id -> snapshot taken when the page opened
    let modelFields = {};

    // Many elements of the webui share an id: a script drawn in txt2img and again in img2img, ControlNet's accordion
    // in three tabs, the checkpoint list in five places. So an id is looked up inside its own tab only, and among
    // namesakes the n-th component gets the n-th element.
    function elementFinder(host) {
        const inHost = new Map();
        for (const element of host ? host.querySelectorAll('[id]') : []) {
            if (!inHost.has(element.id)) inHost.set(element.id, []);
            inHost.get(element.id).push(element);
        }
        const met = new Map();
        return {
            first: function(id) {
                const list = inHost.get(id);
                return (list && list[0]) || document.getElementById(id);
            },
            next: function(component) {
                const id = component.props && component.props.elem_id;
                if (!id) return document.getElementById('component-' + component.id);
                const index = met.get(id) || 0;
                met.set(id, index + 1);
                const list = inHost.get(id);
                return (list && list[index]) || document.getElementById(id);
            },
        };
    }

    function isInputAccordion(component, find) {
        const id = (component.props && component.props.elem_id) || '';
        if (component.type !== 'checkbox' || !id.endsWith('-checkbox')) return false;
        const accordion = find.first(id.slice(0, -'-checkbox'.length));
        return Boolean(accordion && accordion.classList.contains('input-accordion'));
    }

    function buildRegistry() {
        const config = window.gradio_config;
        const byId = new Map();
        for (const component of config.components) byId.set(component.id, component);

        const isInput = new Set();
        const isOutput = new Set();
        const changes = new Map();   // component id -> the "change" dependencies it triggers
        const chained = new Map();   // dependency index -> dependencies that run after it (.then)
        config.dependencies.forEach(function(dependency, index) {
            for (const id of dependency.inputs || []) isInput.add(id);
            for (const id of dependency.outputs || []) isOutput.add(id);
            if (dependency.trigger === 'change') {
                for (const id of dependency.targets || []) {
                    if (!changes.has(id)) changes.set(id, []);
                    changes.get(id).push(index);
                }
            }
            if (dependency.trigger_after !== null && dependency.trigger_after !== undefined) {
                if (!chained.has(dependency.trigger_after)) chained.set(dependency.trigger_after, []);
                chained.get(dependency.trigger_after).push(index);
            }
        });

        function outputsOf(index, seen) {
            if (seen.has(index)) return [];
            seen.add(index);
            let outputs = (config.dependencies[index].outputs || []).slice();
            for (const next of chained.get(index) || []) outputs = outputs.concat(outputsOf(next, seen));
            return outputs;
        }

        function wanted(component, context, find) {
            const props = component.props || {};
            const media = MEDIA_TYPES.has(component.type);
            if (!media && !VALUE_TYPES.has(component.type)) return false;
            if (context.results) return false;
            if (props.interactive === false) return false;
            if (!isInput.has(component.id)) return false;   // nothing reads it, so it is not a setting
            const id = props.elem_id || '';
            if (SKIP_ID.test(id)) return false;
            const classes = props.elem_classes || [];
            if (SKIP_CLASS.some(function(name) {
                return classes.indexOf(name) !== -1;
            })) return false;
            if (component.type === 'textbox' && props.type === 'password') return false;
            if (props.visible === false) {
                // An accordion with a checkbox in its title keeps the value in a hidden checkbox. Any other
                // hidden field is a setting only if the page can show it, which takes an event that outputs to it.
                if (isInputAccordion(component, find)) return true;
                if (media || !isOutput.has(component.id)) return false;
                if (!props.label && !props.elem_id) return false;
            }
            return true;
        }

        function tabRegistry(tab, node) {
            const host = document.getElementById('tab_' + tab);
            const entry = {
                tab: tab, host: host, fields: [], byId: new Map(), tabsets: [], accordions: [], controls: new Map(),
            };
            const find = elementFinder(host);
            const used = new Map();

            function keyFor(component, path, suffix) {
                const props = component.props || {};
                let key;
                if (props.elem_id && !AUTO_ID.test(props.elem_id)) {
                    key = props.elem_id;
                } else {
                    key = path.concat([String(props.label || component.type)]).join(' / ') + (suffix || '');
                }
                const count = (used.get(key) || 0) + 1;
                used.set(key, count);
                return count > 1 ? key + ' #' + count : key;
            }

            function walk(node, context) {
                const component = byId.get(node.id);
                if (!component) return;
                const props = component.props || {};
                const element = find.next(component);
                let inside = context;
                let children = node.children || [];

                if (component.type === 'tabs') {
                    if (EXTRA_TABS.test(props.elem_id || '')) {
                        children = children.slice(0, 1);
                        inside = Object.assign({}, inside, {unnamed: children.length ? children[0].id : null});
                    } else {
                        entry.tabsets.push({comp: component, el: element, key: keyFor(component, context.path, ' [tabs]')});
                    }
                } else if (component.type === 'accordion') {
                    entry.accordions.push({comp: component, el: element, key: keyFor(component, context.path, ' [accordion]')});
                } else if (wanted(component, context, find)) {
                    const field = {
                        id: component.id, comp: component, el: element, type: component.type,
                        media: MEDIA_TYPES.has(component.type),
                        key: keyFor(component, context.path),
                        label: String(props.label || props.elem_id || component.type),
                        weight: 0,
                    };
                    entry.fields.push(field);
                    entry.byId.set(component.id, field);
                }

                if ((component.type === 'accordion' || component.type === 'tabitem') && props.label && context.depth > 0 &&
                        context.unnamed !== node.id) {
                    inside = Object.assign({}, inside, {path: inside.path.concat([String(props.label)])});
                }
                if (/_results$/.test(props.elem_id || '')) inside = Object.assign({}, inside, {results: true});
                inside = Object.assign({}, inside, {depth: context.depth + 1});
                for (const child of children) walk(child, inside);
            }

            walk(node, {path: [], results: false, depth: 0});

            // which fields a field re-sets when it changes; loading sets the cause before its effects
            for (const field of entry.fields) {
                const affected = new Set();
                for (const index of changes.get(field.id) || []) {
                    if (config.dependencies[index].backend_fn) field.weight++;
                    for (const output of outputsOf(index, new Set())) {
                        if (output !== field.id && entry.byId.has(output)) affected.add(output);
                    }
                }
                if (affected.size) entry.controls.set(field.id, affected);
            }
            return entry;
        }

        (function findTabs(node) {
            const component = byId.get(node.id);
            if (component && component.type === 'tabs' && component.props.elem_id === 'tabs') {
                for (const child of node.children || []) {
                    const item = byId.get(child.id);
                    const id = item && item.props && item.props.elem_id;
                    if (item && item.type === 'tabitem' && id && id.indexOf('tab_') === 0) {
                        registry[id.slice(4)] = tabRegistry(id.slice(4), child);
                    }
                }
                return;
            }
            for (const child of node.children || []) findTabs(child);
        })(config.layout);

        // The checkpoint list exists several times on the page under one id (Replacer has its own copies); the
        // real one is the one in the quick settings row at the top.
        modelFields = {};
        (function findModel(node, quick) {
            const component = byId.get(node.id);
            const id = component && component.props && component.props.elem_id;
            quick = quick || id === 'quicksettings';
            if (quick) {
                for (const name in MODEL_FIELDS) {
                    if (id === MODEL_FIELDS[name]) modelFields[name] = component;
                }
            }
            for (const child of node.children || []) findModel(child, quick);
        })(config.layout, false);
    }

    // --- reading ------------------------------------------------------------------------------------------------

    function clone(value) {
        return value === undefined || value === null ? null : JSON.parse(JSON.stringify(value));
    }

    function same(a, b) {
        if (a === undefined) a = null;
        if (b === undefined) b = null;
        if (a === b) return true;
        if (typeof a === 'number' && typeof b === 'number') return Math.abs(a - b) < 1e-9;
        if (a === null || b === null || typeof a !== 'object' || typeof b !== 'object') return false;
        return JSON.stringify(a) === JSON.stringify(b);
    }

    function current(field) {
        const value = field.comp.props.value;
        return value === undefined ? null : value;
    }

    // An image is kept as its data URL; an upload (video, audio, file) as the list of files Gradio holds for it.
    function mediaOf(field) {
        const value = current(field);
        if (value === null || value === '') return null;
        if (field.type === 'image') {
            const image = typeof value === 'string' ? value : (value && value.image);
            return typeof image === 'string' && image.indexOf('data:') === 0 ? {kind: 'image', data: image} : null;
        }
        const many = field.type === 'file' && Array.isArray(value);
        const list = (Array.isArray(value) ? value : [value]).filter(function(file) {
            return file && typeof file === 'object' && (file.name || file.data);
        }).map(function(file) {
            const inline = !file.is_file && typeof file.data === 'string';
            return {
                name: file.name || null, orig_name: file.orig_name || file.name || null,
                size: typeof file.size === 'number' ? file.size : (inline ? file.data.length : null),
                is_file: Boolean(file.is_file), data: inline ? file.data : null,
            };
        }).filter(function(file) {
            return file.is_file || (file.data && file.data.length < MAX_INLINE_UPLOAD);
        });
        return list.length ? {kind: 'files', many: many, files: list} : null;
    }

    function baseName(path) {
        return String(path || '').split(/[\\/]/).pop();
    }

    function mediaPrint(media) {
        if (!media) return '';
        if (media.kind === 'image') return 'image:' + media.data.length + ':' + media.data.slice(-64);
        return 'files:' + media.files.map(function(file) {
            return baseName(file.orig_name || file.name);
        }).join('|');
    }

    // An image the user can paint on (ControlNet input, inpaint) travels as {image, mask}, and the server refuses
    // a bare image there. The mask is an empty one of the same size, as the page makes after an upload.
    // The size is read with createImageBitmap: Image.decode() never finishes while the window is in the background.
    async function paintable(data) {
        const canvas = document.createElement('canvas');
        canvas.width = canvas.height = 1;
        try {
            const bitmap = await Promise.race([
                fetch(data).then(function(response) {
                    return response.blob();
                }).then(function(blob) {
                    return createImageBitmap(blob);
                }),
                sleep(5000).then(function() {
                    return null;
                }),
            ]);
            if (bitmap) {
                canvas.width = bitmap.width;
                canvas.height = bitmap.height;
                bitmap.close();
            }
        } catch (error) {
            // an empty mask of any size still tells the server "nothing painted"
        }
        return {image: data, mask: canvas.toDataURL('image/png')};
    }

    function canBePaintedOn(field) {
        const props = field.comp.props;
        return field.type === 'image' && props.tool === 'sketch' && (props.source === 'upload' || props.source === 'webcam');
    }

    // The tab's own reactions put bare pictures into such components too: ticking ControlNet's "Use Mask" answers
    // with an empty canvas for the mask. Until the page has drawn it, every event that reads that picture fails on
    // the server and shows a red "Error" on the page, and a load sets off many events in that moment. So after each
    // step of a load, a bare picture is given its empty mask straight away.
    async function mendPictures(entry) {
        const since = performance.now();
        let mended = 0;
        for (const field of entry.fields) {
            if (!field.media || !canBePaintedOn(field)) continue;
            const value = current(field);
            const bare = typeof value === 'string' ? value : (value && typeof value === 'object' && !value.mask ? value.image : null);
            if (typeof bare !== 'string' || bare.indexOf('data:') !== 0) continue;
            setValue(field.comp, await paintable(bare));
            mended++;
        }
        if (mended) await settled(since, QUIET);
    }

    async function mediaValue(field, media) {
        if (!media) return null;
        if (media.kind === 'image') {
            if (field.type !== 'image') return null;
            return canBePaintedOn(field) ? paintable(media.data) : media.data;
        }
        if (media.kind !== 'files' || field.type === 'image') return null;
        if (field.type === 'video') return [media.files[0], null];
        if (field.type === 'file' && media.many) return media.files;
        return media.files[0];
    }

    function navButtons(tabset) {
        const nav = tabset.el && tabset.el.querySelector(':scope > .tab-nav');
        return nav ? Array.from(nav.querySelectorAll(':scope > button')) : [];
    }

    function labelWrap(accordion) {
        return accordion.el ? accordion.el.querySelector(':scope > .label-wrap') : null;
    }

    function captureLayout(entry) {
        const layout = {tabs: {}, accordions: {}};
        for (const tabset of entry.tabsets) {
            const buttons = navButtons(tabset);
            const index = buttons.findIndex(function(button) {
                return button.classList.contains('selected');
            });
            if (index >= 0) layout.tabs[tabset.key] = [index, buttons.length];
        }
        for (const accordion of entry.accordions) {
            const wrap = labelWrap(accordion);
            if (wrap) layout.accordions[accordion.key] = wrap.classList.contains('open');
        }
        return layout;
    }

    function captureModel() {
        const model = {};
        for (const name in modelFields) model[name] = clone(modelFields[name].props.value);
        return model;
    }

    function capture(tab, withMedia) {
        const entry = registry[tab];
        const snapshot = {
            version: FORMAT, values: {}, layout: captureLayout(entry), model: captureModel(), media: {},
            media_included: Boolean(withMedia),
        };
        for (const field of entry.fields) {
            if (!field.media) {
                snapshot.values[field.key] = clone(current(field));
            } else if (withMedia) {
                const media = mediaOf(field);
                if (media) snapshot.media[field.key] = media;
            }
        }
        return snapshot;
    }

    // --- writing ------------------------------------------------------------------------------------------------

    // The same two steps Gradio takes for a value that arrives from the server: the page's own record, then the
    // component. `value_is_output` keeps components from reporting it as something the user typed.
    function setValue(component, value) {
        const copy = clone(value);
        component.props.value = copy;
        component.props.value_is_output = true;
        if (component.instance && typeof component.instance.$set === 'function') {
            component.instance.$set({value: copy, value_is_output: true});
        }
    }

    function offered(field, value) {
        const choices = field.comp.props.choices;
        if (!Array.isArray(choices) || !choices.length) return true;
        if (field.type !== 'dropdown' && field.type !== 'radio') return true;
        if (field.comp.props.allow_custom_value) return true;
        const values = choices.map(function(choice) {
            return Array.isArray(choice) ? choice[1] : choice;
        });
        return (Array.isArray(value) ? value : [value]).every(function(one) {
            return one === null || one === '' || values.indexOf(one) !== -1;
        });
    }

    // How late each field has to be set: a field that others re-set when it changes goes first.
    function depths(entry, seeds) {
        const depth = new Map();
        for (const field of seeds) depth.set(field.id, 0);
        for (let pass = 0; pass < 4; pass++) {
            let moved = false;
            for (const [id, level] of Array.from(depth)) {
                if (level >= 4) continue;
                for (const affected of entry.controls.get(id) || []) {
                    if ((depth.has(affected) ? depth.get(affected) : -1) < level + 1) {
                        depth.set(affected, level + 1);
                        moved = true;
                    }
                }
            }
            if (!moved) break;
        }
        return depth;
    }

    // What the tab should hold after a load: the preset's value for every field it has, and the starting value for a
    // field it does not know (a ControlNet unit added since it was saved, a newly installed extension). Without the
    // second part whatever was in those fields before would stay and mix with the preset.
    function targets(entry, values) {
        const start = (defaults[entry.tab] && defaults[entry.tab].values) || {};
        const want = new Map();
        for (const field of entry.fields) {
            if (field.media) continue;
            if (Object.prototype.hasOwnProperty.call(values, field.key)) want.set(field, values[field.key]);
            else if (Object.prototype.hasOwnProperty.call(start, field.key)) want.set(field, start[field.key]);
        }
        return want;
    }

    async function applyValues(entry, values, report, progress) {
        const want = targets(entry, values);
        const known = new Set(entry.fields.map(function(field) {
            return field.key;
        }));
        report.unknown = Object.keys(values).filter(function(key) {
            return !known.has(key);
        }).length;

        const tries = new Map();
        const touched = report.touched;
        const before = touched.size;
        let expected = 0;

        for (let round = 0; round < MAX_ROUNDS; round++) {
            const differing = [];
            for (const [field, value] of want) {
                if (!same(current(field), value)) differing.push(field);
            }
            if (!differing.length) break;
            expected = Math.max(expected, touched.size - before + differing.length);

            for (const field of differing) {
                const count = (tries.get(field) || 0) + 1;
                tries.set(field, count);
                if (count > MAX_TRIES) {
                    // the tab keeps putting its own value there (a model that is gone, a field tied to another)
                    (offered(field, want.get(field)) ? report.failed : report.missing).push(field);
                    want.delete(field);
                }
            }

            const depth = depths(entry, differing.filter(function(field) {
                return want.has(field);
            }));
            const deepest = Math.max(0, ...Array.from(depth.values()));
            for (let level = 0; level <= deepest; level++) {
                const layer = [];
                for (const [field, value] of want) {
                    if (depth.get(field.id) === level && !same(current(field), value)) layer.push(field);
                }
                // fields nobody listens to cost nothing and go together; the others in steps the page can absorb
                layer.sort(function(a, b) {
                    return a.weight - b.weight;
                });
                let position = 0;
                while (position < layer.length) {
                    const since = performance.now();
                    let events = 0;
                    while (position < layer.length && (events === 0 || events + layer[position].weight <= EVENT_BUDGET)) {
                        const field = layer[position++];
                        if (same(current(field), want.get(field))) continue;
                        setValue(field.comp, want.get(field));
                        touched.add(field);
                        events += field.weight;
                    }
                    if (progress) progress(touched.size - before, Math.max(expected, touched.size - before));
                    await settled(since, events ? QUIET : 80);
                    if (events) await mendPictures(entry);
                }
            }
        }

        for (const [field, value] of want) {
            if (!same(current(field), value)) (offered(field, value) ? report.failed : report.missing).push(field);
        }
    }

    // Strokes painted on a picture are kept by the page on a canvas of their own.
    function hasPaint(field) {
        const canvas = field.el && field.el.querySelector('canvas[key="mask"]');
        if (!canvas || !canvas.width || !canvas.height) return false;
        try {
            const pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
            for (let alpha = 3; alpha < pixels.length; alpha += 4) {
                if (pixels[alpha] !== 0) return true;
            }
        } catch (error) {
            // a canvas that cannot be read is treated as clean
        }
        return false;
    }

    async function applyMedia(entry, snapshot, report) {
        if (!snapshot.media_included) return;
        const jobs = [];
        for (const field of entry.fields) {
            if (!field.media) continue;
            const target = snapshot.media[field.key] || null;
            const holds = current(field) !== null && current(field) !== '';
            const paintable = canBePaintedOn(field);
            const unchanged = mediaPrint(mediaOf(field)) === mediaPrint(target) && Boolean(mediaOf(field)) === holds;
            if (unchanged && !(paintable && holds && hasPaint(field))) continue;
            jobs.push({field: field, target: target, empty: paintable && holds && target !== null});
        }
        if (!jobs.length) return;

        // A picture that can be painted on is taken out before another goes in. The component keeps the strokes and
        // the canvas size of the old picture for as long as it holds one, and ControlNet would read those strokes
        // as a mask for the new picture.
        let since = performance.now();
        const emptied = jobs.filter(function(job) {
            return job.empty;
        });
        for (const job of emptied) setValue(job.field.comp, null);
        if (emptied.length) await settled(since, QUIET);

        since = performance.now();
        for (const job of jobs) {
            try {
                setValue(job.field.comp, await mediaValue(job.field, job.target));
                report.touched.add(job.field);
            } catch (error) {
                report.failed.push(job.field);   // a picture the browser cannot read
            }
        }
        await settled(since, QUIET);
    }

    async function applyLayout(entry, layout) {
        if (!layout) return;
        const since = performance.now();
        let clicks = 0;
        for (const tabset of entry.tabsets) {
            const saved = (layout.tabs || {})[tabset.key];
            if (!saved) continue;
            const buttons = navButtons(tabset);
            if (buttons.length !== saved[1] || !buttons[saved[0]] || buttons[saved[0]].classList.contains('selected')) continue;
            buttons[saved[0]].click();
            clicks++;
        }
        for (const accordion of entry.accordions) {
            const saved = (layout.accordions || {})[accordion.key];
            const wrap = labelWrap(accordion);
            if (typeof saved !== 'boolean' || !wrap || wrap.classList.contains('open') === saved) continue;
            wrap.click();
            clicks++;
        }
        if (clicks) await settled(since, QUIET);
    }

    // Scripts on the page follow checkboxes and radio buttons through the browser's own "change" event, which only
    // a click sends: ControlNet marks a unit as active, counts the active units and writes "[Canny]" in the unit's
    // title that way. A value set by a load is silent, so those marks would keep showing the previous state. Gradio
    // itself ignores a "change" that changes nothing, so it is sent for every box and button the load set.
    function announce(fields) {
        for (const field of fields) {
            if (!field.el || (field.type !== 'checkbox' && field.type !== 'radio')) continue;
            const input = field.el.querySelector(field.type === 'radio' ? 'input[type="radio"]:checked' : 'input[type="checkbox"]');
            if (input) input.dispatchEvent(new Event('change', {bubbles: true}));
        }
    }

    // ControlNet shows a small copy of a unit's picture in its folded title, and draws it only at the moment the
    // unit is folded by hand. After a load the title would show the picture that was there before, or none.
    function refreshThumbnails(host) {
        if (!host || !(window.opts && window.opts.controlnet_input_thumbnail)) return;
        for (const unit of host.querySelectorAll('#controlnet .input-accordion')) {
            const wrap = unit.querySelector(':scope > .label-wrap');
            const title = wrap && wrap.querySelector(':scope > span');
            if (!title) continue;
            for (const old of title.querySelectorAll('.cnet-thumbnail')) old.remove();
            if (wrap.classList.contains('open')) continue;
            const picture = unit.querySelector('.cnet-input-image-group .cnet-image img');
            const mask = unit.querySelector('.cnet-mask-image-group .cnet-mask-image img');
            for (const shown of [picture, mask]) {
                if (!shown || !shown.src) continue;
                const copy = document.createElement('img');
                copy.src = shown.src;
                copy.className = 'cnet-thumbnail';
                title.appendChild(copy);
            }
        }
    }

    function applyModel(model, report) {
        for (const name in modelFields) {
            if (!model || !Object.prototype.hasOwnProperty.call(model, name) || model[name] === null) continue;
            const component = modelFields[name];
            if (same(component.props.value, model[name])) continue;
            const field = {comp: component, type: component.type};
            if (!offered(field, model[name])) {
                report.notes.push(t('modelMissing', {name: model[name]}));
                continue;
            }
            // model-defaults.js leaves the preset's own sampler/steps/CFG alone for a checkpoint set here
            if (name === 'checkpoint' && window.tabPresets) window.tabPresets.modelSetAt = Date.now();
            setValue(component, model[name]);
            if (name === 'checkpoint') report.notes.push(t('modelLoading', {name: model[name]}));
        }
    }

    async function apply(tab, snapshot, options) {
        const entry = registry[tab];
        const values = snapshot.values || {};
        const report = {changed: 0, failed: [], missing: [], unknown: 0, notes: [], touched: new Set()};
        const pictures = [];
        net.loading++;
        try {
            // Settings, then pictures and files, then which sub-tabs and accordions are open. Each of the three can
            // undo part of an earlier one (ticking "Use Mask" empties the mask picture, a picture resets a preview,
            // opening an accordion with a checkbox ticks it), so settings and pictures are gone over again after the
            // others. A pass that finds nothing to do costs nothing.
            const settings = async function(progress) {
                const pass = {failed: [], missing: [], unknown: 0, touched: report.touched};
                await applyValues(entry, values, pass, progress);
                return pass;
            };
            const media = async function() {
                if (!options.media) return;
                const pass = {failed: [], touched: report.touched};
                await applyMedia(entry, snapshot, pass);
                pictures.push(pass.failed);
            };

            // how long each part took and how many server calls it set off, for whoever looks into a slow load
            const parts = report.parts = [];
            const timed = async function(name, work) {
                const from = performance.now();
                const calls = net.started;
                const result = await work();
                parts.push(name + ' ' + Math.round(performance.now() - from) + 'ms ' + (net.started - calls));
                return result;
            };

            report.unknown = (await timed('settings', function() {
                return settings(options.progress);
            })).unknown;
            await timed('pictures', media);
            await timed('layout', function() {
                return applyLayout(entry, snapshot.layout);
            });
            await timed('settings again', settings);
            await timed('pictures again', media);

            const last = await timed('titles', async function() {
                const since = performance.now();
                announce(report.touched);
                await settled(since, 80);
                const pass = await settings();
                refreshThumbnails(entry.host);
                return pass;
            });

            report.failed = last.failed.concat(pictures.length ? pictures[pictures.length - 1] : []);
            report.missing = last.missing;
            report.changed = report.touched.size;
        } finally {
            net.loading--;
        }
        // the model is set last and not waited for: a checkpoint takes its time and shows its own progress
        if (options.model && snapshot.version >= FORMAT) applyModel(snapshot.model, report);
        for (const name of [tab + '_prompt', tab + '_neg_prompt']) {
            if (typeof window.recalculatePromptTokens === 'function') window.recalculatePromptTokens(name);
        }
        return report;
    }

    // --- the server side: files on disk -------------------------------------------------------------------------

    async function call(path, body) {
        const response = await fetch('./tab-presets/' + path, body === undefined ? {} : {
            method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body),
        });
        if (!response.ok) {
            const error = new Error('HTTP ' + response.status);
            error.status = response.status;
            throw error;
        }
        return response.json();
    }

    // --- the bar ------------------------------------------------------------------------------------------------

    const bars = {};

    function make(tag, className, text) {
        const element = document.createElement(tag);
        if (className) element.className = className;
        if (text !== undefined) element.textContent = text;
        return element;
    }

    function stored(key, fallback) {
        try {
            const value = localStorage.getItem('tab-presets:' + key);
            return value === null ? fallback : value;
        } catch (e) {
            return fallback;
        }
    }

    function store(key, value) {
        try {
            localStorage.setItem('tab-presets:' + key, value);
        } catch (e) {
            // the choice is then kept for this page only
        }
    }

    function option(name) {
        return stored('with-' + name, '1') === '1';
    }

    function ago(seconds) {
        const passed = Date.now() / 1000 - seconds;
        if (passed < 90) return t('now');
        if (passed < 3600) return t('minutes', {n: Math.round(passed / 60)});
        if (passed < 20 * 3600) return t('hours', {n: Math.round(passed / 3600)});
        if (passed < 44 * 3600) return t('yesterday');
        return new Date(seconds * 1000).toLocaleDateString(hebrew() ? 'he-IL' : undefined);
    }

    function names(fields) {
        const list = fields.slice(0, 4).map(function(field) {
            return field.label;
        });
        if (fields.length > 4) list.push('+' + (fields.length - 4));
        return list.join(', ');
    }

    function createBar(tab, host) {
        const bar = {tab: tab, presets: [], recent: [], last: null, current: null, undo: null, busy: false, confirm: null, menu: null, said: null};
        const root = make('div', 'tab-presets-bar hebrew-ui-skip');
        root.id = 'tab-presets-' + tab.replace(/[^\w-]/g, '_');

        const title = make('span', 'tab-presets-title');
        const select = make('select', 'tab-presets-select');
        const load = make('button', 'tab-presets-button tab-presets-primary');
        const save = make('button', 'tab-presets-button');
        const saveAs = make('button', 'tab-presets-button');
        const recent = make('button', 'tab-presets-button');
        const reset = make('button', 'tab-presets-button');
        const more = make('button', 'tab-presets-button tab-presets-more');
        const naming = make('span', 'tab-presets-naming');
        const nameInput = make('input', 'tab-presets-name');
        const nameOk = make('button', 'tab-presets-button tab-presets-primary');
        const nameCancel = make('button', 'tab-presets-button');
        const status = make('span', 'tab-presets-status');
        nameInput.type = 'text';
        nameInput.maxLength = 80;
        naming.hidden = true;
        naming.append(nameInput, nameOk, nameCancel);
        for (const button of [load, save, saveAs, recent, reset, more, nameOk, nameCancel]) button.type = 'button';
        root.append(title, select, load, save, saveAs, naming, recent, reset, more, status);

        function chosen() {
            return select.value || '';
        }

        function say(key, values, kind, withUndo) {
            bar.said = key ? {key: key, values: values, kind: kind, withUndo: withUndo} : null;
            status.textContent = '';
            status.className = 'tab-presets-status' + (kind ? ' tab-presets-' + kind : '');
            if (!key) return;
            status.append(make('span', '', t(key, values)));
            if (withUndo && bar.undo) {
                const undo = make('button', 'tab-presets-link', t('undo'));
                undo.type = 'button';
                undo.addEventListener('click', undoLast);
                status.append(undo);
            }
        }

        // a big preset takes a while on this page (every server reaction costs about half a second), so it counts
        function progressOf(key, values) {
            return function(done, total) {
                say(key, values);
                status.append(make('span', 'tab-presets-count', t('progress', {done: done, total: total})));
            };
        }

        function sayReport(key, sameKey, values, report) {
            say(report.changed ? key : sameKey, Object.assign({n: report.changed}, values), 'ok', true);
            const notes = report.notes.slice();
            if (report.missing.length) notes.push(t('missing', {list: names(report.missing)}));
            if (report.failed.length) notes.push(t('failed', {list: names(report.failed)}));
            if (report.unknown) notes.push(t('unknown', {n: report.unknown}));
            for (const note of notes) status.append(make('span', 'tab-presets-note', note));
            if (report.missing.length || report.failed.length) status.classList.add('tab-presets-warn');
        }

        function setBusy(value) {
            bar.busy = value;
            root.classList.toggle('tab-presets-busy', value);
            for (const button of [load, save, saveAs, recent, reset, more, nameOk]) button.disabled = value;
            select.disabled = value;
        }

        function fillSelect(keep) {
            const wanted = keep === undefined ? chosen() : keep;
            select.textContent = '';
            const first = make('option', '', t(bar.presets.length ? 'choose' : 'empty'));
            first.value = '';
            select.append(first);
            for (const preset of bar.presets) {
                const item = make('option', '', preset.name);
                item.value = preset.name;
                select.append(item);
            }
            select.value = bar.presets.some(function(preset) {
                return preset.name === wanted;
            }) ? wanted : '';
            describeChoice();
        }

        function describeChoice() {
            const preset = bar.presets.find(function(one) {
                return one.name === chosen();
            });
            select.title = preset ? [
                t('settings', {n: preset.count}) + (preset.media ? t('mediaCount', {n: preset.media}) : ''),
                ago(preset.saved_at),
                preset.model && preset.model.checkpoint ? String(preset.model.checkpoint) : '',
            ].filter(Boolean).join(' · ') : '';
        }

        function paint() {
            root.dir = hebrew() ? 'rtl' : 'ltr';
            title.textContent = '💾 ' + t('title');
            load.textContent = '📂 ' + t('load');
            load.title = t('loadTip');
            save.textContent = '💾 ' + t('save');
            save.title = t('saveTip');
            saveAs.textContent = t('saveAs');
            saveAs.title = t('saveAsTip');
            recent.textContent = '🕘 ' + t('recent') + ' ▾';
            recent.title = t('recentTip');
            reset.textContent = '↺ ' + t('reset');
            reset.title = t('resetTip');
            more.textContent = '⋯';
            more.title = t('more');
            nameInput.placeholder = t('name');
            nameOk.textContent = t('ok');
            nameCancel.textContent = t('cancel');
            fillSelect();
            closeMenu();
            if (bar.said) say(bar.said.key, bar.said.values, bar.said.kind, bar.said.withUndo);
        }

        async function refresh(keep, attempt) {
            try {
                const listing = await call('list?tab=' + encodeURIComponent(tab));
                bar.presets = listing.presets || [];
                bar.recent = listing.recent || [];
                bar.last = listing.last || null;
            } catch (error) {
                // right after a start the page can be up a moment before the server has added these routes
                if ((attempt || 0) < 5 && error.status === 404) {
                    await sleep(2000);
                    return refresh(keep, (attempt || 0) + 1);
                }
                say('error', {message: error.message}, 'warn');
            }
            fillSelect(keep);
        }

        function closeMenu() {
            if (bar.menu) bar.menu.remove();
            bar.menu = null;
        }

        function openMenu(anchor, build) {
            const reopening = bar.menu && bar.menu.anchor === anchor;
            closeMenu();
            if (reopening) return;
            const menu = make('div', 'tab-presets-menu');
            menu.anchor = anchor;
            build(menu);
            root.append(menu);
            // under its button, opening away from the near edge, and never past either end of the bar
            const frame = root.getBoundingClientRect();
            const button = anchor.getBoundingClientRect();
            const width = menu.getBoundingClientRect().width;
            const start = hebrew() ? button.right - frame.left - width : button.left - frame.left;
            menu.style.left = Math.max(0, Math.min(start, frame.width - width)) + 'px';
            menu.style.top = (anchor.offsetTop + anchor.offsetHeight + 4) + 'px';
            bar.menu = menu;
        }

        function menuItem(menu, text, hint, action) {
            const item = make('button', 'tab-presets-item');
            item.type = 'button';
            item.append(make('span', '', text));
            if (hint) item.append(make('span', 'tab-presets-hint', hint));
            item.addEventListener('click', action);
            menu.append(item);
            return item;
        }

        function menuCheck(menu, text, name) {
            const label = make('label', 'tab-presets-item tab-presets-check');
            const box = make('input');
            box.type = 'checkbox';
            box.checked = option(name);
            box.addEventListener('change', function() {
                store('with-' + name, box.checked ? '1' : '0');
            });
            label.append(box, make('span', '', text));
            menu.append(label);
        }

        async function run(work) {
            if (bar.busy) return;
            closeMenu();
            setBusy(true);
            try {
                await work();
            } catch (error) {
                console.error('[Tab Presets]', error);
                say('error', {message: error.message || String(error)}, 'warn');
            } finally {
                setBusy(false);
            }
        }

        function loadPreset(name) {
            if (!name) return say('needChoice', null, 'warn');
            return run(async function() {
                say('working', {name: name});
                const snapshot = await call('load?tab=' + encodeURIComponent(tab) + '&name=' + encodeURIComponent(name));
                bar.undo = capture(tab, option('media'));
                const report = await apply(tab, snapshot, {
                    media: option('media'), model: option('model'), progress: progressOf('working', {name: name}),
                });
                store('last:' + tab, name);
                bar.current = name;
                await refresh(name);
                sayReport('loaded', 'loadedSame', {name: name}, report);
            });
        }

        // what the tab held when Generate was last pressed (kept by rememberGeneration below), also after a restart
        function loadLast() {
            return run(async function() {
                const name = t('last');
                say('working', {name: name});
                const snapshot = await call('load?tab=' + encodeURIComponent(tab) + '&last=true');
                bar.undo = capture(tab, option('media'));
                const report = await apply(tab, snapshot, {
                    media: option('media'), model: option('model'), progress: progressOf('working', {name: name}),
                });
                sayReport('loaded', 'loadedSame', {name: name}, report);
            });
        }

        function resetTab() {
            return run(async function() {
                say('resetting');
                bar.undo = capture(tab, true);   // Reset clears pictures and files as well
                const report = await apply(tab, defaults[tab], {media: true, model: false, progress: progressOf('resetting')});
                sayReport('wasReset', 'resetSame', {}, report);
            });
        }

        function undoLast() {
            return run(async function() {
                if (!bar.undo) return;
                say('undoing');
                const before = bar.undo;
                bar.undo = null;
                await apply(tab, before, {media: before.media_included, model: false});
                say('undone', null, 'ok');
            });
        }

        function savePreset(name, overwrite) {
            name = String(name || '').trim();
            if (!name) return say('needName', null, 'warn');
            return run(async function() {
                say('saving');
                const preset = capture(tab, option('media'));
                try {
                    const result = await call('save', {tab: tab, name: name, preset: preset, overwrite: overwrite});
                    bar.confirm = null;
                    bar.current = result.name;
                    naming.hidden = true;
                    store('last:' + tab, result.name);
                    await refresh(result.name);
                    say('saved', {name: result.name, n: result.count, media: result.media ? t('mediaCount', {n: result.media}) : ''}, 'ok');
                } catch (error) {
                    if (error.status !== 409) throw error;
                    bar.confirm = name;   // pressing Save again with the same name replaces it
                    say('exists', {name: name}, 'warn');
                }
            });
        }

        function askName() {
            closeMenu();
            naming.hidden = false;
            nameInput.value = chosen();
            bar.confirm = null;
            nameInput.focus();
            nameInput.select();
        }

        function confirmName() {
            const name = nameInput.value.trim();
            savePreset(name, bar.confirm !== null && bar.confirm === name);
        }

        load.addEventListener('click', function() {
            loadPreset(chosen());
        });
        save.addEventListener('click', function() {
            const name = chosen();
            if (!name) return askName();
            // the preset in use is updated at once; any other one is replaced only on a second press
            savePreset(name, name === bar.current || name === bar.confirm);
        });
        saveAs.addEventListener('click', askName);
        nameOk.addEventListener('click', confirmName);
        nameCancel.addEventListener('click', function() {
            naming.hidden = true;
            bar.confirm = null;
        });
        nameInput.addEventListener('keydown', function(event) {
            event.stopPropagation();   // the webui has page-wide shortcuts (Ctrl+Enter generates)
            if (event.key === 'Enter') confirmName();
            if (event.key === 'Escape') nameCancel.click();
        });
        select.addEventListener('change', function() {
            describeChoice();
            store('last:' + tab, chosen());
        });
        reset.addEventListener('click', resetTab);
        recent.addEventListener('click', function() {
            openMenu(recent, function(menu) {
                if (bar.last) {
                    menuItem(menu, '⟲ ' + t('last'), ago(bar.last.saved_at), loadLast).title = t('lastTip');
                    if (bar.recent.length) menu.append(make('span', 'tab-presets-line'));
                } else if (!bar.recent.length) {
                    menu.append(make('span', 'tab-presets-item tab-presets-hint', t('noRecent')));
                }
                for (const entry of bar.recent.slice(0, 10)) {
                    menuItem(menu, entry.name, ago(entry.at), function() {
                        select.value = entry.name;
                        loadPreset(entry.name);
                    });
                }
            });
        });
        more.addEventListener('click', function() {
            openMenu(more, function(menu) {
                menuCheck(menu, t('withModel'), 'model');
                menuCheck(menu, t('withMedia'), 'media');
                menuItem(menu, '📁 ' + t('folder'), '', function() {
                    closeMenu();
                    call('open-folder', {tab: tab}).catch(function(error) {
                        say('error', {message: error.message}, 'warn');
                    });
                });
                const name = chosen();
                if (name) {
                    let sure = false;
                    const item = menuItem(menu, '🗑 ' + t('remove', {name: name}), '', function() {
                        if (!sure) {
                            sure = true;
                            item.firstChild.textContent = '🗑 ' + t('removeSure', {name: name});
                            item.classList.add('tab-presets-danger');
                            return;
                        }
                        run(async function() {
                            await call('delete', {tab: tab, name: name});
                            await refresh('');
                            say('removed', {name: name}, 'ok');
                        });
                    });
                }
            });
        });
        document.addEventListener('click', function(event) {
            if (bar.menu && !bar.menu.contains(event.target) && event.target !== bar.menu.anchor) closeMenu();
        });

        bar.root = root;
        bar.paint = paint;
        bar.refresh = refresh;
        bar.load = loadPreset;
        bar.save = savePreset;
        bar.reset = resetTab;
        bar.undoLast = undoLast;
        host.insertBefore(root, host.firstChild);
        paint();
        refresh(stored('last:' + tab, ''));
        return bar;
    }

    // Pressing Generate keeps a copy of the tab as it is at that moment ("Last generation" in the Recent list), with
    // its pictures and files. Reading the tab costs nothing here, so it happens on every press. A picture is
    // megabytes of text though, so one the server already holds from the previous press is only named, not sent.
    const sentBefore = {};   // tab -> Map(field key -> what was sent for it)

    function sentPrint(media) {
        if (media.kind === 'image') return mediaPrint(media);
        return 'files:' + media.files.map(function(file) {
            return [baseName(file.orig_name || file.name), file.size, file.name, file.data ? file.data.length : ''].join(':');
        }).join('|');
    }

    function rememberGeneration(event) {
        const button = event.target && event.target.closest ? event.target.closest('button[id$="_generate"]') : null;
        const host = button && button.closest('#tabs > .tabitem[id^="tab_"]');
        const tab = host ? host.id.slice(4) : null;
        if (!tab || !bars[tab]) return;
        try {
            const preset = capture(tab, option('media'));
            const known = sentBefore[tab] || new Map();
            const prints = new Map();
            for (const key in preset.media) {
                prints.set(key, sentPrint(preset.media[key]));
                if (known.get(key) === prints.get(key)) preset.media[key] = {kind: 'kept'};
            }
            bars[tab].last = {saved_at: Date.now() / 1000};
            // sent a moment later, so that Generate itself starts first
            sleep(0).then(function() {
                return call('save', {tab: tab, preset: preset, last: true});
            }).then(function(result) {
                for (const key of result.lost || []) prints.delete(key);
                sentBefore[tab] = prints;
            }).catch(function(error) {
                sentBefore[tab] = new Map();
                console.warn('[Tab Presets] the last generation was not kept:', error);
            });
        } catch (error) {
            console.warn('[Tab Presets]', error);   // Generate itself must never be held up by this
        }
    }

    // --- start --------------------------------------------------------------------------------------------------

    function hiddenTabs() {
        const setting = (window.opts && window.opts.tab_presets_hidden_tabs) || '';
        return new Set(String(setting).split(',').map(function(name) {
            return name.trim();
        }).filter(Boolean));
    }

    async function start() {
        if (!window.gradio_config || !window.gradio_config.components) return;
        buildRegistry();
        // The page fires a few events of its own right after it opens; the starting values are read once they have
        // landed. Some of those events never end (extensions that refresh themselves on a timer), hence the limit.
        await settled(0, 500, 3000);
        const hidden = hiddenTabs();
        for (const tab in registry) {
            defaults[tab] = capture(tab, false);
            defaults[tab].media_included = true;   // Reset also clears pictures and files
            const host = document.getElementById('tab_' + tab);
            if (!host || NO_BAR.has(tab) || hidden.has(tab) || registry[tab].fields.length < MIN_FIELDS) continue;
            bars[tab] = createBar(tab, host);
        }
        document.addEventListener('click', rememberGeneration, true);
        new MutationObserver(function() {
            for (const tab in bars) bars[tab].paint();
        }).observe(document.documentElement, {attributes: true, attributeFilter: ['class']});
    }

    window.tabPresets = {registry: registry, defaults: defaults, bars: bars, net: net, capture: capture, apply: apply, settled: settled};

    onUiLoaded(start);
})();

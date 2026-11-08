/* global gradioApp, onUiLoaded, onAfterUiUpdate */
(function() {
    'use strict';
    if (window.ForgeGalleryGenerate) return;
    const states = new Map();
    const idFor = tab => tab + '_gallery_generate_shortcut';

    function nativeBusy(state) {
        const app = gradioApp();
        // These are Forge's own lifecycle signals, also used for prompt submission,
        // queued jobs, restored progress, hires upscale and interrupt completion.
        return ['interrupt', 'skip', 'interrupting'].some(action => {
            const node = app.getElementById(state.tab + '_' + action);
            return node && getComputedStyle(node).display !== 'none';
        }) || !!state.progressParent.querySelector(':scope > .progressDiv');
    }
    function sync(state) {
        const running = nativeBusy(state);
        if (running) { state.pending = false; clearTimeout(state.timer); }
        const busy = running || state.pending;
        const disabled = busy || state.source.disabled || state.source.getAttribute('aria-disabled') === 'true';
        if (state.proxy.disabled !== disabled) state.proxy.disabled = disabled;
        if (state.proxy.getAttribute('aria-busy') !== String(busy)) state.proxy.setAttribute('aria-busy', String(busy));
        // Keep the requested English tooltip, even if a localization script visits it.
        if (state.proxy.title !== 'Generate') state.proxy.title = 'Generate';
    }
    function started(state) {
        state.pending = true;
        clearTimeout(state.timer);
        sync(state);
        // A prevented native click or client-side validation failure must not leave
        // the shortcut locked forever when no native task was actually started.
        state.timer = setTimeout(() => { state.pending = false; sync(state); }, 2000);
    }
    function dispose(state) {
        clearTimeout(state.timer);
        state.abort.abort();
        state.observers.forEach(observer => observer.disconnect());
        state.proxy.remove();
    }
    function refresh() {
        const app = gradioApp();
        for (const tab of ['txt2img', 'img2img']) {
            const source = app.getElementById(tab + '_generate');
            const row = app.getElementById('image_buttons_' + tab);
            const gallery = app.getElementById(tab + '_gallery_container');
            const template = row?.querySelector('button:not(.fgg-button)');
            const parent = template?.parentElement;
            const progressParent = gallery?.parentElement;
            const previous = states.get(tab);
            if (!source || !parent || !progressParent) {
                if (previous) { dispose(previous); states.delete(tab); }
                continue; // Unsupported/missing output panel leaves native controls untouched.
            }
            if (previous && previous.source === source && previous.parent === parent && previous.progressParent === progressParent && previous.proxy.isConnected) {
                sync(previous);
                continue;
            }
            if (previous) dispose(previous);
            const proxy = document.createElement('button');
            proxy.id = idFor(tab);
            proxy.type = 'button';
            proxy.className = template.className + ' fgg-button';
            proxy.textContent = 'G';
            proxy.title = 'Generate';
            proxy.lang = 'en';
            proxy.translate = false;
            proxy.setAttribute('aria-label', 'Generate');
            const state = {tab, source, parent, progressParent, proxy, pending: false, timer: null, abort: new AbortController(), observers: []};
            states.set(tab, state);
            // Append in the DOM, display first with CSS order: existing tool-button
            // positions and extensions that clone the first original button survive.
            parent.append(proxy);
            source.addEventListener('click', () => { if (!source.disabled) started(state); }, {capture: true, signal: state.abort.signal});
            proxy.addEventListener('click', event => {
                event.stopPropagation();
                sync(state);
                if (proxy.disabled) return;
                // Click the current native element, preserving its Gradio callbacks,
                // inputs, scripts and task/progress machinery. Never call a backend directly.
                source.click();
                sync(state);
            }, {signal: state.abort.signal});
            const observe = (node, options) => {
                const observer = new MutationObserver(() => {
                    if (!source.isConnected || !proxy.isConnected) refresh();
                    else sync(state);
                });
                observer.observe(node, options);
                state.observers.push(observer);
            };
            observe(source.parentElement, {subtree: true, childList: true, attributes: true, attributeFilter: ['style', 'class', 'disabled', 'aria-disabled', 'hidden']});
            observe(progressParent, {childList: true});
            observe(proxy, {attributes: true, attributeFilter: ['title']});
            sync(state);
        }
    }
    window.ForgeGalleryGenerate = {refresh};
    onUiLoaded(refresh);
    onAfterUiUpdate(refresh);
})();

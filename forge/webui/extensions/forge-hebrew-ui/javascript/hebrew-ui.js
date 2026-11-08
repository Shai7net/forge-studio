// Forge Hebrew UI: Optimized, non-blocking one-click language switcher.
// Completely hardened against MutationObserver infinite loops and Gradio model dropdown desynchronization.

(function() {
    const STORAGE_KEY = 'forge-hebrew-ui';

    // Elements that must NEVER have their text or attributes modified
    const SKIP_ALWAYS = 'script, style, pre, code, svg, .extra-network-cards, .card, .gallery, .thumbnails, ' +
        '.json-holder, .cm-editor, #licenses, #hebrew-ui-toggle, .hebrew-ui-block, .hebrew-ui-hidden, ' +
        '.hebrew-ui-skip, #forge-desktop-bar, #forge-desktop-floating-toggle, #forge-desktop-toast, ' +
        '.progress-bar, .progressbar, .token-counter, #live_preview, #progress_bar, .performance';

    // Elements whose internal values must NEVER be translated (inputs, prompts, dropdown options)
    // ONLY outer labels of dropdowns should be translated!
    // Not plain `.wrap`: in Gradio 3.41 the whole app sits inside a div.wrap, and with it here nothing on the page
    // was translated any more (found 2026-10-24). A dropdown's value lives in .wrap-inner / .secondary-wrap / .token.
    const SKIP = 'textarea, select, input, .single-select, .wrap-inner, .secondary-wrap, .token, ul.options, li.item, .options, ' +
        '#setting_sd_model_checkpoint .wrap, #setting_sd_vae .wrap, ' +
        '#txt2img_prompt, #img2img_prompt, #txt2img_neg_prompt, #img2img_neg_prompt, ' +
        SKIP_ALWAYS;

    const ATTRIBUTES = ['title', 'placeholder'];
    const has = Object.prototype.hasOwnProperty;

    let active = false;
    let observer = null;
    let isProcessing = false;
    let scheduleTimer = null;

    const translatedNodes = new Map(); // text node -> [original, translated]
    const translatedAttributes = new Map(); // element -> {attribute: [original, translated]}
    const blockOverlays = new Map(); // .prose element -> overlay element with the Hebrew html
    const directionMarked = new Set(); // elements we gave dir="auto"
    const pendingNodes = new Set();

    function dictionary() {
        return window.hebrewUiDictionary || {texts: {}, blocks: {}, contextual: {}};
    }

    function root() {
        return typeof gradioApp === 'function' ? gradioApp() : document.body;
    }

    function lookup(text, element) {
        if (!text) return undefined;
        const dict = dictionary();

        // Optimized contextual lookup: only search closest tab if contextual overrides actually exist
        if (dict.contextual && Object.keys(dict.contextual).length > 0 && element && element.closest) {
            const tab = element.closest('#tabs > .tabitem');
            if (tab && dict.contextual[tab.id] && has.call(dict.contextual[tab.id], text)) {
                return dict.contextual[tab.id][text];
            }
        }

        if (dict.texts && has.call(dict.texts, text)) {
            return dict.texts[text];
        }
        return undefined;
    }

    function skipped(element, selector) {
        if (!element) return true;
        try {
            return element.closest ? (element.closest(selector || SKIP) !== null) : false;
        } catch (e) {
            return false;
        }
    }

    function hintFor(text) {
        if (window.titles && has.call(window.titles, text)) return window.titles[text];
        if (window.deforum_titles && has.call(window.deforum_titles, text)) return window.deforum_titles[text];
        return undefined;
    }

    function rememberAttribute(element, attribute, original, translated) {
        let saved = translatedAttributes.get(element);
        if (!saved) {
            saved = {};
            translatedAttributes.set(element, saved);
        }
        saved[attribute] = [has.call(saved, attribute) ? saved[attribute][0] : original, translated];
    }

    function markDirection(element) {
        if (!element.hasAttribute('dir')) {
            element.setAttribute('dir', 'auto');
            directionMarked.add(element);
        }
    }

    function translateTextNode(node) {
        const parent = node.parentElement;
        const raw = node.nodeValue;
        if (!parent || !raw) return;

        // Skip if already translated
        const saved = translatedNodes.get(node);
        if (saved && saved[1] === raw) return;

        const key = raw.trim();
        if (!key || skipped(parent)) return;

        const hebrew = lookup(key, parent);
        if (hebrew === undefined) return;

        const translated = raw.replace(key, () => hebrew);
        translatedNodes.set(node, [raw, translated]);
        node.nodeValue = translated;
        markDirection(parent);

        if (!parent.getAttribute('title')) {
            const hint = hintFor(key);
            if (hint !== undefined) {
                const hebrewHint = lookup(hint, parent) || hint;
                rememberAttribute(parent, 'title', hint, hebrewHint);
                parent.setAttribute('title', hebrewHint);
            }
        }
    }

    function translateAttributes(element) {
        if (!element.getAttribute || skipped(element, SKIP_ALWAYS)) return;
        for (const attribute of ATTRIBUTES) {
            const value = element.getAttribute(attribute);
            if (!value) continue;
            const hebrew = lookup(value.trim(), element);
            if (hebrew === undefined) continue;
            rememberAttribute(element, attribute, value, hebrew);
            element.setAttribute(attribute, hebrew);
        }
    }

    function translateBlock(prose) {
        if (!prose.isConnected || skipped(prose.parentElement)) return;
        const key = prose.textContent.replace(/\s+/g, ' ').trim();
        const blocks = dictionary().blocks || {};
        const overlay = blockOverlays.get(prose);
        if (overlay) {
            if (overlay.dataset.key === key) return;
            overlay.remove();
            blockOverlays.delete(prose);
            prose.classList.remove('hebrew-ui-hidden');
        }
        if (!has.call(blocks, key)) return;

        const replacement = document.createElement('div');
        replacement.className = prose.className + ' hebrew-ui-block';
        replacement.dataset.key = key;
        replacement.setAttribute('dir', 'rtl');
        replacement.innerHTML = blocks[key];
        prose.classList.add('hebrew-ui-hidden');
        prose.insertAdjacentElement('afterend', replacement);
        blockOverlays.set(prose, replacement);
    }

    function translateSubtree(start) {
        if (!start || !start.isConnected) return;
        if (start.nodeType === Node.TEXT_NODE) {
            translateTextNode(start);
            return;
        }
        if (start.nodeType !== Node.ELEMENT_NODE && start.nodeType !== Node.DOCUMENT_FRAGMENT_NODE) return;

        // Skip our own injected elements
        if (start.classList && (start.classList.contains('hebrew-ui-block') || start.id === 'hebrew-ui-toggle')) return;

        if (blockOverlays.has(start)) translateBlock(start);
        if (start.nodeType === Node.ELEMENT_NODE && skipped(start)) {
            translateAttributes(start);
            return;
        }

        if (start.querySelectorAll) {
            if (start.matches && start.matches('.prose')) translateBlock(start);
            start.querySelectorAll('.prose').forEach(function(prose) {
                if (!prose.classList.contains('hebrew-ui-block')) translateBlock(prose);
            });
        }

        const walker = document.createTreeWalker(start, NodeFilter.SHOW_TEXT);
        let node;
        while ((node = walker.nextNode())) {
            translateTextNode(node);
        }

        if (start.nodeType === Node.ELEMENT_NODE) translateAttributes(start);
        if (start.querySelectorAll) {
            start.querySelectorAll('[title], [placeholder]').forEach(translateAttributes);
        }
    }

    function flush() {
        if (!active) {
            pendingNodes.clear();
            return;
        }

        const nodes = Array.from(pendingNodes);
        pendingNodes.clear();

        isProcessing = true;
        try {
            // Disconnect observer during translation pass to prevent ANY recursion
            if (observer) observer.disconnect();

            for (const node of nodes) {
                if (node.isConnected) translateSubtree(node);
            }
        } catch (e) {
            console.error('[Hebrew UI] Error in translation pass:', e);
        } finally {
            isProcessing = false;
            // Reconnect observer
            if (active && observer) {
                observer.observe(root(), { childList: true, subtree: true });
            }
        }
    }

    function schedule() {
        if (scheduleTimer) clearTimeout(scheduleTimer);
        // Debounce by 60ms to let Gradio finish DOM mutations
        scheduleTimer = setTimeout(flush, 60);
    }

    function startObserving() {
        if (observer) observer.disconnect();

        observer = new MutationObserver(function(records) {
            if (isProcessing || !active) return;

            let hasNewNodes = false;
            for (const record of records) {
                if (record.type === 'childList') {
                    for (const node of record.addedNodes) {
                        if (node.nodeType === Node.ELEMENT_NODE) {
                            if (node.classList && (node.classList.contains('hebrew-ui-block') || node.id === 'hebrew-ui-toggle')) continue;
                            pendingNodes.add(node);
                            hasNewNodes = true;
                        }
                    }
                }
            }

            if (hasNewNodes) {
                schedule();
            }
        });

        // Observe ONLY childList on root! Never characterData or attributes globally.
        observer.observe(root(), { childList: true, subtree: true });
    }

    function restoreEverything() {
        isProcessing = true;
        try {
            if (observer) observer.disconnect();

            for (const [node, [original, translated]] of translatedNodes) {
                if (node.nodeValue === translated) node.nodeValue = original;
            }
            translatedNodes.clear();

            for (const [element, saved] of translatedAttributes) {
                for (const attribute of Object.keys(saved)) {
                    const [original, translated] = saved[attribute];
                    if (element.getAttribute(attribute) === translated) element.setAttribute(attribute, original);
                }
            }
            translatedAttributes.clear();

            for (const [prose, replacement] of blockOverlays) {
                replacement.remove();
                prose.classList.remove('hebrew-ui-hidden');
            }
            blockOverlays.clear();

            for (const element of directionMarked) element.removeAttribute('dir');
            directionMarked.clear();

        } finally {
            isProcessing = false;
        }
    }

    function updateToggle() {
        const button = document.getElementById('hebrew-ui-toggle');
        if (!button) return;
        button.textContent = active ? 'English' : 'עברית';
        button.title = active ? 'Switch the interface back to English (Ctrl+Alt+E)' : 'להעביר את הממשק לעברית';
        button.setAttribute('aria-pressed', String(active));
    }

    function setActive(value, remember) {
        value = Boolean(value);
        if (value !== active) {
            active = value;
            document.documentElement.classList.toggle('hebrew-ui-active', active);
            if (active) {
                isProcessing = true;
                try {
                    translateSubtree(root());
                } finally {
                    isProcessing = false;
                }
                startObserving();
            } else {
                if (observer) observer.disconnect();
                observer = null;
                pendingNodes.clear();
                restoreEverything();
            }
        }
        if (remember) {
            try {
                localStorage.setItem(STORAGE_KEY, active ? 'he' : 'en');
            } catch (e) {
                // storage can be unavailable
            }
        }
        updateToggle();
    }

    function createToggle() {
        if (document.getElementById('hebrew-ui-toggle')) return;
        const button = document.createElement('button');
        button.id = 'hebrew-ui-toggle';
        button.type = 'button';
        button.addEventListener('click', function() {
            setActive(!active, true);
        });
        const quicksettings = root().querySelector('#quicksettings');
        if (quicksettings) {
            quicksettings.appendChild(button);
        } else {
            button.classList.add('hebrew-ui-floating');
            document.body.appendChild(button);
        }
        updateToggle();
    }

    // Emergency Reset function accessible globally
    window.resetHebrewUi = function() {
        try {
            localStorage.removeItem(STORAGE_KEY);
        } catch (e) {}
        setActive(false, false);
        console.log('[Hebrew UI] Emergency reset executed. Language returned to English.');
    };

    // Keyboard shortcut: Ctrl+Alt+E or Ctrl+Alt+H to emergency reset
    window.addEventListener('keydown', function(e) {
        if (e.ctrlKey && e.altKey && (e.key.toLowerCase() === 'e' || e.key.toLowerCase() === 'h')) {
            window.resetHebrewUi();
        }
    });

    window.hebrewUi = {
        // Gradio changes a label or a button's text in place, which the observer above does not watch (it sees new
        // elements only). A tab that relabels its own controls asks here for them to be looked at again.
        refresh: function(element) {
            if (!active || !element) return;
            pendingNodes.add(element);
            schedule();
        },
        setActive: setActive,
        isActive: function() {
            return active;
        },
        lookup: lookup,
        reset: window.resetHebrewUi
    };

    onUiLoaded(function() {
        createToggle();
        let saved = null;
        try {
            saved = localStorage.getItem(STORAGE_KEY);
        } catch (e) {
            saved = null;
        }

        // Wait safely until Gradio finishes initializing checkpoint models before applying Hebrew
        if (saved === 'he') {
            setTimeout(function() {
                try {
                    setActive(true, false);
                } catch (e) {
                    console.error('[Hebrew UI] Safe startup error:', e);
                    window.resetHebrewUi();
                }
            }, 1200);
        }
    });
})();

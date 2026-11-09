// Forge interface languages: English, the built-in Hebrew, and any language made with the translation kit
// (translation-kit/, served with the languages people add by scripts/ui_languages.py).
// Hardened against MutationObserver loops and Gradio model dropdown desynchronization.

(function() {
    const STORAGE_KEY = 'forge-hebrew-ui';       // the language shown: 'en', 'he' or a code from languages/
    const LAST_KEY = 'forge-hebrew-ui-last';     // the one the main button switches to from English
    const BUILT_IN = [{code: 'en', name: 'English', direction: 'ltr'}, {code: 'he', name: 'עברית', direction: 'rtl'}];
    const EMPTY = {texts: {}, blocks: {}, contextual: {}};

    // Elements that must NEVER have their text or attributes modified
    const SKIP_ALWAYS = 'script, style, pre, code, svg, .extra-network-cards, .card, .gallery, .thumbnails, ' +
        '.json-holder, .cm-editor, #licenses, #hebrew-ui-toggle, .hebrew-ui-block, .hebrew-ui-hidden, ' +
        '.hebrew-ui-skip, #ui-lang-dialog, #forge-desktop-bar, #forge-desktop-floating-toggle, #forge-desktop-toast, ' +
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

    let language = 'en';
    let active = false;                          // a language other than English is shown
    let languages = BUILT_IN.slice();            // + the ones in languages/ (refreshLanguages)
    let kit = null;                              // {parts, texts, blocks, prompt} of translation-kit/
    const loaded = {};                           // code -> dictionary of a language from languages/
    let switching = 0;
    let observer = null;
    let isProcessing = false;
    let scheduleTimer = null;

    const translatedNodes = new Map(); // text node -> [original, translated]
    const translatedAttributes = new Map(); // element -> {attribute: [original, translated]}
    const blockOverlays = new Map(); // .prose element -> overlay element with the Hebrew html
    const directionMarked = new Set(); // elements we gave dir="auto"
    const pendingNodes = new Set();

    function dictionary() {
        if (language === 'he') return window.hebrewUiDictionary || EMPTY;
        return loaded[language] || EMPTY;
    }

    function info(code) {
        return languages.find((l) => l.code === code) || {code: code, name: code, direction: 'ltr'};
    }

    function direction() {
        if (language === 'he') return 'rtl';
        return (loaded[language] && loaded[language].direction) || info(language).direction || 'ltr';
    }

    // help blocks of a language from languages/ come from somebody's AI: no scripts, frames or event handlers
    function cleanHtml(html) {
        const template = document.createElement('template');   // inert: nothing in it loads or runs
        template.innerHTML = html;
        template.content.querySelectorAll('script, iframe, object, embed, style, link, meta, base, form')
            .forEach((element) => element.remove());
        template.content.querySelectorAll('*').forEach((element) => {
            for (const attribute of Array.from(element.attributes)) {
                if (/^on/i.test(attribute.name) || /^\s*javascript:/i.test(attribute.value)) element.removeAttribute(attribute.name);
            }
        });
        return template.innerHTML;
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
        replacement.setAttribute('dir', direction());
        replacement.innerHTML = language === 'he' ? blocks[key] : cleanHtml(blocks[key]);
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

    function storage(key, value) {
        try {
            if (value === undefined) return localStorage.getItem(key);
            localStorage.setItem(key, value);
        } catch (e) {
            // storage can be unavailable
        }
        return null;
    }

    // the language's own words for the menu and the window below (they are in every dictionary, the kit has them)
    function tr(text) {
        if (!active) return text;
        const translated = lookup(text);
        return translated === undefined ? text : translated;
    }

    function format(text) {
        const values = Array.prototype.slice.call(arguments, 1);
        return text.replace(/\{(\d+)\}/g, (all, i) => (values[i] === undefined ? all : String(values[i])));
    }

    function lastLanguage() {
        let last = storage(LAST_KEY);
        if (!last) {
            const shown = storage(STORAGE_KEY);
            if (shown && shown !== 'en') last = shown;
        }
        return last && last !== 'en' && (last === 'he' || languages.some((l) => l.code === last)) ? last : null;
    }

    // other extensions follow the language through <html>'s classes: hebrew-ui-active (Hebrew, they draw it
    // themselves), ui-lang-active / ui-lang-rtl / ui-lang-<code> (any language), in one change of the attribute
    function applyClasses() {
        const html = document.documentElement;
        const classes = Array.from(html.classList).filter((c) => c !== 'hebrew-ui-active' && !c.startsWith('ui-lang-'));
        if (language === 'he') classes.push('hebrew-ui-active');
        if (active) {
            classes.push('ui-lang-active', 'ui-lang-' + language);
            if (direction() === 'rtl') classes.push('ui-lang-rtl');
        }
        html.setAttribute('data-ui-language', language);
        html.className = classes.join(' ');
    }

    async function setLanguage(code, remember) {
        code = code || 'en';
        const ticket = ++switching;
        if (code !== 'en' && code !== 'he' && !loaded[code]) {
            try {
                const response = await fetch('/forge-ui/languages/' + encodeURIComponent(code));
                if (!response.ok) throw new Error('HTTP ' + response.status);
                loaded[code] = await response.json();
            } catch (e) {
                console.error('[Hebrew UI] could not load the language', code, e);
                code = 'en';
            }
            if (ticket !== switching) return;
        }
        if (code !== language) {
            if (active) {
                if (observer) observer.disconnect();
                observer = null;
                pendingNodes.clear();
                restoreEverything();
            }
            language = code;
            active = code !== 'en';
            applyClasses();
            if (active) {
                isProcessing = true;
                try {
                    translateSubtree(root());
                } finally {
                    isProcessing = false;
                }
                startObserving();
            }
        }
        if (remember) {
            storage(STORAGE_KEY, language);
            if (language !== 'en') storage(LAST_KEY, language);
        }
        updateToggle();
    }

    function setActive(value, remember) {
        return setLanguage(value ? (lastLanguage() || 'he') : 'en', remember);
    }

    async function refreshLanguages() {
        try {
            const response = await fetch('/forge-ui/languages');
            if (!response.ok) return;
            const data = await response.json();
            languages = BUILT_IN.concat((data.languages || []).filter((l) => l.code !== 'en' && l.code !== 'he'));
            kit = data.kit || null;
        } catch (e) {
            // an older Forge without scripts/ui_languages.py: English and Hebrew only
        }
        updateToggle();
    }

    // ------------------------------------------------------------------ the button, the menu and the window

    function makeButton(text, className, onClick) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = className;
        button.textContent = text;
        button.addEventListener('click', onClick);
        return button;
    }

    function updateToggle() {
        const box = document.getElementById('hebrew-ui-toggle');
        if (!box) return;
        const main = box.querySelector('.ui-lang-main');
        const more = box.querySelector('.ui-lang-more');
        const last = lastLanguage();
        if (active) {
            main.textContent = 'English';
            main.title = tr('Back to English (Ctrl+Alt+E)');
        } else if (last) {
            main.textContent = info(last).name;
            main.title = info(last).name;
        } else {
            main.textContent = '🌐 Language';
            main.title = tr('Languages');
        }
        main.setAttribute('aria-pressed', String(active));
        main.dir = 'auto';
        more.hidden = !active && !last;
        more.title = tr('Languages');
    }

    function closeMenu() {
        const menu = document.querySelector('#hebrew-ui-toggle .ui-lang-menu');
        if (menu) menu.remove();
        document.removeEventListener('click', clickOutside, true);
    }

    function clickOutside(e) {
        if (!e.target.closest || !e.target.closest('#hebrew-ui-toggle')) closeMenu();
    }

    function openMenu() {
        const box = document.getElementById('hebrew-ui-toggle');
        if (!box) return;
        if (box.querySelector('.ui-lang-menu')) {
            closeMenu();
            return;
        }
        const menu = document.createElement('div');
        menu.className = 'ui-lang-menu';
        menu.setAttribute('role', 'menu');
        for (const item of languages) {
            const button = makeButton('', 'ui-lang-item' + (item.code === language ? ' ui-lang-current' : ''), () => {
                closeMenu();
                setLanguage(item.code, true);
            });
            const name = document.createElement('bdi');   // its own direction, lined up with the others
            name.textContent = item.name;
            button.appendChild(name);
            button.setAttribute('role', 'menuitemradio');
            button.setAttribute('aria-checked', String(item.code === language));
            menu.appendChild(button);
        }
        menu.appendChild(document.createElement('hr'));
        menu.appendChild(makeButton(tr('Add a language…'), 'ui-lang-item', () => {
            closeMenu();
            openWindow();
        }));
        menu.dir = direction();
        box.appendChild(menu);
        setTimeout(() => document.addEventListener('click', clickOutside, true), 0);
    }

    // a sentence with folder paths in it: each path is its own left-to-right island, so it reads the same in a
    // right-to-left language
    function withPaths(node, text) {
        const paths = Array.prototype.slice.call(arguments, 2);
        text.split(/(\{\d+\})/).forEach((piece) => {
            const match = /^\{(\d+)\}$/.exec(piece);
            if (match && paths[match[1]] !== undefined) {
                const code = document.createElement('code');
                code.dir = 'ltr';
                code.textContent = paths[match[1]];
                node.append(code);
            } else if (piece) {
                node.append(piece);
            }
        });
        return node;
    }

    function closeWindow() {
        const old = document.getElementById('ui-lang-dialog');
        if (old) old.remove();
    }

    function openWindow(message) {
        closeWindow();
        const element = (tag, props, children) => {
            const node = document.createElement(tag);
            Object.assign(node, props || {});
            for (const child of children || []) node.append(child);
            return node;
        };
        const status = element('div', {className: 'ui-lang-status'});
        status.setAttribute('aria-live', 'polite');
        if (message) status.textContent = message;

        const promptOf = (name) => (kit && kit.prompt ? kit.prompt : 'Translate this Forge Studio interface file into {language}.')
            .replace('{language}', name || '<your language>');
        const prompt = element('textarea', {className: 'ui-lang-prompt', readOnly: true, rows: 4, value: promptOf('')});
        prompt.dir = 'ltr';
        const name = element('input', {className: 'ui-lang-name', type: 'text', placeholder: tr('Your language')});
        name.addEventListener('input', () => {
            prompt.value = promptOf(name.value.trim());
        });
        const copy = makeButton(tr('Copy'), 'ui-lang-button', () => {
            prompt.select();
            const done = () => {
                copy.textContent = tr('Copied');
                setTimeout(() => (copy.textContent = tr('Copy')), 1500);
            };
            if (navigator.clipboard && navigator.clipboard.writeText) {
                navigator.clipboard.writeText(prompt.value).then(done, () => {
                    document.execCommand('copy');
                    done();
                });
            } else {
                document.execCommand('copy');
                done();
            }
        });

        const files = element('input', {type: 'file', multiple: true, accept: '.json,.txt,application/json,text/plain', hidden: true});
        const load = makeButton(tr('Load the translated files'), 'ui-lang-button ui-lang-primary', () => files.click());
        files.addEventListener('change', async () => {
            if (!files.files.length) return;
            status.textContent = tr('Loading…');
            load.disabled = true;
            try {
                const items = [];
                for (const file of files.files) items.push({name: file.name, text: await file.text()});
                const response = await fetch('/forge-ui/languages', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({files: items, name: name.value.trim()}),
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(data.detail || data.error || ('HTTP ' + response.status));
                delete loaded[data.code];
                await refreshLanguages();
                if (language === data.code) await setLanguage('en', false);
                await setLanguage(data.code, true);
                let text = format(tr('{0}: {1} of {2} texts are translated. Whatever is missing stays in English.'),
                    data.name, data.covered, data.total);
                if (data.problems && data.problems.length) {
                    text += '\n' + format(tr('Some files could not be read: {0}'), data.problems.join('; '));
                }
                openWindow(text);
            } catch (e) {
                status.textContent = format(tr('Could not load: {0}'), e.message || e);
                load.disabled = false;
            }
        });

        const download = element('a', {className: 'ui-lang-button', href: '/forge-ui/translation-kit.zip',
            download: 'forge-studio-translation-kit.zip', textContent: 'forge-studio-translation-kit.zip'});
        download.dir = 'ltr';
        const count = kit ? format(tr('{0} files, {1} texts'), kit.parts, kit.texts + kit.blocks) : '';

        const box = element('div', {className: 'ui-lang-box'}, [
            element('h3', {textContent: tr('Forge Studio in your language')}),
            element('p', {textContent: tr('Forge Studio comes in English and Hebrew. Any AI chat (ChatGPT, Claude, Gemini…) or AI coding agent can translate it into another language from the translation kit; what it returns is loaded here.')}),
            element('h4', {textContent: tr('1. Download the translation kit')}),
            element('div', {className: 'ui-lang-row'}, [download, element('span', {className: 'ui-lang-note', textContent: count})]),
            element('h4', {textContent: tr('2. Give your AI one or more of the files at a time, with this request:')}),
            element('div', {className: 'ui-lang-row'}, [name, copy]),
            prompt,
            element('h4', {textContent: tr('3. Load the files it returned')}),
            element('div', {className: 'ui-lang-row'}, [load, files]),
            status,
            withPaths(element('p', {className: 'ui-lang-note'}), tr('Or copy the files into {0} (for example {1}) and reload the page.'),
                'extensions/forge-hebrew-ui/languages/<code>/', 'languages/es/'),
            element('div', {className: 'ui-lang-row ui-lang-end'}, [makeButton(tr('Close'), 'ui-lang-button', closeWindow)]),
        ]);
        box.setAttribute('role', 'dialog');
        box.setAttribute('aria-modal', 'true');
        const back = element('div', {id: 'ui-lang-dialog', className: 'ui-lang-back'}, [box]);
        back.dir = direction();
        back.addEventListener('click', (e) => {
            if (e.target === back) closeWindow();
        });
        back.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') closeWindow();
        });
        document.body.appendChild(back);
        name.focus();
    }

    function createToggle() {
        if (document.getElementById('hebrew-ui-toggle')) return;
        const box = document.createElement('div');
        box.id = 'hebrew-ui-toggle';
        box.className = 'ui-lang-switch';
        box.appendChild(makeButton('', 'ui-lang-main', () => {
            if (active) setLanguage('en', true);
            else if (lastLanguage()) setLanguage(lastLanguage(), true);
            else openMenu();
        }));
        box.appendChild(makeButton('▾', 'ui-lang-more', openMenu));
        const quicksettings = root().querySelector('#quicksettings');
        if (quicksettings) {
            quicksettings.appendChild(box);
        } else {
            box.classList.add('hebrew-ui-floating');
            document.body.appendChild(box);
        }
        updateToggle();
    }

    // Emergency Reset function accessible globally
    window.resetHebrewUi = function() {
        try {
            localStorage.removeItem(STORAGE_KEY);
        } catch (e) {}
        setLanguage('en', false);
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
        setLanguage: setLanguage,
        isActive: function() {
            return active;
        },
        language: function() {
            return language;
        },
        direction: direction,
        languages: function() {
            return languages.slice();
        },
        // For pages that draw their own words and keep their own Hebrew: the text in a language from languages/
        // (undefined in English and Hebrew, or when that language has no translation for it)
        text: function(english) {
            if (language === 'en' || language === 'he') return undefined;
            const texts = loaded[language] && loaded[language].texts;
            return texts && has.call(texts, english) ? texts[english] : undefined;
        },
        lookup: lookup,
        reset: window.resetHebrewUi
    };

    onUiLoaded(function() {
        createToggle();
        const saved = storage(STORAGE_KEY);
        const languagesReady = refreshLanguages();

        // Wait safely until Gradio finishes initializing checkpoint models before applying a language
        if (saved && saved !== 'en') {
            setTimeout(function() {
                // a language picked in the meantime wins over the remembered one
                languagesReady.then(() => (switching === 0 ? setLanguage(saved, false) : null)).catch(function(e) {
                    console.error('[Hebrew UI] Safe startup error:', e);
                    window.resetHebrewUi();
                });
            }, 1200);
        }
    });
})();

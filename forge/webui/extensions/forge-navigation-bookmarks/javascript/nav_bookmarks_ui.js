/* global gradioApp, onUiLoaded, onAfterUiUpdate */
(function() {
    'use strict';
    if (window.ForgeNavigationBookmarks) return;
    const M = window.ForgeNavBookmarksModel;
    const KEY = 'forge-navigation-bookmarks.v1';
    const SERVER_CONFIG = '/forge-navigation-bookmarks/config';
    const RECOVERY_KEY = 'forge-navigation-bookmarks.startup-recovery.v1';
    const MIME = 'application/x-forge-navigation-bookmark';
    let config, draft, nav, controls, contents, region, dialog, status, notice, observer, controller;
    let moveDialog, moveOrigin, moveItem;
    let presentationController;
    let noticeTimer;
    let bindings = new Map();
    let originals = [];
    let openFolder = null, drag = null, suppressClickUntil = 0;
    let loadError = '', rawSaved = null, storageChanged = false;
    let serverLoadStarted = false, serverEndpointKnown = false, serverConfigLoaded = false, serverSaveQueue = Promise.resolve();
    const nativeLabels = new WeakMap();
    const available = () => [...bindings].map(([id, binding]) => ({id, registration: binding.registration}));
    const decorated = new WeakMap();
    const tr = value => window.localization?.[value] || value;

    // Edge app windows can keep the previous document visible while Forge is still
    // starting (or while a reload briefly loses the local server). Give that page a
    // bounded, invisible recovery path once the server is reachable again. The
    // organizer never reloads a page that already has native tabs, and it attempts
    // at most one recovery per document.
    function startupRecoveryWatchdog() {
        let elapsed = 0, recoveryStarted = false, timer;
        const stop = () => { if (timer) clearInterval(timer); timer = null; };
        const tick = async () => {
            if (document.querySelector('#tabs > .tab-nav > button')) return stop();
            elapsed += 3000;
            if (elapsed < 45000 || recoveryStarted) return;
            let previous;
            try { previous = sessionStorage.getItem(RECOVERY_KEY); } catch (_) { previous = null; }
            if (previous && Date.now() - Number(previous) < 300000) return stop();
            try {
                const response = await fetch(SERVER_CONFIG, {cache: 'no-store'});
                if (!response.ok) return;
                recoveryStarted = true;
                try { sessionStorage.setItem(RECOVERY_KEY, String(Date.now())); } catch (_) { /* private mode */ }
                stop();
                window.setTimeout(() => window.location.reload(), 50);
            } catch (_) {
                // The server may still be loading its model. Keep polling until the
                // five-minute bound, without surfacing a new control or error.
                if (elapsed >= 300000) stop();
            }
        };
        timer = window.setInterval(tick, 3000);
        tick();
    }
    startupRecoveryWatchdog();

    function el(tag, cls, value) {
        const node = document.createElement(tag);
        if (cls) node.className = cls;
        if (value != null) node.textContent = value;
        return node;
    }
    function button(label, action, cls = '', guardClick = true) {
        const node = el('button', cls, tr(label));
        node.type = 'button';
        node.addEventListener('click', event => {
            event.stopPropagation();
            if (!guardClick || Date.now() >= suppressClickUntil) action(event);
        });
        return node;
    }
    function nativeButton(label, action, cls) {
        const node = button(label, action, cls);
        // Reuse Gradio's actual theme classes. No copied colors, fonts or title styles.
        const template = originals[0];
        if (template) [...template.classList].filter(c => c.startsWith('svelte-')).forEach(c => node.classList.add(c));
        return node;
    }
    function announce(message, error = false) {
        const target = dialog?.open ? status : notice;
        if (!target) return;
        target.textContent = tr(message);
        target.classList.toggle('fnb-error', error);
        if (target === notice) {
            clearTimeout(noticeTimer);
            if (message === 'Arrangement saved') noticeTimer = setTimeout(() => { target.textContent = ''; }, 3000);
        }
    }
    function loadLocal() {
        loadError = '';
        try {
            rawSaved = localStorage.getItem(KEY);
            config = M.normalize(rawSaved ? JSON.parse(rawSaved) : null, available());
        } catch (error) {
            console.error('Forge navigation bookmarks: loading failed', error);
            loadError = error.message;
            config = M.normalize(null, available());
        }
    }
    function rememberLocal(normalized) {
        try {
            localStorage.setItem(KEY, JSON.stringify(normalized));
            return true;
        } catch (error) {
            console.warn('Forge navigation bookmarks: local browser fallback unavailable', error);
            return false;
        }
    }
    function saveServer(normalized) {
        const snapshot = JSON.stringify(normalized);
        serverSaveQueue = serverSaveQueue.catch(() => {}).then(async () => {
            const response = await fetch(SERVER_CONFIG, {
                method: 'POST', credentials: 'same-origin',
                headers: {'Content-Type': 'application/json'}, body: snapshot,
            });
            if (!response.ok) throw new Error('server returned ' + response.status);
            serverConfigLoaded = true;
        }).catch(error => {
            console.warn('Forge navigation bookmarks: server save failed', error);
            announce('Arrangement saved in this browser; Forge server storage is unavailable.', true);
        });
    }
    async function loadServer() {
        if (serverLoadStarted || !nav) return;
        serverLoadStarted = true;
        const timeout = new AbortController();
        const timer = setTimeout(() => timeout.abort(), 2500);
        let serverApplied = false;
        try {
            const response = await fetch(SERVER_CONFIG, {credentials: 'same-origin', cache: 'no-store', signal: timeout.signal});
            if (!response.ok) throw new Error('server returned ' + response.status);
            serverEndpointKnown = true;
            const envelope = await response.json();
            const serverRaw = envelope?.config;
            if (serverRaw != null) {
                config = M.normalize(serverRaw, available());
                rawSaved = JSON.stringify(config);
                loadError = '';
                serverConfigLoaded = true;
                serverApplied = true;
            } else if (!loadError && rawSaved) {
                // First upgrade keeps an existing arrangement, then shares it with every browser.
                const migrated = M.normalize(JSON.parse(rawSaved), available());
                config = migrated;
                rawSaved = JSON.stringify(migrated);
                saveServer(migrated);
                serverApplied = true;
            } else if (!loadError) {
                config = M.normalize(null, available());
                // Do not let a brand-new browser race an existing browser's first migration.
                // The server document is created by an explicit user save or by migration
                // from an existing browser arrangement.
                serverApplied = true;
            }
        } catch (error) {
            // A server request must never hold the UI hostage. The local copy remains a safe fallback.
            console.warn('Forge navigation bookmarks: server load failed; using browser fallback', error);
        } finally {
            clearTimeout(timer);
            if (serverApplied && config && !loadError) {
                rememberLocal(config);
                renderNavigation();
            }
        }
    }
    function persist(next) {
        try {
            if (loadError && rawSaved) localStorage.setItem(KEY + '.recovery', rawSaved);
            const normalized = M.normalize(next, available());
            config = normalized;
            rawSaved = JSON.stringify(config);
            loadError = '';
            storageChanged = false;
            // Keep a browser fallback for offline recovery, while the Forge server is authoritative.
            if (!rememberLocal(config) && !serverEndpointKnown) throw new Error('browser storage unavailable');
            saveServer(config);
            renderNavigation();
            announce('Arrangement saved');
            return true;
        } catch (error) {
            console.error('Forge navigation bookmarks: saving failed', error);
            announce('Could not save bookmarks. The draft was kept open.', true);
            return false;
        }
    }
    function title(id) {
        return bindings.get(id)?.button.textContent.trim() || config?.items.find(i => i.id === id)?.registration?.label || id.slice(4);
    }
    function activate(id) {
        // Gradio 3 replaces a selected/unselected button when its state changes.
        // Resolve again before activation instead of clicking a detached old button.
        reconcile();
        bindings.get(id)?.button.click();
    }
    function applyAssignments() {
        if (!config) return;
        for (const item of config.items) {
            const node = bindings.get(item.id)?.button;
            if (!node) continue;
            const inFolder = item.destination !== 'favorites';
            node.dataset.fnbDestination = inFolder ? 'folder' : 'main';
            node.classList.toggle('fnb-in-folder', inFolder);
        }
    }
    function syncActive() {
        if (!controls) return;
        applyAssignments();
        const active = [...bindings].find(([, b]) => b.button.classList.contains('selected'))?.[0];
        controls.querySelectorAll('[data-folder]').forEach(node => {
            const current = config.items.some(i => i.id === active && i.destination === node.dataset.folder);
            node.classList.toggle('fnb-current-folder', current);
            if (current) node.setAttribute('aria-current', 'page');
            else node.removeAttribute('aria-current');
        });
        contents?.querySelectorAll('[data-item]').forEach(node => {
            const current = node.dataset.item === active;
            node.classList.toggle('selected', current);
            node.disabled = bindings.get(node.dataset.item)?.button.disabled || false;
            if (current) node.setAttribute('aria-current', 'page');
            else node.removeAttribute('aria-current');
        });
    }
    function folderButton(id) {
        return [...(controls?.querySelectorAll('[data-folder]') || [])].find(node => node.dataset.folder === id);
    }
    function positionFolder() {
        const anchor = folderButton(openFolder);
        if (!anchor || !region || region.hidden) return;
        const rect = anchor.getBoundingClientRect();
        const width = Math.min(320, window.innerWidth - 16);
        region.style.width = width + 'px';
        const left = Math.max(8, Math.min(rect.left, window.innerWidth - width - 8));
        const height = region.getBoundingClientRect().height;
        const top = rect.bottom + height + 4 <= window.innerHeight - 8 ? rect.bottom + 4 : Math.max(8, rect.top - height - 4);
        region.style.left = left + 'px';
        region.style.top = top + 'px';
    }
    function closeFolder(restoreFocus = false) {
        const id = openFolder;
        openFolder = null;
        if (region) region.hidden = true;
        if (contents) contents.hidden = true;
        controls?.querySelectorAll('[data-folder]').forEach(node => node.setAttribute('aria-expanded', 'false'));
        if (restoreFocus) {
            folderButton(id)?.focus();
            queueMicrotask(() => queueMicrotask(() => folderButton(id)?.focus()));
        }
    }
    function closeMove() {
        moveDialog.close();
        const item = config.items.find(i => i.id === moveItem);
        const target = item?.destination === 'favorites' ? bindings.get(moveItem)?.button : folderButton(item?.destination);
        if (target?.isConnected) target.focus();
        else if (moveOrigin?.isConnected) moveOrigin.focus();
        else controls?.querySelector('.fnb-manage')?.focus();
    }
    function openMove(id, origin) {
        if (draft || (moveDialog?.open && moveItem === id)) return;
        reconcile();
        const item = config.items.find(i => i.id === id);
        if (!item || !bindings.has(id)) return;
        closeFolder();
        moveItem = id; moveOrigin = origin;
        if (!moveDialog) {
            moveDialog = el('dialog', 'fnb-move-dialog');
            moveDialog.id = 'forge-navigation-bookmarks-move';
            moveDialog.setAttribute('aria-labelledby', 'fnb-move-heading');
            moveDialog.addEventListener('keydown', event => event.stopPropagation());
            moveDialog.addEventListener('cancel', event => { event.preventDefault(); closeMove(); });
            nav.parentElement.parentElement.append(moveDialog);
        }
        moveDialog.replaceChildren();
        const heading = el('h2', '', tr('Move extension')); heading.id = 'fnb-move-heading';
        const label = el('p', 'fnb-move-title', title(id)); label.dir = 'auto';
        const help = el('p', 'fnb-help', tr('Choose where this extension appears. Changes are saved immediately.'));
        const destinations = el('div', 'fnb-move-destinations');
        for (const destination of [{id: 'favorites', name: tr('Main bar')}, ...config.folders]) {
            const choice = button(destination.name, () => {
                if (loadError && !window.confirm(tr('Replace unreadable bookmarks with this arrangement? A recovery copy will be kept.'))) return;
                const next = M.clone(config);
                if (M.move(next, id, destination.id) && persist(next)) closeMove();
                else if (notice?.textContent) {
                    const error = el('p', 'fnb-error', notice.textContent); error.setAttribute('role', 'status'); moveDialog.append(error);
                }
            }, 'fnb-move-destination', false);
            choice.dataset.destination = destination.id;
            choice.disabled = destination.id === item.destination;
            choice.setAttribute('aria-current', String(destination.id === item.destination));
            choice.dir = 'auto';
            destinations.append(choice);
        }
        moveDialog.append(heading, label, help, destinations, button('Cancel', closeMove, '', false));
        moveDialog.showModal();
        moveDialog.querySelector('button:not(:disabled)')?.focus();
    }
    function quickMove(node, id, signal = undefined) {
        let timer, start, blockClick = false, touchDraggable, releaseTimer;
        const cancel = () => {
            clearTimeout(timer); start = null;
            if (touchDraggable !== undefined) { node.draggable = touchDraggable; touchDraggable = undefined; }
        };
        const show = (hold = false) => {
            if (hold) {
                blockClick = true;
                suppressClickUntil = Date.now() + 350;
                clearTimeout(releaseTimer);
                releaseTimer = setTimeout(() => { blockClick = false; }, 1200);
            }
            openMove(id, node);
        };
        node.addEventListener('pointerdown', event => {
            if (event.button !== 0 || node.disabled) return;
            cancel(); clearTimeout(releaseTimer); blockClick = false; start = {x: event.clientX, y: event.clientY};
            // Let a finger scroll normally; use a hold for moving instead of browser-native touch dragging.
            if (event.pointerType === 'touch') { touchDraggable = node.draggable; node.draggable = false; }
            timer = setTimeout(() => { if (start) show(true); }, 550);
        }, {signal});
        node.addEventListener('pointermove', event => {
            if (start && Math.hypot(event.clientX - start.x, event.clientY - start.y) > 8) cancel();
        }, {signal});
        node.addEventListener('pointerup', () => {
            if (blockClick) {
                suppressClickUntil = Date.now() + 350;
                clearTimeout(releaseTimer);
                releaseTimer = setTimeout(() => { blockClick = false; }, 400);
            }
            cancel();
        }, {signal});
        node.addEventListener('pointercancel', cancel, {signal});
        node.addEventListener('pointerleave', cancel, {signal});
        node.addEventListener('dragstart', cancel, {signal});
        node.addEventListener('click', event => {
            if (!blockClick) return;
            event.preventDefault(); event.stopImmediatePropagation(); blockClick = false;
        }, {capture: true, signal});
        node.addEventListener('contextmenu', event => { event.preventDefault(); event.stopPropagation(); cancel(); show(); }, {signal});
        node.addEventListener('keydown', event => {
            if (event.key !== 'ContextMenu' && !(event.shiftKey && event.key === 'F10')) return;
            event.preventDefault(); event.stopPropagation(); show();
        }, {signal});
        signal?.addEventListener('abort', () => { cancel(); clearTimeout(releaseTimer); blockClick = false; }, {once: true});
    }
    function clearDrop() {
        gradioApp().querySelectorAll('.fnb-drop-before, .fnb-drop-zone').forEach(node => node.classList.remove('fnb-drop-before', 'fnb-drop-zone'));
    }
    function draggable(node, kind, id) {
        node.draggable = true;
        node.addEventListener('dragstart', event => {
            if (event.target.closest('input, select')) { event.preventDefault(); return; }
            drag = {kind, id, draft: !!draft};
            event.dataTransfer.setData(MIME, JSON.stringify(drag));
            event.dataTransfer.setData('text/plain', title(id));
            event.dataTransfer.effectAllowed = 'move';
            node.classList.add('fnb-dragging');
            event.stopPropagation();
        });
        node.addEventListener('dragend', () => {
            drag = null;
            suppressClickUntil = Date.now() + 250;
            node.classList.remove('fnb-dragging');
            clearDrop();
        });
    }
    function dropTarget(node, destination, before = null, folders = false, signal = undefined) {
        const accepts = () => drag && drag.draft === !!draft && (folders ? drag.kind === 'folder' : drag.kind === 'item');
        node.addEventListener('dragover', event => {
            if (!accepts()) return;
            event.preventDefault(); event.stopPropagation();
            event.dataTransfer.dropEffect = 'move';
            clearDrop();
            node.classList.add(before ? 'fnb-drop-before' : 'fnb-drop-zone');
        }, {signal});
        node.addEventListener('dragleave', event => {
            if (!node.contains(event.relatedTarget)) node.classList.remove('fnb-drop-before', 'fnb-drop-zone');
        }, {signal});
        node.addEventListener('drop', event => {
            if (!accepts()) return;
            event.preventDefault(); event.stopPropagation();
            const operation = drag;
            drag = null; // Ignore duplicate drops and drops from outside this organizer.
            suppressClickUntil = Date.now() + 250;
            clearDrop();
            const next = draft || M.clone(config);
            const changed = folders ? M.moveFolder(next, operation.id, before) : M.move(next, operation.id, destination, before);
            if (!changed) return;
            if (draft) { renderManager(); announce('Moved. Save to keep your changes.'); }
            else persist(next);
        }, {signal});
    }
    function decorateNative(node, id) {
        if (decorated.has(node)) return;
        // Save only properties this extension owns, so disabling it restores native navigation.
        const abort = new AbortController();
        decorated.set(node, {order: node.style.order, draggable: node.getAttribute('draggable'), abort});
        // Own drag listeners are attached once per native button and removed on structural changes.
        // Native listeners use the same operations without replacing or cloning the real button.
        node.draggable = true;
        node.dataset.fnbItem = id;
        quickMove(node, id, abort.signal);
        node.addEventListener('dragstart', event => {
            drag = {kind: 'item', id, draft: false};
            event.dataTransfer.setData(MIME, JSON.stringify(drag));
            event.dataTransfer.setData('text/plain', title(id));
            event.dataTransfer.effectAllowed = 'move';
            node.classList.add('fnb-dragging');
            event.stopPropagation();
        }, {signal: abort.signal});
        node.addEventListener('dragend', () => {
            drag = null; suppressClickUntil = Date.now() + 250;
            node.classList.remove('fnb-dragging'); clearDrop();
        }, {signal: abort.signal});
        node.addEventListener('dragover', event => {
            if (!drag || drag.kind !== 'item' || drag.draft) return;
            const item = config.items.find(i => i.id === id);
            if (item?.destination !== 'favorites') return;
            event.preventDefault(); event.stopPropagation(); clearDrop(); node.classList.add('fnb-drop-before');
        }, {signal: abort.signal});
        node.addEventListener('drop', event => {
            if (!drag || drag.kind !== 'item' || drag.draft) return;
            event.preventDefault(); event.stopPropagation();
            const operation = drag; drag = null; suppressClickUntil = Date.now() + 250; clearDrop();
            const next = M.clone(config);
            if (M.move(next, operation.id, 'favorites', id)) persist(next);
        }, {signal: abort.signal});
    }
    function restoreNative() {
        originals.forEach(node => {
            const saved = decorated.get(node);
            if (!saved) return;
            saved.abort?.abort();
            node.style.order = saved.order;
            if (saved.draggable == null) node.removeAttribute('draggable');
            else node.setAttribute('draggable', saved.draggable);
            node.classList.remove('fnb-in-folder', 'fnb-dragging', 'fnb-drop-before', 'fnb-drop-zone');
            delete node.dataset.fnbItem;
            delete node.dataset.fnbDestination;
            decorated.delete(node);
        });
    }
    function renderNavigation() {
        if (!nav) return;
        presentationController?.abort();
        presentationController = new AbortController();
        clearTimeout(noticeTimer);
        controls?.remove(); region?.remove(); notice?.remove();
        const mainItems = config.items.filter(i => i.destination === 'favorites' && bindings.has(i.id));
        const slots = originals.map((node, index) => [...bindings.values()].some(b => b.button === node) ? index : null).filter(i => i != null);
        originals.forEach((node, index) => {
            if (![...bindings.values()].some(b => b.button === node)) {
                if (!decorated.has(node)) decorated.set(node, {order: node.style.order, draggable: node.getAttribute('draggable'), abort: null});
                node.style.order = String(index * 1000);
            }
        });
        for (const [id, binding] of bindings) {
            decorateNative(binding.button, id);
            const index = mainItems.findIndex(i => i.id === id);
            binding.button.classList.toggle('fnb-in-folder', index < 0);
            binding.button.dataset.fnbDestination = index < 0 ? 'folder' : 'main';
            binding.button.style.order = String(index < 0 ? 0 : slots[index] * 1000);
        }
        controls = el('span', 'fnb-controls');
        for (const folder of config.folders) {
            const count = config.items.filter(i => i.destination === folder.id && bindings.has(i.id)).length;
            const toggle = nativeButton('▸ ' + folder.name + ' (' + count + ')', () => {
                openFolder = openFolder === folder.id ? null : folder.id;
                renderNavigation();
                [...controls.querySelectorAll('[data-folder]')].find(n => n.dataset.folder === folder.id)?.focus();
            }, 'fnb-folder-toggle');
            toggle.dataset.folder = folder.id; toggle.dir = 'auto'; toggle.title = folder.name;
            toggle.style.order = String((slots.at(-1) ?? originals.length) * 1000 + 10 + config.folders.indexOf(folder));
            toggle.setAttribute('aria-expanded', String(openFolder === folder.id));
            toggle.setAttribute('aria-controls', 'fnb-folder-contents');
            toggle.addEventListener('keydown', event => {
                if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
                event.preventDefault(); event.stopPropagation();
                openFolder = folder.id; renderNavigation();
                const items = [...contents.querySelectorAll('button:not(:disabled)')];
                items[event.key === 'ArrowDown' ? 0 : items.length - 1]?.focus();
            });
            draggable(toggle, 'folder', folder.id);
            dropTarget(toggle, folder.id);
            dropTarget(toggle, null, folder.id, true);
            controls.append(toggle);
        }
        const manage = nativeButton('Organize…', openManager, 'fnb-manage');
        manage.style.order = String((originals.length + 1) * 1000);
        manage.title = tr('Organize the main bar and extension folders');
        controls.append(manage);
        // Appended after ALL original buttons: numeric selectors and native handlers remain valid.
        nav.append(controls);
        contents = el('div', nav.className + ' fnb-folder-contents');
        contents.id = 'fnb-folder-contents';
        contents.hidden = !openFolder || !config.folders.some(f => f.id === openFolder);
        if (!contents.hidden) {
            const items = config.items.filter(i => i.destination === openFolder && bindings.has(i.id));
            contents.setAttribute('aria-label', config.folders.find(f => f.id === openFolder).name);
            contents.setAttribute('role', 'menu');
            items.forEach(item => {
                const proxy = nativeButton(title(item.id), () => { activate(item.id); closeFolder(true); }, 'fnb-folder-item');
                proxy.dataset.item = item.id; proxy.title = title(item.id); proxy.setAttribute('aria-controls', bindings.get(item.id).panel.id);
                proxy.setAttribute('role', 'menuitem');
                quickMove(proxy, item.id, presentationController.signal);
                draggable(proxy, 'item', item.id); dropTarget(proxy, openFolder, item.id);
                contents.append(proxy);
            });
            if (!items.length) contents.append(el('span', 'fnb-empty', tr('Empty folder. Drop extensions here or use Organize.')));
            dropTarget(contents, openFolder);
            contents.addEventListener('keydown', event => {
                event.stopPropagation();
                const keys = ['ArrowDown', 'ArrowUp', 'Home', 'End'];
                if (!keys.includes(event.key)) return;
                event.preventDefault();
                const items = [...contents.querySelectorAll('button:not(:disabled)')];
                if (!items.length) return;
                const index = items.indexOf(document.activeElement);
                const next = event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1 : index < 0 ? (event.key === 'ArrowDown' ? 0 : items.length - 1) :
                    (index + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
                items[next].focus();
            });
            contents.addEventListener('focusout', () => {
                setTimeout(() => {
                    if (openFolder && !contents.contains(document.activeElement) && document.activeElement !== folderButton(openFolder)) closeFolder();
                }, 0);
            });
        }
        region = el('div', 'fnb-folder-region');
        region.id = 'forge-navigation-bookmarks-folder';
        region.hidden = contents.hidden;
        region.append(contents);
        nav.parentElement.parentElement.append(region);
        notice = el('div', 'fnb-notice'); notice.setAttribute('role', 'status'); notice.setAttribute('aria-live', 'polite');
        nav.after(notice);
        syncActive();
        positionFolder();
        if (loadError) announce('Saved bookmarks could not be loaded. Organize can restore defaults; the original data is preserved.', true);
    }
    function field(label, input) {
        const wrapper = el('label', 'fnb-field');
        input.setAttribute('aria-label', tr(label));
        wrapper.append(el('span', '', tr(label)), input);
        return wrapper;
    }
    function nudge(kind, id, delta) {
        const list = kind === 'folder' ? draft.folders : draft.items.filter(i => i.destination === draft.items.find(j => j.id === id).destination);
        const index = list.findIndex(i => i.id === id);
        if (index + delta < 0 || index + delta >= list.length) return;
        const before = delta < 0 ? list[index - 1].id : list[index + 2]?.id || null;
        if (kind === 'folder') M.moveFolder(draft, id, before);
        else M.move(draft, id, list[index].destination, before);
        renderManager('fnb-' + kind + '-' + id + '-' + (delta < 0 ? 'up' : 'down'));
        announce('Moved. Save to keep your changes.');
    }
    function arrows(kind, id, index, total) {
        const group = el('div', 'fnb-arrows');
        for (const [delta, direction, symbol] of [[-1, 'up', '↑'], [1, 'down', '↓']]) {
            const node = button(symbol, () => nudge(kind, id, delta));
            node.id = 'fnb-' + kind + '-' + id + '-' + direction;
            node.setAttribute('aria-label', tr('Move ' + direction)); node.title = tr('Move ' + direction);
            node.disabled = delta < 0 ? index === 0 : index === total - 1;
            group.append(node);
        }
        return group;
    }
    function openManager() {
        if (dialog?.open) return;
        closeFolder();
        draft = M.clone(config); storageChanged = false;
        if (!dialog) {
            dialog = el('dialog', 'fnb-dialog');
            dialog.id = 'forge-navigation-bookmarks-manager';
            dialog.setAttribute('aria-labelledby', 'fnb-heading');
            dialog.addEventListener('keydown', event => event.stopPropagation());
            dialog.addEventListener('cancel', event => { event.preventDefault(); closeManager(); });
            nav.parentElement.parentElement.append(dialog);
        }
        renderManager(); dialog.showModal(); dialog.querySelector('button')?.focus();
        if (loadError) announce(loadError + ' Saving defaults will keep a recovery copy.', true);
    }
    function closeManager() {
        draft = null; drag = null; clearDrop(); dialog.close();
        if (storageChanged) { loadLocal(); storageChanged = false; renderNavigation(); }
        controls?.querySelector('.fnb-manage')?.focus();
    }
    function renderManager(focusId) {
        const scroll = dialog.querySelector('.fnb-groups')?.scrollTop || 0;
        dialog.replaceChildren();
        const header = el('header', 'fnb-dialog-header');
        const heading = el('h2', '', tr('Extension bookmarks')); heading.id = 'fnb-heading';
        header.append(heading, button('Close', closeManager));
        const help = el('p', 'fnb-help', tr('Main bar extensions stay visible beside your folders. Extensions inside folders are hidden from the main bar. Drag to move, or use Destination and the arrows. Save to apply.'));
        const groups = el('div', 'fnb-groups'); dropTarget(groups, null, null, true);
        const name = el('input'); name.type = 'text'; name.maxLength = 80; name.placeholder = tr('New folder name');
        const create = el('div', 'fnb-create');
        const add = () => {
            const value = name.value.trim();
            if (!value || draft.folders.some(f => f.name.toLocaleLowerCase() === value.toLocaleLowerCase())) {
                announce('Use a non-empty, unique folder name.', true); name.focus(); return;
            }
            if (draft.folders.length >= 100) { announce('The folder limit is 100.', true); return; }
            const id = 'folder-' + (crypto.randomUUID ? crypto.randomUUID() : Date.now().toString(36) + '-' + Math.random().toString(36).slice(2));
            draft.folders.push({id, name: value}); renderManager(); announce('Folder created. Save to keep your changes.');
        };
        name.addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); add(); } });
        create.append(field('New folder', name), button('Create folder', add)); groups.append(create);
        // Keep the old storage identifier so existing arrangements need no migration or reset.
        const destinations = [{id: 'favorites', name: tr('Main bar')}, ...draft.folders];
        for (const destination of destinations) {
            const section = el('section', 'fnb-group'); section.dataset.destination = destination.id;
            const sectionHead = el('div', 'fnb-group-head');
            const items = draft.items.filter(i => i.destination === destination.id);
            sectionHead.append(el('h3', '', destination.name + ' · ' + items.length));
            if (destination.id !== 'favorites') {
                draggable(sectionHead, 'folder', destination.id); dropTarget(sectionHead, null, destination.id, true);
                sectionHead.append(arrows('folder', destination.id, draft.folders.findIndex(f => f.id === destination.id), draft.folders.length));
                const folderName = el('input'); folderName.value = destination.name; folderName.maxLength = 80;
                const edit = el('div', 'fnb-folder-edit');
                edit.append(field('Folder name', folderName), button('Rename', () => {
                    const value = folderName.value.trim();
                    if (!value || draft.folders.some(f => f.id !== destination.id && f.name.toLocaleLowerCase() === value.toLocaleLowerCase())) {
                        announce('Use a non-empty, unique folder name.', true); folderName.focus(); return;
                    }
                    destination.name = value; renderManager(); announce('Folder renamed. Save to keep your changes.');
                }), button('Delete folder', () => {
                    if (!window.confirm(tr('Delete this folder? Its extensions will move to the main bar.'))) return;
                    M.deleteFolder(draft, destination.id); renderManager(); announce('Folder deleted; its extensions moved to the main bar.');
                }));
                section.append(sectionHead, edit);
            } else section.append(sectionHead);
            const list = el('div', 'fnb-item-list'); dropTarget(list, destination.id);
            items.forEach((item, index) => {
                const row = el('div', 'fnb-item-row'); row.dataset.item = item.id;
                draggable(row, 'item', item.id); dropTarget(row, destination.id, item.id);
                const label = el('span', 'fnb-item-title', title(item.id)); label.dir = 'auto'; label.title = title(item.id);
                if (!bindings.has(item.id)) label.append(el('small', 'fnb-missing', tr('Unavailable · preferences kept')));
                const select = el('select');
                destinations.forEach(d => { const option = el('option', '', d.name); option.value = d.id; select.append(option); });
                select.value = item.destination; select.setAttribute('aria-label', tr('Destination') + ': ' + title(item.id));
                select.id = 'fnb-destination-' + item.id;
                select.addEventListener('change', () => { M.move(draft, item.id, select.value); renderManager(select.id); });
                row.append(el('span', 'fnb-grip', '⠿'), label, select, arrows('item', item.id, index, items.length));
                list.append(row);
            });
            if (!items.length) list.append(el('p', 'fnb-empty', tr('Drop extensions here')));
            section.append(list); groups.append(section);
        }
        status = el('div', 'fnb-status'); status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite');
        // Forge's global stylesheet hides every <footer>. Keep these actions in a normal div.
        const footer = el('div', 'fnb-dialog-footer');
        footer.setAttribute('role', 'group');
        footer.setAttribute('aria-label', tr('Arrangement actions'));
        footer.append(button('Restore defaults', () => {
            if (!window.confirm(tr('Restore the default main bar and folders? This only affects bookmarks. Save to apply.'))) return;
            draft = M.normalize(null, available()); renderManager(); announce('Defaults restored in the draft. Save to apply, or Cancel to keep your arrangement.');
        }, 'fnb-reset'), button('Cancel', closeManager), button('Save', () => {
            if (storageChanged && !window.confirm(tr('Bookmarks changed in another window. Replace them with this draft?'))) return;
            if (loadError && !window.confirm(tr('Replace unreadable bookmarks with this arrangement? A recovery copy will be kept.'))) return;
            if (persist(draft)) { closeManager(); announce('Arrangement saved'); }
        }, 'fnb-save', false));
        dialog.append(header, help, groups, status, footer); groups.scrollTop = scroll;
        if (focusId) {
            const target = document.getElementById(focusId);
            if (target && !target.disabled) target.focus();
            else dialog.querySelector('.fnb-create input')?.focus();
        }
    }
    function registeredLabels(panels, buttons) {
        // Read the native registration labels before localization. Component numbers are
        // used only to traverse this page's layout, never as persisted identities.
        const components = new Map((window.gradio_config?.components || []).map(c => [c.id, c]));
        const tabsId = [...components.values()].find(c => c.props?.elem_id === 'tabs')?.id;
        const find = node => node?.id === tabsId ? node : (node?.children || []).map(find).find(Boolean);
        const children = find(window.gradio_config?.layout)?.children || [];
        const registered = children.map(c => components.get(c.id));
        const matches = registered.length === panels.length && registered.every((c, i) => c?.props?.elem_id === panels[i].id);
        return panels.map((panel, index) => {
            if (matches) nativeLabels.set(panel, String(registered[index].props.label));
            else if (!nativeLabels.has(panel)) nativeLabels.set(panel, buttons[index].textContent.trim());
            return {panelId: panel.id, label: nativeLabels.get(panel)};
        });
    }
    function reconcile() {
        try {
            reconcileUnsafe();
        } catch (error) {
            // Never let organizer errors prevent Forge's own tabs from working.
            console.error('Forge navigation bookmarks: reconciliation failed; restoring native tabs', error);
            try {
                restoreNative();
                controls?.remove(); region?.remove(); notice?.remove();
                controls = null;
                presentationController?.abort(); observer?.disconnect(); controller?.abort();
            } catch (cleanupError) {
                console.error('Forge navigation bookmarks: native fallback failed', cleanupError);
            }
        }
    }
    function reconcileUnsafe() {
        const tabs = gradioApp().querySelector('#tabs');
        const currentNav = tabs?.querySelector(':scope > .tab-nav');
        if (!currentNav) return;
        const buttons = [...currentNav.querySelectorAll(':scope > button')];
        const panels = [...tabs.querySelectorAll(':scope > .tabitem')];
        if (buttons.length !== panels.length || panels.some(p => !p.id.startsWith('tab_'))) {
            restoreNative(); controls?.remove(); region?.remove(); notice?.remove(); controls = null;
            presentationController?.abort();
            observer?.disconnect(); controller?.abort();
            if (moveDialog?.open) closeMove();
            if (dialog?.open) closeManager();
            return; // Fail open: incompatible DOM leaves all native tabs available.
        }
        let saved = config;
        if (!saved) {
            try { saved = M.normalize(JSON.parse(localStorage.getItem(KEY))); }
            catch { saved = null; } // load() below reports malformed/unsupported data and keeps it intact.
        }
        const instances = M.identify(registeredLabels(panels, buttons), saved);
        const next = new Map();
        panels.forEach((panel, index) => {
            const instance = instances[index];
            next.set(instance.id, {button: buttons[index], panel, registration: instance.registration});
        });
        if (nav === currentNav && controls?.isConnected && next.size === bindings.size && [...next].every(([id, b]) => bindings.get(id)?.button === b.button && bindings.get(id)?.panel === b.panel)) {
            syncActive(); return;
        }
        restoreNative(); observer?.disconnect(); controller?.abort();
        nav = currentNav; bindings = next; originals = buttons;
        if (!config) loadLocal(); else config = M.normalize(config, available());
        if (draft) { draft = M.normalize(draft, available()); renderManager(); }
        controller = new AbortController();
        nav.addEventListener('click', event => {
            if (Date.now() < suppressClickUntil && event.target.closest('button')) {
                event.preventDefault(); event.stopImmediatePropagation();
            }
        }, {capture: true, signal: controller.signal});
        // Blank space on the original bar appends a main-bar item; the stored identifier remains compatible.
        dropTarget(nav, 'favorites', null, false, controller.signal);
        renderNavigation();
        observer = new MutationObserver(mutations => {
            const nativeChanged = mutations.some(m => m.type === 'childList' && m.target === nav &&
                [...m.addedNodes, ...m.removedNodes].some(node => node.nodeName === 'BUTTON'));
            if (nativeChanged) reconcile();
            else syncActive();
        });
        observer.observe(nav, {subtree: true, childList: true, attributes: true, attributeFilter: ['class', 'disabled']});
        loadServer();
    }
    window.addEventListener('storage', event => {
        if (event.key !== KEY && event.key !== null) return;
        if (draft) { storageChanged = true; announce('Bookmarks changed in another window. Cancel to reload, or Save to replace them.', true); }
        else {
            loadLocal(); renderNavigation();
            if (moveDialog?.open) {
                const id = moveItem, origin = moveOrigin;
                moveDialog.close(); openMove(id, origin);
            }
        }
    });
    document.addEventListener('pointerdown', event => {
        const target = event.composedPath()[0];
        if (!openFolder || region?.contains(target) || controls?.contains(target)) return;
        closeFolder();
    }, true);
    document.addEventListener('keydown', event => {
        if (event.key !== 'Escape' || !openFolder || dialog?.open || moveDialog?.open) return;
        event.preventDefault(); event.stopImmediatePropagation(); closeFolder(true);
    }, true);
    window.addEventListener('resize', positionFolder);
    window.addEventListener('scroll', event => {
        if (openFolder && !(event.target instanceof Node && region?.contains(event.target))) closeFolder();
    }, true);
    window.ForgeNavigationBookmarks = {refresh: reconcile};
    onUiLoaded(reconcile); onAfterUiUpdate(reconcile);
})();

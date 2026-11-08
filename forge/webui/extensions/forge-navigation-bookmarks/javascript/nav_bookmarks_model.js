/* global module */
// Pure configuration operations; no Forge settings or DOM writes.
(function(root) {
    'use strict';
    const VERSION = 1;
    const text = (value, max) => typeof value === 'string' ? value.trim().slice(0, max) : '';
    const validId = value => typeof value === 'string' && value.length <= 4096 && value.startsWith('tab_');
    const registration = value => value && validId(value.panelId) && typeof value.label === 'string' && value.label.length <= 256 &&
        Number.isInteger(value.occurrence) && value.occurrence > 0 && value.occurrence <= 2000 ?
        {panelId: value.panelId, label: value.label, occurrence: value.occurrence} : null;
    const empty = () => ({version: VERSION, folders: [], items: []});
    const clone = value => JSON.parse(JSON.stringify(value));

    // Separate organizer identity from the original DOM ID. Match saved instances before
    // allocating keys, so removing one duplicate cannot transfer its assignment to another.
    // Labels come from Gradio's untranslated registration, not the localized button text.
    function identify(tabs, raw) {
        const saved = normalize(raw).items;
        const reserved = new Set(saved.map(i => i.id));
        const used = new Set();
        const occurrences = new Map();
        const counts = new Map();
        tabs.forEach(t => counts.set(t.panelId, (counts.get(t.panelId) || 0) + 1));
        return tabs.map(tab => {
            const label = String(tab.label).slice(0, 256);
            const token = JSON.stringify([tab.panelId, label]);
            const occurrence = (occurrences.get(token) || 0) + 1;
            occurrences.set(token, occurrence);
            const instance = {panelId: tab.panelId, label, occurrence};
            let match = saved.find(i => !used.has(i.id) && i.registration &&
                i.registration.panelId === tab.panelId && i.registration.label === label && i.registration.occurrence === occurrence);
            if (!match) match = saved.find(i => i.id === tab.panelId && !used.has(i.id) && !i.registration);
            // A sole registration may change its title while retaining its native ID.
            if (!match && counts.get(tab.panelId) === 1 && saved.filter(i => i.registration?.panelId === tab.panelId).length === 1) {
                match = saved.find(i => i.registration?.panelId === tab.panelId && !used.has(i.id));
            }
            let id = match?.id;
            if (!id && !reserved.has(tab.panelId) && !used.has(tab.panelId)) id = tab.panelId;
            if (!id) {
                const base = tab.panelId + '::fnb:' + encodeURIComponent(label) + ':' + occurrence;
                id = base;
                let suffix = 1;
                while (used.has(id) || reserved.has(id)) id = base + ':' + suffix++;
            }
            used.add(id);
            return {id, registration: instance};
        });
    }

    function normalize(raw, available = []) {
        if (raw != null && (!raw || typeof raw !== 'object' || raw.version !== VERSION)) {
            throw new Error('Unsupported bookmarks format. Your saved data has been left untouched.');
        }
        const result = empty();
        const folderIds = new Set();
        for (const folder of Array.isArray(raw?.folders) ? raw.folders.slice(0, 100) : []) {
            const id = text(folder?.id, 80);
            const name = text(folder?.name, 80);
            if (!/^folder-[a-zA-Z0-9-]+$/.test(id) || !name || folderIds.has(id)) continue;
            folderIds.add(id);
            result.folders.push({id, name});
        }
        const itemIds = new Set();
        for (const item of Array.isArray(raw?.items) ? raw.items.slice(0, 2000) : []) {
            if (!validId(item?.id) || itemIds.has(item.id)) continue;
            itemIds.add(item.id);
            const normalized = {
                id: item.id,
                destination: folderIds.has(item.destination) ? item.destination : 'favorites',
            };
            const instance = registration(item.registration);
            if (instance) normalized.registration = instance;
            result.items.push(normalized);
        }
        const entries = [...new Map(available.map(value => typeof value === 'string' ? {id: value} : value)
            .filter(value => validId(value?.id)).map(value => [value.id, value])).values()];
        for (const [index, entry] of entries.entries()) {
            const {id} = entry;
            if (validId(id) && !itemIds.has(id)) {
                itemIds.add(id);
                // Newly eligible core/duplicate tabs retain their natural placement, while
                // all existing assignments and the relative order of saved items survive.
                const following = new Set(entries.slice(index + 1).map(e => e.id));
                const insertion = result.items.findIndex(i => i.destination === 'favorites' && following.has(i.id));
                result.items.splice(insertion < 0 ? result.items.length : insertion, 0, {id, destination: 'favorites'});
            }
            const instance = registration(entry.registration);
            if (instance) result.items.find(i => i.id === id).registration = instance;
        }
        return result;
    }

    // Insertion uses stable IDs, never numeric Gradio component IDs or translated labels.
    function move(config, id, destination, before = null) {
        if (destination !== 'favorites' && !config.folders.some(f => f.id === destination)) return false;
        const item = config.items.find(i => i.id === id);
        if (!item || before === id) return false;
        if (before && !config.items.some(i => i.id === before && i.destination === destination)) return false;
        config.items.splice(config.items.indexOf(item), 1);
        item.destination = destination;
        const index = before ? config.items.findIndex(i => i.id === before) : config.items.length;
        config.items.splice(index, 0, item);
        return true;
    }

    function moveFolder(config, id, before = null) {
        const folder = config.folders.find(f => f.id === id);
        if (!folder || id === before || (before && !config.folders.some(f => f.id === before))) return false;
        config.folders.splice(config.folders.indexOf(folder), 1);
        config.folders.splice(before ? config.folders.findIndex(f => f.id === before) : config.folders.length, 0, folder);
        return true;
    }

    function deleteFolder(config, id) {
        if (!config.folders.some(f => f.id === id)) return false;
        config.folders = config.folders.filter(f => f.id !== id);
        config.items.filter(i => i.destination === id).forEach(i => { i.destination = 'favorites'; });
        return true;
    }

    const api = {VERSION, empty, clone, identify, normalize, move, moveFolder, deleteFolder};
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
    else root.ForgeNavBookmarksModel = api;
})(typeof window === 'undefined' ? globalThis : window);

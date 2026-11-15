/* The Lora / Checkpoints / Textual Inversion cards (2026-11-15). Forge fills them once, by an event sent when the page
 * opens; when that event was held up (other extensions loading hundreds of files at the same moment) the tab stayed
 * on a spinner with no cards for good, and only its refresh button brought them. When such a tab is opened and still
 * has no cards after a few seconds, its refresh button is pressed here, once per tab per page. */
(function () {
    'use strict';
    const app = () => (typeof gradioApp === 'function' ? gradioApp() : document);
    const tried = new Set();

    function check(tabname, page) {
        const id = `${tabname}_${page}`;
        if (tried.has(id)) return;
        const cards = app().getElementById(`${id}_cards`);
        const html = app().getElementById(`${id}_cards_html`);
        if (!html || html.offsetParent === null) return;                       // not the tab in view
        if (cards && (cards.querySelector('.card') || cards.querySelector('.nocards'))) return;
        if (html.querySelector('.nocards')) return;                            // a page with nothing in it
        const refresh = app().getElementById(`${id}_extra_refresh`);
        if (!refresh) return;
        tried.add(id);
        console.info(`[UI] ${id}: no cards came; asking for them again`);
        refresh.click();
    }

    document.addEventListener('click', (e) => {
        const button = e.target.closest && e.target.closest('.tab-nav > button');
        if (!button) return;
        const tabs = button.closest('[id$="_extra_tabs"]');
        if (!tabs) return;
        const tabname = tabs.id.replace(/_extra_tabs$/, '');
        setTimeout(() => {
            // the page shown now: its cards element tells which one it is
            const shown = Array.from(app().querySelectorAll(`[id^="${tabname}_"][id$="_cards_html"]`)).find((el) => el.offsetParent !== null);
            if (shown) check(tabname, shown.id.slice(tabname.length + 1).replace(/_cards_html$/, ''));
        }, 8000);
    }, true);
})();

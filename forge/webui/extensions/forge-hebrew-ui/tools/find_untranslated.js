// Lists English text on the Forge page that the Hebrew UI does not translate yet.
// Paste into the browser console. Strings marked "=" (kept in English on purpose) are not reported.
(function() {
    const dict = window.hebrewUiDictionary;
    if (!dict || typeof gradioApp !== 'function') {
        console.warn('Forge Hebrew UI is not loaded on this page');
        return [];
    }
    const has = Object.prototype.hasOwnProperty;
    const kept = new Set(dict.kept || []);
    const skip = 'textarea, select, script, style, pre, code, svg, .extra-network-cards, .card, .gallery, .thumbnails, ' +
        '.json-holder, .cm-editor, #licenses, #hebrew-ui-toggle, .hebrew-ui-block, .hebrew-ui-hidden';
    const known = text => has.call(dict.texts, text) || kept.has(text);
    const found = new Map();
    const note = (text, element, kind) => {
        if (!text || !/[A-Za-z]{2}/.test(text) || /[֐-׿]/.test(text) || known(text) || found.has(text)) return;
        const tab = element.closest('#tabs > .tabitem');
        found.set(text, {tab: tab ? tab.id : '(top)', kind: kind, text: text});
    };
    const walker = document.createTreeWalker(gradioApp(), NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walker.nextNode())) {
        const parent = node.parentElement;
        if (parent && !parent.closest(skip)) note(node.nodeValue.trim(), parent, 'text');
    }
    gradioApp().querySelectorAll('[title], [placeholder]').forEach(function(element) {
        if (element.closest(skip.replace('textarea, select, ', ''))) return;
        note((element.getAttribute('title') || '').trim(), element, 'title');
        note((element.getAttribute('placeholder') || '').trim(), element, 'placeholder');
    });
    const rows = Array.from(found.values());
    console.table(rows);
    return rows;
})();

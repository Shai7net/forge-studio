/* Forge UI Skins - the extension cards of txt2img and img2img (2026-11-15).
 *
 * The cards (ControlNet, ReActor, AnimateDiff, FLUX 3...) start closed (some extensions open theirs). Each card's
 * title bar has a ⠿ handle to drag it into another place and a ⤓ button that puts it into "More", a box at the end
 * that keeps the cards seldom used out of the way (⤒ brings one back). The order and More are the user's own,
 * kept on the server (webui/ui-skins/layout.json), apart from the skins. Settings -> UI Skins turns either off.
 */
(function () {
    'use strict';
    const SK = window.forgeSkins = window.forgeSkins || {};
    const app = () => (typeof gradioApp === 'function' ? gradioApp() : document);
    const TABS = ['txt2img', 'img2img'];
    const AUTO_ID = /^(component-\d+|input-accordion-\d+)$/;
    const T = (t, ...a) => (SK.T ? SK.T(t, ...a) : t);
    Object.assign(SK.HE || (SK.HE = {}), {
        'More': 'עוד', 'Drag to another place': 'גרור למקום אחר', 'Into More': 'העבר ל"עוד"', 'Back from More': 'החזר מ"עוד"',
        'Cards seldom used: ⤓ on a card puts it here, ⤒ brings it back.': 'כרטיסים שבשימוש נדיר: ⤓ על כרטיס מעביר אותו לכאן, ⤒ מחזיר אותו.',
    });

    let layout = {}, settings = {cards_closed: true, cards_organize: true};
    const relabelers = [];
    new MutationObserver(() => relabelers.forEach((f) => f())).observe(document.documentElement, {attributes: true, attributeFilter: ['class']});
    const lists = {};   // tab -> {parent, more, moreBody, start}

    const titleOf = (card) => {
        const span = card.querySelector(':scope > .label-wrap span');
        return (span ? (span.dataset.skinOrigText || span.textContent) : '').replace(/\s+/g, ' ').trim();
    };
    // each extension's card sits in a group of its own: what moves is that group (an "item"), a child of the shared
    // list (the first .styler of <tab>_script_container)
    const cardOf = (item) => (item.classList.contains('gradio-accordion') ? item
        : Array.from(item.querySelectorAll('.gradio-accordion')).find((a) => !a.parentElement.closest('.gradio-accordion')) || null);
    const keyOf = (item) => {
        const card = item.uicard || cardOf(item);
        return card.id && !AUTO_ID.test(card.id) ? '#' + card.id : 'card:' + (SK.english ? SK.english(titleOf(card)) : titleOf(card));
    };
    const cardsIn = (el) => Array.from(el.children).filter((c) => c.classList.contains('uicards-item'));

    function save() {
        for (const tab of TABS) {
            const l = lists[tab];
            if (!l) continue;
            layout[tab] = {order: cardsIn(l.parent).map(keyOf), more: cardsIn(l.moreBody).map(keyOf)};
        }
        fetch('/ui-skins/layout', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({layout})}).catch(() => null);
    }

    function close(item) {
        // every card of the group (AnimateDiff and Dance video share one)
        const cards = item.classList.contains('gradio-accordion') ? [item]
            : Array.from(item.querySelectorAll('.gradio-accordion')).filter((a) => !a.parentElement.closest('.gradio-accordion'));
        for (const card of cards) {
            const bar = card.querySelector(':scope > .label-wrap');
            if (bar && bar.classList.contains('open')) bar.click();
        }
    }

    function tools(item, tab) {
        const card = item.uicard;
        if (card.querySelector(':scope > .uicards-tools')) return;
        const box = document.createElement('div');
        box.className = 'uicards-tools hebrew-ui-skip';
        const grip = document.createElement('span');
        grip.className = 'uicards-grip';
        grip.textContent = '⠿';
        grip.draggable = true;
        grip.title = T('Drag to another place');
        grip.addEventListener('dragstart', (e) => {
            e.dataTransfer.setData('application/x-ui-card', keyOf(item));
            e.dataTransfer.effectAllowed = 'move';
            item.classList.add('uicards-dragging');
            drag = {card: item, tab};
        });
        grip.addEventListener('dragend', () => { item.classList.remove('uicards-dragging'); clearLine(); drag = null; });
        const move = document.createElement('button');
        move.type = 'button';
        move.className = 'uicards-move';
        const relabel = () => {
            const inMore = !!item.closest('.uicards-more');
            move.textContent = inMore ? '⤒' : '⤓';
            move.title = inMore ? T('Back from More') : T('Into More');
        };
        move.addEventListener('click', (e) => {
            e.preventDefault(); e.stopPropagation();
            const l = lists[tab];
            if (item.closest('.uicards-more')) l.parent.insertBefore(item, l.more); else { l.moreBody.append(item); close(item); }
            relabel(); count(tab); save();
        });
        for (const el of [grip, move]) el.addEventListener('click', (e) => e.stopPropagation());
        box.append(grip, move);
        card.append(box);
        card.classList.add('uicards-card');
        item.uicardsRelabel = relabel;
        relabel();
    }

    /* dragging a card: a line shows where it goes */
    let drag = null, line = null;
    const clearLine = () => { if (line) line.remove(); line = null; };
    function dropTarget(e, tab) {
        const l = lists[tab];
        const target = e.target.closest && e.target.closest('.uicards-item, .uicards-more-head');
        if (!target || !l || !(l.parent.contains(target))) return null;
        if (target.classList.contains('uicards-more-head')) return {into: l.moreBody};
        const r = target.getBoundingClientRect();
        return {card: target, after: e.clientY > r.top + r.height / 2};
    }
    function listen(tab) {
        const l = lists[tab];
        l.parent.addEventListener('dragover', (e) => {
            if (!drag || drag.tab !== tab) return;
            const t = dropTarget(e, tab);
            if (!t || t.card === drag.card) return clearLine();
            e.preventDefault();
            e.dataTransfer.dropEffect = 'move';
            if (!line) { line = document.createElement('div'); line.className = 'uicards-line'; }
            if (t.into) l.moreBody.prepend(line);
            else t.card.insertAdjacentElement(t.after ? 'afterend' : 'beforebegin', line);
        });
        l.parent.addEventListener('drop', (e) => {
            if (!drag || drag.tab !== tab) return;
            const t = dropTarget(e, tab);
            e.preventDefault();
            clearLine();
            if (!t || t.card === drag.card) return;
            if (t.into) { t.into.prepend(drag.card); close(drag.card); }
            else t.card.insertAdjacentElement(t.after ? 'afterend' : 'beforebegin', drag.card);
            if (drag.card.uicardsRelabel) drag.card.uicardsRelabel();
            count(tab); save();
        });
    }

    function count(tab) {
        const l = lists[tab];
        const n = cardsIn(l.moreBody).length;
        l.moreCount.textContent = n ? ` · ${n}` : '';
        l.more.classList.toggle('uicards-empty', !n);
    }

    function setUp(tab) {
        if (lists[tab]) return true;
        const container = app().getElementById(tab + '_script_container');
        if (!container) return false;
        const parent = container.querySelector(':scope > .styler') || container;
        const cards = Array.from(parent.children).filter((child) => {
            const card = cardOf(child);
            if (!card) return false;
            child.uicard = card;
            child.classList.add('uicards-item');
            return true;
        });
        if (!cards.length) return false;
        if (settings.cards_closed) cards.forEach(close);
        if (!settings.cards_organize) { lists[tab] = {parent, closedOnly: true}; return true; }

        // More: after the last card
        const more = document.createElement('div');
        more.className = 'uicards-more hebrew-ui-skip';
        const head = document.createElement('button');
        head.type = 'button';
        head.className = 'uicards-more-head';
        const arrow = document.createElement('span');
        arrow.className = 'uicards-more-arrow';
        arrow.textContent = '▸';
        const word = document.createElement('span');
        word.textContent = T('More');
        const moreCount = document.createElement('span');
        moreCount.className = 'uicards-more-count';
        head.append(arrow, ' ', word, moreCount);
        const body = document.createElement('div');
        body.className = 'uicards-more-body';
        body.hidden = true;
        const hint = document.createElement('div');
        hint.className = 'uicards-more-hint';
        hint.textContent = T('Cards seldom used: ⤓ on a card puts it here, ⤒ brings it back.');
        body.append(hint);
        head.addEventListener('click', () => { body.hidden = !body.hidden; more.classList.toggle('open', !body.hidden); });
        relabelers.push(() => {
            word.textContent = T('More');
            hint.textContent = T('Cards seldom used: ⤓ on a card puts it here, ⤒ brings it back.');
            parent.querySelectorAll('.uicards-item').forEach((it) => { if (it.uicardsRelabel) it.uicardsRelabel(); });
            parent.querySelectorAll('.uicards-grip').forEach((g) => { g.title = T('Drag to another place'); });
        });
        more.append(head, body);
        cards[cards.length - 1].insertAdjacentElement('afterend', more);
        lists[tab] = {parent, more, moreBody: body, moreCount, start: cards[0].previousElementSibling};

        // the saved order, then the cards it does not know in their own order; More's cards into More
        const saved = layout[tab] || {};
        const byKey = new Map(cards.map((c) => [keyOf(c), c]));
        const order = (saved.order || []).filter((k) => byKey.has(k));
        const inMore = (saved.more || []).filter((k) => byKey.has(k));
        const rest = cards.filter((c) => !order.includes(keyOf(c)) && !inMore.includes(keyOf(c)));
        let ref = lists[tab].start;
        for (const card of order.map((k) => byKey.get(k)).concat(rest)) {
            if (ref) ref.insertAdjacentElement('afterend', card); else parent.prepend(card);
            ref = card;
        }
        more.remove();
        ref.insertAdjacentElement('afterend', more);
        for (const k of inMore) { body.append(byKey.get(k)); close(byKey.get(k)); }
        cards.forEach((c) => tools(c, tab));
        listen(tab);
        count(tab);
        return true;
    }

    async function boot() {
        try {
            const [state, saved] = await Promise.all([fetch('/ui-skins/state').then((r) => r.json()), fetch('/ui-skins/layout').then((r) => r.json())]);
            settings = state;
            layout = saved || {};
        } catch (e) { /* defaults */ }
        let tries = 0;
        const go = () => {
            const done = TABS.map(setUp);
            if (done.some((d) => !d) && ++tries < 40) setTimeout(go, 500);
        };
        go();
    }
    if (typeof onUiLoaded === 'function') onUiLoaded(boot); else window.addEventListener('load', boot);
})();

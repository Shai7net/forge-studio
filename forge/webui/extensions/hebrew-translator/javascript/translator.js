// Hebrew prompt writer: 🌐 in a prompt field writes an English prompt for the model behind it (scripts/translator.py),
// and the bar under the field rates it: 👍 keeps it as an example to write like, 👎 writes another version and takes a
// note on what was wrong. The bar draws its own Hebrew (it sits in .hebrew-ui-skip): the Hebrew UI translates only
// new elements, and these texts change in place.
(function() {
    const FIELDS = ["txt2img_prompt", "txt2img_neg_prompt", "img2img_prompt", "img2img_neg_prompt", "vs_prompt",
        "vs_part1", "vs_part2", "vs_part3", "vs_part4", "vs_part5", "ch_character", "ch_video"];
    const state = {}; // per field: {source, written, target, label, id, rated, dislikedId}

    const TEXT = {
        write: ["Write an English prompt for this model from what you wrote (any language)",
            "לכתוב מזה פרומפט באנגלית שמתאים למודל (אפשר לכתוב בעברית)"],
        like: ["Good prompt: the writer learns from it (what is in the box now is kept, your edits too)",
            "פרומפט טוב: הכותב לומד ממנו (נשמר מה שבתיבה עכשיו, כולל התיקונים שלך)"],
        dislike: ["Not good: the writer learns from it and writes another version",
            "לא טוב: הכותב לומד מזה וכותב גרסה אחרת"],
        again: ["Another version of the same idea", "גרסה אחרת לאותו רעיון"],
        undo: ["Back to what you wrote", "חזרה למה שכתבת"],
        library: ["What the writer has learned from your ratings", "מה הכותב למד מהדירוגים שלך"],
        close: ["Close", "סגירה"],
        writing: ["Writing…", "כותב…"],
        written: ["Written for {target} in {s} s", "נכתב בשביל {target} ב-{s} שניות"],
        fromLiked: [" · following {n} you liked", " · לפי {n} שאהבת"],
        liked: ["👍 Kept. The writer will write more like this.", "👍 נשמר. הכותב יכתוב יותר כמו זה."],
        disliked: ["👎 Noted. Writing another version…", "👎 נרשם. כותב גרסה אחרת…"],
        dislikedDone: ["👎 Noted, here is another version.", "👎 נרשם, הנה גרסה אחרת."],
        why: ["What was wrong? A wrong word: צב = turtle (it remembers the word). Optional, Enter saves",
            "מה לא היה טוב? מילה לא נכונה: צב = turtle (הוא יזכור את המילה). לא חובה, Enter שומר"],
        noteSaved: ["Note saved: the writer will follow it.", "ההערה נשמרה: הכותב יתחשב בה."],
        empty: ["Write an idea in the box first.", "קודם כותבים רעיון בתיבה."],
        undone: ["Back to what you wrote.", "חזרת למה שכתבת."],
        busy: ["Forge is busy making a picture or a video: the writer needs the card. Try again when it ends.",
            "Forge באמצע יצירה של תמונה או וידאו, והכותב צריך את כרטיס המסך. נסה שוב כשזה נגמר."],
        failed: ["The writer failed: ", "הכותב נכשל: "],
        title: ["What the writer has learned", "מה הכותב למד"],
        counts: ["{liked} liked · {disliked} disliked", "{liked} אהבת · {disliked} לא אהבת"],
        nothing: ["Nothing yet: rate prompts with 👍 and 👎 and they appear here.",
            "עדיין כלום: דרג פרומפטים ב-👍 וב-👎 והם יופיעו כאן."],
        forget: ["Forget this one", "לשכוח את זה"],
        note: ["Note: ", "הערה: "],
        meaning: ["Understood:", "הבין:"],
        meaningTip: ["What the writer understood from your words. A wrong word here? 👎 and say which word you meant.",
            "מה הכותב הבין מהמילים שלך. מילה לא נכונה? 👎 וכתוב לאיזו מילה התכוונת."],
    };
    const TARGET_HE = {
        sd15: "SD 1.5", sdxl: "SDXL", pony: "Pony", booru: "Illustrious / אנימה", flux: "FLUX",
        wan_t2v: "וידאו Wan מטקסט", wan_i2v: "וידאו Wan מתמונה", negative: "פרומפט שלילי",
        fibo: "FIBO 2.0 (JSON מובנה)", fibo_edit: "עריכה ב-FIBO 2.0",
        character: "דמות לגיליון דמות (JSON)", wan_ingredients: "וידאו Wan מתמונות רכיבים",
        ltx_t2v: "וידאו LTX מטקסט (עם סאונד)", ltx_i2v: "וידאו LTX מתמונה (עם סאונד)",
        ltx_ingredients: "וידאו LTX מתמונות רכיבים (עם סאונד)", wan_replace: "החלפת דמות (Wan Animate)",
    };
    // Material Icons "thumb_up" (Apache 2.0); thumb down is the same turned over
    const THUMB = '<svg viewBox="0 0 24 24" width="17" height="17" aria-hidden="true"><path fill="currentColor" ' +
        'd="M1 21h4V9H1v12zm22-11c0-1.1-.9-2-2-2h-6.31l.95-4.57.03-.32c0-.41-.17-.79-.44-1.06L14.17 1 7.59 7.59C7.22 ' +
        '7.95 7 8.45 7 9v10c0 1.1.9 2 2 2h9c.83 0 1.54-.5 1.84-1.22l3.02-7.05c.09-.23.14-.47.14-.73v-2z"/></svg>';

    const hebrew = () => document.documentElement.classList.contains("hebrew-ui-active");

    // a language made with the translation kit (forge-hebrew-ui's languages/) has its own words for the English side
    const other = (english) => {
        const text = window.hebrewUi && window.hebrewUi.text ? window.hebrewUi.text(english) : undefined;
        return text === undefined ? english : text;
    };

    function t(key, values) {
        let text = hebrew() ? TEXT[key][1] : other(TEXT[key][0]);
        for (const [k, v] of Object.entries(values || {})) text = text.replace(`{${k}}`, v);
        return text;
    }

    function targetLabel(target, label) {
        return hebrew() ? (TARGET_HE[target] || label) : label;
    }

    function textarea(field) {
        const el = gradioApp().getElementById(field);
        return el && el.querySelector("textarea");
    }

    async function call(path, body) {
        const r = await fetch(path, body === undefined ? {} : {
            method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body),
        });
        const data = await r.json().catch(() => ({}));
        if (r.status === 409) throw new Error(t("busy"));
        if (!r.ok || data.error) throw new Error(t("failed") + (data.error || data.detail || r.statusText));
        return data;
    }

    function make(tag, attrs, html) {
        const el = document.createElement(tag);
        for (const [k, v] of Object.entries(attrs || {})) el.setAttribute(k, v);
        if (html) el.innerHTML = html;
        return el;
    }

    // ------------------------------------------------------------------ the bar under a field

    function bar(field) {
        let el = document.getElementById(`pw_bar_${field}`);
        if (el) return el;
        el = make("div", {id: `pw_bar_${field}`, class: "pw-bar hebrew-ui-skip"});
        el.innerHTML =
            '<div class="pw-row">' +
            '<span class="pw-status"></span>' +
            `<button type="button" class="pw-btn pw-like" data-tip="like">${THUMB}</button>` +
            `<button type="button" class="pw-btn pw-dislike" data-tip="dislike">${THUMB}</button>` +
            '<button type="button" class="pw-btn" data-tip="again" data-act="again">🔄</button>' +
            '<button type="button" class="pw-btn" data-tip="undo" data-act="undo">↩</button>' +
            '<button type="button" class="pw-btn pw-lib" data-tip="library" data-act="library">📚 <span class="pw-count"></span></button>' +
            '<button type="button" class="pw-btn" data-tip="close" data-act="close">✕</button>' +
            '</div>' +
            '<div class="pw-meaning" hidden><span class="pw-meaning-label"></span> <span class="pw-meaning-text" dir="ltr"></span></div>' +
            '<div class="pw-why" hidden><input type="text" class="pw-why-input"></div>';
        el.querySelector(".pw-like").addEventListener("click", () => rate(field, 1));
        el.querySelector(".pw-dislike").addEventListener("click", () => rate(field, -1));
        el.querySelector('[data-act="again"]').addEventListener("click", () => write(field, true));
        el.querySelector('[data-act="undo"]').addEventListener("click", () => undo(field));
        el.querySelector('[data-act="library"]').addEventListener("click", openLibrary);
        el.querySelector('[data-act="close"]').addEventListener("click", () => el.remove());
        el.querySelector(".pw-why-input").addEventListener("keydown", (e) => {
            if (e.key === "Enter") {
                e.preventDefault();
                saveNote(field, e.target.value);
            }
        });
        // inside the field's block, under the textarea: next to it, a flex row (txt2img's prompt row) puts it beside
        gradioApp().getElementById(field).appendChild(el);
        paint(el);
        return el;
    }

    // tooltips and placeholders in the language the page is in now
    function paint(el) {
        el.dir = hebrew() ? "rtl" : "ltr";
        el.querySelectorAll("[data-tip]").forEach((b) => (b.title = t(b.dataset.tip)));
        el.querySelector(".pw-meaning-label").textContent = t("meaning");
        el.querySelector(".pw-meaning").title = t("meaningTip");
        const input = el.querySelector(".pw-why-input");
        if (input) input.placeholder = t("why");
    }

    function say(field, html, kind) {
        const el = bar(field);
        const status = el.querySelector(".pw-status");
        status.innerHTML = html;
        status.className = "pw-status" + (kind ? ` pw-${kind}` : "");
    }

    function writtenStatus(field) {
        const s = state[field];
        if (!s) return "";
        let text = t("written", {target: `<b>${targetLabel(s.target, s.label)}</b>`, s: s.seconds});
        if (s.examples > 0) text += t("fromLiked", {n: s.examples});
        return text;
    }

    function setCounts(counts) {
        if (!counts) return;
        document.querySelectorAll(".pw-count").forEach((c) => (c.textContent = counts.liked + counts.disliked || ""));
    }

    function setBusy(field, busy) {
        const button = document.getElementById(`pw_write_${field}`);
        if (button) button.classList.toggle("pw-busy", busy);
        const el = document.getElementById(`pw_bar_${field}`);
        if (el) el.querySelectorAll(".pw-btn").forEach((b) => (b.disabled = busy && b.dataset.act !== "close"));
    }

    function showRated(field) {
        const el = bar(field);
        const rated = state[field] ? state[field].rated : 0;
        el.querySelector(".pw-like").classList.toggle("pw-on", rated > 0);
        el.querySelector(".pw-dislike").classList.toggle("pw-on", rated < 0);
    }

    // ------------------------------------------------------------------ actions

    async function write(field, again) {
        const ta = textarea(field);
        if (!ta) return;
        let s = state[field];
        // 🌐 on an untouched answer, 🔄 and 👎 write the same idea again; otherwise the box holds a new idea
        if (s && ta.value.trim() === s.written) again = true;
        const source = again && s ? s.source : ta.value.trim();
        if (!source) {
            say(field, t("empty"), "error");
            return;
        }
        if (!(again && s)) bar(field).querySelector(".pw-why").hidden = true;
        say(field, t("writing"), "working");
        setBusy(field, true);
        try {
            // Video Studio's sub-tab, and "ltx:" before it when its video model is an LTX one (LTX reads a long
            // chronological paragraph with the sound in it; Wan a short motion paragraph)
            let video = typeof videoStudioMode === "function" ? videoStudioMode() : "";
            const model = gradioApp().querySelector("#vs_model input");
            if ((field.startsWith("vs_") || field === "ch_video") && model && /\bLTX\b/.test(model.value)) {
                video = "ltx:" + (field === "ch_video" ? "ingredients" : video);
            }
            const d = await call("/translator/write", {
                text: source, field, video_mode: video, again: !!again, seed: Math.floor(Math.random() * 1e9),
            });
            const keep = s && again ? s.dislikedId : null;
            state[field] = {source, written: d.prompt, meaning: d.meaning || "", target: d.target, label: d.target_label,
                seconds: d.seconds, examples: d.examples, id: null, rated: 0, dislikedId: keep};
            ta.value = d.prompt;
            updateInput(ta);
            const meaning = bar(field).querySelector(".pw-meaning");
            meaning.hidden = !d.meaning;
            meaning.querySelector(".pw-meaning-text").textContent = d.meaning || "";
            say(field, writtenStatus(field));
            setCounts(d.counts);
            return true;
        } catch (e) {
            say(field, e.message, "error");
            return false;
        } finally {
            setBusy(field, false);
            showRated(field);
        }
    }

    async function rate(field, rating) {
        const s = state[field];
        const ta = textarea(field);
        if (!s || !ta) return;
        try {
            const d = await call("/translator/rate", {
                source: s.source, target: s.target, written: s.written,
                // 👍 keeps what is in the box now (corrections are the best lesson); 👎 is about what was written
                final: rating > 0 ? ta.value.trim() : s.written, rating, meaning: s.meaning, id: s.id || "",
            });
            s.id = d.entry.id;
            s.rated = rating;
            setCounts(d.counts);
            showRated(field);
            if (rating > 0) {
                say(field, t("liked"), "ok");
                return;
            }
            s.dislikedId = d.entry.id;
            const why = bar(field).querySelector(".pw-why");
            why.hidden = false;
            why.querySelector("input").value = d.entry.note || "";
            say(field, t("disliked"), "working");
            if (await write(field, true)) say(field, `${t("dislikedDone")} ${writtenStatus(field)}`);
        } catch (e) {
            say(field, e.message, "error");
        }
    }

    async function saveNote(field, note) {
        const s = state[field];
        if (!s || !s.dislikedId) return;
        try {
            await call("/translator/note", {id: s.dislikedId, note});
            say(field, t("noteSaved"), "ok");
            bar(field).querySelector(".pw-why").hidden = true;
        } catch (e) {
            say(field, e.message, "error");
        }
    }

    function undo(field) {
        const s = state[field];
        const ta = textarea(field);
        if (!s || !ta) return;
        ta.value = s.source;
        updateInput(ta);
        say(field, t("undone"));
    }

    // ------------------------------------------------------------------ what was learned

    function escape(text) {
        const d = document.createElement("div");
        d.textContent = text || "";
        return d.innerHTML;
    }

    async function openLibrary() {
        document.getElementById("pw_library")?.remove();
        const overlay = make("div", {id: "pw_library", class: "pw-overlay hebrew-ui-skip"});
        overlay.dir = hebrew() ? "rtl" : "ltr";
        overlay.innerHTML = `<div class="pw-panel"><div class="pw-head"><b>${t("title")}</b>` +
            '<span class="pw-head-counts"></span><button type="button" class="pw-btn pw-x">✕</button></div>' +
            '<div class="pw-list"></div></div>';
        overlay.addEventListener("click", (e) => {
            if (e.target === overlay || e.target.closest(".pw-x")) overlay.remove();
        });
        document.body.appendChild(overlay);
        const d = await call("/translator/learned").catch((e) => ({error: e.message}));
        const list = overlay.querySelector(".pw-list");
        if (d.error) {
            list.textContent = d.error;
            return;
        }
        overlay.querySelector(".pw-head-counts").textContent = t("counts", d.counts);
        if (!d.entries.length) {
            list.textContent = t("nothing");
            return;
        }
        for (const e of d.entries) {
            const row = make("div", {class: "pw-item"});
            row.innerHTML =
                `<span class="pw-mark ${e.rating > 0 ? "pw-like" : "pw-dislike"}">${THUMB}</span>` +
                '<div class="pw-texts">' +
                `<div class="pw-meta">${escape(targetLabel(e.target, d.targets[e.target] || e.target))} · ${escape(e.time)}</div>` +
                `<div class="pw-src" dir="rtl">${escape(e.source)}</div>` +
                (e.meaning ? `<div class="pw-meta" dir="ltr">≈ ${escape(e.meaning)}</div>` : "") +
                `<div class="pw-out" dir="ltr">${escape(e.final)}</div>` +
                (e.note ? `<div class="pw-note">${t("note")}${escape(e.note)}</div>` : "") +
                `</div><button type="button" class="pw-btn pw-forget" title="${t("forget")}">✕</button>`;
            row.querySelector(".pw-forget").addEventListener("click", async () => {
                const r = await call("/translator/forget", {id: e.id}).catch(() => null);
                if (r) {
                    row.remove();
                    overlay.querySelector(".pw-head-counts").textContent = t("counts", r.counts);
                    setCounts(r.counts);
                }
            });
            list.appendChild(row);
        }
    }

    // ------------------------------------------------------------------ the 🌐 buttons

    function attach() {
        for (const field of FIELDS) {
            const el = gradioApp().getElementById(field);
            if (!el || document.getElementById(`pw_write_${field}`) || !el.querySelector("textarea")) continue;
            // the 🌐 is drawn by style.css: as text, A1111's hints.js would give it panorama viewer's "🌐" tooltip
            const button = make("button", {type: "button", id: `pw_write_${field}`, class: "pw-write hebrew-ui-skip"});
            button.dataset.tip = "write";
            button.title = t("write");
            button.addEventListener("click", (e) => {
                e.preventDefault();
                write(field, false);
            });
            // in the textarea's own wrapper, so the bar below does not move it
            const holder = el.querySelector("textarea").parentElement;
            holder.classList.add("pw-field");
            holder.appendChild(button);
        }
    }

    onUiLoaded(() => {
        attach();
        // parts of Video Studio and other tabs may be drawn later
        setTimeout(attach, 3000);
        // the language switch of forge-hebrew-ui: tooltips and the bar follow it
        new MutationObserver(() => {
            document.querySelectorAll(".pw-write").forEach((b) => (b.title = t("write")));
            document.querySelectorAll(".pw-bar").forEach((el) => {
                paint(el);
                const field = el.id.replace("pw_bar_", "");
                if (state[field] && !el.querySelector(".pw-status").className.includes("pw-error")) {
                    say(field, writtenStatus(field));
                }
            });
        }).observe(document.documentElement, {attributes: true, attributeFilter: ["class"]});
    });
})();

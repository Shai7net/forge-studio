// Video Studio: Generate reads the open sub-tab here, at the moment of the click. A queued `.select` event can be
// dropped on a heavy page and leave the wrong mode behind (it happened to Extras, 2026-10-07).
function videoStudioOpenTab() {
    const tabs = gradioApp().querySelector("#vs_mode_tabs");
    if (!tabs) return null;
    const open = Array.from(tabs.querySelectorAll(":scope > .tabitem")).find((item) => item.style.display !== "none");
    return open ? open.id : null;
}

function videoStudioMode() {
    return {vs_tab_t2v: "t2v", vs_tab_i2v: "i2v", vs_tab_ingredients: "ingredients",
            vs_tab_replace: "replace", vs_tab_ltx_audio: "ltx_audio"}[videoStudioOpenTab()] || "t2v";
}

function videoStudioSubmit(...args) {
    args[0] = videoStudioMode();
    return args;
}

// Metadata callbacks use the visible tab, just as Generate does, including rapid tab switches.
function videoStudioMediaInputs(...args) {
    args[0] = videoStudioMode();
    videoStudioSyncControls();
    return args;
}

// Opens a tab of a gr.Tabs by its id. The tab buttons are not a safe way in: the navigation bookmarks extension
// moves the top-level ones into folders, so their order says nothing.
function videoStudioOpen(tabsId, id) {
    const tabs = (window.gradio_config && window.gradio_config.components || []).find(
        (c) => c.type === "tabs" && c.props && c.props.elem_id === tabsId);
    if (!tabs || !tabs.instance) return false;
    tabs.props.selected = id;
    tabs.instance.$set({selected: id});
    return true;
}

function videoStudioToImageTab(...args) {
    videoStudioOpen("vs_mode_tabs", "i2v");
    for (const ms of [50, 300]) setTimeout(videoStudioSyncControls, ms);
    return args;
}

// 🎬 under the txt2img and img2img results: the picture goes into Image to Video (videostudio/ui.py sends it).
function switch_to_video_studio() {
    videoStudioOpen("tabs", "video_studio");
    videoStudioOpen("vs_mode_tabs", "i2v");
    for (const ms of [50, 300]) setTimeout(videoStudioSyncControls, ms);
    return Array.from(arguments);
}

// Image to Video's pictures (videostudio/ui.py builds six and shows as many as "Pictures in use" says). These run in
// the page and return Gradio updates; {__type__: "update"} alone leaves a component as it is.
const VS_PICTURES = 6;
const vsKeep = () => ({__type__: "update"});

function vsCount(n) {
    return Math.max(1, Math.min(VS_PICTURES, parseInt(n) || 1));
}

function videoStudioPictureLayout(count) {
    const n = vsCount(count);
    const label = (k) => (n === 1 ? "Start picture" : n === 2 && k < 2 ? ["First frame", "Last frame"][k] : `Picture ${k + 1}`);
    const note = n === 1
        ? "One picture: the video starts from it. ➕ Last frame makes it end on a picture of yours too."
        : n === 2
            ? "The video goes from the first picture to the last. More pictures make a longer video: one part for each pair, joined into one."
            : `${n} pictures: ${n - 1} parts, one for each pair, joined into one video. The length below is per part; each part can have its own description.`;
    const out = [];
    // two pictures side by side as before; from three on they are smaller, so the parts below stay in view
    for (let k = 0; k < VS_PICTURES; k++) out.push({__type__: "update", visible: k < n, label: label(k), height: n <= 2 ? 360 : 240});
    for (let r = 0; r < VS_PICTURES / 2; r++) out.push({__type__: "update", visible: 2 * r < n});
    out.push({__type__: "update", value: n === 1 ? "➕ Last frame" : "➕ Another picture (a longer video)", visible: n < VS_PICTURES});
    out.push({__type__: "update", visible: n > 1});
    out.push({__type__: "update", visible: n > 1});
    out.push({__type__: "update", value: note});
    out.push({__type__: "update", visible: n > 2});
    for (let k = 0; k < VS_PICTURES - 1; k++) out.push({__type__: "update", visible: k < n - 1});
    out.push({__type__: "update", label: n > 2 ? "Length of each part (seconds)" : "Length (seconds)"});
    for (const ms of [100, 600]) setTimeout(videoStudioTranslate, ms);
    return out;
}

function videoStudioAddPicture(count) {
    return Math.min(VS_PICTURES, vsCount(count) + 1);
}

// the last picture and the description of the part that ended on it are cleared
function videoStudioRemovePicture(count) {
    const n = vsCount(count);
    if (n === 1) return Array(1 + VS_PICTURES + VS_PICTURES - 1).fill(0).map(vsKeep);
    const out = [n - 1];
    for (let k = 0; k < VS_PICTURES; k++) out.push(k === n - 1 ? {__type__: "update", value: null} : vsKeep());
    for (let k = 0; k < VS_PICTURES - 1; k++) out.push(k === n - 2 ? {__type__: "update", value: ""} : vsKeep());
    return out;
}

// a part's description stays with its two pictures, which now come in the other order
// (Gradio passes the inputs and then the outputs' current values; only the inputs are read)
function videoStudioReversePictures(count, ...values) {
    const n = vsCount(count);
    const pictures = values.slice(0, VS_PICTURES);
    const texts = values.slice(VS_PICTURES, 2 * VS_PICTURES - 1);
    const order = (list, used) => list.slice(0, used).reverse().concat(list.slice(used));
    return order(pictures, n).concat(order(texts, n - 1));
}

// The pictures' labels and the ➕ button change their text in place; the Hebrew interface translates new elements
// only, so it is asked to look at Image to Video again.
function videoStudioTranslate() {
    if (!(window.hebrewUi && window.hebrewUi.refresh)) return [];
    for (const id of ["vs_tab_i2v", "vs_seconds"]) window.hebrewUi.refresh(gradioApp().getElementById(id));
    return [];
}

// Replace a character has its own length and quality: the shared ones are hidden while it is open.
function videoStudioSyncControls() {
    const main = gradioApp().getElementById('vs_main'), mode = videoStudioMode();
    if (main && main.dataset.vsMode !== mode) main.dataset.vsMode = mode;
}

document.addEventListener("click", (e) => {
    if (!(e.target.closest && e.target.closest("#vs_mode_tabs .tab-nav button"))) return;
    // Gradio marks the new tab selected only after its own re-render
    for (const ms of [50, 300, 1000]) setTimeout(videoStudioSyncControls, ms);
});

// Quality starts the sub-tab bar and the Shape card closes it (on a line of its own): the choices of every video
// right above the prompt.
function videoStudioPlaceModel() {
    const quality = gradioApp().querySelector("#vs_quality");
    const card = gradioApp().querySelector("#vs_create");
    const nav = gradioApp().querySelector("#vs_mode_tabs > .tab-nav");
    if (!quality || !card || !nav) return false;
    if (quality.parentElement !== nav) nav.prepend(quality);
    if (card.parentElement !== nav) nav.append(card);
    return true;
}

// Generate and its outcome right under the window (Forge Studio's timeline drawer comes in after the window)
function videoStudioOrderSide() {
    const stage = gradioApp().getElementById("vs_stage");
    const go = gradioApp().getElementById("vs_go_row");
    const status = gradioApp().getElementById("vs_status");
    if (!stage || !go || !status || go.parentElement !== stage.parentElement) return;
    if (stage.nextElementSibling !== go) stage.after(go);
    if (go.nextElementSibling !== status) go.after(status);
}

// The video model chosen now (a row of picture buttons; the prompt writer and Characters ask for it too).
function videoStudioModel() {
    const on = gradioApp().querySelector("#vs_model input:checked");
    return on ? on.value : "";
}

// The model buttons: a picture and a short name on each choice, in the order Shai laid them out (2026-11-27).
const VS_MODEL_LOOK = [
    [/MiniMax/i, "minimax", "MiniMax-H3", "8 GB"],
    [/LTX.*Q3/i, "ltx_q3", "LTX 2.3", "Q3 · 8 GB"],
    [/LTX.*Q2/i, "ltx_q2", "LTX 2.3", "Q2 · 6 GB"],
    [/LTX.*Q4/i, "ltx_q4", "LTX 2.3", "Q4 · 12 GB+"],
    [/Wan.*Q3/i, "wan_q3", "Wan 2.2", "Q3"],
    [/Wan.*Q4/i, "wan_q4", "Wan 2.2", "Q4 · sharper"],
];
function videoStudioModelButtons() {
    const script = document.querySelector('script[src*="video-studio.js"]');
    const base = script ? script.src.split("?")[0].replace(/javascript\/video-studio\.js$/, "assets/models/") : "";
    let installed = null;
    try { installed = JSON.parse(gradioApp().querySelector("#vs_installed").dataset.installed); } catch (e) { /* unknown */ }
    for (const input of gradioApp().querySelectorAll("#vs_model input[type=radio]")) {
        const label = input.closest("label");
        if (!label || label.querySelector(".vs-model-pic")) continue;
        const k = VS_MODEL_LOOK.findIndex(([re]) => re.test(input.value));
        const [, file, name, note] = k >= 0 ? VS_MODEL_LOOK[k] : [null, null, input.value, ""];
        label.style.order = k >= 0 ? k : 99;
        label.title = input.value;
        const pic = document.createElement("span");
        pic.className = "vs-model-pic";
        if (file) pic.style.backgroundImage = `url("${base}${file}.jpg")`;
        const cap = document.createElement("span");
        cap.className = "vs-model-cap hebrew-ui-skip";
        cap.innerHTML = "<b></b><small></small>";
        cap.querySelector("b").textContent = name;
        cap.querySelector("small").textContent = note;
        label.prepend(pic);
        label.append(cap);
        if (installed && !installed.includes(input.value)) {
            label.classList.add("vs-not-installed");
            const badge = document.createElement("span");
            badge.className = "vs-model-get";
            badge.textContent = "⬇";
            badge.title = "Not downloaded yet: Models → Download and set up this model";
            pic.append(badge);
        }
    }
}

// While a video is made the window shows the frog, the share done and the time left, over the picture made so far.
function videoStudioLoader() {
    const stage = gradioApp().getElementById("vs_stage");
    const box = gradioApp().querySelector("#vs_status .vs-status");
    if (!stage) return;
    let el = stage.querySelector(".vs-loader");
    if (!el) {
        el = document.createElement("div");
        el.className = "vs-loader hebrew-ui-skip";
        el.innerHTML = '<div class="vs-loader-frog">🐸</div><div class="vs-loader-pct"></div><div class="vs-loader-left"></div><div class="vs-loader-stage"></div>';
        stage.append(el);
    }
    const hide = stage.dataset.waiting !== "1";
    if (el.hidden !== hide) el.hidden = hide;
    if (!box || hide) return;
    // only a changed text is written: a write is a change the page's observer sees, and it calls this again
    const put = (sel, text) => { const n = el.querySelector(sel); if (n.textContent !== text) n.textContent = text; };
    const he = document.documentElement.classList.contains("hebrew-ui-active");
    const pct = parseInt(box.dataset.pct || "0", 10);
    put(".vs-loader-pct", pct + "%");
    const left = box.dataset.left;
    put(".vs-loader-left", left
        ? (he ? `נשארו בערך ${left}` : `about ${left} left`)
        : (he ? "מחשב כמה זמן נשאר…" : "working out the time left…"));
    const label = box.dataset.label || "";
    const t = window.hebrewUiDictionary && window.hebrewUiDictionary.texts;
    put(".vs-loader-stage", he && t && t[label] ? t[label] : label);
}

// Shape: a drawn rectangle of each shape on its choice (the choice's text stays under it, translated or not).
const VS_SHAPES = {"9:16 portrait": [9, 16], "16:9 landscape": [16, 9], "1:1 square": [1, 1], "4:5 portrait": [4, 5], "3:4 portrait": [3, 4]};
function videoStudioShapeIcons() {
    for (const input of gradioApp().querySelectorAll("#vs_shape input[type=radio]")) {
        const label = input.closest("label");
        if (!label || label.querySelector(".vs-shape-icon")) continue;
        const [w, h] = VS_SHAPES[input.value] || [1, 1];
        const k = 22 / Math.max(w, h);
        const icon = document.createElement("span");
        icon.className = "vs-shape-icon";
        icon.style.width = Math.round(w * k) + "px";
        icon.style.height = Math.round(h * k) + "px";
        label.prepend(icon);
    }
}

// While a video is made: Stop in Generate's place, and the stage shows the picture made so far; then the video.
const VS_IDLE = ["idle", "done", "error", "cancelled"];
let vsClicked = 0, vsVideoBefore = null;   // the video shown when Generate was pressed
function videoStudioState() {
    const main = gradioApp().getElementById("vs_main");
    const box = gradioApp().querySelector("#vs_status .vs-status");
    if (!main || !box) return;
    const stage = box.dataset.stage || "idle";
    // right after the click the status has not changed yet: count it as working for a few seconds
    const running = !VS_IDLE.includes(stage) || (Date.now() - vsClicked < 4000 && stage !== "error");
    main.classList.toggle("vs-running", running);
    const picture = gradioApp().querySelector("#vs_preview img");
    const hasPicture = !!(picture && picture.getAttribute("src"));
    const video = gradioApp().querySelector("#vs_video video");
    const src = video ? video.getAttribute("src") || "" : "";
    const stageBox = gradioApp().getElementById("vs_stage");
    // the picture until this run's video arrives (the sound is still being added then; the video is the result)
    const newVideo = vsVideoBefore !== null && src && src !== vsVideoBefore;
    if (stageBox) stageBox.dataset.show = running && hasPicture && !newVideo ? "picture" : "video";
    if (!running) vsVideoBefore = null;
    if (stageBox) stageBox.classList.toggle("vs-has-video", !!(video && video.getAttribute("src")));
    // the frog waits with the user until this run's video is there
    if (stageBox) stageBox.dataset.waiting = running && !newVideo ? "1" : "";
    videoStudioLoader();
}

document.addEventListener("click", (e) => {
    const b = e.target.closest && e.target.closest("#vs_generate, #vs_refine_go, #vs_sound_go");
    if (!b) return;
    vsClicked = Date.now();
    const video = gradioApp().querySelector("#vs_video video");
    vsVideoBefore = video ? video.getAttribute("src") || "" : "";
    videoStudioState();
}, true);

onUiLoaded(() => {
    const ready = () => videoStudioPlaceModel();
    if (!ready()) setTimeout(ready, 1500);
    videoStudioShapeIcons();
    videoStudioModelButtons();
    const root = gradioApp().getElementById("vs_main");
    if (root) new MutationObserver((records) => {
        // the loader's own text is not news (it would call itself for ever)
        if (records.every((r) => r.target.closest && r.target.closest(".vs-loader"))) return;
        videoStudioShapeIcons(); videoStudioModelButtons(); videoStudioOrderSide(); videoStudioState();
    })
        .observe(root, {subtree: true, childList: true, attributes: true, attributeFilter: ["data-stage", "src"]});
    setInterval(videoStudioState, 1000);
    videoStudioState();
});

// The 🎬 button's own tooltip still names the SVD tab.
onUiLoaded(() => {
    for (const button of gradioApp().querySelectorAll("#txt2img_send_to_svd, #img2img_send_to_svd")) {
        button.title = "Send the picture to Video Studio: Image to Video";
    }
});

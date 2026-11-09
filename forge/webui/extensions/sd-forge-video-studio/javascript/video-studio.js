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
            vs_tab_replace: "replace"}[videoStudioOpenTab()] || "t2v";
}

function videoStudioSubmit(...args) {
    args[0] = videoStudioMode();
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
    const replace = videoStudioMode() === "replace";
    for (const id of ["vs_quality", "vs_seconds"]) {
        const el = gradioApp().getElementById(id);
        if (el) el.style.display = replace ? "none" : "";
    }
}

document.addEventListener("click", (e) => {
    if (!(e.target.closest && e.target.closest("#vs_mode_tabs .tab-nav button"))) return;
    // Gradio marks the new tab selected only after its own re-render
    for (const ms of [50, 300, 1000]) setTimeout(videoStudioSyncControls, ms);
});

// Shape and the picture sampler serve every sub-tab, so they live in the sub-tab bar itself, after the buttons: on
// the same line when there is room, on a line of their own when not (the bar wraps).
function videoStudioPlaceSharedRow() {
    const row = gradioApp().querySelector("#vs_shared_row");
    const nav = gradioApp().querySelector("#vs_mode_tabs > .tab-nav");
    if (!row || !nav) return false;
    if (row.parentElement !== nav) nav.appendChild(row);
    return true;
}

onUiLoaded(() => {
    if (!videoStudioPlaceSharedRow()) setTimeout(videoStudioPlaceSharedRow, 1500);
});

// The 🎬 button's own tooltip still names the SVD tab.
onUiLoaded(() => {
    for (const button of gradioApp().querySelectorAll("#txt2img_send_to_svd, #img2img_send_to_svd")) {
        button.title = "Send the picture to Video Studio: Image to Video";
    }
});

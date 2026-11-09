// Characters: the page side of the tab (forgecharacters/ui.py).

function charactersHebrew() {
    return document.documentElement.classList.contains("hebrew-ui-active");
}

// A click on a character in the library: its place goes to the hidden #ch_pick box, which loads it (Gradio 3.41's
// gallery does not send "select" again for the item it still shows as chosen).
document.addEventListener("click", (e) => {
    const item = e.target.closest && e.target.closest("#ch_library .thumbnail-item");
    if (!item) return;
    const items = Array.from(gradioApp().querySelectorAll("#ch_library .thumbnail-item"));
    const box = gradioApp().querySelector("#ch_pick textarea, #ch_pick input");
    const go = gradioApp().querySelector("#ch_pick_go");
    if (!box || !go) return;
    // Forge's own way for the page to start an event: the box is set with updateInput, then a hidden button is
    // pressed (a "change" of a hidden box was dropped, 2026-11-09)
    box.value = `${items.indexOf(item)}:${Date.now()}`;
    updateInput(box);
    go.click();
}, true);

// 🗑 asks first; the character goes to output/characters/_trash, nothing is erased.
// (the chosen character is a gr.State, which reaches the page as null: its name is read from the Name box)
function charactersConfirmDelete(cid, ok, show, query) {
    const field = gradioApp().querySelector("#ch_sel_name textarea, #ch_sel_name input");
    const name = field ? field.value.trim() : "";
    if (!name) return [cid, false, show, query];
    const text = charactersHebrew()
        ? `למחוק את "${name}"? הדמות תעבור לתיקייה _trash (שום דבר לא נמחק לגמרי).`
        : `Delete "${name}"? It moves to the _trash folder (nothing is erased).`;
    return [cid, confirm(text), show, query];
}

// Opens a tab of a gr.Tabs by its id (as Video Studio does: the top tab buttons may be moved into folders).
function charactersOpenTab(tabsId, id) {
    const tabs = (window.gradio_config && window.gradio_config.components || []).find(
        (c) => c.type === "tabs" && c.props && c.props.elem_id === tabsId);
    if (!tabs || !tabs.instance) return false;
    tabs.props.selected = id;
    tabs.instance.$set({selected: id});
    return true;
}

// 🌍: the character's 3D model, kept in Simulation 360's "My objects" (forgecharacters/model3d.py), is stood in
// the world that is open there; with no world open it waits in "Plain shapes and my objects".
async function charactersSendToSim360(payload) {
    if (!payload) return [];
    let data;
    try {
        data = JSON.parse(payload);
    } catch (e) {
        return [];
    }
    const he = charactersHebrew();
    const E = window.Sim360 && window.Sim360.engine;
    if (!E) {
        alert(he ? "Simulation 360 לא מותקן." : "Simulation 360 is not installed.");
        return [];
    }
    charactersOpenTab("tabs", "sim360");
    try {
        await E.library();
        const world = E.name && E.world ? E.world() : null;
        if (world && world.file && !(E.jobs && E.jobs.size)) {
            await E.useKept({id: data.id, name: data.name});
            if (E.say) E.say(he ? `"${data.name}" עומד בעולם` : `"${data.name}" stands in the world`);
        } else {
            E.emit("objects");
            alert(he ? `"${data.name}" נשמר ב"האובייקטים שלי". פתחו פרויקט ועולם ב-Simulation 360 ולחצו עליו ב"Plain shapes and my objects".`
                     : `"${data.name}" is in "My objects". Open a project and a world in Simulation 360 and click it under "Plain shapes and my objects".`);
        }
    } catch (e) {
        alert((he ? "ההעברה ל-Simulation 360 נכשלה: " : "Could not send it to Simulation 360: ") + (e.message || e));
    }
    return [];
}

onUiLoaded(() => {
    const button = gradioApp().querySelector("#ch_to_sim360");
    if (button) {
        button.title = charactersHebrew() ? "שליחת המודל התלת-ממדי ל-Simulation 360" : "Send the 3D model to Simulation 360";
    }
});

// 🎬 Send to Ingredients to Video: the sheet goes into an ingredient slot of Video Studio, the Wan prompt into its
// prompt, and the "first frame by FLUX 3 Klein" way is chosen (the one that keeps a character's face and style).
async function charactersSendToVideo(payload) {
    if (!payload) return [];
    let data;
    try {
        data = JSON.parse(payload);
    } catch (e) {
        return [];
    }
    const app = gradioApp();
    if (typeof videoStudioOpen !== "function" || !app.querySelector("#vs_ingredient1")) {
        alert(charactersHebrew() ? "Video Studio לא מותקן." : "Video Studio is not installed.");
        return [];
    }
    videoStudioOpen("tabs", "video_studio");
    videoStudioOpen("vs_mode_tabs", "ingredients");
    if (typeof videoStudioSyncControls === "function") setTimeout(videoStudioSyncControls, 300);

    // the slot: the one asked for, or the first without a picture
    let slot = parseInt(data.slot, 10);
    if (!(slot >= 1 && slot <= 4)) {
        slot = 1;
        for (let k = 1; k <= 4; k++) {
            if (!app.querySelector(`#vs_ingredient${k} img`)) {
                slot = k;
                break;
            }
        }
    }
    try {
        const res = await fetch("file=" + data.sheet);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const blob = await res.blob();
        const input = app.querySelector(`#vs_ingredient${slot} input[type=file]`);
        if (!input) throw new Error("no upload field in the slot");
        const dt = new DataTransfer();
        dt.items.add(new File([blob], `${data.name || "character"}.png`, {type: blob.type || "image/png"}));
        input.files = dt.files;
        input.dispatchEvent(new Event("change", {bubbles: true}));
    } catch (e) {
        alert((charactersHebrew() ? "שליחת הגיליון נכשלה: " : "Could not send the sheet: ") + e.message);
        return [];
    }
    const prompt = app.querySelector("#vs_prompt textarea");
    if (prompt && data.prompt) {
        prompt.value = data.prompt;
        prompt.dispatchEvent(new Event("input", {bubbles: true}));
    }
    const way = app.querySelector('#vs_ingredients_method input[value^="First frame"]');
    if (way && !way.checked) way.click();
    return [];
}

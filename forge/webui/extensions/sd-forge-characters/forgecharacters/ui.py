"""The Characters tab: create (words or a reference picture -> character -> sheet), the library, and the video."""

import html
import json
import os
import random
import shutil
import time

import gradio as gr

from . import BUILTIN_TEMPLATE, LIBRARY_DIR, WEBUI_DIR, library, model3d, sheet, writer

ALL = "All projects"
SLOTS = ["First empty", "1", "2", "3", "4"]
WORK_DIR = os.path.join(LIBRARY_DIR, "_work")  # sheets drawn but not saved yet (cleared at each Create)


def status(kind, text):
    color = {"ok": "#22c55e", "error": "#ef4444", "busy": "#f59e0b"}.get(kind, "#9ca3af")
    return (f'<div class="ch-status" style="border-inline-start:4px solid {color}">'
            f'{html.escape(text).replace(chr(10), "<br>")}</div>')


def project_choices():
    return library.projects(LIBRARY_DIR) or [library.DEFAULT_PROJECT]


def gallery_items(project, query):
    items = library.characters(LIBRARY_DIR, None if project in (None, "", ALL) else project, query)
    return [(c["sheet"], f"{c.get('name', '')} · {c['id'].split('/')[0]}") for c in items], [c["id"] for c in items]


def refresh_library(project, query):
    pictures, ids = gallery_items(project, query)
    choices = project_choices()
    return (gr.update(value=pictures), ids, gr.update(choices=[ALL] + choices),
            gr.update(choices=choices), gr.update(choices=choices))


# ---------------------------------------------------------------------- create

def create(text, reference, template, count, seed, sheet_text_value):
    """Generator: (character box, status, results gallery, results, name)."""
    keep = gr.update()
    text = (text or "").strip()
    if not text and not reference:
        yield keep, status("error", "Describe the character (any language), or give a reference picture."), keep, keep, keep
        return
    template = template or sheet.default_template()
    idea = text  # the user's own words, kept with the character
    character = sheet.parse_character(text)
    if character is None and not text:
        # only a picture: the writer cannot see it, so the picture itself is the character
        character = dict(sheet.FROM_REFERENCE)
        text = json.dumps(character, ensure_ascii=False, indent=1)
    if character is None:
        yield keep, status("busy", "The prompt writer is writing the character…"), keep, keep, keep
        try:
            answer = writer.write(text, "ch_character")
        except writer.WriterError as e:
            yield keep, status("error", str(e)), keep, keep, keep
            return
        character = sheet.parse_character(answer.get("prompt", ""))
        if character is None:
            yield keep, status("error", "The writer gave no usable character. Press Create again, or write it in "
                                        "English."), keep, keep, keep
            return
        text = json.dumps(character, ensure_ascii=False, indent=1)
    if sheet.flux3_glue() is None:
        yield (text, status("error", "Select FLUX 3 Klein at the top of the page: it draws the character sheets. "
                                     "The character is written above; press Create again after selecting it."),
               keep, keep, character.get("name") or keep)
        return
    prompt = sheet.compose(character, bool(reference), sheet_text_value)
    count = max(1, min(4, int(count or 1)))
    first = int(seed) if seed is not None and int(seed) >= 0 else random.randint(0, 2 ** 31 - 1)
    shutil.rmtree(WORK_DIR, ignore_errors=True)
    os.makedirs(WORK_DIR, exist_ok=True)
    made = []
    from modules.call_queue import queue_lock
    yield text, status("busy", "Waiting for Forge to be free…"), keep, keep, character.get("name") or keep
    with queue_lock:
        for k in range(count):
            yield (keep, status("busy", f"FLUX 3 Klein draws sheet {k + 1} of {count} (about a minute each)…"),
                   keep, keep, keep)
            try:
                picture = sheet.draw(prompt, template, reference, first + k)
            except Exception as e:  # noqa: BLE001 - shown in the tab
                yield keep, status("error", f"Drawing failed: {type(e).__name__}: {e}"), keep, keep, keep
                return
            path = os.path.join(WORK_DIR, f"sheet-{first + k}.png")
            picture.save(path)
            made.append({"path": path, "seed": first + k, "prompt": prompt, "character": character,
                         "idea": idea if sheet.parse_character(idea) is None else "", "template": template,
                         "reference": reference})
            yield (keep, keep, gr.update(value=[(m["path"], f"seed {m['seed']}") for m in made]), made, keep)
    yield (keep, status("ok", f"{len(made)} sheet{'s' if len(made) > 1 else ''} drawn. Choose one, name it, choose a "
                              "project and save it."), keep, made, keep)


def choose_result(results, evt: gr.SelectData):
    return evt.index


def save_result(results, chosen, name, project, filter_project, query):
    """(status, library gallery, ids, filter choices, project choices, selected-project choices)."""
    keep = gr.update()
    if not results:
        return (status("error", "Create a sheet first."), keep, keep, keep, keep, keep)
    pick = results[min(int(chosen or 0), len(results) - 1)]
    name = (name or pick["character"].get("name") or "Character").strip()
    data = {"character": pick["character"], "idea": pick.get("idea", ""), "sheet_prompt": pick["prompt"],
            "seed": pick["seed"], "model": "FLUX 3 Klein (edit with reference)"}
    cid = library.save(LIBRARY_DIR, project or library.DEFAULT_PROJECT, name, pick["path"], data,
                       template=pick.get("template"), reference=pick.get("reference"))
    lib, ids, f_choices, p_choices, s_choices = refresh_library(filter_project, query)
    return (status("ok", f"Saved: {cid}"), lib, ids, f_choices, p_choices, s_choices)


# ---------------------------------------------------------------------- library

def select_character(ids, pick):
    """(selected id, info, name, project, sheet, 3D model). `pick` is "<index>:<time>" from javascript/characters.js: Gradio
    3.41's gallery does not send "select" again for the item it still shows as chosen (after a delete the next
    character lands there), so the page reports every click itself."""
    try:
        index = int(str(pick).split(":")[0])
    except ValueError:
        return "", "", gr.update(), gr.update(), gr.update(), gr.update()
    if not ids or not 0 <= index < len(ids):
        return "", "", gr.update(), gr.update(), gr.update(), gr.update()
    c = library.load(LIBRARY_DIR, ids[index])
    if c is None:
        return "", "This character is gone (refresh).", gr.update(), gr.update(), gr.update(), gr.update()
    ch = c.get("character", {})
    made = time.strftime("%Y-%m-%d %H:%M", time.localtime(c.get("created", 0)))
    info = (f"**{c.get('name', '')}** · {c['id'].split('/')[0]}  \n{ch.get('summary', '')}  \n"
            f"*{ch.get('style', '')}* · seed {c.get('seed', '')} · {made}")
    return c["id"], info, c.get("name", ""), c["id"].split("/")[0], c["sheet"], model_path(c)


def model_path(c):
    """The character's 3D model file, or None."""
    record = c.get("model3d") if c else None
    path = os.path.join(c["folder"], record.get("file") or model3d.MODEL_FILE) if record else None
    return path if path and os.path.isfile(path) else None


# ---------------------------------------------------------------------- 3D

def make_model(cid, detail):
    """Generator: (status, 3D model). Hunyuan3D-2 (Simulation 360's) makes the model of the sheet's front view."""
    keep = gr.update()
    if not cid:
        yield status("error", "Choose a character in the library first."), keep
        return
    ok, why = model3d.available()
    if not ok:
        yield status("error", why), keep
        return
    progress = {"stage": "starting", "share": 0.0}
    result, failure = [], []

    def work():
        try:
            result.append(model3d.make(cid, detail, lambda stage, share: progress.update(stage=stage, share=share)))
        except Exception as e:  # noqa: BLE001 - shown in the tab
            failure.append(e)

    import threading
    thread = threading.Thread(target=work, name="characters-3d-ui", daemon=True)
    thread.start()
    started = time.time()
    while thread.is_alive():
        share = progress.get("share") or 0.0
        yield (status("busy", f"3D model: {progress.get('stage') or '…'} · {int(share * 100)}% · "
                              f"{int(time.time() - started)} s (about three minutes)"), keep)
        thread.join(2.0)
    if failure:
        yield status("error", f"The 3D model could not be made: {failure[0]}"), keep
        return
    facts = result[0].get("facts") or {}
    c = library.load(LIBRARY_DIR, cid)
    yield (status("ok", f"3D model made: {facts.get('triangles', '?')} triangles in {facts.get('seconds', '?')} s. "
                        "🌍 sends it to Simulation 360."), model_path(c))


def send_to_sim360(cid):
    """(payload for the page, status): the model as a kept object of Simulation 360."""
    if not cid:
        return "", status("error", "Choose a character in the library first.")
    try:
        kept = model3d.keep_in_sim360(cid)
    except model3d.ModelError as e:
        return "", status("error", str(e))
    return (json.dumps(dict(kept, nonce=time.time())),
            status("ok", "Sent to Simulation 360 → Plain shapes and my objects."))


def rename_character(cid, name, project, filter_project, query):
    keep = gr.update()
    if not cid:
        return (status("error", "Choose a character in the library first."), cid, keep, keep, keep, keep, keep)
    new = library.update(LIBRARY_DIR, cid, name, project)
    lib, ids, f_choices, p_choices, s_choices = refresh_library(filter_project, query)
    return (status("ok", f"Saved: {new}"), new, lib, ids, f_choices, p_choices, s_choices)


def delete_character(cid, confirmed, filter_project, query):
    keep = gr.update()
    if not cid:
        return (status("error", "Choose a character in the library first."), cid, keep, keep, keep, keep, keep, keep,
                keep, keep, keep)
    if not confirmed:
        return (keep, cid, keep, keep, keep, keep, keep, keep, keep, keep, keep)
    where = library.trash(LIBRARY_DIR, cid)
    lib, ids, f_choices, p_choices, s_choices = refresh_library(filter_project, query)
    return (status("ok", f"Moved to {os.path.relpath(where, WEBUI_DIR)} (nothing was erased)."), "", lib, ids,
            f_choices, p_choices, s_choices, gr.update(value=None), "Click a character to choose it.", "", None)


def edit_character(cid):
    """The saved character back in the create column, to draw new versions of it."""
    keep = gr.update()
    c = library.load(LIBRARY_DIR, cid) if cid else None
    if c is None:
        return keep, keep, keep, keep, keep, status("error", "Choose a character in the library first.")
    return (json.dumps(c.get("character", {}), ensure_ascii=False, indent=1), c.get("reference_path"),
            c.get("template_path") or sheet.default_template(), c.get("name", ""), c["id"].split("/")[0],
            status("ok", "The character is in the box: change it if you like and press Create."))


# ---------------------------------------------------------------------- templates and sheet text

def use_template_by_default(template):
    if not template:
        return status("error", "Put a template picture in the box first."), gr.update()
    folder = os.path.join(LIBRARY_DIR, library.TEMPLATES)
    os.makedirs(folder, exist_ok=True)
    dest = os.path.join(folder, f"template-{time.strftime('%Y%m%d-%H%M%S')}.png")
    library._copy_png(template, dest)
    sheet.save_settings(template=dest)
    return status("ok", "This template is now the default."), gr.update(value=dest)


def builtin_template():
    sheet.save_settings(template=None)
    return status("ok", "Back to the built-in pose template."), gr.update(value=BUILTIN_TEMPLATE)


def save_sheet_text(text):
    if "{appearance}" not in (text or ""):
        return status("error", "The sheet prompt needs {appearance} where the character goes.")
    sheet.save_settings(sheet_text=text)
    return status("ok", "Sheet prompt saved.")


def reset_sheet_text():
    sheet.save_settings(sheet_text=None)
    return status("ok", "Sheet prompt back to the built-in one."), gr.update(value=sheet.DEFAULT_TEXT)


# ---------------------------------------------------------------------- video

def web_path(path):
    """A path the page can fetch through Gradio's /file= route."""
    rel = os.path.relpath(path, WEBUI_DIR)
    return (path if rel.startswith("..") else rel).replace("\\", "/")


def prepare_video(cid, idea, slot, video_model=""):
    """(video box, payload for the page, status): the Wan prompt (written by the writer when the idea is not in
    English) and what javascript/characters.js puts into Ingredients to Video."""
    keep = gr.update()
    c = library.load(LIBRARY_DIR, cid) if cid else None
    if c is None:
        return keep, "", status("error", "Choose a character in the library first.")
    idea = (idea or "").strip()
    prompt = idea
    if not idea:
        return keep, "", status("error", "Write what the character does in the video (any language).")
    if not sheet.is_english(idea):
        try:
            # an LTX model chosen in Video Studio gets an LTX prompt (with the sound in it)
            ltx = " LTX " in f" {video_model or ''} "
            answer = writer.write(writer.video_request(idea, c.get("character", {})), "ch_video",
                                  video_mode="ltx:ingredients" if ltx else "")
        except writer.WriterError as e:
            return keep, "", status("error", str(e))
        prompt = answer.get("prompt", "").strip() or idea
    payload = json.dumps({"sheet": web_path(c["sheet"]), "name": c.get("name", ""), "prompt": prompt,
                          "slot": slot, "nonce": time.time()})
    return prompt, payload, status("ok", "Sent to Video Studio → Ingredients to Video.")


# ---------------------------------------------------------------------- the tab

INTRO = ("Describe a character in any language - or give a reference picture - and FLUX 3 Klein draws a "
         "consistent character sheet over the pose template (front, side, back, faces, a close-up). Save the ones "
         "you like in projects, then send a character to Video Studio's Ingredients to Video with what it should "
         "do. Needs FLUX 3 Klein selected at the top of the page.")


def build():
    choices = project_choices()
    pictures, ids = gallery_items(ALL, "")
    with gr.Blocks(analytics_enabled=False) as tab:
        results = gr.State([])
        chosen = gr.State(0)
        lib_ids = gr.State(ids)
        selected = gr.State("")
        with gr.Row(elem_id="ch_main"):
            with gr.Column(scale=5):
                gr.Markdown(INTRO)
                character = gr.Textbox(label="The character: who you want, in any language (🌐 writes it as a "
                                             "character; Create does it too)", lines=7, elem_id="ch_character")
                with gr.Row():
                    reference = gr.Image(label="Reference picture (optional): the character is made from it",
                                         type="filepath", height=250, elem_id="ch_reference")
                    template = gr.Image(value=sheet.default_template(), label="Pose template (the sheet's layout)",
                                        type="filepath", height=250, elem_id="ch_template")
                with gr.Row():
                    template_default = gr.Button("Make this template the default", size="sm")
                    template_builtin = gr.Button("Back to the built-in template", size="sm")
                with gr.Row():
                    count = gr.Slider(1, 4, value=1, step=1, label="Sheets to draw (choose the best)")
                    seed = gr.Number(value=-1, precision=0, label="Seed of the sheet (-1: random)")
                with gr.Accordion("Sheet prompt (advanced)", open=False):
                    sheet_text = gr.Textbox(value=sheet.sheet_text(), lines=10, elem_id="ch_sheet_text",
                                            label="The prompt around the character: {appearance}, {style}, {avoid}, "
                                                  "{reference} are filled in")
                    with gr.Row():
                        sheet_text_save = gr.Button("Save the sheet prompt", size="sm")
                        sheet_text_reset = gr.Button("Built-in sheet prompt", size="sm")
                create_btn = gr.Button("✨ Create the character sheet", variant="primary", elem_id="ch_create")
                info = gr.HTML(status("", "Ready"))
                result_gallery = gr.Gallery(label="Sheets drawn: click the one to keep", columns=2, height=520,
                                            elem_id="ch_results", object_fit="contain")
                with gr.Row():
                    name = gr.Textbox(label="Name of the character", elem_id="ch_name")
                    project = gr.Dropdown(choices, value=choices[0], allow_custom_value=True, elem_id="ch_project",
                                          label="Project (choose one or type a new name)")
                save_btn = gr.Button("💾 Save the character", variant="primary", elem_id="ch_save")
            with gr.Column(scale=5):
                gr.Markdown("### My characters")
                with gr.Row():
                    show = gr.Dropdown([ALL] + choices, value=ALL, label="Show", elem_id="ch_show")
                    query = gr.Textbox(label="Search", placeholder="a name or a word", elem_id="ch_query")
                    refresh = gr.Button("🔄", size="sm", elem_id="ch_refresh", min_width=40)
                lib = gr.Gallery(value=pictures, label="Characters", columns=4, height=430, elem_id="ch_library",
                                 allow_preview=False, object_fit="contain")
                pick = gr.Textbox(elem_id="ch_pick", elem_classes=["ch-hidden"], show_label=False)
                pick_go = gr.Button(elem_id="ch_pick_go", elem_classes=["ch-hidden"])
                with gr.Group():
                    sel_info = gr.Markdown("Click a character to choose it.")
                    with gr.Row():
                        sel_name = gr.Textbox(label="Name", elem_id="ch_sel_name")
                        sel_project = gr.Dropdown(choices, allow_custom_value=True, label="Project of the character",
                                                  elem_id="ch_sel_project")
                    with gr.Row():
                        rename_btn = gr.Button("Save name and project", size="sm")
                        edit_btn = gr.Button("✏️ Draw new versions of it", size="sm")
                        delete_btn = gr.Button("🗑 Delete", size="sm", elem_id="ch_delete")
                        delete_ok = gr.Checkbox(value=False, visible=False, elem_id="ch_delete_ok")
                    sel_sheet = gr.Image(label="Sheet", interactive=False, height=360, elem_id="ch_sel_sheet")
                gr.Markdown("### A 3D model of this character")
                with gr.Row():
                    detail = gr.Dropdown(list(model3d.DETAIL), value="Fine", label="Detail of the shape",
                                         elem_id="ch_model_detail")
                    make_model_btn = gr.Button("🧊 Make a 3D model", elem_id="ch_make_model")
                    sim_btn = gr.Button("🌍", elem_id="ch_to_sim360", min_width=48)
                model_view = gr.Model3D(label="3D model", height=360, elem_id="ch_model", interactive=False)
                sim_payload = gr.Textbox(elem_id="ch_sim_payload", elem_classes=["ch-hidden"], show_label=False)
                gr.Markdown("### A video with this character")
                video = gr.Textbox(label="What the character does in the video (any language; it is written as a Wan "
                                         "prompt)", lines=4, elem_id="ch_video")
                with gr.Row():
                    slot = gr.Dropdown(SLOTS, value=SLOTS[0], label="Ingredient slot", elem_id="ch_slot")
                    send_btn = gr.Button("🎬 Send to Ingredients to Video", variant="primary", elem_id="ch_send")
                payload = gr.Textbox(visible=False, elem_id="ch_payload")

        lib_outputs = [lib, lib_ids, show, project, sel_project]
        create_btn.click(create, inputs=[character, reference, template, count, seed, sheet_text],
                         outputs=[character, info, result_gallery, results, name])
        result_gallery.select(choose_result, inputs=[results], outputs=[chosen])
        save_btn.click(save_result, inputs=[results, chosen, name, project, show, query],
                       outputs=[info] + lib_outputs)
        for trigger in (show.change, query.submit, refresh.click):
            trigger(refresh_library, inputs=[show, query], outputs=lib_outputs)
        pick_go.click(select_character, inputs=[lib_ids, pick], outputs=[selected, sel_info, sel_name, sel_project,
                                                                        sel_sheet, model_view])
        make_model_btn.click(make_model, inputs=[selected, detail], outputs=[info, model_view])
        sim_btn.click(send_to_sim360, inputs=[selected], outputs=[sim_payload, info]).then(
            None, inputs=[sim_payload], outputs=None, _js="charactersSendToSim360")
        rename_btn.click(rename_character, inputs=[selected, sel_name, sel_project, show, query],
                         outputs=[info, selected] + lib_outputs)
        delete_btn.click(delete_character, inputs=[selected, delete_ok, show, query],
                         outputs=[info, selected] + lib_outputs + [sel_sheet, sel_info, sel_name, model_view],
                         _js="charactersConfirmDelete")
        edit_btn.click(edit_character, inputs=[selected], outputs=[character, reference, template, name, project, info])
        template_default.click(use_template_by_default, inputs=[template], outputs=[info, template])
        template_builtin.click(builtin_template, outputs=[info, template])
        sheet_text_save.click(save_sheet_text, inputs=[sheet_text], outputs=[info])
        sheet_text_reset.click(reset_sheet_text, outputs=[info, sheet_text])
        send_btn.click(prepare_video, inputs=[selected, video, slot, payload], outputs=[video, payload, info],
                       _js="charactersVideoModel").then(
            None, inputs=[payload], outputs=None, _js="charactersSendToVideo")
    return tab

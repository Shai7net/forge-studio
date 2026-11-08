# The "🗺️ Map Key" sub-tab of Deforum's Keyframes tab: a visual editor for the camera motion, the prompts and Strength.
# The editor runs in the browser (javascript/map-key.js draws it, javascript/map-key-core.js does the math); this file
# builds the tab, reads Deforum's fields into the editor (📥 Load) and writes the editor's result back (✅ Apply).
#
# Like Audio Keyframes, nothing here is part of get_component_names(): the tab only fills in Deforum's existing fields.

import hashlib
import html
import json
import os
import time
import traceback

import gradio as gr
from modules.ui_components import FormRow, ToolButton

from . import audio_keyframes as ak
from . import map_key as mk
from .audio_keyframes_ui import bilingual, ffmpeg_exe, nosave, number_or, player_update

PREFIX = "map_key_"
# Deforum fields the editor reads, in the order of the Load event's inputs (after the request box)
SOURCES = ["animation_mode", "max_frames", "diffusion_cadence", "fps", "animation_prompts"] + mk.LANE_KEYS + \
          ["audio_kf_path", "audio_kf_normalize", "audio_kf_threshold", "audio_kf_parts", "fov_schedule"]

GUIDE_HTML = """
<div dir="rtl" style="text-align:right; line-height:1.65">
<b>איך זה עובד</b>
<ol>
<li><b>📥 טען מדיפורום</b> קורא את מה שיש עכשיו בשדות: תנועות המצלמה (Motion), Strength, CFG, הפרומפטים ו-Max frames, וגם את השיר שנבחר ב-Audio Keyframes. בפעם הראשונה שפותחים את הטאב זה קורה לבד.</li>
<li><b>ציור:</b> כל שורה היא פרמטר על ציר הזמן. עם ✏ מציירים: <b>למעלה = מהר, למטה = איטי או עצירה</b>. 📏 מצייר קו ישר. כפתורי − ו-+ ליד השורה משנים את הטווח: + כדי לצייר מהירויות גבוהות יותר, − כדי לדייק במהירויות נמוכות.</li>
<li><b>מינוס:</b> עם ⬚ מסמנים קטע בגרירה ולוחצים <b>− מינוס</b>. בקטע הזה המצלמה נוסעת הפוך (אחורה, שמאלה או למטה) באותה מהירות, והוא נצבע באדום. <b>+</b> מחזיר, ⇄ הופך, ≈ מחליק, ×½ ו-×2 משנים את המהירות ו-⟲ מאפס. גרירה בשורת המוזיקה מסמנת את אותו זמן בכל השורות.</li>
<li><b>מוזיקה:</b> השיר מוצג למעלה עם הביטים וחלקי השיר. ♫ בכל שורה קובע כמה התנועה עוקבת אחרי המוזיקה. 0% = בדיוק הציור. 100% = הציור הוא התקרה, והמוזיקה מזיזה בתוכו. "שיאים" מגיב בעיקר למכות (כמו <span dir="ltr">1 + x^4</span>), ו"שווה" לכל העוצמה.</li>
<li><b>פרומפטים:</b> כל פרומפט הוא סמן סגול.
<ul>
<li>גוררים אותו על הציר, עם הצמדה לביטים. לחיצה כפולה מוסיפה פרומפט.</li>
<li>לחיצה על סמן פותחת עריכה: הטקסט, <b>עוצמת מעבר</b> (Strength ברגע המעבר, למשל 0.45) ובכמה פריימים היא חוזרת.</li>
<li>הירידה מחזיקה לפחות Cadence פריימים, כדי שתיפול על פריים שבאמת מג'ונרט, והיא זזה יחד עם הפרומפט.</li>
</ul></li>
<li><b>מסלול 3D:</b>
<ul>
<li>מסמנים "המצלמה נוסעת במסלול" ומציירים מסלול במפה. המפה היא מבט מלמעלה, ולמעלה במפה = קדימה.</li>
<li>בכלי ✋ גוררים נקודות, לחיצה כפולה מוסיפה נקודה ולחיצה ימנית מוחקת.</li>
<li>שורת הגובה מרימה ומורידה את המסלול לאורכו.</li>
<li>בתצוגת ה-3D רואים את המסלול ואת המצלמה. גרירה מסובבת את התצוגה, ו-Shift עם גרירה על נקודה מרים אותה.</li>
<li><b>"מהירות לאורך המסלול"</b> קובעת כמה מהר נוסעים, ומינוס בשורה הזו = נסיעה אחורה במסלול.</li>
<li>"מסתכלת לאורך המסלול" מסובבת את המצלמה בפניות. "תמיד קדימה" מחליקה הצידה בלי להסתובב.</li>
<li>🍞 אפה מעתיק את המסלול לשורות, כדי לערוך אותן ביד.</li>
<li><b>אופק ישר בפנייה שעולה:</b> כשהמסלול עולה בתוך פנייה, Map Key מוסיף גלגול קטן (Rotation 3D Z) ששומר על האופק ישר. הגלגול נוסף על שורת הגלגול שלך, והיא נשארת לעריכה. דיפורום מסובב את התמונה רחוק מהמרכז חזק יותר ממצלמה אמיתית, ולכן התיקון מותאם ל-FOV. ה-FOV שהתיקון מכוון אליו כתוב בסרגל המסלול. ברינדור אמיתי של טיפוס 19° בתוך פנייה של 33°, האופק נטה 7.5° בלי התיקון, ו-0.0° איתו. אחרי שינוי ה-FOV לוחצים 📥 טען.</li>
</ul></li>
<li><b>✅ החל על דיפורום</b>:
<ul>
<li>כותב לשדות רק שורות שסומנו "לכתוב". שורה מסתמנת לבד כשעורכים אותה.</li>
<li>את הפרומפטים הוא כותב רק אם שינית אותם.</li>
<li>שורה שלא נגעת בה נשארת כמו שהייתה, כולל נוסחאות כמו <span dir="ltr">sin(t)</span>.</li>
<li>קו ישר נכתב כשני מפתחות בלבד.</li>
</ul></li>
<li><b>מקשים</b> (אחרי לחיצה בתוך העורך):
<ul>
<li>ניגון: רווח = ניגון/עצירה · חצים = פריים (Shift = שנייה).</li>
<li>כלים: D ציור · L קו · S סימון.</li>
<li>על הקטע המסומן: − מינוס · = פלוס · F הפוך.</li>
<li>עריכה: M פרומפט במחוון · Delete מחיקה · Ctrl+Z ביטול · Ctrl+Y חזרה.</li>
<li>ניווט: גלגלת = זום בציר, Shift עם גלגלת = גלילה.</li>
</ul></li>
</ol>
<b>טיפים</b>
<ul>
<li>ב-3D התנועה קדימה היא Translation Z. ב-2D היא Zoom, והמסלול כותב Zoom, X ו-Y.</li>
<li>⛶ פותח את העורך במסך מלא. Esc יוצא.</li>
<li><b>פרויקטים:</b> נשמרים ב-<span dir="ltr">models/Deforum/map_key</span>. כל Apply נשמר גם כ"ה-Apply האחרון", וכך אפשר להמשיך לערוך אחרי הפעלה מחדש.</li>
<li>עם DeforumationQT דלוק, המתג של כל פרמטר ב-DeforumationQT קובע: דלוק = הערך של DeforumationQT במקום הלוח שנכתב כאן.</li>
</ul>
</div>
"""


def models_folder():
    from modules import paths
    return os.path.join(paths.models_path, "Deforum")


def projects_folder():
    return os.path.join(models_folder(), "map_key")


def last_apply_path():
    return os.path.join(models_folder(), "map_key_last.json")


def projects():
    try:
        return mk.list_projects(projects_folder())
    except Exception:
        return []


def expand_schedule(text, frames):
    from .camera_directing_ui import expand_schedule as deforum_expand
    return deforum_expand(text, frames)


def editor_html(data=None):
    """The editor's data for javascript/map-key.js. A new version on every Load, so a Load always starts over."""
    if data is None:
        return ("<div class='mk-data' data-version=''></div><div class='mk-placeholder'>"
                + bilingual("Press 📥 Load from Deforum to draw the camera motion, the prompts and Strength here.",
                            "לוחצים 📥 טען מדיפורום כדי לצייר כאן את תנועת המצלמה, הפרומפטים ו-Strength.") + "</div>")
    payload = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    version = hashlib.md5(f"{payload}{time.time_ns()}".encode("utf-8")).hexdigest()[:12]
    return f"<div class='mk-data' data-version='{version}' data-json='{html.escape(payload, quote=True)}'></div>"


def status_html(lines):
    """Lines of (English, Hebrew) or plain text (shown as it is in both languages)."""
    parts = []
    for line in lines:
        english, hebrew = line if isinstance(line, tuple) else (line, line)
        parts.append(f"<div>{bilingual(english, hebrew)}</div>")
    return f"<div class='mk-status-lines'>{''.join(parts)}</div>"


def failure_html(error):
    if isinstance(error, (mk.MapKeyError, ak.AudioKeyframeError)):
        text = html.escape(str(error))
    else:
        traceback.print_exc()
        text = html.escape(f"Unexpected error: {error} (details in the console).")
    return status_html([(f"⚠️ {text}", f"⚠️ {text}")])


# --- load ------------------------------------------------------------------------------------------------

def read_deforum(source, project=None):
    """The editor's data from Deforum's fields (and the song chosen in Audio Keyframes). Returns (data, notes, song path)."""
    fps = number_or(source["fps"], 30) or 30
    schedules = {key: source[key] for key in mk.LANE_KEYS}
    path = ak.clean_path((project or {}).get("song_path") or source["audio_kf_path"])
    notes, song = [], None
    if path:
        try:
            analysis = ak.analyze_audio(path, fps, ffmpeg_exe())
            try:
                parts = list(ak.part_start_frames(ak.parse_parts(source["audio_kf_parts"]), analysis.fps,
                                                  analysis.frame_count))
            except ak.AudioKeyframeError:
                parts = [0]
            song = mk.song_data(analysis, source["audio_kf_normalize"], number_or(source["audio_kf_threshold"], 0.0),
                                parts, path)
        except Exception as err:
            notes.append(f"The song could not be read ({err}); the editor works without it.")
    data, load_notes = mk.load_state(schedules, source["animation_prompts"], source["max_frames"], fps,
                                     source["diffusion_cadence"], source["animation_mode"], expand_schedule, song, project,
                                     source["fov_schedule"])
    try:  # the live control plan is drawn on the timeline, next to the music it was written against
        from . import live_control
        data["plan"] = live_control.read_state()["plan"]
    except Exception as err:
        data["plan"] = []
        notes.append(f"The plan could not be read ({err}).")
    return data, notes + load_notes, path


def loaded_status(data, notes, what=("Loaded from Deforum", "נטען מדיפורום")):
    song = data.get("song")
    frames, fps = data["frames"], data["fps"]
    project = data.get("project") or {}
    prompts = len(project["prompts"]) if isinstance(project.get("prompts"), list) else len(data["prompts"])
    english = f"📥 {what[0]}: {frames:,} frames at {fps:g} fps, {data['mode']}, {prompts} prompts"
    hebrew = f"📥 {what[1]}: {frames:,} פריימים ב-{fps:g} FPS, {data['mode']}, {prompts} פרומפטים"
    if song:
        name = html.escape(song["name"])
        english += f", song <b>{name}</b> ({len(song['beats'])} beats)"
        hebrew += f", השיר <b>{name}</b> ({len(song['beats'])} ביטים)"
    lines = [(english + ".", hebrew + "."),
             ("Draw on the lanes, then ✅ Apply to Deforum. Only what you change is written.",
              "מציירים על השורות ואז ✅ החל על דיפורום. נכתב רק מה ששינית.")]
    return status_html(lines + [f"⚠️ {html.escape(note)}" for note in notes])


def on_load(request, *values):
    if str(request or "") == "skip":
        return gr.update(), gr.update(), gr.update()
    source = dict(zip(SOURCES, values))
    try:
        data, notes, path = read_deforum(source)
        return editor_html(data), player_update({"path": path}), loaded_status(data, notes)
    except Exception as err:
        return gr.update(), gr.update(), failure_html(err)


def open_project(project, source, what):
    """The editor for a saved project: its own length, FPS, mode and cadence, drawn over Deforum's current fields."""
    source = dict(source)
    source.update(max_frames=project["frames"], fps=project.get("fps") or source["fps"],
                  animation_mode=project.get("mode") or source["animation_mode"],
                  diffusion_cadence=project.get("cadence") or source["diffusion_cadence"])
    data, notes, path = read_deforum(source, project)
    return editor_html(data), player_update({"path": path}), loaded_status(data, notes, what)


def on_open(request, name, *values):
    if str(request or "") == "skip":
        return gr.update(), gr.update(), gr.update()
    try:
        if not name:
            raise mk.MapKeyError("Choose a saved project first.")
        project = mk.load_project(mk.project_file(projects_folder(), name))
        return open_project(project, dict(zip(SOURCES, values)), (f"Opened {html.escape(name)}", f"נפתח {html.escape(name)}"))
    except Exception as err:
        return gr.update(), gr.update(), failure_html(err)


def on_last(request, *values):
    if str(request or "") == "skip":
        return gr.update(), gr.update(), gr.update()
    try:
        project = mk.load_project(last_apply_path())
        return open_project(project, dict(zip(SOURCES, values)), ("Opened the last Apply", "נפתח ה-Apply האחרון"))
    except Exception as err:
        return gr.update(), gr.update(), failure_html(err)


# --- apply and projects ----------------------------------------------------------------------------------

def on_apply(request, max_frames):
    unchanged = [gr.update()] * (len(mk.LANE_KEYS) + 1)
    try:
        updates, prompts_text, project, notes = mk.apply_payload(request, max_frames)
    except Exception as err:
        return unchanged + [failure_html(err)]
    outputs = [gr.update(value=updates[key]) if key in updates else gr.update() for key in mk.LANE_KEYS]
    outputs.append(gr.update(value=prompts_text) if prompts_text is not None else gr.update())
    if project:
        try:
            mk.save_project(last_apply_path(), project)
        except Exception:
            traceback.print_exc()
    written = [f"{ak.TARGETS_BY_KEY[key].label} ({text.count(':')} keys)" for key, text in updates.items()]
    written_he = [f"{ak.TARGETS_BY_KEY[key].label} ({text.count(':')} מפתחות)" for key, text in updates.items()]
    if prompts_text is not None:
        count = len(json.loads(prompts_text))
        written.append(f"Prompts ({count})")
        written_he.append(f"Prompts ({count})")
    if written:
        lines = [(f"✅ Written to Deforum: {', '.join(written)}.", f"✅ נכתב לדיפורום: {', '.join(written_he)}."),
                 ("Everything else was left as it was.", "כל השאר נשאר כמו שהיה.")]
    else:
        lines = [("Nothing was changed yet, so nothing was written.", "עוד לא שינית כלום, אז לא נכתב כלום.")]
    return outputs + [status_html(lines + [f"⚠️ {html.escape(note)}" for note in notes])]


def on_save(request, name):
    try:
        if not str(request or "").strip():
            raise mk.MapKeyError("There is nothing to save yet: press 📥 Load from Deforum first.")
        project = json.loads(request)
        path = mk.project_file(projects_folder(), name)
        replaced = os.path.isfile(path)
        mk.save_project(path, project)
    except Exception as err:
        return failure_html(err), gr.update()
    shown = html.escape(os.path.basename(path)[:-len(mk.PROJECT_SUFFIX)])
    return (status_html([(f"💾 Project <b>{shown}</b> {'replaced' if replaced else 'saved'}.",
                          f"💾 הפרויקט <b>{shown}</b> {'הוחלף' if replaced else 'נשמר'}.")]),
            gr.update(choices=projects(), value=os.path.basename(path)[:-len(mk.PROJECT_SUFFIX)]))


# --- layout and wiring -----------------------------------------------------------------------------------

def create_map_key_tab():
    """Builds the tab inside the caller's TabItem. Returns its components; every key starts with map_key_."""
    c = {}

    def add(name, component):
        c[PREFIX + name] = nosave(component)
        return component

    with nosave(gr.Accordion("📖 מדריך קצר (עברית)", open=False, elem_id="map_key_guide_accord")):
        gr.HTML(GUIDE_HTML)
    with FormRow(variant="compact"):
        add("load_btn", gr.Button("📥 Load from Deforum", elem_id="map_key_load_btn"))
        add("apply_btn", gr.Button("✅ Apply to Deforum", variant="primary", elem_id="map_key_apply_btn"))
        add("last_btn", gr.Button("↩ Open the last Apply", elem_id="map_key_last_btn"))
    add("status", gr.HTML("", elem_id="map_key_status"))
    add("player", gr.Audio(label="Listen", type="filepath", interactive=False, show_download_button=False,
                           elem_id="map_key_player"))
    add("editor", gr.HTML(editor_html(), elem_id="map_key_editor"))
    with nosave(gr.Accordion("💾 Map Key projects", open=False, elem_id="map_key_projects_accord")):
        with FormRow(variant="compact"):
            add("project_name", gr.Textbox(label="Project name", max_lines=1, placeholder="e.g. Seinfeld flight",
                                           elem_id="map_key_project_name"))
            add("save_btn", gr.Button("💾 Save project", elem_id="map_key_save_btn"))
        with FormRow(variant="compact"):
            add("projects", gr.Dropdown(choices=projects(), label="Saved projects", elem_id="map_key_projects"))
            add("refresh_btn", ToolButton(value="\U0001f504", tooltip="Refresh the list of projects",
                                          elem_id="map_key_refresh_btn"))
            add("open_btn", gr.Button("📂 Open project", elem_id="map_key_open_btn"))
    add("request", gr.Textbox(visible=False, elem_id="map_key_request"))
    add("plan_request", gr.Textbox(visible=False, elem_id="map_key_plan_request"))
    add("plan_save", gr.Button(visible=False, elem_id="map_key_plan_save"))
    return c


def wire_map_key(l):
    """Connects the tab to Deforum's fields; `l` is setup_deforum_left_side_ui's locals() with every tab built.
    The browser fills the hidden request box just before each event (the _js functions in javascript/map-key.js)."""
    if PREFIX + "apply_btn" not in l:
        return  # the tab failed to build; Deforum works without it
    c = {name[len(PREFIX):]: component for name, component in l.items() if name.startswith(PREFIX)}
    sources = [l[name] for name in SOURCES]
    editor = [c["editor"], c["player"], c["status"]]

    def js(function, inputs):  # the page sends only the inputs, not the outputs' old values (the editor is big)
        return f"(...args) => {function}(args, {len(inputs)})"

    load_inputs = [c["request"]] + sources
    c["load_btn"].click(fn=on_load, _js=js("mapKeyLoad", load_inputs), inputs=load_inputs, outputs=editor)
    if "map_key_tab" in l:  # the first visit loads by itself
        l["map_key_tab"].select(fn=on_load, _js=js("mapKeyAutoLoad", load_inputs), inputs=load_inputs, outputs=editor)
    apply_inputs = [c["request"], l["max_frames"]]
    c["apply_btn"].click(fn=on_apply, _js=js("mapKeyApply", apply_inputs), inputs=apply_inputs,
                         outputs=[l[key] for key in mk.LANE_KEYS] + [l["animation_prompts"], c["status"]])
    c["last_btn"].click(fn=on_last, _js=js("mapKeyLoad", load_inputs), inputs=load_inputs, outputs=editor)
    save_inputs = [c["request"], c["project_name"]]
    c["save_btn"].click(fn=on_save, _js=js("mapKeySave", save_inputs), inputs=save_inputs,
                        outputs=[c["status"], c["projects"]])
    open_inputs = [c["request"], c["projects"]] + sources
    c["open_btn"].click(fn=on_open, _js=js("mapKeyLoad", open_inputs), inputs=open_inputs, outputs=editor)
    c["refresh_btn"].click(fn=lambda: gr.update(choices=projects()), inputs=[], outputs=[c["projects"]],
                           queue=False, show_progress=False)

    def on_plan(text):
        """A step was dragged or removed on the timeline: the bar's plan follows at once."""
        from . import live_control
        try:
            rows = json.loads(text or "[]")
        except ValueError:
            return
        if isinstance(rows, list):
            live_control.set_plan(rows)

    c["plan_save"].click(fn=on_plan, inputs=[c["plan_request"]], outputs=[], queue=False, show_progress=False)

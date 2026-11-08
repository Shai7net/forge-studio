# The "Audio Keyframes" sub-tab of Deforum's Keyframes tab: layout, events and wiring.
# All the audio and schedule logic lives in audio_keyframes.py (pure, unit-tested).
#
# Nothing here is part of get_component_names(): the tab only writes text into Deforum's existing fields,
# so the run arguments, the settings files, the API and DeforumationQT's hooks are untouched.

import filecmp
import hashlib
import html
import json
import os
import shutil
import time
import traceback

import gradio as gr
import numpy as np
from modules.ui_components import FormRow, ToolButton

from . import audio_keyframes as ak
from . import camera_directing as cd

PREFIX = "audio_kf_"
AUDIO_FILE_TYPES = ["audio", "video", ".mp3", ".wav", ".flac", ".ogg", ".opus", ".m4a", ".aac", ".wma", ".aiff",
                    ".mp4", ".mov", ".mkv", ".webm"]

GENERAL_SETTINGS = ["path", "fps", "normalize", "threshold", "raw_function", "raw_band", "reaction",
                    "split_count", "split_seconds", "auto_min", "parts", "crossfade",
                    "set_fps", "set_soundtrack", "set_max_frames",
                    "kp_source", "kp_band", "kp_count", "kp_gap", "kp_zero", "kp_placeholder", "kp_mode"]
ROW_FIELDS = ["enabled", "band", "function", "quiet", "loud", "smooth", "min", "max", "switch"]
SETTING_NAMES = GENERAL_SETTINGS + [f"{t.key}_{field}" for t in ak.TARGETS for field in ROW_FIELDS]

# What + does on screen for each song-parts column, measured with Deforum motion-preview renders (- is the opposite).
DIRECTION_HINTS = {
    "Zoom": "+ zoom in (2D)",
    "Angle": "+ picture turns counter-clockwise (2D)",
    "X": "+ in 2D the picture slides right; in 3D the camera moves right (picture slides left)",
    "Y": "+ picture slides down (camera up)",
    "Z": "+ camera moves forward (3D)",
    "Rot X": "+ camera tilts up (3D)",
    "Rot Y": "+ camera pans right (3D)",
    "Rot Z": "+ picture turns counter-clockwise (3D)",
    "Flip theta/phi/gamma": "perspective flip angles",
}

GUIDE_HTML = """
<div dir="rtl" style="text-align:right; line-height:1.65">
<b>איך זה עובד</b>
<ol>
<li><b>מוזיקה:</b> מעלים שיר או מדביקים את הנתיב שלו (אפשר גם עם מרכאות). הניתוח רץ מיד ומחשב עוצמה לכל פריים, כמו באתר chigozie: לכל השיר (Full) וגם בנפרד לבס, לאמצע ולגבוהים.</li>
<li><b>FPS:</b> בוחרים FPS לקיפריימים. הכפתור ⟲ מעתיק את ה-FPS מטאב Output.</li>
<li><b>🎧 האזנה וסימון:</b> אחרי הניתוח השיר מתנגן כאן, ולידו כתוב בכל רגע באיזה פריים הוא נמצא לפי ה-FPS, ואיזה פרומפט פעיל שם. ציר הזמן מראה את כל השיר, ומתחתיו הגדלה של כמה שניות סביב הנקודה הנוכחית עם מספר לכל פריים. לחיצה על ציר הזמן קופצת לשם. אחרי לחיצה על הנגן או על ציר הזמן המקשים פעילים: <b>רווח</b> = ניגון/עצירה, <b>חצים</b> = פריים אחד, <b>Shift עם חצים</b> = שנייה, <b>M</b> = פרומפט חדש בפריים הזה, <b>P</b> = חלק שיר חדש שמתחיל כאן. בזמן ניגון הסימון נופל קצת לפני מה ששומעים (Reaction time), כדי לפצות על זמן התגובה. כשהשיר עצור, הסימון נופל בדיוק על הפריים שמוצג, אז אפשר לעצור, לכוון עם החצים וללחוץ M.</li>
<li><b>חלקי שיר:</b> כל שורה בטבלה היא חלק בשיר (זמן התחלה). בכל עמודת תנועה כותבים <b>+</b> (כיוון חיובי), <b>-</b> (כיוון הפוך), <b>0</b> (בלי התנועה הזו) או מספר כמו 0.5 (חצי). אפשר לחלק לחלקים שווים, כל X שניות, או אוטומטית לפי שינויים בשיר. שורות חדשות ממשיכות את הדפוס של השורות הקיימות.</li>
<li><b>מה + עושה (נמדד בדיפורום):</b> Zoom + = זום פנימה · Z + = המצלמה נוסעת קדימה · Rot Y + = המצלמה פונה ימינה · Rot X + = המצלמה מסתכלת למעלה · Y + = התמונה יורדת (המצלמה עולה) · X + ב-3D = המצלמה זזה ימינה, וב-2D = התמונה זזה ימינה · Angle + ו-Rot Z + = התמונה מסתובבת נגד כיוון השעון. מינוס עושה את ההפך.</li>
<li><b>פרמטרים:</b> מסמנים מה המוזיקה מזיזה. לכל פרמטר יש <b>Function</b> משלו: פונקציה של x, העוצמה בין 0 ל-1, כמו באתר. ברירת המחדל <code dir="ltr">1 + x^4</code> מגיבה בעיקר לשיאים, <code dir="ltr">x</code> מגיבה באופן שווה ו-<code dir="ltr">x^2</code> באמצע. <b>Quiet</b> ו-<b>Loud</b> מותחים את הפונקציה בין הערך ברגע הכי שקט בשיר לערך ברגע הכי חזק, למשל Translation Z (מהירות הזום) מ-0.40 עד 1, ו-Strength מ-0.60 עד 0.80. משאירים את שניהם ריקים כדי לקבל את ערכי הפונקציה עצמה, בדיוק כמו באתר. בפונקציה אפשר להשתמש גם ב-bass, mids, highs, t (מספר הפריים) ו-sec. <b>Smooth</b> = במשך כמה פריימים הערך חוזר אחרי מכה. <b>Min / Max</b> = גבול קשיח. <b>Switch</b> (בתנועות מצלמה) = החלפת כיוון: <code dir="ltr">10</code> = כל 10 פריימים, <code dir="ltr">beats</code> = על הביטים, <code dir="ltr">beats 1.5</code> = על ביטים שרחוקים לפחות 1.5 שניות זה מזה, ריק = בלי. כשיוצאים משדה ששונה, הגרף מתעדכן לבד.</li>
<li><b>Apply to Deforum</b> כותב את הלוחות לשדות של דיפורום (Strength, Motion ועוד), ומעדכן גם FPS, Soundtrack ו-Max frames לפי השיר.</li>
<li><b>Key-prompts:</b> יוצר מפתחות בטאב Prompts ברגעים החזקים בשיר, ושם כותבים את הפרומפטים. פרומפט מתחלף בדיוק בפריים שלו.</li>
<li><b>🎬 בימוי מצלמה:</b> בוחרים מצב (תנועות קלות, אקשן, אומנותי, חלומי, מצלמת כתף) ולוחצים <b>🎬 ביים את המצלמה</b> (Direct the camera). המצב מחלק את השיר לחלקים וממלא את שורות המצלמה: מה זז, כמה מהר ועם איזה פס, ומתי מתחלף הכיוון. אחר כך לוחצים Apply to Deforum. <b>💾 שמור</b> (Save) שומר את שורות המצלמה המסומנות ואת דפוס החלקים כמצב חדש. <b>🧠 למד</b> (Learn) קורא את התנועות שבטאב Motion (למשל מסרטון שאהבת), מבין מהן את הסגנון ושומר אותו כמצב לשירים אחרים.</li>
</ol>
<b>טיפים</b>
<ul>
<li>Strength נמוך יותר = התמונה משתנה יותר. ברירת המחדל היא 0.60 בשקט ו-0.80 בשיא: בקטעים השקטים התמונה משתנה, ובשיאים היא יציבה. רוצים הפוך? מחליפים בין Quiet ל-Loud.</li>
<li>Zoom ו-Angle עובדים רק במצב 2D. ב-3D משתמשים ב-Translation Z (קדימה/אחורה) וב-Rotation 3D.</li>
<li><b>🗺️ Map Key</b> (הטאב שליד): אחרי Apply לוחצים 📥 טען, ואז רואים ועורכים הכל על ציר זמן אחד. אפשר לצייר מהירויות, לסמן קטעים במינוס, לגרור פרומפטים עם עוצמת מעבר, ולשרטט מסלול מצלמה במפה ובתלת-ממד.</li>
<li><b>בטאב Motion</b> ליד כל תנועת מצלמה יש כפתורים: <b>⇄</b> הופך כיוון (ימין↔שמאל, קדימה↔אחורה), ו-<b>− ↔N +</b> מחליפים כיוון כל N פריימים (1 = כל פריים, off = כיוון אחד). המהירויות נשארות כמו שהן.</li>
<li>עם Cadence גדול מ-1 דיפורום מייצר תמונה רק כל כמה פריימים, וירידה קצרה מאוד ב-Strength עלולה להתפספס. Smooth של 2–4 עוזר.</li>
<li><b>DeforumationQT:</b> לכל פרמטר ב-DeforumationQT יש מתג: Strength, CFG, FOV, זום (Translation Z), הזזה (Translation X/Y), סיבוב (Rotation 3D X/Y) והטיה (Rotation 3D Z). כשהמתג דלוק, הערך של DeforumationQT מחליף את הלוח. כשמכבים אותו, גם באמצע ג'ינרוט, דיפורום חוזר מהפריים הבא ללוח שבפורג, למשל זה שנבנה כאן מהמוזיקה. כך אפשר לעבור תוך כדי תנועה בין שליטה ידנית למוזיקה. הכפתור שמעל התצוגה מכבה את DeforumationQT לגמרי.</li>
</ul>
</div>
"""


def nosave(component):
    """Keeps a component out of ui-config.json, so code defaults stay in charge and repeated labels never clash."""
    component.do_not_save_to_config = True
    return component


def ffmpeg_exe():
    from .video_audio_utilities import get_ffmpeg_params
    return get_ffmpeg_params()[0]


def audio_folder():
    from modules import shared
    root = shared.opts.outdir_samples or shared.opts.outdir_img2img_samples
    return os.path.join(os.path.abspath(root), "Deforum_audio")


def last_apply_path():
    from modules import paths
    return os.path.join(paths.models_path, "Deforum", "audio_keyframes_last.json")


def keep_upload(file):
    """Copies an uploaded song next to the Deforum outputs, so the Soundtrack path still works after a restart."""
    source = getattr(file, "name", file)
    name = os.path.basename(getattr(file, "orig_name", "") or source)
    folder = audio_folder()
    os.makedirs(folder, exist_ok=True)
    stem, ext = os.path.splitext(name)
    candidate, index = os.path.join(folder, name), 1
    while os.path.exists(candidate):
        if filecmp.cmp(candidate, source, shallow=False):
            return candidate
        candidate, index = os.path.join(folder, f"{stem}_{index}{ext}"), index + 1
    shutil.copy2(source, candidate)
    return candidate


def config_from(values):
    return dict(zip(SETTING_NAMES, values))


def row_settings(config):
    return [ak.TargetSettings(t.key, enabled=config[f"{t.key}_enabled"], band=config[f"{t.key}_band"],
                              function=config[f"{t.key}_function"], quiet=config[f"{t.key}_quiet"],
                              loud=config[f"{t.key}_loud"], smooth=config[f"{t.key}_smooth"],
                              lo=config[f"{t.key}_min"], hi=config[f"{t.key}_max"], switch=config[f"{t.key}_switch"])
            for t in ak.TARGETS]


def analyze(config):
    return ak.analyze_audio(config["path"], config["fps"], ffmpeg_exe())


def number_or(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# --- listen and mark ---------------------------------------------------------------------------------------

PROMPTS_DOM_ID = ""  # DOM id of Deforum's Prompts box (set when the tab is wired); the timeline reads live prompts from it
VIDEO_EXTENSIONS = (".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".wmv")


def playable_audio(path):
    """What the Listen player loads: a URL or an audio file as it is, and the sound of a video, taken out once into
    Deforum_audio so a big video is never copied just to be heard."""
    source = ak.clean_path(path)
    if not source or ak.is_url(source):
        return source or None
    if not os.path.isfile(source):
        return None
    if os.path.splitext(source)[1].lower() not in VIDEO_EXTENSIONS:
        return source
    stat = os.stat(source)
    folder = audio_folder()
    os.makedirs(folder, exist_ok=True)
    stem = os.path.splitext(os.path.basename(source))[0]
    target = os.path.join(folder, f"{stem}_sound_{stat.st_size}_{int(stat.st_mtime)}.m4a")
    if not os.path.isfile(target):
        partial = target[:-len(".m4a")] + ".partial.m4a"
        result = ak._run([ffmpeg_exe(), "-hide_banner", "-v", "error", "-nostdin", "-y", "-i", source, "-map", "0:a:0",
                          "-vn", "-c:a", "aac", "-b:a", "160k", partial])
        if result.returncode != 0 or not os.path.isfile(partial):
            raise ak.AudioKeyframeError(f"ffmpeg could not take the sound out of {source}: {ak._tail(result.stderr)}")
        os.replace(partial, target)
    return target


def player_update(config):
    try:
        return gr.update(value=playable_audio(config["path"]))
    except Exception as err:
        print(f"Audio Keyframes player: {err}")
        return gr.update()


def bilingual(english, hebrew):
    """Both languages in the page; the Hebrew UI switch (html.hebrew-ui-active) decides which one shows."""
    return f"<span class='akf-en'>{english}</span><span class='akf-he'>{hebrew}</span>"


TIMELINE_BUTTONS = (
    ("second-back", "−1 s", "שנייה אחורה", "One second back (Shift + ←)", ""),
    ("frame-back", "◀ 1 frame", "פריים אחורה", "One frame back (←)", ""),
    ("play", "▶ ❚❚", "▶ ❚❚", "Play / pause (Space)", ""),
    ("frame-forward", "1 frame ▶", "פריים קדימה", "One frame forward (→)", ""),
    ("second-forward", "+1 s", "שנייה קדימה", "One second forward (Shift + →)", ""),
    ("mark-prompt", "✚ Prompt here (M)", "✚ פרומפט כאן (M)", "New prompt key at this frame (M)", "akf-mark"),
    ("mark-part", "✚ Song part here (P)", "✚ חלק שיר כאן (P)", "New song part starting at this frame (P)", "akf-mark"),
)


def timeline_html(analysis=None, config=None, prompts_text="", part_frames=None):
    """The Listen panel: the song's data for javascript/audio-keyframes-player.js, the live readout and the buttons."""
    if analysis is None:
        return ("<div class='akf-empty'>" + bilingual("Analyze a song to listen to it here and mark frames.",
                                                      "מנתחים שיר כדי לשמוע אותו כאן ולסמן פריימים.") + "</div>")
    data = ak.timeline_data(analysis, config["normalize"], number_or(config["threshold"], 0.0))
    data.update(parts=sorted(part_frames or [0]), prompts=ak.prompt_frames(prompts_text), prompts_id=PROMPTS_DOM_ID)
    payload = json.dumps(data, separators=(",", ":"))
    version = hashlib.md5(payload.encode("utf-8")).hexdigest()[:12]
    fps, frames = f"{analysis.fps:g}", f"{analysis.frame_count:,}"
    buttons = "".join(f"<button type='button' class='{extra}' data-akf='{action}' title='{title}'>{bilingual(en, he)}</button>"
                      for action, en, he, title, extra in TIMELINE_BUTTONS)
    keys = bilingual("Space play / pause · ← → one frame · Shift + ← → one second · M prompt at this frame · "
                     "P song part from here · click the timeline to jump",
                     "רווח ניגון/עצירה · חצים פריים אחד · Shift עם חצים שנייה · M פרומפט בפריים הזה · "
                     "P חלק שיר מכאן · לחיצה על ציר הזמן קופצת")
    return (f"<div class='akf-timeline' data-version='{version}' data-json='{html.escape(payload, quote=True)}'>"
            "<div class='akf-readout'>"
            "<span class='akf-clock'>0:00.000</span>"
            f"<span>{bilingual('frame', 'פריים')} <b class='akf-frame'>0</b> "
            f"<span class='akf-total'>{bilingual(f'of {frames} · {fps} fps', f'מתוך {frames} · {fps} FPS')}</span></span>"
            f"<span>{bilingual('song part', 'חלק שיר')} <b class='akf-part-number'>1</b></span>"
            f"<span class='akf-keys-on'>{bilingual('⌨ keys on', '⌨ המקשים פעילים')}</span>"
            f"<span class='akf-keys-off'>{bilingual('⌨ keys: click the player or the timeline', '⌨ למקשים: לוחצים על הנגן או על ציר הזמן')}</span>"
            "</div>"
            f"<div class='akf-prompt'>{bilingual('prompt', 'פרומפט')}: <span class='akf-prompt-text'>—</span></div>"
            "<canvas class='akf-canvas' height='158'></canvas>"
            f"<div class='akf-controls'>{buttons}</div>"
            f"<div class='akf-keys'>{keys}</div>"
            "</div>")


BAND_COLORS = {ak.BAND_FULL: "#7f7f7f", ak.BAND_BASS: "#d62728", ak.BAND_MIDS: "#ff7f0e", ak.BAND_HIGHS: "#1f77b4"}


def make_plot(analysis, config, part_frames=None, results=None, prompt_frames=None):
    from matplotlib.figure import Figure

    rows = 2 if results else 1
    fig = Figure(figsize=(10, 2.8 * rows), dpi=100)
    seconds = np.arange(analysis.frame_count) / analysis.fps
    axes = [fig.add_subplot(rows, 1, 1)]
    if results:
        axes.append(fig.add_subplot(rows, 1, 2, sharex=axes[0]))
    for band, curve in analysis.curves(config["normalize"], number_or(config["threshold"], 0.0)).items():
        axes[0].plot(seconds, curve, lw=0.8, color=BAND_COLORS[band], alpha=0.95 if band == ak.BAND_FULL else 0.7,
                     label=band)
    axes[0].set_ylabel("loudness 0..1")
    axes[0].set_ylim(-0.02, 1.05)
    if results:
        for result in results.values():
            values = result.values
            low, high = float(values.min()), float(values.max())
            scaled = (values - low) / (high - low) if high > low else np.full_like(values, 0.5)
            label = f"{result.target.label}: {ak.format_number(low, result.target.decimals)} .. " \
                    f"{ak.format_number(high, result.target.decimals)}"
            axes[1].plot(seconds, scaled, lw=1.0, label=label)
        axes[1].set_ylabel("schedules (min..max)")
        axes[1].set_ylim(-0.05, 1.05)
    for ax in axes:
        for frame in (part_frames or [])[1:]:
            ax.axvline(frame / analysis.fps, color="#2ca02c", lw=1.0, ls="--", alpha=0.8)
        for frame in prompt_frames or []:
            ax.axvline(frame / analysis.fps, color="#9467bd", lw=1.2, alpha=0.8)
        ax.set_xlim(0, max(analysis.duration, 1 / analysis.fps))
        ax.legend(loc="upper right", fontsize=7, ncol=4, framealpha=0.6)
        ax.grid(alpha=0.2)
    axes[-1].set_xlabel("seconds  (green dashes = song parts, purple = key-prompts)")
    fig.tight_layout()
    return fig


# --- layout ----------------------------------------------------------------------------------------------

def parts_help_html():
    hints = " &nbsp;·&nbsp; ".join(f"<b>{column}</b>: {hint}" for column, hint in DIRECTION_HINTS.items())
    return ("<p style='margin:0.2em 0'>Each row is a part of the song (start time). In each motion column write "
            "<b>+</b>, <b>-</b>, <b>0</b> (off) or a number such as 0.5 (half). How far it moves comes from the "
            "music and from the ticked row below; the table only decides the direction. "
            f"Empty cells count as +.</p><p style='margin:0.2em 0; font-size:0.9em'>{hints}</p>")


PARAMETERS_HELP_HTML = (
    "<p style='margin:0.2em 0'>Tick a parameter to open its settings. <b>Function</b> is its own function of x, the "
    "loudness 0..1 of its band, like on the website: <code>1 + x^4</code> reacts mostly to peaks, <code>x</code> evenly; "
    "bass, mids, highs, t (frame) and sec work too. <b>Quiet</b> and <b>Loud</b> stretch it between the value at the "
    "quietest and at the loudest moment of the song (leave both empty for the function's own values). <b>Smooth</b> = "
    "frames to fall back after a hit, <b>Min / Max</b> = hard limits (empty = none). <b>Switch</b> (camera motions) "
    "turns the direction around: <code>10</code> every 10 frames, <code>beats</code> on the beats, <code>beats 1.5</code> "
    "on beats at least 1.5 s apart, empty = never. The plot updates when you leave a changed field.</p>")


def target_row(add, target):
    key = target.key
    enabled = key == "strength_schedule"
    where = f" · {target.mode} only" if target.mode else ""
    column = f" · direction column {target.column}" if target.signed else ""
    with FormRow(variant="compact", elem_classes="audio-kf-row"):
        add(f"{key}_enabled", gr.Checkbox(label=f"{target.label}{where}{column}", value=enabled,
                                          elem_id=f"audio_kf_{key}_enabled"))
    with FormRow(variant="compact", visible=enabled, elem_classes="audio-kf-settings") as settings:
        add(f"{key}_band", gr.Dropdown(ak.BANDS, value=ak.BAND_FULL, label="Band", min_width=90))
        add(f"{key}_function", gr.Textbox(label="Function", value=ak.DEFAULT_FUNCTION, max_lines=1, min_width=170,
                                          elem_classes="audio-kf-function"))
        add(f"{key}_quiet", gr.Textbox(label="Quiet", value=ak.format_number(target.quiet, target.decimals), max_lines=1,
                                       min_width=60))
        add(f"{key}_loud", gr.Textbox(label="Loud", value=ak.format_number(target.loud, target.decimals), max_lines=1,
                                      min_width=60))
        add(f"{key}_smooth", gr.Number(label="Smooth", value=0, precision=0, min_width=60))
        add(f"{key}_min", gr.Textbox(label="Min", min_width=60, max_lines=1,
                                     value="" if target.lo is None else ak.format_number(target.lo, target.decimals)))
        add(f"{key}_max", gr.Textbox(label="Max", min_width=60, max_lines=1,
                                     value="" if target.hi is None else ak.format_number(target.hi, target.decimals)))
        add(f"{key}_switch", gr.Textbox(label="Switch", value="", max_lines=1, min_width=80, placeholder="off / N / beats",
                                        visible=target.signed))
    add(f"{key}_settings_row", settings)


def create_audio_keyframes_tab():
    """Builds the tab inside the caller's TabItem. Returns its components; every key starts with audio_kf_."""
    c = {}

    def add(name, component):
        c[PREFIX + name] = nosave(component)
        return component

    with nosave(gr.Accordion("📖 מדריך קצר (עברית)", open=False, elem_id="audio_kf_guide_accord")):
        gr.HTML(GUIDE_HTML)

    with nosave(gr.Accordion("1 · Music", open=True, elem_id="audio_kf_music_accord")):
        with gr.Row(variant="compact"):
            add("file", gr.File(label="Upload music (audio, or a video with sound)", file_types=AUDIO_FILE_TYPES,
                                elem_id="audio_kf_file"))
            with gr.Column(min_width=260):
                add("path", gr.Textbox(label="Music path or URL", lines=1, max_lines=1, elem_id="audio_kf_path",
                                       placeholder=r"D:\Music\song.mp3   (quotes are fine)"))
                with FormRow(variant="compact"):
                    add("fps", gr.Number(label="Keyframes FPS", value=30, precision=0, elem_id="audio_kf_fps"))
                    add("use_output_fps", ToolButton(value="\u27f2", tooltip="Use the FPS from the Output tab",
                                                     elem_id="audio_kf_use_output_fps"))
        with FormRow(variant="compact"):
            add("normalize", gr.Radio(ak.NORMALIZE_MODES, value=ak.NORMALIZE_PEAK, label="Loudness 1.0 is the",
                                      elem_id="audio_kf_normalize"))
            add("threshold", gr.Slider(minimum=0, maximum=0.9, step=0.01, value=0, label="Ignore sound quieter than",
                                       elem_id="audio_kf_threshold"))
        add("analyze_btn", gr.Button("Analyze music", variant="secondary", elem_id="audio_kf_analyze_btn"))
        add("status", gr.Markdown("Upload a song or paste its path, then press **Analyze music**.",
                                  elem_id="audio_kf_status"))
        add("plot", gr.Plot(label="Loudness and schedules", elem_id="audio_kf_plot"))
        with nosave(gr.Accordion("Raw keyframes, like the website", open=False, elem_id="audio_kf_raw_accord")):
            with FormRow(variant="compact"):
                add("raw_function", gr.Textbox(label="Function", value=ak.DEFAULT_FUNCTION,
                                               info="x = loudness 0..1, y = frame number, ^ = power, e.g. 1 + x^4"))
                add("raw_band", gr.Dropdown(ak.BANDS, value=ak.BAND_FULL, label="Band"))
            add("raw_btn", gr.Button("Generate raw keyframes", elem_id="audio_kf_raw_btn"))
            add("raw_text", gr.Textbox(label="Raw keyframes", lines=3, max_lines=8, show_copy_button=True,
                                       interactive=False, elem_id="audio_kf_raw_text"))

    with nosave(gr.Accordion("🎧 Listen and mark frames", open=True, elem_id="audio_kf_listen_accord")):
        add("player", gr.Audio(label="Listen", type="filepath", interactive=False, show_download_button=False,
                               elem_id="audio_kf_player"))
        add("timeline", gr.HTML(timeline_html(), elem_id="audio_kf_timeline"))
        with FormRow(variant="compact"):
            add("reaction", gr.Number(label="Reaction time (s)", value=0.15, elem_id="audio_kf_reaction",
                                      info="While the song plays, M and P mark this much earlier. 0 = exactly what you hear."))
        add("mark_status", gr.Markdown("", elem_id="audio_kf_mark_status"))
        add("mark_request", gr.Textbox(visible=False, elem_id="audio_kf_mark_request"))
        add("mark_btn", gr.Button("Mark", visible=False, elem_id="audio_kf_mark_btn"))

    headers = ak.PART_HEADERS
    with nosave(gr.Accordion("2 · Song parts and directions (+ / -)", open=True, elem_id="audio_kf_parts_accord")):
        gr.HTML(parts_help_html())
        with FormRow(variant="compact"):
            add("split_count", gr.Number(label="Parts", value=4, precision=0, min_width=70))
            add("split_equal_btn", gr.Button("Split into equal parts", min_width=150, elem_id="audio_kf_split_equal_btn"))
            add("split_seconds", gr.Number(label="Seconds per part", value=5, min_width=70))
            add("split_every_btn", gr.Button("Split every X seconds", min_width=150, elem_id="audio_kf_split_every_btn"))
        with FormRow(variant="compact"):
            add("auto_min", gr.Number(label="Shortest auto part (s)", value=8, min_width=70))
            add("split_auto_btn", gr.Button("Auto: split where the song changes", min_width=200,
                                            elem_id="audio_kf_split_auto_btn"))
            add("add_part_btn", gr.Button("+ Add part", min_width=90, elem_id="audio_kf_add_part_btn"))
            add("reset_parts_btn", gr.Button("Reset", min_width=70, elem_id="audio_kf_reset_parts_btn"))
        add("parts", gr.Dataframe(headers=headers, datatype=["str"] * len(headers), value=ak.default_part_rows(),
                                  row_count=(1, "dynamic"), col_count=(len(headers), "fixed"), type="array",
                                  interactive=True, label="Song parts", elem_id="audio_kf_parts"))
        add("crossfade", gr.Slider(minimum=0, maximum=60, step=1, value=6, label="Crossfade between parts (frames)",
                                   elem_id="audio_kf_crossfade"))

    with nosave(gr.Accordion("3 · Parameters the music drives", open=True, elem_id="audio_kf_rows_accord")):
        gr.HTML(PARAMETERS_HELP_HTML)
        for group, title in (("image", "Image change"), ("motion", "Camera motion"), ("more", "More")):
            with nosave(gr.Accordion(title, open=group != "more", elem_id=f"audio_kf_{group}_accord")):
                for target in ak.TARGETS:
                    if target.group == group:
                        target_row(add, target)

    with nosave(gr.Accordion("🎬 Camera directing", open=True, elem_id="audio_kf_camera_accord")):
        gr.HTML(CAMERA_HELP_HTML)
        modes = camera_modes()
        first_mode = next(iter(modes))
        with FormRow(variant="compact"):
            add("camera_mode", gr.Dropdown(list(modes), value=first_mode, label="Camera mode", elem_id="audio_kf_camera_mode"))
            add("camera_direct_btn", gr.Button("🎬 Direct the camera for this song", variant="primary",
                                               elem_id="audio_kf_camera_direct_btn"))
        add("camera_about", gr.HTML(mode_about_html(first_mode, modes), elem_id="audio_kf_camera_about"))
        with FormRow(variant="compact"):
            add("camera_name", gr.Textbox(label="Name for a new mode", max_lines=1, placeholder="e.g. Space orbit",
                                          elem_id="audio_kf_camera_name"))
            add("camera_save_btn", gr.Button("💾 Save the camera rows as a mode", elem_id="audio_kf_camera_save_btn"))
            add("camera_learn_btn", gr.Button("🧠 Learn a mode from the Motion tab", elem_id="audio_kf_camera_learn_btn"))
            add("camera_delete_btn", gr.Button("🗑 Delete the chosen mode", elem_id="audio_kf_camera_delete_btn"))
        add("camera_status", gr.Markdown("", elem_id="audio_kf_camera_status"))

    with nosave(gr.Accordion("4 · Send to Deforum", open=True, elem_id="audio_kf_apply_accord")):
        with FormRow(variant="compact"):
            add("set_fps", gr.Checkbox(label="Set Output FPS", value=True))
            add("set_soundtrack", gr.Checkbox(label="Set the soundtrack (Output tab)", value=True))
            add("set_max_frames", gr.Checkbox(label="Set Max frames to the song length", value=True))
        add("apply_btn", gr.Button("Apply to Deforum", variant="primary", elem_id="audio_kf_apply_btn"))
        add("apply_status", gr.Markdown("", elem_id="audio_kf_apply_status"))
        add("sync_status", gr.Markdown("", elem_id="audio_kf_sync_status"))
        add("applied_fps", gr.Number(value=0, visible=False, elem_id="audio_kf_applied_fps"))

    with nosave(gr.Accordion("5 · Key-prompts: new prompts at musical moments", open=True,
                             elem_id="audio_kf_prompts_accord")):
        with FormRow(variant="compact"):
            add("kp_source", gr.Dropdown(ak.PROMPT_SOURCES, value=ak.SOURCE_JUMPS, label="Place new prompts at"))
            add("kp_band", gr.Dropdown(ak.BANDS, value=ak.BAND_FULL, label="Band", min_width=90))
            add("kp_count", gr.Number(label="Max prompts (incl. frame 0)", value=8, precision=0, min_width=90))
            add("kp_gap", gr.Number(label="Min gap (s)", value=2.0, min_width=70))
        with FormRow(variant="compact"):
            add("kp_placeholder", gr.Textbox(label="Text for new prompts", value=ak.DEFAULT_PLACEHOLDER,
                                             info="{time}, {frame} and {prev} (the prompt before it) are filled in"))
            add("kp_zero", gr.Checkbox(label="Keep a prompt at frame 0", value=True))
        add("kp_mode", gr.Radio(ak.PROMPT_MODES, value=ak.PROMPT_MODE_ADD, label="Prompts tab"))
        add("kp_btn", gr.Button("Create key-prompts", variant="secondary", elem_id="audio_kf_kp_btn"))
        add("kp_status", gr.Markdown("", elem_id="audio_kf_kp_status"))

    with nosave(gr.Accordion("6 · Save / load these settings", open=False, elem_id="audio_kf_presets_accord")):
        add("preset_path", gr.Textbox(label="Settings file", value="audio_keyframes_settings.json",
                                      info="relative to the webui folder, or a full path"))
        with FormRow(variant="compact"):
            add("save_btn", gr.Button("Save settings", elem_id="audio_kf_save_btn"))
            add("load_btn", gr.Button("Load settings", elem_id="audio_kf_load_btn"))
            add("restore_btn", gr.Button("Restore the last Apply", elem_id="audio_kf_restore_btn"))
        add("preset_status", gr.Markdown(""))
    return c


# --- events ----------------------------------------------------------------------------------------------

def warn(message):
    return f"⚠️ {message}"


def failure(error):
    """Status text for an exception: expected problems read plainly, anything else also goes to the console."""
    if isinstance(error, ak.AudioKeyframeError):
        return warn(str(error))
    traceback.print_exc()
    return warn(f"Unexpected error: {error} (details in the console).")


def schedules_for(analysis, config):
    return ak.build_schedules(analysis, row_settings(config), config["parts"], number_or(config["crossfade"], 0),
                              config["normalize"], number_or(config["threshold"], 0.0))


def preview(config, prompt_frames=None):
    """Analysis and plot of the bands, song parts and ticked rows. Nothing is written to Deforum."""
    analysis = analyze(config)
    parts = ak.parse_parts(config["parts"])
    part_frames = list(ak.part_start_frames(parts, analysis.fps, analysis.frame_count))
    results, notes = {}, []
    try:
        results, _, _, notes = schedules_for(analysis, config)
    except ak.AudioKeyframeError as err:
        notes = [f"Schedules are not previewed: {err}"]
    text = "\n\n".join([analysis.describe()] + [warn(note) for note in notes])
    return analysis, results, make_plot(analysis, config, part_frames, results, prompt_frames), text, part_frames


def on_analyze(existing_prompts, *values):
    config = config_from(values)
    try:
        analysis, _, fig, text, part_frames = preview(config, ak.prompt_frames(existing_prompts))
        return text, fig, player_update(config), timeline_html(analysis, config, existing_prompts, part_frames)
    except Exception as err:
        return failure(err), None, gr.update(), gr.update()


def on_upload(file, existing_prompts, *values):
    if file is None:
        return gr.update(), gr.update(), gr.update(), gr.update(), gr.update()
    try:
        path = keep_upload(file)
    except Exception as err:
        return gr.update(), failure(err), gr.update(), gr.update(), gr.update()
    config = config_from(values)
    config["path"] = path
    try:
        analysis, _, fig, text, part_frames = preview(config, ak.prompt_frames(existing_prompts))
        return (path, f"{text}\n\nA copy for the soundtrack was saved to `{path}`", fig, player_update(config),
                timeline_html(analysis, config, existing_prompts, part_frames))
    except Exception as err:
        return path, failure(err), None, gr.update(), gr.update()


def on_row_preview(existing_prompts, *values):
    """A parameter field was left or Enter was pressed: redraw the preview when a song is chosen."""
    config = config_from(values)
    if not ak.clean_path(config["path"]):
        return gr.update(), gr.update()
    try:
        _, _, fig, text, _ = preview(config, ak.prompt_frames(existing_prompts))
        return text, fig
    except Exception as err:
        return failure(err), gr.update()


def on_mark(request, existing_prompts, *values):
    """A mark from the Listen panel: M puts a prompt key at the frame, P starts a song part there."""
    config = config_from(values)
    try:
        mark = json.loads(request or "{}")
        analysis = analyze(config)
        seconds = max(float(mark.get("frame", 0)), 0.0) / max(float(mark.get("fps") or analysis.fps), 1e-9)
        frame = min(int(round(seconds * analysis.fps)), analysis.frame_count - 1)
        at = f"frame **{frame}** ({ak.format_time_precise(frame / analysis.fps)})"
        prompts, rows = existing_prompts, config["parts"]
        prompts_update, parts_update = gr.update(), gr.update()
        if mark.get("kind") == "prompt":
            prompts, added = ak.mark_prompt(existing_prompts, frame, analysis.fps, config["kp_placeholder"])
            if added:
                prompts_update = gr.update(value=prompts)
            status = f"✚ New prompt at {at}. Write it in the Prompts tab." if added else f"The prompt at {at} is already there."
        elif mark.get("kind") == "part":
            rows, added = ak.insert_part(config["parts"], frame / analysis.fps, analysis.fps)
            if added:
                parts_update = gr.update(value=rows)
            status = (f"✚ New song part from {at}, with the directions of the part it splits." if added
                      else f"A song part already starts at {at}.")
        else:
            raise ak.AudioKeyframeError("Unknown mark.")
        part_frames = list(ak.part_start_frames(ak.parse_parts(rows), analysis.fps, analysis.frame_count))
        return prompts_update, parts_update, status, timeline_html(analysis, config, prompts, part_frames)
    except Exception as err:
        return gr.update(), gr.update(), failure(err), gr.update()


def on_use_output_fps(output_fps):
    return gr.update(value=int(round(number_or(output_fps, 30))))


def on_raw(*values):
    config = config_from(values)
    try:
        analysis = analyze(config)
        text = ak.raw_keyframes_text(analysis, config["raw_function"], config["raw_band"], config["normalize"],
                                     number_or(config["threshold"], 0.0))
        return text, f"{analysis.describe()} · raw keyframes for {analysis.frame_count:,} frames"
    except Exception as err:
        return gr.update(), failure(err)


def split_handler(kind):
    def handler(existing_prompts, *values):
        config = config_from(values)
        try:
            analysis = analyze(config)
            if kind == "equal":
                starts = ak.split_equal(analysis.duration, number_or(config["split_count"], 4))
            elif kind == "every":
                starts = ak.split_every(analysis.duration, number_or(config["split_seconds"], 5))
            else:
                starts = ak.split_auto(analysis, number_or(config["auto_min"], 8))
            rows = ak.part_rows(starts, config["parts"])
            config["parts"] = rows
            analysis, _, fig, text, part_frames = preview(config, ak.prompt_frames(existing_prompts))
            return (rows, f"{text}\n\nSong parts: **{len(rows)}**", fig,
                    timeline_html(analysis, config, existing_prompts, part_frames))
        except Exception as err:
            return gr.update(), failure(err), gr.update(), gr.update()
    return handler


def on_add_part(existing_prompts, *values):
    config = config_from(values)
    try:
        starts = [start for start, _ in ak.parse_parts(config["parts"])]
        step = max(number_or(config["split_seconds"], 5.0), 0.1)
        rows = ak.part_rows(starts + [starts[-1] + step if starts else 0.0], config["parts"])
    except Exception as err:
        failure(err)
        return gr.update(), gr.update()
    try:
        config["parts"] = rows
        analysis = analyze(config)
        part_frames = list(ak.part_start_frames(ak.parse_parts(rows), analysis.fps, analysis.frame_count))
        return rows, timeline_html(analysis, config, existing_prompts, part_frames)
    except Exception:
        return rows, gr.update()  # no song yet: only the table changes


def apply_summary(analysis, config, results, notes, animation_mode, output_fps):
    lines = []
    if results:
        lines.append("| Parameter | Min | Max | Keyframes |\n|---|---|---|---|")
        for result in results.values():
            decimals = result.target.decimals
            lines[-1] += (f"\n| {result.target.label} | {ak.format_number(result.values.min(), decimals)} | "
                          f"{ak.format_number(result.values.max(), decimals)} | {result.keyframe_count:,} |")
    else:
        lines.append("No parameter row is ticked, so no schedule was changed.")
    synced = []
    if config["set_fps"]:
        synced.append(f"FPS **{int(round(analysis.fps))}**")
    if config["set_max_frames"]:
        synced.append(f"Max frames **{analysis.frame_count:,}**")
    if config["set_soundtrack"]:
        synced.append(f"soundtrack `{ak.clean_path(config['path'])}`")
    if synced:
        lines.append("Also set: " + ", ".join(synced) + ".")
    notes = list(notes) + ak.mode_warnings(results.keys(), animation_mode)
    if not config["set_fps"] and abs(number_or(output_fps, 0) - analysis.fps) > 1e-6:
        notes.append(f"The Output FPS is {number_or(output_fps, 0):g} but the keyframes were made at "
                     f"{analysis.fps:g} fps, so the video will drift from the music.")
    lines += [warn(note) for note in notes]
    if results:
        lines.append("DeforumationQT: while its switch for one of these parameters (Strength, CFG, FOV, zoom, pan, rotation, tilt) "
                     "is on, its value replaces the schedule. Turn the switch off, even mid-render, and Deforum uses these "
                     "schedules again from the next frame; DeforumationQT OFF above the preview does it for all of them.")
    return "\n\n".join(lines)


def on_apply(animation_mode, output_fps, *values):
    config = config_from(values)
    unchanged = [gr.update()] * (len(ak.TARGETS) + 5)
    try:
        analysis = analyze(config)
        results, parts, _, notes = schedules_for(analysis, config)
    except Exception as err:
        return unchanged + [failure(err), gr.update(), gr.update(), gr.update()]
    schedules = [gr.update(value=results[t.key].text) if t.key in results else gr.update() for t in ak.TARGETS]
    flip_used = any(key in ak.PERSPECTIVE_FLIP_KEYS for key in results)
    deforum = [
        gr.update(value=int(round(analysis.fps))) if config["set_fps"] else gr.update(),
        gr.update(value="File") if config["set_soundtrack"] else gr.update(),
        gr.update(value=ak.clean_path(config["path"])) if config["set_soundtrack"] else gr.update(),
        gr.update(value=analysis.frame_count) if config["set_max_frames"] else gr.update(),
        gr.update(value=True) if flip_used else gr.update(),
    ]
    try:
        save_config(last_apply_path(), values)
    except Exception:
        traceback.print_exc()
    part_frames = list(ak.part_start_frames(parts, analysis.fps, analysis.frame_count))
    fig = make_plot(analysis, config, part_frames, results)
    status = apply_summary(analysis, config, results, notes, animation_mode, output_fps)
    return schedules + deforum + [status, fig, analysis.fps, ""]


def on_output_fps_change(output_fps, applied_fps):
    applied = number_or(applied_fps, 0)
    current = number_or(output_fps, 0)
    if applied <= 0 or abs(current - applied) < 1e-6:
        return ""
    return warn(f"The Output FPS is now {current:g}, but the audio keyframes were made at {applied:g} fps and no "
                "longer match the music. Set Keyframes FPS and press Apply to Deforum again.")


def on_key_prompts(existing_prompts, *values):
    config = config_from(values)
    try:
        analysis = analyze(config)
        normalize, threshold = config["normalize"], number_or(config["threshold"], 0.0)
        parts = ak.parse_parts(config["parts"])
        part_frames = list(ak.part_start_frames(parts, analysis.fps, analysis.frame_count))
        count = max(1, int(number_or(config["kp_count"], 8)))
        gap_seconds = max(number_or(config["kp_gap"], 2.0), 0.0)
        results = {}
        try:
            results = schedules_for(analysis, config)[0]
        except ak.AudioKeyframeError:
            if config["kp_source"] == ak.SOURCE_STRENGTH:
                raise
        scores = None
        if config["kp_source"] == ak.SOURCE_PARTS:
            frames = [f for f in part_frames if f or config["kp_zero"]][:count]
        else:
            strength = results["strength_schedule"].values if "strength_schedule" in results else None
            scores = ak.moment_scores(config["kp_source"], analysis, config["kp_band"], normalize, threshold, strength)
            frames = ak.pick_moments(scores, count, max(1, int(round(gap_seconds * analysis.fps))),
                                     bool(config["kp_zero"]))
        text, cues = ak.build_prompts(frames, analysis.fps, existing_prompts, config["kp_placeholder"],
                                      config["kp_mode"], scores)
    except Exception as err:
        return gr.update(), failure(err), gr.update(), gr.update()
    lines = [f"**{len(cues)} key-prompts** in the Prompts tab ({config['kp_mode'].lower()}). "
             "Write your prompts at these moments:",
             "| Frame | Time | Score |\n|---|---|---|" + "".join(
                 f"\n| {frame} | {time} | {'-' if score is None else f'{score:.2f}'} |" for frame, time, score in cues)]
    if len(cues) < count and config["kp_source"] != ak.SOURCE_PARTS:
        lines.append(f"Only {len(cues)} moments fit with a {gap_seconds:g} s gap.")
    fig = make_plot(analysis, config, part_frames, results or None, ak.prompt_frames(text))
    return text, "\n\n".join(lines), fig, timeline_html(analysis, config, text, part_frames)


# --- camera directing ------------------------------------------------------------------------------------

CAMERA_HELP_HTML = (
    "<p style='margin:0.2em 0'>Pick a camera mode and press <b>🎬 Direct the camera</b>: the mode splits the analysed song "
    "into parts and fills the camera rows above (which motions move, how fast with which band, and when they switch "
    "direction). Then press <b>Apply to Deforum</b>. <b>Save</b> keeps the ticked camera rows and the song-part pattern as "
    "a new mode. <b>Learn</b> reads the Motion tab, for example a video whose camera you liked, and saves its style as a "
    "mode for other songs.</p>")


def camera_modes_path():
    from modules import paths
    return os.path.join(paths.models_path, "Deforum", "camera_modes.json")


def camera_modes():
    try:
        return cd.all_modes(camera_modes_path())
    except Exception as err:  # a broken modes file must not break the tab
        print(f"Camera modes: {err}")
        return dict(cd.BUILTIN_MODES)


def mode_about_html(name, modes=None):
    mode = (modes if modes is not None else camera_modes()).get(name)
    if not mode:
        return ""
    moves = " · ".join(ak.TARGETS_BY_KEY[key].label for key in mode["rows"] if key in ak.TARGETS_BY_KEY)
    about = html.escape(mode.get("about") or "")
    return (f"<div class='akf-about'>{bilingual(about, html.escape(mode.get('about_he') or mode.get('about') or ''))}"
            f"<div class='akf-about-moves'>{html.escape(moves)}</div></div>")


def on_camera_direct(mode_name, animation_mode, existing_prompts, *values):
    """🎬: the chosen mode fills the camera rows and the song parts for the analysed song, and the preview is redrawn."""
    config = config_from(values)
    try:
        mode = camera_modes().get(mode_name)
        if mode is None:
            raise ak.AudioKeyframeError("Choose a camera mode first.")
        mode_values, notes = cd.mode_settings(mode, analyze(config), animation_mode)
        config.update(mode_values)
        analysis, results, fig, text, part_frames = preview(config, ak.prompt_frames(existing_prompts))
    except Exception as err:
        return [gr.update()] * len(SETTING_NAMES) + [failure(err), gr.update(), gr.update(), gr.update()]
    count = len(mode_values["parts"])
    lines = [f"🎬 **{mode_name}** directed the camera in {count} song part{'' if count == 1 else 's'}:"]
    for result in results.values():
        target = result.target
        if target.signed:
            lines.append(f"- {target.label}: {ak.format_number(result.values.min(), target.decimals)} → "
                         f"{ak.format_number(result.values.max(), target.decimals)}, "
                         f"{cd.direction_changes(result.values, target.neutral)} direction changes")
    lines += [warn(note) for note in notes]
    lines.append("Press **Apply to Deforum** to write it into the Motion tab.")
    updates = [gr.update(value=mode_values[name]) if name in mode_values else gr.update() for name in SETTING_NAMES]
    return updates + ["\n".join(lines), text, fig, timeline_html(analysis, config, existing_prompts, part_frames)]


def on_camera_save(name, *values):
    config = config_from(values)
    try:
        mode = cd.mode_from_settings(config, number_or(config["fps"], 30) or 30)
        name = str(name or "").strip()
        replaced = cd.add_user_mode(camera_modes_path(), name, mode)
        modes = camera_modes()
    except Exception as err:
        return gr.update(), gr.update(), failure(err)
    moves = ", ".join(ak.TARGETS_BY_KEY[key].label for key in mode["rows"])
    return (gr.update(choices=list(modes), value=name), mode_about_html(name, modes),
            f"💾 Mode **{name}** {'replaced' if replaced else 'saved'} ({moves}). It is in the Camera mode list for every song.")


def on_camera_learn(name, *motion_and_values):
    """🧠: a mode learned from the schedules now in Deforum's Motion tab, measured against the analysed song."""
    motion = dict(zip(cd.MOTION_KEYS, motion_and_values[:len(cd.MOTION_KEYS)]))
    config = config_from(motion_and_values[len(cd.MOTION_KEYS):])
    try:
        from .camera_directing_ui import expand_schedule
        analysis = analyze(config)
        schedules = {key: expand_schedule(text, max(analysis.frame_count, cd.last_key_frame(text) + 1))
                     for key, text in motion.items() if str(text or "").strip()}
        mode, lines = cd.learn_mode(analysis, schedules, config["normalize"], number_or(config["threshold"], 0.0))
        name = str(name or "").strip() or time.strftime("Learned %Y-%m-%d %H:%M")
        replaced = cd.add_user_mode(camera_modes_path(), name, mode)
        modes = camera_modes()
    except Exception as err:
        return gr.update(), gr.update(), failure(err)
    status = "\n".join([f"🧠 Learned **{name}** from the Motion tab{' (it replaced the old one)' if replaced else ''}:"]
                       + [f"- {line}" for line in lines] + ["Choose it for any song and press 🎬 Direct the camera."])
    return gr.update(choices=list(modes), value=name), mode_about_html(name, modes), status


def on_camera_delete(mode_name):
    try:
        cd.delete_user_mode(camera_modes_path(), mode_name)
        modes = camera_modes()
        first = next(iter(modes))
    except Exception as err:
        return gr.update(), gr.update(), failure(err)
    return gr.update(choices=list(modes), value=first), mode_about_html(first, modes), f"🗑 Mode **{mode_name}** deleted."


def resolve_settings_path(path):
    return os.path.abspath(ak.clean_path(path) or "audio_keyframes_settings.json")


def save_config(path, values):
    config = {name: (value.tolist() if hasattr(value, "tolist") else value) for name, value in config_from(values).items()}
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=4)


def load_updates(path):
    if not os.path.isfile(path):
        raise ak.AudioKeyframeError(f"There is no settings file at {path}.")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or not any(name in data for name in SETTING_NAMES):
        raise ak.AudioKeyframeError(f"{path} is not an Audio Keyframes settings file.")
    data = ak.upgrade_settings(data)  # files from before each row had a Function
    return [gr.update(value=data[name]) if name in data else gr.update() for name in SETTING_NAMES]


def on_save(path, *values):
    try:
        target = resolve_settings_path(path)
        save_config(target, values)
        return f"Saved to `{target}`."
    except Exception as err:
        return failure(err)


def on_load(path):
    try:
        target = resolve_settings_path(path)
        return load_updates(target) + [f"Loaded `{target}`."]
    except Exception as err:
        return [gr.update()] * len(SETTING_NAMES) + [failure(err)]


def on_restore():
    try:
        return load_updates(last_apply_path()) + ["Restored the settings of the last Apply to Deforum."]
    except Exception as err:
        return [gr.update()] * len(SETTING_NAMES) + [failure(err)]


def wire_audio_keyframes(l):
    """Connects the tab to Deforum's fields; `l` is setup_deforum_left_side_ui's locals() with every tab built."""
    global PROMPTS_DOM_ID
    if PREFIX + "apply_btn" not in l:
        return  # the tab failed to build; Deforum works without it
    c = {name[len(PREFIX):]: component for name, component in l.items() if name.startswith(PREFIX)}
    settings = [c[name] for name in SETTING_NAMES]
    prompts = l["animation_prompts"]
    PROMPTS_DOM_ID = prompts.elem_id or f"component-{prompts._id}"
    listen = [c["player"], c["timeline"]]

    for target in ak.TARGETS:
        key = target.key
        toggle = c[f"{key}_enabled"]
        # Direct events: loading settings flips many toggles at once, and queued jobs can be dropped (see gradio_funcs.py)
        toggle.change(fn=lambda on: gr.update(visible=bool(on)), inputs=[toggle], outputs=[c[f"{key}_settings_row"]],
                      queue=False, show_progress=False)
        # The preview follows the row: a function on Enter, and the function, Quiet or Loud when the field is left
        c[f"{key}_function"].submit(fn=on_row_preview, inputs=[prompts] + settings, outputs=[c["status"], c["plot"]])
        for field in ("function", "quiet", "loud") + (("switch",) if target.signed else ()):
            c[f"{key}_{field}"].blur(fn=on_row_preview, inputs=[prompts] + settings, outputs=[c["status"], c["plot"]])

    c["file"].upload(fn=on_upload, inputs=[c["file"], prompts] + settings, outputs=[c["path"], c["status"], c["plot"]] + listen)
    c["analyze_btn"].click(fn=on_analyze, inputs=[prompts] + settings, outputs=[c["status"], c["plot"]] + listen)
    c["path"].submit(fn=on_analyze, inputs=[prompts] + settings, outputs=[c["status"], c["plot"]] + listen)
    c["use_output_fps"].click(fn=on_use_output_fps, inputs=[l["fps"]], outputs=[c["fps"]])
    c["raw_btn"].click(fn=on_raw, inputs=settings, outputs=[c["raw_text"], c["status"]])
    for button, kind in (("split_equal_btn", "equal"), ("split_every_btn", "every"), ("split_auto_btn", "auto")):
        c[button].click(fn=split_handler(kind), inputs=[prompts] + settings,
                        outputs=[c["parts"], c["status"], c["plot"], c["timeline"]])
    c["add_part_btn"].click(fn=on_add_part, inputs=[prompts] + settings, outputs=[c["parts"], c["timeline"]])
    c["reset_parts_btn"].click(fn=ak.default_part_rows, inputs=[], outputs=[c["parts"]])
    # M / P in the Listen panel (javascript/audio-keyframes-player.js writes the frame into mark_request)
    c["mark_btn"].click(fn=on_mark, inputs=[c["mark_request"], prompts] + settings,
                        outputs=[prompts, c["parts"], c["mark_status"], c["timeline"]], queue=False, show_progress=False)

    deforum_outputs = [l[t.key] for t in ak.TARGETS] + [l["fps"], l["add_soundtrack"], l["soundtrack_path"],
                                                        l["max_frames"], l["enable_perspective_flip"]]
    c["apply_btn"].click(fn=on_apply, inputs=[l["animation_mode"], l["fps"]] + settings,
                         outputs=deforum_outputs + [c["apply_status"], c["plot"], c["applied_fps"], c["sync_status"]])
    l["fps"].change(fn=on_output_fps_change, inputs=[l["fps"], c["applied_fps"]], outputs=[c["sync_status"]],
                    queue=False, show_progress=False)
    c["kp_btn"].click(fn=on_key_prompts, inputs=[prompts] + settings,
                      outputs=[prompts, c["kp_status"], c["plot"], c["timeline"]])

    c["save_btn"].click(fn=on_save, inputs=[c["preset_path"]] + settings, outputs=[c["preset_status"]])
    c["load_btn"].click(fn=on_load, inputs=[c["preset_path"]], outputs=settings + [c["preset_status"]])
    c["restore_btn"].click(fn=on_restore, inputs=[], outputs=settings + [c["preset_status"]])

    # 🎬 Camera directing: a mode fills the camera rows and song parts; Save, Learn (from the Motion tab) and Delete modes
    camera_outputs = [c["camera_mode"], c["camera_about"], c["camera_status"]]
    c["camera_mode"].change(fn=lambda name: mode_about_html(name), inputs=[c["camera_mode"]], outputs=[c["camera_about"]],
                            queue=False, show_progress=False)
    c["camera_direct_btn"].click(fn=on_camera_direct, inputs=[c["camera_mode"], l["animation_mode"], prompts] + settings,
                                 outputs=settings + [c["camera_status"], c["status"], c["plot"], c["timeline"]])
    c["camera_save_btn"].click(fn=on_camera_save, inputs=[c["camera_name"]] + settings, outputs=camera_outputs)
    c["camera_learn_btn"].click(fn=on_camera_learn, inputs=[c["camera_name"]] + [l[key] for key in cd.MOTION_KEYS] + settings,
                                outputs=camera_outputs)
    c["camera_delete_btn"].click(fn=on_camera_delete, inputs=[c["camera_mode"]], outputs=camera_outputs)

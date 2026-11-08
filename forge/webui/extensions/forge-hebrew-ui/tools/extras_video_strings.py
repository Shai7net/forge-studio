"""The Hebrew UI strings of the Extras Video tab. Edit the rows, run this file, then build_dictionary.py.

The lines the tab writes while it works (what the video is, what was saved) carry both languages themselves:
see both() in modules/postprocessing_video.py."""
import ast
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
WEBUI = os.path.join(HERE, "..", "..", "..")
os.chdir(os.path.join(HERE, "..", "translations"))

rows = [
    ("Preview", "תצוגה מקדימה"),
    ("Preview a frame", "תצוגה מקדימה של פריים"),
    ("Preview from second", "תצוגה מקדימה החל משנייה"),
    ("Preview length (seconds)", "אורך התצוגה המקדימה (שניות)"),
    ("Compare with the original", "השוואה למקור"),
    ("Take the moment the source video is standing on", "לקחת את הרגע שבו סרטון המקור עומד"),
    ("Quality of videos made in Extras (H.264 CRF)", "איכות הסרטונים שנוצרים בכלים נוספים (H.264 CRF)"),
    ("(lower = better picture and a bigger file; 16 is hard to tell from the frames themselves, 23 is ffmpeg's default)",
     "(נמוך יותר = תמונה טובה יותר וקובץ גדול יותר; ב-16 קשה להבחין בהבדל מהפריימים עצמם, 23 הוא ברירת המחדל של ffmpeg)"),
    ("Keep restored faces steady from frame to frame in videos made in Extras",
     "לשמור על פנים משוחזרות יציבות מפריים לפריים בסרטונים שנוצרים בכלים נוספים"),
    ("(GFPGAN and CodeFormer restore each frame on its own, so faces shimmer and pop in and out; off = every frame as a picture would come out)",
     "(GFPGAN ו-CodeFormer משחזרים כל פריים בנפרד, ולכן הפנים מרצדות ומופיעות ונעלמות; כבוי = כל פריים כפי שתמונה הייתה יוצאת)"),
    ("Hires seed", "מספר התחלה להגדלה (Seed)"),
    ("Added to the prompt", "מתווסף לפרומפט"),
    ("Keep the drawing steady from frame to frame", "לשמור על הציור יציב מפריים לפריים"),
    ("Faster on large sizes (attention in tiles)", "מהיר יותר בגדלים גדולים (Attention באריחים)"),
    ("Empty: the prompt the video or picture was made with, when it still carries it",
     "ריק: הפרומפט שאיתו נוצרו הסרטון או התמונה, אם הוא עדיין שמור בהם"),
    ("Empty: the negative prompt it was made with", "ריק: הפרומפט השלילי שאיתו נוצרו"),
]
json.dump([{"i": i, "en": en, "ctx": "[t] Extras > Video (ui_postprocessing.py, shared_options.py)"} for i, (en, he) in enumerate(rows)],
          open("9_manual_extrasvideo_01.en.json", "w", encoding="utf8"), ensure_ascii=False, indent=1)
open("9_manual_extrasvideo_01.he.txt", "w", encoding="utf8").write("".join(f"{i}|{he}\n" for i, (en, he) in enumerate(rows)))

# the guide under the video is matched by its text, so that is read from the code itself
source = ast.parse(open(os.path.join(WEBUI, "modules", "ui_postprocessing.py"), encoding="utf8").read())
guide = next(ast.literal_eval(node.value) for node in source.body
             if isinstance(node, ast.Assign) and node.targets[0].id == "video_guide")
key = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", guide)).strip()
he = ('<p>מעלים סרטון ובוחרים למטה מה לעשות עם הפריימים שלו: Hires. fix, מגדיל (Upscaler), שחזור פנים. '
      '<b>תצוגה מקדימה של פריים</b> מרנדרת את הפריים האחד שב"תצוגה מקדימה החל משנייה", ו<b>תצוגה מקדימה</b> קטע קצר משם. '
      'שתיהן מוצגות עם קו שגוררים על פני התמונה: מצד אחד המקור, מהצד השני התוצאה. '
      'אחרי לחיצה על התמונה גלגלת העכבר מקרבת, ואז גרירה מזיזה אותה. '
      '<b>צור</b> מרנדר את כל הסרטון. התוצאה היא קובץ MP4 עם הקול המקורי.</p>'
      '<p>RTX Video Super Resolution נבנה לווידאו והוא המהיר ביותר בפער גדול; '
      'לסרטון מהטלפון או מהרשת, הגרסה שלו עם (compressed video) גם מנקה את ריבועי הדחיסה. '
      'R-ESRGAN מצייר מחדש יותר ולוקח שנייה או יותר לפריים; בהגדלה ×2, המודל R-ESRGAN 2x+ מהיר בערך פי שלושה מ-4x+. '
      'DAT מתאים רק לכמה שניות של וידאו.</p>')
# the guide inside the Hires. fix panel, the same way
source = ast.parse(open(os.path.join(WEBUI, "scripts", "postprocessing_hires.py"), encoding="utf8").read())
guide = next(ast.literal_eval(node.value) for node in source.body
             if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "guide")
hires_key = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", guide)).strip()
hires_he = ('<p>התמונה, או כל פריים בסרטון, מוגדלים עם המגדיל (Upscaler) ואז מצוירים מחדש על ידי ה-checkpoint הטעון: '
            'זה מה שמוסיף חדות ופרטים. החלונית מתחילה בהגדרות של LCM: שיטת דגימה LCM, 8 צעדים, CFG 1, וה-LoRA של LCM '
            'ב<b>מתווסף לפרומפט</b>, שמצטרף לפרומפט שאיתו נוצרו התמונה או הסרטון (או לזה שכתוב כאן). '
            'עוצמת השינוי (Denoising) קובעת כמה מצויר מחדש: 0.2 עד 0.3 מחדד ושומר על התמונה; '
            '0.5 מוסיף הרבה יותר, ממציא דברים שלא היו, ובסרטון יותר ממנו מרצד. '
            'כמו ב-Hires. fix, כל צעדי ההגדלה (Hires steps) רצים, בכל פריים: פחות צעדים = מהר יותר. '
            '0 צעדים, 0 ב-CFG, Seed של ‎-1 ו-“Use same sampler” לוקחים את מה שאיתו זה נוצר. '
            'כל פריים הוא יצירה בפני עצמה בגודל החדש, לכן כדאי לנסות קודם <b>תצוגה מקדימה של פריים</b>.</p>')
json.dump([{"i": 0, "text": key, "html": ""}, {"i": 1, "text": hires_key, "html": ""}],
          open("9_manual_extrasvideo_blocks.en.json", "w", encoding="utf8"), ensure_ascii=False, indent=1)
json.dump({"0": he, "1": hires_he}, open("9_manual_extrasvideo_blocks.he.json", "w", encoding="utf8"), ensure_ascii=False, indent=1)
print("guide key:", key)

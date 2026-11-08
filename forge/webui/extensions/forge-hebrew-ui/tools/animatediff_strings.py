"""The AnimateDiff Music / Dance strings of the Hebrew UI. Edit the rows, run this file, then build_dictionary.py."""
import json
import os

os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "translations"))

rows = [
    ("🎵 Music", "🎵 מוזיקה"),
    ("The MP4 / WEBM comes out with this music already on it. GIF and WEBP cannot hold sound, so MP4 is added when neither MP4 nor WEBM is selected.",
     "קובץ ה-MP4 / WEBM ייצא כשהמוזיקה הזאת כבר עליו. GIF ו-WEBP לא יכולים להכיל קול, לכן MP4 נוסף אוטומטית כשלא נבחר MP4 או WEBM."),
    ("Or music path", "או נתיב לקובץ המוזיקה"),
    ("Start the music from second", "להתחיל את המוזיקה משנייה"),
    ("Fade out at the end", "דעיכה בסוף (Fade out)"),
    ("When the music is longer than the video", "כשהמוזיקה ארוכה מהווידאו"),
    ("Cut the music to the video", "לחתוך את המוזיקה לאורך הווידאו"),
    ("Loop the video for the whole music", "לחזור על הווידאו לאורך כל המוזיקה"),
    ("⏱ Set Number of frames to fit the music", "⏱ לקבוע מספר פריימים לפי אורך המוזיקה"),
    ("Upload music first.", "קודם צריך להעלות מוזיקה."),
    ("Take frames evenly at the FPS (stays in sync with the music)", "לקחת פריימים במרווחים שווים לפי ה-FPS (נשאר מסונכרן עם המוזיקה)"),
    ("Use the dance video's own sound when no music is uploaded", "להשתמש בסאונד של וידאו הריקוד כשלא הועלתה מוזיקה"),
    ("Black dancers on white", "רקדנים שחורים על לבן"),
    ("White dancers on gray (keeps the prompt's background)", "רקדנים לבנים על אפור (שומר על הרקע מהפרומפט)"),
    ("White dancers on black (dark background)", "רקדנים לבנים על שחור (רקע כהה)"),
    ("Silhouette", "צללית"),
    ("Silhouette edge softness", "ריכוך קצוות הצללית"),
    ("👁 Preview silhouette", "👁 תצוגה מקדימה של הצללית"),
    ("Silhouette preview", "תצוגה מקדימה של הצללית"),
    ("Long video: render in parts of (frames, 0 = one pass)", "וידאו ארוך: לרנדר בחלקים של (פריימים, 0 = הכל במעבר אחד)"),
    ("Frames shared by neighbouring parts", "פריימים משותפים לחלקים סמוכים"),
    ("Upscale the frames with", "להגדיל את הפריימים עם"),
    ("💃 Dance video", "💃 סרטון ריקוד"),
    ("Make a dance video", "ליצור סרטון ריקוד"),
    ("1 · Dance video (only the movement is taken)", "1 · סרטון הריקוד (לוקחים ממנו רק את התנועה)"),
    ("2 · The dancers: what they are made of (1-4 pictures)", "2 · הרקדנים: ממה הם עשויים (1-4 תמונות)"),
    ("3 · The background behind the dancers (1-4 pictures, optional)", "3 · הרקע שמאחורי הרקדנים (1-4 תמונות, לא חובה)"),
    ("Follow the dancers' shape", "לעקוב אחרי צורת הרקדנים"),
    ("Follow the pose (arms and legs)", "לעקוב אחרי התנוחה (ידיים ורגליים)"),
    ("Dancers like their pictures", "הרקדנים כמו התמונות שלהם"),
    ("Background like its pictures", "הרקע כמו התמונות שלו"),
    ("Frames per second (8 and 12 draw faster, for tests)", "פריימים לשנייה (8 ו-12 מציירים מהר יותר, לבדיקות)"),
    ("8 (quick test)", "8 (בדיקה מהירה)"),
    ("24 (smooth, FILM)", "24 (חלק, FILM)"),
    ("Width and height in the video's shape", "רוחב וגובה לפי צורת הווידאו"),
    ("LCM motion module: sampler LCM, 8 steps, CFG 2, LCM LoRA", "מודול תנועה LCM: סמפלר LCM, 8 צעדים, CFG 2, LoRA של LCM"),
    ("Other music (otherwise the dance's own sound)", "מוזיקה אחרת (אחרת הסאונד של הריקוד)"),
    ("Upload the dance video first.", "קודם צריך להעלות את סרטון הריקוד."),
    ("Dancer pictures only on the dancers, background pictures only behind them", "תמונות הרקדנים רק על הרקדנים, תמונות הרקע רק מאחוריהם"),
    ("Objects, not people (adds person, face, skin, hair to the negative prompt)", "חפצים, לא אנשים (מוסיף person, face, skin, hair לפרומפט השלילי)"),
    ("Final size (the finished frames are upscaled)", "גודל סופי (הפריימים המוכנים מוגדלים)"),
    ("Final size upscaler", "אפסקיילר לגודל הסופי"),
    ("As drawn", "כפי שצויר"),
    ("Mask folder for an AnimateDiff video (a mask for every frame, used instead of the mask above)",
     "תיקיית מסכות לסרטון AnimateDiff (מסכה לכל פריים, במקום המסכה שלמעלה)"),
    ("Each frame is drawn only where its own mask is white, and the picture stays as it is everywhere else. "
     "With Number of frames at 0 the video gets one frame for every mask.",
     "כל פריים מצויר רק במקום שהמסכה שלו לבנה, ובכל שאר התמונה היא נשארת כמו שהיא. "
     "כשמספר הפריימים הוא 0, הסרטון מקבל פריים אחד לכל מסכה."),
    (r"C:\my video\masks   (quotes are fine)", r"C:\my video\masks   (אפשר עם מרכאות)"),
    ("A small dot in the masks is a ball (it is drawn as a ball in the colors of the figures)",
     "נקודה קטנה במסכות היא כדור (היא מצוירת ככדור בצבעים של הדמויות)"),
]
json.dump([{"i": i, "en": en, "ctx": "[t] AnimateDiff Music / Dance video (animatediff_ui.py)"} for i, (en, he) in enumerate(rows)],
          open("9_manual_animatediff_01.en.json", "w", encoding="utf8"), ensure_ascii=False, indent=1)
open("9_manual_animatediff_01.he.txt", "w", encoding="utf8").write("".join(f"{i}|{he}\n" for i, (en, he) in enumerate(rows)))

key = ("Upload up to three things, write the prompt in the usual place and press Generate: "
       "Dance video: real people dancing. Only their movement is taken, not how they look. "
       "The dancers: pictures of what the dancers are made of (a water drop, glass, spaghetti). Close-ups of the material work best. "
       "In the prompt, describe them as the material: two figures made entirely of spaghetti, rather than two dancers. "
       "The background (optional): pictures of the place behind the dancers (a jungle, a beach, a stage). They give the background "
       "its atmosphere and colors; the picture itself is not copied. Without them the background comes only from the prompt. "
       "Pick a picture taken from the same angle as the dance video (a stage filmed from the side needs a side view; a picture from above turns the whole scene into a top view). "
       "A picture of the place without the dancers' material works best (a bowl with sauce on a table, not a bowl full of spaghetti), so the dancers stand out. "
       "When the dancers are small in the frame, keep the picture plain (an empty table and wall): objects in it get repeated around the empty space. "
       "Length, music, size and ControlNet are set automatically; the checkpoint is the usual one. "
       "Your own ControlNet units are switched off while this is on.")
he = ('<p>מעלים עד שלושה דברים, כותבים את הפרומפט במקום הרגיל ולוחצים <b>Generate</b>:</p><ol>'
      '<li><b>סרטון ריקוד</b>: אנשים אמיתיים רוקדים. לוקחים ממנו רק את התנועה, לא את המראה שלהם.</li>'
      '<li><b>הרקדנים</b>: תמונות של ממה הרקדנים עשויים (טיפת מים, זכוכית, ספגטי). הכי טוב תקריב של החומר. בפרומפט כדאי לתאר אותם כחומר: <i>two figures made entirely of spaghetti</i>, ולא <i>two dancers</i>.</li>'
      '<li><b>הרקע</b> (לא חובה): תמונות של המקום שמאחורי הרקדנים (ג׳ונגל, חוף, במה). הן נותנות לרקע את האווירה והצבעים; '
      'התמונה עצמה לא מועתקת. בלעדיהן הרקע מגיע רק מהפרומפט. כדאי לבחור תמונה מאותה זווית כמו סרטון הריקוד (במה שצולמה מהצד צריכה תמונה מהצד; תמונה מלמעלה הופכת את כל הסצנה למבט מלמעלה). הכי טוב תמונה של המקום בלי החומר של הרקדנים (קערה עם רוטב על שולחן, לא קערה מלאה בספגטי), כדי שהרקדנים יבלטו. כשהרקדנים קטנים בפריים, עדיף תמונה נקייה (שולחן וקיר ריקים): חפצים שבתמונה חוזרים ומופיעים בשטח הריק.</li></ol>'
      '<p>האורך, המוזיקה, הגודל וה-ControlNet נקבעים אוטומטית; ה-checkpoint הוא הרגיל. '
      'יחידות ה-ControlNet שלך כבויות כל עוד זה פעיל.</p>')
json.dump([{"i": 0, "text": key, "html": ""}], open("9_manual_animatediff_blocks.en.json", "w", encoding="utf8"), ensure_ascii=False, indent=1)
json.dump({"0": he}, open("9_manual_animatediff_blocks.he.json", "w", encoding="utf8"), ensure_ascii=False, indent=1)

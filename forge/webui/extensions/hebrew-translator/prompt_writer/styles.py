"""What each model wants to read, and the instructions the writer gets for it.

A target is the kind of prompt to write: the field it goes into and the model behind it decide it (see
`target_for`). Everything here is plain text, so it can be read and tuned without touching the code.
"""

import re

TARGETS = {
    "sd15": "SD 1.5",
    "sdxl": "SDXL",
    "pony": "Pony (SDXL)",
    "booru": "Illustrious / anime (SDXL)",
    "flux": "FLUX",
    "wan_t2v": "Wan video, from text",
    "wan_i2v": "Wan video, from a picture",
    "fibo": "FIBO 2.0 (structured JSON)",
    "fibo_edit": "FIBO 2.0 edit",
    "negative": "Negative prompt",
}

# targets that write alike: a liked example of one teaches the others in its family a little
FAMILY = {"sd15": "tags", "sdxl": "mixed", "pony": "booru", "booru": "booru", "flux": "prose",
          "wan_t2v": "video", "wan_i2v": "video", "fibo": "json", "fibo_edit": "edit", "negative": "negative"}

CREATIVITY = {
    "Faithful": "Add as little as possible: translate the idea into clear prompt English and add only the words "
                "the target model needs (for example its style and quality words). Invent nothing.",
    "Rich": "Make the idea concrete and visual: how the subject looks, what exactly happens, where, the light, the "
            "framing and the style. Add only details that serve the idea; no new characters, objects or story.",
    "Creative": "Turn the idea into a striking picture: choose a strong composition, lighting, setting and style "
                "that make it memorable, and add details that enrich it. Never change what the user asked for.",
}

BASE = """You write prompts for AI image and video generators. The user describes an idea, usually in Hebrew (sometimes English or a mix). Understand it the way a native Hebrew speaker means it, including slang, idioms and words with two meanings, and write ONE prompt in English for the target model described below.

Rules:
- Keep everything the user asked for: the subject, the action, how many, colors, mood, place, style. Never drop a detail and never change the meaning.
- {creativity}
- If the user names a style (photo, anime, oil painting, 3D, Pixar...) keep it. If not, choose the style that suits the idea.
- Keep <lora:...> tags, (word:1.2) weights and English words the user wrote exactly as they are.
- Real people, brands and places keep their names, correctly spelled in English.

Answer in exactly two lines:
Meaning: a plain, literal English translation of the user's words, word by word, adding nothing (check each word: which animal, food, object or place it names).
Prompt: the prompt, built on that meaning. No quotes, no explanation, no Hebrew.

Target model: {target}
{guide}"""

GUIDES = {
    "sd15": """Stable Diffusion 1.5 reads short comma-separated phrases, and the first phrases matter most (about 75 tokens). Write 15 to 35 phrases on one line: the main subject and its action first, then details of the subject, the setting, the lighting, the camera and composition, the art style, and at the end a few quality words (masterpiece, best quality, highly detailed). No full sentences.""",
    "sdxl": """SDXL understands one short descriptive sentence followed by comma-separated details. Write one clear sentence about the subject and what it does, then 8 to 20 comma-separated details: setting, lighting, composition, style and a few quality words. One line, under 75 words.""",
    "pony": """A Pony Diffusion (SDXL) model trained on booru tags. Start with "score_9, score_8_up, score_7_up," then write comma-separated booru-style tags: the subject count (1girl, 1boy, 2girls, no humans...), the character's features, clothing, action and pose, the setting, the lighting, the framing (close-up, cowboy shot, full body...). One line, no sentences.""",
    "booru": """An Illustrious / NoobAI style anime model (SDXL) trained on booru tags. Start with "masterpiece, best quality," then comma-separated booru-style tags: the subject count (1girl, 1boy, solo, no humans...), features, clothing, action and pose, setting, lighting and framing. One line, no sentences.""",
    "flux": """FLUX reads natural language with a strong text encoder. Write 2 to 4 flowing sentences of plain descriptive English, 40 to 90 words: the subject and its action, how it looks, the setting, the light and the mood, then the camera or medium and the style. No tag lists, no weights, no quality words like "masterpiece". Text that must appear in the picture goes in double quotes.""",
    "wan_t2v": """Wan 2.2, a video model that makes a 5 second clip from text. Write one paragraph of 50 to 110 words in the present tense describing ONE continuous shot: who or what is in it and how it looks, then exactly what moves and how, in order, with clear motion verbs, then the camera (static, slow push-in, tracking shot, pan, orbit...), the setting, the light and the style. Describe motion over time, not a still picture. No cuts, no lists, no quality tags.""",
    "wan_i2v": """Wan 2.2, a video model that animates the user's own picture (the first frame is given). Write one paragraph of 40 to 90 words in the present tense: mostly what moves and how, in order, with clear motion verbs, and what the camera does (static, slow push-in, tracking shot, pan, orbit...). Name the subject briefly so the motion is clear, but do not describe the picture at length. ONE continuous shot: no cuts, no lists, no quality tags.""",
    "negative": """A negative prompt: things the picture must NOT contain. Translate what the user lists into short comma-separated English phrases on one line. Add nothing they did not ask for.""",
    # Bria's FIBO is trained on structured captions (VGL); the fields are the ones of its own prompt-to-JSON writer
    "fibo": """FIBO 2.0 (Bria) was trained on structured JSON captions and follows them far more exactly than plain text. After "Prompt:" write ONE valid JSON object (double quotes, no comments, no markdown) with these keys:
"short_description": one sentence with the whole picture;
"objects": a list, one entry per subject or important thing, each {"description", "location" (where in the frame), "relative_size", "appearance_details"};
"background_setting": the place and what is behind;
"lighting": {"conditions", "direction"};
"aesthetics": {"composition", "mood_atmosphere"};
"photographic_characteristics": {"camera_angle", "focus"};
"style_medium": photograph, oil painting, 3D render...;
"artistic_style": a few words.
Only when the user asks for written words in the picture, add "text_render": a list of {"text" (exactly as asked, in its own language), "location", "color", "font"}. No other keys.""",
    "fibo_edit": """FIBO 2.0 Edit changes the user's own picture. Write ONE direct English instruction in the imperative, 1 to 3 sentences: exactly what to change, how it should look after, and what must stay as it is ("keep the face, the pose and the background unchanged"). Do not describe the whole picture. When several reference pictures are used, call them <image_1>, <image_2>... in the user's order.""",
}

# One example per target, used while fewer than two liked examples of its own exist. The learned ones replace it.
EXAMPLES = {
    "sd15": ("ילדה ג'ינג'ית רצה בשדה חמניות בשקיעה",
             "a red-haired girl running through a sunflower field at sunset, long flowing red hair, summer dress, "
             "tall sunflowers all around, golden hour, warm backlight, lens flare, wide shot, photorealistic, "
             "highly detailed, masterpiece, best quality"),
    "sdxl": ("ילדה ג'ינג'ית רצה בשדה חמניות בשקיעה",
             "A red-haired girl running through a field of tall sunflowers at sunset, long hair flowing behind her, "
             "golden hour backlight, soft lens flare, wide shot, cinematic photograph, warm saturated colors, "
             "sharp focus, highly detailed"),
    "pony": ("ילדה ג'ינג'ית רצה בשדה חמניות בשקיעה",
             "score_9, score_8_up, score_7_up, 1girl, solo, red hair, long hair, running, sundress, sunflower field, "
             "sunset, golden hour, backlighting, lens flare, wide shot, full body"),
    "booru": ("ילדה ג'ינג'ית רצה בשדה חמניות בשקיעה",
              "masterpiece, best quality, 1girl, solo, red hair, long hair, running, sundress, sunflower field, "
              "sunset, golden hour, backlighting, lens flare, wide shot, full body"),
    "flux": ("ילדה ג'ינג'ית רצה בשדה חמניות בשקיעה",
             "A red-haired girl runs through a field of tall sunflowers at sunset, her long hair streaming behind "
             "her. The low sun sits behind her, rimming her hair with golden light and casting long shadows across "
             "the flowers. A wide, cinematic photograph with a soft lens flare and warm, saturated colors."),
    "wan_t2v": ("איש עושה עמידת ידיים על החוף",
                "A man in swim shorts stands on a sunny beach, bends down, places his hands on the wet sand and "
                "kicks his legs up into a steady handstand, his body straight and his toes pointing at the sky. He "
                "holds the pose while small waves roll in behind him. The camera stays at eye level and slowly "
                "circles to his side. Bright midday sun, clear blue sky, realistic, natural motion."),
    "wan_i2v": ("הילדה מסתובבת אל המצלמה ומחייכת, הרוח מזיזה לה את השיער",
                "The girl slowly turns her head toward the camera and breaks into a warm smile, while a light "
                "breeze lifts strands of her hair across her face. The flowers behind her sway gently. The camera "
                "holds steady, then pushes in slightly toward her face. Smooth, natural motion."),
    "negative": ("ידיים מעוותות, מטושטש, טקסט, סימן מים",
                 "deformed hands, blurry, text, watermark"),
    "fibo": ("ילדה ג'ינג'ית רצה בשדה חמניות בשקיעה",
             '{"short_description": "A red-haired girl runs through a field of tall sunflowers at sunset.", '
             '"objects": [{"description": "a young girl with long red hair in a light summer dress, running", '
             '"location": "center", "relative_size": "medium", "appearance_details": "hair streaming behind her, '
             'arms slightly open, joyful"}, {"description": "tall sunflowers", "location": "all around and in the '
             'foreground", "relative_size": "large", "appearance_details": "golden petals catching the low sun"}], '
             '"background_setting": "an open sunflower field reaching to the horizon under a warm evening sky", '
             '"lighting": {"conditions": "golden hour sunset", "direction": "backlit from behind the girl"}, '
             '"aesthetics": {"composition": "wide shot, the girl in the center third", "mood_atmosphere": '
             '"carefree, warm, nostalgic"}, "photographic_characteristics": {"camera_angle": "eye level", '
             '"focus": "sharp on the girl, the far field softly blurred"}, "style_medium": "photograph", '
             '"artistic_style": "cinematic, warm saturated colors, soft lens flare"}'),
    "fibo_edit": ("תצבע את המכונית בצהוב",
                  "Paint the car bright yellow. Keep the road, the mountains, the lighting, the camera angle and "
                  "every other detail unchanged."),
}


# the literal meaning of each built-in example (the model writes one before every prompt)
MEANINGS = {
    "ילדה ג'ינג'ית רצה בשדה חמניות בשקיעה": "A red-haired girl runs in a sunflower field at sunset.",
    "איש עושה עמידת ידיים על החוף": "A man does a handstand on the beach.",
    "הילדה מסתובבת אל המצלמה ומחייכת, הרוח מזיזה לה את השיער":
        "The girl turns to the camera and smiles; the wind moves her hair.",
    "ידיים מעוותות, מטושטש, טקסט, סימן מים": "Deformed hands, blurry, text, watermark.",
    "תצבע את המכונית בצהוב": "Paint the car yellow.",
}

def answer(meaning, prompt):
    """An example answer in the two-line form the model is asked for."""
    return f"Meaning: {meaning}\nPrompt: {prompt}" if meaning else f"Prompt: {prompt}"


def example(target):
    source, prompt = EXAMPLES[target]
    return source, answer(MEANINGS.get(source, ""), prompt)


def parse(text):
    """(meaning, prompt) from the model's two lines; an answer without labels is all prompt."""
    text = (text or "").strip()
    m = re.search(r"^\s*meaning\s*:\s*(.*)$", text, re.I | re.M)
    meaning = m.group(1).strip() if m else ""
    p = re.search(r"^\s*(?:english\s+)?prompt\s*:\s*", text, re.I | re.M)
    if p:
        prompt = text[p.end():]
    elif m:
        prompt = text[:m.start()] + text[m.end():]
    else:
        prompt = text
    return meaning, prompt.strip()


def system_prompt(target, creativity="Rich", notes=(), avoid=(), words=()):
    """The writer's instructions for `target`, with the user's word lessons and notes from earlier ratings, and
    versions he disliked."""
    text = BASE.format(creativity=CREATIVITY.get(creativity, CREATIVITY["Rich"]), target=TARGETS[target],
                       guide=GUIDES[target])
    if target == "negative":
        # a list of things to avoid has nothing to enrich
        text = text.replace(CREATIVITY.get(creativity, CREATIVITY["Rich"]), CREATIVITY["Faithful"])
    if words:
        text += ("\n\nWords the user taught you (they are in this request; translate them exactly so):\n"
                 + "\n".join(f"- {he} = {en}" for he, en in words))
    if notes:
        text += "\n\nNotes the user gave on earlier prompts (follow them):\n" + "\n".join(f"- {n}" for n in notes)
    if avoid:
        # worded with care: "write differently" alone made the model change the subject (a fox became a wolf)
        text += ("\n\nThe user rated these earlier prompts for the same idea as weak. Translate the idea afresh from "
                 "the user's own words and keep its meaning and subject exactly; improve the way it is written:\n"
                 + "\n".join(f"- {a}" for a in avoid))
    return text


# ---------------------------------------------------------------------- which target

_BOORU = re.compile(r"illustrious|noob|ilxl|animagine|\bwai", re.I)


def target_for(field, architecture="", checkpoint="", video_mode=""):
    """The target for a prompt field. `architecture` is "sd15", "sdxl", "flux" (read from the checkpoint's
    weights) or "fibo" (a FIBO 2.0 checkpoint of sd-forge-fibo2), `video_mode` the open Video Studio sub-tab."""
    field = field or ""
    if field.startswith("vs_part"):
        return "wan_i2v"  # a part runs between two of the user's pictures
    if field.startswith("vs_"):
        return "wan_i2v" if video_mode == "i2v" else "wan_t2v"
    if "neg" in field:
        return "negative"
    if architecture == "fibo":
        # img2img and Edit Turbo always edit a picture; Dev in txt2img draws a new one
        edit = field.startswith("img2img") or re.search(r"edit", checkpoint or "", re.I)
        return "fibo_edit" if edit else "fibo"
    if architecture == "flux":
        return "flux"
    if architecture == "sdxl":
        name = checkpoint or ""
        if re.search(r"pony", name, re.I):
            return "pony"
        if _BOORU.search(name):
            return "booru"
        return "sdxl"
    return "sd15"


# ---------------------------------------------------------------------- cleaning the answer

_LORA = re.compile(r"<(?:lora|lyco|hypernet):[^>]+>", re.I)


def split_tags(text):
    """(text without <lora:...> tags, the tags). The tags go back on unchanged after writing."""
    tags = _LORA.findall(text or "")
    return _LORA.sub(" ", text or "").strip(), tags


def tidy_json(text):
    """FIBO's caption as JSON, laid out to be read and edited in the prompt box. A model answer that is not valid
    JSON raises ValueError (sd-forge-fibo2 refuses a prompt that starts with "{" and does not parse)."""
    import json
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError(f"the writer gave no JSON caption: {text[:120]!r}")
    value = json.loads(text[start:end + 1])
    if not isinstance(value, dict) or not isinstance(value.get("short_description"), str):
        raise ValueError("the writer's JSON caption has no short_description")
    return json.dumps(value, ensure_ascii=False, indent=1)


def tidy(text, target, tags=()):
    """The prompt part of the model's answer, clean: no quotes, labels or line breaks, the user's LoRA tags at the
    end."""
    text = parse(text)[1]
    if target == "fibo":
        return tidy_json(text)
    if len(text) > 1 and text[0] == text[-1] and text[0] in "\"'`":
        text = text[1:-1].strip()
    tag_like = FAMILY[target] in ("tags", "mixed", "booru", "negative")
    text = re.sub(r"\s*\n+\s*", ", " if tag_like and FAMILY[target] != "mixed" else " ", text)
    text = re.sub(r"[ \t]{2,}", " ", text).strip()
    if tag_like:
        # "fox in snow:1.2, ..." -> "(fox in snow:1.2), ...": a weight without its brackets is read as words
        text = re.sub(r"(^|,\s*)([A-Za-z][^,():]*?):(\d(?:\.\d+)?)(?=\s*(?:,|$))", r"\1(\2:\3)", text)
    if tags:
        missing = [t for t in tags if t.lower() not in text.lower()]
        if missing:
            text = f"{text} {' '.join(missing)}".strip()
    return text

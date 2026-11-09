"""The character sheet: the prompt around a character, and FLUX 3 Klein drawing it over the pose template.

The way that worked for Shai (2026-11-09): FLUX 3 Klein img2img at strength 1, "edit with reference", with an
OpenPose sheet (front / side / back, rows of heads, a close-up) as the reference and a JSON prompt naming the
character and asking for every view of it. Klein keeps the layout and draws one consistent character in it.
"""

import json
import os
import re

from . import BUILTIN_TEMPLATE, SETTINGS_FILE

# The prompt around the character. {appearance}, {style}, {avoid} and {reference} are filled in (JSON-escaped);
# the user can change the rest in the tab (Sheet prompt).
DEFAULT_TEXT = """{
 "prompt": "A professional character reference sheet featuring {appearance} {style}. {reference}Multiple consistent views following the exact uploaded OpenPose reference: front, side, back, facial rotations and full-body poses. Identical face, outfit, proportions and accessories in every view. Clean light gray studio background, soft professional lighting.",
 "style": "{style}",
 "reference": "Strictly follow the uploaded pose sheet layout and skeleton positions",
 "consistency": "Same character identity, facial structure, clothing and proportions across every angle",
 "negative_prompt": "{avoid}"
}"""

REFERENCE_LINE = ("The layout and every pose come only from the first reference picture, the pose sheet: each skeleton "
                  "in it becomes this character, the full-body views, every small head in the rows of heads and the "
                  "large close-up. The character's look comes from the second reference picture: the same face, hair, "
                  "skin tone, build, clothes and accessories. ")
DEFAULT_STYLE = "Premium stylized 3D animation, polished cinematic rendering"
DEFAULT_AVOID = ("different faces, inconsistent clothing, extra limbs, distorted anatomy, skeleton overlays, text, "
                 "cropped body")
KEYS = ("name", "summary", "appearance", "style", "avoid")
# A reference picture and no words: the picture is the character, look and style alike
FROM_REFERENCE = {
    "name": "", "summary": "The character from the reference picture.",
    "appearance": "the character shown in the second reference picture, with exactly that face, hair, skin tone, "
                  "body, clothes and accessories.",
    "style": "The same look and rendering style as the second reference picture",
    "avoid": DEFAULT_AVOID}


# ---------------------------------------------------------------------- the character

def parse_character(text):
    """The character dict from the tab's box (the writer's JSON), or None when the box holds plain words."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip())
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        value = json.loads(text[start:end + 1])
    except ValueError:
        return None
    if not isinstance(value, dict) or not str(value.get("appearance") or "").strip():
        return None
    return {k: str(value.get(k) or "").strip() for k in KEYS}


def _esc(text):
    return json.dumps(str(text))[1:-1]


def compose(character, reference=False, text=None):
    """The sheet prompt: the template text with the character's appearance, style and things to avoid in it."""
    style = (character.get("style") or DEFAULT_STYLE).strip().rstrip(".")
    appearance = (character.get("appearance") or "").strip()
    if appearance and appearance[-1] not in ".!?":
        appearance += "."
    avoid = character.get("avoid") or DEFAULT_AVOID
    out = text if (text or "").strip() else DEFAULT_TEXT
    for key, value in (("{appearance}", appearance), ("{style}", style), ("{avoid}", avoid),
                       ("{reference}", REFERENCE_LINE if reference else "")):
        out = out.replace(key, _esc(value))
    return out


def is_english(text):
    """True when the text has no letters outside Latin (so it needs no writer)."""
    return not any(ch.isalpha() and ord(ch) > 0x24F for ch in text or "")


# ---------------------------------------------------------------------- settings

def settings():
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(**changes):
    data = settings()
    for key, value in changes.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def default_template():
    path = settings().get("template")
    return path if path and os.path.isfile(path) else BUILTIN_TEMPLATE


def sheet_text():
    return settings().get("sheet_text") or DEFAULT_TEXT


# ---------------------------------------------------------------------- drawing

def fit16(width, height, area=1024 * 1024):
    scale = (area / (width * height)) ** 0.5
    return max(256, round(width * scale / 16) * 16), max(256, round(height * scale / 16) * 16)


def flux3_glue():
    """The FLUX 3 extension's glue when FLUX 3 is the checkpoint selected at the top of the page, else None."""
    try:
        from flux3 import glue
        from modules import shared
        return glue if glue.active(shared.sd_model) else None
    except Exception:
        return None


def draw(prompt, template, reference, seed, steps=16):
    """One sheet (a PIL picture) by FLUX 3 Klein: the template, and the reference picture when there is one, are
    its references; the size follows the template's shape."""
    from PIL import Image
    from modules import processing, shared
    glue = flux3_glue()
    if glue is None:
        raise RuntimeError("Select FLUX 3 Klein at the top of the page: it draws the character sheets.")
    refs = []
    for path in [template] + ([reference] if reference else []):
        with Image.open(path) as im:
            refs.append(im.convert("RGB"))
    w, h = fit16(*refs[0].size)
    p = processing.StableDiffusionProcessingImg2Img(
        sd_model=shared.sd_model, init_images=[refs[0]], denoising_strength=1.0, prompt=prompt, negative_prompt="",
        width=w, height=h, steps=int(steps), cfg_scale=1.0, sampler_name="Euler", seed=int(seed),
        do_not_save_samples=True, do_not_save_grid=True)
    p.flux3_edit_mode = glue.EDIT_MODES[0]
    p.flux3_references = refs
    try:
        res = processing.process_images(p)
    finally:
        p.close()
    return res.images[0]

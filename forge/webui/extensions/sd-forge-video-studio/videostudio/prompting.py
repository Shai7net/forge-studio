"""Styles and LoRA tags for Video Studio, the way txt2img has them.

Styles are Forge's own (shared.prompt_styles, styles.csv): the same list as txt2img, applied the same way
({prompt} placeholder or appended). A `<lora:name:weight>` tag in the prompt or in a style becomes a Wan LoRA when
a Wan LoRA file of that name exists; every tag is taken out of what Wan reads (its text encoder would read it as
words), and the trigger words around it stay. A LoRA trained as a pair (`..._high_noise` / `..._low_noise`) is
loaded as a pair whichever half was chosen, each half on its own expert.
"""

import os
import re

from . import models

LORA_TAG = re.compile(r"<lora:([^:>]+)(?::([-0-9.]+))?(?::[^>]*)?>", re.I)
PAIR = re.compile(r"(?i)([_\- ]?)(high|low)([_\- ]?noise)")


def style_names():
    try:
        from modules import shared
        return list(shared.prompt_styles.styles)
    except Exception:
        return []


def apply_styles(prompt, negative, styles):
    if not styles:
        return prompt, negative
    from modules import shared
    bridge = getattr(shared.prompt_styles, 'apply_styles_for_model', None)
    if bridge:
        return bridge(prompt, negative, styles, 'video')
    return (shared.prompt_styles.apply_styles_to_prompt(prompt, styles),
            shared.prompt_styles.apply_negative_styles_to_prompt(negative, styles))


def save_style(name, prompt, negative):
    """Adds or replaces a style in Forge's styles.csv (the txt2img list too)."""
    from modules import shared, styles
    name = (name or "").strip()
    if not name:
        raise ValueError("Give the style a name.")
    db = shared.prompt_styles
    old = db.styles.get(name)
    path = old.path if old is not None and old.path else str(db.default_path)
    db.styles[name] = styles.PromptStyle(name, prompt or "", negative or "", path)
    db.save_styles()
    return name


def _norm(name):
    return re.sub(r"[^a-z0-9]+", "", os.path.splitext(os.path.basename(name))[0].lower())


def lora_files():
    return {os.path.basename(p): p for p in models.loras()}


def find_lora(name):
    """A Wan LoRA file for a tag's name (case, spaces and punctuation ignored), or None."""
    want = _norm(name)
    for base, path in lora_files().items():
        if _norm(base) == want:
            return path
    return None


def partner(path):
    """The other half of a high/low-noise pair, if it is there."""
    base = os.path.basename(path)
    m = PAIR.search(base)
    if not m:
        return None
    other = "low" if m.group(2).lower() == "high" else "high"
    word = other.upper() if m.group(2).isupper() else (other.capitalize() if m.group(2)[0].isupper() else other)
    candidate = base[:m.start(2)] + word + base[m.end(2):]
    for name, p in lora_files().items():
        if name.lower() == candidate.lower():
            return p
    return None


def split_loras(prompt):
    """(prompt for Wan with every <lora:...> tag removed, [(file, strength)] for the tags that are Wan LoRAs,
    [names of tags that are not])."""
    found, unknown = [], []
    for m in LORA_TAG.finditer(prompt or ""):
        path = find_lora(m.group(1))
        weight = float(m.group(2)) if m.group(2) else 1.0
        if path:
            found.append((path, weight))
        else:
            unknown.append(m.group(1))
    clean = LORA_TAG.sub(" ", prompt or "")
    clean = re.sub(r"\s*,\s*(,\s*)+", ", ", clean)
    clean = re.sub(r"[ \t]{2,}", " ", clean).strip(" ,\n")
    return clean, found, unknown


def with_partners(loras):
    """[{path, strength}] with the missing half of every high/low pair added (same strength), no duplicates."""
    out, seen = [], set()
    for item in loras:
        for path in (item["path"], partner(item["path"])):
            if path and os.path.normcase(path) not in seen:
                seen.add(os.path.normcase(path))
                out.append({"path": path, "strength": item["strength"]})
    return out

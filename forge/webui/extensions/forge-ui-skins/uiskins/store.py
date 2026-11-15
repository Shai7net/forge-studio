"""Skins and the card layout on disk (2026-11-15).

A skin is how the user re-dressed Forge's buttons, tabs and cards: webui/ui-skins/<folder>/skin.json plus the pictures
put on buttons. "Classic" is no skin at all (Forge as it is) and is never written. The card layout (the order of the
extension cards in txt2img / img2img and which ones went into "More") is the user's own, kept apart from the skins in
webui/ui-skins/layout.json, so switching skins never moves cards.

skin.json: {"name", "rules": {key: {bg, color, image, fit, radius, size, bold, hidden, text, shadow}}, "made", "changed"}
A key says which element: "#elem_id", "tab:<tabs id>:<English text>", "card:<English title>" or a CSS path.
"""
import base64
import json
import os
import re
import shutil
import time

CLASSIC = "Classic"
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
               ".gif": "image/gif", ".svg": "image/svg+xml"}
MAX_IMAGE = 8 * 1024 * 1024
STYLE_KEYS = {"bg", "color", "image", "fit", "radius", "size", "bold", "hidden", "text", "shadow", "border"}
_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
ROOT = None


class SkinError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def root():
    global ROOT
    if ROOT is None:
        here = os.path.dirname(os.path.abspath(__file__))
        ROOT = os.path.normpath(os.path.join(here, "..", "..", "..", "ui-skins"))   # webui/ui-skins
    os.makedirs(ROOT, exist_ok=True)
    return ROOT


def clean_name(name):
    name = _BAD.sub("", str(name or "")).strip().strip(".")
    if not name:
        raise SkinError("a skin needs a name")
    if name.lower() == CLASSIC.lower():
        raise SkinError("Classic is Forge as it is: choose another name")
    return name[:60]


def _folder(name, create=False):
    folder = os.path.join(root(), clean_name(name))
    if not create and not os.path.isfile(os.path.join(folder, "skin.json")):
        raise SkinError(f"no skin named {name}", 404)
    os.makedirs(folder, exist_ok=True)
    return folder


def _write_json(path, value):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def clean_rules(rules):
    """Only known style fields with plain values; empty entries dropped."""
    out = {}
    for key, style in (rules or {}).items():
        if not isinstance(key, str) or not key or len(key) > 400 or not isinstance(style, dict):
            continue
        kept = {}
        for k, v in style.items():
            if k not in STYLE_KEYS or v in (None, "", False):
                continue
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                kept[k] = v
            elif isinstance(v, bool):
                kept[k] = v
            else:
                kept[k] = str(v)[:300]
        if kept:
            out[key] = kept
    return out


def listing():
    skins = []
    for entry in sorted(os.listdir(root()), key=str.lower):
        path = os.path.join(root(), entry, "skin.json")
        if os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                skins.append({"name": entry, "rules": len(data.get("rules") or {}), "changed": data.get("changed", 0)})
            except (OSError, ValueError):
                continue
    return skins


def names():
    return [CLASSIC] + [s["name"] for s in listing()]


def load(name):
    if not name or name == CLASSIC:
        return {"name": CLASSIC, "rules": {}}
    with open(os.path.join(_folder(name), "skin.json"), encoding="utf-8") as f:
        data = json.load(f)
    data["name"] = clean_name(name)
    data["rules"] = clean_rules(data.get("rules"))
    return data


def save(name, rules, copy_from=None):
    """Writes the skin (a new one, or over itself). `copy_from`: the skin a "Save as" started from, whose pictures the
    new skin needs too."""
    name = clean_name(name)
    folder = _folder(name, create=True)
    path = os.path.join(folder, "skin.json")
    made = time.time()
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                made = json.load(f).get("made", made)
        except (OSError, ValueError):
            pass
    rules = clean_rules(rules)
    if copy_from and copy_from not in (CLASSIC, name):
        try:
            source = _folder(copy_from)
            for style in rules.values():
                image = style.get("image", "")
                if image and not image.startswith(("http:", "https:", "data:")):
                    src = os.path.join(source, os.path.basename(image))
                    if os.path.isfile(src) and not os.path.isfile(os.path.join(folder, os.path.basename(image))):
                        shutil.copy2(src, folder)
        except SkinError:
            pass
    _write_json(path, {"name": name, "rules": rules, "made": made, "changed": time.time()})
    return {"name": name, "rules": len(rules)}


def store_image(name, filename, data):
    """A picture for a button: kept in the skin's folder (a skin not saved yet keeps it in _drafts until it is)."""
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in IMAGE_TYPES:
        raise SkinError("pictures only: png, jpg, webp, gif or svg")
    if not data or len(data) > MAX_IMAGE:
        raise SkinError("the picture is empty or larger than 8 MB")
    folder = os.path.join(root(), "_drafts") if not name or name == CLASSIC else _folder(name, create=True)
    os.makedirs(folder, exist_ok=True)
    stem = re.sub(r"[^A-Za-z0-9_-]", "_", os.path.splitext(os.path.basename(filename))[0])[:40] or "picture"
    target = f"{stem}-{int(time.time() * 1000) % 10 ** 8}{ext}"
    with open(os.path.join(folder, target), "wb") as f:
        f.write(data)
    return target


def image_path(name, filename):
    base = os.path.basename(filename or "")
    if os.path.splitext(base)[1].lower() not in IMAGE_TYPES:
        raise SkinError("not a picture", 404)
    for folder in ([os.path.join(root(), clean_name(name))] if name and name not in (CLASSIC, "_drafts") else []) + \
            [os.path.join(root(), "_drafts")]:
        path = os.path.join(folder, base)
        if os.path.isfile(path):
            return path
    raise SkinError("no such picture", 404)


def adopt_drafts(name, rules):
    """Pictures put on buttons before the skin had a name move into it when it is saved."""
    drafts = os.path.join(root(), "_drafts")
    folder = _folder(name, create=True)
    for style in (rules or {}).values():
        image = os.path.basename(style.get("image", "") or "")
        src = os.path.join(drafts, image)
        if image and os.path.isfile(src) and not os.path.isfile(os.path.join(folder, image)):
            shutil.move(src, os.path.join(folder, image))


def rename(name, to):
    src, to = _folder(name), clean_name(to)
    target = os.path.join(root(), to)
    if os.path.exists(target):
        raise SkinError(f"a skin named {to} already exists", 409)
    os.replace(src, target)
    data = load(to)
    data["name"] = to
    _write_json(os.path.join(target, "skin.json"), data)
    return to


def delete(name):
    """Moved aside to ui-skins/_trash (nothing is erased)."""
    src = _folder(name)
    trash = os.path.join(root(), "_trash")
    os.makedirs(trash, exist_ok=True)
    target = os.path.join(trash, f"{os.path.basename(src)}-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.move(src, target)
    return target


def export(name):
    """One file to share: the skin with its pictures inside (base64)."""
    data = load(name)
    pictures = {}
    folder = _folder(name)
    for style in data["rules"].values():
        image = os.path.basename(style.get("image", "") or "")
        path = os.path.join(folder, image)
        if image and os.path.isfile(path):
            with open(path, "rb") as f:
                pictures[image] = base64.b64encode(f.read()).decode("ascii")
    return {"forge_ui_skin": 1, "name": data["name"], "rules": data["rules"], "pictures": pictures}


def import_skin(payload):
    if not isinstance(payload, dict) or payload.get("forge_ui_skin") != 1:
        raise SkinError("this is not a Forge UI skin file")
    base = clean_name(payload.get("name") or "Imported skin")
    name, n = base, 2
    while os.path.exists(os.path.join(root(), name)):
        name, n = f"{base} ({n})", n + 1
    folder = _folder(name, create=True)
    for picture, b64 in (payload.get("pictures") or {}).items():
        base_name = os.path.basename(picture)
        if os.path.splitext(base_name)[1].lower() in IMAGE_TYPES:
            raw = base64.b64decode(b64)
            if len(raw) <= MAX_IMAGE:
                with open(os.path.join(folder, base_name), "wb") as f:
                    f.write(raw)
    save(name, payload.get("rules"))
    return name


# ---- the cards' layout (not part of a skin)

def layout():
    path = os.path.join(root(), "layout.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_layout(data):
    clean = {}
    for tab, value in (data or {}).items():
        if not isinstance(tab, str) or not isinstance(value, dict):
            continue
        clean[tab[:40]] = {"order": [str(k)[:200] for k in value.get("order", [])][:300],
                           "more": [str(k)[:200] for k in value.get("more", [])][:300]}
    _write_json(os.path.join(root(), "layout.json"), clean)
    return clean

"""Interface languages besides English and the built-in Hebrew.

The translation kit (translation-kit/part-*.json) holds every English text the interface shows, in parts small enough
for an AI chat to translate in one answer. Somebody gives the parts to their AI, and the files it returns go into
languages/<code>/ (or through the page's "Add a language" window); scripts/ui_languages.py serves them to the page.

Used by tools/build_dictionary.py (writes the kit) and scripts/ui_languages.py (reads the languages). No Forge imports.
"""
import html
import io
import json
import os
import re
import zipfile

KIT_FORMAT = 1
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRANSLATIONS = os.path.join(ROOT, "translations")
KIT = os.path.join(ROOT, "translation-kit")
LANGUAGES = os.path.join(ROOT, "languages")
EXTENSIONS = os.path.dirname(ROOT)
PART_CHARS = 16000          # English characters in one part: an answer of about 8-10 thousand tokens
BUILT_IN = ("en", "he")

# Pages that draw their own words (they sit in .hebrew-ui-skip) keep them in a JavaScript table: English -> Hebrew.
# The kit takes the English side, and the page asks window.hebrewUi.text() for a language that is not Hebrew.
JS_TABLES = [
    ("Simulation 360", "sd-forge-simulation-360/javascript/sim360-3-strings.js", "S.he = {", "keys"),
    ("Simulation 360", "sd-forge-simulation-360/javascript/sim360-3-strings.js", "S.helpText", "list"),
    ("Forge Studio editor", "sd-forge-studio/javascript/forge-studio-b-strings.js", "FS.he = {", "keys"),
    ("Time Strata", "sd-forge-time-strata/javascript/strata-3-strings.js", "S.he = {", "keys"),
    ("Prompt writer", "hebrew-translator/javascript/translator.js", "const TEXT = {", "pairs"),
]
AREAS = [("0_", "Main tabs"), ("1_", "Main tabs"), ("2_", "Deforum"), ("3_", "Infinite Zoom"), ("4_", "Other tabs"),
         ("5_", "Settings"), ("9_", "Forge Studio tabs")]

INSTRUCTIONS = [
    "This file is one part of the interface of Forge Studio, an AI image and video app (Stable Diffusion WebUI Forge).",
    "Translate every value in \"texts\" (and in \"blocks\", when there is one) into the language you were asked for. "
    "Never change a key: the program finds each text by its English key.",
    "Fill in \"language\" with the language's own name (for example \"Español\"), \"code\" with its short code "
    "(for example \"es\" or \"pt-br\"), and set \"direction\" to \"rtl\" for a language written right to left.",
    "Keep exactly as they are: {0}, {name} and other words in curly braces, %s, ${...}, HTML tags and their "
    "attributes, emoji, \\n line breaks, numbers and units, keyboard keys, file names and paths, and code.",
    "Keep the names of models, samplers, programs and formats in English (FLUX, SDXL, SD 1.5, LoRA, ControlNet, "
    "AnimateDiff, Deforum, Wan, VAE, CFG, Euler a, LCM, Hires. fix, PNG, MP4...). For a technical term the user will "
    "meet in English tutorials, add the English in brackets once, for example \"Escala CFG (CFG Scale)\".",
    "Short labels stay short: they are buttons, tabs and field names. Use the plain, friendly words a video editor or "
    "a photo app uses in that language.",
    "A value that should stay in English may stay unchanged.",
    "Return the complete file as valid JSON with the same structure and nothing before or after it.",
]

PROMPT = ("Translate this Forge Studio interface file into {language}. Follow the \"instructions\" inside the file "
          "exactly. Return the complete file as valid JSON (same keys, every value translated), with nothing before "
          "or after it.")

RTL_LETTERS = re.compile(r"[֐-ࣿיִ-﷿ﹰ-﻿]")   # Hebrew, Arabic, Syriac, Thaana, N'Ko...
LATIN_OR_RTL = re.compile(r"[A-Za-z]|[֐-ࣿיִ-﷿ﹰ-﻿]")
LRM, RLM = "‎", "‏"
SIGN_RUN = re.compile(r"[-+]+(?=[0-9A-Za-z.])")


# ------------------------------------------------------------------------------------------ right to left text

def mark_signed_numbers(text):
    """After right-to-left text, "-1" or "--neg" is drawn with the sign on the wrong side; a left-to-right mark keeps
    it in front."""
    out, last = [], 0
    for match in SIGN_RUN.finditer(text):
        start = match.start()
        j = start - 1
        while j >= 0 and text[j] == " ":
            j -= 1
        previous = text[j] if j >= 0 else ""
        joined_to_word = j == start - 1 and (previous.isalnum() or previous in ("_", LRM))  # גובה-רוחב, ל-LeReS, 5-15
        if not joined_to_word and not previous.isdigit() and previous not in (")", "]"):
            out.append(text[last:start])
            out.append(LRM)
            last = start
    out.append(text[last:])
    return "".join(out)


def display_form(text):
    """The browser takes each line's direction from its first letter, so a right-to-left line that opens with an
    English word (for example "SAM אוטומטי") gets a right-to-left mark in front."""
    if not RTL_LETTERS.search(text):
        return text
    lines = []
    for line in mark_signed_numbers(text).split("\n"):
        first = LATIN_OR_RTL.search(line)
        if first and not RTL_LETTERS.match(first.group()):
            line = RLM + line
        lines.append(line)
    return "\n".join(lines)


# ------------------------------------------------------------------------------------------ what the kit holds

def _js_strings(source, start):
    """(position, value, next significant character) for every string literal from `start` to the end of the
    statement that `start` opens (the brackets balance again)."""
    i, depth, out = start, 0, []
    while i < len(source):
        c = source[i]
        if c in "'\"`":
            j, buf = i + 1, []
            while j < len(source) and source[j] != c:
                if source[j] == "\\" and j + 1 < len(source):
                    nxt = source[j + 1]
                    buf.append({"n": "\n", "t": "\t", "\\": "\\", "'": "'", '"': '"', "`": "`"}.get(nxt, "\\" + nxt))
                    j += 2
                    continue
                buf.append(source[j])
                j += 1
            k = j + 1
            while k < len(source) and source[k] in " \t\r\n":
                k += 1
            prev = i - 1
            while prev >= 0 and source[prev] in " \t\r\n":
                prev -= 1
            out.append(("".join(buf), source[k] if k < len(source) else "", source[prev] if prev >= 0 else ""))
            i = j + 1
            continue
        if c == "/" and source[i + 1:i + 2] == "/":
            i = source.find("\n", i)
            i = len(source) if i < 0 else i
            continue
        if c == "/" and source[i + 1:i + 2] == "*":
            i = source.find("*/", i + 2)
            i = len(source) if i < 0 else i + 2
            continue
        if c in "[{(":
            depth += 1
        elif c in "]})":
            depth -= 1
            if depth == 0:
                break
        elif c == ";" and depth == 0 and i > start:
            break
        i += 1
    return out


def js_table_texts(path, marker, kind):
    try:
        source = open(path, encoding="utf-8").read()
    except OSError:
        return []
    at = source.find(marker)
    if at < 0:
        return []
    if kind == "list":   # S.helpText = () => hebrew ? [...] : [English...]: the second list
        at = source.find(":", source.find("?", at))
        at = source.find("[", at)
    else:
        at = source.find("{", at)
    found = _js_strings(source, at)
    if kind == "keys":
        texts = [s for s, nxt, _ in found if nxt == ":" and not re.fullmatch(r"help-\d+", s)]
    elif kind == "pairs":
        texts = [s for s, _, prev in found if prev == "["]
    else:
        texts = [s for s, _, _ in found]
    return texts


def _letters(text):
    return any(ch.isalpha() for ch in text)


def kit_sources():
    """[(area, kind, key, english)] in the order the kit is cut into parts. Texts the Hebrew interface keeps in
    English on purpose (model names and the like) are left out."""
    rows, seen = [], set()

    def area_of(name):
        for prefix, label in AREAS:
            if name.startswith(prefix):
                return label
        return "Other tabs"

    def take(area, kind, key, english):
        if key in seen or not _letters(english):
            return
        seen.add(key)
        rows.append((area, kind, key, english))

    names = sorted(os.path.basename(p)[:-len(".en.json")] for p in os.listdir(TRANSLATIONS) if p.endswith(".en.json"))
    for name in names:
        if name.endswith("_blocks"):
            continue
        source = json.load(open(os.path.join(TRANSLATIONS, name + ".en.json"), encoding="utf-8"))
        translated = os.path.join(TRANSLATIONS, name + ".he.txt")
        kept = set()
        if os.path.exists(translated):
            for line in open(translated, encoding="utf-8"):
                index, _, value = line.rstrip("\r\n").partition("|")
                if value == "=" and index.strip().isdigit():
                    kept.add(int(index))
        for row in source:
            if row["i"] not in kept:
                take(area_of(name), "text", row["en"], row["en"])
    for area, rel, marker, kind in JS_TABLES:
        for text in js_table_texts(os.path.join(EXTENSIONS, rel), marker, kind):
            take(area, "text", text, text)
    for name in names:
        if not name.endswith("_blocks"):
            continue
        source = json.load(open(os.path.join(TRANSLATIONS, name + ".en.json"), encoding="utf-8"))
        target = os.path.join(TRANSLATIONS, name + ".he.json")
        hebrew = json.load(open(target, encoding="utf-8")) if os.path.exists(target) else {}
        for row in source:
            if hebrew.get(str(row["i"]), "=") == "=":
                continue
            take("Help texts", "block", f"{name}#{row['i']}", row.get("html") or html.escape(row["text"]))
    return rows


def block_keys():
    """Kit block id ("<batch>#<i>") -> the key the page looks a help block up by (its English text)."""
    keys = {}
    for p in os.listdir(TRANSLATIONS):
        if p.endswith("_blocks.en.json"):
            name = p[:-len(".en.json")]
            for row in json.load(open(os.path.join(TRANSLATIONS, p), encoding="utf-8")):
                keys[f"{name}#{row['i']}"] = row["text"]
    return keys


def write_kit(out=KIT):
    rows = kit_sources()
    parts, current, size = [], [], 0
    for row in rows:
        weight = len(row[3]) + 8
        if current and (size + weight > PART_CHARS or (row[1] == "block") != (current[-1][1] == "block")):
            parts.append(current)
            current, size = [], 0
        current.append(row)
        size += weight
    if current:
        parts.append(current)
    os.makedirs(out, exist_ok=True)
    for old in os.listdir(out):
        if old.startswith("part-") and old.endswith(".json"):
            os.remove(os.path.join(out, old))
    for n, part in enumerate(parts, 1):
        areas = []
        for row in part:
            if row[0] not in areas:
                areas.append(row[0])
        data = {"forge_studio_translation": KIT_FORMAT, "part": f"{n} of {len(parts)}", "area": ", ".join(areas),
                "language": "", "code": "", "direction": "ltr", "instructions": INSTRUCTIONS,
                "texts": {key: english for _, kind, key, english in part if kind == "text"}}
        blocks = {key: english for _, kind, key, english in part if kind == "block"}
        if blocks:
            data["blocks"] = blocks
        with open(os.path.join(out, f"part-{n:02d}.json"), "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
            f.write("\n")
    texts = sum(1 for r in rows if r[1] == "text")
    with open(os.path.join(out, "README.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write(KIT_README.format(parts=len(parts), texts=texts, blocks=len(rows) - texts,
                                  prompt=PROMPT.format(language="<your language>")))
    return len(parts), texts, len(rows) - texts


KIT_README = """# Forge Studio translation kit

Forge Studio comes in English and Hebrew. This kit lets any AI translate it into another language:
{parts} files with {texts} interface texts and {blocks} help texts, in English.

1. Give your AI (ChatGPT, Claude, Gemini, ...) one or more of the `part-*.json` files at a time, with this request:

   > {prompt}

2. Save every file it returns (the name does not matter, `.json`).
3. In Forge Studio, open the language menu (▾ next to the language button, top right) → **Add a language...** →
   **Load the translated files**, and choose all of them. Or copy them into
   `forge/webui/extensions/forge-hebrew-ui/languages/<code>/` (for example `languages/es/`) and reload the page.

A language does not have to be complete: whatever is missing stays in English. After an update, a new kit may have
new texts; translate the new parts and load them into the same language.

An AI coding agent can do the whole kit in one go: ask it to translate every part in this folder by these rules and
to save the results in `forge/webui/extensions/forge-hebrew-ui/languages/<code>/`.
"""


def kit_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name in sorted(os.listdir(KIT)):
            if name.endswith((".json", ".md")):
                z.write(os.path.join(KIT, name), "forge-studio-translation-kit/" + name)
    return buf.getvalue()


def kit_keys():
    texts, blocks = set(), set()
    if os.path.isdir(KIT):
        for name in os.listdir(KIT):
            if name.startswith("part-") and name.endswith(".json"):
                data = json.load(open(os.path.join(KIT, name), encoding="utf-8"))
                texts.update(data.get("texts", {}))
                blocks.update(data.get("blocks", {}))
    return texts, blocks


# ------------------------------------------------------------------------------------------ language files

CODE = re.compile(r"[^a-z0-9_-]+")
UNSAFE_HTML = re.compile(r"<\s*(script|iframe|object|embed|style|link|meta)\b[^>]*>.*?(?:<\s*/\s*\1\s*>|$)|"
                         r"<\s*/?\s*(?:script|iframe|object|embed|style|link|meta)\b[^>]*>", re.I | re.S)
EVENT_ATTR = re.compile(r"\s+on[a-z]+\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s>]+)", re.I)
JS_URL = re.compile(r"(href|src)\s*=\s*([\"']?)\s*javascript:", re.I)


def clean_code(code):
    return CODE.sub("-", (code or "").strip().lower()).strip("-")[:24]


def safe_html(value):
    value = UNSAFE_HTML.sub("", value)
    value = EVENT_ATTR.sub("", value)
    return JS_URL.sub(r"\1=\2#", value)


def parse_file(text):
    """A file as an AI returns it: plain JSON, or JSON inside ``` fences or with words around it."""
    text = text.lstrip("﻿")
    try:
        data = json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("no JSON in it")
        data = json.loads(text[start:end + 1])
    if not isinstance(data, dict):
        raise ValueError("not a JSON object")
    if "texts" not in data and "blocks" not in data:
        # a plain {english: translation} table (A1111's localization files look like this)
        data = {"texts": data}
    texts = data.get("texts") if isinstance(data.get("texts"), dict) else {}
    blocks = data.get("blocks") if isinstance(data.get("blocks"), dict) else {}
    out = {"language": str(data.get("language") or "").strip()[:40], "code": clean_code(str(data.get("code") or "")),
           "direction": "rtl" if str(data.get("direction", "")).lower() == "rtl" else "",
           "texts": {k: v for k, v in texts.items() if isinstance(k, str) and isinstance(v, str) and v.strip()},
           "blocks": {k: v for k, v in blocks.items() if isinstance(k, str) and isinstance(v, str) and v.strip()}}
    if not out["texts"] and not out["blocks"]:
        raise ValueError("no translated texts in it")
    return out


def _files_of(code):
    folder = os.path.join(LANGUAGES, code)
    if os.path.isdir(folder):
        return [os.path.join(folder, f) for f in sorted(os.listdir(folder)) if f.lower().endswith(".json")]
    single = os.path.join(LANGUAGES, code + ".json")
    return [single] if os.path.isfile(single) else []


def language_codes():
    if not os.path.isdir(LANGUAGES):
        return []
    codes = set()
    for name in os.listdir(LANGUAGES):
        if os.path.isdir(os.path.join(LANGUAGES, name)):
            code = name.lower()
        elif name.lower().endswith(".json"):
            code = name[:-5].lower()
        else:
            continue
        if code == clean_code(code) and code not in BUILT_IN and _files_of(code):
            codes.add(code)
    return sorted(codes)


def load_language(code):
    """The page's dictionary for a language made from the kit: every file of it merged."""
    merged = {"code": code, "name": "", "direction": "", "texts": {}, "blocks": {}, "contextual": {}, "kept": [],
              "problems": []}
    keys = block_keys()
    for path in _files_of(code):
        try:
            data = parse_file(open(path, encoding="utf-8", errors="replace").read())
        except ValueError as e:
            merged["problems"].append(f"{os.path.basename(path)}: {e}")
            continue
        merged["name"] = merged["name"] or data["language"]
        merged["direction"] = merged["direction"] or data["direction"]
        merged["texts"].update(data["texts"])
        for block_id, value in data["blocks"].items():
            key = keys.get(block_id, block_id)
            merged["blocks"][re.sub(r"\s+", " ", key).strip()] = safe_html(value)
    if not merged["direction"]:
        sample = "".join(list(merged["texts"].values())[:200])
        rtl = len(RTL_LETTERS.findall(sample))
        merged["direction"] = "rtl" if rtl > len(re.findall(r"[A-Za-z]", sample)) else "ltr"
    if merged["direction"] == "rtl":
        merged["texts"] = {k: display_form(v) for k, v in merged["texts"].items()}
    merged["texts"] = {k: v for k, v in merged["texts"].items() if v != k}
    merged["name"] = merged["name"] or code
    return merged


def describe(code):
    data = load_language(code)
    texts, blocks = kit_keys()
    keys = block_keys()   # the kit names a help block by its id, the dictionary by its English text
    covered = len(texts & set(data["texts"])) + sum(
        1 for block_id in blocks if re.sub(r"\s+", " ", keys.get(block_id, block_id)).strip() in data["blocks"])
    return {"code": code, "name": data["name"], "direction": data["direction"],
            "covered": covered, "total": len(texts) + len(blocks), "problems": data["problems"]}


def save_language(files, name="", code=""):
    """Files the page sent ([{name, text}]): checked, then written to languages/<code>/. Returns describe(code)."""
    parsed, problems = [], []
    for item in files:
        try:
            parsed.append((item.get("name") or "part.json", parse_file(item.get("text") or "")))
        except ValueError as e:
            problems.append(f"{item.get('name') or '?'}: {e}")
    if not parsed:
        raise ValueError("; ".join(problems) or "no files")
    code = clean_code(code) or next((d["code"] for _, d in parsed if d["code"]), "") \
        or clean_code(name) or clean_code(next((d["language"] for _, d in parsed if d["language"]), ""))
    if not code or not re.search(r"[a-z]", code):
        raise ValueError("the files do not say which language they are: fill in \"code\" (for example \"es\")")
    if code in BUILT_IN:
        raise ValueError(f"\"{code}\" is built in already; give the language another code")
    folder = os.path.join(LANGUAGES, code)
    os.makedirs(folder, exist_ok=True)
    for filename, data in parsed:
        base = re.sub(r"[^A-Za-z0-9_.-]+", "-", os.path.splitext(os.path.basename(filename))[0]).strip("-.") or "part"
        if name and not data["language"]:
            data["language"] = name[:40]
        record = {"forge_studio_translation": KIT_FORMAT, "language": data["language"], "code": code,
                  "direction": data["direction"] or "", "texts": data["texts"], "blocks": data["blocks"]}
        with open(os.path.join(folder, base[:60] + ".json"), "w", encoding="utf-8", newline="\n") as f:
            json.dump(record, f, ensure_ascii=False, indent=1)
    info = describe(code)
    info["problems"] = problems + info["problems"]
    return info

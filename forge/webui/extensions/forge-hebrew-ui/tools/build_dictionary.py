"""Builds javascript/hebrew-ui-dictionary.js from the translation files.

translations/<batch>.en.json    source strings collected from the live UI: [{"i", "en", "ctx"}] or blocks [{"i", "text", "html"}]
translations/<batch>.he.txt     one line per string: "<i>|<hebrew>"; "<i>|=" keeps the English. \\n means a line break
translations/<batch>.he.json    blocks: {"<i>": "<hebrew html>"}
translations/contextual.he.json optional per-tab overrides: {"<top tab id>": {"<english>": "<hebrew>"}}

Run: python tools/build_dictionary.py   (prints what is missing or suspicious; exit code 1 on errors)
"""
import glob
import html.parser
import json
import os
import re
import sys

import ui_kit  # tools/ui_kit.py, next to this file

ROOT =os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRANSLATIONS = os.path.join(ROOT, "translations")
OUTPUT = os.path.join(ROOT, "javascript", "hebrew-ui-dictionary.js")
HEBREW = re.compile(r"[֐-׿]")
TOKENS = re.compile(r"\{[^{}]*\}|%[sd]|\$\{[^}]*\}|<[^>]+>|\b[A-Z]{2,}\d*\b|\b\d+(?:\.\d+)?\b")
# plain English words written in capitals; translating them is fine
IGNORED_TOKENS = {"UI", "ON", "OFF", "OR", "AND", "EG", "NOTE", "ATTENTION", "IMPORTANT", "WARNING", "NOT", "ALL", "NEW",
                  "ONLY", "READ", "ME", "WORKING", "RECOMMENDED", "DON", "NON"}
LRM, RLM = "‎", "‏"
SIGN_RUN = re.compile(r"[-+]+(?=[0-9A-Za-z.])")


def mark_signed_numbers(text):
    """After Hebrew, "-1" or "--neg" is drawn with the sign on the wrong side; a left-to-right mark keeps it in front."""
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


def display_form(hebrew):
    """The browser takes each line's direction from its first letter, so a Hebrew line that opens with an English
    word (for example "SAM אוטומטי") gets a right-to-left mark in front."""
    lines = []
    for line in mark_signed_numbers(hebrew).split("\n"):
        first_letter = re.search(r"[A-Za-zא-ת]", line)
        if first_letter and first_letter.group() < "א" and HEBREW.search(hebrew):
            line = RLM + line
        lines.append(line)
    return "\n".join(lines)


class TagCounter(html.parser.HTMLParser):
    VOID = {"br", "img", "hr", "input", "meta", "link", "source", "wbr"}

    def __init__(self):
        super().__init__()
        self.stack, self.errors = [], []

    def handle_starttag(self, tag, attrs):
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        if tag in self.stack:
            while self.stack and self.stack.pop() != tag:
                pass
        else:
            self.errors.append(f"unexpected </{tag}>")


def parse_lines(path):
    rows = {}
    with open(path, encoding="utf-8") as f:
        for number, line in enumerate(f, 1):
            line = line.rstrip("\n").rstrip("\r")
            if not line.strip():
                continue
            index, sep, text = line.partition("|")
            if not sep or not index.strip().isdigit():
                raise ValueError(f"{os.path.basename(path)} line {number}: expected '<index>|<text>'")
            rows[int(index)] = text.replace("\\n", "\n")
    return rows


def main():
    texts, blocks, kept, problems, warnings = {}, {}, set(), [], []
    for source_path in sorted(glob.glob(os.path.join(TRANSLATIONS, "*.en.json"))):
        name = os.path.basename(source_path)[:-len(".en.json")]
        source = json.load(open(source_path, encoding="utf-8"))
        if name.endswith("_blocks"):
            target = os.path.join(TRANSLATIONS, name + ".he.json")
            if not os.path.exists(target):
                warnings.append(f"{name}: not translated yet ({len(source)} blocks)")
                continue
            translated = json.load(open(target, encoding="utf-8"))
            for row in source:
                hebrew = translated.get(str(row["i"]))
                if hebrew is None:
                    warnings.append(f"{name} #{row['i']}: missing")
                    continue
                if hebrew == "=":
                    continue
                checker = TagCounter()
                checker.feed(hebrew)
                if checker.errors or checker.stack:
                    problems.append(f"{name} #{row['i']}: unbalanced html {checker.errors or checker.stack}")
                blocks[row["text"]] = hebrew
            continue
        target = os.path.join(TRANSLATIONS, name + ".he.txt")
        if not os.path.exists(target):
            warnings.append(f"{name}: not translated yet ({len(source)} strings)")
            continue
        translated = parse_lines(target)
        for row in source:
            hebrew = translated.get(row["i"])
            if hebrew is None:
                warnings.append(f"{name} #{row['i']}: missing ({row['en'][:50]!r})")
                continue
            if hebrew == "=":
                kept.add(row["en"])
                continue
            if not HEBREW.search(hebrew):
                warnings.append(f"{name} #{row['i']}: no Hebrew in {hebrew[:50]!r}")
            lost = [t for t in set(TOKENS.findall(row["en"])) if t not in hebrew and t not in IGNORED_TOKENS]
            if lost:
                warnings.append(f"{name} #{row['i']}: {lost} from {row['en'][:60]!r} not in {hebrew[:60]!r}")
            shown = display_form(hebrew)
            if row["en"] in texts and texts[row["en"]] != shown:
                warnings.append(f"{name} #{row['i']}: {row['en'][:40]!r} translated differently elsewhere")
            texts[row["en"]] = shown
    contextual = {}
    contextual_path = os.path.join(TRANSLATIONS, "contextual.he.json")
    if os.path.exists(contextual_path):
        tables = json.load(open(contextual_path, encoding="utf-8"))
        contextual = {tab: {en: display_form(he) for en, he in table.items()} for tab, table in tables.items()}
    # kept: strings deliberately left in English, so tools/find_untranslated.js does not report them
    payload = json.dumps({"texts": texts, "blocks": blocks, "contextual": contextual, "kept": sorted(kept - texts.keys())},
                         ensure_ascii=False, separators=(",", ":"))
    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write("// Generated by tools/build_dictionary.py from translations/. Edit the translation files, not this file.\n")
        f.write("window.hebrewUiDictionary = " + payload + ";\n")
    for line in warnings:
        print("warning:", line)
    for line in problems:
        print("ERROR:", line)
    print(f"wrote {OUTPUT}: {len(texts)} texts, {len(blocks)} blocks, {sum(len(v) for v in contextual.values())} contextual")
    # the kit other languages are translated from (tools/ui_kit.py): the same English, cut into parts for an AI
    parts, kit_texts, kit_blocks = ui_kit.write_kit()
    print(f"wrote {ui_kit.KIT}: {parts} parts, {kit_texts} texts, {kit_blocks} help blocks")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

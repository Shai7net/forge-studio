# Forge Hebrew UI

כפתור **עברית** בראש העמוד (ליד ההגדרות המהירות) מעביר את כל הממשק של Forge לעברית בלחיצה אחת, ו-**English** מחזיר אותו לאנגלית בדיוק כמו שהיה.
הבחירה נשמרת בדפדפן, כך שבפעם הבאה העמוד נפתח בשפה האחרונה שנבחרה.

A one-click switch between the English Forge interface and a Hebrew translation, for txt2img, img2img, ControlNet,
AnimateDiff, Deforum (including Audio Keyframes), Infinite Zoom, Replacer, Segment Anything, ReActor, Dynamic Prompts,
Image Browser, Extras, Train, Settings and Extensions.

## What it changes, and what it never changes

- Only what people read changes: labels, buttons, tabs, help texts, tooltips and placeholders. The text is right to left;
  the layout stays where it is.
- Nothing sent to the server changes. Prompts, dropdown values, native `<select>` options, galleries, model and LoRA
  cards, code and file paths are never touched.
- Every original is kept, so switching back to English restores the exact text.
- Model, sampler, file and extension names stay in English. Key terms keep the English word in parentheses, for example
  `עוצמת היצמדות לפרומפט (CFG)`, so tutorials in English are still easy to follow.

## How it works

`javascript/hebrew-ui.js` swaps text nodes whose trimmed text is an exact dictionary key, translates `title` and
`placeholder` attributes, and gives each translated label `dir="auto"`. Long help blocks (Gradio `.prose` Markdown/HTML)
are not edited in place: the original is hidden and a Hebrew copy is inserted next to it, because Gradio owns those nodes.
A `MutationObserver` translates content that appears later (accordions, tabs, updated labels). Tooltips from `hints.js`
and Deforum's `deforum-hints.js` are looked up by their English label, so translated labels get the Hebrew tooltip.

`javascript/hebrew-ui-dictionary.js` is generated. Do not edit it by hand.

## Editing or adding translations

| File | Format |
|---|---|
| `translations/<batch>.en.json` | Source strings collected from the live UI: `[{"i", "en", "ctx"}]` |
| `translations/<batch>.he.txt` | One line per string: `<i>|<hebrew>`. `<i>|=` keeps the English. `\n` is a line break |
| `translations/<batch>_blocks.en.json` / `.he.json` | Help blocks: `{"<i>": "<hebrew html>"}`, or `"="` to leave a block in English |
| `translations/contextual.he.json` | Optional per-tab overrides: `{"<top tab id>": {"<english>": "<hebrew>"}}` |
| `translations/GLOSSARY.md` | Style rules and the fixed term list |

After editing, rebuild and reload the browser page (no Forge restart needed):

```bash
python tools/build_dictionary.py
```

The build prints warnings for missing lines, lost placeholders or numbers, and strings translated two different ways,
and fails on unbalanced HTML in blocks. It also adds the invisible direction marks that keep mixed Hebrew and English
readable (a right-to-left mark before a line that opens with an English word, a left-to-right mark before `-1` or `--neg`).

To find strings that are still in English on the page you are looking at, paste `tools/find_untranslated.js` into the
browser console while Hebrew is on.

## Rules that keep it safe

- Never add translations for text a program reads back: example prompts, dropdown values of native `<select>` elements,
  infotext field names, setting keys, file names. Use `=` for those.
- A block that contains live controls (checkboxes, buttons with handlers, tables that refresh) must stay `"="`.
- New JavaScript or CSS files in this extension need a Forge restart; dictionary changes only need a page reload.

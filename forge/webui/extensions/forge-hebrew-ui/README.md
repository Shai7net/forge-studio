# Forge interface languages (forge-hebrew-ui)

The language button at the top right (next to the quick settings) switches the whole Forge interface between English
and another language with one click, and back to exactly what it was. A full **Hebrew** translation is built in; any
other language can be added with the **translation kit** and any AI (see *Other languages* below). The ▾ next to the
button lists the languages, and the choice is remembered in the browser.

Covered: txt2img, img2img, ControlNet, AnimateDiff, Deforum (including Audio Keyframes), Infinite Zoom, Replacer,
Segment Anything, ReActor, Dynamic Prompts, Image Browser, Extras, Train, Settings, Extensions and Forge Studio's own
tabs (Video Studio, Characters, Forge Studio, Simulation 360, Time Strata, FLUX 3, FIBO 2.0, the prompt writer).

כפתור השפה בראש העמוד מעביר את כל הממשק לעברית בלחיצה אחת, ובלחיצה נוספת מחזיר אותו לאנגלית בדיוק כמו שהיה.

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

## Other languages

`translation-kit/` (written by `tools/build_dictionary.py` together with the Hebrew dictionary) holds every English text
of the interface in parts small enough for an AI chat: Forge's own texts, the help blocks, and the words of the pages
that draw themselves (Simulation 360, the Forge Studio editor, Time Strata, the prompt writer). Each part carries its
rules in `"instructions"`. The files an AI returns go to `languages/<code>/` (any names, any number), either through
**▾ → Add a language…** on the page or by copying them there; `scripts/ui_languages.py` serves them:

| Route | |
|---|---|
| `GET /forge-ui/languages` | the languages in `languages/`, each with how many of the kit's texts it covers |
| `GET /forge-ui/languages/<code>` | one language's dictionary, all its files merged |
| `GET /forge-ui/translation-kit.zip` | the kit |
| `POST /forge-ui/languages` | `{"files": [{"name", "text"}], "name"}`: saves returned files (only from the PC itself) |

Files may come wrapped in a chat's words or in ``` fences; a plain `{"English": "translation"}` table works too.
Missing texts stay in English. For a right-to-left language the same direction marks as for Hebrew are added, and help
blocks are cleaned of scripts and event handlers before they reach the page. Pages that draw their own words ask
`window.hebrewUi.text(english)` for a language from `languages/` and keep their own Hebrew tables.

## Rules that keep it safe

- Never add translations for text a program reads back: example prompts, dropdown values of native `<select>` elements,
  infotext field names, setting keys, file names. Use `=` for those.
- A block that contains live controls (checkboxes, buttons with handlers, tables that refresh) must stay `"="`.
- New JavaScript or CSS files in this extension need a Forge restart; dictionary changes only need a page reload.

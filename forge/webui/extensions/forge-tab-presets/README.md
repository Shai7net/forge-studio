# Forge Tab Presets

סרגל **שמור / טען / אחרונים / איפוס** בראש כל לשונית (txt2img, img2img, Extras, Deforum, Infinite Zoom, Replacer…).
שמירה תופסת את *כל* מה שמוגדר בלשונית באותו רגע: פרומפטים, דגימה, Hires. fix, כל יחידות ה-ControlNet, AnimateDiff
(כולל Music ו-Dance), סקריפטים וכל תוסף אחר, וגם איזו תת-לשונית ואיזה אקורדיון פתוחים. טעינה מחזירה את הכול בדיוק.

A **Save / Load / Recent / Reset** bar at the top of every tab. A preset holds everything that is set in the tab at that
moment, for every extension, without a list of supported fields.

## The bar

| | |
|---|---|
| **List** | The saved presets of this tab. The last one used is chosen again when the page opens. |
| **📂 Load** | Puts the chosen preset back on the tab. |
| **💾 Save** | Updates the preset in use. With nothing chosen it asks for a name. A preset that was not loaded or saved in this session is replaced only on a second press. |
| **Save as…** | Saves under a new name. |
| **🕘 Recent** | The presets saved or loaded last, newest first; a click loads. The first line, *Last generation*, is what the tab held the last time Generate was pressed, pictures and files included, and it survives a restart. |
| **↺ Reset** | The whole tab back to how it opens after a restart (pictures and files are cleared too). |
| **Undo** | Shown after a Load or a Reset: back to what was on screen before it. |
| **⋯** | Load the model too · Keep pictures and files too · Open the presets folder · Delete. |

## Where presets live

```
webui/tab-presets/<tab>/<name>.json      the preset: plain JSON, one line per setting
webui/tab-presets/<tab>/<name>.media/    its pictures and files (ControlNet image, dance video, music…)
webui/tab-presets/<tab>/_last.json       the last generation (with _last.media/)
webui/tab-presets/<tab>/_history/        the 5 previous versions of every preset that was saved over
webui/tab-presets/<tab>/_deleted/        deleted presets are moved here, never erased
```

A preset can be copied to another computer as the `.json` file plus its `.media` folder.

When a preset with files is loaded, the page gets copies placed in Gradio's own upload folder, so nothing on screen
points into `tab-presets`: a preset can be saved over or deleted while its video is still in use. A picture that can be
painted on (ControlNet input, inpaint) is restored without the painted mask.

## How it works

Everything happens in the page (`javascript/tab-presets.js`). Gradio keeps the live value of every component in
`window.gradio_config.components[n].props.value`; a preset is those values. Loading writes them back the way Gradio
applies an answer from the server, so every "on change" reaction of the tab runs as if the change was made by hand
(ControlNet fills its model list, Hires. fix opens, sliders get their ranges).

Those reactions can overwrite values (choosing a ControlNet type resets its preprocessor and model), so loading:

1. sets a field before the fields it re-sets, using the page's own event list to know which is which;
2. waits for the server's answers after each step;
3. compares the tab with the preset and repeats for whatever still differs, up to a few rounds.

Whatever cannot be set (a model that is no longer installed) is named in the bar instead of failing silently.

## A load does not depend on what was there before

The tab ends up as the preset describes it, whatever it held when Load was pressed:

- **Fields the preset does not know** go back to their starting value: a ControlNet unit added after the preset was
  saved, the fields of an extension installed since. Otherwise their old content would stay and mix with the preset.
- **Pictures that can be painted on** (ControlNet input and mask, inpaint) are taken out before another goes in. Gradio
  keeps the painted strokes and the canvas of the old picture for as long as the component holds one, and ControlNet
  reads leftover strokes as a mask. A picture that is already the right one is still put in again if it has strokes.
- **Settings, pictures and the open sub-tabs are each gone over twice**, because each can undo part of another: ticking
  "Use Mask" empties the mask picture, a new picture cancels a preview, opening an accordion that has a checkbox in
  its title ticks it.
- **ControlNet's titles are brought up to date.** The green unit name, the "2 units" badge and the "[Canny]" after a
  unit's name are drawn by ControlNet's own script when a box is clicked, and the small picture in a folded unit's
  title only when the unit is folded by hand. A load sends the browser's "change" event for every checkbox and radio
  button it set and redraws those small pictures, so the titles show the loaded state and not the previous one.
- **Whether each ControlNet unit is open or folded** is part of a preset.

The model in a preset is the one in the quick settings row at the top of the page. Presets saved before format 2 took
it from another list with the same id, so their model is not loaded.

A field is part of a preset when it is a value control that some event reads and the user can reach. Result panels,
the model browser tabs, the style editor and ControlNet's own preset list are left out.

`scripts/tab_presets_api.py` only stores the files (`/tab-presets/list`, `load`, `save`, `delete`, `open-folder`).

## Speed

On a page with ~9,000 components Gradio 3.41 redraws the whole tree for every status message, 150 to 200 ms each, and a
queued event shows three of them. Settings nobody listens to (prompt, steps, sampler, AnimateDiff numbers…) load at
once; each ControlNet field sets off one or two server events. While a preset loads, the bar therefore:

- drops the two messages per event that only show a place in the queue (`estimation`, `process_starts`);
- hands answers that arrive together to Gradio together, so they share one redraw;
- does its waiting in a worker, whose timers keep running when the window is behind another one;
- starts at most about ten events per step, so the queue never times one out.

Measured on this install: a preset with two ControlNet units and Hires. fix (42 server events) loads in about 7 seconds,
down from 26 without the above. A preset with four ControlNet units, five pictures and an AnimateDiff video (about 80
events) takes about 15 seconds, a third of it for the pictures. The bar counts while it works.

## Settings

Settings → Tab Presets → *Tabs without the presets bar*: tab ids separated by commas (`extras, svd`).

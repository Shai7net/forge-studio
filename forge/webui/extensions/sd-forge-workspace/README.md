# Forge Workspace

A tidier txt2img / img2img for Forge.

- **Checkpoint above Generate.** The checkpoint list and its 🔄 sit at the top of the Generate column.
- **Model settings card.** SD VAE, Clip skip, Restore faces and the face restoration model move into a "Model settings"
  card among the extension cards (ControlNet, AnimateDiff, ...). They are Forge's own quick-settings components,
  moved, not copies: they keep working and saving to the settings as before. On any other tab they return to the
  quick-settings row, which now sits under the tab bar.
- **The tab bar on top.** With the quick-settings row out of the way the tab bar is the page's first line, up to the
  update frog and the language switch (it leaves room for them on the right).
- **🎨 Styles | 🧩 LoRA.** Next to the small buttons under Generate. LoRA opens a gallery of every LoRA: preview,
  name, SD version (a red badge when it was made for another model), folders on the side, search, and "Fits this
  model" / "All versions". A click adds a LoRA as a chip under the styles (− / + weight in steps of 0.1, ✕ removes);
  a right-click (on a card or a chip) edits its trigger words, saved in the LoRA's `.json` as `activation text`, the
  same field Forge's own LoRA card editor uses.
- **How the chips reach the picture.** They live in a hidden field of the "Model settings" script; before the prompt
  is processed (`before_process`) each chip adds its trigger words and `<lora:alias:weight>` to the prompt, so Forge
  loads the LoRA as if it had been typed, and the picture's infotext records it. The prompt box itself stays clean
  (the 🌐 prompt writer can rewrite it without losing the LoRAs). A LoRA already typed in the prompt is not added twice.

- **The right column as tall as the prompts.** The checkpoint without its heading line, a slightly lower Generate:
  Generation's tabs sit right under the prompts (no gap).
- **Recent** (`javascript/viewer.js`). Under txt2img / img2img / Extras' result buttons, a strip of the newest
  outputs of all of Forge, pictures and videos (Forge Studio's library index; grids, Video Studio's first/last frames
  and a clip whose `-sound` copy exists are left out), ‹ › and the wheel scroll it, ⟳ looks again; it refreshes by
  itself when a result changes or a tab opens. Forge Studio's timeline drawer comes after it.
- **The viewer.** A click on the result picture (instead of Forge's lightbox) or on a Recent item: the wheel zooms at
  the pointer, a drag moves the zoomed picture, a double-click goes between fit and 2.5x, ‹ › and ← → go to earlier /
  later ones (this generation first, then Recent), ⛶ or F full screen, Esc closes. Videos play in it. For a picture
  of this generation the result's own buttons (send to img2img / inpaint / Extras, Panorama, 🎬 Video Studio, ...) are
  moved into the viewer and act on it (the gallery selects it); pressing one closes the viewer. For an older item:
  open its folder, send it to Forge Studio's editor.

## API

`GET /forge-workspace/loras` → `{"loras": [{"name", "alias", "folder", "preview", "trigger", "weight", "version"}],
"model": "SD1" | "SDXL" | "SD2" | "Unknown", "default_weight"}`

`POST /forge-workspace/lora-trigger` with `{"name": "<lora name>", "trigger": "<words>"}` saves trigger words.

```bash
curl http://127.0.0.1:7860/forge-workspace/loras
curl -X POST http://127.0.0.1:7860/forge-workspace/lora-trigger -H "Content-Type: application/json" -d '{"name": "more_details", "trigger": "intricate details"}'
```

Through `/sdapi/v1/txt2img` LoRAs are given in the prompt as usual; the chips are a page feature.

## Notes

- Several scripts create components with the checkpoint's own id (`setting_sd_model_checkpoint`, e.g. img2img's
  "Force a different Stable Diffusion model"); the page moves the real quick-settings components by reference.
- Tests: `..\..\venv\Scripts\python.exe tests\test_workspace_lora.py`.

# Hebrew prompt writer (🌐)

Write an idea in Hebrew (or any language) in a prompt box, press **🌐** in the box's corner, and it becomes an
English prompt written the way the model behind that box reads best. Then rate it: **👍** (green) or **👎** (red).
Every rating teaches the writer, and the next prompts follow what you liked.

Until 2026-10-27 this button ran NLLB-600M, a word-for-word translator: "נחש מצייר ציור" came out as "A snake draws
a picture". Now the same idea for FLUX comes out as a whole scene: the snake, the brush, the canvas, the light and
the style.

## Where 🌐 is

| Box | Written for |
|---|---|
| txt2img / img2img prompt | the selected checkpoint: SD 1.5 (comma phrases + quality words), SDXL (a sentence + details), Pony / Illustrious (booru tags, by the checkpoint's name), FLUX and FLUX 3 (flowing sentences) |
| txt2img with FIBO 2.0 Dev | FIBO's structured JSON caption (VGL: objects, background, lighting, camera, style; `text_render` keeps requested lettering in its own language) |
| img2img with FIBO, or Edit Turbo | one direct edit instruction ("Change ... Keep ... unchanged") |
| txt2img / img2img negative prompt | a plain list of things to avoid (nothing added) |
| Video Studio prompt, Wan model chosen | Wan 2.2: one continuous shot with motion and camera; from a picture (Image to Video), mostly the motion; Ingredients names "the ... from picture N" |
| Video Studio prompt, LTX model chosen | LTX-2.3: one long chronological paragraph (action, gestures, looks, place, camera, light, changes) ending with the sound - ambience, effects, music and spoken lines in quotes - since LTX makes the sound too; per sub-tab (`ltx_t2v`, `ltx_i2v` also for Image + Audio, `ltx_ingredients`) |
| Video Studio part boxes | Wan 2.2 or LTX-2.3 between two of your pictures, by the chosen model |
| Characters: character / video | the character as JSON for a sheet; the video idea for Ingredients, for the model chosen in Video Studio |
| Forge Studio sound effects (`fs_effects`) | MMAudio: audible source, action, material, rhythm, distance and acoustic environment; no visual prompt |
| Forge Studio music (`fs_music`) | ACE-Step: genre, instruments, tempo, mood and arrangement; instrumental music |

Forge Studio invokes these two audio targets automatically before generating sound, for input in any language,
including English. The resulting English prompt is shown in the sound request status. These targets use audio
instructions instead of the image and video creativity settings.

`<lora:...>` tags you wrote are kept as they are.

## The bar under the box

- **Understood:** what the writer understood from your words, in plain English. A wrong word shows up here
  first (for example "raccoon" for "קיפוד").
- **👍** keeps what is in the box **now**: correct the prompt first if you like, and the correction is what is
  learned.
- **👎** marks the prompt as weak and writes another version right away. A box opens for what was wrong. A note
  like `קיפוד = hedgehog` (or `צב = turtle; ג'חנון = jachnun`) is a **word lesson**: from then on, every request
  with that word, in any box, gets it right. Any other note ("shorter", "no painting style") is followed by the
  prompts of that kind.
- **🔄** another version of the same idea. **↩** back to what you wrote. **📚** everything learned (with ✕ to forget
  one). **✕** closes the bar.

## How it learns

Each prompt is written with, in front of the model:
1. the three liked prompts closest to your new idea for the same kind of box (your corrected versions);
2. word lessons for the words in your idea;
3. your other notes (newest first);
4. for the same idea, the versions you disliked, to write past them.

So a rating changes the very next prompt. Everything is kept in `data/learned.json` (Hebrew, what was written, the
final text, the meaning, your rating and note), which is also a ready data set for training a model of your own
later.

## The model

**Gemma 4 E4B** (Google, 2026), `models/LLM/gemma-4-E4B_q4_0-it.gguf` (4.8 GB, Google's own QAT q4_0), run by the
llama.cpp installed with winget (`llama-server`, Vulkan). Chosen after a test of hard Hebrew words:

| Idea | Qwen3-VL-4B (FLUX 3's) | DictaLM 3.0 1.7B | NLLB 600M | Gemma 4 E4B |
|---|---|---|---|---|
| שועל בשלג | wolf | bear | fox | **fox** |
| זאב ושועל | wolf, fox | wolf, fox | wolf, wolf | **wolf, fox** |
| פלאפל | watermelon | falafel | flap | **falafel** |
| פאות וכיפה | horns, helmet | beard, kippah | | **side curls, kippah** |
| בורקס | | borscht | | **borekas** |
| קיפוד | | rabbit | | **hedgehog** (raccoon once) |

(Blank: not asked.) DictaLM 3.0 12B also reads Hebrew well, but its 7 GB do not fit the 8 GB card next to Forge
(about one word a second). Gemma still misses now and then (ג'חנון, חמסין): that is what word lessons are for.

- It takes 2.9 GB of the card. It starts on the first 🌐 (about 6 s), writes a prompt in about 1.5 s, stays up
  for 45 s so 👎 and 🔄 answer at once, and leaves the card **immediately** when anything takes Forge's queue (a
  render, a Video Studio job, a checkpoint change) - Settings → Prompt writer.
- The very first prompt after installing it, or after a driver update, waits about 45 s: Vulkan compiles its
  shaders once and the driver keeps them.
- Without the .gguf or llama.cpp it falls back to Qwen3-VL-4B, the text encoder of FLUX 3 (in Forge's own
  process, 4-5 s a prompt, weaker Hebrew), and without that to NLLB.
- While Forge is drawing, 🌐 answers "busy": the writer needs the card.

Settings → **Prompt writer**: how much it adds (Faithful / Rich / Creative), the model, how long it stays.

## API

```bash
curl -X POST http://127.0.0.1:7860/translator/write -H "Content-Type: application/json" \
     -d '{"text": "שועל בשלג", "field": "txt2img_prompt"}'
# {"prompt": "A vibrant red fox ...", "meaning": "A fox in the snow.", "target": "flux", "seconds": 1.4, ...}
```

```python
import requests
r = requests.post("http://127.0.0.1:7860/translator/write",
                  json={"text": "הקיפוד מסתובב אל המצלמה", "field": "vs_prompt", "video_mode": "i2v"}).json()
requests.post("http://127.0.0.1:7860/translator/rate",
              json={"source": "הקיפוד מסתובב אל המצלמה", "target": r["target"], "written": r["prompt"],
                    "final": r["prompt"], "rating": 1, "meaning": r["meaning"]})
```

```javascript
const r = await (await fetch("/translator/write", {method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({text: "נחש מצייר ציור", field: "txt2img_prompt"})})).json();
```

| Route | Body | Answer |
|---|---|---|
| `POST /translator/write` | `text`, `field` (a box id above), `video_mode` (`t2v`/`i2v`), `again` (another version), `seed` | `prompt`, `meaning`, `target`, `target_label`, `seconds`, `examples`, `counts` |
| `POST /translator/rate` | `source`, `target`, `written`, `final`, `rating` (1 / -1), `meaning`, `note`, `id` (to change one) | `entry`, `counts` |
| `POST /translator/note` | `id`, `note` | `entry` |
| `GET /translator/learned` | | `entries`, `counts`, `targets` |
| `POST /translator/forget` | `id` | `forgotten` |
| `POST /translator/hebrew-to-english` | `text` | `translated_text` (the old route; now a prompt for txt2img's model) |

## Files

- `scripts/translator.py`: Forge glue (routes, settings, which model is behind a box, sharing the card).
- `prompt_writer/styles.py`: what each model wants to read, the instructions, the built-in examples.
- `prompt_writer/memory.py`: ratings, word lessons and what each new prompt is written with.
- `prompt_writer/llama.py`: Gemma through llama-server. `prompt_writer/model.py`: the Qwen fallback (code of
  sd-forge-flux3).
- `javascript/translator.js`, `style.css`: the 🌐 buttons and the bar.
- Tests: `tests/test_prompt_writer.py` (no model), `tests/try_writer.py` (writes for real ideas, Forge stopped).

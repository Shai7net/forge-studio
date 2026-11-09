# Characters

A character manager for Forge: describe a character in any language (or give a reference picture), get a
consistent **character sheet** drawn by FLUX 3 Klein over a pose template, keep your characters in **projects**, and
send one to Video Studio's **Ingredients to Video** with a Wan prompt written for it.

## How to use

1. Select **FLUX 3 Klein** at the top of the page (it draws the sheets).
2. In **The character**, write who you want - Hebrew, English, any language: *"סבא חביב בן 70 עם זקן לבן ארוך,
   משקפיים עגולים, כובע קסקט חום, סוודר ירוק סרוג"*. Press **✨ Create the character sheet**. The prompt writer (the
   🌐 of the hebrew-translator extension) first turns the words into the character - a JSON with `name`, `summary`,
   `appearance`, `style` and `avoid` - and leaves it in the box, where you can change it. 🌐 in the box does the same
   step alone, and 👍 / 👎 under it teach the writer, as in every prompt field.
3. Optional: a **Reference picture** - the character is made from it (with or without words).
4. **Pose template**: the layout of the sheet. The built-in one is an OpenPose sheet (front, side and back with arms
   out, three rows of heads turning, a big close-up). Put your own picture there; **Make this template the default**
   keeps it (a copy goes to `output/characters/_templates`).
5. **Sheets to draw**: 1-4 versions with consecutive seeds; click the one to keep, name it, choose or type a
   **Project**, and **💾 Save the character**.
6. **My characters**: all projects or one, search by name or words. Click a character to rename it, move it to
   another project, draw new versions of it (✏️ puts it back in the create column) or delete it (it moves to
   `output/characters/_trash`, nothing is erased).
7. **A video with this character**: write what it does, in any language, and press **🎬 Send to Ingredients to
   Video**. Words that are not English are written as a Wan prompt that names the character as "the ... from
   picture 1" (writer target `wan_ingredients`); the sheet goes into the first empty ingredient slot (or the one you
   choose), the prompt into Video Studio's prompt, and the way "First frame by FLUX 3 Klein, then Wan 2.2" is chosen.
   Press Generate there.

8. **A 3D model of this character**: choose a saved character and press **🧊 Make a 3D model** (Detail: Fine by
   default). The sheet's front view (whole figure, arms out) is found and cut out, and Simulation 360's Hunyuan3D-2
   builds a textured model of it: about 3 minutes, 40,000 triangles; it shows in the 3D viewer under the button
   (drag to turn it) and is kept as `model.glb` beside the sheet. **🌍** sends it to Simulation 360: it becomes a
   kept object in "Plain shapes and my objects" and, when a world is open there, stands in it at once, with bones
   (it can walk, wave, dance). Long thin things the character holds (a walking stick) come out short - Hunyuan3D
   keeps thin parts badly.

**Sheet prompt (advanced)**: the text around the character, with `{appearance}`, `{style}`, `{avoid}` and
`{reference}` filled in. It is the JSON form that worked on Shai's sheets (2026-11-09); change it and save it if you
like.

## How it works

- The sheet: FLUX 3 Klein img2img at strength 1, *edit with reference*: the template (and the reference picture)
  are its references through `p.flux3_references` (sd-forge-flux3), each at its own shape; the sheet has the
  template's shape at about 1024². About 60-70 s a sheet on an RTX 3070 Laptop (16 steps).
- Why the video keeps the character: Phantom (Video Studio's other Ingredients way) kept the clothes of a stylized
  3D character but drew a different, real person. Klein drawing the first frame from the whole sheet keeps the face,
  the clothes and the style, and Wan's Image to Video keeps what the first frame shows.
- The 3D model (`forgecharacters/model3d.py`) uses Simulation 360's own pieces: Video Studio's sheet reader for
  the front view, `sim360.objects.cut_out`, and `sim360.solid.make` (its Hunyuan3D, started when needed, with
  Forge's models off the card, under Forge's queue). Hunyuan3D needs ~16 GB of free memory and FLUX 3 Klein holds
  ~10 GB after drawing sheets: when Simulation 360's memory check refuses, FLUX 3 is released **on Forge's main
  thread** and the check is made again (released from another thread its memory stayed committed to the process:
  12.6 GB before and after; on the main thread 12.6 -> 2.5 GB). FLUX 3 loads again by itself at the next sheet.
  The kept object is written straight into `simulation-360/_library/<id>/` (picture, model, `object.json` with a
  two-legged body) and its id is remembered in `character.json` (`sim360_kept`), so a second 🌍 does not copy again.
- The writer is asked over Forge's own route `POST /translator/write` with the fields `ch_character` (target
  `character`) and `ch_video` (target `wan_ingredients`); it shares the card with Forge (it leaves the card when a
  render starts).

## Files

```
output/characters/<project>/<name>/character.json   the character, the idea, the sheet prompt, the seed
                                   sheet.png        the sheet
                                   template.png     the template it was drawn on
                                   reference.png    the reference picture, when one was used
                                   model.glb        the 3D model, when one was made
                                   model-picture.png  the cut-out front view it was made of
output/characters/_templates/   templates made default
output/characters/_trash/       deleted characters
output/characters/_work/        the last sheets drawn and not saved (replaced at every Create)
```

## API

The tab's work is plain Python and HTTP, usable from scripts.

Write a character from words (Python):

```python
import json, urllib.request
req = urllib.request.Request("http://127.0.0.1:7860/translator/write",
                             json.dumps({"text": "סבא עם זקן לבן וכובע קסקט", "field": "ch_character"}).encode(),
                             {"Content-Type": "application/json"})
character = json.loads(json.load(urllib.request.urlopen(req))["prompt"])
```

The same with curl and JavaScript:

```bash
curl -s -X POST http://127.0.0.1:7860/translator/write -H "Content-Type: application/json" -d "{\"text\": \"a pirate cat\", \"field\": \"ch_character\"}"
```

```javascript
const r = await fetch("/translator/write", {method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({text: "a pirate cat", field: "ch_character"})});
const character = JSON.parse((await r.json()).prompt);
```

Draw a sheet with the API (FLUX 3 Klein selected; Forge started with `--api`): the sheet prompt from
`forgecharacters.sheet.compose(character)`, the template as the init picture, strength 1:

```python
payload = {"init_images": [template_base64], "denoising_strength": 1.0, "prompt": sheet_prompt,
           "width": 1024, "height": 1024, "steps": 16, "cfg_scale": 1, "sampler_name": "Euler", "seed": 11}
# POST http://127.0.0.1:7860/sdapi/v1/img2img
```

The library is `forgecharacters.library` (`save`, `characters`, `load`, `update`, `trash`) over plain folders.

## Tests

```
webui\venv\Scripts\python.exe -m unittest discover -s extensions\sd-forge-characters\tests
webui\venv\Scripts\python.exe extensions\hebrew-translator\tests\test_prompt_writer.py
```

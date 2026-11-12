# FIBO 2.0 for Forge

The native-controls panel also follows a selected FLUX 3 checkpoint. Its title and writer
label change with the model; ordered references, source-size preservation, Auto/Generate/Edit
and an optional exact mask are passed to FLUX's own engine. The main img2img picture stays
reference 1. FIBO retains its VGL and adapter behavior; its setup/adapter section is hidden
for FLUX because those weights are incompatible. Use FLUX adapters in the normal LoRA picker.
Writer choices are remembered separately for each family while switching models.

Native FIBO runs initialize Forge's batch and step counters, so the normal progress endpoint
reports sampling progress through decoding. Forge Studio also supplies frog feedback during
model loading, sampling and result delivery when installed.

FIBO 2.0 Dev generates and edits images. Edit Turbo edits in four steps at CFG 1.
The two models are in Forge's own **Stable Diffusion checkpoint** list, used from
txt2img and img2img like any checkpoint, next to SD 1.5, SDXL and FLUX 3.

## Using it

1. Choose **FIBO 2.0 Dev (INT8)** or **FIBO 2.0 Edit Turbo (INT8)** in the checkpoint list.
   Sampler, steps, CFG and a 1024 size are set for it; choosing an ordinary checkpoint
   again brings your own values back (forge-tab-presets' `model-defaults.js`, see the fixes below).
2. txt2img with Dev draws a new picture. img2img (or txt2img with references in the FIBO
   panel) edits: the img2img picture is `<image_1>`, panel references follow in order.
3. Write a prompt, an edit instruction, or a structured VGL JSON caption. The 🌐 button in
   the prompt box (hebrew-translator) writes FIBO's JSON caption from Hebrew, and an edit
   instruction in img2img.
4. Click **Generate**. Results are saved as PNG files in `webui/outputs/fibo2`.

AnimateDiff, ControlNet and Hires. fix are SD features: FIBO does not run them. With
AnimateDiff on, FIBO refuses with a message (it would return one still picture); ControlNet
and Hires. fix are skipped and named in the infotext ("FIBO ignored").

## Fixes of 2026-11-05 (Claude, after the first checkpoint-list version)

- **img2img and the API never reached FIBO.** `modules/img2img.py` and `modules/api/api.py`
  import `process_images` by name, so replacing `processing.process_images` left them on
  Forge's own: with FIBO chosen they quietly drew with the checkpoint kept behind it (the two
  "FIBO" car edits of that afternoon were SDXL pictures). `glue.route_callers()` points those
  names at FIBO too. Verified: /sdapi txt2img and img2img infotexts say FIBO 2.0.
- **Settings leaked between models.** fibo2.js and FLUX 3's flux3.js each kept "your" values
  on their own, so FIBO -> FLUX 3 -> SD 1.5 gave SD 1.5 FIBO's 50 steps, CFG 5 and 1024x1024,
  and a page reload lost them. One keeper now does it for both: forge-tab-presets'
  `javascript/model-defaults.js` (kept in localStorage, leaves a preset's own values alone).
- **The VAE list is no longer switched to flux2-vae**: the worker always uses the FLUX.2 VAE,
  and switching the list made Forge load that VAE into the SD model kept behind FIBO.
- **The checkpoint kept behind FIBO** is the one used last, else the smallest (it was the
  first of the list, a 6.5 GB SDXL).
- AnimateDiff with FIBO is refused with a message (see above).

Speed on the RTX 3070 Laptop: ComfyUI keeps about 5 GB for the computation at 1024x1024, so
FIBO's 8 GB of weights stream from system memory on every step: Dev ~5.5 s a step (12 steps
in 106 s), an Edit Turbo edit ~56 s in all, about half of it loading.

An edit needs no mask. To control the edit area and guarantee exact preservation
outside it, upload a grayscale mask with the same dimensions as one source:
white edits, black preserves. Multiple references cannot be combined with a mask.
The white area is greyed out before conditioning, following Bria's masked-edit
preprocessing, and the decoded result is composited over the source afterward.
**Without an explicit mask, untouched pixels are not guaranteed to be identical.**

Single-image edits keep the source size by default, floored to multiples of 16.
Disable that checkbox to choose a different canvas. At another canvas size,
preserved pixels refer to the resized, center-cropped source. The model was trained
near one megapixel: 1024×1024, 1424×800 and 1248×832 are useful sizes. This
integration accepts up to four megapixels, with no quality guarantee beyond the
model's training range. Multiple references keep their own trained aspect buckets.

The first result can be reused as a reference, or sent to Forge's img2img or
Extras for further processing. Each invocation has a separate seed; a batch runs
sequentially with seeds `seed`, `seed+1`, etc. **Stop FIBO** terminates its worker
and releases its GPU allocations. The standard API interrupt is also observed.

## Prompts, typography and adapters

FIBO accepts short descriptions and edit instructions directly. VGL JSON gives
finer control over objects, lighting, composition, style and camera properties.
JSON is passed to the official FIBO encoder unchanged; A1111 emphasis and prompt
schedules are not applied to it.

```json
{
  "short_description": "A bakery storefront with a hand-painted sign at dawn",
  "text_render": [{"text": "FRESH BREAD", "location": "above the door"}]
}
```

For structured edits include `edit_instruction`, with the target caption in
`structured_caption` or alongside it. Native FIBO conditioning draws text entries
as glyph images. Text rendering quality, including Hebrew lettering, depends on
the model and fonts; it is not a guarantee of error-free typography.

The optional **local prompt writer** reuses Qwen3-VL-4B to translate and expand
requests, including Hebrew, into VGL. `off` passes your text directly; `auto`
expands short or non-Latin prompts; `always` expands all plain-text requests.
Existing JSON is never rewritten. The generated caption is saved in the PNG
metadata. It can be reviewed and reused. No cloud API or API key is used.

The Advanced section accepts FIBO-compatible LoRAs from `models/Lora` and a
strength. These run through ComfyUI's native adapter loader. An adapter with no
matching FIBO layers fails rather than silently doing nothing. SD and FLUX
adapters are not interchangeable with FIBO adapters. This is inference support,
not a LoRA training interface.

## Setup and files

Setup is explicit in the tab. It never runs automatically during Forge startup.
The isolated runtime uses Python 3.11+, PyTorch 2.9.1/CUDA 13 and ComfyUI's native
FIBO implementation at commit `8a98ecf5864433064809ac1f2b52ccbf8b444471`.
It runs inference only, without a separate ComfyUI web server or custom nodes.

- Runtime/environment/logs/jobs: `webui/fibo2`
- Diffusion weights: `webui/models/Fibo2`
- Shared text encoder: `webui/models/text_encoder/qwen3vl_4b_int8_convrot.safetensors`
- Shared VAE: `webui/models/VAE/flux2-vae.safetensors`
- Results: `webui/outputs/fibo2`

The tutorial's [Comfy-Org weights](https://huggingface.co/Comfy-Org/Fibo-2.0)
are downloaded with **aria2c**, eight connections, resuming incomplete transfers,
and checked against official SHA256 hashes. Revision:
`4742d4bec410034142c8e65559e5f00c6f29eccb`.
Int8 and bf16 diffusion files are supported. Int8 is recommended for this 8 GB GPU.
Existing text encoder and VAE files are checked and read, never overwritten.

Manual setup, from the `webui` directory, with Python 3.11+:

```powershell
py -3.11 extensions/sd-forge-fibo2/fibo2/setup_runtime.py install
py -3.11 extensions/sd-forge-fibo2/fibo2/setup_runtime.py download --variant edit-turbo
py -3.11 extensions/sd-forge-fibo2/fibo2/setup_runtime.py download --variant dev
```

Downloads need aria2c on PATH. Installation prefers uv, with pip as fallback.
The existing Forge environment is not upgraded. Set `FIBO2_DISABLE=1` before
launch to disable the extension's tab and API.

## Coexistence with Forge and FLUX3

The extension registers the FIBO checkpoints (`sd_models.list_models`), keeps an
ordinary checkpoint loaded behind them (`sd_models.load_model`), and sends txt2img,
img2img and /sdapi generations to the FIBO worker while a FIBO checkpoint is chosen
(`processing.process_images` and the copies of it in `modules.img2img` and
`modules.api.api`, `glue.route_callers`). With any other checkpoint all three call
Forge's own code unchanged. FLUX 3's loader wraps these the same way and comes after.
Sampler/steps/CFG follow the model through forge-tab-presets' `model-defaults.js`.

GPU jobs use Forge's existing `queue_lock`. Immediately before FIBO inference,
Forge's memory manager offloads its models and the existing FLUX3 engine is
explicitly offloaded if present. The FIBO subprocess exits after each request;
subsequent Forge requests reload their GPU weights normally. This makes model
loading part of each FIBO request's latency. The RTX 3070 does not reproduce
datacenter-GPU timings from tutorials.

The worker uses low-VRAM model offloading and tiled VAE decoding. Its text encoder
uses the GPU with CPU offloading between stages. Models, the runtime, and logs
have their own directories. Every inference log is retained under
`webui/fibo2/logs`; setup uses `setup-latest.log`.

## Local API

Start Forge with its usual `--api` option. Routes obey `--api-auth` when configured:

- `GET /fibo2/status`: runtime/model availability and current/last job.
- `POST /fibo2/generate`: returns base64 PNG `images` and measured `info`.
- `POST /fibo2/interrupt`: interrupts the current FIBO job.

The request fields are `variant` (`dev`, `edit-turbo`), `mode` (`generate`, `edit`),
`prompt`, `negative_prompt`, `width`, `height`, `steps`, `cfg`, `seed`, `count`,
`precision` (`int8`, `bf16`), `prompt_writer` (`off`, `auto`, `always`), `images`
(up to four base64 images), optional base64 `mask`, `loras` (names relative to
`models/Lora`), and `lora_weight`. URLs and arbitrary server image paths are not
accepted. Steps/CFG 0 select model defaults; seed -1 chooses a random seed.

Python:

```python
import base64
from pathlib import Path
import requests

result = requests.post("http://127.0.0.1:7860/fibo2/generate", json={
    "variant": "edit-turbo", "mode": "edit",
    "prompt": "Change the jacket to red leather",
    "images": [base64.b64encode(Path("photo.png").read_bytes()).decode()],
    "width": 1024, "height": 1024, "seed": 1234
}, timeout=3600)
result.raise_for_status()
Path("edited.png").write_bytes(base64.b64decode(result.json()["images"][0]))
```

JavaScript (Node):

```javascript
import { readFile, writeFile } from 'node:fs/promises';
const image = (await readFile('photo.png')).toString('base64');
const response = await fetch('http://127.0.0.1:7860/fibo2/generate', {
  method: 'POST', headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({variant: 'edit-turbo', mode: 'edit',
    prompt: 'Change the jacket to red leather', images: [image], seed: 1234})
});
if (!response.ok) throw new Error(await response.text());
const result = await response.json();
await writeFile('edited.png', Buffer.from(result.images[0], 'base64'));
```

Curl (save the request as UTF-8 `request.json`; `images` are base64 strings):

```json
{"variant":"dev","mode":"generate","prompt":"A red bicycle beside a white wall","seed":1234}
```

```sh
curl -X POST http://127.0.0.1:7860/fibo2/generate -H "Content-Type: application/json" --data-binary @request.json
curl http://127.0.0.1:7860/fibo2/status
curl -X POST http://127.0.0.1:7860/fibo2/interrupt
```

## Verification

From this Forge installation's root:

```powershell
webui/venv/Scripts/python.exe -m unittest discover -s webui/extensions/sd-forge-fibo2/tests -v
webui/venv/Scripts/python.exe webui/extensions/sd-forge-fibo2/tests/run_live.py --url http://127.0.0.1:7869 --case regression
```

Unit/integration tests exercise real Gradio and FastAPI, validation, image
serialization, API authentication and mask compositing. They do not substitute
for model inference. The live script calls real local GPU endpoints and saves
PNG images, timings, settings, pixel comparisons and logs in
`webui/outputs/fibo2/validation`. Read `VALIDATION.md` for measured results.

## Sources and limitations

- [Bria Dev model card](https://huggingface.co/briaai/Fibo-2.0-dev)
- [Bria Edit Turbo model card](https://huggingface.co/briaai/Fibo-2.0-edit-turbo)
- [Official ComfyUI FIBO node](https://github.com/Comfy-Org/ComfyUI/blob/8a98ecf5864433064809ac1f2b52ccbf8b444471/comfy_extras/nodes_fibo2.py)
- [Bria/Diffusers masked-edit preprocessing](https://github.com/huggingface/diffusers/blob/main/src/diffusers/pipelines/bria_fibo2/image_processor.py)

Model quality and adherence depend on the prompt and seed. Dedicated forthcoming
Bria background removal, eraser, product-shot, expansion and multi-turn LoRAs are
not represented as released features. Ordinary instruction-based edits can ask
for such changes, with the model's usual limitations. Built-in SD ControlNet,
ADetailer, AnimateDiff, Hires fix and SD samplers are not implicitly applied to
FIBO. Use Send to img2img/Extras for supported downstream Forge processing.

FIBO weights: Bria CC BY-NC 4.0; commercial use requires Bria's agreement.
ComfyUI: GPL-3.0, retained in its separately cloned runtime with upstream license.

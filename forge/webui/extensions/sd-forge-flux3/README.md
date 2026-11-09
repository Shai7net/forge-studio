# FLUX 3 for Forge (sd-forge-flux3)

FLUX 3 Image Klein (Black Forest Labs, 2026-10-15, 5.6B) as an ordinary checkpoint of this Forge (f0.0.17).
Put the checkpoint in `models/Stable-diffusion`, choose it at the top of the page and press Generate: the text
encoder and the VAE load with it, and the page switches to the settings FLUX 3 is made for.

## Files

| What | Where | File (int8, for 8 GB cards) |
|---|---|---|
| Image model (checkpoint) | `models/Stable-diffusion` | `flux3-image-klein-int8-convrot.safetensors` (5.8 GB) |
| Text encoder (Qwen3-VL-4B-Instruct) | `models/text_encoder` | `qwen3vl_4b_int8_convrot.safetensors` (4.9 GB) |
| Tokenizer + config of the text encoder | `models/text_encoder/qwen3vl_4b_tokenizer` | from `Qwen/Qwen3-VL-4B-Instruct` |
| VAE (the FLUX.2 autoencoder) | `models/VAE` | `flux2-vae.safetensors` (0.34 GB) |

All three come from `black-forest-labs/flux-3-image-klein-comfy` on Hugging Face (accept the FLUX Kommunity
License on its page first). The bf16 files (11.1 + 8.9 GB) work too. A FLUX 3 file is recognised by its weight
names, whatever its file name. Another text encoder or VAE for one checkpoint: a `<checkpoint>.json` next to it
with `"flux3_text_encoder": "<file in models/text_encoder>"` and/or `"vae": "<file in models/VAE>"`.

## What choosing a FLUX 3 checkpoint does

- Forge's current model leaves the card and memory; FLUX 3, its text encoder and VAE load (into pinned memory).
- txt2img and img2img switch to **Euler, 16 steps, CFG 1** (the distilled Klein has no guidance; the negative
  prompt is not used) and to 1024x1024 when the size was SD 1.5 sized. Choosing another checkpoint puts the
  previous values back. (Settings > FLUX 3 turns this off.)
- Sampling is the reference recipe whatever sampler is chosen: flow-map Euler over `linspace(1, 1/N, N)` shifted
  by 6.93; each step is also told the time it jumps to. Infotext records `Sampler: Euler` and `FLUX 3 sampler`.

## Using it

- **txt2img**, batch, seeds and variation seeds, styles, Hires. fix (the first pass is enlarged with the chosen
  upscaler, then redrawn with "Denoising strength"), X/Y/Z, the API (`/sdapi/v1/txt2img`) all work.
- **Prompt writer** (FLUX 3 panel, "Auto" by default): FLUX 3 is meant to get a long, exact caption. With a
  short prompt or one in Hebrew/any other language, Qwen3-VL-4B (the text encoder itself, no extra download)
  writes the English caption with Black Forest Labs' own instructions (`system_prompts.json` of
  `flux-3-image-klein-pup`, Apache-2.0). The caption is printed in the console, shown under the image and cached
  (`cache/captions.json`), so the same request gives the same picture again. "Off" sends the prompt as typed.
- **img2img**: "Edit with reference (FLUX 3)" (default) gives the picture to FLUX 3 as a reference and draws the
  scene you describe from it (describe the result, e.g. "the same scene at night under a full moon");
  "Classic img2img" redraws with Denoising strength. Inpainting (with a mask, including "Only masked") always
  runs the classic way and Forge pastes back only the masked area.
  Another extension can give its own references with `p.flux3_references` (a list of PIL pictures): each keeps its
  shape (the first at 1024², the others sharing half as much, at least 512² each) instead of being stretched to the output like the init
  picture. Video Studio's Ingredients to Video draws its first frame this way.
- **LoRA**: `<lora:name:1>` in the prompt, FLUX 3 LoRAs in `models/Lora` (diffusers/PEFT, ComfyUI or kohya keys).
- A1111 prompt syntax is reduced to plain text (emphasis weights are dropped, `[a:b:0.5]` uses its final
  prompt, `BREAK` becomes a new line).

## How it runs on an 8 GB card (RTX 3070 Laptop)

- Weights are int8 with ComfyUI's ConvRot rotation; matmuls run W8A8 on the int8 tensor cores (`torch._int_mm`).
- The image model keeps as many of its 43 blocks on the card as fit next to the activations of the requested
  size and streams the others in on a side stream. The text encoder streams layer by layer (prompts are
  cached), the prompt writer gets the card to itself, the VAE decodes whole or in tiles.
- When Forge needs the card (an upscaler, face restoration, another checkpoint) FLUX 3 steps off it.

## Measured on the RTX 3070 Laptop (8 GB), 2026-10-26

| | |
|---|---|
| Loading on selection (model + text encoder + VAE, 9.5 GB of RAM) | 11-20 s |
| 1024x1024, 16 steps | 25 s (1.56 s/step, 40 of 43 blocks on the card, peak 6.1 GB) |
| 1024x1024, 8 steps (good for drafts) / 4 steps | 12.5 s / 6 s |
| 1920x1088, 16 steps | 63 s (33 blocks on the card, the rest streamed, peak 5.6 GB) |
| Edit with a 1024x1024 reference, 16 steps | 72 s (8,272 tokens) |
| Hires. fix 832 -> 1248 (8 steps) | 69 s in all |
| Prompt writer, ~250 words | 17 s (CUDA graph; 58 s without), then cached |
| VAE decode 1024x1024 | 1.1 s |

Edits: FLUX 3 wants the *target* described as a whole ("the same scene at night under a full moon" works, a
bare "make it winter" is weak); results vary by seed, so try two or three. Inpainting (a mask) always uses the
classic path, describing what goes into the mask.

## Tests

- `tests/test_transformer_reference.py`: this transformer against a transcription of diffusers'
  `Flux3Transformer2DModel.forward`, random weights at full width: cos 0.99998 (bf16), 0.99991 (int8 ConvRot).
- `tests/run_engine.py`: the engine alone (load, write, encode, sample, decode, timings).
- In Forge (2026-10-26): with the same noise, Forge's img2img edit and the engine alone give the same image
  (mean difference 0.7/255); SD 1.5 renders are unchanged by the extension (differences only at the level of
  Forge's own variation between processes); switching SD -> FLUX 3 -> SD three times keeps memory flat
  (no leak) and restores the sampler, steps, CFG and size.

## Licences

FLUX 3 Image Klein weights: FLUX Kommunity License v1.0 (non-commercial; commercial use of outputs for
individuals and companies under US$5M revenue). Qwen3-VL: Apache-2.0. The upsampler instructions: Apache-2.0.
Preview colour factors: ComfyUI (GPL-3.0).

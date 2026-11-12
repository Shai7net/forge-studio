# Video Studio (סטודיו וידאו)

Wan 2.2 and LTX-2.3 video generation inside Forge, built for 8 GB graphics cards (written and measured on an RTX 3070 Laptop,
8 GB, with 48 GB of system memory).

Five sub-tabs, one **Generate video** button, and a visible **Video model** selector:

| Sub-tab | What you give | What the video engine does |
|---|---|---|
| **Text to Video** | a prompt | Wan: Forge draws a first frame with the image checkpoint, then animates it. LTX: generates video directly, optionally with synchronized sound |
| **Image to Video** | one picture and a prompt | a clip that starts on the picture |
| | **➕ Last frame**: a second picture | a clip that starts on the first picture and ends on the last, doing what the prompt says in between |
| | **➕ Another picture**: up to six | one part per pair of pictures (1→2, 2→3, …), all made in one run and joined into one video; each part can have its own description, and Length is per part |
| **Ingredients to Video** | up to four pictures (people, a pet, an object, a place) and a prompt | a video with them in it, keeping their looks, doing what the prompt says (like Flow's Ingredients) |
| **Replace a character** | a video and a picture of a character | the person in the video becomes the character: motion, expressions, camera and background stay |
| **Image + Audio to Video** | a picture, an audio recording and a scene prompt | LTX animates the picture to the recording and preserves the source audio in the output |

## LTX-2.3 on limited VRAM

Choose **LTX 2.3 Distilled 1.1 · Q3 · recommended for 8 GB** above the tabs.
The model switch automatically sets the native 8-step distilled schedule, CFG 1,
24 fps, Euler ancestral sampling, and LTX quality choices. The recommended 8 GB
starting point is **416p**; 288p is a quicker draft. Exact dimensions are aligned
to 32 pixels and frame counts to 8n+1. A requested duration is rounded to the
nearest valid frame count (within one sixth of a second). Frame size and duration
still determine activation memory; a larger quant does not replace that memory.

Q3 is the default compromise between retained detail, system memory and streaming
cost. Q2 is offered for tighter memory; Q4 for higher quality with more headroom.
The three choices use the same encoders and VAEs. Model downloads are selected
individually, use pinned Hugging Face revisions, and verify SHA-256 after download.
The Q3 package is about **21.2 GB** including Gemma 3 12B Q4_K_M, the LTX-2.3
embedding connectors, and both video/audio VAEs. Existing Wan assets are retained.
Allow system RAM for CPU offload as well as VRAM; 32 GB or more is recommended.

Measured on this RTX 3070 Laptop: a 5-second 736×416 clip, 8 steps, native sound,
and a cached prompt completed in about 117 seconds, peaking at 5.99 GB allocated
VRAM. A 3-second audio-driven portrait, FILM to 48 fps and x2 enlargement to
832×1408 completed in about 186 seconds, at 6.07 GB allocated VRAM. Cold text
encoding adds time; timings vary with the scene, resolution and available memory.

Under **Models**, download the missing files and press **Set up LTX engine** once.
The LTX worker has its own environment under `video-studio/ltx/env`. Setup can
reference an already installed compatible FIBO or Video Studio environment
read-only, avoiding duplicate CUDA installations. Additional packages install in
the LTX environment only. ComfyUI and ComfyUI-GGUF are pinned to the revisions in
`videostudio/ltx.py`; no separately running ComfyUI server is required. The native
Comfy LTX nodes perform conditioning, joint audio/video sampling and tiled VAE
decoding. GGUF blocks stream between CPU and CUDA; the text encoder unloads before
the diffusion model starts. Encoded prompts are cached between jobs.

Both backends share Image to Video, first/last frame conditioning, chains of up to
six pictures, Continue, Styles, FILM and enlargement. Ingredients with LTX goes through
one FLUX 3 Klein first frame of all the pictures (see Ingredients to Video below).
Phantom and character replacement remain their dedicated **Wan** models, even
when LTX is selected; they are not represented as native LTX capabilities.

**LTX synchronized sound** generates sound during video sampling and takes priority
over automatic MMAudio. Disable it to make a silent clip or use the existing
MMAudio workflow. **Image + Audio to Video** uses the supplied recording as audio
conditioning and copies that audio into the final clip, including after FILM.
Use a clear, visible face for dialogue; prompt following and lip quality depend
on the shot and model. For deterministic speech editing, the existing speech and
MuseTalk tools also remain available after either backend generates a clip.

LTX Refine re-encodes the rendered motion at the requested size (up to 576p on
these profiles), runs the late distilled steps, and retains its original audio.
Refine follows the last video's model family; it never sends LTX latents to Wan.
Multi-part refinement follows the existing single-clip restriction.

LoRAs are separated by model family. LTX adapters live under
`models/VideoStudio/ltx/loras` or `models/Lora/ltx`; they can be selected in the
normal slots or referenced by `<lora:name:weight>`. Wan adapters never load into
LTX. A LoRA with no compatible model layers fails with an explicit message.

The worker uses the existing job lifecycle: progress events, queue locking,
background completion and cancellation. Outputs retain `.first.png`, `.last.png`
and `.json`; LTX metadata includes the backend, base frame rate/count, audio
mode, model and peak allocated VRAM. These private outputs and downloaded model
files do not belong in a source release.

The 🎬 button under the txt2img and img2img results sends the picture to Image to Video (it used to go to the SVD
tab).

Forge Studio's frame dialog and Simulation 360's camera button can also send PNG frames here. Repeated sends
fill the next free Image to Video picture (up to six) or Ingredient (up to four), preserving existing inputs.
Image to Video reveals additional rows and updates its picture count automatically. A full destination asks
you to clear a slot; it never replaces a picture silently. Capture several shots or world viewpoints to collect
references, then write the prompt and Generate when ready.

In a video made from several pictures, each picture is a frame of the video (part 1 ends on picture 2 and part 2
starts on it, so that frame is kept once). The two Wan experts are loaded once for all the parts, and FILM and the
enlargement run once on the joined video. Refine works on single clips, not on these.

After a clip is made, **Continue from the last frame** puts its last frame into Image to Video, so a longer
scene can be built from 5-second pieces.

**Refine the last video** is a video Hires fix. Make takes quickly at 480p (about 5 min for 5 s, 8 min for 7 s),
and when one is right, refine it to 576p or 720p. The take is encoded at the larger size and only the last one
(light) or two (stronger) of the four steps run on it, so the motion and the composition stay and the detail is
drawn again. 576p is exactly 1080x1920 after x1.875 enlargement. 720p fits clips of up to 5 s on 8 GB.

A job runs on by itself: reloading or closing the page does not stop it, and Forge's queue stays held until it
ends. **Show the last video** brings back the newest result (or the progress of the running job).

## Sound and speech

**Sound and speech for the last video** puts sound on a finished clip:

* **AI sound effects (MMAudio large 44.1 kHz)**, generated from the frames themselves (what is seen, and when it
  moves) plus optional plain English words ("waves on a beach, footsteps on sand"). Clips longer than 8 s are made in
  overlapping 8 s windows and crossfaded. When the clip also has speech, the background is made from the words only:
  a talking face on screen makes MMAudio murmur.
* **Speech from text**, in Chatterbox's 23 languages. Hebrew goes through Phonikud first, which adds the vowel marks
  and stress (Chatterbox's Hebrew is barely intelligible without them). Voices: four Hebrew samples (two men, two
  women), or **copy the voice of a recording** (5-15 s of clear speech, any language). A fast Hebrew voice (Piper,
  one male voice, a few seconds a line) is there too. Phonikud's voices are licensed for non-commercial use.
* **Speech from a recording**: a sound file or the microphone.
* **Lips moved to the words (MuseTalk 1.5)** over the whole clip, so the lower face looks the same before, during
  and after the words. If the speech is longer than the clip, the video plays once and holds its last frame;
  MuseTalk continues animating the mouth there. Motion is never replayed or reversed to fill long speech.
* **The mix**: speech at its start time, the background ducked under it, the whole at about -14 LUFS.

Measured on the RTX 3070 Laptop: a 7 s Hebrew line takes about 1 minute (Chatterbox), effects for a 7 s clip about
1.5 minutes, lips about 1.5 minutes. Faces need to be reasonably large for lip sync: make a medium close-up (Image to
Video from a crop) for a character who talks.

## Ingredients to Video

Choose the workflow in the tab; it stays interactive for both video families.

**Automatic** (the default) uses the first-frame way whenever a complete FLUX 3 Klein is installed, and Phantom
otherwise. Explicit Phantom uses Wan independently of the main video selector; the result details name the engine
actually used.

**First frame by FLUX 3 Klein, then the selected video model.** FLUX 3 Klein draws one frame of the scene with all
the ingredient pictures as its references - its own editing, each picture at its own shape (`p.flux3_references` in
sd-forge-flux3) - and the selected Wan or LTX model animates it as Image to Video. Klein reads a whole character sheet
and keeps the face, the clothes and the drawn style; Image to Video keeps what the first frame shows. Measured on
Shai's 3D character sheet (2026-11-09): this kept him; Phantom kept his clothes but drew a different, real person.

FLUX 3 does not have to be the selected image checkpoint: with another one selected, the extension loads Klein's own
engine for that one picture and unloads it afterwards; the selected checkpoint and its options stay as they were.
The frame is drawn at 8 steps: on a two-character frame (1376x768, two references) 8 steps looked as good as 16 in
42 s instead of 83 (2026-11-11). Measured end to end with two pictures and LTX Q3 at 416p, 5 s, native sound, FILM
48 fps and x2 enlargement: 6.5 min, both characters kept through the shot, 5.8 GB peak VRAM. A temporary engine adds
about 20 s of loading. Video Studio's Stop works during this first-frame phase as well as during the video worker.

A native LTX Ingredients route (the official IC-LoRA recipe: Dev Q3 + Ingredients adapter on a reference sheet) was
built and tested on 2026-11-11 and removed: it returned the pictures as separate panels instead of one scene, needed
a 23 GB extra package, and was slower than the first-frame way.

**Phantom: the pictures go into the video.** Phantom-Wan 14B (ByteDance's subject-to-video model on Wan 2.1 14B; the FusionX merge, so 8 steps at CFG 1 are
enough; Q3_K_M GGUF, 7.2 GB in `models/VideoStudio/phantom`). Each picture is encoded by the VAE into one latent frame
of its own, and they go along after the video's frames, clean at every step (ComfyUI's `WanPhantomSubjectToVideo` /
`time_dim_concat`); the model's answer for them is dropped. The pictures are fitted whole on white, as Phantom was
trained (a crop would cut a head off). Name each ingredient in the prompt ("the white dog from picture 1..."). The
shape is the Shape at the top of the tab (shared by every sub-tab, with the picture sampler). Wan 2.2 high/low LoRAs
are left out (another model); Wan 2.1 14B LoRAs fit.
Measured on the RTX 3070 Laptop: 832 x 480, 5 s, two ingredients in 8.5 min.

**Character sheets** (`videostudio/sheets.py`). A reference sheet - front, side and back, rows of heads, a close-up,
on a studio backdrop - shows one person many times and small; given whole, Phantom reads a crowd and the person in
the video comes out a stranger. So a sheet is split before it goes in: Time Strata's BiRefNet-lite cuts the figures
out (on the card in under a second when it has room, ~6-20 s on the processor), the tallest are the full-body views,
the front is the first of the widest (arms out; the side view is narrow, the back comes after the front), and a big
figure cut by the bottom edge is the close-up. The front and the close-up go in as two ingredients of the same
person. It runs only for pictures on a smooth backdrop, and only when the full-body figures share their colours
(same clothes, same skin), so a photo of several different people is used whole. The details under the video say
when a sheet was split.

## Replace a character (video to video)

Wan 2.2 Animate 14B in its replacement mode (Q3_K_S GGUF, 8 GB on disk), with the lightx2v I2V distill LoRA (so 6
steps are enough) and the relight LoRA (the character takes the scene's light). What happens:

1. **Find the people** numbers everyone on the first frame, from the left; choose who is replaced.
2. The video is read at 16 (or 24) fps. DWPose finds that person's whole-body pose on every frame and follows them
   (box overlap from frame to frame); a 512 x 512 crop of the face is taken per frame; SAM 2.1 cuts the person out,
   prompted with their body points on four key frames (other people's points are marked "not this one").
3. The cut-out is grown (7 x 7 dilation, three times) and, by default, filled to its box (the official setting: the
   character may have another shape, hair or clothes); **Outline** keeps more of the background instead.
4. Wan draws the character from the picture into the hole, following the skeleton, with the face motion through
   its face adapter, in parts of 77 frames; each part starts from the last 5 frames of the one before, so they join.
5. The video's own sound is put back (or MMAudio makes one if it had none).

The prompt is optional (empty: the official "the person in the video is moving"), but describing the character
(clothes, trousers, shoes, hair) makes a real difference. Best character pictures: the whole body, facing the camera,
on a plain background. Wan Animate takes ONE character per picture: a picture of several people is numbered too, and
the chosen one is cut out (SAM) onto white before use; **Who becomes whom → Everyone in the picture** replaces each
person of the video with one of the picture (from the left, or reversed), one pass per person, each pass on the
result of the one before. Very different body shapes (a child for an adult) can bend.

Character sheets are recognized before group-person segmentation. The complete
front view is used for Animate, and a large close-up is retained for the optional
face pass. The repeated views are one identity in Find the people as well.
This avoids SAM treating a sheet's many heads as separate people and erasing the
body with its negative prompts. The worker shows and saves the actual front view.

**Make the faces like the picture** (optional, off by default; available when FaceFusion is installed next to Forge)
finishes with a face swap: at 480p Wan draws a dancer's face at about 40 pixels with the driving face's expression,
so it is a look-alike; HyperSwap (512 boost) + GFPGAN put each character's real face from the picture on their dancer,
matched by position on the first frame, about a minute per person. Use this for photographic faces;
leave it off for drawn and 3D characters, whose style it can change. The video without it is kept as `-raw.mp4`.

The person is followed by SAM 2 (prompted on the first frame), not by boxes: when two dancers cross, box overlap
jumped to the partner. Each frame's skeleton is the detected body whose points lie inside SAM's cut.

Each frame's area also covers its two neighbours on each side: the VAE packs four frames into one latent frame, and
a fast-moving skirt otherwise left traces in the background that came out as white smoke.

## The Wan model

Wan 2.2 I2V A14B with the lightx2v 4-step distillation of 2026-04-12 (`lightx2v/Wan2.2-Distill-Models`), as GGUF
(`Abiray/Wan2.2-LightX2V-260412-4STEP-GGUF`), Q3_K_M: 7.2 GB for each of the two experts. The high-noise expert
lays out the motion for the first two steps, the low-noise expert draws the detail in the last two. CFG 1,
shift 5, Euler. The text encoder (UMT5-XXL fp8) and the VAE (Wan 2.1) are ComfyUI's repackaged files.
A Q4_K_M pair (19.3 GB) is listed under Models for a sharper result when there is disk space.

## How it fits in 8 GB

* **Separate process.** Each job runs in `videostudio/engine/worker.py`; Forge frees its own models first and
  holds its queue while the job runs. Everything the job loads is returned when it exits.
* **Weights stay in system memory.** The 40 transformer blocks are copied to the card one at a time on a side
  stream while the previous block computes; as many blocks as fit stay on the card.
* **3-bit weights are decoded on the card** in one fused kernel just before their matrix product.
* **int8 math.** Each matrix product runs on the int8 tensor cores (weight per channel, activations per
  token): 16 ms instead of 44.7 ms for an 8192x5120x13824 product on this card.
* **SageAttention** (8-bit attention): 0.54 s instead of 1.19 s for one 480p self-attention.
* **The VAE's 3D convolutions run as 2D convolutions.** PyTorch 2.9 for Windows runs Conv3d without cuDNN
  (4.6 s and 9.4 GB for one layer); as a sum of 2D convolutions it takes 0.11 s and 2.6 GB.
* **Memory cap.** On Windows a full card borrows system memory instead of failing, and the job then crawls.
  The worker caps itself at the free memory, and when that runs out it repeats the step with fewer blocks
  on the card and smaller slices. Large frames are encoded and decoded in tiles.

## Engine environment

The worker runs in `webui/video-studio/env` (PyTorch 2.9.1 + CUDA 13, SageAttention 2.2, Triton). On this PC it is
a hard-linked copy of the Forge Neo environment (`tools/link_env.py`, no extra disk space). Without it the worker
runs on Forge's own Python, slower (no SageAttention; Forge's PyTorch has no FlashAttention on Windows).

## Files

```
models/VideoStudio/diffusion/      the two experts (.gguf)
models/VideoStudio/text_encoders/  umt5_xxl_fp8_e4m3fn_scaled.safetensors
models/VideoStudio/vae/            wan_2.1_vae.safetensors
models/VideoStudio/loras/          Wan 2.2 LoRAs (also read from models/Lora/wan)
models/VideoStudio/animate/        Wan 2.2 Animate 14B (.gguf), lightx2v I2V and relight LoRAs
models/VideoStudio/sam2.1-hiera-small/  SAM 2.1 (cuts the person out)
models/ControlNetPreprocessor/     CLIP-ViT-H-14 and DWPose (openpose/), shared with ControlNet
output/video-studio/<date>/        clips (.mp4) with .json settings and first/last frame pictures
models/VideoStudio/voices/         Chatterbox multilingual, Phonikud, Piper, reference voices (add your own .wav)
video-studio/voice-env/            the voices' Python (hard links of Simulation 360's Chatterbox env + Forge's GPU PyTorch)
video-studio/cache/                prompt embeddings and compiled kernels
video-studio/jobs/                 each job's settings, inputs and worker log
```

## Options

* **Quality**: 480p, 576p or 720p at the pictures' aspect ratio. Time grows faster than the pixel count.
* **Length**: 1 to 7.5 s at 16 fps (Wan 2.2 is trained on 81 frames = 5 s).
* **Smooth motion**: FILM in-between frames (Deforum's model), 16 -> 32 fps.
* **Enlarge**: Real-ESRGAN x2 on every frame after interpolation.
* **LoRA**: two slots; a file named `...high...` / `...low...` goes to that expert only.
* **Advanced**: model, steps, shift, CFG (above 1 the negative prompt is used and each step costs double),
  sampler (euler / lcm), attention, int8 on/off, exact width and height.

Write prompts in English and describe the motion in order: what moves, how, and where the camera is.
Between two very different pictures, describe ONE continuous shot ("the camera tracks with him as the sand gives
way to a courtyard, no cuts") and spell out each action ("pushes his right arm into the sleeve, then the left");
give the story room: 7 s (113 frames) did a whole change of clothes where 5 s cut to the last frame.

## Engine API (for scripts)

```python
import json, subprocess
job = {
  "mode": "flf2v", "prompt": "...", "start_image": "a.png", "end_image": "b.png",
  "width": 480, "height": 832, "frames": 81, "fps": 16, "steps": 4, "shift": 5.0, "cfg": 1.0, "seed": 1,
  "text_encoder": ".../umt5_xxl_fp8_e4m3fn_scaled.safetensors", "vae": ".../wan_2.1_vae.safetensors",
  "experts": [{"path": ".../high.gguf", "until_sigma": 0.9, "label": "high noise"},
              {"path": ".../low.gguf", "label": "low noise"}],
  "cache_dir": "...", "output_dir": "...", "basename": "clip"}
json.dump(job, open("job.json", "w"))
subprocess.run([r"webui\video-studio\env\Scripts\python.exe", r"extensions\sd-forge-video-studio\videostudio\engine\worker.py", "job.json"])
```

Several pictures: `"parts": [{"start_image", "end_image", "prompt"}, ...]` (one per pair; `"seed"` + part index is
each part's noise); the parts are joined and finished as one video.

Replacing a character: `"mode": "replace"`, `"driving_video"`, `"character_image"`, `"person"` (0 = leftmost),
`"start"`, `"seconds"`, `"fps"`, `"mask_shape"` (`box` / `outline`), `"keep_sound"`, `"model"` (the Animate .gguf),
`"clip_vision"`, `"pose_detector"`, `"pose_model"`, `"sam_model"` (folder), and the lightx2v / relight files in
`"loras"`. The job folder gets `prepared.mp4` (the mask and the skeleton side by side) for checking.

Refining: add `"init_video": "take.mp4"`, `"init_every": 2` (when the take was made at 32 fps with FILM) and
`"denoise_start_step": 3` (light) or `2` (stronger).

The worker prints progress as `@@VS {json}` lines and ends with `{"stage": "done", "video": ...}` or
`{"stage": "error", ...}`. Creating the file named in `cancel_file` stops it after the current block.

## Credits

LTX: Lightricks LTX-2.3 / Distilled 1.1 (LTX community license), GGUF quantization
by Unsloth, ComfyUI native LTX nodes (GPL-3.0), and City96 ComfyUI-GGUF
(Apache-2.0). Sources and reference workflows:
https://huggingface.co/Lightricks/LTX-2.3,
https://huggingface.co/unsloth/LTX-2.3-GGUF,
https://docs.comfy.org/tutorials/video/ltx/ltx-2-3,
https://github.com/Lightricks/ComfyUI-LTXVideo,
https://github.com/city96/ComfyUI-GGUF.

Model code follows ComfyUI (`comfy/ldm/wan`) and Forge Neo, after Wan-Video/Wan2.1 (Apache-2.0). GGUF dequantisation
follows llama.cpp's formats via gguf-py. Weights: Wan-AI (Apache-2.0), lightx2v distillation, GGUF by Abiray.
Ingredients: Phantom (Phantom-video, Apache-2.0), the FusionX merge by vrgamedevgirl84, GGUF by QuantStack.
Replacement follows Wan-Video/Wan2.2 `wan/animate.py` and its preprocessing (Apache-2.0; the skeleton drawing is
copied from it) and ComfyUI's `model_animate.py` / `WanAnimateToVideo`; Animate GGUF by QuantStack, relight and
lightx2v LoRAs from Kijai's WanVideo_comfy; DWPose (Apache-2.0) as in Forge's ControlNet; SAM 2.1 (Meta, Apache-2.0)
through transformers.

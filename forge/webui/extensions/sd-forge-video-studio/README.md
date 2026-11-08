# Video Studio (סטודיו וידאו)

Wan 2.2 video generation inside Forge, built for 8 GB graphics cards (written and measured on an RTX 3070 Laptop,
8 GB, with 48 GB of system memory).

Three sub-tabs, one **Generate video** button:

| Sub-tab | What you give | What Wan does |
|---|---|---|
| **Text to Video** | a prompt | Forge draws the first frame with the checkpoint selected at the top of the page, then Wan animates it |
| **Image to Video** | one picture and a prompt | a clip that starts on the picture |
| | **➕ Last frame**: a second picture | a clip that starts on the first picture and ends on the last, doing what the prompt says in between |
| | **➕ Another picture**: up to six | one part per pair of pictures (1→2, 2→3, …), all made in one run and joined into one video; each part can have its own description, and Length is per part |
| **Replace a character** | a video and a picture of a character | the person in the video becomes the character: motion, expressions, camera and background stay |

The 🎬 button under the txt2img and img2img results sends the picture to Image to Video (it used to go to the SVD
tab).

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
  and after the words. If the speech is longer than the clip, its last second plays back and forth.
* **The mix**: speech at its start time, the background ducked under it, the whole at about -14 LUFS.

Measured on the RTX 3070 Laptop: a 7 s Hebrew line takes about 1 minute (Chatterbox), effects for a 7 s clip about
1.5 minutes, lips about 1.5 minutes. Faces need to be reasonably large for lip sync: make a medium close-up (Image to
Video from a crop) for a character who talks.

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

**Make the faces like the picture** (on when FaceFusion is installed next to Forge, `C:\ForgeStudioacefusion`)
finishes with a face swap: at 480p Wan draws a dancer's face at about 40 pixels with the driving face's expression,
so it is a look-alike; HyperSwap (512 boost) + GFPGAN put each character's real face from the picture on their dancer,
matched by position on the first frame, about a minute per person. The video without it is kept as `-raw.mp4`.

The person is followed by SAM 2 (prompted on the first frame), not by boxes: when two dancers cross, box overlap
jumped to the partner. Each frame's skeleton is the detected body whose points lie inside SAM's cut.

Each frame's area also covers its two neighbours on each side: the VAE packs four frames into one latent frame, and
a fast-moving skirt otherwise left traces in the background that came out as white smoke.

## The model

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

Model code follows ComfyUI (`comfy/ldm/wan`) and Forge Neo, after Wan-Video/Wan2.1 (Apache-2.0). GGUF dequantisation
follows llama.cpp's formats via gguf-py. Weights: Wan-AI (Apache-2.0), lightx2v distillation, GGUF by Abiray.
Replacement follows Wan-Video/Wan2.2 `wan/animate.py` and its preprocessing (Apache-2.0; the skeleton drawing is
copied from it) and ComfyUI's `model_animate.py` / `WanAnimateToVideo`; Animate GGUF by QuantStack, relight and
lightx2v LoRAs from Kijai's WanVideo_comfy; DWPose (Apache-2.0) as in Forge's ControlNet; SAM 2.1 (Meta, Apache-2.0)
through transformers.

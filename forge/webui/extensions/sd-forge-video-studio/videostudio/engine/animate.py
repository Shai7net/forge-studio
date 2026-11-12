"""Character replacement with Wan 2.2 Animate 14B: the person in a video becomes the character in a picture, with
the video's motion, face, camera and background kept.

Follows the official replacement mode (Wan-Video/Wan2.2 wan/animate.py, Apache-2.0) and ComfyUI's
WanAnimateToVideo:
  1. prepare  -> skeleton video, face crops, person mask, background (animate_prep)
  2. prompt   -> UMT5; reference picture -> CLIP ViT-H penultimate features (257 x 1280)
  3. chunks of 77 frames (20 latent frames + the reference frame in front), each one:
       condition = [reference latent | VAE(background, with the last frames of the previous chunk in front)]
                   with a mask that marks the reference, the carried-over frames and the background as known;
       the skeleton latent is added to the patch embedding, the face motion goes through the face adapter;
       Euler over shifted sigmas (the lightx2v distill LoRA makes 4-8 steps enough), decode, drop the overlap
  4. the original sound is put back, FILM / enlargement as for the other modes
One model is loaded once for all chunks; between chunks the blocks leave the card for the VAE.
"""

import os
import subprocess
import time

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image

from . import attention as attn
from . import wan as wan_mod
from .animate_model import AnimateWanModel
from .gguf_load import read_model, strip_prefix
from .ops import INT8, BlockStreamer
from .pipeline import (ENCODE_BYTES_PER_PIXEL, DECODE_BYTES_PER_PIXEL, activation_reserve_gb, auto_tile,
                       encode_prompts, ffmpeg_exe, finish, free_memory, plan_resident, preview_png, sigmas_for,
                       start, usable_free, write_outputs)
from .vae import WanVAE
from .wan import attach_weights, config_from_shapes

CLIP_LEN = 77
SAMPLE_PROMPT = "视频中的人在做动作"  # the official default: "the person in the video is moving"
CLIP_MEAN = (0.48145466, 0.4578275, 0.40821073)
CLIP_STD = (0.26862954, 0.26130258, 0.27577711)


def pad_resize(path, width, height):
    """The character picture fitted inside width x height on black (the official padding_resize), [3, H, W] in
    [-1, 1]. Transparent pictures go on black too."""
    im = Image.open(path)
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        im = im.convert("RGBA")
        im = Image.alpha_composite(Image.new("RGBA", im.size, (0, 0, 0, 255)), im)
    im = im.convert("RGB")
    scale = min(width / im.width, height / im.height)
    nw, nh = max(1, int(im.width * scale)), max(1, int(im.height * scale))
    canvas = Image.new("RGB", (width, height))
    canvas.paste(im.resize((nw, nh), Image.LANCZOS), ((width - nw) // 2, (height - nh) // 2))
    a = torch.from_numpy(np.asarray(canvas).astype(np.float32) / 127.5 - 1.0)
    return a.permute(2, 0, 1)


def clip_features(path, ref, device):
    """CLIP ViT-H/14 (laion2b; the image tower of Wan's xlm-roberta CLIP) output of the 31st of 32 layers."""
    from safetensors.torch import load_file
    from transformers import CLIPVisionConfig, CLIPVisionModel
    cfg = CLIPVisionConfig(hidden_size=1280, intermediate_size=5120, num_hidden_layers=32, num_attention_heads=16,
                           image_size=224, patch_size=14, hidden_act="gelu", layer_norm_eps=1e-5, projection_dim=1024)
    model = CLIPVisionModel(cfg)
    sd = {k: v for k, v in load_file(path).items() if k.startswith("vision_model.") and "position_ids" not in k}
    missing, _ = model.load_state_dict(sd, strict=False)
    if [m for m in missing if "position_ids" not in m]:
        raise RuntimeError(f"CLIP vision: missing {missing[:3]}")
    model = model.to(device, torch.float16).eval()
    img = F.interpolate(ref[None].float(), size=(224, 224), mode="bicubic", align_corners=False)
    img = (img * 0.5 + 0.5)
    mean = torch.tensor(CLIP_MEAN).view(1, 3, 1, 1)
    std = torch.tensor(CLIP_STD).view(1, 3, 1, 1)
    img = ((img - mean) / std).to(device, torch.float16)
    out = model(pixel_values=img, output_hidden_states=True).hidden_states[-2]
    del model
    free_memory()
    return out.to(torch.bfloat16).cpu()


def load_animate(path, device, report):
    t0 = time.time()
    weights, shapes = read_model(path, pinned=True, on_progress=lambda n, t: report("loading", n / t,
                                                                                     note="loading Wan Animate"))
    weights, shapes = strip_prefix(weights, shapes)
    cfg = config_from_shapes(shapes)
    model = AnimateWanModel(**cfg)
    unused, missing = attach_weights(model, weights)
    if missing:
        raise RuntimeError(f"{os.path.basename(path)}: missing weights {missing[:5]}")
    model.set_dense(weights, device)
    for w in (model.patch_embedding.weight, model.patch_bias.weight, model.pose_patch_embedding.weight,
              model.pose_patch_bias.weight):
        w.gpu = w.cpu.to(device)
    report("loading", 1.0, note=f"Wan Animate loaded in {time.time() - t0:.0f}s")
    return model, cfg, weights


def parts(n, clip_len, overlap):
    """(first frame, length) of each part. Parts are `clip_len` frames, each starting `overlap` frames before the
    previous one ends; the last (or only) one is cut to the 4k + 1 frames it needs (the official code pads the video
    to whole parts instead, which made 72 frames for a 3-frame tail)."""
    plan, s0 = [], 0
    while True:
        need = n - s0
        length = min(clip_len, max(9, 1 + 4 * -(-(need - 1) // 4)))
        plan.append((s0, length))
        if s0 + length >= n:
            return plan
        s0 += length - overlap


def _pad(a, n):
    return a if len(a) >= n else np.concatenate([a, np.repeat(a[-1:], n - len(a), axis=0)])


def _video(a, device, dtype):
    """uint8 [T, H, W, 3] -> [1, 3, T, H, W] in [-1, 1]."""
    t = torch.from_numpy(np.ascontiguousarray(a)).to(device).permute(3, 0, 1, 2).unsqueeze(0)
    return t.to(dtype).div_(127.5).sub_(1.0)


def _encode(vae, video, device, report):
    h, w = video.shape[-2:]
    tile = auto_tile(w, h, ENCODE_BYTES_PER_PIXEL, usable_free(device))
    try:
        return vae.encode(video, tile=tile).float()
    except torch.cuda.OutOfMemoryError:
        free_memory()
        tile = max(32, (tile or min(h, w) // 8) // 2)
        report("condition", None, note=f"retrying in tiles of {tile * 8}px")
        return vae.encode(video, tile=tile).float()


def _decode(vae, x, device, report, label):
    lh, lw = x.shape[-2:]
    tile = auto_tile(lw * 8, lh * 8, DECODE_BYTES_PER_PIXEL, usable_free(device))
    on_frame = lambda i, n: report("decode", i / n, note=label)  # noqa: E731
    try:
        return vae.decode(x, tile=tile, on_frame=on_frame)
    except torch.cuda.OutOfMemoryError:
        free_memory()
        tile = max(32, (tile or min(lh, lw)) // 2)
        report("decode", 0.0, note=f"retrying in tiles of {tile * 8}px")
        return vae.decode(x, tile=tile, on_frame=on_frame)


def i2v_mask(known, lt, lh, lw):
    """[T, h, w] (1 = known) -> [1, 4, lt, h, w]: the first frame counted four times, as Wan's VAE packs it."""
    m = torch.cat([known[:1].repeat(4, 1, 1), known[1:]], dim=0)
    return m.view(lt, 4, lh, lw).transpose(0, 1).unsqueeze(0)


def mux_sound(video, source, start, seconds, target):
    """The driving video's own sound under the new picture (nothing is done when it has none)."""
    cmd = [ffmpeg_exe(), "-y", "-loglevel", "error", "-i", video]
    if start:
        cmd += ["-ss", f"{float(start):.3f}"]
    cmd += ["-i", source, "-map", "0:v:0", "-map", "1:a:0?", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k"]
    if seconds:
        cmd += ["-t", f"{float(seconds):.3f}"]
    cmd += ["-movflags", "+faststart", target]
    r = subprocess.run(cmd, capture_output=True)
    return r.returncode == 0 and os.path.isfile(target)


def generate_replace(job, emit):
    from . import animate_prep
    report, device, dtype = start(job, emit)
    width, height = int(job["width"]), int(job["height"])
    lh, lw = height // 8, width // 8
    overlap = int(job.get("overlap", 5))
    steps = int(job["steps"])
    seed = int(job["seed"])

    # 1. the character (one person) and the driving video
    job = dict(job, character_image=animate_prep.character_picture(job, device, report))
    prep = animate_prep.prepare(job, device, report)
    n = len(prep["frames"])
    free_memory()

    # 2. prompt and reference
    job = dict(job, prompt=(job.get("prompt") or "").strip() or SAMPLE_PROMPT)
    contexts = encode_prompts(job, device, report)
    ref = pad_resize(job["character_image"], width, height)
    report("condition", 0.1, note="reading the character picture (CLIP)")
    clip_fea = clip_features(job["clip_vision"], ref, device)

    # 3. model, once
    model, mcfg, weights = load_animate(job["model"], device, report)
    if job.get("loras"):
        from . import lora
        lora.apply(model, job["loras"], "animate", device,
                   lambda name, hit: report("loading", 1.0, note=f"LoRA {name}: {hit} layers"), weights=weights)
    ctx = [c.to(device, dtype).unsqueeze(0) for c in contexts]
    cfg_scale = float(job.get("cfg", 1.0))
    sigmas = sigmas_for(steps, float(job["shift"]))
    gen = torch.Generator("cpu").manual_seed(seed)

    vae = WanVAE.load(job["vae"], device, dtype)
    ref_latent = _encode(vae, ref[None, :, None].to(device, dtype), device, report)
    plan = parts(n, CLIP_LEN, overlap)
    end = plan[-1][0] + plan[-1][1]
    pose_all, face_all = _pad(prep["pose"], end), _pad(prep["face"], end)
    bg_all, mask_all = _pad(prep["bg"], end), _pad(prep["mask"], end)
    chunks = len(plan)
    tokens = ((max(length for _, length in plan) - 1) // 4 + 2) * (lh // 2) * (lw // 2)
    preview_path = os.path.join(job.get("work_dir") or job["output_dir"], "preview.png")
    nblocks = mcfg["num_layers"]
    passes = 2 if cfg_scale > 1 else 1
    total = chunks * steps * nblocks * passes
    done = [0]
    resident = None
    out, prev = [], None

    for ci, (s0, length) in enumerate(plan):
        s1 = s0 + length
        lt = (length - 1) // 4 + 1
        label = f"part {ci + 1}/{chunks}"
        # condition: skeleton latent, background latent with the carried-over frames, mask, face motion
        vae.to(device)
        report("condition", 0.3, note=f"{label}: encoding the skeleton and the background")
        pose_latent = _encode(vae, _video(pose_all[s0:s1], device, dtype), device, report)
        bg = _video(bg_all[s0:s1], device, dtype)
        known = 1.0 - torch.from_numpy(mask_all[s0:s1].astype(np.float32))
        known = F.interpolate(known[:, None], size=(lh, lw), mode="nearest")[:, 0]
        if prev is not None:
            bg[:, :, :overlap] = prev.to(device, dtype)
            known[:overlap] = 1.0
        bg_latent = _encode(vae, bg, device, report)
        del bg
        y = torch.cat([
            torch.cat([torch.ones(1, 4, 1, lh, lw, device=device), ref_latent], dim=1),
            torch.cat([i2v_mask(known, lt, lh, lw).to(device), bg_latent], dim=1),
        ], dim=2)
        del bg_latent
        faces = torch.from_numpy(face_all[s0:s1]).to(device).permute(0, 3, 1, 2).float().div_(127.5).sub_(1.0)
        motion = model.face_motion(faces, lt + 1)
        motion_neg = model.face_motion(faces * 0 - 1, lt + 1) if cfg_scale > 1 else None
        del faces
        vae.to("cpu")
        free_memory()

        # sampling
        if resident is None:
            # + the face adapter's slices, the 769-token image+text context and LoRA layers in transit: 10 resident
            # blocks with 0.3 GB more ran out of memory at 480p (2026-10-25)
            reserve = activation_reserve_gb(tokens, mcfg["dim"], ffn_dim=mcfg["ffn_dim"]) + 1.2
            resident = plan_resident(model, device, reserve) if job.get("resident", True) else 0
        streamer = BlockStreamer(model.blocks, device, resident=resident)
        report("sampling", done[0] / total, note=f"{label}: {resident}/{nblocks} blocks resident")

        def on_block(i):
            done[0] += 1
            report("sampling", min(done[0] / total, 0.999), note=label)

        def predict(inp, t):
            nonlocal streamer, resident
            for attempt in range(4):
                mark = done[0]
                try:
                    v = model(inp, t, ctx[0], streamer, on_block, clip_fea, pose_latent, motion).float()
                    if cfg_scale > 1:
                        v_neg = model(inp, t, ctx[1], streamer, on_block, clip_fea, pose_latent, motion_neg).float()
                        v = v_neg + cfg_scale * (v - v_neg)
                    if not torch.isfinite(v).all():
                        if attempt == 3 or (attn.name() == "sdpa" and not INT8["on"]):
                            raise RuntimeError("The model produced invalid numbers (NaN) even with exact attention.")
                        attn.choose("sdpa", device)
                        INT8["on"] = False
                        done[0] = mark
                        report("sampling", done[0] / total, note="invalid numbers: redoing the step with exact attention")
                        continue
                    return v
                except torch.cuda.OutOfMemoryError:
                    if attempt == 3:
                        raise
                    done[0] = mark
                    streamer.close()
                    free_memory()
                    resident = resident // 3 if attempt == 0 else 0
                    wan_mod.FFN_SLICE = max(2048, wan_mod.FFN_SLICE // 2)
                    INT8["rows"] = max(2048, INT8["rows"] // 2)
                    attn.CHUNK["max_bytes"] = max(32 * 1024 ** 2, attn.CHUNK["max_bytes"] // 2)
                    streamer = BlockStreamer(model.blocks, device, resident=resident)
                    report("sampling", done[0] / total, note=f"memory full: retrying with {resident} blocks resident")

        x = torch.randn(1, 16, lt + 1, lh, lw, generator=gen, dtype=torch.float32).to(device)
        for i in range(steps):
            sigma, sigma_next = float(sigmas[i]), float(sigmas[i + 1])
            t = torch.tensor([sigma * 1000.0], device=device)
            v = predict(torch.cat([x, y], dim=1), t)
            x = x + (sigma_next - sigma) * v
            preview_png(x - sigma_next * v if sigma_next > 0 else x, preview_path)
            report("sampling", done[0] / total, step=i + 1, steps=steps, preview=preview_path, note=label)
            del v
        streamer.close()
        del streamer, y, pose_latent, motion, motion_neg
        free_memory()

        # decode, without the reference frame
        vae.to(device)
        video = _decode(vae, x[:, :, 1:].to(dtype), device, report, label)
        del x
        prev = video[:, :, -overlap:].float().cpu()
        frames = ((video[0].permute(1, 2, 3, 0).float() + 1) * 127.5).round().clamp(0, 255).byte().cpu().numpy()
        del video
        out.append(frames if ci == 0 else frames[overlap:])
        vae.to("cpu")
        free_memory()

    del model, vae
    free_memory()
    frames_u8 = np.concatenate(out)[:n]
    frames_u8 = finish(job, frames_u8, device, report)
    job = dict(job, frames=int(frames_u8.shape[0]), fps=int(job.get("fps", 16)))
    result = write_outputs(job, frames_u8, report)
    # the picture the character was drawn from (cut out of a group picture), for the face pass after
    Image.open(job["character_image"]).convert("RGB").save(result["video"][:-4] + ".character.png")
    if job.get("character_face"):
        Image.open(job["character_face"]).convert("RGB").save(result["video"][:-4] + ".character-face.png")
    if job.get("keep_sound", True):
        with_sound = result["video"][:-4] + "-sound.mp4"
        if mux_sound(result["video"], job["driving_video"], job.get("start", 0.0), n / prep["fps"], with_sound):
            os.replace(with_sound, result["video"])
    report("done", 1.0, **result)
    return result

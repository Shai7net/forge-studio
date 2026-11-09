"""Text / image / first+last frame to video with Wan 2.2, one stage at a time so an 8 GB card is enough.

Stages, each freeing what it loaded before the next starts:
  1. prompt  -> UMT5 embedding (cached on disk per prompt and encoder file)
  2. frames  -> condition latent (Wan VAE encode of [start, grey..., end] + a mask of the known frames)
  3. noise   -> video latent: flow-matching Euler over shifted sigmas; the Wan 2.2 A14B pair hands over from the
                high-noise expert to the low-noise expert at the boundary (t = 900 for I2V, 875 for T2V)
  4. latent  -> frames (VAE decode, tiled when the frame is large) -> mp4 (ffmpeg, x264 CRF 15)

A job with `parts` (pictures 1 -> 2 -> 3 ...) makes one first+last clip per pair in the same run: each expert is
loaded once and samples every part, and the clips are joined before FILM and enlargement, which then run once.
"""

import gc
import hashlib
import json
import math
import os
import subprocess
import time

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image

from . import attention as attn
from .gguf_load import read_model, strip_prefix
from .ops import BlockStreamer
from .ops import INT8
from .vae import DECODE_BYTES_PER_PIXEL, ENCODE_BYTES_PER_PIXEL, WanVAE, auto_tile
from . import wan as wan_mod
from .wan import WanModel, attach_weights, config_from_shapes

# latent -> rgb for previews (ComfyUI latent_formats.Wan21)
RGB_FACTORS = torch.tensor([
    [-0.1299, -0.1692, 0.2932], [0.0671, 0.0406, 0.0442], [0.3568, 0.2548, 0.1747], [0.0372, 0.2344, 0.1420],
    [0.0313, 0.0189, -0.0328], [0.0296, -0.0956, -0.0665], [-0.3477, -0.4059, -0.2925], [0.0166, 0.1902, 0.1975],
    [-0.0412, 0.0267, -0.1364], [-0.1293, 0.0740, 0.1636], [0.0680, 0.3019, 0.1128], [0.0032, 0.0581, 0.0639],
    [-0.1251, 0.0927, 0.1699], [0.0060, -0.0633, 0.0005], [0.3477, 0.2275, 0.2950], [0.1984, 0.0913, 0.1861]])
RGB_BIAS = torch.tensor([-0.1835, -0.0868, -0.3360])

# Wan's own default negative prompt (used only when CFG > 1).
DEFAULT_NEGATIVE = ("色调艳丽，过曝，静态，细节模糊不清，字幕，风格，作品，画作，画面，静止，整体发灰，最差质量，低质量，JPEG压缩残留，丑陋的，"
                    "残缺的，多余的手指，画得不好的手部，画得不好的脸部，畸形的，毁容的，形态畸形的肢体，手指融合，静止不动的画面，杂乱的背景，"
                    "三条腿，背景人很多，倒着走")


class Cancelled(Exception):
    pass


class Reporter:
    def __init__(self, emit, cancel_file=None):
        self.emit = emit
        self.cancel_file = cancel_file
        self.started = time.time()

    def __call__(self, stage, progress=None, **extra):
        if self.cancel_file and os.path.exists(self.cancel_file):
            raise Cancelled()
        msg = {"stage": stage, "elapsed": round(time.time() - self.started, 1)}
        if progress is not None:
            msg["progress"] = round(float(progress), 4)
        msg.update(extra)
        self.emit(msg)


CAP = {"bytes": None}


def usable_free(device):
    """Free card memory inside this process's cap (the driver's own figure ignores the cap)."""
    if device.type != "cuda":
        return 1 << 40
    free = torch.cuda.mem_get_info(device)[0]
    if CAP["bytes"]:
        free = min(free, CAP["bytes"] - torch.cuda.memory_allocated(device))
    return max(0, free)


def free_memory():
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def sigmas_for(steps, shift):
    t = torch.linspace(1.0, 0.0, steps + 1, dtype=torch.float64)
    s = shift * t / (1 + (shift - 1) * t)
    return s.float()


def fit_size(width, height, target_area, multiple=16):
    """Keep the picture's aspect ratio at about `target_area` pixels, both sides a multiple of 16."""
    ratio = width / height
    h = math.sqrt(target_area / ratio)
    w = h * ratio
    return max(multiple, int(round(w / multiple)) * multiple), max(multiple, int(round(h / multiple)) * multiple)


def load_image(path, width, height):
    """RGB, scaled to cover (width, height) and centre-cropped, as a [3, H, W] tensor in [-1, 1]."""
    im = Image.open(path)
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        im = Image.alpha_composite(bg, im)
    im = im.convert("RGB")
    scale = max(width / im.width, height / im.height)
    nw, nh = max(width, round(im.width * scale)), max(height, round(im.height * scale))
    im = im.resize((nw, nh), Image.LANCZOS)
    left, top = (nw - width) // 2, (nh - height) // 2
    im = im.crop((left, top, left + width, top + height))
    a = torch.from_numpy(np.asarray(im).astype(np.float32) / 127.5 - 1.0)
    return a.permute(2, 0, 1)


def load_reference(path, width, height, background=255):
    """A reference picture (an "ingredient") whole inside (width, height), centred on white, [3, H, W] in [-1, 1]:
    Phantom was trained on subjects padded this way, and cropping would cut a head or a hand off."""
    im = Image.open(path)
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        im = im.convert("RGBA")
        im = Image.alpha_composite(Image.new("RGBA", im.size, (background,) * 3 + (255,)), im)
    im = im.convert("RGB")
    scale = min(width / im.width, height / im.height)
    nw, nh = max(1, round(im.width * scale)), max(1, round(im.height * scale))
    canvas = Image.new("RGB", (width, height), (background,) * 3)
    canvas.paste(im.resize((nw, nh), Image.LANCZOS), ((width - nw) // 2, (height - nh) // 2))
    a = torch.from_numpy(np.asarray(canvas).astype(np.float32) / 127.5 - 1.0)
    return a.permute(2, 0, 1)


def encode_references(job, vae, width, height, device, dtype, report):
    """Phantom's references: each picture its own latent frame, side by side in time -> [1, 16, R, H/8, W/8].
    The model sees them after the video's frames, clean at every step, and takes from them who and what is in the
    video (ComfyUI's WanPhantomSubjectToVideo / time_dim_concat)."""
    latents = []
    for i, path in enumerate(job["references"]):
        report("condition", 0.2 + 0.6 * i / len(job["references"]), note=f"reading ingredient {i + 1}")
        pic = load_reference(path, width, height)[None, :, None].to(device, dtype)
        latents.append(vae.encode(pic).float())
    return torch.cat(latents, dim=2)


def load_video(path, width, height, frames, every=1):
    """The first `frames` frames of a video (every `every`-th: 2 skips FILM's in-between frames), scaled to cover
    (width, height) and centre-cropped, in [-1, 1]."""
    vf = f"scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,crop={width}:{height}"
    if every > 1:
        vf = "select=not(mod(n\,%d)),setpts=N/TB," % every + vf
    cmd = [ffmpeg_exe(), "-loglevel", "error", "-i", path, "-vf", vf, "-frames:v", str(frames),
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    a = np.frombuffer(raw, dtype=np.uint8).reshape(-1, height, width, 3)
    if a.shape[0] < frames:
        a = np.concatenate([a, np.repeat(a[-1:], frames - a.shape[0], axis=0)])
    t = torch.from_numpy(a.copy()).float().div_(127.5).sub_(1.0)
    return t.permute(3, 0, 1, 2).unsqueeze(0)


def preview_png(latent, path, frame=None):
    """Cheap preview of one latent frame through the linear latent->RGB map."""
    z = latent[0, :, frame if frame is not None else latent.shape[2] // 2].float().cpu()
    rgb = torch.einsum("chw,cr->hwr", z, RGB_FACTORS) + RGB_BIAS
    rgb = ((rgb + 1) / 2).clamp(0, 1).mul(255).byte().numpy()
    Image.fromarray(rgb).resize((rgb.shape[1] * 4, rgb.shape[0] * 4), Image.BILINEAR).save(path)


class TextCache:
    def __init__(self, folder):
        self.folder = folder
        os.makedirs(folder, exist_ok=True)

    def path(self, encoder_path, text):
        st = os.stat(encoder_path)
        key = hashlib.sha1(f"{os.path.basename(encoder_path)}|{st.st_size}|{text}".encode("utf-8")).hexdigest()[:24]
        return os.path.join(self.folder, key + ".pt")

    def get(self, encoder_path, text):
        p = self.path(encoder_path, text)
        if os.path.exists(p):
            try:
                return torch.load(p, map_location="cpu")
            except Exception:
                return None
        return None

    def put(self, encoder_path, text, value):
        torch.save(value, self.path(encoder_path, text))


def encode_prompts(job, device, report, prompts=None):
    """[embedding of each prompt] + [the negative's] when CFG is above 1. `prompts` defaults to the job's prompt."""
    cache = TextCache(job["cache_dir"])
    te_path = job["text_encoder"]
    texts = list(prompts) if prompts else [job["prompt"]]
    if job.get("cfg", 1.0) > 1.0:
        texts.append(job.get("negative") or DEFAULT_NEGATIVE)
    out, missing = {}, []
    for t in dict.fromkeys(texts):
        hit = cache.get(te_path, t)
        if hit is not None:
            out[t] = hit
        else:
            missing.append(t)
    if missing:
        report("text", 0.0, note="loading text encoder")
        from .umt5 import UMT5Encoder
        enc = UMT5Encoder(te_path)
        for i, t in enumerate(missing):
            report("text", (i + 0.5) / len(missing), note="encoding prompt")
            emb, length = enc.encode(t, device)
            value = emb[0, :512].to(torch.bfloat16).cpu()
            cache.put(te_path, t, value)
            out[t] = value
        enc.close()
        del enc
        free_memory()
    report("text", 1.0)
    return [out[t] for t in texts]


def build_condition(job, vae, width, height, frames, device, dtype, report, start=None, end=None):
    """Wan I2V condition: VAE latent of [start, grey..., end] (16 ch) and a 4-channel mask of known frames.
    `start` and `end` default to the job's pictures."""
    if start is None and end is None:
        start, end = job.get("start_image"), job.get("end_image")
    lt = (frames - 1) // 4 + 1
    video = torch.zeros(1, 3, frames, height, width)
    mask = torch.zeros(1, 1, lt * 4, height // 8, width // 8)
    if start:
        video[0, :, 0] = load_image(start, width, height)
        mask[:, :, :4] = 1.0
    if end:
        video[0, :, -1] = load_image(end, width, height)
        mask[:, :, -1] = 1.0
    report("condition", 0.2, note="encoding frames")
    tile = auto_tile(width, height, ENCODE_BYTES_PER_PIXEL, usable_free(device))
    try:
        latent = vae.encode(video.to(device, dtype), tile=tile)
    except torch.cuda.OutOfMemoryError:
        free_memory()
        tile = max(32, (tile or min(height, width) // 8) // 2)
        report("condition", 0.3, note=f"retrying in tiles of {tile * 8}px")
        latent = vae.encode(video.to(device, dtype), tile=tile)
    mask = mask.view(1, lt, 4, height // 8, width // 8).transpose(1, 2)
    return torch.cat([mask.to(device), latent.to(device)], dim=1).float()


def load_expert(path, device, report, label):
    t0 = time.time()

    def prog(n, total):
        report("loading", n / total, note=f"loading {label}")

    weights, shapes = read_model(path, pinned=True, on_progress=prog)
    weights, shapes = strip_prefix(weights, shapes)
    cfg = config_from_shapes(shapes)
    model = WanModel(**cfg)
    unused, missing = attach_weights(model, weights)
    if missing:
        raise RuntimeError(f"{os.path.basename(path)}: missing weights {missing[:5]}")
    for w in (model.patch_embedding.weight, model.patch_bias.weight):
        w.gpu = w.cpu.to(device)
    report("loading", 1.0, note=f"{label} loaded in {time.time() - t0:.0f}s")
    return model, cfg


def plan_resident(model, device, reserve_gb):
    """How many blocks can stay on the card for the whole run, leaving `reserve_gb` for activations."""
    if device.type != "cuda":
        return 0
    free = usable_free(device)
    per_block = sum(w.nbytes for w in _block_weights(model.blocks[0]))
    budget = free - reserve_gb * 1024 ** 3 - 2 * per_block
    return max(0, min(len(model.blocks), int(budget // per_block)))


def _block_weights(block):
    from .ops import stream_weights
    return stream_weights(block)


def activation_reserve_gb(tokens, dim, ffn_slice=8192, ffn_dim=13824):
    """Card memory one block needs besides its weights. Measured 2026-10-24 with in-place modulation, int8 and
    SageAttention: 2.64 GB at 32760 tokens (480p), 3.47 GB at 48384 (576p), 5.43 GB at 75600 (720p), i.e. 7-8
    copies of the hidden state; planned with 8.5 and 0.8 GB to spare."""
    return tokens * dim * 2 * 8.5 / 1024 ** 3 + 0.8


def start(job, emit):
    """Reporter, device and dtype for a job; caps this process's card memory (see below)."""
    report = Reporter(emit, job.get("cancel_file"))
    device = torch.device(job.get("device", "cuda"))
    if device.type == "cuda" and device.index is None:
        device = torch.device("cuda", torch.cuda.current_device())
    dtype = torch.bfloat16
    torch.backends.cuda.matmul.allow_tf32 = True
    torch.backends.cudnn.allow_tf32 = True
    out_dir = job["output_dir"]
    os.makedirs(out_dir, exist_ok=True)
    attn_name = attn.choose(job.get("attention", "auto"), device)
    INT8["on"] = bool(job.get("int8", True)) and device.type == "cuda"
    if device.type == "cuda":
        # On Windows the driver lends system RAM when the card is full ("sysmem fallback") instead of failing,
        # and a step that should take a minute then takes an hour (seen 2026-10-24: 58 s per block). Capping
        # PyTorch at what is really free turns that into an out-of-memory error, which predict() recovers from.
        free, total = torch.cuda.mem_get_info(device)
        margin = float(job.get("memory_margin_gb", 0.35)) * 1024 ** 3
        fraction = max(0.3, min(1.0, (free - margin) / total))
        torch.cuda.set_per_process_memory_fraction(fraction, device)
        CAP["bytes"] = int(fraction * total)
    report("start", 0.0, attention=attn_name, int8=INT8["on"], torch=torch.__version__)
    return report, device, dtype


@torch.inference_mode()
def generate(job, emit):
    if job.get("mode") == "replace":
        from .animate import generate_replace
        return generate_replace(job, emit)
    report, device, dtype = start(job, emit)
    out_dir = job["output_dir"]

    width, height, frames = int(job["width"]), int(job["height"]), int(job["frames"])
    frames = 1 + 4 * max(1, (frames - 1) // 4)
    lt, lh, lw = (frames - 1) // 4 + 1, height // 8, width // 8
    seed = int(job["seed"])
    parts = job.get("parts") or [{"start_image": job.get("start_image"), "end_image": job.get("end_image"),
                                  "prompt": job["prompt"]}]
    count = len(parts)

    def of_parts(j):
        return f" | part {j + 1} of {count}" if count > 1 else ""

    # 1. prompts (one per part; the same text is encoded once)
    contexts = encode_prompts(job, device, report, [part.get("prompt") or job["prompt"] for part in parts])
    negative = contexts[count] if len(contexts) > count else None

    # 2. conditions
    vae = WanVAE.load(job["vae"], device, dtype)
    # The I2V experts always take the 20 condition channels; with no picture they get an all-grey video and an
    # empty mask, as ComfyUI gives them.
    conds, refs = [], None
    if job.get("references"):
        # Ingredients (Phantom, a text-to-video model): no picture channels, the references go along in time
        refs = encode_references(job, vae, width, height, device, dtype, report).cpu()
        conds = [None] * count
    for j, part in enumerate(parts if refs is None else []):
        report("condition", j / count, note=f"encoding the pictures{of_parts(j)}")
        conds.append(build_condition(job, vae, width, height, frames, device, dtype, report,
                                     part.get("start_image"), part.get("end_image")).cpu())
    init_latent = None
    if job.get("init_video"):
        # Refine (a video Hires fix): the take is encoded at this size and only the last steps run on it, so the
        # composition and motion stay and the detail is drawn again at the new resolution.
        report("condition", 0.6, note="encoding the video to refine")
        clip = load_video(job["init_video"], width, height, frames, int(job.get("init_every", 1)))
        tile = auto_tile(width, height, ENCODE_BYTES_PER_PIXEL, usable_free(device))
        init_latent = vae.encode(clip.to(device, dtype), tile=tile).float()
        del clip
    vae.to("cpu")
    free_memory()
    report("condition", 1.0)

    # 3. sampling: each part has its own noise (seed + part)
    steps = int(job["steps"])
    sigmas = sigmas_for(steps, float(job["shift"]))
    if job.get("denoise_start_step"):
        sigmas = sigmas[int(job["denoise_start_step"]):]
    gens, xs = [], []
    for j in range(count):
        gen = torch.Generator("cpu").manual_seed(seed + j)
        x = torch.randn(1, 16, lt, lh, lw, generator=gen, dtype=torch.float32)
        if init_latent is not None:
            x = (1 - float(sigmas[0])) * init_latent.cpu() + float(sigmas[0]) * x
        gens.append(gen)
        xs.append(x)
    del init_latent
    experts = job["experts"]  # [{"path", "until_sigma"}] in order, high noise first
    cfg_scale = float(job.get("cfg", 1.0))
    ctx = [c.to(device, dtype).unsqueeze(0) for c in contexts]
    tokens = (lt + (refs.shape[2] if refs is not None else 0)) * (lh // 2) * (lw // 2)
    preview_path = os.path.join(job.get("work_dir") or out_dir, "preview.png")
    done_blocks = [0]
    passes = 2 if cfg_scale > 1 else 1

    done_steps = [0] * count
    for ei, expert in enumerate(experts):
        boundary = float(expert.get("until_sigma", 0.0))
        todo = [i for i in range(done_steps[0], len(sigmas) - 1) if sigmas[i] >= boundary or ei == len(experts) - 1]
        if not todo:
            continue
        label = expert.get("label", f"expert {ei + 1}")
        model, mcfg = load_expert(expert["path"], device, report, label)
        if job.get("loras"):
            from . import lora
            lora.apply(model, job["loras"], label, device,
                       lambda name, hit: report("loading", 1.0, note=f"LoRA {name}: {hit} layers ({label})"))
        reserve = activation_reserve_gb(tokens, mcfg["dim"], ffn_dim=mcfg["ffn_dim"])
        resident = plan_resident(model, device, reserve) if job.get("resident", True) else 0
        streamer = BlockStreamer(model.blocks, device, resident=resident)
        nblocks = len(model.blocks)
        total = steps * nblocks * passes * count
        report("sampling", done_blocks[0] / total, note=f"{label}: {resident}/{nblocks} blocks resident")

        for j in range(count):
            part_note = of_parts(j)

            def on_block(i, j=j, part_note=part_note):
                done_blocks[0] += 1
                report("sampling", min(done_blocks[0] / total, 0.999), step=done_steps[j] + 1, steps=steps,
                       note=part_note.lstrip(" |"))

            def predict(inp, t, positive):
                nonlocal streamer, resident
                for attempt in range(4):
                    mark = done_blocks[0]
                    try:
                        v = model(inp, t, positive, streamer, on_block).float()
                        if cfg_scale > 1.0:
                            v_neg = model(inp, t, negative_ctx, streamer, on_block).float()
                            v = v_neg + cfg_scale * (v - v_neg)
                        if not torch.isfinite(v).all():
                            # A fast kernel overflowed (it happened with fp16 attention accumulation): never save a
                            # black clip, redo the step with exact attention and 16-bit math.
                            if attempt == 3 or (attn.name() == "sdpa" and not INT8["on"]):
                                raise RuntimeError("The model produced invalid numbers (NaN) even with exact attention.")
                            attn.choose("sdpa", device)
                            INT8["on"] = False
                            done_blocks[0] = mark
                            report("sampling", done_blocks[0] / total,
                                   note="invalid numbers: redoing the step with exact attention")
                            continue
                        return v
                    except torch.cuda.OutOfMemoryError:
                        # Out of the memory cap: keep fewer blocks on the card, work in smaller slices, same step again.
                        if attempt == 3:
                            raise
                        done_blocks[0] = mark
                        streamer.close()
                        free_memory()
                        resident = resident // 3 if attempt == 0 else 0
                        wan_mod.FFN_SLICE = max(2048, wan_mod.FFN_SLICE // 2)
                        INT8["rows"] = max(2048, INT8["rows"] // 2)
                        attn.CHUNK["max_bytes"] = max(32 * 1024 ** 2, attn.CHUNK["max_bytes"] // 2)
                        streamer = BlockStreamer(model.blocks, device, resident=resident)
                        report("sampling", done_blocks[0] / total,
                               note=f"memory full: retrying with {resident} blocks resident")

            negative_ctx = ctx[count] if negative is not None else None
            x, gen = xs[j].to(device), gens[j]
            cond = conds[j].to(device) if conds[j] is not None else None
            ref = refs.to(device) if refs is not None else None
            for i in todo:
                sigma, sigma_next = float(sigmas[i]), float(sigmas[i + 1])
                t = torch.tensor([sigma * 1000.0], device=device)
                if ref is not None:  # the ingredients after the video's frames; the model's answer for them is dropped
                    v = predict(torch.cat([x, ref], dim=2), t, ctx[j])[:, :, :lt]
                else:
                    inp = torch.cat([x, cond], dim=1)
                    v = predict(inp, t, ctx[j])
                    del inp
                if job.get("sampler", "euler") == "lcm" and sigma_next > 0:
                    x0 = x - sigma * v
                    noise = torch.randn(x.shape, generator=gen, dtype=torch.float32).to(device)
                    x = (1 - sigma_next) * x0 + sigma_next * noise
                else:
                    x = x + (sigma_next - sigma) * v
                done_steps[j] = i + 1
                preview_png(x - sigma_next * v if sigma_next > 0 else x, preview_path)
                report("sampling", min(done_blocks[0] / total, 0.999), step=i + 1, steps=steps, preview=preview_path,
                       note=part_note.lstrip(" |"))
                del v
            xs[j] = x.cpu()
            del x, cond, ref
        streamer.close()
        del streamer, model
        free_memory()
    del ctx, conds

    # 4. decode each part; a later part starts on the picture the one before ended on, so that frame is dropped
    report("decode", 0.0)
    vae.to(device)
    tile = auto_tile(width, height, DECODE_BYTES_PER_PIXEL, usable_free(device))
    pieces = []
    for j, x in enumerate(xs):
        def on_frame(i, n, j=j):
            report("decode", (j + i / n) / count, note=of_parts(j).lstrip(" |"))
        try:
            video = vae.decode(x.to(device, dtype), tile=tile, on_frame=on_frame)
        except torch.cuda.OutOfMemoryError:
            free_memory()
            tile = max(32, (tile or min(lh, lw)) // 2)
            report("decode", j / count, note=f"retrying in tiles of {tile * 8}px")
            video = vae.decode(x.to(device, dtype), tile=tile, on_frame=on_frame)
        piece = ((video[0].permute(1, 2, 3, 0).float() + 1) * 127.5).round().clamp(0, 255).byte().numpy()
        del video
        pieces.append(piece if j == 0 else piece[1:])
        xs[j] = None
    del vae
    free_memory()

    frames_u8 = pieces[0] if count == 1 else np.concatenate(pieces)
    del pieces
    frames_u8 = finish(job, frames_u8, device, report)
    result = write_outputs(job, frames_u8, report)
    report("done", 1.0, **result)
    return result


def finish(job, frames, device, report):
    """Optional FILM in-between frames (doubles the frame rate) and Real-ESRGAN enlargement, in that order."""
    from . import post
    if job.get("interpolate") and job.get("film_model") and os.path.isfile(job["film_model"]):
        report("interpolate", 0.0)
        frames = post.film_double(frames, job["film_model"], device, lambda p: report("interpolate", p))
        job["fps"] = int(job.get("fps", 16)) * 2
        free_memory()
    up = job.get("upscale") or {}
    if up.get("model") and os.path.isfile(up["model"]) and float(up.get("scale", 1)) > 1:
        report("upscale", 0.0)
        frames = post.enlarge(frames, up["model"], float(up["scale"]), device, lambda p: report("upscale", p))
        free_memory()
    return frames


def ffmpeg_exe():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


def write_outputs(job, frames, report):
    out_dir = job["output_dir"]
    base = job.get("basename") or time.strftime("%Y%m%d-%H%M%S")
    fps = int(job.get("fps", 16))
    path = os.path.join(out_dir, base + ".mp4")
    h, w = frames.shape[1:3]
    meta = {k: job[k] for k in ("mode", "prompt", "negative", "seed", "steps", "shift", "cfg", "width", "height", "frames", "fps",
                                "model_label", "start_image", "end_image", "init_video", "denoise_start_step", "loras", "references",
                                "driving_video", "character_image", "person", "start", "seconds", "parts") if k in job}
    cmd = [ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(fps),
           "-i", "-", "-c:v", "libx264", "-preset", "slow", "-crf", "15", "-pix_fmt", "yuv420p",
           "-movflags", "+faststart", "-metadata", "comment=" + json.dumps(meta, ensure_ascii=False), path]
    report("writing", 0.5)
    # a few frames at a time: a long joined video is gigabytes, and tobytes() would copy all of it at once
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for i in range(0, frames.shape[0], 16):
            proc.stdin.write(frames[i:i + 16].tobytes())
        proc.stdin.close()
    except (BrokenPipeError, OSError):
        pass
    err = proc.stderr.read()
    if proc.wait() != 0:
        raise RuntimeError("ffmpeg: " + err.decode("utf-8", "replace")[-500:])
    Image.fromarray(frames[0]).save(os.path.join(out_dir, base + ".first.png"))
    Image.fromarray(frames[-1]).save(os.path.join(out_dir, base + ".last.png"))
    with open(os.path.join(out_dir, base + ".json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    return {"video": path, "frames": int(frames.shape[0]), "width": int(w), "height": int(h), "fps": fps}

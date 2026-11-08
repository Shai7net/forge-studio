"""FLUX 3 inside Forge: loading the three parts, sharing the 8 GB card with Forge, and sampling.

Card policy (RTX 3070 Laptop, 8 GB, measured numbers in the README):
- the text encoder (Qwen3-VL-4B, 4.9 GB int8) streams through the card one layer at a time for a prompt, so the
  image model can stay on the card meanwhile; prompts are cached, so a repeated prompt costs nothing;
- the image model (5.8 GB int8) keeps as many blocks on the card as fit next to the activations of the requested
  size, and streams the rest on a side stream while the previous block computes;
- the VAE decodes whole when there is room, otherwise the image model steps off the card first (it comes back in
  under a second from pinned memory), and huge images decode in tiles;
- when Forge needs the card for its own models (an upscaler, ADetailer, a switch to an SD checkpoint) FLUX 3
  steps off the card (see glue.py: model_management.free_memory is wrapped).

Windows' "sysmem fallback" lets CUDA spill into shared memory instead of failing, which makes a step crawl. While
FLUX 3 samples, the process is capped just under the card's size so that a real out-of-memory error is raised and
handled (fewer resident blocks, then the step again).
"""

import gc
import hashlib
import json
import math
import os
import threading
import time
from collections import OrderedDict

import torch
from safetensors import safe_open

from . import ops
from . import model as fmodel
from . import text_encoder as fte
from . import vae as fvae

SHIFT = 6.93
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(os.path.dirname(HERE), "cache")


def log(msg):
    print(f"[FLUX 3] {msg}", flush=True)


def load_safetensors(path, pin=False):
    out = {}
    with safe_open(path, framework="pt", device="cpu") as f:
        for k in f.keys():
            out[k] = f.get_tensor(k)
    return out


def flow_sigmas(steps, shift=SHIFT, denoise=1.0):
    """The reference schedule: linspace(1, 1/N, N) shifted by `shift`, then 0. With denoise < 1, a schedule of
    round(steps / denoise) steps of which the last `steps` run (A1111's img2img convention)."""
    total = steps if denoise >= 0.999 else max(steps, int(round(steps / max(denoise, 1e-3))))
    s = torch.linspace(1.0, 1.0 / total, total, dtype=torch.float64)
    s = shift * s / (1 + (shift - 1) * s)
    s = torch.cat([s, torch.zeros(1, dtype=torch.float64)])
    return s[total - steps:] if denoise < 0.999 else s


def latent_preview(z):
    """Fast RGB preview of normalised packed latents [128, h, w] -> PIL (ComfyUI's Flux2 factors)."""
    from PIL import Image
    d = latent_preview.data
    if d is None:
        with open(os.path.join(HERE, "preview_factors.json")) as f:
            j = json.load(f)
        d = latent_preview.data = (torch.tensor(j["factors"]), torch.tensor(j["bias"]))
    fac, bias = d
    c, h, w = z.shape
    z32 = z.float().cpu().reshape(32, 2, 2, h, w).permute(0, 3, 1, 4, 2).reshape(32, h * 2, w * 2)
    rgb = torch.einsum("chw,cr->hwr", z32, fac) + bias
    rgb = ((rgb + 1) * 127.5).clamp(0, 255).byte().numpy()
    return Image.fromarray(rgb).resize((w * 16, h * 16), Image.BILINEAR)


latent_preview.data = None


class Flux3Engine:
    """One loaded FLUX 3 checkpoint with its text encoder and VAE."""

    def __init__(self, paths):
        self.paths = paths  # dict: model, text_encoder, tokenizer_dir, vae
        self.model = None
        self.te = None
        self.vae = None
        self.device = None
        self.lock = threading.RLock()
        self.emb_cache = OrderedDict()
        self.loaded_at = None
        self.stats = {}

    # ------------------------------------------------------------------ load / unload

    def load(self, device):
        with self.lock:
            if self.model is not None:
                return
            t0 = time.time()
            self.device = device
            sd = load_safetensors(self.paths["model"])
            self.model = fmodel.Flux3Model(sd)
            del sd
            gc.collect()
            self.model.pin()
            t1 = time.time()
            te_sd = load_safetensors(self.paths["text_encoder"])
            cfg = fte.load_config(self.paths["tokenizer_dir"])
            self.te = fte.QwenText(te_sd, self.paths["tokenizer_dir"], cfg)
            del te_sd
            gc.collect()
            self.te.pin()
            t2 = time.time()
            self.vae = fvae.Flux2VAE(load_safetensors(self.paths["vae"]))
            self.loaded_at = time.time()
            log(f"loaded: image model {self.model.total_bytes() / 2**30:.2f} GB in {t1 - t0:.1f} s "
                f"({'flow map, ' if self.model.flow_map else ''}{len(self.model.single_blocks)} joint blocks), "
                f"text encoder {self.te.total_bytes() / 2**30:.2f} GB in {t2 - t1:.1f} s, VAE in {time.time() - t2:.1f} s")

    def unload(self):
        with self.lock:
            self.offload_all()
            torch.cuda.synchronize()
            for part in (self.model, self.te):
                if part is not None:
                    part.release()
            self.model = self.te = self.vae = None
            self.emb_cache.clear()
            gc.collect()
            torch.cuda.empty_cache()
            # pinned (page-locked) blocks stay in PyTorch's host cache after their tensors are gone: hand the
            # ~10 GB back to Windows
            if hasattr(torch._C, "_host_emptyCache"):
                torch._C._host_emptyCache()

    def offload_all(self):
        if self.model is not None:
            self.model.offload()
        if self.te is not None:
            self.te.offload()
        if self.vae is not None:
            self.vae.to("cpu")
        torch.cuda.empty_cache()

    def on_card_bytes(self):
        n = 0
        if self.model is not None:
            n += self.model.on_card_bytes()
        if self.vae is not None and next(self.vae.parameters()).is_cuda:
            n += sum(p.numel() * p.element_size() for p in self.vae.parameters())
        return n

    # ------------------------------------------------------------------ text

    @torch.no_grad()
    def encode(self, prompt):
        """Cached [1, T, 20480] bf16 on the CPU."""
        key = prompt
        hit = self.emb_cache.get(key)
        if hit is not None:
            self.emb_cache.move_to_end(key)
            return hit
        t0 = time.time()
        self.te.to_device(self.device, 0)
        emb = self.te.encode(prompt).to("cpu")
        self.te.offload()
        self.emb_cache[key] = emb
        while len(self.emb_cache) > 24:
            self.emb_cache.popitem(last=False)
        log(f"prompt encoded ({emb.shape[1]} tokens) in {time.time() - t0:.1f} s")
        return emb

    def _caption_cache(self):
        path = os.path.join(CACHE_DIR, "captions.json")
        try:
            with open(path, encoding="utf8") as f:
                return path, json.load(f)
        except Exception:  # noqa: BLE001
            return path, {}

    @torch.no_grad()
    def upsample(self, prompt, width, height, mode="t2i", max_new=700):
        """The prompt writer: Qwen3-VL-4B with BFL's system prompt (from the upsampler's own repo). Greedy, so the
        caption is the same every time for the same request and canvas; it is cached on disk."""
        with open(os.path.join(HERE, "system_prompts.json"), encoding="utf8") as f:
            system = json.load(f)[mode]
        g = math.gcd(int(width), int(height)) or 1
        user = f"TARGET CANVAS: {width // g}:{height // g}\n{prompt}" if mode == "t2i" else prompt
        key = hashlib.sha1(f"{mode}\n{user}".encode("utf8")).hexdigest()
        path, cache = self._caption_cache()
        if key in cache:
            return cache[key]
        t0 = time.time()
        # the writer reads all 36 layers for every word: they must all be on the card
        self.model.offload()
        torch.cuda.empty_cache()
        self.te.to_device(self.device, len(self.te.layers))
        try:
            caption = self.te.generate(system, user, max_new=max_new, presence_penalty=1.5 if mode == "t2i" else 0.0)
        finally:
            self.te.offload()
            torch.cuda.empty_cache()
        log(f"prompt writer: {len(caption.split())} words in {time.time() - t0:.1f} s")
        cache[key] = caption
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(path, "w", encoding="utf8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=1)
        return caption

    # ------------------------------------------------------------------ card

    def _act_bytes(self, tokens):
        # measured on the 3070 at 1024x1024 (4,256 tokens); see README. Generous on purpose.
        return int(tokens * 200_000 + (2 * ops.SLICE_BYTES) + (384 << 20))

    def place_model(self, tokens):
        """Put the image model on the card: all small layers plus as many blocks as fit."""
        m = self.model
        m.to_device(self.device, 0)
        torch.cuda.empty_cache()
        free, total = torch.cuda.mem_get_info(self.device)
        budget = free - self._act_bytes(tokens) - 2 * m.block_bytes()  # two streamed blocks in flight
        n = max(0, min(len(m.order), budget // m.block_bytes()))
        m.to_device(self.device, int(n))
        self.stats["resident_blocks"] = int(n)
        return int(n)

    # ------------------------------------------------------------------ sampling

    @torch.no_grad()
    def sample(self, noise, emb, steps, denoise=1.0, init=None, mask=None, refs=None, callback=None,
               should_stop=None, shift=SHIFT):
        """Flow-map Euler (the reference: x_next = x + (sigma_next - sigma) * v, each step told the time it jumps
        to). noise/init: packed latents [B, 128, h, w]; mask: [1, 1, h, w], 1 = draw anew, 0 = keep `init`."""
        dev = self.device
        sig = flow_sigmas(steps, shift, denoise if init is not None else 1.0)
        noise = noise.to(dev, torch.float32)
        if init is not None:
            init = init.to(dev, torch.float32)
            x = (1 - sig[0]) * init + sig[0] * noise
        else:
            x = noise
        if mask is not None:
            mask = mask.to(dev, torch.float32)
        txt = emb.to(dev, torch.bfloat16)
        refs = [r.to(dev, torch.bfloat16) for r in refs] if refs else None
        tokens = x.shape[-1] * x.shape[-2] + txt.shape[1] + (sum(r.shape[-1] * r.shape[-2] for r in refs) if refs else 0)
        log(f"inputs: noise std {noise.std().item():.3f}, prompt {tuple(txt.shape)} std {txt.float().std().item():.2f}"
            + (f", references {[tuple(r.shape) for r in refs]} std {refs[0].float().std().item():.3f}" if refs else "")
            + (f", start {float(sig[0]):.3f}" if init is not None else ""))
        self.place_model(tokens)
        cap = self._cap_memory()
        t0 = time.time()
        torch.cuda.reset_peak_memory_stats(dev)
        try:
            i = 0
            while i < len(sig) - 1:
                if should_stop is not None and should_stop():
                    break
                s, s_next = float(sig[i]), float(sig[i + 1])
                try:
                    v = torch.cat([self.model.forward(x[b:b + 1], s, s_next, txt, refs) for b in range(x.shape[0])])
                except torch.OutOfMemoryError:
                    if not self._shrink():
                        raise
                    continue
                x0 = x - s * v
                x = x + (s_next - s) * v
                del v
                if mask is not None:
                    known = (1 - s_next) * init + s_next * noise
                    x = known + (x - known) * mask
                    x0 = init + (x0 - init) * mask
                if callback is not None:
                    callback(i, len(sig) - 1, x0)
                i += 1
        finally:
            self._uncap_memory(cap)
        dt = time.time() - t0
        self.stats.update(steps=i, seconds=dt, peak_gb=torch.cuda.max_memory_allocated(dev) / 2**30, tokens=tokens)
        log(f"{i} steps at {tokens} tokens in {dt:.1f} s ({dt / max(i, 1):.2f} s/step, "
            f"{self.stats['resident_blocks']}/{len(self.model.order)} blocks resident, "
            f"peak {self.stats['peak_gb']:.2f} GB)")
        return x

    def _shrink(self):
        m = self.model
        n = len(m.streamer.resident)
        if n == 0:
            return False
        m.to_device(self.device, n * 3 // 4)
        self.stats["resident_blocks"] = len(m.streamer.resident)
        torch.cuda.empty_cache()
        log(f"out of memory: {len(m.streamer.resident)} blocks resident now")
        return True

    def _cap_memory(self):
        try:
            total = torch.cuda.get_device_properties(self.device).total_memory
            frac = max(0.5, (total - (300 << 20)) / total)
            torch.cuda.set_per_process_memory_fraction(frac, self.device)
            return True
        except Exception:  # noqa: BLE001
            return False

    def _uncap_memory(self, capped):
        if capped:
            try:
                torch.cuda.set_per_process_memory_fraction(1.0, self.device)
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------ VAE

    def _vae_room(self, pixels, encode=False):
        return int(pixels * (1100 if not encode else 700)) + (512 << 20)

    @torch.no_grad()
    def decode(self, z):
        """Packed latents -> images [B, 3, H, W] in [-1, 1] on the CPU."""
        h, w = z.shape[-2] * 16, z.shape[-1] * 16
        need = self._vae_room(h * w)
        free = torch.cuda.mem_get_info(self.device)[0]
        if free < need + (400 << 20):
            self.model.offload()
            torch.cuda.empty_cache()
        self.vae.to_device(self.device)
        free = torch.cuda.mem_get_info(self.device)[0]
        out = []
        for b in range(z.shape[0]):
            zb = z[b:b + 1].to(self.device)
            if free > need:
                try:
                    out.append(self.vae.decode(zb).cpu())
                    continue
                except torch.OutOfMemoryError:
                    torch.cuda.empty_cache()
            out.append(self.vae.decode_tiled(zb).cpu())
        return torch.cat(out)

    @torch.no_grad()
    def encode_image(self, image):
        """images [B, 3, H, W] in [-1, 1] (H, W multiples of 16) -> packed latents on the device."""
        h, w = image.shape[-2:]
        if torch.cuda.mem_get_info(self.device)[0] < self._vae_room(h * w, True):
            self.model.offload()
            torch.cuda.empty_cache()
        self.vae.to_device(self.device)
        out = []
        for b in range(image.shape[0]):
            ib = image[b:b + 1].to(self.device)
            try:
                out.append(self.vae.encode(ib))
            except torch.OutOfMemoryError:
                torch.cuda.empty_cache()
                out.append(self.vae.encode_tiled(ib))
        return torch.cat(out)

    def vae_off(self):
        if self.vae is not None:
            self.vae.to("cpu")

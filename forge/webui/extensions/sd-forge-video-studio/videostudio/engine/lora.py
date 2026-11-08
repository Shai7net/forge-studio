"""Wan LoRAs (kohya, diffusers/peft and ComfyUI key styles), applied beside the quantised weights.

Nothing is merged: each StreamLinear gets (down, up, scale) and adds up(down(x)) * scale to its output, so a LoRA
works on Q3 GGUF weights exactly as on 16-bit ones and costs only its own small matmuls.
"""

import os
import re

import torch

from .ops import StreamLinear
from .wan import _rename

PREFIXES = ("diffusion_model.", "model.diffusion_model.", "transformer.", "base_model.model.", "model.")


def _modules(model):
    found = {}
    for name, m in model.named_modules():
        if isinstance(m, StreamLinear):
            found[name] = m
            found[name.replace(".", "_")] = m
    return found


def _norm_base(base):
    for p in PREFIXES:
        if base.startswith(p):
            base = base[len(p):]
            break
    if base.startswith("lora_unet_"):
        return base[len("lora_unet_"):], True
    return _rename(base + ".x")[:-2], False


def _groups(sd):
    groups = {}
    patterns = [
        (r"^(.*)\.lora_down\.weight$", "down"), (r"^(.*)\.lora_up\.weight$", "up"),
        (r"^(.*)\.lora_A\.weight$", "down"), (r"^(.*)\.lora_B\.weight$", "up"),
        (r"^(.*)\.lora\.down\.weight$", "down"), (r"^(.*)\.lora\.up\.weight$", "up"),
        (r"^(.*)\.alpha$", "alpha"), (r"^(.*)\.diff$", "diff"), (r"^(.*)\.diff_b$", "diff_b"),
    ]
    for key, value in sd.items():
        for pat, kind in patterns:
            m = re.match(pat, key)
            if m:
                groups.setdefault(m.group(1), {})[kind] = value
                break
    return groups


def applies_to(path, expert_label, choice="auto"):
    """'auto' reads the file name: '...high...' goes to the high-noise expert, '...low...' to the low-noise one."""
    if choice in ("high", "low"):
        return choice in expert_label
    name = os.path.basename(path).lower()
    is_high = re.search(r"(^|[^a-z])(high|hn)([^a-z]|$)", name) is not None
    is_low = re.search(r"(^|[^a-z])(low|ln)([^a-z]|$)", name) is not None
    if is_high and not is_low:
        return "high" in expert_label
    if is_low and not is_high:
        return "low" in expert_label
    return True


# A LoRA file bigger than this stays in pinned system memory and is copied to the card layer by layer (the
# lightx2v distill LoRA alone is 0.74 GB, which an 8 GB card cannot keep beside the blocks).
ON_CARD_BYTES = 200 * 1024 ** 2


def _add_diffs(weights, base, parts, strength):
    """`.diff` / `.diff_b` entries (full differences for norms and biases, as in the lightx2v LoRAs) are added to
    the model's own unquantised weights. Returns how many were applied."""
    if weights is None:
        return 0
    hit = 0
    for kind, suffix in (("diff", ".weight"), ("diff_b", ".bias")):
        w = weights.get(base + suffix) if kind in parts else None
        if w is not None and w.qtype is None and tuple(w.shape) == tuple(parts[kind].shape):
            w.cpu.add_(parts[kind].to(w.cpu.dtype) * strength)
            if w.gpu is not None:
                w.gpu = w.cpu.to(w.gpu.device)
            hit += 1
    return hit


def apply(model, loras, expert_label, device, report=None, weights=None):
    """loras: [{"path", "strength", "expert": auto/high/low/both}]. Returns how many layers each LoRA reached.
    `weights` (the model's name -> Weight) lets `.diff`/`.diff_b` entries reach norms and biases."""
    from safetensors.torch import load_file
    mods = _modules(model)
    counts = []
    for item in loras:
        if not applies_to(item["path"], expert_label, item.get("expert", "auto")):
            continue
        strength = float(item.get("strength", 1.0))
        sd = load_file(item["path"])
        on_card = os.path.getsize(item["path"]) <= ON_CARD_BYTES or device.type != "cuda"
        place = (lambda t: t.to(device, torch.bfloat16)) if on_card else (lambda t: t.to(torch.bfloat16).pin_memory())
        hit = 0
        for base, parts in _groups(sd).items():
            stripped = base
            for p in PREFIXES:
                if stripped.startswith(p):
                    stripped = stripped[len(p):]
                    break
            hit += _add_diffs(weights, stripped, parts, strength)
            if "down" not in parts or "up" not in parts:
                continue
            name, kohya = _norm_base(base)
            target = mods.get(name) or (mods.get(name.replace(".", "_")) if not kohya else None)
            if target is None:
                continue
            down, up = parts["down"], parts["up"]
            rank = down.shape[0]
            alpha = float(parts["alpha"]) if "alpha" in parts else rank
            scale = strength * alpha / rank
            target.loras.append((place(down), place(up), scale))
            hit += 1
        counts.append((os.path.basename(item["path"]), hit))
        if report:
            report(os.path.basename(item["path"]), hit)
    return counts

"""Read a diffusion model from .gguf or .safetensors into `Weight`s held in pinned system memory."""

import numpy as np
import torch

from .ops import GGML_TYPES, Weight, pin


def _orig_shapes(reader):
    shapes = {}
    for key, field in reader.fields.items():
        if key.startswith("comfy.gguf.orig_shape."):
            shapes[key[len("comfy.gguf.orig_shape."):]] = tuple(int(field.parts[i][0]) for i in field.data)
    return shapes


def read_gguf(path, pinned=True, on_progress=None):
    import gguf
    reader = gguf.GGUFReader(path)
    orig = _orig_shapes(reader)
    weights, shapes = {}, {}
    total = len(reader.tensors)
    for n, t in enumerate(reader.tensors):
        tid = int(t.tensor_type)
        if tid not in GGML_TYPES:
            raise ValueError(f"{t.name}: GGML type {t.tensor_type.name} is not supported")
        qname = GGML_TYPES[tid][0]
        shape = orig.get(t.name) or tuple(int(x) for x in reversed(list(t.shape)))
        raw = torch.from_numpy(np.asarray(t.data))
        if qname in ("F32", "F16", "BF16"):
            dtype = {"F32": torch.float32, "F16": torch.float16, "BF16": torch.bfloat16}[qname]
            tensor = raw.view(torch.uint8).view(dtype).reshape(shape) if qname == "BF16" else raw.view(dtype).reshape(shape)
            w = Weight(pin(tensor) if pinned and tensor.numel() > 4096 else tensor.clone())
        else:
            data = raw.view(torch.uint8).reshape(-1)
            w = Weight(pin(data) if pinned else data.clone(), qtype=qname, shape=shape)
        weights[t.name] = w
        shapes[t.name] = torch.Size(shape)
        if on_progress and (n % 50 == 0 or n == total - 1):
            on_progress(n + 1, total)
    del reader
    return weights, shapes


def read_safetensors(path, pinned=True, on_progress=None):
    from safetensors import safe_open
    weights, shapes = {}, {}
    with safe_open(path, framework="pt", device="cpu") as f:
        keys = [k for k in f.keys() if not k.endswith(".scale_weight") and k != "scaled_fp8"]
        all_keys = set(f.keys())
        for n, key in enumerate(keys):
            t = f.get_tensor(key)
            scale_key = key[:-len("weight")] + "scale_weight" if key.endswith(".weight") else None
            scale = f.get_tensor(scale_key).float().item() if scale_key in all_keys else None
            weights[key] = Weight(pin(t) if pinned and t.numel() > 4096 else t, scale=scale)
            shapes[key] = t.shape
            if on_progress and (n % 50 == 0 or n == len(keys) - 1):
                on_progress(n + 1, len(keys))
    return weights, shapes


def read_model(path, pinned=True, on_progress=None):
    if path.lower().endswith(".gguf"):
        return read_gguf(path, pinned, on_progress)
    return read_safetensors(path, pinned, on_progress)


def strip_prefix(weights, shapes):
    prefixes = ("model.diffusion_model.", "diffusion_model.", "model.")
    out_w, out_s = {}, {}
    for k, v in weights.items():
        nk = k
        for p in prefixes:
            if nk.startswith(p):
                nk = nk[len(p):]
                break
        out_w[nk] = v
        out_s[nk] = shapes[k]
    return out_w, out_s

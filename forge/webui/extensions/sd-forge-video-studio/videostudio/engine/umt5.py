"""UMT5-XXL text encoder (Wan's), run one block at a time from an fp8 or 16-bit safetensors file.

The fp8 "scaled" files (ComfyUI's umt5_xxl_fp8_e4m3fn_scaled) keep one fp32 `scale_weight` per matrix. The
embedding table (256k x 4096) never goes to the card: the prompt's rows are looked up in system memory. Layout
follows ComfyUI's comfy/text_encoders/t5.py (UMT5: every layer has its own relative position bias).
"""

import json
import lzma
import math
import os

import torch
import torch.nn.functional as F
from safetensors import safe_open

from .ops import Weight

HERE = os.path.dirname(os.path.abspath(__file__))
TOKENIZER = os.path.join(HERE, "umt5.tokenizer.json.xz")


def load_tokenizer():
    from tokenizers import Tokenizer
    data = json.loads(lzma.open(TOKENIZER).read())

    # tokenizers < 0.14 reads Metaspace with add_prefix_space instead of prepend_scheme/split.
    def fix(o):
        if isinstance(o, dict):
            if o.get("type") == "Metaspace" and "prepend_scheme" in o:
                scheme = o.pop("prepend_scheme")
                o.pop("split", None)
                o["add_prefix_space"] = scheme != "never"
            for v in o.values():
                fix(v)
        elif isinstance(o, list):
            for v in o:
                fix(v)

    try:
        return Tokenizer.from_str(json.dumps(data))
    except Exception:
        fix(data)
        return Tokenizer.from_str(json.dumps(data))


def _bucket(relative_position, num_buckets=32, max_distance=128):
    num_buckets //= 2
    buckets = (relative_position > 0).to(torch.long) * num_buckets
    rp = torch.abs(relative_position)
    max_exact = num_buckets // 2
    is_small = rp < max_exact
    large = max_exact + (torch.log(rp.float().clamp(min=1) / max_exact) / math.log(max_distance / max_exact) * (num_buckets - max_exact)).to(torch.long)
    large = torch.clamp(large, max=num_buckets - 1)
    return buckets + torch.where(is_small, rp, large)


class UMT5Encoder:
    def __init__(self, path):
        self.path = path
        self.weights = {}
        with safe_open(path, framework="pt", device="cpu") as f:
            keys = list(f.keys())
            for key in keys:
                if key.endswith(".scale_weight") or key in ("scaled_fp8", "spiece_model"):
                    continue
                t = f.get_tensor(key)
                scale_key = key[:-len("weight")] + "scale_weight" if key.endswith(".weight") else None
                scale = f.get_tensor(scale_key).float().item() if scale_key and scale_key in keys else None
                name = key.replace("encoder.", "", 1) if key.startswith("encoder.") else key
                if name == "shared.weight":
                    self.weights[name] = Weight(t, scale=scale)
                else:
                    self.weights[name] = Weight(t, scale=scale)
        self.num_layers = 1 + max(int(k.split(".")[1]) for k in self.weights if k.startswith("block."))
        self.heads = self.weights["block.0.layer.0.SelfAttention.relative_attention_bias.weight"].shape[1]
        self.tokenizer = load_tokenizer()

    def tokens(self, text, min_length=512):
        ids = self.tokenizer.encode(text).ids
        if not ids or ids[-1] != 1:
            ids = ids + [1]
        length = len(ids)
        if length < min_length:
            ids = ids + [0] * (min_length - length)
        return ids, length

    def _w(self, name, device, dtype):
        return self.weights[name].materialize(device, dtype)

    @torch.inference_mode()
    def encode(self, text, device, dtype=torch.bfloat16, min_length=512):
        ids, length = self.tokens(text, min_length)
        ids_t = torch.tensor(ids, dtype=torch.long)
        table = self.weights["shared.weight"]
        rows = table.cpu[ids_t]
        x = Weight(rows, scale=table.scale).materialize(device, torch.float32).to(dtype).unsqueeze(0)
        x = torch.nan_to_num(x)
        n = len(ids)
        mask = torch.zeros(1, 1, n, n, device=device, dtype=dtype)
        mask[..., length:] = float("-inf")
        pos = torch.arange(n, device=device)
        buckets = _bucket(pos[None, :] - pos[:, None])
        h = self.heads
        for i in range(self.num_layers):
            p = f"block.{i}.layer."
            bias = self._w(p + "0.SelfAttention.relative_attention_bias.weight", device, dtype)[buckets].permute(2, 0, 1).unsqueeze(0)
            hx = self._ln(x, p + "0.layer_norm.weight", device, dtype)
            q = F.linear(hx, self._w(p + "0.SelfAttention.q.weight", device, dtype))
            k = F.linear(hx, self._w(p + "0.SelfAttention.k.weight", device, dtype))
            v = F.linear(hx, self._w(p + "0.SelfAttention.v.weight", device, dtype))
            d = q.shape[-1] // h
            q, k, v = (t.view(1, n, h, d).transpose(1, 2) for t in (q, k, v))
            # T5 attention has no 1/sqrt(d) scaling.
            out = F.scaled_dot_product_attention(q, k, v, attn_mask=mask + bias, scale=1.0)
            out = out.transpose(1, 2).reshape(1, n, h * d)
            x = x + F.linear(out, self._w(p + "0.SelfAttention.o.weight", device, dtype))
            hx = self._ln(x, p + "1.layer_norm.weight", device, dtype)
            g = F.gelu(F.linear(hx, self._w(p + "1.DenseReluDense.wi_0.weight", device, dtype)), approximate="tanh")
            g = g * F.linear(hx, self._w(p + "1.DenseReluDense.wi_1.weight", device, dtype))
            x = x + F.linear(g, self._w(p + "1.DenseReluDense.wo.weight", device, dtype))
        x = self._ln(x, "final_layer_norm.weight", device, dtype)
        x[:, length:] = 0
        return x, length

    def _ln(self, x, name, device, dtype):
        w = self._w(name, device, dtype)
        xf = x.float()
        return (xf * torch.rsqrt(xf.pow(2).mean(-1, keepdim=True) + 1e-6)).to(dtype) * w

    def close(self):
        self.weights.clear()

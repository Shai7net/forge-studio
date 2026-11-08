"""Attention for [B, L, heads, head_dim] tensors, with the fastest kernel this machine has.

Order of preference: SageAttention (8-bit QK, about twice FlashAttention-2 on RTX 30/40 cards, needs Triton),
PyTorch's scaled_dot_product_attention (FlashAttention-2 / memory-efficient kernels, never builds the L x L
matrix), then xformers. `choose()` probes each once on a tiny input, so a kernel that is installed but broken on
this card is skipped instead of failing mid-render.
"""

import torch
import torch.nn.functional as F

_impl = None
_name = None


def _sdpa(q, k, v):
    out = F.scaled_dot_product_attention(q.transpose(1, 2), k.transpose(1, 2), v.transpose(1, 2))
    return out.transpose(1, 2)


def _xformers(q, k, v):
    import xformers.ops as xops
    return xops.memory_efficient_attention(q.contiguous(), k.contiguous(), v.contiguous())


def _sage(q, k, v):
    """SageAttention 2, P.V accumulated in fp16 and carried in fp32 ("fp16+fp32"): 514 ms against 539 ms for the
    default on a 480p self-attention (32760 tokens x 40 heads, RTX 3070 Laptop, 2026-10-24).
    Pure fp16 accumulation (406 ms) overflows at full clip length: an 81-frame 480p clip came out all black
    (NaN) while 17 frames (7800 tokens) were fine."""
    import sageattention as sa
    if hasattr(sa, "sageattn_qk_int8_pv_fp16_cuda"):
        return sa.sageattn_qk_int8_pv_fp16_cuda(q, k, v, tensor_layout="NHD", is_causal=False, pv_accum_dtype="fp16+fp32")
    return sa.sageattn(q, k, v, tensor_layout="NHD", is_causal=False)


def choose(preferred="auto", device=None):
    global _impl, _name
    device = device or torch.device("cuda")
    candidates = [("sage", _sage), ("sdpa", _sdpa), ("xformers", _xformers)]
    if preferred != "auto":
        candidates.sort(key=lambda c: c[0] != preferred)
    for name, fn in candidates:
        try:
            q = torch.randn(1, 64, 2, 128, device=device, dtype=torch.bfloat16)
            out = fn(q, q, q)
            ref = _sdpa(q.float(), q.float(), q.float())
            if not torch.isfinite(out).all() or (out.float() - ref).abs().max() > 0.1:
                continue
            _impl, _name = fn, name
            return name
        except Exception:
            continue
    _impl, _name = _sdpa, "sdpa"
    return _name


# Heads are attended in groups so the kernel's own copies (sage casts V to fp16 and quantises Q and K) stay small:
# at 720p one full-size copy is 740 MB and the whole-tensor call ran out of memory (2026-10-24). The pipeline
# halves `max_bytes` when a step runs out of memory.
CHUNK = {"max_bytes": 192 * 1024 ** 2}


def attention(q, k, v):
    if _impl is None:
        choose(device=q.device)
    if _name == "sage" and k.shape[1] < 128:
        # Cross-attention over 512 text tokens is tiny; sage's per-block quantisation gains nothing there.
        return _sdpa(q, k, v)
    heads = q.shape[2]
    per_head = q.shape[0] * q.shape[1] * q.shape[3] * q.element_size()
    group = max(1, min(heads, CHUNK["max_bytes"] // max(1, per_head)))
    if group >= heads:
        return _impl(q, k, v)
    out = torch.empty_like(q)
    for h in range(0, heads, group):
        sl = slice(h, h + group)
        out[:, :, sl] = _impl(q[:, :, sl].contiguous(), k[:, :, sl].contiguous(), v[:, :, sl].contiguous())
    return out


def name():
    return _name

"""The Wan 2.1 / 2.2 diffusion transformer (14B text/image-to-video, 5B TI2V), written for streamed weights.

Follows ComfyUI's comfy/ldm/wan/model.py (itself after Wan-Video/Wan2.1 wan/modules/model.py, Apache-2.0) and the
Forge Neo port. What differs is memory: weights are StreamLinear/StreamParam, the 40 blocks are streamed by a
BlockStreamer, the feed-forward runs over the sequence in slices (its 13824-wide hidden state is the largest
activation of the model: 0.9 GB for an 81-frame 480p clip) and the residual stream is updated in place.
"""

import math
import re

import torch
import torch.nn as nn
import torch.nn.functional as F

from .attention import attention
from .ops import StreamLinear, StreamParam

FFN_SLICE = 8192


def sinusoidal_embedding_1d(dim, position):
    half = dim // 2
    position = position.type(torch.float64)
    sinusoid = torch.outer(position, torch.pow(10000, -torch.arange(half).to(position).div(half)))
    return torch.cat([torch.cos(sinusoid), torch.sin(sinusoid)], dim=1)


def rope_freqs(t_len, h_len, w_len, head_dim, device, theta=10000.0):
    """Wan's 3D rotary embedding as [L, head_dim/2, 2, 2] rotation matrices (time, height, width axes)."""
    axes = [head_dim - 4 * (head_dim // 6), 2 * (head_dim // 6), 2 * (head_dim // 6)]
    grids = torch.meshgrid(
        torch.arange(t_len, device=device, dtype=torch.float32),
        torch.arange(h_len, device=device, dtype=torch.float32),
        torch.arange(w_len, device=device, dtype=torch.float32),
        indexing="ij",
    )
    parts = []
    for pos, dim in zip(grids, axes):
        scale = torch.linspace(0, (dim - 2) / dim, steps=dim // 2, dtype=torch.float64, device=device)
        omega = (1.0 / (theta ** scale)).float()
        out = pos.reshape(-1, 1) * omega.reshape(1, -1)
        parts.append(torch.stack([torch.cos(out), -torch.sin(out), torch.sin(out), torch.cos(out)], dim=-1).reshape(-1, dim // 2, 2, 2))
    return torch.cat(parts, dim=1)


def apply_rope(x, freqs):
    """x: [B, L, H, D]; freqs: [L, D/2, 2, 2]. Computed in fp32 one head group at a time to keep the copy small."""
    b, l, h, d = x.shape
    f = freqs.reshape(1, l, 1, d // 2, 2, 2)
    out = torch.empty_like(x)
    step = max(1, h // 4)
    for s in range(0, h, step):
        x_ = x[:, :, s:s + step].float().reshape(b, l, -1, d // 2, 1, 2)
        y = f[..., 0] * x_[..., 0] + f[..., 1] * x_[..., 1]
        out[:, :, s:s + step] = y.reshape(b, l, -1, d).to(x.dtype)
    return out


class RMSNorm(nn.Module):
    def __init__(self, dim, eps=1e-6):
        super().__init__()
        self.eps = eps
        self.param = StreamParam()

    def forward(self, x):
        w = self.param.get(x.device, x.dtype)
        return F.rms_norm(x, (x.shape[-1],), w, self.eps) if hasattr(F, "rms_norm") else _rms(x, w, self.eps)


def _rms(x, w, eps):
    xf = x.float()
    return (xf * torch.rsqrt(xf.pow(2).mean(-1, keepdim=True) + eps)).to(x.dtype) * w


class SelfAttention(nn.Module):
    def __init__(self, dim, num_heads, eps=1e-6):
        super().__init__()
        self.num_heads = num_heads
        self.head_dim = dim // num_heads
        self.q = StreamLinear(dim, dim)
        self.k = StreamLinear(dim, dim)
        self.v = StreamLinear(dim, dim)
        self.o = StreamLinear(dim, dim)
        self.norm_q = RMSNorm(dim, eps)
        self.norm_k = RMSNorm(dim, eps)

    def forward(self, x, freqs):
        b, s = x.shape[:2]
        n, d = self.num_heads, self.head_dim
        q = apply_rope(self.norm_q(self.q(x)).view(b, s, n, d), freqs)
        k = apply_rope(self.norm_k(self.k(x)).view(b, s, n, d), freqs)
        v = self.v(x).view(b, s, n, d)
        out = attention(q, k, v)
        del q, k, v
        return self.o(out.reshape(b, s, n * d))


class CrossAttention(nn.Module):
    """Text cross-attention (the Wan 2.2 checkpoints have no CLIP image branch)."""

    def __init__(self, dim, num_heads, eps=1e-6, image_branch=False):
        super().__init__()
        self.num_heads = num_heads
        self.head_dim = dim // num_heads
        self.q = StreamLinear(dim, dim)
        self.k = StreamLinear(dim, dim)
        self.v = StreamLinear(dim, dim)
        self.o = StreamLinear(dim, dim)
        self.norm_q = RMSNorm(dim, eps)
        self.norm_k = RMSNorm(dim, eps)
        self.image_branch = image_branch
        if image_branch:
            self.k_img = StreamLinear(dim, dim)
            self.v_img = StreamLinear(dim, dim)
            self.norm_k_img = RMSNorm(dim, eps)

    def forward(self, x, context, context_img_len=0):
        b, s = x.shape[:2]
        n, d = self.num_heads, self.head_dim
        q = self.norm_q(self.q(x)).view(b, s, n, d)
        ctx = context[:, context_img_len:]
        k = self.norm_k(self.k(ctx)).view(b, -1, n, d)
        v = self.v(ctx).view(b, -1, n, d)
        out = attention(q, k, v).reshape(b, s, n * d)
        if self.image_branch and context_img_len:
            img = context[:, :context_img_len]
            k_img = self.norm_k_img(self.k_img(img)).view(b, -1, n, d)
            v_img = self.v_img(img).view(b, -1, n, d)
            out = out + attention(q, k_img, v_img).reshape(b, s, n * d)
        return self.o(out)


class LayerNorm(nn.Module):
    """LayerNorm with an optional streamed affine."""

    def __init__(self, dim, eps=1e-6, affine=False):
        super().__init__()
        self.dim = dim
        self.eps = eps
        self.weight = StreamParam() if affine else None
        self.bias = StreamParam() if affine else None

    def forward(self, x):
        w = self.weight.get(x.device, x.dtype) if self.weight is not None else None
        b = self.bias.get(x.device, x.dtype) if self.bias is not None else None
        return F.layer_norm(x, (self.dim,), w, b, self.eps)


def _repeat_e(e, x):
    if e.shape[1] > 1 and e.shape[1] != x.shape[1]:
        return torch.repeat_interleave(e, x.shape[1] // e.shape[1], dim=1)
    return e


class AttentionBlock(nn.Module):
    def __init__(self, dim, ffn_dim, num_heads, cross_attn_norm=True, eps=1e-6, image_branch=False):
        super().__init__()
        self.dim = dim
        self.norm1 = LayerNorm(dim, eps)
        self.self_attn = SelfAttention(dim, num_heads, eps)
        self.norm3 = LayerNorm(dim, eps, affine=True) if cross_attn_norm else None
        self.cross_attn = CrossAttention(dim, num_heads, eps, image_branch)
        self.norm2 = LayerNorm(dim, eps)
        self.ffn_0 = StreamLinear(dim, ffn_dim)
        self.ffn_2 = StreamLinear(ffn_dim, dim)
        self.modulation = StreamParam()

    def forward(self, x, e, freqs, context, context_img_len=0):
        mod = self.modulation.get(x.device, torch.float32)
        if e.ndim < 4:
            e = (mod + e).chunk(6, dim=1)
        else:
            e = (mod.unsqueeze(0) + e).unbind(2)

        # The modulation and the gated residual are computed in fp32, as Wan does, but in place over slices of the
        # sequence: whole-sequence fp32 copies were 670 MB each at 480p and 1.5 GB at 720p, and three of them at
        # once ran an 8 GB card out of memory even with no blocks kept on it (2026-10-24).
        def piece(t, s, ref):
            return t if t.shape[1] == 1 else _repeat_e(t, ref)[:, s:s + FFN_SLICE]

        def modulate_(h, shift, scale):
            for s in range(0, h.shape[1], FFN_SLICE):
                hs = h[:, s:s + FFN_SLICE]
                hs.copy_(hs.float() * (1 + piece(scale, s, h)) + piece(shift, s, h))
            return h

        def gate_add_(y, gate):
            for s in range(0, x.shape[1], FFN_SLICE):
                xs = x[:, s:s + FFN_SLICE]
                xs.copy_(xs.float() + y[:, s:s + FFN_SLICE].float() * piece(gate, s, x))

        y = self.self_attn(modulate_(self.norm1(x), e[0], e[1]), freqs)
        gate_add_(y, e[2])
        del y
        h = self.norm3(x) if self.norm3 is not None else x
        x.add_(self.cross_attn(h, context, context_img_len))
        del h

        # Feed-forward over slices of the sequence; the residual is updated slice by slice, in place.
        for s in range(0, x.shape[1], FFN_SLICE):
            xs = x[:, s:s + FFN_SLICE]
            hs = modulate_(self.norm2(xs), piece(e[3], s, x), piece(e[4], s, x))
            hs = self.ffn_2(F.gelu(self.ffn_0(hs), approximate="tanh"))
            xs.copy_(xs.float() + hs.float() * piece(e[5], s, x))
        return x


class Head(nn.Module):
    def __init__(self, dim, out_dim, patch_size, eps=1e-6):
        super().__init__()
        self.dim = dim
        self.norm = LayerNorm(dim, eps)
        self.head = StreamLinear(dim, math.prod(patch_size) * out_dim)
        self.modulation = StreamParam()

    def forward(self, x, e):
        mod = self.modulation.get(x.device, torch.float32)
        if e.ndim < 3:
            e = (mod + e.unsqueeze(1)).chunk(2, dim=1)
        else:
            e = (mod.unsqueeze(0) + e.unsqueeze(2)).unbind(2)
        return self.head((self.norm(x).float() * (1 + _repeat_e(e[1], x)) + _repeat_e(e[0], x)).to(x.dtype))


class WanModel(nn.Module):
    def __init__(self, in_dim=16, dim=5120, ffn_dim=13824, freq_dim=256, text_dim=4096, out_dim=16,
                 num_heads=40, num_layers=40, patch_size=(1, 2, 2), cross_attn_norm=True, eps=1e-6,
                 image_branch=False):
        super().__init__()
        self.in_dim, self.dim, self.out_dim = in_dim, dim, out_dim
        self.freq_dim, self.num_heads = freq_dim, num_heads
        self.patch_size = patch_size
        self.patch_embedding = StreamParam()
        self.patch_bias = StreamParam()
        self.text_embedding_0 = StreamLinear(text_dim, dim)
        self.text_embedding_2 = StreamLinear(dim, dim)
        self.time_embedding_0 = StreamLinear(freq_dim, dim)
        self.time_embedding_2 = StreamLinear(dim, dim)
        self.time_projection_1 = StreamLinear(dim, dim * 6)
        self.blocks = nn.ModuleList([
            AttentionBlock(dim, ffn_dim, num_heads, cross_attn_norm, eps, image_branch) for _ in range(num_layers)
        ])
        self.head = Head(dim, out_dim, patch_size, eps)

    def embed_text(self, context):
        return self.text_embedding_2(F.gelu(self.text_embedding_0(context), approximate="tanh"))

    def forward(self, x, t, context, streamer, on_block=None):
        """x: [B, C, T, H, W] already holding any concatenated condition channels; t: [B] or [B, tokens]."""
        dtype = context.dtype
        b, c, tt, hh, ww = x.shape
        pt, ph, pw = self.patch_size
        w = self.patch_embedding.get(x.device, dtype)
        bias = self.patch_bias.get(x.device, dtype)
        x = F.conv3d(x.to(dtype), w, bias, stride=self.patch_size)
        grid = x.shape[2:]
        x = x.flatten(2).transpose(1, 2).contiguous()
        freqs = rope_freqs(*grid, self.dim // self.num_heads, x.device)

        e = sinusoidal_embedding_1d(self.freq_dim, t.flatten()).to(x.device, torch.float32)
        e = self.time_embedding_2(F.silu(self.time_embedding_0(e.to(dtype)))).float()
        e = e.reshape(t.shape[0], -1, e.shape[-1])
        e0 = self.time_projection_1(F.silu(e).to(dtype)).float().unflatten(2, (6, self.dim))
        context = self.embed_text(context)

        state = {"x": x}

        def run(i, block):
            state["x"] = block(state["x"], e0, freqs, context)
            if on_block is not None:
                on_block(i)

        streamer.run(run)
        x = self.head(state["x"], e)
        return self.unpatchify(x, grid)[:, :, :tt, :hh, :ww]

    def unpatchify(self, x, grid):
        c = self.out_dim
        b = x.shape[0]
        u = x[:, :math.prod(grid)].view(b, *grid, *self.patch_size, c)
        u = torch.einsum("bfhwpqrc->bcfphqwr", u)
        return u.reshape(b, c, *[i * j for i, j in zip(grid, self.patch_size)])


# GGUF / safetensors key -> attribute path in WanModel
def _rename(key):
    key = key.replace("model.diffusion_model.", "")
    key = key.replace("text_embedding.0.", "text_embedding_0.").replace("text_embedding.2.", "text_embedding_2.")
    key = key.replace("time_embedding.0.", "time_embedding_0.").replace("time_embedding.2.", "time_embedding_2.")
    key = key.replace("time_projection.1.", "time_projection_1.")
    key = key.replace(".ffn.0.", ".ffn_0.").replace(".ffn.2.", ".ffn_2.")
    # Wan Animate: the image projection, and the face adapter that follows every fifth block
    key = key.replace("img_emb.proj.", "img_emb.proj_")
    key = re.sub(r"^face_adapter\.fuser_blocks\.(\d+)\.", lambda m: f"blocks.{int(m.group(1)) * 5}.face.", key)
    if key in ("patch_embedding.bias", "pose_patch_embedding.bias"):
        return key.replace("_embedding.bias", "_bias")
    return key


def config_from_shapes(shapes):
    """Model hyper-parameters from tensor shapes (name -> torch.Size)."""
    dim = shapes["patch_embedding.weight"][0]
    in_dim = shapes["patch_embedding.weight"][1]
    num_layers = 1 + max(int(k.split(".")[1]) for k in shapes if k.startswith("blocks."))
    ffn_dim = shapes["blocks.0.ffn.0.weight"][0]
    out_dim = shapes["head.head.weight"][0] // 4
    num_heads = dim // 128
    return dict(in_dim=in_dim, dim=dim, ffn_dim=ffn_dim, out_dim=out_dim, num_heads=num_heads,
                num_layers=num_layers, cross_attn_norm="blocks.0.norm3.weight" in shapes,
                image_branch="blocks.0.cross_attn.k_img.weight" in shapes)


def attach_weights(model, weights):
    """weights: name -> Weight (original names). Fills every StreamLinear/StreamParam; returns names not used."""
    unused = set(weights)
    for name, w in weights.items():
        path = _rename(name)
        parts = path.split(".")
        leaf = parts[-1]
        owner = model
        try:
            for p in parts[:-1]:
                owner = getattr(owner, p) if not p.isdigit() else owner[int(p)]
        except (AttributeError, IndexError):
            continue
        if isinstance(owner, StreamLinear) and leaf in ("weight", "bias"):
            setattr(owner, leaf, w)
        elif isinstance(owner, (RMSNorm,)) and leaf == "weight":
            owner.param.weight = w
        elif isinstance(owner, LayerNorm) and leaf in ("weight", "bias"):
            getattr(owner, leaf).weight = w
        elif leaf == "modulation" and hasattr(owner, "modulation"):
            owner.modulation.weight = w
        elif path in ("patch_embedding.weight", "pose_patch_embedding.weight"):
            getattr(model, path.split(".")[0]).weight = w
        elif path in ("patch_bias", "pose_patch_bias"):
            getattr(model, path).weight = w
        else:
            continue
        unused.discard(name)
    missing = []
    for mname, m in model.named_modules():
        if isinstance(m, StreamLinear) and m.weight is None:
            missing.append(mname)
        if isinstance(m, StreamParam) and m.weight is None:
            missing.append(mname)
    return unused, missing

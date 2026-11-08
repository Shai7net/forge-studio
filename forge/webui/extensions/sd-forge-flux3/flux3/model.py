"""The FLUX 3 Image Klein transformer (5.6B), written for an 8 GB card.

Follows the reference (diffusers `Flux3Transformer2DModel` and ComfyUI `comfy/ldm/flux3/model.py`) with the
weight names of the BFL / ComfyUI files:
- three token modes: the noisy image, the reference images (editing) and the text (stacked Qwen3-VL states);
- stage 1: 5 blocks per mode, each mode attending to itself only; stage 2: 28 joint blocks over all tokens;
- every block runs attention and a SwiGLU MLP in parallel on one modulated LayerNorm and adds both under one gate;
- image tokens are modulated by the step's time, text and references by time 0; a flow map (the 16-step Klein)
  also embeds the time the step jumps to and adds it to every modulation;
- RoPE on four axes (t, row, column, l) of 32 dimensions each, rows and columns at half scale.

Memory: the int8 file is 5.8 GB. Blocks are kept on the card as far as the free memory allows and the rest are
streamed in on a side stream (`ops.BlockStreamer`), so 2-4 megapixels still fit.
"""

import math

import torch
import torch.nn.functional as F

from . import ops

HIDDEN = 3072
HEADS = 24
HEAD_DIM = 128
MLP = 9216
IMAGE_ROPE_SCALE = 0.5
AXES = (32, 32, 32, 32)
THETA = 10000.0

try:
    import xformers.ops as xops
except Exception:  # noqa: BLE001
    xops = None


def attention(q, k, v):
    """q, k, v: [B, L, H, D] -> [B, L, H*D]."""
    if xops is not None and q.is_cuda:
        out = xops.memory_efficient_attention(q, k, v)
    else:
        out = F.scaled_dot_product_attention(q.transpose(1, 2), k.transpose(1, 2), v.transpose(1, 2)).transpose(1, 2)
    return out.reshape(out.shape[0], out.shape[1], -1)


def timestep_embedding(t, dim=256, max_period=10000.0):
    """[cos, sin] of t * 1000 (t is the flow time in [0, 1]), as BFL's Flux and diffusers' Timesteps."""
    t = t.float() * 1000.0
    half = dim // 2
    freqs = torch.exp(-math.log(max_period) * torch.arange(half, dtype=torch.float32, device=t.device) / half)
    args = t[:, None] * freqs[None]
    return torch.cat([torch.cos(args), torch.sin(args)], dim=-1)


def rope(ids):
    """ids [L, 4] -> (cos, sin) [L, 64] for the 64 rotated pairs of a 128-dim head."""
    out = []
    for i, d in enumerate(AXES):
        freqs = 1.0 / (THETA ** (torch.arange(0, d, 2, dtype=torch.float64, device=ids.device) / d))
        out.append(ids[:, i].double()[:, None] * freqs[None])
    ang = torch.cat(out, dim=-1)
    return torch.cos(ang).float(), torch.sin(ang).float()


def apply_rope(x, cos, sin, out_dtype=None):
    """x [B, L, H, D] in fp32; pairs (2i, 2i+1) rotate by the angle of pair i. One complex multiply (the pairs as
    complex numbers) instead of a chain of slices and a stack."""
    xc = torch.view_as_complex(x.unflatten(-1, (-1, 2)))
    rot = torch.view_as_real(xc * torch.complex(cos, sin)[None, :, None, :]).flatten(-2)
    return rot.to(out_dtype) if out_dtype is not None else rot


def image_ids(h, w, l, device):
    ids = torch.zeros(h, w, 4, device=device)
    ids[..., 1] = torch.arange(h, device=device)[:, None] * IMAGE_ROPE_SCALE
    ids[..., 2] = torch.arange(w, device=device)[None, :] * IMAGE_ROPE_SCALE
    ids[..., 3] = l
    return ids.reshape(-1, 4)


def text_ids(n, device):
    ids = torch.zeros(n, 4, device=device)
    ids[:, 3] = torch.arange(n, device=device)
    return ids


class Block:
    """Parallel attention + SwiGLU under one modulated LayerNorm and one gate (per-mode and joint blocks)."""

    def __init__(self, sd, prefix):
        if prefix + "qkv.weight" in sd:
            # ComfyUI's fused layout: one [3*3072, 3072] matrix (per-row scales, so it splits by rows)
            q, k, v = ops.split_rows(ops.take_linear(sd, prefix + "qkv."), [HIDDEN] * 3)
        else:
            q, k, v = (ops.take_linear(sd, f"{prefix}{n}_proj.") for n in ("q", "k", "v"))
        mlp_in = ops.take_linear(sd, prefix + "mlp_in.")
        self.qkvm, self.sizes = ops.fuse([q, k, v, mlp_in])
        if self.qkvm is None:
            self.parts = [q, k, v, mlp_in]
        self.attn_out = ops.take_linear(sd, prefix + "attn_out.")
        self.mlp_out = ops.take_linear(sd, prefix + "mlp_out.")
        self.q_norm = ops.Weight(sd[prefix + "norm.query_norm.scale"].float())
        self.k_norm = ops.Weight(sd[prefix + "norm.key_norm.scale"].float())

    def linears(self):
        lin = [self.qkvm] if self.qkvm is not None else list(self.parts)
        return lin + [self.attn_out, self.mlp_out]

    def weights(self):
        out = [self.q_norm, self.k_norm]
        for l in self.linears():
            out += l.weights()
        return out

    def project(self, x):
        if self.qkvm is not None:
            y = self.qkvm(x)
            return torch.split(y, self.sizes, dim=-1)
        return [l(x) for l in self.parts]

    def __call__(self, x, mods, cos, sin):
        """x [B, N, C]; mods: list of (start, end, shift, scale, gate) row ranges covering x."""
        B, N, C = x.shape
        normed = F.layer_norm(x, (C,), eps=1e-6)
        x_mod = torch.empty_like(x)
        for s, e, shift, scale, _ in mods:
            x_mod[:, s:e] = torch.addcmul(shift, normed[:, s:e], 1 + scale)
        del normed
        q, k, v, mlp = self.project(x_mod)
        del x_mod
        dev = x.device
        q = F.rms_norm(q.float().view(B, N, HEADS, HEAD_DIM), (HEAD_DIM,), self.q_norm.on(dev), eps=1e-6)
        k = F.rms_norm(k.float().view(B, N, HEADS, HEAD_DIM), (HEAD_DIM,), self.k_norm.on(dev), eps=1e-6)
        q = apply_rope(q, cos, sin, v.dtype)
        k = apply_rope(k, cos, sin, v.dtype)
        a = attention(q, k, v.reshape(B, N, HEADS, HEAD_DIM))
        del q, k, v
        out = self.attn_out(a)
        del a
        half = mlp.shape[-1] // 2
        hidden = F.silu(mlp[..., :half]) * mlp[..., half:]
        del mlp
        out += self.mlp_out(hidden)
        del hidden
        for s, e, _, _, gate in mods:
            x[:, s:e].addcmul_(out[:, s:e], gate)
        return x


def _f(sd, key):
    return ops.Weight(sd[key])


class Flux3Model:
    """Weights in system memory; `forward` runs one denoising step."""

    def __init__(self, sd):
        self.flow_map = "ref_time_in.in_layer.weight" in sd
        self.has_image_cond = "emb_in.image_cond.weight" in sd
        lin = ops.take_linear
        self.emb_in = {m: lin(sd, f"emb_in.{m}.") for m in ("image", "image_cond") if f"emb_in.{m}.weight" in sd}
        self.txt_in = lin(sd, "txt_in.")
        self.time_in = (lin(sd, "time_in.in_layer."), lin(sd, "time_in.out_layer."))
        self.ref_time_in = (lin(sd, "ref_time_in.in_layer."), lin(sd, "ref_time_in.out_layer.")) if self.flow_map else None
        modes = [m for m in ("image", "image_cond", "txt") if f"early_stream_modulations.{m}.lin.weight" in sd]
        self.early_mod = {m: lin(sd, f"early_stream_modulations.{m}.lin.") for m in modes}
        self.single_mod = {m: lin(sd, f"single_stream_modulations.{m}.lin.") for m in modes}
        depth = len({k.split(".")[2] for k in sd if k.startswith("content_mode_blocks.image.")})
        self.image_blocks = [Block(sd, f"content_mode_blocks.image.{i}.") for i in range(depth)]
        self.cond_blocks = [Block(sd, f"content_mode_blocks.image_cond.{i}.") for i in range(depth)] if self.has_image_cond else []
        self.txt_blocks = [Block(sd, f"txt_mode_blocks.{i}.") for i in range(depth)]
        n_single = len({k.split(".")[1] for k in sd if k.startswith("single_blocks.")})
        self.single_blocks = [Block(sd, f"single_blocks.{i}.") for i in range(n_single)]
        self.final_ada = lin(sd, "final_layer.image.adaLN_modulation.1.")
        self.final_linear = lin(sd, "final_layer.image.linear.")
        # small layers always live on the card while the model is in use
        small = [self.txt_in, *self.time_in, self.final_ada, self.final_linear, *self.emb_in.values(),
                 *self.early_mod.values(), *self.single_mod.values()]
        if self.ref_time_in:
            small += list(self.ref_time_in)
        self.small = [w for l in small for w in l.weights()]
        # stream order of one step: text blocks, reference blocks, image blocks, joint blocks
        self.order = self.txt_blocks + self.cond_blocks + self.image_blocks + self.single_blocks
        self.streamer = None
        self.device = None

    # ---------------------------------------------------------------- memory

    def all_linears(self):
        out = [self.txt_in, *self.time_in, self.final_ada, self.final_linear, *self.emb_in.values(),
               *self.early_mod.values(), *self.single_mod.values()]
        if self.ref_time_in:
            out += list(self.ref_time_in)
        for b in self.order:
            out += b.linears()
        return out

    def pin(self):
        self.arena = ops.PinnedArena(self.small + [w for b in self.order for w in b.weights()])

    def release(self):
        self.offload()
        if getattr(self, "arena", None) is not None:
            self.arena.release()
            self.arena = None

    def total_bytes(self):
        return sum(w.nbytes for w in self.small) + sum(w.nbytes for b in self.order for w in b.weights())

    def to_device(self, device, resident_blocks):
        self.device = device
        for w in self.small:
            if w.gpu is None:
                w.gpu = w.cpu.to(device)
        if self.streamer is None:
            self.streamer = ops.BlockStreamer([b.weights() for b in self.order], device)
        self.streamer.set_resident(resident_blocks)

    def block_bytes(self):
        return max(sum(w.nbytes for w in b.weights()) for b in self.order)

    def offload(self):
        for w in self.small:
            w.gpu = None
        if self.streamer is not None:
            self.streamer.offload()

    def on_card_bytes(self):
        n = sum(w.nbytes for w in self.small if w.gpu is not None)
        if self.streamer is not None:
            n += sum(self.streamer.block_bytes(i) for i in self.streamer.resident)
        return n

    # ---------------------------------------------------------------- forward

    def _embed(self, mlp, t, dtype):
        a, b = mlp
        return b(F.silu(a(timestep_embedding(t).to(dtype))))

    @staticmethod
    def _mod(lin, act):
        shift, scale, gate = lin(act)[:, None, :].chunk(3, dim=-1)
        return shift, scale, gate

    @torch.no_grad()
    def forward(self, x, sigma, target, txt, refs=None, dtype=torch.bfloat16):
        """One step. x: packed latents [B, 128, h, w]; sigma, target: floats; txt: [B, T, 20480];
        refs: list of packed reference latents [B, 128, hi, wi] or None. Returns the velocity like x."""
        B, C, H, W = x.shape
        dev = x.device
        s = torch.full((B,), float(sigma), device=dev)
        vec = self._embed(self.time_in, s, dtype)
        vec0 = self._embed(self.time_in, torch.zeros(B, device=dev), dtype)
        if self.flow_map:
            vt = self._embed(self.ref_time_in, torch.full((B,), float(target), device=dev), dtype)
            vec = vec + vt
            vec0 = vec0 + vt
        act, act0 = F.silu(vec), F.silu(vec0)

        img = self.emb_in["image"](x.to(dtype).flatten(2).transpose(1, 2))
        txt_h = self.txt_in(txt.to(dtype))
        n_img, n_txt = img.shape[1], txt_h.shape[1]
        img_cos, img_sin = rope(image_ids(H, W, 0, dev))
        txt_cos, txt_sin = rope(text_ids(n_txt, dev))

        cond = None
        if refs:
            cond = self.emb_in["image_cond"](torch.cat([r.to(dtype).flatten(2).transpose(1, 2) for r in refs], dim=1))
            cond_ids = torch.cat([image_ids(r.shape[-2], r.shape[-1], -1 - i, dev) for i, r in enumerate(refs)])
            cond_cos, cond_sin = rope(cond_ids)

        e_img = self._mod(self.early_mod["image"], act)
        e_txt = self._mod(self.early_mod["txt"], act0)
        j_img = self._mod(self.single_mod["image"], act)
        j_txt = self._mod(self.single_mod["txt"], act0)
        if cond is not None:
            e_cond = self._mod(self.early_mod["image_cond"], act0)
            j_cond = self._mod(self.single_mod["image_cond"], act0)

        state = {"img": img, "txt": txt_h, "cond": cond}
        n_txtb, n_condb, n_imgb = len(self.txt_blocks), len(self.cond_blocks), len(self.image_blocks)
        joint = {}

        def run(i):
            block = self.order[i]
            if i < n_txtb:
                state["txt"] = block(state["txt"], [(0, n_txt, *e_txt)], txt_cos, txt_sin)
                return
            i -= n_txtb
            if i < n_condb:
                state["cond"] = block(state["cond"], [(0, state["cond"].shape[1], *e_cond)], cond_cos, cond_sin)
                return
            i -= n_condb
            if i < n_imgb:
                state["img"] = block(state["img"], [(0, n_img, *e_img)], img_cos, img_sin)
                return
            if "x" not in joint:
                parts = [state["img"]]
                cos_parts, sin_parts = [img_cos], [img_sin]
                spans = [(0, n_img, *j_img)]
                start = n_img
                if state["cond"] is not None:
                    n_c = state["cond"].shape[1]
                    parts.append(state["cond"])
                    cos_parts.append(cond_cos)
                    sin_parts.append(cond_sin)
                    spans.append((start, start + n_c, *j_cond))
                    start += n_c
                parts.append(state["txt"])
                cos_parts.append(txt_cos)
                sin_parts.append(txt_sin)
                spans.append((start, start + n_txt, *j_txt))
                joint["x"] = torch.cat(parts, dim=1)
                joint["cos"] = torch.cat(cos_parts)
                joint["sin"] = torch.cat(sin_parts)
                joint["spans"] = spans
                state.clear()
            joint["x"] = block(joint["x"], joint["spans"], joint["cos"], joint["sin"])

        # without references the reference blocks are skipped (and not streamed)
        indices = [i for i in range(len(self.order)) if cond is not None or not (n_txtb <= i < n_txtb + n_condb)]
        self.streamer.run(indices, run)

        out = joint["x"][:, :n_img]
        del joint
        shift, scale = self.final_ada(act)[:, None, :].chunk(2, dim=-1)
        out = torch.addcmul(shift, F.layer_norm(out, (HIDDEN,), eps=1e-6), 1 + scale)
        out = self.final_linear(out)
        return out.transpose(1, 2).reshape(B, C, H, W).float()

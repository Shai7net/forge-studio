"""My FLUX 3 transformer against a transcription of diffusers' Flux3Transformer2DModel.forward (diffusers main,
src/diffusers/models/transformers/transformer_flux3.py, Apache-2.0), with random weights at the real width
(3072, 24 heads) and fewer blocks.

The reference keeps diffusers' weight names and conventions (scale/shift order of norm_out, [cos, sin] timestep
embedding, interleaved RoPE pairs, joint order [text, image, references]); the BFL/ComfyUI state dict for my model
is made from it with the inverse of diffusers' `convert_flux3_transformer_checkpoint_to_diffusers`. Run from the
webui folder:  venv\\Scripts\\python.exe extensions\\sd-forge-flux3\\tests\\test_transformer_reference.py
"""

import json
import math
import os
import sys

import torch
import torch.nn.functional as F

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from flux3 import model as fmodel  # noqa: E402
from flux3 import ops  # noqa: E402

torch.manual_seed(0)
DEV = torch.device("cuda")
D, H, HD, MLP = 3072, 24, 128, 9216
DEPTH, SINGLE = 1, 3


# ----------------------------------------------------------------------------- reference (diffusers, transcribed)

def ref_timesteps(t, dim=256):
    # diffusers Timesteps(num_channels=256, flip_sin_to_cos=True, downscale_freq_shift=0)
    half = dim // 2
    exponent = -math.log(10000) * torch.arange(half, dtype=torch.float32, device=t.device) / half
    emb = t[:, None].float() * torch.exp(exponent)[None]
    emb = torch.cat([torch.sin(emb), torch.cos(emb)], dim=-1)
    return torch.cat([emb[:, half:], emb[:, :half]], dim=-1)


def ref_rope(ids, axes=(32, 32, 32, 32), theta=10000):
    # get_1d_rotary_pos_embed(..., repeat_interleave_real=True, use_real=True) per axis, concatenated
    cos_out, sin_out = [], []
    for i, d in enumerate(axes):
        freqs = 1.0 / (theta ** (torch.arange(0, d, 2, dtype=torch.float64, device=ids.device)[: d // 2] / d))
        f = torch.outer(ids[:, i].double(), freqs)
        cos_out.append(f.cos().repeat_interleave(2, dim=1).float())
        sin_out.append(f.sin().repeat_interleave(2, dim=1).float())
    return torch.cat(cos_out, -1), torch.cat(sin_out, -1)


def ref_apply_rotary(x, cos, sin):
    # apply_rotary_emb(use_real=True, use_real_unbind_dim=-1), sequence_dim=1: x [B, S, H, D]
    cos, sin = cos[None, :, None, :], sin[None, :, None, :]
    x_real, x_imag = x.reshape(*x.shape[:-1], -1, 2).unbind(-1)
    x_rot = torch.stack([-x_imag, x_real], dim=-1).flatten(3)
    return (x.float() * cos + x_rot.float() * sin).to(x.dtype)


def ref_attn(w, p, x, rot):
    q, k, v = (F.linear(x, w[p + n]) for n in ("attn.to_q.weight", "attn.to_k.weight", "attn.to_v.weight"))
    q, k, v = (t.unflatten(-1, (H, -1)) for t in (q, k, v))
    q = F.rms_norm(q.float(), (HD,), w[p + "attn.norm_q.weight"], eps=1e-6)
    k = F.rms_norm(k.float(), (HD,), w[p + "attn.norm_k.weight"], eps=1e-6)
    q, k = ref_apply_rotary(q, *rot), ref_apply_rotary(k, *rot)
    q, k = q.to(v.dtype), k.to(v.dtype)
    o = F.scaled_dot_product_attention(q.transpose(1, 2), k.transpose(1, 2), v.transpose(1, 2)).transpose(1, 2)
    return F.linear(o.flatten(2, 3), w[p + "attn.to_out.0.weight"])


def ref_ff(w, p, x):
    h = F.linear(x, w[p + "ff.linear_in.weight"])
    half = h.shape[-1] // 2
    return F.linear(F.silu(h[..., :half]) * h[..., half:], w[p + "ff.linear_out.weight"])


def ref_mode_block(w, p, x, mod, rot):
    shift, scale, gate = mod.chunk(3, dim=-1)
    n = (1 + scale) * F.layer_norm(x, (D,), eps=1e-6) + shift
    return x + gate * (ref_attn(w, p, n, rot) + ref_ff(w, p, n))


def ref_single_block(w, p, x, ctx, mod, cmod, rot):
    shift, scale, gate = mod.chunk(3, dim=-1)
    cs, csc, cg = cmod.chunk(3, dim=-1)
    n = (1 + scale) * F.layer_norm(x, (D,), eps=1e-6) + shift
    cn = (1 + csc) * F.layer_norm(ctx, (D,), eps=1e-6) + cs
    comb = torch.cat([cn, n], dim=1)
    out = ref_attn(w, p, comb, rot) + ref_ff(w, p, comb)
    co, xo = out.split([ctx.shape[1], x.shape[1]], dim=1)
    return x + gate * xo, ctx + cg * co


def ref_temb(w, name, t):
    e = ref_timesteps(t)
    return F.linear(F.silu(F.linear(e, w[name + ".linear_1.weight"])), w[name + ".linear_2.weight"])


def ref_forward(w, x, txt, t, target, img_ids, txt_ids, cond=None, cond_ids=None):
    B, N = x.shape[:2]
    image_temb = ref_temb(w, "timestep_embedder", t * 1000)[:, None]
    zero_temb = ref_temb(w, "timestep_embedder", torch.zeros(B, device=x.device))[:, None]
    tt = ref_temb(w, "target_timestep_embedder", target * 1000)[:, None]
    image_temb, zero_temb = image_temb + tt, zero_temb + tt
    image_temb = image_temb.expand(B, N, -1)
    za = F.silu(zero_temb)
    ta = za.expand(B, txt.shape[1], -1)
    txt_e = F.linear(ta, w["early_stream_modulations.txt.linear.weight"])
    txt_s = F.linear(ta, w["single_stream_modulations.txt.linear.weight"])
    ctx = F.linear(txt, w["context_embedder.weight"])
    trot = ref_rope(txt_ids)
    ia = F.silu(image_temb)
    img_e = F.linear(ia, w["early_stream_modulations.image.linear.weight"])
    img_s = F.linear(ia, w["single_stream_modulations.image.linear.weight"])
    h = F.linear(x, w["x_embedders.image.weight"])
    irot = ref_rope(img_ids)
    if cond is not None:
        ca = za.expand(B, cond.shape[1], -1)
        c_e = F.linear(ca, w["early_stream_modulations.image_cond.linear.weight"])
        c_s = F.linear(ca, w["single_stream_modulations.image_cond.linear.weight"])
        c = F.linear(cond, w["x_embedders.image_cond.weight"])
        crot = ref_rope(cond_ids)
    for i in range(DEPTH):
        ctx = ref_mode_block(w, f"txt_mode_blocks.{i}.", ctx, txt_e, trot)
    for i in range(DEPTH):
        h = ref_mode_block(w, f"mode_blocks.image.{i}.", h, img_e, irot)
    if cond is not None:
        for i in range(DEPTH):
            c = ref_mode_block(w, f"mode_blocks.image_cond.{i}.", c, c_e, crot)
        content, cmod = torch.cat([h, c], 1), torch.cat([img_s, c_s], 1)
        rot = (torch.cat([trot[0], irot[0], crot[0]]), torch.cat([trot[1], irot[1], crot[1]]))
    else:
        content, cmod = h, img_s
        rot = (torch.cat([trot[0], irot[0]]), torch.cat([trot[1], irot[1]]))
    for i in range(SINGLE):
        content, ctx = ref_single_block(w, f"single_transformer_blocks.{i}.", content, ctx, cmod, txt_s, rot)
    h = content[:, :N]
    emb = F.linear(F.silu(image_temb), w["norm_out.linear.weight"])
    scale, shift = emb.chunk(2, dim=-1)
    h = F.layer_norm(h, (D,), eps=1e-6) * (1 + scale) + shift
    return F.linear(h, w["proj_out.weight"])


# ----------------------------------------------------------------------------- weights

def make_diffusers_weights():
    w = {}

    def lin(name, o, i, std=None):
        w[name] = torch.randn(o, i, device=DEV) * (std if std else 1 / math.sqrt(i))

    for m in ("image", "image_cond"):
        lin(f"x_embedders.{m}.weight", D, 128)
    lin("context_embedder.weight", D, 20480)
    for e in ("timestep_embedder", "target_timestep_embedder"):
        lin(f"{e}.linear_1.weight", D, 256)
        lin(f"{e}.linear_2.weight", D, D)
    for m in ("image", "image_cond", "txt"):
        lin(f"early_stream_modulations.{m}.linear.weight", 3 * D, D, 0.3 / math.sqrt(D))
        lin(f"single_stream_modulations.{m}.linear.weight", 3 * D, D, 0.3 / math.sqrt(D))

    def block(p):
        for n in ("to_q", "to_k", "to_v", "to_out.0"):
            lin(p + f"attn.{n}.weight", D, D)
        w[p + "attn.norm_q.weight"] = 1 + 0.1 * torch.randn(HD, device=DEV)
        w[p + "attn.norm_k.weight"] = 1 + 0.1 * torch.randn(HD, device=DEV)
        lin(p + "ff.linear_in.weight", 2 * MLP, D)
        lin(p + "ff.linear_out.weight", D, MLP, 0.5 / math.sqrt(MLP))

    for i in range(DEPTH):
        block(f"txt_mode_blocks.{i}.")
        block(f"mode_blocks.image.{i}.")
        block(f"mode_blocks.image_cond.{i}.")
    for i in range(SINGLE):
        block(f"single_transformer_blocks.{i}.")
    lin("norm_out.linear.weight", 2 * D, D, 0.3 / math.sqrt(D))
    lin("proj_out.weight", 128, D)
    return w


def to_bfl(w):
    """Inverse of diffusers' convert_flux3_transformer_checkpoint_to_diffusers."""
    exact = {"timestep_embedder.linear_1.weight": "time_in.in_layer.weight",
             "timestep_embedder.linear_2.weight": "time_in.out_layer.weight",
             "target_timestep_embedder.linear_1.weight": "ref_time_in.in_layer.weight",
             "target_timestep_embedder.linear_2.weight": "ref_time_in.out_layer.weight",
             "proj_out.weight": "final_layer.image.linear.weight",
             "norm_out.linear.weight": "final_layer.image.adaLN_modulation.1.weight"}
    prefix = {"x_embedders.": "emb_in.", "context_embedder.": "txt_in.", "mode_blocks.": "content_mode_blocks.",
              "single_transformer_blocks.": "single_blocks."}
    block = {"attn.to_q.weight": "q_proj.weight", "attn.to_k.weight": "k_proj.weight", "attn.to_v.weight": "v_proj.weight",
             "attn.to_out.0.weight": "attn_out.weight", "attn.norm_q.weight": "norm.query_norm.scale",
             "attn.norm_k.weight": "norm.key_norm.scale", "ff.linear_in.weight": "mlp_in.weight",
             "ff.linear_out.weight": "mlp_out.weight"}
    out = {}
    for k, v in w.items():
        if k in exact:
            if k == "norm_out.linear.weight":
                scale, shift = v.chunk(2, dim=0)
                v = torch.cat([shift, scale])
            out[exact[k]] = v
            continue
        nk = k
        for a, b in prefix.items():
            if nk.startswith(a):
                nk = b + nk[len(a):]
                break
        nk = nk.replace(".linear.weight", ".lin.weight")
        for a, b in block.items():
            if nk.endswith(a):
                nk = nk[: -len(a)] + b
                break
        out[nk] = v
    return out


def quantize_convrot(sd, group=256):
    """ComfyUI's int8_tensorwise + convrot: W_rot = W @ H^T, then int8 per row."""
    out = {}
    for k, v in sd.items():
        if k.endswith(".weight") and v.dim() == 2 and v.shape[1] % group == 0:
            h = ops.hadamard(group, v.device, torch.float32)
            wr = (v.float().reshape(v.shape[0], -1, group) @ h.T).reshape(v.shape)
            s = wr.abs().amax(dim=1, keepdim=True) / 127.0
            out[k] = (wr / s).round().clamp(-127, 127).to(torch.int8).cpu()
            out[k[:-6] + "weight_scale"] = s.reshape(-1).cpu()
            conf = {"format": "int8_tensorwise", "convrot": True, "convrot_groupsize": group}
            out[k[:-6] + "comfy_quant"] = torch.tensor(list(json.dumps(conf).encode()), dtype=torch.uint8)
        else:
            out[k] = v.to(torch.bfloat16).cpu() if v.dim() == 2 else v.cpu()
    return out


# ----------------------------------------------------------------------------- run

def cos(a, b):
    return F.cosine_similarity(a.flatten().float(), b.flatten().float(), dim=0).item()


@torch.no_grad()
def main():
    w = make_diffusers_weights()
    hh, ww, T = 24, 32, 160
    x = torch.randn(1, 128, hh, ww, device=DEV)
    txt = torch.randn(1, T, 20480, device=DEV) * 0.5
    sigma, target = 0.8, 0.65
    ids_img = fmodel.image_ids(hh, ww, 0, DEV)
    ids_txt = fmodel.text_ids(T, DEV)
    ref = ref_forward(w, x.flatten(2).transpose(1, 2), txt, torch.tensor([sigma], device=DEV),
                      torch.tensor([target], device=DEV), ids_img, ids_txt)
    ref = ref.transpose(1, 2).reshape(1, 128, hh, ww)

    cond = torch.randn(1, 128, 16, 20, device=DEV)
    cond_ids = fmodel.image_ids(16, 20, -1, DEV)
    ref_c = ref_forward(w, x.flatten(2).transpose(1, 2), txt, torch.tensor([sigma], device=DEV),
                        torch.tensor([target], device=DEV), ids_img, ids_txt,
                        cond.flatten(2).transpose(1, 2), cond_ids).transpose(1, 2).reshape(1, 128, hh, ww)

    bfl = to_bfl(w)
    results = {}
    for name, sd in (("bf16", {k: (v.to(torch.bfloat16) if v.dim() == 2 else v).cpu() for k, v in bfl.items()}),
                     ("int8 convrot", quantize_convrot(bfl))):
        m = fmodel.Flux3Model(sd)
        assert m.flow_map and len(m.single_blocks) == SINGLE and len(m.image_blocks) == DEPTH
        m.to_device(DEV, 1)  # one block resident, the rest streamed: exercises both paths
        out = m.forward(x, sigma, target, txt)
        out_c = m.forward(x, sigma, target, txt, refs=[cond])
        results[name] = (cos(out, ref), (out - ref).norm().item() / ref.norm().item(),
                         cos(out_c, ref_c), (out_c - ref_c).norm().item() / ref_c.norm().item())
        m.offload()
    for name, (c1, e1, c2, e2) in results.items():
        print(f"{name:13s} text-to-image: cos {c1:.5f} rel.err {e1:.4f} | with a reference: cos {c2:.5f} rel.err {e2:.4f}")
    ok = results["bf16"][0] > 0.999 and results["bf16"][2] > 0.999 and results["int8 convrot"][0] > 0.99
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

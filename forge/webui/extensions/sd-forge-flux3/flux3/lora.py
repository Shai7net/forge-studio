"""FLUX 3 LoRAs (diffusers/PEFT, ComfyUI/BFL and kohya key styles), applied on the side of the int8 layers.

y += strength * (x @ down.T) @ up.T, computed from the unrotated input, so nothing is merged into the quantised
weights and switching LoRAs costs nothing. Layers fused for speed (q, k, v and the MLP input) get the LoRA's up
matrix placed at the right rows of the fused output.
"""

import re

import torch

from . import model as fmodel

DIFFUSERS_TO_BFL = [
    ("single_transformer_blocks.", "single_blocks."),
    ("mode_blocks.", "content_mode_blocks."),
    ("x_embedders.", "emb_in."),
    ("context_embedder", "txt_in"),
    ("attn.to_q", "q_proj"),
    ("attn.to_k", "k_proj"),
    ("attn.to_v", "v_proj"),
    ("attn.to_out.0", "attn_out"),
    ("ff.linear_in", "mlp_in"),
    ("ff.linear_out", "mlp_out"),
    ("target_timestep_embedder.linear_1", "ref_time_in.in_layer"),
    ("target_timestep_embedder.linear_2", "ref_time_in.out_layer"),
    ("timestep_embedder.linear_1", "time_in.in_layer"),
    ("timestep_embedder.linear_2", "time_in.out_layer"),
    ("norm_out.linear", "final_layer.image.adaLN_modulation.1"),
    ("proj_out", "final_layer.image.linear"),
]
PREFIXES = ("base_model.model.", "transformer.", "diffusion_model.", "model.diffusion_model.", "unet.")


def targets(m: "fmodel.Flux3Model"):
    """BFL module name -> (Linear, row offset in that Linear's output, rows)."""
    out = {"txt_in": (m.txt_in, 0, m.txt_in.out_features),
           "final_layer.image.linear": (m.final_linear, 0, m.final_linear.out_features),
           "final_layer.image.adaLN_modulation.1": (m.final_ada, 0, m.final_ada.out_features),
           "time_in.in_layer": (m.time_in[0], 0, m.time_in[0].out_features),
           "time_in.out_layer": (m.time_in[1], 0, m.time_in[1].out_features)}
    for k, l in m.emb_in.items():
        out[f"emb_in.{k}"] = (l, 0, l.out_features)
    for k, l in m.early_mod.items():
        out[f"early_stream_modulations.{k}.lin"] = (l, 0, l.out_features)
    for k, l in m.single_mod.items():
        out[f"single_stream_modulations.{k}.lin"] = (l, 0, l.out_features)
    groups = [("content_mode_blocks.image", m.image_blocks), ("content_mode_blocks.image_cond", m.cond_blocks),
              ("txt_mode_blocks", m.txt_blocks), ("single_blocks", m.single_blocks)]
    for name, blocks in groups:
        for i, b in enumerate(blocks):
            p = f"{name}.{i}."
            if b.qkvm is not None:
                off = 0
                for part, n in zip(("q_proj", "k_proj", "v_proj", "mlp_in"), b.sizes):
                    out[p + part] = (b.qkvm, off, n)
                    off += n
            else:
                for part, l in zip(("q_proj", "k_proj", "v_proj", "mlp_in"), b.parts):
                    out[p + part] = (l, 0, l.out_features)
            out[p + "attn_out"] = (b.attn_out, 0, b.attn_out.out_features)
            out[p + "mlp_out"] = (b.mlp_out, 0, b.mlp_out.out_features)
    return out


def _normalise(name, known):
    for p in PREFIXES:
        if name.startswith(p):
            name = name[len(p):]
    if name in known:
        return name
    for a, b in DIFFUSERS_TO_BFL:
        if a.endswith("blocks.") or a in ("x_embedders.", "context_embedder"):
            # module prefixes: only at the start ("mode_blocks." is also the tail of "txt_mode_blocks.")
            if name.startswith(a):
                name = b + name[len(a):]
        else:
            name = name.replace(a, b)
    name = re.sub(r"\.linear$", ".lin", name) if "stream_modulations" in name else name
    if name in known:
        return name
    return None


def _kohya_map(known):
    return {k.replace(".", "_"): k for k in known}


def split_pairs(sd):
    """{module: (down, up, alpha)} from any of the usual key styles."""
    pairs = {}
    for k, v in sd.items():
        for down_tag, up_tag in ((".lora_A.weight", ".lora_B.weight"), (".lora_down.weight", ".lora_up.weight"),
                                 (".lora_A.default.weight", ".lora_B.default.weight")):
            if k.endswith(down_tag):
                base = k[: -len(down_tag)]
                up = sd.get(base + up_tag)
                if up is not None:
                    alpha = sd.get(base + ".alpha")
                    pairs[base] = (v, up, float(alpha) if alpha is not None else None)
    return pairs


def apply(m, loras):
    """loras: list of (state_dict, strength). Replaces whatever LoRAs were on the model. Returns (applied, missed)."""
    tmap = targets(m)
    kohya = _kohya_map(tmap)
    for l in m.all_linears():
        l.loras = []
    applied = missed = 0
    for sd, strength in loras:
        for base, (down, up, alpha) in split_pairs(sd).items():
            name = _normalise(base, tmap)
            if name is None:
                k = base.split("lora_unet_")[-1] if "lora_unet_" in base else base
                name = kohya.get(k)
            if name is None:
                missed += 1
                continue
            lin, off, n = tmap[name]
            rank = down.shape[0]
            scale = strength * (alpha / rank if alpha else 1.0)
            up = up.to(torch.bfloat16)
            if name.endswith("adaLN_modulation.1") and not base.endswith("adaLN_modulation.1"):
                up = torch.cat(up.chunk(2, dim=0)[::-1])  # diffusers keeps [scale, shift], BFL [shift, scale]
            if off != 0 or n != lin.out_features:
                full = torch.zeros(lin.out_features, rank, dtype=torch.bfloat16)
                full[off:off + n] = up
                up = full
            lin.loras.append((down.to(torch.bfloat16).contiguous(), up.contiguous(), scale))
            applied += 1
    return applied, missed

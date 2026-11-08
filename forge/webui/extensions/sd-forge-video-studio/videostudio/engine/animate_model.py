"""Wan 2.2 Animate 14B: the Wan 14B transformer plus what makes it follow a driving video.

Follows ComfyUI's comfy/ldm/wan/model_animate.py (after Wan-Video/Wan2.2 wan/modules/animate, Apache-2.0):
  - pose_patch_embedding: the skeleton video's latent, patch-embedded and added to every frame but the first
    (the first latent frame is the reference picture)
  - img_emb: CLIP ViT-H features of the reference picture, prepended to the text for the image cross-attention
  - motion_encoder + face_encoder: 512 x 512 face crops -> 20 motion numbers per frame -> 5 tokens per latent frame
  - face_adapter: after every fifth block, each latent frame's tokens attend to that frame's face tokens
The face adapter's weights ride with the block they follow (`block.face`), so the BlockStreamer brings them to the
card together; the motion and face encoders are small and run once per chunk in fp32.
"""

import math

import torch
import torch.nn as nn
import torch.nn.functional as F

from .ops import StreamLinear, StreamParam
from .wan import LayerNorm, RMSNorm, WanModel, rope_freqs, sinusoidal_embedding_1d

FACE_EVERY = 5
FACE_SLICE = 8192


class MLPProj(nn.Module):
    """img_emb: LayerNorm, Linear, GELU, Linear, LayerNorm."""

    def __init__(self, in_dim, out_dim):
        super().__init__()
        self.proj_0 = LayerNorm(in_dim, 1e-5, affine=True)
        self.proj_1 = StreamLinear(in_dim, in_dim)
        self.proj_3 = StreamLinear(in_dim, out_dim)
        self.proj_4 = LayerNorm(out_dim, 1e-5, affine=True)

    def forward(self, x):
        return self.proj_4(self.proj_3(F.gelu(self.proj_1(self.proj_0(x)))))


class FaceBlock(nn.Module):
    def __init__(self, dim, num_heads):
        super().__init__()
        self.dim, self.num_heads = dim, num_heads
        self.linear1_kv = StreamLinear(dim, dim * 2)
        self.linear1_q = StreamLinear(dim, dim)
        self.linear2 = StreamLinear(dim, dim)
        self.q_norm = RMSNorm(dim // num_heads)
        self.k_norm = RMSNorm(dim // num_heads)

    def add_(self, x, motion, frame_tokens):
        """x += attention of each latent frame's tokens to its own face tokens. x: [1, L*S, C]; motion: [1, L, 5, C]."""
        b, frames, n, c = motion.shape
        h, d = self.num_heads, c // self.num_heads
        kv = self.linear1_kv(F.layer_norm(motion.to(x.dtype), (c,), eps=1e-6))
        k, v = kv.view(b, frames, n, 2, h, d).unbind(3)
        k = self.k_norm(k).to(x.dtype)
        per = max(1, FACE_SLICE // frame_tokens)
        for f0 in range(0, frames, per):
            f1 = min(frames, f0 + per)
            xs = x[:, f0 * frame_tokens:f1 * frame_tokens]
            q = self.linear1_q(F.layer_norm(xs, (c,), eps=1e-6))
            q = self.q_norm(q.view(f1 - f0, frame_tokens, h, d)).transpose(1, 2)
            kk = k[0, f0:f1].transpose(1, 2)
            vv = v[0, f0:f1].transpose(1, 2)
            out = F.scaled_dot_product_attention(q, kk, vv).transpose(1, 2).reshape(1, -1, c)
            del q
            xs.add_(self.linear2(out))
            del out


# ---- the motion encoder (a StyleGAN2-style image encoder, after BasicSR), dense fp32 weights ------------------

def _blur(x, kernel, pad):
    c = x.shape[1]
    x = F.pad(x, [pad[0], pad[1], pad[0], pad[1]])
    k = torch.flip(kernel, [0, 1])[None, None].expand(c, 1, *kernel.shape)
    return F.conv2d(x, k, groups=c)


def _fused_lrelu(x, bias):
    return F.leaky_relu(x + bias, 0.2) * math.sqrt(2)


class MotionEncoder:
    """Face crop [B, 3, 512, 512] in [-1, 1] -> motion vector [B, 512]. Weights: name -> tensor (on device)."""

    def __init__(self, w):
        self.w = w
        self.kernel = None

    def _equal_conv(self, x, name, stride=1, padding=0, bias=None):
        weight = self.w[name]
        scale = 1 / math.sqrt(weight.shape[1] * weight.shape[2] ** 2)
        return F.conv2d(x, weight * scale, bias, stride=stride, padding=padding)

    def _conv_layer(self, x, prefix, k, downsample=False, activate=True):
        i = 0
        if downsample:
            p = 2 + (k - 1)
            x = _blur(x, self.w.get(f"{prefix}.0.kernel", self.kernel), ((p + 1) // 2, p // 2))
            i = 1
            x = self._equal_conv(x, f"{prefix}.{i}.weight", stride=2)
        else:
            x = self._equal_conv(x, f"{prefix}.{i}.weight", padding=k // 2)
        if activate:
            x = _fused_lrelu(x, self.w[f"{prefix}.{i + 1}.bias"])
        return x

    def __call__(self, img):
        p = "motion_encoder.enc.net_app.convs"
        k = torch.tensor([1.0, 3.0, 3.0, 1.0], device=img.device)
        self.kernel = (k[None] * k[:, None]) / (k[None] * k[:, None]).sum()
        h = self._conv_layer(img, f"{p}.0", 1)
        for i in range(1, 8):
            out = self._conv_layer(h, f"{p}.{i}.conv1", 3)
            out = self._conv_layer(out, f"{p}.{i}.conv2", 3, downsample=True)
            skip = self._conv_layer(h, f"{p}.{i}.skip", 1, downsample=True, activate=False)
            h = (out + skip) / math.sqrt(2)
        h = self._equal_conv(h, f"{p}.8.weight").flatten(1)
        for i in range(5):
            weight, bias = self.w[f"motion_encoder.enc.fc.{i}.weight"], self.w[f"motion_encoder.enc.fc.{i}.bias"]
            h = F.linear(h, weight / math.sqrt(weight.shape[1]), bias)
        direction = self.w["motion_encoder.dec.direction.weight"]
        stab = direction + 1e-8 * torch.eye(direction.shape[0], direction.shape[1], device=direction.device)
        q, _ = torch.linalg.qr(stab.float())
        return torch.sum(h.unsqueeze(-1) * q.T.to(h.dtype), dim=1)


class FaceEncoder:
    """Motion vectors [B, T, 512] -> face tokens [B, T', 5, dim] (T' = T / 4 rounded up, as the latent frames)."""

    def __init__(self, w, num_heads=4):
        self.w = w
        self.num_heads = num_heads

    def _causal(self, x, name, stride):
        x = F.pad(x, (2, 0), mode="replicate")
        return F.conv1d(x, self.w[f"face_encoder.{name}.conv.weight"], self.w[f"face_encoder.{name}.conv.bias"],
                        stride=stride)

    def __call__(self, x):
        b = x.shape[0]
        x = self._causal(x.transpose(1, 2), "conv1_local", 1)                     # b, 4*1024, t
        x = x.view(b, self.num_heads, 1024, -1).flatten(0, 1).transpose(1, 2)    # b*n, t, 1024
        x = F.silu(F.layer_norm(x, (1024,), eps=1e-6))
        x = F.silu(F.layer_norm(self._causal(x.transpose(1, 2), "conv2", 2).transpose(1, 2), (1024,), eps=1e-6))
        x = F.silu(F.layer_norm(self._causal(x.transpose(1, 2), "conv3", 2).transpose(1, 2), (1024,), eps=1e-6))
        x = F.linear(x, self.w["face_encoder.out_proj.weight"], self.w["face_encoder.out_proj.bias"])
        x = x.view(b, self.num_heads, x.shape[1], -1).transpose(1, 2)            # b, t, n, dim
        pad = self.w["face_encoder.padding_tokens"].to(x.dtype).reshape(1, 1, 1, -1).expand(b, x.shape[1], 1, -1)
        return torch.cat([x, pad], dim=2)


class AnimateWanModel(WanModel):
    def __init__(self, **cfg):
        cfg["image_branch"] = True
        super().__init__(**cfg)
        self.pose_patch_embedding = StreamParam()
        self.pose_patch_bias = StreamParam()
        self.img_emb = MLPProj(1280, self.dim)
        for i in range(0, len(self.blocks), FACE_EVERY):
            self.blocks[i].face = FaceBlock(self.dim, self.num_heads)
        self.dense = {}

    def set_dense(self, weights, device):
        """The motion and face encoders' weights as fp32 tensors on `device`."""
        self.dense = {n: w.materialize(device, torch.float32) for n, w in weights.items()
                      if n.startswith(("motion_encoder.", "face_encoder."))}

    @torch.no_grad()
    def face_motion(self, faces, latent_frames):
        """faces: [T, 3, 512, 512] in [-1, 1] -> [1, latent_frames, 5, dim], the first (reference) frame empty."""
        enc = MotionEncoder(self.dense)
        vec = torch.cat([enc(faces[i:i + 8].float()) for i in range(0, faces.shape[0], 8)])
        tokens = FaceEncoder(self.dense)(vec.unsqueeze(0))
        tokens = torch.cat([torch.zeros_like(tokens[:, :1]), tokens], dim=1)
        if tokens.shape[1] < latent_frames:
            tokens = torch.cat([tokens, tokens.new_zeros(1, latent_frames - tokens.shape[1], *tokens.shape[2:])], dim=1)
        return tokens[:, :latent_frames]

    def forward(self, x, t, context, streamer, on_block=None, clip_fea=None, pose_latent=None, motion=None):
        dtype = context.dtype
        b, c, tt, hh, ww = x.shape
        x = F.conv3d(x.to(dtype), self.patch_embedding.get(x.device, dtype), self.patch_bias.get(x.device, dtype),
                     stride=self.patch_size)
        if pose_latent is not None:
            p = F.conv3d(pose_latent.to(dtype), self.pose_patch_embedding.get(x.device, dtype),
                         self.pose_patch_bias.get(x.device, dtype), stride=self.patch_size)
            x[:, :, 1:p.shape[2] + 1] += p[:, :, :x.shape[2] - 1]
            del p
        grid = x.shape[2:]
        x = x.flatten(2).transpose(1, 2).contiguous()
        freqs = rope_freqs(*grid, self.dim // self.num_heads, x.device)

        e = sinusoidal_embedding_1d(self.freq_dim, t.flatten()).to(x.device, torch.float32)
        e = self.time_embedding_2(F.silu(self.time_embedding_0(e.to(dtype)))).float()
        e = e.reshape(t.shape[0], -1, e.shape[-1])
        e0 = self.time_projection_1(F.silu(e).to(dtype)).float().unflatten(2, (6, self.dim))
        context = self.embed_text(context)
        img_len = 0
        if clip_fea is not None:
            context = torch.cat([self.img_emb(clip_fea.to(x.device, dtype)), context], dim=1)
            img_len = clip_fea.shape[1]
        frame_tokens = grid[1] * grid[2]
        state = {"x": x}

        def run(i, block):
            state["x"] = block(state["x"], e0, freqs, context, img_len)
            if motion is not None and hasattr(block, "face"):
                block.face.add_(state["x"], motion, frame_tokens)
            if on_block is not None:
                on_block(i)

        streamer.run(run)
        x = self.head(state["x"], e)
        return self.unpatchify(x, grid)[:, :, :tt, :hh, :ww]

"""Wan 2.1 video VAE (8x spatial, 4x temporal, 16 channels): the one the Wan 2.2 14B models use.

Causal 3D convolutions with a per-layer cache of the last frames, so the clip is encoded 4 frames and decoded one
latent frame at a time (ComfyUI comfy/ldm/wan/vae.py, after Wan-Video/Wan2.1 wan/modules/vae.py, Apache-2.0).
Decoding a large frame can still outgrow an 8 GB card at the full-resolution stage; `decode` then works in
overlapping spatial tiles over the whole clip (the causal cache runs along time, so tiles never need each other)
and blends the seams with a linear ramp.
"""

import torch
import torch.nn as nn
import torch.nn.functional as F

CACHE_T = 2

LATENT_MEAN = [-0.7571, -0.7089, -0.9113, 0.1075, -0.1745, 0.9653, -0.1517, 1.5508,
               0.4134, -0.0715, 0.5517, -0.3632, -0.1922, -0.9497, 0.2503, -0.2921]
LATENT_STD = [2.8184, 1.4541, 2.3275, 2.6558, 1.2196, 1.7708, 2.6052, 2.0743,
              3.2687, 2.1526, 2.8652, 1.5579, 1.6382, 1.1253, 2.8251, 1.9160]


def conv3d_as_2d(x, weight, bias, stride, padding):
    """A 3D convolution as a sum of 2D convolutions, one per temporal tap.

    PyTorch 2.9 for Windows runs Conv3d without cuDNN: one 96-channel 3x3x3 layer over six 480x832 frames took
    4.6 s and 9.4 GB (2.6 GB and 0.11 s this way, same result to 2e-5). Measured 2026-10-24."""
    b, c, t, h, w = x.shape
    kt, st = weight.shape[2], stride[0]
    if padding[0]:
        x = F.pad(x, (0, 0, 0, 0, padding[0], padding[0]))
        t = x.shape[2]
    out_t = (t - kt) // st + 1
    xs = x.transpose(1, 2).contiguous()
    acc = None
    for k in range(kt):
        frames = xs[:, k:k + st * (out_t - 1) + 1:st].reshape(b * out_t, c, h, w)
        y = F.conv2d(frames, weight[:, :, k], bias if k == 0 else None, stride=stride[1:], padding=padding[1:])
        acc = y if acc is None else acc.add_(y)
    return acc.view(b, out_t, *acc.shape[1:]).transpose(1, 2)


class CausalConv3d(nn.Conv3d):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._padding = 2 * self.padding[0]
        self.padding = (0, self.padding[1], self.padding[2])

    def _conv_forward(self, x, weight, bias):
        return conv3d_as_2d(x, weight, bias, self.stride, self.padding)

    def forward(self, x, cache_x=None):
        if self._padding > 0:
            need = self._padding
            parts = []
            if cache_x is not None:
                cache_x = cache_x.to(x.device)
                need = max(0, need - cache_x.shape[2])
            if need:
                shape = list(x.shape)
                shape[2] = need
                parts.append(torch.zeros(shape, device=x.device, dtype=x.dtype))
            if cache_x is not None:
                parts.append(cache_x)
            parts.append(x)
            x = torch.cat(parts, dim=2) if len(parts) > 1 else x
        return super().forward(x)


class RMS_norm(nn.Module):
    def __init__(self, dim, channel_first=True, images=True, bias=False):
        super().__init__()
        broadcastable = (1, 1, 1) if not images else (1, 1)
        shape = (dim, *broadcastable) if channel_first else (dim,)
        self.channel_first = channel_first
        self.scale = dim ** 0.5
        self.gamma = nn.Parameter(torch.ones(shape))
        self.bias = nn.Parameter(torch.zeros(shape)) if bias else None

    def forward(self, x):
        out = F.normalize(x, dim=(1 if self.channel_first else -1)) * self.scale * self.gamma.to(x)
        return out + self.bias.to(x) if self.bias is not None else out


def _cache_of(x, old):
    cache_x = x[:, :, -CACHE_T:].clone()
    if cache_x.shape[2] < 2 and old is not None and not isinstance(old, str):
        cache_x = torch.cat([old[:, :, -1:].to(cache_x.device), cache_x], dim=2)
    return cache_x


class Resample(nn.Module):
    def __init__(self, dim, mode):
        super().__init__()
        self.mode = mode
        if mode in ("upsample2d", "upsample3d"):
            self.resample = nn.Sequential(nn.Upsample(scale_factor=(2.0, 2.0), mode="nearest-exact"), nn.Conv2d(dim, dim // 2, 3, padding=1))
        elif mode in ("downsample2d", "downsample3d"):
            self.resample = nn.Sequential(nn.ZeroPad2d((0, 1, 0, 1)), nn.Conv2d(dim, dim, 3, stride=(2, 2)))
        else:
            self.resample = nn.Identity()
        if mode == "upsample3d":
            self.time_conv = CausalConv3d(dim, dim * 2, (3, 1, 1), padding=(1, 0, 0))
        elif mode == "downsample3d":
            self.time_conv = CausalConv3d(dim, dim, (3, 1, 1), stride=(2, 1, 1), padding=(0, 0, 0))

    def forward(self, x, feat_cache=None, feat_idx=[0]):
        b, c, t, h, w = x.size()
        if self.mode == "upsample3d" and feat_cache is not None:
            idx = feat_idx[0]
            if feat_cache[idx] is None:
                feat_cache[idx] = "Rep"
                feat_idx[0] += 1
            else:
                cache_x = x[:, :, -CACHE_T:].clone()
                if cache_x.shape[2] < 2 and feat_cache[idx] is not None and feat_cache[idx] != "Rep":
                    cache_x = torch.cat([feat_cache[idx][:, :, -1:].to(cache_x.device), cache_x], dim=2)
                if cache_x.shape[2] < 2 and feat_cache[idx] is not None and feat_cache[idx] == "Rep":
                    cache_x = torch.cat([torch.zeros_like(cache_x), cache_x], dim=2)
                if isinstance(feat_cache[idx], str):
                    x = self.time_conv(x)
                else:
                    x = self.time_conv(x, feat_cache[idx])
                feat_cache[idx] = cache_x
                feat_idx[0] += 1
                x = x.reshape(b, 2, c, t, h, w)
                x = torch.stack((x[:, 0], x[:, 1]), 3)
                x = x.reshape(b, c, t * 2, h, w)
        t = x.shape[2]
        x = x.permute(0, 2, 1, 3, 4).reshape(-1, x.shape[1], x.shape[3], x.shape[4])
        x = self.resample(x)
        x = x.reshape(b, t, x.shape[1], x.shape[2], x.shape[3]).permute(0, 2, 1, 3, 4)
        if self.mode == "downsample3d" and feat_cache is not None:
            idx = feat_idx[0]
            if feat_cache[idx] is None:
                feat_cache[idx] = x.clone()
                feat_idx[0] += 1
            else:
                cache_x = x[:, :, -1:].clone()
                x = self.time_conv(torch.cat([feat_cache[idx][:, :, -1:], x], 2))
                feat_cache[idx] = cache_x
                feat_idx[0] += 1
        return x


class ResidualBlock(nn.Module):
    def __init__(self, in_dim, out_dim):
        super().__init__()
        self.residual = nn.Sequential(
            RMS_norm(in_dim, images=False), nn.SiLU(), CausalConv3d(in_dim, out_dim, 3, padding=1),
            RMS_norm(out_dim, images=False), nn.SiLU(), nn.Dropout(0.0), CausalConv3d(out_dim, out_dim, 3, padding=1))
        self.shortcut = CausalConv3d(in_dim, out_dim, 1) if in_dim != out_dim else nn.Identity()

    def forward(self, x, feat_cache=None, feat_idx=[0]):
        h = self.shortcut(x)
        for layer in self.residual:
            if isinstance(layer, CausalConv3d) and feat_cache is not None:
                idx = feat_idx[0]
                cache_x = _cache_of(x, feat_cache[idx])
                x = layer(x, feat_cache[idx])
                feat_cache[idx] = cache_x
                feat_idx[0] += 1
            else:
                x = layer(x)
        return x + h


class AttentionBlock(nn.Module):
    def __init__(self, dim):
        super().__init__()
        self.norm = RMS_norm(dim)
        self.to_qkv = nn.Conv2d(dim, dim * 3, 1)
        self.proj = nn.Conv2d(dim, dim, 1)

    def forward(self, x):
        identity = x
        b, c, t, h, w = x.size()
        x = x.permute(0, 2, 1, 3, 4).reshape(b * t, c, h, w)
        x = self.norm(x)
        q, k, v = self.to_qkv(x).chunk(3, dim=1)
        q, k, v = (z.reshape(b * t, 1, c, h * w).transpose(2, 3) for z in (q, k, v))
        x = F.scaled_dot_product_attention(q, k, v)
        x = x.transpose(2, 3).reshape(b * t, c, h, w)
        x = self.proj(x)
        x = x.reshape(b, t, c, h, w).permute(0, 2, 1, 3, 4)
        return x + identity


def _run_cached(layer, x, feat_cache, feat_idx):
    idx = feat_idx[0]
    cache_x = _cache_of(x, feat_cache[idx])
    x = layer(x, feat_cache[idx])
    feat_cache[idx] = cache_x
    feat_idx[0] += 1
    return x


class Encoder3d(nn.Module):
    def __init__(self, dim=96, z_dim=32, dim_mult=(1, 2, 4, 4), num_res_blocks=2, temporal_downsample=(False, True, True)):
        super().__init__()
        dims = [dim * u for u in [1] + list(dim_mult)]
        self.conv1 = CausalConv3d(3, dims[0], 3, padding=1)
        downs = []
        for i, (in_dim, out_dim) in enumerate(zip(dims[:-1], dims[1:])):
            for _ in range(num_res_blocks):
                downs.append(ResidualBlock(in_dim, out_dim))
                in_dim = out_dim
            if i != len(dim_mult) - 1:
                downs.append(Resample(out_dim, "downsample3d" if temporal_downsample[i] else "downsample2d"))
        self.downsamples = nn.Sequential(*downs)
        self.middle = nn.Sequential(ResidualBlock(out_dim, out_dim), AttentionBlock(out_dim), ResidualBlock(out_dim, out_dim))
        self.head = nn.Sequential(RMS_norm(out_dim, images=False), nn.SiLU(), CausalConv3d(out_dim, z_dim, 3, padding=1))

    def forward(self, x, feat_cache, feat_idx):
        x = _run_cached(self.conv1, x, feat_cache, feat_idx)
        for layer in self.downsamples:
            x = layer(x, feat_cache, feat_idx)
        for layer in self.middle:
            x = layer(x, feat_cache, feat_idx) if isinstance(layer, ResidualBlock) else layer(x)
        for layer in self.head:
            x = _run_cached(layer, x, feat_cache, feat_idx) if isinstance(layer, CausalConv3d) else layer(x)
        return x


class Decoder3d(nn.Module):
    def __init__(self, dim=96, z_dim=16, dim_mult=(1, 2, 4, 4), num_res_blocks=2, temporal_upsample=(True, True, False)):
        super().__init__()
        dims = [dim * u for u in [dim_mult[-1]] + list(dim_mult)[::-1]]
        self.conv1 = CausalConv3d(z_dim, dims[0], 3, padding=1)
        self.middle = nn.Sequential(ResidualBlock(dims[0], dims[0]), AttentionBlock(dims[0]), ResidualBlock(dims[0], dims[0]))
        ups = []
        for i, (in_dim, out_dim) in enumerate(zip(dims[:-1], dims[1:])):
            if i in (1, 2, 3):
                in_dim = in_dim // 2
            for _ in range(num_res_blocks + 1):
                ups.append(ResidualBlock(in_dim, out_dim))
                in_dim = out_dim
            if i != len(dim_mult) - 1:
                ups.append(Resample(out_dim, "upsample3d" if temporal_upsample[i] else "upsample2d"))
        self.upsamples = nn.Sequential(*ups)
        self.head = nn.Sequential(RMS_norm(out_dim, images=False), nn.SiLU(), CausalConv3d(out_dim, 3, 3, padding=1))

    def forward(self, x, feat_cache, feat_idx):
        x = _run_cached(self.conv1, x, feat_cache, feat_idx)
        for layer in self.middle:
            x = layer(x, feat_cache, feat_idx) if isinstance(layer, ResidualBlock) else layer(x)
        for layer in self.upsamples:
            x = layer(x, feat_cache, feat_idx)
        for layer in self.head:
            x = _run_cached(layer, x, feat_cache, feat_idx) if isinstance(layer, CausalConv3d) else layer(x)
        return x


def _count(model):
    return sum(isinstance(m, CausalConv3d) for m in model.modules())


class WanVAE(nn.Module):
    def __init__(self, dim=96, z_dim=16):
        super().__init__()
        self.z_dim = z_dim
        self.encoder = Encoder3d(dim, z_dim * 2, temporal_downsample=(False, True, True))
        self.conv1 = CausalConv3d(z_dim * 2, z_dim * 2, 1)
        self.conv2 = CausalConv3d(z_dim, z_dim, 1)
        self.decoder = Decoder3d(dim, z_dim, temporal_upsample=(True, True, False))
        self.register_buffer("mean", torch.tensor(LATENT_MEAN).view(1, z_dim, 1, 1, 1), persistent=False)
        self.register_buffer("std", torch.tensor(LATENT_STD).view(1, z_dim, 1, 1, 1), persistent=False)

    @classmethod
    def load(cls, path, device, dtype):
        from safetensors.torch import load_file
        sd = load_file(path)
        sd = {k.replace("first_stage_model.", ""): v for k, v in sd.items()}
        dim = sd["encoder.conv1.weight"].shape[0]
        vae = cls(dim=dim)
        vae.load_state_dict(sd, strict=True)
        return vae.to(device, dtype).eval()

    @torch.inference_mode()
    def encode(self, video, tile=None, overlap=8):
        """video: [B, 3, T, H, W] in [-1, 1], T = 1 + 4k -> normalised latent [B, 16, 1 + k, H/8, W/8].
        `tile` (latent pixels) encodes overlapping spatial tiles and blends the latents."""
        h, w = video.shape[-2] // 8, video.shape[-1] // 8
        if not tile or (h <= tile and w <= tile):
            return self._encode_full(video)
        out, weight = None, torch.zeros(1, 1, 1, h, w, device=video.device)
        for y0 in _starts(h, tile, overlap):
            for x0 in _starts(w, tile, overlap):
                part = self._encode_full(video[..., y0 * 8:(y0 + tile) * 8, x0 * 8:(x0 + tile) * 8])
                ph, pw = part.shape[-2:]
                ramp = _ramp(ph, pw, overlap, y0 > 0, y0 + tile < h, x0 > 0, x0 + tile < w).to(part.device)
                if out is None:
                    out = torch.zeros(*part.shape[:3], h, w, device=part.device)
                out[..., y0:y0 + ph, x0:x0 + pw] += part * ramp
                weight[..., y0:y0 + ph, x0:x0 + pw] += ramp
        return out / weight.clamp(min=1e-6)

    def _encode_full(self, video):
        t = video.shape[2]
        feat = [None] * _count(self.encoder)
        outs = []
        for i in range(1 + (t - 1) // 4):
            chunk = video[:, :, :1] if i == 0 else video[:, :, 1 + 4 * (i - 1):1 + 4 * i]
            idx = [0]
            outs.append(self.encoder(chunk.to(self.conv1.weight.dtype), feat, idx))
        mu = self.conv1(torch.cat(outs, 2)).chunk(2, dim=1)[0]
        return ((mu.float() - self.mean) / self.std)

    @torch.inference_mode()
    def _decode_full(self, z, on_frame=None):
        feat = [None] * _count(self.decoder)
        x = self.conv2(z.to(self.conv2.weight.dtype))
        outs = []
        for i in range(x.shape[2]):
            idx = [0]
            outs.append(self.decoder(x[:, :, i:i + 1], feat, idx).float().clamp_(-1, 1).cpu())
            if on_frame:
                on_frame(i + 1, x.shape[2])
        return torch.cat(outs, 2)

    @torch.inference_mode()
    def decode(self, z, tile=None, overlap=8, on_frame=None):
        """Normalised latent -> video in [-1, 1] on the CPU. `tile` (latent pixels) splits the frame spatially."""
        z = z.float() * self.std + self.mean
        _, _, t, h, w = z.shape
        if not tile or (h <= tile and w <= tile):
            return self._decode_full(z, on_frame)
        out = torch.zeros(1, 3, 1 + 4 * (t - 1), h * 8, w * 8)
        weight = torch.zeros(1, 1, 1, h * 8, w * 8)
        ys = _starts(h, tile, overlap)
        xs = _starts(w, tile, overlap)
        total = len(ys) * len(xs)
        n = 0
        for y0 in ys:
            for x0 in xs:
                part = self._decode_full(z[:, :, :, y0:y0 + tile, x0:x0 + tile])
                ph, pw = part.shape[-2:]
                ramp = _ramp(ph, pw, overlap * 8, y0 > 0, y0 + tile < h, x0 > 0, x0 + tile < w)
                out[..., y0 * 8:y0 * 8 + ph, x0 * 8:x0 * 8 + pw] += part * ramp
                weight[..., y0 * 8:y0 * 8 + ph, x0 * 8:x0 * 8 + pw] += ramp
                n += 1
                if on_frame:
                    on_frame(n, total)
        return out / weight.clamp(min=1e-6)


# Peak card memory per output pixel of an 81-frame clip in bf16, measured at 480x832 (2026-10-24): encode 4.66 GB,
# decode 5.45 GB. The cost does not grow with length (frames go through 4 at a time), only with frame area.
ENCODE_BYTES_PER_PIXEL = 11700
DECODE_BYTES_PER_PIXEL = 13700


def auto_tile(width, height, per_pixel, free_bytes, overlap=8):
    """Tile side in latent pixels so a tile fits in 75% of the free memory, or None when the frame fits."""
    budget = free_bytes * 0.75
    if width * height * per_pixel <= budget:
        return None
    side_px = (budget / per_pixel) ** 0.5
    return max(32, int(side_px // 8) - overlap)


def _starts(size, tile, overlap):
    if size <= tile:
        return [0]
    step = tile - overlap
    starts = list(range(0, size - tile, step)) + [size - tile]
    return sorted(set(starts))


def _ramp(h, w, o, top, bottom, left, right):
    ry = torch.ones(h)
    rx = torch.ones(w)
    if o:
        r = torch.linspace(0, 1, o + 2)[1:-1]
        if top:
            ry[:o] = r
        if bottom:
            ry[-o:] = r.flip(0)
        if left:
            rx[:o] = r
        if right:
            rx[-o:] = r.flip(0)
    return (ry[:, None] * rx[None, :]).view(1, 1, 1, h, w)

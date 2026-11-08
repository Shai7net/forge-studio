"""The FLUX.2 autoencoder that FLUX 3 uses (ae.safetensors / flux2-vae.safetensors, 0.34 GB).

A standard KL autoencoder (Forge's own Encoder/Decoder from ldm_patched): 8x down, 32 latent channels, with
quant_conv / post_quant_conv. The image model sees the latents packed 2x2 into 128 channels (16 px per token) and
normalised by the batch-norm statistics stored in the file: (z - running_mean) / sqrt(running_var + 1e-4).
Encoding takes the mean of the latent distribution (diffusers: `latent_dist.mode()`).
"""

import torch

from ldm_patched.ldm.modules.diffusionmodules.model import Decoder, Encoder

BN_EPS = 1e-4
DDCONFIG = dict(ch=128, out_ch=3, ch_mult=[1, 2, 4, 4], num_res_blocks=2, attn_resolutions=[], dropout=0.0,
                in_channels=3, resolution=256, z_channels=32, double_z=True)


def patchify(z):
    b, c, h, w = z.shape
    return z.view(b, c, h // 2, 2, w // 2, 2).permute(0, 1, 3, 5, 2, 4).reshape(b, c * 4, h // 2, w // 2)


def unpatchify(z):
    b, c, h, w = z.shape
    return z.reshape(b, c // 4, 2, 2, h, w).permute(0, 1, 4, 2, 5, 3).reshape(b, c // 4, h * 2, w * 2)


class Flux2VAE(torch.nn.Module):
    def __init__(self, sd):
        super().__init__()
        sd = dict(sd)
        for a, b in (("encoder.quant_conv.", "quant_conv."), ("decoder.post_quant_conv.", "post_quant_conv.")):
            for k in [k for k in sd if k.startswith(a)]:
                sd[b + k[len(a):]] = sd.pop(k)
        cfg = dict(DDCONFIG)
        cfg["z_channels"] = sd["decoder.conv_in.weight"].shape[1]
        self.encoder = Encoder(**cfg)
        self.decoder = Decoder(**cfg)
        z = cfg["z_channels"]
        self.quant_conv = torch.nn.Conv2d(2 * z, 2 * z, 1) if "quant_conv.weight" in sd else None
        self.post_quant_conv = torch.nn.Conv2d(z, z, 1) if "post_quant_conv.weight" in sd else None
        self.register_buffer("bn_mean", sd.pop("bn.running_mean").float().view(1, -1, 1, 1))
        self.register_buffer("bn_std", torch.sqrt(sd.pop("bn.running_var").float() + BN_EPS).view(1, -1, 1, 1))
        sd.pop("bn.num_batches_tracked", None)
        missing, unexpected = self.load_state_dict(sd, strict=False)
        missing = [k for k in missing if not k.startswith("bn_")]
        if missing or unexpected:
            raise ValueError(f"FLUX 2 VAE: missing {missing[:5]}, unexpected {unexpected[:5]}")
        self.eval().requires_grad_(False)
        self.dtype = torch.bfloat16

    def to_device(self, device):
        self.to(device=device, dtype=self.dtype)
        self.bn_mean.data = self.bn_mean.data.float()
        self.bn_std.data = self.bn_std.data.float()

    @torch.no_grad()
    def encode(self, image):
        """image [B, 3, H, W] in [-1, 1] (H, W multiples of 16) -> normalised packed latents [B, 128, H/16, W/16]."""
        h = self.encoder(image.to(self.bn_mean.device, self.dtype))
        if self.quant_conv is not None:
            h = self.quant_conv(h)
        mean = h[:, : h.shape[1] // 2].float()
        return (patchify(mean) - self.bn_mean) / self.bn_std

    @torch.no_grad()
    def decode(self, z):
        """normalised packed latents -> image [B, 3, H, W] in [-1, 1] (float32)."""
        z = unpatchify(z.float() * self.bn_std + self.bn_mean).to(self.dtype)
        if self.post_quant_conv is not None:
            z = self.post_quant_conv(z)
        return self.decoder(z).float().clamp_(-1, 1)

    @torch.no_grad()
    def decode_tiled(self, z, tile=64, overlap=8):
        """Same in tiles of `tile` packed-latent cells (1024 px), feathered over `overlap` cells."""
        from ldm_patched.modules import utils
        return utils.tiled_scale(z, lambda t: self.decode(t), tile_x=tile, tile_y=tile, overlap=overlap,
                                 upscale_amount=16, out_channels=3, output_device=z.device)

    @torch.no_grad()
    def encode_tiled(self, image, tile=1024, overlap=128):
        from ldm_patched.modules import utils
        return utils.tiled_scale(image, lambda t: self.encode(t), tile_x=tile, tile_y=tile, overlap=overlap,
                                 upscale_amount=1 / 16, out_channels=128, output_device=image.device)

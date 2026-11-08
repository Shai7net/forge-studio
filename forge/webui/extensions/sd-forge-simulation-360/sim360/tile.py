"""Tile X: the picture is drawn as a ring, so a panorama has no seam where its two ends meet.

Two things make a seam, and each has its own cure:

  * the UNet pads every convolution with zeros at the picture's border. While a panorama is drawn its
    convolutions are padded from the other side of the picture instead (left to right only; up and down
    stay as they are, a panorama does not continue from its floor into its sky).
  * the VAE has the same border. It is left alone, because Forge may decode a large picture in strips or
    tiles and each of those would then wrap onto itself. Instead the latent is given a few columns from its
    other side before it is decoded, and they are cut off the picture afterwards (and the same on the way
    in). Forge offers a hook for exactly this (`model_vae_decode_wrapper`).

Forge's own "Tiling" checkbox tiles both ways, and the "Asymmetric tiling" extension looks for
`sd_hijack.model_hijack.layers`, which Forge never fills, so it does nothing here.
"""

import torch
from torch.nn import functional as F
from torch.nn.modules.utils import _pair

# latent columns borrowed from the other side for the VAE: 8 columns are 64 pixels, more than the reach of
# the VAE's convolutions
LATENT_WRAP = 8

_layers = []    # the convolutions that are padded as a ring right now
_vaes = []      # the VAEs whose hooks are ours right now
_model = None   # the checkpoint they belong to

# Normally only this extension's own jobs draw rings. With this on, everything Forge draws does (txt2img,
# img2img, Deforum): that is how a 360 video is made in Deforum. It is off again after a restart.
everything = False


def keep_on(sd_model):
    """Called while anything is being drawn: with `everything` on, the checkpoint in use draws rings."""
    if everything and (not _layers or _model is not sd_model):
        apply(sd_model)


def _ring_forward(layer, start_step):
    left, right, top, bottom = layer._reversed_padding_repeated_twice

    def forward(input, weight, bias):
        from modules import shared
        ring = shared.state.sampling_step >= start_step and input.shape[-1] > max(left, right)
        working = F.pad(input, (left, right, 0, 0), mode="circular" if ring else "constant")
        if top or bottom:
            working = F.pad(working, (0, 0, top, bottom), mode="constant")
        return F.conv2d(working, weight, bias, layer.stride, _pair(0), layer.dilation, layer.groups)

    return forward


def _decode_as_ring(inner, samples):
    columns = min(LATENT_WRAP, samples.shape[-1])
    wide = torch.cat([samples[..., -columns:], samples, samples[..., :columns]], dim=-1)
    pixels = inner(wide)                                    # batch, height, width, channels
    cut = columns * (pixels.shape[2] // wide.shape[-1])
    return pixels[:, :, cut:pixels.shape[2] - cut, :]


def _encode_as_ring(inner, pixels):                         # batch, height, width, channels
    latent_width = pixels.shape[2] // 8
    columns = min(LATENT_WRAP, latent_width)
    borrowed = columns * (pixels.shape[2] // latent_width)
    wide = torch.cat([pixels[:, :, -borrowed:, :], pixels, pixels[:, :, :borrowed, :]], dim=2)
    latent = inner(wide)                                    # batch, channels, height, width
    cut = columns * latent.shape[-1] // (latent_width + 2 * columns)
    return latent[..., cut:latent.shape[-1] - cut]


def apply(sd_model, start_step=0):
    """Makes the loaded checkpoint draw rings until `restore` is called."""
    global _model
    restore(force=True)
    _model = sd_model
    objects = getattr(sd_model, "forge_objects", None)
    unet = getattr(getattr(objects, "unet", None), "model", None)
    if unet is None:
        raise RuntimeError("Tile X needs Forge's UNet, and this checkpoint does not have one")
    for layer in unet.modules():
        if not isinstance(layer, torch.nn.Conv2d) or isinstance(layer.padding, str):
            continue
        left, right, _top, _bottom = layer._reversed_padding_repeated_twice
        if left or right:
            layer._conv_forward = _ring_forward(layer, int(start_step or 0))
            _layers.append(layer)
    vae = getattr(objects, "vae", None)
    options = getattr(getattr(vae, "patcher", None), "model_options", None)
    if isinstance(options, dict):
        options["model_vae_decode_wrapper"] = _decode_as_ring
        options["model_vae_encode_wrapper"] = _encode_as_ring
        _vaes.append(options)
    return len(_layers)


def restore(force=False):
    """Gives the checkpoint its ordinary borders back. Safe to call when nothing was changed.
    While `everything` is on the rings stay (a job of ours ending must not switch them off)."""
    global _model
    if everything and not force:
        return
    _model = None
    for layer in _layers:
        try:
            del layer._conv_forward
        except AttributeError:
            pass
    _layers.clear()
    for options in _vaes:
        if options.get("model_vae_decode_wrapper") is _decode_as_ring:
            del options["model_vae_decode_wrapper"]
        if options.get("model_vae_encode_wrapper") is _encode_as_ring:
            del options["model_vae_encode_wrapper"]
    _vaes.clear()


def active():
    return bool(_layers)

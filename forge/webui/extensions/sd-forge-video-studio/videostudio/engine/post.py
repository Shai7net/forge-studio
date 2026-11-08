"""Finishing passes on the decoded frames: FILM in-between frames (16 -> 32 fps) and Real-ESRGAN enlargement.

Both reuse files Forge already has: Deforum's TorchScript FILM model (models/Deforum/film_interpolation/
film_net_fp16.pt, the one AnimateDiff calls) and the Real-ESRGAN weights in models/RealESRGAN, loaded once
through Spandrel. Interpolation runs first, at the small size, where it is four times cheaper.
"""

import numpy as np
import torch
import torch.nn.functional as F


@torch.inference_mode()
def film_double(frames, model_path, device, report=None):
    """frames: uint8 [T, H, W, 3] -> [2T - 1, H, W, 3] with a FILM frame between every pair."""
    model = torch.jit.load(model_path, map_location="cpu").half().to(device).eval()
    t, h, w, _ = frames.shape
    ph, pw = (64 - h % 64) % 64, (64 - w % 64) % 64

    def prep(f):
        x = torch.from_numpy(f).to(device).permute(2, 0, 1).unsqueeze(0).half() / 255.0
        return F.pad(x, (pw // 2, pw - pw // 2, ph // 2, ph - ph // 2), mode="replicate")

    out = np.empty((2 * t - 1, h, w, 3), dtype=np.uint8)
    out[0] = frames[0]
    prev = prep(frames[0])
    dt = torch.full((1, 1), 0.5, device=device, dtype=torch.float16)
    for i in range(1, t):
        cur = prep(frames[i])
        mid = model(prev, cur, dt).clamp(0, 1)
        mid = mid[:, :, ph // 2:ph // 2 + h, pw // 2:pw // 2 + w]
        out[2 * i - 1] = (mid[0].permute(1, 2, 0).float() * 255).round().byte().cpu().numpy()
        out[2 * i] = frames[i]
        prev = cur
        if report and i % 8 == 0:
            report(i / (t - 1))
    del model
    torch.cuda.empty_cache()
    return out


@torch.inference_mode()
def enlarge(frames, model_path, scale, device, report=None):
    """Real-ESRGAN (or any Spandrel model) on every frame, then resized to exactly `scale` x the input."""
    import spandrel
    desc = spandrel.ModelLoader(device="cpu").load_from_file(model_path)
    model = desc.model.to(device).half().eval()
    t, h, w, _ = frames.shape
    tw, th = int(round(w * scale / 2)) * 2, int(round(h * scale / 2)) * 2
    out = np.empty((t, th, tw, 3), dtype=np.uint8)
    for i in range(t):
        x = torch.from_numpy(frames[i]).to(device).permute(2, 0, 1).unsqueeze(0).half() / 255.0
        y = model(x).float()
        if y.shape[-2:] != (th, tw):
            y = F.interpolate(y, size=(th, tw), mode="bicubic", antialias=True, align_corners=False)
        out[i] = (y[0].clamp(0, 1).permute(1, 2, 0) * 255).round().byte().cpu().numpy()
        if report and i % 8 == 0:
            report(i / t)
    del model
    torch.cuda.empty_cache()
    return out

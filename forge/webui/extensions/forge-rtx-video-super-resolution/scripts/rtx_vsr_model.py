"""
RTX Video Super Resolution as a Forge upscaler.

NVIDIA's own video upscaler (Video Effects SDK), through the official `nvidia-vfx` package:
    pip install nvidia-vfx --extra-index-url https://pypi.nvidia.com
It runs on RTX 20 and newer with driver 570.65 or later. On an RTX 3070 Laptop a 408x728 frame takes about
5 ms at x2 and the effect holds about 200 MB of VRAM, so it suits video frames: it keeps the picture as drawn
and adds edge definition, where R-ESRGAN redraws it (measured 316 ms a frame). Any size up to 4x in one pass.

Forge collects every Upscaler subclass, so this one appears in all upscaler lists (Extras, Hires fix,
AnimateDiff, the dance panel). Without the package it is not listed.

It is listed twice, for the two kinds of source:
- "RTX Video Super Resolution": NVIDIA's high-bitrate mode, for clean pictures such as generated frames.
- "RTX Video Super Resolution (compressed video)": the standard mode, which also smooths away compression blocks.
  On a heavily compressed video the high-bitrate mode sharpens those blocks instead. 40 ms against 24 ms for a
  2160x3832 frame.
"""
import threading

import numpy as np
import torch
from PIL import Image

from modules import script_callbacks, shared
from modules.upscaler import Upscaler, UpscalerData

NAME = "RTX Video Super Resolution"
MODES = {NAME: "HIGHBITRATE_ULTRA", NAME + " (compressed video)": "ULTRA"}      # nvvfx quality levels
MAX_SCALE = 4
IDLE_SECONDS = 30      # the effects are dropped after this long without a picture, to give their VRAM back
EFFECTS_KEPT = 2       # Hires. fix and then the Upscale panel enlarge every frame of a video, each to its own size

try:
    from nvvfx import VideoSuperRes     # after torch: both bring CUDA runtime libraries
except Exception as error:              # the package is missing, or this GPU or driver cannot run it
    VideoSuperRes = None
    print(f"[{NAME}] not available: {error}")


class UpscalerRTXVideo(Upscaler):
    name = "RTXVideo"

    def __init__(self, dirname=None):
        super().__init__(False)
        self.user_path = dirname
        self.effects = {}       # (width, height, mode) -> effect, the one used last at the end
        self.timer = None
        self.lock = threading.Lock()
        # the mode travels as the "model path", which Forge hands back to do_upscale()
        self.scalers = [UpscalerData(name, mode, self, 2) for name, mode in MODES.items()] if VideoSuperRes is not None else []

    def load_model(self, path):
        return None

    def release(self):
        with self.lock:
            self.effects.clear()

    def effect_for(self, width: int, height: int, mode: str):
        """An effect is loaded for one output size and mode. A video's frames share it, so it is built once."""
        key = (width, height, mode)
        effect = self.effects.pop(key, None)
        if effect is None:
            while len(self.effects) >= EFFECTS_KEPT:
                del self.effects[next(iter(self.effects))]
            effect = VideoSuperRes(quality=getattr(VideoSuperRes.QualityLevel, mode))
            effect.output_width, effect.output_height = width, height
            effect.load()
        self.effects[key] = effect
        return effect

    def do_upscale(self, img: Image.Image, selected_model: str = None):
        scale = min(float(self.scale or 2), MAX_SCALE)
        width, height = int((img.width * scale) // 8 * 8), int((img.height * scale) // 8 * 8)
        if width <= img.width or height <= img.height:
            return img
        with self.lock:
            if self.timer is not None:
                self.timer.cancel()
            try:
                frame = torch.from_numpy(np.array(img.convert("RGB"))).to("cuda")
                frame = frame.permute(2, 0, 1).float().div(255).contiguous()
                result = self.effect_for(width, height, selected_model or MODES[NAME]).run(frame)
                out = torch.from_dlpack(result.image).clone()     # the effect reuses its buffer on the next run
                # laid out as a picture while still on the GPU: PIL takes 120 ms to read a 4K frame whose
                # channels are not next to each other in memory, and 20 ms when they are
                out = out.clamp(0, 1).mul(255).round().byte().permute(1, 2, 0).contiguous()
                img = Image.fromarray(out.cpu().numpy())
            except Exception as error:
                print(f"[{NAME}] failed, so the picture is only resized: {error}")
                self.effects.clear()
            self.timer = threading.Timer(IDLE_SECONDS, self.release)
            self.timer.daemon = True
            self.timer.start()
        return img


def release_effects():
    """Reload UI builds the upscalers again, so the old one gives its VRAM back now and not when its timer fires."""
    for data in shared.sd_upscalers:
        if isinstance(data.scaler, UpscalerRTXVideo):
            data.scaler.release()


# This callback is also what keeps the upscaler listed. Forge finds upscalers through Upscaler.__subclasses__(),
# which holds classes weakly, and keeps no reference to an extension script's module. With nothing else pointing
# here, the class was garbage collected before the list was built on some launches and not on others.
script_callbacks.on_script_unloaded(release_effects)

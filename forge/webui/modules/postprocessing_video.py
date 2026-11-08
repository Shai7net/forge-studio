"""
Extras for videos.

A video's frames go through the same postprocessing scripts as a picture (Upscale, GFPGAN, CodeFormer and whatever
extensions add), and come back as an H.264 MP4 with the original sound.

What is different from running the frames as a batch of pictures:
- ffmpeg decodes straight into memory and encodes straight from it, so there is no folder of PNGs and a long
  video never sits in RAM.
- An upscaler model is loaded once for the whole video. Forge's upscalers load theirs from disk for every picture
  and cut it into 192 px tiles: about 3 s a frame against 0.3 s.
- A frame goes through the model whole when the GPU has room for it, and in a few large cross-faded tiles when it
  has not. The room is calculated, because on Windows the NVIDIA driver spills into system RAM instead of failing:
  an RTX 3070 8 GB took 21 s for a tile that takes 3.6 s when it fits, with no error to catch.
- Colors: the source's own matrix is used on the way in, and the result is written and tagged as BT.709. Without
  the tag a player guesses the matrix from the size, so an SD video enlarged to HD would change color.
- Restored faces are followed from frame to frame and kept steady (modules/face_restoration_video.py); restored
  one frame at a time, as pictures are, they shimmer and pop in and out.
"""
import contextlib
import dataclasses
import glob
import html
import math
import os
import random
import shutil
import subprocess
import threading
import time
from fractions import Fraction

import cv2
import numpy as np
import torch
from PIL import Image

from modules import devices, face_restoration_utils, face_restoration_video, modelloader, paths, scripts, scripts_postprocessing, shared, torch_utils
from modules.shared import cmd_opts, opts
from modules.upscaler import LANCZOS
from modules_forge.forge_util import prepare_free_memory
from ldm_patched.modules import sd as forge_sd

# While a video runs. The postprocessing scripts look here to know that their picture is a frame:
frame_upscalers = None          # a FrameUpscalers: the upscaler models, kept loaded (scripts/postprocessing_upscale.py)
redraw_steadier = None          # a RedrawSteadier: keeps what Hires. fix draws from flickering (scripts/postprocessing_hires.py)
source_infotext = ""            # the generation parameters the video carries, if it was made here
remarks = None                  # a dict, {english: hebrew}: what the scripts want said under the result

# Input pixels shared by neighbouring tiles; the seam is cross-faded over them. A cut edge disturbs the model's
# output well into the tile. Measured against whole frames, in 1/255 at 16 / 32 / 48 / 96 px from the cut:
# R-ESRGAN 4x+ 0.9 / 0.7 / 0.1 / 0.04, R-ESRGAN 2x+ 0.8 / 1.1 / 0.4 / 0.1, ESRGAN_4x and DAT x2 under 0.1 from 32.
# In a video a seam stays in place while the picture moves, so it shows sooner than in a still.
OVERLAP = 128
TILE_STEP = 8                   # tiles begin on multiples of this, so a model that works on 2x2 or 4x4 blocks
                                # of pixels cuts every tile into the same blocks as the whole frame
SMALLEST_TILE = 256
PROBE_SIDE = 192                # the model's memory use is measured on a square of this side
# Bytes of VRAM per pixel of the enlarged picture, beside what the model itself takes. For the whole frame: the
# picture being put together (float32 RGB, 12), the tiles' weights (4) and its two 8 bit copies (6). For the
# tile in hand: its float32 copy (12), its weights (4) and their product (12).
FRAME_BYTES = 22
TILE_BYTES = 28
PREVIEWS_KEPT = 5
LIVE_PREVIEW_SECONDS = 1.0
LIVE_PREVIEW_SIDE = 960
HDR_TRANSFERS = (16, 18)        # libav's numbers for PQ (HDR10) and HLG

# Hires. fix on a video (RedrawSteadier). The movement between two frames is measured on a copy this many pixels
# on its long side. What the checkpoint drew is carried along that movement from the frame before: REDRAW_CARRY of
# it where the picture, followed along its movement, is what it was (within REDRAW_STILL of 255), none where it is
# something else (REDRAW_MOVED): a thing coming into view, movement the measuring lost. The movement is measured
# back as well, and where the two do not lead to the same place within REDRAW_APART pixels (nothing is carried at
# twice that) it was lost too: across a cut the picture's flat parts otherwise pass for the same thing, and the
# outlines of the scene before showed in the next one's water. A frame in which less than REDRAW_CUT of the
# picture is still the same thing is a cut, and starts afresh. Measured on an AnimateDiff clip with two cuts:
# 0.06 and 0.07 of the picture across them, 0.66 at the least between its other frames.
FLOW_SIDE = 640
REDRAW_CARRY = 0.8
REDRAW_STILL = 3.0
REDRAW_MOVED = 14.0
REDRAW_BLUR = 2.0
REDRAW_APART = 1.5
REDRAW_CUT = 0.5
UNTAGGED_MATRIX = 2
# HDR is tone mapped down to SDR; read as it is, it comes out gray and washed out
TONE_MAP = ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=hable:desat=0,"
            "zscale=t=bt709:m=bt709:r=tv,format=yuv420p")
SWS_FLAGS = "bicubic+accurate_rnd+full_chroma_int"
# Measured on 2160x3832 frames at CRF 16 (i7-11370H): medium 0.20 s a frame, faster 0.11 s for 0.2 dB less at
# 53 dB and 2% more file. With RTX Video Super Resolution the encoder is what a 4K video waits for.
ENCODER_PRESET = "faster"


class Stopped(Exception):
    """Interrupt was pressed in the middle of a frame."""


class NothingToDo(Exception):
    """No script changed the frame: nothing is switched on in the panels under the video."""


class Stopwatch:
    """Where a video's time went, for the line in the console."""

    def __init__(self):
        self.spent = {}

    @contextlib.contextmanager
    def measure(self, what: str):
        began = time.perf_counter()
        try:
            yield
        finally:
            self.spent[what] = self.spent.get(what, 0.0) + time.perf_counter() - began

    def each(self, what: str, items):
        """The items of an iterator, with the waiting for each one measured."""
        while True:
            with self.measure(what):
                item = next(items, None)
            if item is None:
                return
            yield item

    def __str__(self):
        return ", ".join(f"{what} {seconds:.1f} s" for what, seconds in self.spent.items())


@dataclasses.dataclass
class VideoInfo:
    path: str
    width: int                  # as shown: after the rotation a phone stores next to the picture
    height: int
    fps: Fraction
    duration: float             # seconds
    codec: str = ""
    audio: bool = True
    audio_codec: str = ""       # empty when not known
    tagged: bool = True         # the file says which color matrix it uses
    hdr: bool = False
    comment: str = ""           # AnimateDiff writes the generation parameters here

    @property
    def frames(self):
        return max(1, round(self.duration * self.fps))


def ffmpeg_exe():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        found = shutil.which("ffmpeg")
        assert found, "ffmpeg was not found: install imageio-ffmpeg in the venv, or put ffmpeg on the PATH"
        return found


def probe(path: str) -> VideoInfo:
    try:
        import av
    except ImportError:
        return probe_with_opencv(path)

    with av.open(path) as container:
        assert container.streams.video, "this file has no video in it"
        stream = container.streams.video[0]
        context = stream.codec_context
        if stream.duration:
            duration = float(stream.duration * stream.time_base)
        else:
            duration = (container.duration or 0) / av.time_base
        width, height = context.width, context.height
        frame = next(container.decode(stream), None)
        if frame is not None and abs(getattr(frame, "rotation", 0)) % 180 == 90:
            width, height = height, width
        return VideoInfo(
            path, width, height,
            fps=Fraction(stream.average_rate or stream.guessed_rate or 30),
            duration=duration,
            codec=context.name,
            audio=len(container.streams.audio) > 0,
            audio_codec=container.streams.audio[0].codec_context.name if container.streams.audio else "",
            tagged=getattr(context, "colorspace", UNTAGGED_MATRIX) != UNTAGGED_MATRIX,
            hdr=getattr(context, "color_trc", 0) in HDR_TRANSFERS,
            comment=container.metadata.get("comment", ""),
        )


def probe_with_opencv(path: str) -> VideoInfo:
    """Without PyAV: no word on sound or colors, so sound is looked for and the colors are taken as tagged."""
    import cv2

    capture = cv2.VideoCapture(path)
    try:
        assert capture.isOpened(), "this file could not be opened as a video"
        fps = capture.get(cv2.CAP_PROP_FPS) or 30
        return VideoInfo(
            path, int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)), int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            fps=Fraction(fps).limit_denominator(1001),
            duration=capture.get(cv2.CAP_PROP_FRAME_COUNT) / fps,
        )
    finally:
        capture.release()


def collect_messages(stream):
    """Reads what a process says in the background, so a full pipe never stalls it."""
    lines = []

    def read():
        for line in stream:
            lines.append(line.decode("utf8", "replace").strip())

    threading.Thread(target=read, daemon=True).start()
    return lines


def decode_filters(info: VideoInfo) -> str:
    # a constant frame rate: a phone's frames are not evenly spaced, and the sound has to stay in step
    filters = [f"fps={info.fps.numerator}/{info.fps.denominator}"]
    to_rgb = f"scale=flags={SWS_FLAGS}"
    if info.hdr:
        filters.append(TONE_MAP)
    elif not info.tagged:
        # the same guess a browser makes for a file that does not say: HD is BT.709, smaller is BT.601
        to_rgb += ":in_color_matrix=" + ("bt709" if info.height >= 720 or info.width >= 1280 else "bt601")
    return ",".join(filters + [to_rgb, "format=rgb24"])


def read_frames(info: VideoInfo, start: float = 0.0, length: float = None):
    """Yields the frames as H x W x 3 uint8 arrays, from `start` seconds, for `length` seconds or to the end."""
    command = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-nostdin"]
    if start > 0:
        command += ["-ss", f"{start:.3f}"]
    command += ["-i", info.path]
    if length:
        command += ["-t", f"{length:.3f}"]
    command += ["-map", "0:v:0", "-vf", decode_filters(info), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]

    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    messages = collect_messages(process.stderr)
    size = info.width * info.height * 3
    count = 0
    try:
        while True:
            data = process.stdout.read(size)
            if len(data) < size:
                break
            count += 1
            yield np.frombuffer(data, np.uint8).reshape(info.height, info.width, 3)
        if process.wait() != 0 and count == 0:
            raise RuntimeError("ffmpeg could not read the video: " + " ".join(messages))
    finally:
        if process.poll() is None:
            process.kill()
        process.stdout.close()


class Encoder:
    """An H.264 MP4 written frame by frame."""

    def __init__(self, path: str, width: int, height: int, fps: Fraction, preview: bool = False, comment: str = ""):
        self.width, self.height = width // 2 * 2, height // 2 * 2       # 4:2:0 needs even sides
        # a preview is watched once: it is encoded as fast as it goes, with a full picture twice a second, so
        # dragging through it and stepping by frames answer at once
        speed = ["-preset", "veryfast", "-g", str(max(1, round(fps / 2)))] if preview else ["-preset", ENCODER_PRESET]
        command = [
            ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{self.width}x{self.height}",
            "-r", f"{fps.numerator}/{fps.denominator}", "-i", "-",
            # the tags go in as a filter: ffmpeg 7 drops -color_primaries and -color_trc given as output options
            "-vf", f"scale=flags={SWS_FLAGS}:out_color_matrix=bt709:out_range=tv,format=yuv420p,"
                   "setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv",
            "-c:v", "libx264", "-crf", str(int(opts.extras_video_crf)), *speed,
            # the generation parameters stay with the video, so a later Hires. fix still finds its prompt
            *(["-metadata", "comment=" + comment] if comment else []),
            "-movflags", "+faststart", path,
        ]
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        self.messages = collect_messages(self.process.stderr)

    def write(self, frame: np.ndarray):
        try:
            self.process.stdin.write(np.ascontiguousarray(frame[:self.height, :self.width]).data)
        except OSError:
            raise RuntimeError("ffmpeg stopped while encoding: " + " ".join(self.messages)) from None

    def close(self):
        self.process.stdin.close()
        if self.process.wait() != 0:
            raise RuntimeError("ffmpeg could not finish the video: " + " ".join(self.messages))

    def abort(self):
        self.process.kill()
        self.process.wait()


def add_sound(silent: str, info: VideoInfo, start: float, length: float, target: str) -> bool:
    """Writes `target`: the new picture with the source's sound from the same stretch of time."""
    command = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-i", silent]
    if start > 0:
        command += ["-ss", f"{start:.3f}"]
    command += ["-t", f"{length:.3f}", "-i", info.path, "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy"]
    kinds = [["-c:a", "aac", "-b:a", "192k"]]
    if info.audio_codec in ("aac", "mp3", ""):
        kinds.insert(0, ["-c:a", "copy"])       # these play from an MP4 everywhere; any other kind is converted
    for sound in kinds:
        if subprocess.run(command + sound + ["-movflags", "+faststart", target], capture_output=True).returncode == 0:
            return True
    return False


def tiles_along(length: int, largest: int):
    """One side of a frame cut into as few tiles as `largest` allows, all the same length, evenly spaced and
    sharing at least OVERLAP pixels with the next: (tile length, where the tiles begin)."""
    if largest >= length:
        return length, [0]
    count = math.ceil((length - OVERLAP) / (largest - OVERLAP))
    while True:
        step = (length - OVERLAP) // count // TILE_STEP * TILE_STEP
        tile = length - (count - 1) * step
        if tile <= largest or step <= TILE_STEP:
            return tile, [i * step for i in range(count)]
        count += 1


def fade(length: int, width: int, at_start: bool, at_end: bool, device) -> torch.Tensor:
    """A tile's weight along one side: 1, rising smoothly from near 0 over `width` pixels where it meets another
    tile. The rise is slow at first, because the pixels right at a cut are the most disturbed."""
    weights = torch.ones(length, device=device)
    slope = torch.sin((torch.arange(width, device=device) + 0.5) / width * (math.pi / 2)) ** 2
    if at_start:
        weights[:width] = slope
    if at_end:
        weights[-width:] = slope.flip(0)
    return weights


def tile_plan(width: int, height: int, pixels: float):
    """How a frame is cut for a model that can take `pixels` at once: None when the whole frame fits, or the
    tiles across and down, each as tiles_along() gives them."""
    if width * height <= pixels:
        return None
    across = tiles_along(width, max(SMALLEST_TILE, int(math.sqrt(pixels))))
    return across, tiles_along(height, max(SMALLEST_TILE, int(pixels // across[0])))


def run_model(model, frame: torch.Tensor, pixels: float) -> torch.Tensor:
    """frame: 1 x 3 x H x W on the model's device; pixels: how much the model can take at once.
    Returns the enlarged frame as float32."""
    height, width = frame.shape[-2:]
    scale = model.scale

    def enlarged(part: torch.Tensor) -> torch.Tensor:
        # cut to the exact size: RealESRGAN x2 pads an odd side to an even one and returns the padding too
        return model(part)[..., :part.shape[-2] * scale, :part.shape[-1] * scale].float()

    plan = tile_plan(width, height, pixels)
    if plan is None:
        return enlarged(frame)

    (tile_w, xs), (tile_h, ys) = plan
    result = torch.zeros(1, 3, height * scale, width * scale, device=frame.device)
    weight = torch.zeros(1, 1, height * scale, width * scale, device=frame.device)
    for y in ys:
        for x in xs:
            if shared.state.interrupted:
                raise Stopped
            mask = (fade(tile_h * scale, OVERLAP * scale, y > 0, y + tile_h < height, frame.device)[:, None]
                    * fade(tile_w * scale, OVERLAP * scale, x > 0, x + tile_w < width, frame.device)[None, :])
            area = (..., slice(y * scale, (y + tile_h) * scale), slice(x * scale, (x + tile_w) * scale))
            result[area] += enlarged(frame[:, :, y:y + tile_h, x:x + tile_w]) * mask
            weight[area] += mask
    return result.div_(weight)


class FrameUpscalers:
    """The upscaler models of one video, each loaded once and kept until the video is done."""

    def __init__(self):
        self.loaded = {}        # upscaler name -> (model, bytes of VRAM it takes per pixel), or None for the other kinds

    def upscale(self, image: Image.Image, upscaler, scale: float) -> Image.Image:
        """The same picture as upscaler.scaler.upscale() gives: the model runs until the size is reached,
        then Lanczos brings it to the exact size."""
        if upscaler.name not in self.loaded:
            self.loaded[upscaler.name] = self.load(upscaler, image.width, image.height)
        if self.loaded[upscaler.name] is None:
            return upscaler.scaler.upscale(image, scale, upscaler.data_path)

        model, per_pixel = self.loaded[upscaler.name]
        param = torch_utils.get_param(model)
        target = (int(image.width * scale // 8 * 8), int(image.height * scale // 8 * 8))
        for _ in range(3):
            if image.width >= target[0] and image.height >= target[1]:
                break
            before = image.size
            # blue, green, red: the order Forge's upscalers give their models, so a frame matches a picture
            frame = torch.from_numpy(np.array(image.convert("RGB"))).to(param.device).flip(2).permute(2, 0, 1)
            frame = frame.unsqueeze(0).to(param.dtype).div(255)
            with torch.no_grad(), devices.without_autocast():
                frame = run_model(model, frame, self.room(model, per_pixel, image.width, image.height))
            # laid out as a picture while still on the GPU, which PIL reads six times faster; and changed in
            # place, because every copy of a x4 model's frame is hundreds of megabytes of VRAM
            frame = frame.squeeze(0).clamp_(0, 1).mul_(255).round_().byte().flip(0).permute(1, 2, 0).contiguous()
            image = Image.fromarray(frame.cpu().numpy())
            del frame
            if image.size == before:
                break
        return image if image.size == target else image.resize(target, LANCZOS)

    @staticmethod
    def room(model, per_pixel: float, width: int, height: int) -> float:
        """How many pixels of a frame the model can take at once right now.

        Asked for every frame, because what else is on the GPU changes while a video runs: the face restoration
        models load after the first frame's upscale. Half of the free memory is planned for (PyTorch holds about
        1.4 times what a run needs), less what putting the enlarged frame together takes, which for a x4 model
        is as much again as the model itself: planned once and without it, a 1088x1456 video with ESRGAN_4x and
        CodeFormer filled an 8 GB card and ran at 30 s a frame.
        """
        device = torch_utils.get_param(model).device
        if device.type != "cuda":
            return 512 * 512
        from ldm_patched.modules import model_management
        enlarged = model.scale ** 2
        usable = model_management.get_free_memory(device) / 2 - FRAME_BYTES * width * height * enlarged
        return max(0.0, usable) / (per_pixel + TILE_BYTES * enlarged)

    def load(self, upscaler, width: int, height: int):
        """The Spandrel model behind a Forge upscaler and how many pixels it can take at once. None for the kinds
        that are not a plain model here (Lanczos, RTX Video Super Resolution, LDSR, SwinIR, ScuNET): those keep
        running their own way."""
        scaler = upscaler.scaler
        kind = type(scaler).__name__
        half = not cmd_opts.no_half and not cmd_opts.upcast_sampling
        if kind in ("UpscalerESRGAN", "UpscalerHAT", "UpscalerRealESRGAN", "UpscalerDAT"):
            prepare_free_memory()
        if kind in ("UpscalerESRGAN", "UpscalerHAT"):
            model = scaler.load_model(upscaler.data_path)
            model.to(devices.device_esrgan)
            if half and model.supports_half:
                model.model.half()
        elif kind in ("UpscalerRealESRGAN", "UpscalerDAT"):
            model = modelloader.load_spandrel_model(
                scaler.load_model(upscaler.data_path).local_data_path, device=scaler.device, prefer_half=half,
                expected_architecture="DAT" if kind == "UpscalerDAT" else "ESRGAN")
        else:
            return None

        device = torch_utils.get_param(model).device
        if device.type != "cuda":
            return model, 0.0

        # The model's memory use grows evenly with the picture (measured: 8.2 KB a pixel for the x4 ESRGAN family
        # in half precision, 2.1 KB for RealESRGAN x2plus, 16.6 KB for DAT x2), so one small run tells how much
        # of a frame fits.
        from ldm_patched.modules import model_management
        sample = torch.zeros(1, 3, PROBE_SIDE, PROBE_SIDE, device=device, dtype=torch_utils.get_param(model).dtype)
        used = torch.cuda.memory_allocated(device)
        torch.cuda.reset_peak_memory_stats(device)
        with torch.no_grad(), devices.without_autocast():
            model(sample)
        per_pixel = max(1.0, (torch.cuda.max_memory_allocated(device) - used) / PROBE_SIDE ** 2)
        # Forge's own models leave the GPU only as far as whole frames need
        enlarged = model.scale ** 2
        model_management.free_memory(((per_pixel + TILE_BYTES * enlarged) * 2 + FRAME_BYTES * enlarged) * width * height, device)
        plan = tile_plan(width, height, self.room(model, per_pixel, width, height))
        print(f"Extras video: {upscaler.name} loaded once, {per_pixel / 1024:.1f} KB of VRAM a pixel, {width}x{height} frames "
              + ("go through whole" if plan is None else f"in {len(plan[0][1]) * len(plan[1][1])} tiles of {plan[0][0]}x{plan[1][0]}"))
        return model, per_pixel

    def close(self):
        self.loaded.clear()
        devices.torch_gc()


class RedrawSteadier:
    """
    Keeps what Hires. fix draws from flickering between a video's frames.

    The checkpoint draws every frame on its own, and although the noise it starts from is the same for all of
    them, the detail it adds to a wall or a face comes out different each time. What it added to the frame
    before (the redrawn frame less the enlarged one it was drawn over) is moved along the picture's own movement,
    measured between the two enlarged frames, and most of it is kept wherever the picture is still the same
    thing. Only the added detail is carried: the picture under it is always this frame's.
    """

    def __init__(self, source: str):
        # one noise for all the frames when no seed is given, and the same one whenever this video is run again
        self.seed = random.Random(source).randrange(4294967294)
        self.flow = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM)
        self.small = self.gray = self.detail = self.grid = None

    def steady(self, before: Image.Image, after: Image.Image) -> Image.Image:
        """before: the enlarged frame the checkpoint drew over; after: what it drew."""
        current = np.asarray(before, dtype=np.float32)
        detail = np.asarray(after, dtype=np.float32) - current
        height, width = current.shape[:2]
        shrink = max(1.0, max(width, height) / FLOW_SIDE)
        small = cv2.resize(current, (round(width / shrink), round(height / shrink)), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY).astype(np.uint8)

        if self.detail is not None and self.detail.shape == detail.shape:
            # for every pixel of this frame, where it was in the frame before
            flow = self.flow.calc(gray, self.gray, None)
            ys, xs = np.mgrid[0:small.shape[0], 0:small.shape[1]].astype(np.float32)
            from_x, from_y = xs + flow[:, :, 0], ys + flow[:, :, 1]
            was = cv2.remap(self.small, from_x, from_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
            changed = cv2.GaussianBlur(np.abs(small - was).mean(axis=2), (0, 0), REDRAW_BLUR)
            keep = 1 - np.clip((changed - REDRAW_STILL) / (REDRAW_MOVED - REDRAW_STILL), 0, 1)
            keep[(from_x < 0) | (from_y < 0) | (from_x > small.shape[1] - 1) | (from_y > small.shape[0] - 1)] = 0      # it was outside the picture
            back = cv2.remap(self.flow.calc(self.gray, gray, None), from_x, from_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
            apart = cv2.GaussianBlur(np.hypot(flow[:, :, 0] + back[:, :, 0], flow[:, :, 1] + back[:, :, 1]), (0, 0), REDRAW_BLUR)
            keep *= 1 - np.clip(apart / REDRAW_APART - 1, 0, 1)
            if keep.mean() < REDRAW_CUT:
                keep[:] = 0
            keep *= REDRAW_CARRY

            flow = cv2.resize(flow, (width, height), interpolation=cv2.INTER_LINEAR) * shrink
            flow[np.abs(flow) < 0.1] = 0        # standing still: carried as it is, not resampled a little softer every frame
            if self.grid is None or self.grid[0].shape != (height, width):
                self.grid = np.meshgrid(np.arange(width, dtype=np.float32), np.arange(height, dtype=np.float32))
            carried = cv2.remap(self.detail, self.grid[0] + flow[:, :, 0], self.grid[1] + flow[:, :, 1], cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
            detail += cv2.resize(keep, (width, height), interpolation=cv2.INTER_LINEAR)[:, :, None] * (carried - detail)

        self.small, self.gray, self.detail = small, gray, detail
        return Image.fromarray(np.clip(current + detail + 0.5, 0, 255).astype(np.uint8))


def stop_asked() -> bool:
    """Interrupt or Skip was pressed. The first press of Interrupt only asks Forge to stop after the picture it is
    on (the second one interrupts), and for a video that picture is the frame."""
    return shared.state.interrupted or shared.state.skipped or shared.state.stopping_generation


def enhance(image: Image.Image, args) -> Image.Image:
    """One frame through the Extras scripts, with the settings of the Extras tab."""
    pp = scripts_postprocessing.PostprocessedImage(image)
    scripts.scripts_postproc.run(pp, args)
    if pp.image is image and not pp.info:
        raise NothingToDo
    return pp.image if pp.image.mode == "RGB" else pp.image.convert("RGB")


def compare_layout(source: tuple, result: tuple) -> dict:
    """
    How a frame of the comparison video is laid out, for the before / after viewer in the page
    (javascript/extras_compare.js). Both pictures are in one video, so the viewer's two sides can never be a frame
    apart: the result at its own pixels, and beside it (under it, for a wide picture) the original at its own,
    smaller size. The viewer enlarges the original while drawing, which costs the encoder and the browser a
    quarter more than the result alone; a second full-size picture would double it.

    "before" is the part of the original that the result shows: all of it, or the middle when the result is a
    crop of it ("Scale to" with "Crop to fit").
    """
    (w, h), (W, H) = source, result
    beside = H >= W
    x, y = (W, 0) if beside else (0, H)
    cover = max(W / w, H / h)
    shown = W / cover, H / cover
    return {
        "size": [W + w, max(H, h)] if beside else [max(W, w), H + h],
        "original_at": [x, y],
        "after": [0, 0, W, H],
        "before": [x + (w - shown[0]) / 2, y + (h - shown[1]) / 2, *shown],
    }


def work_folder() -> str:
    """Where previews live, and where a video is written before it gets its sound and its name."""
    folder = os.path.join(paths.data_path, "tmp", "extras-video")
    os.makedirs(folder, exist_ok=True)
    return folder


def preview_path() -> str:
    for old in sorted(glob.glob(os.path.join(work_folder(), "preview-*.mp4")), key=os.path.getmtime)[:-PREVIEWS_KEPT]:
        try:
            os.remove(old)
        except OSError:     # still open in a player
            pass
    return os.path.join(work_folder(), f"preview-{time.strftime('%Y%m%d-%H%M%S')}.mp4")


def output_path(source: str, size) -> str:
    """Next to the pictures Extras saves, named after the source and the new size: Dance-2160x3840.mp4, and
    Dance-2160x3840-2.mp4 for the next one. Forge's running number is not used: it comes out 23 digits long in a
    folder that also holds files named by date and time."""
    folder = opts.outdir_samples or opts.outdir_extras_samples
    os.makedirs(folder, exist_ok=True)
    name = f"{os.path.splitext(os.path.basename(source))[0]}-{size[0]}x{size[1]}"
    path, number = os.path.join(folder, name + ".mp4"), 1
    while os.path.exists(path):
        number += 1
        path = os.path.join(folder, f"{name}-{number}.mp4")
    return path


def ltr(text) -> str:
    """Numbers, sizes and paths keep their left-to-right order inside a Hebrew line."""
    return f"<bdi dir='ltr'>{html.escape(str(text))}</bdi>"


def both(english: str, hebrew: str) -> str:
    """A message in both languages; style.css shows the one that fits the Hebrew UI switch."""
    return (f"<p class='hebrew-ui-skip'><span class='extras-en'>{english}</span>"
            f"<span class='extras-he' dir='rtl'>{hebrew}</span></p>")


def time_span(seconds: float):
    """(english, hebrew)"""
    if seconds < 90:
        number = f"{seconds:.2f}" if seconds < 1 else f"{seconds:.1f}"
        return f"{number} s", f"{ltr(number)} שניות"
    if seconds < 90 * 60:
        return f"{seconds / 60:.1f} min", f"{ltr(f'{seconds / 60:.1f}')} דקות"
    return f"{seconds / 3600:.1f} h", f"{ltr(f'{seconds / 3600:.1f}')} שעות"


def clock(seconds: float) -> str:
    return f"{int(seconds // 60)}:{seconds % 60:04.1f}"


def describe(video) -> str:
    """The line under the source video."""
    if not video:
        return ""
    try:
        info = probe(video)
    except Exception as error:
        return both(f"This video cannot be read: {html.escape(str(error))}", f"אי אפשר לקרוא את הווידאו: {ltr(error)}")
    length = time_span(info.duration)
    facts = [f"{info.width}×{info.height}", f"{float(info.fps):.5g} FPS"]
    codec = [info.codec.upper()] if info.codec else []
    hdr = (["HDR, will be tone mapped"], ["HDR, יומר לצבעים רגילים"]) if info.hdr else ([], [])
    return both(
        " · ".join(facts + [length[0], f"{info.frames} frames"] + codec + hdr[0] + ["with sound" if info.audio else "no sound"]),
        " · ".join([ltr(fact) for fact in facts] + [length[1], f"{ltr(info.frames)} פריימים"] + [ltr(c) for c in codec]
                   + hdr[1] + ["עם קול" if info.audio else "בלי קול"]))


def run(video, *args, preview=None):
    """
    The Extras tab's Generate (the whole video) and Preview (preview = (start second, seconds, compare); no
    seconds = the one frame at the start second).
    args are the values of the postprocessing scripts' controls. Returns the video's path, pictures for the
    gallery (the first frame after and before), the html line under them, and for a comparison preview how its
    frames are laid out (compare_layout() plus "fps"), otherwise None. With nothing to render there is no path,
    and the line says why.
    """
    global frame_upscalers, redraw_steadier, source_infotext, remarks

    if not video:
        return None, [], both("Upload a video first.", "קודם צריך להעלות סרטון."), None
    info = probe(video)
    start, length, compare, one_frame = 0.0, None, False, False
    if preview:
        start, length, compare = preview
        one_frame = not length
        # for one frame a little more than its time is read, so that rounding cannot leave none
        length = 1.5 / float(info.fps) if one_frame else max(0.2, float(length))
        start = min(max(0.0, float(start or 0)), max(0.0, info.duration - length))
    total = 1 if one_frame else max(1, round((length or info.duration) * info.fps))
    silent = os.path.join(work_folder(), f"picture-{time.strftime('%Y%m%d-%H%M%S')}.mp4")

    shared.state.job_count = total
    frame_upscalers = FrameUpscalers()
    redraw_steadier = RedrawSteadier(video)
    source_infotext = info.comment
    remarks = said = {}
    forge_sd.frames_of_one_video = {}       # Hires. fix decodes every frame the way it decoded the first
    if opts.extras_video_steady_faces:
        face_restoration_utils.steadier = face_restoration_video.FaceSteadier(float(info.fps))
    encoder = layout = packed = None
    done, size, pictures = 0, None, []
    began = shown = first_done = time.time()
    watch = Stopwatch()
    try:
        for frame in watch.each("reading", read_frames(info, start, length)):
            if stop_asked():
                break
            source = Image.fromarray(frame)
            try:
                with watch.measure("frames"):
                    result = enhance(source, args)
            except Stopped:
                break
            if shared.state.skipped:        # the scripts stop in the middle of a frame on Skip
                break
            if encoder is None:
                first_done = time.time()    # the first frame also loads the models, so it is left out of the pace
                size, pictures = result.size, [result, source]
                written = size
                if compare:
                    layout = compare_layout(source.size, size)
                    written = layout["size"]
                    packed = np.zeros((written[1], written[0], 3), np.uint8)
                encoder = Encoder(silent, *written, info.fps, preview=bool(preview), comment=info.comment)
            elif result.size != size:       # a script that crops by what is in the picture
                result = result.resize(size, LANCZOS)
            with watch.measure("encoding"):
                if compare:
                    x, y = layout["original_at"]
                    packed[:size[1], :size[0]] = np.asarray(result)
                    packed[y:y + source.height, x:x + source.width] = frame
                encoder.write(packed if compare else np.asarray(result))

            done += 1
            shared.state.nextjob()
            shared.state.textinfo = f"{done}/{total}"
            if one_frame:
                break
            if time.time() - shown > LIVE_PREVIEW_SECONDS:
                shown = time.time()
                shared.state.assign_current_image(result.reduce(max(1, max(result.size) // LIVE_PREVIEW_SIDE)))
        if not done and stop_asked():
            raise Stopped
        assert done, "no frame could be read from this video"
        with watch.measure("encoding"):
            encoder.close()
        work = time.time() - began
        print(f"Extras video: {done} frames in {work:.1f} s ({watch})")
        pace = (time.time() - first_done) / (done - 1) if done > 1 else work
    except NothingToDo:
        return None, [], both(
            "Nothing to do yet: switch on Hires. fix or face restoration, or choose an upscaler, in the panels under the video.",
            "אין עדיין מה לעשות: צריך להפעיל Hires. fix או שחזור פנים, או לבחור מגדיל (Upscaler), בחלוניות שמתחת לסרטון."), None
    except Stopped:
        return None, [], both("Stopped before the first frame was ready.", "נעצר לפני שהפריים הראשון היה מוכן."), None
    except BaseException:
        if encoder is not None:
            encoder.abort()
        if os.path.exists(silent):
            os.remove(silent)
        raise
    finally:
        frame_upscalers.close()
        frame_upscalers = redraw_steadier = remarks = forge_sd.frames_of_one_video = None
        source_infotext = ""
        face_restoration_utils.steadier = None

    seconds = float(done / info.fps)
    target = preview_path() if preview else output_path(video, size)
    if not (info.audio and not one_frame and add_sound(silent, info, start, seconds, target)):
        shutil.move(silent, target)
    if os.path.exists(silent):
        os.remove(silent)

    sizes = f"{info.width}×{info.height} → {size[0]}×{size[1]}"
    took, a_frame = time_span(work), time_span(pace)
    speed = f"{took[0]} of work, {a_frame[0]} a frame", f"{took[1]} עבודה, {a_frame[1]} לפריים"
    sides = (" Drag the line: the original is on its left, the result on its right.",
             " גוררים את הקו: משמאלו המקור, מימינו התוצאה.") if compare else ("", "")
    if one_frame:
        # no word on the whole video: this frame's time includes loading the models
        line = both(f"One frame at {clock(start)}, {sizes}. {took[0]} of work.{sides[0]}",
                    f"פריים אחד ב-{ltr(clock(start))}, {ltr(sizes)}. {took[1]} עבודה.{sides[1]}")
    elif preview:
        whole = time_span(pace * info.frames)
        line = both(
            f"Preview: {time_span(seconds)[0]} from {clock(start)} ({done} frames), {sizes}. {speed[0]}; "
            f"the whole video ({info.frames} frames) would take about {whole[0]}.{sides[0]}",
            f"תצוגה מקדימה: {time_span(seconds)[1]} מ-{ltr(clock(start))} ({ltr(done)} פריימים), {ltr(sizes)}. {speed[1]}; "
            f"הווידאו המלא ({ltr(info.frames)} פריימים) ייקח בערך {whole[1]}.{sides[1]}")
    elif done < total and stop_asked():
        line = both(
            f"Stopped after {done} of {total} frames; that part is saved as {html.escape(target)} ({sizes}). {speed[0]}.",
            f"נעצר אחרי {ltr(done)} מתוך {ltr(total)} פריימים; החלק הזה נשמר בשם {ltr(target)} ({ltr(sizes)}). {speed[1]}.")
    else:
        line = both(
            f"Saved {html.escape(target)}: {sizes}, {done} frames. {speed[0]}.",
            f"נשמר {ltr(target)}: {ltr(sizes)}, {ltr(done)} פריימים. {speed[1]}.")
    line += "".join(both(html.escape(english), hebrew) for english, hebrew in said.items())
    return target, pictures, line, layout and {**layout, "fps": float(info.fps), "still": one_frame}

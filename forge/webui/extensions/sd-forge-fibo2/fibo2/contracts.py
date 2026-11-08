"""Validation and image operations shared by Forge and the isolated worker."""

import base64
import io
import json
import math
from dataclasses import dataclass, replace

from PIL import Image, ImageOps

VARIANTS = {"dev": (50, 5.0), "edit-turbo": (4, 1.0)}
MAX_REFERENCES = 4
MAX_PIXELS = 2048 * 2048


def integer(value, name, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or int(value) != value:
        raise ValueError(f"{name} must be an integer")
    value = int(value)
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


@dataclass(frozen=True)
class Generation:
    variant: str = "dev"
    mode: str = "generate"
    prompt: str = ""
    negative_prompt: str = ""
    width: int = 1024
    height: int = 1024
    steps: int = 0
    cfg: float = 0
    seed: int = -1
    count: int = 1
    precision: str = "int8"
    prompt_writer: str = "off"

    def validated(self, references=(), mask=None):
        if self.variant not in VARIANTS:
            raise ValueError("Choose dev or edit-turbo")
        if self.mode not in ("generate", "edit"):
            raise ValueError("Choose generate or edit")
        if self.precision not in ("int8", "bf16"):
            raise ValueError("Choose int8 or bf16 weights")
        if self.prompt_writer not in ("off", "auto", "always"):
            raise ValueError("Choose off, auto or always for the prompt writer")
        if not isinstance(self.prompt, str) or not self.prompt.strip() or len(self.prompt) > 64000:
            raise ValueError("Provide a prompt or VGL JSON (up to 64000 characters)")
        if not isinstance(self.negative_prompt, str) or len(self.negative_prompt) > 64000:
            raise ValueError("Negative prompt must be text (up to 64000 characters)")
        if self.prompt.lstrip().startswith(("{", "[")):
            try:
                caption = json.loads(self.prompt)
            except ValueError as exc:
                raise ValueError(f"Invalid VGL JSON: {exc}") from exc
            if not isinstance(caption, dict):
                raise ValueError("VGL must be a JSON object")
            if self.mode == "edit" and not isinstance(caption.get("edit_instruction"), str):
                raise ValueError("An editing JSON prompt needs an edit_instruction string")
        if len(references) > MAX_REFERENCES:
            raise ValueError("FIBO accepts up to four reference images in this integration")
        if self.mode == "generate":
            if self.variant == "edit-turbo":
                raise ValueError("Edit Turbo is for image editing. Choose Dev to generate an image.")
            if references or mask is not None:
                raise ValueError("Choose Edit to use reference images or a mask")
        elif not references:
            raise ValueError("Image editing needs at least one reference image")
        if mask is not None:
            if len(references) != 1:
                raise ValueError("A mask requires exactly one reference image")
            if mask.size != references[0].size:
                raise ValueError("The mask must have the same dimensions as the source image")
        for size, name in ((self.width, "Width"), (self.height, "Height")):
            if integer(size, name, 64, 4096) % 16:
                raise ValueError(f"{name} must be a multiple of 16")
        if self.width * self.height > MAX_PIXELS:
            raise ValueError("Use an output area of at most four megapixels")
        integer(self.steps, "Steps", 0, 100)
        integer(self.seed, "Seed", -1, 2**32 - 1)
        integer(self.count, "Image count", 1, 4)
        if isinstance(self.cfg, bool) or not isinstance(self.cfg, (float, int)) or not math.isfinite(self.cfg) or not 0 <= self.cfg <= 20:
            raise ValueError("CFG must be between 0 and 20 (0 selects the model default)")
        if self.variant == "edit-turbo" and self.cfg not in (0, 1):
            raise ValueError("Edit Turbo needs CFG 1")
        # Gradio 3 sliders return floats even with step=1; canonicalize before range/tensor dimensions.
        return replace(self, width=int(self.width), height=int(self.height), steps=int(self.steps),
                       seed=int(self.seed), count=int(self.count), cfg=float(self.cfg))

    @property
    def effective_steps(self):
        return int(self.steps or VARIANTS[self.variant][0])

    @property
    def effective_cfg(self):
        return float(self.cfg or VARIANTS[self.variant][1])


def decode_image(data):
    """The API accepts embedded images only, never URLs or arbitrary server paths."""
    if not isinstance(data, str) or len(data) > 48 * 1024 * 1024:
        raise ValueError("Provide a base64 image of at most 36 MB")
    if data.startswith("data:image/"):
        data = data.split(",", 1)[-1]
    try:
        raw = base64.b64decode(data, validate=True)
        with Image.open(io.BytesIO(raw)) as im:
            if im.width * im.height > 32 * 1024 * 1024:
                raise ValueError("Input images must be at most 32 megapixels")
            return ImageOps.exif_transpose(im).copy()
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ValueError("Invalid base64 image or image dimensions") from exc


def encode_image(image):
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    return base64.b64encode(stream.getvalue()).decode("ascii")


def crop_to_canvas(image, width, height, mask=False):
    """Match Bria's scale-to-cover, ceil, then integer center crop."""
    image = image.convert("L" if mask else "RGB")
    scale = max(height / image.height, width / image.width)
    size = math.ceil(image.width * scale), math.ceil(image.height * scale)
    if size != image.size:
        image = image.resize(size, Image.Resampling.NEAREST if mask else Image.Resampling.LANCZOS)
    left, top = (size[0] - width) // 2, (size[1] - height) // 2
    return image.crop((left, top, left + width, top + height))


def grey_out(image, mask):
    """Bria's native masked-edit input: binary white areas become RGB 128."""
    binary = mask.convert("L").point(lambda x: 255 if x >= 128 else 0)
    return Image.composite(Image.new("RGB", image.size, (128, 128, 128)), image.convert("RGB"), binary)


def preserve_unmasked(result, source, mask):
    """Explicit masks preserve every black pixel of the source at the output canvas size."""
    width, height = result.size
    source = crop_to_canvas(source, width, height)
    mask = crop_to_canvas(mask, width, height, mask=True)
    return Image.composite(result.convert("RGB"), source, mask)

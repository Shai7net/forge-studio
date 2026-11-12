"""Model-neutral image controls shared with the FIBO panel; no FIBO weights or VGL assumptions."""
from PIL import Image, ImageOps


def prepare(p):
    if not hasattr(p, "fibo2_keep_source_size"):
        return  # Programmatic callers keep their existing FLUX settings.
    img2img = hasattr(p, "init_images")
    refs = [ImageOps.exif_transpose(im).convert("RGB") for im in getattr(p, "fibo2_references", [])]
    if getattr(p, "is_api", False) and not refs and getattr(p, "fibo2_mask", None) is None:
        return  # API defaults must not override the caller's FLUX writer/size/reference settings.
    if img2img:
        source = next(iter(getattr(p, "init_images", []) or []), None)
        if source is None:
            raise ValueError("FLUX 3 img2img needs a source image")
        refs.insert(0, ImageOps.exif_transpose(source).convert("RGB"))
    mode = "edit" if img2img else getattr(p, "fibo2_mode", "auto")
    mask = getattr(p, "fibo2_mask", None)
    if mode == "auto":
        mode = "edit" if refs or mask is not None else "generate"
    if mode not in ("edit", "generate"):
        raise ValueError("Choose Auto, Generate or Edit")
    if mode == "generate" and (refs or mask is not None):
        raise ValueError("Choose Auto or Edit to use reference images or a mask")
    if mode == "edit" and not refs:
        raise ValueError("FLUX 3 editing needs a reference image")
    if len(refs) > 4:
        raise ValueError("FLUX 3 supports up to four ordered references in this panel")
    if mask is not None:
        mask = ImageOps.exif_transpose(mask).convert("L")
        if len(refs) != 1 or mask.size != refs[0].size:
            raise ValueError("An exact mask needs one source reference and the same dimensions")
    if mode == "edit" and len(refs) == 1 and p.fibo2_keep_source_size:
        p.width, p.height = (max(64, n // 16 * 16) for n in refs[0].size)
        # txt2img calculated these before scripts.process.
        for attr, value in (("hr_upscale_to_x", p.width), ("hr_upscale_to_y", p.height)):
            if hasattr(p, attr) and not getattr(p, "enable_hr", False):
                setattr(p, attr, value)
    p.flux3_panel_writer = getattr(p, "fibo2_prompt_writer", "off").title()
    p.flux3_references = refs
    p.flux3_panel_edit = mode == "edit"
    p.flux3_exact_mask = mask
    if mask is not None:
        p.extra_generation_params["FLUX 3 exact mask"] = "white edits, black preserved"
    if refs:
        p.extra_generation_params["FLUX 3 references"] = len(refs)


def preserve_exact(result, source, mask):
    """Apply the panel mask in pixel space, preserving every black pixel exactly."""
    source = source.resize(result.size, Image.Resampling.LANCZOS)
    mask = mask.resize(result.size, Image.Resampling.NEAREST)
    return Image.composite(result.convert("RGB"), source.convert("RGB"), mask)


def restore_black_pixels(result, source, mask):
    source = source.resize(result.size, Image.Resampling.LANCZOS).convert("RGB")
    keep = mask.convert("L").point(lambda x: 255 if x == 0 else 0).resize(result.size, Image.Resampling.NEAREST)
    result = result.convert("RGB").copy()
    result.paste(source, mask=keep)
    return result

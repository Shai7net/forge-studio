"""
Hires. fix in Extras: a picture, or every frame of a video, is enlarged and then drawn over by the checkpoint, the
way Hires. fix finishes a txt2img picture. An upscaler only makes a picture bigger; this adds detail that was not
in it.

It runs before the Upscale panel, so the order is the one of a generation: Hires. fix, then an optional plain
upscale of the result, then face restoration.

The panel starts on the LCM setup pictures and videos are made with here: sampler LCM, 8 steps, CFG 1, and the
LCM and detail LoRAs added to the prompt. The prompt itself is the one the picture or video was made with, when it
still carries it (a PNG's parameters; the comment AnimateDiff writes into its MP4), unless one is written here.
"""
import random
import re
from contextlib import closing

import gradio as gr
from einops import rearrange

from modules import infotext_utils, postprocessing_video, scripts_postprocessing, sd_samplers, shared, ui_components
from modules.processing import StableDiffusionProcessingImg2Img, process_images
from modules.shared import opts
from modules.ui_components import FormRow
from modules.upscaler import LANCZOS

SAME_SAMPLER = "Use same sampler"
FIRST_CHOICE_UPSCALERS = ["RTX Video Super Resolution (compressed video)", "RTX Video Super Resolution", "R-ESRGAN 2x+", "R-ESRGAN 4x+", "Lanczos"]
# What the panel starts on, and what "the same as it was made with" falls back to for a picture or video that does
# not say how it was made: LCM, which needs its LoRA, a low CFG and few steps.
SAMPLER, STEPS, CFG_SCALE, DENOISING_STRENGTH = "LCM", 8, 1.0, 0.22
ADDED_TO_PROMPT = "<lora:LCM_SD15:1>, <lora:more_details:1>, D9CF2F88DE"
# the smallest side of a tile of self-attention, in latent pixels: the size the checkpoint was trained on
TILE_SD, TILE_SDXL = 64, 128

guide = (
    "<p>The picture, or every frame of the video, is enlarged with the upscaler and then drawn over by the loaded checkpoint: "
    "this is what adds sharpness and detail. The panel starts on the LCM setup: sampler LCM, 8 steps, CFG 1, and the LCM LoRA "
    "in <b>Added to the prompt</b>, which joins the prompt the picture or video was made with (or the one written here). "
    "Denoising strength is how much is drawn anew: 0.2 to 0.3 sharpens and keeps the picture; "
    "0.5 adds much more, invents things that were not there, and in a video more of it flickers. "
    "As in Hires. fix, all the Hires steps are run, on every frame: fewer steps are faster. "
    "0 steps, 0 CFG, seed -1 and “Use same sampler” take what it was made with. "
    "Every frame is a generation of its own at the new size, so try <b>Preview a frame</b> first.</p>"
)


def read_infotext(text: str) -> dict:
    """Generation parameters as Forge writes them under a picture, with their line breaks or, as AnimateDiff
    puts them into a video's comment, without."""
    text = (text or "").strip()
    if not text:
        return {}
    if "\n" not in text:
        text = text.replace(" Negative prompt: ", "\nNegative prompt: ", 1)
        last = None
        for last in re.finditer(r" Steps: \d+, ", text):
            pass
        if last:
            text = text[:last.start()] + "\n" + text[last.start() + 1:]
    made_with = infotext_utils.parse_generation_parameters(text)

    # the prompts as they were written: parse_generation_parameters takes out of them the styles it recognises,
    # and with a style goes the LoRA it adds, which an LCM picture cannot be drawn without
    *lines, last = text.split("\n")
    if len(infotext_utils.re_param.findall(last)) < 3:
        lines.append(last)
    prompts = re.split(r"(?:^|\n)Negative prompt:", "\n".join(lines), maxsplit=1)
    made_with["Prompt"] = prompts[0].strip()
    made_with["Negative prompt"] = prompts[1].strip() if len(prompts) > 1 else ""
    return made_with


def with_added(prompt: str, added: str) -> str:
    """The prompt, and after it the parts of `added` it does not have yet: a LoRA named twice is applied twice."""
    have = set(re.findall(r"<lora:([^:>]+)", prompt))
    parts = [prompt.strip().rstrip(",")]
    for part in (part.strip() for part in (added or "").split(",")):
        lora = re.match(r"<lora:([^:>]+)", part)
        if part and not (lora.group(1) in have if lora else part.lower() in prompt.lower()):
            parts.append(part)
    return ", ".join(part for part in parts if part)


def tiles_along(side: int, least: int) -> int:
    """Into how many tiles a side of the latent picture goes evenly, none of them smaller than least."""
    return next(n for n in range(max(1, side // least), 0, -1) if side % n == 0)


def attention_in_tiles(unet, least: int):
    """The UNet, with the self-attention of its first level done tile by tile (the idea of HyperTile).

    At that level every latent pixel looks at every other one, so the work grows with the square of the picture:
    at 1088x1936 it is most of a step. A tile is never smaller than the size the checkpoint was trained on, so
    each sees as much around it as when the checkpoint draws a picture of its own.

    Forge's own HyperTile is not used: for a picture that is not square it takes the height for the width, and
    it leaves what a tile looks at uncut, so a tile ends up looking at a strip of the picture somewhere else.
    """
    tiled = {}

    def split(n, context, value, extra_options):
        height, width = extra_options["original_shape"][-2:]
        rows, columns = tiles_along(height, least), tiles_along(width, least)
        if n.shape[1] != height * width or rows * columns == 1:
            return n, context, value
        tiled["as"] = dict(rows=rows, columns=columns, h=height // rows, w=width // columns)
        n = rearrange(n, "b (rows h columns w) c -> (b rows columns) (h w) c", **tiled["as"])
        return n, n, n

    def join(n, extra_options):
        if tiled:
            n = rearrange(n, "(b rows columns) (h w) c -> b (rows h columns w) c", **tiled.pop("as"))
        return n

    unet = unet.clone()
    unet.set_model_attn1_patch(split)
    unet.set_model_attn1_output_patch(join)
    return unet


class Redraw(StableDiffusionProcessingImg2Img):
    """
    img2img that samples the way Hires. fix does in its second pass, so the same numbers give the same picture.

    Hires. fix hands the sampler its steps, and the sampler then runs all of them, on the end of a schedule long
    enough for them to start from the denoising strength's share of the noise. img2img on its own does not, and
    runs only int(steps x strength) + 1 of them: 8 steps at 0.22 would be 2 steps from 0.25 of the noise.

    It can also do its self-attention in tiles. No scripts run in it, so nothing else would switch that on.
    """

    in_tiles = False

    def init(self, *args, **kwargs):
        super().init(*args, **kwargs)
        sample = self.sampler.sample_img2img

        def sample_as_hires_fix(*args, **kwargs):
            if self.in_tiles:
                # by now sample() has made forge_objects this sampling's own copy
                objects = self.sd_model.forge_objects
                objects.unet = attention_in_tiles(objects.unet, TILE_SDXL if self.sd_model.is_sdxl else TILE_SD)
            return sample(*args, steps=self.steps, **kwargs)

        self.sampler.sample_img2img = sample_as_hires_fix


def redraw(image, prompt, negative_prompt, sampler_name, steps, cfg_scale, denoising_strength, seed, in_tiles):
    """img2img over the whole picture with the loaded checkpoint. None when it was interrupted."""
    state = shared.state
    job_no, job_count, job = state.job_no, state.job_count, state.job
    p = Redraw(
        sd_model=shared.sd_model,
        outpath_samples=opts.outdir_samples or opts.outdir_img2img_samples,
        outpath_grids=opts.outdir_grids or opts.outdir_img2img_grids,
        prompt=prompt,
        negative_prompt=negative_prompt,
        seed=seed,
        sampler_name=sampler_name,
        steps=steps,
        cfg_scale=cfg_scale,
        width=image.width,
        height=image.height,
        init_images=[image],
        resize_mode=0,
        denoising_strength=denoising_strength,
        do_not_save_samples=True,
        do_not_save_grid=True,
    )
    p.in_tiles = in_tiles
    try:
        with closing(p):
            processed = process_images(p)
    finally:
        # process_images counts its picture as a job done; here the job is the Extras picture or the video's
        # frame, and whoever runs those counts them
        state.job_no, state.job_count, state.job = job_no, job_count, job
    if not processed.images or state.interrupted:
        return None
    return processed.images[0].convert("RGB")


class ScriptPostprocessingHires(scripts_postprocessing.ScriptPostprocessing):
    name = "Hires. fix"
    order = 500

    def ui(self):
        upscalers = [x.name for x in shared.sd_upscalers]
        first = next((name for name in FIRST_CHOICE_UPSCALERS if name in upscalers), upscalers[0])
        with ui_components.InputAccordion(False, label="Hires. fix", elem_id="extras_hires") as enable:
            with FormRow():
                upscaler_name = gr.Dropdown(label="Upscaler", choices=upscalers, value=first, elem_id="extras_hires_upscaler")
                steps = gr.Slider(label="Hires steps", minimum=0, maximum=150, step=1, value=STEPS, elem_id="extras_hires_steps")
                denoising_strength = gr.Slider(label="Denoising strength", minimum=0.0, maximum=1.0, step=0.01, value=DENOISING_STRENGTH, elem_id="extras_hires_denoising_strength")
            with FormRow():
                upscale_by = gr.Slider(label="Upscale by", minimum=1.0, maximum=4.0, step=0.05, value=2.0, elem_id="extras_hires_upscale_by")
                samplers = [x.name for x in sd_samplers.all_samplers]
                sampler_name = gr.Dropdown(label="Hires sampling method", choices=[SAME_SAMPLER] + samplers, value=SAMPLER if SAMPLER in samplers else SAME_SAMPLER, elem_id="extras_hires_sampler")
                cfg_scale = gr.Slider(label="Hires CFG Scale", minimum=0.0, maximum=30.0, step=0.5, value=CFG_SCALE, elem_id="extras_hires_cfg_scale")
            prompt = gr.Textbox(label="Hires prompt", lines=2, placeholder="Empty: the prompt the video or picture was made with, when it still carries it", elem_id="extras_hires_prompt")
            negative_prompt = gr.Textbox(label="Hires negative prompt", lines=2, placeholder="Empty: the negative prompt it was made with", elem_id="extras_hires_negative_prompt")
            added = gr.Textbox(label="Added to the prompt", lines=1, value=ADDED_TO_PROMPT, elem_id="extras_hires_added")
            with FormRow():
                seed = gr.Number(label="Hires seed", value=-1, precision=0, elem_id="extras_hires_seed")
                steady = gr.Checkbox(label="Keep the drawing steady from frame to frame", value=True, elem_id="extras_hires_steady")
                in_tiles = gr.Checkbox(label="Faster on large sizes (attention in tiles)", value=True, elem_id="extras_hires_in_tiles")
            gr.HTML(guide, elem_id="extras_hires_guide")

        return {
            "enable": enable,
            "upscaler_name": upscaler_name,
            "steps": steps,
            "denoising_strength": denoising_strength,
            "upscale_by": upscale_by,
            "sampler_name": sampler_name,
            "cfg_scale": cfg_scale,
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "added": added,
            "seed": seed,
            "steady": steady,
            "in_tiles": in_tiles,
        }

    def process(self, pp: scripts_postprocessing.PostprocessedImage, enable=False, upscaler_name=None, steps=STEPS, denoising_strength=DENOISING_STRENGTH,
                upscale_by=2.0, sampler_name=SAMPLER, cfg_scale=CFG_SCALE, prompt="", negative_prompt="", added=ADDED_TO_PROMPT, seed=-1, steady=True, in_tiles=True):
        if not enable or not denoising_strength:
            return

        video = postprocessing_video.redraw_steadier        # set while a video runs
        made_with = read_infotext(postprocessing_video.source_infotext if video else pp.image.info.get("parameters", ""))
        prompt = with_added((prompt or "").strip() or made_with.get("Prompt", ""), added)
        negative_prompt = (negative_prompt or "").strip() or made_with.get("Negative prompt", "")
        steps = int(steps or 0) or int(made_with.get("Steps", STEPS))
        cfg_scale = float(cfg_scale or 0) or float(made_with.get("CFG scale", CFG_SCALE))
        if not sampler_name or sampler_name == SAME_SAMPLER:
            sampler_name = made_with.get("Sampler", SAMPLER)
        if sampler_name not in sd_samplers.all_samplers_map:
            sampler_name = sd_samplers.all_samplers[0].name
        if seed is None or int(seed) < 0:
            # The seed it was made with: a picture then comes out exactly as txt2img's own Hires. fix makes it.
            # Without one, a video has one of its own, the same for all its frames (with a new noise each time
            # nothing would stay in place) and on every run, so a previewed frame is the finished video's frame.
            seed = made_with.get("Seed") or (video.seed if video else random.randrange(4294967294))
        seed = int(seed)

        image = pp.image.convert("RGB")
        upscaler = next((x for x in shared.sd_upscalers if x.name == upscaler_name and x.name != "None"), None)
        if upscale_by > 1 and upscaler is not None:
            frames = postprocessing_video.frame_upscalers
            image = frames.upscale(image, upscaler, upscale_by) if frames else upscaler.scaler.upscale(image, upscale_by, upscaler.data_path)
        # the checkpoint draws in multiples of 8
        size = (int(pp.image.width * upscale_by) // 8 * 8, int(pp.image.height * upscale_by) // 8 * 8)
        if image.size != size:
            image = image.resize(size, LANCZOS)

        redrawn = redraw(image, prompt, negative_prompt, sampler_name, steps, cfg_scale, denoising_strength, seed, in_tiles)
        if redrawn is None:
            if video:
                raise postprocessing_video.Stopped
            return
        if video and steady:
            redrawn = video.steady(image, redrawn)

        loaded = shared.sd_model.sd_checkpoint_info
        if video and made_with.get("Model hash") and loaded.shorthash and made_with["Model hash"] != loaded.shorthash:
            made = made_with.get("Model", made_with["Model hash"])
            postprocessing_video.remarks[f"Hires. fix drew with the loaded checkpoint, {loaded.name_for_extra}; the video was made with {made}."] = (
                f"Hires. fix צייר עם ה-checkpoint הטעון, {postprocessing_video.ltr(loaded.name_for_extra)}; "
                f"הסרטון נוצר עם {postprocessing_video.ltr(made)}.")

        pp.image = redrawn
        pp.info["Hires upscale"] = upscale_by
        pp.info["Hires upscaler"] = upscaler.name if upscaler is not None else "Lanczos"
        pp.info["Denoising strength"] = denoising_strength
        pp.info["Hires steps"] = steps
        pp.info["Hires sampler"] = sampler_name
        pp.info["Hires CFG Scale"] = cfg_scale
        pp.info["Hires seed"] = seed
        pp.info["Hires checkpoint"] = loaded.name_for_extra
        if prompt:
            pp.info["Hires prompt"] = prompt

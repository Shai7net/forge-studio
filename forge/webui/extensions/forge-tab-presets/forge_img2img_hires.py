"""A second image-to-image refinement pass; the original processing object stays intact."""
import copy
import math

TITLE = 'Hires. fix (img2img)'


def target_size(width, height, scale=2, resize_x=0, resize_y=0):
    if not all(math.isfinite(float(v)) for v in (width, height, scale, resize_x, resize_y)):
        raise ValueError('Hires dimensions must be finite numbers.')
    if not 1 <= float(scale) <= 4:
        raise ValueError('Hires scale must be between 1 and 4.')
    w, h = float(resize_x or 0), float(resize_y or 0)
    if not w and not h:
        w, h = width * scale, height * scale
    elif not w:
        w = h * width / height
    elif not h:
        h = w * height / width
    w, h = max(64, round(w / 8) * 8), max(64, round(h / 8) * 8)
    if max(w, h) > 8192 or w * h > 16_777_216:
        raise ValueError('Hires output is limited to 8192 pixels per side and 16 megapixels.')
    return w, h


def settings(p):
    if getattr(p, '_forge_img2img_hires_pass', False):
        return None
    for script in getattr(getattr(p, 'scripts', None), 'alwayson_scripts', []):
        if script.title() == TITLE:
            values = list(p.script_args[script.args_from:script.args_to])
            if len(values) == 7 and values[0]:
                return values
    return None


def annotate(p, values):
    _, upscaler, scale, steps, denoise, width, height = values
    p.extra_generation_params.update({'Hires fix (img2img)': True, 'Hires upscaler': upscaler,
        'Hires upscale': scale, 'Hires steps': int(steps or p.steps), 'Hires denoising strength': denoise})
    if width or height:
        p.extra_generation_params['Hires resize'] = f'{int(width or 0)}x{int(height or 0)}'


def refine(p, image, values, index=0, prompt=None, negative=None, seed=None):
    from modules import images, processing, scripts, shared
    _, upscaler, scale, steps, denoise, width, height = values
    w, h = target_size(image.width, image.height, scale, width, height)
    enlarged = images.resize_image(0, image, w, h, upscaler_name=upscaler)
    if shared.state.interrupted or shared.state.skipped:
        return image
    from forge_workflow import checkpoint_family, pause_scripts
    family = checkpoint_family(shared.opts.sd_model_checkpoint)
    if float(denoise) == 0 and family != 'fibo':
        return enlarged
    prompts = getattr(p, 'all_prompts', None) or [p.prompt]
    negatives = getattr(p, 'all_negative_prompts', None) or [p.negative_prompt]
    seeds = getattr(p, 'all_seeds', None) or [p.seed]
    prompt = prompt if prompt is not None else prompts[min(index, len(prompts) - 1)]
    negative = negative if negative is not None else negatives[min(index, len(negatives) - 1)]
    seed = seed if seed is not None else seeds[min(index, len(seeds) - 1)]
    if family == 'fibo':
        prompt = ('Refine the provided image at higher resolution. Enhance natural textures and fine detail while '
                  'preserving its composition, subjects, faces, pose, colors and visual style exactly.')
    q = processing.StableDiffusionProcessingImg2Img(
        sd_model=p.sd_model, init_images=[enlarged], prompt=prompt, negative_prompt=negative, styles=[],
        seed=int(seed), subseed=int(getattr(p, 'subseed', -1)), width=w, height=h, batch_size=1, n_iter=1,
        steps=int(steps or p.steps), cfg_scale=p.cfg_scale, sampler_name=p.sampler_name,
        denoising_strength=float(denoise), resize_mode=0, do_not_save_samples=True, do_not_save_grid=True,
        outpath_samples=p.outpath_samples, outpath_grids=p.outpath_grids,
        restore_faces=False, tiling=getattr(p, 'tiling', False))
    q._forge_img2img_hires_pass = True
    # Only ControlNet participates again. Postprocessors run once on the final
    # parent image; video-producing scripts must not run on this still-image pass.
    q.scripts = copy.copy(p.scripts) if p.scripts else scripts.ScriptRunner()
    q.scripts.alwayson_scripts = [s for s in getattr(q.scripts, 'alwayson_scripts', [])
                                 if 'controlnet' in s.title().lower()]
    q.script_args = list(getattr(p, 'script_args', []) or [])
    if family in ('flux', 'fibo'):
        pause_scripts(q)
    if family == 'flux':
        q.flux3_edit_mode = 'Classic img2img (denoise)'
        q.flux3_writer = 'Off'
    if family == 'fibo':
        for key in ('fibo2_variant', 'fibo2_precision', 'fibo2_loras', 'fibo2_lora_weight'):
            if hasattr(p, key):
                setattr(q, key, getattr(p, key))
        q.fibo2_mode, q.fibo2_keep_source_size, q.fibo2_prompt_writer = 'edit', False, 'off'
    q.extra_generation_params.update(p.extra_generation_params)
    try:
        result = processing.process_images(q)
        if not result.images:
            return image
        return result.images[result.index_of_first_image]
    finally:
        q.close()


def native_result(p, result):
    """FIBO bypasses Forge's ScriptRunner postprocessing, so finish its native outputs here."""
    from modules import processing
    if not isinstance(p, processing.StableDiffusionProcessingImg2Img):
        return result
    values = settings(p)
    if not values:
        return result
    annotate(p, values)
    for i in range(result.index_of_first_image, len(result.images)):
        index = i - result.index_of_first_image
        result.images[i] = refine(p, result.images[i], values, index=index,
            prompt=result.all_prompts[index], negative=result.all_negative_prompts[index], seed=result.all_seeds[index])
    result.width, result.height = result.images[-1].size
    result.extra_generation_params.update(p.extra_generation_params)
    return result

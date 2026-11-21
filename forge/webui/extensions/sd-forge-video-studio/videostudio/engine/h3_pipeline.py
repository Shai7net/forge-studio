"""Headless execution of the linked MiniMax-H3 FL2V 8 GB ComfyUI workflow."""
import gc
import importlib.util
import json
import os
from pathlib import Path
import sys
import threading
import time

from .pipeline import Cancelled, finish, write_outputs


def _package(name, source):
    source = Path(source)
    spec = importlib.util.spec_from_file_location(name, source,
                submodule_search_locations=[str(source.parent)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def backend(job):
    runtime = Path(job['comfy_runtime'])
    sys.path.insert(0, str(runtime))
    import comfy.options
    comfy.options.enable_args_parsing()
    sys.argv = ['forge-h3-worker', '--lowvram', '--fp16-vae', '--reserve-vram', '1.0',
                '--use-pytorch-cross-attention', '--preview-method', 'latent2rgb']
    import torch
    import comfy.model_management
    import comfy.utils
    import folder_paths
    comfy.utils.set_progress_bar_enabled(False)
    for key, paths in {
        'diffusion_models': [job['model']], 'unet': [job['model']],
        'text_encoders': [job['text_encoder']], 'clip': [job['text_encoder']],
        'vae': [job['vae']], 'clip_projections': [job['projection']],
    }.items():
        for path in paths:
            folder_paths.add_model_folder_path(key, str(Path(path).parent))
    custom = Path(job['h3_custom_runtime'])
    clipproj = _package('forge_h3_clipproj', custom / 'ComfyUI-ClipProj/__init__.py')
    spectrum = _package('forge_h3_spectrum', custom / 'ComfyUI-Spectrum-MiniMax-H3/__init__.py')
    return torch, comfy, clipproj.NODE_CLASS_MAPPINGS, spectrum.NODE_CLASS_MAPPINGS


def unload(comfy, torch):
    comfy.model_management.unload_all_models()
    gc.collect()
    comfy.model_management.soft_empty_cache()
    torch.cuda.empty_cache()


def generate(job, emit):
    started = time.monotonic()
    cancel_file = Path(job['cancel_file']) if job.get('cancel_file') else None
    stopped = threading.Event()

    def check():
        if cancel_file and cancel_file.exists():
            raise Cancelled()

    def report(stage, progress=None, **extra):
        check()
        event = dict(stage=stage, elapsed=round(time.monotonic() - started, 1), **extra)
        if progress is not None:
            event['progress'] = min(1.0, max(0.0, float(progress)))
        emit(event)

    report('start', 0)
    torch, comfy, clipproj, spectrum = backend(job)
    if not torch.cuda.is_available():
        raise RuntimeError('MiniMax-H3 requires a CUDA GPU in this worker.')

    def watch_cancel():
        while not stopped.wait(0.2):
            if cancel_file and cancel_file.exists():
                comfy.model_management.interrupt_current_processing(True)
                return

    thread = threading.Thread(target=watch_cancel, daemon=True)
    thread.start()
    try:
        with torch.inference_mode():
            return run(job, torch, comfy, clipproj, spectrum, report)
    except comfy.model_management.InterruptProcessingException:
        raise Cancelled()
    except Exception:
        check()
        raise
    finally:
        stopped.set()
        comfy.model_management.interrupt_current_processing(False)


def run(job, torch, comfy, clipproj, spectrum, report):
    import numpy as np
    from PIL import Image, ImageOps
    import nodes
    from comfy_extras.nodes_custom_sampler import (BasicGuider, BasicScheduler, KSamplerSelect,
                                                    RandomNoise, SamplerCustomAdvanced)
    from comfy_extras.nodes_minimax_h3 import MiniMaxH3ImageToVideo, MiniMaxH3SigmaShift
    from comfy_extras.nodes_model_advanced import ModelAttentionBackend

    parts = job.get('parts') or [dict(start_image=job.get('start_image'), end_image=job.get('end_image'),
                                     prompt=job['prompt'])]
    if len(parts) > 5:
        raise ValueError('MiniMax-H3 supports at most six pictures (five joined parts) per job.')

    width, height, frames = job['width'], job['height'], job['frames']

    def pixels(path):
        if not path:
            return None
        with Image.open(path) as source:
            image = ImageOps.fit(source.convert('RGB'), (width, height), method=Image.Resampling.LANCZOS)
            return torch.from_numpy(np.asarray(image).astype(np.float32) / 255)[None]

    report('text', 0, note='Qwen3-VL 4B INT8 + ClipProj')
    clip = nodes.CLIPLoader().load_clip(Path(job['text_encoder']).name, 'krea2')[0]
    clip = clipproj['ClipProjApply']().apply(clip, Path(job['projection']).name)[0]
    vae = nodes.VAELoader().load_vae(Path(job['vae']).name)[0]
    prepared = []
    for index, part in enumerate(parts):
        positive, latent = MiniMaxH3ImageToVideo.execute(
            clip, vae, part.get('prompt') or job['prompt'], width, height, frames,
            pixels(part.get('start_image')), pixels(part.get('end_image'))).result
        prepared.append((positive, latent))
        report('text', (index + 1) / len(parts), note=f'part {index + 1} of {len(parts)}')
    del clip
    unload(comfy, torch)

    report('loading', 0, note='MiniMax-H3 W4A8 with low-VRAM offload')
    model = nodes.UNETLoader().load_unet(Path(job['model']).name, 'default')[0]
    model = ModelAttentionBackend.execute(model, job['attention']).result[0]
    model = MiniMaxH3SigmaShift.execute(model, job['shift'], job['audio_shift']).result[0]
    if job.get('spectrum', True):
        model = spectrum['SpectrumApplyMiniMaxH3']().apply(
            model, True, 0.5, 1, 0.1, 2.0, 0.75, 1, 1, 8, False,
            'system_ram', True, False, False, True, 0.0, 'system_ram', 'off', 0.65,
            False, False, 'coordinate_rls', 'hard_clip', 0.4, 'no_attenuation', 'balanced')[0]
    report('loading', 1)

    pieces = []
    previous_hook = comfy.utils.PROGRESS_BAR_HOOK
    try:
        for index, (positive, latent) in enumerate(prepared):
            noise = RandomNoise.execute(int(job['seed']) + index).result[0]
            guider = BasicGuider.execute(model, positive).result[0]
            sampler = KSamplerSelect.execute('euler').result[0]
            sigmas = BasicScheduler.execute(model, 'simple', 20, 1.0).result[0]

            def hook(value, total, preview=None, **_kwargs):
                report('sampling', (index + value / max(total, 1)) / len(parts),
                       step=int(value), steps=int(total), note=f'part {index + 1} of {len(parts)}')

            comfy.utils.set_progress_bar_global_hook(hook)
            report('sampling', index / len(parts), note=f'part {index + 1} of {len(parts)}')
            sampled = SamplerCustomAdvanced.execute(noise, guider, sampler, sigmas, latent).result[0]
            del guider, latent
            unload(comfy, torch)
            report('decode', index / len(parts))
            images = nodes.VAEDecode().decode(vae, sampled)[0]
            if not torch.isfinite(images).all() or not len(images):
                raise RuntimeError('MiniMax-H3 did not produce valid video frames.')
            array = images.float().clamp(0, 1).mul(255).round().byte().cpu().numpy()
            preview_path = Path(job.get('work_dir') or job['output_dir']) / 'h3-decoded-preview.png'
            Image.fromarray(array[len(array) // 2]).save(preview_path)
            report('decode', (index + 1) / len(parts), preview=str(preview_path), preview_key=100 + index)
            pieces.append(array if index == 0 else array[1:])
            del positive, sampled, images
            unload(comfy, torch)
    finally:
        comfy.utils.set_progress_bar_global_hook(previous_hook)

    del model, vae, prepared
    unload(comfy, torch)
    output = np.concatenate(pieces)
    base_frames = len(output)
    output = finish(job, output, torch.device('cuda'), report)
    job['frames'] = len(output)
    result = write_outputs(job, output, report)
    meta_path = Path(result['video']).with_suffix('.json')
    meta = json.loads(meta_path.read_text(encoding='utf-8'))
    meta.update(backend='h3', native_audio=False, base_frames=base_frames, base_fps=24,
                model=Path(job['model']).name, workflow_revision=job.get('workflow_revision'),
                comfy_runtime_revision='8a98ecf5864433064809ac1f2b52ccbf8b444471',
                peak_vram_gb=round(torch.cuda.max_memory_allocated() / 2**30, 3))
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')
    report('done', 1, **result)
    return result

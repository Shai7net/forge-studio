"""Headless ComfyUI LTX nodes, isolated in Video Studio's cancellable worker."""
import gc
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

from .pipeline import Cancelled, ffmpeg_exe, finish, write_outputs


def backend(job):
    runtime = Path(job['comfy_runtime'])
    sys.path.insert(0, str(runtime))
    import comfy.options
    comfy.options.enable_args_parsing()
    sys.argv = ['forge-ltx-worker', '--lowvram', '--disable-dynamic-vram',
                '--use-pytorch-cross-attention', '--reserve-vram', '1.0', '--bf16-vae',
                '--preview-method', 'latent2rgb']
    import torch
    import comfy.sd
    import comfy.sample
    import comfy.samplers
    import comfy.model_management
    import comfy.utils
    # Keep machine-readable @@VS events on clean lines; the progress hook still runs.
    comfy.utils.set_progress_bar_enabled(False)
    import folder_paths
    # Register only our model directories. Nothing in the shared runtime is edited.
    for key, paths in {'diffusion_models': [job['model']], 'unet': [job['model']],
                       'text_encoders': [job['text_encoder'], job['connectors']],
                       'clip': [job['text_encoder'], job['connectors']]}.items():
        for path in paths:
            folder_paths.add_model_folder_path(key, str(Path(path).parent))
    source = Path(job['gguf_runtime']) / '__init__.py'
    spec = importlib.util.spec_from_file_location('forge_ltx_gguf', source,
                submodule_search_locations=[str(source.parent)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return torch, comfy, module.NODE_CLASS_MAPPINGS


def unload(comfy, torch):
    comfy.model_management.unload_all_models()
    gc.collect()
    comfy.model_management.soft_empty_cache()
    torch.cuda.empty_cache()


def read_audio(path, seconds, torch):
    import numpy as np
    command = [ffmpeg_exe(), '-v', 'error', '-i', path, '-t', str(seconds), '-af', 'apad',
               '-ar', '44100', '-ac', '2', '-f', 'f32le', '-']
    result = subprocess.run(command, capture_output=True, check=True,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    values = np.frombuffer(result.stdout, dtype=np.float32).copy().reshape(-1, 2).T
    return {'waveform': torch.from_numpy(values)[None], 'sample_rate': 44100}


def mux_audio(path, waveform, sample_rate, frames, fps):
    """Keep native/source audio synchronized when FILM changes the output frame rate."""
    import numpy as np
    destination = Path(path)
    temporary = destination.with_name(destination.stem + '.audio.mp4')
    audio = np.asarray(waveform, dtype=np.float32)
    if audio.ndim == 3:
        audio = audio[0]
    if audio.ndim == 1:
        audio = audio[None]
    command = [ffmpeg_exe(), '-y', '-v', 'error', '-i', str(destination), '-f', 'f32le',
        '-ar', str(sample_rate), '-ac', str(audio.shape[0]), '-i', '-', '-map', '0:v:0', '-map', '1:a:0',
        '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k', '-af', f'apad,atrim=duration={frames / fps:.8f}',
        '-map_metadata', '0', '-movflags', '+faststart', str(temporary)]
    try:
        subprocess.run(command, input=audio.T.copy().tobytes(), capture_output=True, check=True,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


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
    torch, comfy, loaders = backend(job)
    if not torch.cuda.is_available():
        raise RuntimeError('LTX requires a CUDA GPU in this worker.')

    def watch_cancel():
        while not stopped.wait(0.2):
            if cancel_file and cancel_file.exists():
                comfy.model_management.interrupt_current_processing(True)
                return

    thread = threading.Thread(target=watch_cancel, daemon=True)
    thread.start()
    try:
        with torch.inference_mode():
            return run(job, torch, comfy, loaders, report)
    except comfy.model_management.InterruptProcessingException:
        raise Cancelled()
    except Exception:
        check()
        raise
    finally:
        stopped.set()
        comfy.model_management.interrupt_current_processing(False)


def run(job, torch, comfy, loaders, report):
    import numpy as np
    from PIL import Image, ImageOps
    from comfy_extras import nodes_lt as video_nodes
    from comfy_extras import nodes_lt_audio as audio_nodes
    from comfy_extras.nodes_custom_sampler import SamplerCustomAdvanced, RandomNoise, CFGGuider, KSamplerSelect
    import nodes

    parts = job.get('parts') or [dict(start_image=job.get('start_image'), end_image=job.get('end_image'),
                                     prompt=job['prompt'])]
    if len(parts) > 5:
        raise ValueError('LTX supports at most six pictures per job.')
    base_report, last_overall, index = report, [0.0], 0

    def report(stage, progress=None, **extra):
        p = float(progress or 0)
        if stage == 'text':
            overall = 0.08 * p
        elif stage in ('condition', 'loading'):
            overall = 0.08 + 0.8 * (index + (0.05 if stage == 'loading' else 0)) / len(parts)
        elif stage == 'sampling':
            overall = 0.08 + 0.8 * p
        elif stage == 'decode':
            overall = 0.08 + 0.8 * (index + 0.95) / len(parts)
        elif stage == 'interpolate':
            overall = 0.9 + 0.04 * p
        elif stage == 'upscale':
            overall = 0.94 + 0.04 * p
        elif stage == 'writing':
            overall = 0.98 + 0.019 * p
        else:
            overall = 1.0 if stage == 'done' else last_overall[0]
        last_overall[0] = max(last_overall[0], overall)
        base_report(stage, progress, overall_progress=last_overall[0], **extra)
    prompts = [p.get('prompt') or job['prompt'] for p in parts]
    cache_key = hashlib.sha256(json.dumps([job['text_encoder'], job['connectors'], prompts,
        job.get('negative', '')], ensure_ascii=False).encode()).hexdigest()
    cache = Path(job['cache_dir']) / 'ltx' / (cache_key + '.pt')
    conditions = None
    if cache.is_file():
        try:
            conditions = torch.load(cache, map_location='cpu', weights_only=True)
        except (OSError, ValueError, RuntimeError):
            conditions = None
    report('text', 0, note='Gemma 3 text encoder' if conditions is None else 'cached LTX prompt')
    if conditions is None:
        clip_loader = loaders['DualCLIPLoaderGGUF']()
        paths = [job['text_encoder'], job['connectors']]
        clip = clip_loader.load_patcher(paths, comfy.sd.CLIPType.LTXV, clip_loader.load_data(paths))
        # Comfy's --lowvram defaults the text encoder to CPU. This GGUF patcher
        # streams quantized layers, so encode on CUDA and offload between layers.
        clip.patcher.load_device = torch.device('cuda')
        conditions = []
        encoded_prompts = {}
        for index, prompt in enumerate(prompts + [job.get('negative', '')]):
            if prompt not in encoded_prompts:
                encoded_prompts[prompt] = clip.encode_from_tokens_scheduled(clip.tokenize(prompt))
            conditions.append(encoded_prompts[prompt])
            report('text', (index + 1) / (len(prompts) + 1))
        unload(comfy, torch)
        del clip, clip_loader, encoded_prompts
        gc.collect()
        cache.parent.mkdir(parents=True, exist_ok=True)
        torch.save(conditions, cache)
    report('text', 1)

    def load_vae(path, dtype=None):
        state, metadata = comfy.utils.load_torch_file(path, return_metadata=True)
        vae = comfy.sd.VAE(sd=state, metadata=metadata, dtype=dtype)
        vae.throw_exception_if_invalid()
        return vae

    vae = load_vae(job['vae'])
    # Audio preprocessing produces float32 mel features. Its small encoder must
    # stay float32; the video VAE still uses BF16 and spatial/temporal tiling.
    audio_vae = load_vae(job['audio_vae'], torch.float32)
    audio_source = None
    if job.get('source_audio'):
        audio_source = read_audio(job['source_audio'], (job['frames'] - 1) / job['fps'], torch)
    pieces, sounds = [], []
    model = None
    for index, part in enumerate(parts):
        report('condition', 0, note=f'part {index + 1} of {len(parts)}')
        width, height, frames = job['width'], job['height'], job['frames']
        positive, negative = video_nodes.LTXVConditioning.execute(conditions[index], conditions[-1], job['fps']).result
        latent = video_nodes.EmptyLTXVLatentVideo.execute(width, height, frames, 1).result[0]

        def pixels(path):
            with Image.open(path) as source:
                image = ImageOps.fit(source.convert('RGB'), (width, height), method=Image.Resampling.LANCZOS)
                return torch.from_numpy(np.asarray(image).astype(np.float32) / 255)[None]

        if job.get('init_video'):
            video_path = job['init_video']
            command = [ffmpeg_exe(), '-v', 'error', '-i', video_path, '-vf',
                f'fps={job["fps"]},scale={width}:{height}', '-frames:v', str(frames), '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-']
            data = subprocess.run(command, capture_output=True, check=True).stdout
            values = np.frombuffer(data, dtype=np.uint8).reshape(-1, height, width, 3).copy()
            if not len(values):
                raise ValueError('The source video has no decodable frames.')
            if len(values) < frames:
                values = np.concatenate([values, np.repeat(values[-1:], frames - len(values), axis=0)])
            latent = nodes.VAEEncodeTiled().encode(vae, torch.from_numpy(values).float() / 255,
                                                   256, 64, 32, 8)[0]
        elif part.get('end_image'):
            for path, frame_idx in [(part.get('start_image'), 0), (part['end_image'], -1)]:
                if path:
                    positive, negative, latent = video_nodes.LTXVAddGuide.execute(
                        positive, negative, vae, latent, pixels(path), frame_idx, 1.0).result
        elif part.get('start_image'):
            latent = video_nodes.LTXVImgToVideoInplace.execute(vae, pixels(part['start_image']), latent, 1.0).result[0]

        if audio_source:
            audio_latent = audio_nodes.LTXVAudioVAEEncode.execute(audio_source, audio_vae).result[0]
            audio_latent['noise_mask'] = torch.zeros_like(audio_latent['samples'])
        else:
            audio_latent = audio_nodes.LTXVEmptyLatentAudio.execute(frames, job['fps'], 1, audio_vae).result[0]
        latent = video_nodes.LTXVConcatAVLatent.execute(latent, audio_latent).result[0]
        unload(comfy, torch)
        if model is None:
            report('loading', 0, note='LTX 2.3 GGUF with CPU offload')
            model = loaders['UnetLoaderGGUFAdvanced']().load_unet(Path(job['model']).name,
                      dequant_dtype='bfloat16', patch_dtype='bfloat16', patch_on_device=False)[0]
            for item in job.get('loras', []):
                if item.get('family') != 'ltx':
                    raise ValueError('A Wan LoRA cannot be loaded into LTX.')
                weights = comfy.utils.load_torch_file(item['path'], safe_load=True)
                before = sum(map(len, model.patches.values()))
                model, _ = comfy.sd.load_lora_for_models(model, None, weights, item['strength'], 0)
                if sum(map(len, model.patches.values())) <= before:
                    raise ValueError('No compatible LTX layers in LoRA: ' + Path(item['path']).name)
                del weights
        sigmas = torch.tensor(job['sigmas'], dtype=torch.float32)
        if job.get('init_video'):
            sigmas = sigmas[int(job.get('denoise_start_step', 5)):]
        noise = RandomNoise.execute(int(job['seed']) + index).result[0]
        guider = CFGGuider.execute(model, positive, negative, 1.0).result[0]
        sampler = KSamplerSelect.execute(job.get('sampler', 'euler_ancestral')).result[0]
        def hook(value, total, preview=None, **kwargs):
            image_path = None
            if preview is not None and len(preview) > 1 and hasattr(preview[1], 'save'):
                target = Path(job.get('work_dir') or job['output_dir']) / 'ltx-preview.png'
                temporary = target.with_suffix('.tmp.png')
                preview[1].save(temporary)
                os.replace(temporary, target)
                image_path = str(target)
            report('sampling', (index + value / max(total, 1)) / len(parts),
                   step=int(value), steps=int(total), note=f'part {index + 1} of {len(parts)}',
                   preview=image_path, preview_key=index * 8 + int(value))
        previous_hook = comfy.utils.PROGRESS_BAR_HOOK
        comfy.utils.set_progress_bar_global_hook(hook)
        report('sampling', index / len(parts), note=f'part {index + 1} of {len(parts)}')
        try:
            sampled = SamplerCustomAdvanced.execute(noise, guider, sampler, sigmas, latent).result[0]
        finally:
            comfy.utils.set_progress_bar_global_hook(previous_hook)
        del guider, latent
        unload(comfy, torch)
        video_latent, generated_audio = video_nodes.LTXVSeparateAVLatent.execute(sampled).result
        _, _, video_latent = video_nodes.LTXVCropGuides.execute(positive, negative, video_latent).result
        report('decode', index / len(parts))
        images = nodes.VAEDecodeTiled().decode(vae, video_latent, 256, 64, 32, 8)[0]
        if not torch.isfinite(images).all() or not len(images):
            raise RuntimeError('LTX did not produce valid video frames.')
        array = images.float().clamp(0, 1).mul(255).round().byte().cpu().numpy()
        decoded_preview = Path(job.get('work_dir') or job['output_dir']) / 'ltx-decoded-preview.png'
        Image.fromarray(array[len(array) // 2]).save(decoded_preview)
        report('decode', (index + 1) / len(parts), preview=str(decoded_preview), preview_key=100 + index)
        piece = array[:job.get('requested_frames', frames)]
        pieces.append(piece if index == 0 else piece[1:])
        if job.get('native_audio', True) and not audio_source:
            decoded = audio_nodes.LTXVAudioVAEDecode.execute(generated_audio, audio_vae).result[0]
            waveform = decoded['waveform'].float().cpu().numpy()
            rate = decoded['sample_rate']
            # Endpoints overlap by one video frame. Audio has continuous duration
            # (frames - 1) / fps per part: join it without a gap or an extra trim.
            samples = round((len(piece) - 1) / job['fps'] * rate)
            waveform = waveform[..., :samples]
            if waveform.shape[-1] < samples:
                waveform = np.pad(waveform, [(0, 0)] * (waveform.ndim - 1) + [(0, samples - waveform.shape[-1])])
            sounds.append((waveform, rate))
        del sampled, video_latent, generated_audio, images
        unload(comfy, torch)
    del model, vae, audio_vae, conditions
    unload(comfy, torch)
    frames = np.concatenate(pieces)
    base_frames, base_fps = len(frames), job['fps']
    frames = finish(job, frames, torch.device('cuda'), report)
    job['frames'] = len(frames)
    result = write_outputs(job, frames, report)
    if audio_source or sounds:
        report('writing', 0.9, note='saving synchronized audio')
        waveform = audio_source['waveform'].numpy() if audio_source else np.concatenate([x[0] for x in sounds], axis=-1)
        rate = audio_source['sample_rate'] if audio_source else sounds[0][1]
        mux_audio(result['video'], waveform, rate, result['frames'], result['fps'])
    meta_path = Path(result['video']).with_suffix('.json')
    meta = json.loads(meta_path.read_text(encoding='utf-8'))
    meta.update(backend='ltx', native_audio=bool(audio_source or sounds), source_audio=job.get('source_audio'),
                base_frames=base_frames, base_fps=base_fps,
                model=Path(job['model']).name, comfy_runtime_revision='8a98ecf5864433064809ac1f2b52ccbf8b444471',
                peak_vram_gb=round(torch.cuda.max_memory_allocated() / 2**30, 3))
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')
    report('done', 1, **result)
    return result

"""MiniMax-H3 FL2V assets and the fixed 8 GB workflow contract."""
import json
import math
import os
from pathlib import Path

from . import HOME_DIR, MODELS_DIR, WEBUI_DIR

COMFY_REVISION = '8a98ecf5864433064809ac1f2b52ccbf8b444471'
CLIPPROJ_REVISION = 'c01ba8fb8f41b4f2094dbd0b185cdc238fb6134c'
SPECTRUM_REVISION = '7df883bb2361b7884ad789e97effd1dc37c8963a'
WORKFLOW_REVISION = 'ea4ff39a99eb9b599ed361f874231d5fb33ad691'
H3_HOME = Path(HOME_DIR) / 'h3'


def asset(repo, revision, filename, size, sha256):
    return dict(path='h3/' + filename, size=size, sha256=sha256,
                url=f'https://huggingface.co/{repo}/resolve/{revision}/{filename}')


ASSETS = {
    'h3_model': asset('starsfriday/MiniMax-H3-w4a8', '2a5ad5871296af3506fda7c5edd571612253cedb',
        'minimax_h3_fl2va_pruned_w4a8_mixed.safetensors', 12540858008,
        '29d0a0e4be82dacdc4a5849bf756c2b32d34131f7a18f9aac754e16a2e215eb5'),
    'h3_text': asset('Stick9190/qwen3vl_4b_int8_convrot', 'd9449494156acb47a84c68777d78bd8d9d5df97d',
        'qwen3vl_4b_int8_convrot.safetensors', 5374169456,
        'ebbb3efdf4a6e1b766e32f6dc4fa92af9530edede0968c070b3e604aeca413aa'),
    'h3_vae': asset('Kijai/MiniMax-H3-experimental', 'c2e7b89b1800123c49a0f620a1932c01d8952256',
        'minimax_h3_video_vae_int8_convrot.safetensors', 3171670912,
        '9bb2d96f218c76babd85e0611b85ca8fb330a90546c01a0005e8a58a59593410'),
    'h3_projection': asset('NicoLab28/ClipProj-MiniMax-H3', '2ebdbcdc27a29a9607efdb221a9afcb9a0cdd808',
        'mmh3-4b-ClipProj-v3-mlp.safetensors', 503434368,
        'feef06ef3b9aede3b1f3331b71eebbc873e21a867d73bcf40ea2c0b007270693'),
}

PRESETS = {
    'MiniMax-H3 FL2V · W4A8 · 8 GB': dict(
        family='h3', model='h3_model', text_encoder='h3_text', vae='h3_vae',
        projection='h3_projection', steps=20, shift=12.0, cfg=1.0),
}
DEFAULT_PRESET = next(iter(PRESETS))
QUALITY = {
    # the area: the Shape decides which way round (608x352 landscape, 352x608 portrait, ...)
    'MiniMax-H3 · fast (about 350p, the workflow default)': 608 * 352,
    'MiniMax-H3 · sharper (about 480p, tested on 8 GB)': 480 * 864,
}
DEFAULT_QUALITY = next(iter(QUALITY))


def is_h3(preset):
    return preset in PRESETS


def asset_keys(_preset):
    return ['h3_model', 'h3_text', 'h3_vae', 'h3_projection']


def path(key):
    return str(Path(MODELS_DIR) / ASSETS[key]['path'])


def runtime_paths():
    comfy = Path(WEBUI_DIR) / 'fibo2/runtime/ComfyUI'
    custom = H3_HOME / 'runtime'
    return comfy, custom


def python_executable():
    relative = 'Scripts/python.exe' if os.name == 'nt' else 'bin/python'
    return Path(WEBUI_DIR) / 'fibo2/env' / relative


def runtime_ready():
    comfy, custom = runtime_paths()
    try:
        ready = json.loads((H3_HOME / 'runtime-ready.json').read_text(encoding='utf-8'))
        fibo = json.loads((Path(WEBUI_DIR) / 'fibo2/runtime-ready.json').read_text(encoding='utf-8'))
    except (OSError, ValueError, AttributeError):
        return False
    return (ready.get('comfy_revision') == COMFY_REVISION
            and ready.get('clipproj_revision') == CLIPPROJ_REVISION
            and ready.get('spectrum_revision') == SPECTRUM_REVISION
            and fibo.get('comfy_revision') == COMFY_REVISION
            and python_executable().is_file()
            and (comfy / 'comfy_extras/nodes_minimax_h3.py').is_file()
            and (custom / 'ComfyUI-ClipProj/clipproj_nodes.py').is_file()
            and (custom / 'ComfyUI-Spectrum-MiniMax-H3/comfyui_spectrum_h3/nodes.py').is_file())


def frames_for(seconds):
    seconds = float(seconds)
    if not math.isfinite(seconds) or not 0.2 <= seconds <= 15.0:
        raise ValueError('MiniMax-H3 length must be between 0.2 and 15 seconds per part.')
    frames = max(5, round(seconds * 24))
    return frames + (5 - frames % 17) % 17


def adapt_job(job, preset, quality, seconds):
    """Replace the Wan contract with the linked FL2V workflow's fixed schedule."""
    if job.get('loras'):
        raise ValueError('Wan and LTX LoRAs cannot be loaded into MiniMax-H3. Clear the LoRA slots and tags first.')
    width, height = int(job['width']), int(job['height'])
    width, height = max(32, round(width / 32) * 32), max(32, round(height / 32) * 32)
    if width * height > max(QUALITY.values()):
        raise ValueError('The 8 GB MiniMax-H3 profile supports up to the tested 480x864 pixel area.')
    pr = PRESETS[preset]
    comfy, custom = runtime_paths()
    result = dict(job, backend='h3', model=path(pr['model']), text_encoder=path(pr['text_encoder']),
        vae=path(pr['vae']), projection=path(pr['projection']), width=width, height=height,
        frames=frames_for(seconds), fps=24, steps=20, cfg=1.0, shift=12.0, audio_shift=3.0,
        sampler='euler', scheduler='simple', denoise=1.0, attention='comfy kitchen attention',
        spectrum=True, comfy_runtime=str(comfy), h3_custom_runtime=str(custom), native_audio=False,
        workflow_revision=WORKFLOW_REVISION)
    result.pop('experts', None)
    result['negative'] = ''
    return result

"""LTX-2.3 assets and job contracts. Inference stays outside Forge's process."""
import math
import os
import json
from pathlib import Path

from . import HOME_DIR, MODELS_DIR, WEBUI_DIR

REVISION = '96e8ed4925ead3db9ff4d0084f165ef6a74f28d0'
GEMMA_REVISION = '858acec7ec0541a46c39985c95d3b52d8f3ab183'
COMFY_REVISION = '8a98ecf5864433064809ac1f2b52ccbf8b444471'
GGUF_REVISION = '6ea2651e7df66d7585f6ffee804b20e92fb38b8a'
LTX_HOME = Path(HOME_DIR) / 'ltx'
SIGMAS = [1.0, 0.99375, 0.9875, 0.98125, 0.975, 0.909375, 0.725, 0.421875, 0.0]


def asset(repo, revision, filename, size, sha256):
    return dict(path='ltx/' + filename, size=size, sha256=sha256,
                url=f'https://huggingface.co/{repo}/resolve/{revision}/{filename}')


ASSETS = {
    'ltx_q2': asset('unsloth/LTX-2.3-GGUF', REVISION, 'distilled-1.1/ltx-2.3-22b-distilled-1.1-Q2_K.gguf',
        7935826976, '3066a3e36232423670a4613c5ea00843eb6ed9985ea150bba628863cef685b80'),
    'ltx_q3': asset('unsloth/LTX-2.3-GGUF', REVISION, 'distilled-1.1/ltx-2.3-22b-distilled-1.1-Q3_K_S.gguf',
        9741626400, '4449208e0613d4a65cf5aa11ae7ae04fa417ad54489a273a283cf15fd088a146'),
    'ltx_q4': asset('unsloth/LTX-2.3-GGUF', REVISION, 'distilled-1.1/ltx-2.3-22b-distilled-1.1-Q4_K_S.gguf',
        12961578016, 'f9e2b72221a9b9bc0a3ade678ddda102b14af7a4f83476b7319e3ec38423cd80'),
    'ltx_text': asset('unsloth/gemma-3-12b-it-qat-GGUF', GEMMA_REVISION, 'gemma-3-12b-it-qat-Q4_K_M.gguf',
        7300778368, '5be55c72dac2e6ccaf8140bb6e793ad1c9d39f54fc4f3ccde54f2412e75cc830'),
    'ltx_connectors': asset('unsloth/LTX-2.3-GGUF', REVISION, 'text_encoders/ltx-2.3-22b-distilled_embeddings_connectors.safetensors',
        2312144712, 'c61cbb396e2a8175d8b2da51f0fdac885a4ccd22c9f64dafa5aa2c455dc8a507'),
    'ltx_video_vae': asset('unsloth/LTX-2.3-GGUF', REVISION, 'vae/ltx-2.3-22b-distilled_video_vae.safetensors',
        1452256522, 'e68d6d8f8a42942ac9b862cc315beb3bc30805a8876c7ad63ba5bf7a2b8e168a'),
    'ltx_audio_vae': asset('unsloth/LTX-2.3-GGUF', REVISION, 'vae/ltx-2.3-22b-distilled_audio_vae.safetensors',
        364853140, '3cd6a6eb8cb28f5ecc12f1f3126952b2a3d2b0b42ad3270e63cefafafe0d9b57'),
}
COMMON = ['ltx_text', 'ltx_connectors', 'ltx_video_vae', 'ltx_audio_vae']
PRESETS = {
    'LTX 2.3 Distilled 1.1 · Q3 · recommended for 8 GB': dict(family='ltx', model='ltx_q3'),
    'LTX 2.3 Distilled 1.1 · Q2 · smaller for 6 GB': dict(family='ltx', model='ltx_q2'),
    'LTX 2.3 Distilled 1.1 · Q4 · quality for 12 GB+': dict(family='ltx', model='ltx_q4'),
}
for _preset in PRESETS.values():
    _preset.update(text_encoder='ltx_text', vae='ltx_video_vae', steps=8, shift=2.0, cfg=1.0)
DEFAULT_PRESET = next(iter(PRESETS))
QUALITY = {'LTX 288p · quick draft': 288 * 512, 'LTX 416p · balanced for 8 GB': 416 * 704,
           'LTX 480p · more detail': 480 * 832, 'LTX 576p · more memory': 576 * 1024}
DEFAULT_QUALITY = list(QUALITY)[1]


def default_quality(preset):
    key = PRESETS[preset]['model']
    return list(QUALITY)[{'ltx_q2': 0, 'ltx_q3': 1, 'ltx_q4': 2}[key]]


def is_ltx(preset):
    return preset in PRESETS


def loras():
    roots = [Path(MODELS_DIR) / 'ltx/loras', Path(WEBUI_DIR) / 'models/Lora/ltx']
    return sorted({str(p) for root in roots for p in root.rglob('*.safetensors')}, key=str.casefold)


def asset_keys(preset):
    return [PRESETS[preset]['model']] + COMMON


def path(key):
    return str(Path(MODELS_DIR) / ASSETS[key]['path'])


def runtime_paths():
    own = LTX_HOME / 'runtime' / 'ComfyUI'
    shared = Path(WEBUI_DIR) / 'fibo2' / 'runtime' / 'ComfyUI'
    try:
        shared_revision = json.loads((Path(WEBUI_DIR) / 'fibo2/runtime-ready.json').read_text(encoding='utf-8')).get('comfy_revision')
    except (OSError, ValueError, AttributeError):
        shared_revision = None
    comfy = own if own.is_dir() or shared_revision != COMFY_REVISION else shared
    return comfy, LTX_HOME / 'runtime' / 'ComfyUI-GGUF'


def python_executable():
    relative = 'Scripts/python.exe' if os.name == 'nt' else 'bin/python'
    return LTX_HOME / 'env' / relative


def runtime_ready():
    comfy, gguf = runtime_paths()
    try:
        ready = json.loads((LTX_HOME / 'runtime-ready.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return False
    return (isinstance(ready, dict) and ready.get('comfy_revision') == COMFY_REVISION
            and ready.get('gguf_revision') == GGUF_REVISION and python_executable().is_file()
            and (comfy / 'comfy_extras/nodes_lt_audio.py').is_file() and (gguf / 'nodes.py').is_file())


def frames_for(seconds, fps=24):
    if not math.isfinite(float(seconds)) or not 0 < float(seconds) <= 8:
        raise ValueError('LTX length must be between 0 and 8 seconds per part.')
    return 1 + 8 * max(1, round(float(seconds) * fps / 8))


def adapt_job(job, preset, quality, seconds):
    """Use the distilled schedule and model family; never pass Wan experts or LoRAs."""
    if any(item.get('family') != 'ltx' for item in job.get('loras', [])):
        raise ValueError('Wan LoRAs cannot be used with LTX. Clear the LoRA slots and tags first.')
    w, h = int(job['width']), int(job['height'])
    w, h = max(64, round(w / 32) * 32), max(64, round(h / 32) * 32)
    if w * h > 576 * 1024:
        raise ValueError('LTX on this profile supports up to 576p. Start at 416p for an 8 GB card.')
    model = PRESETS[preset]['model']
    comfy, gguf = runtime_paths()
    result = dict(job, backend='ltx', model=path(model), width=w, height=h, fps=24,
        frames=frames_for(seconds), steps=8, cfg=1.0, shift=2.0, sampler='euler_ancestral',
        sigmas=SIGMAS, connectors=path('ltx_connectors'), audio_vae=path('ltx_audio_vae'),
        comfy_runtime=str(comfy), gguf_runtime=str(gguf), native_audio=True)
    result.pop('experts', None)
    return result

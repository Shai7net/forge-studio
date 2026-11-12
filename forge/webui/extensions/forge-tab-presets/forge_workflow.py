"""Image-workflow helpers owned by this extension, without changes to Forge or ControlNet."""
import copy
import functools
import json
import math
from pathlib import Path
import re
import struct
import sys
import threading

QR = 'QR-CODE'
INVERT = 'invert (from white bg & black line)'
LORA = re.compile(r'<lora:([^:>]+)(?::[^>]*)?>', re.I)
_write_lock = threading.RLock()
_headers = {}


def title_family(title):
    text = str(title or '').lower()
    if 'fibo' in text:
        return 'fibo'
    if 'flux' in text:
        return 'flux'
    if 'sdxl' in text or 'sd_xl' in text or re.search(r'\bxl\b|[a-z]xl(?:_|\b)', text):
        return 'sdxl'
    return 'sd1'


def file_family(filename, metadata=None):
    metadata = metadata or {}
    description = ' '.join(str(metadata.get(k, '')) for k in
                           ('modelspec.architecture', 'ss_base_model_version', 'ss_sd_model_name'))
    hint = title_family(description + ' ' + Path(filename).name)
    if hint in ('fibo', 'flux', 'sdxl'):
        return hint
    path = Path(filename)
    if path.suffix.lower() != '.safetensors' or not path.is_file():
        return hint
    key = (str(path), path.stat().st_size, path.stat().st_mtime_ns)
    if key in _headers:
        return _headers[key]
    try:
        with path.open('rb') as source:
            length = struct.unpack('<Q', source.read(8))[0]
            if length > 32 << 20:
                return hint
            header = json.loads(source.read(length))
        keys = list(header)
        if any('double_blocks' in k or 'double_stream_blocks' in k for k in keys):
            hint = 'flux'
        elif any(k.startswith('conditioner.embedders.1.') for k in keys):
            hint = 'sdxl'
        else:
            for name, value in header.items():
                if 'attn2' in name and ('to_k' in name or 'k_proj' in name) and isinstance(value, dict):
                    shape = value.get('shape', [])
                    if len(shape) == 2:
                        hint = {768: 'sd1', 1024: 'sd2', 2048: 'sdxl'}.get(shape[-1], hint)
                        break
    except (OSError, ValueError, struct.error):
        pass
    _headers[key] = hint
    return hint


def checkpoint_family(title):
    from modules import sd_models
    info = sd_models.checkpoints_list.get(title) or sd_models.checkpoint_aliases.get(title)
    if info is None:
        return title_family(title)
    for module_name, check, family in [('fibo2.glue', 'is_fibo_info', 'fibo'), ('flux3.glue', 'is_flux3_info', 'flux')]:
        module = sys.modules.get(module_name)
        if module and getattr(module, check)(info):
            return family
    return file_family(info.filename, getattr(info, 'metadata', {}))


def lora_rows():
    import networks
    from modules import extra_networks, shared
    result = []
    for name, item in list(networks.available_networks.items()):
        user = extra_networks.get_user_metadata(item.filename) or {}
        version = user.get('sd version') or getattr(getattr(item, 'sd_version', None), 'name', '')
        family = {'SD1': 'sd1', 'SD2': 'sd2', 'SDXL': 'sdxl'}.get(version)
        family = family or file_family(item.filename, getattr(item, 'metadata', {}))
        weight = user.get('preferred weight') or shared.opts.data.get('extra_networks_default_multiplier', 1)
        result.append(dict(name=name, alias=item.get_alias(), family=family, weight=float(weight),
                           activation=user.get('activation text') or '', negative=user.get('negative text') or ''))
    return result


def save_lora_style(lora, name='', weight=1.0, overwrite=False):
    from fastapi import HTTPException
    from modules import shared, styles
    row = next((r for r in lora_rows() if r['name'] == lora), None)
    if row is None:
        raise HTTPException(404, 'The LoRA is no longer installed. Refresh the LoRA list.')
    name = str(name or row['name']).strip()
    if not name or len(name) > 150 or any(ord(c) < 32 for c in name):
        raise HTTPException(400, 'Enter a style name of 1 to 150 characters.')
    if not math.isfinite(float(weight)) or not -4 <= float(weight) <= 4:
        raise HTTPException(400, 'LoRA weight must be between -4 and 4.')
    if any(c in row['alias'] for c in '<>:'):
        raise HTTPException(400, 'This LoRA alias contains prompt delimiters. Change its alias first.')
    prompt = f"<lora:{row['alias']}:{float(weight):g}>"
    if row['activation']:
        prompt += ' ' + row['activation']
    with _write_lock:
        db = shared.prompt_styles
        previous = db.styles.get(name)
        if previous and (previous.prompt != prompt or previous.negative_prompt != row['negative']) and not overwrite:
            raise HTTPException(409, 'A different style already has this name. Choose another name or confirm replacement.')
        db.styles[name] = styles.PromptStyle(name, prompt, row['negative'], str(db.default_path))
        try:
            db.save_styles()
        except Exception:
            if previous is None:
                db.styles.pop(name, None)
            else:
                db.styles[name] = previous
            raise
    return dict(name=name, styles=list(db.styles))


def catalog():
    from modules import shared
    return dict(loras=lora_rows(), styles={name: {'prompt': row.prompt or '', 'negative': row.negative_prompt or ''}
                                         for name, row in shared.prompt_styles.styles.items()})


def install_qr():
    """Add one Control Type and a disjoint callback; keep ControlNet's original UI and inference."""
    try:
        import gradio as gr
        from lib_controlnet import global_state
        from lib_controlnet.controlnet_ui.controlnet_ui_group import ControlNetUiGroup
    except ImportError:
        return
    if getattr(global_state, '_forge_qr_installed', False):
        return
    global_state._forge_qr_installed = True
    original_tags = global_state.get_all_preprocessor_tags
    original_pre = global_state.get_filtered_preprocessor_names
    original_models = global_state.get_filtered_controlnet_names
    global_state.get_all_preprocessor_tags = lambda: list(dict.fromkeys(original_tags() + [QR]))
    global_state.get_filtered_preprocessor_names = lambda tag: ['None', INVERT] if tag == QR else original_pre(tag)

    def models(tag):
        if tag != QR:
            return original_models(tag)
        names = [n for n in global_state.get_all_controlnet_names() if 'qrcodemonster' in re.sub(r'[^a-z0-9]', '', n.lower())]
        names.sort(key=lambda n: ('5e5778cb' not in n.lower(), 'v20' not in n.lower(), n.lower()))
        return ['None'] + names
    global_state.get_filtered_controlnet_names = models
    original = ControlNetUiGroup.register_build_sliders

    @functools.wraps(original)
    def register(group):
        original(group)
        def defaults(tag):
            return [gr.update(value=x) for x in (0.8, 0.24, 0.79)] if tag == QR else [gr.update()] * 3
        group.type_filter.change(defaults, inputs=[group.type_filter],
                                 outputs=[group.weight, group.guidance_start, group.guidance_end], show_progress=False)
    ControlNetUiGroup.register_build_sliders = register


def compatible(lora_family, family):
    return lora_family == family or (family.startswith('sd') and not lora_family)


def pause_scripts(p):
    """Copy processing arguments, never mutate the UI's saved ControlNet/AnimateDiff units."""
    runner = getattr(p, 'scripts', None)
    if not runner:
        return
    args = list(getattr(p, 'script_args', []) or [])
    for script in runner.alwayson_scripts:
        title = script.title().lower()
        if 'controlnet' not in title and 'animatediff' not in title:
            continue
        for index in range(script.args_from, min(script.args_to, len(args))):
            value = args[index]
            flag = 'enabled' if hasattr(value, 'enabled') else 'enable' if hasattr(value, 'enable') else None
            if flag:
                value = copy.copy(value)
                setattr(value, flag, False)
                args[index] = value
            elif isinstance(value, dict) and ('enabled' in value or 'enable' in value):
                args[index] = dict(value, **{k: False for k in ('enabled', 'enable') if k in value})
    p.script_args = args


def guard_processing(p):
    from modules import shared
    family = checkpoint_family(shared.opts.sd_model_checkpoint)
    rows = lora_rows()
    by_alias = {str(r[k]).lower(): r['family'] for r in rows for k in ('name', 'alias')}
    def clean(text):
        return LORA.sub(lambda m: m.group(0) if compatible(by_alias.get(m.group(1).lower()), family) else '', text or '')
    selected = []
    for name in getattr(p, 'styles', []) or []:
        style = shared.prompt_styles.styles.get(name)
        tags = LORA.findall((getattr(style, 'prompt', '') or '') + ' ' + (getattr(style, 'negative_prompt', '') or ''))
        if all(compatible(by_alias.get(tag.lower()), family) for tag in tags):
            selected.append(name)
    p.styles = selected
    for key in ('prompt', 'negative_prompt'):
        value = getattr(p, key, '')
        setattr(p, key, [clean(x) for x in value] if isinstance(value, list) else clean(value))
    if family in ('flux', 'fibo'):
        pause_scripts(p)
    return family

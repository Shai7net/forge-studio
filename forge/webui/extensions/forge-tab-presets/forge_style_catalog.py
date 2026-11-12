"""Curated styles in Forge's shared selector, rendered for each model's prompt format."""
from functools import lru_cache
import json
from pathlib import Path
import sys

MARKER = 'do_not_save'
CATALOG = Path(__file__).with_name('universal_styles.json')


@lru_cache(maxsize=1)
def rows():
    data = json.loads(CATALOG.read_text(encoding='utf-8'))
    if len({r['name'] for r in data}) != len(data):
        raise ValueError('Universal style names must be unique')
    return {r['name']: r for r in data}


def generic(row):
    return ', '.join(x for x in (row.get('medium'), row['treatment']) if x)


def append(text, extra, separator='; '):
    text = text or ''
    return text if not extra or extra in text else separator.join(x for x in (text, extra) if x)


def vgl(prompt, row):
    """Keep subjects, references, quoted lettering and edit_instruction as supplied."""
    value = json.loads(prompt)
    if not isinstance(value, dict):
        raise ValueError('A structured image prompt must be a JSON object')
    if row.get('medium'):
        value['style_medium'] = row['medium']
    previous = value.get('artistic_style', '')
    if isinstance(previous, str):
        value['artistic_style'] = append(previous, row['treatment'])
    else:
        value['short_description'] = append(value.get('short_description', ''), row['treatment'])
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def render(prompt, row, family):
    prompt = prompt or ''
    if family == 'fibo' and prompt.lstrip().startswith('{'):
        return vgl(prompt, row)
    treatment = generic(row)
    if family == 'sd':
        return append(prompt, treatment, ', ')
    text = append(prompt, 'Visual treatment: ' + treatment + '.', '\n')
    if family == 'video':
        text = append(text, 'Maintain this visual treatment across frames while preserving the requested action and camera motion.', '\n')
    return text


def family_for(shared):
    opts = shared.opts
    title = str(getattr(opts, 'sd_model_checkpoint', None) or getattr(opts, 'data', {}).get('sd_model_checkpoint', ''))
    registry = sys.modules.get('modules.sd_models')
    info = getattr(registry, 'checkpoint_aliases', {}).get(title)
    for module, check, family in [('fibo2.glue', 'is_fibo_info', 'fibo'), ('flux3.glue', 'is_flux3_info', 'flux')]:
        loaded = sys.modules.get(module)
        if info is not None and loaded and getattr(loaded, check)(info):
            return family
    title = title.casefold()
    return 'fibo' if 'fibo' in title else 'flux' if 'flux' in title else 'sd'


def hidden(path):
    if not path.is_file():
        return set()
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        return set(value.get('hidden', [])) & rows().keys()
    except (ValueError, TypeError, AttributeError):
        return set()


def managed(db, name):
    style, row = db.styles.get(name), rows().get(name)
    return bool(style and row and style.path == MARKER and style.prompt == generic(row) and not style.negative_prompt)


def install(styles_module, shared, preferences):
    cls = styles_module.StyleDatabase
    if getattr(cls, '_forge_catalog_originals', None):
        populate(shared.prompt_styles, styles_module, preferences)
        return
    originals = {name: getattr(cls, name) for name in ('reload', 'save_styles', 'apply_styles_to_prompt', 'apply_negative_styles_to_prompt')}
    cls._forge_catalog_originals = originals

    def reload(db):
        originals['reload'](db)
        populate(db, styles_module, preferences)

    def positive(db, prompt, styles, family=None):
        family = family or family_for(shared)
        for name in styles or []:
            if managed(db, name):
                prompt = render(prompt, rows()[name], family)
            elif family == 'fibo' and (prompt or '').lstrip().startswith('{'):
                style = db.styles.get(name)
                if style and style.prompt:
                    prompt = vgl(prompt, {'treatment': style.prompt.replace('{prompt}', '').strip(' ,')})
            else:
                prompt = originals['apply_styles_to_prompt'](db, prompt, [name])
        return prompt

    def negative(db, prompt, styles):
        # Curated styles are positive art direction. The user's negatives are preserved.
        return originals['apply_negative_styles_to_prompt'](db, prompt, [n for n in styles or [] if not managed(db, n)])

    def pair(db, prompt, negative_prompt, names, family):
        return positive(db, prompt, names, family), negative(db, negative_prompt, names)

    def save(db, path=None):
        excluded = hidden(preferences)
        excluded.update(n for n in getattr(db, '_forge_catalog_visible', []) if n not in db.styles)
        for name, style in list(db.styles.items()):
            if name in rows() and style.path == MARKER and not managed(db, name):
                # An edited built-in is a personal override, saved in the normal user CSV.
                db.styles[name] = style._replace(path=str(db.default_path))
        if excluded != hidden(preferences):
            preferences.parent.mkdir(parents=True, exist_ok=True)
            temporary = preferences.with_suffix('.tmp')
            temporary.write_text(json.dumps({'hidden': sorted(excluded)}, indent=2), encoding='utf-8')
            temporary.replace(preferences)
        return originals['save_styles'](db, path)

    cls.reload = reload
    cls.save_styles = save
    cls.apply_styles_to_prompt = positive
    cls.apply_negative_styles_to_prompt = negative
    cls.apply_styles_for_model = pair
    populate(shared.prompt_styles, styles_module, preferences)


def populate(db, styles_module, preferences):
    excluded = hidden(preferences)
    for name, row in rows().items():
        if name not in excluded and name not in db.styles:
            db.styles[name] = styles_module.PromptStyle(name, generic(row), '', MARKER)
    db._forge_catalog_visible = [n for n in rows() if n in db.styles]


def uninstall(styles_module, shared):
    cls = styles_module.StyleDatabase
    originals = getattr(cls, '_forge_catalog_originals', None)
    if not originals:
        return
    for name in list(shared.prompt_styles.styles):
        if managed(shared.prompt_styles, name):
            del shared.prompt_styles.styles[name]
    for name, method in originals.items():
        setattr(cls, name, method)
    del cls._forge_catalog_originals
    del cls.apply_styles_for_model

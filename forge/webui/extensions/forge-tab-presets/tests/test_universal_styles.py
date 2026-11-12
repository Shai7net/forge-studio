"""Exercise the actual Forge style database without loading checkpoints or Torch."""
import csv
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

EXTENSION = Path(__file__).resolve().parents[1]
WEBUI = EXTENSION.parents[1]
sys.path.insert(0, str(EXTENSION))
import forge_style_catalog as catalog


def load_native_styles():
    modules = ModuleType('modules')
    modules.errors = SimpleNamespace(report=lambda *a, **k: None)
    spec = importlib.util.spec_from_file_location('native_styles_for_catalog_test', WEBUI / 'modules/styles.py')
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {'modules': modules}):
        spec.loader.exec_module(module)
    return module


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'styles.csv'
        self.path.write_text('name,prompt,negative_prompt\nPersonal,"before {prompt} after",blur\nLegacy,<lora:personal:1>,bad\n', encoding='utf-8')
        self.original = self.path.read_bytes()
        self.module = load_native_styles()
        self.db = self.module.StyleDatabase([self.path])
        self.shared = SimpleNamespace(prompt_styles=self.db, opts=SimpleNamespace(sd_model_checkpoint='SDXL'))
        self.preferences = self.root / 'private' / '_universal_style_hidden.json'
        catalog.install(self.module, self.shared, self.preferences)
        self.addCleanup(catalog.uninstall, self.module, self.shared)

    def name(self, suffix):
        return next(n for n in catalog.rows() if n.endswith(' · ' + suffix))

    def test_catalog_is_distinct_portable_and_grouped(self):
        rows = list(catalog.rows().values())
        self.assertEqual(len(rows), 96)
        self.assertEqual(len({r['id'] for r in rows}), 96)
        self.assertEqual(len({r['treatment'] for r in rows}), 96)
        counts = {c: sum(r['category'] == c for r in rows) for c in {r['category'] for r in rows}}
        self.assertEqual(counts, dict(Photo=12, Cinema=12, Painting=12, Illustration=12,
                                      **{'3D': 12}, Design=12, Material=8, Lighting=8, Color=8))
        for row in rows:
            self.assertNotRegex(catalog.generic(row).lower(), r'<lora:|embedding:|\(.*:\d|cfg|sampler|masterpiece')
            for family in ('sd', 'flux', 'fibo', 'video'):
                result = catalog.render('The requested subject performs the requested action', row, family)
                self.assertTrue(result.startswith('The requested subject performs the requested action'))

    def test_install_and_reload_preserve_user_bytes_and_order(self):
        for _ in range(3):
            catalog.install(self.module, self.shared, self.preferences)
            self.db.reload()
            self.assertEqual(list(self.db.styles)[:2], ['Personal', 'Legacy'])
            self.assertEqual(len(self.db.styles), 98)
            self.assertEqual(self.path.read_bytes(), self.original)
            self.assertFalse(self.preferences.exists())

    def test_sd_and_flux_use_their_prompt_formats_and_preserve_negative(self):
        style = self.name('Watercolor')
        sd = self.db.apply_styles_to_prompt(prompt='A red boat', styles=[style])
        self.assertTrue(sd.startswith('A red boat, watercolor painting,'))
        self.shared.opts.sd_model_checkpoint = 'flux3-image-klein-int8'
        flux = self.db.apply_styles_to_prompt('A red boat', [style])
        self.assertTrue(flux.startswith('A red boat\nVisual treatment: watercolor painting,'))
        self.assertEqual(self.db.apply_negative_styles_to_prompt(prompt='Keep this', styles=[style]), 'Keep this')

    def test_legacy_placeholders_lora_and_negative_are_unchanged(self):
        self.assertEqual(self.db.apply_styles_to_prompt('A boat', ['Personal', 'Legacy']),
                         'before A boat after, <lora:personal:1>')
        self.assertEqual(self.db.apply_negative_styles_to_prompt('wrong', ['Personal', 'Legacy']), 'wrong, blur, bad')
        self.assertEqual(self.db.apply_styles_to_prompt('A boat', ['unknown']), 'A boat')

    def test_fibo_json_keeps_subjects_text_and_edit_instructions(self):
        self.shared.opts.sd_model_checkpoint = 'FIBO 2.0 Dev (INT8)'
        source = dict(short_description='A shop', objects=[{'description': 'red boat'}],
                      text_render=[{'text': 'שלום "OPEN"'}], edit_instruction='Keep <image_1> unchanged',
                      artistic_style='delicate', style_medium='photography')
        prompt, negative = self.db.apply_styles_for_model(json.dumps(source, ensure_ascii=False), 'no blur',
                            [self.name('Watercolor'), self.name('Soft Window')], 'fibo')
        value = json.loads(prompt)
        for key in ('short_description', 'objects', 'text_render', 'edit_instruction'):
            self.assertEqual(value[key], source[key])
        self.assertEqual(value['style_medium'], 'watercolor painting')
        self.assertIn('Transparent washes', value['artistic_style'])
        self.assertIn('window-light', value['artistic_style'])
        self.assertTrue(value['artistic_style'].startswith('delicate;'))
        self.assertEqual(negative, 'no blur')

    def test_fibo_json_keeps_existing_structured_artistic_style(self):
        source = {'short_description': 'A boat', 'artistic_style': {'movement': 'original'}}
        result = catalog.vgl(json.dumps(source), catalog.rows()[self.name('Gouache')])
        self.assertEqual(json.loads(result)['artistic_style'], source['artistic_style'])
        self.assertIn('Matte opaque', json.loads(result)['short_description'])

    def test_fibo_personal_style_does_not_break_json(self):
        result, negative = self.db.apply_styles_for_model('{"short_description":"A boat"}', '', ['Personal'], 'fibo')
        self.assertEqual(json.loads(result)['short_description'], 'A boat')
        self.assertEqual(json.loads(result)['artistic_style'], 'before  after')
        self.assertEqual(negative, 'blur')

    def test_save_only_writes_user_styles_and_personal_overrides(self):
        style = self.name('Watercolor')
        self.db.styles[style] = self.db.styles[style]._replace(prompt='My custom watercolor', negative_prompt='blur')
        self.db.styles['My new style'] = self.module.PromptStyle('My new style', 'soft', '')
        self.db.save_styles()
        with self.path.open(encoding='utf-8-sig') as handle:
            saved = list(csv.DictReader(handle))
        self.assertEqual([r['name'] for r in saved], ['Personal', 'Legacy', style, 'My new style'])
        self.db.reload()
        self.assertEqual(self.db.styles[style].prompt, 'My custom watercolor')
        self.assertFalse(catalog.managed(self.db, style))
        self.assertEqual(self.db.apply_styles_to_prompt('boat', [style]), 'boat, My custom watercolor')

    def test_deleting_builtin_and_override_survives_reload(self):
        first, second = self.name('Watercolor'), self.name('Film Noir')
        self.db.styles[first] = self.db.styles[first]._replace(prompt='Personal override')
        self.db.save_styles()
        del self.db.styles[first], self.db.styles[second]
        self.db.save_styles()
        self.db.reload()
        self.assertNotIn(first, self.db.styles)
        self.assertNotIn(second, self.db.styles)
        self.assertEqual(set(json.loads(self.preferences.read_text())['hidden']), {first, second})
        self.assertEqual(len(self.db.styles), 96)

    def test_hidden_preferences_are_private_and_invalid_data_is_ignored(self):
        self.preferences.parent.mkdir()
        for content in ('broken', '[]', '{"hidden": null}'):
            self.preferences.write_text(content)
            self.db.reload()
            self.assertEqual(len(self.db.styles), 98)

    def test_personal_name_collision_is_never_overwritten(self):
        style = self.name('Watercolor')
        self.db.styles[style] = self.module.PromptStyle(style, 'personal recipe', 'my negative', str(self.path))
        catalog.install(self.module, self.shared, self.preferences)
        self.assertEqual(self.db.apply_styles_to_prompt('boat', [style]), 'boat, personal recipe')

    def test_video_uses_explicit_target_even_with_fibo_selected(self):
        sys.path.insert(0, str(WEBUI / 'extensions/sd-forge-video-studio'))
        from videostudio import prompting
        self.shared.opts.sd_model_checkpoint = 'FIBO 2.0'
        modules = ModuleType('modules')
        modules.shared = self.shared
        with patch.dict(sys.modules, {'modules': modules}):
            prompt, negative = prompting.apply_styles('A boat sails. Camera follows.', 'flicker', [self.name('Watercolor')])
        self.assertIn('A boat sails. Camera follows.\nVisual treatment:', prompt)
        self.assertIn('preserving the requested action and camera motion', prompt)
        self.assertEqual(negative, 'flicker')

    def test_actual_fibo_request_receives_selected_style_without_mutating_input(self):
        sys.path.insert(0, str(WEBUI / 'extensions/sd-forge-fibo2'))
        from fibo2 import glue
        p = SimpleNamespace(prompt='{"short_description":"A boat"}', negative_prompt='no blur',
            styles=[self.name('Watercolor')], width=1024, height=1024, steps=50, cfg_scale=5, seed=123,
            batch_size=1, n_iter=1)
        info = SimpleNamespace(fibo2_variant='dev', fibo2_precision='int8')
        modules = ModuleType('modules')
        modules.shared = self.shared
        with patch.dict(sys.modules, {'modules': modules}):
            request, references, mask = glue._fibo_request(p, info)
        request.validated(references, mask)
        self.assertEqual(json.loads(request.prompt)['style_medium'], 'watercolor painting')
        self.assertEqual(p.prompt, '{"short_description":"A boat"}')
        self.assertEqual(request.negative_prompt, 'no blur')

    def test_unload_restores_original_methods_and_keeps_overrides(self):
        self.db.styles[self.name('Watercolor')] = self.db.styles[self.name('Watercolor')]._replace(prompt='custom')
        self.db.save_styles()
        catalog.uninstall(self.module, self.shared)
        self.assertEqual(len(self.db.styles), 3)
        self.assertFalse(hasattr(self.db, 'apply_styles_for_model'))
        self.assertEqual(self.db.apply_styles_to_prompt('boat', ['Personal']), 'before boat after')


if __name__ == '__main__':
    unittest.main()

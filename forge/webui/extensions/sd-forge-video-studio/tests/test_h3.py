"""MiniMax-H3 preset, runtime, and Video Studio wiring contracts."""
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from videostudio import h3, models, runner, ui


class H3Contracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        output = patch.object(ui, 'OUTPUT_DIR', str(self.root / 'output'))
        output.start()
        self.addCleanup(output.stop)

    def job(self, **overrides):
        values = dict(kind='flf', start=None, end=None, prompt='A cinematic camera move.', negative='blur',
            quality=h3.DEFAULT_QUALITY, seconds=5, seed=7, preset=h3.DEFAULT_PRESET, steps=4, shift=5, cfg=8,
            sampler='dpmpp_2m', int8=False, attention='pytorch', width=0, height=0, smooth=False, upscale='Off',
            loras=[], aspect='16:9 landscape')
        values.update(overrides)
        return ui.build_job(**values)

    def test_pinned_asset_manifest_and_existing_families_stay_separate(self):
        self.assertEqual(len(h3.ASSETS), 4)
        self.assertEqual(models.preset_files(h3.DEFAULT_PRESET), h3.asset_keys(h3.DEFAULT_PRESET))
        self.assertFalse(set(models.preset_files(models.DEFAULT_PRESET)) & set(h3.asset_keys(h3.DEFAULT_PRESET)))
        for asset in h3.ASSETS.values():
            self.assertEqual(len(asset['sha256']), 64)
            self.assertGreater(asset['size'], 100_000_000)
            self.assertTrue(asset['url'].startswith('https://huggingface.co/'))
            self.assertTrue(asset['path'].startswith('h3/'))

    def test_linked_workflow_settings_override_wan_controls(self):
        job = self.job()
        self.assertEqual(job['backend'], 'h3')
        self.assertEqual((job['steps'], job['shift'], job['audio_shift'], job['cfg']), (20, 12.0, 3.0, 1.0))
        self.assertEqual((job['sampler'], job['scheduler'], job['fps'], job['frames']), ('euler', 'simple', 24, 124))
        self.assertEqual((job['width'], job['height']), (608, 352))
        self.assertEqual(job['attention'], 'comfy kitchen attention')
        self.assertTrue(job['spectrum'])
        self.assertFalse(job['native_audio'])
        self.assertNotIn('experts', job)
        self.assertEqual(job['negative'], '')

    def test_h3_frame_grid_and_limits(self):
        self.assertEqual(h3.frames_for(5), 124)
        self.assertEqual(h3.frames_for(6.6), 158)
        for seconds in (0.2, 1, 5, 10, 15):
            self.assertEqual(h3.frames_for(seconds) % 17, 5)
        for seconds in (0, -1, 16, float('nan')):
            with self.assertRaises(ValueError):
                h3.frames_for(seconds)

    def test_cross_family_loras_are_not_sent_to_h3(self):
        with self.assertRaisesRegex(ValueError, 'cannot be loaded into MiniMax-H3'):
            self.job(loras=[dict(path='wan.safetensors', strength=1)])
        styled, prompt, negative, adapters, skipped = ui.video_loras(
            'A boat <lora:ImageStyle:0.7>', 'blur', [], 'None', 1, 'None', 1, h3.DEFAULT_PRESET)
        self.assertIn('<lora:', styled)
        self.assertNotIn('<lora:', prompt)
        self.assertEqual((negative, adapters, skipped), ('blur', [], ['ImageStyle']))

    def test_worker_uses_isolated_h3_python_and_actionable_setup_error(self):
        with patch.object(h3, 'runtime_ready', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'Set up the MiniMax-H3 engine'):
                runner.worker_python({'backend': 'h3'})
        with patch.object(h3, 'runtime_ready', return_value=True):
            self.assertEqual(runner.worker_python({'backend': 'h3'}), str(h3.python_executable()))

    def test_runtime_marker_requires_all_pinned_components(self):
        webui, home = self.root / 'webui', self.root / 'h3'
        relative = Path('Scripts/python.exe') if sys.platform == 'win32' else Path('bin/python')
        (webui / 'fibo2/env' / relative).parent.mkdir(parents=True)
        (webui / 'fibo2/env' / relative).touch()
        (webui / 'fibo2/runtime/ComfyUI/comfy_extras').mkdir(parents=True)
        (webui / 'fibo2/runtime/ComfyUI/comfy_extras/nodes_minimax_h3.py').touch()
        (webui / 'fibo2/runtime-ready.json').write_text(json.dumps({'comfy_revision': h3.COMFY_REVISION}))
        (home / 'runtime/ComfyUI-ClipProj').mkdir(parents=True)
        (home / 'runtime/ComfyUI-ClipProj/clipproj_nodes.py').touch()
        (home / 'runtime/ComfyUI-Spectrum-MiniMax-H3/comfyui_spectrum_h3').mkdir(parents=True)
        (home / 'runtime/ComfyUI-Spectrum-MiniMax-H3/comfyui_spectrum_h3/nodes.py').touch()
        marker = dict(comfy_revision=h3.COMFY_REVISION, clipproj_revision=h3.CLIPPROJ_REVISION,
                      spectrum_revision=h3.SPECTRUM_REVISION)
        (home / 'runtime-ready.json').write_text(json.dumps(marker))
        with patch.object(h3, 'WEBUI_DIR', str(webui)), patch.object(h3, 'H3_HOME', home):
            self.assertTrue(h3.runtime_ready())
            marker['spectrum_revision'] = 'wrong'
            (home / 'runtime-ready.json').write_text(json.dumps(marker))
            self.assertFalse(h3.runtime_ready())

    def test_model_selection_loads_h3_defaults(self):
        selected = ui._preset_defaults(h3.DEFAULT_PRESET)
        self.assertEqual([selected[i]['value'] for i in (0, 1, 2, 5)], [20, 12.0, 1.0, 'euler'])
        self.assertEqual(selected[4]['choices'], list(h3.QUALITY))
        self.assertFalse(selected[0]['interactive'])
        self.assertFalse(selected[11]['visible'])
        self.assertEqual(selected[13]['choices'], ui.ingredient_methods(False, True))


if __name__ == '__main__':
    unittest.main()

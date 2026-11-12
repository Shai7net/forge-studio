"""LTX job contracts and real Gradio wiring; no checkpoint inference in unit tests."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
from videostudio import ltx, models, runner, ui


class LtxContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.patch_output = patch.object(ui, 'OUTPUT_DIR', str(self.root / 'output'))
        self.patch_output.start()
        self.addCleanup(self.patch_output.stop)

    def job(self, preset=ltx.DEFAULT_PRESET, **overrides):
        values = dict(kind='t2v', start=None, end=None, prompt='A boat sails.', negative='blur',
            quality=ltx.DEFAULT_QUALITY, seconds=5, seed=12, preset=preset, steps=4, shift=5, cfg=4,
            sampler='euler', int8=True, attention='auto', width=0, height=0, smooth=False, upscale='Off',
            loras=[], aspect='16:9 landscape')
        values.update(overrides)
        return ui.build_job(**values)

    def test_pinned_complete_asset_manifest_without_wan_regression(self):
        self.assertEqual(len(ltx.ASSETS), 7)  # three distilled models + Gemma, connectors, two VAEs
        for asset in ltx.ASSETS.values():
            self.assertEqual(len(asset['sha256']), 64)
            self.assertTrue(asset['url'].startswith('https://huggingface.co/'))
            self.assertTrue(asset['path'].startswith('ltx/'))
            self.assertGreater(asset['size'], 100_000_000)
        self.assertEqual(len(models.preset_files(models.DEFAULT_PRESET)), 4)
        self.assertEqual(len(models.preset_files(ltx.DEFAULT_PRESET)), 5)
        self.assertFalse(set(models.preset_files(models.DEFAULT_PRESET)) & set(ltx.asset_keys(ltx.DEFAULT_PRESET)))

    def test_distilled_schedule_and_geometry_override_wan_parameters(self):
        job = self.job()
        self.assertEqual((job['backend'], job['steps'], job['cfg'], job['fps']), ('ltx', 8, 1, 24))
        self.assertNotIn('experts', job)
        self.assertEqual(job['frames'], 121)
        self.assertEqual(job['width'] % 32, 0)
        self.assertEqual(job['height'] % 32, 0)
        self.assertEqual(len(job['sigmas']), 9)
        self.assertEqual(job['sigmas'][-1], 0)
        self.assertEqual(job['prompt'], 'A boat sails.')

    def test_wan_job_retains_existing_sampling_and_frame_rules(self):
        job = self.job(models.DEFAULT_PRESET, quality=ui.DEFAULT_QUALITY, steps=4, cfg=1)
        self.assertNotIn('backend', job)
        self.assertEqual(len(job['experts']), 2)
        self.assertEqual((job['frames'], job['fps'], job['steps']), (81, 16, 4))

    def test_frame_lengths_are_valid_for_temporal_vae(self):
        for seconds in (1, 1.25, 2.75, 5, 7.5, 8):
            frames = ltx.frames_for(seconds)
            self.assertEqual((frames - 1) % 8, 0)
            self.assertLessEqual(abs((frames - 1) / 24 - seconds), 1 / 6 + 1e-10)
        for seconds in (0, -1, float('nan'), 9):
            with self.assertRaises(ValueError):
                ltx.frames_for(seconds)

    def test_endpoint_chain_is_preserved(self):
        first, last = self.root / 'first.png', self.root / 'last.png'
        Image.new('RGB', (128, 256), 'red').save(first)
        Image.new('RGB', (128, 256), 'blue').save(last)
        parts = [dict(start_image=str(first), end_image=str(last), prompt='Turn around.'),
                 dict(start_image=str(last), end_image=str(first), prompt='Turn back.')]
        job = self.job(kind='multi', start=str(first), end=str(last), parts=parts)
        self.assertEqual(job['parts'], parts)
        self.assertEqual(job['end_image'], str(last))
        self.assertLess(job['width'], job['height'])

    def test_cross_family_adapters_are_refused(self):
        with self.assertRaisesRegex(ValueError, 'Wan LoRAs'):
            self.job(loras=[dict(path='wan.safetensors', strength=1)])
        result = self.job(loras=[dict(path='ltx.safetensors', strength=1, family='ltx')])
        self.assertEqual(result['loras'][0]['family'], 'ltx')

    def test_ltx_lora_tags_resolve_only_in_ltx_namespace(self):
        with patch.object(ltx, 'loras', return_value=[str(self.root / 'Motion.safetensors')]):
            _, prompt, negative, adapters, skipped = ui.video_loras('A boat. <lora:Motion:0.7> <lora:ImageStyle:1>',
                'blur', [], 'None', 1, 'None', 1, ltx.DEFAULT_PRESET)
            self.assertNotIn('<lora:', prompt)
            self.assertEqual(negative, 'blur')
            self.assertEqual(adapters[0]['strength'], 0.7)
            self.assertEqual(skipped, ['ImageStyle'])
            with self.assertRaisesRegex(ValueError, 'LTX folder'):
                ui.video_loras('A boat', '', [], 'Wan motion', 1, 'None', 1, ltx.DEFAULT_PRESET)

    def test_worker_python_is_isolated_and_missing_runtime_is_actionable(self):
        with patch.object(ltx, 'runtime_ready', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'Set up the LTX engine'):
                runner.worker_python({'backend': 'ltx'})
        with patch.object(ltx, 'runtime_ready', return_value=True):
            self.assertEqual(runner.worker_python({'backend': 'ltx'}), str(ltx.python_executable()))
        self.assertEqual(runner.worker_python({}), runner.engine_python())

    def test_runtime_reuse_requires_the_pinned_comfy_revision(self):
        webui, home = self.root / 'webui', self.root / 'ltx'
        marker = webui / 'fibo2/runtime-ready.json'
        marker.parent.mkdir(parents=True)
        with patch.object(ltx, 'WEBUI_DIR', str(webui)), patch.object(ltx, 'LTX_HOME', home):
            marker.write_text(json.dumps({'comfy_revision': ltx.COMFY_REVISION}))
            self.assertEqual(ltx.runtime_paths()[0], webui / 'fibo2/runtime/ComfyUI')
            marker.write_text(json.dumps({'comfy_revision': 'another version'}))
            self.assertEqual(ltx.runtime_paths()[0], home / 'runtime/ComfyUI')

    def test_corrupt_or_mismatched_runtime_marker_requires_setup(self):
        with patch.object(ltx, 'LTX_HOME', self.root):
            for value in ('broken', '[]', '{}', '{"comfy_revision":"old"}'):
                (self.root / 'runtime-ready.json').write_text(value)
                self.assertFalse(ltx.runtime_ready())

    def args(self, mode='t2v', picture=None, audio=None):
        rest = ['16:9 landscape', '', 25, 6, 'Euler', 'A boat sails.', '', ltx.DEFAULT_QUALITY,
            1, 12, ltx.DEFAULT_PRESET, 4, 5, 1, 'euler', True, 'auto', 0, 0, False, 'Off',
            'None', 1, 'None', 1, [], True, '']
        rest += [None] * 14 + [None] * ui.MAX_INGREDIENTS + [ui.ING_FRAME, picture, audio, True]
        return [mode, 1] + [None] * ui.MAX_PICTURES + [''] * (ui.MAX_PICTURES - 1) + rest

    def run_generator(self, args):
        recorded = []
        modules = types.ModuleType('modules')
        queue = types.ModuleType('modules.call_queue')
        queue.queue_lock = threading.RLock()
        modules.call_queue = queue

        def run(spec, details, out, nothing):
            recorded.append(spec)
            yield out(ui.status_html('done', 1))
            return 'test.mp4'

        with patch.dict(sys.modules, {'modules': modules, 'modules.call_queue': queue}), \
             patch.object(ltx, 'runtime_ready', return_value=True), patch.object(models, 'missing', return_value=[]), \
             patch.object(runner, 'busy', return_value=False), patch.object(ui, '_run_job', side_effect=run), \
             patch.object(ui, 'draw_first_frame', side_effect=AssertionError('LTX native T2V must not draw a Forge image')), \
             patch.object(ui, '_sound_after', side_effect=AssertionError('Do not overwrite native audio with MMAudio')), \
             patch.object(ui, 'JOBS_DIR', str(self.root / 'jobs')):
            result = list(ui.generate(*args))
        self.assertTrue(result)
        return recorded

    def test_native_text_to_video_skips_forge_image_and_mmaudio(self):
        jobs = self.run_generator(self.args())
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]['mode'], 't2v')
        self.assertIsNone(jobs[0]['start_image'])
        self.assertTrue(jobs[0]['native_audio'])

    def test_audio_driven_mode_stages_picture_and_original_audio(self):
        picture, audio = self.root / 'face.png', self.root / 'speech.wav'
        Image.new('RGB', (64, 64), 'red').save(picture)
        with wave.open(str(audio), 'wb') as target:
            target.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
            target.writeframes(b'\0\0' * 16000)
        jobs = self.run_generator(self.args('ltx_audio', str(picture), str(audio)))
        self.assertEqual(jobs[0]['mode'], 'i2v')
        self.assertEqual(Path(jobs[0]['source_audio']).read_bytes(), audio.read_bytes())
        self.assertEqual(Path(jobs[0]['start_image']).read_bytes(), picture.read_bytes())

    def test_audio_mode_rejects_missing_audio_before_starting_job(self):
        outputs = list(ui.generate(*self.args('ltx_audio')))
        self.assertIn('add a picture and an audio file', outputs[-1][0])

    def test_character_replacement_stays_on_dedicated_wan_backend(self):
        called = []
        def replace(*args):
            called.append(args)
            yield ('replacement stays available', None, None, None, None)
        with patch.object(ltx, 'runtime_ready', return_value=False), patch.object(ui, 'generate_replace', side_effect=replace):
            output = list(ui.generate(*self.args('replace')))
        self.assertEqual(len(called), 1)
        self.assertEqual(output[0][0], 'replacement stays available')

    def test_ltx_ingredients_routes_flux_first_frame_to_selected_backend(self):
        picture = self.root / 'ingredient.png'
        Image.new('RGB', (128, 256), 'red').save(picture)
        args = self.args('ingredients')
        rest_start = 2 + ui.MAX_PICTURES + ui.MAX_PICTURES - 1
        args[rest_start + 42] = str(picture)
        args[rest_start + 46] = ui.ING_LTX_FRAME
        with patch.object(ui, 'flux3_glue', return_value=object()), \
             patch.object(ui, 'draw_from_ingredients', return_value=str(picture)), \
             patch.object(ui, 'generate_ingredients', side_effect=AssertionError('Do not silently run Phantom for LTX')):
            jobs = self.run_generator(args)
        self.assertEqual(jobs[0]['backend'], 'ltx')
        self.assertEqual(jobs[0]['start_image'], str(picture))

    def test_automatic_wan_references_do_not_require_flux(self):
        self.assertEqual(ui.ingredient_route(ui.ING_AUTO, False, False), 'phantom')
        self.assertEqual(ui.ingredient_route(ui.ING_AUTO, False, True), 'flux')
        self.assertEqual(ui.ingredient_route(ui.ING_PHANTOM, True, True), 'phantom')
        self.assertEqual(ui.ingredient_route(ui.ING_AUTO, True, True), 'flux')
        self.assertEqual(ui.ingredient_route(ui.ING_AUTO, True, False), 'phantom')
        self.assertEqual(ui.ingredient_route(ui.ING_LTX_FRAME, True, True), 'flux')

    def test_installed_flux_discovery_skips_incomplete_models(self):
        bad = types.SimpleNamespace(filename='a-klein.safetensors')
        good = types.SimpleNamespace(filename='b-klein.safetensors')
        def components(info):
            if info is bad:
                raise FileNotFoundError('missing text encoder')
            return {'model': info.filename}
        glue = types.SimpleNamespace(sd_models=types.SimpleNamespace(checkpoints_list={'a': bad, 'b': good}),
                                     is_flux3_info=lambda info: True, components=components)
        self.assertIs(ui.installed_flux3_checkpoint(glue), good)

    def test_transient_flux_engine_does_not_change_the_selected_checkpoint(self):
        import torch
        class Engine:
            lock = threading.RLock()
            closed = False
            def load(self, device):
                self.device = device
            def encode(self, text):
                self.text = text
                return torch.zeros(1)
            def sample(self, noise, embedding, steps, **kw):
                self.steps = steps
                self.references = kw['refs']
                return noise
            def decode(self, latent):
                return torch.zeros(1, 3, 64, 64)
            def unload(self):
                self.closed = True
        engine = Engine()
        selected = object()
        options = {'sd_model_checkpoint': 'my-chosen-image-model'}
        shared = types.SimpleNamespace(sd_model=selected, opts=types.SimpleNamespace(data=options),
                                       state=types.SimpleNamespace(interrupted=False))
        modules = types.ModuleType('modules')
        modules.shared, modules.devices = shared, types.SimpleNamespace(device=torch.device('cpu'))
        glue = types.SimpleNamespace(feng=types.SimpleNamespace(Flux3Engine=lambda paths: engine),
            components=lambda info: {}, clean_prompt=lambda s: s, encode_references=lambda eng, refs: refs,
            _to_pil=lambda tensor: Image.new('RGB', (64, 64)))
        with patch.dict(sys.modules, {'modules': modules}), \
             patch.object(ui, 'installed_flux3_checkpoint', return_value=object()), \
             patch.object(runner, 'free_forge_memory'):
            result = ui.draw_with_installed_flux(glue, 'A woman. <lora:old-sd-style:1>', ['a', 'b'], 64, 64, 12)
        self.assertEqual(result.size, (64, 64))
        self.assertEqual(engine.references, ['a', 'b'])
        self.assertEqual(engine.steps, ui.FIRST_FRAME_STEPS)
        self.assertNotIn('<lora:', engine.text)
        self.assertTrue(engine.closed)
        self.assertIs(shared.sd_model, selected)
        self.assertEqual(options, {'sd_model_checkpoint': 'my-chosen-image-model'})
        engine.closed = False
        with patch.dict(sys.modules, {'modules': modules}), \
             patch.object(ui, 'installed_flux3_checkpoint', return_value=object()), \
             patch.object(runner, 'free_forge_memory'), patch.object(engine, 'encode', side_effect=RuntimeError('failed')):
            with self.assertRaisesRegex(RuntimeError, 'failed'):
                ui.draw_with_installed_flux(glue, 'Test', ['a', 'b'], 64, 64, 12)
        self.assertTrue(engine.closed)

    def test_first_frame_stop_cancels_only_its_own_state_and_next_draw_recovers(self):
        state = types.SimpleNamespace(interrupted=False, begins=0, ends=0)
        def begin(**kwargs):
            state.interrupted = False
            state.begins += 1
        def end():
            state.ends += 1
        state.begin, state.end = begin, end
        state.interrupt = lambda: setattr(state, 'interrupted', True)
        modules = types.ModuleType('modules')
        modules.shared = types.SimpleNamespace(state=state)
        @ui.frame_task
        def stopped():
            ui.cancel()
            return 'partial frame'
        @ui.frame_task
        def next_frame():
            return 'complete frame'
        with patch.dict(sys.modules, {'modules': modules}), patch.object(runner, 'cancel_current'), \
             patch.object(ui.sound, 'cancel_current'):
            with self.assertRaises(ui.FrameCancelled):
                stopped()
            self.assertEqual(next_frame(), 'complete frame')
        self.assertFalse(ui._FRAME_ACTIVE)
        self.assertEqual((state.begins, state.ends), (2, 2))

    def two_pictures(self, method):
        paths = [self.root / 'red.png', self.root / 'blue.png']
        for path, color in zip(paths, ['red', 'blue']):
            Image.new('RGB', (128, 256), color).save(path)
        args = self.args('ingredients')
        rest_start = 2 + ui.MAX_PICTURES + ui.MAX_PICTURES - 1
        args[rest_start + 42:rest_start + 44] = list(map(str, paths))
        args[rest_start + 46] = method
        return args, [str(p) for p in paths]

    def test_two_references_with_ltx_draw_one_first_frame_from_both(self):
        """The case that was blocked (2026-11-11): two pictures, LTX, another image checkpoint selected. FLUX 3 draws
        one scene of both (transiently when it is not the selected checkpoint); LTX animates that frame."""
        args, paths = self.two_pictures(ui.ING_AUTO)
        frame = self.root / 'frame.png'
        Image.new('RGB', (128, 256), 'green').save(frame)
        seen = {}

        def draw(prompt, pictures, aspect, seed, folder):
            seen['pictures'] = list(pictures)
            return str(frame)
        with patch.object(ui, 'flux3_glue', return_value=object()),              patch.object(ui, 'draw_from_ingredients', side_effect=draw),              patch.object(ui, 'generate_ingredients', side_effect=AssertionError('FLUX 3 is installed: no Phantom')):
            jobs = self.run_generator(args)
        self.assertEqual(seen['pictures'], paths)
        self.assertEqual(jobs[0]['backend'], 'ltx')
        self.assertEqual(jobs[0]['start_image'], str(frame))
        self.assertNotIn('references', jobs[0])

    def test_without_flux_two_references_fall_back_to_phantom(self):
        args, paths = self.two_pictures(ui.ING_AUTO)
        called = []

        def phantom(*a):
            called.append([x for x in a[-ui.MAX_INGREDIENTS:] if x])
            yield ('phantom', None, None, None, None)
        with patch.object(ui, 'flux3_glue', return_value=None), patch.object(ui, 'generate_ingredients', phantom):
            list(ui.generate(*args))
        self.assertEqual(called, [paths])

    def test_ltx_refine_follows_rendered_family_and_preserves_source_audio(self):
        base = self.root / 'rendered'
        video, last = str(base) + '.mp4', str(base) + '.last.png'
        Path(video).write_bytes(b'placeholder; inference is outside this contract test')
        Image.new('RGB', (64, 64)).save(last)
        Path(str(base) + '.json').write_text(json.dumps(dict(backend='ltx', width=512, height=288,
            frames=49, fps=48, base_frames=25, base_fps=24, model_label=ltx.DEFAULT_PRESET,
            seed=12, prompt='A boat', native_audio=True)), encoding='utf-8')
        called = []
        def run(spec, details, out, nothing):
            called.append(spec)
            yield out(ui.status_html('done', 1))
        modules, queue = types.ModuleType('modules'), types.ModuleType('modules.call_queue')
        queue.queue_lock = threading.RLock()
        modules.call_queue = queue
        with patch.dict(sys.modules, {'modules': modules, 'modules.call_queue': queue}), \
             patch.object(ltx, 'runtime_ready', return_value=True), patch.object(models, 'missing', return_value=[]), \
             patch.object(runner, 'busy', return_value=False), patch.object(ui, '_run_job', side_effect=run):
            list(ui.refine(last, next(iter(ui.REFINE_QUALITY)), next(iter(ui.REFINE_STRENGTH)),
                           False, 'Off', True, 'auto', models.DEFAULT_PRESET))
        spec = called[0]
        self.assertEqual(spec['backend'], 'ltx')
        self.assertEqual(spec['frames'], 25)
        self.assertEqual(spec['source_audio'], video)
        self.assertEqual(spec['init_video'], video)
        self.assertNotIn('experts', spec)

    def test_actual_gradio_build_and_bidirectional_model_defaults(self):
        modules = types.ModuleType('modules')
        modules.infotext_utils = types.SimpleNamespace(add_paste_fields=lambda *a, **k: None, registered_param_bindings=[])
        with patch.dict(sys.modules, {'modules': modules, 'modules.infotext_utils': modules.infotext_utils}):
            tab = ui.build()
        self.addCleanup(tab.close)
        config = tab.get_config_file()
        ids = {c['props'].get('elem_id') for c in config['components']}
        self.assertTrue({'vs_model', 'vs_tab_ltx_audio', 'vs_ltx_picture', 'vs_ltx_audio', 'vs_ltx_setup'} <= ids)
        callback = next(f.fn for f in tab.fns if getattr(f.fn, '__name__', '') == 'preset_defaults')
        selected = callback(ltx.DEFAULT_PRESET)
        self.assertEqual(selected[0]['value'], 8)
        self.assertEqual(selected[4]['choices'], list(ltx.QUALITY))
        self.assertTrue(selected[6]['visible'])
        self.assertFalse(selected[7]['visible'])
        self.assertEqual([value['value'] for value in selected[-2:]], [0, 0])
        wan = callback(models.DEFAULT_PRESET)
        self.assertEqual(wan[0]['value'], 4)
        self.assertFalse(wan[6]['visible'])
        self.assertTrue(wan[7]['visible'])


if __name__ == '__main__':
    unittest.main()

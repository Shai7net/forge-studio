import importlib.util
import json
from pathlib import Path
import struct
import sys
import tempfile
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import forge_workflow as workflow
import forge_img2img_hires as hires
from test_universal_styles import load_native_styles
from PIL import Image


class Workflow(unittest.TestCase):
    def test_architecture_from_tensor_shapes_and_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'renamed.safetensors'
            header = json.dumps({'model.diffusion_model.attn2.to_k.weight': {'shape': [320, 2048]}}).encode()
            path.write_bytes(struct.pack('<Q', len(header)) + header)
            self.assertEqual(workflow.file_family(path), 'sdxl')
            self.assertEqual(workflow.file_family(path, {'modelspec.architecture': 'flux.1-dev/lora'}), 'flux')

    def test_lora_shortcut_saves_real_csv_and_does_not_overwrite_another_style(self):
        with tempfile.TemporaryDirectory() as directory:
            module = load_native_styles()
            path = Path(directory) / 'styles.csv'
            path.write_text('name,prompt,negative_prompt\nExisting,keep this,blur\n')
            db = module.StyleDatabase([path])
            modules = ModuleType('modules'); modules.shared = NS(prompt_styles=db); modules.styles = module
            rows = [dict(name='My LoRA', alias='the-alias', activation='trigger words', negative='bad hands')]
            with patch.dict(sys.modules, {'modules': modules}), patch.object(workflow, 'lora_rows', return_value=rows):
                workflow.save_lora_style('My LoRA', 'Shortcut', 0.8)
                from fastapi import HTTPException
                with self.assertRaises(HTTPException) as raised:
                    workflow.save_lora_style('My LoRA', 'Existing')
                self.assertEqual(raised.exception.status_code, 409)
                with self.assertRaises(HTTPException):
                    workflow.save_lora_style('missing', 'Other')
            loaded = module.StyleDatabase([path])
            self.assertEqual(loaded.styles['Shortcut'].prompt, '<lora:the-alias:0.8> trigger words')
            self.assertEqual(loaded.styles['Shortcut'].negative_prompt, 'bad hands')
            self.assertEqual(loaded.styles['Existing'].prompt, 'keep this')

    def test_generation_guard_copies_units_and_preserves_compatible_native_loras(self):
        unit, motion = NS(enabled=True), NS(enable=True)
        scripts = NS(alwayson_scripts=[NS(title=lambda: 'ControlNet', args_from=0, args_to=1),
                                      NS(title=lambda: 'AnimateDiff', args_from=1, args_to=2)])
        styles = {'SD style': NS(prompt='<lora:sd:1>', negative_prompt=''),
                  'Flux style': NS(prompt='<lora:fx:1>', negative_prompt=''),
                  'Art': NS(prompt='watercolor', negative_prompt='')}
        modules = ModuleType('modules'); modules.shared = NS(opts=NS(sd_model_checkpoint='Flux'), prompt_styles=NS(styles=styles))
        p = NS(prompt='Subject <lora:sd:1> <lora:fx:0.7>', negative_prompt='',
               styles=list(styles), scripts=scripts, script_args=[unit, motion])
        with patch.dict(sys.modules, {'modules': modules}), patch.object(workflow, 'checkpoint_family', return_value='flux'), \
             patch.object(workflow, 'lora_rows', return_value=[dict(name='sd', alias='sd', family='sd1'), dict(name='fx', alias='fx', family='flux')]):
            workflow.guard_processing(p)
        self.assertTrue(unit.enabled); self.assertTrue(motion.enable)
        self.assertFalse(p.script_args[0].enabled); self.assertFalse(p.script_args[1].enable)
        self.assertNotIn('<lora:sd:', p.prompt)
        self.assertIn('<lora:fx:', p.prompt)
        self.assertEqual(p.styles, ['Flux style', 'Art'])

    def test_qr_defaults_use_original_preprocessor_and_exact_model_preference(self):
        recorded = []
        class Group:
            def register_build_sliders(self): recorded.append('original')
        group = Group()
        group.type_filter = NS(change=lambda fn, **kw: recorded.append((fn, kw)))
        group.weight, group.guidance_start, group.guidance_end = object(), object(), object()
        state = ModuleType('lib_controlnet.global_state')
        state.get_all_preprocessor_tags = lambda: ['All', 'Canny']
        state.get_filtered_preprocessor_names = lambda tag: ['None', 'canny']
        state.get_filtered_controlnet_names = lambda tag: ['None', 'old']
        state.get_all_controlnet_names = lambda: ['None', 'qrCodeMonster_v10 [xxx]', 'qrCodeMonster_v20 [5e5778cb]']
        base = ModuleType('lib_controlnet'); base.global_state = state
        ui = ModuleType('lib_controlnet.controlnet_ui.controlnet_ui_group'); ui.ControlNetUiGroup = Group
        with patch.dict(sys.modules, {'lib_controlnet': base, 'lib_controlnet.global_state': state,
             'lib_controlnet.controlnet_ui': ModuleType('lib_controlnet.controlnet_ui'),
             'lib_controlnet.controlnet_ui.controlnet_ui_group': ui,
             'gradio': NS(update=lambda **kw: kw)}):
            workflow.install_qr(); group.register_build_sliders()
        self.assertIn('QR-CODE', state.get_all_preprocessor_tags())
        self.assertEqual(state.get_filtered_preprocessor_names('QR-CODE'), ['None', workflow.INVERT])
        self.assertEqual(state.get_filtered_controlnet_names('QR-CODE')[1], 'qrCodeMonster_v20 [5e5778cb]')
        self.assertEqual(state.get_filtered_controlnet_names('Canny'), ['None', 'old'])
        self.assertEqual([v['value'] for v in recorded[1][0]('QR-CODE')], [0.8, 0.24, 0.79])


class Hires(unittest.TestCase):
    def test_dimensions_keep_aspect_and_reject_unbounded_work(self):
        self.assertEqual(hires.target_size(512, 768), (1024, 1536))
        self.assertEqual(hires.target_size(512, 768, resize_x=1024), (1024, 1536))
        with self.assertRaises(ValueError): hires.target_size(4096, 4096, 4)
        with self.assertRaises(ValueError): hires.target_size(512, 512, float('nan'))

    def test_second_pass_has_own_lifecycle_and_only_controlnet_scripts(self):
        made = []
        class Processing:
            def __init__(self, **kw): self.__dict__.update(kw); self.extra_generation_params = {}; made.append(self)
            def close(self): self.closed = True
        modules = ModuleType('modules')
        modules.shared = NS(state=NS(interrupted=False, skipped=False), opts=NS(sd_model_checkpoint='SD'))
        modules.processing = NS(StableDiffusionProcessingImg2Img=Processing,
            process_images=lambda p: NS(images=[Image.new('RGB', (p.width, p.height))], index_of_first_image=0))
        modules.images = NS(resize_image=lambda mode, image, w, h, **kw: image.resize((w, h)))
        modules.scripts = NS(ScriptRunner=lambda: NS(alwayson_scripts=[]))
        cn = NS(title=lambda: 'ControlNet'); ad = NS(title=lambda: 'AnimateDiff')
        parent = NS(sd_model=object(), prompt='subject', negative_prompt='', seed=12, steps=8, cfg_scale=1.5,
            sampler_name='LCM', outpath_samples='', outpath_grids='', scripts=NS(alwayson_scripts=[cn, ad]),
            script_args=[], extra_generation_params={}, all_prompts=['subject <lora:detail:1>'], all_seeds=[55])
        with patch.dict(sys.modules, {'modules': modules}), patch.object(workflow, 'checkpoint_family', return_value='sd1'):
            result = hires.refine(parent, Image.new('RGB', (128, 128)), [True, 'Lanczos', 2, 0, 0.35, 0, 0])
        self.assertEqual(result.size, (256, 256))
        self.assertEqual(made[0].seed, 55)
        self.assertIn('<lora:detail:', made[0].prompt)
        self.assertEqual(made[0].scripts.alwayson_scripts, [cn])
        self.assertEqual(parent.scripts.alwayson_scripts, [cn, ad])
        self.assertTrue(made[0].closed)
        self.assertTrue(made[0]._forge_img2img_hires_pass)


if __name__ == '__main__': unittest.main()

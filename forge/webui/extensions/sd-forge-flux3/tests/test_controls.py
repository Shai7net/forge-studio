"""Shared-panel mapping is tested without loading GPU weights."""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from PIL import Image
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from flux3.controls import prepare, preserve_exact, restore_black_pixels


class Controls(unittest.TestCase):
    def request(self, **values):
        args = dict(fibo2_mode="auto", fibo2_keep_source_size=True, fibo2_references=[],
                    fibo2_mask=None, fibo2_prompt_writer="off", width=512, height=512, extra_generation_params={})
        args.update(values)
        return SimpleNamespace(**args)

    def test_reference_order_source_size_and_prompt_writer(self):
        first, second = Image.new("RGB", (131, 99), "red"), Image.new("RGB", (64, 64), "blue")
        p = self.request(fibo2_references=[first], fibo2_prompt_writer="always")
        prepare(p)
        self.assertEqual((p.width, p.height), (128, 96))
        self.assertEqual(p.flux3_panel_writer, "Always")
        self.assertTrue(p.flux3_panel_edit)
        p = self.request(init_images=[first], fibo2_references=[second])
        prepare(p)
        prepare(p)  # validation is repeated at the inference boundary after Script.process
        self.assertEqual([im.getpixel((0, 0)) for im in p.flux3_references], [(255, 0, 0), (0, 0, 255)])
        self.assertEqual((p.width, p.height), (512, 512))

    def test_generate_and_invalid_edits(self):
        p = self.request(); prepare(p)
        self.assertFalse(p.flux3_panel_edit)
        for values in [dict(fibo2_mode="edit"), dict(fibo2_mask=Image.new("L", (64, 64))),
                       dict(fibo2_mode="generate", fibo2_references=[Image.new("RGB", (64, 64))]),
                       dict(fibo2_references=[Image.new("RGB", (64, 64))], fibo2_mask=Image.new("L", (32, 32)))]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                prepare(self.request(**values))

    def test_mask_black_pixels_survive_exactly(self):
        source = Image.new("RGB", (64, 64), (17, 49, 231))
        mask = Image.new("L", (64, 64), 0); mask.paste(255, (32, 0, 64, 64))
        p = self.request(fibo2_references=[source], fibo2_mask=mask)
        prepare(p)
        result = preserve_exact(Image.new("RGB", (64, 64), "red"), p.flux3_references[0], p.flux3_exact_mask)
        self.assertEqual(result.crop((0, 0, 32, 64)).tobytes(), source.crop((0, 0, 32, 64)).tobytes())
        self.assertEqual(result.getpixel((48, 30)), (255, 0, 0))

    def test_programmatic_requests_keep_existing_behavior(self):
        p = SimpleNamespace(flux3_references=["existing reference"])
        prepare(p)
        self.assertEqual(p.flux3_references, ["existing reference"])
        self.assertFalse(hasattr(p, "flux3_panel_writer"))
        p=self.request(is_api=True,init_images=[Image.new('RGB',(64,64))],flux3_writer='Always')
        prepare(p)
        self.assertFalse(hasattr(p,'flux3_panel_writer'))
        self.assertEqual(p.flux3_writer,'Always')
        self.assertEqual((p.width,p.height),(512,512))

    def test_final_pixel_copy_corrects_forge_float_quantization(self):
        source=Image.new('RGB',(3,1),(17,49,231))
        result=Image.new('RGB',(3,1),(16,48,230))
        mask=Image.new('L',(3,1)); mask.putdata([0,127,255])
        actual=restore_black_pixels(result,source,mask)
        self.assertEqual(list(actual.getdata()),[(17,49,231),(16,48,230),(16,48,230)])

if __name__ == "__main__":
    unittest.main()

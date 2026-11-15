"""FIBO's pictures are saved where every generation goes, through Forge's save_image (2026-11-15)."""
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image, PngImagePlugin  # noqa: E402

from fibo2 import service  # noqa: E402
from fibo2.contracts import Generation  # noqa: E402


class Output(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.saved = []
        worker = os.path.join(self.tmp, "job-01.png")
        info = PngImagePlugin.PngInfo()
        info.add_text("parameters", "A cup\nSteps: 8")
        info.add_text("fibo2", '{"seed": 7}')
        Image.new("RGB", (8, 8), "red").save(worker, pnginfo=info)
        self.worker = worker

        def save_image(image, path, basename, seed=None, prompt=None, extension="png", info=None, p=None,
                       existing_info=None, **kw):
            target = os.path.join(path, f"{len(self.saved):05d}-{seed}.{extension}")
            self.saved.append({"path": path, "seed": seed, "info": info, "extra": existing_info})
            return target, None
        opts = types.SimpleNamespace(samples_save=True, samples_format="png", outdir_samples="",
                                     outdir_txt2img_samples="outputs/txt2img-images",
                                     outdir_img2img_samples="outputs/img2img-images")
        modules = types.ModuleType("modules")
        modules.images = types.SimpleNamespace(save_image=save_image)
        modules.shared = types.SimpleNamespace(opts=opts)
        self.opts = opts
        self.old = {k: sys.modules.get(k) for k in ("modules", "modules.images", "modules.shared")}
        sys.modules.update({"modules": modules, "modules.images": modules.images, "modules.shared": modules.shared})

    def fresh(self):
        return {"images": [{"path": self.worker, "metadata": {"seed": 7}}]}

    def tearDown(self):
        for k, v in self.old.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v

    def test_saved_in_the_generation_folder_with_its_details(self):
        r = service.keep_like_forge(self.fresh(), Generation(prompt="A cup"), None)
        self.assertEqual(self.saved[0]["path"], "outputs/txt2img-images")
        self.assertEqual(self.saved[0]["info"], "A cup\nSteps: 8")
        self.assertEqual(self.saved[0]["extra"], {"fibo2": '{"seed": 7}'})
        self.assertTrue(r["images"][0]["path"].startswith("outputs/txt2img-images"))
        self.assertTrue(r["images"][0]["worker_path"].endswith("job-01.png"))

    def test_the_general_folder_and_the_page_folder_win(self):
        self.opts.outdir_samples = "pictures-everything-goes"
        service.keep_like_forge(self.fresh(), Generation(prompt="A cup"), None)
        self.assertEqual(self.saved[-1]["path"], "pictures-everything-goes")
        p = types.SimpleNamespace(outpath_samples="outputs/img2img-images", do_not_save_samples=False)
        service.keep_like_forge(self.fresh(), Generation(prompt="A cup"), p)
        self.assertEqual(self.saved[-1]["path"], "outputs/img2img-images")

    def test_a_job_that_asked_not_to_save_keeps_them_in_memory(self):
        p = types.SimpleNamespace(outpath_samples="x", do_not_save_samples=True)
        r = service.keep_like_forge(self.fresh(), Generation(prompt="A cup"), p)
        self.assertEqual(self.saved, [])
        self.assertTrue(r["images"][0]["path"].endswith("job-01.png"))
        self.opts.samples_save = False
        service.keep_like_forge(self.fresh(), Generation(prompt="A cup"), None)
        self.assertEqual(self.saved, [])


if __name__ == "__main__":
    unittest.main()

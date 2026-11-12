"""Hires controls for the normal img2img tab; no core UI or processing patch."""
from pathlib import Path
import sys
import gradio as gr
from modules import scripts, shared
from modules.ui_components import InputAccordion

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import forge_img2img_hires as hires


class Script(scripts.Script):
    sorting_priority = -100

    def title(self):
        return hires.TITLE

    def show(self, is_img2img):
        return scripts.AlwaysVisible if is_img2img else False

    def ui(self, is_img2img):
        with InputAccordion(False, label='Hires. fix', elem_id='img2img_hires') as enabled:
            gr.Markdown('Upscale the generated image, then refine it in a second pass. FIBO uses its native image edit; denoising strength applies to SD, SDXL and FLUX.')
            with gr.Row():
                upscaler = gr.Dropdown(list(dict.fromkeys(['Lanczos'] + [u.name for u in shared.sd_upscalers])),
                    value='Lanczos', label='Upscaler', elem_id='img2img_hires_upscaler')
                scale = gr.Slider(1, 4, value=2, step=0.05, label='Upscale by', elem_id='img2img_hires_scale')
            with gr.Row():
                steps = gr.Slider(0, 60, value=0, step=1, label='Hires steps (0: same as first pass)', elem_id='img2img_hires_steps')
                denoise = gr.Slider(0, 1, value=0.35, step=0.01, label='Hires denoising strength', elem_id='img2img_hires_denoise')
            with gr.Row():
                width = gr.Slider(0, 4096, value=0, step=8, label='Resize width to (0: automatic)', elem_id='img2img_hires_width')
                height = gr.Slider(0, 4096, value=0, step=8, label='Resize height to (0: automatic)', elem_id='img2img_hires_height')
        # The switch is not pasted back: a picture made with it carries 'Hires fix (img2img): True', and sending it
        # to img2img again (or PNG Info) turned the second pass on by itself, doubling the size every round. It stays
        # off until the user opens it; its values (upscaler, scale, steps, strength) are still filled in.
        self.infotext_fields = [(upscaler, 'Hires upscaler'), (scale, 'Hires upscale'),
                               (steps, 'Hires steps'), (denoise, 'Hires denoising strength')]
        return [enabled, upscaler, scale, steps, denoise, width, height]

    def process(self, p, *values):
        if values[0] and not getattr(p, '_forge_img2img_hires_pass', False):
            hires.annotate(p, values)

    def postprocess_image_after_composite(self, p, pp, *values):
        if not values[0] or getattr(p, '_forge_img2img_hires_pass', False):
            return
        index = getattr(p, 'iteration', 0) * p.batch_size + getattr(p, 'batch_index', 0)
        pp.image = hires.refine(p, pp.image, values, index=index)

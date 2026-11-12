"""FIBO 2.0 inside Forge's native checkpoint, txt2img and img2img UI."""

import os
import sys

import gradio as gr

EXT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if EXT_DIR not in sys.path:
    sys.path.insert(0, EXT_DIR)

from fibo2 import glue, service, ui  # noqa: E402
from modules import script_callbacks, scripts, shared  # noqa: E402

if os.environ.get("FIBO2_DISABLE") != "1":
    glue.install()


class Fibo2Script(scripts.Script):
    def title(self):
        return "FIBO 2.0"

    def show(self, is_img2img):
        return scripts.AlwaysVisible

    def ui(self, is_img2img):
        tab = "img2img" if is_img2img else "txt2img"
        with gr.Group(elem_id=f"{tab}_fibo2_panel", visible=True):
            with gr.Accordion("FIBO 2.0 · native controls", open=True, elem_id=f"{tab}_fibo2_controls"):
                gr.Markdown(
                    "This panel is active only when a FIBO 2.0 model is selected in **Stable Diffusion checkpoint**. "
                    "The main prompt, negative prompt, dimensions, seed, batch, sampler, steps and CFG above are used directly.",
                    elem_id=f"{tab}_fibo2_model_note")
                gr.Markdown(
                    "FLUX 3 uses these ordered references, source size and exact mask controls. "
                    "Write a plain-language edit instruction; quoted lettering stays in its original language. "
                    "Use FLUX-compatible LoRAs from the normal LoRA picker; FIBO VGL and FIBO adapters are model-specific.",
                    elem_classes=["fibo2-flux-note"])
                mode = gr.Radio(["auto", "generate", "edit"], value="edit" if is_img2img else "auto",
                                label="Task (Auto edits when references are supplied)", visible=not is_img2img,
                                elem_id=f"{tab}_fibo2_mode")
                writer = gr.Radio(["off", "auto", "always"], value="off",
                                  label="Local Qwen3-VL prompt writer · any language → VGL",
                                  elem_id=f"{tab}_fibo2_writer")
                keep_size = gr.Checkbox(True, label="Single-image edit: keep source size (rounded to /16)",
                                        elem_id=f"{tab}_fibo2_keep_size")
                if is_img2img:
                    gr.Markdown("The main img2img image is `<image_1>`. Add up to three more references below.")
                    labels = ["Reference 2", "Reference 3", "Reference 4"]
                else:
                    gr.Markdown("For editing, add up to four ordered references (`<image_1>` … `<image_4>`).")
                    labels = ["Reference 1", "Reference 2", "Reference 3", "Reference 4"]
                with gr.Row():
                    refs = [gr.Image(type="pil", label=label, elem_id=f"{tab}_fibo2_reference_{i + 1}")
                            for i, label in enumerate(labels)]
                mask = gr.Image(type="pil", image_mode="L",
                                label="Optional exact mask · white edits, black pixels are preserved",
                                elem_id=f"{tab}_fibo2_mask")
                with gr.Accordion("Text rendering, FIBO LoRA and setup", open=False, elem_classes=["fibo2-only"]):
                    gr.Markdown(
                        "Typography uses VGL `text_render`. JSON edits also need `edit_instruction`. "
                        "Only FIBO-compatible LoRAs can be applied; SD/FLUX LoRAs use different layers."
                    )
                    loras = gr.Dropdown(choices=service.lora_choices(), multiselect=True,
                                        label="FIBO LoRAs from models/Lora", elem_id=f"{tab}_fibo2_loras")
                    weight = gr.Slider(-4, 4, value=1, step=0.05, label="LoRA strength",
                                       elem_id=f"{tab}_fibo2_lora_weight")
                    with gr.Row():
                        install = gr.Button("Install/verify isolated FIBO runtime")
                        download = gr.Button("Download selected FIBO model with aria2c")
                        refresh = gr.Button("Refresh FIBO status")
                    health = gr.Textbox(value=ui.status(), lines=5, label="FIBO status", interactive=False)

        install.click(lambda: ui.setup("install"), outputs=health)

        def download_selected():
            info = glue.selected_info()
            if info is None:
                raise gr.Error("Choose a FIBO 2.0 checkpoint first")
            return ui.setup("download", info.fibo2_variant, info.fibo2_precision)

        download.click(download_selected, outputs=health)
        refresh.click(ui.status, outputs=health, queue=False)
        return [mode, writer, keep_size, *refs, mask, loras, weight]

    def setup(self, p, mode, writer, keep_size, *args):
        # The last three values are mask, LoRAs and LoRA strength; all earlier
        # values are ordered reference images (four in txt2img, three in img2img).
        refs, mask, loras, weight = args[:-3], args[-3], args[-2], args[-1]
        p.fibo2_mode = mode
        p.fibo2_prompt_writer = writer
        p.fibo2_keep_source_size = bool(keep_size)
        p.fibo2_references = [x for x in refs if x is not None]
        p.fibo2_mask = mask
        p.fibo2_loras = loras or []
        p.fibo2_lora_weight = float(weight)


def on_app_started(demo, app):
    from fibo2 import api
    glue.route_callers()  # the API module exists by now
    api.register(app, getattr(shared.cmd_opts, "api_auth", None))

    @app.get("/fibo2/checkpoints")
    def fibo2_checkpoints():
        return glue.checkpoint_payload()


def on_ui_settings():
    shared.opts.add_option("fibo2_auto_defaults", shared.OptionInfo(
        True, "Set FIBO sampler / steps / CFG / 1024 defaults automatically and restore native values after",
        section=("fibo2", "FIBO 2.0")))


def on_unloaded():
    from fibo2.runner import runner
    runner.cancel()


if os.environ.get("FIBO2_DISABLE") != "1":
    script_callbacks.on_app_started(on_app_started)
    script_callbacks.on_ui_settings(on_ui_settings)
    script_callbacks.on_script_unloaded(on_unloaded)

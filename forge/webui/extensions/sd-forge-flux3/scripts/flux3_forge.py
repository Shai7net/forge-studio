"""FLUX 3 for Forge: installs the FLUX 3 loader (flux3/glue.py) and adds a small FLUX 3 panel to txt2img/img2img."""

import os
import sys

import gradio as gr

EXT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if EXT_DIR not in sys.path:
    sys.path.insert(0, EXT_DIR)

from flux3 import glue  # noqa: E402
from modules import script_callbacks, scripts, shared  # noqa: E402

if os.environ.get("FLUX3_DISABLE") != "1":  # FLUX3_DISABLE=1: start Forge without the patches (regression tests)
    glue.install()

WRITER_CHOICES = ["Auto", "Always", "Off"]


class Flux3Script(scripts.Script):
    def title(self):
        return "FLUX 3"

    def show(self, is_img2img):
        return scripts.AlwaysVisible

    def ui(self, is_img2img):
        tab = "img2img" if is_img2img else "txt2img"
        with gr.Accordion("FLUX 3", open=False, elem_id=f"{tab}_flux3_accordion"):
            gr.Markdown(
                "Used only while a FLUX 3 checkpoint is selected. Choosing one sets Euler, 16 steps, CFG 1 "
                "(the distilled Klein has no guidance) and 1024x1024; choosing another checkpoint brings your settings back.",
                elem_id=f"{tab}_flux3_note")
            writer = gr.Radio(WRITER_CHOICES, value="Auto", label="Prompt writer (Qwen3-VL expands short prompts, any language)",
                              elem_id=f"{tab}_flux3_writer", visible=not is_img2img)
            edit = gr.Radio(list(glue.EDIT_MODES), value=glue.EDIT_MODES[0], label="img2img with FLUX 3",
                            elem_id=f"{tab}_flux3_edit_mode", visible=is_img2img)
        self.infotext_fields = []
        return [writer, edit]

    def process(self, p, writer="Auto", edit_mode=None, *args):
        p.flux3_writer = writer if writer in WRITER_CHOICES else "Auto"
        if edit_mode in glue.EDIT_MODES:
            p.flux3_edit_mode = edit_mode

    def postprocess(self, p, processed, *args):
        captions = getattr(p, "flux3_captions", None)
        if captions and glue.active():
            processed.comments = (processed.comments or "") + "FLUX 3 caption: " + captions[0] + "\n"


def on_app_started(demo, app):
    @app.get("/flux3/checkpoints")
    def flux3_checkpoints():
        return {"titles": glue.flux3_titles(), "defaults": glue.DEFAULTS}

    @app.get("/flux3/status")
    def flux3_status():
        eng = glue.ENGINE
        if eng is None:
            return {"loaded": False}
        return {"loaded": True, "checkpoint": os.path.basename(eng.paths["model"]),
                "text_encoder": os.path.basename(eng.paths["text_encoder"]), "vae": os.path.basename(eng.paths["vae"]),
                "flow_map": bool(eng.model.flow_map), "on_card_gb": round(eng.on_card_bytes() / 2**30, 2),
                "stats": eng.stats}


def on_ui_settings():
    section = ("flux3", "FLUX 3")
    shared.opts.add_option("flux3_auto_defaults", shared.OptionInfo(
        True, "Set Euler / 16 steps / CFG 1 / 1024x1024 when a FLUX 3 checkpoint is chosen, and restore after", section=section))


script_callbacks.on_app_started(on_app_started)
script_callbacks.on_ui_settings(on_ui_settings)

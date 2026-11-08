"""Simulation 360: the tab, the settings, the routes, and the one hook into drawing (Tile X).

Everything the user sees is drawn by the page itself (javascript/sim360-*.js) inside one empty block, and it
talks to the routes in sim360/api.py. No Gradio components or events are added, so the rest of the UI, its
saved defaults and its event list stay as they were.
"""

import gradio as gr

from modules import script_callbacks, scripts, shared

from sim360 import NAME, api, tile


class Simulation360Hooks(scripts.Script):
    """A panorama is drawn as a ring. Only the jobs of this extension ask for it (they mark their own
    processing object), so nothing else in Forge is touched."""

    def title(self):
        return NAME

    def show(self, is_img2img):
        return scripts.AlwaysVisible

    def ui(self, is_img2img):
        return []

    def process(self, p, *args):
        if getattr(p, "sim360_tile_x", False):
            tile.apply(p.sd_model, getattr(p, "sim360_tile_start", 0))
            p.extra_generation_params["Tile X"] = True

    def postprocess(self, p, processed, *args):
        if tile.active():
            tile.restore()


def on_ui_tabs():
    with gr.Blocks(analytics_enabled=False) as tab:
        gr.HTML('<div class="sim360-mount"></div>', elem_id="sim360_host")
    return [(tab, NAME, "sim360")]


def on_ui_settings():
    section = ("sim360", NAME)
    shared.opts.add_option("sim360_projects_dir", shared.OptionInfo(
        "", "Folder for projects (empty: simulation-360 inside the webui folder)", section=section))
    shared.opts.add_option("sim360_exports_dir", shared.OptionInfo(
        "", "Folder for recorded videos and packed projects (empty: output\\simulation inside the webui folder)", section=section))
    shared.opts.add_option("sim360_kokoro_dir", shared.OptionInfo(
        "", "Kokoro's folder, for its voices (empty: models\\sim360-kokoro)", section=section))
    shared.opts.add_option("sim360_facefusion_dir", shared.OptionInfo(
        "", "FaceFusion's folder, for talking characters (empty: found by itself beside the Forge folder)", section=section))
    shared.opts.add_option("sim360_musetalk_dir", shared.OptionInfo(
        "", "MuseTalk's folder, for sharper lips on talking characters (empty: models\\sim360-musetalk)", section=section))
    shared.opts.add_option("sim360_ai4animation_dir", shared.OptionInfo(
        "", "AI4AnimationPy's folder, for real steps of walking figures (empty: models\sim360-ai4animation)", section=section))
    shared.opts.add_option("sim360_hunyuan3d_dir", shared.OptionInfo(
        "", "Hunyuan3D-2's folder, for 3D models (empty: Forge's own in models\\sim360-hunyuan3d, or the one Pinokio installed)", section=section))
    shared.opts.add_option("sim360_hunyuan3d_flags", shared.OptionInfo(
        "", "How Hunyuan3D-2 is started (empty: --h2 --turbo --profile 4, or what its flags.txt says; profile 5 takes less memory and is slower)", section=section))


def on_app_started(_demo, app):
    api.register(app)


def on_cfg_denoiser(_params):
    # every drawing passes here, whatever started it (Deforum too, which runs none of the tabs' scripts)
    if tile.everything:
        tile.keep_on(shared.sd_model)


script_callbacks.on_cfg_denoiser(on_cfg_denoiser)
script_callbacks.on_ui_tabs(on_ui_tabs)
script_callbacks.on_ui_settings(on_ui_settings)
script_callbacks.on_app_started(on_app_started)

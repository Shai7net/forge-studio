"""Forge Studio: the tab, its settings and its routes.

The page draws the whole editor itself (javascript/forge-studio-*.js) inside one empty block and talks to the
routes in forgestudio/api.py. No Gradio components or events are added, so the rest of the UI, its saved
defaults and its event queue stay as they were.
"""

import gradio as gr

from modules import script_callbacks, shared

from forgestudio import NAME, api


def on_ui_tabs():
    with gr.Blocks(analytics_enabled=False) as tab:
        gr.HTML('<div class="fstudio-mount"></div>', elem_id="fstudio_host")
    return [(tab, NAME, "forge_studio")]


def on_ui_settings():
    section = ("forge_studio", NAME)
    shared.opts.add_option("forge_studio_projects_dir", shared.OptionInfo(
        "", "Folder for projects (empty: forge-studio\\projects inside the webui folder)", section=section))
    shared.opts.add_option("forge_studio_exports_dir", shared.OptionInfo(
        "", "Folder for exported videos (empty: output\\forge-studio inside the webui folder)", section=section))
    shared.opts.add_option("forge_studio_library_extra", shared.OptionInfo(
        "", "More folders for the library, one per line", gr.Textbox, {"lines": 3}, section=section))


def on_app_started(_demo, app):
    api.register(app)


script_callbacks.on_ui_tabs(on_ui_tabs)
script_callbacks.on_ui_settings(on_ui_settings)
script_callbacks.on_app_started(on_app_started)

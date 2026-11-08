"""Time Strata: the tab, its settings and its routes.

Everything the user sees is drawn by the page itself (javascript/strata-*.js) inside one empty block, and it
talks to the routes in strata/api.py. No Gradio components or events are added, so the rest of the UI, its
saved defaults and its event queue stay as they were.
"""

import gradio as gr

from modules import script_callbacks, shared

from strata import NAME, api


def on_ui_tabs():
    with gr.Blocks(analytics_enabled=False) as tab:
        gr.HTML('<div class="strata-mount"></div>', elem_id="strata_host")
    return [(tab, NAME, "time_strata")]


def on_ui_settings():
    section = ("time_strata", NAME)
    shared.opts.add_option("strata_projects_dir", shared.OptionInfo(
        "", "Folder for projects (empty: time-strata inside the webui folder)", section=section))
    shared.opts.add_option("strata_exports_dir", shared.OptionInfo(
        "", "Folder for recorded videos (empty: output\\time-strata inside the webui folder)", section=section))


def on_app_started(_demo, app):
    api.register(app)


script_callbacks.on_ui_tabs(on_ui_tabs)
script_callbacks.on_ui_settings(on_ui_settings)
script_callbacks.on_app_started(on_app_started)

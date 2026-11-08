"""Video Studio: the tab. The engine runs in its own process (videostudio/engine/worker.py)."""

from modules import script_callbacks

from videostudio import NAME, ui


def on_ui_tabs():
    return [(ui.build(), NAME, "video_studio")]


script_callbacks.on_ui_tabs(on_ui_tabs)

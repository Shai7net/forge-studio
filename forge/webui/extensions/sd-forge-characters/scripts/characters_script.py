"""Characters: the tab (forgecharacters/ui.py)."""

import os
import sys

EXT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if EXT_DIR not in sys.path:
    sys.path.insert(0, EXT_DIR)

from modules import script_callbacks  # noqa: E402

from forgecharacters import NAME, ui  # noqa: E402


def on_ui_tabs():
    return [(ui.build(), NAME, "characters")]


script_callbacks.on_ui_tabs(on_ui_tabs)

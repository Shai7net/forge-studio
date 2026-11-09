"""Characters: consistent characters for Forge.

A character is described in any language (or taken from a reference picture), turned into a character-sheet prompt
by the 🌐 prompt writer (hebrew-translator), drawn by FLUX 3 Klein over a pose template, kept in a library grouped
by projects, and sent to Video Studio's Ingredients to Video together with a Wan prompt written for it.
"""

import os

NAME = "Characters"
EXT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEBUI_DIR = os.path.dirname(os.path.dirname(EXT_DIR))
LIBRARY_DIR = os.path.join(WEBUI_DIR, "output", "characters")
BUILTIN_TEMPLATE = os.path.join(EXT_DIR, "templates", "pose-sheet.png")
SETTINGS_FILE = os.path.join(EXT_DIR, "data", "settings.json")

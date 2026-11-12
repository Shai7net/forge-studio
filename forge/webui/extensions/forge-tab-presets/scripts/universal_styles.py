"""Register the curated style catalog before Forge builds its normal Styles dropdowns."""
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import forge_style_catalog
from modules import paths, script_callbacks, shared, styles


def register():
    forge_style_catalog.install(styles, shared, Path(paths.data_path) / 'tab-presets' / '_universal_style_hidden.json')


script_callbacks.on_before_ui(register)
script_callbacks.on_script_unloaded(lambda: forge_style_catalog.uninstall(styles, shared))

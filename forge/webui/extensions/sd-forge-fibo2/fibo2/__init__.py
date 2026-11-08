"""FIBO 2.0 integration; inference dependencies live in a separate process."""

from pathlib import Path

EXT_DIR = Path(__file__).resolve().parent.parent
WEBUI_DIR = EXT_DIR.parent.parent
HOME_DIR = WEBUI_DIR / "fibo2"
ENV_DIR = HOME_DIR / "env"
RUNTIME_DIR = HOME_DIR / "runtime" / "ComfyUI"
MODELS_DIR = WEBUI_DIR / "models" / "Fibo2"
OUTPUT_DIR = WEBUI_DIR / "outputs" / "fibo2"
COMFY_REVISION = "8a98ecf5864433064809ac1f2b52ccbf8b444471"
MODEL_REVISION = "4742d4bec410034142c8e65559e5f00c6f29eccb"


def python_executable():
    import os
    return ENV_DIR / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

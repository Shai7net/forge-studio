"""Resolve FIBO-only components without modifying other checkpoints or files."""

from pathlib import Path

from . import HOME_DIR, MODELS_DIR, WEBUI_DIR, RUNTIME_DIR, python_executable


MODEL_SHA256 = {
    ("dev", "int8"): "063dd397395809fd22234c6bd32932cfcb22619f4d2282bdbf1dc58c406fe325",
    ("edit-turbo", "int8"): "1f6fa7c499191f405467123f655f11762661eaa5bd479d1da8f161398ef72c3b",
}

DISPLAY_NAMES = {
    ("dev", "int8"): "FIBO 2.0 Dev (INT8)",
    ("edit-turbo", "int8"): "FIBO 2.0 Edit Turbo (INT8)",
    ("dev", "bf16"): "FIBO 2.0 Dev (BF16)",
    ("edit-turbo", "bf16"): "FIBO 2.0 Edit Turbo (BF16)",
}


def model_name(variant, precision="int8"):
    variant = {"dev": "dev", "edit-turbo": "edit_turbo"}[variant]
    suffix = {"int8": "int8_convrot", "bf16": "bf16"}[precision]
    return f"fibo2_{variant}_{suffix}.safetensors"


def complete_file(path):
    return path.is_file() and not Path(str(path) + ".aria2").exists() and path.stat().st_size > 1000


def components(variant, precision="int8"):
    model = MODELS_DIR / model_name(variant, precision)
    te_dir = WEBUI_DIR / "models" / "text_encoder"
    vae_dir = WEBUI_DIR / "models" / "VAE"
    text = next((p for p in [te_dir / "qwen3vl_4b_int8_convrot.safetensors", te_dir / "qwen3vl_4b_bf16.safetensors"] if complete_file(p)), None)
    vae = vae_dir / "flux2-vae.safetensors"
    missing = []
    if not complete_file(model):
        missing.append(str(model))
    if text is None:
        missing.append("models/text_encoder/qwen3vl_4b_int8_convrot.safetensors")
    if not complete_file(vae):
        missing.append(str(vae))
    if missing:
        raise FileNotFoundError("Download the missing FIBO components first: " + "; ".join(missing))
    return {"model": str(model.resolve()), "text_encoder": str(text.resolve()), "vae": str(vae.resolve())}


def status():
    available = {}
    for variant in ("dev", "edit-turbo"):
        for precision in ("int8", "bf16"):
            try:
                available[f"{variant}/{precision}"] = components(variant, precision)
            except FileNotFoundError:
                pass
    return {"runtime_installed": (HOME_DIR / "runtime-ready.json").is_file() and python_executable().is_file() and (RUNTIME_DIR / "comfy_extras/nodes_fibo2.py").is_file(),
            "models": available, "model_directory": str(MODELS_DIR)}

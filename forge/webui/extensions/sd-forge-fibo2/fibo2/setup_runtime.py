"""Explicit, reproducible setup; never run installers during Forge startup."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import venv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fibo2 import COMFY_REVISION, ENV_DIR, HOME_DIR, MODEL_REVISION, MODELS_DIR, RUNTIME_DIR, WEBUI_DIR, python_executable
from fibo2.models import model_name

FILES = {
    "fibo2_dev_int8_convrot.safetensors": (8408562474, "063dd397395809fd22234c6bd32932cfcb22619f4d2282bdbf1dc58c406fe325"),
    "fibo2_edit_turbo_int8_convrot.safetensors": (8408562474, "1f6fa7c499191f405467123f655f11762661eaa5bd479d1da8f161398ef72c3b"),
    "fibo2_dev_bf16.safetensors": (16452934194, "2a09866ff073cae9114f5fd93c0f530f84d41f74dc06e3d0c1372587bb3e9984"),
    "fibo2_edit_turbo_bf16.safetensors": (16452934194, "028f6cf13af75840d7cc7346a5784fe55887c6b9299d173c8271161ba5ccf7d0"),
    "qwen3vl_4b_int8_convrot.safetensors": (4858559240, "32de113cbf8282b02cbff0794b637b9d6676da09314a1db5a30bcea9bf4ee01c"),
    "flux2-vae.safetensors": (336211292, "868fe7b343cc8f3a19dbcfcafbc3d5f888802be3f89bd81b65b3621a066ce8f3"),
}


def command(args, **kwargs):
    print("Running:", " ".join(map(str, args)), flush=True)
    subprocess.run(list(map(str, args)), check=True, **kwargs)


def install():
    HOME_DIR.mkdir(parents=True, exist_ok=True)
    if sys.version_info < (3, 11):
        raise RuntimeError("Use Python 3.11+ to install the FIBO runtime; Forge's Python 3.10 stays unchanged")
    (HOME_DIR / "runtime-ready.json").unlink(missing_ok=True)
    if not (RUNTIME_DIR / ".git").exists():
        command(["git", "clone", "https://github.com/Comfy-Org/ComfyUI.git", RUNTIME_DIR])
    actual = subprocess.check_output(["git", "-C", str(RUNTIME_DIR), "rev-parse", "HEAD"], text=True).strip()
    if actual != COMFY_REVISION:
        command(["git", "-C", RUNTIME_DIR, "fetch", "origin", COMFY_REVISION])
        command(["git", "-C", RUNTIME_DIR, "checkout", "--detach", COMFY_REVISION])
    if not python_executable().is_file():
        venv.EnvBuilder(with_pip=True).create(ENV_DIR)
    uv = shutil.which("uv")
    prefix = [uv, "pip", "install", "--python", str(python_executable())] if uv else [str(python_executable()), "-m", "pip", "install"]
    env = os.environ.copy()
    env["UV_CACHE_DIR"] = str(HOME_DIR / "cache" / "uv")
    env["PIP_CACHE_DIR"] = str(HOME_DIR / "cache" / "pip")
    command(prefix + ["torch==2.9.1", "torchvision==0.24.1", "--index-url", "https://download.pytorch.org/whl/cu130"], env=env)
    constraints = Path(__file__).parent.parent / "runtime-constraints.txt"
    command(prefix + ["-r", str(RUNTIME_DIR / "requirements.txt"), "-c", str(constraints)], env=env)
    command([python_executable(), Path(__file__).with_name("worker.py"), "--check"])
    (HOME_DIR / "runtime-ready.json").write_text(json.dumps({"comfy_revision": COMFY_REVISION}), encoding="utf-8")
    print("FIBO runtime ready. Forge's environment was not modified.", flush=True)


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def download_file(folder, remote_folder, name):
    aria2 = shutil.which("aria2c")
    if not aria2:
        raise RuntimeError("aria2c was not found on PATH")
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / name
    size, sha256 = FILES[name]
    control = Path(str(destination) + ".aria2")
    if destination.exists() and not control.exists():
        print(f"Verifying existing {destination}", flush=True)
        if destination.stat().st_size == size and digest(destination) == sha256:
            return
        raise RuntimeError(f"Existing file failed FIBO verification; it was left untouched: {destination}")
    remaining = max(0, size - (destination.stat().st_size if destination.exists() else 0))
    if shutil.disk_usage(folder).free < remaining + 2 * 1024**3:
        raise RuntimeError("Not enough free disk space for this model plus 2 GB working space")
    command([aria2, "--continue=true", "--max-connection-per-server=8", "--split=8", "--min-split-size=4M",
             "--file-allocation=none", "--auto-file-renaming=false", "--allow-overwrite=false",
             "--connect-timeout=15", "--timeout=30", "--max-tries=5", "--retry-wait=3",
             "--summary-interval=30", "--enable-color=false", "--show-console-readout=false",
             f"--checksum=sha-256={sha256}", f"--dir={folder}", f"--out={name}",
             f"https://huggingface.co/Comfy-Org/Fibo-2.0/resolve/{MODEL_REVISION}/{remote_folder}/{name}"])
    if destination.stat().st_size != size or digest(destination) != sha256:
        raise RuntimeError(f"Downloaded file failed verification: {destination}")


def download(variant, precision):
    download_file(MODELS_DIR, "diffusion_models", model_name(variant, precision))
    download_file(WEBUI_DIR / "models" / "text_encoder", "text_encoders", "qwen3vl_4b_int8_convrot.safetensors")
    download_file(WEBUI_DIR / "models" / "VAE", "vae", "flux2-vae.safetensors")
    print("FIBO model, text encoder and VAE verified.", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["install", "download", "verify"])
    parser.add_argument("--variant", choices=["dev", "edit-turbo"], default="edit-turbo")
    parser.add_argument("--precision", choices=["int8", "bf16"], default="int8")
    args = parser.parse_args()
    if args.action == "install":
        install()
    elif args.action == "download":
        download(args.variant, args.precision)
    else:
        name = model_name(args.variant, args.precision)
        target = MODELS_DIR / name
        if not target.is_file() or target.stat().st_size != FILES[name][0] or digest(target) != FILES[name][1]:
            raise SystemExit(f"Verification failed: {target}")
        print(f"SHA256 verified: {name}")

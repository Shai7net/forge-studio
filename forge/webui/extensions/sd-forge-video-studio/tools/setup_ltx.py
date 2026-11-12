"""Install an isolated LTX worker without altering Forge or Wan dependencies."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from videostudio import ltx, WEBUI_DIR


def archive(repo, revision, target):
    target = target.resolve()
    owned_root = (ltx.LTX_HOME / 'runtime').resolve()
    if not target.is_relative_to(owned_root):
        raise ValueError('Runtime destination must stay within the LTX runtime directory')
    if target.is_dir():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent, prefix='ltx-setup-') as stage:
        stage = Path(stage).resolve()
        package = stage / 'source.zip'
        print('Downloading pinned runtime:', repo, revision, flush=True)
        urllib.request.urlretrieve(f'https://codeload.github.com/{repo}/zip/{revision}', package)
        with zipfile.ZipFile(package) as source:
            for item in source.infolist():
                if not (stage / item.filename).resolve().is_relative_to(stage):
                    raise ValueError('Unsafe runtime archive path')
            source.extractall(stage)
        folder = next(p for p in stage.iterdir() if p.is_dir())
        shutil.move(str(folder), str(target))


def main():
    home = ltx.LTX_HOME
    home.mkdir(parents=True, exist_ok=True)
    relative = 'Scripts/python.exe' if os.name == 'nt' else 'bin/python'
    candidates = [Path(WEBUI_DIR) / 'fibo2/env' / relative,
                  Path(WEBUI_DIR) / 'video-studio/env' / relative, Path(sys.executable)]
    base = next(p for p in candidates if p.is_file())
    python = ltx.python_executable()
    if not python.is_file():
        subprocess.run([str(base), '-m', 'venv', '--system-site-packages', str(home / 'env')], check=True)
    site = subprocess.check_output([str(python), '-c', 'import site; print(next(p for p in site.getsitepackages() if p.endswith("site-packages")))'], text=True).strip()
    base_site = subprocess.check_output([str(base), '-c', 'import site; print(next(p for p in site.getsitepackages() if p.endswith("site-packages")))'], text=True).strip()
    Path(site, 'forge-ltx-base.pth').write_text(base_site + '\n', encoding='utf-8')
    comfy, gguf = ltx.runtime_paths()
    if not (comfy / 'comfy_extras/nodes_lt_audio.py').is_file():
        comfy = home / 'runtime/ComfyUI'
        archive('Comfy-Org/ComfyUI', ltx.COMFY_REVISION, comfy)
    archive('city96/ComfyUI-GGUF', ltx.GGUF_REVISION, gguf)
    for required in [comfy / 'comfy_extras/nodes_lt.py', comfy / 'comfy_extras/nodes_lt_audio.py', gguf / 'nodes.py']:
        if not required.is_file():
            raise RuntimeError('Incomplete LTX runtime: ' + str(required) + '. Rename its runtime folder and run setup again.')
    # A compatible existing Comfy environment is shared read-only via .pth. Missing
    # dependencies install only in this venv; neither source environment is changed.
    minimal = ['gguf==0.18.0', 'sentencepiece', 'torchsde', 'safetensors', 'einops', 'scipy',
               'transformers>=4.50.3', 'aiohttp>=3.11.8', 'pyyaml', 'pydantic>=2',
               'comfy-aimdo==0.6.0', 'comfy-kitchen==0.2.40', 'av>=17', 'simpleeval>=1',
               'blake3', 'psutil', 'tqdm', 'imageio-ffmpeg']
    if 'fibo2' in base.parts:
        # uv does not include custom .pth directories in dependency resolution;
        # these requirements already exist in the compatible read-only base.
        minimal = ['gguf==0.18.0']
        uv = shutil.which('uv')
        cmd = [uv, 'pip', 'install', '--no-deps', '--python', str(python)] if uv else [str(python), '-m', 'pip', 'install', '--no-deps']
    else:
        # pip observes the read-only base .pth, including its CUDA Torch. Avoid
        # resolving a CPU wheel over an already installed working CUDA build.
        cmd = [str(python), '-m', 'pip', 'install']
    subprocess.run(cmd + minimal, check=True)
    subprocess.run([str(python), '-c', 'import torch; assert torch.cuda.is_available(); assert tuple(map(int, torch.__version__.split("+")[0].split(".")[:2])) >= (2, 7)'], check=True)
    ready = dict(comfy=str(comfy), comfy_revision=ltx.COMFY_REVISION,
                 gguf_revision=ltx.GGUF_REVISION, base_python=str(base))
    (home / 'runtime-ready.json').write_text(json.dumps(ready, indent=2), encoding='utf-8')
    print('LTX engine ready.', flush=True)


if __name__ == '__main__':
    main()

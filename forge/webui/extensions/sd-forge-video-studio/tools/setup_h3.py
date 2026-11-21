"""Install pinned MiniMax-H3 custom nodes without altering Forge or the shared Comfy runtime."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from videostudio import h3


def archive(repo, revision, target):
    target = target.resolve()
    owned_root = (h3.H3_HOME / 'runtime').resolve()
    if not target.is_relative_to(owned_root):
        raise ValueError('Runtime destination must stay within the MiniMax-H3 runtime directory')
    marker = target / '.forge-revision'
    if marker.is_file() and marker.read_text(encoding='utf-8').strip() == revision:
        return
    if (target / '.git').is_dir():
        current = subprocess.check_output(['git', '-C', str(target), 'rev-parse', 'HEAD'], text=True).strip()
        if current == revision:
            marker.write_text(revision + '\n', encoding='utf-8')
            return
    if target.exists():
        raise RuntimeError('Unrecognized MiniMax-H3 runtime folder: ' + str(target)
                           + '. Rename it and run setup again; it is never overwritten automatically.')
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent, prefix='h3-setup-') as stage_name:
        stage = Path(stage_name).resolve()
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
    marker.write_text(revision + '\n', encoding='utf-8')


def main():
    comfy, custom = h3.runtime_paths()
    if not (comfy / 'comfy_extras/nodes_minimax_h3.py').is_file():
        raise RuntimeError('The compatible shared ComfyUI runtime is missing. Set up FIBO 2.0 first.')
    if not h3.python_executable().is_file():
        raise RuntimeError('The compatible FIBO Python environment is missing. Set up FIBO 2.0 first.')
    archive('nicolab28/ComfyUI-ClipProj', h3.CLIPPROJ_REVISION, custom / 'ComfyUI-ClipProj')
    archive('xmarre/ComfyUI-Spectrum-MiniMax-H3', h3.SPECTRUM_REVISION,
            custom / 'ComfyUI-Spectrum-MiniMax-H3')
    subprocess.run([str(h3.python_executable()), '-c',
        'import torch, comfy_kitchen; assert torch.cuda.is_available()'], check=True)
    ready = dict(comfy=str(comfy), comfy_revision=h3.COMFY_REVISION,
                 clipproj_revision=h3.CLIPPROJ_REVISION, spectrum_revision=h3.SPECTRUM_REVISION,
                 workflow_revision=h3.WORKFLOW_REVISION)
    h3.H3_HOME.mkdir(parents=True, exist_ok=True)
    (h3.H3_HOME / 'runtime-ready.json').write_text(json.dumps(ready, indent=2), encoding='utf-8')
    print('MiniMax-H3 engine ready.', flush=True)


if __name__ == '__main__':
    main()

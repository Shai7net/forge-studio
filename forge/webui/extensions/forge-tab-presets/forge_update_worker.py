"""Standalone, standard-library-only updater. Copied outside the running app before installation."""
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import threading
import time
import urllib.request
import zipfile

REPOSITORY = 'Shai7net/forge-studio'
MANIFEST = 'forge-studio-code-manifest.json'
PRIVATE = {'models', 'outputs', 'output', 'venv', 'repositories', 'forge-studio', 'simulation-360',
           'time-strata', 'tab-presets', 'video-studio', 'cache', 'logs', 'embeddings'}


def safe_path(root, name):
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or any(':' in p or '\\' in p for p in path.parts):
        raise ValueError('Invalid update file path')
    parts = path.parts
    if '.git' in parts or not parts or parts[0] in ('system', '_cache'):
        raise ValueError('An update cannot replace private files')
    if parts[:2] == ('forge', 'webui'):
        if len(parts) > 2 and (parts[2] in PRIVATE or parts[2] in ('config.json', 'ui-config.json', 'styles.csv')):
            raise ValueError('An update cannot replace private files')
        if any(p in ('data', 'cache', 'models', 'outputs', 'output', '.git') for p in parts[3:]):
            raise ValueError('An update cannot replace extension data')
    target = (Path(root) / Path(*parts)).resolve()
    if not target.is_relative_to(Path(root).resolve()):
        raise ValueError('Update path escaped its installation')
    return target


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1 << 20), b''): value.update(block)
    return value.hexdigest()


def read_manifest(path):
    value = json.loads(Path(path).read_text(encoding='utf-8'))
    if value.get('repository') != REPOSITORY or value.get('format') != 1 or not isinstance(value.get('files'), dict):
        raise ValueError('Invalid Forge Studio update manifest')
    return value


def prepare_archive(root, archive, stage, tag):
    root, stage = Path(root), Path(stage)
    old = read_manifest(root / MANIFEST)
    with zipfile.ZipFile(archive) as package:
        manifests = [n for n in package.namelist() if n.count('/') == 1 and n.endswith('/' + MANIFEST)]
        if len(manifests) != 1:
            raise ValueError('The release has no unique code manifest')
        prefix = manifests[0].split('/')[0] + '/'
        manifest = json.loads(package.read(manifests[0]))
        if manifest.get('repository') != REPOSITORY or manifest.get('format') != 1 or manifest.get('version') != tag.lstrip('v'):
            raise ValueError('The downloaded release does not match the approved version')
        for name, sha in manifest['files'].items():
            dest = safe_path(stage, name)
            source = safe_path(root, name)
            if source.is_file():
                expected = old['files'].get(name, sha)
                if digest(source) != expected:
                    raise ValueError('Local source changes must be kept before updating: ' + name)
            dest.parent.mkdir(parents=True, exist_ok=True)
            with package.open(prefix + name) as incoming, dest.open('wb') as output:
                shutil.copyfileobj(incoming, output)
            if digest(dest) != sha:
                raise ValueError('Downloaded file checksum mismatch: ' + name)
        for name in old['files']:
            source = safe_path(root, name)
            if source.is_file() and digest(source) != old['files'][name]:
                raise ValueError('Local source changes must be kept before updating: ' + name)
        (stage / MANIFEST).write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return old, manifest


def backup_files(root, backup, old, new):
    for name in set(old['files']) | set(new['files']) | {MANIFEST}:
        source, target = safe_path(root, name), safe_path(backup, name)
        if source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def apply_files(root, stage, old, new):
    for name in set(old['files']) - set(new['files']):
        target = safe_path(root, name)
        if target.is_file(): target.unlink()
    for name in list(new['files']) + [MANIFEST]:
        target = safe_path(root, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(safe_path(stage, name), target)


def rollback_files(root, backup, old, new):
    for name in set(new['files']) - set(old['files']):
        target = safe_path(root, name)
        if target.is_file() and digest(target) == new['files'][name]: target.unlink()
    for name in list(old['files']) + [MANIFEST]:
        source = safe_path(backup, name)
        if source.is_file():
            target = safe_path(root, name); target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def main(operation):
    operation = Path(operation).resolve()
    config = json.loads((operation / 'request.json').read_text(encoding='utf-8'))
    root = Path(config['root']).resolve()
    state = {'job': operation.name, 'active': True, 'stage': 'preparing', 'message': 'Preparing the update', 'version': config['tag']}
    state_file = operation / 'status.json'
    lock = threading.Lock()
    def report(stage, message, **extra):
        with lock:
            state.update(stage=stage, message=message, **extra)
            temp = state_file.with_suffix('.tmp'); temp.write_text(json.dumps(state), encoding='utf-8'); os.replace(temp, state_file)
    def run(command, **kwargs):
        result = subprocess.run(command, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding='utf-8', errors='replace', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), **kwargs)
        with (operation / 'update.log').open('a', encoding='utf-8') as log: log.write(result.stdout)
        if result.returncode: raise RuntimeError(Path(command[0]).name + ' failed; see the local update log.')
        return result.stdout.strip()
    server = None
    action = threading.Event(); restart = [False]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            if self.path.startswith('/forge-workflow/updates/status'):
                body = json.dumps(state).encode(); content = 'application/json'
            else:
                body = b'<html><body><h2>Forge Studio update</h2><p>The update is running. Keep this window open.</p></body></html>'; content = 'text/html'
            self.send_response(200); self.send_header('Content-Type', content); self.end_headers(); self.wfile.write(body)
        def do_POST(self):
            try:
                if self.headers.get('Content-Type', '').split(';')[0] != 'application/json': raise ValueError()
                value = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
                if value.get('job') != operation.name or value.get('confirm') is not True or state['active']: raise ValueError()
                if self.path not in ('/forge-workflow/updates/restart', '/forge-workflow/updates/finish'): raise ValueError()
                restart[0] = self.path.endswith('/restart')
                self.send_response(200); self.end_headers(); self.wfile.write(b'{"ok":true}'); action.set()
            except Exception:
                self.send_response(400); self.end_headers()
    old = new = None; old_head = new_head = None; stopped = False
    env = dict(os.environ)
    env['PATH'] = os.pathsep.join(str(root / p) for p in ('system/uv', 'system/git/cmd', 'system/llama.cpp', 'system/ffmpeg')) + os.pathsep + env.get('PATH', '')
    env.update(GIT_TERMINAL_PROMPT='0', GCM_INTERACTIVE='never', UV_CACHE_DIR=str(root/'system/uv-cache'),
               UV_PYTHON_INSTALL_DIR=str(root/'system/python'), PIP_CACHE_DIR=str(root/'system/pip-cache'))
    git = str(root / 'system/git/cmd/git.exe') if (root / 'system/git/cmd/git.exe').is_file() else shutil.which('git')
    def setup():
        run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(root/'installer/install.ps1'),
             '-Yes', '-Models', 'none', '-NoShortcut'], env=env)
    try:
        report('downloading', 'Downloading the approved release')
        request = urllib.request.Request(f'https://api.github.com/repos/{REPOSITORY}/zipball/{config["tag"]}', headers={'User-Agent':'Forge-Studio-Updater'})
        archive = operation / 'release.zip'
        with urllib.request.urlopen(request, timeout=60) as source, archive.open('wb') as output:
            total, done = int(source.headers.get('Content-Length') or 0), 0
            for block in iter(lambda: source.read(1 << 20), b''):
                done += len(block)
                if done > 512 << 20: raise ValueError('The code archive is unexpectedly large')
                output.write(block)
                report('downloading', 'Downloading the approved release', bytes=done, total=total)
        stage, backup = operation / 'staged', operation / 'backup'
        old, new = prepare_archive(root, archive, stage, config['tag'])
        if (root / '.git').exists():
            if not git: raise RuntimeError('Git is not available')
            if run([git, 'status', '--porcelain', '--untracked-files=no'], env=env):
                raise ValueError('Local source changes must be kept before updating')
            origin = run([git, 'remote', 'get-url', 'origin'], env=env).lower().removesuffix('.git')
            if origin not in ('https://github.com/shai7net/forge-studio', 'git@github.com:shai7net/forge-studio'):
                raise ValueError('This repository is not Forge Studio')
            run([git, 'fetch', '--no-tags', 'origin', 'refs/tags/' + config['tag']], env=env)
            old_head = run([git, 'rev-parse', 'HEAD'], env=env); new_head = run([git, 'rev-parse', 'FETCH_HEAD'], env=env)
            run([git, 'merge-base', '--is-ancestor', old_head, new_head], env=env)
        backup_files(root, backup, old, new)
        report('installing', 'Closing Forge to install the update')
        time.sleep(2)
        for pid in config['pids']:
            subprocess.run(['taskkill', '/PID', str(int(pid)), '/T', '/F'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        stopped = True
        for attempt in range(30):
            try:
                ThreadingHTTPServer.allow_reuse_address = True
                server = ThreadingHTTPServer(('127.0.0.1', int(config['port'])), Handler); break
            except OSError: time.sleep(1)
        if server is None: raise RuntimeError('The update status server could not start')
        threading.Thread(target=server.serve_forever, daemon=True).start()
        if old_head:
            run([git, 'merge', '--ff-only', new_head], env=env)
        else:
            apply_files(root, stage, old, new)
        report('installing', 'Updating the installed components; models and projects are kept')
        setup()
        report('complete', 'Update installed. Restart Forge Studio to apply it.', active=False)
    except Exception as error:
        repaired = False
        if stopped and old and new:
            try:
                rollback_files(root, operation/'backup', old, new)
                if old_head and new_head:
                    run([git, 'update-ref', 'HEAD', old_head, new_head], env=env)
                    run([git, 'read-tree', old_head], env=env)
                setup(); repaired = True
            except Exception:
                pass
        report('error', str(error), active=False, rolled_back=repaired)
    if server:
        action.wait(300)
        server.shutdown(); server.server_close()
        if restart[0] and (state['stage'] == 'complete' or state.get('rolled_back')):
            import ctypes
            result = ctypes.windll.shell32.ShellExecuteW(None, 'open', str(root/'Forge Studio.bat'), None, str(root), 0)
            if result <= 32: report('error', 'The update is installed; start Forge Studio.bat manually.', active=False)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('operation'); main(parser.parse_args().operation)

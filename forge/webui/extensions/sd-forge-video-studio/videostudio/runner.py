"""Runs a video job in a worker process and turns its progress lines into events for the UI."""

import importlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from contextlib import contextmanager

from . import ENV_DIR, JOBS_DIR, WORKER
from . import childguard

_current = {"job": None}
_lock = threading.Lock()


@contextmanager
def generation_queue(lock, cancelled=None):
    """A caller with a cancellation token can leave Forge's queue without interrupting its current owner."""
    if cancelled is None:
        with lock:
            yield
        return
    # Forge's FIFOLock accepts blocking=False but has no timeout argument. Do not leave a blocked helper
    # thread behind when a queued request is cancelled.
    while not lock.acquire(False):
        if cancelled():
            raise RuntimeError("cancelled")
        time.sleep(0.05)
    try:
        if cancelled():
            raise RuntimeError("cancelled")
        yield
    finally:
        lock.release()


def engine_python():
    """The engine's own Python (PyTorch 2.9 + SageAttention + Triton) when set up, else Forge's own Python."""
    exe = os.path.join(ENV_DIR, "Scripts", "python.exe") if os.name == "nt" else os.path.join(ENV_DIR, "bin", "python")
    return exe if os.path.isfile(exe) else sys.executable


NEO_VENVS = [
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(ENV_DIR))), "..", "ForgeNeo-v2", "sd-webui-forge-neo", "venv"),
]


def setup_engine():
    """Make the engine environment as hard links of a Forge Neo venv (PyTorch 2.9 + SageAttention + Triton).
    Returns a message for the UI."""
    if os.path.isfile(engine_python()) and "video-studio" in engine_python():
        return "The fast engine is already set up."
    for src in NEO_VENVS:
        src = os.path.normpath(src)
        if os.path.isfile(os.path.join(src, "Scripts", "python.exe")):
            tool = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "link_env.py")
            r = subprocess.run([sys.executable, tool, src, ENV_DIR], capture_output=True, text=True)
            if r.returncode == 0 and os.path.isfile(engine_python()):
                return "Fast engine set up from " + src + " (hard links, no extra disk space)."
            return "Setting up failed: " + (r.stderr or r.stdout)[-400:]
    return ("No Forge Neo environment found to link. Without it the engine runs on Forge's own Python "
            "(works, but slower: no SageAttention).")


def free_forge_memory():
    """Let every memory manager Forge has loaded drop its models, so the card is empty for the worker."""
    names = ("backend.memory_management", "ldm_patched.modules.model_management")
    loaded = [n for n in names if n in sys.modules] or list(names)
    for name in loaded:
        try:
            manager = importlib.import_module(name)
            manager.unload_all_models()
            manager.soft_empty_cache()
        except Exception:
            pass
    flux = sys.modules.get('flux3.glue')
    engine = getattr(flux, 'ENGINE', None)
    if engine is not None:
        with engine.lock:
            engine.offload_all()
    try:
        import torch
        torch.cuda.empty_cache()
    except Exception:
        pass


class Job:
    """One worker process. Its output is read by a thread of its own, not by the page: a page that reloads,
    closes or is throttled in the background (a hidden tab dropped its connection mid-render, 2026-10-24) used to
    stop the reading, and the worker then died on its next write. Now the job always runs to the end and saves;
    any number of readers can follow `run()`."""

    def __init__(self, spec):
        self.id = time.strftime("%Y%m%d-%H%M%S")
        self.dir = os.path.join(JOBS_DIR, self.id)
        os.makedirs(self.dir, exist_ok=True)
        self.cancel_file = os.path.join(self.dir, "cancel")
        spec = dict(spec, cancel_file=self.cancel_file, work_dir=self.dir)
        self.spec = spec
        self.path = os.path.join(self.dir, "job.json")
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(spec, f, ensure_ascii=False, indent=1)
        self.proc = None
        self.log = []
        self.events = []
        self.ended = False
        self.cond = threading.Condition()

    def cancel(self):
        with open(self.cancel_file, "w") as f:
            f.write("cancel")

    def launch(self):
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"
        # Forge's own variables (HF_HOME on a missing drive, its CUDA_VISIBLE_DEVICES choices are fine) can confuse
        # a different Python; PYTHONPATH from Forge would put its old packages in front of the engine's.
        env.pop("PYTHONPATH", None)
        env.pop("PYTHONHOME", None)
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.proc = subprocess.Popen(
            [worker_python(self.spec), "-u", WORKER, self.path], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            env=env, cwd=self.dir, creationflags=flags, text=True, encoding="utf-8", errors="replace", bufsize=1)
        childguard.bind(self.proc)  # ends with Forge (childguard.py)
        threading.Thread(target=self._read, name=f"video-studio-{self.id}", daemon=True).start()

    def _push(self, event):
        with self.cond:
            self.events.append(event)
            self.cond.notify_all()

    def _read(self):
        last = None
        try:
            with open(os.path.join(self.dir, "worker.log"), "w", encoding="utf-8") as logf:
                for line in self.proc.stdout:
                    logf.write(line)
                    logf.flush()
                    if line.startswith("@@VS "):
                        try:
                            event = json.loads(line[5:])
                        except ValueError:
                            continue
                        last = event
                        if event.get("stage") == "done" and event.get("video"):
                            LAST["video"] = event["video"]
                        self._push(event)
                    else:
                        self.log.append(line.rstrip())
                        self.log = self.log[-60:]
            code = self.proc.wait()
            if last is None or last.get("stage") not in ("done", "error", "cancelled"):
                tail = "\n".join(self.log[-25:])
                self._push({"stage": "error", "error": f"The worker stopped (exit code {code}).", "trace": tail})
        finally:
            with self.cond:
                self.ended = True
                self.cond.notify_all()
            finish(self)

    def run(self):
        """Yields event dicts until the worker ends; the last one has stage done / error / cancelled.
        Closing this generator (the page went away) leaves the job running."""
        seen = 0
        while True:
            with self.cond:
                while seen >= len(self.events) and not self.ended:
                    self.cond.wait(1.0)
                batch = self.events[seen:]
                seen = len(self.events)
                ended = self.ended
            for event in batch:
                yield event
            if ended and seen >= len(self.events):
                return

    def latest(self):
        with self.cond:
            return self.events[-1] if self.events else None


LAST = {"video": None}


def worker_python(spec):
    if spec.get('backend') == 'ltx':
        from . import ltx
        if not ltx.runtime_ready():
            raise RuntimeError('Set up the LTX engine in Models before generating with LTX.')
        return str(ltx.python_executable())
    if spec.get('backend') == 'h3':
        from . import h3
        if not h3.runtime_ready():
            raise RuntimeError('Set up the MiniMax-H3 engine in Models before generating with MiniMax-H3.')
        return str(h3.python_executable())
    return engine_python()


def setup_ltx_engine():
    tool = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'tools', 'setup_ltx.py')
    result = subprocess.run([sys.executable, '-u', tool], capture_output=True, text=True,
                            encoding='utf-8', errors='replace', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise RuntimeError('LTX setup failed: ' + (result.stderr or result.stdout)[-1800:])
    return 'LTX engine ready.'


def fibo_runtime_ready():
    """FIBO 2.0's ComfyUI + Python 3.11 environment, which MiniMax-H3 runs in, set up at the shared revision."""
    from . import WEBUI_DIR, h3
    try:
        with open(os.path.join(WEBUI_DIR, 'fibo2', 'runtime-ready.json'), encoding='utf-8') as f:
            ready = json.load(f)
    except (OSError, ValueError):
        return False
    return isinstance(ready, dict) and ready.get('comfy_revision') == h3.COMFY_REVISION and h3.python_executable().is_file()


def _python311():
    """A Python 3.11 to build FIBO's environment with: the py launcher's, else uv's (Forge Studio puts uv on PATH and
    keeps its Pythons in its own folder; uv fetches 3.11 once). Forge's own 3.10 is never used for it."""
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    py = shutil.which('py')
    if py and subprocess.run([py, '-3.11', '-c', 'pass'], capture_output=True, creationflags=flags).returncode == 0:
        return subprocess.check_output([py, '-3.11', '-c', 'import sys; print(sys.executable)'], text=True,
                                       creationflags=flags).strip()
    uv = shutil.which('uv')
    if not uv:
        raise RuntimeError('Python 3.11 is needed once to build the engine: install it, or update Forge Studio (it brings uv).')
    found = subprocess.run([uv, 'python', 'find', '3.11'], capture_output=True, text=True, creationflags=flags)
    if found.returncode != 0:
        subprocess.run([uv, 'python', 'install', '3.11', '--no-bin', '--no-registry'], capture_output=True, creationflags=flags)
        found = subprocess.run([uv, 'python', 'find', '3.11'], capture_output=True, text=True, creationflags=flags)
    if found.returncode != 0 or not found.stdout.strip():
        raise RuntimeError('Could not get Python 3.11 through uv: ' + (found.stderr or '')[-300:])
    return found.stdout.strip()


def setup_fibo_runtime():
    """FIBO 2.0's own 'Install FIBO runtime': ComfyUI at the pinned revision and its Python 3.11 environment."""
    from . import WEBUI_DIR
    tool = os.path.join(WEBUI_DIR, 'extensions', 'sd-forge-fibo2', 'fibo2', 'setup_runtime.py')
    if not os.path.isfile(tool):
        raise RuntimeError('The FIBO 2.0 extension (sd-forge-fibo2) is not installed; MiniMax-H3 runs in its environment.')
    env = os.environ.copy()
    for key in ('PYTHONPATH', 'PYTHONHOME'):
        env.pop(key, None)
    env['PYTHONUTF8'] = '1'
    result = subprocess.run([_python311(), '-u', tool, 'install'], capture_output=True, text=True, env=env,
                            cwd=WEBUI_DIR,
                            encoding='utf-8', errors='replace', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise RuntimeError('Setting up FIBO 2.0\'s engine failed: ' + (result.stderr or result.stdout)[-1500:])


# "Download and set up this model": the downloads run in models.DOWNLOADS; the engine setup here, beside them
SETUP = {'running': False, 'text': '', 'error': ''}


def start_setup(preset):
    """In the background: what the chosen model needs before it can run (LTX: its engine; MiniMax-H3: FIBO 2.0's
    engine if missing, then its own nodes). Nothing for Wan."""
    from . import h3, ltx
    steps = []
    if ltx.is_ltx(preset) and not ltx.runtime_ready():
        steps.append(('LTX engine', setup_ltx_engine))
    if h3.is_h3(preset) and not h3.runtime_ready():
        if not fibo_runtime_ready():
            steps.append(("FIBO 2.0's engine (MiniMax-H3 runs in it; a few GB, several minutes)", setup_fibo_runtime))
        steps.append(('MiniMax-H3 engine', setup_h3_engine))  # it finds FIBO's engine ready by then
    if not steps or SETUP['running']:
        return

    def run():
        SETUP.update(running=True, error='')
        try:
            for label, step in steps:
                SETUP['text'] = 'Setting up ' + label + '…'
                step()
            SETUP['text'] = 'Engine ready.'
        except Exception as error:  # noqa: BLE001 - shown in the Models section
            SETUP.update(text='', error=str(error)[-1500:])
        finally:
            SETUP['running'] = False
    threading.Thread(target=run, daemon=True, name='video-studio-setup').start()


def setup_h3_engine():
    if not fibo_runtime_ready():  # MiniMax-H3 runs in FIBO 2.0's environment: build it first if it is not there
        setup_fibo_runtime()
    tool = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'tools', 'setup_h3.py')
    result = subprocess.run([sys.executable, '-u', tool], capture_output=True, text=True,
                            encoding='utf-8', errors='replace', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise RuntimeError('MiniMax-H3 setup failed: ' + (result.stderr or result.stdout)[-1800:])
    return 'MiniMax-H3 engine ready.'


def start(spec):
    with _lock:
        if _current["job"] is not None:
            raise RuntimeError("A video is already being made.")
        job = Job(spec)
        _current["job"] = job
    try:
        job.launch()
    except Exception:
        finish(job)
        raise
    return job


def finish(job):
    with _lock:
        if _current["job"] is job:
            _current["job"] = None


def current():
    return _current["job"]


def cancel_current():
    with _lock:
        job = _current["job"]
    if job is not None:
        job.cancel()
        return True
    return False


def busy():
    return _current["job"] is not None

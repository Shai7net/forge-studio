"""Subprocess lifecycle; no inference imports, sockets or model monkeypatches in Forge."""

import json
import os
from pathlib import Path
import secrets
import subprocess
import threading
import time
import uuid
from dataclasses import asdict

from . import EXT_DIR, HOME_DIR, OUTPUT_DIR, python_executable
from . import models


class Cancelled(RuntimeError):
    pass


class Runner:
    def __init__(self):
        self.lock = threading.Lock()
        self.cancel_event = threading.Event()
        self.process = None
        self.current_job = None
        self.last = {"stage": "idle"}

    def status(self):
        current = self.current_job
        if current is not None:
            try:
                return json.loads((current / "progress.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return {"stage": "starting"}
        return dict(self.last)

    def cancel(self):
        if not self.lock.locked():
            return {"cancelled": False, "stage": "idle"}
        self.cancel_event.set()
        return {"cancelled": True}

    def run(self, request, references=(), mask=None, loras=(), on_progress=None):
        request = request.validated(references, mask)
        components = models.components(request.variant, request.precision)
        if not models.status()["runtime_installed"]:
            raise RuntimeError("Install the FIBO runtime first (Setup in the FIBO tab)")
        if not self.lock.acquire(blocking=False):
            raise RuntimeError("A FIBO job is already running")
        self.cancel_event.clear()
        jobdir = HOME_DIR / "jobs" / uuid.uuid4().hex
        logpath = HOME_DIR / "logs" / f"{jobdir.name}.log"
        try:
            jobdir.mkdir(parents=True, exist_ok=False)
            logpath.parent.mkdir(parents=True, exist_ok=True)
            self.current_job = jobdir
            seed = secrets.randbelow(2**32) if request.seed == -1 else int(request.seed)
            paths = []
            for index, reference in enumerate(references):
                path = jobdir / f"reference-{index + 1}.png"
                reference.convert("RGB").save(path)
                paths.append(str(path))
            maskpath = None
            if mask is not None:
                maskpath = str(jobdir / "mask.png")
                mask.convert("L").save(maskpath)
            job = {"request": asdict(request), "seed": seed, "references": paths, "mask": maskpath,
                   "loras": list(loras), "components": components, "output_dir": str(OUTPUT_DIR)}
            jobpath = jobdir / "request.json"
            jobpath.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
            env = os.environ.copy()
            for key in ("PYTHONPATH", "PYTHONHOME"):
                env.pop(key, None)
            env.update(PYTHONUTF8="1", PYTHONUNBUFFERED="1", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
            with logpath.open("w", encoding="utf-8") as logfile:
                args = [str(python_executable()), "-u", str(EXT_DIR / "fibo2" / "worker.py"), "--job", str(jobpath)]
                self.process = subprocess.Popen(args, cwd=str(HOME_DIR), env=env, stdout=logfile,
                                                stderr=subprocess.STDOUT,
                                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                while self.process.poll() is None:
                    if self.cancel_event.wait(0.25):
                        self.process.terminate()
                        try:
                            self.process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            self.process.kill()
                            self.process.wait()
                        raise Cancelled("FIBO stopped; GPU memory released")
                    if on_progress:
                        on_progress(self.status())
                if self.process.returncode:
                    tail = logpath.read_text(encoding="utf-8", errors="replace")[-2500:]
                    code = self.process.returncode
                    raise RuntimeError(f"FIBO worker failed (exit {code}, 0x{code & 0xffffffff:08X}). Log: {logpath}\n{tail}")
            response = json.loads((jobdir / "result.json").read_text(encoding="utf-8"))
            response["log"] = str(logpath)
            self.last = {"stage": "done", **response}
            return response
        except Cancelled:
            self.last = {"stage": "cancelled", "log": str(logpath)}
            raise
        except Exception as exc:
            self.last = {"stage": "error", "error": str(exc), "log": str(logpath)}
            raise
        finally:
            # A host-side callback error must not leave a detached CUDA worker alive.
            if self.process is not None and self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait()
            self.process = None
            self.current_job = None
            self.lock.release()


runner = Runner()

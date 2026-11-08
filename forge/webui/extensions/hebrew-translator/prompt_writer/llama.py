"""The writer: Gemma 4 E4B (Google, 2026; models/LLM/gemma-4-E4B_q4_0-it.gguf, 4.8 GB QAT q4_0) run by llama.cpp's
llama-server in its own process (the llama.cpp installed with winget, Vulkan build).

Chosen 2026-10-27 after a test of hard Hebrew words (fox/wolf, falafel, sidelocks, kippah, burekas, hedgehog...):
the 4B Qwen3-VL inside FLUX 3 read "פאות וכיפה" as "horns and a helmet" and "שועל" as a wolf; NLLB and DictaLM
3.0 1.7B missed others; DictaLM 3.0 12B read Hebrew well but its 7 GB do not fit the card next to Forge (0.8-1.4
words a second). Gemma got 10 of 12 words right and writes a prompt in about 1.5 s.

It takes about 2.9 GB of the card while up. It is started for a write (about 6 s), stays up a short while so 👎 and
🔄 answer at once, and is stopped when that time passes or as soon as Forge, Video Studio or anything else takes
Forge's queue (scripts/translator.py). The very first prompt after installing (or after a driver update) waits
about 45 s while Vulkan compiles its shaders; the driver keeps them, so that happens once.
"""

import glob
import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request

_THINK = re.compile(r"<think>.*?</think>", re.S)
MODEL_PATTERNS = ("gemma-4-E4B*.gguf",)


def find_server():
    # Forge Studio's installer puts llama.cpp in <root>/system/llama.cpp (<root> holds forge/webui)
    webui = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    bundled = os.path.join(webui, "..", "..", "system", "llama.cpp", "llama-server.exe")
    if os.path.isfile(bundled):
        return os.path.normpath(bundled)
    exe = shutil.which("llama-server")
    if exe:
        return exe
    base = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Packages")
    found = sorted(glob.glob(os.path.join(base, "ggml.llamacpp*", "llama-server.exe")))
    return found[-1] if found else None


def find_model(models_dir):
    for pattern in MODEL_PATTERNS:
        found = sorted(p for p in glob.glob(os.path.join(models_dir, "LLM", pattern)) if "mmproj" not in p.lower())
        if found:
            return found[0]
    return None


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def nvidia_device(exe):
    """llama.cpp's name for the NVIDIA card: on this laptop Vulkan0 is the Intel graphics, which would put the
    model in shared system memory."""
    try:
        out = subprocess.run([exe, "--list-devices"], capture_output=True, text=True, timeout=60,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        for line in (out.stdout + out.stderr).splitlines():
            m = re.match(r"\s*(\w+\d+):\s*(.*)", line)
            if m and re.search(r"nvidia|geforce|rtx", m.group(2), re.I):
                return m.group(1)
    except Exception:  # noqa: BLE001
        pass
    return None


class LlamaWriter:
    def __init__(self, exe, model_path, log_path=None):
        self.exe, self.model_path, self.log_path = exe, model_path, log_path
        self.proc = None
        self.port = None
        self.device = None
        self.lock = threading.RLock()
        self.used_at = 0.0
        self._log_started = False

    @property
    def name(self):
        return os.path.basename(self.model_path)

    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self, timeout=240):
        with self.lock:
            if self.running():
                return
            if self.device is None:
                self.device = nvidia_device(self.exe) or ""
            self.port = _free_port()
            args = [self.exe, "-m", self.model_path, "--host", "127.0.0.1", "--port", str(self.port),
                    "-c", "4096", "-np", "1", "--fit", "on", "--jinja", "--no-webui", "--reasoning-budget", "0"]
            if self.device:
                args += ["-dev", self.device]
            log = subprocess.DEVNULL
            if self.log_path:
                log = open(self.log_path, "a" if self._log_started else "w", encoding="utf8")
                self._log_started = True
            t0 = time.time()
            self.proc = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT,
                                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            while time.time() - t0 < timeout:
                if self.proc.poll() is not None:
                    self.proc = None
                    raise RuntimeError(f"llama-server stopped while loading {self.name}"
                                       + (f" (see {self.log_path})" if self.log_path else ""))
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health", timeout=2) as r:
                        if r.status == 200:
                            print(f"[Prompt writer] {self.name} on {self.device or 'the default device'}, "
                                  f"ready in {time.time() - t0:.1f} s", flush=True)
                            return
                except (urllib.error.URLError, ConnectionError, OSError):
                    pass
                time.sleep(0.25)
            self.stop()
            raise RuntimeError(f"llama-server did not get ready in {timeout} s")

    def stop(self, why=""):
        with self.lock:
            if self.proc is None:
                return
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)
            self.proc = None
            print(f"[Prompt writer] {self.name} left the card{f' ({why})' if why else ''}", flush=True)

    def chat(self, system, user, turns=(), temperature=0.0, seed=0, max_new=320):
        messages = [{"role": "system", "content": system}]
        for u, a in turns or ():
            messages += [{"role": "user", "content": u}, {"role": "assistant", "content": a}]
        messages.append({"role": "user", "content": user})
        body = {"messages": messages, "max_tokens": max_new, "temperature": temperature, "seed": int(seed) & 0x7FFFFFFF,
                "top_k": 40, "top_p": 0.95, "chat_template_kwargs": {"enable_thinking": False}}
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/v1/chat/completions",
                                     data=json.dumps(body).encode("utf8"), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=300) as r:
            data = json.loads(r.read())
        text = data["choices"][0]["message"].get("content") or ""
        return _THINK.sub("", text).strip()

    def write(self, system, user, turns=(), temperature=0.0, seed=0, max_new=320):
        """One answer. The server stays up afterwards; the caller stops it (idle time, or Forge wants the card)."""
        with self.lock:
            self.start()
            try:
                return self.chat(system, user, turns, temperature, seed, max_new)
            finally:
                self.used_at = time.time()

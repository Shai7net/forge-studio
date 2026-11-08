"""The likeness pass after Replace a character: FaceFusion swaps each character's real face (from the picture) onto
the person who now dances in their place.

Wan Animate draws a 480p dancer's face at about 40 pixels and copies the driving face's expression onto it, so the
characters came out as generic look-alikes (2026-10-25); HyperSwap + GFPGAN from the picture gave the real faces.
Each face is swapped only where it matches a reference face taken on the first frame (position = the person's number
from the left), with a strict detector score: at 0.35 a "face" in a stone wall got one too.
"""

import os
import subprocess

from . import WEBUI_DIR


def root():
    """FaceFusion's folder (the one with facefusion.py) and its Python, or (None, None)."""
    home = os.path.abspath(os.path.join(WEBUI_DIR, "..", ".."))
    for folder in (os.path.join(home, "facefusion", "facefusion"), os.path.join(home, "facefusion")):
        if os.path.isfile(os.path.join(folder, "facefusion.py")):
            for relative in (("venv", "Scripts", "python.exe"), (".venv", "Scripts", "python.exe"), ("venv", "bin", "python")):
                python = os.path.join(folder, *relative)
                if os.path.isfile(python):
                    return folder, python
    return None, None


def available():
    return root()[0] is not None


def command(source, video, target, position, work):
    folder, python = root()
    settings = os.path.join(work, "facefusion.ini")
    open(settings, "w").close()  # FaceFusion's own defaults, whatever the user's FaceFusion is set to
    return [python, "facefusion.py", "headless-run", "--config-path", settings,
            "--temp-path", os.path.join(work, "ff-temp"), "--jobs-path", os.path.join(work, "ff-jobs"),
            "--processors", "face_swapper", "face_enhancer",
            "--face-swapper-model", "hyperswap_1a_256", "--face-swapper-pixel-boost", "512x512",
            "--face-enhancer-model", "gfpgan_1.4", "--face-enhancer-blend", "70",
            "-s", source, "-t", video, "-o", target,
            "--face-selector-mode", "reference", "--face-selector-order", "left-right",
            "--reference-face-position", str(int(position)), "--reference-frame-number", "0",
            "--reference-face-distance", "0.45", "--face-detector-model", "yolo_face", "--face-detector-score", "0.6",
            "--output-video-encoder", "libx264", "--output-video-quality", "95",
            "--execution-providers", "cuda", "cpu", "--log-level", "info"], folder


def start(source, video, target, position, work):
    """Starts one swap; the caller polls the process and reads `log(work)` when it fails. FaceFusion keeps the
    video's sound. Its output goes to a file: into an unread pipe it filled the buffer and stopped (2026-10-25)."""
    os.makedirs(work, exist_ok=True)
    cmd, folder = command(source, video, target, position, work)
    with open(log(work), "w", encoding="utf-8") as out:
        return subprocess.Popen(cmd, cwd=folder, stdout=out, stderr=subprocess.STDOUT,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def log(work):
    return os.path.join(work, "facefusion.log")

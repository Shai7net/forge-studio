"""Sharper frames with Real-ESRGAN, through the same small program Deforum uses for its upscaling
(realesrgan-ncnn-vulkan: runs on the graphics card through Vulkan, a folder of pictures at a time).

Deforum keeps it in models\\Deforum\\realesrgan_ncnn; when it is not there it is downloaded once, from the same
release and checked against the same checksum, into models\\time-strata\\realesrgan_ncnn. Its models:
- realesr-animevideov3 (x2, x3, x4): small and fast, made for video; clean lines, steady from frame to frame.
- realesrgan-x4plus (x4): for photographs and realistic pictures; the most detail, the slowest.
- realesrgan-x4plus-anime (x4): for drawn and anime pictures.
A x4 model asked for x2 upscales x4 and is then brought down to x2 (sharper than a plain x2 enlargement).
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile

import cv2
import numpy as np

from . import jobs, media

MODELS = {"realesr-animevideov3": (2, 3, 4), "realesrgan-x4plus": (4,), "realesrgan-x4plus-anime": (4,)}
CHOICES = ["realesr-animevideov3:2", "realesr-animevideov3:4", "realesrgan-x4plus:2", "realesrgan-x4plus:4",
           "realesrgan-x4plus-anime:2", "realesrgan-x4plus-anime:4"]
EXE = "realesrgan-ncnn-vulkan.exe" if os.name == "nt" else "realesrgan-ncnn-vulkan"
_RELEASES = {
    "win32": ("https://github.com/hithereai/Real-ESRGAN/releases/download/real-esrgan-ncnn-windows/realesrgan-ncnn-windows.zip",
              "1d073f520a4a3f6438a500fea88407964da6d4a87489719bedfa7445b76c019fdd95a5c39576ca190d7ac22c906b33d5250a6f48cb7eda2b6af3e86ec5f09dfc"),
    "linux": ("https://github.com/hithereai/Real-ESRGAN/releases/download/real-esrgan-ncnn-linux/realesrgan-ncnn-linux.zip",
              "df44c4e9a1ff66331079795f018a67fbad8ce37c4472929a56b5a38440cf96982d6e164a086b438c3d26d269025290dd6498bd50846bda8691521ecf8f0fafdf"),
    "darwin": ("https://github.com/hithereai/Real-ESRGAN/releases/download/real-esrgan-ncnn-mac/realesrgan-ncnn-mac.zip",
               "65f09472025b55b18cf6ba64149ede8cded90c20e18d35a9edb1ab60715b383a6ffbf1be90d973fc2075cf99d4cc1411fbdc459411af5c904f544b8656111469"),
}
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def parse(choice):
    """'realesr-animevideov3:2' -> ('realesr-animevideov3', 2); nothing asked -> None."""
    if not choice:
        return None
    name, _, scale = str(choice).partition(":")
    if name not in MODELS:
        raise jobs.JobError(f"unknown upscaling model: {name}")
    scale = int(scale or MODELS[name][0])
    if scale not in (2, 3, 4):
        raise jobs.JobError(f"upscaling works x2, x3 or x4, not x{scale}")
    return name, scale


def _models_root():
    try:
        from modules import paths
        return paths.models_path
    except Exception:
        webui = os.environ.get("STRATA_WEBUI") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        return os.path.join(webui, "models")


def homes():
    root = _models_root()
    return [os.path.join(root, "Deforum", "realesrgan_ncnn"), os.path.join(root, "time-strata", "realesrgan_ncnn")]


def find():
    for home in homes():
        if os.path.isfile(os.path.join(home, EXE)):
            return os.path.join(home, EXE)
    return None


def _param(folder, name, scale):
    return os.path.join(folder, "models", f"{name}-x{scale}.param" if name == "realesr-animevideov3" else f"{name}.param")


def available():
    """{ready, path, models: [{name, scales}]} - what is on disk now."""
    exe = find()
    models = []
    if exe:
        folder = os.path.dirname(exe)
        for name, scales in MODELS.items():
            ready = [s for s in scales if os.path.isfile(_param(folder, name, s))]
            if ready:
                models.append({"name": name, "scales": ready})
    return {"ready": bool(exe), "path": exe, "models": models}


def download(step=None):
    """The program and its models, from the release Deforum uses, checked by its SHA-512."""
    url, digest = _RELEASES.get("win32" if os.name == "nt" else ("darwin" if sys.platform == "darwin" else "linux"))
    folder = homes()[1]
    os.makedirs(folder, exist_ok=True)
    archive = os.path.join(folder, os.path.basename(url))
    if step:
        step("downloading Real-ESRGAN (about 45 MB, once)")
    import urllib.request
    with urllib.request.urlopen(url, timeout=60) as answer, open(archive, "wb") as file:
        shutil.copyfileobj(answer, file)
    with open(archive, "rb") as file:
        if hashlib.sha512(file.read()).hexdigest() != digest:
            os.remove(archive)
            raise jobs.JobError(f"the Real-ESRGAN download is damaged; download {url} and unpack it into {folder}")
    with zipfile.ZipFile(archive) as packed:
        packed.extractall(folder)
    os.remove(archive)
    exe = os.path.join(folder, EXE)
    if os.name != "nt" and os.path.isfile(exe):
        os.chmod(exe, 0o755)
    return exe


def upscale_folder(source, target, choice, job=None, share=(0.0, 1.0)):
    """Every picture of `source` made larger into `target` (PNG, same names); a x4 model asked for less is brought
    down to the asked size. Returns the number of pictures. `job` hears the progress (and may cancel)."""
    name, scale = parse(choice)
    exe = find() or download(job.step if job else None)
    native = scale if name == "realesr-animevideov3" else 4
    pictures = sorted(n for n in os.listdir(source) if n.lower().endswith((".png", ".jpg", ".jpeg", ".webp")))
    if not pictures:
        raise jobs.JobError("there are no pictures to upscale")
    os.makedirs(target, exist_ok=True)
    for n in os.listdir(target):
        os.remove(os.path.join(target, n))
    command = [exe, "-i", source, "-o", target, "-n", name, "-s", str(native), "-f", "png"]
    started = time.time()
    # (what it says goes to a file: it reports every tile, and a pipe read only at the end fills up after a few
    # pictures and stops it - 16 of 207 frames, then nothing)
    said_path = os.path.join(os.path.dirname(os.path.abspath(target)), "realesrgan.log")
    said_file = open(said_path, "wb")
    process = subprocess.Popen(command, cwd=os.path.dirname(exe), stdout=said_file, stderr=subprocess.STDOUT,
                               stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)
    try:
        while process.poll() is None:
            made = len(os.listdir(target))
            if job:
                left = (time.time() - started) / max(1, made) * (len(pictures) - made)
                try:
                    job.step(f"upscaling with {name} x{scale}: picture {made} of {len(pictures)} (about {int(left)} s left)",
                             share[0] + (share[1] - share[0]) * made / len(pictures))
                except jobs.Cancelled:
                    process.kill()
                    raise
            time.sleep(0.5)
    finally:
        if process.poll() is None:
            process.kill()
        said_file.close()
    with open(said_path, "rb") as file:
        said = file.read()[-20000:].decode("utf8", "replace")
    said = "\n".join(line for line in said.splitlines() if line.strip() and not line.strip().endswith("%"))   # (not its tiles)
    made = sorted(os.listdir(target))
    if process.returncode != 0 or len(made) < len(pictures):
        raise jobs.JobError(f"Real-ESRGAN stopped after {len(made)} of {len(pictures)} pictures: {said.strip()[-300:]}")
    if native != scale:
        for n in made:
            path = os.path.join(target, n)
            picture = media.imread(path, cv2.IMREAD_UNCHANGED)
            h, w = picture.shape[:2]
            smaller = cv2.resize(picture, (max(1, w * scale // native), max(1, h * scale // native)), interpolation=cv2.INTER_AREA)
            media.imwrite(path, smaller)
    return len(pictures)


def upscaled_frames(job, folder, count, frames, choice, share=(0.0, 0.2)):
    """The source frames made larger once and kept in `folder` (a chain's own upscale folder), so placing them again
    reads the same frames; a frame whose enlarged copy is missing (drawn again) is enlarged again, alone.
    -> a function giving them again, as RGBA arrays."""
    done_path = os.path.join(folder, "done.json")
    wanted = {"choice": choice, "count": count}
    out = os.path.join(folder, "out")
    try:
        with open(done_path, "r", encoding="utf8") as file:
            same = json.load(file) == wanted
    except (OSError, ValueError):
        same = False
    have = set(os.listdir(out)) if same and os.path.isdir(out) else set()
    missing = [n for n in range(count) if f"{n:06d}.png" not in have]
    if missing:
        source, made, alphas = os.path.join(folder, "in"), os.path.join(folder, "made"), os.path.join(folder, "alpha")
        for path in (source, alphas):
            shutil.rmtree(path, ignore_errors=True)
            os.makedirs(path)
        asked = set(missing)
        for n, frame in enumerate(frames()):
            if n >= count:
                break
            if n not in asked:
                continue
            # the program gets colour only (its four-channel path garbles small pictures: colours off, alpha
            # 59); a frame's own transparency is enlarged on its own and put back
            media.imwrite(os.path.join(source, f"{n:06d}.png"), cv2.cvtColor(np.ascontiguousarray(frame[..., :3]), cv2.COLOR_RGB2BGR))
            if frame.shape[2] == 4 and frame[..., 3].min() < 255:
                media.imwrite(os.path.join(alphas, f"{n:06d}.png"), frame[..., 3])
            if job and n % 16 == 0:
                job.step(f"setting out frame {n + 1} of {count} for upscaling", share[0] + (share[1] - share[0]) * 0.1 * n / count)
        from . import cut
        cut.free_graphics_memory()           # (Vulkan shares the card with Forge's models)
        upscale_folder(source, made, choice, job, (share[0] + (share[1] - share[0]) * 0.1, share[1]))
        for name in os.listdir(alphas):
            colour = media.imread(os.path.join(made, name), cv2.IMREAD_COLOR)
            alpha = cv2.resize(media.imread(os.path.join(alphas, name), cv2.IMREAD_GRAYSCALE), (colour.shape[1], colour.shape[0]),
                               interpolation=cv2.INTER_CUBIC)
            media.imwrite(os.path.join(made, name), np.dstack([colour, alpha]))
        if not same:
            shutil.rmtree(out, ignore_errors=True)
        os.makedirs(out, exist_ok=True)
        for name in os.listdir(made):
            os.replace(os.path.join(made, name), os.path.join(out, name))
        for path in (source, made, alphas):
            shutil.rmtree(path, ignore_errors=True)
        with open(done_path, "w", encoding="utf8") as file:
            json.dump(wanted, file)
    names = [f"{n:06d}.png" for n in range(count)]

    def again():
        for name in names:
            picture = media.imread(os.path.join(out, name), cv2.IMREAD_UNCHANGED)
            if picture.ndim == 2:
                picture = cv2.cvtColor(picture, cv2.COLOR_GRAY2BGRA)
            elif picture.shape[2] == 3:
                picture = cv2.cvtColor(picture, cv2.COLOR_BGR2BGRA)
            yield cv2.cvtColor(picture, cv2.COLOR_BGRA2RGBA)
    return again

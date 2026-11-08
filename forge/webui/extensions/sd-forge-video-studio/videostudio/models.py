"""The model files Video Studio knows, where they live, and fetching the missing ones (aria2c when present)."""

import glob
import os
import shutil
import subprocess
import threading
import time
import urllib.request

from . import MODELS_DIR, WEBUI_DIR

HF = "https://huggingface.co/"
COMFY = HF + "Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/"
LIGHTX2V = HF + "Abiray/Wan2.2-LightX2V-260412-4STEP-GGUF/resolve/main/"

FILES = {
    "umt5": dict(path="text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors", size=6735906897,
                 url=COMFY + "text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors"),
    "vae21": dict(path="vae/wan_2.1_vae.safetensors", size=253815318,
                  url=COMFY + "vae/wan_2.1_vae.safetensors"),
    "i2v_high_q3": dict(path="diffusion/wan2.2_i2v_A14b_high_noise_lightx2v_4step_260412-Q3_K_M.gguf", size=7185947299,
                        url=LIGHTX2V + "high_noise_260412/wan2.2_i2v_A14b_high_noise_lightx2v_4step_720p_260412-Q3_K_M.gguf"),
    "i2v_low_q3": dict(path="diffusion/wan2.2_i2v_A14b_low_noise_lightx2v_4step_260412-Q3_K_M.gguf", size=7185947299,
                       url=LIGHTX2V + "low_noise_260412/wan2.2_i2v_A14b_low_noise_lightx2v_4step_720p_260412-Q3_K_M.gguf"),
    "i2v_high_q4": dict(path="diffusion/wan2.2_i2v_A14b_high_noise_lightx2v_4step_260412-Q4_K_M.gguf", size=9661569699,
                        url=LIGHTX2V + "high_noise_260412/wan2.2_i2v_A14b_high_noise_lightx2v_4step_720p_260412-Q4_K_M.gguf"),
    "i2v_low_q4": dict(path="diffusion/wan2.2_i2v_A14b_low_noise_lightx2v_4step_260412-Q4_K_M.gguf", size=9661569699,
                       url=LIGHTX2V + "low_noise_260412/wan2.2_i2v_A14b_low_noise_lightx2v_4step_720p_260412-Q4_K_M.gguf"),
    # Character replacement (Wan 2.2 Animate 14B) and what prepares its inputs
    "animate_q3": dict(path="animate/Wan2.2-Animate-14B-Q3_K_S.gguf", size=7969675072,
                       url=HF + "QuantStack/Wan2.2-Animate-14B-GGUF/resolve/main/Wan2.2-Animate-14B-Q3_K_S.gguf"),
    "animate_relight": dict(path="animate/WanAnimate_relight_lora_fp16_resized_from_128_to_dynamic_22.safetensors",
                            size=272805784, url=HF + "Kijai/WanVideo_comfy/resolve/main/LoRAs/Wan22_relight/"
                                                     "WanAnimate_relight_lora_fp16_resized_from_128_to_dynamic_22.safetensors"),
    "lightx2v_i2v": dict(path="animate/lightx2v_I2V_14B_480p_cfg_step_distill_rank64_bf16.safetensors", size=738005744,
                         url=HF + "Kijai/WanVideo_comfy/resolve/main/Lightx2v/"
                                  "lightx2v_I2V_14B_480p_cfg_step_distill_rank64_bf16.safetensors"),
    "sam2": dict(path="sam2.1-hiera-small/model.safetensors", size=184305280,
                 url=HF + "facebook/sam2.1-hiera-small/resolve/main/model.safetensors"),
    "sam2_config": dict(path="sam2.1-hiera-small/config.json", size=5698,
                        url=HF + "facebook/sam2.1-hiera-small/resolve/main/config.json"),
    "sam2_pre": dict(path="sam2.1-hiera-small/preprocessor_config.json", size=683,
                     url=HF + "facebook/sam2.1-hiera-small/resolve/main/preprocessor_config.json"),
    "sam2_proc": dict(path="sam2.1-hiera-small/processor_config.json", size=95,
                      url=HF + "facebook/sam2.1-hiera-small/resolve/main/processor_config.json"),
    "sam2_video": dict(path="sam2.1-hiera-small/video_preprocessor_config.json", size=705,
                       url=HF + "facebook/sam2.1-hiera-small/resolve/main/video_preprocessor_config.json"),
    # shared with Forge's ControlNet (IP-Adapter's image encoder; DWPose)
    "clip_h": dict(path="../ControlNetPreprocessor/CLIP-ViT-H-14.safetensors", size=2528373448,
                   url=HF + "h94/IP-Adapter/resolve/main/models/image_encoder/model.safetensors"),
    "dw_det": dict(path="../ControlNetPreprocessor/openpose/yolox_l.onnx", size=216746733,
                   url=HF + "yzd-v/DWPose/resolve/main/yolox_l.onnx"),
    "dw_pose": dict(path="../ControlNetPreprocessor/openpose/dw-ll_ucoco_384.onnx", size=134399116,
                    url=HF + "yzd-v/DWPose/resolve/main/dw-ll_ucoco_384.onnx"),
}
REPLACE_FILES = ["animate_q3", "animate_relight", "lightx2v_i2v", "sam2", "sam2_config", "sam2_pre", "sam2_proc",
                 "sam2_video", "clip_h", "dw_det", "dw_pose", "umt5", "vae21"]


def replace_missing():
    return [k for k in REPLACE_FILES if not present(k)]

# Wan 2.2 I2V A14B: the high-noise expert lays out motion and composition while t >= 900, the low-noise expert
# draws the detail after it. The lightx2v 260412 distillation (April 2026) needs 4 steps at CFG 1, shift 5.
PRESETS = {
    "Wan 2.2 14B, 4 steps, Q3 (14.4 GB)": dict(
        experts=[("i2v_high_q3", 0.9, "high noise"), ("i2v_low_q3", 0.0, "low noise")],
        text_encoder="umt5", vae="vae21", steps=4, shift=5.0, cfg=1.0),
    "Wan 2.2 14B, 4 steps, Q4 (19.3 GB, sharper)": dict(
        experts=[("i2v_high_q4", 0.9, "high noise"), ("i2v_low_q4", 0.0, "low noise")],
        text_encoder="umt5", vae="vae21", steps=4, shift=5.0, cfg=1.0),
}
DEFAULT_PRESET = next(iter(PRESETS))


def file_path(key):
    return os.path.join(MODELS_DIR, FILES[key]["path"])


def present(key):
    p = file_path(key)
    return os.path.isfile(p) and os.path.getsize(p) == FILES[key]["size"] and not os.path.exists(p + ".aria2")


def preset_files(name):
    pr = PRESETS[name]
    return [k for k, _, _ in pr["experts"]] + [pr["text_encoder"], pr["vae"]]


def missing(name):
    return [k for k in preset_files(name) if not present(k)]


def missing_bytes(name):
    return sum(FILES[k]["size"] for k in missing(name))


def installed_presets():
    return [n for n in PRESETS if not missing(n)]


def loras():
    """LoRA files for Wan: models/VideoStudio/loras and models/Lora/wan (where ComfyUI-style setups keep them)."""
    found = []
    for folder in (os.path.join(MODELS_DIR, "loras"), os.path.join(WEBUI_DIR, "models", "Lora", "wan")):
        found += glob.glob(os.path.join(folder, "**", "*.safetensors"), recursive=True)
    return sorted(set(found), key=lambda p: os.path.basename(p).lower())


def film_model():
    p = os.path.join(WEBUI_DIR, "models", "Deforum", "film_interpolation", "film_net_fp16.pt")
    return p if os.path.isfile(p) else None


def upscaler_models():
    folder = os.path.join(WEBUI_DIR, "models", "RealESRGAN")
    return {os.path.splitext(os.path.basename(p))[0]: p for p in sorted(glob.glob(os.path.join(folder, "*.pth")))}


class Download:
    """Fetches files in a background thread; `status()` is polled by the UI."""

    def __init__(self):
        self.lock = threading.Lock()
        self.thread = None
        self.state = {"active": False, "text": "", "done": 0, "total": 0, "error": None}

    def status(self):
        with self.lock:
            return dict(self.state)

    def start(self, keys):
        if self.thread and self.thread.is_alive():
            return False
        keys = [k for k in keys if not present(k)]
        if not keys:
            return False
        free = shutil.disk_usage(MODELS_DIR if os.path.isdir(MODELS_DIR) else WEBUI_DIR).free
        need = sum(FILES[k]["size"] for k in keys)
        if need > free - 2 * 1024 ** 3:
            with self.lock:
                self.state.update(error=f"Not enough disk space: {need / 1e9:.1f} GB needed, {free / 1e9:.1f} GB free")
            return False
        self.thread = threading.Thread(target=self._run, args=(keys,), daemon=True)
        self.thread.start()
        return True

    def _run(self, keys):
        total = sum(FILES[k]["size"] for k in keys)
        with self.lock:
            self.state.update(active=True, error=None, total=total, done=0)
        try:
            base = 0
            for k in keys:
                info = FILES[k]
                dest = file_path(k)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with self.lock:
                    self.state["text"] = os.path.basename(dest)
                _fetch(info["url"], dest, lambda n: self._progress(base + n))
                if os.path.getsize(dest) != info["size"]:
                    raise RuntimeError(f"{os.path.basename(dest)}: size {os.path.getsize(dest)} != {info['size']}")
                base += info["size"]
        except Exception as e:
            with self.lock:
                self.state["error"] = f"{type(e).__name__}: {e}"
        finally:
            with self.lock:
                self.state["active"] = False

    def _progress(self, n):
        with self.lock:
            self.state["done"] = n


def _aria2c():
    for p in (shutil.which("aria2c"), r"C:\ProgramData\chocolatey\bin\aria2c.exe"):
        if p and os.path.isfile(p):
            return p
    return None


def _fetch(url, dest, on_bytes):
    """aria2c with 8 connections and resume (long single streams break on this line), else urllib with resume."""
    aria = _aria2c()
    if aria:
        cmd = [aria, "-c", "-x", "8", "-s", "8", "-k", "4M", "--max-tries=0", "--retry-wait=3",
               "--file-allocation=none", "--console-log-level=warn", "--summary-interval=0",
               "-d", os.path.dirname(dest), "-o", os.path.basename(dest), url]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
        while proc.poll() is None:
            if os.path.exists(dest):
                on_bytes(_written(dest))
            time.sleep(1)
        if proc.returncode != 0:
            raise RuntimeError(f"aria2c exited with {proc.returncode}")
        on_bytes(os.path.getsize(dest))
        return
    have = os.path.getsize(dest) if os.path.exists(dest) else 0
    req = urllib.request.Request(url, headers={"Range": f"bytes={have}-"} if have else {})
    with urllib.request.urlopen(req, timeout=60) as r, open(dest, "ab" if have else "wb") as f:
        n = have
        while True:
            chunk = r.read(8 << 20)
            if not chunk:
                break
            f.write(chunk)
            n += len(chunk)
            on_bytes(n)


def _written(path):
    """Bytes really written of a file aria2c is filling (it pre-sizes nothing with --file-allocation=none)."""
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


DOWNLOADS = Download()

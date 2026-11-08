"""The language model that writes: Qwen3-VL-4B-Instruct, the same file FLUX 3 uses as its text encoder
(models/text_encoder/qwen3vl_4b_int8_convrot.safetensors, 4.9 GB int8). The code that runs it lives in the
sd-forge-flux3 extension (flux3/text_encoder.py: the int8 layers and the CUDA-graph writer), so nothing is
downloaded and the model is on disk once.

All 36 layers sit on the card while writing (about 6 GB with the output head); between prompts the model waits in
page-locked system memory and comes back to the card in about half a second.
"""

import gc
import os
import sys
import threading
import time

import torch

FLUX3_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "sd-forge-flux3")
FILE = "qwen3vl_4b_int8_convrot.safetensors"
TOKENIZER = "qwen3vl_4b_tokenizer"


def flux3_text_encoder():
    # only text_encoder (and its ops): flux3.engine pulls in Forge's VAE code
    if FLUX3_DIR not in sys.path:
        sys.path.insert(0, FLUX3_DIR)
    from flux3 import text_encoder as fte
    return fte


def load_safetensors(path):
    from safetensors import safe_open
    with safe_open(path, framework="pt", device="cpu") as f:
        return {k: f.get_tensor(k) for k in f.keys()}


def find_files(models_dir):
    folder = os.path.join(models_dir, "text_encoder")
    model, tok = os.path.join(folder, FILE), os.path.join(folder, TOKENIZER)
    return (model, tok) if os.path.isfile(model) and os.path.isfile(os.path.join(tok, "tokenizer.json")) else None


class Writer:
    """The model in system memory, put on the card for each prompt."""

    def __init__(self, model_path, tokenizer_dir):
        self.model_path, self.tokenizer_dir = model_path, tokenizer_dir
        self.te = None
        self.lock = threading.RLock()
        self.used_at = 0.0

    def load(self):
        with self.lock:
            if self.te is not None:
                return
            fte = flux3_text_encoder()
            t0 = time.time()
            sd = load_safetensors(self.model_path)
            te = fte.QwenText(sd, self.tokenizer_dir, fte.load_config(self.tokenizer_dir))
            del sd
            gc.collect()
            te.pin()
            self.te = te
            print(f"[Prompt writer] Qwen3-VL-4B loaded in {time.time() - t0:.1f} s", flush=True)

    def release(self):
        with self.lock:
            if self.te is None:
                return
            self.te.release()
            self.te = None
            gc.collect()
            torch.cuda.empty_cache()
            if hasattr(torch._C, "_host_emptyCache"):
                torch._C._host_emptyCache()
            print("[Prompt writer] model released from memory", flush=True)

    @staticmethod
    def card_bytes(te):
        # the layers, the output head as bf16 (151k x 2560) and room for the cache and activations
        return te.total_bytes() + te.embed.weight.cpu.shape[0] * te.embed.weight.cpu.shape[1] * 2 + (700 << 20)

    @staticmethod
    def run(te, device, system, user, turns=(), temperature=0.0, seed=0, max_new=320):
        """One answer from `te` (a loaded QwenText), all layers on the card for the time it takes."""
        te.to_device(device, len(te.layers))
        try:
            return te.generate(system, user, max_new=max_new, presence_penalty=0.3, turns=turns,
                               temperature=temperature, top_k=40, seed=seed)
        finally:
            te.offload()
            torch.cuda.empty_cache()

    def write(self, device, system, user, turns=(), temperature=0.0, seed=0, max_new=320):
        with self.lock:
            self.load()
            self.used_at = time.time()
            return self.run(self.te, device, system, user, turns, temperature, seed, max_new)

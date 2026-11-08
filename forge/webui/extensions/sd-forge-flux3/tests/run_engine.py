"""The FLUX 3 engine alone, outside Forge: load, encode, sample, decode, with timings and peak memory.

Run from the webui folder:
  venv\\Scripts\\python.exe extensions\\sd-forge-flux3\\tests\\run_engine.py --prompt "..." --out out.png [--writer]
"""

import argparse
import io
import os
import sys
import time

WEBUI = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, WEBUI)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.stdout.reconfigure(encoding="utf-8")

import torch  # noqa: E402
from PIL import Image  # noqa: E402

from flux3 import engine as feng  # noqa: E402

M = os.path.join(WEBUI, "models")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", default="Full-color outdoor photograph of a red vintage car parked beside a calm alpine lake at golden hour, snow-capped mountains behind, warm low sunlight from the left.")
    ap.add_argument("--out", default="flux3_test.png")
    ap.add_argument("--width", type=int, default=1024)
    ap.add_argument("--height", type=int, default=1024)
    ap.add_argument("--steps", type=int, default=16)
    ap.add_argument("--seed", type=int, default=1234)
    ap.add_argument("--writer", action="store_true")
    ap.add_argument("--edit", default=None, help="reference image for an edit")
    args, _ = ap.parse_known_args()
    paths = {"model": os.path.join(M, "Stable-diffusion", "flux3-image-klein-int8-convrot.safetensors"),
             "text_encoder": os.path.join(M, "text_encoder", "qwen3vl_4b_int8_convrot.safetensors"),
             "tokenizer_dir": os.path.join(M, "text_encoder", "qwen3vl_4b_tokenizer"),
             "vae": os.path.join(M, "VAE", "flux2-vae.safetensors")}
    dev = torch.device("cuda")
    eng = feng.Flux3Engine(paths)
    t = time.time()
    eng.load(dev)
    print(f"load {time.time() - t:.1f} s")
    with torch.inference_mode():
        prompt = args.prompt
        if args.writer:
            t = time.time()
            prompt = eng.upsample(prompt, args.width, args.height)
            print(f"writer {time.time() - t:.1f} s: {prompt}")
        t = time.time()
        emb = eng.encode(prompt)
        print(f"encode {time.time() - t:.1f} s, {tuple(emb.shape)}, std {emb.float().std():.3f}")
        g = torch.Generator("cpu").manual_seed(args.seed)
        noise = torch.randn(1, 128, args.height // 16, args.width // 16, generator=g)
        refs = None
        if args.edit:
            im = Image.open(args.edit).convert("RGB").resize((args.width, args.height), Image.LANCZOS)
            x = torch.from_numpy(__import__("numpy").asarray(im).astype("float32") / 127.5 - 1).permute(2, 0, 1)[None]
            refs = [eng.encode_image(x)]
        t = time.time()
        lat = eng.sample(noise, emb, args.steps, refs=refs)
        torch.cuda.synchronize()
        print(f"sample {time.time() - t:.1f} s, stats {eng.stats}")
        t = time.time()
        img = eng.decode(lat)[0]
        print(f"decode {time.time() - t:.1f} s, peak {torch.cuda.max_memory_allocated() / 2**30:.2f} GB")
    arr = ((img.clamp(-1, 1) + 1) * 127.5).round().byte().permute(1, 2, 0).numpy()
    Image.fromarray(arr).save(args.out)
    print("saved", args.out)


if __name__ == "__main__":
    main()

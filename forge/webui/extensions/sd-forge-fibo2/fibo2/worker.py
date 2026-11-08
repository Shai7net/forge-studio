"""One FIBO inference job per process; exits to release all CUDA allocations."""

import argparse
import json
import logging
import os
from pathlib import Path
import sys
import time

# This script is invoked directly, outside Forge's Python environment.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fibo2 import RUNTIME_DIR, COMFY_REVISION  # noqa: E402
from fibo2.contracts import Generation, grey_out, preserve_unmasked  # noqa: E402


def write_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def load_backend(cpu=False):
    sys.path.insert(0, str(RUNTIME_DIR))
    import comfy.options
    comfy.options.enable_args_parsing()
    sys.argv = ["fibo2-worker", "--cpu"] if cpu else [
        "fibo2-worker", "--lowvram", "--disable-dynamic-vram", "--use-pytorch-cross-attention", "--reserve-vram", "1.0",
        "--bf16-text-enc", "--bf16-vae"]
    import torch
    import comfy.sd
    import comfy.utils
    import comfy.model_management
    import comfy.sample
    import comfy.samplers
    from comfy_extras.nodes_fibo2 import TextEncodeFibo2
    return torch, comfy, TextEncodeFibo2


def run(job_path):
    from dataclasses import asdict
    import numpy as np
    from PIL import Image, PngImagePlugin

    job_path = Path(job_path).resolve()
    job = json.loads(job_path.read_text(encoding="utf-8"))
    request = Generation(**job["request"])
    refs = [Image.open(p).convert("RGB") for p in job.get("references", [])]
    mask = Image.open(job["mask"]).convert("L") if job.get("mask") else None
    request = request.validated(refs, mask)
    output = Path(job["output_dir"])
    output.mkdir(parents=True, exist_ok=True)
    progress_path = job_path.parent / "progress.json"
    started = time.monotonic()

    def progress(stage, **extra):
        write_json(progress_path, {"stage": stage, **extra})

    progress("loading")
    torch, comfy, encode_node = load_backend()
    if not torch.cuda.is_available():
        raise RuntimeError("This FIBO runtime requires a CUDA GPU")
    torch.cuda.reset_peak_memory_stats()
    parts = job["components"]
    with torch.inference_mode():
        model = comfy.sd.load_diffusion_model(parts["model"])
        if model is None:
            raise RuntimeError("The selected file is not a supported FIBO 2.0 diffusion model")
        clip = comfy.sd.load_clip(ckpt_paths=[parts["text_encoder"]], clip_type=comfy.sd.CLIPType.FIBO2,
                                 model_options={"load_device": torch.device("cuda"), "offload_device": torch.device("cpu")})
        vae = comfy.sd.VAE(sd=comfy.utils.load_torch_file(parts["vae"], safe_load=True))
        for lora in job.get("loras", []):
            weights = comfy.utils.load_torch_file(lora["path"], safe_load=True)
            adapted_model, adapted_clip = comfy.sd.load_lora_for_models(model, clip, weights, lora["weight"], lora["weight"])
            old_count = sum(map(len, model.patches.values())) + sum(map(len, clip.patcher.patches.values()))
            new_count = sum(map(len, adapted_model.patches.values())) + sum(map(len, adapted_clip.patcher.patches.values()))
            if new_count <= old_count:
                raise ValueError(f"This LoRA has no layers compatible with FIBO 2.0: {Path(lora['path']).name}")
            model, clip = adapted_model, adapted_clip

        from fibo2 import prompting
        final_prompt = request.prompt
        if prompting.wants_writer(request):
            progress("writing_prompt")
            final_prompt = prompting.expand(clip, request, refs)
            logging.info("FIBO local VGL caption: %s", final_prompt)

        progress("encoding")
        conditioning_refs = refs
        if mask is not None:
            conditioning_refs = [grey_out(refs[0], mask)]
        image_inputs = {f"image_{i + 1}": torch.from_numpy(np.asarray(im).astype("float32") / 255.0)[None]
                        for i, im in enumerate(conditioning_refs)}
        encoded = encode_node.execute(clip=clip, prompt=final_prompt, negative_prompt=request.negative_prompt,
                                      vae=vae, images=image_inputs, width=request.width, height=request.height, batch_size=1)
        positive, negative, latent = encoded.result
        results = []
        # Sequential images share the encoded prompt but keep independent reproducible seeds and bounded VRAM.
        for index in range(request.count):
            seed = (job["seed"] + index) % 2**32
            noise = comfy.sample.prepare_noise(latent["samples"], seed)

            def callback(step, x0, x, total):
                progress("sampling", image=index + 1, count=request.count, step=step + 1, steps=total)

            progress("sampling", image=index + 1, count=request.count, step=0, steps=request.effective_steps)
            samples = comfy.sample.sample(model, noise, request.effective_steps, request.effective_cfg,
                                          "euler", "simple", positive, negative, latent["samples"],
                                          denoise=1.0, callback=callback, seed=seed)
            progress("decoding", image=index + 1)
            # Tiled decoding bounds the VAE activation memory on 8 GB GPUs.
            decoded = vae.decode_tiled(samples, tile_x=64, tile_y=64, overlap=16)
            arr = decoded[0].float().clamp(0, 1).mul(255).round().byte().cpu().numpy()
            result = Image.fromarray(arr)
            if mask is not None:
                result = preserve_unmasked(result, refs[0], mask)
            metadata = {**asdict(request), "seed": seed, "steps": request.effective_steps,
                        "cfg": request.effective_cfg, "sampler": "euler", "scheduler": "simple",
                        "references": len(refs), "masked": mask is not None, "backend_revision": COMFY_REVISION,
                        "rendered_prompt": final_prompt,
                        "loras": [{"name": Path(x["path"]).name, "weight": x["weight"]} for x in job.get("loras", [])]}
            pnginfo = PngImagePlugin.PngInfo()
            pnginfo.add_text("fibo2", json.dumps(metadata, ensure_ascii=False))
            pnginfo.add_text("parameters", f"{request.prompt}\nNegative prompt: {request.negative_prompt}\n"
                             f"Steps: {request.effective_steps}, Sampler: Euler, CFG scale: {request.effective_cfg}, "
                             f"Seed: {seed}, Size: {request.width}x{request.height}, Model: FIBO 2.0 {request.variant}")
            filename = output / f"{job_path.parent.name}-{index + 1:02d}.png"
            result.save(filename, pnginfo=pnginfo)
            results.append({"path": str(filename), "metadata": metadata})
        response = {"images": results, "elapsed_seconds": round(time.monotonic() - started, 2),
                    "peak_cuda_gb": round(torch.cuda.max_memory_allocated() / 2**30, 3)}
        write_json(job_path.parent / "result.json", response)
        progress("done", **response)
    return response


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--job")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        torch, comfy, node = load_backend(cpu=True)
        print(json.dumps({"ready": True, "torch": torch.__version__, "cuda": torch.cuda.is_available(),
                          "fibo2": hasattr(comfy.sd.CLIPType, "FIBO2"), "node": node.__name__}))
    elif args.job:
        run(args.job)
    else:
        parser.error("Provide --job or --check")


if __name__ == "__main__":
    main()

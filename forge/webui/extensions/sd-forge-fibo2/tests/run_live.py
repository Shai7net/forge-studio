"""Real GPU/API regression checks. Never mocks inference; saves measured evidence."""

import argparse
import base64
import io
import json
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fibo2 import WEBUI_DIR
from fibo2.contracts import encode_image, decode_image
from PIL import Image, ImageDraw
import numpy as np


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:7869")
    parser.add_argument("--case", choices=["regression", "baseline", "generate", "multiref", "fourrefs", "flux3", "cancel", "writer"], default="regression")
    args = parser.parse_args()
    output = WEBUI_DIR / "outputs" / "fibo2" / "validation"
    output.mkdir(parents=True, exist_ok=True)

    def call(path, payload=None, quiet=False):
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(args.url + path, data=data, headers={"Content-Type": "application/json"})
        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=3600) as response:
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(exc.read().decode()) from exc
        if not quiet:
            print(f"{path}: {time.monotonic() - started:.1f}s", flush=True)
        return result

    fixtures = Path(__file__).resolve().parent / "fixtures"
    source = Image.open(fixtures / "source-car.png").convert("RGB")
    if args.case == "baseline":
        payload = {"prompt": "A red ceramic mug on a wooden table, realistic photograph", "seed": 4011, "steps": 8,
                   "width": 512, "height": 512, "sampler_name": "Euler", "cfg_scale": 7, "save_images": False}
        image = decode_image(call("/sdapi/v1/txt2img", payload)["images"][0])
        image.save(output / "sd-fibo-disabled.png")
        previous = Image.open(output / "sd-before.png")
        delta = np.abs(np.asarray(image).astype(float) - np.asarray(previous).astype(float))
        report = {"case": args.case, "mean_difference": float(delta.mean()), "max_difference": float(delta.max())}
    elif args.case == "regression":
        sd = {"prompt": "A red ceramic mug on a wooden table, realistic photograph", "seed": 4011, "steps": 8,
              "width": 512, "height": 512, "sampler_name": "Euler", "cfg_scale": 7, "save_images": False}
        before = decode_image(call("/sdapi/v1/txt2img", sd)["images"][0])
        before.save(output / "sd-before.png")
        mask = Image.new("L", source.size)
        ImageDraw.Draw(mask).rectangle((32, 208, 485, 394), fill=255)
        mask.save(output / "car-mask.png")
        payload = {"variant": "edit-turbo", "mode": "edit", "prompt": "Change the car paint to blue. Preserve the car shape and all other details.",
                   "images": [encode_image(source)], "mask": encode_image(mask), "width": 512, "height": 512, "seed": 1234}
        result = call("/fibo2/generate", payload)
        edited = decode_image(result["images"][0])
        edited.save(output / "turbo-masked.png")
        original, actual = np.asarray(source), np.asarray(edited)
        keep = np.asarray(mask) == 0
        keep_changed = int(np.count_nonzero(np.any(original != actual, axis=-1) & keep))
        edit_delta = float(np.abs(original.astype(float) - actual.astype(float))[~keep].mean())
        assert keep_changed == 0, f"Mask leaked: {keep_changed} outside pixels changed"
        assert edit_delta > 1, "Masked inference made no meaningful pixel change"
        after = decode_image(call("/sdapi/v1/txt2img", sd)["images"][0])
        after.save(output / "sd-after.png")
        delta = np.abs(np.asarray(before).astype(float) - np.asarray(after).astype(float))
        report = {"case": args.case, "unmasked_pixels_changed": keep_changed, "masked_mean_difference": edit_delta,
                  "sd_before_after_mean_difference": float(delta.mean()), "sd_before_after_max_difference": float(delta.max()),
                  "fibo": result["info"]}
        assert delta.max() == 0, "SD output changed after FIBO; inspect the saved before/after images"
    elif args.case == "generate":
        prompt = {"short_description": "A red ceramic mug on a wooden table beside a window, morning sunlight, realistic product photograph.",
                  "objects": [{"description": "A red glazed ceramic mug with a round handle", "location": "center", "relative_size": "large"}],
                  "background_setting": "A wooden table beside a bright window", "style_medium": "photograph",
                  "lighting": {"conditions": "soft morning sunlight", "direction": "from the left"}}
        result = call("/fibo2/generate", {"variant": "dev", "mode": "generate", "prompt": json.dumps(prompt),
                                          "seed": 4021, "width": 1024, "height": 1024})
        image = decode_image(result["images"][0])
        assert image.size == (1024, 1024)
        assert np.asarray(image).std() > 10
        image.save(output / "dev-generation.png")
        report = {"case": args.case, "fibo": result["info"]}
    elif args.case in ("multiref", "fourrefs"):
        blue = Image.open(fixtures / "blue-car.png")
        refs = [source, blue] if args.case == "multiref" else [source, blue, source, blue]
        result = call("/fibo2/generate", {"variant": "edit-turbo", "mode": "edit",
                                          "prompt": "Change the paint of the car in <image_1> to the blue color of the car in <image_2>. Keep the landscape and framing from <image_1>.",
                                          "images": [encode_image(im) for im in refs], "seed": 4022, "width": 512, "height": 512})
        image = decode_image(result["images"][0])
        image.save(output / f"turbo-{args.case}.png")
        report = {"case": args.case, "fibo": result["info"]}
    elif args.case == "flux3":
        payload = {"prompt": "A red ceramic mug on a wooden table, soft daylight, a realistic photograph", "seed": 4023,
                   "steps": 4, "cfg_scale": 1, "width": 512, "height": 512, "sampler_name": "Euler",
                   "override_settings": {"sd_model_checkpoint": "flux3-image-klein-int8-convrot.safetensors"},
                   "override_settings_restore_afterwards": True, "save_images": False,
                   "alwayson_scripts": {"FLUX 3": {"args": ["Off", None]}}}
        result = call("/sdapi/v1/txt2img", payload)
        image = decode_image(result["images"][0])
        image.save(output / "flux3-after.png")
        report = {"case": args.case, "info": result["info"]}
    elif args.case == "writer":
        result = call("/fibo2/generate", {"variant": "edit-turbo", "mode": "edit", "prompt_writer": "auto",
                                          "prompt": "שנה את צבע המכונית לירוק ושמור על הנוף והזווית",
                                          "images": [encode_image(source)], "seed": 4024, "width": 512, "height": 512})
        image = decode_image(result["images"][0])
        image.save(output / "turbo-hebrew-writer.png")
        rendered = json.loads(result["info"]["images"][0]["metadata"]["rendered_prompt"])
        assert "edit_instruction" in rendered
        report = {"case": args.case, "fibo": result["info"]}
    else:
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(call, "/fibo2/generate", {"variant": "edit-turbo", "mode": "edit", "prompt": "Change the car to green",
                                                          "images": [encode_image(source)], "width": 512, "height": 512})
            for _ in range(600):
                time.sleep(0.2)
                job = call("/fibo2/status", quiet=True)["job"]
                if job["stage"] == "sampling" and job.get("step", 0) >= 1:
                    break
            else:
                raise AssertionError("Worker did not reach an active denoising step")
            assert call("/fibo2/interrupt", {})["cancelled"]
            try:
                future.result(timeout=30)
            except RuntimeError as exc:
                assert "GPU memory released" in str(exc), str(exc)
            else:
                raise AssertionError("Inference was not cancelled")
        report = {"case": args.case, "interrupted_at": job, "job": call("/fibo2/status")["job"]}
    (output / f"{args.case}.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()

"""An additive Forge tab; SD/SDXL/FLUX3 checkpoint selection remains unchanged."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import threading

import gradio as gr

from . import EXT_DIR, HOME_DIR
from . import models, service
from .runner import runner

_setup_lock = threading.Lock()


def status():
    return json.dumps({**models.status(), "job": runner.status()}, indent=2, ensure_ascii=False)


def setup(action, variant="edit-turbo", precision="int8"):
    """Use a modern Python only for setup; never pip-install into Forge."""
    if not _setup_lock.acquire(blocking=False):
        raise gr.Error("FIBO setup is already running")
    try:
        HOME_DIR.mkdir(parents=True, exist_ok=True)
        log = HOME_DIR / "logs" / "setup-latest.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            py = shutil.which("py")
            command = [py, "-3.11"] if py else []
        else:
            py = shutil.which("python3.11") or shutil.which("python3")
            command = [py] if py else []
        if not command:
            raise gr.Error("Install Python 3.11+ for FIBO setup; Forge's Python is kept unchanged")
        command += [str(EXT_DIR / "fibo2/setup_runtime.py"), action, "--variant", variant, "--precision", precision]
        env = os.environ.copy()
        for key in ("PYTHONPATH", "PYTHONHOME"):
            env.pop(key, None)
        env["PYTHONUTF8"] = "1"
        with log.open("w", encoding="utf-8") as stream:
            result = subprocess.run(command, cwd=str(HOME_DIR), env=env, stdout=stream, stderr=subprocess.STDOUT,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode:
            raise gr.Error(f"Setup failed. See {log}\n{log.read_text(encoding='utf-8', errors='replace')[-1500:]}")
        return status()
    finally:
        _setup_lock.release()


def build():
    from modules import infotext_utils
    with gr.Blocks(analytics_enabled=False) as tab:
        # English only: the interface language switch (forge-hebrew-ui) translates these labels
        gr.Markdown("## FIBO 2.0 · image generation and editing")
        gr.Markdown("**Dev:** generation and editing (50 steps / CFG 5). **Edit Turbo:** editing (4 steps / CFG 1). "
                    "Choose a FIBO model here. Your SD/SDXL/FLUX3 checkpoint and settings stay selected.")
        with gr.Row():
            with gr.Column():
                with gr.Row():
                    variant = gr.Radio(["dev", "edit-turbo"], value="dev", label="FIBO model")
                    mode = gr.Radio(["generate", "edit"], value="generate", label="Task")
                prompt = gr.Textbox(lines=5, label="Prompt / Edit instruction / VGL JSON", elem_id="fibo2_prompt",
                                    placeholder='Describe an image, write an edit instruction, or paste structured VGL JSON.')
                negative = gr.Textbox(lines=2, label="Negative prompt (empty uses FIBO's trained null caption)")
                writer = gr.Radio(["off", "auto", "always"], value="off", label="Local prompt writer (Qwen3-VL: any language to VGL)")
                with gr.Row():
                    width = gr.Number(value=1024, precision=0, label="Width (multiple of 16)")
                    height = gr.Number(value=1024, precision=0, label="Height (multiple of 16)")
                    keep_size = gr.Checkbox(value=True, label="Single-image edits: keep source size (rounded down to /16)")
                with gr.Row():
                    steps = gr.Slider(0, 100, value=0, step=1, label="Steps (0 = model default)")
                    cfg = gr.Slider(0, 20, value=0, step=0.1, label="CFG (0 = model default)")
                    seed = gr.Number(value=-1, precision=0, label="Seed (-1 = random)")
                    count = gr.Slider(1, 4, value=1, step=1, label="Images")
                gr.Markdown("**Image editing:** upload one source, or up to four references, addressed as "
                            "`<image_1>` … `<image_4>`. An instruction-only edit needs no mask. "
                            "For exact preservation outside an area, upload a white-on-black mask with one source. "
                            "Without a mask, pixel-identical preservation is not guaranteed.")
                with gr.Row():
                    refs = [gr.Image(type="pil", label=f"Reference {i + 1}", elem_id=f"fibo2_reference_{i + 1}") for i in range(4)]
                mask = gr.Image(type="pil", image_mode="L", label="Optional mask: white = edit, black = preserve (same size as source)")
                with gr.Accordion("Advanced · text rendering, LoRA and precision", open=False):
                    gr.Markdown('FIBO handles typography through VGL `text_render`, e.g. '
                                '`{"short_description":"A bakery sign", "text_render":[{"text":"FRESH BREAD"}]}`. '
                                'For JSON edits also add `"edit_instruction":"..."`. '
                                'Use FIBO-compatible LoRAs only; SD/FLUX LoRAs have different architectures.')
                    precision = gr.Radio(["int8", "bf16"], value="int8", label="Weights (int8 recommended on 8 GB)")
                    loras = gr.Dropdown(choices=service.lora_choices(), multiselect=True, label="FIBO LoRAs in models/Lora")
                    weight = gr.Slider(-4, 4, value=1, step=0.05, label="LoRA strength")
                with gr.Row():
                    generate = gr.Button("Generate or edit",variant="primary", elem_id="fibo2_generate")
                    stop = gr.Button("Stop FIBO", elem_id="fibo2_stop")
            with gr.Column():
                gallery = gr.Gallery(label="FIBO results", elem_id="fibo2_gallery").style(columns=2)
                gr.HTML('<span role="status" aria-live="polite">Ready</span>', elem_id="fibo2_live_progress")
                details = gr.Textbox(label="Status / timing / log", lines=5, interactive=False)
                metadata = gr.Textbox(label="Reproducible generation metadata", lines=8, interactive=False)
                with gr.Row():
                    again = gr.Button("Use first result as reference 1")
                    send_img = gr.Button("Send to img2img")
                    send_extra = gr.Button("Send to Extras")
                with gr.Accordion("Setup and downloads", open=False):
                    gr.Markdown("Install once, then download the chosen model. Downloads use aria2c, resume, and SHA256 verification. "
                                "The existing FLUX3 text encoder and FLUX.2 VAE are reused read-only. "
                                "Setup logs: `webui/fibo2/logs/setup-latest.log`. "
                                "Weights: [Comfy-Org/Fibo-2.0](https://huggingface.co/Comfy-Org/Fibo-2.0).")
                    install = gr.Button("Install FIBO runtime (separate environment)")
                    download = gr.Button("Download selected FIBO model")
                    refresh = gr.Button("Refresh status")
                    health = gr.Textbox(value=status(), lines=7, label="FIBO availability", interactive=False)
        inputs = [variant, mode, prompt, negative, width, height, steps, cfg, seed, count, precision, keep_size, *refs, mask, loras, weight, writer]

        def run(*args):
            try:
                return service.ui_generate(*args)
            except Exception as exc:
                raise gr.Error(str(exc)) from exc

        generate.click(run, inputs, [gallery, details, metadata])
        variant.change(lambda v: gr.update(value="edit") if v == "edit-turbo" else gr.update(), [variant], mode)
        stop.click(lambda: json.dumps(runner.cancel()), outputs=details, queue=False)
        install.click(lambda: setup("install"), outputs=health)
        download.click(lambda v, p: setup("download", v, p), [variant, precision], health)
        refresh.click(status, outputs=health, queue=False)

        def first_result(items):
            image = infotext_utils.image_from_url_text(items[0]) if items else None
            return image, "edit"

        again.click(first_result, [gallery], [refs[0], mode])
        for button, destination in ((send_img, "img2img"), (send_extra, "extras")):
            infotext_utils.register_paste_params_button(infotext_utils.ParamBinding(
                paste_button=button, tabname=destination, source_image_component=gallery))
    return tab

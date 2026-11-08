"""Hebrew prompt writer for Forge: 🌐 beside the prompt fields (javascript/translator.js) turns an idea written in
Hebrew into an English prompt for the model behind that field, and 👍 / 👎 teach it (prompt_writer/memory.py).

The writer is Gemma 4 E4B run by llama.cpp (prompt_writer/llama.py); without it, Qwen3-VL-4B, the file FLUX 3 already
uses as its text encoder (prompt_writer/model.py), and without both NLLB-600M, the literal translator this button ran
before 2026-10-27.

HTTP routes (also usable from scripts, see README):
  POST /translator/write   {text, field, video_mode?, again?, seed?} -> {prompt, meaning, target, target_label, ...}
  POST /translator/rate    {source, target, written, final, rating (1 / -1), meaning?, note?, id?} -> {entry, counts}
  POST /translator/note    {id, note}
  GET  /translator/learned -> {entries, counts, targets}
  POST /translator/forget  {id}
  POST /translator/hebrew-to-english {text} -> {translated_text}   (the old route: a prompt for txt2img's model)
"""

import json
import os
import struct
import sys
import threading
import time

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from modules import devices, paths, script_callbacks, sd_models, shared

EXT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if EXT_DIR not in sys.path:
    sys.path.insert(0, EXT_DIR)

import prompt_writer  # noqa: E402
from prompt_writer import llama as pllama, memory as pmem, model as pmodel, styles  # noqa: E402

MEMORY = pmem.Memory(os.path.join(EXT_DIR, "data", "learned.json"))

_server, _gguf = pllama.find_server(), pllama.find_model(paths.models_path)
GEMMA = pllama.LlamaWriter(_server, _gguf, os.path.join(EXT_DIR, "data", "llama-server.log")) if _server and _gguf else None
_qwen_files = pmodel.find_files(paths.models_path)
QWEN = pmodel.Writer(*_qwen_files) if _qwen_files else None
ENGINES = {"Gemma 4 E4B (llama.cpp)": GEMMA, "Qwen3-VL-4B (FLUX 3's text encoder)": QWEN}

_local = threading.local()  # .writer is set in the thread that writes, so the queue hook leaves its server alone


def log(msg):
    print(f"[Prompt writer] {msg}", flush=True)


class Busy(Exception):
    pass


def engine_name():
    wanted = shared.opts.data.get("prompt_writer_engine", "")
    if ENGINES.get(wanted) is not None:
        return wanted
    return next((name for name, eng in ENGINES.items() if eng is not None), None)


# ---------------------------------------------------------------------- which model is behind the field

_arch_cache = {}


def architecture():
    """("sd15" | "sdxl" | "flux" | "fibo", checkpoint title) of the selected checkpoint, read from its weight names
    (FIBO 2.0 checkpoints are the ones sd-forge-fibo2 registers)."""
    title = shared.opts.data.get("sd_model_checkpoint") or ""
    info = sd_models.get_closet_checkpoint_match(title) if title else None
    if getattr(info, "fibo2_variant", None):
        return "fibo", title
    path = getattr(info, "filename", "") or ""
    if path in _arch_cache:
        return _arch_cache[path], title
    arch = "sd15"
    try:
        if path.lower().endswith(".safetensors"):
            with open(path, "rb") as f:
                n = struct.unpack("<Q", f.read(8))[0]
                keys = json.loads(f.read(n)).keys()
            if any("double_blocks." in k or "single_blocks." in k for k in keys):
                arch = "flux"  # FLUX.1 and FLUX 3 (sd-forge-flux3) alike
            elif any(k.startswith("conditioner.embedders.1.") for k in keys):
                arch = "sdxl"
        elif "xl" in os.path.basename(path).lower():
            arch = "sdxl"
    except Exception as e:  # noqa: BLE001
        log(f"could not read {path}: {e}")
    _arch_cache[path] = arch
    return arch, title


# ---------------------------------------------------------------------- sharing the card

def _take_queue(wait=3.0):
    """Forge's own lock (renders and Video Studio jobs hold it): the writer needs part of the card."""
    from modules.call_queue import queue_lock
    end = time.time() + wait
    while not queue_lock.acquire(blocking=False):
        if time.time() > end:
            raise Busy()
        time.sleep(0.1)
    return queue_lock


def _free_card(need):
    from ldm_patched.modules import model_management
    model_management.free_memory(need, devices.device)  # Forge's models step off the card (sd-forge-flux3 too)
    model_management.soft_empty_cache()


def _stop_gemma(why):
    if GEMMA is not None and GEMMA.running():
        with GEMMA.lock:  # waits for a write in progress, then frees the card
            GEMMA.stop(why)


def _guard_queue():
    """Whoever takes Forge's queue (a render, a Video Studio job, a checkpoint change...) first sends the writer's
    server off the card: it must never hold 2.9 GB while something else draws."""
    from modules import call_queue
    lock = call_queue.queue_lock
    base = type(lock)

    class WriterAwareLock(base):
        def acquire(self, blocking=True):
            if not getattr(_local, "writer", False):
                _stop_gemma("Forge needs the card")
            return base.acquire(self, blocking)

        __enter__ = acquire

    if base.__name__ != "WriterAwareLock":
        lock.__class__ = WriterAwareLock


def _generate(name, system, turns, user, temperature, seed):
    _local.writer = True
    try:
        lock = _take_queue()
    finally:
        _local.writer = False
    try:
        if ENGINES[name] is GEMMA:
            with GEMMA.lock:
                if not GEMMA.running():
                    _free_card(4 << 30)
                return GEMMA.write(system, user, turns, temperature, seed)
        glue = sys.modules.get("flux3.glue")
        eng = getattr(glue, "ENGINE", None)
        if eng is not None and getattr(eng, "te", None) is not None:
            # FLUX 3 is loaded: its text encoder is this very model, so it writes and ours is not kept twice
            QWEN.release()
            with eng.lock:
                eng.offload_all()
                _free_card(pmodel.Writer.card_bytes(eng.te))
                return pmodel.Writer.run(eng.te, devices.device, system, user, turns, temperature, seed)
        with QWEN.lock:  # the idle watch must not let it go between loading and writing
            QWEN.load()
            QWEN.used_at = time.time()
            _free_card(pmodel.Writer.card_bytes(QWEN.te))
            return QWEN.write(devices.device, system, user, turns, temperature, seed)
    finally:
        lock.release()


def _idle_watch():
    """Gemma leaves the card a little after the last prompt; Qwen leaves system memory (4.9 GB) after longer."""
    while True:
        time.sleep(5)
        try:
            if GEMMA is not None and GEMMA.running() and time.time() - GEMMA.used_at > float(
                    shared.opts.data.get("prompt_writer_keep_seconds", 45)):
                if GEMMA.lock.acquire(blocking=False):
                    try:
                        GEMMA.stop("idle")
                    finally:
                        GEMMA.lock.release()
            if QWEN is not None and QWEN.te is not None and time.time() - QWEN.used_at > float(
                    shared.opts.data.get("prompt_writer_keep_minutes", 10)) * 60:
                if QWEN.lock.acquire(blocking=False):
                    try:
                        QWEN.release()
                    finally:
                        QWEN.lock.release()
        except Exception as e:  # noqa: BLE001
            log(f"idle check: {e}")


_nllb = {}


def _nllb_translate(text):
    """The old literal translator, only when neither writer is there."""
    import torch
    from transformers import AutoModelForSeq2SeqLM, AutoTokenizer, pipeline
    if "pipe" not in _nllb:
        name = "facebook/nllb-200-distilled-600M"
        tok = AutoTokenizer.from_pretrained(name)
        model = AutoModelForSeq2SeqLM.from_pretrained(name)
        _nllb["pipe"] = pipeline("translation", model=model, tokenizer=tok, src_lang="heb_Hebr", tgt_lang="eng_Latn",
                                 max_length=512, device=0 if torch.cuda.is_available() else -1)
    return _nllb["pipe"](text)[0]["translation_text"].strip().lstrip("-").strip()


# ---------------------------------------------------------------------- writing

def write(text, field="txt2img_prompt", video_mode="", again=False, seed=0):
    arch, checkpoint = architecture()
    target = styles.target_for(field, arch, checkpoint, video_mode)
    t0 = time.time()
    name = engine_name()
    if name is None:
        prompt = _nllb_translate(text)
        return {"prompt": prompt, "meaning": "", "target": target, "target_label": "NLLB (translation only)",
                "engine": "NLLB", "seconds": round(time.time() - t0, 1), "examples": 0, "counts": MEMORY.counts()}
    creativity = shared.opts.data.get("prompt_writer_creativity", "Rich")
    system, turns, user, tags = prompt_writer.compose(text, target, MEMORY, creativity)
    # the first answer is the model's best (greedy); 🔄 and 👎 draw another one
    answer = _generate(name, system, turns, user, 0.8 if again else 0.0, seed)
    meaning, _ = styles.parse(answer)
    prompt = styles.tidy(answer, target, tags)
    if not prompt:
        raise RuntimeError(f"the model gave no prompt: {answer[:200]!r}")
    learned = sum(1 for turn in turns if turn != styles.example(target))
    log(f"{styles.TARGETS[target]}{' (again)' if again else ''}, {learned} learned examples, {name.split(' (')[0]}, "
        f"{time.time() - t0:.1f} s | {meaning} | {prompt}")
    return {"prompt": prompt, "meaning": meaning, "target": target, "target_label": styles.TARGETS[target],
            "engine": name.split(" (")[0], "seconds": round(time.time() - t0, 1), "examples": learned,
            "counts": MEMORY.counts()}


# ---------------------------------------------------------------------- routes

class WriteRequest(BaseModel):
    text: str
    field: str = "txt2img_prompt"
    video_mode: str = ""
    again: bool = False
    seed: int = 0


class RateRequest(BaseModel):
    source: str
    target: str
    written: str
    final: str = ""
    rating: int
    meaning: str = ""
    note: str = ""
    id: str = ""


class NoteRequest(BaseModel):
    id: str
    note: str = ""


class TranslationRequest(BaseModel):
    text: str


def _error(message, status=400):
    return JSONResponse({"error": message}, status_code=status)


def mount(demo, app: FastAPI):
    @app.post("/translator/write")
    def route_write(req: WriteRequest):
        if not req.text.strip():
            return _error("Write an idea first.")
        try:
            return write(req.text.strip(), req.field, req.video_mode, req.again, req.seed)
        except Busy:
            return _error("Forge is busy making a picture or a video. The writer needs the card: try again when "
                          "it ends.", 409)
        except Exception as e:  # noqa: BLE001
            log(f"writing failed: {type(e).__name__}: {e}")
            return _error(f"{type(e).__name__}: {e}", 500)

    @app.post("/translator/rate")
    def route_rate(req: RateRequest):
        if req.target not in styles.TARGETS:
            return _error(f"Unknown target {req.target}")
        entry = MEMORY.rate(req.source, req.target, req.written, req.final, req.rating,
                            architecture()[1], req.note, req.id or None, req.meaning)
        return {"entry": entry, "counts": MEMORY.counts()}

    @app.post("/translator/note")
    def route_note(req: NoteRequest):
        entry = MEMORY.note(req.id, req.note)
        return {"entry": entry, "counts": MEMORY.counts()} if entry else _error("No such rating.", 404)

    @app.get("/translator/learned")
    def route_learned():
        return {"entries": MEMORY.listing(), "counts": MEMORY.counts(), "targets": styles.TARGETS}

    @app.post("/translator/forget")
    def route_forget(req: NoteRequest):
        return {"forgotten": MEMORY.forget(req.id), "counts": MEMORY.counts()}

    @app.post("/translator/hebrew-to-english")
    def route_translate(req: TranslationRequest):
        try:
            return {"translated_text": write(req.text, "txt2img_prompt")["prompt"]}
        except Busy:
            return _error("Forge is busy.", 409)

    _guard_queue()
    threading.Thread(target=_idle_watch, name="prompt-writer-idle", daemon=True).start()
    counts = MEMORY.counts()
    log(f"ready: {engine_name() or 'NLLB fallback (no writer model found)'}, "
        f"{counts['liked']} liked / {counts['disliked']} disliked")


def on_ui_settings():
    import gradio as gr
    section = ("prompt_writer", "Prompt writer (🌐 Hebrew to English)")
    shared.opts.add_option("prompt_writer_creativity", shared.OptionInfo(
        "Rich", "How much the writer adds to your idea", gr.Radio, {"choices": list(styles.CREATIVITY)},
        section=section).info("Faithful: only what you wrote. Rich: concrete looks, place, light and framing. "
                              "Creative: a striking picture of your idea"))
    names = [name for name, eng in ENGINES.items() if eng is not None]
    if len(names) > 1:
        shared.opts.add_option("prompt_writer_engine", shared.OptionInfo(
            names[0], "Writer model", gr.Radio, {"choices": names}, section=section).info(
            "Gemma reads Hebrew far better; Qwen needs no extra program"))
    shared.opts.add_option("prompt_writer_keep_seconds", shared.OptionInfo(
        45, "Keep Gemma on the card after a prompt (seconds)", gr.Slider, {"minimum": 0, "maximum": 600, "step": 5},
        section=section).info("2.9 GB of the card while kept; it leaves at once when Forge starts drawing"))
    shared.opts.add_option("prompt_writer_keep_minutes", shared.OptionInfo(
        10, "Keep Qwen in memory after a prompt (minutes)", gr.Slider, {"minimum": 0, "maximum": 60, "step": 1},
        section=section).info("4.9 GB of system memory while kept; loading it again takes a few seconds"))


script_callbacks.on_app_started(mount)
script_callbacks.on_ui_settings(on_ui_settings)

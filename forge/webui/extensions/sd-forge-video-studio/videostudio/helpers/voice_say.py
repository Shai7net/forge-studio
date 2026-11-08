"""Speaks text into a .wav. Runs in Video Studio's voice environment (webui/video-studio/voice-env: Chatterbox
0.1.7 with GPU PyTorch, Phonikud, Piper), never inside Forge.

    python voice_say.py <voices folder> <request.json>

request.json: {"text", "out", "engine": "chatterbox" | "piper", "language": "he" | "en" | ...,
               "reference": voice sample .wav (Chatterbox copies its timbre), "exaggeration": 0.5, "cfg": 0.5,
               "temperature": 0.8, "seed": 0, "speed": 1.0, "pause": 0.25}

Hebrew goes through Phonikud first, which writes the vowel marks (nikud) and the stress: without them
Chatterbox's Hebrew is barely intelligible (its own authors' example does this), and Piper's voice is trained on
Phonikud's phonemes. Text is spoken sentence by sentence (Chatterbox drifts on long inputs) and joined with a
short pause. Lines written for whoever started it:  PROGRESS 0.4   DONE <seconds> <out>   FAILED <why>
"""
import json
import os
import re
import sys


def say(*words):
    print(*words, flush=True)


def sentences(text):
    parts = re.split(r"(?<=[.!?׃:;])\s+|\n+", text.strip())
    out = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        # very long sentences are cut at commas so no piece runs past ~25 s of speech
        while len(p) > 220 and "," in p[:220]:
            cut = p[:220].rfind(",") + 1
            out.append(p[:cut].strip())
            p = p[cut:].strip()
        out.append(p)
    return out


def main():
    home, request = sys.argv[1], sys.argv[2]
    with open(request, encoding="utf-8") as f:
        r = json.load(f)
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    import numpy as np
    import soundfile as sf

    lang = r.get("language", "he")
    text = r["text"]
    pieces = sentences(text)
    if not pieces:
        say("FAILED", "no text")
        return 2

    phonikud = None
    if lang == "he":
        # Phonikud asks the Hub for dicta's tokenizer on every start; it is kept beside the model instead.
        from tokenizers import Tokenizer
        local_tokenizer = os.path.join(home, "phonikud", "dictabert-large-char-menaked.tokenizer.json")
        hub_loader = Tokenizer.from_pretrained
        Tokenizer.from_pretrained = staticmethod(
            lambda name, *a, **k: Tokenizer.from_file(local_tokenizer) if "dictabert" in name else hub_loader(name, *a, **k))
        from phonikud_onnx import Phonikud
        phonikud = Phonikud(os.path.join(home, "phonikud", "phonikud-1.0.int8.onnx"))

    engine = r.get("engine", "chatterbox")
    pause = float(r.get("pause", 0.25))
    audio, rate = [], None

    if engine == "piper":
        if lang != "he":
            say("FAILED", "the Piper voice speaks Hebrew only")
            return 2
        from phonikud import phonemize
        from piper_onnx import Piper
        piper = Piper(os.path.join(home, "phonikud", "piper-he-shaul.onnx"), os.path.join(home, "phonikud", "piper-he-shaul.onnx.json"))
        speed = float(r.get("speed", 1.0))
        for i, p in enumerate(pieces):
            phonemes = phonemize(phonikud.add_diacritics(p))
            samples, rate = piper.create(phonemes, is_phonemes=True, length_scale=1.2 / max(0.5, speed),
                                         noise_scale=0.64, noise_w=1.0)
            audio.append(np.asarray(samples, dtype=np.float32))
            audio.append(np.zeros(int(rate * pause), dtype=np.float32))
            say("PROGRESS", round((i + 1) / len(pieces), 3))
    else:
        import torch
        from safetensors.torch import load_file
        from chatterbox.models.s3gen import S3Gen
        from chatterbox.models.t3 import T3
        from chatterbox.models.t3.modules.t3_config import T3Config
        from chatterbox.models.tokenizers import MTLTokenizer
        from chatterbox.models.voice_encoder import VoiceEncoder
        from chatterbox.mtl_tts import ChatterboxMultilingualTTS, Conditionals
        device = "cuda" if torch.cuda.is_available() else "cpu"
        ck = os.path.join(home, "chatterbox")
        ve = VoiceEncoder()
        ve.load_state_dict(load_file(os.path.join(ck, "ve.safetensors")))
        ve.to(device).eval()
        t3 = T3(T3Config.multilingual())
        state = load_file(os.path.join(ck, "t3_mtl23ls_v2.safetensors"))
        if "model" in state:
            state = state["model"][0]
        t3.load_state_dict(state)
        t3.to(device).eval()
        s3 = S3Gen()
        s3.load_state_dict(load_file(os.path.join(ck, "s3gen.safetensors")), strict=False)
        s3.to(device).eval()
        tok = MTLTokenizer(os.path.join(ck, "grapheme_mtl_merged_expanded_v1.json"))
        conds = Conditionals.load(os.path.join(ck, "conds.pt"), map_location="cpu").to(device)
        model = ChatterboxMultilingualTTS(t3, s3, ve, tok, device, conds=conds)
        rate = model.sr
        reference = r.get("reference") or None
        seed = int(r.get("seed", 0))
        from phonikud import lexicon
        for i, p in enumerate(pieces):
            if phonikud is not None:
                p = phonikud.add_diacritics(p)
                p = re.sub(f"[{lexicon.NON_STANDARD_DIAC}]", "", p)
            if seed:
                torch.manual_seed(seed + i)
            wav = model.generate(p, language_id=lang, audio_prompt_path=reference,
                                 exaggeration=float(r.get("exaggeration", 0.5)), cfg_weight=float(r.get("cfg", 0.5)),
                                 temperature=float(r.get("temperature", 0.8)))
            audio.append(wav.squeeze(0).float().cpu().numpy())
            audio.append(np.zeros(int(rate * pause), dtype=np.float32))
            say("PROGRESS", round((i + 1) / len(pieces), 3))

    data = np.concatenate(audio[:-1]) if len(audio) > 1 else audio[0]
    peak = float(np.abs(data).max()) or 1.0
    data = data * min(1.0, 0.95 / peak) if peak > 0.95 else data
    sf.write(r["out"], data, rate)
    say("DONE", round(len(data) / rate, 3), r["out"])
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001 - reported to the caller as one line
        import traceback
        traceback.print_exc()
        say("FAILED", f"{type(e).__name__}: {e}")
        sys.exit(2)

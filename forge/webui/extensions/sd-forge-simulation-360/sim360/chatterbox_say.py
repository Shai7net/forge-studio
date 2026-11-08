"""Speaks with Chatterbox (Resemble AI's speech model: a voice that acts, and can take its timbre from a few
seconds of another voice). This file is not part of Forge: it is run by Chatterbox's own Python (speech.py
starts it), so nothing Chatterbox needs is installed into Forge's environment.

    python chatterbox_say.py <weights folder> say <text file> <out.wav> [--voice ref.wav] [--exaggeration 0.5]
                             [--cfg 0.5] [--temperature 0.8] [--seed 0]
    python chatterbox_say.py <weights folder> batch <jobs.json>

`batch`: many lines with the model loaded once. jobs.json is a list of {text, out, voice, exaggeration, cfg,
temperature, seed}; after each line "SPOKEN <seconds> <out>" is written.
exaggeration: how much it acts (0.25 flat .. 1 dramatic); cfg: how closely it keeps to the reference voice's
pace (lower = slower, freer). Sound comes out at the model's own rate (24 kHz), mono.
"""
import argparse
import json
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("weights")
    parser.add_argument("what", choices=["say", "batch"])
    parser.add_argument("first")
    parser.add_argument("out", nargs="?")
    parser.add_argument("--voice")
    parser.add_argument("--exaggeration", type=float, default=0.5)
    parser.add_argument("--cfg", type=float, default=0.5)
    parser.add_argument("--temperature", type=float, default=0.8)
    parser.add_argument("--seed", type=int, default=0)
    a = parser.parse_args()

    import torch
    import torchaudio
    from chatterbox.tts import ChatterboxTTS
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = ChatterboxTTS.from_local(a.weights, device)

    def speak(text, out, voice=None, exaggeration=0.5, cfg=0.5, temperature=0.8, seed=0):
        if seed:
            torch.manual_seed(int(seed))
        wave = model.generate(text, audio_prompt_path=voice or None, exaggeration=float(exaggeration), cfg_weight=float(cfg), temperature=float(temperature))
        torchaudio.save(out, wave.cpu(), model.sr)
        print(f"SPOKEN {wave.shape[-1] / model.sr:.2f} {out}", flush=True)

    if a.what == "say":
        with open(a.first, encoding="utf8") as file:
            speak(file.read().strip(), a.out, a.voice, a.exaggeration, a.cfg, a.temperature, a.seed)
    else:
        with open(a.first, encoding="utf8") as file:
            jobs = json.load(file)
        for job in jobs:
            speak(job["text"], job["out"], job.get("voice"), job.get("exaggeration", 0.5), job.get("cfg", 0.5), job.get("temperature", 0.8), job.get("seed", 0))
    return 0


if __name__ == "__main__":
    sys.exit(main())

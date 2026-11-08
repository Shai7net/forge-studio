"""Sound for a silent video with MMAudio (large 44.1 kHz v2): the sound is generated from the frames themselves
(what is seen and when it moves) and an optional text prompt. Runs in MMAudio's own environment, from its own
folder (MMAudio-main beside the forge folder, the user's install), never inside Forge.

    python mmaudio_v2a.py <request.json>

request.json: {"video", "out" (.wav), "prompt", "negative", "seed", "steps": 25, "cfg": 4.5, "work"}

MMAudio is trained on 8 s clips. A longer video is cut into 8 s windows that overlap by 1.5 s; each window gets
its own sound and the overlaps are crossfaded with equal-power curves, so a 20 s video does not drift or loop.
Lines for the caller:  PROGRESS 0.4   DONE <seconds> <out>   FAILED <why>
"""
import json
import math
import os
import subprocess
import sys

WINDOW, OVERLAP = 8.0, 1.5


def say(*words):
    print(*words, flush=True)


def probe_duration(ffmpeg, path):
    out = subprocess.run([ffmpeg, "-i", path], capture_output=True, text=True, errors="replace").stderr
    for line in out.splitlines():
        if "Duration:" in line:
            h, m, s = line.split("Duration:")[1].split(",")[0].strip().split(":")
            return int(h) * 3600 + int(m) * 60 + float(s)
    raise RuntimeError("cannot read the video's length")


def main():
    with open(sys.argv[1], encoding="utf-8") as f:
        r = json.load(f)
    # MMAudio is not installed as a package: it runs from its own folder (the caller makes that the working folder).
    sys.path.insert(0, r.get("home") or os.getcwd())
    import torch
    import torchaudio
    from mmaudio.eval_utils import all_model_cfg, generate, load_video
    from mmaudio.model.flow_matching import FlowMatching
    from mmaudio.model.networks import get_my_mmaudio
    from mmaudio.model.utils.features_utils import FeaturesUtils

    ffmpeg = r.get("ffmpeg") or "ffmpeg"
    work = r["work"]
    os.makedirs(work, exist_ok=True)
    total = probe_duration(ffmpeg, r["video"])

    cfg = all_model_cfg["large_44k_v2"]
    cfg.download_if_needed()
    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.bfloat16
    net = get_my_mmaudio(cfg.model_name).to(device, dtype).eval()
    net.load_weights(torch.load(cfg.model_path, map_location=device, weights_only=True))
    feats = FeaturesUtils(tod_vae_ckpt=cfg.vae_path, synchformer_ckpt=cfg.synchformer_ckpt, enable_conditions=True,
                          mode=cfg.mode, bigvgan_vocoder_ckpt=cfg.bigvgan_16k_path, need_vae_encoder=False).to(device, dtype).eval()
    seq = cfg.seq_cfg
    rate = seq.sampling_rate
    steps = int(r.get("steps", 25))
    fm = FlowMatching(min_sigma=0, inference_mode="euler", num_steps=steps)

    if total <= WINDOW + 2.0:
        starts = [0.0]
        length = total
    else:
        n = math.ceil((total - OVERLAP) / (WINDOW - OVERLAP))
        step = (total - WINDOW) / max(1, n - 1)
        starts = [round(i * step, 3) for i in range(n)]
        length = WINDOW

    pieces = []
    for i, start in enumerate(starts):
        clip = os.path.join(work, f"window_{i}.mp4")
        subprocess.run([ffmpeg, "-loglevel", "error", "-y", "-ss", f"{start:.3f}", "-i", r["video"], "-t", f"{length:.3f}",
                        "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", clip], check=True)
        if r.get("text_only"):
            # From the words alone: a talking face on screen makes MMAudio murmur (heard as "Speech 0.53, Heart
            # sounds 0.40" under a dialogue, 2026-10-24), so a background under speech is not made from the picture.
            clip_frames = sync_frames = None
            seq.duration = length
        else:
            info = load_video(clip, length)
            seq.duration = info.duration_sec
            clip_frames, sync_frames = info.clip_frames.unsqueeze(0), info.sync_frames.unsqueeze(0)
        net.update_seq_lengths(seq.latent_seq_len, seq.clip_seq_len, seq.sync_seq_len)
        rng = torch.Generator(device=device)
        rng.manual_seed(int(r.get("seed", 42)) + i)
        with torch.inference_mode():
            audio = generate(clip_frames, sync_frames, [r.get("prompt", "")],
                             negative_text=[r.get("negative", "")], feature_utils=feats, net=net, fm=fm, rng=rng,
                             cfg_strength=float(r.get("cfg", 4.5)))
        pieces.append((start, audio.float().cpu()[0]))
        say("PROGRESS", round((i + 1) / len(starts), 3))

    out = torch.zeros(pieces[0][1].shape[0], int(round(total * rate)) + rate)
    weight = torch.zeros(1, out.shape[1])
    fade = int(OVERLAP * rate)
    for i, (start, audio) in enumerate(pieces):
        a = audio.shape[1]
        w = torch.ones(1, a)
        if len(pieces) > 1:
            ramp = torch.sin(torch.linspace(0, math.pi / 2, fade)) ** 2
            if i > 0:
                w[:, :fade] = ramp
            if i < len(pieces) - 1:
                w[:, -fade:] = ramp.flip(0)
        s = int(round(start * rate))
        out[:, s:s + a] += audio * w
        weight[:, s:s + a] += w
    out = out / weight.clamp(min=1e-6)
    out = out[:, :int(round(total * rate))]
    torchaudio.save(r["out"], out, rate)
    say("DONE", round(out.shape[1] / rate, 3), r["out"])
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001 - reported to the caller as one line
        import traceback
        traceback.print_exc()
        say("FAILED", f"{type(e).__name__}: {e}")
        sys.exit(2)

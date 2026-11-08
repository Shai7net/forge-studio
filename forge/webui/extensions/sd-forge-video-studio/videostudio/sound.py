"""Sound and speech for a finished video: AI sound effects (MMAudio), a voice from text (Chatterbox with Phonikud
for Hebrew, or Piper) or from the user's own recording, lips moved to the words (MuseTalk 1.5), and a mix.

Every model runs in its own Python, as a separate process, the way the video engine does:
  voices    webui/video-studio/voice-env      helpers/voice_say.py      (Chatterbox 0.1.7 + GPU PyTorch 2.6, Phonikud, Piper)
  effects   ../../MMAudio-main            helpers/mmaudio_v2a.py    (the user's MMAudio install and its venv)
  lips      models/sim360-musetalk            helpers/musetalk_say.py   (MuseTalk 1.5, Simulation 360's install)
A job runs in a thread of its own and keeps Forge's queue until it ends, so it survives the page going away.
"""

import json
import os
import re
import shutil
import subprocess
import threading
import time

from . import HOME_DIR, MODELS_DIR, WEBUI_DIR

HELPERS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "helpers")
VOICES_DIR = os.path.join(MODELS_DIR, "voices")
VOICE_ENV = os.path.join(HOME_DIR, "voice-env")
MMAUDIO_HOME = os.path.normpath(os.path.join(WEBUI_DIR, "..", "..", "MMAudio-main"))
MUSETALK_HOME = os.path.join(WEBUI_DIR, "models", "sim360-musetalk")
HF_CACHE = os.path.normpath(os.path.join(WEBUI_DIR, "..", "..", "_cache", "hf"))

# Chatterbox's 23 languages (the multilingual model's list), Hebrew first.
LANGUAGES = {"Hebrew": "he", "English": "en", "Arabic": "ar", "Russian": "ru", "French": "fr", "Spanish": "es",
             "German": "de", "Italian": "it", "Portuguese": "pt", "Polish": "pl", "Turkish": "tr", "Dutch": "nl",
             "Greek": "el", "Hindi": "hi", "Japanese": "ja", "Korean": "ko", "Chinese": "zh", "Danish": "da",
             "Finnish": "fi", "Malay": "ms", "Norwegian": "no", "Swedish": "sv", "Swahili": "sw"}
PIPER = "Fast Hebrew voice (Piper)"
CLONE = "Copy the voice of my recording"


def ffmpeg():
    for p in (shutil.which("ffmpeg"), r"C:\ProgramData\chocolatey\bin\ffmpeg.exe"):
        if p and os.path.isfile(p):
            return p
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


def reference_voices():
    folder = os.path.join(VOICES_DIR, "reference")
    if not os.path.isdir(folder):
        return {}
    return {os.path.splitext(f)[0]: os.path.join(folder, f) for f in sorted(os.listdir(folder))
            if f.lower().endswith((".wav", ".mp3", ".flac", ".ogg"))}


def voice_choices():
    return list(reference_voices()) + [CLONE, PIPER]


def status():
    """What is installed, for the Models section."""
    def ok(path):
        return os.path.exists(path)
    return {
        "voices": ok(os.path.join(VOICE_ENV, "Scripts", "python.exe")) and ok(os.path.join(VOICES_DIR, "chatterbox", "t3_mtl23ls_v2.safetensors")),
        "piper": ok(os.path.join(VOICES_DIR, "phonikud", "piper-he-shaul.onnx")),
        "effects": ok(os.path.join(MMAUDIO_HOME, "venv", "Scripts", "python.exe")) and ok(os.path.join(MMAUDIO_HOME, "weights", "mmaudio_large_44k_v2.pth")),
        "lips": ok(os.path.join(MUSETALK_HOME, "env", "Scripts", "python.exe")) and ok(os.path.join(MUSETALK_HOME, "app", "models", "musetalkV15", "unet.pth")),
    }


def duration(path):
    out = subprocess.run([ffmpeg(), "-hide_banner", "-i", path], capture_output=True, text=True, errors="replace").stderr
    m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", out)
    if not m:
        raise RuntimeError(f"cannot read the length of {os.path.basename(path)}")
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


def video_info(path):
    out = subprocess.run([ffmpeg(), "-hide_banner", "-i", path], capture_output=True, text=True, errors="replace").stderr
    m = re.search(r"Video:.*?(\d{2,5})x(\d{2,5}).*?([\d.]+) fps", out)
    if not m:
        raise RuntimeError(f"{os.path.basename(path)} has no video stream")
    return int(m.group(1)), int(m.group(2)), float(m.group(3))


def _clean_env(extra=None):
    """What Forge set for itself must not reach another program: its Hugging Face folders, its Python paths."""
    env = {k: v for k, v in os.environ.items()
           if not re.match(r"(HF_|HUGGINGFACE_|TRANSFORMERS_|DIFFUSERS_|PYTHON|TORCH_HOME|VIRTUAL_ENV)", k, re.I)}
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    env.update(extra or {})
    return env


def _run_helper(task, stage, command, cwd, env, span):
    """Runs a helper, turning its PROGRESS lines into the task's progress inside `span` (a, b)."""
    lines = []
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.Popen(command, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace", creationflags=flags)
    task.proc = proc
    result = None
    for line in proc.stdout:
        lines.append(line.rstrip())
        lines[:] = lines[-80:]
        m = re.match(r"PROGRESS\s+([0-9.]+)", line)
        if m:
            task.push(stage, span[0] + (span[1] - span[0]) * min(1.0, float(m.group(1))))
        if line.startswith("DONE"):
            result = line.split()[1:]
        if line.startswith("FAILED"):
            raise RuntimeError(line[6:].strip() or "failed")
        if task.cancelled:
            proc.kill()
            raise RuntimeError("stopped")
    code = proc.wait()
    if code != 0 or result is None:
        raise RuntimeError(f"{os.path.basename(command[-1] if command else '')} stopped (exit code {code}):\n" + "\n".join(lines[-12:]))
    return result


def make_speech(task, text, language, voice, out, recording=None, exaggeration=0.5, seed=0, span=(0, 1)):
    engine = "piper" if voice == PIPER else "chatterbox"
    reference = None
    if voice == CLONE:
        if not recording:
            raise RuntimeError("To copy a voice, give a recording of it (5-15 seconds of clear speech).")
        reference = recording
    elif voice in reference_voices():
        reference = reference_voices()[voice]
    request = os.path.join(task.dir, "voice.json")
    with open(request, "w", encoding="utf-8") as f:
        json.dump({"text": text, "out": out, "engine": engine, "language": LANGUAGES.get(language, "he"),
                   "reference": reference, "exaggeration": float(exaggeration), "seed": int(seed)}, f, ensure_ascii=False)
    python = os.path.join(VOICE_ENV, "Scripts", "python.exe")
    _run_helper(task, "voice", [python, "-I", "-u", "-X", "utf8", os.path.join(HELPERS, "voice_say.py"), VOICES_DIR, request],
                task.dir, _clean_env({"HF_HUB_OFFLINE": "1", "HF_HOME": os.path.join(HOME_DIR, "cache", "hf")}), span)
    return out


def make_effects(task, video, prompt, negative, seed, out, span=(0, 1), text_only=False):
    request = os.path.join(task.dir, "effects.json")
    with open(request, "w", encoding="utf-8") as f:
        json.dump({"video": video, "out": out, "prompt": prompt or "", "negative": negative or "", "seed": int(seed),
                   "text_only": bool(text_only),
                   "work": os.path.join(task.dir, "effects"), "ffmpeg": ffmpeg(), "home": MMAUDIO_HOME}, f, ensure_ascii=False)
    python = os.path.join(MMAUDIO_HOME, "venv", "Scripts", "python.exe")
    _run_helper(task, "effects", [python, "-u", "-X", "utf8", os.path.join(HELPERS, "mmaudio_v2a.py"), request],
                MMAUDIO_HOME, _clean_env({"HF_HOME": HF_CACHE}), span)
    return out


def _segment(video, start, length, fps, out):
    """[start, start + length) of the video at its own frame rate; past its end the last second plays back and
    forth (a still frame would freeze the face while it talks)."""
    total = duration(video)
    ff = ffmpeg()
    if start + length <= total + 1e-3:
        subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{start:.3f}", "-i", video, "-t", f"{length:.3f}",
                        "-an", "-r", f"{fps:g}", "-c:v", "libx264", "-crf", "12", "-preset", "fast", out], check=True)
        return
    tail = min(1.0, total - start) if total - start > 0.2 else 1.0
    tail_start = max(0.0, total - tail)
    need = start + length - total
    loops = int(need / (2 * tail)) + 2
    chain = "".join(f"[f{i}][r{i}]" for i in range(loops))
    graph = (f"[0:v]trim=start={start:.3f},setpts=PTS-STARTPTS[main];"
             f"[0:v]trim=start={tail_start:.3f},setpts=PTS-STARTPTS,split={loops}" + "".join(f"[t{i}]" for i in range(loops)) + ";"
             + "".join(f"[t{i}]split[f{i}][rr{i}];[rr{i}]reverse[r{i}];" for i in range(loops))
             + f"[main]{chain}concat=n={1 + 2 * loops}:v=1:a=0,trim=duration={length:.3f}[v]")
    subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-y", "-i", video, "-filter_complex", graph, "-map", "[v]",
                    "-r", f"{fps:g}", "-c:v", "libx264", "-crf", "12", "-preset", "fast", out], check=True)


def lipsync(task, video, speech, start, out, span=(0, 1)):
    """The mouth drawn again for the words over the whole clip: silence before `start` and after the words keeps
    it closed. Drawing only the spoken part made the lower face jump from the video's own skin to MuseTalk's
    smoother one where the words began (seen 2026-10-24); over the whole clip the look stays the same."""
    w, h, fps = video_info(video)
    total = duration(video)
    said = duration(speech)
    length = max(total, start + said)
    seg = os.path.join(task.dir, "talk_in.mp4")
    _segment(video, 0.0, length, fps, seg)
    padded = os.path.join(task.dir, "talk_sound.wav")
    delay = int(round(start * 1000))
    subprocess.run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-i", speech,
                    "-af", f"aresample=16000,adelay={delay}|{delay},apad,atrim=duration={length:.3f}", "-ac", "1", padded], check=True)
    python = os.path.join(MUSETALK_HOME, "env", "Scripts", "python.exe")
    work = os.path.join(MUSETALK_HOME, "work", f"vs-{time.time_ns()}")
    env = _clean_env({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HOME": os.path.join(MUSETALK_HOME, "hf"),
                      "TORCH_HOME": os.path.join(MUSETALK_HOME, "torch")})
    talk = os.path.join(task.dir, "talk_out.mp4")
    try:
        _run_helper(task, "lips", [python, "-I", "-u", "-X", "utf8", os.path.join(HELPERS, "musetalk_say.py"),
                                   os.path.join(MUSETALK_HOME, "app"), seg, padded, talk, "--ffmpeg", ffmpeg(), "--work", work],
                    os.path.join(MUSETALK_HOME, "app"), env, span)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    subprocess.run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-i", talk,
                    "-vf", f"fps={fps:g},scale={w}:{h},format=yuv420p", "-c:v", "libx264", "-crf", "15", "-preset", "medium",
                    "-colorspace", "smpte170m", "-color_primaries", "smpte170m", "-color_trc", "smpte170m",
                    "-movflags", "+faststart", out], check=True)
    return out


def mix(video, out, effects=None, speech=None, start=0.0, effects_db=-6.0, speech_db=0.0):
    """Puts the sound on the video: speech at `start`, effects ducked under it (sidechain, so a voice is never
    drowned by its own background), the whole mix at -14 LUFS, the picture copied as it is."""
    ff = ffmpeg()
    length = duration(video)
    inputs = ["-i", video]
    graph = []
    if effects:
        inputs += ["-i", effects]
        graph.append(f"[{len(inputs) // 2 - 1}:a]aresample=48000,volume={effects_db}dB,apad,atrim=duration={length:.3f}[fx]")
    if speech:
        inputs += ["-i", speech]
        delay = int(round(start * 1000))
        graph.append(f"[{len(inputs) // 2 - 1}:a]aresample=48000,volume={speech_db}dB,adelay={delay}|{delay},apad,atrim=duration={length:.3f}[sp]")
    if effects and speech:
        graph.append("[sp]asplit[sp1][sp2]")
        graph.append("[fx][sp1]sidechaincompress=threshold=0.03:ratio=6:attack=20:release=400[duck]")
        graph.append("[duck][sp2]amix=inputs=2:normalize=0[mix]")
    elif effects:
        graph.append("[fx]anull[mix]")
    else:
        graph.append("[sp]anull[mix]")
    graph.append("[mix]loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[a]")
    subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-y", *inputs, "-filter_complex", ";".join(graph),
                    "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ac", "2",
                    "-movflags", "+faststart", out], check=True)
    # (no -shortest: the sound is already padded and cut to the picture's length, and -shortest with a copied
    # picture dropped the last 4 of 33 frames of a 1 s clip)
    return out


class Task:
    """A sound job in its own thread; `events` is read by the page (any number of times, or not at all)."""

    def __init__(self, folder):
        self.id = time.strftime("%Y%m%d-%H%M%S")
        self.dir = os.path.join(folder, "sound-" + self.id)
        os.makedirs(self.dir, exist_ok=True)
        self.events = []
        self.ended = False
        self.cancelled = False
        self.proc = None
        self.cond = threading.Condition()

    def push(self, stage, progress=None, **extra):
        with self.cond:
            self.events.append(dict(stage=stage, progress=progress, **extra))
            self.cond.notify_all()

    def cancel(self):
        self.cancelled = True
        if self.proc is not None and self.proc.poll() is None:
            self.proc.kill()

    def run(self):
        seen = 0
        while True:
            with self.cond:
                while seen >= len(self.events) and not self.ended:
                    self.cond.wait(1.0)
                batch = self.events[seen:]
                seen = len(self.events)
                ended = self.ended
            yield from batch
            if ended and seen >= len(self.events):
                return


_current = {"task": None}


def busy():
    return _current["task"] is not None


def cancel_current():
    t = _current["task"]
    if t is not None:
        t.cancel()
        return True
    return False


def start(job, folder):
    """job: {video, out, effects: {on, prompt, negative, seed, db}, speech: {mode: none|recording|text, text,
    language, voice, recording, exaggeration, seed, start, db, lips}}. Returns the running Task."""
    if _current["task"] is not None:
        raise RuntimeError("Sound is already being made for a video.")
    task = Task(folder)
    _current["task"] = task
    threading.Thread(target=_work, args=(task, job), daemon=True, name="video-studio-sound").start()
    return task


def _work(task, job):
    from modules.call_queue import queue_lock
    from . import runner
    try:
        with queue_lock:
            runner.free_forge_memory()
            video = job["video"]
            fx, sp = job.get("effects") or {}, job.get("speech") or {}
            speech_wav = effects_wav = None
            picture = video
            mode = sp.get("mode", "none")
            plan = [s for s, on in (("voice", mode == "text"), ("effects", fx.get("on")), ("lips", mode != "none" and sp.get("lips"))) if on]
            spans, step = {}, 0.9 / max(1, len(plan))
            for i, s in enumerate(plan):
                spans[s] = (i * step, (i + 1) * step)
            if mode == "text":
                task.push("voice", 0.0, note="writing the voice")
                speech_wav = make_speech(task, sp["text"], sp.get("language", "Hebrew"), sp.get("voice"),
                                         os.path.join(task.dir, "speech.wav"), sp.get("recording"),
                                         sp.get("exaggeration", 0.5), sp.get("seed", 0), spans["voice"])
            elif mode == "recording":
                if not sp.get("recording"):
                    raise RuntimeError("Choose or record the sound the character says.")
                speech_wav = os.path.join(task.dir, "speech.wav")
                subprocess.run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-i", sp["recording"], "-vn",
                                "-ac", "1", "-ar", "44100", speech_wav], check=True)
            if fx.get("on"):
                task.push("effects", spans["effects"][0], note="listening to the picture")
                # Under speech the background comes from the words only (a talking face makes MMAudio murmur);
                # without words to go by, it still has to come from the picture.
                words_only = mode != "none" and bool((fx.get("prompt") or "").strip())
                effects_wav = make_effects(task, video, fx.get("prompt"), fx.get("negative"), fx.get("seed", 0),
                                           os.path.join(task.dir, "effects.wav"), spans["effects"], text_only=words_only)
            if speech_wav and sp.get("lips"):
                task.push("lips", spans["lips"][0], note="moving the lips to the words")
                try:
                    picture = lipsync(task, video, speech_wav, float(sp.get("start", 0.0)),
                                      os.path.join(task.dir, "lips.mp4"), spans["lips"])
                except RuntimeError as e:
                    if "no face" not in str(e):
                        raise
                    task.push("lips", spans["lips"][1], note="no face was found: the voice is added without moving lips")
            task.push("mixing", 0.93)
            mix(picture, job["out"], effects=effects_wav, speech=speech_wav, start=float(sp.get("start", 0.0)),
                effects_db=float(fx.get("db", -6.0)), speech_db=float(sp.get("db", 0.0)))
            task.push("done", 1.0, video=job["out"])
    except Exception as e:  # noqa: BLE001 - shown on the page
        import traceback
        traceback.print_exc()
        task.push("cancelled" if task.cancelled else "error", None, error=f"{type(e).__name__}: {e}")
    finally:
        with task.cond:
            task.ended = True
            task.cond.notify_all()
        _current["task"] = None

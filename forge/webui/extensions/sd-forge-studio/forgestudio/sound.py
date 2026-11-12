"""Sound and words for the timeline, made by the tools already on this PC, each in its own Python:

- captions: Whisper large-v2 (auto-subtitle-main beside the forge folder) on the edit's own sound, mixed down
  by render.py;
- narration: Video Studio's voices (Chatterbox multilingual with Phonikud niqqud for Hebrew, or Piper);
- music: ACE-Step 1.5 turbo (ACE-Step-1.5 beside the forge folder), helpers/music_make.py;
- effects for a clip: MMAudio from the clip's picture (Video Studio's helper).

Each request runs as a task (frames.tasks, followed by the page) under Forge's queue, with Forge's models put
aside first, so it never shares the 8 GB card with a picture being drawn.
"""

import json
import math
import os
import re
import subprocess
import sys
import time
import types
import uuid

from . import frames, library, media, paths, projects

SD_ROOT = os.path.normpath(os.path.join(paths.WEBUI, "..", ".."))
WHISPER_PY = os.path.join(SD_ROOT, "auto-subtitle-main", "venv", "Scripts", "python.exe")
ACE_HOME = os.path.join(SD_ROOT, "ACE-Step-1.5")
ACE_PY = os.path.join(ACE_HOME, ".venv", "Scripts", "python.exe")
HF_CACHE = os.path.join(SD_ROOT, "_cache", "hf")
HELPERS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "helpers")
CAPTION_LANGUAGES = {"Hebrew": "he", "English": "en", "Arabic": "ar", "Russian": "ru", "Auto": None}


def _vs_sound():
    from .generate import _vs
    _vs()  # loads Video Studio
    return sys.modules["videostudio.sound"]


def available():
    out = {"captions": os.path.isfile(WHISPER_PY), "music": os.path.isfile(ACE_PY), "voices": [], "voice": False,
           "effects": False, "languages": list(CAPTION_LANGUAGES)}
    try:
        vs = _vs_sound()
        have = vs.status()
        out.update(voice=bool(have["voices"] or have["piper"]), effects=bool(have["effects"]),
                   voices=vs.voice_choices()[:-2] + ([vs.PIPER] if have["piper"] else []),
                   speech_languages=list(vs.LANGUAGES))
    except Exception as e:
        out["message"] = f"Video Studio's sound is not available: {e}"
    return out


def _folder(name, *parts):
    folder = os.path.join(projects._folder(name), "sound", *parts)
    os.makedirs(folder, exist_ok=True)
    return folder


class _Task:
    """What Video Studio's sound helpers expect of a task (a folder, progress, cancelling)."""

    def __init__(self, state, folder):
        self.state, self.dir, self.cancelled, self.proc = state, folder, False, None

    def push(self, stage, progress=None, **_):
        self.state.update(text=stage, progress=progress if progress is not None else self.state.get("progress", 0))


def _on_card(fn):
    """Runs fn under Forge's queue with Forge's models off the card."""
    from modules.call_queue import queue_lock
    with queue_lock:
        try:
            from .generate import _vs
            _vs().runner.free_forge_memory()
        except Exception:
            pass
        return fn()


def _env(extra=None):
    env = {k: v for k, v in os.environ.items()
           if not re.match(r"(HF_|HUGGINGFACE_|TRANSFORMERS_|DIFFUSERS_|PYTHON|TORCH_HOME|VIRTUAL_ENV)", k, re.I)}
    env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"})
    env.update(extra or {})
    return env


def _run(command, cwd, env, task, log):
    """A helper process; output to a log file (an unread pipe stalls a long run), PROGRESS lines followed."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    with open(log, "w", encoding="utf-8", errors="replace") as out:
        proc = subprocess.Popen(command, cwd=cwd, env=env, stdout=out, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, creationflags=flags)
        while proc.poll() is None:
            time.sleep(1)
            try:
                with open(log, encoding="utf-8", errors="replace") as f:
                    tail = f.read()[-4000:]
                m = re.findall(r"PROGRESS\s+([0-9.]+)", tail)
                if m:
                    task["progress"] = float(m[-1])
            except OSError:
                pass
    with open(log, encoding="utf-8", errors="replace") as f:
        text = f.read()
    if proc.returncode != 0:
        lines = [l for l in text.strip().splitlines() if l.strip()]
        raise RuntimeError("\n".join(lines[-6:])[-800:] or f"stopped (exit code {proc.returncode})")
    return text


# ---- captions ----

def captions(name, doc, language="Hebrew"):
    return frames._task("captions", _captions, name, doc, language)


def _captions(task, name, doc, language):
    if not os.path.isfile(WHISPER_PY):
        raise RuntimeError(f"Whisper is not installed ({os.path.dirname(os.path.dirname(os.path.dirname(WHISPER_PY)))})")
    from . import render
    work = _folder(name, "captions-" + uuid.uuid4().hex[:6])
    task["text"] = "mixing the sound of the edit"
    wav = render.mixdown(name, doc, os.path.join(work, "sequence.wav"))
    task.update(text="Whisper is listening (large-v2)", progress=0.2)
    lang = CAPTION_LANGUAGES.get(language, "he")
    cmd = [WHISPER_PY, "-m", "whisper", wav, "--model", "large-v2", "--output_format", "json", "--output_dir", work,
           "--verbose", "False", "--fp16", "True", "--condition_on_previous_text", "False", "--word_timestamps", "True"]
    if lang:
        cmd += ["--language", lang]
    _on_card(lambda: _run(cmd, work, _env(), task, os.path.join(work, "whisper.log")))
    with open(os.path.join(work, "sequence.json"), encoding="utf-8") as f:
        result = json.load(f)
    cues = []
    for s in result.get("segments", []):
        text = s.get("text", "").strip()
        if not text:
            continue
        words = s.get("words") or []
        # the segment's real start and end are its first and last word (a segment starts at the silence before)
        start = words[0]["start"] if words else s["start"]
        end = words[-1]["end"] if words else s["end"]
        cues.append({"start": round(start, 3), "end": round(max(end, start + 0.4), 3), "text": text})
    return {"cues": cues, "language": result.get("language"), "progress": 1.0, "text": f"{len(cues)} captions"}


# ---- narration ----

def voice(name, text, language="Hebrew", voice_name=None, expressive=0.5, seed=0):
    if not (text or "").strip():
        raise ValueError("write what is said")
    return frames._task("voice", _voice, name, text.strip(), language, voice_name, float(expressive), int(seed))


def _voice(task, name, text, language, voice_name, expressive, seed):
    vs = _vs_sound()
    choices = vs.voice_choices()
    voice_name = voice_name if voice_name in choices else choices[0]
    work = _folder(name, "voice-" + uuid.uuid4().hex[:6])
    out = os.path.join(_folder(name, "made"), f"voice-{uuid.uuid4().hex[:10]}.wav")
    t = _Task(task, work)
    task["text"] = "making the voice"
    _on_card(lambda: vs.make_speech(t, text, language, voice_name, out, exaggeration=expressive, seed=seed))
    return _result(name, out, f"{vs.duration(out):.1f} s of speech")


# ---- music ----

def music(name, caption, seconds, seed=-1):
    if not (caption or "").strip():
        raise ValueError("describe the music")
    seconds = _seconds(seconds, 240)
    return frames._task("music", _music, name, caption.strip(), seconds, int(seed))


def _music(task, name, caption, seconds, seed):
    if not os.path.isfile(ACE_PY):
        raise RuntimeError(f"ACE-Step is not installed ({ACE_HOME})")
    caption = _english(task, caption, "fs_music")
    work = _folder(name, "music-" + uuid.uuid4().hex[:8])
    out = os.path.join(_folder(name, "made"), f"music-{uuid.uuid4().hex[:10]}.wav")
    request = os.path.join(work, "request.json")
    with open(request, "w", encoding="utf-8") as f:
        json.dump({"home": ACE_HOME, "caption": caption, "seconds": max(5, seconds), "seed": seed, "out": out, "work": work,
                   "instrumental": True}, f, ensure_ascii=False)
    task["text"] = "ACE-Step is composing"
    _on_card(lambda: _run([ACE_PY, "-u", os.path.join(HELPERS, "music_make.py"), request], work,
                          _env({"HF_HOME": HF_CACHE}), task, os.path.join(work, "music.log")))
    if not os.path.isfile(out):
        raise RuntimeError("ACE-Step made nothing")
    _fit_audio(out, seconds)
    return _result(name, out, f"{seconds:.0f} s of music")


# ---- effects for a clip ----

def effects(name, web=None, words="", seed=0, seconds=None, start=0.0, speed=1.0):
    if not web and not (words or "").strip():
        raise ValueError("describe the sound effect")
    seconds = _seconds(seconds, 240) if seconds is not None else None
    start, speed = float(start), float(speed)
    if not math.isfinite(start) or start < 0 or not math.isfinite(speed) or not 0.1 <= speed <= 8:
        raise ValueError("invalid sound source range")
    return frames._task("effects", _effects, name, web, words or "", int(seed), seconds, start, speed)


def _effects(task, name, web, words, seed, seconds=None, start=0.0, speed=1.0):
    vs = _vs_sound()
    if not vs.status()["effects"]:
        raise RuntimeError("MMAudio is not installed")
    source = projects.media_path(name, web) if web else None
    if source and library.kind_of(source) != "video":
        raise ValueError("sound effects are made from a video clip")
    words = _english(task, words, "fs_effects") if words.strip() else ""
    seconds = seconds or (media.probe(source).get("duration") if source else 5)
    seconds = _seconds(seconds, 240)
    model_seconds = max(1.0, seconds)
    work = _folder(name, "effects-" + uuid.uuid4().hex[:8])
    out = os.path.join(_folder(name, "made"), f"effects-{uuid.uuid4().hex[:10]}.wav")
    carrier = os.path.join(work, "source.mp4")
    exe = library.ffmpeg()
    cmd = [exe, "-v", "error", "-y"]
    if source:
        cmd += ["-ss", str(start), "-i", source, "-vf",
                f"setpts=(PTS-STARTPTS)/{speed},scale=320:-2,tpad=stop_mode=clone:stop_duration={model_seconds},fps=25"]
    else:
        cmd += ["-f", "lavfi", "-i", "color=c=black:s=320x180:r=25"]
    cmd += ["-t", str(model_seconds), "-an", "-c:v", "libx264", "-preset", "ultrafast", carrier]
    subprocess.run(cmd, capture_output=True, check=True, timeout=180,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    t = _Task(task, work)
    task["text"] = "MMAudio is listening to the picture"
    _on_card(lambda: vs.make_effects(t, carrier, words, "music, speech, talking", seed, out, text_only=not bool(source)))
    if not os.path.isfile(out):
        raise RuntimeError("MMAudio made no sound")
    _fit_audio(out, seconds)
    return _result(name, out, f"{seconds:g} s of sound effects")


def _seconds(value, maximum=240):
    seconds = float(value)
    if not math.isfinite(seconds) or not 1 / 120 - 1e-9 <= seconds <= maximum:
        raise ValueError(f"duration must be between 1/120 and {maximum} seconds")
    return seconds


def _english(task, text, field):
    """Reuse the installed prompt writer, before taking the generation queue. Never feed an untranslated failure silently."""
    # Read module dictionaries directly: torch.classes and lazy modules run code on getattr.
    writer = next((m for m in list(sys.modules.values()) if isinstance(m, types.ModuleType)
                   and isinstance(vars(m).get("EXT_DIR"), str)
                   and vars(m)["EXT_DIR"].replace("\\", "/").endswith("/hebrew-translator")
                   and callable(vars(m).get("write"))), None)
    bridge = sys.modules.get("prompt_writer")
    if isinstance(bridge, types.ModuleType) and callable(vars(bridge).get("audio_write")):
        writer = types.SimpleNamespace(write=vars(bridge)["audio_write"], engine_name=vars(bridge)["audio_engine_name"])
    task.update(text="writing the audio prompt in English", source_prompt=text)
    if writer is None or (callable(getattr(writer, "engine_name", None)) and writer.engine_name() is None):
        if any(ord(c) > 127 and c.isalpha() for c in text):
            raise RuntimeError("The multilingual prompt writer is unavailable. Start the Prompt writer extension or enter an English prompt.")
        task["prompt"] = text
        return text
    while True:
        try:
            answer = writer.write(text, field)
            break
        except Exception as exc:
            if type(exc).__name__ != "Busy":
                raise
            task["text"] = "waiting for the card to write the audio prompt"
            time.sleep(1)
    prompt = (answer.get("prompt") or "").strip()
    if not prompt:
        raise RuntimeError("The audio prompt writer returned no English prompt. Try again.")
    task["prompt"] = prompt
    return prompt


def _fit_audio(path, seconds):
    target = os.path.splitext(path)[0] + "-fit.wav"
    subprocess.run([library.ffmpeg(), "-v", "error", "-y", "-i", path, "-af",
                    f"apad,atrim=duration={seconds:.9f},asetpts=PTS-STARTPTS", "-c:a", "pcm_s16le", target],
                   check=True, capture_output=True, timeout=180, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    os.replace(target, path)


def _result(name, path, text):
    web = projects.add_media(name, path)
    return {"web": web, "path": path, "progress": 1.0, "text": text}


def presets():
    """Export loudness choices (LUFS): streaming and social want -14, broadcast -23."""
    return [{"id": "", "label": "As mixed"}, {"id": "-14", "label": "-14 LUFS (YouTube, Instagram, TikTok)"},
            {"id": "-16", "label": "-16 LUFS (podcasts, Apple)"}, {"id": "-23", "label": "-23 LUFS (TV, EBU R128)"}]

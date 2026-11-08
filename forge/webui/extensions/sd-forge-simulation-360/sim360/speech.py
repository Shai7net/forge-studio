"""The sound of a character's line.

A line is either spoken by one of Windows' own voices (the ones under Settings > Time & language > Speech;
a voice added there shows up here) or it is an audio file the user brings: a recording, or speech made
anywhere else. Either way it ends up as the same thing, a tidy WAV file: one channel, silence cut from both
ends, a short breath of quiet put back, and every line about equally loud, so a dialogue does not jump.

    speech-<line>-0003.wav
"""

import os
import re
import subprocess
import threading
import wave

import numpy as np

from .runner import JobError

RATE = 44100
QUIET = 0.12                # seconds of quiet kept before and after the words
LOUDNESS = 0.11             # the level every line is brought to (RMS of its loud parts; 1.0 is full scale)
PEAK = 0.89
LONGEST = 120.0             # seconds; a longer line is cut

_CATEGORIES = (r"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech_OneCore\Voices",
               r"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech\Voices")
_lock = threading.Lock()
_voices = None


def _language(code):
    """'40D' -> 'he-IL'"""
    import locale
    try:
        return locale.windows_locale[int(str(code).split(";")[0], 16)].replace("_", "-")
    except (KeyError, ValueError):
        return ""


def _tokens():
    """Every installed voice: {name: token}. The newer voices (OneCore) come first; a 'Desktop' twin of a
    voice that exists there is left out."""
    import win32com.client
    found = {}
    for category in _CATEGORIES:
        try:
            listed = win32com.client.Dispatch("SAPI.SpObjectTokenCategory")
            listed.SetId(category, False)
            tokens = listed.EnumerateTokens()
        except Exception:
            continue
        for i in range(tokens.Count):
            token = tokens.Item(i)
            try:
                name = token.GetAttribute("Name")
            except Exception:
                continue
            short = re.sub(r"\s+Desktop$", "", name)
            if short not in found:
                found[short] = token
    return found


def _in_com(function):
    """Windows' speech objects live in the thread that asked for them."""
    try:
        import pythoncom
    except ImportError:
        raise JobError("Windows' voices cannot be reached from this Python (pywin32 is missing)")
    pythoncom.CoInitialize()
    try:
        return function()
    finally:
        pythoncom.CoUninitialize()


KOKORO = "kokoro:"          # how a Kokoro voice's id begins
_KOKORO_LANGUAGES = {"a": "en-US", "b": "en-GB", "e": "es-ES", "f": "fr-FR", "h": "hi-IN", "i": "it-IT", "j": "ja-JP", "p": "pt-BR", "z": "zh-CN"}
_kokoro_voices = None


def kokoro_home():
    """Kokoro's folder: its own small Python (env), its model and its voices. None when it is not installed.
    Kokoro is a small speech model whose English sounds like a person; it runs on the processor."""
    from modules import paths, shared
    wanted = str(shared.opts.data.get("sim360_kokoro_dir") or "").strip() or os.path.join(paths.models_path, "sim360-kokoro")
    python = os.path.join(wanted, "env", "Scripts", "python.exe")
    if not os.path.isfile(python):
        python = os.path.join(wanted, "env", "bin", "python")
    model, sounds = os.path.join(wanted, "kokoro-v1.0.onnx"), os.path.join(wanted, "voices-v1.0.bin")
    if all(os.path.isfile(p) for p in (python, model, sounds)):
        return {"python": python, "model": model, "voices": sounds}
    return None


def _kokoro(home, *arguments, timeout=600):
    helper = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kokoro_say.py")
    done = subprocess.run([home["python"], helper, home["model"], home["voices"], *arguments], capture_output=True, timeout=timeout,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    said = done.stdout.decode("utf8", "replace")
    if done.returncode != 0:
        raise JobError("Kokoro could not speak: " + done.stderr.decode("utf8", "replace").strip()[-300:])
    return said


def kokoro_voices():
    global _kokoro_voices
    if _kokoro_voices is None:
        _kokoro_voices = []
        try:
            home = kokoro_home()
            if home:
                import json
                listed = _kokoro(home, "list", timeout=120)
                names = json.loads(listed[listed.index("VOICES ") + 7:].splitlines()[0])
                _kokoro_voices = [{"id": KOKORO + n, "name": n[3:].capitalize() + " (Kokoro)", "language": _KOKORO_LANGUAGES.get(n[:1], ""),
                                   "gender": "Female" if n[1:2] == "f" else "Male"} for n in names]
        except Exception as e:
            print(f"[Simulation 360] Kokoro's voices could not be listed: {type(e).__name__}: {e}")
    return list(_kokoro_voices)


def _say_kokoro(text, voice, path, rate, pitch):
    """rate -10..10 becomes Kokoro's own speed; pitch is done afterwards, by playing the voice a little
    faster or slower and stretching it back to its length."""
    from . import export
    home = kokoro_home()
    if not home:
        raise JobError("Kokoro is not installed any more")
    words, spoken = path + ".txt", path + ".kokoro.wav"
    try:
        with open(words, "w", encoding="utf8") as file:
            file.write(text)
        _kokoro(home, "say", voice[len(KOKORO):], f"{max(0.5, min(1.8, 1 + rate * 0.05)):.3f}", words, spoken)
        factor = 2 ** (pitch * 0.4 / 12)                # the whole range is four semitones either way
        tone = f"asetrate=24000*{factor:.5f},aresample=24000,atempo={1 / factor:.5f}," if pitch else ""
        done = subprocess.run([export.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-i", spoken, "-af", tone + f"aresample={RATE}",
                               "-ac", "1", "-c:a", "pcm_s16le", path], capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if done.returncode != 0:
            raise JobError("Kokoro's sound could not be read: " + done.stderr.decode("utf8", "replace")[-200:])
    finally:
        for leftover in (words, spoken):
            try:
                os.remove(leftover)
            except OSError:
                pass
    return path


def voices(again=False):
    """[{id, name, language, gender}]: Kokoro's voices when it is installed, then Windows' own."""
    return kokoro_voices() + windows_voices(again)


def windows_voices(again=False):
    """[{id, name, language, gender}], or [] where Windows' voices cannot be reached."""
    global _voices
    with _lock:
        if _voices is None or again:
            def read():
                listed = []
                for name, token in _tokens().items():
                    def attribute(key):
                        try:
                            return token.GetAttribute(key)
                        except Exception:
                            return ""
                    listed.append({"id": name, "name": re.sub(r"^Microsoft\s+", "", name),
                                   "language": _language(attribute("Language")), "gender": attribute("Gender")})
                return sorted(listed, key=lambda v: (v["language"], v["name"]))
            try:
                _voices = _in_com(read)
            except Exception as e:
                print(f"[Simulation 360] Windows' voices could not be listed: {type(e).__name__}: {e}")
                _voices = []
        return list(_voices)


def say(text, voice, path, rate=0, pitch=0):
    """Speaks `text` into a WAV file. rate and pitch: -10..10, 0 is the voice as it is."""
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if not text:
        raise JobError("the line has no words")
    rate, pitch = int(round(max(-10, min(10, rate)))), int(round(max(-10, min(10, pitch))))
    if str(voice or "").startswith(KOKORO):
        return _say_kokoro(text, voice, path, rate, pitch)

    def speak():
        import win32com.client
        tokens = _tokens()
        token = tokens.get(voice) or tokens.get(re.sub(r"\s+Desktop$", "", str(voice or "")))
        if token is None:
            if not tokens:
                raise JobError("Windows has no voice installed (Settings > Time & language > Speech)")
            raise JobError(f"the voice {voice} is not installed any more")
        speaker = win32com.client.Dispatch("SAPI.SpVoice")
        stream = win32com.client.Dispatch("SAPI.SpFileStream")
        shape = win32com.client.Dispatch("SAPI.SpAudioFormat")
        shape.Type = 34                                 # 44.1 kHz, 16 bits, one channel
        stream.Format = shape
        stream.Open(path, 3, False)
        try:
            speaker.AudioOutputStream = stream
            speaker.Voice = token
            speaker.Rate = rate
            if pitch:
                safe = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                speaker.Speak(f'<pitch absmiddle="{pitch}">{safe}</pitch>', 8)        # 8: the text is XML
            else:
                speaker.Speak(text, 16)                                                # 16: it is not
        finally:
            stream.Close()

    with _lock:
        _in_com(speak)
    if not os.path.isfile(path) or os.path.getsize(path) < 1000:
        raise JobError("the voice said nothing")
    return path


def read(path):
    """A WAV file as floats between -1 and 1 (one channel) and its rate."""
    with wave.open(path, "rb") as file:
        rate, channels, width, count = file.getframerate(), file.getnchannels(), file.getsampwidth(), file.getnframes()
        raw = file.readframes(count)
    if width != 2:
        raise JobError("this sound file is not 16-bit")
    data = np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    return data, rate


def write(path, data, rate=RATE):
    with wave.open(path, "wb") as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(rate)
        file.writeframes((np.clip(data, -1, 1) * 32767).astype(np.int16).tobytes())


def _loud(data, rate, window=0.02):
    """How loud each stretch of `window` seconds is (RMS)."""
    size = max(1, int(rate * window))
    count = len(data) // size
    if count == 0:
        return np.zeros(0, np.float32)
    return np.sqrt((data[:count * size].reshape(count, size) ** 2).mean(axis=1))


def neaten(data, rate):
    """Silence off both ends, a little quiet back on, and the standard loudness. Returns the new samples."""
    if rate != RATE:
        positions = np.arange(0, len(data), rate / RATE)
        data = np.interp(positions, np.arange(len(data)), data).astype(np.float32)
        rate = RATE
    data = data[:int(LONGEST * rate)]
    loud = _loud(data, rate)
    if not len(loud) or loud.max() < 0.003:
        raise JobError("this sound is silent")
    heard = np.nonzero(loud > max(0.008, 0.06 * loud.max()))[0]
    size = int(rate * 0.02)
    start, end = max(0, heard[0] * size - int(0.03 * rate)), min(len(data), (heard[-1] + 1) * size + int(0.08 * rate))
    data = data[start:end].copy()
    spoken = _loud(data, rate)
    level = float(np.sqrt((spoken[spoken > 0.25 * spoken.max()] ** 2).mean()))
    gain = min(LOUDNESS / max(level, 1e-5), PEAK / max(float(np.abs(data).max()), 1e-5))
    data *= gain
    fade = int(0.012 * rate)
    if len(data) > 2 * fade:
        data[:fade] *= np.linspace(0, 1, fade, dtype=np.float32)
        data[-fade:] *= np.linspace(1, 0, fade, dtype=np.float32)
    quiet = np.zeros(int(QUIET * rate), np.float32)
    return np.concatenate([quiet, data, quiet])


def tidy(source, target):
    """Any sound file -> the tidy WAV. Returns its length in seconds."""
    from . import export
    plain = target + ".plain.wav"
    try:
        done = subprocess.run([export.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-i", source, "-vn", "-ac", "1",
                               "-ar", str(RATE), "-c:a", "pcm_s16le", "-t", str(LONGEST), plain],
                              capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if done.returncode != 0 or not os.path.isfile(plain):
            raise JobError("this sound file cannot be read: " + done.stderr.decode("utf8", "replace")[-200:])
        data, rate = read(plain)
        data = neaten(data, rate)
        write(target, data)
        return len(data) / RATE
    finally:
        try:
            os.remove(plain)
        except OSError:
            pass


def plain(source, target, longest=900.0):
    """A sound of the scene (music, an effect) is kept as it is: only its format is made ours.
    Returns its length in seconds."""
    from . import export
    done = subprocess.run([export.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-i", source, "-vn", "-ac", "2", "-ar", "48000",
                           "-c:a", "pcm_s16le", "-t", str(longest), target], capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if done.returncode != 0 or not os.path.isfile(target):
        raise JobError("this sound file cannot be read: " + done.stderr.decode("utf8", "replace")[-200:])
    with wave.open(target, "rb") as file:
        return file.getnframes() / file.getframerate()


def envelope(data, rate, fps):
    """How loud the speech is at each video frame, 0..1: what the mouth and the head follow."""
    step = rate / fps
    count = max(1, int(np.ceil(len(data) / step)))
    span = int(step * 1.5)
    loud = np.zeros(count, np.float32)
    for i in range(count):
        middle = int((i + 0.5) * step)
        piece = data[max(0, middle - span // 2):middle + span // 2]
        if len(piece):
            loud[i] = np.sqrt((piece ** 2).mean())
    top = np.percentile(loud, 95) if loud.max() > 0 else 0
    if top <= 1e-5:
        return loud
    loud = np.clip(loud / top, 0, 1)
    loud[loud < 0.08] = 0
    return loud


def brightness(data, rate, fps):
    """Where the weight of the sound is at each video frame, 0 (dark: oo, oh) .. 1 (bright: ee, s).
    A rough guide to the shape of the mouth when nothing better is at hand."""
    step = rate / fps
    count = max(1, int(np.ceil(len(data) / step)))
    size = 1024
    window = np.hanning(size).astype(np.float32)
    tones = np.fft.rfftfreq(size, 1 / rate)
    band = (tones > 200) & (tones < 5000)
    bright = np.full(count, 0.5, np.float32)
    for i in range(count):
        middle = int((i + 0.5) * step)
        piece = data[max(0, middle - size // 2):middle + size // 2]
        if len(piece) < size:
            continue
        power = np.abs(np.fft.rfft(piece * window))[band] ** 2
        if power.sum() > 1e-6:
            centre = float((tones[band] * power).sum() / power.sum())
            bright[i] = np.clip((centre - 700) / 1500, 0, 1)
    return bright


def next_number(folder, line):
    highest = 0
    for name in os.listdir(folder):
        match = re.match(rf"^speech-{re.escape(line)}-(\d+)\.", name)
        if match:
            highest = max(highest, int(match.group(1)))
    return highest + 1

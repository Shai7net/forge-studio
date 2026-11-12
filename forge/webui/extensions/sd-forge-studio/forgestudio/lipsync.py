"""Lip sync for a clip of the edit, three ways, each giving the clip a new take:

- to a sound already in the edit (a narration, a recording): MuseTalk draws the mouth over the clip for that sound
  (Video Studio's `lipsync`: the whole clip, silence before and after the words so the mouth rests, its last frame
  held past its end when the words are longer);
- to written words: a voice of Video Studio (Chatterbox, with niqqud for Hebrew) says them, then the same;
- to a face video (someone filmed saying it): LivePortrait (the sd-webui-live-portrait extension, through its own
  API function, as Simulation 360 does) moves the character's face, head and lips like the face in the video, and
  the video's own sound comes with it.

The clip's used part of its file is cut out first under a plain name (MuseTalk's and LivePortrait's tools trip over
spaces and Hebrew in paths). Every run waits its turn for the graphics card (Forge's queue, Forge's models put aside).
"""

import asyncio
import json
import os
import shutil
import subprocess
import uuid

from . import frames, library, media, projects
from .sound import _Task, _on_card, _vs_sound

APP = None   # Forge's FastAPI app, set by api.register: LivePortrait's route is found among its routes
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _live_portrait():
    """The function behind /live-portrait/human, or None without the extension."""
    for route in getattr(APP, "routes", []) or []:
        if getattr(route, "path", None) == "/live-portrait/human":
            return route.endpoint
    return None


def available():
    out = {"lips": False, "face": _live_portrait() is not None, "voices": [], "languages": []}
    try:
        vs = _vs_sound()
        have = vs.status()
        out.update(lips=bool(have.get("lips")), voices=vs.voice_choices()[:-2] + ([vs.PIPER] if have.get("piper") else []),
                   languages=list(vs.LANGUAGES), voice=bool(have.get("voices") or have.get("piper")))
    except Exception as e:
        out["message"] = f"Video Studio's sound is not available: {e}"
    return out


def _ff(args, what):
    r = subprocess.run([library.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y"] + args, capture_output=True,
                       creationflags=NO_WINDOW)
    if r.returncode != 0:
        lines = (r.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        raise RuntimeError(f"{what}: {lines[-1] if lines else 'ffmpeg failed'}")


def _part(path, start, seconds, out, video=True):
    """`seconds` of a file from `start` under a plain name: the picture alone (H.264) or the sound alone (WAV). A
    still picture becomes a video of it for `seconds` (25 frames a second), so a portrait can be made to talk."""
    if video and (media.probe(path).get("kind") or library.kind_of(path)) == "image":
        _ff(["-loop", "1", "-i", path, "-t", f"{seconds:.3f}", "-r", "25", "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
             "-c:v", "libx264", "-crf", "12", "-preset", "fast", "-pix_fmt", "yuv420p", out], "could not read the picture")
    elif video:
        _ff(["-ss", f"{start:.3f}", "-i", path, "-t", f"{seconds:.3f}", "-an", "-c:v", "libx264", "-crf", "12",
             "-preset", "fast", "-pix_fmt", "yuv420p", out], "could not cut the clip")
    else:
        _ff(["-ss", f"{start:.3f}", "-i", path, "-t", f"{seconds:.3f}", "-vn", "-ac", "1", "-ar", "44100", out],
            "could not cut the sound")
    return out


def lipsync(name, web, start, seconds, mode, options=None):
    """`web`'s file from `start` for `seconds` (the clip's used part) made to say something: mode 'sound' (options:
    sound, sound_in, sound_seconds, offset = where the words begin in the clip), 'text' (text, language, voice,
    expressive) or 'face' (face = a face video of the project, region = 'all' | 'lips')."""
    if mode not in ("sound", "text", "face", "prepared"):
        raise ValueError("unknown lip sync mode")
    path = projects.media_path(name, web)
    if (media.probe(path).get("kind") or library.kind_of(path)) not in ("video", "image"):
        raise ValueError("lip sync needs a video or a picture")
    if mode == 'prepared':
        from .lipspeech import number
        opt = options or {}
        total = number(opt.get('clip_seconds', seconds), 1 / 120, 240, 'Shot duration')
        offset = number(opt.get('offset', 0), 0, total, 'Speech start')
        speech_path = projects.media_path(name, opt.get('speech'))
        if library.kind_of(speech_path) != 'audio':
            raise ValueError('Choose prepared speech audio')
        said = media.probe(speech_path).get('duration') or 0
        if said <= 0 or offset + said > total + 0.01:
            raise ValueError('The prepared speech must fit completely inside the shot')
        options = dict(opt, clip_seconds=total)
    return frames._task("lipsync", _lipsync, name, web, float(start), float(seconds), mode, dict(options or {}))


def _lipsync(task, name, web, start, seconds, mode, opt):
    work = os.path.join(projects._folder(name), "sound", "lips-" + uuid.uuid4().hex[:6])
    os.makedirs(work, exist_ok=True)
    folder = os.path.join(projects._folder(name), "media")
    os.makedirs(folder, exist_ok=True)
    stem = os.path.splitext(os.path.basename(web))[0]
    out = os.path.join(folder, f"{stem}-lips-{uuid.uuid4().hex[:6]}.mp4")
    task["text"] = "cutting the clip"
    original = projects.media_path(name, web)
    clip = _part(original, start, seconds, os.path.join(work, "clip.mp4"))
    speech_web = None
    duration_out = None
    try:
        if mode == "face":
            picture = None
            if (media.probe(original).get("kind") or library.kind_of(original)) == "image":
                # a still picture goes to LivePortrait as a picture (its portrait mode), under a plain name
                picture = os.path.join(work, "portrait" + (os.path.splitext(original)[1].lower() or ".png"))
                shutil.copyfile(original, picture)
            _face(task, name, clip, opt, work, out, picture)
        else:
            vs = _vs_sound()
            t = _Task(task, work)
            if mode == 'prepared':
                wav = projects.media_path(name, opt['speech'])
                speech_web = opt['speech']
                offset = float(opt.get('offset', 0))
                fps = vs.video_info(clip)[2]
                speed = float(opt.get('source_speed', 1))
                if not 0.1 <= speed <= 8:
                    raise ValueError('invalid source speed')
                if speed != 1:
                    retimed = os.path.join(work, 'retimed.mp4')
                    _ff(['-i', clip, '-vf', f'setpts=(PTS-STARTPTS)/{speed},fps={fps:g}', '-an', '-c:v', 'libx264', '-crf', '12', retimed], 'could not retime the source')
                    clip = retimed
                whole = os.path.join(work, 'whole-shot.mp4')
                vs._segment(clip, 0, float(opt['clip_seconds']), fps, whole)
                task['text'] = 'Animating lips for the approved speech in the selected range'
                _on_card(lambda: vs.lipsync(t, whole, wav, offset, out, span=(0.05, 1)))
            elif mode == "sound":
                wav = _part(projects.media_path(name, opt["sound"]), float(opt.get("sound_in") or 0),
                            float(opt.get("sound_seconds") or 0) or 600.0, os.path.join(work, "said.wav"), video=False)
                offset = max(0.0, float(opt.get("offset") or 0))
                task["text"] = "waiting for the graphics card"
                _on_card(lambda: vs.lipsync(t, clip, wav, offset, out, span=(0.05, 1.0)))
            else:
                text = (opt.get("text") or "").strip()
                if not text:
                    raise ValueError("write what is said")
                choices = vs.voice_choices()
                voice = opt.get("voice") if opt.get("voice") in choices else choices[0]
                made = os.path.join(projects._folder(name), "sound", "made")
                os.makedirs(made, exist_ok=True)
                wav = os.path.join(made, f"voice-{uuid.uuid4().hex[:10]}.wav")
                task["text"] = "waiting for the graphics card"

                def run():
                    vs.make_speech(t, text, opt.get("language") or "Hebrew", voice, wav,
                                   exaggeration=float(opt.get("expressive") or 0.5), span=(0.0, 0.3))
                    vs.lipsync(t, clip, wav, 0.0, out, span=(0.3, 1.0))
                _on_card(run)
                speech_web = projects.add_media(name, wav)
            # A self-contained preview with the words, while the timeline continues to use its independent
            # sound track. Applying the result mutes this embedded copy to avoid doubled speech.
            voiced = os.path.join(work, "preview.mp4")
            duration_out = media.probe(out).get("duration") or seconds
            offset = max(0.0, float(opt.get("offset") or 0)) if mode in ("sound", "prepared") else 0.0
            delay = int(round(offset * 1000))
            _ff(["-i", out, "-i", wav, "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
                 "-af", f"adelay={delay}|{delay},apad,atrim=duration={duration_out:.9f}",
                 "-c:a", "aac", "-b:a", "192k", "-t", f"{duration_out:.9f}", "-movflags", "+faststart", voiced],
                "could not add the preview's speech")
            os.replace(voiced, out)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    new = projects.add_media(name, out)
    seconds_out = duration_out or media.probe(out).get("duration") or seconds
    # Keep enough local provenance to reopen the form and repair a result without guessing its source or voice.
    projects._write(out + ".lips.json", json.dumps({"source": web, "start": start, "seconds": seconds, "mode": mode,
                                                   "options": opt, "speech": speech_web}, ensure_ascii=False, indent=1))
    return {"web": new, "speech": speech_web, "speech_offset": float(opt.get('offset', 0)), "seconds": seconds_out, "progress": 1.0,
            "text": f"{seconds_out:.1f} s talking"}


def _face(task, name, clip, opt, work, out, picture=None):
    """LivePortrait: the clip's face moved like the face video's, with the video's own sound. `picture`: the clip is
    this still picture (then it is the source as it is, and the result is as long as the face video).

    The face video has to be a close-up (a selfie, the face filling much of the frame): from a full-body shot whose
    face was ~100 pixels wide the mouth hardly moved, whatever the settings (multiplier 2, lip retargeting, absolute
    motion, all tried 2026-11-08); a close-up drove the same picture's mouth fully open and shut."""
    endpoint = _live_portrait()
    if endpoint is None:
        raise RuntimeError("LivePortrait (sd-webui-live-portrait) is not installed in this Forge")
    model = getattr(endpoint, "__annotations__", {}).get("payload")
    if model is None:
        raise RuntimeError("this version of LivePortrait cannot be driven from here")
    driving = projects.media_path(name, opt["face"])
    vs = _vs_sound()
    said = media.probe(driving).get("duration") or 0
    # LivePortrait pairs the clip's frames with the face video's one to one and writes them at its own fixed rate
    # (force_output_fps, 25 in this Forge's copy): a 16 fps face came out 1.5 times too fast and the speech cut short
    # (seen 2026-11-08). So both go in at the rate it writes, the face video's frames repeated as needed.
    try:
        rate = int(endpoint.__globals__["ArgumentConfig"]().force_output_fps or 0)
    except Exception:
        rate = 25
    drive = os.path.join(work, "face.mp4")
    _ff(["-i", driving] + (["-r", str(rate)] if rate else []) + ["-c:v", "libx264", "-crf", "12", "-preset", "fast",
        "-pix_fmt", "yuv420p", "-c:a", "aac", drive], "could not read the face video")
    w, h, fps = vs.video_info(clip)
    face_fps = vs.video_info(drive)[2]
    # A video clip as long as the face video, holding its last frame past the source's end.
    source = picture
    if not source:
        source = os.path.join(work, "source.mp4")
        vs._segment(clip, 0.0, max(said, vs.duration(clip)), face_fps, source)
    # only the lips unless asked: moving the whole face (or only its expression) left the edge of the pasted face
    # visible on a still portrait, a square on the forehead (seen 2026-11-08); the lips alone stayed clean
    region = "all" if opt.get("region") == "all" else "lip"
    payload = model(source=source, driving=drive, output_dir=os.path.join(work, "lp"), save_output=True, send_output=False,
                    use_model_cache=True, flag_stitching=True, flag_relative_motion=True, flag_pasteback=True,
                    flag_do_crop=True, flag_crop_driving_video=True, driving_option="expression-friendly",
                    driving_multiplier=1.0, animation_region=region, audio_priority="driving",
                    source_max_dim=2048, source_division=2)

    def run():
        try:
            from ldm_patched.modules import model_management
            model_management.unload_all_models()
            model_management.soft_empty_cache()
        except Exception:
            pass
        task["text"] = "LivePortrait is moving the face"
        try:
            asyncio.run(endpoint(payload))
        except Exception as e:
            said_err = str(getattr(e, "detail", "") or e)
            if "face" in said_err.lower() or isinstance(e, (IndexError, TypeError)):
                raise RuntimeError("LivePortrait found no face: the character's face and the face in the video must look "
                                   "towards the camera and not be too small")
            raise RuntimeError(f"LivePortrait failed: {type(e).__name__}: {said_err[:300]}")
        finally:
            try:
                endpoint.__globals__["clear_model_cache"]()   # its models leave the card again
            except Exception:
                pass
    _on_card(run)
    made = [os.path.join(base, n) for base, _dirs, names in os.walk(os.path.join(work, "lp")) for n in names
            if n.endswith(".mp4") and not n.endswith("_concat.mp4")]
    if not made:
        raise RuntimeError("LivePortrait made no video")
    made = max(made, key=os.path.getmtime)
    # the picture at the clip's size and frame rate, the face video's sound with it
    has_sound = media.probe(drive).get("audio")
    args = ["-i", made] + (["-i", drive] if has_sound else []) + [
        "-map", "0:v:0"] + (["-map", "1:a:0", "-c:a", "aac", "-b:a", "192k", "-shortest"] if has_sound else ["-an"]) + [
        "-vf", f"fps={fps:g},scale={w}:{h},format=yuv420p", "-c:v", "libx264", "-crf", "15", "-preset", "medium",
        "-movflags", "+faststart", out]
    _ff(args, "could not write the talking clip")

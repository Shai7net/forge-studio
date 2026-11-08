"""A character that talks.

An object's picture becomes a short video of the same character saying a line:

  1. the line's sound (speech.py): a Windows voice, or a sound file the user brought;
  2. a performance written for that sound: the head moves a little and nods on the stressed words, the brows
     follow, the eyes blink, and the first and the last frame are the still picture itself, so the video can
     take the picture's place and give it back without a jump;
  3. LivePortrait (the sd-webui-live-portrait extension, through its own API function) plays the performance
     on the character's face;
  4. a lip syncer shapes the mouth to the words: FaceFusion's (Wav2Lip or EDTalk, run from FaceFusion's own
     folder and environment), or MuseTalk 1.5 (models\sim360-musetalk: its program, its own Python and its
     weights; musetalk_say.py is what is run there). Without either, or when no face is found, the mouth
     opens and closes with the loudness of the voice instead, drawn by LivePortrait;
  5. the character is lifted off its background again. The picture goes through steps 3 and 4 standing on
     one flat colour that it does not contain; what did not move is taken from the still picture as it was,
     and only the part that moved (the head) is cut from the video, by that colour.

    speech-<line>-0003.wav        the sound
    speech-<line>-0003.mp4        the character saying it: colours on the left half, the cut on the right
"""

import asyncio
import os
import pickle
import re
import shutil
import subprocess
import time

import numpy as np
from PIL import Image

from . import export, objects, runner, speech, store
from .runner import JobError

FPS = 25                    # Wav2Lip was taught at 25 frames a second
LONGEST_SIDE = 1280         # the talking video is made no larger than this
CLOSE_SIDE = 512            # a close-up of a head is made this large: the size the face models work at
SOFT_ENDS = 7               # frames over which a talking video leaves the still picture and comes back to it
LIPS = ("wav2lip_gan_96", "wav2lip_96", "edtalk_256")
MUSETALK = "musetalk_v15"
# what MuseTalk's folder must hold to shape a mouth (its training needs more, and is not done here)
MUSETALK_NEEDS = (("env", "Scripts", "python.exe"), ("app", "musetalk", "utils", "preprocessing.py"),
                  ("app", "models", "musetalkV15", "unet.pth"), ("app", "models", "musetalkV15", "musetalk.json"),
                  ("app", "models", "sd-vae", "config.json"), ("app", "models", "sd-vae", "diffusion_pytorch_model.bin"),
                  ("app", "models", "whisper", "config.json"), ("app", "models", "whisper", "pytorch_model.bin"),
                  ("app", "models", "dwpose", "dw-ll_ucoco_384.pth"),
                  ("app", "models", "face-parse-bisent", "79999_iter.pth"), ("app", "models", "face-parse-bisent", "resnet18-5c106cde.pth"))
MUSETALK_ROOM = 7.0         # gigabytes Windows must still be able to promise before MuseTalk is started
BY_LOUDNESS = "loudness"
KEYS = ((0, 255, 0), (255, 0, 255), (0, 0, 255))


# --- the performance ---------------------------------------------------------------------------------------------

def _rotation(pitch, yaw, roll):
    """Degrees -> the head's rotation, laid out the way LivePortrait's own get_rotation_matrix does it."""
    x, y, z = np.radians([pitch, yaw, roll])
    rx = np.array([[1, 0, 0], [0, np.cos(x), -np.sin(x)], [0, np.sin(x), np.cos(x)]])
    ry = np.array([[np.cos(y), 0, np.sin(y)], [0, 1, 0], [-np.sin(y), 0, np.cos(y)]])
    rz = np.array([[np.cos(z), -np.sin(z), 0], [np.sin(z), np.cos(z), 0], [0, 0, 1]])
    return (rz @ ry @ rx).T.astype(np.float32)


def _face(blink=0.0, brow=0.0, smile=0.0, mouth=0.0, wide=0.0):
    """One frame's expression as LivePortrait keeps it: 21 points, each moved a little from rest.
    The amounts are those of LivePortrait's own sliders (wink, eyebrow, smile, lip opening, grin).
    blink 0..1, brow -1 (frown) .. 1 (raised), smile 0..1, mouth 0..1 (open), wide 0..1."""
    e = np.zeros((21, 3), np.float32)
    w = 34.0 * blink
    e[11, 1] += w * 0.001; e[13, 1] += w * -0.0003              # one eye
    e[15, 1] += w * 0.001; e[16, 1] += w * -0.0003              # the other
    b = 14.0 * brow
    if b > 0:
        e[1, 1] += b * 0.001; e[2, 1] += b * -0.001
    else:
        e[1, 0] += b * -0.001; e[2, 0] += b * 0.001; e[1, 1] += b * 0.0003; e[2, 1] += b * -0.0003
    s = 0.75 * smile
    e[20, 1] += s * -0.01; e[14, 1] += s * -0.02; e[17, 1] += s * 0.0065; e[17, 2] += s * 0.003
    e[13, 1] += s * -0.00275; e[16, 1] += s * -0.00275; e[3, 1] += s * -0.0035; e[7, 1] += s * -0.0035
    o = 44.0 * mouth
    e[19, 1] += o * 0.001; e[19, 2] += o * 0.0001; e[17, 1] += o * -0.0001
    g = 9.0 * wide
    e[20, 2] += g * -0.001; e[20, 1] += g * -0.001; e[14, 1] += g * -0.001
    return e


def _smooth(values, frames):
    """A running average over about `frames` frames, the ends held."""
    size = max(1, int(round(frames)))
    if size <= 1 or len(values) < 2:
        return np.asarray(values, np.float32)
    padded = np.concatenate([np.full(size, values[0]), values, np.full(size, values[-1])])
    return np.convolve(padded, np.ones(size) / size, "same")[size:-size].astype(np.float32)


def acted(beats, count, fps):
    """An act without words, as curves over the frames. `beats`: [{t, head, turn, eyes, smile, laugh}], each
    a moment (seconds) and what the face is doing by then; between moments it glides.
    head: degrees the head hangs down (15: looking at the ground). turn: degrees it is turned aside.
    eyes: -1 wide open .. 0 as they are .. 1 shut. smile 0..1. laugh 0..1."""
    names = ("head", "turn", "eyes", "smile", "laugh")
    beats = sorted(beats or [], key=lambda b: float(b.get("t") or 0))
    curves = {name: np.zeros(count, np.float32) for name in names}
    if not beats:
        return curves
    moments = np.array([float(b.get("t") or 0) for b in beats])
    t = np.arange(count) / fps
    for name in names:
        values, known = [], 0.0
        for beat in beats:
            if beat.get(name) is not None:
                known = float(beat[name])
            values.append(known)
        index = np.clip(np.searchsorted(moments, t, side="right") - 1, 0, len(beats) - 1)
        after = np.clip(index + 1, 0, len(beats) - 1)
        span = np.maximum(moments[after] - moments[index], 1e-6)
        x = np.clip((t - moments[index]) / span, 0, 1)
        x = x * x * (3 - 2 * x)
        values = np.array(values, np.float32)
        curves[name] = (values[index] * (1 - x) + values[after] * x).astype(np.float32)
    return curves


def performance(seconds, fps=FPS, loud=None, bright=None, liveliness=0.5, mood=0.0, lips=False, seed=0, act=None):
    """What the face does, frame by frame: a LivePortrait motion template.
    loud, bright: the voice at each frame (speech.envelope, speech.brightness), or None for a face that
    only lives (looks about, blinks). liveliness 0..1. mood -1 (stern) .. 1 (smiling).
    lips: the mouth follows the loudness (when no lip syncer will do it afterwards).
    act: beats of an act without words (see acted); the video then begins and ends as the act says, not
    as the still picture."""
    count = max(2, int(round(seconds * fps)))
    t = np.arange(count, dtype=np.float32) / fps
    rng = np.random.default_rng(int(seed) & 0x7fffffff)
    k = 0.35 + 1.5 * float(np.clip(liveliness, 0, 1))
    def per_frame(values, rest):
        values = np.full(count, rest, np.float32) if values is None else np.asarray(values, np.float32)[:count]
        return np.pad(values, (0, count - len(values)), constant_values=rest)

    loud, bright = per_frame(loud, 0.0), per_frame(bright, 0.5)
    speaking = bool(loud.max() > 0)

    # still at both ends: the video begins and ends as the picture
    edge = np.clip(np.minimum(t, t[-1] - t) / 0.4, 0, 1)
    edge = edge * edge * (3 - 2 * edge)
    play = acted(act, count, fps) if act else None
    if play:
        edge = np.ones(count, np.float32)

    def wander(size):
        total = np.zeros(count, np.float32)
        for weight in (1.0, 0.55, 0.3):
            total += weight * np.sin(2 * np.pi * rng.uniform(0.09, 0.34) * t + rng.uniform(0, 2 * np.pi))
        return size * total / 1.85

    # the words: what rises above the voice's own average is a stressed syllable
    body = _smooth(loud, fps * 0.16)
    stress = np.clip(body - _smooth(body, fps * 1.1), 0, None)
    stress = stress / max(float(stress.max()), 1e-5) if speaking else stress
    stress = _smooth(stress, fps * 0.12)
    phrase = _smooth((body > 0.12).astype(np.float32), fps * 0.5)        # 1 while a phrase is being said

    pitch = wander(1.2 * k) + 2.6 * k * stress + 0.6 * k * phrase       # a nod on each stress
    yaw = wander(2.0 * k) + 0.7 * k * np.sin(2 * np.pi * 0.21 * t + rng.uniform(0, 6.28)) * phrase
    roll = wander(0.9 * k) - 0.8 * k * stress * np.sign(rng.uniform(-1, 1))
    shift_x, shift_y = wander(0.0035 * k), wander(0.002 * k) + 0.003 * k * stress
    brow = np.clip(0.75 * k * _smooth(stress, fps * 0.25) + 0.18 * max(mood, 0) + 0.7 * min(mood, 0), -1, 1)
    smile = np.full(count, max(mood, 0) * 0.8, np.float32) * (0.75 + 0.25 * np.sin(2 * np.pi * 0.13 * t))

    blink = np.zeros(count, np.float32)
    shape = np.interp(np.linspace(0, 1, max(4, int(round(fps * 0.2)))), [0, 0.3, 0.55, 1], [0.3, 1, 0.95, 0.25])
    at = rng.uniform(0.5, 1.3)
    while at < seconds - 0.45:
        start = int(at * fps)
        end = min(count, start + len(shape))
        blink[start:end] = np.maximum(blink[start:end], shape[:end - start])
        at += rng.uniform(2.1, 4.4) if speaking else rng.uniform(2.6, 5.2)

    mouth = np.zeros(count, np.float32)
    wide = np.zeros(count, np.float32)
    if lips and speaking:
        opened = np.power(np.clip(loud, 0, 1), 0.7)
        for i in range(count):                                      # opens at once, closes a little slower
            mouth[i] = max(opened[i], mouth[i - 1] * 0.55 if i else 0)
        mouth = _smooth(mouth, 2)
        wide = _smooth(np.clip(bright - 0.45, 0, 1) * 1.6 * (mouth > 0.1), 3)

    if play:
        # laughing: the mouth goes ha-ha-ha, the head shakes with it and tips back, the eyes narrow
        ha = play["laugh"] * (0.5 + 0.5 * np.sin(2 * np.pi * 4.6 * t + rng.uniform(0, 6.28))) * (0.6 + 0.4 * np.sin(2 * np.pi * 0.7 * t + rng.uniform(0, 6.28)) ** 2)
        mouth = np.maximum(mouth, 0.85 * ha)
        smile = np.maximum(smile, np.maximum(play["smile"], play["laugh"]))
        wide = np.maximum(wide, 0.6 * play["laugh"])
        pitch = pitch * (1 - 0.6 * np.clip(play["head"] / 12, 0, 1)) + play["head"] - 3.0 * play["laugh"] + 1.6 * ha
        yaw = yaw + play["turn"]
        roll = roll + 2.2 * play["laugh"] * np.sin(2 * np.pi * 1.1 * t + rng.uniform(0, 6.28))
        brow = np.clip(brow + 0.9 * np.clip(-play["eyes"], 0, 1) - 0.3 * play["laugh"], -1, 1)
        stare = np.clip(-play["eyes"], 0, 1)
        blink = np.clip(blink * (1 - stare) - 0.7 * stare + np.clip(play["eyes"], 0, 1) + 0.38 * play["laugh"] * (1 - blink), -0.7, 1)

    motion = []
    for i in range(count):
        g = float(edge[i])
        motion.append({
            "scale": np.ones((1, 1), np.float32),
            "R": _rotation(pitch[i] * g, yaw[i] * g, roll[i] * g)[None],
            "exp": _face(blink[i] * (g > 0.5 or blink[i] < 0), brow[i] * g, smile[i] * g, mouth[i] * g, wide[i] * g)[None],
            "t": np.array([[shift_x[i] * g, shift_y[i] * g, 0]], np.float32),
        })
    return {"n_frames": count, "output_fps": int(fps), "motion": motion,
            "c_eyes_lst": [np.array([[0.36, 0.36]], np.float32)] * count, "c_lip_lst": [np.array([[0.0]], np.float32)] * count}


# --- off the background and back on ------------------------------------------------------------------------------

def _lean(rgb, key):
    """How far each pixel leans to the key colour: about 255 on the key colour itself, 0 or less on a
    colour that has nothing of it."""
    on = [c for c in range(3) if key[c] > 127]
    off = [c for c in range(3) if key[c] <= 127]
    return rgb[..., on].min(axis=-1).astype(np.float32) - rgb[..., off].max(axis=-1).astype(np.float32)


def key_colour(rgb, alpha):
    """The flat colour the character will stand on: the one it has least of."""
    solid = rgb[alpha > 160]
    if not len(solid):
        return KEYS[0]
    solid = solid[::max(1, len(solid) // 60000)]
    return min(KEYS, key=lambda key: float((_lean(solid, key) > 24).mean()))


def on_key(rgb, alpha, key):
    a = (alpha.astype(np.float32) / 255.0)[..., None]
    return (rgb.astype(np.float32) * a + np.array(key, np.float32) * (1 - a)).round().astype(np.uint8)


def unkey(frame, key, low=34.0, high=188.0):
    """A frame of the character on the key colour -> its colours (the key colour taken out of the edges)
    and its cut."""
    pixels = frame.astype(np.float32)
    a = np.clip(1 - (_lean(pixels, key) - low) / (high - low), 0, 1)
    # an edge pixel is a mix: seen = a * own + (1 - a) * key  ->  own = (seen - (1 - a) * key) / a
    own = (pixels - (1 - a)[..., None] * np.array(key, np.float32)) / np.maximum(a, 0.15)[..., None]
    over = np.clip(_lean(own, key), 0, None)                    # what still leans to the key colour: the rim
    for c in range(3):                                          # that the video's coarser colour leaves
        if key[c] > 127:
            own[..., c] -= over
    return np.clip(own, 0, 255).astype(np.uint8), (a * 255).round().astype(np.uint8)


def rimmed(frame, colours, cut, key):
    """The thin rim where the character meets the key colour, done properly. A rim pixel is a mix of the
    character's colour just inside it and the key colour; the colour just inside is known (it is spread
    outwards from the solid part), so both the pixel's colour and how much of it is character can be
    worked out, instead of guessed from how green it looks. Returns colours (float) and the cut (uint8)."""
    import cv2
    solid = cv2.erode((cut >= 236).astype(np.float32), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
    weight = cv2.GaussianBlur(solid, (0, 0), 2.0)
    inside = cv2.GaussianBlur(colours.astype(np.float32) * solid[..., None], (0, 0), 2.0) / np.maximum(weight, 1e-3)[..., None]
    rim = (cut < 236) & (weight > 0.03)
    colours = colours.astype(np.float32)
    colours[rim] = inside[rim]
    # seen = share * inside + (1 - share) * key   ->   share = (seen - key) . (inside - key) / |inside - key|^2
    backdrop = np.array(key, np.float32)
    towards = inside - backdrop
    share = ((frame.astype(np.float32) - backdrop) * towards).sum(axis=2) / np.maximum((towards * towards).sum(axis=2), 1.0)
    cut = cut.copy()
    cut[rim] = (np.clip(share[rim], 0, 1) * 255).round().astype(np.uint8)
    return colours, cut


def moved_part(changes, height):
    """Where the video differs from the still picture (the largest difference of any frame, per pixel)
    -> a soft mask, 1 where the video is used."""
    import cv2
    moved = (changes > 16).astype(np.uint8)
    moved = cv2.morphologyEx(moved, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
    if not moved.any():
        return np.zeros(changes.shape, np.float32)
    reach = max(5, int(round(height * 0.022)) * 2 + 1)
    moved = cv2.dilate(moved, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (reach, reach)))
    return np.clip(cv2.GaussianBlur(moved.astype(np.float32), (0, 0), reach * 0.35) * 1.15, 0, 1)


# --- LivePortrait ------------------------------------------------------------------------------------------------

def _live_portrait():
    """The function behind /live-portrait/human, or None when the extension is not installed."""
    for route in getattr(runner._app, "routes", []) or []:
        if getattr(route, "path", None) == "/live-portrait/human":
            return route.endpoint
    return None


def live_portrait_available():
    return _live_portrait() is not None


def _free_graphics_memory():
    try:
        from ldm_patched.modules import model_management
        model_management.unload_all_models()
        model_management.soft_empty_cache()
    except Exception as e:
        print(f"[Simulation 360] could not free graphics memory: {type(e).__name__}: {e}")


def animate(job, picture_path, template, folder):
    """LivePortrait plays `template` on the face in the picture. Returns the path of the video it made."""
    from modules.call_queue import queue_lock
    endpoint = _live_portrait()
    if endpoint is None:
        raise JobError("the LivePortrait extension (sd-webui-live-portrait) is not installed in this Forge")
    model = getattr(endpoint, "__annotations__", {}).get("payload")
    if model is None:
        raise JobError("this version of LivePortrait cannot be driven from here")
    driving = os.path.join(folder, "performance.pkl")
    with open(driving, "wb") as file:
        pickle.dump(template, file)
    out = os.path.join(folder, "liveportrait")
    payload = model(source=picture_path, driving=driving, output_dir=out, save_output=True, send_output=False,
                    use_model_cache=True, flag_stitching=True, flag_relative_motion=True, flag_pasteback=True,
                    flag_do_crop=True, driving_option="expression-friendly", driving_multiplier=1.0,
                    animation_region="all", source_max_dim=2048, source_division=2)
    with queue_lock:
        _free_graphics_memory()
        try:
            asyncio.run(endpoint(payload))
        except Exception as e:
            said = str(getattr(e, "detail", "") or e)
            if "face" in said.lower() or isinstance(e, (IndexError, TypeError)):
                raise JobError("LivePortrait found no face on this object. It needs a face that looks towards the "
                               "camera and is not too small.")
            raise JobError(f"LivePortrait failed: {type(e).__name__}: {said[:300]}")
        finally:
            try:
                endpoint.__globals__["clear_model_cache"]()         # its models leave the graphics card again
            except Exception:
                pass
    made = [os.path.join(base, name) for base, _dirs, names in os.walk(out) for name in names
            if name.endswith(".mp4") and not name.endswith("_concat.mp4")]
    if not made:
        raise JobError("LivePortrait made no video")
    return max(made, key=os.path.getmtime)


# --- FaceFusion --------------------------------------------------------------------------------------------------

def facefusion_root():
    """FaceFusion's folder (the one with facefusion.py), or None."""
    from modules import paths, shared
    home = os.path.abspath(os.path.join(paths.data_path, "..", ".."))
    wanted = [str(shared.opts.data.get("sim360_facefusion_dir") or "").strip(),
              str(shared.opts.data.get("live_portrait_facefusion_root") or "").strip(),
              os.path.join(home, "facefusion", "facefusion"), os.path.join(home, "facefusion"),
              os.path.join(os.path.dirname(paths.data_path), "facefusion")]
    for folder in wanted:
        if folder and os.path.isfile(os.path.join(folder, "facefusion.py")):
            return os.path.abspath(folder)
    return None


def _facefusion_python(root):
    for relative in (("venv", "Scripts", "python.exe"), (".venv", "Scripts", "python.exe"), ("venv", "bin", "python"),
                     ("..", ".env", "python.exe"), ("..", "env", "python.exe")):
        path = os.path.abspath(os.path.join(root, *relative))
        if os.path.isfile(path):
            return path
    return None


def musetalk_home():
    """MuseTalk's folder (models\\sim360-musetalk, or the one named in Settings) when all it needs to shape a
    mouth is there, or None."""
    from modules import paths, shared
    wanted = str(shared.opts.data.get("sim360_musetalk_dir") or "").strip() or \
        os.path.join(getattr(paths, "models_path", None) or os.path.join(paths.data_path, "models"), "sim360-musetalk")
    return os.path.abspath(wanted) if all(os.path.isfile(os.path.join(wanted, *part)) for part in MUSETALK_NEEDS) else None


def lip_models():
    """The lip syncers that can be used right now, the best first."""
    # MuseTalk first: on the same lines its mouths were clearer and more varied than Wav2Lip's, with teeth, in
    # the same time (it does smooth a stubbled chin a little). When it cannot, a FaceFusion one is tried.
    found = [MUSETALK] if musetalk_home() else []
    root = facefusion_root()
    if root and _facefusion_python(root):
        models = os.path.join(root, ".assets", "models")
        found += [name for name in LIPS if os.path.isfile(os.path.join(models, name + ".onnx"))]
    return found


def lip_syncer_name(model):
    return "MuseTalk" if model == MUSETALK else "FaceFusion"


def _musetalk(job, video, audio, target, folder):
    """MuseTalk draws the mouth in `video` again for the words in `audio`, in its own Python. False when it
    could not."""
    home = musetalk_home()
    if not home:
        return False
    try:
        from . import solid
        promise = solid.memory_free()[1]
    except Exception:
        promise = None
    if promise is not None and promise < MUSETALK_ROOM:
        print(f"[Simulation 360] MuseTalk was not started: Windows can promise only {promise:.1f} GB more memory")
        return False
    helper = os.path.join(os.path.dirname(os.path.abspath(__file__)), "musetalk_say.py")
    work = os.path.join(home, "work", f"job-{time.time_ns()}")              # (a folder named in plain letters)
    # what Forge set for itself must not reach another program: its Hugging Face folders, its Python's paths
    environment = {k: v for k, v in os.environ.items()
                   if not re.match(r"(HF_|HUGGINGFACE_|TRANSFORMERS_|DIFFUSERS_|PYTHON|TORCH_HOME|VIRTUAL_ENV)", k, re.I)}
    environment.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HOME=os.path.join(home, "hf"), TORCH_HOME=os.path.join(home, "torch"))
    command = [os.path.join(home, "env", "Scripts", "python.exe"), "-I", "-u", "-X", "utf8", helper, os.path.join(home, "app"),
               video, audio, target, "--ffmpeg", export.ffmpeg(), "--work", work]
    said = []
    try:
        process = subprocess.Popen(command, cwd=os.path.join(home, "app"), env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, encoding="utf8", errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError as e:
        print(f"[Simulation 360] MuseTalk could not be started: {e}")
        return False
    try:
        for line in process.stdout:
            said.append(line)
            seen = re.match(r"PROGRESS ([0-9.]+)", line)
            if seen:
                job.share = 0.45 + 0.4 * min(1.0, float(seen.group(1)))
            if job.status == "cancelling":
                process.kill()
                raise JobError("cancelled")
        code = process.wait(timeout=60)
    finally:
        if process.poll() is None:
            process.kill()
        shutil.rmtree(work, ignore_errors=True)
    if code != 0 or not os.path.isfile(target) or os.path.getsize(target) < 2000:
        why = next((line.strip() for line in reversed(said) if line.startswith("FAILED")), "")
        tail = " | ".join(line.strip() for line in said if line.strip() and not line.startswith("PROGRESS"))[-700:]
        print(f"[Simulation 360] MuseTalk failed ({code}) {why}: {tail}")
        return False
    return True


def lip_sync(job, video, audio, target, model, folder):
    """A lip syncer shapes the mouth in `video` to the words in `audio`: MuseTalk, or one of FaceFusion's.
    False when it could not."""
    if model == MUSETALK:
        return _musetalk(job, video, audio, target, folder)
    root = facefusion_root()
    python = _facefusion_python(root) if root else None
    if not python:
        return False
    models = os.path.join(root, ".assets", "models")
    detector = next((name for name, file in (("yolo_face", "yoloface_8n"), ("scrfd", "scrfd_2.5g"), ("retinaface", "retinaface_10g"))
                     if os.path.isfile(os.path.join(models, file + ".onnx"))), None)
    settings = os.path.join(folder, "facefusion.ini")
    open(settings, "w").close()                         # its own defaults, whatever the user's FaceFusion is set to
    command = [python, "facefusion.py", "headless-run", "--config-path", settings,
               "--temp-path", os.path.join(folder, "ff-temp"), "--jobs-path", os.path.join(folder, "ff-jobs"),
               "--processors", "lip_syncer", "--lip-syncer-model", model,
               "-s", audio, "-t", video, "-o", target,
               "--face-selector-mode", "one", "--face-detector-score", "0.3",
               "--output-video-encoder", "libx264", "--output-video-quality", "96", "--output-video-preset", "fast",
               "--execution-providers", "cuda", "cpu", "--log-level", "info"]
    if detector:
        command += ["--face-detector-model", detector]
    said = []
    try:
        process = subprocess.Popen(command, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                   encoding="utf8", errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError as e:
        print(f"[Simulation 360] FaceFusion could not be started: {e}")
        return False
    try:
        buffer = ""
        while True:
            piece = process.stdout.read(160)
            if not piece:
                break
            buffer = (buffer + piece)[-4000:]
            said.append(piece)
            seen = re.findall(r"(\d+)%\|", buffer)
            if seen:
                job.share = 0.45 + 0.4 * int(seen[-1]) / 100
            if job.status == "cancelling":
                process.kill()
                raise JobError("cancelled")
        code = process.wait(timeout=60)
    finally:
        if process.poll() is None:
            process.kill()
    text = "".join(said)
    if code != 0 or not os.path.isfile(target) or os.path.getsize(target) < 2000:
        tail = " | ".join(line.strip() for line in re.split(r"[\r\n]+", text) if line.strip())[-500:]
        print(f"[Simulation 360] FaceFusion's lip syncer failed ({code}): {tail}")
        return False
    return True


def _frames(path, width, height, matrix="bt601"):
    """The frames of a video as RGB arrays, read with the colour arithmetic that is named.
    A video's own label cannot be trusted here: FaceFusion labels its videos BT.709 and writes them with
    BT.601's numbers, and a reader that believes the label turns pure green into (18, 255, 7)."""
    command = [export.ffmpeg(), "-hide_banner", "-loglevel", "error", "-i", path, "-an",
               "-vf", f"scale={width}:{height}:flags=bicubic+accurate_rnd+full_chroma_int:in_color_matrix={matrix}:in_range=tv,format=rgb24",
               "-f", "rawvideo", "-"]
    reader = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    size = width * height * 3
    try:
        while True:
            data = reader.stdout.read(size)
            if len(data) < size:
                break
            yield np.frombuffer(data, np.uint8).reshape(height, width, 3)
    finally:
        reader.stdout.close()
        if reader.poll() is None:
            reader.kill()
        reader.wait()


def _matrix_of(path, like):
    """Which colour arithmetic a video was really written with: the one that gives back the picture that
    went in (`like`: the character on its flat colour)."""
    height, width = like.shape[:2]
    best, least = "bt601", None
    for matrix in ("bt601", "bt709"):
        frame = next(_frames(path, width, height, matrix), None)
        if frame is None:
            continue
        error = float(np.abs(frame.astype(np.int16) - like.astype(np.int16)).mean())
        if least is None or error < least:
            best, least = matrix, error
    return best


def _same_mouths(before, after, width, height, matrix):
    """Whether the lip syncer left the video as it was (it found no face to work on)."""
    import cv2
    most = 0.0
    for index, (a, b) in enumerate(zip(_frames(before, width, height), _frames(after, width, height, matrix))):
        if index % 6 != 3:
            continue
        difference = cv2.GaussianBlur(np.abs(a.astype(np.int16) - b).max(axis=2).astype(np.float32), (0, 0), 3)
        most = max(most, float(difference.max()))
        if most >= 9.0:
            return False
    return True


# --- putting it together -----------------------------------------------------------------------------------------

def close_up(crop):
    """A part of a picture asked for as [x0, y0, x1, y1] (shares of it, a little past its edges at most), or None."""
    try:
        x0, y0, x1, y1 = (float(np.clip(float(v), -1, 2)) for v in crop)
    except (TypeError, ValueError):
        return None
    return [x0, y0, x1, y1] if x1 - x0 > 0.02 and y1 - y0 > 0.02 else None


def _still(project, world, name, side=LONGEST_SIDE, crop=None):
    """An object's still picture as colours and cut, no larger than the talking video may be, even-sided.
    With `crop`, that part of it only, enlarged to `side`: a face is given the pixels its lips need."""
    try:
        picture = Image.open(store.resolve(project, f"worlds/{world}/{name}")).convert("RGBA")
    except Exception:
        raise JobError("the object's picture cannot be read")
    if crop:
        box = [int(round(crop[0] * picture.width)), int(round(crop[1] * picture.height)),
               int(round(crop[2] * picture.width)), int(round(crop[3] * picture.height))]
        picture = picture.crop(box)                 # (what is past the picture's edge comes out see-through)
    k = side / max(picture.size) if crop else min(1.0, side / max(picture.size))
    width, height = max(2, int(picture.width * k) // 2 * 2), max(2, int(picture.height * k) // 2 * 2)
    if (width, height) != picture.size:
        picture = picture.resize((width, height), Image.LANCZOS)
    pixels = np.asarray(picture)
    return np.ascontiguousarray(pixels[..., :3]), np.ascontiguousarray(pixels[..., 3])


def _lift(job, video, rgb, alpha, key, target, fps, matrix="bt601"):
    """The video of the character on the key colour -> the object's own kind of video (colours | cut).
    What did not move comes from the still picture itself."""
    import cv2
    height, width = alpha.shape
    backdrop = on_key(rgb, alpha, key).astype(np.int16)
    changes = np.zeros((height, width), np.float32)
    count = 0
    for frame in _frames(video, width, height, matrix):
        difference = np.abs(frame.astype(np.int16) - backdrop).max(axis=2).astype(np.float32)
        changes = np.maximum(changes, cv2.blur(difference, (3, 3)))         # single speckles of the encoder do not count
        count += 1
    if count < 2:
        raise JobError("the talking video came out empty")
    whole_part = moved_part(changes, height)
    still_rgb, still_alpha = rgb.astype(np.float32), alpha.astype(np.float32)
    rim_shape = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))

    command = [export.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
               "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width * 2}x{height}", "-r", f"{fps:g}", "-i", "-", "-an",
               "-vf", "format=yuv420p", "-c:v", "libx264", "-crf", "15", "-preset", "medium", "-g", "5",
               "-movflags", "+faststart", target]
    encoder = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    written = 0
    try:
        for index, frame in enumerate(_frames(video, width, height, matrix)):
            colours, cut = rimmed(frame, *unkey(frame, key), key)
            # (A lip syncer draws the mouth its own way even when it is shut. Over the first and the last
            # frames the video is let in and out gradually, so the face does not change skin in one frame
            # when the character begins to speak or falls silent.)
            ease = min(1.0, (index + 0.5) / SOFT_ENDS, (count - index - 0.5) / SOFT_ENDS)
            part, keep = whole_part * ease, 1 - whole_part * ease
            out_rgb = still_rgb * keep[..., None] + colours * part[..., None]
            out_alpha = still_alpha * keep + cut.astype(np.float32) * part
            packed = np.concatenate([out_rgb.round().astype(np.uint8),
                                     np.repeat(out_alpha.round().astype(np.uint8)[..., None], 3, axis=2)], axis=1)
            encoder.stdin.write(np.ascontiguousarray(packed).tobytes())
            written += 1
            job.share = 0.86 + 0.13 * written / count
            if job.status == "cancelling":
                raise JobError("cancelled")
    finally:
        try:
            encoder.stdin.close()
        except OSError:
            pass
    said = encoder.stderr.read().decode("utf8", "replace")
    if encoder.wait() != 0 or written < 2:
        raise JobError("the talking video could not be written: " + said[-300:])
    return written, float(whole_part.mean())


@runner.handler("speech")
def make_speech(job):
    """A line's words -> its sound, in one of Windows' voices."""
    params = job.params
    project, world, line = params["project"], params["world"], store.clean_id(params["line"], "line")
    folder = store.world_dir(project, world)
    job.stage = "speaking"
    name = f"speech-{line}-{speech.next_number(folder, line):04d}.wav"
    raw = os.path.join(folder, f"_spoken-{line}.wav")
    try:
        speech.say(params.get("text"), params.get("voice"), raw, float(params.get("rate") or 0), float(params.get("pitch") or 0))
        data, rate = speech.read(raw)
        data = speech.neaten(data, rate)
        speech.write(os.path.join(folder, name), data)
    finally:
        try:
            os.remove(raw)
        except OSError:
            pass
    return {"audio": name, "seconds": round(len(data) / speech.RATE, 3)}


def _stretch(project, lines, seconds, path):
    """The sound of a stretch of a scene: each line's sound at its moment (`at`, seconds into the stretch), and
    silence around them. Only for the face to move to: the film's sound is still the lines' own."""
    total = np.zeros(max(1, int(round(max(1.0, min(seconds, 180.0)) * speech.RATE))), np.float32)
    for item in lines:
        world = store.clean_id(item.get("world"), "world")
        data, rate = speech.read(store.resolve(project, f"worlds/{world}/{os.path.basename(str(item['audio']))}"))
        data = np.asarray(data, np.float32).reshape(-1)
        start = int(round(max(0.0, float(item.get("at") or 0)) * speech.RATE))
        end = min(len(total), start + len(data))
        if end > start:
            total[start:end] += data[:end - start]
    speech.write(path, np.clip(total, -1, 1))
    return path


@runner.handler("talk")
def make_talk(job):
    """An object's picture -> a video of it saying a line (params.audio), or, with params.idle (seconds),
    a short loop of it simply being alive."""
    params = job.params
    project, world = params["project"], params["world"]
    object_id = store.clean_id(params["object"], "object")
    folder = store.world_dir(project, world)
    # a line keeps its files in the world it was first made in; the one who says it may stand in another
    home = store.clean_id(params.get("line_world") or world, "world")
    line_folder = store.world_dir(project, home)
    s = params.get("settings") or {}
    idle = float(params.get("idle") or 0)
    liveliness = float(np.clip(float(s.get("liveliness", 0.5)), 0, 1))
    mood = float(np.clip(float(s.get("mood", 0)), -1, 1))
    seed = int(s.get("seed") or 0) or int(time.time() * 1000) % 2147483647
    wanted = str(s.get("lips") or "")
    if not live_portrait_available():
        raise JobError("the LivePortrait extension (sd-webui-live-portrait) is not installed in this Forge")

    # a smaller video is made faster: for a figure that is never seen close
    crop = close_up(params.get("crop")) if params.get("crop") else None
    side = int(np.clip(float(s.get("side") or (CLOSE_SIDE if crop else LONGEST_SIDE)), 256, LONGEST_SIDE))
    rgb, alpha = _still(project, world, params["picture"], side, crop)
    height, width = alpha.shape
    key = key_colour(rgb, alpha)
    work = os.path.join(folder, f"_talk-{object_id}-{time.time_ns()}")
    os.makedirs(work)
    try:
        picture_path = os.path.join(work, "character.png")
        Image.fromarray(on_key(rgb, alpha, key)).save(picture_path)
        loud = bright = None
        audio_path = None
        act = params.get("act") or None
        if idle > 0:
            seconds = float(np.clip(idle, 2, 20))
            line = None
        elif act:
            line = store.clean_id(params["line"], "line")
            seconds = float(np.clip(float(params.get("seconds") or 6), 1, 40))
        else:
            line = store.clean_id(params["line"], "line")
            if params.get("lines"):
                # a whole stretch of the scene: every line of this speaker at its moment, silence between them
                audio_path = _stretch(project, params["lines"], float(params.get("seconds") or 0), os.path.join(work, "stretch.wav"))
            elif not params.get("audio"):
                raise JobError("the line has no sound yet")
            else:
                audio_path = store.resolve(project, f"worlds/{home}/{os.path.basename(str(params['audio']))}")
            data, rate = speech.read(audio_path)
            seconds = len(data) / rate
            loud, bright = speech.envelope(data, rate, FPS), speech.brightness(data, rate, FPS)

        available = lip_models()
        model = None if idle > 0 or act or wanted == BY_LOUDNESS else (wanted if wanted in available else (available[0] if available else None))
        note = None
        job.stage = "bringing the face to life"
        job.share = 0.05
        template = performance(seconds, FPS, loud, bright, liveliness if idle <= 0 else liveliness * 0.6, mood,
                               lips=idle <= 0 and not act and (model is None or model == MUSETALK), seed=seed, act=act)
        # (MuseTalk draws a mouth again from the sound AND from the frame it is given: given a closed mouth it
        # opens it timidly, so it is given the mouth that already opens with the loudness. Wav2Lip looks at
        # the sound only.)
        video = animate(job, picture_path, template, work)
        job.share = 0.45
        matrix = "bt601"
        if model:
            job.stage = "shaping the mouth to the words"
            synced = os.path.join(work, "synced.mp4")
            # (when MuseTalk cannot, the best of FaceFusion's is tried before the mouth is left to the loudness)
            tries = [model] + [other for other in available if other != MUSETALK and model == MUSETALK][:1]
            done = False
            for attempt in tries:
                who = lip_syncer_name(attempt)
                done = lip_sync(job, video, audio_path, synced, attempt, work)
                if done:
                    matrix = _matrix_of(synced, on_key(rgb, alpha, key))
                if done and _same_mouths(video, synced, width, height, matrix):
                    done = False
                    note = f"{who} found no face to work on, so the mouth follows the loudness of the voice"
                elif not done:
                    note = f"{who}'s lip syncer failed, so the mouth follows the loudness of the voice"
                if done:
                    note = None if attempt == model else "MuseTalk could not shape this mouth, so FaceFusion's lip syncer did"
                    model = attempt
                    break
                if job.status == "cancelling":
                    raise JobError("cancelled")
            if done:
                video = synced
            else:
                if job.status == "cancelling":
                    raise JobError("cancelled")
                model, matrix = None, "bt601"
                job.stage = "bringing the face to life"
                template = performance(seconds, FPS, loud, bright, liveliness, mood, lips=True, seed=seed)
                video = animate(job, picture_path, template, work)
        job.stage = "cutting out"
        job.share = 0.86
        if idle > 0:
            name = f"object-{object_id}-{objects._next(folder, object_id):04d}.mp4"
            target = os.path.join(folder, name)
        else:
            name = f"speech-{line}-{speech.next_number(line_folder, line):04d}.mp4"
            target = os.path.join(line_folder, name)
        frames, share = _lift(job, video, rgb, alpha, key, target, FPS, matrix)
        result = {"file": name, "frames": frames, "fps": FPS, "seconds": round(frames / FPS, 3), "width": width, "height": height,
                  "lips": model or (BY_LOUDNESS if idle <= 0 and not act else None), "seed": seed, "moved": round(share, 4), "note": note,
                  "crop": crop}
        if idle > 0:
            result.update(kind="video", poster=params["picture"])
        elif not act and not params.get("lines"):
            result["audio"] = params["audio"]
        return result
    finally:
        if os.environ.get("SIM360_KEEP_WORK"):              # for looking at the steps in between
            print(f"[Simulation 360] the talking video's working files stay in {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)


def describe():
    """What the page needs to know about talking."""
    root = facefusion_root()
    return {"live_portrait": live_portrait_available(), "facefusion": root, "musetalk": musetalk_home(), "lip_models": lip_models(),
            "voices": speech.voices(), "fps": FPS}

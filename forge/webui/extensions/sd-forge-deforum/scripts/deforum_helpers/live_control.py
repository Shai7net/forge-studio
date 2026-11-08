"""Live control of Animation mode and of the video init, while a render is running.

Deforum reads the whole UI once, when a render starts, so Animation mode and the video init are fixed for the
rest of that render. This module hands them back: the bar above Deforum's preview (ui_right.py) and
DeforumationQT can both change them between one frame and the next. That is what makes a render that starts on
a video - a talking head, say - and moves over into 2D or 3D possible.

The bar's state is kept in live_control.json in the extension folder, so it survives a page reload:

    {"enabled": false, "mode": "Deforum", "video": 1.0}

- enabled: the bar's master switch. While it is off nothing is overridden and a render runs exactly as before.
- mode:    "Deforum" (whatever Animation mode the project is set to), "2D", "3D" or "Video Input".
- video:   how much of the video init goes into every frame, 0 to 1. It applies while the mode is 2D or 3D;
           "Video Input" is the whole video and "Deforum" leaves the project's own setting alone.

So the way out of a video is: pick 2D or 3D (the picture does not change, the video is still at 100%), then
take the video slider down. At 1 the video frame is the init image, exactly as Deforum has always done it;
below 1 the video frame is blended into the warped previous frame, so the animation takes over gradually.

DeforumationQT wins over the bar while the switch is ON and its own selector is not on "Deforum (as set)". It
speaks through two mediator parameters, both empty when DeforumationQT does not want to override:

    live_animation_mode   ""  |  "2D"  |  "3D"  |  "Video Input"
    live_video_influence  ""  |  0 .. 1

An older DeforumationQT mediator does not know them and answers "NO SUCH COMMAND"; the read then falls back to
the bar and is not tried again for the rest of the render (see deforum_mediator.mediator_get_optional).

Max frames: a Video Input render normally ends with the video's last frame. With the bar's master switch on
before the render starts, Deforum's own Max frames is kept when it asks for more, so the render can go on long
after the video is over (render_modes.render_input_video). Once the video runs out it switches itself off.
"""
import json
import os
import time

MODES = ("Deforum", "2D", "3D", "Video Input")
MOTION_MODES = ("2D", "3D")
DEFAULT_STATE = {"enabled": False, "mode": "Deforum", "video": 1.0, "fade": None, "plan": []}
PLAN_COLUMNS = ("At frame", "Animation mode", "Video %", "Fade frames")  # plain "Mode" collides with a statistics word in the Hebrew dictionary
PLAN_MODES = {"": "", "deforum": "Deforum", "2d": "2D", "3d": "3D",
              "video": "Video Input", "video input": "Video Input", "vid": "Video Input"}
# the plan's first column can be written in music: "beat 16", "part 2", a time like 1:30, or a plain frame number
MUSIC_WORDS = {"beat": "beats", "beats": "beats", "\u05d1\u05d9\u05d8": "beats", "\u05d1\u05d9\u05d8\u05d9\u05dd": "beats",
               "part": "parts", "parts": "parts", "section": "parts",
               "\u05d7\u05dc\u05e7": "parts", "\u05d7\u05dc\u05e7\u05d9\u05dd": "parts"}
STATE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "live_control.json")
# what the render is doing right now, for the line under the bar. The page polls it, so it says who is in charge:
# with DeforumationQT ON its selector wins over the bar, and the bar's own controls would otherwise look like a lie.
STATUS_FILE = os.path.join(os.path.dirname(STATE_FILE), "live_control_status.json")

_last_good = dict(DEFAULT_STATE)


def _clean_mode(mode):
    """A mode from the bar or from DeforumationQT. Empty, "Deforum" and anything unknown all mean: no override."""
    mode = str(mode if mode is not None else "").strip()
    return mode if mode in MODES and mode != "Deforum" else ""


def _clean_video(value):
    """The video's share as a number between 0 and 1, or None when there is nothing to override."""
    text = str(value if value is not None else "").strip()
    if text == "" or text.lower() in ("none", "null"):
        return None
    try:
        return max(0.0, min(1.0, float(text)))
    except ValueError:
        return None


def _clean_fade(data):
    """A fade on its way: where it started, where it is going and how many frames it takes.

    An armed fade is one the bar has just asked for; the render turns it into a real one on its next frame,
    which is when it knows the frame number to start counting from and the share to start from.
    """
    if not isinstance(data, dict):
        return None
    target = _clean_video(data.get("to"))
    if target is None:
        return None
    try:
        frames = max(1, int(data.get("frames", 30)))
    except (TypeError, ValueError):
        return None
    try:
        waits_for = None if data.get("at") in (None, "") else max(0, int(data.get("at")))
    except (TypeError, ValueError):
        waits_for = None
    if data.get("armed"):
        fade = {"frames": frames, "to": target, "armed": True}
        if waits_for is not None:
            fade["at"] = waits_for    # planned: it starts on that frame, not on the next one
        return fade
    origin = _clean_video(data.get("from"))
    try:
        start = int(data.get("start_frame"))
    except (TypeError, ValueError):
        return None
    if origin is None:
        return None
    return {"frames": frames, "to": target, "armed": False, "from": origin, "start_frame": start}


def _seconds(text):
    """"1:02.5", "0:10" or "10.5s" -> seconds, or None. The same shape Audio Keyframes reads, kept here so this
    module stays free of the audio code and its imports."""
    value = str(text or "").strip().lower().replace(",", ".")
    if value.endswith("s"):
        value = value[:-1].strip()
    if not value or ":" not in value and not value.replace(".", "", 1).isdigit():
        return None
    try:
        numbers = [float(field) for field in value.split(":")]
    except ValueError:
        return None
    if len(numbers) > 3 or any(number < 0 for number in numbers):
        return None
    seconds = 0.0
    for number in numbers:
        seconds = seconds * 60 + number
    return seconds


def parse_at(text, fps=15.0, song=None):
    """One cell of the plan's first column -> (frame, what it says), or (None, why it could not be read).

    A plain number is a frame. "1:30" or "12s" is a time. "beat 16" and "part 2" are the beats and the song parts
    Audio Keyframes found, counted from 1.
    """
    raw = str(text if text is not None else "").strip()
    if not raw:
        return None, ""
    try:
        return max(0, int(round(float(raw.replace(",", "."))))), ""
    except ValueError:
        pass
    word, _, number = raw.lower().replace("#", " ").partition(" ")
    kind = MUSIC_WORDS.get(word.strip())
    if kind:
        points = list((song or {}).get(kind) or [])
        try:
            index = int(float(number.strip() or 1))
        except ValueError:
            return None, f"{raw}: which one? Write it like 'beat 16'."
        if not points:
            return None, f"{raw}: no song is loaded in Audio Keyframes."
        if not 1 <= index <= len(points):
            return None, f"{raw}: the song has {len(points)} {kind}."
        return int(points[index - 1]), f"{raw} \u2192 frame {int(points[index - 1])}"
    seconds = _seconds(raw)
    if seconds is None:
        return None, f"{raw}: not a frame, a time or a point in the music."
    frame = max(0, int(round(seconds * float(fps or 15))))
    return frame, f"{raw} \u2192 frame {frame}"


def song_points(path, fps, normalize="peak", threshold=0.0, parts_rows=None):
    """The beats and the song-part starts of the song picked in Audio Keyframes, for the plan's first column."""
    from . import audio_keyframes as ak
    from .audio_keyframes_ui import ffmpeg_exe
    path = ak.clean_path(path or "")
    if not path:
        return {"beats": [], "parts": []}
    analysis = ak.analyze_audio(path, float(fps or 15), ffmpeg_exe())
    try:
        parts = list(ak.part_start_frames(ak.parse_parts(parts_rows or []), analysis.fps, analysis.frame_count))
    except Exception:
        parts = [0]
    try:
        curve = analysis.curves(normalize or ak.NORMALIZE_PEAK, float(threshold or 0.0))[ak.BAND_FULL]
        beats = [int(beat) for beat in ak.beat_frames(curve, analysis.fps, 0.25)]
    except Exception:
        beats = []
    return {"beats": beats, "parts": sorted({int(part) for part in parts} | {0})}


def _clean_plan_entry(row, fps=15.0, song=None):
    """One planned step: the frame it happens on, and what happens - a mode, a video share, or both."""
    note, text = "", ""
    if isinstance(row, dict):
        # the saved form: the share is already 0 to 1 and the frame is already worked out
        at, mode, video, frames = row.get("at"), row.get("mode"), row.get("video"), row.get("frames")
        video = _clean_video(video)
        text = str(row.get("at_text") or "")
        try:
            at = int(float(at))
        except (TypeError, ValueError):
            return None
    elif isinstance(row, (list, tuple)):
        # a line of the table: the share is a percentage and the frame may be written in music
        padded = list(row) + [None] * 4
        at, mode, video, frames = padded[:4]
        try:
            video = None if video is None or str(video).strip() == "" else max(0.0, min(1.0, float(video) / 100.0))
        except (TypeError, ValueError):
            video = None
        text = str(at if at is not None else "").strip()
        at, note = parse_at(at, fps, song)
        if at is None:
            return note or None     # a line that cannot be read says why, and is left out
        if not note:
            text = ""               # a plain frame number needs no explaining
    else:
        return None
    mode = PLAN_MODES.get(str(mode if mode is not None else "").strip().lower(), "")
    try:
        frames = 0 if frames is None or str(frames).strip() == "" else max(0, int(float(frames)))
    except (TypeError, ValueError):
        frames = 0
    if at < 0 or (not mode and video is None):
        return None   # a row with nothing to do is just an empty line in the table
    entry = {"at": at, "mode": mode, "video": video, "frames": frames}
    if text:
        entry["at_text"] = text   # what was typed, so the table still shows "beat 16"
    if note:
        entry["note"] = note
    return entry


def _clean_plan(data, fps=15.0, song=None):
    """The table, read. Lines that could not be read come back as notes, not as steps."""
    plan, notes = [], []
    for row in (data or []):
        entry = _clean_plan_entry(row, fps, song)
        if isinstance(entry, str):
            notes.append(entry)
        elif entry:
            notes.extend([entry.pop("note")] if "note" in entry else [])
            plan.append(entry)
    plan.sort(key=lambda entry: entry["at"])
    return plan, notes


def plan_text(entry):
    """What a planned step does, in a few words."""
    parts = []
    if entry["mode"]:
        parts.append(entry["mode"])
    if entry["video"] is not None:
        share = f"video {round(entry['video'] * 100)}%"
        parts.append(share + (f" over {entry['frames']}" if entry["frames"] else ""))
    return " + ".join(parts)


def set_plan(rows, fps=15, path="", normalize="peak", threshold=0.0, parts_rows=None):
    """The table in the bar, written down. Empty rows are dropped, and music words become frames.

    Returns (plan, notes): the notes are what the music words came to, and why a line could not be read.
    """
    song = None
    if any(MUSIC_WORDS.get(str(row[0]).strip().lower().replace("#", " ").partition(" ")[0])
           for row in (rows or []) if isinstance(row, (list, tuple)) and row and row[0] is not None):
        try:
            song = song_points(path, fps, normalize, threshold, parts_rows)
        except Exception as e:
            song = {"beats": [], "parts": []}
            print(f"Deforum: the song could not be read for the plan ({e})")
    plan, notes = _clean_plan(rows, fps, song)
    _write({"plan": plan})
    print("Deforum: the plan is " + ("; ".join(f"frame {e['at']}: {plan_text(e)}" for e in plan) if plan else "empty"))
    return plan, notes


def plan_rows():
    """The same list the way the table shows it, always with one empty line to type into."""
    rows = [[entry.get("at_text") or entry["at"], entry["mode"],
             None if entry["video"] is None else round(entry["video"] * 100), entry["frames"]]
            for entry in read_state()["plan"]]
    return rows + [[None, "", None, None]]


def _clean_state(data):
    state = dict(DEFAULT_STATE)
    if isinstance(data, dict):
        state["enabled"] = bool(data.get("enabled", False))
        mode = str(data.get("mode", "Deforum"))
        state["mode"] = mode if mode in MODES else "Deforum"
        video = _clean_video(data.get("video"))
        state["video"] = DEFAULT_STATE["video"] if video is None else video
        state["fade"] = _clean_fade(data.get("fade"))
        state["plan"] = _clean_plan(data.get("plan"))[0]
    return state


def read_state():
    """The bar's state. The file is read on every frame, so a click lands on the very next one."""
    global _last_good
    try:
        with open(STATE_FILE, encoding="utf-8-sig") as f:
            _last_good = _clean_state(json.load(f))
    except (OSError, ValueError):
        pass  # no file yet, or a half written one: keep the last state that did read
    return dict(_last_good)


def _write(changes):
    global _last_good
    state = read_state()
    state.update(changes)
    state = _clean_state(state)
    _last_good = dict(state)
    try:
        # written beside the file and moved into place, so a render never reads half of it
        temporary = STATE_FILE + ".tmp"
        with open(temporary, "w", encoding="utf-8") as f:
            json.dump(state, f)
        os.replace(temporary, STATE_FILE)
    except OSError as e:
        print(f"Deforum: could not save the live control bar: {e}")
    return state


def is_enabled():
    return read_state()["enabled"]


def current_mode():
    return read_state()["mode"]


def current_video_percent():
    return round(read_state()["video"] * 100)


def keeps_max_frames():
    """Whether a Video Input render keeps Deforum's own Max frames, so it can go on after the video ends."""
    return is_enabled()


def set_enabled(value):
    state = _write({"enabled": bool(value)})
    print(f"Deforum: live control {'on' if state['enabled'] else 'off'}"
          + (f" (mode {state['mode']}, video {round(state['video'] * 100)}%)" if state["enabled"] else ""))
    return state["enabled"]


def set_mode(mode):
    return _write({"mode": str(mode)})["mode"]


def set_video_percent(percent):
    try:
        value = float(percent) / 100.0
    except (TypeError, ValueError):
        value = DEFAULT_STATE["video"]
    # a hand on the slider wins: a fade that went on walking would look like the slider was broken
    return _write({"video": value, "fade": None})["video"]


def write_status(status):
    try:
        temporary = STATUS_FILE + ".tmp"
        with open(temporary, "w", encoding="utf-8") as f:
            json.dump(status, f)
        os.replace(temporary, STATUS_FILE)
    except OSError:
        pass  # the line under the bar is a nicety; a render never fails over it


def clear_status():
    write_status({"running": False})


def start_fade(frames, target_percent, at_frame=0):
    """Ask for the video init to walk to target_percent over that many frames.

    at_frame 0 means from the next frame the render draws; anything else waits for that frame, so a long render
    can be set up before it starts and left alone.
    """
    try:
        frames = max(1, int(float(frames)))
    except (TypeError, ValueError):
        frames = 30
    try:
        target = max(0.0, min(1.0, float(target_percent) / 100.0))
    except (TypeError, ValueError):
        target = 0.0
    try:
        at_frame = max(0, int(float(at_frame or 0)))
    except (TypeError, ValueError):
        at_frame = 0
    fade = {"frames": frames, "to": target, "armed": True}
    if at_frame:
        fade["at"] = at_frame
    state = _write({"enabled": True, "fade": fade})
    print(f"Deforum: the video init will fade to {round(target * 100)}% over {frames} frames,"
          + (f" starting at frame {at_frame}" if at_frame else " from the next frame on"))
    return state["fade"]


def cancel_fade():
    return _write({"fade": None})


def resolve(base_mode, state=None, mediator_mode="", mediator_video="", last_motion_mode=None):
    """What one frame should do: (motion mode, the video's share 0..1, whether anything was overridden).

    base_mode is the project's own Animation mode. DeforumationQT's two values win over the bar's, and both
    sides say "no override" with an empty value.
    """
    bar = read_state() if state is None else _clean_state(state)
    mode = _clean_mode(mediator_mode)
    if not mode and bar["enabled"]:
        mode = _clean_mode(bar["mode"])
    video = _clean_video(mediator_video)
    if video is None and bar["enabled"] and mode in MOTION_MODES:
        video = bar["video"]  # the bar's slider belongs to its 2D and 3D modes; "Deforum" leaves the project alone

    effective = mode or (base_mode if base_mode in MODES else "")
    fallback = last_motion_mode if last_motion_mode in MOTION_MODES else "2D"
    if effective == "Video Input":
        motion, mix = fallback, (1.0 if video is None else video)
    else:
        motion = effective if effective in MOTION_MODES else fallback
        mix = 0.0 if video is None else video
    return motion, max(0.0, min(1.0, mix)), bool(mode) or video is not None


class RenderState:
    """The live state of one render. render.py updates it once per frame and then just reads the attributes."""

    def __init__(self, base_mode, mediator_on):
        self.base_mode = base_mode
        self.mediator_on = bool(mediator_on)
        self.motion_mode = base_mode if base_mode in MOTION_MODES else "2D"
        self.video_mix = 1.0 if base_mode == "Video Input" else 0.0
        self.video_only = self.video_mix >= 1.0
        self.active = False
        self.extracted = None      # how many video frames are on disk, once that has been looked up
        self.extract_tried = False
        self.missing_warned = False
        self.source = ""
        self.fade_left = None
        self.fade_to = None
        self.fade_at = None
        self.fired = set()
        self.next_step = None
        self._reported = None

    def update(self, args, anim_args, frame_idx):
        mediator_mode = mediator_video = ""
        if self.mediator_on:
            from .deforum_mediator import mediator_get_optional, mediator_setValue
            mediator_mode = mediator_get_optional("live_animation_mode")
            mediator_video = mediator_get_optional("live_video_influence")
            asked = mediator_get_optional("live_video_fade")  # DeforumationQT's own fade button
            if asked:
                mediator_setValue("live_video_fade", "")      # taken; it must not fire again on the next frame
                frames, _, rest = asked.partition(":")
                target, _, at_frame = rest.partition(":")
                start_fade(frames, float(target or 0) * 100, at_frame)
                mediator_video = ""
        bar = self._plan(read_state(), frame_idx)
        motion, mix, active = resolve(self.base_mode, bar, mediator_mode, mediator_video, self.motion_mode)
        if _clean_video(mediator_video) is not None:
            if bar.get("fade"):
                cancel_fade()  # DeforumationQT is holding the video itself, so there is nothing left to fade
        else:
            faded = self._fade(bar, mix, frame_idx)
            if faded is not None:
                mix, active = faded, True
        if mix > 0 and not self.video_ready(args, anim_args, frame_idx):
            mix = 0.0  # no video frame for this frame: the animation goes on by itself
        self.source = ("DeforumationQT" if (_clean_mode(mediator_mode) or _clean_video(mediator_video) is not None)
                       else ("bar" if active else ""))
        self.motion_mode, self.video_mix, self.active = motion, mix, active
        self.video_only = mix >= 1.0
        self._report(frame_idx)
        return self

    def _plan(self, bar, frame_idx):
        """Carry out every planned step the render has reached, once each, and say what is coming next."""
        self.next_step = None
        if not bar["enabled"]:
            return bar
        for entry in bar["plan"]:
            key = (entry["at"], entry["mode"], entry["video"], entry["frames"])
            if key in self.fired:
                continue
            if entry["at"] > frame_idx:
                self.next_step = entry       # the list is sorted, so this is the next one due
                break
            self.fired.add(key)
            if entry["mode"]:
                set_mode(entry["mode"])
            if entry["video"] is not None:
                if entry["frames"]:
                    start_fade(entry["frames"], entry["video"] * 100)
                else:
                    set_video_percent(entry["video"] * 100)
            print(f"\033[36mDeforum live control:\033[0m frame {frame_idx}, as planned: {plan_text(entry)}")
            bar = read_state()
        return bar

    def _fade(self, bar, mix, frame_idx):
        """Where the fade has got to on this frame, or None when no fade is running."""
        fade = bar.get("fade")
        if not fade:
            self.fade_left = self.fade_at = None
            return None
        if fade.get("armed") and fade.get("at") and frame_idx < fade["at"]:
            self.fade_at = fade["at"]       # still waiting for its frame: nothing is overridden yet
            self.fade_left = None
            return None
        self.fade_at = None
        if fade.get("armed"):
            fade = {"frames": fade["frames"], "to": fade["to"], "armed": False, "from": mix, "start_frame": frame_idx}
            _write({"fade": fade})
            print(f"\033[36mDeforum live control:\033[0m fading the video init from {round(mix * 100)}%"
                  f" to {round(fade['to'] * 100)}% over {fade['frames']} frames, starting at frame {frame_idx}")
        walked = frame_idx - fade["start_frame"]
        if walked >= fade["frames"]:
            _write({"fade": None, "video": fade["to"]})
            self.fade_left = self.fade_at = None
            print(f"\033[36mDeforum live control:\033[0m the video init is at {round(fade['to'] * 100)}% now")
            return fade["to"]
        value = fade["from"] + (fade["to"] - fade["from"]) * (max(0, walked) / fade["frames"])
        self.fade_left = fade["frames"] - max(0, walked)
        self.fade_to = fade["to"]
        _write({"video": value})  # the bar's own slider walks along with it, and so does the line under it
        return value

    def needs_depth_model(self, anim_args, args):
        """3D warping needs depth, and a render that started in 2D or on a video has no depth model yet."""
        return (self.motion_mode == "3D" and not self.video_only
                and anim_args.use_depth_warping and not args.motion_preview_mode)

    def frame_path(self, args, anim_args, frame_idx):
        if not anim_args.video_init_path:
            return None
        # the same name Deforum's own get_next_frame builds: the video's file name, without its extension
        name = os.path.splitext(os.path.basename(anim_args.video_init_path))[0]
        return os.path.join(args.outdir, "inputframes", name + f"{frame_idx:09}.jpg")

    def video_ready(self, args, anim_args, frame_idx):
        path = self.frame_path(args, anim_args, frame_idx)
        if path and os.path.isfile(path):
            return True
        if path and not self.extract_tried:
            self.extract_tried = True  # a render that did not start on the video has no frames yet: extract them once
            self._extract(args, anim_args)
            if os.path.isfile(path):
                return True
        if not self.missing_warned:
            self.missing_warned = True
            if not anim_args.video_init_path:
                print("Deforum: live control asked for the video init, but no Video init path is set. Going on without it.")
            else:
                print(f"Deforum: the video has no frame {frame_idx}"
                      + (f" (it holds {self.extracted})" if self.extracted else "") + ". Going on without it.")
        return False

    def _extract(self, args, anim_args):
        from .video_audio_utilities import vid2frames
        folder = os.path.join(args.outdir, "inputframes")
        os.makedirs(folder, exist_ok=True)
        print(f"Deforum: live control turned the video init on. Extracting frames to {folder}...")
        try:
            vid2frames(video_path=anim_args.video_init_path, video_in_frame_path=folder, n=anim_args.extract_nth_frame,
                       overwrite=anim_args.overwrite_extracted_frames, extract_from_frame=anim_args.extract_from_frame,
                       extract_to_frame=anim_args.extract_to_frame)
            self.extracted = len([f for f in os.listdir(folder) if f.endswith(".jpg")])
            print(f"Deforum: {self.extracted} video frames ready.")
        except Exception as e:
            print(f"Deforum: could not extract the video init frames: {e}")

    def blend_video(self, prev_img, args, anim_args, frame_idx):
        """Mix the video frame into the warped previous frame: that is the cross-fade from the one to the other."""
        import cv2
        path = self.frame_path(args, anim_args, frame_idx)
        frame = cv2.imread(path) if path and os.path.isfile(path) else None
        if frame is None:
            return prev_img
        if frame.shape[:2] != prev_img.shape[:2]:
            frame = cv2.resize(frame, (prev_img.shape[1], prev_img.shape[0]), interpolation=cv2.INTER_AREA)
        return cv2.addWeighted(prev_img, 1.0 - self.video_mix, frame, self.video_mix, 0)

    def _report(self, frame_idx):
        # the page's line under the bar is written on every frame, so it also shows that a render is alive
        write_status({"running": True, "at": time.time(), "frame": frame_idx, "mode": self.motion_mode,
                      "video": round(self.video_mix, 3), "video_only": self.video_only, "source": self.source,
                      "project": self.base_mode, "fade_left": self.fade_left, "fade_at": self.fade_at,
                      "fade_to": None if self.fade_to is None else round(self.fade_to, 3),
                      "next_at": None if self.next_step is None else self.next_step["at"],
                      "next_what": None if self.next_step is None else plan_text(self.next_step)})
        now = (self.motion_mode, round(self.video_mix, 3), self.video_only, self.active, self.source,
               self.fade_left, self.fade_at, None if self.next_step is None else self.next_step["at"])
        if now == self._reported:
            return
        self._reported = now
        if not self.active:
            return
        if self.video_only:
            what = "the video init"
        elif self.video_mix > 0:
            what = f"{self.motion_mode} with the video at {round(self.video_mix * 100)}%"
        else:
            what = self.motion_mode
        print(f"\033[36mDeforum live control:\033[0m frame {frame_idx} goes on with {what}.")

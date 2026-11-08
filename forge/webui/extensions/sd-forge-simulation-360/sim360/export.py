"""The recorded flight as a video file.

The page draws every frame at the size the user asked for and sends its pixels here as they are (RGBA, the
top row first); ffmpeg scales them when the frames were drawn larger than the video (supersampling) and
encodes. Nothing is compressed on the way, so the video is as good as the encoder's own setting. A browser
that turns out to send slowly (measured on the first frames) packs each frame as a 97% JPEG instead; they are
unpacked here, and the difference cannot be seen after the encoder.
"""

import os
import re
import subprocess
import threading
import time
import uuid

FORMATS = {
    # name: (extension, what it is for)
    "mp4-h264": ("mp4", "plays everywhere"),
    "mp4-nvenc": ("mp4", "the same, encoded by the NVIDIA card: several times faster"),
    "mp4-h265": ("mp4", "smaller files, newer players"),
    "webm-vp9": ("webm", "for the web"),
    "mov-prores": ("mov", "for editing, very large"),
    "gif": ("gif", "short loops"),
    "png": ("png", "one picture per frame"),
}

_sessions = {}
_lock = threading.Lock()


def ffmpeg():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


# Loudness. Video sites play everything at about -14 LUFS: what is louder they turn down, what is quieter
# they leave quiet. A mix of speech, a drone and a few effects comes out several units below that, so the
# film sounds faint beside whatever plays before and after it.
LOUD_TARGET = -14.0       # LUFS, the whole recording
LOUD_CEILING = -1.0       # dBTP: no peak above this
LOUD_TOUCHED = 0.05       # turning up may push this share of the recording into the limiter, and no more
LOUD_DEEPEST = 4.0        # and the limiter never has to take more than this many dB off the loudest moment


def loud_gain(integrated, peaks, target=LOUD_TARGET, ceiling=LOUD_CEILING, touched=LOUD_TOUCHED, deepest=LOUD_DEEPEST):
    """How many dB a mix is turned up (or down) to be as loud as the web plays things, without squashing it.
    `integrated`: its loudness in LUFS; `peaks`: its peak level in dBFS over every twentieth of a second.
    A mix that is too loud is simply turned down. One that is too quiet is turned up as far as the target,
    but only so far that the limiter touches no more than `touched` of it (the tips of the loudest
    syllables: speech has tall peaks) and takes no more than `deepest` dB off anything."""
    wanted = target - integrated
    if wanted <= 0:
        return wanted
    if not len(peaks):
        return 0.0
    ordered = sorted(peaks)
    high = ordered[min(len(ordered) - 1, int(len(ordered) * (1 - touched)))]
    return max(0.0, min(wanted, (ceiling - 0.5) - high, (ceiling - 0.5) - ordered[-1] + deepest))


def duck_expression(spoken, depth, attack=0.08, release=0.3):
    """The volume, moment by moment, of everything that is not speech: `depth` dB lower while a line is
    spoken, back up in the gaps. `spoken`: [(start, end)] in seconds. The fall is finished when the line
    begins and the rise begins when it ends, so nothing pumps. An expression for ffmpeg's volume filter,
    or None when there is nothing to do."""
    spans = []
    for start, end in sorted((float(a), float(b)) for a, b in spoken if b > a):
        if spans and start - spans[-1][1] < attack + release:
            spans[-1][1] = max(spans[-1][1], end)           # lines that follow each other closely are one stretch
        else:
            spans.append([start, end])
    if not spans or depth <= 0:
        return None
    down = 1 - 10 ** (-depth / 20)
    windows = [f"clip((t-{a - attack:.3f})/{attack:.3f},0,1)*clip(({b + release:.3f}-t)/{release:.3f},0,1)" for a, b in spans]
    most = windows[0]
    for window in windows[1:]:
        most = f"max({most},{window})"
    return f"1-{down:.4f}*{most}"


def measure(source):
    """A sound's loudness as the standard measures it: {'lufs', 'peak'} (LUFS, dBTP), or None. `source`:
    the ffmpeg arguments that name the input."""
    done = subprocess.run([ffmpeg(), "-hide_banner", "-nostats", "-nostdin", *source, "-vn", "-af", "ebur128=peak=true", "-f", "null", "-"],
                          capture_output=True, stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    said = done.stderr.decode("utf8", "replace")
    summary = said[said.rfind("Summary:"):] if "Summary:" in said else ""
    loud, peak = re.search(r"I:\s+(-?[\d.]+) LUFS", summary), re.search(r"Peak:\s+(-?[\d.]+) dBFS", summary)
    if not loud:
        return None
    return {"lufs": float(loud.group(1)), "peak": float(peak.group(1)) if peak else None}


def _stamp(seconds):
    """Seconds as an .srt file writes them: 00:00:03,250"""
    ms = int(round(max(0.0, float(seconds)) * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def subtitle_text(lines):
    """[{start, end, text}] -> the text of an .srt file ('' when there is nothing to show)."""
    shown = []
    for line in sorted(lines or [], key=lambda l: float(l.get("start") or 0)):
        text = " ".join(str(line.get("text") or "").split())
        start, end = float(line.get("start") or 0), float(line.get("end") or 0)
        if text and end > start:
            if any("֐" <= c <= "ࣿ" for c in text):
                # a Hebrew or Arabic line is told to run from the right, or its full stop lands at the wrong end
                text = "‫" + text + "‬"
            shown.append(f"{len(shown) + 1}\n{_stamp(start)} --> {_stamp(end)}\n{text}\n")
    return "\n".join(shown)


# what a stretch of weather sounds like: ffmpeg makes the noise, the filters shape it, the number is its level
_AMBIENCE = {
    "rain": ("anoisesrc=color=white:amplitude=0.5:seed=7", "highpass=f=700,lowpass=f=7500,tremolo=f=0.37:d=0.12", 0.11),
    "snow": ("anoisesrc=color=brown:amplitude=0.7:seed=11", "highpass=f=90,lowpass=f=520,tremolo=f=0.11:d=0.5", 0.16),
}


class Session:
    def __init__(self, folder, name, width, height, fps, kind, quality, source_width, source_height,
                 subtitles=None, burn=False):
        if kind not in FORMATS:
            raise ValueError(f"unknown format {kind}")
        self.id = uuid.uuid4().hex[:12]
        self.kind = kind
        self.fps = float(fps)
        self.folder = folder
        self.note = None
        self.sound = 0
        self.loudness = None
        self.frames = 0
        self.started = time.time()
        self.frame_bytes = source_width * source_height * 4
        self.error = None
        extension = FORMATS[kind][0]
        stem = self._free_stem(folder, name, extension, kind == "png")
        if kind == "png":
            self.path = os.path.join(folder, stem)
            os.makedirs(self.path)
            target = os.path.join(self.path, "%05d.png")
        else:
            self.path = os.path.join(folder, f"{stem}.{extension}")
            target = self.path
        # H.264 and H.265 want even sides
        if kind in ("mp4-h264", "mp4-nvenc", "mp4-h265", "webm-vp9"):
            width, height = width - width % 2, height - height % 2
        self.width, self.height = width, height
        self.stem = stem
        self.subtitles = subtitle_text(subtitles)
        written = None
        if self.subtitles and burn:
            # drawn into the picture by ffmpeg's own subtitle renderer (it shapes Hebrew and Arabic properly);
            # the file gets a plain name in the working folder, because the filter chokes on a Windows path
            written = f".{self.id}.srt"
            with open(os.path.join(folder, written), "w", encoding="utf-8") as file:
                file.write(self.subtitles)
        self.burned = written

        filters = []
        scale =f"scale={width}:{height}:flags=lanczos+accurate_rnd+full_chroma_int"
        tags = "setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv"
        quality = int(quality)
        # The encoder sets the pace of a recording: the page waits for each frame to be taken. "slow" made a
        # 1080p recording crawl at 1.6 frames a second on this four-core laptop for a few percent of file size.
        if kind == "mp4-h264":
            filters += [scale + ":out_color_matrix=bt709:out_range=tv", "format=yuv420p", tags]
            codec = ["-c:v", "libx264", "-crf", str(quality), "-preset", "medium", "-movflags", "+faststart"]
        elif kind == "mp4-nvenc":
            filters += [scale + ":out_color_matrix=bt709:out_range=tv", "format=yuv420p", tags]
            codec = ["-c:v", "h264_nvenc", "-preset", "p6", "-tune", "hq", "-rc", "vbr", "-cq", str(quality), "-b:v", "0",
                     "-movflags", "+faststart"]
        elif kind == "mp4-h265":
            filters += [scale + ":out_color_matrix=bt709:out_range=tv", "format=yuv420p", tags]
            codec = ["-c:v", "libx265", "-crf", str(quality), "-preset", "fast", "-tag:v", "hvc1",
                     "-x265-params", "log-level=error", "-movflags", "+faststart"]
        elif kind == "webm-vp9":
            filters += [scale + ":out_color_matrix=bt709:out_range=tv", "format=yuv420p", tags]
            codec = ["-c:v", "libvpx-vp9", "-crf", str(quality), "-b:v", "0", "-row-mt", "1", "-deadline", "good",
                     "-cpu-used", "2"]
        elif kind == "mov-prores":
            filters += [scale + ":out_color_matrix=bt709:out_range=tv", "format=yuv422p10le", tags]
            codec = ["-c:v", "prores_ks", "-profile:v", "3", "-vendor", "apl0"]
        elif kind == "gif":
            filters += [scale, "split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=sierra2_4a"]
            codec = ["-loop", "0"]
        else:
            filters += [scale]
            codec = ["-c:v", "png", "-start_number", "1"]
        if written:
            size = max(11, round(15 * min(1.0, (width / height) / (16 / 9)) ** 0.5))     # a little smaller on an upright video
            style = f"FontName=Arial,FontSize={size},Bold=1,Outline=1.3,Shadow=0.7,MarginV=22,Alignment=2,Encoding=-1"
            filters.insert(1, f"subtitles=f={written}:force_style='{style}'")
        command = [ffmpeg(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                   "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{source_width}x{source_height}",
                   "-r", f"{fps:g}", "-i", "-", "-an", "-vf", ",".join(filters), *codec, "-r", f"{fps:g}", target]
        self.log = open(os.path.join(folder, f".{self.id}.ffmpeg.log"), "w+b")
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=self.log, stderr=subprocess.STDOUT, cwd=folder,
                                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    @staticmethod
    def _free_stem(folder, name, extension, is_folder):
        stem, n = name, 1
        while os.path.exists(os.path.join(folder, stem if is_folder else f"{stem}.{extension}")):
            n += 1
            stem = f"{name} ({n})"
        return stem

    def _unpacked(self, data):
        """A frame that arrived as a JPEG or PNG picture (a browser that sends slowly packs its frames)."""
        import base64
        import cv2
        import numpy as np
        if data[:11] == b"data:image/":             # packed as text
            data = base64.b64decode(data[data.index(b",") + 1:])
        if data[:3] != b"\xff\xd8\xff" and data[:8] != b"\x89PNG\r\n\x1a\n":
            raise ValueError(f"a frame of {len(data)} bytes, expected {self.frame_bytes}")
        picture = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if picture is None or picture.shape[0] * picture.shape[1] * 4 != self.frame_bytes:
            raise ValueError("a packed frame of the wrong size")
        return cv2.cvtColor(picture, cv2.COLOR_BGR2RGBA).tobytes()

    def _said(self):
        try:
            self.log.flush()
            self.log.seek(0)
            return self.log.read().decode("utf8", "replace").strip()[-600:]
        except Exception:
            return ""

    def add(self, pixels):
        if len(pixels) != self.frame_bytes:
            pixels = self._unpacked(pixels)
        if self.process.poll() is not None:
            raise RuntimeError("the encoder stopped: " + self._said())
        try:
            self.process.stdin.write(pixels)
        except OSError:
            raise RuntimeError("the encoder stopped: " + self._said())
        self.frames += 1

    def _close(self):
        try:
            self.process.stdin.close()
        except OSError:
            pass
        try:
            code = self.process.wait(timeout=600)
        except subprocess.TimeoutExpired:
            self.process.kill()
            code = -1
        said = self._said()
        self.log.close()
        for leftover in (self.log.name, os.path.join(self.folder, self.burned) if self.burned else None):
            try:
                if leftover:
                    os.remove(leftover)
            except OSError:
                pass
        return code, said

    def _add_sound(self, tracks, ambience, loudness=None, duck=0.0):
        """Mixes the spoken lines and the weather into one sound track and puts it on the video.
        tracks: [{path, start, gain, speech, seconds}]; ambience: [{kind, start, end, gain}]. A recording
        that cannot carry sound (pictures, a GIF) gets the mix beside it as a .wav file.
        `duck`: dB by which everything that is not speech is lowered while a line is spoken. `loudness`: the
        LUFS the whole mix is brought to (see loud_gain); None leaves it as it was mixed."""
        duration = self.frames / self.fps
        inputs, chains, mixed, voices, spoken = [], [], [], [], []
        shape = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
        for track in tracks:
            if track["start"] >= duration or not os.path.isfile(track["path"]):
                continue
            inputs += ["-i", track["path"]]
            n = len(mixed)
            chains.append(f"[{n + 1}:a]{shape},volume={max(0.0, min(4.0, track['gain'])):.3f},"
                          f"adelay={int(round(track['start'] * 1000))}:all=1[s{n}]")
            mixed.append(f"[s{n}]")
            if track.get("speech"):
                voices.append(f"[s{n}]")
                spoken.append((track["start"], track["start"] + float(track.get("seconds") or 0)))
        for stretch in ambience:
            made = _AMBIENCE.get(str(stretch.get("kind")))
            start, end = max(0.0, float(stretch.get("start") or 0)), min(duration, float(stretch.get("end") or 0))
            if not made or end - start < 0.3:
                continue
            length, fade = end - start, min(0.6, (end - start) / 3)
            inputs += ["-f", "lavfi", "-t", f"{length:.3f}", "-i", made[0]]
            n = len(mixed)
            level = made[2] * max(0.0, min(2.0, float(stretch.get("gain") or 1)))
            chains.append(f"[{n + 1}:a]{made[1]},{shape},volume={level:.4f},"
                          f"afade=t=in:d={fade:.2f},afade=t=out:st={length - fade:.3f}:d={fade:.2f},"
                          f"adelay={int(round(start * 1000))}:all=1[s{n}]")
            mixed.append(f"[s{n}]")
        if not mixed:
            return
        ducked = duck_expression(spoken, float(duck or 0)) if voices and len(voices) < len(mixed) else None
        if ducked:
            # everything that is not speech is mixed by itself, steps back while somebody speaks, and joins the voices
            rest = [label for label in mixed if label not in voices]
            chains.append("".join(rest) + f"amix=inputs={len(rest)}:normalize=0:dropout_transition=0,volume=eval=frame:volume='{ducked}'[bed]")
            together = "".join(voices) + f"[bed]amix=inputs={len(voices) + 1}:normalize=0:dropout_transition=0"
        else:
            together = "".join(mixed) + f"amix=inputs={len(mixed)}:normalize=0:dropout_transition=0"
        whole = None
        if loudness is not None:
            # The mix is made once as it is, measured, and then turned up or down by one amount for the whole
            # recording; a limiter only catches the few peaks that would go over the ceiling.
            whole = os.path.join(self.folder, f".{self.id}.mix.raw")
            made = subprocess.run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-f", "lavfi", "-t", "0.1", "-i", "anullsrc=r=48000:cl=stereo",
                                   *inputs, "-filter_complex", ";".join(chains + [together + f",apad,atrim=0:{duration:.3f}[sound]"]),
                                   "-map", "[sound]", "-f", "f32le", "-ar", "48000", "-ac", "2", whole],
                                  capture_output=True, stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            before = measure(["-f", "f32le", "-ar", "48000", "-ac", "2", "-i", whole]) if made.returncode == 0 else None
            if before is None:
                self.note = "the loudness could not be measured: the sound is as it was mixed"
                try:
                    os.remove(whole)
                except OSError:
                    pass
                whole = None
            else:
                import numpy
                samples = numpy.memmap(whole, dtype=numpy.float32, mode="r")
                step = 4800                                         # a twentieth of a second, both channels
                count = len(samples) // step
                peaks = numpy.abs(samples[:count * step].reshape(count, step)).max(axis=1) if count else numpy.zeros(0)
                peaks = (20 * numpy.log10(numpy.maximum(peaks[peaks > 1e-5], 1e-5))).tolist()
                del samples
                gain = loud_gain(before["lufs"], peaks, float(loudness))
                limit = 10 ** ((LOUD_CEILING - 0.8) / 20)           # the limiter sees samples; true peaks are a little higher
                inputs, chains = ["-f", "f32le", "-ar", "48000", "-ac", "2", "-i", whole], []
                together = f"[1:a]volume={gain:.2f}dB,alimiter=limit={limit:.4f}:level=false:attack=3:release=60"
                self.loudness = {"before": before["lufs"], "gain": round(gain, 2)}
        if whole is None:
            together += ",alimiter=limit=0.94"
        chains.append(together + f",apad,atrim=0:{duration:.3f}[sound]")
        silent = self.kind in ("gif", "png")
        if silent:
            target = os.path.join(self.path, "sound.wav") if self.kind == "png" else os.path.join(self.folder, self.stem + ".wav")
            command = [ffmpeg(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-f", "lavfi", "-t", "0.1", "-i", "anullsrc=r=48000:cl=stereo",
                       *inputs, "-filter_complex", ";".join(chains), "-map", "[sound]", "-c:a", "pcm_s16le", target]
        else:
            extension = os.path.splitext(self.path)[1]
            target = os.path.join(self.folder, f".{self.id}.sound{extension}")
            codec = {".webm": ["-c:a", "libopus", "-b:a", "160k"], ".mov": ["-c:a", "pcm_s16le"]}.get(extension, ["-c:a", "aac", "-b:a", "224k"])
            command = [ffmpeg(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", self.path, *inputs,
                       "-filter_complex", ";".join(chains), "-map", "0:v", "-map", "[sound]", "-c:v", "copy", *codec]
            if extension == ".mp4":
                command += ["-movflags", "+faststart"]
            command.append(target)
        done = subprocess.run(command, capture_output=True, stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if whole:
            try:
                os.remove(whole)
            except OSError:
                pass
        if done.returncode != 0 or not os.path.isfile(target):
            self.note = "the sound could not be added: " + done.stderr.decode("utf8", "replace").strip()[-300:]
            self.loudness = None
            try:
                os.remove(target)
            except OSError:
                pass
            return
        if not silent:
            os.replace(target, self.path)
        self.sound = len(mixed)
        if self.loudness:
            # what came of it, measured on the finished file: this is what is shown, not what was aimed at
            after = measure(["-i", target if silent else self.path])
            if after:
                self.loudness.update(lufs=after["lufs"], peak=after["peak"])

    def finish(self, tracks=None, ambience=None, loudness=None, duck=0.0):
        code, said = self._close()
        if code != 0:
            raise RuntimeError("the encoder failed: " + said)
        if tracks or ambience:
            self._add_sound(tracks or [], ambience or [], loudness, duck)
        subtitles = None
        if self.subtitles:
            subtitles = os.path.join(self.path, "subtitles.srt") if os.path.isdir(self.path) else os.path.join(self.folder, self.stem + ".srt")
            with open(subtitles, "w", encoding="utf-8-sig") as file:
                file.write(self.subtitles)
        size = 0
        if os.path.isfile(self.path):
            size = os.path.getsize(self.path)
        elif os.path.isdir(self.path):
            size = sum(os.path.getsize(os.path.join(self.path, f)) for f in os.listdir(self.path))
        return {"path": self.path, "frames": self.frames, "bytes": size, "width": self.width, "height": self.height,
                "seconds": round(time.time() - self.started, 1), "sound": self.sound, "subtitles": subtitles, "note": self.note,
                "loudness": self.loudness}

    def cancel(self):
        try:
            self.process.kill()
        except OSError:
            pass
        self._close()
        try:
            if os.path.isfile(self.path):
                os.remove(self.path)
            elif os.path.isdir(self.path):
                import shutil
                shutil.rmtree(self.path, ignore_errors=True)
        except OSError:
            pass


def start(folder, name, width, height, fps, kind, quality, source_width, source_height, subtitles=None, burn=False):
    session = Session(folder, name, width, height, fps, kind, quality, source_width, source_height, subtitles, burn)
    with _lock:
        _sessions[session.id] = session
    return session


def get(session_id):
    with _lock:
        session = _sessions.get(session_id)
    if session is None:
        raise KeyError("no such recording")
    return session


def end(session_id):
    with _lock:
        return _sessions.pop(session_id, None)

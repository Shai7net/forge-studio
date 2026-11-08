"""The beats of a piece of music, for cutting on the beat.

Found the classic way (Ellis 2007, as librosa does it), with numpy alone:
1. an onset envelope: how much the sound's spectrum rises from one short window to the next (log magnitudes,
   positive differences summed), with its slow average taken away;
2. the tempo: the lag at which that envelope repeats best (autocorrelation), weighted towards 120 beats a minute so
   a song is not read at half or double its speed without reason;
3. the beats: dynamic programming over the envelope, each beat strong and about one period after the one before;
4. the bars: of the four ways to count every fourth beat, the one whose beats are strongest is taken as the "one".

Answers are cached next to the media's other pictures (media.py), keyed by the file.
"""

import json
import os
import subprocess

import numpy as np

from . import library, media

SR = 22050
HOP = 512
NFFT = 2048
VERSION = 1


def _decode(path):
    out = subprocess.run([library.ffmpeg(), "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                         capture_output=True, timeout=600, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return np.frombuffer(out.stdout, np.float32)


def onset_envelope(y):
    """Spectral flux, one value per HOP samples."""
    if y.size < NFFT:
        return np.zeros(1, np.float32)
    frames = 1 + (y.size - NFFT) // HOP
    idx = np.arange(NFFT)[None, :] + HOP * np.arange(frames)[:, None]
    win = np.hanning(NFFT).astype(np.float32)
    env = np.zeros(frames, np.float32)
    prev = None
    # log-spaced bands (like mel bands) from 30 Hz to 8 kHz, each counted alike: a kick's few low bins weigh as much
    # as a hi-hat's hundreds, so the beat is not read on the off-beat
    edges = np.unique(np.round(np.geomspace(30, 8000, 33) * NFFT / SR).astype(int))
    bands = [(edges[i], max(edges[i] + 1, edges[i + 1])) for i in range(len(edges) - 1)]
    # in blocks, so a long song does not need its whole spectrogram at once
    for a in range(0, frames, 2048):
        spec = np.abs(np.fft.rfft(y[idx[a:a + 2048]] * win, axis=1))
        pooled = np.stack([spec[:, lo:hi].mean(axis=1) for lo, hi in bands], axis=1)
        logm = np.log1p(100.0 * pooled)
        first = logm[:1] if prev is None else prev
        diff = np.diff(np.concatenate([first, logm]), axis=0)
        env[a:a + len(logm)] = np.maximum(diff, 0).sum(axis=1)
        prev = logm[-1:]
    # the slow average away (about a second), and zero below it
    k = int(SR / HOP)
    smooth = np.convolve(env, np.ones(k) / k, mode="same")
    env = np.maximum(env - smooth, 0)
    peak = env.max()
    return env / peak if peak > 0 else env


def tempo(env, fps, lo=60.0, hi=200.0, prior=120.0):
    """Beats a minute: the best repeating lag of the envelope, weighted (log-normal, one octave wide) towards `prior`."""
    n = env.size
    if n < fps * 4:
        return prior
    e = env - env.mean()
    spec = np.fft.rfft(e, 2 * n)
    ac = np.fft.irfft(spec * np.conj(spec))[:n]
    lags = np.arange(int(fps * 60 / hi), int(fps * 60 / lo) + 1)
    lags = lags[(lags > 0) & (lags < n)]
    bpm = 60.0 * fps / lags
    weight = np.exp(-0.5 * (np.log2(bpm / prior)) ** 2)
    score = ac[lags] * weight
    best = int(np.argmax(score))
    # a finer lag between the neighbours (parabola through the three scores)
    if 0 < best < len(lags) - 1:
        a, b, c = score[best - 1], score[best], score[best + 1]
        d = 0.5 * (a - c) / (a - 2 * b + c) if (a - 2 * b + c) != 0 else 0.0
        lag = lags[best] + max(-0.5, min(0.5, d))
    else:
        lag = lags[best]
    return 60.0 * fps / lag


def track(env, fps, bpm, tightness=100.0):
    """Beat frames by dynamic programming: strong onsets about a period apart."""
    period = 60.0 * fps / bpm
    n = env.size
    local = np.convolve(env, np.exp(-0.5 * ((np.arange(-period, period + 1)) / (period / 32.0)) ** 2), mode="same")
    score = local.copy()
    back = -np.ones(n, np.int64)
    window = np.arange(-int(round(2 * period)), -int(round(period / 2)) + 1)
    penalty = -tightness * np.log(-window / period) ** 2
    for t in range(n):
        prev = t + window
        ok = prev >= 0
        if not ok.any():
            continue
        cand = score[prev[ok]] + penalty[ok]
        j = int(np.argmax(cand))
        if cand[j] > 0:
            score[t] = local[t] + cand[j]
            back[t] = prev[ok][j]
    # the last beat: the best score in the last period, among the onsets that are not almost silent
    tail = np.arange(max(0, n - int(period) - 1), n)
    last = int(tail[np.argmax(score[tail])])
    beats = [last]
    while back[beats[-1]] >= 0:
        beats.append(int(back[beats[-1]]))
    beats = np.array(beats[::-1])
    # trim the weak beats the tracking adds before the music starts and after it ends
    strong = local[beats]
    level = 0.5 * np.median(strong) if strong.size else 0
    keep = np.where(strong >= level)[0]
    if keep.size:
        beats = beats[keep[0]:keep[-1] + 1]
    return beats


def analyse(path):
    """{tempo, beats: [seconds in the file], bar: index of the first beat that is a "one", duration}."""
    y = _decode(path)
    fps = SR / HOP
    env = onset_envelope(y)
    bpm = tempo(env, fps)
    frames = track(env, fps, bpm) if env.size > fps else np.array([], np.int64)
    # the onset sits a little after the window starts: half a window is NFFT/2 samples
    times = (frames * HOP + NFFT / 2) / SR
    bar = 0
    if len(frames) >= 8:
        strength = [float(np.mean(env[frames[p::4]])) for p in range(4)]
        bar = int(np.argmax(strength))
    return {"tempo": round(float(bpm), 2), "beats": [round(float(t), 4) for t in times], "bar": bar,
            "duration": round(y.size / SR, 3), "version": VERSION}


def beats(path):
    out = media._cache(media._key(path) + f"-beats{VERSION}.json")
    if os.path.isfile(out):
        with open(out, encoding="utf-8") as f:
            return json.load(f)
    found = analyse(path)
    with open(out + ".tmp", "w", encoding="utf-8") as f:
        json.dump(found, f)
    os.replace(out + ".tmp", out)
    return found

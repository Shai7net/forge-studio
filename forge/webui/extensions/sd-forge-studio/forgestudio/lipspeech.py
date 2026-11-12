"""Speech audition and tuning before lip generation. No function here runs MuseTalk or edits the timeline."""
import hashlib
import json
import math
import os
import tempfile
import uuid

from . import frames, library, media, paths, projects
from .sound import _Task, _on_card, _vs_sound

DELIVERIES = {'Natural': 0.5, 'Relaxed': 0.25, 'Expressive': 0.85, 'Energetic': 1.2}


def number(value, lo, hi, label):
    value = float(value)
    if not math.isfinite(value) or not lo <= value <= hi:
        raise ValueError(f'{label} must be between {lo} and {hi}')
    return value


def voices():
    vs = _vs_sound()
    references = vs.reference_voices()
    return [{'name': name, 'sample': True} for name in references] + ([{'name': vs.PIPER, 'sample': os.path.isfile(_sample_path(vs.PIPER))}] if vs.status().get('piper') else [])


def _sample_path(voice):
    return os.path.join(paths.sub('voice-previews'), hashlib.sha256(voice.encode()).hexdigest()[:16] + '.wav')


def sample_path(voice):
    vs = _vs_sound()
    if voice in vs.reference_voices():
        return vs.reference_voices()[voice]
    if voice != vs.PIPER or not os.path.isfile(_sample_path(voice)):
        raise ValueError('No sample is available for this voice yet')
    return _sample_path(voice)


def sample(voice):
    vs = _vs_sound()
    if voice in vs.reference_voices() or os.path.isfile(_sample_path(voice)):
        return {'state': 'done', 'voice': voice}
    if voice != vs.PIPER:
        raise ValueError('Choose an installed voice')
    def run(task):
        with tempfile.TemporaryDirectory(prefix='voice-sample-', dir=paths.sub('voice-previews')) as work:
            # The sample text is stored as Unicode escapes; UI text lives in translation files.
            text = '\u05e9\u05dc\u05d5\u05dd, \u05d6\u05d5 \u05d3\u05d5\u05d2\u05de\u05d4 \u05e9\u05dc \u05d4\u05e7\u05d5\u05dc \u05e9\u05dc\u05d9.'
            target = os.path.join(work, 'sample.wav')
            _on_card(lambda: vs.make_speech(_Task(task, work), text, 'Hebrew', voice, target))
            os.replace(target, _sample_path(voice))
        return {'voice': voice, 'progress': 1, 'text': 'Voice sample ready'}
    return frames._task('voice-sample', run)


def speech(name, text, language, voice, seconds, delivery='Natural', expressive=None, seed=0):
    if not (text or '').strip():
        raise ValueError('Write what the character says')
    seconds = number(seconds, 1 / 120, 240, 'Selected duration')
    expressive = number(DELIVERIES.get(delivery, 0.5) if expressive is None else expressive, 0, 2, 'Expression')
    vs = _vs_sound()
    if voice not in [v['name'] for v in voices()]:
        raise ValueError('Choose an installed voice')
    if voice == vs.PIPER and language != 'Hebrew':
        raise ValueError('The Piper voice speaks Hebrew only')
    return frames._task('lip-speech', _speech, name, text.strip(), language, voice, seconds, expressive, int(seed), delivery)


def _speech(task, name, text, language, voice, seconds, expressive, seed, delivery):
    vs = _vs_sound()
    folder = os.path.join(projects._folder(name), 'sound', 'made')
    os.makedirs(folder, exist_ok=True)
    raw = os.path.join(folder, 'voice-raw-' + uuid.uuid4().hex[:10] + '.wav')
    with tempfile.TemporaryDirectory(prefix='speech-', dir=os.path.dirname(folder)) as work:
        task['text'] = 'Making speech only; lip sync has not started'
        _on_card(lambda: vs.make_speech(_Task(task, work), text, language, voice, raw, exaggeration=expressive, seed=seed))
    web = projects.add_media(name, raw)
    # Keep the exact raw take: pitch, speed, EQ and gain can be changed repeatedly without regenerating it.
    provenance = {'text': text, 'language': language, 'voice': voice, 'delivery': delivery, 'expressive': expressive, 'seed': seed}
    projects._write(raw + '.speech.json', json.dumps(provenance, ensure_ascii=False, indent=1))
    return {'raw': web, 'raw_seconds': media.probe(raw)['duration'], 'selected_seconds': seconds,
            'settings': provenance, 'progress': 1, 'text': 'Speech ready. Listen and tune it before generating lips.'}


def _tempo(speed):
    filters = []
    while speed > 2.0:
        filters.append('atempo=2'); speed /= 2
    while speed < 0.5:
        filters.append('atempo=0.5'); speed /= 0.5
    return filters + [f'atempo={speed:.9f}']


def prepare(name, web, seconds, speed=1, pitch=0, tone=0, gain=0, fit=True):
    seconds = number(seconds, 1 / 120, 240, 'Selected duration')
    speed = number(speed, 0.5, 3, 'Speed')
    pitch = number(pitch, -12, 12, 'Pitch')
    tone = number(tone, -1, 1, 'Tone')
    gain = number(gain, -12, 12, 'Volume')
    source = projects.media_path(name, web)
    if library.kind_of(source) != 'audio':
        raise ValueError('Choose a speech audio file')
    duration = media.probe(source).get('duration') or 0
    required = duration / seconds
    effective = max(speed, required) if fit else speed
    if duration / effective > seconds + 0.01:
        raise ValueError(f'The words need {duration / effective:.2f} seconds at this speed. Select more time or enable automatic fitting.')
    if effective > 8:
        raise ValueError('The selection is too short for all the words. Select more time or shorten the text.')
    return frames._task('tune-speech', _prepare, name, web, seconds, speed, pitch, tone, gain, bool(fit), effective, duration)


def _prepare(task, name, web, seconds, speed, pitch, tone, gain, fit, effective, duration):
    import subprocess
    ratio = 2 ** (pitch / 12)
    filters = ['aresample=48000', f'asetrate={48000 * ratio:.6f}', 'aresample=48000'] + _tempo(1 / ratio) + _tempo(effective)
    filters += [f'bass=g={-tone * 6:.3f}', f'treble=g={tone * 6:.3f}', f'volume={gain:.3f}dB',
                f'apad,atrim=duration={seconds:.9f},asetpts=PTS-STARTPTS']
    folder = os.path.join(projects._folder(name), 'sound', 'made')
    os.makedirs(folder, exist_ok=True)
    out = os.path.join(folder, 'voice-tuned-' + uuid.uuid4().hex[:10] + '.wav')
    task['text'] = 'Tuning the approved speech; lip sync has not started'
    subprocess.run([library.ffmpeg(), '-v', 'error', '-y', '-i', projects.media_path(name, web), '-af', ','.join(filters),
                    '-ar', '48000', '-ac', '1', '-c:a', 'pcm_s16le', out], capture_output=True, check=True, timeout=180,
                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    settings = {'raw': web, 'seconds': seconds, 'speed': speed, 'effective_speed': effective, 'pitch': pitch,
                'tone': tone, 'gain': gain, 'fit': fit, 'speech_seconds': duration / effective}
    projects._write(out + '.speech.json', json.dumps(settings, indent=1))
    return dict(settings, web=projects.add_media(name, out), progress=1, text=f'Speech ready: {seconds:g} seconds, speed {effective:.2f}x')


def edit(name, web, seconds, segments):
    """Compile the local speech timeline. The same WAV is auditioned and passed to MuseTalk."""
    seconds = number(seconds, 1 / 120, 240, 'Shot duration')
    source = projects.media_path(name, web)
    if library.kind_of(source) != 'audio':
        raise ValueError('Choose speech audio')
    duration = number(media.probe(source).get('duration') or 0, .001, 600, 'Speech duration')
    if not isinstance(segments, list) or not 1 <= len(segments) <= 128:
        raise ValueError('Keep between 1 and 128 speech pieces')
    clean = []
    for part in segments:
        at = number(part.get('start'), 0, seconds, 'Speech position')
        cin = number(part.get('in'), 0, duration, 'Speech trim')
        length = number(part.get('duration'), .001, seconds, 'Speech piece duration')
        if at + length > seconds + 1 / 48000 or cin + length > duration + .001:
            raise ValueError('Speech pieces must fit inside the shot and recording')
        clean.append({'start': at, 'in': cin, 'duration': length})
    clean.sort(key=lambda p: p['start'])
    if any(a['start'] + a['duration'] > b['start'] + 1 / 48000 for a, b in zip(clean, clean[1:])):
        raise ValueError('Speech pieces cannot overlap')
    key = hashlib.sha256(json.dumps([media._key(source), seconds, clean], sort_keys=True).encode()).hexdigest()[:20]
    folder = os.path.join(projects._folder(name), 'sound', 'made')
    os.makedirs(folder, exist_ok=True)
    target = os.path.join(folder, f'voice-edit-{key}.wav')
    if os.path.isfile(target):
        return {'state': 'done', 'web': projects.add_media(name, target), 'seconds': seconds, 'segments': clean}
    return frames._task('edit-speech', _edit, name, source, target, seconds, clean)


def _edit(task, name, source, target, seconds, segments):
    import subprocess
    import wave
    import numpy as np
    rate = 48000
    limit = max(p['in'] + p['duration'] for p in segments)
    pcm = subprocess.run([library.ffmpeg(), '-v', 'error', '-i', source, '-t', f'{limit:.9f}',
                          '-ac', '1', '-ar', str(rate), '-f', 'f32le', '-'], capture_output=True, check=True, timeout=120,
                         creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).stdout
    recording = np.frombuffer(pcm, np.float32)
    mixed = np.zeros(round(seconds * rate), dtype=np.float32)
    for part in segments:
        at, cin, length = (round(part[k] * rate) for k in ('start', 'in', 'duration'))
        length = min(length, len(recording) - cin, len(mixed) - at)
        if length <= 0:
            raise ValueError('The speech recording ends before this piece')
        piece = recording[cin:cin + length].copy()
        fade = min(round(.003 * rate), length // 2)
        if fade:
            piece[:fade] *= np.linspace(0, 1, fade)
            piece[-fade:] *= np.linspace(1, 0, fade)
        mixed[at:at + length] = piece
    # Unique temporary output makes parallel identical preview requests safe.
    temporary = target + '.' + uuid.uuid4().hex[:8] + '.tmp'
    try:
        with wave.open(temporary, 'wb') as wav:
            wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(rate)
            wav.writeframes((np.clip(mixed, -1, 1) * 32767).astype('<i2').tobytes())
        os.replace(temporary, target)
    finally:
        if os.path.isfile(temporary):
            os.remove(temporary)
    return {'web': projects.add_media(name, target), 'seconds': seconds, 'segments': segments,
            'progress': 1, 'text': 'Edited speech ready'}

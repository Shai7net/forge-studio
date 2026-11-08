"""Speaks with Kokoro. This file is not part of Forge: it is run by Kokoro's own small Python (speech.py
starts it), so nothing Kokoro needs is installed into Forge's environment.

    python kokoro_say.py <model.onnx> <voices.bin> list
    python kokoro_say.py <model.onnx> <voices.bin> say <voice> <speed> <text file> <out.wav>
"""
import json
import sys


def main():
    from kokoro_onnx import Kokoro
    kokoro = Kokoro(sys.argv[1], sys.argv[2])
    if sys.argv[3] == "list":
        print("VOICES " + json.dumps(sorted(kokoro.get_voices())))
        return 0
    voice, speed, text_file, out = sys.argv[4], float(sys.argv[5]), sys.argv[6], sys.argv[7]
    with open(text_file, encoding="utf8") as file:
        text = file.read().strip()
    language = {"a": "en-us", "b": "en-gb", "e": "es", "f": "fr-fr", "h": "hi", "i": "it", "j": "ja", "p": "pt-br", "z": "cmn"}.get(voice[:1], "en-us")
    samples, rate = kokoro.create(text, voice=voice, speed=speed, lang=language)
    import soundfile
    soundfile.write(out, samples, rate, subtype="PCM_16")
    print(f"SPOKEN {len(samples) / rate:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

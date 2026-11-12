"""Shapes a mouth to words with MuseTalk. This file is not part of Forge: it is run by MuseTalk's own Python
(talk.py starts it), so nothing MuseTalk needs is installed into Forge's environment.

    python musetalk_say.py <MuseTalk's app folder> <video> <sound> <video out> --ffmpeg <ffmpeg.exe> --work <folder>
                           [--full] [--batch 4]

The video is a face that already moves (LivePortrait's), the sound is what it says; the video that comes out is
the same frames, in the same order, with the lower half of the face drawn again by MuseTalk 1.5 for each frame's own moment of the sound (a frame in which no face is found is passed on as it is). It is
MuseTalk's own scripts/inference.py with three differences: the frames stay in memory and go to ffmpeg through
a pipe (no command lines put together from file names, which break on a space), everything is read and
written in a working folder whose name is plain letters (OpenCV cannot open a file under a Hebrew name on
Windows), and a frame in which no face is found is passed on as it is.

Lines it writes for whoever started it:   PROGRESS 0.42      DONE <frames> <faces found>      FAILED <why>
"""
import argparse
import os
import shutil
import subprocess
import sys


def say(*words):
    print(*words, flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("app")
    parser.add_argument("video")
    parser.add_argument("sound")
    parser.add_argument("out")
    parser.add_argument("--ffmpeg", required=True)
    parser.add_argument("--work", required=True)
    parser.add_argument("--full", action="store_true", help="32-bit numbers: a little better, twice the graphics memory")
    parser.add_argument("--batch", type=int, default=4)
    parser.add_argument("--margin", type=int, default=10, help="pixels below the chin that are drawn again")
    parser.add_argument("--blend", default="jaw", choices=["jaw", "raw", "neck"])
    a = parser.parse_args()

    app = os.path.abspath(a.app)
    work = os.path.abspath(a.work)
    os.makedirs(work, exist_ok=True)
    video, sound = os.path.join(work, "in.mp4"), os.path.join(work, "in.wav")
    shutil.copyfile(a.video, video)
    # (the sound as plain 16 kHz mono, whatever kind of file it came as)
    done = subprocess.run([a.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", a.sound, "-vn", "-ac", "1", "-ar", "16000", sound],
                          capture_output=True, stdin=subprocess.DEVNULL)
    if done.returncode != 0 or not os.path.isfile(sound):
        say("FAILED the sound could not be read:", done.stderr.decode("utf8", "replace")[-300:])
        return 2

    os.chdir(app)                               # MuseTalk names its files from its own folder
    sys.path.insert(0, app)
    import copy

    import cv2
    import numpy as np
    import torch
    from transformers import WhisperModel

    say("PROGRESS 0.02")
    from musetalk.utils.audio_processor import AudioProcessor
    from musetalk.utils.blending import get_image
    from musetalk.utils.face_parsing import FaceParsing
    from musetalk.utils.utils import datagen, load_all_model
    from musetalk.utils import preprocessing            # (loads the pose model and the face detector)

    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
    half = device.type == "cuda" and not a.full

    # ---- the frames ----
    reader = cv2.VideoCapture(video)
    fps = reader.get(cv2.CAP_PROP_FPS) or 25.0
    frames = []
    while True:
        ok, frame = reader.read()
        if not ok:
            break
        frames.append(frame)
    reader.release()
    if len(frames) < 2:
        say("FAILED the video could not be read")
        return 2
    height, width = frames[0].shape[:2]
    say("PROGRESS 0.06")

    # ---- where the face is in each frame (MuseTalk's own way: body landmarks, then the face detector) ----
    stills = os.path.join(work, "frames")
    os.makedirs(stills, exist_ok=True)
    names = []
    for index, frame in enumerate(frames):
        name = os.path.join(stills, f"{index:08d}.png")
        cv2.imwrite(name, frame)
        names.append(name)
    boxes, frames = preprocessing.get_landmark_and_bbox(names, 0)
    found = sum(1 for box in boxes if box != preprocessing.coord_placeholder)
    if not found:
        say("FAILED no face was found in the video")
        return 3
    say("PROGRESS 0.30")

    # ---- the models ----
    vae, unet, pe = load_all_model(unet_model_path=os.path.join("models", "musetalkV15", "unet.pth"), vae_type="sd-vae",
                                   unet_config=os.path.join("models", "musetalkV15", "musetalk.json"), device=device)
    if half:
        pe, vae.vae, unet.model = pe.half(), vae.vae.half(), unet.model.half()
    pe, vae.vae, unet.model = pe.to(device), vae.vae.to(device), unet.model.to(device)
    timesteps = torch.tensor([0], device=device)
    whisper_dir = os.path.join("models", "whisper")
    audio = AudioProcessor(feature_extractor_path=whisper_dir)
    kind = unet.model.dtype
    whisper = WhisperModel.from_pretrained(whisper_dir).to(device=device, dtype=kind).eval()
    whisper.requires_grad_(False)
    parser_of_faces = FaceParsing(left_cheek_width=90, right_cheek_width=90)
    say("PROGRESS 0.42")

    with torch.no_grad():
        features, length = audio.get_audio_feature(sound)
        chunks = audio.get_whisper_chunk(features, device, kind, whisper, length, fps=fps, audio_padding_length_left=2, audio_padding_length_right=2)
        # each frame's face, as the model sees it (a frame with no face is passed on as it is, in its place)
        if not len(chunks):
            raise RuntimeError("the speech produced no audio features")
        # Whisper rounds the audio down to whole frames. Keep the video's final frame as well (rounding away
        # one frame cut the end of a 7.53 s line at 32 fps); only that tiny audio remainder uses the last chunk.
        total = len(frames)
        latents, with_face = [], []
        for index in range(total):
            box, frame = boxes[index], frames[index]
            if box == preprocessing.coord_placeholder:
                continue
            x1, y1, x2, y2 = box
            y2 = min(y2 + a.margin, frame.shape[0])
            crop = cv2.resize(frame[y1:y2, x1:x2], (256, 256), interpolation=cv2.INTER_LANCZOS4)
            latents.append(vae.get_latents_for_unet(crop))
            with_face.append(index)
        say("PROGRESS 0.50")
        # the mouths: every frame that has a face gets the mouth of its own moment of the sound
        mouths = {}
        size = max(1, a.batch)
        for start in range(0, len(with_face), size):
            some = with_face[start:start + size]
            said = pe(torch.stack([chunks[min(i, len(chunks) - 1)] for i in some]).to(device))
            latent_batch = torch.cat(latents[start:start + size], dim=0).to(device=device, dtype=kind)
            predicted = unet.model(latent_batch, timesteps, encoder_hidden_states=said).sample
            for index, mouth in zip(some, vae.decode_latents(predicted)):
                mouths[index] = mouth
            say(f"PROGRESS {0.50 + 0.30 * (start + len(some)) / max(1, len(with_face)):.3f}")

    # ---- the mouths back into their frames, and out ----
    command =[a.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{width}x{height}", "-r", f"{fps:g}", "-i", "-", "-an",
               "-vf", "scale=out_color_matrix=bt601:out_range=tv,format=yuv420p", "-c:v", "libx264", "-crf", "14", "-preset", "medium",
               "-colorspace", "smpte170m", "-color_primaries", "smpte170m", "-color_trc", "smpte170m", "-movflags", "+faststart", os.path.join(work, "out.mp4")]
    writer = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    written = 0
    try:
        for index in range(total):
            picture = frames[index]
            if index in mouths:
                x1, y1, x2, y2 = boxes[index]
                y2 = min(y2 + a.margin, picture.shape[0])
                try:
                    face = cv2.resize(mouths[index].astype(np.uint8), (x2 - x1, y2 - y1))
                    picture = get_image(copy.deepcopy(picture), face, [x1, y1, x2, y2], mode=a.blend, fp=parser_of_faces)
                except Exception as e:                      # one frame that cannot be put together stays as it was
                    print("a frame was left as it was:", e, file=sys.stderr)
            writer.stdin.write(np.ascontiguousarray(picture[:, :, :3]).tobytes())
            written += 1
            if index % 10 == 0:
                say(f"PROGRESS {0.80 + 0.19 * (index + 1) / max(1, total):.3f}")
    finally:
        try:
            writer.stdin.close()
        except OSError:
            pass
    complaint = writer.stderr.read().decode("utf8", "replace")
    if writer.wait() != 0 or written < 2:
        say("FAILED the video could not be written:", complaint[-300:])
        return 4
    shutil.move(os.path.join(work, "out.mp4"), a.out)
    say(f"DONE {written} {len(mouths)}")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except Exception as e:                                  # (said in one line: the caller shows it to the user)
        import traceback
        traceback.print_exc()
        say(f"FAILED {type(e).__name__}: {e}")
        code = 1
    sys.exit(code)

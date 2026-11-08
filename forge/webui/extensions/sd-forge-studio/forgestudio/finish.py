"""After an export: more frames, a bigger picture, HDR. A process of its own run with Forge's Python (PyTorch with
CUDA and NVIDIA's `nvidia-vfx`, as Forge's RTX upscaler uses it): `python finish.py job.json`, progress as lines
`@@FS {json}`. sdr2hdr needs Python 3.11, so it runs with Video Studio's engine Python (`hdr_python`).

In this order, frames streamed through ffmpeg pipes so a long edit needs no more memory than a short one:
1. frames x2 or x4: RIFE 4.6 (Deforum's code and weights, models/Deforum/RIFE46.pkl) draws the frames between
   each pair, at any point in time, so x4 is three frames at 1/4, 1/2, 3/4; FILM (Deforum's TorchScript model, the
   one Video Studio and AnimateDiff use) is the other choice: finer on big motion, but at 1080 x 1920 it filled the
   8 GB card and crawled (2026-11-02), where RIFE needs about a gigabyte;
2. a bigger picture: RTX Video Super Resolution (NVIDIA's own video upscaler, `nvvfx`), any factor up to 4, made
   for video frames (5-40 ms a frame on a 3070 Laptop); or Lanczos without AI;
3. HDR10: sdr2hdr (github.com/jpneagle/sdr2hdr, its own TorchScript model, kept in its own folder and called as is)
   turns the result into HEVC Main 10 with BT.2020 / PQ and HDR10 metadata.
The export's sound is put back at the end, untouched.
"""

import json
import os
import subprocess
import sys
import time

CREATE = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def emit(stage, progress, text=""):
    sys.stdout.write("@@FS " + json.dumps({"stage": stage, "progress": round(progress, 4), "text": text}) + "\n")
    sys.stdout.flush()


def probe(ffmpeg, path):
    probe_exe = os.path.join(os.path.dirname(ffmpeg), "ffprobe" + (".exe" if os.name == "nt" else ""))
    r = subprocess.run([probe_exe, "-v", "error", "-select_streams", "v:0", "-count_packets", "-show_entries",
                        "stream=width,height,avg_frame_rate,nb_read_packets", "-of", "json", path],
                       capture_output=True, creationflags=CREATE)
    s = json.loads(r.stdout)["streams"][0]
    num, _, den = s["avg_frame_rate"].partition("/")
    return int(s["width"]), int(s["height"]), float(num) / float(den or 1), int(s.get("nb_read_packets") or 0)


class Film:
    """FILM between two frames (uint8 HxWx3 on the card), padded to a multiple of 64 as the model needs."""

    def __init__(self, path, h, w):
        import torch
        self.torch = torch
        self.model = torch.jit.load(path, map_location="cpu").half().cuda().eval()
        self.h, self.w = h, w
        self.ph, self.pw = (64 - h % 64) % 64, (64 - w % 64) % 64
        self.dt = torch.full((1, 1), 0.5, device="cuda", dtype=torch.float16)

    def prep(self, f):
        import torch.nn.functional as F
        x = f.permute(2, 0, 1).unsqueeze(0).half() / 255.0
        return F.pad(x, (self.pw // 2, self.pw - self.pw // 2, self.ph // 2, self.ph - self.ph // 2), mode="replicate")

    def between(self, a, b):
        with self.torch.inference_mode():
            mid = self.model(self.prep(a), self.prep(b), self.dt).clamp(0, 1)
            mid = mid[0, :, self.ph // 2:self.ph // 2 + self.h, self.pw // 2:self.pw // 2 + self.w]
            return (mid.permute(1, 2, 0).float() * 255).round().byte()


class Rife:
    """RIFE 4.6 between two frames at time t (Deforum's IFNet_HDv3 and its weights)."""

    def __init__(self, weights, deforum_scripts, h, w):
        import torch
        if deforum_scripts not in sys.path:
            sys.path.insert(0, deforum_scripts)
        from deforum_helpers.src.rife.rife_new_gen.IFNet_HDv3 import IFNet
        self.torch = torch
        net = IFNet()
        state = torch.load(weights, map_location="cpu")
        net.load_state_dict({k.replace("module.", ""): v for k, v in state.items() if "module." in k}, False)
        self.net = net.cuda().eval()
        self.h, self.w = h, w
        self.ph, self.pw = (64 - h % 64) % 64, (64 - w % 64) % 64
        # RIFE looks at the motion at 1/8 .. 1/1 of the frame; on a big frame from half the size up (its own advice)
        self.scales = [4, 2, 1, 0.5] if max(h, w) > 2000 else [8, 4, 2, 1]

    def prep(self, f):
        import torch.nn.functional as F
        x = f.permute(2, 0, 1).unsqueeze(0).float() / 255.0
        return F.pad(x, (0, self.pw, 0, self.ph))

    def between(self, a, b, t=0.5):
        with self.torch.inference_mode():
            imgs = self.torch.cat((self.prep(a), self.prep(b)), 1)
            merged = self.net(imgs, t, self.scales)[2][3]
            return (merged[0, :, :self.h, :self.w].clamp(0, 1).permute(1, 2, 0) * 255).round().byte()


class Rtx:
    """RTX Video Super Resolution to exactly (width, height) (multiples of 8)."""

    def __init__(self, width, height, mode):
        from nvvfx import VideoSuperRes
        self.effect = VideoSuperRes(quality=getattr(VideoSuperRes.QualityLevel, mode))
        self.effect.output_width, self.effect.output_height = width, height
        self.effect.load()

    def __call__(self, frame):
        import torch
        with torch.inference_mode():
            x = frame.permute(2, 0, 1).float().div(255).contiguous()
            out = torch.from_dlpack(self.effect.run(x).image).clone()
            return out.clamp(0, 1).mul(255).round().byte().permute(1, 2, 0).contiguous()


def frames_and_size(job, ffmpeg):
    src = job["input"]
    w, h, fps, count = probe(ffmpeg, src)
    times = int(job.get("fps_x") or 1)
    up = job.get("upscale") or None
    ow, oh = w, h
    if up:
        scale = float(up.get("scale", 2))
        ow, oh = int(w * scale) // 8 * 8, int(h * scale) // 8 * 8
    return src, w, h, fps, count, times, up, ow, oh


def picture_pass(job, ffmpeg, out_path, encoder):
    """FILM and the upscale over every frame, encoded with `encoder` (a list of ffmpeg arguments) into out_path."""
    import torch
    src, w, h, fps, count, times, up, ow, oh = frames_and_size(job, ffmpeg)
    film = None
    if times > 1:
        film = Film(job["film_model"], h, w) if job.get("interpolator") == "film" else             Rife(job["rife_model"], job["deforum_scripts"], h, w)
    rtx = Rtx(ow, oh, up.get("mode", "HIGHBITRATE_ULTRA")) if up and up.get("engine", "rtx") == "rtx" else None
    scale_filter = [] if rtx or (ow, oh) == (w, h) else ["-vf", f"scale={ow}:{oh}:flags=lanczos"]
    dec = subprocess.Popen([ffmpeg, "-v", "error", "-i", src, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                           stdout=subprocess.PIPE, creationflags=CREATE)
    fw, fh = (ow, oh) if rtx else (w, h)
    enc = subprocess.Popen([ffmpeg, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{fw}x{fh}",
                            "-r", f"{fps * times:.6f}", "-i", "-"] + scale_filter + encoder + [out_path],
                           stdin=subprocess.PIPE, creationflags=CREATE)
    size = w * h * 3
    total = max(1, count)
    prev, n, t0 = None, 0, time.time()

    def write(frame):
        if rtx is not None:
            frame = rtx(frame)
        enc.stdin.write(frame.cpu().numpy().tobytes())

    try:
        while True:
            raw = dec.stdout.read(size)
            if len(raw) < size:
                break
            cur = torch.frombuffer(bytearray(raw), dtype=torch.uint8).view(h, w, 3).cuda()
            if prev is not None and film is not None:
                if times >= 4 and isinstance(film, Rife):
                    for t in (0.25, 0.5, 0.75):
                        write(film.between(prev, cur, t))
                elif times >= 4:
                    mid = film.between(prev, cur)
                    write(film.between(prev, mid))
                    write(mid)
                    write(film.between(mid, cur))
                else:
                    write(film.between(prev, cur))
            write(cur)
            prev, n = cur, n + 1
            if n % 10 == 0:
                rate = n / max(0.01, time.time() - t0)
                emit("picture", n / total, f"{n} of {total} frames ({rate:.1f} a second)")
    finally:
        enc.stdin.close()
        enc.wait()
        dec.wait()
    if enc.returncode != 0 or not os.path.isfile(out_path):
        raise RuntimeError("encoding the bigger / smoother picture failed")


def hdr_pass(job, src, out_path):
    hdr = job["hdr"]
    home = job["sdr2hdr"]
    model = os.path.join(home, "models", hdr.get("model") or "enhancement_model_reuse_v1.pt")
    env = dict(os.environ, PYTHONPATH=os.path.join(home, "src"), PYTHONIOENCODING="utf-8")
    cmd = [job.get("hdr_python") or sys.executable, "-u", "-m", "sdr2hdr.cli", src, out_path, "--model-path", model,
           "--encoder", "hevc_nvenc", "--input-eotf", hdr.get("eotf", "srgb"), "--hdr-style", hdr.get("style", "natural"),
           "--tone", hdr.get("tone", "vivid"), "--ai-strength", str(hdr.get("strength", 0.25)), "--preset", hdr.get("preset", "portrait")]
    proc = subprocess.Popen(cmd, cwd=home, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace", creationflags=CREATE)
    tail = []
    for line in proc.stdout:
        line = line.strip()
        tail = (tail + [line])[-12:]
        if "/" in line and "frames" in line:
            try:
                done, total = line.split(" ")[0].split("/")
                emit("hdr", int(done) / max(1, int(total)), line)
            except ValueError:
                pass
    proc.wait()
    if proc.returncode != 0 or not os.path.isfile(out_path):
        raise RuntimeError("HDR (sdr2hdr) failed: " + " | ".join(tail)[-500:])


def cap_memory():
    """Out of memory as an error, not Windows lending system RAM to the card (then a frame takes many seconds)."""
    import torch
    free, total = torch.cuda.mem_get_info()
    torch.cuda.set_per_process_memory_fraction(max(0.2, min(1.0, (free - 300 * 1024 ** 2) / total)))


def main():
    with open(sys.argv[1], encoding="utf-8") as f:
        job = json.load(f)
    ffmpeg = job["ffmpeg"]
    work = job["work"]
    os.makedirs(work, exist_ok=True)
    current = job["input"]
    picture = (job.get("fps_x") or 1) > 1 or job.get("upscale")
    if picture:
        cap_memory()
        staged = os.path.join(work, "picture.mp4" if job.get("hdr") else "picture" + os.path.splitext(job["output"])[1])
        # before HDR: nearly lossless, 10 bits, HEVC (H.264 on NVENC stops at 4096 pixels a side)
        encoder = ["-c:v", "hevc_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "14", "-b:v", "0", "-pix_fmt", "p010le"] \
            if job.get("hdr") else job["encoder"]
        picture_pass(job, ffmpeg, staged, encoder)
        current = staged
    if job.get("hdr"):
        staged = os.path.join(work, "hdr.mp4")
        hdr_pass(job, current, staged)
        current = staged
    emit("sound", 0.99, "putting the sound back")
    r = subprocess.run([ffmpeg, "-v", "error", "-y", "-i", current, "-i", job["input"], "-map", "0:v", "-map", "1:a?",
                        "-c:v", "copy", "-c:a", "copy"] +
                       (["-movflags", "+faststart"] if job["output"].lower().endswith((".mp4", ".mov")) else []) +
                       [job["output"]], capture_output=True, creationflags=CREATE)
    if r.returncode != 0 or not os.path.isfile(job["output"]):
        raise RuntimeError("putting the sound back failed: " + r.stderr.decode(errors="replace")[-400:])
    emit("done", 1.0, job["output"])


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001 - reported to the export
        sys.stdout.write("@@FS " + json.dumps({"stage": "error", "progress": 0, "text": f"{type(e).__name__}: {e}"}) + "\n")
        sys.stdout.flush()
        sys.exit(2)

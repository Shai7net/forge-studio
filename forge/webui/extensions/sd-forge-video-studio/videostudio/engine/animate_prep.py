"""Character replacement, part 1: what Wan 2.2 Animate needs from the driving video, made before the model loads.

Follows the official replacement preprocessing (Wan-Video/Wan2.2 wan/modules/animate/preprocess, Apache-2.0):
  - frames at the job's frame rate, scaled to the job's size
  - one person's 133 whole-body keypoints per frame (DWPose here: the same COCO-WholeBody layout as the official
    ViTPose), tracked from the person chosen on the first frame
  - the skeleton picture (the official AAPose drawing) and a 512 x 512 face crop per frame
  - the person's mask (SAM 2.1 prompted with the person's body points on key frames), grown by a 7 x 7 dilation
    three times and filled to a w_len x h_len grid (1 x 1, the official setting, is the whole box), and the
    background = the frame with the mask blacked out
The official tool is for one-person videos; here the person is chosen and tracked, and the other people's bodies
are given to SAM as points that are not the person.
"""

import os
import subprocess

import cv2
import numpy as np
import torch

from .animate_draw import aug_mask, draw_pose, face_box

BODY_POINTS = [0, 1, 2, 5, 8, 11, 10, 13]  # nose, neck, shoulders, hips, ankles in Wan's 20-point body order
SAM_WINDOW = 200  # frames tracked per SAM session (the official uses 400; 200 keeps the session on an 8 GB card)


def ffmpeg_exe():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


def video_size(path):
    cap = cv2.VideoCapture(path)
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    if not w or not h:
        raise RuntimeError(f"Cannot read the video {path}")
    return w, h, fps, count


def read_frames(path, width, height, fps, start=0.0, seconds=None):
    """RGB uint8 [N, H, W, 3] at `fps`, scaled to width x height."""
    cmd = [ffmpeg_exe(), "-loglevel", "error"]
    if start:
        cmd += ["-ss", f"{float(start):.3f}"]
    cmd += ["-i", path]
    if seconds:
        cmd += ["-t", f"{float(seconds):.3f}"]
    cmd += ["-vf", f"fps={fps},scale={width}:{height}:flags=lanczos", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    frames = np.frombuffer(raw, dtype=np.uint8).reshape(-1, height, width, 3)
    if not len(frames):
        raise RuntimeError("The video gave no frames (check the start time).")
    return frames


class Pose:
    """DWPose with onnxruntime: CUDA when it loads, else the CPU."""

    def __init__(self, det_path, pose_path, gpu=True):
        import onnxruntime as ort
        providers = ["CPUExecutionProvider"]
        if gpu and "CUDAExecutionProvider" in ort.get_available_providers():
            providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        opts = ort.SessionOptions()
        opts.log_severity_level = 3
        self.det = ort.InferenceSession(det_path, opts, providers=providers)
        self.pose = ort.InferenceSession(pose_path, opts, providers=providers)
        self.on_gpu = "CUDAExecutionProvider" in self.det.get_providers()

    def __call__(self, frame):
        """Every person in an RGB frame: boxes [n, 4] (x1, y1, x2, y2) and keypoints [n, 133, 3] in pixels."""
        from .dwpose.cv_ox_det import inference_detector
        from .dwpose.cv_ox_pose import inference_pose
        boxes = inference_detector(self.det, frame)
        if boxes is None or len(boxes) == 0:
            return np.zeros((0, 4)), np.zeros((0, 133, 3))
        kps, scores = inference_pose(self.pose, boxes, frame)
        return np.asarray(boxes, dtype=np.float32), np.concatenate([kps, scores[..., None]], axis=-1)


def people(pose, frame):
    """People on a frame, numbered left to right: [(box, keypoints)]."""
    boxes, kps = pose(frame)
    order = np.argsort((boxes[:, 0] + boxes[:, 2]) / 2) if len(boxes) else []
    return [(boxes[i], kps[i]) for i in order]


def mark_people(frame, found):
    """The frame with each person's box and number drawn on it (for choosing who is replaced)."""
    out = frame.copy()
    for i, (box, _) in enumerate(found):
        x1, y1, x2, y2 = [int(v) for v in box]
        cv2.rectangle(out, (x1, y1), (x2, y2), (255, 210, 0), 3)
        size = max(0.8, (y2 - y1) / 250)
        cv2.putText(out, str(i + 1), (x1 + 6, y1 + int(34 * size)), cv2.FONT_HERSHEY_SIMPLEX, size, (0, 0, 0), 6)
        cv2.putText(out, str(i + 1), (x1 + 6, y1 + int(34 * size)), cv2.FONT_HERSHEY_SIMPLEX, size, (255, 210, 0), 2)
    return out


def detect_all(pose, frames, report):
    """Everyone on every frame: [(boxes, keypoints)]."""
    found = []
    for n, frame in enumerate(frames):
        found.append(pose(frame))
        if n % 8 == 0:
            report("prepare", 0.05 + 0.3 * n / len(frames), note=f"finding the people: frame {n + 1}/{len(frames)}")
    return found


def first_choice(found, pick):
    """The chosen person on the first frame (numbered from the left) and everyone else there."""
    boxes, kps = found[0]
    if not len(boxes):
        raise RuntimeError("No person was found on the first frame of the video.")
    order = list(np.argsort((boxes[:, 0] + boxes[:, 2]) / 2))
    chosen = order[min(max(0, int(pick)), len(order) - 1)]
    return kps[chosen], [kps[i] for i in order if i != chosen]


def follow(found, cut, start_kps, threshold):
    """The person's keypoints on every frame: the detection whose body points lie inside SAM's cut of them.
    Boxes cannot tell two dancers apart when they cross (the box overlap jumped to the partner, 2026-10-25); SAM
    follows the person by how they look. A frame where nobody fits keeps the last pose."""
    h, w = cut.shape[1:3]
    chosen, others, lost, last = [], [], 0, start_kps
    for (boxes, kps), m in zip(found, cut):
        best, best_score = None, 0.3  # share of the body points inside the cut
        for i, k in enumerate(kps):
            body = k[:17]
            ok = body[:, 2] >= threshold
            if ok.sum() < 3:
                continue
            xs = np.clip(body[ok, 0].astype(int), 0, w - 1)
            ys = np.clip(body[ok, 1].astype(int), 0, h - 1)
            score = float(m[ys, xs].mean())
            if score > best_score:
                best, best_score = i, score
        if best is None:
            lost += 1
        else:
            last = kps[best]
        chosen.append(last.copy())
        others.append([kps[i] for i in range(len(kps)) if i != best])
    return np.stack(chosen), others, lost


def faces(frames, kps):
    """512 x 512 face crops; a frame whose face points are weak (turned away) keeps the last good box."""
    h, w = frames.shape[1:3]
    out = np.zeros((len(frames), 512, 512, 3), np.uint8)
    last = None
    for i, (frame, k) in enumerate(zip(frames, kps)):
        if k[23:91, 2].mean() > 0.3 or last is None:
            box = face_box(k, 1.3, h, w)
            if box[1] - box[0] > 4 and box[3] - box[2] > 4:
                last = box
        if last is None:
            continue
        x1, x2, y1, y2 = last
        out[i] = cv2.resize(frame[y1:y2, x1:x2], (512, 512))
    return out


def skeletons(kps, height, width, threshold):
    out = np.zeros((len(kps), height, width, 3), np.uint8)
    for i, k in enumerate(kps):
        draw_pose(out[i], k, threshold)
    return out


def _body_points(k, threshold, w, h):
    from .animate_draw import split_kp2ds_for_aa
    body = split_kp2ds_for_aa(k)[0]
    pts = [body[i] for i in BODY_POINTS]
    return [[float(p[0]), float(p[1])] for p in pts if p[2] >= threshold and 0 <= p[0] < w and 0 <= p[1] < h]


def masks(frames, kps, others, sam_dir, device, threshold, report):
    """SAM 2.1 masks of one person through the video: prompted with their body points on the first frame (and the
    other people's as 'not this'), then tracked by SAM itself; each later window starts on the previous one's last
    frame, prompted with its mask."""
    from transformers import Sam2VideoModel, Sam2VideoProcessor
    n, h, w = frames.shape[:3]
    dtype = torch.bfloat16 if device.type == "cuda" else torch.float32
    model = Sam2VideoModel.from_pretrained(sam_dir, dtype=dtype).to(device).eval()
    processor = Sam2VideoProcessor.from_pretrained(sam_dir)
    out = np.zeros((n, h, w), np.uint8)
    start = 0
    while True:
        part = frames[start:start + SAM_WINDOW]
        session = processor.init_video_session(video=list(part), inference_device=device, video_storage_device="cpu",
                                               dtype=dtype)
        if start == 0:
            pos = _body_points(kps, threshold, w, h)
            if not pos:
                raise RuntimeError("The chosen person's body is not clear on the first frame: try another start time.")
            neg = [p for other in others for p in _body_points(other, threshold, w, h)]
            processor.add_inputs_to_inference_session(inference_session=session, frame_idx=0, obj_ids=1,
                                                      input_points=[[pos + neg]],
                                                      input_labels=[[[1] * len(pos) + [0] * len(neg)]])
        else:
            processor.add_inputs_to_inference_session(inference_session=session, frame_idx=0, obj_ids=1,
                                                      input_masks=out[start] > 0)
        model(inference_session=session, frame_idx=0)
        for output in model.propagate_in_video_iterator(session, start_frame_idx=0):
            m = processor.post_process_masks([output.pred_masks], original_sizes=[[h, w]], binarize=True)[0]
            out[start + output.frame_idx] = m[0, 0].cpu().numpy().astype(np.uint8)
            if output.frame_idx % 8 == 0:
                report("prepare", 0.35 + 0.5 * (start + output.frame_idx) / n,
                       note=f"following the person: frame {start + output.frame_idx + 1}/{n}")
        del session
        if start + len(part) >= n:
            break
        start += len(part) - 1
    del model
    if device.type == "cuda":
        torch.cuda.empty_cache()
    return out


def cut_out(image, pose, sam_dir, device, pick=0, background=255, margin=0.06):
    """One person from a picture of several: SAM's cut of person `pick` (numbered from the left) on a plain
    background, cropped to them with a margin. Wan Animate takes ONE character per picture: given two men in one
    picture it blended them (2026-10-25). Returns (RGB uint8, number of people in the picture)."""
    frame = np.asarray(image.convert("RGB"))
    found = people(pose, frame)
    if not found:
        return frame, 0
    pick = min(max(0, int(pick)), len(found) - 1)
    others = [k for i, (_, k) in enumerate(found) if i != pick]
    mask = masks(frame[None], found[pick][1], others, sam_dir, device, 0.3, lambda *a, **k: None)[0]
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))  # small holes where two people touch
    mask = cv2.dilate(mask, np.ones((3, 3), np.uint8))
    out = np.where(mask[..., None] > 0, frame, np.uint8(background))
    ys, xs = np.nonzero(mask)
    if len(xs):
        h, w = mask.shape
        mx, my = int((xs.max() - xs.min()) * margin) + 4, int((ys.max() - ys.min()) * margin) + 4
        out = out[max(0, ys.min() - my):min(h, ys.max() + my + 1), max(0, xs.min() - mx):min(w, xs.max() + mx + 1)]
    return np.ascontiguousarray(out), len(found)


def character_picture(job, device, report):
    """The character picture the model gets: as given when it shows one person, else the chosen person
    (`character_person`, numbered from the left) cut out onto white."""
    from PIL import Image
    path = job["character_image"]
    with Image.open(path) as im:
        image = im.convert("RGB")
    pose = Pose(job["pose_detector"], job["pose_model"], gpu=device.type == "cuda")
    count = len(people(pose, np.asarray(image)))
    pick = job.get("character_person")
    if count <= 1 and not pick:
        return path
    report("prepare", 0.0, note=f"the picture shows {count} people: cutting out number {int(pick or 0) + 1}")
    out, _ = cut_out(image, pose, job["sam_model"], device, int(pick or 0))
    target = os.path.join(job.get("work_dir") or job["output_dir"], "character-cut.png")
    Image.fromarray(out).save(target)
    if device.type == "cuda":
        torch.cuda.empty_cache()
    return target


def grow(mask, iterations=3, k=7, w_len=1, h_len=1):
    if iterations > 0:
        mask = cv2.dilate(mask, np.ones((k, k), np.uint8), iterations=iterations)
    return aug_mask(mask, w_len, h_len)


def preview(path, frames, pose, mask, fps):
    """Side by side for checking the preparation: the video with the mask tinted, and the skeleton."""
    tint = frames.copy()
    tint[mask > 0] = (tint[mask > 0] * 0.45 + np.array([255, 60, 60]) * 0.55).astype(np.uint8)
    both = np.concatenate([tint, np.maximum(pose, (frames * 0.25).astype(np.uint8))], axis=2)
    h, w = both.shape[1:3]
    cmd = [ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
           "-r", str(fps), "-i", "-", "-c:v", "libx264", "-crf", "23", "-pix_fmt", "yuv420p", path]
    subprocess.run(cmd, input=both.tobytes(), capture_output=True, check=True)


def prepare(job, device, report):
    """Returns frames, pose pictures, face crops, background and mask (all uint8, N frames) for the job."""
    width, height, fps = int(job["width"]), int(job["height"]), int(job.get("fps", 16))
    report("prepare", 0.0, note="reading the video")
    frames = read_frames(job["driving_video"], width, height, fps, job.get("start", 0.0), job.get("seconds"))
    threshold = float(job.get("pose_threshold", 0.4))
    pose = Pose(job["pose_detector"], job["pose_model"], gpu=device.type == "cuda")
    found = detect_all(pose, frames, report)
    del pose
    start_kps, start_others = first_choice(found, int(job.get("person", 0)))
    raw = masks(frames, start_kps, start_others, job["sam_model"], device, threshold, report)
    kps, _, lost = follow(found, raw, start_kps, threshold)
    report("prepare", 0.9, note=f"{len(frames)} frames; the person's pose was not found on {lost}")
    face = faces(frames, kps)
    skel = skeletons(kps, height, width, threshold)
    shape = job.get("mask_shape", "box")
    w_len, h_len = (1, 1) if shape == "box" else (10, 20)
    grown = np.stack([grow(m, int(job.get("mask_grow", 3)), 7, w_len, h_len) for m in raw])
    # Wan's VAE packs four frames into one latent frame, so a person who moves fast (a skirt in a turn) leaves parts of
    # themselves in the background latent of the neighbouring frames, which the model drew as white smoke
    # (2026-10-25). Each frame's area covers its neighbours' too.
    reach = int(job.get("mask_time", 2))
    if reach > 0:
        grown = np.stack([grown[max(0, i - reach):i + reach + 1].max(axis=0) for i in range(len(grown))])
    bg = frames * (1 - grown[..., None])
    if job.get("work_dir"):
        path = os.path.join(job["work_dir"], "prepared.mp4")
        try:
            preview(path, frames, skel, grown, fps)
            report("prepare", 1.0, note="prepared", prepared=path)
        except Exception as e:
            report("prepare", 1.0, note=f"prepared (no preview: {e})")
    return {"frames": frames, "pose": skel, "face": face, "bg": bg, "mask": grown, "fps": fps, "lost": lost}

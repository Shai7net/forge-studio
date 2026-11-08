"""
Keeping restored faces steady from one video frame to the next.

GFPGAN and CodeFormer restore every frame on its own. What they draw (pores, lashes, the edge of the teeth) comes
out a little different each time, and a face the detector finds in one frame and misses in the next is restored
in one and not in the other, so in a video restored faces shimmer and pop. This works in the 512x512 square a
face is aligned to before it is restored, where a face stays in place while the head moves across the picture:

1. The square is steadied. The detector's five points jitter by a pixel or so, which on a small face moves the
   square by several of its own pixels, and the restoration follows. The square's place, size and angle are
   filtered over time: firmly while the face is nearly still, hardly at all while it moves fast.
2. What the restoration added to the face is carried over from the frame before wherever the face itself did
   not change, and taken fresh wherever it did (a blink, a turn, a new expression).
3. A face is not dropped for a frame. Forge takes a face only when the detector is 97% sure of it, and in a
   moving video a face dips under that for a frame or two all the time, which shows as a flash of the blurred
   face. A face that is already being followed is kept down to TRACKED_CONFIDENCE.
4. A restoration comes and goes gradually. A face that appears is restored more with every frame it is found in.
   A face the detector loses is not restored in that frame; what was added before stays on, fading, wherever
   the square still shows what it showed, and a single frame's false find never shows at full strength.

GFPGAN also draws with new random noise on every call; modules/gfpgan_model.py switches that off while a
steadier is set.

One steadier serves one video: modules/postprocessing_video.py sets face_restoration_utils.steadier for its run.
"""
import math

import cv2
import numpy as np
import torch

FOUND_CONFIDENCE = 0.97     # the detector's confidence from which a face is taken: what Forge uses for pictures
TRACKED_CONFIDENCE = 0.8    # ... and from which a face that was there in the frame before is kept
ODD_RESTORATION = 2.0       # a restoration this many times further from its face than that face's usual is not used
MATCH_DISTANCE = 0.5        # a face in the next frame is the same face within this many face sizes
FADE_SECONDS = 0.2          # a restoration reaches full strength, and fades out, over this long

# The 1 euro filter of the square: the cutoff (Hz) at rest, and how much it rises per unit of speed.
# Place is in face sizes, size in its logarithm, angle in radians.
REST_CUTOFF = 1.0
SPEED_GAIN = 12.0
SPEED_CUTOFF = 1.0

# Carrying the added detail: how much of the last frame's is kept where the face is still, and the change in
# the face itself (in 1/255, smoothed over MOVED_BLUR pixels of the square) from which less and then none is.
# A small face in fast motion changes more than that in every frame, so there the detail is each frame's own.
# Carrying it anyway was tried (allowing a small face four times the change): on a dance video it took 39% of
# the flicker and 28% of the restoration with it, because detail that differs in every frame averages to blur.
CARRY = 0.8
STILL = 2.0
MOVED = 9.0
MOVED_BLUR = 5


def similarity(source: np.ndarray, target: np.ndarray):
    """The turn, scale and shift that take the source points closest to the target points (least squares over
    all of them), as (scale * cos, scale * sin, shift x, shift y). facexlib fits its squares with a method that
    picks a subset of the five points, and which subset can change from frame to frame."""
    source_middle, target_middle = source.mean(axis=0), target.mean(axis=0)
    s, t = source - source_middle, target - target_middle
    norm = (s ** 2).sum()
    cos = (s * t).sum() / norm
    sin = (s[:, 0] * t[:, 1] - s[:, 1] * t[:, 0]).sum() / norm
    return cos, sin, target_middle[0] - (cos * source_middle[0] - sin * source_middle[1]), target_middle[1] - (sin * source_middle[0] + cos * source_middle[1])


class OneEuro:
    """The 1 euro filter (Casiez, Roussel and Vogel, 2012): strong smoothing while a value is nearly still, where
    jitter shows, and little while it moves fast, where lag would show."""

    def __init__(self, rate: float):
        self.rate = rate
        self.value = self.speed = None

    def weight(self, cutoff):
        return 1.0 / (1.0 + self.rate / (2 * math.pi * cutoff))

    def __call__(self, value: np.ndarray) -> np.ndarray:
        if self.value is None:
            self.value, self.speed = value, np.zeros_like(value)
            return value
        self.speed = self.speed + self.weight(SPEED_CUTOFF) * ((value - self.value) * self.rate - self.speed)
        self.value = self.value + self.weight(REST_CUTOFF + SPEED_GAIN * np.abs(self.speed)) * (value - self.value)
        return self.value


class Track:
    """One face followed through the frames."""

    def __init__(self, rate: float, unit: float, strength: float):
        self.filter = OneEuro(rate)
        self.unit = unit                    # the face's size when first found: the unit its place is filtered in
        self.middle = None                  # where the face is in the picture, after filtering
        self.found = True                   # whether the detector found it in this frame
        self.strength = strength            # how much of its restoration shows, 0 to 1
        self.face = self.detail = None      # the aligned face of the last frame, and what its restoration added
        self.usual = None                   # how far its restorations usually are from the face they were made from


class FaceSteadier:
    def __init__(self, fps: float):
        self.fps = float(fps)
        self.step = 1.0 / max(1.0, self.fps * FADE_SECONDS)     # the change of a face's strength in one frame
        self.tracks = {}        # per face helper (GFPGAN and CodeFormer each have their own): the faces being followed
        self.started = set()    # the helpers that have seen their first frame
        self.current = {}       # per face helper: the tracks of this frame's squares, in the helper's order

    def square(self, face_helper, track: Track, pose: np.ndarray):
        """Adds the square at this place, size and angle to the helper's faces of this frame."""
        side = face_helper.face_size[0]
        x, y, log_size, angle = pose
        track.middle = np.array([x * track.unit, y * track.unit])
        scale = math.exp(log_size) / side
        cos, sin = scale * math.cos(angle), scale * math.sin(angle)
        to_picture = np.array([[cos, -sin, track.middle[0] - (cos - sin) * side / 2],
                               [sin, cos, track.middle[1] - (sin + cos) * side / 2]])
        affine = cv2.invertAffineTransform(to_picture)
        face_helper.affine_matrices.append(affine)
        face_helper.cropped_faces.append(cv2.warpAffine(
            face_helper.input_img, affine, face_helper.face_size, borderMode=cv2.BORDER_CONSTANT, borderValue=(135, 133, 132)))
        self.current[id(face_helper)].append(track)

    def find_faces(self, face_helper, resize: int, eye_dist_threshold: float):
        """In place of face_helper.get_face_landmarks_5(resize=..., eye_dist_threshold=...) and align_warp_face():
        the squares of this frame's faces, steadied; and after them the squares of the faces that were there a
        moment ago and that the detector did not find now."""
        key = id(face_helper)
        tracks = self.tracks.setdefault(key, [])
        first_frame = key not in self.started
        self.started.add(key)
        side = face_helper.face_size[0]
        self.current[key] = []
        lost = list(tracks)

        height, width = face_helper.input_img.shape[:2]
        shrink = min(height, width) / resize
        small = cv2.resize(face_helper.input_img, (int(width / shrink), int(height / shrink)), interpolation=cv2.INTER_LANCZOS4)
        with torch.no_grad():
            found = face_helper.face_det.detect_faces(small, TRACKED_CONFIDENCE)
        for box in sorted(found, key=lambda box: -box[4]):
            landmarks = box[5:15].reshape(5, 2).astype(np.float64) * shrink
            if np.linalg.norm(landmarks[0] - landmarks[1]) < eye_dist_threshold:
                continue
            # the square as this frame's points put it: from the template's pixels to the picture's
            cos, sin, shift_x, shift_y = similarity(face_helper.face_template, landmarks)
            size = math.hypot(cos, sin) * side
            angle = math.atan2(sin, cos)
            middle = np.array([(cos - sin) * side / 2 + shift_x, (sin + cos) * side / 2 + shift_y])

            near = [track for track in lost if np.linalg.norm(track.middle - middle) < MATCH_DISTANCE * size]
            if near:
                track = min(near, key=lambda track: np.linalg.norm(track.middle - middle))
                lost.remove(track)
                angle += 2 * math.pi * round((track.filter.value[3] - angle) / (2 * math.pi))      # on from where it was, not around the circle
            elif box[4] >= FOUND_CONFIDENCE:
                track = Track(self.fps, size, strength=1.0 if first_frame else 0.0)     # a face there from the start is not faded in
                tracks.append(track)
            else:
                continue
            face_helper.all_landmarks_5.append(landmarks)
            face_helper.det_faces.append(box[0:5] * shrink)
            track.found = True
            track.strength = min(1.0, track.strength + self.step)
            self.square(face_helper, track, track.filter(np.array([middle[0] / track.unit, middle[1] / track.unit, math.log(size), angle])))

        for track in lost:
            track.found = False
            track.strength -= self.step
            if track.strength <= 0 or track.detail is None:
                tracks.remove(track)
                continue
            # where it would be now, going on at the speed it had
            pose = track.filter.value.copy()
            pose[:2] += track.filter.speed[:2] / self.fps
            track.filter.value = pose
            self.square(face_helper, track, pose)

    def is_lost(self, face_helper, index: int) -> bool:
        """Whether this square is of a face the detector did not find in this frame: it is not to be restored."""
        return not self.current[id(face_helper)][index].found

    def changed(self, track: Track, face: np.ndarray) -> np.ndarray:
        """Per pixel of the square, 0 where the face is as it was in the frame before, 1 where it changed."""
        change = cv2.GaussianBlur(np.abs(face - track.face).mean(axis=2), (0, 0), MOVED_BLUR)
        return np.clip((change - STILL) / (MOVED - STILL), 0, 1)[:, :, None]

    def steady(self, face_helper, index: int, face: np.ndarray, restored: np.ndarray) -> np.ndarray:
        """The restored face (uint8, like `face`: the aligned square before restoration) with the detail of the
        frame before kept wherever the face did not change."""
        track = self.current[id(face_helper)][index]
        detail = restored.astype(np.float32) - face.astype(np.float32)
        amount = float(np.abs(detail).mean())
        if track.usual is not None and amount > ODD_RESTORATION * track.usual:
            # a face half behind a hand or another head comes back as someone else's, or as a blotch
            return self.carry(face_helper, index, face)
        track.usual = amount if track.usual is None else track.usual + 0.2 * (amount - track.usual)

        face = face.astype(np.float32)
        if track.detail is not None:
            detail += CARRY * (1 - self.changed(track, face)) * (track.detail - detail)
        track.face, track.detail = face, detail
        return np.clip(face + detail * track.strength + 0.5, 0, 255).astype(np.uint8)

    def carry(self, face_helper, index: int, face: np.ndarray) -> np.ndarray:
        """For a face that is not restored in this frame (the detector did not find it, or its restoration came
        out odd): the square as it is, with what the restoration added before, wherever the square still shows
        what it showed then."""
        track = self.current[id(face_helper)][index]
        if track.detail is None:
            return face
        face = face.astype(np.float32)
        track.detail = track.detail * (1 - self.changed(track, face))
        track.face = face
        return np.clip(face + track.detail * track.strength + 0.5, 0, 255).astype(np.uint8)

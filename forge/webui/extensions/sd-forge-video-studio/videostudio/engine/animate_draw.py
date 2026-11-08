"""The skeleton picture Wan 2.2 Animate was trained on, drawn from 133 COCO-WholeBody keypoints.

Copied from Wan-Video/Wan2.2 wan/modules/animate/preprocess (human_visualization.py draw_aapose_new /
draw_handpose_new and pose2d_utils.py split_kp2ds_for_aa, Apache-2.0); only matplotlib's hsv_to_rgb was swapped
for colorsys, which gives the same colours.
"""

import colorsys
import math

import cv2
import numpy as np

LIMBS = [[2, 3], [2, 6], [3, 4], [4, 5], [6, 7], [7, 8], [2, 9], [9, 10], [10, 11], [2, 12], [12, 13], [13, 14],
         [2, 1], [1, 15], [15, 17], [1, 16], [16, 18], [14, 19], [11, 20]]
COLORS = [[255, 0, 0], [255, 85, 0], [255, 170, 0], [255, 255, 0], [170, 255, 0], [85, 255, 0], [0, 255, 0],
          [0, 255, 85], [0, 255, 170], [0, 255, 255], [0, 170, 255], [0, 85, 255], [0, 0, 255], [85, 0, 255],
          [170, 0, 255], [255, 0, 255], [255, 0, 170], [255, 0, 85], [200, 200, 0], [100, 100, 0]]
HAND_EDGES = [[0, 1], [1, 2], [2, 3], [3, 4], [0, 5], [5, 6], [6, 7], [7, 8], [0, 9], [9, 10], [10, 11], [11, 12],
              [0, 13], [13, 14], [14, 15], [15, 16], [0, 17], [17, 18], [18, 19], [19, 20]]


def split_kp2ds_for_aa(kp2ds):
    """133 x 3 (x, y, score) -> body (20, Wan's own order with neck and toes), left hand, right hand, face."""
    body = (kp2ds[[0, 6, 6, 8, 10, 5, 7, 9, 12, 14, 16, 11, 13, 15, 2, 1, 4, 3, 17, 20]] +
            kp2ds[[0, 5, 6, 8, 10, 5, 7, 9, 12, 14, 16, 11, 13, 15, 2, 1, 4, 3, 18, 21]]) / 2
    return body.copy(), kp2ds[91:112].copy(), kp2ds[112:133].copy(), kp2ds[22:91].copy()


def draw_hand(canvas, keypoints, threshold):
    eps = 0.01
    h, w = canvas.shape[:2]
    stick = max(max(int(min(h, w) / 200) - 1, 1) // 2, 1)
    for ie, (e1, e2) in enumerate(HAND_EDGES):
        k1, k2 = keypoints[e1], keypoints[e2]
        if k1[2] < threshold or k2[2] < threshold:
            continue
        x1, y1, x2, y2 = int(k1[0]), int(k1[1]), int(k2[0]), int(k2[1])
        if x1 > eps and y1 > eps and x2 > eps and y2 > eps:
            color = [c * 255 for c in colorsys.hsv_to_rgb(ie / float(len(HAND_EDGES)), 1.0, 1.0)]
            cv2.line(canvas, (x1, y1), (x2, y2), color, thickness=stick)
    for k in keypoints:
        if k[2] < threshold:
            continue
        x, y = int(k[0]), int(k[1])
        if x > eps and y > eps:
            cv2.circle(canvas, (x, y), stick, (0, 0, 255), thickness=-1)
    return canvas


def draw_pose(canvas, kp133, threshold=0.5):
    """kp133: [133, 3] in pixels with scores. Draws body, hands and head on `canvas` (H x W x 3 uint8, RGB)."""
    body, lhand, rhand, _ = split_kp2ds_for_aa(kp133)
    h, w = canvas.shape[:2]
    stick = max(int(min(h, w) / 200) - 1, 1)
    for (a, b), color in zip(LIMBS, COLORS):
        k1, k2 = body[a - 1], body[b - 1]
        if k1[-1] < threshold or k2[-1] < threshold:
            continue
        ys, xs = np.array([k1[0], k2[0]]), np.array([k1[1], k2[1]])
        length = ((xs[0] - xs[1]) ** 2 + (ys[0] - ys[1]) ** 2) ** 0.5
        angle = math.degrees(math.atan2(xs[0] - xs[1], ys[0] - ys[1]))
        polygon = cv2.ellipse2Poly((int(np.mean(ys)), int(np.mean(xs))), (int(length / 2), stick), int(angle), 0, 360, 1)
        cv2.fillConvexPoly(canvas, polygon, [int(float(c) * 0.6) for c in color])
    for k, color in zip(body, COLORS):
        if k[-1] < threshold:
            continue
        cv2.circle(canvas, (int(k[0]), int(k[1])), stick, color, thickness=-1)
    draw_hand(canvas, lhand, threshold)
    draw_hand(canvas, rhand, threshold)
    return canvas


def face_box(kp133, scale, height, width):
    """The official face crop: the 68 face points' box grown to `scale` times its area, more above than below.
    Returns x1, x2, y1, y2."""
    pts = kp133[23:91, :2]
    min_x, min_y = pts.min(axis=0)
    max_x, max_y = pts.max(axis=0)
    w0, h0 = max(max_x - min_x, 1.0), max(max_y - min_y, 1.0)
    area = w0 * h0 * scale
    nw, nh = math.sqrt(area * (w0 / h0)), math.sqrt(area * (h0 / w0))
    dw, dh = (nw - w0) / 2, (nh - h0) / 4
    return (int(max(min_x - dw, 0)), int(min(max_x + dw, width)),
            int(max(min_y - 3 * dh, 0)), int(min(max_y + dh, height)))


def aug_mask(mask, w_len, h_len):
    """The official get_aug_mask: every cell of a w_len x h_len grid over the person's box that touches the
    person becomes part of the mask (1 x 1 = the whole box)."""
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return mask
    x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
    ws, hs = max(1, int((x1 - x0) / w_len)), max(1, int((y1 - y0) / h_len))
    out = mask.copy()
    for xa in range(x0, x1, ws):
        xb = min(xa + ws, x1)
        for ya in range(y0, y1, hs):
            yb = min(ya + hs, y1)
            if mask[ya:yb, xa:xb].sum() > 0:
                out[ya:yb, xa:xb] = 1
    return out

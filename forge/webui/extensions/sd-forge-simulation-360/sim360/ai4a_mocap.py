"""Reads a motion capture file (BVH, FBX, GLB, or AI4Animation's NPZ) with AI4AnimationPy's importers and writes
it as a clip for Simulation 360. This file is not part of Forge: it is run by AI4AnimationPy's own Python.

    python ai4a_mocap.py <ai4animationpy folder> <motion file> <out.json> [--take N]

out: {"fps": 30, "start": 0, "names": [standard names], "frames": [[[x, y, z] of each joint] ...],
      "root": [[x, z, yaw] ...], "height", "seconds", "source", "skeleton": [the file's own joint names]}
      (root: where the hips are over the ground, in the first frame's body frame, and how far it has turned
      since then)

  * every skeleton's joints are given the standard names the page knows (Hips, Spine2, Neck, Head, LeftArm,
    LeftForeArm, LeftHand, LeftUpLeg, LeftLeg, LeftFoot, ... and the same on the right), whatever the file calls
    them (Mixamo's "mixamorig:", LaFan's, CMU's, 3ds Max's "Bip01 L Thigh", ...);
  * the frames are in the body's frame of the FIRST frame (its left +x, up +y, facing +z, in metres, the ground
    at y 0), with where the hips are over the ground taken out of every frame: the body moves in place, and
    turns as it turns. Where it goes is in "root" (x, z in the same frame; yaw in degrees, as the scene measures
    headings), for whoever wants it to go there too;
  * which way is up (y or z), the units (cm or m) and a mirrored world are found from the body itself;
  * a T-pose at the start (the capture's calibration, arms out) is skipped: the clip begins once the arms are down.
"""
import json
import math
import os
import re
import sys

import numpy as np

STANDARD = {
    "Hips": ["hips", "pelvis", "root", "bip01pelvis", "spinebase", "bip01"],
    "Spine2": ["spine2", "spine3", "chest", "spine1", "upperchest", "bip01spine2", "bip01spine1", "spinechest", "spine"],
    "Neck": ["neck", "neck0", "neck1", "bip01neck", "neckbase"],
    "Head": ["head", "bip01head", "headtop"],
    "*Arm": ["*arm", "*upperarm", "*shoulder2", "bip01*upperarm", "*uparm"],
    "*ForeArm": ["*forearm", "*lowerarm", "*elbow", "bip01*forearm", "*loarm"],
    "*Hand": ["*hand", "*wrist", "bip01*hand"],
    "*UpLeg": ["*upleg", "*thigh", "*upperleg", "*hip", "bip01*thigh", "*femur"],
    "*Leg": ["*leg", "*knee", "*shin", "*calf", "*lowerleg", "bip01*calf", "*tibia"],
    "*Foot": ["*foot", "*ankle", "*talocrural", "bip01*foot"],
    "*ToeBase": ["*toebase", "*toe", "*toes", "*ball", "bip01*toe0", "*toe0"],
}
SIDES = {"Left": ["left", "l", "l_", "lft"], "Right": ["right", "r", "r_", "rgt"]}


def plain(name):
    """A joint's name without its prefix ("mixamorig:", "Armature|", "Character1_") and without separators."""
    name = re.split(r"[:|]", str(name or ""))[-1]
    return re.sub(r"[^a-z0-9]", "", name.lower())


def standard_names(names):
    """{standard name: index in the file}"""
    found, keys = {}, [plain(n) for n in names]
    for want, aliases in STANDARD.items():
        sides = [""] if "*" not in want else ["Left", "Right"]
        for side in sides:
            target = want.replace("*", side)
            if target in found:
                continue
            # the name itself first; then a name that ends with it ("b_l_arm", "DEF-upper_arm.L" are prefixed)
            for exact in (True, False):
                for alias in aliases:
                    options = [alias] if not side else [alias.replace("*", s) for s in SIDES[side]]
                    if not exact:
                        options = [o for o in options if len(o) >= 4]
                    hit = next((i for i, k in enumerate(keys) if i not in found.values() and (k in options if exact else any(k.endswith(o) for o in options))), None)
                    if hit is not None:
                        found[target] = hit
                        break
                if target in found:
                    break
    return found


def calibration(left_hand, right_hand, left_foot, right_foot, height):
    """How many frames at the start of a take are its calibration: a T-pose (or an A-pose), the hands far apart
    and high, until the arms are down. 0 when it does not begin so, or when that would be most of the take.
    Each argument: [frames, 3] positions, y up; `height` in the same units."""
    span = np.linalg.norm((left_hand - right_hand)[:, [0, 2]], axis=-1)
    hands = np.minimum(left_hand[:, 1], right_hand[:, 1]) - np.minimum(left_foot[:, 1], right_foot[:, 1])
    spread = (span > 0.62 * height) & (hands > 0.5 * height)
    if len(spread) < 5 or not spread[:5].all() or spread.all():
        return 0
    down = np.nonzero(span < 0.45 * height)[0]
    down = down[down > np.argmin(spread)]
    return int(down[0]) if len(down) and down[0] < 0.4 * len(span) else 0


def main():
    home, source, out = (os.path.abspath(p) for p in sys.argv[1:4])
    sys.path.insert(0, home)
    from ai4animation import Motion
    from ai4animation.Math import Transform
    kind = os.path.splitext(source)[1].lower()
    if kind == ".bvh":
        motion = Motion.LoadFromBVH(source, scale=1.0)
    elif kind == ".fbx":
        try:
            motion = Motion.LoadFromFBX(source)
        except Exception:
            raise SystemExit("FAILED reading .fbx needs Autodesk's FBX SDK, which is not installed: save it as .glb or .bvh (Blender: File > Export)")
    elif kind in (".glb", ".gltf"):
        motion = Motion.LoadFromGLB(source)
    elif kind == ".npz":
        motion = Motion.LoadFromNPZ(source)
    else:
        raise SystemExit("FAILED this is not a motion file Simulation 360 can read (BVH, FBX, GLB, NPZ)")
    names = list(motion.Hierarchy.BoneNames)
    positions = np.asarray(Transform.GetPosition(motion.Frames), dtype=np.float64)        # [frames, joints, 3]
    if positions.ndim != 3 or positions.shape[0] < 2:
        raise SystemExit("FAILED the file has no motion in it (fewer than two frames)")
    index = standard_names(names)
    needed = ["Hips", "Spine2", "Neck", "Head", "LeftArm", "LeftForeArm", "LeftHand", "RightArm", "RightForeArm", "RightHand",
              "LeftUpLeg", "LeftLeg", "LeftFoot", "RightUpLeg", "RightLeg", "RightFoot"]
    missing = [n for n in needed if n not in index]
    if missing:
        raise SystemExit("FAILED a skeleton this cannot read: no " + ", ".join(missing) + " among " + ", ".join(str(n) for n in names[:40]))

    # 30 frames a second
    fps_in = float(motion.Framerate or 30)
    times = np.arange(0, (positions.shape[0] - 1) / fps_in + 1e-9, 1 / 30)
    at = times * fps_in
    lo = np.clip(np.floor(at).astype(int), 0, positions.shape[0] - 1)
    hi = np.clip(lo + 1, 0, positions.shape[0] - 1)
    u = (at - lo)[:, None, None]
    positions = positions[lo] * (1 - u) + positions[hi] * u

    P = lambda name: positions[:, index[name]]
    feet = (P("LeftFoot") + P("RightFoot")) / 2
    # which way is up: the axis along which the head is farthest from the feet in the first frame
    rise = P("Head")[0] - feet[0]
    axis = int(np.argmax(np.abs(rise)))
    if axis != 1:
        # z-up (or x-up): turned so that this axis is y, keeping the world right-handed
        sign = np.sign(rise[axis])
        if axis == 2:
            positions = np.stack([positions[..., 0], positions[..., 2] * sign, -positions[..., 1] * sign], axis=-1)
        else:
            positions = np.stack([-positions[..., 1] * sign, positions[..., 0] * sign, positions[..., 2]], axis=-1)
    elif rise[1] < 0:
        positions = positions * np.array([1, -1, -1])           # (upside down: turned half round about x)
    # metres: a body is about 1.7 m from feet to the top of its head
    height = float((P("Head")[0] - (P("LeftFoot")[0] + P("RightFoot")[0]) / 2)[1]) * 1.1
    scale = 1.0
    if height > 20:
        scale = 0.01            # centimetres
    elif height > 5:
        scale = 0.1             # (a game's decimetres, seldom)
    positions = positions * scale
    height *= scale
    # a take that begins in a T-pose (arms out for the capture's calibration) begins where the arms are down
    skipped = calibration(P("LeftHand"), P("RightHand"), P("LeftFoot"), P("RightFoot"), height)
    if skipped:
        positions = positions[skipped:]
    ground = min(float(P("LeftFoot")[:, 1].min()), float(P("RightFoot")[:, 1].min()), float(P("LeftToeBase")[:, 1].min()) if "LeftToeBase" in index else 1e9)
    positions[..., 1] -= ground - 0.03 * height                 # (the ankle joint is a little above the floor)

    # the body's frame of the first frame
    first = positions[0]
    across = first[index["LeftUpLeg"]] - first[index["RightUpLeg"]]
    across[1] = 0
    across /= np.linalg.norm(across) or 1
    forward = np.cross(across, [0.0, 1.0, 0.0])                # (left x up: facing, in a right-handed world)
    if "LeftToeBase" in index:
        toes = (P("LeftToeBase")[0] - P("LeftFoot")[0]) + (P("RightToeBase")[0] - P("RightFoot")[0])
        toes[1] = 0
        if float(np.dot(toes, forward)) < 0:
            forward = -forward                                  # a mirrored world: its left and its facing disagree
    origin = first[index["Hips"]].copy()
    origin[1] = 0
    frame = np.stack([across, [0.0, 1.0, 0.0], forward])       # rows: the new x, y, z
    local = (positions - origin) @ frame.T
    hips = local[:, index["Hips"]]
    root = np.stack([hips[:, 0], hips[:, 2]], axis=-1)
    local[..., 0] -= hips[:, None, 0]
    local[..., 2] -= hips[:, None, 2]
    # where it faces in each frame, as the scene measures headings (0: away from the first facing's left...)
    # how far it has turned since the first frame, in degrees, the way the scene's headings grow (its facing is
    # the hips' side-to-side line turned a quarter round: left x up)
    a = local[:, index["LeftUpLeg"]] - local[:, index["RightUpLeg"]]
    yaw = np.degrees(np.arctan2(-a[:, 2], -a[:, 0]))
    yaw = (yaw - yaw[0] + 180) % 360 - 180
    order = list(index)
    frames = np.round(local[:, [index[n] for n in order]], 4)
    with open(out, "w", encoding="utf8") as file:
        json.dump({"fps": 30, "start": 0, "names": order, "frames": frames.tolist(), "kind": "biped",
                   "root": [[round(float(x), 4), round(float(z), 4), round(float(y), 2)] for (x, z), y in zip(root, yaw)],
                   "height": round(height, 3), "seconds": round((len(frames) - 1) / 30, 3), "source": os.path.basename(source),
                   "skipped": round(skipped / 30, 2),
                   "skeleton": [str(n) for n in names]}, file, separators=(",", ":"))
    print(f"MADE {len(frames)} frames, {len(order)} joints, {height:.2f} m tall, {len(frames) / 30:.1f} s" + (f" (a T-pose of {skipped / 30:.1f} s skipped)" if skipped else ""), flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"FAILED {type(e).__name__}: {e}", flush=True)
        sys.exit(1)

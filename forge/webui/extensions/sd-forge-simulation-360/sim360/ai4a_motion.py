"""Walks a body along a path with AI4Animation's neural locomotion controller (github.com/facebookresearch/
ai4animationpy, CC BY-NC 4.0: for non-commercial use). This file is not part of Forge: it is run by
AI4AnimationPy's own Python, from the folder of the locomotion demo whose network it uses.

    python ai4a_motion.py <ai4animationpy folder> <request.json> <out.json>

request: {"kind": "biped" | "four", "style": "Neutral", "fps": 30, "path": [[t, x, z], ...], "acts": [[t, "sit"], ...]}
    The path is where the body should be at each second of the scene, on the ground, in metres (the scene's own
    map: right-handed, y up; the network's world is the same, so nothing is mirrored). Between
    the path's points the body walks, runs, slows, stops and starts as the speed asks; the network chooses its
    steps, its turns and when its feet touch the ground. `style` is one of the demo's guidances (Neutral, Zombie,
    Chicken, Dinosaur, OnHeels, BigSteps, ...). "four" is the dog of the MANN paper (Starke 2018): it walks, paces,
    trots and canters by its speed, and while it stands `acts` may make it sit, lie down or stand up ([[second,
    "sit" | "lie" | "stand" | ""], ...], from that second on).

out: {"fps", "start", "names", "frames": [[[x, y, z] for each joint] for each frame], "root": [[x, z, yaw] ...] (yaw as the
      scene measures headings: atan2(x, -z)),
      "contacts": [[left ankle, left ball, right ankle, right ball] ...]}
    Joint positions are in the body's own frame (x to its left, y up, z where it faces), so they say how it is
    held, not where it is: where it is stays the scene's business.
"""
import json
import math
import os
import sys

import numpy as np

KINDS = {"biped": ("Demos/Locomotion/Biped", "Geno"), "four": ("Demos/Locomotion/Quadruped", "Quadruped")}
WARM = 1.5                      # seconds of standing before the path begins, for the network to settle


def main():
    home, request, out = (os.path.abspath(p) for p in sys.argv[1:4])
    with open(request, encoding="utf8") as file:
        ask = json.load(file)
    demo, character = KINDS[ask.get("kind", "biped")]
    folder = os.path.join(home, demo)
    os.chdir(folder)
    sys.path[:0] = [home, folder, os.path.join(home, "Demos", "_ASSETS_", character)]

    import torch
    if not torch.cuda.is_available():               # (the networks were saved from a graphics card)
        loading = torch.load
        torch.load = lambda *a, **k: loading(*a, **{**k, "map_location": "cpu"})
    import Program as Demo                          # the demo's own program: its network, its IK, its sequences
    kind = ask.get("kind", "biped")
    if kind == "four":
        Demo.CHARACTER_MODELS = {"dog": "Dog.glb"}      # (the wolf it also names is not among its files)
    from ai4animation import AI4Animation, Tensor, Time, Transform, Vector3

    fps = int(ask.get("fps", 30))
    path = np.array(ask["path"], dtype=np.float64)
    path = path[np.argsort(path[:, 0])]
    start, end = float(path[0, 0]), float(path[-1, 0])
    acts = sorted((float(a), str(b)) for a, b in ask.get("acts") or [])

    def place(t):
        t = min(max(t, start), end)
        return np.array([np.interp(t, path[:, 0], path[:, 1]), np.interp(t, path[:, 0], path[:, 2])])

    class Nothing:                                  # (what the demo asks of a window it does not have here)
        def __getattr__(self, name):
            return self

        def __call__(self, *a, **k):
            return None

    class Walk(Demo.Program):
        def Start(self):
            for name in ("Standalone", "GUI", "Draw", "Color"):
                setattr(AI4Animation, name, Nothing())
            try:
                super().Start()
            finally:
                AI4Animation.Standalone = None

        def Control(self):
            t = start + Time.TotalTime - WARM
            h = 0.25
            v = (place(t + h) - place(t - h)) / (2 * h) if t > start - h else np.zeros(2)
            # The network keeps to the velocity it is given, not to places: left alone it drifts ahead of the
            # path or behind it. Where it is is compared with where the path is, and the difference is made up.
            sim = np.asarray(self.SimulationObject.GetPosition(0))[[0, 2]]
            if not hasattr(self, "Origin"):
                self.Origin = sim - place(start)
            if np.linalg.norm(v) > 0.1:
                error = (place(t) + self.Origin) - sim
                v = v + np.clip(error * 1.2, -0.6, 0.6)
            velocity = Vector3.Create(v[0], 0, v[1])     # (both worlds are right-handed with y up: no mirror)
            speed = Vector3.Length(velocity)
            act = ""
            for at, what in acts:
                if t >= at:
                    act = what
            if kind == "four":
                # the dog: its gait follows its speed; standing, it may sit, lie down or stand up. From standing
                # still, a slow walk keeps all four feet on the ground and the network never sets off: the first
                # moment of a start is asked faster, and then eased back to the path's own pace.
                if speed > 0.1:
                    self.Going = getattr(self, "Going", 0.0) + Time.DeltaTime
                    push = 1.0 + 1.6 * max(0.0, 1.0 - self.Going / 0.9)
                    velocity = velocity * push
                    speed = Vector3.Length(velocity)
                else:
                    self.Going = 0.0
                action = act if act in ("sit", "lie", "stand") and speed < 0.1 else ""
                if action:
                    velocity, direction = Vector3.Zero(), self.Actor.GetRootDirection()
                else:
                    direction = velocity
            else:
                direction = Vector3.Create(0, 0, 0)
            position = Vector3.Lerp(self.SimulationObject.GetPosition(0), self.Actor.GetRootPosition(), self.Synchronization)
            self.SimulationObject.Control(position, direction, velocity, Time.DeltaTime)
            speed = Vector3.Length(velocity)
            if kind == "four":
                modes = Demo.LOCOMOTION_MODES
                state = {"sit": "Sit", "lie": "Lie", "stand": "Stand"}.get(action) or ("Idle" if speed < 0.1 else "Walk" if speed < modes["pace"] else
                                                                                    "Pace" if speed < modes["trot"] else "Trot" if speed < modes["canter"] else "Canter")
                self.CurrentGuidanceState = state
                self.GuidanceControl.Positions = self.GuidanceTemplates[state].Positions.copy()
                self.RootControl.Transforms = self.SimulationObject.Transforms.copy()
                self.RootControl.Velocities = self.SimulationObject.Velocities.copy()
            else:
                self.GuidanceControl.Positions = self.GuidanceTemplates["Idle" if speed < 0.1 else self.SelectedGuidance].Positions.copy()
            if self.Sequence is not None:
                self.RootControl.Transforms = Transform.Interpolate(self.SimulationObject.Transforms, self.Sequence.Trajectory.Transforms, self.TrajectoryCorrection)
                for i in range(self.RootControl.SampleCount):
                    target = Transform.GetPosition(self.RootControl.Transforms)[i:]
                    current = self.Actor.GetRootPosition().reshape(-1, 3)
                    time = self.RootControl.Timestamps[i:].reshape(-1, 1)
                    self.RootControl.Velocities[i] = Tensor.Sum(target - current, axis=0, keepDim=False) / Tensor.Sum(time, axis=0, keepDim=False)
                self.RootControl.Velocities = Vector3.Lerp(self.RootControl.Velocities, self.Sequence.Trajectory.Velocities, self.TrajectoryCorrection)

    program = Walk()
    AI4Animation(program, mode=AI4Animation.Mode.MANUAL)
    style = ask.get("style") or ("Neutral" if kind == "biped" else "")
    if kind == "biped" and style in program.GuidanceNames:
        program._set_guidance(program.GuidanceNames.index(style))
    program.Previous = program.Sequence = None
    names = program.Actor.GetBoneNames()

    frames, roots, contacts = [], [], []
    dt = 1.0 / fps
    steps = int(round((WARM + end - start) * fps)) + 1
    for k in range(steps):
        AI4Animation.Update(dt)
        if Time.TotalTime < WARM - 1e-6:
            continue
        root = program.Actor.Root
        local = Transform.TransformationTo(program.Actor.GetTransforms(), root)
        positions = np.asarray(Transform.GetPosition(local), dtype=np.float64)
        frames.append(np.round(positions, 4).tolist())
        at, forward = np.asarray(Transform.GetPosition(root)), np.asarray(Transform.GetAxisZ(root))
        roots.append([round(float(at[0]), 4), round(float(at[2]), 4), round(math.degrees(math.atan2(float(forward[0]), -float(forward[2]))), 2)])
        c = program.Sequence.SampleContacts(0.0) if program.Sequence is not None and hasattr(program.Sequence, "SampleContacts") else np.zeros(4)
        if os.environ.get("AI4A_DEBUG") and k % 15 == 0:
            print(f"t={Time.TotalTime - WARM:5.2f} state={getattr(program, 'CurrentGuidanceState', '')} lock={float(np.asarray(program.Sequence.GetRootLock())):.2f} "
                  f"sim={np.round(np.asarray(program.SimulationObject.GetPosition(0))[[0, 2]], 2)} root={roots[-1][:2]} ts={float(np.asarray(program.Timescale)):.2f} sync={float(np.asarray(program.Synchronization)):.2f}", flush=True)
        contacts.append([round(float(x), 3) for x in np.asarray(c).reshape(-1)[:4]])
    with open(out, "w", encoding="utf8") as file:
        json.dump({"fps": fps, "start": start, "names": names, "frames": frames, "root": roots, "contacts": contacts, "style": style}, file)
    print(f"MADE {len(frames)} frames of {len(names)} joints, style {style}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

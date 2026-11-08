"""Motion capture files: which joint of a skeleton is which, and where a take begins. Runs without Forge and
without AI4AnimationPy:

    venv\Scripts\python.exe extensions\sd-forge-simulation-360\tests\test_mocap.py
"""
import os
import sys
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "sim360"))

import ai4a_mocap  # noqa: E402

NEEDED = ["Hips", "Spine2", "Neck", "Head", "LeftArm", "LeftForeArm", "LeftHand", "RightArm", "RightForeArm", "RightHand",
          "LeftUpLeg", "LeftLeg", "LeftFoot", "RightUpLeg", "RightLeg", "RightFoot"]


class JointNames(unittest.TestCase):
    def named(self, names):
        found = ai4a_mocap.standard_names(names)
        return {want: names[i] for want, i in found.items()}

    def test_mixamo(self):
        names = ["mixamorig:" + n for n in ["Hips", "Spine", "Spine1", "Spine2", "Neck", "Head", "HeadTop_End", "LeftShoulder", "LeftArm",
                                            "LeftForeArm", "LeftHand", "RightShoulder", "RightArm", "RightForeArm", "RightHand", "LeftUpLeg",
                                            "LeftLeg", "LeftFoot", "LeftToeBase", "RightUpLeg", "RightLeg", "RightFoot", "RightToeBase"]]
        got = self.named(names)
        for want in NEEDED + ["LeftToeBase", "RightToeBase"]:
            self.assertEqual(got[want], "mixamorig:" + want)

    def test_cranberry_prefixed_and_unnamed(self):
        names = ["b_root", "b_spine0", "b_spine1", "b_spine2", "b_spine3", "b_neck0", "b_head", "b_l_shoulder", "b_l_arm", "b_l_forearm",
                 "b_l_wrist", "b_r_shoulder", "b_r_arm", "b_r_forearm", "b_r_wrist", "b_l_upleg", "b_l_leg", "b_l_talocrural", "b_l_ball",
                 "b_r_upleg", "b_r_leg", "b_r_talocrural", "b_r_ball", None]
        got = self.named(names)
        self.assertEqual(got["Hips"], "b_root")
        self.assertEqual(got["LeftArm"], "b_l_arm")
        self.assertEqual(got["LeftForeArm"], "b_l_forearm")
        self.assertEqual(got["LeftHand"], "b_l_wrist")
        self.assertEqual(got["RightFoot"], "b_r_talocrural")
        self.assertEqual(got["Head"], "b_head")
        self.assertTrue(set(NEEDED) <= set(got))

    def test_3ds_max_biped(self):
        names = ["Bip01", "Bip01 Pelvis", "Bip01 Spine", "Bip01 Spine1", "Bip01 Spine2", "Bip01 Neck", "Bip01 Head", "Bip01 L Clavicle",
                 "Bip01 L UpperArm", "Bip01 L Forearm", "Bip01 L Hand", "Bip01 R Clavicle", "Bip01 R UpperArm", "Bip01 R Forearm",
                 "Bip01 R Hand", "Bip01 L Thigh", "Bip01 L Calf", "Bip01 L Foot", "Bip01 R Thigh", "Bip01 R Calf", "Bip01 R Foot"]
        got = self.named(names)
        self.assertEqual(got["Hips"], "Bip01 Pelvis")
        self.assertEqual(got["LeftUpLeg"], "Bip01 L Thigh")
        self.assertEqual(got["RightLeg"], "Bip01 R Calf")
        self.assertEqual(got["RightArm"], "Bip01 R UpperArm")
        self.assertTrue(set(NEEDED) <= set(got))

    def test_left_and_right_are_not_mixed(self):
        got = self.named(["Hips", "Spine2", "Neck", "Head", "RightArm", "LeftArm", "RightForeArm", "LeftForeArm", "RightHand", "LeftHand",
                          "RightUpLeg", "LeftUpLeg", "RightLeg", "LeftLeg", "RightFoot", "LeftFoot"])
        for want, name in got.items():
            self.assertEqual(want, name)


class Calibration(unittest.TestCase):
    def take(self, tpose_frames, frames=300):
        """hands 1.6 m apart at shoulder height for the T-pose, then 2 s of bringing them down, then by the hips"""
        t = np.arange(frames)
        out = np.clip((t - tpose_frames) / 60.0, 0, 1)             # 0 in the T-pose, 1 once they are down
        half = 0.8 * (1 - out) + 0.2 * out
        y = 1.4 * (1 - out) + 0.85 * out
        left = np.stack([half, y, np.zeros(frames)], axis=-1)
        right = np.stack([-half, y, np.zeros(frames)], axis=-1)
        feet = np.zeros((frames, 3))
        return left, right, feet, feet.copy()

    def test_a_tpose_at_the_start_is_left_out(self):
        skipped = ai4a_mocap.calibration(*self.take(45), height=1.7)
        self.assertGreater(skipped, 45)
        self.assertLess(skipped, 45 + 60)

    def test_a_take_without_one_is_whole(self):
        left, right, a, b = self.take(-100)
        self.assertEqual(ai4a_mocap.calibration(left, right, a, b, height=1.7), 0)

    def test_arms_out_all_the_way_is_not_a_calibration(self):
        left, right, a, b = self.take(10 ** 6)
        self.assertEqual(ai4a_mocap.calibration(left, right, a, b, height=1.7), 0)

    def test_most_of_the_take_is_never_cut(self):
        left, right, a, b = self.take(200, frames=300)
        self.assertEqual(ai4a_mocap.calibration(left, right, a, b, height=1.7), 0)


if __name__ == "__main__":
    unittest.main(verbosity=1)

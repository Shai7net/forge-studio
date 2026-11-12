"""Regression tests for character sheets and native ingredient layout."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image
import torch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from videostudio import sheets
from videostudio.engine import animate_prep


class CharacterSheet(unittest.TestCase):
    def test_sheet_uses_complete_front_without_group_segmentation(self):
        with tempfile.TemporaryDirectory() as directory:
            original = Path(directory) / 'sheet.png'
            Image.new('RGB', (100, 100), 'red').save(original)
            front = Image.new('RGB', (48, 80), 'purple')
            close = Image.new('RGB', (30, 40), 'gold')
            job = dict(character_image=str(original), work_dir=directory)
            events = []
            with patch.object(sheets, 'split', return_value=[('front', front), ('close-up', close)]), \
                 patch.object(animate_prep, 'Pose', side_effect=AssertionError('A sheet is not a group')), \
                 patch.object(animate_prep, 'cut_out', side_effect=AssertionError('Do not fragment the sheet')):
                result = animate_prep.character_picture(job, torch.device('cpu'), lambda *a, **kw: events.append(kw))
            with Image.open(result) as saved:
                self.assertEqual(saved.size, front.size)
                np.testing.assert_array_equal(np.asarray(saved), np.asarray(front))
            self.assertTrue(Path(job['character_face']).is_file())
            self.assertTrue(job['character_sheet'])
            self.assertIn('complete front', events[0]['note'])
            self.assertTrue(original.is_file())

    def test_holes_in_a_person_cut_are_filled(self):
        """SAM left a purple hoodie as a hole that Wan Animate drew as white cloth (2026-11-11): the cut is whole."""
        cut = np.zeros((40, 20), np.uint8)
        cut[5:35, 5:15] = 1
        cut[15:22, 8:12] = 0  # the hole
        with patch.object(sheets, 'matte_file', return_value=None):
            whole = animate_prep.whole_figure(Image.new('RGB', (20, 40), 'white'), cut)
        self.assertTrue(whole[15:22, 8:12].all())
        self.assertEqual(int(whole.sum()), 30 * 10)

    def test_single_person_picture_passes_through(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'portrait.png'
            Image.new('RGB', (32, 64)).save(path)
            with patch.object(sheets, 'split', return_value=[]), patch.object(animate_prep, 'Pose'), \
                 patch.object(animate_prep, 'people', return_value=[(None, None)]):
                result = animate_prep.character_picture(dict(character_image=str(path), pose_detector='d',
                    pose_model='p'), torch.device('cpu'), lambda *a, **kw: None)
            self.assertEqual(result, str(path))


if __name__ == '__main__':
    unittest.main()

import base64
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image
from forgestudio import framefiles, library, projectbin, projects


class ProjectMediaAndFrames(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.env=patch.dict(os.environ,{'FORGE_STUDIO_ROOT':str(self.root/'data')})
        self.env.start()
        projects.create('first');projects.create('second')
        self.source=self.root/'source.png'
        Image.new('RGBA',(80,48),(24,62,199,180)).save(self.source)
        self.image='data:image/png;base64,'+base64.b64encode(self.source.read_bytes()).decode()
    def tearDown(self):
        self.env.stop();self.tmp.cleanup()
    def test_new_project_is_empty_and_only_registered_files_are_visible(self):
        self.assertEqual(projectbin.listed('first')['total'],0)
        projects.add_media('second',str(self.source))
        with patch.object(library,'query',side_effect=AssertionError('global library must not be queried')):
            self.assertEqual(projectbin.listed('first')['items'],[])
            projects.add_media('first',str(self.source))
            result=projectbin.listed('first')
        self.assertEqual(result['total'],1)
        self.assertEqual(result['items'][0]['project'],'first')
        self.assertIn('name=first',result['items'][0]['url'])
        self.assertEqual(projectbin.listed('first',kind='video')['total'],0)
        self.assertEqual(projectbin.listed('first',text='absent')['total'],0)
        projects.store_upload('first','reference.first.png',self.source.read_bytes())
        self.assertEqual(projectbin.listed('first',kind='image')['total'],2)
    def test_equal_timestamps_paginate_without_dropping_files_and_missing_files_are_skipped(self):
        for n in range(6):projects.store_upload('first',f'frame-{n}.png',self.source.read_bytes())
        folder=projects._folder('first');studio=projects._studio(folder)
        for m in studio['media'].values():m['added']=123
        projects._save_studio(folder,studio)
        names=[i['web'] for offset in (0,2,4) for i in projectbin.listed('first',offset=offset,limit=2)['items']]
        self.assertEqual(len(set(names)),6)
        Path(projects.media_path('first',names[0])).unlink()
        self.assertEqual(projectbin.listed('first')['total'],5)
    def test_project_thumbnail_does_not_require_the_global_index(self):
        with patch.object(library,'get',side_effect=AssertionError('global index is not needed')):
            thumb=library.thumbnail_path(str(self.source))
        with Image.open(thumb) as image:self.assertEqual(image.size,(80,48))
    def test_save_as_writes_exact_pixels_only_at_the_chosen_location(self):
        target=self.root/'chosen.png'
        with patch.object(framefiles,'pick_path',return_value=str(target)):
            result=framefiles.export(self.image,'shot.png')
        self.assertTrue(result['saved'])
        with Image.open(target) as actual,Image.open(self.source) as expected:
            self.assertEqual(actual.tobytes(),expected.tobytes())
        self.assertEqual(projectbin.listed('first')['total'],0)
    def test_cancel_save_and_bad_input_have_no_side_effects(self):
        with patch.object(framefiles,'pick_path',return_value=''):
            self.assertEqual(framefiles.export(self.image),{'cancelled':True})
        with patch.object(framefiles,'pick_path') as pick:
            for data in ['https://remote/image.png','data:image/png;base64,invalid!']:
                with self.assertRaises(ValueError):framefiles.export(data)
            pick.assert_not_called()

if __name__=='__main__':unittest.main()

"""Combining an extension renders one new take and leaves both originals and the edit intact."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from forgestudio import clipkit,library,media,projects

class Combine(unittest.TestCase):
    def setUp(self):
        self.tmp=Path(tempfile.mkdtemp(prefix='combine-'))
        self.env=patch.dict(os.environ,{'FORGE_STUDIO_ROOT':str(self.tmp)})
        self.env.start();projects.create('combine')
        self.clips=[]
        for i,color in enumerate(['red','blue']):
            p=self.tmp/f'{color}.mp4'
            subprocess.run([library.ffmpeg(),'-v','error','-y','-f','lavfi','-i',f'color={color}:s=96x128:r=24:d=1','-f','lavfi','-i','sine=frequency=440:duration=1','-c:v','libx264','-c:a','aac','-shortest',str(p)],check=True)
            self.clips.append({'id':color,'kind':'video','track':'v1','media':projects.add_media('combine',str(p)),'start':3+i,'duration':1,'in':0})
    def tearDown(self):
        self.env.stop();shutil.rmtree(self.tmp)
    def test_rendered_order_duration_and_source_preservation(self):
        original={p:p.read_bytes() for p in self.tmp.glob('*.mp4')}
        with patch.object(clipkit.frames,'_task',side_effect=lambda kind,fn,*a:fn({},*a)):
            result=clipkit.merge('combine',self.clips,{'width':96,'height':128,'fps':24,'background':'#000000'})
        path=projects.media_path('combine',result['web'])
        self.assertEqual(result['seconds'],2)
        self.assertAlmostEqual(media.probe(path)['duration'],2,delta=.05)
        for t,dominant in [(.3,0),(1.3,2)]:
            rgb=subprocess.check_output([library.ffmpeg(),'-v','error','-ss',str(t),'-i',path,'-frames:v','1','-vf','scale=1:1','-f','rawvideo','-pix_fmt','rgb24','-'])
            self.assertGreater(rgb[dominant],200)
        self.assertEqual(projects.load('combine')['doc']['clips'],[])
        for p,data in original.items():self.assertEqual(p.read_bytes(),data)
    def test_nonadjacent_clips_are_rejected(self):
        self.clips[1]['start']=8
        with self.assertRaisesRegex(ValueError,'touch'):clipkit.merge('combine',self.clips,{'fps':24})
    def test_linked_detached_audio_is_baked_into_combined_video(self):
        import array
        for c in self.clips:c['muted']=True
        audio=[dict(c,kind='audio',track='a1',muted=False,linkedTo=c['id']) for c in self.clips]
        with patch.object(clipkit.frames,'_task',side_effect=lambda kind,fn,*a:fn({},*a)):
            result=clipkit.merge('combine',self.clips,{'width':96,'height':128,'fps':24,'background':'#000000'},audio)
        path=projects.media_path('combine',result['web'])
        self.assertTrue(media.probe(path)['audio'])
        for start in (.2,1.2):
            raw=subprocess.check_output([library.ffmpeg(),'-v','error','-ss',str(start),'-i',path,'-t','0.4','-f','s16le','-ac','1','-ar','16000','-'])
            samples=array.array('h',raw)
            self.assertGreater(sum(x*x for x in samples)/len(samples),10000)
    def test_linked_audio_outside_the_combined_shot_is_rejected(self):
        audio=[dict(self.clips[0],kind='audio',start=2)]
        with self.assertRaisesRegex(ValueError,'fit inside'):clipkit.merge('combine',self.clips,{'fps':24},audio)
if __name__=='__main__':unittest.main()

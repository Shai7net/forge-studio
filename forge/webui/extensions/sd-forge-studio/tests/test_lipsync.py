"""Self-contained lip-sync previews keep source media and timelines untouched until client approval."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'sd-forge-video-studio'))
from forgestudio import lipsync, library, media, projects
from videostudio import sound as vs_sound


class Lipsync(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='studio-lips-')
        self.env = patch.dict(os.environ, {'FORGE_STUDIO_ROOT': self.tmp})
        self.env.start()
        projects.create('lips')
        self.video = os.path.join(self.tmp, 'clip.mp4')
        self.wav = os.path.join(self.tmp, 'voice.wav')
        subprocess.run([library.ffmpeg(), '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=s=96x128:r=32:d=1',
                        '-c:v', 'libx264', self.video], check=True)
        subprocess.run([library.ffmpeg(), '-v', 'error', '-y', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=1.53', self.wav], check=True)
        self.web = projects.add_media('lips', self.video)
        self.audio = projects.add_media('lips', self.wav)

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.tmp)

    def fake_vs(self):
        def say(task, video, speech, start, out, **kwargs):
            vs_sound._segment(video, 0, 1.5625, 32, out)
        return types.SimpleNamespace(voice_choices=lambda: ['Voice'], make_speech=lambda task, text, lang, voice, out, **kwargs: shutil.copy2(self.wav, out), lipsync=say)

    def test_sound_preview_has_the_words_and_keeps_the_edit_unchanged(self):
        before = projects.load('lips')['doc']
        original = Path(self.video).read_bytes()
        with patch.object(lipsync, '_vs_sound', return_value=self.fake_vs()), patch.object(lipsync, '_on_card', side_effect=lambda f: f()):
            result = lipsync._lipsync({}, 'lips', self.web, 0, 1, 'sound', {'sound': self.audio})
        path = projects.media_path('lips', result['web'])
        self.assertTrue(media.probe(path)['audio'])
        self.assertEqual(result['seconds'], 1.5625)
        self.assertEqual(projects.load('lips')['doc'], before)
        self.assertEqual(Path(self.video).read_bytes(), original)
        with open(path + '.lips.json', encoding='utf-8') as f:
            self.assertEqual(json.load(f)['source'], self.web)

    def test_text_preview_keeps_its_voice_and_settings_for_reuse(self):
        with patch.object(lipsync, '_vs_sound', return_value=self.fake_vs()), patch.object(lipsync, '_on_card', side_effect=lambda f: f()):
            result = lipsync._lipsync({}, 'lips', self.web, 0, 1, 'text', {'text': 'Help me!', 'voice': 'Voice', 'language': 'English'})
        self.assertTrue(os.path.isfile(projects.media_path('lips', result['speech'])))
        self.assertTrue(media.probe(projects.media_path('lips', result['web']))['audio'])
        self.assertEqual(projects.load('lips')['doc']['clips'], [])

    def test_prepared_words_begin_inside_the_shot_and_do_not_change_its_length(self):
        import numpy as np
        import wave
        calls=[]
        def say(task,clip,speech,offset,out,**kwargs):
            calls.append((media.probe(clip)['duration'],offset))
            vs_sound._segment(clip,0,3,32,out)
        vs=types.SimpleNamespace(video_info=vs_sound.video_info,_segment=vs_sound._segment,lipsync=say,
            make_speech=lambda *a,**k:self.fail('Prepared speech must not be regenerated'))
        with patch.object(lipsync,'_vs_sound',return_value=vs),patch.object(lipsync,'_on_card',side_effect=lambda fn:fn()):
            result=lipsync._lipsync({},'lips',self.web,0,1,'prepared',{'speech':self.audio,'offset':1,'clip_seconds':3})
        self.assertEqual(calls,[(3,1)])
        self.assertEqual((result['seconds'],result['speech_offset'],result['speech']),(3,1,self.audio))
        pcm=subprocess.check_output([library.ffmpeg(),'-v','error','-i',projects.media_path('lips',result['web']),'-f','s16le','-ar','16000','-ac','1','-'])
        samples=np.frombuffer(pcm,np.int16).astype(float)
        self.assertLess(np.max(abs(samples[:15000])),20)
        self.assertGreater(np.sqrt(np.mean(samples[17000:30000]**2)),100)
        self.assertEqual(projects.load('lips')['doc']['clips'],[])


if __name__ == '__main__':
    unittest.main()

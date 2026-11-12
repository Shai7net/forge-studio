"""Speech-first workflow: exact ranges, preserved words, independent pitch/speed, and no lip generation."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import wave
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from forgestudio import lipspeech, library, media, projects


class Speech(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='lip-speech-'))
        self.env = patch.dict(os.environ, {'FORGE_STUDIO_ROOT': str(self.tmp)})
        self.env.start(); projects.create('speech')
        self.raw = self.tmp/'source.wav'
        subprocess.run([library.ffmpeg(),'-v','error','-y','-f','lavfi','-i','sine=frequency=440:duration=4','-ar','48000',str(self.raw)],check=True)
        self.web = projects.add_media('speech',str(self.raw))

    def tearDown(self):
        self.env.stop(); shutil.rmtree(self.tmp)

    def test_pitch_does_not_change_timing_and_fast_speech_is_padded_not_repeated(self):
        result = lipspeech._prepare({},'speech',self.web,3,2,12,0,0,True,2,4)
        with wave.open(projects.media_path('speech',result['web'])) as wav:
            rate = wav.getframerate(); samples = np.frombuffer(wav.readframes(wav.getnframes()),np.int16).astype(float)
        self.assertEqual(len(samples),3*48000)
        spectrum = np.abs(np.fft.rfft(samples[rate//4:rate]))
        peak = np.fft.rfftfreq(3*rate//4,1/rate)[spectrum.argmax()]
        self.assertAlmostEqual(peak,880,delta=3)
        self.assertGreater(np.sqrt(np.mean(samples[rate:rate+rate//2]**2)),100)
        self.assertLess(np.max(abs(samples[rate*5//2:])),2)
        self.assertEqual(projects.load('speech')['doc']['clips'],[])

    def test_auto_fit_retains_every_word_and_strict_speed_rejects_overflow(self):
        with patch.object(lipspeech.frames,'_task',side_effect=lambda kind,fn,*a: fn({},*a)):
            result = lipspeech.prepare('speech',self.web,2,speed=1)
        self.assertAlmostEqual(result['effective_speed'],2,places=3)
        self.assertAlmostEqual(media.probe(projects.media_path('speech',result['web']))['duration'],2,places=3)
        with self.assertRaisesRegex(ValueError,'Select more time'):
            lipspeech.prepare('speech',self.web,2,speed=1,fit=False)
        with self.assertRaisesRegex(ValueError,'too short'):
            lipspeech.prepare('speech',self.web,0.1)

    def test_stage_one_only_makes_speech_and_passes_the_requested_voice_and_expression(self):
        calls=[]
        def make(task,text,language,voice,out,**kwargs):
            calls.append((text,language,voice,kwargs)); shutil.copy2(self.raw,out)
        vs=types.SimpleNamespace(make_speech=make,lipsync=lambda *a,**k: self.fail('Speech preview must not run lip sync'))
        with patch.object(lipspeech,'_vs_sound',return_value=vs),patch.object(lipspeech,'_on_card',side_effect=lambda fn:fn()):
            result=lipspeech._speech({},'speech','The requested words','Hebrew','Hebrew woman 2',2,1.2,42,'Energetic')
        self.assertEqual(calls[0][:3],('The requested words','Hebrew','Hebrew woman 2'))
        self.assertEqual(calls[0][3]['exaggeration'],1.2)
        self.assertEqual(result['raw_seconds'],4)
        self.assertEqual(result['selected_seconds'],2)
        self.assertEqual(projects.load('speech')['doc']['clips'],[])

    def test_invalid_tuning_values_never_start_a_job(self):
        for key,value in [('seconds',0),('speed',0),('pitch',13),('gain',float('nan')),('tone',2)]:
            kwargs={'seconds':2,key:value}
            with self.assertRaises(ValueError): lipspeech.prepare('speech',self.web,**kwargs)

    def test_edited_audio_uses_exact_positions_trims_and_silent_gaps(self):
        before=self.raw.read_bytes()
        parts=[{'start':1,'in':0,'duration':.5},{'start':3,'in':2,'duration':1}]
        with patch.object(lipspeech.frames,'_task',side_effect=lambda kind,fn,*a:fn({},*a)):
            result=lipspeech.edit('speech',self.web,5,parts)
            cached=lipspeech.edit('speech',self.web,5,parts)
        self.assertEqual(result['web'],cached['web'])
        with wave.open(projects.media_path('speech',result['web'])) as w:
            y=np.frombuffer(w.readframes(w.getnframes()),np.int16)
        self.assertEqual(len(y),5*48000)
        self.assertLess(abs(y[:47000]).max(),2)
        self.assertGreater(np.sqrt(np.mean(y[50000:65000].astype(float)**2)),100)
        self.assertLess(abs(y[80000:140000]).max(),2)
        self.assertGreater(np.sqrt(np.mean(y[150000:180000].astype(float)**2)),100)
        self.assertEqual(self.raw.read_bytes(),before)
        self.assertEqual(projects.load('speech')['doc']['clips'],[])

    def test_invalid_edit_bounds_and_overlaps_cannot_start_rendering(self):
        for parts in [[],[{'start':4,'in':0,'duration':2}],[{'start':1,'in':3,'duration':2}],
                      [{'start':0,'in':0,'duration':2},{'start':1,'in':2,'duration':1}],
                      [{'start':float('nan'),'in':0,'duration':1}]]:
            with self.assertRaises(ValueError):lipspeech.edit('speech',self.web,5,parts)


if __name__=='__main__': unittest.main()

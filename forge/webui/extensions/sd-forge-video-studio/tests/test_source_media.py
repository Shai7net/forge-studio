"""Source-driven UI settings and real FPS/audio preparation; no checkpoint inference."""
import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image
import torch
from videostudio import ltx, models, sound, ui
from videostudio.engine import animate_prep, ltx_pipeline


class SourceMedia(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def picture(self,size):
        path=self.root/f'{size[0]}x{size[1]}.png'
        Image.new('RGB',size,'blue').save(path)
        return str(path)

    def audio(self,seconds):
        path=self.root/'voice.wav'
        with wave.open(str(path),'wb') as f:
            f.setparams((1,2,24000,0,'NONE','not compressed'))
            f.writeframes(b'\0\0'*round(seconds*24000))
        return str(path)

    def test_audio_tab_prefers_installed_ltx_and_keeps_an_existing_ltx_choice(self):
        other=next(p for p in ltx.PRESETS if p!=ltx.DEFAULT_PRESET)
        with patch.object(models,'installed_presets',return_value=[models.DEFAULT_PRESET,ltx.DEFAULT_PRESET,other]):
            self.assertEqual(ui.audio_preset(models.DEFAULT_PRESET),ltx.DEFAULT_PRESET)
            self.assertEqual(ui.audio_preset(other),other)
        with patch.object(models,'installed_presets',return_value=[other]):
            self.assertEqual(ui.audio_preset(models.DEFAULT_PRESET),other)

    def test_audio_duration_overrides_manual_length_and_restores_normal_slider(self):
        update=ui.source_length('ltx_audio',self.audio(9.25),1)
        self.assertAlmostEqual(update['value'],9.25,places=2)
        self.assertFalse(update['interactive'])
        self.assertGreaterEqual(update['maximum'],9.25)
        normal=ui.source_length('i2v',None,9.25)
        self.assertTrue(normal['interactive'])
        self.assertLessEqual(normal['value'],7.5)

    def test_source_shapes_follow_landscape_and_portrait_without_upscaling_small_images(self):
        landscape=self.picture((1280,720))
        portrait=self.picture((720,1280))
        for preset,quality in [(models.DEFAULT_PRESET,ui.DEFAULT_QUALITY),(ltx.DEFAULT_PRESET,ltx.DEFAULT_QUALITY)]:
            multiple=32 if ltx.is_ltx(preset) else 16
            w,h=ui.source_dimensions('i2v',landscape,None,None,quality,preset,None)
            self.assertGreater(w,h)
            self.assertLess(abs(w/h-1280/720),.08)
            self.assertEqual((w%multiple,h%multiple),(0,0))
            w,h=ui.source_dimensions('ltx_audio',None,portrait,None,quality,preset,None)
            self.assertLess(w,h)
        small=self.picture((128,256))
        self.assertEqual(ui.source_dimensions('i2v',small,None,None,ltx.DEFAULT_QUALITY,ltx.DEFAULT_PRESET,None),(128,256))

    def test_replacement_uses_source_shape_duration_and_remaining_time(self):
        with patch.object(ui,'video_info',return_value=(1920,1080,42.137,True)):
            whole=ui.replacement_length('test.mp4')
            self.assertEqual(whole['value'],42.137)
            self.assertEqual(whole['maximum'],42.137)
            self.assertEqual(ui.replacement_length('test.mp4',2)['value'],40.137)
            w,h=ui.source_dimensions('replace',None,None,'test.mp4',None,models.DEFAULT_PRESET,None)
            self.assertGreater(w,h)
            self.assertLess(abs(w/h-16/9),.08)

    def test_long_audio_windows_cover_the_tail_without_gaps_or_oversized_latents(self):
        for seconds in (.02,1.137,8,9.25,42.137,240):
            parts=ltx.audio_parts(seconds)
            at=0
            for part in parts:
                self.assertAlmostEqual(part['audio_start'],at/24)
                self.assertEqual((part['frames']-1)%8,0)
                self.assertLessEqual(part['frames'],193)
                self.assertLessEqual(part['requested_frames'],part['frames'])
                at+=part['requested_frames']-1
            self.assertGreaterEqual(at/24+1e-9,seconds)
            self.assertLess(at/24-seconds,1/24+1e-9)
        with self.assertRaises(ValueError):
            ltx.audio_parts(241)

    def test_audio_window_uses_the_corresponding_interval_and_only_pads_the_tail(self):
        source={'sample_rate':10,'waveform':torch.arange(100).reshape(1,1,100).float()}
        window=ltx_pipeline.audio_window(source,8,4,torch)['waveform']
        self.assertTrue(torch.equal(window[0,0,:20],torch.arange(80,100).float()))
        self.assertEqual(window.shape[-1],40)
        self.assertEqual(window[0,0,20:].sum().item(),0)

    def test_2997_source_is_resampled_before_motion_analysis_without_retiming_or_source_changes(self):
        path=self.root/'2997.mp4'
        subprocess.run([sound.ffmpeg(),'-v','error','-y','-f','lavfi','-i',
                        'testsrc2=s=64x48:r=30000/1001:d=2.137','-c:v','libx264','-pix_fmt','yuv420p',str(path)],
                       check=True,capture_output=True,timeout=15)
        before=hashlib.sha256(path.read_bytes()).hexdigest()
        for fps in (16,24):
            frames=animate_prep.read_frames(str(path),64,48,fps,.333333333,1.25)
            self.assertEqual(frames.shape[1:],(48,64,3))
            self.assertLessEqual(abs(len(frames)/fps-1.25),1/fps)
            self.assertGreater((frames[-1].astype(float)-frames[0]).std(),1)
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),before)


if __name__=='__main__':
    unittest.main()

"""Completed results are recoverable from project junctions, idempotently and without another AI run."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from forgestudio import api, generate, library, paths, projects


class GenerationResults(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.env=patch.dict(os.environ,{'FORGE_STUDIO_ROOT':str(self.root/'data'),'FORGE_STUDIO_LIBRARY':str(self.root/'output')})
        self.env.start();(self.root/'output').mkdir()
        self.external=self.root/'external-project';self.external.mkdir()
        self.link=Path(paths.projects_dir())/'linked'
        projects.create('linked')
        assert self.link.parent.resolve()==Path(paths.projects_dir()).resolve()
        for child in self.link.iterdir():child.rename(self.external/child.name)
        self.link.rmdir()
        if os.name=='nt':
            subprocess.run(['cmd.exe','/c','mklink','/J',str(self.link),str(self.external)],check=True,capture_output=True)
        else:
            self.link.symlink_to(self.external,target_is_directory=True)
        self.video=self.link/'generated/job/shot.mp4';self.video.parent.mkdir(parents=True)
        subprocess.run([library.ffmpeg(),'-v','error','-y','-f','lavfi','-i','color=blue:s=64x64:r=24:d=1.5',
                        '-c:v','libx264','-pix_fmt','yuv420p',str(self.video)],check=True,capture_output=True)
        generate._write('linked',[{'id':'job','state':'done','video':str(self.video),'seconds':1.5}])

    def tearDown(self):
        self.env.stop()
        # Remove the junction itself before recursive temporary-directory cleanup.
        if os.name=='nt': os.rmdir(self.link)
        else: self.link.unlink()
        self.tmp.cleanup()

    def test_real_junction_result_registers_once_without_regenerating_or_changing_video(self):
        self.assertFalse(paths.inside(str(self.video),[paths.projects_dir()]))
        before=hashlib.sha256(self.video.read_bytes()).hexdigest()
        with patch.object(generate,'_make',side_effect=AssertionError('completed result must never regenerate')):
            first=generate.result('linked','job');second=generate.result('linked','job')
        self.assertEqual(first,second)
        self.assertEqual(len(projects._studio(str(self.link))['media']),1)
        self.assertEqual(hashlib.sha256(self.video.read_bytes()).hexdigest(),before)
        self.assertEqual(generate.records('linked')[0]['web'],first)

    def test_legacy_generic_import_accepts_selected_junction_project(self):
        with patch.object(api,'_sendable_roots',return_value=[paths.projects_dir()]),patch.object(api,'_temporary_roots',return_value=[]):
            result=api.api_project_add(name='linked',id=None,path=str(self.video))
        self.assertEqual(Path(projects.media_path('linked',result['web'])).resolve(),self.video.resolve())

    def test_cancelled_result_cannot_be_imported(self):
        generate._update('linked','job',state='cancelled')
        with self.assertRaises(projects.ProjectError) as caught:generate.result('linked','job')
        self.assertEqual(caught.exception.status,409)

    def test_missing_output_reports_error_instead_of_running_generation(self):
        self.video.unlink()
        with self.assertRaises(projects.ProjectError) as caught:generate.result('linked','job')
        self.assertEqual(caught.exception.status,404)

    def test_record_cannot_import_a_file_outside_the_project_and_outputs(self):
        foreign=self.root/'foreign.mp4';foreign.write_bytes(b'private')
        generate._write('linked',[{'id':'forged','state':'done','video':str(foreign)}])
        with self.assertRaises(projects.ProjectError) as caught:generate.result('linked','forged')
        self.assertEqual(caught.exception.status,403)

    def test_result_api_recovers_existing_done_and_placed_records(self):
        result=api.api_gen_result(name='linked',id='job')
        self.assertIn('web',result)
        generate.placed('linked','job',result['web'])
        self.assertEqual(api.api_gen_result(name='linked',id='job')['web'],result['web'])
        self.assertEqual(generate.records('linked')[0]['state'],'placed')


if __name__=='__main__':unittest.main()

"""Cancellation owns only its request, stays terminal, and stops queued work and local processes."""
import os
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

EXTENSION = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EXTENSION))
sys.path.insert(0, str(EXTENSION.parent / 'sd-forge-video-studio'))
from forgestudio import generate, projects
from videostudio import runner


class CancelGeneration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'FORGE_STUDIO_ROOT': self.tmp.name})
        self.env.start()
        projects.create('test')
        generate._queue.clear(); generate._cancelled.clear(); generate._jobs.clear(); generate.live.clear()
        generate._write('test', [{'id': 'job', 'state': 'waiting'}])

    def tearDown(self):
        generate._queue.clear(); generate._cancelled.clear(); generate._jobs.clear(); generate.live.clear()
        self.env.stop(); self.tmp.cleanup()

    def test_cancel_queued_request_never_interrupts_other_work(self):
        generate._queue.append(('test', 'job'))
        with patch.object(generate, '_vs') as other:
            self.assertEqual(generate.cancel('test', 'job')['state'], 'cancelled')
            other.assert_not_called()
        self.assertEqual(generate._queue, [])
        self.assertEqual(generate.records('test')[0]['state'], 'cancelled')

    def test_only_owned_job_is_cancelled_and_late_done_cannot_replace_cancelled_state(self):
        generate._update('test', 'job', state='running')
        owned = Mock(); generate._jobs['job'] = owned
        with patch.object(generate, '_vs') as other:
            generate.cancel('test', 'job'); owned.cancel.assert_called_once(); other.assert_not_called()
        generate._update('test', 'job', state='done', video='late.mp4')
        self.assertEqual(generate.records('test')[0]['state'], 'cancelled')
        generate.placed('test', 'job')
        generate._update('test', 'job', state='cancelled')
        self.assertEqual(generate.records('test')[0]['state'], 'cancelled')
        self.assertTrue(generate.records('test')[0]['acknowledged'])

    def test_real_owned_worker_observes_its_cancellation_file(self):
        with patch.object(runner, 'JOBS_DIR', self.tmp.name):
            job = runner.Job({})
        child = subprocess.Popen([sys.executable, '-c',
            'import pathlib,sys,time; p=pathlib.Path(sys.argv[1]);\nwhile not p.exists(): time.sleep(.02)', job.cancel_file],
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            generate._update('test', 'job', state='running')
            generate._remember_job('job', job)
            generate.cancel('test', 'job')
            child.wait(timeout=3)
            self.assertEqual(child.returncode, 0)
            self.assertTrue(Path(job.cancel_file).is_file())
        finally:
            if child.poll() is None:
                child.kill(); child.wait()

    def test_cancel_before_worker_registration_is_applied_when_it_arrives(self):
        generate.cancel('test', 'job')
        job = Mock(); generate._remember_job('job', job)
        job.cancel.assert_called_once()

    def test_cannot_cancel_a_job_from_another_project(self):
        with self.assertRaises(ValueError):
            generate.cancel('test', 'foreign')

    def test_cancelled_ffmpeg_style_process_is_reaped_promptly(self):
        children = []; real = subprocess.Popen
        def spawn(*args, **kwargs):
            child = real(*args, **kwargs); children.append(child); return child
        timer = threading.Timer(.2, lambda: generate._cancelled.add('process'))
        before = time.monotonic(); timer.start()
        try:
            with patch.object(generate.subprocess, 'Popen', side_effect=spawn):
                with self.assertRaisesRegex(RuntimeError, 'cancelled'):
                    generate._run_command('process', [sys.executable, '-c', 'import time; time.sleep(30)'], 60)
        finally:
            timer.join()
        self.assertLess(time.monotonic() - before, 4)
        self.assertIsNotNone(children[0].poll())

    def test_cancel_while_waiting_for_forge_keeps_current_owners_lock(self):
        lock = threading.Lock(); lock.acquire(); cancel = threading.Event(); stopped = threading.Event()
        def waiter():
            try:
                with runner.generation_queue(lock, cancel.is_set):
                    self.fail('cancelled work acquired the queue')
            except RuntimeError:
                stopped.set()
        thread = threading.Thread(target=waiter); thread.start(); cancel.set()
        thread.join(2)
        self.assertTrue(stopped.is_set()); self.assertTrue(lock.locked()); lock.release()

    def test_cancel_on_queue_entry_releases_lock(self):
        lock = threading.Lock()
        with self.assertRaisesRegex(RuntimeError, 'cancelled'):
            with runner.generation_queue(lock, lambda: True):
                self.fail('cancelled work started')
        self.assertFalse(lock.locked())

    def test_uses_real_forge_fifo_lock_without_timeout_keyword(self):
        source = EXTENSION.parent.parent / 'modules/fifo_lock.py'
        if not source.is_file():
            self.skipTest('Forge core is not installed in this standalone checkout')
        spec = importlib.util.spec_from_file_location('forge_fifo_lock_test', source)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        lock = module.FIFOLock()
        with runner.generation_queue(lock, lambda: False):
            self.assertFalse(lock.acquire(False))
        self.assertTrue(lock.acquire(False)); lock.release()


if __name__ == '__main__':
    unittest.main()

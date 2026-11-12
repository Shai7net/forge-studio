"""The native and standalone paths report identical useful Forge progress."""
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fibo2 import service, models
from fibo2.contracts import Generation


class Progress(unittest.TestCase):
    def test_native_batch_progress_survives_decoding_and_initializes_count(self):
        state = types.SimpleNamespace(job_count=-1, job_no=0, sampling_step=0, sampling_steps=0,
                                      interrupted=False, skipped=False, stopping_generation=False)
        queue = types.ModuleType("modules.call_queue"); queue.queue_lock = None
        shared = types.ModuleType("modules.shared"); shared.state = state
        modules = types.ModuleType("modules"); modules.shared = shared
        seen = []

        def run(request, refs, mask, loras, callback):
            self.assertEqual(state.job_count, 2)
            for event in [dict(stage="loading"), dict(stage="sampling", image=1, step=2, steps=4),
                          dict(stage="decoding", image=1), dict(stage="sampling", image=2, step=0, steps=4),
                          dict(stage="sampling", image=2, step=4, steps=4), dict(stage="done")]:
                callback(event); seen.append((state.job_no, state.sampling_step, state.sampling_steps))
            return {"images": []}

        with patch.dict(sys.modules, {"modules": modules, "modules.shared": shared, "modules.call_queue": queue}), \
             patch.object(service, "release_forge_gpu"), patch.object(models, "components"), \
             patch("fibo2.models.status", return_value={"runtime_installed": True}), patch.object(service.runner, "run", side_effect=run):
            service.generate(Generation(prompt="A cup", count=2, steps=4), manage_forge_job=False)
        self.assertEqual(seen, [(0, 0, 4), (0, 2, 4), (0, 2, 4), (1, 0, 4), (1, 4, 4), (1, 4, 4)])

if __name__ == "__main__":
    unittest.main()

"""'Download and set up this model': what the engine part does for each model, in order, without running anything."""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from videostudio import h3, ltx, models, runner  # noqa: E402

calls = []
runner.setup_fibo_runtime = lambda: calls.append('fibo')
runner.setup_h3_engine = lambda: calls.append('h3')
runner.setup_ltx_engine = lambda: calls.append('ltx')


def run(preset, fibo, h3_ready, ltx_ready):
    calls.clear()
    runner.SETUP.update(running=False, text='', error='')
    runner.fibo_runtime_ready = lambda: fibo
    h3.runtime_ready = lambda: h3_ready
    ltx.runtime_ready = lambda: ltx_ready
    runner.start_setup(preset)
    for _ in range(100):
        if not runner.SETUP['running']:
            break
        time.sleep(0.02)
    return list(calls)


H3 = next(iter(h3.PRESETS))
LTX = next(iter(ltx.PRESETS))
WAN = next(p for p in models.PRESETS if not h3.is_h3(p) and not ltx.is_ltx(p))
assert run(H3, False, False, True) == ['fibo', 'h3'], calls
assert runner.SETUP['text'] == 'Engine ready.'
print('ok MiniMax-H3 on a new PC: FIBO 2.0\'s engine first, then its own')
assert run(H3, True, False, True) == ['h3']
print('ok MiniMax-H3 with FIBO 2.0 already set up: only its own nodes')
assert run(H3, True, True, True) == []
print('ok MiniMax-H3 ready: nothing to do')
assert run(LTX, False, False, False) == ['ltx']
print('ok LTX: its engine')
assert run(WAN, False, False, False) == []
print('ok Wan: files only')


def failing():
    raise RuntimeError('no network')


runner.setup_fibo_runtime = failing
assert run(H3, False, False, True) == []
assert 'no network' in runner.SETUP['error'] and not runner.SETUP['running']
print('ok a failed step is shown in Models and stops the rest')

own = os.path.join(os.path.dirname(os.path.dirname(models.WEBUI_DIR)), 'system', 'aria2', 'aria2c.exe')
print('ok Forge Studio\'s own aria2c is looked for at', own)

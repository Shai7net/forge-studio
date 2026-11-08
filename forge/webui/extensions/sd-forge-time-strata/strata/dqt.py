"""DeforumationQT's live values, read from its mediator, to steer a chain's trail and the follow camera.

DeforumationQT's mediator answers on the named pipe \\\\.\\pipe\\Deforum: a pickled [0, name, 0] asks for a value
and the answer is a pickled list holding it as text (Deforum's own deforum_mediator.py talks to it the same way).
Time Strata only reads: its pan, rotation, zoom and lens sliders become the trail's turn and climb and the camera's
angle, so playing with them in DeforumationQT moves the figure in space while Deforum draws it. Nothing is ever
written to the mediator, so DeforumationQT's own state and a Deforum render it drives are left as they are.

The pipe takes one connection at a time and a Deforum render uses it many times a frame, so a busy pipe is
tried again a few times and a value that does not come is left out of the answer (the page keeps the last one).
"""

import io
import os
import pickle
import threading
import time

PIPE = r"\\.\pipe\Deforum"
NAMES = ("translation_x", "translation_y", "translation_z", "rotation_x", "rotation_y", "fov")
_lock = threading.Lock()


class _OnlyPlainValues(pickle.Unpickler):
    """The mediator's answers are lists of text and numbers: nothing that needs a class is accepted."""

    def find_class(self, module, name):
        raise pickle.UnpicklingError(f"not a plain value: {module}.{name}")


def _plain(data):
    return _OnlyPlainValues(io.BytesIO(data)).load()


def running():
    if os.name != "nt":
        return False
    try:
        return "Deforum" in os.listdir("\\\\.\\pipe\\")
    except OSError:
        return False


def _ask(name):
    import win32file
    import win32pipe
    try:
        win32pipe.WaitNamedPipe(PIPE, 60)
    except Exception:
        pass
    handle = win32file.CreateFile(PIPE, win32file.GENERIC_READ | win32file.GENERIC_WRITE, 0, None, win32file.OPEN_EXISTING, 0, None)
    try:
        win32pipe.SetNamedPipeHandleState(handle, win32pipe.PIPE_READMODE_MESSAGE, None, None)
        win32file.WriteFile(handle, pickle.dumps([0, name, 0]))
        _, data = win32file.ReadFile(handle, 64 * 1024)
    finally:
        win32file.CloseHandle(handle)
    answer = _plain(data)
    return answer[0] if isinstance(answer, (list, tuple)) and answer else answer


def read(names=NAMES, tries=6):
    """{"running": bool, "values": {name: number}} - only the values that could be read now."""
    if not running():
        return {"running": False, "values": {}}
    values = {}
    with _lock:
        for name in names:
            for _attempt in range(tries):
                try:
                    value = _ask(name)
                    break
                except Exception:
                    time.sleep(0.015)
            else:
                continue
            try:
                values[name] = float(value)
            except (TypeError, ValueError):
                pass          # (an older mediator answers "NO SUCH COMMAND" for what it does not know)
    return {"running": True, "values": values}

"""Runs another program's own script, and ends it when the Forge that started it is gone.

    python solid_host.py <pid of Forge> <script.py> [the script's own arguments]

Hunyuan3D is started through this, in its own Python: left alone after Forge was closed or had crashed, it
would go on holding more than ten gigabytes of memory with nobody to stop it.
"""
import os
import runpy
import sys
import threading
import time


def _watch(pid):
    if os.name == "nt":
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x00100000, False, pid)        # SYNCHRONIZE: only to wait for it
        if handle:
            ctypes.windll.kernel32.WaitForSingleObject(handle, 0xFFFFFFFF)
            os._exit(0)
    while True:
        time.sleep(2)
        try:
            os.kill(pid, 0)
        except OSError:
            os._exit(0)


if __name__ == "__main__":
    parent, script = int(sys.argv[1]), os.path.abspath(sys.argv[2])
    threading.Thread(target=_watch, args=(parent,), daemon=True).start()
    sys.argv = [script] + sys.argv[3:]
    sys.path[0] = os.path.dirname(script)
    runpy.run_path(script, run_name="__main__")

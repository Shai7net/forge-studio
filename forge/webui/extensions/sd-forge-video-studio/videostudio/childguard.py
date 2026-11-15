"""Workers end with Forge (2026-11-15). A worker started by an extension (a video engine, ComfyUI for FIBO, the
prompt writer's llama-server, a sound or export job) kept running when Forge was closed, crashed or restarted, and
held the graphics card: the next session's renders crawled or ran out of memory. `bind(process)` puts the started
process into a Windows job object owned by this Forge process; when Forge's process ends, for any reason, Windows
ends the job's processes (and what they started). The same named job is shared by every extension's copy of this
file. Elsewhere than Windows, and if anything here fails, the process is returned untouched."""
import os

_job = None


def bind(process):
    global _job
    if os.name != "nt" or process is None:
        return process
    try:
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        if _job is None:
            class Basic(ctypes.Structure):
                _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                            ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                            ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                            ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                            ("SchedulingClass", wintypes.DWORD)]

            class Counters(ctypes.Structure):
                _fields_ = [(n, ctypes.c_uint64) for n in ("Reads", "Writes", "Others", "ReadBytes", "WriteBytes",
                                                           "OtherBytes")]

            class Extended(ctypes.Structure):
                _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", Counters),
                            ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                            ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

            kernel.CreateJobObjectW.restype = wintypes.HANDLE
            kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
            kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
            job = kernel.CreateJobObjectW(None, f"Local\\ForgeWorkers-{os.getpid()}")
            if not job:
                return process
            info = Extended()
            info.BasicLimitInformation.LimitFlags = 0x2000   # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not kernel.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):  # extended info
                return process
            _job = job
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.AssignProcessToJobObject(_job, int(process._handle))
    except Exception:  # noqa: BLE001 - a worker that is not tied is still a worker
        pass
    return process

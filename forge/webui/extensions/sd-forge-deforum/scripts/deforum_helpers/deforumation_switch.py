"""DeforumationQT ON/OFF switch, shown above Deforum's preview.

ON: renders are controlled by DeforumationQT through its mediator's named pipe, and wait while it is not running.
OFF: renders use Deforum's own settings only and never touch the pipe.
The choice is kept in deforumation_switch.json in the extension folder, so it survives a restart.

With DeforumationQT ON and its pan/zoom/rotation/tilt checkbox ticked, DeforumationQT's value is the camera motion,
so 0 stands still. Upstream DeforumationQT added it on top of Deforum's own schedule instead (Translation Z defaults
to 1.75, so its "0" still flew forward); set "motion_adds_to_deforum_schedule": true in the json to get that back.
"""
import json
import os
import re
import subprocess
import sys

STATE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "deforumation_switch.json")
PIPE_NAME = "Deforum"
_state = None


def _load():
    global _state
    if _state is None:
        try:
            with open(STATE_FILE, encoding="utf-8-sig") as f:
                _state = json.load(f)
        except (OSError, ValueError):
            _state = {}
    return _state


def is_enabled():
    # without a saved choice keep the old behaviour, where DeforumationQT is always used
    return bool(_load().get("enabled", True))


def motion_adds_to_schedule():
    return bool(_load().get("motion_adds_to_deforum_schedule", False))


def set_enabled(value):
    state = _load()
    state["enabled"] = bool(value)
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f)
    except OSError as e:
        print(f"Deforum: could not save the DeforumationQT switch: {e}")
    print(f"Deforum: DeforumationQT switched {'ON' if state['enabled'] else 'OFF'}")
    return state["enabled"]


def mediator_is_running():
    try:
        return PIPE_NAME in os.listdir("\\\\.\\pipe\\")
    except OSError:
        return False


# Starting DeforumationQT from Deforum: the other half of the switch bar runs its own runme script, the one that
# starts the mediator and the GUI together. The folder is looked for once and then remembered in the json, under
# "deforumationqt_launcher", so it can also just be written there by hand.
LAUNCHER_KEY = "deforumationqt_launcher"
LAUNCHER_NAMES = (("runme_windows_named_pipes.bat", "runme_windows_websockets.bat") if sys.platform == "win32"
                  else ("runme_linux_websockets.sh", "runme_linux_websockets_osc.sh"))


def _launcher_in(folder):
    for name in LAUNCHER_NAMES:
        path = os.path.join(folder, name)
        if os.path.isfile(path):
            return path
    return None


def _search_launcher():
    """Look for DeforumationQT around the webui: beside it, above it, and one folder to the side of those."""
    here = os.path.dirname(STATE_FILE)                 # the extension folder
    folder = os.path.dirname(os.path.dirname(here))    # the webui folder
    seen = set()
    for _ in range(4):
        parent = os.path.dirname(folder)
        if not parent or parent == folder:
            break
        folder = parent
        if folder in seen:
            break
        seen.add(folder)
        found = _launcher_in(os.path.join(folder, "DeforumationQT"))
        if found:
            return found
        try:  # one level sideways, because the folder is often beside the webui's own, under another name
            neighbours = sorted(os.listdir(folder))[:200]
        except OSError:
            continue
        for name in neighbours:
            found = _launcher_in(os.path.join(folder, name, "DeforumationQT"))
            if found:
                return found
    return None


def launcher_path():
    saved = _load().get(LAUNCHER_KEY)
    if saved and os.path.isfile(saved):
        return saved
    if saved and os.path.isdir(saved):          # a folder was written in by hand: take its runme script
        found = _launcher_in(saved)
        if found:
            return found
    found = _search_launcher()
    if found:
        state = _load()
        state[LAUNCHER_KEY] = found
        try:
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(state, f)
        except OSError:
            pass                                 # not being able to remember it only costs another search
    return found


# Forge's own launcher exports these, and DeforumationQT's runme script would then take Forge's Python and its
# venv: it would pip install DeforumationQT's requirements into Forge's venv and start the GUI with the wrong
# interpreter. NoDefaultCurrentDirectoryInExePath is the other one: with it set, cmd cannot find the rw.bat that
# the runme script calls by name, and the launch quietly does nothing at all.
INHERITED_TO_DROP = ("PYTHON", "VENV_DIR", "VIRTUAL_ENV", "PIP_INSTALLER_LOCATION", "PYTHONPATH", "PYTHONHOME",
                     "NoDefaultCurrentDirectoryInExePath", "ACCELERATE", "COMMANDLINE_ARGS")


def _runme_arguments(path):
    """The two arguments a runme script hands on: the first to the mediator, the second to the GUI."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            quoted = re.findall(r'"([^"]*)"', f.read())
    except OSError:
        quoted = []
    return (quoted + ["", ""])[:2]


def _child_environment():
    environment = {name: value for name, value in os.environ.items()
                   if name.upper() not in {dropped.upper() for dropped in INHERITED_TO_DROP}}
    return environment


def launch():
    """Start DeforumationQT, mediator and GUI. Returns (started, what to tell the user)."""
    if mediator_is_running():
        return False, "DeforumationQT is already running. Close its Mediator and Deforumation windows first to start it again."
    path = launcher_path()
    if not path:
        return False, ('DeforumationQT was not found. Put its folder next to the webui, or write the path of its'
                       ' runme script into "%s" in deforumation_switch.json.' % LAUNCHER_KEY)
    folder = os.path.dirname(path)
    try:
        # The quick way, and the one that looks like nothing is broken: start the mediator and the GUI straight
        # from DeforumationQT's own venv, which is what the runme script does after its pip install. That install
        # takes a minute or two every time, and until it is over no window appears at all.
        interpreter = os.path.join(folder, "venv", "Scripts", "python.exe") if sys.platform == "win32"             else os.path.join(folder, "venv", "bin", "python")
        if os.path.isfile(interpreter):
            mediator_argument, gui_argument = _runme_arguments(path)
            for script, argument in (("mediator.py", mediator_argument), ("deforumation.py", gui_argument)):
                command = [interpreter, script] + ([argument] if argument else [])
                subprocess.Popen(command, cwd=folder, close_fds=True, env=_child_environment(),
                                 creationflags=(subprocess.CREATE_NEW_CONSOLE | subprocess.CREATE_NEW_PROCESS_GROUP)
                                 if sys.platform == "win32" else 0,
                                 start_new_session=sys.platform != "win32")
            print(f"Deforum: started DeforumationQT with {interpreter}")
            return True, "DeforumationQT is starting. Its Mediator and Deforumation windows open in a moment."
        if sys.platform == "win32":
            # Exactly what double clicking the runme script does: cmd runs it in a console window of its own, so
            # the Mediator and Deforumation windows it opens keep running after Forge is closed. "start" was tried
            # first and is too easy to get wrong - it reads a quoted argument as a window title, and it looks a
            # bare file name up on the PATH - so the .bat is handed to cmd itself, by its full path.
            subprocess.Popen(["cmd", "/c", path], cwd=folder, close_fds=True, env=_child_environment(),
                             creationflags=subprocess.CREATE_NEW_CONSOLE | subprocess.CREATE_NEW_PROCESS_GROUP)
        else:
            subprocess.Popen(["/bin/sh", os.path.basename(path)], cwd=folder, close_fds=True, start_new_session=True,
                             env=_child_environment())
    except OSError as e:
        return False, f"DeforumationQT could not be started: {e}"
    print(f"Deforum: starting DeforumationQT from {path}")
    return True, "DeforumationQT is starting. Its Mediator and Deforumation windows open in a moment."

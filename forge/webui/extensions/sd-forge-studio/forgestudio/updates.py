"""Checking for a newer Forge Studio on GitHub, updating, and restarting to apply it (the 🐸 button at the top).

The installed version is the `VERSION` file at the installation's root (`<root>/forge/webui` is this Forge); the
latest is the newest Release of Shai7net/forge-studio. Updating = `git pull --ff-only` in the root (an installation
cloned from GitHub; a copy that is not, or has local changes, is told to use update.bat instead). When the update
changes the installer's package lock or the extension list, update.bat is still needed (it installs what changed);
otherwise a restart applies it. The restart is Forge's own (webui.bat starts it again).
"""

import json
import os
import re
import subprocess
import urllib.request

from . import paths

REPO = "Shai7net/forge-studio"
ROOT = os.path.abspath(os.path.join(paths.WEBUI, "..", ".."))
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
NEEDS_INSTALL = ("installer/", "requirements", ".lock", "extensions.json", "models.json")


def _version_tuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", v or "")[:3]) or (0,)


def installed():
    try:
        with open(os.path.join(ROOT, "VERSION"), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def _git(*args, timeout=60):
    r = subprocess.run(["git", "-C", ROOT, *args], capture_output=True, text=True, timeout=timeout, creationflags=NO_WINDOW)
    return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()


def _from_github():
    code, out, _ = _git("remote", "get-url", "origin")
    return code == 0 and REPO.lower() in out.lower()


def check():
    """{installed, latest, available, notes, url, can_update, reason}"""
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases/latest",
                                 headers={"User-Agent": "ForgeStudio-updater", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        rel = json.load(r)
    latest = (rel.get("tag_name") or "").lstrip("v")
    have = installed()
    out = {"installed": have, "latest": latest, "url": rel.get("html_url", ""), "title": rel.get("name", ""),
           "notes": (rel.get("body") or "")[:3000], "available": bool(latest) and _version_tuple(latest) > _version_tuple(have),
           "can_update": False, "reason": ""}
    if not os.path.isdir(os.path.join(ROOT, ".git")) or not _from_github():
        out["reason"] = "not-git"        # a copy not cloned from GitHub: update.bat or a new download
    else:
        code, status, _ = _git("status", "--porcelain", "--untracked-files=no")
        out["can_update"] = code == 0 and not status
        if status:
            out["reason"] = "local-changes"
    if not have:
        out["available"] = bool(latest) and out["reason"] != "not-git"
    return out


def apply():
    """Pull the newest version. Returns what changed and whether update.bat is still needed (packages / extensions)."""
    state = check()
    if not state["can_update"]:
        raise ValueError({"not-git": "this installation is not a clone of the GitHub repository: run update.bat or "
                                     "download the new version",
                          "local-changes": "files of Forge Studio were changed here: run update.bat (see README, Updating)"}
                         .get(state["reason"], "cannot update here"))
    code, before, err = _git("rev-parse", "HEAD")
    if code:
        raise ValueError(err or "git failed")
    code, out, err = _git("pull", "--ff-only", timeout=600)
    if code:
        raise ValueError((err or out or "git pull failed").splitlines()[-1])
    _, after, _ = _git("rev-parse", "HEAD")
    _, files, _ = _git("diff", "--name-only", before, after)
    changed = [f for f in files.splitlines() if f]
    return {"changed": len(changed), "installed": installed(), "updated": before != after,
            "needs_install": any(any(k in f for k in NEEDS_INSTALL) for f in changed)}


def restart():
    """Forge's own restart: webui.bat starts it again (only when it was started from webui.bat)."""
    from modules import restart as forge_restart
    if not forge_restart.is_restartable():
        raise ValueError("this Forge was not started from webui.bat, so it cannot restart itself: close it and start it again")
    import threading
    threading.Timer(1.0, forge_restart.restart_program).start()   # answer the page first
    return {"restarting": True}

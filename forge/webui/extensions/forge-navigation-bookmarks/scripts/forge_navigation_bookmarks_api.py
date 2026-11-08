"""Server-side persistence for Forge navigation bookmarks.

The browser UI stays responsible for validating tab identities because those
identities are discovered from the live Forge page.  This endpoint only stores
the versioned arrangement as one small, fixed-location JSON document so every
browser using the same Forge instance sees the same arrangement.
"""

import json
import os
import tempfile
import threading

from fastapi import Body, FastAPI, HTTPException

from modules import paths, script_callbacks


CONFIG_PATH = os.path.join(paths.data_path, "forge-navigation-bookmarks.json")
MAX_CONFIG_BYTES = 1024 * 1024
_lock = threading.Lock()


def _read_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as file:
            value = json.load(file)
        return value if isinstance(value, dict) else None
    except FileNotFoundError:
        return None
    except Exception as error:
        print(f"[Forge navigation bookmarks] cannot read saved arrangement: {error}")
        return None


def _write_config(value):
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_CONFIG_BYTES:
        raise HTTPException(status_code=413, detail="bookmark arrangement is too large")

    folder = os.path.dirname(CONFIG_PATH)
    os.makedirs(folder, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=folder,
            prefix=".forge-navigation-bookmarks-", suffix=".tmp", delete=False,
        ) as file:
            temporary = file.name
            file.write(encoded)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, CONFIG_PATH)
    except Exception as error:
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass
        raise HTTPException(status_code=500, detail=f"could not save bookmark arrangement: {error}") from error


def api_get():
    return {"version": 1, "config": _read_config()}


def api_save(payload: dict = Body(...)):
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise HTTPException(status_code=400, detail="unsupported bookmark arrangement")
    with _lock:
        _write_config(payload)
    return {"ok": True, "version": 1}


def on_app_started(_demo, app: FastAPI):
    app.add_api_route("/forge-navigation-bookmarks/config", api_get, methods=["GET"])
    app.add_api_route("/forge-navigation-bookmarks/config", api_save, methods=["POST"])


script_callbacks.on_app_started(on_app_started)

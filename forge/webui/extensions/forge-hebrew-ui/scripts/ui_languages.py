"""Interface languages besides English and Hebrew, for javascript/hebrew-ui.js.

GET  /forge-ui/languages               the languages in languages/ and the size of the translation kit
GET  /forge-ui/languages/<code>        one language's dictionary (all its files merged)
GET  /forge-ui/translation-kit.zip     translation-kit/ in a zip, to give to an AI
POST /forge-ui/languages               {"files": [{"name", "text"}], "name"}: files an AI returned, saved in
                                       languages/<code>/ (only from this PC)

The work is in tools/ui_kit.py.
"""
import importlib.util
import os

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from modules import script_callbacks

_KIT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "ui_kit.py")
_spec = importlib.util.spec_from_file_location("forge_ui_kit", _KIT_PATH)
ui_kit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ui_kit)

MAX_UPLOAD = 24 * 2**20
LOCAL = {"127.0.0.1", "::1", "localhost"}


def _kit_info():
    texts, blocks = ui_kit.kit_keys()
    parts = len([f for f in os.listdir(ui_kit.KIT) if f.startswith("part-")]) if os.path.isdir(ui_kit.KIT) else 0
    return {"parts": parts, "texts": len(texts), "blocks": len(blocks), "prompt": ui_kit.PROMPT}


def list_languages():
    return {"languages": [ui_kit.describe(code) for code in ui_kit.language_codes()], "kit": _kit_info()}


def get_language(code: str):
    code = ui_kit.clean_code(code)
    if code not in ui_kit.language_codes():
        return JSONResponse({"error": f"no language '{code}' in languages/"}, status_code=404)
    return ui_kit.load_language(code)


def translation_kit():
    return Response(ui_kit.kit_zip(), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="forge-studio-translation-kit.zip"'})


async def add_language(request: Request):
    host = request.client.host if request.client else ""
    if host not in LOCAL:
        return JSONResponse({"error": "languages can be added only on the PC that runs Forge Studio: copy the files "
                                      "into extensions/forge-hebrew-ui/languages/<code>/ there"}, status_code=403)
    body = await request.body()
    if len(body) > MAX_UPLOAD:
        return JSONResponse({"error": "the files are too large"}, status_code=413)
    try:
        data = await request.json()
        files = data.get("files") or []
        if not isinstance(files, list):
            raise ValueError("no files")
        return ui_kit.save_language(files, name=str(data.get("name") or ""), code=str(data.get("code") or ""))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


def on_app_started(_demo, app):
    app.add_api_route("/forge-ui/languages", list_languages, methods=["GET"])
    app.add_api_route("/forge-ui/languages/{code}", get_language, methods=["GET"])
    app.add_api_route("/forge-ui/translation-kit.zip", translation_kit, methods=["GET"])
    app.add_api_route("/forge-ui/languages", add_language, methods=["POST"])


script_callbacks.on_app_started(on_app_started)

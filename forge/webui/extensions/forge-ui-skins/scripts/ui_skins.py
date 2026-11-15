"""Forge UI Skins (2026-11-15): the routes the page uses and the settings (Settings -> UI Skins).

Routes (all under /ui-skins):
  GET  state                 -> {active, skins, long_press, long_press_ms, cards_closed, cards_organize}
  GET  skin?name=            -> the skin {name, rules}
  POST save {name, rules, from?}   writes it (a "Save as" passes the skin it started from, whose pictures it needs)
  POST active {name}         makes it the active skin (Classic: Forge as it is)
  POST image?skin=&filename= (body: the picture) -> {file, url}
  GET  file/{skin}/{file}    a picture of a skin
  POST rename {name, to}, POST delete {name}
  GET  export?name=          one shareable file (pictures inside); POST import (that file's JSON)
  GET/POST layout            the extension cards' order and "More" (txt2img / img2img), apart from the skins
"""
import os
import sys

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from modules import script_callbacks, shared

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from uiskins import store  # noqa: E402

SECTION = ("ui_skins", "UI Skins")


def _guard(fn):
    try:
        return fn()
    except store.SkinError as e:
        raise HTTPException(status_code=e.status, detail=str(e))


def _opt(key, default):
    return shared.opts.data.get(key, default)


def _set_active(name):
    shared.opts.set("ui_skins_active", name)
    shared.opts.save(shared.config_filename)


def api_state():
    active = _opt("ui_skins_active", store.CLASSIC) or store.CLASSIC
    if active not in store.names():
        active = store.CLASSIC
    return {"active": active, "skins": store.listing(), "long_press": bool(_opt("ui_skins_long_press", True)),
            "long_press_ms": int(_opt("ui_skins_long_press_ms", 650)), "cards_closed": bool(_opt("ui_cards_closed", True)),
            "cards_organize": bool(_opt("ui_cards_organize", True))}


def api_skin(name: str = ""):
    return _guard(lambda: store.load(name))


def api_save(name: str = Body(...), rules: dict = Body(...), source: str = Body("", alias="from"),
             activate: bool = Body(True)):
    def run():
        saved = store.save(name, rules, copy_from=source)
        store.adopt_drafts(saved["name"], rules)
        if activate:
            _set_active(saved["name"])
        return saved
    return _guard(run)


def api_active(name: str = Body(..., embed=True)):
    if name != store.CLASSIC and name not in store.names():
        raise HTTPException(status_code=404, detail=f"no skin named {name}")
    _set_active(name)
    return {"active": name}


async def api_image(request: Request, skin: str = "", filename: str = "picture.png"):
    data = await request.body()
    file = _guard(lambda: store.store_image(skin, filename, data))
    return {"file": file}


def api_file(skin: str, file: str):
    path = _guard(lambda: store.image_path(skin, file))
    ext = os.path.splitext(path)[1].lower()
    return FileResponse(path, media_type=store.IMAGE_TYPES.get(ext), headers={"Cache-Control": "public, max-age=86400"})


def api_rename(name: str = Body(...), to: str = Body(...)):
    def run():
        new = store.rename(name, to)
        if _opt("ui_skins_active", "") == name:
            _set_active(new)
        return {"name": new}
    return _guard(run)


def api_delete(name: str = Body(..., embed=True)):
    def run():
        moved = store.delete(name)
        if _opt("ui_skins_active", "") == name:
            _set_active(store.CLASSIC)
        return {"moved_to": moved}
    return _guard(run)


def api_export(name: str):
    data = _guard(lambda: store.export(name))
    from urllib.parse import quote
    return JSONResponse(data, headers={"Content-Disposition": "attachment; filename=\"skin.forgeskin.json\"; "
                                                              f"filename*=UTF-8''{quote(name + '.forgeskin.json')}"})


def api_import(payload: dict = Body(...)):
    return {"name": _guard(lambda: store.import_skin(payload))}


def api_layout_get():
    return store.layout()


def api_layout_set(layout: dict = Body(..., embed=True)):
    return store.save_layout(layout)


def on_app_started(_demo, app: FastAPI):
    r = lambda path, fn, methods: app.add_api_route("/ui-skins/" + path, fn, methods=methods)  # noqa: E731
    r("state", api_state, ["GET"])
    r("skin", api_skin, ["GET"])
    r("save", api_save, ["POST"])
    r("active", api_active, ["POST"])
    r("image", api_image, ["POST"])
    r("file/{skin}/{file}", api_file, ["GET"])
    r("rename", api_rename, ["POST"])
    r("delete", api_delete, ["POST"])
    r("export", api_export, ["GET"])
    r("import", api_import, ["POST"])
    r("layout", api_layout_get, ["GET"])
    r("layout", api_layout_set, ["POST"])


def on_ui_settings():
    import gradio as gr
    shared.opts.add_option("ui_skins_active", shared.OptionInfo(
        store.CLASSIC, "Skin (how Forge's buttons, tabs and cards look; Classic = Forge as it is)", gr.Dropdown,
        lambda: {"choices": store.names()}, refresh=lambda: None, section=SECTION))
    shared.opts.add_option("ui_skins_long_press", shared.OptionInfo(
        True, "Hold the mouse on a button, tab or card title to edit its look", section=SECTION))
    shared.opts.add_option("ui_skins_long_press_ms", shared.OptionInfo(
        650, "How long to hold (milliseconds)", gr.Slider, {"minimum": 350, "maximum": 2000, "step": 50}, section=SECTION))
    shared.opts.add_option("ui_cards_closed", shared.OptionInfo(
        True, "Extension cards in txt2img and img2img start closed", section=SECTION))
    shared.opts.add_option("ui_cards_organize", shared.OptionInfo(
        True, "Extension cards can be dragged into another order and moved into \"More\"", section=SECTION))


script_callbacks.on_app_started(on_app_started)
script_callbacks.on_ui_settings(on_ui_settings)

"""Forge Workspace: a tidier txt2img / img2img (the page side is javascript/workspace.js).

This script gives each of the two tabs:
- a "Model settings" card among the extension cards, where the page moves SD VAE, Clip skip and face restoration
  (Forge's own quick-settings components, moved, so they keep working and saving as before);
- a hidden field holding the LoRAs picked as chips beside the styles: [{"name", "alias", "weight", "trigger"}]. Before
  the prompt is processed they are added to it as `<lora:alias:weight>` with their trigger words, so they reach
  Forge's own LoRA loading and the picture's infotext exactly as if they had been typed.

Routes: GET /forge-workspace/loras (every LoRA with its preview, trigger words, weight and SD version, and the SD
version of the loaded model), POST /forge-workspace/lora-trigger (saves a LoRA's trigger words into its .json, the
"activation text" Forge's own LoRA card editor writes).
"""

import json
import os
import sys

import gradio as gr

from modules import scripts, script_callbacks, shared

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from workspace_lora import add_to_prompt, parse_loras  # noqa: E402


class WorkspaceScript(scripts.Script):
    sorting_priority = -1000   # its before_process runs early: the tags are in the prompt before other scripts read it

    def title(self):
        return "Model settings"

    def show(self, is_img2img):
        return scripts.AlwaysVisible

    def ui(self, is_img2img):
        tab = "img2img" if is_img2img else "txt2img"
        with gr.Accordion("Model settings", open=False, elem_id=f"{tab}_ws_model_card"):
            gr.HTML('<div class="ws-model-home-note"></div>', elem_id=f"{tab}_ws_model_home")
        loras = gr.Textbox(value="[]", elem_id=f"{tab}_ws_loras", elem_classes=["ws-hidden"], show_label=False,
                           interactive=True)
        return [loras]

    def before_process(self, p, loras="[]", *args):
        items = parse_loras(loras)
        if not items:
            return
        if isinstance(p.prompt, list):
            p.prompt = [add_to_prompt(x, items) for x in p.prompt]
        else:
            p.prompt = add_to_prompt(p.prompt, items)
        hr = getattr(p, "hr_prompt", None)
        if isinstance(hr, str) and hr.strip():
            p.hr_prompt = add_to_prompt(hr, items)


# ---- routes ----

def _lora_page():
    from modules import ui_extra_networks
    return next((x for x in ui_extra_networks.extra_pages if x.name == "lora"), None)


def _model_version():
    m = shared.sd_model
    if m is None:
        return "Unknown"
    return "SDXL" if getattr(m, "is_sdxl", False) else "SD2" if getattr(m, "is_sd2", False) else "SD1"


def list_loras():
    import networks
    page = _lora_page()
    if not networks.available_networks:
        networks.list_available_networks()
    out = []
    for index, name in enumerate(list(networks.available_networks)):
        try:
            item = page.create_item(name, index, enable_filter=False) if page else None
        except Exception:
            item = None
        net = networks.available_networks.get(name)
        if net is None:
            continue
        meta = (item or {}).get("user_metadata") or {}
        rel = os.path.relpath(net.filename, shared.cmd_opts.lora_dir) if getattr(shared.cmd_opts, "lora_dir", None) else net.filename
        alias = net.get_alias()
        # the name in the tag: the alias unless two LoRAs share it (Forge then only knows the file name)
        tag = name if alias.lower() in getattr(networks, "forbidden_network_aliases", {}) else alias
        out.append({
            "name": name, "alias": tag, "folder": os.path.dirname(rel).replace("\\", "/"),
            "preview": (item or {}).get("preview"), "trigger": meta.get("activation text", "") or "",
            "weight": float(meta.get("preferred weight") or 0) or None,
            "version": (item or {}).get("sd_version") or net.sd_version.name,
            "mtime": os.path.getmtime(net.filename) if os.path.isfile(net.filename) else 0,
        })
    return {"loras": out, "model": _model_version(), "default_weight": float(shared.opts.extra_networks_default_multiplier or 1.0)}


def save_trigger(name, trigger):
    import networks
    net = networks.available_networks.get(name)
    if net is None:
        raise KeyError(name)
    path = os.path.splitext(net.filename)[0] + ".json"
    data = {}
    if os.path.isfile(path):
        with open(path, encoding="utf8") as f:
            try:
                data = json.load(f)
            except ValueError:
                data = {}
    data["activation text"] = (trigger or "").strip()
    with open(path, "w", encoding="utf8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
    return {"name": name, "trigger": data["activation text"]}


def on_app_started(_demo, app):
    from fastapi import Body, HTTPException

    def api_loras():
        return list_loras()

    def api_trigger(name: str = Body(...), trigger: str = Body("")):
        try:
            return save_trigger(name, trigger)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"no LoRA named {name}")

    app.add_api_route("/forge-workspace/loras", api_loras, methods=["GET"])
    app.add_api_route("/forge-workspace/lora-trigger", api_trigger, methods=["POST"])
    cache_api_info(app)


def cache_api_info(app):
    """Gradio builds /info (the "Use via API" description of all ~3,700 events, 550 KB) from scratch on every page open:
    about 3 s of the server's time each time, and FastAPI turns the reply into JSON on the server's own thread. The
    events do not change while Forge runs, so it is made once and kept (as ready JSON)."""
    import json as _json

    from starlette.responses import Response

    from fastapi.dependencies.utils import get_dependant, get_parameterless_sub_dependant
    from fastapi.encoders import jsonable_encoder
    from fastapi.routing import request_response

    cache = {}
    done = 0
    for route in app.router.routes:
        if getattr(route, "path", None) not in ("/info", "/info/") or "GET" not in (getattr(route, "methods", None) or ()):
            continue
        def make(original):
            # its only parameter is the one FastAPI should read from the request
            def cached_info(serialize: bool = True):
                if serialize not in cache:
                    data = original(serialize=serialize)
                    try:
                        body = _json.dumps(data, ensure_ascii=False)
                    except TypeError:
                        body = _json.dumps(jsonable_encoder(data), ensure_ascii=False)
                    cache[serialize] = body.encode("utf-8")
                return Response(cache[serialize], media_type="application/json")
            return cached_info

        cached_info = make(route.endpoint)
        route.endpoint = cached_info
        route.dependant = get_dependant(path=route.path_format, call=cached_info)
        for depends in (route.dependencies or [])[::-1]:   # Gradio's login check stays
            route.dependant.dependencies.insert(0, get_parameterless_sub_dependant(depends=depends, path=route.path_format))
        route.response_model = None
        route.app = request_response(route.get_route_handler())
        done += 1
    return done


script_callbacks.on_app_started(on_app_started)

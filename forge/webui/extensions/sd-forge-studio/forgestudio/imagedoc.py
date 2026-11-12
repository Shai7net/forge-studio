"""Image shots: a picture of the edit made in the image workspace, kept as layers.

A shot lives in `projects/<project>/images/<shot id>/`: `doc.json` (size, the layers top to bottom in drawing order
from the bottom, each with name, visibility, opacity, blend mode, offset and its PNG) and one PNG per layer, each the
size of the shot. Saving also writes the flattened picture into the project's media under a new name each time
(`shot-<id>-<n>.png`), which the clip then shows; the earlier flattened pictures of the shot that no clip shows any
more are removed. The page draws and flattens (it is what the user sees); the server keeps the files.
"""

import io
import json
import os
import re
import time
import uuid

from . import projects

ID = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
BLENDS = {"normal", "multiply", "screen", "overlay", "darken", "lighten", "color-dodge", "color-burn", "hard-light",
          "soft-light", "difference", "exclusion", "hue", "saturation", "color", "luminosity"}


def _check(value, what):
    if not ID.match(value or ""):
        raise ValueError(f"not a {what}")
    return value


def _dir(name, shot):
    return os.path.join(projects._folder(name), "images", _check(shot, "shot id"))


def _doc_path(name, shot):
    return os.path.join(_dir(name, shot), "doc.json")


def _write_json(path, data):
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(path + ".tmp", path)


def _png(image, path):
    image.save(path + ".tmp", "PNG")
    os.replace(path + ".tmp", path)


def _layer(name, file, **extra):
    layer = {"id": "l" + uuid.uuid4().hex[:8], "name": name, "visible": True, "opacity": 1.0, "blend": "normal",
             "x": 0, "y": 0, "file": file}
    layer.update(extra)
    return layer


def new(name, width, height, fill="#ffffff", picture=None, title=None):
    """A new shot `width` x `height`: one layer filled with `fill` (a colour or 'transparent'), or holding `picture`
    (a file of the project's media, fitted inside and centred over a transparent shot)."""
    from PIL import Image
    width, height = int(width), int(height)
    if not (16 <= width <= 8192 and 16 <= height <= 8192):
        raise ValueError("the size must be between 16 and 8192 pixels")
    shot = "s" + uuid.uuid4().hex[:10]
    folder = _dir(name, shot)
    os.makedirs(folder, exist_ok=True)
    layers = []
    if picture:
        src = projects.media_path(name, picture)
        with Image.open(src) as im:
            im = im.convert("RGBA")
            k = min(width / im.width, height / im.height)
            w, h = max(1, round(im.width * k)), max(1, round(im.height * k))
            canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            canvas.paste(im.resize((w, h), Image.LANCZOS), ((width - w) // 2, (height - h) // 2))
        layer = _layer(os.path.splitext(os.path.basename(picture))[0][:40] or "Picture", "")
    else:
        colour = (0, 0, 0, 0) if fill == "transparent" else _colour(fill)
        canvas = Image.new("RGBA", (width, height), colour)
        layer = _layer("Background", "")
    layer["file"] = f"{layer['id']}-1.png"
    _png(canvas, os.path.join(folder, layer["file"]))
    layers.append(layer)
    doc = {"id": shot, "title": title or "", "width": width, "height": height, "layers": layers, "rev": 0,
           "created": time.time(), "saved": time.time()}
    _write_json(_doc_path(name, shot), doc)
    return doc


def _colour(value):
    m = re.match(r"^#([0-9a-fA-F]{6})$", value or "")
    if not m:
        raise ValueError("a colour is #rrggbb")
    v = m.group(1)
    return tuple(int(v[i:i + 2], 16) for i in (0, 2, 4)) + (255,)


def load(name, shot):
    path = _doc_path(name, shot)
    if not os.path.isfile(path):
        raise projects.ProjectError(f"no image shot {shot}", 404)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def file_path(name, shot, file):
    if not re.match(r"^(gen/)?[A-Za-z0-9_-]{1,60}\.png$", file or ""):
        raise ValueError("not a layer file")
    path = os.path.join(_dir(name, shot), file)
    if not os.path.isfile(path):
        raise projects.ProjectError(f"no layer file {file}", 404)
    return path


def save(name, shot, doc, files, flat):
    """Keep the page's document: `files` = {layer id: PNG bytes} for the layers whose pixels changed (each gets a
    new file name so the page's cached pictures are never stale), `flat` = the flattened PNG. Layer files no layer
    uses any more are removed. Returns the document and the flattened picture's media name."""
    from PIL import Image
    old = load(name, shot)
    folder = _dir(name, shot)
    width, height = int(doc.get("width") or old["width"]), int(doc.get("height") or old["height"])
    known = {l["id"]: l for l in old["layers"]}
    layers = []
    for l in doc.get("layers") or []:
        lid = _check(l.get("id"), "layer id")
        keep = known.get(lid, {})
        layer = {"id": lid, "name": str(l.get("name") or "Layer")[:60], "visible": bool(l.get("visible", True)),
                 "opacity": max(0.0, min(1.0, float(l.get("opacity", 1.0)))),
                 "blend": l.get("blend") if l.get("blend") in BLENDS else "normal",
                 "x": int(round(float(l.get("x", 0)))), "y": int(round(float(l.get("y", 0)))),
                 "file": keep.get("file", "")}
        if l.get("text"):
            layer["text"] = l["text"]
        data = files.get(lid)
        if data is not None:
            with Image.open(io.BytesIO(data)) as im:
                im = im.convert("RGBA")
                if im.size != (width, height):
                    raise ValueError(f"layer {lid} is {im.size[0]}x{im.size[1]}, the shot is {width}x{height}")
                rev = int(re.sub(r"\D", "", (keep.get("file") or "-0").rsplit("-", 1)[-1]) or 0) + 1
                layer["file"] = f"{lid}-{rev}.png"
                _png(im, os.path.join(folder, layer["file"]))
        if not layer["file"]:
            raise ValueError(f"layer {lid} has no picture")
        layers.append(layer)
    if not layers:
        raise ValueError("a shot needs at least one layer")
    used = {l["file"] for l in layers}
    for f in os.listdir(folder):
        if f.endswith(".png") and f not in used:
            try:
                os.remove(os.path.join(folder, f))
            except OSError:
                pass
    rev = int(old.get("rev", 0)) + 1
    new_doc = dict(old, width=width, height=height, layers=layers, rev=rev, saved=time.time(),
                   title=str(doc.get("title", old.get("title", "")))[:80])
    _write_json(_doc_path(name, shot), new_doc)
    # the flattened picture, under a new name: the clip shows it
    media = os.path.join(projects._folder(name), "media")
    os.makedirs(media, exist_ok=True)
    flat_path = os.path.join(media, f"shot-{shot}-{rev}.png")
    with Image.open(io.BytesIO(flat)) as im:
        im = im.convert("RGBA")
        if im.size != (width, height):
            raise ValueError("the flattened picture has the wrong size")
        _png(im, flat_path)
    web = projects.add_media(name, flat_path)
    return {"doc": new_doc, "web": web}


def forget_old_flats(name, shot, keep_webs):
    """Remove this shot's earlier flattened pictures that no clip shows (`keep_webs`: every media name a clip of the
    edit uses, with its takes)."""
    studio = projects._studio(projects._folder(name))
    gone = []
    for web, item in list(studio["media"].items()):
        base = os.path.basename(item.get("path", ""))
        if re.match(rf"^shot-{re.escape(shot)}-\d+\.png$", base) and web not in keep_webs:
            try:
                os.remove(item["path"])
            except OSError:
                pass
            del studio["media"][web]
            gone.append(web)
    if gone:
        projects._save_studio(projects._folder(name), studio)
    return gone


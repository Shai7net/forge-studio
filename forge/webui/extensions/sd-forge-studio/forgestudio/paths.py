"""Where Forge Studio keeps things. Everything large lives under webui\\forge-studio, never in the extension."""

import os

EXTENSION = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEBUI = os.path.dirname(os.path.dirname(EXTENSION))


def _opt(name, default=""):
    try:
        from modules import shared
        return getattr(shared.opts, name, default) or default
    except Exception:
        return default


def data_root():
    root = os.environ.get("FORGE_STUDIO_ROOT") or os.path.join(WEBUI, "forge-studio")
    os.makedirs(root, exist_ok=True)
    return root


def sub(*parts):
    path = os.path.join(data_root(), *parts)
    os.makedirs(path, exist_ok=True)
    return path


def engine_root(*parts):
    """FilmCraft's builds (shared by every data folder; tests point FORGE_STUDIO_ENGINE at the real one)."""
    base = os.environ.get("FORGE_STUDIO_ENGINE") or os.path.join(data_root(), "engine")
    path = os.path.join(base, *parts)
    os.makedirs(path, exist_ok=True)
    return path


def projects_dir():
    return _opt("forge_studio_projects_dir") or sub("projects")


def exports_dir():
    path = (os.environ.get("FORGE_STUDIO_EXPORTS") or _opt("forge_studio_exports_dir")
            or os.path.join(WEBUI, "output", "forge-studio"))
    os.makedirs(path, exist_ok=True)
    return path


def webui_path(path):
    """A Forge setting's folder: relative ones are relative to the webui folder, as Forge reads them."""
    return os.path.normpath(path if os.path.isabs(path) else os.path.join(WEBUI, path))


def library_roots():
    """The folders the library shows: Forge's own output folders, the output folders of the extensions
    (webui\\output, webui\\outputs) and anything added in the settings, one per line."""
    roots = []
    for key in ("outdir_samples", "outdir_txt2img_samples", "outdir_img2img_samples", "outdir_extras_samples",
                "outdir_grids", "outdir_txt2img_grids", "outdir_img2img_grids"):
        value = _opt(key)
        if value:
            roots.append(webui_path(value))
    roots += [os.path.join(WEBUI, "output"), os.path.join(WEBUI, "outputs")]
    roots += [line.strip() for line in str(_opt("forge_studio_library_extra")).splitlines() if line.strip()]
    extra = os.environ.get("FORGE_STUDIO_LIBRARY")
    if extra:
        roots = [p for p in extra.split(os.pathsep) if p]
    out = []  # a folder inside another one is covered by it
    key = lambda p: os.path.normcase(p)  # noqa: E731
    for root in roots:
        root = os.path.normpath(root)
        if os.path.isdir(root) and not any(key(root) == key(r) or key(root).startswith(key(r) + os.sep) for r in out):
            out = [r for r in out if not key(r).startswith(key(root) + os.sep)] + [root]
    return out


def inside(path, roots):
    path = os.path.normcase(os.path.realpath(path))
    return any(path == os.path.normcase(os.path.realpath(r)) or
               path.startswith(os.path.normcase(os.path.realpath(r)) + os.sep) for r in roots)

"""Small host adapters shared by legacy Forge, Forge 2 and Forge Neo."""

import importlib
import os
import sys


def free_graphics_memory():
    """Ask every memory manager the host has loaded to let its models go. A Forge can have more than one loaded:
    an older Forge with Forge 2 files beside it loads its checkpoint through ldm_patched while backend is imported
    too, and freeing only the first one found (backend) left the checkpoint on the card, so a cut-out model ran
    out of memory (2026-10-20). Each manager frees what it holds; one that holds nothing costs nothing. When none
    is loaded yet, the known layouts are tried in turn."""
    names = ("backend.memory_management", "ldm_patched.modules.model_management")
    loaded = [name for name in names if name in sys.modules]
    errors, freed = [], False
    for name in loaded or names:
        try:
            manager = importlib.import_module(name)
        except ImportError:
            continue
        try:
            manager.unload_all_models()
            manager.soft_empty_cache()
            freed = True
        except Exception as error:
            errors.append(f"{name}: {type(error).__name__}: {error}")
        if freed and not loaded:
            break
    if not freed:
        detail = "; ".join(errors) or "no supported Forge memory manager found"
        print(f"[Time Strata] could not free graphics memory: {detail}")
    return freed


def deforum_dir():
    """Find an enabled Deforum installation, including custom folder names."""
    try:
        from modules import extensions
        for extension in extensions.active():
            folder = extension.path
            if os.path.isfile(os.path.join(folder, "scripts", "deforum_api.py")):
                return folder
        # Respect disabled extensions when the host has an extension registry.
        return None
    except (ImportError, AttributeError):
        pass
    try:
        from modules import paths
        webui = paths.data_path
    except ImportError:
        webui = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    for name in ("sd-forge-deforum", "sd-webui-deforum"):
        folder = os.path.join(webui, "extensions", name)
        if os.path.isfile(os.path.join(folder, "scripts", "deforum_api.py")):
            return folder
    return None

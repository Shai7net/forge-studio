import sys

# this will break any attempt to import xformers which will prevent stability diffusion repo from trying to use it
# Not when Forge's own backend has loaded it already (ldm_patched's model_management imports it first and picks
# xformers attention): blocking it then broke the import in ldm_patched's attention on a clean install (2026-11-16).
if "--xformers" not in "".join(sys.argv) and "xformers" not in sys.modules:
    sys.modules["xformers"] = None

# Hack to fix a changed import in torchvision 0.17+, which otherwise breaks
# basicsr; see https://github.com/AUTOMATIC1111/stable-diffusion-webui/issues/13985
try:
    import torchvision.transforms.functional_tensor  # noqa: F401
except ImportError:
    try:
        import torchvision.transforms.functional as functional
        sys.modules["torchvision.transforms.functional_tensor"] = functional
    except ImportError:
        pass  # shrug...

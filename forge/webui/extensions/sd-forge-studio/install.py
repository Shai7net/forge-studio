"""Install only what Forge Studio needs and Forge lacks, into Forge's own Python environment.

moderngl draws the GLSL transitions and effects in the export (forgestudio/glfx.py: an OpenGL 3.3 context without a
window). It is small and pure wheels (moderngl + glcontext); nothing of Forge's own is replaced.
"""
import importlib.util

import launch

for module, package in (("moderngl", "moderngl>=5.8,<6"),):
    if importlib.util.find_spec(module) is None:
        launch.run_pip(f'install "{package}"', f"Forge Studio: {module}")

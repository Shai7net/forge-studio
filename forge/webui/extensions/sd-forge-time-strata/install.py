"""Install only missing essentials into Forge's own Python environment.

AI matting is optional: do not replace the host's ONNX, CUDA, Torch or Gradio.
"""
import importlib.util

import launch


for module, package in (
    ("numpy", "numpy"),
    ("PIL", "Pillow"),
    ("cv2", "opencv-python-headless"),
    ("imageio_ffmpeg", "imageio-ffmpeg>=0.5,<0.7"),
):
    if importlib.util.find_spec(module) is None:
        launch.run_pip(f'install "{package}"', f"Time Strata: {module}")

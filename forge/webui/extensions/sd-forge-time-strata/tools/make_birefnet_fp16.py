"""Makes the half-precision copy of BiRefNet-lite that Time Strata uses when it is there.

In full precision BiRefNet-lite needs about 6 GB of the graphics card; in half precision less than 4 GB, faster,
with a cut 99.95% the same (measured on an 8 GB laptop card, 2026-10-20). The converter is not in Forge's own
environment and does not need to be: run this once in a throwaway one, for example with uv:

    uv run --no-project --python 3.10 --with onnx --with onnxconverter-common python tools/make_birefnet_fp16.py

It reads webui/models/time-strata/birefnet-general-lite.onnx (downloaded by the extension the first time it cuts)
and writes birefnet-general-lite.fp16.onnx beside it. Pass other paths to convert another file.
"""
import os
import sys

import onnx
from onnxconverter_common import float16

here = os.path.dirname(os.path.abspath(__file__))
models = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(here))), "models", "time-strata")
source = sys.argv[1] if len(sys.argv) > 1 else os.path.join(models, "birefnet-general-lite.onnx")
target = sys.argv[2] if len(sys.argv) > 2 else source[:-len(".onnx")] + ".fp16.onnx"
if not os.path.isfile(source):
    sys.exit(f"not found: {source} (cut something out once with BiRefNet-lite, which downloads it)")
half = float16.convert_float_to_float16(onnx.load(source), keep_io_types=True)
onnx.save(half, target)
print("written", target)

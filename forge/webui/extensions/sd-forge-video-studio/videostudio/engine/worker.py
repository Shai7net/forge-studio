"""Runs one video job in its own process: `python worker.py job.json`.

Forge starts this with its own Python, so nothing is installed for it, but the job lives in a separate process:
whatever it allocates (14 GB of pinned weights, the card) is returned to the system the moment it exits, and an
out-of-memory error cannot take Forge down. Progress goes to stdout as lines `@@VS {json}`.
"""

import json
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

os.environ.setdefault("PYTORCH_ALLOC_CONF", "expandable_segments:True")
os.environ.setdefault("TQDM_DISABLE", "1")  # SAM 2 prints a progress bar per frame into the job log
# Compiled kernels are kept between runs (the first run compiles ~20 small kernels, about a minute).
os.environ.setdefault("TORCHINDUCTOR_CACHE_DIR", os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(HERE))), "..", "video-studio", "cache", "inductor"))


def emit(msg):
    sys.stdout.write("@@VS " + json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main():
    with open(sys.argv[1], encoding="utf-8") as f:
        job = json.load(f)
    from videostudio.engine import pipeline
    try:
        pipeline.generate(job, emit)
    except pipeline.Cancelled:
        emit({"stage": "cancelled"})
        sys.exit(3)
    except Exception as e:
        import torch
        oom = isinstance(e, torch.cuda.OutOfMemoryError) or "out of memory" in str(e).lower()
        emit({"stage": "error", "error": f"{type(e).__name__}: {e}", "oom": oom, "trace": traceback.format_exc()[-3000:]})
        sys.exit(2)


if __name__ == "__main__":
    main()

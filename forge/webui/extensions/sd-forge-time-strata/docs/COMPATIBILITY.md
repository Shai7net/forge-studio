# Compatibility and optional setup

## Verification scope

The development machine uses Windows and Python 3.10.11. The author's example is a 16-second H.264 export from their existing installation. It demonstrates that installation's workflow; it does not establish that every Forge fork or model works.

Current Forge and Neo sources were checked for `on_ui_tabs`, `on_ui_settings`, `on_app_started`, `unload_all_models()` and `soft_empty_cache()`. The modern and legacy memory adapters have isolated tests, including fallback and selection of an already-loaded backend.

References checked on 2026-10-19:

- [Forge callbacks](https://github.com/lllyasviel/stable-diffusion-webui-forge/blob/main/modules/script_callbacks.py) and [memory management](https://github.com/lllyasviel/stable-diffusion-webui-forge/blob/main/backend/memory_management.py).
- [Neo callbacks](https://github.com/Haoming02/sd-webui-forge-classic/blob/neo/modules/script_callbacks.py) and [memory management](https://github.com/Haoming02/sd-webui-forge-classic/blob/neo/backend/memory_management.py).

Full UI, AI extraction and Deforum runs on clean Forge 2 and Neo installations have **not yet been verified**. Standalone tests do not replace these runs. Other forks preserving the interfaces may work but are unverified.

## Optional background removal

Use **the Python used by your Forge installation**. On a typical Windows installation:

```powershell
.\venv\Scripts\python.exe -m pip install onnxruntime
```

On a typical Linux/macOS installation:

```bash
./venv/bin/python -m pip install onnxruntime
```

This enables CPU ONNX Runtime for direct BiRefNet inference. If your environment already provides working ONNX Runtime, keep it. CUDA acceleration requires an `onnxruntime-gpu` version compatible with CUDA/cuDNN/PyTorch; use the [official compatibility table](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html#requirements). Choose one ONNX Runtime distribution rather than installing CPU and GPU distributions together.

BiRefNet runs directly through ONNX Runtime; models download on first use into `models/time-strata`. **Auto** uses alpha when present, then a colour key for a flat backdrop, otherwise the best ready image model (falling back to `u2net`). Select a BiRefNet model explicitly to download and use it.

Other image models use `rembg`, installed separately with the same Python. Check [rembg's requirements](https://github.com/danielgatis/rembg#requirements) and choose a release compatible with your Forge Python. The package resolver can select a compatible release; changing Forge's Python is a separate host maintenance task. Older rembg releases may expose fewer models.

RVM files are **not automatically downloaded**. Obtain the matching ONNX export from [Robust Video Matting](https://github.com/PeterL1n/RobustVideoMatting), check its license and place it in `models/time-strata`:

| UI model | Filename |
| --- | --- |
| `rvm` | `rvm_resnet50_fp32.onnx` |
| `rvm-fast` | `rvm_mobilenetv3_fp32.onnx` |

RVM expects consecutive frames and targets human video matting. Extraction quality varies with footage; clothes resembling the backdrop may need another method.

## Deforum and model families

Time Strata finds enabled Deforum extensions through Forge's registry, including custom folder names. It expects `scripts/deforum_api.py`, `JobStatusTracker` and `run_deforum_batch`. A folder alone does not prove API compatibility.

Defaults target LCM/SD 1.5. The corresponding checkpoint, LCM LoRA, sampler and OpenPose assets must exist, or settings must be changed. SDXL and newer model families require support from the installed Deforum and ControlNet versions. Flux, Qwen, Z-Image and other host-supported models are not automatically supported by this Deforum workflow.

## Manual host validation

Record each host's repository, branch and commit, then check:

- [ ] Installation completes without changing Torch, Gradio or CUDA.
- [ ] The tab creates a project and restores it after restart.
- [ ] Transparent images produce a chain without matting packages.
- [ ] A flat-background video works with colour keying.
- [ ] AI matting works with the intended provider, releases its model, and permits subsequent Forge image generation.
- [ ] Camera presets, navigation, time modes and cutting plane render correctly.
- [ ] An H.264 export plays with expected orientation, timing and music.
- [ ] NVENC works when supported, or ordinary H.264 is selected.
- [ ] Optional Deforum generates a separate chain with pose guidance and restores any temporary DeforumationQT switch.

Include these results in compatibility reports. This checklist describes host validation to perform, not passed checks on every fork.

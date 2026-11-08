# Third-party software and models

Forge Studio is a distribution of other people's work plus its own extensions. Thank you to all of them.

## In this repository

| Component | Author | License | Notes |
|---|---|---|---|
| [Stable Diffusion WebUI Forge](https://github.com/lllyasviel/stable-diffusion-webui-forge) (f0.0.17) | lllyasviel and contributors | AGPL-3.0 | `forge/webui`, with Forge Studio's changes (startup app window, FIBO/FLUX 3 hooks...) |
| [Stable Diffusion WebUI](https://github.com/AUTOMATIC1111/stable-diffusion-webui) | AUTOMATIC1111 and contributors | AGPL-3.0 | Forge's base |
| [Deforum for Forge](https://github.com/deforum-art/sd-forge-deforum) | Deforum LLC and contributors | AGPL-3.0 | `extensions/sd-forge-deforum`, extended: Audio Keyframes, Map Key, DeforumationQT switch, live control |
| [Time Strata](https://github.com/Shai7net/sd-forge-time-strata) | Shai Tiri | MIT | `extensions/sd-forge-time-strata` |
| Forge Studio's extensions: Video Studio, Forge Studio editor, Simulation 360, FLUX 3, FIBO 2.0, prompt writer (hebrew-translator), Tab Presets, Hebrew UI, Navigation Bookmarks, Gallery Generate, RTX Video Super Resolution | Shai Tiri, written with AI coding assistants | AGPL-3.0 | `extensions/` |

Some of Forge Studio's extensions carry small copies of other projects' code (for example DWPose in Video Studio,
from Forge's ControlNet preprocessors, Apache-2.0); each keeps its origin noted in the file.

## Fetched by the installer

Extensions, at the exact commits Forge Studio was tested with (`installer/extensions.json`). Where Forge Studio
changed one, its change is applied as a patch (`installer/patches`) on top of the author's own code:

| Extension | Source (commit) | Forge Studio's change |
|---|---|---|
| --sd-webui-ar-plusplus | [altoiddealer/--sd-webui-ar-plusplus](https://github.com/altoiddealer/--sd-webui-ar-plusplus) (`8b900cd274`) | none |
| StyleSelectorXL | [ahgsql/StyleSelectorXL](https://github.com/ahgsql/StyleSelectorXL) (`d538b0c475`) | none |
| Wonder3D | [xxlong0/Wonder3D](https://github.com/xxlong0/Wonder3D) (`d894f827aa`) | none |
| asymmetric-tiling-sd-webui | [tjm35/asymmetric-tiling-sd-webui](https://github.com/tjm35/asymmetric-tiling-sd-webui) (`b4470ddc38`) | [patch](installer/patches/asymmetric-tiling-sd-webui.patch) |
| infinite-zoom-automatic1111-webui | [v8hid/infinite-zoom-automatic1111-webui](https://github.com/v8hid/infinite-zoom-automatic1111-webui) (`60adae2252`) | [patch](installer/patches/infinite-zoom-automatic1111-webui.patch) |
| next-view | [NextDiffusion/next-view](https://github.com/NextDiffusion/next-view) (`287e21d38e`) | none |
| sd-dynamic-prompts | [adieyal/sd-dynamic-prompts](https://github.com/adieyal/sd-dynamic-prompts) (`de056ff8d8`) | none |
| sd-extension-system-info | [vladmandic/sd-extension-system-info](https://github.com/vladmandic/sd-extension-system-info) (`9942ea11aa`) | none |
| sd-forge-animatediff | [continue-revolution/sd-forge-animatediff](https://github.com/continue-revolution/sd-forge-animatediff) (`b20f7519a1`) | [patch](installer/patches/sd-forge-animatediff.patch) |
| sd-webui-ar | [alemelis/sd-webui-ar](https://github.com/alemelis/sd-webui-ar) (`3973c86a7a`) | none |
| sd-webui-aspect-ratio-helper | [thomasasfk/sd-webui-aspect-ratio-helper](https://github.com/thomasasfk/sd-webui-aspect-ratio-helper) (`09096711a8`) | none |
| sd-webui-cleaner | [novitalabs/sd-webui-cleaner](https://github.com/novitalabs/sd-webui-cleaner) (`d89d173a01`) | [patch](installer/patches/sd-webui-cleaner.patch) |
| sd-webui-controlnet-evaclip | [huchenlei/sd-webui-controlnet-evaclip](https://github.com/huchenlei/sd-webui-controlnet-evaclip) (`39966e79a5`) | [patch](installer/patches/sd-webui-controlnet-evaclip.patch) |
| sd-webui-live-portrait | [dimitribarbot/sd-webui-live-portrait](https://github.com/dimitribarbot/sd-webui-live-portrait) (`2d990f5382`) | [patch](installer/patches/sd-webui-live-portrait.patch) |
| sd-webui-panorama-viewer | [GeorgLegato/sd-webui-panorama-viewer](https://github.com/GeorgLegato/sd-webui-panorama-viewer) (`aa2a238136`) | none |
| sd-webui-reactor | [Gourieff/sd-webui-reactor-sfw](https://github.com/Gourieff/sd-webui-reactor-sfw) (`0788c549de`) | [patch](installer/patches/sd-webui-reactor.patch) |
| sd-webui-replacer | [light-and-ray/sd-webui-replacer](https://github.com/light-and-ray/sd-webui-replacer) (`aa46106a1d`) | none |
| sd-webui-segment-anything | [continue-revolution/sd-webui-segment-anything](https://github.com/continue-revolution/sd-webui-segment-anything) (`982138cfb5`) | none |
| sd_web_ui_preset_utils | [Gerschel/sd_web_ui_preset_utils](https://github.com/Gerschel/sd_web_ui_preset_utils) (`e301734567`) | none |
| stable-diffusion-webui-images-browser | [AlUlkesh/stable-diffusion-webui-images-browser](https://github.com/AlUlkesh/stable-diffusion-webui-images-browser) (`88bfbe3997`) | none |
| stable-diffusion-webui-prompt-travel | [Kahsolt/stable-diffusion-webui-prompt-travel](https://github.com/Kahsolt/stable-diffusion-webui-prompt-travel) (`9c532f3487`) | [patch](installer/patches/stable-diffusion-webui-prompt-travel.patch) |

Tools:

| Tool | Author | License |
|---|---|---|
| [DeforumationQT](https://github.com/Rakile/DeforumationQT) | Rakile | AGPL-3.0 |
| [llama.cpp](https://github.com/ggml-org/llama.cpp) | ggml-org | MIT |
| [FFmpeg](https://ffmpeg.org) 7.1.1, [gyan.dev](https://www.gyan.dev/ffmpeg/builds/) essentials build | FFmpeg developers, build by Gyan Doshi | GPL-3.0 (its `LICENSE.txt` is kept in `system\ffmpeg`) |
| [uv](https://github.com/astral-sh/uv) | Astral | MIT / Apache-2.0 |
| [Python](https://www.python.org) | Python Software Foundation | PSF |
| [MinGit](https://github.com/git-for-windows/git) (only when git is missing) | Git for Windows | GPL-2.0 |
| [ComfyUI](https://github.com/Comfy-Org/ComfyUI) (FIBO 2.0's runtime, when FIBO is chosen) | Comfy Org | GPL-3.0 |
| Python packages (`installer/requirements-lock.txt`) | their authors | their own licenses |

## Models

Every model is downloaded from its authors' own page; its license is listed in `installer/models.json`. Notably:

- **FLUX 3 Image Klein**: Black Forest Labs, FLUX Kommunity License (accept it on Hugging Face before downloading).
- **FIBO 2.0**: Bria, CC BY-NC 4.0 (non-commercial; commercial use needs Bria's agreement).
- **Gemma 4**: Google, Apache-2.0. **Wan 2.2**: Alibaba Wan team, Apache-2.0.
- **Stable Diffusion 1.5 / SDXL checkpoints, LoRAs and ControlNets**: CreativeML OpenRAIL-M / OpenRAIL++ and similar.

You are responsible for using each model within its license.

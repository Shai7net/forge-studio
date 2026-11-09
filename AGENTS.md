# Installing Forge Studio with an AI agent

Instructions for AI coding agents (Claude Code, Codex, Cursor, Gemini...) asked to install or repair Forge Studio
on a user's Windows PC. Read the whole file before running anything.

## Before installing

1. Confirm the machine: Windows 10/11 64-bit, an NVIDIA GPU (`nvidia-smi` works), driver 560 or newer, and free
   disk space on the target drive (about 15 GB plus the models: see `installer\models.json` presets).
2. Ask the user which models they want if they did not say: `minimal`, `recommended` (default), `video`, `all`,
   or a comma list of group ids (`python installer\download_models.py --list` shows them with sizes; it needs only
   Python's standard library).
3. FLUX 3 is gated on Hugging Face. Only download it with a token the user gives you after accepting the license
   themselves at https://huggingface.co/black-forest-labs/flux-3-image-klein-comfy . Never create accounts or accept
   licenses on the user's behalf. Pass the token on the command line or in `HF_TOKEN`; do not write it to any file.
4. Install into a short path without non-ASCII characters (e.g. `C:\AI\ForgeStudio`).

## Install

From the Forge Studio folder (cmd or PowerShell):

```bat
install.bat -Yes -Models recommended
```

- `-Yes` never waits for input and accepts every default; the exit code is `0` on success, `1` on failure.
- Other options: `-HfToken <token>`, `-NoShortcut`, `-NoDeforumationQT`, `-NoPromptWriter`, `-ModelsOnly`
  (documented at the top of `installer\install.ps1`).
- It can take 20-60 minutes (PyTorch alone is ~2.5 GB, models come on top). Run it as a background or long-running
  command and watch `installer\install.log`.
- It is safe to run again: finished steps are skipped, downloads resume.

Everything is installed inside the folder: `system\` (uv, Python 3.10.11, MinGit if git was missing, ffmpeg,
llama.cpp, caches), `forge\webui\venv` (Forge's environment), `DeforumationQT\`. Nothing is installed system-wide;
the only thing outside the folder is the desktop shortcut.

## Verify

1. Start the server without opening a window (cmd):
   ```bat
   set SD_WEBUI_RESTARTING=1
   "Forge Studio.bat"
   ```
   Without the first line it also opens the app window for the user. Wait for `Running on local URL:
   http://127.0.0.1:7860` in the output (1-5 minutes; the first start is the slowest).
2. Check the API: `curl http://127.0.0.1:7860/sdapi/v1/sd-models` lists the downloaded checkpoints, and
   `curl http://127.0.0.1:7860/sdapi/v1/scripts` lists the extensions' scripts.
3. Optional render test (SD 1.5 must be downloaded):
   ```bat
   curl -X POST http://127.0.0.1:7860/sdapi/v1/txt2img -H "Content-Type: application/json" -d "{\"prompt\":\"a red bicycle\",\"steps\":8,\"width\":512,\"height\":512}"
   ```
4. Errors while loading extensions are printed as `*** Error loading script: ...` in the server output; report them
   to the user with the lines around them.

Stop the server when you are done testing, unless the user wants it running: close its console, or end the
`python.exe` processes whose command line contains `launch.py` **in this folder only** (other Python programs on the
PC are the user's).

## Repairing

- Read `installer\install.log` first; the last `ERROR:` line names the failed step.
- Packages: `installer\install.ps1` reinstalls missing ones from `installer\requirements-lock.txt` with uv; do not
  upgrade packages in `forge\webui\venv` by hand (Forge needs these exact versions).
- A third-party extension that is broken can be deleted from `forge\webui\extensions\<name>`; the next `install.bat`
  fetches it again at the pinned commit with Forge Studio's patch. The installer only manages folders that hold its
  `.forge-studio` marker (written once the fetch and patch are complete); a folder without it is treated as the
  user's own and left alone, and a leftover `<name>.part` folder is an interrupted fetch that the next run removes.
- Models: `download-models.bat -Models <group>` resumes and verifies them.

## Translating the interface into another language

The interface is English, with a built-in Hebrew translation. To add a language the user asks for:

1. Read `forge\webui\extensions\forge-hebrew-ui\translation-kit\README.md`; every `part-*.json` there carries its
   own rules in `"instructions"`.
2. Translate each part by those rules: change only the values of `"texts"` and `"blocks"`, never a key; fill in
   `"language"` (its own name), `"code"` (for example `es`) and `"direction"` (`rtl` for right-to-left scripts).
3. Save the results as `forge\webui\extensions\forge-hebrew-ui\languages\<code>\part-NN.json` (valid JSON, UTF-8).
4. Check: with Forge Studio running, `GET http://127.0.0.1:7860/forge-ui/languages` lists the language with how
   many of the kit's texts it covers; the language button's ▾ menu shows it. Missing texts stay in English.

## Do not

- Do not move models, outputs or settings the user made (`forge\webui\models`, `outputs`, `config.json`,
  `styles.csv`, `tab-presets`, `forge-studio`, `simulation-360`, `time-strata`).
- Do not kill processes by image name (`taskkill /IM python.exe`): the user may run other Python programs.
- Do not publish, upload or share the user's outputs or settings.

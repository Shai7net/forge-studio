<#
Forge Studio installer (Windows 10/11, NVIDIA GPU).

  install.bat                                   interactive
  install.bat -Yes -Models recommended          no questions (for scripts and AI agents)
  install.bat -Yes -Models all -HfToken hf_...  everything, FLUX 3 included (gated on Hugging Face)
  install.bat -ModelsOnly                       only the model menu (same as download-models.bat)

Options:
  -Yes               accept every default, never ask
  -Models <x>        minimal | recommended | video | all | none | group,group,...  (see installer\models.json)
  -HfToken <t>       Hugging Face token for gated models (or set HF_TOKEN)
  -NoShortcut        no desktop shortcut
  -NoDeforumationQT  skip DeforumationQT (Deforum's live control panel)
  -NoPromptWriter    skip llama.cpp (the prompt writer's engine)
  -ModelsOnly        skip everything but the model download
  -AllowNoGpu        go on without an NVIDIA card (for testing the setup itself; nothing will render)
Everything goes into this folder (Python, git, caches included); nothing is installed system-wide.
A log is written to installer\install.log.
#>
param(
    [switch]$Yes,
    [string]$Models = "",
    [string]$HfToken = $env:HF_TOKEN,
    [switch]$NoShortcut,
    [switch]$NoDeforumationQT,
    [switch]$NoPromptWriter,
    [switch]$ModelsOnly,
    [switch]$AllowNoGpu
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
# a repository that is gone must fail at once, not wait for a GitHub username nobody will type
$env:GIT_TERMINAL_PROMPT = "0"
$env:GCM_INTERACTIVE = "never"

$Root = Split-Path -Parent $PSScriptRoot
$Installer = $PSScriptRoot
$Webui = Join-Path $Root "forge\webui"
$System = Join-Path $Root "system"
$Venv = Join-Path $Webui "venv"
$VenvPy = Join-Path $Venv "Scripts\python.exe"
$PythonVersion = "3.10.11"
$LlamaRelease = "b9553"
$MinGitUrl = "https://github.com/git-for-windows/git/releases/download/v2.50.1.windows.1/MinGit-2.50.1-64-bit.zip"
$UvUrl = "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip"
$DeforumationQT = @{ url = "https://github.com/Rakile/DeforumationQT.git"; commit = "ad69b619d2" }
# ffmpeg + ffprobe (gyan.dev essentials build, the one Forge Studio is made with); LivePortrait, Infinite Zoom and
# Forge Studio need ffprobe, which the ffmpeg bundled in the Python packages does not have
$FfmpegUrl = "https://github.com/GyanD/codexffmpeg/releases/download/7.1.1/ffmpeg-7.1.1-essentials_build.zip"
$FfmpegSha256 = "04861d3339c5ebe38b56c19a15cf2c0cc97f5de4fa8910e4d47e5e6404e4a2d4"

New-Item -ItemType Directory -Force -Path $System | Out-Null
try { Start-Transcript -Path (Join-Path $Installer "install.log") -Append | Out-Null } catch {}

# everything this installer runs finds our own tools first, and keeps its caches on this drive
$env:UV_CACHE_DIR = Join-Path $System "uv-cache"
$env:UV_PYTHON_INSTALL_DIR = Join-Path $System "python"
$env:UV_LINK_MODE = "hardlink"
$env:PIP_CACHE_DIR = Join-Path $System "pip-cache"
$env:PATH = "$System\uv;$System\git\cmd;$System\llama.cpp;$System\ffmpeg;$env:PATH"

function Step($text) { Write-Host ""; Write-Host "== $text" -ForegroundColor Cyan }
function Info($text) { Write-Host "   $text" }
function Warn($text) { Write-Host "   ! $text" -ForegroundColor Yellow }
function Fail($text) { Write-Host ""; Write-Host "ERROR: $text" -ForegroundColor Red; try { Stop-Transcript | Out-Null } catch {}; exit 1 }

function Ask($question, $default) {
    if ($Yes) { return $default }
    $hint = "y/N"; if ($default) { $hint = "Y/n" }
    $answer = Read-Host "   $question [$hint]"
    if ([string]::IsNullOrWhiteSpace($answer)) { return $default }
    return $answer.Trim().ToLower().StartsWith("y")
}

function Get-File($url, $out) {
    for ($i = 1; $i -le 4; $i++) {
        try { Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $out -Headers @{ "User-Agent" = "ForgeStudio-installer" }; return }
        catch { Warn "download failed ($($_.Exception.Message)), try $i of 4"; Start-Sleep -Seconds (3 * $i) }
    }
    Fail "could not download $url"
}

function Expand-Into($zip, $dest) {
    if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }
    Expand-Archive -Path $zip -DestinationPath $dest -Force
    Remove-Item -Force $zip
}

function Run($exe, [string[]]$arguments, $what) {
    & $exe @arguments
    if ($LASTEXITCODE -ne 0) { Fail "$what failed (exit code $LASTEXITCODE). See installer\install.log" }
}

Write-Host ""
Write-Host "  Forge Studio - setup" -ForegroundColor Green
Write-Host "  folder: $Root"
Write-Host "  (first version: works, still experimental)"

# ------------------------------------------------------------------------------------------------ checks
if (-not $ModelsOnly) {
    Step "Checking this PC"
    if (-not [Environment]::Is64BitOperatingSystem) { Fail "Forge Studio needs 64-bit Windows 10 or 11." }
    $os = [Environment]::OSVersion.Version
    Info "Windows $($os.Major).$($os.Minor) build $($os.Build)"
    if ($os.Major -lt 10) { Fail "Forge Studio needs Windows 10 or 11." }

    $smi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
    if (-not $smi) {
        Warn "No NVIDIA GPU / driver found (nvidia-smi). Forge Studio needs an NVIDIA card with 8 GB or more and a recent driver."
        if (-not $AllowNoGpu -and -not (Ask "Continue anyway?" $false)) { exit 1 }
    } else {
        $gpu = (& nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader,nounits | Select-Object -First 1).Split(",")
        $name = $gpu[0].Trim(); $vram = [int]$gpu[1].Trim(); $driver = $gpu[2].Trim()
        Info "GPU: $name, $([math]::Round($vram / 1024, 1)) GB, driver $driver"
        if ($vram -lt 7500) { Warn "Less than 8 GB of video memory: SD 1.5 works; video, FLUX 3 and FIBO may not." }
        if ([double]($driver.Split(".")[0]) -lt 560) {
            Warn "The driver is older than 560: PyTorch with CUDA 12.6 needs 560 or newer. Update it from nvidia.com (Game Ready or Studio)."
            if (-not (Ask "Continue anyway?" $false)) { exit 1 }
        }
    }

    $drive = (Get-Item $Root).PSDrive
    $freeGb = [math]::Round((Get-PSDrive $drive.Name).Free / 1GB, 1)
    Info "Free on drive $($drive.Name): $freeGb GB (Forge Studio itself takes ~15 GB; models come on top)"
    if ($freeGb -lt 20) {
        Warn "Less than 20 GB free on this drive."
        if (-not (Ask "Continue anyway?" $false)) { exit 1 }
    }
    if ($Root -match "[^\x00-\x7F]") { Warn "The folder path has non-English letters; some tools break on such paths. A path like C:\AI\ForgeStudio is safest." }
    try {
        $long = (Get-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" -Name LongPathsEnabled -ErrorAction Stop).LongPathsEnabled
        if ($long -ne 1 -and $Root.Length -gt 40) { Warn "Windows long paths are off and this folder path is long; if a step fails with 'path too long', move the folder closer to the drive root (e.g. C:\AI\ForgeStudio)." }
    } catch {}

    # -------------------------------------------------------------------------------------------- tools
    Step "Tools (kept inside this folder)"
    if (-not (Test-Path "$System\uv\uv.exe")) {
        Info "uv (Python and package manager)"
        $zip = Join-Path $System "uv.zip"; Get-File $UvUrl $zip; Expand-Into $zip "$System\uv"
    }
    Info ("uv " + (& "$System\uv\uv.exe" --version))

    $git = Get-Command git -ErrorAction SilentlyContinue
    if (-not $git) {
        Info "git (MinGit)"
        $zip = Join-Path $System "git.zip"; Get-File $MinGitUrl $zip; Expand-Into $zip "$System\git"
    }
    Info ("git " + ((& git --version) -replace "git version ", ""))

    Step "Python $PythonVersion"
    # --no-bin / --no-registry: no python.exe in the user's PATH folder and no registry entry; it stays in system\python
    Run "$System\uv\uv.exe" @("python", "install", $PythonVersion, "--no-bin", "--no-registry") "Installing Python"

    # -------------------------------------------------------------------------------------- environment
    Step "Forge's Python environment (PyTorch 2.6 + CUDA 12.6 and ~240 packages, the exact versions Forge Studio was built with)"
    if (-not (Test-Path $VenvPy)) {
        Run "$System\uv\uv.exe" @("venv", $Venv, "--python", $PythonVersion, "--seed") "Creating the environment"
    }
    $lock = Join-Path $Installer "requirements-lock.txt"
    $buildPins = Join-Path $Installer "build-constraints.txt"
    # --no-deps: the lock is the complete, working environment, installed exactly as it is. Re-resolving refuses it:
    # A1111/Forge pin protobuf 3.20.3, pillow 9.5 and opencv 4.8, which some packages declare too old although they
    # run with them (pip check lists 7 such declarations).
    Run "$System\uv\uv.exe" @("pip", "install", "--python", $VenvPy, "-r", $lock, "--no-deps", "--index-strategy", "unsafe-best-match", "--build-constraints", $buildPins) "Installing packages"
    if (-not ((Test-Path "$System\ffmpeg\ffmpeg.exe") -and (Test-Path "$System\ffmpeg\ffprobe.exe"))) {
        $zip = Join-Path $System "ffmpeg.zip"
        Get-File $FfmpegUrl $zip
        if ((Get-FileHash -Algorithm SHA256 $zip).Hash.ToLower() -ne $FfmpegSha256) { Remove-Item -Force $zip; Fail "the ffmpeg download is damaged (sha256 differs); run install.bat again" }
        $tmp = Join-Path $System "ffmpeg.tmp"
        Expand-Into $zip $tmp
        New-Item -ItemType Directory -Force -Path "$System\ffmpeg" | Out-Null
        $bin = Get-ChildItem -Path $tmp -Recurse -Directory -Filter bin | Select-Object -First 1
        foreach ($f in @("ffmpeg.exe", "ffprobe.exe")) { Copy-Item -Force (Join-Path $bin.FullName $f) "$System\ffmpeg\$f" }
        $lic = Get-ChildItem -Path $tmp -Recurse -File -Filter LICENSE | Select-Object -First 1
        if ($lic) { Copy-Item -Force $lic.FullName "$System\ffmpeg\LICENSE.txt" }
        Remove-Item -Recurse -Force $tmp
    }
    Info "ffmpeg + ffprobe: $System\ffmpeg"

    # --------------------------------------------------------------------------------------- extensions
    Step "Extensions by other authors (fetched at the exact versions Forge Studio uses)"
    $manifest = Get-Content -Raw -Encoding UTF8 (Join-Path $Installer "extensions.json") | ConvertFrom-Json
    # Each extension Forge Studio put in place carries two files of ours: .forge-studio (the version it is at) and
    # .forge-studio.patch (our changes, kept so an update can take them out again before moving to a new version).
    # A fetch is made in "<name>.part" and renamed only when complete, so a half-done fetch never looks finished.
    foreach ($ext in $manifest.extensions) {
        $dir = Join-Path $Webui ("extensions\" + $ext.name)
        $part = "$dir.part"
        $marker = Join-Path $dir ".forge-studio"
        $applied = Join-Path $dir ".forge-studio.patch"
        $patch = $null; $want = $ext.commit
        if ($ext.patch) { $patch = Join-Path $Installer $ext.patch; $want += " " + (Get-FileHash -Algorithm SHA256 $patch).Hash.ToLower() }
        if (Test-Path $part) { Remove-Item -Recurse -Force $part }

        if (Test-Path $marker) {
            if ((Get-Content -Raw -LiteralPath $marker).Trim() -eq $want) { Info "ok   $($ext.name)"; continue }
            # put in place by an older Forge Studio: take our old changes out, then move to the new version
            if (Test-Path $applied) {
                $clean = $true
                try { $null = & git -C $dir apply -R --check $applied 2>&1; $clean = ($LASTEXITCODE -eq 0) } catch { $clean = $false }
                if (-not $clean) { Warn "$($ext.name) was changed by hand; left as it is"; continue }
                Run "git" @("-C", $dir, "apply", "-R", $applied) "Taking the old changes out of $($ext.name)"
                Remove-Item -Force $applied
            }
            Info "new  $($ext.name)"
            Run "git" @("-C", $dir, "fetch", "--quiet", "origin") "Fetching $($ext.name)"
            Run "git" @("-C", $dir, "checkout", "--quiet", "-B", "forge-studio", $ext.commit) "Checking out $($ext.name)"
        }
        elseif (Test-Path $dir) {
            Warn "$($ext.name) is already there (not put there by this installer); left as it is"
            continue
        }
        else {
            Info "get  $($ext.name)"
            # files exactly as the author stored them (no line-ending conversion), so Forge Studio's patch fits
            Run "git" @("-c", "core.autocrlf=false", "clone", "--quiet", $ext.url, $part) "Fetching $($ext.name)"
            Run "git" @("-C", $part, "config", "core.autocrlf", "false") "Configuring $($ext.name)"
            Run "git" @("-C", $part, "checkout", "--quiet", "-B", "forge-studio", $ext.commit) "Checking out $($ext.name)"
            Add-Content -Encoding ASCII -LiteralPath (Join-Path $part ".git\info\exclude") -Value "/.forge-studio`n/.forge-studio.patch"
            $dir = $part
        }
        if ($patch) {
            Run "git" @("-C", $dir, "apply", "--whitespace=nowarn", $patch) "Applying Forge Studio's changes to $($ext.name)"
            Copy-Item -Force -LiteralPath $patch -Destination (Join-Path $dir ".forge-studio.patch")
        }
        # -LiteralPath/-Value spelled out: Windows PowerShell 5.1 silently writes nothing for a positional path whose
        # name starts with a dot
        Set-Content -Encoding ASCII -NoNewline -LiteralPath (Join-Path $dir ".forge-studio") -Value $want
        if ($dir -eq $part) { Rename-Item $part $ext.name }
    }

    # ----------------------------------------------------------------------------------- first settings
    Step "Settings"
    foreach ($f in @("config.json", "styles.csv", "forge-navigation-bookmarks.json")) {
        $dest = Join-Path $Webui $f
        if (-not (Test-Path $dest)) { Copy-Item (Join-Path $Installer "defaults\$f") $dest; Info "default $f" }
        else { Info "kept your $f" }
    }
    & $VenvPy -c "import json,sys; p=sys.argv[1]; d=json.load(open(p,encoding='utf-8-sig')); d['deforum_ffmpeg_location']=sys.argv[2]; json.dump(d,open(p,'w',encoding='utf8'),ensure_ascii=False,indent=4)" (Join-Path $Webui "config.json") "$System\ffmpeg\ffmpeg.exe"

    # ------------------------------------------------------------------------------------- DeforumationQT
    if (-not $NoDeforumationQT) {
        Step "DeforumationQT (live control panel for Deforum: camera, prompts, audio, joystick)"
        $dqt = Join-Path $Root "DeforumationQT"
        if (-not (Test-Path (Join-Path $dqt "mediator.py"))) {
            Run "git" @("clone", "--quiet", $DeforumationQT.url, $dqt) "Fetching DeforumationQT"
            Run "git" @("-C", $dqt, "-c", "advice.detachedHead=false", "checkout", "--quiet", $DeforumationQT.commit) "Checking out DeforumationQT"
        }
        if (-not (Test-Path (Join-Path $dqt "venv\Scripts\python.exe"))) {
            Run "$System\uv\uv.exe" @("venv", (Join-Path $dqt "venv"), "--python", $PythonVersion, "--seed") "DeforumationQT environment"
        }
        Run "$System\uv\uv.exe" @("pip", "install", "--python", (Join-Path $dqt "venv\Scripts\python.exe"), "-r", (Join-Path $dqt "requirements_win.txt")) "DeforumationQT packages"
        Info "ready: Deforum tab > 'Open DeforumationQT'"
    }

    # --------------------------------------------------------------------------------- prompt writer engine
    if (-not $NoPromptWriter -and -not (Test-Path "$System\llama.cpp\llama-server.exe")) {
        Step "llama.cpp $LlamaRelease (runs the prompt writer's language model on the GPU through Vulkan)"
        $zip = Join-Path $System "llama.zip"
        Get-File "https://github.com/ggml-org/llama.cpp/releases/download/$LlamaRelease/llama-$LlamaRelease-bin-win-vulkan-x64.zip" $zip
        Expand-Into $zip "$System\llama.cpp"
    }

    # ------------------------------------------------------------------------- first start of Forge (offline part)
    Step "Preparing Forge (its own repositories and the extensions' first-run setup)"
    Push-Location $Webui
    $env:SD_WEBUI_RESTARTING = "1"
    & $VenvPy launch.py --exit --skip-torch-cuda-test
    $code = $LASTEXITCODE
    Pop-Location
    if ($code -ne 0) { Fail "Forge's first-run preparation failed (exit code $code)." }
}

# ------------------------------------------------------------------------------------------------- models
$catalog = Get-Content -Raw -Encoding UTF8 (Join-Path $Installer "models.json") | ConvertFrom-Json
$groupMb = @{}
foreach ($f in $catalog.files) { $groupMb[$f.group] = [double]$groupMb[$f.group] + [double]$f.mb }
function PresetGb($name) { $sum = 0; foreach ($g in $catalog.presets.$name) { $sum += $groupMb[$g] }; return [math]::Round($sum / 1024, 1) }

if (-not $Models) {
    if ($Yes) { $Models = "recommended" }
    else {
        Step "Models"
        Write-Host "   Which models should be downloaded now? (download-models.bat brings more later)"
        Write-Host "     1) Minimal      ~$(PresetGb 'minimal') GB   only the LCM + panorama LoRAs (bring your own checkpoints)"
        Write-Host "     2) Recommended  ~$(PresetGb 'recommended') GB   SD 1.5 + AnimateDiff + ControlNet + upscalers + prompt writer"
        Write-Host "     3) Video        ~$(PresetGb 'video') GB   Recommended + Wan 2.2 video + character replacement"
        Write-Host "     4) Everything   ~$(PresetGb 'all') GB  + SDXL + FIBO 2.0 + FLUX 3 (FLUX 3 needs a Hugging Face token)"
        Write-Host "     5) Choose group by group"
        Write-Host "     0) None now"
        $choice = Read-Host "   Your choice [2]"
        switch ($choice.Trim()) {
            "1" { $Models = "minimal" } "3" { $Models = "video" } "4" { $Models = "all" } "0" { $Models = "none" }
            "5" {
                $picked = @()
                foreach ($g in $catalog.groups) {
                    if ($g.always) { continue }
                    $gb = [math]::Round($groupMb[$g.id] / 1024, 1)
                    if (Ask "$($g.title) (~$gb GB)?" $false) { $picked += $g.id }
                }
                $Models = ($picked -join ",")
                if (-not $Models) { $Models = "minimal" }
            }
            default { $Models = "recommended" }
        }
    }
}

if ($Models -ne "none") {
    $wantFlux = ($Models -eq "all") -or ($Models -match "flux3")
    if ($wantFlux -and -not $HfToken -and -not $Yes) {
        Write-Host ""
        Write-Host "   FLUX 3 is gated: open https://huggingface.co/black-forest-labs/flux-3-image-klein-comfy , accept the"
        Write-Host "   license, then create a token at https://huggingface.co/settings/tokens (Read). Empty = skip FLUX 3."
        $HfToken = Read-Host "   Hugging Face token"
    }
    Step "Downloading models ($Models)"
    $dlArgs = @((Join-Path $Installer "download_models.py"))
    if ($catalog.presets.PSObject.Properties.Name -contains $Models) { $dlArgs += @("--preset", $Models) } else { $dlArgs += @("--groups", $Models) }
    if ($HfToken) { $env:HF_TOKEN = $HfToken }
    $py = $VenvPy; if (-not (Test-Path $py)) { $py = "python" }
    & $py @dlArgs
    if ($LASTEXITCODE -ne 0) { Warn "Some models did not finish. Run download-models.bat to resume them." }

    $wantFibo = ($Models -eq "all") -or ($Models -match "fibo")
    if ($wantFibo -and -not $ModelsOnly) {
        Step "FIBO 2.0 runtime (its own ComfyUI-based environment, Python 3.11 + PyTorch 2.9; ~4 GB)"
        Run "$System\uv\uv.exe" @("python", "install", "3.11", "--no-bin", "--no-registry") "Installing Python 3.11"
        $py311 = (& "$System\uv\uv.exe" python find 3.11).Trim()
        & $py311 (Join-Path $Webui "extensions\sd-forge-fibo2\fibo2\setup_runtime.py") install
        if ($LASTEXITCODE -ne 0) { Warn "The FIBO runtime did not install; the FIBO panel's 'Install/verify' button retries it." }
    }
}

# ---------------------------------------------------------------------------------------------- the checkpoint
$cfgPath = Join-Path $Webui "config.json"
if ((Test-Path $cfgPath) -and (Test-Path $VenvPy)) {
    & $VenvPy -c @"
import json, os, sys
p, models = sys.argv[1], sys.argv[2]
d = json.load(open(p, encoding='utf-8-sig'))
if not d.get('sd_model_checkpoint'):
    for name in ('DreamShaper_8_pruned.safetensors', 'Juggernaut-XL_v9_RunDiffusionPhoto_v2.safetensors', 'flux3-image-klein-int8-convrot.safetensors'):
        if os.path.isfile(os.path.join(models, name)):
            d['sd_model_checkpoint'] = name
            break
json.dump(d, open(p, 'w', encoding='utf8'), ensure_ascii=False, indent=4)
"@ $cfgPath (Join-Path $Webui "models\Stable-diffusion")
}

# ----------------------------------------------------------------------------------------------- shortcut
if (-not $ModelsOnly -and -not $NoShortcut) {
    Step "Desktop shortcut"
    $shell = New-Object -ComObject WScript.Shell
    $desktop = [Environment]::GetFolderPath("Desktop")
    $link = $shell.CreateShortcut((Join-Path $desktop "Forge Studio.lnk"))
    $link.TargetPath = Join-Path $Root "Forge Studio.bat"
    $link.WorkingDirectory = $Root
    $link.IconLocation = Join-Path $Root "forge studio.ico"
    $link.Description = "Forge Studio"
    $link.WindowStyle = 7
    $link.Save()
    Info "Forge Studio.lnk on the desktop"
}

Write-Host ""
Write-Host "  Forge Studio is installed." -ForegroundColor Green
if ($NoShortcut) { Write-Host "  Start it with 'Forge Studio.bat' (it opens in its own window)." }
else { Write-Host "  Start it from the desktop shortcut or 'Forge Studio.bat' (it opens in its own window)." }
Write-Host "  More models any time: download-models.bat"
try { Stop-Transcript | Out-Null } catch {}
if (-not $Yes -and -not $ModelsOnly) {
    if (Ask "Start Forge Studio now?" $true) { Start-Process -FilePath (Join-Path $Root "Forge Studio.bat") -WorkingDirectory $Root }
}
exit 0

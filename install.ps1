<#
.SYNOPSIS
  Install Utterlane Live on Windows (idempotent; safe to re-run).

.DESCRIPTION
  Everything is installed inside this folder:
    .tools\uv.exe   the uv package manager (https://github.com/astral-sh/uv), PATH untouched
    .venv\          Python 3.12 environment (uv downloads Python if it is missing)
  Models and voice profiles go to %LOCALAPPDATA%\Utterlane.

  NVIDIA GPU detected  -> PyTorch CUDA 12.8 + CUDA runtime for Whisper (fast)
  no NVIDIA GPU        -> CPU-only PyTorch (smaller download), Whisper on the CPU

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File install.ps1
  powershell -ExecutionPolicy Bypass -File install.ps1 -Cpu        # force CPU even with a GPU
  powershell -ExecutionPolicy Bypass -File install.ps1 -SkipModels -NoShortcut
#>
[CmdletBinding()]
param(
    [switch]$Cpu,
    [switch]$SkipModels,
    [switch]$NoShortcut
)

$ErrorActionPreference = "Stop"
if ($PSVersionTable.PSVersion.Major -lt 6) {
    # Windows PowerShell started from PowerShell 7 inherits a module path it cannot load from.
    $env:PSModulePath = @(
        (Join-Path ([Environment]::GetFolderPath("MyDocuments")) "WindowsPowerShell\Modules"),
        (Join-Path $env:ProgramFiles "WindowsPowerShell\Modules"),
        (Join-Path $env:SystemRoot "system32\WindowsPowerShell\v1.0\Modules")
    ) -join ";"
}
$Root = $PSScriptRoot
Set-Location $Root
function Step($text) { Write-Host "`n==> $text" -ForegroundColor Cyan }

Step "uv (package manager)"
$UvVersion = if ($env:UTTERLANE_UV_VERSION) { $env:UTTERLANE_UV_VERSION } else { "0.13.1" }
$Tools = Join-Path $Root ".tools"
$uv = Join-Path $Tools "uv.exe"
if (-not (Test-Path $uv)) {
    New-Item -ItemType Directory -Force $Tools | Out-Null
    $arch = if ($env:PROCESSOR_ARCHITECTURE -eq "ARM64") { "aarch64" } else { "x86_64" }
    $zip = Join-Path $Tools "uv.zip"
    $url = "https://github.com/astral-sh/uv/releases/download/$UvVersion/uv-$arch-pc-windows-msvc.zip"
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
    Expand-Archive -Path $zip -DestinationPath $Tools -Force
    Remove-Item $zip
    if (-not (Test-Path $uv)) {
        $found = Get-ChildItem $Tools -Recurse -Filter uv.exe | Select-Object -First 1
        if ($found) { Move-Item $found.FullName $uv -Force }
    }
}
& $uv --version

Step "Python 3.12 environment"
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    & $uv venv --python 3.12 .venv
    if ($LASTEXITCODE -ne 0) { throw "Could not create the Python environment." }
}

$gpu = (-not $Cpu) -and ($null -ne (Get-Command nvidia-smi -ErrorAction SilentlyContinue))
if ($gpu) {
    Step "Dependencies (NVIDIA GPU detected: CUDA build)"
    & $uv pip install --python .venv --torch-backend cu128 -e ".[cuda]"
} else {
    Step "Dependencies (CPU build)"
    & $uv pip install --python .venv --torch-backend cpu -e .
}
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }

if (-not $SkipModels) {
    Step "Models (hash-verified downloads)"
    & .venv\Scripts\utterlane.exe setup
    if ($LASTEXITCODE -ne 0) { Write-Warning "Model setup reported a problem; run '.venv\Scripts\utterlane.exe doctor'." }
}

if (-not $NoShortcut) {
Step "Desktop shortcut"
try {
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath("Desktop")) "Utterlane Live.lnk"))
    $link.TargetPath = Join-Path $Root "Utterlane.cmd"
    $link.WorkingDirectory = $Root
    $link.Save()
    Write-Host "Created 'Utterlane Live' on the desktop."
} catch {
    Write-Host "(Skipped the desktop shortcut.)"
}
}

Step "Done"
Write-Host "Start it with Utterlane.cmd (or: .venv\Scripts\utterlane.exe)" -ForegroundColor Green
if (-not (Test-Path ".env")) {
    Write-Host "Optional: copy .env.example to .env to choose the language, the voice and the AI 'brain'." -ForegroundColor Yellow
}

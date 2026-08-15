param([switch]$SkipNative)

$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
Set-Location $Repo

if (-not (Get-Command python -ErrorAction SilentlyContinue)) { throw "Python was not found on PATH" }
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) { throw "FFmpeg was not found on PATH" }
if (-not (Get-Command ffprobe -ErrorAction SilentlyContinue)) { throw "ffprobe was not found on PATH" }

if (-not (Test-Path .venv)) { python -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt

if (-not $SkipNative) {
    if (Get-Command make -ErrorAction SilentlyContinue) {
        make -C src/native_encoder
    } else {
        Write-Warning "make was not found; the Python reference encoder is ready, but the native encoder was not built."
    }
}

& .\.venv\Scripts\python.exe -m pytest -q

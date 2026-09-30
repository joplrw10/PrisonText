param(
    [switch]$WithDemucs,
    [switch]$SkipFfmpegInstall
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Test-Command([string]$Name) {
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$Program,
        [Parameter(Mandatory = $true)][string[]]$ArgumentList
    )
    & $Program @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        throw "$Program exited with code $LASTEXITCODE"
    }
}

Write-Host ""
Write-Host "PumpkinSync Windows setup" -ForegroundColor DarkYellow
Write-Host "=========================" -ForegroundColor DarkYellow

$pythonProgram = $null
$pythonPrefix = @()
if (Test-Command "py") {
    & py -3.11 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" 2>$null
    if ($LASTEXITCODE -eq 0) {
        $pythonProgram = "py"
        $pythonPrefix = @("-3.11")
    } else {
        & py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" 2>$null
        if ($LASTEXITCODE -eq 0) {
            $pythonProgram = "py"
            $pythonPrefix = @("-3")
        }
    }
}
if (-not $pythonProgram -and (Test-Command "python")) {
    & python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" 2>$null
    if ($LASTEXITCODE -eq 0) {
        $pythonProgram = "python"
    }
}
if (-not $pythonProgram) {
    throw "Python 3.11 or newer was not found. Install Python, enable the Python launcher/PATH option, then rerun this script. Python 3.11 or 3.12 is recommended when using Demucs."
}

if (-not (Test-Command "ffmpeg") -or -not (Test-Command "ffprobe")) {
    if (-not $SkipFfmpegInstall -and (Test-Command "winget")) {
        Write-Host "FFmpeg was not found. Installing the Gyan.FFmpeg package with WinGet..." -ForegroundColor Yellow
        Invoke-Checked -Program "winget" -ArgumentList @("install", "--id", "Gyan.FFmpeg", "-e", "--source", "winget", "--accept-package-agreements", "--accept-source-agreements")
        $wingetLinks = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Links"
        if (Test-Path $wingetLinks) {
            $env:PATH = "$wingetLinks;$env:PATH"
        }
    }
}
if (-not (Test-Command "ffmpeg") -or -not (Test-Command "ffprobe")) {
    throw "FFmpeg and FFprobe are required but are not currently on PATH. Install them, open a new PowerShell window, and rerun setup_windows.ps1. WinGet command: winget install --id Gyan.FFmpeg -e"
}

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Creating local Python environment..."
    & $pythonProgram @pythonPrefix -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "Could not create .venv" }
}

$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
Invoke-Checked -Program $venvPython -ArgumentList @("-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel")
if ($WithDemucs) {
    Write-Host "Installing PumpkinSync plus optional Demucs song-vocal isolation..."
    Invoke-Checked -Program $venvPython -ArgumentList @("-m", "pip", "install", "-e", ".[songs]")
} else {
    Write-Host "Installing PumpkinSync base application..."
    Invoke-Checked -Program $venvPython -ArgumentList @("-m", "pip", "install", "-e", ".")
}

New-Item -ItemType Directory -Force -Path (Join-Path $PSScriptRoot "data") | Out-Null
Write-Host ""
Write-Host "Setup complete." -ForegroundColor Green
Write-Host "Run run_windows.bat to open PumpkinSync."
Write-Host "For optional song vocal isolation later: .\setup_windows.ps1 -WithDemucs"

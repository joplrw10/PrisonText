$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$repository = "DanielSWolf/rhubarb-lip-sync"
$releaseApi = "https://api.github.com/repos/$repository/releases/latest"
Write-Host "Installing the latest official Rhubarb Lip Sync release from $releaseApi" -ForegroundColor DarkYellow

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "PumpkinSync's .venv was not found. Run setup_windows.ps1 first."
}

& $python "scripts\install_rhubarb.py" `
    --repository $repository `
    --destination "tools\rhubarb" `
    --platform windows
if ($LASTEXITCODE -ne 0) {
    throw "Rhubarb installation exited with code $LASTEXITCODE"
}

Write-Host "Rhubarb is ready. Restart PumpkinSync and check the Health panel." -ForegroundColor Green

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    Write-Error '[Voice Healer] Virtual environment not found. Create it with: python -m venv .venv'
}
& $python (Join-Path $PSScriptRoot 'skills\voice-healer\scripts\ui_server.py') --open

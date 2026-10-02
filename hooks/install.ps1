$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$gitDir = Join-Path $root ".git"
$source = Join-Path $PSScriptRoot "pre-commit"
$destination = Join-Path $gitDir "hooks\pre-commit"

if (-not (Test-Path -LiteralPath $gitDir -PathType Container)) {
    throw "Not a Git repository. Run 'git init' or clone the repository first."
}

if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
    throw "Tracked hook source is missing: $source"
}

$destinationDir = Split-Path -Parent $destination
New-Item -ItemType Directory -Force -Path $destinationDir | Out-Null
Copy-Item -LiteralPath $source -Destination $destination -Force
Write-Host "[Hooks] Installed pre-commit hook at $destination"

<#
.SYNOPSIS
    Installs PDK for Ignition Perspective on this machine.

.DESCRIPTION
    Gets the repo (clones it if this script is not running from a checkout), installs uv if it is missing,
    the Python dependencies (uv brings Python 3.12 if needed), extracts the schema bundle of the installed
    Perspective and downloads Playwright's Chromium. Safe to run again: it updates what is there.

    It never touches the gateway, ignition.conf or anything that needs admin rights; it prints those steps.

.EXAMPLE
    irm https://raw.githubusercontent.com/fcastel2002/pdk/main/install.ps1 | iex

.EXAMPLE
    .\install.ps1                       # from a clone

.NOTES
    Environment variables:
      PDK_DIR   where to clone (default: .\pdk in the current directory)
      PDK_REF   branch or tag to clone (default: main)
      PDK_REPO  repository URL (default: https://github.com/fcastel2002/pdk.git)
#>

$ErrorActionPreference = 'Stop'

function Step($text) { Write-Host "`n==> $text" -ForegroundColor Cyan }
function Fail($text) { Write-Host "pdk install: $text" -ForegroundColor Red; exit 1 }
function Has($command) { [bool](Get-Command $command -ErrorAction SilentlyContinue) }

$repoUrl = if ($env:PDK_REPO) { $env:PDK_REPO } else { 'https://github.com/fcastel2002/pdk.git' }
$ref = if ($env:PDK_REF) { $env:PDK_REF } else { 'main' }

# 1. The repo: this checkout, or a clone.
if ($PSScriptRoot -and (Test-Path (Join-Path $PSScriptRoot 'pdk\cli.py'))) {
    $root = $PSScriptRoot
    Step "Using the checkout at $root"
} else {
    if (-not (Has git)) { Fail 'git is required to clone PDK. Install it (winget install Git.Git) and run this again.' }
    $root = if ($env:PDK_DIR) { $env:PDK_DIR } else { Join-Path (Get-Location) 'pdk' }
    if (Test-Path (Join-Path $root '.git')) {
        Step "Updating $root"
        git -C $root pull --ff-only
        if ($LASTEXITCODE -ne 0) { Fail "git pull failed in $root (local changes?). Resolve it and run this again." }
    } elseif ((Test-Path $root) -and (Get-ChildItem $root -Force | Select-Object -First 1)) {
        Fail "$root exists and is not a PDK clone. Set PDK_DIR to another folder."
    } else {
        Step "Cloning $repoUrl ($ref) into $root"
        git clone -c core.longpaths=true --branch $ref $repoUrl $root
        if ($LASTEXITCODE -ne 0) { Fail 'git clone failed.' }
    }
}
Set-Location $root

# 2. uv (it also provides Python).
if (-not (Has uv)) {
    Step 'Installing uv (https://docs.astral.sh/uv/)'
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
    if (-not (Has uv)) { Fail 'uv was installed but is not on PATH. Open a new terminal and run this again.' }
}
Step "uv $((uv --version) -replace '^uv ', '')"

# 3. Python dependencies.
Step 'Installing the Python dependencies (uv sync)'
uv sync
if ($LASTEXITCODE -ne 0) { Fail 'uv sync failed.' }

# 4. Local setup and the full check: schemas, Chromium, then everything about the gateway.
Step 'Extracting the schemas and downloading Chromium (pdk doctor --fix)'
uv run pdk doctor --fix
$doctor = $LASTEXITCODE

Write-Host ''
if ($doctor -eq 0) {
    Write-Host 'PDK is installed and every check passed.' -ForegroundColor Green
} else {
    Write-Host 'PDK is installed. The checks above marked FAIL are the steps left, usually on the gateway:' -ForegroundColor Yellow
    Write-Host '  1. Create an empty Perspective project to use as the sandbox (e.g. pdk_sandbox).'
    Write-Host '  2. Add the ignition.conf lines that doctor printed (as admin) and restart the gateway.'
    Write-Host '  3. Create a gateway API key and save it to ~/.pdk/ignition-api-key.'
    Write-Host '  4. uv run pdk gateway install --restart'
    Write-Host '  5. uv run pdk doctor   (until everything is ok)'
    Write-Host 'Details: README.md, section "Gateway setup".'
}
Write-Host "`nNext: cd `"$root`" and open it with Claude Code (claude), or use the CLI: uv run pdk --help"

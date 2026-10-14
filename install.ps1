<#
.SYNOPSIS
    Installs PDK for Ignition Perspective on this machine.

.DESCRIPTION
    Installs uv if it is missing (uv brings its own Python: nothing else to install), then PDK as a command
    (`uv tool install pdk-perspective`): `pdk` and `pdk-mcp` work from any folder, with no clone and no git. It
    extracts the schema bundle of the installed Perspective, downloads Playwright's Chromium and sets up a folder
    for Claude Code (`pdk init`). Safe to run again: it upgrades PDK and refreshes that folder.

    It never touches the gateway, ignition.conf or anything that needs admin rights; it prints those steps.

.EXAMPLE
    irm https://raw.githubusercontent.com/fcastel2002/pdk/main/install.ps1 | iex

.NOTES
    Environment variables:
      PDK_PROJECT  the folder to set up for Claude Code (default: .\pdk-views in the current directory)
      PDK_SOURCE   what to install (default: pdk-perspective from PyPI; e.g. a wheel file or git+https://...)
#>

$ErrorActionPreference = 'Stop'

function Step($text) { Write-Host "`n==> $text" -ForegroundColor Cyan }
function Fail($text) { Write-Host "pdk install: $text" -ForegroundColor Red; exit 1 }
function Has($command) { [bool](Get-Command $command -ErrorAction SilentlyContinue) }

$source = if ($env:PDK_SOURCE) { $env:PDK_SOURCE } else { 'pdk-perspective' }
$project = if ($env:PDK_PROJECT) { $env:PDK_PROJECT } else { Join-Path (Get-Location) 'pdk-views' }

# 1. uv (it also provides Python).
if (-not (Has uv)) {
    Step 'Installing uv (https://docs.astral.sh/uv/)'
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
    if (-not (Has uv)) { Fail 'uv was installed but is not on PATH. Open a new terminal and run this again.' }
}
Step "uv $((uv --version) -replace '^uv ', '')"

# 2. PDK itself, as a command on PATH.
Step "Installing PDK ($source)"
uv tool install --upgrade $source
if ($LASTEXITCODE -ne 0 -and -not $env:PDK_SOURCE -and (Has git)) {
    Write-Host 'Not on PyPI yet: installing from GitHub.' -ForegroundColor Yellow
    uv tool install --upgrade 'git+https://github.com/fcastel2002/pdk'
}
if ($LASTEXITCODE -ne 0) { Fail 'uv tool install failed.' }
uv tool update-shell *> $null
$bin = (uv tool dir --bin).Trim()
$env:Path = "$bin;$env:Path"
$pdk = Join-Path $bin 'pdk.exe'
Step (& $pdk --version)

# 3. A folder for Claude Code: skill, subagents, MCP server, hook, CLAUDE.md.
Step "Setting up $project (pdk init)"
& $pdk init $project
if ($LASTEXITCODE -ne 0) { Write-Host "pdk init reported problems in $project (see above)." -ForegroundColor Yellow }

# 4. Local setup and the full check: schemas, Chromium, then everything about the gateway.
Step 'Extracting the schemas and downloading Chromium (pdk doctor --fix)'
& $pdk doctor --fix
$doctor = $LASTEXITCODE

Write-Host ''
if ($doctor -eq 0) {
    Write-Host 'PDK is installed and every check passed.' -ForegroundColor Green
} else {
    Write-Host 'PDK is installed. The checks above marked FAIL are the steps left, usually on the gateway:' -ForegroundColor Yellow
    Write-Host '  1. Create an empty Perspective project to use as the sandbox (e.g. pdk_sandbox).'
    Write-Host '  2. Add the ignition.conf lines that doctor printed (as admin) and restart the gateway.'
    Write-Host '  3. Create a gateway API key and save it to ~/.pdk/ignition-api-key.'
    Write-Host '  4. pdk gateway install --restart'
    Write-Host '  5. pdk doctor   (until everything is ok)'
    Write-Host 'Details: https://fcastel2002.github.io/pdk/installation.html'
}
Write-Host "`nNext: open `"$project`" with Claude Code (cd `"$project`"; claude). ``pdk --help`` works from any folder."
Write-Host 'Open a new terminal if `pdk` is not found.'

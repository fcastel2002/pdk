#!/bin/sh
# Installs PDK for Ignition Perspective on this machine (Linux and macOS).
#
# Installs uv if it is missing (uv brings its own Python: nothing else to install), then PDK as a command
# (`uv tool install pdk-perspective`): `pdk` and `pdk-mcp` work from any folder, with no clone and no git. It
# extracts the schema bundle of the installed Perspective, downloads Playwright's Chromium and sets up a folder
# for Claude Code (`pdk init`). Safe to run again: it upgrades PDK and refreshes that folder.
# It never touches the gateway, ignition.conf or anything that needs root; it prints those steps.
#
#   curl -LsSf https://raw.githubusercontent.com/fcastel2002/pdk/main/install.sh | sh
#
# Environment variables:
#   PDK_PROJECT  the folder to set up for Claude Code (default: ./pdk-views in the current directory)
#   PDK_SOURCE   what to install (default: pdk-perspective from PyPI; e.g. a wheel file or git+https://...)

set -eu

step() { printf '\n==> %s\n' "$1"; }
fail() { printf 'pdk install: %s\n' "$1" >&2; exit 1; }
has() { command -v "$1" >/dev/null 2>&1; }

SOURCE=${PDK_SOURCE:-pdk-perspective}
PROJECT=${PDK_PROJECT:-$(pwd)/pdk-views}

# 1. uv (it also provides Python).
if ! has uv; then
    step "Installing uv (https://docs.astral.sh/uv/)"
    has curl || fail "curl is required to install uv. Install uv yourself (https://docs.astral.sh/uv/) and run this again."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    PATH="$HOME/.local/bin:$PATH"
    export PATH
    has uv || fail "uv was installed but is not on PATH. Open a new terminal and run this again."
fi
step "$(uv --version)"

# 2. PDK itself, as a command on PATH.
step "Installing PDK ($SOURCE)"
if ! uv tool install --upgrade "$SOURCE"; then
    if [ -z "${PDK_SOURCE:-}" ] && has git; then
        echo "Not on PyPI yet: installing from GitHub."
        uv tool install --upgrade "git+https://github.com/fcastel2002/pdk" || fail "uv tool install failed."
    else
        fail "uv tool install failed."
    fi
fi
uv tool update-shell >/dev/null 2>&1 || true
BIN=$(uv tool dir --bin)
PATH="$BIN:$PATH"
export PATH
step "$(pdk --version)"

# 3. A folder for Claude Code: skill, subagents, MCP server, hook, CLAUDE.md.
step "Setting up $PROJECT (pdk init)"
pdk init "$PROJECT" || echo "pdk init reported problems in $PROJECT (see above)."

# 4. Local setup and the full check: schemas, Chromium, then everything about the gateway.
step "Extracting the schemas and downloading Chromium (pdk doctor --fix)"
doctor=0
pdk doctor --fix || doctor=$?

echo
if [ "$doctor" -eq 0 ]; then
    echo "PDK is installed and every check passed."
else
    echo "PDK is installed. The checks above marked FAIL are the steps left, usually on the gateway:"
    echo "  1. Create an empty Perspective project to use as the sandbox (e.g. pdk_sandbox)."
    echo "  2. Add the ignition.conf lines that doctor printed (as root) and restart the gateway."
    echo "  3. Create a gateway API key and save it to ~/.pdk/ignition-api-key."
    echo "  4. pdk gateway install --restart"
    echo "  5. pdk doctor   (until everything is ok)"
    echo "Details: https://fcastel2002.github.io/pdk/installation.html"
fi
if [ "$(uname -s)" = "Linux" ]; then
    echo
    echo "On Linux, Chromium may need system libraries: sudo \"$(uv tool dir)/pdk-perspective/bin/playwright\" install-deps chromium"
fi
printf '\nNext: open "%s" with Claude Code (cd "%s" && claude). `pdk --help` works from any folder.\n' "$PROJECT" "$PROJECT"
echo "Open a new terminal if pdk is not found."

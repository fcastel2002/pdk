#!/bin/sh
# Installs PDK for Ignition Perspective on this machine (Linux and macOS).
#
# Gets the repo (clones it if this script is not running from a checkout), installs uv if it is missing,
# the Python dependencies (uv brings Python 3.12 if needed), extracts the schema bundle of the installed
# Perspective and downloads Playwright's Chromium. Safe to run again: it updates what is there.
# It never touches the gateway, ignition.conf or anything that needs root; it prints those steps.
#
#   curl -LsSf https://raw.githubusercontent.com/fcastel2002/pdk/main/install.sh | sh
#   ./install.sh                         # from a clone
#
# Environment variables:
#   PDK_DIR   where to clone (default: ./pdk in the current directory)
#   PDK_REF   branch or tag to clone (default: main)
#   PDK_REPO  repository URL (default: https://github.com/fcastel2002/pdk.git)

set -eu

step() { printf '\n==> %s\n' "$1"; }
fail() { printf 'pdk install: %s\n' "$1" >&2; exit 1; }
has() { command -v "$1" >/dev/null 2>&1; }

REPO_URL=${PDK_REPO:-https://github.com/fcastel2002/pdk.git}
REF=${PDK_REF:-main}

# 1. The repo: this checkout, or a clone.
here=$(cd "$(dirname "$0")" 2>/dev/null && pwd || true)
if [ -n "$here" ] && [ -f "$here/pdk/cli.py" ]; then
    root=$here
    step "Using the checkout at $root"
else
    has git || fail "git is required to clone PDK. Install it and run this again."
    root=${PDK_DIR:-$(pwd)/pdk}
    if [ -d "$root/.git" ]; then
        step "Updating $root"
        git -C "$root" pull --ff-only || fail "git pull failed in $root (local changes?). Resolve it and run this again."
    elif [ -d "$root" ] && [ -n "$(ls -A "$root")" ]; then
        fail "$root exists and is not a PDK clone. Set PDK_DIR to another folder."
    else
        step "Cloning $REPO_URL ($REF) into $root"
        git clone -c core.longpaths=true --branch "$REF" "$REPO_URL" "$root" || fail "git clone failed."
    fi
fi
cd "$root"

# 2. uv (it also provides Python).
if ! has uv; then
    step "Installing uv (https://docs.astral.sh/uv/)"
    has curl || fail "curl is required to install uv. Install uv yourself (https://docs.astral.sh/uv/) and run this again."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    PATH="$HOME/.local/bin:$PATH"
    export PATH
    has uv || fail "uv was installed but is not on PATH. Open a new terminal and run this again."
fi
step "$(uv --version)"

# 3. Python dependencies.
step "Installing the Python dependencies (uv sync)"
uv sync || fail "uv sync failed."

# 4. Local setup and the full check: schemas, Chromium, then everything about the gateway.
step "Extracting the schemas and downloading Chromium (pdk doctor --fix)"
doctor=0
uv run pdk doctor --fix || doctor=$?

echo
if [ "$doctor" -eq 0 ]; then
    echo "PDK is installed and every check passed."
else
    echo "PDK is installed. The checks above marked FAIL are the steps left, usually on the gateway:"
    echo "  1. Create an empty Perspective project to use as the sandbox (e.g. pdk_sandbox)."
    echo "  2. Add the ignition.conf lines that doctor printed (as root) and restart the gateway."
    echo "  3. Create a gateway API key and save it to ~/.pdk/ignition-api-key."
    echo "  4. uv run pdk gateway install --restart"
    echo "  5. uv run pdk doctor   (until everything is ok)"
    echo "Details: README.md, section \"Gateway setup\"."
fi
if [ "$(uname -s)" = "Linux" ]; then
    echo
    echo "On Linux, Chromium may need system libraries: sudo \"$root/.venv/bin/playwright\" install-deps chromium"
fi
printf '\nNext: cd "%s" and open it with Claude Code (claude), or use the CLI: uv run pdk --help\n' "$root"

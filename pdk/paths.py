"""Where PDK keeps things: a checkout of the repo, or an installed package (`uv tool install pdk-perspective`).

From a checkout everything stays in the repo, as always (`schemas/`, `work/`, `gateway/build/`). Installed, the
files that ship with PDK (the Java module's sources, the skill, the subagents) are inside the package, the schema
bundle and the module build are per machine under ~/.pdk (or PDK_HOME), and pulled views go to `work/` in the
folder the agent works in.
"""

from __future__ import annotations

import os
from pathlib import Path

from pdk import PACKAGE_DIR, REPO_ROOT

CHECKOUT = (REPO_ROOT / "gateway" / "module.xml").is_file() and (REPO_ROOT / "pyproject.toml").is_file()
BUNDLED = PACKAGE_DIR / "_bundled"
RUN = "uv run " if CHECKOUT else ""  # how to call the CLI in hints: `uv run pdk ...` in a checkout, `pdk ...` installed


def home() -> Path:
    return Path(os.environ.get("PDK_HOME") or Path.home() / ".pdk")


def gateway_dir() -> Path:
    """module.xml and src/ of the Java module."""
    return REPO_ROOT / "gateway" if CHECKOUT else BUNDLED / "gateway"


def gateway_build_dir() -> Path:
    return REPO_ROOT / "gateway" / "build" if CHECKOUT else home() / "gateway-build"


def default_schemas_dir() -> Path:
    return REPO_ROOT / "schemas" if CHECKOUT else home() / "schemas"


def default_work_dir() -> Path:
    return REPO_ROOT / "work" if CHECKOUT else Path.cwd() / "work"


def skill_dir() -> Path:
    return (REPO_ROOT / ".claude" if CHECKOUT else BUNDLED) / "skills" / "perspective-views"


def agents_dir() -> Path:
    return (REPO_ROOT / ".claude" if CHECKOUT else BUNDLED) / "agents"
SYNC = "uv sync" if CHECKOUT else "uv tool install --reinstall pdk-perspective"  # reinstall the dependencies

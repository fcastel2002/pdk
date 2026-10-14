"""Perspective DevKit: exact offline validation and agent tooling for Ignition Perspective views."""

from __future__ import annotations

from pathlib import Path

__version__ = "0.2.0"

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent

from pdk import config as _config  # noqa: E402,F401  (fills unset PDK_* variables from ~/.pdk/config.toml)

"""Perspective DevKit: exact offline validation and agent tooling for Ignition Perspective views."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

try:
    __version__ = version("pdk-perspective")  # set by the git tag at build time (hatch-vcs)
except PackageNotFoundError:
    __version__ = "0.0.0"

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent

from pdk import config as _config  # noqa: E402,F401  (fills unset PDK_* variables from ~/.pdk/config.toml)

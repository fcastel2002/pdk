"""Sphinx configuration for the PDK documentation (build: uv run --group docs sphinx-build docs docs/_build/html)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pdk import __version__  # noqa: E402

project = "PDK for Ignition Perspective"
author = "Francisco Castel"
copyright = "2026, Francisco Castel"
version = release = __version__

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.viewcode",
    "sphinxarg.ext",
    "sphinx_copybutton",
    "sphinxcontrib.mermaid",
]

exclude_patterns = ["_build"]
templates_path: list[str] = []

html_theme = "furo"
html_title = "PDK for Ignition Perspective"
html_short_title = "PDK"
html_theme_options = {
    "source_repository": "https://github.com/fcastel2002/pdk/",
    "source_branch": "main",
    "source_directory": "docs/",
}

autodoc_default_options = {"members": True, "member-order": "bysource"}
autodoc_typehints = "description"
autodoc_preserve_defaults = True

copybutton_prompt_text = r"^\$ "
copybutton_prompt_is_regexp = True

nitpicky = False

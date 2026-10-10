"""Claude Code PostToolUse hook: validate every view.json (and check every stylesheet.css) the agent writes or edits.

Exit 2 sends the report to Claude through stderr (the edit already happened; Claude fixes it).
Exit 0 is silent. Exit 1 is a non-blocking internal failure shown only to the user.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pdk.bundle import BundleError, load_bundle
from pdk.diagnostics import render
from pdk.stylesheet import check_css
from pdk.validate import validate_file

HOOK_BUDGET = 700


def _target(event: dict[str, Any]) -> Path | None:
    tool_input = event.get("tool_input") if isinstance(event.get("tool_input"), dict) else {}
    raw = tool_input.get("file_path") or tool_input.get("path")
    name = raw.replace("\\", "/").rsplit("/", 1)[-1] if isinstance(raw, str) else ""
    if name not in ("view.json", "stylesheet.css"):
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = Path(event.get("cwd") or ".") / path
    return path if path.is_file() else None


def run_hook(stdin_text: str) -> tuple[int, str]:
    try:
        event = json.loads(stdin_text) if stdin_text.strip() else {}
    except json.JSONDecodeError:
        return 0, ""
    path = _target(event) if isinstance(event, dict) else None
    if path is None:
        return 0, ""
    if path.name.endswith("stylesheet.css"):
        problems = check_css(path.read_text(encoding="utf-8", errors="replace"))
        if not problems:
            return 0, ""
        report = "\n".join(problems[:20])
        return 2, f"pdk: {path.name} has {len(problems)} CSS problem(s)\n{report}\nFix these before moving on."
    try:
        bundle = load_bundle()
    except BundleError as exc:
        return 1, f"pdk hook: {exc}"
    result = validate_file(path, bundle)
    problems = [d for d in result.diagnostics if d["severity"] in ("error", "warning")]
    if not problems:
        return 0, ""
    title = f"pdk: {path.name} does not validate against Perspective {bundle.version}"
    report = render(problems, title=title, budget=HOOK_BUDGET)
    return 2, report + "\nFix these before moving on (pdk_validate with focus=<code|component> gives detail)."

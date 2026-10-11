"""Validate raw view.json against the exact schemas of the installed Perspective version."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pdk.bundle import Bundle
from pdk.diagnostics import Diagnostic, make, ordered
from pdk.project import Project, locate
from pdk.validate import code, config, lint, refs, schema, structure
from pdk.validate.context import Context
from pdk.validate.tree import build_tree, walk

CHECKS = (structure.check, schema.check, config.check, code.check, refs.check, lint.check)


def validate_view(
    view: Any,
    bundle: Bundle,
    *,
    project: Project | None = None,
    view_path: str | None = None,
    profile: str | None = None,
) -> list[Diagnostic]:
    if not isinstance(view, dict):
        return [make("S003", "error", "", "", "view.json must be a JSON object",
                     'start from {"custom": {}, "params": {}, "props": {}, "root": {...}}')]
    root = build_tree(view)
    ctx = Context(view, bundle, root, walk(root), project, view_path, profile)
    diagnostics: list[Diagnostic] = []
    for check in CHECKS:
        diagnostics.extend(check(ctx))
    return ordered(diagnostics)


@dataclass
class FileResult:
    path: Path
    diagnostics: list[Diagnostic]
    project: Project | None = None
    view_path: str | None = None


def parse_view(text: str) -> tuple[Any, Diagnostic | None]:
    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        return None, make("S000", "error", "", "", f"invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}",
                          "fix the JSON syntax (quotes, commas, braces)")


Compiler = Callable[[str], list[Diagnostic]]  # view.json text -> E009 of the scripts that do not compile


def validate_text(
    text: str, bundle: Bundle, *, project: Project | None = None, view_path: str | None = None,
    profile: str | None = None, compiler: Compiler | None = None,
) -> list[Diagnostic]:
    """With `compiler` (pdk.sync.compiler: the gateway's Jython), a view without errors also gets its scripts
    compiled."""
    view, error = parse_view(text)
    if error:
        return [error]
    diagnostics = validate_view(view, bundle, project=project, view_path=view_path, profile=profile)
    if compiler is not None and not any(d["severity"] == "error" for d in diagnostics):
        diagnostics = ordered(diagnostics + compiler(text))
    return diagnostics


def validate_file(
    path: Path, bundle: Bundle, *, project: Project | None = None, profile: str | None = None,
    compiler: Compiler | None = None,
) -> FileResult:
    view_path = None
    located = locate(path)
    if located:
        located_project, view_path = located
        project = project or located_project
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        error = make("S000", "error", "", "", f"cannot read {path}: {exc.strerror}", "check the path")
        return FileResult(path, [error], project, view_path)
    diagnostics = validate_text(text, bundle, project=project, view_path=view_path, profile=profile,
                                compiler=compiler)
    return FileResult(path, diagnostics, project, view_path)


def view_files(target: Path) -> list[Path]:
    if target.is_file():
        return [target]
    return sorted(p for p in target.rglob("*.json") if p.name == "view.json" or p.name.endswith(".view.json"))


def validate_paths(
    target: Path, bundle: Bundle, *, project: Project | None = None, profile: str | None = None,
    compiler: Compiler | None = None,
) -> list[FileResult]:
    return [validate_file(p, bundle, project=project, profile=profile, compiler=compiler) for p in view_files(target)]

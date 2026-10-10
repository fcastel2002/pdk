"""Validation reports shared by the CLI, the MCP server and the hook."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from pdk.bundle import Bundle
from pdk.diagnostics import CHARS_PER_TOKEN, DEFAULT_BUDGET, Diagnostic, counts, render, summary_line
from pdk.project import Project, open_project
from pdk.validate import FileResult, validate_paths, validate_text


def resolve_project(spec: str | None) -> Project | None:
    return open_project(spec) if spec else None


def _title(result: FileResult, base: Path | None, version: str) -> str:
    try:
        shown = result.path.resolve().relative_to(base.resolve()) if base else result.path
    except ValueError:
        shown = result.path
    view = f" [{result.project.name}:{result.view_path}]" if result.project and result.view_path else ""
    return f"{Path(shown).as_posix()}{view} (Perspective {version})"


def report_paths(
    target: Path,
    bundle: Bundle,
    *,
    project: Project | None = None,
    profile: str | None = None,
    focus: str | None = None,
    budget: int = DEFAULT_BUDGET,
) -> tuple[str, int]:
    """Text report for a file or a directory of views, and the number of errors found."""
    results = validate_paths(target, bundle, project=project, profile=profile)
    if not results:
        return f"No view.json under {target}", 0
    errors = sum(counts(r.diagnostics)["error"] for r in results)
    base = target if target.is_dir() else target.parent
    if len(results) == 1:
        return render(results[0].diagnostics, title=_title(results[0], base, bundle.version),
                      budget=budget, focus=focus), errors
    return _report_many(results, base, bundle.version, focus, budget), errors


def _label(result: FileResult, base: Path) -> str:
    if result.project and result.view_path:
        return result.view_path
    try:
        return result.path.resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return result.path.as_posix()


def _report_many(results: list[FileResult], base: Path, version: str, focus: str | None, budget: int) -> str:
    everything: list[Diagnostic] = [d for r in results for d in r.diagnostics]
    by_code = Counter(d["code"] for d in everything if d["severity"] != "info")
    flagged = [r for r in results if any(d["severity"] != "info" for d in r.diagnostics)]
    projects = sorted({r.project.name for r in results if r.project})
    scope = f"project {projects[0]}, " if len(projects) == 1 else ""
    lines = [f"{scope}{len(results)} views (Perspective {version}): {summary_line(everything)}; "
             f"{len(flagged)} with errors or warnings"]
    if by_code:
        lines.append("by code: " + ", ".join(f"{code}x{n}" for code, n in by_code.most_common(12)))
    limit = int(budget * CHARS_PER_TOKEN)
    used = sum(len(line) + 1 for line in lines)
    ranked = sorted(flagged, key=lambda r: (-counts(r.diagnostics)["error"], -len(r.diagnostics), str(r.path)))
    for i, result in enumerate(ranked):
        if focus:
            text = render(result.diagnostics, title=_label(result, base), focus=focus,
                          budget=max(200, int((limit - used) / CHARS_PER_TOKEN)))
            if ": 0 of" in text.splitlines()[0]:
                continue
        else:
            text = f"  {_label(result, base)}: {summary_line(result.diagnostics)}"
        if used + len(text) > limit - 120:
            lines.append(f"... +{len(ranked) - i} more views; validate one file, or pass focus=<code>")
            break
        lines.append(text)
        used += len(text) + 1
    if not focus and flagged:
        lines.append("detail: validate a single view.json, or focus=<code> (e.g. focus=B003) across the directory")
    return "\n".join(lines)


def report_text(
    text: str,
    bundle: Bundle,
    *,
    project: Project | None = None,
    view_path: str | None = None,
    profile: str | None = None,
    focus: str | None = None,
    budget: int = DEFAULT_BUDGET,
) -> tuple[str, int]:
    diagnostics = validate_text(text, bundle, project=project, view_path=view_path, profile=profile)
    where = f" [{project.name}:{view_path}]" if project and view_path else ""
    title = f"inline view{where} (Perspective {bundle.version})"
    return render(diagnostics, title=title, budget=budget, focus=focus), counts(diagnostics)["error"]

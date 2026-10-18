"""R020/R021: the tag paths a view uses, checked against the gateway before the view runs.

Direct paths are checked as written; indirect, expression-mode and tag() paths are expanded through the values the
view's callers pass for its params (pdk.graph), so `{1}/Run` with `{1} = view.params.tagPath` is checked for every
equipment the view is embedded for. A path the gateway does not have is R020; paths that depend on values only
known at runtime are counted once in R021 (pdk_inspect reports them as Q001 when the view runs).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pdk.diagnostics import Diagnostic, make
from pdk.gateway.client import Gateway, GatewayError
from pdk.graph import Graph, TagUse, read_view
from pdk.project import Project

BATCH = 200
MISSING = "Bad_NotFound"

TagChecker = Callable[[dict[str, Any], str | None, Project | None], list[Diagnostic]]


def checker(gateway: Gateway, *, strict: bool = False) -> TagChecker:
    """For validate's `tags`: a view's tag paths checked on this gateway. Without `strict`, a gateway that cannot
    read tags gives nothing; with it, the GatewayError propagates."""
    def check(view: dict[str, Any], view_path: str | None, project: Project | None) -> list[Diagnostic]:
        try:
            return diagnostics(gateway, view, view_path, project)
        except GatewayError:
            if strict:
                raise
            return []
    return check


def diagnostics(gateway: Gateway, view: dict[str, Any], view_path: str | None,
                project: Project | None) -> list[Diagnostic]:
    key = (view_path or "").strip("/")
    info = read_view(key, view)
    if project is not None:
        shared = Graph.of(project)  # this view as it is now (unsaved edits included), without changing the cache
        graph = Graph(project, {**shared.views, key: info}, shared.routes)
    else:
        graph = Graph(None, {key: info}, {})
    paths: dict[str, list[tuple[TagUse, str]]] = {}
    unresolved: list[TagUse] = []
    for use in info.tags:
        expanded = [(path, via) for path, via in graph.expand(use) if "{" not in path and "}" not in path]
        if not expanded:
            unresolved.append(use)
            continue
        for path, via in expanded:
            paths.setdefault(path.strip(), []).append((use, via))
    qualities = _qualities(gateway, sorted(paths))
    out: list[Diagnostic] = []
    reported: set[tuple[str, str]] = set()
    guessed: list[TagUse] = []
    for path, uses in paths.items():
        if qualities.get(path) != MISSING:
            continue
        for use, via in uses:
            if via and all(part.endswith("(default)") for part in via.split("; ")):
                guessed.append(use)  # a param default is often a placeholder: not proof the tag is missing
                continue
            if (use.pointer, path) in reported:
                continue
            reported.add((use.pointer, path))
            out.append(make("R020", "warning", use.pointer, use.component,
                            f"tag '{path}' does not exist on the gateway" + (f" (as {via} passes it)" if via else ""),
                            "fix the path, or the value the caller passes for the param; pdk refs tag shows where "
                            "else it is used"))
    flagged = {d["pointer"] for d in out}
    seen = {use.pointer for use in unresolved}
    for use in guessed:
        if use.pointer not in flagged and use.pointer not in seen:
            seen.add(use.pointer)
            unresolved.append(use)
    if unresolved:
        first = unresolved[0]
        out.append(make("R021", "info", first.pointer, first.component,
                        f"{len(unresolved)} tag path(s) depend on values known only at runtime and were not checked",
                        "pdk_inspect reports a missing one as Q001 Bad_NotFound when the view runs",
                        "; ".join(f"{u.component or '(view)'} {u.template.strip()[:60]}" for u in unresolved[:8])))
    return out


def _qualities(gateway: Gateway, paths: list[str]) -> dict[str, str]:
    """Each path's quality as the gateway reads it; a batch the gateway refuses (a path it cannot parse) is read
    one path at a time, and the unreadable ones are left out."""
    found: dict[str, str] = {}
    for start in range(0, len(paths), BATCH):
        batch = paths[start:start + BATCH]
        try:
            results = gateway.read_tags(batch).get("results", [])
        except GatewayError as exc:
            if exc.code != "G009":
                raise
            for path in batch:
                try:
                    single_results = gateway.read_tags([path]).get("results") or [{}]
                    found[path] = str(single_results[0].get("quality", ""))
                except GatewayError as single:
                    if single.code != "G009":
                        raise
            continue
        for asked, result in zip(batch, results, strict=False):
            found[asked] = str(result.get("quality", ""))
    return found

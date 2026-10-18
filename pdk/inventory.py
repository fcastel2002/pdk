"""Reports over a project's dependency graph (pdk.graph): where a tag or a view is used, and the project's inventory
of screens, routes, params, tag dependencies, write points, embedded views and named queries."""

from __future__ import annotations

import csv
import io
import json
from typing import Any

from pdk.graph import Graph, TagUse
from pdk.project import open_project

SHOWN = 25  # lines of a listing before "(+N more)"


def graph_for(spec: str | None) -> Graph:
    """The graph of a project directory or local gateway project; without one, the sandbox's."""
    if not spec:
        from pdk.gateway.client import Gateway

        spec = Gateway().health().get("sandbox")
        if not spec:
            raise ValueError("pass --project: the gateway has no sandbox project")
    return Graph.build(open_project(spec))


def tag_text(graph: Graph, pattern: str) -> str:
    """Every use of tags matching `pattern`: view, component, how it is bound and the concrete path."""
    found = graph.tag_uses(pattern)
    if not found:
        return (f"no view of {graph.project.name} uses a tag matching {pattern!r} (as written, or expanded through "
                "the params their callers pass)")
    views = sorted({use.view for use, _path, _via in found})
    lines = [f"{len(found)} use(s) of {pattern!r} in {len(views)} view(s) of {graph.project.name}:"]
    for use, path, via in found[:SHOWN]:
        lines.append(f"  {use.view} {use.component or '(view)'} {use.pointer} [{_how(use)}] {path}"
                     + (f"  via {via}" if via else ""))
    if len(found) > SHOWN:
        lines.append(f"  (+{len(found) - SHOWN} more)")
    return "\n".join(lines)


def view_text(graph: Graph, view: str) -> str:
    """Who opens a view (and what params they pass) and what it uses."""
    path = view.strip("/")
    info = graph.views.get(path)
    if info is None:
        return f"{graph.project.name} has no view {path!r}"
    callers = graph.callers(path)
    lines = [f"{path} in {graph.project.name}: params {sorted(info.params) or 'none'}"]
    lines.append(f"used by {len(callers)}:" if callers else "used by nothing (no embed, repeater, popup, nav or route)")
    groups: dict[tuple[str, str], list[str]] = {}
    passed: dict[tuple[str, str], set[str]] = {}
    for use in callers:
        key = (use.view or "page-config", use.kind)
        groups.setdefault(key, []).append(use.component.rsplit("/", 1)[-1] or use.pointer)
        passed.setdefault(key, set()).update(name for instance in use.params for name in instance)
    for (caller, kind), names in list(groups.items())[:SHOWN]:
        shown = ", ".join(names[:4]) + (f", +{len(names) - 4}" if len(names) > 4 else "")
        given = ", ".join(sorted(passed[(caller, kind)]))
        lines.append(f"  {caller}: {len(names)} {kind}(s) ({shown})" + (f" passing {given}" if given else ""))
    if info.uses:
        used = sorted({f"{u.target} ({u.kind})" for u in info.uses})
        lines.append(f"uses {len(info.uses)} view(s): " + ", ".join(used))
    writes = [use for use in info.tags if use.writes]
    lines.append(f"tags: {len(info.tags)} use(s), {len(writes)} write(s); named queries: "
                 f"{', '.join(sorted(set(info.queries))) or 'none'}")
    return "\n".join(lines)


def inventory(graph: Graph) -> dict[str, Any]:
    """The project's screens, as data: per view its routes, params, callers, the views it uses, its tags (expanded)
    and writes, named queries and style classes."""
    routed: dict[str, list[str]] = {}
    for url, view in graph.routes.items():
        routed.setdefault(view, []).append(url)
    views = []
    for path in sorted(graph.views):
        info = graph.views[path]
        tags = []
        for use in info.tags:
            expanded = graph.expand(use)
            tags.append({"component": use.component, "pointer": use.pointer, "mode": use.mode, "writes": use.writes,
                         "template": use.template.strip(), "paths": sorted({p for p, _via in expanded})})
        views.append({
            "view": path, "routes": sorted(routed.get(path, [])), "params": sorted(info.params),
            "usedBy": sorted({f"{u.view or 'page-config'} ({u.kind})" for u in graph.callers(path)}),
            "uses": sorted({f"{u.target} ({u.kind})" for u in info.uses}),
            "tags": tags, "namedQueries": sorted(set(info.queries)), "styleClasses": sorted(info.classes),
        })
    return {"project": graph.project.name, "routes": dict(sorted(graph.routes.items())), "views": views}


def inventory_text(data: dict[str, Any], fmt: str = "md") -> str:
    if fmt == "json":
        return json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    if fmt == "csv":
        out = io.StringIO()
        writer = csv.writer(out, lineterminator="\n")
        writer.writerow(["view", "component", "pointer", "mode", "writes", "template", "path"])
        for view in data["views"]:
            for tag in view["tags"]:
                for path in tag["paths"] or [""]:
                    writer.writerow([view["view"], tag["component"], tag["pointer"], tag["mode"], tag["writes"],
                                     tag["template"], path])
        return out.getvalue()
    lines = [f"# Inventory of {data['project']}", "",
             "| View | Routes | Params | Used by | Tags | Writes | Named queries |", "|---|---|---|---|---|---|---|"]
    for view in data["views"]:
        concrete = {p for tag in view["tags"] for p in tag["paths"]}
        writes = sum(1 for tag in view["tags"] if tag["writes"])
        lines.append(f"| {view['view']} | {', '.join(view['routes'])} | {', '.join(view['params'])} | "
                     f"{len(view['usedBy'])} | {len(view['tags'])} ({len(concrete)} resolved) | {writes} | "
                     f"{', '.join(view['namedQueries'])} |")
    orphans = [view["view"] for view in data["views"] if not view["usedBy"] and not view["routes"]]
    if orphans:
        lines += ["", f"Views nothing opens ({len(orphans)}): " + ", ".join(orphans)]
    return "\n".join(lines) + "\n"


def inventory_summary(data: dict[str, Any]) -> str:
    views = data["views"]
    tags = sum(len(view["tags"]) for view in views)
    concrete = len({p for view in views for tag in view["tags"] for p in tag["paths"]})
    writes = sum(1 for view in views for tag in view["tags"] if tag["writes"])
    orphans = sum(1 for view in views if not view["usedBy"] and not view["routes"])
    return (f"{data['project']}: {len(views)} views, {len(data['routes'])} routes and docks, {tags} tag uses "
            f"({concrete} distinct tags resolved), {writes} write point(s), {orphans} view(s) nothing opens")


def _how(use: TagUse) -> str:
    return use.mode + (", writes" if use.writes else "")

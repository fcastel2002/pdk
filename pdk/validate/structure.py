"""S family: the view.json envelope, component members, types, containers and names."""

from __future__ import annotations

from collections import Counter

from pdk.diagnostics import Diagnostic, join, make
from pdk.suggest import closest, did_you_mean
from pdk.validate.context import Context
from pdk.validate.tree import Node

# From Perspective's own view reader/writer (no schema covers the whole view.json).
VIEW_KEYS = ("custom", "params", "props", "root", "events", "permissions", "propConfig")
COMPONENT_KEYS = (
    "version", "type", "props", "custom", "meta", "position", "events", "propConfig", "scripts", "children",
)
_VIEW_OBJECTS = ("custom", "params", "props", "events", "permissions", "propConfig")
_COMPONENT_OBJECTS = ("props", "custom", "meta", "position", "events", "propConfig", "scripts")
_MISPLACED = {
    "name": "component names live in meta.name",
    "style": "styles live in props.style",
    "text": "component props live under props",
    "bindings": "bindings live in propConfig.<path>.binding",
    "params": "only views have params; use custom on components",
}


def check(ctx: Context) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    view = ctx.view
    for key in view:
        if key not in VIEW_KEYS:
            out.append(make("S001", "error", join("", key), "", f"unknown view key '{key}'",
                            did_you_mean(key, VIEW_KEYS), f"view keys: {', '.join(VIEW_KEYS)}"))
    for key in _VIEW_OBJECTS:
        if key in view and not isinstance(view[key], dict):
            out.append(make("S003", "error", join("", key), "", f"view '{key}' must be an object",
                            f"use \"{key}\": {{}}"))
    if "root" not in view:
        out.append(make("S002", "error", "", "", "view has no root component",
                        'add "root": {"type": "ia.container.flex", "meta": {"name": "root"}, "children": []}'))
        return out
    if not isinstance(view["root"], dict):
        out.append(make("S003", "error", "/root", "", "root must be a component object",
                        'use "root": {"type": "ia.container.flex", "meta": {"name": "root"}}'))
        return out
    for node in ctx.nodes:
        out.extend(_component(node, ctx))
    return out


def _component(node: Node, ctx: Context) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    c, ptr, where = node.component, node.pointer, node.path
    for key in c:
        if key not in COMPONENT_KEYS:
            hint = _MISPLACED.get(key) or did_you_mean(key, COMPONENT_KEYS)
            out.append(make("S004", "error", join(ptr, key), where, f"unknown component key '{key}'", hint,
                            f"component keys: {', '.join(COMPONENT_KEYS)}"))
    for key in _COMPONENT_OBJECTS:
        if key in c and not isinstance(c[key], dict):
            out.append(make("S003", "error", join(ptr, key), where, f"'{key}' must be an object",
                            f"use \"{key}\": {{}}"))
    children = c.get("children")
    if children is not None and not isinstance(children, list):
        out.append(make("S003", "error", join(ptr, "children"), where, "'children' must be an array",
                        'use "children": [ {...}, ... ]'))
        children = None
    for i, child in enumerate(children or []):
        if not isinstance(child, dict):
            out.append(make("S003", "error", join(ptr, "children", i), where, "child must be a component object",
                            'use {"type": "...", "meta": {"name": "..."}}'))

    ctype = c.get("type")
    component = ctx.component(node)
    if not isinstance(ctype, str) or not ctype:
        out.append(make("S005", "error", join(ptr, "type"), where, "component has no type",
                        'add "type", e.g. "ia.display.label" (see pdk_catalog)'))
    elif component is None:
        match = closest(ctype, ctx.bundle.components, cutoff=0.85)
        if match:
            out.append(make("S006", "error", join(ptr, "type"), where, f"unknown component type '{ctype}'",
                            f"did you mean '{match}'?"))
        else:
            out.append(make("S006", "warning", join(ptr, "type"), where,
                            f"component type '{ctype}' is not in the Perspective {ctx.bundle.version} bundle",
                            "a module may be missing (the gateway logs 'Unable to find registered component'); "
                            "its props are not validated"))
    else:
        if component.deprecated:
            out.append(make("S011", "warning", join(ptr, "type"), where, f"'{ctype}' is deprecated",
                            "prefer a current component (pdk_catalog search)"))
        if children and not component.is_container:
            out.append(make("S007", "error", join(ptr, "children"), where,
                            f"'{ctype}' is not a container and cannot have children",
                            "move the children into a container such as ia.container.flex"))

    if node.name is None or not node.name:
        default = (component.default_meta_name if component else None) or "Component"
        out.append(make("S008", "error", join(ptr, "meta", "name"), where, "component has no meta.name",
                        f'add "meta": {{"name": "{default}"}}'))
    elif node.parent is None and node.name != "root":
        out.append(make("S009", "warning", "/root/meta/name", where,
                        f"root component is named '{node.name}'", "name it 'root': absolute paths start at /root"))

    names = Counter(child.name for child in node.children if child.name)
    seen: set[str] = set()
    for child in node.children:
        if child.name and names[child.name] > 1:
            if child.name in seen:
                out.append(make("S010", "error", join(child.pointer, "meta", "name"), child.path,
                                f"duplicate sibling name '{child.name}'",
                                "sibling names must be unique; rename it (e.g. add a suffix)"))
            seen.add(child.name)
    return out

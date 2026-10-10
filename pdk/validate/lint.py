"""HMI family: design lint ported from perspective-compiler's linter.py to raw view.json (severity info).

Generic rules always run. Token rules (HMI002/003/004/006/007/008) assume the `hmi-*` style classes
of the perspective-compiler design system and only run with profile "hmi".
"""

from __future__ import annotations

from typing import Any

from pdk.diagnostics import Diagnostic, join, make
from pdk.validate.context import Context
from pdk.validate.tree import Node

FLEX = "ia.container.flex"
BUTTON = "ia.input.button"
LABEL = "ia.display.label"
SIZE_SENSITIVE = ("ia.display.table", "ia.chart.timeseries", "ia.navigation.menutree")


def _classes(node: Node) -> list[str]:
    style = node.props().get("style")
    classes = style.get("classes") if isinstance(style, dict) else None
    return classes.split() if isinstance(classes, str) else []


def _position(node: Node) -> dict[str, Any]:
    position = node.component.get("position")
    return position if isinstance(position, dict) else {}


def _sized(node: Node) -> bool:
    position = _position(node)
    basis, grow = position.get("basis"), position.get("grow")
    return (basis not in (None, "auto", "")) or (isinstance(grow, int | float) and grow > 0)


def _size_signature(node: Node) -> tuple[Any, Any, Any]:
    position = _position(node)
    return position.get("basis", "auto"), position.get("grow", 0), position.get("shrink", 1)


def _token(node: Node, prefix: str) -> str | None:
    return next((c for c in _classes(node) if c.startswith(prefix)), None)


def check(ctx: Context) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    hmi = ctx.profile == "hmi"
    for node in ctx.nodes:
        ptr, where, kids = node.pointer, node.path, node.children
        if node.type == FLEX:
            direction = node.props().get("direction", "row")
            if node.parent is not None and not kids:
                out.append(make("HMI009", "info", join(ptr, "children"), where, "layout group has no children",
                                "add child components or remove the empty group"))
            if (node.parent is not None and len(kids) == 1 and kids[0].type == FLEX
                    and kids[0].props().get("direction", "row") == direction
                    and not _classes(node) and not _sized(node)):
                out.append(make("HMI005", "info", ptr, where,
                                "flex container redundantly wraps one flex child with the same direction",
                                "remove the outer wrapper or give it its own layout, style or size"))
            buttons = len(kids) >= 2 and all(k.type == BUTTON for k in kids)
            if buttons and len({_size_signature(k) for k in kids}) > 1:
                out.append(make("HMI001", "info", ptr, where, "buttons in this group use different sizes",
                                "give every button the same basis/grow/shrink"))
            if hmi and len(kids) >= 2 and not _token(node, "hmi-spacing-") and "gap" not in (
                    node.props().get("style") or {}):
                out.append(make("HMI004", "info", ptr, where, "layout group with several children has no spacing",
                                "add an hmi-spacing-* class (none, xs, sm, md, lg)"))
            if hmi and buttons:
                if sum(1 for k in kids if "hmi-command-primary" in _classes(k)) > 1:
                    out.append(make("HMI003", "info", ptr, where, "button group has more than one primary action",
                                    "keep one hmi-command-primary; mark the rest secondary or destructive"))
                if len({_token(k, "hmi-control-height-") for k in kids} - {None}) > 1:
                    out.append(make("HMI007", "info", ptr, where, "button group mixes control heights",
                                    "use one hmi-control-height-* class for every button"))
            for kid in kids:
                if kid.type in SIZE_SENSITIVE and not _sized(kid):
                    out.append(make("HMI010", "info", join(kid.pointer, "position"), kid.path,
                                    f"{kid.type} in a flex without basis or grow renders collapsed",
                                    "set position.basis (e.g. '300px') or grow: 1"))
        if node.type == "ia.display.alarmstatustable":
            events = node.component.get("events")
            dom = events.get("dom") if isinstance(events, dict) else None
            if isinstance(dom, dict) and "onSelect" in dom:
                out.append(make("HMI011", "info", join(ptr, "events", "dom", "onSelect"), where,
                                "onSelect is the DOM text-selection event; it does not fire on row selection",
                                "remove it; the alarm status table has no row event, so navigate with a button"))
        if hmi and node.type == LABEL and not _token(node, "hmi-typography-"):
            out.append(make("HMI006", "info", join(ptr, "props", "style", "classes"), where,
                            "label has no typography token", "add hmi-typography-heading, -body or -caption"))
        if hmi and node.type == BUTTON:
            if not _token(node, "hmi-command-"):
                out.append(make("HMI002", "info", join(ptr, "props", "style", "classes"), where,
                                "button has no command variant", "add hmi-command-primary, -secondary or -destructive"))
            if not _token(node, "hmi-control-height-"):
                out.append(make("HMI008", "info", join(ptr, "props", "style", "classes"), where,
                                "button has no control height", "add hmi-control-height-compact, -normal or -large"))
    return out

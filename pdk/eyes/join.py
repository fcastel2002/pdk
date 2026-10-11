"""Join of the three views of one component: the local view.json, the DOM and the gateway's evaluated tree.

All three address components by child index from the root: the view.json pointer /root/children/1/children/0,
the gateway address path 0:1:0 and the DOM path <mountPath>.0:1:0 (C.0:1:0 for the routed view). Descendants of
embedded views carry a `$` in the DOM path, docks a T[/L[/R[/B[ prefix; neither belongs to the inspected view.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pdk.validate.tree import Node, build_tree, walk

MOUNT = "C"
# Containers that mount only some of their children by design (the active breakpoint, the selected tab).
PARTIAL_MOUNT = frozenset({"ia.container.breakpt", "ia.container.tab"})


@dataclass(eq=False)
class Part:
    """One component of the view.json and what was observed of it."""

    node: Node
    dom: dict[str, Any] | None = None  # from browser.EXTRACT_JS
    live: dict[str, Any] | None = None  # from Gateway.page(): address, type, props..., qualities, bindings
    mount: str = MOUNT  # C for the routed view, P<id> for a popup
    prefix: str = ""  # before the name path: "popup:<id>/" inside a popup

    @property
    def dom_path(self) -> str:
        return f"{self.mount}.{self.node.address}"

    @property
    def pointer(self) -> str:
        return self.node.pointer

    @property
    def name(self) -> str:
        return self.prefix + self.node.path

    @property
    def type(self) -> str | None:
        return self.node.type


@dataclass
class Joined:
    parts: list[Part]
    view: dict[str, Any]
    live_view: dict[str, Any] | None  # the gateway's entry for the routed view (params, custom, qualities)
    viewport: dict[str, int]
    by_dom: dict[str, Part] = field(default_factory=dict)
    by_name: dict[str, Part] = field(default_factory=dict)
    mismatches: list[str] = field(default_factory=list)  # why the page does not render this view.json
    foreign: int = 0  # DOM components outside the view (embedded view contents, docks)
    mount: str = MOUNT
    popups: dict[str, str] = field(default_factory=dict)  # open popup id -> its view path
    url: str = ""  # the page's location when it was measured
    routed: str | None = None  # the view the gateway has at this mount (after a nav action, maybe another one)

    @property
    def root_path(self) -> str:
        return f"{self.mount}.0"

    def part(self, dom_path: str | None) -> Part | None:
        return self.by_dom.get(dom_path) if dom_path else None


def _live_components(node: dict[str, Any], found: dict[str, dict[str, Any]]) -> None:
    found[str(node.get("address"))] = node
    for child in node.get("children", []):
        _live_components(child, found)


def live_view(page: dict[str, Any] | None, view_path: str, mount: str = MOUNT) -> dict[str, Any] | None:
    """The instance of `view_path` mounted at `mount` (C routed, P<id> popup), if the gateway reported it."""
    if not page:
        return None
    for view in page.get("views", []):
        if view.get("resourcePath") == view_path and view.get("mountPath") == mount:
            return view
    return None


def mounted_view(page: dict[str, Any] | None, mount: str = MOUNT) -> str | None:
    """The view the gateway lists at `mount` on the page (C: the routed view), if it reported the page."""
    return next((str(v.get("resourcePath")) for v in (page or {}).get("views", []) if v.get("mountPath") == mount),
                None)


def join(view: dict[str, Any], extracted: dict[str, Any], page: dict[str, Any] | None, view_path: str, *,
         mount: str = MOUNT, prefix: str = "") -> Joined:
    root = build_tree(view)
    parts = [Part(node, mount=mount, prefix=prefix) for node in walk(root)]
    routed = live_view(page, view_path, mount)
    joined = Joined(parts, view, routed, dict(extracted.get("viewport") or {}), mount=mount,
                    url=str(extracted.get("url") or ""), routed=mounted_view(page, mount))
    dom_nodes = {node["path"]: node for node in extracted.get("nodes", [])}
    live_nodes: dict[str, dict[str, Any]] = {}
    if routed and isinstance(routed.get("root"), dict):
        _live_components(routed["root"], live_nodes)
    for part in parts:
        joined.by_dom[part.dom_path] = part
        joined.by_name[part.name] = part
        part.dom = dom_nodes.get(part.dom_path)
        part.live = live_nodes.get(part.node.address)
        if part.dom and part.type and part.dom.get("type") != part.type:
            joined.mismatches.append(f"{part.dom_path} is {part.dom.get('type')} in the page but {part.type} in "
                                     f"the view.json ({part.pointer})")
        if part.live and part.type and part.live.get("type") != part.type:
            joined.mismatches.append(f"{part.node.address} is {part.live.get('type')} on the gateway but "
                                     f"{part.type} in the view.json ({part.pointer})")
    joined.foreign = sum(1 for path in dom_nodes if path not in joined.by_dom)
    stray = sorted(path for path in dom_nodes if path not in joined.by_dom and _primary(path, mount))
    if stray:
        joined.mismatches.append(f"the page renders components the view.json lacks: {', '.join(stray[:5])}")
    return joined


def _primary(dom_path: str, mount: str = MOUNT) -> bool:
    return (dom_path == f"{mount}.0" or dom_path.startswith(f"{mount}.0:")) and "$" not in dom_path


def mounted_by_design(part: Part) -> bool:
    """False when the component may legitimately be absent from the DOM."""
    position = part.node.component.get("position")
    if isinstance(position, dict) and position.get("display") is False:
        return False
    node = part.node.parent
    while node is not None:
        if node.type in PARTIAL_MOUNT:
            return False
        position = node.component.get("position")
        if isinstance(position, dict) and position.get("display") is False:
            return False
        node = node.parent
    return True

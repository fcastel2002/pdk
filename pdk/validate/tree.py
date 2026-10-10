"""Component tree over a raw view.json: pointers, name paths and address paths."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pdk.diagnostics import join


@dataclass(eq=False)
class Node:
    component: dict[str, Any]
    pointer: str  # "/root", "/root/children/0", ...
    path: str  # names: "root/Panel/Label"
    address: str  # Perspective address path: "0", "0:1", "0:1:0"
    parent: Node | None = None
    children: list[Node] = field(default_factory=list)

    @property
    def type(self) -> str | None:
        value = self.component.get("type")
        return value if isinstance(value, str) else None

    @property
    def name(self) -> str | None:
        meta = self.component.get("meta")
        name = meta.get("name") if isinstance(meta, dict) else None
        return name if isinstance(name, str) else None

    def child(self, name: str) -> Node | None:
        return next((c for c in self.children if c.name == name), None)

    def props(self) -> dict[str, Any]:
        props = self.component.get("props")
        return props if isinstance(props, dict) else {}


def _build(component: dict[str, Any], pointer: str, parent: Node | None, address: str, index: int | None) -> Node:
    meta = component.get("meta")
    name = meta.get("name") if isinstance(meta, dict) else None
    label = name if isinstance(name, str) and name else f"[{index if index is not None else 'root'}]"
    path = f"{parent.path}/{label}" if parent else label
    node = Node(component, pointer, path, address, parent)
    children = component.get("children")
    if isinstance(children, list):
        for i, child in enumerate(children):
            if isinstance(child, dict):
                node.children.append(_build(child, join(pointer, "children", i), node, f"{address}:{i}", i))
    return node


def build_tree(view: dict[str, Any]) -> Node | None:
    root = view.get("root")
    return _build(root, "/root", None, "0", None) if isinstance(root, dict) else None


def walk(node: Node | None) -> list[Node]:
    """Pre-order list of nodes."""
    if node is None:
        return []
    out = [node]
    for child in node.children:
        out.extend(walk(child))
    return out


def find_by_path(root: Node, names: list[str]) -> Node | None:
    """Resolve `/root/A/B` style name segments (first segment is the root's name)."""
    if not names or names[0] != root.name:
        return None
    node: Node | None = root
    for name in names[1:]:
        node = node.child(name) if node else None
    return node

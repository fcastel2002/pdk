"""Shared state for one validation run."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pdk.bundle import Bundle, Component
from pdk.project import Project
from pdk.validate.tree import Node


@dataclass
class Context:
    view: dict[str, Any]
    bundle: Bundle
    root: Node | None
    nodes: list[Node]
    project: Project | None = None
    view_path: str | None = None
    profile: str | None = None
    by_pointer: dict[str, Node] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.by_pointer = {n.pointer: n for n in self.nodes}

    def component(self, node: Node | None) -> Component | None:
        return self.bundle.components.get(node.type) if node and node.type else None

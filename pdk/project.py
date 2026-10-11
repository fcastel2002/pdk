"""Read-only view of an Ignition project directory: views, their params and style classes."""

from __future__ import annotations

import json
import re
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pdk.bundle import find_ignition_home

PERSPECTIVE_DIR = "com.inductiveautomation.perspective"
NAMED_QUERY_DIR = "ignition/named-query"


@dataclass(frozen=True)
class ViewParam:
    name: str
    direction: str  # input | output | inout
    default: Any


@dataclass(eq=False)
class Project:
    root: Path
    parent: Project | None = None
    _views: dict[str, Path] | None = field(default=None, repr=False)
    _params: dict[str, dict[str, ViewParam]] = field(default_factory=dict, repr=False)
    _stylesheet_classes: set[str] | None = field(default=None, repr=False)
    _named_queries: list[str] | None = field(default=None, repr=False)

    @property
    def name(self) -> str:
        return self.root.name

    @property
    def views(self) -> dict[str, Path]:
        if self._views is None:
            base = self.root / PERSPECTIVE_DIR / "views"
            self._views = {
                _resource_path(f.parent.relative_to(base).parts): f for f in sorted(base.rglob("view.json"))
            } if base.is_dir() else {}
        return self._views

    def find_view(self, view_path: str) -> Path | None:
        path = self.views.get(view_path.strip("/"))
        if path is None and self.parent:
            return self.parent.find_view(view_path)
        return path

    def view_params(self, view_path: str) -> dict[str, ViewParam] | None:
        key = view_path.strip("/")
        if key not in self._params:
            path = self.find_view(key)
            if path is None:
                return None
            try:
                view = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None
            params = view.get("params") if isinstance(view.get("params"), dict) else {}
            config = view.get("propConfig") if isinstance(view.get("propConfig"), dict) else {}
            self._params[key] = {
                name: ViewParam(name, _direction(config.get(f"params.{name}")), default)
                for name, default in params.items()
            }
        return self._params[key]

    @property
    def stylesheet_classes(self) -> set[str]:
        if self._stylesheet_classes is None:
            try:
                css = (self.root / PERSPECTIVE_DIR / "stylesheet" / "stylesheet.css").read_text(encoding="utf-8")
            except (OSError, ValueError):
                css = ""
            css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
            self._stylesheet_classes = {_unescape_css(m) for m in _PSC.findall(css)}
        return self._stylesheet_classes

    def has_style_class(self, name: str) -> bool:
        if (self.root / PERSPECTIVE_DIR / "style-classes" / name / "style.json").is_file():
            return True
        if name in self.stylesheet_classes:
            return True
        return bool(self.parent and self.parent.has_style_class(name))

    def named_queries(self) -> list[str]:
        """Named query paths (`Folder/Name`) of this project and its parents: each
        `ignition/named-query/<path>/query.sql`."""
        if self._named_queries is None:
            base = self.root / NAMED_QUERY_DIR
            own = {_resource_path(f.parent.relative_to(base).parts) for f in base.rglob("query.sql")} \
                if base.is_dir() else set()
            inherited = self.parent.named_queries() if self.parent else []
            self._named_queries = sorted(own | set(inherited))
        return self._named_queries

    def has_named_query(self, path: str) -> bool:
        return path.strip("/") in self.named_queries()


_PSC = re.compile(r"\.psc-((?:[A-Za-z0-9_-]|\\(?:[0-9a-fA-F]{1,6}\s?|.))+)", re.S)
_CSS_ESCAPE = re.compile(r"\\(?:([0-9a-fA-F]{1,6})\s?|(.))", re.S)


def _unescape_css(text: str) -> str:
    return _CSS_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)) if m.group(1) else m.group(2), text)


def _resource_path(parts: tuple[str, ...]) -> str:
    """Resource path from folder names on disk, where Ignition percent-encodes names Windows reserves (Aux)."""
    return "/".join(urllib.parse.unquote(part) for part in parts)


def _direction(entry: Any) -> str:
    direction = entry.get("paramDirection") if isinstance(entry, dict) else None
    return direction if direction in ("input", "output", "inout") else "input"


def _parent_name(root: Path) -> str:
    try:
        data = json.loads((root / "project.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    parent = data.get("parent") if isinstance(data, dict) else ""
    return parent if isinstance(parent, str) else ""


def load_project(root: Path, _depth: int = 0) -> Project:
    parent_name = _parent_name(root)
    parent_root = root.parent / parent_name if parent_name else None
    parent = load_project(parent_root, _depth + 1) if parent_root and parent_root.is_dir() and _depth < 8 else None
    return Project(root, parent)


def open_project(spec: str) -> Project:
    """A project directory, or the name of a project on the local gateway."""
    path = Path(spec)
    if path.is_dir():
        return load_project(path)
    home = find_ignition_home()
    candidate = home / "data" / "projects" / spec if home else None
    if candidate and candidate.is_dir():
        return load_project(candidate)
    raise FileNotFoundError(f"Project {spec!r} is neither a directory nor a project on the local gateway")


def locate(view_file: Path) -> tuple[Project, str] | None:
    """Project and view path for a view.json stored inside a project tree."""
    parts = view_file.resolve().parts
    for i in range(len(parts) - 1, 0, -1):
        if parts[i] == PERSPECTIVE_DIR and i + 1 < len(parts) and parts[i + 1] == "views":
            root = Path(*parts[:i])
            view_path = _resource_path(parts[i + 2 : -1])
            return load_project(root), view_path
    return None

"""Who uses what in a project: each view's tag paths, the views it embeds, opens or navigates to, the named queries
and style classes it uses, and the page routes. Indirect tag paths are expanded through the values the callers of
a view pass for its params (an embedded view's `params`, a repeater's `instances`, a popup's or nav's params),
following params bound to the caller's own params. Read-only, from the project's files; Ignition has no such
cross-reference.
"""

from __future__ import annotations

import itertools
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from typing import Any

from pdk.diagnostics import join
from pdk.project import PERSPECTIVE_DIR, Project
from pdk.validate.code import scripts
from pdk.validate.config import BINDING_ALIASES
from pdk.validate.refs import binding_expressions
from pdk.validate.tree import Node, build_tree, walk

MAX_DEPTH = 4  # callers followed when a param is itself bound to a param of the caller
MAX_EXPANSIONS = 200  # concrete paths kept per tag use
_PARAM_REF = re.compile(r"^\{\s*view\.params\.([A-Za-z_][\w]*)\s*\}$")
_LITERAL = re.compile(r"""^\s*(?:"((?:\\.|[^"\\])*)"|'((?:\\.|[^'\\])*)')\s*$""")
_TAG_CALL = re.compile(r"\btag\s*\(")
_SCRIPT_CALL = re.compile(r"system\.tag\.(read\w*|write\w*)\s*\(")
_STRING = re.compile(r"""\"((?:\\.|[^"\\\n])*)\"|'((?:\\.|[^'\\\n])*)'""")
_PROVIDER = re.compile(r"^\[[^\]]*\]")
PAGE_CONFIG = f"{PERSPECTIVE_DIR}/page-config/config.json"


@dataclass(frozen=True)
class Param:
    """How a caller sets a param: a literal value, or bound to one of the caller's own params."""
    value: Any = None
    from_param: str | None = None


@dataclass
class TagUse:
    view: str
    pointer: str
    component: str
    template: str  # as written: "[default]Plant/Pump1/Run", "{1}/Run", an expression or a script literal
    mode: str  # direct | indirect | expression | tag() | script
    writes: bool = False
    references: dict[str, str] = field(default_factory=dict)


@dataclass
class ViewUse:
    view: str
    pointer: str
    component: str
    target: str
    kind: str  # embed | repeater | popup | nav | route (page routes and shared docks)
    params: list[dict[str, Param]] = field(default_factory=list)  # one per instance (a repeater has several)


@dataclass
class ViewInfo:
    path: str
    params: dict[str, Any]
    tags: list[TagUse] = field(default_factory=list)
    uses: list[ViewUse] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    classes: set[str] = field(default_factory=set)


class Graph:
    def __init__(self, project: Project | None, views: dict[str, ViewInfo], routes: dict[str, str]):
        self.project = project
        self.views = views
        self.routes = routes
        self._callers: dict[str, list[ViewUse]] = {}
        for info in views.values():
            for use in info.uses:
                self._callers.setdefault(use.target, []).append(use)
        for url, view in routes.items():
            self._callers.setdefault(view, []).append(ViewUse("", f"page-config:{url}", "", view, "route", [{}]))

    @classmethod
    def of(cls, project: Project) -> Graph:
        """The project's graph, built once per Project object (a validation run shares one)."""
        graph = getattr(project, "_pdk_graph", None)
        if graph is None:
            graph = cls.build(project)
            project._pdk_graph = graph  # type: ignore[attr-defined]
        return graph

    @classmethod
    def build(cls, project: Project) -> Graph:
        views: dict[str, ViewInfo] = {}
        for path, file in project.views.items():
            try:
                document = json.loads(file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(document, dict):
                views[path] = read_view(path, document)
        return cls(project, views, _routes(project))

    def callers(self, view: str) -> list[ViewUse]:
        return self._callers.get(view.strip("/"), [])

    def param_values(self, view: str, param: str, depth: int = 0) -> list[tuple[Any, str]]:
        """The values a view's param can take: its default, and what each caller passes (following params bound
        to the caller's own params), each with where it comes from."""
        info = self.views.get(view)
        found: list[tuple[Any, str]] = []
        if info is not None and info.params.get(param) not in (None, ""):
            found.append((info.params[param], f"{view} (default)"))
        if depth >= MAX_DEPTH:
            return found
        for use in self.callers(view):
            for instance in use.params:
                given = instance.get(param)
                if given is None:
                    continue
                if given.from_param:
                    found += [(value, f"{use.view} > {via}") for value, via in
                              self.param_values(use.view, given.from_param, depth + 1)]
                elif given.value not in (None, ""):
                    found.append((given.value, f"{use.view} {use.component}".strip()))
        return found

    def expand(self, use: TagUse) -> list[tuple[str, str]]:
        """The concrete tag paths a use stands for, each with the caller that gives it; [] when unknown."""
        if use.mode == "direct":
            return [] if "{" in use.template else [(use.template, "")]
        if use.mode == "script":
            return [(use.template, "")]
        if use.mode == "indirect":
            options: list[list[tuple[str, str, str]]] = []
            for key in re.findall(r"\{([^{}]+)\}", use.template):
                reference = use.references.get(key.strip())
                values = self._reference_values(use.view, reference) if reference is not None else []
                if not values:
                    return []
                options.append([(key, str(value), via) for value, via in values])
            return self._combine(use.template, options)
        parts = concatenation(use.template)
        if parts is None:
            return []
        options = []
        template = ""
        for i, (kind, text) in enumerate(parts):
            if kind == "literal":
                template += text
            else:
                values = self.param_values(use.view, text)
                if not values:
                    return []
                options.append([(str(i), str(value), via) for value, via in values])
                template += "{" + str(i) + "}"
        return self._combine(template, options)

    def _reference_values(self, view: str, reference: str) -> list[tuple[Any, str]]:
        match = _PARAM_REF.match(reference.strip())
        if match:
            return self.param_values(view, match.group(1))
        if "{" not in reference:
            return [(reference.strip(), "")]
        return []

    @staticmethod
    def _combine(template: str, options: list[list[tuple[str, str, str]]]) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        for combination in itertools.islice(itertools.product(*options), MAX_EXPANSIONS):
            path = template
            vias = []
            for key, value, via in combination:
                path = path.replace("{" + key + "}", value)
                if via:
                    vias.append(via)
            out.append((path, "; ".join(dict.fromkeys(vias))))
        return list(dict.fromkeys(out))

    def tag_uses(self, pattern: str) -> list[tuple[TagUse, str, str]]:
        """Uses whose template or an expanded path matches `pattern` (substring or glob, case-insensitive, the
        provider optional): (use, matching path, via)."""
        out = []
        for info in self.views.values():
            for use in info.tags:
                for path, via in self.expand(use) or [(use.template, "")]:
                    if matches(path, pattern):
                        out.append((use, path, via))
                        break
        return out

    def depth(self, view: str, seen: frozenset[str] = frozenset()) -> int:
        """How deep embedded views nest below this view (an embed of an embed is 2); cycles stop."""
        info = self.views.get(view)
        if info is None or view in seen:
            return 0
        inner = [self.depth(use.target, seen | {view}) + 1 for use in info.uses if use.kind in ("embed", "repeater")]
        return max(inner, default=0)

    def repeated(self, view: str) -> list[ViewUse]:
        """The repeaters that render this view once per instance."""
        return [use for use in self.callers(view) if use.kind == "repeater"]


def matches(path: str, pattern: str) -> bool:
    bare_path = _PROVIDER.sub("", path).lower()
    wanted = pattern.lower()
    bare_wanted = _PROVIDER.sub("", wanted)
    if any(c in pattern for c in "*?"):
        return fnmatchcase(path.lower(), wanted) or fnmatchcase(bare_path, bare_wanted)
    return bare_wanted in bare_path


def concatenation(expression: str) -> list[tuple[str, str]] | None:
    """An expression made only of string literals and {view.params.X} joined by +, as parts; None otherwise."""
    parts: list[tuple[str, str]] = []
    for piece in _split_plus(expression):
        literal = _LITERAL.match(piece)
        param = _PARAM_REF.match(piece.strip())
        if literal:
            parts.append(("literal", literal.group(1) if literal.group(1) is not None else literal.group(2)))
        elif param:
            parts.append(("param", param.group(1)))
        else:
            return None
    return parts or None


def _split_plus(expression: str) -> list[str]:
    pieces, current, quote, depth = [], "", "", 0
    for char in expression:
        if quote:
            current += char
            if char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
            current += char
        elif char in "{(":
            depth += 1
            current += char
        elif char in "})":
            depth -= 1
            current += char
        elif char == "+" and depth == 0:
            pieces.append(current)
            current = ""
        else:
            current += char
    pieces.append(current)
    return [piece for piece in pieces if piece.strip()]


# --- reading one view --------------------------------------------------------------------------


def read_view(path: str, document: dict[str, Any]) -> ViewInfo:
    params = document.get("params") if isinstance(document.get("params"), dict) else {}
    info = ViewInfo(path, dict(params))
    root = build_tree(document)
    nodes = walk(root)
    owners: list[tuple[Node | None, Any, str]] = [(None, document.get("propConfig"), "/propConfig")]
    owners += [(node, node.component.get("propConfig"), join(node.pointer, "propConfig")) for node in nodes]
    for node, config, base in owners:
        if not isinstance(config, dict):
            continue
        where = node.path if node else ""
        for prop, entry in config.items():
            binding = entry.get("binding") if isinstance(entry, dict) else None
            if isinstance(binding, dict):
                _binding_uses(info, binding, join(base, prop, "binding"), where, node)
    for text, pointer, where in scripts(document, nodes):
        for use in _script_tags(text, pointer, where, path):
            info.tags.append(use)
    for node in nodes:
        _view_uses(info, node)
        style = node.props().get("style")
        classes = style.get("classes") if isinstance(style, dict) else None
        if isinstance(classes, str):
            info.classes.update(classes.split())
    return info


def _binding_uses(info: ViewInfo, binding: dict[str, Any], pointer: str, where: str, node: Node | None) -> None:
    btype = BINDING_ALIASES.get(str(binding.get("type")), binding.get("type"))
    config = binding.get("config") if isinstance(binding.get("config"), dict) else {}
    if btype == "tag" and isinstance(config.get("tagPath"), str) and config["tagPath"].strip():
        references = config.get("references") if isinstance(config.get("references"), dict) else {}
        info.tags.append(TagUse(info.path, join(pointer, "config", "tagPath"), where, config["tagPath"],
                                str(config.get("mode") or "direct"), bool(config.get("bidirectional")),
                                {str(k): _this_reference(node, str(v)) for k, v in references.items()}))
    elif btype == "query" and isinstance(config.get("queryPath"), str) and config.get("mode") != "expression":
        info.queries.append(config["queryPath"].strip("/"))
    for text, epointer, _transform in binding_expressions(binding, pointer):
        if btype == "tag" and epointer.endswith("/tagPath"):
            continue  # an expression-mode tag binding is recorded above
        for match in _TAG_CALL.finditer(text):
            argument = _call_argument(text, match.end())
            info.tags.append(TagUse(info.path, epointer, where, argument, "tag()"))


_THIS_REF = re.compile(r"^\{\s*this\.((?:props|custom)(?:\.[A-Za-z_][\w]*)+)\s*\}$")


def _this_reference(node: Node | None, reference: str) -> str:
    """`{this.props.params.tagPath}` as what the component holds there: its literal value, or the view param it is
    bound to (`{view.params.X}`); any other reference stays as it is."""
    match = _THIS_REF.match(reference.strip())
    if node is None or not match:
        return reference
    path = match.group(1)
    config = node.component.get("propConfig")
    entry = config.get(path) if isinstance(config, dict) else None
    if isinstance(entry, dict) and isinstance(entry.get("binding"), dict):
        bound = _bound_params(node, path.rsplit(".", 1)[0]).get(path.rsplit(".", 1)[1])
        return "{view.params." + bound.from_param + "}" if bound and bound.from_param else reference
    value: Any = node.component
    for key in path.split("."):
        value = value.get(key) if isinstance(value, dict) else None
    return value if isinstance(value, str) and value and "{" not in value else reference


def _script_tags(text: str, pointer: str, where: str, view: str) -> Iterator[TagUse]:
    for match in _SCRIPT_CALL.finditer(text):
        argument = _call_argument(text, match.end())
        for literal in _STRING.finditer(argument):
            value = literal.group(1) if literal.group(1) is not None else literal.group(2)
            if value.startswith("[") and "]" in value:  # a full path; a fragment like "/Run" is joined at runtime
                yield TagUse(view, pointer, where, value, "script", match.group(1).startswith("write"))


def _call_argument(text: str, start: int) -> str:
    """The text between a call's opening parenthesis (just before `start`) and its matching close."""
    depth, quote = 1, ""
    for i in range(start, len(text)):
        char = text[i]
        if quote:
            if char == quote and text[i - 1] != "\\":
                quote = ""
        elif char in "\"'":
            quote = char
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
            if depth == 0:
                return text[start:i]
    return text[start:]


def _bound_params(node: Node, prefix: str) -> dict[str, Param]:
    """Params given through bindings on `<prefix>.<name>`: bound to a view param of the caller, or unknown."""
    found: dict[str, Param] = {}
    config = node.component.get("propConfig")
    for key, entry in (config.items() if isinstance(config, dict) else []):
        if not key.startswith(prefix + "."):
            continue
        name = key[len(prefix) + 1:].split(".")[0].split("[")[0]
        binding = entry.get("binding") if isinstance(entry, dict) else None
        bconfig = binding.get("config") if isinstance(binding, dict) and isinstance(binding.get("config"), dict) else {}
        source = ""
        if isinstance(binding, dict) and binding.get("type") == "property":
            source = "{" + str(bconfig.get("path", "")) + "}"
        elif isinstance(binding, dict) and binding.get("type") in ("expr", "expression"):
            source = str(bconfig.get("expression", ""))
        match = _PARAM_REF.match(source.strip())
        found[name] = Param(from_param=match.group(1)) if match else Param()
    return found


def _literal_params(values: Any) -> dict[str, Param]:
    return {str(k): Param(value=v) for k, v in values.items()} if isinstance(values, dict) else {}


def _view_uses(info: ViewInfo, node: Node) -> None:
    props = node.props()
    if node.type == "ia.display.view" and isinstance(props.get("path"), str) and props["path"]:
        params = _literal_params(props.get("params"))
        params.update(_bound_params(node, "props.params"))
        info.uses.append(ViewUse(info.path, join(node.pointer, "props", "path"), node.path,
                                 props["path"].strip("/"), "embed", [params]))
    elif node.type == "ia.display.flex-repeater" and isinstance(props.get("path"), str) and props["path"]:
        instances = props.get("instances") if isinstance(props.get("instances"), list) else []
        given = [_literal_params(instance) for instance in instances if isinstance(instance, dict)]
        info.uses.append(ViewUse(info.path, join(node.pointer, "props", "path"), node.path,
                                 props["path"].strip("/"), "repeater", given or [{}]))
    elif node.type == "ia.display.carousel":
        for i, entry in enumerate(props.get("views") if isinstance(props.get("views"), list) else []):
            if isinstance(entry, dict) and isinstance(entry.get("viewPath"), str) and entry["viewPath"]:
                info.uses.append(ViewUse(info.path, join(node.pointer, "props", "views", i, "viewPath"), node.path,
                                         entry["viewPath"].strip("/"), "embed",
                                         [_literal_params(entry.get("viewParams"))]))
    events = node.component.get("events")
    for group, handlers in (events.items() if isinstance(events, dict) else []):
        for name, handler in (handlers.items() if isinstance(handlers, dict) else []):
            for i, item in enumerate(handler if isinstance(handler, list) else [handler]):
                if not isinstance(item, dict) or not isinstance(item.get("config"), dict):
                    continue
                config = item["config"]
                pointer = join(node.pointer, "events", group, name, *([i] if isinstance(handler, list) else []))
                if item.get("type") == "nav" and isinstance(config.get("view"), str) and config["view"]:
                    info.uses.append(ViewUse(info.path, join(pointer, "config", "view"), node.path,
                                             config["view"].strip("/"), "nav", [_literal_params(config.get("params"))]))
                elif item.get("type") == "popup" and isinstance(config.get("viewPath"), str) and config["viewPath"]:
                    info.uses.append(ViewUse(info.path, join(pointer, "config", "viewPath"), node.path,
                                             config["viewPath"].strip("/"), "popup",
                                             [_literal_params(config.get("viewParams"))]))


def _routes(project: Project) -> dict[str, str]:
    try:
        config = json.loads((project.root / PAGE_CONFIG).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    pages = config.get("pages") if isinstance(config, dict) and isinstance(config.get("pages"), dict) else {}
    routes = {url: page["viewPath"].strip("/") for url, page in pages.items()
              if isinstance(page, dict) and isinstance(page.get("viewPath"), str) and page["viewPath"]}
    docks = config.get("sharedDocks") if isinstance(config, dict) else None
    for side, entries in (docks.items() if isinstance(docks, dict) else []):
        for i, dock in enumerate(entries if isinstance(entries, list) else []):
            if isinstance(dock, dict) and isinstance(dock.get("viewPath"), str) and dock["viewPath"]:
                routes[f"dock:{side}:{i}"] = dock["viewPath"].strip("/")
    return routes

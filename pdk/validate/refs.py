"""R family: references inside the view (binding paths, expressions) and to the project (views, params, classes).

Property paths: `this.props.x`, `parent.custom.y`, `view.params.z`, `session.props.w`, `page.props.v`,
absolute component paths `/root/Panel/Label.props.text` and relative ones `../Label.props.text`
(`..` is the parent container, `.` the component itself). Component names cannot contain dots.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

from pdk.diagnostics import Diagnostic, join, make
from pdk.shape import SchemaRef, check_path, items_ref, object_shape, parse_path, resolve
from pdk.suggest import closest, did_you_mean, listing
from pdk.validate.config import BINDING_ALIASES
from pdk.validate.context import Context
from pdk.validate.schema import META_EXTRA
from pdk.validate.tree import Node, find_by_path

_BRACES = re.compile(r"\{([^{}]+)\}")
_SCOPES = ("this", "view", "parent", "session", "page")
_SECTIONS = ("props", "custom", "meta", "position", "params")
_IDENT_PATH = re.compile(r"^[A-Za-z_][\w ]*\.(props|custom|meta|position|params)(\.|\[|$)")
_EMBEDDED_PARAMS = {"ia.display.view": "params"}


def check(ctx: Context) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    for node, ref, pointer in _references(ctx):
        out += _resolve(ctx, node, ref, pointer)
    if ctx.project is not None:
        out += _project_refs(ctx)
        out += _named_query_refs(ctx)
    return out


# --- collecting references --------------------------------------------------------------------


def _expression_refs(text: Any, pointer: str, skip_value: bool = False) -> Iterator[tuple[str, str]]:
    if not isinstance(text, str):
        return
    for match in _BRACES.finditer(text):
        ref = match.group(1).strip()
        if ref.startswith("[") or ref.isdigit() or (skip_value and (ref == "value" or ref.startswith("value."))):
            continue  # tag paths, indirect placeholders, the transform input
        yield ref, pointer


def binding_expressions(binding: Any, pointer: str) -> Iterator[tuple[str, str, bool]]:
    """Every expression of a binding: (text, pointer, is_transform), where a transform's `{value}` is its input."""
    if not isinstance(binding, dict):
        return
    config = binding.get("config") if isinstance(binding.get("config"), dict) else {}
    cpointer = join(pointer, "config")
    btype = BINDING_ALIASES.get(str(binding.get("type")), binding.get("type"))
    if btype == "expr" and isinstance(config.get("expression"), str):
        yield config["expression"], join(cpointer, "expression"), False
    elif btype == "expr-struct" and isinstance(config.get("struct"), dict):
        for key, text in config["struct"].items():
            if isinstance(text, str):
                yield text, join(cpointer, "struct", key), False
    elif btype == "tag" and config.get("mode") == "expression" and isinstance(config.get("tagPath"), str):
        yield config["tagPath"], join(cpointer, "tagPath"), False
    for i, transform in enumerate(binding.get("transforms") or []):
        if (isinstance(transform, dict) and transform.get("type") == "expression"
                and isinstance(transform.get("expression"), str)):
            yield transform["expression"], join(pointer, "transforms", i, "expression"), True


def _binding_refs(binding: Any, pointer: str) -> Iterator[tuple[str, str]]:
    if not isinstance(binding, dict) or not isinstance(binding.get("config"), dict):
        return
    config, cpointer = binding["config"], join(pointer, "config")
    btype = BINDING_ALIASES.get(str(binding.get("type")), binding.get("type"))
    if btype == "property" and isinstance(config.get("path"), str) and config["path"]:
        yield config["path"], join(cpointer, "path")
    if btype == "tag" and isinstance(config.get("references"), dict):
        for key, text in config["references"].items():
            yield from _expression_refs(text, join(cpointer, "references", key))
    for text, epointer, in_transform in binding_expressions(binding, pointer):
        yield from _expression_refs(text, epointer, skip_value=in_transform)


def _prop_configs(ctx: Context) -> list[tuple[Node | None, dict[str, Any], str]]:
    """Every propConfig that can hold bindings: the view's own (node None) and each component's, with its pointer."""
    owners: list[tuple[Node | None, Any, str]] = [(None, ctx.view.get("propConfig"), "/propConfig")]
    owners += [(n, n.component.get("propConfig"), join(n.pointer, "propConfig")) for n in ctx.nodes]
    return [(node, config, base) for node, config, base in owners if isinstance(config, dict)]


def _references(ctx: Context) -> Iterator[tuple[Node | None, str, str]]:
    for node, config, base in _prop_configs(ctx):
        for path, entry in config.items():
            if isinstance(entry, dict):
                for ref, pointer in _binding_refs(entry.get("binding"), join(base, path, "binding")):
                    yield node, ref, pointer


# --- resolving --------------------------------------------------------------------------------


def _declared(container: Any, config: Any, section: str) -> set[str]:
    names = set(container) if isinstance(container, dict) else set()
    if isinstance(config, dict):
        names |= {k.split(".")[1].split("[")[0] for k in config if k.startswith(section + ".") and "." in k}
    return names


def _resolve(ctx: Context, node: Node | None, ref: str, pointer: str) -> list[Diagnostic]:
    where = node.path if node else ""
    head = ref.split(".", 1)[0]
    if ref.startswith(("/", ".")):
        return _component_path(ctx, node, ref, pointer, where)
    if head not in _SCOPES:
        if _IDENT_PATH.match(ref):
            return [make("R007", "error", pointer, where, f"unknown reference root '{head}' in '{ref}'",
                         did_you_mean(head, _SCOPES) + " (or a component path like ../Name.props.x)")]
        return []
    rest = parse_path(ref)[1:]
    if not rest:
        return []
    if head == "view":
        return _view_ref(ctx, ref, rest, pointer, where)
    if head == "session":
        return _session_ref(ctx, ref, rest, pointer, where)
    if head == "page":
        return []
    target = node if head == "this" else (node.parent if node else None)
    if node is None:
        return []
    if target is None:
        return [make("R004", "error", pointer, where, f"'{ref}': the root component has no parent",
                     "use view.* or a /root/... path")]
    return _member(ctx, target, ref, rest, pointer, where)


def _view_ref(ctx: Context, ref: str, rest: list[Any], pointer: str, where: str) -> list[Diagnostic]:
    section = rest[0]
    if section not in ("params", "custom", "props"):
        return [make("R003", "error", pointer, where, f"'{ref}': views have params, custom and props",
                     did_you_mean(str(section), ("params", "custom", "props")))]
    if len(rest) < 2:
        return []
    name = str(rest[1])
    if section == "params":
        declared = _declared(ctx.view.get("params"), ctx.view.get("propConfig"), "params")
        if name not in declared:
            return [make("R001", "error", pointer, where, f"'{ref}': view param '{name}' is not declared",
                         did_you_mean(name, declared) if declared else f"declare it: \"params\": {{\"{name}\": null}}",
                         f"declared params: {listing(declared, 40) or 'none'}")]
    elif section == "custom":
        declared = _declared(ctx.view.get("custom"), ctx.view.get("propConfig"), "custom")
        if name not in declared:
            return [make("R002", "warning", pointer, where, f"'{ref}': view custom '{name}' is not declared",
                         did_you_mean(name, declared) if declared else "declare it in the view's custom object")]
    return []


def _session_ref(ctx: Context, ref: str, rest: list[Any], pointer: str, where: str) -> list[Diagnostic]:
    if rest[0] != "props" or len(rest) < 2:
        return []
    schema = ctx.bundle.schemas.get("schemas/session-props.json")
    shape = object_shape(SchemaRef(schema, schema), ctx.bundle) if schema else None
    name = str(rest[1])
    if shape and not shape.open and name not in shape.properties:
        return [make("R006", "warning", pointer, where, f"'{ref}': session has no prop '{name}'",
                     did_you_mean(name, shape.properties) + "; custom session values live in session.custom")]
    return []


def _component_path(ctx: Context, node: Node | None, ref: str, pointer: str, where: str) -> list[Diagnostic]:
    if ctx.root is None:
        return []
    path_part, _, member = _split_component_ref(ref)
    if ref.startswith("/"):
        names = [n for n in path_part.split("/") if n]
        target = find_by_path(ctx.root, names)
        failed_at = _first_missing(ctx.root, names)
    else:
        if node is None:
            return []
        target, failed_at = _walk_relative(node, path_part)
    if target is None:
        parent, missing = failed_at if failed_at else (None, path_part)
        siblings = [c.name for c in parent.children if c.name] if parent else []
        hint = did_you_mean(missing, siblings) if siblings else "check the component path"
        return [make("R004", "error", pointer, where, f"'{ref}': no component '{missing}' at that path", hint,
                     f"available here: {listing(siblings, 30) or 'none'}")]
    if not member:
        return [make("R005", "warning", pointer, where, f"'{ref}' points at a component, not a property",
                     "append the property, e.g. .props.text")]
    return _member(ctx, target, ref, parse_path(member), pointer, where)


def _split_component_ref(ref: str) -> tuple[str, str, str]:
    """`../A/B.props.x` -> ('../A/B', '.', 'props.x'); leading dots are path syntax, not separators."""
    prefix = re.match(r"^(?:\.{1,2}/)*\.{0,2}", ref).group(0)
    body = ref[len(prefix):]
    head, dot, member = body.partition(".")
    return prefix + head, dot, member


def _walk_relative(node: Node, path_part: str) -> tuple[Node | None, tuple[Node, str] | None]:
    current: Node | None = node
    for token in [t for t in path_part.split("/") if t]:
        if current is None:
            return None, None
        if token == ".":
            continue
        if token == "..":
            current = current.parent
            if current is None:
                return None, (node, "..")
            continue
        child = current.child(token)
        if child is None:
            return None, (current, token)
        current = child
    return current, None


def _first_missing(root: Node, names: list[str]) -> tuple[Node, str] | None:
    if not names or names[0] != root.name:
        return (root, names[0] if names else "") if names else None
    current = root
    for name in names[1:]:
        child = current.child(name)
        if child is None:
            return current, name
        current = child
    return None


def _member(ctx: Context, target: Node, ref: str, rest: list[Any], pointer: str, where: str) -> list[Diagnostic]:
    section = rest[0]
    if section not in _SECTIONS or section == "params":
        return [make("R003", "error", pointer, where, f"'{ref}': components have props, custom, meta and position",
                     did_you_mean(str(section), ("props", "custom", "meta", "position")))]
    if len(rest) < 2:
        return []
    name = str(rest[1])
    if section == "custom":
        declared = _declared(target.component.get("custom"), target.component.get("propConfig"), "custom")
        if name not in declared:
            return [make("R002", "warning", pointer, where, f"'{ref}': {target.path} has no custom '{name}'",
                         did_you_mean(name, declared) if declared else "declare it in the component's custom object")]
        return []
    if section == "meta" and name in META_EXTRA:
        return []
    if section == "props":
        component = ctx.component(target)
        if component is None:
            return []
        schema, owner = component.schema, component.id
    elif section == "position":
        parent = ctx.component(target.parent)
        if parent is None or parent.child_position_schema is None:
            return []
        schema, owner = parent.child_position_schema, f"children of {parent.id}"
    else:
        schema, owner = ctx.bundle.schema("meta-schema.json"), "meta"
    miss = check_path(SchemaRef(schema, schema), rest[1:], ctx.bundle)
    if miss is None or (miss.index > 0 and not miss.closed and not closest(miss.segment, miss.allowed, 0.8)):
        return []
    severity = "error" if miss.index == 0 or miss.closed else "warning"
    noun = {"props": "prop", "meta": "meta key", "position": "position key"}[section]
    return [make("R003", severity, pointer, where, f"'{ref}': {owner} has no {noun} '{miss.segment}'",
                 did_you_mean(miss.segment, miss.allowed), f"declared: {listing(miss.allowed, 40)}")]


# --- project references -----------------------------------------------------------------------


def _bound(component: dict[str, Any], prop_path: str) -> bool:
    config = component.get("propConfig")
    return isinstance(config, dict) and any(
        prop_path == key or prop_path.startswith(key + ".") or prop_path.startswith(key + "[")
        for key, entry in config.items() if isinstance(entry, dict) and "binding" in entry)


def _view_paths(ctx: Context, instance: Any, ref: SchemaRef, pointer: str, path: str) -> Iterator[tuple[str, str, str]]:
    """(pointer, prop path, value) for every string whose schema has format: view-path."""
    resolved = resolve(ref, ctx.bundle)
    if resolved is None or not isinstance(resolved.schema, dict):
        return
    if resolved.schema.get("format") == "view-path" and isinstance(instance, str):
        yield pointer, path, instance
        return
    if isinstance(instance, dict):
        shape = object_shape(resolved, ctx.bundle)
        if shape is None or shape.is_style:
            return
        for key, value in instance.items():
            sub = shape.properties.get(key) or shape.extra
            if sub is not None:
                yield from _view_paths(ctx, value, sub, join(pointer, key), f"{path}.{key}")
    elif isinstance(instance, list):
        items = items_ref(resolved, ctx.bundle)
        if items is not None:
            for i, value in enumerate(instance):
                yield from _view_paths(ctx, value, items, join(pointer, i), f"{path}[{i}]")


def _missing_view_hint(target: str, views: list[str]) -> str:
    """Suggest a view for a missing one, searching its own folder first and then the ancestors up to the root."""
    if not views:
        return "create the view first"
    folders = target.split("/")[:-1]
    for depth in range(len(folders), -1, -1):
        prefix = "".join(f"{part}/" for part in folders[:depth])
        match = closest(target, [v for v in views if v.startswith(prefix)], cutoff=0.5)
        if match:
            if depth == len(folders):
                return f"did you mean '{match}'?"
            return f"create the view first (deploy the views it uses before it); closest elsewhere: '{match}'"
    return did_you_mean(target, views, cutoff=0.5)


def _project_refs(ctx: Context) -> list[Diagnostic]:
    project = ctx.project
    assert project is not None
    out: list[Diagnostic] = []
    views = list(project.views)
    for node in ctx.nodes:
        component = ctx.component(node)
        props = node.props()
        if component is not None:
            for pointer, prop_path, value in _view_paths(ctx, props, SchemaRef(component.schema, component.schema),
                                                         join(node.pointer, "props"), "props"):
                if value and not _bound(node.component, prop_path) and project.find_view(value) is None:
                    out.append(make("R010", "error", pointer, node.path,
                                    f"view '{value}' does not exist in project {project.name}",
                                    _missing_view_hint(value, views)))
        if node.type == "ia.display.view" and isinstance(props.get("path"), str):
            bound = {k[len("props.params."):].split(".")[0] for k in (node.component.get("propConfig") or {})
                     if k.startswith("props.params.")}
            params = props.get("params") if isinstance(props.get("params"), dict) else {}
            out += _params(ctx, props["path"], params, bound, join(node.pointer, "props", "params"), node.path,
                           embedded=True)
        elif node.type == "ia.display.flex-repeater" and isinstance(props.get("path"), str):
            if not _bound(node.component, "props.instances") and isinstance(props.get("instances"), list):
                for i, instance in enumerate(props["instances"]):
                    if isinstance(instance, dict):
                        values = {k: v for k, v in instance.items() if k not in ("instancePosition", "instanceStyle")}
                        out += _params(ctx, props["path"], values, set(),
                                       join(node.pointer, "props", "instances", i), node.path, embedded=True)
        out += _event_views(ctx, node)
        out += _style_classes(ctx, node, props, join(node.pointer, "props"), "props")
    return out


def _named_query_refs(ctx: Context) -> list[Diagnostic]:
    """R016: a query binding names a named query that neither the project nor its parents define."""
    project = ctx.project
    assert project is not None
    out: list[Diagnostic] = []
    for node, config, base in _prop_configs(ctx):
        for prop, entry in config.items():
            binding = entry.get("binding") if isinstance(entry, dict) else None
            if not isinstance(binding, dict) or not isinstance(binding.get("config"), dict):
                continue
            if BINDING_ALIASES.get(str(binding.get("type")), binding.get("type")) != "query":
                continue
            bconf = binding["config"]
            query = bconf.get("queryPath")
            # Expression mode computes the path at runtime; a missing or empty path has nothing to look up.
            if bconf.get("mode") == "expression" or not isinstance(query, str) or not query.strip("/"):
                continue
            wanted = query.strip("/")
            if not project.has_named_query(wanted):
                match = closest(wanted, project.named_queries(), cutoff=0.6)
                out.append(make("R016", "error", join(base, prop, "binding", "config", "queryPath"),
                                node.path if node else "",
                                f"named query {wanted!r} does not exist in project {project.name}"
                                + (f"; did you mean '{match}'?" if match else ""),
                                "Create the named query in the Designer (Data > Named Queries) or fix "
                                "config.queryPath; PDK cannot run it without a database, so pdk_inspect shows "
                                "its result quality (Q001/Q002)."))
    return out


def _event_views(ctx: Context, node: Node) -> list[Diagnostic]:
    project = ctx.project
    assert project is not None
    out: list[Diagnostic] = []
    events = node.component.get("events")
    for group, handlers in (events.items() if isinstance(events, dict) else []):
        for name, handler in (handlers.items() if isinstance(handlers, dict) else []):
            for i, item in enumerate(handler if isinstance(handler, list) else [handler]):
                if not isinstance(item, dict) or not isinstance(item.get("config"), dict):
                    continue
                pointer = join(node.pointer, "events", group, name, *([i] if isinstance(handler, list) else []))
                config = item["config"]
                if item.get("type") == "nav":
                    view_key, params_key = "view", "params"
                elif item.get("type") == "popup" and config.get("type") == "open":
                    view_key, params_key = "viewPath", "viewParams"
                else:
                    continue
                target = config.get(view_key)
                if not isinstance(target, str) or not target:
                    continue
                if project.find_view(target) is None:
                    out.append(make("R010", "error", join(pointer, "config", view_key), node.path,
                                    f"view '{target}' does not exist in project {project.name}",
                                    _missing_view_hint(target, list(project.views))))
                    continue
                params = config.get(params_key) if isinstance(config.get(params_key), dict) else {}
                out += _params(ctx, target, params, set(), join(pointer, "config", params_key), node.path)
    return out


def _params(ctx: Context, view_path: str, values: dict[str, Any], bound: set[str], pointer: str,
            where: str, embedded: bool = False) -> list[Diagnostic]:
    """Port of perspective-compiler target.check_params against the project's own view files.

    Embedded views mirror output params into props.params, so direction is only checked for nav/popup.
    """
    assert ctx.project is not None
    target = ctx.project.view_params(view_path)
    if target is None:
        return []
    out: list[Diagnostic] = []
    supplied = set(values) | bound
    for name in sorted(supplied - target.keys()):
        # Undeclared params are ignored at runtime: a likely typo is an error, a leftover only a warning.
        match = closest(name, target, cutoff=0.75)
        out.append(make("R011", "error" if match else "warning", join(pointer, name), where,
                        f"param '{name}' is not declared by view '{view_path}'",
                        f"did you mean '{match}'?" if match else
                        (f"declared: {listing(target)}" if target else f"'{view_path}' declares no params")))
    for name in sorted(supplied & target.keys()):
        param = target[name]
        if param.direction == "output" and not embedded:
            out.append(make("R012", "warning", join(pointer, name), where,
                            f"param '{name}' of view '{view_path}' is output-only; passing it has no effect",
                            "remove it, or change its direction to inout"))
        if name in values and name not in bound and param.default is not None:
            expected, actual = _kind(param.default), _kind(values[name])
            if expected and actual and expected != actual:
                out.append(make("R014", "warning", join(pointer, name), where,
                                f"param '{name}' is {actual}; '{view_path}' declares a {expected} default",
                                f"pass a {expected}"))
    for name, param in sorted(target.items()):
        if param.direction in ("input", "inout") and param.default is None and name not in supplied:
            out.append(make("R013", "warning", pointer, where,
                            f"view '{view_path}' expects param '{name}' (no default) and it is not passed",
                            f"pass '{name}' or give it a default in '{view_path}'"))
    return out


def _kind(value: Any) -> str | None:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int | float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object" if isinstance(value, dict) else None


def _style_classes(ctx: Context, node: Node, value: Any, pointer: str, path: str) -> list[Diagnostic]:
    assert ctx.project is not None
    out: list[Diagnostic] = []
    if isinstance(value, dict):
        classes = value.get("classes")
        if isinstance(classes, str) and classes.strip() and not _bound(node.component, f"{path}.classes"):
            for name in classes.split():
                if not ctx.project.has_style_class(name):
                    out.append(make("R015", "warning", join(pointer, "classes"), node.path,
                                    f"style class '{name}' does not exist in project {ctx.project.name}",
                                    "create it under Perspective > Styles, or as .psc-<name> in the project "
                                    "stylesheet, or fix the name"))
        for key, sub in value.items():
            if key != "classes":
                out += _style_classes(ctx, node, sub, join(pointer, key), f"{path}.{key}")
    elif isinstance(value, list):
        for i, sub in enumerate(value):
            out += _style_classes(ctx, node, sub, join(pointer, i), f"{path}[{i}]")
    return out

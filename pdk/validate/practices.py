"""BP family: bindings and performance done the way Ignition integrators recommend (forum regulars, IA's ICC load-time
sessions, the user manual). Only what breaks or costs is an error or a warning; the rest is information.

Per view: tag() in bindings, script transforms that should be map or expression transforms or that block, binding
cycles, change scripts without an origin guard, bound input params, polling, clocks and runScript, pass-through
expressions, a tag bound many times, UDTs dropped by binding, literal colors, repeated inline styles, bindings that
move the layout. With a project (pdk.graph): how deep embedded views nest, and heavy work in a repeated view.
"""

from __future__ import annotations

import ast
import json
import re
from collections import Counter
from collections.abc import Iterator
from typing import Any

from pdk.diagnostics import Diagnostic, join, make
from pdk.validate.code import WRAPPER
from pdk.validate.config import BINDING_ALIASES
from pdk.validate.context import Context
from pdk.validate.refs import _split_component_ref, _walk_relative, binding_expressions
from pdk.validate.tree import Node, find_by_path

POLL_MIN_S = 10
NOW_MIN_MS = 1000
RUNSCRIPT_MIN_MS = 5000
SAME_TAG_MAX = 3
NESTING_MAX = 3
REPEATED_STYLE_MIN = 3
HEAVY = {"ia.chart.gauge", "ia.chart.xy", "ia.chart.pie", "ia.chart.timeseries", "ia.chart.powerchart",
         "ia.display.markdown", "ia.display.alarmstatustable", "ia.display.alarmjournaltable", "ia.display.table"}
LAYOUT_PROPS = ("position.basis", "position.grow", "position.shrink", "position.width", "position.height",
                "props.style.display", "props.style.margin", "props.style.padding", "props.style.borderWidth",
                "props.style.width", "props.style.height")
COLOR_KEYS = ("color", "backgroundColor", "borderColor", "fill", "stroke", "outlineColor")
_COLOR = re.compile(r"^\s*(#[0-9a-fA-F]{3,8}|rgba?\(|hsla?\()")
_TAG_CALL = re.compile(r"\btag\s*\(")
_NOW = re.compile(r"\bnow\s*\(\s*(\d*)\s*\)")
_RUNSCRIPT = re.compile(r"\brunScript\s*\(")
_BLOCKING = re.compile(r"system\.(tag\.(read|write)\w*|db\.\w+|net\.\w+|util\.(sendRequest|getHttpClient))\b")
_REF = re.compile(r"\{([^{}]+)\}")
_ONLY_REF = re.compile(r"^\s*\{([^{}]+)\}\s*$")
# A write to a property of the script's owner: self is the component, or the view in a view's change script.
_SELF_WRITE = re.compile(r"\bself\.(props|custom|position|meta)(\.[\w\[\]'\"]+)*\s*=[^=]")
_VIEW_SELF_WRITE = re.compile(r"\bself\.(params|custom|props)(\.[\w\[\]'\"]+)*\s*=[^=]")
_BUILTINS = {"str", "int", "float", "round", "abs", "min", "max", "len", "bool", "unicode"}
_METHODS = {"upper", "lower", "strip", "format", "replace", "title", "capitalize", "zfill", "rstrip", "lstrip"}


def check(ctx: Context) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    tag_paths: Counter[str] = Counter()
    first_pointer: dict[str, tuple[str, str]] = {}
    expressions = 0
    clocks: list[tuple[str, str]] = []
    for node, config, base in _prop_configs(ctx):
        where = node.path if node else ""
        for prop, entry in config.items():
            if not isinstance(entry, dict):
                continue
            pointer = join(base, prop)
            binding = entry.get("binding")
            if isinstance(binding, dict):
                bpointer = join(pointer, "binding")
                btype = BINDING_ALIASES.get(str(binding.get("type")), binding.get("type"))
                bconfig = binding.get("config") if isinstance(binding.get("config"), dict) else {}
                out += _expressions(binding, bpointer, where, clocks)
                out += _transforms(binding, bpointer, where)
                out += _query(btype, bconfig, bpointer, where)
                if btype == "tag" and isinstance(bconfig.get("tagPath"), str) and bconfig["tagPath"].strip():
                    key = _tag_key(bconfig)
                    tag_paths[key] += 1
                    first_pointer.setdefault(key, (join(bpointer, "config", "tagPath"), where))
                if btype in ("expr", "expression") and isinstance(bconfig.get("expression"), str):
                    expressions += 1
                    only = _ONLY_REF.match(bconfig["expression"])
                    if only and not only.group(1).strip().startswith("["):
                        out.append(make("BP011", "info", join(bpointer, "config", "expression"), where,
                                        f"the expression only reads {{{only.group(1).strip()}}}",
                                        "use a property binding to that path: same result, less work per change"))
                if node is None and prop.startswith("params.") and entry.get("paramDirection", "input") == "input":
                    out.append(make("BP007", "warning", bpointer, where,
                                    f"view param '{prop[len('params.'):]}' is input but bound inside the view",
                                    "the caller's value and the binding fight over it: bind a custom property "
                                    "instead, or make the param output if the view computes it"))
                if node is not None and prop in LAYOUT_PROPS:
                    out.append(make("BP018", "info", bpointer, where, f"a binding on {prop} moves the layout",
                                    "every change makes the browser lay the page out again; for states, change "
                                    "color or an icon rather than size, or hide with meta.visible"))
            on_change = entry.get("onChange")
            if isinstance(on_change, dict) and isinstance(on_change.get("script"), str):
                out += _on_change(on_change["script"], prop, join(pointer, "onChange", "script"), where,
                                  of_view=node is None)
    for key, count in tag_paths.items():
        if count > SAME_TAG_MAX:
            pointer, where = first_pointer[key]
            out.append(make("BP012", "info", pointer, where,
                            f"the same tag is bound {count} times in this view ({key})",
                            "bind it once to a custom property and reference that property: one place to change, "
                            "one overlay to read (subscriptions are shared, so this is about upkeep, not speed)"))
    if len(clocks) > 1:
        pointer, where = clocks[1]
        out.append(make("BP009", "info", pointer, where, f"{len(clocks)} clocks (now()) in this view",
                        "keep one view custom property bound to now(1000) and reference it everywhere"))
    out += _cycles(ctx)
    out += _drop_config(ctx)
    out += _styles(ctx)
    if ctx.project is not None:
        out += _project_rules(ctx)
    return out


# --- expressions ---------------------------------------------------------------------------------


def _expressions(binding: dict[str, Any], pointer: str, where: str, clocks: list[tuple[str, str]]) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    for text, epointer, _transform in binding_expressions(binding, pointer):
        if _TAG_CALL.search(text):
            out.append(make("BP001", "warning", epointer, where, "tag() inside a binding expression",
                            "bind a custom property with an indirect tag binding (tagPath '{1}/Member', "
                            "references {'1': '{view.params.tagPath}'}) and reference it: tag() blocks on its first "
                            "call and subscribes again whenever any part of the path changes"))
        for match in _NOW.finditer(text):
            clocks.append((epointer, where))
            rate = int(match.group(1)) if match.group(1) else NOW_MIN_MS
            if rate < NOW_MIN_MS:
                out.append(make("BP009", "warning", epointer, where,
                                f"now({rate}) re-runs this binding every {rate} ms",
                                "use now(1000) or slower, from one custom property the view shares"))
        for match in _RUNSCRIPT.finditer(text):
            rate = _runscript_rate(text, match.end())
            severity = "warning" if rate is not None and 0 < rate < RUNSCRIPT_MIN_MS else "info"
            out.append(make("BP010", severity, epointer, where,
                            "runScript in a binding" + (f", polling every {rate} ms" if severity == "warning" else ""),
                            "prefer an expression, a transform, or a gateway timer script that writes a tag the "
                            "view binds to; runScript runs Python on the gateway for every session"))
    return out


def _runscript_rate(text: str, start: int) -> int | None:
    """The poll rate argument of runScript(script, pollRate, ...), when it is a literal."""
    depth, quote, args, current = 1, "", [], ""
    for char in text[start:]:
        if quote:
            current += char
            if char == quote:
                quote = ""
            continue
        if char in "\"'":
            quote = char
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
            if depth == 0:
                break
        elif char == "," and depth == 1:
            args.append(current)
            current = ""
            continue
        current += char
    args.append(current)
    return int(args[1].strip()) if len(args) > 1 and args[1].strip().isdigit() else None


# --- transforms ----------------------------------------------------------------------------------


def _transforms(binding: dict[str, Any], pointer: str, where: str) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    for i, transform in enumerate(binding.get("transforms") or []):
        if not isinstance(transform, dict) or transform.get("type") != "script" or not isinstance(
                transform.get("code"), str):
            continue
        tpointer = join(pointer, "transforms", i, "code")
        code = transform["code"]
        blocking = _BLOCKING.search(code)
        if blocking:
            out.append(make("BP004", "warning", tpointer, where, f"the script transform calls {blocking.group(0)}",
                            "a transform runs on every change of its binding, in the session: read tags with a tag "
                            "binding, query with a query binding, or move the work to a gateway script"))
            continue
        body = _function(code)
        if body is None:
            continue
        if _map_like(body):
            out.append(make("BP002", "info", tpointer, where, "the script transform only maps values to values",
                            "use a map transform: no Python per change, and the table reads at a glance"))
        elif _expression_like(body):
            out.append(make("BP003", "info", tpointer, where, "the script transform only computes from value",
                            "use an expression transform (or a format transform for numbers and dates); if it "
                            "builds a tag path, an indirect tag binding does that by itself"))
    return out


def _function(code: str) -> list[ast.stmt] | None:
    try:
        tree = ast.parse(WRAPPER + code)
    except SyntaxError:
        return None
    function = tree.body[0] if tree.body and isinstance(tree.body[0], ast.FunctionDef) else None
    if function is None:
        return None
    return [s for s in function.body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))
            and not isinstance(s, ast.Pass)]


def _constant(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, ast.UnaryOp) and isinstance(node.operand, ast.Constant):
        return True
    return isinstance(node, ast.Tuple | ast.List) and all(_constant(e) for e in node.elts)


def _is_value(node: ast.AST) -> bool:
    return isinstance(node, ast.Name) and node.id == "value"


def _value_test(test: ast.AST) -> bool:
    if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.Or):
        return all(_value_test(v) for v in test.values)
    return (isinstance(test, ast.Compare) and _is_value(test.left) and len(test.ops) == 1
            and isinstance(test.ops[0], ast.Eq | ast.In) and _constant(test.comparators[0]))


def _map_like(body: list[ast.stmt]) -> bool:
    if len(body) == 1 and isinstance(body[0], ast.Return) and body[0].value is not None:
        value = body[0].value
        if isinstance(value, ast.Subscript) and isinstance(value.value, ast.Dict) and _is_value(value.slice):
            return all(k is not None and _constant(k) and _constant(v) for k, v in zip(value.value.keys,
                                                                                     value.value.values, strict=True))
        if (isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute) and value.func.attr == "get"
                and isinstance(value.func.value, ast.Dict) and value.args and _is_value(value.args[0])):
            return True
    branches = 0
    for statement in body:
        node: ast.stmt = statement
        if not isinstance(node, ast.If):
            if not _returns_constant([node]):
                return False
            continue
        while True:
            if not _value_test(node.test) or not _returns_constant(node.body):
                return False
            branches += 1
            if len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If):
                node = node.orelse[0]
                continue
            if node.orelse and not _returns_constant(node.orelse):
                return False  # the last else computes something: not a plain mapping
            break
    return branches >= 2


def _returns_constant(statements: list[ast.stmt]) -> bool:
    return (len(statements) == 1 and isinstance(statements[0], ast.Return) and statements[0].value is not None
            and _constant(statements[0].value))


def _expression_like(body: list[ast.stmt]) -> bool:
    if len(body) != 1 or not isinstance(body[0], ast.Return) or body[0].value is None:
        return False
    uses_value = False
    for node in ast.walk(body[0].value):
        if isinstance(node, ast.Name):
            if node.id == "value":
                uses_value = True
            elif node.id not in _BUILTINS:
                return False
        elif isinstance(node, ast.Call):
            func = node.func
            if not (isinstance(func, ast.Name) and func.id in _BUILTINS
                    or isinstance(func, ast.Attribute) and func.attr in _METHODS):
                return False
        elif isinstance(node, ast.Attribute):
            if node.attr not in _METHODS:
                return False
        elif not isinstance(node, ast.expr | ast.operator | ast.cmpop | ast.boolop | ast.unaryop | ast.expr_context
                                  | ast.keyword | ast.FormattedValue):
            return False
    return uses_value


# --- query bindings ------------------------------------------------------------------------------


def _query(btype: Any, config: dict[str, Any], pointer: str, where: str) -> list[Diagnostic]:
    if btype != "query" or not isinstance(config.get("polling"), dict):
        return []
    polling = config["polling"]
    rate = polling.get("rate", 5)
    if not polling.get("enabled") or not isinstance(rate, int | float) or rate <= 0:
        return []
    out = []
    if rate < POLL_MIN_S:
        out.append(make("BP008", "warning", join(pointer, "config", "polling", "rate"), where,
                        f"the query runs every {rate:g} s in every open session",
                        f"poll every {POLL_MIN_S} s or slower, or not at all: refresh it with refreshBinding() "
                        "from a button or after a write, and cache the named query"))
    if config.get("bypassCache"):
        out.append(make("BP008", "info", join(pointer, "config", "bypassCache"), where,
                        "a polling query that bypasses the named query cache",
                        "let sessions share the cached result unless each one needs its own"))
    return out


def _tag_key(config: dict[str, Any]) -> str:
    references = config.get("references") if isinstance(config.get("references"), dict) else {}
    path = str(config["tagPath"]).strip()
    for key, value in references.items():
        path = path.replace("{" + str(key) + "}", str(value))
    return path


# --- change scripts ------------------------------------------------------------------------------


def _on_change(script: str, prop: str, pointer: str, where: str, *, of_view: bool) -> list[Diagnostic]:
    """BP006: the change script writes its own property back. In a component's script `self.view.custom.x` is the
    view's property, another one; in the view's own script `self` is the view (`self.params.x`)."""
    if "origin" in script:
        return []
    own = prop.split("[")[0]
    for match in (_VIEW_SELF_WRITE if of_view else _SELF_WRITE).finditer(script):
        written = match.group(0).split("=")[0].strip()[len("self."):].split("[")[0]
        if written == own or written.startswith(own + ".") or own.startswith(written + "."):
            return [make("BP006", "warning", pointer, where, f"the change script of {prop} writes {prop} back",
                         "check origin first (if origin != 'BindingWriteback': ...) or the write fires the script "
                         "again and the two ping-pong")]
    return []


# --- binding cycles ------------------------------------------------------------------------------


def _owner_key(node: Node | None, prop: str) -> str:
    parts = prop.split(".")
    top = ".".join(parts[:2]) if len(parts) > 1 else prop
    return f"{node.pointer if node else 'view'}:{top.split('[')[0]}"


def _ref_key(ctx: Context, node: Node | None, ref: str) -> str | None:
    """The property a reference reads, as an owner key (component pointer or 'view', and scope.name)."""
    ref = ref.strip()
    if ref.startswith(("/", ".")) and ctx.root is not None:
        path_part, _, member = _split_component_ref(ref)
        if ref.startswith("/"):
            target = find_by_path(ctx.root, [n for n in path_part.split("/") if n])
        else:
            target = _walk_relative(node, path_part)[0] if node else None
        return _owner_key(target, member) if target is not None and member else None
    head, _, rest = ref.partition(".")
    if head == "view" and rest:
        return _owner_key(None, rest)
    if head == "this" and node is not None and rest:
        return _owner_key(node, rest)
    if head == "parent" and node is not None and node.parent is not None and rest:
        return _owner_key(node.parent, rest)
    return None


def _cycles(ctx: Context) -> list[Diagnostic]:
    edges: dict[str, set[str]] = {}
    mirrored: set[tuple[str, str]] = set()  # (source, target) of bidirectional property bindings
    where_of: dict[str, tuple[str, str]] = {}
    for node, config, base in _prop_configs(ctx):
        for prop, entry in config.items():
            binding = entry.get("binding") if isinstance(entry, dict) else None
            if not isinstance(binding, dict):
                continue
            target = _owner_key(node, prop)
            where_of.setdefault(target, (join(base, prop, "binding"), node.path if node else ""))
            sources: set[str] = set()
            bconfig = binding.get("config") if isinstance(binding.get("config"), dict) else {}
            btype = BINDING_ALIASES.get(str(binding.get("type")), binding.get("type"))
            if btype == "property" and isinstance(bconfig.get("path"), str):
                key = _ref_key(ctx, node, bconfig["path"])
                if key:
                    sources.add(key)
                    if bconfig.get("bidirectional"):
                        mirrored.add((key, target))
            for text, _p, in_transform in binding_expressions(binding, join(base, prop, "binding")):
                for match in _REF.finditer(text):
                    ref = match.group(1)
                    if in_transform and (ref == "value" or ref.startswith("value.")):
                        continue
                    key = _ref_key(ctx, node, ref)
                    if key:
                        sources.add(key)
            for source in sources:
                if source != target:
                    edges.setdefault(source, set()).add(target)
    out: list[Diagnostic] = []
    reported: set[frozenset[str]] = set()
    for start in list(edges):
        cycle = _find_cycle(edges, start)
        if cycle and frozenset(cycle) not in reported:
            reported.add(frozenset(cycle))
            names = {n.pointer: n.path for n in ctx.nodes}
            pointer, where = where_of.get(cycle[0], ("", ""))
            if len(cycle) == 2 and (cycle[0], cycle[1]) in mirrored and (cycle[1], cycle[0]) in mirrored:
                out.append(make("BP005", "warning", pointer, where,
                                f"two bidirectional bindings mirror each other: {_short(cycle[0], names)} <-> "
                                f"{_short(cycle[1], names)}",
                                "keep one: a single bidirectional binding already writes back; two can ping-pong"))
                continue
            out.append(make("BP005", "error", pointer, where,
                            "bindings feed each other in a loop: "
                            + " -> ".join(_short(k, names) for k in cycle + [cycle[0]]),
                            "break the loop: one of them must read a source outside it (a tag, a param); loops keep "
                            "Perspective threads busy and the values never settle"))
    return out


def _find_cycle(edges: dict[str, set[str]], start: str) -> list[str] | None:
    stack: list[tuple[str, list[str]]] = [(start, [start])]
    while stack:
        current, path = stack.pop()
        for following in edges.get(current, ()):
            if following == start:
                return path
            if following not in path and len(path) < 12:
                stack.append((following, path + [following]))
    return None


def _short(key: str, names: dict[str, str]) -> str:
    owner, _, prop = key.partition(":")
    return f"view.{prop}" if owner == "view" else f"{names.get(owner, owner)}.{prop}"


# --- drop config and styles ----------------------------------------------------------------------


def _drop_config(ctx: Context) -> list[Diagnostic]:
    props = ctx.view.get("props") if isinstance(ctx.view.get("props"), dict) else {}
    drop = props.get("dropConfig") if isinstance(props.get("dropConfig"), dict) else {}
    out = []
    for i, entry in enumerate(drop.get("udts") if isinstance(drop.get("udts"), list) else []):
        if isinstance(entry, dict) and entry.get("action", "bind") == "bind":
            out.append(make("BP013", "warning", join("/props/dropConfig/udts", i, "action"), "",
                            "dropping a UDT binds every member of it into the param",
                            "set action 'path' and pass the UDT's path to a tagPath param: the view binds only what "
                            "it shows, and keeps tag properties like EngUnit"))
    return out


def _styles(ctx: Context) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    seen: dict[str, list[Node]] = {}
    colors: list[tuple[Node, str, str]] = []
    for node in ctx.nodes:
        style = node.props().get("style")
        if not isinstance(style, dict):
            continue
        for key in COLOR_KEYS:
            value = style.get(key)
            if isinstance(value, str) and _COLOR.match(value):
                colors.append((node, key, value.strip()))
        own = {k: v for k, v in style.items() if k != "classes"}
        if len(own) >= 2:
            seen.setdefault(json.dumps(own, sort_keys=True), []).append(node)
    if colors:
        node, key, value = colors[0]
        distinct = sorted({value for _n, _k, value in colors})
        out.append(make("BP016", "info", join(node.pointer, "props", "style", key), node.path,
                        f"{len(colors)} literal color(s) in inline styles ({', '.join(distinct[:4])}"
                        + (", ..." if len(distinct) > 4 else "") + ")",
                        "use theme variables (var(--neutral-90)) or style classes, so the theme and the "
                        "high-performance palette apply everywhere at once",
                        "; ".join(f"{n.path} {k} {v}" for n, k, v in colors[:12])))
    repeated = sorted((nodes for nodes in seen.values() if len(nodes) >= REPEATED_STYLE_MIN), key=len, reverse=True)
    if repeated:
        nodes = repeated[0]
        out.append(make("BP017", "info", join(nodes[0].pointer, "props", "style"), nodes[0].path,
                        f"{len(repeated)} inline style(s) repeated on {REPEATED_STYLE_MIN}+ components (the most on "
                        f"{len(nodes)})",
                        "make each a style class and set props.style.classes: one place to change it",
                        "; ".join(f"{len(group)}x {group[0].path}" for group in repeated[:8])))
    return out


# --- with a project ------------------------------------------------------------------------------


def _project_rules(ctx: Context) -> list[Diagnostic]:
    from pdk.graph import Graph, read_view

    project = ctx.project
    assert project is not None
    graph = Graph.of(project)
    info = read_view(ctx.view_path or "", ctx.view)
    out: list[Diagnostic] = []
    for use in info.uses:
        if use.kind in ("embed", "repeater"):
            depth = graph.depth(use.target, frozenset({ctx.view_path or ""})) + 1
            if depth > NESTING_MAX:
                out.append(make("BP014", "warning", use.pointer, use.component,
                                f"embedded views nest {depth} levels deep from here",
                                f"keep it to {NESTING_MAX} or fewer: each level adds a load round and bindings that "
                                "wait for the level above; flatten the inner views"))
    if ctx.view_path and graph.repeated(ctx.view_path):
        heavy = [n for n in ctx.nodes if n.type in HEAVY]
        queries = [(node, base, prop) for node, config, base in _prop_configs(ctx) for prop, entry in config.items()
                   if isinstance(entry, dict) and isinstance(entry.get("binding"), dict)
                   and BINDING_ALIASES.get(str(entry["binding"].get("type")), entry["binding"].get("type"))
                   in ("query", "tag-history")]
        repeaters = ", ".join(sorted({f"{u.view} {u.component}" for u in graph.repeated(ctx.view_path)}))
        for node, base, prop in queries:
            out.append(make("BP015", "warning", join(base, prop, "binding"), node.path if node else "",
                            f"a {prop} query runs once per repeater instance ({repeaters})",
                            "query once in the view that holds the repeater and pass each instance its row"))
        for node in heavy[:3]:
            out.append(make("BP015", "info", node.pointer, node.path,
                            f"{node.type} is heavy and this view is repeated ({repeaters})",
                            "with many instances prefer a light component (a label, a moving analog indicator) "
                            "and open the heavy one in a popup"))
    for node in ctx.nodes:
        config = node.component.get("propConfig")
        if node.type == "ia.display.flex-repeater" and isinstance(config, dict) and "props.instances" in config:
            out.append(make("BP018", "info", join(node.pointer, "propConfig", "props.instances", "binding"), node.path,
                            "binding props.instances rebuilds every instance on each change",
                            "keep the list the same size and update the instance values, or bind the values inside "
                            "the instance view"))
    return out


def _prop_configs(ctx: Context) -> Iterator[tuple[Node | None, dict[str, Any], str]]:
    owners: list[tuple[Node | None, Any, str]] = [(None, ctx.view.get("propConfig"), "/propConfig")]
    owners += [(n, n.component.get("propConfig"), join(n.pointer, "propConfig")) for n in ctx.nodes]
    for node, config, base in owners:
        if isinstance(config, dict):
            yield node, config, base

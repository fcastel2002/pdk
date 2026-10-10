"""B and E families: propConfig (bindings, transforms, onChange, paramDirection), events and scripts."""

from __future__ import annotations

from typing import Any

from pdk.diagnostics import Diagnostic, join, make
from pdk.shape import SchemaRef, check_path, parse_path
from pdk.suggest import closest, did_you_mean, listing
from pdk.validate.context import Context
from pdk.validate.schema import META_EXTRA
from pdk.validate.tree import Node

BINDING_TYPES = ("tag", "query", "expr", "expr-struct", "property", "tag-history", "http")
BINDING_ALIASES = {
    "expression": "expr", "expression-structure": "expr-struct", "structure": "expr-struct", "prop": "property",
    "tagHistory": "tag-history", "history": "tag-history", "sql": "query", "named-query": "query",
}
TRANSFORM_TYPES = ("script", "map", "expression", "format")
TRANSFORM_ALIASES = {"expr": "expression", "python": "script", "mapping": "map"}
TRANSFORM_SCHEMAS = {"format": "transform-format.json", "expression": "transform-expr.json"}
# Hand-written where the shipped schema lags the Designer (transform-map.json lacks inputType 'scalar').
LOCAL_TRANSFORM_SCHEMAS: dict[str, dict[str, Any]] = {
    "script": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]},
    "map": {"type": "object", "required": ["mappings"],
            "properties": {"mappings": {"type": "array"}, "fallback": {}, "inputType": {"type": "string"},
                           "outputType": {"type": "string"}}},
}
# Keys the Designer writes that the shipped (closed) binding schemas do not declare.
DESIGNER_BINDING_KEYS = {"tag": {"fallbackDelay"}, "tag-history": {"enableValueCache"}}
PROPCONFIG_KEYS = ("binding", "persistent", "access", "onChange", "paramDirection")
BINDING_KEYS = ("type", "config", "transforms", "overlayOptOut", "enabled")
PARAM_DIRECTIONS = ("input", "output", "inout")
ACCESS_LEVELS = ("PUBLIC", "PRIVATE", "SYSTEM")
COMPONENT_PREFIXES = ("props", "custom", "meta", "position")
VIEW_PREFIXES = ("params", "custom", "props")

GATEWAY_ACTIONS = ("script", "auth-challenge")
CLIENT_ACTIONS = ("nav", "popup", "dock", "alter-dock", "login", "logout", "fullscreen", "request-print",
                  "refresh", "theme", "debug", "console-log", "alter-logging")
ACTION_ALIASES = {"navigate": "nav", "navigation": "nav", "openPopup": "popup", "python": "script",
                  "runScript": "script"}
EVENT_GROUPS = ("dom", "component", "system")
SYSTEM_EVENTS = ("onStartup", "onShutdown")
HANDLER_KEYS = ("type", "scope", "config", "enabled")
SCRIPT_KEYS = ("customMethods", "messageHandlers", "extensionFunctions")


def check(ctx: Context) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    view_config = ctx.view.get("propConfig")
    if isinstance(view_config, dict):
        out += _prop_config(ctx, view_config, "/propConfig", None)
    if isinstance(ctx.view.get("events"), dict):
        out += _events(ctx, ctx.view["events"], "/events", None)
    for node in ctx.nodes:
        c = node.component
        if isinstance(c.get("propConfig"), dict):
            out += _prop_config(ctx, c["propConfig"], join(node.pointer, "propConfig"), node)
        if isinstance(c.get("events"), dict):
            out += _events(ctx, c["events"], join(node.pointer, "events"), node)
        if isinstance(c.get("scripts"), dict):
            for key in c["scripts"]:
                if key not in SCRIPT_KEYS:
                    out.append(make("E006", "warning", join(node.pointer, "scripts", key), node.path,
                                    f"unknown scripts key '{key}'", did_you_mean(key, SCRIPT_KEYS)))
    return out


# --- propConfig -------------------------------------------------------------------------------


def _prop_config(ctx: Context, config: dict[str, Any], base: str, node: Node | None) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    where = node.path if node else ""
    for path, entry in config.items():
        pointer = join(base, path)
        out += _target(ctx, path, pointer, node)
        if not isinstance(entry, dict):
            out.append(make("B002", "error", pointer, where, f"propConfig entry for '{path}' must be an object",
                            'use {"binding": {...}} or {"persistent": true}'))
            continue
        for key in entry:
            if key not in PROPCONFIG_KEYS:
                out.append(make("B002", "error", join(pointer, key), where, f"unknown propConfig key '{key}'",
                                did_you_mean(key, PROPCONFIG_KEYS)))
        if "paramDirection" in entry:
            direction = entry["paramDirection"]
            if node is not None or not path.startswith("params."):
                out.append(make("B007", "error", join(pointer, "paramDirection"), where,
                                f"paramDirection on '{path}': only view params have a direction",
                                "remove it, or declare the value as a view param (params.<name>)"))
            elif direction not in PARAM_DIRECTIONS:
                out.append(make("B007", "error", join(pointer, "paramDirection"), where,
                                f"invalid paramDirection {direction!r}",
                                did_you_mean(str(direction), PARAM_DIRECTIONS)))
        if "persistent" in entry and not isinstance(entry["persistent"], bool):
            out.append(make("B009", "warning", join(pointer, "persistent"), where, "persistent must be a boolean",
                            "use true or false"))
        if "access" in entry and entry["access"] not in ACCESS_LEVELS:
            out.append(make("B009", "warning", join(pointer, "access"), where,
                            f"invalid access {entry['access']!r}", did_you_mean(str(entry["access"]), ACCESS_LEVELS)))
        if "onChange" in entry:
            out += _on_change(entry["onChange"], join(pointer, "onChange"), where)
        if "binding" in entry:
            out += _binding(ctx, entry["binding"], join(pointer, "binding"), where)
    return out


def _target(ctx: Context, path: str, pointer: str, node: Node | None) -> list[Diagnostic]:
    """The propConfig key must name a property the component or view actually has."""
    where = node.path if node else ""
    segments = parse_path(path)
    head = segments[0] if segments else ""
    prefixes = COMPONENT_PREFIXES if node else VIEW_PREFIXES
    if head not in prefixes:
        hint = ("components have no params; use custom.<name>" if node and head == "params"
                else did_you_mean(str(head), prefixes))
        return [make("B001", "error", pointer, where, f"propConfig path '{path}' must start with "
                     f"{', '.join(p + '.' for p in prefixes)}", hint)]
    if len(segments) < 2:
        return []
    bundle = ctx.bundle
    owner = ""
    if head == "props":
        if node is None:
            schema = bundle.schema("view-props-schema.json")
            owner = "view"
        else:
            component = ctx.component(node)
            if component is None:
                return []
            schema, owner = component.schema, component.id
    elif head == "position" and node is not None:
        parent = ctx.component(node.parent)
        if parent is None or parent.child_position_schema is None:
            return []
        schema, owner = parent.child_position_schema, f"children of {parent.id}"
    elif head == "meta":
        schema, owner = bundle.schema("meta-schema.json"), "meta"
        if segments[1] in META_EXTRA:
            return []
    else:
        return []
    miss = check_path(SchemaRef(schema, schema), segments[1:], bundle)
    if miss is None:
        return []
    if miss.index > 0 and not miss.closed and not closest(miss.segment, miss.allowed, cutoff=0.8):
        return []  # nested free-form key; only likely typos are reported
    severity = "error" if miss.index == 0 or miss.closed else "warning"
    return [make("B001", severity, pointer, where,
                 f"propConfig targets '{path}', but {owner} has no '{miss.segment}'",
                 did_you_mean(miss.segment, miss.allowed), f"declared: {listing(miss.allowed, 40)}")]


def _on_change(value: Any, pointer: str, where: str) -> list[Diagnostic]:
    if not isinstance(value, dict) or not isinstance(value.get("script"), str):
        return [make("B008", "error", pointer, where, "onChange needs a script string",
                     'use {"enabled": true, "script": "\\t..."}')]
    return [make("B008", "warning", join(pointer, key), where, f"unknown onChange key '{key}'",
                 did_you_mean(key, ("script", "enabled")))
            for key in value if key not in ("script", "enabled")]


def _binding(ctx: Context, binding: Any, pointer: str, where: str) -> list[Diagnostic]:
    if not isinstance(binding, dict):
        return [make("B004", "error", pointer, where, "binding must be an object",
                     'use {"type": "expr", "config": {"expression": "..."}}')]
    out: list[Diagnostic] = []
    for key in binding:
        if key not in BINDING_KEYS:
            out.append(make("B004", "warning", join(pointer, key), where, f"unknown binding key '{key}'",
                            did_you_mean(key, BINDING_KEYS)))
    btype = binding.get("type")
    if btype not in BINDING_TYPES:
        alias = BINDING_ALIASES.get(str(btype))
        hint = f"use '{alias}'" if alias else did_you_mean(str(btype), BINDING_TYPES)
        message = "binding has no type" if btype is None else f"unknown binding type {btype!r}"
        out.append(make("B003", "error", join(pointer, "type"), where, message, hint,
                        f"binding types: {', '.join(BINDING_TYPES)}"))
    config = binding.get("config")
    if not isinstance(config, dict):
        out.append(make("B004", "error", join(pointer, "config"), where, "binding has no config object",
                        "add config (see pdk_catalog(topic='bindings'))"))
    elif btype in BINDING_TYPES:
        out += _config_against(ctx, ctx.bundle.schema(f"binding-{btype}.json"), config, join(pointer, "config"),
                               where, "B004", f"{btype} binding config", DESIGNER_BINDING_KEYS.get(btype, set()))
    transforms = binding.get("transforms")
    if transforms is not None:
        if not isinstance(transforms, list):
            out.append(make("B006", "error", join(pointer, "transforms"), where, "transforms must be an array",
                            'use "transforms": [{"type": "expression", "expression": "..."}]'))
        else:
            for i, transform in enumerate(transforms):
                out += _transform(ctx, transform, join(pointer, "transforms", i), where)
    return out


def _transform(ctx: Context, transform: Any, pointer: str, where: str) -> list[Diagnostic]:
    if not isinstance(transform, dict):
        return [make("B006", "error", pointer, where, "transform must be an object",
                     'use {"type": "expression", "expression": "{value}"}')]
    ttype = transform.get("type")
    if ttype not in TRANSFORM_TYPES:
        alias = TRANSFORM_ALIASES.get(str(ttype))
        hint = (f"use '{alias}' (transforms say 'expression'; bindings say 'expr')" if alias == "expression"
                else f"use '{alias}'" if alias else did_you_mean(str(ttype), TRANSFORM_TYPES))
        message = "transform has no type" if ttype is None else f"unknown transform type {ttype!r}"
        return [make("B005", "error", join(pointer, "type"), where, message, hint)]
    body = {k: v for k, v in transform.items() if k not in ("type", "enabled")}
    schema = LOCAL_TRANSFORM_SCHEMAS.get(ttype) or ctx.bundle.schema(TRANSFORM_SCHEMAS[ttype])
    return _config_against(ctx, schema, body, pointer, where, "B006", f"{ttype} transform", set())


def _config_against(
    ctx: Context, schema: dict[str, Any], config: dict[str, Any], pointer: str, where: str, code: str,
    label: str, designer_keys: set[str],
) -> list[Diagnostic]:
    """Binding/transform configs: trust top-level required, types and key names; enums only warn.

    The shipped binding schemas lag the Designer (closed objects missing newer keys, stale enums),
    so nested `required` is ignored and unknown keys without a close match are only warnings.
    """
    out: list[Diagnostic] = []
    declared = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    for key in schema.get("required") or []:
        if key not in config:
            out.append(make(code, "error", join(pointer, key), where, f"{label} requires '{key}'",
                            f"add '{key}'", f"declared: {listing(declared, 40)}"))
    for key in config:
        if key in declared or key in designer_keys or not declared:
            continue
        match = closest(key, declared, cutoff=0.6)
        if match:
            out.append(make(code, "error", join(pointer, key), where, f"unknown {label} key '{key}'",
                            f"did you mean '{match}'?", f"declared: {listing(declared, 40)}"))
        else:
            out.append(make(code, "warning", join(pointer, key), where,
                            f"'{key}' is not declared for {label} in Perspective {ctx.bundle.version}",
                            "check the key name; undeclared keys are ignored", f"declared: {listing(declared, 40)}"))
    for error in ctx.bundle.validator(schema).iter_errors(config):
        path = list(error.absolute_path)
        if error.validator in ("required", "additionalProperties", "oneOf", "anyOf"):
            continue  # top-level required and unknown keys handled above; stale variants are not trusted
        if error.validator == "enum":
            value = str(error.instance).lower()
            if any(value == str(v).lower() for v in error.validator_value or []):
                continue
            severity = "warning"
        else:
            severity = "error" if error.validator == "type" else "warning"
        dotted = "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in path)
        out.append(make(code, severity, join(pointer, *path), where, f"{label}{dotted}: {error.message[:140]}",
                        str((error.schema or {}).get("description", "") if isinstance(error.schema, dict) else "")[:140]
                        or "fix the value"))
    return out


# --- events -----------------------------------------------------------------------------------


def _events(ctx: Context, events: dict[str, Any], base: str, node: Node | None) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    where = node.path if node else ""
    component = ctx.component(node)
    groups = EVENT_GROUPS if node else ("system",)
    for group, handlers in events.items():
        gpointer = join(base, group)
        if group not in groups:
            if group in ctx.bundle.dom_events:
                hint = f"nest it: events.dom.{group}"
            elif node is None:
                hint = "view events live under events.system (onStartup, onShutdown)"
            else:
                hint = did_you_mean(group, groups)
            out.append(make("E001", "error", gpointer, where, f"unknown event group '{group}'", hint))
            continue
        if not isinstance(handlers, dict):
            out.append(make("E005", "error", gpointer, where, f"events.{group} must be an object",
                            f'use "events": {{"{group}": {{"<eventName>": {{...}}}}}}'))
            continue
        if group == "dom":
            valid = list(ctx.bundle.dom_events)
        elif group == "component":
            valid = list(component.events) if component else []
        else:
            valid = list(SYSTEM_EVENTS)
        for name, handler in handlers.items():
            npointer = join(gpointer, name)
            if (group != "component" or component is not None) and name not in valid:
                out.append(make("E002", "error", npointer, where, f"unknown {group} event '{name}'",
                                _event_hint(ctx, name, group, component),
                                f"{group} events: {listing(valid, 40) or 'none'}"))
            for i, item in enumerate(handler if isinstance(handler, list) else [handler]):
                hpointer = join(npointer, i) if isinstance(handler, list) else npointer
                out += _handler(item, hpointer, where)
    return out


def _event_hint(ctx: Context, name: str, group: str, component: Any) -> str:
    if group == "dom" and component and name in component.events:
        return f"'{name}' is a component event: use events.component.{name}"
    if group == "component" and name in ctx.bundle.dom_events:
        return f"'{name}' is a DOM event: use events.dom.{name}"
    pool = ctx.bundle.dom_events if group == "dom" else (component.events if component else SYSTEM_EVENTS)
    if group == "component" and component and not component.events:
        return f"{component.id} has no component events; DOM events go under events.dom"
    return did_you_mean(name, pool)


def _handler(handler: Any, pointer: str, where: str) -> list[Diagnostic]:
    if not isinstance(handler, dict):
        return [make("E005", "error", pointer, where, "event handler must be an object",
                     '{"type": "script", "scope": "G", "config": {"script": "\\t..."}}')]
    out: list[Diagnostic] = []
    for key in handler:
        if key not in HANDLER_KEYS:
            out.append(make("E005", "warning", join(pointer, key), where, f"unknown handler key '{key}'",
                            did_you_mean(key, HANDLER_KEYS)))
    atype = handler.get("type")
    if atype not in GATEWAY_ACTIONS + CLIENT_ACTIONS:
        alias = ACTION_ALIASES.get(str(atype))
        hint = f"use '{alias}'" if alias else did_you_mean(str(atype), GATEWAY_ACTIONS + CLIENT_ACTIONS)
        out.append(make("E003", "error", join(pointer, "type"), where, f"unknown action type {atype!r}", hint))
        return out
    expected = "G" if atype in GATEWAY_ACTIONS else "C"
    if handler.get("scope") != expected:
        side = "the gateway" if expected == "G" else "the browser"
        out.append(make("E004", "error", join(pointer, "scope"), where,
                        f"'{atype}' action has scope {handler.get('scope')!r}; it runs on {side}",
                        f"set scope to '{expected}'"))
    config = handler.get("config")
    if not isinstance(config, dict):
        out.append(make("E005", "error", join(pointer, "config"), where, f"'{atype}' action has no config",
                        "add config, e.g. {\"script\": \"\\t...\"} for scripts"))
        return out
    cpointer = join(pointer, "config")
    if atype == "script" and not isinstance(config.get("script"), str):
        out.append(make("E005", "error", cpointer, where, "script action needs config.script",
                        'use {"script": "\\tsystem.perspective.print(\'hi\')"}'))
    elif atype == "popup":
        ptype = config.get("type")
        if ptype not in ("open", "close", "toggle"):
            out.append(make("E005", "error", join(cpointer, "type"), where, f"popup config.type is {ptype!r}",
                            did_you_mean(str(ptype), ("open", "close", "toggle"))))
        elif ptype in ("open", "toggle") and not isinstance(config.get("viewPath"), str):
            out.append(make("E005", "error", cpointer, where, f"popup {ptype} needs config.viewPath",
                            'add "viewPath": "Popups/MyPopup" and an "id"'))
    elif atype == "nav" and not any(isinstance(config.get(k), str) for k in ("page", "view", "url")):
        out.append(make("E005", "error", cpointer, where, "nav action needs config.page, config.view or config.url",
                        'use {"page": "/alarms"} to change page, or {"view": "Path/To/View", "params": {}}'))
    return out

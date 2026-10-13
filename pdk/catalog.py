"""Catalog: compact, budgeted answers about components, props, bindings and events, from the exact schemas."""

from __future__ import annotations

import json
from typing import Any

from pdk.bundle import Bundle, Component
from pdk.diagnostics import CHARS_PER_TOKEN, CODES, DEFAULT_BUDGET
from pdk.shape import SchemaRef, describe_type, items_ref, object_shape, resolve
from pdk.suggest import closest
from pdk.validate.config import (
    BINDING_TYPES,
    CLIENT_ACTIONS,
    COMPONENT_PREFIXES,
    EVENT_GROUPS,
    GATEWAY_ACTIONS,
    PARAM_DIRECTIONS,
    PROPCONFIG_KEYS,
    SYSTEM_EVENTS,
    TRANSFORM_TYPES,
)
from pdk.validate.structure import COMPONENT_KEYS, VIEW_KEYS

TOPICS = ("bindings", "transforms", "events", "view", "style", "codes")


def catalog(
    bundle: Bundle,
    *,
    type: str | None = None,
    prop: str | None = None,
    search: str | None = None,
    topic: str | None = None,
    budget: int = DEFAULT_BUDGET,
) -> str:
    if topic:
        lines = _topic(bundle, topic)
    elif search:
        lines = _search(bundle, search)
    elif type:
        component = find_component(bundle, type)
        if component is None:
            match = closest(type, bundle.components, cutoff=0.5)
            hint = f" Did you mean '{match}'?" if match else ""
            return f"Unknown component type '{type}'.{hint} Use pdk_catalog(search='...') to look it up."
        lines = _prop(bundle, component, prop) if prop else _component(bundle, component)
    else:
        lines = _index(bundle)
    return _fit(lines, budget)


def find_component(bundle: Bundle, name: str) -> Component | None:
    """By id (`ia.display.label`), short id (`label`, `display.label`), display name (`Label`), or the start of a
    short id or display name when only one component has it (`numeric entry` -> ia.input.numeric-entry-field)."""
    if name in bundle.components:
        return bundle.components[name]
    lowered = name.lower().strip()
    for component in bundle.components.values():
        if component.id.lower().endswith("." + lowered) or component.name.lower() == lowered:
            return component
    dashed = lowered.replace(" ", "-")
    starts = [c for c in bundle.components.values()
              if c.id.lower().rsplit(".", 1)[-1].startswith(dashed) or c.name.lower().startswith(lowered)]
    return starts[0] if len(starts) == 1 else None


def _fit(lines: list[str], budget: int) -> str:
    limit = int(budget * CHARS_PER_TOKEN)
    out: list[str] = []
    used = 0
    for i, line in enumerate(lines):
        if used + len(line) + 1 > limit - 90:
            out.append(f"... +{len(lines) - i} more lines; narrow with type=/prop=/search=")
            break
        out.append(line)
        used += len(line) + 1
    return "\n".join(out)


def _short(value: Any, width: int = 40) -> str:
    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else json.dumps(value)
    return text if len(text) <= width else text[: width - 3] + "..."


def _describe(bundle: Bundle, name: str, ref: SchemaRef, width: int = 90) -> str:
    resolved = resolve(ref, bundle)
    schema = resolved.schema if resolved and isinstance(resolved.schema, dict) else {}
    text = f"  {name}: {describe_type(ref, bundle)}"
    if "default" in schema and schema["default"] not in ({}, [], None):
        text += f" = {_short(schema['default'])}"
    description = str(schema.get("description") or "").strip().replace("\n", " ")
    if description:
        room = max(20, width - len(text))
        text += " -- " + (description if len(description) <= room else description[: room - 3] + "...")
    return text


def _index(bundle: Bundle) -> list[str]:
    lines = [f"Perspective {bundle.version}: {len(bundle.components)} components. pdk_catalog(type=...) for props, "
             "search=... to find one, topic=bindings|transforms|events|view|style|codes."]
    by_category: dict[str, list[Component]] = {}
    for component in bundle.components.values():
        by_category.setdefault(component.category, []).append(component)
    for category in sorted(by_category):
        names = ", ".join(c.id + (" (deprecated)" if c.deprecated else "")
                          for c in sorted(by_category[category], key=lambda c: c.id))
        lines.append(f"{category}: {names}")
    return lines


def _search(bundle: Bundle, query: str) -> list[str]:
    q = query.lower().strip()
    scored: list[tuple[int, Component]] = []
    for component in bundle.components.values():
        fields = (component.id.lower(), component.name.lower(), component.tooltip.lower())
        labels = [str(v.get("label") or "").lower() for v in component.variants]
        if q in fields[0] or q == fields[1]:
            scored.append((0, component))
        elif q in fields[1] or any(q in label for label in labels):
            scored.append((1, component))
        elif q in fields[2]:
            scored.append((2, component))
    if not scored:
        names = [c.id for c in bundle.components.values()] + [c.name for c in bundle.components.values()]
        match = closest(query, names, cutoff=0.5)
        component = find_component(bundle, match) if match else None
        if component is None:
            return [f"No component matches '{query}'. pdk_catalog() lists them all."]
        scored = [(3, component)]
    lines = [f"{len(scored)} match(es) for '{query}':"]
    for _, c in sorted(scored, key=lambda s: (s[0], s[1].id)):
        lines.append(f"  {c.id} ({c.name}, {c.category}) -- {c.tooltip[:90]}")
    return lines


def _component(bundle: Bundle, component: Component) -> list[str]:
    root = SchemaRef(component.schema, component.schema)
    shape = object_shape(root, bundle)
    props = shape.properties if shape else {}
    header = f"{component.id} ({component.name}, {component.category})"
    if component.deprecated:
        header += " DEPRECATED"
    lines = [header + (f" -- {component.tooltip}" if component.tooltip else ""),
             f"props ({len(props)}){' [closed: unknown props are rejected]' if shape and shape.closed else ''}:"]
    lines += [_describe(bundle, name, ref) for name, ref in sorted(props.items())]
    if component.events:
        lines.append("component events (events.component.<name>):")
        for name, event in sorted(component.events.items()):
            lines.append(f"  {name} -- {str(event.get('description') or '')[:90]}")
    else:
        lines.append("component events: none (DOM events go under events.dom, see topic=events)")
    variants = [v for v in component.variants if v.get("props")]
    if variants:
        lines.append("palette variants (props the Designer sets when dropping one; write them for that look):")
        lines += [f"  {v.get('label') or v.get('id')}: {_short(v['props'], 120)}" for v in variants]
    if component.extension_functions:
        lines.append('extension functions (scripts.extensionFunctions: [{"name", "script", "enabled": true}], '
                     "the script is the body of def <name>(self, <args>)):")
        for name, function in sorted(component.extension_functions.items()):
            args = ", ".join(["self"] + [str(a.get("name")) for a in function.get("arguments") or []
                                         if isinstance(a, dict)])
            text = str(function.get("description") or "")
            text = text if len(text) <= 90 else text[:87] + "..."
            default = str(function.get("defaultImplementation") or "").strip()
            lines.append(f"  {name}({args}) -- {text}" + (f" | default: {_short(default, 60)}" if default else ""))
    if component.child_position_schema:
        position = object_shape(SchemaRef(component.child_position_schema, component.child_position_schema), bundle)
        keys = position.properties if position else {}
        lines.append(f"children position keys ({len(keys)}):" if keys else "children position: free-form object")
        lines += [_describe(bundle, name, ref, 80) for name, ref in sorted(keys.items())]
    lines.append(f"detail: pdk_catalog(type='{component.id}', prop='<name>') e.g. a nested object or array item")
    return lines


def _prop(bundle: Bundle, component: Component, path: str) -> list[str]:
    ref: SchemaRef | None = SchemaRef(component.schema, component.schema)
    label = component.id
    for token in [t for t in path.replace("[]", ".[]").split(".") if t]:
        if ref is None:
            break
        if token == "[]" or token.isdigit():
            ref = items_ref(ref, bundle)
            label += "[]"
            continue
        shape = object_shape(ref, bundle)
        if shape is None or token not in shape.properties:
            options = sorted(shape.properties) if shape else []
            match = closest(token, options)
            hint = f" Did you mean '{match}'?" if match else f" Keys: {', '.join(options[:30])}"
            return [f"{label} has no '{token}'.{hint}"]
        ref = shape.properties[token]
        label += f".{token}"
    if ref is None:
        return [f"{label}: not an array; use the property name without []"]
    resolved = resolve(ref, bundle)
    schema = resolved.schema if resolved and isinstance(resolved.schema, dict) else {}
    lines = [f"{label}: {describe_type(ref, bundle)}"]
    if schema.get("description"):
        lines.append(f"  {schema['description']}")
    if isinstance(schema.get("enum"), list):
        lines.append(f"  values: {', '.join(str(v) for v in schema['enum'])}")
    if "default" in schema:
        lines.append(f"  default: {_short(schema['default'], 300)}")
    shape = object_shape(ref, bundle)
    if shape and shape.is_style:
        lines.append("  style object: any CSS property in camelCase (backgroundColor, borderRadius, ...) plus "
                     "'classes' (space-separated project style classes)")
    elif shape and shape.properties:
        lines.append(f"  keys ({len(shape.properties)}):")
        lines += ["  " + _describe(bundle, name, sub, 100) for name, sub in sorted(shape.properties.items())]
    items = items_ref(ref, bundle)
    if items is not None:
        item_shape = object_shape(items, bundle)
        lines.append(f"  items: {describe_type(items, bundle)}")
        if item_shape and item_shape.properties:
            lines += ["  " + _describe(bundle, name, sub, 100) for name, sub in sorted(item_shape.properties.items())]
            lines.append(f"  deeper: prop='{path.removesuffix('[]')}[].<key>'")
    return lines


def _schema_keys(bundle: Bundle, name: str) -> str:
    schema = bundle.schemas.get(f"schemas/{name}") or {}
    required = set(schema.get("required") or [])
    parts = []
    for key, sub in (schema.get("properties") or {}).items():
        label = describe_type(SchemaRef(sub, schema), bundle)
        parts.append(f"{key}{'*' if key in required else ''}: {label}")
    return ", ".join(parts)


_BINDING_EXAMPLES = {
    "tag": '{"type": "tag", "config": {"tagPath": "[default]Pump/Speed", "mode": "direct", "bidirectional": false}}',
    "expr": '{"type": "expr", "config": {"expression": "{view.params.speed} * 2"}}',
    "property": '{"type": "property", "config": {"path": "../Slider.props.value", "bidirectional": false}}',
    "query": '{"type": "query", "config": {"queryPath": "Pumps/List", "parameters": {"area": "{view.params.area}"}}}',
    "expr-struct": '{"type": "expr-struct", "config": {"struct": {"a": "{view.params.x}", "b": "now(1000)"}}}',
}


def _topic(bundle: Bundle, topic: str) -> list[str]:
    topic = topic.lower().strip()
    if topic == "bindings":
        lines = ['Bindings live in propConfig: "propConfig": {"props.text": {"binding": {...}}}.',
                 f"binding keys: type, config, transforms, overlayOptOut. Types: {', '.join(BINDING_TYPES)} "
                 "(it is 'expr', never 'expression'). Config keys (* required):"]
        for btype in BINDING_TYPES:
            lines.append(f"  {btype}: {_schema_keys(bundle, f'binding-{btype}.json')}")
        lines.append("examples:")
        lines += [f"  {example}" for example in _BINDING_EXAMPLES.values()]
        lines.append("paths in property bindings/expressions: this.props.x, parent.custom.y, view.params.z, "
                     "session.props.w, page.props.v, /root/Panel/Label.props.text, ../Sibling.props.value")
        lines.append("transforms: topic=transforms")
        return lines
    if topic == "transforms":
        return [f"binding.transforms: array, applied in order. Types: {', '.join(TRANSFORM_TYPES)} "
                "(it is 'expression' here, but 'expr' for bindings). {value} is the incoming value.",
                '  expression: {"type": "expression", "expression": "{value} > 50"}',
                '  map: {"type": "map", "inputType": "scalar", "outputType": "color", "mappings": '
                '[{"input": 1, "output": "#00FF00"}], "fallback": "#808080"}',
                f"  format: {_schema_keys(bundle, 'transform-format.json') or 'formatType*, formatValue*'}",
                '  script: {"type": "script", "code": "\\treturn value * 2"}']
    if topic == "events":
        by_category: dict[str, list[str]] = {}
        for name, category in bundle.dom_events.items():
            by_category.setdefault(category, []).append(name)
        lines = [f"events groups: {', '.join(EVENT_GROUPS)}; "
                 f"views only have events.system ({', '.join(SYSTEM_EVENTS)}).",
                 "handler: {\"type\": <action>, \"scope\": \"G\"|\"C\", \"config\": {...}} (or a list of them)",
                 f"  scope G (gateway): {', '.join(GATEWAY_ACTIONS)}",
                 f"  scope C (browser): {', '.join(CLIENT_ACTIONS)}",
                 '  script: {"type": "script", "scope": "G", "config": {"script": "\\tsystem.perspective.print(1)"}}',
                 '  nav: {"type": "nav", "scope": "C", "config": {"page": "/alarms"}} '
                 'or {"view": "Path", "params": {}}',
                 '  popup: {"type": "popup", "scope": "C", "config": {"type": "open", "id": "p1", "viewPath": '
                 '"Popups/Detail", "viewParams": {}, "modal": false}}',
                 "events.dom names:"]
        lines += [f"  {category}: {', '.join(names)}" for category, names in sorted(by_category.items())]
        lines.append("events.component names depend on the component: pdk_catalog(type=...)")
        return lines
    if topic == "view":
        return [f"view.json keys: {', '.join(VIEW_KEYS)} (root required).",
                f"component keys: {', '.join(COMPONENT_KEYS)} "
                "(type and meta.name required; children only on containers).",
                f"propConfig keys: '<section>.<path>' with section {', '.join(COMPONENT_PREFIXES)} on components and "
                "params, custom, props on the view; entry keys: " + ", ".join(PROPCONFIG_KEYS) + ".",
                f"view params: declare defaults in params and direction in propConfig['params.<name>'].paramDirection "
                f"({', '.join(PARAM_DIRECTIONS)}).",
                "position: keys of the PARENT container (flex: basis/grow/shrink; coord: x/y/width/height).",
                "The Designer omits values equal to defaults; you may too.",
                'minimal: {"custom": {}, "params": {}, "props": {"defaultSize": {"width": 800, "height": 600}}, '
                '"root": {"type": "ia.container.flex", "meta": {"name": "root"}, "props": {"direction": "column"}, '
                '"children": [{"type": "ia.display.label", "meta": {"name": "Title"}, "position": {"basis": "32px"}, '
                '"props": {"text": "Hello"}}]}}']
    if topic == "style":
        return ["style objects (props.style, textStyle, ...): CSS properties in camelCase, values as CSS strings "
                "(\"12px\", \"#2F5D7C\", \"var(--neutral-20)\").",
                "  'classes': space-separated project style classes, e.g. \"hmi-typography-heading hmi-spacing-md\".",
                "  kebab-case keys (background-color) are wrong; use backgroundColor."]
    if topic == "codes":
        return ["diagnostic codes (S structure, P props/meta/position, B propConfig/bindings, E events, "
                "R references, HMI lint):"] + [f"  {code}: {slug}" for code, slug in CODES.items()]
    return [f"Unknown topic '{topic}'. Topics: {', '.join(TOPICS)}."]

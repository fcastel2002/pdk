"""P family: props, meta and position against the exact schemas, plus unknown-key detection."""

from __future__ import annotations

import re
from collections.abc import Callable
from functools import cache
from typing import Any

from jsonschema.exceptions import ValidationError, best_match

from pdk import PACKAGE_DIR
from pdk.bundle import Bundle, is_typed_value
from pdk.diagnostics import Diagnostic, Severity, join, make
from pdk.shape import STYLE_SCHEMAS, SchemaRef, Shape, describe_type, items_ref, object_shape
from pdk.suggest import camel_case, closest, did_you_mean, is_kebab, listing
from pdk.validate.context import Context

_REQUIRED = re.compile(r"'(.+)' is a required property")
META_EXTRA = frozenset({"hasDelegate"})  # written by the Designer, absent from meta-schema.json
_KINDS = {
    # kind: (label prefix, unknown-key code, noun)
    "prop": ("props", "P001", "prop"),
    "view": ("props", "P001", "view prop"),
    "meta": ("meta", "P007", "meta key"),
    "position": ("position", "P010", "position key"),
    "binding": ("config", "B004", "binding config key"),
    "transform": ("transform", "B006", "transform key"),
}
_SINGLE_CODE = {"binding": "B004", "transform": "B006"}  # kinds whose problems all share one code


@cache
def _css_file() -> frozenset[str]:
    text = (PACKAGE_DIR / "data" / "css-properties.txt").read_text(encoding="utf-8")
    return frozenset(line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#"))


def css_properties(bundle: Bundle) -> frozenset[str]:
    declared: set[str] = {"classes"}
    for name in STYLE_SCHEMAS:
        declared.update((bundle.schemas.get(name) or {}).get("properties", {}))
    return _css_file() | declared


def is_css_key(key: str, bundle: Bundle) -> bool:
    return key in css_properties(bundle) or key.startswith("--")


def style_hint(key: str, bundle: Bundle) -> str:
    if is_kebab(key):
        return f"style keys are camelCase: use '{camel_case(key)}'"
    match = closest(key, css_properties(bundle), cutoff=0.75)
    return f"did you mean '{match}'?" if match else "not a known CSS property; check the spelling"


def check(ctx: Context) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    bundle = ctx.bundle
    view_props = ctx.view.get("props")
    if isinstance(view_props, dict):
        schema = bundle.schema("view-props-schema.json")
        out += check_instance(ctx, view_props, SchemaRef(schema, schema), "/props", "", "view", "view")
    meta_schema = bundle.schema("meta-schema.json")
    for node in ctx.nodes:
        component = ctx.component(node)
        c = node.component
        if component and isinstance(c.get("props"), dict):
            out += check_instance(ctx, c["props"], SchemaRef(component.schema, component.schema),
                                  join(node.pointer, "props"), node.path, "prop", component.id,
                                  lenient=component.id.startswith("ia.shapes."))
        if isinstance(c.get("meta"), dict):
            out += check_instance(ctx, c["meta"], SchemaRef(meta_schema, meta_schema),
                                  join(node.pointer, "meta"), node.path, "meta", "meta")
        parent = ctx.component(node.parent)
        if parent and parent.child_position_schema and isinstance(c.get("position"), dict):
            schema = parent.child_position_schema
            out += check_instance(ctx, c["position"], SchemaRef(schema, schema),
                                  join(node.pointer, "position"), node.path, "position", parent.id)
    return out


def check_instance(
    ctx: Context, instance: Any, ref: SchemaRef, base: str, where: str, kind: str, owner: str,
    lenient: bool = False,
) -> list[Diagnostic]:
    out = _schema_errors(ctx.bundle, instance, ref, base, where, kind, owner)
    prefix, code, noun = _KINDS[kind]

    def unknown(pointer: str, key: str, shape: Shape, depth: int) -> None:
        if (kind == "meta" and key in META_EXTRA) or (lenient and is_css_key(key, ctx.bundle)):
            return  # Designer meta extras; SVG/CSS presentation attributes on shapes
        severity: Severity
        if depth == 0:
            severity = "warning" if lenient else "error"
        elif shape.closed:
            severity = "error"
        elif not lenient and closest(key, shape.properties, cutoff=0.8):
            severity = "warning"  # nested objects often carry free keys (data rows); flag only likely typos
        else:
            return
        container = "children of " + owner if kind == "position" else owner
        out.append(make(code, severity, pointer, where, f"unknown {noun} '{key}' for {container}",
                        did_you_mean(key, shape.properties), f"declared: {listing(shape.properties, 40)}"))

    def style(pointer: str, key: str) -> None:
        out.append(make("P006", "warning", pointer, where, f"unknown style property '{key}'",
                        style_hint(key, ctx.bundle)))

    walk_unknown(ctx.bundle, instance, ref, base, 0, unknown, style)
    single = _SINGLE_CODE.get(kind)
    if single:
        for d in out:
            d["code"] = single
    return out


def walk_unknown(
    bundle: Bundle,
    instance: Any,
    ref: SchemaRef,
    pointer: str,
    depth: int,
    unknown: Callable[[str, str, Shape, int], None],
    style: Callable[[str, str], None],
) -> None:
    if isinstance(instance, dict):
        if is_typed_value(instance):
            return
        shape = object_shape(ref, bundle)
        if shape is None:
            return
        if shape.is_style:
            for key in instance:
                if not is_css_key(key, bundle):
                    style(join(pointer, key), key)
            return
        for key, value in instance.items():
            if key in shape.properties:
                walk_unknown(bundle, value, shape.properties[key], join(pointer, key), depth + 1, unknown, style)
            elif shape.extra is not None:
                walk_unknown(bundle, value, shape.extra, join(pointer, key), depth + 1, unknown, style)
            elif not shape.open:
                unknown(join(pointer, key), key, shape, depth)
    elif isinstance(instance, list):
        items = items_ref(ref, bundle)
        if items is not None:
            for i, value in enumerate(instance):
                walk_unknown(bundle, value, items, join(pointer, i), depth + 1, unknown, style)


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int | float):
        return "number"
    if isinstance(value, str):
        return "string"
    return "array" if isinstance(value, list) else "object"


def _label(prefix: str, path: list[Any]) -> str:
    text = prefix
    for token in path:
        text += f"[{token}]" if isinstance(token, int) else f".{token}"
    return text


def _schema_errors(
    bundle: Bundle, instance: Any, ref: SchemaRef, base: str, where: str, kind: str, owner: str
) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    prefix = _KINDS[kind][0]
    for error in bundle.validator(ref.schema).iter_errors(instance):
        if error.validator == "additionalProperties":
            continue  # reported with suggestions by walk_unknown
        expected_from = error
        if error.validator in ("oneOf", "anyOf") and error.context:
            error = best_match(error.context)
            if error.validator == "additionalProperties":
                continue
        if error.validator == "required" and kind in ("prop", "view"):
            continue  # Perspective fills missing props from component defaults; `required` is Designer-only
        out.append(_convert(bundle, error, expected_from, base, where, prefix, owner, kind))
    return out


def _convert(
    bundle: Bundle, error: ValidationError, outer: ValidationError, base: str, where: str, prefix: str,
    owner: str, kind: str,
) -> Diagnostic:
    path = list(error.absolute_path)
    pointer = join(base, *path)
    label = _label(prefix, path)
    schema = error.schema if isinstance(error.schema, dict) else {}
    description = str(schema.get("description") or "")[:160]
    if error.validator == "type":
        outer_schema = outer.schema if isinstance(outer.schema, dict) else {}
        expected = describe_type(SchemaRef(outer_schema, outer_schema), bundle) if outer is not error else (
            "|".join(schema["type"]) if isinstance(schema.get("type"), list) else str(schema.get("type")))
        hint = f"use a {expected} value"
        if "default" in schema:
            hint += f" (default: {_short(schema['default'])})"
        actual = _json_type(error.instance)
        # The Designer stores numbers typed into string fields (e.g. flex basis 2000); Perspective coerces them.
        severity: Severity = "warning" if actual == "number" and expected == "string" else "error"
        if severity == "warning":
            hint = f"write it as a string, e.g. '{error.instance}px' for lengths"
        return make("P002", severity, pointer, where, f"{label} is {actual}, expected {expected}",
                    hint, description or None)
    if error.validator == "enum":
        allowed = [str(v) for v in error.validator_value or []]
        hint = did_you_mean(str(error.instance), allowed) if isinstance(error.instance, str) else \
            f"valid: {listing(allowed)}"
        return make("P003", "error", pointer, where, f"{label}: {_short(error.instance)} is not allowed", hint,
                    f"allowed: {', '.join(allowed)}")
    if error.validator == "required":
        match = _REQUIRED.search(error.message)
        missing = match.group(1) if match else "?"
        return make("P004", "error", join(pointer, missing), where, f"{label} requires '{missing}'",
                    f"add '{missing}'", description or None)
    if kind == "meta" and error.validator == "pattern" and path == ["name"]:
        return make("P008", "error", pointer, where, f"invalid component name {_short(error.instance)}",
                    "use letters, digits, spaces, _ ( ) -; start with a letter or _")
    message = error.message if len(error.message) <= 160 else error.message[:157] + "..."
    fallback = f"see pdk_catalog(type='{owner}')" if kind == "prop" else "fix the value"
    return make("P005", "error", pointer, where, f"{label}: {message}", description or fallback)


def _short(value: Any) -> str:
    text = repr(value)
    return text if len(text) <= 60 else text[:57] + "..."

"""Navigation over Perspective JSON Schemas: `$ref` resolution, object shapes and property paths.

Perspective schemas rarely close their objects (`additionalProperties: false`), so jsonschema alone
never flags an invented prop. The helpers here compute which keys a schema declares so the
validator can report unknown keys with suggestions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pdk.bundle import URN_PREFIX

if TYPE_CHECKING:
    from pdk.bundle import Bundle

STYLE_SCHEMAS = ("schemas/style-properties.schema.json", "schemas/css-props.schema.json")
_COMBINATORS = ("allOf", "anyOf", "oneOf")
_PATH_TOKEN = re.compile(r"[^.\[\]]+|\[(\d+)\]")


@dataclass(frozen=True)
class SchemaRef:
    """A schema node plus the document its local `#/...` references resolve against."""

    schema: Any
    root: Any


@dataclass
class Shape:
    properties: dict[str, SchemaRef] = field(default_factory=dict)
    extra: SchemaRef | None = None  # additionalProperties as a schema
    open: bool = False  # unknown keys are allowed
    closed: bool = False  # explicit additionalProperties: false
    is_style: bool = False

    def describe(self) -> str:
        return ", ".join(sorted(self.properties))


def resolve(ref: SchemaRef, bundle: Bundle) -> SchemaRef | None:
    """Follow `$ref` chains (local and `urn:ignition-schema:`). None when unresolvable."""
    schema, root = ref.schema, ref.root
    for _ in range(32):
        target = schema.get("$ref") if isinstance(schema, dict) else None
        if not isinstance(target, str):
            return SchemaRef(schema, root)
        document, _, fragment = target.partition("#")
        if document:
            if not document.startswith(URN_PREFIX):
                return None
            root = bundle.schemas.get(document[len(URN_PREFIX):])
            if root is None:
                return None
        node: Any = root
        for token in fragment.split("/")[1:] if fragment else []:
            token = token.replace("~1", "/").replace("~0", "~")
            if isinstance(node, dict) and token in node:
                node = node[token]
            elif isinstance(node, list) and token.isdigit() and int(token) < len(node):
                node = node[int(token)]
            else:
                return None
        schema = node
    return None


def _admits(schema: dict[str, Any], json_type: str) -> bool:
    declared = schema.get("type")
    if declared is None:
        return True
    return json_type in declared if isinstance(declared, list) else declared == json_type


def is_style(ref: SchemaRef, bundle: Bundle) -> bool:
    resolved = resolve(ref, bundle)
    return resolved is not None and any(resolved.schema is bundle.schemas.get(n) for n in STYLE_SCHEMAS)


def object_shape(ref: SchemaRef, bundle: Bundle, _depth: int = 0) -> Shape | None:
    """Keys an object schema declares, merging allOf/anyOf/oneOf branches. None: cannot tell."""
    resolved = resolve(ref, bundle)
    if resolved is None or not isinstance(resolved.schema, dict) or _depth > 8:
        return None
    schema = resolved.schema
    if any(schema is bundle.schemas.get(n) for n in STYLE_SCHEMAS):
        return Shape(open=True, is_style=True)
    if not _admits(schema, "object"):
        return None

    shape = Shape()
    declared = isinstance(schema.get("properties"), dict)
    for key, sub in (schema.get("properties") or {}).items():
        if isinstance(sub, dict):
            shape.properties[key] = SchemaRef(sub, resolved.root)
    additional = schema.get("additionalProperties")
    if additional is False:
        shape.closed = True
    elif isinstance(additional, dict):
        shape.extra = SchemaRef(additional, resolved.root)
        shape.open = True
    elif additional is True or "patternProperties" in schema:
        shape.open = True

    for combinator in _COMBINATORS:
        for branch in schema.get(combinator) or []:
            if not isinstance(branch, dict):
                continue
            branch_resolved = resolve(SchemaRef(branch, resolved.root), bundle)
            if branch_resolved is None or not isinstance(branch_resolved.schema, dict):
                shape.open = True
                continue
            if not _admits(branch_resolved.schema, "object"):
                continue
            if not any(k in branch_resolved.schema for k in
                       ("properties", "additionalProperties", "patternProperties", *_COMBINATORS)):
                continue  # constraint-only branch, e.g. {"required": ["path"]}
            branch_shape = object_shape(branch_resolved, bundle, _depth + 1)
            if branch_shape is None:
                shape.open = True
                continue
            declared = declared or bool(branch_shape.properties)
            for key, sub in branch_shape.properties.items():
                shape.properties.setdefault(key, sub)
            if branch_shape.is_style:
                shape.is_style = True
            if branch_shape.open:
                shape.open = True
            if branch_shape.extra and not shape.extra:
                shape.extra = branch_shape.extra
    if not declared:
        shape.open = True
    if shape.open:
        shape.closed = False
    return shape


def items_ref(ref: SchemaRef, bundle: Bundle) -> SchemaRef | None:
    resolved = resolve(ref, bundle)
    if resolved is None or not isinstance(resolved.schema, dict):
        return None
    items = resolved.schema.get("items")
    if isinstance(items, dict):
        return SchemaRef(items, resolved.root)
    for combinator in _COMBINATORS:
        for branch in resolved.schema.get(combinator) or []:
            if isinstance(branch, dict):
                found = items_ref(SchemaRef(branch, resolved.root), bundle)
                if found:
                    return found
    return None


def parse_path(path: str) -> list[str | int]:
    """`props.columns[0].field` -> ['props', 'columns', 0, 'field']."""
    return [int(m.group(1)) if m.group(1) is not None else m.group(0) for m in _PATH_TOKEN.finditer(path)]


@dataclass(frozen=True)
class PathMiss:
    """First segment of a property path that the schema does not declare."""

    index: int
    segment: str
    allowed: tuple[str, ...]
    closed: bool


def check_path(ref: SchemaRef, segments: list[str | int], bundle: Bundle) -> PathMiss | None:
    """Walk `segments` through the schema; report the first undeclared key in a declared object."""
    current: SchemaRef | None = ref
    for index, segment in enumerate(segments):
        if current is None:
            return None
        if isinstance(segment, int) or segment.isdigit():
            current = items_ref(current, bundle)
            continue
        shape = object_shape(current, bundle)
        if shape is None or shape.is_style:
            return None
        if segment in shape.properties:
            current = shape.properties[segment]
        elif shape.extra is not None:
            current = shape.extra
        elif shape.open:
            return None
        else:
            return PathMiss(index, segment, tuple(sorted(shape.properties)), shape.closed)
    return None


def describe_type(ref: SchemaRef, bundle: Bundle) -> str:
    """Compact type label: `string`, `enum[a|b]`, `number|string`, `style`, `object{a,b}`, `array`."""
    resolved = resolve(ref, bundle)
    if resolved is None or not isinstance(resolved.schema, dict):
        return "?"
    schema = resolved.schema
    if any(schema is bundle.schemas.get(n) for n in STYLE_SCHEMAS):
        return "style"
    if isinstance(schema.get("enum"), list):
        values = [str(v) for v in schema["enum"]]
        shown = "|".join(values[:8]) + ("|…" if len(values) > 8 else "")
        return f"enum[{shown}]"
    declared = schema.get("type")
    if isinstance(declared, list):
        return "|".join(str(t) for t in declared)
    if declared:
        return str(declared)
    for combinator in ("oneOf", "anyOf"):
        if isinstance(schema.get(combinator), list):
            labels = {describe_type(SchemaRef(b, resolved.root), bundle) for b in schema[combinator]}
            return "|".join(sorted(labels))
    return "any"

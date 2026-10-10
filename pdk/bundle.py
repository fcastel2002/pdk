"""Schema bundle: extraction from the installed Perspective module and loading for validation.

A bundle is a verbatim copy of the JSON resources Perspective itself ships (component
descriptors, `schemas/`, the Designer's DOM event list) plus a manifest. It lives under
`schemas/<perspective-version>/` and is not versioned: regenerate it with `pdk schemas extract`.
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import re
import shutil
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft4Validator, validators
from jsonschema.protocols import Validator
from referencing import Registry
from referencing.jsonschema import DRAFT4

from pdk import REPO_ROOT

URN_PREFIX = "urn:ignition-schema:"
BUNDLE_FORMAT = "pdk-schema-bundle-v1"
_COMPONENT_FILE = re.compile(r"^[^/]+\.components?\.json$")
_JAR = "perspective-{kind}-{version}.jar"
_DEFAULT_IGNITION_HOMES = (
    Path(r"C:\Program Files\Inductive Automation\Ignition"),
    Path("/usr/local/bin/ignition"),
    Path("/usr/local/ignition"),
)


class BundleError(RuntimeError):
    """The schema bundle is missing, incomplete or cannot be extracted."""


# --- extraction -------------------------------------------------------------------------------


def find_ignition_home() -> Path | None:
    env = os.environ.get("IGNITION_HOME")
    for candidate in [Path(env)] if env else _DEFAULT_IGNITION_HOMES:
        if (candidate / "user-lib" / "modules").is_dir():
            return candidate
    return None


def find_perspective_modl(ignition_home: Path) -> Path:
    matches = sorted((ignition_home / "user-lib" / "modules").glob("Perspective*.modl"))
    if not matches:
        raise BundleError(f"No Perspective .modl under {ignition_home / 'user-lib' / 'modules'}")
    return matches[0]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _module_version(module_xml: str) -> tuple[str, str]:
    match = re.search(r"<version>\s*([0-9]+\.[0-9]+\.[0-9]+)(?:\.([0-9]+))?\s*</version>", module_xml)
    if not match:
        raise BundleError("Cannot read the module version from module.xml")
    return match.group(1), match.group(2) or ""


def _ignition_version(ignition_home: Path | None) -> str | None:
    info = ignition_home / "lib" / "install-info.txt" if ignition_home else None
    if info and info.is_file():
        for line in info.read_text(encoding="utf-8", errors="replace").splitlines():
            key, _, value = line.partition("=")
            if key.strip() == "gateway.version":
                return value.strip()
    return None


def extract_bundle(modl_path: Path, out_root: Path, ignition_home: Path | None = None) -> Path:
    """Copy Perspective's schema resources from `modl_path` into `out_root/<version>/`."""
    modl_bytes = modl_path.read_bytes()
    files: dict[str, bytes] = {}
    with zipfile.ZipFile(io.BytesIO(modl_bytes)) as modl:
        version, build = _module_version(modl.read("module.xml").decode("utf-8"))
        names = set(modl.namelist())
        common_jar = _JAR.format(kind="common", version=version)
        designer_jar = _JAR.format(kind="designer", version=version)
        if common_jar not in names:
            raise BundleError(f"{modl_path.name} has no {common_jar}")
        with zipfile.ZipFile(io.BytesIO(modl.read(common_jar))) as common:
            for name in common.namelist():
                if _COMPONENT_FILE.match(name):
                    files[f"components/{name}"] = common.read(name)
                elif name.startswith("schemas/") and name.endswith(".json"):
                    files[name] = common.read(name)
        if designer_jar in names:
            with zipfile.ZipFile(io.BytesIO(modl.read(designer_jar))) as designer:
                for name in designer.namelist():
                    if name.endswith("/dom_events.json") or name == "dom_events.json":
                        files["designer/dom_events.json"] = designer.read(name)
    if not any(n.startswith("components/") for n in files):
        raise BundleError(f"{common_jar} contains no component descriptors")

    manifest = {
        "format": BUNDLE_FORMAT,
        "perspectiveVersion": version,
        "perspectiveBuild": build,
        "ignitionVersion": _ignition_version(ignition_home),
        "source": {"modl": str(modl_path), "sha256": _sha256(modl_bytes)},
        "extractedAt": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "files": {name: _sha256(data) for name, data in sorted(files.items())},
    }
    target = out_root / version
    staging = out_root / f".{version}.partial"
    shutil.rmtree(staging, ignore_errors=True)
    for name, data in files.items():
        path = staging / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (staging / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    shutil.rmtree(target, ignore_errors=True)
    staging.rename(target)
    load_bundle.cache_clear()
    return target


# --- loading ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Component:
    id: str
    name: str
    category: str
    tooltip: str
    schema: dict[str, Any]
    child_position_schema: dict[str, Any] | None
    events: dict[str, dict[str, Any]]
    deprecated: bool
    default_meta_name: str | None
    library: str

    @property
    def is_container(self) -> bool:
        return self.child_position_schema is not None


_STANDARD_TYPES = {"array", "boolean", "integer", "null", "number", "object", "string", "any"}
def is_typed_value(value: Any, tag: str | None = None) -> bool:
    """Ignition's typed JSON encoding, e.g. a Date: {"$": ["ts", 0, 1757428595830], "$ts": 1757428589273}."""
    marker = value.get("$") if isinstance(value, dict) else None
    return isinstance(marker, list) and bool(marker) and (tag is None or marker[0] == tag)


_PERSPECTIVE_TYPES = {
    # Non-standard type names used by Perspective schemas, as they serialize in view.json.
    "date": lambda _, v: (isinstance(v, int | float | str) and not isinstance(v, bool)) or is_typed_value(v, "ts"),
    "dataset": lambda _, v: isinstance(v, dict | list),
    "none": lambda _, v: v is None,
    "Number": lambda _, v: isinstance(v, int | float) and not isinstance(v, bool),
}


def _validator_class(type_names: set[str]) -> type[Validator]:
    checkers = dict(_PERSPECTIVE_TYPES)
    for name in type_names - _STANDARD_TYPES - set(checkers):
        checkers[name] = lambda _, v: True  # unknown vendor type: accept rather than crash
    return validators.extend(Draft4Validator, type_checker=Draft4Validator.TYPE_CHECKER.redefine_many(checkers))


def _type_names(node: Any, names: set[str], in_properties: bool = False) -> None:
    if isinstance(node, dict):
        declared = node.get("type")
        if not in_properties:
            if isinstance(declared, str):
                names.add(declared)
            elif isinstance(declared, list):
                names.update(t for t in declared if isinstance(t, str))
        for key, value in node.items():
            if key not in ("default", "example", "examples", "suggestions"):
                _type_names(value, names, key == "properties")
    elif isinstance(node, list):
        for value in node:
            _type_names(value, names)


@dataclass
class Bundle:
    version: str
    root: Path
    manifest: dict[str, Any]
    components: dict[str, Component]
    schemas: dict[str, dict[str, Any]]  # "schemas/<file>" -> schema
    dom_events: dict[str, str]  # event name -> category
    registry: Registry
    validator_class: type[Validator] = Draft4Validator
    _validators: dict[int, Validator] = field(default_factory=dict, repr=False)

    def schema(self, name: str) -> dict[str, Any]:
        """A schema by file name, e.g. `meta-schema.json`."""
        return self.schemas[f"schemas/{name}"]

    def validator(self, schema: dict[str, Any]) -> Validator:
        key = id(schema)
        validator = self._validators.get(key)
        if validator is None:
            validator = self.validator_class(schema, registry=self.registry)
            self._validators[key] = validator
        return validator


def schemas_root() -> Path:
    return Path(os.environ.get("PDK_SCHEMAS_DIR") or REPO_ROOT / "schemas")


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(p) for p in version.split(".") if p.isdigit())


def available_versions(root: Path | None = None) -> list[str]:
    root = root or schemas_root()
    if not root.is_dir():
        return []
    versions = [p.name for p in root.iterdir() if (p / "manifest.json").is_file()]
    return sorted(versions, key=_version_key)


def _relax_required(node: Any) -> None:
    """Drop `required` keys that have a default: the Designer omits default values on save."""
    if isinstance(node, dict):
        required, props = node.get("required"), node.get("properties")
        if isinstance(required, list) and isinstance(props, dict):
            defaults = node.get("default") if isinstance(node.get("default"), dict) else {}
            node["required"] = [
                key for key in required
                if not (isinstance(props.get(key), dict) and "default" in props[key]) and key not in defaults
            ]
        for value in node.values():
            _relax_required(value)
    elif isinstance(node, list):
        for value in node:
            _relax_required(value)


def _descriptor_list(document: Any) -> tuple[str, list[dict[str, Any]]]:
    if isinstance(document, dict) and isinstance(document.get("components"), list):
        return str(document.get("libraryName", "")), document["components"]
    if isinstance(document, dict) and "id" in document:
        return "", [document]
    return "", []


@cache
def load_bundle(version: str | None = None, root: Path | None = None) -> Bundle:
    root = root or schemas_root()
    version = version or os.environ.get("PDK_PERSPECTIVE_VERSION")
    versions = available_versions(root)
    if not versions:
        raise BundleError(f"No schema bundle under {root}. Run `uv run pdk schemas extract`.")
    if version is None:
        version = versions[-1]
    elif version not in versions:
        raise BundleError(f"No bundle for Perspective {version} under {root} (have: {', '.join(versions)})")
    base = root / version
    manifest = json.loads((base / "manifest.json").read_text(encoding="utf-8"))

    schemas: dict[str, dict[str, Any]] = {}
    for path in sorted((base / "schemas").glob("*.json")):
        schema = json.loads(path.read_text(encoding="utf-8"))
        if not path.name.startswith(("binding-", "transform-")):  # binding configs are always written in full
            _relax_required(schema)
        schemas[f"schemas/{path.name}"] = schema

    components: dict[str, Component] = {}
    for path in sorted((base / "components").glob("*.json")):
        library, descriptors = _descriptor_list(json.loads(path.read_text(encoding="utf-8")))
        for raw in descriptors:
            descriptor = copy.deepcopy(raw)
            _relax_required(descriptor)
            palette = descriptor.get("palette") or {}
            variants = palette.get("variants") or [{}]
            components[descriptor["id"]] = Component(
                id=descriptor["id"],
                name=descriptor.get("name") or variants[0].get("label") or descriptor["id"],
                category=palette.get("category", "other"),
                tooltip=variants[0].get("tooltip", ""),
                schema=descriptor.get("schema") or {"type": "object"},
                child_position_schema=descriptor.get("childPositionSchema"),
                events={e["name"]: e for e in descriptor.get("events", []) if isinstance(e, dict) and "name" in e},
                deprecated=bool(descriptor.get("deprecated", False)),
                default_meta_name=descriptor.get("defaultMetaName"),
                library=library,
            )

    dom_events: dict[str, str] = {}
    dom_file = base / "designer" / "dom_events.json"
    if dom_file.is_file():
        for category in json.loads(dom_file.read_text(encoding="utf-8")).get("categories", []):
            for event in category.get("events", []):
                name = event.get("name") if isinstance(event, dict) else event
                if isinstance(name, str):
                    dom_events[name] = category.get("name", "")

    registry: Registry = Registry().with_resources(
        (URN_PREFIX + name, DRAFT4.create_resource(schema)) for name, schema in schemas.items()
    )
    type_names: set[str] = set()
    for schema in schemas.values():
        _type_names(schema, type_names)
    for component in components.values():
        _type_names(component.schema, type_names)
        _type_names(component.child_position_schema, type_names)
    return Bundle(version, base, manifest, components, schemas, dom_events, registry, _validator_class(type_names))

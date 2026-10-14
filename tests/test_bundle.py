from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from pdk.bundle import (
    Bundle,
    Functions,
    available_versions,
    extract_bundle,
    find_ignition_home,
    find_perspective_modl,
    load_bundle,
    load_functions,
    save_functions,
)
from pdk.cli import main
from pdk.shape import SchemaRef, resolve


def test_bundle_contents(bundle) -> None:
    if bundle.version != "3.3.9":
        pytest.skip("counts are pinned for Perspective 3.3.9")
    assert len(bundle.components) == 81
    assert len(bundle.schemas) == 25
    assert len(bundle.dom_events) == 32
    assert bundle.components["ia.container.flex"].is_container
    assert not bundle.components["ia.display.label"].is_container


def test_every_ref_resolves(bundle) -> None:
    refs = 0

    def walk(node, root) -> None:
        nonlocal refs
        if isinstance(node, dict):
            if isinstance(node.get("$ref"), str):
                refs += 1
                assert resolve(SchemaRef(node, root), bundle) is not None, node["$ref"]
            for value in node.values():
                walk(value, root)
        elif isinstance(node, list):
            for value in node:
                walk(value, root)

    for component in bundle.components.values():
        walk(component.schema, component.schema)
        walk(component.child_position_schema, component.child_position_schema)
    for schema in bundle.schemas.values():
        walk(schema, schema)
    assert refs > 500


def test_required_with_defaults_is_relaxed_but_bindings_are_not(bundle) -> None:
    coord = bundle.components["ia.container.coord"].child_position_schema
    assert "x" not in coord.get("required", [])
    assert bundle.schema("binding-tag.json")["required"] == ["tagPath"]


def test_extract_round_trip(bundle: Bundle, tmp_path: Path) -> None:
    home = find_ignition_home()
    if home is None:
        pytest.skip("no local Ignition install")
    target = extract_bundle(find_perspective_modl(home), tmp_path, home)
    manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["format"] == "pdk-schema-bundle-v1"
    assert all((target / name).is_file() for name in manifest["files"])
    copy = load_bundle(target.name, tmp_path)
    assert len(copy.components) == len(bundle.components)
    load_bundle.cache_clear()


def test_a_leftover_staging_folder_is_not_a_version(tmp_path: Path) -> None:
    for name in ("3.3.8", ".3.3.9.partial"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "manifest.json").write_text("{}", encoding="utf-8")
    assert available_versions(tmp_path) == ["3.3.8"]


def test_load_functions_prefers_the_camel_case_name_in_either_order(tmp_path: Path) -> None:
    for names in (["toString", "tostring"], ["tostring", "toString"]):
        save_functions(tmp_path, {"expression": [{"name": name} for name in names], "script": []}, "8.3.2")
        loaded = load_functions(tmp_path)
        assert loaded is not None and loaded.expression == {"tostring": "toString"}


def test_load_functions_without_a_usable_file_is_none(tmp_path: Path) -> None:
    assert load_functions(tmp_path) is None
    (tmp_path / "functions.json").write_text("{not json", encoding="utf-8")
    assert load_functions(tmp_path) is None
    (tmp_path / "functions.json").write_text("[]", encoding="utf-8")
    assert load_functions(tmp_path) is None


def test_save_then_load_round_trips_the_listing(tmp_path: Path) -> None:
    listing = {"expression": [{"name": "dateFormat", "category": "Date"}, {"name": "now"}],
               "script": ["system", "system.tag"]}
    assert save_functions(tmp_path, listing, "8.3.2") == tmp_path / "functions.json"
    assert load_functions(tmp_path) == Functions({"dateformat": "dateFormat", "now": "now"},
                                                 frozenset({"system", "system.tag"}), "8.3.2")


def test_schemas_functions_saves_the_gateway_listing(tmp_path: Path, monkeypatch, capsys, bundle) -> None:
    saved: list[tuple[Path, dict[str, Any], str | None]] = []

    class FakeGateway:
        def __init__(self, url: str | None = None) -> None:
            pass

        def functions(self) -> dict[str, Any]:
            return {"expression": [{"name": "dateFormat"}, {"name": "now"}], "script": ["system", "system.tag"]}

        def health(self) -> dict[str, Any]:
            return {"ignitionVersion": "8.3.2"}

    def save(base: Path, listing: dict[str, Any], ignition: str | None) -> Path:
        saved.append((base, listing, ignition))
        return base / "functions.json"

    monkeypatch.setattr("pdk.gateway.client.Gateway", FakeGateway)
    monkeypatch.setattr("pdk.bundle.save_functions", save)
    assert main(["schemas", "functions"]) == 0
    assert len(saved) == 1 and saved[0][2] == "8.3.2" and saved[0][1]["script"] == ["system", "system.tag"]
    assert "2 expression functions and 2 system.* names" in capsys.readouterr().out

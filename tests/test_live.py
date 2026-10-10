"""The live catalog against a fake module: drift by pointer, live-only components and the change log text."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from pdk import live

LABEL = {
    "id": "ia.display.label", "name": "Label", "palette": {"category": "display", "variants": [{"label": "Label"}]},
    "defaultMetaName": "Label",
    "schema": {"type": "object", "properties": {"text": {"type": "string", "default": ""}}},
}
FLEX = {
    "id": "ia.container.flex", "name": "Flex", "palette": {"category": "container"}, "defaultMetaName": "FlexContainer",
    "schema": {"type": "object"}, "childPositionSchema": {"type": "object", "properties": {"grow": {"type": "number"}}},
}
MAP = {"id": "ia.display.map", "name": "Map", "palette": {"category": "display"}, "schema": {"type": "object"},
       "events": [{"name": "onClick", "description": "", "documentationUrl": "", "schema": {"type": "object"}}]}


def _registered(descriptor: dict[str, Any], module: str = "com.inductiveautomation.perspective") -> dict[str, Any]:
    """The module's shape: every key present, empty ones as null or []."""
    out = {"childPositionSchema": None, "events": [], "extensionFunctions": [], "deprecated": False,
           **copy.deepcopy(descriptor), "moduleId": module}
    return out


def _bundle(root: Path) -> Path:
    components = root / "3.3.9" / "components"
    components.mkdir(parents=True)
    (root / "3.3.9" / "manifest.json").write_text("{}", encoding="utf-8")
    (components / live.BUILT_IN_FILE).write_text(json.dumps({"components": [LABEL, FLEX]}), encoding="utf-8")
    (components / "perspective-map.components.json").write_text(json.dumps({"components": [MAP]}),
                                                                encoding="utf-8")
    return root / "3.3.9"


class FakeGateway:
    url = "http://127.0.0.1:8088"

    def __init__(self, components: list[dict[str, Any]], version: str = "3.3.9 (b2026082511)") -> None:
        self.components, self.version = components, version

    def health(self) -> dict[str, Any]:
        return {"perspectiveVersion": self.version}

    def catalog(self, full: bool = False) -> dict[str, Any]:
        return {"schemaAvailable": True, "count": len(self.components), "components": self.components}


def test_identical_catalog_has_no_drift(tmp_path: Path) -> None:
    _bundle(tmp_path)
    ok, text = live.sync(FakeGateway([_registered(d) for d in (LABEL, FLEX, MAP)]), tmp_path)
    assert ok and text.startswith("Perspective 3.3.9: drift 0/2 built-in, 0/1 other bundled; 0 live-only")
    assert not (tmp_path / "3.3.9" / "components" / live.LIVE_FILE).exists()


def test_drift_is_reported_by_pointer(tmp_path: Path) -> None:
    _bundle(tmp_path)
    changed = _registered(LABEL)
    changed["schema"]["properties"]["text"]["type"] = ["string", "number"]
    ok, text = live.sync(FakeGateway([changed, _registered(FLEX)]), tmp_path)
    assert not ok and "drift 1/2 built-in" in text
    assert "~ ia.display.label: /schema/properties/text/type" in text
    assert "not registered: ia.display.map" in text


def test_registry_defaults_are_not_drift(tmp_path: Path) -> None:
    _bundle(tmp_path)
    shape = {"id": "ia.shapes.rect", "palette": {}, "showOnPalette": False, "schema": {"type": "object"}}
    components = tmp_path / "3.3.9" / "components" / live.BUILT_IN_FILE
    components.write_text(json.dumps({"components": [LABEL, FLEX, shape]}), encoding="utf-8")
    filled = _registered({**shape, "name": "Rectangle", "defaultMetaName": "Rectangle", "deprecated": True,
                          "palette": {"category": "unknown"}})
    del filled["showOnPalette"]
    ok, text = live.sync(FakeGateway([_registered(LABEL), _registered(FLEX), _registered(MAP), filled]), tmp_path)
    assert ok and "drift 0/3 built-in" in text, text


def test_third_party_components_are_written_for_the_loader(tmp_path: Path) -> None:
    _bundle(tmp_path)
    vendor = _registered({"id": "acme.gauge", "name": "Acme Gauge", "palette": {"category": "display"},
                          "schema": {"type": "object", "properties": {"value": {"type": "number"}}}}, "com.acme")
    ok, text = live.sync(FakeGateway([_registered(LABEL), _registered(FLEX), _registered(MAP), vendor]), tmp_path)
    assert ok and "1 live-only" in text and "+ acme.gauge (com.acme) Acme Gauge" in text
    written = json.loads((tmp_path / "3.3.9" / "components" / live.LIVE_FILE).read_text(encoding="utf-8"))
    assert [c["id"] for c in written["components"]] == ["acme.gauge"]
    assert set(live.offline_descriptors(tmp_path / "3.3.9")) == {"ia.display.label", "ia.container.flex",
                                                                 "ia.display.map"}  # the live file is not offline

    live.sync(FakeGateway([_registered(LABEL), _registered(FLEX), _registered(MAP)]), tmp_path)
    assert not (tmp_path / "3.3.9" / "components" / live.LIVE_FILE).exists()  # stale live components go away


def test_a_gateway_without_its_bundle_is_refused(tmp_path: Path) -> None:
    _bundle(tmp_path)
    ok, text = live.sync(FakeGateway([], version="3.4.0 (b1)"), tmp_path)
    assert not ok and "no bundle for it" in text and "pdk schemas extract" in text


def test_change_log_text() -> None:
    page = {"seq": 7, "watching": "pdk_sandbox", "truncated": False, "events": [
        {"seq": 6, "at": "2026-10-04T21:00:00Z", "op": "modified", "type": "com.inductiveautomation.perspective/views",
         "path": "PDK/Demo", "origin": "external", "actor": "admin", "signature": "0123456789abcdef", "folder": False},
        {"seq": 7, "at": "2026-10-04T21:00:01Z", "op": "deleted", "type": "com.inductiveautomation.perspective/views",
         "path": "PDK", "origin": "pdk", "actor": None, "signature": None, "folder": None},
    ]}
    lines = live.changes_text(page).splitlines()
    assert lines[0] == "seq 7 in pdk_sandbox"
    assert lines[1] == "#6 2026-10-04T21:00:00Z modified views/PDK/Demo [external by admin] 0123456789"
    assert lines[2] == "#7 2026-10-04T21:00:01Z deleted views/PDK [pdk]"
    assert "read-only" in live.changes_text({"seq": 0, "watching": None, "events": []})

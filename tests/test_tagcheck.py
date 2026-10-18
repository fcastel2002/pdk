"""R020/R021 (pdk.tagcheck): a view's tag paths checked against a gateway, with a fake gateway and synthetic views."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from pdk.gateway.client import GatewayError
from pdk.project import PERSPECTIVE_DIR, load_project
from pdk.tagcheck import BATCH, checker, diagnostics
from tests.helpers import label, view

BINDING_POINTER = "/root/children/0/propConfig/props.text/binding/config/tagPath"


class FakeGateway:
    """read_tags as the module answers it: Bad_NotFound for the missing paths, Good for the others; a batch holding
    a path with `refused` in it is refused with G009 (the module cannot parse it); `error` is raised for every call."""

    def __init__(self, missing: set[str] | frozenset[str] = frozenset(), refused: str | None = None,
                 error: GatewayError | None = None) -> None:
        self.missing = set(missing)
        self.refused = refused
        self.error = error
        self.calls: list[list[str]] = []

    def read_tags(self, paths: list[str]) -> dict[str, Any]:
        self.calls.append(list(paths))
        if self.error is not None:
            raise self.error
        if self.refused and any(self.refused in path for path in paths):
            raise GatewayError("G009", "the module cannot parse the tag path")
        return {"results": [{"path": p, "quality": "Bad_NotFound" if p in self.missing else "Good"} for p in paths]}


def _bound(path: str, **config: Any) -> dict[str, Any]:
    """The propConfig of a label's text bound to a tag (direct unless the config says otherwise)."""
    return {"props.text": {"binding": {"type": "tag", "config": {"tagPath": path, "mode": "direct", **config}}}}


def _indirect() -> dict[str, Any]:
    return _bound("{1}/Running", mode="indirect", references={"1": "{view.params.tagPath}"})


def _row(params: dict[str, Any] | None = None) -> dict[str, Any]:
    """A view that shows the running state of the equipment its tagPath param names."""
    return view(label("Run", propConfig=_indirect()), params=params if params is not None else {"tagPath": ""})


def _embed(path: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"type": "ia.display.view", "meta": {"name": "Pump"}, "position": {"basis": "48px"},
            "props": {"path": path, "params": params}}


def _project(tmp_path: Path, views: dict[str, dict[str, Any]]) -> Any:
    root = tmp_path / "sandbox"
    for path, data in views.items():
        file = root / PERSPECTIVE_DIR / "views" / path / "view.json"
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(json.dumps(data), encoding="utf-8")
    return load_project(root)


def test_a_missing_direct_tag_is_r020_at_its_binding() -> None:
    screen = view(label("Run", propConfig=_bound("[default]Plant/Pump9/Run")))
    [d] = diagnostics(FakeGateway({"[default]Plant/Pump9/Run"}), screen, "Screens/Pump", None)
    assert (d["code"], d["severity"]) == ("R020", "warning")
    assert d["pointer"] == BINDING_POINTER
    assert "tag '[default]Plant/Pump9/Run' does not exist on the gateway" in d["message"]
    assert d["hint"]


def test_an_indirect_path_is_checked_as_each_caller_passes_it(tmp_path) -> None:
    row = _row()
    home = view(_embed("Screens/Row", {"tagPath": "[default]Plant/Pump9"}))
    project = _project(tmp_path, {"Screens/Row": row, "Screens/Home": home})
    [d] = diagnostics(FakeGateway({"[default]Plant/Pump9/Running"}), row, "Screens/Row", project)
    assert (d["code"], d["severity"], d["pointer"]) == ("R020", "warning", BINDING_POINTER)
    assert "'[default]Plant/Pump9/Running'" in d["message"]
    assert "(as Screens/Home " in d["message"] and d["message"].endswith("passes it)")


def test_existing_tags_through_the_callers_give_nothing(tmp_path) -> None:
    row = _row()
    home = view(_embed("Screens/Row", {"tagPath": "[default]Plant/Pump1"}))
    project = _project(tmp_path, {"Screens/Row": row, "Screens/Home": home})
    gateway = FakeGateway()
    assert diagnostics(gateway, row, "Screens/Row", project) == []
    assert gateway.calls == [["[default]Plant/Pump1/Running"]]


def test_a_path_only_the_param_default_gives_is_counted_in_r021_not_reported(tmp_path) -> None:
    row = _row({"tagPath": "[default]Plant/Placeholder"})
    project = _project(tmp_path, {"Screens/Row": row})
    found = diagnostics(FakeGateway({"[default]Plant/Placeholder/Running"}), row, "Screens/Row", project)
    assert [d["code"] for d in found] == ["R021"]
    assert found[0]["severity"] == "info"
    assert "1 tag path(s) depend on values known only at runtime" in found[0]["message"]


def test_a_runtime_only_path_is_r021_and_not_sent_to_the_gateway() -> None:
    screen = view(label("Run", propConfig=_bound("{1}/Running", mode="indirect",
                                                 references={"1": "{session.custom.plant}"})))
    gateway = FakeGateway()
    [d] = diagnostics(gateway, screen, "Screens/Run", None)
    assert (d["code"], d["severity"]) == ("R021", "info")
    assert "{1}/Running" in d["detail"]
    assert gateway.calls == []


def test_paths_with_braces_are_never_sent_to_the_gateway() -> None:
    screen = view(label("A", propConfig=_bound("{view.params.tagPath}/Run")),
                  label("B", propConfig=_bound("[default]Plant/Pump1/{view.params.x}")))
    gateway = FakeGateway({"[default]Plant/Pump1/Run"})
    found = diagnostics(gateway, screen, "Screens/Braces", None)
    assert gateway.calls == []
    assert [d["code"] for d in found] == ["R021"] and "2 tag path(s)" in found[0]["message"]


def test_existing_direct_tags_give_nothing() -> None:
    screen = view(label("Run", propConfig=_bound("[default]Plant/Pump1/Run")))
    gateway = FakeGateway()
    assert diagnostics(gateway, screen, "Screens/Pump", None) == []
    assert gateway.calls == [["[default]Plant/Pump1/Run"]]


def test_checker_without_strict_gives_nothing_when_the_gateway_cannot_read() -> None:
    screen = view(label("Run", propConfig=_bound("[default]Plant/Pump9/Run")))
    unreachable = GatewayError("G001", "the gateway does not answer")
    assert checker(FakeGateway(error=unreachable))(screen, "Screens/Pump", None) == []


def test_checker_with_strict_raises_the_gateway_error() -> None:
    screen = view(label("Run", propConfig=_bound("[default]Plant/Pump9/Run")))
    unreachable = GatewayError("G001", "the gateway does not answer")
    with pytest.raises(GatewayError):
        checker(FakeGateway(error=unreachable), strict=True)(screen, "Screens/Pump", None)


def test_checker_returns_the_same_diagnostics_as_diagnostics() -> None:
    screen = view(label("Run", propConfig=_bound("[default]Plant/Pump9/Run")))
    found = checker(FakeGateway({"[default]Plant/Pump9/Run"}))(screen, "Screens/Pump", None)
    assert [d["code"] for d in found] == ["R020"] and found[0]["pointer"] == BINDING_POINTER


def test_a_batch_the_gateway_refuses_is_read_one_path_at_a_time() -> None:
    screen = view(label("Gone", propConfig=_bound("[default]Plant/Pump9/Run")),
                  label("Odd", propConfig=_bound("[default]Plant/!!Odd/Run")))
    gateway = FakeGateway({"[default]Plant/Pump9/Run"}, refused="!!")
    [d] = diagnostics(gateway, screen, "Screens/Mix", None)
    assert d["code"] == "R020" and "Pump9" in d["message"]
    assert [len(call) for call in gateway.calls] == [2, 1, 1]  # the batch, then each path alone


def test_more_than_200_paths_are_read_in_batches() -> None:
    children = [label(f"L{i}", propConfig=_bound(f"[default]Plant/Pump{i}/Run")) for i in range(BATCH + 50)]
    gateway = FakeGateway()
    assert diagnostics(gateway, view(*children), "Screens/Many", None) == []
    assert [len(call) for call in gateway.calls] == [BATCH, 50]
    assert len({path for call in gateway.calls for path in call}) == BATCH + 50


def test_an_empty_answer_to_a_single_path_read_is_skipped() -> None:
    class Gateway:
        def read_tags(self, paths: list[str]) -> dict[str, Any]:
            if len(paths) > 1:
                raise GatewayError("G009", "bad path")
            return {"results": []}

    view = {"root": {"type": "ia.container.flex", "meta": {"name": "root"}, "children": [
        {"type": "ia.display.label", "meta": {"name": "A"}, "propConfig": {"props.text": {"binding": {
            "type": "tag", "config": {"mode": "direct", "tagPath": "[default]PDK/A"}}}}},
        {"type": "ia.display.label", "meta": {"name": "B"}, "propConfig": {"props.text": {"binding": {
            "type": "tag", "config": {"mode": "direct", "tagPath": "[default]PDK/B"}}}}}]}}
    assert diagnostics(Gateway(), view, "V", None) == []

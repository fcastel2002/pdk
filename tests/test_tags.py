"""Simulated tags (F5 part B) without a gateway: paths, values, the tag steps of act and the tag/alarm expectations."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from pdk import tags
from pdk.cli import main
from pdk.eyes import act, expect
from pdk.gateway import client
from tests.test_eyes_act import _nodes
from tests.test_eyes_expect import _joined


class FakeTags:
    """The tag routes of the module over a dict of path -> (value, quality), plus alarm events."""

    def __init__(self, values: dict[str, tuple[Any, str]] | None = None,
                 alarms: list[dict[str, Any]] | None = None) -> None:
        self.values = dict(values or {})
        self.events = alarms or []
        self.writes: list[dict[str, Any]] = []
        self.reads = 0

    def _state(self, path: str) -> dict[str, Any]:
        full = path if path.startswith("[") else f"[default]{path}"
        value, quality = self.values.get(full, (None, "Bad_NotFound"))
        return {"path": full, "value": value, "quality": quality}

    def read_tags(self, paths: list[str]) -> dict[str, Any]:
        self.reads += 1
        return {"results": [self._state(path) for path in paths]}

    def write_tags(self, writes: list[dict[str, Any]]) -> dict[str, Any]:
        self.writes += writes
        results = []
        for write in writes:
            state = self._state(write["path"])
            if state["quality"] in ("Bad_NotFound", tags.LICENSE_EXCEEDED):
                results.append({**state, "write": "Bad_Unsupported" if state["quality"] != "Bad_NotFound"
                                else "Bad_NotFound"})
                continue
            self.values[state["path"]] = (write["value"], write.get("quality", "Good"))
            results.append({**self._state(write["path"]), "write": "Good"})
        return {"results": results}

    def alarms(self, path: str | None = None) -> dict[str, Any]:
        return {"path": path, "alarms": self.events}

    def import_tags(self, path: str, tags_json: dict[str, Any]) -> dict[str, Any]:
        return {"path": f"[default]{path}", "tags": 1, "qualities": {"Good": 1}}

    def tags(self, path: str | None = None, depth: int = 1) -> dict[str, Any]:
        return {"path": path, "nodes": []}

    def acknowledge_alarms(self, path: str) -> dict[str, Any]:
        if not self.events:
            raise client.GatewayError("G002", "no route")
        return {"path": path, "unacknowledged": len(self.events), "acknowledged": len(self.events)}


def test_paths_hang_from_pdk() -> None:
    assert tags.full_path("Sandbox/Setpoint") == "PDK/Sandbox/Setpoint"
    assert tags.full_path("/Sandbox/Setpoint/") == "PDK/Sandbox/Setpoint"
    assert tags.full_path("pdk/Sandbox") == "pdk/Sandbox"
    assert tags.full_path("PDK") == "PDK"
    assert tags.full_path("[default]Planta/X") == "[default]Planta/X"


@pytest.mark.parametrize(("raw", "value"), [
    ("42", 42), ("42.5", 42.5), ("true", True), ("null", None), ("en marcha", "en marcha"), ("[1]", "[1]"),
    ('"x"', '"x"'),
])
def test_values_parse_like_json_scalars(raw: str, value: Any) -> None:
    assert tags.parse_value(raw) == value


@pytest.mark.parametrize(("actual", "raw", "ok"), [
    (42.0, "42", True), (42.099998474, "42.1", True), (42.0, "42.5", False), (True, "true", True),
    (1, "true", False), ("Done", "Done", True), ("42", "42", True), (None, "null", True), ("", "null", False),
])
def test_read_values_match_the_expected_text(actual: Any, raw: str, ok: bool) -> None:
    assert tags.matches(actual, raw) is ok


def test_write_parses_the_value_and_can_keep_it() -> None:
    fake = FakeTags({"[default]PDK/Sandbox/Setpoint": (20.0, "Good")})
    assert tags.write(fake, "Sandbox/Setpoint", "42")["value"] == 42
    result = tags.write(fake, "Sandbox/Setpoint", None, "Bad_Stale")
    assert (result["value"], result["quality"]) == (42, "Bad_Stale")
    assert fake.writes[-1] == {"path": "PDK/Sandbox/Setpoint", "value": 42, "quality": "Bad_Stale"}


def test_write_reports_missing_and_unlicensed_tags() -> None:
    fake = FakeTags({"[default]PDK/Over": (None, tags.LICENSE_EXCEEDED)})
    with pytest.raises(ValueError, match="does not exist"):
        tags.write(fake, "Nope", "1")
    with pytest.raises(ValueError, match="does not exist"):
        tags.write(fake, "Nope", None, "Bad")
    with pytest.raises(ValueError, match="over its tag license"):
        tags.write(fake, "Over", "1")


def test_texts_add_the_license_hint_only_when_needed() -> None:
    good = tags.values_text([{"path": "[default]PDK/A", "value": 1, "quality": "Good"}])
    assert good == "[default]PDK/A = 1 (Good)"
    listed = tags.list_text({"path": "[default]PDK", "nodes": [
        {"path": "[default]PDK/F", "type": "Folder"},
        {"path": "[default]PDK/F/A", "type": "AtomicTag", "dataType": "Int4", "value": None,
         "quality": tags.LICENSE_EXCEEDED}]})
    assert "[default]PDK/F/\n" in listed and "Maker runs 10,000 tags" in listed


def test_tag_steps_parse_without_components() -> None:
    steps, malformed = act.parse('tag Sandbox/Setpoint 42; tag Sandbox/Message "en marcha" quality Bad_Stale; '
                                 "tag Sandbox/Setpoint quality Good", _nodes())
    assert malformed == []
    assert [(s.verb, s.tag, s.value, s.quality) for s in steps] == [
        ("tag", "Sandbox/Setpoint", "42", None), ("tag", "Sandbox/Message", "en marcha", "Bad_Stale"),
        ("tag", "Sandbox/Setpoint", None, "Good")]
    _, malformed = act.parse("tag Sandbox/Setpoint; tag A 1 2; tag A 1 quality", _nodes())
    assert [d["code"] for d in malformed] == ["A005"] * 3


class _Page:
    def __init__(self) -> None:
        self.waited: list[int] = []

    def wait_for_timeout(self, ms: int) -> None:
        self.waited.append(ms)


def test_tag_steps_write_through_the_gateway_and_settle() -> None:
    steps, _ = act.parse("tag Sandbox/Setpoint 42; wait 10", _nodes())
    written: list[act.Step] = []
    settled: list[Any] = []
    page = _Page()
    outcome = act.run(page, steps, settled.append, {}, tags=written.append)
    assert outcome.summary() == "act: 2/2 done" and outcome.diagnostics == []
    assert [s.tag for s in written] == ["Sandbox/Setpoint"] and page.waited == [act.TAG_SETTLE_MS, 10]
    assert len(settled) == 2


def test_a_tag_step_the_gateway_refuses_is_a004_with_its_message() -> None:
    steps, _ = act.parse("tag [default]Planta/X 1; wait 10", _nodes())

    def refuse(_step: act.Step) -> None:
        raise client.GatewayError("G013", "Tag writes go only to [default]PDK/, not '[default]Planta/X'.")

    outcome = act.run(_Page(), steps, lambda _page: None, {}, tags=refuse)
    [failure] = outcome.diagnostics
    assert (outcome.done, failure["code"]) == (0, "A004")
    assert "G013 Tag writes go only" in failure["message"] and "pdk tags apply" in failure["hint"]
    outcome = act.run(_Page(), steps, lambda _page: None, {})
    assert "no gateway" in outcome.diagnostics[0]["message"]


def _check(text: str, fake: FakeTags) -> list[dict[str, Any]]:
    return expect.check(_joined(), text, tags.Probe(fake)).diagnostics


def test_tag_expectations_read_back_once() -> None:
    fake = FakeTags({"[default]PDK/Sandbox/Setpoint": (42.0, "Good"),
                     "[default]PDK/Sandbox/Message": ("x", "Bad_Stale")})
    assert _check("tag Sandbox/Setpoint = 42; tag Sandbox/Setpoint 42.0; tag Sandbox/Setpoint quality Good; "
                  "tag Sandbox/Message quality Bad_Stale; tag Sandbox/Message quality Bad", fake) == []
    assert fake.reads == 2
    failures = _check("tag Sandbox/Setpoint = 50; tag Sandbox/Message = x; tag Nope = 1; "
                      "tag Sandbox/Setpoint quality Bad", fake)
    assert [d["code"] for d in failures] == ["A001"] * 4
    assert "it is 42.0 (Good)" in failures[0]["message"]
    assert "(Bad_Stale)" in failures[1]["message"]  # the value matches, the quality does not
    assert "Bad_NotFound" in failures[2]["message"]


def test_alarm_expectations() -> None:
    active = {"tag": "[default]PDK/North/Level", "name": "High", "state": "ActiveUnacked", "cleared": False}
    cleared = {**active, "state": "ClearUnacked", "cleared": True}
    assert _check("alarm North/Level active", FakeTags(alarms=[cleared, active])) == []
    assert _check("alarm North/Level cleared", FakeTags(alarms=[cleared])) == []
    assert _check("alarm North/Level cleared", FakeTags()) == []
    [failure] = _check("alarm North/Level active", FakeTags(alarms=[cleared]))
    assert failure["code"] == "A001" and "0 active" in failure["message"] and "ClearUnacked" in failure["message"]


def test_tag_expectation_errors() -> None:
    codes = [d["code"] for d in expect.check(_joined(), "tag A = 1; alarm A on; tag A quality").diagnostics]
    assert codes == ["A002"] * 3  # no gateway, unknown state, missing code

    class Down(FakeTags):
        def read_tags(self, paths: list[str]) -> dict[str, Any]:
            raise client.GatewayError("G012", "busy")

    [failure] = _check("tag A = 1", Down())
    assert failure["code"] == "A001" and "could not read it" in failure["message"]


def test_module_tag_errors_have_their_codes() -> None:
    assert client.error_for(403, {"error": "OUTSIDE_TAG_FOLDER", "message": "x"}, "u").code == "G013"
    assert client.error_for(404, {"error": "TAG_NOT_FOUND", "message": "x"}, "u").code == "G007"
    assert client.error_for(504, {"error": "TAGS_BUSY", "message": "x"}, "u").code == "G012"
    assert client.error_for(400, {"error": "INVALID_TAGS", "message": "x"}, "u").code == "G009"


def test_cli_checks_the_arguments_of_each_action(capsys) -> None:
    assert main(["tags", "write", "Sandbox/Setpoint"]) == 2
    assert main(["tags", "apply"]) == 2
    assert main(["tags", "list", "a", "b"]) == 2
    assert "pdk tags list [path]" in capsys.readouterr().err


def test_bench_fixture_is_importable() -> None:
    data = json.loads((Path(__file__).parents[1] / "bench" / "tags" / "sandbox.json").read_text(encoding="utf-8"))
    names = {folder["name"]: [tag["name"] for tag in folder["tags"]] for folder in data["tags"]}
    assert names == {"Sandbox": ["Setpoint", "Message"], "North Area": ["Tank 1 Level", "Pump 1 Fault"]}
    for folder in data["tags"]:
        for tag in folder["tags"]:
            assert tag["valueSource"] == "memory"
            for alarm in tag.get("alarms", []):
                assert alarm["displayPath"].startswith("North Area/")


def test_apply_acknowledges_the_alarms_it_resets(tmp_path: Path) -> None:
    file = tmp_path / "tags.json"
    file.write_text('{"tags": []}', encoding="utf-8")
    event = {"tag": "[default]PDK/A", "name": "High", "state": "ClearUnacked", "cleared": True}
    assert "1 alarm events acknowledged" in tags.apply(FakeTags(alarms=[event]), file)
    assert "reinstall it" in tags.apply(FakeTags(), file)  # an older module without the route

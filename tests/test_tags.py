"""Simulated tags (F5 part B) without a gateway: paths, values, the tag steps of act and the tag/alarm expectations."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from pdk import mcp_server, tags
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


class BrowseTags(FakeTags):
    """A FakeTags whose tree lists the given nodes, for the bad-node report of an apply."""

    def __init__(self, nodes: list[dict[str, Any]], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.nodes = nodes

    def tags(self, path: str | None = None, depth: int = 1) -> dict[str, Any]:
        return {"path": path, "nodes": self.nodes}


class NoLiveTags(FakeTags):
    """A gateway for an apply into _types_/PDK: acknowledging or reading tags there would be a bug."""

    def acknowledge_alarms(self, path: str) -> dict[str, Any]:
        raise AssertionError("definitions have no alarm events to acknowledge")

    def tags(self, path: str | None = None, depth: int = 1) -> dict[str, Any]:
        raise AssertionError("definitions have no values to read back")


class FakeHistory:
    """The history routes of the gateway: its providers, and the points of each store call."""

    def __init__(self, providers: list[str] | None = None) -> None:
        self.providers = ["Hist"] if providers is None else providers
        self.stored: list[tuple[str, list[dict[str, Any]]]] = []

    def history_providers(self) -> list[str]:
        return list(self.providers)

    def store_history(self, provider: str, points: list[dict[str, Any]]) -> dict[str, Any]:
        self.stored.append((provider, points))
        return {"stored": len(points), "tagProvider": "default"}


END_MS = 1_700_000_000_000  # the fixed end of the stored points


def _gateway_as(monkeypatch: pytest.MonkeyPatch, gateway: Any) -> None:
    monkeypatch.setattr(client, "Gateway", lambda *args, **kwargs: gateway)


def test_full_path_keeps_types_and_providers() -> None:
    assert tags.full_path("_types_/PDK/Motor") == "_types_/PDK/Motor"
    assert tags.full_path("_types_") == "_types_"
    assert tags.full_path("Sandbox/X") == "PDK/Sandbox/X"
    assert tags.full_path("[default]X") == "[default]X"


@pytest.mark.parametrize(("text", "ms"), [("500ms", 500), ("30s", 30_000), ("1m", 60_000), ("2h", 7_200_000),
                                          ("1d", 86_400_000), ("1.5m", 90_000), ("10", 10_000)])
def test_durations_are_milliseconds(text: str, ms: int) -> None:
    assert tags.duration_ms(text) == ms


@pytest.mark.parametrize("text", ["abc", ""])
def test_bad_durations_raise(text: str) -> None:
    with pytest.raises(ValueError, match="is not a duration"):
        tags.duration_ms(text)


def test_history_stores_the_values_ending_at_the_end() -> None:
    gateway = FakeHistory()
    text = tags.history(gateway, "Sandbox/Flow", ["1", "2", "3"], every="1m", end_ms=END_MS)
    [(provider, points)] = gateway.stored
    assert provider == "Hist"
    assert [point["timestamp"] for point in points] == [END_MS - 120_000, END_MS - 60_000, END_MS]
    assert [point["value"] for point in points] == [1, 2, 3]
    assert all(type(point["value"]) is int for point in points)
    assert {point["path"] for point in points} == {"PDK/Sandbox/Flow"}
    assert "stored 3 point(s)" in text and "every 1m" in text


def test_history_adds_the_quality_only_when_given() -> None:
    gateway = FakeHistory()
    tags.history(gateway, "Sandbox/Flow", ["1", "2"], end_ms=END_MS)
    tags.history(gateway, "Sandbox/Flow", ["3"], quality="Bad_Stale", end_ms=END_MS)
    plain, marked = gateway.stored
    assert all("quality" not in point for point in plain[1])
    assert [point.get("quality") for point in marked[1]] == ["Bad_Stale"]


def test_history_provider_defaults_to_the_only_one_or_takes_the_given() -> None:
    gateway = FakeHistory(["Hist"])
    tags.history(gateway, "Sandbox/Flow", ["1"], end_ms=END_MS)
    assert gateway.stored[-1][0] == "Hist"
    several = FakeHistory(["Hist", "Other"])
    with pytest.raises(ValueError, match="Hist, Other"):
        tags.history(several, "Sandbox/Flow", ["1"], end_ms=END_MS)
    assert several.stored == []
    tags.history(several, "Sandbox/Flow", ["1"], provider="Other", end_ms=END_MS)
    assert several.stored[0][0] == "Other"


def test_history_needs_values() -> None:
    gateway = FakeHistory()
    with pytest.raises(ValueError, match="no values"):
        tags.history(gateway, "Sandbox/Flow", [], end_ms=END_MS)
    with pytest.raises(ValueError, match="2 are empty"):
        tags.history(gateway, "Sandbox/Flow", ["10", " ", "12"], end_ms=END_MS)
    assert gateway.stored == []


def test_list_hides_udt_parameters_and_marks_instances() -> None:
    result = {"path": "[default]PDK", "nodes": [
        {"type": "Node", "path": "[default]PDK/Motor 1/Parameters"},
        {"type": "UdtInstance", "path": "[default]PDK/Motor 1", "typeId": "PDK/Motor"},
        {"type": "AtomicTag", "path": "[default]PDK/Sandbox/Setpoint", "dataType": "Int4", "value": 42,
         "quality": "Good"},
    ]}
    assert tags.list_text(result).splitlines() == [
        "[default]PDK: 2 nodes",
        "  [default]PDK/Motor 1/ (UdtInstance)",
        "  [default]PDK/Sandbox/Setpoint Int4 = 42 (Good)",
    ]


def test_apply_into_types_reports_the_import_only(tmp_path: Path) -> None:
    file = tmp_path / "motor.json"
    file.write_text('{"tags": []}', encoding="utf-8")
    event = {"tag": "[default]PDK/A", "name": "High", "state": "ClearUnacked", "cleared": True}
    assert tags.apply(NoLiveTags(alarms=[event]), file, to="_types_/PDK") == \
        "motor.json -> [default]_types_/PDK: 1 tags (Good 1)"
    assert "1 alarm events acknowledged" in tags.apply(FakeTags(alarms=[event]), file, to="PDK")


def test_apply_reports_only_the_bad_atomic_tags(tmp_path: Path) -> None:
    file = tmp_path / "bad.json"
    file.write_text('{"tags": []}', encoding="utf-8")
    event = {"tag": "[default]PDK/A", "name": "High", "state": "ClearUnacked", "cleared": True}
    nodes = [
        {"type": "AtomicTag", "path": "[default]PDK/Sandbox/Missing", "dataType": "Int4", "value": None,
         "quality": "Bad_NotFound"},
        {"type": "UdtInstance", "path": "[default]PDK/Motor 1", "quality": "Bad_Unsupported"},
        {"type": "AtomicTag", "path": "[default]PDK/Sandbox/Setpoint", "dataType": "Int4", "value": 42,
         "quality": "Good"},
    ]
    assert tags.apply(BrowseTags(nodes, alarms=[event]), file).splitlines() == [
        "bad.json -> [default]PDK: 1 tags (Good 1)",
        "  1 alarm events acknowledged",
        "  [default]PDK/Sandbox/Missing Int4 = null (Bad_NotFound)",
    ]


def test_cli_history_lists_the_providers(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    _gateway_as(monkeypatch, FakeHistory())
    assert main(["tags", "history"]) == 0
    assert capsys.readouterr().out.strip() == "history providers: Hist"


def test_cli_history_takes_a_path_and_a_value(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    _gateway_as(monkeypatch, FakeHistory())
    assert main(["tags", "history", "Sandbox/Flow"]) == 2
    assert "pdk tags history <path> <v1,v2,...>" in capsys.readouterr().err


def test_cli_history_passes_the_values_and_options(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    calls: list[tuple[str, list[str], dict[str, Any]]] = []

    def record(gateway: Any, path: str, values: list[str], *, every: str, provider: str | None,
               quality: str | None) -> str:
        calls.append((path, values, {"every": every, "provider": provider, "quality": quality}))
        return "stored 3 point(s)"

    _gateway_as(monkeypatch, FakeHistory())
    monkeypatch.setattr(tags, "history", record)
    assert main(["tags", "history", "Sandbox/Flow", "1,2,3", "--every", "30s", "--provider", "Other",
                 "--quality", "Bad_Stale"]) == 0
    assert main(["tags", "history", "Sandbox/Flow", "4"]) == 0
    assert calls == [
        ("Sandbox/Flow", ["1", "2", "3"], {"every": "30s", "provider": "Other", "quality": "Bad_Stale"}),
        ("Sandbox/Flow", ["4"], {"every": "1m", "provider": None, "quality": None}),
    ]
    assert capsys.readouterr().out == "stored 3 point(s)\nstored 3 point(s)\n"


def test_mcp_history_lists_then_needs_a_value_then_stores(monkeypatch: pytest.MonkeyPatch) -> None:
    gateway = FakeHistory()
    monkeypatch.setattr(mcp_server, "Gateway", lambda *args, **kwargs: gateway)
    assert mcp_server.pdk_tags(action="history") == "history providers: Hist"
    assert mcp_server.pdk_tags(action="history", path="Sandbox/Flow") == \
        "pdk: history needs value: comma-separated values, oldest first"
    assert gateway.stored == []
    text = mcp_server.pdk_tags(action="history", path="Sandbox/Flow", value="1,2,3")
    assert text.startswith("stored 3 point(s) of [default]PDK/Sandbox/Flow in Hist, every 1m")
    assert [point["value"] for point in gateway.stored[0][1]] == [1, 2, 3]


def test_apply_sample_reads_the_tags_pdk_ships(monkeypatch, tmp_path) -> None:
    from pdk import tags

    seen = {}

    class Gateway:
        def import_tags(self, path, data):
            seen["data"] = data
            return {"path": path, "tags": 4, "qualities": {"Good": 6}}

        def acknowledge_alarms(self, path):
            return {"acknowledged": 0}

        def tags(self, path, depth):
            return {"nodes": []}

    monkeypatch.chdir(tmp_path)  # no file called "sample" here
    text = tags.apply(Gateway(), Path("sample"))
    assert text.startswith("sandbox.json -> PDK: 4 tags") and seen["data"]

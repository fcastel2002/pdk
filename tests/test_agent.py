"""Agent sessions: what each tool call does, the rules the gate enforces, and the session-only tools."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from pdk import mcp_server, sync
from pdk.agent import gate, plan, tools, work
from pdk.agent.gate import NORMAL, ULTRA, Gate, Grant, State
from pdk.agent.tools import Effect
from pdk.gateway.client import GatewayError
from tests.helpers import label, view

KEY = b"0123456789abcdef"
NOW = 1_000_000.0


# --- what each call does --------------------------------------------------------------------------


def test_every_tool_the_server_registers_is_classified() -> None:
    registered = {tool.name for tool in asyncio.run(mcp_server.server.list_tools())}
    assert registered - tools.TOOLS.keys() == set()
    assert {"pdk_work", "pdk_plan"} <= tools.TOOLS.keys()  # registered only in agent sessions


@pytest.mark.parametrize(("name", "args", "expected"), [
    ("pdk_tags", {"action": "list"}, Effect.READ),
    ("pdk_tags", {"action": "history", "path": "X"}, Effect.READ),
    ("pdk_tags", {"action": "history", "path": "X", "value": "1,2"}, Effect.TAGS_WRITE),
    ("pdk_tags", {"action": "write", "path": "X", "value": "1"}, Effect.TAGS_WRITE),
    ("pdk_pages", {"action": "list"}, Effect.READ),
    ("pdk_pages", {"action": "add", "url": "/x"}, Effect.SANDBOX_WRITE),
    ("pdk_promote", {"path": "PDK/X"}, Effect.SANDBOX_WRITE),
    ("pdk_promote", {"path": "PDK/X", "to_project": "target"}, Effect.TARGET_WRITE),
    ("pdk_inspect", {"path": "PDK/X", "act": "click root/Save"}, Effect.ACT),
    ("pdk_inspect", {"path": "PDK/X"}, Effect.LOOK),
    ("pdk_work", {"action": "read", "path": "a/view.json"}, Effect.READ),
    ("pdk_work", {"action": "edit", "path": "a/view.json"}, Effect.WORK_WRITE),
])
def test_effects_follow_the_arguments(name: str, args: dict[str, Any], expected: Effect) -> None:
    assert tools.effects(name, args) == {expected}


def test_the_eyes_on_a_promoted_view_are_a_look_at_the_target(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(sync.WORK_ENV, str(tmp_path))
    promoted = tmp_path / "Plant" / "Main"
    promoted.mkdir(parents=True)
    assert tools.effects("pdk_screenshot", {"path": str(promoted)}, "Plant") == {Effect.LOOK_TARGET}
    assert tools.effects("pdk_screenshot", {"path": str(promoted)}, "Other") == {Effect.LOOK}
    assert tools.effects("pdk_nonsense", {}) is None


def test_carries_code_finds_scripts_events_and_code_bindings() -> None:
    assert not tools.carries_code([{"op": "set", "prop": "props.text", "value": "Hola"}])
    assert tools.carries_code({"events": {"dom": {"onClick": {}}}})
    assert tools.carries_code({"propConfig": {"props.text": {"binding": {"type": "expr", "config": {
        "expression": "runScript('x')"}}}}})
    assert tools.carries_code({"propConfig": {"props.data": {"binding": {"type": "query"}}}})
    assert tools.carries_code({"binding": {"transforms": [{"type": "script", "code": "return value"}]}})


# --- the signed state -----------------------------------------------------------------------------


def _write(folder: Path, mode: str = NORMAL, grant: Grant | None = None, key: bytes = KEY) -> None:
    assert gate.write_state(folder, State(mode, str(folder / "work"), None, grant), key)


def test_a_state_signed_with_another_key_or_edited_reads_as_none(tmp_path: Path) -> None:
    _write(tmp_path, ULTRA)
    assert gate.read_state(tmp_path, KEY).mode == ULTRA
    assert gate.read_state(tmp_path, b"another key") is None and gate.read_state(tmp_path, None) is None
    document = json.loads((tmp_path / gate.STATE_FILE).read_text(encoding="utf-8"))
    document["payload"]["grant"] = {"plan": "mine", "targets": ["view:X"], "expires": NOW * 10}
    (tmp_path / gate.STATE_FILE).write_text(json.dumps(document), encoding="utf-8")  # the agent writing its grant
    assert gate.read_state(tmp_path, KEY) is None


def _gate(folder: Path, target: str | None = "Plant") -> Gate:
    return Gate(folder, KEY, target=lambda: target, clock=lambda: NOW)


def _code(refusal: str | None) -> str | None:
    return None if refusal is None else refusal.split("\n")[1].split()[1]


# --- the rules ------------------------------------------------------------------------------------


def test_without_a_readable_state_only_reading_and_looking_run(tmp_path: Path) -> None:
    session = _gate(tmp_path)
    assert session.check("pdk_validate", {}) is None and session.check("pdk_inspect", {"path": "PDK/X"}) is None
    assert _code(session.check("pdk_deploy", {"path": "PDK/X"})) == "T005"
    assert _code(Gate(tmp_path, None).check("pdk_deploy", {"path": "PDK/X"})) == "T005"  # no key: no trust


def test_normal_mode_reads_looks_and_edits_props_of_the_open_view(tmp_path: Path, monkeypatch) -> None:
    _write(tmp_path, NORMAL)
    session = _gate(tmp_path)
    def designer_edit(args: dict[str, Any], target: str | None) -> frozenset[Effect]:
        return frozenset({Effect.DESIGNER_CODE if tools.carries_code(args.get("ops")) else Effect.DESIGNER_EDIT})

    monkeypatch.setitem(tools.TOOLS, "pdk_designer_edit", designer_edit)
    assert session.check("pdk_designer_edit", {"ops": [{"op": "set", "prop": "props.text", "value": "x"}]}) is None
    refused = session.check("pdk_designer_edit", {"ops": [{"op": "add", "components": [{"events": {}}]}]})
    assert _code(refused) == "T002" and "code that runs on the gateway" in refused
    assert _code(session.check("pdk_deploy", {"path": "PDK/X"})) == "T002"
    assert _code(session.check("pdk_plan", {"summary": "x"})) == "T002"
    assert _code(session.check("pdk_inspect", {"path": "PDK/X", "act": "click root/A"})) == "T002"
    assert _code(session.check("pdk_whatever", {})) == "T001"


def test_ultra_waits_for_the_plan_then_holds_the_agent_to_it(tmp_path: Path, monkeypatch) -> None:
    _write(tmp_path, ULTRA)
    session = _gate(tmp_path)
    assert session.check("pdk_plan", {"summary": "a screen"}) is None
    assert _code(session.check("pdk_deploy", {"path": "PDK/X"})) == "T003"
    _write(tmp_path, ULTRA, Grant("p1", frozenset({"view:Main/Overview"}), NOW + 60))
    assert session.check("pdk_deploy", {"path": "PDK/X"}) is None
    assert session.check("pdk_tags", {"action": "write", "path": "X", "value": "1"}) is None
    monkeypatch.setattr(gate, "promote_destination", lambda args: args["to"])
    assert session.check("pdk_promote", {"path": "PDK/X", "to": "view:Main/Overview", "to_project": "target"}) is None
    refused = session.check("pdk_promote", {"path": "PDK/X", "to": "view:Main/Other", "to_project": "target"})
    assert _code(refused) == "T004" and "view:Main/Other" in refused


def test_a_grant_is_a_lease(tmp_path: Path) -> None:
    _write(tmp_path, ULTRA, Grant("p1", frozenset(), NOW - 1))
    assert _code(_gate(tmp_path).check("pdk_deploy", {"path": "PDK/X"})) == "T003"


def test_promote_destination_names_what_the_promote_writes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(sync.WORK_ENV, str(tmp_path))
    folder = tmp_path / "sb" / "PDK" / "Main"
    folder.mkdir(parents=True)
    assert gate.promote_destination({"path": str(folder)}) == "view:Main"
    assert gate.promote_destination({"path": str(folder), "to": "/Screens/Main/"}) == "view:Screens/Main"
    style = tmp_path / "sb" / "_style-classes" / "PDK" / "Card"
    style.mkdir(parents=True)
    assert gate.promote_destination({"path": str(style)}) == "style-class:Card"
    assert gate.promote_destination({"path": str(tmp_path / "sb" / "_stylesheet")}) == "stylesheet"
    assert gate.promote_destination({}) is None


def test_the_server_refuses_through_the_gate(tmp_path: Path, monkeypatch) -> None:
    _write(tmp_path, NORMAL)
    monkeypatch.setattr(mcp_server.server, "gate", _gate(tmp_path))
    result = asyncio.run(mcp_server.server.call_tool("pdk_deploy", {"path": "PDK/X"}))
    assert result.is_error and "T002" in result.content[0].text


# --- pdk_work -------------------------------------------------------------------------------------


@pytest.mark.parametrize("raw", ["../outside/view.json", "sb/../../view.json", "sb/.claude/view.json",
                                 "sb/PDK/X/view.json:stream", "sb/PDK/NUL/view.json", "sb/PDK/X./view.json",
                                 "sb/PDK/X/settings.json", "sb/PDK/X/notes.md"])
def test_pdk_work_stays_in_the_work_folder_and_on_resource_files(tmp_path: Path, raw: str) -> None:
    assert "T006" in work.write(tmp_path, raw, "{}", None)
    assert not (tmp_path.parent / "outside").exists()


def test_pdk_work_refuses_an_absolute_path_outside(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-other" / "view.json"
    assert "T006" in work.read(tmp_path / "work", str(outside))


def test_pdk_work_writes_validates_edits_and_lists(tmp_path: Path, bundle) -> None:
    raw = "sb/PDK/Card/view.json"
    data = view(label("Title", props={"text": "Hola", "textt": 1}))
    written = work.write(tmp_path, raw, json.dumps(data, indent=2), bundle)
    assert written.startswith("wrote sb/PDK/Card/view.json") and "P001" in written
    assert (tmp_path / "sb" / "PDK" / "Card" / "view.json").read_bytes().count(b"\r\n") == 0
    fixed = work.edit(tmp_path, raw, [{"old": '"textt": 1', "new": '"textStyle": {}'}], bundle)
    assert "P001" not in fixed
    assert "occurs 0 times" in work.edit(tmp_path, raw, [{"old": "nope", "new": "x"}], bundle)
    assert work.listing(tmp_path, None) == "sb/PDK/Card/view.json"
    assert json.loads(work.read(tmp_path, raw))["root"]["children"][0]["props"]["text"] == "Hola"
    assert "read-only" in work.write(tmp_path, "sb/PDK/Card/view.gateway.json", "{}", bundle)


# --- pdk_plan -------------------------------------------------------------------------------------


class _PlanGateway:
    def health(self) -> dict[str, Any]:
        return {"sandbox": "sb", "target": "Plant"}

    def resource(self, project: str, type_key: str, path: str) -> dict[str, Any]:
        if path == "Main/Overview":
            return {"signature": "abc", "folder": False}
        raise GatewayError("G007", "not found", status=404)


def test_pdk_plan_saves_the_plan_with_what_the_gateway_says(tmp_path: Path) -> None:
    text = plan.submit(tmp_path, _PlanGateway(), summary="- Overview", attention="Nothing.",
                       targets={"views": ["Main/Overview", "Main/Detail"], "style_classes": ["Card"],
                                "tags": ["Pump/Speed"]}, checks="inspect with expect")
    saved = json.loads((tmp_path / gate.PLAN_FILE).read_text(encoding="utf-8"))
    assert saved["targets"] == ["view:Main/Overview", "view:Main/Detail", "style-class:Card"]
    assert "view:Main/Overview in Plant: exists: the promote overwrites it" in saved["computed"][0]
    assert saved["computed"][1].endswith(": new") and "tags cannot be put in the target" in saved["computed"][3]
    assert text.startswith(f"plan {saved['id']} submitted: end your turn now")


# --- pdk_guide ------------------------------------------------------------------------------------


def test_pdk_guide_serves_the_skill() -> None:
    assert "perspective-views" in mcp_server.pdk_guide() or "pdk_validate" in mcp_server.pdk_guide()
    assert mcp_server.pdk_guide("snippets").startswith("snippets (pdk_guide section=<name>): ")
    assert json.loads(mcp_server.pdk_guide("table"))["root"]
    assert "no section" in mcp_server.pdk_guide("nonsense")

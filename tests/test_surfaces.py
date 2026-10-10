"""CLI, catalog, hook, snippets and the MCP server over a real stdio connection."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import anyio
import pytest
from mcp import Client, StdioServerParameters

from pdk import REPO_ROOT
from pdk.catalog import TOPICS, catalog
from pdk.cli import main
from pdk.diagnostics import DEFAULT_BUDGET, estimate_tokens
from pdk.hook import run_hook
from pdk.validate import validate_paths
from tests.helpers import label, view

SNIPPETS = REPO_ROOT / ".claude" / "skills" / "perspective-views" / "snippets"
BROKEN = view(label(props={"textt": "x"}))


def _write(tmp_path: Path, data: dict, name: str = "view.json") -> Path:
    path = tmp_path / "Bench" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_snippets_are_clean(bundle) -> None:
    results = validate_paths(SNIPPETS, bundle)
    assert len(results) >= 8
    for result in results:
        assert [d for d in result.diagnostics if d["severity"] != "info"] == [], result.path.name


@pytest.mark.parametrize("kwargs", [{}, {"type": "ia.display.table"}, {"type": "table", "prop": "columns[]"},
                                    {"search": "tank"}, *({"topic": t} for t in TOPICS)])
def test_catalog_answers_fit_the_budget(bundle, kwargs) -> None:
    text = catalog(bundle, **kwargs)
    assert text and estimate_tokens(text) <= DEFAULT_BUDGET


def test_catalog_finds_by_short_name_and_suggests(bundle) -> None:
    assert catalog(bundle, type="label").startswith("ia.display.label")
    assert "ia.display.label" in catalog(bundle, type="ia.display.lable")
    assert catalog(bundle, type="numeric entry").startswith("ia.input.numeric-entry-field")  # unique start of a name
    assert catalog(bundle, type="alarm").startswith("Unknown component type")  # ambiguous: status or journal table


def test_cli_exit_codes(bundle, tmp_path, capsys) -> None:
    assert main(["validate", str(_write(tmp_path, view(label())))]) == 0
    assert main(["validate", str(_write(tmp_path, BROKEN, "broken.view.json"))]) == 1
    assert "P001" in capsys.readouterr().out
    assert main(["validate", str(tmp_path / "missing.json")]) == 2
    assert main(["catalog", "label"]) == 0


def test_hook_reports_only_for_broken_views(bundle, tmp_path) -> None:
    broken = _write(tmp_path, BROKEN)
    code, message = run_hook(json.dumps({"tool_name": "Write", "tool_input": {"file_path": str(broken)}}))
    assert code == 2 and "P001" in message and "did you mean 'text'?" in message
    clean = _write(tmp_path, view(label()), "clean.view.json")
    assert run_hook(json.dumps({"tool_input": {"file_path": str(clean)}})) == (0, "")
    assert run_hook(json.dumps({"tool_input": {"file_path": str(tmp_path / "notes.md")}})) == (0, "")
    assert run_hook("not json") == (0, "")


def test_mcp_server_over_stdio(bundle, tmp_path) -> None:
    broken = _write(tmp_path, BROKEN)

    async def scenario() -> tuple[set[str], str, str]:
        params = StdioServerParameters(command=sys.executable, args=["-m", "pdk.mcp_server"], cwd=str(REPO_ROOT),
                                        env=dict(os.environ))
        async with Client(params) as client:
            tools = {tool.name for tool in (await client.list_tools()).tools}
            validated = await client.call_tool("pdk_validate", {"path": str(broken)})
            looked_up = await client.call_tool("pdk_catalog", {"type": "ia.display.label"})
            return tools, validated.content[0].text, looked_up.content[0].text

    tools, validated, looked_up = anyio.run(scenario)
    assert {"pdk_validate", "pdk_catalog", "pdk_pull", "pdk_deploy", "pdk_promote", "pdk_delete"} <= tools
    assert "P001" in validated and "did you mean 'text'?" in validated
    assert looked_up.startswith("ia.display.label")

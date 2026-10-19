from __future__ import annotations

import asyncio

from pdk import mcp_server


def _tools() -> dict:
    return {tool.name: tool for tool in asyncio.run(mcp_server.server.list_tools())}


def test_preview_tools_are_not_declared_read_only():
    tools = _tools()
    for name in ("pdk_open",):
        assert tools[name].annotations.read_only_hint is False, name
        assert tools[name].annotations.idempotent_hint is True, name


def test_tools_that_act_are_declared_destructive_not_read_only():
    for name in ("pdk_inspect", "pdk_screenshot"):  # act runs the view's event scripts
        annotations = _tools()[name].annotations
        assert annotations.read_only_hint is False, name
        assert annotations.destructive_hint is True, name
        assert annotations.idempotent_hint is False, name


def test_pdk_inspect_description_warns_about_act_scripts():
    assert "act runs the view's own event scripts on the gateway" in _tools()["pdk_inspect"].description


def test_every_tool_is_listed_where_agents_and_users_read_the_tool_set() -> None:
    from pdk import REPO_ROOT

    names = set(_tools())
    for rel in ("CLAUDE.md", "pdk/templates/CLAUDE.md", "docs/reference/mcp.rst"):
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        missing = sorted(name for name in names if f"`{name}`" not in text)
        assert not missing, f"{rel} does not list {missing}"

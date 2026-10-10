from __future__ import annotations

import asyncio

from pdk import mcp_server


def _tools() -> dict:
    return {tool.name: tool for tool in asyncio.run(mcp_server.server.list_tools())}


def test_preview_tools_are_not_declared_read_only():
    tools = _tools()
    for name in ("pdk_open", "pdk_screenshot"):
        assert tools[name].annotations.read_only_hint is False, name
        assert tools[name].annotations.idempotent_hint is True, name


def test_pdk_inspect_is_declared_destructive_not_read_only():
    annotations = _tools()["pdk_inspect"].annotations
    assert annotations.read_only_hint is False
    assert annotations.destructive_hint is True
    assert annotations.idempotent_hint is False


def test_pdk_inspect_description_warns_about_act_scripts():
    assert "act runs the view's own event scripts on the gateway" in _tools()["pdk_inspect"].description

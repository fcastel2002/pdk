from __future__ import annotations

import json

import pytest

from pdk import REPO_ROOT, __version__, init, paths


def test_init_writes_the_skill_agents_mcp_hook_and_claude_md(tmp_path) -> None:
    result = init.init(tmp_path)
    assert not result.problems
    skill = tmp_path / ".claude" / "skills" / "perspective-views"
    assert (skill / "SKILL.md").is_file()
    assert (skill / init.VERSION_FILE).read_text().strip() == __version__
    for name in init.AGENTS:
        assert (tmp_path / ".claude" / "agents" / name).is_file()
    server = json.loads((tmp_path / ".mcp.json").read_text())["mcpServers"]["pdk"]
    assert (server["args"] or [server["command"]])[-1] == "pdk-mcp"
    hooks = json.loads((tmp_path / ".claude" / "settings.json").read_text())["hooks"]["PostToolUse"]
    assert hooks[0]["hooks"][0]["command"].endswith("pdk hook")
    assert init.BEGIN in (tmp_path / "CLAUDE.md").read_text()


def test_init_merges_and_runs_again_without_duplicates(tmp_path) -> None:
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}))
    (tmp_path / ".claude").mkdir()
    other_hook = {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo hi"}]}
    (tmp_path / ".claude" / "settings.json").write_text(json.dumps({"hooks": {"PostToolUse": [other_hook]}}))
    (tmp_path / "CLAUDE.md").write_text("# Mine\n\nkeep this\n")
    init.init(tmp_path)
    init.init(tmp_path)
    assert set(json.loads((tmp_path / ".mcp.json").read_text())["mcpServers"]) == {"other", "pdk"}
    hooks = json.loads((tmp_path / ".claude" / "settings.json").read_text())["hooks"]["PostToolUse"]
    assert hooks[0] == other_hook and len(hooks) == 2
    text = (tmp_path / "CLAUDE.md").read_text()
    assert text.startswith("# Mine\n\nkeep this\n") and text.count(init.BEGIN) == 1


def test_a_broken_settings_file_is_left_alone(tmp_path) -> None:
    (tmp_path / ".mcp.json").write_text("{not json")
    result = init.init(tmp_path)
    assert (tmp_path / ".mcp.json").read_text() == "{not json"
    assert any(".mcp.json" in problem for problem in result.problems)


def test_refresh_only_where_init_ran_and_the_version_changed(tmp_path) -> None:
    assert not init.refresh_if_stale(tmp_path)
    init.init(tmp_path)
    assert not init.refresh_if_stale(tmp_path)
    (tmp_path / ".claude" / "skills" / "perspective-views" / init.VERSION_FILE).write_text("0.0.1\n")
    assert init.refresh_if_stale(tmp_path)


def test_installed_text_calls_pdk_directly(monkeypatch) -> None:
    monkeypatch.setattr(paths, "CHECKOUT", False)
    assert init._text("run `uv run pdk inspect X`") == "run `pdk inspect X`"
    assert init._command("pdk-mcp") == ["pdk-mcp"]


@pytest.mark.skipif(not paths.CHECKOUT, reason="only from a checkout")
def test_init_refuses_the_checkout_itself() -> None:
    with pytest.raises(ValueError):
        init.init(REPO_ROOT)

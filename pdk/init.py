"""`pdk init`: set up a folder so Claude Code works there with PDK, with no clone of the PDK repository.

It writes what an agent needs and nothing else: the `perspective-views` skill and the user-facing subagents under
`.claude/`, the `pdk` MCP server in `.mcp.json`, the hook that validates every `view.json` in `.claude/settings.json`,
and a PDK section in `CLAUDE.md`. Files that belong to the folder are merged, not replaced: other MCP servers, other
hooks and the rest of `CLAUDE.md` stay. Run it again after upgrading PDK to refresh the skill and the subagents;
the MCP server refreshes them (and the CLAUDE.md section) by itself, and leaves `.mcp.json` and the hook alone.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pdk import PACKAGE_DIR, REPO_ROOT, __version__, paths

AGENTS = ("pdk-reviewer.md", "pdk-log-reader.md")
BEGIN = "<!-- pdk:begin (written by `pdk init`; edit outside these markers) -->"
END = "<!-- pdk:end -->"
VERSION_FILE = ".pdk-version"
HOOK_MATCHER = "Write|Edit|MultiEdit"


@dataclass
class InitResult:
    folder: Path
    written: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def _command(*args: str) -> list[str]:
    """How the folder calls PDK: the installed commands, or `uv run` against the checkout this runs from."""
    if paths.CHECKOUT:
        return ["uv", "run", "--quiet", "--project", str(REPO_ROOT), *args]
    return list(args)


def _text(source: str) -> str:
    """The skill and the subagents are written for the checkout (`uv run pdk ...`); a project folder calls `pdk`."""
    return source if paths.CHECKOUT else source.replace("uv run pdk", "pdk").replace("uv run playwright", "playwright")


def _copy_tree(source: Path, target: Path) -> None:
    if target.exists():
        shutil.rmtree(target)
    for path in sorted(source.rglob("*")):
        if path.is_dir() or "__pycache__" in path.parts:
            continue
        out = target / path.relative_to(source)
        out.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix == ".md":
            out.write_text(_text(path.read_text(encoding="utf-8")), encoding="utf-8", newline="\n")
        else:
            shutil.copyfile(path, out)


def _read_json(path: Path, result: InitResult) -> dict[str, Any] | None:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        result.problems.append(f"{path.name} is not valid JSON ({exc}); left as it is")
        return None
    if not isinstance(data, dict):
        result.problems.append(f"{path.name} is not a JSON object; left as it is")
        return None
    return data


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")


def _mcp(folder: Path, result: InitResult) -> None:
    path = folder / ".mcp.json"
    data = _read_json(path, result)
    if data is None:
        return
    command = _command("pdk-mcp")
    servers = data.setdefault("mcpServers", {})
    entry = servers.get("pdk") if isinstance(servers.get("pdk"), dict) else {}
    servers["pdk"] = {**entry, "type": "stdio", "command": command[0], "args": command[1:]}  # keeps env and the rest
    _write_json(path, data)
    result.written.append(".mcp.json")


def _hook(folder: Path, result: InitResult) -> None:
    path = folder / ".claude" / "settings.json"
    data = _read_json(path, result)
    if data is None:
        return
    command = " ".join(f'"{part}"' if " " in part else part for part in _command("pdk", "hook"))
    entries = data.setdefault("hooks", {}).setdefault("PostToolUse", [])
    ours = [hook for entry in entries for hook in entry.get("hooks", [])
            if str(hook.get("command", "")).endswith("pdk hook")]
    timeout = next((hook["timeout"] for hook in ours if isinstance(hook.get("timeout"), int)), 60)
    entries[:] = [entry for entry in entries
                  if not any(str(hook.get("command", "")).endswith("pdk hook") for hook in entry.get("hooks", []))]
    entries.append({"matcher": HOOK_MATCHER, "hooks": [{"type": "command", "command": command, "timeout": timeout}]})
    _write_json(path, data)
    result.written.append(".claude/settings.json")


def _claude_md(folder: Path) -> None:
    section = f"{BEGIN}\n{(PACKAGE_DIR / 'templates' / 'CLAUDE.md').read_text(encoding='utf-8').strip()}\n{END}"
    path = folder / "CLAUDE.md"
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    start, end = text.find(BEGIN), text.find(END)
    if start >= 0 and end > start:
        text = text[:start] + section + text[end + len(END):]
    else:
        text = (text.rstrip() + "\n\n" if text.strip() else "") + section + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")


def init(folder: Path) -> InitResult:
    folder = folder.resolve()
    if paths.CHECKOUT and folder == REPO_ROOT.resolve():
        raise ValueError("this is the PDK checkout itself: run `pdk init` in the folder you work on views in")
    folder.mkdir(parents=True, exist_ok=True)
    result = InitResult(folder)
    _assets(folder, result)
    _mcp(folder, result)
    _hook(folder, result)
    return result


def _assets(folder: Path, result: InitResult) -> None:
    """What ships with PDK and changes with it: the skill, the subagents and the CLAUDE.md section."""
    skill = folder / ".claude" / "skills" / "perspective-views"
    _copy_tree(paths.skill_dir(), skill)
    (skill / VERSION_FILE).write_text(__version__ + "\n", encoding="utf-8")
    result.written.append(".claude/skills/perspective-views/")
    for name in AGENTS:
        target = folder / ".claude" / "agents" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_text((paths.agents_dir() / name).read_text(encoding="utf-8")), encoding="utf-8",
                          newline="\n")
        result.written.append(f".claude/agents/{name}")
    _claude_md(folder)
    result.written.append("CLAUDE.md (PDK section)")


def refresh_if_stale(folder: Path) -> bool:
    """After an upgrade, bring the skill, the subagents and the CLAUDE.md section of a folder that `pdk init` set up
    to this version. Only where init ran before; `.mcp.json` and the hook are the user's to change."""
    marker = folder / ".claude" / "skills" / "perspective-views" / VERSION_FILE
    if paths.CHECKOUT and folder.resolve() == REPO_ROOT.resolve():
        return False  # the checkout's own skill is the source, never a copy to refresh
    try:
        if not marker.is_file() or marker.read_text(encoding="utf-8").strip() == __version__:
            return False
        _assets(folder.resolve(), InitResult(folder))
    except OSError:
        return False
    return True

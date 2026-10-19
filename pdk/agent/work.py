"""`pdk_work`: the agent's files in an agent session, confined to the work folder and to PDK's resource files.

In a session the runtime's own file tools are off, so this is how the agent reads and writes the views, style
classes, scripts and stylesheet it deploys. Every path is resolved (links and junctions followed) and must stay
under the work folder; hidden folders, Windows alternate streams, device names and names that end in a dot or a
space are refused. A written `view.json` comes back validated, the way the Claude Code hook reports it.
"""

from __future__ import annotations

import re
from pathlib import Path

from pdk.bundle import Bundle
from pdk.diagnostics import DEFAULT_BUDGET, make, render

RESOURCE_FILES = frozenset({"view.json", "style.json", "code.py", "stylesheet.css"})
READABLE_FILES = RESOURCE_FILES | {"view.gateway.json", "style.gateway.json", "code.gateway.py",
                                   "stylesheet.gateway.css"}  # the gateway's version after a G008 conflict
MAX_READ_CHARS = 400_000
MAX_LISTED = 200
_DEVICE = re.compile(r"^(con|prn|aux|nul|com[0-9]|lpt[0-9])(\..*)?$", re.IGNORECASE)


class OutsideWork(ValueError):
    pass


def _refused(message: str) -> str:
    return render([make("T006", "error", "", "", message,
                        "use a path under the work folder, to a view.json, style.json, code.py or stylesheet.css "
                        "(pdk_work action=list shows what is there)")], title="pdk_work")


def resolve(root: Path, raw: str, *, file: bool) -> Path:
    """The path under `root` that `raw` names (relative to root, or absolute inside it). OutsideWork otherwise."""
    if not raw or "\x00" in raw:
        raise OutsideWork("an empty path")
    given = Path(raw)
    parts = given.parts[1:] if given.is_absolute() else given.parts
    for part in parts:
        name = part.rstrip("\\/")
        if not name:
            continue
        if name.startswith(".") or ":" in name or name != name.rstrip(" .") or _DEVICE.match(name):
            raise OutsideWork(f"{raw}: {name!r} is not a name pdk_work takes")
    base = root.resolve()
    location = (given if given.is_absolute() else base / given).resolve()
    if not location.is_relative_to(base):
        raise OutsideWork(f"{raw} is outside the work folder {base}")
    if file and location.name not in READABLE_FILES:
        raise OutsideWork(f"{raw}: pdk_work handles {', '.join(sorted(RESOURCE_FILES))} only")
    return location


def read(root: Path, raw: str) -> str:
    try:
        location = resolve(root, raw, file=True)
    except OutsideWork as exc:
        return _refused(str(exc))
    if not location.is_file():
        return f"pdk_work: {raw} does not exist (pdk_pull downloads a view, pdk_designer_copy the open one)"
    text = location.read_text(encoding="utf-8")
    if len(text) > MAX_READ_CHARS:
        return text[:MAX_READ_CHARS] + f"\n... cut at {MAX_READ_CHARS} characters"
    return text


def listing(root: Path, raw: str | None) -> str:
    try:
        folder = resolve(root, raw, file=False) if raw else root.resolve()
    except OutsideWork as exc:
        return _refused(str(exc))
    if not folder.is_dir():
        return f"pdk_work: {raw or folder} is not a folder"
    base = root.resolve()
    found = sorted(p for p in folder.rglob("*") if p.is_file() and p.name in READABLE_FILES
                   and not any(part.startswith(".") for part in p.relative_to(base).parts))
    lines = [p.relative_to(base).as_posix() for p in found[:MAX_LISTED]]
    if len(found) > MAX_LISTED:
        lines.append(f"... +{len(found) - MAX_LISTED} more; list a subfolder")
    if not lines:
        return f"pdk_work: no resource files under {folder.relative_to(base).as_posix() or '.'}"
    return "\n".join(lines)


def write(root: Path, raw: str, content: str, bundle: Bundle | None, budget: int = DEFAULT_BUDGET) -> str:
    try:
        location = resolve(root, raw, file=True)
    except OutsideWork as exc:
        return _refused(str(exc))
    if location.name not in RESOURCE_FILES:
        return _refused(f"{raw}: the gateway's copy after a conflict is read-only; merge into "
                        f"{location.name.replace('.gateway', '')}")
    location.parent.mkdir(parents=True, exist_ok=True)
    with open(location, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)
    shown = location.relative_to(root.resolve()).as_posix()
    if location.name != "view.json" or bundle is None:
        return f"wrote {shown} ({len(content)} characters); pdk_deploy checks it"
    from pdk.validate import validate_file

    result = validate_file(location, bundle)
    return f"wrote {shown}\n" + render(result.diagnostics, title=shown, budget=budget)


def edit(root: Path, raw: str, edits: list[dict[str, str]], bundle: Bundle | None,
         budget: int = DEFAULT_BUDGET) -> str:
    """Exact replacements, in order: each `old` must occur once in the file as it is at that point."""
    try:
        location = resolve(root, raw, file=True)
    except OutsideWork as exc:
        return _refused(str(exc))
    if not location.is_file():
        return f"pdk_work: {raw} does not exist; write it first"
    text = location.read_text(encoding="utf-8")
    for number, change in enumerate(edits or [], 1):
        old, new = change.get("old"), change.get("new")
        if not isinstance(old, str) or not isinstance(new, str) or not old:
            return f"pdk_work: edit {number} needs 'old' (non-empty text) and 'new'; nothing was written"
        count = text.count(old)
        if count != 1:
            return (f"pdk_work: edit {number}: 'old' occurs {count} times in {raw}; it must occur exactly once "
                    "(add context around it); nothing was written")
        text = text.replace(old, new, 1)
    if not edits:
        return "pdk_work: no edits given; nothing was written"
    return write(root, raw, text, bundle, budget)

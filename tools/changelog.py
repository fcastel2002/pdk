"""CHANGELOG.md for releases: what is pending, which version comes next, and the release notes.

    python tools/changelog.py pending            # the [Unreleased] section (empty if nothing changed)
    python tools/changelog.py next 0.2.0         # the suggested next version, from the kinds of change pending
    python tools/changelog.py roll 0.3.0 2026-11-02   # [Unreleased] becomes [0.3.0] - date, with a new empty one
    python tools/changelog.py notes 0.3.0        # that version's section, for the GitHub release

The suggestion follows Semantic Versioning on the headings of [Unreleased] (Keep a Changelog): "Removed" or a line
with "BREAKING" is a major change (a minor one before 1.0), "Added" or "Changed" a minor one, anything else a patch.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

CHANGELOG = Path(__file__).resolve().parents[1] / "CHANGELOG.md"
UNRELEASED = "## [Unreleased]"
_SECTION = re.compile(r"^## \[", re.MULTILINE)


def section(text: str, name: str) -> str:
    """The body of `## [name]...` up to the next `## [`, stripped."""
    match = re.search(rf"^## \[{re.escape(name)}\][^\n]*\n", text, re.MULTILINE)
    if not match:
        return ""
    rest = text[match.end():]
    following = _SECTION.search(rest)
    return (rest[:following.start()] if following else rest).strip()


def entries(body: str) -> int:
    return sum(1 for line in body.splitlines() if line.startswith("- "))


def next_version(current: str, pending: str) -> str | None:
    """None when nothing is pending."""
    if not entries(pending):
        return None
    major, minor, patch = (int(part) for part in re.findall(r"\d+", current)[:3])
    headings = set(re.findall(r"^### (\w+)", pending, re.MULTILINE))
    if "Removed" in headings or "BREAKING" in pending:
        return f"{major}.{minor + 1}.0" if major == 0 else f"{major + 1}.0.0"
    if headings & {"Added", "Changed"}:
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def roll(text: str, version: str, date: str) -> str:
    if UNRELEASED not in text:
        raise ValueError(f"no '{UNRELEASED}' heading")
    if re.search(rf"^## \[{re.escape(version)}\]", text, re.MULTILINE):
        raise ValueError(f"{version} is already in the changelog")
    return text.replace(UNRELEASED, f"{UNRELEASED}\n\n## [{version}] - {date}", 1)


def main(argv: list[str]) -> int:
    text = CHANGELOG.read_text(encoding="utf-8")
    command, *args = argv or ["pending"]
    if command == "pending":
        print(section(text, "Unreleased"))
    elif command == "next":
        print(next_version(args[0], section(text, "Unreleased")) or "")
    elif command == "notes":
        print(section(text, args[0].lstrip("v")))
    elif command == "roll":
        CHANGELOG.write_text(roll(text, args[0].lstrip("v"), args[1]), encoding="utf-8", newline="\n")
    else:
        print(__doc__, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

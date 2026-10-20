"""Says when a newer PDK is out, at most one network call a day, and that this is the final community edition.

The latest release comes from GitHub's public API (no token, nothing about this machine is sent) and is cached in
~/.pdk/update-check.json with the time of the check, so an offline machine pays the timeout once a day, not on every
command. `PDK_UPDATE_CHECK=0` (or `update_check = false` in ~/.pdk/config.toml) turns it off; it is also off in CI.

PDK 0.5.0 is the final community edition: the day's first command (the one that runs the daily check) also says so,
with where the maintained edition is. No call is added for it.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from pathlib import Path

from pdk import __version__, paths

REPOSITORY = "fcastel2002/pdk"
LATEST_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
RELEASES_URL = f"https://github.com/{REPOSITORY}/releases"
MAINTAINED_URL = "https://github.com/fcastel2002"
FROZEN = ("This is the final community edition of PDK (Apache-2.0): it gets no more updates or fixes. The maintained "
          f"edition (newer Ignition and Perspective, signed module, support) is commercial: {MAINTAINED_URL}")
INTERVAL_S = 24 * 3600
TIMEOUT_S = 1.5
ENV = "PDK_UPDATE_CHECK"


def enabled() -> bool:
    if os.environ.get("CI"):
        return False
    return os.environ.get(ENV, "1").strip().lower() not in ("0", "false", "no", "off")


def version_key(version: str) -> tuple[int, ...]:
    """`0.2.1` > `0.2.1.dev3+g1a2b3c4` > `0.2.0`: a version between releases sorts before the release it leads to."""
    match = re.match(r"v?(\d+)\.(\d+)\.(\d+)(.*)", version.strip())
    if not match:
        return (0, 0, 0, 0)
    return (*(int(part) for part in match.groups()[:3]), 0 if match.group(4) else 1)


def cache_file() -> Path:
    return paths.home() / "update-check.json"


def _fetch(timeout: float) -> str | None:
    request = urllib.request.Request(LATEST_URL, headers={"Accept": "application/vnd.github+json",
                                                          "User-Agent": f"pdk/{__version__}"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            tag = json.load(response).get("tag_name")
    except (OSError, ValueError):
        return None
    return str(tag).lstrip("v") if tag else None


def _due(now: float) -> bool:
    """True when the cached check is a day old or missing, so the next `latest()` checks again."""
    try:
        return now - float(json.loads(cache_file().read_text(encoding="utf-8")).get("checked", 0)) >= INTERVAL_S
    except (OSError, ValueError, AttributeError, TypeError):
        return True


def latest(now: float | None = None, timeout: float = TIMEOUT_S) -> str | None:
    """The newest released version: from the cache if it was checked in the last day, else from GitHub."""
    now = time.time() if now is None else now
    cache = cache_file()
    try:
        cached = json.loads(cache.read_text(encoding="utf-8"))
        if now - float(cached.get("checked", 0)) < INTERVAL_S:
            return cached.get("latest")
    except (OSError, ValueError, AttributeError):
        pass
    found = _fetch(timeout)
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"checked": now, "latest": found}), encoding="utf-8")
    except OSError:
        pass
    return found


def upgrade_command() -> str:
    if paths.CHECKOUT:
        return "git pull && uv sync   (in the PDK checkout)"
    return "uv tool upgrade pdk-perspective, then `pdk init` in each project folder"


def notice(current: str = __version__, now: float | None = None) -> str | None:
    """One line when a newer release exists; else, once a day (with the check), that this edition is frozen; else None
    (also when the check is off)."""
    if not enabled():
        return None
    now = time.time() if now is None else now
    first_today = _due(now)
    newest = latest(now)
    if newest and version_key(newest) > version_key(current):
        return f"PDK {newest} is available (this is {current}): {upgrade_command()}. What changed: {RELEASES_URL}"
    return FROZEN if first_today else None

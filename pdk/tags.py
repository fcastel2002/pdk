"""Simulated tags (F5 part B): memory tags under [provider]PDK/ of the development gateway.

The module imports Ignition's own tag export JSON (folders and memory tags only), writes values with a quality and
reads them back, and lists the alarm events of those tags. Paths without `[provider]` hang from PDK/:
`Sandbox/Setpoint` is `[default]PDK/Sandbox/Setpoint`. Reads take any path; writes only PDK/ (G013).
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from pdk.gateway.client import Gateway, GatewayError

FOLDER = "PDK"
LICENSE_EXCEEDED = "Bad_LicenseExceeded"
LICENSE_HINT = ("hint: the gateway is over its tag license (Maker runs 10,000 tags): tags left out stay "
                "Bad_LicenseExceeded and take no writes (Bad_Unsupported) until tags are removed and the gateway "
                "restarts.")
MAX_LINES = 60


def full_path(path: str) -> str:
    """`Sandbox/Setpoint` -> `PDK/Sandbox/Setpoint`; paths with [provider] or already under PDK/ stay as they are."""
    text = path.strip().strip("/")
    if text.startswith("[") or text.lower() == FOLDER.lower() or text.lower().startswith(FOLDER.lower() + "/"):
        return text
    return f"{FOLDER}/{text}"


def parse_value(raw: str) -> Any:
    """A written or expected value: JSON numbers, true/false and null; anything else is a string."""
    try:
        value = json.loads(raw)
    except ValueError:
        return raw
    return value if value is None or isinstance(value, (bool, int, float)) else raw


def matches(actual: Any, raw: str) -> bool:
    """A read value against the expected text: numbers within float precision, else equal as text."""
    expected = parse_value(raw)
    if isinstance(actual, bool) or isinstance(expected, bool):
        return isinstance(actual, bool) and isinstance(expected, bool) and actual == expected  # 1 is not true
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        return math.isclose(actual, expected, rel_tol=1e-6, abs_tol=1e-9)
    if actual is None or expected is None:
        return actual is None and expected is None
    return str(actual) == raw


def show(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def write(gateway: Gateway, path: str, raw: str | None, quality: str | None = None) -> dict[str, Any]:
    """Write one tag (raw None keeps its current value, to change only the quality); raises ValueError with the
    reason when the gateway does not take it."""
    target = full_path(path)
    if raw is None:
        [current] = gateway.read_tags([target])["results"]
        if current.get("quality") == "Bad_NotFound":
            raise ValueError(f"{target} does not exist")
        value = current.get("value")
    else:
        value = parse_value(raw)
    entry: dict[str, Any] = {"path": target, "value": value}
    if quality:
        entry["quality"] = quality
    [result] = gateway.write_tags([entry])["results"]
    if result.get("write") != "Good":
        why = ("it does not exist" if result.get("quality") == "Bad_NotFound"
               else f"the write returned {result.get('write')}")
        if result.get("quality") == LICENSE_EXCEEDED:
            why += " (the tag is Bad_LicenseExceeded: the gateway is over its tag license)"
        raise ValueError(f"{result.get('path', target)}: {why}")
    return result


class Probe:
    """Tag reads for expectations, cached for one inspect."""

    def __init__(self, gateway: Gateway) -> None:
        self.gateway = gateway
        self._values: dict[str, dict[str, Any]] = {}
        self._alarms: dict[str, list[dict[str, Any]]] = {}

    def read(self, path: str) -> dict[str, Any]:
        target = full_path(path)
        if target not in self._values:
            [self._values[target]] = self.gateway.read_tags([target])["results"]
        return self._values[target]

    def alarms(self, path: str) -> list[dict[str, Any]]:
        target = full_path(path)
        if target not in self._alarms:
            self._alarms[target] = self.gateway.alarms(target).get("alarms", [])
        return self._alarms[target]


def apply(gateway: Gateway, file: Path, to: str = FOLDER) -> str:
    """Import a tag export JSON file into `to` (under PDK/): tags are created, or overwritten with their declared
    values, and their alarm events acknowledged, which resets them between runs."""
    data = json.loads(file.read_text(encoding="utf-8"))
    result = gateway.import_tags(full_path(to), data)
    qualities = ", ".join(f"{name} {count}" for name, count in result.get("qualities", {}).items())
    lines = [f"{file.name} -> {result.get('path')}: {result.get('tags')} tags ({qualities})"]
    try:
        acked = gateway.acknowledge_alarms(result.get("path")).get("acknowledged", 0)
        if acked:
            lines.append(f"  {acked} alarm events acknowledged")
    except GatewayError as error:
        if error.code != "G002":
            raise
        lines.append("  alarm events not acknowledged: the module predates /alarms/ack (reinstall it)")
    nodes = gateway.tags(result.get("path"), depth=10).get("nodes", [])
    bad = [n for n in nodes if n.get("type") != "Folder" and n.get("quality") not in (None, "Good")]
    lines += [f"  {_node(n)}" for n in bad[:MAX_LINES]]
    if any(n.get("quality") == LICENSE_EXCEEDED for n in bad):
        lines.append(LICENSE_HINT)
    return "\n".join(lines)


def list_text(result: dict[str, Any]) -> str:
    nodes = result.get("nodes", [])
    lines = [f"{result.get('path')}: {len(nodes)} nodes" + (" (truncated)" if result.get("truncated") else "")]
    lines += [f"  {_node(n)}" for n in nodes[:MAX_LINES]]
    if len(nodes) > MAX_LINES:
        lines.append(f"  … {len(nodes) - MAX_LINES} more (narrow the path or the depth)")
    return _with_hint(lines, nodes)


def values_text(results: list[dict[str, Any]]) -> str:
    lines = []
    for entry in results:
        line = f"{entry.get('path')} = {show(entry.get('value'))} ({entry.get('quality')})"
        if entry.get("write") not in (None, "Good"):
            line += f" | write: {entry['write']}"
        if entry.get("message"):
            line += f" | {entry['message']}"
        lines.append(line)
    return _with_hint(lines, results)


def alarms_text(result: dict[str, Any]) -> str:
    events = result.get("alarms", [])
    lines = [f"{result.get('path')}: {len(events)} alarm events" + (" (truncated)" if result.get("truncated") else "")]
    for event in events[:MAX_LINES]:
        lines.append(f"  {event.get('tag')} / {event.get('name')}: {event.get('state')}, {event.get('priority')}, "
                     f"display {event.get('displayPath')!r}")
    return "\n".join(lines)


def _node(node: dict[str, Any]) -> str:
    if node.get("type") == "Folder":
        return f"{node.get('path')}/"
    return f"{node.get('path')} {node.get('dataType')} = {show(node.get('value'))} ({node.get('quality')})"


def _with_hint(lines: list[str], entries: list[dict[str, Any]]) -> str:
    if any(entry.get("quality") == LICENSE_EXCEEDED for entry in entries):
        lines.append(LICENSE_HINT)
    return "\n".join(lines)

"""Simulated tags (F5 part B): memory tags under [provider]PDK/ of the development gateway.

The module imports Ignition's own tag export JSON (folders, memory tags, UDT definitions under _types_/PDK/ with
memory members and instances of them), writes values with a quality and reads them back, lists the alarm events of
those tags, and stores synthetic history points for them. Paths without `[provider]` hang from PDK/:
`Sandbox/Setpoint` is `[default]PDK/Sandbox/Setpoint`; `_types_/PDK/...` is where UDT definitions go. Reads take
any path; writes only PDK/ and _types_/PDK/ (G013).
"""

from __future__ import annotations

import json
import math
import re
import time
from pathlib import Path
from typing import Any

from pdk.gateway.client import Gateway, GatewayError

FOLDER = "PDK"
TYPES = "_types_"
UNITS_MS = {"ms": 1, "s": 1000, "m": 60_000, "h": 3_600_000, "d": 86_400_000}
LICENSE_EXCEEDED = "Bad_LicenseExceeded"
LICENSE_HINT = ("hint: the gateway is over its tag license (Maker runs 10,000 tags): tags left out stay "
                "Bad_LicenseExceeded and take no writes (Bad_Unsupported) until tags are removed and the gateway "
                "restarts.")
MAX_LINES = 60


def full_path(path: str) -> str:
    """`Sandbox/Setpoint` -> `PDK/Sandbox/Setpoint`; paths with [provider] or already under PDK/ stay as they are."""
    text = path.strip().strip("/")
    if text.startswith("[") or text.lower() in (FOLDER.lower(), TYPES) \
            or text.lower().startswith((FOLDER.lower() + "/", TYPES + "/")):
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
    if full_path(to).lower().startswith(TYPES):
        return "\n".join(lines)  # definitions: no values, no alarms
    try:
        acked = gateway.acknowledge_alarms(result.get("path")).get("acknowledged", 0)
        if acked:
            lines.append(f"  {acked} alarm events acknowledged")
    except GatewayError as error:
        if error.code != "G002":
            raise
        lines.append("  alarm events not acknowledged: the module predates /alarms/ack (reinstall it)")
    nodes = gateway.tags(result.get("path"), depth=10).get("nodes", [])
    bad = [n for n in nodes if n.get("type") == "AtomicTag" and n.get("quality") not in (None, "Good")]
    lines += [f"  {_node(n)}" for n in bad[:MAX_LINES]]
    if any(n.get("quality") == LICENSE_EXCEEDED for n in bad):
        lines.append(LICENSE_HINT)
    return "\n".join(lines)


def duration_ms(text: str) -> int:
    """`500ms`, `30s`, `1m`, `2h`, `1d` (a bare number is seconds) in milliseconds."""
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(ms|s|m|h|d)?\s*", text or "")
    if not match:
        raise ValueError(f"{text!r} is not a duration like 30s, 1m, 2h or 1d")
    return int(float(match.group(1)) * UNITS_MS[match.group(2) or "s"])


def history(gateway: Gateway, path: str, values: list[str], *, every: str = "1m", provider: str | None = None,
            quality: str | None = None, end_ms: int | None = None) -> str:
    """Store `values` as the history of a tag under PDK/, one every `every`, the last one now (or at `end_ms`), in
    `provider` (the gateway's only history provider when not given)."""
    providers = gateway.history_providers()
    if provider is None:
        if len(providers) != 1:
            raise ValueError(f"pass a history provider: the gateway has {', '.join(providers) or 'none'}")
        provider = providers[0]
    if not values:
        raise ValueError("no values to store")
    empty = [str(i + 1) for i, raw in enumerate(values) if not raw.strip()]
    if empty:
        raise ValueError(f"value(s) {', '.join(empty)} are empty: write them as numbers, true/false or text")
    step = duration_ms(every)
    end = end_ms if end_ms is not None else int(time.time() * 1000)
    target = full_path(path)
    points = []
    for i, raw in enumerate(values):
        point: dict[str, Any] = {"path": target, "value": parse_value(raw.strip()),
                                 "timestamp": end - (len(values) - 1 - i) * step}
        if quality:
            point["quality"] = quality
        points.append(point)
    result = gateway.store_history(provider, points)
    first, last = points[0]["timestamp"], points[-1]["timestamp"]
    return (f"stored {result.get('stored', len(points))} point(s) of [{result.get('tagProvider', '?')}]{target} in "
            f"{provider}, every {every} from {_iso(first)} to {_iso(last)}"
            + (f" ({result['result']})" if result.get("result") not in (None, "", "[]") else ""))


def _iso(ms: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ms / 1000))


def list_text(result: dict[str, Any]) -> str:
    nodes = [n for n in result.get("nodes", []) if n.get("type") != "Node"]  # a UDT's Parameters, not a tag
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
    if node.get("type") in ("UdtInstance", "UdtType"):
        return f"{node.get('path')}/ ({node['type']})"
    return f"{node.get('path')} {node.get('dataType')} = {show(node.get('value'))} ({node.get('quality')})"


def _with_hint(lines: list[str], entries: list[dict[str, Any]]) -> str:
    if any(entry.get("quality") == LICENSE_EXCEEDED for entry in entries):
        lines.append(LICENSE_HINT)
    return "\n".join(lines)

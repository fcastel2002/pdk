"""Diagnostics: the stable code table, construction, ordering and budgeted rendering."""

from __future__ import annotations

import re
from collections import Counter
from typing import Literal, NotRequired, TypedDict

Severity = Literal["error", "warning", "info"]
SEVERITY_RANK: dict[str, int] = {"error": 0, "warning": 1, "info": 2}
CHARS_PER_TOKEN = 3.5
DEFAULT_BUDGET = 1500


class Diagnostic(TypedDict):
    code: str
    severity: Severity
    pointer: str  # RFC 6901 pointer into the view.json
    component: str  # name path, e.g. root/Panel/Speed ("" for view-level)
    message: str
    hint: str
    detail: NotRequired[str]  # extra context, rendered only when focused


# Stable codes. Families: S structure, P props/meta/position, B propConfig/bindings,
# E events/scripts, R references, HMI design lint; and from the eyes (pdk.eyes.rules): V rendered geometry,
# Q gateway qualities, L gateway log, C browser console, X state of the eyes; A expectations (pdk.eyes.expect).
CODES: dict[str, str] = {
    "S000": "invalid-json",
    "S001": "unknown-view-key",
    "S002": "missing-root",
    "S003": "malformed-member",
    "S004": "unknown-component-key",
    "S005": "missing-type",
    "S006": "unknown-component-type",
    "S007": "children-on-leaf",
    "S008": "missing-name",
    "S009": "root-name",
    "S010": "duplicate-name",
    "S011": "deprecated-component",
    "P001": "unknown-prop",
    "P002": "wrong-type",
    "P003": "invalid-enum",
    "P004": "missing-required",
    "P005": "schema-violation",
    "P006": "unknown-style-key",
    "P007": "unknown-meta-key",
    "P008": "invalid-name",
    "P010": "unknown-position-key",
    "B001": "unknown-binding-target",
    "B002": "unknown-propconfig-key",
    "B003": "unknown-binding-type",
    "B004": "invalid-binding-config",
    "B005": "unknown-transform-type",
    "B006": "invalid-transform",
    "B007": "invalid-param-direction",
    "B008": "invalid-onchange",
    "B009": "invalid-propconfig-value",
    "B010": "expression-syntax",
    "E001": "unknown-event-group",
    "E002": "unknown-event",
    "E003": "unknown-action-type",
    "E004": "wrong-action-scope",
    "E005": "invalid-action-config",
    "E006": "unknown-scripts-key",
    "E007": "script-not-indented",
    "R001": "undeclared-view-param",
    "R002": "undeclared-custom",
    "R003": "unknown-prop-ref",
    "R004": "unresolved-component-path",
    "R005": "incomplete-ref",
    "R006": "unknown-session-prop",
    "R007": "unknown-ref-root",
    "R010": "view-not-found",
    "R011": "param-undefined",
    "R012": "param-direction-mismatch",
    "R013": "required-param-missing",
    "R014": "param-type-mismatch",
    "R015": "style-class-not-found",
    "HMI001": "inconsistent-button-sizing",
    "HMI002": "missing-command-variant",
    "HMI003": "multiple-primary-actions",
    "HMI004": "missing-group-spacing",
    "HMI005": "redundant-layout-wrapper",
    "HMI006": "missing-typography",
    "HMI007": "mixed-control-heights",
    "HMI008": "missing-control-height",
    "HMI009": "empty-layout-group",
    "HMI010": "unsized-data-component",
    "HMI011": "alarm-onselect-not-row-selection",
    "V001": "text-clipped",
    "V002": "component-overflow",
    "V003": "viewport-overflow",
    "V007": "collapsed-component",
    "V010": "not-mounted",
    "V011": "embedded-view-failed",
    "V012": "repeater-instances-overflow",
    "Q001": "bad-quality",
    "Q002": "error-quality",
    "Q003": "uncertain-quality",
    "L001": "script-error-logged",
    "C001": "browser-error",
    "X001": "eyes-degraded",
    "X002": "page-out-of-sync",
    "A001": "expectation-failed",
    "A002": "malformed-expectation",
    "A003": "expectation-unknown-component",
    "A004": "action-failed",
    "A005": "malformed-action",
}


def make(
    code: str, severity: Severity, pointer: str, component: str, message: str, hint: str, detail: str | None = None
) -> Diagnostic:
    assert code in CODES, code
    diagnostic: Diagnostic = {
        "code": code,
        "severity": severity,
        "pointer": pointer,
        "component": component,
        "message": message,
        "hint": hint,
    }
    if detail:
        diagnostic["detail"] = detail
    return diagnostic


def escape(token: str | int) -> str:
    return str(token).replace("~", "~0").replace("/", "~1")


def join(pointer: str, *tokens: str | int) -> str:
    return pointer + "".join("/" + escape(t) for t in tokens)


def _natural(pointer: str) -> tuple[tuple[int, int | str], ...]:
    return tuple((0, int(p)) if p.isdigit() else (1, p) for p in pointer.split("/"))


def ordered(diagnostics: list[Diagnostic]) -> list[Diagnostic]:
    """Deduplicate and order: severity, then document position, then code."""
    seen: set[tuple[str, str, str]] = set()
    unique: list[Diagnostic] = []
    for d in diagnostics:
        key = (d["code"], d["pointer"], d["message"])
        if key not in seen:
            seen.add(key)
            unique.append(d)
    return sorted(unique, key=lambda d: (SEVERITY_RANK[d["severity"]], _natural(d["pointer"]), d["code"]))


def counts(diagnostics: list[Diagnostic]) -> Counter[str]:
    return Counter(d["severity"] for d in diagnostics)


def summary_line(diagnostics: list[Diagnostic]) -> str:
    c = counts(diagnostics)
    if not diagnostics:
        return "clean: no issues"
    parts = [f"{c[s]} {s}{'s' if c[s] != 1 and s != 'info' else ''}" for s in ("error", "warning", "info") if c[s]]
    return ", ".join(parts)


def _letter(severity: str) -> str:
    return {"error": "E", "warning": "W", "info": "I"}[severity]


def _where(d: Diagnostic) -> str:
    return f"{d['pointer']} ({d['component']})" if d["component"] else d["pointer"]


def matches_focus(d: Diagnostic, focus: str) -> bool:
    f = focus.strip()
    if not f:
        return True
    if f.startswith("/"):
        return d["pointer"].startswith(f)
    if re.fullmatch(r"[A-Za-z]+\d*", f) and d["code"].lower().startswith(f.lower()):
        return True
    return d["component"] == f or d["component"].startswith(f + "/")


def render(
    diagnostics: list[Diagnostic],
    *,
    title: str,
    budget: int = DEFAULT_BUDGET,
    focus: str | None = None,
) -> str:
    """Compact, budgeted text for agents. `focus` expands matching diagnostics with their detail."""
    diagnostics = ordered(diagnostics)
    limit = int(budget * CHARS_PER_TOKEN)
    if focus:
        selected = [d for d in diagnostics if matches_focus(d, focus)]
        lines = [f"{title} | focus {focus!r}: {len(selected)} of {len(diagnostics)}"]
        used = len(lines[0])
        for i, d in enumerate(selected):
            block = [f"{_letter(d['severity'])} {d['code']} {_where(d)}", f"  {d['message']}", f"  hint: {d['hint']}"]
            if d.get("detail"):
                block.append(f"  detail: {d['detail']}")
            text = "\n".join(block)
            if used + len(text) > limit - 80:
                lines.append(f"... +{len(selected) - i} more; narrow the focus (pointer prefix or component path)")
                break
            lines.append(text)
            used += len(text) + 1
        return "\n".join(lines)

    lines = [f"{title} | {summary_line(diagnostics)}"]
    if not diagnostics:
        return lines[0]
    groups: dict[tuple[str, str, str, str], list[Diagnostic]] = {}
    for d in diagnostics:
        groups.setdefault((d["severity"], d["code"], d["message"], d["hint"]), []).append(d)
    used = len(lines[0])
    rest: list[Diagnostic] = []
    for (severity, code, message, hint), members in groups.items():
        first = members[0]
        if len(members) == 1:
            text = f"{_letter(severity)} {code} {_where(first)} | {message} | hint: {hint}"
        else:
            text = f"{_letter(severity)} {code} x{len(members)} {message} | hint: {hint} | first {_where(first)}"
        if rest or used + len(text) > limit - 160:
            rest.extend(members)
            continue
        lines.append(text)
        used += len(text) + 1
    if rest:
        by_code = Counter(d["code"] for d in rest)
        listed = ", ".join(f"{code}x{n}" for code, n in by_code.most_common(6))
        lines.append(f"... +{len(rest)} more ({listed}); call again with focus=<code|component|pointer>")
    return "\n".join(lines)


def estimate_tokens(text: str) -> int:
    return int(len(text) / CHARS_PER_TOKEN) + 1

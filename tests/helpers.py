"""Minimal view builders for rule tests."""

from __future__ import annotations

from typing import Any

from pdk.bundle import Bundle
from pdk.diagnostics import Diagnostic
from pdk.validate import validate_view


def label(name: str = "Label", **extra: Any) -> dict[str, Any]:
    component: dict[str, Any] = {"type": "ia.display.label", "meta": {"name": name}, "position": {"basis": "32px"},
                                 "props": {"text": name}}
    component.update(extra)
    return component


def view(*children: dict[str, Any], root_type: str = "ia.container.flex", **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"custom": {}, "params": {}, "props": {},
                           "root": {"type": root_type, "meta": {"name": "root"}, "children": list(children)}}
    out.update(extra)
    return out


def codes(bundle: Bundle, data: dict[str, Any], *, severities: tuple[str, ...] = ("error", "warning")) -> set[str]:
    return {d["code"] for d in validate_view(data, bundle) if d["severity"] in severities}


def find(bundle: Bundle, data: dict[str, Any], code: str) -> list[Diagnostic]:
    return [d for d in validate_view(data, bundle) if d["code"] == code]

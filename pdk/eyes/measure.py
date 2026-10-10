"""What was measured of selected components: one compact line each, so an agent can check a layout without a
screenshot.

A query is a comma-separated list of terms: a name path (root/Header matches it and its subtree), a component type
(ia.display.label), a pointer prefix (/root/children/0) or * for every component. Values that are at their neutral
default (no padding, no border, transparent background, no gap) are left out; coordinates are viewport pixels.
"""

from __future__ import annotations

from typing import Any

from pdk.eyes.join import Joined, Part, mounted_by_design

MAX_PARTS = 40
TRANSPARENT = frozenset({"rgba(0, 0, 0, 0)", "transparent"})


def select(joined: Joined, query: str) -> list[Part]:
    terms = [term.strip().rstrip("/") or "/" for term in query.split(",") if term.strip()]
    return [part for part in joined.parts if any(_matches(part, term) for term in terms)]


def _matches(part: Part, term: str) -> bool:
    if term == "*":
        return True
    if term.startswith("ia."):
        return part.type == term
    if term.startswith("/"):
        return term == "/" or part.pointer == term or part.pointer.startswith(term + "/")
    return part.name == term or part.name.startswith(term + "/")


def describe(joined: Joined, query: str) -> str:
    parts = select(joined, query)
    viewport = joined.viewport
    head = f"measured {query!r} @{viewport.get('width')}x{viewport.get('height')}: "
    if not parts:
        return head + "no component matches (use a name path like root/Panel, a type like ia.display.label, a " \
                      "pointer prefix or *)"
    lines = [head + f"{len(parts)} component{'s' if len(parts) != 1 else ''}"]
    lines += [line(part) for part in parts[:MAX_PARTS]]
    if len(parts) > MAX_PARTS:
        lines.append(f"... {len(parts) - MAX_PARTS} more (narrow the query)")
    return "\n".join(lines)


def line(part: Part) -> str:
    out = f"{part.name} {part.type or '?'}"
    dom = part.dom
    if dom is None:
        return out + (" not rendered" if mounted_by_design(part) else " not rendered (hidden or inactive by design)")
    if not dom.get("visible", True):
        return out + f" hidden (display {dom.get('display')})"
    b = dom.get("bounds") or {}
    fields = [f"@{fmt(b.get('x'))},{fmt(b.get('y'))} {fmt(b.get('width'))}x{fmt(b.get('height'))}"]
    if dom.get("text"):
        text = " ".join(str(dom["text"]).split())
        fields.append('"' + (text if len(text) <= 40 else text[:39] + "…") + '"')
        fields.append(_font(dom))
    if dom.get("background_color") and dom["background_color"] not in TRANSPARENT:
        fields.append(f"bg {dom['background_color']}")
    for key, label in (("padding", "pad"), ("border_width", "border")):
        if any(dom.get(key) or []):
            fields.append(f"{label} {','.join(fmt(v) for v in dom[key])}")
    if dom.get("display") == "flex":
        fields.append(_flex(dom))
    scroll = _scroll(dom)
    if scroll:
        fields.append(scroll)
    return out + " " + " | ".join(f for f in fields if f)


def _font(dom: dict[str, Any]) -> str:
    family = str(dom.get("font_family") or "").split(",")[0].strip().strip("\"'")
    size, weight = dom.get("font_size"), dom.get("font_weight")
    parts = [f"{dom['color']}" if dom.get("color") else "", f"{fmt(size)}px" if size is not None else "",
             str(weight) if weight else "", family, f"align {dom['text_align']}" if dom.get("text_align") else ""]
    return " ".join(p for p in parts if p)


def _flex(dom: dict[str, Any]) -> str:
    out = [f"flex {dom.get('flex_direction') or 'row'}"]
    gaps = {dom.get("row_gap"), dom.get("column_gap")} - {None, 0}
    if gaps:
        out.append(f"gap {fmt(dom.get('row_gap') or 0)}/{fmt(dom.get('column_gap') or 0)}")
    for key, label in (("justify_content", "justify"), ("align_items", "align")):
        if dom.get(key) and dom[key] not in ("normal", "flex-start", "start"):
            out.append(f"{label} {dom[key]}")
    return " ".join(out)


def _scroll(dom: dict[str, Any]) -> str | None:
    sw, sh = dom.get("scroll_width"), dom.get("scroll_height")
    cw, ch = dom.get("client_width"), dom.get("client_height")
    if None in (sw, sh, cw, ch) or (sw <= cw + 0.5 and sh <= ch + 0.5):
        return None
    return f"content {fmt(sw)}x{fmt(sh)} in {fmt(cw)}x{fmt(ch)} ({dom.get('overflow_x')}/{dom.get('overflow_y')})"


def fmt(value: Any) -> str:
    if value is None:
        return "?"
    rounded = round(float(value), 1)
    return str(int(rounded)) if rounded == int(rounded) else str(rounded)

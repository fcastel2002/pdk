"""Anomaly rules over a joined view: geometry (V), gateway qualities (Q), gateway log (L), browser console (C) and
the state of the eyes themselves (X). They produce the validator's Diagnostic, pointing into the local view.json,
so pdk.diagnostics.render budgets and focuses them the same way.

The geometry rules are perspective-compiler's VIS001/002/003/007 on the joined model, with two changes: text cut
with an ellipsis counts (it hides information too), and a collapse is any component with content shrunk to zero
inside a parent that has room, not only leaves.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pdk.diagnostics import Diagnostic, Severity, join, make
from pdk.eyes.join import Joined, Part, mounted_by_design

EPSILON_PX = 0.5  # sub-pixel text and scroll rounding is not a defect
TEXT_TYPES = frozenset({"ia.display.label", "ia.input.button", "ia.navigation.link"})
SCROLLING = frozenset({"auto", "scroll"})

QUALITY_HINTS: tuple[tuple[str, str], ...] = (
    ("Bad_NotFound", "The tag or path does not exist: check the path against the tag provider (provider, folders, "
                     "case) or create the tag."),
    ("Error_ScriptEval", "The script raised: fix the code; focus on this diagnostic for the traceback."),
    ("Error_ExpressionEval", "The expression failed: check its functions, references and types."),
    ("Error_TypeConversion", "The value does not fit the property: convert it in a transform."),
    ("Error", "The binding failed while evaluating: focus on this diagnostic for the gateway's message."),
    ("Uncertain", "The binding has not produced a good value yet (initial or stale): check its source, or inspect "
                  "again."),
    ("Bad", "The source does not deliver a good value: check the tag, its provider and the device connection."),
)


def _detail(evidence: dict[str, Any]) -> str:
    return json.dumps(evidence, ensure_ascii=False, separators=(",", ":"), default=str)


def _diag(code: str, severity: Severity, part: Part | None, message: str, hint: str,
          evidence: dict[str, Any] | None = None, pointer: str | None = None) -> Diagnostic:
    return make(code, severity, pointer if pointer is not None else (part.pointer if part else ""),
                part.name if part else "", message, hint, _detail(evidence) if evidence else None)


def _box(node: dict[str, Any]) -> tuple[float, float, float, float]:
    b = node["bounds"]
    return b["left"], b["top"], b["right"], b["bottom"]


def _excess(inner: tuple[float, float, float, float], outer: tuple[float, float, float, float]) -> float:
    return max(outer[0] - inner[0], outer[1] - inner[1], inner[2] - outer[2], inner[3] - outer[3], 0.0)


def _short(text: str | None, width: int = 60) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= width else text[: width - 1] + "…"


def _dom_parent(joined: Joined, part: Part) -> Part | None:
    return joined.part(part.dom.get("parent")) if part.dom else None


def scrolls(dom: dict[str, Any], axis: str) -> bool:
    """The component scrolls along x or y (overflow auto or scroll) instead of clipping or spilling."""
    return dom.get(f"overflow_{axis}") in SCROLLING


def _clip_owner(joined: Joined, part: Part) -> Part | None:
    """The nearest component, itself included, whose overflow clips."""
    current: Part | None = part
    while current is not None and current.dom is not None:
        if current.dom.get("overflow_x") != "visible" or current.dom.get("overflow_y") != "visible":
            return current
        current = _dom_parent(joined, current)
    return None


# ------------------------------------------------------------------------------------------------ geometry


def text_clipped(joined: Joined) -> list[Diagnostic]:
    """V001: visible text of a label, button or link runs past the rectangle that clips it. Along an axis a scroll
    container scrolls, text past its edge is scrolled out of view, not cut (cut_rect leaves those axes out)."""
    found: list[Diagnostic] = []
    for part in joined.parts:
        dom = part.dom
        if not dom or part.type not in TEXT_TYPES or not dom.get("visible") or not dom.get("text"):
            continue
        clip = dom.get("cut_rect", dom.get("clip_rect"))
        rects = dom.get("text_rects") or []
        if not clip or not rects:
            continue
        owner = _clip_owner(joined, part)
        if owner is not None and owner.dom_path == joined.root_path:
            continue  # only the root clips it: that is V003's viewport overflow
        outer = (clip["left"], clip["top"], clip["right"], clip["bottom"])
        worst = max(_excess((r["left"], r["top"], r["right"], r["bottom"]), outer) for r in rects)
        if worst <= EPSILON_PX:
            continue
        wide = max(r["right"] for r in rects) - min(r["left"] for r in rects)
        tall = max(r["bottom"] for r in rects) - min(r["top"] for r in rects)
        across = wide - clip["width"] >= tall - clip["height"]
        needed, shown, unit = (wide, clip["width"], "px wide") if across else (tall, clip["height"], "px tall")
        ellipsis = dom.get("text_overflow") == "ellipsis"
        found.append(_diag(
            "V001", "warning", part,
            f"text cut{' with an ellipsis' if ellipsis else ''}: {_short(dom['text'], 40)!r} needs {needed:.0f} "
            f"{unit}, shows {shown:.0f}",
            "Give it room (position basis/grow, or a wider parent), let it wrap (style.whiteSpace normal), "
            "or shorten the text.",
            {"dom": part.dom_path, "text": dom["text"], "clip_rect": clip,
             "text_size": [round(wide, 1), round(tall, 1)],
             "clipped_by": owner.name if owner else None, "white_space": dom.get("white_space"),
             "text_overflow": dom.get("text_overflow")}))
    return found


def component_overflow(joined: Joined) -> list[Diagnostic]:
    """V002: a container's children overflow its content box, along an axis it does not scroll."""
    found: list[Diagnostic] = []
    for part in joined.parts:
        dom = part.dom
        if not dom or part.dom_path == joined.root_path or not (part.type or "").startswith("ia.container.") \
                or not dom.get("visible"):
            continue
        extra_w = 0.0 if scrolls(dom, "x") else dom["scroll_width"] - dom["client_width"]
        extra_h = 0.0 if scrolls(dom, "y") else dom["scroll_height"] - dom["client_height"]
        if max(extra_w, extra_h) <= EPSILON_PX:
            continue
        b = dom["bounds"]
        far = 1e6  # an axis that scrolls has no edge to stick out of
        content = (-far if scrolls(dom, "x") else b["left"], -far if scrolls(dom, "y") else b["top"],
                   far if scrolls(dom, "x") else b["left"] + dom["client_width"],
                   far if scrolls(dom, "y") else b["top"] + dom["client_height"])
        offenders = [child for child in joined.parts
                     if child.dom and child.dom.get("parent") == part.dom_path
                     and _excess(_box(child.dom), content) > EPSILON_PX]
        if not offenders:
            continue
        names = ", ".join(child.name.rsplit("/", 1)[-1] for child in offenders[:4])
        found.append(_diag(
            "V002", "error", part,
            f"children overflow the container by {max(extra_w, extra_h):.0f} px ({names})",
            "Shrink or let the children shrink (position shrink/basis), wrap them (props.wrap), "
            "or give the container more room.",
            {"dom": part.dom_path, "client": [dom["client_width"], dom["client_height"]],
             "scroll": [dom["scroll_width"], dom["scroll_height"]],
             "offenders": [{"name": c.name, "pointer": c.pointer, "bounds": c.dom["bounds"]} for c in offenders]}))
    return found


def viewport_overflow(joined: Joined) -> list[Diagnostic]:
    """V003: the view's content does not fit the viewport; reported on the deepest component that sticks out."""
    root = joined.part(joined.root_path)
    if root is None or not root.dom or not root.dom.get("visible"):
        return []
    dom = root.dom
    extra_w = dom["scroll_width"] - dom["client_width"]
    extra_h = dom["scroll_height"] - dom["client_height"]
    if max(extra_w, extra_h) <= EPSILON_PX:
        return []
    b = dom["bounds"]
    content = (b["left"], b["top"], b["left"] + dom["client_width"], b["top"] + dom["client_height"])
    contributors = []
    for part in joined.parts:
        if part is root or not part.dom:
            continue
        owner = _clip_owner(joined, _dom_parent(joined, part) or root)
        if owner is not None and owner is not root:
            continue  # a nested container clips it: V002's business
        if _excess(_box(part.dom), content) > EPSILON_PX:
            contributors.append(part)
    contributors.sort(key=lambda p: (-p.node.address.count(":"), p.node.address))
    target = contributors[0] if contributors else root
    viewport = joined.viewport
    return [_diag(
        "V003", "error", target,
        f"the view overflows the {viewport.get('width')}x{viewport.get('height')} viewport by "
        f"{max(extra_w, extra_h):.0f} px" + (f" ({target.name.rsplit('/', 1)[-1]} sticks out)" if contributors else ""),
        "Let the content shrink or wrap, or reduce fixed sizes; set the viewport the view is designed for "
        "with --viewport.",
        {"scroll": [dom["scroll_width"], dom["scroll_height"]], "client": [dom["client_width"], dom["client_height"]],
         "contributors": [p.name for p in contributors[:8]]})]


def collapsed(joined: Joined) -> list[Diagnostic]:
    """V007: a component with content is rendered with zero width or height inside a parent that has room."""
    found: list[Diagnostic] = []
    for part in joined.parts:
        dom = part.dom
        if not dom or part.dom_path == joined.root_path or not dom.get("visible"):
            continue
        b = dom["bounds"]
        if b["width"] > EPSILON_PX and b["height"] > EPSILON_PX:
            continue
        parent = _dom_parent(joined, part)
        if parent is None or not parent.dom:
            continue
        pb = parent.dom["bounds"]
        if pb["width"] <= EPSILON_PX or pb["height"] <= EPSILON_PX:
            continue  # the parent's own collapse is the defect
        if not (dom.get("text") or part.node.children):
            continue  # empty and content-sized: nothing is hidden
        axis = "height" if b["height"] <= EPSILON_PX else "width"
        position = part.node.component.get("position") or {}
        found.append(_diag(
            "V007", "error", part,
            f"collapsed to zero {axis} ({len(part.node.children)} children hidden)" if part.node.children
            else f"collapsed to zero {axis}, hiding {_short(dom.get('text'), 40)!r}",
            "Give it a size: position basis (e.g. '40px' or 'auto') or grow > 0 in a flex parent, "
            "width/height in a coordinate parent.",
            {"dom": part.dom_path, "bounds": b, "position": position, "flex_basis": dom.get("flex_basis"),
             "parent": parent.name}))
    return found


def not_mounted(joined: Joined) -> list[Diagnostic]:
    """V010: a component of the view.json is not in the page (only the topmost missing one is reported)."""
    found: list[Diagnostic] = []
    for part in joined.parts:
        if part.dom is not None or not mounted_by_design(part):
            continue
        parent = joined.by_dom.get(f"{joined.mount}.{part.node.parent.address}") if part.node.parent else None
        if parent is not None and parent.dom is None:
            continue
        found.append(_diag(
            "V010", "warning", part, f"{part.type} is in the view.json but not in the page",
            "Check that the deploy finished and the page was reopened; a component that never renders usually "
            "has a parent that does not mount it.",
            {"dom": part.dom_path}))
    return found


def failed_views(joined: Joined) -> list[Diagnostic]:
    """V011: an embedded view or repeater shows Perspective's failed-view placeholder instead of its view."""
    found: list[Diagnostic] = []
    for part in joined.parts:
        failed = (part.dom or {}).get("failed_views") or 0
        if not failed:
            continue
        props = part.node.props()
        has_path = "path" in props or "props.path" in (part.node.component.get("propConfig") or {})
        found.append(_diag(
            "V011", "error", part, f"{part.type} shows {failed} view(s) that did not load (not found, error or "
                                   f"access denied) instead of {props.get('path') or 'its view'!r}",
            "Check props.path against the project's views (pdk_validate with project= reports R010), the params "
            "the view declares, and inspect the embedded view on its own for its errors.",
            {"failed": failed, "path": props.get("path")},
            pointer=join(part.pointer, "props", "path") if has_path else None))
    return found


# ------------------------------------------------------------------------------------------------ gateway


def _binding_key(path: str, bindings: dict[str, Any]) -> str | None:
    for key in sorted(bindings, key=len, reverse=True):
        if path == key or path.startswith(key + ".") or path.startswith(key + "["):
            return key
    return None


def _quality_message(text: str | None) -> str:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if not lines:
        return ""
    return lines[-1] if lines[0].startswith("Traceback") else lines[0]


def _quality_rule(quality: str, level: str) -> tuple[str, Severity] | None:
    if level == "Error" or quality.startswith("Error"):
        return "Q002", "error"
    if level == "Bad" or quality.startswith("Bad"):
        return "Q001", "error"
    if level == "Uncertain" or quality.startswith("Uncertain"):
        return "Q003", "warning"
    return None


def _hint(quality: str) -> str:
    return next(hint for prefix, hint in QUALITY_HINTS if quality.startswith(prefix)) \
        if any(quality.startswith(prefix) for prefix, _ in QUALITY_HINTS) else QUALITY_HINTS[-1][1]


def _qualities(part: Part | None, issues: list[dict[str, Any]], bindings: dict[str, Any],
               configs: dict[str, Any], base_pointer: str) -> list[Diagnostic]:
    found: list[Diagnostic] = []
    reported: list[str] = []
    for issue in issues:
        path = str(issue.get("path", ""))
        if any(path == seen or path.startswith(seen + ".") or path.startswith(seen + "[") for seen in reported):
            continue  # inside a node already reported
        rule = _quality_rule(str(issue.get("quality", "")), str(issue.get("level", "")))
        if rule is None:
            continue
        key = _binding_key(path, bindings)
        reported.append(key or path)
        binding = (configs.get(key) or {}).get("binding") if key else None
        kind = f"{binding.get('type')} binding" if isinstance(binding, dict) else "value"
        message = _quality_message(issue.get("message"))
        found.append(_diag(
            rule[0], rule[1], part,
            f"{path} ({kind}) is {issue.get('quality')}" + (f": {_short(message, 90)}" if message else ""),
            _hint(str(issue.get("quality"))),
            {"path": path, "quality": issue.get("quality"), "message": issue.get("message"),
             "binding": binding.get("config") if isinstance(binding, dict) else None,
             "transforms": len(binding.get("transforms") or []) if isinstance(binding, dict) else 0},
            pointer=join(base_pointer, "propConfig", key) if key else (base_pointer or None)))
    return found


def qualities(joined: Joined) -> list[Diagnostic]:
    """Q001/Q002/Q003: a property the gateway evaluated is Bad, Error or Uncertain, on the binding that feeds it."""
    found: list[Diagnostic] = []
    if joined.live_view:
        view_configs = joined.view.get("propConfig") if isinstance(joined.view.get("propConfig"), dict) else {}
        found += _qualities(None, joined.live_view.get("qualities", []), view_configs, view_configs, "")
    for part in joined.parts:
        if not part.live or not part.live.get("qualities"):
            continue
        configs = part.node.component.get("propConfig")
        configs = configs if isinstance(configs, dict) else {}
        bindings = part.live.get("bindings") or {key: value for key, value in configs.items()
                                                 if isinstance(value, dict) and "binding" in value}
        found += _qualities(part, part.live["qualities"], bindings, configs, part.pointer)
    return found


def overlays(joined: Joined) -> list[Diagnostic]:
    """Without the gateway tree: Q001/Q002 from the quality overlay Perspective draws over a bad binding."""
    found: list[Diagnostic] = []
    for part in joined.parts:
        level = (part.dom or {}).get("overlay")
        if not level:
            continue
        code: str = "Q002" if level == "error" else "Q001"
        found.append(_diag(
            code, "error", part, f"Perspective draws a '{level}' quality overlay (DOM only: no gateway tree)",
            "A binding of this component is not Good; with the gateway tree the eyes would name the property.",
            {"dom": part.dom_path, "overlay": level}))
    return found


_ACTION = re.compile(r"Error running action '([\w.]+)' on ")


def logged(joined: Joined, events: list[dict[str, Any]]) -> list[Diagnostic]:
    """L001: the gateway logged a script error for this view, attributed to the component in the MDC."""
    found: list[Diagnostic] = []
    seen: set[tuple[str, str]] = set()
    by_path = {part.node.path: part for part in joined.parts}  # the MDC names components within their view
    for event in events:
        mdc = event.get("mdc") or {}
        part = by_path.get(str(mdc.get("component", "")))
        text = str(event.get("message") or "")
        error = event.get("error") or {}
        last = _quality_message(error.get("message")) or _quality_message(text)
        action = _ACTION.search(text)
        what = f"{action.group(1)} raised" if action else _short(text.splitlines()[0] if text else "", 70)
        key = (part.name if part else "", what + last)
        if key in seen:
            continue
        seen.add(key)
        pointer = None
        if part and action:
            pointer = join(part.pointer, "events", *action.group(1).split(".", 1))
        found.append(_diag(
            "L001", "error" if error else "warning", part,
            f"{what}: {_short(last, 90)}" if last and last not in what else what,
            "A script on this component failed at runtime: fix it; focus on this diagnostic for the traceback.",
            {"logger": event.get("logger"), "level": event.get("level"), "message": _short(text, 600),
             "error": error.get("class"), "view": mdc.get("view")},
            pointer=pointer))
    return found


def console(entries: list[dict[str, str]]) -> list[Diagnostic]:
    """C001: an uncaught exception or a console error in the browser."""
    found: list[Diagnostic] = []
    seen: set[str] = set()
    for entry in entries:
        if entry.get("type") not in ("error", "pageerror") or entry.get("text") in seen:
            continue
        seen.add(entry["text"])
        uncaught = entry["type"] == "pageerror"
        found.append(_diag(
            "C001", "error" if uncaught else "warning", None,
            f"browser {'exception' if uncaught else 'console error'}: {_short(entry['text'], 100)}",
            "Usually a component that cannot render its props, or a resource that fails to load; "
            "focus on this diagnostic for the full text.",
            {"type": entry["type"], "text": entry["text"]}))
    return found


def degraded(reasons: list[str]) -> list[Diagnostic]:
    """X001: part of the eyes is missing, so some anomalies cannot be seen."""
    return [_diag("X001", "warning", None, reason,
                  "Check `uv run pdk gateway health`; the report still covers what the browser sees.")
            for reason in reasons]


def out_of_sync(joined: Joined) -> list[Diagnostic]:
    """X002: the open page does not render this view.json, so nothing else can be trusted."""
    if not joined.mismatches:
        return []
    return [_diag("X002", "error", None, f"the page does not render this view.json: {joined.mismatches[0]}",
                  "Deploy the view (pdk_deploy) and inspect again; inspect a pulled or deployed copy.",
                  {"mismatches": joined.mismatches[:10]})]


def evaluate(joined: Joined, *, logs: list[dict[str, Any]] | None = None,
             console_entries: list[dict[str, str]] | None = None,
             degraded_reasons: list[str] | None = None) -> list[Diagnostic]:
    """Every anomaly of one inspection. A page out of sync with the view.json reports only that, and what happens
    inside a collapsed component is reported once, as that component's V007."""
    found = degraded(degraded_reasons or []) + console(console_entries or [])
    sync = out_of_sync(joined)
    if sync:
        return sync + found
    hidden = collapsed(joined)
    inside = tuple(d["component"] + "/" for d in hidden)
    geometry = viewport_overflow(joined) + component_overflow(joined) + text_clipped(joined)
    found += hidden + [d for d in geometry if not (inside and d["component"].startswith(inside))]
    found += not_mounted(joined) + failed_views(joined)
    found += qualities(joined) if joined.live_view else overlays(joined)
    found += logged(joined, logs or [])
    return found

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
from collections.abc import Mapping, Set
from typing import Any

from pdk.diagnostics import Diagnostic, Severity, join, make
from pdk.eyes.join import Joined, Part, mounted_by_design
from pdk.paths import RUN

EPSILON_PX = 0.5  # sub-pixel text and scroll rounding is not a defect
TEXT_TYPES = frozenset({"ia.display.label", "ia.input.button", "ia.navigation.link"})
SCROLLING = frozenset({"auto", "scroll"})
SLIVER_PX = 24.0  # a scrollbar for less than this is content that almost fits, not a scrolling area

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


def scroll_extent(dom: dict[str, Any], axis: str) -> float:
    """How far the content runs past the component's content box along x or y."""
    return dom["scroll_width"] - dom["client_width"] if axis == "x" else dom["scroll_height"] - dom["client_height"]


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
            "or give the container more room; do not hide it behind style.overflow auto (V014).",
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


def _scrolling_ancestor(joined: Joined, part: Part, axis: str) -> Part | None:
    current = _dom_parent(joined, part)
    while current is not None and current.dom is not None:
        if scrolls(current.dom, axis) and scroll_extent(current.dom, axis) > EPSILON_PX:
            return current
        current = _dom_parent(joined, current)
    return None


def unrequested_scroll(joined: Joined, declared: Mapping[str, Set[str]] | None = None) -> list[Diagnostic]:
    """V014: a container scrolls (its content overflows along an axis with overflow auto or scroll), or shows a
    scrollbar with nothing to scroll (overflow scroll), and no `scrolls` expectation declares it. A scroll hides
    content the way V002 reports, so one the view did not ask for is usually V002 silenced. It is an error when it
    is plainly not a scrolling area: nested in another scroll along the same axis, or for a sliver of content."""
    declared = declared or {}
    found: list[Diagnostic] = []
    for part in joined.parts:
        dom = part.dom
        if not dom or part.dom_path == joined.root_path or not (part.type or "").startswith("ia.container.") \
                or not dom.get("visible"):
            continue
        open_axes = [ax for ax in ("x", "y") if ax not in declared.get(part.name, ())]
        axes = [ax for ax in open_axes if scrolls(dom, ax) and scroll_extent(dom, ax) > EPSILON_PX]
        bare = [ax for ax in open_axes if dom.get(f"overflow_{ax}") == "scroll" and ax not in axes]
        evidence = {"dom": part.dom_path, "overflow": [dom.get("overflow_x"), dom.get("overflow_y")],
                    "client": [dom["client_width"], dom["client_height"]],
                    "scroll": [dom["scroll_width"], dom["scroll_height"]]}
        if axes:
            extra = {ax: round(scroll_extent(dom, ax), 1) for ax in axes}
            nested = {ax: outer for ax in axes if (outer := _scrolling_ancestor(joined, part, ax))}
            sliver = [ax for ax in axes if extra[ax] < SLIVER_PX]
            message = "scrolls along " + ", ".join(f"{ax} ({extra[ax]:.0f} px out of sight)" for ax in axes)
            if nested:
                message += "; inside " + ", ".join(f"{outer.name}, which scrolls along {ax} too"
                                                   for ax, outer in nested.items())
            elif sliver:
                message += f"; a scrollbar for under {SLIVER_PX:.0f} px: content that almost fits"
            found.append(_diag(
                "V014", "error" if nested or sliver else "warning", part,
                message + "; no expectation asks for it",
                "Make the content fit instead: let the children shrink (position shrink/basis), wrap them "
                "(props.wrap) or give the container room, and remove style.overflow. If the design asks for a "
                f"scrolling area (a list, a log), declare it: expect \"{part.name} scrolls {axes[0]}\".",
                {**evidence, "extra": extra, "nested_in": {ax: p.name for ax, p in nested.items()}}))
        elif bare:
            found.append(_diag(
                "V014", "warning", part,
                f"overflow scroll shows a scrollbar along {', '.join(bare)} with nothing to scroll",
                "Remove style.overflow; where content may outgrow it, auto shows a scrollbar only when it does.",
                evidence))
    return found


REPEATER = "ia.display.flex-repeater"


def repeater_overflow(joined: Joined) -> list[Diagnostic]:
    """V012: a flex repeater's instances are bigger than the repeater along the axis the instance count does not
    explain (the cross axis without wrap, the main axis with it), so each one scrolls inside a strip of it."""
    found: list[Diagnostic] = []
    for part in joined.parts:
        dom = part.dom
        if part.type != REPEATER or not dom:
            continue
        props = part.node.props()
        row = str(props.get("direction") or "row").startswith("row")
        wraps = str(props.get("wrap") or "nowrap") != "nowrap"
        horizontal = row == wraps  # the axis to check: width for column/nowrap and row/wrap
        size, client = ("scroll_width", "client_width") if horizontal else ("scroll_height", "client_height")
        content, room = dom.get(size) or 0, dom.get(client) or 0
        if room <= 0 or content - room <= EPSILON_PX:
            continue
        side = "wider" if horizontal else "taller"
        found.append(_diag(
            "V012", "warning", part,
            f"its instances are {side} than it ({content:.0f} px in {room:.0f}): each one scrolls inside it",
            "Give the repeater room on that axis (position.basis/grow), or size the instances: "
            f"props.{'useDefaultViewWidth' if horizontal else 'useDefaultViewHeight'} false with "
            "props.elementPosition, or a smaller defaultSize in the instance view.",
            {"axis": "x" if horizontal else "y", "content": content, "room": room}))
    return found


_RGBA = re.compile(r"rgba?\(([^)]*)\)")
LARGE_TEXT_PX = 24.0  # WCAG "large": 18pt, or 14pt bold
LARGE_BOLD_TEXT_PX = 18.66
# Components whose text is their content and takes props.style.color; the text inside charts, tables or other
# widgets is drawn by the component itself, so a contrast warning there could not be acted on.
CONTRAST_TYPES = TEXT_TYPES | {"ia.display.markdown", "ia.input.checkbox", "ia.input.toggle-switch",
                               "ia.input.radio-group"}


def _rgba(value: str | None) -> tuple[float, float, float, float] | None:
    match = _RGBA.fullmatch((value or "").strip())
    if not match:
        return None
    parts = [float(p) for p in re.split(r"[\s,/]+", match.group(1).strip()) if p]
    if len(parts) < 3:
        return None
    return parts[0], parts[1], parts[2], parts[3] if len(parts) > 3 else 1.0


def _luminance(rgb: tuple[float, float, float]) -> float:
    def channel(c: float) -> float:
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(color: str | None, background: str | None) -> float | None:
    """WCAG contrast of a text color (its alpha composited over the background) on an opaque background."""
    fg, bg = _rgba(color), _rgba(background)
    if fg is None or bg is None:
        return None
    alpha = fg[3]
    mixed = tuple(fg[i] * alpha + bg[i] * (1 - alpha) for i in range(3))
    lighter, darker = sorted((_luminance(mixed), _luminance(bg[:3])), reverse=True)  # type: ignore[arg-type]
    return (lighter + 0.05) / (darker + 0.05)


STYLED_KEYS = ("color", "backgroundColor", "background", "classes")


def _chooses_colors(part: Part) -> bool:
    """Whether the view.json sets the colors of this component or of an ancestor (a style color or background, a
    style class, or a binding on its style): otherwise they are the theme's, not the view's to fix."""
    node: Any = part.node
    while node is not None:
        props = node.props()
        for key in ("style", "textStyle"):
            style = props.get(key)
            if isinstance(style, dict) and any(style.get(k) for k in STYLED_KEYS):
                return True
        configs = node.component.get("propConfig")
        if isinstance(configs, dict) and any(str(k).startswith(("props.style", "props.textStyle"))
                                             for k in configs):
            return True
        node = node.parent
    return False


def low_contrast(joined: Joined) -> list[Diagnostic]:
    """V013: text whose color is too close to what is behind it (WCAG AA: 4.5:1, or 3:1 for large text), on the
    components whose text is theirs to color (CONTRAST_TYPES), when the view chose those colors (the theme's
    defaults are not judged). Disabled controls are exempt, and text over a background image is not judged."""
    found: list[Diagnostic] = []
    for part in joined.parts:
        dom = part.dom
        if part.type not in CONTRAST_TYPES or not dom or not dom.get("text") or dom.get("disabled") \
                or not dom.get("visible", True):
            continue
        ratio = contrast_ratio(dom.get("color"), dom.get("text_bg"))
        if ratio is None or not _chooses_colors(part):
            continue
        size = dom.get("font_size") or 0
        bold = str(dom.get("font_weight") or "400").isdigit() and int(dom["font_weight"]) >= 700
        need = 3.0 if size >= LARGE_TEXT_PX or (bold and size >= LARGE_BOLD_TEXT_PX) else 4.5
        if ratio >= need:
            continue
        found.append(_diag(
            "V013", "warning", part,
            f"text contrast {ratio:.2f}:1 ({dom.get('color')} on {dom.get('text_bg')}), below {need:g}:1",
            "Darken the text or lighten what is behind it (props.style.color, the backgroundColor of it or its "
            "parent, or their style classes); WCAG AA asks 4.5:1, or 3:1 from 24px (18.66px bold).",
            {"ratio": round(ratio, 2), "color": dom.get("color"), "background": dom.get("text_bg"),
             "font_size": size, "needed": need}))
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
                  f"Check `{RUN}pdk gateway health`; the report still covers what the browser sees.")
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
             degraded_reasons: list[str] | None = None,
             declared_scrolls: Mapping[str, Set[str]] | None = None) -> list[Diagnostic]:
    """Every anomaly of one inspection. A page out of sync with the view.json reports only that, and what happens
    inside a collapsed component is reported once, as that component's V007. `declared_scrolls` maps component
    names to the axes `scrolls` expectations ask for (expect.declared_scrolls); V014 leaves those alone."""
    found = degraded(degraded_reasons or []) + console(console_entries or [])
    sync = out_of_sync(joined)
    if sync:
        return sync + found
    hidden = collapsed(joined)
    names = {d["component"] for d in hidden}
    inside = tuple(name + "/" for name in names)

    def shown(d: Diagnostic) -> bool:  # the collapsed component itself and what it holds: only its V007
        return d["component"] not in names and not (inside and d["component"].startswith(inside))

    geometry = viewport_overflow(joined) + component_overflow(joined) + text_clipped(joined)
    geometry += unrequested_scroll(joined, declared_scrolls)
    found += hidden + [d for d in geometry if shown(d)]
    found += not_mounted(joined) + failed_views(joined) + repeater_overflow(joined)
    found += [d for d in low_contrast(joined) if shown(d)]
    found += qualities(joined) if joined.live_view else overlays(joined)
    found += logged(joined, logs or [])
    return found

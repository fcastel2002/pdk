"""Expectations about a rendered view, checked against what was measured (A001-A003).

One expectation per `;` or line, `<component> <relation> [args]`, components by name path (quote names with spaces):

    root/Subtitle below root/Title [gap 8]        also above, left-of, right-of; gap >= 8, gap 8..16
    root/Log/Last inside root/Log                 within its box, or its content along an axis it scrolls
    root/Save aligned right root/Form             edges: left right top bottom center-x center-y
    root/Table width 100%                         width height x y; = (default), >=, <=, >, <; px or % of the parent
    root/Pump x 150                               x and y from the parent's inner top-left (coord's position.x/y)
    root/Title text "Pump 1"                      text ~ "Pump" for contains
    root/Alarm visible                            or hidden (rendered with a size, wherever it is scrolled to)
    root/Save enabled                             or disabled (the control, or the input inside it)
    popup detail open                             or closed; inside an open popup: popup:detail/root/Name text "P-1"
    root/Log scrolls [y]                          its content overflows along x/y (either) and it scrolls there
    root/Log/Last in-view                         wholly in sight: not scrolled away, clipped, or off the viewport
    root/Title color #323232                      color, bg (hex, rgb(), rgba()), font-size [op] N, font-weight N
    tag Sandbox/Setpoint = 42                     a simulated tag's value (read back from the gateway), under PDK/
    tag Sandbox/Setpoint quality Bad_Stale        its quality: a name, or a level (Good, Bad, Uncertain, Error)
    alarm North/Level active                      or cleared: alarm events of the tags at or under the path

Lengths compare with a 1px tolerance (sub-pixel layout); edges are viewport pixels of the component's box, except x
and y, which are measured from the parent's padding box (the viewport for root), like position.x/y in a coord.
"""

from __future__ import annotations

import difflib
import re
import shlex
from dataclasses import dataclass
from typing import Any, Protocol

from pdk import tags as simulated
from pdk.diagnostics import Diagnostic, make
from pdk.eyes.join import Joined, Part
from pdk.eyes.measure import fmt
from pdk.eyes.rules import scrolls

EPSILON_PX = 1.0
SIDES = {"below", "above", "left-of", "right-of", "inside"}
EDGES = {"left", "right", "top", "bottom", "center-x", "center-y"}
METRICS = {"width", "height", "x", "y"}
OPS = {"=", ">=", "<=", ">", "<"}
WEIGHTS = {"normal": "400", "bold": "700"}
GRAMMAR = ("One per ; or line: `<name path> below|above|left-of|right-of <name path> [gap [op] N | gap N..M]`, "
           "`<a> inside <b>`, "
           "`<a> aligned left|right|top|bottom|center-x|center-y <b>`, `<a> width|height|x|y [=|>=|<=|>|<] N[px|%]`, "
           "`<a> text [~] \"...\"`, `<a> visible|hidden|in-view`, `<a> enabled|disabled`, `<a> scrolls [x|y]`, "
           "`<a> color|bg <css color>`, "
           "`popup <id> open|closed`, components of an open popup as popup:<id>/root/..., "
           "`<a> font-size [op] N`, "
           "`<a> font-weight N`, `tag <path> [=] <value>`, `tag <path> quality <Code|Good|Bad|Uncertain|Error>`, "
           "`alarm <path> active|cleared` (tag paths under PDK/ like Sandbox/Setpoint).")
HINTS = {
    "below": "Check the order of the parent's children, its direction (column) or this component's position.y.",
    "above": "Check the order of the parent's children, its direction (column) or this component's position.y.",
    "left-of": "Check the order of the parent's children, its direction (row) or this component's position.x.",
    "right-of": "Check the order of the parent's children, its direction (row) or this component's position.x.",
    "inside": "Check position (x/y/width/height in coord, basis/shrink in flex) and the parent's overflow.",
    "aligned": "Check the parent's alignItems/justify (flex) or the x/y and sizes (coord).",
    "width": "Check position.basis/grow/shrink (flex) or position.width (coord), and the parent's alignItems.",
    "height": "Check position.basis/grow/shrink (flex) or position.height (coord), and the parent's alignItems.",
    "x": "Check position.x (coord) or the siblings before it and the parent's justify (flex).",
    "y": "Check position.y (coord) or the siblings before it and the parent's direction (flex).",
    "text": "Check props.text and the binding that feeds it (pdk_inspect reports its quality).",
    "visible": "Check position.display, style display/visibility/opacity and the parent's size (V007 collapsed).",
    "hidden": "Check position.display or the binding that should hide it.",
    "enabled": "Check props.enabled and the binding that feeds it (pdk_inspect reports its quality).",
    "disabled": "Check props.enabled and the binding that feeds it (pdk_inspect reports its quality).",
    "in-view": "Give the scroll container room or move the component up its children; a parent with overflow "
               "hidden cuts it.",
    "scrolls": "Set style.overflowY (or overflowX) auto on the container, and let its content outgrow it: children "
               "with shrink 0 and a basis, the container itself with a bounded size (basis 0 and grow).",
    "color": "Check props.style.color and the style classes that apply (the stylesheet may override it).",
    "bg": "Check props.style.backgroundColor and the style classes that apply.",
    "font-size": "Check props.style.fontSize and the style classes that apply.",
    "font-weight": "Check props.style.fontWeight and the style classes that apply.",
    "tag": ("Check what writes the tag: the control's event script (pdk_inspect reports L001 if it failed), its "
            "tag path and the value it sends; pdk tags read shows the tag now."),
    "alarm": ("Check the alarm's mode and setpoint in the tag JSON against the value written; pdk tags alarms "
              "lists the events."),
}


class TagReader(Protocol):
    def read(self, path: str) -> dict[str, Any]: ...

    def alarms(self, path: str) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class Outcome:
    total: int
    diagnostics: list[Diagnostic]

    @property
    def passed(self) -> int:
        return self.total - len(self.diagnostics)

    def summary(self) -> str:
        return f"expect: {self.passed}/{self.total} passed"


class _Malformed(ValueError):
    pass


def split(text: str) -> list[str]:
    """Expectations separated by `;` or newlines, outside quotes."""
    out, current, quote = [], "", ""
    for ch in text:
        if quote:
            quote = "" if ch == quote else quote
        elif ch in "\"'":
            quote = ch
        elif ch in ";\n":
            out.append(current)
            current = ""
            continue
        current += ch
    out.append(current)
    return [item.strip() for item in out if item.strip()]


def check(joined: Joined, text: str, tags: TagReader | None = None) -> Outcome:
    """`tags` reads simulated tags and their alarms (pdk.tags.Probe) for the tag and alarm expectations."""
    items = split(text)
    found: list[Diagnostic] = []
    for item in items:
        try:
            failure = _check_one(joined, item, tags)
        except _Malformed as error:
            found.append(make("A002", "error", "", "", f"cannot read expectation {item!r}: {error}", GRAMMAR))
            continue
        if failure:
            found.append(failure)
    return Outcome(len(items), found)


def _check_one(joined: Joined, item: str, tags: TagReader | None = None) -> Diagnostic | None:
    try:
        tokens = shlex.split(item)
    except ValueError as error:
        raise _Malformed(str(error)) from None
    if len(tokens) < 2:
        raise _Malformed("needs a component and a relation")
    subject_name, relation, args = tokens[0], tokens[1], tokens[2:]
    if subject_name == "popup":
        return _popup(joined, relation, args, item)
    if subject_name in ("tag", "alarm"):
        if tags is None:
            raise _Malformed(f"{subject_name} expectations need the gateway")
        try:
            return (_tag if subject_name == "tag" else _alarm)(tags, relation, args, item)
        except _Malformed:
            raise
        except Exception as error:  # noqa: BLE001  (a gateway error is the measured state of this expectation)
            return make("A001", "error", "", "", f"{item}: could not read it: {error}", HINTS[subject_name])
    subject = _part(joined, subject_name)
    if isinstance(subject, str):
        return make("A003", "error", "", subject_name, f"{item}: {subject}",
                    "Use the name path of a component of this view (root/Panel/Label); pdk_inspect query=* lists them.")
    if relation in SIDES:
        return _side(joined, subject, relation, args, item)
    if relation == "aligned":
        return _aligned(joined, subject, args, item)
    if relation in METRICS:
        return _metric(joined, subject, relation, args, item)
    if relation == "text":
        return _text(subject, args, item)
    if relation in ("visible", "hidden"):
        if args:
            raise _Malformed(f"{relation} takes no arguments")
        shown = _shown(subject)
        if shown == (relation == "visible"):
            return None
        why = "it is not rendered" if subject.dom is None else (
            "it is rendered" if shown else f"it is hidden or has no size ({_box_text(subject)})")
        return _fail(subject, relation, f"{item}: {why}")
    if relation in ("enabled", "disabled"):
        if args:
            raise _Malformed(f"{relation} takes no arguments")
        if subject.dom is None:
            return _fail(subject, relation, f"{item}: it is not rendered")
        if bool(subject.dom.get("disabled")) == (relation == "disabled"):
            return None
        return _fail(subject, relation, f"{item}: it is {'disabled' if subject.dom.get('disabled') else 'enabled'}")
    if relation == "in-view":
        if args:
            raise _Malformed("in-view takes no arguments")
        return _in_view(joined, subject, item)
    if relation == "scrolls":
        if len(args) > 1 or (args and args[0] not in ("x", "y")):
            raise _Malformed("expected `scrolls [x|y]`")
        return _scrolls(subject, args[0] if args else None, item)
    if relation in ("color", "bg"):
        return _color(subject, relation, args, item)
    if relation in ("font-size", "font-weight"):
        return _font(subject, relation, args, item)
    raise _Malformed(f"unknown relation {relation!r}")


def _popup(joined: Joined, pid: str, args: list[str], item: str) -> Diagnostic | None:
    if len(args) != 1 or args[0] not in ("open", "closed"):
        raise _Malformed("expected `popup <id> open|closed`")
    if (pid in joined.popups) == (args[0] == "open"):
        return None
    shown = ", ".join(joined.popups) or "none"
    return make("A001", "error", "", "", f"{item}: it is {'open' if pid in joined.popups else 'closed'} "
                                          f"(open popups: {shown})",
                "Check the popup action's id and type (open/close/toggle) and the step that should trigger it.")


def _tag(tags: TagReader, path: str, args: list[str], item: str) -> Diagnostic | None:
    if len(args) == 2 and args[0] == "quality":
        wanted = args[1]
        state = tags.read(path)
        quality = str(state.get("quality"))
        if quality == wanted or (wanted in ("Good", "Bad", "Uncertain", "Error") and quality.startswith(wanted)):
            return None
        return make("A001", "error", "", "", f"{item}: it is {quality} (value {simulated.show(state.get('value'))})",
                    HINTS["tag"])
    if len(args) == 2 and args[0] == "=":
        raw = args[1]
    elif len(args) == 1:
        raw = args[0]
    else:
        raise _Malformed("expected `tag <path> [=] <value>` or `tag <path> quality <Code>`")
    state = tags.read(path)
    if state.get("quality") == "Good" and simulated.matches(state.get("value"), raw):
        return None
    why = f"it is {simulated.show(state.get('value'))} ({state.get('quality')}"
    why += f": {state['message']})" if state.get("message") else ")"
    if state.get("quality") == simulated.LICENSE_EXCEEDED:
        why += "; " + simulated.LICENSE_HINT.removeprefix("hint: ")
    return make("A001", "error", "", "", f"{item}: {why}", HINTS["tag"])


def _alarm(tags: TagReader, path: str, args: list[str], item: str) -> Diagnostic | None:
    if len(args) != 1 or args[0] not in ("active", "cleared"):
        raise _Malformed("expected `alarm <path> active|cleared`")
    events = tags.alarms(path)
    active = [e for e in events if not e.get("cleared")]
    if bool(active) == (args[0] == "active"):
        return None
    seen = "; ".join(f"{e.get('tag')}/{e.get('name')} {e.get('state')}" for e in events[:5]) or "no alarm events"
    return make("A001", "error", "", "", f"{item}: {len(active)} active ({seen})", HINTS["alarm"])


def _part(joined: Joined, name: str) -> Part | str:
    part = joined.by_name.get(name.rstrip("/"))
    if part is not None:
        return part
    close = difflib.get_close_matches(name, list(joined.by_name), n=1)
    return f"no component {name!r}" + (f" (did you mean {close[0]}?)" if close else "")


def _shown(part: Part) -> bool:
    dom = part.dom
    if dom is None or not dom.get("visible", True):
        return False
    b = dom.get("bounds") or {}
    return (b.get("width") or 0) > 0 and (b.get("height") or 0) > 0


def _edges(part: Part) -> dict[str, float] | None:
    if part.dom is None:
        return None
    b = part.dom["bounds"]
    return {"left": b["left"], "right": b["right"], "top": b["top"], "bottom": b["bottom"],
            "center-x": (b["left"] + b["right"]) / 2, "center-y": (b["top"] + b["bottom"]) / 2,
            "width": b["width"], "height": b["height"], "x": b["left"], "y": b["top"]}


def _box_text(part: Part) -> str:
    e = _edges(part)
    return "not rendered" if e is None else f"@{fmt(e['x'])},{fmt(e['y'])} {fmt(e['width'])}x{fmt(e['height'])}"


def _fail(part: Part, kind: str, message: str, evidence: str | None = None) -> Diagnostic:
    return make("A001", "error", part.pointer, part.name, message, HINTS[kind], evidence)


def _pair(joined: Joined, subject: Part, other_name: str, kind: str,
          item: str) -> tuple[dict[str, float], dict[str, float], Part] | Diagnostic:
    other = _part(joined, other_name)
    if isinstance(other, str):
        return make("A003", "error", subject.pointer, subject.name, f"{item}: {other}",
                    "Use the name path of a component of this view (root/Panel/Label); pdk_inspect query=* lists them.")
    a, b = _edges(subject), _edges(other)
    if a is None or b is None:
        missing = subject if a is None else other
        return _fail(subject, kind, f"{item}: {missing.name} is not rendered")
    return a, b, other


def _side(joined: Joined, subject: Part, relation: str, args: list[str], item: str) -> Diagnostic | None:
    if not args:
        raise _Malformed(f"{relation} needs another component")
    gap: tuple[str, float, float | None] | None = None
    if len(args) >= 3 and args[1] == "gap" and relation != "inside":
        gap = _gap(args[2:], relation)
    elif len(args) != 1:
        raise _Malformed(f"expected `{relation} <name path>" + ("`" if relation == "inside" else
                                                                 " [gap [op] N | gap N..M]`"))
    pair = _pair(joined, subject, args[0], relation, item)
    if not isinstance(pair, tuple):
        return pair
    a, b, other = pair
    boxes = f"{subject.name} {_box_text(subject)}, {other.name} {_box_text(other)}"
    if relation == "inside":
        area, scrolled = _content_area(other, b)
        excess = max(area["left"] - a["left"], area["top"] - a["top"], a["right"] - area["right"],
                     a["bottom"] - area["bottom"])
        if excess <= EPSILON_PX:
            return None
        where = f" of {other.name}'s scrolled content" if scrolled else ""
        return _fail(subject, relation, f"{item}: it sticks out {fmt(excess)}px{where}", boxes)
    near, far, label = {
        "below": (a["top"], b["bottom"], "top {a} vs bottom {b}"),
        "above": (b["top"], a["bottom"], "bottom {a} vs top {b}"),
        "right-of": (a["left"], b["right"], "left {a} vs right {b}"),
        "left-of": (b["left"], a["right"], "right {a} vs left {b}"),
    }[relation]
    actual = near - far  # the space between them, negative when they overlap or are the other way round
    a_edge, b_edge = (near, far) if relation in ("below", "right-of") else (far, near)
    edges = label.format(a=fmt(a_edge), b=fmt(b_edge))
    if actual < -EPSILON_PX:
        return _fail(subject, relation, f"{item}: it is not ({edges})", boxes)
    if gap is not None:
        op, low, high = gap
        if high is not None:
            ok = low - EPSILON_PX <= actual <= high + EPSILON_PX
        else:
            ok = _compare(actual, op, low, EPSILON_PX)
        if not ok:
            return _fail(subject, relation, f"{item}: the gap is {fmt(actual)}px ({edges})", boxes)
    return None


def _gap(args: list[str], relation: str) -> tuple[str, float, float | None]:
    """`gap N`, `gap >= N` (any comparison) or `gap N..M` (inclusive)."""
    if len(args) == 1 and ".." in args[0]:
        low, high = (_number(v.removesuffix("px")) for v in args[0].split("..", 1))
        if low > high:
            raise _Malformed(f"gap {args[0]}: the range is upside down")
        return "=", low, high
    op, raw = _op_value(args, f"{relation} <name path> gap")
    return op, _number(raw.removesuffix("px")), None


def _content_area(part: Part, box: dict[str, float]) -> tuple[dict[str, float], bool]:
    """The box `inside` checks against: along an axis the component scrolls, its whole scrollable content."""
    dom = part.dom
    area = {k: box[k] for k in ("left", "top", "right", "bottom")}
    scrolled = False
    border = dom.get("border_width") or [0, 0, 0, 0]
    if scrolls(dom, "x") and dom["scroll_width"] - dom["client_width"] > EPSILON_PX:
        area["left"] = box["left"] + (border[3] or 0) - (dom.get("scroll_left") or 0)
        area["right"] = area["left"] + dom["scroll_width"]
        scrolled = True
    if scrolls(dom, "y") and dom["scroll_height"] - dom["client_height"] > EPSILON_PX:
        area["top"] = box["top"] + (border[0] or 0) - (dom.get("scroll_top") or 0)
        area["bottom"] = area["top"] + dom["scroll_height"]
        scrolled = True
    return area, scrolled


def _aligned(joined: Joined, subject: Part, args: list[str], item: str) -> Diagnostic | None:
    if len(args) != 2 or args[0] not in EDGES:
        raise _Malformed(f"expected `aligned {'|'.join(sorted(EDGES))} <name path>`")
    pair = _pair(joined, subject, args[1], "aligned", item)
    if not isinstance(pair, tuple):
        return pair
    a, b, other = pair
    edge = args[0]
    if abs(a[edge] - b[edge]) <= EPSILON_PX:
        return None
    return _fail(subject, "aligned", f"{item}: {edge} is {fmt(a[edge])} vs {fmt(b[edge])}",
                 f"{subject.name} {_box_text(subject)}, {other.name} {_box_text(other)}")


def _metric(joined: Joined, subject: Part, metric: str, args: list[str], item: str) -> Diagnostic | None:
    op, raw = _op_value(args, metric)
    edges = _edges(subject)
    if edges is None:
        return _fail(subject, metric, f"{item}: it is not rendered")
    parent = _parent(joined, subject)
    target = _length(joined, parent, metric, raw)
    actual = edges[metric]
    origin = ""
    if metric in ("x", "y"):
        actual -= _inner(joined, parent)[metric]
        origin = f" from {parent.name if parent else 'the viewport'}"
    if _compare(actual, op, target, EPSILON_PX):
        return None
    shown = f"{fmt(target)}px" + (f" ({raw})" if raw.endswith("%") else "")
    return _fail(subject, metric, f"{item}: {metric} is {fmt(actual)}px{origin}, expected {op} {shown}",
                 _box_text(subject))


def _parent(joined: Joined, subject: Part) -> Part | None:
    """The parent component, if it was rendered; None stands for the viewport."""
    parent_node = subject.node.parent
    parent = joined.by_name.get(parent_node.path) if parent_node else None
    return parent if parent is not None and parent.dom is not None else None


def _inner(joined: Joined, parent: Part | None) -> dict[str, float]:
    """The parent's padding box, where coord children's position.x/y start, in viewport px."""
    if parent is None:
        viewport = joined.viewport
        return {"x": 0.0, "y": 0.0, "width": viewport.get("width", 0), "height": viewport.get("height", 0)}
    dom = parent.dom
    b = dom["bounds"]
    border = dom.get("border_width") or [0, 0, 0, 0]
    return {"x": b["left"] + (border[3] or 0), "y": b["top"] + (border[0] or 0),
            "width": b["width"] - (border[1] or 0) - (border[3] or 0),
            "height": b["height"] - (border[0] or 0) - (border[2] or 0)}


def _length(joined: Joined, parent: Part | None, metric: str, raw: str) -> float:
    if not raw.endswith("%"):
        return _number(raw.removesuffix("px"))
    if metric in ("width", "height"):
        base = parent.dom["bounds"][metric] if parent is not None else joined.viewport.get(metric, 0)
    else:
        base = _inner(joined, parent)["width" if metric == "x" else "height"]
    return base * _number(raw[:-1]) / 100


def _text(subject: Part, args: list[str], item: str) -> Diagnostic | None:
    contains = bool(args) and args[0] == "~"
    values = args[1:] if contains else args
    if len(values) != 1:
        raise _Malformed('expected `text "..."` or `text ~ "..."` (quote text with spaces)')
    want = " ".join(values[0].split())
    if subject.dom is None:
        return _fail(subject, "text", f"{item}: it is not rendered")
    have = " ".join(str(subject.dom.get("text") or "").split())
    if (want in have) if contains else (want == have):
        return None
    return _fail(subject, "text", f"{item}: the text is {have!r}")


def _in_view(joined: Joined, subject: Part, item: str) -> Diagnostic | None:
    if not _shown(subject):
        return _fail(subject, "in-view", f"{item}: it is not shown ({_box_text(subject)})")
    dom = subject.dom
    b = dom["bounds"]
    clip = dom.get("clip_rect") or {}
    width, height = joined.viewport.get("width"), joined.viewport.get("height")
    sight = (max(clip.get("left", 0), 0), max(clip.get("top", 0), 0),
             min(clip.get("right", width or 1e6), width or 1e6), min(clip.get("bottom", height or 1e6), height or 1e6))
    hidden = max(sight[0] - b["left"], sight[1] - b["top"], b["right"] - sight[2], b["bottom"] - sight[3], 0.0)
    if hidden <= EPSILON_PX:
        return None
    return _fail(subject, "in-view", f"{item}: {fmt(hidden)}px of it is out of sight",
                 f"{_box_text(subject)} in sight {fmt(sight[0])},{fmt(sight[1])}-{fmt(sight[2])},{fmt(sight[3])}")


def _scrolls(subject: Part, axis: str | None, item: str) -> Diagnostic | None:
    dom = subject.dom
    if dom is None:
        return _fail(subject, "scrolls", f"{item}: it is not rendered")
    reasons = []
    for ax in [axis] if axis else ["x", "y"]:
        size, client = ("scroll_width", "client_width") if ax == "x" else ("scroll_height", "client_height")
        extra = dom[size] - dom[client]
        if scrolls(dom, ax) and extra > EPSILON_PX:
            return None
        if not scrolls(dom, ax):
            reasons.append(f"overflow-{ax} is {dom.get(f'overflow_{ax}')}")
        else:
            reasons.append(f"its content fits along {ax} ({fmt(dom[size])} in {fmt(dom[client])})")
    return _fail(subject, "scrolls", f"{item}: " + "; ".join(reasons))


def _color(subject: Part, relation: str, args: list[str], item: str) -> Diagnostic | None:
    if not args:
        raise _Malformed(f"{relation} needs a color")
    want = normalize_color(" ".join(args))
    if subject.dom is None:
        return _fail(subject, relation, f"{item}: it is not rendered")
    have = subject.dom.get("color" if relation == "color" else "background_color")
    if have is None:
        return _fail(subject, relation, f"{item}: {relation} was not measured (redeploy with a newer pdk)")
    if normalize_color(have) == want:
        return None
    return _fail(subject, relation, f"{item}: it is {have}")


def _font(subject: Part, relation: str, args: list[str], item: str) -> Diagnostic | None:
    if subject.dom is None:
        return _fail(subject, relation, f"{item}: it is not rendered")
    if relation == "font-weight":
        if len(args) != 1:
            raise _Malformed("expected `font-weight N`")
        want = WEIGHTS.get(args[0], args[0])
        have = str(subject.dom.get("font_weight"))
        return None if have == want else _fail(subject, relation, f"{item}: it is {have}")
    op, raw = _op_value(args, relation)
    target, have = _number(raw.removesuffix("px")), subject.dom.get("font_size")
    if have is not None and _compare(have, op, target, 0.5):
        return None
    return _fail(subject, relation, f"{item}: it is {fmt(have)}px")


def _op_value(args: list[str], what: str) -> tuple[str, str]:
    if len(args) == 1:
        return "=", args[0]
    if len(args) == 2 and args[0] in OPS:
        return args[0], args[1]
    raise _Malformed(f"expected `{what} [=|>=|<=|>|<] N`")


def _compare(actual: float, op: str, target: float, epsilon: float) -> bool:
    return {
        "=": abs(actual - target) <= epsilon,
        ">=": actual >= target - epsilon,
        "<=": actual <= target + epsilon,
        ">": actual > target,
        "<": actual < target,
    }[op]


def _number(raw: str) -> float:
    try:
        return float(raw)
    except ValueError:
        raise _Malformed(f"{raw!r} is not a number") from None


def normalize_color(value: str) -> str:
    """Computed-style form: rgb(r, g, b) or rgba(r, g, b, a); hex and opaque rgba are converted."""
    text = value.strip().lower().replace(" ", "")
    hexed = re.fullmatch(r"#([0-9a-f]{3,8})", text)
    if hexed:
        digits = hexed.group(1)
        if len(digits) in (3, 4):
            digits = "".join(c * 2 for c in digits)
        if len(digits) not in (6, 8):
            return text
        channels = [int(digits[i:i + 2], 16) for i in range(0, len(digits), 2)]
        if len(channels) == 4 and channels[3] < 255:
            alpha = round(channels[3] / 255, 3)
            return f"rgba({channels[0]},{channels[1]},{channels[2]},{fmt(alpha)})"
        return f"rgb({channels[0]},{channels[1]},{channels[2]})"
    rgba = re.fullmatch(r"rgba\((\d+),(\d+),(\d+),([\d.]+)\)", text)
    if rgba and float(rgba.group(4)) >= 1:
        return f"rgb({rgba.group(1)},{rgba.group(2)},{rgba.group(3)})"
    return {"transparent": "rgba(0,0,0,0)"}.get(text, text)


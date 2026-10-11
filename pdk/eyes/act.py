"""Actions on an open view before it is measured: click, type, choose dropdown options, hover, scroll, slide,
press keys, select a table row, wait (F5 part A), and set a simulated tag (part B).

Steps are written like expectations (`;` or newlines, components by name path):

    click root/Save; type root/Name "Pump 2"; row root/Table 1; wait 500; click popup:detail/root/Ok
    select root/Mode "Auto"; slide root/Speed 40; scroll root/Feed bottom; key root/Name Enter; hover root/Help
    tag Sandbox/Setpoint 42; tag Sandbox/Message "en marcha"; tag Sandbox/Setpoint quality Bad_Stale

Tag paths without [provider] hang from PDK/; the view gets the new value through its subscription.

They run in the browser in order; after each one the page settles like after opening it. A step that cannot be
done stops the sequence and comes back as A004 (with the reason); a step that cannot be read is A005.
"""

from __future__ import annotations

import json
import math
import shlex
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from pdk.diagnostics import Diagnostic, make
from pdk.eyes.expect import split
from pdk.validate.tree import Node

GRAMMAR = ('steps, ;-separated: `click <component>`, `type <component> "text"`, `row <table> N` (1 = first row '
           'shown), `select <dropdown> "Label" ["Label2" ...]`, `hover <component>`, `double-click <component>`, '
           '`scroll <component> down|up|top|bottom|<N>`, `slide <slider> <N>`, `key [<component>] <Key>` (Enter, '
           'Control+A), `wait <ms>`, `tag <path> <value> [quality <Code>]`, `tag <path> quality <Code>` (keeps the '
           'value); components by name path like root/Panel/Save, tags under PDK/ like Sandbox/Setpoint')
TAG_HINT = ("Simulated tags live under [provider]PDK/: create them first (pdk tags apply <file>) and check the path "
            "with pdk tags list; a quality name is like Good, Bad_Stale or Bad_Disabled.")
ROW = ".ia_table__body__row"
EDITABLE = "input:not([type=checkbox]):not([type=search]), textarea"
DROPDOWN_BOX = ".iaDropdownCommon"
DROPDOWN_PANEL = ".iaDropdownCommon_options"
DROPDOWN_OPTION = "a.iaDropdownCommon_option"
SLIDER_HANDLE = '[role="slider"]'
SCROLL_WORDS = ("down", "up", "top", "bottom")
SCROLL_JS = """(e, amount) => {
    const scrolls = el => el.scrollHeight > el.clientHeight + 1
        && ['auto', 'scroll'].includes(getComputedStyle(el).overflowY);
    const box = [e, ...e.querySelectorAll('*')].find(scrolls);
    if (!box) return { scrolled: false, top: 0 };
    if (amount === 'down') box.scrollTop += box.clientHeight;
    else if (amount === 'up') box.scrollTop -= box.clientHeight;
    else if (amount === 'top') box.scrollTop = 0;
    else if (amount === 'bottom') box.scrollTop = box.scrollHeight;
    else box.scrollTop += amount;
    return { scrolled: true, top: box.scrollTop };
}"""
POPUP_PREFIX = "popup:"
STEP_TIMEOUT_MS = 3000
MAX_WAIT_MS = 10000
MAX_SLIDE_PRESSES = 1000
POLL_MS = 50
SLIDE_WAIT_MS = 1000  # per press: the handle moves when the new value is back from the gateway
EQUAL_TOLERANCE = 1e-9  # slider values are floats
TAG_SETTLE_MS = 300  # the new value reaches the page through the session's subscription


@dataclass(frozen=True)
class Step:
    verb: str
    text: str  # as written, for the report
    component: str | None = None  # name path
    dom_path: str | None = None
    value: str | None = None  # typed text
    number: int = 0  # table row (1-based) or milliseconds
    tag: str | None = None  # tag path, as written
    quality: str | None = None  # quality written with the value
    labels: tuple[str, ...] = ()  # dropdown options to pick, in order
    key: str | None = None  # Playwright key name, as written (Enter, Control+A)
    scroll: str | int | None = None  # down, up, top or bottom, or pixels (negative = up)
    level: float | None = None  # slider value


@dataclass
class Outcome:
    total: int
    done: int
    diagnostics: list[Diagnostic]

    def summary(self) -> str:
        return f"act: {self.done}/{self.total} done"


class Malformed(ValueError):
    pass


def parse(text: str, nodes: dict[str, Node]) -> tuple[list[Step], list[Diagnostic]]:
    """Steps for the view's components (name path -> node); malformed steps come back as A005."""
    steps: list[Step] = []
    found: list[Diagnostic] = []
    for item in split(text):
        try:
            steps.append(_step(item, nodes))
        except Malformed as error:
            found.append(make("A005", "error", "", "", f"cannot read step {item!r}: {error}", GRAMMAR))
    return steps, found


def _step(item: str, nodes: dict[str, Node]) -> Step:
    try:
        words = shlex.split(item, posix=True)
    except ValueError as exc:
        raise Malformed(str(exc)) from exc
    verb, args = words[0].lower(), words[1:]
    if verb == "wait":
        if len(args) != 1 or not args[0].isdigit():
            raise Malformed("expected `wait <milliseconds>`")
        return Step("wait", item, number=min(int(args[0]), MAX_WAIT_MS))
    if verb == "tag":
        return _tag_step(item, args)
    if verb not in ("click", "type", "row", "select", "hover", "double-click", "scroll", "slide", "key"):
        raise Malformed(f"unknown action {verb!r}")
    if verb == "key":
        return _key_step(item, args, nodes)  # the component is optional
    if not args:
        raise Malformed(f"`{verb}` needs a component")
    name = args[0]
    dom_path = _dom_path(name, nodes)
    if verb == "click":
        if len(args) != 1:
            raise Malformed("expected `click <component>`")
        return Step("click", item, name, dom_path)
    if verb == "type":
        if len(args) != 2:
            raise Malformed('expected `type <component> "text"` (quote text with spaces)')
        return Step("type", item, name, dom_path, value=args[1])
    if verb == "row":
        if len(args) != 2 or not args[1].isdigit() or int(args[1]) < 1:
            raise Malformed("expected `row <table> N`, N from 1")
        return Step("row", item, name, dom_path, number=int(args[1]))
    if verb == "select":
        if len(args) < 2:
            raise Malformed('expected `select <dropdown> "Label" ["Label2" ...]` (quote labels with spaces)')
        return Step("select", item, name, dom_path, labels=tuple(args[1:]))
    if verb in ("hover", "double-click"):
        if len(args) != 1:
            raise Malformed(f"expected `{verb} <component>`")
        return Step(verb, item, name, dom_path)
    if verb == "scroll":
        amount = _scroll_amount(args[1]) if len(args) == 2 else None
        if amount is None:
            raise Malformed("expected `scroll <component> down|up|top|bottom|<N>` (N in pixels, may be negative)")
        return Step("scroll", item, name, dom_path, scroll=amount)
    level = _level(args[1]) if len(args) == 2 else None
    if level is None:
        raise Malformed("expected `slide <slider> <N>` with a number")
    return Step("slide", item, name, dom_path, level=level)


def _dom_path(name: str, nodes: dict[str, Node]) -> str | None:
    """The DOM path of a component; None for popup:<id>/... (resolved when the step runs: the popup is not open)."""
    if name.startswith(POPUP_PREFIX):
        return None
    if name in nodes:
        return f"C.{nodes[name].address}"
    raise Malformed(f"no component {name!r} in the view.json (name paths start at root/, or "
                    "popup:<id>/root/... inside an open popup)")


def _key_step(item: str, args: list[str], nodes: dict[str, Node]) -> Step:
    usage = "expected `key [<component>] <Key>` (a Playwright key name such as Enter, Escape or Control+A)"
    if len(args) == 1:
        if args[0].startswith(POPUP_PREFIX) or args[0] in nodes:
            raise Malformed(usage)  # a component without a key
        return Step("key", item, key=args[0])
    if len(args) != 2:
        raise Malformed(usage)
    return Step("key", item, args[0], _dom_path(args[0], nodes), key=args[1])


def _scroll_amount(text: str) -> str | int | None:
    """down, up, top or bottom (lower case), or a pixel count (negative = up); None for anything else."""
    if text.lower() in SCROLL_WORDS:
        return text.lower()
    try:
        return int(text)
    except ValueError:
        return None


def _level(text: str) -> float | None:
    try:
        level = float(text)
    except ValueError:
        return None
    return level if math.isfinite(level) else None


def _tag_step(item: str, args: list[str]) -> Step:
    if len(args) == 4 and args[2] == "quality":
        return Step("tag", item, value=args[1], tag=args[0], quality=args[3])
    if len(args) == 3 and args[1] == "quality":
        return Step("tag", item, tag=args[0], quality=args[2])  # value None: keep the current one
    if len(args) == 2:
        return Step("tag", item, value=args[1], tag=args[0])
    raise Malformed('expected `tag <path> <value> [quality <Code>]` or `tag <path> quality <Code>` (quote text '
                    'with spaces)')


def run(page: Any, steps: list[Step], settle: Any, pointers: dict[str, str],
        resolve: Callable[[str], str | None] | None = None,
        tags: Callable[[Step], Any] | None = None) -> Outcome:
    """Perform the steps on the worker thread; `settle(page)` waits for a stable layout after each one,
    `resolve(name)` gives the DOM path of a popup:<id>/... component once its popup is open, and `tags(step)`
    writes a tag step through the gateway."""
    for done, step in enumerate(steps):
        if step.verb == "tag":
            try:
                if tags is None:
                    raise RuntimeError("no gateway to write tags through")
                tags(step)
                page.wait_for_timeout(TAG_SETTLE_MS)
                settle(page)
            except Exception as exc:  # noqa: BLE001  (gateway errors carry the reason we report)
                return Outcome(len(steps), done, [make(
                    "A004", "error", "", "", f"step {done + 1} `{step.text}` failed: {_tag_reason(exc)}", TAG_HINT)])
            continue
        try:
            if step.dom_path is None and step.component:
                dom_path = resolve(step.component) if resolve else None
                if dom_path is None:
                    raise RuntimeError(f"no open popup has {step.component.removeprefix(POPUP_PREFIX)!r}")
                step = replace(step, dom_path=dom_path)
            _perform(page, step)
            settle(page)
        except Exception as exc:  # noqa: BLE001  (Playwright errors carry the reason we report)
            reason = _reason(exc)
            pointer = pointers.get(step.component or "", "")
            return Outcome(len(steps), done, [make(
                "A004", "error", pointer, step.component or "", f"step {done + 1} `{step.text}` failed: {reason}",
                "Check that the component is rendered and enabled at that point (inspect without --act shows the "
                "starting state); a disabled control or a missing row is the view's state, not a harness error.")])
    return Outcome(len(steps), len(steps), [])


def _perform(page: Any, step: Step) -> None:
    if step.verb == "wait":
        page.wait_for_timeout(step.number)
        return
    if step.verb == "key" and step.component is None:
        page.keyboard.press(step.key or "")  # no component: the key goes to the focused element
        return
    target = page.locator(f'[data-component-path="{step.dom_path}"]')
    if target.count() == 0:
        raise RuntimeError("it is not rendered")
    if step.verb == "click":
        # A checkbox's input is visually hidden behind its drawn box: click the label that wraps it.
        box = target.locator("label:has(input[type=checkbox])")
        (box.first if box.count() else target).click(timeout=STEP_TIMEOUT_MS)
    elif step.verb == "row":
        rows = target.locator(ROW)
        if rows.count() < step.number:
            raise RuntimeError(f"the table shows {rows.count()} row(s)")
        rows.nth(step.number - 1).click(timeout=STEP_TIMEOUT_MS)
    elif step.verb == "select":
        _select(page, target, step.labels)
    elif step.verb == "key":
        _key(target, step.key or "")
    elif step.verb == "hover":
        target.hover(timeout=STEP_TIMEOUT_MS)
    elif step.verb == "double-click":
        target.dblclick(timeout=STEP_TIMEOUT_MS)
    elif step.verb == "scroll":
        _scroll(target, step.scroll)
    elif step.verb == "slide":
        _slide(page, target, step.level or 0.0)
    else:
        target.click(timeout=STEP_TIMEOUT_MS)  # numeric entry fields only become editable when clicked
        editable = _editable(target)
        if editable.count() == 0:
            raise RuntimeError("it has no text input")
        editable.fill(step.value or "", timeout=STEP_TIMEOUT_MS)
        editable.press("Tab")  # the blur commits deferred values


def _editable(target: Any) -> Any:
    """The element itself when it is an input or textarea, else its first text input (which may not exist)."""
    if target.evaluate("e => e.matches('input, textarea')"):
        return target
    return target.locator(EDITABLE).first


def _key(target: Any, key: str) -> None:
    field = _editable(target)
    if field.count() == 0:
        field = target  # not a text input: the key goes to the component itself
    field.press(key, timeout=STEP_TIMEOUT_MS)


def _select(page: Any, target: Any, labels: tuple[str, ...]) -> None:
    """Picks each label in order (reopening the list when it closed), then checks what the box shows."""
    box = target if target.evaluate("e => e.matches('.iaDropdownCommon')") else target.locator(DROPDOWN_BOX).first
    for label in labels:
        if not _dropdown_open(page):
            box.click(timeout=STEP_TIMEOUT_MS)
            page.locator(DROPDOWN_PANEL).first.wait_for(state="visible", timeout=STEP_TIMEOUT_MS)
        option = page.locator(f'{DROPDOWN_OPTION}[data-label="{_quote(label)}"]')  # the list may be in a portal
        if option.count() == 0:
            shown = page.locator(DROPDOWN_OPTION).evaluate_all("els => els.map(e => e.dataset.label)")
            raise RuntimeError(f"no option {label!r} (options: {', '.join(str(s) for s in shown[:10])})")
        option.first.click(timeout=STEP_TIMEOUT_MS)  # the component selects on mousedown, which a click includes
    if _dropdown_open(page):
        box.press("Escape", timeout=STEP_TIMEOUT_MS)

    def shown() -> list[str]:
        raw = box.get_attribute("data-selected-labels") or target.get_attribute("data-selected-labels")
        return json.loads(raw or "[]")

    # The box shows the new value once it has gone through the gateway and back: wait for it.
    selected = _poll(page, shown, lambda now: all(label in now for label in labels))
    if any(label not in selected for label in labels):
        raise RuntimeError(f"the dropdown shows {selected} after selecting {list(labels)}")


def _poll(page: Any, read: Any, done: Any, timeout_ms: int = STEP_TIMEOUT_MS) -> Any:
    """`read()` until `done(value)` or the timeout; the last value read."""
    value = read()
    for _ in range(max(1, timeout_ms // POLL_MS)):
        if done(value):
            break
        page.wait_for_timeout(POLL_MS)
        value = read()
    return value


def _dropdown_open(page: Any) -> bool:
    panel = page.locator(DROPDOWN_PANEL).first
    return panel.count() > 0 and panel.is_visible()


def _quote(text: str) -> str:
    """Escapes a value for a double-quoted CSS attribute selector."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _scroll(target: Any, amount: str | int | None) -> None:
    result = target.evaluate(SCROLL_JS, amount)  # the element, or its first descendant that scrolls
    if not result["scrolled"]:
        raise RuntimeError("it has nothing to scroll")


def _slide(page: Any, target: Any, level: float) -> None:
    """Moves the handle with the keys (Home or End for an end of the range, else arrows one step at a time) until
    aria-valuenow reads `level`; a step that passes it means `level` is not one of the slider's steps."""
    handle = target.locator(SLIDER_HANDLE).first
    if handle.count() == 0:
        raise RuntimeError("it has no slider handle")
    low, high = _aria(handle, "aria-valuemin"), _aria(handle, "aria-valuemax")
    if not low <= level <= high:
        raise RuntimeError(f"{level:g} is outside {low:g}..{high:g}")
    handle.focus(timeout=STEP_TIMEOUT_MS)
    start = now = _aria(handle, "aria-valuenow")
    if _same(now, level):
        return
    if _same(level, low) or _same(level, high):
        key = "Home" if _same(level, low) else "End"
    else:
        key = "ArrowRight" if level > now else "ArrowLeft"
    for _ in range(MAX_SLIDE_PRESSES):
        handle.press(key, timeout=STEP_TIMEOUT_MS)
        # The handle follows the value once it is back from the gateway.
        after = _poll(page, lambda: _aria(handle, "aria-valuenow"), lambda value, was=now: not _same(value, was),
                      SLIDE_WAIT_MS)
        if _same(after, now):
            raise RuntimeError("the slider does not move (disabled?)")
        if _same(after, level):
            return
        if (key == "ArrowRight" and after > level) or (key == "ArrowLeft" and after < level):
            raise RuntimeError(f"the slider goes from {start:g} to {after:g}: {level:g} is not one of its steps")
        now = after
    raise RuntimeError(f"the slider does not reach {level:g} in {MAX_SLIDE_PRESSES} presses")


def _aria(handle: Any, name: str) -> float:
    text = handle.get_attribute(name)
    if text is None:
        raise RuntimeError(f"the slider has no {name}")
    return float(text)


def _same(a: float, b: float) -> bool:
    return abs(a - b) < EQUAL_TOLERANCE


def _tag_reason(exc: Exception) -> str:
    """A gateway error's own message (with its G-code), not the Playwright reading of _reason."""
    code = getattr(exc, "code", None)
    text = str(getattr(exc, "message", None) or exc).strip()
    text = text.splitlines()[0] if text else type(exc).__name__
    return f"{code} {text}"[:300] if code else text[:300]


def _reason(exc: Exception) -> str:
    text = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
    if type(exc) is RuntimeError:  # a reason this module wrote, already plain (it may say "disabled")
        return text[:200]
    if "not enabled" in str(exc) or "disabled" in str(exc).lower():
        return "the control is disabled"
    if "Timeout" in type(exc).__name__ or "Timeout" in text:
        return f"it did not become clickable in {STEP_TIMEOUT_MS} ms ({text[:120]})"
    return text[:200]

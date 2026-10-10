"""Actions on an open view before it is measured: click, type, select a table row, wait (F5 part A), and set a
simulated tag (part B).

Steps are written like expectations (`;` or newlines, components by name path):

    click root/Save; type root/Name "Pump 2"; row root/Table 1; wait 500; click popup:detail/root/Ok
    tag Sandbox/Setpoint 42; tag Sandbox/Message "en marcha"; tag Sandbox/Setpoint quality Bad_Stale

Tag paths without [provider] hang from PDK/; the view gets the new value through its subscription.

They run in the browser in order; after each one the page settles like after opening it. A step that cannot be
done stops the sequence and comes back as A004 (with the reason); a step that cannot be read is A005.
"""

from __future__ import annotations

import shlex
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from pdk.diagnostics import Diagnostic, make
from pdk.eyes.expect import split
from pdk.validate.tree import Node

GRAMMAR = ('steps, ;-separated: `click <component>`, `type <component> "text"`, `row <table> N` (1 = first row '
           "shown), `wait <ms>`, `tag <path> <value> [quality <Code>]`, `tag <path> quality <Code>` (keeps the "
           "value); components by name path like root/Panel/Save, tags under PDK/ like Sandbox/Setpoint")
TAG_HINT = ("Simulated tags live under [provider]PDK/: create them first (pdk tags apply <file>) and check the path "
            "with pdk tags list; a quality name is like Good, Bad_Stale or Bad_Disabled.")
ROW = ".ia_table__body__row"
EDITABLE = "input:not([type=checkbox]):not([type=search]), textarea"
POPUP_PREFIX = "popup:"
STEP_TIMEOUT_MS = 3000
MAX_WAIT_MS = 10000
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
    if verb not in ("click", "type", "row"):
        raise Malformed(f"unknown action {verb!r}")
    if not args:
        raise Malformed(f"`{verb}` needs a component")
    name = args[0]
    if name.startswith(POPUP_PREFIX):
        dom_path = None  # resolved when the step runs: the popup is not open yet
    elif name in nodes:
        dom_path = f"C.{nodes[name].address}"
    else:
        raise Malformed(f"no component {name!r} in the view.json (name paths start at root/, or "
                        "popup:<id>/root/... inside an open popup)")
    if verb == "click":
        if len(args) != 1:
            raise Malformed("expected `click <component>`")
        return Step("click", item, name, dom_path)
    if verb == "type":
        if len(args) != 2:
            raise Malformed('expected `type <component> "text"` (quote text with spaces)')
        return Step("type", item, name, dom_path, value=args[1])
    if len(args) != 2 or not args[1].isdigit() or int(args[1]) < 1:
        raise Malformed("expected `row <table> N`, N from 1")
    return Step("row", item, name, dom_path, number=int(args[1]))


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
    else:
        target.click(timeout=STEP_TIMEOUT_MS)  # numeric entry fields only become editable when clicked
        editable = target if target.evaluate("e => e.matches('input, textarea')") else target.locator(EDITABLE).first
        if editable.count() == 0:
            raise RuntimeError("it has no text input")
        editable.fill(step.value or "", timeout=STEP_TIMEOUT_MS)
        editable.press("Tab")  # the blur commits deferred values


def _tag_reason(exc: Exception) -> str:
    """A gateway error's own message (with its G-code), not the Playwright reading of _reason."""
    code = getattr(exc, "code", None)
    text = str(getattr(exc, "message", None) or exc).strip()
    text = text.splitlines()[0] if text else type(exc).__name__
    return f"{code} {text}"[:300] if code else text[:300]


def _reason(exc: Exception) -> str:
    text = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
    if "not enabled" in str(exc) or "disabled" in str(exc).lower():
        return "the control is disabled"
    if "Timeout" in type(exc).__name__ or "Timeout" in text:
        return f"it did not become clickable in {STEP_TIMEOUT_MS} ms ({text[:120]})"
    return text[:200]

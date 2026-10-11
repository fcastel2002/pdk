"""Open, inspect and screenshot a deployed view: the eyes' entry points for the CLI and the MCP server.

The view.json joined with the page is the one the gateway has (what is rendered), not the local copy; when the
local copy differs, the report says the edits are not deployed. The gateway log cursor is taken before the page
(re)loads, so startup scripts that fail are caught. Bindings start Uncertain until their first value arrives: the
gateway tree is read again for a short while until no quality is Uncertain.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pdk import sync
from pdk import tags as simulated
from pdk.diagnostics import DEFAULT_BUDGET, counts, render
from pdk.eyes import act as actions
from pdk.eyes import expect as expectations
from pdk.eyes import measure, rules
from pdk.eyes.browser import Eyes, EyesError, Opened, Viewport
from pdk.eyes.expect import page_location
from pdk.eyes.join import Joined, join, mounted_view
from pdk.gateway.client import VIEWS, Gateway, GatewayError
from pdk.validate.tree import build_tree, walk

SETTLE_S = 3.0
POLL_S = 0.25
LOG_GRACE_S = 0.3  # log events are appended asynchronously; give the last ones a moment
OPEN_HINTS = {
    "gateway-unavailable": "Start the gateway, or check PDK_GATEWAY_URL.",
    "http-error": "Check the project name and that the preview page exists (deploy the view again).",
    "view-not-found": "Deploy the view (pdk_deploy): the preview page points at a view the gateway does not have.",
    "session-refused": "Maker caps concurrent sessions: close other Perspective tabs, or wait for idle ones to time "
                       "out.",
    "disconnected": "The gateway dropped the session: check it is running, then inspect again.",
    "readiness-timeout": "The view did not render in time: check the gateway log and the browser console.",
    "layout-unstable": "Something keeps moving (an animation or a growing component): inspect again, or look at "
                       "the component named in the message.",
    "browser-crash": "Inspect again; if it repeats, update Playwright's Chromium (uv run playwright install chromium).",
    "browser-missing": "uv sync, then uv run playwright install chromium.",
}


@dataclass(frozen=True)
class Target:
    project: str
    path: str
    view: dict[str, Any]  # the gateway's view.json
    note: str | None  # e.g. local edits not deployed


@dataclass(frozen=True)
class Result:
    ok: bool
    text: str
    errors: int = 0


def resolve(gateway: Gateway, location: str) -> Target:
    """A local view (folder or view.json under work/) or a view path in the sandbox, read from the gateway."""
    local = Path(location)
    directory: Path | None = None
    if local.exists():
        directory, _, project, path = sync._target(local, None)
    else:
        project, path = sync._sandbox(gateway), location.strip("/")
        candidate = sync.local_dir(project, path)
        directory = candidate if (candidate / sync.VIEW_FILE).is_file() else None
    state = gateway.resource(project, VIEWS, path)
    if state.get("folder"):
        raise ValueError(f"{project}:{path} is a folder; inspect one view")
    text = state.get("files", {}).get(sync.VIEW_FILE)
    view = json.loads(text) if text else {}
    note = None
    if directory is not None:
        try:
            mine = json.loads((directory / sync.VIEW_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            mine = None
        if mine is not None and mine != view:
            note = "the local copy has edits that are not deployed: this inspects the gateway version"
    return Target(project, path, view, note)


def session_closer(gateway: Gateway) -> Any:
    """Eyes' `release`: close the sandbox sessions of the browser's pages on the gateway (best effort, so a module
    without the route only leaves them to time out)."""
    def release(page_ids: list[str]) -> None:
        try:
            project = gateway.health().get("sandbox")
        except GatewayError:
            return
        for page_id in page_ids:
            try:
                gateway.close_session(project, page_id)
            except GatewayError:
                continue
    return release


def _preview(gateway: Gateway, target: Target) -> str:
    sandbox = gateway.health().get("sandbox")
    if target.project != sandbox:
        raise ValueError(f"only views of the sandbox ({sandbox}) have a preview page; promote copies stay unopened")
    warning = sync.ensure_preview(gateway, target.project, target.path)
    if warning:
        raise ValueError(f"no preview page: {warning}")
    return sync.preview_url(gateway.url, target.project, target.path)


def _endless_note(opened: Opened) -> str:
    """A plain note, not a diagnostic: the stability wait did not wait for these infinite animations."""
    return (f"note: {len(opened.endless)} endless animation(s) left out of the stability wait "
            f"({', '.join(opened.endless[:5])})")


def _failed(target: Target | None, location: str, error: Exception) -> Result:
    where = f"{target.project}:{target.path}" if target else location
    if isinstance(error, EyesError):
        return Result(False, f"{where}: could not open the view ({error.status}): {error} | hint: "
                             f"{OPEN_HINTS.get(error.status, 'Inspect again.')}")
    return Result(False, f"{where}: {error}")


def _settled_page(gateway: Gateway, project: str, page_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + SETTLE_S
    while True:
        page = gateway.page(project, page_id)
        pending = any(q.get("level") == "Uncertain" for q in _all_qualities(page))
        if not pending or time.monotonic() >= deadline:
            return page
        time.sleep(POLL_S)


def _all_qualities(page: dict[str, Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []

    def visit(node: dict[str, Any]) -> None:
        found.extend(node.get("qualities", []))
        for child in node.get("children", []):
            visit(child)

    for view in page.get("views", []):
        found.extend(view.get("qualities", []))
        if isinstance(view.get("root"), dict):
            visit(view["root"])
    return found


@dataclass(frozen=True)
class Popup:
    id: str
    path: str  # its view
    view: dict[str, Any]
    extracted: dict[str, Any]


def _popup_resolver(gateway: Gateway, project: str, opened: Opened) -> Any:
    """DOM path of popup:<id>/<name path>, read from the popup's view when a step needs it."""
    def resolve(name: str) -> str | None:
        pid, _, path = name.removeprefix("popup:").partition("/")
        page = gateway.page(project, opened.page_id or "")
        view_path = next((v.get("resourcePath") for v in page.get("views", []) if v.get("mountPath") == f"P{pid}"),
                         None)
        text = gateway.resource(project, VIEWS, view_path).get("files", {}).get(sync.VIEW_FILE) if view_path else None
        if not text:
            return None
        node = next((n for n in walk(build_tree(json.loads(text))) if n.path == path), None)
        return f"P{pid}.{node.address}" if node else None
    return resolve


def _mounted(events: list[dict[str, Any]], path: str, mount: str) -> list[dict[str, Any]]:
    """The log events of the view instance `path@mount` (Perspective's MDC names it so)."""
    instance = f"{path}@{mount}"
    return [e for e in events if str((e.get("mdc") or {}).get("view", "")).startswith(instance)]


def _open_popups(eyes: Eyes, gateway: Gateway, opened: Opened, project: str, page: dict[str, Any] | None,
                 reasons: list[str]) -> list[Popup]:
    """The popups open on the page with their view.json, so the rules and expectations reach their components."""
    found: list[Popup] = []
    for pid in eyes.popups(opened):
        mount = f"P{pid}"
        path = next((v.get("resourcePath") for v in (page or {}).get("views", []) if v.get("mountPath") == mount),
                    None)
        if not path:
            reasons.append(f"popup {pid} is open but the gateway does not say which view it shows: not inspected")
            continue
        text = gateway.resource(project, VIEWS, path).get("files", {}).get(sync.VIEW_FILE)
        if not text:
            reasons.append(f"popup {pid} shows {path}, which the gateway does not return: not inspected")
            continue
        found.append(Popup(pid, path, json.loads(text), eyes.extract(opened, f"{mount}.0")))
    return found


def open_view(eyes: Eyes, gateway: Gateway, location: str, viewport: str | None = None) -> Result:
    """Open the view's preview in a fresh tab and report whether it renders."""
    target = None
    try:
        target = resolve(gateway, location)
        opened = eyes.open(_preview(gateway, target), Viewport.parse(viewport))
        extracted = eyes.extract(opened)
    except (EyesError, GatewayError, ValueError) as error:
        return _failed(target, location, error)
    expected = len(walk(build_tree(target.view)))
    shown = sum(1 for node in extracted["nodes"] if "$" not in node["path"] and node["path"].startswith("C.0"))
    errors = [entry for entry in extracted["console"] if entry["type"] in ("error", "pageerror")]
    lines = [f"{target.project}:{target.path} open @{opened.viewport} in {opened.readiness_ms:.0f} ms "
             f"(page {opened.page_id}): {shown}/{expected} components rendered, {len(errors)} browser errors",
             f"url: {opened.url}",
             "next: pdk_inspect for anomalies, pdk_screenshot to look at it"]
    if target.note:
        lines.insert(1, f"note: {target.note}")
    if opened.endless:
        lines.insert(1, _endless_note(opened))
    return Result(True, "\n".join(lines))


def inspect_view(eyes: Eyes, gateway: Gateway, location: str, viewport: str | None = None, *,
                 focus: str | None = None, budget: int = DEFAULT_BUDGET, query: str | None = None,
                 expect: str | None = None, act: str | None = None) -> Result:
    """Reload the view and report its anomalies: rendering, gateway qualities, logged script errors, console; with
    `act`, after performing those steps (pdk.eyes.act); with `query`, also what was measured of the matching
    components (measure.describe); with `expect`, the expectations that fail, as A diagnostics (expect.check)."""
    target = None
    try:
        target = resolve(gateway, location)
        url = _preview(gateway, target)
        health = gateway.health()
        reasons: list[str] = []
        cursor = None
        if health.get("logs"):
            cursor = gateway.logs(limit=1).get("last", 0)
        else:
            reasons.append("the gateway log tap is not attached: script errors in events are not seen")
        acted: actions.Outcome | None = None
        steps: list[actions.Step] = []
        if act:
            nodes = {node.path: node for node in walk(build_tree(target.view))}
            steps, malformed = actions.parse(act, nodes)
            if malformed:
                acted = actions.Outcome(len(steps) + len(malformed), 0, malformed)
        opened = eyes.open(url, Viewport.parse(viewport))
        if steps and acted is None:
            pointers = {node.path: node.pointer for node in walk(build_tree(target.view))}
            acted = eyes.act(opened, steps, pointers, _popup_resolver(gateway, target.project, opened),
                             lambda step: simulated.write(gateway, step.tag, step.value, step.quality))
        page: dict[str, Any] | None = None
        if health.get("runtime"):
            try:
                page = _settled_page(gateway, target.project, opened.page_id or "")
            except GatewayError as error:
                reasons.append(f"no gateway tree for page {opened.page_id} ({error.code}): qualities come from the "
                               "DOM overlays only")
        else:
            reasons.append("Perspective's session API is unavailable: qualities come from the DOM overlays only")
        view, view_path, navigated = target.view, target.path, None
        try:
            extracted = eyes.extract(opened)
        except EyesError:
            where = eyes.location(opened) if steps else ""
            if not where or page_location(where).startswith("/"):
                raise
            # A step left Perspective (a nav action with a url): only the page and the console can be read.
            extracted = {"url": where, "viewport": {}, "nodes": [], "console": list(opened.console)}
            view, view_path, navigated = {}, "", f"a step left Perspective for {where}"
        routed = mounted_view(page)
        if steps and routed and routed != target.path:
            text = gateway.resource(target.project, VIEWS, routed).get("files", {}).get(sync.VIEW_FILE)
            if text:
                view, view_path = json.loads(text), routed
                navigated = (f"a step navigated from {target.path} to {routed} "
                             f"(page {page_location(extracted.get('url'))}): this reports {routed}")
        popups = _open_popups(eyes, gateway, opened, target.project, page, reasons)
        events: list[dict[str, Any]] = []
        if cursor is not None:
            time.sleep(LOG_GRACE_S)
            for path in dict.fromkeys([view_path or target.path, *(popup.path for popup in popups)]):
                events += gateway.logs(since=cursor, project=target.project, view=path).get("events", [])
    except (EyesError, GatewayError, ValueError) as error:
        return _failed(target, location, error)
    if view:
        joined = join(view, extracted, page, view_path)
        if page is not None and joined.live_view is None:
            reasons.append(f"the gateway does not list {view_path} on page {opened.page_id}")
        diagnostics = rules.evaluate(joined, logs=_mounted(events, view_path, "C"),
                                     console_entries=extracted["console"], degraded_reasons=reasons)
    else:
        joined = Joined([], {}, None, {}, url=str(extracted["url"]))
        diagnostics = rules.evaluate(joined, console_entries=extracted["console"], degraded_reasons=reasons)
    components = len(joined.parts)
    for popup in popups:
        inside = join(popup.view, popup.extracted, page, popup.path, mount=f"P{popup.id}",
                      prefix=f"popup:{popup.id}/")
        diagnostics += rules.evaluate(inside, logs=_mounted(events, popup.path, f"P{popup.id}"))
        joined.popups[popup.id] = popup.path
        joined.parts += inside.parts
        joined.by_dom.update(inside.by_dom)
        joined.by_name.update(inside.by_name)
    if acted:
        diagnostics += acted.diagnostics
    outcome = (expectations.check(joined, expect, simulated.Probe(gateway))
               if expect and not joined.mismatches else None)
    if outcome:
        diagnostics += outcome.diagnostics
    title = f"{target.project}:{view_path or target.path} @{opened.viewport} ({components} components"
    title += f", {joined.foreign} embedded)" if joined.foreign else ")"
    if joined.popups:
        title += " + popups " + ", ".join(f"{pid} ({path})" for pid, path in joined.popups.items())
    text = render(diagnostics, title=title, budget=budget, focus=focus)
    if acted:
        text += "\n" + acted.summary()
    if outcome:
        text += "\n" + outcome.summary()
    elif expect:
        text += "\nexpect: not checked, the page does not render this view.json"
    if query:
        text += "\n" + measure.describe(joined, query)
    if navigated:
        text += f"\nnote: {navigated}"
    if target.note:
        text += f"\nnote: {target.note}"
    if opened.endless:
        text += "\n" + _endless_note(opened)
    errors = counts(diagnostics)["error"]
    return Result(errors == 0, text, errors)


def screenshot(eyes: Eyes, gateway: Gateway, location: str, viewport: str | None = None,
               component: str | None = None, act: str | None = None) -> tuple[bytes | None, str]:
    """PNG of the view (opened afresh) or of one component, by name path (root/Panel/Label), DOM path (C.0:1) or,
    once `act` has opened it, popup:<id>/root/... With `act`, the steps (pdk.eyes.act) run before capturing: a step
    that cannot be read comes back as A005, one that cannot be done as A004, and neither gives a PNG."""
    target = None
    acted: actions.Outcome | None = None
    try:
        target = resolve(gateway, location)
        nodes = {node.path: node for node in walk(build_tree(target.view))}
        popup_component = bool(component) and component.startswith(actions.POPUP_PREFIX)
        dom_path = None
        if component and not popup_component:
            if component.startswith("C."):
                dom_path = component
            elif component in nodes:
                dom_path = f"C.{nodes[component].address}"
            else:
                return None, f"{target.project}:{target.path} has no component {component!r} (use a name path " \
                             "like root/Panel/Label, or popup:<id>/root/... after act opened the popup)"
        steps: list[actions.Step] = []
        if act:
            steps, malformed = actions.parse(act, nodes)
            if malformed:
                return None, render(malformed, title=f"{target.project}:{target.path}")
        opened: Opened = eyes.open(_preview(gateway, target), Viewport.parse(viewport))
        resolve_popup = _popup_resolver(gateway, target.project, opened)
        if steps:
            pointers = {path: node.pointer for path, node in nodes.items()}
            acted = eyes.act(opened, steps, pointers, resolve_popup,
                             lambda step: simulated.write(gateway, step.tag, step.value, step.quality))
            if acted.diagnostics:
                return None, render(acted.diagnostics, title=f"{target.project}:{target.path}") + "\n" + acted.summary()
        if popup_component:
            dom_path = resolve_popup(component)
            if dom_path is None:
                return None, f"{target.project}:{target.path} has no component {component!r} in an open popup " \
                             "(act must open it first, e.g. click root/Open)"
        png = eyes.screenshot(opened, dom_path)
    except (EyesError, GatewayError, ValueError) as error:
        return None, _failed(target, location, error).text
    what = f"{component} ({dom_path})" if component else "the viewport"
    text = f"{target.project}:{target.path} @{opened.viewport}: {what}, {len(png)} bytes"
    return png, text + ("\n" + acted.summary() if acted else "")

"""MCP server (stdio): exact validation and catalog for Perspective view.json, signed writes to the sandbox, and
eyes on the deployed view (one headless Chromium kept for the life of the server)."""

from __future__ import annotations

import atexit
import os
import threading
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import Image, MCPServer
from mcp.types import ToolAnnotations

from pdk import __version__
from pdk.bundle import BundleError, load_bundle
from pdk.catalog import catalog
from pdk.gateway.client import Gateway, GatewayError
from pdk.report import report_paths, report_text, resolve_project

INSTRUCTIONS = (
    "Perspective DevKit: validates Ignition Perspective view.json against the exact schemas of the installed "
    "Perspective version. Flow: pdk_catalog to look up components, props, bindings and events -> write the "
    "view.json -> pdk_validate -> fix every error -> repeat until clean. Diagnostics read "
    "'E|W|I CODE /json/pointer (component path) | message | hint'; pass focus=<code|component|pointer> for detail. "
    "To put a view on the development gateway: pdk_pull (or write work/<project>/PDK/<name>/view.json) -> edit -> "
    "pdk_deploy (validates, writes with the signature of the pull, prints a preview URL). A G008 conflict means "
    "someone (often the Designer) changed it: read the diff, merge from view.gateway.json, deploy with "
    "resolved=true. pdk_promote moves PDK/X to X (to_project='target' sends it to the final project, "
    "only with the user's OK); pdk_delete removes a pulled view. After a deploy, look at it: "
    "pdk_inspect reloads the view in headless Chromium and reports anomalies on the component that has them "
    "(V layout: clipped text, overflow, collapsed; Q binding quality like Bad_NotFound or Error_ScriptEval; "
    "L script errors logged by the gateway; C browser errors). Fix, deploy, inspect again until clean. "
    "To confirm it looks as asked, pass expect= (e.g. 'root/Body below root/Header; root/Header width 100%'): "
    "failures come back as A001 with the measured values; query= shows what was measured. "
    "pdk_screenshot shows the view or one component, for what only an image can judge. "
    "Simulated tags (memory tags under [provider]PDK/) come from pdk_tags (apply a tag export JSON, write with a "
    "quality, read back, alarms); in pdk_inspect, act 'tag Sandbox/Setpoint 42' sets one and expect "
    "'tag Sandbox/Setpoint = 42; alarm North/Level active' checks the result."
)
READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
# ensure_preview adds page-config on the gateway: additive and idempotent, not read-only
PREVIEW_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)
# act runs the view's event scripts on the gateway, which can write tags or databases
ACTS = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False)
LOCAL_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)
SIGNED_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False)
RESOLVED = ("resolved=true only after a G008 conflict, once you merged the gateway version (view.gateway.json): it "
            "writes over that version.")

server = MCPServer("pdk", instructions=INSTRUCTIONS, version=__version__)


@server.tool(
    name="pdk_validate",
    description=(
        "Validate a Perspective view.json (file, directory of views, or inline JSON) against the exact schemas of "
        "the installed Perspective. Reports only problems: unknown or misspelled props, wrong types/enums, position "
        "keys of the wrong container, malformed bindings/transforms, events and scopes, broken property paths, "
        "missing views/params. Args: path (view.json or directory) or view (JSON text); project (project dir or "
        "local gateway project name, enables view/param/style-class checks; auto-detected inside a project tree); "
        "focus (code like P001, component path like root/Panel, or pointer prefix) to expand details; "
        "profile='hmi' for design-token lint."
    ),
    annotations=READ_ONLY,
    structured_output=False,
)
def pdk_validate(
    path: str | None = None,
    view: str | None = None,
    project: str | None = None,
    focus: str | None = None,
    profile: str | None = None,
) -> str:
    try:
        bundle = load_bundle()
        resolved_project = resolve_project(project)
        if view is not None:
            return report_text(view, bundle, project=resolved_project, profile=profile, focus=focus)[0]
        if not path:
            return "Pass path (a view.json or a directory) or view (the JSON text)."
        target = Path(path)
        if not target.exists():
            return f"{target} does not exist (relative paths resolve against {Path.cwd()})."
        return report_paths(target, bundle, project=resolved_project, profile=profile, focus=focus)[0]
    except (BundleError, FileNotFoundError) as exc:
        return f"pdk: {exc}"


@server.tool(
    name="pdk_catalog",
    description=(
        "Look up what the installed Perspective version supports, from its own schemas. No args: all component "
        "types by category. type: props (type, default, description), component events and, for containers, the "
        "position keys of their children. type+prop: a nested prop (e.g. prop='columns[]'). search: find a "
        "component by name or description. topic: bindings | transforms | events | view | style | codes."
    ),
    annotations=READ_ONLY,
    structured_output=False,
)
def pdk_catalog(
    type: str | None = None,
    prop: str | None = None,
    search: str | None = None,
    topic: str | None = None,
) -> str:
    try:
        return catalog(load_bundle(), type=type, prop=prop, search=search, topic=topic)
    except BundleError as exc:
        return f"pdk: {exc}"


def _sync(action) -> str:
    from pdk.sync import Outcome

    try:
        outcome: Outcome = action(Gateway())
    except GatewayError as exc:
        return str(exc)
    except (BundleError, FileNotFoundError) as exc:
        return f"pdk: {exc}"
    return outcome.text


@server.tool(
    name="pdk_pull",
    description=(
        "Download a view (or every view below a folder) from the gateway to work/<project>/<view path>/view.json, "
        "with the signature it was read at. path='stylesheet' pulls the project stylesheet to "
        "work/<sandbox>/_stylesheet/stylesheet.css. Args: path (view path like PDK/Menu, "
        "'stylesheet', or a local copy); project "
        "(default: the sandbox); force=true discards local edits."
    ),
    annotations=LOCAL_WRITE,
    structured_output=False,
)
def pdk_pull(path: str, project: str | None = None, force: bool = False) -> str:
    from pdk import sync

    return _sync(lambda gateway: sync.pull(gateway, path, project, force=force))


@server.tool(
    name="pdk_deploy",
    description=(
        "Validate a local view and write it to the sandbox with the signature of its pull (creates it if it was "
        "never pulled: put new views in work/<sandbox>/PDK/<name>/view.json). Refuses views with errors. Prints "
        "a preview URL. On G008 (changed on the gateway since the pull) nothing is written and you get a diff by "
        "JSON pointer. Also deploys the stylesheet (sandbox only; CSS syntax is checked, 409 gives a unified diff). "
        "Args: path (the view folder or its view.json, its sandbox view path like PDK/X, or "
        "work/<sandbox>/_stylesheet); " + RESOLVED
    ),
    annotations=SIGNED_WRITE,
    structured_output=False,
)
def pdk_deploy(path: str, resolved: bool = False) -> str:
    from pdk import sync

    return _sync(lambda gateway: sync.deploy(gateway, Path(path), load_bundle(), resolved=resolved))


@server.tool(
    name="pdk_promote",
    description=(
        "Copy a deployed sandbox view PDK/X to X (or `to`) in the same project, replacing the target only if it "
        "is still the version promoted or pulled before (else G008 with a diff). With to_project='target' the copy "
        "goes to the target project (the final one: ask the user first); it may not reference PDK/ views. A "
        "_stylesheet folder promotes the project stylesheet (to_project='target' only). Args: "
        "path (local folder of the deployed PDK/ view); to (target view path); to_project; " + RESOLVED
    ),
    annotations=DESTRUCTIVE,
    structured_output=False,
)
def pdk_promote(path: str, to: str | None = None, to_project: str | None = None, resolved: bool = False) -> str:
    from pdk import sync

    return _sync(lambda gateway: sync.promote(gateway, Path(path), load_bundle(), to=to, to_project=to_project,
                                              resolved=resolved))


@server.tool(
    name="pdk_delete",
    description=(
        "Delete a pulled view on the gateway if nobody changed it since (else G008 with a diff); empty folders "
        "and its preview page go too, the local copy stays. Args: path (local folder of the view); " + RESOLVED
    ),
    annotations=DESTRUCTIVE,
    structured_output=False,
)
def pdk_delete(path: str, resolved: bool = False) -> str:
    from pdk import sync

    return _sync(lambda gateway: sync.delete(gateway, Path(path), resolved=resolved))


@server.tool(
    name="pdk_tags",
    description=(
        "Simulated tags on the development gateway: memory tags under [provider]PDK/ (paths without [provider] "
        "hang from PDK/, e.g. Sandbox/Setpoint). action: 'list' (path, depth: the nodes under a folder with value "
        "and quality), 'read' (path: one tag, any provider), 'write' (path, value as text: numbers, true/false and "
        "null are parsed; quality like Bad_Stale or Bad_Disabled writes that quality, without value it keeps the "
        "current one), 'apply' (file: a tag export JSON of folders and memory tags with \"valueSource\": "
        "\"memory\", alarms included, imported into `to` under PDK/ and overwriting: it also resets values "
        "and acknowledges their alarms), "
        "'delete' (path under PDK/), 'alarms' (path: alarm events of the tags under it). Writes outside PDK/ are "
        "G013. Bad_LicenseExceeded means the gateway is over its tag license."
    ),
    annotations=DESTRUCTIVE,
    structured_output=False,
)
def pdk_tags(action: str, path: str | None = None, value: str | None = None, quality: str | None = None,
             file: str | None = None, to: str | None = None, depth: int = 1) -> str:
    from pdk import tags

    gateway = Gateway()
    try:
        if action == "list":
            return tags.list_text(gateway.tags(tags.full_path(path) if path else tags.FOLDER, depth))
        if action == "alarms":
            return tags.alarms_text(gateway.alarms(tags.full_path(path) if path else None))
        if action == "apply":
            if not file:
                return "pdk: apply needs file (a tag export JSON)"
            return tags.apply(gateway, Path(file), to or tags.FOLDER)
        if not path:
            return f"pdk: {action} needs path"
        if action == "read":
            return tags.values_text(gateway.read_tags([tags.full_path(path)])["results"])
        if action == "write":
            if value is None and not quality:
                return "pdk: write needs value, quality or both"
            return tags.values_text([tags.write(gateway, path, value, quality)])
        if action == "delete":
            return f"deleted {gateway.delete_tags(tags.full_path(path)).get('path')}"
    except GatewayError as exc:
        return str(exc)
    except (ValueError, OSError) as exc:
        return f"pdk: {exc}"
    return f"pdk: unknown action {action!r} (list, read, write, apply, delete, alarms)"


_eyes: Any = None
EYES_IDLE_S = 120  # close the browser, and its Perspective sessions, after this long unused
_eyes_lock = threading.Lock()


def _shared_eyes() -> Any:
    """One Chromium for the server's life: Maker caps sessions and a kept context keeps its Perspective session."""
    global _eyes
    with _eyes_lock:
        if _eyes is None:
            from pdk.eyes.browser import Eyes
            from pdk.eyes.inspect import session_closer

            _eyes = Eyes(idle_s=float(os.environ.get("PDK_EYES_IDLE_S") or EYES_IDLE_S),
                         release=session_closer(Gateway()))
            atexit.register(_eyes.close)
        return _eyes


VIEW_ARG = ("path: the local view folder or view.json under work/<sandbox>/, or a view path in the sandbox like "
            "PDK/Menu; viewport: WIDTHxHEIGHT (default 1366x768)")


@server.tool(
    name="pdk_open",
    description=(
        "Open a deployed sandbox view in headless Chromium (its preview page) and wait until it renders and its "
        "layout is stable. Reports load time, components rendered vs in the view.json and browser errors. The "
        "browser stays open for pdk_inspect/pdk_screenshot. Args: " + VIEW_ARG
    ),
    annotations=PREVIEW_WRITE,
    structured_output=False,
)
def pdk_open(path: str, viewport: str | None = None) -> str:
    from pdk.eyes import inspect

    return inspect.open_view(_shared_eyes(), Gateway(), path, viewport).text


@server.tool(
    name="pdk_inspect",
    description=(
        "Reload a deployed sandbox view and report what is wrong at runtime, each anomaly on the view.json pointer "
        "of its component: V001 text cut, V002 container overflow, V003 viewport overflow, V007 collapsed, V010 "
        "not rendered, V011 embedded view failed to load, V012 repeater instances bigger than it; "
        "Q001/Q002/Q003 a bound property is Bad/Error/Uncertain "
        "(missing tag, failing script or expression), pointing at the binding; L001 a script error the gateway "
        "logged for the component; C001 browser error; X001 partial eyes; X002 the page does not render this "
        "view.json (deploy first). Inspects the deployed version. Args: " + VIEW_ARG + "; focus (code, component "
        "path or pointer prefix) for detail; "
        "query (comma-separated name paths with their subtree, types like ia.display.label, pointer prefixes, or *) "
        "adds one line per matching component with what was measured: box in viewport px, text, color/font, "
        "background, padding, border, flex direction/gap/alignment, scrolled content. Check a layout with query "
        "before reaching for pdk_screenshot. expect: what the view must look like, one per ; or line, failures "
        "reported as A001 on the component: `root/B below root/A [gap 8|gap >= 8|gap 8..16]` (also above, "
        "left-of, right-of), `root/B inside root/A` (counts A's scrolled content), "
        "`root/B aligned left|right|top|bottom|center-x|center-y root/A`, `root/T width|height|x|y [=|>=|<=] N|N%` "
        "(% of the parent; x/y from the parent's inner top-left, like a coord's position.x/y), "
        "`root/T text \"...\"` (text ~ \"...\" contains), `root/T visible|hidden`, "
        "`root/T in-view` (not scrolled or clipped away), `root/Log scrolls [x|y]`, "
        "`root/T color|bg #hex|rgb()`, `root/T font-size N`, `root/T font-weight 700`, "
        "`root/Save enabled|disabled`, `tag Sandbox/Setpoint = 42` (read back from the gateway), "
        "`tag X quality Bad_Stale|Good|Bad`, `alarm North/Level active|cleared`, "
        "`root/Bar prop value >= 40` (a prop as the gateway evaluated it: props by default, or "
        "position/custom/meta; ops = != >= <= > < ~), `view prop custom.total = 3`, `page /detail` (the page's URL "
        "path, `page ~ det` contains) and `page shows PDK/Detail` (the view on the page). 1px tolerance. "
        "act: steps performed before measuring (so query, expect, "
        "qualities and logged script errors see the state after them), one per ; or line: `click root/Save`, "
        "`type root/Name \"P-2\"` (commits with Tab), `row root/Table 1` (first row shown), `wait 500`, "
        "`tag Sandbox/Setpoint 42 [quality Bad_Stale]` (a simulated tag under PDK/, see pdk_tags); "
        "a step that cannot be done stops there as A004. act runs the view's own event scripts on the gateway (a click "
        "on a button that writes tags writes them): use it only on sandbox views meant for it. Popups open after the "
        "steps are inspected too: expect "
        "`popup <id> open|closed`, and their components as popup:<id>/root/... in act, expect, query and anomalies. "
        "If a step navigates (a nav action), the report is about the view the page shows then, with a note."
    ),
    annotations=ACTS,
    structured_output=False,
)
def pdk_inspect(path: str, viewport: str | None = None, focus: str | None = None, query: str | None = None,
                expect: str | None = None, act: str | None = None) -> str:
    from pdk.eyes import inspect

    return inspect.inspect_view(_shared_eyes(), Gateway(), path, viewport, focus=focus, query=query,
                                 expect=expect, act=act).text


@server.tool(
    name="pdk_screenshot",
    description=(
        "PNG of a deployed sandbox view (reloaded) or of one component. Args: " + VIEW_ARG + "; component: name "
        "path like root/Panel/Label (or DOM path C.0:1) to capture only that component. act: steps performed before "
        "capturing (same grammar as pdk_inspect act), e.g. to capture a popup or a state after typing."
    ),
    annotations=ACTS,
    structured_output=False,
)
def pdk_screenshot(path: str, viewport: str | None = None, component: str | None = None,
                   act: str | None = None) -> list[Any] | str:
    from pdk.eyes import inspect

    png, text = inspect.screenshot(_shared_eyes(), Gateway(), path, viewport, component, act)
    if png is None:
        return text
    return [text, Image(data=png, format="png")]


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()

"""Routes of the sandbox's page configuration: URL -> view, so that a nav action to a page has a destination.

The preview pages under /pdk/ belong to deploy and the eyes (sync.ensure_preview): they are counted, never listed or
changed here. A route to a view under PDK/ is the agent's own work and may be removed; any other route is the
project's and is changed in the Designer.
"""

from __future__ import annotations

import json
from typing import Any

from pdk.gateway.client import VIEWS, Gateway, GatewayError
from pdk.sync import PAGE_CONFIG, PAGE_FILE, PREVIEW_PREFIX, Outcome, _edit_pages, _sandbox

OWN_PREFIX = "PDK/"  # the views the agent deploys: routes to them may be removed


def _view_of(route: Any) -> str:
    return str(route.get("viewPath") if isinstance(route, dict) else route)


def _is_preview(url: str) -> bool:
    return url.startswith(PREVIEW_PREFIX)


def list(gateway: Gateway) -> Outcome:
    """One line per route, "<url> -> <view>", sorted by URL; the preview pages are counted on a last line."""
    project = _sandbox(gateway)
    try:
        config = json.loads(gateway.resource(project, PAGE_CONFIG)["files"][PAGE_FILE])
    except (KeyError, ValueError):
        return Outcome(False, f"the {PAGE_FILE} of page-config cannot be read")
    routes: dict[str, Any] = config.get("pages", {})
    own = [url for url in sorted(routes) if not _is_preview(url)]
    lines = [f"{url} -> {_view_of(routes[url])}" for url in own] or ["no routes"]
    previews = len(routes) - len(own)
    if previews:
        lines.append(f"+ {previews} preview {'page' if previews == 1 else 'pages'} under {PREVIEW_PREFIX}")
    return Outcome(True, "\n".join(lines))


def add(gateway: Gateway, url: str, view: str, title: str | None = None) -> Outcome:
    """Route url to a view of the sandbox; a URL that already opens another view is refused, not replaced."""
    if not url.startswith("/"):
        return Outcome(False, f"{url} is not a URL path: it starts with '/', like /detail")
    if url == "/pdk" or _is_preview(url):
        return Outcome(False, f"{url} is reserved for the preview pages ({PREVIEW_PREFIX}...): pick another URL")
    project = _sandbox(gateway)
    try:
        found = gateway.resource(project, VIEWS, view)
    except GatewayError as exc:
        if exc.code != "G007":
            raise
        return Outcome(False, f"{view} is not in the sandbox: deploy it first")
    if found.get("folder"):
        return Outcome(False, f"{view} is a folder, not a view: deploy the view first")
    entry: dict[str, Any] = {"viewPath": view}
    if title:
        entry["title"] = title
    opened: str | None = None  # what the URL opens already, if it has a route

    def route(pages: dict[str, Any]) -> bool:
        nonlocal opened
        opened = None
        if url in pages:
            opened = _view_of(pages[url])
            return False
        pages[url] = entry
        return True

    warning = _edit_pages(gateway, project, route)
    if warning:
        return Outcome(False, warning)
    if opened is None:
        return Outcome(True, f"added {url} -> {view}")
    if opened == view:
        return Outcome(True, f"{url} already opens {view}")
    return Outcome(False, f"{url} already opens {opened}; pick another URL")


def remove(gateway: Gateway, url: str) -> Outcome:
    """Remove the route of url if it opens a view under PDK/; the project's own routes stay, and the preview pages
    go with their views (pdk delete)."""
    if url.rstrip("/") == PREVIEW_PREFIX.rstrip("/") or url.startswith(PREVIEW_PREFIX):
        return Outcome(False, f"{url} is a preview page: it goes when its view is deleted (pdk delete)")
    project = _sandbox(gateway)
    opened: str | None = None

    def drop(pages: dict[str, Any]) -> bool:
        nonlocal opened
        opened = None
        if url not in pages:
            return False
        opened = _view_of(pages[url])
        if not opened.startswith(OWN_PREFIX):
            return False
        del pages[url]
        return True

    warning = _edit_pages(gateway, project, drop)
    if warning:
        return Outcome(False, warning)
    if opened is None:
        return Outcome(False, f"no route {url}")
    if not opened.startswith(OWN_PREFIX):
        return Outcome(False, f"{url} opens {opened}, outside PDK/: it is the project's; change it in the Designer")
    return Outcome(True, f"removed {url} -> {opened}")

"""The live catalog: what the running gateway's Perspective registers, against the offline bundle.

`pdk schemas sync` compares every registered component with the descriptor the bundle extracted from the
installed .modl (drift), and writes the components the bundle lacks — third-party modules — to
`components/live.components.json` inside the bundle, where the loader already picks them up.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pdk.bundle import available_versions, load_bundle, schemas_root
from pdk.diagnostics import CHARS_PER_TOKEN, DEFAULT_BUDGET
from pdk.gateway.client import Gateway
from pdk.paths import RUN
from pdk.sync import json_diff

LIVE_FILE = "live.components.json"
BUILT_IN_FILE = "ia.components.json"


@dataclass
class Drift:
    version: str
    built_in: int = 0
    bundled: int = 0
    differing: dict[str, list[str]] = field(default_factory=dict)  # id -> first differing pointers
    missing: list[str] = field(default_factory=list)  # in the bundle, not registered
    live_only: list[dict[str, Any]] = field(default_factory=list)
    without_schema: list[str] = field(default_factory=list)
    built_in_ids: set[str] = field(default_factory=set)


def offline_descriptors(bundle_dir: Path) -> dict[str, tuple[str, dict[str, Any]]]:
    """id -> (file name, raw descriptor) as extracted from the .modl, without the live file."""
    found: dict[str, tuple[str, dict[str, Any]]] = {}
    for path in sorted((bundle_dir / "components").glob("*.json")):
        if path.name == LIVE_FILE:
            continue
        document = json.loads(path.read_text(encoding="utf-8"))
        descriptors = document.get("components") if isinstance(document, dict) and "components" in document \
            else [document]
        for descriptor in descriptors:
            if isinstance(descriptor, dict) and "id" in descriptor:
                found[descriptor["id"]] = (path.name, descriptor)
    return found


def comparable(descriptor: dict[str, Any], declared: dict[str, Any] | None = None) -> dict[str, Any]:
    """The parts validation relies on, normalized so absent and empty mean the same on both sides.

    The registry fills metadata the file leaves out (a derived name or defaultMetaName, category "unknown") and
    marks components hidden from the palette (showOnPalette: false) as deprecated: metadata is compared only
    where the offline descriptor (`declared`) states it.
    """
    declared = descriptor if declared is None else declared
    palette = declared.get("palette") or {}
    events = {
        event["name"]: {"description": event.get("description") or "", "schema": event.get("schema") or None}
        for event in descriptor.get("events") or [] if isinstance(event, dict) and "name" in event
    }
    return {
        "name": descriptor.get("name") if "name" in declared else None,
        "category": (descriptor.get("palette") or {}).get("category") if "category" in palette else None,
        "defaultMetaName": descriptor.get("defaultMetaName") if "defaultMetaName" in declared else None,
        "deprecated": bool(descriptor.get("deprecated", False)) or descriptor.get("showOnPalette") is False,
        "schema": descriptor.get("schema") or None,
        "childPositionSchema": descriptor.get("childPositionSchema") or None,
        "events": events,
        "extensionFunctions": sorted(f.get("name", "") for f in descriptor.get("extensionFunctions") or []),
    }


def compare(live: list[dict[str, Any]], offline: dict[str, tuple[str, dict[str, Any]]], version: str) -> Drift:
    drift = Drift(version)
    registered = {component["id"]: component for component in live}
    for component_id, component in sorted(registered.items()):
        if component.get("schema") is None:
            drift.without_schema.append(component_id)
            continue
        if component_id not in offline:
            drift.live_only.append(component)
            continue
        file_name, descriptor = offline[component_id]
        if file_name == BUILT_IN_FILE:
            drift.built_in += 1
            drift.built_in_ids.add(component_id)
        else:
            drift.bundled += 1
        changes = json_diff(comparable(descriptor), comparable(component, descriptor))
        if changes:
            drift.differing[component_id] = [pointer for pointer, _, _ in changes]
    drift.missing = sorted(set(offline) - set(registered))
    return drift


def live_document(components: list[dict[str, Any]], url: str) -> dict[str, Any]:
    return {
        "libraryName": "live",
        "source": url,
        "syncedAt": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "components": components,
    }


def sync(gateway: Gateway, root: Path | None = None, budget: int = DEFAULT_BUDGET) -> tuple[bool, str]:
    """Compare the gateway's components with the bundle of its Perspective version; write the live-only ones."""
    root = root or schemas_root()
    live_version = str(gateway.health().get("perspectiveVersion") or "").split(" ")[0]
    if live_version not in available_versions(root):
        have = ", ".join(available_versions(root)) or "none"
        return False, (f"The gateway runs Perspective {live_version} but there is no bundle for it under {root} "
                       f"(have: {have}). Run `{RUN}pdk schemas extract` first.")
    catalog = gateway.catalog(full=True)
    if not catalog.get("schemaAvailable"):
        return False, ("The module cannot read raw component schemas on this gateway (see its log): only the "
                       "offline bundle is available.")
    bundle_dir = root / live_version
    drift = compare(catalog["components"], offline_descriptors(bundle_dir), live_version)
    target = bundle_dir / "components" / LIVE_FILE
    if drift.live_only:
        document = live_document(drift.live_only, gateway.url)
        target.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    else:
        target.unlink(missing_ok=True)
    load_bundle.cache_clear()
    newest = available_versions(root)[-1]
    lines = [report(drift, budget)]
    if drift.live_only:
        lines.append(f"Wrote {len(drift.live_only)} component(s) to {target}")
    if newest != live_version:
        lines.append(f"warning: validation uses the newest bundle ({newest}) by default, not the gateway's "
                     f"{live_version}; set PDK_PERSPECTIVE_VERSION={live_version}")
    return not drift.differing, "\n".join(lines)


def report(drift: Drift, budget: int = DEFAULT_BUDGET) -> str:
    built_in_drift = sum(1 for component_id in drift.differing if component_id in drift.built_in_ids)
    lines = [
        f"Perspective {drift.version}: drift {built_in_drift}/{drift.built_in} built-in, "
        f"{len(drift.differing) - built_in_drift}/{drift.bundled} other bundled; "
        f"{len(drift.live_only)} live-only; {len(drift.missing)} bundled but not registered",
    ]
    limit = int(budget * CHARS_PER_TOKEN)
    for component_id, pointers in drift.differing.items():
        shown = ", ".join(pointers[:4]) + (f" (+{len(pointers) - 4})" if len(pointers) > 4 else "")
        lines.append(f"  ~ {component_id}: {shown}")
    for component in drift.live_only:
        lines.append(f"  + {component['id']} ({component.get('moduleId')}) {component.get('name')}")
    if drift.missing:
        lines.append(f"  - not registered: {', '.join(drift.missing)}")
    if drift.without_schema:
        lines.append(f"  ? no schema from the gateway: {', '.join(drift.without_schema)}")
    text = "\n".join(lines)
    return text if len(text) <= limit else text[: limit - 60].rsplit("\n", 1)[0] + "\n  ... (truncated)"


def changes_text(page: dict[str, Any]) -> str:
    """One line per change event, for `pdk gateway changes`."""
    if not page.get("watching"):
        return "Not watching: the module is read-only (no -Dpdk.sandbox), so it logs no changes."
    lines = [f"seq {page.get('seq')} in {page['watching']}"
             + (" (older events were dropped: the buffer keeps the last 1000)" if page.get("truncated") else "")]
    for event in page.get("events", []):
        actor = f" by {event['actor']}" if event.get("actor") else ""
        kind = " folder" if event.get("folder") else ""
        signature = f" {event['signature'][:10]}" if event.get("signature") else ""
        lines.append(f"#{event['seq']} {event['at']} {event['op']}{kind} {event['type'].split('/')[-1]}/"
                     f"{event['path']} [{event['origin']}{actor}]{signature}")
    if not page.get("events"):
        lines.append("no changes")
    return "\n".join(lines)



def sessions_text(listing: dict[str, Any]) -> str:
    """One line per page, for `pdk gateway sessions`."""
    lines = [f"{len(listing.get('sessions', []))} session(s) in {listing.get('project')}"]
    for session in listing.get("sessions", []):
        for page in session.get("pages", []):
            views = ", ".join(f"{view['resourcePath']}@{view['mountPath']}" for view in page.get("views", []))
            state = "connected" if page.get("connected") else "disconnected"
            lines.append(f"session {session['id']} page {page['id']} [{state}] {views or '(no views)'}")
    return "\n".join(lines)


def logs_text(page: dict[str, Any]) -> str:
    """One block per log event, for `pdk gateway logs`."""
    if not page.get("attached"):
        return "The module's log tap is not attached: /logs stays empty."
    lines = [f"last seq {page.get('last')}"]
    for event in page.get("events", []):
        mdc = event.get("mdc") or {}
        where = " ".join(f"{key}={mdc[key]}" for key in ("project-name", "view", "component") if key in mdc)
        message = str(event.get("message") or "").strip().splitlines()
        lines.append(f"#{event['seq']} {event['level']} {event['logger']} {where}".rstrip())
        lines.extend(f"    {line}" for line in message[:6])
    if not page.get("events"):
        lines.append("no events")
    return "\n".join(lines)

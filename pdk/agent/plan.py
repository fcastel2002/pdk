"""`pdk_plan`: the plan of an Ultra request, as data the host shows as a card and the gate holds the agent to.

It does not wait for the user: it saves the plan in the session folder (plan.json), adds what the gateway says about
each target (new, or overwritten and saved at once), and tells the agent to end its turn. The host asks the user;
an approval becomes a grant for exactly the targets named here (pdk.agent.gate).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pdk.agent.gate import PLAN_FILE
from pdk.gateway.client import VIEWS, Gateway, GatewayError

KINDS = {  # plan key -> (target prefix, resource type key)
    "views": ("view", VIEWS),
    "style_classes": ("style-class", "com.inductiveautomation.perspective/style-classes"),
    "scripts": ("script", "ignition/script-python"),
}
NOT_PROMOTED = ("tags", "pages")  # PDK cannot put these in the target: the user adds them in the Designer


def targets_of(targets: Mapping[str, Any] | None) -> list[str]:
    """The plan's targets in the gate's form: "view:Main/Overview", "style-class:Card", "script:util", "stylesheet"."""
    found: list[str] = []
    for key, (prefix, _) in KINDS.items():
        for path in (targets or {}).get(key) or []:
            if isinstance(path, str) and path.strip("/"):
                found.append(f"{prefix}:{path.strip('/')}")
    if (targets or {}).get("stylesheet"):
        found.append("stylesheet")
    return found


def _status(gateway: Gateway | None, project: str | None, target: str) -> str:
    if gateway is None or not project:
        return "unknown (no gateway)"
    if target == "stylesheet":
        return "replaces the project's stylesheet, saved at once (no Ctrl+Z)"
    prefix, path = target.split(":", 1)
    type_key = next(kind for kind_prefix, kind in KINDS.values() if kind_prefix == prefix)
    try:
        state = gateway.resource(project, type_key, path)
    except GatewayError as exc:
        return "new" if exc.code == "G007" else f"unknown ({exc.code})"
    if state.get("folder"):
        return "is a folder in the target: the plan must name a resource"
    return "exists: the promote overwrites it, saved at once (no Ctrl+Z)"


def submit(folder: Path, gateway: Gateway | None, *, summary: str, attention: str = "", details: str = "",
           targets: Mapping[str, Any] | None = None, checks: str = "") -> str:
    target_project = None
    if gateway is not None:
        try:
            target_project = gateway.health().get("target")
        except GatewayError:
            gateway = None
    named = targets_of(targets)
    computed = [f"{target} in {target_project or 'the target'}: {_status(gateway, target_project, target)}"
                for target in named]
    for key in NOT_PROMOTED:
        if (targets or {}).get(key):
            computed.append(f"{key} cannot be put in the target by PDK: the user adds them in the Designer (the "
                            "sandbox's simulated ones stay under PDK/)")
    plan = {"id": uuid.uuid4().hex[:12], "summary": summary.strip(), "attention": attention.strip(),
            "computed": computed, "details": details.strip(), "checks": checks.strip(), "targets": named}
    with open(folder / PLAN_FILE, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(plan, handle, indent=1, ensure_ascii=False)
    lines = [f"plan {plan['id']} submitted: end your turn now. The user approves or rejects it in the panel; once it "
             "is approved, do all of it without asking."]
    if computed:
        lines.append("The panel adds what the gateway says:")
        lines += [f"- {line}" for line in computed]
    if not named:
        lines.append("It names nothing for the target: an approval lets you work in the sandbox and on the open view "
                     "only.")
    return "\n".join(lines)

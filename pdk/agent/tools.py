"""What a call to each PDK tool does, from its arguments: the effects the gate rules on.

Every tool the server registers has an entry in TOOLS (a test checks it), so a new tool cannot reach an agent
session without a decision about it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from enum import StrEnum
from pathlib import Path
from typing import Any


class Effect(StrEnum):
    READ = "read"  # reads files, schemas, the gateway or the Designer
    LOOK = "look"  # the eyes on a sandbox view, without act
    LOOK_TARGET = "look_target"  # the eyes on a promoted view: a preview page in the target, its startup scripts run
    ACT = "act"  # the eyes run steps, which run the view's own event scripts
    DESIGNER_EDIT = "designer_edit"  # changes the view open in the Designer (unsaved, undoable)
    DESIGNER_CODE = "designer_code"  # the same, adding scripts, events or bindings that run code
    WORK_WRITE = "work_write"  # writes files under work/
    SANDBOX_WRITE = "sandbox_write"  # writes resources or page routes of the sandbox
    TAGS_WRITE = "tags_write"  # writes simulated tags (or their history) under PDK/
    TARGET_WRITE = "target_write"  # writes the target project (promote --to-project)
    PLAN = "plan"  # presents a plan for the user to approve


Effects = frozenset[Effect]
# (arguments, the target project's name or None) -> effects of that call
Classifier = Callable[[Mapping[str, Any], str | None], Effects]

_CODE_KEYS = frozenset({"events", "scripts", "customMethods", "messageHandlers", "onChange"})
_CODE_TYPES = frozenset({"script", "query"})  # script transforms and bindings, query bindings
_CODE_TEXT = ("runScript(", "system.")


def carries_code(value: Any) -> bool:
    """Whether a component, a prop value or a list of edit operations brings code that runs on the gateway: event
    and change scripts, custom methods, message handlers, script transforms or bindings, query bindings, and
    expressions that call runScript or a system function."""
    if isinstance(value, dict):
        if _CODE_KEYS & value.keys() or value.get("type") in _CODE_TYPES:
            return True
        return any(carries_code(item) for item in value.values())
    if isinstance(value, list):
        return any(carries_code(item) for item in value)
    return isinstance(value, str) and any(marker in value for marker in _CODE_TEXT)


def _project_of_path(path: Any) -> str | None:
    """The project of a local view folder (its sidecar, or work/<project>/...); None for a sandbox view path."""
    if not isinstance(path, str) or not path:
        return None
    from pdk import sync

    location = Path(path)
    if not location.exists():
        return None
    try:
        return sync._target(location, None)[2]
    except (ValueError, OSError):
        return None


def _eyes(args: Mapping[str, Any], target: str | None) -> Effects:
    if args.get("act"):
        return frozenset({Effect.ACT})
    on_target = target is not None and _project_of_path(args.get("path")) == target
    return frozenset({Effect.LOOK_TARGET if on_target else Effect.LOOK})


def _always(*effects: Effect) -> Classifier:
    found = frozenset(effects)
    return lambda args, target: found


def _by_action(reading: frozenset[str], writing: Effect) -> Classifier:
    """Tools whose `action` argument decides: the reading ones only read."""
    def classify(args: Mapping[str, Any], target: str | None) -> Effects:
        action = str(args.get("action") or "").lower()
        return frozenset({Effect.READ if action in reading else writing})
    return classify


def _tags(args: Mapping[str, Any], target: str | None) -> Effects:
    action = str(args.get("action") or "").lower()
    if action in ("list", "read", "alarms") or (action == "history" and not args.get("value")):
        return frozenset({Effect.READ})
    return frozenset({Effect.TAGS_WRITE})


def _promote(args: Mapping[str, Any], target: str | None) -> Effects:
    return frozenset({Effect.TARGET_WRITE if args.get("to_project") else Effect.SANDBOX_WRITE})


def _work(args: Mapping[str, Any], target: str | None) -> Effects:
    action = str(args.get("action") or "").lower()
    return frozenset({Effect.READ if action in ("read", "list") else Effect.WORK_WRITE})


TOOLS: dict[str, Classifier] = {
    "pdk_validate": _always(Effect.READ),
    "pdk_catalog": _always(Effect.READ),
    "pdk_guide": _always(Effect.READ),
    "pdk_refs": _always(Effect.READ),  # inventory also writes its own report under work/_inventory/
    "pdk_pull": _always(Effect.WORK_WRITE),
    "pdk_work": _work,
    "pdk_deploy": _always(Effect.SANDBOX_WRITE),  # deploy never reaches the target (sync.deploy, the module)
    "pdk_delete": _always(Effect.SANDBOX_WRITE),
    "pdk_promote": _promote,
    "pdk_pages": _by_action(frozenset({"list"}), Effect.SANDBOX_WRITE),
    "pdk_tags": _tags,
    "pdk_open": _eyes,
    "pdk_inspect": _eyes,
    "pdk_screenshot": _eyes,
    "pdk_plan": _always(Effect.PLAN),
}


def effects(name: str, args: Mapping[str, Any] | None, target: str | None = None) -> Effects | None:
    """The effects of one call, or None for a tool PDK does not know (the gate refuses those)."""
    classify = TOOLS.get(name)
    return None if classify is None else classify(args or {}, target)

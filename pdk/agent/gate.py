"""The rules of an agent session, enforced inside `pdk-mcp --agent-session <folder>`.

The host (the PDK Agent broker) owns the session folder and writes `state.json` there: the mode, the work folder and,
in Ultra, the grant of the plan the user approved. The state is signed with HMAC-SHA256 and a key that only the host
and this server have (`PDK_AGENT_KEY`, in the server's environment), so the agent cannot write itself a grant with
its own tools. Before every tool call the gate reads it again and decides from what the call does
(`pdk.agent.tools`):

- Normal mode: read, look at sandbox views, edit the view open in the Designer (props, no code).
- Ultra before approval: read, look, and present a plan (pdk_plan).
- Ultra with a live grant: all of PDK, the target only for what the plan names.

A grant is a lease: it carries an expiry the host renews while the user's request runs, so one the host could not
withdraw (a crash, a write that failed) ends by itself. Without a readable, correctly signed state the gate fails
closed: reading and looking only.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pdk.agent.tools import Effect, effects
from pdk.diagnostics import Diagnostic, make, render

STATE_FILE = "state.json"
PLAN_FILE = "plan.json"
KEY_ENV = "PDK_AGENT_KEY"
NORMAL, ULTRA = "normal", "ultra"

SAFE = frozenset({Effect.READ, Effect.LOOK})  # what a session may always do, even with no readable state
ALLOWED = {
    NORMAL: SAFE | {Effect.DESIGNER_EDIT},
    ULTRA: SAFE | {Effect.PLAN},  # until a plan is approved
}


@dataclass(frozen=True)
class Grant:
    plan: str
    targets: frozenset[str]  # "view:<path>", "style-class:<path>", "script:<path>", "stylesheet"
    expires: float  # time.time() after which it no longer counts


@dataclass(frozen=True)
class State:
    mode: str
    work: str | None = None
    designer: str | None = None
    grant: Grant | None = None
    generation: int = 0

    def live_grant(self, now: float | None = None) -> Grant | None:
        if self.mode != ULTRA or self.grant is None:
            return None
        return self.grant if (now if now is not None else time.time()) < self.grant.expires else None


def _canonical(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sign(payload: Mapping[str, Any], key: bytes) -> str:
    return hmac.new(key, _canonical(payload), hashlib.sha256).hexdigest()


def payload_of(state: State) -> dict[str, Any]:
    grant = None if state.grant is None else {"plan": state.grant.plan, "targets": sorted(state.grant.targets),
                                              "expires": state.grant.expires}
    return {"v": 1, "mode": state.mode, "work": state.work, "designer": state.designer, "grant": grant,
            "generation": state.generation}


def write_state(folder: Path, state: State, key: bytes, *, attempts: int = 5) -> bool:
    """The host's side: replace state.json atomically. False when it could not (an antivirus holding the file): the
    caller treats that as a revocation that did not happen, and the lease ends it."""
    payload = payload_of(state)
    text = json.dumps({"payload": payload, "sig": sign(payload, key)}, indent=1)
    temporary = folder / (STATE_FILE + ".tmp")
    for attempt in range(attempts):
        try:
            temporary.write_text(text, encoding="utf-8")
            os.replace(temporary, folder / STATE_FILE)
            return True
        except PermissionError:
            time.sleep(0.05 * (attempt + 1))
    return False


def read_state(folder: Path, key: bytes | None) -> State | None:
    """The state, or None when it is missing, unreadable, unsigned or signed with another key."""
    if not key:
        return None
    try:
        document = json.loads((folder / STATE_FILE).read_text(encoding="utf-8"))
        payload, signature = document["payload"], document["sig"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not isinstance(signature, str) or not hmac.compare_digest(sign(payload, key), signature):
        return None
    if payload.get("v") != 1 or payload.get("mode") not in (NORMAL, ULTRA):
        return None
    raw = payload.get("grant")
    grant = None
    if isinstance(raw, dict):
        try:
            grant = Grant(str(raw["plan"]), frozenset(str(t) for t in raw.get("targets") or []), float(raw["expires"]))
        except (KeyError, TypeError, ValueError):
            return None
    return State(payload["mode"], payload.get("work"), payload.get("designer"), grant,
                 int(payload.get("generation") or 0))


def promote_destination(args: Mapping[str, Any]) -> str | None:
    """What a promote to the target would write, in the form plans name targets ("view:Main/Overview"), or None
    when it cannot be told from the arguments."""
    from pdk import resources, stylesheet, sync

    raw = args.get("path")
    if not isinstance(raw, str) or not raw:
        return None
    location = Path(raw)
    to = str(args["to"]).strip("/") if args.get("to") else None
    try:
        if stylesheet.is_stylesheet(location):
            return "stylesheet"
        if kind := resources.kind_for(location, None):
            _, _, _, path = resources._locate(kind, location, None)
            kind_name = "style-class" if kind is resources.STYLE_CLASS else "script"
        else:
            _, _, _, path = sync._target(location, None)
            kind_name = "view"
    except (ValueError, OSError):
        return None
    prefix = sync.SANDBOX_FOLDER + "/"
    destination = to or (path[len(prefix):] if path.startswith(prefix) else None)
    return f"{kind_name}:{destination}" if destination else None


def _refusal(code: str, message: str, hint: str) -> str:
    diagnostic: Diagnostic = make(code, "error", "", "", message, hint)
    return render([diagnostic], title="pdk agent session")


class Gate:
    def __init__(self, folder: Path, key: bytes | None, target: Callable[[], str | None] | None = None,
                 clock: Callable[[], float] = time.time):
        self.folder = folder
        self.key = key
        self._target = target or (lambda: None)
        self._clock = clock

    def state(self) -> State | None:
        return read_state(self.folder, self.key)

    def check(self, tool: str, args: Mapping[str, Any] | None) -> str | None:
        """None when the call may run, else the refusal the agent gets (a T diagnostic with its hint)."""
        try:
            target = self._target()
        except Exception:  # noqa: BLE001 - no gateway: a promoted view cannot be told apart, so it counts as one
            target = None
        found = effects(tool, args, target)
        if found is None:
            return _refusal("T001", f"{tool} is not a PDK tool this session knows",
                            "use PDK's tools (pdk_catalog, pdk_validate, pdk_work, pdk_deploy, pdk_inspect...)")
        state = self.state()
        if state is None:
            if found <= SAFE:
                return None
            return _refusal("T005", "the session state could not be read, so only reading and looking are allowed",
                            "tell the user that PDK Agent lost its session state and needs New chat")
        grant = state.live_grant(self._clock())
        allowed = ALLOWED.get(state.mode, SAFE) if grant is None else None
        if allowed is not None and not found <= allowed:
            return self._mode_refusal(tool, state.mode, found - allowed)
        if grant is not None and Effect.TARGET_WRITE in found:
            destination = promote_destination(args or {})
            if destination is None or destination not in grant.targets:
                return _refusal("T004", f"{tool} would write {destination or 'something'} in the target, which the "
                                        "approved plan does not name",
                                "present an amended plan with pdk_plan that names it, or leave it in the sandbox")
        return None

    @staticmethod
    def _mode_refusal(tool: str, mode: str, extra: frozenset[Effect]) -> str:
        if mode == ULTRA:
            return _refusal("T003", f"{tool} needs the plan approved first",
                            "present the plan with pdk_plan and end your turn; once the user approves it, do it all")
        if Effect.PLAN in extra:
            return _refusal("T002", "there is no plan to approve in normal mode",
                            "make the change with pdk_designer_edit, or tell the user that creating views needs Ultra")
        if Effect.DESIGNER_CODE in extra:
            return _refusal("T002", f"{tool} would add code that runs on the gateway (scripts, events, script or "
                                    "query bindings, runScript) to the open view",
                            "in normal mode PDK Agent changes props only: tell the user it needs Ultra and a plan")
        if Effect.LOOK_TARGET in extra:
            return _refusal("T002", f"{tool} on a view of the target adds a preview page there and runs its startup "
                                    "scripts", "look at its PDK/ copy in the sandbox, or ask for it in Ultra")
        return _refusal("T002", f"{tool} writes ({', '.join(sorted(extra))}), which normal mode does not do",
                        "tell the user it needs Ultra (the switch in the panel), where they approve a plan first")

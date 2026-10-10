"""Pull, deploy and delete the project stylesheet through the pdk-gateway module.

On the gateway it is a singleton resource (type `com.inductiveautomation.perspective/stylesheet`, path "",
one file `stylesheet.css`). Locally it lives in `work/<project>/_stylesheet/stylesheet.css` next to the same
`.pdk-base.json` sidecar views use: every write sends the signature of the pull, and a 409 saves the gateway
version as `stylesheet.gateway.css` and reports a unified diff. It is edited in the sandbox; the target project
(`-Dpdk.target`) takes it only through `promote --to-project`.
"""

from __future__ import annotations

import difflib
import json
from pathlib import Path
from typing import Any

from pdk.diagnostics import DEFAULT_BUDGET
from pdk.gateway.client import Gateway, GatewayError
from pdk.sync import (
    SIDECAR,
    Outcome,
    _budgeted,
    _expected_signature,
    _roles,
    _sandbox,
    _short,
    _shown,
    _stamp,
    local_dir,
    read_sidecar,
)

STYLESHEET = "com.inductiveautomation.perspective/stylesheet"
FOLDER = "_stylesheet"
CSS_FILE = "stylesheet.css"
GATEWAY_COPY = "stylesheet.gateway.css"
NAMES = ("stylesheet", CSS_FILE)  # what `pull` takes besides a local path
REFUSED = "the stylesheet is edited in the sandbox only (the target takes it through promote --to-project)"


def is_stylesheet(location: Path) -> bool:
    """A stylesheet.css, its `_stylesheet` folder, or any folder whose sidecar says so."""
    directory = location.parent if location.name == CSS_FILE else location
    if location.name == CSS_FILE or directory.name == FOLDER:
        return True
    sidecar = read_sidecar(directory)
    return bool(sidecar) and sidecar.get("type") == STYLESHEET


def is_pull_path(path: str) -> bool:
    return path in NAMES or is_stylesheet(Path(path))


def check_css(text: str) -> list[str]:
    """Syntax problems that break a stylesheet: unterminated comment or string, unbalanced braces."""
    problems: list[str] = []
    opened: list[int] = []
    line, i, n = 1, 0, len(text)
    while i < n:
        char = text[i]
        if char == "\n":
            line += 1
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            if end < 0:
                problems.append(f"line {line}: unterminated comment")
                break
            line += text.count("\n", i, end)
            i = end + 1
        elif char in "\"'":
            start = i
            i += 1
            while i < n and text[i] != char and text[i] != "\n":
                i += 2 if text[i] == "\\" else 1
            if i >= n or text[i] == "\n":
                problems.append(f"line {line}: unterminated string")
                i = min(i, n)
                continue
            line += text.count("\n", start, i)
        elif char == "{":
            opened.append(line)
        elif char == "}":
            if opened:
                opened.pop()
            else:
                problems.append(f"line {line}: unmatched '}}'")
        i += 1
    problems.extend(f"line {start}: '{{' is never closed" for start in opened)
    return problems


# --- local state ----------------------------------------------------------------------------------


def _norm(text: str | None) -> str | None:
    return None if text is None else text.replace("\r\n", "\n")


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="")


def _write_sidecar(directory: Path, project: str, state: dict[str, Any], text: str) -> None:
    sidecar = {
        "project": project,
        "type": STYLESHEET,
        "path": "",
        "signature": state.get("signature"),
        "lastModification": state.get("lastModification"),
        "base": {CSS_FILE: text},
    }
    (directory / SIDECAR).write_text(json.dumps(sidecar, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (directory / GATEWAY_COPY).unlink(missing_ok=True)


def _locate(location: Path, root: Path | None) -> tuple[Path, dict[str, Any] | None, str]:
    """Directory, sidecar and project of a local stylesheet (the css file or its folder)."""
    directory = location.parent if location.name == CSS_FILE else location
    sidecar = read_sidecar(directory)
    if sidecar and sidecar.get("project"):
        return directory, sidecar, sidecar["project"]
    if directory.name != FOLDER or not directory.parent.name:
        raise ValueError(f"{directory} is not a {FOLDER} folder: use work/<project>/{FOLDER}/{CSS_FILE}")
    return directory, sidecar, directory.parent.name


# --- pull -----------------------------------------------------------------------------------------


def pull(gateway: Gateway, project: str | None = None, *, force: bool = False,
         root: Path | None = None, path: str | None = None) -> Outcome:
    """Download the stylesheet with its signature. `path` may be a local copy, which names the project."""
    if path and path not in NAMES:
        local = Path(path)
        sidecar = read_sidecar(local.parent if local.name == CSS_FILE else local)
        project = project or (sidecar or {}).get("project")
    project = project or _sandbox(gateway)
    directory = local_dir(project, FOLDER, root)
    try:
        state = gateway.resource(project, STYLESHEET, "")
    except GatewayError as exc:
        if exc.code != "G007":
            raise
        state = {"folder": True}
    if state.get("folder"):  # the root of a missing singleton reads as an empty listing, not a 404
        return Outcome(True, f"{project}:stylesheet does not exist yet: write {_shown(directory / CSS_FILE)} "
                             "and `pdk deploy` it to create it")
    text = state.get("files", {}).get(CSS_FILE)
    if text is None:
        return Outcome(False, f"skip {project}:stylesheet: no {CSS_FILE}")
    local = directory / CSS_FILE
    sidecar = read_sidecar(directory)
    if not force and sidecar and local.is_file() \
            and _norm(local.read_text(encoding="utf-8")) != _norm(sidecar["base"].get(CSS_FILE)):
        return Outcome(False, f"skip {project}:stylesheet: {_shown(local)} has local edits; deploy them first, or "
                              "pull --force to discard them")
    directory.mkdir(parents=True, exist_ok=True)
    _write(local, text)
    _write_sidecar(directory, project, state, text)
    return Outcome(True, f"pulled {project}:stylesheet -> {_shown(directory)} ({_stamp(state)})")


# --- deploy and delete ----------------------------------------------------------------------------


def deploy(gateway: Gateway, location: Path, *, resolved: bool = False, root: Path | None = None,
           budget: int = DEFAULT_BUDGET, _promoting: bool = False) -> Outcome:
    """Check the local stylesheet.css and write it with the signature of the pull (create if never pulled)."""
    try:
        directory, sidecar, project = _locate(location, root)
        signature = _expected_signature(sidecar, resolved)
    except ValueError as exc:
        return Outcome(False, f"pdk: {exc}")
    if project != _roles(gateway)[_promoting]:
        return Outcome(False, f"pdk: {REFUSED}; {project} is not the sandbox")
    local = directory / CSS_FILE
    if not local.is_file():
        return Outcome(False, f"pdk: {local} does not exist")
    text = local.read_text(encoding="utf-8")
    if sidecar and not resolved and not sidecar.get("conflict") and _norm(text) == _norm(sidecar["base"].get(CSS_FILE)):
        return Outcome(True, f"{project}:stylesheet unchanged since the pull ({_short(signature)}); nothing to deploy")
    problems = check_css(text)
    if problems:
        return Outcome(False, f"{_shown(local)}: {len(problems)} CSS problem(s)\n" + "\n".join(problems[:20])
                       + "\nNot deployed: fix the errors above first.")
    try:
        written = gateway.put_resource(project, STYLESHEET, "", {CSS_FILE: text}, signature=signature)
    except GatewayError as exc:
        if exc.code != "G008":
            raise
        return Outcome(False, _conflict(exc, directory, sidecar, project, text, "deploy", budget))
    _write_sidecar(directory, project, written, text)
    return Outcome(True, f"{written.get('op', 'written')} {project}:stylesheet "
                         f"{_short(signature) or 'new'} -> {_short(written.get('signature'))}\n"
                         "note: open sessions pick it up on reload")


def promote(gateway: Gateway, location: Path, *, to_project: str | None = None, resolved: bool = False,
            root: Path | None = None, budget: int = DEFAULT_BUDGET) -> Outcome:
    """Copy the deployed sandbox stylesheet to the target project, with the target's own signature."""
    try:
        directory, sidecar, project = _locate(location, root)
    except ValueError as exc:
        return Outcome(False, f"pdk: {exc}")
    sandbox, target = _roles(gateway)
    if to_project is None:
        return Outcome(False, "pdk: the stylesheet is promoted only to the target project: pass --to-project target")
    if not target:
        return Outcome(False, "pdk: the gateway has no target project: add -Dpdk.target=<project> to ignition.conf "
                              "and restart it")
    if to_project not in ("target", target):
        return Outcome(False, f"pdk: {to_project!r} is not the target project ({target})")
    if project != sandbox:
        return Outcome(False, f"pdk: promote to the target starts from the sandbox ({sandbox}), not {project}")
    if sidecar is None or sidecar.get("conflict"):
        return Outcome(False, f"pdk: deploy {_shown(directory)} first (it has no clean {SIDECAR}); promote copies "
                              "what is on the gateway")
    local = directory / CSS_FILE
    if _norm(local.read_text(encoding="utf-8") if local.is_file() else None) != _norm(sidecar["base"].get(CSS_FILE)):
        return Outcome(False, f"pdk: {_shown(directory)} has local edits: deploy them before promoting")
    current = gateway.resource(project, STYLESHEET, "")
    if current.get("folder") or current.get("signature") != sidecar.get("signature"):
        return Outcome(False, f"pdk: {project}:stylesheet changed on the gateway after your deploy: pull it, review "
                              "and deploy before promoting")
    destination = local_dir(target, FOLDER, root)
    destination.mkdir(parents=True, exist_ok=True)
    _write(destination / CSS_FILE, current["files"][CSS_FILE])
    outcome = deploy(gateway, destination, resolved=resolved, root=root, budget=budget, _promoting=True)
    return Outcome(outcome.ok, f"promote {project}:stylesheet -> {target}: {outcome.text}")


def delete(gateway: Gateway, location: Path, *, resolved: bool = False, root: Path | None = None,
           budget: int = DEFAULT_BUDGET) -> Outcome:
    """Delete the stylesheet on the gateway, only if it is still the version you pulled."""
    try:
        directory, sidecar, project = _locate(location, root)
        if sidecar is None:
            raise ValueError(f"{directory} has no {SIDECAR}: pull the stylesheet first, so the delete is signed")
        signature = _expected_signature(sidecar, resolved)
    except ValueError as exc:
        return Outcome(False, f"pdk: {exc}")
    if project != _roles(gateway)[0]:
        return Outcome(False, f"pdk: {REFUSED}; {project} is not the sandbox")
    if signature is None:
        return Outcome(False, f"pdk: {project}:stylesheet no longer exists on the gateway")
    try:
        gateway.delete_resource(project, STYLESHEET, "", signature)
    except GatewayError as exc:
        if exc.code != "G008" or "current" not in exc.payload:
            raise
        local = directory / CSS_FILE
        text = local.read_text(encoding="utf-8") if local.is_file() else None
        return Outcome(False, _conflict(exc, directory, sidecar, project, text, "delete", budget))
    (directory / SIDECAR).unlink(missing_ok=True)
    (directory / GATEWAY_COPY).unlink(missing_ok=True)
    return Outcome(True, f"deleted {project}:stylesheet ({_short(signature)}). The local copy stays in "
                         f"{_shown(directory)}; deploying it creates the stylesheet again.")


def _conflict(error: GatewayError, directory: Path, sidecar: dict[str, Any] | None, project: str,
              text: str | None, action: str, budget: int) -> str:
    """Save the gateway version and the signature to resolve with; report the diffs."""
    current = error.payload.get("current") or None
    theirs = (current or {}).get("files", {}).get(CSS_FILE)
    if sidecar is not None:
        sidecar["conflict"] = {
            "signature": current.get("signature") if current else None,
            "lastModification": current.get("lastModification") if current else None,
        }
        (directory / SIDECAR).write_text(json.dumps(sidecar, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if theirs is not None:
        _write(directory / GATEWAY_COPY, theirs)
    shown = _shown(directory)
    base = _norm(sidecar["base"].get(CSS_FILE)) if sidecar else None
    if current is None:
        head = f"G008 conflict: {project}:stylesheet was deleted on the gateway after your pull; nothing was written."
    elif sidecar is None:
        head = (f"G008 conflict: {project}:stylesheet already exists on the gateway ({_stamp(current)}); "
                "nothing was written.")
    else:
        head = (f"G008 conflict: {project}:stylesheet changed on the gateway after your pull "
                f"({_short(sidecar.get('signature'))} -> {_stamp(current)}); nothing was written.")
    lines = [head]
    if current is not None and base is not None:
        lines.append(_diff("Gateway changes since your pull", base, _norm(theirs) or "", "pull", "gateway"))
        if action == "deploy":
            lines.append(_diff("Your changes", base, _norm(text) or "", "pull", "yours"))
    elif current is not None and action == "deploy":
        lines.append(_diff("Gateway version -> yours", _norm(theirs) or "", _norm(text) or "", "gateway", "yours"))
    if action == "delete":
        lines.append(f"The gateway version is in {shown}/{GATEWAY_COPY}. To delete it anyway: `pdk delete {shown} "
                     f"--resolved`; to keep it: `pdk pull stylesheet --force`.")
    elif current is None:
        lines.append(f"To create it again from your copy: `pdk deploy {shown} --resolved`.")
    elif sidecar is None:
        lines.append(f"The gateway version is in {shown}/{GATEWAY_COPY}. Pull it (`pdk pull stylesheet`) to work "
                     "on it.")
    else:
        lines.append(f"The gateway version is in {shown}/{GATEWAY_COPY}. Merge its changes into {CSS_FILE}, then "
                     f"`pdk deploy {shown} --resolved`; or discard yours with `pdk pull stylesheet --force`.")
    return _budgeted("\n".join(lines), budget)


def _diff(title: str, before: str, after: str, old_name: str, new_name: str) -> str:
    diff = list(difflib.unified_diff(before.splitlines(), after.splitlines(), old_name, new_name, lineterm="", n=1))
    return f"{title}: none" if not diff else f"{title}:\n" + "\n".join(diff)

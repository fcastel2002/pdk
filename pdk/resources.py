"""Pull, deploy, promote and delete the project's style classes and Python scripts through the pdk-gateway module.

They work like views: the agent's live under `PDK/` in the sandbox, each in
`work/<project>/<folder>/<path>/<file>` (`_style-classes/.../style.json`, `_scripts/.../code.py`) next to the same
`.pdk-base.json` sidecar. Every write carries the signature of the pull, so a Designer edit gives a 409 with a
unified diff instead of being overwritten; `promote` copies `PDK/X` to `X` in the sandbox, or with `to_project` to
the target. Before a write, a style class is checked as JSON with known CSS properties (P005, P006), and a script
with the gateway's own Jython (E009) and its `system.*` names (E008).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pdk.bundle import Bundle
from pdk.diagnostics import DEFAULT_BUDGET, Diagnostic, counts, make, render
from pdk.gateway.client import Gateway, GatewayError
from pdk.stylesheet import _diff, _norm, _write
from pdk.sync import (
    SANDBOX_FOLDER,
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
    scratch,
    work_root,
)
from pdk.validate import code
from pdk.validate.schema import is_css_key, style_hint


@dataclass(frozen=True)
class Kind:
    name: str  # for messages
    type: str  # resource type key
    folder: str  # under work/<project>/
    file: str  # the resource's one file
    scope: str  # application scope it is written with
    prefixes: tuple[str, ...]  # what `pull` takes before the resource path

    @property
    def plural(self) -> str:
        return self.name + ("es" if self.name.endswith("s") else "s")

    @property
    def gateway_copy(self) -> str:
        stem, _, suffix = self.file.rpartition(".")
        return f"{stem}.gateway.{suffix}"


STYLE_CLASS = Kind("style class", "com.inductiveautomation.perspective/style-classes", "_style-classes", "style.json",
                   "G", ("style-classes/", "_style-classes/", "style-class:"))
SCRIPT = Kind("script", "ignition/script-python", "_scripts", "code.py", "A", ("scripts/", "_scripts/", "script:"))
KINDS = (STYLE_CLASS, SCRIPT)
STYLE_KEYS = {"base": ("style", "animation"), "variant": ("style", "animation", "pseudo", "media")}


# --- where a resource is --------------------------------------------------------------------------


def pull_kind(path: str) -> tuple[Kind, str] | None:
    """`style-classes/PDK/Card`, `_scripts/PDK/util`, `script:PDK/util`... as (kind, resource path)."""
    text = path.replace("\\", "/")
    for kind in KINDS:
        for prefix in kind.prefixes:
            if text.startswith(prefix) and text[len(prefix):].strip("/"):
                return kind, text[len(prefix):].strip("/")
    return None


def kind_of(location: Path, root: Path | None = None) -> Kind | None:
    """The kind of a local copy: by its sidecar, its file name or the folder it is under."""
    directory = _directory(location)
    sidecar = read_sidecar(directory)
    if sidecar:
        return next((kind for kind in KINDS if kind.type == sidecar.get("type")), None)
    parts = _relative(directory, root)
    if len(parts) >= 3:
        return next((kind for kind in KINDS if parts[1] == kind.folder), None)
    return None


def kind_for(location: Path, root: Path | None = None) -> Kind | None:
    """The kind a CLI/MCP path names: a local copy, or `style-classes/...`/`scripts/...` text."""
    found = pull_kind(location.as_posix())
    return found[0] if found else kind_of(location, root)


def pull_path(gateway: Gateway, text: str, project: str | None = None, *, force: bool = False,
              root: Path | None = None) -> Outcome | None:
    """`pull` of `style-classes/PDK/X`, `scripts/PDK/X` or a local copy of one; None for anything else."""
    found = pull_kind(text)
    if found:
        return pull(gateway, found[0], found[1], project, force=force, root=root)
    location = Path(text)
    kind = kind_of(location, root) if location.exists() else None
    if kind is None:
        return None
    try:
        _, _, local_project, path = _locate(kind, location, root)
    except ValueError as exc:
        return Outcome(False, f"pdk: {exc}")
    return pull(gateway, kind, path, project or local_project, force=force, root=root)


def _directory(location: Path) -> Path:
    return location.parent if any(location.name == kind.file for kind in KINDS) else location


def _relative(directory: Path, root: Path | None) -> tuple[str, ...]:
    try:
        return directory.resolve().relative_to((root or work_root()).resolve()).parts
    except ValueError:
        return ()


def _locate(kind: Kind, location: Path, root: Path | None) -> tuple[Path, dict[str, Any] | None, str, str]:
    """Directory, sidecar, project and resource path of a local copy."""
    directory = _directory(location)
    sidecar = read_sidecar(directory)
    if sidecar and sidecar.get("project"):
        return directory, sidecar, sidecar["project"], sidecar["path"]
    parts = _relative(directory, root)
    if len(parts) < 3 or parts[1] != kind.folder:
        raise ValueError(f"{directory} has no {SIDECAR} and is not under {root or work_root()}/<project>/"
                         f"{kind.folder}/<path>")
    return directory, sidecar, parts[0], "/".join(parts[2:])


def _local_location(gateway: Gateway, location: Path, root: Path | None) -> Path:
    """A local copy as given, or `style-classes/PDK/X`-like text mapped to its folder under work/<sandbox>/."""
    if location.exists():
        return location
    found = pull_kind(location.as_posix())
    if found is None:
        return location
    kind, path = found
    return local_dir(_sandbox(gateway), f"{kind.folder}/{path}", root)


def _write_sidecar(kind: Kind, directory: Path, project: str, path: str, state: dict[str, Any], text: str) -> None:
    sidecar = {"project": project, "type": kind.type, "path": path, "signature": state.get("signature"),
               "lastModification": state.get("lastModification"), "base": {kind.file: text}}
    (directory / SIDECAR).write_text(json.dumps(sidecar, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (directory / kind.gateway_copy).unlink(missing_ok=True)


# --- checks ---------------------------------------------------------------------------------------


def check_style(text: str, bundle: Bundle) -> list[Diagnostic]:
    """A style class: {"base": {"style", "animation"}, "variants": [{"pseudo" | "media", "style", "animation"}]},
    with known CSS properties (camelCase, or --custom)."""
    try:
        data = json.loads(text)
    except ValueError as exc:
        return [make("S000", "error", "", "", f"invalid JSON: {exc}", "fix the JSON syntax (quotes, commas, braces)")]
    if not isinstance(data, dict):
        return [make("P005", "error", "", "", "a style class is a JSON object",
                     'start from {"base": {"style": {"color": "#323232"}}}')]
    out: list[Diagnostic] = []
    pseudo = (bundle.schemas.get("schemas/style-class-schema.json") or {}).get("properties", {}).get("pseudo", {})
    for key in data:
        if key not in ("base", "variants"):
            out.append(make("P005", "error", f"/{key}", "", f"unknown key '{key}' in a style class",
                            "a style class has `base` and optionally `variants`"))
    blocks: list[tuple[str, Any, str]] = [("/base", data.get("base"), "base")]
    variants = data.get("variants", [])
    if not isinstance(variants, list):
        out.append(make("P005", "error", "/variants", "", "`variants` must be a list",
                        'e.g. [{"pseudo": "hover", "style": {...}}]'))
        variants = []
    blocks += [(f"/variants/{i}", variant, "variant") for i, variant in enumerate(variants)]
    for pointer, block, role in blocks:
        if block is None and role == "base":
            continue
        if not isinstance(block, dict):
            out.append(make("P005", "error", pointer, "", f"the {role} must be an object", '{"style": {...}}'))
            continue
        for key in block:
            if key not in STYLE_KEYS[role]:
                out.append(make("P005", "error", f"{pointer}/{key}", "", f"unknown key '{key}' in the {role}",
                                f"a {role} has {', '.join(STYLE_KEYS[role])}"))
        if role == "variant" and "pseudo" in block and block["pseudo"] not in pseudo.get("enum", [block["pseudo"]]):
            out.append(make("P005", "error", f"{pointer}/pseudo", "", f"unknown pseudo-class {block['pseudo']!r}",
                            f"one of {', '.join(pseudo.get('enum', []))}"))
        style = block.get("style", {})
        if not isinstance(style, dict):
            out.append(make("P005", "error", f"{pointer}/style", "", "`style` must be an object",
                            '{"color": "#323232"}'))
            continue
        for key in style:
            if not is_css_key(key, bundle):
                out.append(make("P006", "warning", f"{pointer}/style/{key}", "", f"unknown style property '{key}'",
                                style_hint(key, bundle)))
    return out


def check_script(text: str, gateway: Gateway, bundle: Bundle, *, strict: bool = False) -> list[Diagnostic]:
    """A project script: its `system.*` names (with the gateway's list) and its syntax (the gateway's Jython)."""
    out = code.system_names(text, "", "", bundle.functions) if bundle.functions else []
    try:
        results = gateway.compile_scripts([{"id": "0", "code": text}])
    except GatewayError:
        if strict:
            raise
        return out
    return out + code.compile_diagnostics(results, {"0": ("", "")}, offset=0)


def _check(kind: Kind, text: str, gateway: Gateway, bundle: Bundle) -> list[Diagnostic]:
    return check_style(text, bundle) if kind is STYLE_CLASS else check_script(text, gateway, bundle)


# --- pull -----------------------------------------------------------------------------------------


def pull(gateway: Gateway, kind: Kind, path: str, project: str | None = None, *, force: bool = False,
         root: Path | None = None) -> Outcome:
    """Download a style class or script, or every one below a folder, with its signature."""
    project = project or _sandbox(gateway)
    found = gateway.resource(project, kind.type, path)
    paths = [child["path"] for child in found.get("children", []) if not child.get("folder")] \
        if found.get("folder") else [found["path"]]
    if not paths:
        return Outcome(True, f"{project}:{kind.folder}/{path} is an empty folder")
    lines, ok = [], True
    for each in paths:
        state = found if not found.get("folder") else gateway.resource(project, kind.type, each)
        directory = local_dir(project, f"{kind.folder}/{each}", root)
        text = state.get("files", {}).get(kind.file)
        if text is None:
            lines.append(f"skip {project}:{each}: no {kind.file}")
            ok = False
            continue
        sidecar = read_sidecar(directory)
        local = directory / kind.file
        if not force and sidecar and local.is_file() \
                and _norm(local.read_text(encoding="utf-8")) != _norm(sidecar["base"].get(kind.file)):
            lines.append(f"skip {project}:{each}: {_shown(local)} has local edits; deploy them first, or pull "
                         "--force to discard them")
            ok = False
            continue
        directory.mkdir(parents=True, exist_ok=True)
        _write(local, text)
        _write_sidecar(kind, directory, project, each, state, text)
        lines.append(f"pulled {kind.name} {project}:{each} -> {_shown(directory)} ({_stamp(state)})")
    return Outcome(ok, "\n".join(lines))


# --- deploy, promote, delete ----------------------------------------------------------------------


def deploy(gateway: Gateway, kind: Kind, location: Path, bundle: Bundle, *, resolved: bool = False,
           root: Path | None = None, budget: int = DEFAULT_BUDGET, _promoting: bool = False) -> Outcome:
    """Check the local copy and write it with the signature of the pull (create if never pulled)."""
    location = _local_location(gateway, location, root)
    try:
        directory, sidecar, project, path = _locate(kind, location, root)
        signature = _expected_signature(sidecar, resolved)
    except ValueError as exc:
        return Outcome(False, f"pdk: {exc}")
    if project != _roles(gateway)[_promoting]:
        return Outcome(False, f"pdk: {kind.plural} are deployed to the sandbox ({_roles(gateway)[0]}) and reach the "
                              "target only through promote --to-project")
    local = directory / kind.file
    if not local.is_file():
        return Outcome(False, f"pdk: {local} does not exist")
    text = local.read_text(encoding="utf-8")
    if sidecar and not resolved and not sidecar.get("conflict") \
            and _norm(text) == _norm(sidecar["base"].get(kind.file)):
        return Outcome(True, f"{kind.name} {project}:{path} unchanged since the pull ({_short(signature)}); "
                             "nothing to deploy")
    diagnostics = _check(kind, text, gateway, bundle)
    if counts(diagnostics)["error"]:
        report = render(diagnostics, title=f"{_shown(local)} [{project}:{path}]", budget=budget)
        return Outcome(False, report + "\nNot deployed: fix the errors above first.")
    try:
        written = gateway.put_resource(project, kind.type, path, {kind.file: text}, signature=signature,
                                       scope=kind.scope)
    except GatewayError as exc:
        if exc.code != "G008":
            raise
        return Outcome(False, _conflict(kind, exc, directory, sidecar, project, path, text, "deploy", budget))
    _write_sidecar(kind, directory, project, path, written, text)
    warnings = counts(diagnostics)["warning"]
    note = f", {warnings} warning(s)" if warnings else ""
    hint = "\nnote: open sessions pick it up on reload" if kind is STYLE_CLASS else \
        f"\nnote: call it as {path.replace('/', '.')}.<function>() from scripts of this project"
    return Outcome(True, f"{written.get('op', 'written')} {kind.name} {project}:{path} "
                         f"{_short(signature) or 'new'} -> {_short(written.get('signature'))}{note}" + hint)


def promote(gateway: Gateway, kind: Kind, location: Path, bundle: Bundle, *, to: str | None = None,
            to_project: str | None = None, resolved: bool = False, root: Path | None = None,
            budget: int = DEFAULT_BUDGET) -> Outcome:
    """Copy a deployed sandbox `PDK/X` to `X` (or `to`) in the sandbox, or with `to_project` to the target."""
    location = _local_location(gateway, location, root)
    try:
        directory, sidecar, project, path = _locate(kind, location, root)
    except ValueError as exc:
        return Outcome(False, f"pdk: {exc}")
    sandbox, target = _roles(gateway)
    if project != sandbox:
        return Outcome(False, f"pdk: promote starts from the sandbox ({sandbox}), not {project}")
    if sidecar is None or sidecar.get("conflict"):
        return Outcome(False, f"pdk: deploy {_shown(directory)} first (it has no clean {SIDECAR}); promote copies "
                              "what is on the gateway")
    local = directory / kind.file
    mine = local.read_text(encoding="utf-8") if local.is_file() else None
    if _norm(mine) != _norm(sidecar["base"].get(kind.file)):
        return Outcome(False, f"pdk: {_shown(directory)} has local edits: deploy them before promoting")
    destination_project = sandbox
    if to_project is not None:
        if not target:
            return Outcome(False, "pdk: the gateway has no target project: add -Dpdk.target=<project> to "
                                  "ignition.conf and restart it")
        if to_project not in ("target", target):
            return Outcome(False, f"pdk: {to_project!r} is not the target project ({target})")
        destination_project = target
    prefix = SANDBOX_FOLDER + "/"
    if to is None and scratch(path):
        return Outcome(False, f"pdk: {path} is in a scratch folder ({prefix}_*), not finished work: pass --to <path> "
                              "to promote it anyway")
    destination = (to or (path[len(prefix):] if path.startswith(prefix) else "")).strip("/")
    if not destination or destination == SANDBOX_FOLDER or destination.startswith(prefix):
        return Outcome(False, f"pdk: promote takes {kind.plural} from {prefix} to a path outside it: pass --to <path>")
    current = gateway.resource(project, kind.type, path)
    if current.get("folder") or current.get("signature") != sidecar.get("signature"):
        return Outcome(False, f"pdk: {project}:{path} changed on the gateway after your deploy: pull it, review and "
                              "deploy before promoting")
    target_dir = local_dir(destination_project, f"{kind.folder}/{destination}", root)
    target_dir.mkdir(parents=True, exist_ok=True)
    _write(target_dir / kind.file, current["files"][kind.file])
    if read_sidecar(target_dir) is None:
        try:
            existing = gateway.resource(destination_project, kind.type, destination)
        except GatewayError as exc:
            if exc.code != "G007":
                raise
            existing = None
        if existing and not existing.get("folder"):
            (target_dir / SIDECAR).write_text(json.dumps({  # promote over it only with --resolved, like views
                "project": destination_project, "type": kind.type, "path": destination,
                "signature": existing.get("signature"), "lastModification": existing.get("lastModification"),
                "base": {kind.file: existing["files"].get(kind.file)},
                "conflict": {"signature": existing.get("signature")}}, indent=2) + "\n", encoding="utf-8")
            if not resolved:
                return Outcome(False, f"pdk: {destination_project}:{destination} already exists: promote with "
                                      "--resolved to overwrite it (its signature is in the destination's sidecar)")
    outcome = deploy(gateway, kind, target_dir, bundle, resolved=resolved, root=root, budget=budget,
                     _promoting=destination_project != sandbox)
    return Outcome(outcome.ok, f"promote {kind.name} {project}:{path} -> {destination_project}:{destination}: "
                               f"{outcome.text}")


def delete(gateway: Gateway, kind: Kind, location: Path, *, resolved: bool = False, root: Path | None = None,
           budget: int = DEFAULT_BUDGET) -> Outcome:
    """Delete a pulled style class or script on the sandbox, only if it is still the version you pulled."""
    location = _local_location(gateway, location, root)
    try:
        directory, sidecar, project, path = _locate(kind, location, root)
        if sidecar is None:
            raise ValueError(f"{directory} has no {SIDECAR}: pull it first, so the delete is signed")
        signature = _expected_signature(sidecar, resolved)
    except ValueError as exc:
        return Outcome(False, f"pdk: {exc}")
    if project != _roles(gateway)[0]:
        return Outcome(False, f"pdk: {project} is not the sandbox: PDK deletes only there")
    if signature is None:
        return Outcome(False, f"pdk: {project}:{path} no longer exists on the gateway")
    try:
        gateway.delete_resource(project, kind.type, path, signature)
    except GatewayError as exc:
        if exc.code != "G008" or "current" not in exc.payload:
            raise
        local = directory / kind.file
        text = local.read_text(encoding="utf-8") if local.is_file() else None
        return Outcome(False, _conflict(kind, exc, directory, sidecar, project, path, text, "delete", budget))
    (directory / SIDECAR).unlink(missing_ok=True)
    (directory / kind.gateway_copy).unlink(missing_ok=True)
    return Outcome(True, f"deleted {kind.name} {project}:{path} ({_short(signature)}). The local copy stays in "
                         f"{_shown(directory)}; deploying it creates it again.")


def _conflict(kind: Kind, error: GatewayError, directory: Path, sidecar: dict[str, Any] | None, project: str,
              path: str, text: str | None, action: str, budget: int) -> str:
    """Save the gateway version and the signature to resolve with; report the diffs."""
    current = error.payload.get("current") or None
    theirs = (current or {}).get("files", {}).get(kind.file)
    pulled = sidecar is not None
    if not pulled and current is not None:  # it exists but was never pulled: keep what --resolved overwrites
        sidecar = {"project": project, "type": kind.type, "path": path, "signature": None, "lastModification": None,
                   "base": {}}
    if sidecar is not None:
        sidecar["conflict"] = {"signature": current.get("signature") if current else None,
                               "lastModification": current.get("lastModification") if current else None}
        (directory / SIDECAR).write_text(json.dumps(sidecar, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if theirs is not None:
        _write(directory / kind.gateway_copy, theirs)
    shown = _shown(directory)
    base = _norm(sidecar["base"].get(kind.file)) if pulled else None
    what = f"{kind.name} {project}:{path}"
    if current is None:
        head = f"G008 conflict: {what} was deleted on the gateway after your pull; nothing was written."
    elif not pulled:
        head = f"G008 conflict: {what} already exists on the gateway ({_stamp(current)}); nothing was written."
    else:
        head = (f"G008 conflict: {what} changed on the gateway after your pull ({_short(sidecar.get('signature'))} "
                f"-> {_stamp(current)}); nothing was written.")
    lines = [head]
    if current is not None and base is not None:
        lines.append(_diff("Gateway changes since your pull", base, _norm(theirs) or "", "pull", "gateway"))
        if action == "deploy":
            lines.append(_diff("Your changes", base, _norm(text) or "", "pull", "yours"))
    elif current is not None and action == "deploy":
        lines.append(_diff("Gateway version -> yours", _norm(theirs) or "", _norm(text) or "", "gateway", "yours"))
    if current is None:
        lines.append(f"To create it again from your copy: `pdk deploy {shown} --resolved`.")
    else:
        lines.append(f"The gateway version is in {shown}/{kind.gateway_copy}. Merge its changes into {kind.file}, "
                     f"then `pdk {action} {shown} --resolved`; or discard yours with `pdk pull {shown} --force`.")
    return _budgeted("\n".join(lines), budget)

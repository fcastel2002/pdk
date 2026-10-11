"""Pull, deploy, promote and delete views through the pdk-gateway module.

A pulled view lives in `work/<project>/<view path>/view.json`, next to a `.pdk-base.json` sidecar with the
signature it was read at and its text. Every write sends that signature: if the view changed on the gateway
since (a Designer save, another agent), the module answers 409 and nothing is written. The 409 is reported as
a diff by JSON pointer — what changed on the gateway since the pull and what you changed — and the gateway
version is saved as `view.gateway.json` to merge from. `--resolved` then writes over that newer version.

Agent work lives under `PDK/` in the sandbox; `promote` copies `PDK/X` to `X` in the same project, signed the
same way, or to the target project (`-Dpdk.target`), which takes finished views only: no `PDK/`, no preview page.
Every deployed view gets a page at `/pdk/<view path>` in page-config, so it can be opened in a browser;
page-config is edited additively with its own signature and re-read on a 409.
"""

from __future__ import annotations

import json
import os
import re
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pdk import REPO_ROOT
from pdk.bundle import Bundle
from pdk.diagnostics import CHARS_PER_TOKEN, DEFAULT_BUDGET, Diagnostic, counts, join, render
from pdk.gateway.client import VIEWS, Gateway, GatewayError
from pdk.project import open_project
from pdk.validate import code, parse_view, validate_text
from pdk.validate.tree import build_tree, walk

WORK_ENV = "PDK_WORK_DIR"
SIDECAR = ".pdk-base.json"
VIEW_FILE = "view.json"
GATEWAY_COPY = "view.gateway.json"
SANDBOX_FOLDER = "PDK"
PAGE_CONFIG = "com.inductiveautomation.perspective/page-config"
PAGE_FILE = "config.json"
PREVIEW_PREFIX = "/pdk/"
MISSING: Any = object()  # a key or index absent on one side of a diff


@dataclass(frozen=True)
class Outcome:
    ok: bool
    text: str


def work_root() -> Path:
    return Path(os.environ.get(WORK_ENV) or REPO_ROOT / "work")


def local_dir(project: str, path: str, root: Path | None = None) -> Path:
    return (root or work_root()) / project / Path(*path.strip("/").split("/"))


# --- sidecar --------------------------------------------------------------------------------------


def read_sidecar(directory: Path) -> dict[str, Any] | None:
    try:
        data = json.loads((directory / SIDECAR).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_sidecar(directory: Path, project: str, path: str, state: dict[str, Any], text: str) -> None:
    sidecar = {
        "project": project,
        "type": VIEWS,
        "path": path,
        "signature": state.get("signature"),
        "lastModification": state.get("lastModification"),
        "base": {VIEW_FILE: text},
    }
    (directory / SIDECAR).write_text(json.dumps(sidecar, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (directory / GATEWAY_COPY).unlink(missing_ok=True)


def _target(location: Path, root: Path | None) -> tuple[Path, dict[str, Any] | None, str, str]:
    """Directory, sidecar, project and view path of a local view (a view.json or its folder)."""
    directory = location.parent if location.name.endswith(".json") else location
    sidecar = read_sidecar(directory)
    if sidecar:
        return directory, sidecar, sidecar["project"], sidecar["path"]
    try:
        parts = directory.resolve().relative_to((root or work_root()).resolve()).parts
    except ValueError:
        parts = ()
    if len(parts) < 2:
        raise ValueError(f"{directory} has no {SIDECAR} and is not under {root or work_root()}/<project>/<view path>")
    return directory, None, parts[0], "/".join(parts[1:])


def _local(gateway: Gateway, location: Path, root: Path | None) -> Path:
    """A local view as given, or a sandbox view path (PDK/X) mapped to its folder under work/<sandbox>/."""
    if location.exists() or location.is_absolute():
        return location
    try:
        candidate = local_dir(_sandbox(gateway), location.as_posix(), root)
    except GatewayError:
        return location
    return candidate if candidate.exists() else location


def _same(a: Any, b: Any) -> bool:
    """JSON equality: 1 == 1.0, but true != 1."""
    return a == b and isinstance(a, bool) == isinstance(b, bool)


def _parsed(text: str | None) -> Any:
    if text is None:
        return MISSING
    value, error = parse_view(text)
    return MISSING if error else value


# --- pull -----------------------------------------------------------------------------------------


def pull(gateway: Gateway, path: str, project: str | None = None, *, force: bool = False,
         root: Path | None = None) -> Outcome:
    """Download a view, or every view below a folder, with its signature. `path` may be a local copy."""
    from pdk import stylesheet
    if stylesheet.is_pull_path(path):
        return stylesheet.pull(gateway, project, force=force, root=root, path=path)
    local = Path(path)
    if ((local.parent if local.name == VIEW_FILE else local) / SIDECAR).is_file():
        _, _, project, path = _target(local, root)
    project = project or _sandbox(gateway)
    found = gateway.resource(project, VIEWS, path)
    paths = [child["path"] for child in found.get("children", []) if not child.get("folder")] \
        if found.get("folder") else [found["path"]]
    if not paths:
        return Outcome(True, f"{project}:{path} is an empty folder")
    lines, ok = [], True
    for view_path in paths:
        state = found if not found.get("folder") else gateway.resource(project, VIEWS, view_path)
        line, written = _pull_one(project, view_path, state, force, root)
        lines.append(line)
        ok = ok and written
    return Outcome(ok, "\n".join(lines))


def _pull_one(project: str, path: str, state: dict[str, Any], force: bool, root: Path | None) -> tuple[str, bool]:
    directory = local_dir(project, path, root)
    text = state.get("files", {}).get(VIEW_FILE)
    if text is None:
        return f"skip {project}:{path}: no {VIEW_FILE}", False
    sidecar = read_sidecar(directory)
    local = directory / VIEW_FILE
    if not force and sidecar and local.is_file():
        edited = not _same(_parsed(local.read_text(encoding="utf-8")), _parsed(sidecar["base"].get(VIEW_FILE)))
        if edited:
            return (f"skip {project}:{path}: {local} has local edits; deploy them first, or pull --force to "
                    "discard them"), False
    directory.mkdir(parents=True, exist_ok=True)
    local.write_text(text, encoding="utf-8")
    _write_sidecar(directory, project, path, state, text)
    return f"pulled {project}:{path} -> {_shown(directory)} ({_stamp(state)})", True


# --- deploy and delete ----------------------------------------------------------------------------


def deploy(gateway: Gateway, location: Path, bundle: Bundle, *, resolved: bool = False,
           root: Path | None = None, budget: int = DEFAULT_BUDGET, _promoting: bool = False) -> Outcome:
    """Validate the local view.json and write it with the signature of the pull (create if never pulled).

    The target project only gets views through promote, and never a preview page. The stylesheet goes to
    `pdk.stylesheet`.
    """
    from pdk import stylesheet
    location = _local(gateway, location, root)
    if stylesheet.is_stylesheet(location):
        return stylesheet.deploy(gateway, location, resolved=resolved, root=root, budget=budget)
    try:
        directory, sidecar, project, path = _target(location, root)
        signature = _expected_signature(sidecar, resolved)
    except ValueError as exc:
        return Outcome(False, f"pdk: {exc}")
    in_target = project == _roles(gateway)[1]
    if in_target and not _promoting:
        return Outcome(False, f"pdk: {project} is the target project: views reach it only with promote "
                              f"--to-project (iterate in the sandbox under {SANDBOX_FOLDER}/)")

    def preview() -> str:
        return "" if in_target else _preview_note(gateway, project, path)
    local = directory / VIEW_FILE
    if not local.is_file():
        return Outcome(False, f"pdk: {local} does not exist")
    text = local.read_text(encoding="utf-8")
    if sidecar and not resolved and not sidecar.get("conflict") \
            and _same(_parsed(text), _parsed(sidecar["base"].get(VIEW_FILE))):
        return Outcome(True, f"{project}:{path} unchanged since the pull ({_short(signature)}); nothing to deploy"
                             + preview())

    diagnostics = validate_text(text, bundle, project=_local_project(project), view_path=path)
    if not counts(diagnostics)["error"]:
        diagnostics += compiled(gateway, text)
    if counts(diagnostics)["error"]:
        report = render(diagnostics, title=f"{_shown(local)} [{project}:{path}]", budget=budget)
        return Outcome(False, report + "\nNot deployed: fix the errors above first.")

    try:
        written = gateway.put_resource(project, VIEWS, path, {VIEW_FILE: text}, signature=signature)
    except GatewayError as exc:
        if exc.code != "G008":
            raise
        return Outcome(False, _conflict(exc, directory, sidecar, project, path, text,
                                        "promote" if _promoting else "deploy", budget))
    _write_sidecar(directory, project, path, written, text)
    issues = counts(diagnostics)
    note = f", {issues['warning']} validation warning(s)" if issues["warning"] else ""
    return Outcome(True, f"{written.get('op', 'written')} {project}:{path} "
                         f"{_short(signature) or 'new'} -> {_short(written.get('signature'))}{note}"
                         + preview())


def compiled(gateway: Gateway, text: str, *, strict: bool = False) -> list[Diagnostic]:
    """E009 for the scripts of a view that the gateway's Jython does not compile. Without `strict`, nothing when
    the gateway cannot compile (an older module without the route); with it, that GatewayError propagates."""
    view, error = parse_view(text)
    if error or not isinstance(view, dict):
        return []
    sources, where = code.compile_requests(view)
    if not sources:
        return []
    try:
        results = gateway.compile_scripts(sources)
    except GatewayError:
        if strict:
            raise
        return []
    return code.compile_diagnostics(results, where)


def compiler(gateway: Gateway) -> Callable[[str], list[Diagnostic]]:
    """For validate's `compiler`: the scripts of a view compiled by the gateway (GatewayError if it cannot)."""
    return lambda text: compiled(gateway, text, strict=True)


def promote(gateway: Gateway, location: Path, bundle: Bundle, *, to: str | None = None,
            to_project: str | None = None, resolved: bool = False, root: Path | None = None,
            budget: int = DEFAULT_BUDGET) -> Outcome:
    """Copy a deployed sandbox view `PDK/X` to `X` (or `to`) in the same project, with the target's signature.

    With `to_project` ("target" or its name) the copy goes to the target project instead; there it may not
    reference `PDK/` views, and views it references that the target lacks are reported.

    The target's own sidecar holds the signature it was promoted or pulled at: a target someone edited since
    gives the usual 409; a target that exists but was never pulled gives a create conflict.
    """
    from pdk import stylesheet
    location = _local(gateway, location, root)
    if stylesheet.is_stylesheet(location):
        return stylesheet.promote(gateway, location, to_project=to_project, resolved=resolved, root=root,
                                  budget=budget)
    try:
        directory, sidecar, project, path = _target(location, root)
    except ValueError as exc:
        return Outcome(False, f"pdk: {exc}")
    if sidecar is None or sidecar.get("conflict"):
        return Outcome(False, f"pdk: deploy {_shown(directory)} first (it has no clean {SIDECAR}); promote copies "
                              "what is on the gateway")
    local = directory / VIEW_FILE
    text = local.read_text(encoding="utf-8") if local.is_file() else None
    if not _same(_parsed(text), _parsed(sidecar["base"].get(VIEW_FILE))):
        return Outcome(False, f"pdk: {_shown(directory)} has local edits: deploy them before promoting")
    sandbox, target_project = _roles(gateway)
    destination = project
    if to_project is not None:
        if not target_project:
            return Outcome(False, "pdk: the gateway has no target project: add -Dpdk.target=<project> to "
                                  "ignition.conf and restart it")
        if to_project not in ("target", target_project):
            return Outcome(False, f"pdk: {to_project!r} is not the target project ({target_project})")
        if project != sandbox:
            return Outcome(False, f"pdk: promote to the target starts from the sandbox ({sandbox}), not {project}")
        destination = target_project
    current = gateway.resource(project, VIEWS, path)
    if current.get("signature") != sidecar.get("signature"):
        return Outcome(False, f"pdk: {project}:{path} changed on the gateway after your deploy: pull it, review "
                              "and deploy before promoting")
    if to is None:
        if not path.startswith(SANDBOX_FOLDER + "/"):
            return Outcome(False, f"pdk: {path} is not under {SANDBOX_FOLDER}/: pass --to <view path>")
        to = path[len(SANDBOX_FOLDER) + 1:]
    to = to.strip("/")
    if not to or to == SANDBOX_FOLDER or to.startswith(SANDBOX_FOLDER + "/"):
        return Outcome(False, f"pdk: promote takes a view out of {SANDBOX_FOLDER}/; {to!r} is not a valid target")
    view_text = current["files"][VIEW_FILE]
    references = sorted(set(re.findall('"(' + SANDBOX_FOLDER + '/[^"]+)"', view_text)))
    shown_to = to if destination == project else f"{destination}:{to}"
    if references and destination != project:
        return Outcome(False, f"pdk: {project}:{path} references sandbox views ({', '.join(references[:5])}): a "
                              f"view in {destination} cannot; promote those first and update the paths")
    target = local_dir(destination, to, root)
    target.mkdir(parents=True, exist_ok=True)
    (target / VIEW_FILE).write_text(view_text, encoding="utf-8")
    outcome = deploy(gateway, target, bundle, resolved=resolved, root=root, budget=budget, _promoting=True)
    note = ""
    if outcome.ok and references:
        note = (f"\nnote: it still references sandbox views ({', '.join(references[:5])}); promote those and "
                "update the paths if they should move too")
    elif outcome.ok and destination != project:
        missing = _missing_views(gateway, view_text, sandbox, destination, to)
        note = (f"\nwarning: it references views {destination} does not have ({', '.join(missing[:5])}); "
                "promote them too") if missing else ""
    return Outcome(outcome.ok, f"promote {project}:{path} -> {shown_to}: {outcome.text}{note}")


def _missing_views(gateway: Gateway, view_text: str, source: str, destination: str, itself: str) -> list[str]:
    """Strings of the view that name a view of `source` but not of `destination`."""
    try:
        known = {child["path"] for child in gateway.resource(source, VIEWS).get("children", [])}
        present = {child["path"] for child in gateway.resource(destination, VIEWS).get("children", [])}
    except GatewayError:
        return []
    found: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, str) and value.strip("/") in known:
            found.add(value.strip("/"))

    walk(_parsed(view_text))
    return sorted(found - present - {itself})


def delete(gateway: Gateway, location: Path, *, resolved: bool = False, root: Path | None = None,
           budget: int = DEFAULT_BUDGET) -> Outcome:
    """Delete a pulled view on the gateway, only if it is still the version you pulled."""
    from pdk import stylesheet
    location = _local(gateway, location, root)
    if stylesheet.is_stylesheet(location):
        return stylesheet.delete(gateway, location, resolved=resolved, root=root, budget=budget)
    try:
        directory, sidecar, project, path = _target(location, root)
        if sidecar is None:
            raise ValueError(f"{directory} has no {SIDECAR}: pull the view first, so the delete is signed")
        signature = _expected_signature(sidecar, resolved)
    except ValueError as exc:
        return Outcome(False, f"pdk: {exc}")
    if project == _roles(gateway)[1]:
        return Outcome(False, f"pdk: {project} is the target project: PDK never deletes there (remove the view in the "
                              "Designer); delete works on the sandbox")
    if signature is None:
        return Outcome(False, f"pdk: {project}:{path} no longer exists on the gateway")
    try:
        result = gateway.delete_resource(project, VIEWS, path, signature)
    except GatewayError as exc:
        if exc.code != "G008" or "current" not in exc.payload:
            raise
        local = directory / VIEW_FILE
        text = local.read_text(encoding="utf-8") if local.is_file() else None
        return Outcome(False, _conflict(exc, directory, sidecar, project, path, text, "delete", budget))
    (directory / SIDECAR).unlink(missing_ok=True)
    (directory / GATEWAY_COPY).unlink(missing_ok=True)
    warning = remove_preview(gateway, project, path)
    pruned = result.get("pruned") or []
    folders = f"; removed the empty folder(s) {', '.join(pruned)}" if pruned else ""
    return Outcome(True, f"deleted {project}:{path} ({_short(signature)}){folders}. "
                         f"The local copy stays in {_shown(directory)}; deploying it creates the view again."
                         + (f"\nwarning: preview page not removed: {warning}" if warning else ""))


# --- preview pages --------------------------------------------------------------------------------


def page_url(view_path: str) -> str:
    """The preview page of a view: /pdk/<view path>, blanks turned into dashes."""
    return PREVIEW_PREFIX + "/".join(re.sub(r"\s+", "-", segment) for segment in view_path.strip("/").split("/"))


def preview_url(base_url: str, project: str, view_path: str) -> str:
    return (f"{base_url.rstrip('/')}/data/perspective/client/{urllib.parse.quote(project)}"
            f"{urllib.parse.quote(page_url(view_path))}")


def ensure_preview(gateway: Gateway, project: str, view_path: str) -> str | None:
    """Map /pdk/<view path> to the view in page-config; a warning if it cannot."""
    page = page_url(view_path)
    taken: list[str] = []

    def add(pages: dict[str, Any]) -> bool:
        existing = pages.get(page)
        if isinstance(existing, dict) and existing.get("viewPath") == view_path:
            return False
        if existing is not None:
            taken.append(str(existing.get("viewPath") if isinstance(existing, dict) else existing))
            return False
        pages[page] = {"viewPath": view_path}
        return True

    warning = _edit_pages(gateway, project, add)
    return warning or (f"{page} already opens {taken[0]}; left as is" if taken else None)


def remove_preview(gateway: Gateway, project: str, view_path: str) -> str | None:
    page = page_url(view_path)

    def remove(pages: dict[str, Any]) -> bool:
        existing = pages.get(page)
        if isinstance(existing, dict) and existing.get("viewPath") == view_path:
            del pages[page]
            return True
        return False

    return _edit_pages(gateway, project, remove)


def _edit_pages(gateway: Gateway, project: str, change: Callable[[dict[str, Any]], bool],
                attempts: int = 3) -> str | None:
    """Apply `change` to page-config's pages with its signature; on a 409 re-read and apply it again."""
    for _ in range(attempts):
        try:
            current = gateway.resource(project, PAGE_CONFIG)
            config = json.loads(current["files"][PAGE_FILE])
        except GatewayError as exc:
            return str(exc)
        except (KeyError, ValueError):
            return f"the {PAGE_FILE} of page-config cannot be read"
        pages = config.setdefault("pages", {})
        if not change(pages):
            return None
        try:
            gateway.put_resource(project, PAGE_CONFIG, "", {PAGE_FILE: json.dumps(config, indent=2)},
                                 signature=current["signature"])
            return None
        except GatewayError as exc:
            if exc.code != "G008":
                return str(exc)
    return "page-config kept changing while it was being updated"


def _preview_note(gateway: Gateway, project: str, path: str) -> str:
    warning = ensure_preview(gateway, project, path)
    return f"\npreview: {preview_url(gateway.url, project, path)}" + (f" (warning: {warning})" if warning else "")


def _expected_signature(sidecar: dict[str, Any] | None, resolved: bool) -> str | None:
    if not resolved:
        return sidecar.get("signature") if sidecar else None
    if not sidecar or "conflict" not in sidecar:
        raise ValueError("--resolved needs a previous conflict (a 409 on deploy or delete); there is none")
    return sidecar["conflict"].get("signature")


def _conflict(error: GatewayError, directory: Path, sidecar: dict[str, Any] | None, project: str, path: str,
              text: str | None, action: str, budget: int) -> str:
    """Save the gateway version and the signature to resolve with; report the three-way diff."""
    current = error.payload.get("current") or None
    theirs_text = (current or {}).get("files", {}).get(VIEW_FILE)
    pulled = sidecar is not None
    if not pulled and current is not None:  # it exists but was never pulled: keep what --resolved overwrites
        sidecar = {"project": project, "type": VIEWS, "path": path, "signature": None, "lastModification": None,
                   "base": {}}
    if sidecar is not None:
        sidecar["conflict"] = {
            "signature": current.get("signature") if current else None,
            "lastModification": current.get("lastModification") if current else None,
        }
        (directory / SIDECAR).write_text(json.dumps(sidecar, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if theirs_text is not None:
        (directory / GATEWAY_COPY).write_text(theirs_text, encoding="utf-8")

    base = _parsed(sidecar["base"].get(VIEW_FILE)) if sidecar else MISSING
    mine, theirs = _parsed(text), _parsed(theirs_text)
    writing = action in ("deploy", "promote")
    if current is None:
        head = f"G008 conflict: {project}:{path} was deleted on the gateway after your pull; nothing was written."
    elif not pulled:
        head = (f"G008 conflict: {project}:{path} already exists on the gateway ({_stamp(current)}); "
                "nothing was written.")
    else:
        head = (f"G008 conflict: {project}:{path} changed on the gateway after your pull "
                f"({_short(sidecar.get('signature'))} -> {_stamp(current)}); nothing was written.")
    lines = [head]
    if current is not None and base is not MISSING:
        theirs_changes = json_diff(base, theirs)
        lines.append(diff_section("Gateway changes since your pull", theirs_changes, base, theirs) if theirs_changes
                     else "Gateway changes since your pull: none in the view content (only formatting or other "
                          "files such as the thumbnail); deploying with --resolved loses nothing")
        if writing:
            lines.append(diff_section("Your changes", json_diff(base, mine), base, mine))
            overlap = overlapping(json_diff(base, theirs), json_diff(base, mine), theirs, mine)
            if overlap:
                lines.append(f"Both changed ({len(overlap)}): " + ", ".join(overlap[:12])
                             + (" ..." if len(overlap) > 12 else ""))
    elif current is not None and writing:
        lines.append(diff_section("Gateway version -> yours", json_diff(theirs, mine), theirs, mine))
    lines.append(_resolution(directory, action, current is not None, pulled))
    return _budgeted("\n".join(line for line in lines if line), budget)


def _resolution(directory: Path, action: str, exists: bool, pulled: bool) -> str:
    shown = _shown(directory)
    if action == "delete":
        return (f"The gateway version is in {shown}/{GATEWAY_COPY}. To delete it anyway: `pdk delete {shown} "
                f"--resolved`; to keep it: `pdk pull {shown} --force`.")
    if action == "promote":
        if not exists:
            return "To create it again: repeat the promote with --resolved."
        if not pulled:
            return (f"The gateway version is in {shown}/{GATEWAY_COPY}. To replace it with yours: repeat the "
                    "promote with --resolved; to keep it: promote to another path (--to).")
        return (f"The gateway version is in {shown}/{GATEWAY_COPY}. Merge its changes into the sandbox view, "
                "deploy it, and repeat the promote with --resolved.")
    if not exists:
        return f"To create it again from your copy: `pdk deploy {shown} --resolved`."
    if not pulled:
        return (f"The gateway version is in {shown}/{GATEWAY_COPY}. To replace it with yours: "
                f"`pdk deploy {shown} --resolved`; to work on it instead: `pdk pull {shown} --force`.")
    return (f"The gateway version is in {shown}/{GATEWAY_COPY}. Merge its changes into {VIEW_FILE}, then "
            f"`pdk deploy {shown} --resolved`; or discard yours with `pdk pull {shown} --force`.")


# --- JSON diff ------------------------------------------------------------------------------------


def json_diff(a: Any, b: Any, pointer: str = "") -> list[tuple[str, Any, Any]]:
    """(pointer, old, new) for every leaf that differs; MISSING marks an added or removed member."""
    if isinstance(a, dict) and isinstance(b, dict):
        out: list[tuple[str, Any, Any]] = []
        for key in [*a, *(k for k in b if k not in a)]:
            if key in a and key in b:
                out.extend(json_diff(a[key], b[key], join(pointer, key)))
            else:
                out.append((join(pointer, key), a.get(key, MISSING), b.get(key, MISSING)))
        return out
    if isinstance(a, list) and isinstance(b, list):
        out = []
        for i in range(max(len(a), len(b))):
            if i < len(a) and i < len(b):
                out.extend(json_diff(a[i], b[i], join(pointer, i)))
            else:
                out.append((join(pointer, i), a[i] if i < len(a) else MISSING, b[i] if i < len(b) else MISSING))
        return out
    return [] if _same(a, b) else [(pointer, a, b)]


def overlapping(theirs: list[tuple[str, Any, Any]], mine: list[tuple[str, Any, Any]],
                theirs_doc: Any, mine_doc: Any) -> list[str]:
    """Pointers both sides changed (one inside the other) to different results."""
    found: list[str] = []
    for p, _, _ in theirs:
        for q, _, _ in mine:
            outer = p if q == p or q.startswith(p + "/") else q if p.startswith(q + "/") else None
            if outer is not None and not _same(_at(theirs_doc, outer), _at(mine_doc, outer)) and outer not in found:
                found.append(outer)
    return found


def _at(document: Any, pointer: str) -> Any:
    node = document
    for raw in pointer.split("/")[1:]:
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict) and token in node:
            node = node[token]
        elif isinstance(node, list) and token.isdigit() and int(token) < len(node):
            node = node[int(token)]
        else:
            return MISSING
    return node


def diff_section(title: str, changes: list[tuple[str, Any, Any]], before: Any, after: Any) -> str:
    """One line per change, with the component it falls in (named from the side where it exists)."""
    if not changes:
        return f"{title}: none"
    trees = [walk(build_tree(doc)) if isinstance(doc, dict) else [] for doc in (before, after)]
    lines = [f"{title} ({len(changes)}):"]
    for pointer, old, new in changes:
        mark = "+" if old is MISSING else "-" if new is MISSING else "~"
        nodes = trees[0] if mark == "-" else trees[1]
        owner = max((n for n in nodes if pointer == n.pointer or pointer.startswith(n.pointer + "/")),
                    key=lambda n: len(n.pointer), default=None)
        where = f" ({owner.path})" if owner else ""
        value = _value(new) if mark == "+" else _value(old) if mark == "-" else f"{_value(old)} -> {_value(new)}"
        lines.append(f"  {mark} {pointer}{where}: {value}")
    return "\n".join(lines)


def _value(value: Any, width: int = 60) -> str:
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return text if len(text) <= width else text[: width - 3] + "..."


def _budgeted(text: str, budget: int) -> str:
    limit = int(budget * CHARS_PER_TOKEN)
    if len(text) <= limit:
        return text
    lines = text.splitlines()
    head, tail = lines[:-1], lines[-1]
    kept: list[str] = []
    used = len(tail) + 80
    for line in head:
        if used + len(line) + 1 > limit:
            break
        kept.append(line)
        used += len(line) + 1
    return "\n".join([*kept, f"... +{len(head) - len(kept)} more lines (see the files)", tail])


# --- helpers --------------------------------------------------------------------------------------


def _roles(gateway: Gateway) -> tuple[str | None, str | None]:
    """The sandbox and the target project (either may be None)."""
    health = gateway.health()
    return health.get("sandbox"), health.get("target")


def _sandbox(gateway: Gateway) -> str:
    sandbox = gateway.health().get("sandbox")
    if not sandbox:
        raise GatewayError("G005", "The module is read-only: pass --project to pull from a project")
    return sandbox


def _local_project(name: str):
    """The project as it is on disk, for view/param references; None if it is not a local project."""
    try:
        return open_project(name)
    except FileNotFoundError:
        return None


def _short(signature: str | None) -> str:
    return signature[:10] if signature else ""


def _stamp(state: dict[str, Any]) -> str:
    modification = state.get("lastModification") or {}
    who = f", {modification.get('actor')} {modification.get('timestamp')}" if modification.get("actor") else ""
    return f"signature {_short(state.get('signature'))}{who}"


def _shown(path: Path) -> str:
    try:
        return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return path.as_posix()

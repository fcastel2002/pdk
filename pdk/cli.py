"""Command line entry point: `pdk doctor | validate | catalog | schemas | hook | gateway | pull | deploy | promote |
delete | tags | open | inspect | screenshot`."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pdk.bundle import (
    BundleError,
    available_versions,
    extract_bundle,
    find_ignition_home,
    find_perspective_modl,
    load_bundle,
    schemas_root,
)
from pdk.catalog import TOPICS, catalog
from pdk.diagnostics import DEFAULT_BUDGET
from pdk.report import report_paths, resolve_project
from pdk.validate import validate_paths

EXIT_OK, EXIT_FINDINGS, EXIT_USAGE = 0, 1, 2


def _utf8_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")


_UNSAFE_FILE_CHARS = re.compile(r'[\\/:*?"<>|]')


def screenshot_name(view: str, component: str | None = None) -> str:
    """File stem of a default screenshot: the view path with "/" as "__", then "__" and the component (its "/"
    become "_"). Characters that file names cannot hold become "_"."""
    stem = view.strip("/").replace("/", "__")
    if component and component.strip("/"):
        stem += "__" + component.strip("/")
    return _UNSAFE_FILE_CHARS.sub("_", stem)


def default_screenshot(view: str, component: str | None = None) -> Path:
    """Where `pdk screenshot` writes when --out is omitted: <work>/_screenshots/<screenshot_name>.png, with the
    work folder from PDK_WORK_DIR or the repository's work/ (gitignored). The path is absolute."""
    from pdk.sync import work_root

    return (work_root() / "_screenshots" / f"{screenshot_name(view, component)}.png").resolve()


def _cmd_validate(args: argparse.Namespace) -> int:
    bundle = load_bundle(args.perspective)
    project = resolve_project(args.project)
    compiler = None
    if args.compile:
        from pdk import sync
        from pdk.gateway.client import Gateway

        compiler = sync.compiler(Gateway(url=args.url) if args.url else Gateway())
    try:
        return _validate(args, bundle, project, compiler)
    except Exception as exc:  # a GatewayError from --compile: the gateway could not compile
        from pdk.gateway.client import GatewayError
        if not isinstance(exc, GatewayError):
            raise
        print(f"pdk: --compile needs the gateway's module (0.2 or later): {exc}", file=sys.stderr)
        return EXIT_FINDINGS


def _validate(args: argparse.Namespace, bundle: Any, project: Any, compiler: Any) -> int:
    worst = EXIT_OK
    for raw in args.paths:
        target = Path(raw)
        if not target.exists():
            print(f"pdk: {target} does not exist", file=sys.stderr)
            return EXIT_USAGE
        if args.json:
            results = validate_paths(target, bundle, project=project, profile=args.profile, compiler=compiler)
            payload = [{"path": str(r.path), "project": r.project.name if r.project else None,
                        "viewPath": r.view_path, "diagnostics": r.diagnostics} for r in results]
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            errors = sum(1 for r in results for d in r.diagnostics if d["severity"] == "error")
        else:
            text, errors = report_paths(target, bundle, project=project, profile=args.profile,
                                        focus=args.focus, budget=args.budget, compiler=compiler)
            print(text)
        if errors:
            worst = EXIT_FINDINGS
    return worst


def _cmd_catalog(args: argparse.Namespace) -> int:
    bundle = load_bundle(args.perspective)
    print(catalog(bundle, type=args.type, prop=args.prop, search=args.search, topic=args.topic, budget=args.budget))
    return EXIT_OK


def _cmd_schemas(args: argparse.Namespace) -> int:
    if args.action == "functions":
        from pdk.bundle import save_functions
        from pdk.gateway.client import Gateway, GatewayError

        gateway = Gateway(url=args.url) if args.url else Gateway()
        try:
            listing = gateway.functions()
            ignition = gateway.health().get("ignitionVersion")
        except GatewayError as exc:
            print(f"pdk: {exc}", file=sys.stderr)
            return EXIT_FINDINGS
        bundle = load_bundle(args.perspective)
        path = save_functions(bundle.root, listing, ignition if isinstance(ignition, str) else None)
        print(f"{len(listing.get('expression') or [])} expression functions and {len(listing.get('script') or [])} "
              f"system.* names -> {path}")
        return EXIT_OK
    if args.action == "sync":
        from pdk import live
        from pdk.gateway.client import Gateway, GatewayError

        try:
            ok, text = live.sync(Gateway(url=args.url) if args.url else Gateway(),
                                 Path(args.out) if args.out else None)
        except GatewayError as exc:
            print(f"pdk: {exc}", file=sys.stderr)
            return EXIT_FINDINGS
        print(text)
        return EXIT_OK if ok else EXIT_FINDINGS
    if args.action == "list":
        versions = available_versions()
        if not versions:
            print(f"No bundles under {schemas_root()}. Run `pdk schemas extract`.")
            return EXIT_OK
        for version in versions:
            bundle = load_bundle(version)
            print(f"{version}  {len(bundle.components)} components, {len(bundle.schemas)} schemas, "
                  f"{len(bundle.dom_events)} DOM events  ({bundle.root})")
        return EXIT_OK

    ignition_home = Path(args.ignition_home) if args.ignition_home else find_ignition_home()
    if args.modl:
        modl = Path(args.modl)
    elif ignition_home:
        modl = find_perspective_modl(ignition_home)
    else:
        print("Ignition install not found: pass --ignition-home or --modl, or set IGNITION_HOME.", file=sys.stderr)
        return EXIT_USAGE
    target = extract_bundle(modl, Path(args.out) if args.out else schemas_root(), ignition_home)
    bundle = load_bundle(target.name, target.parent)
    print(f"Extracted Perspective {bundle.version} from {modl}")
    print(f"  {len(bundle.components)} components, {len(bundle.schemas)} schemas, "
          f"{len(bundle.dom_events)} DOM events -> {target}")
    return EXIT_OK


def _cmd_hook(args: argparse.Namespace) -> int:
    from pdk.hook import run_hook

    code, message = run_hook(sys.stdin.read())
    if message:
        print(message, file=sys.stderr)
    return code


def _print_health(health: dict) -> None:
    print(f"{health.get('module')} {health.get('moduleVersion')} on Ignition {health.get('ignitionVersion')} "
          f"({health.get('edition')}), Perspective {health.get('perspectiveVersion')}, Java {health.get('java')}")
    sandbox = health.get("sandbox")
    if not sandbox:
        print("read-only: no -Dpdk.sandbox in ignition.conf")
    else:
        state = "exists" if health.get("sandboxExists") else "MISSING"
        print(f"sandbox {sandbox} ({state}), writes {'enabled' if health.get('writeEnabled') else 'disabled'}")
    if health.get("target"):
        print(f"target {health['target']} ({'exists' if health.get('targetExists') else 'MISSING'}): finished "
              "views and the stylesheet only, through promote --to-project")
    if health.get("targetIgnored"):
        print(f"warning: -Dpdk.target={health['targetIgnored']} is ignored: it needs a sandbox and must differ "
              "from it")
    tags = health.get("tags") or {}
    if tags:
        state = "exists" if tags.get("providerExists") else "MISSING"
        print(f"tags [{tags.get('provider')}]{tags.get('folder')}/ (provider {state})")
    if health.get("runtime") is False:
        print("warning: Perspective's session API is unavailable: eyes fall back to the DOM only")
    if health.get("logs") is False:
        print("warning: the log tap is not attached: no gateway log events for the eyes")
    live = str(health.get("perspectiveVersion") or "").split(" ")[0]  # "3.3.9 (b2026082511)"
    try:
        bundle = load_bundle().version
    except BundleError:
        bundle = None
    if bundle and live and live != bundle:
        print(f"warning: the schema bundle is {bundle} but the gateway runs {live}; run `uv run pdk schemas extract`")


def _cmd_gateway(args: argparse.Namespace) -> int:
    from pdk.gateway import client, module

    home = Path(args.ignition_home) if args.ignition_home else None
    try:
        if args.action in ("build", "install"):
            result = module.build(home)
            print(f"Built {result.modl} (module {result.version}, {result.classes} classes, "
                  f"Java {module.JAVA_RELEASE} bytecode)")
        if args.action == "install":
            print("Warning: PDK is for development gateways only; never install this module on a production gateway.",
                  file=sys.stderr)
            info = module.install(result.modl, args.url)
            upload = info["upload"] if isinstance(info["upload"], dict) else {}
            print(f"Uploaded {upload.get('moduleId', module.MODULE_ID)}; install: "
                  f"{(info['install'] or {}).get('message', info['install'])}")
            if not args.restart:
                print("It loads on the next gateway restart: `uv run pdk gateway restart`.")
                return EXIT_OK
        if args.action == "restart" or (args.action == "install" and args.restart):
            print("Restarting the gateway...", flush=True)
            _print_health(module.restart(args.url))
        elif args.action == "health":
            _print_health(client.Gateway(url=args.url or client.gateway_url()).health())
        elif args.action == "scan":
            from pdk.live import changes_text

            gateway = client.Gateway(url=args.url or client.gateway_url())
            before = gateway.changes(limit=1)["seq"]
            done = gateway.scan().get("scanned")
            print("Scan finished." if done else "Scan requested; it did not finish within 60 s.")
            print(changes_text(gateway.changes(since=before, limit=args.limit)))
        elif args.action == "changes":
            from pdk.live import changes_text

            gateway = client.Gateway(url=args.url or client.gateway_url())
            print(changes_text(gateway.changes(since=args.since, limit=args.limit)))
        elif args.action in ("sessions", "logs"):
            from pdk.live import logs_text, sessions_text

            gateway = client.Gateway(url=args.url or client.gateway_url())
            project = args.project or gateway.health().get("sandbox")
            if args.action == "sessions":
                if not project:
                    print("pdk: pass --project (the module has no sandbox)", file=sys.stderr)
                    return EXIT_FINDINGS
                print(sessions_text(gateway.sessions(project)))
            else:
                print(logs_text(gateway.logs(since=args.since, limit=args.limit, project=args.project,
                                             view=args.view)))
    except (module.ModuleError, client.GatewayError) as exc:
        print(f"pdk: {exc}", file=sys.stderr)
        return EXIT_FINDINGS
    return EXIT_OK


def _cmd_sync(args: argparse.Namespace) -> int:
    from pdk import sync
    from pdk.gateway.client import Gateway, GatewayError

    gateway = Gateway(url=args.url) if args.url else Gateway()
    try:
        if args.command == "pull":
            outcome = sync.pull(gateway, args.path, args.project, force=args.force)
        elif args.command == "deploy":
            outcome = sync.deploy(gateway, Path(args.path), load_bundle(args.perspective), resolved=args.resolved,
                                  budget=args.budget)
        elif args.command == "promote":
            outcome = sync.promote(gateway, Path(args.path), load_bundle(args.perspective), to=args.to,
                                   to_project=args.to_project, resolved=args.resolved, budget=args.budget)
        else:
            outcome = sync.delete(gateway, Path(args.path), resolved=args.resolved, budget=args.budget)
    except GatewayError as exc:
        print(f"pdk: {exc}", file=sys.stderr)
        return EXIT_FINDINGS
    print(outcome.text)
    return EXIT_OK if outcome.ok else EXIT_FINDINGS


TAG_ARITY = {"list": (0, 1), "read": (1, 100), "write": (1, 2), "apply": (1, 1), "delete": (1, 1), "alarms": (0, 1)}


def _cmd_tags(args: argparse.Namespace) -> int:
    from pdk import tags
    from pdk.gateway.client import Gateway, GatewayError

    low, high = TAG_ARITY[args.action]
    items = args.items
    if not low <= len(items) <= high or (args.action == "write" and len(items) == 1 and not args.quality):
        usage = {"list": "[path]", "read": "<path>...", "write": "<path> <value> [--quality Q] | <path> --quality Q",
                 "apply": "<file.json> [--to PDK/folder]", "delete": "<path>", "alarms": "[path]"}[args.action]
        print(f"pdk tags {args.action} {usage}", file=sys.stderr)
        return EXIT_USAGE
    gateway = Gateway(url=args.url) if args.url else Gateway()
    try:
        if args.action == "list":
            print(tags.list_text(gateway.tags(tags.full_path(items[0]) if items else tags.FOLDER, args.depth)))
        elif args.action == "read":
            print(tags.values_text(gateway.read_tags([tags.full_path(item) for item in items])["results"]))
        elif args.action == "write":
            print(tags.values_text([tags.write(gateway, items[0], items[1] if len(items) > 1 else None,
                                               args.quality)]))
        elif args.action == "apply":
            print(tags.apply(gateway, Path(items[0]), args.to or tags.FOLDER))
        elif args.action == "delete":
            print(f"deleted {gateway.delete_tags(tags.full_path(items[0])).get('path')}")
        else:
            print(tags.alarms_text(gateway.alarms(tags.full_path(items[0]) if items else None)))
    except (GatewayError, ValueError) as exc:
        print(f"pdk: {exc}", file=sys.stderr)
        return EXIT_FINDINGS
    return EXIT_OK


def _cmd_eyes(args: argparse.Namespace) -> int:
    from pdk.eyes import inspect
    from pdk.eyes.browser import Eyes
    from pdk.gateway.client import Gateway

    gateway = Gateway(url=args.url) if args.url else Gateway()
    with Eyes(release=inspect.session_closer(gateway)) as eyes:
        if args.command == "open":
            result = inspect.open_view(eyes, gateway, args.path, args.viewport)
        elif args.command == "inspect":
            result = inspect.inspect_view(eyes, gateway, args.path, args.viewport, focus=args.focus,
                                          budget=args.budget, query=args.query,
                                          expect=args.expect, act=args.act)
        else:
            before = None
            if args.compare:  # read before the browser is needed, so a wrong path costs nothing
                try:
                    before = Path(args.compare).read_bytes()
                except OSError as error:
                    print(f"cannot read {args.compare}: {error.strerror or error}", file=sys.stderr)
                    return EXIT_USAGE
            diff = None
            if before is None:
                png, text = inspect.screenshot(eyes, gateway, args.path, args.viewport, args.component, args.act)
            else:
                png, diff, text = inspect.compare_screenshot(eyes, gateway, args.path, before, args.viewport,
                                                             args.component, args.act)
            if png is None:
                print(text, file=sys.stderr)
                return EXIT_FINDINGS
            out = Path(args.out) if args.out else default_screenshot(args.path, args.component)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(png)
            print(f"{text} -> {out}")
            if diff is not None:
                diff_out = out.with_name(f"{out.stem}__diff.png")
                diff_out.write_bytes(diff)
                print(f"diff -> {diff_out}")
            return EXIT_OK
    print(result.text)
    return EXIT_OK if result.ok else EXIT_FINDINGS


def _cmd_doctor(args: argparse.Namespace) -> int:
    from pdk import doctor

    checks = doctor.run(fix=args.fix)
    print(doctor.report(checks))
    return EXIT_FINDINGS if any(check.status == doctor.FAIL for check in checks) else EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pdk", description="Perspective DevKit")
    parser.add_argument("--perspective", help="bundle version to use (default: newest, or PDK_PERSPECTIVE_VERSION)")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor", help="check this machine's setup (Ignition, schemas, Chromium, gateway, "
                                           "module, sandbox) and say how to fix what is missing")
    doctor.add_argument("--fix", action="store_true",
                        help="also apply the local fixes: extract the schema bundle, download Chromium")
    doctor.set_defaults(func=_cmd_doctor)

    validate = sub.add_parser("validate", help="validate view.json files or directories")
    validate.add_argument("paths", nargs="+", help="view.json files or directories (searched recursively)")
    validate.add_argument("--project", help="project dir or local gateway project name, for view/param references")
    validate.add_argument("--profile", choices=["hmi"], help="extra lint rules (hmi-* design tokens)")
    validate.add_argument("--focus", help="expand diagnostics by code, component path or pointer prefix")
    validate.add_argument("--budget", type=int, default=DEFAULT_BUDGET * 4, help="output budget in tokens")
    validate.add_argument("--json", action="store_true", help="machine-readable diagnostics")
    validate.add_argument("--compile", action="store_true",
                          help="also compile every script with the gateway's Jython (E009); needs the module")
    validate.add_argument("--url", help="--compile: gateway URL (default: PDK_GATEWAY_URL or http://127.0.0.1:8088)")
    validate.set_defaults(func=_cmd_validate)

    cat = sub.add_parser("catalog", help="components, props, bindings and events of the installed version")
    cat.add_argument("type", nargs="?", help="component id, short id or name (e.g. ia.display.label, label)")
    cat.add_argument("--prop", help="nested prop path, e.g. columns[] or columns[].render")
    cat.add_argument("--search", help="find components by id, name or description")
    cat.add_argument("--topic", choices=TOPICS, help="reference card")
    cat.add_argument("--budget", type=int, default=DEFAULT_BUDGET * 2, help="output budget in tokens")
    cat.set_defaults(func=_cmd_catalog)

    schemas = sub.add_parser("schemas", help="extract or list schema bundles")
    schemas.add_argument("action", choices=["extract", "list", "sync", "functions"],
                         help="sync: compare with the gateway's live catalog and add its third-party components; "
                              "functions: save the gateway's expression functions and system.* names next to the "
                              "bundle, for B011 and E008")
    schemas.add_argument("--ignition-home", help="Ignition install dir (default: IGNITION_HOME or the standard path)")
    schemas.add_argument("--modl", help="explicit Perspective .modl file")
    schemas.add_argument("--out", help="bundle root (default: ./schemas or PDK_SCHEMAS_DIR)")
    schemas.add_argument("--url", help="sync, functions: gateway URL (default: PDK_GATEWAY_URL or "
                                        "http://127.0.0.1:8088)")
    schemas.set_defaults(func=_cmd_schemas)

    hook = sub.add_parser("hook", help="Claude Code PostToolUse hook (reads the event JSON on stdin)")
    hook.set_defaults(func=_cmd_hook)

    gateway = sub.add_parser("gateway", help="build, install, restart or check the pdk-gateway module")
    gateway.add_argument("action", choices=["build", "install", "restart", "health", "scan", "changes",
                                            "sessions", "logs"])
    gateway.add_argument("--since", type=int, default=0, help="changes/logs: only events after this sequence number")
    gateway.add_argument("--limit", type=int, default=50, help="changes/scan/logs: at most this many events")
    gateway.add_argument("--project", help="sessions: project (default: the sandbox); logs: filter by project")
    gateway.add_argument("--view", help="logs: filter by view resource path, e.g. PDK/Demo")
    gateway.add_argument("--restart", action="store_true", help="install: restart the gateway right after")
    gateway.add_argument("--url", help="gateway URL (default: PDK_GATEWAY_URL or http://127.0.0.1:8088)")
    gateway.add_argument("--ignition-home", help="Ignition install dir (default: IGNITION_HOME or the standard path)")
    gateway.set_defaults(func=_cmd_gateway)

    work = "work/<project>/<view path> (PDK_WORK_DIR)"
    pull = sub.add_parser("pull", help=f"download a view, a folder or `stylesheet` with its signature to {work}")
    pull.add_argument("path", help="view path or folder in the project (e.g. PDK/Menu), `stylesheet`, or a local copy")
    pull.add_argument("--project", help="project to read from (default: the sandbox)")
    pull.add_argument("--force", action="store_true", help="overwrite local edits")
    deploy = sub.add_parser("deploy", help="validate a local view (or the stylesheet) and write it, signed")
    deploy.add_argument("path", help=f"local view folder or view.json, its sandbox view path (PDK/X), or the "
                        f"{work}/<sandbox>/_stylesheet folder")
    promote = sub.add_parser("promote", help="copy a deployed PDK/<X> view to <X> in the sandbox or the target, signed")
    promote.add_argument("path", help="local folder of the deployed PDK/ view")
    promote.add_argument("--to", help="target view path (default: the path without PDK/)")
    promote.add_argument("--to-project", metavar="target",
                         help="copy to the target project (-Dpdk.target; 'target' or its name), not the sandbox")
    delete = sub.add_parser("delete", help="delete a pulled view on the gateway, only if nobody changed it since")
    delete.add_argument("path", help="local view folder or view.json (it needs its .pdk-base.json)")
    for command in (deploy, promote, delete):
        command.add_argument("--resolved", action="store_true",
                             help="after a 409: write over the gateway version you reviewed and merged")
        command.add_argument("--budget", type=int, default=DEFAULT_BUDGET, help="output budget in tokens")
    for command in (pull, deploy, promote, delete):
        command.add_argument("--url", help="gateway URL (default: PDK_GATEWAY_URL or http://127.0.0.1:8088)")
        command.set_defaults(func=_cmd_sync)

    tagger = sub.add_parser("tags", help="simulated tags under [provider]PDK/: list, read, write (with a quality), "
                                         "apply a tag export JSON, delete, alarms")
    tagger.add_argument("action", choices=list(TAG_ARITY))
    tagger.add_argument("items", nargs="*", help="tag paths (without [provider] they hang from PDK/: "
                                                 "Sandbox/Setpoint), a value to write, or the JSON file to apply")
    tagger.add_argument("--quality", help="write: quality to write with the value, e.g. Bad_Stale, Bad_Disabled")
    tagger.add_argument("--depth", type=int, default=1, help="list: folder levels to walk (max 10)")
    tagger.add_argument("--to", help="apply: folder under PDK/ to import into (default: PDK)")
    tagger.add_argument("--url", help="gateway URL (default: PDK_GATEWAY_URL or http://127.0.0.1:8088)")
    tagger.set_defaults(func=_cmd_tags)

    view_arg = f"local view folder or view.json under {work}, or a view path in the sandbox (e.g. PDK/Menu)"
    opener = sub.add_parser("open", help="open a deployed view in headless Chromium and check that it renders")
    inspector = sub.add_parser("inspect", help="report the anomalies of a deployed view: layout, binding qualities, "
                                               "script errors, browser errors")
    inspector.add_argument("--focus", help="expand anomalies by code, component path or pointer prefix")
    inspector.add_argument("--query", help="also print what was measured of these components: name paths (with their "
                                           "subtree), types, pointer prefixes, or * for all; comma-separated")
    inspector.add_argument("--expect", help="expectations, ;-separated: 'root/B below root/A; root/T width 100%%; "
                                            "root/T text \"Pump\"; tag Sandbox/Setpoint = 42; "
                                            "alarm North/Level active'; failures are A001 diagnostics")
    inspector.add_argument("--act", help="steps to perform before measuring, ;-separated: 'click root/Save; "
                                         "type root/Name \"P-2\"; row root/Table 1; select root/Mode \"Auto\"; "
                                         "slide root/Level 7; key [root/Name] Enter; hover root/X; double-click "
                                         "root/X; scroll root/List bottom; wait 500; "
                                         "tag Sandbox/Setpoint 42 [quality Bad_Stale]'; failures are A004")
    inspector.add_argument("--budget", type=int, default=DEFAULT_BUDGET, help="output budget in tokens")
    shooter = sub.add_parser("screenshot", help="PNG of a deployed view, or of one of its components")
    shooter.add_argument("--component", help="name path (root/Panel/Label) or DOM path (C.0:1); popup:<id>/root/... "
                                             "once --act opened the popup")
    shooter.add_argument("--out", default=None, help="PNG file to write (default: <work>/_screenshots/<view>"
                                                     "[__<component>].png, <work> being PDK_WORK_DIR or work/)")
    shooter.add_argument("--act", help="steps performed before capturing, same grammar as inspect --act (to capture a "
                                       "popup or a state after typing); a failing step is A004 and no PNG")
    shooter.add_argument("--compare", metavar="BEFORE.png", help="PNG taken before, of the same view, viewport and "
                                                                 "component: reports the %% of pixels that changed and "
                                                                 "where, and writes the changed pixels in red to "
                                                                 "<out>__diff.png")
    for command in (opener, inspector, shooter):
        command.add_argument("path", help=view_arg)
        command.add_argument("--viewport", default=None, help="WIDTHxHEIGHT or desktop, tablet, phone, wide "
                                                              "(default: 1366x768); inspect takes several, "
                                                              "comma-separated, one report each")
        command.add_argument("--url", help="gateway URL (default: PDK_GATEWAY_URL or http://127.0.0.1:8088)")
        command.set_defaults(func=_cmd_eyes)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    _utf8_streams()
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (BundleError, FileNotFoundError) as exc:
        print(f"pdk: {exc}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())

"""`pdk doctor`: check every prerequisite of a working PDK on this machine and say how to fix what is missing.

Ordered from the local install to the gateway; a check whose prerequisite failed is skipped. `--fix` applies only
local, reversible fixes (extract the schema bundle, download Playwright's Chromium). It never touches the gateway,
ignition.conf or anything that needs admin rights or a restart: those are printed for the user to do.
"""

from __future__ import annotations

import re
import subprocess
import sys
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pdk import config
from pdk.bundle import (
    BundleError,
    _ignition_version,
    _module_version,
    available_versions,
    extract_bundle,
    find_ignition_home,
    find_perspective_modl,
    schemas_root,
)

OK, WARN, FAIL, SKIP = "ok", "warn", "FAIL", "skip"
SUPPORTED_IGNITION = "8.3."


@dataclass
class Check:
    name: str
    status: str
    detail: str
    fix: str | None = None


@dataclass
class State:
    """What earlier checks found, for the later ones."""

    ignition_home: Path | None = None
    perspective: str | None = None
    gateway_up: bool = False
    health: dict | None = None
    sandbox_flag: str | None = None


def _config(_: State, __: bool) -> Check:
    path, applied, problem = config.LOADED
    if problem:
        status = WARN if "unknown keys" in problem else FAIL
        return Check("config", status, problem, f"Fix {path} (keys: {', '.join(config.KEYS)}).")
    if not path.is_file():
        return Check("config", OK, f"no {path}: using environment variables and defaults (optional file)")
    shown = ", ".join(f"{variable} (from {key})" for variable, key in applied.items()) or "nothing (all set already)"
    return Check("config", OK, f"{path} sets {shown}")


def _playwright(_: State, __: bool) -> Check:
    try:
        import playwright  # noqa: F401
    except ImportError:
        return Check("python", FAIL, "playwright is not installed", "uv sync")
    return Check("python", OK, f"Python {sys.version.split()[0]}, dependencies installed")


def _ignition(state: State, _: bool) -> Check:
    home = find_ignition_home()
    if home is None:
        return Check("ignition", FAIL, "no Ignition install found (needs user-lib/modules)",
                     "Install Ignition 8.3 on this machine (PDK talks to it over loopback), or set ignition_home in "
                     f"{config.config_path()} / IGNITION_HOME.")
    state.ignition_home = home
    version = _ignition_version(home)
    if version and not version.startswith(SUPPORTED_IGNITION):
        return Check("ignition", WARN, f"{home}: Ignition {version}; PDK is built and tested on 8.3")
    return Check("ignition", OK, f"{home} (Ignition {version or 'version unknown'})")


def _schemas(state: State, fix: bool) -> Check:
    if state.ignition_home is None:
        return Check("schemas", SKIP, "needs the Ignition install")
    try:
        modl = find_perspective_modl(state.ignition_home)
        with zipfile.ZipFile(modl) as archive:
            version, _ = _module_version(archive.read("module.xml").decode("utf-8"))
    except (BundleError, OSError, KeyError, zipfile.BadZipFile) as exc:
        return Check("schemas", FAIL, f"cannot read the Perspective module: {exc}")
    state.perspective = version
    if version in available_versions():
        return Check("schemas", OK, f"bundle for Perspective {version} in {schemas_root() / version}")
    if fix:
        try:
            extract_bundle(modl, schemas_root(), state.ignition_home)
        except (BundleError, OSError) as exc:
            return Check("schemas", FAIL, f"extraction failed: {exc}", "uv run pdk schemas extract")
        return Check("schemas", OK, f"extracted the bundle for Perspective {version} (--fix)")
    return Check("schemas", FAIL, f"no schema bundle for the installed Perspective {version}",
                 "uv run pdk schemas extract   (or pdk doctor --fix)")


def _chromium(_: State, fix: bool) -> Check:
    def executable() -> Path | None:
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as playwright:
                return Path(playwright.chromium.executable_path)
        except Exception:  # noqa: BLE001
            return None

    found = executable()
    if found and found.is_file():
        return Check("chromium", OK, f"Playwright's Chromium at {found.parent}")
    if fix:
        result = subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], capture_output=True,
                                text=True, timeout=900)
        found = executable()
        if result.returncode == 0 and found and found.is_file():
            return Check("chromium", OK, "downloaded Playwright's Chromium (--fix)")
        return Check("chromium", FAIL, f"download failed: {(result.stderr or result.stdout).strip()[-300:]}",
                     "uv run playwright install chromium")
    return Check("chromium", FAIL, "Playwright's Chromium is not downloaded (the eyes need it)",
                 "uv run playwright install chromium   (or pdk doctor --fix)")


def _jdk(_: State, __: bool) -> Check:
    from pdk.gateway import module

    try:
        javac = module.find_javac()
    except module.ModuleError:
        return Check("jdk", WARN, f"no JDK {module.JAVA_RELEASE}+ (only needed to build the gateway module)",
                     "Install a JDK 17 or newer, or set JAVA_HOME.")
    return Check("jdk", OK, f"{javac}")


_FLAG = re.compile(r"^\s*wrapper\.java\.additional\.(\d+)\s*=\s*(.*?)\s*$")


def _ignition_conf(state: State, _: bool) -> Check:
    if state.ignition_home is None:
        return Check("ignition.conf", SKIP, "needs the Ignition install")
    conf = state.ignition_home / "data" / "ignition.conf"
    try:
        lines = conf.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return Check("ignition.conf", WARN, f"cannot read {conf}: {exc}")
    flags = {match.group(2): int(match.group(1)) for line in lines if (match := _FLAG.match(line))}
    numbers = set(flags.values())
    unsigned = any(flag.replace(" ", "") == "-Dignition.allowunsignedmodules=true" for flag in flags)
    sandbox = next((flag.split("=", 1)[1].strip() for flag in flags if flag.startswith("-Dpdk.sandbox=")), None)
    state.sandbox_flag = sandbox
    missing = []
    if not unsigned:
        missing.append("-Dignition.allowunsignedmodules=true")
    if not sandbox:
        missing.append("-Dpdk.sandbox=<sandbox project>")
    if not missing:
        return Check("ignition.conf", OK, f"unsigned modules allowed, sandbox {sandbox}")
    first = max(numbers, default=0) + 1
    add = "\n".join(f"  wrapper.java.additional.{first + i}={flag}" for i, flag in enumerate(missing))
    return Check("ignition.conf", FAIL, f"{conf} lacks {', '.join(missing)}",
                 f"As admin, add to {conf} and restart the gateway:\n{add}\n"
                 "The sandbox is the only project PDK writes to: a dedicated, non-production project.")


def _gateway(state: State, _: bool) -> Check:
    from pdk.gateway import client, module

    url = client.gateway_url()
    status = module.gateway_state(url)
    if status is None:
        return Check("gateway", FAIL, f"{url} does not answer",
                     f"Start the Ignition service, or set gateway_url in {config.config_path()} / PDK_GATEWAY_URL "
                     "(it must be this machine: the module is loopback-only).")
    state.gateway_up = status == "RUNNING"
    if not state.gateway_up:
        return Check("gateway", FAIL, f"{url} is {status}",
                     "Open the gateway web page and finish what it asks for (commissioning, accepting a module).")
    return Check("gateway", OK, f"{url} RUNNING")


def _api_key(state: State, _: bool) -> Check:
    from pdk.gateway import client, module

    if not state.gateway_up:
        return Check("api key", SKIP, "needs the gateway")
    try:
        key = module.api_key()
        module._api(client.gateway_url(), "GET", "/data/api/v1/restart-tasks/pending", key, timeout=10)
    except module.ModuleError as exc:
        return Check("api key", FAIL, str(exc).split(". Create")[0],
                     "In the gateway web page create an API key (Platform > Security > API Keys) with security level "
                     "Administrator and 'Require secure connections' unchecked; save it to "
                     f"{module.API_KEY_FILE}, or api_key in {config.config_path()}.")
    return Check("api key", OK, "accepted by the gateway (install and restart work)")


def _module(state: State, _: bool) -> Check:
    from pdk.gateway.client import Gateway, GatewayError

    if not state.gateway_up:
        return Check("module", SKIP, "needs the gateway")
    try:
        state.health = Gateway().health()
    except GatewayError as exc:
        return Check("module", FAIL, f"{exc.code}: {exc.message}",
                     "uv run pdk gateway install --restart   (builds and installs pdk-gateway; restarts Ignition)")
    health = state.health
    perspective = str(health.get("perspectiveVersion") or "").split(" ")[0]
    detail = f"{health.get('module')} {health.get('moduleVersion')}, Perspective {perspective}"
    if state.perspective and perspective and perspective != state.perspective:
        return Check("module", WARN, f"{detail}, but the install has Perspective {state.perspective}",
                     "Restart the gateway so it runs the installed version.")
    return Check("module", OK, detail)


def _sandbox(state: State, _: bool) -> Check:
    health = state.health
    if health is None:
        return Check("sandbox", SKIP, "needs the module")
    name = health.get("sandbox")
    if not name:
        return Check("sandbox", FAIL, "the module is read-only: the gateway runs without -Dpdk.sandbox",
                     "Add the flag (see ignition.conf above) and restart the gateway.")
    if state.sandbox_flag and state.sandbox_flag != name:
        return Check("sandbox", WARN, f"the gateway runs with sandbox {name}, ignition.conf says "
                     f"{state.sandbox_flag}", "Restart the gateway to apply ignition.conf.")
    if not health.get("sandboxExists"):
        return Check("sandbox", FAIL, f"project {name} does not exist",
                     f"Create an empty Perspective project named {name} (gateway web page > Projects, or the "
                     "Designer).")
    if health.get("targetIgnored"):
        return Check("sandbox", WARN, f"{name}: writes enabled; -Dpdk.target={health['targetIgnored']} is ignored "
                     "(it must differ from the sandbox)", "Point -Dpdk.target to the final project and restart.")
    target = health.get("target")
    if target and not health.get("targetExists"):
        return Check("sandbox", WARN, f"{name}: writes enabled; target project {target} does not exist",
                     "Fix -Dpdk.target in ignition.conf (or import the project) and restart the gateway.")
    detail = f"{name}: writes enabled under PDK/" if health.get("writeEnabled") else f"{name}: exists, writes disabled"
    return Check("sandbox", OK, detail + (f"; target {target} (finished views via promote)" if target else ""))


def _eyes(state: State, _: bool) -> Check:
    health = state.health
    if health is None:
        return Check("eyes", SKIP, "needs the module")
    missing = [what for what, key in (("Perspective session API", "runtime"), ("log tap", "logs"))
               if health.get(key) is False]
    if missing:
        return Check("eyes", WARN, f"{' and '.join(missing)} unavailable: inspect falls back to the DOM",
                     "See the gateway log (logger pdk.gateway) after a restart.")
    return Check("eyes", OK, "gateway tree and log tap available")


CHECKS: tuple[Callable[[State, bool], Check], ...] = (
    _config, _playwright, _ignition, _schemas, _chromium, _jdk, _ignition_conf, _gateway, _api_key, _module,
    _sandbox, _eyes,
)


def run(fix: bool = False) -> list[Check]:
    state = State()
    return [check(state, fix) for check in CHECKS]


def report(checks: list[Check]) -> str:
    width = max(len(check.name) for check in checks)
    lines = []
    for check in checks:
        lines.append(f"{check.status:>4}  {check.name:<{width}}  {check.detail}")
        if check.fix and check.status in (FAIL, WARN):
            pad = " " * (4 + 2 + width + 2)
            lines.append(pad + "-> " + check.fix.replace("\n", "\n" + pad + "   "))
    failed = sum(check.status == FAIL for check in checks)
    lines.append("ready: every check passed" if not failed else
                 f"{failed} check(s) failed: fix them in order (later checks depend on earlier ones)")
    return "\n".join(lines)

"""Build, install and restart the pdk-gateway module.

The build runs `javac --release 17` from any JDK >= 17 against the jars of the installed gateway, so the module
links against exactly the Ignition and Perspective it runs on, without Gradle. Installing and restarting go
through the gateway's own REST API with an Ignition API key that has the Gateway Write permission.
"""

from __future__ import annotations

import glob
import io
import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from pdk import REPO_ROOT, __version__
from pdk.bundle import BundleError, find_ignition_home, find_perspective_modl
from pdk.gateway.client import OPENER, Gateway, GatewayError, gateway_url, read_token_file

MODULE_ID = "dev.pdk.gateway"
MODULE_FILE = "pdk-gateway.modl"
JAR_FILE = "pdk-gateway.jar"
JAVA_RELEASE = 17
GATEWAY_DIR = REPO_ROOT / "gateway"
BUILD_DIR = GATEWAY_DIR / "build"
API_KEY_ENV = "PDK_IGNITION_API_KEY"
API_KEY_FILE = Path.home() / ".pdk" / "ignition-api-key"
_JDK_GLOBS = (
    "C:/Program Files/Java/jdk*",
    "C:/Program Files/Eclipse Adoptium/jdk*",
    "C:/Program Files/Zulu/zulu*",
    "C:/Program Files/Microsoft/jdk*",
    "/usr/lib/jvm/*",
    "/Library/Java/JavaVirtualMachines/*/Contents/Home",
)
_PERSPECTIVE_JAR = re.compile(r"perspective-(common|gateway)-[0-9.]+\.jar")
_ZIP_TIME = (1980, 1, 1, 0, 0, 0)


class ModuleError(RuntimeError):
    """The module cannot be built, installed or restarted."""


@dataclass(frozen=True)
class BuildResult:
    modl: Path
    version: str
    classes: int


# --- build ------------------------------------------------------------------------------------


def module_version(now: datetime | None = None) -> str:
    """`<pdk version>.<yyDDDHHmm>`: the build number grows with every build and fits Ignition's int."""
    return f"{__version__}.{(now or datetime.now()):%y%j%H%M}"


def render_module_xml(version: str) -> str:
    template = (GATEWAY_DIR / "module.xml").read_text(encoding="utf-8")
    return template.replace("@VERSION@", version).replace("@JAR@", JAR_FILE)


def find_javac() -> Path:
    """A javac from a JDK >= 17: JAVA_HOME, then PATH, then the usual install folders."""
    name = "javac.exe" if os.name == "nt" else "javac"
    candidates = [Path(os.environ["JAVA_HOME"]) / "bin" / name] if os.environ.get("JAVA_HOME") else []
    if found := shutil.which("javac"):
        candidates.append(Path(found))
    for pattern in _JDK_GLOBS:
        candidates.extend(Path(home) / "bin" / name for home in sorted(glob.glob(pattern), reverse=True))
    for javac in candidates:
        if javac.is_file() and _javac_major(javac) >= JAVA_RELEASE:
            return javac
    raise ModuleError(f"No JDK {JAVA_RELEASE}+ found: install one or set JAVA_HOME.")


def _javac_major(javac: Path) -> int:
    try:
        result = subprocess.run([str(javac), "-version"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return 0
    match = re.search(r"javac (\d+)", result.stdout + result.stderr)
    return int(match.group(1)) if match else 0


def compile_classpath(ignition_home: Path, cache: Path) -> list[Path]:
    """The installed platform jars plus Perspective's jars, unpacked from its .modl into `cache`."""
    core = ignition_home / "lib" / "core"
    jars = sorted((core / "common").glob("*.jar")) + sorted((core / "gateway").glob("*.jar"))
    if not jars:
        raise ModuleError(f"No Ignition jars under {core}")
    try:
        modl = find_perspective_modl(ignition_home)
    except BundleError as exc:
        raise ModuleError(str(exc)) from exc
    cache.mkdir(parents=True, exist_ok=True)
    perspective: list[Path] = []
    with zipfile.ZipFile(modl) as archive:
        for info in archive.infolist():
            if _PERSPECTIVE_JAR.fullmatch(info.filename):
                target = cache / info.filename
                if not target.is_file() or target.stat().st_size != info.file_size:
                    target.write_bytes(archive.read(info))
                perspective.append(target)
    if len(perspective) != 2:
        raise ModuleError(f"{modl.name} lacks perspective-common/perspective-gateway jars")
    return jars + sorted(perspective)


def javac_args(sources: Sequence[Path], out_dir: Path, classpath: Sequence[Path]) -> list[str]:
    return [
        "--release", str(JAVA_RELEASE), "-encoding", "UTF-8", "-proc:none", "-Xlint:all,-path", "-Werror",
        "-d", out_dir.as_posix(), "-cp", os.pathsep.join(p.as_posix() for p in classpath),
        *(source.as_posix() for source in sources),
    ]


def argfile_text(args: Sequence[str]) -> str:
    """javac @file syntax: one quoted argument per line (paths already use forward slashes)."""
    return "\n".join('"' + arg.replace("\\", "/").replace('"', '\\"') + '"' for arg in args) + "\n"


def compile_java(sources: Sequence[Path], out_dir: Path, classpath: Sequence[Path]) -> int:
    if not sources:
        raise ModuleError("No Java sources to compile")
    javac = find_javac()
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True)
    argfile = out_dir.parent / f"{out_dir.name}.javac-args"
    argfile.write_text(argfile_text(javac_args(sources, out_dir, classpath)), encoding="utf-8")
    result = subprocess.run([str(javac), f"@{argfile}"], capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        raise ModuleError("javac failed:\n" + (result.stdout + result.stderr).strip()[-4000:])
    return sum(1 for _ in out_dir.rglob("*.class"))


def build(ignition_home: Path | None = None, out_dir: Path = BUILD_DIR, now: datetime | None = None) -> BuildResult:
    """Compile gateway/src/main/java and package an unsigned .modl in `out_dir`."""
    home = ignition_home or find_ignition_home()
    if home is None:
        raise ModuleError("Ignition install not found: set IGNITION_HOME.")
    sources = sorted((GATEWAY_DIR / "src" / "main" / "java").rglob("*.java"))
    classes_dir = out_dir / "classes"
    count = compile_java(sources, classes_dir, compile_classpath(home, out_dir / "sdk"))
    version = module_version(now)
    modl = out_dir / MODULE_FILE
    with zipfile.ZipFile(modl, "w", zipfile.ZIP_DEFLATED) as archive:
        _add(archive, "module.xml", render_module_xml(version).encode("utf-8"))
        _add(archive, JAR_FILE, _jar(classes_dir))
    return BuildResult(modl, version, count)


def _jar(classes_dir: Path) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as jar:
        _add(jar, "META-INF/MANIFEST.MF", b"Manifest-Version: 1.0\r\nCreated-By: pdk\r\n\r\n")
        for path in sorted(p for p in classes_dir.rglob("*") if p.is_file()):
            _add(jar, path.relative_to(classes_dir).as_posix(), path.read_bytes())
    return buffer.getvalue()


def _add(archive: zipfile.ZipFile, name: str, data: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=_ZIP_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    archive.writestr(info, data)


# --- install and restart (gateway REST API) ----------------------------------------------------


def api_key() -> str:
    value = os.environ.get(API_KEY_ENV, "").strip()
    if not value and API_KEY_FILE.is_file():
        value = API_KEY_FILE.read_text(encoding="utf-8").strip()
    if not value:
        raise ModuleError(
            f"No Ignition API key. Create one (Platform > Security > API Keys, security level Administrator, "
            f"'Require secure connections' unchecked) and save it to {API_KEY_FILE} or set {API_KEY_ENV}."
        )
    return value


def _api(base: str, method: str, path: str, key: str, body: bytes | None = None, timeout: float = 60,
         content_type: str = "application/octet-stream") -> Any:
    request = urllib.request.Request(base + path, data=body, method=method)
    request.add_header("X-Ignition-API-Token", key)
    request.add_header("Accept", "application/json")
    if body is not None:
        request.add_header("Content-Type", content_type)
    try:
        with OPENER.open(request, timeout=timeout) as response:
            text = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace").strip()[:300]
        hint = {
            401: " The API key was rejected: check it, and that 'Require secure connections' is unchecked.",
            403: " The API key lacks the Gateway Write permission: give it the Administrator security level.",
        }.get(error.code, "")
        raise ModuleError(f"{method} {path}: HTTP {error.code} {detail}.{hint}") from None
    except (urllib.error.URLError, OSError) as error:
        raise ModuleError(f"{base} is unreachable: {getattr(error, 'reason', error)}") from None
    try:
        return json.loads(text) if text.strip() else None
    except ValueError:
        return text


def install(modl: Path, base: str | None = None) -> dict[str, Any]:
    """Upload and install the module. It loads on the next gateway restart (8.3 has no hot reload)."""
    base, key = base or gateway_url(), api_key()
    upload = _api(base, "POST", f"/data/api/v1/modules/upload?fileName={MODULE_FILE}", key,
                  body=modl.read_bytes(), timeout=120)
    if isinstance(upload, dict) and upload.get("moduleId") not in (None, MODULE_ID):
        raise ModuleError(f"The gateway read module id {upload.get('moduleId')!r} from {modl.name}")
    installed = _api(base, "POST", f"/data/api/v1/modules/install?moduleId={MODULE_ID}", key, timeout=120)
    if isinstance(installed, dict) and installed.get("success") is False:
        raise ModuleError(f"Install failed: {installed.get('message')}")
    accept_certificate(base, key)
    pending = _api(base, "GET", "/data/api/v1/restart-tasks/pending", key)
    return {"upload": upload, "install": installed, "pending": pending}


def accept_certificate(base: str | None = None, key: str | None = None) -> None:
    """Accept the (empty) certificate of the unsigned module after every upload.

    Each upload clears the acceptance; without it the next start stops at commissioning and quarantines the
    module. Needs -Dignition.allowunsignedmodules=true, otherwise the gateway refuses unsigned modules.
    """
    base, key = base or gateway_url(), key or api_key()
    try:
        _api(base, "POST", f"/data/api/v1/modules/certificate?moduleId={MODULE_ID}", key)
    except ModuleError as exc:
        if "already accepted" not in str(exc):
            raise


def gateway_state(base: str, timeout: float = 5) -> str | None:
    """RUNNING, STARTING..., or COMMISSIONING when the gateway waits for someone to accept a new module."""
    try:
        with OPENER.open(base + "/StatusPing", timeout=timeout) as response:
            status = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(status, dict):
        return None
    return "COMMISSIONING" if status.get("details") == "COMMISSIONING" else status.get("state")


def restart(base: str | None = None, timeout: float = 300, poll: float = 2.0) -> dict[str, Any]:
    """Restart the gateway, wait for RUNNING, then for the module's /health with its new token."""
    base, key = base or gateway_url(), api_key()
    old_token = read_token_file()  # the file, not PDK_GATEWAY_TOKEN: the env value never changes on restart
    try:
        _api(base, "POST", "/data/api/v1/restart-tasks/restart?confirm=true", key)
    except ModuleError:
        if gateway_state(base) == "RUNNING":
            raise  # a real refusal, not the connection dropping because the restart already began
    deadline = time.monotonic() + timeout
    _wait(lambda: gateway_state(base) != "RUNNING" or read_token_file() != old_token,
          min(deadline, time.monotonic() + 60), poll)
    if not _wait(lambda: gateway_state(base) in ("RUNNING", "COMMISSIONING"), deadline, poll):
        raise ModuleError(f"The gateway did not reach RUNNING within {timeout:.0f} s")
    if gateway_state(base) == "COMMISSIONING":
        raise ModuleError(f"The gateway stopped at commissioning: open {base} and accept the PDK Gateway module "
                          "(new modules are quarantined until accepted), then run `uv run pdk gateway health`.")
    health: dict[str, Any] = {}

    def module_ready() -> bool:
        token = read_token_file()
        if not token or token == old_token:
            return False
        try:
            health.update(Gateway(url=base, token=token).health())
        except GatewayError:
            return False
        return True

    if not _wait(module_ready, deadline, poll):
        raise ModuleError("The gateway is RUNNING but the module did not answer /health with a new token")
    return health


def _wait(condition: Callable[[], bool], deadline: float, poll: float) -> bool:
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(poll)
    return condition()

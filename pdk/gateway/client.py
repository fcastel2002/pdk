"""Client for the pdk-gateway module: loopback HTTP plus the token the module writes on every startup."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pdk.bundle import find_ignition_home

DEFAULT_URL = "http://127.0.0.1:8088"
BASE_PATH = "/data/pdk/v1"
TOKEN_HEADER = "X-PDK-Token"
TOKEN_FILE = "pdk-gateway.token"
URL_ENV = "PDK_GATEWAY_URL"
TOKEN_ENV = "PDK_GATEWAY_TOKEN"
VIEWS = "com.inductiveautomation.perspective/views"

# Never send loopback calls through a proxy: the module rejects proxied requests anyway.
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# Stable codes for gateway failures. Problems inside a view.json stay in pdk.diagnostics.
CODES: dict[str, str] = {
    "G001": "gateway-unreachable",
    "G002": "module-not-running",
    "G003": "token-rejected",
    "G004": "forbidden",
    "G005": "write-disabled",
    "G006": "outside-sandbox",
    "G007": "not-found",
    "G008": "conflict",
    "G009": "bad-request",
    "G010": "gateway-error",
    "G011": "runtime-unavailable",
    "G012": "session-busy",
    "G013": "outside-tag-folder",
}
HINTS: dict[str, str] = {
    "G001": "Start the Ignition service, or set PDK_GATEWAY_URL.",
    "G002": "Install the module (`uv run pdk gateway install --restart`) and check it is running.",
    "G003": "The token changes on every gateway restart and is read from the token file; check PDK_GATEWAY_TOKEN.",
    "G004": "The module only answers loopback connections without proxy headers.",
    "G005": "Add -Dpdk.sandbox=<project> to ignition.conf and restart the gateway.",
    "G006": ("Writes go to the sandbox shown by `uv run pdk gateway health`; the target takes only views "
             "outside PDK/, through promote --to-project."),
    "G007": "Check the project and the resource path.",
    "G008": "It changed after your pull: pull again, merge, and deploy with the new signature.",
    "G009": "Fix the path, signature or files of the request.",
    "G010": "See the gateway log (logger pdk.gateway).",
    "G011": "Perspective's session API did not link on this version: eyes fall back to the DOM only.",
    "G012": ("The session (a slow script or binding) or the tag provider (a large tag operation, a fast timer "
             "script) is busy; retry, or look for long-running work."),
    "G013": ("Simulated tags are written only under [provider]PDK/ (`uv run pdk gateway health` names the "
             "provider), never config properties like X.ValueSource; other tags are read-only."),
}
_MODULE_CODES = {
    "WRITE_DISABLED": "G005",
    "OUTSIDE_SANDBOX": "G006",
    "OUTSIDE_TARGET": "G006",
    "NOT_FOUND": "G007",
    "PROJECT_NOT_FOUND": "G007",
    "CONFLICT": "G008",
    "FOLDER_NOT_EMPTY": "G008",
    "PAGE_NOT_FOUND": "G007",
    "SESSION_NOT_FOUND": "G007",
    "RUNTIME_UNAVAILABLE": "G011",
    "SESSION_BUSY": "G012",
    "TAGS_BUSY": "G012",
    "OUTSIDE_TAG_FOLDER": "G013",
    "TAG_NOT_FOUND": "G007",
    "TAG_PROVIDER_NOT_FOUND": "G007",
}


class GatewayError(RuntimeError):
    """A failed call to the module, with a stable G-code, a hint and the module's JSON payload."""

    def __init__(self, code: str, message: str, *, status: int | None = None, payload: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.status = status
        self.payload = payload or {}

    @property
    def message(self) -> str:
        return str(self.args[0])

    @property
    def hint(self) -> str:
        return HINTS[self.code]

    def __str__(self) -> str:
        return f"{self.code} {CODES[self.code]}: {self.message} | {self.hint}"


def gateway_url() -> str:
    return os.environ.get(URL_ENV, DEFAULT_URL).rstrip("/")


def token_path(ignition_home: Path | None = None) -> Path | None:
    home = ignition_home or find_ignition_home()
    return home / "data" / TOKEN_FILE if home else None


def read_token(ignition_home: Path | None = None) -> str | None:
    """The current token: PDK_GATEWAY_TOKEN, else the file the module rewrites on every startup."""
    if os.environ.get(TOKEN_ENV):
        return os.environ[TOKEN_ENV].strip()
    path = token_path(ignition_home)
    if path is None:
        return None
    try:
        return path.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def error_for(status: int, payload: Any, url: str) -> GatewayError:
    body = payload if isinstance(payload, dict) else {}
    module_code = body.get("error")
    message = str(body.get("message") or f"HTTP {status} from {url}")
    if module_code in _MODULE_CODES:
        code = _MODULE_CODES[module_code]
    elif module_code:
        code = "G009" if 400 <= status < 500 else "G010"
    elif status == 401:
        code = "G003"
    elif status == 403:
        code = "G004"
    elif status == 404:
        code = "G002"  # the route itself is missing: the module is not installed or not running
    else:
        code = "G010" if status >= 500 else "G009"
    return GatewayError(code, message, status=status, payload=body)


def unwrap_arrays(node: Any) -> Any:
    """Module builds before 2026-10-05 leave qualified arrays as {"$a": [...]} and their quality paths as `x.$a[i]`."""
    if isinstance(node, list):
        return [unwrap_arrays(item) for item in node]
    if isinstance(node, dict):
        if set(node) == {"$a"}:
            return unwrap_arrays(node["$a"])
        return {key: (value.replace(".$a[", "[") if key == "path" and isinstance(value, str) else unwrap_arrays(value))
                for key, value in node.items()}
    return node


def resource_route(project: str, type_key: str, path: str = "") -> str:
    segments = [project, "resources", *type_key.split("/"), *(path.strip("/").split("/") if path.strip("/") else [])]
    return "/projects/" + "/".join(urllib.parse.quote(segment, safe="") for segment in segments)


@dataclass
class Gateway:
    url: str = field(default_factory=gateway_url)
    token: str | None = None  # None: read the token file on every call (it rotates on restart)
    timeout: float = 30.0

    def health(self) -> dict[str, Any]:
        return self.call("GET", "/health")

    def resource(self, project: str, type_key: str, path: str = "") -> dict[str, Any]:
        """A resource with its files and signature, or the listing of a folder."""
        return self.call("GET", resource_route(project, type_key, path))

    def put_resource(self, project: str, type_key: str, path: str, files: dict[str, str],
                     signature: str | None = None, scope: str | None = None) -> dict[str, Any]:
        """Create (signature None) or modify (the signature you read). A stale signature raises G008."""
        body: dict[str, Any] = {"files": files, "signature": signature}
        if scope:
            body["scope"] = scope
        return self.call("PUT", resource_route(project, type_key, path), body)

    def delete_resource(self, project: str, type_key: str, path: str, signature: str) -> dict[str, Any]:
        route = resource_route(project, type_key, path) + "?" + urllib.parse.urlencode({"signature": signature})
        return self.call("DELETE", route)

    def scan(self) -> dict[str, Any]:
        """Make the gateway re-read project files changed on disk."""
        return self.call("POST", "/scan")

    def changes(self, since: int = 0, limit: int = 200) -> dict[str, Any]:
        """Sandbox resource changes after sequence `since`, each with origin pdk or external."""
        return self.call("GET", "/changes?" + urllib.parse.urlencode({"since": since, "limit": limit}))

    def catalog(self, full: bool = False) -> dict[str, Any]:
        """Components registered in the gateway; `full` adds schemas, events and palette."""
        return self.call("GET", "/catalog" + ("?full=true" if full else ""))

    def sessions(self, project: str) -> dict[str, Any]:
        """Client sessions of a project, with their pages and the view instances on each."""
        return self.call("GET", "/sessions?" + urllib.parse.urlencode({"project": project}))

    def page(self, project: str, page_id: str) -> dict[str, Any]:
        """The evaluated tree of every view on a page: plain values, non-Good qualities and configured bindings."""
        route = f"/pages/{urllib.parse.quote(page_id, safe='')}?" + urllib.parse.urlencode({"project": project})
        return unwrap_arrays(self.call("GET", route))

    def logs(self, since: int = 0, limit: int = 200, project: str | None = None,
             view: str | None = None) -> dict[str, Any]:
        """WARN/ERROR gateway log events after `since`, filtered by project and view (resource path) via the MDC."""
        query: dict[str, Any] = {"since": since, "limit": limit}
        if project:
            query["project"] = project
        if view:
            query["view"] = view
        return self.call("GET", "/logs?" + urllib.parse.urlencode(query))

    def tags(self, path: str | None = None, depth: int = 1) -> dict[str, Any]:
        """Nodes under a tag folder (any provider), flat, with data type, value and quality."""
        query: dict[str, Any] = {"depth": depth}
        if path:
            query["path"] = path
        return self.call("GET", "/tags?" + urllib.parse.urlencode(query))

    def read_tags(self, paths: list[str]) -> dict[str, Any]:
        """Value, quality and timestamp of each tag path (any provider)."""
        return self.call("POST", "/tags/read", {"paths": paths})

    def write_tags(self, writes: list[dict[str, Any]]) -> dict[str, Any]:
        """[{"path", "value", "quality"?}] under PDK/: the write result and the read-back of each."""
        return self.call("POST", "/tags/write", {"writes": writes})

    def import_tags(self, path: str, tags: dict[str, Any]) -> dict[str, Any]:
        """Tag export JSON (folders and memory tags) into a folder under PDK/, overwriting what is there."""
        return self.call("PUT", "/tags?" + urllib.parse.urlencode({"path": path}), tags)

    def delete_tags(self, path: str) -> dict[str, Any]:
        return self.call("DELETE", "/tags?" + urllib.parse.urlencode({"path": path}))

    def alarms(self, path: str | None = None) -> dict[str, Any]:
        """Alarm events of the tags at or under a path (default PDK/)."""
        return self.call("GET", "/alarms" + ("?" + urllib.parse.urlencode({"path": path}) if path else ""))

    def acknowledge_alarms(self, path: str) -> dict[str, Any]:
        """Acknowledge the unacknowledged alarm events of the tags under a path in PDK/."""
        return self.call("POST", "/alarms/ack?" + urllib.parse.urlencode({"path": path}))

    def call(self, method: str, route: str, body: Any = None) -> Any:
        token = self.token or read_token()
        if not token:
            raise GatewayError("G002", f"No module token ({token_path() or TOKEN_FILE}): the module has not started.")
        url = self.url + BASE_PATH + route
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header(TOKEN_HEADER, token)
        request.add_header("Accept", "application/json")
        if data is not None:
            request.add_header("Content-Type", "application/json; charset=utf-8")
        try:
            with OPENER.open(request, timeout=self.timeout) as response:
                return _decode(response.read())
        except urllib.error.HTTPError as error:
            raise error_for(error.code, _decode(error.read()), url) from None
        except (urllib.error.URLError, OSError) as error:
            reason = getattr(error, "reason", error)
            raise GatewayError("G001", f"{self.url} is unreachable: {reason}") from None


def _decode(raw: bytes) -> Any:
    text = raw.decode("utf-8", "replace")
    try:
        return json.loads(text) if text.strip() else None
    except ValueError:
        return text

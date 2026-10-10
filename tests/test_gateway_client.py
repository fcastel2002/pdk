"""The module client against a fake module: token, URL quoting, the 409 contract and the G-codes."""

from __future__ import annotations

import hashlib
import json
import socket
import threading
import urllib.parse
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from pdk.gateway import client
from pdk.gateway.client import CODES, HINTS, VIEWS, Gateway, GatewayError

TOKEN = "t0k3n"


class FakeModule(BaseHTTPRequestHandler):
    """Just enough of /data/pdk/v1 to exercise the client; resources keyed by their decoded route."""

    installed = True
    writable = True
    store: dict[str, dict[str, str]] = {}
    seen: list[str] = []

    def log_message(self, *args: Any) -> None:
        pass

    def do_GET(self) -> None:
        self._handle()

    def do_PUT(self) -> None:
        self._handle()

    def do_DELETE(self) -> None:
        self._handle()

    def _send(self, status: int, body: Any = None) -> None:
        data = json.dumps(body).encode("utf-8") if body is not None else b"<html>Not Found</html>"
        self.send_response(status)
        self.send_header("Content-Type", "application/json" if body is not None else "text/html")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _handle(self) -> None:
        url = urllib.parse.urlsplit(self.path)
        self.seen.append(url.path)
        if not self.installed or not url.path.startswith(client.BASE_PATH + "/"):
            return self._send(404)
        if self.headers.get(client.TOKEN_HEADER) != TOKEN:
            return self._send(401)
        route = urllib.parse.unquote(url.path[len(client.BASE_PATH):])
        if route == "/health":
            return self._send(200, {"api": 1, "sandbox": "sandbox", "writeEnabled": self.writable})
        current = self.store.get(route)
        if self.command == "GET":
            if current is None:
                return self._send(404, {"error": "NOT_FOUND", "message": f"{route} does not exist"})
            return self._send(200, _describe(route, current))
        # Read the body before any answer: replying first races the client's upload (connection reset on Windows).
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if not self.writable:
            return self._send(403, {"error": "WRITE_DISABLED", "message": "read-only"})
        if self.command == "DELETE":
            expected = urllib.parse.parse_qs(url.query).get("signature", [""])[0]
            if current is None:
                return self._send(404, {"error": "NOT_FOUND", "message": "gone"})
            if _signature(current) != expected:
                conflict = {"error": "CONFLICT", "message": "changed", "current": _describe(route, current)}
                return self._send(409, conflict)
            del self.store[route]
            return self._send(200, {"op": "deleted"})
        body = json.loads(raw.decode("utf-8"))
        expected = body.get("signature")
        if (expected is None and current is not None) or (
                expected is not None and (current is None or _signature(current) != expected)):
            described = _describe(route, current) if current is not None else None
            return self._send(409, {"error": "CONFLICT", "message": "not written", "current": described})
        self.store[route] = body["files"]
        return self._send(201 if expected is None else 200, {"op": "created" if expected is None else "modified",
                                                            "signature": _signature(body["files"])})


def _signature(files: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode("utf-8")).hexdigest()


def _describe(route: str, files: dict[str, str]) -> dict[str, Any]:
    return {"path": route, "signature": _signature(files), "files": files}


@pytest.fixture
def fake() -> Iterator[Gateway]:
    FakeModule.installed, FakeModule.writable = True, True
    FakeModule.store, FakeModule.seen = {}, []
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeModule)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield Gateway(url=f"http://127.0.0.1:{server.server_port}", token=TOKEN, timeout=5)
    server.shutdown()
    server.server_close()


def test_health_sends_the_token(fake: Gateway) -> None:
    assert fake.health()["sandbox"] == "sandbox"


def test_round_trip_with_expected_signatures(fake: Gateway) -> None:
    path = "PDK/Menu Horizontal"
    created = fake.put_resource("sandbox", VIEWS, path, {"view.json": "{}"})
    assert created["op"] == "created"
    assert any("Menu%20Horizontal" in seen for seen in FakeModule.seen)  # each segment is URL-quoted
    modified = fake.put_resource("sandbox", VIEWS, path, {"view.json": '{"a": 1}'}, signature=created["signature"])
    assert modified["op"] == "modified"
    with pytest.raises(GatewayError) as stale:
        fake.put_resource("sandbox", VIEWS, path, {"view.json": "{}"}, signature=created["signature"])
    assert stale.value.code == "G008"
    assert stale.value.payload["current"]["files"] == {"view.json": '{"a": 1}'}
    with pytest.raises(GatewayError) as exists:
        fake.put_resource("sandbox", VIEWS, path, {"view.json": "{}"})
    assert exists.value.code == "G008"
    assert fake.resource("sandbox", VIEWS, path)["signature"] == modified["signature"]
    assert fake.delete_resource("sandbox", VIEWS, path, modified["signature"])["op"] == "deleted"
    with pytest.raises(GatewayError) as gone:
        fake.resource("sandbox", VIEWS, path)
    assert gone.value.code == "G007"


def test_read_only_module_maps_to_write_disabled(fake: Gateway) -> None:
    FakeModule.writable = False
    with pytest.raises(GatewayError) as error:
        fake.put_resource("sandbox", VIEWS, "PDK/X", {"view.json": "{}"})
    assert error.value.code == "G005" and "ignition.conf" in error.value.hint


def test_wrong_token_and_missing_module(fake: Gateway) -> None:
    with pytest.raises(GatewayError) as rejected:
        Gateway(url=fake.url, token="wrong", timeout=5).health()
    assert rejected.value.code == "G003"
    FakeModule.installed = False
    with pytest.raises(GatewayError) as missing:
        fake.health()
    assert missing.value.code == "G002"


def test_unreachable_gateway() -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    with pytest.raises(GatewayError) as error:
        Gateway(url=f"http://127.0.0.1:{port}", token=TOKEN, timeout=2).health()
    assert error.value.code == "G001"


def test_error_codes_are_complete_and_rendered() -> None:
    assert set(CODES) == set(HINTS)
    text = str(client.error_for(409, {"error": "CONFLICT", "message": "changed"}, "u"))
    assert text.startswith("G008 conflict: changed | ")
    assert client.error_for(500, "<html>", "u").code == "G010"
    assert client.error_for(400, {"error": "BAD_SIGNATURE", "message": "x"}, "u").code == "G009"


def test_token_comes_from_env_or_the_gateway_data_dir(tmp_path, monkeypatch) -> None:
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / client.TOKEN_FILE).write_text(" from-file \n", encoding="utf-8")
    monkeypatch.delenv(client.TOKEN_ENV, raising=False)
    monkeypatch.setattr(client, "find_ignition_home", lambda: tmp_path)
    assert client.read_token() == "from-file"
    monkeypatch.setenv(client.TOKEN_ENV, "from-env")
    assert client.read_token() == "from-env"
    assert client.resource_route("p", VIEWS, "/A b/C/") == (
        "/projects/p/resources/com.inductiveautomation.perspective/views/A%20b/C")

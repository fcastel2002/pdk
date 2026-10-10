"""Against the real module on the local gateway: `uv run pytest -m gateway`.

Writes only under PDK/_selftest/ in the sandbox and deletes what it creates.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import pytest

from pdk import sync
from pdk.bundle import find_ignition_home
from pdk.gateway.client import BASE_PATH, OPENER, TOKEN_HEADER, VIEWS, Gateway, GatewayError, read_token
from pdk.project import PERSPECTIVE_DIR

pytestmark = pytest.mark.gateway

VIEW = {
    "custom": {},
    "params": {},
    "props": {},
    "root": {"type": "ia.container.flex", "meta": {"name": "root"}, "props": {"direction": "column"}, "children": []},
}


@pytest.fixture(scope="module")
def gateway() -> Gateway:
    client = Gateway()
    try:
        client.health()
    except GatewayError as exc:
        pytest.skip(str(exc))
    return client


@pytest.fixture(scope="module")
def sandbox(gateway: Gateway) -> str:
    health = gateway.health()
    if not health["writeEnabled"]:
        pytest.skip("read-only module: no -Dpdk.sandbox")
    return health["sandbox"]


def _status(url: str, token: str | None) -> int:
    request = urllib.request.Request(url)
    if token:
        request.add_header(TOKEN_HEADER, token)
    try:
        with OPENER.open(request, timeout=10) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def _lan_address() -> str | None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.connect(("192.0.2.1", 9))  # TEST-NET address: nothing is sent, it only picks the outbound interface
            address = probe.getsockname()[0]
        except OSError:
            return None
    return None if address.startswith("127.") else address


def test_health_reports_the_installed_versions(gateway: Gateway) -> None:
    health = gateway.health()
    assert health["api"] == 1 and health["module"] == "dev.pdk.gateway"
    assert str(health["perspectiveVersion"]).startswith("3.")


def test_token_is_required(gateway: Gateway) -> None:
    url = gateway.url + BASE_PATH + "/health"
    assert _status(url, None) == 401
    assert _status(url, "not-the-token") == 401
    assert _status(url, read_token()) == 200


def test_non_loopback_connections_are_refused(gateway: Gateway) -> None:
    address = _lan_address()
    if address is None:
        pytest.skip("no LAN address")
    port = gateway.url.rsplit(":", 1)[-1]
    try:
        assert _status(f"http://{address}:{port}{BASE_PATH}/health", read_token()) == 403
    except urllib.error.URLError as exc:
        pytest.skip(f"the gateway does not listen on {address}: {exc.reason}")


def test_writes_outside_the_sandbox_are_refused(gateway: Gateway) -> None:
    health = gateway.health()
    with pytest.raises(GatewayError) as error:
        gateway.put_resource("pdk_not_the_sandbox", VIEWS, "PDK/_selftest/X", {"view.json": "{}"})
    assert error.value.code == ("G006" if health["writeEnabled"] else "G005")


def test_create_modify_conflict_delete(gateway: Gateway, sandbox: str) -> None:
    path = f"PDK/_selftest/Spike {uuid.uuid4().hex[:6]}"
    first = json.dumps(VIEW, indent=2)
    created = gateway.put_resource(sandbox, VIEWS, path, {"view.json": first})
    try:
        assert created["op"] == "created" and created["lastModification"]["actor"] == "pdk"
        read = gateway.resource(sandbox, VIEWS, path)
        assert read["files"]["view.json"] == first and read["signature"] == created["signature"]

        second = json.dumps({**VIEW, "custom": {"pdk": 1}}, indent=2)
        modified = gateway.put_resource(sandbox, VIEWS, path, {"view.json": second}, signature=created["signature"])
        assert modified["op"] == "modified" and modified["signature"] != created["signature"]

        with pytest.raises(GatewayError) as stale:
            gateway.put_resource(sandbox, VIEWS, path, {"view.json": first}, signature=created["signature"])
        assert stale.value.code == "G008"
        assert stale.value.payload["current"]["signature"] == modified["signature"]
        assert stale.value.payload["current"]["files"]["view.json"] == second

        with pytest.raises(GatewayError) as exists:
            gateway.put_resource(sandbox, VIEWS, path, {"view.json": first})
        assert exists.value.code == "G008"

        with pytest.raises(GatewayError) as stale_delete:
            gateway.delete_resource(sandbox, VIEWS, path, created["signature"])
        assert stale_delete.value.code == "G008"
        assert gateway.resource(sandbox, VIEWS, path)["files"]["view.json"] == second  # nothing was overwritten
    finally:
        current = gateway.resource(sandbox, VIEWS, path)
        gateway.delete_resource(sandbox, VIEWS, path, current["signature"])
    with pytest.raises(GatewayError) as gone:
        gateway.resource(sandbox, VIEWS, path)
    assert gone.value.code == "G007"


def _on_disk(sandbox: str, path: str) -> Path:
    home = find_ignition_home()
    if home is None:
        pytest.skip("Ignition install not found")
    return home / "data" / "projects" / sandbox / PERSPECTIVE_DIR / "views" / Path(*path.split("/"))


def test_delete_prunes_the_folders_it_leaves_empty(gateway: Gateway, sandbox: str) -> None:
    run = f"PDK/_selftest/Run {uuid.uuid4().hex[:6]}"
    path = f"{run}/Nested/View"
    created = gateway.put_resource(sandbox, VIEWS, path, {"view.json": json.dumps(VIEW)})
    assert _on_disk(sandbox, path).is_dir()
    deleted = gateway.delete_resource(sandbox, VIEWS, path, created["signature"])
    assert deleted["pruned"][:2] == [f"{run}/Nested", run]
    for folder in (f"{run}/Nested", run):
        assert not _on_disk(sandbox, folder).exists()
        with pytest.raises(GatewayError) as gone:
            gateway.resource(sandbox, VIEWS, folder)
        assert gone.value.code == "G007"


def test_pull_deploy_conflict_delete(gateway: Gateway, sandbox: str, bundle, tmp_path: Path) -> None:
    path = f"PDK/_selftest/Sync {uuid.uuid4().hex[:6]}"
    directory = sync.local_dir(sandbox, path, tmp_path)
    directory.mkdir(parents=True)
    (directory / sync.VIEW_FILE).write_text(json.dumps(VIEW, indent=2), encoding="utf-8")
    try:
        created = sync.deploy(gateway, directory, bundle, root=tmp_path)
        assert created.ok and created.text.startswith(f"created {sandbox}:{path}"), created.text

        pulled = sync.pull(gateway, path, sandbox, root=tmp_path)
        assert pulled.ok and sync.read_sidecar(directory)["lastModification"]["actor"] == "pdk"

        outside = gateway.resource(sandbox, VIEWS, path)  # another writer changes it after the pull
        gateway.put_resource(sandbox, VIEWS, path, {"view.json": json.dumps({**VIEW, "custom": {"other": 1}})},
                             signature=outside["signature"])
        (directory / sync.VIEW_FILE).write_text(json.dumps({**VIEW, "custom": {"mine": 1}}), encoding="utf-8")
        conflict = sync.deploy(gateway, directory, bundle, root=tmp_path)
        assert not conflict.ok and "+ /custom/other" in conflict.text and "+ /custom/mine" in conflict.text
        assert json.loads(gateway.resource(sandbox, VIEWS, path)["files"]["view.json"])["custom"] == {"other": 1}

        resolved = sync.deploy(gateway, directory, bundle, resolved=True, root=tmp_path)
        assert resolved.ok, resolved.text
        deleted = sync.delete(gateway, directory, root=tmp_path)
        assert deleted.ok, deleted.text
    finally:
        try:
            current = gateway.resource(sandbox, VIEWS, path)
            gateway.delete_resource(sandbox, VIEWS, path, current["signature"])
        except GatewayError:
            pass
    with pytest.raises(GatewayError):
        gateway.resource(sandbox, VIEWS, path)


def test_catalog_matches_the_offline_bundle(gateway: Gateway) -> None:
    from pdk import live
    from pdk.bundle import available_versions, schemas_root

    catalog = gateway.catalog(full=True)
    assert catalog["schemaAvailable"] and catalog["count"] >= 70
    version = str(gateway.health()["perspectiveVersion"]).split(" ")[0]
    if version not in available_versions():
        pytest.skip(f"no offline bundle for Perspective {version}")
    drift = live.compare(catalog["components"], live.offline_descriptors(schemas_root() / version), version)
    assert drift.built_in == 70 and not drift.differing, live.report(drift)
    assert gateway.catalog()["components"][0].keys() >= {"id", "moduleId", "container"}


def test_changes_tell_pdk_writes_apart(gateway: Gateway, sandbox: str) -> None:
    path = f"PDK/_selftest/Changes {uuid.uuid4().hex[:6]}"
    before = gateway.changes(limit=1)["seq"]
    created = gateway.put_resource(sandbox, VIEWS, path, {"view.json": json.dumps(VIEW)})
    gateway.delete_resource(sandbox, VIEWS, path, created["signature"])
    events = [e for e in gateway.changes(since=before)["events"] if e["path"] == path]
    assert [(e["op"], e["origin"]) for e in events] == [("created", "pdk"), ("deleted", "pdk")], events
    assert events[0]["actor"] == "pdk" and events[0]["signature"] == created["signature"]
    assert gateway.scan()["scanned"] is True


def test_deploy_maps_a_preview_page(gateway: Gateway, sandbox: str, bundle, tmp_path: Path) -> None:
    path = f"PDK/_selftest/Preview {uuid.uuid4().hex[:6]}"
    directory = sync.local_dir(sandbox, path, tmp_path)
    directory.mkdir(parents=True)
    (directory / sync.VIEW_FILE).write_text(json.dumps(VIEW), encoding="utf-8")

    def pages() -> dict:
        return json.loads(gateway.resource(sandbox, sync.PAGE_CONFIG)["files"][sync.PAGE_FILE]).get("pages", {})

    deployed = sync.deploy(gateway, directory, bundle, root=tmp_path)
    try:
        url = sync.preview_url(gateway.url, sandbox, path)
        assert deployed.ok and f"preview: {url}" in deployed.text, deployed.text
        assert pages()[sync.page_url(path)] == {"viewPath": path}
        with OPENER.open(url, timeout=10) as response:
            assert response.status == 200
    finally:
        assert sync.delete(gateway, directory, root=tmp_path).ok
    assert sync.page_url(path) not in pages()


RUNTIME_VIEW = {
    "custom": {},
    "params": {},
    "props": {},
    "root": {
        "type": "ia.container.flex", "meta": {"name": "root"}, "props": {"direction": "column"},
        "children": [
            {"type": "ia.display.label", "meta": {"name": "Missing"}, "position": {"basis": "32px"},
             "propConfig": {"props.text": {"binding": {"type": "tag", "config": {
                 "mode": "direct", "tagPath": "[default]PDK_Selftest/NoSuchTag", "fallbackDelay": 2.5}}}}},
            {"type": "ia.display.label", "meta": {"name": "Broken"}, "position": {"basis": "32px"},
             "propConfig": {"props.text": {"binding": {
                 "type": "expr", "config": {"expression": "'x'"},
                 "transforms": [{"type": "script", "code": "\traise ValueError('pdk')"}]}}},
             "events": {"system": {"onStartup": {
                 "type": "script", "scope": "G",
                 "config": {"script": "\traise RuntimeError('pdk selftest startup')"}}}}},
            {"type": "ia.input.dropdown", "meta": {"name": "Choice"}, "position": {"basis": "32px"},
             "props": {"options": [{"label": "A", "value": 1}, {"label": "B", "value": 2}]}},
        ],
    },
}


def _open_page(url: str):
    """A headless Chromium page on the preview URL and its Perspective page id; the caller closes the browser."""
    sync_api = pytest.importorskip("playwright.sync_api")
    playwright = sync_api.sync_playwright().start()
    browser = playwright.chromium.launch()
    page = browser.new_page(viewport={"width": 1366, "height": 768})
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_selector('[data-component-path="C.0"]', state="visible", timeout=20000)
    page_id = page.evaluate("() => sessionStorage.getItem('perspective-tab-id')")
    return playwright, browser, page, page_id


def _components(node: dict) -> dict[str, dict]:
    found = {node["path"]: node}
    for child in node.get("children", []):
        found.update(_components(child))
    return found


def test_runtime_tree_and_logs_of_an_open_page(gateway: Gateway, sandbox: str, bundle, tmp_path: Path) -> None:
    import time

    if not gateway.health().get("runtime"):
        pytest.skip("Perspective's session API is unavailable on this gateway")
    path = f"PDK/_selftest/Runtime {uuid.uuid4().hex[:6]}"
    directory = sync.local_dir(sandbox, path, tmp_path)
    directory.mkdir(parents=True)
    (directory / sync.VIEW_FILE).write_text(json.dumps(RUNTIME_VIEW), encoding="utf-8")
    since = gateway.logs(limit=1)["last"]
    assert sync.deploy(gateway, directory, bundle, root=tmp_path).ok
    playwright = browser = None
    try:
        playwright, browser, _, page_id = _open_page(sync.preview_url(gateway.url, sandbox, path))
        assert page_id, "the client keeps its page id in sessionStorage 'perspective-tab-id'"
        listing = gateway.sessions(sandbox)
        assert any(p["id"] == page_id for s in listing["sessions"] for p in s["pages"])

        deadline = time.monotonic() + 10
        while True:
            tree = gateway.page(sandbox, page_id)
            view = next(v for v in tree["views"] if v["resourcePath"] == path)
            parts = _components(view["root"])
            qualities = {q["quality"] for c in parts.values() for q in c.get("qualities", [])}
            if {"Bad_NotFound", "Error_ScriptEval"} <= qualities or time.monotonic() > deadline:
                break
            time.sleep(0.3)
        assert view["mountPath"] == "C" and view["root"]["address"] == "0"
        missing, broken, choice = parts["root/Missing"], parts["root/Broken"], parts["root/Choice"]
        assert missing["address"] == "0:0" and missing["type"] == "ia.display.label"
        bad, error = missing["qualities"][0], broken["qualities"][0]
        assert bad["path"] == "props.text" and bad["quality"] == "Bad_NotFound" and "NoSuchTag" in bad["message"]
        assert error["quality"] == "Error_ScriptEval" and "ValueError" in error["message"]
        assert broken["bindings"]["props.text"] == {"type": "expr", "enabled": True, "transforms": 1}
        assert choice["props"]["options"] == [{"label": "A", "value": 1}, {"label": "B", "value": 2}]
        assert "qualities" not in choice and choice["position"]["basis"] == "32px"

        deadline = time.monotonic() + 5
        while True:
            events = gateway.logs(since=since, project=sandbox, view=path)["events"]
            if events or time.monotonic() > deadline:
                break
            time.sleep(0.3)
        assert events and events[0]["mdc"]["component"] == "root/Broken", events
        assert "pdk selftest startup" in events[0]["message"]
        assert not gateway.logs(since=since, project=sandbox, view=path + "x")["events"]

        with pytest.raises(GatewayError) as missing_page:
            gateway.page(sandbox, "nosuchpage")
        assert missing_page.value.code == "G007"
    finally:
        if browser is not None:
            browser.close()
        if playwright is not None:
            playwright.stop()
        assert sync.delete(gateway, directory, root=tmp_path).ok


def test_eyes_open_extract_and_screenshot(gateway: Gateway, sandbox: str, bundle, tmp_path: Path) -> None:
    from pdk.eyes.browser import Eyes

    pytest.importorskip("playwright.sync_api")
    path = f"PDK/_selftest/Eyes {uuid.uuid4().hex[:6]}"
    directory = sync.local_dir(sandbox, path, tmp_path)
    directory.mkdir(parents=True)
    (directory / sync.VIEW_FILE).write_text(json.dumps(RUNTIME_VIEW), encoding="utf-8")
    assert sync.deploy(gateway, directory, bundle, root=tmp_path).ok
    try:
        with Eyes() as eyes:
            url = sync.preview_url(gateway.url, sandbox, path)
            opened = eyes.open(url)
            assert opened.page_id and opened.readiness_ms < 10_000
            extracted = eyes.extract(opened)
            nodes = {node["path"]: node for node in extracted["nodes"]}
            assert set(nodes) == {"C.0", "C.0:0", "C.0:1", "C.0:2"}
            assert nodes["C.0:0"]["type"] == "ia.display.label" and nodes["C.0:0"]["parent"] == "C.0"
            assert nodes["C.0:0"]["overlay"] == "error" and nodes["C.0:0"]["text"] == "null"
            assert extracted["viewport"] == {"width": 1366, "height": 768}

            again = eyes.open(url)  # a new tab of the same context: a new page of the same session, no Maker notice
            assert again.page_id != opened.page_id and not again.maker_notice
            sessions = gateway.sessions(sandbox)["sessions"]
            assert any({opened.page_id, again.page_id} <= {p["id"] for p in s["pages"]} for s in sessions)
            assert eyes.screenshot(again).startswith(b"\x89PNG")
            assert eyes.screenshot(again, "C.0:2").startswith(b"\x89PNG")
    finally:
        assert sync.delete(gateway, directory, root=tmp_path).ok


DEFECTS = Path(__file__).parent / "fixtures" / "eyes" / "defects.view.json"


def test_inspect_finds_every_seeded_defect_on_its_component(gateway: Gateway, sandbox: str, bundle,
                                                            tmp_path: Path) -> None:
    """F3 exit criterion: missing tag, overflow, collapsed container and script errors, each on the right component,
    within the token budget; and again on a second inspection in the same browser."""
    from pdk.diagnostics import estimate_tokens
    from pdk.eyes import inspect
    from pdk.eyes.browser import Eyes

    pytest.importorskip("playwright.sync_api")
    if not gateway.health().get("runtime"):
        pytest.skip("Perspective's session API is unavailable on this gateway")
    path = f"PDK/_selftest/Defects {uuid.uuid4().hex[:6]}"
    directory = sync.local_dir(sandbox, path, tmp_path)
    directory.mkdir(parents=True)
    (directory / sync.VIEW_FILE).write_text(DEFECTS.read_text(encoding="utf-8"), encoding="utf-8")
    assert sync.deploy(gateway, directory, bundle, root=tmp_path).ok
    expected = {
        ("Q001", "/root/children/1/propConfig/props.text", "root/MissingTag"),
        ("Q002", "/root/children/2/propConfig/props.text", "root/ScriptError"),
        ("V001", "/root/children/3/children/0", "root/Row/Narrow"),
        ("V007", "/root/children/4", "root/Collapsed"),
        ("L001", "/root/children/0/events/system/onStartup", "root/Healthy"),
    }
    try:
        with Eyes() as eyes:
            for _ in range(2):
                result = inspect.inspect_view(eyes, gateway, path)
                found = {tuple(line.split(" ")[1:4]) for line in result.text.splitlines()[1:]}
                found = {(code, pointer, name.strip("()")) for code, pointer, name in found}
                assert found == expected, result.text
                assert not result.ok and result.errors == 4 and estimate_tokens(result.text) <= 1500
    finally:
        assert sync.delete(gateway, directory, root=tmp_path).ok


def _selftest_tags(gateway: Gateway, name: str, tags: list[dict]) -> str:
    """Import memory tags into PDK/_selftest/<name>; skips when the gateway is over its tag license."""
    from pdk import tags as simulated

    folder = f"PDK/_selftest/{name}"
    gateway.import_tags(folder, {"tags": tags})
    nodes = gateway.tags(folder, depth=3)["nodes"]
    if any(node.get("quality") == simulated.LICENSE_EXCEEDED for node in nodes):
        gateway.delete_tags(folder)
        pytest.skip("the gateway is over its tag license: new tags are Bad_LicenseExceeded")
    return folder


def test_simulated_tags_write_quality_alarm_and_guard(gateway: Gateway, sandbox: str) -> None:
    """F5 part B: import, write with a quality, read back, an alarm that fires and clears, writes only in PDK/."""
    import time

    from pdk import tags as simulated

    level = {"name": "Level", "tagType": "AtomicTag", "valueSource": "memory", "dataType": "Float8", "value": 20,
             "alarms": [{"name": "High", "mode": "AboveValue", "setpointA": 80, "priority": "High",
                         "displayPath": "Selftest/Level"}]}
    folder = _selftest_tags(gateway, f"Tags {uuid.uuid4().hex[:6]}", [level])
    path = f"{folder}/Level"
    try:
        assert simulated.write(gateway, path, "95")["value"] == 95.0
        deadline = time.monotonic() + 5
        while not [a for a in gateway.alarms(folder)["alarms"] if not a["cleared"]] and time.monotonic() < deadline:
            time.sleep(0.2)
        [event] = gateway.alarms(folder)["alarms"]
        assert (event["state"], event["priority"], event["displayPath"]) == ("ActiveUnacked", "High", "Selftest/Level")
        assert gateway.acknowledge_alarms(folder)["acknowledged"] == 1
        assert [a["state"] for a in gateway.alarms(folder)["alarms"]] == ["ActiveAcked"]
        with pytest.raises(GatewayError) as outside_ack:
            gateway.acknowledge_alarms("[default]Planta")
        assert outside_ack.value.code == "G013"
        stale = simulated.write(gateway, path, None, "Bad_Stale")
        assert (stale["value"], stale["quality"]) == (95.0, "Bad_Stale")
        assert simulated.write(gateway, path, "20", "Good")["quality"] == "Good"
        with pytest.raises(GatewayError) as outside:
            gateway.write_tags([{"path": "[default]Planta/X", "value": 1}])
        assert outside.value.code == "G013"
        with pytest.raises(GatewayError) as opc:
            gateway.import_tags(folder, {"name": "Opc", "tagType": "AtomicTag", "valueSource": "opc"})
        assert opc.value.code == "G009"
        assert gateway.read_tags(["[default]PDK/_selftest/Nope"])["results"][0]["quality"] == "Bad_NotFound"
    finally:
        gateway.delete_tags(folder)
    assert gateway.read_tags([path])["results"][0]["quality"] == "Bad_NotFound"


def test_inspect_sets_tags_and_reads_them_back(gateway: Gateway, sandbox: str, bundle, tmp_path: Path) -> None:
    """F5 part B exit criterion: a tag step reaches a bound label, a button's script writes a tag that expect reads
    back, and a forced bad quality is Q001 on the binding."""
    from pdk.eyes import inspect
    from pdk.eyes.browser import Eyes

    pytest.importorskip("playwright.sync_api")
    if not gateway.health().get("runtime"):
        pytest.skip("Perspective's session API is unavailable on this gateway")
    run = uuid.uuid4().hex[:6]
    folder = _selftest_tags(gateway, f"Tags {run}", [
        {"name": "Value", "tagType": "AtomicTag", "valueSource": "memory", "dataType": "Int4", "value": 0},
        {"name": "Message", "tagType": "AtomicTag", "valueSource": "memory", "dataType": "String", "value": ""}])
    tag = f"[default]{folder}"
    view = {**VIEW, "root": {**VIEW["root"], "children": [
        {"type": "ia.display.label", "meta": {"name": "Reading"}, "position": {"basis": "32px"},
         "propConfig": {"props.text": {"binding": {"type": "tag", "config": {
             "mode": "direct", "tagPath": f"{tag}/Value", "fallbackDelay": 2.5}}}}},
        {"type": "ia.input.button", "meta": {"name": "Send"}, "position": {"basis": "40px"},
         "props": {"text": "Send"},
         "events": {"component": {"onActionPerformed": {"type": "script", "scope": "G", "config": {
             "script": f"\tsystem.tag.writeBlocking(['{tag}/Message'], ['Done'])"}}}}}]}}
    path = f"PDK/_selftest/Tags {run}"
    directory = sync.local_dir(sandbox, path, tmp_path)
    directory.mkdir(parents=True)
    (directory / sync.VIEW_FILE).write_text(json.dumps(view), encoding="utf-8")
    assert sync.deploy(gateway, directory, bundle, root=tmp_path).ok
    short = folder.removeprefix("PDK/")
    try:
        with Eyes() as eyes:
            result = inspect.inspect_view(
                eyes, gateway, path, act=f'tag "{short}/Value" 7; click root/Send',
                expect=f'root/Reading text "7"; tag "{short}/Message" = Done; tag "{short}/Value" quality Good')
            assert result.ok and "act: 2/2 done" in result.text and "expect: 3/3 passed" in result.text, result.text
            result = inspect.inspect_view(eyes, gateway, path, act=f'tag "{short}/Value" quality Bad_Stale',
                                          expect=f'tag "{short}/Value" = 7')
            assert "Q001 /root/children/0/propConfig/props.text (root/Reading)" in result.text, result.text
            assert "expect: 0/1 passed" in result.text and "(Bad_Stale)" in result.text
            result = inspect.inspect_view(eyes, gateway, path, act="tag [default]Planta/X 1")
            assert "A004" in result.text and "G013" in result.text, result.text
    finally:
        assert sync.delete(gateway, directory, root=tmp_path).ok
        gateway.delete_tags(folder)

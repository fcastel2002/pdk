"""pull / deploy / delete against an in-memory module: sidecar, expected signatures and the 409 report."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from pdk import sync
from pdk.cli import main
from pdk.diagnostics import make
from pdk.gateway.client import GatewayError
from pdk.sync import GATEWAY_COPY, MISSING, PAGE_CONFIG, SIDECAR, VIEW_FILE
from tests.helpers import label, view

PROJECT = "sandbox"


class FakeGateway:
    """The module's resource contract: signature checks, 409 with the current state, folder listings."""

    url = "http://127.0.0.1:8088"

    def __init__(self, target: str | None = None) -> None:
        self.views: dict[str, tuple[str, str]] = {}  # path -> (view.json text, actor), in the sandbox
        self.projects: dict[str, dict[str, tuple[str, str]]] = {PROJECT: self.views}
        self.target = target
        if target:
            self.projects[target] = {}
        self.pages: dict[str, Any] = {}
        self.page_writes = 0
        self.writes = 0

    def _views(self, project: str) -> dict[str, tuple[str, str]]:
        if project not in self.projects:
            raise GatewayError("G007", f"project {project} does not exist", status=404)
        return self.projects[project]

    def save(self, path: str, data: dict[str, Any], actor: str = "admin", project: str = PROJECT) -> None:
        """A write that does not go through PDK, like a Designer save."""
        self._views(project)[path] = (json.dumps(data, indent=2), actor)

    def signature(self, path: str, project: str = PROJECT) -> str:
        return hashlib.sha256(self._views(project)[path][0].encode("utf-8")).hexdigest()

    def describe(self, path: str, project: str = PROJECT) -> dict[str, Any]:
        text, actor = self._views(project)[path]
        return {"project": project, "path": path, "signature": self.signature(path, project), "folder": False,
                "lastModification": {"actor": actor, "timestamp": "2026-10-04T20:00:00Z"},
                "files": {VIEW_FILE: text}}

    def health(self) -> dict[str, Any]:
        return {"sandbox": PROJECT, "writeEnabled": True, "target": self.target}

    def _page_config(self) -> dict[str, Any]:
        text = json.dumps({"pages": self.pages})
        return {"path": "", "signature": hashlib.sha256(text.encode()).hexdigest(), "files": {"config.json": text}}

    def resource(self, project: str, type_key: str, path: str = "") -> dict[str, Any]:
        if type_key == PAGE_CONFIG:
            return self._page_config()
        views = self._views(project)
        if path in views:
            return self.describe(path, project)
        prefix = path + "/" if path else ""
        children = [{"path": p, "folder": False, "signature": self.signature(p, project)}
                    for p in sorted(views) if p.startswith(prefix)]
        if not children and path:
            raise GatewayError("G007", f"{path} does not exist", status=404)
        return {"project": project, "path": path, "folder": True, "children": children}

    def _check(self, project: str, path: str, signature: str | None) -> None:
        exists = path in self._views(project)
        if exists != (signature is not None) or (exists and self.signature(path, project) != signature):
            payload = {"error": "CONFLICT", "current": self.describe(path, project) if exists else None}
            raise GatewayError("G008", f"{path} was not written", status=409, payload=payload)

    def _writable(self, project: str, type_key: str, path: str) -> None:
        """Sandbox.requireWritable(project, target) of the module."""
        if project == PROJECT:
            return
        if project != self.target:
            raise GatewayError("G006", f"{project} is not the sandbox", status=403)
        if type_key != sync.VIEWS or not path or path == "PDK" or path.startswith("PDK/"):
            raise GatewayError("G006", f"the target only takes views outside PDK/, not {path}", status=403)

    def put_resource(self, project: str, type_key: str, path: str, files: dict[str, str],
                     signature: str | None = None, scope: str | None = None) -> dict[str, Any]:
        self._writable(project, type_key, path)
        if type_key == PAGE_CONFIG:
            if signature != self._page_config()["signature"]:
                raise GatewayError("G008", "page-config changed", status=409, payload={"current": None})
            self.pages = json.loads(files["config.json"])["pages"]
            self.page_writes += 1
            return self._page_config()
        self._check(project, path, signature)
        self.writes += 1
        self._views(project)[path] = (files[VIEW_FILE], "pdk")
        described = self.describe(path, project)
        del described["files"]
        return {**described, "op": "created" if signature is None else "modified"}

    def delete_resource(self, project: str, type_key: str, path: str, signature: str) -> dict[str, Any]:
        self._writable(project, type_key, path)
        views = self._views(project)
        if path not in views:
            raise GatewayError("G007", f"{path} does not exist", status=404)
        self._check(project, path, signature)
        del views[path]
        folder = path.rsplit("/", 1)[0]
        empty = "/" in path and not any(p.startswith(folder + "/") for p in views)
        return {"op": "deleted", "pruned": [folder] if empty else []}


@pytest.fixture
def gw() -> FakeGateway:
    fake = FakeGateway()
    fake.save("PDK/Menu", view(label("Title"), label("Subtitle")), actor="pdk")
    return fake


def _edit(directory: Path, change) -> None:
    data = json.loads((directory / VIEW_FILE).read_text(encoding="utf-8"))
    change(data)
    (directory / VIEW_FILE).write_text(json.dumps(data, indent=2), encoding="utf-8")


def _set_text(index: int, text: str):
    return lambda data: data["root"]["children"][index]["props"].update(text=text)


def test_pull_writes_the_view_and_its_signature(gw: FakeGateway, tmp_path: Path) -> None:
    outcome = sync.pull(gw, "PDK/Menu", root=tmp_path)
    directory = tmp_path / PROJECT / "PDK" / "Menu"
    assert outcome.ok and "pulled sandbox:PDK/Menu" in outcome.text
    sidecar = json.loads((directory / SIDECAR).read_text(encoding="utf-8"))
    assert sidecar["signature"] == gw.signature("PDK/Menu") and sidecar["project"] == PROJECT
    assert sidecar["base"][VIEW_FILE] == (directory / VIEW_FILE).read_text(encoding="utf-8")


def test_deploy_sends_the_pulled_signature(gw: FakeGateway, tmp_path: Path, bundle) -> None:
    sync.pull(gw, "PDK/Menu", root=tmp_path)
    directory = tmp_path / PROJECT / "PDK" / "Menu"
    unchanged = sync.deploy(gw, directory, bundle, root=tmp_path)
    assert unchanged.ok and "nothing to deploy" in unchanged.text and gw.writes == 0

    _edit(directory, _set_text(0, "Agent"))
    outcome = sync.deploy(gw, directory / VIEW_FILE, bundle, root=tmp_path)
    assert outcome.ok and outcome.text.startswith("modified sandbox:PDK/Menu")
    assert json.loads(gw.views["PDK/Menu"][0])["root"]["children"][0]["props"]["text"] == "Agent"
    assert sync.read_sidecar(directory)["signature"] == gw.signature("PDK/Menu")

    _edit(directory, _set_text(0, "Again"))
    assert sync.deploy(gw, directory, bundle, root=tmp_path).ok  # the new signature was kept


def test_designer_save_gives_a_409_with_a_three_way_diff(gw: FakeGateway, tmp_path: Path, bundle) -> None:
    sync.pull(gw, "PDK/Menu", root=tmp_path)
    directory = tmp_path / PROJECT / "PDK" / "Menu"
    designer = view(label("Title"), label("Subtitle"))
    designer["root"]["children"][0]["props"]["text"] = "Designer"
    designer["root"]["children"][1]["props"]["text"] = "Designer too"
    gw.save("PDK/Menu", designer)
    designer_text = gw.views["PDK/Menu"][0]

    _edit(directory, _set_text(0, "Agent"))
    outcome = sync.deploy(gw, directory, bundle, root=tmp_path)
    assert not outcome.ok and gw.views["PDK/Menu"][0] == designer_text  # the gateway is untouched
    text = outcome.text
    assert text.startswith("G008 conflict: sandbox:PDK/Menu changed on the gateway") and "admin" in text
    assert "Gateway changes since your pull (2)" in text and "Your changes (1)" in text
    assert '~ /root/children/0/props/text (root/Title): "Title" -> "Designer"' in text
    assert "Both changed (1): /root/children/0/props/text" in text
    assert (directory / GATEWAY_COPY).read_text(encoding="utf-8") == designer_text
    assert sync.read_sidecar(directory)["conflict"]["signature"] == gw.signature("PDK/Menu")

    again = sync.deploy(gw, directory, bundle, root=tmp_path)
    assert not again.ok and gw.writes == 0  # without --resolved it never overwrites

    _edit(directory, _set_text(1, "Designer too"))  # merge their other change
    resolved = sync.deploy(gw, directory, bundle, resolved=True, root=tmp_path)
    assert resolved.ok and gw.writes == 1
    assert not (directory / GATEWAY_COPY).exists() and "conflict" not in sync.read_sidecar(directory)


def test_invalid_views_are_not_deployed(gw: FakeGateway, tmp_path: Path, bundle) -> None:
    sync.pull(gw, "PDK/Menu", root=tmp_path)
    directory = tmp_path / PROJECT / "PDK" / "Menu"
    _edit(directory, lambda data: data["root"]["children"][0]["props"].update(txet="typo"))
    outcome = sync.deploy(gw, directory, bundle, root=tmp_path)
    assert not outcome.ok and "Not deployed" in outcome.text and "/root/children/0/props/txet" in outcome.text
    assert gw.writes == 0


def test_new_views_are_created_from_the_work_layout(gw: FakeGateway, tmp_path: Path, bundle) -> None:
    directory = tmp_path / PROJECT / "PDK" / "New One"
    directory.mkdir(parents=True)
    (directory / VIEW_FILE).write_text(json.dumps(view(label())), encoding="utf-8")
    created = sync.deploy(gw, directory, bundle, root=tmp_path)
    assert created.ok and created.text.startswith("created sandbox:PDK/New One new ->")
    assert sync.read_sidecar(directory)["signature"] == gw.signature("PDK/New One")

    taken = tmp_path / PROJECT / "PDK" / "Menu"
    taken.mkdir(parents=True)
    (taken / VIEW_FILE).write_text(json.dumps(view(label())), encoding="utf-8")
    exists = sync.deploy(gw, taken, bundle, root=tmp_path)
    assert not exists.ok and "already exists on the gateway" in exists.text
    assert "Gateway version -> yours" in exists.text and f"`pdk deploy {taken.as_posix()}" in exists.text
    assert gw.views["PDK/Menu"][0] != (taken / VIEW_FILE).read_text(encoding="utf-8")
    replaced = sync.deploy(gw, taken, bundle, resolved=True, root=tmp_path)
    assert replaced.ok and "modified sandbox:PDK/Menu" in replaced.text, replaced.text
    assert json.loads(gw.views["PDK/Menu"][0]) == json.loads((taken / VIEW_FILE).read_text(encoding="utf-8"))
    assert "conflict" not in sync.read_sidecar(taken)

    outside = sync.deploy(gw, tmp_path.parent, bundle, root=tmp_path)
    assert not outside.ok and SIDECAR in outside.text


def test_delete_needs_the_current_signature(gw: FakeGateway, tmp_path: Path) -> None:
    gw.save("PDK/Other", view(label()), actor="pdk")
    sync.pull(gw, "PDK/Menu", root=tmp_path)
    directory = tmp_path / PROJECT / "PDK" / "Menu"
    gw.save("PDK/Menu", view(label("Changed")))
    stale = sync.delete(gw, directory, root=tmp_path)
    assert not stale.ok and "PDK/Menu" in gw.views and "delete" in stale.text and "--resolved" in stale.text
    deleted = sync.delete(gw, directory, resolved=True, root=tmp_path)
    assert deleted.ok and "PDK/Menu" not in gw.views and not (directory / SIDECAR).exists()
    assert (directory / VIEW_FILE).exists()  # the local copy stays

    sync.pull(gw, "PDK/Other", root=tmp_path)
    last = sync.delete(gw, tmp_path / PROJECT / "PDK" / "Other", root=tmp_path)
    assert last.ok and "removed the empty folder(s) PDK" in last.text


def test_pull_keeps_local_edits_unless_forced(gw: FakeGateway, tmp_path: Path) -> None:
    sync.pull(gw, "PDK/Menu", root=tmp_path)
    directory = tmp_path / PROJECT / "PDK" / "Menu"
    _edit(directory, _set_text(0, "Mine"))
    kept = sync.pull(gw, "PDK/Menu", root=tmp_path)
    assert not kept.ok and "local edits" in kept.text and "Mine" in (directory / VIEW_FILE).read_text(encoding="utf-8")
    forced = sync.pull(gw, str(directory), force=True, root=tmp_path)  # a local copy works as the path
    assert forced.ok and "Mine" not in (directory / VIEW_FILE).read_text(encoding="utf-8")


def test_pull_of_a_folder_pulls_every_view_below(gw: FakeGateway, tmp_path: Path) -> None:
    gw.save("PDK/Sub/Deep", view(label()))
    outcome = sync.pull(gw, "PDK", root=tmp_path)
    assert outcome.ok and outcome.text.count("pulled") == 2
    assert (tmp_path / PROJECT / "PDK" / "Sub" / "Deep" / SIDECAR).is_file()


def test_json_diff_by_pointer() -> None:
    before = {"a": 1, "b": True, "list": [1, 2], "x/y": {"k": "v"}}
    after = {"a": 1.0, "b": 1, "list": [1], "x/y": {"k": "w"}, "new": None}
    assert sync.json_diff(before, after) == [
        ("/b", True, 1),
        ("/list/1", 2, MISSING),
        ("/x~1y/k", "v", "w"),
        ("/new", MISSING, None),
    ]
    theirs, mine = {"p": {"q": 1, "r": 1}}, {"p": {"q": 1, "r": 2}}
    assert sync.overlapping(sync.json_diff({}, theirs), sync.json_diff({}, mine), theirs, mine) == ["/p"]
    assert sync.overlapping(sync.json_diff({}, theirs), sync.json_diff({}, theirs), theirs, theirs) == []


def test_conflict_reports_stay_within_the_budget() -> None:
    lines = "\n".join(f"  ~ /root/children/{i}/props/text: \"a\" -> \"b\"" for i in range(400))
    text = sync._budgeted(f"head\n{lines}\nresolution line", 300)
    assert len(text) <= 300 * 3.5 and text.endswith("resolution line") and "more lines" in text


def test_a_save_that_only_reformats_is_reported_as_such(gw: FakeGateway, tmp_path: Path, bundle) -> None:
    sync.pull(gw, "PDK/Menu", root=tmp_path)
    directory = tmp_path / PROJECT / "PDK" / "Menu"
    text, _ = gw.views["PDK/Menu"]
    gw.views["PDK/Menu"] = (json.dumps(json.loads(text), sort_keys=True), "admin")  # the Designer's first save
    _edit(directory, _set_text(0, "Agent"))
    outcome = sync.deploy(gw, directory, bundle, root=tmp_path)
    assert not outcome.ok and "none in the view content (only formatting" in outcome.text


def test_deploy_maps_a_preview_page_and_delete_removes_it(gw: FakeGateway, tmp_path: Path, bundle) -> None:
    gw.pages = {"/": {"viewPath": "Home"}}
    directory = tmp_path / PROJECT / "PDK" / "New One"
    directory.mkdir(parents=True)
    (directory / VIEW_FILE).write_text(json.dumps(view(label())), encoding="utf-8")
    created = sync.deploy(gw, directory, bundle, root=tmp_path)
    assert "preview: http://127.0.0.1:8088/data/perspective/client/sandbox/pdk/PDK/New-One" in created.text
    assert gw.pages == {"/": {"viewPath": "Home"}, "/pdk/PDK/New-One": {"viewPath": "PDK/New One"}}
    _edit(directory, _set_text(0, "Again"))
    sync.deploy(gw, directory, bundle, root=tmp_path)
    assert gw.page_writes == 1  # the page is already there

    deleted = sync.delete(gw, directory, root=tmp_path)
    assert deleted.ok and gw.pages == {"/": {"viewPath": "Home"}}  # only its own page goes


def test_page_config_is_edited_additively_on_a_409(gw: FakeGateway) -> None:
    original = gw.put_resource
    raced = []

    def racing_put(project, type_key, path, files, signature=None, scope=None):
        if type_key == PAGE_CONFIG and not raced:  # someone adds a page between our read and our write
            raced.append(True)
            gw.pages["/theirs"] = {"viewPath": "Theirs"}
        return original(project, type_key, path, files, signature, scope)

    gw.put_resource = racing_put
    assert sync.ensure_preview(gw, PROJECT, "PDK/Menu") is None
    assert gw.pages == {"/theirs": {"viewPath": "Theirs"}, "/pdk/PDK/Menu": {"viewPath": "PDK/Menu"}}
    gw.pages["/pdk/PDK/Other"] = {"viewPath": "Elsewhere"}
    assert "already opens Elsewhere" in sync.ensure_preview(gw, PROJECT, "PDK/Other")


def test_promote_copies_the_sandbox_view_with_the_target_signature(gw: FakeGateway, tmp_path: Path,
                                                                   bundle) -> None:
    sync.pull(gw, "PDK/Menu", root=tmp_path)
    source = tmp_path / PROJECT / "PDK" / "Menu"
    first = sync.promote(gw, source, bundle, root=tmp_path)
    assert first.ok and "promote sandbox:PDK/Menu -> Menu: created sandbox:Menu" in first.text, first.text
    assert gw.views["Menu"][0] == gw.views["PDK/Menu"][0]

    _edit(source, _set_text(0, "v2"))
    sync.deploy(gw, source, bundle, root=tmp_path)
    second = sync.promote(gw, source, bundle, root=tmp_path)
    assert second.ok and "modified sandbox:Menu" in second.text

    edited = json.loads(gw.views["Menu"][0])
    edited["root"]["children"][1]["props"]["text"] = "by hand"
    gw.save("Menu", edited)  # someone edits the promoted view in the Designer
    _edit(source, _set_text(0, "v3"))
    sync.deploy(gw, source, bundle, root=tmp_path)
    blocked = sync.promote(gw, source, bundle, root=tmp_path)
    assert not blocked.ok and "G008 conflict: sandbox:Menu changed" in blocked.text
    assert json.loads(gw.views["Menu"][0])["root"]["children"][1]["props"]["text"] == "by hand"


def test_promote_refuses_what_is_not_deployed(gw: FakeGateway, tmp_path: Path, bundle) -> None:
    sync.pull(gw, "PDK/Menu", root=tmp_path)
    source = tmp_path / PROJECT / "PDK" / "Menu"
    _edit(source, _set_text(0, "draft"))
    assert "has local edits" in sync.promote(gw, source, bundle, root=tmp_path).text
    gw.save("Plain", view(label()))
    sync.pull(gw, "Plain", root=tmp_path)
    assert "pass --to" in sync.promote(gw, tmp_path / PROJECT / "Plain", bundle, root=tmp_path).text
    sync.pull(gw, "PDK/Menu", force=True, root=tmp_path)
    assert "not a valid target" in sync.promote(gw, source, bundle, to="PDK/X", root=tmp_path).text


TARGET = "Planta"


@pytest.fixture
def tgw() -> FakeGateway:
    fake = FakeGateway(target=TARGET)
    fake.save("PDK/Menu", view(label("Title"), label("Subtitle")), actor="pdk")
    return fake


def test_promote_to_the_target_writes_only_the_finished_view(tgw: FakeGateway, tmp_path: Path, bundle) -> None:
    sync.pull(tgw, "PDK/Menu", root=tmp_path)
    source = tmp_path / PROJECT / "PDK" / "Menu"
    first = sync.promote(tgw, source, bundle, to_project="target", to="Planta/Menu", root=tmp_path)
    assert first.ok and f"-> {TARGET}:Planta/Menu: created {TARGET}:Planta/Menu" in first.text, first.text
    assert "preview" not in first.text and tgw.page_writes == 0
    assert tgw.projects[TARGET]["Planta/Menu"][0] == tgw.views["PDK/Menu"][0] and "Planta/Menu" not in tgw.views
    target_dir = tmp_path / TARGET / "Planta" / "Menu"
    assert sync.read_sidecar(target_dir)["signature"] == tgw.signature("Planta/Menu", TARGET)

    _edit(source, _set_text(0, "v2"))
    sync.deploy(tgw, source, bundle, root=tmp_path)
    assert sync.promote(tgw, source, bundle, to_project=TARGET, to="Planta/Menu", root=tmp_path).ok
    edited = json.loads(tgw.projects[TARGET]["Planta/Menu"][0])
    edited["root"]["children"][1]["props"]["text"] = "by hand"
    tgw.save("Planta/Menu", edited, project=TARGET)  # a Designer save, or a re-import from production
    _edit(source, _set_text(0, "v3"))
    sync.deploy(tgw, source, bundle, root=tmp_path)
    blocked = sync.promote(tgw, source, bundle, to_project="target", to="Planta/Menu", root=tmp_path)
    assert not blocked.ok and "G008" in blocked.text
    assert json.loads(tgw.projects[TARGET]["Planta/Menu"][0])["root"]["children"][1]["props"]["text"] == "by hand"


def test_promote_over_a_target_view_never_pulled(tgw: FakeGateway, tmp_path: Path, bundle) -> None:
    tgw.save("Menu", view(label("Original")), project=TARGET)
    sync.pull(tgw, "PDK/Menu", root=tmp_path)
    source = tmp_path / PROJECT / "PDK" / "Menu"
    blocked = sync.promote(tgw, source, bundle, to_project="target", root=tmp_path)
    assert not blocked.ok and "already exists on the gateway" in blocked.text
    assert "repeat the promote with --resolved" in blocked.text and "pdk deploy" not in blocked.text
    assert json.loads(tgw.projects[TARGET]["Menu"][0])["root"]["children"][0]["props"]["text"] == "Original"

    replaced = sync.promote(tgw, source, bundle, to_project="target", resolved=True, root=tmp_path)
    assert replaced.ok and f"modified {TARGET}:Menu" in replaced.text, replaced.text
    assert tgw.projects[TARGET]["Menu"][0] == tgw.views["PDK/Menu"][0]


def test_the_target_takes_views_only_through_promote(tgw: FakeGateway, tmp_path: Path, bundle) -> None:
    sync.pull(tgw, "PDK/Menu", root=tmp_path)
    source = tmp_path / PROJECT / "PDK" / "Menu"
    assert sync.promote(tgw, source, bundle, to_project="target", root=tmp_path).ok
    target_dir = tmp_path / TARGET / "Menu"
    assert "starts from the sandbox" in sync.promote(tgw, target_dir, bundle, to_project="target", to="X",
                                                     root=tmp_path).text
    _edit(target_dir, _set_text(0, "direct"))
    refused = sync.deploy(tgw, target_dir, bundle, root=tmp_path)
    assert not refused.ok and "only with promote" in refused.text

    sync.pull(tgw, "Menu", project=TARGET, force=True, root=tmp_path)
    deleted = sync.delete(tgw, target_dir, root=tmp_path)
    assert not deleted.ok and "Menu" in tgw.projects[TARGET] and tgw.page_writes == 0


def test_delete_never_reaches_the_target_project(tgw: FakeGateway, tmp_path: Path, bundle,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    sync.pull(tgw, "PDK/Menu", root=tmp_path)
    assert sync.promote(tgw, tmp_path / PROJECT / "PDK" / "Menu", bundle, to_project="target", root=tmp_path).ok
    target_dir = tmp_path / TARGET / "Menu"
    calls: list[str] = []
    real_delete = tgw.delete_resource

    def spy(project: str, *args: Any) -> dict[str, Any]:
        calls.append(project)
        return real_delete(project, *args)

    monkeypatch.setattr(tgw, "delete_resource", spy)
    refused = sync.delete(tgw, target_dir, root=tmp_path)
    assert not refused.ok and f"{TARGET} is the target project" in refused.text and calls == []
    assert "Menu" in tgw.projects[TARGET] and (target_dir / SIDECAR).exists()


def test_promote_to_the_target_checks_references(tgw: FakeGateway, tmp_path: Path, bundle) -> None:
    tgw.save("PDK/Card", view(label("Card")), actor="pdk")
    tgw.save("Shared/Header", view(label("Header")))
    with_sandbox_ref = view(label("Title"))
    with_sandbox_ref["custom"] = {"popup": "PDK/Card"}
    tgw.save("PDK/Menu", with_sandbox_ref, actor="pdk")
    sync.pull(tgw, "PDK/Menu", root=tmp_path)
    source = tmp_path / PROJECT / "PDK" / "Menu"
    refused = sync.promote(tgw, source, bundle, to_project="target", root=tmp_path)
    assert not refused.ok and "references sandbox views (PDK/Card)" in refused.text
    assert "Menu" not in tgw.projects[TARGET]

    with_shared_ref = view(label("Title"))
    with_shared_ref["custom"] = {"header": "Shared/Header"}
    tgw.save("PDK/Menu", with_shared_ref, actor="pdk")
    sync.pull(tgw, "PDK/Menu", force=True, root=tmp_path)
    warned = sync.promote(tgw, source, bundle, to_project="target", root=tmp_path)
    assert warned.ok and f"views {TARGET} does not have (Shared/Header)" in warned.text, warned.text


def test_promote_to_a_missing_or_wrong_target(gw: FakeGateway, tgw: FakeGateway, tmp_path: Path, bundle) -> None:
    sync.pull(gw, "PDK/Menu", root=tmp_path)
    source = tmp_path / PROJECT / "PDK" / "Menu"
    assert "no target project" in sync.promote(gw, source, bundle, to_project="target", root=tmp_path).text
    assert "is not the target project" in sync.promote(tgw, source, bundle, to_project="Otro", root=tmp_path).text


def test_deploy_and_delete_take_the_sandbox_view_path(gw: FakeGateway, tmp_path: Path, bundle) -> None:
    directory = tmp_path / PROJECT / "PDK" / "Bench" / "Tabla"
    directory.mkdir(parents=True)
    (directory / VIEW_FILE).write_text(json.dumps(view(label())), encoding="utf-8")
    assert sync.deploy(gw, Path("PDK/Bench/Tabla"), bundle, root=tmp_path).ok
    assert "PDK/Bench/Tabla" in gw.views
    assert sync.delete(gw, Path("PDK/Bench/Tabla"), root=tmp_path).ok
    assert "PDK/Bench/Tabla" not in gw.views


def test_a_sandbox_path_without_a_local_folder_still_says_where_to_put_it(gw: FakeGateway, tmp_path: Path,
                                                                         bundle) -> None:
    outcome = sync.deploy(gw, Path("PDK/Nowhere"), bundle, root=tmp_path)
    assert not outcome.ok and "<project>/<view path>" in outcome.text


# --- the gateway's compile of view scripts ----------------------------------------------------------


def _scripted(text: str = "\treturn 1") -> dict[str, Any]:
    return view(label("Title", scripts={"customMethods": [{"name": "go", "params": [], "script": text}]}))


def test_compile_without_the_gateway_route_is_skipped_unless_strict(gw: FakeGateway) -> None:
    def missing(sources: list[dict[str, str]]) -> list[dict[str, Any]]:
        raise GatewayError("G007", "no /scripts/compile route", status=404)

    gw.compile_scripts = missing
    text = json.dumps(_scripted())
    assert sync.compiled(gw, text) == []
    with pytest.raises(GatewayError):
        sync.compiled(gw, text, strict=True)
    with pytest.raises(GatewayError):
        sync.compiler(gw)(text)


def test_a_view_without_scripts_never_reaches_the_gateway(gw: FakeGateway) -> None:
    def spy(sources: list[dict[str, str]]) -> list[dict[str, Any]]:
        raise AssertionError("nothing to compile")

    gw.compile_scripts = spy
    text = json.dumps(view(label("Title")))
    assert sync.compiled(gw, text) == [] and sync.compiled(gw, text, strict=True) == []


def test_compiled_maps_the_gateways_rejections_to_e009(gw: FakeGateway) -> None:
    def rejects(sources: list[dict[str, str]]) -> list[dict[str, Any]]:
        return [{"id": s["id"], "ok": False, "line": 2, "column": 1, "message": "invalid syntax"} for s in sources]

    gw.compile_scripts = rejects
    found = sync.compiled(gw, json.dumps(_scripted()))
    assert [(d["code"], d["pointer"]) for d in found] == [
        ("E009", "/root/children/0/scripts/customMethods/0/script")]


def test_deploy_refuses_a_script_the_gateway_does_not_compile(gw: FakeGateway, tmp_path: Path, bundle) -> None:
    sync.pull(gw, "PDK/Menu", root=tmp_path)
    directory = tmp_path / PROJECT / "PDK" / "Menu"
    _edit(directory, lambda data: data["root"]["children"][0].update(
        scripts={"customMethods": [{"name": "go", "params": [], "script": "\tx = (1"}]}))

    def rejects(sources: list[dict[str, str]]) -> list[dict[str, Any]]:
        return [{"id": "0", "ok": False, "line": 2, "column": 9, "message": "unexpected EOF"}]

    gw.compile_scripts = rejects
    outcome = sync.deploy(gw, directory, bundle, root=tmp_path)
    assert not outcome.ok and "E009" in outcome.text and "Not deployed" in outcome.text
    assert "/root/children/0/scripts/customMethods/0/script" in outcome.text
    assert gw.writes == 0


def test_cli_validate_compile_reports_the_gateways_findings(tmp_path: Path, monkeypatch, capsys, bundle) -> None:
    path = tmp_path / "view.json"
    path.write_text(json.dumps(view(label())), encoding="utf-8")
    rejected = make("E009", "error", "/root/children/0/props/text", "root/Title", "script does not compile",
                    "fix the Jython 2.7 syntax")
    monkeypatch.setattr(sync, "compiler", lambda gateway: lambda text: [rejected])
    assert main(["validate", str(path), "--compile"]) == 1
    assert "E009" in capsys.readouterr().out


def test_cli_validate_compile_without_the_module_says_so(tmp_path: Path, monkeypatch, capsys, bundle) -> None:
    path = tmp_path / "view.json"
    path.write_text(json.dumps(view(label())), encoding="utf-8")

    def refuse(text: str) -> list[Any]:
        raise GatewayError("G007", "no /scripts/compile route", status=404)

    monkeypatch.setattr(sync, "compiler", lambda gateway: refuse)
    assert main(["validate", str(path), "--compile"]) == 1
    assert "--compile needs the gateway's module" in capsys.readouterr().err

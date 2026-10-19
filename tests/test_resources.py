"""Style classes and project scripts: pull kinds, checks, deploy, promote, delete and the sync dispatch.

A fake gateway keeps the resources of each project with their signatures (a stale write gets a 409 with the
current state, like the module) and records the calls it got.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from pdk import resources, sync
from pdk.bundle import Bundle, Functions
from pdk.gateway.client import GatewayError
from pdk.sync import SIDECAR

SANDBOX = "sandbox"
TARGET = "final"
STYLE = resources.STYLE_CLASS.type
SCRIPT_TYPE = resources.SCRIPT.type
CLASS_JSON = '{"base": {"style": {"color": "#333"}}}\n'
CODE = "def run():\n    return 1\n"


def _signature(files: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()


class FakeGateway:
    """Resources per (project, type, path) with signatures; writes and deletes check them like the module."""

    url = "http://127.0.0.1:8088"

    def __init__(self, target: str | None = TARGET) -> None:
        self.target = target
        self.store: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.puts: list[dict[str, Any]] = []
        self.deletes: list[dict[str, Any]] = []
        self.compile_results: list[dict[str, Any]] | None = None  # None: every source compiles
        self.compile_error: GatewayError | None = None

    def health(self, refresh: bool = False) -> dict[str, Any]:
        return {"sandbox": SANDBOX, "writeEnabled": True, "target": self.target}

    def add(self, project: str, type_key: str, path: str, files: dict[str, str]) -> None:
        """Seed the gateway with a resource, as if the Designer or an earlier deploy made it."""
        self.store[(project, type_key, path)] = self._state(project, type_key, path, files)

    def _state(self, project: str, type_key: str, path: str, files: dict[str, str]) -> dict[str, Any]:
        return {"project": project, "type": type_key, "path": path, "folder": False, "files": dict(files),
                "signature": _signature(files),
                "lastModification": {"actor": "admin", "timestamp": "2026-10-06T10:00:00Z"}}

    def resource(self, project: str, type_key: str, path: str = "") -> dict[str, Any]:
        state = self.store.get((project, type_key, path))
        if state is None:
            raise GatewayError("G007", f"{project}:{path} not found", status=404)
        return dict(state)

    def put_resource(self, project: str, type_key: str, path: str, files: dict[str, str],
                     signature: str | None = None, scope: str | None = None) -> dict[str, Any]:
        self.puts.append({"project": project, "type": type_key, "path": path, "files": files,
                          "signature": signature, "scope": scope})
        current = self.store.get((project, type_key, path))
        if (current is None) != (signature is None) or (current is not None and current["signature"] != signature):
            raise GatewayError("G008", f"{path} changed", status=409, payload={"current": current})
        state = self._state(project, type_key, path, files)
        self.store[(project, type_key, path)] = state
        return {**state, "op": "created" if signature is None else "modified"}

    def delete_resource(self, project: str, type_key: str, path: str, signature: str) -> dict[str, Any]:
        self.deletes.append({"project": project, "path": path, "signature": signature})
        current = self.store.get((project, type_key, path))
        if current is None:
            raise GatewayError("G007", f"{path} not found", status=404)
        if current["signature"] != signature:
            raise GatewayError("G008", "changed", status=409, payload={"current": current})
        del self.store[(project, type_key, path)]
        return {"op": "deleted"}

    def compile_scripts(self, sources: list[dict[str, str]]) -> list[dict[str, Any]]:
        if self.compile_error is not None:
            raise self.compile_error
        if self.compile_results is not None:
            return self.compile_results
        return [{"id": source["id"], "ok": True} for source in sources]


def _write_local(root: Path, project: str, kind: resources.Kind, path: str, text: str) -> Path:
    """A local copy as the agent edits it: `<root>/<project>/<folder>/<path>/<file>`."""
    directory = root / project / kind.folder / path
    directory.mkdir(parents=True, exist_ok=True)
    (directory / kind.file).write_text(text, encoding="utf-8")
    return directory


def _pulled_card(gw: FakeGateway, root: Path, path: str = "PDK/Card") -> Path:
    """Seed `PDK/Card` on the sandbox, pull it, and return its local folder."""
    gw.add(SANDBOX, STYLE, path, {resources.STYLE_CLASS.file: CLASS_JSON})
    assert resources.pull(gw, resources.STYLE_CLASS, path, root=root).ok
    return root / SANDBOX / "_style-classes" / Path(*path.split("/"))


# --- where a resource is --------------------------------------------------------------------------


@pytest.mark.parametrize("text, expected", [
    ("style-classes/PDK/Card", (resources.STYLE_CLASS, "PDK/Card")),
    ("_style-classes/PDK/Card", (resources.STYLE_CLASS, "PDK/Card")),
    ("style-class:PDK/Card", (resources.STYLE_CLASS, "PDK/Card")),
    ("scripts/PDK/util", (resources.SCRIPT, "PDK/util")),
    ("PDK/Menu", None),
    ("style-classes/", None),
])
def test_pull_kind(text: str, expected: tuple[resources.Kind, str] | None) -> None:
    assert resources.pull_kind(text) == expected


def test_kind_of_reads_the_folder_without_sidecar(tmp_path: Path) -> None:
    style = _write_local(tmp_path, "proj", resources.STYLE_CLASS, "PDK/Card", CLASS_JSON)
    script = _write_local(tmp_path, "proj", resources.SCRIPT, "PDK/util", CODE)
    assert resources.kind_of(style, tmp_path) is resources.STYLE_CLASS
    assert resources.kind_of(style / "style.json", tmp_path) is resources.STYLE_CLASS
    assert resources.kind_of(script, tmp_path) is resources.SCRIPT


def test_kind_of_reads_the_sidecar_type(tmp_path: Path) -> None:
    folder = tmp_path / "proj" / "Util"  # not under _scripts/: only the sidecar says what it is
    folder.mkdir(parents=True)
    (folder / SIDECAR).write_text(json.dumps({"type": SCRIPT_TYPE}), encoding="utf-8")
    assert resources.kind_of(folder, tmp_path) is resources.SCRIPT


def test_kind_of_views_folder_is_none(tmp_path: Path) -> None:
    views = tmp_path / "proj" / "PDK" / "Card"  # a view folder: work/<project>/PDK/<name>
    views.mkdir(parents=True)
    assert resources.kind_of(views, tmp_path) is None


# --- checks ---------------------------------------------------------------------------------------


def test_check_style_accepts_a_valid_class(bundle: Bundle) -> None:
    valid = {"base": {"style": {"color": "#333"}}, "variants": [{"pseudo": "hover", "style": {}}]}
    assert resources.check_style(json.dumps(valid), bundle) == []


def test_check_style_unknown_top_key_is_p005(bundle: Bundle) -> None:
    found = resources.check_style(json.dumps({"base": {}, "extra": 1}), bundle)
    assert [(d["code"], d["pointer"]) for d in found] == [("P005", "/extra")]


def test_check_style_unknown_pseudo_is_p005(bundle: Bundle) -> None:
    found = resources.check_style(json.dumps({"variants": [{"pseudo": "sparkle", "style": {}}]}), bundle)
    assert [(d["code"], d["pointer"]) for d in found] == [("P005", "/variants/0/pseudo")]


def test_check_style_misspelled_property_is_p006_warning(bundle: Bundle) -> None:
    found = resources.check_style(json.dumps({"base": {"style": {"colour": "#333"}}}), bundle)
    assert [(d["code"], d["severity"], d["pointer"]) for d in found] == [("P006", "warning", "/base/style/colour")]
    assert "did you mean 'color'" in found[0]["hint"]


def test_check_style_invalid_json_is_s000(bundle: Bundle) -> None:
    found = resources.check_style("{", bundle)
    assert [(d["code"], d["severity"]) for d in found] == [("S000", "error")]


def test_check_style_variants_must_be_a_list(bundle: Bundle) -> None:
    found = resources.check_style(json.dumps({"variants": {"pseudo": "hover"}}), bundle)
    assert [(d["code"], d["pointer"]) for d in found] == [("P005", "/variants")]


def test_check_script_gateway_syntax_error_is_e009(bundle: Bundle) -> None:
    gw = FakeGateway()
    gw.compile_results = [{"id": "0", "ok": False, "line": 4, "column": 8, "message": "m"}]
    found = resources.check_script(CODE, gw, bundle)
    assert [(d["code"], d["severity"]) for d in found] == [("E009", "error")]
    assert "at line 4, column 8: m" in found[0]["message"]


def test_check_script_gateway_down_is_silent_unless_strict(bundle: Bundle) -> None:
    gw = FakeGateway()
    gw.compile_error = GatewayError("G007", "x")
    assert resources.check_script(CODE, gw, bundle) == []
    with pytest.raises(GatewayError):
        resources.check_script(CODE, gw, bundle, strict=True)


def test_check_script_unknown_system_name_is_e008(bundle: Bundle) -> None:
    functions = Functions({}, frozenset({"system", "system.tag", "system.tag.readBlocking"}))
    known = dataclasses.replace(bundle, functions=functions)
    found = resources.check_script("v = system.tag.readBlockin('[default]x')\n", FakeGateway(), known)
    assert [d["code"] for d in found] == ["E008"]
    assert "system.tag.readBlockin" in found[0]["message"]


# --- deploy ---------------------------------------------------------------------------------------


def test_deploy_creates_a_style_class_with_scope_g(bundle: Bundle, tmp_path: Path) -> None:
    folder = _write_local(tmp_path, SANDBOX, resources.STYLE_CLASS, "PDK/Card", CLASS_JSON)
    gw = FakeGateway()
    outcome = resources.deploy(gw, resources.STYLE_CLASS, folder, bundle, root=tmp_path)
    assert outcome.ok and "created" in outcome.text
    put = gw.puts[-1]
    assert (put["project"], put["type"], put["path"], put["signature"], put["scope"]) == (
        SANDBOX, STYLE, "PDK/Card", None, "G")
    assert (folder / SIDECAR).is_file()


def test_deploy_creates_a_script_with_scope_a(bundle: Bundle, tmp_path: Path) -> None:
    folder = _write_local(tmp_path, SANDBOX, resources.SCRIPT, "PDK/util", CODE)
    gw = FakeGateway()
    assert resources.deploy(gw, resources.SCRIPT, folder, bundle, root=tmp_path).ok
    put = gw.puts[-1]
    assert (put["type"], put["path"], put["signature"], put["scope"]) == (SCRIPT_TYPE, "PDK/util", None, "A")
    assert put["files"] == {"code.py": CODE}


def test_deploy_refuses_a_style_class_with_errors(bundle: Bundle, tmp_path: Path) -> None:
    folder = _write_local(tmp_path, SANDBOX, resources.STYLE_CLASS, "PDK/Card", '{"base": {}, "extra": 1}')
    gw = FakeGateway()
    outcome = resources.deploy(gw, resources.STYLE_CLASS, folder, bundle, root=tmp_path)
    assert not outcome.ok and "Not deployed" in outcome.text
    assert gw.puts == []


def test_deploy_unchanged_since_pull_writes_nothing(bundle: Bundle, tmp_path: Path) -> None:
    gw = FakeGateway()
    folder = _pulled_card(gw, tmp_path)
    outcome = resources.deploy(gw, resources.STYLE_CLASS, folder, bundle, root=tmp_path)
    assert outcome.ok and "nothing to deploy" in outcome.text
    assert gw.puts == []


@pytest.mark.parametrize("kind, pulled, gateway_text, mine, copy_name", [
    (resources.STYLE_CLASS, CLASS_JSON, '{"base": {"style": {"color": "#555"}}}\n',
     '{"base": {"style": {"color": "#444"}}}\n', "style.gateway.json"),
    (resources.SCRIPT, CODE, CODE + "# changed in the Designer\n", CODE + "x = 2\n", "code.gateway.py"),
])
def test_deploy_409_writes_conflict_and_gateway_copy(bundle: Bundle, tmp_path: Path, kind: resources.Kind,
                                                     pulled: str, gateway_text: str, mine: str,
                                                     copy_name: str) -> None:
    gw = FakeGateway()
    gw.add(SANDBOX, kind.type, "PDK/Card", {kind.file: pulled})
    assert resources.pull(gw, kind, "PDK/Card", root=tmp_path).ok
    gw.add(SANDBOX, kind.type, "PDK/Card", {kind.file: gateway_text})  # the Designer changes it
    folder = tmp_path / SANDBOX / kind.folder / "PDK" / "Card"
    (folder / kind.file).write_text(mine, encoding="utf-8")
    outcome = resources.deploy(gw, kind, folder, bundle, root=tmp_path)
    assert not outcome.ok and "G008 conflict" in outcome.text
    sidecar = json.loads((folder / SIDECAR).read_text(encoding="utf-8"))
    assert sidecar["conflict"]["signature"] == gw.store[(SANDBOX, kind.type, "PDK/Card")]["signature"]
    assert (folder / copy_name).read_text(encoding="utf-8") == gateway_text


def test_a_create_conflict_is_recorded_so_resolved_can_overwrite(bundle: Bundle, tmp_path: Path) -> None:
    gw = FakeGateway()
    gw.add(SANDBOX, resources.SCRIPT.type, "PDK/util", {resources.SCRIPT.file: CODE})  # never pulled
    folder = _write_local(tmp_path, SANDBOX, resources.SCRIPT, "PDK/util", CODE + "x = 2\n")
    first = resources.deploy(gw, resources.SCRIPT, folder, bundle, root=tmp_path)
    assert not first.ok and "already exists" in first.text and "--resolved" in first.text
    second = resources.deploy(gw, resources.SCRIPT, folder, bundle, resolved=True, root=tmp_path)
    assert second.ok, second.text
    assert gw.store[(SANDBOX, resources.SCRIPT.type, "PDK/util")]["files"] == {resources.SCRIPT.file: CODE + "x = 2\n"}


def test_deploy_refuses_a_project_that_is_not_the_sandbox(bundle: Bundle, tmp_path: Path) -> None:
    folder = _write_local(tmp_path, TARGET, resources.STYLE_CLASS, "PDK/Card", CLASS_JSON)
    gw = FakeGateway()
    outcome = resources.deploy(gw, resources.STYLE_CLASS, folder, bundle, root=tmp_path)
    assert not outcome.ok and "deployed to the sandbox" in outcome.text
    assert gw.puts == []


# --- promote --------------------------------------------------------------------------------------


def test_promote_default_destination_drops_the_pdk_prefix(bundle: Bundle, tmp_path: Path) -> None:
    gw = FakeGateway()
    folder = _pulled_card(gw, tmp_path)
    outcome = resources.promote(gw, resources.STYLE_CLASS, folder, bundle, root=tmp_path)
    assert outcome.ok, outcome.text
    assert gw.store[(SANDBOX, STYLE, "Card")]["files"] == {"style.json": CLASS_JSON}


def test_promote_to_overrides_the_destination(bundle: Bundle, tmp_path: Path) -> None:
    gw = FakeGateway()
    folder = _pulled_card(gw, tmp_path)
    outcome = resources.promote(gw, resources.STYLE_CLASS, folder, bundle, to="Menus/Card", root=tmp_path)
    assert outcome.ok, outcome.text
    assert (SANDBOX, STYLE, "Menus/Card") in gw.store and (SANDBOX, STYLE, "Card") not in gw.store


def test_promote_refuses_a_destination_inside_pdk(bundle: Bundle, tmp_path: Path) -> None:
    gw = FakeGateway()
    folder = _pulled_card(gw, tmp_path)
    outcome = resources.promote(gw, resources.STYLE_CLASS, folder, bundle, to="PDK/Other", root=tmp_path)
    assert not outcome.ok and "outside it" in outcome.text
    assert gw.puts == []


def test_promote_leaves_scratch_folders_unless_told_where(bundle: Bundle, tmp_path: Path) -> None:
    gw = FakeGateway()
    gw.add(SANDBOX, STYLE, "PDK/_bench/Card", {"style.json": CLASS_JSON})
    assert resources.pull(gw, resources.STYLE_CLASS, "PDK/_bench/Card", root=tmp_path).ok
    folder = tmp_path / SANDBOX / resources.STYLE_CLASS.folder / "PDK" / "_bench" / "Card"
    refused = resources.promote(gw, resources.STYLE_CLASS, folder, bundle, root=tmp_path)
    assert not refused.ok and "scratch folder" in refused.text and gw.puts == []
    assert resources.promote(gw, resources.STYLE_CLASS, folder, bundle, to="Card", root=tmp_path).ok


def test_promote_refuses_local_edits(bundle: Bundle, tmp_path: Path) -> None:
    gw = FakeGateway()
    folder = _pulled_card(gw, tmp_path)
    (folder / "style.json").write_text('{"base": {"style": {"color": "#444"}}}\n', encoding="utf-8")
    outcome = resources.promote(gw, resources.STYLE_CLASS, folder, bundle, root=tmp_path)
    assert not outcome.ok and "has local edits" in outcome.text
    assert gw.puts == []


def test_promote_to_project_target_without_target_is_refused(bundle: Bundle, tmp_path: Path) -> None:
    gw = FakeGateway(target=None)
    folder = _pulled_card(gw, tmp_path)
    outcome = resources.promote(gw, resources.STYLE_CLASS, folder, bundle, to_project="target", root=tmp_path)
    assert not outcome.ok and "no target project" in outcome.text
    assert gw.puts == []


def test_promote_to_project_target_writes_the_target(bundle: Bundle, tmp_path: Path) -> None:
    gw = FakeGateway()
    folder = _pulled_card(gw, tmp_path)
    outcome = resources.promote(gw, resources.STYLE_CLASS, folder, bundle, to_project="target", root=tmp_path)
    assert outcome.ok, outcome.text
    assert (TARGET, STYLE, "Card") in gw.store and (SANDBOX, STYLE, "Card") not in gw.store


def test_promote_existing_destination_needs_resolved(bundle: Bundle, tmp_path: Path) -> None:
    gw = FakeGateway()
    folder = _pulled_card(gw, tmp_path)
    gw.add(SANDBOX, STYLE, "Card", {"style.json": '{"base": {"style": {"color": "#999"}}}\n'})
    outcome = resources.promote(gw, resources.STYLE_CLASS, folder, bundle, root=tmp_path)
    assert not outcome.ok and "already exists" in outcome.text
    assert gw.puts == []


def test_promote_existing_destination_with_resolved_overwrites(bundle: Bundle, tmp_path: Path) -> None:
    gw = FakeGateway()
    folder = _pulled_card(gw, tmp_path)
    gw.add(SANDBOX, STYLE, "Card", {"style.json": '{"base": {"style": {"color": "#999"}}}\n'})
    outcome = resources.promote(gw, resources.STYLE_CLASS, folder, bundle, resolved=True, root=tmp_path)
    assert outcome.ok, outcome.text
    assert gw.store[(SANDBOX, STYLE, "Card")]["files"] == {"style.json": CLASS_JSON}


# --- delete ---------------------------------------------------------------------------------------


def test_delete_needs_a_sidecar(bundle: Bundle, tmp_path: Path) -> None:
    folder = _write_local(tmp_path, SANDBOX, resources.STYLE_CLASS, "PDK/Card", CLASS_JSON)
    gw = FakeGateway()
    outcome = resources.delete(gw, resources.STYLE_CLASS, folder, root=tmp_path)
    assert not outcome.ok and SIDECAR in outcome.text
    assert gw.deletes == []


def test_delete_refuses_outside_the_sandbox(bundle: Bundle, tmp_path: Path) -> None:
    gw = FakeGateway()
    gw.add(TARGET, STYLE, "PDK/Card", {"style.json": CLASS_JSON})
    assert resources.pull(gw, resources.STYLE_CLASS, "PDK/Card", TARGET, root=tmp_path).ok
    folder = tmp_path / TARGET / "_style-classes" / "PDK" / "Card"
    outcome = resources.delete(gw, resources.STYLE_CLASS, folder, root=tmp_path)
    assert not outcome.ok and "is not the sandbox" in outcome.text
    assert gw.deletes == [] and (TARGET, STYLE, "PDK/Card") in gw.store


def test_delete_with_the_pulled_signature_removes_the_sidecar(bundle: Bundle, tmp_path: Path) -> None:
    gw = FakeGateway()
    folder = _pulled_card(gw, tmp_path)
    signature = gw.store[(SANDBOX, STYLE, "PDK/Card")]["signature"]
    outcome = resources.delete(gw, resources.STYLE_CLASS, folder, root=tmp_path)
    assert outcome.ok, outcome.text
    assert gw.deletes == [{"project": SANDBOX, "path": "PDK/Card", "signature": signature}]
    assert (SANDBOX, STYLE, "PDK/Card") not in gw.store
    assert not (folder / SIDECAR).exists() and (folder / "style.json").is_file()


# --- sync dispatch --------------------------------------------------------------------------------


@pytest.mark.parametrize("kind, body, text", [
    (resources.STYLE_CLASS, CLASS_JSON, "style-classes/PDK/X"),
    (resources.SCRIPT, CODE, "scripts/PDK/X"),
])
@pytest.mark.parametrize("as_folder", [False, True], ids=["text", "local-folder"])
def test_sync_deploy_dispatches_to_resources(bundle: Bundle, tmp_path: Path, kind: resources.Kind, body: str,
                                             text: str, as_folder: bool) -> None:
    folder = _write_local(tmp_path, SANDBOX, kind, "PDK/X", body)
    gw = FakeGateway()
    outcome = sync.deploy(gw, folder if as_folder else Path(text), bundle, root=tmp_path)
    assert outcome.ok, outcome.text
    put = gw.puts[-1]
    assert (put["type"], put["path"], put["scope"]) == (kind.type, "PDK/X", kind.scope)

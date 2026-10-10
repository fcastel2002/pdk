"""The project stylesheet: pull, deploy, 409 report, CSS check, promote to the target and the hook."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from pdk import sync
from pdk.gateway.client import GatewayError
from pdk.hook import run_hook
from pdk.stylesheet import CSS_FILE, FOLDER, GATEWAY_COPY, STYLESHEET, check_css
from pdk.sync import SIDECAR

PROJECT = "sandbox"
CSS = ".psc-a {\n  color: red;\n}\n"


class FakeGateway:
    """The stylesheet singleton of the module: signature checks and 409 with the current state."""

    url = "http://127.0.0.1:8088"

    def __init__(self, target: str | None = "final", text: str | None = CSS) -> None:
        self.text, self.target, self.writes = text, target, 0
        self.final: str | None = None  # the stylesheet of the target project

    def _swap(self, project: str) -> bool:
        """Serve the target project by swapping its text in; True if swapped (swap back after)."""
        if project == self.target and project != PROJECT:
            self.text, self.final = self.final, self.text
            return True
        if project != PROJECT:
            raise GatewayError("G006", "outside the sandbox", status=403)
        return False

    def health(self) -> dict[str, Any]:
        return {"sandbox": PROJECT, "writeEnabled": True, "target": self.target}

    def signature(self) -> str:
        return hashlib.sha256((self.text or "").encode()).hexdigest()

    def describe(self) -> dict[str, Any]:
        return {"path": "", "signature": self.signature(), "files": {CSS_FILE: self.text},
                "lastModification": {"actor": "admin", "timestamp": "2026-10-06T10:00:00Z"}}

    def resource(self, project: str, type_key: str, path: str = "") -> dict[str, Any]:
        assert type_key == STYLESHEET
        swapped = self._swap(project)
        try:
            if self.text is None:  # like the module: the root of a missing singleton is an empty listing
                return {"project": project, "type": type_key, "path": "", "folder": True, "signature": None,
                        "children": []}
            return self.describe()
        finally:
            if swapped:
                self._swap(project)

    def put_resource(self, project: str, type_key: str, path: str, files: dict[str, str],
                     signature: str | None = None, scope: str | None = None) -> dict[str, Any]:
        swapped = self._swap(project)
        try:
            exists = self.text is not None
            if exists != (signature is not None) or (exists and signature != self.signature()):
                raise GatewayError("G008", "not written", status=409,
                                   payload={"current": self.describe() if exists else None})
            self.text, self.writes = files[CSS_FILE], self.writes + 1
            described = self.describe()
            del described["files"]
            return {**described, "op": "created" if signature is None else "modified"}
        finally:
            if swapped:
                self._swap(project)

    def delete_resource(self, project: str, type_key: str, path: str, signature: str) -> dict[str, Any]:
        if signature != self.signature():
            raise GatewayError("G008", "not deleted", status=409, payload={"current": self.describe()})
        self.text = None
        return {"op": "deleted"}


@pytest.fixture
def gw() -> FakeGateway:
    return FakeGateway()


def _folder(root: Path) -> Path:
    return root / PROJECT / FOLDER


def _edit(root: Path, text: str) -> None:
    (_folder(root) / CSS_FILE).write_text(text, encoding="utf-8")


def test_pull_writes_file_and_sidecar(gw: FakeGateway, tmp_path: Path) -> None:
    outcome = sync.pull(gw, "stylesheet", root=tmp_path)
    assert outcome.ok and "pulled sandbox:stylesheet" in outcome.text
    assert (_folder(tmp_path) / CSS_FILE).read_text(encoding="utf-8") == CSS
    sidecar = json.loads((_folder(tmp_path) / SIDECAR).read_text(encoding="utf-8"))
    assert sidecar["type"] == STYLESHEET and sidecar["path"] == "" and sidecar["signature"] == gw.signature()
    assert sidecar["base"] == {CSS_FILE: CSS}


def test_pull_refuses_to_overwrite_local_edits(gw: FakeGateway, tmp_path: Path) -> None:
    sync.pull(gw, "stylesheet", root=tmp_path)
    _edit(tmp_path, ".mine {}\n")
    outcome = sync.pull(gw, "stylesheet", root=tmp_path)
    assert not outcome.ok and "local edits" in outcome.text
    assert sync.pull(gw, "stylesheet", force=True, root=tmp_path).ok
    assert (_folder(tmp_path) / CSS_FILE).read_text(encoding="utf-8") == CSS


def test_pull_when_missing_writes_nothing(tmp_path: Path) -> None:
    outcome = sync.pull(FakeGateway(text=None), "stylesheet", root=tmp_path)
    assert outcome.ok and "does not exist yet" in outcome.text
    assert not _folder(tmp_path).exists()


def test_deploy_sends_the_pulled_signature(gw: FakeGateway, tmp_path: Path, bundle) -> None:
    sync.pull(gw, "stylesheet", root=tmp_path)
    unchanged = sync.deploy(gw, _folder(tmp_path), bundle, root=tmp_path)
    assert unchanged.ok and "nothing to deploy" in unchanged.text and gw.writes == 0
    _edit(tmp_path, ".psc-a { color: blue; }\n")
    outcome = sync.deploy(gw, _folder(tmp_path) / CSS_FILE, bundle, root=tmp_path)
    assert outcome.ok and outcome.text.startswith("modified sandbox:stylesheet") and "reload" in outcome.text
    assert gw.text == ".psc-a { color: blue; }\n"
    sidecar = json.loads((_folder(tmp_path) / SIDECAR).read_text(encoding="utf-8"))
    assert sidecar["signature"] == gw.signature()


def test_deploy_creates_when_missing(tmp_path: Path, bundle) -> None:
    gw = FakeGateway(text=None)
    _folder(tmp_path).mkdir(parents=True)
    _edit(tmp_path, CSS)
    outcome = sync.deploy(gw, _folder(tmp_path), bundle, root=tmp_path)
    assert outcome.ok and outcome.text.startswith("created sandbox:stylesheet new ->") and gw.text == CSS


def test_conflict_reports_diff_and_resolved_overwrites(gw: FakeGateway, tmp_path: Path, bundle) -> None:
    sync.pull(gw, "stylesheet", root=tmp_path)
    gw.text = CSS + ".theirs { margin: 0; }\n"
    _edit(tmp_path, CSS + ".mine { padding: 0; }\n")
    outcome = sync.deploy(gw, _folder(tmp_path), bundle, root=tmp_path)
    assert not outcome.ok and "G008 conflict" in outcome.text
    assert "+.theirs { margin: 0; }" in outcome.text and "+.mine { padding: 0; }" in outcome.text
    assert "--resolved" in outcome.text and "pull stylesheet --force" in outcome.text
    assert (_folder(tmp_path) / GATEWAY_COPY).read_text(encoding="utf-8") == gw.text
    assert gw.writes == 0

    done = sync.deploy(gw, _folder(tmp_path), bundle, resolved=True, root=tmp_path)
    assert done.ok and gw.text == CSS + ".mine { padding: 0; }\n"
    assert not (_folder(tmp_path) / GATEWAY_COPY).exists()


@pytest.mark.parametrize("text, expected", [
    (CSS, []),
    (".a { color: red;\n", "line 1: '{' is never closed"),
    (".a { }\n}\n", "line 2: unmatched '}'"),
    (".a { }\n/* open\n.b { }\n", "line 2: unterminated comment"),
    (".a { content: 'x;\n}\n", "line 1: unterminated string"),
    (".a::after { content: \"}{\"; } /* } */\n", []),
])
def test_check_css(text: str, expected) -> None:
    problems = check_css(text)
    assert problems == expected if expected == [] else expected in problems


def test_broken_css_blocks_deploy(gw: FakeGateway, tmp_path: Path, bundle) -> None:
    sync.pull(gw, "stylesheet", root=tmp_path)
    _edit(tmp_path, ".a { color: red;\n")
    outcome = sync.deploy(gw, _folder(tmp_path), bundle, root=tmp_path)
    assert not outcome.ok and "never closed" in outcome.text and "Not deployed" in outcome.text and gw.writes == 0


def test_target_project_takes_it_only_through_promote(gw: FakeGateway, tmp_path: Path, bundle) -> None:
    directory = tmp_path / "final" / FOLDER
    directory.mkdir(parents=True)
    (directory / CSS_FILE).write_text(CSS, encoding="utf-8")
    outcome = sync.deploy(gw, directory, bundle, root=tmp_path)
    assert not outcome.ok and "the stylesheet is edited in the sandbox only" in outcome.text and gw.writes == 0


def test_promote_copies_the_deployed_stylesheet_to_the_target(gw: FakeGateway, tmp_path: Path, bundle) -> None:
    sync.pull(gw, "stylesheet", root=tmp_path)
    assert "--to-project" in sync.promote(gw, _folder(tmp_path), bundle, root=tmp_path).text
    _edit(tmp_path, ".psc-b { color: blue; }\n")
    edited = sync.promote(gw, _folder(tmp_path), bundle, to_project="target", root=tmp_path)
    assert not edited.ok and "local edits" in edited.text
    sync.deploy(gw, _folder(tmp_path), bundle, root=tmp_path)
    outcome = sync.promote(gw, _folder(tmp_path), bundle, to_project="target", root=tmp_path)
    assert outcome.ok and outcome.text.startswith("promote sandbox:stylesheet -> final: created final:stylesheet")
    assert gw.final == ".psc-b { color: blue; }\n"
    again = sync.promote(gw, _folder(tmp_path), bundle, to_project="target", root=tmp_path)
    assert again.ok and "nothing to deploy" in again.text
    gw.final = ".psc-designer {}\n"  # someone edits it in the target
    _edit(tmp_path, ".psc-c {}\n")
    sync.deploy(gw, _folder(tmp_path), bundle, root=tmp_path)
    conflict = sync.promote(gw, _folder(tmp_path), bundle, to_project="target", root=tmp_path)
    assert not conflict.ok and "G008" in conflict.text and gw.final == ".psc-designer {}\n"
    assert not sync.promote(FakeGateway(target=None), _folder(tmp_path), bundle, to_project="target",
                            root=tmp_path).ok


def test_delete_is_signed(gw: FakeGateway, tmp_path: Path) -> None:
    sync.pull(gw, "stylesheet", root=tmp_path)
    outcome = sync.delete(gw, _folder(tmp_path), root=tmp_path)
    assert outcome.ok and gw.text is None and not (_folder(tmp_path) / SIDECAR).exists()


def test_hook_reports_a_broken_stylesheet(tmp_path: Path) -> None:
    path = tmp_path / CSS_FILE
    path.write_text(".a { color: red;\n", encoding="utf-8")
    code, message = run_hook(json.dumps({"tool_input": {"file_path": str(path)}}))
    assert code == 2 and "line 1" in message and "never closed" in message
    path.write_text(CSS, encoding="utf-8")
    assert run_hook(json.dumps({"tool_input": {"file_path": str(path)}})) == (0, "")

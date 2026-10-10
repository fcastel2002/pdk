from __future__ import annotations

import json

import pytest

from pdk import hook
from pdk.bundle import BundleError


@pytest.fixture
def no_bundle(monkeypatch: pytest.MonkeyPatch) -> None:
    # A missing bundle makes run_hook return exit 1 once it reaches validation: the code tells whether a file was picked
    def fail(*args: object, **kwargs: object) -> None:
        raise BundleError("stub bundle")

    monkeypatch.setattr(hook, "load_bundle", fail)


def _event(path: str) -> str:
    return json.dumps({"tool_name": "Write", "tool_input": {"file_path": path}})


@pytest.mark.parametrize("name", ["overview.json", "preview.json"])
def test_hook_ignores_files_that_only_end_with_view_json(tmp_path, no_bundle, name):
    path = tmp_path / name
    path.write_text("{}", encoding="utf-8")
    assert hook.run_hook(_event(str(path))) == (0, "")


def test_hook_picks_view_json(tmp_path, no_bundle):
    path = tmp_path / "view.json"
    path.write_text("{}", encoding="utf-8")
    code, message = hook.run_hook(_event(str(path)))
    assert code == 1
    assert "stub bundle" in message

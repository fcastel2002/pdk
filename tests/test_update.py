from __future__ import annotations

import json

import pytest

from pdk import update


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("PDK_HOME", str(tmp_path))
    monkeypatch.setenv("PDK_UPDATE_CHECK", "1")
    monkeypatch.delenv("CI", raising=False)
    return tmp_path


def test_a_version_between_releases_sorts_before_the_release() -> None:
    assert update.version_key("0.2.0") < update.version_key("0.2.1.dev3+g1a2b3c4") < update.version_key("0.2.1")
    assert update.version_key("v0.10.0") > update.version_key("0.9.9")


def test_the_check_is_cached_for_a_day(home, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(update, "_fetch", lambda timeout: calls.append(1) or "0.3.0")
    assert update.latest(now=1000.0) == "0.3.0"
    assert update.latest(now=1000.0 + update.INTERVAL_S - 1) == "0.3.0"
    assert len(calls) == 1
    update.latest(now=1000.0 + update.INTERVAL_S + 1)
    assert len(calls) == 2


def test_an_unreachable_github_is_also_cached(home, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(update, "_fetch", lambda timeout: calls.append(1) or None)
    assert update.latest(now=5.0) is None
    assert update.latest(now=6.0) is None
    assert len(calls) == 1
    assert json.loads((home / "update-check.json").read_text())["latest"] is None


def test_notice_only_when_newer(home, monkeypatch) -> None:
    monkeypatch.setattr(update, "_fetch", lambda timeout: "0.3.0")
    assert "PDK 0.3.0 is available (this is 0.2.0)" in update.notice("0.2.0")
    assert update.notice("0.3.0") is None
    assert update.notice("0.3.1.dev1") is None


def test_the_final_edition_says_it_is_frozen_once_a_day(home, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(update, "_fetch", lambda timeout: calls.append(1) or "0.5.0")
    assert update.notice("0.5.0", now=1000.0) == update.FROZEN
    assert update.notice("0.5.0", now=1000.0 + 60) is None
    assert update.MAINTAINED_URL in update.notice("0.5.0", now=1000.0 + update.INTERVAL_S + 1)
    assert len(calls) == 2  # no call of its own: one a day, as before


@pytest.mark.parametrize("value", ["0", "false", "off"])
def test_the_check_can_be_turned_off(home, monkeypatch, value) -> None:
    monkeypatch.setenv("PDK_UPDATE_CHECK", value)
    monkeypatch.setattr(update, "_fetch", lambda timeout: pytest.fail("no network call when off"))
    assert update.notice("0.0.1") is None


def test_off_in_ci(home, monkeypatch) -> None:
    monkeypatch.setenv("CI", "true")
    assert not update.enabled()

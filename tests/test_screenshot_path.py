"""Default output of `pdk screenshot`: the file name from the view path and component, and the folder under work/."""

from __future__ import annotations

from pathlib import Path

import pytest

from pdk import REPO_ROOT, cli
from pdk.cli import default_screenshot, main, screenshot_name

PNG = b"\x89PNG\r\n\x1a\n"


@pytest.mark.parametrize(
    ("view", "component", "expected"),
    [
        ("PDK/_bench/r3/Nav", "root/Panel", "PDK___bench__r3__Nav__root_Panel"),
        ("PDK/Pump", None, "PDK__Pump"),
        ("/PDK/Pump/", "", "PDK__Pump"),
        ("/PDK/Pump/", "/root/Panel/", "PDK__Pump__root_Panel"),
        ("PDK/Pump", "C.0:1", "PDK__Pump__C.0_1"),
    ],
)
def test_screenshot_name(view: str, component: str | None, expected: str) -> None:
    assert screenshot_name(view, component) == expected


def test_screenshot_name_replaces_characters_file_names_cannot_hold() -> None:
    name = screenshot_name('PDK/Pump:x*?"<>|', None)
    assert name == "PDK__Pump_x______"
    assert not set(name) & set('\\/:*?"<>|')
    assert screenshot_name("D:\\views\\Pump") == "D__views_Pump"


def test_default_screenshot_lives_under_work_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PDK_WORK_DIR", str(tmp_path / "work"))
    path = default_screenshot("PDK/Pump", "root/Panel")
    assert path == (tmp_path / "work" / "_screenshots" / "PDK__Pump__root_Panel.png").resolve()
    assert path.is_absolute()


def test_default_screenshot_falls_back_to_repo_work(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PDK_WORK_DIR", raising=False)
    assert default_screenshot("PDK/Pump") == (REPO_ROOT / "work" / "_screenshots" / "PDK__Pump.png").resolve()


class _FakeEyes:
    def __enter__(self) -> _FakeEyes:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def _fake_shot(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replaces the browser and the gateway so that `pdk screenshot` runs without Chromium or a gateway."""
    from pdk.eyes import browser, inspect
    from pdk.gateway import client

    monkeypatch.setattr(browser, "Eyes", _FakeEyes)
    monkeypatch.setattr(client, "Gateway", lambda *a, **k: None)
    monkeypatch.setattr(inspect, "screenshot", lambda *a, **k: (PNG, "shot of PDK/Pump"))


def test_screenshot_without_out_writes_default_and_prints_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PDK_WORK_DIR", str(tmp_path / "work"))
    _fake_shot(monkeypatch)
    assert main(["screenshot", "PDK/Pump", "--component", "root/Panel"]) == cli.EXIT_OK
    expected = (tmp_path / "work" / "_screenshots" / "PDK__Pump__root_Panel.png").resolve()
    assert expected.read_bytes() == PNG
    assert str(expected) in capsys.readouterr().out


def test_screenshot_explicit_out_is_kept(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PDK_WORK_DIR", str(tmp_path / "work"))
    _fake_shot(monkeypatch)
    target = tmp_path / "elsewhere" / "pump.png"
    assert main(["screenshot", "PDK/Pump", "--out", str(target)]) == cli.EXIT_OK
    assert target.read_bytes() == PNG
    assert not (tmp_path / "work" / "_screenshots").exists()

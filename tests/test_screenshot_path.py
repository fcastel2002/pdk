"""Default output of `pdk screenshot`: the file name from the view path and component, and the folder under work/."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

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
    def __init__(self, **options: object) -> None:
        self.options = options

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


VIEW = {"root": {"meta": {"name": "root"}, "type": "ia.container.flex",
                 "children": [{"meta": {"name": "Save"}, "type": "ia.input.button"}]}}


class _ActEyes:
    """Eyes stand-in: records the order of open, act and screenshot; act answers `outcome`, or every step done."""

    def __init__(self, outcome: Any = None) -> None:
        self.calls: list[str] = []
        self.outcome = outcome
        self.steps: list[Any] = []

    def open(self, url: str, viewport: Any) -> SimpleNamespace:
        self.calls.append("open")
        return SimpleNamespace(viewport="1366x768", page_id="p1")

    def act(self, opened: Any, steps: list[Any], pointers: dict[str, str], resolve: Any, tags: Any) -> Any:
        from pdk.eyes import act as actions

        self.calls.append("act")
        self.steps = steps
        return self.outcome if self.outcome is not None else actions.Outcome(len(steps), len(steps), [])

    def screenshot(self, opened: Any, dom_path: str | None) -> bytes:
        self.calls.append(f"screenshot {dom_path}")
        return PNG


def _stub_view(monkeypatch: pytest.MonkeyPatch, popup: Any = None) -> None:
    """Replaces the view lookup, the preview URL and the popup resolver, so that screenshot runs on _ActEyes alone."""
    from pdk.eyes import inspect

    target = inspect.Target("PDK", "Pump", VIEW, None)
    monkeypatch.setattr(inspect, "resolve", lambda gateway, location: target)
    monkeypatch.setattr(inspect, "_preview", lambda gateway, view_target: "http://gateway/preview")
    monkeypatch.setattr(inspect, "_popup_resolver", lambda gateway, project, opened: popup or (lambda name: None))


def test_screenshot_act_runs_steps_before_capture(monkeypatch: pytest.MonkeyPatch) -> None:
    from pdk.eyes import inspect

    _stub_view(monkeypatch)
    eyes = _ActEyes()
    png, text = inspect.screenshot(eyes, None, "PDK/Pump", None, None, "click root/Save; wait 100")
    assert png == PNG
    assert eyes.calls == ["open", "act", "screenshot None"]
    assert [step.verb for step in eyes.steps] == ["click", "wait"]
    assert "act: 2/2 done" in text


def test_screenshot_failing_act_step_gives_a004_and_no_png(monkeypatch: pytest.MonkeyPatch) -> None:
    from pdk.diagnostics import make
    from pdk.eyes import act as actions
    from pdk.eyes import inspect

    _stub_view(monkeypatch)
    failed = actions.Outcome(1, 0, [make("A004", "error", "", "root/Save",
                                         "step 1 `click root/Save` failed: no element", "check the component")])
    eyes = _ActEyes(failed)
    png, text = inspect.screenshot(eyes, None, "PDK/Pump", None, None, "click root/Save")
    assert png is None
    assert "A004" in text
    assert text.endswith("act: 0/1 done")
    assert eyes.calls == ["open", "act"]


def test_screenshot_malformed_act_is_a005_and_opens_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    from pdk.eyes import inspect

    _stub_view(monkeypatch)
    eyes = _ActEyes()
    png, text = inspect.screenshot(eyes, None, "PDK/Pump", None, None, "jump root/Save")
    assert png is None
    assert "A005" in text
    assert eyes.calls == []


def test_screenshot_popup_component_is_resolved_after_act(monkeypatch: pytest.MonkeyPatch) -> None:
    from pdk.eyes import inspect

    _stub_view(monkeypatch, lambda name: "P1.0:1" if name == "popup:1/root/Panel" else None)
    eyes = _ActEyes()
    png, text = inspect.screenshot(eyes, None, "PDK/Pump", None, "popup:1/root/Panel", "click root/Save")
    assert png == PNG
    assert eyes.calls == ["open", "act", "screenshot P1.0:1"]
    assert "popup:1/root/Panel (P1.0:1)" in text


def test_screenshot_popup_component_refused_when_no_popup_is_open(monkeypatch: pytest.MonkeyPatch) -> None:
    from pdk.eyes import inspect

    _stub_view(monkeypatch)
    eyes = _ActEyes()
    png, text = inspect.screenshot(eyes, None, "PDK/Pump", None, "popup:1/root/Panel")
    assert png is None
    assert "in an open popup" in text
    assert eyes.calls == ["open"]


def test_screenshot_cli_passes_component_and_act_on(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from pdk.eyes import browser, inspect
    from pdk.gateway import client

    seen: list[tuple[Any, ...]] = []

    def shot(*args: Any) -> tuple[bytes, str]:
        seen.append(args)
        return PNG, "shot of PDK/Pump"

    monkeypatch.setenv("PDK_WORK_DIR", str(tmp_path / "work"))
    monkeypatch.setattr(browser, "Eyes", _FakeEyes)
    monkeypatch.setattr(client, "Gateway", lambda *a, **k: None)
    monkeypatch.setattr(inspect, "screenshot", shot)
    argv = ["screenshot", "PDK/Pump", "--component", "popup:1/root/Panel", "--act", "click root/Open"]
    assert main(argv) == cli.EXIT_OK
    assert seen[0][-2:] == ("popup:1/root/Panel", "click root/Open")


class _SessionGateway:
    """The two gateway calls `session_closer` makes: health() for the sandbox, close_session() per page."""

    def __init__(self, fail_on: str | None = None, health_error: bool = False) -> None:
        self.fail_on, self.health_error = fail_on, health_error
        self.closed: list[tuple[str, str]] = []

    def health(self) -> dict[str, str]:
        if self.health_error:
            from pdk.gateway.client import GatewayError

            raise GatewayError("G007", "x")
        return {"sandbox": "sb"}

    def close_session(self, project: str, page_id: str) -> None:
        self.closed.append((project, page_id))
        if page_id == self.fail_on:
            from pdk.gateway.client import GatewayError

            raise GatewayError("G007", "x")


def test_session_closer_closes_each_page_on_the_sandbox() -> None:
    from pdk.eyes.inspect import session_closer

    gateway = _SessionGateway()
    session_closer(gateway)(["a", "b"])
    assert gateway.closed == [("sb", "a"), ("sb", "b")]


def test_session_closer_keeps_going_after_a_gateway_error() -> None:
    from pdk.eyes.inspect import session_closer

    gateway = _SessionGateway(fail_on="a")
    session_closer(gateway)(["a", "b"])
    assert gateway.closed == [("sb", "a"), ("sb", "b")]


def test_session_closer_does_nothing_when_the_sandbox_is_unknown() -> None:
    from pdk.eyes.inspect import session_closer

    gateway = _SessionGateway(health_error=True)
    session_closer(gateway)(["a", "b"])
    assert gateway.closed == []

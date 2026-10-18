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


# -- visual comparison (--compare / compare=): the naming of changed regions, the outcomes and the two entry points

DIFF = b"\x89PNG\r\n\x1a\ndiff"
NAMED_VIEW = {"root": {"meta": {"name": "root"}, "type": "ia.container.flex", "children": [
    {"meta": {"name": "Panel"}, "type": "ia.container.flex", "children": [
        {"meta": {"name": "Label"}, "type": "ia.display.label"}]},
    {"meta": {"name": "Footer"}, "type": "ia.display.label"}]}}


def _box(path: str, left: float, top: float, width: float, height: float) -> dict[str, Any]:
    bounds = {"left": left, "top": top, "right": left + width, "bottom": top + height, "width": width, "height": height}
    return {"path": path, "bounds": bounds}


def _region(x: int, y: int, width: int, height: int, pixels: int = 1) -> dict[str, int]:
    return {"x": x, "y": y, "width": width, "height": height, "pixels": pixels}


def _answer(regions: list[dict[str, int]], changed: int = 168, total: int = 120000) -> dict[str, Any]:
    """What Eyes.compare answers for a 400x300 capture."""
    return {"width": 400, "height": 300, "changed": changed, "total": total, "region_count": len(regions),
            "regions": regions, "diff_png": DIFF}


class _CompareEyes(_ActEyes):
    """_ActEyes that also compares: `result` is what Eyes.compare answers and `nodes` what extract reports."""

    def __init__(self, result: dict[str, Any], nodes: list[dict[str, Any]] | None = None) -> None:
        super().__init__()
        self.result = result
        self.nodes = nodes or []
        self.extract_roots: list[str] = []

    def screenshot(self, opened: Any, dom_path: str | None) -> bytes:
        self.calls.append(f"screenshot {dom_path}")
        return PNG

    def compare(self, opened: Any, before: bytes, after: bytes) -> dict[str, Any]:
        self.calls.append("compare")
        return self.result

    def extract(self, opened: Any, root: str = "C.0") -> dict[str, Any]:
        self.calls.append("extract")
        self.extract_roots.append(root)
        return {"nodes": self.nodes}


def _stub_target(monkeypatch: pytest.MonkeyPatch, view: dict[str, Any]) -> None:
    from pdk.eyes import inspect

    target = inspect.Target("PDK", "Pump", view, None)
    monkeypatch.setattr(inspect, "resolve", lambda gateway, location: target)
    monkeypatch.setattr(inspect, "_preview", lambda gateway, view_target: "http://gateway/preview")
    monkeypatch.setattr(inspect, "_popup_resolver", lambda gateway, project, opened: lambda name: None)


def test_each_region_is_named_by_the_smallest_component_under_its_centre(monkeypatch: pytest.MonkeyPatch) -> None:
    from pdk.eyes import inspect

    _stub_target(monkeypatch, NAMED_VIEW)
    nodes = [_box("C.0", 0, 0, 400, 300), _box("C.0:0", 10, 10, 200, 100), _box("C.0:0:0", 20, 20, 60, 40)]
    regions = [_region(30, 30, 10, 10, 100), _region(150, 60, 10, 10, 50), _region(300, 200, 5, 5, 4),
               _region(500, 500, 2, 2)]
    eyes = _CompareEyes(_answer(regions), nodes)
    png, diff, text = inspect.compare_screenshot(eyes, None, "PDK/Pump", b"before")
    assert (png, diff) == (PNG, DIFF)
    assert text.splitlines()[-1] == ("compare: 0.1% of pixels differ in 4 region(s): root/Panel/Label 10x10 @30,30; "
                                     "root/Panel 10x10 @150,60; root 5x5 @300,200; (no component) 2x2 @500,500")
    assert eyes.extract_roots == ["C.0"]


def test_a_region_inside_an_embedded_view_is_named_by_its_host(monkeypatch: pytest.MonkeyPatch) -> None:
    from pdk.eyes import inspect

    _stub_target(monkeypatch, NAMED_VIEW)
    nodes = [_box("C.0", 0, 0, 400, 300), _box("C.0:1$C.0:0", 300, 200, 50, 50)]
    eyes = _CompareEyes(_answer([_region(310, 210, 10, 10)]), nodes)
    _png, _diff, text = inspect.compare_screenshot(eyes, None, "PDK/Pump", b"before")
    assert text.splitlines()[-1].endswith("root/Footer 10x10 @310,210")


def test_dom_paths_map_to_name_paths_and_unknown_ones_stay_as_they_are() -> None:
    from pdk.eyes import inspect

    names = {"0": "root", "0:0": "root/Panel"}
    assert inspect._component_name("C.0:0", names) == "root/Panel"
    assert inspect._component_name("C.0:0$C.0:2:1", names) == "root/Panel"
    assert inspect._component_name("C.0:7$C.0:0", names) == "C.0:7$C.0:0"
    assert inspect._component_name("P1.0:1", names) == "P1.0:1"


def test_a_component_capture_adds_the_component_offset_back(monkeypatch: pytest.MonkeyPatch) -> None:
    from pdk.eyes import inspect

    _stub_target(monkeypatch, NAMED_VIEW)
    nodes = [_box("C.0", 0, 0, 400, 300), _box("C.0:0", 100, 50, 200, 200), _box("C.0:0:0", 120, 70, 60, 40)]
    # the region is 40,30 inside the Panel's box: its centre is at 150,90 on the page, inside the Label
    eyes = _CompareEyes(_answer([_region(40, 30, 20, 20, 9)]), nodes)
    _png, _diff, text = inspect.compare_screenshot(eyes, None, "PDK/Pump", b"before", None, "root/Panel")
    assert "screenshot C.0:0" in eyes.calls
    assert text.splitlines()[-1].endswith("root/Panel/Label 20x20 @40,30")


def test_identical_capture_has_no_diff_and_no_extraction(monkeypatch: pytest.MonkeyPatch) -> None:
    from pdk.eyes import inspect

    _stub_target(monkeypatch, NAMED_VIEW)
    eyes = _CompareEyes(_answer([], changed=0))
    png, diff, text = inspect.compare_screenshot(eyes, None, "PDK/Pump", b"before")
    assert (png, diff) == (PNG, None)
    assert text.endswith("\ncompare: identical")
    assert "extract" not in eyes.calls


def test_sizes_that_differ_are_reported_without_regions(monkeypatch: pytest.MonkeyPatch) -> None:
    from pdk.eyes import inspect

    _stub_target(monkeypatch, NAMED_VIEW)
    eyes = _CompareEyes({"size_before": [400, 300], "size_after": [420, 300]})
    png, diff, text = inspect.compare_screenshot(eyes, None, "PDK/Pump", b"before")
    assert (png, diff) == (PNG, None)
    assert text.endswith("\ncompare: sizes differ (before 400x300, now 420x300)")
    assert "extract" not in eyes.calls


def test_compare_line_gives_the_share_and_lists_at_most_eight_regions(monkeypatch: pytest.MonkeyPatch) -> None:
    from pdk.eyes import inspect

    _stub_target(monkeypatch, NAMED_VIEW)
    regions = [_region(i * 10, 0, 2, 2) for i in range(10)]
    eyes = _CompareEyes(_answer(regions, changed=12000), [_box("C.0", 0, 0, 400, 300)])
    _png, _diff, text = inspect.compare_screenshot(eyes, None, "PDK/Pump", b"before")
    listed = "; ".join(f"root 2x2 @{i * 10},0" for i in range(8))
    assert text.splitlines()[-1] == f"compare: 10.0% of pixels differ in 10 region(s): {listed}; +2 more"


def test_screenshot_with_compare_keeps_the_pair_and_adds_the_line(monkeypatch: pytest.MonkeyPatch) -> None:
    from pdk.eyes import inspect

    _stub_target(monkeypatch, NAMED_VIEW)
    png, text = inspect.screenshot(_CompareEyes(_answer([], changed=0)), None, "PDK/Pump", compare=b"before")
    assert png == PNG
    assert text.endswith("\ncompare: identical")


def _fake_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    from pdk.eyes import browser
    from pdk.gateway import client

    monkeypatch.setattr(browser, "Eyes", _FakeEyes)
    monkeypatch.setattr(client, "Gateway", lambda *a, **k: None)


def _fake_compare(monkeypatch: pytest.MonkeyPatch, answer: tuple[bytes | None, bytes | None, str]) -> list[Any]:
    """Replaces compare_screenshot; the calls it gets are recorded as (location, before, (viewport, component, act))."""
    from pdk.eyes import inspect

    calls: list[Any] = []

    def compare_screenshot(eyes: Any, gateway: Any, location: str, before: bytes, *rest: Any) -> Any:
        calls.append((location, before, rest))
        return answer

    monkeypatch.setattr(inspect, "compare_screenshot", compare_screenshot)
    return calls


def test_screenshot_cli_writes_the_diff_beside_the_out_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _fake_browser(monkeypatch)
    before = tmp_path / "before.png"
    before.write_bytes(b"before-bytes")
    calls = _fake_compare(monkeypatch, (PNG, DIFF, "compare: 0.1% of pixels differ in 1 region(s): root 2x2 @0,0"))
    out = tmp_path / "shots" / "pump.png"
    assert main(["screenshot", "PDK/Pump", "--compare", str(before), "--out", str(out)]) == cli.EXIT_OK
    assert out.read_bytes() == PNG
    diff_out = tmp_path / "shots" / "pump__diff.png"
    assert diff_out.read_bytes() == DIFF
    assert calls == [("PDK/Pump", b"before-bytes", (None, None, None))]
    printed = capsys.readouterr().out
    assert "compare: 0.1%" in printed and str(out) in printed and f"diff -> {diff_out}" in printed


def test_screenshot_cli_without_a_diff_writes_only_the_capture(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _fake_browser(monkeypatch)
    before = tmp_path / "before.png"
    before.write_bytes(b"before-bytes")
    _fake_compare(monkeypatch, (PNG, None, "compare: identical"))
    out = tmp_path / "pump.png"
    assert main(["screenshot", "PDK/Pump", "--compare", str(before), "--out", str(out)]) == cli.EXIT_OK
    assert sorted(path.name for path in tmp_path.iterdir()) == ["before.png", "pump.png"]


def test_screenshot_cli_names_a_missing_compare_file_and_opens_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _fake_browser(monkeypatch)
    calls = _fake_compare(monkeypatch, (PNG, None, "unused"))
    assert main(["screenshot", "PDK/Pump", "--compare", str(tmp_path / "missing.png")]) == cli.EXIT_USAGE
    assert calls == []
    assert "cannot read" in capsys.readouterr().err


def _mcp_compare(monkeypatch: pytest.MonkeyPatch, answer: tuple[bytes | None, bytes | None, str]) -> list[Any]:
    """Replaces the shared browser, the gateway and compare_screenshot; the calls are recorded as
    (eyes, location, before, (viewport, component, act))."""
    from pdk import mcp_server
    from pdk.eyes import inspect

    calls: list[Any] = []

    def compare_screenshot(eyes: Any, gateway: Any, location: str, before: bytes, *rest: Any) -> Any:
        calls.append((eyes, location, before, rest))
        return answer

    monkeypatch.setattr(mcp_server, "_shared_eyes", lambda: "eyes")
    monkeypatch.setattr(mcp_server, "Gateway", lambda: None)
    monkeypatch.setattr(inspect, "compare_screenshot", compare_screenshot)
    return calls


def test_mcp_screenshot_with_compare_returns_the_capture_and_the_diff(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from mcp.server.mcpserver import Image

    from pdk import mcp_server

    before = tmp_path / "before.png"
    before.write_bytes(b"before-bytes")
    calls = _mcp_compare(monkeypatch, (PNG, DIFF, "compare: 0.1% of pixels differ in 1 region(s): root 2x2 @0,0"))
    result = mcp_server.pdk_screenshot("PDK/Pump", compare=str(before))
    assert [type(item) for item in result] == [str, Image, Image]
    assert result[0].startswith("compare: 0.1%")
    assert result[1].data == PNG and result[2].data == DIFF
    assert calls == [("eyes", "PDK/Pump", b"before-bytes", (None, None, None))]


def test_mcp_screenshot_without_a_diff_returns_one_image(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from pdk import mcp_server

    before = tmp_path / "before.png"
    before.write_bytes(b"before-bytes")
    _mcp_compare(monkeypatch, (PNG, None, "compare: identical"))
    result = mcp_server.pdk_screenshot("PDK/Pump", compare=str(before))
    assert len(result) == 2 and result[0] == "compare: identical"


def test_mcp_screenshot_names_an_unreadable_compare_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from pdk import mcp_server

    calls = _mcp_compare(monkeypatch, (PNG, None, "unused"))
    result = mcp_server.pdk_screenshot("PDK/Pump", compare=str(tmp_path / "missing.png"))
    assert result.startswith("cannot read the compare PNG") and calls == []


def test_session_closer_tries_the_target_for_a_page_the_sandbox_does_not_have() -> None:
    from pdk.eyes.inspect import session_closer

    gateway = _SessionGateway(fail_on="a")
    gateway.health = lambda: {"sandbox": "sb", "target": "tg"}  # type: ignore[method-assign]
    session_closer(gateway)(["a", "b"])
    assert gateway.closed == [("sb", "a"), ("tg", "a"), ("sb", "b")]


@pytest.mark.parametrize(("project", "opens"), [("sb", True), ("tg", True), ("Other", False)])
def test_preview_pages_exist_in_the_sandbox_and_the_target_only(monkeypatch: pytest.MonkeyPatch, project: str,
                                                                 opens: bool) -> None:
    from pdk import sync
    from pdk.eyes import inspect

    class Health:
        url = "http://gw"

        def health(self) -> dict[str, str]:
            return {"sandbox": "sb", "target": "tg"}

    added: list[tuple[str, str]] = []
    monkeypatch.setattr(sync, "ensure_preview", lambda gateway, name, path: added.append((name, path)))
    target = inspect.Target(project, "Page/Home", {}, None)
    if opens:
        assert inspect._preview(Health(), target) == f"http://gw/data/perspective/client/{project}/pdk/Page/Home"
        assert added == [(project, "Page/Home")]
    else:
        with pytest.raises(ValueError, match="only views of the sandbox"):
            inspect._preview(Health(), target)
        assert added == []

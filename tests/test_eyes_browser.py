"""The pure parts of pdk.eyes.browser; the browser itself is exercised by `pytest -m gateway`."""

from __future__ import annotations

import math
import time
from typing import Any

import pytest

from pdk.eyes import browser
from pdk.eyes.browser import Viewport


def test_viewport_parsing() -> None:
    assert Viewport.parse(None) == Viewport(1366, 768)
    assert Viewport.parse(" 1920 x 1080 ") == Viewport(1920, 1080)
    assert str(Viewport.parse("800X600")) == "800x600"
    for bad in ("1366", "wide", "1x2", "1366x768x2"):
        with pytest.raises(ValueError):
            Viewport.parse(bad)


@pytest.mark.parametrize(("state", "status"), [
    ({"viewNotFound": True}, browser.VIEW_NOT_FOUND),
    ({"sessionExceeded": True, "sessionExceededText": "Too many sessions"}, browser.SESSION_REFUSED),
    ({"bannerActive": True, "bannerText": "Reconnecting"}, browser.DISCONNECTED),
])
def test_terminal_page_states(state: dict, status: str) -> None:
    assert browser.classify_state(state)[0] == status


def test_loading_page_is_not_terminal() -> None:
    assert browser.classify_state({"rootVisible": False, "bannerActive": False}) is None


def test_geometry_delta() -> None:
    a = {"C.0": [0, 0, 100, 50], "C.0:0": [0, 0, 100, 20]}
    assert browser.geometry_delta(a, dict(a)) == (0.0, None)
    moved = {**a, "C.0:0": [0, 3.5, 100, 20]}
    assert browser.geometry_delta(a, moved) == (3.5, "C.0:0")
    mounted = {**a, "C.0:1": [0, 20, 100, 20]}
    delta, where = browser.geometry_delta(a, mounted)
    assert math.isinf(delta) and where == "C.0:1"


class _FakePage:
    """evaluate() answers by the script's identity: scripted animations, scripted geometry, a no-op rAF wait."""

    def __init__(self, animations: list[dict]) -> None:
        self.animations = animations
        self.geometry_args: list[Any] = []

    def evaluate(self, script: str, arg: Any = None) -> Any:
        if script == browser.ANIMATIONS_JS:
            return list(self.animations)
        if script == browser.GEOMETRY_JS:
            self.geometry_args.append(arg)
            return {"C.0": [0, 0, 100, 50], "C.0:0": [0, 0, 100, 20]}
        return None


@pytest.fixture
def fast_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(browser, "POLL_S", 0)
    monkeypatch.setattr(browser, "STABILITY_MS", 50)


def test_endless_animations_do_not_raise_and_are_returned(fast_wait: None) -> None:
    page = _FakePage([{"name": "blink", "path": "C.0:1", "endless": True},
                      {"name": "spin", "path": "C.0:2", "endless": True},
                      {"name": "spin", "path": "C.0:2", "endless": True},
                      {"name": None, "path": None, "endless": True}])
    assert browser._wait_stable(page) == ["C.0:1", "C.0:2"]
    assert page.geometry_args[-1] == {"sel": browser.SELECTORS, "skip": ["C.0:1", "C.0:2"]}


def test_no_animations_return_nothing_and_skip_nothing(fast_wait: None) -> None:
    page = _FakePage([])
    assert browser._wait_stable(page) == []
    assert page.geometry_args[-1] == {"sel": browser.SELECTORS, "skip": []}


def test_a_finite_animation_still_raises_after_the_deadline(fast_wait: None) -> None:
    fade = {"name": "fade", "path": "C.0:0", "endless": False}
    page = _FakePage([fade, {"name": "blink", "path": "C.0:1", "endless": True}])
    with pytest.raises(browser.EyesError) as caught:
        browser._wait_stable(page)
    assert caught.value.status == browser.LAYOUT_UNSTABLE
    assert caught.value.evidence == {"animations": [fade]}


def test_extract_js_reports_computed_style() -> None:
    sync_api = pytest.importorskip("playwright.sync_api")
    html = """
    <div id="root">
      <div data-component-path="C.0" data-component="ia.container.flex"
           style="display:flex; flex-direction:column; gap:8px; padding:4px 6px"></div>
      <div data-component-path="C.1" data-component="ia.display.label"
           style="color:red; font-size:14px; font-weight:700; border:2px solid black">Hola</div>
      <div data-component-path="C.2" data-component="ia.display.label" style="color:rgb(50, 50, 50)">
        <span style="color:#C62828; font-size:12px; font-weight:700">Obligatorio</span></div>
    </div>
    """
    with sync_api.sync_playwright() as p:
        try:
            chromium = p.chromium.launch(headless=True)
        except Exception as exc:  # the browser binary is not installed for Playwright on this machine
            pytest.skip(f"Playwright Chromium is not available: {exc}")
        try:
            page = chromium.new_page()
            page.set_content(html)
            result = page.evaluate(browser.EXTRACT_JS, {"root": "#root"})
        finally:
            chromium.close()

    nodes = {node["path"]: node for node in result["nodes"] if node["path"]}
    flex, label = nodes["C.0"], nodes["C.1"]

    assert flex["display"] == "flex" and flex["flex_direction"] == "column"
    assert flex["row_gap"] == 8.0 and flex["column_gap"] == 8.0
    assert flex["padding"] == [4.0, 6.0, 4.0, 6.0]
    assert flex["border_width"] == [0.0, 0.0, 0.0, 0.0]
    assert flex["justify_content"] and flex["align_items"]
    assert flex["color"].startswith("rgb(") and flex["background_color"].startswith("rgb")
    assert flex["font_family"] and flex["text_align"]

    assert label["color"] == "rgb(255, 0, 0)"
    assert label["background_color"] == "rgba(0, 0, 0, 0)"
    assert label["font_size"] == 14.0
    assert label["font_weight"] == "700"
    assert label["border_width"] == [2.0, 2.0, 2.0, 2.0]
    assert label["padding"] == [0.0, 0.0, 0.0, 0.0]
    assert label["row_gap"] is None and label["column_gap"] is None

    # A label's textStyle styles the element inside it: color and font are the text's, not the root's.
    styled = nodes["C.2"]
    assert (styled["color"], styled["font_size"], styled["font_weight"]) == ("rgb(198, 40, 40)", 12.0, "700")


def test_extract_js_cut_rect_frees_the_axes_a_container_scrolls() -> None:
    sync_api = pytest.importorskip("playwright.sync_api")
    # Body clips; Log, inside it, scrolls along y; Far lies past both bottoms; Cut is wider than Log, which clips x
    html = """
    <div id="root" style="overflow:hidden; width:300px; height:300px">
      <div data-component-path="C.0" data-component="ia.container.flex" style="overflow:hidden; height:200px">
        <div data-component-path="C.0:0" data-component="ia.container.flex"
             style="overflow-x:hidden; overflow-y:auto; width:200px; height:100px">
          <div data-component-path="C.0:0:0" data-component="ia.display.label" style="height:400px">Near</div>
          <div data-component-path="C.0:0:1" data-component="ia.display.label" style="height:20px">Far</div>
          <div data-component-path="C.0:0:2" data-component="ia.display.label"
               style="width:500px; white-space:nowrap">Cut</div>
        </div>
      </div>
    </div>
    """
    with sync_api.sync_playwright() as p:
        try:
            chromium = p.chromium.launch(headless=True)
        except Exception as exc:  # the browser binary is not installed for Playwright on this machine
            pytest.skip(f"Playwright Chromium is not available: {exc}")
        try:
            page = chromium.new_page()
            page.set_content(html)
            result = page.evaluate(browser.EXTRACT_JS, {"root": "#root"})
        finally:
            chromium.close()

    nodes = {node["path"]: node for node in result["nodes"] if node["path"]}
    far, cut = nodes["C.0:0:1"], nodes["C.0:0:2"]
    assert far["clip_rect"]["bottom"] == far["clip_rect"]["top"] + 100  # what is in sight: Log's box
    assert far["cut_rect"]["top"] < -1e5 and far["cut_rect"]["bottom"] > 1e5  # y scrolls, up to the root
    assert cut["cut_rect"]["width"] == 200  # x does not scroll: Log still cuts it


def test_an_idle_browser_is_closed_and_the_next_call_reopens_it() -> None:
    eyes = browser.Eyes(idle_s=0.2)
    closed: list[int] = []

    def fake_close() -> None:
        closed.append(1)
        eyes._browser = None

    eyes._close = fake_close  # type: ignore[method-assign]
    eyes._browser = object()
    eyes.run(lambda: None)
    time.sleep(0.6)
    assert closed == [1] and eyes._browser is None
    eyes.close()


class _FakeContext:
    """A browser context that records its close into the shared event list."""

    def __init__(self, events: list[Any]) -> None:
        self.events = events

    def close(self) -> None:
        self.events.append("close")


def test_release_gets_one_page_per_session_before_the_contexts_close() -> None:
    events: list[Any] = []
    eyes = browser.Eyes(release=lambda pages: events.append(("release", sorted(pages))))
    try:
        eyes._session_pages = {Viewport(1366, 768): "p1", Viewport(375, 812): "p2"}
        eyes._contexts = {Viewport(1366, 768): _FakeContext(events), Viewport(375, 812): _FakeContext(events)}
        eyes.run(eyes._close)
        assert events == [("release", ["p1", "p2"]), "close", "close"]
        assert eyes._session_pages == {}
    finally:
        eyes.close()


def test_a_release_that_raises_does_not_stop_the_close() -> None:
    events: list[Any] = []

    def broken(pages: list[str]) -> None:
        events.append("release")
        raise RuntimeError("gateway down")

    eyes = browser.Eyes(release=broken)
    try:
        eyes._session_pages = {Viewport(1366, 768): "p1"}
        eyes._contexts = {Viewport(1366, 768): _FakeContext(events)}
        eyes.run(eyes._close)
        assert events == ["release", "close"]
        assert eyes._session_pages == {} and eyes._contexts == {}
    finally:
        eyes.close()

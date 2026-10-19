"""The pure parts of pdk.eyes.browser; the browser itself is exercised by `pytest -m gateway`."""

from __future__ import annotations

import base64
import contextlib
import math
import struct
import time
import zlib
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from pdk.eyes import browser
from pdk.eyes.browser import Viewport


def test_viewport_parsing() -> None:
    assert Viewport.parse(None) == Viewport(1366, 768)
    assert Viewport.parse(" 1920 x 1080 ") == Viewport(1920, 1080)
    assert str(Viewport.parse("800X600")) == "800x600"
    for bad in ("1366", "huge", "1x2", "1366x768x2"):
        with pytest.raises(ValueError):
            Viewport.parse(bad)


def test_viewport_presets_and_lists() -> None:
    assert Viewport.parse("phone") == Viewport(375, 812)
    assert Viewport.parse(" Tablet ") == Viewport(768, 1024)
    assert Viewport.parse("wide") == Viewport(1920, 1080)
    assert Viewport.parse_many("desktop, phone,1366x768") == [Viewport(1366, 768), Viewport(375, 812)]
    assert Viewport.parse_many(None) == [Viewport(1366, 768)]
    with pytest.raises(ValueError):
        Viewport.parse_many("phone,nope")


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


# -- visual comparison: COMPARE_JS in a real Chromium, Eyes.compare with a fake page

WHITE = (255, 255, 255, 255)
BLACK = (0, 0, 0, 255)


def _png(width: int, height: int, pixel: Callable[[int, int], tuple[int, int, int, int]]) -> bytes:
    """A small RGBA PNG (8 bits, no filter), built with zlib and struct: the tests need no image library."""
    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    rows = b"".join(b"\x00" + bytes(channel for x in range(width) for channel in pixel(x, y)) for y in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


def _data_url(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def _changed(x: int, y: int) -> tuple[int, int, int, int]:
    """A 6x6 block at 10,10 and a pixel touching its corner (one region), a 2x2 block at 70,70 (another), and a
    pixel off by 10 at 40,40 (under the threshold)."""
    if (10 <= x <= 15 and 10 <= y <= 15) or (x, y) == (16, 16) or (70 <= x <= 71 and 70 <= y <= 71):
        return BLACK
    if (x, y) == (40, 40):
        return (245, 255, 255, 255)
    return WHITE


@contextlib.contextmanager
def _chromium_page() -> Iterator[Any]:
    """A blank page of Playwright's Chromium; the test is skipped where the browser is not installed."""
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as p:
        try:
            chromium = p.chromium.launch(headless=True)
        except Exception as exc:  # the browser binary is not installed for Playwright on this machine
            pytest.skip(f"Playwright Chromium is not available: {exc}")
        try:
            yield chromium.new_page()
        finally:
            chromium.close()


def test_compare_js_boxes_the_changed_regions_and_paints_the_diff() -> None:
    before = _png(96, 96, lambda x, y: WHITE)
    after = _png(96, 96, _changed)
    with _chromium_page() as page:
        result = page.evaluate(browser.COMPARE_JS, {"before": _data_url(before), "after": _data_url(after),
                                                     "threshold": 32, "cell": 16})
        # the diff, read back in the page: the changed pixel is red, the one under the threshold is not
        pixels = page.evaluate("""async (src) => {
          const img = new Image();
          img.src = src;
          await img.decode();
          const canvas = document.createElement('canvas');
          canvas.width = img.naturalWidth;
          canvas.height = img.naturalHeight;
          const ctx = canvas.getContext('2d');
          ctx.drawImage(img, 0, 0);
          return [Array.from(ctx.getImageData(12, 12, 1, 1).data), Array.from(ctx.getImageData(40, 40, 1, 1).data)];
        }""", result["diff"])

    assert (result["width"], result["height"], result["total"]) == (96, 96, 96 * 96)
    assert result["changed"] == 41 and result["region_count"] == 2
    # 8-connected: the corner pixel joins the block, so one region of 37 pixels, not two
    assert result["regions"] == [
        {"x": 10, "y": 10, "width": 7, "height": 7, "pixels": 37},
        {"x": 70, "y": 70, "width": 2, "height": 2, "pixels": 4},
    ]
    assert result["diff"].startswith("data:image/png;base64,")
    assert pixels[0] == [230, 0, 0, 255]
    assert pixels[1][1] > 200  # the unchanged pixel: the faded after image, not red


def test_compare_js_answers_the_sizes_when_they_differ() -> None:
    with _chromium_page() as page:
        result = page.evaluate(browser.COMPARE_JS, {"before": _data_url(_png(40, 40, lambda x, y: WHITE)),
                                                     "after": _data_url(_png(96, 96, lambda x, y: WHITE)),
                                                     "threshold": 32, "cell": 16})
    assert result == {"size_before": [40, 40], "size_after": [96, 96]}


class _ComparePage:
    """Stands in for the page: records each evaluate call and answers `result`, or raises `error`."""

    def __init__(self, result: Any = None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[tuple[str, Any]] = []

    def evaluate(self, script: str, arg: Any = None) -> Any:
        self.calls.append((script, arg))
        if self.error is not None:
            raise self.error
        return self.result


def _opened(page: _ComparePage) -> browser.Opened:
    return browser.Opened(url="http://gateway/preview", viewport=Viewport.parse(None), page=page, page_id=None,
                          readiness_ms=0.0, maker_notice=False)


def test_compare_sends_both_pngs_and_decodes_the_diff_into_bytes() -> None:
    diff = b"\x89PNG\r\n\x1a\ndiff"
    regions = [{"x": 1, "y": 2, "width": 3, "height": 4, "pixels": 5}]
    page = _ComparePage({"width": 8, "height": 8, "changed": 5, "total": 64, "region_count": 1, "regions": regions,
                         "diff": _data_url(diff)})
    with browser.Eyes() as eyes:
        result = eyes.compare(_opened(page), b"before", b"after", threshold=5, cell=4)
    script, arg = page.calls[0]
    assert script == browser.COMPARE_JS
    assert arg == {"before": _data_url(b"before"), "after": _data_url(b"after"), "threshold": 5, "cell": 4}
    assert result == {"width": 8, "height": 8, "changed": 5, "total": 64, "region_count": 1, "regions": regions,
                      "diff_png": diff}


def test_compare_of_different_sizes_has_no_diff() -> None:
    page = _ComparePage({"size_before": [2, 2], "size_after": [3, 3]})
    with browser.Eyes() as eyes:
        assert eyes.compare(_opened(page), b"a", b"b") == {"size_before": [2, 2], "size_after": [3, 3]}


def test_compare_failing_in_the_page_is_compare_failed() -> None:
    page = _ComparePage(error=RuntimeError("Image decode failed"))
    with browser.Eyes() as eyes, pytest.raises(browser.EyesError) as info:
        eyes.compare(_opened(page), b"a", b"b")
    assert info.value.status == browser.COMPARE_FAILED


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
        eyes._session_pages = {(Viewport(1366, 768), "sb"): "p1", (Viewport(1366, 768), "tg"): "p3",
                               (Viewport(375, 812), "sb"): "p2"}
        eyes._contexts = {Viewport(1366, 768): _FakeContext(events), Viewport(375, 812): _FakeContext(events)}
        eyes.run(eyes._close)
        assert events == [("release", [("sb", "p1"), ("sb", "p2"), ("tg", "p3")]), "close", "close"]
        assert eyes._session_pages == {}
    finally:
        eyes.close()


def test_a_release_that_raises_does_not_stop_the_close() -> None:
    events: list[Any] = []

    def broken(pages: list[tuple[str, str]]) -> None:
        events.append("release")
        raise RuntimeError("gateway down")

    eyes = browser.Eyes(release=broken)
    try:
        eyes._session_pages = {(Viewport(1366, 768), "sb"): "p1"}
        eyes._contexts = {Viewport(1366, 768): _FakeContext(events)}
        eyes.run(eyes._close)
        assert events == ["release", "close"]
        assert eyes._session_pages == {} and eyes._contexts == {}
    finally:
        eyes.close()


def test_forget_releases_every_session_of_that_viewport() -> None:
    released: list[Any] = []
    eyes = browser.Eyes(release=released.append)
    try:
        phone, desktop = Viewport(375, 812), Viewport(1366, 768)
        eyes._session_pages = {(phone, "sb"): "p1", (phone, "tg"): "p2", (desktop, "sb"): "p3"}
        eyes._contexts = {phone: _FakeContext([]), desktop: _FakeContext([])}
        eyes.run(eyes._forget, phone)
        assert released == [[("sb", "p1"), ("tg", "p2")]] and eyes._session_pages == {(desktop, "sb"): "p3"}
    finally:
        eyes._session_pages = {}
        eyes.close()


@pytest.mark.parametrize(("url", "project"), [
    ("http://127.0.0.1:8088/data/perspective/client/sb/pdk/PDK/Pump", "sb"),
    ("http://gw/data/perspective/client/Mi%20Planta/pdk/X?x=1", "Mi Planta"),
    ("http://gw/web/home", ""),
])
def test_project_of_a_perspective_url(url: str, project: str) -> None:
    assert browser.project_of(url) == project


def test_the_idle_close_waits_for_a_call_in_flight() -> None:
    eyes = browser.Eyes(idle_s=0.2)
    closed: list[int] = []
    eyes._close = lambda: closed.append(1)  # type: ignore[method-assign]
    try:
        eyes._last_use = time.monotonic() - 10
        eyes._in_flight = 1  # a call submitted after the watchdog looked
        eyes._close_if_idle()
        assert closed == []
        eyes._in_flight = 0
        eyes._close_if_idle()
        assert closed == [1]
    finally:
        eyes.close()


def test_a_playwright_error_from_the_worker_is_an_eyes_error() -> None:
    class TimeoutError(Exception):  # noqa: A001 - a stand-in for the one Playwright raises
        pass

    TimeoutError.__module__ = "playwright._impl._errors"

    def fails() -> None:
        raise TimeoutError("Timeout 30000ms exceeded")

    eyes = browser.Eyes()
    try:
        with pytest.raises(browser.EyesError) as caught:
            eyes.run(fails)
        assert caught.value.status == browser.READINESS_TIMEOUT
        with pytest.raises(KeyError):  # PDK's own errors are not hidden
            eyes.run(lambda: {}["x"])
    finally:
        eyes.close()


def test_a_screenshot_of_a_component_with_no_size_fails_at_once() -> None:
    class Locator:
        def count(self) -> int:
            return 1

        def bounding_box(self) -> dict[str, float]:
            return {"x": 0, "y": 0, "width": 120, "height": 0}

        def screenshot(self) -> bytes:
            raise AssertionError("it would wait 30 s")

    class Page:
        def evaluate(self, script: str, arg: Any = None) -> bool:
            return False  # no Maker notice

        def locator(self, selector: str) -> Locator:
            return Locator()

    eyes = browser.Eyes()
    try:
        opened = browser.Opened("http://gw", Viewport(1366, 768), Page(), None, 0.0, False)
        with pytest.raises(browser.EyesError, match="has no size"):
            eyes.run(eyes._screenshot, opened, "C.0:1")
    finally:
        eyes.close()

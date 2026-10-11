"""Chromium side of the eyes: readiness, stability, DOM extraction, console and screenshots.

Ported from perspective-compiler's runtime harness (open_ready_page, geometry stability, extraction) without the
IR, hashes or artifacts. Playwright's sync API is bound to the thread that started it and refuses to run inside an
asyncio loop (the MCP server has one), so every browser call runs on one dedicated worker thread owned by `Eyes`.
One Chromium and one context per viewport stay open between calls: Maker caps concurrent Perspective sessions, and
a kept context keeps its session; every open is a new tab, so a new Perspective page whose views start afresh.
"""

from __future__ import annotations

import contextlib
import re
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, TypeVar

T = TypeVar("T")

NAVIGATION_MS = 10_000
READY_MS = 15_000
STABILITY_MS = 5_000
FONTS_MS = 10_000
STABLE_SAMPLES = 3
POLL_S = 0.05
LAYOUT_EPSILON_PX = 0.0  # measured by perspective-compiler over 2640 rAF samples: geometry settles exactly
DEFAULT_VIEWPORT = "1366x768"
ROOT_PATH = "C.0"
CONSOLE_LIMIT = 50

SELECTORS: dict[str, str] = {
    "root": f'[data-component-path="{ROOT_PATH}"]',
    "session_exceeded": ".not-licensed-page",
    "maker_notice": ".one-time-maker-modal",
    "maker_notice_dismiss": ".one-time-maker-footer > div:last-child",
    "connection_banner": ".connection-banner",
    "banner_active_class": "banner-active",
    "view_not_found": ".ia_viewStateDisplay__primaryMessage",
}

# Failure statuses of opening a view, stable for the report (X0xx codes are assigned in pdk.eyes.rules).
GATEWAY_UNAVAILABLE = "gateway-unavailable"
HTTP_ERROR = "http-error"
VIEW_NOT_FOUND = "view-not-found"
SESSION_REFUSED = "session-refused"
DISCONNECTED = "disconnected"
READINESS_TIMEOUT = "readiness-timeout"
LAYOUT_UNSTABLE = "layout-unstable"
BROWSER_CRASH = "browser-crash"
BROWSER_MISSING = "browser-missing"


class EyesError(RuntimeError):
    """Opening or reading the page failed; `status` is one of the constants above."""

    def __init__(self, status: str, message: str, evidence: dict[str, Any] | None = None):
        super().__init__(message)
        self.status = status
        self.evidence = evidence or {}


@dataclass(frozen=True)
class Viewport:
    width: int
    height: int

    @classmethod
    def parse(cls, text: str | None) -> Viewport:
        match = re.fullmatch(r"\s*(\d{2,5})\s*[xX×]\s*(\d{2,5})\s*", text or DEFAULT_VIEWPORT)
        if not match:
            raise ValueError(f"viewport must look like 1366x768, got {text!r}")
        return cls(int(match.group(1)), int(match.group(2)))

    def __str__(self) -> str:
        return f"{self.width}x{self.height}"


@dataclass
class Opened:
    """A ready page. Only the worker thread may touch `page`."""

    url: str
    viewport: Viewport
    page: Any
    page_id: str | None
    readiness_ms: float
    maker_notice: bool
    console: list[dict[str, str]] = field(default_factory=list)
    endless: list[str] = field(default_factory=list)  # component paths of infinite animations, left out of the wait


# ------------------------------------------------------------------------------------------------ page scripts

STATE_JS = r"""
(sel) => {
  const visible = (el) => {
    if (!el) return false;
    const cs = getComputedStyle(el), r = el.getBoundingClientRect();
    return cs.display !== 'none' && cs.visibility !== 'hidden' && Number(cs.opacity) > 0
      && r.width > 0 && r.height > 0;
  };
  const banner = document.querySelector(sel.connection_banner);
  const exceeded = document.querySelector(sel.session_exceeded);
  return {
    rootVisible: visible(document.querySelector(sel.root)),
    bannerActive: !!banner && typeof banner.className === 'string'
      && banner.className.includes(sel.banner_active_class),
    bannerText: banner ? (banner.textContent || '').trim().slice(0, 200) : null,
    viewNotFound: visible(document.querySelector(sel.view_not_found)),
    sessionExceeded: visible(exceeded),
    sessionExceededText: exceeded ? (exceeded.textContent || '').trim().slice(0, 200) : null,
    makerNotice: visible(document.querySelector(sel.maker_notice)),
    fonts: document.fonts ? document.fonts.status : null,
    dpr: window.devicePixelRatio,
    pageId: sessionStorage.getItem('perspective-tab-id'),
  };
}
"""

ANIMATIONS_JS = r"""
(sel) => {
  const root = document.querySelector(sel.root);
  return document.getAnimations().filter((a) => {
    const t = a.effect && a.effect.target;
    return a.playState === 'running' && (!t || !root || root.contains(t));
  }).map((a) => {
    const t = a.effect && a.effect.target;
    const owner = t && t.closest ? t.closest('[data-component-path]') : null;
    // Infinite iterations (a blinking alarm, a spinner) never finish: the caller does not wait for them.
    const timing = a.effect && a.effect.getTiming ? a.effect.getTiming() : null;
    const computed = a.effect && a.effect.getComputedTiming ? a.effect.getComputedTiming() : null;
    const endless = (timing && timing.iterations === Infinity) || (computed && computed.endTime === Infinity);
    return {name: a.animationName || null, path: owner ? owner.getAttribute('data-component-path') : null,
            endless: !!endless};
  });
}
"""

# arg = {sel: SELECTORS, skip: [component paths]}: the skipped components and their descendants are not sampled.
GEOMETRY_JS = r"""
(arg) => {
  const root = document.querySelector(arg.sel.root);
  if (!root) return null;
  const skip = arg.skip || [];
  const skipped = (path) => skip.some((p) => path === p || path.startsWith(p + ':'));
  const out = {};
  for (const el of [root, ...root.querySelectorAll('[data-component-path]')]) {
    const path = el.getAttribute('data-component-path');
    if (skipped(path)) continue;
    const r = el.getBoundingClientRect();
    out[path] = [r.x, r.y, r.width, r.height];
  }
  return out;
}
"""

# Per component: box, overflow, computed style (colors, font, padding, border, flex, gaps), own text (not its child
# components' nor the quality overlay's) with the color and font of the element that holds it, the rects of that
# text, the clip rect of overflow-clipping ancestors, and the quality overlay Perspective draws on bad bindings.
# cut_rect is clip_rect without the axes that scroll: text past a scroll container's edge is scrolled out of view,
# not cut.
POPUPS_JS = r"""
() => [...document.querySelectorAll('.popups-pane [id^="popup-"]')]
  .filter((el) => getComputedStyle(el).visibility !== 'hidden')
  .map((el) => el.id.slice('popup-'.length))
"""

EXTRACT_JS = r"""
(sel) => {
  const root = document.querySelector(sel.root);
  if (!root) return null;
  const rectOf = (r) => ({x: r.x, y: r.y, top: r.top, right: r.right, bottom: r.bottom, left: r.left,
                          width: r.width, height: r.height});
  const intersect = (a, b) => {
    const left = Math.max(a.left, b.left), top = Math.max(a.top, b.top);
    const right = Math.min(a.right, b.right), bottom = Math.min(a.bottom, b.bottom);
    return {x: left, y: top, top, right, bottom, left,
            width: Math.max(0, right - left), height: Math.max(0, bottom - top)};
  };
  const clips = (el) => {
    const cs = getComputedStyle(el);
    return cs.overflowX !== 'visible' || cs.overflowY !== 'visible';
  };
  const scrolls = (v) => v === 'auto' || v === 'scroll';
  const clipRect = (el, ignoreScroll) => {
    let rect = null, freeX = false, freeY = false;  // past a scrolling ancestor, outer ones do not cut that axis
    for (let cur = el; cur; cur = cur.parentElement) {
      if (clips(cur)) {
        const cs = getComputedStyle(cur), r = rectOf(cur.getBoundingClientRect());
        freeX = freeX || (ignoreScroll && scrolls(cs.overflowX));
        freeY = freeY || (ignoreScroll && scrolls(cs.overflowY));
        if (freeX) { r.left = r.x = -1e6; r.right = 1e6; r.width = 2e6; }
        if (freeY) { r.top = r.y = -1e6; r.bottom = 1e6; r.height = 2e6; }
        rect = rect ? intersect(rect, r) : r;
      }
      if (cur === root) break;
    }
    return rect;
  };
  const ownText = (el) => {
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    const rects = [];
    let text = '', holder = null;
    for (let node; (node = walker.nextNode());) {
      const parent = node.parentElement;
      if (!parent || parent.closest('[data-component-path]') !== el || parent.closest('.cfo-wrapper')) continue;
      if (parent.closest('svg')) continue;
      const value = node.nodeValue || '';
      if (!value.trim()) continue;
      holder = holder || parent;
      text += value;
      const range = document.createRange();
      range.selectNodeContents(node);
      for (const r of range.getClientRects()) if (r.width > 0 && r.height > 0) rects.push(rectOf(r));
    }
    return {text: text.trim() || null, rects, holder};
  };
  const overlay = (el) => {
    for (const wrapper of el.querySelectorAll(':scope > .cfo-wrapper')) {
      const o = wrapper.querySelector('.ia_qualityOverlay');
      if (!o) continue;
      const m = /ia_qualityOverlay--(\w+)/.exec(o.className);
      return m ? m[1] : 'unknown';
    }
    return null;
  };
  const parentPath = (el) => {
    for (let cur = el.parentElement; cur; cur = cur.parentElement)
      if (cur.hasAttribute('data-component-path')) return cur.getAttribute('data-component-path');
    return null;
  };
  const px = (v) => { const n = parseFloat(v); return Number.isNaN(n) ? null : n; };
  // Perspective draws ViewStateDisplay where an embedded or repeated view failed (not found, error, access denied).
  const failedViews = (el) => {
    let n = 0;
    for (const s of el.querySelectorAll('.view-state-display')) if (s.closest('[data-component-path]') === el) n++;
    return n;
  };
  const inputValue = (el) => {
    const field = 'input:not([type=checkbox]):not([type=radio]), textarea';
    for (const i of [el, ...el.querySelectorAll(field)])
      if (i.matches(field) && i.closest('[data-component-path]') === el) return i.value.trim() || null;
    return null;
  };
  const nodes = [];
  for (const el of [root, ...root.querySelectorAll('[data-component-path]')]) {
    const cs = getComputedStyle(el), r = el.getBoundingClientRect(), text = ownText(el);
    // Text color and font are those of the element holding the text (a label's textStyle styles an inner one).
    const ts = text.holder && text.holder !== el ? getComputedStyle(text.holder) : cs;
    nodes.push({
      path: el.getAttribute('data-component-path'), type: el.getAttribute('data-component'), parent: parentPath(el),
      bounds: rectOf(r), client_width: el.clientWidth, client_height: el.clientHeight,
      scroll_width: el.scrollWidth, scroll_height: el.scrollHeight,
      scroll_left: el.scrollLeft, scroll_top: el.scrollTop,
      visible: cs.display !== 'none' && cs.visibility !== 'hidden' && Number(cs.opacity) > 0,
      display: cs.display, position: cs.position, overflow_x: cs.overflowX, overflow_y: cs.overflowY,
      white_space: cs.whiteSpace, text_overflow: cs.textOverflow,
      flex_grow: Number(cs.flexGrow), flex_shrink: Number(cs.flexShrink), flex_basis: cs.flexBasis,
      text: text.text, text_rects: text.rects, clip_rect: clipRect(el, false),
      // No text of its own, for `text` expectations: what an embedded view or repeater renders, an input's value.
      inner_text: /^ia\.display\.(view|flex-repeater)$/.test(el.getAttribute('data-component') || '')
        ? el.innerText.trim() || null : inputValue(el),
      cut_rect: clipRect(el, true), overlay: overlay(el), failed_views: failedViews(el),
      disabled: el.matches(':disabled') || !!el.querySelector('input:disabled, button:disabled, textarea:disabled'),
      color: ts.color, background_color: cs.backgroundColor,
      font_family: ts.fontFamily, font_size: px(ts.fontSize), font_weight: ts.fontWeight, text_align: cs.textAlign,
      padding: [px(cs.paddingTop), px(cs.paddingRight), px(cs.paddingBottom), px(cs.paddingLeft)],
      border_width: [px(cs.borderTopWidth), px(cs.borderRightWidth), px(cs.borderBottomWidth), px(cs.borderLeftWidth)],
      flex_direction: cs.flexDirection, justify_content: cs.justifyContent, align_items: cs.alignItems,
      row_gap: px(cs.rowGap), column_gap: px(cs.columnGap),
    });
  }
  return {url: location.href, viewport: {width: window.innerWidth, height: window.innerHeight}, nodes};
}
"""


# ------------------------------------------------------------------------------------------------ eyes


class Eyes:
    """A long-lived headless Chromium; every method blocks until its work on the browser thread is done.

    With `idle_s`, the browser closes after that long without work: an open page keeps its Perspective session
    connected (Maker caps them per gateway), and a long-lived server would otherwise hold it forever. The next call
    starts it again. A browser that goes away only disconnects its sessions, which then hold their slots until they
    time out: `release(page_ids)`, called before the contexts close with one page of each session, closes them on
    the gateway.
    """

    def __init__(self, headless: bool = True, idle_s: float | None = None,
                 release: Callable[[list[str]], None] | None = None):
        self._headless = headless
        self._release = release
        self._session_pages: dict[Viewport, str] = {}  # one page id per context (= Perspective session)
        self._worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pdk-eyes")
        self._idle_s = idle_s
        self._last_use = time.monotonic()
        self._watchdog: threading.Thread | None = None
        self._stopped = threading.Event()
        self._playwright: Any = None
        self._browser: Any = None
        self._contexts: dict[Viewport, Any] = {}
        self._pages: dict[tuple[str, Viewport], Opened] = {}

    def __enter__(self) -> Eyes:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def run(self, work: Callable[..., T], *args: Any) -> T:
        try:
            return self._worker.submit(work, *args).result()
        finally:
            self._last_use = time.monotonic()
            if self._idle_s and self._watchdog is None and not self._stopped.is_set():
                self._watchdog = threading.Thread(target=self._close_when_idle, name="pdk-eyes-idle", daemon=True)
                self._watchdog.start()

    def _close_when_idle(self) -> None:
        assert self._idle_s
        while not self._stopped.wait(min(30.0, self._idle_s / 2)):
            if self._browser is not None and time.monotonic() - self._last_use > self._idle_s:
                with contextlib.suppress(RuntimeError):  # the pool is shutting down
                    self._worker.submit(self._close).result()

    # -- public API (any thread) ---------------------------------------------------------------------------------

    def open(self, url: str, viewport: Viewport | None = None, *, fresh: bool = True) -> Opened:
        """Open `url` in a new tab (replacing the one already open, unless `fresh` is False) and wait until it is
        ready and stable."""
        return self.run(self._open, url, viewport or Viewport.parse(None), fresh)

    def extract(self, opened: Opened, root: str = ROOT_PATH) -> dict[str, Any]:
        """Boxes, text and overflow of every component under the root (C.0, or P<id>.0 for a popup), after the
        geometry is stable again."""
        return self.run(self._extract, opened, root)

    def location(self, opened: Opened) -> str:
        """The page's URL now (a step may have navigated it)."""
        return self.run(lambda: opened.page.url)

    def popups(self, opened: Opened) -> list[str]:
        """Ids of the popups open on the page, in stacking order."""
        return self.run(lambda: opened.page.evaluate(POPUPS_JS))

    def act(self, opened: Opened, steps: list[Any], pointers: dict[str, str],
            resolve: Callable[[str], str | None] | None = None, tags: Callable[[Any], Any] | None = None) -> Any:
        """Perform pdk.eyes.act steps on the page, letting it settle after each one."""
        from pdk.eyes import act
        return self.run(act.run, opened.page, steps, _wait_stable, pointers, resolve, tags)

    def screenshot(self, opened: Opened, component: str | None = None) -> bytes:
        """PNG of the viewport, or of one component by its DOM path (e.g. C.0:3:0)."""
        return self.run(self._screenshot, opened, component)

    def close(self) -> None:
        """Idempotent; safe from atexit, where the worker pool may already be shut down."""
        self._stopped.set()
        try:
            self.run(self._close)
        except RuntimeError:
            pass
        finally:
            self._worker.shutdown(wait=True)

    # -- worker thread -------------------------------------------------------------------------------------------

    def _ensure_browser(self) -> Any:
        if self._browser is not None and self._browser.is_connected():
            return self._browser
        try:
            from playwright.sync_api import sync_playwright  # lazy: validation never needs a browser
        except ImportError as exc:
            raise EyesError(BROWSER_MISSING, "playwright is not installed (uv sync)") from exc
        if self._playwright is None:
            self._playwright = sync_playwright().start()
        try:
            self._browser = self._playwright.chromium.launch(headless=self._headless)
        except Exception as exc:  # noqa: BLE001
            raise EyesError(BROWSER_MISSING,
                            f"Chromium did not start ({exc}); run `uv run playwright install chromium`") from exc
        self._contexts.clear()
        self._pages.clear()
        self._session_pages.clear()
        return self._browser

    def _context(self, viewport: Viewport) -> Any:
        browser = self._ensure_browser()
        context = self._contexts.get(viewport)
        if context is None:
            context = browser.new_context(viewport={"width": viewport.width, "height": viewport.height},
                                          device_scale_factor=1, locale="en-US", timezone_id="UTC")
            self._contexts[viewport] = context
        return context

    def _open(self, url: str, viewport: Viewport, fresh: bool) -> Opened:
        key = (url, viewport)
        previous = self._pages.get(key)
        if previous is not None and not previous.page.is_closed() and not fresh:
            return previous
        # A new tab, not a reload: Perspective keeps the page and its view instances across a reload of the same
        # tab (sessionStorage keeps the page id), so startup scripts would not run again. A new tab in the same
        # context is a new page of the same session.
        page = self._context(viewport).new_page()
        opened = Opened(url, viewport, page, None, 0.0, False)
        page.on("console", lambda message, o=opened: _console(o, message.type, message.text))
        page.on("pageerror", lambda error, o=opened: _console(o, "pageerror", str(error)))
        started = time.perf_counter()
        try:
            self._navigate(page, url)
        except BaseException:
            page.close()
            raise
        if previous is not None:
            with contextlib.suppress(Exception):
                previous.page.close()
        self._pages[key] = opened
        state = _wait_ready(opened.page)
        opened.readiness_ms = round((time.perf_counter() - started) * 1000, 1)
        opened.page_id = state.get("pageId")
        if opened.page_id:
            self._session_pages[viewport] = opened.page_id
        opened.maker_notice = _dismiss_maker_notice(opened.page)
        if state.get("dpr") != 1:
            raise EyesError(BROWSER_CRASH, f"unexpected device pixel ratio {state.get('dpr')}")
        try:
            opened.page.wait_for_function("() => !document.fonts || document.fonts.status === 'loaded'",
                                          timeout=FONTS_MS)
        except Exception as exc:  # noqa: BLE001
            raise EyesError(READINESS_TIMEOUT, f"fonts never finished loading: {exc}") from exc
        opened.endless = _wait_stable(opened.page)
        return opened

    @staticmethod
    def _navigate(page: Any, url: str) -> None:
        try:
            response = page.goto(url, wait_until="domcontentloaded", timeout=NAVIGATION_MS)
        except Exception as exc:  # noqa: BLE001
            raise EyesError(_navigation_status(exc), f"navigation to {url} failed: {exc}") from exc
        if response is not None and response.status >= 400:
            raise EyesError(HTTP_ERROR, f"{url} answered HTTP {response.status}", {"http_status": response.status})

    def _extract(self, opened: Opened, root: str = ROOT_PATH) -> dict[str, Any]:
        opened.endless = sorted({*opened.endless, *_wait_stable(opened.page)})
        try:
            extracted = opened.page.evaluate(EXTRACT_JS, {**SELECTORS, "root": f'[data-component-path="{root}"]'})
        except Exception as exc:  # noqa: BLE001
            raise EyesError(_crash_or(exc, READINESS_TIMEOUT), f"DOM extraction failed: {exc}") from exc
        if not extracted:
            raise EyesError(READINESS_TIMEOUT, "the view root disappeared before extraction")
        extracted["console"] = list(opened.console)
        return extracted

    def _screenshot(self, opened: Opened, component: str | None) -> bytes:
        _dismiss_maker_notice(opened.page)
        if component is None:
            return opened.page.screenshot(full_page=False)
        locator = opened.page.locator(f'[data-component-path="{component}"]')
        if locator.count() != 1:
            raise EyesError(READINESS_TIMEOUT, f"no single component at DOM path {component}")
        return locator.screenshot()

    def _close(self) -> None:
        pages = list(self._session_pages.values())
        self._session_pages.clear()
        if pages and self._release is not None:
            with contextlib.suppress(Exception):  # best effort: an unclosed session only times out
                self._release(pages)
        for context in self._contexts.values():
            with contextlib.suppress(Exception):
                context.close()
        self._contexts.clear()
        self._pages.clear()
        if self._browser is not None:
            with contextlib.suppress(Exception):
                self._browser.close()
            self._browser = None
        if self._playwright is not None:
            with contextlib.suppress(Exception):
                self._playwright.stop()
            self._playwright = None


# ------------------------------------------------------------------------------------------------ helpers


def _console(opened: Opened, kind: str, text: str) -> None:
    if kind in ("error", "warning", "pageerror") and len(opened.console) < CONSOLE_LIMIT:
        opened.console.append({"type": kind, "text": text[:1000]})


UNREACHABLE = ("ERR_CONNECTION_REFUSED", "ERR_CONNECTION_RESET", "ERR_NAME_NOT_RESOLVED", "ERR_CONNECTION_TIMED_OUT",
               "Timeout")


def _navigation_status(exc: Exception) -> str:
    text = str(exc)
    if any(marker in text for marker in UNREACHABLE):
        return GATEWAY_UNAVAILABLE
    return _crash_or(exc, GATEWAY_UNAVAILABLE)


def _crash_or(exc: Exception, fallback: str) -> str:
    text = str(exc)
    crashed = "has been closed" in text or "Target closed" in text or "crash" in text.lower()
    return BROWSER_CRASH if crashed else fallback


def classify_state(state: dict[str, Any]) -> tuple[str, str] | None:
    """A terminal failure visible in the page state, or None while it may still become ready."""
    if state.get("viewNotFound"):
        return VIEW_NOT_FOUND, "Perspective shows 'View Not Found' for the routed view"
    if state.get("sessionExceeded"):
        return SESSION_REFUSED, f"the gateway refused a new session: {state.get('sessionExceededText')}"
    if state.get("bannerActive"):
        return DISCONNECTED, f"the session is disconnected: {state.get('bannerText')}"
    return None


def _wait_ready(page: Any) -> dict[str, Any]:
    deadline = time.perf_counter() + READY_MS / 1000
    state: dict[str, Any] = {}
    while time.perf_counter() < deadline:
        try:
            state = page.evaluate(STATE_JS, SELECTORS)
        except Exception as exc:  # noqa: BLE001
            raise EyesError(_crash_or(exc, READINESS_TIMEOUT), f"state probe failed: {exc}") from exc
        if state.get("rootVisible"):
            return state
        failure = classify_state(state)
        if failure:
            raise EyesError(failure[0], failure[1], {"state": state})
        time.sleep(POLL_S)
    raise EyesError(READINESS_TIMEOUT, f"the view root never became visible within {READY_MS // 1000} s",
                    {"state": state})


def _dismiss_maker_notice(page: Any) -> bool:
    """The Maker 'Personal Use Only' modal shows once per context, over the view; close it before measuring."""
    try:
        if not page.evaluate("(s) => !!document.querySelector(s)", SELECTORS["maker_notice"]):
            return False
        page.click(SELECTORS["maker_notice_dismiss"], timeout=5000)
        page.wait_for_selector(SELECTORS["maker_notice"], state="detached", timeout=5000)
        return True
    except Exception as exc:  # noqa: BLE001
        raise EyesError(_crash_or(exc, READINESS_TIMEOUT), f"the Maker notice did not close: {exc}") from exc


def geometry_delta(previous: dict[str, list[float]], current: dict[str, list[float]]) -> tuple[float, str | None]:
    """The largest move of any component present in both samples, and its path."""
    worst, where = 0.0, None
    for path, box in current.items():
        before = previous.get(path)
        if before is None:
            continue
        delta = max(abs(a - b) for a, b in zip(box, before, strict=True))
        if delta > worst:
            worst, where = delta, path
    if set(previous) != set(current):
        changed = sorted(set(previous) ^ set(current))
        return float("inf"), changed[0]
    return worst, where


def _wait_stable(page: Any) -> list[str]:
    """No running finite animations under the root and STABLE_SAMPLES equal rAF geometry samples in a row. Endless
    animations (infinite iterations) never finish: they are not waited for and their components are left out of the
    samples. Returns the sorted component paths of those endless animations."""
    deadline = time.perf_counter() + STABILITY_MS / 1000
    running: list[dict[str, Any]] = []
    endless: set[str] = set()
    while time.perf_counter() < deadline:
        running = page.evaluate(ANIMATIONS_JS, SELECTORS)
        endless.update(a["path"] for a in running if a.get("endless") and a.get("path"))
        finite = [a for a in running if not a.get("endless")]
        if not finite:
            break
        time.sleep(POLL_S)
    else:
        finite = [a for a in running if not a.get("endless")]
        raise EyesError(LAYOUT_UNSTABLE, "animations under the view root never finished", {"animations": finite})
    skip = sorted(endless)
    previous: dict[str, list[float]] | None = None
    steady = 0
    moving: tuple[float, str | None] = (0.0, None)
    while time.perf_counter() < deadline:
        page.evaluate("() => new Promise((resolve) => requestAnimationFrame(() => resolve(null)))")
        sample = page.evaluate(GEOMETRY_JS, {"sel": SELECTORS, "skip": skip})
        if sample is None:
            raise EyesError(READINESS_TIMEOUT, "the view root disappeared while waiting for a stable layout")
        if previous is not None:
            moving = geometry_delta(previous, sample)
            steady = steady + 1 if moving[0] <= LAYOUT_EPSILON_PX else 0
            if steady >= STABLE_SAMPLES - 1:
                return skip
        previous = sample
        time.sleep(POLL_S)
    raise EyesError(LAYOUT_UNSTABLE, f"the layout kept moving (largest move {moving[0]} px at {moving[1]})",
                    {"path": moving[1], "delta": moving[0]})

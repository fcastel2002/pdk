"""pdk.eyes.act: parsing steps against the view's name paths, and running them on a stand-in page."""

from __future__ import annotations

import json
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

from pdk.eyes import act
from pdk.validate.tree import build_tree, walk
from tests.test_eyes_rules import _view


def _nodes(*extra: dict[str, Any]) -> dict[str, Any]:
    view = _view()
    view["root"]["children"].extend(extra)
    return {node.path: node for node in walk(build_tree(view))}


def _widget(kind: str, name: str) -> dict[str, Any]:
    return {"type": kind, "meta": {"name": name}}


def _widgets() -> dict[str, Any]:
    """The view's components plus a dropdown D (C.0:3), a slider S (C.0:4) and a scrolling feed T (C.0:5)."""
    return _nodes(_widget("ia.input.dropdown", "D"), _widget("ia.input.slider", "S"),
                  _widget("ia.display.flex-repeater", "T"))


def test_steps_resolve_components_to_dom_paths() -> None:
    text = 'click root/Row/Narrow; type root/Title "Pump 2"\nrow root/Box 3; wait 99999'
    steps, malformed = act.parse(text, _nodes())
    assert malformed == []
    assert [(s.verb, s.dom_path, s.value, s.number) for s in steps] == [
        ("click", "C.0:1:0", None, 0), ("type", "C.0:0", "Pump 2", 0), ("row", "C.0:2", None, 3),
        ("wait", None, None, act.MAX_WAIT_MS)]


def test_malformed_steps_are_a005() -> None:
    steps, malformed = act.parse("tap root/Title; click root/Nope; type root/Title; row root/Box 0; wait soon",
                                 _nodes())
    assert steps == [] and [d["code"] for d in malformed] == ["A005"] * 5
    assert "no component 'root/Nope'" in malformed[1]["message"]


def test_new_verbs_resolve_and_keep_their_arguments() -> None:
    text = ('select root/D "Auto" "Manual"; hover root/Title; double-click root/Row/Narrow; scroll root/T 120; '
            'scroll root/T down; key Enter; key root/Title Control+A; slide root/S 2.5')
    steps, malformed = act.parse(text, _widgets())
    assert malformed == []
    assert [(s.verb, s.dom_path, s.labels, s.key, s.scroll, s.level) for s in steps] == [
        ("select", "C.0:3", ("Auto", "Manual"), None, None, None),
        ("hover", "C.0:0", (), None, None, None),
        ("double-click", "C.0:1:0", (), None, None, None),
        ("scroll", "C.0:5", (), None, 120, None),
        ("scroll", "C.0:5", (), None, "down", None),
        ("key", None, (), "Enter", None, None),
        ("key", "C.0:0", (), "Control+A", None, None),
        ("slide", "C.0:4", (), None, None, 2.5)]


def test_new_verbs_that_cannot_be_read_are_a005() -> None:
    text = ('select root/D; key; key root/Title; key root/Title Enter extra; key Enter Tab Escape; '
            'scroll root/T sideways; slide root/S abc')
    steps, malformed = act.parse(text, _widgets())
    assert steps == [] and [d["code"] for d in malformed] == ["A005"] * 7
    assert "expected `key [<component>] <Key>`" in malformed[1]["message"]
    assert "expected `slide <slider> <N>`" in malformed[6]["message"]


class _Failing:
    """A page whose components are never rendered."""

    def locator(self, _selector: str) -> Any:
        return self

    def count(self) -> int:
        return 0


def test_a_step_that_cannot_be_done_stops_the_sequence_as_a004() -> None:
    steps, _ = act.parse("click root/Title; click root/Box", _nodes())
    settled: list[Any] = []
    outcome = act.run(_Failing(), steps, settled.append, {"root/Title": "/root/children/0"})
    assert (outcome.done, outcome.total, outcome.summary()) == (0, 2, "act: 0/2 done")
    [failure] = outcome.diagnostics
    assert (failure["code"], failure["pointer"]) == ("A004", "/root/children/0")
    assert "not rendered" in failure["message"] and settled == []


class _Clicks:
    """A page where every component is rendered once and records what was clicked."""

    def __init__(self) -> None:
        self.clicked: list[str] = []
        self._selector = ""

    def locator(self, selector: str) -> Any:
        page = _Clicks.__new__(_Clicks)
        page.clicked, page._selector = self.clicked, selector
        return page

    def count(self) -> int:
        return 0 if "label:has" in self._selector else 1

    def click(self, timeout: int) -> None:
        self.clicked.append(self._selector)


def test_popup_components_resolve_when_the_step_runs() -> None:
    steps, malformed = act.parse("click root/Title; click popup:detail/root/Ok", _nodes())
    assert malformed == [] and steps[1].dom_path is None
    page = _Clicks()
    outcome = act.run(page, steps, lambda _page: None, {}, lambda name: "Pdetail.0:3")
    assert outcome.done == 2 and page.clicked[-1] == '[data-component-path="Pdetail.0:3"]'
    closed = act.run(_Clicks(), steps, lambda _page: None, {}, lambda name: None)
    assert closed.done == 1 and "no open popup has 'detail/root/Ok'" in closed.diagnostics[0]["message"]


class _Dom:
    """A stand-in page. `elements` maps a selector (a chained one is the parent's, a space, the child's) to a state
    dict: `attrs`, `visible`, `labels` (for evaluate_all), `evaluate(expr, arg)`, `on_click()`, `on_press(key)`.
    An element exists when its selector is a key; the actions the steps take on it are logged."""

    def __init__(self, elements: dict[str, dict[str, Any]]) -> None:
        self.elements = elements
        self.log: list[tuple[Any, ...]] = []
        self.keyboard = SimpleNamespace(press=lambda key: self.log.append(("keyboard", key)))

    def locator(self, selector: str) -> _Loc:
        return _Loc(self, selector)

    def wait_for_timeout(self, _ms: int) -> None:
        pass


class _Loc:
    def __init__(self, dom: _Dom, selector: str) -> None:
        self.dom, self.selector = dom, selector
        self.state = dom.elements.get(selector, {})

    @property
    def first(self) -> _Loc:
        return self

    def locator(self, sub: str) -> _Loc:
        return _Loc(self.dom, f"{self.selector} {sub}")

    def count(self) -> int:
        return int(self.selector in self.dom.elements)

    def is_visible(self) -> bool:
        return self.count() > 0 and bool(self.state.get("visible", True))

    def get_attribute(self, name: str) -> str | None:
        return self.state.get("attrs", {}).get(name)

    def evaluate(self, expr: str, arg: Any = None) -> Any:
        return self.state["evaluate"](expr, arg) if "evaluate" in self.state else None

    def evaluate_all(self, _expr: str) -> list[str]:
        return self.state.get("labels", [])

    def wait_for(self, **_: Any) -> None:
        pass

    def focus(self, **_: Any) -> None:
        pass

    def click(self, **_: Any) -> None:
        self.dom.log.append(("click", self.selector))
        if "on_click" in self.state:
            self.state["on_click"]()

    def hover(self, **_: Any) -> None:
        self.dom.log.append(("hover", self.selector))

    def dblclick(self, **_: Any) -> None:
        self.dom.log.append(("dblclick", self.selector))

    def press(self, key: str, **_: Any) -> None:
        self.dom.log.append(("press", self.selector, key))
        if "on_press" in self.state:
            self.state["on_press"](key)


ROOT_D = '[data-component-path="C.0:3"]'
BOX = f'{ROOT_D} .iaDropdownCommon'
OPTION = 'a.iaDropdownCommon_option[data-label="{}"]'


def _pick(box: dict[str, Any], panel: dict[str, Any], label: str, records: bool) -> Callable[[], None]:
    def pick() -> None:
        if records:
            box["attrs"]["data-selected-labels"] = json.dumps(
                json.loads(box["attrs"]["data-selected-labels"]) + [label])
        panel["visible"] = False  # a single pick closes the list
    return pick


def _dropdown(labels: tuple[str, ...] = ("Auto", "Manual"), records: bool = True) -> _Dom:
    """A ia.input.dropdown: its box opens the options list; an option adds its label to data-selected-labels
    (unless `records` is off) and closes the list."""
    panel: dict[str, Any] = {"visible": False}
    box: dict[str, Any] = {"attrs": {"data-selected-labels": "[]"}, "on_click": lambda: panel.update(visible=True)}
    elements: dict[str, dict[str, Any]] = {
        ROOT_D: {"evaluate": lambda _expr, _arg: False}, BOX: box, act.DROPDOWN_PANEL: panel,
        act.DROPDOWN_OPTION: {"labels": list(labels)}}
    for label in labels:
        elements[OPTION.format(label)] = {"on_click": _pick(box, panel, label, records)}
    return _Dom(elements)


def test_select_opens_the_list_for_each_label_and_checks_what_the_box_shows() -> None:
    steps, _ = act.parse('select root/D "Auto" "Manual"', _widgets())
    dom = _dropdown()
    outcome = act.run(dom, steps, lambda _page: None, {})
    assert (outcome.done, outcome.diagnostics) == (1, [])
    assert [entry[1] for entry in dom.log] == [BOX, OPTION.format("Auto"), BOX, OPTION.format("Manual")]
    assert json.loads(dom.elements[BOX]["attrs"]["data-selected-labels"]) == ["Auto", "Manual"]


def test_select_names_the_options_it_has_when_one_is_missing() -> None:
    steps, _ = act.parse('select root/D "Turbo"', _widgets())
    [failure] = act.run(_dropdown(), steps, lambda _page: None, {}).diagnostics
    assert failure["code"] == "A004" and "no option 'Turbo' (options: Auto, Manual)" in failure["message"]


def test_select_fails_when_the_box_does_not_show_the_label() -> None:
    steps, _ = act.parse('select root/D "Manual"', _widgets())
    [failure] = act.run(_dropdown(records=False), steps, lambda _page: None, {}).diagnostics
    assert "the dropdown shows [] after selecting ['Manual']" in failure["message"]


def test_key_presses_the_page_or_the_editable_inside_a_component() -> None:
    steps, _ = act.parse("key Enter; key root/Title Control+A", _nodes())
    title = '[data-component-path="C.0:0"]'
    inner = f"{title} {act.EDITABLE}"
    dom = _Dom({title: {"evaluate": lambda _expr, _arg: False}, inner: {}})
    outcome = act.run(dom, steps, lambda _page: None, {})
    assert outcome.done == 2 and dom.log == [("keyboard", "Enter"), ("press", inner, "Control+A")]


def test_hover_and_double_click_act_on_the_component() -> None:
    steps, _ = act.parse("hover root/Title; double-click root/Row/Narrow", _nodes())
    title, narrow = '[data-component-path="C.0:0"]', '[data-component-path="C.0:1:0"]'
    dom = _Dom({title: {}, narrow: {}})
    outcome = act.run(dom, steps, lambda _page: None, {})
    assert outcome.done == 2 and dom.log == [("hover", title), ("dblclick", narrow)]


def test_scroll_asks_the_component_to_scroll_and_fails_when_nothing_scrolls() -> None:
    steps, _ = act.parse("scroll root/T 120; scroll root/T bottom", _widgets())
    feed = '[data-component-path="C.0:5"]'
    calls: list[Any] = []

    def scroll(_expr: str, amount: Any) -> dict[str, Any]:
        calls.append(amount)
        return {"scrolled": True, "top": 120}

    outcome = act.run(_Dom({feed: {"evaluate": scroll}}), steps, lambda _page: None, {})
    assert outcome.done == 2 and calls == [120, "bottom"]
    flat = _Dom({feed: {"evaluate": lambda _expr, _arg: {"scrolled": False, "top": 0}}})
    assert "it has nothing to scroll" in act.run(flat, steps, lambda _page: None, {}).diagnostics[0]["message"]


SLIDER = '[data-component-path="C.0:4"]'
HANDLE = f'{SLIDER} [role="slider"]'


def _slider(now: float, step: float = 1.0, low: float = 0.0, high: float = 10.0) -> _Dom:
    """A ia.input.slider whose handle moves `step` per arrow key; Home and End go to the ends of the range."""
    attrs = {"aria-valuemin": str(low), "aria-valuemax": str(high), "aria-valuenow": str(now)}

    def press(key: str) -> None:
        value = float(attrs["aria-valuenow"])
        moved = {"ArrowRight": value + step, "ArrowLeft": value - step, "Home": low, "End": high}[key]
        attrs["aria-valuenow"] = str(min(max(moved, low), high))

    return _Dom({SLIDER: {}, HANDLE: {"attrs": attrs, "on_press": press}})


def test_slide_reaches_the_value_with_arrow_keys_or_the_ends() -> None:
    steps, _ = act.parse("slide root/S 5; slide root/S 0", _widgets())
    dom = _slider(2)
    outcome = act.run(dom, steps, lambda _page: None, {})
    assert outcome.done == 2
    assert [entry[2] for entry in dom.log if entry[0] == "press"] == ["ArrowRight"] * 3 + ["Home"]


def test_slide_fails_on_overshoot_a_frozen_handle_or_a_value_out_of_range() -> None:
    steps, _ = act.parse("slide root/S 4", _widgets())
    passed = act.run(_slider(2, step=3), steps, lambda _page: None, {})
    assert "the slider goes from 2 to 5: 4 is not one of its steps" in passed.diagnostics[0]["message"]
    frozen = act.run(_slider(2, step=0), steps, lambda _page: None, {})
    assert "the slider does not move (disabled?)" in frozen.diagnostics[0]["message"]
    beyond, _ = act.parse("slide root/S 11", _widgets())
    assert "11 is outside 0..10" in act.run(_slider(2), beyond, lambda _page: None, {}).diagnostics[0]["message"]

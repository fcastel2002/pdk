"""pdk.eyes.act: parsing steps against the view's name paths, and running them on a stand-in page."""

from __future__ import annotations

from typing import Any

from pdk.eyes import act
from pdk.validate.tree import build_tree, walk
from tests.test_eyes_rules import _view


def _nodes() -> dict[str, Any]:
    return {node.path: node for node in walk(build_tree(_view()))}


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

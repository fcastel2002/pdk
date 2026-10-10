"""pdk.eyes.measure over the synthetic snapshot of test_eyes_rules (no browser, no gateway)."""

from __future__ import annotations

from pdk.eyes import measure
from pdk.eyes.join import join
from tests.test_eyes_rules import PATH, _dom, _view


def _joined(**changes: dict) -> object:
    dom = _dom()
    for node in dom["nodes"]:
        node.update(changes.get(node["path"], {}))
    return join(_view(), dom, None, PATH)


def _names(query: str) -> list[str]:
    return [part.name for part in measure.select(_joined(), query)]


def test_select_by_name_path_type_pointer_and_all() -> None:
    assert _names("root/Row") == ["root/Row", "root/Row/Narrow"]
    assert _names("root/Row/") == ["root/Row", "root/Row/Narrow"]
    assert _names("root/Ro") == []
    assert _names("ia.display.label") == ["root/Title", "root/Row/Narrow", "root/Box/Inner"]
    assert _names("/root/children/2") == ["root/Box", "root/Box/Inner"]
    assert _names("root/Title, root/Box/Inner") == ["root/Title", "root/Box/Inner"]
    assert len(_names("*")) == 6


def test_line_reports_box_text_and_style_but_not_neutral_defaults() -> None:
    joined = _joined(**{"C.0:0": {"color": "rgb(255, 0, 0)", "font_size": 14.0, "font_weight": "700",
                                  "font_family": '"Noto Sans", sans-serif', "text_align": "start",
                                  "background_color": "rgba(0, 0, 0, 0)", "padding": [0, 0, 0, 0],
                                  "border_width": [0, 0, 0, 0], "display": "block"}})
    text = measure.describe(joined, "root/Title")
    assert text.splitlines()[0] == "measured 'root/Title' @800x600: 1 component"
    assert text.splitlines()[1] == 'root/Title ia.display.label @0,0 800x32 | "Pump" | rgb(255, 0, 0) 14px 700 ' \
                                   "Noto Sans align start"


def test_line_reports_container_padding_border_flex_and_scroll() -> None:
    joined = _joined(**{"C.0:1": {"background_color": "rgb(0, 0, 255)", "padding": [4, 6.25, 4, 6.25],
                                  "border_width": [1, 1, 1, 1], "flex_direction": "column", "row_gap": 8.0,
                                  "column_gap": 8.0, "justify_content": "center", "align_items": "normal",
                                  "scroll_width": 800, "scroll_height": 120, "overflow_y": "auto"}})
    line = measure.line(joined.by_name["root/Row"])
    assert line == "root/Row ia.container.flex @0,32 800x40 | bg rgb(0, 0, 255) | pad 4,6.2,4,6.2 | border 1,1,1,1 " \
                   "| flex column gap 8/8 justify center | content 800x120 in 800x40 (visible/auto)"


def test_absent_and_hidden_components() -> None:
    joined = _joined(**{"C.0:2": {"visible": False, "display": "none"}})
    inner = joined.by_name["root/Box/Inner"]
    inner.dom = None
    assert measure.line(joined.by_name["root/Box"]) == "root/Box ia.container.flex hidden (display none)"
    assert measure.line(inner) == "root/Box/Inner ia.display.label not rendered"


def test_no_match_and_cap() -> None:
    assert "no component matches" in measure.describe(_joined(), "root/Nope")
    joined = _joined()
    joined.parts = joined.parts * 10
    lines = measure.describe(joined, "*").splitlines()
    assert len(lines) == 1 + measure.MAX_PARTS + 1 and lines[-1] == "... 20 more (narrow the query)"

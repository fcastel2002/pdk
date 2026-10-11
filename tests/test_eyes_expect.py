"""pdk.eyes.expect over the synthetic snapshot of test_eyes_rules (no browser, no gateway).

Boxes: root 0,0 800x600; Title 0,0 800x32 "Pump"; Row 0,32 800x40; Row/Narrow 0,32 200x40 "Speed setpoint";
Box 0,72 800x40; Box/Inner 0,72 800x32 "Hidden".
"""

from __future__ import annotations

import pytest

from pdk.diagnostics import CODES
from pdk.eyes import expect
from pdk.eyes.join import Joined, join
from tests.test_eyes_rules import PATH, _dom, _scrolled, _view


def _joined(**changes: dict) -> Joined:
    dom = _dom()
    for node in dom["nodes"]:
        node.update(changes.get(node["path"], {}))
    return join(_view(), dom, None, PATH)


def _codes(text: str, joined: Joined | None = None) -> list[str]:
    return [d["code"] for d in expect.check(joined or _joined(), text).diagnostics]


@pytest.mark.parametrize("text", [
    "root/Row below root/Title",
    "root/Row below root/Title gap 0",
    "root/Box below root/Title gap 40",
    "root/Title above root/Box",
    "root/Row/Narrow inside root/Row",
    "root/Row/Narrow aligned left root/Row",
    "root/Row/Narrow aligned top root/Row",
    "root/Title width 100%",
    "root/Title width 800",
    "root/Row/Narrow width 25%",
    "root/Row/Narrow width <= 200px",
    "root/Title height 32.6",
    "root/Box y >= 72",
    'root/Title text "Pump"',
    'root/Row/Narrow text ~ "setpoint"',
    "root/Title visible",
    "root/Title/ visible",
])
def test_holding_expectations_pass(text: str) -> None:
    assert _codes(text) == []


@pytest.mark.parametrize("text", [
    "root/Title below root/Row",
    "root/Box below root/Title gap 8",
    "root/Title left-of root/Row/Narrow",
    "root/Row inside root/Row/Narrow",
    "root/Row/Narrow aligned right root/Row",
    "root/Row/Narrow width 100%",
    "root/Title height > 32",
    'root/Title text "Pump 1"',
    'root/Title text ~ "Motor"',
    "root/Title hidden",
])
def test_broken_expectations_fail_on_the_subject(text: str) -> None:
    found = expect.check(_joined(), text).diagnostics
    assert [d["code"] for d in found] == ["A001"]
    subject = text.split()[0].rstrip("/")
    assert found[0]["component"] == subject and found[0]["pointer"].startswith("/root") and found[0]["hint"]


def test_messages_carry_the_measured_values() -> None:
    joined = _joined()
    [gap] = expect.check(joined, "root/Box below root/Title gap 8").diagnostics
    assert gap["message"] == "root/Box below root/Title gap 8: the gap is 40px (top 72 vs bottom 32)"
    [width] = expect.check(joined, "root/Row/Narrow width 100%").diagnostics
    assert width["message"] == "root/Row/Narrow width 100%: width is 200px, expected = 800px (100%)"
    [order] = expect.check(joined, "root/Title below root/Row").diagnostics
    assert order["message"] == "root/Title below root/Row: it is not (top 0 vs bottom 72)"
    assert "root/Title @0,0 800x32" in order["detail"]


def test_style_expectations_normalize_colors_and_weights() -> None:
    joined = _joined(**{"C.0:0": {"color": "rgb(50, 50, 50)", "background_color": "rgba(0, 0, 0, 0)",
                                  "font_size": 16.0, "font_weight": "700"}})
    ok = "root/Title color #323232; root/Title color rgb(50,50,50); root/Title bg transparent; " \
         "root/Title font-size 16; root/Title font-size >= 14px; root/Title font-weight bold"
    outcome = expect.check(joined, ok)
    assert outcome.diagnostics == [] and outcome.summary() == "expect: 6/6 passed"
    [color] = expect.check(joined, "root/Title color #ff0000").diagnostics
    assert color["code"] == "A001" and color["message"].endswith("it is rgb(50, 50, 50)")
    assert _codes("root/Title font-weight 400", joined) == ["A001"]


def test_not_rendered_and_hidden_components() -> None:
    joined = _joined(**{"C.0:2": {"visible": False}})
    joined.by_name["root/Box/Inner"].dom = None
    assert _codes("root/Box hidden; root/Box/Inner hidden", joined) == []
    [missing] = expect.check(joined, "root/Box/Inner below root/Title").diagnostics
    assert missing["code"] == "A001" and missing["message"].endswith("root/Box/Inner is not rendered")
    [other] = expect.check(joined, "root/Title above root/Box/Inner").diagnostics
    assert other["message"].endswith("root/Box/Inner is not rendered")


def test_unknown_components_and_malformed_expectations() -> None:
    [unknown] = expect.check(_joined(), "root/Titel visible").diagnostics
    assert unknown["code"] == "A003" and "did you mean root/Title?" in unknown["message"]
    assert _codes("root/Title below root/Nope") == ["A003"]
    for bad in ("root/Title", "root/Title glows", "root/Title width wide", "root/Title x fifty",
                "root/Title below", "root/Title aligned middle root/Row", 'root/Title text "open'):
        assert _codes(bad) == ["A002"], bad


def test_split_and_summary() -> None:
    assert expect.split('a visible; b text "x; y"\nc hidden;;') == ["a visible", 'b text "x; y"', "c hidden"]
    outcome = expect.check(_joined(), "root/Title visible\nroot/Title hidden; root/Nope visible")
    assert outcome.summary() == "expect: 1/3 passed"
    assert {"A001", "A002", "A003"} <= CODES.keys()


def test_scrolls_and_in_view() -> None:
    joined = join(_view(), _scrolled(_dom(), "hidden", "auto"), None, PATH)
    assert _codes("root/Row scrolls; root/Row scrolls y; root/Title in-view; root/Row/Narrow visible", joined) == []
    [axis] = expect.check(joined, "root/Row scrolls x").diagnostics
    assert axis["message"].endswith("overflow-x is hidden")
    [fits] = expect.check(joined, "root/Box scrolls").diagnostics
    assert fits["message"].endswith("overflow-x is visible; overflow-y is visible")
    [away] = expect.check(joined, "root/Row/Narrow in-view").diagnostics
    assert away["code"] == "A001" and away["message"].endswith("118px of it is out of sight")
    roomy = join(_view(), _scrolled(_dom(), "visible", "auto"), None, PATH)
    roomy.by_name["root/Row"].dom["scroll_height"] = 40
    [roomy_fits] = expect.check(roomy, "root/Row scrolls y").diagnostics
    assert roomy_fits["message"].endswith("its content fits along y (40 in 40)")
    assert _codes("root/Title in-view x; root/Title scrolls z") == ["A002", "A002"]


def test_x_and_y_start_at_the_parents_padding_box() -> None:
    narrow = {"left": 89, "x": 89, "right": 289, "top": 42, "y": 42, "bottom": 82, "width": 200, "height": 40}
    joined = _joined(**{"C.0:1": {"border_width": [2, 0, 0, 10]}, "C.0:1:0": {"bounds": narrow}})
    # Row starts at 0,32 with a 10px left and 2px top border: Narrow's 89,42 is 79,8 inside it, 10% of its 790px
    assert _codes("root/Row/Narrow x 79; root/Row/Narrow y 8; root/Row/Narrow x 10%; root/Row y 32; root/Title x 0",
                  joined) == []
    [far] = expect.check(joined, "root/Row/Narrow x 150").diagnostics
    assert far["message"] == "root/Row/Narrow x 150: x is 79px from root/Row, expected = 150px"


def test_gap_comparisons_and_ranges() -> None:
    # Box is 40px below Title
    assert _codes("root/Box below root/Title gap >= 8; root/Box below root/Title gap 30..50; "
                  "root/Box below root/Title gap 40px; root/Box below root/Title gap 40..40") == []
    assert _codes("root/Box below root/Title gap < 40; root/Box below root/Title gap 0..30") == ["A001", "A001"]
    for bad in ("root/Box below root/Title gap 50..30", "root/Box below root/Title gap",
                "root/Box below root/Title gap ~ 8", "root/Row inside root/Box gap 0"):
        assert _codes(bad) == ["A002"], bad


def test_inside_a_scroll_container_counts_its_content() -> None:
    joined = join(_view(), _scrolled(_dom(), "hidden", "auto"), None, PATH)  # Narrow at 150..190, Row 32..72
    assert _codes("root/Row/Narrow inside root/Row", joined) == []
    joined.by_name["root/Row"].dom.update(scroll_height=140, scroll_top=10)  # content 22..162
    [out] = expect.check(joined, "root/Row/Narrow inside root/Row").diagnostics
    assert out["message"].endswith("it sticks out 28px of root/Row's scrolled content")
    flat = join(_view(), _scrolled(_dom(), "hidden", "hidden"), None, PATH)  # clipped, not scrolled: its box counts
    assert expect.check(flat, "root/Row/Narrow inside root/Row").diagnostics[0]["message"].endswith("sticks out 118px")


def test_enabled_and_disabled() -> None:
    joined = _joined(**{"C.0:0": {"disabled": True}})
    assert _codes("root/Title disabled; root/Row enabled", joined) == []
    [failure] = expect.check(joined, "root/Title enabled").diagnostics
    assert failure["code"] == "A001" and failure["message"].endswith("it is disabled")


def test_popups_open_and_closed() -> None:
    joined = _joined()
    joined.popups["detail"] = "Popups/Detail"
    assert _codes("popup detail open; popup other closed", joined) == []
    [failure] = expect.check(joined, "popup other open").diagnostics
    assert failure["code"] == "A001" and "open popups: detail" in failure["message"]
    assert _codes("popup detail maybe", joined) == ["A002"]


def test_percent_sizes_are_shares_of_the_parents_content_box() -> None:
    # Row is 800 wide; with 2px borders and 298px of padding on each side its content box is 200 wide, like CSS.
    joined = _joined(**{"C.0:1": {"border_width": [0, 2, 0, 2], "padding": [0, 298, 0, 298]}})
    assert _codes("root/Row/Narrow width 100%; root/Row/Narrow width >= 50%", joined) == []
    [wide] = expect.check(joined, "root/Row/Narrow width 50%").diagnostics
    assert wide["message"] == "root/Row/Narrow width 50%: width is 200px, expected = 100px (50%)"


def test_embedded_views_are_read_through_their_rendered_text() -> None:
    # An embedded view or repeater has no text of its own; the eyes give it the text its views render.
    joined = _joined(**{"C.0:2": {"inner_text": "Summary\nArea North\nP-205"}})
    assert _codes('root/Box text ~ "Area North P-205"', joined) == []
    [failure] = expect.check(joined, 'root/Box text ~ "P-101"').diagnostics
    assert failure["code"] == "A001" and "'Summary Area North P-205'" in failure["message"]
    # An input's value comes the same way: its own text is empty.
    assert _codes('root/Box text "P-205"', _joined(**{"C.0:2": {"inner_text": "P-205"}})) == []


def _titled(**scopes: dict) -> Joined:
    """Joined with a gateway tree on root/Title: props, position, custom and meta as the gateway reports them."""
    tree = {"props": {"text": "Pump 1", "value": 35, "flag": True, "style": {"color": "red"},
                      "items": [{"label": "a"}, {"label": "b"}]},
            "position": {"x": 0, "y": 0}, "custom": {"total": 3}, "meta": {"name": "Title"}}
    tree.update(scopes)
    joined = _joined()
    joined.by_name["root/Title"].live = tree
    return joined


def test_prop_operators_on_numbers() -> None:
    joined = _titled()
    assert _codes("root/Title prop value = 35; root/Title prop value != 36; root/Title prop value >= 35; "
                  "root/Title prop value <= 35; root/Title prop value > 34; root/Title prop value < 36",
                  joined) == []
    found = expect.check(joined, "root/Title prop value >= 40; root/Title prop value > 35; "
                                 "root/Title prop value != 35; root/Title prop value < 35; "
                                 "root/Title prop value = 36")
    assert [d["message"] for d in found.diagnostics] == [
        "root/Title prop value >= 40: it is 35", "root/Title prop value > 35: it is 35",
        "root/Title prop value != 35: it is 35", "root/Title prop value < 35: it is 35",
        "root/Title prop value = 36: it is 35"]
    assert all(d["code"] == "A001" and d["component"] == "root/Title" and d["hint"] for d in found.diagnostics)


def test_prop_defaults_to_props_and_explicit_scopes_read_the_others() -> None:
    joined = _titled()
    assert _codes("root/Title prop value = 35; root/Title prop props.value = 35; root/Title prop custom.total = 3; "
                  "root/Title prop position.x = 0; root/Title prop meta.name = Title", joined) == []
    [failure] = expect.check(joined, "root/Title prop total = 3").diagnostics
    assert failure["code"] == "A001"
    assert failure["message"] == ("root/Title prop total = 3: it has no props.total "
                                  "(it has: text, value, flag, style, items)")


def test_prop_nested_paths_list_indices_and_json() -> None:
    joined = _titled()
    assert _codes("root/Title prop style.color = red; root/Title prop items[1].label = b; "
                  "root/Title prop items.0.label = a; root/Title prop items[1] = '{\"label\":\"b\"}'",
                  joined) == []
    assert _codes("root/Title prop style = '{\"color\":\"red\"}'", joined) == []
    [missing] = expect.check(joined, "root/Title prop items[2].label = c").diagnostics
    assert missing["message"] == ("root/Title prop items[2].label = c: it has no props.items.2.label "
                                  "(it has: text, value, flag, style, items)")
    [wrong] = expect.check(joined, "root/Title prop style = red").diagnostics
    assert wrong["message"] == 'root/Title prop style = red: it is {"color": "red"}'


def test_prop_bool_is_not_a_number() -> None:
    joined = _titled()
    assert _codes("root/Title prop flag = true; root/Title prop flag != 1", joined) == []
    [failure] = expect.check(joined, "root/Title prop flag = 1").diagnostics
    assert failure["message"] == "root/Title prop flag = 1: it is true"
    assert _codes("root/Title prop flag > 0", joined) == ["A001"]


def test_prop_contains_text_and_searches_json() -> None:
    joined = _titled()
    assert _codes('root/Title prop text ~ "Pump"; root/Title prop items ~ label', joined) == []
    [failure] = expect.check(joined, "root/Title prop text ~ Motor").diagnostics
    assert failure["message"] == 'root/Title prop text ~ Motor: it is "Pump 1"'
    [text] = expect.check(joined, "root/Title prop text > 3").diagnostics
    assert text["message"] == 'root/Title prop text > 3: it is "Pump 1", not a number'
    [expected] = expect.check(joined, "root/Title prop value > abc").diagnostics
    assert expected["message"] == "root/Title prop value > abc: the value abc is not a number"


def test_prop_without_a_gateway_tree_fails() -> None:
    [failure] = expect.check(_joined(), "root/Title prop value = 35").diagnostics
    assert failure["code"] == "A001" and failure["component"] == "root/Title"
    assert failure["message"] == "root/Title prop value = 35: the gateway tree is not available for it"
    assert failure["hint"] == expect.HINTS["prop"]


def test_prop_malformed_expectations() -> None:
    joined = _titled()
    for bad in ("root/Title prop", "root/Title prop value glows 3", "root/Title prop a..b = 1", "root/Title prop [0]"):
        assert _codes(bad, joined) == ["A002"], bad


def test_view_props_read_the_routed_view() -> None:
    joined = _joined()
    joined.live_view = {"params": {"unit": "P-101"}, "custom": {"total": 3}}
    assert _codes("view prop custom.total = 3; view prop params.unit = P-101; view prop custom.total >= 2",
                  joined) == []
    [failure] = expect.check(joined, "view prop custom.total > 3").diagnostics
    assert failure["code"] == "A001" and failure["pointer"] == "" and failure["component"] == ""
    assert failure["message"] == "view prop custom.total > 3: it is 3"
    assert failure["hint"] == "Check the view's params/custom bindings and what writes them."
    [missing] = expect.check(joined, "view prop custom.limit = 3").diagnostics
    assert missing["message"] == "view prop custom.limit = 3: it has no custom.limit (it has: total)"
    [no_tree] = expect.check(_joined(), "view prop custom.total = 3").diagnostics
    assert no_tree["message"] == "view prop custom.total = 3: the gateway tree is not available for it"


def test_view_prop_must_name_params_or_custom() -> None:
    joined = _joined()
    joined.live_view = {"params": {}, "custom": {"total": 3}}
    [bad] = expect.check(joined, "view prop props.x = 1").diagnostics
    assert bad["code"] == "A002" and "params or custom" in bad["message"]
    assert _codes("view prop = 1", joined) == ["A002"]


DETAIL = "http://127.0.0.1:8088/data/perspective/client/proj/detail"


def _at(url: str, routed: str | None = None) -> Joined:
    """The synthetic snapshot measured at `url`; `routed` is the view the gateway lists at the routed mount."""
    dom = _dom()
    dom["url"] = url
    page = {"views": [{"resourcePath": routed, "mountPath": "C"}]} if routed else None
    return join(_view(), dom, page, PATH)


@pytest.mark.parametrize("url, expected", [
    ("http://127.0.0.1:8088/data/perspective/client/my%20proj/pdk/PDK/A?x=1", "/pdk/PDK/A?x=1"),
    ("http://127.0.0.1:8088/data/perspective/client/proj", "/"),
    ("https://example.com/a", "https://example.com/a"),
    ("", ""),
])
def test_page_location_is_the_path_inside_the_project(url: str, expected: str) -> None:
    assert expect.page_location(url) == expected


def test_page_path_matches_exactly_or_as_a_substring() -> None:
    joined = _at(DETAIL)
    assert _codes("page /detail", joined) == []
    assert _codes("page = detail/", joined) == []
    assert _codes("page ~ det", joined) == []
    [wrong] = expect.check(joined, "page /other").diagnostics
    assert wrong["code"] == "A001" and "it is /detail" in wrong["message"]
    assert _codes("page = a b", joined) == ["A002"]


def test_page_shows_checks_the_view_routed_on_the_page() -> None:
    assert _codes("page shows PDK/X", _at(DETAIL, routed="PDK/X")) == []
    [wrong] = expect.check(_at(DETAIL, routed="PDK/Y"), "page shows PDK/X").diagnostics
    assert wrong["code"] == "A001" and "it shows PDK/Y" in wrong["message"]
    [unknown] = expect.check(_at(DETAIL), "page shows PDK/X").diagnostics
    assert unknown["code"] == "A001"

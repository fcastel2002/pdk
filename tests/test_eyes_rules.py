"""pdk.eyes.join and pdk.eyes.rules over synthetic snapshots (no browser, no gateway, no real project views)."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from pdk.diagnostics import CODES, estimate_tokens, render
from pdk.eyes.join import join
from pdk.eyes.rules import evaluate

PATH = "PDK/Test"


def _view() -> dict[str, Any]:
    label = {"type": "ia.display.label", "position": {"basis": "32px"}}
    return {
        "custom": {}, "params": {"speed": 0}, "props": {},
        "propConfig": {"params.speed": {"paramDirection": "input"}},
        "root": {
            "type": "ia.container.flex", "meta": {"name": "root"}, "props": {"direction": "column"},
            "children": [
                {**label, "meta": {"name": "Title"}, "props": {"text": "Pump"},
                 "propConfig": {"props.text": {"binding": {"type": "tag", "config": {"tagPath": "[default]P/Name"}}}}},
                {"type": "ia.container.flex", "meta": {"name": "Row"}, "position": {"basis": "40px"},
                 "children": [{**label, "meta": {"name": "Narrow"}, "props": {"text": "Speed setpoint"},
                               "events": {"system": {"onStartup": {"type": "script", "scope": "G",
                                                                   "config": {"script": "\tpass"}}}}}]},
                {"type": "ia.container.flex", "meta": {"name": "Box"}, "position": {"basis": "40px"},
                 "children": [{**label, "meta": {"name": "Inner"}, "props": {"text": "Hidden"}}]},
            ],
        },
    }


def _node(path: str, kind: str, parent: str | None, box: tuple[float, float, float, float], *, text: str | None = None,
          clip: tuple[float, float, float, float] | None = None, overflow: str = "visible",
          scroll: tuple[float, float] | None = None, text_box: tuple[float, float, float, float] | None = None,
          overlay: str | None = None) -> dict[str, Any]:
    x, y, w, h = box

    def rect(r: tuple[float, float, float, float]) -> dict[str, float]:
        return {"x": r[0], "y": r[1], "left": r[0], "top": r[1], "right": r[0] + r[2], "bottom": r[1] + r[3],
                "width": r[2], "height": r[3]}

    return {
        "path": path, "type": kind, "parent": parent, "bounds": rect(box), "client_width": w, "client_height": h,
        "scroll_width": scroll[0] if scroll else w, "scroll_height": scroll[1] if scroll else h, "visible": True,
        "display": "flex", "position": "relative", "overflow_x": overflow, "overflow_y": overflow,
        "white_space": "normal", "text_overflow": "clip", "text": text,
        "text_rects": [rect(text_box)] if text_box else ([rect((x, y, min(w, 50), min(h, 20)))] if text else []),
        "clip_rect": rect(clip) if clip else None, "overlay": overlay,
    }


def _dom() -> dict[str, Any]:
    nodes = [
        _node("C.0", "ia.container.flex", None, (0, 0, 800, 600), overflow="hidden", clip=(0, 0, 800, 600)),
        _node("C.0:0", "ia.display.label", "C.0", (0, 0, 800, 32), text="Pump", clip=(0, 0, 800, 600)),
        _node("C.0:1", "ia.container.flex", "C.0", (0, 32, 800, 40), clip=(0, 0, 800, 600)),
        _node("C.0:1:0", "ia.display.label", "C.0:1", (0, 32, 200, 40), text="Speed setpoint", clip=(0, 0, 800, 600)),
        _node("C.0:2", "ia.container.flex", "C.0", (0, 72, 800, 40), clip=(0, 0, 800, 600)),
        _node("C.0:2:0", "ia.display.label", "C.0:2", (0, 72, 800, 32), text="Hidden", clip=(0, 0, 800, 600)),
    ]
    return {"viewport": {"width": 800, "height": 600}, "nodes": nodes, "console": []}


def _page(**qualities: list[dict[str, Any]]) -> dict[str, Any]:
    def comp(address: str, kind: str, name: str, children: list | None = None) -> dict[str, Any]:
        node: dict[str, Any] = {"address": address, "type": kind, "name": name, "path": name}
        if address in qualities:
            node["qualities"] = qualities[address]
        if address == "0:0":
            node["bindings"] = {"props.text": {"type": "tag", "enabled": True, "transforms": 0}}
        if children:
            node["children"] = children
        return node

    root = comp("0", "ia.container.flex", "root", [
        comp("0:0", "ia.display.label", "root/Title"),
        comp("0:1", "ia.container.flex", "root/Row", [comp("0:1:0", "ia.display.label", "root/Row/Narrow")]),
        comp("0:2", "ia.container.flex", "root/Box", [comp("0:2:0", "ia.display.label", "root/Box/Inner")]),
    ])
    view = {"instance": f"{PATH}@C", "resourcePath": PATH, "mountPath": "C", "root": root}
    if "view" in qualities:
        view["qualities"] = qualities["view"]
    return {"session": "s", "page": "p", "views": [view]}


def _codes(diagnostics: list[dict[str, Any]]) -> list[tuple[str, str]]:
    return sorted((d["code"], d["pointer"]) for d in diagnostics)


def _run(dom: dict[str, Any] | None = None, page: dict[str, Any] | None = None, view: dict[str, Any] | None = None,
         **kwargs: Any) -> list[dict[str, Any]]:
    joined = join(view or _view(), dom or _dom(), page if page is not None else _page(), PATH)
    return evaluate(joined, **kwargs)


def test_every_code_is_registered() -> None:
    eyes = {"V001", "V002", "V003", "V007", "V010", "V011", "Q001", "Q002", "Q003", "L001", "C001", "X001", "X002"}
    assert eyes <= set(CODES)


def test_a_clean_page_has_no_anomalies() -> None:
    assert _run() == []


def test_join_lines_up_pointer_dom_and_gateway() -> None:
    joined = join(_view(), _dom(), _page(), PATH)
    narrow = joined.by_name["root/Row/Narrow"]
    assert narrow.pointer == "/root/children/1/children/0" and narrow.dom_path == "C.0:1:0"
    assert narrow.dom["text"] == "Speed setpoint" and narrow.live["name"] == "root/Row/Narrow"
    assert not joined.mismatches and joined.foreign == 0


def test_v001_text_cut_by_its_own_box() -> None:
    dom = _dom()
    dom["nodes"][3].update(overflow_x="hidden", overflow_y="hidden", text_overflow="ellipsis",
                           clip_rect=_node("x", "", None, (0, 32, 200, 40), text="t")["bounds"])
    dom["nodes"][3]["text_rects"][0].update(right=480, width=480)
    found = _run(dom)
    assert _codes(found) == [("V001", "/root/children/1/children/0")]
    assert found[0]["severity"] == "warning" and "ellipsis" in found[0]["message"] and "px wide" in found[0]["message"]


def test_v001_ignores_text_only_the_root_clips() -> None:
    dom = _dom()
    dom["nodes"][3]["text_rects"][0].update(right=900, width=900)  # past the root's 800 px, nothing else clips
    assert [d["code"] for d in _run(dom)] == []


def test_v002_children_overflow_a_container() -> None:
    dom = _dom()
    dom["nodes"][2]["scroll_width"] = 1000
    dom["nodes"][3]["bounds"].update(right=1000, width=1000)
    found = _run(dom)
    assert _codes(found) == [("V002", "/root/children/1")] and "Narrow" in found[0]["message"]


def _scrolled(dom: dict[str, Any], x: str, y: str) -> dict[str, Any]:
    """Row scrolls (or not) per axis; Narrow sits below it, past its bottom edge, its text with it."""
    row, narrow = dom["nodes"][2], dom["nodes"][3]
    row.update(overflow_x=x, overflow_y=y, scroll_height=160)
    narrow["bounds"].update(top=150, y=150, bottom=190)
    narrow["text_rects"][0].update(top=150, y=150, bottom=170)
    narrow["clip_rect"] = _node("x", "", None, (0, 32, 800, 40))["bounds"]
    narrow["cut_rect"] = _node("x", "", None, (0, -1e6, 800, 2e6))["bounds"] if y == "auto" else narrow["clip_rect"]
    return dom


def test_scrolled_content_is_neither_overflow_nor_cut_text() -> None:
    assert _run(_scrolled(_dom(), "hidden", "auto")) == []
    found = _run(_scrolled(_dom(), "auto", "hidden"))  # it scrolls the other way: the content spills along y
    assert [d["code"] for d in found] == ["V002", "V001"]


def test_v003_viewport_overflow_names_the_deepest_contributor() -> None:
    dom = _dom()
    dom["nodes"][0]["scroll_height"] = 900
    dom["nodes"][5]["bounds"].update(top=700, bottom=900)
    found = _run(dom)
    assert _codes(found) == [("V003", "/root/children/2/children/0")] and "800x600" in found[0]["message"]


def test_v007_collapsed_container_reports_once() -> None:
    dom = _dom()
    dom["nodes"][4]["bounds"].update(height=0, bottom=72)
    dom["nodes"][5]["bounds"].update(height=0, bottom=72)
    dom["nodes"][5].update(clip_rect=_node("x", "", None, (0, 72, 800, 0))["bounds"])
    found = _run(dom)
    assert _codes(found) == [("V007", "/root/children/2")] and "1 children hidden" in found[0]["message"]


def test_v007_ignores_empty_content_sized_components() -> None:
    dom = _dom()
    dom["nodes"][1].update(text=None, text_rects=[])
    dom["nodes"][1]["bounds"].update(height=0)
    assert _run(dom) == []


def test_v010_reports_the_topmost_missing_component() -> None:
    dom = _dom()
    dom["nodes"] = [n for n in dom["nodes"] if not n["path"].startswith("C.0:2")]
    assert _codes(_run(dom)) == [("V010", "/root/children/2")]


def test_v010_respects_display_false() -> None:
    view = _view()
    view["root"]["children"][2]["position"]["display"] = False
    dom = _dom()
    dom["nodes"] = [n for n in dom["nodes"] if not n["path"].startswith("C.0:2")]
    assert _run(dom, view=view) == []


def _with_embed(failed: int) -> tuple[dict[str, Any], dict[str, Any]]:
    view = _view()
    view["root"]["children"].append({"type": "ia.display.view", "meta": {"name": "Embed"},
                                     "position": {"basis": "40px"}, "props": {"path": "PDK/Missing"}})
    dom = _dom()
    embed = _node("C.0:3", "ia.display.view", "C.0", (0, 112, 800, 40), clip=(0, 0, 800, 600))
    embed["failed_views"] = failed
    dom["nodes"].append(embed)
    return view, dom


def test_v011_points_at_the_path_of_an_embedded_view_that_did_not_load() -> None:
    view, dom = _with_embed(failed=2)
    found = _run(dom, view=view)
    assert _codes(found) == [("V011", "/root/children/3/props/path")]
    assert "PDK/Missing" in found[0]["message"] and found[0]["severity"] == "error"


def test_v011_is_silent_when_the_embedded_view_loads() -> None:
    view, dom = _with_embed(failed=0)
    assert _run(dom, view=view) == []


def test_qualities_point_at_the_binding() -> None:
    page = _page(**{"0:0": [{"path": "props.text", "quality": "Bad_NotFound", "level": "Bad",
                             "message": "Path '[default]P/Name' not found."}],
                    "0:1:0": [{"path": "props.text", "quality": "Error_ScriptEval", "level": "Error",
                               "message": "Traceback (most recent call last):\n  File x\nValueError: boom\n"},
                              {"path": "props.text.inner", "quality": "Error_ScriptEval", "level": "Error"}],
                    "0:2:0": [{"path": "props.text", "quality": "Uncertain_InitialValue", "level": "Uncertain"}]})
    found = {d["code"]: d for d in _run(page=page)}
    assert found["Q001"]["pointer"] == "/root/children/0/propConfig/props.text"
    assert "tag binding" in found["Q001"]["message"] and "not found" in found["Q001"]["message"]
    assert "[default]P/Name" in found["Q001"]["detail"]
    assert found["Q002"]["pointer"] == "/root/children/1/children/0" and "ValueError: boom" in found["Q002"]["message"]
    assert found["Q003"]["severity"] == "warning"
    assert len(_run(page=page)) == 3  # the nested Error under props.text is not repeated


def test_view_level_quality() -> None:
    page = _page(view=[{"path": "params.speed", "quality": "Bad_NotFound", "level": "Bad"}])
    found = _run(page=page)
    assert _codes(found) == [("Q001", "/propConfig/params.speed")] and found[0]["component"] == ""


def test_without_the_gateway_tree_overlays_stand_in() -> None:
    dom = _dom()
    dom["nodes"][1]["overlay"] = "error"
    found = _run(dom, page={}, degraded_reasons=["Perspective's session API is unavailable"])
    assert _codes(found) == [("Q002", "/root/children/0"), ("X001", "")]


def test_l001_attributes_logged_script_errors() -> None:
    event = {"level": "WARN", "logger": "perspective.actions.script",
             "message": f"Error running action 'system.onStartup' on {PATH}@C/root/Row/Narrow: Traceback ...",
             "mdc": {"component": "root/Row/Narrow", "view": f"{PATH}@C", "project-name": "p"},
             "error": {"class": "JythonExecException",
                       "message": "Traceback (most recent call last):\nRuntimeError: x"}}
    found = _run(logs=[event, dict(event)])
    assert _codes(found) == [("L001", "/root/children/1/children/0/events/system/onStartup")]
    assert found[0]["message"] == "system.onStartup raised: RuntimeError: x" and found[0]["severity"] == "error"


def test_c001_browser_errors() -> None:
    entries = [{"type": "pageerror", "text": "TypeError: x is undefined"}, {"type": "error", "text": "404 icon"},
               {"type": "error", "text": "404 icon"}, {"type": "warning", "text": "deprecated"}]
    found = _run(console_entries=entries)
    assert [(d["code"], d["severity"]) for d in found] == [("C001", "error"), ("C001", "warning")]


def test_x002_a_page_out_of_sync_reports_only_that() -> None:
    dom = _dom()
    dom["nodes"][1]["type"] = "ia.input.button"
    dom["nodes"][0]["scroll_height"] = 2000
    found = _run(dom)
    assert [d["code"] for d in found] == ["X002"] and "ia.input.button" in found[0]["message"]


def test_embedded_and_dock_components_are_foreign() -> None:
    dom = _dom()
    dom["nodes"].append(_node("C.0:0$0:0", "ia.display.label", "C.0:0", (0, 0, 10, 10)))
    joined = join(_view(), dom, _page(), PATH)
    assert joined.foreign == 1 and not joined.mismatches


@pytest.mark.parametrize("count", [40, 300])
def test_report_stays_in_budget(count: int) -> None:
    view = _view()
    leaf = view["root"]["children"][0]
    view["root"]["children"] = [copy.deepcopy({**leaf, "meta": {"name": f"L{i}"}}) for i in range(count)]
    dom = {"viewport": {"width": 800, "height": 600}, "console": [], "nodes": [
        _node("C.0", "ia.container.flex", None, (0, 0, 800, 600))]}
    issues = {}
    for i in range(count):
        dom["nodes"].append(_node(f"C.0:{i}", "ia.display.label", "C.0", (0, i, 800, 1), text="x"))
        issues[f"0:{i}"] = [{"path": "props.text", "quality": "Bad_NotFound", "level": "Bad", "message": f"T{i}"}]
    page = {"views": [{"resourcePath": PATH, "mountPath": "C", "root": {
        "address": "0", "type": "ia.container.flex", "children": [
            {"address": f"0:{i}", "type": "ia.display.label", "qualities": issues[f"0:{i}"]} for i in range(count)]}}]}
    text = render(_run(dom, page, view), title=PATH)
    assert estimate_tokens(text) <= 1500 and f"{count} errors" in text


def test_a_popup_joins_under_its_mount_with_prefixed_names() -> None:
    dom = _dom()
    for node in dom["nodes"]:
        node["path"] = node["path"].replace("C.", "Pdetail.", 1)
        node["parent"] = node["parent"] and node["parent"].replace("C.", "Pdetail.", 1)
    dom["nodes"] = [n for n in dom["nodes"] if not n["path"].startswith("Pdetail.0:2")]
    joined = join(_view(), dom, None, "Popups/Detail", mount="Pdetail", prefix="popup:detail/")
    assert joined.root_path == "Pdetail.0" and joined.mismatches == []
    assert joined.by_name["popup:detail/root/Title"].dom_path == "Pdetail.0:0"
    [missing] = evaluate(joined)
    assert (missing["code"], missing["pointer"], missing["component"]) == ("V010", "/root/children/2",
                                                                         "popup:detail/root/Box")


def test_log_events_go_to_the_view_instance_that_logged_them() -> None:
    from pdk.eyes.inspect import _mounted

    events = [{"mdc": {"view": "Main@C"}}, {"mdc": {"view": "Popups/Detail@Pdetail"}}, {"mdc": {}}]
    assert _mounted(events, "Main", "C") == [events[0]]
    assert _mounted(events, "Popups/Detail", "Pdetail") == [events[1]]


def _repeater(props: dict[str, Any], *, client: tuple[float, float] = (1366, 120),
              scroll: tuple[float, float] = (4800, 800)) -> list[dict[str, Any]]:
    """The V012 findings for a flex repeater (the first child of root) with this DOM box and scroll size."""
    view = {"custom": {}, "params": {}, "props": {}, "propConfig": {},
            "root": {"type": "ia.container.flex", "meta": {"name": "root"}, "props": {"direction": "column"},
                     "children": [{"type": "ia.display.flex-repeater", "meta": {"name": "Feed"}, "props": props}]}}
    dom = {"viewport": {"width": 1366, "height": 768}, "console": [], "nodes": [
        _node("C.0", "ia.container.flex", None, (0, 0, 1366, 768), overflow="hidden", clip=(0, 0, 1366, 768)),
        _node("C.0:0", "ia.display.flex-repeater", "C.0", (0, 0, client[0], client[1]), scroll=scroll)]}
    return [d for d in evaluate(join(view, dom, None, PATH)) if d["code"] == "V012"]


def test_v012_flags_a_default_row_repeater_whose_instances_are_taller_than_it() -> None:
    [finding] = _repeater({})
    assert finding["severity"] == "warning"
    assert "taller" in finding["message"] and "800 px in 120" in finding["message"]


def test_v012_checks_the_width_of_a_column_repeater() -> None:
    assert _repeater({"direction": "column"}, scroll=(1366, 800)) == []
    [wide] = _repeater({"direction": "column"}, scroll=(1600, 800))
    assert "wider" in wide["message"] and "1600 px in 1366" in wide["message"]


def test_v012_wrapping_row_repeater_checks_the_width() -> None:
    [wide] = _repeater({"wrap": "wrap"}, scroll=(1600, 800))
    assert "wider" in wide["message"] and "1600 px in 1366" in wide["message"]


def test_v012_ignores_sub_pixel_overflow_on_the_checked_axis() -> None:
    assert _repeater({}, scroll=(4800, 120.4)) == []
    assert len(_repeater({}, scroll=(4800, 121))) == 1


def _contrast(color: str, background: str | None, **dom: Any) -> list[dict[str, Any]]:
    """The V013 findings for the Title label drawn in `color` over `background`, colors the view chose."""
    page = _dom()
    page["nodes"][1].update({"color": color, "text_bg": background, "font_size": 14, "font_weight": "400", **dom})
    view = _view()
    view["root"]["children"][0]["props"]["style"] = {"color": color}
    return [d for d in _run(page, view=view) if d["code"] == "V013"]


def test_v013_leaves_the_themes_colors_alone() -> None:
    page = _dom()
    page["nodes"][1].update({"color": "rgb(204, 204, 204)", "text_bg": "rgb(255, 255, 255)", "font_size": 14,
                             "font_weight": "400"})
    assert [d for d in _run(page) if d["code"] == "V013"] == []  # no color, background or class in the view
    view = _view()
    view["root"]["props"]["style"] = {"classes": "dim"}  # an ancestor's class counts
    assert len([d for d in _run(page, view=view) if d["code"] == "V013"]) == 1
    view = _view()
    view["root"]["children"][0]["propConfig"]["props.style.color"] = {"binding": {"type": "expr",
                                                                                  "config": {"expression": "'#ccc'"}}}
    assert len([d for d in _run(page, view=view) if d["code"] == "V013"]) == 1


def test_contrast_ratio_follows_wcag() -> None:
    from pdk.eyes.rules import contrast_ratio
    assert contrast_ratio("rgb(0, 0, 0)", "rgb(255, 255, 255)") == pytest.approx(21.0)
    assert contrast_ratio("rgb(255, 255, 255)", "rgb(255, 255, 255)") == pytest.approx(1.0)
    assert contrast_ratio("rgba(0, 0, 0, 0.5)", "rgb(255, 255, 255)") == pytest.approx(3.95, abs=0.05)
    assert contrast_ratio("rgb(1 2 3 / 0.5)", "rgb(255, 255, 255)") is not None  # CSS level 4 syntax
    assert contrast_ratio("red", "rgb(255, 255, 255)") is None


def test_v013_flags_low_contrast_text_and_names_the_colors() -> None:
    [finding] = _contrast("rgb(204, 204, 204)", "rgb(255, 255, 255)")
    assert finding["severity"] == "warning" and finding["component"] == "root/Title"
    assert "1.61:1" in finding["message"] and "below 4.5:1" in finding["message"]
    assert _contrast("rgb(50, 50, 50)", "rgb(255, 255, 255)") == []


def test_v013_asks_less_of_large_text() -> None:
    gray = "rgb(140, 140, 140)"  # about 3.4:1 on white
    assert len(_contrast(gray, "rgb(255, 255, 255)")) == 1
    assert _contrast(gray, "rgb(255, 255, 255)", font_size=24) == []
    assert _contrast(gray, "rgb(255, 255, 255)", font_size=19, font_weight="700") == []
    assert len(_contrast(gray, "rgb(255, 255, 255)", font_size=19, font_weight="400")) == 1


def test_v013_skips_disabled_controls_and_unknown_backgrounds() -> None:
    faint = "rgb(230, 230, 230)"
    assert _contrast(faint, None) == []  # a background image on the way: not judged
    assert _contrast(faint, "rgb(255, 255, 255)", disabled=True) == []


def test_v013_judges_only_components_whose_text_is_theirs_to_color() -> None:
    page = _dom()
    page["nodes"][1].update({"type": "ia.chart.timeseries", "color": "rgb(170, 170, 170)",
                             "text_bg": "rgb(250, 250, 250)", "font_size": 12, "font_weight": "400"})
    view = _view()
    view["root"]["children"][0]["type"] = "ia.chart.timeseries"
    assert [d for d in _run(page, view=view) if d["code"] == "V013"] == []

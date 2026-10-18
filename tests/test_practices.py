"""The BP rules (pdk.validate.practices): each code on a minimal synthetic view, and not on the recommended alternative.
The rules that read the project (BP014, BP015, BP018 on repeaters) use a small project tree in tmp_path."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from pdk.bundle import Bundle
from pdk.project import PERSPECTIVE_DIR, load_project
from pdk.validate import validate_view
from tests.helpers import label, view


def bp(bundle: Bundle, data: dict[str, Any], *, project: Any = None, view_path: str | None = None) -> list[dict]:
    return [d for d in validate_view(data, bundle, project=project, view_path=view_path) if d["code"].startswith("BP")]


def only(found: list[dict], code: str) -> dict:
    """The one diagnostic with this code (the test fails when there are none or several)."""
    matching = [d for d in found if d["code"] == code]
    assert len(matching) == 1, [(d["code"], d["message"]) for d in found]
    return matching[0]


def _label(binding: dict[str, Any], name: str = "Lbl") -> dict[str, Any]:
    return label(name, propConfig={"props.text": {"binding": binding}})


def _expr(expression: str) -> dict[str, Any]:
    return {"type": "expr", "config": {"expression": expression}}


def _script(code: str) -> dict[str, Any]:
    """A property binding whose value goes through a script transform (the body is indented, as Ignition stores it)."""
    return {"type": "property", "config": {"path": "view.custom.x"}, "transforms": [{"type": "script", "code": code}]}


def _tag(path: str, **config: Any) -> dict[str, Any]:
    return {"type": "tag", "config": {"tagPath": path, "mode": "direct", **config}}


def _project(tmp_path: Path, views: dict[str, dict[str, Any]]) -> Any:
    root = tmp_path / "sandbox"
    for path, data in views.items():
        file = root / PERSPECTIVE_DIR / "views" / path / "view.json"
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(json.dumps(data), encoding="utf-8")
    return load_project(root)


def _embed(path: str) -> dict[str, Any]:
    return {"type": "ia.display.view", "meta": {"name": "Inner"}, "position": {"basis": "48px"},
            "props": {"path": path, "params": {}}}


def _repeater(path: str, **extra: Any) -> dict[str, Any]:
    component: dict[str, Any] = {"type": "ia.display.flex-repeater", "meta": {"name": "Rows"},
                                 "position": {"basis": "300px"}, "props": {"path": path, "instances": [{}]}}
    component.update(extra)
    return component


# --- BP001 to BP004: expressions and script transforms ----------------------------------------


def test_bp001_tag_call_in_an_expression_is_a_warning(bundle) -> None:
    found = bp(bundle, view(_label(_expr('if(tag("[default]Plant/Pump1/Run") > 5, "hot", "cold")'))))
    d = only(found, "BP001")
    assert d["severity"] == "warning" and d["pointer"].endswith("/binding/config/expression")
    assert d["hint"]


def test_bp001_is_not_raised_for_an_indirect_tag_binding(bundle) -> None:
    indirect = _tag("{1}/Run", mode="indirect", references={"1": "{view.params.tagPath}"})
    assert [d for d in bp(bundle, view(_label(indirect), params={"tagPath": ""})) if d["code"] == "BP001"] == []


@pytest.mark.parametrize("code", [
    "\tif value == 1:\n\t\treturn 'a'\n\telif value == 2:\n\t\treturn 'b'\n\treturn 'c'\n",
    "\treturn {1: 'a', 2: 'b'}[value]\n",
])
def test_bp002_constant_mapping_is_info_and_a_map_transform_is_not(bundle, code) -> None:
    d = only(bp(bundle, view(_label(_script(code)))), "BP002")
    assert d["severity"] == "info" and d["pointer"].endswith("/transforms/0/code") and d["hint"]
    map_transform = {"type": "property", "config": {"path": "view.custom.x"},
                     "transforms": [{"type": "map", "config": {"fallback": "c", "mappings": []}}]}
    assert [d for d in bp(bundle, view(_label(map_transform))) if d["code"] == "BP002"] == []


def test_bp002_and_bp003_are_not_raised_for_a_transform_that_calls_a_function(bundle) -> None:
    found = bp(bundle, view(_label(_script("\treturn compute(value)\n"))))
    assert [d["code"] for d in found if d["code"] in ("BP002", "BP003")] == []


def test_bp003_computing_from_the_value_is_info(bundle) -> None:
    d = only(bp(bundle, view(_label(_script("\treturn value * 10\n")))), "BP003")
    assert d["severity"] == "info" and d["pointer"].endswith("/transforms/0/code")
    expression = {"type": "property", "config": {"path": "view.custom.x"},
                  "transforms": [{"type": "expression", "config": {"expression": "{value} * 10"}}]}
    assert [d for d in bp(bundle, view(_label(expression))) if d["code"] == "BP003"] == []


def test_bp004_blocking_tag_read_in_a_transform_is_a_warning_and_not_bp002_or_bp003(bundle) -> None:
    code = "\treturn system.tag.readBlocking(['[default]Plant/Run'])[0].value\n"
    found = bp(bundle, view(_label(_script(code))))
    assert only(found, "BP004")["severity"] == "warning"
    assert [d["code"] for d in found if d["code"] in ("BP002", "BP003")] == []


def test_bp004_is_not_raised_for_a_tag_binding(bundle) -> None:
    assert [d for d in bp(bundle, view(_label(_tag("[default]Plant/Run")))) if d["code"] == "BP004"] == []


# --- BP005 to BP007: bindings that feed each other, change scripts, view params -----------------


def _prop(path: str, *, bidirectional: bool = False) -> dict[str, Any]:
    config: dict[str, Any] = {"path": path}
    if bidirectional:
        config["bidirectional"] = True
    return {"binding": {"type": "property", "config": config}}


def test_bp005_bindings_that_feed_each_other_in_a_loop_are_an_error(bundle) -> None:
    data = view(propConfig={"custom.a": _prop("view.custom.b"), "custom.b": _prop("view.custom.a")},
                custom={"a": None, "b": None})
    d = only(bp(bundle, data), "BP005")
    assert d["severity"] == "error" and "bindings feed each other in a loop" in d["message"]
    assert d["hint"]


def test_bp005_two_mirrored_bidirectional_bindings_are_a_warning(bundle) -> None:
    data = view(propConfig={"custom.a": _prop("view.custom.b", bidirectional=True),
                            "custom.b": _prop("view.custom.a", bidirectional=True)}, custom={"a": None, "b": None})
    d = only(bp(bundle, data), "BP005")
    assert d["severity"] == "warning" and "mirror each other" in d["message"]


def test_bp005_is_not_raised_for_a_chain_without_a_loop(bundle) -> None:
    data = view(propConfig={"custom.a": _prop("view.custom.b"), "custom.b": {"binding": _tag("[default]Plant/Sp")}},
                custom={"a": None, "b": None})
    assert [d for d in bp(bundle, data) if d["code"] == "BP005"] == []


def test_bp006_change_script_writing_back_its_own_prop_is_a_warning(bundle) -> None:
    found = bp(bundle, view(label("Lbl", propConfig={"props.text": {
        "onChange": {"script": "self.props.text = value.upper()"}}})))
    d = only(found, "BP006")
    assert d["severity"] == "warning" and d["pointer"].endswith("/onChange/script")


def test_bp006_is_not_raised_for_a_write_to_another_prop(bundle) -> None:
    other = label("Lbl", propConfig={"props.text": {"onChange": {"script": "self.props.tooltip = value"}}})
    assert [d for d in bp(bundle, view(other)) if d["code"] == "BP006"] == []


def test_bp006_is_not_raised_when_the_script_checks_origin(bundle) -> None:
    script = "if origin != 'BindingWriteback':\n\tself.props.text = value.upper()"
    assert [d for d in bp(bundle, view(label("Lbl", propConfig={"props.text": {"onChange": {"script": script}}})))
            if d["code"] == "BP006"] == []


def test_bp007_view_param_bound_inside_the_view_is_a_warning(bundle) -> None:
    data = view(label("Lbl"), params={"tagPath": ""},
                propConfig={"params.tagPath": {"paramDirection": "input", **_prop("view.custom.x")}})
    d = only(bp(bundle, data), "BP007")
    assert d["severity"] == "warning" and d["pointer"].endswith("/propConfig/params.tagPath/binding")
    no_direction = view(label("Lbl"), params={"tagPath": ""}, propConfig={"params.tagPath": _prop("view.custom.x")})
    assert only(bp(bundle, no_direction), "BP007")["severity"] == "warning"


def test_bp007_is_not_raised_for_an_output_param(bundle) -> None:
    data = view(label("Lbl"), params={"result": 0},
                propConfig={"params.result": {"paramDirection": "output", **_prop("view.custom.x")}})
    assert [d for d in bp(bundle, data) if d["code"] == "BP007"] == []


# --- BP008 to BP011: queries, clocks, runScript and expressions ---------------------------------


def _query(**polling: Any) -> dict[str, Any]:
    config: dict[str, Any] = {"queryPath": "Plant/Alarms/Count"}
    config.update(polling)
    return {"type": "query", "config": config}


def test_bp008_polling_every_2_seconds_is_a_warning(bundle) -> None:
    query = _query(polling={"enabled": True, "rate": 2})
    d = only(bp(bundle, view(_label(query))), "BP008")
    assert d["severity"] == "warning" and d["pointer"].endswith("/config/polling/rate")


def test_bp008_polling_every_30_seconds_is_fine_and_bypassing_the_cache_is_info(bundle) -> None:
    slow = _query(polling={"enabled": True, "rate": 30})
    assert [d for d in bp(bundle, view(_label(slow))) if d["code"] == "BP008"] == []
    bypass = _query(polling={"enabled": True, "rate": 30}, bypassCache=True)
    d = only(bp(bundle, view(_label(bypass))), "BP008")
    assert d["severity"] == "info" and d["pointer"].endswith("/config/bypassCache")


def test_bp009_a_clock_faster_than_a_second_is_a_warning(bundle) -> None:
    d = only(bp(bundle, view(_label(_expr("now(500)")))), "BP009")
    assert d["severity"] == "warning" and d["pointer"].endswith("/config/expression")


def test_bp009_two_clocks_in_one_view_are_one_info(bundle) -> None:
    data = view(_label(_expr("now()"), "A"), _label(_expr("now()"), "B"))
    d = only(bp(bundle, data), "BP009")
    assert d["severity"] == "info" and "2 clocks" in d["message"]
    assert [x for x in bp(bundle, view(_label(_expr("now(1000)")))) if x["code"] == "BP009"] == []


def test_bp010_run_script_polling_fast_is_a_warning_and_once_is_info(bundle) -> None:
    fast = _label(_expr('runScript("Plant.Scripts.calc", 1000)'))
    assert only(bp(bundle, view(fast)), "BP010")["severity"] == "warning"
    once = _label(_expr('runScript("Plant.Scripts.calc", 0)'))
    d = only(bp(bundle, view(once)), "BP010")
    assert d["severity"] == "info" and d["pointer"].endswith("/config/expression")


def test_bp011_expression_that_only_reads_one_path_is_info(bundle) -> None:
    d = only(bp(bundle, view(_label(_expr("{view.params.a}")))), "BP011")
    assert d["severity"] == "info" and d["pointer"].endswith("/config/expression")
    prop_binding = {"type": "property", "config": {"path": "view.params.a"}}
    assert [x for x in bp(bundle, view(_label(prop_binding))) if x["code"] == "BP011"] == []


# --- BP012 to BP013: one tag bound often, drop of a UDT ------------------------------------------


def test_bp012_the_same_tag_bound_four_times_is_one_info(bundle) -> None:
    data = view(*[_label(_tag("[default]Plant/Pump1/Run"), f"L{i}") for i in range(4)])
    d = only(bp(bundle, data), "BP012")
    assert d["severity"] == "info" and "4 times" in d["message"]
    three = view(*[_label(_tag("[default]Plant/Pump1/Run"), f"L{i}") for i in range(3)])
    assert [x for x in bp(bundle, three) if x["code"] == "BP012"] == []


def test_bp013_dropping_a_udt_that_binds_every_member_is_a_warning(bundle) -> None:
    for entry in ({"action": "bind"}, {}):  # the default action is bind
        d = only(bp(bundle, view(props={"dropConfig": {"udts": [entry]}})), "BP013")
        assert d["severity"] == "warning" and d["pointer"] == "/props/dropConfig/udts/0/action"
    path_only = view(props={"dropConfig": {"udts": [{"action": "path"}]}})
    assert [x for x in bp(bundle, path_only) if x["code"] == "BP013"] == []


# --- BP014 and BP015: project-aware (nesting, repeaters) -----------------------------------------


def test_bp014_four_embeds_deep_is_a_warning(bundle, tmp_path) -> None:
    project = _project(tmp_path / "deep", {"Chain/L0": view(_embed("Chain/L1")), "Chain/L1": view(_embed("Chain/L2")),
                                           "Chain/L2": view(_embed("Chain/L3")), "Chain/L3": view(label("Leaf"))})
    d = only(bp(bundle, view(_embed("Chain/L0")), project=project, view_path="Screens/Top"), "BP014")
    assert d["severity"] == "warning" and d["pointer"] == "/root/children/0/props/path"


def test_bp014_three_embeds_deep_and_no_project_are_fine(bundle, tmp_path) -> None:
    project = _project(tmp_path / "shallow", {"Chain/L0": view(_embed("Chain/L1")),
                                              "Chain/L1": view(_embed("Chain/L2")), "Chain/L2": view(label("Leaf"))})
    top = view(_embed("Chain/L0"))
    assert [d for d in bp(bundle, top, project=project, view_path="Screens/Top") if d["code"] == "BP014"] == []
    deep = view(_embed("Chain/L0"))
    assert [d for d in bp(bundle, deep) if d["code"] == "BP014"] == []


def test_bp015_query_in_a_view_a_repeater_renders_is_a_warning(bundle, tmp_path) -> None:
    row = view(_label(_query()))
    project = _project(tmp_path / "rows", {"Screens/List": view(_repeater("Rows/Row")), "Rows/Row": row})
    d = only(bp(bundle, row, project=project, view_path="Rows/Row"), "BP015")
    assert d["severity"] == "warning" and d["pointer"].endswith("/propConfig/props.text/binding")


def test_bp015_heavy_component_in_a_repeated_view_is_info_and_not_repeated_is_fine(bundle, tmp_path) -> None:
    pie = {"type": "ia.chart.pie", "meta": {"name": "Share"}, "position": {"basis": "200px"}, "props": {}}
    row = view(pie)
    project = _project(tmp_path / "pie", {"Screens/List": view(_repeater("Rows/Row")), "Rows/Row": row})
    d = only(bp(bundle, row, project=project, view_path="Rows/Row"), "BP015")
    assert d["severity"] == "info" and d["pointer"] == "/root/children/0"
    alone = _project(tmp_path / "alone", {"Rows/Row": row})
    assert [x for x in bp(bundle, row, project=alone, view_path="Rows/Row") if x["code"] == "BP015"] == []


# --- BP016 to BP018: styles, layout bindings, repeater instances ---------------------------------


def test_bp016_literal_colors_are_one_info_with_their_count(bundle) -> None:
    data = view(label("a", props={"text": "a", "style": {"color": "#ff0000"}}),
                label("b", props={"text": "b", "style": {"backgroundColor": "rgb(0, 0, 0)"}}),
                label("c", props={"text": "c", "style": {"color": "#00ff00"}}))
    d = only(bp(bundle, data), "BP016")
    assert d["severity"] == "info" and "3 literal color(s)" in d["message"]
    assert "#ff0000" in d["detail"] and d["hint"]


def test_bp016_theme_variables_are_not_literal_colors(bundle) -> None:
    data = view(label("a", props={"text": "a", "style": {"color": "var(--neutral-90)"}}))
    assert [d for d in bp(bundle, data) if d["code"] == "BP016"] == []


def test_bp017_the_same_inline_style_on_three_components_is_one_info(bundle) -> None:
    style = {"fontWeight": "bold", "fontSize": "14px"}
    data = view(*[label(f"L{i}", props={"text": "x", "style": dict(style)}) for i in range(3)])
    d = only(bp(bundle, data), "BP017")
    assert d["severity"] == "info" and "3+ components" in d["message"]


def test_bp017_style_classes_are_the_recommended_alternative(bundle) -> None:
    data = view(*[label(f"L{i}", props={"text": "x", "style": {"classes": "hmi-card"}}) for i in range(3)])
    assert [d for d in bp(bundle, data) if d["code"] == "BP017"] == []


def test_bp018_binding_a_layout_property_is_info(bundle) -> None:
    layout = label("Lbl", propConfig={"position.basis": {"binding": {"type": "property",
                                                                    "config": {"path": "view.custom.h"}}}})
    d = only(bp(bundle, view(layout)), "BP018")
    assert d["severity"] == "info" and d["pointer"].endswith("/propConfig/position.basis/binding")
    visible = label("Lbl", propConfig={"meta.visible": {"binding": {"type": "property",
                                                                   "config": {"path": "view.custom.v"}}}})
    assert [d for d in bp(bundle, view(visible)) if d["code"] == "BP018"] == []


def test_bp018_binding_a_repeaters_instances_is_info_with_a_project(bundle, tmp_path) -> None:
    bound = _repeater("Rows/Row", propConfig={"props.instances": _prop("view.custom.rows")})
    project = _project(tmp_path / "instances", {"Rows/Row": view(label("Run")), "Screens/List": view(bound)})
    d = only(bp(bundle, view(bound), project=project, view_path="Screens/List"), "BP018")
    assert d["severity"] == "info" and d["pointer"] == "/root/children/0/propConfig/props.instances/binding"
    plain = _repeater("Rows/Row")
    assert [x for x in bp(bundle, view(plain), project=project, view_path="Screens/List") if x["code"] == "BP018"] == []


# --- every diagnostic carries a pointer and a hint ---------------------------------------------


def test_every_bp_diagnostic_has_a_pointer_and_a_hint(bundle) -> None:
    data = view(_label(_expr('if(tag("[default]Plant/Pump1/Run") > 5, "hot", "cold")'), "A"),
                _label(_script("\treturn value * 10\n"), "B"),
                _label(_expr("now(500)"), "C"),
                label("D", props={"text": "d", "style": {"color": "#fff"}}),
                propConfig={"custom.a": _prop("view.custom.b"), "custom.b": _prop("view.custom.a")},
                custom={"a": None, "b": None})
    found = bp(bundle, data)
    assert {d["code"] for d in found} >= {"BP001", "BP003", "BP005", "BP009", "BP016"}
    for d in found:
        assert d["pointer"].startswith("/") and d["hint"], d["code"]


def test_bp002_is_not_raised_when_the_last_else_computes(bundle) -> None:
    code = "\tif value == 1:\n\t\treturn 'a'\n\telif value == 2:\n\t\treturn 'b'\n\telse:\n\t\treturn compute(value)"
    view = {"root": {"type": "ia.container.flex", "meta": {"name": "root"}, "children": [
        {"type": "ia.display.label", "meta": {"name": "L"}, "propConfig": {"props.text": {"binding": {
            "type": "expr", "config": {"expression": "1"}, "transforms": [{"type": "script", "code": code}]}}}}]}}
    codes = {d["code"] for d in validate_view(view, bundle) if d["code"].startswith("BP")}
    assert "BP002" not in codes


def test_bp006_needs_the_same_prop_not_one_that_starts_alike(bundle) -> None:
    view = {"root": {"type": "ia.container.flex", "meta": {"name": "root"}, "children": [
        {"type": "ia.display.label", "meta": {"name": "L"}, "propConfig": {"props.text": {
            "onChange": {"script": "\tself.props.textColor = 'red'", "enabled": None}}}}]}}
    assert not [d for d in validate_view(view, bundle) if d["code"] == "BP006"]

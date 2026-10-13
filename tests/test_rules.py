"""One test per rule family: the defect is reported where it lives, and its correct form stays clean."""

from __future__ import annotations

import copy

from tests.helpers import codes, find, label, view

CLEAN = view(label("Title"), label("Value"))


def test_clean_view_has_no_findings(bundle) -> None:
    assert codes(bundle, CLEAN, severities=("error", "warning", "info")) == set()


# --- S structure ------------------------------------------------------------------------------


def test_unknown_view_and_component_keys(bundle) -> None:
    data = view(label())
    data["rot"] = {}
    data["root"]["children"][0]["name"] = "Label"
    found = codes(bundle, data)
    assert {"S001", "S004"} <= found
    [s004] = find(bundle, data, "S004")
    assert "meta.name" in s004["hint"]


def test_component_type_typo_suggests_the_real_type(bundle) -> None:
    data = view(label())
    data["root"]["children"][0]["type"] = "ia.display.lable"
    [d] = find(bundle, data, "S006")
    assert d["severity"] == "error" and "ia.display.label" in d["hint"]


def test_unknown_module_type_is_only_a_warning(bundle) -> None:
    data = view({"type": "ia.reporting.report-viewer", "meta": {"name": "Report"}})
    [d] = find(bundle, data, "S006")
    assert d["severity"] == "warning"


def test_children_only_on_containers_and_unique_names(bundle) -> None:
    data = view(label("A", children=[label("Inner")]), label("A"))
    found = codes(bundle, data)
    assert {"S007", "S010"} <= found
    [dup] = find(bundle, data, "S010")
    assert dup["pointer"] == "/root/children/1/meta/name"


def test_missing_name_and_type(bundle) -> None:
    data = view({"type": "ia.display.label"}, {"meta": {"name": "X"}})
    assert {"S008", "S005"} <= codes(bundle, data)


# --- P props / meta / position -----------------------------------------------------------------


def test_invented_prop_reports_suggestion(bundle) -> None:
    data = view(label(props={"textt": "x"}))
    [d] = find(bundle, data, "P001")
    assert d["severity"] == "error" and d["pointer"].endswith("/props/textt") and "'text'" in d["hint"]


def test_closed_container_rejects_css_style_prop(bundle) -> None:
    data = view(label())
    data["root"]["props"] = {"justifyContent": "space-between"}
    [d] = find(bundle, data, "P001")
    assert d["severity"] == "error" and "justify" in d["hint"]


def test_wrong_type_and_enum(bundle) -> None:
    data = view(label(props={"text": "x", "alignVertical": "middle"}), {
        "type": "ia.input.checkbox", "meta": {"name": "Check"}, "props": {"selected": "yes"}})
    found = codes(bundle, data)
    assert {"P002", "P003"} <= found


def test_number_in_string_field_is_a_warning(bundle) -> None:
    [d] = find(bundle, view(label(position={"basis": 2000})), "P002")
    assert d["severity"] == "warning"


def test_position_key_of_the_wrong_container(bundle) -> None:
    [d] = find(bundle, view(label(position={"x": 10, "basis": "32px"})), "P010")
    assert d["pointer"].endswith("/position/x") and "basis" in d["hint"]


def test_coord_children_may_omit_defaults(bundle) -> None:
    data = view(label(position={}), root_type="ia.container.coord")
    assert codes(bundle, data) == set()


def test_meta_name_pattern_and_designer_meta_keys(bundle) -> None:
    data = view(label("Pump.Speed"), label("Ok", meta={"name": "Ok", "hasDelegate": True}))
    assert codes(bundle, data) == {"P008"}


def test_style_keys_are_lenient_but_kebab_case_warns(bundle) -> None:
    data = view(label(props={"text": "x", "style": {"padding": "4px", "borderRadius": "4px",
                                                     "background-color": "red"}}))
    [d] = find(bundle, data, "P006")
    assert d["severity"] == "warning" and "backgroundColor" in d["hint"]


def test_typed_dates_and_free_data_rows_are_accepted(bundle) -> None:
    chart = {"type": "ia.chart.timeseries", "meta": {"name": "Chart"}, "position": {"basis": "300px"},
             "props": {"series": [{"name": "Flow", "data": [{"time": 1, "value": 2, "Pressure": 3}]}]}}
    table = {"type": "ia.display.table", "meta": {"name": "Table"}, "position": {"basis": "200px"},
             "props": {"selection": {"data": [{"Pump": "P-101", "Torque": 9}]}}}
    assert codes(bundle, view(chart, table)) == set()


def test_svg_attributes_on_shapes_are_accepted(bundle) -> None:
    shape = {"type": "ia.shapes.group", "meta": {"name": "Group"}, "props": {"transform": "translate(1,2)"}}
    assert codes(bundle, view(shape, root_type="ia.container.coord")) == set()


# --- B propConfig / bindings -------------------------------------------------------------------


def _bound(binding: dict, path: str = "props.text") -> dict:
    return view(label(propConfig={path: {"binding": binding}}))


def test_expression_binding_type_is_expr(bundle) -> None:
    [d] = find(bundle, _bound({"type": "expression", "config": {"expression": "1"}}), "B003")
    assert d["hint"] == "use 'expr'"


def test_binding_target_must_exist(bundle) -> None:
    [d] = find(bundle, _bound({"type": "expr", "config": {"expression": "1"}}, "props.txt"), "B001")
    assert "'text'" in d["hint"]


def test_binding_config_required_and_designer_keys(bundle) -> None:
    assert "B004" in codes(bundle, _bound({"type": "tag", "config": {"mode": "direct"}}))
    designer = {"type": "tag", "config": {"tagPath": "[default]X", "mode": "direct", "fallbackDelay": 2.5}}
    assert codes(bundle, _bound(designer)) == set()


def test_transforms(bundle) -> None:
    bad = {"type": "expr", "config": {"expression": "1"}, "transforms": [{"type": "expr", "expression": "{value}"}]}
    [d] = find(bundle, _bound(bad), "B005")
    assert "'expression'" in d["hint"]
    good = {"type": "tag", "config": {"tagPath": "[default]X", "mode": "direct"}, "transforms": [
        {"type": "map", "inputType": "scalar", "outputType": "scalar", "mappings": [], "fallback": ""},
        {"type": "format", "formatType": "numeric", "formatValue": "number"},
        {"type": "script", "code": "\treturn value"}]}
    assert codes(bundle, _bound(good)) == set()


def test_param_direction_only_on_view_params(bundle) -> None:
    data = view(label(propConfig={"props.text": {"paramDirection": "input"}}),
                params={"motor": ""}, propConfig={"params.motor": {"paramDirection": "in"}})
    assert len(find(bundle, data, "B007")) == 2


# --- E events --------------------------------------------------------------------------------------


def test_event_names_groups_and_scopes(bundle) -> None:
    data = view(label(events={"dom": {"onClik": {"type": "script", "scope": "C", "config": {"script": "\tpass"}}},
                              "onClick": {}}))
    found = codes(bundle, data)
    assert {"E001", "E002", "E004"} <= found


def test_extension_functions_only_the_declared_names(bundle) -> None:
    table = {"type": "ia.display.alarmstatustable", "meta": {"name": "Alarms"}, "position": {"basis": "300px"},
             "scripts": {"extensionFunctions": [
                 {"name": "filterAlarm", "script": "\treturn True", "enabled": True},
                 {"name": "filterAlarms", "script": "\treturn True", "enabled": True}]}}
    [d] = find(bundle, view(table), "E010")
    assert d["pointer"] == "/root/children/0/scripts/extensionFunctions/1/name"
    assert "filterAlarm" in d["hint"]
    stray = label(scripts={"extensionFunctions": [{"name": "filterAlarm", "script": "\treturn True", "enabled": True}]})
    [d] = find(bundle, view(stray), "E010")
    assert "no extension functions" in d["hint"]


def test_button_component_event_and_popup_shape(bundle) -> None:
    button = {"type": "ia.input.button", "meta": {"name": "Open"}, "props": {"text": "Open"},
              "events": {"component": {"onActionPerformed": {"type": "popup", "scope": "C",
                                                             "config": {"type": "open", "id": "p"}}}}}
    [d] = find(bundle, view(button), "E005")
    assert "viewPath" in d["message"]


# --- R references ----------------------------------------------------------------------------------


def test_property_paths_resolve_inside_the_view(bundle) -> None:
    ok = view(label("Source"), label("Target", propConfig={"props.text": {"binding": {
        "type": "property", "config": {"path": "../Source.props.text"}}}}))
    assert codes(bundle, ok) == set()
    broken = copy.deepcopy(ok)
    broken["root"]["children"][1]["propConfig"]["props.text"]["binding"]["config"]["path"] = "../Sourse.props.text"
    [d] = find(bundle, broken, "R004")
    assert "'Source'" in d["hint"]


def test_expression_refs_to_view_params(bundle) -> None:
    data = view(label(propConfig={"props.text": {"binding": {
        "type": "expr", "config": {"expression": "{view.params.motr} + {/root/Label.props.textt}"}}}}),
        params={"motor": "P-101"})
    found = codes(bundle, data)
    assert {"R001", "R003"} <= found


# --- HMI lint ---------------------------------------------------------------------------------------


def test_lint_is_info_only(bundle) -> None:
    table = {"type": "ia.display.table", "meta": {"name": "Table"}}
    empty = {"type": "ia.container.flex", "meta": {"name": "Empty"}, "children": []}
    found = codes(bundle, view(table, empty), severities=("info",))
    assert {"HMI010", "HMI009"} <= found
    assert codes(bundle, view(table, empty)) == set()

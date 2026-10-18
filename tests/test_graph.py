"""The project graph (pdk.graph) and its reports (pdk.inventory, `pdk refs`, `pdk inventory`, pdk_refs), over small
synthetic project trees written to tmp_path. No real project is copied."""

from __future__ import annotations

import csv
import io
import json
import os
from pathlib import Path
from typing import Any

import pytest

from pdk import mcp_server
from pdk.cli import main
from pdk.graph import MAX_DEPTH, Graph, concatenation, matches, read_view
from pdk.inventory import inventory, inventory_summary, inventory_text, tag_text, view_text
from pdk.project import PERSPECTIVE_DIR, load_project
from tests.helpers import label, view


def _tree(tmp_path: Path, views: dict[str, Any], page_config: dict[str, Any] | None = None) -> Path:
    root = tmp_path / "sandbox"
    for path, data in views.items():
        file = root / PERSPECTIVE_DIR / "views" / path / "view.json"
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    if page_config is not None:
        file = root / PERSPECTIVE_DIR / "page-config" / "config.json"
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(json.dumps(page_config), encoding="utf-8")
    return root


def _graph(tmp_path: Path, views: dict[str, Any], page_config: dict[str, Any] | None = None) -> Graph:
    return Graph.build(load_project(_tree(tmp_path, views, page_config)))


def _tag(path: str, **config: Any) -> dict[str, Any]:
    """A tag binding (direct unless the config says otherwise)."""
    return {"type": "tag", "config": {"tagPath": path, "mode": "direct", **config}}


def _text(binding: dict[str, Any]) -> dict[str, Any]:
    return {"props.text": {"binding": binding}}


def _indirect(template: str = "{1}/Running", reference: str = "{view.params.tagPath}") -> dict[str, Any]:
    return _tag(template, mode="indirect", references={"1": reference})


def _embed(path: str, params: dict[str, Any] | None = None, name: str = "Pump", **extra: Any) -> dict[str, Any]:
    component: dict[str, Any] = {"type": "ia.display.view", "meta": {"name": name}, "position": {"basis": "48px"},
                                 "props": {"path": path, "params": params or {}}}
    component.update(extra)
    return component


def _row_view(params: dict[str, Any] | None = None) -> dict[str, Any]:
    """A view that shows one indirect tag of its tagPath param."""
    return view(label("Run", propConfig=_text(_indirect())), params=params if params is not None else {"tagPath": ""})


# --- reading one view -------------------------------------------------------------------------


def test_direct_tag_binding_is_a_direct_use(tmp_path) -> None:
    graph = _graph(tmp_path, {"Screens/Pump": view(label("Run", propConfig=_text(_tag("[default]Plant/Pump1/Run"))))})
    [use] = graph.views["Screens/Pump"].tags
    assert (use.mode, use.template, use.writes) == ("direct", "[default]Plant/Pump1/Run", False)
    assert use.pointer.endswith("/propConfig/props.text/binding/config/tagPath")
    assert graph.expand(use) == [("[default]Plant/Pump1/Run", "")]


def test_bidirectional_tag_binding_is_a_write(tmp_path) -> None:
    graph = _graph(tmp_path, {"Screens/Sp": view(
        label("Sp", propConfig=_text(_tag("[default]Plant/Setpoint", bidirectional=True))))})
    [use] = graph.views["Screens/Sp"].tags
    assert use.writes is True
    assert [u.writes for u, _path, _via in graph.tag_uses("Setpoint")] == [True]


def test_expression_mode_tag_binding_keeps_its_template_and_expands_params(tmp_path) -> None:
    expression = '"[default]Plant/" + {view.params.area} + "/Run"'
    screen = view(label("Run", propConfig=_text(_tag(expression, mode="expression"))), params={"area": ""})
    home = view(_embed("Screens/Area", {"area": "Pump1"}))
    graph = _graph(tmp_path, {"Screens/Area": screen, "Screens/Home": home})
    [use] = graph.views["Screens/Area"].tags
    assert (use.mode, use.template) == ("expression", expression)
    [(path, via)] = graph.expand(use)
    assert path == "[default]Plant/Pump1/Run" and via.startswith("Screens/Home")


def test_tag_call_in_an_expression_binding_is_a_tag_use(tmp_path) -> None:
    expression = 'if(tag("[default]Plant/Pump1/Run") > 5, "hot", "cold")'
    graph = _graph(tmp_path, {"Screens/Hot": view(label("Hot", propConfig=_text(
        {"type": "expr", "config": {"expression": expression}})))})
    [use] = graph.views["Screens/Hot"].tags
    assert (use.mode, use.template) == ("tag()", '"[default]Plant/Pump1/Run"')
    assert graph.expand(use) == [("[default]Plant/Pump1/Run", "")]


def test_script_tag_calls_are_uses_and_fragments_are_ignored(tmp_path) -> None:
    script = ("values = system.tag.readBlocking(['[default]Plant/Pump1/Run', '/Run'])\n"
              "system.tag.write('[default]Plant/Pump1/Setpoint', 5)\n"
              "system.tag.write('/Local', 1)\n")
    screen = view(label("Go", events={"component": {"onActionPerformed": {
        "type": "script", "config": {"script": script}}}}))
    graph = _graph(tmp_path, {"Screens/Go": screen})
    uses = [(u.template, u.mode, u.writes) for u in graph.views["Screens/Go"].tags]
    assert uses == [("[default]Plant/Pump1/Run", "script", False), ("[default]Plant/Pump1/Setpoint", "script", True)]
    assert all(graph.expand(u) == [(u.template, "")] for u in graph.views["Screens/Go"].tags)


def test_indirect_tag_is_expanded_through_the_caller(tmp_path) -> None:
    home = view(_embed("Screens/Row", {"tagPath": "[default]Plant/Pump1"}))
    graph = _graph(tmp_path, {"Screens/Row": _row_view(), "Screens/Home": home})
    [use] = graph.views["Screens/Row"].tags
    [(path, via)] = graph.expand(use)
    assert path == "[default]Plant/Pump1/Running" and via.startswith("Screens/Home")


def test_indirect_tag_with_no_caller_and_no_default_is_not_expanded(tmp_path) -> None:
    graph = _graph(tmp_path, {"Screens/Row": _row_view()})
    [use] = graph.views["Screens/Row"].tags
    assert graph.expand(use) == []
    # the unexpanded use still shows up under its template
    assert [(u.template, path) for u, path, _via in graph.tag_uses("Running")] == [("{1}/Running", "{1}/Running")]


def test_runtime_only_reference_stays_unexpanded(tmp_path) -> None:
    session_row = view(label("Run", propConfig=_text(_indirect(reference="{session.custom.plant}"))))
    graph = _graph(tmp_path, {"Screens/Session": session_row})
    [use] = graph.views["Screens/Session"].tags
    assert graph.expand(use) == []


def test_default_of_a_param_is_a_value_marked_as_default(tmp_path) -> None:
    home = view(_embed("Screens/Row", {"tagPath": "[default]Plant/Pump2"}))
    graph = _graph(tmp_path, {"Screens/Row": _row_view({"tagPath": "[default]Plant/Default"}), "Screens/Home": home})
    values = graph.param_values("Screens/Row", "tagPath")
    assert values[0] == ("[default]Plant/Default", "Screens/Row (default)")
    assert [value for value, _via in values] == ["[default]Plant/Default", "[default]Plant/Pump2"]
    assert values[1][1].startswith("Screens/Home")
    [use] = graph.views["Screens/Row"].tags
    assert sorted(path for path, _via in graph.expand(use)) == [
        "[default]Plant/Default/Running", "[default]Plant/Pump2/Running"]
    assert [via for path, via in graph.expand(use) if path.startswith("[default]Plant/Default")] == [
        "Screens/Row (default)"]


def test_param_bound_to_the_callers_param_is_followed_up_the_chain(tmp_path) -> None:
    middle = view(_embed("Screens/Row", {}, propConfig={"props.params.tagPath": {"binding": {
        "type": "property", "config": {"path": "view.params.area"}}}}), params={"area": ""})
    top = view(_embed("Screens/Middle", {"area": "[default]Plant/Pump3"}, name="Area"))
    graph = _graph(tmp_path, {"Screens/Row": _row_view(), "Screens/Middle": middle, "Screens/Top": top})
    [(value, via)] = graph.param_values("Screens/Row", "tagPath")
    assert value == "[default]Plant/Pump3" and via.startswith("Screens/Middle > Screens/Top")
    [use] = graph.views["Screens/Row"].tags
    assert [path for path, _via in graph.expand(use)] == ["[default]Plant/Pump3/Running"]


def test_param_cycle_terminates_without_values(tmp_path) -> None:
    def bound(other: str) -> dict[str, Any]:
        return view(_embed(other, {}, propConfig={"props.params.x": {"binding": {
            "type": "property", "config": {"path": "view.params.x"}}}}), params={"x": ""})
    graph = _graph(tmp_path, {"Screens/A": bound("Screens/B"), "Screens/B": bound("Screens/A")})
    assert graph.param_values("Screens/A", "x") == []


def test_this_reference_to_a_literal_custom_property_is_resolved(tmp_path) -> None:
    binding = _tag("{1}/Running", mode="indirect", references={"1": "{this.custom.plant}"})
    screen = view(label("Run", custom={"plant": "[default]Plant/Pump5"}, propConfig=_text(binding)))
    graph = _graph(tmp_path, {"Screens/Pump5": screen})
    [use] = graph.views["Screens/Pump5"].tags
    assert graph.expand(use) == [("[default]Plant/Pump5/Running", "")]


def test_repeater_gives_one_set_of_values_per_instance(tmp_path) -> None:
    rows = {"type": "ia.display.flex-repeater", "meta": {"name": "Rows"}, "position": {"basis": "300px"},
            "props": {"path": "Screens/Row", "instances": [{"tagPath": "[default]Plant/A"},
                                                           {"tagPath": "[default]Plant/B"}]}}
    graph = _graph(tmp_path, {"Screens/Row": _row_view(), "Screens/List": view(rows)})
    [use] = graph.views["Screens/Row"].tags
    assert sorted(path for path, _via in graph.expand(use)) == ["[default]Plant/A/Running", "[default]Plant/B/Running"]
    [repeater] = graph.repeated("Screens/Row")
    assert len(repeater.params) == 2


def test_nav_popup_and_carousel_are_uses_with_their_params(tmp_path) -> None:
    nav = label("Open", events={"component": {"onActionPerformed": {
        "type": "nav", "config": {"view": "/Screens/Detail", "params": {"id": 3}}}}})
    popup = label("Pop", events={"component": {"onActionPerformed": {
        "type": "popup", "config": {"viewPath": "Screens/Pop", "viewParams": {"x": 1}}}}})
    carousel = {"type": "ia.display.carousel", "meta": {"name": "Slides"}, "position": {"basis": "200px"},
                "props": {"views": [{"viewPath": "Screens/Slide1"}, {"viewPath": "Screens/Slide2"}]}}
    graph = _graph(tmp_path, {"Screens/Home": view(nav, popup, carousel)})
    [nav_use] = graph.callers("Screens/Detail")
    assert nav_use.kind == "nav" and nav_use.params[0]["id"].value == 3
    [pop_use] = graph.callers("Screens/Pop")
    assert pop_use.kind == "popup" and pop_use.params[0]["x"].value == 1
    assert sorted(u.kind for slide in ("Screens/Slide1", "Screens/Slide2") for u in graph.callers(slide)) == [
        "embed", "embed"]


def test_routes_and_docks_come_from_page_config(tmp_path) -> None:
    page_config = {"pages": {"/pdk/home": {"viewPath": "Screens/Home"}},
                   "sharedDocks": {"left": [{"viewPath": "Docks/Nav"}]}}
    graph = _graph(tmp_path, {"Screens/Home": view(), "Docks/Nav": view()}, page_config)
    assert graph.routes == {"/pdk/home": "Screens/Home", "dock:left:0": "Docks/Nav"}
    assert [u.kind for u in graph.callers("Screens/Home")] == ["route"]
    assert [u.pointer for u in graph.callers("/Docks/Nav")] == ["page-config:dock:left:0"]


def test_named_queries_and_style_classes_are_read(tmp_path) -> None:
    query = {"type": "query", "config": {"queryPath": "/Plant/Alarms/Active"}}
    expression_query = {"type": "query", "config": {"queryPath": "Plant/Other", "mode": "expression"}}
    styled = label("Card", props={"text": "x", "style": {"classes": "hmi-card hmi-row"}},
                   propConfig={"props.text": {"binding": query}, "props.tooltip": {"binding": expression_query}})
    info = read_view("Screens/Card", view(styled))
    assert info.queries == ["Plant/Alarms/Active"]
    assert info.classes == {"hmi-card", "hmi-row"}


def test_unreadable_views_are_skipped(tmp_path) -> None:
    graph = _graph(tmp_path, {"Screens/Broken": "{not json", "Screens/List": "[1, 2]", "Screens/Ok": view()})
    assert sorted(graph.views) == ["Screens/Ok"]


def test_graph_of_is_built_once_per_project(tmp_path) -> None:
    project = load_project(_tree(tmp_path, {"Screens/Ok": view()}))
    assert Graph.of(project) is Graph.of(project)


def test_depth_counts_nested_embeds_and_stops_on_cycles(tmp_path) -> None:
    chain = {"Screens/A": view(_embed("Screens/B")), "Screens/B": view(_embed("Screens/C")), "Screens/C": view()}
    graph = _graph(tmp_path, chain)
    assert (graph.depth("Screens/A"), graph.depth("Screens/C")) == (2, 0)
    cyclic = _graph(tmp_path / "cycle",
                    {"Screens/A": view(_embed("Screens/B")), "Screens/B": view(_embed("Screens/A"))})
    assert cyclic.depth("Screens/A") == 2


def test_params_are_followed_only_up_to_the_depth_limit(tmp_path) -> None:
    # L_i embeds L_(i-1) with its own param x; Top gives x a literal at the top of the chain
    links = MAX_DEPTH + 2
    views: dict[str, Any] = {"Chain/L0": view(params={"x": ""})}
    bound = {"props.params.x": {"binding": {"type": "property", "config": {"path": "view.params.x"}}}}
    for i in range(1, links + 1):
        views[f"Chain/L{i}"] = view(_embed(f"Chain/L{i - 1}", {}, propConfig=bound), params={"x": ""})
    views["Chain/Top"] = view(_embed(f"Chain/L{links}", {"x": "[default]Plant/Far"}, name="Far"))
    graph = _graph(tmp_path, views)
    assert graph.param_values("Chain/L0", "x") == []  # too far up: not followed
    assert [value for value, _via in graph.param_values(f"Chain/L{links - 1}", "x")] == ["[default]Plant/Far"]


# --- pure helpers ----------------------------------------------------------------------------


@pytest.mark.parametrize(("path", "pattern", "expected"), [
    ("[default]Plant/Pump1/Run", "plant/pump1", True),
    ("[default]Plant/Pump1/Run", "[default]Plant/Pump1", True),
    ("[default]Plant/Pump1/Run", "Pump1/Run", True),
    ("[default]Plant/Pump1/Run", "*/Pump?/Run", True),
    ("[default]Plant/Pump1/Run", "plant/*", True),
    ("[default]Plant/Pump1/Run", "Pump2", False),
    ("[default]Plant/Pump1/Run", "Pump?/Stop", False),
])
def test_matches_is_case_insensitive_with_optional_provider_and_globs(path, pattern, expected) -> None:
    assert matches(path, pattern) is expected


@pytest.mark.parametrize(("expression", "parts"), [
    ('"[default]Plant/" + {view.params.area} + "/Run"',
     [("literal", "[default]Plant/"), ("param", "area"), ("literal", "/Run")]),
    ("'a+b' + {view.params.x}", [("literal", "a+b"), ("param", "x")]),
    ("{view.params.area}", [("param", "area")]),
    ('str({view.params.area})', None),
    ("", None),
])
def test_concatenation_keeps_literals_and_params_only(expression, parts) -> None:
    assert concatenation(expression) == parts


# --- reports: pdk refs, pdk inventory, pdk_refs ----------------------------------------------


def _shop(tmp_path: Path) -> Graph:
    """Home embeds Row twice (one tag per instance); Row shows an indirect tag; Orphan is opened by nothing."""
    rows = {"type": "ia.display.flex-repeater", "meta": {"name": "Rows"}, "position": {"basis": "300px"},
            "props": {"path": "Screens/Row", "instances": [{"tagPath": "[default]Plant/A"},
                                                           {"tagPath": "[default]Plant/B"}]}}
    views = {"Screens/Row": _row_view(), "Screens/Home": view(rows), "Screens/Orphan": view(
        label("Writes", propConfig=_text(_tag("[default]Plant/Setpoint", bidirectional=True))))}
    return _graph(tmp_path, views, {"pages": {"/pdk/home": {"viewPath": "Screens/Home"}}})


def test_tag_text_lists_each_use_with_its_expanded_path(tmp_path) -> None:
    text = tag_text(_shop(tmp_path), "Plant/A")
    assert text.splitlines()[0] == "1 use(s) of 'Plant/A' in 1 view(s) of sandbox:"
    assert "Screens/Row" in text
    assert "[default]Plant/A/Running" in text and "via Screens/Home" in text


def test_tag_text_says_when_nothing_matches(tmp_path) -> None:
    assert "no view of sandbox uses a tag matching 'Nope'" in tag_text(_shop(tmp_path), "Nope")


def test_view_text_names_callers_params_and_what_it_uses(tmp_path) -> None:
    text = view_text(_shop(tmp_path), "/Screens/Row/")
    assert "params ['tagPath']" in text and "used by 1:" in text
    assert "Screens/Home: 1 repeater(s)" in text and "passing tagPath" in text
    assert "tags: 1 use(s), 0 write(s); named queries: none" in text


def test_view_text_for_an_orphan_and_an_unknown_view(tmp_path) -> None:
    graph = _shop(tmp_path)
    assert "used by nothing" in view_text(graph, "Screens/Orphan")
    assert view_text(graph, "Screens/Nope") == "sandbox has no view 'Screens/Nope'"


def test_inventory_data_has_routes_callers_tags_and_writes(tmp_path) -> None:
    data = inventory(_shop(tmp_path))
    assert data["routes"] == {"/pdk/home": "Screens/Home"}
    by_view = {v["view"]: v for v in data["views"]}
    assert by_view["Screens/Home"]["routes"] == ["/pdk/home"]
    assert by_view["Screens/Row"]["usedBy"] == ["Screens/Home (repeater)"]
    assert sorted(by_view["Screens/Row"]["tags"][0]["paths"]) == [
        "[default]Plant/A/Running", "[default]Plant/B/Running"]
    assert by_view["Screens/Orphan"]["tags"][0]["writes"] is True
    assert inventory_summary(data) == ("sandbox: 3 views, 1 routes and docks, 2 tag uses (3 distinct tags resolved), "
                                       "1 write point(s), 1 view(s) nothing opens")


def test_inventory_markdown_flags_views_nothing_opens(tmp_path) -> None:
    text = inventory_text(inventory(_shop(tmp_path)), "md")
    assert text.startswith("# Inventory of sandbox")
    assert "| Screens/Row |  | tagPath | 1 |" in text
    assert text.rstrip().endswith("Views nothing opens (1): Screens/Orphan")


def test_inventory_csv_has_one_row_per_resolved_path(tmp_path) -> None:
    rows = list(csv.reader(io.StringIO(inventory_text(inventory(_shop(tmp_path)), "csv"))))
    assert rows[0] == ["view", "component", "pointer", "mode", "writes", "template", "path"]
    row_paths = sorted(row[6] for row in rows[1:] if row[0] == "Screens/Row")
    assert row_paths == ["[default]Plant/A/Running", "[default]Plant/B/Running"]


def test_inventory_json_is_the_whole_data(tmp_path) -> None:
    data = inventory(_shop(tmp_path))
    assert json.loads(inventory_text(data, "json")) == json.loads(json.dumps(data))


def test_cli_refs_and_inventory_read_a_project_directory(tmp_path, capsys) -> None:
    root = _tree(tmp_path, {"Screens/Row": _row_view(),
                         "Screens/Home": view(_embed("Screens/Row", {"tagPath": "[default]Plant/Pump1"}))})
    assert main(["refs", "tag", "Plant/Pump1", "--project", str(root)]) == 0
    assert "[default]Plant/Pump1/Running" in capsys.readouterr().out
    assert main(["refs", "view", "Screens/Row", "--project", str(root)]) == 0
    assert "used by 1:" in capsys.readouterr().out
    out = tmp_path / "inventory.json"
    assert main(["inventory", "--project", str(root), "--format", "json", "--out", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["project"] == "sandbox"


def test_mcp_refs_tool_answers_tag_view_and_writes_the_inventory(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    root = _tree(tmp_path, {"Screens/Row": _row_view(),
                         "Screens/Home": view(_embed("Screens/Row", {"tagPath": "[default]Plant/Pump1"}))})
    assert "[default]Plant/Pump1/Running" in mcp_server.pdk_refs("tag", "Plant/Pump1", project=str(root))
    assert "used by 1:" in mcp_server.pdk_refs("view", "Screens/Row", project=str(root))
    answer = mcp_server.pdk_refs("inventory", project=str(root))
    table = answer.splitlines()[-1]
    assert table.startswith("full table: ") and table.endswith(os.path.join("_inventory", "sandbox.md"))
    assert Path(table.removeprefix("full table: ")).is_file()
    assert mcp_server.pdk_refs("tag", project=str(root)).startswith("pdk: usage")

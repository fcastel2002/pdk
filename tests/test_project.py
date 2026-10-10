"""Project-aware references against a temporary project tree (views, params, style classes)."""

from __future__ import annotations

import json
from pathlib import Path

from pdk.project import PERSPECTIVE_DIR, load_project, locate
from pdk.validate import validate_file
from pdk.validate.refs import _missing_view_hint
from tests.helpers import label, view


def _view(root: Path, path: str, data: dict) -> Path:
    file = root / PERSPECTIVE_DIR / "views" / path / "view.json"
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(data), encoding="utf-8")
    return file


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "sandbox"
    template = view(label(), params={"name": "", "flow": None, "result": 0},
                    propConfig={"params.name": {"paramDirection": "input"},
                                "params.flow": {"paramDirection": "input"},
                                "params.result": {"paramDirection": "output"}})
    _view(root, "Templates/Pump", template)
    style = root / PERSPECTIVE_DIR / "style-classes" / "hmi-card" / "style.json"
    style.parent.mkdir(parents=True)
    style.write_text("{}", encoding="utf-8")
    return root


def test_locate_detects_project_and_view_path(tmp_path) -> None:
    root = _project(tmp_path)
    project, view_path = locate(root / PERSPECTIVE_DIR / "views" / "Templates" / "Pump" / "view.json")
    assert project.name == "sandbox" and view_path == "Templates/Pump"


def test_embedded_views_params_and_style_classes(bundle, tmp_path) -> None:
    root = _project(tmp_path)
    embedded = {"type": "ia.display.view", "meta": {"name": "Pump"}, "position": {"basis": "48px"},
                "props": {"path": "Templates/Pump", "params": {"nmae": "P-101", "result": 1}}}
    missing = {"type": "ia.display.view", "meta": {"name": "Ghost"}, "position": {"basis": "48px"},
               "props": {"path": "TablaAlarmas"}}
    styled = label("Styled", props={"text": "x", "style": {"classes": "hmi-card hmi-missing"}})
    file = _view(root, "Bench/Screen", view(embedded, missing, styled))
    codes = {}
    for d in validate_file(file, bundle).diagnostics:
        codes.setdefault(d["code"], []).append(d)
    assert codes["R011"][0]["severity"] == "error" and "'name'" in codes["R011"][0]["hint"]
    assert "R013" in codes  # flow has no default and is not passed
    assert "R012" not in codes  # embedded views mirror output params
    assert "TablaAlarmas" in codes["R010"][0]["message"]
    assert [d["message"] for d in codes["R015"]] == ["style class 'hmi-missing' does not exist in project sandbox"]


def test_stylesheet_classes_count_as_defined_and_are_inherited(bundle, tmp_path) -> None:
    root = _project(tmp_path)
    css = root / PERSPECTIVE_DIR / "stylesheet" / "stylesheet.css"
    css.parent.mkdir(parents=True)
    css.write_text(
        "/* .psc-Commented { color: red; } */\n"
        ".psc-Alarmas\\/Active_Acked { background-color: #FFD347 !important; }\n"
        ".tab-menu-item.psc-TabHover::after { color: red; }\n",
        encoding="utf-8",
    )
    classes = "Alarmas/Active_Acked TabHover Commented hmi-missing"
    file = _view(root, "Bench/Css", view(label("Styled", props={"text": "x", "style": {"classes": classes}})))
    messages = [d["message"] for d in validate_file(file, bundle).diagnostics if d["code"] == "R015"]
    assert len(messages) == 2
    assert "'Commented'" in messages[0] and "'hmi-missing'" in messages[1]

    child = tmp_path / "child"
    child.mkdir()
    (child / "project.json").write_text(json.dumps({"parent": root.name}), encoding="utf-8")
    inherited = load_project(child)
    assert inherited.has_style_class("Alarmas/Active_Acked") and not inherited.has_style_class("nope")


def test_inherited_views_resolve_through_the_parent(bundle, tmp_path) -> None:
    parent = _project(tmp_path)
    child = tmp_path / "child"
    child.mkdir()
    (child / "project.json").write_text(json.dumps({"parent": parent.name}), encoding="utf-8")
    assert load_project(child).find_view("Templates/Pump") is not None


def test_percent_encoded_folders_resolve_by_their_resource_name(tmp_path) -> None:
    root = tmp_path / "sandbox"
    _view(root, "Bench/%41%75%78/Pump", view(label()))
    project = load_project(root)
    assert project.find_view("Bench/Aux/Pump") is not None
    assert project.find_view("Bench/%41%75%78/Pump") is None
    file = root / PERSPECTIVE_DIR / "views" / "Bench" / "%41%75%78" / "Pump" / "view.json"
    assert locate(file)[1] == "Bench/Aux/Pump"


def _r010_hint(bundle, base: Path, target: str, views: list[str], *, event: bool = False) -> str:
    root = _project(base)
    for path in views:
        _view(root, path, view(label()))
    if event:  # popup open, the event call site
        node = {"type": "ia.input.button", "meta": {"name": "Open"}, "position": {"basis": "48px"},
                "props": {"text": "Open"},
                "events": {"component": {"onActionPerformed": {"type": "popup", "scope": "C",
                                                               "config": {"type": "open", "id": "p",
                                                                          "viewPath": target}}}}}
    else:  # embedded view
        node = {"type": "ia.display.view", "meta": {"name": "Ghost"}, "position": {"basis": "48px"},
                "props": {"path": target}}
    file = _view(root, "Bench/Screen", view(node))
    [hint] = [d["hint"] for d in validate_file(file, bundle).diagnostics if d["code"] == "R010"]
    return hint


def test_missing_view_hint_prefers_a_match_in_its_own_folder(bundle, tmp_path) -> None:
    assert _r010_hint(bundle, tmp_path, "Bench/Run/Popop", ["Bench/Run/Popup"]) == "did you mean 'Bench/Run/Popup'?"


def test_missing_view_hint_points_to_a_sibling_run_when_the_folder_is_absent(bundle, tmp_path) -> None:
    hint = _r010_hint(bundle, tmp_path, "Bench/r3/Auxiliares/Popup", ["Bench/r2/Helpers/Popup"])
    assert hint == ("create the view first (deploy the views it uses before it); "
                    "closest elsewhere: 'Bench/r2/Helpers/Popup'")


def test_missing_view_hint_keeps_the_own_folder_over_a_closer_elsewhere(bundle, tmp_path) -> None:
    views = ["Bench/Run/Sub/Pop", "Bench/Run/Sub2/Popup"]
    assert _r010_hint(bundle, tmp_path, "Bench/Run/Sub/Popup", views) == "did you mean 'Bench/Run/Sub/Pop'?"


def test_event_targets_share_the_missing_view_hint(bundle, tmp_path) -> None:
    assert _r010_hint(bundle, tmp_path / "own", "Bench/Run/Popop", ["Bench/Run/Popup"], event=True) == \
        "did you mean 'Bench/Run/Popup'?"
    hint = _r010_hint(bundle, tmp_path / "run", "Bench/r3/Auxiliares/Popup", ["Bench/r2/Helpers/Popup"], event=True)
    assert hint == _r010_hint(bundle, tmp_path / "embedded", "Bench/r3/Auxiliares/Popup",
                              ["Bench/r2/Helpers/Popup"])
    assert hint.startswith("create the view first") and "closest elsewhere: 'Bench/r2/Helpers/Popup'" in hint


def test_missing_view_hint_without_views_asks_to_create_it() -> None:
    assert _missing_view_hint("Bench/Run/Popup", []) == "create the view first"

"""B010 expression syntax and E007 script indentation, offline."""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from pdk.bundle import Functions
from pdk.validate.code import (
    WRAPPER,
    _expression,
    _Parser,
    _script,
    _Syntax,
    compile_diagnostics,
    compile_requests,
)
from tests.helpers import find, label, view


def _expr_label(expression: str) -> dict[str, Any]:
    return label(propConfig={"props.text": {"binding": {"type": "expr", "config": {"expression": expression}}}})


@pytest.mark.parametrize("text", [
    "{view.custom.a} + {view.custom.b}",
    'if(len(coalesce({view.custom.nombre}, "")) > 0 && len({view.custom.nombre}) <= 20, true, false)',
    "!{view.custom.on} || ({this.props.value} >= 10 && {this.props.value} != -1)",
    "{/root/Table.props.selection.data}[0] = 'x'",
    "dateFormat(now(0), \"HH:mm\") + ' h' // trailing comment",
    "{value} * 2.5e3 % 0x10",
    "toInt({[default]Area/Tag}) xor 1",
    "{view.params.name} like 'P-%'",
    "now()",
    "TRUE",
])
def test_valid_expressions_parse(text: str) -> None:
    _Parser(text).parse()


@pytest.mark.parametrize(("text", "certain"), [
    ("{view.custom.a} and {view.custom.b}", True),
    ("not {view.custom.on}", True),
    ("if({a} > 1, 2, 3", True),
    ('concat("open, {a})', True),
    ("{view.custom.a", True),
    ("({a} + 1))", True),
    ("{a} + ", True),
    ("{a} {b}", False),
    ("enabled", False),
])
def test_invalid_expressions_raise(text: str, certain: bool) -> None:
    with pytest.raises(_Syntax) as raised:
        _Parser(text).parse()
    assert raised.value.certain is certain


def test_b010_points_at_the_expression_and_suggests_the_operator(bundle) -> None:
    found = find(bundle, view(_expr_label("{view.params.a} and {view.params.b}"), params={"a": 1, "b": 2}), "B010")
    assert [(d["severity"], d["pointer"]) for d in found] == [
        ("error", "/root/children/0/propConfig/props.text/binding/config/expression")]
    assert "&&" in found[0]["hint"]


def test_b010_checks_transform_expressions(bundle) -> None:
    binding = {"type": "property", "config": {"path": "view.params.a"},
               "transforms": [{"type": "expression", "expression": "{value} or false"}]}
    found = find(bundle, view(label(propConfig={"props.text": {"binding": binding}}), params={"a": 1}), "B010")
    assert [d["pointer"] for d in found] == ["/root/children/0/propConfig/props.text/binding/transforms/0/expression"]


def test_e007_flags_unindented_bodies_everywhere(bundle) -> None:
    transform = {"type": "property", "config": {"path": "view.params.a"},
                 "transforms": [{"type": "script", "code": "return value * 2"}]}
    button = {"type": "ia.input.button", "meta": {"name": "Go"}, "position": {"basis": "32px"},
              "events": {"component": {"onActionPerformed": {"type": "script", "scope": "G",
                                                             "config": {"script": "\tx = 1\nprint(x)"}}}}}
    data = view(label(propConfig={"props.text": {"binding": transform}}), button, params={"a": 1},
                events={"system": {"onStartup": {"type": "script", "scope": "G",
                                                 "config": {"script": "\tself.custom.x = 1"}}}})
    found = find(bundle, data, "E007")
    assert sorted(d["pointer"] for d in found) == [
        "/root/children/0/propConfig/props.text/binding/transforms/0/code",
        "/root/children/1/events/component/onActionPerformed/config/script",
    ]
    assert "line 2" in next(d["message"] for d in found if "onActionPerformed" in d["pointer"])


def _script_findings(bundle, code: str) -> list[dict[str, Any]]:
    binding = {"type": "property", "config": {"path": "view.params.a"},
               "transforms": [{"type": "script", "code": code}]}
    return find(bundle, view(label(propConfig={"props.text": {"binding": binding}}), params={"a": 1}), "E007")


@pytest.mark.parametrize("code", [
    '\tq = """\nSELECT *\nFROM t\n"""\n\treturn q',  # triple-quoted string with column-0 lines
    "\tx = foo(1,\n2)\n\treturn x",  # call continued inside brackets
    "\tx = 1 + \\\n2\n\treturn x",  # backslash continuation
    "\tx = 1\n# note\n\treturn x",  # column-0 comment
])
def test_e007_ignores_column_zero_lines_python_accepts(bundle, code: str) -> None:
    assert _script_findings(bundle, code) == []


def test_e007_flags_a_column_zero_statement_after_a_bracket_or_string_closes(bundle) -> None:
    found = _script_findings(bundle, "\tx = foo(1,\n2)\ny = 3")
    assert len(found) == 1 and "line 3" in found[0]["message"]
    found = _script_findings(bundle, '\tq = """\nSELECT\n"""\ny = 1')
    assert len(found) == 1 and "line 4" in found[0]["message"]


def test_e007_flags_the_first_column_zero_statement(bundle) -> None:
    found = _script_findings(bundle, "\tx = 1\ny = 2")
    assert len(found) == 1 and found[0]["severity"] == "error"
    assert "line 2" in found[0]["message"]
    assert found[0]["pointer"] == "/root/children/0/propConfig/props.text/binding/transforms/0/code"


def test_e007_flags_an_unindented_first_line(bundle) -> None:
    found = _script_findings(bundle, "return value * 2\n\treturn value")
    assert len(found) == 1 and "line 1" in found[0]["message"]


# --- B011 and E008: names the gateway lists (functions.json) -----------------------------------


def _functions(*names: str, script: tuple[str, ...] = ()) -> Functions:
    return Functions({name.lower(): name for name in names}, frozenset(script))


SYSTEM = _functions(script=("system", "system.tag", "system.tag.readBlocking", "system.tag.writeBlocking",
                            "system.util", "system.util.getLogger"))


def test_b011_flags_an_unknown_function_and_suggests_the_gateway_name() -> None:
    found = _expression("toStrng(1)", "/p", "root", _functions("toString", "toStr"))
    assert [(d["code"], d["severity"], d["pointer"]) for d in found] == [("B011", "error", "/p")]
    assert "toStrng()" in found[0]["message"] and "did you mean toString?" in found[0]["message"]
    assert found[0]["hint"]


@pytest.mark.parametrize("text", ["dateFormat(now(), 'HH:mm')", "DATEFORMAT(now())", "dateformat(NOW())"])
def test_b011_lookup_is_case_insensitive(text: str) -> None:
    assert _expression(text, "/p", "root", _functions("dateFormat", "now")) == []


def test_b011_needs_a_function_list() -> None:
    assert _expression("toStrng(1)", "/p", "root", None) == []
    assert _expression("toStrng(1)", "/p", "root", Functions({}, frozenset())) == []


@pytest.mark.parametrize("text", ["toStrng(1) and 2", "a and b"])
def test_b010_still_wins_over_b011(text: str) -> None:
    found = _expression(text, "/p", "root", _functions("toString"))
    assert [(d["code"], d["severity"]) for d in found] == [("B010", "error")]


def test_b011_reaches_a_view_through_the_bundle(bundle) -> None:
    loaded = dataclasses.replace(bundle, functions=_functions("dateFormat", "toString"))
    found = find(loaded, view(_expr_label("toStrng({view.params.a})"), params={"a": 1}), "B011")
    assert [d["pointer"] for d in found] == ["/root/children/0/propConfig/props.text/binding/config/expression"]


def test_e008_flags_an_unknown_system_name_and_suggests_the_gateway_name() -> None:
    found = _script("\tsystem.tag.writeBlockin([], [])", "/p", "root/Label", SYSTEM)
    assert [d["code"] for d in found] == ["E008"]
    assert found[0]["pointer"] == "/p" and found[0]["component"] == "root/Label"
    assert "script line 1" in found[0]["message"] and "system.tag.writeBlocking" in found[0]["message"]


def test_e008_ignores_names_in_strings_and_comments() -> None:
    assert _script("\tx = 'system.nope.x'  # system.foo.bar", "/p", "", SYSTEM) == []


@pytest.mark.parametrize("body", ['\tsystem.util.getLogger("a").info("b")', "\tsystem.tag"])
def test_e008_accepts_known_modules_and_calls_on_their_results(body: str) -> None:
    assert _script(body, "/p", "", SYSTEM) == []


def test_e008_is_not_checked_for_an_unindented_body() -> None:
    found = _script("system.tag.writeBlockin([], [])", "/p", "", SYSTEM)
    assert [d["code"] for d in found] == ["E007"]


# --- the gateway's compile of the view's scripts ----------------------------------------------------


def _scripted_view() -> dict[str, Any]:
    transform = {"type": "script", "code": "\treturn value * 2"}
    return view(
        label("Label",
              propConfig={"props.text": {"binding": {"type": "property", "config": {"path": "view.params.a"},
                                                     "transforms": [transform]},
                                         "onChange": {"script": "\tprint(value)"}}},
              scripts={"customMethods": [{"name": "go", "params": [], "script": "\treturn 1"}]}),
        params={"a": 1},
        events={"system": {"onStartup": {"type": "script", "scope": "G", "config": {"script": "\tpass"}}}})


def test_compile_requests_wrap_every_script_body_in_order() -> None:
    sources, where = compile_requests(_scripted_view())
    assert [s["id"] for s in sources] == ["0", "1", "2", "3"]
    assert all(s["code"].startswith(WRAPPER) for s in sources)
    assert sources[0]["code"] == WRAPPER + "\treturn value * 2\n\tpass\n"
    assert [where[str(i)][0] for i in range(4)] == [
        "/root/children/0/propConfig/props.text/binding/transforms/0/code",
        "/root/children/0/propConfig/props.text/onChange/script",
        "/events/system/onStartup/config/script",
        "/root/children/0/scripts/customMethods/0/script",
    ]


def test_compile_diagnostics_map_a_failure_to_its_body_line() -> None:
    _, where = compile_requests(_scripted_view())
    found = compile_diagnostics([{"id": 1, "ok": False, "line": 3, "column": 5, "message": "m"}], where)
    assert [(d["code"], d["severity"], d["pointer"]) for d in found] == [
        ("E009", "error", "/root/children/0/propConfig/props.text/onChange/script")]
    assert "at line 2, column 5: m" in found[0]["message"]


def test_compile_diagnostics_ignore_passing_and_unknown_sources() -> None:
    _, where = compile_requests(_scripted_view())
    assert compile_diagnostics([{"id": 0, "ok": True}, {"id": 9, "ok": False, "line": 2, "message": "x"}], where) == []


def test_compile_diagnostics_without_a_line_give_the_message_alone() -> None:
    _, where = compile_requests(_scripted_view())
    found = compile_diagnostics([{"id": 3, "ok": False, "message": "boom"}], where)
    assert [d["pointer"] for d in found] == ["/root/children/0/scripts/customMethods/0/script"]
    assert found[0]["message"] == "script does not compile: boom"

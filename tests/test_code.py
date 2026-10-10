"""B010 expression syntax and E007 script indentation, offline."""

from __future__ import annotations

from typing import Any

import pytest

from pdk.validate.code import _Parser, _Syntax
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

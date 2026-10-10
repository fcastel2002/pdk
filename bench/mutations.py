"""Mutation benchmark: inject typical LLM mistakes into known-good views and measure detection.

Run: `uv run python -m bench.mutations` (prints the score per operator).
"""

from __future__ import annotations

import copy
import json
import random
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

from pdk import REPO_ROOT
from pdk.bundle import Bundle, load_bundle
from pdk.diagnostics import DEFAULT_BUDGET, Diagnostic, escape, estimate_tokens, render
from pdk.shape import SchemaRef, object_shape, resolve
from pdk.validate import validate_view
from pdk.validate.refs import binding_expressions

FIXTURES = REPO_ROOT / "tests" / "fixtures" / "compiler-v1"
SEED = 20261004


@dataclass(frozen=True)
class Site:
    component: dict[str, Any]
    pointer: str
    parent: dict[str, Any] | None


@dataclass(frozen=True)
class Mutant:
    operator: str
    fixture: str
    view: dict[str, Any]
    code: str  # expected diagnostic code
    pointer: str  # where the defect lives


Operator = Callable[[dict[str, Any], random.Random, Bundle], tuple[str, str] | None]


def _sites(view: dict[str, Any]) -> list[Site]:
    out: list[Site] = []

    def visit(component: Any, pointer: str, parent: dict[str, Any] | None) -> None:
        if not isinstance(component, dict):
            return
        out.append(Site(component, pointer, parent))
        for i, child in enumerate(component.get("children") or []):
            visit(child, f"{pointer}/children/{i}", component)

    visit(view.get("root"), "/root", None)
    return out


def _declared(bundle: Bundle, ctype: Any) -> dict[str, SchemaRef]:
    component = bundle.components.get(ctype) if isinstance(ctype, str) else None
    if component is None:
        return {}
    shape = object_shape(SchemaRef(component.schema, component.schema), bundle)
    return shape.properties if shape else {}


def _typo(word: str, rng: random.Random) -> str:
    if len(word) < 4:
        return word + word[-1]
    i = rng.randrange(1, len(word) - 2)
    kind = rng.choice(("swap", "drop", "double"))
    if kind == "swap" and word[i] != word[i + 1]:
        return word[:i] + word[i + 1] + word[i] + word[i + 2:]
    if kind == "drop":
        return word[:i] + word[i + 1:]
    return word[:i] + word[i] + word[i:]


def _known(view: dict[str, Any], bundle: Bundle) -> list[Site]:
    return [s for s in _sites(view) if s.component.get("type") in bundle.components]


def _ensure(component: dict[str, Any], key: str) -> dict[str, Any]:
    value = component.get(key)
    if not isinstance(value, dict):
        value = component[key] = {}
    return value


# --- operators: each mutates `view` in place and returns (expected code, pointer) -----------------


def prop_typo(view: dict[str, Any], rng: random.Random, bundle: Bundle) -> tuple[str, str] | None:
    candidates = [(s, k) for s in _known(view, bundle) for k in (s.component.get("props") or {})
                  if k in _declared(bundle, s.component["type"]) and len(k) >= 4]
    if not candidates:
        return None
    site, key = rng.choice(candidates)
    declared = _declared(bundle, site.component["type"])
    typo = next((t for t in (_typo(key, rng) for _ in range(10)) if t not in declared and t != key), None)
    if typo is None:
        return None
    props = site.component["props"]
    props[typo] = props.pop(key)
    return "P001", f"{site.pointer}/props/{escape(typo)}"


INVENTED = ("label", "value", "color", "fontSize", "onClick", "title", "disabled", "backgroundColor", "tooltip",
            "placeholder", "padding", "align", "width", "height", "icon")


def prop_invented(view: dict[str, Any], rng: random.Random, bundle: Bundle) -> tuple[str, str] | None:
    sites = [s for s in _known(view, bundle) if not s.component["type"].startswith("ia.shapes.")]
    if not sites:
        return None
    site = rng.choice(sites)
    declared = _declared(bundle, site.component["type"])
    options = [k for k in INVENTED if k not in declared]
    if not options:
        return None
    key = rng.choice(options)
    _ensure(site.component, "props")[key] = "x"
    return "P001", f"{site.pointer}/props/{key}"


def _single_type(ref: SchemaRef, bundle: Bundle) -> str | None:
    resolved = resolve(ref, bundle)
    schema = resolved.schema if resolved else None
    if isinstance(schema, dict) and isinstance(schema.get("type"), str) and "enum" not in schema:
        return schema["type"] if schema["type"] in ("string", "number", "boolean") else None
    return None


def prop_wrong_type(view: dict[str, Any], rng: random.Random, bundle: Bundle) -> tuple[str, str] | None:
    candidates = []
    for site in _known(view, bundle):
        for key, ref in _declared(bundle, site.component["type"]).items():
            kind = _single_type(ref, bundle)
            if kind:
                candidates.append((site, key, kind))
    if not candidates:
        return None
    site, key, kind = rng.choice(candidates)
    _ensure(site.component, "props")[key] = {"string": True, "number": "fast", "boolean": "yes"}[kind]
    return "P002", f"{site.pointer}/props/{escape(key)}"


def prop_bad_enum(view: dict[str, Any], rng: random.Random, bundle: Bundle) -> tuple[str, str] | None:
    candidates = []
    for site in _known(view, bundle):
        for key, ref in _declared(bundle, site.component["type"]).items():
            resolved = resolve(ref, bundle)
            enum = resolved.schema.get("enum") if resolved and isinstance(resolved.schema, dict) else None
            if isinstance(enum, list) and enum and all(isinstance(v, str) for v in enum):
                candidates.append((site, key, enum))
    if not candidates:
        return None
    site, key, enum = rng.choice(candidates)
    wrong = "middle" if "center" in enum else next(v.upper() for v in enum if v.upper() not in enum) \
        if any(v.upper() not in enum for v in enum) else "unknown"
    _ensure(site.component, "props")[key] = wrong
    return "P003", f"{site.pointer}/props/{escape(key)}"


def position_foreign_key(view: dict[str, Any], rng: random.Random, bundle: Bundle) -> tuple[str, str] | None:
    sites = [s for s in _sites(view) if s.parent and s.parent.get("type") in ("ia.container.flex",
                                                                              "ia.container.coord")]
    if not sites:
        return None
    site = rng.choice(sites)
    key, value = ("x", 10) if site.parent["type"] == "ia.container.flex" else ("basis", "100px")
    _ensure(site.component, "position")[key] = value
    return "P010", f"{site.pointer}/position/{key}"


def _bindable(view: dict[str, Any], bundle: Bundle) -> list[tuple[Site, str]]:
    out = []
    for site in _known(view, bundle):
        declared = _declared(bundle, site.component["type"])
        for key in ("text", "value", "enabled", "visible", "data", "source", "path", "style"):
            if key in declared:
                out.append((site, key))
                break
    return out


def binding_type_alias(view: dict[str, Any], rng: random.Random, bundle: Bundle) -> tuple[str, str] | None:
    options = _bindable(view, bundle)
    if not options:
        return None
    site, key = rng.choice(options)
    path = f"props.{key}"
    wrong = rng.choice(("expression", "tags", "prop", "expr-structure"))
    _ensure(site.component, "propConfig")[path] = {"binding": {"type": wrong, "config": {"expression": "1"}}}
    return "B003", f"{site.pointer}/propConfig/{path}/binding/type"


def binding_missing_required(view: dict[str, Any], rng: random.Random, bundle: Bundle) -> tuple[str, str] | None:
    options = _bindable(view, bundle)
    if not options:
        return None
    site, key = rng.choice(options)
    path = f"props.{key}"
    btype, config, missing = rng.choice((("tag", {"mode": "direct"}, "tagPath"),
                                         ("expr", {}, "expression"),
                                         ("query", {"parameters": {}}, "queryPath")))
    _ensure(site.component, "propConfig")[path] = {"binding": {"type": btype, "config": config}}
    return "B004", f"{site.pointer}/propConfig/{path}/binding/config/{missing}"


def transform_type(view: dict[str, Any], rng: random.Random, bundle: Bundle) -> tuple[str, str] | None:
    options = _bindable(view, bundle)
    if not options:
        return None
    site, key = rng.choice(options)
    path = f"props.{key}"
    wrong = rng.choice(("expr", "python", "mapping", "lookup"))
    _ensure(site.component, "propConfig")[path] = {"binding": {
        "type": "expr", "config": {"expression": "1"}, "transforms": [{"type": wrong, "expression": "{value}"}]}}
    return "B005", f"{site.pointer}/propConfig/{path}/binding/transforms/0/type"


def component_type_typo(view: dict[str, Any], rng: random.Random, bundle: Bundle) -> tuple[str, str] | None:
    sites = [s for s in _known(view, bundle) if s.parent is not None]
    if not sites:
        return None
    site = rng.choice(sites)
    prefix, _, last = site.component["type"].rpartition(".")
    for _ in range(10):
        wrong = f"{prefix}.{_typo(last, rng)}"
        if wrong not in bundle.components:
            site.component["type"] = wrong
            return "S006", f"{site.pointer}/type"
    return None


def duplicate_name(view: dict[str, Any], rng: random.Random, bundle: Bundle) -> tuple[str, str] | None:
    sites = [s for s in _sites(view) if len(s.component.get("children") or []) >= 2]
    if not sites:
        return None
    site = rng.choice(sites)
    first, second = site.component["children"][0], site.component["children"][1]
    name = (first.get("meta") or {}).get("name")
    if not name:
        return None
    _ensure(second, "meta")["name"] = name
    return "S010", f"{site.pointer}/children/1/meta/name"


def children_on_leaf(view: dict[str, Any], rng: random.Random, bundle: Bundle) -> tuple[str, str] | None:
    sites = [s for s in _known(view, bundle) if not bundle.components[s.component["type"]].is_container]
    if not sites:
        return None
    site = rng.choice(sites)
    site.component["children"] = [{"type": "ia.display.label", "meta": {"name": "Inner"}, "props": {"text": "x"}}]
    return "S007", f"{site.pointer}/children"


def wrong_scope(view: dict[str, Any], rng: random.Random, bundle: Bundle) -> tuple[str, str] | None:
    sites = _known(view, bundle)
    if not sites:
        return None
    site = rng.choice(sites)
    handler = rng.choice(({"type": "script", "scope": "C", "config": {"script": "\tpass"}},
                          {"type": "nav", "scope": "G", "config": {"page": "/"}},
                          {"type": "popup", "scope": "G", "config": {"type": "close", "id": "p"}}))
    _ensure(_ensure(site.component, "events"), "dom")["onClick"] = handler
    return "E004", f"{site.pointer}/events/dom/onClick/scope"


def dom_event_typo(view: dict[str, Any], rng: random.Random, bundle: Bundle) -> tuple[str, str] | None:
    sites = _known(view, bundle)
    if not sites:
        return None
    site = rng.choice(sites)
    name = rng.choice(("onClik", "onclick", "onDoubleclick", "onMouseOverr", "onPress", "onChange"))
    _ensure(_ensure(site.component, "events"), "dom")[name] = {"type": "script", "scope": "G",
                                                               "config": {"script": "\tpass"}}
    return "E002", f"{site.pointer}/events/dom/{name}"


def propconfig_bad_path(view: dict[str, Any], rng: random.Random, bundle: Bundle) -> tuple[str, str] | None:
    candidates = [(s, k) for s in _known(view, bundle) for k in _declared(bundle, s.component["type"]) if len(k) >= 4]
    if not candidates:
        return None
    site, key = rng.choice(candidates)
    declared = _declared(bundle, site.component["type"])
    typo = next((t for t in (_typo(key, rng) for _ in range(10)) if t not in declared), None)
    if typo is None:
        return None
    path = f"props.{typo}"
    _ensure(site.component, "propConfig")[path] = {"binding": {"type": "expr", "config": {"expression": "1"}}}
    return "B001", f"{site.pointer}/propConfig/{escape(path)}"


def meta_name_invalid(view: dict[str, Any], rng: random.Random, bundle: Bundle) -> tuple[str, str] | None:
    sites = [s for s in _sites(view) if s.parent is not None]
    if not sites:
        return None
    site = rng.choice(sites)
    _ensure(site.component, "meta")["name"] = rng.choice(("Motor #1", "Pump.Speed", "1stLabel", "Temp/Value"))
    return "P008", f"{site.pointer}/meta/name"


def envelope_key(view: dict[str, Any], rng: random.Random, bundle: Bundle) -> tuple[str, str] | None:
    sites = [s for s in _sites(view) if "props" in s.component]
    if not sites:
        return None
    site = rng.choice(sites)
    wrong = rng.choice(("properties", "prop", "attributes"))
    site.component[wrong] = site.component.pop("props")
    return "S004", f"{site.pointer}/{wrong}"


def undeclared_param_ref(view: dict[str, Any], rng: random.Random, bundle: Bundle) -> tuple[str, str] | None:
    options = _bindable(view, bundle)
    if not options:
        return None
    site, key = rng.choice(options)
    params = view.get("params") if isinstance(view.get("params"), dict) else {}
    name = next(n for n in ("motorId", "tagPathBase", "equipmentName", "setpoint2") if n not in params)
    path = f"props.{key}"
    _ensure(site.component, "propConfig")[path] = {"binding": {"type": "property",
                                                                "config": {"path": f"view.params.{name}"}}}
    return "R001", f"{site.pointer}/propConfig/{path}/binding/config/path"


def bad_component_path(view: dict[str, Any], rng: random.Random, bundle: Bundle) -> tuple[str, str] | None:
    options = [(s, k) for s, k in _bindable(view, bundle) if s.parent is not None]
    if not options:
        return None
    site, key = rng.choice(options)
    siblings = {(c.get("meta") or {}).get("name") for c in site.parent.get("children") or []}
    missing = next(n for n in ("Label_1", "SpeedLabel", "Display", "Value") if n not in siblings)
    path = f"props.{key}"
    _ensure(site.component, "propConfig")[path] = {"binding": {"type": "property",
                                                                "config": {"path": f"../{missing}.props.text"}}}
    return "R004", f"{site.pointer}/propConfig/{path}/binding/config/path"


def _set_at(view: dict[str, Any], pointer: str, value: Any) -> None:
    *parents, last = [token.replace("~1", "/").replace("~0", "~") for token in pointer.split("/")[1:]]
    node: Any = view
    for token in parents:
        node = node[int(token)] if isinstance(node, list) else node[token]
    node[int(last) if isinstance(node, list) else last] = value


def _expressions(view: dict[str, Any]) -> list[tuple[str, str]]:
    """(pointer, text) of every binding expression on a component, transform expressions included."""
    out: list[tuple[str, str]] = []
    for site in _sites(view):
        config = site.component.get("propConfig")
        for path, entry in (config.items() if isinstance(config, dict) else ()):
            binding = entry.get("binding") if isinstance(entry, dict) else None
            base = f"{site.pointer}/propConfig/{escape(path)}/binding"
            out += [(pointer, text) for text, pointer, _ in binding_expressions(binding, base)]
    return out


def _scripts(view: dict[str, Any]) -> list[tuple[str, str]]:
    """(pointer, body) of the scripts the offline check reads: transform code and script event actions."""
    out: list[tuple[str, Any]] = []
    for site in _sites(view):
        config = site.component.get("propConfig")
        for path, entry in (config.items() if isinstance(config, dict) else ()):
            binding = entry.get("binding") if isinstance(entry, dict) else None
            transforms = binding.get("transforms") if isinstance(binding, dict) else None
            for i, transform in enumerate(transforms or []):
                if isinstance(transform, dict) and transform.get("type") == "script":
                    out.append((f"{site.pointer}/propConfig/{escape(path)}/binding/transforms/{i}/code",
                                transform.get("code")))
        events = site.component.get("events")
        for group, handlers in (events.items() if isinstance(events, dict) else ()):
            for name, handler in (handlers.items() if isinstance(handlers, dict) else ()):
                for i, item in enumerate(handler if isinstance(handler, list) else [handler]):
                    prefix = f"{site.pointer}/events/{escape(group)}/{escape(name)}"
                    where = f"{prefix}/{i}" if isinstance(handler, list) else prefix
                    if isinstance(item, dict) and item.get("type") == "script" and isinstance(item.get("config"), dict):
                        out.append((f"{where}/config/script", item["config"].get("script")))
    return [(pointer, body) for pointer, body in out if isinstance(body, str)]


def expr_word_operator(view: dict[str, Any], rng: random.Random, bundle: Bundle) -> tuple[str, str] | None:
    candidates = [(pointer, text) for pointer, text in _expressions(view) if "&&" in text or "||" in text]
    if not candidates:
        return None
    pointer, text = rng.choice(candidates)
    old, new = ("&&", " and ") if "&&" in text else ("||", " or ")
    _set_at(view, pointer, text.replace(old, new, 1))
    return "B010", pointer


def script_unindented(view: dict[str, Any], rng: random.Random, bundle: Bundle) -> tuple[str, str] | None:
    candidates = []
    for pointer, body in _scripts(view):
        first = next((line for line in body.split("\n") if line.strip()), "")
        if first.startswith("\t"):
            candidates.append((pointer, body))
    if not candidates:
        return None
    pointer, body = rng.choice(candidates)
    lines = body.split("\n")
    i = next(i for i, line in enumerate(lines) if line.strip())
    lines[i] = lines[i].lstrip(" \t")
    _set_at(view, pointer, "\n".join(lines))
    return "E007", pointer


OPERATORS: dict[str, Operator] = {f.__name__: f for f in (
    prop_typo, prop_invented, prop_wrong_type, prop_bad_enum, position_foreign_key, binding_type_alias,
    binding_missing_required, transform_type, component_type_typo, duplicate_name, children_on_leaf,
    wrong_scope, dom_event_typo, propconfig_bad_path, meta_name_invalid, envelope_key, undeclared_param_ref,
    bad_component_path, expr_word_operator, script_unindented,
)}


def fixtures() -> Iterator[tuple[str, dict[str, Any]]]:
    for path in sorted(FIXTURES.rglob("view.json")):
        yield path.relative_to(FIXTURES).as_posix(), json.loads(path.read_text(encoding="utf-8"))


def mutants(bundle: Bundle) -> Iterator[Mutant]:
    for name, view in fixtures():
        for op_name, operator in OPERATORS.items():
            rng = random.Random(f"{SEED}:{name}:{op_name}")
            mutated = copy.deepcopy(view)
            expected = operator(mutated, rng, bundle)
            if expected:
                yield Mutant(op_name, name, mutated, expected[0], expected[1])


def detected(mutant: Mutant, diagnostics: list[Diagnostic]) -> bool:
    return any(d["code"] == mutant.code and d["severity"] in ("error", "warning")
               and (d["pointer"] == mutant.pointer or d["pointer"].startswith(mutant.pointer + "/"))
               for d in diagnostics)


@dataclass
class Score:
    mutants: int = 0
    detected: int = 0
    diagnostics: int = 0
    with_pointer_and_hint: int = 0
    within_budget: int = 0

    @property
    def rate(self) -> float:
        return self.detected / self.mutants if self.mutants else 1.0


def run(bundle: Bundle | None = None) -> tuple[dict[str, Score], list[Mutant]]:
    bundle = bundle or load_bundle()
    scores: dict[str, Score] = {name: Score() for name in OPERATORS}
    missed: list[Mutant] = []
    for mutant in mutants(bundle):
        diagnostics = validate_view(mutant.view, bundle)
        score = scores[mutant.operator]
        score.mutants += 1
        if detected(mutant, diagnostics):
            score.detected += 1
        else:
            missed.append(mutant)
        score.diagnostics += len(diagnostics)
        score.with_pointer_and_hint += sum(1 for d in diagnostics if d["pointer"] and d["hint"])
        if estimate_tokens(render(diagnostics, title=mutant.fixture)) <= DEFAULT_BUDGET:
            score.within_budget += 1
    return scores, missed


def total(scores: dict[str, Score]) -> Score:
    out = Score()
    for s in scores.values():
        out.mutants += s.mutants
        out.detected += s.detected
        out.diagnostics += s.diagnostics
        out.with_pointer_and_hint += s.with_pointer_and_hint
        out.within_budget += s.within_budget
    return out


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        getattr(stream, "reconfigure", lambda **_: None)(encoding="utf-8")
    scores, missed = run()
    print(f"{'operator':26} {'mutants':>8} {'detected':>9} {'rate':>7}")
    for name, s in scores.items():
        print(f"{name:26} {s.mutants:8} {s.detected:9} {s.rate:7.1%}")
    t = total(scores)
    print(f"{'TOTAL':26} {t.mutants:8} {t.detected:9} {t.rate:7.1%}")
    print(f"diagnostics with pointer+hint: {t.with_pointer_and_hint}/{t.diagnostics}; "
          f"reports within {DEFAULT_BUDGET} tokens: {t.within_budget}/{t.mutants}")
    for m in missed[:15]:
        print(f"  missed {m.operator} in {m.fixture}: expected {m.code} at {m.pointer}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

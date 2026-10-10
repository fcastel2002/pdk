"""Exit criteria of F1: clean fixtures and mutation detection."""

from __future__ import annotations

import pytest

from bench import mutations
from pdk.bundle import BundleError, load_bundle
from pdk.validate import validate_paths

# Compiler-derived fixtures that bind view params they never declare: real defects, not false positives.
KNOWN_DEFECTS = {
    "ignition/3.3.8/ButtonEnabledExample/view.json": {"R001"},
    "ignition/3.3.8/ReactiveStyleClassesExample/view.json": {"R001"},
    "ignition/3.3.8/ReactiveVisibilityExample/view.json": {"R001"},
}


@pytest.fixture(scope="session")
def bundle():
    try:
        return load_bundle()
    except BundleError as exc:
        pytest.skip(str(exc))


def test_fixtures_have_no_false_positives(bundle) -> None:
    results = validate_paths(mutations.FIXTURES, bundle)
    assert len(results) == 65
    for result in results:
        name = result.path.relative_to(mutations.FIXTURES).as_posix()
        codes = {d["code"] for d in result.diagnostics if d["severity"] != "info"}
        assert codes <= KNOWN_DEFECTS.get(name, set()), (name, result.diagnostics)


def test_mutation_score(bundle) -> None:
    scores, missed = mutations.run(bundle)
    total = mutations.total(scores)
    assert total.rate >= 0.95, [(m.operator, m.fixture, m.code, m.pointer) for m in missed]
    assert total.with_pointer_and_hint == total.diagnostics
    assert total.within_budget >= 0.9 * total.mutants

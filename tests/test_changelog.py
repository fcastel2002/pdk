from __future__ import annotations

import pytest

from tools import changelog

TEXT = """# Changelog

## [Unreleased]

### Fixed
- a fix

## [0.2.0] - 2026-10-14

### Added
- a thing
"""


def test_section_reads_up_to_the_next_version() -> None:
    assert changelog.section(TEXT, "Unreleased") == "### Fixed\n- a fix"
    assert changelog.section(TEXT, "0.2.0") == "### Added\n- a thing"
    assert changelog.section(TEXT, "9.9.9") == ""


@pytest.mark.parametrize(("pending", "expected"), [
    ("", None),
    ("### Fixed\n", None),
    ("### Fixed\n- x", "0.2.1"),
    ("### Added\n- x\n### Fixed\n- y", "0.3.0"),
    ("### Changed\n- x", "0.3.0"),
    ("### Removed\n- x", "0.3.0"),
    ("### Changed\n- BREAKING: x", "0.3.0"),
])
def test_next_version_before_1_0(pending, expected) -> None:
    assert changelog.next_version("0.2.0", pending) == expected


def test_next_version_after_1_0() -> None:
    assert changelog.next_version("1.4.2", "### Removed\n- x") == "2.0.0"
    assert changelog.next_version("v1.4.2", "### Fixed\n- x") == "1.4.3"


def test_roll_keeps_an_empty_unreleased_on_top() -> None:
    rolled = changelog.roll(TEXT, "0.2.1", "2026-11-01")
    assert changelog.section(rolled, "Unreleased") == ""
    assert changelog.section(rolled, "0.2.1") == "### Fixed\n- a fix"
    with pytest.raises(ValueError):
        changelog.roll(rolled, "0.2.1", "2026-11-01")


def test_the_real_changelog_has_an_unreleased_section() -> None:
    assert changelog.UNRELEASED in changelog.CHANGELOG.read_text(encoding="utf-8")

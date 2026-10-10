"""The documentation lists every stable code, so a new code cannot ship undocumented."""

from __future__ import annotations

from pdk import REPO_ROOT
from pdk.diagnostics import CODES
from pdk.gateway.client import CODES as GATEWAY_CODES


def test_every_code_is_documented() -> None:
    text = (REPO_ROOT / "docs" / "reference" / "diagnostics.rst").read_text(encoding="utf-8")
    missing = [code for code in [*CODES, *GATEWAY_CODES] if f"``{code}``" not in text]
    assert not missing, f"document these codes in docs/reference/diagnostics.rst: {missing}"

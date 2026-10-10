from __future__ import annotations

import pytest

from pdk.bundle import Bundle, BundleError, load_bundle


@pytest.fixture(scope="session")
def bundle() -> Bundle:
    try:
        return load_bundle()
    except BundleError as exc:
        pytest.skip(f"{exc}")

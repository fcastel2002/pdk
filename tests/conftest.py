from __future__ import annotations

import os

import pytest

os.environ["PDK_UPDATE_CHECK"] = "0"  # no network call to GitHub from the tests

from pdk.bundle import Bundle, BundleError, load_bundle


@pytest.fixture(scope="session")
def bundle() -> Bundle:
    try:
        return load_bundle()
    except BundleError as exc:
        pytest.skip(f"{exc}")

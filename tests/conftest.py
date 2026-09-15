"""Shared isolation fixtures for the unit-test suite."""

import os

import pytest


@pytest.fixture(autouse=True)
def isolate_cf_baostock_environment(monkeypatch):
    """Keep host CF BaoStock credentials out of unit tests by default."""
    for name in tuple(os.environ):
        if name.startswith("CF_BAOSTOCK_"):
            monkeypatch.delenv(name)

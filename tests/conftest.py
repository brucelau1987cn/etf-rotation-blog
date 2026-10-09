"""Shared isolation fixtures for the unit-test suite."""

import os

import pytest


@pytest.fixture(autouse=True)
def isolate_cf_baostock_environment(monkeypatch):
    """Keep host CF BaoStock credentials out of unit tests by default."""
    for name in tuple(os.environ):
        if name.startswith("CF_BAOSTOCK_"):
            monkeypatch.delenv(name)
    # The calendar gate loads host credentials from disk when the environment
    # does not already carry them. Tests must never reach a real provider
    # through that path, so the loader is disabled unless a test opts in by
    # monkeypatching it directly.
    monkeypatch.setenv("CF_BAOSTOCK_DISABLE_CREDENTIAL_FILE", "1")

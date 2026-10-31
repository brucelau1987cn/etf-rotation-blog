"""The pool's evaluation date must be pinnable for a missed-trade-date replay.

`run_date` / `evaluation_date` mean "the trade date this evaluation applies
to". They default to the local calendar day, but a weekend backfill of a missed
session must be able to pin them — otherwise the selector rejects its own
recommendations with `recommendation/pool date mismatch`.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


@pytest.fixture
def pool_module():
    spec = importlib.util.spec_from_file_location("garden_pool_date_test", SCRIPTS / "generate_garden_pool.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_evaluation_date_honours_explicit_override(pool_module, monkeypatch):
    monkeypatch.setenv("A_SHARE_EVALUATION_DATE", "2026-10-30")
    assert pool_module.local_today() == "2026-10-30"


def test_evaluation_date_defaults_to_local_day(pool_module, monkeypatch):
    monkeypatch.delenv("A_SHARE_EVALUATION_DATE", raising=False)
    assert pool_module.local_today() == pool_module.now_cn().date().isoformat()


def test_evaluation_date_rejects_malformed_override(pool_module, monkeypatch):
    monkeypatch.setenv("A_SHARE_EVALUATION_DATE", "2026-10-30T22:10:00")
    with pytest.raises(SystemExit):
        pool_module.local_today()
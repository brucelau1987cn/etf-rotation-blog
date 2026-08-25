from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

SCRIPT = Path('/root/.hermes/scripts/precheck_a_share_nightly.py')


def load_module():
    spec = importlib.util.spec_from_file_location('precheck_a_share_nightly', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def make_qfq_db(path: Path, coverage: int) -> None:
    with sqlite3.connect(path) as db:
        db.execute(
            'CREATE TABLE daily_bars ('
            'symbol TEXT, trade_date TEXT, adjustment TEXT, is_final INTEGER)'
        )
        db.executemany(
            'INSERT INTO daily_bars VALUES (?, ?, ?, ?)',
            [(f'{i:06d}', '2026-08-25', 'qfq', 1) for i in range(coverage)],
        )


def test_precheck_accepts_partial_qfq_before_cache_refresh(tmp_path):
    module = load_module()
    db_path = tmp_path / 'etf-compass.db'
    make_qfq_db(db_path, coverage=26)

    assert module.qfq_precheck_problems(db_path) == []


def test_precheck_rejects_empty_qfq_database(tmp_path):
    module = load_module()
    db_path = tmp_path / 'etf-compass.db'
    make_qfq_db(db_path, coverage=0)

    assert module.qfq_precheck_problems(db_path) == ['qfq 最新交易日为空']

"""低筹码原始池入库契约测试（全 mock，零外部调用）。"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/sync_low_chip_raw_pool.py"


def load_module():
    spec = importlib.util.spec_from_file_location("sync_low_chip_raw_pool", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sample_payload(trade_date="2026-08-21", with_backfill=False):
    payload = {
        "schema_version": "a-low-profit-v3",
        "data_as_of": trade_date,
        "generated_at": f"{trade_date}T16:09:28+08:00",
        "source": "iWenCai SkillHub",
        "universe": "沪深A股，非ST，非退市，不含北交所",
        "threshold": 2,
        "counts": {"week": 2, "month": 2, "quarter": 1, "year": 1},
        "periods": {
            "week": [
                {"symbol": "600000.SH", "name": "甲股", "value": 1.5, "price": 10.0, "change_percent": -1.2},
                {"symbol": "000001.SZ", "name": "乙股", "value": 2.8, "price": 8.5, "change_percent": 0.4},
            ],
            "month": [
                {"symbol": "600000.SH", "name": "甲股", "value": 2.1, "price": 10.0, "change_percent": -1.2},
                {"symbol": "000001.SZ", "name": "乙股", "value": 2.9, "price": 8.5, "change_percent": 0.4},
            ],
            "quarter": [
                {"symbol": "600000.SH", "name": "甲股", "value": 2.6, "price": 10.0, "change_percent": -1.2},
            ],
            "year": [
                {"symbol": "600000.SH", "name": "甲股", "value": 2.9, "price": 10.0, "change_percent": -1.2},
            ],
        },
        "intersection_before_filters": ["600000.SH"],
        "intersection": ["600000.SH"],
        "filters": {"listing_cutoff": "2026-05-23", "listing_min_days": 90},
    }
    if with_backfill:
        payload["backfill"] = {
            "is_backfill": True,
            "generated_date": "2026-08-25",
            "reason": "quota exhausted",
        }
    return payload


def test_extract_rows_covers_every_period_and_keeps_raw_values():
    module = load_module()
    rows, meta = module.extract_rows(sample_payload())

    # 6 rows total: week 2 + month 2 + quarter 1 + year 1
    assert len(rows) == 6
    periods = sorted({r[2] for r in rows})
    assert periods == ["month", "quarter", "week", "year"]

    week_600000 = [r for r in rows if r[1] == "600000.SH" and r[2] == "week"][0]
    assert week_600000[0] == "2026-08-21"      # trade_date
    assert week_600000[3] == "甲股"             # name
    assert week_600000[4] == pytest.approx(1.5)  # profit_ratio
    assert week_600000[5] == pytest.approx(10.0)  # price
    assert week_600000[6] == pytest.approx(-1.2)  # change_percent

    assert meta is not None
    assert meta[0] == "2026-08-21"
    assert meta[1] == 2              # threshold
    assert meta[9] == 1            # intersection_count
    assert meta[11] == 0           # is_backfill


def test_extract_rows_records_backfill_declaration():
    module = load_module()
    _rows, meta = module.extract_rows(sample_payload(with_backfill=True))
    assert meta[11] == 1
    assert meta[12] == "quota exhausted"


def test_extract_rows_skips_snapshot_without_period_data():
    module = load_module()
    rows, meta = module.extract_rows({"data_as_of": "2026-08-21", "periods": {}})
    assert rows == []
    assert meta is None


def test_extract_rows_skips_snapshot_without_trade_date():
    module = load_module()
    rows, meta = module.extract_rows({"periods": {"week": [{"symbol": "600000.SH"}]}})
    assert rows == []
    assert meta is None


def test_import_is_idempotent_on_repeat_runs(tmp_path):
    """重复导入同一天不得产生重复行——原始池按 (date, code, period) 唯一。"""
    module = load_module()
    db = tmp_path / "t.db"
    conn = sqlite3.connect(db)
    module.ensure_schema(conn)

    rows, meta = module.extract_rows(sample_payload())
    module.write_rows(conn, rows, meta)
    module.write_rows(conn, rows, meta)   # second import
    conn.commit()

    n = conn.execute("SELECT count(*) FROM low_chip_raw_pool").fetchone()[0]
    assert n == 6, "repeat import must not duplicate rows"
    m = conn.execute("SELECT count(*) FROM low_chip_raw_pool_meta").fetchone()[0]
    assert m == 1
    conn.close()


def test_intersection_recomputable_from_db_without_iwencai(tmp_path):
    """核心价值验证：三周期交集可以完全从库内算出，不需要再查 iWenCai。"""
    module = load_module()
    db = tmp_path / "t.db"
    conn = sqlite3.connect(db)
    module.ensure_schema(conn)
    rows, meta = module.extract_rows(sample_payload())
    module.write_rows(conn, rows, meta)
    conn.commit()

    # 周/月/季三周期均命中 = 入池交集
    recomputed = conn.execute(
        """SELECT stock_code FROM low_chip_raw_pool
           WHERE trade_date = ? AND period IN ('week','month','quarter')
           GROUP BY stock_code
           HAVING count(DISTINCT period) = 3
           ORDER BY stock_code""",
        ("2026-08-21",),
    ).fetchall()
    assert [r[0] for r in recomputed] == ["600000.SH"]

    # 换个阈值重算也无需外部调用：≤2.0% 的周线命中
    stricter = conn.execute(
        """SELECT stock_code FROM low_chip_raw_pool
           WHERE trade_date = ? AND period = 'week' AND profit_ratio <= 2.0""",
        ("2026-08-21",),
    ).fetchall()
    assert [r[0] for r in stricter] == ["600000.SH"]
    conn.close()


def test_multiple_dates_coexist(tmp_path):
    module = load_module()
    db = tmp_path / "t.db"
    conn = sqlite3.connect(db)
    module.ensure_schema(conn)
    for date in ("2026-08-20", "2026-08-21"):
        rows, meta = module.extract_rows(sample_payload(trade_date=date))
        module.write_rows(conn, rows, meta)
    conn.commit()

    dates = conn.execute(
        "SELECT DISTINCT trade_date FROM low_chip_raw_pool ORDER BY trade_date"
    ).fetchall()
    assert [d[0] for d in dates] == ["2026-08-20", "2026-08-21"]
    conn.close()


def test_script_never_calls_iwencai():
    """守卫：入库脚本必须是纯本地 JSON→DB，不得引入任何 iWenCai 调用。"""
    source = SCRIPT.read_text(encoding="utf-8")
    assert "iwencai-market-query" not in source
    assert "subprocess" not in source

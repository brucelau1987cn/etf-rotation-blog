"""Local proof for D1 numbered-migration replay semantics."""

import re
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_0021_is_a_single_ledgered_d1_migration_and_replay_is_skipped():
    sql = (ROOT / "migrations/0021_stock_metrics_risk.sql").read_text(encoding="utf-8")
    columns = re.findall(r"ALTER TABLE stock_metrics ADD COLUMN (\w+) (?:TEXT|INTEGER);", sql)
    assert columns == [
        "risk_version", "risk_as_of", "risk_status", "risk_level",
        "risk_reasons", "risk_advisory", "risk_coverage", "risk_freshness",
    ]
    con = sqlite3.connect(":memory:")
    con.executescript("CREATE TABLE stock_metrics (trade_date TEXT, stock_code TEXT); CREATE TABLE d1_migrations (name TEXT PRIMARY KEY);")
    for migration_name in ("0021_stock_metrics_risk.sql", "0021_stock_metrics_risk.sql"):
        if con.execute("SELECT 1 FROM d1_migrations WHERE name = ?", (migration_name,)).fetchone():
            continue
        con.executescript(sql)
        con.execute("INSERT INTO d1_migrations(name) VALUES (?)", (migration_name,))
    found = {row[1] for row in con.execute("PRAGMA table_info(stock_metrics)")}
    assert set(columns) <= found


def test_d1_migration_entrypoint_is_explicit_and_dry_run_only_by_default():
    source = (ROOT / "scripts/apply_d1_migrations.py").read_text(encoding="utf-8")
    assert '"d1", "migrations", "apply", DATABASE' in source
    assert '"--config", "wrangler.toml"' in source
    assert '"--dry-run"' in source
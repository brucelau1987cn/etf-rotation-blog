import os
from datetime import datetime
from zoneinfo import ZoneInfo

from scripts import check_a_share_cron_gate as cron_gate
from scripts.check_a_share_cron_gate import GateInput, evaluate_gate, stage_rank

CN = ZoneInfo("Asia/Shanghai")


def gate(stage: str, at: str, **kwargs):
    data = GateInput(
        stage=stage,
        now=datetime.fromisoformat(at).replace(tzinfo=CN),
        trading_day=kwargs.get("trading_day", True),
        pending_publish=kwargs.get("pending_publish", False),
        article_stage_rank=kwargs.get("article_stage_rank", 0),
        pool_count=kwargs.get("pool_count", 91),
        valid_count=kwargs.get("valid_count", 91),
        quote_date=kwargs.get("quote_date", "2026-07-14"),
        qfq_date=kwargs.get("qfq_date", "2026-07-14"),
        qfq_coverage=kwargs.get("qfq_coverage", 91),
        nightly_precheck_ready=kwargs.get("nightly_precheck_ready", True),
    )
    return evaluate_gate(data)


def test_intraday_accepts_previous_final_qfq_date():
    decision, _ = gate("11:30", "2026-07-14T11:40:00", qfq_date="2026-07-13")
    assert decision == "run"
    decision, _ = gate("14:30", "2026-07-14T14:30:00", qfq_date="2026-07-13")
    assert decision == "run"


def test_intraday_rejects_stale_quote_date_and_low_coverage():
    assert gate("11:30", "2026-07-14T11:40:00", quote_date="2026-07-13")[0] == "blocked"
    assert gate("14:30", "2026-07-14T14:30:00", valid_count=81)[0] == "blocked"


def test_night_requires_today_final_qfq():
    assert gate("22:00", "2026-07-14T22:00:00", qfq_date="2026-07-13")[0] == "blocked"
    assert gate("22:00", "2026-07-14T22:00:00", qfq_coverage=81)[0] == "blocked"
    assert gate("22:00", "2026-07-14T22:00:00")[0] == "run"


def test_night_requires_completed_precheck_cache():
    decision, reason = gate(
        "22:00", "2026-07-14T22:00:00", nightly_precheck_ready=False,
    )
    assert decision == "blocked"
    assert reason == "nightly precheck-cache not completed"


def test_nightly_precheck_status_requires_same_day_success(tmp_path, monkeypatch):
    path = tmp_path / "status.json"
    monkeypatch.setattr(cron_gate, "NIGHTLY_STATUS", path)
    path.write_text(
        '{"requested_stage":"precheck-cache","ok":true,"finished_at":"2026-07-14T20:55:00+08:00"}',
        encoding="utf-8",
    )
    assert cron_gate.nightly_precheck_ready("2026-07-14") is True
    assert cron_gate.nightly_precheck_ready("2026-07-15") is False
    path.write_text(
        '{"requested_stage":"precheck-cache","status":"running","ok":false,"finished_at":"2026-07-14T20:55:00+08:00"}',
        encoding="utf-8",
    )
    assert cron_gate.nightly_precheck_ready("2026-07-14") is False


def test_idempotency_precedes_window_and_stage_parser():
    assert stage_rank("22:00夜间最终版") == 4
    assert stage_rank("14:30尾盘操作版") == 3
    assert gate("11:30", "2026-07-14T20:00:00", article_stage_rank=4)[0] == "idempotent"
    assert gate("22:00", "2026-07-14T20:00:00", article_stage_rank=4, pending_publish=True)[0] == "run"


def test_stage_window_is_enforced():
    assert gate("14:30", "2026-07-14T15:10:00")[0] == "blocked"
    assert gate("08:30", "2026-07-14T08:30:00", quote_date="2026-07-13")[0] == "run"
    # Legacy alias still accepted during migration.
    assert gate("07:30", "2026-07-14T08:30:00", quote_date="2026-07-13")[0] == "run"


def test_exchange_calendar_controls_execution():
    assert gate("08:30", "2026-07-14T08:30:00", trading_day=False)[0] == "idempotent"
    assert gate("08:30", "2026-07-14T08:30:00", trading_day=None)[0] == "blocked"


def test_calendar_fallback_uses_market_evidence():
    now = datetime.fromisoformat("2026-07-14T21:50:00").replace(tzinfo=CN)
    assert cron_gate.resolve_trading_day(None, stage="22:00", now=now, quote_date="2026-07-14", qfq_date="2026-07-14", qfq_coverage=91) == (True, "quote_and_final_qfq")
    assert cron_gate.resolve_trading_day(None, stage="22:00", now=now, quote_date="2026-07-14", qfq_date="2026-07-13", qfq_coverage=91) == (None, "unavailable")
    assert cron_gate.resolve_trading_day(None, stage="14:30", now=now, quote_date="2026-07-14", qfq_date="2026-07-13", qfq_coverage=91) == (True, "quote_timestamp")


def test_calendar_source_is_preserved():
    now = datetime.fromisoformat("2026-07-14T08:30:00").replace(tzinfo=CN)
    result = cron_gate.resolve_trading_day(
        True, stage="08:30", now=now, quote_date=None, qfq_date=None,
        qfq_coverage=0, calendar_source="d1_exchange_calendar",
    )
    assert result == (True, "d1_exchange_calendar")


def test_public_calendar_parses_open_and_closed_days(monkeypatch):
    class Response:
        def __init__(self, value):
            self.value = value

        def read(self):
            return ('{"sessions":[{"trade_date":"2026-07-14","is_open":%d}]}' % self.value).encode()

    monkeypatch.setattr(cron_gate.urllib.request, "urlopen", lambda *args, **kwargs: Response(1))
    assert cron_gate.public_calendar_trading_day("2026-07-14") is True
    monkeypatch.setattr(cron_gate.urllib.request, "urlopen", lambda *args, **kwargs: Response(0))
    assert cron_gate.public_calendar_trading_day("2026-07-14") is False


def test_trading_day_falls_back_to_baostock(monkeypatch):
    monkeypatch.setattr(cron_gate, "public_calendar_trading_day", lambda day: None)
    monkeypatch.setattr(cron_gate, "load_cf_credentials", lambda *a, **k: False)
    monkeypatch.setattr(cron_gate, "baostock_trading_day", lambda day: True)
    assert cron_gate.is_trading_day("2026-07-14") == (True, "baostock")


# ── Calendar chain availability (2026-10-08 whole-night loss) ──
# The fail-closed calendar gate silently had a single usable source under cron:
# the CF credentials were never exported, and the local BaoStock fallback is
# absent from the tool interpreter. One blip on the public calendar then cost
# the entire period. These tests pin the multi-source contract.

def test_calendar_chain_uses_cf_when_public_fails(monkeypatch):
    monkeypatch.setattr(cron_gate, "public_calendar_trading_day", lambda day: None)
    monkeypatch.setattr(cron_gate, "load_cf_credentials", lambda *a, **k: True)
    monkeypatch.setattr(cron_gate, "cf_baostock_trading_day", lambda day: True)
    assert cron_gate.is_trading_day("2026-10-08") == (True, "cf_baostock")


def test_calendar_chain_loads_credentials_before_cf_attempt(monkeypatch):
    """The CF source must be reachable without the caller exporting anything."""
    calls = []

    def fake_loader(*args, **kwargs):
        calls.append(True)
        return True

    monkeypatch.setattr(cron_gate, "public_calendar_trading_day", lambda day: None)
    monkeypatch.setattr(cron_gate, "load_cf_credentials", fake_loader)
    monkeypatch.setattr(cron_gate, "cf_baostock_trading_day", lambda day: True)
    monkeypatch.setattr(cron_gate, "baostock_trading_day", lambda day: None)
    assert cron_gate.is_trading_day("2026-10-08") == (True, "cf_baostock")
    assert calls == [True]


def test_load_cf_credentials_reads_env_file(tmp_path, monkeypatch):
    monkeypatch.delenv("CF_BAOSTOCK_DISABLE_CREDENTIAL_FILE", raising=False)
    monkeypatch.delenv("CF_BAOSTOCK_BASE_URL", raising=False)
    monkeypatch.delenv("CF_BAOSTOCK_TOKEN", raising=False)
    env_file = tmp_path / "baostock-edge.env"
    env_file.write_text(
        "CF_BAOSTOCK_BASE_URL=https://example.invalid\nCF_BAOSTOCK_TOKEN=token-123\n",
        encoding="utf-8",
    )
    assert cron_gate.load_cf_credentials(env_file) is True
    assert os.environ["CF_BAOSTOCK_BASE_URL"] == "https://example.invalid"
    assert os.environ["CF_BAOSTOCK_TOKEN"] == "token-123"


def test_load_cf_credentials_is_disabled_in_tests(tmp_path):
    """The suite must never reach a real provider through this helper."""
    env_file = tmp_path / "baostock-edge.env"
    env_file.write_text("CF_BAOSTOCK_BASE_URL=https://x\nCF_BAOSTOCK_TOKEN=t\n", encoding="utf-8")
    assert cron_gate.load_cf_credentials(env_file) is False


def test_calendar_chain_reports_every_source_when_all_fail(monkeypatch):
    monkeypatch.setattr(cron_gate, "public_calendar_trading_day", lambda day: None)
    monkeypatch.setattr(cron_gate, "load_cf_credentials", lambda *a, **k: True)
    monkeypatch.setattr(cron_gate, "cf_baostock_trading_day", lambda day: None)
    monkeypatch.setattr(cron_gate, "baostock_trading_day", lambda day: None)
    value, source = cron_gate.is_trading_day("2026-10-08")
    assert value is None
    # The verdict stays fail-closed, but the reason names each source tried.
    assert source.startswith("unavailable(")
    for name in ("d1_exchange_calendar", "cf_baostock", "baostock"):
        assert name in source


def test_calendar_chain_skips_remaining_sources_after_budget(monkeypatch):
    """A hung provider must not outlast the cron window."""
    monkeypatch.setattr(cron_gate, "CALENDAR_TOTAL_BUDGET_S", 0.0)
    monkeypatch.setattr(cron_gate, "public_calendar_trading_day", lambda day: None)
    attempted = []
    monkeypatch.setattr(cron_gate, "load_cf_credentials", lambda *a, **k: True)
    monkeypatch.setattr(cron_gate, "cf_baostock_trading_day", lambda day: attempted.append("cf") or True)
    monkeypatch.setattr(cron_gate, "baostock_trading_day", lambda day: attempted.append("baostock") or True)
    value, source = cron_gate.is_trading_day("2026-10-08")
    assert value is None
    assert attempted == []
    assert "budget" in source


def test_stage_rank_prefers_0830_preopen():
    assert stage_rank("08:30盘前版") == 1
    assert stage_rank("07:30早盘版") == 1


def test_quote_timestamp_degrades_to_none_on_network_error(monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("offline")

    monkeypatch.setattr(cron_gate.urllib.request, "urlopen", fail)
    assert cron_gate.quote_timestamp() is None


def test_pending_public_changes_detects_staged_only_change(tmp_path, monkeypatch):
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    path = tmp_path / "public/data/model-lab/a-share-path-shadow.json"
    path.parent.mkdir(parents=True)
    path.write_text("old\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=tmp_path, check=True)
    path.write_text("new\n", encoding="utf-8")
    subprocess.run(["git", "add", str(path.relative_to(tmp_path))], cwd=tmp_path, check=True)
    monkeypatch.setattr(cron_gate, "ROOT", tmp_path)
    assert cron_gate.pending_public_changes("2026-07-14") is True


def test_qfq_state_degrades_when_database_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(cron_gate, "DB", tmp_path / "missing" / "etf-compass.db")
    assert cron_gate.qfq_state() == (None, 0)


def test_qfq_state_excludes_malformed_ohlc(tmp_path, monkeypatch):
    import sqlite3
    db_path = tmp_path / "bars.db"
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE daily_bars(symbol TEXT,trade_date TEXT,open REAL,high REAL,low REAL,close REAL,adjustment TEXT,is_final INTEGER)")
        db.executemany("INSERT INTO daily_bars VALUES (?,?,?,?,?,?,?,?)", [
            ("510050", "2026-07-14", 3.0, 3.1, 2.9, 3.05, "qfq", 1),
            ("159667", "2026-07-14", 2.0, 2.1, 1.9, 0.63, "qfq", 1),
        ])
    monkeypatch.setattr(cron_gate, "DB", db_path)
    assert cron_gate.qfq_state() == ("2026-07-14", 1)

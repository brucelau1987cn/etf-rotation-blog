from datetime import datetime
from zoneinfo import ZoneInfo

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


def test_idempotency_precedes_window_and_stage_parser():
    assert stage_rank("22:00夜间最终版") == 4
    assert stage_rank("14:30尾盘操作版") == 3
    assert gate("11:30", "2026-07-14T20:00:00", article_stage_rank=4)[0] == "idempotent"
    assert gate("22:00", "2026-07-14T20:00:00", article_stage_rank=4, pending_publish=True)[0] == "run"


def test_stage_window_is_enforced():
    assert gate("14:30", "2026-07-14T15:10:00")[0] == "blocked"
    assert gate("07:30", "2026-07-14T08:30:00", quote_date="2026-07-13")[0] == "run"


def test_exchange_calendar_controls_execution():
    assert gate("07:30", "2026-07-14T08:30:00", trading_day=False)[0] == "idempotent"
    assert gate("07:30", "2026-07-14T08:30:00", trading_day=None)[0] == "blocked"

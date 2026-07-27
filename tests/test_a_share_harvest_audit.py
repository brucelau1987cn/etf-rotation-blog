import copy

from scripts import audit_a_share_harvest as audit
from scripts.validate_dashboard_batches import validate_harvest_selection


def pool_rows():
    rows = []
    for index in range(91):
        rows.append({
            "code": f"X{index:05d}", "name": f"ETF{index}", "date": "2026-07-28",
            "trade_state": "可持有", "strength_level": "A", "risk_level": "低",
            "signal_score": 60.0, "target_gap": 20.0, "ret20": 1.0,
        })
    rows[0].update(code="KEEP", trade_state="退出", strength_level="D", signal_score=40.0)
    rows[1].update(code="WATCH", trade_state="观察", target_gap=5.0)
    return rows


def test_harvest_requalification_keeps_current_qualified_and_removes_stale():
    recommendations = {
        "date": "2026-07-28",
        "harvest": [
            {"code": "KEEP", "name": "Keep", "status": "止盈观察"},
            {"code": "WATCH", "name": "Watch", "status": "止盈观察"},
            {"code": "STALE", "name": "Stale", "status": "止盈观察"},
        ],
    }
    pool = {
        "evaluation_date": "2026-07-28",
        "summary": {"universe_count": 91},
        "all_rows": pool_rows(),
    }

    result = audit.apply_harvest_audit(copy.deepcopy(recommendations), pool)

    assert [item["code"] for item in result["harvest"]] == ["KEEP", "WATCH"]
    assert result["harvest_selection"]["removed_codes"] == ["STALE"]
    assert result["harvest_selection"]["selection_mode"] == "current_tracked_harvest_requalification"
    for item in result["harvest"]:
        assert item["selected_from_pool_date"] == "2026-07-28"
        assert item["last_qualified_date"] == "2026-07-28"
        assert item["qualified_reason"]
        assert len(item["source_fingerprint"]) == 64


def test_harvest_audit_fails_closed_on_partial_pool():
    pool = {"evaluation_date": "2026-07-28", "summary": {"universe_count": 90}, "all_rows": pool_rows()[:-1]}
    try:
        audit.apply_harvest_audit({"date": "2026-07-28", "harvest": []}, pool)
    except RuntimeError as exc:
        assert "91" in str(exc)
    else:
        raise AssertionError("expected partial-pool rejection")


def test_harvest_validator_recomputes_current_pool_qualification_metadata():
    pool = {"evaluation_date": "2026-07-28", "summary": {"universe_count": 91}, "all_rows": pool_rows()}
    garden = audit.apply_harvest_audit(
        {"date": "2026-07-28", "harvest": [{"code": "KEEP", "name": "Keep", "status": "止盈观察"}]},
        pool,
    )
    errors = []
    validate_harvest_selection(errors, garden, pool)
    assert errors == []

    garden["harvest"][0]["source_fingerprint"] = "0" * 64
    validate_harvest_selection(errors, garden, pool)
    assert any("fingerprint" in error for error in errors)

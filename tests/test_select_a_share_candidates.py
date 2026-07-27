#!/usr/bin/env python3
"""Regression tests for daily A-share candidate selection."""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "select_a_share_candidates.py"


def load_module():
    spec = importlib.util.spec_from_file_location("select_a_share_candidates", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def row(
    code: str,
    *,
    name: str | None = None,
    theme: str = "科技",
    score: float = 65,
    rank: int = 5,
    strength: str = "B",
    risk: str = "低",
    support_gap: float = 2,
    momentum: bool = True,
    asset_layer: str = "rotation",
):
    price = 1.0
    support = round(price / (1 + support_gap / 100), 4)
    return {
        "code": code,
        "name": name or code,
        "theme": theme,
        "asset_layer": asset_layer,
        "signal_score": score,
        "momentum_rank": rank,
        "strength_level": strength,
        "risk_level": risk,
        "support_gap": support_gap,
        "checks": {"momentum": momentum, "price_above_ma": True, "ma_rising": True},
        "price": price,
        "support": support,
        "target": 1.05,
        "stop": 0.94,
        "date": "2026-07-28",
        "quote_source": "fixture",
        "level_basis": "fixture",
        "level_model_version": "fixture-v1",
    }


def test_daily_reselection_removes_unqualified_incumbents():
    mod = load_module()
    pool = {
        "evaluation_date": "2026-07-28",
        "all_rows": [
            row("NEW001", score=74, rank=1, strength="B", theme="中药"),
            row("OLD001", score=40, rank=40, strength="D", momentum=False, theme="银行"),
        ],
    }
    previous = [{"code": "OLD001", "name": "旧银行", "status": "候场", "candidate_since": "2026-07-15"}]

    selected, audit = mod.select_candidates(pool, previous, limit=3)

    assert [item["code"] for item in selected] == ["NEW001"]
    assert selected[0]["selected_from_pool_date"] == "2026-07-28"
    assert selected[0]["last_qualified_date"] == "2026-07-28"
    assert selected[0]["candidate_since"] == "2026-07-28"
    assert audit["evaluated_count"] == 2


def test_continuity_bonus_keeps_age_but_refreshes_action_text():
    mod = load_module()
    pool = {
        "evaluation_date": "2026-07-28",
        "all_rows": [
            row("KEEP01", score=65, rank=4, strength="B", momentum=True),
            row("FRESH1", score=65, rank=4, strength="B", momentum=True),
        ],
    }
    previous = [{
        "code": "KEEP01",
        "status": "候场",
        "candidate_since": "2026-07-25",
        "last_qualified_date": "2026-07-27",
        "action": "保留已有盘中说明",
        "trigger": "保留已有触发说明",
    }]

    selected, _ = mod.select_candidates(pool, previous, limit=1)

    assert selected[0]["code"] == "KEEP01"
    assert selected[0]["candidate_since"] == "2026-07-25"
    assert selected[0]["action"] != "保留已有盘中说明"
    assert "等待回踩" in selected[0]["action"]


def test_harvest_codes_are_excluded_before_candidate_ranking():
    mod = load_module()
    pool = {
        "evaluation_date": "2026-07-28",
        "all_rows": [
            row("SELL01", score=90, rank=1, theme="通信"),
            row("BUY001", score=70, rank=2, theme="中药"),
        ],
    }

    selected, _ = mod.select_candidates(pool, [], limit=3, excluded_codes={"SELL01"})

    assert [item["code"] for item in selected] == ["BUY001"]


def test_selection_limits_defensive_candidates_to_one():
    mod = load_module()
    pool = {
        "evaluation_date": "2026-07-28",
        "all_rows": [
            row("BANK01", score=80, rank=1, theme="银行"),
            row("DIV001", score=79, rank=2, theme="红利"),
            row("TECH01", score=70, rank=3, theme="通信"),
        ],
    }

    selected, _ = mod.select_candidates(pool, [], limit=3)

    assert [item["code"] for item in selected] == ["BANK01", "TECH01"]


def test_no_qualified_rows_returns_empty_candidate_list():
    mod = load_module()
    pool = {
        "evaluation_date": "2026-07-28",
        "all_rows": [row("WEAK01", score=35, rank=80, strength="D", risk="高", momentum=False)],
    }

    selected, audit = mod.select_candidates(pool, [{"code": "WEAK01", "status": "候场"}], limit=3)

    assert selected == []
    assert audit["selected_count"] == 0


def test_apply_selection_replaces_entire_plant_array():
    mod = load_module()
    recommendations = {
        "date": "2026-07-28",
        "plant": [{"code": "STALE1", "status": "候场"}],
        "harvest": [],
    }
    rows = [row("LIVE01", score=72, rank=1, theme="中药")]
    rows.extend(row(f"WEAK{i:02d}", score=30, rank=90, strength="D", momentum=False) for i in range(90))
    pool = {
        "evaluation_date": "2026-07-28",
        "summary": {"universe_count": 91},
        "all_rows": rows,
    }

    result = mod.apply_selection(recommendations, pool, limit=3)

    assert [item["code"] for item in result["plant"]] == ["LIVE01"]
    assert result["candidate_selection"]["selected_codes"] == ["LIVE01"]
    assert result["candidate_selection"]["rule_version"] == mod.RULE_VERSION


def test_apply_selection_fails_closed_on_partial_formal_pool():
    mod = load_module()
    recommendations = {"date": "2026-07-28", "plant": [], "harvest": []}
    pool = {
        "evaluation_date": "2026-07-28",
        "summary": {"universe_count": 90},
        "all_rows": [row("ONLY01")],
    }

    try:
        mod.apply_selection(recommendations, pool)
    except ValueError as exc:
        assert "91" in str(exc)
    else:
        raise AssertionError("partial pool must fail closed")

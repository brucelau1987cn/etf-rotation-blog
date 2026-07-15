#!/usr/bin/env python3
"""Validate cross-file dashboard batch consistency before static publication."""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "public/data"
A_STAGES = {"08:30盘前版", "11:30上午收盘修正版", "14:30尾盘操作版", "22:00夜间最终版"}
A_STATUSES = {"候场", "伏击", "止盈观察", "兑现", "破位撤退"}
A_RISKS = {"低", "中", "高"}
A_STRENGTHS = {"A", "B", "C", "D"}
A_TRADE_STATES = {"可持有", "回踩候选", "观察", "退出"}
US_STAGES = {"美股盘前快照", "美股盘中快照", "美股收盘版"}
US_SESSIONS = {"preopen", "open", "closed"}
US_SIGNALS = {"候场", "伏击触发", "止盈观察", "兑现触发", "破位撤退"}


@dataclass
class CheckResult:
    status: str
    errors: list[str]
    warnings: list[str]
    batches: dict[str, Any]


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"missing file: {display_path(path)}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {display_path(path)}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"root must be object: {display_path(path)}")
    return payload


def date_prefix(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) < 10:
        return None
    raw = value[:10]
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date().isoformat()
    except ValueError:
        return None


def require_date(errors: list[str], label: str, value: Any) -> str | None:
    parsed = date_prefix(value)
    if parsed is None:
        errors.append(f"{label} missing or invalid: {value!r}")
    return parsed


def number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed and abs(parsed) != float("inf") else None


def require_fields(errors: list[str], label: str, payload: dict[str, Any], fields: tuple[str, ...]) -> None:
    missing = [field for field in fields if payload.get(field) in (None, "")]
    if missing:
        errors.append(f"{label} missing fields: {', '.join(missing)}")


def validate_levels(
    findings: list[str], label: str, item: dict[str, Any], *, allow_invalid: bool = False,
    require_target_above_support: bool = True,
) -> None:
    values = {key: number(item.get(key)) for key in ("price", "support", "target", "stop")}
    if any(value is None for value in values.values()):
        findings.append(f"{label} has non-numeric price levels")
        return
    if allow_invalid and item.get("level_status") == "invalid" and item.get("level_invalid_reason"):
        return
    numeric = {key: value for key, value in values.items() if value is not None}
    if any(value <= 0 for value in numeric.values()):
        findings.append(f"{label} has non-positive price levels")
        return
    stop, support, target = numeric["stop"], numeric["support"], numeric["target"]
    if not stop < support:
        findings.append(f"{label} requires stop < support")
    if require_target_above_support and not target > support:
        findings.append(f"{label} requires target > support")


def validate_runtime_schema(
    errors: list[str], warnings: list[str], garden: dict[str, Any], a_pool: dict[str, Any], a_mid: dict[str, Any],
    shadow: dict[str, Any], us: dict[str, Any], us_pool: dict[str, Any], us_macro: dict[str, Any],
) -> None:
    require_fields(errors, "garden-recommendations", garden, ("date", "updated_at", "stage", "market_state", "position", "summary"))
    if garden.get("stage") not in A_STAGES:
        errors.append(f"garden-recommendations invalid stage: {garden.get('stage')!r}")
    seen_codes: set[str] = set()
    for section in ("plant", "harvest"):
        rows = garden.get(section)
        if not isinstance(rows, list):
            errors.append(f"garden-recommendations {section} must be an array")
            continue
        for index, item in enumerate(rows):
            label = f"garden-recommendations {section}[{index}]"
            if not isinstance(item, dict):
                errors.append(f"{label} must be an object")
                continue
            require_fields(errors, label, item, ("code", "name", "status", "action"))
            code = str(item.get("code") or "")
            if code in seen_codes:
                errors.append(f"garden-recommendations duplicate action code: {code}")
            seen_codes.add(code)
            if item.get("status") not in A_STATUSES:
                errors.append(f"{label} invalid status: {item.get('status')!r}")
            if item.get("risk_level") not in (None, *A_RISKS):
                errors.append(f"{label} invalid risk_level: {item.get('risk_level')!r}")
            require_target = item.get("status") not in {"破位撤退"}
            validate_levels(errors, label, item, allow_invalid=True, require_target_above_support=require_target)
            if item.get("status") == "伏击" and item.get("eligibility") == "blocked":
                errors.append(f"{label} blocked item cannot be formal 伏击")

    summary = a_pool.get("summary") or {}
    rows = a_pool.get("all_rows")
    if not isinstance(rows, list) or not rows:
        errors.append("etf-garden-pool all_rows must be a non-empty array")
    else:
        row_count = len(rows)
        if int(summary.get("universe_count") or 0) != row_count:
            errors.append(f"etf-garden-pool universe_count {summary.get('universe_count')} differs from rows {row_count}")
        codes = [str(item.get("code") or "") for item in rows if isinstance(item, dict)]
        if len(codes) != len(set(codes)):
            errors.append("etf-garden-pool contains duplicate codes")
        for index, item in enumerate(rows):
            label = f"etf-garden-pool all_rows[{index}]"
            require_fields(errors, label, item, ("code", "name", "date", "trade_state", "strength_level", "risk_level"))
            if item.get("trade_state") not in A_TRADE_STATES:
                errors.append(f"{label} invalid trade_state: {item.get('trade_state')!r}")
            if item.get("strength_level") not in A_STRENGTHS:
                errors.append(f"{label} invalid strength_level: {item.get('strength_level')!r}")
            if item.get("risk_level") not in A_RISKS:
                errors.append(f"{label} invalid risk_level: {item.get('risk_level')!r}")
            # Pool-wide levels are research diagnostics. Rows in 观察/退出 can
            # carry inverted or legacy bands; surface them without blocking the
            # build. Executable recommendation rows above remain strict.
            validate_levels(
                errors if item.get("trade_state") in {"可持有", "回踩候选"} else warnings,
                label, item, allow_invalid=True,
            )

    require_fields(errors, "a-share-mid-macro", a_mid, ("version", "generated_at", "market", "factors", "constraint"))
    if a_mid.get("market") != "CN" or not isinstance(a_mid.get("factors"), list) or len(a_mid.get("factors")) != 3:
        errors.append("a-share-mid-macro requires market=CN and exactly 3 factors")
    if shadow.get("mode") != "shadow_research_only" or shadow.get("production_weights_changed") is not False:
        errors.append("a-share-shadow must remain shadow_research_only with unchanged production weights")

    require_fields(errors, "us-etf-garden", us, ("date", "updated_at", "stage", "session_state", "market_regime", "flower_signals"))
    if us.get("stage") not in US_STAGES:
        errors.append(f"us-etf-garden invalid stage: {us.get('stage')!r}")
    if us.get("session_state") not in US_SESSIONS:
        errors.append(f"us-etf-garden invalid session_state: {us.get('session_state')!r}")
    for section, items in (us.get("flower_signals") or {}).items():
        if section not in {"ready_plant", "plant", "ready_harvest", "harvest", "exit"} or not isinstance(items, list):
            errors.append(f"us-etf-garden invalid signal section: {section!r}")
            continue
        for index, item in enumerate(items):
            label = f"us-etf-garden {section}[{index}]"
            require_fields(errors, label, item, ("symbol", "name", "signal", "trade_state", "risk_level", "trade_date"))
            if item.get("signal") not in US_SIGNALS:
                errors.append(f"{label} invalid signal: {item.get('signal')!r}")
            if item.get("trade_state") not in A_TRADE_STATES:
                errors.append(f"{label} invalid trade_state: {item.get('trade_state')!r}")
            if item.get("risk_level") not in A_RISKS:
                errors.append(f"{label} invalid risk_level: {item.get('risk_level')!r}")
            validate_levels(
                errors, label, item,
                require_target_above_support=item.get("signal") != "破位撤退",
            )

    us_rows = us_pool.get("rows")
    if not isinstance(us_rows, list) or not us_rows:
        errors.append("us-etf-pool rows must be a non-empty array")
    else:
        symbols = [str(item.get("symbol") or "") for item in us_rows if isinstance(item, dict)]
        if len(symbols) != len(set(symbols)):
            errors.append("us-etf-pool contains duplicate symbols")
    require_fields(errors, "us-macro-dashboard", us_macro, ("version", "generated_at", "risk", "market", "data_quality"))


def validate(data_dir: Path = DATA) -> CheckResult:
    errors: list[str] = []
    warnings: list[str] = []
    batches: dict[str, Any] = {}
    try:
        garden = load_json(data_dir / "garden-recommendations.json")
        a_pool = load_json(data_dir / "etf-garden-pool.json")
        a_mid = load_json(data_dir / "a-share-mid-macro.json")
        shadow = load_json(data_dir / "model-lab/a-share-shadow.json")
        us = load_json(data_dir / "us-etf-garden.json")
        us_pool = load_json(data_dir / "us-etf-pool.json")
        us_macro = load_json(data_dir / "us-macro-dashboard.json")
    except ValueError as exc:
        return CheckResult("error", [str(exc)], warnings, batches)

    validate_runtime_schema(errors, warnings, garden, a_pool, a_mid, shadow, us, us_pool, us_macro)

    a_date = require_date(errors, "A recommendations date", garden.get("date"))
    a_applies = require_date(errors, "A recommendations applies_to", garden.get("applies_to"))
    a_level = require_date(errors, "A recommendation level_data_as_of", garden.get("level_data_as_of"))
    pool_eval = require_date(errors, "A pool evaluation_date", a_pool.get("evaluation_date"))
    pool_latest = require_date(errors, "A pool latest_trade_date", a_pool.get("latest_trade_date"))
    mid_generated = require_date(errors, "A mid-macro generated_at", a_mid.get("generated_at"))
    shadow_latest = require_date(errors, "A shadow latest_trade_date", shadow.get("latest_trade_date"))
    action_dates = sorted({
        parsed
        for section in ("plant", "harvest", "watch")
        for item in garden.get(section, []) if isinstance(item, dict)
        if (parsed := date_prefix(item.get("price_date")))
    })
    # Plan/intraday files share the target session date. Historical qfq levels
    # and the shadow model may legitimately remain on the previous final close.
    plan_dates = [x for x in (a_date, a_applies, pool_eval, mid_generated) if x]
    if len(set(plan_dates)) > 1:
        errors.append(
            "A-share plan batch mismatch: "
            f"recommendations={a_date}, applies_to={a_applies}, pool_evaluation={pool_eval}, mid_macro={mid_generated}"
        )
    # Intraday plans may combine the previous final-close baseline with a
    # same-day shadow model produced after the current session closes. The
    # executable levels and pool remain tied to the previous baseline.
    baseline_dates = [x for x in (a_level, pool_latest) if x]
    if len(set(baseline_dates)) > 1:
        errors.append(
            "A-share baseline batch mismatch: "
            f"levels={a_level}, pool_latest={pool_latest}"
        )
    shadow_allowed_dates = {date for date in (a_level, pool_latest, a_date) if date}
    if shadow_latest and shadow_latest not in shadow_allowed_dates:
        errors.append(
            "A-share shadow date outside plan/baseline batches: "
            f"allowed={sorted(shadow_allowed_dates)}, shadow={shadow_latest}"
        )
    stage = str(garden.get("stage") or "")
    allowed_action_dates = {date for date in (a_date, pool_latest) if date}
    unexpected_action_dates = [date for date in action_dates if date not in allowed_action_dates]
    if unexpected_action_dates:
        errors.append(
            f"A action price dates outside plan/baseline batches: allowed={sorted(allowed_action_dates)}, actions={action_dates}"
        )
    if stage.startswith("22:00"):
        final_dates = [x for x in (a_date, a_level, pool_latest, shadow_latest) if x]
        if len(set(final_dates)) > 1:
            errors.append(
                "A 22:00 final stage requires one final date: "
                f"recommendations={a_date}, levels={a_level}, pool_latest={pool_latest}, shadow={shadow_latest}"
            )
        if action_dates and action_dates != [a_date]:
            errors.append(f"A 22:00 final action dates must equal {a_date}: {action_dates}")

    us_date = require_date(errors, "US garden date", us.get("date"))
    us_model = require_date(errors, "US pool model_date", us_pool.get("model_date"))
    us_quote = require_date(errors, "US pool quote_trade_date", us_pool.get("quote_trade_date"))
    macro_generated = require_date(errors, "US macro generated_at", us_macro.get("generated_at"))
    market_dates = sorted({
        parsed for item in (us_macro.get("market") or {}).values() if isinstance(item, dict)
        if (parsed := date_prefix(item.get("date")))
    })
    macro_primary = max(market_dates) if market_dates else None
    if macro_primary is None:
        errors.append("US macro market dates are missing")
    us_expected = [x for x in (us_date, us_model, us_quote, macro_generated, macro_primary) if x]
    if len(set(us_expected)) > 1:
        errors.append(
            "US batch mismatch: "
            f"garden={us_date}, model={us_model}, quote={us_quote}, "
            f"macro_generated={macro_generated}, macro_primary={macro_primary}"
        )
    if us.get("session_state") != us_pool.get("session_state"):
        errors.append(
            f"US session_state mismatch: garden={us.get('session_state')!r}, pool={us_pool.get('session_state')!r}"
        )

    batches["a_share"] = {
        "date": a_date,
        "stage": garden.get("stage"),
        "action_dates": action_dates,
        "pool_latest": pool_latest,
        "mid_macro_date": mid_generated,
        "shadow_date": shadow_latest,
    }
    batches["us"] = {
        "date": us_date,
        "stage": us.get("stage"),
        "session_state": us.get("session_state"),
        "pool_date": us_quote,
        "macro_primary_date": macro_primary,
    }
    return CheckResult("ok" if not errors else "error", errors, warnings, batches)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=DATA)
    args = parser.parse_args()
    result = validate(args.data_dir)
    print(json.dumps(result.__dict__, ensure_ascii=False, indent=2))
    return 0 if result.status == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())

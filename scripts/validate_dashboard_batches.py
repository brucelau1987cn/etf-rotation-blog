#!/usr/bin/env python3
"""Validate cross-file dashboard batch consistency before static publication."""
from __future__ import annotations

import argparse
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from a_share_execution_contract import ALL_TRADE_STATES, EXECUTION_ELIGIBLE_STATES
    from audit_a_share_harvest import RULE_VERSION as HARVEST_RULE_VERSION, qualified_reason as harvest_qualified_reason, row_fingerprint as harvest_row_fingerprint
    from generate_research_audit import DEFAULT_TURNOVER, PROVENANCE, build_payload, combined_fingerprint
    from generate_us_etf_garden import UNIVERSE as US_UNIVERSE, flower_signals as rebuild_us_flower_signals
    from paper_trade_runner import project_public_pending
    from path_shadow_public_schema import validate_public_payload
except ModuleNotFoundError:  # imported as scripts.validate_dashboard_batches in tests
    from scripts.a_share_execution_contract import ALL_TRADE_STATES, EXECUTION_ELIGIBLE_STATES
    from scripts.audit_a_share_harvest import RULE_VERSION as HARVEST_RULE_VERSION, qualified_reason as harvest_qualified_reason, row_fingerprint as harvest_row_fingerprint
    from scripts.generate_research_audit import DEFAULT_TURNOVER, PROVENANCE, build_payload, combined_fingerprint
    from scripts.generate_us_etf_garden import UNIVERSE as US_UNIVERSE, flower_signals as rebuild_us_flower_signals
    from scripts.paper_trade_runner import project_public_pending
    from scripts.path_shadow_public_schema import validate_public_payload

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "public/data"
A_STAGES = {"08:30盘前版", "11:30上午收盘修正版", "14:30尾盘操作版", "22:00夜间最终版"}
A_STATUSES = {"候场", "伏击", "止盈观察", "兑现", "破位撤退"}
A_RISKS = {"低", "中", "高"}
A_STRENGTHS = {"A", "B", "C", "D"}
A_TRADE_STATES = set(ALL_TRADE_STATES)
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


def timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().replace(" UTC+08:00", "+08:00").replace(" CST", "+08:00")
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


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


def non_finite_paths(value: Any, path: str = "root") -> list[str]:
    if isinstance(value, float) and not math.isfinite(value):
        return [path]
    if isinstance(value, dict):
        return [item for key, child in value.items() for item in non_finite_paths(child, f"{path}.{key}")]
    if isinstance(value, list):
        return [item for index, child in enumerate(value) for item in non_finite_paths(child, f"{path}[{index}]")]
    return []


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


def validate_candidate_selection(
    errors: list[str], warnings: list[str], garden: dict[str, Any], a_pool: dict[str, Any],
) -> None:
    """Require auditable current-pool candidate identity for new batches."""
    garden_date = date_prefix(garden.get("date"))
    if garden_date is None or garden_date < "2026-07-28":
        return
    pool_date = date_prefix(a_pool.get("evaluation_date"))
    selection = garden.get("candidate_selection")
    if not isinstance(selection, dict):
        errors.append("garden-recommendations candidate_selection is required for batches on/after 2026-07-28")
        selection = {}
    require_fields(errors, "garden-recommendations candidate_selection", selection, ("evaluation_date", "rule_version", "selected_codes"))
    if date_prefix(selection.get("evaluation_date")) != pool_date:
        errors.append("garden-recommendations candidate_selection evaluation_date differs from current pool")

    raw_pool_rows = a_pool.get("all_rows")
    pool_rows: list[Any] = raw_pool_rows if isinstance(raw_pool_rows, list) else []
    pool_universe_count = (a_pool.get("summary") or {}).get("universe_count")
    if len(pool_rows) != 91 or pool_universe_count != 91:
        errors.append(
            f"etf-garden-pool candidate selection requires exactly 91 formal rows: "
            f"rows={len(pool_rows)}, summary={pool_universe_count!r}"
        )
    pool_map = {str(row.get("code")): row for row in pool_rows if isinstance(row, dict) and row.get("code")}
    raw_plants = garden.get("plant")
    plants: list[Any] = raw_plants if isinstance(raw_plants, list) else []
    selected_codes: list[str] = []
    defensive_count = 0
    for index, item in enumerate(plants):
        if not isinstance(item, dict):
            continue
        label = f"garden-recommendations plant[{index}] candidate audit"
        require_fields(errors, label, item, (
            "selected_from_pool_date", "last_qualified_date", "selection_score", "selection_rank",
            "qualified_reason", "selection_rule_version",
        ))
        code = str(item.get("code") or "")
        selected_codes.append(code)
        if code not in pool_map:
            errors.append(f"{label} code {code!r} is absent from current formal pool")
        if date_prefix(item.get("selected_from_pool_date")) != pool_date:
            errors.append(f"{label} selected_from_pool_date differs from current pool")
        if date_prefix(item.get("last_qualified_date")) != pool_date:
            errors.append(f"{label} last_qualified_date differs from current pool")
        if number(item.get("selection_score")) is None or not isinstance(item.get("selection_rank"), int):
            errors.append(f"{label} has invalid selection score/rank")
        if not isinstance(item.get("qualified_reason"), list) or not item.get("qualified_reason"):
            errors.append(f"{label} qualified_reason must be a non-empty array")
        row = pool_map.get(code) or {}
        defensive_text = " ".join(str(row.get(key) or "") for key in ("theme", "name", "category"))
        if any(token in defensive_text for token in ("银行", "红利", "低波", "现金流", "国债", "债券", "货币")):
            defensive_count += 1
    if defensive_count > 1:
        errors.append("garden-recommendations candidate selection exceeds defensive candidate limit 1")
    if selection.get("selected_codes") != selected_codes:
        errors.append("garden-recommendations candidate_selection selected_codes differs from plant order")
    if selection.get("unchanged_from_previous") is True:
        warnings.append("A-share candidate set unchanged from previous batch; current-day qualification metadata verified")


def validate_public_pending(
    errors: list[str], paper: dict[str, Any], garden: dict[str, Any], us: dict[str, Any],
) -> None:
    accounts = paper.get("accounts")
    if not isinstance(accounts, dict):
        errors.append("paper-trading accounts must be an object")
        return
    sources = {"A": garden, "US": us}
    paper_updated_at = timestamp(paper.get("updated_at"))
    if paper_updated_at is None:
        errors.append("paper-trading updated_at must be a timezone-aware timestamp")
    expected_projection = project_public_pending(paper, sources)
    for market, source in sources.items():
        account = accounts.get(market)
        if not isinstance(account, dict):
            errors.append(f"paper-trading {market} account is missing")
            continue
        positions = account.get("positions")
        held = set(positions) if isinstance(positions, dict) else set()
        expected: list[tuple[str, str]] = []
        if market == "A":
            for item in source.get("plant") or []:
                if not isinstance(item, dict) or item.get("level_status") == "invalid":
                    continue
                status = item.get("status")
                if status in {"伏击", "种花"} and item.get("eligibility") != "blocked":
                    public_status = "伏击"
                elif status in {"候场", "准备种花"}:
                    public_status = "候场"
                else:
                    continue
                symbol = str(item.get("code") or "")
                if symbol and symbol not in held:
                    expected.append((symbol, public_status))
        else:
            signals = source.get("flower_signals") or {}
            for section, accepted, public_status in (
                ("plant", {"伏击触发", "种花", "伏击"}, "伏击"),
                ("ready_plant", {"候场", "准备种花"}, "候场"),
            ):
                for item in signals.get(section) or []:
                    if not isinstance(item, dict) or item.get("signal") not in accepted:
                        continue
                    symbol = str(item.get("symbol") or "")
                    if symbol and symbol not in held:
                        expected.append((symbol, public_status))
        public_items = account.get("public_pending_signals")
        if not isinstance(public_items, list):
            errors.append(f"paper-trading {market} public_pending_signals must be an array")
            continue
        actual = [
            (str(item.get("symbol") or ""), str(item.get("status") or ""))
            for item in public_items if isinstance(item, dict)
        ]
        if actual != expected or len(actual) != len(public_items):
            errors.append(
                f"paper-trading {market} public pending identity mismatch: expected={expected}, actual={actual}"
            )
        source_date = source.get("date")
        source_updated_at = source.get("updated_at")
        if any(
            not isinstance(item, dict)
            or item.get("source_date") != source_date
            or item.get("source_updated_at") != source_updated_at
            for item in public_items
        ):
            errors.append(f"paper-trading {market} public pending source metadata mismatch")
        if paper_updated_at is not None and any(
            (source_time := timestamp(item.get("source_updated_at"))) is None
            or source_time > paper_updated_at
            for item in public_items if isinstance(item, dict)
        ):
            errors.append("paper-trading updated_at predates embedded source metadata")
        expected_items = expected_projection.get("accounts", {}).get(market, {}).get("public_pending_signals", [])
        if public_items != expected_items:
            errors.append(f"paper-trading {market} public pending content mismatch")


def validate_us_candidate_selection(
    errors: list[str], us: dict[str, Any], us_pool: dict[str, Any],
) -> None:
    sections = ("ready_plant", "plant", "ready_harvest", "harvest", "exit")
    signals = us.get("flower_signals")
    if not isinstance(signals, dict) or set(signals) != set(sections):
        errors.append("US action sections must contain the complete five-section contract")
        return
    rows = us_pool.get("rows")
    expected_pool_size = len(US_UNIVERSE)
    raw_summary = us_pool.get("summary")
    summary: dict[str, Any] = raw_summary if isinstance(raw_summary, dict) else {}
    if not isinstance(rows, list) or len(rows) != expected_pool_size:
        errors.append(f"US current pool must contain exactly {expected_pool_size} rows")
        return
    if summary.get("universe") != expected_pool_size or summary.get("valid") != expected_pool_size:
        errors.append(f"US current pool summary must report {expected_pool_size} universe and valid rows")
    expected_symbols = {item[0] for item in US_UNIVERSE}
    actual_symbols = [str(row.get("symbol") or "") for row in rows if isinstance(row, dict)]
    if len(set(actual_symbols)) != expected_pool_size or set(actual_symbols) != expected_symbols:
        errors.append("US current pool must equal the unique configured 74-symbol universe")
    momentum_count = sum(1 for row in rows if isinstance(row, dict) and row.get("momentum_pass") is True)
    if summary.get("momentum_pass") != momentum_count:
        errors.append(f"US current pool summary momentum_pass must equal {momentum_count}")
    pool_symbols = {
        str(item.get("symbol") or "") for item in rows or [] if isinstance(item, dict)
    } if isinstance(rows, list) else set()
    model_date = str(us_pool.get("model_date") or "")
    seen: set[str] = set()
    actual_counts: dict[str, int] = {}
    for section in sections:
        items = signals.get(section)
        if not isinstance(items, list):
            errors.append(f"US action section {section} must be an array")
            continue
        actual_counts[section] = len(items)
        for item in items:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol") or "")
            if symbol not in pool_symbols or item.get("trade_date") != model_date:
                errors.append(
                    f"US action {symbol or '<empty>'} must belong to current pool and trade_date {model_date}"
                )
            if symbol in seen:
                errors.append(f"US action symbol appears in multiple sections: {symbol}")
            seen.add(symbol)
    if us.get("flower_counts") != actual_counts:
        errors.append(f"US flower_counts differs from action arrays: expected={actual_counts}")
    trigger_base = us_pool.get("trigger_base_rows")
    previous = trigger_base if isinstance(trigger_base, dict) else {}
    try:
        rebuilt = rebuild_us_flower_signals(rows, previous)
    except (KeyError, TypeError, ValueError) as exc:
        errors.append(f"US actions cannot be rebuilt from current pool: {exc}")
    else:
        if signals != rebuilt:
            errors.append("US actions differ from deterministic current-pool rebuild")


def validate_harvest_selection(
    errors: list[str], garden: dict[str, Any], pool: dict[str, Any],
) -> None:
    selection = garden.get("harvest_selection")
    if not isinstance(selection, dict):
        errors.append("garden-recommendations harvest_selection is required")
        return
    pool_date = str(pool.get("evaluation_date") or pool.get("latest_trade_date") or "")
    rows = pool.get("all_rows")
    row_map = {
        str(row.get("code") or ""): row for row in rows or [] if isinstance(row, dict)
    } if isinstance(rows, list) else {}
    items = garden.get("harvest")
    harvest_items = items if isinstance(items, list) else []
    codes = [str(item.get("code") or "") for item in harvest_items if isinstance(item, dict)]
    if selection.get("rule_version") != HARVEST_RULE_VERSION:
        errors.append("garden-recommendations harvest rule version mismatch")
    if selection.get("selection_mode") != "current_tracked_harvest_requalification":
        errors.append("garden-recommendations harvest selection_mode mismatch")
    if selection.get("selected_from_pool_date") != pool_date:
        errors.append("garden-recommendations harvest selected_from_pool_date mismatch")
    if selection.get("selected_codes") != codes or selection.get("selected_count") != len(codes):
        errors.append("garden-recommendations harvest selected_codes differs from harvest order")
    for item in harvest_items:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or "")
        row = row_map.get(code)
        reason = harvest_qualified_reason(row) if row else None
        if not row or not reason:
            errors.append(f"garden-recommendations harvest {code} lacks current-pool qualification")
            continue
        if str(row.get("date") or "") != pool_date:
            errors.append(f"garden-recommendations harvest {code} source row date mismatch")
        if item.get("selected_from_pool_date") != pool_date or item.get("last_qualified_date") != pool_date:
            errors.append(f"garden-recommendations harvest {code} qualification date mismatch")
        if item.get("qualified_reason") != reason:
            errors.append(f"garden-recommendations harvest {code} qualification reason mismatch")
        if item.get("source_fingerprint") != harvest_row_fingerprint(row):
            errors.append(f"garden-recommendations harvest {code} source fingerprint mismatch")


def validate_runtime_schema(
    errors: list[str], warnings: list[str], garden: dict[str, Any], a_pool: dict[str, Any], a_mid: dict[str, Any],
    shadow: dict[str, Any], kronos: dict[str, Any], us: dict[str, Any], us_pool: dict[str, Any], us_macro: dict[str, Any],
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

    validate_candidate_selection(errors, warnings, garden, a_pool)
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
            # Executable rows enforce the full stop < support < target contract.
            # Non-execution rows retain numeric/positive checks while target ordering
            # is classified by the research audit as model_not_applicable.
            executable = item.get("trade_state") in EXECUTION_ELIGIBLE_STATES
            validate_levels(
                errors if executable else warnings,
                label, item, allow_invalid=True,
                require_target_above_support=executable,
            )

    require_fields(errors, "a-share-mid-macro", a_mid, ("version", "generated_at", "market", "factors", "constraint"))
    garden_updated_at = timestamp(garden.get("updated_at"))
    mid_generated_at = timestamp(a_mid.get("generated_at"))
    if garden_updated_at is None:
        errors.append("garden-recommendations updated_at must be a timezone-aware timestamp")
    if mid_generated_at is None:
        errors.append("a-share-mid-macro generated_at must be a timezone-aware timestamp")
    if garden_updated_at is not None and mid_generated_at is not None and garden_updated_at < mid_generated_at:
        errors.append("garden-recommendations updated_at predates a-share-mid-macro generated_at")
    mid_factors = a_mid.get("factors")
    if a_mid.get("market") != "CN" or not isinstance(mid_factors, list) or len(mid_factors) != 3:
        errors.append("a-share-mid-macro requires market=CN and exactly 3 factors")
    if shadow.get("mode") != "shadow_research_only" or shadow.get("production_weights_changed") is not False:
        errors.append("a-share-shadow must remain shadow_research_only with unchanged production weights")
    enhancement = shadow.get("signal_enhancement")
    if not isinstance(enhancement, dict):
        errors.append("a-share-shadow signal_enhancement is required")
    else:
        if enhancement.get("formal_signal_logic_changed") is not False or enhancement.get("production_role") != "shadow_filter_and_audit_only":
            errors.append("a-share-shadow enhancement must remain audit-only with unchanged formal signals")
        if not isinstance(enhancement.get("summary"), dict) or not isinstance(enhancement.get("historical_validation"), dict):
            errors.append("a-share-shadow enhancement requires summary and historical_validation")
        coverage = enhancement.get("coverage")
        if not isinstance(coverage, dict) or int(coverage.get("symbols_at_least_260") or 0) < 82:
            errors.append("a-share-shadow enhancement requires at least 82 symbols with 260-bar history")
        if not isinstance(enhancement.get("feature_parameters"), dict):
            errors.append("a-share-shadow enhancement requires frozen feature_parameters")
        invalid_numbers = non_finite_paths(enhancement, "signal_enhancement")
        if invalid_numbers:
            errors.append(f"a-share-shadow enhancement contains non-finite values: {', '.join(invalid_numbers[:5])}")

    public_path_errors = validate_public_payload(kronos)
    if public_path_errors:
        errors.extend(f"path-shadow public schema: {message}" for message in public_path_errors)
    if kronos.get("mode") != "shadow_research_only" or kronos.get("production_weights_changed") is not False:
        errors.append("Kronos snapshot must remain shadow_research_only with unchanged production weights")
    if kronos.get("formal_signal_logic_changed") is not False or kronos.get("production_role") != "display_and_audit_only":
        errors.append("Kronos snapshot must remain display-and-audit only")
    basis = kronos.get("data_basis") or {}
    if basis.get("adjustment") != "qfq" or basis.get("is_final") is not True or basis.get("universe") != "formal_rotation":
        errors.append("Kronos snapshot requires final qfq formal_rotation data")
    definition = kronos.get("forecast_definition") or {}
    if definition.get("horizon_sessions") != 5 or len(definition.get("future_sessions") or []) != 5:
        errors.append("Kronos snapshot requires five future sessions")
    kronos_items = kronos.get("items") or []
    kronos_coverage = kronos.get("coverage") or {}
    expected = int(kronos_coverage.get("expected_symbols") or 0)
    predicted = int(kronos_coverage.get("predicted_symbols") or 0)
    rotation_codes = {
        str(item.get("code")) for item in (rows or [])
        if isinstance(item, dict) and item.get("asset_layer", "rotation") == "rotation"
    }
    kronos_symbols = [str(item.get("symbol") or "") for item in kronos_items if isinstance(item, dict)]
    if expected != predicted or predicted != len(kronos_items) or set(kronos_symbols) != rotation_codes:
        errors.append(f"Kronos coverage/symbol set mismatch: expected={expected}, predicted={predicted}, items={len(kronos_items)}, rotation={len(rotation_codes)}")
    if len(kronos_symbols) != len(set(kronos_symbols)):
        errors.append("Kronos snapshot contains duplicate symbols")
    for index, item in enumerate(kronos_items):
        label = f"Kronos items[{index}]"
        if item.get("as_of") != kronos.get("latest_trade_date"):
            errors.append(f"{label} as_of differs from Kronos latest_trade_date")
        if len(item.get("steps") or []) != 5:
            errors.append(f"{label} requires five prediction steps")
        for step in item.get("steps") or []:
            if any(number(step.get(field)) is None for field in ("open", "high", "low", "close")):
                errors.append(f"{label} contains non-finite OHLC")
                break
    invalid_kronos = non_finite_paths(kronos, "kronos")
    if invalid_kronos:
        errors.append(f"Kronos snapshot contains non-finite values: {', '.join(invalid_kronos[:5])}")

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


def audit_nonnegative_int(errors: list[str], label: str, value: Any, *, nullable: bool = False) -> None:
    if nullable and value is None:
        return
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        errors.append(f"{label} must be a non-negative integer{' or null' if nullable else ''}")


def audit_finite_number(
    errors: list[str], label: str, value: Any, *, nullable: bool = False,
    minimum: float | None = None, maximum: float | None = None,
) -> None:
    if nullable and value is None:
        return
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        errors.append(f"{label} must be a finite number{' or null' if nullable else ''}")
        return
    if ((minimum is not None and value < minimum)
            or (maximum is not None and value > maximum)):
        errors.append(f"{label} is outside the allowed range")


def validate_research_audit(
    errors: list[str], audit: dict[str, Any], backtest: dict[str, Any], pool: dict[str, Any],
) -> dict[str, Any]:
    require_fields(
        errors, "a-share-research-audit", audit,
        ("schema_version", "mode", "production_rules_changed", "dataset", "walk_forward", "execution_audit", "chip_poc"),
    )
    if audit.get("schema_version") != "research_audit_v1":
        errors.append("a-share-research-audit schema_version must be research_audit_v1")
    if audit.get("mode") != "shadow_research_only" or audit.get("production_rules_changed") is not False:
        errors.append("a-share-research-audit must remain shadow-only with unchanged production rules")

    dataset_value = audit.get("dataset")
    dataset: dict[str, Any] = dataset_value if isinstance(dataset_value, dict) else {}
    require_fields(
        errors, "a-share-research-audit dataset", dataset,
        ("algorithm", "value", "record_count", "pool_row_count", "as_of", "components", "provenance"),
    )
    audit_hash = dataset.get("value")
    if dataset.get("algorithm") != "sha256" or not isinstance(audit_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", audit_hash):
        errors.append("a-share-research-audit requires a lowercase 64-character hexadecimal sha256 fingerprint")

    records_value = backtest.get("records")
    rows_value = pool.get("all_rows")
    records: list[dict[str, Any]] = records_value if isinstance(records_value, list) else []
    rows: list[dict[str, Any]] = rows_value if isinstance(rows_value, list) else []
    expected_hash, expected_history, expected_pool = combined_fingerprint(records, rows, PROVENANCE)
    if audit_hash != expected_hash:
        errors.append("a-share-research-audit combined fingerprint differs from current inputs or provenance")
    if dataset.get("provenance") != PROVENANCE:
        errors.append("a-share-research-audit provenance differs from the frozen reproducibility contract")
    components_value = dataset.get("components")
    components: dict[str, Any] = components_value if isinstance(components_value, dict) else {}
    history_value = components.get("historical_direction_records")
    pool_value = components.get("current_action_pool")
    history_component: dict[str, Any] = history_value if isinstance(history_value, dict) else {}
    pool_component: dict[str, Any] = pool_value if isinstance(pool_value, dict) else {}
    if history_component != expected_history:
        errors.append("a-share-research-audit historical component fingerprint differs from input records")
    if pool_component != expected_pool:
        errors.append("a-share-research-audit action-pool component fingerprint differs from current pool")
    if dataset.get("record_count") != len(records) or dataset.get("pool_row_count") != len(rows):
        errors.append("a-share-research-audit input counts differ from current inputs")

    walk_value = audit.get("walk_forward")
    walk: dict[str, Any] = walk_value if isinstance(walk_value, dict) else {}
    if walk.get("status") not in {"evaluated", "insufficient_history"}:
        errors.append("a-share-research-audit walk_forward has invalid status")
    folds_value = walk.get("folds")
    folds: list[Any] = folds_value if isinstance(folds_value, list) else []
    if walk.get("status") == "evaluated" and not folds:
        errors.append("a-share-research-audit evaluated walk_forward requires at least one fold")
    if walk.get("status") == "insufficient_history" and folds:
        errors.append("a-share-research-audit insufficient_history must not contain evaluated folds")
    configuration_value = walk.get("configuration")
    configuration: dict[str, Any] = configuration_value if isinstance(configuration_value, dict) else {}
    if walk.get("status") == "evaluated" and configuration.get("purge_trade_dates") != 1:
        errors.append("a-share-research-audit walk_forward must purge one T+1 label date")
    aggregate_value = walk.get("aggregate")
    aggregate: dict[str, Any] = aggregate_value if isinstance(aggregate_value, dict) else {}
    if aggregate.get("fold_count") != len(folds):
        errors.append("a-share-research-audit aggregate fold_count differs from folds")
    audit_nonnegative_int(errors, "a-share-research-audit aggregate.fold_count", aggregate.get("fold_count"))
    audit_nonnegative_int(errors, "a-share-research-audit aggregate.oos_count", aggregate.get("oos_count"))
    audit_finite_number(errors, "a-share-research-audit aggregate.oos_hit_rate_pct", aggregate.get("oos_hit_rate_pct"), nullable=True, minimum=0, maximum=100)
    audit_finite_number(errors, "a-share-research-audit aggregate.oos_average_directional_return_pct", aggregate.get("oos_average_directional_return_pct"), nullable=True)
    audit_finite_number(errors, "a-share-research-audit aggregate.positive_fold_consistency_pct", aggregate.get("positive_fold_consistency_pct"), nullable=True, minimum=0, maximum=100)
    previous_test_end: str | None = None
    for index, fold in enumerate(folds):
        if not isinstance(fold, dict):
            errors.append(f"a-share-research-audit fold[{index}] must be an object")
            continue
        train_start = date_prefix(fold.get("train_start"))
        train_end = date_prefix(fold.get("train_end"))
        test_start = date_prefix(fold.get("test_start"))
        test_end = date_prefix(fold.get("test_end"))
        purged_value = fold.get("purged_dates")
        purged: list[Any] = purged_value if isinstance(purged_value, list) else []
        purge_dates = [date_prefix(value) for value in purged]
        purge_date = purge_dates[0] if len(purge_dates) == 1 else None
        if any(value is None for value in (train_start, train_end, test_start, test_end, purge_date)):
            errors.append(f"a-share-research-audit fold[{index}] has invalid dates")
            continue
        assert train_start is not None and train_end is not None
        assert test_start is not None and test_end is not None and purge_date is not None
        if not (train_start <= train_end < purge_date < test_start <= test_end):
            errors.append(f"a-share-research-audit fold[{index}] violates T+1 purge/no-lookahead ordering")
        if fold.get("label_horizon_sessions") != 1:
            errors.append(f"a-share-research-audit fold[{index}] label horizon must be 1")
        test_value = fold.get("test")
        test_metrics: dict[str, Any] = test_value if isinstance(test_value, dict) else {}
        audit_nonnegative_int(errors, f"a-share-research-audit fold[{index}].test.count", test_metrics.get("count"))
        audit_finite_number(errors, f"a-share-research-audit fold[{index}].test.hit_rate_pct", test_metrics.get("hit_rate_pct"), nullable=True, minimum=0, maximum=100)
        audit_finite_number(errors, f"a-share-research-audit fold[{index}].test.average_directional_return_pct", test_metrics.get("average_directional_return_pct"), nullable=True)
        audit_finite_number(errors, f"a-share-research-audit fold[{index}].hit_rate_degradation_pp", fold.get("hit_rate_degradation_pp"), nullable=True)
        if previous_test_end is not None and previous_test_end >= test_start:
            errors.append(f"a-share-research-audit fold[{index}] overlaps a prior OOS window")
        previous_test_end = test_end

    execution_value = audit.get("execution_audit")
    execution: dict[str, Any] = execution_value if isinstance(execution_value, dict) else {}
    blockers_value = execution.get("blockers")
    blockers: dict[str, Any] = blockers_value if isinstance(blockers_value, dict) else {}
    required_blockers = {
        "invalid_levels", "missing_or_nonfinite_levels", "nonpositive_levels",
        "ordering_violation", "model_not_applicable_for_trade_state",
        "stale_rows", "unknown_market_data",
        "pending_close_confirmation", "missing_strict_5m_bars",
    }
    if set(blockers) != required_blockers:
        errors.append("a-share-research-audit blocker schema is incomplete or contains unknown keys")
    for name in sorted(required_blockers):
        blocker_value = blockers.get(name)
        blocker: dict[str, Any] = blocker_value if isinstance(blocker_value, dict) else {}
        status = blocker.get("status")
        count = blocker.get("count")
        if status == "known":
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                errors.append(f"a-share-research-audit blocker {name} known count must be a non-negative integer")
        elif status == "unknown":
            if count is not None or not blocker.get("reason"):
                errors.append(f"a-share-research-audit blocker {name} unknown count must be null with a reason")
        else:
            errors.append(f"a-share-research-audit blocker {name} has invalid status")

    chip_value = audit.get("chip_poc")
    chip: dict[str, Any] = chip_value if isinstance(chip_value, dict) else {}
    if chip.get("status") not in {"evaluated", "blocked"}:
        errors.append("a-share-research-audit chip_poc has invalid status")
    audit_nonnegative_int(errors, "a-share-research-audit chip_poc.eligible_symbols", chip.get("eligible_symbols"))
    audit_nonnegative_int(errors, "a-share-research-audit chip_poc.blocked_symbols", chip.get("blocked_symbols"), nullable=True)
    if not isinstance(chip.get("items"), list):
        errors.append("a-share-research-audit chip_poc.items must be a list")
    generated_at = audit.get("generated_at")
    if not isinstance(generated_at, str) or not generated_at:
        errors.append("a-share-research-audit generated_at must be a non-empty string")
    else:
        expected = build_payload(backtest, pool, DEFAULT_TURNOVER, generated_at)
        if audit != expected:
            errors.append("a-share-research-audit derived payload differs from deterministic reconstruction")

    invalid = non_finite_paths(audit, "a-share-research-audit")
    if invalid:
        errors.append(f"a-share-research-audit contains non-finite values: {', '.join(invalid[:5])}")
    return dataset


def validate(data_dir: Path = DATA) -> CheckResult:
    errors: list[str] = []
    warnings: list[str] = []
    batches: dict[str, Any] = {}
    try:
        garden = load_json(data_dir / "garden-recommendations.json")
        a_pool = load_json(data_dir / "etf-garden-pool.json")
        a_mid = load_json(data_dir / "a-share-mid-macro.json")
        shadow = load_json(data_dir / "model-lab/a-share-shadow.json")
        kronos = load_json(data_dir / "model-lab/a-share-path-shadow.json")
        research_audit = load_json(data_dir / "model-lab/a-share-research-audit.json")
        backtest = load_json(data_dir / "etf-garden-backtest.json")
        us = load_json(data_dir / "us-etf-garden.json")
        us_pool = load_json(data_dir / "us-etf-pool.json")
        us_macro = load_json(data_dir / "us-macro-dashboard.json")
        paper = load_json(data_dir / "paper-trading.json")
    except ValueError as exc:
        return CheckResult("error", [str(exc)], warnings, batches)

    validate_runtime_schema(errors, warnings, garden, a_pool, a_mid, shadow, kronos, us, us_pool, us_macro)
    validate_us_candidate_selection(errors, us, us_pool)
    if garden.get("harvest_selection") is not None or str(garden.get("date") or "") >= "2026-07-28":
        validate_harvest_selection(errors, garden, a_pool)
    validate_public_pending(errors, paper, garden, us)

    audit_dataset = validate_research_audit(errors, research_audit, backtest, a_pool)

    a_date = require_date(errors, "A recommendations date", garden.get("date"))
    a_applies = require_date(errors, "A recommendations applies_to", garden.get("applies_to"))
    a_level = require_date(errors, "A recommendation level_data_as_of", garden.get("level_data_as_of"))
    pool_eval = require_date(errors, "A pool evaluation_date", a_pool.get("evaluation_date"))
    pool_latest = require_date(errors, "A pool latest_trade_date", a_pool.get("latest_trade_date"))
    audit_as_of = require_date(errors, "A research audit as_of", audit_dataset.get("as_of"))
    if audit_as_of and pool_latest and audit_as_of != pool_latest:
        errors.append(f"A research audit as_of {audit_as_of} differs from pool latest {pool_latest}")
    audit_pool_count = audit_dataset.get("pool_row_count")
    if not isinstance(audit_pool_count, int) or audit_pool_count != len(a_pool.get("all_rows") or []):
        errors.append("A research audit pool_row_count differs from current pool rows")
    mid_generated = require_date(errors, "A mid-macro generated_at", a_mid.get("generated_at"))
    shadow_latest = require_date(errors, "A shadow latest_trade_date", shadow.get("latest_trade_date"))
    kronos_latest = require_date(errors, "A Kronos latest_trade_date", kronos.get("latest_trade_date"))
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
    if kronos_latest and kronos_latest not in shadow_allowed_dates:
        errors.append(
            "A-share Kronos date outside plan/baseline batches: "
            f"allowed={sorted(shadow_allowed_dates)}, kronos={kronos_latest}"
        )
    stage = str(garden.get("stage") or "")
    allowed_action_dates = {date for date in (a_date, pool_latest) if date}
    unexpected_action_dates = [date for date in action_dates if date not in allowed_action_dates]
    if unexpected_action_dates:
        errors.append(
            f"A action price dates outside plan/baseline batches: allowed={sorted(allowed_action_dates)}, actions={action_dates}"
        )
    if stage.startswith("22:00"):
        final_dates = [x for x in (a_date, a_level, pool_latest, shadow_latest, kronos_latest) if x]
        if len(set(final_dates)) > 1:
            errors.append(
                "A 22:00 final stage requires one final date: "
                f"recommendations={a_date}, levels={a_level}, pool_latest={pool_latest}, shadow={shadow_latest}, kronos={kronos_latest}"
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
    us_expected = [x for x in (us_date, us_model, us_quote, macro_primary) if x]
    if len(set(us_expected)) > 1:
        errors.append(
            "US batch mismatch: "
            f"garden={us_date}, model={us_model}, quote={us_quote}, "
            f"macro_generated={macro_generated}, macro_primary={macro_primary}"
        )
    if macro_generated and macro_primary and macro_generated < macro_primary:
        errors.append(
            f"US macro generated_at predates market observation: generated={macro_generated}, primary={macro_primary}"
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
        "kronos_date": kronos_latest,
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

#!/usr/bin/env python3
"""Build, archive, and optionally publish the US ETF Compass weekly research page."""
from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import math
import os
import statistics
import subprocess
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "public/data"
LEARNING = DATA / "us-compass-learning.json"
SHADOW = DATA / "us-compass-shadow.json"
POOL = DATA / "us-etf-pool.json"
HEALTH = DATA / "us-compass-health.json"
OUT = DATA / "us-compass-research.json"
CATALOG = DATA / "catalog.json"
IWENCAI_WRAPPER = Path("/root/.hermes/scripts/iwencai-skill-run")
PROJECT = "etf-rotation-blog"
if __package__:
    from .us_compass_fingerprint import consistent_model_fingerprint as _strict_consistent_fingerprint
    from .validate_public_data_contracts import validate_us_compass_health_payload
else:
    fingerprint_path = Path(__file__).resolve().with_name("us_compass_fingerprint.py")
    fingerprint_spec = importlib.util.spec_from_file_location(
        f"_us_compass_fingerprint_{id(fingerprint_path)}", fingerprint_path
    )
    if fingerprint_spec is None or fingerprint_spec.loader is None:
        raise ImportError(f"cannot load model fingerprint from {fingerprint_path}")
    fingerprint_module = importlib.util.module_from_spec(fingerprint_spec)
    fingerprint_spec.loader.exec_module(fingerprint_module)
    _strict_consistent_fingerprint = fingerprint_module.consistent_model_fingerprint
    validator_path = Path(__file__).resolve().with_name("validate_public_data_contracts.py")
    validator_spec = importlib.util.spec_from_file_location(
        f"_validate_public_data_contracts_{id(validator_path)}", validator_path
    )
    if validator_spec is None or validator_spec.loader is None:
        raise ImportError(f"cannot load health validator from {validator_path}")
    validator_module = importlib.util.module_from_spec(validator_spec)
    validator_spec.loader.exec_module(validator_module)
    validate_us_compass_health_payload = validator_module.validate_us_compass_health_payload


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {} if default is None else default


def _validate_archive_report(report: Any, field: str) -> None:
    if not isinstance(report, dict):
        raise ValueError(f"{field} must be a JSON object")
    week = report.get("week_key")
    if not isinstance(week, str) or not week.strip():
        raise ValueError(f"{field} week_key must be a nonempty string")
    parse_iso_date(report.get("trade_date"), f"{field} trade_date")
    if "generated_at" in report:
        parse_utc_timestamp(report["generated_at"], f"{field} generated_at")


def validate_existing_archive_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("existing archive must be a JSON object")
    schema_version = payload.get("schema_version")
    if schema_version is not None and schema_version not in {
        "us-compass-research-v1", "us-compass-research-v2",
    }:
        raise ValueError("existing archive schema_version is invalid")
    reports = payload.get("reports")
    if not isinstance(reports, list):
        raise ValueError("existing archive reports must be a list")
    week_keys: set[str] = set()
    for index, report in enumerate(reports):
        _validate_archive_report(report, f"existing archive reports[{index}]")
        week = report["week_key"]
        if week in week_keys:
            raise ValueError(f"existing archive duplicate week_key: {week}")
        week_keys.add(week)
    if "updated_at" in payload:
        parse_utc_timestamp(payload["updated_at"], "existing archive updated_at")
    if "latest_week" in payload:
        latest_week = payload["latest_week"]
        if not isinstance(latest_week, str) or not latest_week.strip():
            raise ValueError("existing archive latest_week must be a nonempty string")
        if latest_week not in week_keys:
            raise ValueError("existing archive latest_week must belong to a report")
    return payload


def read_existing_archive(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": "us-compass-research-v2", "reports": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"existing archive unreadable or invalid: {path}") from exc
    return validate_existing_archive_payload(payload)


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def pct(value: Any) -> float | None:
    return round(float(value) * 100, 2) if value is not None else None


def week_key(value: str) -> str:
    parsed = date.fromisoformat(value[:10])
    year, week, _ = parsed.isocalendar()
    return f"{year}-W{week:02d}"


def parse_utc_timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field} timestamp must be a UTC-aware ISO datetime")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} timestamp must be a UTC-aware ISO datetime") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError(f"{field} timestamp must be UTC-aware")
    return parsed


def parse_iso_date(value: Any, field: str) -> date:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an ISO date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO date") from exc


def validated_exposure(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("latest learning snapshot exposure must be a finite number between 0 and 1")
    exposure = float(value)
    if not math.isfinite(exposure) or not 0 <= exposure <= 1:
        raise ValueError("latest learning snapshot exposure must be a finite number between 0 and 1")
    return exposure


def current_rows(pool: dict[str, Any], top_symbols: list[str]) -> list[dict[str, Any]]:
    by_symbol = {str(row.get("symbol") or ""): row for row in pool.get("rows", []) if row.get("symbol")}
    rows = []
    for symbol in top_symbols:
        row = by_symbol.get(symbol, {})
        rows.append({
            "symbol": symbol,
            "theme": row.get("theme") or row.get("asset_type") or "—",
            "trend_score": row.get("trend_score"),
            "risk_score": row.get("trading_risk_score"),
            "state": row.get("trade_state") or "—",
        })
    return rows


def consistent_model_fingerprint(learning: dict[str, Any], shadow: dict[str, Any]) -> dict[str, Any]:
    """Return the canonical shared fingerprint or fail closed for v2 reports."""
    try:
        return _strict_consistent_fingerprint(learning, shadow)
    except ValueError as exc:
        raise ValueError(f"model fingerprint invalid: {exc}") from exc


def _compact(section: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {field: section.get(field) for field in fields}


def _data_quality(health: dict[str, Any], pool: dict[str, Any]) -> dict[str, Any]:
    overall = health["overall"]
    sample = health["sample_maturity"]
    shadow = health["shadow_health"]
    cost = health["cost_sensitivity"]
    reasons = list(overall.get("reasons") or [])
    key_statuses = (overall.get("status"), sample.get("status"), shadow.get("status"))
    if "UNAVAILABLE" in key_statuses:
        status = "UNAVAILABLE"
    elif "ACCUMULATING" in key_statuses or not sample.get("mature"):
        status = "ACCUMULATING"
    else:
        status = "HEALTHY"
    if cost.get("status") == "UNAVAILABLE":
        reasons.extend(reason for reason in cost.get("reasons") or [] if reason not in reasons)
    return {
        "status": status,
        "reasons": reasons,
        "health_model_date": health.get("model_date"),
        "pool_model_date": pool.get("model_date"),
        "fingerprint_consistent": True,
        "production_change_allowed": False,
    }


def build_signal_validation(learning: dict[str, Any], fingerprint: dict[str, Any]) -> dict[str, Any]:
    """Project persisted forward outcomes into a read-only, horizon-level audit."""
    snapshots = learning.get("snapshots") or []
    declared = set(fingerprint.get("horizons") or [])
    declared_labels = "、".join(f"{days}D" for days in sorted(declared))
    rows = []
    thresholds = {1: 20, 5: 20, 20: 12}
    for days, label in ((1, "D1"), (5, "5D"), (20, "20D"), (60, "60D")):
        horizon = f"t{days}"
        if days not in declared:
            rows.append({
                "label": label, "horizon": horizon, "status": "UNAVAILABLE", "observations": 0,
                "mean_return": None, "benchmark_return": None, "excess_return": None,
                "mfe": None, "mae": None,
                "unavailable_reasons": [f"{label} UNAVAILABLE：当前模型指纹仅声明{declared_labels}周期"],
            })
            continue
        outcomes = []
        for snapshot in snapshots:
            outcome = (snapshot.get("outcomes") or {}).get(horizon)
            if not isinstance(outcome, dict):
                continue
            model_return = outcome.get("top10_equal_return")
            benchmark_return = outcome.get("spy_return")
            if (
                isinstance(model_return, bool) or not isinstance(model_return, (int, float))
                or isinstance(benchmark_return, bool) or not isinstance(benchmark_return, (int, float))
                or not math.isfinite(model_return) or not math.isfinite(benchmark_return)
            ):
                continue
            outcomes.append((float(model_return), float(benchmark_return)))
        observations = len(outcomes)
        model_mean = statistics.fmean(item[0] for item in outcomes) if outcomes else None
        benchmark_mean = statistics.fmean(item[1] for item in outcomes) if outcomes else None
        rows.append({
            "label": label, "horizon": horizon,
            "status": "EVALUATED" if observations >= thresholds[days] else "ACCUMULATING",
            "observations": observations,
            "mean_return": model_mean, "benchmark_return": benchmark_mean,
            "excess_return": round(model_mean - benchmark_mean, 12) if model_mean is not None and benchmark_mean is not None else None,
            "mfe": None, "mae": None,
            "unavailable_reasons": ["MFE/MAE UNAVAILABLE：持久化快照缺少信号窗口内逐日高低价路径"],
        })
    return {
        "model_version": fingerprint["model_version"],
        "production_change_allowed": False,
        "horizons": rows,
    }


def build_rotation_research_summary(shadow: dict[str, Any]) -> dict[str, Any]:
    """Project the rotation sidecar without granting production authority."""
    source = shadow.get("rotation_research")
    if not isinstance(source, dict):
        return {
            "status": "UNAVAILABLE", "signal": None, "model_date": None,
            "production_change_allowed": False, "production_weights_changed": False,
            "coverage": {}, "observation_gate": {}, "selection": [],
            "reason": "rotation shadow research is unavailable",
        }
    if (
        source.get("mode") != "shadow_research_only"
        or source.get("production_change_allowed") is not False
        or source.get("production_weights_changed") is not False
    ):
        return {
            "status": "UNAVAILABLE", "signal": None, "model_date": source.get("model_date"),
            "production_change_allowed": False, "production_weights_changed": False,
            "coverage": {}, "observation_gate": {}, "selection": [],
            "reason": "rotation shadow isolation contract is invalid",
        }
    if source.get("status") == "UNAVAILABLE":
        return {
            "status": "UNAVAILABLE", "signal": None, "model_date": source.get("model_date"),
            "production_change_allowed": False, "production_weights_changed": False,
            "coverage": {}, "observation_gate": {}, "selection": [],
            "reason": source.get("reason") or "rotation shadow research is unavailable",
        }
    gate_source = source.get("observation_gate")
    coverage_source = source.get("coverage")
    allowed_gate_statuses = {"ACCUMULATING", "MINIMUM_REACHED", "PREFERRED_REACHED"}
    if not isinstance(coverage_source, dict) or not isinstance(gate_source, dict):
        valid_contract = False
    else:
        coverage: dict[str, Any] = coverage_source
        gate: dict[str, Any] = gate_source
        valid_coverage = all(
            isinstance(coverage.get(field), int)
            and not isinstance(coverage.get(field), bool)
            and coverage[field] >= 0
            for field in ("requested", "evaluated", "unavailable")
        )
        if valid_coverage:
            valid_coverage = coverage["evaluated"] + coverage["unavailable"] == coverage["requested"]
        valid_gate = all(
            isinstance(gate.get(field), int)
            and not isinstance(gate.get(field), bool)
            and gate[field] >= 0
            for field in ("minimum_completed_days", "preferred_completed_days", "completed_days")
        )
        if valid_gate:
            valid_gate = (
                gate["minimum_completed_days"] > 0
                and gate["preferred_completed_days"] >= gate["minimum_completed_days"]
                and gate.get("status") in allowed_gate_statuses
            )
        valid_contract = valid_coverage and valid_gate
    if not valid_contract:
        return {
            "status": "UNAVAILABLE", "signal": None, "model_date": source.get("model_date"),
            "production_change_allowed": False, "production_weights_changed": False,
            "coverage": {}, "observation_gate": {}, "selection": [],
            "reason": "rotation shadow coverage or observation gate is invalid",
        }
    assert isinstance(coverage_source, dict) and isinstance(gate_source, dict)
    coverage = coverage_source
    gate = gate_source
    selection_source = source.get("selection")
    if not isinstance(selection_source, list):
        return {
            "status": "UNAVAILABLE", "signal": None, "model_date": source.get("model_date"),
            "production_change_allowed": False, "production_weights_changed": False,
            "coverage": {}, "observation_gate": copy.deepcopy(gate), "selection": [],
            "reason": "rotation shadow selection must be a list",
        }
    selection = []
    for item in selection_source[:10]:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("symbol"), str)
            or not item["symbol"].strip()
        ):
            continue
        row = {key: item.get(key) for key in ("symbol", "theme", "return_21d", "return_63d", "rotation_score")}
        if row["theme"] is not None and not isinstance(row["theme"], str):
            row["theme"] = None
        for field in ("return_21d", "return_63d", "rotation_score"):
            value = row[field]
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
            ):
                row[field] = None
        selection.append(row)
    return {
        "status": gate.get("status") or source.get("status") or "ACCUMULATING",
        "signal": source.get("signal"),
        "model_date": source.get("model_date"),
        "production_change_allowed": False,
        "production_weights_changed": False,
        "coverage": copy.deepcopy(coverage),
        "observation_gate": copy.deepcopy(gate),
        "selection": selection,
        "reason": source.get("reason"),
    }


def build_report(
    learning: dict[str, Any], shadow: dict[str, Any], pool: dict[str, Any],
    iwencai: dict[str, Any] | None = None, *, health: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if health is None:
        raise ValueError("health is required for us-compass-research-v2")
    if not isinstance(health, dict) or "model_fingerprint" not in health:
        raise ValueError("health model fingerprint missing")
    health_errors = validate_us_compass_health_payload(health)
    if health_errors:
        raise ValueError(f"health payload invalid: {health_errors[0]}")
    if health.get("schema_version") != "us-compass-health-v1" or health.get("market") != "US":
        raise ValueError("health payload invalid: expected us-compass-health-v1 market US")
    model_fingerprint = consistent_model_fingerprint(learning, shadow)
    if health.get("model_fingerprint") != model_fingerprint:
        raise ValueError("health model fingerprint must exactly equal learning/shadow model fingerprint")
    pool_model_date = pool.get("model_date")
    if health.get("model_date") != pool_model_date:
        raise ValueError("health model_date must equal pool model_date")
    model_date = parse_iso_date(pool_model_date, "pool model_date")
    snapshots = learning.get("snapshots")
    if not isinstance(snapshots, list) or not snapshots:
        raise ValueError("latest learning snapshot date must equal pool/health model_date")
    latest = snapshots[-1]
    latest_date = parse_iso_date(latest.get("date"), "latest learning snapshot date")
    if latest_date != model_date:
        raise ValueError("latest learning snapshot date must equal pool/health model_date")
    history = shadow.get("history") or []
    if not isinstance(history, list):
        raise ValueError("shadow history must be a list")
    history_dates = [parse_iso_date(item.get("exit_date"), "shadow history exit_date") for item in history]
    latest_history_date = history_dates[-1] if history_dates else None
    shadow_observations = health["shadow_health"]["observations"]
    if shadow_observations > 0 and latest_history_date != model_date:
        raise ValueError("latest shadow history exit_date must equal model_date when shadow observations exist")
    if latest_history_date is not None and latest_history_date > model_date:
        raise ValueError("latest shadow history exit_date must not exceed model_date")
    report_generated_at = datetime.now(timezone.utc)
    health_generated_at = parse_utc_timestamp(health.get("generated_at"), "health.generated_at")
    if health_generated_at > report_generated_at:
        raise ValueError("health.generated_at must not be later than report generated_at")
    for source_name, source in (("learning", learning), ("shadow", shadow)):
        if source.get("updated_at") is not None:
            updated_at = parse_utc_timestamp(source.get("updated_at"), f"{source_name}.updated_at")
            if updated_at > health_generated_at:
                raise ValueError(f"{source_name}.updated_at must not be later than health.generated_at")
    trade_date = str(pool_model_date)
    metrics: dict[str, Any] = {}
    for key in ("t1", "t5", "t20"):
        raw = (learning.get("metrics") or {}).get(key) or {}
        reference = float(raw.get("random_deviation_reference") or 0.333333)
        deviation = raw.get("deviation_mean")
        metrics[key] = {
            "observations": int(raw.get("observations") or 0),
            "rank_ic_pct": pct(raw.get("rank_ic_mean")),
            "positive_rate_pct": pct(raw.get("rank_ic_positive_rate")),
            "deviation_pct": pct(deviation),
            "random_reference_pct": pct(reference),
            "deviation_vs_random_pp": round((float(deviation) - reference) * 100, 2) if deviation is not None else None,
        }
    stats = shadow.get("stats") or {}
    portfolios = {}
    for name in ("benchmark", "timing", "rotation", "fusion"):
        row = stats.get(name) or {}
        portfolios[name] = {
            "total_return_pct": pct(row.get("total_return")),
            "max_drawdown_pct": pct(row.get("max_drawdown")),
            "equity": row.get("equity"),
        }
    benchmark = float((stats.get("benchmark") or {}).get("total_return") or 0)
    timing = float((stats.get("timing") or {}).get("total_return") or 0)
    rotation = float((stats.get("rotation") or {}).get("total_return") or 0)
    fusion = float((stats.get("fusion") or {}).get("total_return") or 0)
    t5_observations = metrics["t5"]["observations"]
    exposure = validated_exposure(latest.get("exposure"))
    top_symbols = [str(item) for item in (latest.get("top10") or [])][:10]
    source = iwencai or {"status": "unavailable", "summary": "问财验证暂不可用", "source": "同花顺问财"}
    return {
        "schema_version": "us-compass-research-v2",
        "week_key": week_key(trade_date),
        "trade_date": trade_date,
        "generated_at": report_generated_at.isoformat(),
        "model_fingerprint": model_fingerprint,
        "health_summary": {
            **_compact(health["overall"], ("status", "score", "reasons")),
            "model_date": health["model_date"],
            "generated_at": health["generated_at"],
        },
        "sample_maturity": json.loads(json.dumps(health["sample_maturity"])),
        "walk_forward_summary": _compact(
            health["walk_forward"],
            ("status", "windows", "evaluated_windows", "positive_windows", "positive_slice_rate", "score", "reasons"),
        ),
        "shadow_health_summary": {
            **_compact(health["shadow_health"], ("status", "observations", "return", "max_drawdown", "score", "reasons")),
            "portfolios": {
                name: _compact(
                    health["shadow_health"]["portfolios"][name],
                    ("total_return", "max_drawdown", "annualized_volatility", "positive_period_rate", "excess_return_vs_benchmark"),
                )
                for name in ("benchmark", "timing", "rotation", "fusion")
            },
        },
        "cost_sensitivity_summary": {
            **_compact(
                health["cost_sensitivity"],
                ("status", "baseline_cost", "observations", "break_even_cost", "score", "reasons"),
            ),
            "scenarios": [
                _compact(item, ("one_way_cost", "value", "annualized_return", "max_drawdown"))
                for item in health["cost_sensitivity"]["scenarios"]
            ],
        },
        "data_quality": _data_quality(health, pool),
        "signal_validation": build_signal_validation(learning, model_fingerprint),
        "rotation_research_summary": build_rotation_research_summary(shadow),
        "verdict": "达到月度评估门槛" if t5_observations >= 20 else "样本积累中",
        "snapshot_count": len(snapshots),
        "metrics": metrics,
        "portfolios": portfolios,
        "attribution": {
            "timing_pp": round((timing - benchmark) * 100, 2),
            "rotation_pp": round((rotation - benchmark) * 100, 2),
            "interaction_pp": round((fusion - timing - rotation + benchmark) * 100, 2),
            "fusion_vs_benchmark_pp": round((fusion - benchmark) * 100, 2),
        },
        "risk_budget": {
            "current_exposure_pct": round(exposure * 100, 1),
            "regime": (pool.get("market_regime") or {}).get("state") or latest.get("regime") or "—",
            "allocation": (pool.get("market_regime") or {}).get("equity_allocation") or "—",
            "allowed_budgets_pct": [0, 50, 100],
        },
        "top10": current_rows(pool, top_symbols),
        "iwencai": {
            "status": source.get("status") or "unavailable",
            "source": "同花顺问财",
            "summary": source.get("summary") or "问财验证暂不可用",
        },
        "execution_basis": {
            "signal": "T日收盘生成",
            "execution": "T+1开盘执行",
            "rebalance": "次日开盘再平衡",
            "one_way_cost_pct": pct(shadow.get("one_way_cost")) or 0.1,
        },
        "production_change_allowed": False,
        "observations": [
            "T+5成熟样本达到20个前维持观察。" if t5_observations < 20 else "T+5样本已达到月度评估门槛，生产调整仍需人工授权。",
            "T+20无成熟样本时持续标记样本积累中。" if metrics["t20"]["observations"] == 0 else "继续观察T+20稳定性。",
            "周报仅记录研究证据，不自动修改权重或仓位阈值。",
        ],
    }


def merge_archive(existing: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    validate_existing_archive_payload(existing)
    try:
        _validate_archive_report(report, "incoming report")
    except ValueError as exc:
        if "incoming report" in str(exc):
            raise
        raise ValueError(f"incoming report invalid: {exc}") from exc
    reports = [item for item in existing["reports"] if item["week_key"] != report["week_key"]]
    reports.append(report)
    reports.sort(
        key=lambda item: (str(item.get("trade_date") or ""), str(item.get("generated_at") or "")),
        reverse=True,
    )
    reports = reports[:104]
    return {
        "schema_version": "us-compass-research-v2",
        "updated_at": report.get("generated_at") or datetime.now(timezone.utc).isoformat(),
        "latest_week": reports[0]["week_key"],
        "reports": reports,
    }


def iwencai_validation(pool: dict[str, Any]) -> dict[str, Any]:
    if not IWENCAI_WRAPPER.exists():
        return {"status": "unavailable", "summary": "问财验证暂不可用", "source": "同花顺问财"}
    rows = sorted(pool.get("rows") or [], key=lambda row: float(row.get("trend_score") or 0), reverse=True)[:10]
    themes = []
    for row in rows:
        theme = str(row.get("theme") or "").strip()
        if theme and theme not in themes:
            themes.append(theme)
    query = "美股 " + " ".join(themes[:5]) + " 近20日涨幅、成交额、行业，按涨幅排序"
    cmd = [str(IWENCAI_WRAPPER), "hithink-usstock-selector", "-q", query, "--limit", "10", "--timeout", "45"]
    proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True)
    if proc.returncode:
        return {"status": "unavailable", "summary": "问财验证暂不可用", "source": "同花顺问财"}
    try:
        payload = json.loads(proc.stdout)
    except ValueError:
        return {"status": "unavailable", "summary": "问财验证暂不可用", "source": "同花顺问财"}
    data = payload.get("datas") or []
    names = [str(item.get("股票简称") or item.get("股票名称") or item.get("股票代码") or "").strip() for item in data[:5]]
    names = [name for name in names if name]
    summary = f"问财查询“{query}”，返回{int(payload.get('code_count') or len(data))}条候选；前列样本：{'、'.join(names)}。" if names else f"问财查询“{query}”返回空结果，外部证据保持中性。"
    return {"status": "ok", "summary": summary, "source": "同花顺问财"}


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, check=True)


def publish() -> str:
    try:
        from pages_release import release_pages
    except ModuleNotFoundError:
        from scripts.pages_release import release_pages
    run(["python3", "scripts/generate_data_catalog.py"])
    run(["python3", "scripts/validate_public_data_contracts.py"])
    run(["npm", "run", "build"])
    run(["git", "add", "public/data/us-compass-research.json", "public/data/catalog.json", "public/data/us-compass-health.json"])
    staged = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT).returncode != 0
    if staged:
        run(["git", "commit", "-m", f"data: publish US Compass research {read_json(OUT).get('latest_week')}"])
        run(["git", "push", "origin", "HEAD:main"])
    return release_pages([
        "https://etf.peekabo.cc/us-compass/research/",
        "https://etf.peekabo.cc/data/us-compass-research.json",
    ], {
        "https://etf.peekabo.cc/data/us-compass-research.json": OUT,
    })


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--learning", type=Path, default=LEARNING)
    parser.add_argument("--shadow", type=Path, default=SHADOW)
    parser.add_argument("--pool", type=Path, default=POOL)
    parser.add_argument("--health", type=Path, default=HEALTH)
    parser.add_argument("--output", type=Path, default=OUT)
    parser.add_argument("--iwencai", action="store_true")
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args(argv)
    if args.publish and args.output != OUT:
        print("publish requires the default production output path", file=sys.stderr)
        return 2
    using_production_inputs = (
        args.learning == LEARNING and args.shadow == SHADOW and args.health == HEALTH
    )
    if using_production_inputs:
        try:
            run([
                "python3", "scripts/generate_us_compass_health.py",
                "--learning", str(args.learning),
                "--shadow", str(args.shadow),
                "--output", str(args.health),
            ])
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or str(exc)).strip()
            print(f"staging blocker: US Compass health regeneration failed: {detail}", file=sys.stderr)
            return 2
    if not args.health.is_file():
        print(f"staging blocker: US Compass health input unavailable: {args.health}", file=sys.stderr)
        return 2
    try:
        learning = json.loads(args.learning.read_text(encoding="utf-8"))
        shadow = json.loads(args.shadow.read_text(encoding="utf-8"))
        pool = json.loads(args.pool.read_text(encoding="utf-8"))
        health = json.loads(args.health.read_text(encoding="utf-8"))
        report = build_report(
            learning, shadow, pool,
            iwencai_validation(pool) if args.iwencai else None,
            health=health,
        )
        archive = merge_archive(
            read_existing_archive(args.output),
            report,
        )
        atomic_write_json(args.output, archive)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.publish:
        print(publish())
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Generate sample-maturity and rolling RankIC health for the US ETF Compass."""
from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import math
import os
import statistics
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LEARNING = ROOT / "public/data/us-compass-learning.json"
DEFAULT_SHADOW = ROOT / "public/data/us-compass-shadow.json"
DEFAULT_OUTPUT = ROOT / "public/data/us-compass-health.json"
HORIZON_THRESHOLDS = {"t1": (20, 40), "t5": (20, 40), "t20": (12, 20)}
SLICE_SIZE = 5
STAGING_BLOCKER = (
    "staging blocker: default US Compass learning and shadow artifacts must be regenerated "
    "by update_us_compass_learning.py to add matching model_fingerprint values"
)

if __package__:
    from .us_compass_fingerprint import consistent_model_fingerprint
    from .us_compass_research_metrics import annualized_volatility, rate_shadow_health, rate_time_slice_audit
else:
    fingerprint_path = Path(__file__).resolve().with_name("us_compass_fingerprint.py")
    fingerprint_spec = importlib.util.spec_from_file_location(
        f"_us_compass_fingerprint_{id(fingerprint_path)}", fingerprint_path
    )
    if fingerprint_spec is None or fingerprint_spec.loader is None:
        raise ImportError(f"cannot load model fingerprint from {fingerprint_path}")
    fingerprint_module = importlib.util.module_from_spec(fingerprint_spec)
    fingerprint_spec.loader.exec_module(fingerprint_module)
    consistent_model_fingerprint = fingerprint_module.consistent_model_fingerprint
    metrics_path = Path(__file__).resolve().with_name("us_compass_research_metrics.py")
    metrics_spec = importlib.util.spec_from_file_location(
        f"_us_compass_research_metrics_{id(metrics_path)}", metrics_path
    )
    if metrics_spec is None or metrics_spec.loader is None:
        raise ImportError(f"cannot load research metrics from {metrics_path}")
    metrics_module = importlib.util.module_from_spec(metrics_spec)
    metrics_spec.loader.exec_module(metrics_module)
    rate_time_slice_audit = metrics_module.rate_time_slice_audit
    annualized_volatility = metrics_module.annualized_volatility
    rate_shadow_health = metrics_module.rate_shadow_health


def _valid_date(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _utc_timestamp(value: str | None) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    if not isinstance(value, str):
        raise ValueError("generated_at must be an ISO UTC date-time")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("generated_at must be an ISO UTC date-time") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError("generated_at must be an ISO UTC date-time")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def extract_rank_ic_series(learning: Any, horizon: str) -> list[dict[str, Any]]:
    """Return matured RankIC points with signal and outcome-date provenance."""
    if not isinstance(learning, dict):
        raise ValueError("learning must be an object")
    snapshots = learning.get("snapshots")
    if not isinstance(snapshots, list):
        raise ValueError("learning snapshots must be an array")
    if not horizon.startswith("t") or not horizon[1:].isdigit() or int(horizon[1:]) < 1:
        raise ValueError(f"invalid horizon: {horizon}")
    step = int(horizon[1:])
    normalized: list[dict[str, Any]] = []
    seen_dates: set[str] = set()
    for index, snapshot in enumerate(snapshots):
        if not isinstance(snapshot, dict):
            raise ValueError(f"snapshot {index} must be an object")
        raw_date = snapshot.get("date")
        if not _valid_date(raw_date):
            raise ValueError(f"snapshot {index} date must be valid YYYY-MM-DD")
        if raw_date in seen_dates:
            raise ValueError(f"duplicate snapshot date: {raw_date}")
        seen_dates.add(raw_date)
        normalized.append(snapshot)
    normalized.sort(key=lambda snapshot: snapshot["date"])

    points: list[dict[str, Any]] = []
    seen_outcome_dates: set[str] = set()
    for index, snapshot in enumerate(normalized):
        raw_date = snapshot["date"]
        outcomes = snapshot.get("outcomes")
        if outcomes is None:
            continue
        if not isinstance(outcomes, dict):
            raise ValueError(f"snapshot {raw_date} outcomes must be an object")
        outcome = outcomes.get(horizon)
        if outcome is None:
            continue
        if not isinstance(outcome, dict):
            raise ValueError(f"snapshot {raw_date} {horizon} outcome must be an object")
        end_date = outcome.get("end_date")
        if not _valid_date(end_date):
            raise ValueError(f"snapshot {raw_date} {horizon} end_date must be valid YYYY-MM-DD")
        future_index = index + step
        if future_index >= len(normalized):
            raise ValueError(f"snapshot {raw_date} {horizon} outcome has no future snapshot at +{step}")
        expected_end_date = normalized[future_index]["date"]
        if end_date != expected_end_date:
            raise ValueError(
                f"snapshot {raw_date} {horizon} end_date {end_date} expected {expected_end_date}"
            )
        value = outcome.get("rank_ic")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"snapshot {raw_date} {horizon} rank_ic must be finite")
        if end_date in seen_outcome_dates:
            raise ValueError(f"duplicate {horizon} rank_ic outcome date: {end_date}")
        seen_outcome_dates.add(end_date)
        points.append({"signal_date": raw_date, "date": end_date, "value": float(value)})
    points.sort(key=lambda point: point["date"])
    return points


def horizon_health(series: list[dict[str, Any]], initial: int, stable: int) -> dict[str, Any]:
    """Summarize one horizon, withholding result fields before its own sample gate."""
    count = len(series)
    base = {
        "status": "ACCUMULATING", "observations": count, "minimum_required": initial,
        "maturity_ratio": min(1.0, count / initial), "rank_ic_mean": None,
        "rank_ic_median": None, "rank_ic_std": None, "icir": None,
        "positive_rate": None, "recent_5_mean": None, "recent_5_count": 0,
        "recent_10_mean": None, "recent_10_count": 0, "trend": None,
        "series": copy.deepcopy(series),
    }
    if count < initial:
        return base
    values = [point["value"] for point in series]
    mean = statistics.fmean(values)
    median = statistics.median(values)
    std = statistics.stdev(values) if count >= 2 else None
    icir = mean / std if std is not None and std != 0 else None
    positive_rate = sum(value > 0 for value in values) / count
    recent_5 = values[-min(5, count):]
    recent_10 = values[-min(10, count):]
    trend = "FLAT"
    if count >= 10:
        difference = statistics.fmean(values[-5:]) - statistics.fmean(values[-10:-5])
        if difference > 0.005:
            trend = "IMPROVING"
        elif difference < -0.005:
            trend = "WEAKENING"
    if count >= stable and mean > 0 and icir is not None and icir > 0:
        status = "STABLE"
    elif mean >= 0 or positive_rate >= 0.5:
        status = "MIXED"
    else:
        status = "FRAGILE"
    return {
        **base, "status": status, "rank_ic_mean": mean, "rank_ic_median": median,
        "rank_ic_std": std, "icir": icir, "positive_rate": positive_rate,
        "recent_5_mean": statistics.fmean(recent_5), "recent_5_count": len(recent_5),
        "recent_10_mean": statistics.fmean(recent_10), "recent_10_count": len(recent_10),
        "trend": trend, "series": copy.deepcopy(series),
    }


def build_time_slices(
    series: list[dict[str, Any]], size: int = SLICE_SIZE
) -> list[dict[str, Any]]:
    """Partition sorted mature RankIC points into consecutive time slices."""
    if isinstance(size, bool) or not isinstance(size, int) or size != SLICE_SIZE:
        raise ValueError("slice size must be 5")
    if not isinstance(series, list):
        raise ValueError("series must be a list")
    signal_dates: list[str] = []
    outcome_dates: list[str] = []
    for index, point in enumerate(series):
        if not isinstance(point, dict):
            raise ValueError(f"point {index} must be an object")
        if set(point) != {"signal_date", "date", "value"}:
            raise ValueError(
                f"point {index} must contain exactly signal_date, date, and value"
            )
        signal_date = point["signal_date"]
        outcome_date = point["date"]
        if not _valid_date(signal_date):
            raise ValueError(f"point {index} signal_date must be valid YYYY-MM-DD")
        if not _valid_date(outcome_date):
            raise ValueError(f"point {index} date must be valid YYYY-MM-DD")
        if signal_date >= outcome_date:
            raise ValueError(f"point {index} signal_date must be before date")
        value = point["value"]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise ValueError(f"point {index} value must be a finite number")
        signal_dates.append(signal_date)
        outcome_dates.append(outcome_date)
    if signal_dates != sorted(signal_dates) or len(signal_dates) != len(set(signal_dates)):
        raise ValueError("signal_dates must be unique and strictly ascending")
    if outcome_dates != sorted(outcome_dates) or len(outcome_dates) != len(set(outcome_dates)):
        raise ValueError("dates must be unique and strictly ascending")
    slices = []
    for index, offset in enumerate(range(0, len(series), size)):
        chunk = series[offset : offset + size]
        values = [point["value"] for point in chunk]
        complete = len(chunk) == size
        mean = statistics.fmean(values) if complete else None
        slices.append({
            "index": index,
            "start_date": chunk[0]["date"],
            "end_date": chunk[-1]["date"],
            "signal_start_date": chunk[0]["signal_date"],
            "signal_end_date": chunk[-1]["signal_date"],
            "observations": len(chunk),
            "status": (
                "POSITIVE" if complete and statistics.fmean(values) > 0
                else "NON_POSITIVE" if complete
                else "INSUFFICIENT"
            ),
            "mean": mean,
            "positive_rate": (
                sum(value > 0 for value in values) / len(values) if complete else None
            ),
        })
    return slices


def build_walk_forward(t5_horizon: dict[str, Any]) -> dict[str, Any]:
    """Build the T+5 non-overlapping time-slice credibility audit."""
    slices = build_time_slices(t5_horizon["series"])
    evaluated = [item for item in slices if item["status"] != "INSUFFICIENT"]
    positive = sum(item["status"] == "POSITIVE" for item in evaluated)
    positive_slice_rate = positive / len(evaluated) if evaluated else None
    observations = t5_horizon["observations"]
    status = rate_time_slice_audit(
        observations,
        positive_slice_rate,
        t5_horizon["icir"],
        HORIZON_THRESHOLDS["t5"][0],
    )
    mature = observations >= HORIZON_THRESHOLDS["t5"][0]
    reason = (
        f"{positive} of {len(evaluated)} evaluated T+5 slices have positive mean RankIC"
        if mature
        else f"T+5 requires 20 observations; {observations} available"
    )
    return {
        "status": status,
        "windows": len(slices),
        "evaluated_windows": len(evaluated),
        "positive_windows": positive,
        "positive_slice_rate": positive_slice_rate,
        "score": positive_slice_rate if mature else None,
        "slice_size": SLICE_SIZE,
        "horizon": "t5",
        "slices": slices,
        "reasons": [reason],
    }


PORTFOLIOS = ("benchmark", "timing", "rotation", "fusion")
COST_SCENARIOS = (0.0, 0.0005, 0.001, 0.002, 0.003)
COST_UNAVAILABLE_REASON = "turnover history unavailable; exact cost scenarios require persisted turnover"


def _finite_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label} must be finite")
    return float(value)


def extract_shadow_history(shadow: Any) -> list[dict[str, Any]]:
    if not isinstance(shadow, dict):
        raise ValueError("shadow must be an object")
    initial = _finite_number(shadow.get("initial_capital_usd"), "shadow initial_capital_usd")
    if initial <= 0:
        raise ValueError("shadow initial_capital_usd must be positive")
    cost = _finite_number(shadow.get("one_way_cost"), "shadow one_way_cost")
    if cost < 0:
        raise ValueError("shadow one_way_cost must be non-negative")
    history = shadow.get("history")
    if not isinstance(history, list):
        raise ValueError("shadow history must be a list")
    periods = []
    seen = {name: set() for name in ("signal_date", "entry_date", "exit_date")}
    for index, row in enumerate(history):
        if not isinstance(row, dict):
            raise ValueError(f"shadow history {index} must be an object")
        dates = {}
        for field in seen:
            value = row.get(field)
            if not _valid_date(value):
                raise ValueError(f"shadow history {index} {field} must be valid YYYY-MM-DD")
            if value in seen[field]:
                raise ValueError(f"duplicate shadow {field}: {value}")
            seen[field].add(value)
            dates[field] = value
        if not dates["signal_date"] < dates["entry_date"] < dates["exit_date"]:
            raise ValueError(f"shadow history {index} must satisfy signal_date < entry_date < exit_date")
        returns = row.get("returns")
        if not isinstance(returns, dict) or set(returns) != set(PORTFOLIOS):
            raise ValueError(f"shadow history {index} returns must contain exactly benchmark, timing, rotation, fusion")
        normalized_returns = {}
        for name in PORTFOLIOS:
            value = _finite_number(returns[name], f"shadow history {index} returns.{name}")
            if value <= -1:
                raise ValueError(f"shadow history {index} returns.{name} must be greater than -1")
            normalized_returns[name] = value
        periods.append({**dates, "date": dates["exit_date"], "returns": normalized_returns})
    periods.sort(key=lambda row: row["exit_date"])
    return periods


def portfolio_health_metrics(periods: list[dict[str, Any]], initial_capital: float, portfolio: str) -> dict[str, Any]:
    equity = initial_capital
    returns = []
    series = []
    peak = initial_capital
    longest = duration = 0
    for period in periods:
        value = period["returns"][portfolio]
        returns.append(value)
        equity *= 1 + value
        series.append({"date": period["exit_date"], "equity": equity})
        if equity >= peak:
            peak = equity
            duration = 0
        else:
            duration += 1
            longest = max(longest, duration)
    observations = len(returns)
    base = {
        "status": "ACCUMULATING", "observations": observations,
        "total_return": None, "annualized_volatility": None, "max_drawdown": None,
        "current_drawdown": None, "longest_drawdown_duration": None,
        "rolling_20d_volatility": None, "positive_period_rate": None,
        "excess_return_vs_benchmark": None, "equity_series": series,
    }
    if observations < 20:
        return base
    total_return = equity / initial_capital - 1
    peak_value = initial_capital
    worst = current = 0.0
    for point in series:
        peak_value = max(peak_value, point["equity"])
        current = 1 - point["equity"] / peak_value
        worst = max(worst, current)
    positive_rate = sum(value > 0 for value in returns) / observations
    return {
        **base, "status": rate_shadow_health(observations, total_return, worst, positive_rate),
        "total_return": total_return, "annualized_volatility": annualized_volatility(returns),
        "max_drawdown": worst, "current_drawdown": current,
        "longest_drawdown_duration": longest,
        "rolling_20d_volatility": annualized_volatility(returns[-20:]),
        "positive_period_rate": positive_rate,
    }


def build_shadow_health(shadow: Any) -> dict[str, Any]:
    periods = extract_shadow_history(shadow)
    initial = float(shadow["initial_capital_usd"])
    portfolios = {name: portfolio_health_metrics(periods, initial, name) for name in PORTFOLIOS}
    benchmark_return = portfolios["benchmark"]["total_return"]
    for metrics in portfolios.values():
        if metrics["total_return"] is not None and benchmark_return is not None:
            metrics["excess_return_vs_benchmark"] = metrics["total_return"] - benchmark_return
    fusion = portfolios["fusion"]
    mature = len(periods) >= 20
    score = None
    if mature:
        return_score = max(0.0, min(1.0, (fusion["total_return"] + 0.2) / 0.4))
        score = 0.5 * fusion["positive_period_rate"] + 0.5 * return_score
    return {
        "status": fusion["status"], "observations": len(periods), "initial_capital": initial,
        "return": fusion["total_return"], "max_drawdown": fusion["max_drawdown"],
        "score": score, "portfolios": portfolios,
        "reasons": ([f"shadow requires 20 observations; {len(periods)} available"] if not mature else ["fusion shadow performance rated from persisted net returns"]),
    }


def build_cost_sensitivity(shadow: Any, _shadow_status: str | None = None) -> dict[str, Any]:
    periods = extract_shadow_history(shadow)
    return {
        "status": "UNAVAILABLE", "baseline_cost": float(shadow["one_way_cost"]),
        "observations": len(periods), "break_even_cost": None, "score": None,
        "scenarios": [
            {"one_way_cost": cost, "value": None, "annualized_return": None, "max_drawdown": None}
            for cost in COST_SCENARIOS
        ],
        "reasons": [COST_UNAVAILABLE_REASON],
    }


def build_health_payload(learning: Any, shadow: Any, generated_at: str | None = None) -> dict[str, Any]:
    if not isinstance(learning, dict):
        raise ValueError("learning must be an object")
    snapshots = learning.get("snapshots")
    if not isinstance(snapshots, list) or not snapshots:
        raise ValueError("learning snapshots must be a non-empty array")
    dates = []
    seen_dates: set[str] = set()
    for index, snapshot in enumerate(snapshots):
        if not isinstance(snapshot, dict) or not _valid_date(snapshot.get("date")):
            raise ValueError(f"snapshot {index} date must be valid YYYY-MM-DD")
        snapshot_date = snapshot["date"]
        if snapshot_date in seen_dates:
            raise ValueError(f"duplicate snapshot date: {snapshot_date}")
        seen_dates.add(snapshot_date)
        dates.append(snapshot_date)
    fingerprint = consistent_model_fingerprint(learning, shadow)
    horizons = {
        name: horizon_health(extract_rank_ic_series(learning, name), *thresholds)
        for name, thresholds in HORIZON_THRESHOLDS.items()
    }
    t5 = horizons["t5"]
    mature = t5["observations"] >= 20
    walk_forward = build_walk_forward(t5)
    governing_status = walk_forward["status"]
    maturity_reason = (
        f"T+5 reached the 20-observation research gate; status is {governing_status}"
        if mature else f"T+5 requires 20 observations; {t5['observations']} available"
    )
    overall_score = walk_forward["score"] if mature else None
    shadow_health = build_shadow_health(shadow)
    cost_sensitivity = build_cost_sensitivity(shadow, shadow_health["status"])
    payload = {
        "schema_version": "us-compass-health-v1", "market": "US",
        "model_date": max(dates), "generated_at": _utc_timestamp(generated_at),
        "model_fingerprint": fingerprint,
        "sample_maturity": {
            "status": governing_status, "observations": t5["observations"],
            "minimum_observations": 20, "mature": mature, "reasons": [maturity_reason],
        },
        "horizons": horizons,
        "walk_forward": walk_forward,
        "shadow_health": shadow_health,
        "cost_sensitivity": cost_sensitivity,
        "overall": {"status": governing_status, "score": overall_score, "reasons": [maturity_reason]},
    }
    return payload


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _read_payload(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {label} payload: {path}") from exc


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--learning", type=Path, default=DEFAULT_LEARNING)
    parser.add_argument("--shadow", type=Path, default=DEFAULT_SHADOW)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--generated-at")
    args = parser.parse_args()
    try:
        learning = _read_payload(args.learning, "learning")
        shadow = _read_payload(args.shadow, "shadow")
        payload = build_health_payload(learning, shadow, args.generated_at)
    except ValueError as exc:
        if args.learning == DEFAULT_LEARNING and args.shadow == DEFAULT_SHADOW and "model_fingerprint" in str(exc):
            raise SystemExit(STAGING_BLOCKER) from None
        raise SystemExit(str(exc)) from None
    atomic_write_json(args.output, payload)
    print(
        f"health generated: model_date={payload['model_date']} "
        f"t1={payload['horizons']['t1']['observations']} "
        f"t5={payload['horizons']['t5']['observations']} "
        f"t20={payload['horizons']['t20']['observations']} output={args.output}"
    )


if __name__ == "__main__":
    main()

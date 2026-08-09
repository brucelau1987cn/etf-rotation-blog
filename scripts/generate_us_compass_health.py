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


def rate_time_slice_audit(
    mature_observations: int,
    positive_slice_rate: float | None,
    t5_icir: float | None,
) -> str:
    """Rate credibility from the governing T+5 time-slice audit."""
    if mature_observations < HORIZON_THRESHOLDS["t5"][0] or positive_slice_rate is None:
        return "ACCUMULATING"
    if positive_slice_rate < 0.5:
        return "FRAGILE"
    if positive_slice_rate < 0.7:
        return "MIXED"
    return "STABLE" if t5_icir is not None and t5_icir > 0 else "MIXED"


def build_walk_forward(t5_horizon: dict[str, Any]) -> dict[str, Any]:
    """Build the T+5 non-overlapping time-slice credibility audit."""
    slices = build_time_slices(t5_horizon["series"])
    evaluated = [item for item in slices if item["status"] != "INSUFFICIENT"]
    positive = sum(item["status"] == "POSITIVE" for item in evaluated)
    positive_slice_rate = positive / len(evaluated) if evaluated else None
    observations = t5_horizon["observations"]
    status = rate_time_slice_audit(observations, positive_slice_rate, t5_horizon["icir"])
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
        "shadow_health": {"status": "ACCUMULATING", "observations": 0, "return": None, "max_drawdown": None, "score": None, "reasons": ["not evaluated in Task 5"]},
        "cost_sensitivity": {"status": "ACCUMULATING", "scenarios": [], "score": None, "reasons": ["not evaluated in Task 5"]},
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

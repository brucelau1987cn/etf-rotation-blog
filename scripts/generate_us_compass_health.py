"""Generate sample-maturity and rolling RankIC health for the US ETF Compass."""
from __future__ import annotations

import argparse
import copy
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
FINGERPRINT_FIELDS = {
    "model_version", "universe_count", "symbols_sha256", "config_sha256",
    "execution_basis", "one_way_cost", "initial_capital", "horizons", "exposure_mapping",
}


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
    """Return valid matured RankIC points sorted by frozen snapshot date."""
    if not isinstance(learning, dict):
        raise ValueError("learning must be an object")
    snapshots = learning.get("snapshots")
    if not isinstance(snapshots, list):
        raise ValueError("learning snapshots must be an array")
    points: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, snapshot in enumerate(snapshots):
        if not isinstance(snapshot, dict):
            raise ValueError(f"snapshot {index} must be an object")
        raw_date = snapshot.get("date")
        if not _valid_date(raw_date):
            raise ValueError(f"snapshot {index} date must be valid YYYY-MM-DD")
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
        value = outcome.get("rank_ic")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"snapshot {raw_date} {horizon} rank_ic must be finite")
        if raw_date in seen:
            raise ValueError(f"duplicate {horizon} rank_ic date: {raw_date}")
        seen.add(raw_date)
        points.append({"date": raw_date, "value": float(value)})
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
        "recent_10_mean": None, "recent_10_count": 0, "trend": None, "series": [],
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


def _validate_fingerprint(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != FINGERPRINT_FIELDS:
        raise ValueError("model_fingerprint is missing or malformed")
    if not isinstance(value.get("model_version"), str) or not value["model_version"].strip():
        raise ValueError("model_fingerprint model_version is invalid")
    if not isinstance(value.get("universe_count"), int) or isinstance(value["universe_count"], bool) or value["universe_count"] < 1:
        raise ValueError("model_fingerprint universe_count is invalid")
    for field in ("symbols_sha256", "config_sha256"):
        digest = value.get(field)
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError(f"model_fingerprint {field} is invalid")
    if not isinstance(value.get("execution_basis"), str) or not value["execution_basis"].strip():
        raise ValueError("model_fingerprint execution_basis is invalid")
    for field in ("one_way_cost", "initial_capital"):
        number = value.get(field)
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number):
            raise ValueError(f"model_fingerprint {field} is invalid")
    if value["one_way_cost"] < 0 or value["initial_capital"] <= 0:
        raise ValueError("model_fingerprint numeric assumptions are invalid")
    horizons = value.get("horizons")
    if not isinstance(horizons, list) or not horizons or len(set(horizons)) != len(horizons) or any(isinstance(v, bool) or not isinstance(v, int) or v < 1 for v in horizons):
        raise ValueError("model_fingerprint horizons are invalid")
    exposure = value.get("exposure_mapping")
    values = exposure.get("values") if isinstance(exposure, dict) else None
    default = exposure.get("default") if isinstance(exposure, dict) else None
    if set(exposure or {}) != {"values", "default"} or not isinstance(values, dict) or not values:
        raise ValueError("model_fingerprint exposure_mapping is invalid")
    for key, number in {**values, "__default__": default}.items():
        if (key != "__default__" and (not isinstance(key, str) or not key.strip())) or isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) or not 0 <= number <= 1:
            raise ValueError("model_fingerprint exposure_mapping is invalid")
    return copy.deepcopy(value)


def _overall_score(t5: dict[str, Any]) -> float:
    positive_rate = float(t5["positive_rate"])
    mean = float(t5["rank_ic_mean"])
    mean_component = max(0.0, min(1.0, (mean + 0.2) / 0.4))
    return max(0.0, min(1.0, 0.5 * positive_rate + 0.5 * mean_component))


def build_health_payload(learning: Any, generated_at: str | None = None) -> dict[str, Any]:
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
    fingerprint = _validate_fingerprint(learning.get("model_fingerprint"))
    horizons = {
        name: horizon_health(extract_rank_ic_series(learning, name), *thresholds)
        for name, thresholds in HORIZON_THRESHOLDS.items()
    }
    t5 = horizons["t5"]
    mature = t5["observations"] >= 20
    governing_status = t5["status"] if mature else "ACCUMULATING"
    maturity_reason = (
        f"T+5 reached the 20-observation research gate; status is {governing_status}"
        if mature else f"T+5 requires 20 observations; {t5['observations']} available"
    )
    overall_score = _overall_score(t5) if mature else None
    payload = {
        "schema_version": "us-compass-health-v1", "market": "US",
        "model_date": max(dates), "generated_at": _utc_timestamp(generated_at),
        "model_fingerprint": fingerprint,
        "sample_maturity": {
            "status": governing_status, "observations": t5["observations"],
            "minimum_observations": 20, "mature": mature, "reasons": [maturity_reason],
        },
        "horizons": horizons,
        "walk_forward": {"status": "ACCUMULATING", "windows": 0, "score": None, "reasons": ["not evaluated in Task 5"]},
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--learning", type=Path, default=DEFAULT_LEARNING)
    parser.add_argument("--shadow", type=Path, default=DEFAULT_SHADOW, help="reserved for future shadow health")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--generated-at")
    args = parser.parse_args()
    try:
        learning = json.loads(args.learning.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read learning payload: {args.learning}") from exc
    payload = build_health_payload(learning, args.generated_at)
    atomic_write_json(args.output, payload)
    print(
        f"health generated: model_date={payload['model_date']} "
        f"t1={payload['horizons']['t1']['observations']} "
        f"t5={payload['horizons']['t5']['observations']} "
        f"t20={payload['horizons']['t20']['observations']} output={args.output}"
    )


if __name__ == "__main__":
    main()

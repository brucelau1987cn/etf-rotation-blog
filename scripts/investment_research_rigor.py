#!/usr/bin/env python3
"""Exact financial checks for the ETF Compass research sidecar."""
from __future__ import annotations

import argparse
import json
from decimal import Decimal, Context, ROUND_HALF_EVEN
from typing import Any

CTX = Context(prec=28, rounding=ROUND_HALF_EVEN)


def dec(value: Any) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def pct_diff(left: Decimal, right: Decimal) -> Decimal:
    if right == 0:
        return Decimal(0) if left == 0 else Decimal("Infinity")
    return abs(left - right) / abs(right) * 100


def verify_market_cap(price: Any, shares: Any, reported: Any, tolerance: Any = 1) -> dict[str, Any]:
    calculated = CTX.multiply(dec(price), dec(shares))
    deviation = pct_diff(calculated, dec(reported))
    return {
        "check": "market_cap",
        "calculated": str(calculated),
        "reported": str(dec(reported)),
        "deviation_pct": None if deviation.is_infinite() else float(deviation),
        "status": "pass" if deviation <= dec(tolerance) else "fail",
    }


def cross_validate(values: dict[str, Any], tolerance: Any = 1) -> dict[str, Any]:
    if len(values) < 2:
        raise ValueError("cross-validation requires at least two independent sources")
    normalized = {key: dec(value) for key, value in values.items()}
    ordered = sorted(normalized.values())
    size = len(ordered)
    reference = ordered[size // 2] if size % 2 else CTX.divide(ordered[size // 2 - 1] + ordered[size // 2], 2)
    deviations = {key: pct_diff(value, reference) for key, value in normalized.items()}
    worst = max(deviations.values())
    return {
        "check": "cross_validate",
        "reference": str(reference),
        "sources": {key: str(value) for key, value in normalized.items()},
        "deviation_pct": {key: None if value.is_infinite() else float(value) for key, value in deviations.items()},
        "status": "pass" if worst <= dec(tolerance) else "fail",
    }


def scenario(price: Any, eps: Any, growth: list[Any], pe: list[Any], years: int = 3) -> dict[str, Any]:
    if len(growth) != 3 or len(pe) != 3:
        raise ValueError("scenario requires three growth and three PE values")
    labels = ("bull", "base", "bear")
    current = dec(price)
    outputs = []
    for label, raw_growth, raw_pe in zip(labels, growth, pe):
        future_eps = dec(eps)
        rate = dec(raw_growth)
        for _ in range(years):
            future_eps = CTX.multiply(future_eps, Decimal(1) + rate)
        target = CTX.multiply(future_eps, dec(raw_pe))
        change = CTX.divide(target - current, current) * 100
        outputs.append({
            "case": label,
            "growth": str(rate),
            "target_pe": str(dec(raw_pe)),
            "future_eps": str(future_eps),
            "target_price": str(target),
            "return_pct": float(change),
        })
    return {"check": "three_scenario", "years": years, "current_price": str(current), "scenarios": outputs}


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    market_cap = sub.add_parser("market-cap")
    market_cap.add_argument("--price", required=True)
    market_cap.add_argument("--shares", required=True)
    market_cap.add_argument("--reported", required=True)
    market_cap.add_argument("--tolerance", default="1")
    cross = sub.add_parser("cross-validate")
    cross.add_argument("--values", required=True, help="JSON object: source -> value")
    cross.add_argument("--tolerance", default="1")
    scenarios = sub.add_parser("scenario")
    scenarios.add_argument("--price", required=True)
    scenarios.add_argument("--eps", required=True)
    scenarios.add_argument("--growth", nargs=3, required=True)
    scenarios.add_argument("--pe", nargs=3, required=True)
    scenarios.add_argument("--years", type=int, default=3)
    args = parser.parse_args()
    if args.command == "market-cap":
        result = verify_market_cap(args.price, args.shares, args.reported, args.tolerance)
    elif args.command == "cross-validate":
        result = cross_validate(json.loads(args.values), args.tolerance)
    else:
        result = scenario(args.price, args.eps, args.growth, args.pe, args.years)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status", "pass") == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())

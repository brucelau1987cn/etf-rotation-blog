#!/usr/bin/env python3
"""Backfill missed paper-trading nav history rows (data-integrity fix).

- A account: 2026-08-07 (flat account, close skipped due to dirty worktree).
- US account: 2026-08-05 and 2026-08-06 (close stalled by the 1h quote-age cap,
  fixed 2026-08-08 but never backfilled). Recomputes 08-07 daily_return so it is
  single-day (was compounded over the missing days).

Deterministic: positions/cash reconstructed from the recorded trade events, closing
prices from Yahoo daily chart. Values are inserted with the runner's exact rounding.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from paper_trade_runner import (  # noqa: E402
    DEFAULT_STATE,
    EXPORT,
    atomic_write,
    build_public_snapshot,
    load,
)

# Yahoo daily closes (ET) for the symbols held across the missing days.
CLOSES = {
    "2026-08-05": {"ETHA": 14.48, "OIH": 385.06, "VNQ": 98.92, "XLP": 85.33,
                   "XLRE": 45.20, "XLV": 164.16, "IBIT": 36.74, "XLE": 57.31},
    "2026-08-06": {"ETHA": 14.40, "OIH": 392.08, "VNQ": 98.04, "XLP": 85.11,
                   "XLRE": 44.81, "XLV": 164.45, "IBIT": 36.49, "XLE": 58.16},
}
# Positions held at 08-05 close and 08-06 close (no trades on 08-06).
POSITIONS = {"ETHA": 144, "OIH": 5, "VNQ": 20, "XLP": 23,
             "XLRE": 44, "XLV": 12, "IBIT": 55, "XLE": 35}
# Cash at 08-05 close and 08-06 close (reconstructed from trade events).
CASH = 4370.098826
INITIAL = 20000.0


def build_us_row(day: str) -> dict:
    pv = sum(POSITIONS[s] * CLOSES[day][s] for s in POSITIONS)
    equity = round(CASH + pv, 6)
    return {
        "date": day,
        "equity": equity,
        "cash": round(CASH, 6),
        "daily_return": 0.0,  # filled after prior equity is known
        "cumulative_return": round(equity / INITIAL - 1, 8),
        "max_drawdown": 0.0,
    }


def main() -> int:
    state = load(DEFAULT_STATE)

    # --- A account: flat 08-07 row ---
    a_hist = state["accounts"]["A"].setdefault("history", [])
    if not any(r["date"] == "2026-08-07" for r in a_hist):
        a_hist.append({"date": "2026-08-07", "equity": 150000.0, "cash": 150000.0,
                       "daily_return": 0.0, "cumulative_return": 0.0, "max_drawdown": 0.0})
    a_hist.sort(key=lambda r: r["date"])

    # --- US account: 08-05 and 08-06 ---
    us = state["accounts"]["US"]
    us_hist = us.setdefault("history", [])

    rows = {"2026-08-05": build_us_row("2026-08-05"),
            "2026-08-06": build_us_row("2026-08-06")}
    existing = {r["date"]: r for r in us_hist}
    merged = dict(existing)
    for day, row in rows.items():
        merged[day] = row

    # Recompute daily_return for the backfilled rows and any downstream rows that
    # now have a different immediately-preceding row.
    ordered = sorted(merged.values(), key=lambda r: r["date"])
    for i, row in enumerate(ordered):
        prior_equity = ordered[i - 1]["equity"] if i > 0 else INITIAL
        row["daily_return"] = round(row["equity"] / prior_equity - 1, 8)

    us["history"] = ordered

    atomic_write(DEFAULT_STATE, state)
    atomic_write(EXPORT, build_public_snapshot(state))

    print("A history dates:", [r["date"] for r in state["accounts"]["A"]["history"]])
    print("US history:")
    for r in state["accounts"]["US"]["history"]:
        print("  ", r["date"], "equity=%.6f" % r["equity"],
              "daily=%.8f" % r["daily_return"], "cum=%.8f" % r["cumulative_return"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

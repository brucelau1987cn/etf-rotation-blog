#!/usr/bin/env python3
"""Generate the public 韩精准制导 signal dataset for the A-share compass page.

Reads the local qfq cache (same source the pool generator uses), runs the
Pine-faithful 韩精准制导 replicate for every formal-pool ETF, and writes a
compact, versioned public payload.

Contract:
- One row per formal-pool code, keyed by 6-digit code.
- Every field is derived from qfq daily bars only; no prose is copied.
- Missing / insufficient data produces an explicit `unavailable` row, never a
  silent zero.
- Output contains no HTML delimiters and no private paths, because the public
  data contract validator rejects both.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import han_precise as hp  # noqa: E402

DB = ROOT / "data/local/etf-compass.db"
POOL = ROOT / "public/data/etf-garden-pool.json"
OUT_JSON = ROOT / "public/data/a-han-precise-signals.json"
SCHEMA_VERSION = "a-han-precise-signals-v1"
CONTRACT_URL = "/schemas/a-han-precise-signals.schema.json"

# Minimum bars the Pine replicate needs: MA89 plus a few bars of runway.
MIN_BARS = 120
# Recent-signal window published to the page.
SIGNAL_LOOKBACK_DAYS = 10
# History depth read from the cache. The Pine replicate needs MA89 plus runway;
# reading deeper keeps the warm-up identical to the pool's own MA20/MA60 basis.
BAR_HISTORY = 400

# The public contract forbids ASCII angle brackets in any string value.
# These labels are the page-facing equivalents of the Pine wording.
BACKGROUND_LABEL = {
    1: "绿色",
    2: "橙色",
    3: "红色",
    4: "蓝色",
    0: "无背景",
}
STATE_LABEL = {
    1: "做多",
    2: "做空",
    3: "强洗",
    4: "弱升",
    0: "无状态",
}
STATE_COLOR = {
    1: "红色",
    2: "绿色",
    3: "橙色",
    4: "青色",
    0: "灰色",
}

# Signal keys published to the page, in display order.
SIGNAL_ORDER = ("底部B", "买入", "加仓", "强势狙击", "DX买", "高抛低吸", "减仓", "清仓", "DX卖1")


def round_or_none(value: Any, digits: int = 2) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return round(number, digits)


def read_pool_codes() -> list[str]:
    payload = json.loads(POOL.read_text(encoding="utf-8"))
    rows = payload.get("all_rows")
    if not isinstance(rows, list) or not rows:
        raise ValueError("etf-garden-pool all_rows is missing")
    codes = [str(row.get("code")) for row in rows if isinstance(row, dict) and row.get("code")]
    if len(codes) != len(set(codes)):
        raise ValueError("etf-garden-pool contains duplicate codes")
    if not codes:
        raise ValueError("etf-garden-pool has no codes")
    return codes


MARKET_ALIASES = {
    "XSHG": "XSHG",
    "XSHE": "XSHE",
    "sh": "XSHG",
    "sz": "XSHE",
    "SH": "XSHG",
    "SZ": "XSHE",
}


def normalize_market(value: Any) -> str | None:
    """Map a pool market label to the cache's exchange code."""
    if not isinstance(value, str):
        return None
    return MARKET_ALIASES.get(value.strip())


def read_bars(db: sqlite3.Connection, code: str, market: str) -> list[tuple[Any, ...]]:
    """Read qfq daily bars using the project's canonical source-priority dedupe.

    The cache primary key includes `source`, so the same trade date can exist
    once per provider. `etf_bar_cache.get_bars` already resolves that with a
    deterministic point-in-time priority (tencent → baostock → iwencai →
    stock-api) and the OHLC sanity filter; reusing it keeps the 韩精准制导
    bars identical to the ones the pool generator uses.
    """
    from etf_bar_cache import get_bars

    rows = get_bars(db, market, code, "qfq", BAR_HISTORY)
    return [
        (
            row["trade_date"],
            row.get("open"),
            row.get("close"),
            row.get("high"),
            row.get("low"),
            row.get("volume"),
        )
        for row in rows
    ]


def build_row(code: str, name: str, bars: list[tuple[Any, ...]]) -> dict[str, Any]:
    """Compute one public row. Insufficient data yields an explicit unavailable row."""
    if len(bars) < MIN_BARS:
        return {
            "code": code,
            "name": name,
            "status": "unavailable",
            "reason": f"K线不足 {len(bars)}/{MIN_BARS}",
            "bar_count": len(bars),
            "background": "无背景",
            "state": "无状态",
            "state_color": "灰色",
            "signal_date": None,
            "signals": [],
            "key_levels": None,
            "metrics": None,
        }

    import pandas as pd

    frame = pd.DataFrame(
        bars, columns=["date", "open", "close", "high", "low", "volume"]
    )
    result = hp.compute(frame, symbol=code)
    fields = result.fields

    recent = result.recent_signals(days=SIGNAL_LOOKBACK_DAYS)
    published: list[dict[str, Any]] = []
    for item in sorted(recent, key=lambda entry: (entry["date"], entry["name"])):
        published.append({
            "name": str(item["name"]),
            "date": str(item["date"]),
            "close": round_or_none(item.get("close"), 3),
        })

    return {
        "code": code,
        "name": name,
        "status": "ok",
        "reason": None,
        "bar_count": len(bars),
        "background": BACKGROUND_LABEL[int(fields.get("background_id", 0))],
        "state": STATE_LABEL[int(fields.get("state_id", 0))],
        "state_color": STATE_COLOR[int(fields.get("state_id", 0))],
        "signal_date": str(fields.get("date")),
        "signals": published,
        "key_levels": {
            "support": round_or_none(fields.get("support"), 3),
            "pressure": round_or_none(fields.get("pressure"), 3),
            "ema15_support": round_or_none(fields.get("ema_support"), 3),
            "ema15_resistance": round_or_none(fields.get("ema_resistance"), 3),
        },
        "metrics": {
            "close": round_or_none(fields.get("close"), 3),
            "day_change_pct": round_or_none(fields.get("day_change_pct")),
            "ma89": round_or_none(fields.get("ma89"), 3),
            "ma03": round_or_none(fields.get("ma03"), 3),
            "bias05": round_or_none(fields.get("bias05")),
            "bias30": round_or_none(fields.get("bias30")),
            "bb05": round_or_none(fields.get("bb05")),
            "bb10": round_or_none(fields.get("bb10")),
            "bb30": round_or_none(fields.get("bb30")),
            "k": round_or_none(fields.get("k")),
            "d": round_or_none(fields.get("d")),
            "macd": round_or_none(fields.get("macd"), 3),
            "x_trend": round_or_none(fields.get("x_trend")),
            "gdx_qs": round_or_none(fields.get("gdx_qs")),
            "dx": round_or_none(fields.get("dx")),
        },
    }


def build_payload(
    codes: list[str],
    names: dict[str, str],
    markets: dict[str, str],
    db: sqlite3.Connection,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for code in codes:
        market = markets.get(code)
        if market is None:
            raise ValueError(f"pool row {code} has no usable market label")
        bars = read_bars(db, code, market)
        rows.append(build_row(code, names.get(code, code), bars))

    ok_rows = [row for row in rows if row["status"] == "ok"]
    unavailable = [row["code"] for row in rows if row["status"] != "ok"]
    signal_date = max(
        (str(row["signal_date"]) for row in ok_rows if row.get("signal_date")),
        default=None,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "contract_url": CONTRACT_URL,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "model_version": "Han Precise Guidance Pine v6 replicate",
        "source": "local SQLite qfq daily cache",
        "observation_date": signal_date,
        "universe_count": len(rows),
        "valid_count": len(ok_rows),
        "unavailable_codes": unavailable,
        "rows": rows,
    }


def semantic_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: semantic_payload(child)
            for key, child in value.items()
            if key not in {"generated_at"}
        }
    if isinstance(value, list):
        return [semantic_payload(child) for child in value]
    return value


def payload_fingerprint(payload: dict[str, Any]) -> str:
    import hashlib

    canonical = json.dumps(
        semantic_payload(payload), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate 韩精准制导 public signals")
    parser.add_argument("--out", type=Path, default=OUT_JSON)
    parser.add_argument("--print-payload", action="store_true", help="print the payload instead of writing it")
    args = parser.parse_args()

    codes = read_pool_codes()
    pool = json.loads(POOL.read_text(encoding="utf-8"))
    names = {
        str(row.get("code")): str(row.get("name") or row.get("quote_name") or row.get("code"))
        for row in pool.get("all_rows", [])
        if isinstance(row, dict) and row.get("code")
    }
    markets: dict[str, str] = {}
    for row in pool.get("all_rows", []):
        if not isinstance(row, dict) or not row.get("code"):
            continue
        code = str(row["code"])
        market = normalize_market(row.get("market")) or normalize_market(row.get("source_market"))
        if market is None:
            raise ValueError(f"pool row {code} has an unrecognized market label")
        markets[code] = market

    if not DB.exists():
        raise SystemExit(f"missing local qfq cache: {DB}")

    db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    # etf_bar_cache.get_bars maps rows to dicts, which requires Row objects.
    db.row_factory = sqlite3.Row
    try:
        payload = build_payload(codes, names, markets, db)
    finally:
        db.close()

    if args.print_payload:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    atomic_write(args.out, payload)
    print(json.dumps({
        "status": "ok",
        "path": str(args.out.relative_to(ROOT)) if args.out.is_relative_to(ROOT) else str(args.out),
        "rows": len(payload["rows"]),
        "valid": payload["valid_count"],
        "unavailable": len(payload["unavailable_codes"]),
        "observation_date": payload["observation_date"],
        "fingerprint": payload_fingerprint(payload)[:16],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Refresh the ETF low-chip research pool.

Gate (mirrors the A-share low-chip stock pool):
  - 股票型ETF universe from iWenCai
  - 周/月/季线收盘获利 strictly < 1.5% (local re-check, not the server boundary)
  - liquidity floor: 基金规模 >= SCALE_MIN (1亿元)
  - ETF with unknown scale is excluded and listed explicitly

Output: public/data/etf-low-chip-pool.json (research observation only; does not
feed compass weights, positions or actions).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "public" / "data" / "etf-low-chip-pool.json"
# Per-code liquidity cache so a mid-run upstream failure resumes instead of restarting.
# Lives under the scratch dir, never in the repo.
LIQ_CACHE = Path("/root/.hermes/cache/scratch/etf_low_chip_liquidity_cache.json")
IWENCAI = "/root/.hermes/scripts/iwencai-market-query"
CN = timezone(timedelta(hours=8))

PROFIT_THRESHOLD = 0.015  # strict: value < 1.5%
PERIODS = (("week", "周"), ("month", "月"), ("quarter", "季"))
SCALE_MIN = 1e8  # 基金规模 >= 1 亿元
PAGE_SIZE = 100
MAX_PAGES = 30
PERIOD_QUERY = "股票ETF {label}线收盘获利不超过1.5%"


class IwencaiError(RuntimeError):
    pass


TRANSIENT_MARKERS = (
    "外部HTTP服务异常",
    "handshake operation timed out",
    "read operation timed out",
    "-1304",
)
RETRY_DELAYS_S = (10, 30)  # bounded: 3 attempts total for transient upstream errors


def _is_transient(stdout: str) -> bool:
    return any(marker in stdout for marker in TRANSIENT_MARKERS)


def iwencai_call(query: str, page: int = 1, limit: int = PAGE_SIZE) -> dict[str, Any]:
    """One iWenCai query with bounded retry on transient upstream errors only.

    Quota refusals and other errors fail immediately; retrying them would only
    spend more of the per-key allowance.
    """
    attempts = len(RETRY_DELAYS_S) + 1
    for attempt in range(attempts):
        proc = subprocess.run(
            [IWENCAI, "-q", query, "--limit", str(limit), "--page", str(page), "--timeout", "120"],
            capture_output=True, text=True, timeout=200,
        )
        stdout = proc.stdout or ""
        try:
            data = json.loads(stdout or "{}")
        except json.JSONDecodeError as exc:
            if attempt < attempts - 1 and _is_transient(stdout):
                time.sleep(RETRY_DELAYS_S[attempt])
                continue
            raise IwencaiError(f"non-JSON response for {query!r} page {page}: {stdout[:200]}") from exc
        if data.get("success"):
            return data
        if attempt < attempts - 1 and _is_transient(stdout):
            time.sleep(RETRY_DELAYS_S[attempt])
            continue
        raise IwencaiError(f"iWenCai failure for {query!r} page {page}: {stdout[:200]}")
    raise IwencaiError(f"iWenCai exhausted retries for {query!r} page {page}")


def fetch_all(query: str, call: Callable[..., dict[str, Any]] = iwencai_call) -> tuple[list[dict], dict]:
    rows: list[dict] = []
    meta: dict[str, Any] = {}
    for page in range(1, MAX_PAGES + 1):
        data = call(query, page=page)
        if not meta:
            meta = {"code_count": data.get("code_count"), "chunks_info": data.get("chunks_info")}
        chunk = data.get("datas") or []
        rows.extend(chunk)
        if not data.get("has_more") or not chunk:
            break
    return rows, meta


def _float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None  # NaN guard


def profit_field(row: dict, label: str) -> tuple[str, float] | None:
    for key, value in row.items():
        if key.startswith(f"{label}线收盘获利"):
            number = _float(value)
            m = re.search(r"\[(\d{8})\]$", key)
            return (m.group(1) if m else "", number) if number is not None else None
    return None


def base_code(code: str) -> str:
    return str(code).split(".")[0]


def lookup_liquidity(code: str, call: Callable[..., dict[str, Any]] = iwencai_call) -> dict | None:
    """Per-code liquidity lookup. The full suffixed code is required: the bare
    six-digit form returns no 基金规模/成交额 fields for several ETFs."""
    # Comma-separated phrasing is the form that reliably returns the flat fields.
    data = call(f"{code}，基金规模，成交额，近一个月日均成交额，周线收盘获利，月线收盘获利，季线收盘获利", page=1, limit=10)
    for row in data.get("datas") or []:
        if row.get("基金代码") == code:
            return row
    return None


# iWenCai returns one of two field-name schemas depending on query phrasing:
#   flat:   基金规模[D] / 成交额平均值[D1-D2]
#   nested: 基金@基金规模[D] / 基金@区间日均成交额[D1-D2]
SCALE_KEYS = ("基金规模[", "基金@基金规模[")
AMOUNT_KEYS = ("成交额平均值[", "基金@区间日均成交额[")


def _field(row: dict, prefixes: tuple[str, ...]) -> float | None:
    for key, value in row.items():
        if key.startswith(prefixes):
            return _float(value)
    return None


def scale_of(row: dict) -> float | None:
    return _field(row, SCALE_KEYS)


def amount_avg_of(row: dict) -> float | None:
    return _field(row, AMOUNT_KEYS)


def _load_liq_cache(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_liq_cache(path: Path, cache: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def build_payload(
    call: Callable[..., dict[str, Any]] = iwencai_call,
    now: datetime | None = None,
    cache_path: Path | None = None,
) -> dict[str, Any]:
    period_rows: dict[str, list[dict]] = {}
    period_meta: dict[str, dict] = {}
    for key, label in PERIODS:
        rows, meta = fetch_all(PERIOD_QUERY.format(label=label), call=call)
        period_rows[key] = rows
        period_meta[key] = meta

    # Strict local gate per period
    profit_hits: dict[str, dict[str, dict]] = {}
    data_dates: set[str] = set()
    names: dict[str, dict] = {}
    for key, label in PERIODS:
        hits: dict[str, dict] = {}
        for row in period_rows[key]:
            code = str(row.get("基金代码") or "").strip()
            field = profit_field(row, label)
            if not code or field is None:
                continue
            date, value = field
            if date:
                data_dates.add(date)
            if 0 <= value < PROFIT_THRESHOLD:
                hits[code] = {"value": value, "date": date}
                names.setdefault(code, row)
        profit_hits[key] = hits

    if len(data_dates) != 1:
        raise RuntimeError(f"expected one chip data date, got {sorted(data_dates)}")
    data_as_of_raw = data_dates.pop()
    data_as_of = f"{data_as_of_raw[:4]}-{data_as_of_raw[4:6]}-{data_as_of_raw[6:]}"

    candidates = sorted(set().union(*(set(v) for v in profit_hits.values())))

    # Universe size: one unpaginated call; code_count is the full 股票ETF count.
    universe_etf = int(call("股票ETF", page=1, limit=1).get("code_count") or 0)

    # Liquidity: per-code lookup for every candidate. The batch universe query is
    # paginated unstably (duplicate codes, missing rows), so it is not used.
    liquidity_source: dict[str, str] = {}
    unknown: list[str] = []
    rows_out: list[dict] = []
    cache_path = cache_path if cache_path is not None else LIQ_CACHE
    cache = _load_liq_cache(cache_path)
    for code in candidates:
        if code in cache:
            row = cache[code]
        else:
            row = lookup_liquidity(code, call=call)
            cache[code] = row
            _save_liq_cache(cache_path, cache)
        if row is None:
            liquidity_source[code] = "missing"
            unknown.append(code)
            continue
        scale = scale_of(row)
        amount = amount_avg_of(row)
        liquidity_source[code] = "per_code"
        if scale is None:
            unknown.append(code)
            continue
        if scale < SCALE_MIN:
            continue
        base: dict = names.get(code) or row

        def _get_profit(p_key: str, p_label: str) -> float | None:
            if code in profit_hits[p_key]:
                return round(profit_hits[p_key][code]["value"] * 100, 2)
            f = profit_field(row, p_label) or profit_field(base, p_label)
            return round(f[1] * 100, 2) if f is not None else None

        rows_out.append({
            "code": code,
            "name": base.get("基金简称") or row.get("基金简称") or "",
            "short_name": base.get("基金扩位简称") or row.get("基金扩位简称") or "",
            "type": row.get("etf类型二级分类") or base.get("etf类型二级分类") or "",
            "price": _float(base.get("最新收盘价")),
            "change_percent": _float(base.get("最新涨跌幅")),
            "scale_yi": round(scale / 1e8, 2),
            "amount_avg_20d_wan": round(amount / 1e4, 1) if amount is not None else None,
            "profit": {k: _get_profit(k, l) for k, l in PERIODS},
            "periods_hit": [k for k, _ in PERIODS if code in profit_hits[k]],
        })

    rows_out.sort(key=lambda r: (len(r["periods_hit"]) * -1, min(
        [v for v in r["profit"].values() if v is not None] or [999])))

    now = now or datetime.now(CN)
    counts = {
        "universe_etf": universe_etf,
        "period_hits": {k: len(profit_hits[k]) for k, _ in PERIODS},
        "candidates_union": len(candidates),
        "liquidity_unknown": len(unknown),
        "pool": len(rows_out),
        "pool_triple": sum(1 for r in rows_out if len(r["periods_hit"]) == 3),
    }
    return {
        "schema_version": 1,
        "data_as_of": data_as_of,
        "generated_at": now.isoformat(timespec="seconds"),
        "source": "iWenCai",
        "metric": "股票型ETF 周/月/季线收盘获利比例（iWenCai 原生字段，本地严格 <1.5% 复核）",
        "gate": {
            "profit_threshold_pct": PROFIT_THRESHOLD * 100,
            "periods": [k for k, _ in PERIODS],
            "scale_min_yi": SCALE_MIN / 1e8,
        },
        "counts": counts,
        "liquidity_unknown": unknown,
        "liquidity_source": liquidity_source,
        "period_meta": period_meta,
        "rows": rows_out,
        "scope": "研究观察；不改变罗盘权重、仓位或交易动作",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--dry-run", action="store_true", help="print summary, do not write")
    args = parser.parse_args(argv)
    payload = build_payload()
    summary = {k: payload[k] for k in ("data_as_of", "counts", "liquidity_unknown")}
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    if args.dry_run:
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=1)
        handle.write("\n")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

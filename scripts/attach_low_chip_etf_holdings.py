#!/usr/bin/env python3
"""Attach ETF-holding data per low-chip stock via Eastmoney F10 (基金持股).

Data source: Eastmoney F10 shareholder research page
(`PC_HSF10/ShareholderResearch/PageAjax`, field `jjcg` = 基金持股).

This replaced the mx-data (东财妙想) "持有ETF 基金持股统计" query on
2026-10-20: mx-data meters calls per API key per day, so a dormant key
returned code=113 and the stage produced empty holdings while the pipeline
still reported success. The Eastmoney route is unauthenticated and
quota-free, and it carries NETVALUE_RATIO (持仓市值占基金资产净值比) which is
the weight figure the old query exposed.

Failure-soft per stock (a stock may genuinely hold no ETF), but transport
failures are counted and reported so a broken source is never confused with
"no ETF holdings".
Outputs atomic JSON write with separators=(",", ":") + ensure_ascii=False.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "public/data/a-low-chip-stocks.json"

TOP_N_HOLDINGS = 5
sys.path.insert(0, str(Path(__file__).resolve().parent))
from em_data_client import EastmoneyClient, EastmoneyError, get_client  # noqa: E402

_client: EastmoneyClient | None = None


def _get_client() -> EastmoneyClient:
    """Shared Eastmoney client (serial + paced requests)."""
    global _client
    if _client is None:
        _client = get_client()
    return _client


def _is_exchange_listed(fund_code: str, derivecode: str) -> bool:
    """Exchange-listed funds only (.SH / .SZ derive codes).

    Eastmoney's `jjcg` mixes ETF/LOF with ordinary (场外) funds. The old
    mx-data parser kept only `.SH` / `.SZ` codes; keep the same rule.
    """
    return derivecode.endswith(".SH") or derivecode.endswith(".SZ")


def parse_em_etf_holdings(rows: list[dict]) -> list[dict]:
    """Map Eastmoney `jjcg` rows into the existing etf_holdings contract.

    Contract fields kept identical to the mx-data version:
      code / name / full_name / weight_pct / rank / holding_value /
      holding_qty / is_heavy / etf_category_l1 / etf_category_l2 /
      fund_manager
    """
    etf_rows: list[dict] = []
    for row in rows:
        fund_code = str(row.get("fund_code") or "").strip()
        fund_name = str(row.get("fund_name") or "").strip()
        derive = str(row.get("fund_derivecode") or "")
        if not _is_exchange_listed(fund_code, derive):
            continue
        try:
            nav_ratio = float(row.get("nav_ratio"))
        except (TypeError, ValueError):
            nav_ratio = None
        try:
            shares = float(row.get("shares"))
        except (TypeError, ValueError):
            shares = None
        try:
            hold_value = float(row.get("hold_value"))
        except (TypeError, ValueError):
            hold_value = None
        if shares is None and hold_value is None:
            continue
        etf_rows.append(
            {
                "code": derive or fund_code,
                "name": fund_name,
                "full_name": fund_name,
                # 持仓市值占基金资产净值比 (%) — the "weight in the fund" figure.
                "weight_pct": round(nav_ratio, 4) if nav_ratio is not None else None,
                "rank": None,
                "holding_value": (hold_value / 1e8) if hold_value is not None else None,
                "holding_qty": shares,
                "is_heavy": None,
                "etf_category_l1": "ETF",
                "etf_category_l2": None,
                "fund_manager": None,
                "_sort_value": hold_value if hold_value is not None else 0.0,
            }
        )

    etf_rows.sort(key=lambda x: x["_sort_value"], reverse=True)
    for i, row in enumerate(etf_rows, 1):
        row["rank"] = i
        del row["_sort_value"]
    return etf_rows[:TOP_N_HOLDINGS]


def top_category(holdings: list[dict]) -> str | None:
    if not holdings:
        return None
    return "ETF"


def attach_for_one(full_code: str) -> tuple[list[dict], str | None, int, bool]:
    """Returns (holdings, category, raw_count, source_ok)."""
    client = _get_client()
    try:
        rows = client.fund_holdings(full_code)
    except EastmoneyError as exc:
        print(f"[{full_code}] eastmoney fund holdings failed: {exc}", file=sys.stderr)
        return [], None, 0, False
    holdings = parse_em_etf_holdings(rows)
    return holdings, top_category(holdings), len(holdings), True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=str(DATA))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    src = Path(args.input)
    if not src.exists():
        print(f"FATAL: input file not found: {src}", file=sys.stderr)
        return 1
    payload = json.loads(src.read_text(encoding="utf-8"))
    symbols = payload.get("intersection") or []
    enrichments = payload.setdefault("enrichments", {})
    if not symbols:
        print("intersection is empty, nothing to do")
        return 0

    success = 0
    empty = 0
    failed = 0
    # Sequential on purpose: the client paces requests to stay under
    # Eastmoney's throttling thresholds. A parallel fan-out risks 403/empty
    # bodies that look like missing data.
    results = [attach_for_one(symbol) for symbol in symbols]
    for i, (code, (holdings, cat, raw_n, source_ok)) in enumerate(
        zip(symbols, results), 1
    ):
        rec = enrichments.setdefault(code, {})
        rec["etf_holdings"] = holdings
        rec["etf_top_category"] = cat
        if not source_ok:
            failed += 1
            print(f"[{i}/{len(symbols)}] {code}: SOURCE FAILED")
            continue
        if raw_n == 0:
            empty += 1
            print(f"[{i}/{len(symbols)}] {code}: 0 ETFs")
        else:
            success += 1
            top = holdings[0] if holdings else None
            print(f"[{i}/{len(symbols)}] {code}: {raw_n} ETFs, top={top['name'] if top else '-'} ({top['code'] if top else '-'})")

    if not args.dry_run:
        tmp = src.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        tmp.replace(src)
    # 显式降级状态：数据源不可用时不能只报「0 empty」——调用方和发布摘要
    # 需要知道这是数据源挂了，而非「这些股票真的没有持仓 ETF」。
    usage = _get_client().usage()
    degraded = bool(failed) or bool(usage.get("failures"))
    print(json.dumps({
        "status": "degraded" if degraded else "ok",
        "source": "eastmoney-direct",
        "success": success,
        "empty": empty,
        "failed": failed,
        "total": len(symbols),
        "client": usage,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

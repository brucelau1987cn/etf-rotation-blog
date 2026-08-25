#!/usr/bin/env python3
"""Collect FTShare SDK data into an isolated research shadow snapshot."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "public/data/a-low-chip-stocks.json"
DEFAULT_OUTPUT = ROOT / "public/data/ftshare-shadow.json"
SDK_BASE_URL = "https://market.ft.tech/gateway/"
SDK_VERSION = "0.1.1"
CN = ZoneInfo("Asia/Shanghai")


def create_retry_session() -> requests.Session:
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        status=3,
        backoff_factor=1.0,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=frozenset({"GET", "POST"}),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def create_sdk_client(timeout: int):
    from ftshare.client import FtshareClient

    return FtshareClient(base_url=SDK_BASE_URL, timeout=timeout, session=create_retry_session())


def unwrap_sdk_rows(payload: Any) -> list[dict[str, Any]]:
    rows = payload
    if isinstance(payload, dict):
        code = payload.get("code")
        if code not in (None, 0, "0", 200, "200"):
            raise RuntimeError(f"FTShare API error {code}: {payload.get('message')}")
        rows = payload.get("data")
        if isinstance(rows, dict):
            if isinstance(rows.get("records"), list):
                rows = rows["records"]
            elif isinstance(rows.get("items"), list):
                rows = rows["items"]
        if rows is None and isinstance(payload.get("items"), list):
            rows = payload["items"]
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise RuntimeError("FTShare SDK returned invalid row payload")
    return rows


def unwrap_paginated_sdk_pages(payloads: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    pages = payloads if isinstance(payloads, list) else [payloads]
    if not pages or any(not isinstance(page, dict) for page in pages):
        raise RuntimeError("FTShare SDK returned invalid paginated payload")
    rows: list[dict[str, Any]] = []
    expected_total: int | None = None
    expected_pages: int | None = None
    for page in pages:
        code = page.get("code")
        if code not in (None, 0, "0", 200, "200"):
            raise RuntimeError(f"FTShare API error {code}: {page.get('message')}")
        data = page.get("data")
        if not isinstance(data, dict):
            raise RuntimeError("FTShare SDK returned invalid paginated data")
        page_rows = data.get("records") if isinstance(data.get("records"), list) else data.get("items")
        if not isinstance(page_rows, list) or any(not isinstance(row, dict) for row in page_rows):
            raise RuntimeError("FTShare SDK returned invalid paginated rows")
        rows.extend(page_rows)
        if expected_total is None and data.get("total") is not None:
            expected_total = int(data["total"])
        if expected_total is None and data.get("total_items") is not None:
            expected_total = int(data["total_items"])
        if expected_pages is None and data.get("pages") is not None:
            expected_pages = int(data["pages"])
        if expected_pages is None and data.get("total_pages") is not None:
            expected_pages = int(data["total_pages"])
    fetched_pages = len(pages)
    page_cap_reached = expected_pages is not None and fetched_pages < expected_pages
    count_mismatch = expected_total is not None and len(rows) != expected_total
    quality = {
        "total": expected_total,
        "returned": len(rows),
        "actual": len(rows),
        "pages": expected_pages,
        "fetched_pages": fetched_pages,
        "page_cap_reached": page_cap_reached,
        "truncated": page_cap_reached or count_mismatch,
        "warnings": ["分页未完整拉取"] if page_cap_reached else [],
        "count_mismatch": count_mismatch,
        "complete": not page_cap_reached and not count_mismatch,
    }
    return rows, quality


def rows_quality(rows: list[dict[str, Any]], *, expected: int | None = None) -> dict[str, Any]:
    actual = len(rows)
    mismatch = expected is not None and expected != actual
    return {
        "total": actual if expected is None else expected,
        "returned": actual,
        "actual": actual,
        "truncated": False,
        "warnings": [],
        "count_mismatch": mismatch,
        "complete": not mismatch,
    }


def latest_row(rows: list[dict[str, Any]], *date_fields: str) -> dict[str, Any] | None:
    if not rows:
        return None
    return max(rows, key=lambda row: tuple(str(row.get(field) or "") for field in date_fields))


def number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def compare_low_chip(source: dict[str, Any], collected: dict[str, Any]) -> dict[str, Any]:
    comparisons: dict[str, Any] = {}
    for symbol, item in (collected.get("items") or {}).items():
        source_metrics = ((source.get("enrichments") or {}).get(symbol) or {}).get("shareholder_metrics") or {}
        holder = item.get("holder_latest") or {}
        float_holder = item.get("float_holder_latest") or {}
        if not source_metrics or not holder:
            continue
        source_count = number(source_metrics.get("shareholder_count"))
        provider_count = number(holder.get("holder_num"))
        source_change = number(source_metrics.get("shareholder_change_pct"))
        provider_change = number(holder.get("holder_num_change_ratio"))
        source_top10 = number(source_metrics.get("top10_float_ratio"))
        provider_top10 = number(float_holder.get("share_holding"))
        if provider_top10 is None:
            provider_top10 = number(holder.get("ften_holder_ratio"))
        comparisons[symbol] = {
            "source_report_period": source_metrics.get("report_period"),
            "ftshare_report_period": holder.get("report_date"),
            "report_period_match": bool(source_metrics.get("report_period"))
            and str(source_metrics.get("report_period")) == str(holder.get("report_date")),
            "holder_count_delta": round(provider_count - source_count, 4)
            if provider_count is not None and source_count is not None
            else None,
            "holder_change_pct_delta": round(provider_change - source_change, 4)
            if provider_change is not None and source_change is not None
            else None,
            "top10_float_ratio_delta": round(provider_top10 - source_top10, 4)
            if provider_top10 is not None and source_top10 is not None
            else None,
        }
    return {"compared": len(comparisons), "items": comparisons}


def count_low_chip_incomplete(collected: dict[str, Any]) -> int:
    count = 0
    for item in (collected.get("items") or {}).values():
        quality = item.get("quality") or {}
        for key in ("holder", "float_holder"):
            section = quality.get(key)
            if isinstance(section, dict) and not section.get("complete", False):
                count += 1
    return count


def error_dict(exc: Exception) -> dict[str, Any]:
    return {
        "code": type(exc).__name__,
        "message": str(exc),
        "retryable": type(exc).__name__ in {"ConnectionError", "ConnectTimeout", "ReadTimeout", "Timeout"},
    }


def collect_low_chip(client: Any, symbols: list[str], sleep_seconds: float = 0.15) -> dict[str, Any]:
    items: dict[str, Any] = {}
    errors: dict[str, Any] = {}
    holder_success = 0
    float_success = 0
    for index, symbol in enumerate(symbols):
        item: dict[str, Any] = {"symbol": symbol, "quality": {}}
        symbol_errors: dict[str, Any] = {}
        try:
            payloads = client.stock_holders_number(
                stock_code=symbol,
                all_pages=True,
                page_size=200,
                max_pages=100,
                raw=True,
            )
            rows, quality = unwrap_paginated_sdk_pages(payloads)
            item["holder_latest"] = latest_row(rows, "publish_date", "report_date")
            item["holder_history_count"] = len(rows)
            item["quality"]["holder"] = quality
            holder_success += 1
        except Exception as exc:  # noqa: BLE001
            symbol_errors["holders"] = error_dict(exc)
        if sleep_seconds:
            time.sleep(sleep_seconds)
        try:
            payloads = client.stock_float_holders(
                stock_code=symbol,
                all_pages=True,
                page_size=200,
                max_pages=100,
                raw=True,
            )
            rows, quality = unwrap_paginated_sdk_pages(payloads)
            item["float_holder_latest"] = latest_row(rows, "publish_date")
            item["float_holder_history_count"] = len(rows)
            item["quality"]["float_holder"] = quality
            float_success += 1
        except Exception as exc:  # noqa: BLE001
            symbol_errors["float_holders"] = error_dict(exc)
        items[symbol] = item
        if symbol_errors:
            errors[symbol] = symbol_errors
        if sleep_seconds and index + 1 < len(symbols):
            time.sleep(sleep_seconds)
    return {
        "requested": len(symbols),
        "holder_success": holder_success,
        "float_holder_success": float_success,
        "items": items,
        "errors": errors,
    }


def collect_market(client: Any, trade_date: str, auction_page_size: int = 200) -> dict[str, Any]:
    output: dict[str, Any] = {"trade_date": trade_date, "errors": {}}
    calls = {
        "limit_up": lambda: client.limit_up_pool(trade_date=trade_date, raw=True),
        "limit_up_break": lambda: client.limit_up_break_pool(trade_date=trade_date, raw=True),
        "limit_down": lambda: client.limit_down_pool(trade_date=trade_date, raw=True),
        "auction": lambda: client.auction_results(
            trade_date=trade_date,
            all_pages=True,
            page_size=auction_page_size,
            max_pages=100,
            raw=True,
        ),
    }
    for key, call in calls.items():
        try:
            payload = call()
            if key == "auction":
                rows, quality = unwrap_paginated_sdk_pages(payload)
            else:
                rows = unwrap_sdk_rows(payload)
                quality = rows_quality(rows)
            output[key] = {"data": rows, "quality": quality}
        except Exception as exc:  # noqa: BLE001
            output["errors"][key] = error_dict(exc)
    output["summary"] = {
        f"{key}_returned": len(value.get("data") or [])
        for key, value in output.items()
        if key not in {"trade_date", "errors", "summary"} and isinstance(value, dict)
    }
    return output


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--max-symbols", type=int, default=20, help="Bounded canary size; 0 means all symbols")
    parser.add_argument("--trade-date", default="", help="YYYYMMDD; defaults to input data_as_of")
    parser.add_argument("--sleep-seconds", type=float, default=0.15)
    parser.add_argument("--timeout", type=int, default=60)
    args = parser.parse_args()

    source = json.loads(args.input.read_text(encoding="utf-8"))
    symbols = list(source.get("intersection") or [])
    if args.max_symbols > 0:
        symbols = symbols[: args.max_symbols]
    trade_date = args.trade_date or str(source.get("data_as_of") or "").replace("-", "")
    if len(trade_date) != 8 or not trade_date.isdigit():
        raise SystemExit("STAGING BLOCKER: missing valid trade date")

    top_errors: dict[str, Any] = {}
    try:
        client = create_sdk_client(args.timeout)
    except Exception as exc:  # noqa: BLE001
        top_errors["sdk_initialize"] = error_dict(exc)
        low_chip = {
            "requested": len(symbols),
            "holder_success": 0,
            "float_holder_success": 0,
            "items": {},
            "errors": {},
            "comparison": {"compared": 0, "items": {}},
        }
        market = {"trade_date": trade_date, "errors": {}, "summary": {}}
    else:
        try:
            low_chip = collect_low_chip(client, symbols, sleep_seconds=max(0, args.sleep_seconds))
            low_chip["comparison"] = compare_low_chip(source, low_chip)
            market = collect_market(client, trade_date)
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()

    quality_failures = len(low_chip["errors"]) + len(market["errors"])
    market_incomplete = sum(
        1
        for section in (market.get("limit_up"), market.get("limit_up_break"), market.get("limit_down"), market.get("auction"))
        if isinstance(section, dict) and not section.get("quality", {}).get("complete", False)
    )
    low_chip_incomplete = count_low_chip_incomplete(low_chip)
    status = "ok" if quality_failures == 0 and market_incomplete == 0 and low_chip_incomplete == 0 and not top_errors else "degraded"
    payload = {
        "schema_version": "ftshare-shadow-v2",
        "mode": "shadow_research_only",
        "production_change_allowed": False,
        "generated_at": dt.datetime.now(CN).isoformat(timespec="seconds"),
        "status": status,
        "source": {
            "provider": "FTShare",
            "transport": "python-sdk",
            "base_url": SDK_BASE_URL,
            "sdk_version": SDK_VERSION,
            "sdk_commit": "d9aa00d1bc12632d823d5ce76d39cc52a1546cbd",
        },
        "errors": top_errors,
        "quality_summary": {
            "low_chip_incomplete_sections": low_chip_incomplete,
            "market_incomplete_sections": market_incomplete,
        },
        "low_chip": low_chip,
        "market": market,
        "disclaimer": "影子研究数据；不修改生产筛选、权重、仓位或交易动作。",
    }
    atomic_write_json(args.output, payload)
    print(json.dumps({
        "status": status,
        "transport": "python-sdk",
        "trade_date": trade_date,
        "low_chip_requested": low_chip["requested"],
        "holder_success": low_chip["holder_success"],
        "float_holder_success": low_chip["float_holder_success"],
        "low_chip_error_symbols": len(low_chip["errors"]),
        "market_errors": market["errors"],
        "market_summary": market["summary"],
        "output": str(args.output),
    }, ensure_ascii=False))
    return 0 if status == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())

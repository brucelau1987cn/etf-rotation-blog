#!/usr/bin/env python3
"""Attach 一年内最低价 / 三年内最低价（不复权真实成交价）到低筹码 enrichment。

口径（用户 2026-11-08 指定）：
  - 不复权（真实成交价），与当年盘面数字一致；含分红送股的涨幅偏差由
    页面文案提示，不做前复权替换。
  - 「一年内」= data_as_of 往前 365 个自然日；「三年内」= data_as_of 往前
    3 个自然年。
  - 最低价取窗口内每日最低价（low）的最小值，并记录该日日期。
  - 距低点百分比 = (当日价 / 窗口最低价 - 1) * 100，当日价用快照 periods
    行里的 price（同为不复权）。

数据源（按顺序降级）：
  1. 腾讯日K 不复权（ifzq.gtimg.cn fqkline，adjustment 传空 → 返回 day 键）
  2. 同花顺 00 序列（不复权）按年份分段拉取

缺失即 fail-closed：不估算、不用复权价冒充不复权价；窗口未覆盖满时打
coverage_*_complete=false，页面按实际覆盖展示并给出提示。
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
import urllib.request
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "public/data/a-low-chip-stocks.json"

ADJUSTMENT = "none"
TENCENT_COUNT = 900  # ≈ 3.5 年交易日，覆盖三年窗口
REQUEST_INTERVAL_S = 0.12
MAX_RETRIES = 2
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
THS_HEADERS = {**UA, "Referer": "https://stockpage.10jqka.com.cn/"}


def _item(symbol: str) -> dict:
    code, _, market = str(symbol).partition(".")
    market = (market or "SZ").upper()
    return {"code": code, "market": "XSHG" if market == "SH" else "XSHE"}


def _shift_years(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year - years)
    except ValueError:  # 2/29
        return day.replace(year=day.year - years, day=28)


def _normalize(bars: list[dict], as_of: str) -> list[dict]:
    """按日去重升序、裁掉 as_of 之后的行、只保留有限正数价格。"""
    by_date: dict[str, dict] = {}
    for row in bars or []:
        day = str(row.get("trade_date") or "")[:10]
        if not day or day > as_of:
            continue
        try:
            low = float(row.get("low"))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(low) or low <= 0:
            continue
        try:
            close = float(row.get("close"))
        except (TypeError, ValueError):
            close = None
        by_date[day] = {
            "trade_date": day,
            "low": low,
            "close": close if close and math.isfinite(close) and close > 0 else None,
        }
    return [by_date[day] for day in sorted(by_date)]


def fetch_tencent_raw(item: dict, as_of: str) -> tuple[list[dict], str]:
    """腾讯日K 不复权：param 末位留空 → 响应 'day' 键即真实成交价。"""
    symbol = ("sh" if item["market"] == "XSHG" else "sz") + item["code"]
    last_error: Exception | None = None
    for host in ("ifzq.gtimg.cn", "web.ifzq.gtimg.cn"):
        url = f"https://{host}/appstock/app/fqkline/get?param={symbol},day,,,{TENCENT_COUNT},"
        for attempt in range(MAX_RETRIES + 1):
            try:
                req = urllib.request.Request(url, headers=UA)
                with urllib.request.urlopen(req, timeout=20) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                node = (payload.get("data") or {}).get(symbol) or {}
                rows = node.get("day") or []
                bars = [
                    {"trade_date": str(r[0])[:10], "low": r[4], "close": r[2]}
                    for r in rows if isinstance(r, list) and len(r) >= 5
                ]
                if bars:
                    return _normalize(bars, as_of), "tencent-day-unadjusted"
            except Exception as exc:  # noqa: BLE001 - host / retry fallback
                last_error = exc
                if attempt < MAX_RETRIES:
                    time.sleep(0.4 * (attempt + 1))
    if last_error:
        raise last_error
    return [], "tencent-day-unadjusted"


def fetch_ths_raw(item: dict, as_of: str) -> tuple[list[dict], str]:
    """同花顺 00（不复权）按年份分段，合并 /last.js 兜底近 140 根。"""
    code = item["code"]
    last_year = int(str(as_of)[:4])
    bars: dict[str, dict] = {}

    def _absorb(text: str) -> None:
        match = re.search(r"\((.*)\)", text, re.S)
        if not match:
            return
        try:
            payload = json.loads(match.group(1))
        except Exception:  # noqa: BLE001 - 非 JSON 响应视为空
            return
        for entry in str(payload.get("data") or "").split(";"):
            parts = entry.split(",")
            if len(parts) < 5 or len(parts[0]) != 8:
                continue
            raw = parts[0]
            day = f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
            try:
                bars[day] = {"trade_date": day, "low": parts[3], "close": parts[4]}
            except (TypeError, ValueError, IndexError):
                continue

    def _fetch(url: str) -> str:
        for attempt in range(MAX_RETRIES):
            try:
                req = urllib.request.Request(url, headers=THS_HEADERS)
                with urllib.request.urlopen(req, timeout=8) as resp:
                    return resp.read().decode("gbk", errors="ignore")
            except Exception:  # noqa: BLE001 - 单年失败重试一次
                if attempt + 1 < MAX_RETRIES:
                    time.sleep(0.3)
        return ""

    for year in range(last_year - 3, last_year + 1):
        _absorb(_fetch(f"https://d.10jqka.com.cn/v6/line/hs_{code}/00/{year}.js"))
    _absorb(_fetch(f"https://d.10jqka.com.cn/v6/line/hs_{code}/00/last.js"))
    return _normalize(list(bars.values()), as_of), "ths-00-unadjusted"


def load_bars(symbol: str, as_of: str) -> tuple[list[dict], str]:
    item = _item(symbol)
    errors: list[str] = []
    for name, fetch in (("tencent", fetch_tencent_raw), ("ths", fetch_ths_raw)):
        try:
            bars, source = fetch(item, as_of)
        except Exception as exc:  # noqa: BLE001 - 降级到下一源
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
            continue
        if bars:
            return bars, source
        errors.append(f"{name}: empty")
        time.sleep(0.1)
    raise RuntimeError("no unadjusted daily bars: " + "; ".join(errors))


def price_range_metrics(bars: list[dict], as_of: str, price: float | None) -> dict:
    """把日K序列折算成 1年/3年最低价契约。bars 已按日升序且不含未来数据。"""
    if not bars:
        return {"status": "UNAVAILABLE", "reason": "no unadjusted daily bars", "adjustment": ADJUSTMENT, "as_of": as_of}
    anchor = date.fromisoformat(as_of)
    start_1y = anchor - timedelta(days=365)
    start_3y = _shift_years(anchor, 3)
    earliest = date.fromisoformat(bars[0]["trade_date"])

    def window(start: date) -> list[dict]:
        return [row for row in bars if date.fromisoformat(row["trade_date"]) >= start]

    win_1y, win_3y = window(start_1y), window(start_3y)
    if not win_3y:
        return {"status": "UNAVAILABLE", "reason": "window has no bars", "adjustment": ADJUSTMENT, "as_of": as_of}

    def pick(rows: list[dict]) -> dict:
        return min(rows, key=lambda row: row["low"])

    low_1y = pick(win_1y) if win_1y else None
    low_3y = pick(win_3y)
    # 覆盖完整性：首个交易日晚于窗口起点说明上市不足该窗口
    complete_1y = bool(win_1y) and earliest <= start_1y + timedelta(days=7)
    complete_3y = earliest <= start_3y + timedelta(days=7)

    def distance(low_row: dict | None) -> float | None:
        if not low_row or not price or price <= 0:
            return None
        return round((price / low_row["low"] - 1) * 100, 2)

    return {
        "status": "ok",
        "adjustment": ADJUSTMENT,
        "as_of": as_of,
        "price": round(price, 3) if price else None,
        "low_1y": low_1y["low"] if low_1y else None,
        "low_1y_date": low_1y["trade_date"] if low_1y else None,
        "low_3y": low_3y["low"],
        "low_3y_date": low_3y["trade_date"],
        "from_low_1y_pct": distance(low_1y),
        "from_low_3y_pct": distance(low_3y),
        "window_1y_start": start_1y.isoformat(),
        "window_3y_start": start_3y.isoformat(),
        "coverage_start": earliest.isoformat(),
        "coverage_bars": len(bars),
        "window_1y_bars": len(win_1y),
        "window_3y_bars": len(win_3y),
        "coverage_1y_complete": complete_1y,
        "coverage_3y_complete": complete_3y,
    }


def snapshot_price(payload: dict, symbol: str) -> float | None:
    for key in ("week", "month", "quarter"):
        for row in (payload.get("periods") or {}).get(key) or []:
            if row.get("symbol") == symbol and row.get("price") is not None:
                try:
                    value = float(row["price"])
                except (TypeError, ValueError):
                    continue
                if math.isfinite(value) and value > 0:
                    return value
    return None


def attach(payload: dict, *, dry_run: bool = False) -> dict:
    as_of = str(payload.get("data_as_of") or "")[:10]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", as_of):
        raise SystemExit(f"invalid data_as_of in payload: {payload.get('data_as_of')!r}")
    codes = list(payload.get("intersection") or [])
    enrichments = payload.setdefault("enrichments", {})
    covered, failed = 0, []
    for symbol in codes:
        enr = enrichments.setdefault(symbol, {})
        try:
            bars, source = load_bars(symbol, as_of)
            metrics = price_range_metrics(bars, as_of, snapshot_price(payload, symbol))
            metrics["source"] = source
        except Exception as exc:  # noqa: BLE001 - 单只失败隔离
            metrics = {
                "status": "UNAVAILABLE",
                "reason": f"{type(exc).__name__}: {exc}"[:300],
                "adjustment": ADJUSTMENT,
                "as_of": as_of,
            }
            print(f"[{symbol}] price_range failed: {metrics['reason']}", file=sys.stderr, flush=True)
        if metrics.get("status") == "ok":
            covered += 1
        else:
            failed.append(symbol)
        enr["price_range"] = metrics
        time.sleep(REQUEST_INTERVAL_S)

    status = "ok" if not failed else ("blocked" if covered == 0 else "degraded")
    payload["price_range_contract"] = {
        "version": "2026-11-08",
        "as_of": as_of,
        "adjustment": ADJUSTMENT,
        "windows": {"one_year_days": 365, "three_year_years": 3},
        "low_basis": "daily low (最低价)",
        "price_basis": "snapshot periods price (不复权)",
        "requested": len(codes),
        "covered": covered,
        "failed": failed,
        "status": status,
        "note": "不复权真实成交价；含分红送股的区间涨幅与复权口径存在偏差。",
    }
    if not dry_run:
        DATA.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    return payload["price_range_contract"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="计算但不写回文件")
    parser.add_argument("--data-as-of", help="只处理该交易日（校验用）")
    args = parser.parse_args()

    payload = json.loads(DATA.read_text(encoding="utf-8"))
    if args.data_as_of and str(payload.get("data_as_of") or "")[:10] != args.data_as_of:
        raise SystemExit(f"data_as_of mismatch: file={payload.get('data_as_of')} arg={args.data_as_of}")
    if not payload.get("intersection"):
        print(json.dumps({"status": "skipped", "reason": "empty intersection", "requested": 0, "covered": 0}, ensure_ascii=False))
        return 0
    contract = attach(payload, dry_run=args.dry_run)
    print(json.dumps(contract, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

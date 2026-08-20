#!/usr/bin/env python3
"""Track daily price + profit-ratio history for every stock that ever entered the low-chip screen.

Data source:
- Daily OHLCV (qfq): Tencent fqkline API (web.ifzq.gtimg.cn)
- Daily 收盘获利盘 ratio: iWenCai per-stock query `收盘获利[YYYYMMDD]` (historical dates supported)

Output: public/data/low-chip-tracking.json
  {
    "schema_version": "low-chip-tracking-v1",
    "generated_at": "...",
    "stocks": {
      "600363.SH": {
        "name": "联创光电",
        "first_seen": "2026-08-05",   // first date the stock appeared in a low-chip snapshot
        "last_seen": "2026-08-07",
        "industry": "...",
        "daily": [
          {"date": "2026-08-05", "close": 24.64, "change_pct": -10.0, "profit_ratio": 12.2},
          ...
        ],
        "latest": {"week": ..., "month": ..., "quarter": ...}  // current 周/月/季线获利 (if available)
      }
    }
  }
"""
from __future__ import annotations

import datetime
import http.client
import json
import math
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "public/data/low-chip-tracking.json"
HISTORY_DIR = ROOT / "public/data/low-chip-history"
MIN_TRACK_DAYS = 1  # 至少 1 天数据才展示（刚加入当天即开始记录）
MAX_TRACK_BARS = 20  # 加入后统计窗口：最多 20 个交易日；不足按实际天数
MAX_STORED_BARS = MAX_TRACK_BARS + 1  # 加入日基准 + 加入后的 20 个交易日


def load_history_dates() -> dict[str, list[str]]:
    """Map symbol -> sorted list of dates it appeared in low-chip snapshots."""
    seen: dict[str, list[str]] = {}
    for path in sorted(HISTORY_DIR.glob("????-??-??.json")):
        try:
            snap = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        day = path.stem
        for code in snap.get("intersection") or []:
            seen.setdefault(code, []).append(day)
    return seen


def _valid_tencent_row(row: object) -> bool:
    if not isinstance(row, (list, tuple)) or len(row) < 6:
        return False
    try:
        datetime.date.fromisoformat(str(row[0]))
        close = float(row[2])
        return math.isfinite(close) and close > 0
    except (TypeError, ValueError):
        return False


def tencent_daily(
    symbol: str,
    start: str,
    end: str,
    *,
    opener=None,
    sleeper=time.sleep,
) -> list[dict]:
    """Daily qfq bars from Tencent with bounded transient-error retries.

    Pitfall (2026-08-11): Tencent fqkline multi-day range queries with a concrete
    end=YYYY-MM-DD often drop the latest session bar (e.g. start=08-05 end=08-11
    returns only through 08-10; even end=08-12 can still miss 08-11 for some
    symbols). Reliable ways to include today:
      - leave end empty: day,{start},,{count},qfq
      - or request a larger open-ended window and clip client-side
    We query with empty end, then clip to the requested [start, end] window.

    Tencent can also return HTTP 200 with a non-zero business code and
    ``data: ""`` during backend/MySQL failures. Retry those responses instead of
    treating the string as the normal data object.
    """
    code = symbol.split(".")[0]
    ex = "sh" if symbol.endswith(".SH") else "sz"
    # Empty end is required for latest-bar inclusion; count caps payload size.
    # 640 keeps full history since first_seen for any reasonable tracking window.
    url = (
        f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?"
        f"param={ex}{code},day,{start},,640,qfq"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    open_request = opener or urllib.request.urlopen
    retry_delays = (1.0, 2.0, 5.0)
    last_error = "unknown response"
    payload = None
    for attempt in range(len(retry_delays) + 1):
        try:
            with open_request(req, timeout=20) as response:
                decoded = json.loads(response.read())
            business_code = decoded.get("code") if isinstance(decoded, dict) else None
            message = decoded.get("msg") if isinstance(decoded, dict) else "invalid JSON root"
            raw_data = decoded.get("data") if isinstance(decoded, dict) else None
            symbol_data = raw_data.get(f"{ex}{code}") if isinstance(raw_data, dict) else None
            candidate_bars = None
            if isinstance(symbol_data, dict):
                candidate_bars = symbol_data.get("qfqday") or symbol_data.get("day")
            valid_rows = [row for row in candidate_bars if _valid_tencent_row(row)] if isinstance(candidate_bars, list) else []
            in_window_rows = [
                row for row in valid_rows
                if (not start or str(row[0]) >= start) and (not end or str(row[0]) <= end)
            ]
            valid_bars = bool(in_window_rows)
            if business_code == 0 and valid_bars:
                payload = symbol_data
                break
            last_error = (
                f"code={business_code}, msg={message!s}, data_type={type(raw_data).__name__}, "
                f"bars_type={type(candidate_bars).__name__}"
            )
        except (TimeoutError, urllib.error.URLError, json.JSONDecodeError, http.client.HTTPException, OSError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        if attempt < len(retry_delays):
            sleeper(retry_delays[attempt])
    if payload is None:
        raise RuntimeError(f"Tencent fqkline unavailable for {symbol} after 4 attempts: {last_error}")

    kl = payload.get("qfqday") or payload.get("day") or []
    bars = []
    prev_close = None
    for row in kl:
        # [date, open, close, high, low, volume]
        if not _valid_tencent_row(row):
            continue
        date, _, close, _, _, _ = row[:6]
        if start and date < start:
            # Keep prev_close so first in-window change_pct is still correct.
            try:
                prev_close = float(close)
            except (TypeError, ValueError):
                pass
            continue
        if end and date > end:
            continue
        try:
            close_f = float(close)
        except (TypeError, ValueError):
            continue
        chg = None
        if prev_close:
            chg = round((close_f - prev_close) / prev_close * 100, 2)
        bars.append({"date": date, "close": close_f, "change_pct": chg})
        prev_close = close_f
    return bars


def iwencai_profit_ratio(symbol: str, date: str) -> float | None:
    """收盘获利盘 ratio for one symbol at one date (iWenCai historical query)."""
    ymd = date.replace("-", "")
    q = f"{symbol.split('.')[0]} 收盘获利[{ymd}]"
    r = subprocess.run(
        ["/root/.hermes/scripts/iwencai-market-query", "-q", q, "--limit", "2", "--timeout", "45"],
        capture_output=True, text=True, check=False,
    )
    if r.returncode != 0:
        return None
    try:
        d = json.loads(r.stdout)
    except json.JSONDecodeError:
        return None
    for row in d.get("datas") or []:
        if str(row.get("股票代码") or "").split(".")[0] != symbol.split(".")[0]:
            continue
        for k, v in row.items():
            if "收盘获利" in k:
                try:
                    return round(float(v), 2)
                except (TypeError, ValueError):
                    return None
    return None


def load_entry_enrichment(entry_snapshot: Path, symbol: str) -> tuple[dict, dict]:
    """Load immutable join-date evidence; missing/corrupt evidence blocks refresh."""
    try:
        snapshot = json.loads(entry_snapshot.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"missing or invalid join-date snapshot for {symbol}: {entry_snapshot.name}") from exc
    enrichments = snapshot.get("enrichments")
    if not isinstance(enrichments, dict) or symbol not in enrichments:
        raise RuntimeError(f"missing join-date enrichment for {symbol}: {entry_snapshot.name}")
    entry_enrichment = enrichments[symbol]
    if not isinstance(entry_enrichment, dict):
        raise RuntimeError(f"invalid join-date enrichment for {symbol}: {entry_snapshot.name}")
    entry_metrics = entry_enrichment.get("shareholder_metrics") or (snapshot.get("shareholder_metrics") or {}).get(symbol) or {}
    return entry_enrichment, entry_metrics


def load_existing() -> dict:
    if not DATA.exists():
        return {"schema_version": "low-chip-tracking-v1", "generated_at": "", "stocks": {}}
    try:
        payload = json.loads(DATA.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"invalid existing tracking data: {DATA}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("stocks"), dict):
        raise RuntimeError(f"invalid existing tracking data: {DATA}")
    return payload


def normalize_daily(rows: object, first_seen: str) -> list[dict]:
    """Keep unique, finite, join-date-onward stored rows in chronological order."""
    by_date: dict[str, dict] = {}
    if not isinstance(rows, list):
        return []
    for row in rows:
        if not isinstance(row, dict):
            continue
        date = str(row.get("date") or "")
        try:
            datetime.date.fromisoformat(date)
            close = float(row.get("close"))
        except (TypeError, ValueError):
            continue
        if date < first_seen or not math.isfinite(close) or close <= 0:
            continue
        clean = dict(row)
        for key in ("change_pct", "profit_ratio"):
            value = clean.get(key)
            if value is None:
                continue
            try:
                if not math.isfinite(float(value)):
                    clean[key] = None
            except (TypeError, ValueError):
                clean[key] = None
        by_date[date] = clean
    return [by_date[date] for date in sorted(by_date)]


def atomic_write_json(path: Path, payload: dict) -> None:
    """Durably replace a JSON file from a temporary sibling."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        temp_path = None
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def main() -> int:
    seen = load_history_dates()
    existing = load_existing()
    stocks = existing.get("stocks", {})
    today = datetime.date.today().isoformat()

    for symbol, dates in sorted(seen.items()):
        first = min(dates)
        last = max(dates)
        rec = stocks.setdefault(symbol, {"name": "", "first_seen": first, "last_seen": last, "industry": "", "daily": []})
        # 更新 first/last_seen（保留最早加入日）
        rec["first_seen"] = min(rec.get("first_seen") or first, first)
        rec["last_seen"] = last
        rec["daily"] = normalize_daily(rec.get("daily"), rec["first_seen"])

        # 固定追踪窗口：加入日基准 + 加入后的前 20 个交易日；完成后停止请求新数据
        if len(rec.get("daily") or []) >= MAX_STORED_BARS:
            rec["daily"] = sorted(rec["daily"], key=lambda x: x["date"])[:MAX_STORED_BARS]
            rec["tracking_complete"] = True
            continue
        bars = tencent_daily(symbol, rec["first_seen"], today)
        target_bars = bars[:MAX_STORED_BARS]
        if not target_bars:
            # 上游无有效数据，保留已有行，下次重试
            rec["daily"] = sorted(rec.get("daily", []), key=lambda x: x["date"])[:MAX_STORED_BARS]
            rec["tracking_complete"] = len(rec["daily"]) >= MAX_STORED_BARS
            print(f"  {symbol}: tencent returned no bars, preserving {len(rec['daily'])} existing rows", flush=True)
            continue
        have = {d["date"] for d in rec["daily"]}
        new_rows = []
        for bar in target_bars:
            if bar["date"] in have:
                continue
            pr = iwencai_profit_ratio(symbol, bar["date"])
            new_rows.append({**bar, "profit_ratio": pr})
            have.add(bar["date"])
            print(f"  {symbol} {bar['date']}: close={bar['close']} chg={bar['change_pct']} profit={pr}", flush=True)
        rec["daily"].extend(new_rows)
        rec["daily"].sort(key=lambda x: x["date"])
        # 固定首段窗口，禁止滚动成“最近 20 日”而改变加入以来口径
        rec["daily"] = rec["daily"][:MAX_STORED_BARS]
        rec["tracking_complete"] = len(rec["daily"]) >= MAX_STORED_BARS

    # 清理：少于 MIN_TRACK_DAYS 天数的股票（刚加入、数据不足）
    for sym in [s for s, r in stocks.items() if len(r.get("daily", [])) < MIN_TRACK_DAYS]:
        print(f"  drop {sym}: only {len(stocks[sym]['daily'])} bars", flush=True)
        stocks.pop(sym, None)

    # 补名称/行业（从历史快照的 enrichments 或 periods）
    snaps = sorted(HISTORY_DIR.glob("????-??-??.json"), reverse=True)
    name_map: dict[str, str] = {}
    industry_map: dict[str, str] = {}
    for p in snaps:
        try:
            s = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        enr = s.get("enrichments") or {}
        for sym, e in enr.items():
            industry_map.setdefault(sym, e.get("industry", ""))
        for period_rows in s.get("periods", {}).values():
            for row in period_rows:
                name_map.setdefault(row["symbol"], row.get("name", ""))
    for sym, r in stocks.items():
        r["name"] = r.get("name") or name_map.get(sym, "")
        r["industry"] = r.get("industry") or industry_map.get(sym, "")
        entry_snapshot = HISTORY_DIR / f"{r['first_seen']}.json"
        entry_enrichment, entry_metrics = load_entry_enrichment(entry_snapshot, sym)
        r["entry_features"] = {
            "quality_shareholder": bool(entry_enrichment.get("quality_shareholder")),
            "quality_shareholder_names": list(entry_enrichment.get("quality_shareholder_names") or []),
            "institutional_shareholder": bool(entry_enrichment.get("institutional_shareholder")),
            "institutional_shareholder_names": list(entry_enrichment.get("institutional_shareholder_names") or []),
            "chip_focus": entry_metrics.get("chip_focus") or "",
            "main_force": entry_metrics.get("main_force"),
            "main_force_label": entry_metrics.get("main_force_label") or "",
        }
        r["entry_financials"] = dict(entry_enrichment.get("financials") or {})

    atomic_write_json(DATA, {"schema_version": "low-chip-tracking-v1", "generated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"), "stocks": stocks})
    print(json.dumps({"stocks": len(stocks), "total_bars": sum(len(r["daily"]) for r in stocks.values())}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

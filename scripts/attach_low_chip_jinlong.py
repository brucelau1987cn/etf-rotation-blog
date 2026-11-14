#!/usr/bin/env python3
"""金龙（双龙抄底PRO）信号 overlay —— 低筹码流水线 enrichment 阶段。

口径：按《金龙选股指标说明报告》复现 JinLong.txt 的最终布尔输出（化简版）：

    XG = COUNT(选股1,13) >= 1 AND 波段1
    选股1 = FILTER(REF(启动1,1) AND C>REF(C,1) AND MACD.MACD(12,26,9)>-1.5, 10)
    波段1 = FILTER(REF(启动1,1) AND C>REF(C,1) AND VAR2>20 AND GL1, 10)
    启动1 = C/MA(C,40)<0.74 AND (H-L)/REF(C,1)>5% AND COUNT(振幅>5%,5)>=2

行情：同花顺前复权日线（`v6/line/hs_{code}/01/{YYYY}.js`），落 sqlite 缓存
`data/local/jinlong-bars.db`，同一标的跨快照复用，历史回填不再重复拉取。

信号极稀疏（实测 257 只池、近一年仅个位数事件，且集中在全市场深跌日），
所以页面开关用「最近 20 个交易日内出现过 XG」的观察窗，而不是要求当日成立。

写入：
  enrichments[code]["jinlong"] = {
      status, hit, as_of, xg_today, last_hit_date, bars_since_hit,
      rsi7, var2, macd_hist, band1, pick1_window, close, history_bars, source, model
  }
  顶层 jinlong_contract = {status, as_of, source, window, adjustment, coverage, failures}

fail-soft：单只取数失败只标该只 UNAVAILABLE，不阻塞发布；全部失败时契约
status=unavailable，页面显示数据不可用而不是「0 只命中」。

用法：
    python attach_low_chip_jinlong.py [TRADE_DATE]
    TRADE_DATE 缺省时回退 payload 的 data_as_of。
"""
from __future__ import annotations

import json
import sqlite3
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_low_chip_base as base  # noqa: E402  (复用 DATA 路径)
from jinlong_signal import MIN_BARS, signal_at  # noqa: E402

SOURCE = "ths-qfq-daily"
MODEL = "jinlong-shuanglong-chaodi-pro-v1"
WINDOW = 20                 # 观察窗：最近 20 个交易日内出现过 XG
CACHE_DB = ROOT / "data/local/jinlong-bars.db"
BAR_YEARS = ("2025", "2026")
REQUEST_TIMEOUT = 20
USER_AGENT = "Mozilla/5.0"

SCHEMA = """
CREATE TABLE IF NOT EXISTS bars (
  code TEXT NOT NULL,
  trade_date TEXT NOT NULL,
  open REAL, high REAL, low REAL, close REAL,
  source TEXT NOT NULL,
  fetched_at TEXT NOT NULL,
  PRIMARY KEY (code, trade_date)
);
"""


def _connect() -> sqlite3.Connection:
    CACHE_DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(CACHE_DB)
    db.executescript(SCHEMA)
    return db


def fetch_ths_year(code: str, year: str) -> list[dict]:
    """同花顺前复权日线（01=前复权）。每行 11 字段，索引 1-5 是 OHLC。"""
    url = f"http://d.10jqka.com.cn/v6/line/hs_{code}/01/{year}.js"
    request = urllib.request.Request(
        url, headers={"Referer": "https://stockpage.10jqka.com.cn/", "User-Agent": USER_AGENT}
    )
    raw = urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT).read().decode("utf-8", "ignore")
    payload = json.loads(raw[raw.index("(") + 1:raw.rindex(")")])
    rows = []
    for line in str(payload.get("data") or "").split(";"):
        fields = line.split(",")
        if len(fields) < 8 or len(fields[0]) != 8:
            continue
        try:
            rows.append({
                "trade_date": f"{fields[0][:4]}-{fields[0][4:6]}-{fields[0][6:8]}",
                "open": float(fields[1]),
                "high": float(fields[2]),
                "low": float(fields[3]),
                "close": float(fields[4]),
            })
        except ValueError:
            continue
    return rows


def load_cached(db: sqlite3.Connection, code: str) -> list[dict]:
    rows = db.execute(
        "SELECT trade_date, open, high, low, close FROM bars WHERE code=? ORDER BY trade_date",
        (code,),
    ).fetchall()
    return [
        {"trade_date": r[0], "open": r[1], "high": r[2], "low": r[3], "close": r[4]}
        for r in rows
    ]


def cache_bars(db: sqlite3.Connection, code: str, bars: list[dict]) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    db.executemany(
        """INSERT INTO bars(code,trade_date,open,high,low,close,source,fetched_at)
           VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(code,trade_date) DO UPDATE SET
           open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,
           source=excluded.source,fetched_at=excluded.fetched_at""",
        [(code, b["trade_date"], b["open"], b["high"], b["low"], b["close"], SOURCE, now) for b in bars],
    )
    db.commit()


def bars_for(code: str, as_of: str, db: sqlite3.Connection) -> list[dict]:
    """取覆盖 as_of 的前复权日线；缓存不足时补拉同花顺年度文件。"""
    bare = code.split(".")[0]
    cached = load_cached(db, bare)
    if cached and cached[-1]["trade_date"] >= as_of and len(cached) >= MIN_BARS:
        return cached
    merged = {b["trade_date"]: b for b in cached}
    for year in BAR_YEARS:
        try:
            for bar in fetch_ths_year(bare, year):
                merged[bar["trade_date"]] = bar
        except Exception:  # noqa: BLE001 单个年度文件 502 不该废掉整只
            continue
    bars = [merged[day] for day in sorted(merged)]
    if bars:
        cache_bars(db, bare, bars)
    return bars


def attach(payload: dict, trade_date: str, *, bars_loader=None) -> dict:
    """把金龙信号写进 payload 的 enrichments，返回契约。不写文件。"""
    if bars_loader is not None:
        return _attach(payload, trade_date, bars_loader)
    db = _connect()
    try:
        return _attach(payload, trade_date, lambda code: bars_for(code, trade_date, db))
    finally:
        db.close()


def _attach(payload: dict, trade_date: str, loader) -> dict:
    codes = payload.get("intersection") or payload.get("intersection_before_filters") or []
    enrichments = payload.setdefault("enrichments", {})

    if not codes:
        contract = {
            "status": "empty_pool",
            "as_of": trade_date,
            "source": SOURCE,
            "model": MODEL,
            "window": WINDOW,
            "adjustment": "qfq",
            "coverage": {"requested": 0, "ok": 0, "unavailable": 0},
            "failures": [],
        }
        payload["jinlong_contract"] = contract
        return contract

    ok = 0
    failures: list[str] = []
    for code in codes:
        entry = enrichments.setdefault(code, {})
        try:
            bars = loader(code)
        except Exception as exc:  # noqa: BLE001 单只取数失败隔离
            failures.append(f"{code}: {str(exc)[:160]}")
            entry["jinlong"] = _unavailable(trade_date, f"行情取数失败: {str(exc)[:160]}")
            continue
        result = signal_at(bars, trade_date)
        if result.get("status") != "ok":
            failures.append(f"{code}: {result.get('reason')}")
            entry["jinlong"] = _unavailable(trade_date, str(result.get("reason")))
            continue
        since = result["bars_since_hit"]
        entry["jinlong"] = {
            "status": "ok",
            "hit": bool(since is not None and since <= WINDOW),
            "hit_today": result["xg"],
            "as_of": result["as_of"],
            "last_hit_date": result["last_hit_date"],
            "bars_since_hit": since,
            "window": WINDOW,
            "close": round(result["close"], 4),
            "rsi7": _round(result["rsi7"]),
            "var2": _round(result["var2"]),
            "macd_hist": _round(result["macd_hist"], 4),
            "band1": result["band1"],
            "pick1_window": result["pick1_window"],
            "history_bars": result["history_bars"],
            "source": SOURCE,
            "model": MODEL,
        }
        ok += 1

    contract = {
        "status": "ok" if ok else "unavailable",
        "as_of": trade_date,
        "source": SOURCE,
        "model": MODEL,
        "window": WINDOW,
        "adjustment": "qfq",
        "coverage": {"requested": len(codes), "ok": ok, "unavailable": len(codes) - ok},
        "failures": failures[:20],
    }
    payload["jinlong_contract"] = contract
    return contract


def _unavailable(trade_date: str, reason: str) -> dict:
    return {
        "status": "UNAVAILABLE",
        "hit": False,
        "hit_today": False,
        "as_of": trade_date,
        "last_hit_date": None,
        "bars_since_hit": None,
        "window": WINDOW,
        "close": None,
        "rsi7": None,
        "var2": None,
        "macd_hist": None,
        "band1": False,
        "pick1_window": None,
        "history_bars": 0,
        "source": SOURCE,
        "model": MODEL,
        "reason": reason,
    }


def _round(value, digits: int = 2):
    return None if value is None else round(float(value), digits)


def main() -> int:
    payload = json.loads(base.DATA.read_text(encoding="utf-8"))
    trade_date = sys.argv[1] if len(sys.argv) > 1 else payload.get("data_as_of") or ""
    contract = attach(payload, trade_date)
    base.DATA.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    coverage = contract["coverage"]
    if contract["status"] == "empty_pool":
        print("jinlong skipped: empty intersection", flush=True)
        return 0
    hits = sum(
        1 for code in (payload.get("intersection") or [])
        if (payload["enrichments"].get(code, {}).get("jinlong") or {}).get("hit")
    )
    today = sum(
        1 for code in (payload.get("intersection") or [])
        if (payload["enrichments"].get(code, {}).get("jinlong") or {}).get("hit_today")
    )
    print(
        f"jinlong: pool={coverage['requested']} ok={coverage['ok']} "
        f"unavailable={coverage['unavailable']} hit_today={today} hit_{WINDOW}d={hits} "
        f"failures={len(contract['failures'])}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

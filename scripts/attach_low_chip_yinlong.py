#!/usr/bin/env python3
"""银龙（RSI9 上穿 20 + MACD 柱为负）信号 overlay —— 低筹码流水线 enrichment 阶段。

口径见 `scripts/yinlong_signal.py`，来源是《银龙选股指标说明报告》引用的
YinLong.txt：

    XG: CROSS(RSI1,20) AND MACD<0

行情：与金龙共用同一份前复权日线缓存 `data/local/jinlong-bars.db`（同花顺 01
序列），所以当日流水线里金龙先跑过之后，本阶段几乎不产生网络请求。

写入：
  enrichments[code]["yinlong"] = {
      status, hit, hit_today, as_of, last_hit_date, bars_since_hit, window,
      rsi9, rsi9_previous, macd_hist, close, history_bars, source, model
  }
  顶层 yinlong_contract = {status, as_of, source, model, window, adjustment, coverage, failures}

fail-soft：单只取数失败只标该只 UNAVAILABLE，不阻塞发布；全部失败时契约
status=unavailable，页面显示数据不可用而不是「0 只命中」。

用法：
    python attach_low_chip_yinlong.py [TRADE_DATE]
    TRADE_DATE 缺省时回退 payload 的 data_as_of。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_low_chip_base as base  # noqa: E402  (复用 DATA 路径)
import attach_low_chip_jinlong as bars_store  # noqa: E402  (共用行情缓存)
from yinlong_signal import MIN_BARS, signal_at  # noqa: E402

SOURCE = bars_store.SOURCE
MODEL = "yinlong-shuanglong-chaodi-pro-v2"
WINDOW = 20                 # 观察窗：最近 20 个交易日内出现过 XG（与金龙一致）


def attach(payload: dict, trade_date: str, *, bars_loader=None) -> dict:
    """把银龙信号写进 payload 的 enrichments，返回契约。不写文件。"""
    if bars_loader is not None:
        return _attach(payload, trade_date, bars_loader)
    db = bars_store._connect()
    try:
        return _attach(
            payload, trade_date, lambda code: bars_store.bars_for(code, trade_date, db)
        )
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
            "window_semantics": "bars_since_lte",
            "signal_semantics": "recent_event_background",
            "adjustment": "qfq",
            "coverage": {"requested": 0, "ok": 0, "unavailable": 0},
            "failures": [],
        }
        payload["yinlong_contract"] = contract
        return contract

    ok = 0
    failures: list[str] = []
    for code in codes:
        entry = enrichments.setdefault(code, {})
        try:
            bars = loader(code)
        except Exception as exc:  # noqa: BLE001 单只取数失败隔离
            failures.append(f"{code}: {str(exc)[:160]}")
            entry["yinlong"] = _unavailable(trade_date, f"行情取数失败: {str(exc)[:160]}")
            continue
        result = signal_at(bars, trade_date)
        if result.get("status") != "ok":
            failures.append(f"{code}: {result.get('reason')}")
            entry["yinlong"] = _unavailable(trade_date, str(result.get("reason")))
            continue
        since = result["bars_since_hit"]
        entry["yinlong"] = {
            "status": "ok",
            "hit": bool(since is not None and since <= WINDOW),
            "recent_hit_5": bool(since is not None and since <= 5),
            "recent_hit_10": bool(since is not None and since <= 10),
            "hit_today": result["xg"],
            "as_of": result["as_of"],
            "last_hit_date": result["last_hit_date"],
            "bars_since_hit": since,
            "window": WINDOW,
            "window_semantics": "bars_since_lte",
            "signal_semantics": "recent_event_background",
            "close": round(result["close"], 4),
            "rsi9": _round(result["rsi9"]),
            "rsi9_previous": _round(result["rsi9_previous"]),
            "macd_hist": _round(result["macd_hist"], 4),
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
        "window_semantics": "bars_since_lte",
        "signal_semantics": "recent_event_background",
        "adjustment": "qfq",
        "coverage": {"requested": len(codes), "ok": ok, "unavailable": len(codes) - ok},
        "failures": failures[:20],
    }
    payload["yinlong_contract"] = contract
    return contract


def _unavailable(trade_date: str, reason: str) -> dict:
    return {
        "status": "UNAVAILABLE",
        "hit": False,
        "recent_hit_5": False,
        "recent_hit_10": False,
        "hit_today": False,
        "as_of": trade_date,
        "last_hit_date": None,
        "bars_since_hit": None,
        "window": WINDOW,
        "window_semantics": "bars_since_lte",
        "signal_semantics": "recent_event_background",
        "close": None,
        "rsi9": None,
        "rsi9_previous": None,
        "macd_hist": None,
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
        print("yinlong skipped: empty intersection", flush=True)
        return 0
    hits = sum(
        1 for code in (payload.get("intersection") or [])
        if (payload["enrichments"].get(code, {}).get("yinlong") or {}).get("hit")
    )
    today = sum(
        1 for code in (payload.get("intersection") or [])
        if (payload["enrichments"].get(code, {}).get("yinlong") or {}).get("hit_today")
    )
    print(
        f"yinlong: pool={coverage['requested']} ok={coverage['ok']} "
        f"unavailable={coverage['unavailable']} hit_today={today} hit_{WINDOW}d={hits} "
        f"failures={len(contract['failures'])}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

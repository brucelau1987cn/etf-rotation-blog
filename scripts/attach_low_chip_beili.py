#!/usr/bin/env python3
"""底背离（直接 + 隔峰）信号 overlay —— 低筹码流水线 enrichment 阶段。

口径见 `scripts/beili_signal.py`，来源是双龙抄底PRO 指标的「底部结构预警」段：
价格创新低而 DIF 抬高，且 MACD 柱仍在零轴下方。直接底背离比较相邻两段死叉结构，
隔峰底背离跨一段比较三段。

行情：与金龙/银龙共用同一份前复权日线缓存 `data/local/jinlong-bars.db`，
流水线里前两阶段跑过之后本阶段零网络请求。

写入：
  enrichments[code]["beili"] = {
      status, hit, today, as_of, last_hit_date, bars_since_hit, window,
      direct_today, separated_today, voided, close, dif, macd_hist,
      cl1, cl2, cl3, difl1, difl2, difl3, history_bars, source, model
  }
  顶层 beili_contract = {status, as_of, source, model, window, adjustment, coverage, failures}

「有效底背离」= 最近一次成立之后没有再触发「底背离消失」（DIFL1 <= DIFL2/3）。
被消失条件否定的不算命中，页面就不会把已经破位的结构当成买点。

fail-soft：单只取数失败只标该只 UNAVAILABLE，不阻塞发布；全部失败时契约
status=unavailable，页面显示数据不可用而不是「0 只命中」。

用法：
    python attach_low_chip_beili.py [TRADE_DATE]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_low_chip_base as base  # noqa: E402
import attach_low_chip_jinlong as bars_store  # noqa: E402
from beili_signal import signal_at  # noqa: E402

SOURCE = bars_store.SOURCE
MODEL = "shuanglong-dingliang-jiegou-divergence-v1"
WINDOW = 20                 # 观察窗：最近 20 个交易日内出现且未被否定的底背离


def attach(payload: dict, trade_date: str, *, bars_loader=None) -> dict:
    """把底背离信号写进 payload 的 enrichments，返回契约。不写文件。"""
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
            "adjustment": "qfq",
            "coverage": {"requested": 0, "ok": 0, "unavailable": 0},
            "failures": [],
        }
        payload["beili_contract"] = contract
        return contract

    ok = 0
    failures: list[str] = []
    for code in codes:
        entry = enrichments.setdefault(code, {})
        try:
            bars = loader(code)
        except Exception as exc:  # noqa: BLE001 单只取数失败隔离
            failures.append(f"{code}: {str(exc)[:160]}")
            entry["beili"] = _unavailable(trade_date, f"行情取数失败: {str(exc)[:160]}")
            continue
        result = signal_at(bars, trade_date)
        if result.get("status") != "ok":
            failures.append(f"{code}: {result.get('reason')}")
            entry["beili"] = _unavailable(trade_date, str(result.get("reason")))
            continue
        since = result["bars_since_hit"]
        valid = bool(since is not None and since <= WINDOW and not result["voided"])
        entry["beili"] = {
            "status": "ok",
            "hit": valid,
            "today": result["today"],
            "as_of": result["as_of"],
            "last_hit_date": result["last_hit_date"],
            "bars_since_hit": since,
            "window": WINDOW,
            "direct_today": result["direct_today"],
            "separated_today": result["separated_today"],
            "voided": result["voided"],
            "close": _round(result["close"], 4),
            "dif": _round(result["dif"], 4),
            "macd_hist": _round(result["macd_hist"], 4),
            "cl1": _round(result["cl1"], 4),
            "cl2": _round(result["cl2"], 4),
            "cl3": _round(result["cl3"], 4),
            "difl1": _round(result["difl1"], 4),
            "difl2": _round(result["difl2"], 4),
            "difl3": _round(result["difl3"], 4),
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
    payload["beili_contract"] = contract
    return contract


def _unavailable(trade_date: str, reason: str) -> dict:
    return {
        "status": "UNAVAILABLE",
        "hit": False,
        "today": False,
        "as_of": trade_date,
        "last_hit_date": None,
        "bars_since_hit": None,
        "window": WINDOW,
        "direct_today": False,
        "separated_today": False,
        "voided": False,
        "close": None,
        "dif": None,
        "macd_hist": None,
        "cl1": None,
        "cl2": None,
        "cl3": None,
        "difl1": None,
        "difl2": None,
        "difl3": None,
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
        print("beili skipped: empty intersection", flush=True)
        return 0
    codes = payload.get("intersection") or []
    hits = sum(1 for c in codes if (payload["enrichments"].get(c, {}).get("beili") or {}).get("hit"))
    today = sum(1 for c in codes if (payload["enrichments"].get(c, {}).get("beili") or {}).get("today"))
    voided = sum(1 for c in codes if (payload["enrichments"].get(c, {}).get("beili") or {}).get("voided"))
    print(
        f"beili: pool={coverage['requested']} ok={coverage['ok']} "
        f"unavailable={coverage['unavailable']} today={today} hit_{WINDOW}d={hits} "
        f"voided={voided} failures={len(contract['failures'])}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

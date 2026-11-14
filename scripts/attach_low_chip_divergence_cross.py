#!/usr/bin/env python3
"""三路金叉型背离（B1/B2/B3 底背离 + D1/D2/D3 顶背离）—— 低筹码流水线 enrichment 阶段。

口径见 `scripts/divergence_cross_signal.py`，来源是双龙抄底PRO 源码里 MACD/KDJ/RSI
三组简单背离段。与已上线的「结构底背离」（`beili_signal.py`，死叉分段 + DIF 有效
数字压缩）是两套不同算法，可以交叉验证。

行情：与金龙/银龙/结构底背离共用同一份前复权日线缓存，流水线里前几阶段跑过之后
本阶段零网络请求。

写入：
  enrichments[code]["xdiv"] = { status, hit, today, as_of, last_hit_date,
                                 bars_since_hit, window, today_parts, source, model }
  enrichments[code]["xtop"] = 同上（顶背离，减仓侧）
  顶层 xdiv_contract = {status, as_of, source, model, window, adjustment, coverage, failures}

窗口：这两组信号本身高频（257 只池近两年 4314 次底背离事件），20 日窗会命中 90%
的池子而失去筛选意义，所以默认取 5 个交易日 —— 语义是「最近一周内刚出现金叉型
背离」。`--window` 可覆盖。

fail-soft：单只取数失败只标该只 UNAVAILABLE，不阻塞发布；全部失败时契约
status=unavailable，页面显示数据不可用而不是「0 只命中」。

用法：
    python attach_low_chip_divergence_cross.py [TRADE_DATE]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_low_chip_base as base  # noqa: E402
import attach_low_chip_jinlong as bars_store  # noqa: E402
from divergence_cross_signal import signal_at  # noqa: E402

SOURCE = bars_store.SOURCE
MODEL = "shuanglong-cross-divergence-v1"
WINDOW = 5                  # 最近 5 个交易日内出现金叉型背离（信号高频，见模块文档）


def attach(payload: dict, trade_date: str, *, bars_loader=None, window: int = WINDOW) -> dict:
    """把三路金叉型背离写进 payload 的 enrichments，返回契约。不写文件。"""
    if bars_loader is not None:
        return _attach(payload, trade_date, bars_loader, window)
    db = bars_store._connect()
    try:
        return _attach(
            payload, trade_date,
            lambda code: bars_store.bars_for(code, trade_date, db),
            window,
        )
    finally:
        db.close()


def _attach(payload: dict, trade_date: str, loader, window: int) -> dict:
    codes = payload.get("intersection") or payload.get("intersection_before_filters") or []
    enrichments = payload.setdefault("enrichments", {})

    if not codes:
        contract = {
            "status": "empty_pool",
            "as_of": trade_date,
            "source": SOURCE,
            "model": MODEL,
            "window": window,
            "adjustment": "qfq",
            "coverage": {"requested": 0, "ok": 0, "unavailable": 0},
            "failures": [],
        }
        payload["xdiv_contract"] = contract
        return contract

    ok = 0
    failures: list[str] = []
    for code in codes:
        entry = enrichments.setdefault(code, {})
        try:
            bars = loader(code)
        except Exception as exc:  # noqa: BLE001 单只取数失败隔离
            failures.append(f"{code}: {str(exc)[:160]}")
            entry["xdiv"] = _unavailable(trade_date, f"行情取数失败: {str(exc)[:160]}", window)
            entry["xtop"] = _unavailable(trade_date, f"行情取数失败: {str(exc)[:160]}", window)
            continue
        result = signal_at(bars, trade_date)
        if result.get("status") != "ok":
            failures.append(f"{code}: {result.get('reason')}")
            entry["xdiv"] = _unavailable(trade_date, str(result.get("reason")), window)
            entry["xtop"] = _unavailable(trade_date, str(result.get("reason")), window)
            continue
        entry["xdiv"] = _entry(result["bottom"], trade_date, window, "bottom")
        entry["xtop"] = _entry(result["top"], trade_date, window, "top")
        ok += 1

    contract = {
        "status": "ok" if ok else "unavailable",
        "as_of": trade_date,
        "source": SOURCE,
        "model": MODEL,
        "window": window,
        "adjustment": "qfq",
        "coverage": {"requested": len(codes), "ok": ok, "unavailable": len(codes) - ok},
        "failures": failures[:20],
    }
    payload["xdiv_contract"] = contract
    return contract


def _entry(block: dict, trade_date: str, window: int, side: str) -> dict:
    since = block["bars_since_hit"]
    return {
        "status": "ok",
        "hit": bool(since is not None and since <= window),
        "today": block["today"],
        "as_of": trade_date,
        "last_hit_date": block["last_hit_date"],
        "bars_since_hit": since,
        "window": window,
        "today_parts": block["today_parts"],
        "close": _round(block["close"], 4),
        "dif": _round(block["dif"], 4),
        "k": _round(block["k"], 2),
        "rsi6": _round(block["rsi6"], 2),
        "side": side,
        "source": SOURCE,
        "model": MODEL,
    }


def _unavailable(trade_date: str, reason: str, window: int) -> dict:
    return {
        "status": "UNAVAILABLE",
        "hit": False,
        "today": False,
        "as_of": trade_date,
        "last_hit_date": None,
        "bars_since_hit": None,
        "window": window,
        "today_parts": [],
        "close": None,
        "dif": None,
        "k": None,
        "rsi6": None,
        "source": SOURCE,
        "model": MODEL,
        "reason": reason,
    }


def _round(value, digits: int = 2):
    return None if value is None else round(float(value), digits)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("trade_date", nargs="?", help="缺省用 payload 的 data_as_of")
    parser.add_argument("--window", type=int, default=WINDOW, help="观察窗交易日数")
    args = parser.parse_args()

    payload = json.loads(base.DATA.read_text(encoding="utf-8"))
    trade_date = args.trade_date or payload.get("data_as_of") or ""
    contract = attach(payload, trade_date, window=args.window)
    base.DATA.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    coverage = contract["coverage"]
    if contract["status"] == "empty_pool":
        print("divergence_cross skipped: empty intersection", flush=True)
        return 0
    codes = payload.get("intersection") or []
    bottom = sum(1 for c in codes if (payload["enrichments"].get(c, {}).get("xdiv") or {}).get("hit"))
    top = sum(1 for c in codes if (payload["enrichments"].get(c, {}).get("xtop") or {}).get("hit"))
    bottom_today = sum(1 for c in codes if (payload["enrichments"].get(c, {}).get("xdiv") or {}).get("today"))
    top_today = sum(1 for c in codes if (payload["enrichments"].get(c, {}).get("xtop") or {}).get("today"))
    print(
        f"divergence_cross: pool={coverage['requested']} ok={coverage['ok']} "
        f"unavailable={coverage['unavailable']} "
        f"bottom_today={bottom_today} bottom_{args.window}d={bottom} "
        f"top_today={top_today} top_{args.window}d={top} "
        f"failures={len(contract['failures'])}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

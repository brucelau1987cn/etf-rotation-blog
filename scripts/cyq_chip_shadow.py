#!/usr/bin/env python3
"""CYQ 筹码分布影子快照 — 本地三角分布推演，与 iWenCai 收盘获利交叉对照。

影子性质：仅写 public/data/cyq-chip-shadow.json，观察本地 CYQ 获利比例与
iWenCai 收盘获利（现有生产追踪页口径）的一致性。不接生产筛选/展示、不改动作/仓位。

数据源：
  - 标的：public/data/low-chip-tracking.json（追踪池，含当前观察池 intersection 超集）
  - OHLC+换手率：baostock 前复权（adjustflag=2）
  - 对照值：追踪池 daily[-1].profit_ratio（iWenCai 收盘获利，百分数）

算法：scripts/reference/a-stock-data/chip_distribution.py 的 chip_distribution()
（三角分布峰值在均价 (high+low+close)/3，换手率衰减 decay=1.0，初始播种为首日全部流通盘）。

运行时：/usr/bin/python3（系统 python，含 baostock/numpy/pandas）。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "reference" / "a-stock-data"))
from chip_distribution import chip_distribution, _bs_code  # noqa: E402

OUT = ROOT / "public" / "data" / "cyq-chip-shadow.json"
TRACKING = ROOT / "public" / "data" / "low-chip-tracking.json"
OBSERVE = ROOT / "public" / "data" / "a-low-chip-stocks.json"
CN_TZ = timezone(timedelta(hours=8))

WINDOW_DAYS = 240          # 回溯自然日，约 115~120 个交易日
DECAY = 1.0                # 换手衰减系数（1.0=真实换手率，标准 CYQ 口径）
SLEEP = 0.3                # baostock 逐只串行限速


def load_symbols() -> tuple[dict[str, str], dict[str, float | None], str]:
    """返回 (code→name, code→iWenCai最新收盘获利, data_as_of)。"""
    track = json.loads(TRACKING.read_text(encoding="utf-8")) or {}
    obs = json.loads(OBSERVE.read_text(encoding="utf-8")) or {}
    data_as_of = obs.get("data_as_of") or ""
    stocks = track.get("stocks", {}) or {}

    names: dict[str, str] = {}
    iwencai: dict[str, float | None] = {}
    for code, v in stocks.items():
        names[code] = v.get("name", "")
        daily = v.get("daily") or []
        pr = None
        if daily:
            pr = daily[-1].get("profit_ratio")
        iwencai[code] = float(pr) if pr is not None else None

    # 观察池 intersection 兜底纳入（理论上已在追踪池，防御性补全）
    for code in obs.get("intersection", []) or []:
        names.setdefault(code, code)
        iwencai.setdefault(code, None)

    return names, iwencai, data_as_of


def fetch_batch(codes: list[str], start_date: str, end_date: str) -> dict[str, pd.DataFrame]:
    """一次 baostock login，逐只取前复权 OHLC+换手率（停牌日过滤）。"""
    import baostock as bs
    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError(f"baostock 登录失败: {lg.error_code} {lg.error_msg}")
    out: dict[str, pd.DataFrame] = {}
    try:
        for code in codes:
            try:
                bscode = _bs_code(bare(code))
                rs = bs.query_history_k_data_plus(
                    bscode, "date,high,low,close,turn,tradestatus",
                    start_date=start_date, end_date=end_date,
                    frequency="d", adjustflag="2")  # 2=前复权
                rows = []
                while rs.next():
                    rows.append(rs.get_row_data())
                if not rows:
                    out[code] = pd.DataFrame()
                else:
                    k = pd.DataFrame(rows, columns=rs.fields)
                    for c in ("high", "low", "close", "turn"):
                        k[c] = pd.to_numeric(k[c], errors="coerce")
                    k = k[k["tradestatus"] == "1"]
                    out[code] = k
            except ValueError:
                # _bs_code 拒绝北交所等不支持号段 → 记为空，由主循环标记 skipped
                out[code] = pd.DataFrame()
            time.sleep(SLEEP)
    finally:
        bs.logout()
    return out


def bare(code: str) -> str:
    return code.split(".")[0].zfill(6)


def main() -> int:
    names, iwencai, data_as_of = load_symbols()
    now = datetime.now(CN_TZ)
    end_date = data_as_of or now.strftime("%Y-%m-%d")
    try:
        start_date = (datetime.strptime(end_date, "%Y-%m-%d") - timedelta(days=WINDOW_DAYS)).strftime("%Y-%m-%d")
    except ValueError:
        start_date = (now - timedelta(days=WINDOW_DAYS)).strftime("%Y-%m-%d")
        end_date = now.strftime("%Y-%m-%d")

    codes = sorted(names, key=lambda c: bare(c))
    frames = fetch_batch(codes, start_date, end_date)

    stocks, errors = [], {}
    computed = matched = 0
    for code in codes:
        df = frames.get(code)
        if df is None or df.empty:
            errors[code] = "无前复权 K 线（可能为北交所/上市过短/停牌）"
            continue
        try:
            r = chip_distribution(df, decay=DECAY)
        except Exception as exc:  # noqa: BLE001
            errors[code] = f"{type(exc).__name__}: {exc}"
            continue
        computed += 1
        iwc = iwencai.get(code)
        diff = None
        if iwc is not None:
            diff = round(r["profit_ratio"] * 100.0 - iwc, 4)
            matched += 1
        stocks.append({
            "code": code,
            "name": names.get(code, ""),
            "cyq_profit_ratio_pct": round(r["profit_ratio"] * 100.0, 4),
            "cyq_avg_cost": round(r["avg_cost"], 4),
            "cyq_cost_90": [round(x, 4) for x in r["cost_90"]],
            "cyq_cost_70": [round(x, 4) for x in r["cost_70"]],
            "cyq_concentration_90_pct": (round(r["concentration_90"] * 100.0, 4)
                                         if r["concentration_90"] is not None else None),
            "cyq_concentration_70_pct": (round(r["concentration_70"] * 100.0, 4)
                                         if r["concentration_70"] is not None else None),
            "cyq_peak_price": round(r["peak_price"], 4),
            "iwencai_profit_ratio_pct": iwc,
            "diff_pct_points": diff,
            "window_bars": int(len(df)),
        })

    diffs = [s["diff_pct_points"] for s in stocks if s["diff_pct_points"] is not None]
    summary = {
        "total_symbols": len(codes),
        "computed": computed,
        "failed": len(errors),
        "matched_with_iwencai": matched,
    }
    if diffs:
        diffs_sorted = sorted(diffs)
        import statistics
        summary.update({
            "diff_mean_points": round(statistics.mean(diffs), 4),
            "diff_median_points": round(statistics.median(diffs), 4),
            "diff_abs_max_points": round(max(abs(x) for x in diffs), 4),
            "diff_within_5pts": sum(1 for x in diffs if abs(x) <= 5),
            "diff_within_10pts": sum(1 for x in diffs if abs(x) <= 10),
        })

    status = "ok"
    if errors:
        status = "degraded"
    if computed == 0:
        status = "error"

    payload = {
        "version": 1,
        "generated_at": now.isoformat(timespec="seconds"),
        "window": {"start": start_date, "end": end_date, "decay": DECAY},
        "status": status,
        "summary": summary,
        "stocks": stocks,
        "errors": errors,
        "disclaimer": "影子快照：本地 CYQ 三角分布推演，与 iWenCai 收盘获利对照，未接入生产筛选/展示。",
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".cyq-chip.", suffix=".tmp", dir=OUT.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, OUT)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

    s = payload["summary"]
    line = f"CYQ影子 {status}｜标的{s['total_symbols']} 计算{s['computed']} 失败{s['failed']} 对照{s['matched_with_iwencai']}"
    if diffs:
        line += (f"｜差值均值{s['diff_mean_points']} 中位{s['diff_median_points']} "
                 f"≤5pt {s['diff_within_5pts']}/{len(diffs)} ≤10pt {s['diff_within_10pts']}/{len(diffs)}")
    print(line + (f"｜异常 {list(errors)[:5]}" if errors else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

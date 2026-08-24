#!/usr/bin/env python3
"""打板层影子快照 — 交易日盘后取涨停/炸板/跌停/连板梯队/涨停揭秘/重点监控/日内异动。

影子性质：仅写 public/data/limit-up-shadow.json，不接页面、不进生产动作/仓位。
观察 1-2 周数据质量后再决定是否上正式页面/口径。

复用 scripts/reference/a-stock-data/limit_up.py（a-stock-data Apache 2.0 抽取）。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REF = ROOT / "scripts" / "reference" / "a-stock-data"
sys.path.insert(0, str(REF))

from limit_up import (  # noqa: E402
    em_dt_pool, em_price_anomaly, em_price_anomaly_count, em_stock_monitor,
    em_yzt_pool, em_zb_pool, em_zt_pool, limit_up_sentiment, ths_limit_up_pool,
)

OUT = ROOT / "public" / "data" / "limit-up-shadow.json"
CN_TZ = timezone(timedelta(hours=8))


def latest_trade_date(max_back: int = 7) -> str:
    """用 baostock 交易日历找最近交易日（YYYYMMDD）；未收盘则回退上一交易日。

    东财 push2ex 传非交易日/未收盘日期会静默回吐上一交易日数据（不报错），
    不能用「返回非空」判断交易日——必须用交易日历，否则会把今天(未开盘)误判成有数据。
    """
    import baostock as bs
    now = datetime.now(CN_TZ)
    start = (now.date() - timedelta(days=max_back)).strftime("%Y-%m-%d")
    end = now.date().strftime("%Y-%m-%d")
    lg = bs.login()
    try:
        rs = bs.query_trade_dates(start_date=start, end_date=end)
        trading = []
        while rs.next():
            row = rs.get_row_data()
            if row[1] == "1":
                trading.append(row[0])
    finally:
        bs.logout()
    if not trading:
        raise RuntimeError("baostock 交易日历查询失败")
    latest = trading[-1]
    # 今天是交易日但尚未收盘（A股 15:00 收盘）→ 用上一交易日，避免拿到盘中不完整数据
    if latest == now.strftime("%Y-%m-%d") and now.hour < 15:
        latest = trading[-2] if len(trading) >= 2 else latest
    return latest.replace("-", "")


def main() -> int:
    date = latest_trade_date()
    errors: dict[str, str] = {}

    zt = em_zt_pool(date)
    zb = em_zb_pool(date)
    dt = em_dt_pool(date)
    sentiment = limit_up_sentiment(date)

    ths = []
    try:
        ths = ths_limit_up_pool(date)
    except Exception as exc:  # noqa: BLE001
        errors["ths_limit_up"] = f"{type(exc).__name__}: {exc}"

    monitor = []
    try:
        monitor = em_stock_monitor()
    except Exception as exc:  # noqa: BLE001
        errors["stock_monitor"] = f"{type(exc).__name__}: {exc}"

    anomaly = {"date": "", "items": []}
    try:
        anomaly = em_price_anomaly(page_size=200)
    except Exception as exc:  # noqa: BLE001
        errors["price_anomaly"] = f"{type(exc).__name__}: {exc}"

    zt_n, zb_n, dt_n = len(zt), len(zb), len(dt)
    break_rate = round(zb_n / (zt_n + zb_n) * 100, 1) if (zt_n + zb_n) else 0
    max_height = max((s["limit_days"] for s in zt), default=0)

    payload = {
        "version": 1,
        "generated_at": datetime.now(CN_TZ).isoformat(timespec="seconds"),
        "trade_date": date,
        "status": "ok" if not errors else "degraded",
        "source": "a-stock-data 打板层（东财 push2ex + 同花顺 data.10jqka）",
        "summary": {
            "zt_count": zt_n,
            "zb_count": zb_n,
            "dt_count": dt_n,
            "break_rate": break_rate,
            "max_height": max_height,
            "ladder": sentiment.get("ladder", {}),
            "ths_count": len(ths),
            "monitor_count": len(monitor),
            "anomaly_count": len(anomaly.get("items", [])),
        },
        "zt_pool": zt,
        "zb_pool": zb,
        "dt_pool": dt,
        "ths_limit_up": ths,
        "stock_monitor": monitor,
        "price_anomaly": anomaly,
        "errors": errors,
        "disclaimer": "影子快照，仅观察数据质量，不进入生产动作与仓位计算。",
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".limit-up.", suffix=".tmp", dir=OUT.parent)
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
    print(
        f"打板影子 {date}｜涨停{s['zt_count']} 炸板{s['zb_count']}(炸板率{s['break_rate']}%) "
        f"跌停{s['dt_count']} 最高{s['max_height']}连板｜涨停揭秘{len(ths)} 监控{len(monitor)} 异动{s['anomaly_count']}"
        + (f"｜异常 {len(errors)}" if errors else "")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

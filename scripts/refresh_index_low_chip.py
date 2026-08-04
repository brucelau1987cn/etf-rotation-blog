#!/usr/bin/env python3
"""Refresh the index low-chip snapshot.

iWenCai query: 指数 收盘获利比例小于{THRESHOLD}%
Futures contracts have no 收盘获利 field in iWenCai, so this page is index-only.

Output: public/data/index-low-chip.json
"""
from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timedelta, timezone

ROOT = "/root/projects/etf-rotation-blog"
CN = timezone(timedelta(hours=8))
IWENCAI = "/root/.hermes/scripts/iwencai-market-query"
OUT = f"{ROOT}/public/data/index-low-chip.json"
THRESHOLD = 2.0


def iwencai(query: str, limit: int = 100, page: int = 1) -> dict:
    proc = subprocess.run(
        [IWENCAI, "-q", query, "--limit", str(limit), "--page", str(page), "--timeout", "120"],
        capture_output=True,
        text=True,
        timeout=180,
    )
    return json.loads(proc.stdout or "{}")


def fetch_all(query: str, page_size: int = 100) -> list[dict]:
    rows: list[dict] = []
    page = 1
    while True:
        data = iwencai(query, limit=page_size, page=page)
        chunk = data.get("datas") or []
        rows.extend(chunk)
        if not data.get("has_more") or not chunk:
            break
        page += 1
        if page > 20:
            break
    return rows


def main() -> None:
    rows = fetch_all(f"指数 收盘获利比例小于{THRESHOLD:g}%")
    if not rows:
        raise RuntimeError("iWenCai returned zero low-profit indices")

    field_date = None
    profit_key = None
    for row in rows:
        for key in row:
            m = re.search(r"收盘获利\[(\d{8})\]$", str(key))
            if m:
                field_date = m.group(1)
                profit_key = key
                break
        if field_date:
            break
    if not field_date or not profit_key:
        raise RuntimeError("no iWenCai index chip field date found")

    items = []
    for row in rows:
        code = str(row.get("指数代码") or "").strip()
        name = str(row.get("指数简称") or "").strip()
        if not code or not name:
            continue
        profit = float(row.get(profit_key) or 0)
        if profit >= THRESHOLD:
            continue
        change = row.get("最新涨跌幅:前复权")
        price = row.get("最新价")
        try:
            price_f = float(price) if price not in (None, "") else None
        except (TypeError, ValueError):
            price_f = None
        try:
            change_f = float(change) if change not in (None, "") else None
        except (TypeError, ValueError):
            change_f = None
        items.append(
            {
                "code": code,
                "name": name,
                "profit": round(profit, 2),
                "price": price_f,
                "change_percent": None if change_f is None else round(change_f, 2),
            }
        )

    items.sort(key=lambda r: (r["profit"], r["code"]))
    data_as_of = f"{field_date[:4]}-{field_date[4:6]}-{field_date[6:]}"
    payload = {
        "schema_version": 1,
        "data_as_of": data_as_of,
        "generated_at": datetime.now(CN).isoformat(timespec="seconds"),
        "source": "iWenCai",
        "universe": "指数",
        "metric": "收盘获利比例",
        "threshold": f"低于{THRESHOLD:g}%",
        "counts": {"indices": len(items)},
        "indices": items,
        "notes": "iWenCai 无商品期货收盘获利字段；本页仅覆盖指数口径。",
    }
    with open(OUT, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=1)
        handle.write("\n")
    print(f"data_as_of={data_as_of} indices={len(items)} -> {OUT}")


if __name__ == "__main__":
    main()

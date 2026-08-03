#!/usr/bin/env python3
"""Refresh the ETF low-chip snapshot.

Pipeline: iWenCai full stock-ETF universe (track index / scale / shares / type)
+ iWenCai indices with 收盘获利比例 < 3% (index-level chip profit proxy,
because iWenCai exposes no per-ETF chip metric) + Tencent quotes for price.

Output: public/data/etf-low-chip-stocks.json (auditable snapshot).
"""
from __future__ import annotations

import glob
import json
import subprocess
import urllib.request
from datetime import datetime, timedelta, timezone

ROOT = "/root/projects/etf-rotation-blog"
CN = timezone(timedelta(hours=8))
IWENCAI = "/root/.hermes/scripts/iwencai-market-query"
OUT = f"{ROOT}/public/data/etf-low-chip-stocks.json"
THRESHOLD = 3.0


def iwencai(query: str, limit: int = 100, page: int = 1) -> dict:
    proc = subprocess.run(
        [IWENCAI, "-q", query, "--limit", str(limit), "--page", str(page), "--timeout", "120"],
        capture_output=True, text=True, timeout=180,
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
        if page > 30:
            break
    return rows


def tencent_quotes(codes: list[str]) -> dict[str, dict]:
    def tx(code: str) -> str:
        return ("sh" if code.endswith(".SH") else "sz") + code.split(".")[0].lower()

    quotes: dict[str, dict] = {}
    for i in range(0, len(codes), 50):
        batch = codes[i:i + 50]
        url = "https://qt.gtimg.cn/q=" + ",".join(tx(c) for c in batch)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"})
        raw = urllib.request.urlopen(req, timeout=30).read().decode("gbk", "ignore")
        for line in raw.split(";"):
            line = line.strip()
            if not line or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip().split("_")[-1].lower()
            parts = val.strip('"').split("~")
            if len(parts) > 35 and parts[3]:
                quotes[key] = {"price": float(parts[3]), "change_pct": float(parts[32])}
    return quotes


def main() -> None:
    etf_rows = fetch_all("股票ETF 跟踪指数、基金规模、基金份额")
    idx_rows = fetch_all("指数 收盘获利比例小于3%")

    etfs: dict[str, dict] = {}
    for x in etf_rows:
        code = str(x.get("基金代码") or "")
        if code:
            etfs.setdefault(code, x)
    idxs: dict[str, dict] = {}
    for x in idx_rows:
        name = str(x.get("指数简称") or "").strip()
        if name:
            idxs.setdefault(name, x)

    low_names = set(idxs)
    matched = [
        (code, x) for code, x in etfs.items()
        if str(x.get("跟踪指数") or "").strip() in low_names
    ]
    codes = [c for c, _ in matched]
    quotes = tencent_quotes(codes)

    def tx(code: str) -> str:
        return ("sh" if code.endswith(".SH") else "sz") + code.split(".")[0].lower()

    items = []
    for code, x in matched:
        idx = str(x.get("跟踪指数") or "").strip()
        if not idx:
            continue
        q = quotes.get(tx(code)) or {}
        items.append({
            "code": code,
            "name": x.get("基金简称"),
            "short_name": x.get("基金扩位简称"),
            "track_index": idx,
            "index_profit": round(float(idxs[idx].get("收盘获利[20260803]") or 0), 2),
            "scale_yi": round((float(x.get("基金规模[20260804]") or 0) / 1e8), 2),
            "shares_yi": round((float(x.get("基金份额[20260804]") or 0) / 1e8), 2),
            "type": x.get("etf类型二级分类") or "",
            "t0": bool(x.get("是否t加0基金")),
            "manager": x.get("现任基金经理姓名") or [],
            "price": q.get("price"),
            "change_percent": q.get("change_pct"),
        })

    items.sort(key=lambda r: (r["index_profit"], -r["scale_yi"]))

    # Data date: derive from the iWenCai index chip field (e.g. 收盘获利[20260803]).
    import re
    field_date = None
    for v in idxs.values():
        for k in v:
            m = re.search(r"\[(\d{8})\]$", str(k))
            if m:
                field_date = m.group(1)
                break
        if field_date:
            break
    if not field_date:
        raise RuntimeError("no iWenCai index chip field date found")
    data_as_of = f"{field_date[:4]}-{field_date[4:6]}-{field_date[6:]}"
    profit_key = f"收盘获利[{field_date}]"
    indices = [
        {"code": str(v.get("指数代码") or ""), "name": name, "profit": float(v.get(profit_key) or 0)}
        for name, v in sorted(idxs.items(), key=lambda kv: float(kv[1].get(profit_key) or 0))
    ]
    payload = {
        "schema_version": 1,
        "data_as_of": data_as_of,
        "generated_at": datetime.now(CN).isoformat(timespec="seconds"),
        "source": "iWenCai + Tencent",
        "metric": "跟踪指数收盘获利比例（ETF 无独立筹码分布，以跟踪指数代理）",
        "threshold": f"低于{THRESHOLD}%",
        "counts": {"low_profit_indices": len(indices), "matched_etfs": len(items),
                   "matched_indices": len({r["track_index"] for r in items})},
        "indices": indices,
        "etfs": items,
    }
    with open(OUT, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=1)
        handle.write("\n")
    print(f"data_as_of={data_as_of} indices={len(indices)} etfs={len(items)} -> {OUT}")


if __name__ == "__main__":
    main()

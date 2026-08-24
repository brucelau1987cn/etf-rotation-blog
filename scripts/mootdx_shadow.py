#!/usr/bin/env python3
"""mootdx 影子快照 — 交易日用 mootdx 取重点标的报价，与腾讯行情交叉对比。

影子性质：仅写 public/data/mootdx-shadow.json，观察 mootdx 作为行情补充源的稳定性。
mootdx 尚不替代生产腾讯行情——先影子校验数据一致性与连通稳定性，再决定是否接入。

运行时：/root/.cache/mootdx/venv/bin/python（mootdx 独立 venv）。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from mootdx_quote import tdx_client  # noqa: E402

OUT = ROOT / "public" / "data" / "mootdx-shadow.json"
CN_TZ = timezone(timedelta(hours=8))

# 重点标的：覆盖沪主板 / 深主板 / 创业板 / 科创板 / 热门
WATCH = {
    "600519": "贵州茅台", "600036": "招商银行", "601318": "中国平安", "600900": "长江电力",
    "000858": "五粮液", "000001": "平安银行", "000333": "美的集团", "002594": "比亚迪",
    "300750": "宁德时代", "300059": "东方财富", "300760": "迈瑞医疗",
    "688981": "中芯国际", "688017": "绿的谐波", "601127": "赛力斯",
}


def tencent_price(code: str) -> float | None:
    """腾讯 qt.gtimg.cn 实时价（GBK，~ 分隔，index 3 = 当前价）。"""
    prefix = "sh" if code.startswith(("6", "9")) else "sz"
    url = f"https://qt.gtimg.cn/q={prefix}{code}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            text = resp.read().decode("gbk", errors="ignore")
    except Exception:
        return None
    # v_sh600519="1~贵州茅台~600519~1272.83~..."
    try:
        payload = text.split("=", 1)[1].strip().strip('";')
        return float(payload.split("~")[3])
    except Exception:
        return None


def main() -> int:
    now = datetime.now(CN_TZ)
    codes = list(WATCH)
    errors: dict[str, str] = {}

    mootdx_map: dict[str, float | None] = {}
    try:
        client = tdx_client()
        q = client.quotes(symbol=codes)
        if q is not None and not q.empty:
            for _, row in q.iterrows():
                code = str(row.get("code", "")).zfill(6)
                price = row.get("price")
                mootdx_map[code] = float(price) if price is not None else None
    except Exception as exc:  # noqa: BLE001
        errors["mootdx"] = f"{type(exc).__name__}: {exc}"

    quotes = []
    for code, name in WATCH.items():
        mp = mootdx_map.get(code)
        tp = tencent_price(code)
        diff = None
        if mp is not None and tp is not None and tp > 0:
            diff = round((mp - tp) / tp * 100, 4)
        quotes.append({
            "code": code, "name": name,
            "mootdx_price": mp, "tencent_price": tp, "diff_pct": diff,
        })

    matched = [x for x in quotes if x["mootdx_price"] is not None and x["tencent_price"] is not None]
    diff_ok = [x for x in matched if x["diff_pct"] is not None and abs(x["diff_pct"]) < 0.5]
    mismatch = [x for x in matched if x["diff_pct"] is not None and abs(x["diff_pct"]) >= 0.5]
    max_diff = max((abs(x["diff_pct"]) for x in mismatch), default=0.0)

    status = "ok" if (not errors and len(diff_ok) >= len(matched) * 0.9) else "degraded"
    if not mootdx_map:
        status = "error"

    payload = {
        "version": 1,
        "generated_at": now.isoformat(timespec="seconds"),
        "status": status,
        "quote_count": len(quotes),
        "summary": {
            "matched": len(matched),
            "diff_within_0.5pct": len(diff_ok),
            "mismatch": len(mismatch),
            "max_diff_pct": round(max_diff, 4),
        },
        "quotes": quotes,
        "errors": errors,
        "disclaimer": "影子快照，mootdx 未替代生产腾讯行情，仅观察数据一致性。",
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".mootdx.", suffix=".tmp", dir=OUT.parent)
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
        f"mootdx影子 {status}｜对比{s['matched']}只 一致{s['diff_within_0.5pct']} "
        f"偏差≥0.5% {s['mismatch']}只(最大{s['max_diff_pct']}%)"
        + (f"｜异常 {list(errors)}" if errors else "")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

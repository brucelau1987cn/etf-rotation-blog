#!/usr/bin/env python3
"""90% 筹码集中度 overlay —— 低筹码流水线 enrichment 阶段。

口径：iWenCai「集中度90」字段（同花顺筹码分布口径），
      集中度90 = (90%成本上限 - 90%成本下限) / (90%成本上限 + 90%成本下限) × 100。
      数值越小代表筹码越集中。页面开关阈值为 < 10%。

写入：
  enrichments[code]["chip_concentration"] = {
      status, concentration_90, as_of, source, reason
  }
  顶层 chip_concentration_contract = {status, as_of, source, threshold, coverage}

fail-soft：单批失败只把该批标的标成 UNAVAILABLE，不阻塞发布；
          全部失败时契约 status=unavailable，页面开关命中 0 只（不静默当成「无集中标的」）。

用法：
    python attach_low_chip_chip_concentration.py [TRADE_DATE]
    TRADE_DATE 缺省时回退 payload 的 data_as_of。
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_low_chip_base as base  # noqa: E402  (复用 paginate / DATA)

FIELD_PREFIX = "集中度90"
THRESHOLD = 10.0
BATCH_SIZE = 40
SOURCE = "iwencai-concentration-90"


def _fetch(query: str, page: int) -> dict:
    """base.fetch 的替身：把 query 直接拼进 /tmp 文件名，40 只代码会超过
    255 字节文件名上限（Errno 36）。这里用 query 的哈希做文件名，重试语义
    与 base.fetch 保持一致。"""
    digest = hashlib.sha1(query.encode("utf-8")).hexdigest()[:16]
    out = Path(f"/tmp/lc_conc_{digest}_p{page}.json")
    detail = ""
    for attempt in range(1, base.FETCH_ATTEMPTS + 1):
        r = subprocess.run(
            [
                "/root/.hermes/scripts/iwencai-market-query",
                "-q", query,
                "--page", str(page),
                "--limit", "100",
                "--timeout", "90",
            ],
            capture_output=True, text=True, check=False,
        )
        if r.returncode == 0:
            out.write_text(r.stdout, encoding="utf-8")
            return json.loads(r.stdout)
        detail = (r.stderr or r.stdout or "").strip()
        if not base._is_transient(detail) or attempt == base.FETCH_ATTEMPTS:
            raise SystemExit(f"iWenCai error: {detail[:500]}")
        time.sleep(base.FETCH_BACKOFF_S * attempt)
    raise SystemExit(f"iWenCai error: {detail[:500]}")


def _value(row: dict) -> float | None:
    """从批量返回行里取集中度90 数值（键形如 集中度90[20261113]）。"""
    for key, value in row.items():
        if str(key).startswith(FIELD_PREFIX) and "[" in str(key):
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
    return None


def fetch_batch(codes: list[str], compact_date: str) -> dict[str, float]:
    """批量取一批标的的集中度90；返回 {code: value}。"""
    bare = [code.split(".")[0] for code in codes]
    base.fetch = _fetch  # paginate 内部调用模块级 fetch
    rows, _ = base.paginate("、".join(bare) + f" {FIELD_PREFIX}[{compact_date}]")
    by_code: dict[str, float] = {}
    for row in rows:
        code = row.get("股票代码")
        if not code:
            continue
        value = _value(row)
        if value is not None:
            by_code[code] = value
    return by_code


def attach(payload: dict, trade_date: str, *, fetch=fetch_batch) -> dict:
    """把集中度90 写进 payload 的 enrichments，返回契约。不写文件。"""
    compact_date = "".join(ch for ch in str(trade_date) if ch.isdigit())[:8]
    codes = payload.get("intersection") or payload.get("intersection_before_filters") or []
    enrichments = payload.setdefault("enrichments", {})

    if not codes:
        contract = {
            "status": "empty_pool",
            "as_of": trade_date,
            "source": SOURCE,
            "threshold": THRESHOLD,
            "coverage": {"requested": 0, "ok": 0, "unavailable": 0},
            "failed_batches": [],
        }
        payload["chip_concentration_contract"] = contract
        return contract

    values: dict[str, float] = {}
    failures: list[str] = []
    for start in range(0, len(codes), BATCH_SIZE):
        batch = codes[start:start + BATCH_SIZE]
        try:
            values.update(fetch(batch, compact_date))
        except (Exception, SystemExit) as exc:  # noqa: BLE001 单批失败隔离
            failures.append(f"batch {start // BATCH_SIZE + 1}: {str(exc)[:200]}")

    ok = 0
    unavailable = 0
    for code in codes:
        enr = enrichments.setdefault(code, {})
        value = values.get(code)
        if value is None:
            unavailable += 1
            enr["chip_concentration"] = {
                "status": "UNAVAILABLE",
                "concentration_90": None,
                "as_of": trade_date,
                "source": SOURCE,
                "reason": "iWenCai 未返回该标的集中度90",
            }
            continue
        ok += 1
        enr["chip_concentration"] = {
            "status": "ok",
            "concentration_90": round(value, 4),
            "as_of": trade_date,
            "source": SOURCE,
            "reason": None,
        }

    contract = {
        "status": "ok" if ok else "unavailable",
        "as_of": trade_date,
        "source": SOURCE,
        "threshold": THRESHOLD,
        "coverage": {"requested": len(codes), "ok": ok, "unavailable": unavailable},
        "failed_batches": failures,
    }
    payload["chip_concentration_contract"] = contract
    return contract


def main() -> int:
    payload = json.loads(base.DATA.read_text(encoding="utf-8"))
    trade_date = sys.argv[1] if len(sys.argv) > 1 else payload.get("data_as_of") or ""
    if trade_date:
        base.DATE = trade_date

    contract = attach(payload, trade_date)
    base.DATA.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    coverage = contract["coverage"]
    if contract["status"] == "empty_pool":
        print("chip concentration skipped: empty intersection", flush=True)
        return 0
    hits = sum(
        1
        for code in (payload.get("intersection") or [])
        if (payload["enrichments"].get(code, {}).get("chip_concentration") or {}).get("concentration_90") is not None
        and payload["enrichments"][code]["chip_concentration"]["concentration_90"] < THRESHOLD
    )
    print(
        f"chip concentration: pool={coverage['requested']} ok={coverage['ok']} "
        f"unavailable={coverage['unavailable']} below_{THRESHOLD:g}={hits} "
        f"failed_batches={len(contract['failed_batches'])}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

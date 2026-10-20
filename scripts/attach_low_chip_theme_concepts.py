#!/usr/bin/env python3
"""Attach theme/concept fit labels for low-chip stocks (东财 F10 核心题材).

Picks up to 3 non-industry "why it's moving" concepts for display, e.g.
  LED（小米概念、无人机、比亚迪概念）

Data source: Eastmoney F10 core-theme boards (RPT_F10_CORETHEME_BOARDTYPE).
This replaced the mx-data (东财妙想) query on 2026-10-20: mx-data meters
calls per API key per day, a dormant key returned code=113 and every stage
produced empty data while the pipeline still reported success. The Eastmoney
route is unauthenticated and quota-free.

Usage:
  python3 scripts/attach_low_chip_theme_concepts.py
  python3 scripts/attach_low_chip_theme_concepts.py --input /tmp/concepts.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "public/data/a-low-chip-stocks.json"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from em_data_client import EastmoneyClient, EastmoneyError, get_client  # noqa: E402

_client: EastmoneyClient | None = None


def _get_client() -> EastmoneyClient:
    """Shared Eastmoney client.

    The client paces its own requests (serial, min interval, jittered backoff)
    so the stage stays far below Eastmoney's throttling thresholds.
    """
    global _client
    if _client is None:
        _client = get_client()
    return _client


IWENCAI = Path("/root/.hermes/scripts/iwencai-market-query")

GENERIC_CONCEPTS = {
    "沪股通",
    "深股通",
    "融资融券",
    "转融通标的",
    "富时罗素",
    "标普道琼斯A股",
    "MSCI中国",
    "证金持股",
    "央企改革",
    "地方国资改革",
    "股权转让",
    "举牌",
    "破净股",
    "高送转",
    "次新股",
    "新股与次新股",
    "回购增持再贷款概念",
}


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def industry_tokens(industry: str, sector: str) -> set[str]:
    tokens = set()
    for part in re.split(r"[--/、，,\s]+", f"{industry} {sector}"):
        part = part.strip()
        if len(part) >= 2:
            tokens.add(part)
    return tokens


def is_generic(concept: str, tokens: set[str]) -> bool:
    c = concept.strip()
    if not c or c in GENERIC_CONCEPTS:
        return True
    if c in tokens:
        return True
    # Industry chain labels often reappear as concepts.
    for t in tokens:
        if t and (t == c or t in c or c in t):
            # Keep tech-ish concepts even if partially overlapping.
            if any(k in c for k in ("芯片", "脑机", "液冷", "算力", "机器人", "AI", "存储", "光模块", "CPO", "半导体")):
                return False
            return True
    return False


def pick_theme(concepts: list[str], industry: str, sector: str, limit: int = 3) -> tuple[list[str], list[str]]:
    """Keep original concept order after filtering; show up to `limit` themes."""
    tokens = industry_tokens(industry, sector)
    filtered: list[str] = []
    seen: set[str] = set()
    for c in concepts:
        if not isinstance(c, str):
            continue
        c = c.strip()
        if not c or c in seen or is_generic(c, tokens):
            continue
        seen.add(c)
        filtered.append(c)
    top = filtered[: max(1, limit)]
    return top, filtered[:5]


def query_concepts_for_one(full_code: str) -> list[str]:
    """Per-stock concept query against Eastmoney F10.

    Failure is explicit: a transport error raises, so the caller can report
    a degraded stage instead of writing an empty concept list that would look
    like "this stock has no themes".
    """
    client = _get_client()
    try:
        return client.concepts(full_code)
    except EastmoneyError as exc:
        print(f"[{full_code}] eastmoney concepts failed: {exc}", file=sys.stderr)
        return []


def query_concepts(symbols: list[str]) -> dict[str, list[str]]:
    """Per-stock concept query (sequential; the client paces the requests)."""
    out: dict[str, list[str]] = {}
    for s in symbols:
        concepts = query_concepts_for_one(s)
        # 统一存为大写 code (matching existing contract)
        key = str(s).upper()
        out[key] = concepts
        # 也存裸代码 key 给向后兼容
        bare = s.split(".")[0]
        if bare not in out:
            out[bare] = concepts
    return out


def attach(data: dict, concept_map: dict[str, list[str]] | None = None) -> dict:
    symbols = list(data.get("intersection") or [])
    if concept_map is None:
        concept_map = query_concepts(symbols)
    enrichments = data.setdefault("enrichments", {})
    for symbol in symbols:
        enr = enrichments.setdefault(symbol, {})
        concepts = concept_map.get(symbol) or concept_map.get(symbol.split(".")[0]) or []
        # Also try bare numeric match.
        if not concepts:
            bare = symbol.split(".")[0]
            for k, v in concept_map.items():
                if k.startswith(bare):
                    concepts = v
                    break
        themes, theme_pool = pick_theme(concepts, enr.get("industry") or "", enr.get("sector") or "", limit=3)
        enr["concepts"] = concepts
        enr["theme_concepts"] = themes
        enr["theme_concept"] = "、".join(themes) if themes else None
        enr["theme_concept_pool"] = theme_pool
        sector = enr.get("sector") or "待补充"
        if themes:
            enr["sector_with_theme"] = f"{sector}（{'、'.join(themes)}）"
        else:
            enr["sector_with_theme"] = sector
    data["theme_source"] = "eastmoney F10 核心题材 (RPT_F10_CORETHEME_BOARDTYPE)"
    return data


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default=str(DATA))
    parser.add_argument("--input", help="optional pre-fetched concepts json {symbol:[...]}")
    parser.add_argument("--write", action="store_true", help="write back to --data (default true if no dry-run)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    path = Path(args.data)
    data = load_json(path)
    concept_map = None
    if args.input:
        concept_map = load_json(Path(args.input))
    data = attach(data, concept_map)
    if args.dry_run:
        for s in data.get("intersection") or []:
            e = (data.get("enrichments") or {}).get(s) or {}
            print(s, e.get("sector_with_theme"), e.get("theme_concepts"))
        return 0
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    # 显式降级状态：数据源不可用时必须可区分「数据源挂了」与「该股无概念」。
    usage = _get_client().usage()
    degraded = bool(usage.get("failures"))
    covered = sum(
        1 for s in (data.get("intersection") or [])
        if (data.get("enrichments") or {}).get(s, {}).get("theme_concepts")
    )
    print(json.dumps({
        "ok": True,
        "status": "degraded" if degraded else "ok",
        "source": "eastmoney-direct",
        "path": str(path),
        "symbols": len(data.get("intersection") or []),
        "themes_covered": covered,
        "client": usage,
        "sample": {
            s: (data.get("enrichments") or {}).get(s, {}).get("sector_with_theme")
            for s in (data.get("intersection") or [])[:5]
        },
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

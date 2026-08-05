#!/usr/bin/env python3
"""Attach theme/concept fit labels for low-chip stocks (同花顺所属概念).

Picks a non-industry "why it's moving" concept for display, e.g.
  商业物业经营（芯片概念）

Usage:
  python3 scripts/attach_low_chip_theme_concepts.py
  python3 scripts/attach_low_chip_theme_concepts.py --input /tmp/concepts.json
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "public/data/a-low-chip-stocks.json"
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


def rank_key(concept: str) -> tuple:
    hot = any(k in concept for k in ("芯片", "脑机", "液冷", "算力", "机器人", "AI", "存储", "光模块", "CPO", "半导体", "创新药", "商业航天"))
    has_concept = "概念" in concept
    return (0 if hot else 1, 0 if has_concept else 1, len(concept), concept)


def pick_theme(concepts: list[str], industry: str, sector: str) -> tuple[str | None, list[str]]:
    tokens = industry_tokens(industry, sector)
    filtered = [c for c in concepts if isinstance(c, str) and not is_generic(c, tokens)]
    filtered = sorted(dict.fromkeys(filtered), key=rank_key)
    return (filtered[0] if filtered else None, filtered[:5])


def query_concepts(symbols: list[str]) -> dict[str, list[str]]:
    if not symbols:
        return {}
    if not IWENCAI.exists():
        raise SystemExit(f"missing iWenCai wrapper: {IWENCAI}")
    # Batch by codes for stable holder fields.
    codes = ",".join(s.split(".")[0] for s in symbols)
    q = f"{codes} 所属概念 热门概念"
    proc = subprocess.run(
        [str(IWENCAI), "-q", q, "--limit", str(max(20, len(symbols))), "--timeout", "60"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise SystemExit(f"iWenCai failed: {proc.stderr or proc.stdout}")
    payload = json.loads(proc.stdout)
    out: dict[str, list[str]] = {}
    for row in payload.get("datas") or []:
        code = str(row.get("股票代码") or row.get("code") or "").upper()
        if not code:
            continue
        concepts = row.get("所属概念") or []
        if isinstance(concepts, str):
            concepts = [x.strip() for x in re.split(r"[、,，/|]", concepts) if x.strip()]
        out[code] = [str(c) for c in concepts if c]
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
        theme, themes = pick_theme(concepts, enr.get("industry") or "", enr.get("sector") or "")
        enr["concepts"] = concepts
        enr["theme_concept"] = theme
        enr["theme_concepts"] = themes
        if theme:
            sector = enr.get("sector") or "待补充"
            enr["sector_with_theme"] = f"{sector}（{theme}）"
        else:
            enr["sector_with_theme"] = enr.get("sector") or "待补充"
    data["theme_source"] = "iWenCai 所属概念 / 热门概念筛选"
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
    print(json.dumps({
        "ok": True,
        "path": str(path),
        "symbols": len(data.get("intersection") or []),
        "sample": {
            s: (data.get("enrichments") or {}).get(s, {}).get("sector_with_theme")
            for s in (data.get("intersection") or [])[:5]
        },
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

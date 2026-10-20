#!/usr/bin/env python3
"""Backfill theme concepts into historical low-chip snapshots.

Why this exists
---------------
The three enrichment stages used mx-data until 2026-10-20. Its API key sat in
a dormant tier (10 calls/day, exhausted), so every snapshot from 2026-09-03
onward was archived with zero concept coverage. The stages now use the
Eastmoney F10 direct route, which has no quota, so the gap can be filled.

IMPORTANT semantic caveat
-------------------------
Eastmoney returns each stock's CURRENT concept boards. Writing them into a
past snapshot means the concept field for 2026-09-10 shows what the boards
look like today, not what they were on that day. Backfilled rows are therefore
tagged:

  enrichments[code]["theme_backfilled"] = True
  enrichments[code]["theme_backfilled_at"] = "<run timestamp>"
  snapshot["theme_backfill"] = {"applied_at": ..., "source": ...}

so the data can always be told apart from genuinely archived history. Do not
remove these markers.

Usage:
  python3 scripts/backfill_low_chip_concepts_history.py --dry-run
  python3 scripts/backfill_low_chip_concepts_history.py
  python3 scripts/backfill_low_chip_concepts_history.py --since 2026-09-01
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "public/data/low-chip-history"
CN = ZoneInfo("Asia/Shanghai")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from em_data_client import EastmoneyClient, EastmoneyError, get_client  # noqa: E402
from attach_low_chip_theme_concepts import pick_theme  # noqa: E402

DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.json$")


def snapshot_files(since: str | None = None) -> list[tuple[str, Path]]:
    """Standard YYYY-MM-DD.json snapshots only.

    Files like `2026-09-03.threshold-2.5.json` are variant experiments, not
    the canonical archive, so they are skipped.
    """
    out: list[tuple[str, Path]] = []
    for path_str in sorted(glob.glob(str(HISTORY / "2*.json"))):
        path = Path(path_str)
        match = DATE_RE.match(path.name)
        if not match:
            continue
        day = match.group(1)
        if since and day < since:
            continue
        out.append((day, path))
    return out


def missing_codes(payload: dict) -> list[str]:
    """Codes in this snapshot whose theme_concepts are absent."""
    inter = payload.get("intersection") or []
    enr = payload.get("enrichments") or {}
    return [c for c in inter if not (enr.get(c) or {}).get("theme_concepts")]


def collect_plan(since: str | None) -> tuple[list[tuple[str, Path, list[str]]], list[str]]:
    plan: list[tuple[str, Path, list[str]]] = []
    all_codes: dict[str, None] = {}
    for day, path in snapshot_files(since):
        payload = json.loads(path.read_text(encoding="utf-8"))
        codes = missing_codes(payload)
        if not codes:
            continue
        plan.append((day, path, codes))
        for code in codes:
            all_codes[code] = None
    return plan, list(all_codes)


def fetch_concepts(codes: list[str]) -> tuple[dict[str, list[str]], list[str]]:
    """Fetch concepts for each unique code once (serial, paced by the client)."""
    client = get_client()
    cache: dict[str, list[str]] = {}
    failed: list[str] = []
    for i, code in enumerate(codes, 1):
        try:
            cache[code] = client.concepts(code)
        except EastmoneyError as exc:
            print(f"[{i}/{len(codes)}] {code} FAILED: {exc}", file=sys.stderr)
            failed.append(code)
            continue
        if i % 25 == 0 or i == len(codes):
            print(f"  fetched {i}/{len(codes)}", file=sys.stderr)
    return cache, failed


def apply_to_snapshot(
    payload: dict,
    codes: list[str],
    cache: dict[str, list[str]],
    applied_at: str,
) -> int:
    """Write concepts + derived themes into one snapshot. Returns rows changed."""
    enr = payload.setdefault("enrichments", {})
    changed = 0
    for code in codes:
        concepts = cache.get(code)
        if not concepts:
            continue
        rec = enr.setdefault(code, {})
        themes, theme_pool = pick_theme(
            concepts,
            rec.get("industry") or "",
            rec.get("sector") or "",
            limit=3,
        )
        rec["concepts"] = concepts
        rec["theme_concepts"] = themes
        rec["theme_concept"] = "、".join(themes) if themes else None
        rec["theme_concept_pool"] = theme_pool
        sector = rec.get("sector") or "待补充"
        rec["sector_with_theme"] = (
            f"{sector}（{'、'.join(themes)}）" if themes else sector
        )
        # Provenance markers: this concept list is today's state, not the
        # state on the snapshot date.
        rec["theme_backfilled"] = True
        rec["theme_backfilled_at"] = applied_at
        changed += 1
    payload["theme_source"] = "eastmoney F10 核心题材 (RPT_F10_CORETHEME_BOARDTYPE)"
    payload["theme_backfill"] = {
        "applied_at": applied_at,
        "source": "eastmoney-direct",
        "note": (
            "Concept boards reflect Eastmoney's current classification, "
            "backfilled after the mx-data quota outage. See "
            "theme_backfilled per row."
        ),
        "rows": changed,
    }
    return changed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", help="only snapshots on/after this date")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, help="process at most N snapshots")
    args = parser.parse_args()

    plan, all_codes = collect_plan(args.since)
    if args.limit:
        plan = plan[: args.limit]
        needed = {c for _, _, codes in plan for c in codes}
        all_codes = [c for c in all_codes if c in needed]

    if not plan:
        print(json.dumps({"status": "nothing-to-do"}, ensure_ascii=False))
        return 0

    print(
        json.dumps(
            {
                "snapshots": len(plan),
                "unique_codes": len(all_codes),
                "est_seconds": round(len(all_codes) * 0.4, 1),
            },
            ensure_ascii=False,
        ),
        file=sys.stderr,
    )
    if args.dry_run:
        print(json.dumps({
            "status": "dry-run",
            "snapshots": [{"date": d, "missing": len(c)} for d, _, c in plan],
            "unique_codes": len(all_codes),
        }, ensure_ascii=False))
        return 0

    cache, failed = fetch_concepts(all_codes)
    applied_at = datetime.now(CN).isoformat(timespec="seconds")

    total_rows = 0
    touched = 0
    for day, path, codes in plan:
        payload = json.loads(path.read_text(encoding="utf-8"))
        changed = apply_to_snapshot(payload, codes, cache, applied_at)
        if not changed:
            continue
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        tmp.replace(path)
        touched += 1
        total_rows += changed

    usage = get_client().usage()
    print(json.dumps({
        "status": "degraded" if failed else "ok",
        "snapshots_planned": len(plan),
        "snapshots_written": touched,
        "rows_filled": total_rows,
        "unique_codes": len(all_codes),
        "fetched": len(cache),
        "failed": len(failed),
        "failed_codes": failed[:20],
        "client": usage,
        "applied_at": applied_at,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

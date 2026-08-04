#!/usr/bin/env python3
"""Archive the current low-chip snapshot and refresh the history index.

Usage:
  python3 scripts/archive_low_chip_snapshot.py
  python3 scripts/archive_low_chip_snapshot.py --input public/data/a-low-chip-stocks.json

Writes:
  public/data/low-chip-history/YYYY-MM-DD.json
  public/data/low-chip-history-index.json
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "public/data/a-low-chip-stocks.json"
HISTORY_DIR = ROOT / "public/data/low-chip-history"
INDEX = ROOT / "public/data/low-chip-history-index.json"
CN = ZoneInfo("Asia/Shanghai")


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict, pretty: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if pretty:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    else:
        path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def archive_snapshot(input_path: Path) -> dict:
    data = load_json(input_path)
    day = str(data.get("data_as_of") or "")[:10]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        raise SystemExit(f"invalid data_as_of in {input_path}: {data.get('data_as_of')!r}")

    payload = dict(data)
    payload["archive"] = True
    payload["data_as_of"] = day
    out = HISTORY_DIR / f"{day}.json"
    write_json(out, payload, pretty=False)

    # rebuild index from all archive files
    items = []
    for path in sorted(HISTORY_DIR.glob("????-??-??.json"), reverse=True):
        try:
            snap = load_json(path)
        except Exception:
            continue
        d = path.stem
        items.append(
            {
                "date": d,
                "href": f"/data/low-chip-history/{d}.json",
                "intersection_count": len(snap.get("intersection") or []),
                "counts": snap.get("counts") or {},
                "threshold": snap.get("threshold"),
            }
        )
    index = {
        "schema_version": "a-low-chip-history-index-v1",
        "generated_at": datetime.now(CN).isoformat(timespec="seconds"),
        "latest": items[0]["date"] if items else day,
        "dates": [item["date"] for item in items],
        "items": items,
    }
    write_json(INDEX, index, pretty=True)
    return {"date": day, "archive": str(out.relative_to(ROOT)), "index": str(INDEX.relative_to(ROOT)), "dates": index["dates"]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    args = parser.parse_args()
    result = archive_snapshot(Path(args.input))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()

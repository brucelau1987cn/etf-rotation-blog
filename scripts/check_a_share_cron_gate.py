#!/usr/bin/env python3
"""Deterministic preflight gate for A-share ETF article cron stages."""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import sqlite3
import subprocess
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
POOL = ROOT / "public/data/etf-garden-pool.json"
DB = ROOT / "data/local/etf-compass.db"
CN = ZoneInfo("Asia/Shanghai")
EXPECTED_FORMAL = 91
MINIMUM_COVERAGE = 82
# Canonical stage keys. "07:30" remains accepted as a legacy alias of the
# 08:30 pre-open plan so old prompts/tests keep working during migration.
STAGE_ORDER = {"08:30": 1, "07:30": 1, "11:30": 2, "14:30": 3, "22:00": 4}
WINDOWS = {
    "08:30": (time(7, 0), time(9, 20)),
    "07:30": (time(7, 0), time(9, 20)),
    "11:30": (time(11, 30), time(12, 59, 59)),
    "14:30": (time(14, 25), time(15, 0)),
    "22:00": (time(15, 15), time(23, 59, 59)),
}


@dataclass
class GateInput:
    stage: str
    now: datetime
    trading_day: bool | None
    pending_publish: bool
    article_stage_rank: int
    pool_count: int
    valid_count: int
    quote_date: str | None
    qfq_date: str | None
    qfq_coverage: int


def stage_rank(value: str | None) -> int:
    text = value or ""
    # Prefer longer/more specific keys first so "08:30盘前版" ranks before
    # any residual "07:30" text that may still appear in old bodies.
    for key, rank in sorted(STAGE_ORDER.items(), key=lambda item: -len(item[0])):
        if key in text:
            return rank
    if "盘前" in text or "早盘" in text:
        return 1
    if "上午收盘" in text:
        return 2
    if "尾盘" in text or "下午收盘" in text:
        return 3
    if "夜间最终" in text:
        return 4
    return 0


def evaluate_gate(data: GateInput) -> tuple[str, str]:
    target = STAGE_ORDER[data.stage]
    if data.trading_day is False:
        return "idempotent", "exchange calendar is closed"
    if data.trading_day is None:
        return "blocked", "exchange calendar unavailable"
    if data.article_stage_rank >= target and not (data.stage == "22:00" and data.pending_publish):
        return "idempotent", "article stage already complete"
    start, end = WINDOWS[data.stage]
    current = data.now.timetz().replace(tzinfo=None)
    if not (start <= current <= end):
        return "blocked", f"outside {data.stage} execution window"
    if data.pool_count != EXPECTED_FORMAL or data.valid_count < MINIMUM_COVERAGE:
        return "blocked", f"formal pool coverage {data.valid_count}/{data.pool_count}"
    today = data.now.date().isoformat()
    if data.stage in {"11:30", "14:30", "22:00"} and data.quote_date != today:
        return "blocked", f"quote date {data.quote_date} differs from {today}"
    if data.stage == "22:00" and (data.qfq_date != today or data.qfq_coverage < MINIMUM_COVERAGE):
        return "blocked", f"final qfq {data.qfq_date} coverage {data.qfq_coverage}"
    return "run", "all mandatory gates passed"


def read_article_stage(day: str) -> str | None:
    path = ROOT / f"src/content/blog/{day}.md"
    if not path.exists():
        return None
    match = re.search(r"^stage:\s*['\"]?([^'\"\n]+)", path.read_text(encoding="utf-8"), re.MULTILINE)
    return match.group(1).strip() if match else None


def quote_timestamp() -> str | None:
    request = urllib.request.Request("https://qt.gtimg.cn/q=sh510300", headers={"User-Agent": "Mozilla/5.0"})
    raw = urllib.request.urlopen(request, timeout=15).read().decode("gbk", errors="replace")
    parts = raw.split("~")
    value = parts[30] if len(parts) > 30 else ""
    return value if re.fullmatch(r"\d{14}", value) else None


def is_trading_day(day: str) -> bool | None:
    try:
        import baostock as bs  # type: ignore[import-not-found]
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            login = bs.login()
            if login.error_code != "0":
                return None
            result = bs.query_trade_dates(start_date=day, end_date=day)
            row = result.get_row_data() if result.error_code == "0" and result.next() else None
            bs.logout()
        return bool(row and len(row) > 1 and row[1] == "1")
    except Exception:
        return None


def pending_public_changes(day: str) -> bool:
    paths = [
        "public/data/model-lab/a-share-shadow.json",
        "public/data/etf-garden-pool.json",
        "public/data/garden-recommendations.json",
        "public/data/a-share-mid-macro.json",
        f"src/content/blog/{day}.md",
    ]
    result = subprocess.run(["git", "diff", "--quiet", "--", *paths], cwd=ROOT, check=False)
    return result.returncode == 1


def qfq_state() -> tuple[str | None, int]:
    with sqlite3.connect(DB) as db:
        latest = db.execute(
            "SELECT max(trade_date) FROM daily_bars WHERE adjustment='qfq' AND is_final=1"
        ).fetchone()[0]
        coverage = db.execute(
            "SELECT count(distinct symbol) FROM daily_bars WHERE adjustment='qfq' AND is_final=1 AND trade_date=?",
            (latest,),
        ).fetchone()[0]
    return latest, int(coverage)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=STAGE_ORDER, required=True)
    parser.add_argument("--now", help="ISO timestamp for deterministic tests")
    parser.add_argument("--quote-timestamp", help="YYYYMMDDhhmmss override")
    parser.add_argument("--article-stage", help="frontmatter stage override")
    args = parser.parse_args()
    now = datetime.fromisoformat(args.now).astimezone(CN) if args.now else datetime.now(CN)
    day = now.date().isoformat()
    payload: dict[str, Any] = json.loads(POOL.read_text(encoding="utf-8"))
    summary = payload.get("summary") or {}
    qfq_date, qfq_coverage = qfq_state()
    timestamp = args.quote_timestamp or quote_timestamp()
    quote_date = f"{timestamp[:4]}-{timestamp[4:6]}-{timestamp[6:8]}" if timestamp else None
    article_stage = args.article_stage if args.article_stage is not None else read_article_stage(day)
    gate = GateInput(
        stage=args.stage,
        now=now,
        trading_day=is_trading_day(day),
        pending_publish=pending_public_changes(day),
        article_stage_rank=stage_rank(article_stage),
        pool_count=int(summary.get("universe_count") or 0),
        valid_count=int(summary.get("valid_count") or 0),
        quote_date=quote_date,
        qfq_date=qfq_date,
        qfq_coverage=qfq_coverage,
    )
    decision, reason = evaluate_gate(gate)
    result = {
        "decision": decision,
        "reason": reason,
        "article_stage": article_stage,
        "quote_timestamp": timestamp,
        **{k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in asdict(gate).items()},
    }
    print(json.dumps(result, ensure_ascii=False))
    return 2 if decision == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())

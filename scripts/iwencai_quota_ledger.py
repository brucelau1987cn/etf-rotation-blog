#!/usr/bin/env python3
"""iWenCai 每日额度用量统计与预检。

背景
----
`iwencai-skill-run` 只维护一个永不重置的累计计数器
(`~/.hermes/state/iwencai-key-index`)，没有按天的用量视图。
后果：调用方无法知道「今天已用多少、还剩多少」，只能撞墙才发现额度耗尽，
而额度耗尽会以各种误导性错误拖垮多条 cron 链路（2026-08-24 事故）。

本脚本按天记录用量，提供三个能力：
  1. `--record N`  记录本次运行消耗了 N 次调用
  2. `--report`    查看今日/近期用量
  3. `--check N`   预检：本次预计消耗 N 次，若会超出安全水位则非零退出

额度口径：优先 key 1000 次/天，其余 key 各 100 次/天；10 keys 总计 1900 次/天。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

CN = ZoneInfo("Asia/Shanghai")
STATE = Path.home() / ".hermes" / "state" / "iwencai-usage.json"
KEY_INDEX = Path.home() / ".hermes" / "state" / "iwencai-key-index"

PRIMARY_KEY_LIMIT = 1000
REGULAR_KEY_LIMIT = 100
# 安全水位：留 15% 余量给夜间链路与重试，避免把额度用到刚好卡死。
SAFETY_RATIO = 0.85


def key_count() -> int:
    raw = os.environ.get("IWENCAI_APIKEYS")
    if raw:
        try:
            return len(json.loads(raw)) or 1
        except (json.JSONDecodeError, TypeError):
            pass
    declared = os.environ.get("IWENCAI_APIKEY_COUNT")
    if declared and declared.isdigit():
        return int(declared)
    return 10


def key_limits() -> list[int]:
    count = key_count()
    if count <= 0:
        return []
    return [PRIMARY_KEY_LIMIT] + [REGULAR_KEY_LIMIT] * (count - 1)


def daily_capacity() -> int:
    return sum(key_limits())


def today() -> str:
    return dt.datetime.now(CN).date().isoformat()


def load() -> dict:
    if not STATE.exists():
        return {"days": {}}
    try:
        data = json.loads(STATE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"days": {}}
    if not isinstance(data.get("days"), dict):
        return {"days": {}}
    return data


def save(data: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    # 只保留最近 60 天，避免无界增长
    days = data.get("days") or {}
    for key in sorted(days)[:-60]:
        days.pop(key, None)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, STATE)


def used_today(data: dict) -> int:
    entry = (data.get("days") or {}).get(today()) or {}
    return int(entry.get("calls") or 0)


def record(count: int, stage: str) -> dict:
    data = load()
    days = data.setdefault("days", {})
    entry = days.setdefault(today(), {"calls": 0, "stages": {}})
    entry["calls"] = int(entry.get("calls") or 0) + count
    stages = entry.setdefault("stages", {})
    stages[stage] = int(stages.get(stage) or 0) + count
    entry["updated_at"] = dt.datetime.now(CN).isoformat(timespec="seconds")
    save(data)
    return entry


def report(data: dict) -> dict:
    cap = daily_capacity()
    used = used_today(data)
    days = data.get("days") or {}
    recent = []
    for day in sorted(days)[-7:]:
        recent.append({"date": day, "calls": days[day].get("calls", 0)})
    return {
        "date": today(),
        "keys": key_count(),
        "key_limits": key_limits(),
        "capacity": cap,
        "used": used,
        "remaining": cap - used,
        "safety_limit": int(cap * SAFETY_RATIO),
        "stages_today": (days.get(today()) or {}).get("stages") or {},
        "recent": recent,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--record", type=int, metavar="N",
                        help="record N consumed calls for today")
    parser.add_argument("--stage", default="unspecified",
                        help="label for the consuming stage (used with --record)")
    parser.add_argument("--check", type=int, metavar="N",
                        help="pre-flight: exit non-zero if N more calls would "
                             "exceed the safety limit")
    parser.add_argument("--report", action="store_true", help="print usage report")
    args = parser.parse_args()

    if args.record is not None:
        entry = record(args.record, args.stage)
        info = report(load())
        print(json.dumps({"recorded": args.record, "stage": args.stage,
                          "used_today": entry["calls"],
                          "remaining": info["remaining"]}, ensure_ascii=False))
        return 0

    if args.check is not None:
        info = report(load())
        projected = info["used"] + args.check
        payload = {**info, "projected": projected, "planned": args.check}
        if projected > info["safety_limit"]:
            payload["verdict"] = "BLOCKED"
            payload["reason"] = (
                f"projected {projected} calls exceeds safety limit "
                f"{info['safety_limit']} (capacity {info['capacity']})")
            print(json.dumps(payload, ensure_ascii=False))
            return 1
        payload["verdict"] = "OK"
        print(json.dumps(payload, ensure_ascii=False))
        return 0

    info = report(load())
    print(json.dumps(info, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

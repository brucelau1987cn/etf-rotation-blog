#!/usr/bin/env python3
"""Process queued ETF Compass uploads with a transparent daily provisional backtest."""
from __future__ import annotations

import csv
import datetime as dt
import json
import math
import re
import subprocess
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DB_NAME = "etf-compass-auth"
CLI = ["npx", "-y", "stock-api@2.7.3"]
OPS = {"准备种花": "candidate", "种花": "伏击", "准备摘花": "止盈观察", "摘花": "兑现"}


def d1(sql: str) -> list[dict[str, Any]]:
    command_env = dict(__import__('os').environ)
    global_email = command_env.get('CF_EMAIL', '')
    command_env.pop('CF_API_TOKEN', None)
    command_env.pop('CF_EMAIL', None)
    if command_env.get('CF_GLOBAL_KEY') and global_email:
        command_env['CLOUDFLARE_API_KEY'] = command_env['CF_GLOBAL_KEY']
        command_env['CLOUDFLARE_EMAIL'] = global_email
    proc = subprocess.run(["npx", "wrangler", "d1", "execute", DB_NAME, "--remote", "--json", "--command", sql], cwd=ROOT, text=True, capture_output=True, timeout=120, env=command_env)
    if proc.returncode:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "D1 command failed")
    payload = json.loads(proc.stdout)
    if not payload or not payload[0].get("success", False):
        raise RuntimeError(json.dumps(payload, ensure_ascii=False))
    return payload[0].get("results", [])


def sql(value: Any) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def market_code(code: str) -> str:
    code = str(code).zfill(6)
    return ("SH" if code.startswith(("5", "6", "68")) else "SZ") + code


def fetch(code: str, adjust: str) -> list[dict[str, Any]]:
    proc = subprocess.run(CLI + ["get-klines", market_code(code), "--period", "day", "--count", "240", "--adjust", adjust, "--source", "auto"], cwd=ROOT, text=True, capture_output=True, timeout=90)
    if proc.returncode:
        return []
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return []
    rows = payload if isinstance(payload, list) else payload.get("klines") or payload.get("data") or payload.get("rows") or []
    out = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("date"):
            continue
        try:
            out.append({"date": str(row["date"]), "open": float(row["open"]), "high": float(row["high"]), "low": float(row["low"]), "close": float(row["close"])})
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(out, key=lambda x: x["date"])


def pct(a: float | None, b: float | None) -> float | None:
    if a is None or b in (None, 0) or not math.isfinite(a) or not math.isfinite(b):
        return None
    return round((a / b - 1) * 100, 2)


def csv_rows(text: str) -> list[dict[str, str]]:
    reader = csv.DictReader(text.lstrip("\ufeff").splitlines())
    return [{str(k).strip(): str(v or "").strip() for k, v in row.items()} for row in reader]


def infer_date(job: dict[str, Any]) -> str | None:
    value = str(job.get("trade_date") or "")[:10]
    if re.fullmatch(r"20\d{2}-\d{2}-\d{2}", value):
        return value
    match = re.search(r"20\d{2}[.-]\d{2}[.-]\d{2}", str(job.get("filename") or ""))
    return match.group(0).replace(".", "-") if match else None


def backtest(job: dict[str, Any]) -> dict[str, Any]:
    trade_date = infer_date(job)
    if not trade_date:
        raise ValueError("无法从文件名识别交易日期")
    rows = csv_rows(job["csv_text"])
    records = []
    cache: dict[str, tuple[list[dict[str, Any]], list[dict[str, Any]]]] = {}
    for item in rows:
        code = item.get("代码", "").zfill(6)
        operation = item.get("操作", "")
        if operation not in OPS:
            continue
        try:
            target = float(item.get("目标", ""))
        except ValueError:
            continue
        if code not in cache:
            none_rows = fetch(code, "none")
            qfq_rows = fetch(code, "qfq")
            cache[code] = (none_rows, qfq_rows)
        none_rows, qfq_rows = cache[code]
        day = next((r for r in none_rows if r["date"] == trade_date), None)
        qday = next((r for r in qfq_rows if r["date"] == trade_date), None)
        next_q = next((r for r in qfq_rows if r["date"] > trade_date), None)
        if not day or not qday:
            records.append({"code": code, "name": item.get("名称"), "operation": operation, "target": target, "data_status": "missing_daily_data"})
            continue
        target_hit = day["low"] <= target
        confirmed = target_hit and day["close"] >= target
        next_return = pct(next_q["close"], qday["close"]) if next_q else None
        category = OPS[operation]
        direction_hit = None
        if next_return is not None:
            direction_hit = next_return > 0 if category in {"candidate", "伏击"} else next_return < 0
        records.append({"code": code, "name": item.get("名称"), "operation": operation, "category": category, "target": target, "trade_date": trade_date, "target_hit": target_hit, "close_confirmed": confirmed if category == "伏击" else None, "low": day["low"], "high": day["high"], "close": day["close"], "next_date": next_q["date"] if next_q else None, "next_close": next_q["close"] if next_q else None, "next_return_pct": next_return, "next_direction_hit": direction_hit, "data_status": "ok"})
    valid = [r for r in records if r.get("data_status") == "ok"]
    def group(category: str) -> dict[str, Any]:
        part = [r for r in valid if r.get("category") == category]
        returns = [r["next_return_pct"] for r in part if r.get("next_return_pct") is not None]
        hits = [r["next_direction_hit"] for r in part if r.get("next_direction_hit") is not None]
        return {"count": len(part), "target_hit": sum(r["target_hit"] for r in part), "confirmed": sum(bool(r.get("close_confirmed")) for r in part), "t1_samples": len(hits), "t1_hit": sum(hits), "t1_hit_rate": round(sum(hits) / len(hits) * 100, 1) if hits else None, "avg_t1_return_pct": round(sum(returns) / len(returns), 2) if returns else None}
    return {"version": "daily_provisional_v1", "methodology": "日线预回测；目标触发使用未复权最低价≤目标；T+1使用前复权收盘；5分钟触价/站回待增强处理。", "trade_date": trade_date, "filename": job["filename"], "row_count": len(rows), "valid_count": len(valid), "missing_count": len(records) - len(valid), "by_category": {key: group(key) for key in ["candidate", "伏击", "止盈观察", "兑现"]}, "records": records, "generated_at": dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat()}


def main() -> int:
    queued = d1("SELECT id,filename,trade_date,csv_text FROM upload_jobs WHERE status='queued' ORDER BY id LIMIT 1")
    if not queued:
        return 0
    job = queued[0]
    d1(f"UPDATE upload_jobs SET status='processing', error_message=NULL WHERE id={int(job['id'])} AND status='queued'")
    try:
        result = backtest(job)
        encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        d1(f"UPDATE upload_jobs SET status='completed', result_json={sql(encoded)}, completed_at=datetime('now') WHERE id={int(job['id'])}")
        print(json.dumps({"job_id": job["id"], "status": "completed", "trade_date": result["trade_date"], "valid_count": result["valid_count"]}, ensure_ascii=False))
    except Exception as exc:
        message = str(exc)[:500]
        d1(f"UPDATE upload_jobs SET status='failed', error_message={sql(message)}, completed_at=datetime('now') WHERE id={int(job['id'])}")
        print(json.dumps({"job_id": job["id"], "status": "failed", "error": message}, ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

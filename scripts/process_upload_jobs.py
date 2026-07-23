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
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DB_NAME = "etf-compass-auth"
CLI = ["npx", "-y", "stock-api@2.7.3"]
OPS = {"准备种花": "candidate", "种花": "伏击", "准备摘花": "止盈观察", "摘花": "兑现"}
D1_RETRY_DELAYS = (2, 6)
D1_TRANSIENT_ERRORS = ("fetch failed", "network", "econnreset", "etimedout", "timeout", "temporarily unavailable")


def is_transient_d1_error(message: str) -> bool:
    return any(marker in message.lower() for marker in D1_TRANSIENT_ERRORS)


def d1(sql: str) -> list[dict[str, Any]]:
    command_env = dict(__import__('os').environ)
    global_email = command_env.get('CF_EMAIL', '')
    command_env.pop('CF_API_TOKEN', None)
    command_env.pop('CF_EMAIL', None)
    if command_env.get('CF_GLOBAL_KEY') and global_email:
        command_env['CLOUDFLARE_API_KEY'] = command_env['CF_GLOBAL_KEY']
        command_env['CLOUDFLARE_EMAIL'] = global_email
    command = ["npx", "wrangler", "d1", "execute", DB_NAME, "--remote", "--json", "--command", sql]
    for attempt, delay in enumerate((*D1_RETRY_DELAYS, None), start=1):
        try:
            proc = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=120, env=command_env)
        except subprocess.TimeoutExpired as exc:
            message = f"D1 command timed out: {exc}"
        else:
            if not proc.returncode:
                try:
                    payload = json.loads(proc.stdout)
                except json.JSONDecodeError as exc:
                    raise RuntimeError(f"D1 returned invalid JSON: {exc}") from exc
                if payload and payload[0].get("success", False):
                    return payload[0].get("results", [])
                message = json.dumps(payload, ensure_ascii=False)
            else:
                message = proc.stderr.strip() or proc.stdout.strip() or "D1 command failed"
        if delay is None or not is_transient_d1_error(message):
            raise RuntimeError(message)
        time.sleep(delay)
    raise AssertionError(f"unreachable D1 retry state after {attempt} attempts")


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


def fetch_m5(code: str) -> list[dict[str, Any]]:
    """Fetch recent Tencent 5-minute bars.

    Tencent timestamps label the end of each bar, so the first afternoon bar is
    13:05.  The endpoint keeps only a recent rolling window; callers must keep
    the explicit daily fallback for older uploads.
    """
    symbol = market_code(code).lower()
    url = f"https://ifzq.gtimg.cn/appstock/app/kline/mkline?param={symbol},m5,,320"
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"})
        with urllib.request.urlopen(request, timeout=25) as response:
            payload = json.load(response)
        rows = payload.get("data", {}).get(symbol, {}).get("m5") or []
    except Exception:
        return []
    out = []
    for row in rows:
        try:
            stamp = str(row[0])
            out.append({"timestamp": stamp, "date": f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}", "time": stamp[8:12], "open": float(row[1]), "close": float(row[2]), "high": float(row[3]), "low": float(row[4])})
        except (IndexError, TypeError, ValueError):
            continue
    return sorted(out, key=lambda x: x["timestamp"])


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
    now_local = dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))
    records = []
    cache: dict[str, tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]] = {}
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
            m5_rows = fetch_m5(code)
            cache[code] = (none_rows, qfq_rows, m5_rows)
        none_rows, qfq_rows, m5_rows = cache[code]
        day = next((r for r in none_rows if r["date"] == trade_date), None)
        qday = next((r for r in qfq_rows if r["date"] == trade_date), None)
        next_q = next((r for r in qfq_rows if r["date"] > trade_date), None)
        if not day or not qday:
            pending = trade_date == now_local.date().isoformat() and now_local.time() < dt.time(15, 5)
            records.append({"code": code, "name": item.get("名称"), "operation": operation, "category": OPS[operation], "target": target, "granularity": "pending_intraday" if pending else None, "data_status": "pending_intraday" if pending else "missing_daily_data"})
            continue
        category = OPS[operation]
        buy_side = category in {"candidate", "伏击"}
        afternoon = [r for r in m5_rows if r["date"] == trade_date and r["time"] >= "1305"]
        has_final_bar = any(r["time"] >= "1500" for r in afternoon)
        if afternoon:
            low = min(r["low"] for r in afternoon)
            high = max(r["high"] for r in afternoon)
            close = afternoon[-1]["close"]
            target_hit = low <= target if buy_side else high >= target
            granularity = "m5_final" if has_final_bar else "m5_partial"
            confirmed = target_hit and close >= target if category == "伏击" and has_final_bar else None
        else:
            if trade_date == now_local.date().isoformat() and now_local.time() < dt.time(15, 5):
                records.append({"code": code, "name": item.get("名称"), "operation": operation, "category": category, "target": target, "trade_date": trade_date, "target_hit": None, "close_confirmed": None, "granularity": "pending_intraday", "strict_intraday": False, "afternoon_bar_count": 0, "next_date": None, "next_close": None, "next_return_pct": None, "next_direction_hit": None, "data_status": "pending_intraday"})
                continue
            low, high, close = day["low"], day["high"], day["close"]
            target_hit = low <= target if buy_side else high >= target
            granularity = "daily_fallback"
            confirmed = None
        next_return = pct(next_q["close"], qday["close"]) if next_q else None
        direction_hit = None
        if next_return is not None:
            direction_hit = next_return > 0 if category in {"candidate", "伏击"} else next_return < 0
        records.append({"code": code, "name": item.get("名称"), "operation": operation, "category": category, "target": target, "trade_date": trade_date, "target_hit": target_hit, "close_confirmed": confirmed if category == "伏击" else None, "granularity": granularity, "strict_intraday": granularity == "m5_final", "afternoon_bar_count": len(afternoon), "low": low, "high": high, "close": close, "next_date": next_q["date"] if next_q else None, "next_close": next_q["close"] if next_q else None, "next_return_pct": next_return, "next_direction_hit": direction_hit, "data_status": "ok"})
    valid = [r for r in records if r.get("data_status") == "ok"]
    def group(category: str) -> dict[str, Any]:
        part = [r for r in valid if r.get("category") == category]
        returns = [r["next_return_pct"] for r in part if r.get("next_return_pct") is not None]
        hits = [r["next_direction_hit"] for r in part if r.get("next_direction_hit") is not None]
        confirmation_rows = [r for r in part if r.get("close_confirmed") is not None]
        return {"count": len(part), "target_samples": len(part), "target_hit": sum(r["target_hit"] for r in part), "confirmation_samples": len(confirmation_rows), "confirmed": sum(bool(r["close_confirmed"]) for r in confirmation_rows), "t1_samples": len(hits), "t1_hit": sum(hits), "t1_hit_rate": round(sum(hits) / len(hits) * 100, 1) if hits else None, "avg_t1_return_pct": round(sum(returns) / len(returns), 2) if returns else None}
    quality = {key: sum(r.get("granularity") == key for r in records) for key in ["m5_final", "m5_partial", "daily_fallback", "pending_intraday"]}
    t1_pending = any(r.get("data_status") == "ok" and r.get("next_return_pct") is None for r in records)
    return {"version": "strict_intraday_v2", "methodology": "上午收盘名单仅使用13:00后5分钟行情验证目标；候场/伏击按最低价触及，止盈观察/兑现按最高价触及；伏击仅在15:00最终bar后计算收盘站回。历史5分钟缺失时保留日线粗略fallback且不计严格确认；T+1使用前复权收盘。", "trade_date": trade_date, "filename": job["filename"], "row_count": len(rows), "valid_count": len(valid), "missing_count": sum(r.get("data_status") == "missing_daily_data" for r in records), "pending_count": sum(r.get("data_status") == "pending_intraday" for r in records), "t1_pending": t1_pending, "data_quality": quality, "by_category": {key: group(key) for key in ["candidate", "伏击", "止盈观察", "兑现"]}, "records": records, "generated_at": now_local.isoformat()}


def main() -> int:
    queued = d1("""SELECT id,filename,trade_date,csv_text,status,result_json FROM upload_jobs
      WHERE status='queued'
         OR (status='waiting_close' AND (trade_date < date('now','+8 hours') OR time('now','+8 hours') >= '15:05:00'))
         OR (status='completed' AND trade_date < date('now','+8 hours') AND time('now','+8 hours') >= '15:05:00'
             AND CAST(strftime('%w','now','+8 hours') AS INTEGER) BETWEEN 1 AND 5
             AND json_extract(result_json,'$.version')='strict_intraday_v2'
             AND json_extract(result_json,'$.t1_pending')=1)
      ORDER BY CASE status WHEN 'queued' THEN 0 WHEN 'waiting_close' THEN 1 ELSE 2 END,id LIMIT 1""")
    if not queued:
        return 0
    job = queued[0]
    d1(f"UPDATE upload_jobs SET status='processing', error_message=NULL WHERE id={int(job['id'])}")
    try:
        result = backtest(job)
        encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        waiting_close = result["data_quality"].get("m5_partial", 0) > 0 or result["data_quality"].get("pending_intraday", 0) > 0
        status = "waiting_close" if waiting_close else "completed"
        completed = "NULL" if waiting_close else "datetime('now')"
        d1(f"UPDATE upload_jobs SET status={sql(status)}, result_json={sql(encoded)}, completed_at={completed} WHERE id={int(job['id'])}")
        print(json.dumps({"job_id": job["id"], "status": status, "trade_date": result["trade_date"], "valid_count": result["valid_count"], "t1_pending": result["t1_pending"]}, ensure_ascii=False))
    except Exception as exc:
        message = str(exc)[:500]
        d1(f"UPDATE upload_jobs SET status='failed', error_message={sql(message)}, completed_at=datetime('now') WHERE id={int(job['id'])}")
        print(json.dumps({"job_id": job["id"], "status": "failed", "error": message}, ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

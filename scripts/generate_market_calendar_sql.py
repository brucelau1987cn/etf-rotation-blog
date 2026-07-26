#!/usr/bin/env python3
"""Generate D1 SQL rows for CN A-share, Hong Kong, and US exchange calendars."""

from __future__ import annotations

import argparse
from datetime import date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import exchange_calendars as xcals

MARKETS = {
    "CN_A": ("XSHG", ZoneInfo("Asia/Shanghai"), "exchange_calendars:XSHG"),
    "HK": ("XHKG", ZoneInfo("Asia/Hong_Kong"), "exchange_calendars:XHKG"),
    "US": ("XNYS", ZoneInfo("America/New_York"), "exchange_calendars:XNYS"),
}


def sql(value: str | None) -> str:
    if value is None:
        return "NULL"
    return "'" + value.replace("'", "''") + "'"


def local_iso(ts, tz: ZoneInfo) -> str | None:
    if ts is None or str(ts) == "NaT":
        return None
    return ts.to_pydatetime().astimezone(tz).isoformat(timespec="seconds")


def generate(start: date, end: date) -> str:
    statements: list[str] = []
    for market, (calendar_name, tz, source) in MARKETS.items():
        cal = xcals.get_calendar(calendar_name)
        schedule = cal.schedule.loc[start.isoformat() : end.isoformat()]
        sessions = {idx.date(): row for idx, row in schedule.iterrows()}
        day = start
        while day <= end:
            row = sessions.get(day)
            if row is None:
                values = [market, day.isoformat(), 0, None, None, None, None, "closed", "休市", source]
            else:
                open_at = local_iso(row.get("open"), tz)
                break_start = local_iso(row.get("break_start"), tz)
                break_end = local_iso(row.get("break_end"), tz)
                close_at = local_iso(row.get("close"), tz)
                regular_close = {"CN_A": "15:00:00", "HK": "16:00:00", "US": "16:00:00"}[market]
                close_time = close_at[11:19] if close_at else ""
                session_type = "early_close" if close_time and close_time != regular_close else "normal"
                note = "提前收市" if session_type == "early_close" else "正常交易"
                values = [market, day.isoformat(), 1, open_at, break_start, break_end, close_at, session_type, note, source]
            row_sql = "(" + ",".join(sql(v) if not isinstance(v, int) else str(v) for v in values) + ")"
            statements.append("""INSERT INTO market_calendar
  (market, trade_date, is_open, open_at, break_start_at, break_end_at, close_at, session_type, note, source)
VALUES
""" + row_sql + """
ON CONFLICT(market, trade_date) DO UPDATE SET
  is_open=excluded.is_open,
  open_at=excluded.open_at,
  break_start_at=excluded.break_start_at,
  break_end_at=excluded.break_end_at,
  close_at=excluded.close_at,
  session_type=excluded.session_type,
  note=excluded.note,
  source=excluded.source,
  updated_at=datetime('now');""")
            day += timedelta(days=1)
    return "\n".join(statements) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2026-01-01")
    parser.add_argument("--end", default="2026-12-31")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output)
    output.write_text(generate(date.fromisoformat(args.start), date.fromisoformat(args.end)), encoding="utf-8")
    print(f"wrote {output}")


if __name__ == "__main__":
    main()

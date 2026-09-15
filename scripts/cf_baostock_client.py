#!/usr/bin/env python3
"""Small stdlib client for the authenticated CF BaoStock gateway."""
from __future__ import annotations

import json
import math
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

CALENDAR_PATH = "/api/internal/v1/baostock/calendar"
KLINES_PATH = "/api/internal/v1/baostock/qfq"
MAX_KLINE_SYMBOLS = 5
MAX_KLINE_SPAN_DAYS = 366
DEFAULT_KLINE_FIELDS = ("date", "close", "volume", "amount", "turn", "tradestatus")
NUMERIC_KLINE_FIELDS = frozenset(("open", "high", "low", "close", "volume", "amount", "turn"))


class CFBaoStockError(RuntimeError):
    """Gateway configuration, transport, or contract failure."""


class CFBaoStockClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: float = 30,
        opener: Callable[..., Any] = urllib.request.urlopen,
    ) -> None:
        if not base_url:
            raise CFBaoStockError("CF BaoStock base URL is required")
        if not token:
            raise CFBaoStockError("CF BaoStock bearer token is required")
        parsed = urllib.parse.urlsplit(base_url)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise CFBaoStockError("CF BaoStock base URL must be an HTTPS origin")
        self.base_url = f"https://{parsed.netloc}"
        self._token = token
        self.timeout = timeout
        self._opener = opener

    @classmethod
    def from_env(cls, **kwargs: Any) -> "CFBaoStockClient":
        base_url = os.environ.get("CF_BAOSTOCK_BASE_URL", "").strip()
        token = os.environ.get("CF_BAOSTOCK_TOKEN", "").strip()
        if not base_url:
            raise CFBaoStockError("CF_BAOSTOCK_BASE_URL is required")
        if not token:
            raise CFBaoStockError("CF_BAOSTOCK_TOKEN is required")
        return cls(base_url, token, **kwargs)

    def _request(self, path: str, *, query: dict[str, str] | None = None, body: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"
        data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8") if body is not None else None
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
            "User-Agent": "ETF-Compass-CF-BaoStock/1.0",
        }
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method="POST" if data is not None else "GET")
        try:
            with self._opener(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise CFBaoStockError(f"CF BaoStock HTTP {exc.code}") from exc
        except (OSError, TimeoutError, urllib.error.URLError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CFBaoStockError(f"CF BaoStock request failed: {type(exc).__name__}") from exc
        if not isinstance(payload, dict):
            raise CFBaoStockError("CF BaoStock response root must be an object")
        if payload.get("ok") is not True:
            code = payload.get("code") or "unknown"
            raise CFBaoStockError(f"CF BaoStock gateway error: {code}")
        if payload.get("source") != "baostock":
            raise CFBaoStockError("CF BaoStock response source is invalid")
        return payload

    def calendar(self, start: str, end: str) -> list[dict[str, Any]]:
        payload = self._request(CALENDAR_PATH, query={"start": start, "end": end})
        rows = payload.get("records")
        if not isinstance(rows, list):
            raise CFBaoStockError("CF BaoStock calendar response lacks records")
        result: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict) or not row.get("date"):
                raise CFBaoStockError("CF BaoStock calendar row is invalid")
            value = row.get("is_trading_day")
            if value not in (0, 1, False, True):
                raise CFBaoStockError("CF BaoStock calendar value is invalid")
            result.append({"trade_date": str(row["date"]), "is_open": bool(value)})
        return result

    def is_trading_day(self, day: str) -> bool | None:
        rows = self.calendar(day, day)
        row = next((item for item in rows if item["trade_date"] == day), None)
        return row["is_open"] if row is not None else None

    def klines(
        self,
        symbols: list[str],
        start: str,
        end: str,
        *,
        adjust: str = "qfq",
        fields: tuple[str, ...] | list[str] = DEFAULT_KLINE_FIELDS,
    ) -> dict[str, list[dict[str, Any]]]:
        if not symbols or len(symbols) > MAX_KLINE_SYMBOLS:
            raise ValueError(f"CF BaoStock klines requires 1 to at most {MAX_KLINE_SYMBOLS} symbols")
        if len(set(symbols)) != len(symbols):
            raise ValueError("CF BaoStock klines requires unique symbols")
        if adjust != "qfq":
            raise ValueError("CF BaoStock first migration supports adjust=qfq")
        requested_fields = tuple(fields)
        if not requested_fields or "date" not in requested_fields or len(set(requested_fields)) != len(requested_fields):
            raise ValueError("CF BaoStock klines fields must be unique and include date")
        try:
            first_day = date.fromisoformat(start)
            last_day = date.fromisoformat(end)
        except ValueError as exc:
            raise ValueError("CF BaoStock klines dates must use YYYY-MM-DD") from exc
        if first_day > last_day:
            raise ValueError("CF BaoStock klines start must be on or before end")

        result = {symbol: [] for symbol in symbols}
        window_start = first_day
        while window_start <= last_day:
            window_end = min(window_start + timedelta(days=MAX_KLINE_SPAN_DAYS - 1), last_day)
            window = self._request_kline_window(
                symbols, window_start.isoformat(), window_end.isoformat(), requested_fields, adjust,
            )
            for symbol in symbols:
                result[symbol].extend(window[symbol])
            window_start = window_end + timedelta(days=1)

        for symbol, rows in result.items():
            dates = [str(row["date"]) for row in rows]
            if len(dates) != len(set(dates)):
                raise CFBaoStockError(f"CF BaoStock klines dates must be unique for {symbol}")
            if dates != sorted(dates):
                raise CFBaoStockError(f"CF BaoStock klines dates must be sorted for {symbol}")
        return result

    def _request_kline_window(
        self,
        symbols: list[str],
        start: str,
        end: str,
        fields: tuple[str, ...],
        adjust: str,
    ) -> dict[str, list[dict[str, Any]]]:
        payload = self._request(KLINES_PATH, query={
            "symbols": ",".join(symbols), "start": start, "end": end, "fields": ",".join(fields),
        })
        expected_metadata = {
            "adjust": adjust, "start": start, "end": end, "fields": list(fields),
            "symbol_count": len(symbols),
        }
        for key, expected in expected_metadata.items():
            if payload.get(key) != expected:
                raise CFBaoStockError(f"CF BaoStock qfq {key} is invalid")
        raw_results = payload.get("results")
        if not isinstance(raw_results, list):
            raise CFBaoStockError("CF BaoStock qfq response lacks results")
        result: dict[str, list[dict[str, Any]]] = {}
        aliases = {symbol.lower(): symbol for symbol in symbols}
        aliases.update({
            f"{symbol.rsplit('.', 1)[1].lower()}.{symbol.split('.', 1)[0]}": symbol
            for symbol in symbols if "." in symbol
        })
        seen_targets: set[str] = set()
        total_count = 0
        for item in raw_results:
            if not isinstance(item, dict) or not isinstance(item.get("symbol"), str):
                raise CFBaoStockError("CF BaoStock qfq result is invalid")
            target = aliases.get(item["symbol"].lower())
            if target is None or target in seen_targets:
                raise CFBaoStockError("CF BaoStock qfq symbol set is invalid")
            rows = item.get("records")
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise CFBaoStockError(f"CF BaoStock klines rows are invalid for {target}")
            if item.get("count") != len(rows):
                raise CFBaoStockError(f"CF BaoStock qfq count is invalid for {target}")
            self._validate_kline_rows(target, rows, start, end, fields)
            seen_targets.add(target)
            result[target] = rows
            total_count += len(rows)
        if seen_targets != set(symbols):
            raise CFBaoStockError("CF BaoStock qfq response is partial")
        if payload.get("count") != total_count:
            raise CFBaoStockError("CF BaoStock qfq count is invalid")
        return result

    @staticmethod
    def _validate_kline_rows(
        symbol: str, rows: list[dict[str, Any]], start: str, end: str, fields: tuple[str, ...],
    ) -> None:
        dates: list[str] = []
        required = set(fields)
        for row in rows:
            if not required.issubset(row):
                raise CFBaoStockError(f"CF BaoStock klines field is missing for {symbol}")
            day = row.get("date")
            if not isinstance(day, str) or not start <= day <= end:
                raise CFBaoStockError(f"CF BaoStock klines date range is invalid for {symbol}")
            try:
                date.fromisoformat(day)
            except ValueError as exc:
                raise CFBaoStockError(f"CF BaoStock klines date is invalid for {symbol}") from exc
            dates.append(day)
            for field in required & NUMERIC_KLINE_FIELDS:
                try:
                    value = float(row[field])
                except (TypeError, ValueError) as exc:
                    raise CFBaoStockError(f"CF BaoStock klines {field} must be finite for {symbol}") from exc
                if not math.isfinite(value):
                    raise CFBaoStockError(f"CF BaoStock klines {field} must be finite for {symbol}")
            if {"open", "high", "low", "close"}.issubset(required):
                open_, high, low, close = (float(row[field]) for field in ("open", "high", "low", "close"))
                if low > min(open_, close) or high < max(open_, close) or low > high:
                    raise CFBaoStockError(f"CF BaoStock klines OHLC is invalid for {symbol}")
        if len(dates) != len(set(dates)):
            raise CFBaoStockError(f"CF BaoStock klines dates must be unique for {symbol}")
        if dates != sorted(dates):
            raise CFBaoStockError(f"CF BaoStock klines dates must be sorted for {symbol}")

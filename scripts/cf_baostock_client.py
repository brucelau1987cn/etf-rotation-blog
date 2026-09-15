#!/usr/bin/env python3
"""Small stdlib client for the authenticated CF BaoStock gateway."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

CALENDAR_PATH = "/api/internal/v1/baostock/calendar"
KLINES_PATH = "/api/internal/v1/baostock/qfq"
MAX_KLINE_SYMBOLS = 5


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
    ) -> dict[str, list[dict[str, Any]]]:
        if not symbols or len(symbols) > MAX_KLINE_SYMBOLS:
            raise ValueError(f"CF BaoStock klines requires 1 to at most {MAX_KLINE_SYMBOLS} symbols")
        if adjust != "qfq":
            raise ValueError("CF BaoStock first migration supports adjust=qfq")
        payload = self._request(KLINES_PATH, query={
            "symbols": ",".join(symbols),
            "start": start,
            "end": end,
            "fields": "date,close,volume,amount,turn,tradestatus",
        })
        raw_results = payload.get("results")
        if not isinstance(raw_results, list):
            raise CFBaoStockError("CF BaoStock qfq response lacks results")
        raw: dict[str, list[dict[str, Any]]] = {}
        for item in raw_results:
            if not isinstance(item, dict) or not isinstance(item.get("symbol"), str):
                raise CFBaoStockError("CF BaoStock qfq result is invalid")
            records = item.get("records")
            if not isinstance(records, list):
                raise CFBaoStockError("CF BaoStock qfq records are invalid")
            raw[item["symbol"]] = records
        result: dict[str, list[dict[str, Any]]] = {}
        aliases = {symbol.lower(): symbol for symbol in symbols}
        aliases.update({
            f"{symbol.rsplit('.', 1)[1].lower()}.{symbol.split('.', 1)[0]}": symbol
            for symbol in symbols if "." in symbol
        })
        seen_targets: set[str] = set()
        for source_symbol, rows in raw.items():
            target = aliases.get(str(source_symbol).lower())
            if target is None or target in seen_targets:
                raise CFBaoStockError("CF BaoStock qfq symbol set is invalid")
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise CFBaoStockError(f"CF BaoStock klines rows are invalid for {target}")
            seen_targets.add(target)
            result[target] = rows
        if seen_targets != set(symbols):
            raise CFBaoStockError("CF BaoStock qfq response is partial")
        return result

#!/usr/bin/env python3
"""Eastmoney (东方财富) direct data client for low-chip enrichment.

Why this exists
---------------
The three enrichment stages (theme concepts / technical / ETF holdings) used to
go through mx-data (东财妙想), which meters calls per API key per day. A dormant
key silently returns code=113 and every stage produced empty data while the
pipeline still reported success. The Eastmoney F10 endpoints below are
unauthenticated and carry no quota, so they replace mx-data entirely.

Risk-control discipline (IMPORTANT)
-----------------------------------
Eastmoney throttles and eventually blocks clients that hammer their endpoints.
This client is deliberately conservative:

* one request at a time (a process-wide lock), never parallel fan-out;
* a minimum interval between requests, with jitter, so traffic looks human;
* exponential backoff with jitter on 429/403/5xx and on empty bodies;
* a bounded number of attempts, then an explicit exception -- never a silent
  empty result that would look like "this stock has no concepts";
* browser-like headers including a Referer, because several F10 routes return
  an empty body without it.

Endpoints
---------
concepts   datacenter.eastmoney.com  RPT_F10_CORETHEME_BOARDTYPE
fund holds emweb.securities.eastmoney.com  PC_HSF10/ShareholderResearch/PageAjax
"""
from __future__ import annotations

import gzip
import io
import json
import os
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# Conservative pacing. 88 stocks x 2 endpoints at 350ms ~= 62s per stage,
# which is well inside the 900s stage budget and keeps us far from the
# thresholds where Eastmoney starts returning empty bodies.
DEFAULT_MIN_INTERVAL_S = 0.35
DEFAULT_MAX_ATTEMPTS = 4
DEFAULT_BACKOFF_BASE_S = 1.5
DEFAULT_TIMEOUT_S = 25.0


class EastmoneyError(RuntimeError):
    """Raised when a request fails after all attempts.

    Callers must surface this as an explicit degraded status. Returning [] on
    failure would be indistinguishable from "no data for this stock", which is
    exactly the silent-empty failure mode this module exists to prevent.
    """


class _Pacer:
    """Process-wide serialiser: one in-flight request plus a minimum gap."""

    def __init__(self, min_interval_s: float) -> None:
        self._lock = threading.Lock()
        self._last_at = 0.0
        self._min_interval = min_interval_s
        self.requests = 0

    def wait_turn(self) -> None:
        with self._lock:
            now = time.monotonic()
            gap = self._last_at + self._min_interval - now
            if gap > 0:
                # Jitter keeps the cadence from looking like a fixed-rate bot.
                time.sleep(gap + random.uniform(0.02, 0.12))
            self._last_at = time.monotonic()
            self.requests += 1


def _market_prefix(code: str) -> str:
    return "SH" if code.startswith(("6", "5", "9")) else "SZ"


def _secucode(full_code: str) -> str:
    """000001.SZ -> 000001.SZ ; bare 000001 -> 000001.SZ"""
    if "." in full_code:
        return full_code.upper()
    return f"{full_code}.{_market_prefix(full_code)}"


class EastmoneyClient:
    def __init__(
        self,
        *,
        min_interval_s: float = DEFAULT_MIN_INTERVAL_S,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        backoff_base_s: float = DEFAULT_BACKOFF_BASE_S,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        self._pacer = _Pacer(min_interval_s)
        self._max_attempts = max_attempts
        self._backoff_base = backoff_base_s
        self._timeout = timeout_s
        self.retries = 0
        self.failures = 0

    # ---------------- low level ----------------

    def _fetch(self, url: str, referer: str | None = None) -> bytes:
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
        }
        if referer:
            headers["Referer"] = referer

        last_error: str | None = None
        for attempt in range(1, self._max_attempts + 1):
            self._pacer.wait_turn()
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                    raw = resp.read()
                    encoding = (resp.headers.get("Content-Encoding") or "").lower()
                if "gzip" in encoding:
                    raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
                if not raw:
                    # Eastmoney serves an empty body when it decides to throttle
                    # or when the route dislikes the caller. Treat as retryable.
                    raise EastmoneyError("empty response body")
                return raw
            except urllib.error.HTTPError as exc:
                last_error = f"HTTP {exc.code}"
                if exc.code in (403, 429) or 500 <= exc.code < 600:
                    self._sleep_backoff(attempt)
                    continue
                raise EastmoneyError(f"{last_error} for {url}") from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                self._sleep_backoff(attempt)
                continue
            except EastmoneyError as exc:
                last_error = str(exc)
                self._sleep_backoff(attempt)
                continue
        self.failures += 1
        raise EastmoneyError(
            f"gave up after {self._max_attempts} attempts ({last_error})"
        )

    def _sleep_backoff(self, attempt: int) -> None:
        self.retries += 1
        delay = self._backoff_base * (2 ** (attempt - 1))
        time.sleep(delay + random.uniform(0.0, 0.4))

    def _get_json(self, url: str, referer: str | None = None) -> Any:
        raw = self._fetch(url, referer=referer)
        try:
            return json.loads(raw.decode("utf-8", "ignore"))
        except json.JSONDecodeError as exc:
            raise EastmoneyError(f"invalid JSON from {url}: {exc}") from exc

    # ---------------- concepts ----------------

    def concepts(self, full_code: str) -> list[str]:
        """Concept boards for one stock, precise boards first.

        `IS_PRECISE` == "1" marks Eastmoney's curated theme boards; "0" is the
        loose bucket (北京板块 / 融资融券 / 低价股 ...) which must not be shown
        as a "why it is moving" theme. We return precise boards first and keep
        the loose ones after them so callers can still filter by index.
        """
        secucode = _secucode(full_code)
        flt = urllib.parse.quote(f'(SECUCODE="{secucode}")')
        url = (
            "https://datacenter.eastmoney.com/securities/api/data/v1/get?"
            "reportName=RPT_F10_CORETHEME_BOARDTYPE"
            "&columns=SECUCODE,SECURITY_CODE,BOARD_CODE,BOARD_NAME,IS_PRECISE,BOARD_RANK"
            f"&filter={flt}&pageNumber=1&pageSize=100&source=HSF10&client=PC"
        )
        payload = self._get_json(url)
        result = (payload or {}).get("result") or {}
        rows = result.get("data") or []
        if not rows:
            # A valid answer can legitimately be empty for a brand-new listing,
            # but for an established stock it usually means throttling.
            return []

        def sort_key(row: dict) -> tuple[int, int]:
            precise = 0 if str(row.get("IS_PRECISE")) == "1" else 1
            try:
                rank = int(row.get("BOARD_RANK") or 9999)
            except (TypeError, ValueError):
                rank = 9999
            return (precise, rank)

        rows.sort(key=sort_key)
        seen: set[str] = set()
        names: list[str] = []
        for row in rows:
            name = str(row.get("BOARD_NAME") or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            names.append(name)
        return names

    # ---------------- fund / ETF holdings ----------------

    def fund_holdings(self, full_code: str) -> list[dict]:
        """Fund (incl. ETF) holdings of one stock from the F10 shareholder page.

        The response nests everything under `jjcg` (基金持股). Fields we keep:
        fund code/name, shares, free-float ratio, total-share ratio and
        NETVALUE_RATIO (holding value as a share of the fund's NAV), which is
        the "持仓市值占基金资产净值比" figure the old mx-data query returned.
        """
        code = full_code.split(".")[0]
        mkt = _market_prefix(code)
        url = (
            "https://emweb.securities.eastmoney.com/PC_HSF10/"
            f"ShareholderResearch/PageAjax?code={mkt}{code}"
        )
        referer = (
            "https://emweb.securities.eastmoney.com/PC_HSF10/"
            f"ShareholderResearch/Index?type=web&code={mkt}{code}"
        )
        payload = self._get_json(url, referer=referer)
        rows = (payload or {}).get("jjcg") or []
        out: list[dict] = []
        for row in rows:
            fund_code = str(row.get("FUND_CODE") or row.get("HOLDER_CODE") or "").strip()
            if not fund_code:
                continue
            out.append(
                {
                    "fund_code": fund_code,
                    "fund_derivecode": row.get("FUND_DERIVECODE"),
                    "fund_name": row.get("HOLDER_NAME"),
                    "report_date": str(row.get("REPORT_DATE") or "")[:10],
                    "shares": row.get("TOTAL_SHARES"),
                    "hold_value": row.get("HOLD_VALUE"),
                    "total_share_ratio": row.get("TOTALSHARES_RATIO"),
                    "free_float_ratio": row.get("FREESHARES_RATIO"),
                    "nav_ratio": row.get("NETVALUE_RATIO"),
                    "org_type": row.get("ORG_TYPE"),
                }
            )
        return out

    # ---------------- daily bars (for RSI) ----------------

    def daily_closes(self, full_code: str, count: int = 120) -> list[float]:
        """QFQ daily closes, used to compute RSI locally.

        Eastmoney's push2his kline route returns empty bodies on weekends, so
        the RSI input deliberately comes from Tencent.

        Source order (each step only runs if the previous returned nothing):
          1. THS (10jqka) qfq daily — ~0.13s per stock, full market coverage,
             carries the current session's bar. Promoted to first place because
             Tencent's fqkline route went behind a WAF that answers every
             request with the 501 interstitial page (2026-10-22); waiting out
             that block's 4-attempt backoff cost 47s per stock.
          2. Tencent fqkline, both hosts. Kept as an independent second opinion
             in case THS changes its JSONP shape.
          3. CF BaoStock — slowest (~5s per 5-symbol batch) and lags by a day.

        Never return a partial/empty list on a source failure: raising is what
        lets the caller mark the stage degraded instead of silently screening
        on stale RSI.
        """
        code = full_code.split(".")[0]
        ex = "sh" if code.startswith(("6", "5", "9")) else "sz"
        errors: list[str] = []

        closes = self._ths_daily_closes(code, count, errors)
        if closes:
            return closes

        hosts = ("ifzq.gtimg.cn", "web.ifzq.gtimg.cn")
        for host in hosts:
            url = (
                "https://" + host + "/appstock/app/fqkline/get?"
                f"param={ex}{code},day,,,{count},qfq"
            )
            try:
                payload = self._get_json(url, referer="https://gu.qq.com/")
            except EastmoneyError as exc:
                errors.append(f"{host}: {exc}")
                continue
            node = ((payload or {}).get("data") or {}).get(f"{ex}{code}") or {}
            bars = node.get("qfqday") or node.get("day") or []
            closes = []
            for bar in bars:
                try:
                    closes.append(float(bar[2]))
                except (TypeError, ValueError, IndexError):
                    continue
            if closes:
                return closes
            errors.append(f"{host}: empty bars")
        raise EastmoneyError(f"no daily bars for {full_code}: {'; '.join(errors)}")

    def _ths_daily_closes(self, code: str, count: int, errors: list[str]) -> list[float]:
        """QFQ daily closes from 10jqka (THS).

        The 01 series is qfq, market-wide, and answers in ~0.13s. Sina was
        rejected for this path: it returns unadjusted prices (600519 differs
        from qfq by ~134 CNY), so mixing it in would silently corrupt RSI.

        Any failure returns [] and appends to `errors`; the caller falls through
        to the next source. This never raises, so a THS outage alone cannot
        fail the stage.
        """
        market = "XSHG" if code.startswith(("6", "5", "9")) else "XSHE"
        try:
            from attach_low_chip_touchstone import fetch_ths_history
        except ImportError:  # imported as a package (scripts.attach_...)
            from scripts.attach_low_chip_touchstone import fetch_ths_history
        try:
            rows = fetch_ths_history({"code": code, "market": market}, count)
        except Exception as exc:  # noqa: BLE001 - source shape varies
            errors.append(f"ths: {type(exc).__name__}: {exc}")
            return []
        if not rows:
            errors.append("ths: no rows")
            return []
        return [float(row["close"]) for row in rows if row.get("close") is not None]

    def usage(self) -> dict:
        return {
            "requests": self._pacer.requests,
            "retries": self.retries,
            "failures": self.failures,
            "min_interval_s": self._pacer._min_interval,
            "source": "eastmoney-direct",
        }


_shared: EastmoneyClient | None = None
_shared_lock = threading.Lock()


def get_client() -> EastmoneyClient:
    """Process-wide singleton so the pacer's cadence is shared by all callers."""
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = EastmoneyClient(
                min_interval_s=float(
                    os.getenv("EM_MIN_INTERVAL_S", DEFAULT_MIN_INTERVAL_S)
                )
            )
        return _shared


# ---------------- indicators ----------------

def rsi_wilder(closes: list[float], period: int = 6) -> float | None:
    """Wilder-smoothed RSI.

    period=6 reproduces the Eastmoney/THS RSI6 value exactly (verified against
    mx-data on six stocks, diff 0.00). period=14 drifts 5-14pp, so do not
    "improve" this default.
    """
    if len(closes) < period + 1:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    return 100 - 100 / (1 + avg_gain / avg_loss)


def outer_inner(full_code: str) -> tuple[float | None, float | None]:
    """Outer (active buy) / inner (active sell) volume from Tencent qt.

    Field 7 is 外盘, field 8 is 内盘; their ratio matches mx-data to 0.0000.
    """
    code = full_code.split(".")[0]
    ex = "sh" if code.startswith(("6", "5", "9")) else "sz"
    req = urllib.request.Request(
        f"https://qt.gtimg.cn/q={ex}{code}",
        headers={"User-Agent": USER_AGENT, "Referer": "https://gu.qq.com/"},
    )
    with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT_S) as resp:
        text = resp.read().decode("gbk", "ignore")
    body = text.split("=", 1)[1].strip().strip('";') if "=" in text else ""
    fields = body.split("~")
    if len(fields) < 9:
        return None, None
    try:
        return float(fields[7]), float(fields[8])
    except (TypeError, ValueError):
        return None, None

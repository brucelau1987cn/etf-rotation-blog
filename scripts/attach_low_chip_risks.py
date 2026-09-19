#!/usr/bin/env python3
"""Attach fail-closed, advisory risk labels to the low-chip staging file."""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import math
import multiprocessing as mp
import os
import re
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterable

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "public/data/a-low-chip-stocks.json"
LEVELS = {"none": 0, "watch": 1, "medium": 2, "high": 3}
SCHEMA_VERSION = "low-chip-risk-v1"
SCHEMA_PATH = ROOT / "public/schemas/low-chip-risk-v1.schema.json"
MAX_PRICE_LAG_DAYS = 7
SHORT_WINDOW = 20
UA = "ETF-Rotation-Blog/1.0 (risk staging)"
_OPEN = urllib.request.urlopen
_CIRCUIT_OPEN: set[str] = set()
_LAST_REQUEST: dict[str, float] = {}
RETRY_DELAYS = (1.0, 2.0, 5.0, 10.0)
HTTP_TIMEOUT_SECONDS = 10.0
SOURCE_BUDGET_SECONDS = 90.0
DRY_RUN_BUDGET_SECONDS = 300.0


def _run_call(child_conn: Any, func: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
    """Run an untrusted adapter in a killable child process."""
    try:
        child_conn.send((True, func(*args, **kwargs)))
    except BaseException as exc:
        child_conn.send((False, f"{type(exc).__name__}: {exc}"))
    finally:
        child_conn.close()


def _hard_call(func: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any],
               timeout: float) -> Any:
    """Call a source boundary with a hard wall-clock timeout."""
    if timeout <= 0:
        raise TimeoutError("call budget exhausted")
    ctx = mp.get_context("fork")
    parent, child = ctx.Pipe(duplex=False)
    process = ctx.Process(target=_run_call, args=(child, func, args, kwargs))
    process.start()
    child.close()
    try:
        if not parent.poll(timeout):
            process.terminate()
            process.join(1)
            raise TimeoutError(f"call exceeded {timeout:.3f}s hard budget")
        ok, value = parent.recv()
        process.join(1)
        if ok:
            return value
        raise RuntimeError(value)
    finally:
        if process.is_alive():
            process.terminate()
        process.join(1)
        parent.close()


def _incomplete(name: str) -> dict[str, Any]:
    return {"complete": False, "error": f"{name} adapter is unconfigured"}


def _source_key(url: str) -> str:
    return urllib.parse.urlsplit(url).netloc


def _request_json(url: str, *, opener: Callable[..., Any] = _OPEN, min_interval: float = 0.25,
                 retries: int | None = None, source: str | None = None,
                 headers: dict[str, str] | None = None,
                 sleep: Callable[[float], None] = time.sleep,
                 deadline: float | None = None) -> Any:
    """HTTPS JSON/JSONP client with per-source pacing, fixed backoff and 403 circuit."""
    key = source or _source_key(url)
    if key in _CIRCUIT_OPEN: raise RuntimeError(f"{key} circuit open after HTTP 403")
    retry_delays = RETRY_DELAYS if retries is None else RETRY_DELAYS[:max(0, retries)]
    last_error: Exception | None = None
    base_headers = {"User-Agent": UA, "Accept": "application/json",
                    "Referer": "https://data.eastmoney.com/"}
    if headers: base_headers.update(headers)
    for attempt in range(len(retry_delays) + 1):
        remaining = deadline - time.monotonic() if deadline is not None else HTTP_TIMEOUT_SECONDS
        if remaining <= 0: raise TimeoutError(f"{key} source budget exhausted")
        delay = min_interval - (time.monotonic() - _LAST_REQUEST.get(key, 0.0))
        if delay > 0:
            if deadline is not None and delay >= remaining: raise TimeoutError(f"{key} source budget exhausted")
            sleep(delay)
        req = urllib.request.Request(url, headers=dict(base_headers))
        try:
            remaining = deadline - time.monotonic() if deadline is not None else HTTP_TIMEOUT_SECONDS
            if remaining <= 0: raise TimeoutError(f"{key} source budget exhausted")
            with opener(req, timeout=min(HTTP_TIMEOUT_SECONDS, remaining)) as response:
                _LAST_REQUEST[key] = time.monotonic()
                if getattr(response, "status", 200) == 403: _CIRCUIT_OPEN.add(key); raise RuntimeError("HTTP 403")
                raw = response.read().decode("utf-8-sig").strip()
                try: return json.loads(raw.lstrip("/"))
                except json.JSONDecodeError:
                    match = re.match(r"^[^(]+\((.*)\)\s*;?$", raw, re.S)
                    if not match: raise
                    return json.loads(match.group(1))
        except urllib.error.HTTPError as exc:
            _LAST_REQUEST[key] = time.monotonic(); last_error = exc
            if exc.code == 403: _CIRCUIT_OPEN.add(key); raise RuntimeError("HTTP 403") from exc
            if exc.code not in (408, 425, 429, 500, 502, 503, 504) or attempt >= len(retry_delays): break
            backoff = retry_delays[attempt]
            if deadline is not None and backoff >= deadline - time.monotonic(): break
            sleep(backoff)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt >= len(retry_delays): break
            backoff = retry_delays[attempt]
            if deadline is not None and backoff >= deadline - time.monotonic(): break
            sleep(backoff)
    raise RuntimeError(f"risk source request failed: {last_error}")


def _date_value(row: dict[str, Any]) -> str | None:
    for key in ("publishDate", "publish_time", "notice_date", "noticeDate", "date", "datetime", "art_time", "emPublishDate"):
        if row.get(key): return str(row[key])[:10]
    return None


def _row_code(row: dict[str, Any]) -> str:
    for key in ("stockCode", "stock_code", "securityCode", "secCode", "code", "ticker", "symbol"):
        value = str(row.get(key, "")).strip()
        if value:
            match = re.search(r"(?<!\d)(\d{6})(?!\d)", value)
            if match: return match.group(1)
    return ""


def _date_checked_rows(source: str, rows: Any, code: str, as_of: str | None) -> list[dict[str, Any]]:
    if not isinstance(rows, list): raise ValueError(f"{source} response list invalid")
    bare = code.split(".")[0]
    checked = []
    for row in rows:
        if not isinstance(row, dict) or _row_code(row) != bare:
            continue
        date_value = _date_value(row)
        if not date_value:
            raise ValueError(f"{source} record has no verifiable date")
        try: dt.date.fromisoformat(date_value)
        except ValueError as exc: raise ValueError(f"{source} record has invalid date {date_value!r}") from exc
        if as_of and date_value > as_of[:10]: raise ValueError(f"{source} returned future-dated data")
        checked.append(row)
    return checked


def _ownership_checked_rows(source: str, rows: Any, *, owned: Callable[[dict[str, Any]], bool],
                            date_keys: tuple[str, ...] = ("date",),
                            as_of: str | None = None) -> list[dict[str, Any]]:
    """Like _date_checked_rows but trusts an explicit ownership predicate instead of a stock_code field.

    News endpoints don't return a stock code on every row, so we filter by an external predicate
    (e.g. title/body contains the bare code) and validate the date separately. Future-dated rows
    are dropped (not raised) so that one announcement scheduled for the next session doesn't
    invalidate the whole batch — the future-dated row is simply excluded.
    """
    if not isinstance(rows, list): raise ValueError(f"{source} response list invalid")
    checked = []
    for row in rows:
        if not isinstance(row, dict) or not owned(row):
            continue
        date_value = next((str(row.get(k))[:10] for k in date_keys if row.get(k)), None)
        if not date_value:
            raise ValueError(f"{source} record has no verifiable date")
        try: dt.date.fromisoformat(date_value)
        except ValueError as exc: raise ValueError(f"{source} record has invalid date {date_value!r}") from exc
        if as_of and date_value > as_of[:10]:
            continue  # drop future-dated rows silently; other rows still pass
        checked.append(row)
    return checked


def _result(source: str, rows: list[dict[str, Any]], *, as_of: str | None,
            reasons: list[str] | None = None, level: str = "none",
            empty_freshness: str | None = None) -> dict[str, Any]:
    dates = sorted(d for d in (_date_value(r) for r in rows) if d)
    if not rows:
        if empty_freshness is None:
            raise RuntimeError(f"{source} returned no verifiable records for requested stock")
        return {"complete": True, "status": "ok", "level": level, "reasons": reasons or [],
                "source": source, "source_as_of": None, "coverage_count": 0,
                "freshness": empty_freshness}
    if not dates: raise RuntimeError(f"{source} returned records without source dates")
    if as_of and dates and any(d > as_of[:10] for d in dates): raise ValueError(f"{source} returned future-dated data")
    return {"complete": True, "status": "ok", "level": level, "reasons": reasons or [], "source": source,
            "source_as_of": dates[-1] if dates else None, "coverage_count": len(rows),
            "freshness_days": ((dt.date.fromisoformat(as_of[:10]) - dt.date.fromisoformat(dates[-1])).days if as_of and dates else None),
            "freshness": "dated"}


_ANN_URL = "https://np-anotice-stock.eastmoney.com/api/security/ann"
_RATING_URL = "https://reportapi.eastmoney.com/report/list"
_NEWS_URL = "https://search-api-web.eastmoney.com/search/jsonp"


def _ann_ownership(row: dict[str, Any], bare: str) -> bool:
    """Announcement row carries the stock under row.codes[].stock_code."""
    codes = row.get("codes") or []
    if not isinstance(codes, list): return False
    for entry in codes:
        if not isinstance(entry, dict): continue
        if str(entry.get("stock_code", "")).strip() == bare: return True
    return False


def fetch_announcements(code: str, *, as_of: str | None = None, deadline: float | None = None) -> dict[str, Any]:
    bare = code.split(".")[0]
    params = {
        "sr": -1, "page_size": 50, "page_index": 1,
        "ann_type": "A", "client_source": "web", "stock_list": bare,
        "f_node": 0, "s_node": 0,
    }
    url = _ANN_URL + "?" + urllib.parse.urlencode(params)
    obj = _request_json(url, deadline=deadline)
    data = obj.get("data") if isinstance(obj, dict) else None
    rows = data.get("list", []) if isinstance(data, dict) else []
    # Re-validate ownership against codes[].stock_code; drop rows that don't match.
    owned = [r for r in rows if isinstance(r, dict) and _ann_ownership(r, bare)]
    matched = _ownership_checked_rows("Eastmoney announcements", owned,
                                      owned=lambda r: bool(_ann_ownership(r, bare)),
                                      date_keys=("notice_date", "display_time", "eiTime",
                                                 "publishDate", "date"),
                                      as_of=as_of)
    if any(not any(str(r.get(k, "")).strip() for k in ("title", "art_title", "title_ch")) for r in matched):
        raise ValueError("Eastmoney announcements record has no title")
    return _result("Eastmoney announcements", matched, as_of=as_of)


def fetch_ratings(code: str, *, as_of: str | None = None, deadline: float | None = None) -> dict[str, Any]:
    bare = code.split(".")[0]
    end = (as_of or dt.date.today().isoformat())[:10]
    params = {
        "cb": "cb", "industryCode": "*", "pageNo": 1, "pageSize": 100,
        "fields": "", "qType": 0, "reportType": 0,
        "beginTime": "2020-01-01", "endTime": end,
        "code": bare,
    }
    url = _RATING_URL + "?" + urllib.parse.urlencode(params)
    raw = _request_json(url, deadline=deadline)
    if not isinstance(raw, dict): raise ValueError("rating response not an object")
    hits_total = raw.get("hits")
    if hits_total is not None and not isinstance(hits_total, (int, float)):
        raise ValueError("rating hits total invalid")
    rows = raw.get("data")
    if not isinstance(rows, list): raise ValueError("rating response data invalid")
    matched = _date_checked_rows("Eastmoney analyst ratings", rows, code, as_of)
    if hits_total == 0 and matched:
        raise ValueError("rating hits/data mismatch")
    return _result("Eastmoney analyst ratings", matched, as_of=as_of,
                   empty_freshness="no_records" if hits_total == 0 else None)


def _news_ownership(row: dict[str, Any], bare: str) -> bool:
    """Accept a news row when title or body contains the bare code (em-wrapped or bare)."""
    title = str(row.get("title", "")).strip()
    body = str(row.get("content", "")).strip()
    if not title or not body:
        return False
    return (bare in title) or (bare in body)


def fetch_news(code: str, *, as_of: str | None = None, deadline: float | None = None) -> dict[str, Any]:
    bare = code.split(".")[0]
    param = {
        "keyword": bare,
        "type": ["cmsArticleWebOld"],
        "client": "web", "clientType": "web", "clientVersion": "curr",
        "searchScope": "default", "sort": "default",
        "pageIndex": 1, "pageSize": 50,
        "preTag": "<em>", "postTag": "</em>",
    }
    url = _NEWS_URL + "?" + urllib.parse.urlencode({
        "cb": "cb", "param": json.dumps(param, ensure_ascii=False, separators=(",", ":")),
    })
    # The search-api-web host rejects `Accept: application/json` with 406; use */* and the
    # search-domain referer to keep the upstream happy.
    obj = _request_json(url, source="search-api-web.eastmoney.com",
                        headers={"Accept": "*/*", "Referer": "https://search-api-web.eastmoney.com/"},
                        deadline=deadline)
    if not isinstance(obj, dict): raise ValueError("news response not an object")
    container = obj.get("result", {})
    rows = container.get("cmsArticleWebOld") if isinstance(container, dict) else None
    if not isinstance(rows, list): raise ValueError("news response cmsArticleWebOld list invalid")
    matched = _ownership_checked_rows("Eastmoney news", rows,
                                      owned=lambda r: _news_ownership(r, bare),
                                      date_keys=("date",), as_of=as_of)
    return _result("Eastmoney news", matched, as_of=as_of)


def fetch_prices(code: str, data_as_of: str | None = None, *, deadline: float | None = None) -> list[dict[str, Any]]:
    try:
        from .attach_low_chip_touchstone import load_history
    except ImportError:
        from attach_low_chip_touchstone import load_history
    return load_history(code, data_as_of or dt.date.today().isoformat())


def _level(level: Any) -> str:
    value = str(level or "").lower()
    if value not in LEVELS:
        raise ValueError(f"invalid risk level: {value}")
    return value


def _schema_errors(payload: Any) -> list[str]:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    return [f"{'.'.join(str(part) for part in error.absolute_path) or '$'}: {error.message}"
            for error in sorted(validator.iter_errors(payload), key=lambda item: list(item.absolute_path))]


def validate_risk_payload(payload: Any) -> dict[str, Any]:
    errors = _schema_errors(payload)
    if errors:
        raise ValueError("low-chip risk schema invalid: " + "; ".join(errors))
    components = payload["components"]
    complete = sum(component.get("complete") is True for component in components)
    if payload["coverage"]["components"] != len(components):
        raise ValueError("low-chip risk semantic invalid: coverage.components mismatch")
    if payload["coverage"]["complete"] != complete:
        raise ValueError("low-chip risk semantic invalid: coverage.complete mismatch")
    component_levels = [_level(component.get("level")) for component in components
                        if component.get("complete") is True]
    component_reasons: list[str] = []
    for component in components:
        for reason in component.get("reasons", []):
            if reason not in component_reasons:
                component_reasons.append(reason)
    if payload["status"] == "ok":
        expected_level = max(component_levels, key=lambda level: LEVELS[level], default="none")
        if payload["level"] != expected_level:
            raise ValueError("low-chip risk semantic invalid: top-level level mismatch")
        if payload["reasons"] != component_reasons:
            raise ValueError("low-chip risk semantic invalid: reasons are not component union")
        price_components = [component for component in components if component.get("source") == "qfq daily close"]
        price_dates = {component.get("as_of") for component in price_components}
        price_as_of = payload["freshness"].get("price_as_of")
        if len(price_components) != 1 or len(price_dates) != 1 or price_as_of not in price_dates:
            raise ValueError("low-chip risk semantic invalid: price freshness mismatch")
        if payload["as_of"] is not None and price_as_of > payload["as_of"]:
            raise ValueError("low-chip risk semantic invalid: price freshness is in the future")
    if payload["status"] == "ok":
        if complete != len(components) or any(component["status"] != "ok" for component in components):
            raise ValueError("low-chip risk semantic invalid: ok payload contains failed component")
    elif complete == len(components):
        raise ValueError("low-chip risk semantic invalid: failed payload is fully complete")
    return payload


def _as_date(value: Any) -> dt.date:
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid price date: {value!r}") from exc


def _symbol_meta(symbol: str | None) -> tuple[str, bool]:
    text = str(symbol or "").upper()
    market = text.rsplit(".", 1)[-1] if "." in text else ""
    code = text.split(".", 1)[0]
    is_st = bool(re.search(r"(^|[^A-Z])ST", text))
    if market == "BJ" or code.startswith("92"): return "BJ", is_st
    if market == "SH" or code.startswith(("688", "689")): return "STAR", is_st
    if market == "SZ" and code.startswith(("300", "301")): return "CHINEXT", is_st
    return market or "A", is_st


def _limit_threshold(symbol: str | None) -> float:
    market, is_st = _symbol_meta(symbol)
    if is_st: return 0.05
    return {"BJ": 0.30, "STAR": 0.20, "CHINEXT": 0.20}.get(market, 0.10)


def _normalise_prices(rows: Iterable[Any], as_of: str | None) -> list[tuple[dt.date, float, dict[str, Any]]]:
    cutoff = _as_date(as_of) if as_of else None
    by_date: dict[dt.date, tuple[float, dict[str, Any]]] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            raise ValueError("price row must be an object with date and close")
        raw_date = row.get("trade_date", row.get("date"))
        day = _as_date(raw_date)
        if cutoff and day > cutoff:
            raise ValueError("price history contains a future date")
        value = row.get("close")
        if value is None: raise ValueError("price close is invalid")
        try:
            close = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("price close is invalid") from exc
        if not math.isfinite(close) or close <= 0: raise ValueError("price close must be positive")
        by_date[day] = (close, row)
    result = [(day, value[0], value[1]) for day, value in sorted(by_date.items())]
    if cutoff and result:
        lag = (cutoff - result[-1][0]).days
        if lag > MAX_PRICE_LAG_DAYS:
            raise ValueError(f"price history is {lag} days behind data_as_of (max {MAX_PRICE_LAG_DAYS})")
    return result


def classify_price_risk(rows: Iterable[Any], *, symbol: str | None = None, as_of: str | None = None) -> dict[str, Any]:
    normalised = _normalise_prices(rows, as_of)
    prices = [item[1] for item in normalised]
    dates = [item[0].isoformat() for item in normalised]
    if len(prices) < 2:
        raise ValueError("price history coverage incomplete: at least 2 dated bars required")
    if len(prices) < SHORT_WINDOW:
        raise ValueError(f"short-term drawdown requires complete {SHORT_WINDOW}-bar window")
    daily = [(prices[i] / prices[i - 1] - 1) for i in range(1, len(prices))]
    threshold = _limit_threshold(symbol)
    limit_down = [change <= -(threshold - 0.005) for change in daily]
    declines = [change < 0 for change in daily]
    decline_streak = 0
    for is_decline in reversed(declines):
        if is_decline: decline_streak += 1
        else: break
    limit_streak = 0
    for is_limit in reversed(limit_down):
        if is_limit: limit_streak += 1
        else: break
    reasons: list[str] = []
    level = "none"
    if limit_streak >= 2:
        reasons.append("consecutive_limit_down")
        level = "high"
    if decline_streak >= 5: reasons.append("consecutive_declines_5d")
    lookback = prices[-SHORT_WINDOW:]
    drawdown = prices[-1] / max(lookback) - 1
    if drawdown <= -0.10: reasons.append("short_term_drawdown")
    if reasons and level != "high": level = "watch"
    return {"complete": True, "level": level, "reasons": reasons, "coverage_bars": len(prices),
            "source": "qfq daily close", "status": "ok", "as_of": dates[-1], "freshness_days": ( _as_date(as_of) - normalised[-1][0]).days if as_of else None,
            "limit_threshold": threshold, "decline_streak": decline_streak, "limit_down_streak": limit_streak}


def _adapter_result(adapter: Callable[..., dict[str, Any]], code: str, as_of: str | None = None,
                    deadline: float | None = None) -> dict[str, Any]:
    try:
        result = adapter(code, as_of=as_of, deadline=deadline)
    except TypeError as exc:
        if "deadline" in str(exc):
            try:
                result = adapter(code, as_of=as_of)
            except TypeError as inner:
                if "as_of" not in str(inner): raise
                result = adapter(code)
        elif "as_of" in str(exc):
            result = adapter(code)
        else:
            raise
    if not isinstance(result, dict) or result.get("complete") is not True:
        error = result.get("error", "incomplete adapter result") if isinstance(result, dict) else "invalid adapter result"
        return {"complete": False, "status": "failed", "error": str(error), "source": "adapter"}
    reasons = result.get("reasons")
    if (not isinstance(reasons, list) or
            any(not isinstance(reason, str) for reason in reasons) or
            not isinstance(result.get("source"), str) or not result["source"]):
        return {"complete": False, "status": "failed", "error": "adapter output schema invalid", "source": "adapter"}
    try: level = _level(result.get("level"))
    except ValueError as exc: return {"complete": False, "status": "failed", "error": str(exc), "source": "adapter"}
    return {**result, "complete": True, "status": "ok", "level": level, "reasons": list(reasons)}


def _invoke_price(fetcher: Callable[..., Iterable[Any]], code: str, as_of: str | None,
                  deadline: float | None = None) -> Iterable[Any]:
    try:
        return fetcher(code, as_of, deadline=deadline)
    except TypeError as exc:
        if "deadline" not in str(exc):
            raise
        try:
            return fetcher(code, as_of)
        except TypeError as inner:
            if "positional" not in str(inner):
                raise
            return fetcher(code)


def _invoke_adapter(adapter: Callable[..., dict[str, Any]], code: str, as_of: str | None,
                    deadline: float) -> dict[str, Any]:
    return _adapter_result(adapter, code, as_of, deadline)


def aggregate_risk(parts: Iterable[dict[str, Any]], *, as_of: str | None = None) -> dict[str, Any]:
    values = list(parts)
    if any(part.get("complete") is not True or part.get("status") != "ok" for part in values):
        raise ValueError("cannot aggregate failed risk components")
    highest = max((_level(part.get("level")) for part in values), key=LEVELS.get, default="none")
    reasons: list[str] = []
    for part in values:
        for reason in part.get("reasons", []):
            if reason not in reasons: reasons.append(reason)
    price_parts = [p for p in values if p.get("source") == "qfq daily close"]
    payload = {"version": SCHEMA_VERSION, "as_of": as_of, "status": "ok", "source": "price+announcement+rating+news adapters",
            "freshness": {"price_as_of": price_parts[0].get("as_of") if len(price_parts) == 1 else None},
            "coverage": {"components": len(values), "complete": len(values)}, "level": highest, "reasons": reasons,
            "components": values, "advisory": True}
    return validate_risk_payload(payload)


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":")); handle.write("\n"); handle.flush(); os.fsync(handle.fileno())
        os.replace(name, path)
    finally: Path(name).unlink(missing_ok=True)


def attach_risks(path: str | Path = DATA, *, dry_run: bool = False,
                 data_as_of: str | None = None,
                 price_fetcher: Callable[..., Iterable[Any]] = fetch_prices,
                 announcement_adapter: Callable[[str], dict[str, Any]] = fetch_announcements,
                 rating_adapter: Callable[[str], dict[str, Any]] = fetch_ratings,
                 news_adapter: Callable[[str], dict[str, Any]] = fetch_news) -> dict[str, Any]:
    target = Path(path); original = json.loads(target.read_text(encoding="utf-8")); payload = copy.deepcopy(original)
    data_as_of = data_as_of or payload.get("data_as_of"); failed: list[str] = []; attached = 0
    started = time.monotonic(); overall_deadline = started + DRY_RUN_BUDGET_SECONDS
    source_deadlines = {name: started + SOURCE_BUDGET_SECONDS for name in ("announcement", "rating", "news")}
    source_stats = {name: {"coverage": 0, "success": 0, "failed": 0, "elapsed_seconds": 0.0} for name in ("price", "announcement", "rating", "news")}
    for code in payload.get("intersection", []):
        parts: list[dict[str, Any]] = []
        errors: list[str] = []
        t = time.monotonic()
        try:
            remaining = overall_deadline - time.monotonic()
            if remaining <= 0: raise TimeoutError("dry-run budget exhausted")
            rows = list(_hard_call(_invoke_price, (price_fetcher, code, data_as_of, overall_deadline), {}, remaining) or [])
            price = classify_price_risk(rows, symbol=code, as_of=data_as_of)
            source_stats["price"]["coverage"] += 1; source_stats["price"]["success"] += 1
            parts.append(price)
        except Exception as exc:
            source_stats["price"]["failed"] += 1; errors.append(f"price: {exc}")
            parts.append({"complete": False, "status": "failed", "level": "unknown", "reasons": [str(exc)], "source": "price"})
        finally:
            source_stats["price"]["elapsed_seconds"] += time.monotonic() - t
        for name, adapter in zip(("announcement", "rating", "news"), (announcement_adapter, rating_adapter, news_adapter)):
            t = time.monotonic()
            try:
                deadline = min(source_deadlines[name], overall_deadline)
                remaining = deadline - time.monotonic()
                if remaining <= 0: raise TimeoutError(f"{name} source budget exhausted")
                part = _hard_call(_invoke_adapter, (adapter, code, data_as_of, deadline), {}, remaining)
                if part.get("complete") is not True: raise RuntimeError(str(part.get("error", "adapter coverage incomplete")))
                source_stats[name]["coverage"] += 1; source_stats[name]["success"] += 1
            except Exception as exc:
                source_stats[name]["failed"] += 1; errors.append(f"{name}: {exc}")
                part = {"complete": False, "status": "failed", "level": "unknown", "reasons": [str(exc)], "source": name}
            finally:
                source_stats[name]["elapsed_seconds"] += time.monotonic() - t
            parts.append(part)
        if not errors:
            payload.setdefault("enrichments", {}).setdefault(code, {})["risk"] = aggregate_risk(parts, as_of=data_as_of); attached += 1
        else:
            failed.append(code)
            failure = {"version": SCHEMA_VERSION, "as_of": data_as_of, "status": "failed", "source": "risk pipeline", "freshness": {"price_as_of": next((p.get("as_of") for p in parts if p.get("status") == "ok"), None)}, "coverage": {"components": len(parts), "complete": sum(p.get("complete") is True for p in parts)}, "level": "unknown", "reasons": errors, "components": parts, "advisory": True}
            payload.setdefault("enrichments", {}).setdefault(code, {})["risk"] = validate_risk_payload(failure)
    coverage = {"requested": len(payload.get("intersection", [])), "attached": attached, "failed": failed,
                "sources": source_stats, "elapsed_seconds": time.monotonic() - started}
    status = "ok" if not failed and attached == coverage["requested"] else "STAGING BLOCKER"
    if status == "ok":
        for code in payload.get("intersection", []):
            validate_risk_payload(payload["enrichments"][code]["risk"])
    if status == "ok" and not dry_run:
        _atomic_write(target, payload)
        persisted = json.loads(target.read_text(encoding="utf-8"))
        for code in persisted.get("intersection", []):
            validate_risk_payload(persisted["enrichments"][code]["risk"])
    return {"status": "dry-run" if dry_run and status == "ok" else status, "coverage": coverage, "payload": payload}


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--data", type=Path, default=DATA); parser.add_argument("--dry-run", action="store_true"); parser.add_argument("--data-as-of"); args = parser.parse_args()
    result = attach_risks(args.data, dry_run=args.dry_run, data_as_of=args.data_as_of)
    print(json.dumps({k: result[k] for k in ("status", "coverage")}, ensure_ascii=False))
    return 0 if result["status"] in {"ok", "dry-run"} else 2


if __name__ == "__main__": raise SystemExit(main())

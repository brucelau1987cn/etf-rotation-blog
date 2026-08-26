#!/usr/bin/env python3
"""A-share valuation shadow collector and fail-closed coverage contract."""
from __future__ import annotations

import argparse
import json
import math
import multiprocessing as mp
import os
import tempfile
import urllib.request
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any


MINIMUM_OBSERVATION_SESSIONS = 10
MATURITY_SESSIONS = 20


def finite_or_none(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def build_shadow_metric(
    *, trade_date: str, stock_code: str, name: str | None, close: Any,
    pe_ttm: Any, pb: Any, ps_ttm: Any, pcf_ttm: Any, total_share: Any,
    observation_sessions: int,
) -> dict[str, Any]:
    price = finite_or_none(close)
    shares = finite_or_none(total_share)
    total_mv = price * shares if price is not None and shares is not None and shares > 0 else None
    status = "OBSERVED" if observation_sessions >= MATURITY_SESSIONS else (
        "ACCUMULATING" if observation_sessions >= MINIMUM_OBSERVATION_SESSIONS else "IMMATURE"
    )
    return {
        "trade_date": str(trade_date).replace("-", ""), "stock_code": stock_code, "stock_name": name,
        "price": price, "pe_ttm": finite_or_none(pe_ttm), "pb": finite_or_none(pb),
        "ps_ttm": finite_or_none(ps_ttm), "pcf_ttm": finite_or_none(pcf_ttm),
        "total_share": shares, "total_mv": total_mv,
        "fundamental_shadow": {
            "mode": "shadow_research_only",
            "observation_sessions": observation_sessions,
            "minimum_observation_sessions": MINIMUM_OBSERVATION_SESSIONS,
            "maturity_sessions": MATURITY_SESSIONS,
            "status": status,
            "production_weights_changed": False,
            "formal_signal_logic_changed": False,
            "production_role": "audit_only",
        },
    }


def normalize_baostock_row(
    values: list[Any], *, total_share: Any, share_observed_date: str | None,
    name: str | None, observation_sessions: int,
) -> dict[str, Any]:
    if len(values) != 7:
        raise ValueError("BaoStock valuation row must have seven fields")
    if not share_observed_date:
        raise ValueError("valuation requires a reliable share observation date")
    trade_date, code, close, pe_ttm, pb, ps_ttm, pcf_ttm = values
    metric = build_shadow_metric(
        trade_date=str(trade_date), stock_code=str(code).split(".")[-1], name=name,
        close=close, pe_ttm=pe_ttm, pb=pb, ps_ttm=ps_ttm, pcf_ttm=pcf_ttm,
        total_share=total_share, observation_sessions=observation_sessions,
    )
    metric["share_observed_date"] = share_observed_date
    return metric


def compute_incremental_returns(
    rows: list[dict[str, Any]], *, last_stored_date: str,
) -> list[dict[str, Any]]:
    ordered = sorted(rows, key=lambda row: str(row.get("date") or ""))
    prior_index = next(
        (index for index, row in enumerate(ordered) if str(row.get("date")) == last_stored_date),
        None,
    )
    if prior_index is None or prior_index == len(ordered) - 1:
        raise ValueError("incremental return calculation requires the preceding trading bar")
    result: list[dict[str, Any]] = []
    previous = finite_or_none(ordered[prior_index].get("close"))
    for row in ordered[prior_index + 1:]:
        close = finite_or_none(row.get("close"))
        if previous is None or previous == 0 or close is None:
            raise ValueError("incremental return calculation requires finite close values")
        current = dict(row)
        current["pct_change"] = round((close / previous - 1) * 100, 10)
        result.append(current)
        previous = close
    return result


def validate_baostock_workers(workers: int) -> int:
    if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
        raise ValueError("BaoStock requires at least 1 independent process")
    if workers > 4:
        raise ValueError("BaoStock supports at most 4 independent processes")
    return workers


def should_relogin(error: str | None) -> bool:
    message = str(error or "").lower()
    return "用户未登录" in message or "not logged" in message or "login" in message and "failed" in message


def partition_items(items: list[Any], *, workers: int) -> list[list[Any]]:
    count = min(validate_baostock_workers(workers), len(items))
    if count == 0:
        return []
    base, remainder = divmod(len(items), count)
    chunks: list[list[Any]] = []
    start = 0
    for index in range(count):
        size = base + (1 if index < remainder else 0)
        chunks.append(items[start:start + size])
        start += size
    return chunks


def attach_share_timeline(
    prices: list[dict[str, Any]], shares: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    timeline = sorted(
        ((str(row.get("date") or ""), finite_or_none(row.get("total_share"))) for row in shares),
        key=lambda item: item[0],
    )
    result: list[dict[str, Any]] = []
    current_share: float | None = None
    index = 0
    for source in sorted(prices, key=lambda row: str(row.get("date") or "")):
        row = dict(source)
        date = str(row.get("date") or "")
        while index < len(timeline) and timeline[index][0] <= date:
            share_value = timeline[index][1]
            if share_value is not None and share_value > 0:
                current_share = share_value
            index += 1
        close = finite_or_none(row.get("close"))
        row["total_share"] = current_share
        row["total_mv"] = close * current_share if close is not None and current_share is not None else None
        result.append(row)
    return result


def build_coverage(
    *, expected: int, succeeded: int, empty: int, failed_symbols: list[str],
) -> dict[str, Any]:
    failed = len(failed_symbols)
    if min(expected, succeeded, empty, failed) < 0 or succeeded + empty + failed != expected:
        raise ValueError("coverage categories must partition expected symbols")
    covered = succeeded + empty
    return {
        "expected": expected,
        "succeeded": succeeded,
        "empty": empty,
        "failed": failed,
        "coverage": round(covered / expected, 6) if expected else 0.0,
        "publishable": expected > 0 and succeeded == expected and empty == 0 and failed == 0,
        "failed_symbols": sorted(set(failed_symbols)),
    }


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POOL = ROOT / "public/data/a-low-chip-stocks.json"
DEFAULT_STATE = ROOT / "data/local/a-share-fundamental-shadow-state.json"
DEFAULT_OUTPUT = ROOT / "data/local/a-share-fundamental-shadow-latest.json"
DEFAULT_ENDPOINT = "https://etf.peekabo.cc/api/public/v1/low-chip-metrics"
BAOSTOCK_FIELDS = "date,code,close,peTTM,pbMRQ,psTTM,pcfNcfTTM"


def load_universe(path: Path = DEFAULT_POOL) -> list[dict[str, str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    unique: dict[str, dict[str, str]] = {}
    for rows in (payload.get("periods") or {}).values():
        for row in rows or []:
            raw = str(row.get("symbol") or "").upper()
            code = raw.split(".")[0]
            suffix = raw.split(".")[-1] if "." in raw else ""
            if len(code) == 6 and code.isdigit() and suffix in {"SH", "SZ"}:
                unique[code] = {"code": code, "name": str(row.get("name") or ""), "market": suffix}
    return [unique[code] for code in sorted(unique)]


def _query_rows(result: Any) -> list[list[str]]:
    if getattr(result, "error_code", "") != "0":
        raise RuntimeError(str(getattr(result, "error_msg", "BaoStock query failed")))
    rows: list[list[str]] = []
    while result.next():
        rows.append(result.get_row_data())
    return rows


def finalize_trade_batch(
    raw_items: list[dict[str, Any]], *, observation_sessions: int,
) -> list[dict[str, Any]]:
    latest_dates = {
        str(item["valuation_rows"][-1][0]) for item in raw_items if item.get("valuation_rows")
    }
    if len(latest_dates) != 1:
        raise ValueError(f"batch latest trade date must be consistent: {sorted(latest_dates)}")
    metrics: list[dict[str, Any]] = []
    for item in raw_items:
        valuation = item["valuation_rows"]
        latest = valuation[-1]
        price_rows = [{"date": str(row[0]), "close": finite_or_none(row[2])} for row in valuation]
        returns = compute_incremental_returns(price_rows[-2:], last_stored_date=str(valuation[-2][0]))
        timeline = attach_share_timeline(price_rows, item["share_rows"])
        latest_timeline = timeline[-1]
        if latest_timeline["total_share"] is None:
            raise ValueError(f"{item['stock_code']} has no reliable share observation")
        eligible_shares = [row for row in item["share_rows"] if str(row["date"]) <= str(latest[0])]
        share_observed_date = max(str(row["date"]) for row in eligible_shares)
        metric = normalize_baostock_row(
            latest, total_share=latest_timeline["total_share"],
            share_observed_date=share_observed_date, name=item.get("stock_name"),
            observation_sessions=observation_sessions,
        )
        metric["pct_change"] = returns[-1]["pct_change"]
        shadow = metric.pop("fundamental_shadow")
        metric["fundamental_shadow_status"] = shadow["status"]
        metric["fundamental_shadow_sessions"] = shadow["observation_sessions"]
        metrics.append(metric)
    return metrics


def _fetch_symbol_with_session(
    bs: Any, item: dict[str, str], _observation_sessions: int,
) -> tuple[str, str, dict[str, Any] | None, str | None]:
    code = item["code"]
    bs_code = f"{'sh' if item['market'] == 'SH' else 'sz'}.{code}"
    try:
        valuation = _query_rows(bs.query_history_k_data_plus(
            bs_code, BAOSTOCK_FIELDS, start_date="2000-01-01", end_date=date.today().isoformat(),
            frequency="d", adjustflag="3",
        ))
        if not valuation:
            return code, "empty", None, None
        if len(valuation) < 2:
            return code, "failed", None, "fewer than two valuation bars"
        year = int(str(valuation[-1][0])[:4])
        shares: list[dict[str, Any]] = []
        for report_year in range(max(2007, year - 6), year + 1):
            for quarter in range(1, 5):
                result = bs.query_profit_data(code=bs_code, year=report_year, quarter=quarter)
                if result.error_code != "0":
                    if should_relogin(str(result.error_msg)):
                        raise RuntimeError(str(result.error_msg))
                    continue
                for row in _query_rows(result):
                    record = dict(zip(result.fields, row))
                    share = finite_or_none(record.get("totalShare"))
                    observed = str(record.get("pubDate") or "")
                    if share is not None and share > 0 and observed and observed <= str(valuation[-1][0]):
                        shares.append({"date": observed, "total_share": share})
        if not shares:
            return code, "failed", None, "no reliable share observation"
        return code, "succeeded", {
            "stock_code": code, "stock_name": item.get("name"),
            "valuation_rows": valuation[-2:], "share_rows": shares,
        }, None
    except Exception as exc:
        return code, "failed", None, f"{type(exc).__name__}: {exc}"


def _fetch_partition(
    items: list[dict[str, str]], observation_sessions: int,
) -> list[tuple[str, str, dict[str, Any] | None, str | None]]:
    import baostock as bs  # type: ignore[import-not-found]

    def timed_fetch(item: dict[str, str]) -> tuple[str, str, dict[str, Any] | None, str | None]:
        parent_conn, child_conn = mp.Pipe(duplex=False)

        def worker():
            try:
                result = _fetch_symbol_with_session(bs, item, observation_sessions)
                child_conn.send(("ok", result))
            except Exception as exc:
                child_conn.send(("err", str(exc)))

        proc = mp.Process(target=worker)
        proc.start()
        if not parent_conn.poll(timeout=60):
            proc.terminate()
            proc.join(timeout=5)
            if proc.is_alive():
                proc.kill()
                proc.join(timeout=2)
            return item["code"], "failed", None, "timeout after 60s"
        try:
            status, result = parent_conn.recv()
        except Exception:
            status = "err"
            result = "pipe recv failed"
        proc.join(timeout=5)
        if proc.is_alive():
            proc.kill()
            proc.join(timeout=2)
        if status == "ok":
            return result  # type: ignore[return-value]
        return item["code"], "failed", None, str(result)

    login = bs.login()
    if login.error_code != "0":
        return [(item["code"], "failed", None, f"login: {login.error_msg}") for item in items]
    try:
        results = [timed_fetch(item) for item in items]
        return results
    finally:
        try:
            bs.logout()
        except Exception:
            pass


def retry_incomplete_or_stale_results(
    universe: list[dict[str, str]],
    results: list[tuple[str, str, dict[str, Any] | None, str | None]],
) -> list[tuple[str, str, dict[str, Any] | None, str | None]]:
    """Retry transient failures and date outliers in one fresh BaoStock session."""
    dates = [
        str(row["valuation_rows"][-1][0])
        for _, status, row, _ in results
        if status == "succeeded" and row is not None and row.get("valuation_rows")
    ]
    if dates:
        counts = Counter(dates)
        largest = max(counts.values())
        target_date = max(value for value, count in counts.items() if count == largest)
        retry_codes = {
            code
            for code, status, row, _ in results
            if status != "succeeded"
            or row is None
            or not row.get("valuation_rows")
            or str(row["valuation_rows"][-1][0]) != target_date
        }
    else:
        retry_codes = {code for code, status, _, _ in results if status != "succeeded"}
    if not retry_codes:
        return results
    by_code = {item["code"]: item for item in universe}
    retry_items = [by_code[code] for code in sorted(retry_codes) if code in by_code]
    repaired = {row[0]: row for row in _fetch_partition(retry_items, 0)}
    return [repaired.get(result[0], result) for result in results]


def atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def observation_sessions(path: Path, trade_date: str) -> int:
    if not path.exists():
        return 1
    payload = json.loads(path.read_text(encoding="utf-8"))
    dates = {str(item) for item in payload.get("observation_dates") or []}
    dates.add(trade_date)
    return len(dates)


def post_metrics(endpoint: str, token: str, metrics: list[dict[str, Any]]) -> int:
    inserted = 0
    for start in range(0, len(metrics), 250):
        body = json.dumps({"metrics": metrics[start:start + 250], "preserve_existing": True}, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(endpoint, data=body, headers={
            "Authorization": f"Bearer {token}", "Content-Type": "application/json",
            "User-Agent": "a-share-fundamental-shadow/1.0",
        })
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = json.loads(response.read().decode("utf-8"))
        expected = len(metrics[start:start + 250])
        confirmed = int(payload.get("inserted") or 0)
        if payload.get("ok") is not True or confirmed != expected:
            raise RuntimeError(f"D1 metric write incomplete: {confirmed}/{expected}")
        inserted += confirmed
    return inserted


def run(
    *, pool_path: Path = DEFAULT_POOL, state_path: Path = DEFAULT_STATE,
    output_path: Path = DEFAULT_OUTPUT, workers: int = 4,
    endpoint: str = DEFAULT_ENDPOINT, token: str | None = None,
    write_d1: bool = True,
) -> dict[str, Any]:
    worker_count = validate_baostock_workers(workers)
    universe = load_universe(pool_path)
    if not universe:
        raise RuntimeError("fundamental shadow universe is empty")
    chunks = partition_items(universe, workers=worker_count)
    if worker_count == 1:
        results = _fetch_partition(chunks[0], 0)
    else:
        context = mp.get_context("spawn")
        with context.Pool(worker_count) as pool:
            async_result = pool.starmap_async(
                _fetch_partition, [(chunk, 0) for chunk in chunks],
            )
            try:
                partitions = async_result.get(timeout=480)
            except Exception:
                pool.terminate()
                pool.join()
                raise
        results = [row for partition in partitions for row in partition]
    results = retry_incomplete_or_stale_results(universe, results)
    raw_items = [row for _, status, row, _ in results if status == "succeeded" and row is not None]
    empty = sum(status == "empty" for _, status, _, _ in results)
    failures = {code: error for code, status, _, error in results if status == "failed"}
    batch_dates = {str(item["valuation_rows"][-1][0]) for item in raw_items}
    if len(batch_dates) != 1:
        raise RuntimeError(f"STAGING BLOCKER inconsistent latest trade dates: {sorted(batch_dates)}")
    trade_date = next(iter(batch_dates))
    sessions = observation_sessions(state_path, trade_date)
    metrics = finalize_trade_batch(raw_items, observation_sessions=sessions)
    coverage = build_coverage(
        expected=len(universe), succeeded=len(metrics), empty=empty,
        failed_symbols=list(failures),
    )
    payload: dict[str, Any] = {
        "schema_version": 1, "mode": "shadow_research_only", "trade_date": trade_date,
        "production_weights_changed": False, "formal_signal_logic_changed": False,
        "production_role": "audit_only", "observation_sessions": sessions,
        "coverage": coverage, "failures": failures,
        "metrics": metrics,
    }
    atomic_write(output_path, payload)
    if not coverage["publishable"]:
        raise RuntimeError(f"STAGING BLOCKER fundamental coverage {coverage}")
    sync_token = token or os.environ.get("LOW_CHIP_SYNC_TOKEN", "")
    if write_d1:
        if not sync_token:
            raise RuntimeError("LOW_CHIP_SYNC_TOKEN is required")
        payload["d1_inserted"] = post_metrics(endpoint, sync_token, metrics)
    previous_dates = []
    if state_path.exists():
        previous_dates = json.loads(state_path.read_text(encoding="utf-8")).get("observation_dates") or []
    state = {"schema_version": 1, "observation_dates": sorted(set(previous_dates) | {trade_date})[-20:]}
    atomic_write(state_path, state)
    atomic_write(output_path, payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pool", type=Path, default=DEFAULT_POOL)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--write", action="store_true", help="write verified metrics to D1")
    args = parser.parse_args()
    payload = run(
        pool_path=args.pool, state_path=args.state, output_path=args.output,
        workers=args.workers, endpoint=args.endpoint, write_d1=args.write,
    )
    print(json.dumps({
        "status": "ok", "trade_date": payload["trade_date"],
        "coverage": payload["coverage"], "observation_sessions": payload["observation_sessions"],
        "d1_inserted": payload.get("d1_inserted"),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

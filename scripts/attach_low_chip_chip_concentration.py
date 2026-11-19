#!/usr/bin/env python3
"""90% 筹码集中度 overlay —— 低筹码流水线 enrichment 阶段。

口径：iWenCai「集中度90」字段（同花顺筹码分布口径），
      集中度90 = (90%成本上限 - 90%成本下限) / (90%成本上限 + 90%成本下限) × 100。
      数值越小代表筹码越集中。页面开关阈值为 < 10%。

写入：
  enrichments[code]["chip_concentration"] = {
      status, concentration_90, as_of, source, reason
  }
  顶层 chip_concentration_contract 记录覆盖数、失败批次和提前停止原因。

fail-soft：保留已验证的结果，未解决标的标成 UNAVAILABLE；
          全部失败时契约 status=unavailable。查询截止后原子写回。

用法：
    python attach_low_chip_chip_concentration.py [TRADE_DATE] [--query-budget-seconds SECONDS]
    TRADE_DATE 缺省时回退 payload 的 data_as_of。
    编排器如需缩短父超时，可用该参数传入扣除落盘余量后的查询预算。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import suppress
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_low_chip_base as base  # noqa: E402

FIELD_PREFIX = "集中度90"
THRESHOLD = 10.0
BATCH_SIZE = 40
MAX_SPLIT_DEPTH = 4
SOURCE = "iwencai-concentration-90"
QUERY_COMMAND = "/root/.hermes/scripts/iwencai-market-query"
# The daily wrapper allows 600 s; leave 60 s for startup, cleanup and output.
QUERY_BUDGET_S = 540.0
REQUEST_TIMEOUT_S = 30.0
QUERY_REAP_TIMEOUT_S = 2.0
BUDGET_REASON = "集中度90 查询预算耗尽"
MISSING_REASON = "iWenCai 未返回该标的当日有效集中度90"


class BudgetExhausted(RuntimeError):
    pass


class QueryCleanupFailed(RuntimeError):
    pass


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise BudgetExhausted(BUDGET_REASON)
    return remaining


def _backoff(attempt: int, deadline: float) -> None:
    delay = base.FETCH_BACKOFF_S * attempt
    if delay >= _remaining(deadline):
        raise BudgetExhausted(BUDGET_REASON)
    time.sleep(delay)


def _run_query(args: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    # In key-pool mode the shell wrapper launches Python without exec. Kill the
    # whole group on timeout, including children holding inherited output pipes.
    expires = time.monotonic() + timeout
    # SIGKILL bypasses the wrapper's EXIT trap for its mktemp file.
    with tempfile.TemporaryDirectory(prefix="lc-conc90-") as scratch:
        proc = subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            start_new_session=True, env={**os.environ, "TMPDIR": scratch},
        )
        try:
            stdout, stderr = proc.communicate(timeout=max(0.0, expires - time.monotonic()))
        except BaseException:
            with suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            try:
                proc.wait(timeout=QUERY_REAP_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                raise QueryCleanupFailed("iWenCai 查询进程组清理超时") from None
            raise
        finally:
            # Do not drain inherited pipes again after killing the process group.
            if proc.stdout is not None:
                proc.stdout.close()
            if proc.stderr is not None:
                proc.stderr.close()
        return subprocess.CompletedProcess(args, proc.returncode, stdout, stderr)


def _fetch(query: str, page: int, deadline: float) -> object:
    for attempt in range(1, base.FETCH_ATTEMPTS + 1):
        timeout = min(REQUEST_TIMEOUT_S, _remaining(deadline))
        try:
            result = _run_query([
                QUERY_COMMAND, "-q", query, "--page", str(page), "--limit", "100",
                "--timeout", str(max(1, int(timeout))),
            ], timeout)
            if result.returncode == 0:
                return json.loads(result.stdout)
            detail = (result.stderr or result.stdout or "").strip()
            transient = base._is_transient(detail)
            # Provider output can contain credential or request metadata.
            label = "上游请求失败" if transient else "上游鉴权或额度错误"
            reason = f"{label} (exit {result.returncode})"
        except subprocess.TimeoutExpired:
            transient = True
            reason = "请求超时"
        _remaining(deadline)
        if not transient or attempt == base.FETCH_ATTEMPTS:
            raise RuntimeError(f"iWenCai: {reason}")
        _backoff(attempt, deadline)
    raise AssertionError("unreachable")


def _valid_page(
    envelope: object, total: int | None, seen: set[str],
) -> tuple[int, bool, list[dict], set[str]] | None:
    if not isinstance(envelope, dict) or envelope.get("success", True) is not True:
        return None
    count, more, rows = (envelope.get(k) for k in ("code_count", "has_more", "datas"))
    if type(count) is not int or count < 0 or (total is not None and total != count):
        return None
    if type(more) is not bool or not isinstance(rows, list):
        return None
    codes = set()
    for row in rows:
        code = row.get("股票代码") if isinstance(row, dict) else None
        if not isinstance(code, str) or not re.fullmatch(r"[0-9]{6}(?:\.(?:SH|SZ|BJ))?", code):
            return None
        if code in codes or code in seen:
            return None
        codes.add(code)
    size = len(seen) + len(rows)
    if (more and (not rows or size >= count)) or (not more and size != count):
        return None
    return count, more, rows, codes


def _pages(query: str, deadline: float) -> Iterator[list[dict]]:
    """Validate each page before yielding it; a later page can fail independently."""
    seen: set[str] = set()
    total = None
    for page in range(1, base.MAX_PAGES + 1):
        for attempt in range(1, 4):
            try:
                candidate = _valid_page(_fetch(query, page, deadline), total, seen)
            except json.JSONDecodeError:
                candidate = None
            if candidate is not None:
                total, more, rows, codes = candidate
                seen.update(codes)
                yield rows
                if not more:
                    return
                break
            if attempt == 3:
                raise RuntimeError(f"iWenCai 分页完整性校验失败 (第 {page} 页)")
            _backoff(attempt, deadline)
    raise RuntimeError(f"iWenCai 分页超过 {base.MAX_PAGES} 页")


def _value(row: dict, compact_date: str) -> float | None:
    """Accept only the requested observation date and a valid percentage."""
    value = row.get(f"{FIELD_PREFIX}[{compact_date}]")
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and 0 <= number <= 100 else None


def _query_codes(codes: list[str]) -> str:
    """批量查询用带交易所后缀的完整代码。

    iWenCai 把裸 6 位代码批量丢进查询时会误识别成基金：2026-07-31 的 20 只
    裸代码返回 `000931.OF`、`600605.OF`…，code_count 只有 3（真正匹配的
    sz 三只），其余静默缺失。带 `.SZ`/`.SH` 后缀后同样 20 只返回 20 条。
    """
    return "、".join(codes)


def _fetch_chunk(
    codes: list[str], compact_date: str, deadline: float, values: dict[str, float],
    reasons: dict[str, str], errors: list[str], depth: int = 0,
) -> None:
    _remaining(deadline)
    requested = set(codes)
    query = _query_codes(codes) + f" {FIELD_PREFIX}[{compact_date}]"
    try:
        for rows in _pages(query, deadline):
            for row in rows:
                code = row["股票代码"]
                if code in requested and (value := _value(row, compact_date)) is not None:
                    values[code] = value
    except (BudgetExhausted, QueryCleanupFailed):
        raise
    except (OSError, ValueError, RuntimeError) as exc:
        reason = f"{exc}"[:200]
        errors.append(reason)
        reasons.update({code: reason for code in codes if code not in values})
        return

    # Legal empty or short envelopes can be caused by query size. Re-query
    # only missing codes, with a shared deadline and the existing depth cap.
    if depth < MAX_SPLIT_DEPTH and len(codes) > 1:
        mid = len(codes) // 2
        for half in (codes[:mid], codes[mid:]):
            missing = [code for code in half if code not in values]
            if missing:
                _fetch_chunk(missing, compact_date, deadline, values, reasons, errors, depth + 1)
    # A completed child with no data stays distinct from a later untouched child.
    for code in codes:
        if code not in values:
            reasons.setdefault(code, MISSING_REASON)


def attach(payload: dict, trade_date: str, *, fetch=None, budget_s: float | None = None) -> dict:
    """把集中度90 写进 payload 的 enrichments，返回契约。不写文件。"""
    if budget_s is not None and (not math.isfinite(budget_s) or budget_s < 0):
        raise ValueError("query budget must be a finite, nonnegative number")
    budget = QUERY_BUDGET_S if budget_s is None else min(budget_s, QUERY_BUDGET_S)
    deadline = time.monotonic() + budget
    compact_date = "".join(ch for ch in str(trade_date) if ch.isdigit())[:8]
    codes = payload.get("intersection") or payload.get("intersection_before_filters") or []
    enrichments = payload.setdefault("enrichments", {})

    if not codes:
        contract = {
            "status": "empty_pool",
            "as_of": trade_date,
            "source": SOURCE,
            "threshold": THRESHOLD,
            "coverage": {"requested": 0, "ok": 0, "unavailable": 0},
            "failed_batches": [],
            "stop_reason": None,
        }
        payload["chip_concentration_contract"] = contract
        return contract

    values: dict[str, float] = {}
    reasons: dict[str, str] = {}
    failures: list[str] = []
    stop_reason = None
    stopped_detail = MISSING_REASON
    for start in range(0, len(codes), BATCH_SIZE):
        batch = codes[start:start + BATCH_SIZE]
        errors: list[str] = []
        try:
            _remaining(deadline)
            if fetch is None:
                _fetch_chunk(batch, compact_date, deadline, values, reasons, errors)
            else:
                values.update(fetch(batch, compact_date))
        except BudgetExhausted:
            stop_reason = "budget_exhausted"
            stopped_detail = BUDGET_REASON
            errors.append(stopped_detail)
        except QueryCleanupFailed as exc:
            stop_reason = "query_cleanup_timeout"
            stopped_detail = str(exc)
            errors.append(stopped_detail)
        except (Exception, SystemExit) as exc:  # noqa: BLE001 单批失败隔离
            reason = str(exc)[:200] or "iWenCai 批次查询失败"
            errors.append(reason)
            reasons.update({code: reason for code in batch if code not in values})
        if errors:
            failures.append(f"batch {start // BATCH_SIZE + 1}: {'; '.join(dict.fromkeys(errors))}")
        if stop_reason:
            break

    ok = 0
    unavailable = 0
    for code in codes:
        enr = enrichments.setdefault(code, {})
        value = values.get(code)
        if value is None:
            unavailable += 1
            enr["chip_concentration"] = {
                "status": "UNAVAILABLE",
                "concentration_90": None,
                "as_of": trade_date,
                "source": SOURCE,
                "reason": reasons.get(code, stopped_detail),
            }
            continue
        ok += 1
        enr["chip_concentration"] = {
            "status": "ok",
            "concentration_90": round(value, 4),
            "as_of": trade_date,
            "source": SOURCE,
            "reason": None,
        }

    contract = {
        "status": "ok" if ok else "unavailable",
        "as_of": trade_date,
        "source": SOURCE,
        "threshold": THRESHOLD,
        "coverage": {"requested": len(codes), "ok": ok, "unavailable": unavailable},
        "failed_batches": failures,
        "stop_reason": stop_reason,
    }
    payload["chip_concentration_contract"] = contract
    return contract


def _write_atomic(path: Path, payload: dict) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", delete=False) as handle:
            temporary = Path(handle.name)
            temporary.chmod(path.stat().st_mode & 0o777)
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trade_date", nargs="?")
    parser.add_argument("--query-budget-seconds", type=float,
                        help=f"override query budget, capped at {QUERY_BUDGET_S:g} seconds")
    args = parser.parse_args()
    payload = json.loads(base.DATA.read_text(encoding="utf-8"))
    trade_date = args.trade_date or payload.get("data_as_of") or ""
    contract = attach(payload, trade_date, budget_s=args.query_budget_seconds)
    _write_atomic(base.DATA, payload)
    coverage = contract["coverage"]
    if contract["status"] == "empty_pool":
        print("chip concentration skipped: empty intersection", flush=True)
        return 0
    hits = sum(
        1
        for code in (payload.get("intersection") or [])
        if (payload["enrichments"].get(code, {}).get("chip_concentration") or {}).get("concentration_90") is not None
        and payload["enrichments"][code]["chip_concentration"]["concentration_90"] < THRESHOLD
    )
    print(
        f"chip concentration: pool={coverage['requested']} ok={coverage['ok']} "
        f"unavailable={coverage['unavailable']} below_{THRESHOLD:g}={hits} "
        f"failed_batches={len(contract['failed_batches'])} "
        f"stop_reason={contract['stop_reason'] or 'none'}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

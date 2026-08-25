#!/usr/bin/env python3
"""Collect FTShare read-only data into an isolated research shadow snapshot."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "public/data/a-low-chip-stocks.json"
DEFAULT_OUTPUT = ROOT / "public/data/ftshare-shadow.json"
MCP_URL = "https://market.ft.tech/gateway/mcp"
PROTOCOL_VERSION = "2025-03-26"
CN = ZoneInfo("Asia/Shanghai")


class FTShareToolError(RuntimeError):
    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.retryable = retryable

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "retryable": self.retryable}


def parse_sse_json(text: str) -> dict[str, Any]:
    payloads = []
    for line in text.splitlines():
        if not line.startswith("data:"):
            continue
        value = line[5:].strip()
        if not value.startswith("{"):
            continue
        payloads.append(json.loads(value))
    if not payloads:
        raise RuntimeError("FTShare MCP response contains no JSON data event")
    error_payload = next((payload for payload in payloads if payload.get("error")), None)
    if error_payload is not None and error_payload is not payloads[-1]:
        raise RuntimeError(f"FTShare MCP JSON-RPC error: {error_payload['error']}")
    return payloads[-1]


class FTShareMCPClient:
    def __init__(self, url: str = MCP_URL, timeout: int = 60):
        self.url = url
        self.timeout = timeout
        self.session_id = ""
        self.protocol_version = PROTOCOL_VERSION
        self._next_id = 1
        self._initialize()

    def _post(self, payload: dict[str, Any], *, expect_json: bool = True) -> tuple[dict[str, Any] | None, Any]:
        headers = {
            "content-type": "application/json",
            "accept": "application/json, text/event-stream",
        }
        if self.session_id:
            headers["mcp-session-id"] = self.session_id
            headers["mcp-protocol-version"] = self.protocol_version
        request = urllib.request.Request(
            self.url,
            data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            method="POST",
            headers=headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8", "replace")
                response_headers = response.headers
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            raise RuntimeError(f"FTShare MCP HTTP {exc.code}: {body[:300]}") from exc
        if not self.session_id:
            self.session_id = response_headers.get("mcp-session-id", "")
        if not expect_json or not body.strip():
            return None, response_headers
        return parse_sse_json(body), response_headers

    def _initialize(self) -> None:
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "etf-compass-ftshare-shadow", "version": "1.0"},
            },
        }
        self._next_id += 1
        response, _headers = self._post(payload)
        result = (response or {}).get("result") or {}
        self.protocol_version = str(result.get("protocolVersion") or PROTOCOL_VERSION)
        if not self.session_id:
            raise RuntimeError("FTShare MCP initialize did not return Mcp-Session-Id")
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}, expect_json=False)

    def _rpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = {"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": params or {}}
        self._next_id += 1
        response, _headers = self._post(payload)
        if response is None:
            raise RuntimeError(f"FTShare MCP {method} returned empty response")
        if response.get("error"):
            error = response["error"]
            raise RuntimeError(f"FTShare MCP JSON-RPC error: {error}")
        return response

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        response = self._rpc("tools/call", {"name": name, "arguments": arguments})
        result = response.get("result") or {}
        if result.get("isError"):
            error_obj: dict[str, Any] = {}
            content = result.get("content") or []
            if content and isinstance(content[0], dict):
                try:
                    parsed = json.loads(str(content[0].get("text") or "{}"))
                    error_obj = parsed.get("error") or {}
                except json.JSONDecodeError:
                    error_obj = {}
            raise FTShareToolError(
                str(error_obj.get("code") or "TOOL_ERROR"),
                str(error_obj.get("message") or f"FTShare tool {name} failed"),
                bool(error_obj.get("retryable")),
            )
        structured = result.get("structuredContent") or result.get("structured_content")
        if not isinstance(structured, dict):
            content = result.get("content") or []
            if content and isinstance(content[0], dict):
                structured = json.loads(str(content[0].get("text") or "{}"))
        if not isinstance(structured, dict) or not isinstance(structured.get("data"), list):
            raise RuntimeError(f"FTShare tool {name} returned invalid structured content")
        return structured


def metadata_quality(structured: dict[str, Any]) -> dict[str, Any]:
    metadata = structured.get("metadata") or {}
    data = structured.get("data") or []
    total = metadata.get("total")
    returned = metadata.get("returned")
    actual = len(data) if isinstance(data, list) else None
    mismatch = False
    if isinstance(returned, int) and isinstance(actual, int) and returned != actual:
        mismatch = True
    if isinstance(total, int) and isinstance(returned, int) and total != returned:
        mismatch = True
    truncated = bool(metadata.get("truncated"))
    return {
        "total": total,
        "returned": returned,
        "actual": actual,
        "truncated": truncated,
        "warnings": list(metadata.get("warnings") or []),
        "count_mismatch": mismatch,
        "complete": not truncated and not mismatch,
    }


def latest_row(rows: list[dict[str, Any]], *date_fields: str) -> dict[str, Any] | None:
    valid = [row for row in rows if isinstance(row, dict)]
    if not valid:
        return None
    return max(valid, key=lambda row: tuple(str(row.get(field) or "") for field in date_fields))


def number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def compare_low_chip(source: dict[str, Any], collected: dict[str, Any]) -> dict[str, Any]:
    comparisons: dict[str, Any] = {}
    for symbol, item in (collected.get("items") or {}).items():
        source_metrics = ((source.get("enrichments") or {}).get(symbol) or {}).get("shareholder_metrics") or {}
        holder = item.get("holder_latest") or {}
        float_holder = item.get("float_holder_latest") or {}
        if not source_metrics or not holder:
            continue
        source_count = number(source_metrics.get("shareholder_count"))
        provider_count = number(holder.get("holder_num"))
        source_change = number(source_metrics.get("shareholder_change_pct"))
        provider_change = number(holder.get("holder_num_change_ratio"))
        source_top10 = number(source_metrics.get("top10_float_ratio"))
        provider_top10 = number(float_holder.get("share_holding"))
        if provider_top10 is None:
            provider_top10 = number(holder.get("ften_holder_ratio"))
        comparisons[symbol] = {
            "source_report_period": source_metrics.get("report_period"),
            "ftshare_report_period": holder.get("report_date"),
            "report_period_match": bool(source_metrics.get("report_period")) and str(source_metrics.get("report_period")) == str(holder.get("report_date")),
            "holder_count_delta": round(provider_count - source_count, 4) if provider_count is not None and source_count is not None else None,
            "holder_change_pct_delta": round(provider_change - source_change, 4) if provider_change is not None and source_change is not None else None,
            "top10_float_ratio_delta": round(provider_top10 - source_top10, 4) if provider_top10 is not None and source_top10 is not None else None,
        }
    return {"compared": len(comparisons), "items": comparisons}


def count_low_chip_incomplete(collected: dict[str, Any]) -> int:
    count = 0
    for item in (collected.get("items") or {}).values():
        quality = item.get("quality") or {}
        for key in ("holder", "float_holder"):
            section = quality.get(key)
            if isinstance(section, dict) and not section.get("complete", False):
                count += 1
    return count


def collect_low_chip(client: FTShareMCPClient, symbols: list[str], sleep_seconds: float = 0.15) -> dict[str, Any]:
    items: dict[str, Any] = {}
    errors: dict[str, Any] = {}
    holder_success = 0
    float_success = 0
    for index, symbol in enumerate(symbols):
        item: dict[str, Any] = {"symbol": symbol, "quality": {}}
        symbol_errors: dict[str, Any] = {}
        try:
            holder = client.call_tool("ft_stock_holders_number", {"stock_code": symbol})
            item["holder_latest"] = latest_row(holder["data"], "publish_date", "report_date")
            item["holder_history_count"] = len(holder["data"])
            item["quality"]["holder"] = metadata_quality(holder)
            holder_success += 1
        except FTShareToolError as exc:
            symbol_errors["holders"] = exc.as_dict()
        except Exception as exc:  # noqa: BLE001
            symbol_errors["holders"] = {"code": type(exc).__name__, "message": str(exc), "retryable": False}
        if sleep_seconds:
            time.sleep(sleep_seconds)
        try:
            float_holder = client.call_tool("ft_stock_float_holders", {"stock_code": symbol})
            item["float_holder_latest"] = latest_row(float_holder["data"], "publish_date")
            item["float_holder_history_count"] = len(float_holder["data"])
            quality = metadata_quality(float_holder)
            item["quality"]["float_holder"] = quality
            item["quality"]["float_holder_truncated"] = quality["truncated"]
            float_success += 1
        except FTShareToolError as exc:
            symbol_errors["float_holders"] = exc.as_dict()
        except Exception as exc:  # noqa: BLE001
            symbol_errors["float_holders"] = {"code": type(exc).__name__, "message": str(exc), "retryable": False}
        items[symbol] = item
        if symbol_errors:
            errors[symbol] = symbol_errors
        if sleep_seconds and index + 1 < len(symbols):
            time.sleep(sleep_seconds)
    return {
        "requested": len(symbols),
        "holder_success": holder_success,
        "float_holder_success": float_success,
        "items": items,
        "errors": errors,
    }


def collect_market(client: FTShareMCPClient, trade_date: str, auction_page_size: int = 200) -> dict[str, Any]:
    calls = {
        "limit_up": ("ft_limit_up_pool", {"trade_date": trade_date}),
        "limit_up_break": ("ft_limit_up_break_pool", {"trade_date": trade_date}),
        "limit_down": ("ft_limit_down_pool", {"trade_date": trade_date}),
        "auction": ("ft_auction_results", {"trade_date": trade_date, "page": 1, "page_size": auction_page_size}),
    }
    output: dict[str, Any] = {"trade_date": trade_date, "errors": {}}
    for key, (tool, arguments) in calls.items():
        try:
            structured = client.call_tool(tool, arguments)
            output[key] = {"data": structured["data"], "quality": metadata_quality(structured)}
        except FTShareToolError as exc:
            output["errors"][key] = exc.as_dict()
        except Exception as exc:  # noqa: BLE001
            output["errors"][key] = {"code": type(exc).__name__, "message": str(exc), "retryable": False}
    output["summary"] = {
        f"{key}_returned": len(value.get("data") or [])
        for key, value in output.items()
        if key not in {"trade_date", "errors", "summary"} and isinstance(value, dict)
    }
    return output


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--max-symbols", type=int, default=20, help="Bounded canary size; 0 means all symbols")
    parser.add_argument("--trade-date", default="", help="YYYYMMDD; defaults to input data_as_of")
    parser.add_argument("--sleep-seconds", type=float, default=0.15)
    parser.add_argument("--timeout", type=int, default=60)
    args = parser.parse_args()

    source = json.loads(args.input.read_text(encoding="utf-8"))
    symbols = list(source.get("intersection") or [])
    if args.max_symbols > 0:
        symbols = symbols[: args.max_symbols]
    trade_date = args.trade_date or str(source.get("data_as_of") or "").replace("-", "")
    if len(trade_date) != 8 or not trade_date.isdigit():
        raise SystemExit("STAGING BLOCKER: missing valid trade date")

    top_errors: dict[str, Any] = {}
    try:
        client = FTShareMCPClient(timeout=args.timeout)
    except Exception as exc:  # noqa: BLE001
        top_errors["mcp_initialize"] = {
            "code": type(exc).__name__,
            "message": str(exc),
            "retryable": isinstance(exc, (TimeoutError, urllib.error.URLError)),
        }
        low_chip = {
            "requested": len(symbols),
            "holder_success": 0,
            "float_holder_success": 0,
            "items": {},
            "errors": {},
            "comparison": {"compared": 0, "items": {}},
        }
        market = {"trade_date": trade_date, "errors": {}, "summary": {}}
    else:
        low_chip = collect_low_chip(client, symbols, sleep_seconds=max(0, args.sleep_seconds))
        low_chip["comparison"] = compare_low_chip(source, low_chip)
        market = collect_market(client, trade_date)
    quality_failures = len(low_chip["errors"]) + len(market["errors"])
    market_incomplete = sum(
        1
        for section in (market.get("limit_up"), market.get("limit_up_break"), market.get("limit_down"), market.get("auction"))
        if isinstance(section, dict) and not section.get("quality", {}).get("complete", False)
    )
    low_chip_incomplete = count_low_chip_incomplete(low_chip)
    status = "ok" if quality_failures == 0 and market_incomplete == 0 and low_chip_incomplete == 0 and not top_errors else "degraded"
    payload = {
        "schema_version": "ftshare-shadow-v1",
        "mode": "shadow_research_only",
        "production_change_allowed": False,
        "generated_at": dt.datetime.now(CN).isoformat(timespec="seconds"),
        "status": status,
        "source": {"provider": "FTShare", "mcp_url": MCP_URL, "server_version": "0.1.1"},
        "errors": top_errors,
        "quality_summary": {
            "low_chip_incomplete_sections": low_chip_incomplete,
            "market_incomplete_sections": market_incomplete,
        },
        "low_chip": low_chip,
        "market": market,
        "disclaimer": "影子研究数据；不修改生产筛选、权重、仓位或交易动作。",
    }
    atomic_write_json(args.output, payload)
    print(json.dumps({
        "status": status,
        "trade_date": trade_date,
        "low_chip_requested": low_chip["requested"],
        "holder_success": low_chip["holder_success"],
        "float_holder_success": low_chip["float_holder_success"],
        "low_chip_error_symbols": len(low_chip["errors"]),
        "market_errors": market["errors"],
        "market_summary": market["summary"],
        "output": str(args.output),
    }, ensure_ascii=False))
    return 0 if status == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())

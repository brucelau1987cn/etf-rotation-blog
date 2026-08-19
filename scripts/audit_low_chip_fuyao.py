#!/usr/bin/env python3
"""Shadow-audit low-chip iWenCai results with Fuyao structured data."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo


BASE_URL = "https://fuyao.aicubes.cn"
ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = ROOT / "public/data/a-low-chip-stocks.json"
DEFAULT_OUTPUT = ROOT / "public/data/model-lab/low-chip-fuyao-shadow.json"
DEFAULT_ENV_FILE = Path("/root/.hermes/credentials/fuyao.env")
CN = ZoneInfo("Asia/Shanghai")
BACKOFFS = (2.0, 5.0, 10.0)
FINANCIAL_IDS = {
    "index_weighted_avg_roe": "roe",
    "sale_net_interest_ratio": "net_margin",
    "sale_gross_margin": "gross_margin",
    "assets_debt_ratio": "debt_ratio",
    "net_profit_cash_content": "cash_profit_ratio",
}
FINANCIAL_FIELDS = tuple(FINANCIAL_IDS.values())


def load_api_key(env_file: Path = DEFAULT_ENV_FILE, environ: dict[str, str] | None = None) -> str:
    values = dict(os.environ if environ is None else environ)
    for name in ("HITHINK_FINANCE_API_KEY", "FUYAO_API_KEY"):
        value = values.get(name, "").strip()
        if value:
            return value
    if env_file.exists():
        for raw in env_file.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.removeprefix("export ").split("=", 1)
            values[name.strip()] = value.strip().strip('"').strip("'")
    for name in ("HITHINK_FINANCE_API_KEY", "FUYAO_API_KEY"):
        value = values.get(name, "").strip()
        if value:
            return value
    raise RuntimeError("HITHINK_FINANCE_API_KEY is missing")


class FuyaoClient:
    def __init__(
        self,
        api_key: str,
        *,
        qps: float = 2.0,
        opener: Callable[..., Any] = urllib.request.urlopen,
        sleeper: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if not api_key:
            raise ValueError("api_key is required")
        if qps <= 0 or qps > 5:
            raise ValueError("qps must be within (0, 5]")
        self._api_key = api_key
        self._min_interval = 1.0 / qps
        self._opener = opener
        self._sleep = sleeper
        self._monotonic = monotonic
        self._last_request_at: float | None = None

    def _pace(self) -> None:
        now = self._monotonic()
        if self._last_request_at is not None:
            remaining = self._min_interval - (now - self._last_request_at)
            if remaining > 0:
                self._sleep(remaining)
                now = self._monotonic()
        self._last_request_at = now

    def get(self, path: str) -> dict[str, Any]:
        url = path if path.startswith("http") else f"{BASE_URL}{path}"
        for attempt in range(len(BACKOFFS) + 1):
            self._pace()
            request = urllib.request.Request(
                url,
                headers={
                    "X-api-key": self._api_key,
                    "User-Agent": "ETFCompassFuyaoShadow/1.0",
                    "Accept": "application/json",
                },
            )
            try:
                with self._opener(request, timeout=30) as response:
                    payload = json.loads(response.read())
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", "replace")
                if exc.code in (429, 500, 502, 503, 504) and attempt < len(BACKOFFS):
                    self._sleep(BACKOFFS[attempt])
                    continue
                raise RuntimeError(f"Fuyao HTTP {exc.code}: {body[:300]}") from exc
            code = payload.get("code")
            if code in (429, 4001, 5001, 5002, 5003) and attempt < len(BACKOFFS):
                self._sleep(BACKOFFS[attempt])
                continue
            if code != 0:
                raise RuntimeError(f"Fuyao business error {code}: {payload.get('message')}")
            return payload
        raise RuntimeError("Fuyao retry budget exhausted")

    def snapshots(self, codes: list[str]) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for offset in range(0, len(codes), 100):
            batch = codes[offset:offset + 100]
            query = urllib.parse.urlencode({"thscodes": ",".join(batch)})
            payload = self.get(f"/api/a-share/prices/snapshot?{query}")
            items = ((payload.get("data") or {}).get("item") or [])
            result.update({str(item.get("thscode")): item for item in items if item.get("thscode")})
        return result

    def valuations(self, codes: list[str]) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for offset in range(0, len(codes), 100):
            batch = codes[offset:offset + 100]
            query = urllib.parse.urlencode({"thscodes": ",".join(batch)})
            payload = self.get(f"/api/a-share/valuations/snapshot?{query}")
            items = ((payload.get("data") or {}).get("item") or [])
            result.update({str(item.get("thscode")): item for item in items if item.get("thscode")})
        return result

    def financials(self, code: str, report: str) -> dict[str, float | None]:
        query = urllib.parse.urlencode({"thscode": code, "report": report})
        payload = self.get(f"/api/a-share/financials/indicators?{query}")
        result: dict[str, float | None] = {}
        for ability in ((payload.get("data") or {}).get("abilities") or []):
            for indicator in ability.get("indicators") or []:
                field = FINANCIAL_IDS.get(str(indicator.get("index_id")))
                if not field:
                    continue
                value = indicator.get("value")
                try:
                    result[field] = float(value) if value is not None else None
                except (TypeError, ValueError):
                    result[field] = None
        return result


def _to_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None


def _financial_report(report_period: Any) -> str | None:
    text = str(report_period or "")
    if len(text) != 8 or not text.isdigit():
        return None
    suffix = {"0331": "1", "0630": "2", "0930": "3", "1231": "4"}.get(text[4:])
    return f"{text[:4]}-{suffix}" if suffix else None


def _check(local: Any, remote: Any, tolerance: float) -> dict[str, Any]:
    left = _to_float(local)
    right = _to_float(remote)
    if left is None or right is None:
        return {"status": "unavailable", "iwencai": left, "fuyao": right, "difference": None}
    difference = right - left
    return {
        "status": "match" if abs(difference) <= tolerance else "mismatch",
        "iwencai": left,
        "fuyao": right,
        "difference": round(difference, 8),
        "tolerance": tolerance,
    }


def build_audit(payload: dict[str, Any], client: Any, max_symbols: int = 0) -> dict[str, Any]:
    all_codes = [str(code) for code in payload.get("intersection") or []]
    codes = all_codes[:max_symbols] if max_symbols > 0 else all_codes
    week = {str(row.get("symbol")): row for row in ((payload.get("periods") or {}).get("week") or [])}
    enrichments = payload.get("enrichments") or {}
    snapshots = client.snapshots(codes)
    valuations = client.valuations(codes)
    rows: dict[str, Any] = {}
    counters = {
        "price_matches": 0,
        "price_mismatches": 0,
        "price_unavailable": 0,
        "financial_matches": 0,
        "financial_mismatches": 0,
        "financial_unavailable": 0,
    }

    for code in codes:
        base = week.get(code) or {}
        local_financials = (enrichments.get(code) or {}).get("financials") or {}
        report = _financial_report(local_financials.get("report_period"))
        remote_financials = client.financials(code, report) if report else {}
        local_price = base.get("price")
        remote_price = (snapshots.get(code) or {}).get("last_price")
        local_price_number = _to_float(local_price)
        price_tolerance = max(0.02, abs(local_price_number or 0.0) * 0.001)
        price_check = _check(local_price, remote_price, price_tolerance)
        price_counter = {
            "match": "price_matches",
            "mismatch": "price_mismatches",
            "unavailable": "price_unavailable",
        }[price_check["status"]]
        counters[price_counter] += 1

        financial_checks = {}
        for field in FINANCIAL_FIELDS:
            check = _check(local_financials.get(field), remote_financials.get(field), 0.1)
            financial_checks[field] = check
            financial_counter = {
                "match": "financial_matches",
                "mismatch": "financial_mismatches",
                "unavailable": "financial_unavailable",
            }[check["status"]]
            counters[financial_counter] += 1

        valuation = valuations.get(code) or {}
        rows[code] = {
            "name": base.get("name") or "",
            "price_check": price_check,
            "financial_report": report,
            "financial_checks": financial_checks,
            "valuation": {
                key: valuation.get(key)
                for key in ("pe_ttm", "pe_mrq", "pb_mrq", "ps_ttm", "pcf_ttm")
            },
        }

    return {
        "schema_version": "low-chip-fuyao-shadow-v1",
        "mode": "shadow",
        "production_effect": "none",
        "data_as_of": payload.get("data_as_of"),
        "generated_at": dt.datetime.now(CN).isoformat(timespec="seconds"),
        "sources": {
            "selection": "iWenCai SkillHub",
            "verification": "Fuyao structured REST API",
        },
        "limits": {
            "qps_max": 5,
            "configured_qps": getattr(client, "_min_interval", None) and round(1 / client._min_interval, 3),
            "retry_backoff_seconds": list(BACKOFFS),
        },
        "summary": {
            "symbols": len(codes),
            "source_symbols": len(all_codes),
            "snapshot_coverage": len(snapshots),
            "valuation_coverage": len(valuations),
            **counters,
        },
        "rows": rows,
    }


def build_failure_audit(data_as_of: Any, source_symbols: int, error: str) -> dict[str, Any]:
    return {
        "schema_version": "low-chip-fuyao-shadow-v1",
        "status": "unavailable",
        "mode": "shadow",
        "production_effect": "none",
        "data_as_of": data_as_of,
        "generated_at": dt.datetime.now(CN).isoformat(timespec="seconds"),
        "sources": {
            "selection": "iWenCai SkillHub",
            "verification": "Fuyao structured REST API",
        },
        "summary": {"source_symbols": source_symbols},
        "error": str(error)[:500],
        "rows": {},
    }


def _write_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--qps", type=float, default=2.0)
    parser.add_argument("--max-symbols", type=int, default=0)
    parser.add_argument("--soft-fail", action="store_true")
    args = parser.parse_args()

    source = json.loads(args.input.read_text(encoding="utf-8"))
    try:
        api_key = load_api_key(args.env_file)
        audit = build_audit(source, FuyaoClient(api_key, qps=args.qps), max_symbols=args.max_symbols)
        audit["status"] = "ok"
    except Exception as exc:
        if not args.soft_fail:
            raise
        audit = build_failure_audit(source.get("data_as_of"), len(source.get("intersection") or []), str(exc))
    _write_atomic(args.output, audit)
    print(json.dumps({"status": audit["status"], "output": str(args.output), "summary": audit["summary"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

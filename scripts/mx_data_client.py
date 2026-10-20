#!/usr/bin/env python3
"""Shared mx-data client with API-key pool rotation.

Why this exists (2026-10-20 incident):
- mx-data (Eastmoney Miaoxiang) enforces a per-KEY daily quota. A key that goes
  inactive is downgraded to 10 calls/day (code=113 "调用次数已达到上限").
- The low-chip attach scripts (theme concepts / technical / ETF holdings) read a
  SINGLE key from ``MX_APIKEY``. When that one key hit its quota, all three
  stages silently produced empty fields: theme_concepts 0/88, etf_holdings 0/88,
  technical 10/88 (exactly the remaining 10 calls) — and the pipeline still
  reported success.
- ``MX_APIKEYS`` already holds a pool of 4 keys, but nothing consumed it.

This module resolves the pool, rotates on quota errors, and reports which key
paid for each call so the pipeline can surface degraded coverage explicitly.

Environment:
  MX_APIKEYS : JSON list of keys (preferred). Falls back to MX_APIKEY.
  MX_DATA_ENV: optional path to a file with `export MX_APIKEY=...` lines.

Quota errors we rotate on:
  113 = daily call limit reached (per-key quota exhausted)
  112 = request rate too high (shared rate limit; a different key may still work)
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Iterable

MX_SKILL_DIR = "/root/.hermes/skills/mx-data"
QUOTA_ERROR_CODES = (112, 113)
DEFAULT_ENV_FILE = Path("/root/.hermes/.env")


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return values
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        values[key] = value.strip().strip('"').strip("'")
    return values


def resolve_key_pool(env_file: Path | None = None) -> list[str]:
    """Return the ordered list of usable mx-data API keys (dedup, order preserved).

    Priority:
      1. MX_APIKEYS env (JSON list or comma-separated)
      2. MX_APIKEYS from the env file
      3. MX_APIKEY from env, then from the env file
    """
    pool: list[str] = []
    raw = os.getenv("MX_APIKEYS") or ""
    if not raw:
        file_values = _read_env_file(env_file or DEFAULT_ENV_FILE)
        raw = file_values.get("MX_APIKEYS") or ""
        if not raw:
            single = os.getenv("MX_APIKEY") or file_values.get("MX_APIKEY") or ""
            raw = json.dumps([single]) if single else ""
    if raw:
        text = raw.strip()
        if text.startswith("["):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = []
            candidates = [str(k).strip() for k in parsed if str(k).strip()]
        else:
            candidates = [part.strip() for part in text.split(",") if part.strip()]
        for key in candidates:
            if key not in pool:
                pool.append(key)
    return pool


class MXQuotaExhausted(RuntimeError):
    """Every key in the pool refused the call (quota or rate limit)."""


class MXPoolClient:
    """Thin wrapper over mx_data.MXData that rotates keys on quota errors.

    `query` returns the raw provider payload; `parse_result` stays the
    provider's static method so existing parsing code keeps working.
    """

    def __init__(
        self,
        pool: list[str] | None = None,
        *,
        sleeper=time.sleep,
        max_attempts: int | None = None,
    ) -> None:
        self.pool = pool if pool is not None else resolve_key_pool()
        if not self.pool:
            raise RuntimeError(
                "no mx-data API key available: set MX_APIKEYS (JSON list) or MX_APIKEY"
            )
        self._sleeper = sleeper
        self._max_attempts = max_attempts or (len(self.pool) * 2)
        self._clients: dict[str, Any] = {}
        self._cursor = 0
        self.exhausted_keys: set[str] = set()
        self.calls_by_key: dict[str, int] = {key: 0 for key in self.pool}

    # -- provider plumbing -------------------------------------------------
    def _client(self, key: str):
        if key not in self._clients:
            if MX_SKILL_DIR not in sys.path:
                sys.path.insert(0, MX_SKILL_DIR)
            from mx_data import MXData

            self._clients[key] = MXData(api_key=key)
        return self._clients[key]

    @staticmethod
    def parse_result(result):
        if MX_SKILL_DIR not in sys.path:
            sys.path.insert(0, MX_SKILL_DIR)
        from mx_data import MXData

        return MXData.parse_result(result)

    def _next_key(self) -> str:
        for _ in range(len(self.pool)):
            key = self.pool[self._cursor % len(self.pool)]
            self._cursor += 1
            if key not in self.exhausted_keys:
                return key
        raise MXQuotaExhausted(
            f"all mx-data keys exhausted: {sorted(self.exhausted_keys)}"
        )

    # -- public API --------------------------------------------------------
    def query(self, tool_query: str) -> dict:
        """Run one query, rotating keys on quota errors.

        Raises MXQuotaExhausted when every key in the pool is spent.
        """
        last_error = "unknown error"
        for attempt in range(self._max_attempts):
            try:
                key = self._next_key()
            except MXQuotaExhausted:
                raise MXQuotaExhausted(
                    f"mx-data pool exhausted for query {tool_query!r}: {last_error}"
                ) from None
            try:
                result = self._client(key).query(tool_query)
            except Exception as exc:  # noqa: BLE001 - network/HTTP failure
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt + 1 < self._max_attempts:
                    self._sleeper(2.0 * (attempt + 1))
                continue

            status = result.get("status") if isinstance(result, dict) else None
            message = str(result.get("message") or "") if isinstance(result, dict) else ""
            if status == 0:
                self.calls_by_key[key] = self.calls_by_key.get(key, 0) + 1
                return result

            last_error = f"状态码 {status} - {message}"
            if status in QUOTA_ERROR_CODES:
                # Per-key quota (113) or shared rate limit (112): burn this key
                # for the rest of the run and try the next one.
                self.exhausted_keys.add(key)
                print(
                    f"[mx-pool] key {key[:12]}... refused ({last_error}); rotating",
                    file=sys.stderr,
                )
                continue
            # Non-quota provider error: surface it to the caller unchanged.
            return result
        raise MXQuotaExhausted(
            f"mx-data pool exhausted for query {tool_query!r}: {last_error}"
        )

    def usage(self) -> dict:
        return {
            "pool_size": len(self.pool),
            "exhausted": sorted(self.exhausted_keys),
            "calls_by_key": dict(self.calls_by_key),
        }


def fetch(pool: Iterable[str] | None = None) -> MXPoolClient:
    return MXPoolClient(list(pool) if pool is not None else None)


if __name__ == "__main__":
    client = fetch()
    print(json.dumps({"pool_size": len(client.pool), "exhausted": client.exhausted_keys}, ensure_ascii=False))

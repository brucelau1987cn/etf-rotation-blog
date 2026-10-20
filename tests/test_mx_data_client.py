"""Contract tests for scripts/mx_data_client.py — the shared mx-data key-pool client.

Background (2026-10-20 incident):
- mx-data enforces a per-KEY daily quota. A single MX_APIKEY hit its limit
  (code=113) and the low-chip attach stages silently produced empty fields
  (theme 0/88, technical 10/88, etf_holdings 0/88) while the pipeline still
  reported success.
- MX_APIKEYS already carried 4 keys but nothing consumed the pool.

These tests pin the three behaviors that matter:
  1. the pool is resolved from MX_APIKEYS (JSON list), deduped, order preserved
  2. a quota refusal on one key rotates to the next key and the call succeeds
  3. when every key is refused, the caller sees an explicit MXQuotaExhausted
     error (not a silent empty result)
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "mx_data_client.py"
SPEC = importlib.util.spec_from_file_location("mx_data_client", SCRIPT)
mod = importlib.util.module_from_spec(SPEC)
sys.modules["mx_data_client"] = mod
SPEC.loader.exec_module(mod)


# ============ resolve_key_pool ============

def test_resolve_key_pool_reads_json_list_from_env(monkeypatch):
    monkeypatch.setenv("MX_APIKEYS", json.dumps(["k1", "k2", "k1"]))
    monkeypatch.setenv("MX_APIKEY", "fallback")
    pool = mod.resolve_key_pool()
    assert pool == ["k1", "k2"]  # deduped, order preserved


def test_resolve_key_pool_accepts_comma_separated(monkeypatch):
    monkeypatch.setenv("MX_APIKEYS", "k1,k2 , k3")
    monkeypatch.setenv("MX_APIKEY", "fallback")
    assert mod.resolve_key_pool() == ["k1", "k2", "k3"]


def test_resolve_key_pool_falls_back_to_single_key(monkeypatch, tmp_path):
    monkeypatch.delenv("MX_APIKEYS", raising=False)
    monkeypatch.setenv("MX_APIKEY", "only-key")
    env_file = tmp_path / ".env"          # no MX_APIKEYS in the file
    env_file.write_text("MX_APIKEY=file-key\n", encoding="utf-8")
    assert mod.resolve_key_pool(env_file) == ["only-key"]


def test_resolve_key_pool_prefers_file_pool_over_single_key(monkeypatch, tmp_path):
    """A file-level MX_APIKEYS pool outranks a single MX_APIKEY env value."""
    monkeypatch.delenv("MX_APIKEYS", raising=False)
    monkeypatch.setenv("MX_APIKEY", "only-key")
    env_file = tmp_path / ".env"
    env_file.write_text('MX_APIKEYS=["file-a","file-b"]\n', encoding="utf-8")
    assert mod.resolve_key_pool(env_file) == ["file-a", "file-b"]


def test_resolve_key_pool_reads_env_file_when_env_missing(monkeypatch, tmp_path):
    monkeypatch.delenv("MX_APIKEYS", raising=False)
    monkeypatch.delenv("MX_APIKEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        'export MX_APIKEYS=["file-a","file-b"]\nMX_APIKEY=ignored\n', encoding="utf-8"
    )
    assert mod.resolve_key_pool(env_file) == ["file-a", "file-b"]


# ============ rotation behaviour ============

class _FakeClient:
    """Fake provider client: each key has a scripted sequence of results."""

    def __init__(self, script: dict[str, list[dict]]):
        self.script = script
        self.calls: list[tuple[str, str]] = []

    def query(self, tool_query: str) -> dict:
        key = self.current_key
        self.calls.append((key, tool_query))
        seq = self.script.get(key, [])
        if not seq:
            return {"status": 0, "data": {}}
        return seq.pop(0)


def _client_with_script(script: dict[str, list[dict]], pool: list[str]) -> mod.MXPoolClient:
    client = mod.MXPoolClient(pool, sleeper=lambda _s: None)
    for key in pool:
        fake = _FakeClient(script)
        fake.current_key = key
        client._clients[key] = fake
    return client


def test_rotates_to_next_key_on_quota_error():
    quota = {"status": 113, "message": "调用次数已达上限"}
    ok = {"status": 0, "data": {}}
    client = _client_with_script({"k1": [quota], "k2": [ok]}, ["k1", "k2"])
    result = client.query("601868 所属概念板块")
    assert result["status"] == 0
    assert "k1" in client.exhausted_keys
    assert client.calls_by_key.get("k2") == 1


def test_rotates_on_rate_limit_code_112():
    limited = {"status": 112, "message": "请求频率过高"}
    ok = {"status": 0, "data": {}}
    client = _client_with_script({"k1": [limited], "k2": [ok]}, ["k1", "k2"])
    assert client.query("x")["status"] == 0
    assert "k1" in client.exhausted_keys


def test_raises_explicit_error_when_every_key_refused():
    quota = {"status": 113, "message": "调用次数已达上限"}
    client = _client_with_script(
        {"k1": [quota], "k2": [quota], "k3": [quota]}, ["k1", "k2", "k3"]
    )
    with pytest.raises(mod.MXQuotaExhausted):
        client.query("601868 所属概念板块")
    assert set(client.exhausted_keys) == {"k1", "k2", "k3"}


def test_non_quota_provider_error_is_returned_unchanged():
    client = _client_with_script({"k1": [{"status": 500, "message": "内部错误"}]}, ["k1"])
    result = client.query("x")
    assert result["status"] == 500  # caller decides what to do with it
    assert client.exhausted_keys == set()


def test_pool_client_requires_at_least_one_key(monkeypatch):
    monkeypatch.delenv("MX_APIKEYS", raising=False)
    monkeypatch.delenv("MX_APIKEY", raising=False)
    with pytest.raises(RuntimeError, match="no mx-data API key"):
        mod.MXPoolClient(pool=[])


def test_usage_reports_pool_state():
    quota = {"status": 113, "message": "调用次数已达上限"}
    ok = {"status": 0, "data": {}}
    client = _client_with_script({"k1": [quota], "k2": [ok]}, ["k1", "k2"])
    client.query("x")
    usage = client.usage()
    assert usage["pool_size"] == 2
    assert usage["exhausted"] == ["k1"]
    assert usage["calls_by_key"]["k2"] == 1


# ============ attach scripts use a quota-free source ============
#
# On 2026-10-20 mx-data was replaced entirely by the Eastmoney F10 direct
# routes (see scripts/em_data_client.py): mx-data meters calls per API key per
# day, and a dormant key made every enrichment stage silently return empty.
# The mx_data_client module stays for reference and for other consumers, but
# the three low-chip attach scripts must no longer depend on it.

@pytest.mark.parametrize(
    "script_name",
    [
        "attach_low_chip_theme_concepts.py",
        "attach_low_chip_technical.py",
        "attach_low_chip_etf_holdings.py",
    ],
)
def test_attach_scripts_no_longer_depend_on_mx_data(script_name):
    """The three attach scripts must not read a single MX_APIKEY any more."""
    path = ROOT / "scripts" / script_name
    source = path.read_text(encoding="utf-8")
    assert 'os.getenv("MX_APIKEY")' not in source
    assert "mx_data_client" not in source, (
        f"{script_name} still imports the mx-data client"
    )


@pytest.mark.parametrize(
    "script_name",
    [
        "attach_low_chip_theme_concepts.py",
        "attach_low_chip_technical.py",
        "attach_low_chip_etf_holdings.py",
    ],
)
def test_attach_scripts_use_eastmoney_client(script_name):
    """Each attach script must go through the shared Eastmoney client."""
    import importlib.util as _util

    path = ROOT / "scripts" / script_name
    spec = _util.spec_from_file_location(path.stem, path)
    module = _util.module_from_spec(spec)
    sys.modules[path.stem] = module
    spec.loader.exec_module(module)

    assert hasattr(module, "EastmoneyClient")
    assert hasattr(module, "EastmoneyError")
    assert hasattr(module, "_get_client")

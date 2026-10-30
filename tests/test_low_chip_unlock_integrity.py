"""Offline unlock gate regression tests; all I/O is isolated or mocked."""
import importlib.util
import json
import socket
import sys
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

ROOT = Path(__file__).resolve().parents[1]
from scripts import query_low_chip_unlocks as unlock


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("real network forbidden")
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(unlock.urllib.request, "urlopen", forbidden)


@pytest.mark.parametrize("start,end", [
    ("2026-01-31", "2026-04-30"), ("2023-11-30", "2024-02-29"),
    ("2026-10-30", "2027-01-30"), ("2026-08-31", "2026-11-30"),
])
def test_three_calendar_months_clamps_month_end(start, end):
    assert unlock.window_end(date.fromisoformat(start)).isoformat() == end


def provider(rows=None, **overrides):
    rows = rows if rows is not None else [{
        "SECURITY_CODE": "600001", "FREE_DATE": "2026-10-30 00:00:00",
        "FREE_SHARES_TYPE": "限售股", "TOTAL_RATIO": 0.000012345678,
        "FREE_RATIO": 0.012345678901,
    }]
    result = {"pages": 1, "count": len(rows), "data": rows}
    result.update(overrides)
    return {"success": True, "version": "snapshot-version", "result": result}


def mock_response(monkeypatch, response):
    requests = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return json.dumps(response).encode()
    def open_mock(request, timeout):
        requests.append((parse_qs(urlparse(request.full_url).query), timeout))
        return Response()
    monkeypatch.setattr(unlock.urllib.request, "urlopen", open_mock)
    return requests


def test_full_universe_uses_one_complete_window_request(monkeypatch):
    calls = mock_response(monkeypatch, provider())
    payload = {"data_as_of": "2026-10-30", "intersection_before_filters": [
        f"{600000 + i:06d}.SH" for i in range(749)
    ]}
    result = unlock.build_cache(payload, "run-a")
    assert len(calls) == 1
    params, timeout = calls[0]
    assert params["filter"] == ["(FREE_DATE>='2026-10-30')(FREE_DATE<='2027-01-30')"]
    assert params["pageSize"] == ["500"]
    assert params["sortColumns"] == ["FREE_DATE,SECURITY_CODE"]
    assert params["sortTypes"] == ["1,1"]
    assert params["pageNumber"] == ["1"]
    assert timeout == 15
    assert result["requested_codes"] == payload["intersection_before_filters"]
    assert result["run_id"] == "run-a"
    assert result["data_as_of"] == "2026-10-30"
    assert result["window_start"] == "2026-10-30"
    assert result["window_end"] == "2027-01-30"
    assert result["datas"][0]["占总股本比例"] == 0.000012345678
    assert result["provider_response"] == provider()


@pytest.mark.parametrize("response", [
    {}, {"success": False, "result": {"count": 0, "pages": 1, "data": []}},
    provider([], count=0), provider(count=2), provider(pages=2), provider(count=501),
    provider(count=True), provider(pages=True),
    provider([{"SECURITY_CODE": "BAD"}]),
    provider([{**provider()["result"]["data"][0], "SECURITY_CODE": 600001}]),
    *[provider([{**provider()["result"]["data"][0], key: value}])
      for key, value in [("TOTAL_RATIO", None), ("TOTAL_RATIO", "NaN"),
                         ("TOTAL_RATIO", -1), ("FREE_RATIO", "inf"),
                         ("FREE_DATE", "2026-10-29"), ("FREE_DATE", "2027-01-31")]],
])
def test_provider_ambiguity_blocks(monkeypatch, response):
    mock_response(monkeypatch, response)
    with pytest.raises(ValueError, match="STAGING BLOCKER"):
        unlock.build_cache({"data_as_of": "2026-10-30", "intersection": ["600001.SH"]}, "run-a")


@pytest.mark.parametrize("mutation", [
    {"run_id": "old-run"}, {"data_as_of": "2026-10-29"},
    {"window_end": "2027-01-31"}, {"requested_codes": []},
    {"status": "failed"}, {"datas": []}, {"provider_response": {}},
    {"datas": [{"股票代码": "600001.SH", "变动日期": "20261030", "股份来源": "限售股",
                "占总股本比例": float("nan"), "占流通股比例": 0.012345678901}]},
])
def test_consumer_rejects_unbound_or_incomplete_cache(monkeypatch, mutation):
    mock_response(monkeypatch, provider())
    payload = {"data_as_of": "2026-10-30", "intersection_before_filters": ["600001.SH"]}
    cache = unlock.build_cache(payload, "run-a")
    cache.update(mutation)
    with pytest.raises(ValueError, match="STAGING BLOCKER"):
        unlock.validate_cache(cache, payload, "run-a")


def test_consumer_retains_all_batches_and_raw_precision(monkeypatch):
    row = provider()["result"]["data"][0]
    mock_response(monkeypatch, provider([row, {**row, "FREE_DATE": "2027-01-30"}]))
    payload = {"data_as_of": "2026-10-30", "intersection": ["600001.SH"]}
    cache = unlock.build_cache(payload, "run-a")
    rows = unlock.validate_cache(cache, payload, "run-a")
    assert len(rows) == 2
    assert rows[1]["变动日期"] == "20270130"
    assert rows[0]["占总股本比例"] == 0.000012345678


def test_enrich_missing_unlock_cache_blocks_before_payload_write(monkeypatch, tmp_path):
    from scripts import enrich_low_chip_stocks as enrich
    data = tmp_path / "payload.json"
    initial = {"data_as_of": "2026-10-30", "intersection_before_filters": ["600001.SH"]}
    data.write_text(json.dumps(initial))
    monkeypatch.setattr(enrich, "DATA", data)
    monkeypatch.setattr(enrich, "PROFILE_FILES", [])
    monkeypatch.setattr(enrich, "INDIVIDUAL", tmp_path / "missing-profile")
    monkeypatch.setenv("LOW_CHIP_RUN_ID", "run-a")
    monkeypatch.setenv("LOW_CHIP_UNLOCK_CACHE", str(tmp_path / "missing-cache"))
    with pytest.raises(ValueError, match="STAGING BLOCKER"):
        enrich.main()
    assert json.loads(data.read_text()) == initial


def test_enrich_retains_audit_and_every_unlock_batch(monkeypatch, tmp_path):
    from scripts import enrich_low_chip_stocks as enrich
    base = provider()["result"]["data"][0]
    mock_response(monkeypatch, provider([base, {**base, "FREE_DATE": "2027-01-30"}]))
    payload = {"data_as_of": "2026-10-30", "intersection_before_filters": ["600001.SH"],
               "filters": {"new_listing_audit": {"status": "complete"}}}
    data, cache = tmp_path / "payload.json", tmp_path / "cache.json"
    data.write_text(json.dumps(payload))
    cache.write_text(json.dumps(unlock.build_cache(payload, "run-a")))
    monkeypatch.setattr(enrich, "DATA", data)
    monkeypatch.setattr(enrich, "PROFILE_FILES", [])
    monkeypatch.setattr(enrich, "INDIVIDUAL", tmp_path / "missing")
    monkeypatch.setattr(enrich, "ENRICHMENT_STATUS", tmp_path / "missing-status")
    monkeypatch.setenv("LOW_CHIP_RUN_ID", "run-a")
    monkeypatch.setenv("LOW_CHIP_UNLOCK_CACHE", str(cache))
    enrich.main()
    result = json.loads(data.read_text())
    assert result["filters"]["new_listing_audit"] == {"status": "complete"}
    assert result["intersection"] == []
    assert len(result["enrichments"]["600001.SH"]["unlock"]["batches"]) == 2
    assert result["enrichments"]["600001.SH"]["unlock"]["ratio"] == 0.000012345678
    assert result["enrichments"]["600001.SH"]["quality_shareholder"] is None


def test_file_import_is_anchored_with_fake_public_module(monkeypatch):
    import types
    fake = types.ModuleType("query_low_chip_unlocks")
    monkeypatch.setitem(sys.modules, "query_low_chip_unlocks", fake)
    before = list(sys.path)
    spec = importlib.util.spec_from_file_location("enrich_file_test", ROOT / "scripts/enrich_low_chip_stocks.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.validate_cache.__globals__["__file__"] == str(ROOT / "scripts/query_low_chip_unlocks.py")
    assert sys.modules["query_low_chip_unlocks"] is fake
    assert sys.path == before


def test_timeout_propagates_without_producing_cache(monkeypatch):
    def timeout(*args, **kwargs):
        raise TimeoutError("provider timeout")
    monkeypatch.setattr(unlock.urllib.request, "urlopen", timeout)
    with pytest.raises(TimeoutError):
        unlock.build_cache({"data_as_of": "2026-10-30", "intersection": ["600001.SH"]}, "run-a")


@pytest.mark.parametrize("code", [" 600001.SH ", "600001.SH\n", "\t600001", " 830001.BJ "])
@pytest.mark.parametrize("side", ["producer", "consumer"])
def test_raw_code_whitespace_blocks(monkeypatch, code, side):
    mock_response(monkeypatch, provider())
    payload = {"data_as_of": "2026-10-30", "intersection_before_filters": ["600001.SH"]}
    cache = unlock.build_cache(payload, "run-a")
    payload["intersection_before_filters"] = [code]
    with pytest.raises(ValueError, match="STAGING BLOCKER"):
        if side == "producer":
            unlock.build_cache(payload, "run-a")
        else:
            unlock.validate_cache(cache, payload, "run-a")


@pytest.mark.parametrize("raw_date", [
    "2026-10-30 25:99:99", "2026-10-30T24:00:00", "2026-10-30junk",
    "2026-10-30 00:00:00junk", "2026-10-30T12:00:00+25:00",
    "2026-10-30T12:00:00+08:99",
])
@pytest.mark.parametrize("side", ["producer", "consumer"])
def test_entire_provider_date_is_validated(monkeypatch, raw_date, side):
    response = provider()
    mock_response(monkeypatch, response)
    payload = {"data_as_of": "2026-10-30", "intersection": ["600001.SH"]}
    cache = unlock.build_cache(payload, "run-a")
    response["result"]["data"][0]["FREE_DATE"] = raw_date
    cache["provider_response"] = response
    with pytest.raises(ValueError, match="STAGING BLOCKER"):
        if side == "producer":
            unlock.build_cache(payload, "run-a")
        else:
            unlock.validate_cache(cache, payload, "run-a")


@pytest.mark.parametrize("code", ["600001", "600001.SH", "000001.SZ", "830001.BJ"])
def test_code_representation_is_preserved(code):
    assert unlock.unlock_query_codes({"intersection": [code]}) == ([] if code.endswith(".BJ") else [code])


@pytest.mark.parametrize("code", [600001, None, "600001.sh", "SH600001", "600001.US", "６００００１.SH"])
def test_only_existing_raw_code_formats_are_accepted(code):
    with pytest.raises(ValueError, match="STAGING BLOCKER"):
        unlock.unlock_query_codes({"intersection": [code]})


@pytest.mark.parametrize("raw_date", [
    "2026-10-30", "2026-10-30 00:00:00", "2026-10-30T12:34:56",
    "2026-10-30T12:34:56.123+08:00", "2026-10-30T00:00:00Z",
])
def test_supported_full_provider_dates(monkeypatch, raw_date):
    response = provider()
    response["result"]["data"][0]["FREE_DATE"] = raw_date
    mock_response(monkeypatch, response)
    payload = {"data_as_of": "2026-10-30", "intersection": ["600001.SH"]}
    cache = unlock.build_cache(payload, "run-a")
    assert unlock.validate_cache(cache, payload, "run-a")[0]["变动日期"] == "20261030"

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ftshare_shadow.py"


def load_module():
    spec = importlib.util.spec_from_file_location("ftshare_shadow_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeSDK:
    def __init__(self, failures=None):
        self.failures = failures or set()

    def _result(self, method, rows):
        if method in self.failures:
            raise RuntimeError(f"{method} failed")
        return rows

    def stock_holders_number(self, **kwargs):
        symbol = kwargs["stock_code"]
        assert kwargs["all_pages"] is True
        assert kwargs["page_size"] == 200
        assert kwargs["max_pages"] == 100
        assert kwargs["raw"] is True
        return self._result("stock_holders_number", [{
            "code": 200,
            "data": {"pageNum": 1, "pageSize": 200, "total": 1, "pages": 1, "items": [{
                "stock_code": symbol,
                "publish_date": "2026-04-17",
                "report_date": "2025-12-31",
                "holder_num": 100,
                "holder_num_change_ratio": "-3.5",
                "ften_holder_ratio": "61.2",
            }]},
        }])

    def stock_float_holders(self, **kwargs):
        symbol = kwargs["stock_code"]
        assert kwargs["all_pages"] is True
        assert kwargs["page_size"] == 200
        assert kwargs["max_pages"] == 100
        assert kwargs["raw"] is True
        return self._result("stock_float_holders", [{
            "code": 200,
            "data": {"pageNum": 1, "pageSize": 200, "total": 1, "pages": 1, "items": [{
                "stock_code": symbol,
                "publish_date": "2026-03-31",
                "share_holding": "60.8",
                "fen_holders": [{"rank": 1, "shareholder_name": "中央汇金"}],
            }]},
        }])

    def limit_up_pool(self, **kwargs):
        return self._result("limit_up_pool", {"code": 200, "message": "success", "data": [{"symbol": "000001.XSHE"}] * 64})

    def limit_up_break_pool(self, **kwargs):
        return self._result("limit_up_break_pool", {"code": 200, "message": "success", "data": [{}] * 16})

    def limit_down_pool(self, **kwargs):
        return self._result("limit_down_pool", {"code": 200, "message": "success", "data": [{}] * 27})

    def auction_results(self, **kwargs):
        assert kwargs["all_pages"] is True
        assert kwargs["page_size"] == 200
        assert kwargs["max_pages"] == 100
        assert kwargs["raw"] is True
        pages = []
        for page in range(1, 28):
            size = 200 if page < 27 else 7
            pages.append({
                "code": 200,
                "message": "success",
                "data": {"pageNum": page, "pageSize": 200, "total": 5207, "pages": 27, "records": [{}] * size},
            })
        return self._result("auction_results", pages)


def test_create_retry_session_covers_429_and_server_errors(monkeypatch):
    mod = load_module()

    class FakeRetry:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeAdapter:
        def __init__(self, max_retries):
            self.max_retries = max_retries

    class FakeSession:
        def __init__(self):
            self.mounts = {}

        def mount(self, prefix, adapter):
            self.mounts[prefix] = adapter

    monkeypatch.setattr(mod, "Retry", FakeRetry)
    monkeypatch.setattr(mod, "HTTPAdapter", FakeAdapter)
    monkeypatch.setattr(mod.requests, "Session", FakeSession)
    session = mod.create_retry_session()
    retry = session.mounts["https://"].max_retries
    assert retry.kwargs["status_forcelist"] == [429, 500, 502, 503, 504]
    assert retry.kwargs["backoff_factor"] == 1.0
    assert retry.kwargs["allowed_methods"] == frozenset({"GET", "POST"})


def test_collect_low_chip_uses_sdk_rows_and_marks_complete():
    mod = load_module()
    result = mod.collect_low_chip(FakeSDK(), ["000001.SZ"], sleep_seconds=0)
    assert result["holder_success"] == 1
    assert result["float_holder_success"] == 1
    assert result["items"]["000001.SZ"]["holder_history_count"] == 1
    assert result["items"]["000001.SZ"]["quality"]["holder"]["complete"] is True
    assert result["items"]["000001.SZ"]["quality"]["float_holder"]["complete"] is True


def test_collect_low_chip_sdk_failure_is_visible():
    mod = load_module()
    result = mod.collect_low_chip(FakeSDK({"stock_float_holders"}), ["000001.SZ"], sleep_seconds=0)
    assert result["holder_success"] == 1
    assert result["float_holder_success"] == 0
    assert result["errors"]["000001.SZ"]["float_holders"]["message"] == "stock_float_holders failed"


def test_collect_low_chip_marks_incomplete_holder_pagination():
    mod = load_module()

    class CappedSDK(FakeSDK):
        def stock_holders_number(self, **kwargs):
            return [{
                "code": 200,
                "data": {"pageNum": 1, "pageSize": 200, "total": 2, "pages": 2, "items": [{
                    "stock_code": kwargs["stock_code"],
                    "publish_date": "2026-04-17",
                    "report_date": "2025-12-31",
                    "holder_num": 100,
                }]},
            }]

    result = mod.collect_low_chip(CappedSDK(), ["000001.SZ"], sleep_seconds=0)
    assert result["items"]["000001.SZ"]["quality"]["holder"]["complete"] is False
    assert mod.count_low_chip_incomplete(result) == 1


def test_collect_market_uses_full_sdk_results():
    mod = load_module()
    result = mod.collect_market(FakeSDK(), "20260824", auction_page_size=200)
    assert result["summary"] == {
        "limit_up_returned": 64,
        "limit_up_break_returned": 16,
        "limit_down_returned": 27,
        "auction_returned": 5207,
    }
    assert all(result[key]["quality"]["complete"] for key in ("limit_up", "limit_up_break", "limit_down", "auction"))


def test_unwrap_sdk_rows_accepts_raw_envelope_and_row_list():
    mod = load_module()
    assert mod.unwrap_sdk_rows({"code": 200, "message": "success", "data": [{"id": 1}]}) == [{"id": 1}]
    assert mod.unwrap_sdk_rows([{"id": 2}]) == [{"id": 2}]
    with pytest.raises(RuntimeError, match="invalid row payload"):
        mod.unwrap_sdk_rows({"code": 200, "data": {"x": 1}})


def test_unwrap_paginated_sdk_pages_preserves_completeness_metadata():
    mod = load_module()
    pages = [
        {"code": 200, "data": {"pageNum": 1, "pageSize": 2, "total": 3, "pages": 2, "records": [{"id": 1}, {"id": 2}]}},
        {"code": 200, "data": {"pageNum": 2, "pageSize": 2, "total": 3, "pages": 2, "records": [{"id": 3}]}},
    ]
    rows, quality = mod.unwrap_paginated_sdk_pages(pages)
    assert [row["id"] for row in rows] == [1, 2, 3]
    assert quality["total"] == 3
    assert quality["pages"] == 2
    assert quality["fetched_pages"] == 2
    assert quality["complete"] is True


def test_unwrap_paginated_sdk_pages_flags_page_cap():
    mod = load_module()
    pages = [
        {"code": 200, "data": {"pageNum": 1, "pageSize": 2, "total": 5, "pages": 3, "records": [{"id": 1}, {"id": 2}]}},
        {"code": 200, "data": {"pageNum": 2, "pageSize": 2, "total": 5, "pages": 3, "records": [{"id": 3}, {"id": 4}]}},
    ]
    rows, quality = mod.unwrap_paginated_sdk_pages(pages)
    assert len(rows) == 4
    assert quality["page_cap_reached"] is True
    assert quality["complete"] is False


def test_compare_low_chip_reports_provider_deltas():
    mod = load_module()
    source = {"enrichments": {"000001.SZ": {"shareholder_metrics": {
        "shareholder_count": 110,
        "shareholder_change_pct": -2.0,
        "top10_float_ratio": 60.0,
        "report_period": "2025-12-31",
    }}}}
    collected = {"items": {"000001.SZ": {
        "holder_latest": {
            "holder_num": 100,
            "holder_num_change_ratio": "-3.5",
            "report_date": "2025-12-31",
        },
        "float_holder_latest": {"share_holding": "60.8"},
    }}}
    row = mod.compare_low_chip(source, collected)["items"]["000001.SZ"]
    assert row["holder_count_delta"] == -10
    assert row["holder_change_pct_delta"] == -1.5
    assert row["top10_float_ratio_delta"] == 0.8
    assert row["report_period_match"] is True


def test_atomic_write_json_preserves_payload(tmp_path):
    mod = load_module()
    output = tmp_path / "shadow.json"
    payload = {"status": "ok", "production_change_allowed": False}
    mod.atomic_write_json(output, payload)
    assert json.loads(output.read_text(encoding="utf-8")) == payload


def test_main_persists_degraded_snapshot_when_sdk_initialize_fails(tmp_path, monkeypatch):
    mod = load_module()
    source = tmp_path / "low-chip.json"
    output = tmp_path / "shadow.json"
    source.write_text(json.dumps({"data_as_of": "2026-08-24", "intersection": ["000001.SZ"], "enrichments": {}}), encoding="utf-8")

    def broken_client(*args, **kwargs):
        raise RuntimeError("sdk unavailable")

    monkeypatch.setattr(mod, "create_sdk_client", broken_client)
    monkeypatch.setattr("sys.argv", ["ftshare_shadow.py", "--input", str(source), "--output", str(output)])
    assert mod.main() == 2
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["status"] == "degraded"
    assert payload["errors"]["sdk_initialize"]["message"] == "sdk unavailable"
    assert payload["source"]["transport"] == "python-sdk"


def test_cli_and_output_are_sdk_shadow_only():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "public/data/ftshare-shadow.json" in text
    assert '"mode": "shadow_research_only"' in text
    assert '"production_change_allowed": False' in text
    assert '"transport": "python-sdk"' in text
    assert "FTShareMCPClient" not in text
    assert "parse_sse_json" not in text
    assert "--max-symbols" in text
    assert "--output" in text

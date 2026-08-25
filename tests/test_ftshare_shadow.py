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


def test_parse_sse_json_uses_last_json_data_event():
    mod = load_module()
    text = 'data: \n\nid: 0\n\ndata: {"jsonrpc":"2.0","id":2,"result":{"ok":true}}\n\n'
    assert mod.parse_sse_json(text)["result"]["ok"] is True


def test_parse_sse_json_preserves_jsonrpc_error():
    mod = load_module()
    text = 'event: message\ndata: {"jsonrpc":"2.0","id":2,"error":{"code":-32602,"message":"bad params"}}\n\n'
    assert mod.parse_sse_json(text)["error"]["code"] == -32602


def test_parse_sse_json_rejects_error_before_later_result():
    mod = load_module()
    text = (
        'data: {"jsonrpc":"2.0","id":2,"error":{"code":-32602,"message":"bad params"}}\n\n'
        'data: {"jsonrpc":"2.0","id":2,"result":{"ok":true}}\n\n'
    )
    with pytest.raises(RuntimeError, match="JSON-RPC error"):
        mod.parse_sse_json(text)


def test_mcp_call_raises_structured_tool_error():
    mod = load_module()
    client = object.__new__(mod.FTShareMCPClient)
    client._rpc = lambda _method, _params=None: {
        "result": {
            "isError": True,
            "content": [{"type": "text", "text": json.dumps({
                "error": {"code": "UPSTREAM_UNAVAILABLE", "message": "busy", "retryable": True}
            })}],
        }
    }
    with pytest.raises(mod.FTShareToolError) as exc:
        client.call_tool("ft_stock_holders_number", {"stock_code": "600519.SH"})
    assert exc.value.code == "UPSTREAM_UNAVAILABLE"
    assert exc.value.retryable is True


def test_collect_low_chip_keeps_partial_failures_visible():
    mod = load_module()

    class FakeClient:
        def call_tool(self, name, args):
            symbol = args.get("stock_code")
            if name == "ft_stock_holders_number":
                return {"metadata": {"truncated": False, "warnings": []}, "data": [{
                    "stock_code": symbol,
                    "publish_date": "2026-04-17",
                    "report_date": "2025-12-31",
                    "holder_num": 100,
                    "holder_num_change_ratio": "-3.5",
                    "chip_concentration": "较集中",
                    "ften_holder_ratio": "61.2",
                }]}
            if name == "ft_stock_float_holders" and symbol == "000001.SZ":
                return {"metadata": {"truncated": True, "warnings": ["截断"]}, "data": [{
                    "stock_code": symbol,
                    "publish_date": "2026-03-31",
                    "share_holding": "60.1",
                    "fen_holders": [{"rank": 1, "shareholder_name": "中央汇金", "shareholder_type": "资产管理公司"}],
                }]}
            raise mod.FTShareToolError("UPSTREAM_UNAVAILABLE", "busy", True)

    result = mod.collect_low_chip(FakeClient(), ["000001.SZ", "000002.SZ"], sleep_seconds=0)
    assert result["requested"] == 2
    assert result["holder_success"] == 2
    assert result["float_holder_success"] == 1
    assert result["items"]["000001.SZ"]["holder_latest"]["holder_num"] == 100
    assert result["items"]["000001.SZ"]["float_holder_latest"]["fen_holders"][0]["shareholder_name"] == "中央汇金"
    assert result["items"]["000001.SZ"]["quality"]["float_holder_truncated"] is True
    assert result["errors"]["000002.SZ"]["float_holders"]["code"] == "UPSTREAM_UNAVAILABLE"


def test_compare_low_chip_reports_provider_deltas():
    mod = load_module()
    source = {
        "enrichments": {
            "000001.SZ": {"shareholder_metrics": {
                "shareholder_count": 110,
                "shareholder_change_pct": -2.0,
                "top10_float_ratio": 60.0,
                "report_period": "2025-12-31",
            }}
        }
    }
    collected = {
        "items": {"000001.SZ": {
            "holder_latest": {
                "holder_num": 100,
                "holder_num_change_ratio": "-3.5",
                "ften_holder_ratio": "61.2",
                "report_date": "2025-12-31",
            },
            "float_holder_latest": {"share_holding": "60.8", "publish_date": "2026-03-31"},
        }}
    }
    comparison = mod.compare_low_chip(source, collected)
    row = comparison["items"]["000001.SZ"]
    assert row["holder_count_delta"] == -10
    assert row["holder_change_pct_delta"] == -1.5
    assert row["top10_float_ratio_delta"] == 0.8
    assert row["report_period_match"] is True
    assert comparison["compared"] == 1


def test_low_chip_incomplete_quality_is_counted():
    mod = load_module()
    collected = {
        "items": {
            "000001.SZ": {"quality": {
                "holder": {"complete": True},
                "float_holder": {"complete": False},
            }},
            "000002.SZ": {"quality": {
                "holder": {"complete": False},
                "float_holder": {"complete": False},
            }},
        }
    }
    assert mod.count_low_chip_incomplete(collected) == 3


def test_metadata_quality_flags_any_total_returned_mismatch():
    mod = load_module()
    quality = mod.metadata_quality({"metadata": {"total": 1, "returned": 2, "truncated": False}, "data": [{}, {}]})
    assert quality["count_mismatch"] is True
    assert quality["complete"] is False


def test_collect_market_shadow_tracks_total_returned_mismatch():
    mod = load_module()

    class FakeClient:
        def call_tool(self, name, args):
            if name == "ft_limit_up_pool":
                return {"metadata": {"total": 64, "returned": 50, "truncated": False, "warnings": []}, "data": [{"symbol": "000001.XSHE"}] * 50}
            if name == "ft_limit_up_break_pool":
                return {"metadata": {"total": 5, "returned": 5, "truncated": False, "warnings": []}, "data": [{}] * 5}
            if name == "ft_limit_down_pool":
                return {"metadata": {"total": 2, "returned": 2, "truncated": False, "warnings": []}, "data": [{}] * 2}
            if name == "ft_auction_results":
                return {"metadata": {"total": 5200, "returned": 3, "truncated": False, "warnings": []}, "data": [{"symbol": "000001.XSHE"}] * 3}
            raise AssertionError(name)

    result = mod.collect_market(FakeClient(), "20260824", auction_page_size=3)
    assert result["limit_up"]["quality"]["count_mismatch"] is True
    assert result["limit_up"]["quality"]["complete"] is False
    assert result["auction"]["quality"]["complete"] is False
    assert result["summary"]["limit_up_returned"] == 50


def test_main_persists_degraded_snapshot_when_mcp_initialize_fails(tmp_path, monkeypatch):
    mod = load_module()
    source = tmp_path / "low-chip.json"
    output = tmp_path / "shadow.json"
    source.write_text(json.dumps({"data_as_of": "2026-08-24", "intersection": ["000001.SZ"], "enrichments": {}}), encoding="utf-8")

    class BrokenClient:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("network down")

    monkeypatch.setattr(mod, "FTShareMCPClient", BrokenClient)
    monkeypatch.setattr("sys.argv", ["ftshare_shadow.py", "--input", str(source), "--output", str(output)])
    assert mod.main() == 2
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["status"] == "degraded"
    assert payload["errors"]["mcp_initialize"]["message"] == "network down"
    assert payload["production_change_allowed"] is False


def test_atomic_write_and_shadow_contract(tmp_path):
    mod = load_module()
    out = tmp_path / "shadow.json"
    payload = {
        "schema_version": "ftshare-shadow-v1",
        "mode": "shadow_research_only",
        "production_change_allowed": False,
        "generated_at": "2026-08-25T17:00:00+08:00",
        "status": "degraded",
        "low_chip": {"requested": 2},
        "market": {"trade_date": "20260824"},
    }
    mod.atomic_write_json(out, payload)
    assert json.loads(out.read_text(encoding="utf-8")) == payload


def test_cli_and_output_are_shadow_only():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "public/data/ftshare-shadow.json" in text
    assert '"mode": "shadow_research_only"' in text
    assert '"production_change_allowed": False' in text
    assert "--max-symbols" in text
    assert "--output" in text

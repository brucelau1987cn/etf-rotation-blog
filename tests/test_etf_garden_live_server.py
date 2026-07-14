import importlib.util
from pathlib import Path


PATH = Path(__file__).resolve().parents[1] / "scripts/etf_garden_live_server.py"
spec = importlib.util.spec_from_file_location("etf_garden_live_server", PATH)
assert spec and spec.loader
live = importlib.util.module_from_spec(spec)
spec.loader.exec_module(live)


def test_parse_codes_supports_csv_prefixes_and_deduplication():
    codes, invalid = live.parse_codes({"codes": ["SH515880,588000,SZ159227,515880"]})
    assert codes == ["515880", "588000", "159227"]
    assert invalid == []


def test_parse_codes_rejects_outside_formal_pool():
    codes, invalid = live.parse_codes({"codes": ["515880,999999"]})
    assert codes == ["515880", "999999"]
    assert invalid == ["999999"]


def test_filter_data_returns_only_requested_items():
    payload = {"ok": True, "count": 3, "items": [
        {"code": "515880"}, {"code": "588000"}, {"code": "159227"},
    ]}
    result = live.filter_data(payload, ["588000", "515880"])
    assert result["count"] == 2
    assert result["requested_count"] == 2
    assert [item["code"] for item in result["items"]] == ["515880", "588000"]
    assert payload["count"] == 3


def test_no_codes_preserves_full_payload():
    payload = {"ok": True, "count": 1, "items": [{"code": "515880"}]}
    assert live.filter_data(payload) is payload

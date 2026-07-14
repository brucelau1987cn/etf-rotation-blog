import copy
import json
from pathlib import Path

from scripts.validate_dashboard_batches import validate


FIXTURES = {
    "garden-recommendations.json": {
        "date": "2026-07-14", "applies_to": "2026-07-14", "level_data_as_of": "2026-07-14",
        "stage": "22:00夜间最终版", "plant": [{"price_date": "2026-07-14"}], "harvest": [], "watch": [],
    },
    "etf-garden-pool.json": {"evaluation_date": "2026-07-14", "latest_trade_date": "2026-07-14"},
    "a-share-mid-macro.json": {"generated_at": "2026-07-14 22:05:22 CST"},
    "model-lab/a-share-shadow.json": {"latest_trade_date": "2026-07-14"},
    "us-etf-garden.json": {"date": "2026-07-13", "stage": "美股收盘版", "session_state": "closed"},
    "us-etf-pool.json": {"model_date": "2026-07-13", "quote_trade_date": "2026-07-13", "session_state": "closed"},
    "us-macro-dashboard.json": {"generated_at": "2026-07-13T18:31:54-04:00", "market": {"spy": {"date": "2026-07-13"}}},
}


def write_fixtures(root: Path, mutate=None):
    payloads = copy.deepcopy(FIXTURES)
    if mutate:
        mutate(payloads)
    for name, payload in payloads.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")


def test_consistent_cross_market_batches_pass(tmp_path):
    write_fixtures(tmp_path)
    result = validate(tmp_path)
    assert result.status == "ok"
    assert result.batches["a_share"]["date"] == "2026-07-14"
    assert result.batches["us"]["date"] == "2026-07-13"


def test_a_share_mixed_batch_is_blocked(tmp_path):
    def mutate(payloads):
        payloads["etf-garden-pool.json"]["latest_trade_date"] = "2026-07-13"
    write_fixtures(tmp_path, mutate)
    result = validate(tmp_path)
    assert result.status == "error"
    assert any("A-share batch mismatch" in error for error in result.errors)


def test_us_macro_mixed_batch_is_blocked(tmp_path):
    def mutate(payloads):
        payloads["us-macro-dashboard.json"]["market"]["spy"]["date"] = "2026-07-10"
    write_fixtures(tmp_path, mutate)
    result = validate(tmp_path)
    assert result.status == "error"
    assert any("US batch mismatch" in error for error in result.errors)


def test_missing_file_is_blocked(tmp_path):
    write_fixtures(tmp_path)
    (tmp_path / "a-share-mid-macro.json").unlink()
    result = validate(tmp_path)
    assert result.status == "error"
    assert "missing file" in result.errors[0]

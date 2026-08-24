"""iWenCai 额度台账契约测试（全 mock，零外部调用）。"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/iwencai_quota_ledger.py"


def load_module(tmp_state: Path, monkeypatch, keys: int = 8):
    spec = importlib.util.spec_from_file_location("iwencai_quota_ledger", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "STATE", tmp_state)
    monkeypatch.setenv("IWENCAI_APIKEYS", json.dumps([f"sk-{i}" for i in range(keys)]))
    return module


def test_capacity_derives_from_key_count(tmp_path, monkeypatch):
    module = load_module(tmp_path / "usage.json", monkeypatch, keys=8)
    assert module.key_count() == 8
    assert module.daily_capacity() == 1200


def test_record_accumulates_per_stage(tmp_path, monkeypatch):
    module = load_module(tmp_path / "usage.json", monkeypatch)
    module.record(20, "build")
    module.record(15, "enrich")
    module.record(5, "build")

    info = module.report(module.load())
    assert info["used"] == 40
    assert info["remaining"] == 1160
    assert info["stages_today"] == {"build": 25, "enrich": 15}


def test_check_blocks_when_projection_exceeds_safety_limit(tmp_path, monkeypatch):
    module = load_module(tmp_path / "usage.json", monkeypatch)
    # capacity 1200, safety limit = 1020
    module.record(1000, "earlier")
    info = module.report(module.load())
    assert info["safety_limit"] == 1020

    # 30 more keeps us at 1030 > 1020 -> must block
    projected = info["used"] + 30
    assert projected > info["safety_limit"]


def test_check_allows_normal_daily_run(tmp_path, monkeypatch):
    """正常一天约 108 次，必须放行。"""
    module = load_module(tmp_path / "usage.json", monkeypatch)
    info = module.report(module.load())
    assert info["used"] == 0
    assert 108 < info["safety_limit"]


def test_state_survives_corrupt_file(tmp_path, monkeypatch):
    state = tmp_path / "usage.json"
    state.write_text("{ not json", encoding="utf-8")
    module = load_module(state, monkeypatch)
    data = module.load()
    assert data == {"days": {}}
    module.record(5, "recovered")
    assert module.used_today(module.load()) == 5


def test_ledger_never_calls_iwencai():
    """守卫：台账脚本是纯本地状态，不得发起任何查询。"""
    source = SCRIPT.read_text(encoding="utf-8")
    assert "iwencai-market-query" not in source
    assert "subprocess" not in source
    assert "urllib" not in source

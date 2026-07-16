import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import scripts.prepare_a_share_nightly as prepare
import scripts.publish_a_share_nightly as publish

CN = ZoneInfo("Asia/Shanghai")


def kronos_fixture(trade_date="2026-07-14", count=89):
    future = ["2026-07-15", "2026-07-16", "2026-07-17", "2026-07-20", "2026-07-21"]
    return {
        "mode": "shadow_research_only", "production_weights_changed": False,
        "formal_signal_logic_changed": False, "production_role": "display_and_audit_only",
        "latest_trade_date": trade_date,
        "data_basis": {"adjustment": "qfq", "is_final": True, "universe": "formal_rotation"},
        "forecast_definition": {"horizon_sessions": 5, "future_sessions": future},
        "coverage": {"expected_symbols": count, "predicted_symbols": count},
        "items": [{
            "symbol": f"{510000 + index}", "as_of": trade_date,
            "steps": [{"open": 4.0, "high": 4.1, "low": 3.9, "close": 4.02} for _ in future],
        } for index in range(count)],
    }


def test_prepare_writes_manifest_for_valid_gate(tmp_path, monkeypatch):
    monkeypatch.setattr(prepare, "ROOT", tmp_path)
    (tmp_path / "public/data/model-lab").mkdir(parents=True)
    (tmp_path / "public/data/etf-garden-pool.json").write_text(json.dumps({
        "latest_trade_date": "2026-07-14", "summary": {"valid_count": 91}
    }))
    (tmp_path / "public/data/model-lab/a-share-shadow.json").write_text(json.dumps({
        "mode": "shadow_research_only", "production_weights_changed": False, "rotation_universe_count": 89,
        "signal_enhancement": {"formal_signal_logic_changed": False, "production_role": "shadow_filter_and_audit_only", "coverage": {"symbols_at_least_260": 89}}
    }))
    (tmp_path / "public/data/model-lab/a-share-kronos-shadow.json").write_text(json.dumps(kronos_fixture()))
    monkeypatch.setattr(prepare, "run_json", lambda command: {
        "decision": "run", "qfq_date": "2026-07-14", "qfq_coverage": 91
    })
    state = tmp_path / "state.json"
    result = prepare.prepare(now=datetime(2026, 7, 14, 21, 50, tzinfo=CN), state_path=state)
    assert result["status"] == "prepared"
    assert result["trade_date"] == "2026-07-14"
    assert json.loads(state.read_text())["expected_stage"] == "22:00夜间最终版"
    assert "public/data/model-lab/a-share-kronos-shadow.json" in result["snapshot_files"]


def test_prepare_blocks_invalid_shadow_and_overwrites_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(prepare, "ROOT", tmp_path)
    (tmp_path / "public/data/model-lab").mkdir(parents=True)
    (tmp_path / "public/data/etf-garden-pool.json").write_text(json.dumps({
        "latest_trade_date": "2026-07-14", "summary": {"valid_count": 91}
    }))
    (tmp_path / "public/data/model-lab/a-share-shadow.json").write_text(json.dumps({
        "mode": "production", "production_weights_changed": True, "rotation_universe_count": 10
    }))
    (tmp_path / "public/data/model-lab/a-share-kronos-shadow.json").write_text(json.dumps(kronos_fixture()))
    monkeypatch.setattr(prepare, "run_json", lambda command: {"decision": "run", "qfq_date": "2026-07-14"})
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"status": "prepared", "trade_date": "2026-07-13"}))
    result = prepare.prepare(now=datetime(2026, 7, 14, 21, 50, tzinfo=CN), state_path=state)
    assert result["status"] == "blocked"
    assert len(result["errors"]) == 4
    assert json.loads(state.read_text())["status"] == "blocked"


def test_prepare_blocks_invalid_kronos_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(prepare, "ROOT", tmp_path)
    (tmp_path / "public/data/model-lab").mkdir(parents=True)
    (tmp_path / "public/data/etf-garden-pool.json").write_text(json.dumps({
        "latest_trade_date": "2026-07-14", "summary": {"valid_count": 91}
    }))
    (tmp_path / "public/data/model-lab/a-share-shadow.json").write_text(json.dumps({
        "mode": "shadow_research_only", "production_weights_changed": False, "rotation_universe_count": 89,
        "signal_enhancement": {"formal_signal_logic_changed": False, "production_role": "shadow_filter_and_audit_only", "coverage": {"symbols_at_least_260": 89}}
    }))
    invalid = kronos_fixture(count=88)
    (tmp_path / "public/data/model-lab/a-share-kronos-shadow.json").write_text(json.dumps(invalid))
    monkeypatch.setattr(prepare, "run_json", lambda command: {"decision": "run", "qfq_date": "2026-07-14"})
    result = prepare.prepare(now=datetime(2026, 7, 14, 21, 50, tzinfo=CN), state_path=tmp_path / "state.json")
    assert result["status"] == "blocked"
    assert any("89/89" in error for error in result["errors"])


def prepared_state():
    return {
        "status": "prepared", "trade_date": "2026-07-14", "prepared_at": "2026-07-14T21:50:00+08:00",
        "expected_stage": "22:00夜间最终版",
        "content_files": ["src/content/blog/2026-07-14.md", "public/data/garden-recommendations.json", "public/data/a-share-mid-macro.json"],
        "snapshot_files": [],
    }


def test_publish_rejects_wrong_stage(tmp_path, monkeypatch):
    monkeypatch.setattr(publish, "ROOT", tmp_path)
    (tmp_path / "src/content/blog").mkdir(parents=True)
    (tmp_path / "public/data").mkdir(parents=True)
    state = tmp_path / "state.json"
    state.write_text(json.dumps(prepared_state()))
    (tmp_path / "src/content/blog/2026-07-14.md").write_text("---\nstage: 14:30尾盘操作版\n---\n")
    (tmp_path / "public/data/garden-recommendations.json").write_text(json.dumps({"date": "2026-07-14", "stage": "14:30尾盘操作版"}))
    (tmp_path / "public/data/a-share-mid-macro.json").write_text("{}")
    try:
        publish.publish(state, dry_run=True, now=datetime(2026, 7, 14, 22, 30, tzinfo=CN))
    except RuntimeError as exc:
        assert "stage/date mismatch" in str(exc)
    else:
        raise AssertionError("expected stage mismatch")


def test_publish_idempotent_when_no_owned_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(publish, "ROOT", tmp_path)
    (tmp_path / "src/content/blog").mkdir(parents=True)
    (tmp_path / "public/data").mkdir(parents=True)
    state = tmp_path / "state.json"
    state.write_text(json.dumps(prepared_state()))
    (tmp_path / "src/content/blog/2026-07-14.md").write_text("---\nstage: '22:00夜间最终版'\n---\n### 22:00 夜间最终整理\n")
    (tmp_path / "public/data/garden-recommendations.json").write_text(json.dumps({"date": "2026-07-14", "stage": "22:00夜间最终版"}))
    (tmp_path / "public/data/a-share-mid-macro.json").write_text("{}")
    monkeypatch.setattr(publish, "git_changes", lambda: {"unrelated.txt"})
    result = publish.publish(state, dry_run=True, now=datetime(2026, 7, 14, 22, 30, tzinfo=CN))
    assert result["status"] == "idempotent"
    assert result["changed"] == []


def test_publish_rejects_stale_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(publish, "ROOT", tmp_path)
    state = tmp_path / "state.json"
    state.write_text(json.dumps({
        "status": "prepared", "trade_date": "2026-07-13", "prepared_at": "2026-07-13T21:50:00+08:00",
        "expected_stage": "22:00夜间最终版", "content_files": [], "snapshot_files": [],
    }))
    try:
        publish.publish(state, dry_run=True, now=datetime(2026, 7, 14, 22, 30, tzinfo=CN))
    except RuntimeError as exc:
        assert "stale nightly manifest" in str(exc)
    else:
        raise AssertionError("expected stale manifest rejection")

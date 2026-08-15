from __future__ import annotations

import importlib.util
import json
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts/update_us_etf_garden.py"
spec = importlib.util.spec_from_file_location("update_us_etf_garden", MODULE_PATH)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def decide(**overrides):
    values = {
        "old": "2026-07-29",
        "latest": "2026-07-29",
        "state": "closed",
        "recovery_dirty": False,
        "garden_date": "2026-07-29",
        "garden_stage": "美股收盘版",
        "garden_session": "closed",
    }
    values.update(overrides)
    return module.decide_action(**values)


def test_completed_dirty_close_recovers_before_new_intraday_bar():
    assert decide(
        latest="2026-07-30",
        state="open",
        recovery_dirty=True,
    ) == "recover"


def test_clean_same_date_is_idempotent():
    assert decide() == "noop"


def test_new_intraday_bar_waits_for_close():
    assert decide(latest="2026-07-30", state="open") == "wait"


def test_new_completed_bar_publishes():
    assert decide(latest="2026-07-30", state="closed") == "publish"


def test_dirty_non_close_snapshot_is_not_recovered():
    assert decide(
        latest="2026-07-30",
        state="open",
        recovery_dirty=True,
        garden_stage="美股盘中快照",
        garden_session="open",
    ) == "wait"


def test_owned_commit_subject_accepts_normal_and_recovery_commits():
    assert module.is_owned_commit_subject("data: update US ETF Compass for 2026-07-29")
    assert module.is_owned_commit_subject("data: recover US ETF Compass close for 2026-07-29")
    assert not module.is_owned_commit_subject("feat: unrelated change")


def test_recovery_scope_excludes_catalog_and_includes_synced_paper_projection():
    assert "public/data/catalog.json" in module.FILES
    assert "public/data/catalog.json" not in module.US_OWNED_FILES
    assert "public/data/us-etf-garden.json" in module.US_OWNED_FILES
    assert "public/data/paper-trading.json" in module.FILES
    assert "public/data/paper-trading.json" in module.US_OWNED_FILES


def test_close_publisher_regenerates_health_after_learning():
    source = MODULE_PATH.read_text(encoding="utf-8")
    learning = source.index('run("python3", "scripts/update_us_compass_learning.py")')
    health = source.index('run("python3", "scripts/generate_us_compass_health.py")')
    assert health > learning
    assert "public/data/us-compass-health.json" in module.FILES
    assert "public/data/us-compass-health.json" in module.US_OWNED_FILES


def test_write_state_is_atomic_and_preserves_fields(tmp_path, monkeypatch):
    state = tmp_path / "publisher.json"
    monkeypatch.setattr(module, "STATE", state)
    module.write_state("evaluated", trade_date="2026-07-29", action="wait")
    module.write_state("published", commit="abc123", verified=True)
    payload = json.loads(state.read_text(encoding="utf-8"))
    assert payload["phase"] == "published"
    assert payload["trade_date"] == "2026-07-29"
    assert payload["action"] == "wait"
    assert payload["commit"] == "abc123"
    assert payload["verified"] is True
    assert not state.with_suffix(".tmp").exists()

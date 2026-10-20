"""Tests for the historical concept backfill (2026-10-20).

The backfill exists because every snapshot from 2026-09-03 onward was archived
with zero concept coverage while mx-data's key was in a dormant tier.

The behaviour that matters most is provenance: Eastmoney returns each stock's
CURRENT concept boards, so a backfilled row must be marked. Without the
markers, a snapshot from September would silently look like it always carried
concept data, which would be indistinguishable from genuinely archived history.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

SCRIPT = SCRIPTS / "backfill_low_chip_concepts_history.py"
SPEC = importlib.util.spec_from_file_location("_probe_backfill_concepts", SCRIPT)
mod = importlib.util.module_from_spec(SPEC)
sys.modules["_probe_backfill_concepts"] = mod
SPEC.loader.exec_module(mod)


def _snapshot(intersection, enrichments=None):
    return {
        "data_as_of": "2026-09-10",
        "intersection": list(intersection),
        "enrichments": enrichments or {
            c: {"industry": "建筑材料", "sector": "玻璃玻纤"} for c in intersection
        },
    }


# ============ missing_codes ============

def test_missing_codes_reports_rows_without_themes():
    payload = _snapshot(
        ["000001.SZ", "000002.SZ"],
        {
            "000001.SZ": {"theme_concepts": ["储能概念"]},
            "000002.SZ": {"theme_concepts": []},
        },
    )
    assert mod.missing_codes(payload) == ["000002.SZ"]


def test_missing_codes_ignores_codes_outside_intersection():
    payload = _snapshot(["000001.SZ"], {"000001.SZ": {}, "000009.SZ": {}})
    assert mod.missing_codes(payload) == ["000001.SZ"]


def test_missing_codes_empty_when_all_covered():
    payload = _snapshot(["000001.SZ"], {"000001.SZ": {"theme_concepts": ["AI"]}})
    assert mod.missing_codes(payload) == []


# ============ apply_to_snapshot ============

def test_apply_writes_concepts_and_derived_fields():
    payload = _snapshot(["000012.SZ"])
    cache = {"000012.SZ": ["沪股通", "建筑材料", "储能概念", "华为概念"]}
    changed = mod.apply_to_snapshot(payload, ["000012.SZ"], cache, "2026-10-20T21:00:00+08:00")
    assert changed == 1
    rec = payload["enrichments"]["000012.SZ"]
    assert rec["concepts"] == cache["000012.SZ"]
    assert rec["theme_concepts"], "派生题材不能为空"
    assert "沪股通" not in rec["theme_concepts"], "泛概念必须被过滤"
    assert rec["sector_with_theme"].startswith("玻璃玻纤（")


def test_apply_marks_backfilled_rows_for_provenance():
    """The marker is what distinguishes backfilled data from archived history."""
    payload = _snapshot(["000012.SZ"])
    cache = {"000012.SZ": ["储能概念"]}
    mod.apply_to_snapshot(payload, ["000012.SZ"], cache, "2026-10-20T21:00:00+08:00")
    rec = payload["enrichments"]["000012.SZ"]
    assert rec["theme_backfilled"] is True
    assert rec["theme_backfilled_at"] == "2026-10-20T21:00:00+08:00"


def test_apply_records_snapshot_level_provenance():
    payload = _snapshot(["000012.SZ"])
    cache = {"000012.SZ": ["储能概念"]}
    mod.apply_to_snapshot(payload, ["000012.SZ"], cache, "2026-10-20T21:00:00+08:00")
    meta = payload["theme_backfill"]
    assert meta["applied_at"] == "2026-10-20T21:00:00+08:00"
    assert meta["source"] == "eastmoney-direct"
    assert meta["rows"] == 1
    assert "current classification" in meta["note"]


def test_apply_skips_codes_missing_from_cache():
    payload = _snapshot(["000012.SZ"])
    changed = mod.apply_to_snapshot(payload, ["000012.SZ"], {}, "t")
    assert changed == 0
    assert "theme_concepts" not in payload["enrichments"]["000012.SZ"]


def test_apply_skips_codes_with_empty_concepts():
    payload = _snapshot(["000012.SZ"])
    changed = mod.apply_to_snapshot(payload, ["000012.SZ"], {"000012.SZ": []}, "t")
    assert changed == 0


def test_apply_sets_theme_source():
    payload = _snapshot(["000012.SZ"])
    mod.apply_to_snapshot(payload, ["000012.SZ"], {"000012.SZ": ["AI"]}, "t")
    assert "eastmoney" in payload["theme_source"].lower()


def test_apply_preserves_existing_enrichment_fields():
    payload = _snapshot(
        ["000012.SZ"],
        {"000012.SZ": {"industry": "玻璃玻纤", "hlp_metrics": {"x": 1}}},
    )
    mod.apply_to_snapshot(payload, ["000012.SZ"], {"000012.SZ": ["储能概念"]}, "t")
    rec = payload["enrichments"]["000012.SZ"]
    assert rec["industry"] == "玻璃玻纤"
    assert rec["hlp_metrics"] == {"x": 1}


# ============ snapshot discovery ============

def test_snapshot_files_skips_variant_files(tmp_path, monkeypatch):
    (tmp_path / "2026-09-03.json").write_text("{}", encoding="utf-8")
    (tmp_path / "2026-09-03.threshold-2.5.json").write_text("{}", encoding="utf-8")
    (tmp_path / "2026-09-04.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(mod, "HISTORY", tmp_path)
    days = [d for d, _ in mod.snapshot_files()]
    assert days == ["2026-09-03", "2026-09-04"], "变体文件不能当正式快照回填"


def test_snapshot_files_respects_since(tmp_path, monkeypatch):
    for day in ("2026-08-21", "2026-09-10", "2026-10-01"):
        (tmp_path / f"{day}.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(mod, "HISTORY", tmp_path)
    days = [d for d, _ in mod.snapshot_files(since="2026-09-01")]
    assert days == ["2026-09-10", "2026-10-01"]


# ============ fetch failure semantics ============

def test_fetch_concepts_records_failures_explicitly(monkeypatch):
    import em_data_client

    class FakeClient:
        def __init__(self):
            self.calls = []

        def concepts(self, code):
            self.calls.append(code)
            if code == "000002.SZ":
                raise em_data_client.EastmoneyError("gave up after 4 attempts")
            return ["储能概念"]

    fake = FakeClient()
    monkeypatch.setattr(mod, "get_client", lambda: fake)
    cache, failed = mod.fetch_concepts(["000001.SZ", "000002.SZ"])
    assert cache == {"000001.SZ": ["储能概念"]}
    assert failed == ["000002.SZ"], "失败必须上报，不能当作无数据"

"""Regression tests for the 2026-10-20 industry-degradation defect.

The defect:
- `refresh_low_chip_ftshare_industry.py` rebuilds the SW industry mapping from
  `load_industry_hints(current, tracking)`.
- `enrichment["industry"]` stores only the SW LEVEL-2 NAME (e.g. 玻璃玻纤), not
  an `L1--L2--L3` path. `split_industry_path` therefore saw a single segment,
  dropped the entry, and the refresh rewrote 81 of 88 published rows to
  「待补充」 — discarding the published dataset's own classification in favour
  of the tracking pool's (empty) industry strings.
- That failed `test_low_chip_page.py::test_low_chip_page_hides_private_screening_strategy`.

The fix:
- `industry_path_from_levels()` rebuilds the path from the structured
  `industry_level1/2/3` fields, which survive the refresh.
- Published `current` rows take priority over tracking-derived hints.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "refresh_low_chip_ftshare_industry.py"
SPEC = importlib.util.spec_from_file_location("refresh_low_chip_industry_probe2", SCRIPT)
mod = importlib.util.module_from_spec(SPEC)
sys.modules["refresh_low_chip_industry_probe2"] = mod
SPEC.loader.exec_module(mod)


# ============ industry_path_from_levels ============

def test_rebuilds_full_path_from_structured_levels():
    enrichment = {
        "industry": "玻璃玻纤",
        "industry_level1": {"code": None, "name": "建筑材料"},
        "industry_level2": {"code": None, "name": "玻璃玻纤"},
        "industry_level3": {"code": None, "name": "玻璃制造"},
    }
    assert mod.industry_path_from_levels(enrichment) == "建筑材料--玻璃玻纤--玻璃制造"


def test_rebuilds_path_without_level3():
    enrichment = {
        "industry_level1": {"name": "建筑材料"},
        "industry_level2": {"name": "玻璃玻纤"},
        "industry_level3": {"name": "待补充"},
    }
    assert mod.industry_path_from_levels(enrichment) == "建筑材料--玻璃玻纤"


def test_returns_empty_when_levels_missing_or_placeholder():
    assert mod.industry_path_from_levels({}) == ""
    assert mod.industry_path_from_levels({"industry_level2": {"name": "待补充"}}) == ""


def test_accepts_plain_string_levels():
    enrichment = {"industry_level1": "建筑材料", "industry_level2": "玻璃玻纤"}
    assert mod.industry_path_from_levels(enrichment) == "建筑材料--玻璃玻纤"


# ============ load_industry_hints must not degrade published rows ============

def test_published_row_keeps_its_industry_when_tracking_is_empty():
    """The exact 2026-10-20 scenario: tracking has no industry, current does."""
    current = {
        "intersection": ["000012.SZ"],
        "enrichments": {
            "000012.SZ": {
                "industry": "玻璃玻纤",
                "industry_level1": {"name": "建筑材料"},
                "industry_level2": {"name": "玻璃玻纤"},
                "industry_level3": {"name": "玻璃制造"},
            },
        },
    }
    tracking = {
        "stocks": {
            "000012.SZ": {"industry": "", "first_seen": "2026-08-01"},
        },
    }
    hints = mod.load_industry_hints(current, tracking)
    assert len(mod.split_industry_path(hints["000012.SZ"])) >= 2, (
        "published row lost its industry path — this is the 2026-10-20 defect"
    )
    assert hints["000012.SZ"] == "建筑材料--玻璃玻纤--玻璃制造"


def test_published_row_wins_over_tracking_hint():
    current = {
        "intersection": ["000001.SZ"],
        "enrichments": {
            "000001.SZ": {
                "industry": "股份制银行Ⅱ",
                "industry_level1": {"name": "银行"},
                "industry_level2": {"name": "股份制银行Ⅱ"},
            },
        },
    }
    tracking = {
        "stocks": {
            "000001.SZ": {"industry": "旧行业--旧二级", "first_seen": "2026-08-01"},
        },
    }
    hints = mod.load_industry_hints(current, tracking)
    assert hints["000001.SZ"] == "银行--股份制银行Ⅱ"


def test_tracking_path_still_used_for_stocks_outside_todays_pool():
    current = {"intersection": [], "enrichments": {}}
    tracking = {
        "stocks": {
            "600000.SH": {"industry": "银行--股份制银行Ⅱ", "first_seen": "2026-07-01"},
        },
    }
    hints = mod.load_industry_hints(current, tracking)
    assert hints["600000.SH"] == "银行--股份制银行Ⅱ"


def test_missing_first_seen_snapshot_does_not_raise():
    """A tracking stock with no history snapshot must not crash the refresh."""
    current = {"intersection": [], "enrichments": {}}
    tracking = {"stocks": {"600001.SH": {"industry": "", "first_seen": "1999-01-01"}}}
    hints = mod.load_industry_hints(current, tracking)
    assert hints["600001.SH"] == ""


# ============ end-to-end: apply_refresh keeps published industries ============

def test_apply_refresh_does_not_rewrite_published_industry_to_placeholder():
    current = {
        "data_as_of": "2026-10-20",
        "intersection": ["000012.SZ"],
        "enrichments": {
            "000012.SZ": {
                "industry": "玻璃玻纤",
                "sector": "玻璃玻纤",
                "industry_level1": {"name": "建筑材料"},
                "industry_level2": {"name": "玻璃玻纤"},
                "industry_level3": {"name": "玻璃制造"},
                "theme_concepts": [],
            },
        },
    }
    tracking = {"stocks": {"000012.SZ": {"industry": "", "first_seen": "2026-08-01"}}}
    mapping = mod.build_industry_mapping(mod.load_industry_hints(current, tracking))
    assert "000012.SZ" in mapping, "the published row's own path must reach the mapping"
    mod.apply_refresh(current, tracking, mapping, {}, "2026-10-20")
    assert current["enrichments"]["000012.SZ"]["industry"] != "待补充"
    assert current["enrichments"]["000012.SZ"]["industry"] == "玻璃玻纤"

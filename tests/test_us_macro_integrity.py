from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "generate_us_macro.py"

spec = importlib.util.spec_from_file_location("generate_us_macro_integrity", SCRIPT)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_carry_forward_official_drops_market_copper_mislabeled_as_fed_assets():
    official: dict[str, dict] = {}
    previous_snapshot = {
        "official": {
            "copper": {"series": "H.4.1 Total assets", "value": 6_738_190.0},
            "fed_assets": {"series": "H.4.1 Total assets", "value": 6_737_204.0},
        },
    }

    module.carry_forward_official(official, previous_snapshot)

    assert "copper" not in official
    assert official["fed_assets"]["series"] == "H.4.1 Total assets"
    assert official["fed_assets"]["stale"] is True


def test_bls_employment_metadata_matches_august_observation_release():
    official = {
        "unemployment": {"observation_period": "2026-08"},
        "payrolls": {"observation_period": "2026-08"},
    }

    module.apply_bls_release_metadata(official)

    for key in ("unemployment", "payrolls"):
        assert official[key]["date"] == "2026-09-04"
        assert official[key]["updated_at"] == "2026-09-04T08:30:00-04:00"


def test_real_retail_metadata_matches_july_observation_release():
    item = {"observation_period": "2026-07"}

    module.apply_real_retail_release_metadata(item)

    assert item["date"] == "2026-08-14"
    assert item["updated_at"] == "2026-08-14T08:30:00-04:00"

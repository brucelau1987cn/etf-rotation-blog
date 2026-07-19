#!/usr/bin/env python3
"""Unit tests for A-share mid-macro constraint helpers."""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "generate_a_share_mid_macro.py"


def load_module():
    spec = importlib.util.spec_from_file_location("generate_a_share_mid_macro", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MidMacroConstraintTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = load_module()

    def test_parse_position_band(self) -> None:
        self.assertEqual(self.mod.parse_position_band("权益10%-30%；防御/现金50%-70%"), (10, 30))
        self.assertEqual(self.mod.parse_position_band("30%-50%"), (30, 50))

    def test_shift_band_never_raises(self) -> None:
        self.assertEqual(self.mod.shift_band((10, 30), 1), (0, 10))
        self.assertEqual(self.mod.shift_band((50, 70), 2), (10, 30))
        self.assertEqual(self.mod.shift_band((0, 10), 3), (0, 10))

    def test_build_constraint_levels(self) -> None:
        factors = [
            {"state": "中性", "score": 0.2},
            {"state": "中性", "score": 0.1},
            {"state": "中性", "score": 0.0},
        ]
        c0 = self.mod.build_constraint(factors, "权益30%-50%；防御/现金50%-70%", "震荡")
        self.assertEqual(c0["headwind_level"], 0)
        self.assertTrue(c0["allow_chase"])
        self.assertTrue(c0["allow_new_offense"])

        factors1 = [
            {"state": "逆风", "score": 1.5},
            {"state": "中性", "score": 0.2},
            {"state": "中性", "score": 0.1},
        ]
        c1 = self.mod.build_constraint(factors1, "权益30%-50%；防御/现金50%-70%", "震荡")
        self.assertEqual(c1["headwind_level"], 1)
        self.assertFalse(c1["allow_chase"])
        self.assertTrue(c1["allow_new_offense"])
        self.assertEqual(c1["constrained_equity_band"]["high"], 30)

        factors3 = [
            {"state": "逆风", "score": 2.0},
            {"state": "逆风", "score": 2.0},
            {"state": "逆风", "score": 2.0},
        ]
        c3 = self.mod.build_constraint(factors3, "权益10%-30%；防御/现金50%-70%", "防御")
        self.assertEqual(c3["headwind_level"], 3)
        self.assertFalse(c3["allow_new_offense"])
        self.assertEqual(c3["position"], "权益0%-10%；防御/现金90%-100%")

    def test_format_band(self) -> None:
        self.assertEqual(self.mod.format_band((0, 10)), "权益0%-10%；防御/现金90%-100%")

    def test_constraint_records_uncompressed_base_source(self) -> None:
        factors = [
            {"state": "逆风", "score": 2.0},
            {"state": "逆风", "score": 2.0},
            {"state": "中性", "score": 0.2},
        ]
        result = self.mod.build_constraint(factors, "权益30%-50%；防御/现金50%-70%", "震荡")
        self.assertEqual(result["base_position"], "权益30%-50%；防御/现金50%-70%")
        self.assertEqual(result["base_position_source"], "etf-garden-pool.market_regime")
        self.assertEqual(result["base_market_state"], "震荡")

    def test_macro_item_formats_values(self) -> None:
        item = self.mod.macro_item(
            key="pmi", title="制造业PMI", value=50.3, unit="", date="2026-06-01",
            frequency="月频", source="国家统计局", source_url="https://www.stats.gov.cn/",
            detail="50为荣枯线", change=0.3, tone="positive",
        )
        self.assertEqual(item["display"], "50.30")
        self.assertEqual(item["change"], 0.3)
        self.assertEqual(item["tone"], "positive")

    def test_official_observations_are_presented_as_concrete_background(self) -> None:
        observations = [{
            "key": "pmi", "title": "制造业PMI", "display": "50.30",
            "value": 50.3, "as_of": "2026-06-01", "source": "国家统计局",
            "detail": "50为荣枯线",
        }]
        self.assertTrue(observations)
        self.assertEqual(observations[0]["display"], "50.30")

    def test_macro_framework_is_complete_and_honest(self) -> None:
        dimensions = self.mod.MACRO_FRAMEWORK
        self.assertEqual(len(dimensions), 6)
        self.assertEqual(
            {item["key"] for item in dimensions},
            {
                "monetary_liquidity",
                "credit_impulse",
                "growth_cycle",
                "inflation_margin",
                "external_fx",
                "market_liquidity",
            },
        )
        covered = {item["coverage"] for item in dimensions}
        self.assertIn("pending_official", covered)
        self.assertTrue(all(item["primary_source"] for item in dimensions))
        self.assertTrue(all(item.get("links") for item in dimensions))
        self.assertTrue(all(link["url"].startswith("https://") for item in dimensions for link in item["links"]))


if __name__ == "__main__":
    unittest.main()

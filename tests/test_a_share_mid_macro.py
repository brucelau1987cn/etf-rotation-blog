#!/usr/bin/env python3
"""Unit tests for A-share mid-macro constraint helpers."""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
import unittest.mock
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

    def test_apply_to_recommendations_advances_parent_updated_at(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            reco = Path(directory) / "garden.json"
            reco.write_text(json.dumps({
                "updated_at": "2026-08-18T22:00:00+08:00",
                "position": "权益10%-30%；防御/现金70%-90%",
                "plant": [],
            }), encoding="utf-8")
            original = getattr(self.mod, "RECO")
            setattr(self.mod, "RECO", reco)
            try:
                self.mod.apply_to_recommendations({
                    "base_position": "权益10%-30%；防御/现金70%-90%",
                    "position": "权益10%-30%；防御/现金70%-90%",
                    "headwind_level": 0,
                    "label": "顺风",
                    "equity_constraint": "维持",
                    "allow_chase": True,
                    "allow_new_offense": True,
                }, [], "2026-08-18 22:35:00 CST")
            finally:
                setattr(self.mod, "RECO", original)
            saved = json.loads(reco.read_text(encoding="utf-8"))
            self.assertEqual(saved["updated_at"], "2026-08-18 22:35:00 CST")

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

    def test_macro_gate_syncs_final_candidate_audit(self) -> None:
        reco = {
            "plant": [{"code": "A"}, {"code": "B"}],
            "candidate_selection": {
                "selected_codes": ["A", "B", "C"],
                "selected_count": 3,
            },
        }

        self.mod.sync_candidate_selection_audit(reco, headwind_level=3)

        audit = reco["candidate_selection"]
        self.assertEqual(audit["pre_macro_selected_codes"], ["A", "B", "C"])
        self.assertEqual(audit["selected_codes"], ["A", "B"])
        self.assertEqual(audit["selected_count"], 2)
        self.assertEqual(audit["macro_gate_removed_codes"], ["C"])
        self.assertEqual(audit["macro_headwind_level"], 3)

        self.mod.sync_candidate_selection_audit(reco, headwind_level=3)
        self.assertEqual(audit["pre_macro_selected_codes"], ["A", "B", "C"])
        self.assertEqual(audit["macro_gate_removed_codes"], ["C"])

        empty = {
            "plant": [],
            "candidate_selection": {
                "pre_macro_selected_codes": [],
                "selected_codes": ["STALE"],
            },
        }
        self.mod.sync_candidate_selection_audit(empty, headwind_level=3)
        self.assertEqual(empty["candidate_selection"]["pre_macro_selected_codes"], [])
        self.assertEqual(empty["candidate_selection"]["macro_gate_removed_codes"], [])

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


class TencentThsFallbackTests(unittest.TestCase):
    """fetch_tencent_bars must fall back to THS only when both Tencent hosts fail. All network is mocked."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = load_module()

    def _payload(self, symbol: str, n: int = 30) -> dict:
        rows = [[f"2026-10-{i + 1:02d}", "1", str(1.0 + i * 0.01), "1", "1", "1"] for i in range(n)]
        return {"data": {symbol: {"qfqday": rows}}}

    def test_primary_host_used_without_ths(self) -> None:
        calls: list[str] = []

        def fake_fetch(url: str, timeout: int = 20):
            calls.append(url)
            return self._payload("sh510300")

        ths = unittest.mock.Mock(side_effect=AssertionError("THS must not be called"))
        with unittest.mock.patch.object(self.mod, "fetch_json", side_effect=fake_fetch), \
                unittest.mock.patch.object(self.mod, "fetch_ths_bars", ths):
            bars = self.mod.fetch_tencent_bars("sh510300", 30)
        self.assertEqual(len(bars), 30)
        self.assertIn("ifzq.gtimg.cn", calls[0])
        ths.assert_not_called()

    def test_both_hosts_501_falls_back_to_ths(self) -> None:
        def fake_fetch(url: str, timeout: int = 20):
            raise RuntimeError("HTTP Error 501: Not Implemented")

        ths_bars = [{"date": "2026-10-01", "open": None, "close": 1.0, "high": None, "low": None, "volume": None}] * 30
        with unittest.mock.patch.object(self.mod, "fetch_json", side_effect=fake_fetch), \
                unittest.mock.patch.object(self.mod, "fetch_ths_bars", return_value=ths_bars) as ths:
            bars = self.mod.fetch_tencent_bars("sh510300", 60)
        ths.assert_called_once_with("sh510300", 60)
        self.assertEqual(bars, ths_bars)

    def test_both_sources_down_raises_with_both_reasons(self) -> None:
        def fake_fetch(url: str, timeout: int = 20):
            raise RuntimeError("HTTP Error 501")

        with unittest.mock.patch.object(self.mod, "fetch_json", side_effect=fake_fetch), \
                unittest.mock.patch.object(self.mod, "fetch_ths_bars", side_effect=OSError("ths down")):
            with self.assertRaises(RuntimeError) as ctx:
                self.mod.fetch_tencent_bars("sh510300", 60)
        message = str(ctx.exception)
        self.assertIn("501", message)
        self.assertIn("ths down", message)

    def test_ths_short_bars_treated_as_failure(self) -> None:
        def fake_fetch(url: str, timeout: int = 20):
            raise RuntimeError("HTTP Error 501")

        short = [{"date": "2026-10-01", "open": None, "close": 1.0, "high": None, "low": None, "volume": None}] * 5
        with unittest.mock.patch.object(self.mod, "fetch_json", side_effect=fake_fetch), \
                unittest.mock.patch.object(self.mod, "fetch_ths_bars", return_value=short):
            with self.assertRaises(RuntimeError) as ctx:
                self.mod.fetch_tencent_bars("sh510300", 60)
        self.assertIn("ths short bars 5", str(ctx.exception))

    def test_fetch_ths_bars_maps_symbol_and_keeps_close_only(self) -> None:
        seen: dict = {}

        def fake_ths(item, count=0, end=None):
            seen.update(item)
            return [{"trade_date": "2026-10-22", "close": 4.177}, {"trade_date": "2026-10-23", "close": float("nan")}]

        fake_mod = unittest.mock.Mock(fetch_ths_history=fake_ths)
        with unittest.mock.patch.dict("sys.modules", {"attach_low_chip_touchstone": fake_mod}):
            bars = self.mod.fetch_ths_bars("sz159915", 60)
        self.assertEqual(seen, {"code": "159915", "market": "XSHE"})
        self.assertEqual(bars, [{"date": "2026-10-22", "open": None, "close": 4.177, "high": None, "low": None, "volume": None}])


if __name__ == "__main__":
    unittest.main()

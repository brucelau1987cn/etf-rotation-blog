#!/usr/bin/env python3
"""Unit tests for US ETF local bar cache helpers."""
from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class UsBarCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cache = load("us_etf_bar_cache", ROOT / "scripts" / "us_etf_bar_cache.py")

    def test_upsert_and_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "us.db"
            with self.cache.connect(db_path) as db:
                self.cache.upsert_instruments(
                    db,
                    [{"symbol": "SPY", "name": "SPDR S&P 500", "asset_type": "宽基", "theme": "美国宽基"}],
                )
                n = self.cache.upsert_bars(
                    db,
                    [
                        {
                            "symbol": "SPY",
                            "trade_date": "2026-07-10",
                            "open": 1,
                            "high": 2,
                            "low": 0.5,
                            "close": 1.5,
                            "adj_close": 1.5,
                            "volume": 10,
                            "source": "yahoo",
                            "is_final": 1,
                        },
                        {
                            "symbol": "SPY",
                            "trade_date": "2026-07-13",
                            "open": 1.6,
                            "high": 1.8,
                            "low": 1.4,
                            "close": 1.7,
                            "adj_close": 1.7,
                            "volume": 12,
                            "source": "yahoo",
                            "is_final": 0,
                        },
                    ],
                )
                self.assertEqual(n, 2)
                bars = self.cache.get_bars(db, "SPY", source="yahoo", limit=10, final_only=False)
                self.assertEqual(len(bars), 2)
                final_only = self.cache.get_bars(db, "SPY", source="yahoo", limit=10, final_only=True)
                self.assertEqual(len(final_only), 1)
                rows = self.cache.bars_to_generator_rows(bars)
                self.assertEqual(rows[-1]["date"], "2026-07-13")
                self.assertEqual(rows[-1]["adj"], 1.7)
                self.assertEqual(self.cache.coverage(db, ["SPY"], "2026-07-10"), 1)


if __name__ == "__main__":
    unittest.main()

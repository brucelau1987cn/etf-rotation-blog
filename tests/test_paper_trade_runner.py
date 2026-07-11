import importlib.util
import sys
import unittest
from pathlib import Path

P = Path(__file__).resolve().parents[1] / "scripts" / "paper_trade_runner.py"
spec = importlib.util.spec_from_file_location("paper", P)
assert spec is not None and spec.loader is not None
paper = importlib.util.module_from_spec(spec)
sys.modules["paper"] = paper
spec.loader.exec_module(paper)


class PaperTradingTests(unittest.TestCase):
    def test_costs_and_lots(self):
        self.assertEqual(paper.costs("A", "buy", 1, 100), 5.05)
        self.assertEqual(paper.costs("US", "buy", 100, 1), 1.05)
        self.assertEqual(paper.size_order("A", 150000, 150000, 1) % 100, 0)
        self.assertEqual(paper.size_order("US", 20000, 20000, 101) % 1, 0)

    def test_sizing_caps_reserve_positions(self):
        self.assertLessEqual(paper.size_order("A", 150000, 150000, 2) * 2, 15000)
        self.assertEqual(paper.size_order("A", 150000, 30000, 1), 0)
        self.assertEqual(paper.size_order("US", 20000, 20000, 10, 10), 0)

    def test_ready_never_trades(self):
        data = {"plant": [{"status": "准备种花", "code": "510000"}], "harvest": [{"status": "准备摘花", "code": "510001"}]}
        self.assertEqual(paper.normalize_signals("A", data), ([], []))
        us = {"flower_signals": {"ready_plant": [{"symbol": "SPY"}], "ready_harvest": [{"symbol": "QQQ"}]}}
        self.assertEqual(paper.normalize_signals("US", us), ([], []))

    def test_same_bar_stop_first(self):
        account = paper.new_account("US")
        paper.execute(account, "SPY", "SPY", "buy", 100, 10, "t", "plant", "buy", 110, 90)
        quotes = {"SPY": {"price": 100, "low": 89, "high": 111, "timestamp": "bar"}}
        trades = paper.process_bar(account, ([], []), quotes, "t2")
        self.assertEqual(trades[0]["reason"], "stop")
        self.assertEqual(trades[0]["price"], 90)

    def test_idempotency_and_immutable_entry_levels(self):
        account = paper.new_account("US")
        signals = ([{"symbol": "SPY", "name": "SPY", "support": 100, "target": 110, "stop": 90, "level_basis": "frozen-v1", "trade_date": "2026-01-02"}], [])
        quotes = {"SPY": {"price": 100, "low": 99, "high": 101, "timestamp": "bar"}}
        self.assertEqual(len(paper.process_bar(account, signals, quotes, "t")), 1)
        self.assertEqual(paper.process_bar(account, signals, quotes, "t"), [])
        self.assertEqual(account["positions"]["SPY"]["target"], 110)
        self.assertEqual(account["positions"]["SPY"]["level_basis"], "frozen-v1")

    def test_close_math_and_same_market_day_replaces(self):
        account = paper.new_account("US")
        paper.execute(account, "SPY", "SPY", "buy", 100, 10, "t", "plant", "buy", 110, 90)
        quotes = {"SPY": {"price": 105}}
        paper.mark(account, quotes, "2026-01-02T15:00:00+00:00", True)
        expected = account["cash"] + 1050
        self.assertAlmostEqual(account["equity"], expected)
        self.assertAlmostEqual(account["unrealized_pnl"], 50 - paper.costs("US", "buy", 100, 10))
        self.assertEqual(len(account["history"]), 1)
        paper.mark(account, quotes, "2026-01-02T20:00:00+00:00", True)
        self.assertEqual(len(account["history"]), 1)

    def test_no_retroactive_fill_before_signal_was_armed(self):
        account = paper.new_account("A")
        signal = {"symbol": "510000", "name": "x", "support": 1.0, "target": 1.1, "stop": .9, "price_date": "2026-07-13"}
        first = {"510000": {"price": 1.05, "low": .98, "high": 1.06, "timestamp": "20260713143000"}}
        self.assertEqual(paper.eligible_buys(account, [signal], first, "t1"), [])
        unchanged = {"510000": {"price": 1.04, "low": .98, "high": 1.06, "timestamp": "20260713143500"}}
        self.assertEqual(paper.eligible_buys(account, [signal], unchanged, "t2"), [])
        fresh_touch = {"510000": {"price": 1.01, "low": .97, "high": 1.05, "timestamp": "20260713144000"}}
        self.assertEqual(len(paper.eligible_buys(account, [signal], fresh_touch, "t3")), 1)

    def test_market_windows_and_quote_freshness(self):
        self.assertTrue(paper.intraday_window("A", "2026-07-13T02:00:00+00:00"))  # 10:00 CST
        self.assertFalse(paper.intraday_window("A", "2026-07-13T04:00:00+00:00"))  # lunch
        self.assertTrue(paper.intraday_window("US", "2026-07-13T14:00:00+00:00"))  # 10:00 EDT
        self.assertFalse(paper.intraday_window("US", "2026-07-11T14:00:00+00:00"))  # weekend
        self.assertEqual(paper.quote_day("A", {"timestamp": "20260713100000"}), "2026-07-13")
        self.assertEqual(paper.quote_day("US", {"timestamp": 1783951200}), "2026-07-13")


if __name__ == "__main__":
    unittest.main()

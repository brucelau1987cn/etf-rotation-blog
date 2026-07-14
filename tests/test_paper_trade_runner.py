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
        buys, sells = paper.normalize_signals("A", data)
        self.assertEqual(sells, [])
        self.assertEqual(len(buys), 1)
        self.assertEqual(buys[0]["kind"], "ready_plant")
        self.assertEqual(buys[0].get("pending_only"), "1")
        # Pending-only ready signals must not fill.
        account = paper.new_account("A")
        quotes = {"510000": {"price": 1, "low": 0.9, "high": 1.1, "timestamp": "t"}}
        self.assertEqual(paper.process_bar(account, (buys, sells), quotes, "t"), [])
        us = {"flower_signals": {"ready_plant": [{"symbol": "SPY", "signal": "候场"}], "ready_harvest": [{"symbol": "QQQ", "signal": "止盈观察"}]}}
        us_buys, us_sells = paper.normalize_signals("US", us)
        self.assertEqual(us_sells, [])
        self.assertEqual(len(us_buys), 1)
        self.assertEqual(us_buys[0]["kind"], "ready_plant")
        self.assertEqual(paper.process_bar(paper.new_account("US"), (us_buys, us_sells), {"SPY": {"price": 100, "low": 99, "high": 101, "timestamp": "t"}}, "t"), [])

    def test_formal_plant_status_aliases(self):
        for status in ("伏击", "种花"):
            buys, sells = paper.normalize_signals("A", {"plant": [{"status": status, "code": "510000", "support": 1, "target": 1.1, "stop": 0.9}], "harvest": []})
            self.assertEqual(len(buys), 1)
            self.assertEqual(buys[0]["kind"], "plant")
            self.assertFalse(buys[0].get("pending_only"))
        for status in ("兑现", "摘花"):
            buys, sells = paper.normalize_signals("A", {"plant": [], "harvest": [{"status": status, "code": "510001"}]})
            self.assertEqual(len(sells), 1)
            self.assertEqual(sells[0]["kind"], "harvest")

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

    def test_stopped_position_cannot_reenter_same_signal(self):
        account = paper.new_account("US")
        signal = {"symbol": "SPY", "name": "SPY", "support": 95, "target": 110, "stop": 90, "trade_date": "2026-07-10", "_signal_id": "US:SPY:2026-07-10:plant"}
        buy_bar = {"SPY": {"price": 95, "low": 95, "high": 96, "timestamp": 1}}
        self.assertEqual(len(paper.process_bar(account, ([signal], []), buy_bar, "2026-07-13T14:00:00+00:00")), 1)
        stop_bar = {"SPY": {"price": 89, "low": 89, "high": 96, "timestamp": 2}}
        trades = paper.process_bar(account, ([signal], []), stop_bar, "2026-07-13T14:05:00+00:00")
        self.assertEqual([(x["side"], x["reason"]) for x in trades], [("sell", "stop")])
        self.assertNotIn("SPY", account["positions"])
        later = {"SPY": {"price": 94, "low": 94, "high": 95, "timestamp": 3}}
        self.assertEqual(paper.process_bar(account, ([signal], []), later, "2026-07-13T14:10:00+00:00"), [])

    def test_signal_status_dates_and_quote_age_guards(self):
        us = paper.normalize_signals("US", {"date": "2026-07-10", "flower_signals": {"plant": [
            {"symbol": "BAD", "signal": "准备种花"}, {"symbol": "GOOD", "signal": "种花"}]}})
        self.assertEqual([x["symbol"] for x in us[0]], ["GOOD"])
        self.assertEqual(len(paper.valid_signals("US", us[0], "2026-07-13")), 1)
        self.assertEqual(paper.valid_signals("US", us[0], "2026-07-15"), [])
        a = paper.normalize_signals("A", {"date": "2026-07-13", "plant": [{"code": "510000", "status": "种花"}]})
        self.assertEqual(len(paper.valid_signals("A", a[0], "2026-07-13")), 1)
        self.assertEqual(paper.valid_signals("A", a[0], "2026-07-14"), [])
        bar = {"timestamp": "20260713100000"}
        self.assertEqual(paper.quote_age_seconds("A", bar, "2026-07-13T02:02:00+00:00"), 120)

    def test_public_export_strips_internal_lifecycle(self):
        state = paper.new_state("2026-07-11T00:00:00+00:00")
        state["accounts"]["A"]["processed_event_ids"] = ["x"]
        state["accounts"]["A"]["consumed_signal_ids"] = ["y"]
        state["accounts"]["A"]["armed_signals"] = {"z": {}}
        public = paper.public_view(state)["accounts"]["A"]
        self.assertNotIn("processed_event_ids", public)
        self.assertNotIn("consumed_signal_ids", public)
        self.assertNotIn("armed_signals", public)

    def test_market_windows_and_quote_freshness(self):
        self.assertTrue(paper.intraday_window("A", "2026-07-13T02:00:00+00:00"))  # 10:00 CST
        self.assertFalse(paper.intraday_window("A", "2026-07-13T04:00:00+00:00"))  # lunch
        self.assertTrue(paper.intraday_window("US", "2026-07-13T14:00:00+00:00"))  # 10:00 EDT
        self.assertFalse(paper.intraday_window("US", "2026-07-11T14:00:00+00:00"))  # weekend
        self.assertEqual(paper.quote_day("A", {"timestamp": "20260713100000"}), "2026-07-13")
        self.assertEqual(paper.quote_day("US", {"timestamp": 1783951200}), "2026-07-13")


if __name__ == "__main__":
    unittest.main()

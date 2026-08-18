import importlib.util
import json
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from unittest import mock
from pathlib import Path

P = Path(__file__).resolve().parents[1] / "scripts" / "paper_trade_runner.py"
spec = importlib.util.spec_from_file_location("paper", P)
assert spec is not None and spec.loader is not None
paper = importlib.util.module_from_spec(spec)
sys.modules["paper"] = paper
spec.loader.exec_module(paper)


class PaperTradingTests(unittest.TestCase):
    def test_public_lock_resolves_to_the_same_file_as_publishers(self):
        self.assertEqual(paper.PUBLIC_LOCK, Path("/root/.hermes/state/etf-paper-publish"))

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
        self.assertEqual(account["events"][0]["signal_date"], "2026-01-02")
        self.assertEqual(account["events"][0]["signal_kind"], "plant")
        self.assertEqual(account["events"][0]["signal_level"], 100)

    def test_sell_event_retains_originating_signal_and_entry_contract(self):
        account = paper.new_account("US")
        signal = {"symbol": "SPY", "name": "SPY", "support": 100, "target": 110, "stop": 90, "_source_date": "2026-01-02", "kind": "plant"}
        buy = {"SPY": {"price": 100, "low": 99, "high": 101, "timestamp": "buy-bar"}}
        paper.process_bar(account, ([signal], []), buy, "2026-01-05T15:00:00+00:00")
        sell = {"SPY": {"price": 110, "low": 109, "high": 111, "timestamp": "sell-bar"}}
        trades = paper.process_bar(account, ([], []), sell, "2026-01-05T18:00:00+00:00")
        event = trades[0]
        self.assertEqual(event["side"], "sell")
        self.assertEqual(event["signal_date"], "2026-01-02")
        self.assertEqual(event["signal_kind"], "plant")
        self.assertEqual(event["signal_level"], 100)
        self.assertEqual(event["entry_price"], 100)
        self.assertEqual(event["entry_at"], "2026-01-05T15:00:00+00:00")

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
        self.assertEqual(paper.valid_signals("US", us[0], "2026-07-14"), [])
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

    def test_public_export_adds_read_only_decision_reason_review(self):
        state = paper.new_state("2026-07-11T00:00:00+00:00")
        account = state["accounts"]["US"]
        account["events"] = [
            {"id": "b", "side": "buy", "reason": "plant", "symbol": "SPY", "price": 100, "quantity": 1},
            {"id": "t", "side": "sell", "reason": "target", "symbol": "SPY", "price": 110, "quantity": 1,
             "entry_price": 100, "entry_cost": 1, "cost": 1},
            {"id": "s", "side": "sell", "reason": "stop", "symbol": "QQQ", "price": 90, "quantity": 1,
             "entry_price": 100, "entry_cost": 1, "cost": 1},
        ]

        public = paper.public_view(state)["accounts"]["US"]

        assert [event["decision_tag"] for event in public["events"]] == ["计划内首仓", "止盈退出", "止损退出"]
        assert public["decision_review"] == {
            "scope": "all_events", "total": 3,
            "by_tag": [
                {"tag": "计划内首仓", "count": 1, "closed_pnl": None},
                {"tag": "止盈退出", "count": 1, "closed_pnl": 8.0},
                {"tag": "止损退出", "count": 1, "closed_pnl": -12.0},
            ],
            "unavailable_reasons": ["胜率与标签收益率 UNAVAILABLE：当前事件未持久化逐笔资金占用和统一持有期"],
        }

    def test_decision_review_degrades_malformed_closed_pnl_to_unavailable(self):
        state = paper.new_state("2026-07-11T00:00:00+00:00")
        account = state["accounts"]["US"]
        account["events"] = [{
            "id": "bad", "side": "sell", "reason": "target", "symbol": "SPY",
            "price": "bad", "quantity": 1, "entry_price": 100, "entry_cost": 1, "cost": 1,
        }]
        public = paper.public_view(state)["accounts"]["US"]
        assert public["decision_review"]["by_tag"] == [
            {"tag": "止盈退出", "count": 1, "closed_pnl": None}
        ]

    def test_paper_page_displays_decision_reason_tags_and_review(self):
        page = (P.parents[1] / "src/pages/paper.astro").read_text(encoding="utf-8")
        for marker in ("操作原因复盘", "decision_review", "decision_tag", "标签仅用于执行复盘"):
            self.assertIn(marker, page)

    def test_public_pending_projection_tracks_current_sources_and_excludes_positions(self):
        state = paper.new_state("2026-07-28T00:00:00+00:00")
        state["accounts"]["A"]["positions"]["159920"] = {"symbol": "159920"}
        state["accounts"]["US"]["positions"]["IBIT"] = {"symbol": "IBIT"}
        sources = {
            "A": {
                "date": "2026-07-28", "updated_at": "2026-07-28 22:00 CST",
                "plant": [
                    {"code": "560080", "name": "央企ETF", "status": "候场", "support": 1, "target": 1.1, "stop": .9},
                    {"code": "159920", "name": "恒生ETF", "status": "伏击", "support": 2, "target": 2.2, "stop": 1.8},
                    {"code": "000000", "name": "无效", "status": "候场", "level_status": "invalid"},
                ],
            },
            "US": {
                "date": "2026-07-27", "updated_at": "2026-07-27T18:30:00-04:00",
                "flower_signals": {
                    "ready_plant": [{"symbol": "EWZ", "name": "Brazil", "signal": "候场", "support": 30, "target": 33, "stop": 28}],
                    "plant": [{"symbol": "IBIT", "name": "Bitcoin", "signal": "伏击触发", "support": 50, "target": 55, "stop": 45}],
                    "harvest": [{"symbol": "SPY", "name": "SPY", "signal": "兑现触发"}],
                },
            },
        }

        public = paper.project_public_pending(state, sources)

        self.assertEqual([x["symbol"] for x in public["accounts"]["A"]["public_pending_signals"]], ["560080"])
        self.assertEqual([x["symbol"] for x in public["accounts"]["US"]["public_pending_signals"]], ["EWZ"])
        self.assertEqual(public["accounts"]["A"]["public_pending_signals"][0]["status"], "候场")
        self.assertEqual(public["accounts"]["US"]["public_pending_signals"][0]["source_date"], "2026-07-27")
        self.assertEqual(public["accounts"]["US"]["public_pending_signals"][0]["source_updated_at"], "2026-07-27T18:30:00-04:00")
        self.assertEqual(state["accounts"]["A"]["pending_signals"], [])

    @unittest.skipIf(
        not os.access("/root/.hermes", os.R_OK | os.W_OK),
        "requires local Hermes environment (/root/.hermes)",
    )
    def test_sync_public_snapshot_reads_sources_and_only_rewrites_public_export(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            export = root / "paper.json"
            source_a = root / "a.json"
            source_us = root / "us.json"
            state = paper.new_state("2026-07-28T00:00:00+00:00")
            state["accounts"]["A"]["pending_signals"] = [{"symbol": "OLD"}]
            export.write_text(json.dumps(paper.public_view(state)), encoding="utf-8")
            source_a.write_text(json.dumps({
                "date": "2026-07-28", "updated_at": "a-ts",
                "plant": [{"code": "560080", "name": "央企ETF", "status": "候场"}],
            }), encoding="utf-8")
            source_us.write_text(json.dumps({
                "date": "2026-07-27", "updated_at": "us-ts",
                "flower_signals": {"plant": [{"symbol": "IYT", "name": "IYT", "signal": "伏击触发"}]},
            }), encoding="utf-8")

            original_now_iso = getattr(paper, "now_iso")
            setattr(paper, "now_iso", lambda _value=None: "2026-07-28T23:00:00+00:00")
            try:
                paper.sync_public_snapshot(export, {"A": source_a, "US": source_us})
            finally:
                setattr(paper, "now_iso", original_now_iso)

            saved = json.loads(export.read_text(encoding="utf-8"))
            self.assertEqual(saved["updated_at"], "2026-07-28T23:00:00+00:00")
            self.assertEqual(saved["accounts"]["A"]["pending_signals"], [{"symbol": "OLD"}])
            self.assertEqual(saved["accounts"]["A"]["public_pending_signals"][0]["symbol"], "560080")
            self.assertEqual(saved["accounts"]["US"]["public_pending_signals"][0]["status"], "伏击")

    def test_sync_public_snapshot_uses_shared_publication_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            export = root / "paper.json"
            source_a = root / "a.json"
            source_us = root / "us.json"
            export.write_text(json.dumps(paper.public_view(paper.new_state())), encoding="utf-8")
            source_a.write_text(json.dumps({"date": "2026-07-28", "updated_at": "a", "plant": []}), encoding="utf-8")
            source_us.write_text(json.dumps({"date": "2026-07-27", "updated_at": "us", "flower_signals": {}}), encoding="utf-8")
            entered = []

            @contextmanager
            def guard():
                entered.append(True)
                yield

            with mock.patch.object(paper, "public_write_lock", guard):
                paper.sync_public_snapshot(export, {"A": source_a, "US": source_us})
            self.assertEqual(entered, [True])

    def test_sync_public_cli_needs_no_market_or_state(self):
        with mock.patch.object(paper, "sync_public_snapshot") as sync:
            paper.main(["--mode", "sync-public"])
        sync.assert_called_once_with()

    def test_init_export_preserves_current_public_projection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "state.json"
            export = root / "paper.json"
            source_a = root / "a.json"
            source_us = root / "us.json"
            source_a.write_text(json.dumps({
                "date": "2026-07-28", "updated_at": "a-ts",
                "plant": [{"code": "560080", "name": "央企ETF", "status": "候场"}],
            }), encoding="utf-8")
            source_us.write_text(json.dumps({
                "date": "2026-07-27", "updated_at": "us-ts",
                "flower_signals": {"ready_plant": [{"symbol": "EWZ", "name": "Brazil", "signal": "候场"}]},
            }), encoding="utf-8")
            with mock.patch.object(paper, "EXPORT", export), mock.patch.object(
                paper, "SOURCES", {"A": source_a, "US": source_us}
            ):
                paper.main(["--market", "A", "--mode", "init", "--state", str(state_path), "--now", "2026-07-28T00:00:00+00:00"])
            saved = json.loads(export.read_text(encoding="utf-8"))
            self.assertEqual([x["symbol"] for x in saved["accounts"]["A"]["public_pending_signals"]], ["560080"])
            self.assertEqual([x["symbol"] for x in saved["accounts"]["US"]["public_pending_signals"]], ["EWZ"])

    def test_init_source_failure_does_not_partially_write_private_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "state.json"
            export = root / "paper.json"
            source_a = root / "a.json"
            source_a.write_text(json.dumps({"date": "2026-07-28", "updated_at": "a-ts", "plant": []}), encoding="utf-8")
            with mock.patch.object(paper, "EXPORT", export), mock.patch.object(
                paper, "SOURCES", {"A": source_a, "US": root / "missing-us.json"}
            ):
                with self.assertRaises(FileNotFoundError):
                    paper.main(["--market", "A", "--mode", "init", "--state", str(state_path), "--now", "2026-07-28T00:00:00+00:00"])
            self.assertFalse(state_path.exists())
            self.assertFalse(export.exists())

    def test_paper_page_prefers_public_projection_with_legacy_fallback(self):
        page = (P.parents[1] / "src/pages/paper.astro").read_text(encoding="utf-8")
        self.assertIn("a.public_pending_signals ?? a.pending_signals ?? []", page)

    def test_us_history_and_paper_pages_cross_link_signal_and_trade_details(self):
        paper_page = (P.parents[1] / "src/pages/paper.astro").read_text(encoding="utf-8")
        history_page = (P.parents[1] / "src/pages/us-compass/history.astro").read_text(encoding="utf-8")
        for marker in ("成交时间", "成交价", "数量", "成交额", "卖出盈亏", "signalHref", "tradeAnchor"):
            self.assertIn(marker, paper_page)
        for marker in ("模拟盘实际成交", "成交明细", "tradesForSignal", "usSignalAnchor"):
            self.assertIn(marker, history_page)

    def test_market_windows_and_quote_freshness(self):
        self.assertTrue(paper.intraday_window("A", "2026-07-13T02:00:00+00:00"))  # 10:00 CST
        self.assertFalse(paper.intraday_window("A", "2026-07-13T04:00:00+00:00"))  # lunch
        self.assertTrue(paper.intraday_window("US", "2026-07-13T14:00:00+00:00"))  # 10:00 EDT
        self.assertFalse(paper.intraday_window("US", "2026-07-11T14:00:00+00:00"))  # weekend
        self.assertEqual(paper.quote_day("A", {"timestamp": "20260713100000"}), "2026-07-13")
        self.assertEqual(paper.quote_day("US", {"timestamp": 1783951200}), "2026-07-13")


if __name__ == "__main__":
    unittest.main()

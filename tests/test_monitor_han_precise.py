"""韩精准制导 · 巡检监控模块的回归测试（全 mock，无外部网络依赖）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import numpy as np
import pandas as pd
import pytest

import monitor_han_precise as monitor


def _create_test_bars(n: int = 150, base_price: float = 10.0, trend: float = 0.0) -> pd.DataFrame:
    closes = [base_price + i * trend for i in range(n)]
    return pd.DataFrame(
        {
            "date": [f"2026-01-{i + 1:03d}" for i in range(n)],
            "open": closes,
            "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes],
            "close": closes,
            "volume": [1000.0] * n,
        }
    )


def test_load_watchlist_custom_path(tmp_path: Path):
    w_file = tmp_path / "watchlist.json"
    w_file.write_text(
        json.dumps(
            {
                "symbols": [
                    {"symbol": "sh603997", "name": "继峰股份", "enabled": True},
                    {"symbol": "sz000001", "name": "平安银行", "enabled": False},
                ]
            }
        ),
        encoding="utf-8",
    )
    items = monitor.load_watchlist(w_file)
    assert len(items) == 1
    assert items[0]["symbol"] == "sh603997"


def test_load_and_save_state(tmp_path: Path):
    s_file = tmp_path / "state.json"
    assert monitor.load_state(s_file) == {}
    test_state = {"symbols": {"sh603997": {"background_id": 1}}}
    monitor.save_state(s_file, test_state)
    assert monitor.load_state(s_file) == test_state


def test_default_state_path_points_to_data_local():
    assert "data/local" in str(monitor.DEFAULT_STATE_PATH)


def test_evaluate_symbol_intraday_silent_when_no_change():
    bars = _create_test_bars(150, 10.0, 0.0)
    sym_info = {"symbol": "test", "name": "测试股", "enabled": True}
    first_state, _ = monitor.evaluate_symbol(sym_info, {}, mode="intraday", bars_override=bars)

    # 第二次在相同状态下巡检，intraday 必须静默（card is None）
    second_state, card = monitor.evaluate_symbol(
        sym_info, first_state, mode="intraday", bars_override=bars
    )
    assert card is None
    assert second_state["state_id"] == first_state["state_id"]


def test_evaluate_symbol_detects_state_change():
    bars_flat = _create_test_bars(150, 10.0, 0.0)
    sym_info = {"symbol": "test", "name": "测试股", "enabled": True}
    prev_state = {
        "symbol": "test",
        "state_id": 2,  # 之前是做空
        "background_id": 1,
        "signals_seen": [],
    }
    # 构造做多状态
    up_bars = _create_test_bars(135, 10.0, 0.0)
    extra_closes = [10.0 + (i + 1) * 0.5 for i in range(15)]
    extra_df = pd.DataFrame(
        {
            "date": [f"2026-02-{i + 1:02d}" for i in range(15)],
            "open": extra_closes,
            "high": [c * 1.01 for c in extra_closes],
            "low": [c * 0.99 for c in extra_closes],
            "close": extra_closes,
            "volume": [1000.0] * 15,
        }
    )
    bars_up = pd.concat([up_bars, extra_df], ignore_index=True)

    new_state, card = monitor.evaluate_symbol(
        sym_info, prev_state, mode="intraday", bars_override=bars_up
    )
    assert card is not None
    assert "状态切换" in card
    assert new_state["state_id"] == 1


def test_evaluate_symbol_close_mode_always_generates_card():
    bars = _create_test_bars(150, 10.0, 0.0)
    sym_info = {"symbol": "test", "name": "测试股", "enabled": True}
    _, card = monitor.evaluate_symbol(sym_info, {}, mode="close", bars_override=bars)
    assert card is not None
    assert "收盘定盘" in card


def test_run_monitor_end_to_end_mocked(tmp_path: Path, monkeypatch):
    w_file = tmp_path / "watchlist.json"
    s_file = tmp_path / "state.json"
    w_file.write_text(
        json.dumps(
            {
                "symbols": [
                    {"symbol": "sh603997", "name": "继峰股份", "enabled": True},
                ]
            }
        ),
        encoding="utf-8",
    )
    bars = _create_test_bars(150, 10.0, 0.0)
    monkeypatch.setattr(monitor.hp, "fetch_bars", lambda *a, **k: bars)

    # 首次执行（force 模式）产生卡片并写入初始状态
    count, cards = monitor.run_monitor(
        watchlist_path=w_file, state_path=s_file, mode="force"
    )
    assert count == 1
    assert s_file.exists()
    state = json.loads(s_file.read_text(encoding="utf-8"))
    assert "sh603997" in state["symbols"]

    # 第二次执行（intraday 模式），因为无异动，应静默
    count2, cards2 = monitor.run_monitor(
        watchlist_path=w_file, state_path=s_file, mode="intraday"
    )
    assert count2 == 0
    assert len(cards2) == 0

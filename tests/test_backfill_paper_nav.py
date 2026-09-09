import importlib.util
from pathlib import Path


def load_module():
    path = Path(__file__).parents[1] / "scripts/backfill_paper_nav.py"
    spec = importlib.util.spec_from_file_location("backfill_paper_nav", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def test_reconstruct_history_row_uses_events_and_closing_prices():
    module = load_module()
    account = {
        "initial_capital": 1000.0,
        "events": [
            {"timestamp": "2026-09-01T14:00:00+00:00", "symbol": "AAA", "side": "buy", "quantity": 2, "price": 100.0, "cost": 1.0},
            {"timestamp": "2026-09-03T14:00:00+00:00", "symbol": "AAA", "side": "sell", "quantity": 2, "price": 105.0, "cost": 1.0},
        ],
    }

    row = module.reconstruct_history_row(account, "2026-09-02", {"AAA": 103.0})

    assert row["cash"] == 799.0
    assert row["equity"] == 1005.0
    assert row["cumulative_return"] == 0.005


def test_insert_rows_recomputes_downstream_daily_returns():
    module = load_module()
    history = [
        {"date": "2026-09-01", "equity": 100.0, "cash": 100.0, "daily_return": 0.0, "cumulative_return": 0.0, "max_drawdown": -0.2},
        {"date": "2026-09-03", "equity": 121.0, "cash": 121.0, "daily_return": 0.21, "cumulative_return": 0.21, "max_drawdown": 0.0},
    ]

    rows = module.insert_rows(history, [{"date": "2026-09-02", "equity": 110.0, "cash": 110.0, "daily_return": 0.0, "cumulative_return": 0.1, "max_drawdown": 0.0}], 100.0)

    assert [row["date"] for row in rows] == ["2026-09-01", "2026-09-02", "2026-09-03"]
    assert rows[1]["daily_return"] == 0.1
    assert rows[2]["daily_return"] == 0.1
    assert rows[1]["max_drawdown"] == -0.2
    assert rows[2]["max_drawdown"] == -0.2
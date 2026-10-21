"""`symbols_needing_backfill` 结构性豁免的交易日口径回归。

背景（2026-10-21 夜间链 480s 超时）：`update_a_share_bar_cache.py --minimum-history 260`
每夜对 `count(distinct trade_date) < 260` 的标的做回填。560710 上市于 2026-02-06，
上市至今最多只有 166 个交易日，永远凑不满 260 根。若它每夜都被选中，回填阶段
就会一直重取同一批行，把夜间链的 480s 预算烧光 —— 这就是 21:20 阶段
`subprocess.TimeoutExpired` 的根因。

现有实现有两道闸：

1. **日历日界** `(today - listed_on).days < minimum` —— 永远安全（交易日不多于
   日历日），但对 560710 这类标的会在上市满 260 个日历日后失效（2026-10-24）。
2. **交易日界** `calendar[0] <= listed_on` 时，用「上市以来日历中的交易日数」
   `expected` 判断结构上是否可能达到 minimum。

第 2 道闸的条件曾写成 `listed_on <= calendar[0]`（方向反了）：日历起点是
2012-06-08，任何近年上市的标的都不满足，于是这道闸**从不生效**，只能靠第 1
道闸兜着。等 2026-10-24 第 1 道闸失效，560710 就会每夜重入回填。

契约：日历新鲜且覆盖标的完整生命期时，`expected < minimum` 必须豁免；
`expected >= minimum` 时必须重新进入回填（豁免不能变成永久放行）。
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from etf_bar_cache import connect, upsert_bars  # noqa: E402


def load_importer():
    spec = importlib.util.spec_from_file_location(
        "update_a_share_bar_cache_exemption", ROOT / "scripts" / "update_a_share_bar_cache.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _bar(symbol: str, market: str, day: str, close: float = 1.0) -> dict:
    return {
        "market": market, "symbol": symbol, "trade_date": day, "close": close,
        "adjustment": "qfq", "source": "tencent", "is_final": True,
    }


def _weekday_series(start: date, count: int) -> list[str]:
    """`count` 个工作日（当作交易日），从 start 起算。"""
    days: list[str] = []
    cursor = start
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return days


def test_structural_exemption_follows_trading_days_not_calendar_days(tmp_path):
    """上市以来交易日不足 minimum 的标的，日历新鲜时必须豁免。

    560710 上市 2026-02-06；到 2026-11-20 已满 287 个日历日，日历日界失效。
    日历里上市之后只有 200 个交易日 < 260，所以必须靠交易日界豁免。
    修复前 `listed_on <= calendar[0]` 恒为假，这道闸不生效。
    """
    importer = load_importer()
    listed = date(2026, 2, 6)
    # 日历整体 300 个交易日（够长，len(calendar) >= minimum 成立），
    # 但标的上市晚，上市之后只有 200 个交易日 < 260。
    full = _weekday_series(date(2025, 11, 3), 300)
    listed_day = full[100]
    days_after = [day for day in full if day >= listed_day]
    assert len(days_after) == 200
    with connect(tmp_path / "bars.db") as db:
        rows = [_bar("560710", "XSHG", listed_day, 0.99)]
        # 陪衬标的撑起完整交易日历，且让日历保持新鲜。
        rows += [_bar("600021", "XSHG", day, 19.5) for day in full]
        upsert_bars(db, rows)
        selected = importer.symbols_needing_backfill(
            db, [{"code": "560710", "market": "XSHG"}], minimum=260,
            today=(date.fromisoformat(full[-1]) + timedelta(days=1)).isoformat(),
        )
    assert selected == [], "上市以来交易日不足 minimum，不能每夜重入回填"


def test_structural_exemption_expires_once_enough_trading_days_exist(tmp_path):
    """豁免必须可过期：日历里的交易日够了以后，必须重新进入回填。"""
    importer = load_importer()
    listed = date(2026, 2, 6)
    days = _weekday_series(listed, 300)  # 上市后 300 个交易日 >= 260
    with connect(tmp_path / "bars.db") as db:
        rows = [_bar("560710", "XSHG", days[0], 0.99)]
        rows += [_bar("600021", "XSHG", day, 19.5) for day in days]
        upsert_bars(db, rows)
        selected = importer.symbols_needing_backfill(
            db, [{"code": "560710", "market": "XSHG"}], minimum=260,
            today=(date.fromisoformat(days[-1]) + timedelta(days=1)).isoformat(),
        )
    assert [item["code"] for item in selected] == ["560710"], "交易日够了必须重新回填"


def test_complete_symbol_is_exempt_even_with_enough_trading_days(tmp_path):
    """反向约束：日历够长但该标的每天都齐了，仍然豁免（不能变成永久重取）。"""
    importer = load_importer()
    listed = date(2026, 2, 6)
    days = _weekday_series(listed, 300)
    with connect(tmp_path / "bars.db") as db:
        rows = [_bar("560710", "XSHG", day, 1.0) for day in days]
        rows += [_bar("600021", "XSHG", day, 19.5) for day in days]
        upsert_bars(db, rows)
        selected = importer.symbols_needing_backfill(
            db, [{"code": "560710", "market": "XSHG"}], minimum=260,
            today=(date.fromisoformat(days[-1]) + timedelta(days=1)).isoformat(),
        )
    assert selected == []


def test_long_listed_thin_cache_is_still_repaired(tmp_path):
    """老标的缓存稀疏时必须被选中，不能被「年轻标的」豁免逻辑连带放过。"""
    importer = load_importer()
    days = _weekday_series(date(2026, 2, 6), 300)
    with connect(tmp_path / "bars.db") as db:
        rows = [_bar("600021", "XSHG", days[0], 19.5)]  # 老标的只有一根
        rows += [_bar("000001", "XSHE", day, 10.0) for day in days]
        upsert_bars(db, rows)
        selected = importer.symbols_needing_backfill(
            db, [{"code": "600021", "market": "XSHG"}], minimum=260,
            today=(date.fromisoformat(days[-1]) + timedelta(days=1)).isoformat(),
        )
    assert [item["code"] for item in selected] == ["600021"]


def test_production_cache_no_longer_selects_the_young_symbol():
    """生产缓存活体断言：560710 结构上凑不满 260 根，当前不应被选中。

    这条断言直接跑真实 `data/local/etf-compass.db`，是 2026-10-21 夜间链超时
    的原始现场。
    """
    importer = load_importer()
    db_path = ROOT / "data" / "local" / "etf-compass.db"
    if not db_path.exists():
        pytest.skip("production bar cache not present")
    import sqlite3

    db = sqlite3.connect(db_path)
    try:
        universe = [
            {"code": row[0], "market": row[1]}
            for row in db.execute("SELECT DISTINCT symbol, market FROM daily_bars")
        ]
        last = db.execute(
            "SELECT max(trade_date) FROM daily_bars WHERE adjustment='qfq' AND is_final=1"
        ).fetchone()[0]
        today = (date.fromisoformat(str(last)) + timedelta(days=1)).isoformat()
        selected = importer.symbols_needing_backfill(db, universe, minimum=260, today=today)
    finally:
        db.close()
    codes = [item["code"] for item in selected]
    assert "560710" not in codes, "560710 上市以来最多 166 个交易日，不能每夜重入回填"

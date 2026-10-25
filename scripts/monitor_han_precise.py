#!/usr/bin/env python3
"""韩精准制导 · 定时巡检与异动告警脚本。

用途：
- 对监控标的（默认载入 data/han_precise_watchlist.json）做周期性巡检。
- 检测四色背景切换、四象限状态切换、以及交易信号（买入、底部B、加仓、强势狙击、DX买等）。
- 状态记录于 data/han_precise_state.json，避免重复告警。
- 模式：
    --mode intraday: 盘中巡检（看门狗模式），仅在出现状态切换或新信号时输出告警卡片；无异动时静默无输出。
    --mode close:    15:05 收盘定盘，输出各标的的当日终局状态报告并持久化。
    --mode check:    只读自检，输出当前状态与信号，不更新状态文件。
    --mode force:    强制输出当前状态卡片并更新状态文件。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# 确保能加载当前工程内的 han_precise 模块
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import han_precise as hp  # noqa: E402

DEFAULT_WATCHLIST_PATH = PROJECT_ROOT / "data" / "han_precise_watchlist.json"
DEFAULT_STATE_PATH = PROJECT_ROOT / "data" / "local" / "han_precise_state.json"


def load_watchlist(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return [
            {
                "symbol": "sh603997",
                "name": "继峰股份",
                "market": "A",
                "enabled": True,
                "alert_on_state_change": True,
                "alert_on_signal": True,
            }
        ]
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return [s for s in data.get("symbols", []) if s.get("enabled", True)]


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        legacy = PROJECT_ROOT / "data" / "han_precise_state.json"
        if legacy != path and legacy.exists():
            try:
                with open(legacy, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(".tmp")
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    temp_path.replace(path)


def format_alert_card(
    symbol_info: dict[str, Any],
    res: hp.HanPreciseResult,
    reasons: list[str],
    is_close_summary: bool = False,
) -> str:
    name = symbol_info.get("name") or res.symbol
    sym = res.symbol
    f = res.fields
    bg_id, bg_name = res.background()
    st_id, st_name, st_color = res.state()

    def fmt(key: str, digits: int = 2, suffix: str = "") -> str:
        val = f.get(key)
        if val is None or (isinstance(val, float) and (val != val)):
            return "—"
        if isinstance(val, (int, float)):
            return f"{val:.{digits}f}{suffix}"
        return str(val)

    title = f"【韩精准制导 · 收盘定盘】{name} ({sym})" if is_close_summary else f"🔔【韩精准制导 · 信号异动】{name} ({sym})"

    lines = [
        title,
        f"• 日期：{f.get('date', '—')}  现价：{fmt('close')} 元 ({fmt('day_change_pct', 2, '%')})",
    ]

    if reasons and not is_close_summary:
        lines.append("• 异动触发：")
        for r in reasons:
            lines.append(f"  ⚡ {r}")

    ema_pt = f"EMA15支撑 {fmt('ema_support')}" if f.get("ema_support") is not None else f"EMA15阻力 {fmt('ema_resistance')}"
    lines.extend([
        f"• 背景环境：{bg_name}（MA89 {fmt('ma89')} / MA03 {fmt('ma03')}）",
        f"• 状态机：{st_name}（{st_color}） | K {fmt('k')}  D {fmt('d')}  MACD {fmt('macd')}",
        f"• 均线角度：BB05 {fmt('bb05')}° | BB10 {fmt('bb10')}° | BB30 {fmt('bb30')}°",
        f"• 关键点位：支撑 {fmt('support')} | 压力 {fmt('pressure')} | {ema_pt}",
        f"• 摆动指标：X_TREND {fmt('x_trend')} | GDX_QS {fmt('gdx_qs')} | DX {fmt('dx')}",
    ])

    recent = res.recent_signals(days=10)
    if recent:
        lines.append("• 近10日信号：")
        for sig in recent[-5:]:
            lines.append(f"  - {sig['date']} {sig['name']} @ {sig['close']}")
    else:
        lines.append("• 近10日信号：无")

    return "\n".join(lines)


def evaluate_symbol(
    symbol_info: dict[str, Any],
    prev_symbol_state: dict[str, Any],
    mode: str = "intraday",
    force: bool = False,
    bars_override: Any = None,
) -> tuple[dict[str, Any], str | None]:
    sym = symbol_info["symbol"]
    if bars_override is not None:
        bars = bars_override
    else:
        bars = hp.fetch_bars(sym, count=250)

    res = hp.compute(bars, symbol=sym)
    f = res.fields
    cur_date = str(f.get("date"))
    cur_bg_id = int(f.get("background_id", 0))
    cur_state_id = int(f.get("state_id", 0))

    prev_bg_id = prev_symbol_state.get("background_id")
    prev_state_id = prev_symbol_state.get("state_id")
    prev_signals_seen = set(prev_symbol_state.get("signals_seen", []))

    reasons: list[str] = []

    # 1. 检测四色背景变化
    if symbol_info.get("alert_on_state_change", True):
        if prev_bg_id is not None and prev_bg_id != cur_bg_id:
            old_name = hp.BACKGROUND_COLORS.get(prev_bg_id, ("未知", None))[0]
            new_name = hp.BACKGROUND_COLORS.get(cur_bg_id, ("未知", None))[0]
            reasons.append(f"背景切换：{old_name} ➔ {new_name}")

        # 2. 检测四象限状态变化
        if prev_state_id is not None and prev_state_id != cur_state_id:
            old_name, old_col = hp.STATE_COLORS.get(prev_state_id, ("未知", "无"))
            new_name, new_col = hp.STATE_COLORS.get(cur_state_id, ("未知", "无"))
            reasons.append(f"状态切换：{old_name}({old_col}) ➔ {new_name}({new_col})")

    # 3. 检测当根 K 线的交易信号
    current_bar_signals: list[str] = []
    signal_keys = [
        ("买入", "buy"),
        ("底部B", "x_bottom"),
        ("强势狙击", "sniper"),
        ("加仓", "add"),
        ("DX买", "dx_buy"),
        ("高抛低吸", "gdx_signal"),
        ("DX加1", "dx_add"),
        ("减仓", "reduce"),
        ("清仓", "clear"),
        ("DX卖", "dx_sell"),
    ]
    for sig_name, sig_field in signal_keys:
        if f.get(sig_field) is True:
            sig_id = f"{cur_date}:{sig_name}"
            current_bar_signals.append(sig_id)
            if symbol_info.get("alert_on_signal", True):
                if sig_id not in prev_signals_seen:
                    reasons.append(f"触发新信号：{sig_name} @ {f.get('close')}")

    # 更新状态字典（排序以保持稳定时间序）
    updated_signals_seen = sorted(prev_signals_seen.union(current_bar_signals))
    # 只保留最近 50 个已见信号ID避免膨胀
    if len(updated_signals_seen) > 50:
        updated_signals_seen = updated_signals_seen[-50:]

    new_symbol_state = {
        "symbol": sym,
        "name": symbol_info.get("name", sym),
        "last_date": cur_date,
        "last_close": float(f.get("close", 0.0)),
        "background_id": cur_bg_id,
        "background_name": res.background()[1],
        "state_id": cur_state_id,
        "state_name": res.state()[1],
        "signals_seen": updated_signals_seen,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    # 判定是否需要输出卡片
    card: str | None = None
    if mode == "close":
        card = format_alert_card(symbol_info, res, reasons, is_close_summary=True)
    elif mode in ("force", "check") or force or bool(reasons):
        card = format_alert_card(symbol_info, res, reasons, is_close_summary=False)

    return new_symbol_state, card


def run_monitor(
    watchlist_path: Path = DEFAULT_WATCHLIST_PATH,
    state_path: Path = DEFAULT_STATE_PATH,
    mode: str = "intraday",
    force: bool = False,
    single_symbol: str | None = None,
) -> tuple[int, list[str]]:
    watchlist = load_watchlist(watchlist_path)
    if single_symbol:
        watchlist = [s for s in watchlist if s["symbol"].lower() == single_symbol.lower()]
        if not watchlist:
            watchlist = [{"symbol": single_symbol, "name": single_symbol, "enabled": True}]

    state = load_state(state_path)
    symbols_state = state.get("symbols", {})

    cards: list[str] = []
    for s_info in watchlist:
        sym = s_info["symbol"]
        prev_sym_state = symbols_state.get(sym, {})
        try:
            new_sym_state, card = evaluate_symbol(s_info, prev_sym_state, mode=mode, force=force)
            if mode != "check":
                symbols_state[sym] = new_sym_state
            if card:
                cards.append(card)
        except Exception as e:
            err_msg = f"⚠️【韩精准制导 · 巡检异常】{s_info.get('name', sym)} ({sym}): {e}"
            cards.append(err_msg)

    if mode != "check":
        state["symbols"] = symbols_state
        state["last_run_at"] = datetime.now(timezone.utc).isoformat()
        state["last_mode"] = mode
        save_state(state_path, state)

    return len(cards), cards


def main() -> None:
    parser = argparse.ArgumentParser(description="韩精准制导 巡检与告警监控")
    parser.add_argument("--mode", choices=["intraday", "close", "check", "force"], default="intraday")
    parser.add_argument("--symbol", help="指定只巡检某一只标的")
    parser.add_argument("--watchlist", type=Path, default=DEFAULT_WATCHLIST_PATH)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE_PATH)
    parser.add_argument("--force", action="store_true", help="强制输出卡片并持久化状态")
    args = parser.parse_args()

    count, cards = run_monitor(
        watchlist_path=args.watchlist,
        state_path=args.state,
        mode=args.mode,
        force=args.force,
        single_symbol=args.symbol,
    )

    if cards:
        print("\n\n---\n\n".join(cards))
    else:
        # intraday 无异动时静默（看门狗模式）
        pass


if __name__ == "__main__":
    main()

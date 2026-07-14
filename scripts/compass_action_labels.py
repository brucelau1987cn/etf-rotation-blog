#!/usr/bin/env python3
"""Canonical ETF Compass action labels + compatibility helpers.

Public-facing A-share recommendation statuses use compass terms.
Legacy garden terms remain accepted as input aliases.
"""
from __future__ import annotations

from typing import Any

# Public status vocabulary (source of truth for JSON / pages / paper trade).
STATUS_WAIT = "候场"          # was 准备种花
STATUS_PLANT = "伏击"         # was 种花
STATUS_READY_HARVEST = "止盈观察"  # was 准备摘花
STATUS_HARVEST = "兑现"       # was 摘花
STATUS_EXIT = "破位撤退"      # was 失效退出

# Legacy garden terms kept for historical blogs / old JSON / paper migration.
LEGACY_TO_COMPASS = {
    "准备种花": STATUS_WAIT,
    "种花": STATUS_PLANT,
    "准备摘花": STATUS_READY_HARVEST,
    "摘花": STATUS_HARVEST,
    "失效退出": STATUS_EXIT,
    # Already-compass terms map to themselves.
    STATUS_WAIT: STATUS_WAIT,
    STATUS_PLANT: STATUS_PLANT,
    STATUS_READY_HARVEST: STATUS_READY_HARVEST,
    STATUS_HARVEST: STATUS_HARVEST,
    STATUS_EXIT: STATUS_EXIT,
}

BUY_STATUSES = {STATUS_PLANT, "种花"}
SELL_STATUSES = {STATUS_HARVEST, "摘花"}
WAIT_STATUSES = {STATUS_WAIT, "准备种花"}
READY_HARVEST_STATUSES = {STATUS_READY_HARVEST, "准备摘花"}
EXIT_STATUSES = {STATUS_EXIT, "失效退出", "破位撤退"}

STAGE_PREOPEN = "08:30盘前版"
STAGE_PREOPEN_LEGACY = "07:30早盘版"
STAGE_LABELS = {
    "08:30": STAGE_PREOPEN,
    "07:30": STAGE_PREOPEN,  # migrate legacy writes to the new label
    "11:30": "11:30上午收盘修正",
    "14:30": "14:30尾盘操作",
    "22:00": "22:00夜间最终版",
}


def normalize_status(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return LEGACY_TO_COMPASS.get(text, text)


def is_buy_status(value: Any) -> bool:
    return normalize_status(value) == STATUS_PLANT or str(value).strip() in BUY_STATUSES


def is_sell_status(value: Any) -> bool:
    return normalize_status(value) == STATUS_HARVEST or str(value).strip() in SELL_STATUSES


def is_wait_status(value: Any) -> bool:
    return normalize_status(value) == STATUS_WAIT or str(value).strip() in WAIT_STATUSES


def normalize_stage(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if "07:30" in text or text == STAGE_PREOPEN_LEGACY:
        return STAGE_PREOPEN
    if "08:30" in text and "盘前" not in text and "早盘" not in text:
        return STAGE_PREOPEN
    if text in {STAGE_PREOPEN, STAGE_PREOPEN_LEGACY}:
        return STAGE_PREOPEN
    return text


def rewrite_garden_terms(text: str) -> str:
    """Rewrite residual garden wording in prose fields."""
    if not text:
        return text
    out = text
    # Longer phrases first.
    for old, new in (
        ("准备种花", STATUS_WAIT),
        ("准备摘花", STATUS_READY_HARVEST),
        ("失效退出", STATUS_EXIT),
        ("07:30早盘版", STAGE_PREOPEN),
        ("07:30准备信号", "08:30准备信号"),
        ("07:30 早盘预测", "08:30 盘前预测"),
        ("07:30早盘预测", "08:30盘前预测"),
        ("ETF花园", "ETF罗盘"),
    ):
        out = out.replace(old, new)
    # Bare single terms after multi-char replacements.
    out = out.replace("种花", STATUS_PLANT).replace("摘花", STATUS_HARVEST)
    return out


def levels_valid(price: Any, support: Any, target: Any, stop: Any) -> tuple[bool, str]:
    """Reject impossible key levels that would poison action cards."""
    try:
        px = float(price) if price is not None else None
        sup = float(support) if support is not None else None
        tgt = float(target) if target is not None else None
        st = float(stop) if stop is not None else None
    except (TypeError, ValueError):
        return False, "non-numeric levels"
    if px is None or px <= 0:
        return False, "invalid price"
    if any(v is None for v in (sup, tgt, st)):
        return False, "missing levels"
    assert sup is not None and tgt is not None and st is not None
    if st <= 0 or sup <= 0 or tgt <= 0:
        return False, "non-positive level"
    if st >= px:
        # Stop should sit below current price for long-only compass actions.
        # Allow tiny float noise only when stop is still below support.
        if st >= sup:
            return False, "stop above support/price"
    if sup > 0 and abs(tgt / sup - 1) > 0.45:
        return False, "target too far from support"
    if px > 0 and abs(tgt / px - 1) > 0.35:
        return False, "target distance >35%"
    if px > 0 and abs(sup / px - 1) > 0.20:
        return False, "support distance >20%"
    if tgt <= st:
        return False, "target <= stop"
    return True, "ok"

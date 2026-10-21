"""阈值变体快照成员必须继续被追踪池枚举到。

背景（2026-10-21 实测）：同日阈值收窄后，canonical `YYYY-MM-DD.json` 会被新阈值
全集整体替换，旧阈值的成员只剩在 `YYYY-MM-DD.threshold-X.json` 里。
`load_history_dates()` 只 glob canonical 快照，于是这些成员虽仍在
`low-chip-tracking.json::stocks` 中，却永远不会再被主循环遍历回填 ——
表现为追踪池里一条"孤儿"记录停在加入日一根 bar（实测 002705.SZ 只有
2026-09-03 一根，而腾讯/BaoStock 都能取到 29 根）。

契约：变体快照的成员同样计入 `load_history_dates()`，日期取文件名前 10 位；
`resolve_entry_snapshot()` 仍能从变体文件取到不可变的入池证据。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load():
    scripts_dir = str(ROOT / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location(
        "update_low_chip_tracking_variants", ROOT / "scripts/update_low_chip_tracking.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _snapshot(intersection: list[str], enrichments: dict) -> str:
    return json.dumps(
        {"data_as_of": "2026-09-03", "intersection": intersection, "enrichments": enrichments},
        ensure_ascii=False,
    )


def test_load_history_dates_includes_threshold_variant_members(tmp_path, monkeypatch):
    mod = _load()
    (tmp_path / "2026-09-03.json").write_text(
        _snapshot(["000012.SZ"], {"000012.SZ": {"industry": "玻璃玻纤"}}), encoding="utf-8"
    )
    (tmp_path / "2026-09-03.threshold-2.5.json").write_text(
        _snapshot(["000012.SZ", "002705.SZ"], {"002705.SZ": {"industry": "小家电"}}), encoding="utf-8"
    )
    monkeypatch.setattr(mod, "HISTORY_DIR", tmp_path)

    seen = mod.load_history_dates()

    assert "002705.SZ" in seen, "变体快照成员必须计入追踪枚举，否则永远轮不到回填"
    assert seen["002705.SZ"] == ["2026-09-03"], "变体日期必须取文件名前 10 位，不能带 .threshold 后缀"
    assert seen["000012.SZ"] == ["2026-09-03"], "同一只股票跨 canonical/变体不能重复计日"


def test_variant_only_symbol_resolves_entry_snapshot(tmp_path, monkeypatch):
    mod = _load()
    (tmp_path / "2026-09-03.json").write_text(
        _snapshot(["000012.SZ"], {"000012.SZ": {"industry": "玻璃玻纤"}}), encoding="utf-8"
    )
    (tmp_path / "2026-09-03.threshold-2.5.json").write_text(
        _snapshot(["002705.SZ"], {"002705.SZ": {"industry": "小家电", "name": "新宝股份"}}), encoding="utf-8"
    )
    monkeypatch.setattr(mod, "HISTORY_DIR", tmp_path)

    entry = mod.resolve_entry_snapshot(tmp_path, "2026-09-03", "002705.SZ")

    assert entry.name == "2026-09-03.threshold-2.5.json"
    enrich, _raw = mod.load_entry_enrichment(entry, "002705.SZ")
    assert enrich["industry"] == "小家电"


def test_every_tracked_stock_is_still_enumerated():
    """生产数据的活体断言：tracking 里的每只股票都必须能被 history 枚举到。

    这正是 2026-10-21 漏掉的那只孤儿（002705.SZ）：留在 stocks 里却不在
    `load_history_dates()` 的结果中，主循环永不遍历它 → K 线永不回填。
    """
    mod = _load()
    seen = mod.load_history_dates()
    existing = mod.load_existing()
    stocks = existing.get("stocks") or {}
    assert stocks, "tracking pool should not be empty"
    orphans = sorted(sym for sym in stocks if sym not in seen)
    assert orphans == [], f"tracking 里存在永远不会被回填的孤儿记录: {orphans}"

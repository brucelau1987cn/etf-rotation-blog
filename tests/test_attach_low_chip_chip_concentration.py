"""90% 筹码集中度（集中度90）契约测试。

覆盖：批量解析、单批失败隔离、全失败降级、空池、阈值边界、
页面/D1/sync 字段成套接线。全程不联网：fetch 全部注入。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import attach_low_chip_chip_concentration as mod  # noqa: E402

PAGE = ROOT / "src/pages/rolling/low-chip.astro"
WORKER = ROOT / "functions/api/public/v1/low-chip-metrics.js"
SYNC = ROOT / "scripts/sync_low_chip_to_d1.py"


def _payload(codes=("600000.SH", "000001.SZ")):
    return {
        "data_as_of": "2026-11-12",
        "intersection": list(codes),
        "enrichments": {code: {"industry": "银行"} for code in codes},
    }


def test_value_parses_dated_field():
    assert mod._value({"集中度90[20261112]": 9.7}) == 9.7
    assert mod._value({"集中度90[20261112]": "8.4"}) == 8.4
    assert mod._value({"集中度90[20261112]": None}) is None
    assert mod._value({"其他字段[20261112]": 1.0}) is None


def test_attach_writes_ok_and_contract():
    payload = _payload()

    def fake_fetch(codes, compact_date):
        assert compact_date == "20261112"
        return {"600000.SH": 9.7, "000001.SZ": 12.3}

    contract = mod.attach(payload, "2026-11-12", fetch=fake_fetch)
    assert payload["enrichments"]["600000.SH"]["chip_concentration"] == {
        "status": "ok",
        "concentration_90": 9.7,
        "as_of": "2026-11-12",
        "source": mod.SOURCE,
        "reason": None,
    }
    assert payload["enrichments"]["000001.SZ"]["chip_concentration"]["concentration_90"] == 12.3
    assert contract["status"] == "ok"
    assert contract["coverage"] == {"requested": 2, "ok": 2, "unavailable": 0}
    assert contract["threshold"] == 10.0
    assert contract["failed_batches"] == []
    # 既有 enrichment 字段不被覆盖
    assert payload["enrichments"]["600000.SH"]["industry"] == "银行"


def test_missing_symbol_is_unavailable_not_zero():
    payload = _payload()

    def fake_fetch(codes, compact_date):
        return {"600000.SH": 9.7}

    contract = mod.attach(payload, "2026-11-12", fetch=fake_fetch)
    missing = payload["enrichments"]["000001.SZ"]["chip_concentration"]
    assert missing["status"] == "UNAVAILABLE"
    assert missing["concentration_90"] is None
    assert missing["reason"]
    assert contract["status"] == "ok"
    assert contract["coverage"] == {"requested": 2, "ok": 1, "unavailable": 1}


def test_batch_failure_is_isolated():
    # 两批：第一批 40 只成功，第二批（含 000002）整批失败
    codes = tuple(f"{600000 + i}.SH" for i in range(mod.BATCH_SIZE)) + ("000002.SZ",)
    payload = _payload(codes)

    def fake_fetch(batch, compact_date):
        if "000002.SZ" in batch:
            raise RuntimeError("upstream 502")
        return {code: 9.0 for code in batch}

    contract = mod.attach(payload, "2026-11-12", fetch=fake_fetch)
    assert contract["status"] == "ok"
    assert contract["coverage"]["ok"] == mod.BATCH_SIZE
    assert contract["coverage"]["unavailable"] == 1
    assert len(contract["failed_batches"]) == 1
    assert "upstream 502" in contract["failed_batches"][0]
    assert payload["enrichments"]["000002.SZ"]["chip_concentration"]["status"] == "UNAVAILABLE"


def test_all_batches_failing_degrades_contract():
    payload = _payload()

    def fake_fetch(codes, compact_date):
        raise SystemExit("iWenCai error: quota")

    contract = mod.attach(payload, "2026-11-12", fetch=fake_fetch)
    assert contract["status"] == "unavailable"
    assert contract["coverage"]["ok"] == 0
    assert all(
        payload["enrichments"][code]["chip_concentration"]["status"] == "UNAVAILABLE"
        for code in payload["intersection"]
    )


def test_empty_pool_short_circuits():
    payload = {"data_as_of": "2026-11-12", "intersection": [], "enrichments": {}}

    def fake_fetch(codes, compact_date):  # pragma: no cover - must not run
        raise AssertionError("fetch must not be called for an empty pool")

    contract = mod.attach(payload, "2026-11-12", fetch=fake_fetch)
    assert contract["status"] == "empty_pool"
    assert contract["coverage"]["requested"] == 0


def test_batches_are_chunked():
    codes = tuple(f"{600000 + i}.SH" for i in range(mod.BATCH_SIZE + 5))
    payload = _payload(codes)
    seen = []

    def fake_fetch(batch, compact_date):
        seen.append(len(batch))
        return {code: 9.0 for code in batch}

    mod.attach(payload, "2026-11-12", fetch=fake_fetch)
    assert seen == [mod.BATCH_SIZE, 5]


def test_page_wires_concentration_filter_and_metric():
    page = PAGE.read_text(encoding="utf-8")
    for marker in (
        'data-filter="conc90"',
        "集中度90 &lt; 10%",
        "chipConcentration: enr.chip_concentration || null",
        "data-conc90={row.chipConcentration?.concentration_90 ?? ''}",
        "chipConcentration: row.chip_concentration || row.chipConcentration || null",
        "chipConcentration: parseJsonSafe(row.chip_concentration)",
        "conc90Html(conc)",
        "activeFilters.has('conc90')",
        "Number(c.dataset.conc90) >= 10",
    ):
        assert marker in page, marker


def test_page_filter_threshold_is_strict_less_than_ten():
    """开关语义是「< 10%」：等于 10 不命中，缺失也不命中。"""
    page = PAGE.read_text(encoding="utf-8")
    line = next(l for l in page.splitlines() if "activeFilters.has('conc90')" in l)
    assert ">= 10" in line
    assert "c.dataset.conc90 === ''" in line


def test_d1_worker_and_sync_carry_chip_concentration():
    worker = WORKER.read_text(encoding="utf-8")
    sync = SYNC.read_text(encoding="utf-8")
    assert "'chip_concentration'" in worker
    assert "ADD COLUMN chip_concentration TEXT" in worker
    assert "metric.chip_concentration = parseJsonField(metric.chip_concentration, null)" in worker
    assert "'chip_concentration': enr.get('chip_concentration')," in sync


def test_chip_concentration_only_channel_updates_single_column():
    worker = WORKER.read_text(encoding="utf-8")
    sync = SYNC.read_text(encoding="utf-8")
    assert "const chipConcentrationOnly = preserveExisting && body?.chip_concentration_only === true;" in worker
    assert "chipConcentrationOnly ? ['chip_concentration'] : [" in worker
    assert 'parser.add_argument("--chip-concentration-only"' in sync
    assert 'body["chip_concentration_only"] = True' in sync
    assert "chip_concentration_only=chip_concentration_only" in sync


def test_query_codes_keeps_exchange_suffix():
    """裸 6 位代码批量查询会被 iWenCai 误识别成基金（2026-07-31 实测
    20 只裸代码只返回 3 条 .SZ，其余变 .OF），必须带 .SZ/.SH 后缀。"""
    assert mod._query_codes(["000931.SZ", "600605.SH"]) == "000931.SZ、600605.SH"


def test_fetch_batch_splits_on_silent_empty_envelope(monkeypatch):
    """iWenCai 负载时返回合法的空信封（code_count=0/has_more=false）。整批静默
    变空必须靠对半拆批识别，不能当成「上游没有数据」。"""
    calls = []
    good = {"000931.SZ": 14.4, "600605.SH": 18.8, "603033.SH": 21.2, "688091.SH": 25.1}

    def fake_paginate(query):
        codes = [part for part in query.split(" ")[0].split("、") if part]
        calls.append(len(codes))
        if len(codes) >= 4:
            return ([], 0)  # 空信封
        return ([{"股票代码": c, "集中度90[20260731]": good[c]} for c in codes], len(codes))

    monkeypatch.setattr(mod.base, "paginate", fake_paginate)
    out = mod.fetch_batch(list(good), "20260731")
    assert out == good
    assert calls[0] == 4 and len(calls) > 1


def test_fetch_batch_gives_up_after_depth_limit(monkeypatch):
    def fake_paginate(query):
        return ([], 0)

    monkeypatch.setattr(mod.base, "paginate", fake_paginate)
    out = mod.fetch_batch(["000931.SZ", "600605.SH"], "20260731")
    assert out == {}


def test_worker_js_parses():
    """字符串断言全过但文件语法坏了（少一个 `};`）会让 Pages Functions 构建失败，
    而本地 astro build 不检查 functions/。必须真跑一次 node --check。"""
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        import pytest

        pytest.skip("node not available")
    result = subprocess.run([node, "--check", str(WORKER)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[-2000:]


def test_worker_cols_and_row_values_stay_aligned():
    """cols 与 rowValues 必须尾部成对追加，错位不会报错但每列都写错。"""
    worker = WORKER.read_text(encoding="utf-8")
    cols_block = worker.split("const cols = [", 1)[1].split("];", 1)[0]
    base_cols = [c.strip() for c in cols_block.split(",") if c.strip()]
    push_line = worker.split("cols.push(", 1)[1].split(");", 1)[0]
    pushed = [c.strip().strip("'\"") for c in push_line.split(",") if c.strip()]
    total_cols = len(base_cols) + len(pushed)

    rv_block = worker.split("const rowValues = (m) => [", 1)[1].split("\n    ];", 1)[0]
    depth = 0
    exprs = 1
    for ch in rv_block:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "," and depth == 0:
            exprs += 1
    if rv_block.rstrip().endswith(","):
        exprs -= 1  # 尾随逗号不是表达式
    assert total_cols == exprs, f"cols={total_cols} rowValues={exprs}"
    assert total_cols <= 50, "2 rows per statement must stay under D1's 100-parameter cap"

"""90% 筹码集中度（集中度90）契约测试。

覆盖：批量解析、单批失败隔离、全失败降级、空池、阈值边界、
页面/D1/sync 字段成套接线。全程使用注入响应或本地替身，不联网。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

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


def _fake_clock(monkeypatch):
    clock = SimpleNamespace(now=0.0)

    def sleep(seconds):
        clock.now += seconds

    monkeypatch.setattr(mod, "time", SimpleNamespace(monotonic=lambda: clock.now, sleep=sleep))
    return clock


def _response(codes, *, total=None, has_more=False, date="20261112"):
    rows = [{"股票代码": code, f"集中度90[{date}]": 9.7} for code in codes]
    return _envelope(rows, total=total, has_more=has_more)


def _envelope(rows, *, total=None, has_more=False):
    return SimpleNamespace(returncode=0, stdout=json.dumps({
        "code_count": len(rows) if total is None else total, "has_more": has_more, "datas": rows,
    }), stderr="")


def _query(args):
    query = args[args.index("-q") + 1]
    return query.split(" ")[0].split("、"), int(args[args.index("--page") + 1])


def test_deadline_preserves_successful_split_sibling(monkeypatch):
    clock = _fake_clock(monkeypatch)
    codes = [f"{600000 + i}.SH" for i in range(5)]
    payload = _payload(codes)
    monkeypatch.setattr(mod, "BATCH_SIZE", 4)
    calls = []

    def fake_run(args, timeout):
        queried, page = _query(args)
        calls.append((queried, page, timeout))
        if queried == codes[:4]:
            return _response([])
        if queried == codes[:2]:
            clock.now += 2
            return _response(queried)
        if queried == codes[2:4]:
            assert timeout <= 3
            clock.now += timeout
            raise mod.subprocess.TimeoutExpired(args, timeout)
        raise AssertionError("budget exhaustion must skip the next batch")

    monkeypatch.setattr(mod, "_run_query", fake_run)
    contract = mod.attach(payload, "2026-11-12", budget_s=5)
    assert [call[:2] for call in calls] == [(codes[:4], 1), (codes[:2], 1), (codes[2:4], 1)]
    assert contract["coverage"] == {"requested": 5, "ok": 2, "unavailable": 3}
    assert contract["status"] == "ok"
    assert contract["stop_reason"] == "budget_exhausted"
    for code in codes[:2]:
        assert payload["enrichments"][code]["chip_concentration"]["concentration_90"] == 9.7
    for code in codes[2:]:
        item = payload["enrichments"][code]["chip_concentration"]
        assert (item["status"], item["concentration_90"]) == ("UNAVAILABLE", None)
        assert "预算" in item["reason"]


def test_value_parses_dated_field():
    assert mod._value({"集中度90[20261112]": 9.7}, "20261112") == 9.7
    assert mod._value({"集中度90[20261112]": "8.4"}, "20261112") == 8.4
    assert mod._value({"其他字段[20261112]": 1.0}, "20261112") is None


@pytest.mark.parametrize("value", [None, True, "NaN", "Infinity", "-0.1", 101, "-", {}])
def test_invalid_value_is_unknown(value):
    assert mod._value({"集中度90[20261112]": value}, "20261112") is None


def test_transport_backoff_shares_deadline(monkeypatch):
    clock = _fake_clock(monkeypatch)
    payload = _payload()
    attempts = []

    def unavailable(args, timeout):
        attempts.append(timeout)
        return SimpleNamespace(returncode=1, stdout="", stderr="upstream 502")

    monkeypatch.setattr(mod, "_run_query", unavailable)
    contract = mod.attach(payload, "2026-11-12", budget_s=5)
    assert attempts == [5, 2]
    assert clock.now == 3
    assert contract["status"] == "unavailable"
    assert contract["stop_reason"] == "budget_exhausted"


def test_default_budget_caps_each_request_and_preserves_completed_batches(monkeypatch):
    clock = _fake_clock(monkeypatch)
    codes = [f"{600000 + i}.SH" for i in range(1023)]
    payload = _payload(codes)
    timeouts = []

    def simulate_slow_queries(args, timeout):
        timeouts.append(timeout)
        clock.now += 29
        return _response(_query(args)[0])

    def fake_run(args, timeout):
        if timeout >= 29:
            return simulate_slow_queries(args, timeout)
        timeouts.append(timeout)
        clock.now += timeout
        raise mod.subprocess.TimeoutExpired(args, timeout)

    monkeypatch.setattr(mod, "_run_query", fake_run)
    contract = mod.attach(payload, "2026-11-12")
    assert timeouts == [30] * 18 + [18]
    assert clock.now == 540 < 600
    assert contract["coverage"] == {"requested": 1023, "ok": 720, "unavailable": 303}
    assert contract["stop_reason"] == "budget_exhausted"


def test_zero_budget_clears_stale_overlay_without_querying(monkeypatch):
    payload = _payload()
    first = payload["intersection"][0]
    payload["enrichments"][first]["chip_concentration"] = {
        "status": "ok", "concentration_90": 7.0, "as_of": "2026-11-11",
    }
    calls = []

    def fake_run(args, timeout):
        calls.append(args)
        return _response([])

    monkeypatch.setattr(mod, "_run_query", fake_run)
    contract = mod.attach(payload, "2026-11-12", budget_s=0)
    assert calls == []
    assert contract["coverage"] == {"requested": 2, "ok": 0, "unavailable": 2}
    assert contract["stop_reason"] == "budget_exhausted"
    assert payload["enrichments"][first]["chip_concentration"] == {
        "status": "UNAVAILABLE", "concentration_90": None, "as_of": "2026-11-12",
        "source": mod.SOURCE, "reason": mod.BUDGET_REASON,
    }


def test_invalid_later_page_preserves_validated_earlier_page(monkeypatch):
    clock = _fake_clock(monkeypatch)
    payload = _payload()
    first, second = payload["intersection"]
    pages = []

    def fake_run(args, timeout):
        _, page = _query(args)
        pages.append(page)
        if page == 1:
            return _response([first], total=2, has_more=True)
        return _response([second], total=3)  # total changed, reject page

    monkeypatch.setattr(mod, "_run_query", fake_run)
    contract = mod.attach(payload, "2026-11-12", budget_s=5)
    assert clock.now == 3
    assert pages == [1, 2, 2]
    assert contract["coverage"] == {"requested": 2, "ok": 1, "unavailable": 1}
    assert contract["stop_reason"] == "budget_exhausted"
    assert payload["enrichments"][first]["chip_concentration"]["concentration_90"] == 9.7
    assert payload["enrichments"][second]["chip_concentration"]["concentration_90"] is None


def test_page_integrity_retry_recovers_on_third_attempt(monkeypatch):
    clock = _fake_clock(monkeypatch)
    payload = _payload()
    calls = []

    def fake_run(args, timeout):
        codes, page = _query(args)
        calls.append(page)
        if len(calls) == 1:
            return SimpleNamespace(returncode=0, stdout="{", stderr="")
        if len(calls) == 2:
            return _response([codes[0], codes[0]])
        return _response(codes)

    monkeypatch.setattr(mod, "_run_query", fake_run)
    contract = mod.attach(payload, "2026-11-12", budget_s=10)
    assert calls == [1, 1, 1]
    assert clock.now == 9
    assert contract["coverage"] == {"requested": 2, "ok": 2, "unavailable": 0}
    assert contract["failed_batches"] == []


def test_failed_split_subtree_does_not_skip_sibling(monkeypatch):
    payload = _payload()
    first, second = payload["intersection"]
    calls = []

    def fake_run(args, timeout):
        codes, _ = _query(args)
        calls.append(codes)
        if len(codes) == 2:
            return _response([])
        if codes == [first]:
            return SimpleNamespace(returncode=1, stdout="", stderr="403 private provider metadata")
        return _response(codes)

    monkeypatch.setattr(mod, "_run_query", fake_run)
    contract = mod.attach(payload, "2026-11-12")
    assert calls == [[first, second], [first], [second]]
    assert contract["coverage"] == {"requested": 2, "ok": 1, "unavailable": 1}
    assert contract["stop_reason"] is None
    assert len(contract["failed_batches"]) == 1
    assert "鉴权" in payload["enrichments"][first]["chip_concentration"]["reason"]
    assert "private provider metadata" not in json.dumps(payload)


def test_short_batch_only_requeries_missing_requested_codes(monkeypatch):
    payload = _payload(("600000.SH", "600001.SH", "600002.SH", "600003.SH"))
    valid, stale, absent, zero = payload["intersection"]
    calls = []
    original_fetch = mod.base.fetch

    def fake_run(args, timeout):
        codes, _ = _query(args)
        calls.append(codes)
        if len(calls) == 1:
            return _envelope([
                {"股票代码": valid, "集中度90[20261111]": 99, "集中度90[20261112]": 9.7},
                {"股票代码": stale, "集中度90[20261111]": 5},
                {"股票代码": zero, "集中度90[20261112]": "0"},
                {"股票代码": "699999.SH", "集中度90[20261112]": 5},
            ])
        return _response([])

    monkeypatch.setattr(mod, "_run_query", fake_run)
    contract = mod.attach(payload, "2026-11-12")
    assert calls == [payload["intersection"], [stale], [absent]]
    assert mod.base.fetch is original_fetch
    assert contract["coverage"] == {"requested": 4, "ok": 2, "unavailable": 2}
    assert contract["stop_reason"] is None
    assert payload["enrichments"][valid]["chip_concentration"]["concentration_90"] == 9.7
    assert payload["enrichments"][zero]["chip_concentration"]["concentration_90"] == 0
    assert payload["enrichments"][stale]["chip_concentration"]["concentration_90"] is None
    assert "699999.SH" not in payload["enrichments"]


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
    assert "chipConcentrationOnly ? ['chip_concentration'] :" in worker
    assert 'parser.add_argument("--chip-concentration-only"' in sync
    assert 'body["chip_concentration_only"] = True' in sync
    assert "chip_concentration_only=chip_concentration_only" in sync


def test_query_codes_keeps_exchange_suffix():
    """裸 6 位代码批量查询会被 iWenCai 误识别成基金（2026-07-31 实测
    20 只裸代码只返回 3 条 .SZ，其余变 .OF），必须带 .SZ/.SH 后缀。"""
    assert mod._query_codes(["000931.SZ", "600605.SH"]) == "000931.SZ、600605.SH"


def test_attach_splits_on_silent_empty_envelope(monkeypatch):
    """iWenCai 负载时返回合法的空信封（code_count=0/has_more=false）。整批静默
    变空必须靠对半拆批识别，不能当成「上游没有数据」。"""
    calls = []
    good = {"000931.SZ": 14.4, "600605.SH": 18.8, "603033.SH": 21.2, "688091.SH": 25.1}

    def fake_run(args, timeout):
        codes, _ = _query(args)
        calls.append(len(codes))
        if len(codes) >= 4:
            return _response([])
        return _envelope([{"股票代码": c, "集中度90[20260731]": good[c]} for c in codes])

    monkeypatch.setattr(mod, "_run_query", fake_run)
    payload = _payload(good)
    contract = mod.attach(payload, "2026-07-31")
    assert contract["coverage"]["ok"] == len(good)
    assert {code: row["chip_concentration"]["concentration_90"]
            for code, row in payload["enrichments"].items()} == good
    assert calls == [4, 2, 2]


def test_attach_gives_up_after_depth_limit(monkeypatch):
    calls = []

    def empty(args, timeout):
        calls.append(_query(args))
        return _response([])

    monkeypatch.setattr(mod, "_run_query", empty)
    payload = _payload([f"{600000 + i}.SH" for i in range(40)])
    contract = mod.attach(payload, "2026-07-31")
    assert len(calls) == 31
    assert contract["coverage"] == {"requested": 40, "ok": 0, "unavailable": 40}
    assert contract["stop_reason"] is None


def test_main_at_1023_scale_writes_partial_and_kills_hung_descendant(monkeypatch, tmp_path, capsys):
    codes = [f"{600000 + i}.SH" for i in range(1023)]
    path = tmp_path / "staged.json"
    payload = _payload(codes)
    path.write_text(json.dumps(payload), encoding="utf-8")
    pid_file = tmp_path / "hung.pid"
    scratch_file = tmp_path / "query-scratch.path"
    wrapper = tmp_path / "local-query"
    wrapper.write_text(
        f"#!{Path(sys.executable).resolve()}\n"
        "import json, os, sys, time\n"
        "from pathlib import Path\n"
        "query = sys.argv[sys.argv.index('-q') + 1]\n"
        "codes, field = query.split(' ')\n"
        "codes = codes.split('、')\n"
        "if len(codes) == 40:\n"
        "    print(json.dumps(dict(code_count=0, has_more=False, datas=[])))\n"
        "elif codes[0] == '600000.SH':\n"
        "    rows = [{'股票代码': code, field: 9.7} for code in codes]\n"
        "    print(json.dumps(dict(code_count=len(rows), has_more=False, datas=rows)))\n"
        "elif codes[0] == '600020.SH':\n"
        "    if os.fork() == 0:\n"
        "        scratch = Path(os.environ['TMPDIR'])\n"
        "        (scratch / 'wrapper.tmp').write_text('unflushed response')\n"
        f"        Path({str(scratch_file)!r}).write_text(str(scratch))\n"
        f"        Path({str(pid_file)!r}).write_text(str(os.getpid()))\n"
        "        time.sleep(30)\n"
        "    os._exit(0)\n"
        "else:\n"
        "    raise AssertionError('new batch started after deadline')\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o700)
    monkeypatch.setattr(mod, "QUERY_COMMAND", str(wrapper))
    monkeypatch.setattr(mod.base, "DATA", path)
    monkeypatch.setattr(sys, "argv", ["attach_low_chip_chip_concentration.py", "--query-budget-seconds", "1.5"])

    started = time.monotonic()
    assert mod.main() == 0
    assert time.monotonic() - started < 5
    result = json.loads(path.read_text(encoding="utf-8"))
    contract = result.pop("chip_concentration_contract")
    assert contract["coverage"] == {"requested": 1023, "ok": 20, "unavailable": 1003}
    assert contract["stop_reason"] == "budget_exhausted"
    assert "pool=1023 ok=20 unavailable=1003" in capsys.readouterr().out
    for index, code in enumerate(codes):
        item = result["enrichments"][code].pop("chip_concentration")
        assert item["concentration_90"] == (9.7 if index < 20 else None)
        assert item["status"] == ("ok" if index < 20 else "UNAVAILABLE")
        assert item["as_of"] == "2026-11-12"
        assert item["reason"] == (None if index < 20 else mod.BUDGET_REASON)
    assert result == payload  # all other data and pool order are unchanged
    assert not list(tmp_path.glob(".staged.json.*"))
    assert not Path(scratch_file.read_text()).exists()

    # The wrapper exited earlier; the descendant alone held stdout/stderr open.
    # A killed orphan may be a zombie briefly before the system reaps it.
    status_path = Path("/proc") / pid_file.read_text() / "status"
    for _ in range(20):
        try:
            if "\nState:\tZ" in status_path.read_text():
                break
        except FileNotFoundError:
            break
        time.sleep(0.05)
    else:
        pytest.fail("query descendant survived the hard timeout")


def test_atomic_replace_failure_preserves_original_bytes(monkeypatch, tmp_path):
    path = tmp_path / "staged.json"
    original = b'{"original":true}\n'
    path.write_bytes(original)

    def fail_replace(source, destination):
        assert source.parent == destination.parent == tmp_path
        assert json.loads(source.read_bytes()) == {"updated": True}
        raise OSError("replace failed")

    monkeypatch.setattr(mod.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        mod._write_atomic(path, {"updated": True})
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]


def test_worker_js_parses():
    """字符串断言全过但文件语法坏了（少一个 `};`）会让 Pages Functions 构建失败，
    而本地 astro build 不检查 functions/。必须真跑一次 node --check。"""
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
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
    # D1 单语句上限 100 个绑定参数；列数超过 50 后 worker 会把每语句行数降到 1，
    # 所以这里校验的是「列数 × 行数」而不是写死 50。
    rows_per_stmt = max(1, min(2, 100 // total_cols))
    assert total_cols * rows_per_stmt <= 100, f"cols={total_cols} exceeds the 100-parameter cap"

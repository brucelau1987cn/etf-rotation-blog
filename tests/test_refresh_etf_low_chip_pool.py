import importlib.util
from pathlib import Path
from datetime import datetime, timezone, timedelta

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "refresh_etf_low_chip_pool", ROOT / "scripts" / "refresh_etf_low_chip_pool.py"
)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

CN = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 24, 15, 0, tzinfo=CN)


def period_row(code, name, label, value, date="20261023"):
    return {"基金代码": code, "基金简称": name, "最新收盘价": "1.0", "最新涨跌幅": 0.5,
            "etf类型二级分类": "宽基指数ETF", f"{label}线收盘获利[{date}]": value}


def liq_row(code, name, scale_yuan, amount_yuan):
    return {"基金代码": code, "基金简称": name, "基金规模[20261024]": scale_yuan,
            "成交额平均值[20260924-20261023]": amount_yuan, "etf类型二级分类": "宽基指数ETF"}


def fake_call_factory(period_data, liquidity=None):
    """period_data: {'week'|'month'|'quarter': [rows]}; liquidity: {code: row or None}."""
    liquidity = liquidity or {}

    def call(query, page=1, limit=100):
        if "收盘获利" in query:
            for key, label in mod.PERIODS:
                if f"{label}线" in query:
                    rows = period_data.get(key, [])
                    return {"success": True, "code_count": 1308, "has_more": False,
                            "datas": rows if page == 1 else []}
        if query == "股票ETF":
            return {"success": True, "code_count": 1308, "has_more": False, "datas": []}
        # per-code liquidity lookup: query starts with the full suffixed code
        head = query.replace("，", " ").split()[0]
        if "." in head and head[:6].isdigit():
            row = liquidity.get(head)
            return {"success": True, "code_count": 1 if row else 0, "has_more": False,
                    "datas": [row] if row else []}
        raise AssertionError(f"unexpected query {query!r}")
    return call


def test_strict_threshold_excludes_exact_1_5_percent():
    period = {"week": [period_row("111111.SZ", "A", "周", 0.015)], "month": [], "quarter": []}
    call = fake_call_factory(period, {"111111.SZ": liq_row("111111.SZ", "A", 2e8, 3e7)})
    payload = mod.build_payload(call=call, now=NOW)
    assert payload["counts"]["period_hits"]["week"] == 0
    assert payload["rows"] == []


def test_liquidity_floor_and_triple_hit():
    period = {
        "week": [period_row("222222.SZ", "B", "周", 0.01), period_row("333333.SZ", "C", "周", 0.005)],
        "month": [period_row("222222.SZ", "B", "月", 0.012), period_row("333333.SZ", "C", "月", 0.009)],
        "quarter": [period_row("222222.SZ", "B", "季", 0.004)],
    }
    liquidity = {
        "222222.SZ": liq_row("222222.SZ", "B", 2e8, 3e7),  # passes
        "333333.SZ": liq_row("333333.SZ", "C", 5e7, 3e7),  # fails scale (<1亿)
    }
    payload = mod.build_payload(call=fake_call_factory(period, liquidity), now=NOW)
    assert [r["code"] for r in payload["rows"]] == ["222222.SZ"]
    row = payload["rows"][0]
    assert row["periods_hit"] == ["week", "month", "quarter"]
    assert payload["counts"]["pool_triple"] == 1
    assert payload["data_as_of"] == "2026-10-23"


def test_amount_floor_is_enforced():
    period = {"week": [period_row("777777.SZ", "G", "周", 0.01)], "month": [], "quarter": []}
    liquidity = {"777777.SZ": liq_row("777777.SZ", "G", 2e8, 5e6)}  # 500万 < 1000万
    payload = mod.build_payload(call=fake_call_factory(period, liquidity), now=NOW)
    assert payload["rows"] == []
    assert payload["liquidity_unknown"] == []


def test_missing_liquidity_is_listed_unknown_not_silently_dropped():
    period = {"week": [period_row("444444.SZ", "D", "周", 0.01)], "month": [], "quarter": []}
    payload = mod.build_payload(call=fake_call_factory(period, {"444444.SZ": None}), now=NOW)
    assert payload["liquidity_unknown"] == ["444444.SZ"]
    assert payload["liquidity_source"]["444444.SZ"] == "missing"
    assert payload["rows"] == []


def test_every_candidate_uses_per_code_lookup_not_batch_universe():
    period = {"week": [period_row("555555.SZ", "E", "周", 0.01)], "month": [], "quarter": []}
    calls = []

    def call(query, page=1, limit=100):
        calls.append(query)
        return fake_call_factory(period, {"555555.SZ": liq_row("555555.SZ", "E", 3e8, 2e7)})(query, page, limit)

    payload = mod.build_payload(call=call, now=NOW)
    assert payload["liquidity_source"]["555555.SZ"] == "per_code"
    assert [r["code"] for r in payload["rows"]] == ["555555.SZ"]
    assert not any(q.startswith("股票ETF 基金规模") for q in calls)


def test_mixed_chip_dates_fail_closed():
    period = {"week": [period_row("666666.SZ", "F", "周", 0.01, date="20261023")],
              "month": [period_row("666666.SZ", "F", "月", 0.01, date="20261022")],
              "quarter": []}
    raised = False
    try:
        mod.build_payload(call=fake_call_factory(period, {"666666.SZ": liq_row("666666.SZ", "F", 2e8, 3e7)}), now=NOW)
    except RuntimeError:
        raised = True
    assert raised


def test_iwencai_failure_propagates():
    def bad_call(query, page=1, limit=100):
        raise mod.IwencaiError("boom")
    raised = False
    try:
        mod.build_payload(call=bad_call, now=NOW)
    except mod.IwencaiError:
        raised = True
    assert raised


def test_transient_upstream_error_is_retried_then_succeeds(monkeypatch):
    import subprocess as sp
    calls = []
    ok_payload = '{"success": true, "code_count": 1, "has_more": false, "datas": []}'
    bad_payload = '{"status_code": -1304, "status_msg": "外部HTTP服务异常"}'

    def fake_run(argv, **kwargs):
        calls.append(argv)
        out = bad_payload if len(calls) < 3 else ok_payload
        return sp.CompletedProcess(argv, 0, stdout=out, stderr="")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    data = mod.iwencai_call("563960.SH 基金规模")
    assert data["success"] is True
    assert len(calls) == 3


def test_quota_refusal_is_not_retried(monkeypatch):
    import subprocess as sp
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return sp.CompletedProcess(argv, 1, stdout="您今天的使用次数已达上限，明天重置后再来试试吧。", stderr="")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    raised = False
    try:
        mod.iwencai_call("159288.SZ 基金规模")
    except mod.IwencaiError:
        raised = True
    assert raised
    assert len(calls) == 1


def test_nested_field_schema_is_accepted():
    row = {"基金代码": "510670.SH", "基金@基金规模[20261022]": 163000000.0,
           "基金@区间日均成交额[20260928-20261023]": 4500000.0}
    assert mod.scale_of(row) == 163000000.0
    assert mod.amount_avg_of(row) == 4500000.0


def test_flat_field_schema_is_accepted():
    row = {"基金规模[20261024]": 1e9, "成交额平均值[20260924-20261023]": 2e7}
    assert mod.scale_of(row) == 1e9
    assert mod.amount_avg_of(row) == 2e7


def test_universe_size_comes_from_single_unpaginated_call():
    period = {"week": [period_row("999999.SZ", "H", "周", 0.02)], "month": [], "quarter": []}
    seen = []

    def call(query, page=1, limit=100):
        seen.append(query)
        return fake_call_factory(period, {"999999.SZ": liq_row("999999.SZ", "H", 2e8, 3e7)})(query, page, limit)

    payload = mod.build_payload(call=call, now=NOW)
    assert payload["counts"]["universe_etf"] == 1308
    assert seen.count("股票ETF") == 1


def test_read_timeout_is_transient_and_retried(monkeypatch):
    import subprocess as sp
    calls = []
    ok_payload = '{"success": true, "code_count": 1, "has_more": false, "datas": []}'
    timeout_payload = '{"error": "发生错误: The read operation timed out"}'

    def fake_run(argv, **kwargs):
        calls.append(argv)
        out = timeout_payload if len(calls) < 2 else ok_payload
        return sp.CompletedProcess(argv, 0, stdout=out, stderr="")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    assert mod.iwencai_call("560930.SH，基金规模")["success"] is True
    assert len(calls) == 2


def test_liquidity_cache_resumes_after_failure_and_never_caches_errors(tmp_path):
    period = {"week": [period_row("888888.SZ", "I", "周", 0.01)], "month": [], "quarter": []}
    cache_path = tmp_path / "liq.json"
    good = liq_row("888888.SZ", "I", 2e8, 3e7)
    attempts = {"n": 0}

    def flaky(query, page=1, limit=100):
        if query.startswith("888888.SZ"):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise mod.IwencaiError("transient")
            return {"success": True, "code_count": 1, "has_more": False, "datas": [good]}
        return fake_call_factory(period)(query, page, limit)

    raised = False
    try:
        mod.build_payload(call=flaky, now=NOW, cache_path=cache_path)
    except mod.IwencaiError:
        raised = True
    assert raised
    assert not cache_path.exists() or "888888.SZ" not in cache_path.read_text(encoding="utf-8")

    payload = mod.build_payload(call=flaky, now=NOW, cache_path=cache_path)
    assert [r["code"] for r in payload["rows"]] == ["888888.SZ"]
    assert attempts["n"] == 2
    # third run hits the cache, not iWenCai, for the liquidity lookup
    before = attempts["n"]
    mod.build_payload(call=flaky, now=NOW, cache_path=cache_path)
    assert attempts["n"] == before

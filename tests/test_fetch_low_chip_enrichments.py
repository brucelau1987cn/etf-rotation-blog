"""Network-isolated regression tests for optional low-chip enrichment failures."""
import importlib.util
import json
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def sandbox(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location(
        "fetch_low_chip_optional_test", ROOT / "scripts/fetch_low_chip_enrichments.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    data = tmp_path / "input.json"
    data.write_text(json.dumps({"intersection": ["600001.SH", "600002.SH"]}))
    monkeypatch.setattr(mod, "DATA", data)

    def local_path(value):
        value = str(value)
        if value == "/tmp":
            return tmp_path
        if value.startswith("/tmp/"):
            return tmp_path / Path(value).name
        raise AssertionError(f"unexpected filesystem access: {value}")

    monkeypatch.setattr(mod, "Path", local_path)
    monkeypatch.setattr(mod, "fetch_ftshare_holders", lambda codes: {})
    # Any missed mock fails immediately, before network access or credential reads.
    def blocked(*args, **kwargs):
        pytest.fail("unmocked subprocess or network access")
    monkeypatch.setattr(mod.subprocess, "run", blocked)
    monkeypatch.setattr(mod.subprocess, "Popen", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    return mod, tmp_path


def test_industry_network_exit_degrades_and_keeps_ftshare_evidence(sandbox, monkeypatch, capsys):
    mod, directory = sandbox
    calls = []
    evidence = {"前十大流通股东名称(报告期)[20260630]": "全国社保基金一一八组合"}
    monkeypatch.setattr(mod, "fetch_ftshare_holders", lambda codes: {"600001": evidence})

    def query(q, **kwargs):
        calls.append(q)
        if "所属申万行业、所属同花顺行业、股票简称" in q:
            raise SystemExit("iWenCai error after retries: SSL handshake timeout")
        if "最新价、最新涨跌幅、所属申万行业、所属概念" in q:
            return {"datas": [{"股票代码": "600001.SH"}, {"股票代码": "600002.SH"}]}
        return {"datas": []}

    monkeypatch.setattr(mod, "iwc", query)
    assert mod.main() == 0
    rows = json.loads((directory / "low_chip_individual.json").read_text())
    assert [row["股票代码"] for row in rows] == ["600001.SH", "600002.SH"]
    assert rows[0][next(iter(evidence))] == next(iter(evidence.values()))
    status = json.loads((directory / "low_chip_enrichment_status.json").read_text())
    assert status["status"] == "degraded"
    assert status["availability"] == "UNAVAILABLE"
    assert status["coverage"]["industry"] == 0
    assert status["coverage"]["top10_shareholders"] == 1
    assert rows[1]["_enrichment_availability"]["top10_shareholders"] == "UNAVAILABLE"
    assert len(calls) == 3  # Two batch calls and one failed optional call; circuit opens.
    assert "UNAVAILABLE" in capsys.readouterr().out


def test_optional_empty_results_have_a_run_wide_call_budget(sandbox, monkeypatch):
    mod, directory = sandbox
    codes = [f"{600001 + i}.SH" for i in range(20)]
    mod.DATA.write_text(json.dumps({"intersection": codes}))
    optional_calls = []

    def query(q, **kwargs):
        if "所属概念" in q:
            return {"datas": [{"股票代码": code} for code in codes]}
        optional_calls.append(q)
        return {"datas": []}

    monkeypatch.setattr(mod, "iwc", query)
    assert mod.main() == 0
    assert len(optional_calls) <= 12
    status = json.loads((directory / "low_chip_enrichment_status.json").read_text())
    assert any("budget" in failure["error"] for failure in status["failures"])


def test_iwc_single_attempt_has_a_subprocess_deadline(sandbox, monkeypatch):
    mod, _ = sandbox
    calls = []
    sleeps = []

    deadlines = []

    def popen(command, **kwargs):
        calls.append(kwargs)
        def communicate(timeout=None):
            deadlines.append(timeout)
            return "", "SSL handshake timeout"
        return SimpleNamespace(args=command, returncode=1, communicate=communicate)

    monkeypatch.setattr(mod.subprocess, "Popen", popen)
    monkeypatch.setattr(mod.time, "sleep", sleeps.append)
    with pytest.raises(SystemExit, match="SSL handshake timeout"):
        mod.iwc("optional query", timeout=20, attempts=1)
    assert len(calls) == 1
    assert calls[0]["start_new_session"] is True
    assert deadlines == [25]
    assert sleeps == []


@pytest.mark.parametrize("stage", ["detail", "period", "names", "quality"])
@pytest.mark.parametrize("error_type", [SystemExit, OSError])
def test_each_optional_failure_is_unavailable(sandbox, monkeypatch, stage, error_type):
    mod, directory = sandbox
    calls = []
    tokens = {
        "detail": "最新完整报告期十大流通股东明细、前十大流通股东名称、公告日期",
        "period": "前十大流通股东报告期、截止日期",
        "names": "600001 前十大流通股东名称",
        "quality": mod.QUALITY_QUERY,
    }

    def query(q, **kwargs):
        calls.append((q, kwargs))
        if "所属概念" in q:
            return {"datas": [
                {"股票代码": code, "所属申万行业": "银行--股份制银行Ⅱ"}
                for code in ("600001.SH", "600002.SH")
            ]}
        if tokens[stage] in q:
            raise error_type("SSL handshake timeout")
        return {"datas": []}

    monkeypatch.setattr(mod, "iwc", query)
    assert mod.main() == 0
    status = json.loads((directory / "low_chip_enrichment_status.json").read_text())
    assert status["status"] == "degraded"
    assert "SSL handshake timeout" in status["failures"][0]["error"]
    assert status["coverage"]["industry"] == 2
    assert status["quality_query"] == "UNAVAILABLE"
    assert json.loads((directory / "low_chip_quality.json").read_text())["availability"] == "UNAVAILABLE"
    for q, kwargs in calls[2:]:
        assert kwargs["attempts"] == 1
        assert kwargs["timeout"] == 20


@pytest.mark.parametrize("stage", ["profile", "individual"])
def test_batch_failure_remains_strict(sandbox, monkeypatch, stage):
    mod, directory = sandbox

    def query(q, **kwargs):
        individual = "最新价、最新涨跌幅、所属申万行业、所属概念" in q
        if individual == (stage == "individual"):
            raise SystemExit("batch unavailable")
        return {"datas": []}

    monkeypatch.setattr(mod, "iwc", query)
    with pytest.raises(SystemExit, match="batch unavailable"):
        mod.main()
    assert not (directory / "low_chip_individual.json").exists()


def test_missing_report_period_is_explicitly_unavailable(sandbox, monkeypatch, capsys):
    mod, directory = sandbox

    def query(q, **kwargs):
        if "所属概念" in q:
            return {"datas": [{"股票代码": "600001.SH", "所属申万行业": "银行"}]}
        if q == "600001 前十大流通股东名称":
            return {"datas": [{"股票代码": "600001.SH", "名称": "全国社保基金一一八组合"}]}
        return {"datas": []}

    monkeypatch.setattr(mod, "iwc", query)
    assert mod.main() == 0
    status = json.loads((directory / "low_chip_enrichment_status.json").read_text())
    assert status["coverage"]["top10_shareholders"] == 0
    output = capsys.readouterr().out
    assert "defaults to false" not in output
    assert "badges default to false" not in output
    assert "UNAVAILABLE" in output


def test_optional_process_timeout_opens_circuit(sandbox, monkeypatch):
    mod, directory = sandbox

    def query(q, **kwargs):
        if "所属概念" in q:
            return {"datas": []}
        raise mod.subprocess.TimeoutExpired("iwencai-market-query", 25)

    monkeypatch.setattr(mod, "iwc", query)
    assert mod.main() == 0
    status = json.loads((directory / "low_chip_enrichment_status.json").read_text())
    assert len(status["failures"]) == 1
    assert "timed out" in status["failures"][0]["error"]
    assert status["coverage"] == {"industry": 0, "top10_shareholders": 0}


@pytest.mark.parametrize("stage", ["industry", "quality"])
@pytest.mark.parametrize("invalid_payload", [
    None,
    "service unavailable",
    {"text_response": "查询失败"},
    {"text_response": "查询失败", "datas": []},
    {"success": False, "datas": []},
    {},
    {"datas": None},
    {"datas": {"股票代码": "600001.SH"}},
    {"datas": [None]},
])
def test_optional_invalid_payload_opens_circuit(sandbox, monkeypatch, stage, invalid_payload):
    mod, directory = sandbox
    optional_calls = []

    def query(q, **kwargs):
        if "所属概念" in q:
            return {"datas": [
                {"股票代码": code,
                 "所属申万行业": "银行" if stage == "quality" else "",
                 "前十大流通股东名称(报告期)[20260630]": "全国社保基金一一八组合"}
                for code in ("600001.SH", "600002.SH")
            ]}
        optional_calls.append(q)
        return invalid_payload

    monkeypatch.setattr(mod, "iwc", query)
    assert mod.main() == 0
    status = json.loads((directory / "low_chip_enrichment_status.json").read_text())
    quality = json.loads((directory / "low_chip_quality.json").read_text())
    assert len(optional_calls) == 1
    assert len(status["failures"]) == 1
    assert "payload" in status["failures"][0]["error"]
    assert status["status"] == "degraded"
    assert status["quality_query"] == "UNAVAILABLE"
    assert quality["availability"] == "UNAVAILABLE"
    assert quality["datas"] == []


def test_valid_empty_quality_payload_stays_available(sandbox, monkeypatch):
    mod, directory = sandbox

    def query(q, **kwargs):
        if "所属概念" in q:
            return {"datas": [
                {"股票代码": code, "所属申万行业": "银行",
                 "前十大流通股东名称[20260630]": "全国社保基金一一八组合"}
                for code in ("600001.SH", "600002.SH")
            ]}
        return {"success": True, "datas": []}

    monkeypatch.setattr(mod, "iwc", query)
    assert mod.main() == 0
    status = json.loads((directory / "low_chip_enrichment_status.json").read_text())
    assert status["quality_query"] == "AVAILABLE"
    assert status["failures"] == []


@pytest.mark.parametrize("source", ["individual", "detail", "ftshare"])
@pytest.mark.parametrize("field,available", [
    ("前十大流通股东名称", False),
    ("前十大流通股东名称(报告期)", False),
    ("前十大流通股东名称[20260630]", True),
    ("前十大流通股东名称(报告期)[20260630]", True),
])
def test_shareholder_sources_require_dated_names(sandbox, monkeypatch, source, field, available):
    mod, directory = sandbox
    mod.DATA.write_text(json.dumps({"intersection": ["600001.SH"]}))
    evidence = {field: "全国社保基金一一八组合"}
    optional_queries = []
    if source == "ftshare":
        monkeypatch.setattr(mod, "fetch_ftshare_holders", lambda codes: {"600001": evidence})

    def query(q, **kwargs):
        if "所属概念" in q:
            row = {"股票代码": "600001.SH", "所属申万行业": "银行"}
            if source == "individual":
                row.update(evidence)
            return {"datas": [row]}
        optional_queries.append(q)
        if source == "detail" and "最新完整报告期十大流通股东明细、前十大流通股东名称、公告日期" in q:
            return {"datas": [{"股票代码": "600001.SH", **evidence}]}
        return {"datas": []}

    monkeypatch.setattr(mod, "iwc", query)
    assert mod.main() == 0
    rows = json.loads((directory / "low_chip_individual.json").read_text())
    status = json.loads((directory / "low_chip_enrichment_status.json").read_text())
    assert rows[0]["_enrichment_availability"]["top10_shareholders"] == (
        "AVAILABLE" if available else "UNAVAILABLE"
    )
    assert status["coverage"]["top10_shareholders"] == int(available)
    if not available:
        assert any("最新完整报告期十大流通股东明细" in q for q in optional_queries)
        assert any("前十大流通股东报告期、截止日期" in q for q in optional_queries)


def test_complete_evidence_stays_available_without_mutating_input(sandbox, monkeypatch):
    mod, directory = sandbox
    source = mod.DATA.read_text()
    evidence = [
        {"股票代码": code, "所属申万行业": "银行",
         "前十大流通股东名称(报告期)[20260630]": "全国社保基金一一八组合"}
        for code in ("600001.SH", "600002.SH")
    ]
    monkeypatch.setattr(mod, "iwc", lambda q, **kwargs: {"datas": evidence})
    assert mod.main() == 0
    assert mod.DATA.read_text() == source
    status = json.loads((directory / "low_chip_enrichment_status.json").read_text())
    assert status["status"] == "ok"
    assert status["availability"] == "AVAILABLE"
    assert status["coverage"] == {"industry": 2, "top10_shareholders": 2}
    assert status["failures"] == []

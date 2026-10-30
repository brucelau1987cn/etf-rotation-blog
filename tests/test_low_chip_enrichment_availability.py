"""Availability propagation using in-memory inputs and intercepted writes only."""
import copy
import importlib.util
import json
import socket
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "enrich_availability_test", ROOT / "scripts/enrich_low_chip_stocks.py"
)
assert SPEC and SPEC.loader
enrich = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(enrich)


@pytest.fixture
def run_enrichment(monkeypatch):
    def forbidden_network(*args, **kwargs):
        raise AssertionError("network access forbidden")

    monkeypatch.setattr(socket, "socket", forbidden_network)

    def run(rows, *, batch=None, source_status=None):
        data = Path("/mock/a-low-chip-stocks.json")
        individual = Path("/mock/individual.json")
        profile = Path("/mock/profile.json")
        codes = [row["股票代码"] for row in rows]
        unlock_cache = Path("/mock/unlock.json")
        monkeypatch.setenv("LOW_CHIP_RUN_ID", "availability-test")
        monkeypatch.setenv("LOW_CHIP_UNLOCK_CACHE", str(unlock_cache))
        inputs = {
            data: {"data_as_of": "2026-10-30", "intersection_before_filters": codes, "periods": {}},
            unlock_cache: {
                "status": "complete", "run_id": "availability-test", "data_as_of": "2026-10-30",
                "window_start": "2026-10-30", "window_end": "2027-01-30",
                "requested_codes": codes, "datas": [],
                "provider_response": {"success": True, "result": {
                    "pages": 1, "count": 1, "data": [{
                        "SECURITY_CODE": "603999", "FREE_DATE": "2026-11-01",
                        "TOTAL_RATIO": 0.1, "FREE_RATIO": 0.2,
                    }],
                }},
            },
            individual: rows,
            profile: {"datas": batch or []},
        }
        if source_status is not None:
            inputs[Path("/tmp/low_chip_enrichment_status.json")] = source_status
        monkeypatch.setattr(enrich, "DATA", data)
        monkeypatch.setattr(enrich, "INDIVIDUAL", individual)
        monkeypatch.setattr(enrich, "PROFILE_FILES", [profile])
        monkeypatch.setattr(enrich, "load", lambda path: copy.deepcopy(inputs[path]))
        monkeypatch.setattr(Path, "exists", lambda path: path in inputs)
        writes = []

        def capture_write(path, text, **kwargs):
            assert path == data, "only the mocked payload may be written"
            writes.append(json.loads(text))
            return len(text)

        monkeypatch.setattr(Path, "write_text", capture_write)
        enrich.main()
        assert len(writes) == 1
        return writes[0]

    return run


def test_unavailable_shareholder_evidence_stays_unknown_in_final_payload(run_enrichment):
    result = run_enrichment([{
        "股票代码": "600001.SH",
        "_enrichment_availability": {
            "industry": "UNAVAILABLE", "top10_shareholders": "UNAVAILABLE",
        },
    }])
    row = result["enrichments"]["600001.SH"]
    assert row["quality_shareholder"] is None
    assert row["institutional_shareholder"] is None
    assert row["enrichment_availability"] == {
        "industry": "UNAVAILABLE", "top10_shareholders": "UNAVAILABLE",
    }
    assert row["enrichment_status"] == "degraded"
    assert result["intersection"] == ["600001.SH"]
    assert result["enrichment_status"]["coverage"] == {
        "industry": 0, "top10_shareholders": 0,
    }


def test_preserves_upstream_degradation_and_failure_details(run_enrichment):
    source = {
        "status": "degraded", "availability": "UNAVAILABLE",
        "total": 1, "coverage": {"industry": 1, "top10_shareholders": 1},
        "unavailable": {"industry": [], "top10_shareholders": []},
        "failures": ["optional quality query quota exhausted"],
        "quality_query": "UNAVAILABLE",
    }
    result = run_enrichment([{
        "股票代码": "600001.SH", "所属申万行业": "银行",
        "前十大流通股东名称(报告期)[20260630]": "自然人甲",
        "_enrichment_availability": {
            "industry": "AVAILABLE", "top10_shareholders": "AVAILABLE",
        },
    }], source_status=source)
    assert result["enrichment_status"] == source


@pytest.mark.parametrize("marker", [None, "AVAILABLE", "UNAVAILABLE"])
def test_empty_evidence_stays_unknown_even_with_available_marker(run_enrichment, marker):
    row: dict = {"股票代码": "600001.SH"}
    if marker:
        row["_enrichment_availability"] = {
            "industry": marker, "top10_shareholders": marker,
        }
    result = run_enrichment([row])
    enrichment = result["enrichments"]["600001.SH"]
    assert enrichment["quality_shareholder"] is None
    assert enrichment["institutional_shareholder"] is None
    assert enrichment["enrichment_status"] == "degraded"


@pytest.mark.parametrize("names,quality,institutional", [
    ("自然人甲, 自然人乙", False, False),
    ("全国社保基金一一八组合", True, False),
    ("香港中央结算有限公司", False, True),
    ("全国社保基金一一八组合, 香港中央结算有限公司", True, True),
])
def test_legacy_evidence_retains_boolean_classification(
    run_enrichment, names, quality, institutional, capsys,
):
    result = run_enrichment([{
        "股票代码": "600001.SH", "所属申万行业": "银行",
        "前十大流通股东名称(报告期)[20260630]": names,
    }])
    enrichment = result["enrichments"]["600001.SH"]
    assert enrichment["quality_shareholder"] is quality
    assert enrichment["institutional_shareholder"] is institutional
    assert enrichment["enrichment_availability"] == {
        "industry": "AVAILABLE", "top10_shareholders": "AVAILABLE",
    }
    assert result["enrichment_status"]["status"] == "ok"
    assert json.loads(capsys.readouterr().out)["quality_count"] == int(quality)


def test_upstream_unavailable_vetoes_stale_batch_shareholder_names(run_enrichment):
    result = run_enrichment([{
        "股票代码": "600001.SH",
        "_enrichment_availability": {
            "industry": "AVAILABLE", "top10_shareholders": "UNAVAILABLE",
        },
    }], batch=[{
        "股票代码": "600001.SH", "所属申万行业": "银行",
        "前十大流通股东名称(报告期)[20260331]": "全国社保基金一一八组合",
    }])
    enrichment = result["enrichments"]["600001.SH"]
    assert enrichment["quality_shareholder"] is None
    assert enrichment["institutional_shareholder"] is None
    assert enrichment["quality_shareholder_names"] == []
    assert enrichment["enrichment_availability"]["top10_shareholders"] == "UNAVAILABLE"


@pytest.mark.parametrize("retained_field,updated_field", [
    ("top10_shareholders", "industry"),
    ("industry", "top10_shareholders"),
])
def test_partial_availability_preserves_uncovered_batch_unavailable(
    run_enrichment, retained_field, updated_field,
):
    result = run_enrichment([{
        "股票代码": "600001.SH",
        "_enrichment_availability": {updated_field: "AVAILABLE"},
    }], batch=[{
        "股票代码": "600001.SH", "所属申万行业": "银行",
        "前十大流通股东名称(报告期)[20260630]": "全国社保基金一一八组合",
        "_enrichment_availability": {
            retained_field: "UNAVAILABLE", updated_field: "UNAVAILABLE",
        },
    }])
    row = result["enrichments"]["600001.SH"]
    assert row["enrichment_availability"] == {
        retained_field: "UNAVAILABLE", updated_field: "AVAILABLE",
    }
    assert row["enrichment_status"] == "degraded"
    assert result["enrichment_status"]["coverage"][retained_field] == 0
    if retained_field == "top10_shareholders":
        assert row["quality_shareholder"] is None
        assert row["quality_shareholder_names"] == []


@pytest.mark.parametrize("status", [None, "ok"])
def test_upstream_availability_alone_preserves_degradation(run_enrichment, status):
    source = {"availability": "UNAVAILABLE", "failures": ["quality query unavailable"]}
    if status is not None:
        source["status"] = status
    result = run_enrichment([{
        "股票代码": "600001.SH", "所属申万行业": "银行",
        "前十大流通股东名称(报告期)[20260630]": "自然人甲",
    }], source_status=source)
    assert result["enrichment_status"]["status"] == "degraded"
    assert result["enrichment_status"]["availability"] == "UNAVAILABLE"
    assert result["enrichment_status"]["failures"] == source["failures"]
    assert result["enrichment_status"]["coverage"] == {
        "industry": 1, "top10_shareholders": 1,
    }


def test_mixed_coverage_is_recomputed_from_final_rows(run_enrichment):
    result = run_enrichment([
        {"股票代码": "600001.SH", "所属申万行业": "银行"},
        {"股票代码": "000002.SZ",
         "前十大流通股东名称(报告期)[20260630]": "自然人甲"},
    ], source_status={
        "status": "ok", "total": 999,
        "coverage": {"industry": 999, "top10_shareholders": 999},
    })
    assert result["enrichment_status"] == {
        "status": "degraded", "availability": "UNAVAILABLE", "total": 2,
        "coverage": {"industry": 1, "top10_shareholders": 1},
        "unavailable": {"industry": ["000002.SZ"], "top10_shareholders": ["600001.SH"]},
    }

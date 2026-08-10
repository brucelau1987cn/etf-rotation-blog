from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "publish_us_compass_research.py"
PAGE = ROOT / "src" / "pages" / "us-compass" / "research.astro"
SUBNAV = ROOT / "src" / "components" / "UsSubnav.astro"
DATA = ROOT / "public" / "data" / "us-compass-research.json"
COMPONENT_DIR = ROOT / "src" / "components" / "us-research"
COMPONENT_NAMES = [
    "UsResearchHero.astro",
    "UsResearchHealthStrip.astro",
    "UsRankIcPanel.astro",
    "UsShadowHealthPanel.astro",
    "UsResearchArchive.astro",
]


def load_module():
    spec = importlib.util.spec_from_file_location("publish_us_compass_research", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixtures():
    fingerprint = {
        "model_version": "us-compass-v1",
        "universe_count": 2,
        "symbols_sha256": "a" * 64,
        "config_sha256": "b" * 64,
        "execution_basis": "T close signal; T+1 open execution; next-open rebalance",
        "one_way_cost": 0.001,
        "initial_capital": 20_000.0,
        "horizons": [1, 5, 20],
        "exposure_mapping": {
            "values": {"偏强": 1.0, "震荡": 0.5, "防御": 0.0},
            "default": 0.5,
        },
    }
    learning = {
        "updated_at": "2026-07-31T22:31:00Z",
        "model_fingerprint": json.loads(json.dumps(fingerprint)),
        "snapshots": [{"date": "2026-07-31", "top10": ["KWEB", "XLE"], "exposure": 0.5}],
        "metrics": {
            "t1": {"observations": 14, "rank_ic_mean": -0.002839, "rank_ic_positive_rate": 0.642857, "deviation_mean": 0.327498, "random_deviation_reference": 0.333333},
            "t5": {"observations": 10, "rank_ic_mean": 0.158769, "rank_ic_positive_rate": 0.7, "deviation_mean": 0.298204, "random_deviation_reference": 0.333333},
            "t20": {"observations": 0, "rank_ic_mean": None, "rank_ic_positive_rate": None, "deviation_mean": None, "random_deviation_reference": 0.333333},
        },
    }
    shadow = {
        "updated_at": "2026-07-31T22:45:00Z",
        "model_fingerprint": json.loads(json.dumps(fingerprint)),
        "basis": "T close signal; T+1 open execution; next-open rebalance",
        "one_way_cost": 0.001,
        "history": [{"exit_date": "2026-07-31"}],
        "stats": {
            "benchmark": {"total_return": -0.011344, "max_drawdown": -0.024117},
            "timing": {"total_return": -0.013433, "max_drawdown": -0.022248},
            "rotation": {"total_return": -0.018578, "max_drawdown": -0.024796},
            "fusion": {"total_return": -0.028304, "max_drawdown": -0.02893},
        },
    }
    pool = {
        "model_date": "2026-07-31",
        "session_state": "closed",
        "market_regime": {"state": "震荡", "equity_allocation": "30%-50%"},
        "rows": [
            {"symbol": "KWEB", "theme": "中国资产", "trend_score": 95.2, "trading_risk_score": 20.8, "trade_state": "可持有"},
            {"symbol": "XLE", "theme": "能源", "trend_score": 80.3, "trading_risk_score": 17.1, "trade_state": "可持有"},
        ],
    }
    return learning, shadow, pool


def health_fixture(learning, pool):
    metric = {
        "status": "ACCUMULATING", "observations": 4, "minimum_required": 20,
        "maturity_ratio": 0.2, "rank_ic_mean": None, "rank_ic_median": None,
        "rank_ic_std": None, "icir": None, "positive_rate": None,
        "recent_5_mean": None, "recent_5_count": 0, "recent_10_mean": None,
        "recent_10_count": 0, "trend": None,
        "series": [
            {"signal_date": "2026-07-20", "date": "2026-07-21", "value": 0.1},
            {"signal_date": "2026-07-21", "date": "2026-07-22", "value": -0.1},
            {"signal_date": "2026-07-22", "date": "2026-07-23", "value": 0.2},
            {"signal_date": "2026-07-23", "date": "2026-07-24", "value": 0.0},
        ],
    }
    portfolio = {
        "status": "ACCUMULATING", "observations": 4, "total_return": None,
        "annualized_volatility": None, "max_drawdown": None, "current_drawdown": None,
        "longest_drawdown_duration": None, "rolling_20d_volatility": None,
        "positive_period_rate": None, "excess_return_vs_benchmark": None,
        "equity_series": [
            {"date": f"2026-07-{index + 21:02d}", "equity": 20_000.0, "period_return": 0.0}
            for index in range(4)
        ],
    }
    return {
        "schema_version": "us-compass-health-v1",
        "market": "US",
        "model_date": pool["model_date"],
        "generated_at": "2026-07-31T23:00:00Z",
        "model_fingerprint": copy.deepcopy(learning["model_fingerprint"]),
        "sample_maturity": {
            "status": "ACCUMULATING", "observations": 4,
            "minimum_observations": 20, "mature": False,
            "reasons": ["forward sample is immature"],
        },
        "horizons": {name: copy.deepcopy(metric) for name in ("t1", "t5", "t20")},
        "walk_forward": {
            "status": "ACCUMULATING", "windows": 1, "evaluated_windows": 0,
            "positive_windows": 0, "positive_slice_rate": None, "score": None,
            "slice_size": 5, "horizon": "t5",
            "slices": [{
                "index": 0, "start_date": "2026-07-21", "end_date": "2026-07-24",
                "signal_start_date": "2026-07-20", "signal_end_date": "2026-07-23",
                "observations": 4, "status": "INSUFFICIENT", "mean": None,
                "positive_rate": None,
            }],
            "reasons": ["T+5 requires 20 observations; 4 available"],
        },
        "shadow_health": {
            "status": "ACCUMULATING", "observations": 4, "initial_capital": 20_000.0,
            "return": None, "max_drawdown": None, "score": None,
            "portfolios": {name: copy.deepcopy(portfolio) for name in ("benchmark", "timing", "rotation", "fusion")},
            "reasons": ["shadow requires 20 observations; 4 available"],
        },
        "cost_sensitivity": {
            "status": "UNAVAILABLE", "baseline_cost": 0.001, "observations": 4,
            "break_even_cost": None, "score": None,
            "scenarios": [
                {"one_way_cost": cost, "value": None, "annualized_return": None, "max_drawdown": None}
                for cost in (0, 0.0005, 0.001, 0.002, 0.003)
            ],
            "reasons": ["turnover history unavailable; exact cost scenarios require persisted turnover"],
        },
        "overall": {"status": "ACCUMULATING", "score": None, "reasons": ["insufficient history"]},
    }


def test_build_report_v2_preserves_legacy_fields_and_projects_health_summaries():
    module = load_module()
    learning, shadow, pool = fixtures()
    health = health_fixture(learning, pool)
    report = module.build_report(
        learning, shadow, pool,
        iwencai={"status": "ok", "summary": "科技与能源仍是主线", "source": "同花顺问财"},
        health=health,
    )

    assert report["schema_version"] == "us-compass-research-v2"
    assert report["trade_date"] == "2026-07-31"
    assert report["verdict"] == "样本积累中"
    assert report["metrics"]["t5"]["observations"] == 10
    assert report["metrics"]["t5"]["rank_ic_pct"] == 15.88
    assert report["attribution"] == {
        "timing_pp": -0.21, "rotation_pp": -0.72,
        "interaction_pp": -0.76, "fusion_vs_benchmark_pp": -1.7,
    }
    assert report["iwencai"]["source"] == "同花顺问财"
    assert report["model_fingerprint"] == learning["model_fingerprint"]
    assert report["model_fingerprint"] is not learning["model_fingerprint"]
    assert report["health_summary"] == {
        "status": "ACCUMULATING", "score": None,
        "reasons": ["insufficient history"], "model_date": "2026-07-31",
        "generated_at": "2026-07-31T23:00:00Z",
    }
    assert report["sample_maturity"] == health["sample_maturity"]
    assert report["walk_forward_summary"] == {
        key: health["walk_forward"][key]
        for key in ("status", "windows", "evaluated_windows", "positive_windows", "positive_slice_rate", "score", "reasons")
    }
    assert report["shadow_health_summary"]["portfolios"]["fusion"] == {
        key: health["shadow_health"]["portfolios"]["fusion"][key]
        for key in ("total_return", "max_drawdown", "annualized_volatility", "positive_period_rate", "excess_return_vs_benchmark")
    }
    assert report["cost_sensitivity_summary"]["scenarios"] == health["cost_sensitivity"]["scenarios"]
    assert report["data_quality"]["status"] == "ACCUMULATING"
    assert report["data_quality"]["fingerprint_consistent"] is True
    assert report["data_quality"]["production_change_allowed"] is False
    assert report["production_change_allowed"] is False


@pytest.mark.parametrize("location", ["learning", "shadow", "health"])
def test_build_report_rejects_missing_or_mismatched_fingerprint(location):
    module = load_module()
    learning, shadow, pool = fixtures()
    health = health_fixture(learning, pool)
    target = {"learning": learning, "shadow": shadow, "health": health}[location]
    if location == "health":
        target.pop("model_fingerprint")
    else:
        target["model_fingerprint"]["horizons"] = [1, 5]
    with pytest.raises(ValueError, match="model fingerprint"):
        module.build_report(learning, shadow, pool, health=health)


def test_build_report_rejects_invalid_unsafe_and_nonfinite_health():
    module = load_module()
    learning, shadow, pool = fixtures()
    for mutate in (
        lambda value: value.update(schema_version="wrong"),
        lambda value: value["overall"].update(score=float("nan")),
        lambda value: value.update(extra="unsafe"),
    ):
        health = health_fixture(learning, pool)
        mutate(health)
        with pytest.raises(ValueError, match="health payload invalid"):
            module.build_report(learning, shadow, pool, health=health)


def test_build_report_rejects_health_model_date_mismatch():
    module = load_module()
    learning, shadow, pool = fixtures()
    health = health_fixture(learning, pool)
    health["model_date"] = "2026-08-01"
    with pytest.raises(ValueError, match="health model_date"):
        module.build_report(learning, shadow, pool, health=health)


def test_build_report_requires_health_keyword_and_preserves_fourth_positional_iwencai():
    module = load_module()
    learning, shadow, pool = fixtures()
    iwencai = {"status": "ok", "summary": "legacy positional", "source": "同花顺问财"}

    with pytest.raises(ValueError, match="health is required"):
        module.build_report(learning, shadow, pool, iwencai)

    report = module.build_report(learning, shadow, pool, iwencai, health=health_fixture(learning, pool))
    assert report["iwencai"]["summary"] == "legacy positional"


@pytest.mark.parametrize("snapshots", [[], [{"date": "2026-07-30", "exposure": 0.5}]])
def test_build_report_rejects_missing_or_stale_latest_learning_snapshot(snapshots):
    module = load_module()
    learning, shadow, pool = fixtures()
    learning["snapshots"] = snapshots
    with pytest.raises(ValueError, match="latest learning snapshot date"):
        module.build_report(learning, shadow, pool, health=health_fixture(learning, pool))


def test_build_report_rejects_stale_or_missing_shadow_history_when_observed():
    module = load_module()
    learning, shadow, pool = fixtures()
    health = health_fixture(learning, pool)
    for history in (
        [],
        [{"exit_date": "2026-07-30"}],
        [{"exit_date": "2026-07-31"}, {"exit_date": "2026-07-30"}],
        [{"exit_date": "not-a-date"}],
    ):
        candidate = copy.deepcopy(shadow)
        candidate["history"] = history
        with pytest.raises(ValueError, match="shadow history"):
            module.build_report(learning, candidate, pool, health=health)


def test_build_report_allows_empty_shadow_history_when_health_has_zero_observations():
    module = load_module()
    learning, shadow, pool = fixtures()
    shadow["history"] = []
    health = health_fixture(learning, pool)
    health["shadow_health"]["observations"] = 0
    health["cost_sensitivity"]["observations"] = 0
    for portfolio in health["shadow_health"]["portfolios"].values():
        portfolio["observations"] = 0
        portfolio["equity_series"] = []
    report = module.build_report(learning, shadow, pool, health=health)
    assert report["trade_date"] == pool["model_date"]


@pytest.mark.parametrize("field", ["health", "learning", "shadow", "future_health"])
def test_build_report_rejects_invalid_or_incoherent_timestamps(field):
    module = load_module()
    learning, shadow, pool = fixtures()
    health = health_fixture(learning, pool)
    if field == "health":
        health["generated_at"] = "2026-07-31T23:00:00"
    elif field == "learning":
        learning["updated_at"] = "2026-08-01T00:00:00Z"
    elif field == "shadow":
        shadow["updated_at"] = "not-a-time"
    else:
        health["generated_at"] = "2999-01-01T00:00:00Z"
    with pytest.raises(ValueError, match="timestamp|generated_at|updated_at"):
        module.build_report(learning, shadow, pool, health=health)


@pytest.mark.parametrize("exposure", [True, 2, float("nan"), float("inf"), "oops"])
def test_build_report_rejects_invalid_exposure(exposure):
    module = load_module()
    learning, shadow, pool = fixtures()
    learning["snapshots"][-1]["exposure"] = exposure
    with pytest.raises(ValueError, match="exposure"):
        module.build_report(learning, shadow, pool, health=health_fixture(learning, pool))


def test_build_report_data_quality_status_rules():
    module = load_module()
    learning, shadow, pool = fixtures()
    accumulating = health_fixture(learning, pool)
    assert module.build_report(learning, shadow, pool, health=accumulating)["data_quality"]["status"] == "ACCUMULATING"

    unavailable = health_fixture(learning, pool)
    unavailable["overall"].update(status="UNAVAILABLE", reasons=["governing evidence unavailable"])
    unavailable["sample_maturity"].update(status="UNAVAILABLE", reasons=["governing evidence unavailable"])
    unavailable["walk_forward"].update(status="UNAVAILABLE", reasons=["governing evidence unavailable"])
    assert module.build_report(learning, shadow, pool, health=unavailable)["data_quality"]["status"] == "UNAVAILABLE"

    healthy = health_fixture(learning, pool)
    healthy["overall"].update(status="MIXED", score=0.75, reasons=[])
    healthy["sample_maturity"].update(status="MIXED", observations=20, mature=True, reasons=[])
    healthy["shadow_health"].update(status="MIXED", observations=20, reasons=[])
    assert module._data_quality(healthy, pool)["status"] == "HEALTHY"

    healthy["shadow_health"].update(status="UNAVAILABLE", reasons=["shadow unavailable"])
    assert module._data_quality(healthy, pool)["status"] == "UNAVAILABLE"


def test_build_report_deep_copies_nested_fingerprint():
    module = load_module()
    learning, shadow, pool = fixtures()
    report = module.build_report(learning, shadow, pool, health=health_fixture(learning, pool))
    report["model_fingerprint"]["exposure_mapping"]["values"]["偏强"] = 0.25
    assert learning["model_fingerprint"]["exposure_mapping"]["values"]["偏强"] == 1.0
    assert shadow["model_fingerprint"]["exposure_mapping"]["values"]["偏强"] == 1.0


def test_merge_archive_preserves_v1_entries_and_dedupes_new_v2_week():
    module = load_module()
    old = {
        "schema_version": "us-compass-research-v1",
        "reports": [
            {"week_key": "2026-W30", "trade_date": "2026-07-24", "verdict": "旧v1"},
            {"week_key": "2026-W31", "trade_date": "2026-07-30", "verdict": "被替换"},
        ],
    }
    new = {
        "schema_version": "us-compass-research-v2", "week_key": "2026-W31",
        "trade_date": "2026-07-31", "verdict": "新v2", "generated_at": "2026-07-31T23:00:00Z",
    }
    merged = module.merge_archive(old, new)
    assert merged["schema_version"] == "us-compass-research-v2"
    assert [item["verdict"] for item in merged["reports"]] == ["新v2", "旧v1"]
    assert "schema_version" not in merged["reports"][1]


def test_merge_archive_sorts_deterministically_and_backfills_latest_week_from_first_report():
    module = load_module()
    existing = {
        "reports": [
            {"week_key": "2026-W30", "trade_date": "2026-07-24", "generated_at": "2026-07-24T23:00:00Z"},
            {"week_key": "2026-W31-old", "trade_date": "2026-07-31", "generated_at": "2026-07-31T22:00:00Z"},
        ],
    }
    report = {
        "week_key": "2026-W29", "trade_date": "2026-07-17", "generated_at": "2026-07-17T23:00:00Z",
    }

    merged = module.merge_archive(existing, report)

    assert [item["week_key"] for item in merged["reports"]] == ["2026-W31-old", "2026-W30", "2026-W29"]
    assert merged["latest_week"] == "2026-W31-old"


def test_cli_explicit_tmp_inputs_write_atomically(tmp_path):
    learning, shadow, pool = fixtures()
    health = health_fixture(learning, pool)
    paths = {}
    for name, payload in (("learning", learning), ("shadow", shadow), ("pool", pool), ("health", health)):
        paths[name] = tmp_path / f"{name}.json"
        paths[name].write_text(json.dumps(payload), encoding="utf-8")
    output = tmp_path / "research.json"
    proc = subprocess.run(
        [
            sys.executable, str(SCRIPT), "--learning", str(paths["learning"]),
            "--shadow", str(paths["shadow"]), "--pool", str(paths["pool"]),
            "--health", str(paths["health"]), "--output", str(output),
        ],
        cwd=ROOT, text=True, capture_output=True,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "us-compass-research-v2"
    assert payload["reports"][0]["schema_version"] == "us-compass-research-v2"
    assert not list(tmp_path.glob("*.tmp"))


def test_cli_default_missing_health_is_staging_blocker_without_write(tmp_path):
    learning, shadow, pool = fixtures()
    for name, payload in (("learning", learning), ("shadow", shadow), ("pool", pool)):
        (tmp_path / f"{name}.json").write_text(json.dumps(payload), encoding="utf-8")
    output = tmp_path / "must-not-exist.json"
    missing_health = tmp_path / "missing-health.json"
    proc = subprocess.run(
        [
            sys.executable, str(SCRIPT), "--learning", str(tmp_path / "learning.json"),
            "--shadow", str(tmp_path / "shadow.json"), "--pool", str(tmp_path / "pool.json"),
            "--health", str(missing_health), "--output", str(output),
        ],
        cwd=ROOT, text=True, capture_output=True,
    )
    assert proc.returncode != 0
    assert "staging blocker" in proc.stderr.lower()
    assert "health" in proc.stderr.lower()
    assert not output.exists()


def test_main_publish_with_nondefault_output_rejects_before_inputs_or_external_calls(tmp_path, monkeypatch):
    module = load_module()
    output = tmp_path / "must-not-exist.json"
    calls = {"iwencai": 0, "publish": 0}

    def unexpected_iwencai(pool):
        calls["iwencai"] += 1
        raise AssertionError("iwencai must not be called")

    def unexpected_publish():
        calls["publish"] += 1
        raise AssertionError("publish must not be called")

    monkeypatch.setattr(module, "iwencai_validation", unexpected_iwencai)
    monkeypatch.setattr(module, "publish", unexpected_publish)

    result = module.main([
        "--publish", "--iwencai", "--output", str(output),
        "--learning", str(tmp_path / "missing-learning.json"),
        "--shadow", str(tmp_path / "missing-shadow.json"),
        "--pool", str(tmp_path / "missing-pool.json"),
        "--health", str(tmp_path / "missing-health.json"),
    ])

    assert result == 2
    assert calls == {"iwencai": 0, "publish": 0}
    assert not output.exists()


def archive_report(week="2026-W30", trade_date="2026-07-24", **extra):
    return {"week_key": week, "trade_date": trade_date, **extra}


@pytest.mark.parametrize(
    "payload",
    [
        [17],
        {"reports": {}},
        {"reports": [17]},
        {"reports": [{}]},
        {"reports": [archive_report(trade_date="2026-99-99")]},
        {"reports": [archive_report(generated_at="2026-07-24T23:00:00")]},
        {"reports": [], "updated_at": "2026-07-24T23:00:00"},
        {"schema_version": "us-compass-research-v3", "reports": []},
        {"reports": [archive_report()], "latest_week": "2026-W29"},
        {"reports": [archive_report(), archive_report(trade_date="2026-07-25")]},
    ],
)
def test_read_existing_archive_rejects_corrupt_archive_payloads(tmp_path, payload):
    module = load_module()
    output = tmp_path / "research.json"
    contents = json.dumps(payload)
    output.write_text(contents, encoding="utf-8")

    with pytest.raises(ValueError, match="existing archive"):
        module.read_existing_archive(output)

    assert output.read_text(encoding="utf-8") == contents


@pytest.mark.parametrize(
    "payload",
    [
        {"reports": [17]},
        {"reports": [{}]},
        {"reports": [archive_report(trade_date="invalid")]},
        {"reports": [archive_report(), archive_report(trade_date="2026-07-25")]},
    ],
)
def test_merge_archive_rejects_invalid_existing_payload_when_called_directly(payload):
    module = load_module()
    incoming = archive_report(
        "2026-W31", "2026-07-31", generated_at="2026-07-31T23:00:00Z"
    )

    with pytest.raises(ValueError, match="existing archive"):
        module.merge_archive(payload, incoming)


@pytest.mark.parametrize(
    "incoming",
    [
        {},
        archive_report(week=""),
        archive_report(trade_date="invalid"),
        archive_report(generated_at="2026-07-24T23:00:00"),
    ],
)
def test_merge_archive_rejects_invalid_incoming_report(incoming):
    module = load_module()

    with pytest.raises(ValueError, match="incoming report"):
        module.merge_archive({"reports": []}, incoming)


def test_read_existing_archive_preserves_unknown_legacy_fields(tmp_path):
    module = load_module()
    payload = {
        "schema_version": "us-compass-research-v1",
        "legacy_archive_field": {"keep": True},
        "reports": [archive_report(legacy_report_field="keep")],
    }
    output = tmp_path / "research.json"
    output.write_text(json.dumps(payload), encoding="utf-8")

    assert module.read_existing_archive(output) == payload


def test_cli_malformed_existing_archive_is_not_overwritten(tmp_path):
    learning, shadow, pool = fixtures()
    health = health_fixture(learning, pool)
    paths = {}
    for name, payload in (("learning", learning), ("shadow", shadow), ("pool", pool), ("health", health)):
        paths[name] = tmp_path / f"{name}.json"
        paths[name].write_text(json.dumps(payload), encoding="utf-8")
    output = tmp_path / "research.json"
    original = b'{"schema_version":"us-compass-research-v2","reports":[17]}\n'
    output.write_bytes(original)

    result = subprocess.run([
        sys.executable, str(SCRIPT), "--learning", str(paths["learning"]),
        "--shadow", str(paths["shadow"]), "--pool", str(paths["pool"]),
        "--health", str(paths["health"]), "--output", str(output),
    ], cwd=ROOT, text=True, capture_output=True)

    assert result.returncode == 2
    assert "existing archive" in result.stderr
    assert output.read_bytes() == original


def test_research_page_and_navigation_contract():
    assert PAGE.exists()
    page = PAGE.read_text(encoding="utf-8")
    nav = SUBNAV.read_text(encoding="utf-8")
    for marker in ("模型学习", "问财验证", "样本积累中"):
        assert marker in page
    assert "public/data/us-compass-research.json" in page
    assert 'active="research"' in page
    assert "'/us-compass/research/'" in nav
    assert nav.index("历史成绩") < nav.index("模型学习")
    for name in COMPONENT_NAMES:
        assert name.replace(".astro", "") in page, f"page should import {name}"


def test_research_components_all_exist():
    for name in COMPONENT_NAMES:
        file = COMPONENT_DIR / name
        assert file.exists(), f"missing component: {name}"
        content = file.read_text(encoding="utf-8")
        assert content.startswith("---"), f"{name} should have frontmatter"
        assert "interface Props" in content, f"{name} should declare Props interface"


def test_research_hero_component_markers():
    hero = COMPONENT_DIR / "UsResearchHero.astro"
    content = hero.read_text(encoding="utf-8")
    assert "research-only-badge" in content, "hero should have research-only badge"
    assert "仅研究用途" in content, "hero should have Chinese research-only badge"
    assert "production_change_allowed" in content, "hero should reference production_change_allowed"
    assert "health_summary" in content, "hero should check for health_summary (v2 detection)"
    assert "data_quality" in content, "hero should reference data_quality"
    assert "FORWARD LEARNING" in content, "hero should show forward learning label"
    assert "top10-grid" in content, "hero should have top10 grid"
    assert "risk_budget" in content, "hero should reference risk budget"


def test_research_health_strip_component_markers():
    strip = COMPONENT_DIR / "UsResearchHealthStrip.astro"
    content = strip.read_text(encoding="utf-8")
    assert "summary-strip" in content, "strip should have summary-strip class"
    assert "health_summary" in content, "strip should detect v2 via health_summary"
    assert "snapshot_count" in content, "strip should handle v1 snapshot_count"
    assert "T+5成熟样本" in content, "strip should show T5 observations in v1"
    assert "data_quality" in content, "strip should reference data_quality in v2"
    assert "sample_maturity" in content, "strip should reference sample_maturity in v2"
    assert "shadow_health_summary" in content, "strip should reference shadow_health in v2"


def test_rank_ic_panel_component_markers():
    panel = COMPONENT_DIR / "UsRankIcPanel.astro"
    content = panel.read_text(encoding="utf-8")
    assert "FORWARD METRICS" in content, "panel should have forward metrics header"
    assert "RankIC" in content, "panel should show RankIC label"
    assert "walk_forward_summary" in content, "panel should reference walk_forward"
    assert "sparkline" in content, "panel should have SVG sparkline support"
    assert "历史详细时序不可用" in content, "panel should show v1 fallback note"
    assert "buildSparkline" in content, "panel should have sparkline builder function"


def test_research_health_strip_component_v1_vs_v2():
    """Verify component has both v1 and v2 render paths."""
    strip = COMPONENT_DIR / "UsResearchHealthStrip.astro"
    content = strip.read_text(encoding="utf-8")
    # v1 path markers
    assert "前向快照" in content
    assert "风险预算" in content
    assert "研究结论" in content
    # v2 path markers
    assert "整体健康度" in content
    assert "样本成熟度" in content
    assert "影子组合健康" in content


def test_shadow_health_panel_component_markers():
    panel = COMPONENT_DIR / "UsShadowHealthPanel.astro"
    content = panel.read_text(encoding="utf-8")
    assert "SHADOW ATTRIBUTION" in content, "panel should have shadow attribution header"
    assert "四组合收益与回撤" in content, "panel should show portfolio returns"
    assert "归因拆解" in content, "panel should have attribution breakdown"
    assert "attribution" in content, "panel should reference attribution"
    assert "shadow_health_summary" in content, "panel should reference shadow health"
    assert "cost_sensitivity_summary" in content, "panel should reference cost sensitivity"
    assert "SHADOW HEALTH" in content, "panel should have shadow health section"
    assert "COST SENSITIVITY" in content, "panel should have cost sensitivity section"
    assert "cost_sensitivity" in content, "panel should have cost sensitivity scenarios"


def test_research_archive_component_markers():
    archive = COMPONENT_DIR / "UsResearchArchive.astro"
    content = archive.read_text(encoding="utf-8")
    assert "WEEKLY ARCHIVE" in content, "archive should have weekly archive header"
    assert "历史周报" in content, "archive should show Chinese header"
    assert "archive-list" in content, "archive should have archive-list class"
    assert "health_summary" in content, "archive should detect v2 via health_summary"
    assert "walk_forward_summary" in content, "archive should reference walk_forward in v2 entries"
    assert "T+1 RankIC" in content, "archive should show T1 RankIC in v1 entries"
    assert "T+5 RankIC" in content, "archive should show T5 RankIC in v1 entries"
    assert "Fusion收益" in content, "archive should show fusion return"


def test_component_no_undefined_or_nan_in_templates():
    """Verify components don't use unsafe patterns that could produce NaN/undefined/Infinity."""
    for name in COMPONENT_NAMES:
        file = COMPONENT_DIR / name
        content = file.read_text(encoding="utf-8")
        # Should not use dangerous patterns without guard
        assert "Number.isFinite" in content or "Number(value).toFixed" not in content, \
            f"{name} should guard toFixed calls with Number.isFinite"


def test_build_output_clean():
    """After build, verify no NaN/undefined/Infinity in rendered output."""
    built = ROOT / "dist" / "us-compass" / "research" / "index.html"
    if not built.exists():
        pytest.skip("build output not found; run 'npm run build' first")
    html = built.read_text(encoding="utf-8")
    assert "undefined" not in html, "rendered output contains 'undefined'"
    assert "NaN" not in html, "rendered output contains 'NaN'"
    assert "Infinity" not in html, "rendered output contains 'Infinity'"


def test_production_change_allowed_wording():
    """Verify production_change_allowed wording in hero component."""
    hero = COMPONENT_DIR / "UsResearchHero.astro"
    content = hero.read_text(encoding="utf-8")
    assert "production_change_allowed=false" in content, \
        "hero should show production_change_allowed=false label"
    assert "生产变更未授权" in content, \
        "hero should show Chinese production change not authorized label"


def test_public_research_archive_contract():
    payload = json.loads(DATA.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "us-compass-research-v1"
    assert isinstance(payload["reports"], list) and payload["reports"]
    latest = payload["reports"][0]
    assert latest["production_change_allowed"] is False
    assert latest["iwencai"]["source"] == "同花顺问财"
    assert latest["execution_basis"]["one_way_cost_pct"] == 0.1


def test_weekly_publisher_deploys_research_archive():
    publisher = (ROOT / "scripts" / "publish_us_compass_research.py").read_text(encoding="utf-8")
    assert '"https://etf.peekabo.cc/us-compass/research/"' in publisher
    assert '"https://etf.peekabo.cc/data/us-compass-research.json"' in publisher
    assert "generate_data_catalog.py" in publisher
    assert "release_pages" in publisher


def test_catalog_registers_research_archive():
    catalog = (ROOT / "scripts" / "generate_data_catalog.py").read_text(encoding="utf-8")
    assert 'DatasetSpec("us-compass-research"' in catalog
    assert '"us-compass-research.json"' in catalog


def test_weekly_wrapper_publishes_research_archive():
    wrapper = Path("/root/.hermes/scripts/publish_us_compass_research.py")
    assert wrapper.exists()
    source = wrapper.read_text(encoding="utf-8")
    assert "scripts/publish_us_compass_research.py" in source
    assert "--publish" in source
    assert "--iwencai" in source

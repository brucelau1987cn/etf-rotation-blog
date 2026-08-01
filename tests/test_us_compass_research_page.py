from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "publish_us_compass_research.py"
PAGE = ROOT / "src" / "pages" / "us-compass" / "research.astro"
SUBNAV = ROOT / "src" / "components" / "UsSubnav.astro"
DATA = ROOT / "public" / "data" / "us-compass-research.json"


def load_module():
    spec = importlib.util.spec_from_file_location("publish_us_compass_research", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixtures():
    learning = {
        "updated_at": "2026-07-31T22:31:00Z",
        "snapshots": [{"date": "2026-07-31", "top10": ["KWEB", "XLE"], "exposure": 0.5}],
        "metrics": {
            "t1": {"observations": 14, "rank_ic_mean": -0.002839, "rank_ic_positive_rate": 0.642857, "deviation_mean": 0.327498, "random_deviation_reference": 0.333333},
            "t5": {"observations": 10, "rank_ic_mean": 0.158769, "rank_ic_positive_rate": 0.7, "deviation_mean": 0.298204, "random_deviation_reference": 0.333333},
            "t20": {"observations": 0, "rank_ic_mean": None, "rank_ic_positive_rate": None, "deviation_mean": None, "random_deviation_reference": 0.333333},
        },
    }
    shadow = {
        "basis": "T close signal; T+1 open execution; next-open rebalance",
        "one_way_cost": 0.001,
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


def test_build_report_preserves_sample_gate_and_attribution():
    module = load_module()
    learning, shadow, pool = fixtures()
    report = module.build_report(learning, shadow, pool, iwencai={"status": "ok", "summary": "科技与能源仍是主线", "source": "同花顺问财"})

    assert report["trade_date"] == "2026-07-31"
    assert report["verdict"] == "样本积累中"
    assert report["metrics"]["t5"]["observations"] == 10
    assert report["metrics"]["t5"]["rank_ic_pct"] == 15.88
    assert report["attribution"]["timing_pp"] == -0.21
    assert report["attribution"]["rotation_pp"] == -0.72
    assert report["attribution"]["interaction_pp"] == -0.76
    assert report["iwencai"]["source"] == "同花顺问财"
    assert report["production_change_allowed"] is False


def test_merge_archive_is_first_write_per_week():
    module = load_module()
    old = {"schema_version": "us-compass-research-v1", "reports": [{"week_key": "2026-W31", "trade_date": "2026-07-30", "verdict": "旧"}]}
    new = {"week_key": "2026-W31", "trade_date": "2026-07-31", "verdict": "新"}
    merged = module.merge_archive(old, new)
    assert len(merged["reports"]) == 1
    assert merged["reports"][0]["verdict"] == "新"


def test_research_page_and_navigation_contract():
    assert PAGE.exists()
    page = PAGE.read_text(encoding="utf-8")
    nav = SUBNAV.read_text(encoding="utf-8")
    for marker in ("模型学习", "T+1 / T+5 / T+20", "四组合收益与回撤", "问财验证", "历史周报", "样本积累中"):
        assert marker in page
    assert "public/data/us-compass-research.json" in page
    assert "active=\"research\"" in page
    assert "'/us-compass/research/'" in nav
    assert nav.index("历史成绩") < nav.index("模型学习")


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

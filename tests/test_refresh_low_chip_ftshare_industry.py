import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "refresh_low_chip_ftshare_industry.py"
CURRENT = ROOT / "public/data/a-low-chip-stocks.json"
TRACKING = ROOT / "public/data/low-chip-tracking.json"


def load_module():
    spec = importlib.util.spec_from_file_location("refresh_low_chip_ftshare_industry", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_display_uses_sw_level2_and_at_most_three_unique_themes():
    mod = load_module()
    row = {
        "swLevel1Code": None,
        "swLevel1Name": "房地产",
        "swLevel2Code": None,
        "swLevel2Name": "房地产开发",
        "swLevel3Code": None,
        "swLevel3Name": "住宅开发",
    }
    result = mod.build_industry_fields(
        row,
        ["融资融券", "租售同权", "物业管理", "租售同权", "冰雪产业", "更多概念"],
        "2026-08-25",
    )
    assert result["industry_standard"] == "SW2021"
    assert result["industry_source"] == "iWenCai 所属申万行业"
    assert result["industry_level2"] == {"code": None, "name": "房地产开发"}
    assert result["sector"] == "房地产开发"
    assert result["theme_concepts"] == ["租售同权", "物业管理", "冰雪产业"]
    assert result["sector_with_theme"] == "房地产开发（租售同权、物业管理、冰雪产业）"

    packed = mod.clean_themes(
        ["芯片概念;租售同权;物业管理;冰雪产业;融资融券"],
        {"商贸零售", "一般零售", "商业物业经营"},
    )
    assert packed == ["芯片概念", "租售同权", "物业管理"]


def test_build_industry_mapping_parses_iwencai_paths_without_codes():
    mod = load_module()
    hints = {
        "000001.SZ": "银行--股份制银行Ⅱ--股份制银行Ⅲ",
        "600000.SH": "银行||股份制银行Ⅱ||股份制银行Ⅲ",
        "300000.SZ": "单一行业",  # 缺二级路径 → 不落地
        "600001.SH": "",  # 空 → 不落地
    }
    result = mod.build_industry_mapping(hints)
    assert set(result) == {"000001.SZ", "600000.SH"}
    assert result["000001.SZ"]["swLevel1Name"] == "银行"
    assert result["000001.SZ"]["swLevel2Name"] == "股份制银行Ⅱ"
    assert result["000001.SZ"]["swLevel3Name"] == "股份制银行Ⅲ"
    assert result["000001.SZ"]["swLevel2Code"] is None
    assert result["600000.SH"]["swLevel2Name"] == "股份制银行Ⅱ"
    assert result["600000.SH"]["swLevel2Code"] is None


def test_load_entry_themes_uses_original_membership_snapshot_after_threshold_change(tmp_path, monkeypatch):
    mod = load_module()
    monkeypatch.setattr(mod, "HISTORY", tmp_path)
    (tmp_path / "2026-09-03.json").write_text(
        json.dumps({"intersection": [], "enrichments": {}}), encoding="utf-8"
    )
    original = tmp_path / "2026-09-03.threshold-2.5.json"
    original.write_text(json.dumps({
        "intersection": ["001289.SZ"],
        "enrichments": {"001289.SZ": {"theme_concepts": ["绿电", "央企"]}},
    }), encoding="utf-8")
    tracking = {"stocks": {"001289.SZ": {"first_seen": "2026-09-03"}}}

    assert mod.load_entry_themes(tracking) == {"001289.SZ": ["绿电", "央企"]}


def test_apply_refresh_updates_current_and_tracking_without_rewriting_daily_rows():
    mod = load_module()
    current = {
        "data_as_of": "2026-08-25",
        "intersection": ["000001.SZ"],
        "enrichments": {"000001.SZ": {"theme_concepts": ["银行", "中特估"]}},
    }
    tracking = {
        "stocks": {"000001.SZ": {
            "first_seen": "2026-08-01",
            "industry": "银行--股份制银行Ⅱ--股份制银行Ⅲ",
            "daily": [{"date": "2026-08-01", "close": 10.0}],
        }}
    }
    history_themes = {"000001.SZ": ["银行", "中特估"]}
    mapping = {"000001.SZ": {
        "swLevel1Code": None, "swLevel1Name": "银行",
        "swLevel2Code": None, "swLevel2Name": "股份制银行Ⅱ",
        "swLevel3Code": None, "swLevel3Name": "股份制银行Ⅲ",
    }}
    before_daily = list(tracking["stocks"]["000001.SZ"]["daily"])
    mod.apply_refresh(current, tracking, mapping, history_themes, "2026-08-25")
    assert current["enrichments"]["000001.SZ"]["sector"] == "股份制银行Ⅱ"
    assert tracking["stocks"]["000001.SZ"]["industry"] == "股份制银行Ⅱ"
    assert tracking["stocks"]["000001.SZ"]["industry_display"] == "股份制银行Ⅱ（中特估）"
    assert tracking["stocks"]["000001.SZ"]["daily"] == before_daily


def test_apply_refresh_skips_symbols_missing_mapping():
    mod = load_module()
    current = {
        "data_as_of": "2026-08-25",
        "intersection": ["000001.SZ"],
        "enrichments": {"000001.SZ": {"theme_concepts": [], "sector": "待补充", "industry": "待补充"}},
    }
    tracking = {"stocks": {}}
    mapping = {}  # 000001 缺二级路径 → 跳过，保留 enrich 阶段「待补充」原值
    mod.apply_refresh(current, tracking, mapping, {}, "2026-08-25")
    assert current["enrichments"]["000001.SZ"]["sector"] == "待补充"
    assert current["enrichments"]["000001.SZ"]["industry"] == "待补充"


def test_published_low_chip_industry_contract_has_full_coverage():
    current = json.loads(CURRENT.read_text(encoding="utf-8"))
    tracking = json.loads(TRACKING.read_text(encoding="utf-8"))
    assert len(current["intersection"]) >= 1
    assert len(tracking["stocks"]) >= 1
    for symbol in current["intersection"]:
        row = current["enrichments"][symbol]
        assert row["industry_source"] == "iWenCai 所属申万行业"
        assert row["industry_standard"] == "SW2021"
        assert row["industry_level2"]["name"] == row["sector"]
        assert 0 <= len(row["theme_concepts"]) <= 3
        assert row["sector_with_theme"].startswith(row["sector"])
    for symbol, row in tracking["stocks"].items():
        assert row["industry_source"] == "iWenCai 所属申万行业", symbol
        assert row["industry_standard"] == "SW2021", symbol
        assert row["industry_level2"]["name"] == row["industry"], symbol
        assert "--" not in row["industry"] and "||" not in row["industry"], symbol
        assert 0 <= len(row["theme_concepts"]) <= 3, symbol
        assert row["industry_display"].startswith(row["industry"]), symbol


def test_transactional_write_restores_all_targets_when_second_write_fails(tmp_path, monkeypatch):
    mod = load_module()
    paths = [tmp_path / "current.json", tmp_path / "tracking.json", tmp_path / "cache.json"]
    originals = [b'{"old":1}\n', b'{"old":2}\n', b'{"old":3}\n']
    for path, content in zip(paths, originals):
        path.write_bytes(content)
    real_write = mod.atomic_write_json
    calls = 0

    def fail_second(path, payload):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("forced failure")
        real_write(path, payload)

    monkeypatch.setattr(mod, "atomic_write_json", fail_second)
    with pytest.raises(RuntimeError, match="forced failure"):
        mod.transactional_write_json([
            (paths[0], {"new": 1}),
            (paths[1], {"new": 2}),
            (paths[2], {"new": 3}),
        ])
    assert [path.read_bytes() for path in paths] == originals


def test_tracking_page_renders_preformatted_industry_display():
    page = (ROOT / "src/pages/rolling/low-chip/tracking.astro").read_text(encoding="utf-8")
    assert "rec.industry_display || rec.industry" in page


def test_ftshare_industry_cache_is_private():
    middleware = (ROOT / "functions/_middleware.js").read_text(encoding="utf-8")
    assert "/data/model-lab/ftshare-sw-industry-map.json" in middleware

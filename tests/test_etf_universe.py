import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_canonical_etf_universe_tiers_and_uniqueness():
    payload = json.loads((ROOT / "data/etf-universe.json").read_text(encoding="utf-8"))
    items = payload["items"]
    assert payload["counts"] == {"total": 121, "formal": 91, "research": 30, "rotation": 89, "defense": 2}
    assert len(items) == 121
    assert len({x["code"] for x in items}) == 121
    assert sum(x["tier"] == "formal" for x in items) == 91
    assert sum(x["tier"] == "research" for x in items) == 30
    assert sum(x["asset_layer"] == "defense" for x in items) == 2
    assert {x["code"] for x in items if x["asset_layer"] == "defense"} == {"511260", "511110"}


def test_generator_uses_formal_pool_only():
    path = ROOT / "scripts/generate_garden_pool.py"
    spec = importlib.util.spec_from_file_location("garden_pool_91", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert len(module.GARDEN_POOL) == 91
    assert len(module.RESEARCH_POOL) == 121
    assert all(x["tier"] == "formal" for x in module.GARDEN_POOL)
    assert {x["code"] for x in module.GARDEN_POOL}.issubset({x["code"] for x in module.RESEARCH_POOL})

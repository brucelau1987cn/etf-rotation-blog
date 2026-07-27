from pathlib import Path
import tomllib


def test_wrangler_binds_rolling_kv_namespace():
    config = tomllib.loads((Path(__file__).parents[1] / "wrangler.toml").read_text(encoding="utf-8"))
    bindings = {item["binding"]: item["id"] for item in config.get("kv_namespaces", [])}
    assert bindings["ROLLING_KV"] == "186d0f7ceb484c47b8c6055fe0c7a724"

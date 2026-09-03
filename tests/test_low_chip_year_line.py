"""attach_low_chip_year_line.py 的单元测试：年线排最后、fail-soft、写回 periods["year"]。"""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "attach_year", ROOT / "scripts/attach_low_chip_year_line.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write(data_file: Path, intersection):
    data_file.write_text(json.dumps({
        "data_as_of": "2026-09-02",
        "periods": {"year": []},
        "counts": {},
        "intersection": intersection,
    }), encoding="utf-8")


def test_year_line_skips_on_empty_intersection(tmp_path, monkeypatch):
    mod = _load()
    data_file = tmp_path / "a-low-chip-stocks.json"
    monkeypatch.setattr(mod.base, "DATA", data_file)
    _write(data_file, [])
    assert mod.main() == 0


def test_year_line_fails_soft_on_fetch_error(tmp_path, monkeypatch):
    mod = _load()
    data_file = tmp_path / "a-low-chip-stocks.json"
    monkeypatch.setattr(mod.base, "DATA", data_file)
    _write(data_file, ["000001.SZ"])

    def boom(codes):
        raise RuntimeError("quota exhausted")

    monkeypatch.setattr(mod.base, "fetch_year_overlay", boom)
    assert mod.main() == 0  # fail-soft：年线失败不崩溃、不阻塞


def test_year_line_writes_year_period_and_count(tmp_path, monkeypatch):
    mod = _load()
    data_file = tmp_path / "a-low-chip-stocks.json"
    monkeypatch.setattr(mod.base, "DATA", data_file)
    _write(data_file, ["000001.SZ", "000002.SZ"])

    def fake_fetch(codes):
        return [{"symbol": c, "name": "测试", "value": 1.5} for c in codes]

    monkeypatch.setattr(mod.base, "fetch_year_overlay", fake_fetch)
    assert mod.main() == 0

    out = json.loads(data_file.read_text(encoding="utf-8"))
    assert len(out["periods"]["year"]) == 2
    assert out["counts"]["year"] == 2
    assert {r["symbol"] for r in out["periods"]["year"]} == {"000001.SZ", "000002.SZ"}

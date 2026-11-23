"""Offline builds check artifact consistency; publication checks managed coverage."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FROZEN_NOW = "2026-11-19T00:00:00+08:00"
sys.path.insert(0, str(ROOT / "scripts"))
import futures_compass_data as data

spec = importlib.util.spec_from_file_location("futures_build_validator", ROOT / "scripts/validate_futures_compass.py")
assert spec and spec.loader
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)


def run_validator(monkeypatch, tmp_path, capsys, payload, *args):
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(payload))
    monkeypatch.setattr(sys, "argv", ["validate", "--path", str(path), "--now", FROZEN_NOW, *args])
    code = validator.main()
    return code, json.loads(capsys.readouterr().out)


@pytest.fixture
def snapshot():
    payload = json.loads((ROOT / "public/data/futures-compass.json").read_text())
    # Keep committed membership/content; pin the freshness input before mutations.
    payload["generated_at"] = FROZEN_NOW
    return payload


@pytest.mark.parametrize("source_generated_at", ["2020-01-01T00:00:00+08:00", "2030-01-01T00:00:00+08:00"])
def test_snapshot_fixture_freezes_committed_date_drift(monkeypatch, request, tmp_path, capsys, source_generated_at):
    source = ROOT / "public/data/futures-compass.json"
    committed = json.loads(source.read_text())
    committed["generated_at"] = source_generated_at
    read_text = Path.read_text

    def read_with_changed_date(path, *args, **kwargs):
        if path == source:
            return json.dumps(committed)
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_with_changed_date)
    snapshot = request.getfixturevalue("snapshot")
    assert snapshot["generated_at"] == "2026-11-19T00:00:00+08:00"
    code, report = run_validator(monkeypatch, tmp_path, capsys, snapshot, "--watchlist", "config/futures-watchlist.json")
    assert code == 0
    assert report["errors"] == []


@pytest.mark.parametrize("bad", ["lc", " LC", "LC ", "", " ", 12, None, [], {}, "ＬＣ"])
@pytest.mark.parametrize("managed", [False, True])
def test_invalid_literal_config_is_structured_and_preserves_export(monkeypatch, tmp_path, capsys, snapshot, bad, managed):
    rows = [{"code": row["code"]} for row in snapshot["items"]]
    rows[0] = {} if bad is None else {"code": bad}
    target = tmp_path / "export.json"
    target.write_bytes(b"original bytes\n")
    args = ["--export-watchlist", str(target)]
    if managed:
        monkeypatch.setattr(data, "load_watchlist", lambda: rows)
    else:
        config = tmp_path / "config.json"
        config.write_text(json.dumps(rows))
        args += ["--watchlist", str(config)]
    code, report = run_validator(monkeypatch, tmp_path, capsys, snapshot, *args)
    assert code == 2
    assert report["status"] == "error"
    assert any("code" in error for error in report["errors"])
    assert target.read_bytes() == b"original bytes\n"
    with pytest.raises(ValueError, match="code"):
        data.export_watchlist(snapshot, target, watchlist=rows)


@pytest.mark.parametrize("error", [ValueError("export rejected"), OSError("disk unavailable")])
def test_export_errors_are_structured_and_preserve_bytes(monkeypatch, tmp_path, capsys, snapshot, error):
    rows = [{"code": row["code"]} for row in snapshot["items"]]
    monkeypatch.setattr(data, "load_watchlist", lambda: rows)
    target = tmp_path / "export.json"
    target.write_bytes(b"original bytes\n")
    def fail(*args, **kwargs):
        raise error
    if isinstance(error, OSError):
        monkeypatch.setattr(data.os, "replace", fail)
    else:
        monkeypatch.setattr(data, "export_watchlist", fail)
    code, report = run_validator(monkeypatch, tmp_path, capsys, snapshot, "--export-watchlist", str(target))
    assert code == 2
    assert report["status"] == "error"
    assert str(error) in report["errors"][0]
    assert target.read_bytes() == b"original bytes\n"


def test_build_validates_committed_dynamic_watchlist_offline(monkeypatch, tmp_path, capsys, snapshot):
    assert len(snapshot["items"]) > len(data.DEFAULT_WATCHLIST)
    monkeypatch.setattr(data, "load_watchlist", lambda: pytest.fail("offline build requested managed watchlist"))
    code, report = run_validator(monkeypatch, tmp_path, capsys, snapshot, "--watchlist", "config/futures-watchlist.json")
    assert code == 0
    assert report["errors"] == []


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "ranking", "stale", "future", "core"])
def test_offline_mode_preserves_snapshot_gates(monkeypatch, tmp_path, capsys, snapshot, mutation):
    if mutation == "missing":
        snapshot["items"].pop()
    elif mutation == "duplicate":
        snapshot["items"][-1] = snapshot["items"][0]
    elif mutation == "ranking":
        snapshot["summary"]["ranking"].pop()
    elif mutation == "stale":
        snapshot["generated_at"] = "2020-01-01T00:00:00+08:00"
    elif mutation == "future":
        snapshot["generated_at"] = "2030-01-01T00:00:00+08:00"
    else:
        snapshot["items"][0].pop("price")
    code, report = run_validator(monkeypatch, tmp_path, capsys, snapshot, "--watchlist", "config/futures-watchlist.json")
    assert code == 2
    assert report["errors"]
    if mutation == "stale":
        assert report["errors"] == ["futures snapshot is older than 259200 seconds"]
    elif mutation == "future":
        assert report["errors"] == ["futures snapshot generated_at is in the future"]


def test_explicit_and_managed_config_detect_coherent_omission(monkeypatch, tmp_path, capsys, snapshot):
    authoritative = [{"code": row["code"]} for row in snapshot["items"]]
    removed = snapshot["items"].pop()["code"]
    snapshot["summary"]["ranking"].remove(removed)
    snapshot["count"] -= 1
    snapshot["expected_count"] -= 1
    monkeypatch.setattr(data, "load_watchlist", lambda: authoritative)
    code, report = run_validator(monkeypatch, tmp_path, capsys, snapshot)
    assert code == 2
    assert any("watchlist mismatch" in error for error in report["errors"])
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"items": authoritative}))
    monkeypatch.setattr(data, "load_watchlist", lambda: pytest.fail("explicit config must win"))
    code, report = run_validator(monkeypatch, tmp_path, capsys, snapshot, "--watchlist", str(config))
    assert code == 2
    assert any("watchlist mismatch" in error for error in report["errors"])
    # Independent versioned configuration preserves the complete denominator.
    code, report = run_validator(monkeypatch, tmp_path, capsys, snapshot, "--watchlist", "config/futures-watchlist.json")
    assert code == 2
    assert any("watchlist mismatch" in error for error in report["errors"])


@pytest.mark.parametrize("config", [[], {}, {"items": "bad"}, ["LC"], [{"code": "LC"}, {"code": "LC"}]])
def test_explicit_config_fails_closed(monkeypatch, tmp_path, capsys, snapshot, config):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config))
    monkeypatch.setattr(data, "load_watchlist", lambda: pytest.fail("invalid config must fail closed"))
    code, report = run_validator(monkeypatch, tmp_path, capsys, snapshot, "--watchlist", str(path))
    assert code == 2
    assert report["errors"]


def test_import_has_no_network_or_cache_side_effects():
    probe = """
import pathlib, socket, sys
sys.path.insert(0, sys.argv[1])
def blocked(*args, **kwargs):
    raise AssertionError('import attempted I/O')
socket.socket.connect = blocked
pathlib.Path.mkdir = blocked
import futures_compass_data
"""
    result = subprocess.run([sys.executable, "-c", probe, str(ROOT / "scripts")], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_build_explicitly_selects_offline_mode(monkeypatch):
    import project_runtime
    calls = []
    monkeypatch.setattr(project_runtime, "run", lambda command, **kwargs: calls.append(command))
    project_runtime.contract_chain_run({})
    assert calls[0] == [project_runtime.BUILD_PYTHON, "scripts/validate_futures_compass.py", "--watchlist", "config/futures-watchlist.json"]

def test_export_binds_managed_rows_and_publisher_scope(monkeypatch, tmp_path, snapshot):
    import publish_futures_compass as publisher
    rows = [{"code": row["code"]} for row in snapshot["items"]]
    monkeypatch.setattr(data, "load_watchlist", lambda: rows)
    target = tmp_path / "watchlist.json"
    data.export_watchlist(snapshot, target)
    assert json.loads(target.read_text())["items"] == rows
    assert "config/futures-watchlist.json" in publisher.PUBLISH_FILES
    snapshot["items"].pop()
    with pytest.raises(ValueError, match="watchlist mismatch"):
        data.export_watchlist(snapshot, target)
    assert json.loads(target.read_text())["items"] == rows

def test_cli_exports_same_managed_configuration_once(monkeypatch, tmp_path, capsys, snapshot):
    rows = json.loads((ROOT / "config/futures-watchlist.json").read_text())["items"]
    calls = []
    def managed():
        calls.append(True)
        return rows
    monkeypatch.setattr(data, "load_watchlist", managed)
    target = tmp_path / "export.json"
    code, report = run_validator(monkeypatch, tmp_path, capsys, snapshot, "--export-watchlist", str(target))
    assert code == 0
    assert calls == [True]
    assert json.loads(target.read_text())["items"] == rows


@pytest.mark.parametrize("change", ["add", "disable"])
def test_export_tracks_managed_membership_changes(monkeypatch, tmp_path, snapshot, change):
    import copy
    rows = json.loads((ROOT / "config/futures-watchlist.json").read_text())["items"]
    if change == "disable":
        removed = rows.pop()["code"]
        snapshot["items"] = [r for r in snapshot["items"] if r["code"] != removed]
        snapshot["summary"]["ranking"].remove(removed)
    else:
        rows.append({"code": "TEST"})
        item = copy.deepcopy(snapshot["items"][0])
        item["code"] = "TEST"
        snapshot["items"].append(item)
        snapshot["summary"]["ranking"].append("TEST")
    snapshot["count"] = snapshot["expected_count"] = len(rows)
    monkeypatch.setattr(data, "load_watchlist", lambda: rows)
    target = tmp_path / "export.json"
    data.export_watchlist(snapshot, target)
    assert json.loads(target.read_text())["items"] == rows

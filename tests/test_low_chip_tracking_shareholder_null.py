"""Offline regressions for nullable entry-time shareholder evidence."""
import importlib.util
import json
from pathlib import Path
import socket
import subprocess

import pytest


@pytest.mark.parametrize("evidence, expected", [
    ({}, None),
    ({"quality_shareholder": None, "institutional_shareholder": None}, None),
    ({"quality_shareholder": False, "institutional_shareholder": False}, False),
    ({"quality_shareholder": True, "institutional_shareholder": True}, True),
    ({"quality_shareholder": 0, "institutional_shareholder": 0}, False),
    ({"quality_shareholder": 1, "institutional_shareholder": 1}, True),
])
def test_entry_shareholder_status_survives_tracking_json(monkeypatch, tmp_path, evidence, expected):
    path = Path(__file__).resolve().parents[1] / "scripts/update_low_chip_tracking.py"
    spec = importlib.util.spec_from_file_location("tracking_nullable_shareholders", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    def forbidden(*args, **kwargs):
        raise AssertionError("Unexpected network, process, or production I/O")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    for name in ("tencent_daily", "baostock_daily_batch", "chip_list_profit_ratios", "iwencai_profit_ratios"):
        monkeypatch.setattr(mod, name, forbidden)
    monkeypatch.setattr(mod.urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(mod.time, "sleep", forbidden)
    monkeypatch.setattr(mod, "HISTORY_DIR", tmp_path)
    monkeypatch.setattr(mod, "DATA", tmp_path / "tracking.json")
    monkeypatch.setattr(mod, "load_history_dates", lambda: {"600000.SH": ["2026-01-01"]})
    monkeypatch.setattr(mod, "load_existing", lambda: {"stocks": {"600000.SH": {
        "first_seen": "2026-01-01",
        "daily": [{"date": f"2026-01-{day:02}", "close": 10.0} for day in range(1, 22)],
    }}})
    monkeypatch.setattr(mod, "resolve_entry_snapshot", lambda *args: tmp_path / "snapshot.json")
    monkeypatch.setattr(mod, "load_entry_enrichment", lambda *args: (evidence, {}))
    monkeypatch.setattr(mod, "fetch_current_year_profit", lambda symbols: {})
    written = {}

    def capture_json(target, payload):
        assert target == tmp_path / "tracking.json"
        written.update(json.loads(json.dumps(payload)))

    monkeypatch.setattr(mod, "atomic_write_json", capture_json)
    assert mod.main() == 0
    features = written["stocks"]["600000.SH"]["entry_features"]
    assert features["quality_shareholder"] is expected
    assert features["institutional_shareholder"] is expected
    assert features["quality_shareholder_names"] == []
    assert features["institutional_shareholder_names"] == []

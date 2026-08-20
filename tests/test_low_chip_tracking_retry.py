import importlib.util
import json
import sys
import urllib.error
from io import BytesIO
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "update_low_chip_tracking.py"


def load_module():
    spec = importlib.util.spec_from_file_location("tracking_retry_contract", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


def valid_payload():
    return {
        "code": 0,
        "msg": "",
        "data": {
            "sz000012": {
                "qfqday": [
                    ["2026-08-19", "3.73", "3.73", "3.80", "3.70", "100"],
                    ["2026-08-20", "3.74", "3.75", "3.77", "3.72", "120"],
                ]
            }
        },
    }


def test_tencent_daily_retries_business_error_with_string_data():
    module = load_module()
    calls = []
    sleeps = []

    def opener(_request, timeout=20):
        calls.append(timeout)
        if len(calls) == 1:
            return Response({
                "code": 11,
                "msg": "mysql connect failed",
                "data": "",
            })
        return Response(valid_payload())

    bars = module.tencent_daily(
        "000012.SZ",
        "2026-08-19",
        "2026-08-20",
        opener=opener,
        sleeper=sleeps.append,
    )

    assert [bar["date"] for bar in bars] == ["2026-08-19", "2026-08-20"]
    assert len(calls) == 2
    assert sleeps == [1.0]


def test_tencent_daily_retries_transport_timeout():
    module = load_module()
    calls = []
    sleeps = []

    def opener(request, timeout=20):
        calls.append(timeout)
        if len(calls) == 1:
            raise TimeoutError("read timed out")
        return Response(valid_payload())

    bars = module.tencent_daily(
        "000012.SZ",
        "2026-08-19",
        "2026-08-20",
        opener=opener,
        sleeper=sleeps.append,
    )

    assert len(bars) == 2
    assert len(calls) == 2
    assert sleeps == [1.0]


def test_tencent_daily_fails_with_explicit_upstream_error_after_retry_budget():
    module = load_module()
    sleeps = []

    with pytest.raises(RuntimeError, match=r"Tencent fqkline unavailable for 000012\.SZ.*code=11.*mysql connect failed"):
        module.tencent_daily(
            "000012.SZ",
            "2026-08-19",
            "2026-08-20",
            opener=lambda *_args, **_kwargs: Response({"code": 11, "msg": "mysql connect failed", "data": ""}),
            sleeper=sleeps.append,
        )

    assert sleeps == [1.0, 2.0, 5.0]


def test_tencent_daily_retries_empty_or_malformed_bar_payload():
    module = load_module()
    responses = [
        {"code": 0, "msg": "", "data": {"sz000012": {"qfqday": []}}},
        {"code": 0, "msg": "", "data": {"sz000012": {"qfqday": "bad"}}},
        {"code": 0, "msg": "", "data": {"sz000012": {"qfqday": [
            ["2026-08-19", "3.73", "not-a-number", "3.80", "3.70", "100"],
        ]}}},
        valid_payload(),
    ]
    sleeps = []

    bars = module.tencent_daily(
        "000012.SZ",
        "2026-08-19",
        "2026-08-20",
        opener=lambda *_args, **_kwargs: Response(responses.pop(0)),
        sleeper=sleeps.append,
    )

    assert len(bars) == 2
    assert sleeps == [1.0, 2.0, 5.0]


def test_tencent_daily_retries_payload_with_no_bar_in_requested_window():
    module = load_module()
    stale = {
        "code": 0,
        "msg": "",
        "data": {"sz000012": {"qfqday": [
            ["2026-08-18", "3.70", "3.71", "3.72", "3.69", "90"],
        ]}},
    }
    responses = [stale, valid_payload()]
    sleeps = []

    bars = module.tencent_daily(
        "000012.SZ",
        "2026-08-19",
        "2026-08-20",
        opener=lambda *_args, **_kwargs: Response(responses.pop(0)),
        sleeper=sleeps.append,
    )

    assert [bar["date"] for bar in bars] == ["2026-08-19", "2026-08-20"]
    assert sleeps == [1.0]


def test_tracking_main_preserves_existing_bars_when_tencent_returns_nothing(tmp_path, monkeypatch):
    """Regression: empty Tencent response must not erase already-saved history rows."""
    module = load_module()
    history = tmp_path / "low-chip-history"
    history.mkdir()
    (history / "2026-01-02.json").write_text(json.dumps({
        "enrichments": {"000012.SZ": {"quality_shareholder": True, "quality_shareholder_names": [], "institutional_shareholder": True, "institutional_shareholder_names": []}},
        "periods": {},
    }))
    monkeypatch.setattr(module, "HISTORY_DIR", history)
    monkeypatch.setattr(module, "DATA", tmp_path / "tracking.json")
    monkeypatch.setattr(module, "load_history_dates", lambda: {"000012.SZ": ["2026-01-02"]})
    module.DATA.write_text(json.dumps({
        "schema_version": "low-chip-tracking-v1", "generated_at": "2026-01-02",
        "stocks": {"000012.SZ": {"name": "万科A", "first_seen": "2026-01-02", "last_seen": "2026-01-02",
                    "industry": "地产", "daily": [
                        {"date": "2026-01-02", "close": 10.0, "change_pct": 0.0, "profit_ratio": 0.5},
                        {"date": "2026-01-03", "close": 10.1, "change_pct": 1.0, "profit_ratio": 0.6},
                    ]}},
    }))
    def fake_no_bars(*_a, **__kw):
        return []
    monkeypatch.setattr(module, "tencent_daily", fake_no_bars)
    monkeypatch.setattr(module, "iwencai_profit_ratio", lambda *_a, **__kw: None)
    rc = module.main()
    result = json.loads(module.DATA.read_text())
    existing = result["stocks"]["000012.SZ"]["daily"]
    assert len(existing) == 2, f"Expected 2 preserved rows, got {len(existing)}: {existing}"


def test_tracking_main_skips_tencent_for_completed_21_bar_records(tmp_path, monkeypatch):
    module = load_module()
    history_dir = tmp_path / "history"
    history_dir.mkdir()
    snapshot = {
        "intersection": ["600000.SH"],
        "enrichments": {"600000.SH": {
            "industry": "银行",
            "financials": {"roe": 31, "net_margin": 26, "cash_profit_ratio": 21, "gross_margin": 16, "debt_ratio": 9},
        }},
        "periods": {"week": [{"symbol": "600000.SH", "name": "浦发银行"}]},
    }
    (history_dir / "2026-01-01.json").write_text(json.dumps(snapshot), encoding="utf-8")
    bars = [
        {"date": f"2026-01-{day:02d}", "close": float(day), "change_pct": 0.0, "profit_ratio": 1.0}
        for day in range(1, 22)
    ]
    data_path = tmp_path / "tracking.json"
    data_path.write_text(json.dumps({
        "schema_version": "low-chip-tracking-v1",
        "generated_at": "2026-01-21T16:00:00+08:00",
        "stocks": {"600000.SH": {
            "name": "浦发银行",
            "first_seen": "2026-01-01",
            "last_seen": "2026-01-01",
            "industry": "银行",
            "daily": bars,
            "tracking_complete": True,
        }},
    }), encoding="utf-8")

    monkeypatch.setattr(module, "HISTORY_DIR", history_dir)
    monkeypatch.setattr(module, "DATA", data_path)
    monkeypatch.setattr(module, "tencent_daily", lambda *_args, **_kwargs: pytest.fail("completed record queried Tencent"))

    assert module.main() == 0
    result = json.loads(data_path.read_text(encoding="utf-8"))
    assert len(result["stocks"]["600000.SH"]["daily"]) == 21
    assert result["stocks"]["600000.SH"]["tracking_complete"] is True

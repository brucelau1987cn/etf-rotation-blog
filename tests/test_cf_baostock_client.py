import json
import urllib.error
import urllib.parse

import pytest

from scripts.cf_baostock_client import CFBaoStockClient, CFBaoStockError


class Response:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def test_calendar_sends_bearer_and_date_range():
    captured = {}

    def opener(request, timeout):
        captured["url"] = request.full_url
        captured["authorization"] = request.get_header("Authorization")
        captured["timeout"] = timeout
        return Response({"ok": True, "source": "baostock", "records": [
            {"date": "2026-09-14", "is_trading_day": False},
            {"date": "2026-09-15", "is_trading_day": True},
        ]})

    client = CFBaoStockClient("https://example.test/", "secret", opener=opener, timeout=7)
    rows = client.calendar("2026-09-14", "2026-09-15")

    assert captured == {
        "url": "https://example.test/api/internal/v1/baostock/calendar?start=2026-09-14&end=2026-09-15",
        "authorization": "Bearer secret",
        "timeout": 7,
    }
    assert rows[-1] == {"trade_date": "2026-09-15", "is_open": True}
    assert client.is_trading_day("2026-09-15") is True


def test_klines_posts_explicit_qfq_contract_and_normalizes_symbols():
    captured = {}

    def opener(request, timeout):
        captured["method"] = request.get_method()
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data) if request.data else None
        captured["authorization"] = request.get_header("Authorization")
        return Response({"ok": True, "source": "baostock", "results": [{
            "symbol": "sz.000858",
            "records": [
                {"date": "2026-09-14", "close": "10.50", "tradestatus": "1"},
            ],
        }]})

    client = CFBaoStockClient("https://example.test", "secret", opener=opener)
    result = client.klines(["000858.SZ"], "2026-09-01", "2026-09-15")

    assert captured["method"] == "GET"
    assert captured["authorization"] == "Bearer secret"
    parsed = urllib.parse.urlparse(captured["url"])
    assert parsed.path == "/api/internal/v1/baostock/qfq"
    assert urllib.parse.parse_qs(parsed.query) == {
        "symbols": ["000858.SZ"],
        "start": ["2026-09-01"],
        "end": ["2026-09-15"],
        "fields": ["date,close,volume,amount,turn,tradestatus"],
    }
    assert result == {"000858.SZ": [
        {"date": "2026-09-14", "close": "10.50", "tradestatus": "1"},
    ]}


def test_klines_enforces_gateway_batch_limit_before_network():
    client = CFBaoStockClient(
        "https://example.test", "secret",
        opener=lambda *_args, **_kwargs: pytest.fail("network called"),
    )
    with pytest.raises(ValueError, match="at most 5"):
        client.klines([f"{code:06d}.SZ" for code in range(6)], "2026-09-01", "2026-09-15")


def test_gateway_errors_are_sanitized_and_fail_closed():
    def opener(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 503, "down", {}, None)

    client = CFBaoStockClient("https://example.test", "top-secret", opener=opener)
    with pytest.raises(CFBaoStockError) as error:
        client.calendar("2026-09-15", "2026-09-15")
    assert "503" in str(error.value)
    assert "top-secret" not in str(error.value)


def test_from_env_requires_url_and_token(monkeypatch):
    monkeypatch.delenv("CF_BAOSTOCK_BASE_URL", raising=False)
    monkeypatch.delenv("CF_BAOSTOCK_TOKEN", raising=False)
    with pytest.raises(CFBaoStockError, match="CF_BAOSTOCK_BASE_URL"):
        CFBaoStockClient.from_env()


    with pytest.raises(CFBaoStockError, match="HTTPS origin"):
        CFBaoStockClient("https://example.test/path", "secret")


def test_klines_rejects_partial_unknown_and_duplicate_symbols():
    payloads = [
        {"ok": True, "source": "baostock", "results": []},
        {"ok": True, "source": "baostock", "results": [{"symbol": "sh.600000", "records": []}]},
        {"ok": True, "source": "baostock", "results": [
            {"symbol": "sz.000858", "records": []},
            {"symbol": "000858.SZ", "records": []},
        ]},
    ]
    for payload in payloads:
        client = CFBaoStockClient("https://example.test", "secret", opener=lambda *_args, p=payload, **_kwargs: Response(p))
        with pytest.raises(CFBaoStockError):
            client.klines(["000858.SZ"], "2026-09-01", "2026-09-15")


def test_calendar_rejects_missing_ok_or_wrong_source():
    for payload in [
        {"source": "baostock", "records": []},
        {"ok": True, "source": "other", "records": []},
    ]:
        client = CFBaoStockClient("https://example.test", "secret", opener=lambda *_args, p=payload, **_kwargs: Response(p))
        with pytest.raises(CFBaoStockError):
            client.calendar("2026-09-15", "2026-09-15")


def test_calendar_helper_prefers_cf_and_falls_back_to_legacy(monkeypatch):
    from scripts import check_a_share_cron_gate as gate

    monkeypatch.setenv("CF_BAOSTOCK_BASE_URL", "https://example.test")
    monkeypatch.setenv("CF_BAOSTOCK_TOKEN", "secret")
    monkeypatch.setattr(gate, "public_calendar_trading_day", lambda day: None)
    monkeypatch.setattr(gate.CFBaoStockClient, "is_trading_day", lambda self, day: True)
    monkeypatch.setattr(gate, "baostock_trading_day", lambda day: pytest.fail("legacy called"))
    assert gate.is_trading_day("2026-09-15") == (True, "cf_baostock")

    monkeypatch.setattr(gate.CFBaoStockClient, "is_trading_day", lambda self, day: (_ for _ in ()).throw(CFBaoStockError("down")))
    monkeypatch.setattr(gate, "public_calendar_trading_day", lambda day: None)
    monkeypatch.setattr(gate, "baostock_trading_day", lambda day: False)
    assert gate.is_trading_day("2026-09-14") == (False, "baostock")


def test_tracking_cf_klines_maps_rows_and_change_pct(monkeypatch):
    import importlib.util
    from pathlib import Path

    script = Path(__file__).resolve().parents[1] / "scripts/update_low_chip_tracking.py"
    spec = importlib.util.spec_from_file_location("tracking_cf_contract", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class Client:
        def klines(self, symbols, start, end):
            assert symbols == ["000858.SZ"]
            return {"000858.SZ": [
                {"date": "2026-09-09", "close": "10.00", "tradestatus": "1"},
                {"date": "2026-09-10", "close": "10.50", "tradestatus": "1"},
                {"date": "2026-09-11", "close": "10.60", "tradestatus": "0"},
                {"date": "2026-09-14", "close": "10.92", "tradestatus": "1"},
            ]}

    bars = module.cf_baostock_daily("000858.SZ", "2026-09-10", "2026-09-15", client=Client())
    assert [bar["date"] for bar in bars] == ["2026-09-10", "2026-09-14"]
    assert bars[0]["change_pct"] == 5.0
    assert bars[1]["change_pct"] == 4.0

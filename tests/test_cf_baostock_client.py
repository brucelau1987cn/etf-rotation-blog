import json
from datetime import date, timedelta
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
        return Response({"ok": True, "source": "baostock", "adjust": "qfq",
            "start": "2026-09-01", "end": "2026-09-15",
            "fields": ["date", "close", "volume", "amount", "turn", "tradestatus"],
            "symbol_count": 1, "count": 1, "results": [{
            "symbol": "sz.000858",
            "count": 1,
            "records": [
                {"date": "2026-09-14", "close": "10.50", "volume": 1000,
                 "amount": 10500, "turn": 1.2, "tradestatus": "1"},
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
        {"date": "2026-09-14", "close": "10.50", "volume": 1000,
         "amount": 10500, "turn": 1.2, "tradestatus": "1"},
    ]}


def test_klines_accepts_explicit_fields_and_splits_long_ranges():
    calls = []
    fields = ("date", "open", "high", "low", "close", "turn", "tradestatus")

    def opener(request, timeout):
        query = urllib.parse.parse_qs(urllib.parse.urlparse(request.full_url).query)
        start = query["start"][0]
        end = query["end"][0]
        calls.append((start, end, query["fields"][0]))
        records = [{"date": start, "open": 10, "high": 11, "low": 9, "close": 10.5,
                    "turn": 1.2, "tradestatus": "1"}]
        return Response({
            "ok": True, "source": "baostock", "adjust": "qfq", "start": start, "end": end,
            "fields": list(fields), "symbol_count": 1, "count": 1,
            "results": [{"symbol": "sz.000001", "count": 1, "records": records}],
        })

    client = CFBaoStockClient("https://example.test", "secret", opener=opener)
    result = client.klines(["000001.SZ"], "2022-01-01", "2026-09-15", fields=fields)

    assert len(calls) == 5
    assert calls[0] == ("2022-01-01", "2023-01-01", ",".join(fields))
    assert calls[-1][1] == "2026-09-15"
    for previous, current in zip(calls, calls[1:]):
        assert date.fromisoformat(current[0]) == date.fromisoformat(previous[1]) + timedelta(days=1)
    assert all((date.fromisoformat(end) - date.fromisoformat(start)).days + 1 <= 366 for start, end, _ in calls)
    assert [row["date"] for row in result["000001.SZ"]] == [call[0] for call in calls]


@pytest.mark.parametrize("mutate,match", [
    (lambda payload: payload.update(adjust="hfq"), "adjust"),
    (lambda payload: payload.update(start="2026-09-02"), "start"),
    (lambda payload: payload.update(end="2026-09-14"), "end"),
    (lambda payload: payload.update(fields=["date", "close"]), "fields"),
    (lambda payload: payload.update(symbol_count=2), "symbol_count"),
    (lambda payload: payload.update(count=2), "count"),
    (lambda payload: payload["results"][0].update(count=2), "count"),
])
def test_klines_rejects_inconsistent_window_metadata(mutate, match):
    payload = {
        "ok": True, "source": "baostock", "adjust": "qfq",
        "start": "2026-09-01", "end": "2026-09-15",
        "fields": ["date", "close"], "symbol_count": 1, "count": 1,
        "results": [{"symbol": "sz.000001", "count": 1,
                     "records": [{"date": "2026-09-14", "close": 10}]}],
    }
    if match == "fields":
        payload["fields"] = ["date", "close", "turn"]
    else:
        mutate(payload)
    client = CFBaoStockClient("https://example.test", "secret", opener=lambda *_a, **_k: Response(payload))
    with pytest.raises(CFBaoStockError, match=match):
        client.klines(["000001.SZ"], "2026-09-01", "2026-09-15", fields=("date", "close"))


@pytest.mark.parametrize("records,match", [
    ([{"date": "2026-09-14"}], "field"),
    ([{"date": "2026-09-14", "close": float("nan")}], "finite"),
    ([{"date": "2026-09-16", "close": 10}], "date range"),
    ([{"date": "2026-09-14", "close": 10}, {"date": "2026-09-14", "close": 11}], "unique"),
    ([{"date": "2026-09-14", "close": 10}, {"date": "2026-09-13", "close": 11}], "sorted"),
])
def test_klines_rejects_invalid_records(records, match):
    payload = {
        "ok": True, "source": "baostock", "adjust": "qfq",
        "start": "2026-09-01", "end": "2026-09-15",
        "fields": ["date", "close"], "symbol_count": 1, "count": len(records),
        "results": [{"symbol": "sz.000001", "count": len(records), "records": records}],
    }
    client = CFBaoStockClient("https://example.test", "secret", opener=lambda *_a, **_k: Response(payload))
    with pytest.raises(CFBaoStockError, match=match):
        client.klines(["000001.SZ"], "2026-09-01", "2026-09-15", fields=("date", "close"))


def test_klines_rejects_illegal_ohlc():
    fields = ("date", "open", "high", "low", "close")
    records = [{"date": "2026-09-14", "open": 10, "high": 9, "low": 8, "close": 10.5}]
    payload = {"ok": True, "source": "baostock", "adjust": "qfq",
               "start": "2026-09-01", "end": "2026-09-15", "fields": list(fields),
               "symbol_count": 1, "count": 1,
               "results": [{"symbol": "sz.000001", "count": 1, "records": records}]}
    client = CFBaoStockClient("https://example.test", "secret", opener=lambda *_a, **_k: Response(payload))
    with pytest.raises(CFBaoStockError, match="OHLC"):
        client.klines(["000001.SZ"], "2026-09-01", "2026-09-15", fields=fields)


def test_klines_enforces_gateway_batch_limit_before_network():
    client = CFBaoStockClient(
        "https://example.test", "secret",
        opener=lambda *_args, **_kwargs: pytest.fail("network called"),
    )
    with pytest.raises(ValueError, match="at most 5"):
        client.klines([f"{code:06d}.SZ" for code in range(6)], "2026-09-01", "2026-09-15")


@pytest.mark.parametrize("status", [429, 500, 501, 502, 505, 599])
def test_gateway_retries_transient_http_errors_then_succeeds(status):
    attempts = []
    sleeps = []

    def opener(request, timeout):
        attempts.append(request.full_url)
        if len(attempts) < 3:
            raise urllib.error.HTTPError(request.full_url, status, "transient", {}, None)
        return Response({"ok": True, "source": "baostock", "records": [
            {"date": "2026-09-15", "is_trading_day": True},
        ]})

    client = CFBaoStockClient(
        "https://example.test", "secret", opener=opener,
        retries=2, retry_delays=(0.1, 0.2), sleeper=sleeps.append,
    )
    assert client.is_trading_day("2026-09-15") is True
    assert len(attempts) == 3
    assert sleeps == [0.1, 0.2]


def test_gateway_does_not_retry_authentication_errors():
    attempts = []

    def opener(request, timeout):
        attempts.append(1)
        raise urllib.error.HTTPError(request.full_url, 401, "unauthorized", {}, None)

    client = CFBaoStockClient(
        "https://example.test", "secret", opener=opener,
        retries=2, sleeper=lambda _delay: pytest.fail("slept after auth error"),
    )
    with pytest.raises(CFBaoStockError, match="401"):
        client.calendar("2026-09-15", "2026-09-15")
    assert attempts == [1]


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

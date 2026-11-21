import datetime
import importlib.util
import json
import socket
import subprocess
import urllib.error
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from hermes_test_support import require_local_script


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "update_low_chip_tracking.py"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("tracking test attempted live network or subprocess I/O")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)


def mock_gap_fallbacks(module, monkeypatch):
    monkeypatch.setattr(module, "confirmed_suspensions", lambda _day: {})
    monkeypatch.setattr(module, "ths_daily", lambda *_args, **_kw: [])
    monkeypatch.setattr(module, "baostock_daily_batch", lambda *_args, **_kw: {})


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

    assert sleeps == [1.0, 2.0, 5.0, 1.0, 2.0, 5.0]


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


class RawByteResponse:
    def __init__(self, raw: bytes):
        self.raw = raw

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self.raw


def test_tencent_daily_handles_var_prefix():
    module = load_module()
    raw = b'kline_dayqfq=' + json.dumps(valid_payload()).encode()
    bars = module.tencent_daily(
        "000012.SZ",
        "2026-08-19",
        "2026-08-20",
        opener=lambda *_args, **_kwargs: RawByteResponse(raw),
    )
    assert [bar["date"] for bar in bars] == ["2026-08-19", "2026-08-20"]


def test_tencent_daily_checks_second_host_after_stale_success():
    module = load_module()
    stale = valid_payload()
    stale["data"]["sz000012"]["qfqday"].pop()
    hosts = []

    def opener(request, **kwargs):
        hosts.append(request.host)
        return Response(stale if len(hosts) == 1 else valid_payload())

    bars = module.tencent_daily("000012.SZ", "2026-08-19", "2026-08-20", opener=opener)
    assert hosts == list(module.TENCENT_KLINE_HOSTS)
    assert [bar["date"] for bar in bars] == ["2026-08-19", "2026-08-20"]


@pytest.mark.parametrize("repetitions", [1, 21])
def test_tencent_hosts_keep_history_and_count_distinct_dates(repetitions):
    module = load_module()
    older, latest = valid_payload()["data"]["sz000012"]["qfqday"]
    replies = iter([[older] * repetitions, [older, latest]])
    bars = module.tencent_daily("000012.SZ", older[0], latest[0],
                                opener=lambda *_a, **_k: Response({"code": 0, "data": {
                                    "sz000012": {"qfqday": next(replies)},
                                }}))
    assert [bar["date"] for bar in bars] == [older[0], latest[0]]
    assert bars[-1]["change_pct"] == 0.54


def test_tencent_does_not_join_disjoint_host_histories():
    module = load_module()
    older, latest = valid_payload()["data"]["sz000012"]["qfqday"]
    replies = iter([[older], [latest[:2] + ["1.91"] + latest[3:]]])
    bars = module.tencent_daily("000012.SZ", older[0], latest[0], opener=lambda *_a, **_k: Response({
        "code": 0, "data": {"sz000012": {"qfqday": next(replies)}},
    }))
    assert bars == [{"date": older[0], "close": 3.73, "change_pct": None}]


@pytest.mark.parametrize("primary_has_target", [False, True])
def test_tencent_reports_conflicting_target_trade_and_tries_compatible_mirror(tmp_path, monkeypatch, primary_has_target):
    module = load_module()
    older, latest = valid_payload()["data"]["sz000012"]["qfqday"]
    prior, target, symbol = older[0], latest[0], "000012.SZ"
    stored = {"date": prior, "close": 3.73, "profit_ratio": 12.0}
    monkeypatch.setattr(module, "DATA", tmp_path / "tracking.json")
    monkeypatch.setattr(module, "HISTORY_DIR", tmp_path)
    snapshot = {"intersection": [symbol], "enrichments": {symbol: {}}}
    (tmp_path / f"{prior}.json").write_text(json.dumps(snapshot))
    module.DATA.write_text(json.dumps({"stocks": {symbol: {"first_seen": prior, "daily": [stored]}}}))
    original = module.DATA.read_bytes()
    mock_gap_fallbacks(module, monkeypatch)
    monkeypatch.setattr(module, "time", SimpleNamespace(sleep=lambda _: None))
    monkeypatch.setattr(module, "chip_list_profit_ratios", lambda *_a, **_k: {})
    monkeypatch.setattr(module, "confirmed_suspensions", lambda _day: {symbol: {
        "status": "halted", "target_day": target, "source": "eastmoney_suspend_report", "reason": "全日停牌",
    }})
    conflicting = [older[:2] + ["8.0"] + older[3:], latest]
    replies = iter([conflicting, [older, latest]] if primary_has_target else [[older], conflicting])
    requests = []

    def opener(req, **_kw):
        requests.append(req.host)
        return Response({"code": 0, "data": {"sz000012": {"qfqday": next(replies)}}})

    monkeypatch.setattr(module.urllib.request, "urlopen", opener)
    result = module.main(target_day=target, skip_iwencai=True)
    if primary_has_target:
        assert result == 0
        daily = json.loads(module.DATA.read_text())["stocks"][symbol]["daily"]
        assert daily[0] == stored and daily[-1]["date"] == target
    else:
        assert result != 0
        assert module.DATA.read_bytes() == original
    assert requests == list(module.TENCENT_KLINE_HOSTS)


def test_tencent_stale_history_does_not_restart_retry_budget_on_second_host():
    module = load_module()
    stale = valid_payload()
    stale["data"]["sz000012"]["qfqday"].pop()
    hosts = []

    def opener(request, **kwargs):
        hosts.append(request.host)
        if len(hosts) == 1:
            return Response(stale)
        raise TimeoutError("secondary host unavailable")

    sleeps = []
    bars = module.tencent_daily("000012.SZ", "2026-08-19", "2026-08-20", opener=opener, sleeper=sleeps.append)
    assert hosts == list(module.TENCENT_KLINE_HOSTS)
    assert sleeps == []
    assert [row["date"] for row in bars] == ["2026-08-19"]


def test_suspension_report_requires_explicit_full_session_evidence(monkeypatch):
    module = load_module()
    day = "2026-11-19"
    base = {
        "SECUCODE": "600185.SH", "DATETIME": day + " 00:00:00", "SUSPEND_START_TIME": "2026-11-12 09:30:00",
        "SUSPEND_END_TIME": None, "PREDICT_RESUME_DATE": None,
        "SUSPEND_EXPIRE": "连续停牌", "SUSPEND_REASON": "拟筹划重大资产重组",
    }
    rows = [base,
            dict(base, SECUCODE="600006.SH", SUSPEND_END_TIME=day + " 14:00:00"),
            dict(base, SECUCODE="600022.SH", PREDICT_RESUME_DATE=day + " 00:00:00"),
            dict(base, SECUCODE="600037.SH", SUSPEND_START_TIME=day + " 13:00:00"),
            dict(base, SECUCODE="600051.SH", SUSPEND_REASON=""),
            dict(base, SECUCODE="600052.SH", SUSPEND_START_TIME="2026-11-20 09:30:00"),
            dict(base, SECUCODE="600053.SH", DATETIME="2026-11-18 00:00:00"),
            {k: v for k, v in dict(base, SECUCODE="600054.SH").items() if k != "SUSPEND_START_TIME"}]
    def suspension_response(request, **_kwargs):
        response = Response({"success": True, "result": {"data": rows, "count": len(rows), "pages": 1}})
        response.geturl = lambda: request.full_url
        return response
    monkeypatch.setattr(module.urllib.request, "urlopen", suspension_response)
    assert module.confirmed_suspensions(day) == {"600185.SH": {
        "status": "halted", "target_day": day, "reason": "拟筹划重大资产重组",
        "source": "eastmoney_suspend_report",
    }}


def ths_price_response(request, quote):
    period = request.full_url.rsplit("/", 1)[-1].removesuffix(".js")
    payload = {"hs_600185": quote} if period == "today" else {
        "data": "20261118,4.1,4.3,4.0,4.2,150;20261119,4.2,4.2,4.2,4.2,0;20261120,4.3,4.5,4.3,4.4,150",
    }
    return RawByteResponse(f"quotebridge_v6_line_hs_600185_01_{period}({json.dumps(payload)})".encode())


THS_CLOSED_QUOTE = {"1": "20261119", "6": "4.2", "11": "4.3", "13": 100, "open": 0, "dt": "1640"}


@pytest.mark.parametrize("updates, expected", [
    ({}, ["2026-11-18", "2026-11-19"]),
    ({"open": 1}, ["2026-11-18"]),
    ({"1": "20261117"}, ["2026-11-18"]),
    ({"13": 0}, ["2026-11-18"]),
    ({"dt": "1459"}, ["2026-11-18"]),
    ({"dt": "1560"}, ["2026-11-18"]),
    ({"6": "5.0"}, ["2026-11-18"]),
    ({"6": None}, ["2026-11-18"]),
])
def test_ths_uses_only_closed_same_session_quote(monkeypatch, updates, expected):
    module = load_module()
    day = "2026-11-19"
    monkeypatch.setattr(module.urllib.request, "urlopen", lambda req, **_kw: ths_price_response(req, THS_CLOSED_QUOTE | updates))
    bars = module.ths_daily("600185.SH", "2026-11-18", day, now=datetime.datetime(2026, 11, 19, 16, 40))
    assert [row["date"] for row in bars] == expected
    if len(bars) == 2:
        assert bars[-1] == {"date": day, "close": 4.3, "change_pct": 2.38}


@pytest.fixture
def tracking_trade_evidence(tmp_path, monkeypatch):
    module = load_module()
    prior, target, symbol = "2026-11-18", "2026-11-19", "600185.SH"

    class ClosedSession(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            current = cls(2026, 11, 19, 16, 40, tzinfo=module.ZoneInfo("Asia/Shanghai"))
            return current.astimezone(tz) if tz else current.replace(tzinfo=None)

    monkeypatch.setattr(module.datetime, "datetime", ClosedSession)
    monkeypatch.setattr(module, "time", SimpleNamespace(sleep=lambda _: None))
    monkeypatch.setattr(module, "DATA", tmp_path / "tracking.json")
    monkeypatch.setattr(module, "HISTORY_DIR", tmp_path)
    (tmp_path / f"{prior}.json").write_text(json.dumps({"intersection": [symbol], "enrichments": {symbol: {}}}))
    stored = {"date": prior, "close": 4.2, "profit_ratio": 12.0, "change_pct": None}
    module.DATA.write_text(json.dumps({"stocks": {symbol: {"first_seen": prior, "daily": [stored]}}}))
    halt = {"status": "halted", "target_day": target, "source": "eastmoney_suspend_report", "reason": "全日停牌"}
    monkeypatch.setattr(module, "confirmed_suspensions", lambda _day: {symbol: halt})
    return module, symbol, stored, target, halt


@pytest.mark.parametrize("updates, bao_close, outcome", [
    ({}, None, "blocked"),
    ({}, 4.4, "covered"),
    ({"13": 0}, None, "halted"),
    ({"11": "nan"}, None, "halted"),
    ({"1": "20261118"}, None, "halted"),
    ({"dt": "1459"}, None, "halted"),
    ({"open": 1}, None, "halted"),
])
def test_tracking_ths_quote_trade_evidence_survives_qfq_mismatch(tracking_trade_evidence, monkeypatch, updates, bao_close, outcome):
    module, symbol, stored, target, halt = tracking_trade_evidence
    quote = THS_CLOSED_QUOTE | {"6": "5.0"} | updates
    requests = []

    def opener(request, **_kwargs):
        requests.append(request.full_url.rsplit("/", 1)[-1])
        return ths_price_response(request, quote)

    monkeypatch.setattr(module.urllib.request, "urlopen", opener)
    monkeypatch.setattr(module, "tencent_daily", lambda *_a, **_kw: [stored])
    monkeypatch.setattr(module, "baostock_daily_batch", lambda *_a, **_kw: {symbol: [
        stored, *([{"date": target, "close": bao_close}] if bao_close is not None else []),
    ]})
    monkeypatch.setattr(module, "chip_list_profit_ratios", lambda *_a, **_kw: {})
    original = module.DATA.read_bytes()
    result = module.main(target_day=target, skip_iwencai=True)
    assert requests == ["2026.js", "today.js"]
    if outcome == "blocked":
        assert result != 0 and module.DATA.read_bytes() == original
    else:
        assert result == 0
        payload = json.loads(module.DATA.read_text())
        record = payload["stocks"][symbol]
        if outcome == "covered":
            assert "coverage" not in record and payload["data_through"] == target
            assert record["daily"] == [stored, {"date": target, "close": 4.4, "change_pct": 4.76, "profit_ratio": None}]
        else:
            assert record["coverage"] == halt and record["daily"] == [stored]
            assert payload["data_through"] == stored["date"]


@pytest.mark.parametrize("annual_mode", ["empty", "failed"])
@pytest.mark.parametrize("outcome", ["backup", "halt"])
def test_tracking_ths_trade_evidence_survives_missing_year_line(
    tracking_trade_evidence, monkeypatch, annual_mode, outcome,
):
    """A missing year line must not suppress the independent target-session check."""
    module, symbol, stored, target, halt = tracking_trade_evidence
    quote = THS_CLOSED_QUOTE if outcome == "backup" else THS_CLOSED_QUOTE | {"13": 0}
    requests = []

    def opener(request, **_kwargs):
        period = request.full_url.rsplit("/", 1)[-1]
        requests.append(period)
        period_name = period.removesuffix(".js")
        if period_name == "today":
            return ths_price_response(request, quote)
        if annual_mode == "empty":
            raw = {"data": ""}
            return RawByteResponse(
                f"quotebridge_v6_line_hs_600185_01_{period_name}({json.dumps(raw)})".encode()
            )
        raise urllib.error.URLError("year line unavailable")

    monkeypatch.setattr(module.urllib.request, "urlopen", opener)
    monkeypatch.setattr(module, "tencent_daily", lambda *_a, **_kw: [stored])
    monkeypatch.setattr(module, "baostock_daily_batch", lambda *_a, **_kw: {symbol: [
        stored,
        *([{"date": target, "close": 4.4}] if outcome == "backup" else []),
    ]})
    monkeypatch.setattr(module, "chip_list_profit_ratios", lambda *_a, **_kw: {})
    original = module.DATA.read_bytes()

    result = module.main(target_day=target, skip_iwencai=True)

    assert requests == ["2026.js", "today.js"]
    if outcome == "backup":
        assert result == 0
        payload = json.loads(module.DATA.read_text())
        record = payload["stocks"][symbol]
        assert "coverage" not in record and payload["data_through"] == target
        assert record["daily"] == [stored, {"date": target, "close": 4.4, "change_pct": 4.76, "profit_ratio": None}]
    else:
        assert result == 0
        payload = json.loads(module.DATA.read_text())
        record = payload["stocks"][symbol]
        assert record["coverage"] == halt and record["daily"] == [stored]
        assert payload["data_through"] == stored["date"]
    assert module.DATA.read_bytes() != original


@pytest.mark.parametrize("annual_mode, prior, target, requested_years", [
    ("failed", "2025-12-31", "2026-01-12", ["2025", "2026"]),
    ("empty", "2025-12-31", "2026-01-12", ["2025", "2026"]),
    ("stale", "2026-01-08", "2026-01-12", ["2025", "2026"]),
    ("saturday", "2026-11-12", "2026-11-16", ["2026"]),
    ("sunday", "2026-11-12", "2026-11-16", ["2026"]),
])
@pytest.mark.parametrize("with_backup", [True, False])
def test_tracking_ths_stale_anchor_cannot_supply_target_close(
    tmp_path, monkeypatch, annual_mode, prior, target, requested_years, with_backup,
):
    module = load_module()
    symbol = "600185.SH"
    session = datetime.date.fromisoformat(target)

    class ClosedSession(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            current = cls(session.year, session.month, session.day, 16, 40, tzinfo=module.ZoneInfo("Asia/Shanghai"))
            return current.astimezone(tz) if tz else current.replace(tzinfo=None)

    monkeypatch.setattr(module.datetime, "datetime", ClosedSession)
    monkeypatch.setattr(module, "time", SimpleNamespace(sleep=lambda _: None))
    monkeypatch.setattr(module, "DATA", tmp_path / "tracking.json")
    monkeypatch.setattr(module, "HISTORY_DIR", tmp_path)
    (tmp_path / f"{prior}.json").write_text(json.dumps({"intersection": [symbol], "enrichments": {symbol: {}}}))
    stored = {"date": prior, "close": 4.2, "profit_ratio": 12.0, "change_pct": None}
    module.DATA.write_text(json.dumps({"stocks": {symbol: {"first_seen": prior, "daily": [stored]}}}))
    original = module.DATA.read_bytes()
    halt = {"status": "halted", "target_day": target, "source": "eastmoney_suspend_report", "reason": "全日停牌"}
    monkeypatch.setattr(module, "confirmed_suspensions", lambda _day: {symbol: halt})
    monkeypatch.setattr(module, "tencent_daily", lambda *_a, **_kw: [])
    monkeypatch.setattr(module, "chip_list_profit_ratios", lambda *_a, **_kw: {})
    requests = []

    def opener(request, **_kwargs):
        period = request.full_url.rsplit("/", 1)[-1].removesuffix(".js")
        requests.append(period)
        if period == "today":
            return ths_price_response(request, THS_CLOSED_QUOTE | {"1": target.replace("-", "")})
        if period == prior[:4]:
            payload = {"data": f"{prior.replace('-', '')},4.1,4.3,4.0,4.20,150"}
            if annual_mode in {"saturday", "sunday"}:
                weekend_day = {"saturday": "20261114", "sunday": "20261115"}[annual_mode]
                payload["data"] += f";{weekend_day},4.1,4.3,4.0,4.20,150"
        elif period == target[:4] and annual_mode == "failed":
            raise TimeoutError("current year line unavailable")
        else:
            # A fresh top-level timestamp does not make an empty series current.
            payload = {"data": "", "today": target.replace("-", "")}
        return RawByteResponse(f"quotebridge_v6_line_hs_600185_01_{period}({json.dumps(payload)})".encode())

    def backup(specs, end, **_kwargs):
        requests.append("BaoStock")
        assert specs == [(symbol, prior)] and end == target
        return {symbol: [stored, {"date": target, "close": 4.4}]} if with_backup else {}

    monkeypatch.setattr(module.urllib.request, "urlopen", opener)
    monkeypatch.setattr(module, "baostock_daily_batch", backup)

    result = module.main(target_day=target, skip_iwencai=True)

    if with_backup:
        assert result == 0
        payload = json.loads(module.DATA.read_text())
        record = payload["stocks"][symbol]
        assert record["daily"] == [stored, {"date": target, "close": 4.4, "change_pct": 4.76, "profit_ratio": None}]
        assert "coverage" not in record and payload["data_through"] == target
    else:
        assert result != 0
        assert module.DATA.read_bytes() == original
    assert requests == [*requested_years, "today", "BaoStock"]


@pytest.mark.parametrize("prior, target", [
    ("2026-11-18", "2026-11-19"),  # Adjacent calendar days.
    ("2026-11-13", "2026-11-16"),  # Only a weekend separates Friday and Monday.
])
def test_ths_accepts_closed_quote_with_adjacent_qfq_anchor(monkeypatch, prior, target):
    module = load_module()

    def opener(request, **_kwargs):
        if request.full_url.endswith("/today.js"):
            return ths_price_response(request, THS_CLOSED_QUOTE | {"1": target.replace("-", "")})
        payload = {"data": f"{prior.replace('-', '')},4.1,4.3,4.0,4.20,150"}
        return RawByteResponse(f"quotebridge_v6_line_hs_600185_01_2026({json.dumps(payload)})".encode())

    monkeypatch.setattr(module.urllib.request, "urlopen", opener)
    reports = []
    bars = module.ths_daily("600185.SH", prior, target,
                            now=datetime.datetime.fromisoformat(f"{target}T16:40"),
                            on_target_trade=lambda: reports.append(target))
    assert reports == [target]
    assert bars == [
        {"date": prior, "close": 4.2, "change_pct": None},
        {"date": target, "close": 4.3, "change_pct": 2.38},
    ]


def mock_ths_same_day_rows(module, monkeypatch, rows, target):
    requests = []

    def opener(request, **_kwargs):
        period = request.full_url.rsplit("/", 1)[-1].removesuffix(".js")
        requests.append(period)
        if period == "today":
            return ths_price_response(request, THS_CLOSED_QUOTE | {"1": target.replace("-", "")})
        payload = {"data": ";".join(f"{day},4.1,4.5,4.0,{close},150" for day, close in rows)}
        return RawByteResponse(f"quotebridge_v6_line_hs_600185_01_{period}({json.dumps(payload)})".encode())

    monkeypatch.setattr(module.urllib.request, "urlopen", opener)
    return requests


@pytest.mark.parametrize("friday_closes", [(4.1, 4.2), (4.2, 4.1), (4.2, 4.1, 4.2)])
def test_ths_friday_conflict_never_anchors_monday(monkeypatch, friday_closes):
    module = load_module()
    target = "2026-11-16"
    requests = mock_ths_same_day_rows(module, monkeypatch, [
        ("20261112", 4.2), *(("20261113", close) for close in friday_closes),
    ], target)
    reports = []
    bars = module.ths_daily("600185.SH", "2026-11-12", target,
                            now=datetime.datetime(2026, 11, 16, 16, 40),
                            on_target_trade=lambda: reports.append(target))
    assert bars == [{"date": "2026-11-12", "close": 4.2, "change_pct": None}]
    assert requests == ["2026", "today"] and reports == [target]


def test_ths_identical_friday_closes_dedupe_and_anchor_monday(monkeypatch):
    module = load_module()
    target = "2026-11-16"
    requests = mock_ths_same_day_rows(module, monkeypatch, [("20261113", 4.2)] * 2, target)
    reports = []
    bars = module.ths_daily("600185.SH", "2026-11-13", target,
                            now=datetime.datetime(2026, 11, 16, 16, 40),
                            on_target_trade=lambda: reports.append(target))
    assert bars == [{"date": "2026-11-13", "close": 4.2, "change_pct": None},
                    {"date": target, "close": 4.3, "change_pct": 2.38}]
    assert requests == ["2026", "today"] and reports == [target]


@pytest.mark.parametrize("historical, hour, expected_trade", [(False, 14, False), (True, 14, True)])
def test_ths_target_year_conflict_respects_session_boundary(monkeypatch, historical, hour, expected_trade):
    module = load_module()
    target = "2026-11-16"
    requests = mock_ths_same_day_rows(module, monkeypatch, [
        ("20261113", 4.2), ("20261116", 4.3), ("20261116", 4.4),
    ], target)
    reports = []
    bars = module.ths_daily("600185.SH", "2026-11-13", target,
                            now=datetime.datetime(2026, 11, 17 if historical else 16, hour),
                            on_target_trade=lambda: reports.append(target))
    assert bars == [{"date": "2026-11-13", "close": 4.2, "change_pct": None}]
    assert requests == ["2026"] and reports == ([target] if expected_trade else [])


@pytest.mark.parametrize("conflict", ["friday", "target"])
@pytest.mark.parametrize("with_backup", [False, True])
def test_tracking_ths_conflict_retains_trade_evidence_and_requires_backup(
    tmp_path, monkeypatch, tracking_publisher, conflict, with_backup,
):
    module = load_module()
    symbol, prior, target = "600185.SH", "2026-11-13", "2026-11-16"

    class ClosedMonday(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            now = cls(2026, 11, 16, 16, 40, tzinfo=module.ZoneInfo("Asia/Shanghai"))
            return now.astimezone(tz) if tz else now.replace(tzinfo=None)

    monkeypatch.setattr(module.datetime, "datetime", ClosedMonday)
    monkeypatch.setattr(module, "time", SimpleNamespace(sleep=lambda _: None))
    monkeypatch.setattr(module, "DATA", tmp_path / "tracking.json")
    monkeypatch.setattr(module, "HISTORY_DIR", tmp_path)
    (tmp_path / f"{prior}.json").write_text(json.dumps({"intersection": [symbol], "enrichments": {symbol: {}}}))
    stored = {"date": prior, "close": 4.2, "change_pct": None, "profit_ratio": 12.0}
    module.DATA.write_text(json.dumps({"stocks": {symbol: {"first_seen": prior, "daily": [stored]}}}))
    before = module.DATA.read_bytes()
    rows = ([("20261113", 4.1), ("20261113", 4.2)] if conflict == "friday" else
            [("20261113", 4.2), ("20261116", 4.4), ("20261116", 4.3)])
    requests = mock_ths_same_day_rows(module, monkeypatch, rows, target)
    if conflict == "target" and not with_backup:
        # No target quote: the conflicting annual bars themselves prove trade.
        original_open = module.urllib.request.urlopen

        def fail_today(request, **kwargs):
            if request.full_url.endswith("/today.js"):
                requests.append("today")
                raise TimeoutError("no target quote")
            return original_open(request, **kwargs)

        monkeypatch.setattr(module.urllib.request, "urlopen", fail_today)
    monkeypatch.setattr(module, "tencent_daily", lambda *_a, **_kw: [stored])
    monkeypatch.setattr(module, "chip_list_profit_ratios", lambda *_a, **_kw: {})
    halt = {"status": "halted", "target_day": target, "source": "eastmoney_suspend_report", "reason": "全日停牌"}
    halt_queries = []

    def suspensions(day):
        halt_queries.append(day)
        return {symbol: halt}

    def backup(specs, end, **_kwargs):
        assert specs == [(symbol, prior)] and end == target
        requests.append("BaoStock")
        return {symbol: [stored, {"date": target, "close": 4.4}]} if with_backup else {}

    monkeypatch.setattr(module, "confirmed_suspensions", suspensions)
    monkeypatch.setattr(module, "baostock_daily_batch", backup)
    result = module.main(target_day=target, skip_iwencai=True)
    if with_backup:
        assert result == 0
        payload = json.loads(module.DATA.read_text())
        record = payload["stocks"][symbol]
        assert record["daily"] == [stored, {"date": target, "close": 4.4, "change_pct": 4.76, "profit_ratio": None}]
        assert "coverage" not in record and payload["data_through"] == target
        assert tracking_publisher.validate_tracking_coverage(payload, target, {symbol})["covered"] == 1
    else:
        assert result != 0 and module.DATA.read_bytes() == before
    assert not halt_queries
    assert requests == ["2026", "today", "BaoStock"]


@pytest.mark.parametrize("target", ["2026-11-14", "2026-11-15"])
@pytest.mark.parametrize("with_target_annual_bar", [False, True])
def test_ths_weekend_target_has_no_price_or_trade_evidence(monkeypatch, target, with_target_annual_bar):
    module = load_module()
    prior = "2026-11-13"
    target_compact = target.replace("-", "")
    requests = []
    reports = []

    def opener(request, **_kwargs):
        period = request.full_url.rsplit("/", 1)[-1].removesuffix(".js")
        requests.append(period)
        if period == "today":
            return ths_price_response(request, THS_CLOSED_QUOTE | {"1": target_compact})
        data = "20261113,4.1,4.3,4.0,4.20,150"
        if with_target_annual_bar:
            data += f";{target_compact},4.2,4.4,4.1,4.30,150"
        payload = {"data": data}
        return RawByteResponse(f"quotebridge_v6_line_hs_600185_01_{period}({json.dumps(payload)})".encode())

    monkeypatch.setattr(module.urllib.request, "urlopen", opener)
    bars = module.ths_daily("600185.SH", prior, target,
                            now=datetime.datetime.fromisoformat(f"{target}T16:40"),
                            on_target_trade=lambda: reports.append(target))
    assert (bars, reports, requests) == ([{"date": prior, "close": 4.2, "change_pct": None}], [], ["2026"])


@pytest.mark.parametrize("qfq_history, day_update, outcome", [
    (False, {}, "blocked"),
    (True, {}, "blocked"),
    (False, {}, "covered"),
    (True, {"owner": "sh600186"}, "halted"),
    (True, {"date": "2026-11-18"}, "halted"),
    (True, {"date": "20261119"}, "halted"),
    (True, {"date": "2026-11-20"}, "halted"),
    (True, {"volume": 0}, "halted"),
    (True, {"volume": "nan"}, "halted"),
    (True, {"close": True}, "halted"),
    (True, {"business_code": 11}, "halted"),
])
def test_tracking_tencent_raw_day_trade_evidence_is_not_a_qfq_bar(
    tracking_trade_evidence, monkeypatch, qfq_history, day_update, outcome,
):
    module, symbol, stored, target, halt = tracking_trade_evidence
    prior = [stored["date"], "4.1", "4.2", "4.3", "4.0", "100"]
    raw_row = [day_update.get("date", target), "8.0", day_update.get("close", "8.1"),
               "8.2", "8.0", day_update.get("volume", "100")]
    data = {"sh600185": {"qfqday": [prior]} if qfq_history else {}}
    data.setdefault(day_update.get("owner", "sh600185"), {})["day"] = [raw_row]
    payload = {"code": day_update.get("business_code", 0), "data": data}
    fetch = module.tencent_daily
    monkeypatch.setattr(module, "tencent_daily", lambda *args, **kwargs: fetch(
        *args, **kwargs, opener=lambda *_a, **_kw: Response(payload), sleeper=lambda _: None,
    ))
    monkeypatch.setattr(module, "ths_daily", lambda *_a, **_kw: [])
    monkeypatch.setattr(module, "baostock_daily_batch", lambda *_a, **_kw: {symbol: [
        stored, *([{"date": target, "close": 4.4}] if outcome == "covered" else []),
    ]})
    monkeypatch.setattr(module, "chip_list_profit_ratios", lambda *_a, **_kw: {})
    original = module.DATA.read_bytes()
    result = module.main(target_day=target, skip_iwencai=True)
    if outcome == "blocked":
        assert result != 0 and module.DATA.read_bytes() == original
    else:
        assert result == 0
        payload = json.loads(module.DATA.read_text())
        record = payload["stocks"][symbol]
        if outcome == "covered":
            assert "coverage" not in record and payload["data_through"] == target
            assert record["daily"] == [stored, {"date": target, "close": 4.4, "change_pct": 4.76, "profit_ratio": None}]
        else:
            assert record["coverage"] == halt and record["daily"] == [stored]
            assert payload["data_through"] == stored["date"]


@pytest.mark.parametrize("missing", [False, True])
def test_tracking_partial_prices_fall_back_or_record_confirmed_halt(tmp_path, monkeypatch, missing, capsys):
    module = load_module()
    target = "2026-11-19"
    prior = "2026-11-18"
    symbols = ("600185.SH", "600006.SH", "600022.SH", "600037.SH")
    tracking = tmp_path / "tracking.json"
    history = tmp_path / "history"
    history.mkdir()
    (history / f"{prior}.json").write_text(json.dumps({
        "intersection": list(symbols), "enrichments": {symbol: {} for symbol in symbols},
    }))
    tracking.write_text(json.dumps({"stocks": {s: {"first_seen": prior, "daily": [
        {"date": prior, "close": 4.0, "profit_ratio": 12.0},
    ], "coverage": {"status": "halted", "target_day": prior, "reason": "old"}} for s in symbols}}))
    monkeypatch.setattr(module, "DATA", tracking)
    monkeypatch.setattr(module, "HISTORY_DIR", history)
    monkeypatch.setattr(module, "tencent_daily", lambda *_a, **_kw: [{"date": prior, "close": 4.0}])
    evidence = {"status": "halted", "target_day": target, "reason": "拟筹划重大资产重组",
                "source": "eastmoney_suspend_report"}
    monkeypatch.setattr(module, "confirmed_suspensions", lambda day: {symbols[0]: evidence, symbols[2]: evidence})
    monkeypatch.setattr(module, "ths_daily", lambda s, _start, _end, **_kw: [
        {"date": prior, "close": 4.0}, {"date": target, "close": 4.4, "change_pct": 10.0},
    ] if s == symbols[2] else [])
    calls = []

    def fallback(specs, end, **_kw):
        calls.append(([s for s, _ in specs], end))
        rows = [{"date": prior, "close": 4.0}, {"date": target, "close": 4.2, "change_pct": 5.0}]
        return {symbols[1]: rows, **({} if missing else {symbols[3]: rows})}

    monkeypatch.setattr(module, "baostock_daily_batch", fallback)
    monkeypatch.setattr(module, "chip_list_profit_ratios", lambda *_a, **_k: {target.replace("-", ""): 15.0})
    monkeypatch.setattr(module, "iwencai_profit_ratios", lambda _pairs: {})
    monkeypatch.setattr(module, "fetch_current_year_profit", lambda _symbols: {})
    if missing:
        monkeypatch.setattr(module, "chip_list_profit_ratios", lambda *_a, **_k: pytest.fail("incomplete batch reached enrichment"))
    original = tracking.read_bytes()
    result = module.main(target_day=target)
    if missing:
        assert result != 0
        assert tracking.read_bytes() == original
        assert symbols[3] in capsys.readouterr().out
        return
    assert result == 0
    records = json.loads(tracking.read_text())["stocks"]
    assert calls == [(sorted((symbols[0], symbols[1], symbols[3])), target)]
    assert records[symbols[0]]["coverage"] == evidence
    assert [r["date"] for r in records[symbols[0]]["daily"]] == [prior]
    assert records[symbols[1]]["daily"][-1]["date"] == target
    assert records[symbols[1]]["daily"][0]["profit_ratio"] == 12.0
    assert records[symbols[2]]["daily"][-1]["close"] == 4.4
    assert all("coverage" not in records[s] for s in symbols[1:])


@pytest.mark.parametrize("backfill", [False, True])
def test_tracking_keeps_halted_stocks_with_empty_or_backfilled_history(tmp_path, monkeypatch, backfill):
    module = load_module()
    prior, target, symbol = "2026-11-18", "2026-11-19", "600185.SH"
    monkeypatch.setattr(module, "HISTORY_DIR", tmp_path)
    monkeypatch.setattr(module, "DATA", tmp_path / "tracking.json")
    (tmp_path / f"{prior}.json").write_text(json.dumps({"intersection": [symbol], "enrichments": {symbol: {}}}))
    mock_gap_fallbacks(module, monkeypatch)
    monkeypatch.setattr(module, "tencent_daily", lambda *_a, **_kw: [])
    monkeypatch.setattr(module, "ths_daily", lambda *_a, **_kw: [{"date": prior, "close": 4.0}] if backfill else [])
    evidence = {"status": "halted", "target_day": target, "source": "eastmoney_suspend_report", "reason": "全日停牌"}
    monkeypatch.setattr(module, "confirmed_suspensions", lambda _d: {symbol: evidence})
    monkeypatch.setattr(module, "chip_list_profit_ratios", lambda *_a, **_k: {})
    assert module.main(target_day=target, skip_iwencai=True) == 0
    payload = json.loads(module.DATA.read_text())
    record = payload["stocks"][symbol]
    assert record["coverage"] == evidence
    assert payload["data_through"] == (prior if backfill else "")
    assert [bar["date"] for bar in record["daily"]] == ([prior] if backfill else [])


def test_join_day_halt_then_resume_has_no_fabricated_entry_baseline(tmp_path, monkeypatch):
    mod = load_module()
    first, resume, symbol = "2026-11-18", "2026-11-19", "600185.SH"
    data = tmp_path / "tracking.json"
    monkeypatch.setattr(mod, "DATA", data)
    monkeypatch.setattr(mod, "HISTORY_DIR", tmp_path)
    monkeypatch.setattr(mod, "time", SimpleNamespace(sleep=lambda _: None))
    (tmp_path / f"{first}.json").write_text(json.dumps({"intersection": [symbol], "enrichments": {symbol: {}}}))
    evidence = {"status": "halted", "target_day": first, "source": "eastmoney_suspend_report", "reason": "全日停牌"}
    mock_gap_fallbacks(mod, monkeypatch)
    monkeypatch.setattr(mod, "confirmed_suspensions", lambda _day: {symbol: evidence})
    monkeypatch.setattr(mod, "tencent_daily", lambda *_a, **_kw: [])
    publisher_path = Path("/root/.hermes/scripts/update_low_chip_tracking_and_release.py")
    require_local_script(publisher_path)
    spec = importlib.util.spec_from_file_location("tracking_release_check", publisher_path)
    assert spec and spec.loader
    publisher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(publisher)
    monkeypatch.setattr(publisher, "ROOT", tmp_path)

    assert mod.main(target_day=first, skip_iwencai=True) == 0
    empty = json.loads(data.read_text())
    assert publisher.validate_tracking_coverage(empty, first, {symbol})["halted"] == 1
    assert empty["stocks"][symbol]["daily"] == []
    assert empty["stocks"][symbol]["entry_baseline_status"] == "missing"
    assert empty["stocks"][symbol]["entry_halt"] == evidence
    assert empty["data_through"] == ""

    monkeypatch.setattr(mod, "tencent_daily", lambda *_a, **_kw: [
        {"date": resume, "close": 10.0, "change_pct": 5.0},
    ])
    monkeypatch.setattr(mod, "chip_list_profit_ratios", lambda *_a, **_kw: {"20261119": 17.0})
    assert mod.main(target_day=resume, skip_iwencai=True) == 0
    resumed = json.loads(data.read_text())
    assert publisher.validate_tracking_coverage(resumed, resume, {symbol})["covered"] == 1
    rec = resumed["stocks"][symbol]
    assert rec["first_seen"] == first
    assert rec["entry_halt"] == evidence and "coverage" not in rec
    assert rec["entry_baseline_status"] == "missing"
    assert rec["daily"] == [{"date": resume, "close": 10.0, "change_pct": None, "profit_ratio": 17.0}]
    assert resumed["data_through"] == resume


def test_delayed_entry_caps_at_20_and_blocks_when_release_cannot_validate(tmp_path, monkeypatch):
    mod = load_module()
    first, symbol = "2026-10-19", "600185.SH"
    dates = [(datetime.date.fromisoformat(first) + datetime.timedelta(days=i)).isoformat() for i in range(1, 22)]
    data = tmp_path / "tracking.json"
    monkeypatch.setattr(mod, "DATA", data)
    monkeypatch.setattr(mod, "HISTORY_DIR", tmp_path)
    monkeypatch.setattr(mod, "time", SimpleNamespace(sleep=lambda _: None))
    (tmp_path / f"{first}.json").write_text(json.dumps({"intersection": [symbol], "enrichments": {symbol: {}}}))
    monkeypatch.setattr(mod, "tencent_daily", lambda *_a, **_kw: [{"date": d, "close": 10.0} for d in dates])
    monkeypatch.setattr(mod, "chip_list_profit_ratios", lambda *_a, **_kw: {})
    mock_gap_fallbacks(mod, monkeypatch)
    assert mod.main(target_day=dates[-2], skip_iwencai=True) == 0
    before = data.read_bytes()
    assert [row["date"] for row in json.loads(before)["stocks"][symbol]["daily"]] == dates[:-1]
    assert mod.main(target_day=dates[-1], skip_iwencai=True) != 0
    assert data.read_bytes() == before


@pytest.fixture
def tracking_publisher():
    path = Path("/root/.hermes/scripts/update_low_chip_tracking_and_release.py")
    require_local_script(path)
    spec = importlib.util.spec_from_file_location("tracking_release_gate", path)
    assert spec and spec.loader
    publisher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(publisher)
    return publisher


def halt_gate_fixture(tmp_path, monkeypatch):
    mod = load_module()
    first, symbol = "2026-09-01", "600185.SH"
    origin = datetime.date.fromisoformat(first)
    dates = [origin + datetime.timedelta(days=i) for i in range(1, 40)]
    observations = [d.isoformat() for d in dates if d.weekday() < 5]
    evidence = {"status": "halted", "target_day": first, "source": "eastmoney_suspend_report", "reason": "全日停牌"}
    monkeypatch.setattr(mod, "HISTORY_DIR", tmp_path)
    monkeypatch.setattr(mod, "DATA", tmp_path / "tracking.json")
    monkeypatch.setattr(mod, "time", SimpleNamespace(sleep=lambda _: None))
    (tmp_path / f"{first}.json").write_text(json.dumps({"intersection": [symbol], "enrichments": {symbol: {}}}))
    mock_gap_fallbacks(mod, monkeypatch)
    monkeypatch.setattr(mod, "chip_list_profit_ratios", lambda *_a, **_kw: {observations[0].replace("-", ""): 17.0})
    return mod, first, symbol, observations, evidence


def no_halt_gate_query(*_a, **_kw):
    pytest.fail("unexpected price, suspension or profit-ratio query")


def test_entry_halt_window_completes_and_stops_querying(tmp_path, monkeypatch, tracking_publisher):
    mod, first, symbol, observations, evidence = halt_gate_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(mod, "tencent_daily", lambda *_a, **_kw: [])
    monkeypatch.setattr(mod, "confirmed_suspensions", lambda day: {symbol: evidence} if day == first else {})
    validate = tracking_publisher.validate_tracking_coverage

    assert mod.main(target_day=first, skip_iwencai=True) == 0
    empty = json.loads(mod.DATA.read_text())
    entry = empty["stocks"][symbol]
    assert entry["daily"] == [] and empty["data_through"] == ""
    assert entry["coverage"] == entry["entry_halt"] == evidence
    assert entry["tracking_complete"] is False
    assert validate(empty, first, {symbol})["halted"] == 1

    bars = [{"date": day, "close": 10.0 + i} for i, day in enumerate(observations)]
    monkeypatch.setattr(mod, "tencent_daily", lambda *_a, **_kw: bars)
    for n in (1, 19):
        day = observations[n - 1]
        assert mod.main(target_day=day, skip_iwencai=True) == 0
        active = json.loads(mod.DATA.read_text())
        assert active["stocks"][symbol]["tracking_complete"] is False
        assert len(active["stocks"][symbol]["daily"]) == n
        assert validate(active, day, {symbol})["covered"] == 1

    twentieth, next_session = observations[19:21]
    assert mod.main(target_day=twentieth, skip_iwencai=True) == 0
    finished = json.loads(mod.DATA.read_text())
    rec = finished["stocks"][symbol]
    assert rec["tracking_complete"] is True
    assert validate(finished, twentieth, {symbol})["complete"] == 1
    assert rec["first_seen"] == first and rec["entry_baseline_status"] == "missing"
    assert rec["entry_halt"] == evidence and "coverage" not in rec
    assert [(r["date"], r["close"]) for r in rec["daily"]] == [(r["date"], r["close"]) for r in bars[:20]]
    assert rec["daily"][0] == {**bars[0], "change_pct": None, "profit_ratio": 17.0}
    assert rec["daily"][-1]["change_pct"] == round(100 / 28, 2)
    assert finished["data_through"] == twentieth

    for name in ("tencent_daily", "ths_daily", "baostock_daily_batch", "confirmed_suspensions", "chip_list_profit_ratios"):
        monkeypatch.setattr(mod, name, no_halt_gate_query)
    assert mod.main(target_day=next_session, skip_iwencai=True) == 0
    after = json.loads(mod.DATA.read_text())
    assert after["stocks"][symbol] == rec and after["data_through"] == twentieth
    assert validate(after, next_session, {symbol})["complete"] == 1


@pytest.mark.parametrize("source", ["tencent_daily", "ths_daily", "baostock_daily_batch"])
def test_entry_halt_twentieth_bar_backfill_satisfies_later_target_without_halt(tmp_path, monkeypatch, tracking_publisher, source):
    mod, first, symbol, observations, evidence = halt_gate_fixture(tmp_path, monkeypatch)
    bars = [{"date": day, "close": 10.0} for day in observations[:20]]
    rec = {"first_seen": first, "entry_halt": evidence, "daily": bars[:19]}
    mod.DATA.write_text(json.dumps({"stocks": {symbol: rec}}))
    monkeypatch.setattr(mod, "tencent_daily", lambda *_a, **_kw: [])
    monkeypatch.setattr(mod, "confirmed_suspensions", no_halt_gate_query)
    if source == "baostock_daily_batch":
        monkeypatch.setattr(mod, source, lambda *_a, **_kw: {symbol: bars})
    else:
        monkeypatch.setattr(mod, source, lambda *_a, **_kw: bars)
        monkeypatch.setattr(mod, "baostock_daily_batch", no_halt_gate_query)
        if source == "tencent_daily":
            monkeypatch.setattr(mod, "ths_daily", no_halt_gate_query)
    target = observations[20]
    assert mod.main(target_day=target, skip_iwencai=True) == 0
    payload = json.loads(mod.DATA.read_text())
    assert payload["stocks"][symbol]["tracking_complete"] is True
    assert payload["data_through"] == observations[19]
    assert tracking_publisher.validate_tracking_coverage(payload, target, {symbol})["complete"] == 1


@pytest.mark.parametrize("post_count,has_baseline,proof_update,expected", [
    (20, False, {}, True),
    (20, True, None, True),
    (19, True, {}, False),
    (19, False, {}, False),
    (20, False, None, False),
    (20, False, {"target_day": "2026-08-31"}, False),
    (20, False, {"source": "unknown"}, False),
    (20, False, {"source": None}, False),
    (20, False, {"status": "partial"}, False),
    (20, False, {"reason": " "}, False),
    (20, False, {"reason": 1}, False),
    (21, False, None, False),
    (21, False, {}, False),
])
def test_entry_halt_completion_matrix(tmp_path, monkeypatch, tracking_publisher, post_count, has_baseline, proof_update, expected):
    mod, first, symbol, observations, evidence = halt_gate_fixture(tmp_path, monkeypatch)
    target = observations[21]
    dates = ([first] if has_baseline else []) + observations[:post_count]
    rec = {"first_seen": first, "daily": [{"date": day, "close": 10.0} for day in dates],
           "tracking_complete": not expected, "entry_baseline_status": "missing" if has_baseline else "available",
           "coverage": {**evidence, "target_day": observations[20]}}
    if proof_update is not None:
        rec["entry_halt"] = {**evidence, **proof_update}
    data = {"schema_version": "low-chip-tracking-v1", "stocks": {symbol: rec}}
    mod.DATA.write_text(json.dumps(data))
    before = mod.DATA.read_bytes()
    validate = tracking_publisher.validate_tracking_coverage
    if expected:
        for name in ("tencent_daily", "ths_daily", "baostock_daily_batch", "confirmed_suspensions"):
            monkeypatch.setattr(mod, name, no_halt_gate_query)
        assert validate(data, target, {symbol})["complete"] == 1
        assert mod.main(target_day=target, skip_iwencai=True) == 0
        after = json.loads(mod.DATA.read_text())["stocks"][symbol]
        assert after["tracking_complete"] is True and after["daily"] == rec["daily"]
    else:
        monkeypatch.setattr(mod, "tencent_daily", lambda *_a, **_kw: [])
        assert mod.main(target_day=target, skip_iwencai=True) == 1
        assert mod.DATA.read_bytes() == before
        with pytest.raises(RuntimeError, match="tracking coverage missing"):
            validate(data, target, {symbol})


@pytest.mark.parametrize("bad_bar", ["duplicate", "future", "before", "not_a_date", "noncanonical", "bad_close"])
def test_entry_halt_gate_rejects_invalid_daily_bars(tmp_path, monkeypatch, tracking_publisher, bad_bar):
    _, first, symbol, observations, evidence = halt_gate_fixture(tmp_path, monkeypatch)
    target = observations[21]
    bars = [{"date": day, "close": 10.0} for day in observations[:20]]
    update = {
        "duplicate": {"date": observations[0]},
        "future": {"date": observations[22]},
        "before": {"date": "2026-08-31"},
        "not_a_date": {"date": "2026-09-31"},
        "noncanonical": {"date": "2026-W39-2"},
        "bad_close": {"close": float("nan")},
    }
    bars[-1].update(update[bad_bar])
    data = {"schema_version": "low-chip-tracking-v1", "stocks": {
        symbol: {"first_seen": first, "entry_halt": evidence, "daily": bars, "tracking_complete": True},
    }}
    with pytest.raises(RuntimeError, match="invalid daily bar"):
        tracking_publisher.validate_tracking_coverage(data, target, {symbol})


@pytest.mark.parametrize("ths_prior,bao_prior", [(10.0, None), (None, None), (10.0, 8.0)])
def test_tracking_qfq_fallback_requires_comparable_stored_prices(tmp_path, monkeypatch, ths_prior, bao_prior):
    module = load_module()
    prior, target, symbol = "2026-11-18", "2026-11-19", "600185.SH"
    monkeypatch.setattr(module, "HISTORY_DIR", tmp_path)
    monkeypatch.setattr(module, "DATA", tmp_path / "tracking.json")
    (tmp_path / f"{prior}.json").write_text(json.dumps({"intersection": [symbol], "enrichments": {symbol: {}}}))
    old = {"date": prior, "close": 8.0, "change_pct": 1.2, "profit_ratio": 12.0}
    module.DATA.write_text(json.dumps({"stocks": {symbol: {"first_seen": prior, "daily": [old]}}}))
    original = module.DATA.read_bytes()
    mock_gap_fallbacks(module, monkeypatch)
    # A conflicting real trade must not be hidden by halt evidence.
    monkeypatch.setattr(module, "confirmed_suspensions", lambda _day: {symbol: {
        "status": "halted", "target_day": target, "source": "eastmoney_suspend_report", "reason": "全日停牌",
    }})
    monkeypatch.setattr(module, "tencent_daily", lambda *_a, **_kw: [old])
    monkeypatch.setattr(module, "ths_daily", lambda *_a, **_kw: [
        *([{"date": prior, "close": ths_prior}] if ths_prior else []),
        {"date": target, "close": 10.1, "change_pct": 1.0},
    ])
    monkeypatch.setattr(module, "baostock_daily_batch", lambda *_a, **_kw: {symbol: [
        {"date": prior, "close": bao_prior}, {"date": target, "close": 8.08, "change_pct": 1.0},
    ]} if bao_prior else {})
    monkeypatch.setattr(module, "chip_list_profit_ratios", lambda *_a, **_k: {})
    result = module.main(target_day=target, skip_iwencai=True)
    if bao_prior is None:
        assert result != 0
        assert module.DATA.read_bytes() == original
    else:
        assert result == 0
        assert json.loads(module.DATA.read_text())["stocks"][symbol]["daily"] == [old, {
            "date": target, "close": 8.08, "change_pct": 1.0, "profit_ratio": None,
        }]


def test_new_change_uses_previous_close_in_final_series(tmp_path, monkeypatch):
    mod = load_module()
    first, middle, target, symbol = "2026-11-17", "2026-11-18", "2026-11-19", "600185.SH"
    monkeypatch.setattr(mod, "DATA", tmp_path / "tracking.json")
    monkeypatch.setattr(mod, "HISTORY_DIR", tmp_path)
    (tmp_path / f"{first}.json").write_text(json.dumps({"intersection": [symbol], "enrichments": {symbol: {}}}))
    old = [{"date": first, "close": 8.0, "profit_ratio": 12.0},
           {"date": middle, "close": 9.0, "change_pct": 12.5, "profit_ratio": 15.0}]
    mod.DATA.write_text(json.dumps({"stocks": {symbol: {"first_seen": first, "daily": old}}}))
    monkeypatch.setattr(mod, "tencent_daily", lambda *_a, **_k: [])
    monkeypatch.setattr(mod, "ths_daily", lambda *_a, **_kw: [old[0], {"date": target, "close": 10.0, "change_pct": 25.0}])
    monkeypatch.setattr(mod, "chip_list_profit_ratios", lambda *_a, **_k: {})
    assert mod.main(target_day=target, skip_iwencai=True) == 0
    daily = json.loads(mod.DATA.read_text())["stocks"][symbol]["daily"]
    assert daily[:2] == old
    assert daily[-1]["change_pct"] == 11.11


def test_complete_single_source_can_replace_unwritten_conflicting_history():
    module = load_module()
    pending = [{"date": "2026-11-18", "close": 8.0}]
    coherent = [{"date": "2026-11-18", "close": 10.0},
                {"date": "2026-11-19", "close": 10.1, "change_pct": 1.0}]
    assert module._merge_prices([], pending, coherent) == coherent
    with pytest.raises(ValueError, match="QFQ overlap"):
        module._merge_prices(pending, [], coherent)


@pytest.mark.parametrize("now", [(2026, 11, 18, 16), (2026, 11, 19, 14)])
def test_tracking_blocks_future_or_unclosed_session_before_fetch(tmp_path, monkeypatch, now):
    module = load_module()

    class FrozenTime(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(*now, tzinfo=tz)

    data = tmp_path / "tracking.json"
    data.write_bytes(b'{"stocks":{}}')
    monkeypatch.setattr(module, "DATA", data)
    monkeypatch.setattr(module.datetime, "datetime", FrozenTime)
    monkeypatch.setattr(module, "load_history_dates", lambda: pytest.fail("unclosed session loaded history"))
    assert module.main(target_day="2026-11-19", skip_iwencai=True) != 0
    assert data.read_bytes() == b'{"stocks":{}}'


def test_tracking_tests_never_hit_live_iwencai(tmp_path, monkeypatch):
    """守卫：main() 的所有外部数据源必须可 mock，单测不得发真实网络请求。

    2026-08-25 事故：本文件 4 个用例 mock 了 tencent_daily / iwencai_profit_ratio
    却漏了 fetch_current_year_profit，导致每次跑发布器都真实查询 iWenCai，
    白烧当日额度；额度耗尽后又反过来拖垮整条低筹码发布链路。
    """
    module = load_module()
    history = tmp_path / "history"
    history.mkdir()
    (history / "2026-01-02.json").write_text(json.dumps({
        "data_as_of": "2026-01-02",
        "intersection": ["000012.SZ"],
        "enrichments": {"000012.SZ": {"industry": "测试", "financials": {}, "shareholder_metrics": {}}},
        "periods": {"week": [{"symbol": "000012.SZ", "name": "测试股"}]},
    }), encoding="utf-8")
    data_path = tmp_path / "tracking.json"
    monkeypatch.setattr(module, "HISTORY_DIR", history)
    monkeypatch.setattr(module, "DATA", data_path)

    # 任何真实外部调用都让用例失败
    def forbid_subprocess(*_a, **_kw):
        pytest.fail("unit test attempted a real subprocess/network call")

    monkeypatch.setattr(module.subprocess, "run", forbid_subprocess)
    monkeypatch.setattr(module, "chip_list_profit_ratios", lambda *_a, **_k: {})
    monkeypatch.setattr(module, "tencent_daily", lambda *_a, **_kw: [
        {"date": "2026-01-02", "close": 10.0, "change_pct": 0.0},
    ])
    monkeypatch.setattr(module, "iwencai_profit_ratio", lambda *_a, **_kw: None)
    monkeypatch.setattr(module, "iwencai_profit_ratios", lambda pairs: {pair: None for pair in pairs})
    monkeypatch.setattr(module, "iwencai_profit_ratios", lambda pairs: {pair: None for pair in pairs})
    monkeypatch.setattr(module, "fetch_current_year_profit", lambda codes: {c: None for c in codes})

    assert module.main(target_day="2026-01-02") == 0
    assert data_path.exists()


def test_tracking_main_preserves_existing_bars_when_tencent_returns_nothing(tmp_path, monkeypatch):
    """Regression: empty Tencent response must not erase already-saved history rows."""
    module = load_module()
    mock_gap_fallbacks(module, monkeypatch)
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
    monkeypatch.setattr(module, "iwencai_profit_ratios", lambda pairs: {pair: None for pair in pairs})
    monkeypatch.setattr(module, "fetch_current_year_profit", lambda codes: {c: None for c in codes})
    rc = module.main(target_day="2026-01-05")
    assert rc != 0
    result = json.loads(module.DATA.read_text())
    existing = result["stocks"]["000012.SZ"]["daily"]
    assert len(existing) == 2, f"Expected 2 preserved rows, got {len(existing)}: {existing}"


def test_tencent_row_rejects_non_finite_close():
    module = load_module()
    assert module._valid_tencent_row(["2026-08-20", "1", "Infinity", "1", "1", "1"]) is False
    assert module._valid_tencent_row(["2026-08-20", "1", "NaN", "1", "1", "1"]) is False


def test_load_existing_fails_closed_on_corrupt_tracking_file(tmp_path, monkeypatch):
    module = load_module()
    data_path = tmp_path / "tracking.json"
    original = b'{"stocks":'
    data_path.write_bytes(original)
    monkeypatch.setattr(module, "DATA", data_path)

    with pytest.raises(RuntimeError, match="invalid existing tracking data"):
        module.load_existing()
    assert data_path.read_bytes() == original


def test_normalize_daily_replaces_non_finite_optional_metrics_with_none():
    module = load_module()
    rows = module.normalize_daily([
        {"date": "2026-08-20", "close": 10.0, "change_pct": float("inf"), "profit_ratio": float("nan")},
    ], "2026-08-20")
    assert rows == [
        {"date": "2026-08-20", "close": 10.0, "change_pct": None, "profit_ratio": None},
    ]


def test_tracking_main_preserves_dates_missing_from_partial_tencent_reply(tmp_path, monkeypatch):
    module = load_module()
    history = tmp_path / "history"
    history.mkdir()
    snapshot = {
        "intersection": ["000012.SZ"],
        "enrichments": {"000012.SZ": {"industry": "建材", "financials": {}}},
        "periods": {"week": [{"symbol": "000012.SZ", "name": "南玻A"}]},
    }
    (history / "2026-01-01.json").write_text(json.dumps(snapshot), encoding="utf-8")
    data_path = tmp_path / "tracking.json"
    original = [
        {"date": "2026-01-01", "close": 10.0, "change_pct": None, "profit_ratio": 1.0},
        {"date": "2026-01-02", "close": 10.1, "change_pct": 1.0, "profit_ratio": 1.1},
    ]
    data_path.write_text(json.dumps({"stocks": {"000012.SZ": {
        "name": "南玻A", "first_seen": "2026-01-01", "last_seen": "2026-01-01",
        "industry": "建材", "daily": original,
    }}}), encoding="utf-8")
    monkeypatch.setattr(module, "HISTORY_DIR", history)
    monkeypatch.setattr(module, "DATA", data_path)
    monkeypatch.setattr(module, "tencent_daily", lambda *_a, **_kw: [
        {"date": "2026-01-02", "close": 10.1, "change_pct": 1.0},
    ])
    monkeypatch.setattr(module, "iwencai_profit_ratio", lambda *_a, **_kw: None)
    monkeypatch.setattr(module, "iwencai_profit_ratios", lambda pairs: {pair: None for pair in pairs})
    monkeypatch.setattr(module, "fetch_current_year_profit", lambda codes: {c: None for c in codes})

    assert module.main(target_day="2026-01-02") == 0
    result = json.loads(data_path.read_text(encoding="utf-8"))
    assert [row["date"] for row in result["stocks"]["000012.SZ"]["daily"]] == ["2026-01-01", "2026-01-02"]


def test_duplicate_daily_dates_do_not_mark_tracking_complete(tmp_path, monkeypatch):
    module = load_module()
    monkeypatch.setattr(module, "chip_list_profit_ratios", lambda *_a, **_k: {})
    history = tmp_path / "history"
    history.mkdir()
    snapshot = {
        "intersection": ["600000.SH"],
        "enrichments": {"600000.SH": {"industry": "银行", "financials": {}}},
        "periods": {"week": [{"symbol": "600000.SH", "name": "浦发银行"}]},
    }
    (history / "2026-01-01.json").write_text(json.dumps(snapshot), encoding="utf-8")
    duplicate = {"date": "2026-01-01", "close": 10.0, "change_pct": None, "profit_ratio": 1.0}
    data_path = tmp_path / "tracking.json"
    data_path.write_text(json.dumps({"stocks": {"600000.SH": {
        "name": "浦发银行", "first_seen": "2026-01-01", "last_seen": "2026-01-01",
        "industry": "银行", "daily": [duplicate] * 21,
    }}}), encoding="utf-8")
    calls = []
    monkeypatch.setattr(module, "HISTORY_DIR", history)
    monkeypatch.setattr(module, "DATA", data_path)
    monkeypatch.setattr(module, "tencent_daily", lambda *_a, **_kw: calls.append(True) or [duplicate] + [
        {"date": "2026-01-02", "close": 10.1, "change_pct": 1.0},
    ] * 21)
    monkeypatch.setattr(module, "iwencai_profit_ratio", lambda *_a, **_kw: None)
    monkeypatch.setattr(module, "iwencai_profit_ratios", lambda pairs: {pair: None for pair in pairs})
    monkeypatch.setattr(module, "fetch_current_year_profit", lambda codes: {c: None for c in codes})

    assert module.main(target_day="2026-01-02") == 0
    assert calls == [True]
    result = json.loads(data_path.read_text(encoding="utf-8"))
    assert len(result["stocks"]["600000.SH"]["daily"]) == 2
    assert result["stocks"]["600000.SH"]["tracking_complete"] is False


def test_atomic_write_fsyncs_file_and_parent_directory(tmp_path, monkeypatch):
    module = load_module()
    path = tmp_path / "tracking.json"
    fsync_calls = []
    real_fsync = module.os.fsync

    def spy_fsync(fd):
        fsync_calls.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(module.os, "fsync", spy_fsync)
    module.atomic_write_json(path, {"new": True})

    assert json.loads(path.read_text(encoding="utf-8")) == {"new": True}
    assert len(fsync_calls) == 2


def test_atomic_write_serialization_failure_preserves_old_file(tmp_path):
    module = load_module()
    path = tmp_path / "tracking.json"
    original = b'{"old":true}'
    path.write_bytes(original)

    with pytest.raises(ValueError, match="Out of range float values"):
        module.atomic_write_json(path, {"bad": float("nan")})

    assert path.read_bytes() == original
    assert not list(tmp_path.glob(".tracking.json.*.tmp"))


def test_atomic_write_directory_fsync_failure_reports_replacement(tmp_path, monkeypatch):
    import stat

    mod = load_module()
    path = tmp_path / "tracking.json"
    path.write_text('{"old":true}')
    real_fsync = mod.os.fsync

    def fail_directory(fd):
        if stat.S_ISDIR(mod.os.fstat(fd).st_mode):
            raise OSError("directory fsync failed")
        real_fsync(fd)

    monkeypatch.setattr(mod.os, "fsync", fail_directory)
    with pytest.raises(OSError, match="already replaced; directory durability unconfirmed"):
        mod.atomic_write_json(path, {"new": True})
    assert json.loads(path.read_text()) == {"new": True}
    assert not list(tmp_path.glob(".tracking.json.*.tmp"))


def test_atomic_json_write_uses_replace_and_leaves_valid_file(tmp_path, monkeypatch):
    module = load_module()
    path = tmp_path / "tracking.json"
    path.write_text('{"old":true}', encoding="utf-8")
    replacements = []
    real_replace = module.os.replace

    def spy_replace(source, destination):
        replacements.append((Path(source), Path(destination)))
        real_replace(source, destination)

    monkeypatch.setattr(module.os, "replace", spy_replace)
    module.atomic_write_json(path, {"new": True})

    assert json.loads(path.read_text(encoding="utf-8")) == {"new": True}
    assert len(replacements) == 1
    assert replacements[0][1] == path
    assert not replacements[0][0].exists()


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
    monkeypatch.setattr(module, "fetch_current_year_profit", lambda codes: {c: None for c in codes})

    assert module.main() == 0
    result = json.loads(data_path.read_text(encoding="utf-8"))
    assert len(result["stocks"]["600000.SH"]["daily"]) == 21
    assert result["stocks"]["600000.SH"]["tracking_complete"] is True


def _baostock_payload(rows):
    return json.dumps({"ok": True, "rows": rows, "error": ""})


def test_batched_baostock_requires_explicit_trade_and_positive_volume(monkeypatch):
    module = load_module()
    good = {"date": "2026-11-16", "close": "4.0", "tradestatus": "1", "volume": "100"}
    rows = [good, dict(good, date="2026-11-17", tradestatus=None),
            dict(good, date="2026-11-18", tradestatus="0"), dict(good, date="2026-11-19", volume="0")]
    monkeypatch.setattr(module.subprocess, "run", lambda cmd, **_kw: subprocess.CompletedProcess(
        cmd, 0, json.dumps({"ok": True, "results": {"sh.600185": rows}}), "",
    ))
    result = module.baostock_daily_batch([("600185.SH", "2026-11-16")], "2026-11-19")
    assert [row["date"] for row in result["600185.SH"]] == ["2026-11-16"]


@pytest.mark.parametrize("conflicting", [False, True])
def test_baostock_keeps_first_batch_evidence_when_later_batch_fails(tmp_path, monkeypatch, conflicting):
    mod = load_module()
    prior, target, traded, halted = "2026-11-18", "2026-11-19", "600185.SH", "600186.SH"
    monkeypatch.setattr(mod, "DATA", tmp_path / "tracking.json")
    monkeypatch.setattr(mod, "HISTORY_DIR", tmp_path)
    (tmp_path / f"{prior}.json").write_text(json.dumps({
        "intersection": [traded, halted], "enrichments": {traded: {}, halted: {}},
    }))
    previous = {"date": prior, "close": 8.0, "profit_ratio": 12.0}
    mod.DATA.write_text(json.dumps({"stocks": {s: {"first_seen": prior, "daily": [previous]}
                                                for s in (traded, halted)}}))
    original = mod.DATA.read_bytes()
    monkeypatch.setattr(mod, "tencent_daily", lambda *_a, **_kw: [])
    monkeypatch.setattr(mod, "ths_daily", lambda *_a, **_kw: [])
    monkeypatch.setattr(mod, "chip_list_profit_ratios", lambda *_a, **_kw: {})
    monkeypatch.setattr(mod, "time", SimpleNamespace(sleep=lambda _: None))
    report = {s: {"status": "halted", "target_day": target, "source": "eastmoney_suspend_report", "reason": "全日停牌"}
              for s in (traded, halted)}
    monkeypatch.setattr(mod, "confirmed_suspensions", lambda _day: report)
    batch = mod.baostock_daily_batch
    monkeypatch.setattr(mod, "baostock_daily_batch", lambda specs, end, **kw: batch(specs, end, batch_size=1, **kw))
    base = {"tradestatus": "1", "volume": "100"}
    rows = [dict(base, date=target, close="10"), dict(base, date=prior, close="8"), dict(base, date=target, close="11" if conflicting else "10")]
    queried = []

    def fake_run(cmd, *, input, **_kwargs):
        code, = json.loads(input)["codes"]
        queried.append(code)
        if code == "sh.600186":
            raise subprocess.TimeoutExpired(cmd, 300)
        return subprocess.CompletedProcess(cmd, 0, json.dumps({"ok": True, "results": {code: rows}}), "")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    result = mod.main(target_day=target, skip_iwencai=True)
    assert queried == ["sh.600185", "sh.600186"]
    if conflicting:
        assert result != 0
        assert mod.DATA.read_bytes() == original
    else:
        assert result == 0
        records = json.loads(mod.DATA.read_text())["stocks"]
        assert records[traded]["daily"] == [previous, {"date": target, "close": 10.0, "change_pct": 25.0, "profit_ratio": None}]
        assert "coverage" not in records[traded]
        assert records[halted]["coverage"] == report[halted]


def test_baostock_daily_maps_rows_and_change_pct(monkeypatch):
    """BaoStock 备用源：字段映射、停牌行跳过、区间外行只用于计算涨跌幅。"""
    module = load_module()
    rows = [
        {"date": "2026-09-09", "close": "10.00", "tradestatus": "1"},
        {"date": "2026-09-10", "close": "10.50", "tradestatus": "1"},
        {"date": "2026-09-11", "close": "10.60", "tradestatus": "0"},
        {"date": "2026-09-14", "close": "10.92", "tradestatus": "1"},
        {"date": "2026-09-15", "close": "", "tradestatus": "1"},
    ]
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["payload"] = json.loads(kwargs["input"])

        class Proc:
            returncode = 0
            stdout = _baostock_payload(rows)
            stderr = ""

        return Proc()

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    bars = module.baostock_daily("000858.SZ", "2026-09-10", "2026-09-15")

    assert captured["payload"]["code"] == "sz.000858"
    assert captured["payload"]["end"] == "2026-09-15"
    assert captured["payload"]["start"] < "2026-09-10"
    # 停牌 09-11 与空收盘 09-15 被跳过；09-10 的涨跌幅仍用区间外 09-09 基准
    assert [bar["date"] for bar in bars] == ["2026-09-10", "2026-09-14"]
    assert bars[0]["close"] == 10.5 and bars[0]["change_pct"] == 5.0
    assert bars[1]["change_pct"] == 4.0


def test_baostock_daily_raises_when_runner_reports_failure(monkeypatch):
    module = load_module()

    def fake_run(*_a, **_kw):
        class Proc:
            returncode = 0
            stdout = json.dumps({"ok": False, "rows": [], "error": "login: 网络错误"})
            stderr = ""

        return Proc()

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="baostock unavailable"):
        module.baostock_daily("000858.SZ", "2026-09-10", "2026-09-15")


def test_tracking_main_uses_baostock_when_tencent_unavailable(tmp_path, monkeypatch):
    """Regression 2026-09-15：腾讯 fqkline 被网关 501 拦截时改走 BaoStock，
    不能让当日行情整段缺失。"""
    module = load_module()
    mock_gap_fallbacks(module, monkeypatch)
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
                                 ]}},
    }))

    def tencent_blocked(*_a, **_kw):
        raise RuntimeError("Tencent fqkline unavailable for 000012.SZ after 4 attempts: HTTP Error 501: Not Implemented")

    calls = {"baostock": 0}

    def fallback_batch(specs, end, **_kwargs):
        calls["baostock"] += 1
        return {
            symbol: [
                {"date": "2026-01-02", "close": 10.0, "change_pct": 0.0},
                {"date": "2026-01-05", "close": 10.4, "change_pct": 4.0},
            ]
            for symbol, _start in specs
        }

    monkeypatch.setattr(module, "tencent_daily", tencent_blocked)
    monkeypatch.setattr(module, "baostock_daily_batch", fallback_batch)
    monkeypatch.setattr(module, "iwencai_profit_ratio", lambda *_a, **_kw: None)
    monkeypatch.setattr(module, "iwencai_profit_ratios", lambda pairs: {pair: None for pair in pairs})
    monkeypatch.setattr(module, "iwencai_profit_ratios", lambda pairs: {pair: None for pair in pairs})
    monkeypatch.setattr(module, "chip_list_profit_ratios", lambda *_a, **_kw: {})
    monkeypatch.setattr(module, "fetch_current_year_profit", lambda codes: {c: None for c in codes})

    assert module.main(target_day="2026-01-05") == 0
    assert calls["baostock"] == 1
    daily = json.loads(module.DATA.read_text(encoding="utf-8"))["stocks"]["000012.SZ"]["daily"]
    assert [row["date"] for row in daily] == ["2026-01-02", "2026-01-05"]
    assert daily[1]["close"] == 10.4


def test_tracking_main_batches_the_baostock_fallback(tmp_path, monkeypatch):
    """腾讯被 WAF 拦截的标的必须一次批量取，不能逐只登录 BaoStock。

    2026-10-28: 377 只标的逐只取 K 线，每次重新登录 BaoStock ≈ 1.4s，
    总计 8.5 分钟；批量取（一次登录 20 只）≈ 70s。
    """
    module = load_module()
    mock_gap_fallbacks(module, monkeypatch)
    history = tmp_path / "low-chip-history"
    history.mkdir()
    (history / "2026-01-02.json").write_text(json.dumps({
        "enrichments": {
            f"{code}.SZ": {"quality_shareholder": False, "quality_shareholder_names": [],
                           "institutional_shareholder": False, "institutional_shareholder_names": []}
            for code in ("000012", "000013", "000014")
        },
        "periods": {},
    }))
    monkeypatch.setattr(module, "HISTORY_DIR", history)
    monkeypatch.setattr(module, "DATA", tmp_path / "tracking.json")

    def tencent_blocked(*_a, **_kw):
        raise RuntimeError("Tencent fqkline unavailable: HTTP Error 501: Not Implemented")

    batches = []

    def fallback_batch(specs, end, **_kwargs):
        batches.append([symbol for symbol, _start in specs])
        return {
            symbol: [{"date": "2026-01-02", "close": 10.0, "change_pct": 0.0}]
            for symbol, _start in specs
        }

    monkeypatch.setattr(module, "tencent_daily", tencent_blocked)
    monkeypatch.setattr(module, "baostock_daily_batch", fallback_batch)
    monkeypatch.setattr(module, "iwencai_profit_ratio", lambda *_a, **_kw: None)
    monkeypatch.setattr(module, "iwencai_profit_ratios", lambda pairs: {pair: None for pair in pairs})
    monkeypatch.setattr(module, "iwencai_profit_ratios", lambda pairs: {pair: None for pair in pairs})
    monkeypatch.setattr(module, "chip_list_profit_ratios", lambda *_a, **_kw: {})
    monkeypatch.setattr(module, "fetch_current_year_profit", lambda codes: {c: None for c in codes})
    monkeypatch.setattr(module, "load_history_dates", lambda: {
        "000012.SZ": ["2026-01-02"], "000013.SZ": ["2026-01-02"], "000014.SZ": ["2026-01-02"],
    })

    assert module.main(target_day="2026-01-02") == 0
    assert len(batches) == 1
    assert sorted(batches[0]) == ["000012.SZ", "000013.SZ", "000014.SZ"]


def test_tencent_daily_fails_fast_on_gateway_rejection():
    """501/403 是网关/WAF 拒绝，不是瞬时故障：每个 host 只撞一次就换下一个，
    两个 host 都拒后立刻失败交给备用源，不能按 1s/2s/5s 空转三轮
    （否则 200+ 只标的的追踪步骤必然超时）。"""
    module = load_module()
    calls = []
    sleeps = []

    def opener(request, **_kwargs):
        calls.append(request.full_url)
        raise urllib.error.HTTPError(request.full_url, 501, "Not Implemented", {}, None)

    with pytest.raises(RuntimeError, match="501"):
        module.tencent_daily(
            "000012.SZ", "2026-08-19", "2026-08-20",
            opener=opener, sleeper=sleeps.append,
        )
    # 双 host 各一次；WAF 拒绝不做退避空转
    assert len(calls) == 2
    assert sleeps == []
    assert any("web.ifzq.gtimg.cn" in url for url in calls)
    assert any("web.ifzq.gtimg.cn" not in url for url in calls)


def test_tencent_daily_still_retries_transient_transport_error():
    """对照：真正的瞬时传输错误仍然要按退避重试。"""
    module = load_module()
    calls = []
    sleeps = []

    def opener(request, **_kwargs):
        calls.append(request.full_url)
        raise TimeoutError("read timed out")

    with pytest.raises(RuntimeError):
        module.tencent_daily(
            "000012.SZ", "2026-08-19", "2026-08-20",
            opener=opener, sleeper=sleeps.append,
        )
    assert len(calls) == 8
    assert sleeps == [1.0, 2.0, 5.0, 1.0, 2.0, 5.0]


def test_iwencai_batch_query_answers_many_symbols_in_one_call(monkeypatch):
    """iWenCai accepts "CODE1、CODE2 … 收盘获利[YYYYMMDD]"。

    2026-10-28: the tracking publisher resolved 获利盘 with one iWenCai call
    per (symbol, date). 340 symbols × ~6s per call blew the 1800s budget and
    the 16:40 publish died with a timeout. One batched call must serve many
    symbols for the same date.
    """
    module = load_module()
    queries = []

    def fake_run(command, **_kwargs):
        queries.append(command[command.index("-q") + 1])
        codes = command[command.index("-q") + 1].split(" 收盘获利")[0].split("、")
        date = command[command.index("-q") + 1].split("[")[1].rstrip("]")
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({
                "datas": [
                    {"股票代码": f"{code}.SZ", f"收盘获利[{date}]": index + 1.5}
                    for index, code in enumerate(codes)
                ],
            }),
            stderr="",
        )

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    values = module._iwencai_batch_query(["000012", "600000", "600519"], "2026-10-27")

    assert values == {"000012": 1.5, "600000": 2.5, "600519": 3.5}
    assert len(queries) == 1
    assert "、" in queries[0]


def test_iwencai_batch_query_retries_an_empty_answer(monkeypatch):
    """The upstream sometimes answers a batch query with an empty result set."""
    module = load_module()
    attempts = []

    def fake_run(command, **_kwargs):
        attempts.append(command)
        if len(attempts) == 1:
            return SimpleNamespace(returncode=0, stdout=json.dumps({"datas": []}), stderr="")
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"datas": [{"股票代码": "000012.SZ", "收盘获利[20261027]": 3.5}]}),
            stderr="",
        )

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    values = module._iwencai_batch_query(["000012"], "2026-10-27")

    assert values == {"000012": 3.5}
    assert len(attempts) == 2


def test_iwencai_profit_ratios_groups_pairs_by_date(monkeypatch):
    module = load_module()
    seen = []

    def fake_batch(codes, date):
        seen.append((tuple(codes), date))
        return {code: 1.0 for code in codes}

    monkeypatch.setattr(module, "_iwencai_batch_query", fake_batch)

    result = module.iwencai_profit_ratios([
        ("000012.SZ", "2026-10-27"),
        ("600000.SH", "2026-10-27"),
        ("600519.SH", "2026-10-28"),
    ])

    assert result[("000012.SZ", "2026-10-27")] == 1.0
    assert result[("600519.SH", "2026-10-28")] == 1.0
    assert seen == [(("000012", "600000"), "2026-10-27"), (("600519",), "2026-10-28")]


def test_chip_list_window_is_anchored_to_first_seen(monkeypatch):
    """同花顺 chip_list only returns the most recent ~40 trading days of the
    requested window. Anchoring the window to first_seen keeps the early bars
    of a long-tracked symbol reachable, so the iWenCai fallback is not needed.
    """
    module = load_module()
    urls = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            payload = {
                "data": {
                    "list": {
                        "20261027": {
                            "summary": {"close_price": 10.0},
                            "curve_data": {"list": [{"price": 1.0, "jeton": 100}]},
                        }
                    }
                }
            }
            return json.dumps(payload).encode()

    def fake_open(request, **_kwargs):
        urls.append(request.full_url)
        return Response()

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_open)

    values = module.chip_list_profit_ratios("000012.SZ", start="2026-07-31")

    assert values == {"20261027": 100.0}
    assert "start_date=" in urls[0]
    anchor = urls[0].split("start_date=")[1].split("&")[0]
    assert int(anchor) == int(datetime.datetime(2026, 7, 31).timestamp() * 1000)

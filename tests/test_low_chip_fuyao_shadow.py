import importlib.util
import json
import urllib.error
import urllib.parse
from io import BytesIO
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "audit_low_chip_fuyao.py"


def load_module():
    spec = importlib.util.spec_from_file_location("audit_low_chip_fuyao", SCRIPT)
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


def test_fuyao_client_retries_429_with_bounded_backoff():
    module = load_module()
    calls = []
    sleeps = []
    clock = [10.0]

    def monotonic():
        return clock[0]

    def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds

    def opener(request, timeout=30):
        calls.append((request.full_url, request.get_header("X-api-key"), timeout))
        if len(calls) == 1:
            raise urllib.error.HTTPError(
                request.full_url,
                429,
                "Too Many Requests",
                {},
                BytesIO(b'{"code":429,"message":"request limit exceeded"}'),
            )
        return Response({"code": 0, "message": "success", "data": {"item": []}})

    client = module.FuyaoClient(
        "test-key",
        qps=2.0,
        opener=opener,
        sleeper=sleep,
        monotonic=monotonic,
    )
    payload = client.get("/api/meta/tickers/search?q=test")

    assert payload["code"] == 0
    assert len(calls) == 2
    assert all(call[1] == "test-key" for call in calls)
    assert 2.0 in sleeps


def test_fuyao_client_paces_successive_requests():
    module = load_module()
    sleeps = []
    clock = [20.0]

    def monotonic():
        return clock[0]

    def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds

    client = module.FuyaoClient(
        "test-key",
        qps=2.0,
        opener=lambda *_args, **_kwargs: Response({"code": 0, "message": "success", "data": {"item": []}}),
        sleeper=sleep,
        monotonic=monotonic,
    )
    client.get("/one")
    client.get("/two")

    assert sleeps == [0.5]


def test_fuyao_client_retries_transient_business_errors():
    module = load_module()
    calls = []
    sleeps = []

    def opener(*_args, **_kwargs):
        calls.append(1)
        if len(calls) == 1:
            return Response({"code": 5002, "message": "upstream timeout", "data": None})
        return Response({"code": 0, "message": "success", "data": {"item": []}})

    client = module.FuyaoClient(
        "test-key",
        qps=2.0,
        opener=opener,
        sleeper=sleeps.append,
        monotonic=lambda: 10.0 + sum(sleeps),
    )
    payload = client.get("/api/a-share/prices/snapshot?thscodes=600519.SH")

    assert payload["code"] == 0
    assert len(calls) == 2
    assert 2.0 in sleeps


def test_batch_endpoints_chunk_at_fuyao_100_token_limit():
    module = load_module()
    client = module.FuyaoClient("test-key")
    paths = []

    def fake_get(path):
        paths.append(path)
        query = path.split("thscodes=", 1)[1]
        codes = urllib.parse.unquote_plus(query).split(",")
        return {"code": 0, "data": {"item": [{"thscode": code} for code in codes]}}

    client.get = fake_get
    codes = [f"{index:06d}.SZ" for index in range(101)]

    assert len(client.snapshots(codes)) == 101
    assert len(client.valuations(codes)) == 101
    assert len(paths) == 4
    assert all(len(urllib.parse.unquote_plus(path.split("thscodes=", 1)[1]).split(",")) <= 100 for path in paths)


def test_build_audit_compares_iwencai_snapshot_without_changing_formal_payload():
    module = load_module()
    source = {
        "data_as_of": "2026-08-19",
        "intersection": ["000001.SZ", "600000.SH"],
        "periods": {
            "week": [
                {"symbol": "000001.SZ", "name": "平安银行", "price": 10.0},
                {"symbol": "600000.SH", "name": "浦发银行", "price": 12.0},
            ]
        },
        "enrichments": {
            "000001.SZ": {"financials": {"report_period": "20251231", "roe": 12.0, "net_margin": 30.0, "gross_margin": 40.0, "debt_ratio": 70.0, "cash_profit_ratio": 110.0}},
            "600000.SH": {"financials": {"report_period": "20251231", "roe": 8.0, "net_margin": 20.0, "gross_margin": 30.0, "debt_ratio": 60.0, "cash_profit_ratio": 90.0}},
        },
    }
    original = json.loads(json.dumps(source))

    class Client:
        def snapshots(self, codes):
            assert codes == source["intersection"]
            return {
                "000001.SZ": {"last_price": 10.0},
                "600000.SH": {"last_price": 12.5},
            }

        def valuations(self, codes):
            return {code: {"pe_ttm": 10.0, "pb_mrq": 1.0} for code in codes}

        def financials(self, code, report):
            assert report == "2025-4"
            if code == "000001.SZ":
                return {"roe": 12.0, "net_margin": 30.0, "gross_margin": 40.0, "debt_ratio": 70.0, "cash_profit_ratio": 110.0}
            return {"roe": 8.0, "net_margin": 20.0, "gross_margin": 30.0, "debt_ratio": 60.0, "cash_profit_ratio": 90.0}

    audit = module.build_audit(source, Client())

    assert source == original
    assert audit["mode"] == "shadow"
    assert audit["production_effect"] == "none"
    assert audit["summary"]["symbols"] == 2
    assert audit["summary"]["price_matches"] == 1
    assert audit["summary"]["price_mismatches"] == 1
    assert audit["summary"]["financial_matches"] == 10
    assert audit["summary"]["financial_mismatches"] == 0
    assert audit["rows"]["000001.SZ"]["valuation"]["pe_ttm"] == 10.0


def test_build_audit_marks_missing_fuyao_financial_values_unavailable():
    module = load_module()
    source = {
        "data_as_of": "2026-08-19",
        "intersection": ["000001.SZ"],
        "periods": {"week": [{"symbol": "000001.SZ", "name": "平安银行", "price": 10.0}]},
        "enrichments": {"000001.SZ": {"financials": {"report_period": "20251231", "roe": 12.0}}},
    }

    class Client:
        def snapshots(self, _codes):
            return {"000001.SZ": {"last_price": 10.0}}

        def valuations(self, _codes):
            return {}

        def financials(self, _code, _report):
            return {}

    audit = module.build_audit(source, Client())

    assert audit["summary"]["financial_unavailable"] == 5
    assert audit["rows"]["000001.SZ"]["financial_checks"]["roe"]["status"] == "unavailable"


def test_load_api_key_reads_private_env_without_exposing_it(tmp_path):
    module = load_module()
    env_file = tmp_path / "fuyao.env"
    env_file.write_text("HITHINK_FINANCE_API_KEY=test-secret\n", encoding="utf-8")

    assert module.load_api_key(env_file, environ={}) == "test-secret"


def test_failure_audit_is_explicit_and_keeps_production_unchanged():
    module = load_module()
    audit = module.build_failure_audit("2026-08-19", 55, "Fuyao HTTP 429: request limit exceeded")

    assert audit["status"] == "unavailable"
    assert audit["mode"] == "shadow"
    assert audit["production_effect"] == "none"
    assert audit["summary"]["source_symbols"] == 55
    assert audit["error"] == "Fuyao HTTP 429: request limit exceeded"

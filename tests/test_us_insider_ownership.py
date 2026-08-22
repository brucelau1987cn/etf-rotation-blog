"""Mock-based tests for build_us_insider_ownership.py — no live SEC requests."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_us_insider_ownership import (  # noqa: E402
    CODE_LABEL,
    UNIVERSE,
    fetch_insider_trades,
    fetch_institution_holdings,
    normalize_issuer,
    parse_13f_all,
    parse_form4,
    ticker_to_cik,
)

F4_FIXTURE = Path("/tmp/f4.xml")
F13F_FIXTURE = Path("/tmp/13f-infotable.xml")


class FakeClient:
    def __init__(self, routes):
        self.routes = routes

    def get(self, url, retries=3):
        for key, body in self.routes.items():
            if key in url:
                return body if isinstance(body, bytes) else body.encode()
        raise AssertionError(f"unmocked url: {url}")


def test_ticker_to_cik_parses_and_zero_pads():
    data = {
        "0": {"ticker": "AAPL", "cik_str": 320193},
        "1": {"ticker": "MSFT", "cik_str": 789019},
    }
    client = FakeClient({"company_tickers.json": json.dumps(data)})
    cik_map = ticker_to_cik(client)
    assert cik_map["AAPL"] == "0000320193"
    assert cik_map["MSFT"] == "0000789019"


def test_parse_form4_extracts_insider_trade():
    xml = F4_FIXTURE.read_bytes()
    rows = parse_form4(xml)
    assert len(rows) == 1
    row = rows[0]
    assert row["owner_name"] == "Newstead Jennifer"
    assert row["title"] == "SVP, GC and Secretary"
    assert row["symbol"] == "AAPL"
    assert row["code"] == "S"
    assert row["code_label"] == "卖出"
    assert row["shares"] == "1439"
    assert row["price"] == "307.49"
    assert row["date"] == "2026-08-18"
    assert row["shares_after"] == "38668"


def test_fetch_insider_trades_uses_atom_then_form4():
    atom = (
        '<?xml version="1.0"?><feed><entry>'
        "<accession-number>0001140361-26-033928</accession-number>"
        "</entry></feed>"
    )
    index_html = (
        '<html><a href="/Archives/edgar/data/320193/000114036126033928/'
        'xslF345X06/form4.xml">render</a>'
        '<a href="/Archives/edgar/data/320193/000114036126033928/form4.xml">body</a></html>'
    )
    f4 = F4_FIXTURE.read_bytes()
    client = FakeClient({
        "browse-edgar": atom,
        "index.htm": index_html,
        "form4.xml": f4,
    })
    trades = fetch_insider_trades(client, "0000320193")
    assert len(trades) == 1
    assert trades[0]["symbol"] == "AAPL"
    assert trades[0]["code"] == "S"


def test_fetch_insider_trades_skips_xsl_render_copy():
    atom = (
        '<?xml version="1.0"?><feed><entry>'
        "<accession-number>0001310264-26-000008</accession-number>"
        "</entry></feed>"
    )
    index_html = (
        '<html><a href="/Archives/edgar/data/1045810/000131026426000008/'
        'xslF345X06/wk-form4_1786569187.xml">render</a>'
        '<a href="/Archives/edgar/data/1045810/000131026426000008/'
        'wk-form4_1786569187.xml">body</a></html>'
    )
    f4 = F4_FIXTURE.read_bytes()
    client = FakeClient({
        "browse-edgar": atom,
        "index.htm": index_html,
        "wk-form4_1786569187.xml": f4,
    })
    trades = fetch_insider_trades(client, "0001045810")
    assert len(trades) == 1


def test_code_label_covers_buy_and_sell():
    assert CODE_LABEL["P"] == "买入"
    assert CODE_LABEL["S"] == "卖出"
    assert CODE_LABEL["M"] == "期权行权"


def test_universe_is_expected_mag7_and_themes():
    assert "NVDA" in UNIVERSE
    assert "COIN" in UNIVERSE
    assert "PLTR" in UNIVERSE
    assert len(UNIVERSE) >= 10


def test_normalize_issuer_strips_punctuation_and_suffixes():
    assert normalize_issuer("Apple Inc.") == "APPLE"
    assert normalize_issuer("APPLE INC") == "APPLE"
    assert normalize_issuer("Meta Platforms, Inc.") == "META PLATFORMS"
    assert normalize_issuer("Alphabet Inc CL A") == "ALPHABET"
    assert normalize_issuer("NVIDIA CORP") == "NVIDIA"
    assert normalize_issuer("AAPL") == "AAPL"


def test_parse_13f_all_parses_every_holding_and_sums_duplicates():
    brk = Path("/tmp/brk-13f.xml").read_bytes()
    holdings = parse_13f_all(brk)
    assert "APPLE" in holdings
    # Berkshire splits AAPL across 12 sub-accounts; parse_13f_all sums them.
    assert holdings["APPLE"]["shares"] == 227917808
    assert holdings["APPLE"]["value_usd"] == 65950296923
    assert len(holdings) > 10  # Berkshire holds many positions


def test_fetch_institution_holdings_finds_targets():
    submissions = {
        "filings": {
            "recent": {
                "form": ["13F-HR", "10-Q", "10-K"],
                "accessionNumber": ["0001193125-26-352200", "x", "y"],
                "filingDate": ["2026-08-14", "x", "y"],
                "reportDate": ["2026-06-30", "x", "y"],
            }
        }
    }
    index_html = (
        '<html><a href="/Archives/edgar/data/1067983/000119312526352200/'
        'xslForm13F_X02/primary_doc.xml">p1</a>'
        '<a href="/Archives/edgar/data/1067983/000119312526352200/primary_doc.xml">p2</a>'
        '<a href="/Archives/edgar/data/1067983/000119312526352200/56757.xml">info</a></html>'
    )
    info_table = Path("/tmp/brk-13f.xml").read_bytes()
    client = FakeClient({
        "submissions": json.dumps(submissions),
        "index.htm": index_html,
        "56757.xml": info_table,
    })
    targets = {"AAPL": ["AAPL", "APPLE INC"]}
    out = fetch_institution_holdings(client, "Berkshire Hathaway", "0001067983", targets)
    assert "AAPL" in out
    assert out["AAPL"]["shares"] == 227917808
    assert out["AAPL"]["value_usd"] == 65950296923
    assert out["AAPL"]["period_ending"] == "2026-06-30"
    assert out["AAPL"]["filed_at"] == "2026-08-14"

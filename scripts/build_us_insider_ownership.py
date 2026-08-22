#!/usr/bin/env python3
"""Build US insider-ownership dataset from SEC EDGAR (Form 4 insider trades).

Read-only collection: outputs a static JSON, never mutates production state.
No API key required; sends a contact User-Agent and respects SEC rate limits.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "public/data/us-insider-ownership.json"

# Contact User-Agent is mandatory for SEC EDGAR (403 without a contact email).
USER_AGENT = "brucelau1987 research brucelau1987@gmail.com"
MIN_INTERVAL = 0.12  # ~8 req/s, under SEC's 10 req/s limit
BASE = "https://www.sec.gov"

UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA",
    "AMD", "AVGO", "MU",
    "COIN", "MSTR", "PLTR",
]

# Well-known 13F-filing institutions, name -> verified CIK (checked via
# data.sec.gov/submissions on 2026-08-22). These are the "majors" whose
# presence/absence in a stock is a meaningful ownership signal.
INSTITUTIONS: dict[str, str] = {
    "Vanguard Group": "0000102909",
    "BlackRock": "0001364742",
    "State Street": "0000093751",
    "Fidelity (FMR)": "0000315066",
    "Berkshire Hathaway": "0001067983",
    "JPMorgan Chase": "0000019617",
    "Goldman Sachs": "0000886982",
    "Morgan Stanley": "0000895421",
    "Capital Research": "0001422848",
    "Baillie Gifford": "0001088875",
    "Geode Capital": "0001214717",
    "T. Rowe Price": "0000080255",
    "Norges Bank": "0001374170",
    "Northern Trust": "0000073124",
    "Invesco": "0000914208",
    "Wellington Mgmt": "0000902219",
    "Legal & General": "0000764068",
    "ARK Invest": "0001697748",
    "Bank of America": "0000070858",
    "Amundi": "0001330387",
}

CODE_LABEL = {
    "P": "买入", "S": "卖出", "A": "授予", "M": "期权行权",
    "F": "税扣", "G": "赠予", "J": "其他", "K": "股权交换",
    "C": "转换", "D": "处置",
}


class SecClient:
    def __init__(self) -> None:
        self._last = 0.0

    def _throttle(self) -> None:
        wait = self._last + MIN_INTERVAL - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()

    def get(self, url: str, retries: int = 3) -> bytes:
        for attempt in range(retries):
            self._throttle()
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    return resp.read()
            except urllib.error.HTTPError as exc:
                if exc.code in (403, 429) and attempt < retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise
            except urllib.error.URLError:
                if attempt < retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise
        raise RuntimeError(f"unreachable: {url}")


def ticker_to_cik(client: SecClient) -> dict[str, str]:
    """Map ticker -> 10-digit CIK from SEC company_tickers.json."""
    raw = client.get(f"{BASE}/files/company_tickers.json")
    data = json.loads(raw)
    return {
        str(item["ticker"]).upper(): str(item["cik_str"]).zfill(10)
        for item in data.values()
    }


def ticker_to_company(client: SecClient) -> dict[str, dict[str, str]]:
    """Map ticker -> {cik, name} from SEC company_tickers.json."""
    raw = client.get(f"{BASE}/files/company_tickers.json")
    data = json.loads(raw)
    return {
        str(item["ticker"]).upper(): {
            "cik": str(item["cik_str"]).zfill(10),
            "name": item.get("title", ""),
        }
        for item in data.values()
    }


def _first_text(xml: bytes, tag: str) -> str | None:
    text = xml.decode("utf-8", errors="ignore")
    import re
    m = re.search(rf"<{tag}>\s*<value>(.*?)</value>", text, re.S)
    if not m:
        m = re.search(rf"<{tag}>(.*?)</{tag}>", text, re.S)
    return m.group(1).strip() if m else None


def parse_form4(xml: bytes) -> list[dict]:
    """Parse a Form 4 XML into a list of insider transaction records."""
    text = xml.decode("utf-8", errors="ignore")
    import re
    import html as _html

    def clean(v: str | None) -> str | None:
        return _html.unescape(v).strip() if v else None

    owner_name = clean(_first_text(xml, "rptOwnerName"))
    owner_cik = clean(_first_text(xml, "rptOwnerCik"))
    title = clean(_first_text(xml, "officerTitle"))
    symbol = clean(_first_text(xml, "issuerTradingSymbol"))

    def flag(tag: str) -> bool:
        m = re.search(rf"<{tag}>\s*(\d)</{tag}>", text, re.S)
        return m is not None and m.group(1) == "1"

    relationship = {
        "is_director": flag("isDirector"),
        "is_officer": flag("isOfficer"),
        "is_ten_percent_owner": flag("isTenPercentOwner"),
        "is_other": flag("isOther"),
    }

    rows: list[dict] = []
    for kind in ("nonDerivativeTransaction", "derivativeTransaction"):
        for block in re.findall(
            rf"<{kind}>.*?</{kind}>", text, re.S
        ):
            def val(tag: str) -> str | None:
                m = re.search(rf"<{tag}>\s*<value>(.*?)</value>", block, re.S)
                return clean(m.group(1)) if m else None

            code_m = re.search(
                r"<transactionCode>(.*?)</transactionCode>", block, re.S
            )
            code = clean(code_m.group(1)) if code_m else None
            rows.append({
                "owner_name": owner_name,
                "owner_cik": owner_cik,
                "title": title,
                "relationship": relationship,
                "symbol": symbol,
                "code": code,
                "code_label": CODE_LABEL.get(code or "", code or ""),
                "acquired_disposed": val("transactionAcquiredDisposedCode"),
                "shares": val("transactionShares"),
                "price": val("transactionPricePerShare"),
                "date": val("transactionDate"),
                "shares_after": val("sharesOwnedFollowingTransaction"),
                "kind": "derivative" if kind.startswith("derivative") else "direct",
            })
    return rows


def fetch_insider_trades(
    client: SecClient, cik: str, limit: int = 5
) -> list[dict]:
    """Fetch recent Form 4 filings for a company and parse their trades."""
    url = (
        f"{BASE}/cgi-bin/browse-edgar?action=getcompany&CIK={cik}"
        f"&type=4&dateb=&owner=include&count={limit}&output=atom"
    )
    raw = client.get(url)
    text = raw.decode("utf-8", errors="ignore")
    import re

    accessions = re.findall(r"<accession-number>(.*?)</accession-number>", text)
    out: list[dict] = []
    for acc in accessions:
        acc = acc.strip()
        no_dash = acc.replace("-", "")
        index_url = f"{BASE}/Archives/edgar/data/{int(cik)}/{no_dash}/{acc}-index.htm"
        index_html = client.get(index_url).decode("utf-8", errors="ignore")
        # The Form 4 body XML lives in the accession dir; the xsl/ sibling is a
        # render copy. Pick the .xml href that is NOT under an xsl* directory.
        xml_hrefs = re.findall(r'href="([^"]+\.xml)"', index_html)
        body_href = next(
            (h for h in xml_hrefs if "/xsl" not in h.lower()),
            xml_hrefs[0] if xml_hrefs else None,
        )
        if not body_href:
            continue
        xml_url = f"{BASE}{body_href}" if body_href.startswith("/") else body_href
        xml = client.get(xml_url)
        out.extend(parse_form4(xml))
    return out


_ISSUER_SUFFIXES = (
    "CLASS A", "CLASS B", "CLASS C", "CL A", "CL B", "CL C",
    "INC", "CORP", "CORPORATION", "CO", "LTD", "LLC", "PLC",
    "HOLDINGS", "HOLDING", "GROUP", "INTERNATIONAL",
)


def normalize_issuer(name: str) -> str:
    """Normalize an issuer name for matching: strip punctuation + common suffixes."""
    import re

    s = re.sub(r"[^A-Z0-9 ]", " ", (name or "").upper())
    s = re.sub(r"\s+", " ", s).strip()
    for _ in range(4):
        changed = False
        for suf in _ISSUER_SUFFIXES:
            if s.endswith(" " + suf):
                s = s[: -(len(suf) + 1)].strip()
                changed = True
            elif s == suf:
                s = ""
                changed = True
        if not changed:
            break
    return s


def parse_13f_all(xml: bytes) -> dict[str, dict]:
    """Parse every holding in a 13F information table, keyed by normalized issuer."""
    text = xml.decode("utf-8", errors="ignore")
    import re

    tag = r"(?:\w+:)?"
    out: dict[str, dict] = {}
    for block in re.findall(rf"<{tag}infoTable>.*?</{tag}infoTable>", text, re.S):
        issuer_m = re.search(rf"<{tag}nameOfIssuer>\s*(.*?)\s*</{tag}nameOfIssuer>", block, re.S)
        if not issuer_m:
            continue
        issuer = normalize_issuer(issuer_m.group(1))
        if not issuer:
            continue
        value_m = re.search(rf"<{tag}value>\s*(\d+)\s*</{tag}value>", block, re.S)
        shares_m = re.search(rf"<{tag}sshPrnamt>\s*([\d.]+)\s*</{tag}sshPrnamt>", block, re.S)
        shares = float(shares_m.group(1)) if shares_m else 0.0
        value = int(value_m.group(1)) if value_m else 0
        if issuer in out:
            out[issuer]["shares"] += shares
            out[issuer]["value_usd"] += value
        else:
            out[issuer] = {"shares": shares, "value_usd": value}
    return out


def fetch_institution_holdings(
    client: SecClient, name: str, cik: str, targets: dict[str, list[str]]
) -> dict[str, dict]:
    """Look up one institution's latest 13F and return its target-stock holdings.

    ``targets`` maps ticker -> acceptable issuer spellings. Returns
    {ticker: {shares, value_usd, period_ending, filed_at}}.
    """
    import re

    sub = json.loads(client.get(f"https://data.sec.gov/submissions/CIK{cik}.json"))
    recent = sub.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    idx = [i for i, f in enumerate(forms) if f == "13F-HR"]
    if not idx:
        return {}
    i = idx[0]
    acc = recent["accessionNumber"][i]
    filed = recent["filingDate"][i]
    period = recent.get("reportDate", [""])[i]
    no_dash = acc.replace("-", "")
    index_url = f"{BASE}/Archives/edgar/data/{int(cik)}/{no_dash}/{acc}-index.htm"
    index_html = client.get(index_url).decode("utf-8", errors="ignore")
    xml_hrefs = re.findall(r'href="([^"]+\.xml)"', index_html)
    # info table is a non-xsl .xml that is not the primary_doc cover
    body = next(
        (h for h in xml_hrefs if "/xsl" not in h.lower() and "primary_doc" not in h.lower()),
        None,
    )
    if not body:
        return {}
    xml_url = f"{BASE}{body}" if body.startswith("/") else body
    holdings = parse_13f_all(client.get(xml_url))

    out: dict[str, dict] = {}
    for ticker, spellings in targets.items():
        for sp in spellings:
            key = normalize_issuer(sp)
            if key and key in holdings:
                out[ticker] = {
                    "shares": holdings[key]["shares"],
                    "value_usd": holdings[key]["value_usd"],
                    "period_ending": period,
                    "filed_at": filed,
                }
                break
    return out


def main() -> None:
    client = SecClient()
    company_map = ticker_to_company(client)

    targets: dict[str, list[str]] = {}
    for ticker in UNIVERSE:
        info = company_map.get(ticker, {})
        targets[ticker] = [ticker, info.get("name", "")]

    stocks: dict[str, dict] = {}
    for ticker in UNIVERSE:
        info = company_map.get(ticker, {})
        cik = info.get("cik")
        entry: dict = {"cik": cik, "insider_transactions": [], "institutional_holders": []}
        if cik:
            try:
                entry["insider_transactions"] = fetch_insider_trades(client, cik)
            except Exception as exc:  # per-stock isolation
                entry["insider_error"] = str(exc)
        stocks[ticker] = entry

    # Forward-lookup each institution's latest 13F and aggregate per stock.
    for name, cik in INSTITUTIONS.items():
        try:
            per_stock = fetch_institution_holdings(client, name, cik, targets)
        except Exception:
            continue  # a failed institution must not sink the run
        for ticker, h in per_stock.items():
            stocks[ticker]["institutional_holders"].append({
                "holder_name": name,
                "holder_cik": cik,
                **h,
            })

    for ticker in UNIVERSE:
        stocks[ticker]["institutional_holders"].sort(
            key=lambda r: -(r.get("value_usd") or 0)
        )

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data_source": "SEC EDGAR",
        "schema_version": "us-insider-ownership-v2",
        "universe": UNIVERSE,
        "institutions": INSTITUTIONS,
        "stocks": stocks,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({len(stocks)} stocks, {len(INSTITUTIONS)} institutions)")


if __name__ == "__main__":
    main()

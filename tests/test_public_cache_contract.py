from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent.parent

CACHEABLE_CLIENTS = [
    ROOT / "public" / "js" / "a-compass-app.js",
    ROOT / "public" / "js" / "a-rolling-app.js",
    ROOT / "public" / "js" / "etf-live-poll.js",
    ROOT / "public" / "js" / "futures-compass-app.js",
    ROOT / "public" / "js" / "home-live-app.js",
    ROOT / "public" / "js" / "jin10-calendar-app.js",
    ROOT / "public" / "js" / "price-volume-tag.js",
    ROOT / "public" / "js" / "us-compass-app.js",
    ROOT / "public" / "js" / "a-momentum-app.js",
    ROOT / "public" / "js" / "us-momentum-app.js",
    ROOT / "public" / "js" / "precious-inventory.js",
    ROOT / "public" / "js" / "jin10-holdings-app.js",
    ROOT / "src" / "pages" / "rolling" / "low-chip.astro",
]


def test_cacheable_clients_do_not_fragment_edge_cache_with_wall_clock_queries():
    for path in CACHEABLE_CLIENTS:
        text = path.read_text(encoding="utf-8")
        fetch_calls = re.findall(r"fetch\([^;]+", text)
        for call in fetch_calls:
            assert "Date.now()" not in call, f"{path.relative_to(ROOT)} wall-clock fetch: {call[:120]}"
            assert "cache: 'no-store'" not in call, f"{path.relative_to(ROOT)} no-store fetch: {call[:120]}"
            assert 'cache: "no-store"' not in call, f"{path.relative_to(ROOT)} no-store fetch: {call[:120]}"


def test_market_clock_keeps_explicit_no_store_for_network_time_calibration():
    text = (ROOT / "public" / "js" / "market-clock.js").read_text(encoding="utf-8")
    assert "method: 'HEAD'" in text
    assert "cache: 'no-store'" in text

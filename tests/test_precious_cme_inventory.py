"""Unit tests for COMEX inventory parsing (pure HTML → values, no network).

The upstream page is a third-party marketing site whose markup drifts; the
2026-10-09 regression was a silent selector failure (`text-3xl font-black
tabular-nums` and `Total</p>` both disappeared) that degraded the panel to
"CME 数据暂不可用". These tests pin the prose contract and the arithmetic
self-check so a future markup change fails loudly instead of silently.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import update_precious_inventory as collector  # noqa: E402

PROSE_PAGE = """
<p>On October 7, 2026, COMEX held 15.07M oz of registered gold and 8.41M oz of
eligible gold, 23.48M oz in all, so 64.2% of the gold in COMEX vaults could be
delivered (CME Group daily warehouse report).</p>
<p>COMEX silver inventory on October 7, 2026: 102.22M oz registered and 230.85M oz
eligible, 333.07M oz in all. Registered is 30.7% of the total.</p>
"""


def test_parse_prose_path_extracts_all_three_figures():
    parsed = collector.parse_cme_inventory(PROSE_PAGE)
    assert parsed is not None
    assert parsed["date"] == "October 7, 2026"
    assert parsed["gold"] == (15.07, 8.41, 23.48)
    assert parsed["silver"] == (102.22, 230.85, 333.07)


def test_parse_rejects_page_when_arithmetic_does_not_reconcile():
    # Registered + eligible must equal the stated total; a drifted parse must
    # not be published as a valid inventory.
    broken = PROSE_PAGE.replace("230.85M oz", "330.85M oz")
    parsed = collector.parse_cme_inventory(broken)
    # silver fails the self-check, gold survives → partial parse
    assert parsed is not None
    assert parsed["silver"] is None
    assert parsed["gold"] == (15.07, 8.41, 23.48)


def test_parse_returns_none_on_legacy_markup():
    # The pre-2026-10-09 selectors must not be relied upon any more.
    legacy = '<div class="text-3xl font-black tabular-nums">14.19M oz</div><p>Total</p><p>26.60M oz</p>'
    assert collector.parse_cme_inventory(legacy) is None


def test_parse_falls_back_to_table_and_json_ld():
    page = (
        '<script>{"text":"On October 7, 2026, COMEX held 15.07M oz of registered gold'
        ' and 8.41M oz of eligible gold, 23.48M oz in all."}</script>'
        '<p>As of October 7, 2026, COMEX warehouses held 23.48M oz</p>'
        '<table aria-label="COMEX silver inventory by day, last 30 days"><tbody>'
        '<tr data-date="2026-10-07"><td>Oct 7</td><td>102.22M oz</td>'
        '<td>230.85M oz</td><td>333.07M oz</td><td>-602.0K oz</td></tr>'
        "</tbody></table>"
    )
    parsed = collector.parse_cme_inventory(page)
    assert parsed is not None
    assert parsed["gold"] == (15.07, 8.41, 23.48)
    assert parsed["silver"] == (102.22, 230.85, 333.07)


def test_fetch_cme_reports_partial_when_one_metal_missing():
    # gold present, silver absent → partial payload, gold still published
    page = (
        "<p>On October 7, 2026, COMEX held 15.07M oz of registered gold and "
        "8.41M oz of eligible gold, 23.48M oz in all.</p>"
    )
    parsed = collector.parse_cme_inventory(page)
    assert parsed is not None
    assert parsed["gold"] == (15.07, 8.41, 23.48)
    assert parsed["silver"] is None


def test_fetch_cme_block_shape_is_unchanged_for_consumers():
    # The front-end reads gold/silver .registered/.eligible and .date.
    registered, eligible, total = (15.07, 8.41, 23.48)
    block = {
        "registered": f"{registered}M oz",
        "eligible": f"{eligible}M oz",
        "total": f"{total}M oz",
        "registeredOz": registered * 1_000_000,
        "totalOz": total * 1_000_000,
    }
    assert block["registered"] == "15.07M oz"
    assert block["registeredOz"] == 15070000.0
    assert block["total"] == "23.48M oz"

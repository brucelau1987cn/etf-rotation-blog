from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_paper_trading_surface_is_removed():
    removed = [
        "src/pages/paper.astro",
        "src/lib/us-paper-trade-links.mjs",
        "public/data/paper-trading.json",
        "scripts/paper_trade_runner.py",
        "scripts/publish_paper_trading.py",
        "scripts/backfill_paper_nav.py",
    ]
    assert all(not ROOT.joinpath(path).exists() for path in removed)


def test_header_and_primary_pages_do_not_link_to_paper():
    files = [
        "src/components/Header.astro",
        "src/pages/us-compass.astro",
        "src/pages/us-compass/history.astro",
        "README.md",
    ]
    combined = "\n".join(ROOT.joinpath(path).read_text(encoding="utf-8") for path in files)
    assert 'href="/paper/"' not in combined
    assert "模拟盘" not in ROOT.joinpath("src/components/Header.astro").read_text(encoding="utf-8")


def test_publishers_and_catalog_no_longer_depend_on_paper_trading():
    files = [
        "scripts/a_share_nightly_contract.py",
        "scripts/publish_a_share_nightly.py",
        "scripts/publish_a_share_stage.py",
        "scripts/update_us_etf_garden.py",
        "scripts/validate_dashboard_batches.py",
        "scripts/generate_data_catalog.py",
        "scripts/shadow_dirty_files.py",
    ]
    combined = "\n".join(ROOT.joinpath(path).read_text(encoding="utf-8") for path in files)
    assert "paper-trading.json" not in combined
    assert "paper_trade_runner" not in combined

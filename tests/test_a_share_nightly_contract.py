import os

from scripts import a_share_nightly_contract as contract


def test_nightly_contract_has_no_paper_trading_lock():
    assert not hasattr(contract, "paper_publish_lock")
    assert not hasattr(contract, "PAPER_LOCK")


def test_site_publish_lock_sets_generic_marker(tmp_path, monkeypatch):
    monkeypatch.setattr(contract, "SITE_PUBLISH_LOCK", tmp_path / "site.lock")
    monkeypatch.delenv("SITE_PUBLISH_LOCK_HELD", raising=False)
    with contract.site_publish_lock():
        assert os.environ["SITE_PUBLISH_LOCK_HELD"] == "1"
    assert "SITE_PUBLISH_LOCK_HELD" not in os.environ

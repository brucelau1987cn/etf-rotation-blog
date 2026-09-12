from scripts import a_share_nightly_contract as contract


def test_nightly_contract_has_no_paper_trading_lock():
    assert not hasattr(contract, "paper_publish_lock")
    assert not hasattr(contract, "PAPER_LOCK")

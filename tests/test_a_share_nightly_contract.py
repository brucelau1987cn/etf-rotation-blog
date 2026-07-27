import os

from scripts import a_share_nightly_contract as contract


def test_paper_publish_lock_sets_child_process_marker(tmp_path, monkeypatch):
    monkeypatch.setattr(contract, "PAPER_LOCK", tmp_path / "paper.lock")
    monkeypatch.delenv("PAPER_PUBLISH_LOCK_HELD", raising=False)
    with contract.paper_publish_lock():
        assert os.environ["PAPER_PUBLISH_LOCK_HELD"] == "1"
    assert "PAPER_PUBLISH_LOCK_HELD" not in os.environ

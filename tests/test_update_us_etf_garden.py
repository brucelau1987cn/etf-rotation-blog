from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts/update_us_etf_garden.py"
spec = importlib.util.spec_from_file_location("update_us_etf_garden", MODULE_PATH)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def decide(**overrides):
    values = {
        "old": "2026-07-29",
        "latest": "2026-07-29",
        "state": "closed",
        "recovery_dirty": False,
        "garden_date": "2026-07-29",
        "garden_stage": "美股收盘版",
        "garden_session": "closed",
    }
    values.update(overrides)
    return module.decide_action(**values)


def test_completed_dirty_close_recovers_before_new_intraday_bar():
    assert decide(
        latest="2026-07-30",
        state="open",
        recovery_dirty=True,
    ) == "recover"


def test_clean_same_date_is_idempotent():
    assert decide() == "noop"


def test_new_intraday_bar_waits_for_close():
    assert decide(latest="2026-07-30", state="open") == "wait"


def test_new_completed_bar_publishes():
    assert decide(latest="2026-07-30", state="closed") == "publish"


def test_dirty_non_close_snapshot_is_not_recovered():
    assert decide(
        latest="2026-07-30",
        state="open",
        recovery_dirty=True,
        garden_stage="美股盘中快照",
        garden_session="open",
    ) == "wait"


def test_owned_commit_subject_accepts_normal_and_recovery_commits():
    assert module.is_owned_commit_subject("data: update US ETF Compass for 2026-07-29")
    assert module.is_owned_commit_subject("data: recover US ETF Compass close for 2026-07-29")
    assert not module.is_owned_commit_subject("feat: unrelated change")


def test_recovery_scope_excludes_catalog_and_includes_synced_paper_projection():
    assert "public/data/catalog.json" not in module.FILES
    assert "public/data/catalog.json" not in module.US_OWNED_FILES
    assert "public/data/us-etf-garden.json" in module.US_OWNED_FILES
    assert "public/data/paper-trading.json" not in module.FILES
    assert "public/data/paper-trading.json" not in module.US_OWNED_FILES


def test_close_publisher_regenerates_health_after_learning():
    source = MODULE_PATH.read_text(encoding="utf-8")
    learning = source.index('run("python3", "scripts/update_us_compass_learning.py")')
    health = source.index('run("python3", "scripts/generate_us_compass_health.py")')
    assert health > learning
    assert "public/data/us-compass-health.json" in module.FILES
    assert "public/data/us-compass-health.json" in module.US_OWNED_FILES


def test_us_release_build_is_independent_from_a_share_batch_gate(tmp_path, monkeypatch):
    calls = []
    env = {"PATH": "/usr/bin"}
    tree = tmp_path / "target"
    for route in module.CLOSE_ROUTES:
        page = tree / "dist" / route / "index.html"
        page.parent.mkdir(parents=True, exist_ok=True)
        page.touch()
    monkeypatch.setattr(module, "validate_us_release_data", lambda *a, **kw: None)
    monkeypatch.setattr(module, "prepare_build_runtime", lambda root: env)

    def run(*args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(module, "run", run)
    module.build_us_release(tree, "2026-09-11")
    assert calls == [
        ((module.BUILD_PYTHON, "scripts/validate_public_data_contracts.py"),
         {"cwd": tree, "env": env, "check": False}),
        ((module.PROJECT_PYTHON, "scripts/build_site.py", "--no-contract-chain", "--clean-dist"),
         {"cwd": tree, "env": env}),
    ]


def test_us_release_validator_rejects_nonfinite_owned_json(tmp_path, monkeypatch):
    data = tmp_path / "public/data"
    data.mkdir(parents=True)
    payloads = {
        "us-etf-pool.json": '{"model_date":"2026-09-10","session_state":"closed","bad":NaN}',
        "us-etf-garden.json": '{"date":"2026-09-10","stage":"美股收盘版","session_state":"closed"}',
        "us-compass-health.json": '{"model_date":"2026-09-10"}',
        "us-macro-dashboard.json": '{}',
    }
    for name, text in payloads.items():
        (data / name).write_text(text, encoding="utf-8")
    monkeypatch.setattr(module, "REPO", tmp_path)

    try:
        module.validate_us_release_data()
    except RuntimeError as error:
        assert "non-finite" in str(error)
    else:
        raise AssertionError("non-finite US release JSON must fail closed")


def test_us_release_validator_rejects_cross_date_payload(tmp_path, monkeypatch):
    data = tmp_path / "public/data"
    data.mkdir(parents=True)
    payloads = {
        "us-etf-pool.json": {"model_date": "2026-09-10", "session_state": "closed"},
        "us-etf-garden.json": {"date": "2026-09-10", "stage": "美股收盘版", "session_state": "closed"},
        "us-compass-health.json": {"model_date": "2026-09-09"},
        "us-macro-dashboard.json": {},
    }
    for name, payload in payloads.items():
        (data / name).write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(module, "REPO", tmp_path)

    try:
        module.validate_us_release_data()
    except RuntimeError as error:
        assert "health model_date" in str(error)
    else:
        raise AssertionError("cross-date US release must fail closed")


def test_us_release_validator_uses_latest_macro_market_observation(tmp_path, monkeypatch):
    data = tmp_path / "public/data"
    data.mkdir(parents=True)
    payloads = {
        "us-etf-pool.json": {"model_date": "2026-09-11", "session_state": "closed"},
        "us-etf-garden.json": {"date": "2026-09-11", "stage": "美股收盘版", "session_state": "closed"},
        "us-compass-health.json": {"model_date": "2026-09-11"},
        "us-macro-dashboard.json": {
            "generated_at": "2026-09-11T18:30:00-04:00",
            "market": {
                "spy": {"date": "2026-09-11"},
                "yield_2y_proxy": {"date": "2026-09-10"},
            },
        },
    }
    for name, payload in payloads.items():
        (data / name).write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(module, "REPO", tmp_path)

    module.validate_us_release_data()


def test_write_state_is_atomic_and_preserves_fields(tmp_path, monkeypatch):
    state = tmp_path / "publisher.json"
    monkeypatch.setattr(module, "STATE", state)
    module.write_state("evaluated", trade_date="2026-07-29", action="wait")
    module.write_state("published", commit="abc123", verified=True)
    payload = json.loads(state.read_text(encoding="utf-8"))
    assert payload["phase"] == "published"
    assert payload["trade_date"] == "2026-07-29"
    assert payload["action"] == "wait"
    assert payload["commit"] == "abc123"
    assert payload["verified"] is True
    assert not state.with_suffix(".tmp").exists()


def test_us_public_contracts_allows_catalog_and_foreign_drift(monkeypatch):
    errors = [
        "catalog etf-garden-pool bytes mismatch",
        "catalog etf-garden-pool sha256 mismatch",
        "catalog etf-garden-pool metadata differs from source dataset",
        "catalog us-etf-garden bytes mismatch",
        "catalog us-etf-pool metadata differs from source dataset",
        "A-share candidate set unchanged from previous batch",
        "a-compass-dashboard differs from etf-garden-pool export",
    ]
    payload = json.dumps({"status": "error", "errors": errors})
    monkeypatch.setattr(module, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args, 2, stdout=payload, stderr=""))
    # Should not raise
    module.validate_us_public_contracts()

    # But unexpected errors still raise
    bad_errors = errors + ["unexpected foreign table error"]
    bad_payload = json.dumps({"status": "error", "errors": bad_errors})
    monkeypatch.setattr(module, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args, 2, stdout=bad_payload, stderr=""))
    try:
        module.validate_us_public_contracts()
    except RuntimeError as exc:
        assert "unexpected foreign table error" in str(exc)
    else:
        raise AssertionError("should have raised on unexpected error")


def test_success_state_clears_stale_error(tmp_path, monkeypatch):
    state = tmp_path / "publisher.json"
    state.write_text(json.dumps({"phase": "failed", "error": "old failure"}), encoding="utf-8")
    monkeypatch.setattr(module, "STATE", state)

    module.write_state("idempotent", trade_date="2026-09-11", verified=True)

    payload = json.loads(state.read_text(encoding="utf-8"))
    assert payload["phase"] == "idempotent"
    assert "error" not in payload


def _completed(returncode: int, args) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args, returncode, stdout="", stderr="")


def test_receipt_commit_accepts_unrelated_descendant(monkeypatch):
    calls = []

    def fake_run(*args, **kwargs):
        calls.append(args)
        return _completed(0, args)

    monkeypatch.setattr(module, "run", fake_run)
    assert module.receipt_commit_covers("a" * 40, "b" * 40) is True
    assert calls and calls[0][:3] == ("git", "merge-base", "--is-ancestor")


def test_receipt_commit_rejects_non_ancestor_and_malformed(monkeypatch):
    monkeypatch.setattr(module, "run", lambda *args, **kwargs: _completed(1, args))
    assert module.receipt_commit_covers("a" * 40, "b" * 40) is False
    assert module.receipt_commit_covers("abc123", "b" * 40) is False
    assert module.receipt_commit_covers(None, "b" * 40) is False


def test_receipt_commit_exact_head_skips_git(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("git should not run when receipt commit equals HEAD")

    monkeypatch.setattr(module, "run", boom)
    assert module.receipt_commit_covers("a" * 40, "a" * 40) is True


def test_verified_same_date_accepts_ancestor_receipt(monkeypatch):
    assets = {"public/data/us-etf-pool.json": "h1"}
    monkeypatch.setattr(module, "close_blob_hashes", lambda blobs: assets)
    monkeypatch.setattr(module, "close_blobs", lambda root, commit: {"public/data/us-etf-pool.json": b"x"})
    monkeypatch.setattr(module, "run", lambda *args, **kwargs: _completed(0, args))
    prior = {
        "receipt_version": module.RECEIPT_VERSION,
        "phase": "published",
        "trade_date": "2026-11-25",
        "target_day": "2026-11-25",
        "commit": "a" * 40,
        "pushed": True,
        "deployed": True,
        "verified": True,
        "deployment_url": f"https://abc123.{module.pages_release.PROJECT}.pages.dev",
        "verified_routes": list(module.CLOSE_ROUTES),
        "verified_assets": assets,
    }
    assert module.verified_same_date(prior, "b" * 40, "2026-11-25") is True


def test_verified_same_date_rejects_asset_drift(monkeypatch):
    monkeypatch.setattr(module, "close_blob_hashes", lambda blobs: {"public/data/us-etf-pool.json": "drifted"})
    monkeypatch.setattr(module, "close_blobs", lambda root, commit: {"public/data/us-etf-pool.json": b"x"})
    monkeypatch.setattr(module, "run", lambda *args, **kwargs: _completed(0, args))
    prior = {
        "receipt_version": module.RECEIPT_VERSION,
        "phase": "published",
        "trade_date": "2026-11-25",
        "target_day": "2026-11-25",
        "commit": "a" * 40,
        "pushed": True,
        "deployed": True,
        "verified": True,
        "deployment_url": f"https://abc123.{module.pages_release.PROJECT}.pages.dev",
        "verified_routes": list(module.CLOSE_ROUTES),
        "verified_assets": {"public/data/us-etf-pool.json": "h1"},
    }
    assert module.verified_same_date(prior, "b" * 40, "2026-11-25") is False

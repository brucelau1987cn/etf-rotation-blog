import hashlib
import json
import subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import scripts.prepare_a_share_nightly as prepare
import scripts.publish_a_share_nightly as publish

CN = ZoneInfo("Asia/Shanghai")


def kronos_fixture(trade_date="2026-07-14", count=89):
    future = ["2026-07-15", "2026-07-16", "2026-07-17", "2026-07-20", "2026-07-21"]
    return {
        "mode": "shadow_research_only", "production_weights_changed": False,
        "formal_signal_logic_changed": False, "production_role": "display_and_audit_only",
        "latest_trade_date": trade_date,
        "data_basis": {"adjustment": "qfq", "is_final": True, "universe": "formal_rotation"},
        "forecast_definition": {"horizon_sessions": 5, "future_sessions": future},
        "coverage": {"expected_symbols": count, "predicted_symbols": count},
        "items": [{
            "symbol": f"{510000 + index}", "as_of": trade_date,
            "steps": [{"open": 4.0, "high": 4.1, "low": 3.9, "close": 4.02} for _ in future],
        } for index in range(count)],
    }


def research_audit_fixture(trade_date="2026-07-14", pool_rows=0):
    return {
        "schema_version": "research_audit_v1", "mode": "shadow_research_only",
        "production_rules_changed": False,
        "dataset": {"as_of": trade_date, "pool_row_count": pool_rows},
    }


def test_prepare_writes_manifest_for_valid_gate(tmp_path, monkeypatch):
    monkeypatch.setattr(prepare, "ROOT", tmp_path)
    monkeypatch.setattr(prepare, "generate_research_audit", lambda *_: (research_audit_fixture(), None))
    monkeypatch.setattr(prepare, "validate_research_audit", lambda errors, audit, backtest, pool: audit.get("dataset", {}))
    monkeypatch.setattr(prepare, "git_head", lambda: "a" * 40)
    (tmp_path / "public/data/model-lab").mkdir(parents=True)
    (tmp_path / "public/data/etf-garden-pool.json").write_text(json.dumps({
        "latest_trade_date": "2026-07-14", "summary": {"valid_count": 91}
    }))
    (tmp_path / "public/data/etf-garden-backtest.json").write_text(json.dumps({"records": []}))
    (tmp_path / "public/data/model-lab/a-share-shadow.json").write_text(json.dumps({
        "mode": "shadow_research_only", "production_weights_changed": False, "rotation_universe_count": 89,
        "signal_enhancement": {"formal_signal_logic_changed": False, "production_role": "shadow_filter_and_audit_only", "coverage": {"symbols_at_least_260": 89}}
    }))
    (tmp_path / "public/data/model-lab/a-share-path-shadow.json").write_text(json.dumps(kronos_fixture()))
    monkeypatch.setattr(prepare, "run_json", lambda command: {
        "decision": "run", "qfq_date": "2026-07-14", "qfq_coverage": 91
    })
    state = tmp_path / "state.json"
    result = prepare.prepare(now=datetime(2026, 7, 14, 21, 50, tzinfo=CN), state_path=state)
    assert result["status"] == "prepared"
    assert result["trade_date"] == "2026-07-14"
    assert json.loads(state.read_text())["expected_stage"] == "22:00夜间最终版"
    assert "public/data/model-lab/a-share-path-shadow.json" in result["snapshot_files"]
    assert "public/data/model-lab/a-share-research-audit.json" in result["snapshot_files"]
    assert "public/data/etf-garden-backtest.json" in result["snapshot_files"]
    assert set(result["snapshot_hashes"]) == set(result["snapshot_files"])
    assert result["base_commit"] == "a" * 40
    assert "public/data/etf-garden-backtest.json" in publish.ALLOWED_STATIC


def test_prepare_preflight_rejects_remote_drift(monkeypatch):
    def fake_run(command, **kwargs):
        args = command[1:]
        if args == ["branch", "--show-current"]:
            return subprocess.CompletedProcess(command, 0, stdout="main\n", stderr="")
        if args == ["diff", "--cached", "--quiet"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if args == ["fetch", "origin", "main"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if args == ["rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(command, 0, stdout="a" * 40 + "\n", stderr="")
        if args == ["rev-parse", "origin/main"]:
            return subprocess.CompletedProcess(command, 0, stdout="b" * 40 + "\n", stderr="")
        raise AssertionError(command)

    monkeypatch.setattr(prepare.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="HEAD == origin/main"):
        prepare.ensure_current_main()


def test_prepare_blocks_when_research_audit_generation_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(prepare, "ROOT", tmp_path)
    monkeypatch.setattr(prepare, "generate_research_audit", lambda *_: ({}, "generator boom"))
    (tmp_path / "public/data/model-lab").mkdir(parents=True)
    (tmp_path / "public/data/etf-garden-pool.json").write_text(json.dumps({
        "latest_trade_date": "2026-07-14", "summary": {"valid_count": 91}
    }))
    (tmp_path / "public/data/etf-garden-backtest.json").write_text(json.dumps({"records": []}))
    (tmp_path / "public/data/model-lab/a-share-shadow.json").write_text(json.dumps({
        "mode": "shadow_research_only", "production_weights_changed": False, "rotation_universe_count": 89,
        "signal_enhancement": {"formal_signal_logic_changed": False, "production_role": "shadow_filter_and_audit_only", "coverage": {"symbols_at_least_260": 89}}
    }))
    (tmp_path / "public/data/model-lab/a-share-path-shadow.json").write_text(json.dumps(kronos_fixture()))
    monkeypatch.setattr(prepare, "run_json", lambda command: {
        "decision": "run", "qfq_date": "2026-07-14", "qfq_coverage": 91
    })
    result = prepare.prepare(
        now=datetime(2026, 7, 14, 21, 50, tzinfo=CN), state_path=tmp_path / "state.json",
    )
    assert result["status"] == "blocked"
    assert any("research audit generation failed" in error for error in result["errors"])


def test_prepare_blocks_invalid_shadow_and_overwrites_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(prepare, "ROOT", tmp_path)
    monkeypatch.setattr(prepare, "generate_research_audit", lambda *_: (research_audit_fixture(), None))
    (tmp_path / "public/data/model-lab").mkdir(parents=True)
    (tmp_path / "public/data/etf-garden-pool.json").write_text(json.dumps({
        "latest_trade_date": "2026-07-14", "summary": {"valid_count": 91}
    }))
    (tmp_path / "public/data/etf-garden-backtest.json").write_text(json.dumps({"records": []}))
    (tmp_path / "public/data/model-lab/a-share-shadow.json").write_text(json.dumps({
        "mode": "production", "production_weights_changed": True, "rotation_universe_count": 10
    }))
    (tmp_path / "public/data/model-lab/a-share-path-shadow.json").write_text(json.dumps(kronos_fixture()))
    audit_path = tmp_path / "public/data/model-lab/a-share-research-audit.json"
    audit_path.write_text('{"sentinel": true}')
    monkeypatch.setattr(prepare, "run_json", lambda command: {"decision": "run", "qfq_date": "2026-07-14"})
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"status": "prepared", "trade_date": "2026-07-13"}))
    result = prepare.prepare(now=datetime(2026, 7, 14, 21, 50, tzinfo=CN), state_path=state)
    assert result["status"] == "blocked"
    assert len(result["errors"]) == 4
    assert json.loads(state.read_text())["status"] == "blocked"
    assert json.loads(audit_path.read_text()) == {"sentinel": True}


def test_prepare_blocks_invalid_kronos_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(prepare, "ROOT", tmp_path)
    monkeypatch.setattr(prepare, "generate_research_audit", lambda *_: (research_audit_fixture(), None))
    (tmp_path / "public/data/model-lab").mkdir(parents=True)
    (tmp_path / "public/data/etf-garden-pool.json").write_text(json.dumps({
        "latest_trade_date": "2026-07-14", "summary": {"valid_count": 91}
    }))
    (tmp_path / "public/data/etf-garden-backtest.json").write_text(json.dumps({"records": []}))
    (tmp_path / "public/data/model-lab/a-share-shadow.json").write_text(json.dumps({
        "mode": "shadow_research_only", "production_weights_changed": False, "rotation_universe_count": 89,
        "signal_enhancement": {"formal_signal_logic_changed": False, "production_role": "shadow_filter_and_audit_only", "coverage": {"symbols_at_least_260": 89}}
    }))
    invalid = kronos_fixture(count=88)
    (tmp_path / "public/data/model-lab/a-share-path-shadow.json").write_text(json.dumps(invalid))
    monkeypatch.setattr(prepare, "run_json", lambda command: {"decision": "run", "qfq_date": "2026-07-14"})
    result = prepare.prepare(now=datetime(2026, 7, 14, 21, 50, tzinfo=CN), state_path=tmp_path / "state.json")
    assert result["status"] == "blocked"
    assert any("89/89" in error for error in result["errors"])


def prepared_state():
    trade_date = "2026-07-14"
    return {
        "version": 2,
        "status": "prepared", "phase": "prepared",
        "generation_id": "a-share-2026-07-14-test",
        "trade_date": trade_date, "prepared_at": "2026-07-14T21:50:00+08:00",
        "base_commit": "a" * 40,
        "expected_stage": "22:00夜间最终版",
        "content_files": list(publish.nightly_content_files(trade_date)),
        "snapshot_files": list(publish.SNAPSHOT_FILES),
        "snapshot_hashes": {path: "0" * 64 for path in publish.SNAPSHOT_FILES},
    }


def test_publish_rejects_wrong_stage(tmp_path, monkeypatch):
    monkeypatch.setattr(publish, "ROOT", tmp_path)
    monkeypatch.setattr(publish, "git_head", lambda: "a" * 40)
    monkeypatch.setattr(publish, "verify_snapshot_hashes", lambda state: None)
    (tmp_path / "src/content/blog").mkdir(parents=True)
    (tmp_path / "public/data").mkdir(parents=True)
    state = tmp_path / "state.json"
    state.write_text(json.dumps(prepared_state()))
    (tmp_path / "src/content/blog/2026-07-14.md").write_text("---\nstage: 14:30尾盘操作版\n---\n")
    (tmp_path / "public/data/garden-recommendations.json").write_text(json.dumps({"date": "2026-07-14", "stage": "14:30尾盘操作版"}))
    (tmp_path / "public/data/a-share-mid-macro.json").write_text("{}")
    try:
        publish.publish(state, dry_run=True, now=datetime(2026, 7, 14, 22, 30, tzinfo=CN))
    except RuntimeError as exc:
        assert "stage/date mismatch" in str(exc)
    else:
        raise AssertionError("expected stage mismatch")


def test_publish_idempotent_when_no_owned_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(publish, "ROOT", tmp_path)
    monkeypatch.setattr(publish, "git_head", lambda: "a" * 40)
    monkeypatch.setattr(publish, "verify_snapshot_hashes", lambda state: None)
    (tmp_path / "src/content/blog").mkdir(parents=True)
    (tmp_path / "public/data").mkdir(parents=True)
    state = tmp_path / "state.json"
    state.write_text(json.dumps(prepared_state()))
    (tmp_path / "src/content/blog/2026-07-14.md").write_text("---\nstage: '22:00夜间最终版'\n---\n### 22:00 夜间最终整理\n")
    (tmp_path / "public/data/garden-recommendations.json").write_text(json.dumps({"date": "2026-07-14", "stage": "22:00夜间最终版"}))
    (tmp_path / "public/data/a-share-mid-macro.json").write_text("{}")
    monkeypatch.setattr(publish, "git_changes", lambda: {"unrelated.txt"})
    result = publish.publish(state, dry_run=True, now=datetime(2026, 7, 14, 22, 30, tzinfo=CN))
    assert result["status"] == "idempotent"
    assert result["changed"] == []


def test_publish_rejects_stale_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(publish, "ROOT", tmp_path)
    monkeypatch.setattr(publish, "git_head", lambda: "a" * 40)
    monkeypatch.setattr(publish, "verify_snapshot_hashes", lambda state: None)
    state = tmp_path / "state.json"
    payload = prepared_state()
    payload.update({
        "trade_date": "2026-07-13",
        "prepared_at": "2026-07-13T21:50:00+08:00",
        "content_files": list(publish.nightly_content_files("2026-07-13")),
    })
    state.write_text(json.dumps(payload))
    try:
        publish.publish(state, dry_run=True, now=datetime(2026, 7, 14, 22, 30, tzinfo=CN))
    except RuntimeError as exc:
        assert "stale nightly manifest" in str(exc)
    else:
        raise AssertionError("expected stale manifest rejection")


def test_snapshot_hash_change_is_blocked(tmp_path, monkeypatch):
    monkeypatch.setattr(publish, "ROOT", tmp_path)
    for relative in publish.SNAPSHOT_FILES:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("original")
    state = {"snapshot_hashes": publish.file_hashes(tmp_path, publish.SNAPSHOT_FILES)}
    (tmp_path / publish.SNAPSHOT_FILES[0]).write_text("tampered")
    with pytest.raises(RuntimeError, match="snapshot changed"):
        publish.verify_snapshot_hashes(state)


def test_snapshot_hash_contract_is_fail_closed():
    with pytest.raises(RuntimeError, match="missing or incomplete"):
        publish.verify_snapshot_hashes({"snapshot_files": ["definitely-missing.json"]})


def test_committed_retry_marks_manifest_published(tmp_path, monkeypatch):
    target = "b" * 40
    state_path = tmp_path / "state.json"
    payload = prepared_state()
    payload.update({
        "status": "committed", "phase": "committed", "commit": target,
        "candidate_tree": "d" * 40,
        "dataset_fingerprint": "c" * 64,
        "public_hashes": {path: "e" * 64 for path in publish.PUBLIC_VERIFY_FILES},
        "changed": ["public/data/garden-recommendations.json"],
    })
    state_path.write_text(json.dumps(payload))
    monkeypatch.setattr(publish, "ROOT", tmp_path)
    monkeypatch.setattr(publish, "sync_remote", lambda expected_local_commit=None: None)
    monkeypatch.setattr(publish, "git_head", lambda: target)
    monkeypatch.setattr(publish, "verify_candidate_identity", lambda state: None)

    def fake_run(command, check=True, **kwargs):
        stdout = target + "\n" if command[:3] == ["git", "rev-parse", "origin/main"] else ""
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    verified = []
    monkeypatch.setattr(publish, "run", fake_run)
    monkeypatch.setattr(
        publish, "verify_production",
        lambda day, fingerprint, *hashes: verified.append((day, fingerprint)),
    )
    result = publish.publish(
        state_path, dry_run=False, now=datetime(2026, 7, 14, 22, 30, tzinfo=CN),
    )
    saved = json.loads(state_path.read_text())
    assert result["status"] == "published"
    assert saved["status"] == "published"
    assert saved["phase"] == "published"
    assert verified == [("2026-07-14", "c" * 64)]


def test_candidate_recovery_resets_index_when_ref_is_already_attached(tmp_path, monkeypatch):
    target = "b" * 40
    payload = prepared_state()
    payload.update({
        "status": "candidate_validated", "phase": "candidate_validated",
        "commit": target, "candidate_tree": "d" * 40,
        "dataset_fingerprint": "c" * 64,
        "public_hashes": {path: "e" * 64 for path in publish.PUBLIC_VERIFY_FILES},
    })
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps(payload))
    calls = []

    def fake_run(command, check=True, **kwargs):
        calls.append(command)
        stdout = target + "\n" if command[:3] == ["git", "rev-parse", "origin/main"] else ""
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(publish, "ROOT", tmp_path)
    monkeypatch.setattr(publish, "sync_remote", lambda expected_local_commit=None: None)
    monkeypatch.setattr(publish, "verify_candidate_identity", lambda state: None)
    monkeypatch.setattr(publish, "git_head", lambda: target)
    monkeypatch.setattr(publish, "run", fake_run)
    monkeypatch.setattr(publish, "verify_production", lambda *args: None)
    result = publish.publish(
        state_path, now=datetime(2026, 7, 14, 22, 30, tzinfo=CN),
    )
    assert result["status"] == "published"
    assert ["git", "reset", "--mixed", target] in calls


def test_sync_remote_rejects_undeclared_local_commit(monkeypatch):
    local = "d" * 40

    def fake_run(command, check=True, **kwargs):
        if command == ["git", "branch", "--show-current"]:
            stdout = "main\n"
        elif command[:3] == ["git", "rev-list", "--count"]:
            stdout = "1\n"
        else:
            stdout = ""
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(publish, "run", fake_run)
    monkeypatch.setattr(publish, "is_ancestor", lambda left, right: True)
    monkeypatch.setattr(publish, "git_head", lambda: local)
    with pytest.raises(RuntimeError, match="undeclared unpushed commit"):
        publish.sync_remote(expected_local_commit=None)
    publish.sync_remote(expected_local_commit=local)


@pytest.mark.parametrize("status", ["candidate_validated", "committed", "deploy_failed"])
def test_recovery_dry_run_has_no_side_effects(tmp_path, monkeypatch, status):
    target = "b" * 40
    payload = prepared_state()
    payload.update({
        "status": status, "phase": status, "commit": target,
        "candidate_tree": "d" * 40,
        "dataset_fingerprint": "c" * 64,
        "public_hashes": {path: "e" * 64 for path in publish.PUBLIC_VERIFY_FILES},
    })
    state_path = tmp_path / "state.json"
    original = json.dumps(payload)
    state_path.write_text(original)
    monkeypatch.setattr(publish, "verify_candidate_identity", lambda state: None)
    monkeypatch.setattr(publish, "git_head", lambda: payload["base_commit"] if status == "candidate_validated" else target)

    def fake_run(command, check=True, **kwargs):
        stdout = payload["base_commit"] + "\n" if command[:3] == ["git", "rev-parse", "origin/main"] else ""
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(publish, "run", fake_run)
    monkeypatch.setattr(publish, "sync_remote", lambda **kwargs: (_ for _ in ()).throw(AssertionError("sync in dry-run")))
    monkeypatch.setattr(publish, "atomic_write_state", lambda *args: (_ for _ in ()).throw(AssertionError("state write in dry-run")))
    monkeypatch.setattr(publish, "verify_production", lambda *args: (_ for _ in ()).throw(AssertionError("deployment probe in dry-run")))
    result = publish.publish(
        state_path, dry_run=True, now=datetime(2026, 7, 14, 22, 30, tzinfo=CN),
    )
    assert result["status"] == "validated_recovery"
    assert state_path.read_text() == original


def test_manifest_cannot_expand_owned_paths(tmp_path):
    payload = prepared_state()
    payload["content_files"].append("foreign.txt")
    state = tmp_path / "state.json"
    state.write_text(json.dumps(payload))
    with pytest.raises(RuntimeError, match="fixed nightly contract"):
        publish.load_state(state)


def test_production_verification_binds_full_public_hash_set(monkeypatch):
    bodies = {path: b"{}" for path in publish.PUBLIC_VERIFY_FILES}
    audit_path = "public/data/model-lab/a-share-research-audit.json"
    reco_path = "public/data/garden-recommendations.json"
    marker_path = "public/data/a-share-nightly-deployment.json"
    bodies[audit_path] = json.dumps({"dataset": {"value": "c" * 64}}).encode()
    bodies[reco_path] = json.dumps({"date": "2026-07-14", "stage": "22:00夜间最终版"}).encode()
    bodies[marker_path] = json.dumps({"generation_id": "generation-test", "trade_date": "2026-07-14"}).encode()

    class Headers:
        def __init__(self, content_type):
            self.content_type = content_type
            self.values = {
                "Content-Security-Policy": "default-src 'self'; object-src 'none'",
                "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY",
            }

        def get_content_type(self):
            return self.content_type

        def get(self, name, default=None):
            return self.values.get(name, default)

    class Response:
        def __init__(self, body, content_type):
            self.body = body
            self.headers = Headers(content_type)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return self.body

    def fake_urlopen(request, timeout=20):
        path = request.full_url.split("?", 1)[0].split("example.invalid/", 1)[1]
        if path == "lab/":
            return Response("研究审计台".encode(), "text/html")
        return Response(bodies["public/" + path], "application/json")

    monkeypatch.setenv("ETF_PUBLIC_BASE_URL", "https://example.invalid")
    monkeypatch.setattr(publish.urllib.request, "urlopen", fake_urlopen)
    expected = {path: hashlib.sha256(body).hexdigest() for path, body in bodies.items()}
    publish.verify_production("2026-07-14", "c" * 64, "generation-test", expected, attempts=1)
    bad = dict(expected)
    bad[audit_path] = "0" * 64
    with pytest.raises(RuntimeError, match="deployment verification failed"):
        publish.verify_production("2026-07-14", "c" * 64, "generation-test", bad, attempts=1)


def test_candidate_identity_rejects_tree_and_public_hash_tampering(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    for relative in publish.PUBLIC_VERIFY_FILES:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{relative}:base\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=tmp_path, check=True)
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True,
        capture_output=True, check=True,
    ).stdout.strip()
    changed = publish.PUBLIC_VERIFY_FILES[0]
    (tmp_path / changed).write_text("candidate\n")
    monkeypatch.setattr(publish, "ROOT", tmp_path)
    candidate, tree = publish.create_candidate_commit([changed], "candidate")
    state = {
        "commit": candidate,
        "base_commit": base,
        "candidate_tree": tree,
        "public_hashes": publish.candidate_file_hashes(candidate),
    }
    publish.verify_candidate_identity(state)
    with pytest.raises(RuntimeError, match="identity differs"):
        publish.verify_candidate_identity({**state, "candidate_tree": "f" * 40})
    bad_hashes = dict(state["public_hashes"])
    bad_hashes[changed] = "0" * 64
    with pytest.raises(RuntimeError, match="public file hashes differ"):
        publish.verify_candidate_identity({**state, "public_hashes": bad_hashes})


def test_candidate_commit_excludes_foreign_worktree_changes(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    owned = tmp_path / "owned.txt"
    foreign = tmp_path / "foreign.txt"
    owned.write_text("old-owned\n")
    foreign.write_text("old-foreign\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=tmp_path, check=True)
    owned.write_text("new-owned\n")
    foreign.write_text("new-foreign\n")
    monkeypatch.setattr(publish, "ROOT", tmp_path)
    monkeypatch.setattr(publish, "git_head", lambda: "a" * 40)
    monkeypatch.setattr(publish, "verify_snapshot_hashes", lambda state: None)
    candidate, _ = publish.create_candidate_commit(["owned.txt"], "candidate")
    candidate_owned = subprocess.run(
        ["git", "show", f"{candidate}:owned.txt"], cwd=tmp_path,
        check=True, text=True, capture_output=True,
    ).stdout
    candidate_foreign = subprocess.run(
        ["git", "show", f"{candidate}:foreign.txt"], cwd=tmp_path,
        check=True, text=True, capture_output=True,
    ).stdout
    assert candidate_owned == "new-owned\n"
    assert candidate_foreign == "old-foreign\n"

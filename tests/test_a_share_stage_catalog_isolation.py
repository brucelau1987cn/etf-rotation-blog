from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import generate_data_catalog as catalog
from scripts import generate_public_dashboard_payloads as dashboard
from scripts import publish_a_share_nightly as nightly
from scripts import publish_a_share_stage as stage
from scripts.a_share_nightly_contract import CATALOG_INPUT_FILES


@pytest.mark.parametrize("failure", [None, "catalog", "contract", "cleanup", "catalog-cleanup", "contract-cleanup"])
def test_catalog_uses_committed_foreign_inputs_with_owned_stage_bytes(tmp_path, monkeypatch, failure):
    source = Path(stage.__file__).resolve().parents[1]
    shutil.copytree(source / "public/data", tmp_path / "public/data")
    shutil.copytree(source / "public/schemas", tmp_path / "public/schemas")

    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=tmp_path, capture_output=True, check=True,
        ).stdout

    git("init", "-q")
    git("config", "user.email", "fixture@example.invalid")
    git("config", "user.name", "Offline fixture")
    git("add", "public")
    git("commit", "-qm", "catalog baseline")
    base = git("rev-parse", "HEAD").strip().decode()
    marker = "public/data/a-share-nightly-deployment.json"
    # Actual DATASETS includes inputs absent from the older nightly list.
    extra = "public/data/us-compass-health.json"
    assert extra not in CATALOG_INPUT_FILES
    assert any(spec.relative_path == "us-compass-health.json" for spec in catalog.DATASETS)
    foreign = [marker, extra, "public/data/research/investment-research-layer.json"]
    for relative in foreign:
        (tmp_path / relative).write_bytes(b'{"trade_date":"2026-11-20","draft":true}\n')
    foreign.append("public/data/foreign-untracked.json")
    (tmp_path / foreign[-1]).write_bytes(b"untracked draft\x00\xff")
    snapshots = {path: (tmp_path / path).read_bytes() for path in foreign}
    pool_path = tmp_path / stage.POOL_FILE
    pool = json.loads(pool_path.read_bytes())
    pool["stage_fixture"] = "owned draft"
    pool_path.write_text(json.dumps(pool, ensure_ascii=False) + "\n", encoding="utf-8")
    (tmp_path / "public/data/a-compass-dashboard.json").write_text(
        json.dumps(dashboard.build_payload(pool), ensure_ascii=False) + "\n", encoding="utf-8",
    )

    monkeypatch.setattr(stage, "ROOT", tmp_path)
    monkeypatch.setattr(nightly, "ROOT", tmp_path)
    calls = []
    original_catalog = (tmp_path / "public/data/catalog.json").read_bytes()
    worktrees = git("worktree", "list", "--porcelain")
    if failure and failure.startswith("catalog"):
        (tmp_path / stage.RECOMMENDATIONS_FILE).write_text('{"date":"invalid"}')
    if failure and failure.startswith("contract"):
        dashboard_path = tmp_path / "public/data/a-compass-dashboard.json"
        invalid_dashboard = json.loads(dashboard_path.read_bytes())
        invalid_dashboard["schema_version"] = "invalid"
        dashboard_path.write_text(json.dumps(invalid_dashboard))

    def offline_run(command, **kwargs):
        calls.append(command)
        if command[:3] == ["git", "worktree", "remove"] and failure and "cleanup" in failure:
            result = subprocess.CompletedProcess(command, 1, "", "injected cleanup failure")
            if kwargs.get("check", True):
                raise RuntimeError("command failed: injected cleanup failure")
            return result
        if command[0] == "git":
            return nightly.run(command, **kwargs)
        if command[1] in {
            "scripts/generate_data_catalog.py", "scripts/validate_public_data_contracts.py",
        }:
            return nightly.run(
                [str(Path(sys.executable).resolve()), str(source / command[1]), *command[2:]],
                **kwargs,
            )
        raise AssertionError(f"unexpected offline command: {command}")

    monkeypatch.setattr(stage, "run", offline_run)
    if failure and "cleanup" in failure:
        try:
            with pytest.raises(RuntimeError, match="cleanup") as caught:
                stage.refresh_catalog_contract()
            assert (tmp_path / "public/data/catalog.json").read_bytes() == original_catalog
            assert {path: (tmp_path / path).read_bytes() for path in foreign} == snapshots
            removal = next(command for command in calls if command[:3] == ["git", "worktree", "remove"])
            retained = Path(removal[-1])
            assert retained.is_dir()
            assert str(retained) in str(caught.value)
            assert str(retained).encode() in git("worktree", "list", "--porcelain")
            if failure != "cleanup":
                assert caught.value.__cause__ is not None
                assert "command failed" in str(caught.value.__cause__)
                script = "generate_data_catalog.py" if failure.startswith("catalog") else "validate_public_data_contracts.py"
                assert script in str(caught.value.__cause__)
            assert git("rev-parse", "HEAD").strip().decode() == base
            assert git("diff", "--cached", "--name-only") == b""
        finally:
            for command in calls:
                if command[:3] == ["git", "worktree", "remove"]:
                    nightly.run(command, cwd=tmp_path, check=False)
                    if Path(command[-1]).parent.exists():
                        Path(command[-1]).parent.rmdir()
        assert git("worktree", "list", "--porcelain") == worktrees
        return
    if failure:
        with pytest.raises(RuntimeError, match="command failed"):
            stage.refresh_catalog_contract()
        assert {path: (tmp_path / path).read_bytes() for path in foreign} == snapshots
        assert (tmp_path / "public/data/catalog.json").read_bytes() == original_catalog
        assert git("worktree", "list", "--porcelain") == worktrees
        assert git("rev-parse", "HEAD").strip().decode() == base
        assert git("diff", "--cached", "--name-only") == b""
        return
    stage.refresh_catalog_contract()
    assert git("worktree", "list", "--porcelain") == worktrees
    payload = json.loads((tmp_path / "public/data/catalog.json").read_bytes())
    by_id = {entry["dataset_id"]: entry for entry in payload["datasets"]}
    for relative in foreign[:-1]:
        spec = next(spec for spec in catalog.DATASETS if relative == f"public/data/{spec.relative_path}")
        assert by_id[spec.dataset_id]["sha256"] == hashlib.sha256(git("show", f"HEAD:{relative}")).hexdigest()
    assert by_id["etf-garden-pool"]["sha256"] == hashlib.sha256(pool_path.read_bytes()).hexdigest()
    assert any(command[1:2] == ["scripts/validate_public_data_contracts.py"] for command in calls)
    assert {path: (tmp_path / path).read_bytes() for path in foreign} == snapshots

    changed = nightly.git_changes()
    owned, ignored = stage.managed_paths("2026-11-20", changed)
    assert set(foreign) <= set(ignored)
    candidate, _ = nightly.create_candidate_commit(owned, "offline stage candidate")
    for relative in foreign[:-1]:
        assert git("show", f"{candidate}:{relative}") == git("show", f"HEAD:{relative}")
    assert git("show", f"{candidate}:{stage.POOL_FILE}") == pool_path.read_bytes()
    assert git("show", f"{candidate}:public/data/catalog.json") == (tmp_path / "public/data/catalog.json").read_bytes()
    assert set(git("diff-tree", "--no-commit-id", "--name-only", "-r", candidate).decode().splitlines()) == set(owned)
    assert git("rev-parse", "HEAD").strip().decode() == base
    assert git("diff", "--cached", "--name-only") == b""
    assert {path: (tmp_path / path).read_bytes() for path in foreign} == snapshots

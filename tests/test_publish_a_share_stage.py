from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts import publish_a_share_stage as stage


def write_stage_files(root: Path, trade_date: str = "2026-07-23", label: str = "08:30盘前版") -> None:
    data = root / "public/data"
    (data / "model-lab").mkdir(parents=True)
    (root / "src/content/blog").mkdir(parents=True)
    (data / "garden-recommendations.json").write_text(json.dumps({
        "date": trade_date,
        "applies_to": trade_date,
        "stage": label,
    }), encoding="utf-8")
    (root / f"src/content/blog/{trade_date}.md").write_text(
        f"---\nstage: {label}\n---\n", encoding="utf-8",
    )


def completed(command, stdout="", returncode=0):
    return subprocess.CompletedProcess(command, returncode, stdout=stdout, stderr="")


def test_validate_stage_inputs_binds_article_and_recommendation(tmp_path, monkeypatch):
    monkeypatch.setattr(stage, "ROOT", tmp_path)
    write_stage_files(tmp_path)
    assert stage.validate_stage_inputs("08:30") == ("2026-07-23", "08:30盘前版")

    payload = json.loads((tmp_path / "public/data/garden-recommendations.json").read_text())
    payload["stage"] = "14:30尾盘操作版"
    (tmp_path / "public/data/garden-recommendations.json").write_text(json.dumps(payload))
    with pytest.raises(RuntimeError, match="stage mismatch"):
        stage.validate_stage_inputs("08:30")


def test_managed_paths_keeps_foreign_changes_out_of_candidate():
    changed = {
        "src/content/blog/2026-07-23.md",
        stage.RECOMMENDATIONS_FILE,
        stage.POOL_FILE,
        stage.MID_MACRO_FILE,
        stage.RESEARCH_AUDIT_FILE,
        "public/data/a-compass-dashboard.json",
        "notes/private.txt",
    }
    owned, foreign = stage.managed_paths("2026-07-23", changed)
    assert set(owned) == changed - {"notes/private.txt"}
    assert foreign == ["notes/private.txt"]


def test_managed_paths_rejects_foreign_catalog_input():
    with pytest.raises(RuntimeError, match="catalog inputs contain unrelated changes"):
        stage.managed_paths("2026-07-23", {"public/data/us-etf-pool.json"})


def test_candidate_validation_failure_prevents_ref_update_and_push(tmp_path, monkeypatch):
    monkeypatch.setattr(stage, "ROOT", tmp_path)
    write_stage_files(tmp_path)
    changed = {
        "src/content/blog/2026-07-23.md",
        stage.RECOMMENDATIONS_FILE,
        stage.POOL_FILE,
        stage.MID_MACRO_FILE,
        stage.RESEARCH_AUDIT_FILE,
    }
    calls = []

    def fake_run(command, check=True, **kwargs):
        calls.append(command)
        if command == ["git", "branch", "--show-current"]:
            return completed(command, "main\n")
        if command == ["git", "diff", "--cached", "--quiet"]:
            return completed(command)
        if command == ["git", "rev-parse", "origin/main"]:
            return completed(command, "a" * 40 + "\n")
        if command[:4] == ["git", "diff", "--quiet", "HEAD"]:
            return completed(command, returncode=1)
        return completed(command)

    monkeypatch.setattr(stage, "run", fake_run)
    monkeypatch.setattr(stage, "git_head", lambda: "a" * 40)
    monkeypatch.setattr(stage, "refresh_derived_artifacts", lambda generated_at: None)
    monkeypatch.setattr(stage, "git_changes", lambda: changed)
    monkeypatch.setattr(stage, "create_candidate_commit", lambda paths, message: ("b" * 40, "c" * 40))
    monkeypatch.setattr(
        stage, "validate_candidate_commit",
        lambda commit: (_ for _ in ()).throw(RuntimeError("candidate build failed")),
    )

    with pytest.raises(RuntimeError, match="candidate build failed"):
        stage._publish_stage("08:30")
    assert not any(command[:2] == ["git", "update-ref"] for command in calls)
    assert not any(command[:2] == ["git", "push"] for command in calls)


def test_validated_candidate_is_attached_and_pushed_atomically(tmp_path, monkeypatch):
    monkeypatch.setattr(stage, "ROOT", tmp_path)
    write_stage_files(tmp_path)
    base = "a" * 40
    candidate = "b" * 40
    tree = "c" * 40
    changed = {
        "src/content/blog/2026-07-23.md",
        stage.RECOMMENDATIONS_FILE,
        stage.POOL_FILE,
        stage.MID_MACRO_FILE,
        stage.RESEARCH_AUDIT_FILE,
    }
    calls = []
    heads = iter([base, base])

    def fake_run(command, check=True, **kwargs):
        calls.append(command)
        if command == ["git", "branch", "--show-current"]:
            return completed(command, "main\n")
        if command == ["git", "diff", "--cached", "--quiet"]:
            return completed(command)
        if command == ["git", "rev-parse", "origin/main"]:
            return completed(command, base + "\n")
        if command[:4] == ["git", "diff", "--quiet", "HEAD"]:
            return completed(command, returncode=1)
        return completed(command)

    monkeypatch.setattr(stage, "run", fake_run)
    monkeypatch.setattr(stage, "git_head", lambda: next(heads))
    monkeypatch.setattr(stage, "refresh_derived_artifacts", lambda generated_at: None)
    monkeypatch.setattr(stage, "git_changes", lambda: changed)
    monkeypatch.setattr(stage, "create_candidate_commit", lambda paths, message: (candidate, tree))
    monkeypatch.setattr(stage, "validate_candidate_commit", lambda commit: None)
    monkeypatch.setattr(stage, "is_ancestor", lambda left, right: True)

    result = stage._publish_stage("08:30")
    assert result["status"] == "published"
    assert ["git", "update-ref", "refs/heads/main", candidate, base] in calls
    assert ["git", "push", "origin", f"{candidate}:main"] in calls


def test_dry_run_restores_managed_files_after_success_and_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(stage, "ROOT", tmp_path)
    target = tmp_path / stage.MID_MACRO_FILE
    target.parent.mkdir(parents=True)
    target.write_bytes(b"original\n")

    def mutate_and_return(*args, **kwargs):
        target.write_bytes(b"mutated\n")
        return {"status": "validated"}

    monkeypatch.setattr(stage, "_publish_stage", mutate_and_return)
    assert stage.publish_stage("08:30", dry_run=True) == {"status": "validated"}
    assert target.read_bytes() == b"original\n"

    def mutate_and_fail(*args, **kwargs):
        target.write_bytes(b"broken\n")
        raise RuntimeError("validation failed")

    monkeypatch.setattr(stage, "_publish_stage", mutate_and_fail)
    with pytest.raises(RuntimeError, match="validation failed"):
        stage.publish_stage("08:30", dry_run=True)
    assert target.read_bytes() == b"original\n"

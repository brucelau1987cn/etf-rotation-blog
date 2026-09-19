"""Fully mocked release-safety tests for the precious-inventory cron publisher."""
from __future__ import annotations

import importlib.util
import subprocess
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path("/root/.hermes/scripts/update_precious_inventory_and_release.py")
SPEC = importlib.util.spec_from_file_location("precious_release", SCRIPT)
assert SPEC and SPEC.loader
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


def completed(stdout: str = "", returncode: int = 0):
    return SimpleNamespace(stdout=stdout, stderr="", returncode=returncode)


def install_pipeline_mocks(monkeypatch, tmp_path: Path, events: list, *, fail_at: str | None = None):
    root = tmp_path / "repo"
    root.mkdir()
    data = root / "public/data/precious-inventory.json"
    data.parent.mkdir(parents=True)
    data.write_text('{"status":"ok","marker":"base"}\n', encoding="utf-8")
    candidate = tmp_path / "candidate"
    candidate.mkdir()

    monkeypatch.setattr(release, "ROOT", root)
    monkeypatch.setattr(release, "DATA", data)
    monkeypatch.setattr(release, "site_publish_lock", nullcontext)
    monkeypatch.setattr(release, "dirty_paths", lambda: set())
    monkeypatch.setattr(release, "validate", lambda payload: (1.25, 2.5))
    monkeypatch.setattr(release, "fetch_live", lambda: {"status": "ok"})
    monkeypatch.setattr(release, "candidate_worktree", lambda sha: nullcontext(candidate))
    monkeypatch.setattr(release, "create_candidate_commit", lambda base: events.append(("commit", base)) or "c" * 40)
    monkeypatch.setattr(release, "restore_source_data", lambda: events.append(("restore", root)))

    def fake_run(args, *, timeout=600, cwd=None):
        actual_cwd = Path(cwd) if cwd is not None else root
        events.append(("run", tuple(args), actual_cwd))
        if args[:2] == ["git", "fetch"]:
            return completed()
        if args == ["git", "rev-parse", "HEAD"]:
            return completed("b" * 40 + "\n")
        if args == ["git", "rev-parse", "origin/main"]:
            return completed("b" * 40 + "\n")
        if args == [release.NPM, "run", "build"]:
            if fail_at == "build":
                raise RuntimeError("build failed")
            (candidate / "dist").mkdir(parents=True, exist_ok=True)
            (candidate / "dist/artifact.txt").write_text("candidate", encoding="utf-8")
            return completed()
        if args[:3] == ["git", "status", "--porcelain"]:
            return completed()
        if args[:2] == ["git", "push"]:
            if fail_at == "push":
                raise RuntimeError("push failed")
            return completed()
        if args[:2] == [release.sys.executable, "-c"]:
            assert Path(cwd) == candidate
            assert (candidate / "dist/artifact.txt").read_text() == "candidate"
            if fail_at == "deploy":
                raise RuntimeError("deploy failed")
            return completed("Deployment complete! https://candidate.pages.dev")
        if "update_precious_inventory.py" in args:
            data.write_text('{"status":"ok","marker":"new"}\n', encoding="utf-8")
        return completed()

    monkeypatch.setattr(release, "run", fake_run)
    return root, data, candidate


def test_builds_candidate_worktree_before_push_and_deploys_same_artifact(monkeypatch, tmp_path):
    events = []
    _, _, candidate = install_pipeline_mocks(monkeypatch, tmp_path, events)

    assert release.run_pipeline() == 0

    build_i = next(i for i, e in enumerate(events) if e[:2] == ("run", (release.NPM, "run", "build")))
    push_i = next(i for i, e in enumerate(events) if e[0] == "run" and e[1][:2] == ("git", "push"))
    deploy_i = next(i for i, e in enumerate(events) if e[0] == "run" and e[1][:2] == (release.sys.executable, "-c"))
    assert build_i < push_i < deploy_i
    assert events[build_i][2] == candidate
    assert events[deploy_i][2] == candidate
    assert events[push_i][1] == ("git", "push", "origin", f"{'c' * 40}:main")


@pytest.mark.parametrize("fail_at", ["build", "push", "deploy"])
def test_failures_restore_source_and_exit_without_reusing_root_dist(monkeypatch, tmp_path, fail_at):
    events = []
    root, _, candidate = install_pipeline_mocks(monkeypatch, tmp_path, events, fail_at=fail_at)
    (root / "dist").mkdir()
    (root / "dist/foreign.txt").write_text("dirty", encoding="utf-8")

    assert release.run_pipeline() == 1
    assert events[-1] == ("restore", root)
    deploys = [e for e in events if e[0] == "run" and e[1][:2] == (release.sys.executable, "-c")]
    assert all(e[2] == candidate for e in deploys)


def test_collector_failure_still_restores_source(monkeypatch, tmp_path):
    events = []
    root, _, _ = install_pipeline_mocks(monkeypatch, tmp_path, events)
    original_run = release.run

    def fail_collector(args, *, timeout=600, cwd=None):
        if any(str(arg).endswith("update_precious_inventory.py") for arg in args):
            raise RuntimeError("collector failed after candidate write")
        return original_run(args, timeout=timeout, cwd=cwd)

    monkeypatch.setattr(release, "run", fail_collector)
    assert release.run_pipeline() == 1
    assert events[-1] == ("restore", root)


def test_candidate_worktree_is_always_removed_when_body_raises(monkeypatch, tmp_path):
    calls = []
    worktree = tmp_path / "candidate"
    monkeypatch.setattr(release.tempfile, "mkdtemp", lambda **kwargs: str(worktree))
    monkeypatch.setattr(release, "link_build_dependencies", lambda path: calls.append(("link", path)))

    def fake_run(args, *, timeout=600, cwd=None):
        calls.append((tuple(args), Path(cwd) if cwd else release.ROOT))
        return completed()

    monkeypatch.setattr(release, "run", fake_run)
    with pytest.raises(RuntimeError, match="boom"):
        with release.candidate_worktree("d" * 40):
            raise RuntimeError("boom")

    assert (("git", "worktree", "remove", "--force", str(worktree)), release.ROOT) in calls


def test_preexisting_precious_dirty_file_blocks_before_collection(monkeypatch):
    monkeypatch.setattr(release, "site_publish_lock", nullcontext)
    monkeypatch.setattr(release, "dirty_paths", lambda: {"public/data/precious-inventory.json"})
    calls = []

    def fake_run(args, *, timeout=600, cwd=None):
        calls.append(args)
        if args[:2] == ["git", "fetch"]:
            return completed()
        if args == ["git", "rev-parse", "HEAD"] or args == ["git", "rev-parse", "origin/main"]:
            return completed("a" * 40 + "\n")
        return completed()

    monkeypatch.setattr(release, "run", fake_run)
    assert release.run_pipeline() == 1
    assert all("update_precious_inventory.py" not in args for args in calls)

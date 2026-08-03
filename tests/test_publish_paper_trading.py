#!/usr/bin/env python3
"""Tests for the serialized paper snapshot publisher preflight."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "publish_paper_trading", ROOT / "scripts" / "publish_paper_trading.py"
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("failed to load publish_paper_trading")
publisher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(publisher)


def result(stdout: str = "", returncode: int = 0):
    return SimpleNamespace(stdout=stdout, stderr="", returncode=returncode)


def test_preflight_allows_unrelated_unstaged_changes(monkeypatch):
    calls = []

    def fake_run(cmd, check=True):
        calls.append(cmd)
        if cmd[:3] == ["git", "branch", "--show-current"]:
            return result("main\n")
        if cmd[:3] == ["git", "diff", "--cached"]:
            return result(returncode=0)
        if cmd[:3] == ["git", "diff", "--quiet"]:
            return result(returncode=0)
        if cmd[:3] == ["git", "merge-base", "--is-ancestor"]:
            return result(returncode=0)
        if cmd[:3] == ["git", "rev-list", "--count"]:
            return result("0\n")
        return result()

    monkeypatch.setattr(publisher, "run", fake_run)
    publisher.sync_before_publish()

    # A broad `git status --porcelain` check would reject unrelated generated
    # files. The preflight now checks only the index and paper snapshot path.
    assert ["git", "status", "--porcelain"] not in calls
    assert ["git", "diff", "--cached", "--quiet"] in calls
    assert ["git", "diff", "--quiet", "--", publisher.PAPER_JSON] in calls


def test_preflight_rejects_staged_content(monkeypatch):
    def fake_run(cmd, check=True):
        if cmd[:3] == ["git", "branch", "--show-current"]:
            return result("main\n")
        if cmd[:3] == ["git", "diff", "--cached"]:
            return result(returncode=1)
        return result()

    monkeypatch.setattr(publisher, "run", fake_run)
    try:
        publisher.sync_before_publish()
    except RuntimeError as exc:
        assert "clean git index" in str(exc)
    else:
        raise AssertionError("expected staged-content rejection")


def test_preflight_rejects_dirty_paper_snapshot(monkeypatch):
    def fake_run(cmd, check=True):
        if cmd[:3] == ["git", "branch", "--show-current"]:
            return result("main\n")
        if cmd[:3] == ["git", "diff", "--cached"]:
            return result(returncode=0)
        if cmd[:3] == ["git", "diff", "--quiet"]:
            return result(returncode=1)
        return result()

    monkeypatch.setattr(publisher, "run", fake_run)
    try:
        publisher.sync_before_publish()
    except RuntimeError as exc:
        assert "owned path" in str(exc)
    else:
        raise AssertionError("expected dirty-paper rejection")


def test_paper_runner_subprocess_inherits_shared_lock_marker():
    assert publisher.paper_subprocess_env()["PAPER_PUBLISH_LOCK_HELD"] == "1"


def test_paper_publication_owns_catalog_hash_with_snapshot():
    assert publisher.PUBLISH_FILES == ("public/data/paper-trading.json", "public/data/catalog.json")


def test_paper_publisher_directly_deploys_and_probes(monkeypatch):
    calls = []

    def fake_subprocess_run(cmd, cwd=None, env=None, text=None, capture_output=None):
        calls.append(cmd)
        return SimpleNamespace(
            stdout="✨ Deployment complete! https://abc123.etf-rotation-blog.pages.dev\n",
            stderr="", returncode=0,
        )

    monkeypatch.setattr(publisher.subprocess, "run", fake_subprocess_run)
    publisher.deploy_and_probe()
    assert calls and calls[0][:4] == ["npx", "wrangler", "pages", "deploy"]


def test_paper_publisher_raises_without_deploy_evidence(monkeypatch):
    def fake_subprocess_run(cmd, cwd=None, env=None, text=None, capture_output=None):
        return SimpleNamespace(stdout="uploaded nothing", stderr="", returncode=0)

    monkeypatch.setattr(publisher.subprocess, "run", fake_subprocess_run)
    try:
        publisher.deploy_and_probe()
    except RuntimeError as exc:
        assert "completion evidence" in str(exc)
    else:
        raise AssertionError("expected deploy-evidence rejection")

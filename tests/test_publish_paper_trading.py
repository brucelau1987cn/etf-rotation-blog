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
        assert "paper snapshot" in str(exc)
    else:
        raise AssertionError("expected dirty-paper rejection")

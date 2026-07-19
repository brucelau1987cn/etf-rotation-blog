#!/usr/bin/env python3
"""Single source of truth for the A-share nightly publication contract."""
from __future__ import annotations

import fcntl
import hashlib
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable

STATE = Path("/root/.hermes/state/a-share-nightly-pipeline.json")
LOCK = Path("/root/.hermes/state/a-share-nightly-publish.lock")

BACKTEST_FILE = "public/data/etf-garden-backtest.json"
POOL_FILE = "public/data/etf-garden-pool.json"
SHADOW_FILE = "public/data/model-lab/a-share-shadow.json"
PATH_SHADOW_FILE = "public/data/model-lab/a-share-path-shadow.json"
RESEARCH_AUDIT_FILE = "public/data/model-lab/a-share-research-audit.json"
DEPLOYMENT_MARKER_FILE = "public/data/a-share-nightly-deployment.json"
RECOMMENDATIONS_FILE = "public/data/garden-recommendations.json"
MID_MACRO_FILE = "public/data/a-share-mid-macro.json"

SNAPSHOT_FILES = (
    BACKTEST_FILE,
    POOL_FILE,
    SHADOW_FILE,
    PATH_SHADOW_FILE,
    RESEARCH_AUDIT_FILE,
    DEPLOYMENT_MARKER_FILE,
)

PUBLIC_VERIFY_FILES = SNAPSHOT_FILES + (RECOMMENDATIONS_FILE, MID_MACRO_FILE)


def nightly_content_files(trade_date: str) -> tuple[str, ...]:
    return (
        f"src/content/blog/{trade_date}.md",
        RECOMMENDATIONS_FILE,
        MID_MACRO_FILE,
    )


def nightly_owned_files(trade_date: str) -> tuple[str, ...]:
    return nightly_content_files(trade_date) + SNAPSHOT_FILES


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_hashes(root: Path, paths: Iterable[str]) -> dict[str, str]:
    return {relative: sha256_file(root / relative) for relative in paths}


@contextmanager
def nightly_lock():
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with LOCK.open("a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)

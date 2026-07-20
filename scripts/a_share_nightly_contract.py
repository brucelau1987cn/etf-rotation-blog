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
COMPACT_DASHBOARD_FILE = "public/data/a-compass-dashboard.json"
DATA_CATALOG_FILE = "public/data/catalog.json"
PUBLIC_SCHEMA_FILES = (
    "public/schemas/data-catalog.schema.json",
    "public/schemas/a-compass-dashboard.schema.json",
    "public/schemas/forward-evidence-ledger.schema.json",
    "public/schemas/decision-thesis.schema.json",
    "public/schemas/decision-drift.schema.json",
)
CATALOG_INPUT_FILES = (
    RECOMMENDATIONS_FILE,
    COMPACT_DASHBOARD_FILE,
    POOL_FILE,
    MID_MACRO_FILE,
    RESEARCH_AUDIT_FILE,
    PATH_SHADOW_FILE,
    "public/data/us-etf-garden.json",
    "public/data/us-etf-pool.json",
    "public/data/us-macro-dashboard.json",
    "public/data/paper-trading.json",
    DEPLOYMENT_MARKER_FILE,
)

SNAPSHOT_FILES = (
    BACKTEST_FILE,
    POOL_FILE,
    SHADOW_FILE,
    PATH_SHADOW_FILE,
    RESEARCH_AUDIT_FILE,
    DEPLOYMENT_MARKER_FILE,
)

GENERATED_PUBLIC_FILES = (COMPACT_DASHBOARD_FILE, DATA_CATALOG_FILE)
PUBLIC_VERIFY_FILES = SNAPSHOT_FILES + (
    RECOMMENDATIONS_FILE,
    MID_MACRO_FILE,
) + GENERATED_PUBLIC_FILES + PUBLIC_SCHEMA_FILES


def nightly_content_files(trade_date: str) -> tuple[str, ...]:
    return (
        f"src/content/blog/{trade_date}.md",
        RECOMMENDATIONS_FILE,
        MID_MACRO_FILE,
    )


def nightly_owned_files(trade_date: str) -> tuple[str, ...]:
    return nightly_content_files(trade_date) + SNAPSHOT_FILES + GENERATED_PUBLIC_FILES


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

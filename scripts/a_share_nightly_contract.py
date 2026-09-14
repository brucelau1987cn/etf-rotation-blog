#!/usr/bin/env python3
"""Single source of truth for the A-share nightly publication contract."""
from __future__ import annotations

import fcntl
import hashlib
import os
import secrets
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable

STATE = Path("/root/.hermes/state/a-share-nightly-pipeline.json")
LOCK = Path("/root/.hermes/state/a-share-nightly-publish.lock")
SITE_PUBLISH_LOCK = Path("/root/.hermes/state/etf-site-publish.lock")
SITE_PUBLISH_LOCK_ENV = "SITE_PUBLISH_LOCK_HELD"
_SITE_PUBLISH_THREAD_LOCK = threading.RLock()
_SITE_PUBLISH_LOCAL = threading.local()

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


@contextmanager
def site_publish_lock():
    """Serialize publishers sharing the ETF worktree and Pages target."""
    with _SITE_PUBLISH_THREAD_LOCK:
        depth = getattr(_SITE_PUBLISH_LOCAL, "depth", 0)
        owner_pid = getattr(_SITE_PUBLISH_LOCAL, "pid", None)
        if depth and owner_pid == os.getpid():
            _SITE_PUBLISH_LOCAL.depth = depth + 1
            try:
                yield
            finally:
                _SITE_PUBLISH_LOCAL.depth -= 1
            return

        _SITE_PUBLISH_LOCAL.pid = os.getpid()
        _SITE_PUBLISH_LOCAL.depth = 1
        try:
            if _can_reuse_ancestor_lease():
                yield
                return

            SITE_PUBLISH_LOCK.parent.mkdir(parents=True, exist_ok=True)
            lease_path = SITE_PUBLISH_LOCK.with_name(SITE_PUBLISH_LOCK.name + ".lease")
            with SITE_PUBLISH_LOCK.open("a+") as handle:
                # Validation processes inherit the lease marker, never this fd.
                os.set_inheritable(handle.fileno(), False)
                fcntl.flock(handle, fcntl.LOCK_EX)
                previous = os.environ.get(SITE_PUBLISH_LOCK_ENV)
                marker = f"{os.getpid()}:{secrets.token_hex(32)}"
                lease_path.write_text(marker, encoding="ascii")
                os.environ[SITE_PUBLISH_LOCK_ENV] = marker
                try:
                    yield
                finally:
                    if previous is None:
                        os.environ.pop(SITE_PUBLISH_LOCK_ENV, None)
                    else:
                        os.environ[SITE_PUBLISH_LOCK_ENV] = previous
                    try:
                        if lease_path.read_text(encoding="ascii") == marker:
                            lease_path.unlink()
                    except FileNotFoundError:
                        pass
                    fcntl.flock(handle, fcntl.LOCK_UN)
        finally:
            _SITE_PUBLISH_LOCAL.depth = 0
            _SITE_PUBLISH_LOCAL.pid = None


def _can_reuse_ancestor_lease() -> bool:
    marker = os.environ.get(SITE_PUBLISH_LOCK_ENV, "")
    owner_text, separator, secret = marker.partition(":")
    if not separator or not secret:
        return False
    try:
        owner_pid = int(owner_text)
    except ValueError:
        return False
    if owner_pid == os.getpid() or not _is_ancestor_process(owner_pid):
        return False
    lease_path = SITE_PUBLISH_LOCK.with_name(SITE_PUBLISH_LOCK.name + ".lease")
    try:
        return lease_path.read_text(encoding="ascii") == marker
    except (FileNotFoundError, OSError, UnicodeError):
        return False


def _is_ancestor_process(candidate_pid: int) -> bool:
    current = os.getppid()
    visited = set()
    while current > 1 and current not in visited:
        if current == candidate_pid:
            return True
        visited.add(current)
        try:
            status = Path(f"/proc/{current}/status").read_text(encoding="ascii")
            parent_line = next(line for line in status.splitlines() if line.startswith("PPid:"))
            current = int(parent_line.split()[1])
        except (FileNotFoundError, OSError, StopIteration, ValueError):
            return False
    return current == candidate_pid

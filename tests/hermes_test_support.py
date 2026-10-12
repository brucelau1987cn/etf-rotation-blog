"""Explicit boundary for tests of host-installed cron wrappers."""
from __future__ import annotations

import os
from pathlib import Path

import pytest


def local_script_available(path: Path) -> bool:
    return os.environ.get("CI", "").lower() != "true" and os.access(path, os.R_OK)


def requires_local_script(path: Path):
    return pytest.mark.skipif(
        not local_script_available(path),
        reason=f"host integration test requires readable local script: {path}",
    )


def require_local_script(path: Path) -> None:
    if not local_script_available(path):
        pytest.skip(
            f"host integration module requires readable local script: {path}",
            allow_module_level=True,
        )

"""Regression: cross-publisher shadow dirty-file exemptions come from ONE source."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
HERMES_SCRIPTS = Path("/root/.hermes/scripts")

sys.path.insert(0, str(SCRIPTS))
import shadow_dirty_files as sdf  # noqa: E402
import publish_futures_compass as fc  # noqa: E402


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT))
    spec.loader.exec_module(module)
    return module


def test_shadow_dirty_files_is_canonical_set():
    # After 2026-08-28 refactor(shadow), SHADOW_DIRTY_FILES is the SINGLE source of truth
    # for ALL cross-publisher dirty-file exemptions (korea-tech-factor/us-selector/us-insider
    # PLUS A-share generated artifacts that must not block the futures/low-chip/precious
    # publishers after a failed intraday LLM stage — 2026-08-10 cascade fix).
    # The test asserts the test module does NOT redefine a stale local copy.
    assert not hasattr(sys.modules[__name__], "SHADOW"), (
        "Remove local SHADOW set: SHADOW_DIRTY_FILES in scripts/shadow_dirty_files.py is the canonical source."
    )


def test_futures_external_dirty_supersets_shadow():
    assert sdf.SHADOW_DIRTY_FILES <= fc.EXTERNAL_DIRTY


def test_low_chip_allowlist_references_single_source():
    lc = _load(HERMES_SCRIPTS / "update_low_chip_and_release.py")
    assert lc.ALLOWED_DIRTY == set(sdf.SHADOW_DIRTY_FILES)


def test_precious_allowlist_references_single_source():
    pi = _load(HERMES_SCRIPTS / "update_precious_inventory_and_release.py")
    assert pi.ALLOWED_DIRTY == {"public/data/precious-inventory.json", *sdf.SHADOW_DIRTY_FILES}

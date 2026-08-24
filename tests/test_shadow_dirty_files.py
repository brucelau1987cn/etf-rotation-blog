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

SHADOW = frozenset({
    "public/data/korea-tech-factor-shadow.json",
    "public/data/us-selector-shadow.json",
    "public/data/us-insider-ownership.json",
})


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT))
    spec.loader.exec_module(module)
    return module


def test_shadow_dirty_files_is_canonical_set():
    assert sdf.SHADOW_DIRTY_FILES == SHADOW


def test_futures_external_dirty_supersets_shadow():
    assert sdf.SHADOW_DIRTY_FILES <= fc.EXTERNAL_DIRTY


def test_low_chip_allowlist_references_single_source():
    lc = _load(HERMES_SCRIPTS / "update_low_chip_and_release.py")
    assert lc.ALLOWED_DIRTY == set(sdf.SHADOW_DIRTY_FILES)


def test_precious_allowlist_references_single_source():
    pi = _load(HERMES_SCRIPTS / "update_precious_inventory_and_release.py")
    assert pi.ALLOWED_DIRTY == {"public/data/precious-inventory.json", *sdf.SHADOW_DIRTY_FILES}

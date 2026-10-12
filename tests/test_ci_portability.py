"""CI must collect repository tests without importing host-only cron files."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from hermes_test_support import local_script_available, require_local_script, requires_local_script

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("ci", ["true", "TRUE", "false", ""])
def test_local_script_boundary_uses_ci_and_file_readability(monkeypatch, tmp_path, ci):
    path = tmp_path / "wrapper.py"
    path.write_text("# fixture\n", encoding="utf-8")
    monkeypatch.setenv("CI", ci)
    expected = ci.lower() != "true"
    assert local_script_available(path) is expected
    marker = requires_local_script(path)
    assert marker.name == "skipif"
    assert marker.args == (not expected,)
    if expected:
        require_local_script(path)
    else:
        with pytest.raises(pytest.skip.Exception):
            require_local_script(path)


def test_missing_host_script_is_explicitly_skipped(monkeypatch, tmp_path):
    monkeypatch.delenv("CI", raising=False)
    path = tmp_path / "missing.py"
    assert local_script_available(path) is False
    assert requires_local_script(path).args == (True,)
    with pytest.raises(pytest.skip.Exception, match="readable local script"):
        require_local_script(path)


def test_unreadable_host_script_is_explicitly_skipped(monkeypatch, tmp_path):
    monkeypatch.delenv("CI", raising=False)
    path = tmp_path / "unreadable.py"
    path.write_text("# fixture\n", encoding="utf-8")
    monkeypatch.setattr(os, "access", lambda candidate, mode: False)
    assert local_script_available(path) is False
    assert requires_local_script(path).args == (True,)
    with pytest.raises(pytest.skip.Exception):
        require_local_script(path)


def test_ci_collects_without_host_cron_scripts():
    env = {**os.environ, "CI": "true", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q",
         "tests/test_precious_release_pipeline.py",
         "tests/test_a_share_nightly_precheck.py",
         "tests/test_shadow_dirty_files.py",
         "tests/test_hermes_scripts_executable.py",
         "tests/test_a_share_nightly_publish_gate.py",
         "tests/test_a_share_nightly_stage_shadow.py"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "test_builds_candidate_worktree_before_push" not in result.stdout
    assert "test_precheck_accepts_partial_qfq" not in result.stdout
    assert "test_shadow_dirty_files_is_canonical_set" in result.stdout
    assert "test_guard_detects_a_missing_execute_bit" in result.stdout

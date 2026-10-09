"""Guard the executable bit on Hermes cron wrapper scripts.

A wrapper created via ``write_file`` lands as mode 0644. cron still launches it
because Hermes invokes the interpreter directly, so the next scheduled run
masks the bug while an ad-hoc force-run fails with ``Permission denied`` and
looks like a script error. This test fails the moment that bit is lost, so the
defect surfaces at commit time instead of during a recovery run.

Paths are read from the live cron inventory, so a newly scheduled wrapper is
covered automatically. The checks skip cleanly on a host without the Hermes
state directory (the suite is also run in isolated checkouts).
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

HERMES_SCRIPTS = Path("/root/.hermes/scripts")
CRON_JOBS = Path(os.environ.get("HERMES_CRON_JOBS", "/root/.hermes/cron/jobs.json"))

# Wrapper types that cron launches and that a force-run must be able to exec.
WRAPPER_SUFFIXES = (".py", ".sh")


def _owner_executable(path: Path) -> bool:
    return bool(path.stat().st_mode & stat.S_IXUSR)


def _mode(path: Path) -> str:
    return oct(path.stat().st_mode & 0o777)


def _resolve_script(raw: str) -> Path:
    """Resolve a cron script reference to a filesystem path.

    The inventory mixes absolute paths with bare names relative to the Hermes
    scripts directory.
    """
    candidate = Path(raw)
    return candidate if candidate.is_absolute() else HERMES_SCRIPTS / candidate


def _enabled_script_jobs() -> list[tuple[str, Path]]:
    if not CRON_JOBS.exists():
        return []
    payload = json.loads(CRON_JOBS.read_text(encoding="utf-8"))
    jobs = payload if isinstance(payload, list) else payload.get("jobs", [])
    found: list[tuple[str, Path]] = []
    for job in jobs:
        if not isinstance(job, dict) or not job.get("enabled"):
            continue
        script = job.get("script")
        if script:
            found.append((str(job.get("id", "?")), _resolve_script(str(script))))
    return found


def _script_wrappers() -> list[Path]:
    if not HERMES_SCRIPTS.is_dir():
        return []
    return sorted(
        path for path in HERMES_SCRIPTS.iterdir()
        if path.is_file() and path.suffix in WRAPPER_SUFFIXES
    )


def test_active_cron_scripts_are_executable():
    """Every script an enabled cron job points at must be owner-executable."""
    jobs = _enabled_script_jobs()
    if not jobs:
        pytest.skip(f"no cron inventory at {CRON_JOBS}")

    missing = [
        f"{job_id}: {path} (mode {_mode(path)})"
        for job_id, path in jobs
        if path.exists() and not _owner_executable(path)
    ]
    assert not missing, "cron wrappers lost the executable bit:\n" + "\n".join(missing)


def test_active_cron_scripts_exist():
    """A scheduled job pointing at a deleted wrapper is a silent no-op."""
    jobs = _enabled_script_jobs()
    if not jobs:
        pytest.skip(f"no cron inventory at {CRON_JOBS}")

    absent = [f"{job_id}: {path}" for job_id, path in jobs if not path.exists()]
    assert not absent, "cron references missing wrappers:\n" + "\n".join(absent)


def test_hermes_script_wrappers_are_executable():
    """Hold the whole wrapper directory at 0755, matching the existing files."""
    wrappers = _script_wrappers()
    if not wrappers:
        pytest.skip(f"no wrappers under {HERMES_SCRIPTS}")

    offenders = [
        f"{path.name} (mode {_mode(path)})"
        for path in wrappers
        if not _owner_executable(path)
    ]
    assert not offenders, "Hermes wrappers lost the executable bit:\n" + "\n".join(offenders)


def test_guard_detects_a_missing_execute_bit(tmp_path):
    """Keep the guard honest: a 0644 wrapper must be reported, not tolerated."""
    wrapper = tmp_path / "wrapper.sh"
    wrapper.write_text("#!/usr/bin/env bash\necho hi\n", encoding="utf-8")
    wrapper.chmod(0o644)
    assert _owner_executable(wrapper) is False

    wrapper.chmod(0o755)
    assert _owner_executable(wrapper) is True


def test_resolve_script_handles_absolute_and_bare_names():
    """The inventory mixes both forms; resolution must cover each."""
    assert _resolve_script("/root/.hermes/scripts/foo.py") == Path("/root/.hermes/scripts/foo.py")
    assert _resolve_script("foo.py") == HERMES_SCRIPTS / "foo.py"

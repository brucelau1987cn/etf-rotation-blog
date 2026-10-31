"""Contract tests for the single project interpreter/build entry point.

Guards two production failures:
- PYTHONPATH leaking the gateway's 3.14 site-packages into the 3.11 build venv
  shadowed jsonschema's `rpds` native extension (`No module named 'rpds.rpds'`).
- Publishers shelling out to a bare `python3`, so whichever interpreter was
  first on PATH (including the gateway venv) owned `.build-venv`.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import project_runtime  # noqa: E402


def test_project_env_strips_gateway_virtualenv():
    env = project_runtime.project_subprocess_env(
        {
            "PYTHONPATH": "/root/.hermes/installs/x/venv/lib/python3.14/site-packages",
            "PYTHONHOME": "/root/.hermes/installs/x/venv",
            "VIRTUAL_ENV": "/root/.hermes/installs/x/venv",
            "PATH": "/usr/bin:/bin",
        }
    )
    assert "PYTHONPATH" not in env
    assert "PYTHONHOME" not in env
    assert "VIRTUAL_ENV" not in env
    assert env["PATH"].startswith(project_runtime.PROJECT_NODE_BIN)


def test_build_python_is_relative_to_project_root():
    assert project_runtime.BUILD_PYTHON == ".build-venv/bin/python"


def test_bootstrap_python_rejects_newer_interpreters(monkeypatch, tmp_path):
    """A newer-only image must not hard-fail: it drives the existing venv."""
    fake_python = tmp_path / "python3.14"
    fake_python.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(project_runtime, "_BOOTSTRAP_PYTHON", None)
    monkeypatch.setattr(project_runtime, "PROJECT_PYTHON", str(fake_python))
    monkeypatch.delenv("HERMES_BUILD_PYTHON", raising=False)

    def fake_version(executable):
        return (3, 14) if executable == str(fake_python) else None

    monkeypatch.setattr(project_runtime, "_python_version", fake_version)
    monkeypatch.setattr(project_runtime.shutil, "which", lambda name: None)
    assert project_runtime.bootstrap_python() == str(fake_python)


def test_bootstrap_python_fails_when_no_interpreter_exists(monkeypatch, tmp_path):
    monkeypatch.setattr(project_runtime, "_BOOTSTRAP_PYTHON", None)
    monkeypatch.setattr(project_runtime, "PROJECT_PYTHON", str(tmp_path / "missing"))
    monkeypatch.delenv("HERMES_BUILD_PYTHON", raising=False)
    monkeypatch.setattr(project_runtime.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError):
        project_runtime.bootstrap_python()


def test_bootstrap_python_prefers_supported_system_python(monkeypatch, tmp_path):
    fake_python = tmp_path / "python3"
    fake_python.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(project_runtime, "_BOOTSTRAP_PYTHON", None)
    monkeypatch.setattr(project_runtime, "PROJECT_PYTHON", str(fake_python))
    monkeypatch.setattr(project_runtime, "_python_version", lambda executable: (3, 11))
    monkeypatch.setattr(project_runtime.shutil, "which", lambda name: None)

    assert project_runtime.bootstrap_python() == str(fake_python)


def _fake_run(calls):
    from types import SimpleNamespace

    def fake(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(stdout="deadbee\n", stderr="", returncode=0)

    return fake


def test_build_static_site_uses_project_interpreters(monkeypatch):
    calls = []
    monkeypatch.setattr(project_runtime, "run", _fake_run(calls))
    monkeypatch.setattr(project_runtime, "bootstrap_python", lambda: "/usr/bin/python3")

    project_runtime.build_static_site()

    assert calls[0] == ["/usr/bin/python3", "scripts/bootstrap_build_python.py"]
    build_python_calls = [c for c in calls if c[0] == project_runtime.BUILD_PYTHON]
    assert [c[1] for c in build_python_calls] == [
        f"scripts/{name}" for name in project_runtime.CONTRACT_CHAIN_SCRIPTS
    ]
    assert ["npx", "astro", "build"] in calls
    assert ["node", "scripts/inject_public_js_version.mjs", "dist"] in calls
    # No step may fall back to an unqualified interpreter.
    assert [c for c in calls if c[0] in {"python3", "python"}] == []


def test_build_static_site_can_skip_cross_market_chain(monkeypatch):
    calls = []
    monkeypatch.setattr(project_runtime, "run", _fake_run(calls))
    monkeypatch.setattr(project_runtime, "bootstrap_python", lambda: "/usr/bin/python3")

    project_runtime.build_static_site(contract_chain=False)

    assert [c for c in calls if c[0] == project_runtime.BUILD_PYTHON] == []
    assert ["npx", "astro", "build"] in calls
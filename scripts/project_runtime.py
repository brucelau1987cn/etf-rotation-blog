#!/usr/bin/env python3
"""Single source of truth for project interpreters and the site build.

Every publisher, every cron stage, and every ad-hoc build must resolve Python
through this module. Two concrete failures motivated it (2026-10-29/30):

- ``PYTHONPATH`` leaking the Hermes gateway's Python 3.14 site-packages into
  the Python 3.11 ``.build-venv`` shadowed jsonschema's ``rpds`` dependency,
  whose native extension is built for 3.14:
  ``ModuleNotFoundError: No module named 'rpds.rpds'``.
  That crashed ``validate_public_data_contracts.py`` and broke a site build.
- Choosing ``python3`` from ``PATH`` let whichever interpreter happened to be
  first — including the 3.14 gateway venv — create or drive ``.build-venv``,
  so the build venv's Python version depended on the caller.

Rules:
- ``PROJECT_PYTHON`` runs repository scripts that only need the stdlib.
- ``BUILD_PYTHON`` (``.build-venv/bin/python``) runs anything importing the
  pinned build requirements (jsonschema, exchange-calendars).
- Both are always launched with ``project_subprocess_env()``.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PROJECT_PYTHON = "/usr/bin/python3"
BUILD_PYTHON = ".build-venv/bin/python"
PROJECT_NODE_BIN = "/root/.local/bin"

# `.build-venv` is Python 3.11. Never let a newer interpreter own it: a 3.14
# gateway venv would rebuild the environment with a different ABI and every
# `import rpds` inside the build then fails on a missing native extension.
MAX_BOOTSTRAP_PYTHON = (3, 12)

_BOOTSTRAP_PYTHON: str | None = None


def project_subprocess_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Environment for project commands: out of any ambient virtualenv."""
    env = dict(os.environ if base is None else base)
    env.pop("VIRTUAL_ENV", None)
    # PYTHONPATH points at the gateway's Python 3.14 site-packages. The build
    # venv is Python 3.11, so those packages shadow the venv's own copies:
    # the gateway's jsonschema needs `rpds`, whose native extension is built
    # for 3.14 and cannot load in 3.11. Project commands must resolve imports
    # from the interpreter they were invoked with — strip PYTHONPATH and
    # PYTHONHOME for the same reason VIRTUAL_ENV is stripped.
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env["PATH"] = f"{PROJECT_NODE_BIN}:/usr/bin:" + env.get("PATH", "")
    return env


def run(
    command: list[str],
    *,
    env: dict[str, str] | None = None,
    check: bool = True,
    timeout: int = 900,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=str(cwd or ROOT),
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "no command output").strip()
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(command)}\n{detail[:800]}"
        )
    return result


def _python_version(executable: str) -> tuple[int, ...] | None:
    try:
        result = subprocess.run(
            [executable, "-c", "import sys;print('%d.%d' % sys.version_info[:2])"],
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    text = (result.stdout or "").strip()
    try:
        return tuple(int(part) for part in text.split("."))
    except ValueError:
        return None


def bootstrap_python() -> str:
    """Interpreter allowed to create/drive `.build-venv`.

    Prefers `/usr/bin/python3`, then any `python3` on PATH, and rejects any
    interpreter newer than `MAX_BOOTSTRAP_PYTHON` so the build venv cannot be
    silently re-created on the gateway's 3.14 ABI.

    When no supported interpreter exists (a CI image older/newer than the pin),
    fall back to the newest available one with a warning rather than failing the
    build: once `.build-venv` exists, `bootstrap_build_python.py` drives the
    venv's own interpreter and only `pip install`s into it, so an outer newer
    python cannot change the venv's ABI.
    """
    global _BOOTSTRAP_PYTHON
    if _BOOTSTRAP_PYTHON:
        return _BOOTSTRAP_PYTHON
    override = os.environ.get("HERMES_BUILD_PYTHON")
    candidates = [override] if override else []
    candidates.append(PROJECT_PYTHON)
    for name in ("python3", "python3.12", "python3.11"):
        found = shutil.which(name)
        if found:
            candidates.append(found)
    fallback: str | None = None
    for candidate in dict.fromkeys(c for c in candidates if c):
        if not Path(candidate).exists():
            continue
        version = _python_version(candidate)
        if version is None:
            continue
        if version <= MAX_BOOTSTRAP_PYTHON:
            _BOOTSTRAP_PYTHON = candidate
            return candidate
        fallback = fallback or candidate
    if fallback:
        print(
            f"warning: no python3 <= {'.'.join(map(str, MAX_BOOTSTRAP_PYTHON))}; "
            f"driving the existing build venv with {fallback} ({_python_version(fallback)})",
            file=sys.stderr,
        )
        _BOOTSTRAP_PYTHON = fallback
        return fallback
    raise RuntimeError(
        "no usable python3 found for the build venv (set HERMES_BUILD_PYTHON)"
    )


def bootstrap_build_python(env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Create/refresh `.build-venv` with an allowed interpreter."""
    return run(
        [bootstrap_python(), "scripts/bootstrap_build_python.py"],
        env=project_subprocess_env(env),
        timeout=600,
    )


def asset_version(env: dict[str, str] | None = None) -> str:
    result = run(
        ["git", "rev-parse", "--short", "HEAD"],
        env=project_subprocess_env(env),
        check=False,
        timeout=30,
    )
    value = (result.stdout or "").strip()
    if result.returncode == 0 and value:
        return value
    import time

    return str(int(time.time()))


def astro_build(env: dict[str, str] | None = None, *, clean_dist: bool = False) -> None:
    """Run the Astro build plus the public JS asset-version injection."""
    import shutil as _shutil

    build_env = project_subprocess_env(env)
    if clean_dist:
        _shutil.rmtree(ROOT / "dist", ignore_errors=True)
    build_env.setdefault("PUBLIC_JS_ASSET_VERSION", asset_version(build_env))
    run(["npx", "astro", "build"], env=build_env, timeout=1800)
    run(["node", "scripts/inject_public_js_version.mjs", "dist"], env=build_env, timeout=300)


CONTRACT_CHAIN_SCRIPTS = (
    "validate_futures_compass.py",
    "validate_dashboard_batches.py",
    "generate_public_dashboard_payloads.py",
    "generate_data_catalog.py",
    "validate_public_data_contracts.py",
)


def contract_chain_run(env: dict[str, str]) -> None:
    """Validator/generator steps owned by the full site build."""
    for script in CONTRACT_CHAIN_SCRIPTS:
        run([BUILD_PYTHON, f"scripts/{script}"], env=env, timeout=900)


def build_static_site(
    env: dict[str, str] | None = None,
    *,
    contract_chain: bool = True,
    clean_dist: bool = False,
) -> None:
    """Canonical static-site build.

    `contract_chain=False` is for single-market publishers that own one payload
    and must not consume another market's live freshness (they validate their
    own snapshot first, then build).
    """
    build_env = project_subprocess_env(env)
    bootstrap_build_python(build_env)
    if contract_chain:
        contract_chain_run(build_env)
    astro_build(build_env, clean_dist=clean_dist)
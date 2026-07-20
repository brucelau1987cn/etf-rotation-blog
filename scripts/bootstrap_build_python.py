#!/usr/bin/env python3
"""Create the isolated Python environment used by static builds."""
from __future__ import annotations

import hashlib
import subprocess
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = ROOT / "requirements-build.txt"
VENV = ROOT / ".build-venv"
STAMP = VENV / ".requirements.sha256"


def main() -> int:
    expected = hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest()
    python = VENV / "bin/python"
    if not python.exists():
        venv.EnvBuilder(with_pip=True, clear=True).create(VENV)
    current = STAMP.read_text(encoding="utf-8").strip() if STAMP.exists() else ""
    if current != expected:
        subprocess.run(
            [str(python), "-m", "pip", "install", "--disable-pip-version-check", "-q", "-r", str(REQUIREMENTS)],
            cwd=ROOT, check=True, timeout=300,
        )
        STAMP.write_text(expected + "\n", encoding="utf-8")
    print(f"build Python ready: {python.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
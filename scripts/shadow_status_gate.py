#!/usr/bin/env python3
"""Persist shadow-source health and surface sustained degradation."""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def record_status(state_path: Path, status: str, *, threshold: int = 3) -> dict[str, Any]:
    previous: dict[str, Any] = {}
    try:
        previous = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    unhealthy = status in {"degraded", "error", "empty", "leverage_only", "stale_input"}
    streak = int(previous.get("streak") or 0) + 1 if unhealthy else 0
    alert = status == "error" or (unhealthy and streak >= threshold)
    payload = {
        "status": status, "streak": streak, "alert": alert,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    atomic_json(state_path, payload)
    return {"status": status, "streak": streak, "alert": alert, "exit_code": 1 if alert else 0}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--threshold", type=int, default=3)
    args = parser.parse_args(argv)
    try:
        payload = json.loads(args.payload.read_text(encoding="utf-8"))
        status = str(payload.get("status") or "error") if isinstance(payload, dict) else "error"
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        status = "error"
    result = record_status(args.state, status, threshold=max(1, args.threshold))
    if result["alert"]:
        print(json.dumps(result, ensure_ascii=False))
    return int(result["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
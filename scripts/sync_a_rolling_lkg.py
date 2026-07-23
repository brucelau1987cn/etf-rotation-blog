#!/usr/bin/env python3
"""Fetch, project and atomically persist the A-share rolling-signal LKG snapshot."""
from __future__ import annotations

import argparse
import json
import os
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from a_rolling_contract import project_upstream, validate_public_payload
except ModuleNotFoundError:
    from scripts.a_rolling_contract import project_upstream, validate_public_payload

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "public/data/a-rolling-signals.json"
DEFAULT_TIMEOUT = 15
MAX_BYTES = 512 * 1024


def atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_upstream(source_file: Path | None, source_url: str | None, timeout: int) -> dict[str, Any]:
    if source_file is not None:
        payload = json.loads(source_file.read_text(encoding="utf-8"))
    elif source_url:
        request = urllib.request.Request(
            source_url,
            headers={"Accept": "application/json", "User-Agent": "ETF-Rolling-LKG/1.0"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                raise RuntimeError(f"upstream returned HTTP {response.status}")
            content_type = response.headers.get_content_type()
            if content_type not in {"application/json", "text/json"}:
                raise RuntimeError(f"upstream returned {content_type}")
            raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise RuntimeError("upstream payload exceeds size limit")
            payload = json.loads(raw.decode("utf-8"))
    else:
        raise RuntimeError("A_ROLLING_UPSTREAM_URL or --source-file is required")
    if not isinstance(payload, dict):
        raise RuntimeError("upstream payload root must be an object")
    return payload


def sync(
    *,
    output: Path = DEFAULT_OUTPUT,
    source_file: Path | None = None,
    source_url: str | None = None,
    stale_after_seconds: int = 900,
    timeout: int = DEFAULT_TIMEOUT,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or datetime.now(timezone.utc)
    upstream = read_upstream(source_file, source_url, timeout)
    projected = project_upstream(
        upstream,
        generated_at=current.isoformat(),
        stale_after_seconds=stale_after_seconds,
    )
    previous = output.read_bytes() if output.exists() else None
    encoded = (json.dumps(projected, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if previous == encoded:
        return {"status": "unchanged", "output": str(output), "data_as_of": projected["data_as_of"]}
    atomic_write(output, projected)
    validate_public_payload(json.loads(output.read_text(encoding="utf-8")))
    return {
        "status": "updated",
        "output": str(output),
        "data_as_of": projected["data_as_of"],
        "freshness": projected["freshness"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--source-file", type=Path)
    parser.add_argument("--source-url", default=os.environ.get("A_ROLLING_UPSTREAM_URL"))
    parser.add_argument("--stale-after-seconds", type=int, default=900)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    args = parser.parse_args()
    try:
        result = sync(
            output=args.output,
            source_file=args.source_file,
            source_url=args.source_url,
            stale_after_seconds=args.stale_after_seconds,
            timeout=args.timeout,
        )
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError, urllib.error.URLError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

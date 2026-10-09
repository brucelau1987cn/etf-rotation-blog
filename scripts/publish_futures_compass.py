#!/usr/bin/env python3
"""Refresh, validate, commit, and directly deploy the futures compass snapshot."""
from __future__ import annotations

import fcntl
import json
import subprocess
from contextlib import contextmanager
from pathlib import Path

try:
    from a_share_nightly_contract import site_publish_lock
    from pages_release import release_pages
    from shadow_dirty_files import SHADOW_DIRTY_FILES
except ModuleNotFoundError:
    from scripts.a_share_nightly_contract import site_publish_lock
    from scripts.pages_release import release_pages
    from scripts.shadow_dirty_files import SHADOW_DIRTY_FILES

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = "public/data/futures-compass.json"
BRIEFING = "public/data/futures-compass-briefing.json"
PUBLISH_FILES = (SNAPSHOT, BRIEFING)
FUTURES_PYTHON = "/root/.cache/etf-futures/venv/bin/python"
LOCK = Path("/root/.hermes/state/futures-compass-publish.lock")
EXTERNAL_DIRTY = {
    # 跨发布器共享的 shadow 脏文件豁免（单一来源 scripts/shadow_dirty_files.py）
    *SHADOW_DIRTY_FILES,
}



def run(command: list[str], check: bool = True, **kwargs) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=ROOT, check=check, text=True, capture_output=True, **kwargs)


@contextmanager
def publish_lock():
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with LOCK.open("a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield
        fcntl.flock(handle, fcntl.LOCK_UN)


def is_ancestor(left: str, right: str) -> bool:
    return run(["git", "merge-base", "--is-ancestor", left, right], check=False).returncode == 0


def _calendar_gate(stdout: str) -> dict | None:
    """Extract the maintenance runner's ``calendar_gate`` from its JSON stdout.

    The runner prints one JSON object; a closed-day run emits only the gate, so
    scan lines and take the first parseable object carrying a gate.
    """
    for line in reversed((stdout or "").splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except ValueError:
            continue
        if isinstance(payload, dict) and isinstance(payload.get("calendar_gate"), dict):
            return payload["calendar_gate"]
    return None


def foreign_dirty_paths(lines: list[str]) -> list[str]:
    paths: list[str] = []
    for line in lines:
        path = line[3:].strip() if len(line) >= 4 else ""
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if path and path not in EXTERNAL_DIRTY:
            # Ignore A-share blog articles (dynamic filenames, can't statically list)
            if path.startswith("src/content/blog/"):
                continue
            paths.append(path)
    return paths


def restore_tracked_dist() -> None:
    for path in EXTERNAL_DIRTY:
        shown = run(["git", "show", f"HEAD:{path}"], check=False)
        if shown.returncode != 0:
            # 未跟踪 / HEAD 不存在的 shadow 文件（如打板层 / mootdx 首次生成，.gitignore 隔离），跳过
            continue
        target = ROOT / "dist" / Path(path).relative_to("public")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(shown.stdout, encoding="utf-8")


def preflight() -> None:
    if run(["git", "branch", "--show-current"]).stdout.strip() != "main":
        raise RuntimeError("futures publisher requires main branch")
    if run(["git", "diff", "--cached", "--quiet"], check=False).returncode:
        raise RuntimeError("futures publisher requires a clean git index")
    if run(["git", "diff", "--quiet", "--", *PUBLISH_FILES], check=False).returncode:
        raise RuntimeError("futures snapshot or briefing already has uncommitted changes")
    dirty = run(["git", "status", "--porcelain", "--untracked-files=all"]).stdout.splitlines()
    foreign = foreign_dirty_paths(dirty)
    if foreign:
        raise RuntimeError(f"futures publisher requires a clean worktree; foreign dirty paths: {foreign}")
    run(["git", "fetch", "origin", "main"])
    if is_ancestor("HEAD", "origin/main"):
        run(["git", "merge", "--ff-only", "origin/main"])
    elif not is_ancestor("origin/main", "HEAD"):
        raise RuntimeError("main and origin/main diverged")


def publish(slot: str) -> dict[str, str]:
    with site_publish_lock(), publish_lock():
        preflight()
        try:
            # check=False so the receipt is reachable on every exit path. With
            # check=True a blocked calendar raised CalledProcessError before the
            # gate was ever parsed, so the guard below only ever saw "skip" and
            # the error case surfaced as a bare traceback (2026-10-08).
            maintenance = run(
                [FUTURES_PYTHON, "scripts/run_futures_compass_maintenance.py", "--slot", slot],
                check=False,
            )
            # A closed exchange calendar makes the maintenance run a no-op; the
            # published snapshot then legitimately stays untouched (and may be
            # older than the validator's freshness window). Honour the gate
            # instead of failing the job on a stale-but-correct snapshot.
            gate = _calendar_gate(maintenance.stdout)
            if gate is not None and gate.get("status") == "skip":
                return {"status": "idempotent", "reason": str(gate.get("reason") or "exchange calendar is closed"), "slot": slot}
            if maintenance.returncode != 0:
                # Fail closed, but name the cause. An unresolvable calendar is
                # UNAVAILABLE rather than a silent skip: it means the period's
                # output cannot be recomputed, which the operator must see.
                if gate is not None and gate.get("status") == "error":
                    raise RuntimeError(
                        f"STAGING BLOCKER: {gate.get('reason')} "
                        f"({gate.get('calendar_source')}) for {gate.get('calendar_date')}"
                    )
                detail = (maintenance.stderr or maintenance.stdout or "").strip()
                raise RuntimeError(
                    f"futures maintenance failed for {slot} (exit {maintenance.returncode}): {detail[:400]}"
                )
            run([FUTURES_PYTHON, "scripts/validate_futures_compass.py"])
            if run(["git", "diff", "--quiet", "--", *PUBLISH_FILES], check=False).returncode == 0:
                return {"status": "unchanged", "slot": slot}
            # The full site build validates every market's live batch. A-share
            # and US shadow writers may legitimately be converging here; this
            # publisher owns only the futures payload and must build the
            # already-validated snapshot without consuming foreign freshness.
            run(["python3", "scripts/bootstrap_build_python.py"])
            run(["npx", "astro", "build"])
            run(["node", "scripts/inject_public_js_version.mjs", "dist"])
            run(["git", "commit", "--only", "-m", f"data: refresh futures compass {slot}", "--", *PUBLISH_FILES])
        except Exception:
            run(["git", "reset", "--quiet", "--", *PUBLISH_FILES], check=False)
            run(["git", "checkout", "--", *PUBLISH_FILES], check=False)
            raise
        run(["git", "fetch", "origin", "main"])
        if not is_ancestor("origin/main", "HEAD"):
            raise RuntimeError("origin/main changed during futures publication")
        run(["git", "push", "origin", "HEAD:main"])
        restore_tracked_dist()
        release_pages([
            "https://etf.peekabo.cc/futures-compass/",
            "https://etf.peekabo.cc/data/futures-compass.json",
            "https://etf.peekabo.cc/data/futures-compass-briefing.json",
        ], {
            "https://etf.peekabo.cc/data/futures-compass.json": Path(SNAPSHOT),
            "https://etf.peekabo.cc/data/futures-compass-briefing.json": Path(BRIEFING),
        })
        return {"status": "published", "slot": slot}


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--slot", required=True, choices=("preopen", "day-close", "night"))
    args = parser.parse_args()
    print(json.dumps(publish(args.slot), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Refresh, validate, commit, and directly deploy the futures compass snapshot."""
from __future__ import annotations

import fcntl
import json
import subprocess
from contextlib import contextmanager
from pathlib import Path

try:
    from pages_release import release_pages
    from shadow_dirty_files import SHADOW_DIRTY_FILES
except ModuleNotFoundError:
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
    with publish_lock():
        preflight()
        try:
            run([FUTURES_PYTHON, "scripts/run_futures_compass_maintenance.py", "--slot", slot])
            run([FUTURES_PYTHON, "scripts/validate_futures_compass.py"])
            if run(["git", "diff", "--quiet", "--", *PUBLISH_FILES], check=False).returncode == 0:
                return {"status": "unchanged", "slot": slot}
            run(["npm", "run", "build"])
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

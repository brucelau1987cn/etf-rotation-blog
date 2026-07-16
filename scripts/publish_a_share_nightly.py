#!/usr/bin/env python3
"""Validate, build, commit, and push a prepared A-share nightly content batch."""
from __future__ import annotations

import argparse
import fcntl
import json
import re
import subprocess
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
STATE = Path("/root/.hermes/state/a-share-nightly-pipeline.json")
LOCK = Path("/root/.hermes/state/a-share-nightly-publish.lock")
CN = ZoneInfo("Asia/Shanghai")
ALLOWED_STATIC = {
    "public/data/etf-garden-pool.json",
    "public/data/model-lab/a-share-shadow.json",
    "public/data/model-lab/a-share-kronos-shadow.json",
}


def run(command: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=check)


@contextmanager
def publish_lock():
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with LOCK.open("a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield
        fcntl.flock(handle, fcntl.LOCK_UN)


def load_state(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("status") != "prepared":
        raise RuntimeError(f"nightly pipeline is not prepared: {payload.get('status')!r}")
    return payload


def git_changes() -> set[str]:
    result = run(["git", "status", "--porcelain", "--untracked-files=normal"])
    return {line[3:].strip() for line in result.stdout.splitlines() if len(line) >= 4}


def is_ancestor(left: str, right: str) -> bool:
    return run(["git", "merge-base", "--is-ancestor", left, right], check=False).returncode == 0


def sync_remote() -> None:
    branch = run(["git", "branch", "--show-current"]).stdout.strip()
    if branch != "main":
        raise RuntimeError(f"nightly publisher requires main branch, got {branch!r}")
    if run(["git", "diff", "--cached", "--quiet"], check=False).returncode != 0:
        raise RuntimeError("nightly publisher requires a clean git index")
    run(["git", "fetch", "origin", "main"])
    if is_ancestor("origin/main", "HEAD"):
        if run(["git", "rev-list", "--count", "origin/main..HEAD"]).stdout.strip() != "0":
            run(["git", "push", "origin", "HEAD:main"])
    elif is_ancestor("HEAD", "origin/main"):
        run(["git", "merge", "--ff-only", "origin/main"])
    else:
        raise RuntimeError("main and origin/main diverged; manual reconciliation required")


def ensure_pub_date(article: Path, trade_date: str) -> None:
    text = article.read_text(encoding="utf-8")
    match = re.match(r"(?s)^---\n(?P<header>.*?)\n---\n", text)
    if not match:
        raise RuntimeError("article frontmatter is missing")
    header = match.group("header")
    pub_match = re.search(r"(?m)^pubDate:\s*['\"]?([^'\"\n]+)", header)
    if pub_match:
        if pub_match.group(1).strip() != trade_date:
            raise RuntimeError(
                f"article pubDate mismatch: pubDate={pub_match.group(1).strip()!r}, trade_date={trade_date!r}"
            )
        return
    updated_header = f"pubDate: {trade_date}\n{header}"
    article.write_text(text[:match.start("header")] + updated_header + text[match.end("header"):], encoding="utf-8")


def publish(state_path: Path = STATE, dry_run: bool = False, now: datetime | None = None) -> dict[str, Any]:
    state = load_state(state_path)
    trade_date = state["trade_date"]
    current = (now or datetime.now(CN)).astimezone(CN)
    prepared_date = str(state.get("prepared_at") or "")[:10]
    if trade_date != current.date().isoformat() or prepared_date != trade_date:
        raise RuntimeError(
            f"stale nightly manifest: trade_date={trade_date!r}, prepared_date={prepared_date!r}, current={current.date().isoformat()!r}"
        )
    article = ROOT / f"src/content/blog/{trade_date}.md"
    recommendations = ROOT / "public/data/garden-recommendations.json"
    mid_macro = ROOT / "public/data/a-share-mid-macro.json"
    for path in (article, recommendations, mid_macro):
        if not path.exists():
            raise RuntimeError(f"required nightly output missing: {path.relative_to(ROOT)}")

    reco = json.loads(recommendations.read_text(encoding="utf-8"))
    if reco.get("date") != trade_date or reco.get("stage") != state.get("expected_stage"):
        raise RuntimeError(
            f"recommendations stage/date mismatch: date={reco.get('date')!r}, stage={reco.get('stage')!r}"
        )
    ensure_pub_date(article, trade_date)
    article_text = article.read_text(encoding="utf-8")
    stage_markers = (
        "stage: 22:00夜间最终版",
        'stage: "22:00夜间最终版"',
        "stage: '22:00夜间最终版'",
    )
    if not any(marker in article_text for marker in stage_markers):
        raise RuntimeError("article frontmatter is not 22:00夜间最终版")
    if "### 22:00 夜间最终整理" not in article_text:
        raise RuntimeError("article is missing the 22:00 final section")

    allowed = set(state["content_files"]) | set(state.get("snapshot_files") or [])
    changed = git_changes()
    owned_changes = changed & allowed
    if not owned_changes:
        return {"status": "idempotent", "trade_date": trade_date, "changed": []}
    foreign = changed - allowed
    # Unrelated generated files may coexist in the working tree. Scoped commit
    # keeps them out; an unexpected change inside the nightly ownership set is fatal.
    unexpected_owned = owned_changes - (set(state["content_files"]) | ALLOWED_STATIC)
    if unexpected_owned:
        raise RuntimeError(f"unexpected owned changes: {sorted(unexpected_owned)}")

    run(["python3", "scripts/enrich_garden_recommendations.py", "--validate"])
    batch = run(["python3", "scripts/validate_dashboard_batches.py"])
    run(["npm", "run", "build"])
    if dry_run:
        return {
            "status": "validated",
            "trade_date": trade_date,
            "changed": sorted(owned_changes),
            "foreign_changes": sorted(foreign),
            "batch": json.loads(batch.stdout),
        }

    sync_remote()
    paths = sorted(owned_changes)
    run(["git", "commit", "--only", "-m", f"data: publish A-share nightly final {trade_date}", "--", *paths])
    run(["git", "fetch", "origin", "main"])
    if not is_ancestor("origin/main", "HEAD"):
        raise RuntimeError("origin/main changed during nightly publication; retry after reconciliation")
    run(["git", "push", "origin", "HEAD:main"])
    commit = run(["git", "rev-parse", "--short", "HEAD"]).stdout.strip()
    state["status"] = "published"
    state["commit"] = commit
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"status": "published", "trade_date": trade_date, "commit": commit, "changed": paths}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, default=STATE)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--now", help="ISO timestamp for deterministic validation")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        assert ROOT.joinpath("scripts/validate_dashboard_batches.py").exists()
        print("publish_a_share_nightly self-test: OK")
        return 0
    now = datetime.fromisoformat(args.now).astimezone(CN) if args.now else None
    with publish_lock():
        result = publish(args.state, args.dry_run, now=now)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Atomically validate and publish one non-final A-share dashboard stage."""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

try:
    from a_share_nightly_contract import (
        BACKTEST_FILE,
        CATALOG_INPUT_FILES,
        GENERATED_PUBLIC_FILES,
        MID_MACRO_FILE,
        POOL_FILE,
        RECOMMENDATIONS_FILE,
        RESEARCH_AUDIT_FILE,
        nightly_lock,
    )
    from generate_research_audit import DEFAULT_TURNOVER, build_payload
    from publish_a_share_nightly import (
        BUILD_PYTHON,
        PROJECT_PYTHON,
        create_candidate_commit,
        git_changes,
        git_head,
        is_ancestor,
        project_subprocess_env,
        run,
        validate_candidate_commit,
    )
except ModuleNotFoundError:
    from scripts.a_share_nightly_contract import (
        BACKTEST_FILE,
        CATALOG_INPUT_FILES,
        GENERATED_PUBLIC_FILES,
        MID_MACRO_FILE,
        POOL_FILE,
        RECOMMENDATIONS_FILE,
        RESEARCH_AUDIT_FILE,
        nightly_lock,
    )
    from scripts.generate_research_audit import DEFAULT_TURNOVER, build_payload
    from scripts.publish_a_share_nightly import (
        BUILD_PYTHON,
        PROJECT_PYTHON,
        create_candidate_commit,
        git_changes,
        git_head,
        is_ancestor,
        project_subprocess_env,
        run,
        validate_candidate_commit,
    )

ROOT = Path(__file__).resolve().parents[1]
CN = ZoneInfo("Asia/Shanghai")
STAGE_LABELS = {
    "08:30": "08:30盘前版",
    "11:30": "11:30上午收盘修正版",
    "14:30": "14:30尾盘操作版",
}
A_SHARE_MANAGED = {
    RECOMMENDATIONS_FILE,
    POOL_FILE,
    MID_MACRO_FILE,
    RESEARCH_AUDIT_FILE,
    *GENERATED_PUBLIC_FILES,
}


def capture_managed_files() -> dict[str, bytes | None]:
    snapshots: dict[str, bytes | None] = {}
    for relative in A_SHARE_MANAGED:
        path = ROOT / relative
        snapshots[relative] = path.read_bytes() if path.exists() else None
    return snapshots


def restore_managed_files(snapshots: dict[str, bytes | None]) -> None:
    for relative, content in snapshots.items():
        path = ROOT / relative
        if content is None:
            path.unlink(missing_ok=True)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def article_path(trade_date: str) -> str:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", trade_date):
        raise RuntimeError(f"invalid A-share trade date: {trade_date!r}")
    return f"src/content/blog/{trade_date}.md"


def refresh_derived_artifacts(generated_at: str) -> None:
    env = project_subprocess_env()
    run([PROJECT_PYTHON, "scripts/generate_a_share_mid_macro.py"], env=env, timeout=300)
    pool = json.loads((ROOT / POOL_FILE).read_text(encoding="utf-8"))
    backtest = json.loads((ROOT / BACKTEST_FILE).read_text(encoding="utf-8"))
    audit = build_payload(backtest, pool, DEFAULT_TURNOVER, generated_at)
    atomic_write_json(ROOT / RESEARCH_AUDIT_FILE, audit)
    run([PROJECT_PYTHON, "scripts/enrich_garden_recommendations.py", "--validate"], env=env)
    run([PROJECT_PYTHON, "scripts/bootstrap_build_python.py"], env=env)
    run([BUILD_PYTHON, "scripts/generate_public_dashboard_payloads.py"], env=env)
    run([BUILD_PYTHON, "scripts/generate_data_catalog.py"], env=env)
    run([BUILD_PYTHON, "scripts/validate_public_data_contracts.py"], env=env)
    run([PROJECT_PYTHON, "scripts/validate_dashboard_batches.py"], env=env)


def validate_stage_inputs(stage_key: str) -> tuple[str, str]:
    expected_stage = STAGE_LABELS[stage_key]
    recommendations = json.loads((ROOT / RECOMMENDATIONS_FILE).read_text(encoding="utf-8"))
    trade_date = str(recommendations.get("date") or "")
    applies_to = str(recommendations.get("applies_to") or "")
    actual_stage = str(recommendations.get("stage") or "")
    if actual_stage != expected_stage:
        raise RuntimeError(f"A-share stage mismatch: expected={expected_stage!r}, actual={actual_stage!r}")
    if trade_date != applies_to:
        raise RuntimeError(f"A-share date/applies_to mismatch: date={trade_date!r}, applies_to={applies_to!r}")
    article = ROOT / article_path(trade_date)
    if not article.exists():
        raise RuntimeError(f"A-share article is missing: {article.relative_to(ROOT)}")
    article_text = article.read_text(encoding="utf-8")
    if expected_stage not in article_text:
        raise RuntimeError(f"A-share article does not declare stage {expected_stage!r}")
    return trade_date, expected_stage


def managed_paths(trade_date: str, changed: set[str]) -> tuple[list[str], list[str]]:
    allowed = set(A_SHARE_MANAGED) | {article_path(trade_date)}
    dirty_catalog_inputs = (changed & set(CATALOG_INPUT_FILES)) - allowed
    if dirty_catalog_inputs:
        raise RuntimeError(f"catalog inputs contain unrelated changes: {sorted(dirty_catalog_inputs)}")
    owned = sorted(changed & allowed)
    foreign = sorted(changed - allowed)
    return owned, foreign


def _publish_stage(stage_key: str, message: str | None = None, dry_run: bool = False) -> dict[str, Any]:
    if stage_key not in STAGE_LABELS:
        raise RuntimeError(f"unsupported A-share stage: {stage_key!r}")
    branch = run(["git", "branch", "--show-current"]).stdout.strip()
    if branch != "main":
        raise RuntimeError(f"A-share stage publisher requires main, got {branch!r}")
    if run(["git", "diff", "--cached", "--quiet"], check=False).returncode != 0:
        raise RuntimeError("A-share stage publisher requires a clean git index")
    run(["git", "fetch", "origin", "main"])
    base = git_head()
    remote = run(["git", "rev-parse", "origin/main"]).stdout.strip()
    if base != remote:
        raise RuntimeError(f"main must equal origin/main before publication: local={base[:12]} remote={remote[:12]}")

    trade_date, expected_stage = validate_stage_inputs(stage_key)
    current = datetime.now(CN).isoformat()
    refresh_derived_artifacts(current)
    trade_date_after, stage_after = validate_stage_inputs(stage_key)
    if (trade_date_after, stage_after) != (trade_date, expected_stage):
        raise RuntimeError("A-share publication identity changed while deriving artifacts")

    changed = git_changes()
    owned, foreign = managed_paths(trade_date, changed)
    required_closure = {RECOMMENDATIONS_FILE, POOL_FILE, MID_MACRO_FILE, RESEARCH_AUDIT_FILE}
    missing = sorted(path for path in required_closure if path not in owned and run(
        ["git", "diff", "--quiet", "HEAD", "--", path], check=False,
    ).returncode != 0)
    if missing:
        raise RuntimeError(f"A-share publication closure is incomplete: {missing}")
    if not owned:
        return {"status": "idempotent", "trade_date": trade_date, "stage": expected_stage, "changed": []}

    commit_message = message or f"data: publish A-share {stage_key} stage {trade_date}"
    candidate, tree = create_candidate_commit(owned, commit_message)
    validate_candidate_commit(candidate)
    _, current_tree = create_candidate_commit(owned, commit_message)
    if current_tree != tree:
        raise RuntimeError("A-share managed files changed during candidate validation")
    if dry_run:
        return {
            "status": "validated",
            "trade_date": trade_date,
            "stage": expected_stage,
            "candidate": candidate,
            "changed": owned,
            "foreign_changes": foreign,
        }

    if git_head() != base:
        raise RuntimeError("main changed before attaching validated A-share candidate")
    run(["git", "update-ref", "refs/heads/main", candidate, base])
    run(["git", "reset", "--mixed", candidate])
    run(["git", "fetch", "origin", "main"])
    remote = run(["git", "rev-parse", "origin/main"]).stdout.strip()
    if remote != base or not is_ancestor(remote, candidate):
        raise RuntimeError("origin/main changed during A-share stage publication")
    run(["git", "push", "origin", f"{candidate}:main"])
    return {
        "status": "published",
        "trade_date": trade_date,
        "stage": expected_stage,
        "commit": candidate[:7],
        "changed": owned,
        "foreign_changes": foreign,
    }


def publish_stage(stage_key: str, message: str | None = None, dry_run: bool = False) -> dict[str, Any]:
    snapshots = capture_managed_files() if dry_run else None
    try:
        return _publish_stage(stage_key, message, dry_run)
    finally:
        if snapshots is not None:
            restore_managed_files(snapshots)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", required=True, choices=sorted(STAGE_LABELS))
    parser.add_argument("--message")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    with nightly_lock():
        result = publish_stage(args.stage, args.message, args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

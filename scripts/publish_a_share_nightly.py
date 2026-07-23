#!/usr/bin/env python3
"""Validate, build, commit, and push a prepared A-share nightly content batch."""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

try:
    from a_share_nightly_contract import (
        CATALOG_INPUT_FILES, GENERATED_PUBLIC_FILES, PUBLIC_VERIFY_FILES, SNAPSHOT_FILES,
        STATE, file_hashes, nightly_content_files, nightly_lock,
    )
except ModuleNotFoundError:
    from scripts.a_share_nightly_contract import (
        CATALOG_INPUT_FILES, GENERATED_PUBLIC_FILES, PUBLIC_VERIFY_FILES, SNAPSHOT_FILES,
        STATE, file_hashes, nightly_content_files, nightly_lock,
    )

ROOT = Path(__file__).resolve().parents[1]
CN = ZoneInfo("Asia/Shanghai")
ALLOWED_STATIC = set(SNAPSHOT_FILES) | set(GENERATED_PUBLIC_FILES)
PROJECT_PYTHON = "/usr/bin/python3"
BUILD_PYTHON = ".build-venv/bin/python"


def project_subprocess_env() -> dict[str, str]:
    """Keep project commands out of the Hermes gateway virtualenv."""
    env = dict(os.environ)
    env.pop("VIRTUAL_ENV", None)
    env["PATH"] = "/usr/bin:" + env.get("PATH", "")
    return env


def run(
    command: list[str], check: bool = True, *, cwd: Path | None = None,
    env: dict[str, str] | None = None, timeout: int = 600,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command, cwd=cwd or ROOT, env=env, text=True, capture_output=True,
        check=False, timeout=timeout,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "no command output")[-4000:]
        raise RuntimeError(f"command failed rc={result.returncode}: {command}\n{detail}")
    return result


FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
PUBLISHABLE_STATUSES = {"prepared", "candidate_validated", "committed", "deploy_failed", "published"}


def validate_manifest(payload: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["manifest must be an object"]
    status = payload.get("status")
    trade_date = payload.get("trade_date")
    if payload.get("version") != 2:
        errors.append("manifest version must be 2")
    if status not in PUBLISHABLE_STATUSES:
        errors.append("manifest status is not publishable")
    if payload.get("phase") != status:
        errors.append("manifest phase must equal status")
    if not isinstance(trade_date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", trade_date):
        errors.append("manifest trade_date is invalid")
        trade_date = ""
    if not isinstance(payload.get("generation_id"), str) or not payload.get("generation_id"):
        errors.append("manifest generation_id is required")
    if not FULL_SHA_RE.fullmatch(str(payload.get("base_commit") or "")):
        errors.append("manifest base_commit must be a full commit SHA")
    expected_content = set(nightly_content_files(trade_date)) if trade_date else set()
    expected_snapshots = set(SNAPSHOT_FILES)
    content = payload.get("content_files")
    snapshots = payload.get("snapshot_files")
    if not isinstance(content, list) or set(content) != expected_content or len(content) != len(expected_content):
        errors.append("manifest content_files differ from the fixed nightly contract")
    if not isinstance(snapshots, list) or set(snapshots) != expected_snapshots or len(snapshots) != len(expected_snapshots):
        errors.append("manifest snapshot_files differ from the fixed nightly contract")
    hashes = payload.get("snapshot_hashes")
    if not isinstance(hashes, dict) or set(hashes) != expected_snapshots:
        errors.append("manifest snapshot_hashes must cover the exact snapshot contract")
    elif any(not SHA256_RE.fullmatch(str(value)) for value in hashes.values()):
        errors.append("manifest snapshot_hashes must contain SHA-256 values")
    if payload.get("expected_stage") != "22:00夜间最终版":
        errors.append("manifest expected_stage is invalid")
    if status in {"candidate_validated", "committed", "deploy_failed", "published"}:
        if not SHA256_RE.fullmatch(str(payload.get("dataset_fingerprint") or "")):
            errors.append("manifest dataset_fingerprint must be SHA-256")
        if not FULL_SHA_RE.fullmatch(str(payload.get("commit") or "")):
            errors.append("manifest commit must be a full commit SHA")
        if not FULL_SHA_RE.fullmatch(str(payload.get("candidate_tree") or "")):
            errors.append("manifest candidate_tree must be a full tree SHA")
        public_hashes = payload.get("public_hashes")
        if not isinstance(public_hashes, dict) or set(public_hashes) != set(PUBLIC_VERIFY_FILES):
            errors.append("manifest public_hashes must cover all public verification files")
        elif any(not SHA256_RE.fullmatch(str(value)) for value in public_hashes.values()):
            errors.append("manifest public_hashes must contain SHA-256 values")
    return errors


def load_state(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    errors = validate_manifest(payload)
    if errors:
        raise RuntimeError("invalid nightly manifest: " + "; ".join(errors))
    return payload


def atomic_write_state(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", text=True,
    )
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


def git_changes() -> set[str]:
    result = run(["git", "status", "--porcelain", "--untracked-files=normal"])
    return {line[3:].strip() for line in result.stdout.splitlines() if len(line) >= 4}


def is_ancestor(left: str, right: str) -> bool:
    return run(["git", "merge-base", "--is-ancestor", left, right], check=False).returncode == 0


def sync_remote(expected_local_commit: str | None = None) -> None:
    branch = run(["git", "branch", "--show-current"]).stdout.strip()
    if branch != "main":
        raise RuntimeError(f"nightly publisher requires main branch, got {branch!r}")
    if run(["git", "diff", "--cached", "--quiet"], check=False).returncode != 0:
        raise RuntimeError("nightly publisher requires a clean git index")
    run(["git", "fetch", "origin", "main"])
    if is_ancestor("origin/main", "HEAD"):
        ahead = run(["git", "rev-list", "--count", "origin/main..HEAD"]).stdout.strip()
        if ahead != "0" and git_head() != expected_local_commit:
            raise RuntimeError("local main has an undeclared unpushed commit")
    elif is_ancestor("HEAD", "origin/main"):
        run(["git", "merge", "--ff-only", "origin/main"])
    else:
        raise RuntimeError("main and origin/main diverged; manual reconciliation required")


def git_head() -> str:
    return run(["git", "rev-parse", "HEAD"]).stdout.strip()


def candidate_file_hashes(commit: str) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for relative in PUBLIC_VERIFY_FILES:
        result = subprocess.run(
            ["git", "show", f"{commit}:{relative}"], cwd=ROOT,
            capture_output=True, check=True, timeout=30,
        )
        hashes[relative] = hashlib.sha256(result.stdout).hexdigest()
    return hashes


def verify_candidate_identity(state: dict[str, Any]) -> None:
    commit = str(state.get("commit") or "")
    expected_parent = str(state.get("base_commit") or "")
    expected_tree = str(state.get("candidate_tree") or "")
    resolved = run(["git", "rev-parse", f"{commit}^{{commit}}"]).stdout.strip()
    parent = run(["git", "rev-parse", f"{commit}^"]).stdout.strip()
    tree = run(["git", "rev-parse", f"{commit}^{{tree}}"]).stdout.strip()
    if resolved != commit or parent != expected_parent or tree != expected_tree:
        raise RuntimeError("candidate commit identity differs from the validated manifest")
    if candidate_file_hashes(commit) != state.get("public_hashes"):
        raise RuntimeError("candidate public file hashes differ from the validated manifest")


def verify_snapshot_hashes(state: dict[str, Any]) -> None:
    expected = state.get("snapshot_hashes")
    if not isinstance(expected, dict) or set(expected) != set(SNAPSHOT_FILES):
        raise RuntimeError("nightly snapshot hashes are missing or incomplete")
    if any(not SHA256_RE.fullmatch(str(value)) for value in expected.values()):
        raise RuntimeError("nightly snapshot hashes contain invalid SHA-256 values")
    actual = file_hashes(ROOT, SNAPSHOT_FILES)
    if actual != expected:
        changed = sorted(set(actual) | set(expected))
        changed = [path for path in changed if actual.get(path) != expected.get(path)]
        raise RuntimeError(f"nightly snapshot changed after prepare: {changed}")


def create_candidate_commit(paths: list[str], message: str) -> tuple[str, str]:
    fd, index_path = tempfile.mkstemp(prefix="a-share-nightly-index-")
    os.close(fd)
    os.unlink(index_path)
    env = dict(os.environ)
    env["GIT_INDEX_FILE"] = index_path
    try:
        run(["git", "read-tree", "HEAD"], env=env)
        run(["git", "add", "--", *paths], env=env)
        tree = run(["git", "write-tree"], env=env).stdout.strip()
        commit = run(
            ["git", "commit-tree", tree, "-p", "HEAD", "-m", message], env=env,
        ).stdout.strip()
        return commit, tree
    finally:
        if os.path.exists(index_path):
            os.unlink(index_path)


def validate_candidate_commit(commit: str) -> None:
    candidate_dir = Path(tempfile.mkdtemp(prefix="a-share-nightly-candidate-"))
    env = project_subprocess_env()
    try:
        run(["git", "worktree", "add", "--detach", str(candidate_dir), commit])
        run(["npm", "ci"], cwd=candidate_dir, env=env, timeout=600)
        run([PROJECT_PYTHON, "-m", "pytest", "-q"], cwd=candidate_dir, env=env, timeout=300)
        run([PROJECT_PYTHON, "scripts/validate_dashboard_batches.py"], cwd=candidate_dir, env=env, timeout=180)
        run(["npm", "run", "build"], cwd=candidate_dir, env=env, timeout=600)
        generated_diff = run(
            ["git", "diff", "--exit-code", "--", *GENERATED_PUBLIC_FILES],
            cwd=candidate_dir, env=env, check=False, timeout=60,
        )
        if generated_diff.returncode != 0:
            raise RuntimeError("candidate build rewrote managed public artifacts")
    finally:
        run(
            ["git", "worktree", "remove", "--force", str(candidate_dir)],
            check=False, timeout=120,
        )
        if candidate_dir.exists():
            import shutil
            shutil.rmtree(candidate_dir, ignore_errors=True)


def verify_production(
    trade_date: str,
    expected_fingerprint: str,
    generation_id: str,
    expected_public_hashes: dict[str, str],
    attempts: int = 24,
) -> None:
    if set(expected_public_hashes) != set(PUBLIC_VERIFY_FILES):
        raise RuntimeError("production verification hashes are incomplete")
    base = os.environ.get("ETF_PUBLIC_BASE_URL", "https://etf.peekabo.cc").rstrip("/")
    headers = {"User-Agent": "Mozilla/5.0 Chrome/126 Safari/537.36"}
    error = "deployment has not converged"
    for attempt in range(attempts):
        try:
            stamp = int(time.time())
            def fetch_public(relative: str) -> tuple[str, bytes]:
                url_path = relative.removeprefix("public/")
                request = urllib.request.Request(f"{base}/{url_path}?deploy={stamp}", headers=headers)
                with urllib.request.urlopen(request, timeout=20) as response:
                    content_type = response.headers.get_content_type()
                    if relative.endswith(".json") and content_type not in {"application/json", "text/json"}:
                        raise RuntimeError(f"unexpected content type for {relative}: {content_type}")
                    return relative, response.read()

            with concurrent.futures.ThreadPoolExecutor(max_workers=len(PUBLIC_VERIFY_FILES)) as pool:
                bodies = dict(pool.map(fetch_public, PUBLIC_VERIFY_FILES))
            actual_hashes = {
                relative: hashlib.sha256(body).hexdigest()
                for relative, body in bodies.items()
            }
            audit = json.loads(bodies["public/data/model-lab/a-share-research-audit.json"])
            recommendations = json.loads(bodies["public/data/garden-recommendations.json"])
            marker = json.loads(bodies["public/data/a-share-nightly-deployment.json"])
            lab_request = urllib.request.Request(f"{base}/lab/?deploy={stamp}", headers=headers)
            with urllib.request.urlopen(lab_request, timeout=20) as response:
                if response.headers.get_content_type() != "text/html":
                    raise RuntimeError("unexpected content type for /lab/")
                security_headers = {
                    "content-security-policy": response.headers.get("Content-Security-Policy", ""),
                    "strict-transport-security": response.headers.get("Strict-Transport-Security", ""),
                    "x-content-type-options": response.headers.get("X-Content-Type-Options", ""),
                    "x-frame-options": response.headers.get("X-Frame-Options", ""),
                }
                if (
                    "default-src 'self'" not in security_headers["content-security-policy"]
                    or "max-age=" not in security_headers["strict-transport-security"]
                    or security_headers["x-content-type-options"].lower() != "nosniff"
                    or security_headers["x-frame-options"].upper() != "DENY"
                ):
                    raise RuntimeError("production security headers are incomplete")
                lab_html = response.read().decode("utf-8")
            if (
                actual_hashes == expected_public_hashes
                and audit.get("dataset", {}).get("value") == expected_fingerprint
                and recommendations.get("date") == trade_date
                and recommendations.get("stage") == "22:00夜间最终版"
                and marker.get("generation_id") == generation_id
                and marker.get("trade_date") == trade_date
                and "研究审计台" in lab_html
            ):
                return
            error = "production content hashes, generation marker, or batch identity differ from candidate"
        except Exception as exc:  # deployment polling must retain the latest real error
            error = f"{type(exc).__name__}: {exc}"
        if attempt + 1 < attempts:
            time.sleep(15)
    raise RuntimeError(f"Cloudflare deployment verification failed: {error}")


def ensure_pub_date(article: Path, trade_date: str, *, write: bool = True) -> None:
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
    if not write:
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

    status = state.get("status")
    if status == "published":
        return {"status": "idempotent", "trade_date": trade_date, "changed": []}
    recovery_statuses = {"candidate_validated", "committed", "deploy_failed"}
    if status in recovery_statuses:
        verify_candidate_identity(state)
        if dry_run:
            target = str(state["commit"])
            remote = run(["git", "rev-parse", "origin/main"], check=False).stdout.strip()
            return {
                "status": "validated_recovery",
                "trade_date": trade_date,
                "manifest_status": status,
                "commit": target[:7],
                "would_attach": status == "candidate_validated" and git_head() == state["base_commit"],
                "would_push": remote != target,
                "would_verify_deployment": True,
            }
    if not dry_run:
        expected_local = str(state.get("commit") or "") if status in recovery_statuses else None
        sync_remote(expected_local_commit=expected_local)
        if status in recovery_statuses:
            verify_candidate_identity(state)

    if status == "candidate_validated":
        candidate = str(state.get("commit") or "")
        base_commit = str(state.get("base_commit") or "")
        head = git_head()
        if head == base_commit:
            run(["git", "update-ref", "refs/heads/main", candidate, base_commit])
        elif head != candidate:
            raise RuntimeError("candidate commit can no longer be attached to main")
        run(["git", "reset", "--mixed", candidate])
        state["status"] = "committed"
        state["phase"] = "committed"
        atomic_write_state(state_path, state)
        status = "committed"

    if status in {"committed", "deploy_failed"}:
        target = str(state.get("commit") or "")
        if not target or git_head() != target:
            raise RuntimeError("publication manifest commit differs from local main")
        run(["git", "fetch", "origin", "main"])
        remote = run(["git", "rev-parse", "origin/main"]).stdout.strip()
        if remote != target:
            if not is_ancestor("origin/main", target):
                raise RuntimeError("remote main is incompatible with committed nightly candidate")
            run(["git", "push", "origin", f"{target}:main"])
        fingerprint = str(state["dataset_fingerprint"])
        try:
            verify_production(
                trade_date,
                fingerprint,
                str(state["generation_id"]),
                dict(state["public_hashes"]),
            )
        except Exception:
            state["status"] = "deploy_failed"
            state["phase"] = "deploy_failed"
            atomic_write_state(state_path, state)
            raise
        state["status"] = "published"
        state["phase"] = "published"
        state["published_at"] = current.isoformat()
        atomic_write_state(state_path, state)
        return {"status": "published", "trade_date": trade_date, "commit": target[:7], "changed": state.get("changed") or []}

    base_commit = str(state.get("base_commit") or "")
    if base_commit and base_commit != git_head():
        raise RuntimeError(
            f"code changed after prepare: base={base_commit[:12]} current={git_head()[:12]}; rerun prepare"
        )
    verify_snapshot_hashes(state)

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
    ensure_pub_date(article, trade_date, write=not dry_run)
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

    expected_content = set(nightly_content_files(trade_date))
    expected_snapshots = set(SNAPSHOT_FILES)
    allowed = expected_content | expected_snapshots | set(GENERATED_PUBLIC_FILES)
    env = project_subprocess_env()
    run([PROJECT_PYTHON, "scripts/enrich_garden_recommendations.py", "--validate"], env=env)
    batch = run([PROJECT_PYTHON, "scripts/validate_dashboard_batches.py"], env=env)
    run([PROJECT_PYTHON, "scripts/bootstrap_build_python.py"], env=env)
    run([BUILD_PYTHON, "scripts/generate_public_dashboard_payloads.py"], env=env)
    run([BUILD_PYTHON, "scripts/generate_data_catalog.py"], env=env)
    run([BUILD_PYTHON, "scripts/validate_public_data_contracts.py"], env=env)
    changed = git_changes()
    dirty_catalog_inputs = (changed & set(CATALOG_INPUT_FILES)) - allowed
    if dirty_catalog_inputs:
        raise RuntimeError(
            f"catalog input files contain foreign changes: {sorted(dirty_catalog_inputs)}"
        )
    owned_changes = changed & allowed
    if not owned_changes:
        return {"status": "idempotent", "trade_date": trade_date, "changed": []}
    foreign = changed - allowed
    paths = sorted(owned_changes)
    message = f"data: publish A-share nightly final {trade_date}"
    candidate, candidate_tree = create_candidate_commit(paths, message)
    validate_candidate_commit(candidate)
    verify_snapshot_hashes(state)
    _, current_tree = create_candidate_commit(paths, message)
    if current_tree != candidate_tree:
        raise RuntimeError("nightly owned files changed during candidate validation")
    public_hashes = candidate_file_hashes(candidate)
    audit_payload = json.loads(
        (ROOT / "public/data/model-lab/a-share-research-audit.json").read_text(encoding="utf-8")
    )
    dataset_fingerprint = str((audit_payload.get("dataset") or {}).get("value") or "")
    if not SHA256_RE.fullmatch(dataset_fingerprint):
        raise RuntimeError("research audit dataset fingerprint is invalid")
    if dry_run:
        return {
            "status": "validated",
            "trade_date": trade_date,
            "changed": paths,
            "foreign_changes": sorted(foreign),
            "candidate_commit": candidate,
            "candidate_tree": candidate_tree,
            "public_hashes": public_hashes,
            "batch": json.loads(batch.stdout),
        }

    state["status"] = "candidate_validated"
    state["phase"] = "candidate_validated"
    state["commit"] = candidate
    state["candidate_tree"] = candidate_tree
    state["public_hashes"] = public_hashes
    state["dataset_fingerprint"] = dataset_fingerprint
    state["changed"] = paths
    atomic_write_state(state_path, state)
    if git_head() != base_commit:
        raise RuntimeError("main changed before attaching validated candidate")
    run(["git", "update-ref", "refs/heads/main", candidate, base_commit])
    run(["git", "reset", "--mixed", candidate])
    verify_candidate_identity(state)
    state["status"] = "committed"
    state["phase"] = "committed"
    atomic_write_state(state_path, state)
    run(["git", "fetch", "origin", "main"])
    if run(["git", "rev-parse", "origin/main"]).stdout.strip() != base_commit:
        raise RuntimeError("origin/main changed during nightly publication")
    run(["git", "push", "origin", f"{candidate}:main"])
    try:
        verify_production(
            trade_date,
            dataset_fingerprint,
            str(state["generation_id"]),
            public_hashes,
        )
    except Exception:
        state["status"] = "deploy_failed"
        state["phase"] = "deploy_failed"
        atomic_write_state(state_path, state)
        raise
    state["status"] = "published"
    state["phase"] = "published"
    state["published_at"] = current.isoformat()
    atomic_write_state(state_path, state)
    return {"status": "published", "trade_date": trade_date, "commit": candidate[:7], "changed": paths}


def format_receipt(result: dict[str, Any]) -> str:
    trade_date = result.get("trade_date", "待确认")
    status = result.get("status")
    if status == "published":
        return f"✅ A股ETF罗盘夜间最终版已发布\n交易日：{trade_date}｜提交：{result.get('commit', '—')}"
    if status == "idempotent":
        return f"✅ A股ETF罗盘夜间最终版已是最新\n交易日：{trade_date}｜无需重复发布"
    if status == "validated":
        return f"✅ A股ETF罗盘夜间批次校验通过\n交易日：{trade_date}｜尚未执行发布"
    return f"A股ETF罗盘夜间任务完成｜交易日：{trade_date}｜状态：{status or 'unknown'}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, default=STATE)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--now", help="ISO timestamp for deterministic validation")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        assert ROOT.joinpath("scripts/validate_dashboard_batches.py").exists()
        assert "夜间最终版已发布" in format_receipt({"status": "published", "trade_date": "2026-07-17", "commit": "abc1234"})
        assert "无需重复发布" in format_receipt({"status": "idempotent", "trade_date": "2026-07-17"})
        print("publish_a_share_nightly self-test: OK")
        return 0
    now = datetime.fromisoformat(args.now).astimezone(CN) if args.now else None
    with nightly_lock():
        result = publish(args.state, args.dry_run, now=now)
    print(format_receipt(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

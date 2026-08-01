#!/usr/bin/env python3
"""Update and deploy US ETF Compass when Yahoo exposes a new completed trade date.

Auto-publish only after the US cash session is closed for that trade date.
Intraday incomplete bars may regenerate locally, but they do not replace the
published close edition.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
from pages_release import release_pages
try:
    from a_share_nightly_contract import paper_publish_lock
except ModuleNotFoundError:
    from scripts.a_share_nightly_contract import paper_publish_lock
POOL = REPO / "public/data/us-etf-pool.json"
GARDEN = REPO / "public/data/us-etf-garden.json"
STATE = Path("/root/.hermes/state/us-etf-close-publisher.json")
NY = ZoneInfo("America/New_York")
FILES = [
    "public/data/us-etf-pool.json",
    "public/data/us-etf-garden.json",
    "public/data/us-etf-backtest.json",
    "public/data/us-etf-flower-history.json",
    "public/data/us-macro-dashboard.json",
    "public/data/us-compass-learning.json",
    "public/data/us-compass-shadow.json",
    "public/data/paper-trading.json",
    "public/data/catalog.json",
]
US_OWNED_FILES = [path for path in FILES if path != "public/data/catalog.json"]
OWNED_COMMIT_PREFIXES = (
    "data: update US ETF Compass for ",
    "data: recover US ETF Compass close for ",
)


def write_state(phase: str, **fields: object) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    current: dict[str, object] = {}
    if STATE.exists():
        try:
            current = json.loads(STATE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            current = {}
    current.update({"phase": phase, "updated_at": datetime.now(timezone.utc).isoformat(), **fields})
    temporary = STATE.with_suffix(".tmp")
    temporary.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, STATE)


def is_owned_commit_subject(subject: str) -> bool:
    return subject.startswith(OWNED_COMMIT_PREFIXES)


def decide_action(*, old: str | None, latest: str, state: str, recovery_dirty: bool,
                  garden_date: str, garden_stage: str, garden_session: str) -> str:
    if recovery_dirty and garden_date == old and garden_stage == "美股收盘版" and garden_session == "closed":
        return "recover"
    if latest == old:
        return "noop"
    if state != "closed":
        return "wait"
    return "publish"


def run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, cwd=REPO, text=True, capture_output=True)
    if check and result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or "no command output"
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(args)}\n{detail}")
    return result


def is_ancestor(left: str, right: str) -> bool:
    return run("git", "merge-base", "--is-ancestor", left, right, check=False).returncode == 0


def sync_remote() -> None:
    """Publish a stranded Compass commit and start from the current remote main."""
    branch = run("git", "branch", "--show-current").stdout.strip()
    if branch != "main":
        raise RuntimeError(f"US ETF publisher requires main branch, got {branch!r}")
    if run("git", "diff", "--cached", "--quiet", check=False).returncode != 0:
        raise RuntimeError("US ETF publisher requires a clean git index")

    run("git", "fetch", "origin", "main")
    if is_ancestor("origin/main", "HEAD"):
        if run("git", "rev-list", "--count", "origin/main..HEAD").stdout.strip() != "0":
            run("git", "push", "origin", "HEAD:main")
        return
    if is_ancestor("HEAD", "origin/main"):
        run("git", "merge", "--ff-only", "origin/main")
        return

    # A prior run can commit while another publisher advances origin/main.
    # Rebase only when every local-only commit belongs to this publisher.
    subjects = run("git", "log", "--format=%s", "origin/main..HEAD").stdout.splitlines()
    if not subjects or any(not is_owned_commit_subject(subject) for subject in subjects):
        raise RuntimeError(f"main and origin/main diverged with unrelated local commits: {subjects}")
    run("git", "rebase", "--autostash", "origin/main")
    run("git", "push", "origin", "HEAD:main")


def push_compass_commit() -> None:
    """Push after reconciling a remote commit that landed during generation."""
    run("git", "fetch", "origin", "main")
    if not is_ancestor("origin/main", "HEAD"):
        subjects = run("git", "log", "--format=%s", "origin/main..HEAD").stdout.splitlines()
        if not subjects or any(not is_owned_commit_subject(subject) for subject in subjects):
            raise RuntimeError(f"origin/main changed and local commits are unrelated: {subjects}")
        run("git", "rebase", "--autostash", "origin/main")
    run("git", "push", "origin", "HEAD:main")


def latest_spy_date() -> str:
    url = "https://query1.finance.yahoo.com/v8/finance/chart/SPY?range=5d&interval=1d"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 ETF-Compass/1.0"})
    with urllib.request.urlopen(req, timeout=20) as response:
        payload = json.load(response)["chart"]["result"][0]
    return datetime.fromtimestamp(payload["timestamp"][-1], timezone.utc).astimezone(NY).date().isoformat()


def session_state(now: datetime, trade_date: str) -> str:
    model_day = datetime.fromisoformat(trade_date).date()
    today = now.date()
    minutes = now.hour * 60 + now.minute
    if model_day < today:
        return "closed"
    if model_day > today:
        return "preopen"
    if minutes < 9 * 60 + 30:
        return "preopen"
    if minutes >= 16 * 60 + 5:
        return "closed"
    return "open"


def main() -> None:
    sync_remote()
    now = datetime.now(NY)
    old = json.loads(POOL.read_text(encoding="utf-8")).get("model_date") if POOL.exists() else None
    latest = latest_spy_date()
    state = session_state(now, latest)
    garden = json.loads(GARDEN.read_text(encoding="utf-8")) if GARDEN.exists() else {}

    # Recover a completed prior close edition before evaluating the newer intraday bar.
    # Otherwise Yahoo exposes today's open session and the script exits early forever,
    # leaving yesterday's valid US catalog inputs dirty and blocking A-share publication.
    dirty = run("git", "status", "--porcelain", "--", *US_OWNED_FILES).stdout.strip()
    garden_date = str(garden.get("date") or "")
    action = decide_action(
        old=old,
        latest=latest,
        state=state,
        recovery_dirty=bool(dirty),
        garden_date=garden_date,
        garden_stage=str(garden.get("stage") or ""),
        garden_session=str(garden.get("session_state") or ""),
    )
    write_state("evaluated", old_model_date=old, latest_trade_date=latest, session_state=state, action=action)
    if action == "recover":
        write_state("validating_recovery", trade_date=old)
        run("python3", "scripts/paper_trade_runner.py", "--mode", "sync-public")
        run("python3", "scripts/validate_dashboard_batches.py")
        run("python3", "scripts/validate_public_data_contracts.py")
        # Commit only US-owned snapshots here. The waiting A-share nightly publisher
        # regenerates and commits catalog.json with both US and A final hashes.
        run("git", "add", *US_OWNED_FILES)
        run("git", "commit", "-m", f"data: recover US ETF Compass close for {old}")
        push_compass_commit()
        commit = run("git", "rev-parse", "HEAD").stdout.strip()
        write_state("committed", trade_date=old, commit=commit)
        # Deploy the recovered close edition as well; commit success alone does not
        # advance etf.peekabo.cc because production uses direct Wrangler Pages deploys.
        run("npm", "run", "build")
        release_pages([
            "https://etf.peekabo.cc/us-compass/",
            "https://etf.peekabo.cc/us-momentum/",
            "https://etf.peekabo.cc/us-compass/history/",
            "https://etf.peekabo.cc/data/us-etf-garden.json",
            "https://etf.peekabo.cc/data/us-etf-pool.json",
        ], {
            "https://etf.peekabo.cc/data/us-etf-garden.json": GARDEN,
            "https://etf.peekabo.cc/data/us-etf-pool.json": POOL,
        })
        write_state("published", trade_date=old, commit=commit, deployed=True, verified=True)
        print(f"🇺🇸 美股ETF罗盘遗留收盘版已恢复提交并部署至 {old}；catalog 交由后续原子发布器收敛。")
        raise SystemExit(0)

    if action == "noop":
        write_state("idempotent", trade_date=old, verified=True)
        raise SystemExit(0)

    # Incomplete session bars must not overwrite a published close edition.
    if action == "wait":
        write_state("waiting_for_close", trade_date=latest, old_model_date=old, session_state=state)
        print(
            f"skip publish: trade_date={latest} session_state={state} "
            f"old_model_date={old} (wait for US cash close 16:05 ET)"
        )
        raise SystemExit(0)

    write_state("generating", trade_date=latest)
    # Refresh local Yahoo daily-bar cache before generating the close edition.
    run("python3", "scripts/update_us_etf_bar_cache.py", "--range", "3mo", "--workers", "8", "--mark-final")
    run("python3", "scripts/generate_us_etf_garden.py")
    run("python3", "scripts/update_us_compass_learning.py")
    pool = json.loads(POOL.read_text(encoding="utf-8"))
    garden = json.loads(GARDEN.read_text(encoding="utf-8"))
    new = pool.get("model_date")
    if new != latest:
        raise RuntimeError(f"trade date mismatch latest={latest} new={new}")
    if garden.get("session_state") != "closed" or garden.get("stage") != "美股收盘版":
        raise RuntimeError(
            f"refusing to publish non-close snapshot stage={garden.get('stage')} "
            f"session_state={garden.get('session_state')}"
        )

    write_state("validated", trade_date=new)
    run("python3", "scripts/paper_trade_runner.py", "--mode", "sync-public")
    run("npm", "run", "build")
    run("git", "add", *FILES)
    # Commit only when the close edition actually changed.
    status = subprocess.run(["git", "status", "--porcelain", "--", *FILES], cwd=REPO, text=True, capture_output=True, check=True)
    if status.stdout.strip():
        run("git", "commit", "-m", f"data: update US ETF Compass for {new}")
        push_compass_commit()
    commit = run("git", "rev-parse", "HEAD").stdout.strip()
    write_state("committed", trade_date=new, commit=commit)
    release_pages([
        "https://etf.peekabo.cc/us-compass/",
        "https://etf.peekabo.cc/us-momentum/",
        "https://etf.peekabo.cc/us-macro/",
        "https://etf.peekabo.cc/us-compass/history/",
        "https://etf.peekabo.cc/data/us-etf-garden.json",
        "https://etf.peekabo.cc/data/us-etf-pool.json",
        "https://etf.peekabo.cc/data/us-macro-dashboard.json",
    ], {
        "https://etf.peekabo.cc/data/us-etf-garden.json": GARDEN,
        "https://etf.peekabo.cc/data/us-etf-pool.json": POOL,
        "https://etf.peekabo.cc/data/us-macro-dashboard.json": REPO / "public/data/us-macro-dashboard.json",
    })
    learning = json.loads((REPO / "public/data/us-compass-learning.json").read_text(encoding="utf-8"))
    snapshots = len(learning.get("snapshots", []))
    write_state("published", trade_date=new, commit=commit, deployed=True, verified=True, learning_snapshots=snapshots)
    print(
        f"🇺🇸 美股ETF罗盘已更新至 {new}：74池、趋势风控、动作触发和学习快照均已构建并推送；"
        f"前向学习样本 {snapshots} 个。"
    )


if __name__ == "__main__":
    try:
        with paper_publish_lock():
            main()
    except Exception as error:
        write_state("error", error=str(error))
        raise

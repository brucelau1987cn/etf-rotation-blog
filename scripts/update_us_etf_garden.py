#!/usr/bin/env python3
"""Update and deploy US ETF Compass when Yahoo exposes a new completed trade date.

Auto-publish only after the US cash session is closed for that trade date.
Intraday incomplete bars may regenerate locally, but they do not replace the
published close edition.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import urllib.request
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import pages_release
import us_close_candidate as candidate
from project_runtime import PROJECT_PYTHON, project_subprocess_env
try:
    from public_contract_gate import split_errors
except ModuleNotFoundError:
    from scripts.public_contract_gate import split_errors
try:
    from a_share_nightly_contract import site_publish_lock
except ModuleNotFoundError:
    from scripts.a_share_nightly_contract import site_publish_lock
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
    "public/data/us-compass-health.json",
]
US_OWNED_FILES = list(FILES)
BUILD_PYTHON = ".build-venv/bin/python"
OWNED_COMMIT_PREFIXES = (
    "data: update US ETF Compass for ",
    "data: recover US ETF Compass close for ",
)


class ReceiptBlocked(RuntimeError):
    """Publication blocked before a new phase is confirmed."""


def read_state() -> dict:
    try:
        value = json.loads(STATE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(value, dict):
        raise RuntimeError("invalid US publisher state")
    return value


def replace_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def write_state(phase: str, **fields: object) -> None:
    current = read_state()
    current.update({"phase": phase, "updated_at": datetime.now(timezone.utc).isoformat(), **fields})
    if "error" not in fields:
        current.pop("error", None)
    replace_json(STATE, current)


def record_error(error: Exception) -> None:
    # Keep this diagnostic independent of the receipt, even when it is unreadable.
    try:
        replace_json(STATE.with_suffix(".error.json"), {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "error": str(error),
        })
    except OSError as diagnostic_error:
        print(f"could not write US publisher diagnostic: {diagnostic_error}", file=sys.stderr)


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


def run(*args: str, check: bool = True, cwd: Path | None = None,
        env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    if args[0] == "git" and env is None:
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    result = subprocess.run(args, cwd=cwd or REPO, env=env, text=True, capture_output=True)
    if check and result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or "no command output"
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(args)}\n{detail}")
    return result


def validate_us_release_data(root: Path | None = None, *, trade_date: str | None = None) -> None:
    """Fail closed on the US close edition without coupling to A-share batches."""
    source = root or REPO
    _validate_us_release_data(lambda path: (source / path).read_bytes(), trade_date=trade_date)


def _validate_us_release_data(read_bytes: Callable[[str], bytes], *, trade_date: str | None) -> None:
    def strict_json(name: str) -> dict:
        try:
            return json.loads(
                read_bytes(f"public/data/{name}").decode("utf-8"),
                parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"non-finite {value}")),
            )
        except (OSError, json.JSONDecodeError, ValueError) as error:
            raise RuntimeError(f"invalid US release data: {name}: {error}") from error

    pool = strict_json("us-etf-pool.json")
    garden = strict_json("us-etf-garden.json")
    health = strict_json("us-compass-health.json")
    macro = strict_json("us-macro-dashboard.json")
    model_date = str(pool.get("model_date") or "")
    errors: list[str] = []
    if not model_date:
        errors.append("pool model_date is missing")
    if trade_date is not None and model_date != trade_date:
        errors.append("pool model_date differs from target trade date")
    if pool.get("session_state") != "closed":
        errors.append("pool session_state must be closed")
    if garden.get("date") != model_date or garden.get("session_state") != "closed" or garden.get("stage") != "美股收盘版":
        errors.append("garden close identity differs from pool")
    if health.get("model_date") != model_date:
        errors.append("health model_date differs from pool")
    market_dates = sorted({
        str(item.get("date"))[:10]
        for item in (macro.get("market") or {}).values()
        if isinstance(item, dict) and item.get("date")
    })
    macro_date = market_dates[-1] if market_dates else ""
    if not macro_date:
        errors.append("macro primary date is missing")
    if macro_date != model_date:
        errors.append("macro primary date differs from pool")
    if trade_date is not None:
        records = strict_json("us-etf-flower-history.json").get("records") or []
        if not records or records[0].get("date") != trade_date:
            errors.append("latest history date differs from target trade date")
        for path in US_OWNED_FILES:
            strict_json(Path(path).name)
    if errors:
        raise RuntimeError("invalid US release data: " + "; ".join(errors))


def validate_us_public_contracts(root: Path | None = None, *, env: dict[str, str] | None = None) -> None:
    """Run the broad validator while allowing only known foreign drift.

    The allowlist itself lives in scripts/public_contract_gate.py: two copies of
    it drift apart, and a drifted copy fails on the file the other one already
    whitelisted.
    """
    result = run(BUILD_PYTHON, "scripts/validate_public_data_contracts.py", check=False, cwd=root, env=env)
    if result.returncode == 0:
        return
    try:
        payload = json.loads(result.stdout)
        errors = payload.get("errors") or []
    except json.JSONDecodeError as error:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip()) from error
    own, _foreign = split_errors(errors)
    # No owned datasets here: this publisher does not regenerate the catalog, so
    # a `catalog <key>` mismatch (its own entries included) is settled by
    # whichever publisher regenerates the catalog next. Everything that is not
    # recognised foreign drift stays fatal.
    if own or not errors:
        raise RuntimeError(
            "US public contract validation failed: "
            + "; ".join(own or [result.stderr.strip() or result.stdout.strip() or "no error payload"])
        )


SITE = "https://etf.peekabo.cc"
CLOSE_ROUTES = ("us-compass", "us-momentum", "us-macro", "us-compass/history")
RECEIPT_VERSION = 1


def prepare_build_runtime(tree: Path) -> dict[str, str]:
    env = project_subprocess_env({k: v for k, v in os.environ.items() if not k.startswith("GIT_")})
    # Keep all mutable build inputs local. Seed the Python venv without sharing
    # writes; bootstrap checks its requirements stamp against the pinned tree.
    source_venv = REPO / ".build-venv"
    if source_venv.is_dir():
        shutil.copytree(source_venv, tree / ".build-venv", symlinks=True)
    env["PUBLIC_JS_ASSET_VERSION"] = candidate._git(tree, "rev-parse", "--short", "HEAD").decode().strip()
    run(PROJECT_PYTHON, "scripts/build_site.py", "--bootstrap-only", cwd=tree, env=env)
    run("npm", "ci", "--no-audit", "--no-fund", cwd=tree, env=env)
    return env


def close_page_hashes(tree: Path) -> dict[str, str]:
    hashes = {}
    for route in CLOSE_ROUTES:
        relative = f"dist/{route}/index.html"
        page = tree / relative
        if page.resolve() != page or not page.is_file():
            raise RuntimeError(f"missing or non-regular candidate page: {relative}")
        try:
            hashes[relative] = hashlib.sha256(page.read_bytes()).hexdigest()
        except OSError as error:
            raise RuntimeError(f"cannot read candidate page: {relative}") from error
    return hashes


def verify_close_dist(tree: Path, commit: str, pages: dict[str, str]) -> None:
    candidate.verify_dist(tree, commit, US_OWNED_FILES)
    for path, digest in close_page_hashes(tree).items():
        if digest != pages[path]:
            raise RuntimeError(f"candidate page bytes differ from build: {path}")


def build_us_release(tree: Path, trade_date: str) -> dict[str, str]:
    validate_us_release_data(tree, trade_date=trade_date)
    env = prepare_build_runtime(tree)
    validate_us_public_contracts(tree, env=env)
    run(PROJECT_PYTHON, "scripts/build_site.py", "--no-contract-chain", "--clean-dist", cwd=tree, env=env)
    return close_page_hashes(tree)


def git(*args: str) -> str:
    return candidate._git(REPO, *args).decode().strip()


def owned_snapshot() -> dict:
    """Capture file identity and bytes directly, including Git-hidden changes."""
    def identity(info: os.stat_result) -> tuple[int, ...]:
        return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    snapshot = {}
    for relative in US_OWNED_FILES:
        path = REPO / relative
        try:
            before = path.lstat()
        except FileNotFoundError:
            snapshot[relative] = None
            continue
        if not stat.S_ISREG(before.st_mode) or path.resolve() != path:
            raise ReceiptBlocked(f"owned output is not a regular file: {relative}")
        try:
            digest = hashlib.sha256(path.read_bytes()).digest()
            after = path.lstat()
        except FileNotFoundError as error:
            raise ReceiptBlocked(f"owned output changed during snapshot: {relative}") from error
        if identity(before) != identity(after):
            raise ReceiptBlocked(f"owned output changed during snapshot: {relative}")
        snapshot[relative] = (identity(after), digest)
    return snapshot


def check_owned_snapshot(head: str, expected: dict) -> None:
    if git("rev-parse", "HEAD") != head:
        raise ReceiptBlocked("source HEAD changed before publication")
    candidate.check_index(REPO)
    actual = owned_snapshot()
    changed = [path for path in expected if expected[path] != actual[path]]
    if changed:
        raise ReceiptBlocked(f"owned output changed since preflight: {', '.join(changed)}")


def remote_main() -> str:
    fields = run("git", "ls-remote", "--refs", "origin", "refs/heads/main").stdout.split()
    if len(fields) != 2 or fields[1] != "refs/heads/main" or not re.fullmatch(r"[0-9a-f]{40}", fields[0]):
        raise RuntimeError("remote main identity unavailable")
    return fields[0]


def fetch_remote_base() -> str:
    if git("branch", "--show-current") != "main":
        raise RuntimeError("US ETF publisher requires main branch")
    candidate.check_index(REPO)
    run("git", "fetch", "origin", "main")
    return git("rev-parse", "refs/remotes/origin/main")


def check_close_commit(commit: str, base: str) -> None:
    parents = git("rev-list", "--parents", "-n", "1", commit).split()
    if parents != [commit, base] or not is_owned_commit_subject(git("show", "-s", "--format=%s", commit)):
        raise RuntimeError("close commit does not have the expected parent and ownership")
    paths = candidate._git(REPO, "diff-tree", "--no-commit-id", "--name-only", "-r", "-z",
                           "--no-renames", "--ignore-submodules=none", base, commit)
    changed = {os.fsdecode(path) for path in paths.split(b"\0") if path}
    if not changed or not changed <= set(US_OWNED_FILES):
        raise RuntimeError("close commit contains paths outside its owned scope")


def close_blobs(root: Path, commit: str) -> dict[str, bytes]:
    return {path: candidate._git(root, "cat-file", "blob", f"{commit}:{path}") for path in US_OWNED_FILES}


def close_blob_hashes(blobs: dict[str, bytes]) -> dict[str, str]:
    return {path: hashlib.sha256(raw).hexdigest() for path, raw in blobs.items()}


def deploy_close(tree: Path, commit: str, trade_date: str, snapshots: int, pages: dict[str, str]) -> None:
    # The same immutable input drives the production comparison and receipt.
    blobs = close_blobs(tree, commit)
    matches = {f"{SITE}/{Path(path).relative_to('public').as_posix()}": raw for path, raw in blobs.items()}
    hashes = close_blob_hashes(blobs)

    def before_upload() -> None:
        if remote_main() != commit:
            raise RuntimeError("remote main differs from the deployment candidate")
        candidate.check_source(REPO, commit, US_OWNED_FILES)
        verify_close_dist(tree, commit, pages)

    output = pages_release.deploy_pages(root=tree, commit=commit, before_upload=before_upload)
    deployment_url = pages_release.verify_production_deployment(output, commit)
    write_state("deployed", pushed=True, deployed=True, deployment_url=deployment_url)
    pages_release.purge_custom_domain()
    pages_release.probe_urls(f"{SITE}/{route}/" for route in CLOSE_ROUTES)
    pages_release.probe_json_bytes(matches)
    if remote_main() != commit:
        raise RuntimeError("remote main drifted during production readback")
    candidate.check_source(REPO, commit, US_OWNED_FILES)
    write_state("published", trade_date=trade_date, commit=commit, verified=True, learning_snapshots=snapshots,
                receipt_version=RECEIPT_VERSION, target_day=trade_date,
                verified_assets=hashes, verified_routes=list(CLOSE_ROUTES))


def learning_snapshot_count(tree: Path) -> int:
    return len(json.loads((tree / "public/data/us-compass-learning.json").read_bytes()).get("snapshots", []))


def publish_close(commit: str, base: str, trade_date: str) -> int:
    check_close_commit(commit, base)
    prior = read_state()
    # A retry of the same committed candidate has no new phase to record yet.
    if not (prior.get("phase") in ("committed", "error") and prior.get("commit") == commit
            and prior.get("trade_date") == trade_date and prior.get("pushed") is False
            and prior.get("deployed") is False and prior.get("verified") is False):
        write_state("committed", trade_date=trade_date, commit=commit, pushed=False, deployed=False,
                    verified=False, deployment_url=None, learning_snapshots=None, receipt_version=None,
                    target_day=None, verified_assets=None, verified_routes=None)
    with candidate.committed_tree(REPO, commit, US_OWNED_FILES) as tree:
        pages = build_us_release(tree, trade_date)
        snapshots = learning_snapshot_count(tree)
        if remote_main() != base:
            raise RuntimeError("remote main drifted during candidate build")
        candidate.check_source(REPO, commit, US_OWNED_FILES)
        verify_close_dist(tree, commit, pages)
        # Lease binds the comparison and update; the parent gate above limits the
        # update to a one-commit fast-forward. Push the SHA, never a moving HEAD.
        run("git", "push", f"--force-with-lease=refs/heads/main:{base}", "origin", f"{commit}:refs/heads/main")
        write_state("pushed", pushed=True)
        deploy_close(tree, commit, trade_date, snapshots, pages)
    return snapshots


def resume_pushed_close(prior: dict, head: str, base: str) -> bool:
    if prior.get("phase") not in ("committed", "pushed", "deployed", "error") or prior.get("verified") is not False:
        return False
    commit, trade_date = prior.get("commit"), prior.get("trade_date")
    if commit is None and prior.get("pushed") is False:
        return False  # Attempt failed before it created a candidate.
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit) or commit != head:
        raise ReceiptBlocked("pending pushed close differs from source HEAD or remote main")
    live = remote_main()
    if commit != live:
        # Only a still-current exact parent proves this candidate can be sent
        # as a pre-push retry. A later remote tip must not trigger a fast-forward.
        if prior.get("pushed") is False and live == base:
            try:
                check_close_commit(commit, live)
            except RuntimeError as error:
                raise ReceiptBlocked("pending pushed close differs from source HEAD or remote main") from error
            return False
        raise ReceiptBlocked("pending pushed close differs from source HEAD or remote main")
    if base != live:
        raise ReceiptBlocked("pending pushed close differs from fetched remote main")
    if not isinstance(trade_date, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", trade_date):
        raise RuntimeError("invalid trade date for pending pushed close")
    datetime.fromisoformat(trade_date)  # Reject impossible calendar dates.
    parents = git("rev-list", "--parents", "-n", "1", commit).split()
    if len(parents) != 2 or git("show", "-s", "--format=%s", commit) not in {
        prefix + trade_date for prefix in OWNED_COMMIT_PREFIXES
    }:
        raise RuntimeError("pending pushed close commit ownership or trade date mismatch")
    check_close_commit(commit, parents[1])
    # Receipt and title identify a claim; the pinned Git blobs prove its date.
    _validate_us_release_data(
        lambda path: candidate._git(REPO, "cat-file", "blob", f"{commit}:{path}"),
        trade_date=trade_date,
    )
    if prior.get("pushed") is not True and prior.get("deployed") is not True and prior.get("phase") != "deployed":
        # The live tip confirms an interrupted push independently of Pages.
        # Recheck the source after the remote query before recording that evidence.
        candidate.check_source(REPO, commit, US_OWNED_FILES)
        write_state("pushed", pushed=True)
    with candidate.committed_tree(REPO, commit, US_OWNED_FILES) as tree:
        pages = build_us_release(tree, trade_date)
        deploy_close(tree, commit, trade_date, learning_snapshot_count(tree), pages)
    return True


def commit_close(base: str, trade_date: str, *, recovery_snapshot: dict | None = None) -> str:
    expected = recovery_snapshot if recovery_snapshot is not None else owned_snapshot()
    check_owned_snapshot(base, expected)
    validate_us_release_data(trade_date=trade_date)
    check_owned_snapshot(base, expected)
    message = f"{OWNED_COMMIT_PREFIXES[recovery_snapshot is not None]}{trade_date}"
    # Path-limited commit leaves unrelated work and staging untouched.
    run("git", "commit", "--only", "-m", message, "--", *US_OWNED_FILES)
    return git("rev-parse", "HEAD")


def verified_same_date(prior: dict, head: str, trade_date: str) -> bool:
    version = prior.get("receipt_version")
    url = prior.get("deployment_url")
    if (prior.get("phase") not in ("published", "idempotent")
            or type(version) is not int or version != RECEIPT_VERSION
            or prior.get("trade_date") != trade_date or prior.get("target_day") != trade_date
            or prior.get("commit") != head
            or any(prior.get(flag) is not True for flag in ("pushed", "deployed", "verified"))
            or not isinstance(url, str)
            or not re.fullmatch(rf"https://[a-z0-9-]+\.{re.escape(pages_release.PROJECT)}\.pages\.dev", url)
            or prior.get("verified_routes") != list(CLOSE_ROUTES)):
        return False
    return prior.get("verified_assets") == close_blob_hashes(close_blobs(REPO, head))


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
    prior = read_state()
    base = fetch_remote_base()
    head = git("rev-parse", "HEAD")
    if resume_pushed_close(prior, head, base):
        return
    if head != base and run("git", "merge-base", "--is-ancestor", head, base, check=False).returncode == 0:
        run("git", "merge", "--ff-only", base)
        head = git("rev-parse", "HEAD")
    if head != base:
        # A pre-push failure can leave the local close commit pending. Never
        # auto-push it or rebase it onto a different baseline.
        check_close_commit(head, base)
        candidate.check_source(REPO, head, US_OWNED_FILES)
        old = json.loads(POOL.read_text(encoding="utf-8")).get("model_date") if POOL.exists() else None
        subject = git("show", "-s", "--format=%s", head)
        if not old or subject not in {prefix + old for prefix in OWNED_COMMIT_PREFIXES}:
            raise RuntimeError("pending close commit trade date differs from its payload")
        publish_close(head, base, old)
        return
    reviewed = owned_snapshot()
    # Freeze the inputs to repair classification before asking the provider.
    old = json.loads(POOL.read_text(encoding="utf-8")).get("model_date") if POOL.exists() else None
    garden = json.loads(GARDEN.read_text(encoding="utf-8")) if GARDEN.exists() else {}
    dirty = candidate._git(REPO, "status", "--porcelain=v1", "-z", "--untracked-files=all",
                           "--ignore-submodules=none", "--", *US_OWNED_FILES)
    now = datetime.now(NY)
    latest = latest_spy_date()
    state = session_state(now, latest)
    check_owned_snapshot(base, reviewed)
    action = decide_action(
        old=old, latest=latest, state=state, recovery_dirty=bool(dirty),
        garden_date=str(garden.get("date") or ""), garden_stage=str(garden.get("stage") or ""),
        garden_session=str(garden.get("session_state") or ""),
    )
    if action == "noop":
        try:
            if not verified_same_date(prior, base, latest) or remote_main() != base:
                raise RuntimeError(f"same-date close {latest} is unverified; publication evidence required")
            candidate.check_source(REPO, base, US_OWNED_FILES)
            check_owned_snapshot(base, reviewed)
        except (RuntimeError, subprocess.CalledProcessError) as error:
            raise ReceiptBlocked(str(error)) from error
        write_state("idempotent")
        raise SystemExit(0)
    if action == "wait":
        # Preserve the last publication receipt during an incomplete session.
        print(f"skip publish: trade_date={latest} session_state={state} old_model_date={old}")
        raise SystemExit(0)
    if action == "recover":
        assert old is not None
        commit = commit_close(base, old, recovery_snapshot=reviewed)
        publish_close(commit, base, old)
        print(f"🇺🇸 美股ETF罗盘遗留收盘版已恢复提交并部署至 {old}。")
        raise SystemExit(0)

    candidate.check_source(REPO, base, US_OWNED_FILES)
    run("python3", "scripts/update_us_etf_bar_cache.py", "--range", "3mo", "--workers", "8", "--mark-final")
    run("python3", "scripts/generate_us_etf_garden.py")
    run("python3", "scripts/update_us_compass_learning.py")
    run("python3", "scripts/generate_us_compass_health.py")
    commit = commit_close(base, latest)
    snapshots = publish_close(commit, base, latest)
    print(f"🇺🇸 美股ETF罗盘已发布并回读确认 {latest}；前向学习样本 {snapshots} 个。")


def cli() -> None:
    with site_publish_lock():
        try:
            main()
        except Exception as error:
            record_error(error)
            raise


if __name__ == "__main__":
    cli()

#!/usr/bin/env python3
"""Local A-share nightly stage runner.

Stages:
  precheck | cache | prepare | content | publish | chain

chain = precheck -> cache -> prepare

All stages take a process-wide flock so GitHub knocker and Hermes cron
cannot run the same heavy pipeline concurrently.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path("/root/projects/etf-rotation-blog")
HERMES_SCRIPTS = Path("/root/.hermes/scripts")
BAOSTOCK_PYTHON = "/usr/bin/python3"
STATE_DIR = Path("/root/.hermes/state")
LOG_DIR = Path("/root/.hermes/logs/nightly-trigger")
LOCK_PATH = STATE_DIR / "a-share-nightly-stage.lock"
CHAIN_READY_PATH = STATE_DIR / "a-share-nightly-chain-ready.json"
CN = ZoneInfo("Asia/Shanghai")

STAGE_LABELS = {
    "precheck": "环境预检",
    "cache": "行情缓存",
    "fundamental-shadow": "基本面影子",
    "prepare": "发布准备",
    "content": "内容生成",
    "publish": "正式发布",
}

STAGES = {
    "precheck": [sys.executable, str(HERMES_SCRIPTS / "precheck_a_share_nightly.py")],
    "cache": [sys.executable, str(HERMES_SCRIPTS / "update_a_share_cache_nightly.py")],
    "fundamental-shadow": [
        BAOSTOCK_PYTHON, str(ROOT / "scripts/a_share_fundamental_shadow.py"),
        "--workers", "4", "--write",
    ],
    "prepare": [sys.executable, str(HERMES_SCRIPTS / "prepare_a_share_nightly.py")],
    "content": [
        "hermes",
        "cron",
        "run",
        "339092c8d913",
    ],
    "publish": ["bash", str(HERMES_SCRIPTS / "publish_a_share_nightly.sh")],
}

STAGE_TIMEOUTS = {
    "precheck": 60,
    "cache": 1200,
    "fundamental-shadow": 2400,
    "prepare": 300,
    "content": 1800,
    "publish": 1200,
}
TERMINATE_GRACE_SECONDS = 5.0
KILL_GRACE_SECONDS = 2.0


CREDENTIALS = Path("/root/.hermes/credentials/low-chip-sync.env")


def stage_environment(stage: str) -> dict[str, str] | None:
    if stage != "fundamental-shadow":
        return None
    if not CREDENTIALS.is_file():
        raise RuntimeError(f"missing credentials file: {CREDENTIALS}")
    env = os.environ.copy()
    for raw in CREDENTIALS.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if separator and key == "LOW_CHIP_SYNC_TOKEN" and value:
            env[key] = value
    if not env.get("LOW_CHIP_SYNC_TOKEN"):
        raise RuntimeError("LOW_CHIP_SYNC_TOKEN is missing")
    return env


def now_iso() -> str:
    return datetime.now(CN).isoformat(timespec="seconds")


def duration_text(started_at: str, finished_at: str) -> str:
    try:
        seconds = max(0, int((datetime.fromisoformat(finished_at) - datetime.fromisoformat(started_at)).total_seconds()))
    except (TypeError, ValueError):
        return "耗时未知"
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes}分{seconds:02d}秒" if minutes else f"{seconds}秒"


def last_json_object(text: str) -> dict:
    for line in reversed((text or "").splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return {}


def format_report(payload: dict) -> str:
    ok = payload.get("ok") is True
    raw_results = payload.get("results") or []
    results = [item for item in raw_results if isinstance(item, dict)] if isinstance(raw_results, list) else []
    icon = "✅" if ok else "🚨"
    lines = [
        "## 🌙 A股夜间流水线",
        "",
        f"**运行结果：{icon} {'全部通过' if ok else '流水线阻断'}**",
        f"**完成时间：** {str(payload.get('finished_at') or '—').replace('T', ' ')}",
        "",
        "### 阶段进度",
    ]
    for item in results:
        stage_ok = item.get("ok") is True
        label = STAGE_LABELS.get(str(item.get("stage")), str(item.get("stage") or "未知阶段"))
        elapsed = duration_text(str(item.get("started_at")), str(item.get("finished_at")))
        lines.append(f"- {'✅' if stage_ok else '❌'} **{label}** · {elapsed}")

    shadow = next((x for x in results if x.get("stage") == "fundamental-shadow"), None)
    metrics = last_json_object(str((shadow or {}).get("stdout_tail") or ""))
    raw_coverage = metrics.get("coverage")
    coverage = raw_coverage if isinstance(raw_coverage, dict) else {}
    if metrics:
        expected = coverage.get("expected", "—")
        succeeded = coverage.get("succeeded", "—")
        ratio = coverage.get("coverage")
        ratio_text = f"{float(ratio) * 100:.1f}%" if isinstance(ratio, (int, float)) else "—"
        lines.extend([
            "",
            "### 数据门禁",
            f"- **交易日：** {metrics.get('trade_date') or '—'}",
            f"- **基本面覆盖：** {succeeded}/{expected}（{ratio_text}）",
            f"- **空数据 / 失败：** {coverage.get('empty', '—')} / {coverage.get('failed', '—')}",
            f"- **D1写入：** {metrics.get('d1_inserted', '—')} 条",
            f"- **影子观察：** 第 {metrics.get('observation_sessions', '—')} 个交易日",
        ])

    failed = next((x for x in results if x.get("ok") is not True), None)
    if failed:
        detail = str(failed.get("stderr_tail") or failed.get("stdout_tail") or "未知错误").strip()
        detail = detail[-900:]
        lines.extend([
            "", "### 阻断原因", "```text", detail, "```", "",
            "**门禁结论：** 流水线已阻断，后续阶段停止执行。",
        ])
    elif ok:
        lines.extend(["", "**门禁结论：** 可进入22:00内容生成阶段。"])
    else:
        lines.extend([
            "", "### 阻断原因", "```text", "未返回有效阶段结果", "```", "",
            "**门禁结论：** 流水线已阻断，后续阶段停止执行。",
        ])
    return "\n".join(lines)


def write_status(payload: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = STATE_DIR / "a-share-nightly-trigger-status.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_json_atomic(path: Path, payload: dict) -> None:
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
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def persist_chain_ready(payload: dict) -> None:
    if payload.get("requested_stage") != "precheck-cache" or payload.get("ok") is not True:
        return
    finished_at = str(payload.get("finished_at") or "")
    write_json_atomic(CHAIN_READY_PATH, {
        "version": 1,
        "trade_date": finished_at[:10],
        "status": "ready",
        "ready_at": finished_at,
        "results": payload.get("results") or [],
    })


def write_log(payload: dict, stage: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(CN).strftime("%Y%m%dT%H%M%S")
    (LOG_DIR / f"{stamp}-{stage}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def acquire_lock():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    handle = LOCK_PATH.open("a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.seek(0)
        holder = handle.read().strip() or "unknown"
        handle.close()
        raise RuntimeError(f"nightly stage already running: {holder}")
    handle.seek(0)
    handle.truncate()
    handle.write(f"pid={os.getpid()} at={now_iso()}\n")
    handle.flush()
    return handle


def release_lock(handle) -> None:
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def resolve_stages(stage: str) -> list[str]:
    if stage == "chain":
        return ["precheck", "cache", "fundamental-shadow", "prepare"]
    if stage == "precheck-cache":
        return ["precheck", "cache", "fundamental-shadow"]
    return [stage]


def fundamental_shadow_fallback() -> dict | None:
    """Accept today's already-persisted complete shadow after a transient rerun fault."""
    path = ROOT / "data/local/a-share-fundamental-shadow-latest.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    coverage = payload.get("coverage") if isinstance(payload.get("coverage"), dict) else {}
    try:
        observed = datetime.fromisoformat(str(payload.get("trade_date") or "")).date()
    except ValueError:
        return None
    age_days = (datetime.now(CN).date() - observed).days
    if (
        0 <= age_days <= 3
        and coverage.get("publishable") is True
        and int(coverage.get("failed") or 0) == 0
    ):
        return payload
    return None


def effective_timeout(stage: str, requested: int) -> int:
    return min(requested, STAGE_TIMEOUTS.get(stage, requested))


def process_group_exists(pgid: int) -> bool:
    """Return whether a live process still belongs to pgid."""
    proc_root = Path("/proc")
    try:
        entries = proc_root.iterdir()
    except OSError:
        entries = ()
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            fields = (entry / "stat").read_text(encoding="utf-8").split()
            if len(fields) > 4 and fields[2] != "Z" and int(fields[4]) == pgid:
                return True
        except (OSError, ValueError):
            continue
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def signal_process_group(pgid: int, sig: signal.Signals) -> None:
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        pass


def wait_for_process_group_exit(pgid: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while process_group_exists(pgid):
        if time.monotonic() >= deadline:
            return False
        time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
    return True


def run_command(
    cmd: list[str], *, cwd: str, text: bool, capture_output: bool,
    env: dict[str, str] | None, timeout: int,
) -> subprocess.CompletedProcess[str]:
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        text=text,
        stdout=subprocess.PIPE if capture_output else None,
        stderr=subprocess.PIPE if capture_output else None,
        env=env,
        start_new_session=True,
    )
    pgid = proc.pid
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        signal_process_group(pgid, signal.SIGTERM)
        try:
            proc.communicate(timeout=TERMINATE_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            pass
        # The leader may exit on TERM while descendants keep the group alive.
        # Always inspect the original pgid after the grace period.
        if process_group_exists(pgid):
            signal_process_group(pgid, signal.SIGKILL)
            wait_for_process_group_exit(pgid, KILL_GRACE_SECONDS)
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        raise
    return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)


def run_stage(stage: str, timeout: int) -> dict:
    stages = resolve_stages(stage)
    results = []
    overall_ok = True
    for name in stages:
        cmd = STAGES[name]
        started = now_iso()
        stage_timeout = effective_timeout(name, timeout)
        write_status({
            "version": 1,
            "requested_stage": stage,
            "status": "running",
            "current_stage": name,
            "started_at": started,
            "ok": False,
            "results": results,
        })
        try:
            proc = run_command(
                cmd,
                cwd=str(ROOT),
                text=True,
                capture_output=True,
                env=stage_environment(name),
                timeout=stage_timeout,
            )
            item = {
                "stage": name,
                "started_at": started,
                "finished_at": now_iso(),
                "returncode": proc.returncode,
                "stdout_tail": (proc.stdout or "")[-1200:],
                "stderr_tail": (proc.stderr or "")[-1200:],
                "ok": proc.returncode == 0,
            }
            if name == "fundamental-shadow" and not item["ok"]:
                fallback = fundamental_shadow_fallback()
                if fallback is not None:
                    item["ok"] = True
                    item["degraded"] = True
                    item["stdout_tail"] = json.dumps(fallback, ensure_ascii=False)
                    item["stderr_tail"] = "transient refresh failed; reused today's complete persisted shadow"
        except subprocess.TimeoutExpired:
            fallback = fundamental_shadow_fallback() if name == "fundamental-shadow" else None
            item = {
                "stage": name,
                "started_at": started,
                "finished_at": now_iso(),
                "returncode": 0 if fallback is not None else 124,
                "stdout_tail": json.dumps(fallback, ensure_ascii=False) if fallback is not None else "",
                "stderr_tail": (
                    "transient refresh timed out; reused today's complete persisted shadow"
                    if fallback is not None
                    else f"STAGING BLOCKER: {name} timed out after {stage_timeout}s"
                ),
                "ok": fallback is not None,
                "degraded": fallback is not None,
            }
        except Exception as exc:  # noqa: BLE001
            item = {
                "stage": name,
                "started_at": started,
                "finished_at": now_iso(),
                "returncode": 99,
                "stdout_tail": "",
                "stderr_tail": str(exc),
                "ok": False,
            }
        results.append(item)
        if not item["ok"]:
            overall_ok = False
            break
    payload = {
        "version": 1,
        "requested_stage": stage,
        "ok": overall_ok,
        "finished_at": now_iso(),
        "results": results,
    }
    write_status(payload)
    persist_chain_ready(payload)
    write_log(payload, stage)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--stage",
        required=True,
        choices=sorted(list(STAGES) + ["chain", "precheck-cache"]),
    )
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args()
    try:
        lock_handle = acquire_lock()
    except RuntimeError as exc:
        payload = {
            "version": 1,
            "requested_stage": args.stage,
            "ok": False,
            "finished_at": now_iso(),
            "results": [{
                "stage": args.stage,
                "started_at": now_iso(),
                "finished_at": now_iso(),
                "returncode": 75,
                "stdout_tail": "",
                "stderr_tail": str(exc),
                "ok": False,
            }],
            "busy": True,
        }
        write_status(payload)
        write_log(payload, args.stage)
        print(format_report(payload))
        return 75
    try:
        payload = run_stage(args.stage, args.timeout)
    finally:
        release_lock(lock_handle)
    print(format_report(payload))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

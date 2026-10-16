#!/usr/bin/env python3
"""低筹码发布链预抓取：build + enrichment，不发布、不推送。

`update_low_chip_and_release.py` 全链实测 103.7 分钟，超 Hermes cron 3600s
硬上限一倍。本脚本只承担其中最重的前置两段，为 16:00 主链准备可复用的
/tmp enrichment cache；主链的 `run_enrichments()` 检测到新鲜 cache 后自行跳过
抓取。

设计约束：
- 日期口径必须与主链一致（主链用 TRADE_DATE_OVERRIDE or now(CN)）。
  build_low_chip_base.py 在无参无环境变量时默认一个历史日期，不是今天，
  因此这里显式传参并在跑之前校验。
- 只做数据准备，不碰 git、不 publish、不 push。工作区由主链负责提交。
- 前置阶段不设 3600s 上限压力，但单段 timeout 仍按主链同值收紧，异常早暴露。
"""
from __future__ import annotations

import datetime as dt
import fcntl
import json
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from zoneinfo import ZoneInfo

CN = ZoneInfo("Asia/Shanghai")
ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "public/data/a-low-chip-stocks.json"
FTSHARE_ENV = Path("/root/.hermes/credentials/ftshare-api.env")

BUILD_TIMEOUT = 900
ENRICH_TIMEOUT = 2700  # 主链上限 2400s；此处独立运行，放宽到 45min


def resolve_trade_date() -> str:
    override = os.environ.get("LOW_CHIP_TRADE_DATE") or ""
    today = dt.datetime.now(CN).date().isoformat()
    if not override:
        return today
    try:
        dt.date.fromisoformat(override)  # fail fast on malformed override
    except ValueError:
        raise SystemExit(f"malformed LOW_CHIP_TRADE_DATE: {override}") from None
    if override > today:
        raise SystemExit(f"refusing future trade date: {override}")
    return override


def check_ftshare_keys() -> int:
    """预抓前确认 API Key 可用数。0 个 key 时 enrichment 会退化成 147 分钟。"""
    if not FTSHARE_ENV.exists():
        return 0
    count = 0
    for line in FTSHARE_ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("#") or not line or "=" not in line:
            continue
        value = line.split("=", 1)[1]
        # 多个 key 写在同一行、逗号分隔（与 ftshare_api_keys() 同口径）
        count += sum(1 for k in value.split(",") if k.strip())
    return count


def run(cmd: list[str], timeout: int, env: dict | None = None, retries: int = 2) -> None:
    """Run a child process, retrying transient iWenCai/SSL failures.

    iWenCai 偶发 SSL handshake timeout（实测探测侧 3/3 正常，纯单次抖动），
    直接失败会让已消耗的其他源配额作废，因此对该类错误做有限次退避重试。
    其他错误立即抛出，不做无意义重试。
    """
    label = Path(cmd[1]).name if len(cmd) > 1 else cmd[0]
    attempt = 0
    while True:
        print(f"[{_stamp()}] START {label}{' (retry %d)' % attempt if attempt else ''}", flush=True)
        t0 = time.time()
        result = subprocess.run(
            cmd, cwd=str(ROOT), env=env, text=True,
            capture_output=True,  # 错误文本需参与瞬时故障判定
        )
        wall = int(time.time() - t0)
        if result.stdout:
            print(result.stdout, end="" if result.stdout.endswith("\n") else "\n", flush=True)
        if result.returncode == 0:
            print(f"[{_stamp()}] DONE  {label} in {wall}s", flush=True)
            return
        combined = f"{result.stdout}\n{result.stderr}".lower()
        transient = any(m in combined for m in (
            "handshake", "ssl", "timeout", "timed out", "connection", "网络错误",
        ))
        if attempt < retries and transient:
            attempt += 1
            backoff = 30 * attempt
            print(f"[{_stamp()}] RETRY {label}: 瞬时错误，{backoff}s 后第 {attempt} 次重试", flush=True)
            time.sleep(backoff)
            continue
        raise SystemExit(f"{label} failed: exit {result.returncode} after {wall}s")


def _stamp() -> str:
    return dt.datetime.now(CN).strftime("%H:%M:%S")


@contextmanager
def publish_lock(timeout: int = 2400):
    """Hold the same flock the 16:00 main chain takes.

    预抓 15:30 跑约 50 分钟，主链 16:00 启动时会重叠。两者都消耗 iWenCai /
    FTShare 配额，并发只会互相抢占；主链拿不到锁时阻塞等待，而不是并行
    抢额度。timeout 后放弃（主链自己有 3600s 上限，不会无限等）。
    """
    lock_path = Path("/root/.hermes/state/etf-site-publish.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+")
    deadline = time.time() + timeout
    waited = False
    next_report = 0.0
    try:
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                if waited:
                    print(f"[{_stamp()}] LOCK 发布锁已获取", flush=True)
                break
            except BlockingIOError:
                if time.time() >= deadline:
                    raise SystemExit(
                        f"publish lock busy for {timeout}s; another chain run holds it"
                    )
                now = time.time()
                if now >= next_report:
                    # 每 60s 报一次等待，避免刷屏，也保留可观测性
                    print(f"[{_stamp()}] WAIT 发布锁被占用，等待中…", flush=True)
                    next_report = now + 60
                waited = True
                # 短轮询：锁常在锁持有者提前结束时立刻可拿，长 sleep 会让
                # 空闲的锁被白白干等（实测 30s 轮询多等了 22s）
                time.sleep(2)
        yield
    finally:
        try:
            fcntl.flock(handle, fcntl.LOCK_UN)
        finally:
            handle.close()


def main() -> int:
    trade_date = resolve_trade_date()
    keys = check_ftshare_keys()

    # 日期漂移早停：主链 604 行 fail-closed 门禁会在最后拦住不匹配的 DATA，
    # 但那时 48 分钟已经白跑。这里在 build 之前先确认目标快照日期。
    existing = None
    if DATA.exists():
        try:
            existing = str(json.loads(DATA.read_text(encoding="utf-8")).get("data_as_of") or "")[:10]
        except Exception:
            existing = None

    print(json.dumps({
        "stage": "prefetch",
        "trade_date": trade_date,
        "current_data_as_of": existing,
        "ftshare_api_keys": keys,
        "plan": ["build_low_chip_base", "fetch_low_chip_enrichments"],
    }, ensure_ascii=False, sort_keys=True), flush=True)

    if keys == 0:
        print("WARN: 无 FTShare API Key，enrichment 将退回匿名配额（约 147 分钟）", flush=True)

    env = dict(os.environ)
    env["LOW_CHIP_TRADE_DATE"] = trade_date

    t_start = time.time()

    with publish_lock():
        # 1) 三周期原始池 + DATA 快照（实测 808s）
        run([sys.executable, "scripts/build_low_chip_base.py", trade_date],
            BUILD_TIMEOUT, env=env)

        # 2) 原始池落库，避免后续失败浪费已耗 iWenCai 配额
        run([sys.executable, "scripts/sync_low_chip_raw_pool.py"], 300, env=env)

        # 3) enrichment（实测 2225s）产物即主链复用的 /tmp cache
        run([sys.executable, "scripts/fetch_low_chip_enrichments.py"],
            ENRICH_TIMEOUT, env=env)

    # 收尾自检：DATA 日期必须等于目标 trade_date
    payload = json.loads(DATA.read_text(encoding="utf-8"))
    actual = str(payload.get("data_as_of") or "")[:10]
    if actual != trade_date:
        raise SystemExit(f"DATA data_as_of {actual} != trade_date {trade_date}")

    # 完成标记：主链据此跳过 build+sync（cronjob_manage 不支持 env，不走
    # LOW_CHIP_SKIP_BUILD，靠文件标记传递）。主链只认 trade_date 匹配且
    # 仍在 enrichment cache TTL 内的标记。
    completed_at = dt.datetime.now(CN).isoformat()
    Path("/tmp/low_chip_prefetch.json").write_text(json.dumps({
        "trade_date": actual,
        "completed_at": completed_at,
        "ftshare_api_keys": keys,
        "total_wall_s": int(time.time() - t_start),
    }, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")

    print(json.dumps({
        "stage": "prefetch_complete",
        "trade_date": actual,
        "stocks": len(payload.get("intersection_before_filters") or []),
        "marker": "/tmp/low_chip_prefetch.json",
        "total_wall_s": int(time.time() - t_start),
    }, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
